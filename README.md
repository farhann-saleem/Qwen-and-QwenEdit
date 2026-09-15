<div align="center">
  <h1 align="center">Qwen Image Edit 2511</h1>
  <p align="center"><i>Image → image for Marketing Studio</i></p>

  [![Python](https://img.shields.io/badge/Python-3.11-blue.svg?style=for-the-badge&logo=python&logoColor=white)](https://python.org)
  [![RunPod](https://img.shields.io/badge/RunPod-Serverless%20GPU-7B2FF7.svg?style=for-the-badge)](https://runpod.io)
  [![ComfyUI](https://img.shields.io/badge/ComfyUI-0.35.1-black.svg?style=for-the-badge)](https://github.com/comfyanonymous/ComfyUI)
</div>

---

> Source image + a sentence. Edited PNG back. The most-exercised GPU worker in the stack.

**Qwen-and-QwenEdit** is the image-edit leg of [Marketing Studio](https://github.com/farhann-saleem/sixeyes). One RunPod serverless GPU runs ComfyUI + Qwen Image Edit 2511 fp8mixed; ~30 GB of weights sit on R2 and warm onto an EU-RO-1 network volume. Warm edits ~$0.03.

Plain Qwen Image T2I is the [Krea worker](https://github.com/farhann-saleem/Krea-2-Turbo)’s job. This repo is edit-aware I2I only.

| | |
| --- | --- |
| **Endpoint** | `ko6zewns6wj3mj` · **EU-RO-1 only** |
| **Stack** | ComfyUI v0.35.1 · PyTorch 2.7.0 · CUDA 12.8 |
| **Defaults** | 20 steps · cfg 4.0 · AuraFlow shift 8.0 |

---

## Architecture

1. **The Core** — ComfyUI image with no weights baked in.
2. **The Swapper** — Boot mounts the volume, pulls UNET/CLIP/VAE from R2, starts Comfy, then accepts jobs. CLIP type is `qwen_image`; encoder is `TextEncodeQwenImageEditPlus`.
3. **The Delivery** — `generate` returns inline `png_b64`. Always health → ping → generate via `/run` + poll.

```
Marketing Studio backend ──► THIS WORKER (GPU I2I)
                          ├── Krea-2-Turbo (T2I)
                          ├── Faceswap-and-FF (CPU)
                          └── Modal LTX-2.5
```

In product: Avatar provider `qwen` + Images I2I. Default avatar remains OpenRouter FLUX.2 Klein.

---

## One request

```json
{
  "input": {
    "op": "generate",
    "prompt": "Make the cat wear a tiny top hat, steampunk style",
    "image_b64": "<base64 PNG>",
    "steps": 20,
    "cfg": 4.0
  }
}
```

```bash
curl -s https://api.runpod.ai/v2/ko6zewns6wj3mj/health \
  -H "Authorization: Bearer $RUNPOD_API_KEY"
# stop if throttled > 0, then ping, then /run the payload above
```

---

## Setup

Deploy & smoke: **[SETUP.md](SETUP.md)** · Graph: **[ARCHITECTURE.md](ARCHITECTURE.md)** · Weights: **[MODELS.md](MODELS.md)**

Siblings: [sixeyes](https://github.com/farhann-saleem/sixeyes) · [Krea-2-Turbo](https://github.com/farhann-saleem/Krea-2-Turbo) · [Faceswap-and-FF](https://github.com/farhann-saleem/Faceswap-and-FF)

<div align="center">
  <i>Edit the frame. Keep the identity.</i>
</div>
