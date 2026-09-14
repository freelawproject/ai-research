"""Shared paths, model table, treatment lists and gold loaders for the
two-stage (gate -> labeler) citator experiment. Imported by every script here."""
import csv
import json
import os
import re
import sys
from collections import defaultdict

ROOT = os.path.dirname(os.path.abspath(__file__))
AI_RESEARCH = os.path.abspath(os.path.join(ROOT, ".."))
FLP = os.path.abspath(os.path.join(AI_RESEARCH, ".."))
PIPELINE = os.path.join(AI_RESEARCH, "citator-pipeline")
TRIAGE = os.path.join(AI_RESEARCH, "experiments_09022026", "triage")
BENCH_DATA = os.path.join(FLP, "citator-benchmark", "data")
CL_ENV = os.path.join(FLP, "citator-benchmark", ".env")

DATA = os.path.join(ROOT, "data")
OPINIONS = os.path.join(DATA, "opinions")      # {cid}.json  CourtListener payloads
INPUTS = os.path.join(DATA, "inputs")          # {cid}.tagged.txt + {cid}.inventory.json
RUNS = os.path.join(DATA, "runs")              # one folder per named run
PROMPTS = os.path.join(ROOT, "prompts")

SPLITS = os.path.join(TRIAGE, "inputs", "splits_full.csv")          # 149 dev / 233 test
CITING_META = os.path.join(AI_RESEARCH, "experiments_05182026", "data", "citing_metadata.csv")
GOLD_CSV = os.path.join(BENCH_DATA, "treatments_most_negative.csv")
PRED_CSV = os.path.join(BENCH_DATA, "predictions.csv")               # 0529 Sonnet rows, source=0529_sonnet
OPINION_CACHES = [
    os.path.join(TRIAGE, "data", "annotator", "data", "opinion_html"),
    os.path.join(BENCH_DATA, "opinion_html"),
]

for p in (TRIAGE, PIPELINE):
    if p not in sys.path:
        sys.path.insert(0, p)

# ── treatments (canonical list: ai-research/CLAUDE.md) ──
DIRECT_HISTORY = [
    "Reversed by", "Reversed and remanded by", "Vacated and remanded by", "Vacated by",
    "Reversed in part; Vacated in part by", "Affirmed in part; Reversed in part by",
    "Affirmed in part; Vacated in part by", "Modified by", "Remanded by",
    "Cert. granted by", "Dismissed by", "Cert. denied by", "Affirmed by",
]
CITING_REFERENCE = [  # most -> least severe
    "Overruled by", "Abrogated by", "Questioned by", "Disapproved by", "Limited by",
    "Criticized by", "Distinguished by", "Declined to follow by",
]
CITED = "Cited by"
OTHER = "Other treatment"
LABELER_LABELS = CITING_REFERENCE + [CITED, OTHER]
SEVERITY = {
    "Overruled by": "Stop", "Abrogated by": "Stop", "Questioned by": "Stop",
    "Disapproved by": "Warning", "Limited by": "Warning",
    "Criticized by": "Caution", "Distinguished by": "Caution", "Declined to follow by": "Caution",
    "Cited by": "Neutral", "Other treatment": "Caution",
}
# rank for "most severe wins" merges (lower = more severe)
RANK = {t: i for i, t in enumerate(CITING_REFERENCE)}
RANK[OTHER] = len(CITING_REFERENCE)
RANK[CITED] = len(CITING_REFERENCE) + 1


def base(t):
    """'X as recognized by' -> 'X by'."""
    return (t or "").replace(" as recognized by", " by")


def is_recognized(t):
    return (t or "").endswith(" as recognized by")


def is_dh(t):
    return base(t) in DIRECT_HISTORY


def is_cr(t):
    """A citing-reference (analytical, non-neutral) label applied by the citing case."""
    return bool(t) and not is_recognized(t) and t in CITING_REFERENCE


def category(t):
    if not t or t == CITED:
        return "Cited by"
    if is_recognized(t):
        return "Related Reference"
    return "Direct History" if t in DIRECT_HISTORY else "Citing Reference"


# ── models ──
# price = on-demand $/M (in, out); batch_price = Bedrock batch (half of on-demand where offered).
# Opus 5 Bedrock price is UNVERIFIED (assumed the Opus 4.5 tier); Kimi batch price is not
# published — half of on-demand assumed.
MODELS = {
    "kimi": {"id": "moonshotai.kimi-k2.5", "family": "chat", "batch": True,
             "max_tokens": 16000, "price": (0.60, 3.00), "batch_price": (0.30, 1.50)},
    "gpt": {"id": "us.openai.gpt-5.6-luna", "family": "converse", "batch": False,
            # max_tokens includes reasoning tokens: xhigh exhausted 32K on the two largest opinions
            "max_tokens": 64000, "price": (0.22, 1.32), "batch_price": None,
            "reasoning_effort": "medium"},
    "haiku": {"id": "us.anthropic.claude-haiku-4-5-20251001-v1:0", "family": "anthropic",
              "batch": True, "max_tokens": 64000, "price": (1.0, 5.0), "batch_price": (0.5, 2.5),
              "thinking": None},
    "sonnet": {"id": "us.anthropic.claude-sonnet-4-6", "family": "anthropic", "batch": True,
               "max_tokens": 64000, "price": (3.0, 15.0), "batch_price": (1.5, 7.5),
               "thinking": {"type": "enabled", "budget_tokens": 8000}},
    "opus": {"id": "us.anthropic.claude-opus-5", "family": "anthropic", "batch": True,
             "max_tokens": 128000, "price": (5.0, 25.0), "batch_price": (2.5, 12.5),
             "thinking": {"type": "adaptive"}},
}
STAGE_PROMPTS = {"gate": "gate_v1.md", "label": "labeler_v1.md"}
PAGE_CHARS = 600_000   # same pagination as the 0529 run (utils.preprocess.split_opinion_to_pages)
PAGE_OVERLAP = 20_000


