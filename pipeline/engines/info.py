"""Model/architecture facts for every engine in the pipeline, rendered on
the viewer home page. Sourced from the models' own cards/docs (linked);
where a card does not publish internals, say so rather than guess."""

ENGINE_INFO: list[dict[str, str]] = [
    {
        "name": "container-YOLO",
        "role": "stage 2 layout — detects the containers (columns, "
        "footnote block, captions, page number, images, tables)",
        "architecture": "DocLayout-YOLO: YOLOv10 detector with a "
        "global-to-local receptive module, pretrained on DocSynth-300K "
        "synthetic pages (arXiv:2410.12628); fine-tuned in two rounds "
        "from the DocStructBench checkpoint for this reporter corpus "
        "(weights ship in the data bundle)",
        "bbox": "the detector itself — pure vision, no text output",
        "runs": "RunPod kit (GPU; CPU works)",
        "license": "Apache 2.0 (base model)",
        "link": "https://huggingface.co/juliozhao/DocLayout-YOLO-DocStructBench",
        "link_label": "Hugging Face",
    },
    {
        "name": "dots.mocr",
        "role": "stage 3 — the MAIN OCR engine in every route",
        "architecture": "3B-parameter vision-language model "
        "(rednote-hilab): in-house vision encoder + Qwen2.5-1.5B language "
        "decoder. ONE model handles both layout detection and OCR, "
        "steered by different prompts (per its technical report)",
        "bbox": "native — the language decoder GENERATES the coordinates "
        "as tokens: [x1,y1,x2,y2] + one of 11 layout categories per "
        "block; text as Markdown (HTML for tables, LaTeX for formulas)",
        "runs": "RunPod kit, GPU via vLLM (v0.11.0 image)",
        "license": "MIT",
        "link": "https://huggingface.co/rednote-hilab/dots.mocr",
        "link_label": "Hugging Face",
    },
    {
        "name": "Gemini",
        "role": "gemini route supplemental — outputs generated upstream "
        "and consumed as input data (no runner in this package)",
        "architecture": "Google multimodal API model — architecture not "
        "published",
        "bbox": "none — tagged XML with col/x/y attributes, used only as "
        "relative reading-order clues (not to scale)",
        "runs": "API (upstream)",
        "license": "proprietary API",
        "link": "https://ai.google.dev/gemini-api/docs",
        "link_label": "API docs",
    },
    {
        "name": "Mistral OCR",
        "role": "mistral + three_way route supplemental",
        "architecture": "proprietary OCR API — architecture not published",
        "bbox": "API returns per-block bboxes (include_blocks) with "
        "Markdown text",
        "runs": "API (cached outputs bundled; batch runner arrives with "
        "the RunPod kits)",
        "license": "proprietary API",
        "link": "https://docs.mistral.ai/capabilities/document/",
        "link_label": "API docs",
    },
    {
        "name": "Surya OCR 2",
        "role": "surya_line, surya_block + three_way route supplemental",
        "architecture": "650M-parameter VLM, Qwen3.5-style architecture; "
        "layout, OCR and tables share one VLM (decoder); line detection "
        "is a separate modified-EfficientViT segformer (pure PyTorch)",
        "bbox": "line mode: detector line bboxes, then per-line OCR; "
        "block mode: VLM layout blocks with bbox + confidence; text as "
        "HTML",
        "runs": "RunPod kit, GPU via vLLM (>= 0.20.1 image)",
        "license": "code Apache 2.0; weights modified AI Pubs OpenRAIL-M "
        "(research + startups under $5M; commercial license via Datalab)",
        "link": "https://huggingface.co/datalab-to/surya-ocr-2",
        "link_label": "Hugging Face",
    },
    {
        "name": "LightOnOCR-2-1B",
        "role": "stage 7 tiebreaker in the single-supplemental routes — "
        "re-reads disputed block crops (the three_way route uses none)",
        "architecture": "1B-parameter vision-language model (LightOn): "
        "Mistral-Small-3.1 vision encoder + two-layer GELU MLP projector "
        "+ Qwen3 language decoder (per its technical report)",
        "bbox": "none used — it reads the bbox crops the pipeline hands "
        "it. Known caution: on small crops the decoder can hallucinate "
        "(generating more text than the crop holds) and skews toward "
        "math/LaTeX output — the motivation for the three_way route",
        "runs": "RunPod kit (GPU via vLLM) or locally on CPU (transformers)",
        "license": "Apache 2.0",
        "link": "https://huggingface.co/lightonai/LightOnOCR-2-1B",
        "link_label": "Hugging Face",
    },
]
