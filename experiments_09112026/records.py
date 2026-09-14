"""Native Bedrock request bodies per model family, output parsing, and result
normalization for the two stages.

Families (common.MODELS[*]["family"]):
  anthropic  Messages body (batch + InvokeModel); tool schema per stage; thinking per model.
             With thinking on, tool_choice must be "auto" (forced tool_choice is rejected
             alongside thinking), so the prompt tells the model to answer via the tool and
             the parser falls back to JSON in the text.
  chat       OpenAI-style chat body (Kimi K2.5: batch + InvokeModel). No system role
             (the pipeline's MODEL_CAPS says Kimi lacks it) — prompt prepended to the user turn.
  converse   Bedrock Converse call (GPT-5.6 Luna has no InvokeModel and no batch);
             the body here is a marker dict the runner turns into converse() kwargs.
"""
import json
import re

from json_repair import repair_json

from common import CITED, CITING_REFERENCE, DIRECT_HISTORY, LABELER_LABELS, MODELS, OTHER, RANK, is_recognized

ANTHROPIC_VERSION = "bedrock-2023-05-31"
CONF_RANK = {"low": 0, "medium": 1, "high": 2}

GATE_SCHEMA = {
    "type": "object",
    "properties": {
        "onAppeal": {"type": "array", "items": {"type": "integer"},
                     "description": "Inventory ids of the case(s) on appeal."},
        "flagged": {
            "type": "array",
            "description": "One entry per cited case the Citing Case treats as more than merely cited.",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer", "description": "Inventory id."},
                    "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
                    "quote": {"type": "string", "maxLength": 600},
                    "signal": {"type": "string", "maxLength": 120},
                },
                "required": ["id", "confidence", "quote", "signal"],
            },
        },
    },
    "required": ["onAppeal", "flagged"],
}
LABEL_SCHEMA = {
    "type": "object",
    "properties": {
        "citedCases": {
            "type": "array",
            "description": "Exactly one entry per flagged case id.",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer", "description": "The flagged case id."},
                    "treatment": {"type": "string", "enum": LABELER_LABELS},
                    "otherTreatmentLabel": {"type": ["string", "null"], "maxLength": 80},
                    "opinionType": {"type": "string", "maxLength": 40},
                    "quote": {"type": ["string", "null"], "maxLength": 1200},
                    "rationale": {"type": "string", "maxLength": 600},
                },
                "required": ["id", "treatment", "otherTreatmentLabel", "opinionType", "quote", "rationale"],
            },
        }
    },
    "required": ["citedCases"],
}
TOOLS = {
    "gate": {"name": "flag_cited_cases",
             "description": "Report the cited cases the Citing Case treats as more than merely cited, "
                            "plus the case(s) on appeal.",
             "input_schema": GATE_SCHEMA},
    "label": {"name": "label_flagged_cases",
              "description": "Report the exact treatment the Citing Case applied to each flagged case.",
              "input_schema": LABEL_SCHEMA},
}


def resolve_thinking(model_key, mode):
    """mode: 'model' (the model's default from MODELS) | 'none' | 'adaptive' | '<budget tokens>'."""
    if mode == "model":
        return MODELS[model_key].get("thinking")
    if mode == "none":
        return None
    if mode == "adaptive":
        return {"type": "adaptive"}
    return {"type": "enabled", "budget_tokens": int(mode)}


