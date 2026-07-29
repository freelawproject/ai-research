# container-YOLO pod kit

Layout detection with the round-2 container fine-tune (DocLayout-YOLO /
YOLOv10). Reads the canonical 1700×2200 page images, predicts container boxes,
and applies the round-2 postprocess rules.

This kit does not require a GPU — CPU works, just slowly.

> **⚠️ Redacted input only.**
>
> The model was trained and evaluated exclusively on **redacted** page
> renders and does not transfer to unredacted scans. On 3,541 unredacted
> pages, 89.3% came back with zero `column` detections — unusable, and it
> fails quietly rather than erroring. Redact before rendering.
>
> Running on unredacted pages, and combining with the blackletter YOLO
> weights, is the next experiment — not a supported path today.

## Weights

Pulled at run time from **[freelawproject/container-yolo](https://huggingface.co/freelawproject/container-yolo)**
— public, no token, cached after the first download. Nothing model-shaped
ships in the kit, so every pod runs the same published checkpoint. Set
`WEIGHTS=/path/to.pt` to run a local one instead.

## Pod image

Any CUDA 12.x image (13.0 also works — this is plain torch, not vLLM, so the
host CUDA version is not load-bearing here).

`run.sh` installs `doclayout-yolo huggingface_hub dill pillow torch`.

This is a 19.5M-parameter detector, so the GPU is not the bottleneck and any
modest card clears thousands of pages in minutes. Unlike the OCR engines it does
not need a bigger or parallel GPU allocation to keep up — see the main README's
Hardware section, which sizes the OCR engines at ~3 s/page/GPU on A40s.

`infer.py` patches two upstream problems at import time:

- **torch ≥ 2.6 `weights_only` default** rejects doclayout-yolo checkpoints, so
  `torch.load` is wrapped to pass `weights_only=False`.
- **`DilatedBlock.dilated_conv` reads `self.dcv.bn`, which `fuse()` deletes** —
  patched to fall back to the fused conv's own bias when `bn` is gone.

## Run

```sh
runpodctl receive <code>
tar xzf container_yolo_<set>_kit.tar.gz && cd container_yolo
bash run.sh                 # single GPU (or CPU)
GPUS=2 bash run.sh          # or one worker per card, disjoint shards
runpodctl send container_yolo_out_<set>.tar.gz
```

## Contents

- `infer.py` — image → predict → postprocess → `out/<stem>.json`
- `postprocess.py` — round-2 detection cleanup rules
- `run.sh` — orchestrator

## Output

```
out/<stem>.json = [{label, bbox, confidence}]
```

Bboxes in 1700×2200 space.

## Licensing

The weights are **AGPL-3.0**, inherited from
[DocLayout-YOLO](https://github.com/opendatalab/DocLayout-YOLO), which this
model is a fine-tune of; the checkpoint carries `license: AGPL-3.0` in its own
metadata.

## The published weights

The checkpoint lives at `freelawproject/container-yolo` on the Hugging Face
Hub, PUBLIC — `infer.py` pulls it with no token and caches it after the first
call. `MODEL_CARD.md` here is that repo's card, kept beside the code it
describes; publishing it is a Hub-side action, not part of running a pod.
To run an unpublished checkpoint instead, pass `WEIGHTS=/path/to.pt`.
