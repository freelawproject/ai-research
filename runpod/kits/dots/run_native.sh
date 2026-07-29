#!/usr/bin/env bash
# dots.mocr whole-page inference — NATIVE transformers FALLBACK (1K validation).
# Prefer run.sh (vLLM) for throughput; use this only if vLLM is unavailable.
# Run from the extracted kit root (holds run_infer_dots.py, images/).
#
#   bash run_native.sh                # isolated venv + infer + tar out/
#   bash run_native.sh i n            # shard i of n (per-GPU); merge out/ after
#
# dots.mocr runs in its OWN isolated uv venv per the model authors' spec
# (github.com/rednote-hilab/dots.mocr): torch 2.7.0 first (so flash-attn's wheel
# matches), then `pip install -e .` (pins transformers==4.57.6 + qwen-vl-utils +
# accelerate), then flash-attn 2.8.0.post2 (dots' modeling HARD-imports it).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"
SHARD="${SHARD:-0/1}"

DOTS_VENV="$ROOT/.dots-venv"
DOTS_REPO="$ROOT/dots.mocr"
DOTS_TORCH_CUDA="${DOTS_TORCH_CUDA:-cu126}"
FLASH_ATTN_WHEEL="${FLASH_ATTN_WHEEL:-https://github.com/Dao-AILab/flash-attention/releases/download/v2.8.0.post2/flash_attn-2.8.0.post2+cu12torch2.7cxx11abiTRUE-cp312-cp312-linux_x86_64.whl}"

command -v uv >/dev/null || pip install -q uv
[[ -d "$DOTS_VENV" ]] || uv venv "$DOTS_VENV" --python 3.12
export VIRTUAL_ENV="$DOTS_VENV"

echo ">> [dots] torch 2.7.0 (${DOTS_TORCH_CUDA}) into isolated venv (~5-6GB, slow to START — not stuck)"
uv pip install torch==2.7.0 torchvision==0.22.0 \
  --index-url "https://download.pytorch.org/whl/${DOTS_TORCH_CUDA}"
[[ -d "$DOTS_REPO" ]] || \
  git clone --depth 1 https://github.com/rednote-hilab/dots.mocr.git "$DOTS_REPO"
echo ">> [dots] pip install -e dots.mocr (pins transformers==4.57.6 + deps)"
uv pip install -e "$DOTS_REPO" hf_transfer pymupdf pillow
echo ">> [dots] flash-attn (required by dots' modeling check_imports)"
"$DOTS_VENV/bin/python" -c "import flash_attn" 2>/dev/null || \
  uv pip install "$FLASH_ATTN_WHEEL" || \
  { echo ">> [dots] wheel failed — compiling flash-attn (needs nvcc, ~20min)"; \
    uv pip install ninja && uv pip install flash-attn==2.8.0.post2 --no-build-isolation; }
unset VIRTUAL_ENV

# Page images are rendered locally at pack time, so the kit arrives ready to
# infer — nothing to convert here.
NIMG=0; [[ -d images ]] && NIMG=$(find images -maxdepth 1 -name '*.png' -not -name '._*' | wc -l | tr -d ' ')
[[ "$NIMG" -gt 0 ]] || { echo "!! no page images staged — see pack.sh" >&2; exit 1; }
echo ">> [dots] $NIMG page images ready"

echo ">> [dots] inferring (shard $SHARD)"
env PYTHONPATH="$ROOT" HF_HUB_ENABLE_HF_TRANSFER=1 "$DOTS_VENV/bin/python" \
  "$ROOT/run_infer_dots.py" --images-dir images --out-dir out \
  --shard "$SHARD"

echo ">> [dots] packaging"
TAR="dots_out_$(cat "$ROOT/SET" 2>/dev/null || echo out).tar.gz"
tar czf "$TAR" -C . out
echo ">> DONE → $ROOT/$TAR ($(du -h "$TAR" | cut -f1))"
echo ">> send back:  runpodctl send $TAR"
