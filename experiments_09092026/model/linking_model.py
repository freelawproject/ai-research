"""Task B model — antecedent-ranking coreference linker.

Two-pass (plan §8): the encoder pass is local (a ±context window per mention,
pooled to one embedding), and the linking pass is global over those
embeddings — so the 8,192-token document limit never binds. Each mention is
linked to its best *antecedent* (nearest valid prior mention) or to a
"new-cluster" dummy; transitive closure forms clusters. High-precision legal
features (name overlap, reporter match, id./supra recency, distance) are
concatenated into the pair scorer to bridge gaps the embeddings miss.

Antecedent-ranking marginal loss (Lee et al. 2017): for each mention, the
gold is the set of earlier same-cluster mentions among its candidates (or the
dummy if none are in window).

Batch = one citing case: all tensors are shaped [M, ...] for M mentions.
"""

from __future__ import annotations

import torch
from torch import nn
from torch.utils.checkpoint import checkpoint
from transformers import AutoModel

NEG = -1e9


def _masked_logsumexp(scores, mask, dim=-1):
    scores = scores.masked_fill(~mask, NEG)
    return torch.logsumexp(scores, dim=dim)


class AntecedentLinker(nn.Module):
    def __init__(self, base_id, n_feats, attn_impl=None, hidden=256, chunk=32768,
                 recompute=False):
        super().__init__()
        self.encoder = AutoModel.from_pretrained(
            base_id, **({"attn_implementation": attn_impl} if attn_impl else {}))
        h = self.encoder.config.hidden_size
        self.pair = nn.Sequential(
            nn.Linear(4 * h + n_feats, hidden), nn.GELU(),
            nn.Dropout(0.1), nn.Linear(hidden, 1))
        self.dummy = nn.Linear(h, 1)   # mention-dependent new-cluster score
        # A case's mentions are processed in slices rather than as one batch:
        # both passes below are per-mention independent, so slicing is
        # numerically identical and caps peak memory no matter how many
        # mentions the case has — a consolidated opinion with 6,122 of them
        # OOM'd a 44GB card as a single batch.
        #
        # The slice is sized in TOKENS, not mentions, so it stays the same cost
        # when the window width changes: `chunk` mentions x lwin subwords is
        # what the encoder actually pays for, and a mention-count budget
        # silently got 4x more expensive when lwin went 64 -> 256.
        self.token_budget = chunk
        # Slicing alone bounds the peak inside ONE call. At training time
        # autograd still keeps every slice's activations alive until backward,
        # so the total is unchanged and a wide window OOMs anyway (round 3:
        # 38.8 GiB at lwin=256). With `recompute`, each slice runs under
        # torch.utils.checkpoint: only its inputs and its pooled output are
        # kept, and the slice is re-run during backward. What survives across
        # the case is then the [M, H] embedding table (12.5 MB at M=6,122) —
        # peak memory becomes independent of both M and the window width, at
        # the cost of one extra forward per slice. No-op under no_grad.
        self.recompute = recompute

    def _run(self, fn, *args):
        if self.recompute and torch.is_grad_enabled():
            return checkpoint(fn, *args, use_reentrant=False)
        return fn(*args)

    def _embed_chunk(self, ids, am, mm):
        out = self.encoder(input_ids=ids, attention_mask=am).last_hidden_state
        m = mm.unsqueeze(-1).float()
        return (out * m).sum(1) / m.sum(1).clamp(min=1.0)

    def _score_chunk(self, emb_q, emb, safe, pf, valid):
        K, H = safe.shape[1], emb.shape[1]
        e_i = emb_q.unsqueeze(1).expand(-1, K, H)
        e_j = emb[safe]                                   # [n,K,H]
        feat = torch.cat([e_i, e_j, e_i * e_j, (e_i - e_j).abs(), pf], dim=-1)
        return self.pair(feat).squeeze(-1).masked_fill(~valid, NEG)

    def embed(self, input_ids, attention_mask, mention_mask):
        """[M, L] windows -> [M, H] mention embeddings (masked mean over the
        mention's own subword tokens), encoded in slices of
        token_budget // L windows at a time."""
        step = max(1, self.token_budget // max(1, input_ids.shape[1]))
        embs = []
        for s in range(0, input_ids.shape[0], step):
            e = s + step
            embs.append(self._run(self._embed_chunk, input_ids[s:e],
                                  attention_mask[s:e], mention_mask[s:e]))
        return torch.cat(embs, dim=0)

    def scores(self, emb, cand_idx, pair_feats):
        """emb [M,H], cand_idx [M,K] (-1 pad), pair_feats [M,K,F] ->
        scores [M, K+1] (last col = dummy).

        Chunked over query rows: the [M,K,H] pair tensors are the second memory
        ceiling after the encoder pass. Candidates are always gathered from the
        full `emb` table, so a query never loses access to an antecedent that
        happens to fall in another slice."""
        M, K = cand_idx.shape
        valid = cand_idx >= 0
        safe = cand_idx.clamp(min=0)
        step = max(1, self.token_budget // max(1, K))
        pairs = []
        for s in range(0, M, step):
            e = s + step
            pairs.append(self._run(self._score_chunk, emb[s:e], emb,
                                   safe[s:e], pair_feats[s:e], valid[s:e]))
        pair = torch.cat(pairs, dim=0)
        dummy = self.dummy(emb)                          # [M,1]
        return torch.cat([pair, dummy], dim=-1), valid

    def forward(self, input_ids, attention_mask, mention_mask,
                cand_idx, pair_feats, gold_ante=None, gold_dummy=None, id_rule=None):
        # `id_rule` (the Id.->nearest decode slots, emitted by the Collator for
        # predict_clusters) is a decode-time input only; the Trainer forwards
        # every batch key, so it has to be accepted and ignored here.
        emb = self.embed(input_ids, attention_mask, mention_mask)
        scores, valid = self.scores(emb, cand_idx, pair_feats)
        out = {"scores": scores}
        if gold_ante is not None:
            ones = torch.ones(valid.shape[0], 1, dtype=torch.bool,
                              device=valid.device)
            allmask = torch.cat([valid, ones], dim=-1)           # dummy always valid
            goldmask = torch.cat([gold_ante.bool() & valid,
                                  gold_dummy.bool().unsqueeze(-1)], dim=-1)
            den = _masked_logsumexp(scores, allmask)
            num = _masked_logsumexp(scores, goldmask)
            out["loss"] = (den - num).mean()
        return out

    @torch.no_grad()
    def predict_clusters(self, batch, id_rule=True):
        """Greedy antecedent decode + union-find -> cluster id per mention.

        `batch["id_rule"]` (from LinkDataset.id_rule_slots) forces an `Id.` to
        a given candidate slot; -1 keeps the argmax. Off with id_rule=False."""
        emb = self.embed(batch["input_ids"], batch["attention_mask"],
                         batch["mention_mask"])
        scores, valid = self.scores(emb, batch["cand_idx"], batch["pair_feats"])
        M, Kp1 = scores.shape
        parent = list(range(M))

        def find(x):
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        choice = scores.argmax(dim=-1).tolist()
        rule = batch.get("id_rule")
        if id_rule and rule is not None:
            cand = batch["cand_idx"]
            for i, slot in enumerate(rule.tolist()):
                if 0 <= slot < Kp1 - 1 and int(cand[i, slot]) >= 0:
                    choice[i] = slot
        for i in range(M):
            c = choice[i]
            if c < Kp1 - 1:                       # linked to a candidate
                j = int(batch["cand_idx"][i, c].item())
                if j >= 0:
                    parent[find(i)] = find(j)
        roots = {}
        out = []
        for i in range(M):
            r = find(i)
            out.append(roots.setdefault(r, len(roots)))
        return out
