"""Passage-level seeder inputs: one batch record per (passage, target), using
the passage prompt and the SAME rendered text the encoder trains on.

Input: a build_windows.py --all-pairs JSONL (unlabeled passages).
Output under --out (default data/seed_passages/<tag>/):
  input.jsonl      Bedrock batch records (chat-completions body; recordId = window_id)
  manifest.json    counts, prompt version, model, token estimate

    python build_windows.py --seed-dir data/seed/pilot_kimi --all-pairs --out data/windows/pilot_passages.jsonl
    python prepare_passage_inputs.py --windows data/windows/pilot_passages.jsonl --model kimi --tag pilot_passages_kimi
"""
import argparse
import json
from pathlib import Path

from passage_prompt import PASSAGE_SCHEMA, VERSION, build_passage_prompt, render_user_message

MODELS = {"kimi": ("moonshotai.kimi-k2.5", 600), "glm5": ("zai.glm-5", 600), "glm47": ("zai.glm-4.7", 600),
          "kimi-thinking": ("moonshot.kimi-k2-thinking", 600)}
TEMPERATURE = 0.1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--windows", required=True)
    ap.add_argument("--model", choices=sorted(MODELS), default="kimi")
    ap.add_argument("--tag", default=None)
    ap.add_argument("--out", default=None)
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()
    model_id, max_tokens = MODELS[a.model]
    tag = a.tag or f"{Path(a.windows).stem}_{a.model}"
    out = Path(a.out or f"data/seed_passages/{tag}")
    out.mkdir(parents=True, exist_ok=True)
    prompt = build_passage_prompt()
    n, chars = 0, 0
    with open(a.windows, encoding="utf-8") as f, open(out / "input.jsonl", "w", encoding="utf-8") as g:
        for line in f:
            r = json.loads(line)
            header, body = r["text"].split("\n\n", 1) if "\n\n" in r["text"] else (r.get("header", ""), r["text"])
            user = render_user_message(prompt, header, body)
            g.write(json.dumps({"recordId": r["window_id"],
                                "modelInput": {"messages": [{"role": "user", "content": user}],
                                               "max_tokens": max_tokens, "temperature": TEMPERATURE}},
                               ensure_ascii=False) + "\n")
            n += 1
            chars += len(user)
            if a.limit and n >= a.limit:
                break
    json.dump({"tag": tag, "prompt_version": VERSION, "model_id": model_id, "max_tokens": max_tokens,
               "temperature": TEMPERATURE, "n_records": n, "approx_input_tokens": chars // 4,
               "prompt_chars": len(prompt), "windows": a.windows, "schema": PASSAGE_SCHEMA},
              open(out / "manifest.json", "w"), indent=1)
    print(f"{tag}: {n} records, ~{chars / 4 / 1e6:.1f}M input tokens (prompt ~{len(prompt) // 4:,} tokens each), "
          f"model {model_id} -> {out}")


if __name__ == "__main__":
    main()
