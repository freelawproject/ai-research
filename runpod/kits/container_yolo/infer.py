"""Container-YOLO layout inference — STANDALONE pod runner. Reads the
canonical 1700x2200 page images (rendered by to_images.py before inference),
runs the round-2 container fine-tune, applies the postprocess rules, and
writes one detections JSON per page:

    out/<stem>.json = [{label, bbox, confidence}]   (bbox in 1700x2200 space)

    python infer.py --images-dir images --out-dir out \
        [--conf 0.2 --imgsz 1024 --device 0 --shard 0/1 --weights PATH]

WEIGHTS COME FROM THE HUB by default — freelawproject/container-yolo, pulled
once and cached under HF_HOME. Nothing model-shaped ships in the kit, so a
kit tarball stays small and every pod runs the same published checkpoint
rather than whatever .pt happened to be staged. Pass --weights to override
with a local file (an unpublished experiment, or an air-gapped pod).

Resume-safe (skips existing outputs); --shard i/n runs every n-th page for
multi-GPU. No dependency on any other project — deps installed by run.sh.
"""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

import torch
from PIL import Image

from postprocess import postprocess_dets

HF_REPO = "freelawproject/container-yolo"
HF_FILE = "container_round2.pt"

# doclayout-yolo checkpoints trip torch>=2.6's weights_only default
_orig_load = torch.load


def _load(*a, **k):
    k["weights_only"] = False
    return _orig_load(*a, **k)


torch.load = _load

from doclayout_yolo import YOLOv10  # noqa: E402


def _fix_fused_dilated_conv():
    """doclayout_yolo bug: DilatedBlock.dilated_conv reads self.dcv.bn, which
    fuse() deletes. When bn is gone, use the fused conv's own bias."""
    import torch.nn.functional as F
    from doclayout_yolo.nn.modules import g2l_crm

    def dilated_conv(self, x, dilation):
        act = self.dcv.act
        weight = self.dcv.conv.weight
        padding = dilation * (self.k // 2)
        bn = getattr(self.dcv, "bn", None)
        if bn is not None:
            return act(
                bn(
                    F.conv2d(
                        x, weight, stride=1, padding=padding, dilation=dilation
                    )
                )
            )
        return act(
            F.conv2d(
                x,
                weight,
                self.dcv.conv.bias,
                stride=1,
                padding=padding,
                dilation=dilation,
            )
        )

    g2l_crm.DilatedBlock.dilated_conv = dilated_conv


def _fetch_weights(repo: str, filename: str) -> str:
    """Download the published checkpoint (cached after the first call).

    The repo is public, so no token is needed; hf_hub_download still honours
    HF_TOKEN if one happens to be set.
    """
    from huggingface_hub import hf_hub_download

    print(f"fetching {repo}/{filename} …", flush=True)
    path = hf_hub_download(repo_id=repo, filename=filename)
    print(f"  weights: {path}", flush=True)
    return path


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--weights", default="", help="local .pt; default pulls from the Hub"
    )
    ap.add_argument(
        "--hf-repo",
        default=HF_REPO,
        help="Hub repo to pull the checkpoint from",
    )
    ap.add_argument("--hf-file", default=HF_FILE)
    ap.add_argument(
        "--images-dir",
        default="images",
        help="dir with <stem>.png page images",
    )
    ap.add_argument("--out-dir", default="out")
    ap.add_argument("--device", default="0")
    ap.add_argument("--imgsz", type=int, default=1024)
    ap.add_argument("--conf", type=float, default=0.2)
    ap.add_argument("--shard", default="0/1", help="i/n — run every n-th page")
    ap.add_argument(
        "--raw",
        action="store_true",
        help="skip postprocess (write raw detections)",
    )
    args = ap.parse_args()

    _fix_fused_dilated_conv()
    weights = args.weights or _fetch_weights(args.hf_repo, args.hf_file)
    images_dir, out = Path(args.images_dir), Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    shard_i, shard_n = (int(v) for v in args.shard.split("/"))

    stems = sorted(
        p.stem for p in images_dir.glob("*.png") if not p.name.startswith("._")
    )[shard_i::shard_n]
    todo = [s for s in stems if not (out / f"{s}.json").exists()]
    print(
        f"{len(stems)} pages in shard {args.shard}, {len(todo)} to do",
        flush=True,
    )

    model = YOLOv10(weights)
    names = model.names
    t0, done = time.time(), 0
    for stem in todo:
        img = Image.open(images_dir / f"{stem}.png").convert("RGB")
        res = model.predict(
            img,
            imgsz=args.imgsz,
            conf=args.conf,
            device=args.device,
            verbose=False,
        )[0]
        dets = [
            {
                "label": names[int(b.cls[0])],
                "bbox": [round(float(v), 1) for v in b.xyxy[0].tolist()],
                "confidence": round(float(b.conf[0]), 3),
            }
            for b in res.boxes
        ]
        if not args.raw:
            dets = postprocess_dets(dets)
        # Atomic: resume reads "file exists" as "page done", so a run killed
        # mid-write must never leave a truncated JSON to be skipped forever.
        tmp = out / f"{stem}.json.part"
        tmp.write_text(json.dumps(dets))
        os.replace(tmp, out / f"{stem}.json")
        done += 1
        if done % 200 == 0:
            rate = done / (time.time() - t0)
            eta = (len(todo) - done) / rate / 3600 if rate else 0
            print(
                f"  {done}/{len(todo)}  {rate:.1f} pg/s  ETA {eta:.1f}h",
                flush=True,
            )
    print(f"done: {done} pages → {out}", flush=True)


if __name__ == "__main__":
    main()
