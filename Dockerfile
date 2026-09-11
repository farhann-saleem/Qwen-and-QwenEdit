# Qwen-Image-Edit-2511 I2I worker. Weights on R2 — never COPY .safetensors.
# All lessons from KREA deployment applied (see ../ms-runpod-krea/RUNPOD_LESSONS.md).
FROM pytorch/pytorch:2.7.0-cuda12.8-cudnn9-devel

ENV PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    CC=gcc \
    CXX=g++ \
    COMFY_DIR=/workspace/ComfyUI

# System deps + gcc for Triton JIT (KREA Lesson #3)
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        git curl libgl1 libglib2.0-0 libx11-6 libegl1 libgles2 \
        gcc g++ build-essential \
    && rm -rf /var/lib/apt/lists/*

# Pin ComfyUI to tag — never HEAD (KREA Lesson #4), canonical URL
RUN git clone --branch v0.35.1 --depth 1 https://github.com/Comfy-Org/ComfyUI.git ${COMFY_DIR} \
    && grep -vE '^(torch|torchvision|torchaudio)([=<>]|$)' ${COMFY_DIR}/requirements.txt > /tmp/comfy-req.txt \
    && pip install --no-cache-dir -r /tmp/comfy-req.txt \
    && pip install --no-cache-dir 'runpod>=1.7.0,<2' boto3 requests

WORKDIR /workspace
COPY handler.py /handler.py

CMD ["python", "-u", "/handler.py"]
