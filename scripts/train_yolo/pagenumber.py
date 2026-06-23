"""Temporary: build an in-memory, page-number-only dataset from the bootstrap
model's outputs, for fine-tuning a focused page-number detector.

The bootstrap model finds page numbers well but has two failure modes, both
filtered out here:

  1. It misses the page number entirely. Such an example has no ``pagenumber``
     box, so we simply drop it.
  2. It tags the page number *plus* the paragraph below it as one giant box.
     Real page-number boxes are tiny (~40x30px at our render DPI); these
     failures span the column (hundreds of px). We drop any box larger than a
     fraction of the page, then drop examples left with no page number.

The per-box filter also cleans "mixed" examples (a good small box alongside a
bad giant one) rather than throwing them away.

Labels are returned in the pipeline format (lists of
``{"class", "conf", "box"}`` dicts) keyed for ``train_yolo``, so the whole
thing can be wired up as ``train_yolo(**prepare_pagenumber_dataset())``.
"""

import json

from PIL import Image

from ocr_tools.utils import load_data

MODEL_NAME = "bootstrap"
PAGENUMBER_CLASS = "pagenumber"

# Boxes wider/taller than these fractions of the page are the "tagged the whole
# paragraph" failure mode. The dataset is cleanly bimodal: real page numbers
# sit well under these, failures well over.
MAX_WIDTH_FRAC = 0.12
MAX_HEIGHT_FRAC = 0.05


def _page_numbers(label_path, page_w, page_h, max_w_frac, max_h_frac):
    """Page-number boxes from a bootstrap label file, minus oversized ones."""
    kept = []
    for det in json.loads(label_path.read_text()):
        if det["class"] != PAGENUMBER_CLASS:
            continue
        x1, y1, x2, y2 = det["box"]
        if (x2 - x1) > max_w_frac * page_w:
            continue
        if (y2 - y1) > max_h_frac * page_h:
            continue
        kept.append(det)
    return kept


def prepare_pagenumber_dataset(
    train_split: str = "train",
    eval_split: str = "val",
    model_name: str = MODEL_NAME,
    max_width_frac: float = MAX_WIDTH_FRAC,
    max_height_frac: float = MAX_HEIGHT_FRAC,
) -> dict:
    """Build a page-number-only dataset from bootstrap outputs.

    Returns a dict with ``train_images``/``eval_images`` (image paths) and
    ``train_labels``/``eval_labels`` (pipeline-format labels), ready to splat
    into ``train_yolo``.
    """
    out = {}
    for role, split in [("train", train_split), ("eval", eval_split)]:
        images, labels = [], []
        for pdf_path in load_data(split)["path"].tolist():
            image_path = pdf_path.parent / f"{pdf_path.stem}.jpg"
            label_path = (
                pdf_path.parent / f"{pdf_path.stem}.yolo.{model_name}.json"
            )
            if not image_path.exists() or not label_path.exists():
                continue
            page_w, page_h = Image.open(image_path).size
            page_numbers = _page_numbers(
                label_path, page_w, page_h, max_width_frac, max_height_frac
            )
            if not page_numbers:  # missed page number, or all boxes oversized
                continue
            images.append(image_path)
            labels.append(page_numbers)
        out[f"{role}_images"] = images
        out[f"{role}_labels"] = labels
    return out


if __name__ == "__main__":
    from ocr_tools.yolo import download_base_model, train_yolo

    dataset = prepare_pagenumber_dataset()
    print(
        f"train: {len(dataset['train_images'])} examples, "
        f"eval: {len(dataset['eval_images'])} examples"
    )

    download_base_model()
    train_yolo(**dataset, name="pagenumber", epochs=5)
