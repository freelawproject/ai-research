import os
import random
from pathlib import Path

import fitz
import pandas as pd
from PIL import Image

REPO_DIR = Path(__file__).parents[1]
DATA_DIR = Path(os.getenv("DATA_DIR", Path(__file__).parents[1] / "data"))
DATA_SPLIT_PATH = DATA_DIR / "splits.csv"

RENDER_DPI = 150


def build_data_split(overwrite: bool = False):
    if not overwrite and DATA_SPLIT_PATH.exists():
        raise ValueError("Data split already exists")
    paths = list((DATA_DIR / "pages").glob("*.pdf"))
    random.shuffle(paths)
    split = int(len(paths) * 0.1)
    split_paths = [
        ("train", paths[: -split * 2]),
        ("val", paths[-split * 2 : -split]),
        ("test", paths[-split:]),
    ]
    data = []
    for split, paths in split_paths:
        for path in paths:
            data.append(
                {
                    "split": split,
                    "path": path,
                }
            )
    data = pd.DataFrame(data)
    data.to_csv(DATA_SPLIT_PATH, index=False)


def load_data(split: str | None = None):
    if not DATA_SPLIT_PATH.exists():
        build_data_split()
    data = pd.read_csv(DATA_SPLIT_PATH)
    data["path"] = data["path"].apply(Path)
    if split is None:
        return data
    return data[data["split"] == split]


def pdf_to_images(pdf_path: Path) -> list[Image.Image]:
    doc = fitz.open(pdf_path)
    try:
        pages = []
        for page in doc:
            pix = page.get_pixmap(dpi=RENDER_DPI)
            pages.append(
                Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
            )
        return pages
    finally:
        doc.close()
