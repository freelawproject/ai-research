import colorsys
import shutil
import tempfile
from pathlib import Path

import yaml
from doclayout_yolo import YOLOv10
from PIL import Image, ImageDraw, ImageFont
from tqdm import tqdm

from ocr_tools.utils import DATA_DIR

MODELS_DIR = DATA_DIR / "models" / "yolo"
DOCLAYOUT_MODEL = MODELS_DIR / "doclayout_yolo_docstructbench_imgsz1024.pt"

MODELS = (
    ("doclayout", DOCLAYOUT_MODEL),
    ("bootstrap", MODELS_DIR / "reporter_yolo_bootstrapped.pt"),
    ("blackletter", MODELS_DIR / "blackletter.pt"),
    ("rachel", MODELS_DIR / "rachel.pt"),
    ("pagenumber", MODELS_DIR / "pagenumber" / "weights" / "best.pt"),
    ("plaintext", MODELS_DIR / "plaintext" / "weights" / "best.pt"),
)


def _to_yolo_lines(
    label: list[dict], w: int, h: int, name_to_id: dict[str, int]
) -> str:
    """Convert one pipeline-format label into normalized YOLO label lines."""
    lines = []
    for det in label:
        cls = name_to_id[det["class"]]
        x1, y1, x2, y2 = det["box"]
        cx = (x1 + x2) / 2 / w
        cy = (y1 + y2) / 2 / h
        bw = (x2 - x1) / w
        bh = (y2 - y1) / h
        lines.append(f"{cls} {cx:.6f} {cy:.6f} {bw:.6f} {bh:.6f}")
    return "\n".join(lines)


def train_yolo(
    train_images: list[Image.Image | str | Path],
    eval_images: list[Image.Image | str | Path],
    train_labels: list[list[dict]],
    eval_labels: list[list[dict]],
    name: str,
    project: str | Path = MODELS_DIR,
    base_model: str | Path = DOCLAYOUT_MODEL,
    imgsz: int = 1024,
    epochs: int = 50,
    batch: int = 16,
):
    """Train a YOLO model on images + pipeline-format labels."""
    splits = {
        "train": (train_images, train_labels),
        "val": (eval_images, eval_labels),
    }
    for split, (split_images, split_labels) in splits.items():
        if len(split_images) != len(split_labels):
            raise ValueError(
                f"{split} images and labels must be the same length"
            )

    names = sorted(
        {
            det["class"]
            for _, split_labels in splits.values()
            for label in split_labels
            for det in label
        }
    )
    name_to_id = {n: i for i, n in enumerate(names)}

    model = YOLOv10(base_model)

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp = Path(tmp_dir)
        for split, (split_images, split_labels) in splits.items():
            (tmp / "images" / split).mkdir(parents=True, exist_ok=True)
            (tmp / "labels" / split).mkdir(parents=True, exist_ok=True)

            for idx, (image, label) in enumerate(
                zip(split_images, split_labels)
            ):
                stem = f"{idx:06d}"

                if isinstance(image, str | Path):
                    src = Path(image)
                    img_path = tmp / "images" / split / f"{stem}{src.suffix}"
                    shutil.copy(src, img_path)
                    with Image.open(src) as im:
                        w, h = im.size
                else:
                    img_path = tmp / "images" / split / f"{stem}.jpg"
                    image.convert("RGB").save(img_path)
                    w, h = image.size

                (tmp / "labels" / split / f"{stem}.txt").write_text(
                    _to_yolo_lines(label, w, h, name_to_id)
                )

        data_config = {
            "path": str(tmp),
            "train": "images/train",
            "val": "images/val",
            "nc": len(names),
            "names": names,
        }
        data_yaml = tmp / "data.yaml"
        data_yaml.write_text(yaml.dump(data_config))

        model.train(
            data=str(data_yaml),
            imgsz=imgsz,
            epochs=epochs,
            batch=batch,
            save_period=-1,
            project=str(project),
            name=name,
            verbose=True,
        )


def predict_yolo(
    images: list[Image.Image] | list[str | Path],
    model: YOLOv10 | str | Path,
    batch_size: int = 1,
    imgsz: int = 1024,
    **predict_kwargs,
) -> list[dict]:
    if not isinstance(model, YOLOv10):
        model = YOLOv10(model)
    names = model.names

    for i in tqdm(range(0, len(images), batch_size), desc="Predicting"):
        batch = images[i : i + batch_size]
        batch_results = model.predict(
            batch, imgsz=imgsz, verbose=False, **predict_kwargs
        )
        for result in batch_results:
            example_result = []
            for box in result.boxes:
                class_id = int(box.cls.item())
                x1, y1, x2, y2 = box.xyxy[0].tolist()
                example_result.append(
                    {
                        "class": names[class_id],
                        "conf": box.conf.item(),
                        "box": (x1, y1, x2, y2),
                    }
                )
            yield example_result


def _iou(a: tuple, b: tuple) -> float:
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    inter = max(0.0, ix2 - ix1) * max(0.0, iy2 - iy1)
    if inter == 0:
        return 0.0
    area_a = (ax2 - ax1) * (ay2 - ay1)
    area_b = (bx2 - bx1) * (by2 - by1)
    return inter / (area_a + area_b - inter)


def deduplicate(result: list[dict], iou_threshold: float = 0.5) -> list[dict]:
    """Drop heavily-overlapping same-class detections, keeping the highest
    confidence one. ``iou_threshold`` sets how much overlap counts as a
    duplicate."""
    kept: list[dict] = []
    for det in sorted(result, key=lambda d: d["conf"], reverse=True):
        if any(
            k["class"] == det["class"]
            and _iou(k["box"], det["box"]) >= iou_threshold
            for k in kept
        ):
            continue
        kept.append(det)
    return kept


def draw_layout(
    image: Image.Image,
    result: list[dict],
    width: int = 3,
) -> Image.Image:
    image = image.convert("RGB").copy()
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default()

    def class_color(name: str) -> tuple[int, int, int]:
        hue = (hash(name) % 360) / 360
        r, g, b = colorsys.hsv_to_rgb(hue, 0.65, 0.95)
        return int(r * 255), int(g * 255), int(b * 255)

    for det in result:
        color = class_color(det["class"])
        x1, y1, x2, y2 = det["box"]
        draw.rectangle((x1, y1, x2, y2), outline=color, width=width)

        label = f"{det['class']} {det['conf']:.2f}"
        tx1, ty1, tx2, ty2 = draw.textbbox((0, 0), label, font=font)
        tw, th = tx2 - tx1, ty2 - ty1
        ly = max(0, y1 - th - 2)
        draw.rectangle((x1, ly, x1 + tw + 4, ly + th + 2), fill=color)
        draw.text((x1 + 2, ly + 1), label, fill=(255, 255, 255), font=font)

    return image