def cost_usd(model_key, in_tokens, out_tokens, batch):
    m = MODELS[model_key]
    price = (m["batch_price"] if batch and m["batch_price"] else m["price"])
    return in_tokens / 1e6 * price[0] + out_tokens / 1e6 * price[1]


# ── file helpers ──
def read_json(path, default=None):
    if not os.path.exists(path):
        return default
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def write_json(path, obj):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=1, ensure_ascii=False)


def read_text(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def prompt_text(name_or_path):
    p = name_or_path if os.path.exists(name_or_path) else os.path.join(PROMPTS, name_or_path)
    return read_text(p)


# ── splits, metadata, gold ──
def load_splits():
    """{cluster_id(str): 'dev'|'test'|'unused'} from the triage full split."""
    with open(SPLITS, encoding="utf-8") as f:
        return {r["cluster_id"]: r["split"] for r in csv.DictReader(f)}


def cluster_ids(split="all"):
    sp = load_splits()
    want = {"dev", "test"} if split == "all" else {split}
    return sorted((c for c, s in sp.items() if s in want), key=int)


def load_citing_meta():
    """{cluster_id: {name, court, year}} from the 0518 benchmark metadata."""
    out = {}
    with open(CITING_META, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            out[r["cluster_id"]] = {"name": r["case_name"], "court": r["court"], "year": r["year"]}
    return out


def load_gold(cids=None):
    """Gold pairs from the benchmark's flat export: {citing: [row]} where row has
    cited_cluster_id, cited_citation, cited_name, final, votes (list, non-empty
    expert_1..3 slots), direction. Only rows with a final treatment."""
    want = set(cids) if cids is not None else None
    out = defaultdict(list)
    with open(GOLD_CSV, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            citing = r["citing_case_cluster_id"]
            if want is not None and citing not in want:
                continue
            if not r["final_treatment"]:
                continue
            out[citing].append({
                "citing": citing,
                "cited_cluster_id": r["cited_case_cluster_id"] or "",
                "cited_citation": r["cited_case_citation"] or "",
                "cited_name": r["cited_case_name"] or "",
                "final": r["final_treatment"],
                "final_source": r["final_treatment_source"],
                "votes": [r[f"expert_{i}_treatment"] for i in (1, 2, 3) if r[f"expert_{i}_treatment"]],
                "direction": r["direction"],
            })
    return out


def load_baseline_predictions(source="0529_sonnet", with_opinion_type=False):
    """{(citing, cited_cluster_id): treatment} for one prediction source in the ledger
    (or {...: (treatment, opinion_type)} with with_opinion_type)."""
    out = {}
    with open(PRED_CSV, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r["source"] == source:
                out[(r["citing_cluster_id"], r["cited_cluster_id"])] = (
                    (r["treatment"], r["opinion_type"]) if with_opinion_type else r["treatment"])
    return out


def separate_opinion(opinion_type):
    """True when a treatment's governing opinion is a dissent or concurrence (not lead/plurality)."""
    t = (opinion_type or "").lower()
    return "dissent" in t or "concur" in t


# ── citation matching (fallback join when the gold row has no CL cluster id) ──
# reporter = alpha tokens ("F.", "U. S.", "App'x", "N.W.2d") or ordinal series tokens ("2d", "3d", "4th");
# the page is the first bare number after them that is NOT itself a series token.
_TOKEN = r"(?:[A-Za-z][A-Za-z0-9.'&]*|\d+(?:d|th|st|nd|rd))"
_VOL_REP_PAGE = re.compile(rf"(\d+)\s+({_TOKEN}(?:\s+{_TOKEN})*?)\s+(\d+)(?![\d]*(?:d|th|st|nd|rd)\b)")


def normalize_citation(s):
    """'249 U. S. 182, 184' -> '249us182'; '726 F. 2d 1448' -> '726f2d1448'; None if no match."""
    if not s:
        return None
    m = _VOL_REP_PAGE.search(str(s).replace("\n", " "))
    if not m:
        return None
    vol, rep, page = m.groups()
    return f"{vol}{re.sub(r'[^a-z0-9]', '', rep.lower())}{page}"


_STOP = {"united", "states", "state", "people", "commonwealth", "city", "county", "board", "company",
         "corporation", "inc", "co", "corp", "ltd", "llc", "the", "of", "and", "ex", "rel", "estate", "matter",
         "in", "re", "v", "vs", "et", "al", "department", "dept", "commissioner", "secretary", "america",
         "association", "assn", "national", "bank", "trust", "insurance", "government"}


def name_tokens(name):
    """Distinctive party tokens of a case name (lowercase, >= 4 letters, stoplist removed)."""
    toks = re.findall(r"[A-Za-z][A-Za-z'\-]{3,}", name or "")
    return {t.lower().strip("'-") for t in toks} - _STOP


def citation_variants(field):
    """The gold export joins several citations with '; ' — split them."""
    return [c.strip() for c in re.split(r";\s*", field or "") if c.strip()]
