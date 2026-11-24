"""Report tokens, time and cost per engine from the saved run metadata.

Prices are list prices per million tokens on the date in PRICES_DATE for
the standard API; pages run through the Batch API are listed separately at
half price and have no time per page. No API calls.

    uv run python -m toc.usage
    uv run python -m toc.usage --pages 20-40
"""

import argparse
import json
from collections import Counter

from toc.config import OUTPUTS_DIR
from toc.pages import parse_page_range

PRICES_DATE = "2026-11-24"
BATCH_DISCOUNT = 0.5
# model -> (input, output) in USD per million tokens
PRICES = {
    "gemini-4-hydrogen": (1.50, 7.50),
    "gpt-6.2-sol": (2.00, 10.00),
}


def tokens(usage: dict) -> tuple[int, int]:
    """(input, output) tokens; reasoning tokens are billed as output."""
    if "prompt_token_count" in usage:  # Gemini
        output = usage.get("candidates_token_count", 0)
        return usage["prompt_token_count"], output + usage.get(
            "thoughts_token_count", 0
        )
    return usage.get("input_tokens", 0), usage.get("output_tokens", 0)  # OpenAI


def report(label: str, model: str, tally: Counter, discount: float) -> None:
    pages = tally["pages"]
    print(f"  {label}: {pages} pages")
    print(f"    input tokens per page    {tally['input'] / pages:>10,.0f}")
    print(f"    output tokens per page   {tally['output'] / pages:>10,.0f}")
    if tally["seconds"]:
        print(f"    seconds per page         {tally['seconds'] / pages:>10.0f}")
    if model in PRICES:
        price_in, price_out = PRICES[model]
        cost = (tally["input"] * price_in + tally["output"] * price_out) / 1e6
        cost *= discount
        print(f"    cost for these pages     {cost:>10.2f} USD")
        print(f"    cost per page            {cost / pages:>10.3f} USD")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--pages", help="page or range, e.g. 20-40")
    args = parser.parse_args()
    wanted = parse_page_range(args.pages) if args.pages else None

    print(f"list prices as of {PRICES_DATE}\n")
    for engine_dir in sorted(d for d in OUTPUTS_DIR.iterdir() if d.is_dir()):
        standard: Counter = Counter()
        batch: Counter = Counter()
        model = ""
        for path in sorted(engine_dir.glob("raw/page_*.meta.json")):
            meta = json.loads(path.read_text())
            if wanted is not None and meta["page"] not in wanted:
                continue
            model = meta["model"]
            used_in, used_out = tokens(meta.get("usage") or {})
            tally = batch if meta.get("batch") else standard
            tally["pages"] += 1
            tally["input"] += used_in
            tally["output"] += used_out
            tally["seconds"] += round(meta.get("seconds") or 0)
        if not standard["pages"] and not batch["pages"]:
            continue
        print(engine_dir.name)
        if standard["pages"]:
            report("standard API", model, standard, 1.0)
        if batch["pages"]:
            report("Batch API (half price)", model, batch, BATCH_DISCOUNT)
        print()


if __name__ == "__main__":
    main()
