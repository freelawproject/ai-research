from huggingface_hub import hf_hub_download
from tqdm import tqdm

from ocr_tools.utils import REPO_DIR, load_data, pdf_to_images
from ocr_tools.yolo import DOCLAYOUT_MODEL


def download_yolov8_model():
    """Download the yolov8 model"""
    path = REPO_DIR / "yolov8n.pt"
    if not path.exists():
        hf_hub_download(
            repo_id="ultralytics/yolov8",
            filename=path.name,
            local_dir=path.parent,
        )


def download_doclayout_model():
    """Download the doclayout model"""
    if not DOCLAYOUT_MODEL.exists():
        DOCLAYOUT_MODEL.parent.mkdir(parents=True, exist_ok=True)
        hf_hub_download(
            repo_id="juliozhao/DocLayout-YOLO-DocStructBench",
            filename=DOCLAYOUT_MODEL.name,
            local_dir=DOCLAYOUT_MODEL.parent,
        )


def save_images():
    """Save images for training"""
    pdf_paths = load_data()["path"].tolist()
    for pdf_path in tqdm(pdf_paths, desc="Saving images"):
        image_path = pdf_path.parent / f"{pdf_path.stem}.jpg"
        if not image_path.exists():
            img = pdf_to_images(pdf_path)[0]
            img.save(image_path)


def main():
    download_yolov8_model()
    download_doclayout_model()
    save_images()


if __name__ == "__main__":
    main()
