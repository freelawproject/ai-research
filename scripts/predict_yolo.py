import argparse
import json

from ocr_tools.utils import load_data
from ocr_tools.yolo import MODELS, predict_yolo


def run_model_predictions(
    model_name: str,
    model_path: str,
    split: str | None = None,
    batch_size: int = 32,
    device: str = "cuda",
    overwrite: bool = False,
):
    pdf_paths = load_data(split)["path"].tolist()
    image_paths = [
        pdf_path.parent / f"{pdf_path.stem}.jpg" for pdf_path in pdf_paths
    ]
    out_paths = [
        pdf_path.parent / f"{pdf_path.stem}.yolo.{model_name}.json"
        for pdf_path in pdf_paths
    ]
    if overwrite:
        for out_path in out_paths:
            if out_path.exists():
                out_path.unlink()
    pending = [i for i in range(len(out_paths)) if not out_paths[i].exists()]
    for i, result in zip(
        pending,
        predict_yolo(
            [image_paths[i] for i in pending],
            model_path,
            batch_size=batch_size,
            device=device,
        ),
    ):
        out_paths[i].write_text(json.dumps(result))


def main():
    parser = argparse.ArgumentParser(
        description="Run YOLO predictions over the dataset."
    )
    parser.add_argument(
        "--model",
        default=None,
        help="Only run this model (default: all models).",
    )
    parser.add_argument(
        "--split",
        default=None,
        help="Data split to run on (default: all).",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=32,
        help="Prediction batch size.",
    )
    parser.add_argument(
        "--device",
        default="cuda",
        help="Device to run inference on.",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite existing prediction files.",
    )
    args = parser.parse_args()

    for model_name, model_path in MODELS:
        if args.model is None or model_name == args.model:
            run_model_predictions(
                model_name,
                model_path,
                args.split,
                args.batch_size,
                args.device,
                args.overwrite,
            )


if __name__ == "__main__":
    main()
