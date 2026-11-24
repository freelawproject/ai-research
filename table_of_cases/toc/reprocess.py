"""Rebuild every data/outputs/<engine>/page_NNNN.json from the raw responses.

Run after changing the clean-up rules in toc.schema.to_page. The raw
responses are rewritten unchanged. No API calls.

    uv run python -m toc.reprocess
"""

import json

from toc.config import OUTPUTS_DIR
from toc.pages import save_result


def main() -> None:
    for meta_path in sorted(OUTPUTS_DIR.glob("*/raw/page_*.meta.json")):
        meta = json.loads(meta_path.read_text())
        raw_path = meta_path.with_name(meta_path.name.replace(".meta.json", ".txt"))
        engine_dir = meta_path.parent.parent
        summary = save_result(engine_dir, meta["page"], raw_path.read_text(), meta)
        print(f"{engine_dir.name} page {meta['page']}: {summary}")


if __name__ == "__main__":
    main()