def build_body(model_key, stage, system, user, thinking="model", max_tokens=None, effort=None):
    m = MODELS[model_key]
    max_tokens = max_tokens or m["max_tokens"]
    if m["family"] == "chat":
        return {"messages": [{"role": "user", "content": f"{system}\n\n{user}"}],
                "max_tokens": max_tokens, "temperature": 0}
    if m["family"] == "converse":
        return {"_converse": True, "system": system, "user": user, "max_tokens": max_tokens,
                "reasoning_effort": effort or m.get("reasoning_effort")}
    tool = TOOLS[stage]
    body = {"anthropic_version": ANTHROPIC_VERSION, "max_tokens": max_tokens, "system": system,
            "messages": [{"role": "user", "content": [{"type": "text", "text": user}]}],
            "tools": [tool]}
    th = resolve_thinking(model_key, thinking)
    if th:
        body["thinking"] = th                      # sampling params must stay unset with thinking
        body["tool_choice"] = {"type": "auto"}
    else:
        body["temperature"] = 0
        body["tool_choice"] = {"type": "tool", "name": tool["name"]}
    return body


# ── output parsing ──
def parse_model_output(mo):
    """Normalize an Anthropic-native / chat-completions / Converse response into
    {text, tool_input, stop_reason, in_tokens, out_tokens}."""
    text_parts, tool_input = [], None
    usage = mo.get("usage") or {}
    if "output" in mo and "content" not in mo:                       # Converse
        content = (mo["output"].get("message") or {}).get("content") or []
        stop = mo.get("stopReason", "")
        tin = (usage.get("inputTokens", 0) or 0) + (usage.get("cacheReadInputTokens", 0) or 0) + (usage.get("cacheWriteInputTokens", 0) or 0)
        tout = usage.get("outputTokens", 0)
    elif "choices" in mo and "content" not in mo:                    # chat completions (Kimi)
        ch = (mo.get("choices") or [{}])[0]
        c = (ch.get("message") or {}).get("content")
        text_parts = [x.get("text", "") for x in c if isinstance(x, dict)] if isinstance(c, list) else [c or ""]
        stop = ch.get("finish_reason", "")
        tin, tout = usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0)
        content = []
    else:                                                            # Anthropic native
        content = mo.get("content") or []
        stop = mo.get("stop_reason", "")
        tin = (usage.get("input_tokens", 0) or 0) + (usage.get("cache_read_input_tokens", 0) or 0) + (usage.get("cache_creation_input_tokens", 0) or 0)
        tout = usage.get("output_tokens", 0)
    for b in content:
        if not isinstance(b, dict):
            continue
        if b.get("type") == "tool_use":
            tool_input = b.get("input")
        elif "toolUse" in b:
            tool_input = b["toolUse"].get("input")
        elif b.get("type") == "text" or ("text" in b and "type" not in b):
            text_parts.append(b.get("text", ""))
    return {"text": "\n".join(t for t in text_parts if t), "tool_input": tool_input,
            "stop_reason": stop or "", "in_tokens": tin or 0, "out_tokens": tout or 0}


_FENCE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.S)


def extract_json(text):
    t = (text or "").strip()
    m = _FENCE.search(t)
    if m:
        t = m.group(1)
    i, j = t.find("{"), t.rfind("}")
    if i < 0 or j <= i:
        raise ValueError("no JSON object in model text")
    s = t[i:j + 1]
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        return json.loads(repair_json(s))


def result_from(parsed):
    """Structured result: the tool input when the model called the tool, else JSON in the text."""
    if isinstance(parsed.get("tool_input"), dict):
        return parsed["tool_input"]
    return extract_json(parsed["text"])


def _as_list(x):
    """Tool inputs sometimes arrive with an array serialized as a JSON string; decode it."""
    if isinstance(x, str):
        t = x.strip()
        if t.startswith("[") or t.startswith("{"):
            try:
                x = json.loads(t)
            except json.JSONDecodeError:
                x = json.loads(repair_json(t))
    if isinstance(x, dict):
        x = [x]
    return x if isinstance(x, list) else []


def _int(x):
    try:
        return int(str(x).strip())
    except (TypeError, ValueError):
        return None


