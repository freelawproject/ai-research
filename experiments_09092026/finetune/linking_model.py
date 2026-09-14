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
from transformers import AutoModel

NEG = -1e9


def _masked_logsumexp(scores, mask, dim=-1):
    scores = scores.masked_fill(~mask, NEG)
    return torch.logsumexp(scores, dim=dim)


class AntecedentLinker(nn.Module):
    def __init__(self, base_id, n_feats, attn_impl=None, hidden=256):
        super().__init__()
        self.encoder = AutoModel.from_pretrained(
            base_id, **({"attn_implementation": attn_impl} if attn_impl else {}))
        h = self.encoder.config.hidden_size
        self.pair = nn.Sequential(
            nn.Linear(4 * h + n_feats, hidden), nn.GELU(),
            nn.Dropout(0.1), nn.Linear(hidden, 1))
        self.dummy = nn.Linear(h, 1)   # mention-dependent new-cluster score

    def embed(self, input_ids, attention_mask, mention_mask):
        """[M, L] windows -> [M, H] mention embeddings (masked mean over the
        mention's own subword tokens)."""
        out = self.encoder(input_ids=input_ids,
                           attention_mask=attention_mask).last_hidden_state
        m = mention_mask.unsqueeze(-1).float()
        summed = (out * m).sum(1)
        cnt = m.sum(1).clamp(min=1.0)
        return summed / cnt

    def scores(self, emb, cand_idx, pair_feats):
        """emb [M,H], cand_idx [M,K] (-1 pad), pair_feats [M,K,F] ->
        scores [M, K+1] (last col = dummy)."""
        M, K = cand_idx.shape
        H = emb.shape[1]
        valid = cand_idx >= 0
        safe = cand_idx.clamp(min=0)
        e_i = emb.unsqueeze(1).expand(M, K, H)
        e_j = emb[safe]                                  # [M,K,H]
        feat = torch.cat([e_i, e_j, e_i * e_j, (e_i - e_j).abs(),
                          pair_feats], dim=-1)
        pair = self.pair(feat).squeeze(-1)               # [M,K]
        pair = pair.masked_fill(~valid, NEG)
        dummy = self.dummy(emb)                          # [M,1]
        return torch.cat([pair, dummy], dim=-1), valid

    def forward(self, input_ids, attention_mask, mention_mask,
                cand_idx, pair_feats, gold_ante=None, gold_dummy=None):
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
    def predict_clusters(self, batch):
        """Greedy antecedent decode + union-find -> cluster id per mention."""
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
