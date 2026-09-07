"""olmOCR-2 (Qwen2.5-VL-7B) runner — invoked as a subprocess by
``shelf.worker.olmocr``.

Ported from the standalone ``scripts/olmocr/reocr.py`` script in the
operator's gitops repo, kept self-contained so it can be invoked
straight from the worker as ``python -m shelf.worker.olmocr_runner
<input.pdf> <output.pdf>``. The subprocess shape is what makes
SIGTERM-based cancellation work: torch + transformers won't let us
interrupt them in-process, but a child PID is killable.

Progress is printed to stderr in the same shape ``ocrmypdf`` uses, so
the worker's existing stderr-progress parser picks up:

    Parsing 12 pages with HocrParser   # signals total
       1 redoing OCR                   # signals page-1 start
       2 redoing OCR

The worker translates those into the SPA's "page N / M" indicator.

Outline generation moved out: it's now a separate Phase C job
(``shelf.worker.outline``) that runs on the CPU pod after the OCR'd
PDF is uploaded. This file does OCR only — render, generate text,
embed it as a hidden text layer, save. No bookmarks here.

Lazy imports of torch / transformers / olmocr keep this module
importable on the CPU pod (which only invokes the *worker* at
``shelf.worker.olmocr``, never this runner) without dragging in
~5 GB of ML deps. The runner itself only ever runs on the GPU pod.
"""

from __future__ import annotations

import argparse
import base64
import platform
import sys
import time
from io import BytesIO
from pathlib import Path
from typing import Any

# Constants shared with the original reocr.py — keep the model id
# and the per-page rendering settings stable across the two scripts
# so a doc OCR'd here is bit-for-bit identical to one OCR'd via the
# manual reocr.py CLI.
PROCESSOR_ID = "Qwen/Qwen2.5-VL-7B-Instruct"
TARGET_IMAGE_DIM = 1288  # longest side for page rasterisation
MAX_NEW_TOKENS = 4096


# ── Hardware detection / model loading ──────────────────────────────────────


def detect_hardware() -> dict[str, Any]:
    """Detect GPU + compute capability, return a dict the profile
    builder reads. Pure: no torch / transformers calls beyond
    ``torch.cuda.*``, so the runner fails fast if no CUDA device is
    present rather than dragging in the full stack."""
    import torch

    info: dict[str, Any] = {
        "hostname": platform.node(),
        "cpu": platform.processor() or platform.machine(),
        "os": f"{platform.system()} {platform.release()}",
    }
    if not torch.cuda.is_available():
        info["gpu"] = None
        info["gpu_vram_mb"] = 0
        info["gpu_compute_capability"] = None
        return info
    info["gpu"] = torch.cuda.get_device_name(0)
    info["gpu_vram_mb"] = torch.cuda.get_device_properties(0).total_memory // (
        1024 * 1024
    )
    major, minor = torch.cuda.get_device_capability(0)
    info["gpu_compute_capability"] = f"{major}.{minor}"
    arch_names = {6: "Pascal", 7: "Volta/Turing", 8: "Ampere", 9: "Hopper/Ada"}
    info["gpu_arch"] = arch_names.get(major, f"SM {major}.x")
    return info


def build_profile(hardware: dict[str, Any]) -> dict[str, Any]:
    """Return the right model + dtype + quantisation for the host.

    Ampere+ with ≥10 GB VRAM: native FP8 model, bfloat16 compute.
    Ampere+ with less VRAM: standard model, 4-bit quant, bfloat16.
    Pascal / Volta / Turing: standard model, 4-bit quant, float16
    (bfloat16 isn't supported on those archs).
    """
    cc = hardware.get("gpu_compute_capability")
    vram = hardware.get("gpu_vram_mb", 0)
    if cc is None:
        sys.stderr.write(
            "olmocr_runner: no CUDA GPU detected; aborting.\n"
        )
        sys.exit(2)
    major = int(cc.split(".")[0])
    if major >= 8 and vram >= 10000:
        return {
            "model_id": "allenai/olmOCR-2-7B-1025-FP8",
            "quantization": None,
            "compute_dtype": "bfloat16",
        }
    if major >= 8:
        return {
            "model_id": "allenai/olmOCR-2-7B-1025",
            "quantization": "4bit",
            "compute_dtype": "bfloat16",
        }
    return {
        "model_id": "allenai/olmOCR-2-7B-1025",
        "quantization": "4bit",
        "compute_dtype": "float16",
    }