# ── stage-specific normalization ──
def norm_gate(obj):
    """Accepts the v1 shape ({flagged:[…]}) and the v2 shape ({verdicts:[{id, more, …}]})."""
    flagged, seen = [], set()
    entries = _as_list(obj.get("flagged") or [])
    n_verdicts = None
    if "verdicts" in obj:
        verdicts = _as_list(obj["verdicts"])
        n_verdicts = len(verdicts)
        entries = list(entries) + [v for v in verdicts if isinstance(v, dict) and v.get("more") in (True, "true", "yes", 1)]
    for e in entries:
        if not isinstance(e, dict):
            e = {"id": e}
        i = _int(e.get("id"))
        if i is None or i in seen:
            continue
        seen.add(i)
        conf = str(e.get("confidence") or "medium").lower()
        flagged.append({"id": i, "confidence": conf if conf in CONF_RANK else "medium",
                        "quote": str(e.get("quote") or "")[:600], "signal": str(e.get("signal") or "")[:200]})
    on_appeal = [i for i in (_int(x) for x in _as_list(obj.get("onAppeal") or [])) if i is not None]
    out = {"onAppeal": on_appeal, "flagged": sorted(flagged, key=lambda f: f["id"])}
    if n_verdicts is not None:
        out["n_verdicts"] = n_verdicts
    return out


def merge_gate(pages):
    """Union across pages; keep the highest confidence per id."""
    best, on_appeal = {}, set()
    for p in pages:
        on_appeal |= set(p["onAppeal"])
        for f in p["flagged"]:
            cur = best.get(f["id"])
            if cur is None or CONF_RANK[f["confidence"]] > CONF_RANK[cur["confidence"]]:
                best[f["id"]] = f
    out = {"onAppeal": sorted(on_appeal), "flagged": [best[k] for k in sorted(best)]}
    if any("n_verdicts" in p for p in pages):
        out["n_verdicts"] = sum(p.get("n_verdicts", 0) for p in pages)
    return out


_LC = {t.lower(): t for t in LABELER_LABELS}
_LC_NOBY = {t.lower().removesuffix(" by"): t for t in CITING_REFERENCE + [CITED]}
_DH_LC = {t.lower() for t in DIRECT_HISTORY}


def canon_treatment(raw):
    """(canonical label, note). Out-of-scope labels (direct history, 'as recognized by')
    become 'Cited by' with a note; unknown strings become 'Other treatment' carrying the raw text."""
    r = str(raw or "").strip().rstrip(".")
    lc = r.lower()
    if lc in _LC:
        return _LC[lc], None
    if lc in _LC_NOBY:
        return _LC_NOBY[lc], None
    if lc.startswith("other"):
        return OTHER, None
    if is_recognized(r) or lc in _DH_LC or lc.replace(" as recognized by", " by") in _DH_LC:
        return CITED, f"out-of-scope label: {r}"
    if not r:
        return CITED, "empty label"
    return OTHER, r


def norm_label(obj):
    out, seen = [], set()
    for e in _as_list(obj.get("citedCases") or []):
        if not isinstance(e, dict):
            continue
        i = _int(e.get("id"))
        if i is None or i in seen:
            continue
        seen.add(i)
        treatment, note = canon_treatment(e.get("treatment"))
        other = e.get("otherTreatmentLabel") or None
        if treatment == OTHER and not other:
            other = note or "unspecified"
        out.append({"id": i, "treatment": treatment, "otherTreatmentLabel": other if treatment == OTHER else None,
                    "opinionType": str(e.get("opinionType") or ""), "quote": e.get("quote"),
                    "rationale": str(e.get("rationale") or ""), "rawTreatment": str(e.get("treatment") or ""),
                    "note": note})
    return {"citedCases": sorted(out, key=lambda c: c["id"])}


def merge_label(pages):
    """Across pages keep the most severe label per id."""
    best = {}
    for p in pages:
        for c in p["citedCases"]:
            cur = best.get(c["id"])
            if cur is None or RANK[c["treatment"]] < RANK[cur["treatment"]]:
                best[c["id"]] = c
    return {"citedCases": [best[k] for k in sorted(best)]}
