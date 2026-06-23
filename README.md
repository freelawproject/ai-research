# OCR Tools

## Data Dir

Defaults to `data` in the root of the repository. Can be overridden by setting the `DATA_DIR` environment variable.

Current structure:

```
data/
├── models/
│   ├── yolo/
│   │   ├── doclayout_yolo_docstructbench_imgsz1024.pt
│   │   ├── reporter_yolo_bootstrapped.pt
│   │   ├── blackletter.pt
│   │   ├── rachel.pt
│   │   ├── {model_name}/weights/best.pt  # Weights for models we train
│   ├── ocr_bootstrapped  # Adapter weights for the GOT-OCR model
│   └── ...
├── pages/
│   ├── f4th.118.1150-1180__page_001.pdf  # PDF page
│   ├── f4th.118.1150-1180__page_001.jpg  # Rendered image of the page
│   ├── f4th.118.1150-1180__page_001.yolo.{model_name}.json  # YOLO predictions for the page for a given model
│   └── ...
├── splits.csv  # Tracks data splits among pages for training, eval, and holdout
└── ...
```

## Layout Detection

Models are registered in the `MODELS` tuple in `ocr_tools/yolo.py`. You can add a model to this list after you train one.

### Some helpful utilities

```python
from ocr_tools.utils import load_data
from ocr_tools.yolo import train_yolo, predict_yolo

# Expected labels format is a list of dicts matching the format returned by `predict_yolo`
train_yolo(
    train_images=train_image_paths,
    eval_images=eval_image_paths,
    train_labels=train_labels,
    eval_labels=eval_labels,
    name="my_model",
    base_model=DOCLAYOUT_MODEL,
)

preds = predict_yolo(
    image_paths,
    model_path,
    batch_size=32,
    device="cuda",
)
```

### Scripts

#### Setup

One time setup. Downloads the DocLayout model and the YOLOv8 model, and saves the images for the dataset.

```bash
python scripts/setup.py
```

#### Predict YOLO

Runs YOLO predictions for all models on all pages in the dataset.

```bash
python scripts/predict_yolo.py
```

Predictions are saved to the `pages` directory as `{pdf_name}.yolo.{model_name}.json` files.

#### Viewer

Runs a local web viewer for the dataset.

```bash
python ocr_tools.viewer
```

Viewer is available at `http://localhost:8000`.

Viewer shows the images in the `pages` directory, and the YOLO predictions for each model in the `pages` directory.

Viewer is a useful tool for debugging and visualizing the predictions.

#### One-off training scripts

These are one-off training scripts that use the predictions from other models and apply heuristics to create new, better training data for training single-class new models.

```bash
python scripts/train_yolo/pagenumber.py
python scripts/train_yolo/plaintext.py
```

Models saved to the `models` directory as `{model_name}/weights/best.pt`.


## Text Extraction

The adapter weights for the hackily-trained OCR model are saved to the `models` directory in `ocr_bootstrapped`. These can be loaded into GOT-OCR for text extraction.

TODO:
- Apply the `plaintext` model to identify bounding boxes for any text to be extracted.
- Run the bootstrapped OCR model on the text blocks.
- Apply a (different) promptable VLM to text blocks.
- Compare and look for heuristics for improvement, fixing with Gemini where feasible
- Train a new OCR model on the improved text blocks.