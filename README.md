# ms-runpod-qwen

Marketing Studio — **Qwen Image Edit 2511 image-to-image** on RunPod serverless GPU.

**Status**: Working. Endpoint live and generating images.

**Job:** source image + editing instruction → edited image (I2I). Models on R2: `comfy/comfy-models/qwen-image-edit-2511/` (~30 GB). Plain **Qwen Image 2512** is T2I — that is Krea's job, not this worker.

## Live Endpoint

- **Endpoint ID**: `ko6zewns6wj3mj`
- **Repo**: `farhann-saleem/Qwen-and-QwenEdit` (branch: main)
- **GPU**: 24GB / 32GB Pro, EU-RO-1
- **ComfyUI**: v0.35.1, PyTorch 2.7.0, CUDA 12.8

## API Usage

### Ping (health check)

```bash
curl -s https://api.runpod.ai/v2/ko6zewns6wj3mj/runsync \
  -H "Authorization: $RUNPOD_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"input":{"op":"ping"}}'
```

Response: `{"ok": true, "volume_mounted": true, "comfy_up": true, "free_gb": 506540}`

### Generate (image-to-image edit)

Use `/run` (async) — sync will timeout on cold start.

```python
import base64, json, requests

API_KEY = "YOUR_RUNPOD_API_KEY"

with open("input_image.png", "rb") as f:
    img_b64 = base64.b64encode(f.read()).decode()

# Submit async job
r = requests.post(
    "https://api.runpod.ai/v2/ko6zewns6wj3mj/run",
    headers={"Authorization": API_KEY},
    json={"input": {
        "op": "generate",
        "prompt": "Make the cat wear a tiny top hat, steampunk style",
        "image_b64": img_b64,
        "steps": 20,
        "cfg": 4.0
    }},
    timeout=30
)
job_id = r.json()["id"]

# Poll until done
import time
while True:
    time.sleep(10)
    s = requests.get(
        f"https://api.runpod.ai/v2/ko6zewns6wj3mj/status/{job_id}",
        headers={"Authorization": API_KEY},
    ).json()
    if s["status"] == "COMPLETED":
        img_bytes = base64.b64decode(s["output"]["png_b64"])
        with open("output.png", "wb") as f:
            f.write(img_bytes)
        print(f"Done! {s['output']['duration_ms']}ms, ${s['output']['estimated_usd']}")
        break
    elif s["status"] == "FAILED":
        print(f"Failed: {s.get('error')}")
        break
    print(f"Status: {s['status']}...")
```

### Generate params

| Param | Default | Notes |
|-------|---------|-------|
| prompt | required | Editing instruction (natural language) |
| image_b64 | required | Base64 PNG of source image |
| width | 1024 | Output width |
| height | 1024 | Output height |
| steps | 20 | Inference steps |
| cfg | 4.0 | Classifier-free guidance scale |
| seed | random | For reproducibility |

## Performance

| Metric | Value |
|--------|-------|
| Cold start (first request, VRAM load + Triton JIT) | ~150s |
| Warm request (20 steps, 1024x1024) | ~10-30s |
| Cost per warm edit | ~$0.03 |
| Model total size on R2 | ~30 GB |

## ComfyUI Workflow (internal)

The handler builds a ComfyUI API workflow matching the official Qwen_Image_Edit_2511 workflow:

```
LoadImage → ImageScaleToTotalPixels → VAEEncode ──────────────────→ KSampler → VAEDecode → SaveImage
                                   ↓                                    ↑
CLIPLoader(qwen_image) → TextEncodeQwenImageEditPlus(positive) ────────┘
                       → TextEncodeQwenImageEditPlus(negative) ────────┘
UNETLoader → ModelSamplingAuraFlow → CFGNorm ──────────────────────────┘
VAELoader ─────────────────────────────────────────────────────────────→
```

Key nodes:
- `CLIPLoader` with `type: "qwen_image"` (NOT `qwen2_5vl`)
- `TextEncodeQwenImageEditPlus` — edit-aware encoding (takes CLIP + VAE + source image + prompt)
- `ModelSamplingAuraFlow` with `shift: 8.0`
- `CFGNorm` with `strength: 1.0`
- `ImageScaleToTotalPixels` with `resolution_steps: 64`

## Models on R2

```
comfy-models/qwen-image-edit-2511/
  diffusion_models/qwen_image_edit_2511_fp8mixed.safetensors  (20.53 GB)
  text_encoders/qwen_2.5_vl_7b_fp8_scaled.safetensors         (9.38 GB)
  vae/qwen_image_vae.safetensors                               (0.25 GB)
```

## Deployment

1. Push to `main` branch on GitHub
2. Create a GitHub **Release** to trigger RunPod rebuild
3. Old workers keep old code — must die (idle timeout) or be manually stopped before new workers spawn with updated image
4. Env vars set on RunPod endpoint (not in git): `R2_ACCOUNT_ID`, `R2_ACCESS_KEY`, `R2_SECRET_KEY`, `R2_BUCKET`

## Debugging

```bash
# Health check
curl -s https://api.runpod.ai/v2/ko6zewns6wj3mj/health \
  -H "Authorization: $RUNPOD_API_KEY"

# Purge stuck jobs
curl -s -X POST https://api.runpod.ai/v2/ko6zewns6wj3mj/purge-queue \
  -H "Authorization: $RUNPOD_API_KEY"

# Check async job status
curl -s https://api.runpod.ai/v2/ko6zewns6wj3mj/status/{JOB_ID} \
  -H "Authorization: $RUNPOD_API_KEY"
```

See `RUNPOD_LESSONS.md` for all hard-won deployment fixes.