def load_model(profile: dict[str, Any]) -> tuple[Any, Any, Any]:
    """Instantiate Qwen2.5-VL according to the profile. Lazy-imports
    torch + transformers so unrelated callers (admin scripts, tests)
    don't pay the ~10 s import cost just to print --help."""
    import torch
    from transformers import (
        AutoProcessor,
        BitsAndBytesConfig,
        Qwen2_5_VLForConditionalGeneration,
    )

    model_id = profile["model_id"]
    dtype_name = profile["compute_dtype"]
    compute_dtype = (
        torch.bfloat16 if dtype_name == "bfloat16" else torch.float16
    )

    load_kwargs: dict[str, Any] = {"device_map": "auto"}
    quant = profile.get("quantization")
    if quant == "4bit":
        load_kwargs["quantization_config"] = BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_compute_dtype=compute_dtype,
        )
    elif quant == "8bit":
        load_kwargs["quantization_config"] = BitsAndBytesConfig(load_in_8bit=True)
    else:
        load_kwargs["torch_dtype"] = compute_dtype

    sys.stderr.write(f"olmocr_runner: loading {model_id} ({dtype_name})\n")
    sys.stderr.flush()
    t0 = time.time()
    model = Qwen2_5_VLForConditionalGeneration.from_pretrained(
        model_id, **load_kwargs
    ).eval()
    processor = AutoProcessor.from_pretrained(PROCESSOR_ID)
    device = next(model.parameters()).device
    sys.stderr.write(
        f"olmocr_runner: model on {device} in {time.time() - t0:.1f}s\n"
    )
    sys.stderr.flush()
    return model, processor, device


# ── Per-page OCR ────────────────────────────────────────────────────────────


def ocr_page(
    model: Any,
    processor: Any,
    device: Any,
    pdf_path: str,
    page_num: int,
    existing_text: str = "",
) -> str:
    """Render one PDF page → run Qwen → return plain text."""
    import torch
    from olmocr.data.renderpdf import render_pdf_to_base64png
    from olmocr.prompts import build_finetuning_prompt
    from PIL import Image

    image_b64 = render_pdf_to_base64png(
        pdf_path, page_num, target_longest_image_dim=TARGET_IMAGE_DIM
    )
    prompt_text = build_finetuning_prompt(existing_text)

    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": prompt_text},
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:image/png;base64,{image_b64}"
                    },
                },
            ],
        }
    ]
    text = processor.apply_chat_template(
        messages, tokenize=False, add_generation_prompt=True
    )
    image = Image.open(BytesIO(base64.b64decode(image_b64)))
    inputs = processor(
        text=[text], images=[image], padding=True, return_tensors="pt"
    )
    inputs = {k: v.to(device) for k, v in inputs.items()}

    with torch.no_grad():
        output = model.generate(
            **inputs,
            temperature=0.1,
            max_new_tokens=MAX_NEW_TOKENS,
            num_return_sequences=1,
            do_sample=True,
        )
    prompt_len = inputs["input_ids"].shape[1]
    new_tokens = output[:, prompt_len:]
    # Annotated because `processor` is Any (transformers isn't typed), and
    # without it both returns below leak Any out of a -> str function.
    result: str = processor.tokenizer.batch_decode(
        new_tokens, skip_special_tokens=True
    )[0]
    if result.startswith("---"):
        parts = result.split("---", 2)
        if len(parts) >= 3:
            return parts[2].strip()
    return result.strip()


# ── Top-level: process one PDF end-to-end ───────────────────────────────────


def process_pdf(
    model: Any, processor: Any, device: Any, inp: Path, out: Path
) -> None:
    """Render → OCR → write a new PDF with a replaced text layer.
    Progress goes to stderr in the format the worker's stderr-progress
    parser already understands. Outlines/bookmarks are added by the
    Phase C outline worker after this PDF is uploaded."""
    import fitz

    doc = fitz.open(str(inp))
    num = len(doc)
    # Match the format ocrmypdf uses so the existing stderr parser
    # pulls progress_total without modification.
    sys.stderr.write(f"Parsing {num} pages with HocrParser\n")
    sys.stderr.flush()

    for idx in range(num):
        page_num = idx + 1
        sys.stderr.write(f"   {page_num} redoing OCR\n")
        sys.stderr.flush()
        try:
            existing = doc[idx].get_text()
            text = ocr_page(
                model, processor, device, str(inp), page_num, existing
            )
        except Exception as e:
            sys.stderr.write(
                f"   {page_num}: ERROR {type(e).__name__}: {e}\n"
            )
            sys.stderr.flush()
            continue

        page = doc[idx]
        tw = fitz.TextWriter(page.rect)
        font = fitz.Font("helv")
        y = 10
        for line in text.split("\n"):
            if not line.strip():
                continue
            try:
                tw.append((10, y), line.strip(), font=font, fontsize=1)
                y += 2
            except Exception:
                continue
        tw.write_text(page, opacity=0)

    doc.save(str(out), garbage=4, deflate=True)
    doc.close()


def main() -> None:
    p = argparse.ArgumentParser(
        prog="shelf.worker.olmocr_runner",
        description="Re-OCR a PDF using olmOCR-2 (Qwen2.5-VL-7B).",
    )
    p.add_argument("input", type=Path, help="Input PDF path")
    p.add_argument("output", type=Path, help="Output PDF path")
    args = p.parse_args()

    if not args.input.is_file():
        sys.stderr.write(f"olmocr_runner: input not found: {args.input}\n")
        sys.exit(2)

    hardware = detect_hardware()
    profile = build_profile(hardware)
    model, processor, device = load_model(profile)
    process_pdf(model, processor, device, args.input, args.output)


if __name__ == "__main__":
    main()
