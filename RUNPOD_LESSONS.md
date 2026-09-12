# RunPod Serverless Lessons Learned

Hard-won fixes from deploying KREA2-Turbo. Apply these to every new endpoint (Qwen, FFmpeg, etc).

## Issue 1: Volume Datacenter Mismatch

**Symptom**: Workers stuck in "initializing", no runtime logs, workers crash silently.

**Root cause**: Network volume was in `EUR-IS-3`, GPUs spawned in different DC. RunPod can't mount cross-DC volumes — fails silently.

**Fix**:
- Volume and endpoint **must be in same datacenter**
- In endpoint settings → Data Centers → select **only** the DC where your volume lives
- Never select "all datacenters" if you use a network volume

**How to verify**: Send a ping request, check `volume_mounted: true` in response.

---

## Issue 2: PyTorch + ComfyUI Version Incompatibility

**Symptom**: `ValueError: infer_schema(func): Parameter kernel_size has unsupported type list[int]`

**Root cause**: ComfyUI v0.35.x ships `comfy_kitchen` which uses Python 3.10+ type hints (`list[int]`). PyTorch 2.6.0's `torch.library.infer_schema` only accepts `typing.List[int]`.

**Fix**: Use PyTorch >= 2.7.0 base image.

**Rule**: Always check ComfyUI release notes for minimum PyTorch version before pinning.

---

## Issue 3: Missing C Compiler for Triton JIT

**Symptom**: `RuntimeError: Failed to find C compiler. Please specify via CC environment variable.`

**Root cause**: Triton (used by ComfyUI for fast attention) JIT-compiles CUDA kernels at runtime. Needs `gcc`. Neither `-runtime` nor `-devel` PyTorch images guarantee `gcc` in PATH.

**Fix**: Always install build tools in Dockerfile:
```dockerfile
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        gcc g++ build-essential \
    && rm -rf /var/lib/apt/lists/*

ENV CC=gcc CXX=g++
```

**Rule**: Any image using Triton (ComfyUI, diffusers, vLLM) needs gcc.

---

## Issue 4: ComfyUI Repo URL Changed

**Symptom**: `git clone` may fail or get stale code.

**Root cause**: `comfyanonymous/ComfyUI` now 301 redirects to `Comfy-Org/ComfyUI`.

**Fix**: Use canonical URL and pin version:
```dockerfile
RUN git clone --branch v0.35.1 --depth 1 https://github.com/Comfy-Org/ComfyUI.git ${COMFY_DIR}
```

**Rule**: Always pin to a tag. Never clone HEAD — ComfyUI breaks weekly.

---

## Issue 5: Worker Throttling After Crashes

**Symptom**: `throttled=1` or `throttled=2` in health check. No workers start. Jobs sit in queue forever.

**Root cause**: RunPod exponentially backs off worker startup after repeated crashes. Account-level — persists across endpoint deletion.

**Fix**:
1. Fix the actual crash first
2. Delete the endpoint, create a fresh one
3. Purge the job queue: `POST /v2/{endpoint}/purge-queue`
4. If throttled on new endpoint, wait 10-15 min for backoff to expire

**Rule**: Never spam retries on a broken endpoint. Fix root cause, then fresh endpoint.

---

## Issue 6: Cold Start = ComfyUI Startup on Every Request

**Symptom**: First request takes ~100s even with cached models. `download_ms: 39ms` but `execution_ms: 98000ms`.

**Root cause**: ComfyUI subprocess starts fresh on each cold worker. Loading model into VRAM + Triton compilation = ~90s.

**Fix**: Move init before `runpod.serverless.start()`:
```python
# Worker init — runs once when worker starts, before accepting jobs
ensure_weights()
ensure_comfy()
runpod.serverless.start({"handler": handler})
```

Worker shows "initializing" longer, but requests complete in ~5-10s.

**Rule**: Always do heavy init (model loading, server startup) before `runpod.serverless.start()`.

---

## Issue 7: ComfyUI CLIPLoader Type Enum Mismatch

**Symptom**: `Comfy queue 400: value_not_in_list — type: 'qwen2_5vl' not in (list of length 28)`

**Root cause**: ComfyUI's `CLIPLoader` node has a strict enum for the `type` parameter. The Qwen CLIP type is `"qwen_image"`, not `"qwen2_5vl"` or any other variant. The full list (as of v0.35.x) includes: `stable_diffusion`, `stable_cascade`, `sd3`, `stable_audio`, `mochi`, `ltxv`, `pixart`, `cosmos`, `lumina2`, `wan`, `hidream`, `chroma`, `ace`, `omnigen2`, `hunyuan_image`, `flux2`, `ovis`, `longcat_image`, `cogvideox`, `lens`, `pixeldit`, `ideogram4`, `boogu`, `krea2`, `joyimage`, `mage`, `minimax`, `qwen_image`.

**Fix**: Use exact enum value:
```python
"class_type": "CLIPLoader",
"inputs": {"clip_name": "qwen_2.5_vl_7b_fp8_scaled.safetensors", "type": "qwen_image"},
```

**Rule**: Never guess CLIP type names. Check ComfyUI source or use `/object_info` API to list valid values.

---

## Issue 8: ComfyUI Node Input Names Differ from Widget Labels

**Symptom**: `required_input_missing` for `prompt`, `strength`, `resolution_steps` — even though you passed `text`, omitted `strength`, etc.

**Root cause**: ComfyUI API input names don't always match UI widget labels or what you'd expect. Examples from Qwen workflow:
- `TextEncodeQwenImageEditPlus`: input is `prompt`, not `text`
- `CFGNorm`: requires `strength` parameter (e.g., `1.0`)
- `ImageScaleToTotalPixels`: requires `resolution_steps` (e.g., `64`)

**Fix**: Always verify node inputs against ComfyUI's `/object_info/{node_type}` endpoint or the official workflow JSON. Don't assume parameter names.

**Rule**: When building ComfyUI API workflows, start from a known-working workflow JSON. Don't hand-write node inputs from memory.

---

## Issue 9: Qwen Image Edit Requires Specialized Nodes

**Symptom**: Standard `CLIPTextEncode` produces garbage or wrong conditioning for Qwen Image Edit models.

**Root cause**: Qwen Image Edit 2511 uses `TextEncodeQwenImageEditPlus` — a specialized text encoder that takes CLIP + VAE + source image(s) + prompt to create edit-aware conditioning. Standard `CLIPTextEncode` doesn't understand image context.

**Fix**: Use the official workflow node chain:
```python
# Edit-aware text encoding — NOT standard CLIPTextEncode
"class_type": "TextEncodeQwenImageEditPlus",
"inputs": {"clip": [...], "vae": [...], "image1": [...], "prompt": "edit instruction"},

# Model chain — required for Qwen diffusion sampling
UNETLoader → ModelSamplingAuraFlow(shift=8.0) → CFGNorm(strength=1.0) → KSampler
```

**Rule**: For model-specific workflows (Qwen, Flux, etc.), always start from the official ComfyUI workflow. Don't substitute standard nodes for specialized ones.

---

## Issue 10: Old Workers Serve Stale Code After Image Rebuild

**Symptom**: Push fix to GitHub, RunPod builds new image, but requests still hit old code and fail with the same error.

**Root cause**: Running workers keep their container image until they die. New image only applies to newly spawned workers.

**Fix**:
1. Set idle timeout to 5s
2. Purge queue: `POST /v2/{endpoint}/purge-queue`
3. Wait for old workers to idle out (or manually stop via dashboard)
4. New request triggers fresh worker with updated image

**Rule**: After deploying a fix, always verify the worker ID in the response is different from the old one. Check logs for the new worker's startup messages.

---

## Issue 11: Disk Space on Container Without Volume

**Symptom**: `free_gb: 4.98` — only ~5GB available. Model downloads fail or fill disk.

**Root cause**: Without volume mount, container rootfs is tiny (~5GB). Models need ~18GB+.

**Fix**: Always attach a network volume. Check with ping: `free_gb` should be >> model size.

---

## Dockerfile Template (Battle-Tested)

```dockerfile
FROM pytorch/pytorch:2.7.0-cuda12.8-cudnn9-devel

ENV PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    CC=gcc \
    CXX=g++ \
    COMFY_DIR=/workspace/ComfyUI

# System deps + compiler for Triton
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        git curl libgl1 libglib2.0-0 libx11-6 libegl1 libgles2 \
        gcc g++ build-essential \
    && rm -rf /var/lib/apt/lists/*

# Pin ComfyUI version — never use HEAD
RUN git clone --branch v0.35.1 --depth 1 https://github.com/Comfy-Org/ComfyUI.git ${COMFY_DIR} \
    && grep -vE '^(torch|torchvision|torchaudio)([=<>]|$)' ${COMFY_DIR}/requirements.txt > /tmp/comfy-req.txt \
    && pip install --no-cache-dir -r /tmp/comfy-req.txt \
    && pip install --no-cache-dir 'runpod>=1.7.0,<2' boto3 requests

WORKDIR /workspace
COPY handler.py /handler.py

CMD ["python", "-u", "/handler.py"]
```

---

## Handler Template (Key Patterns)

```python
# 1. Debug prints at top — shows where crash happens
print(">>> handler.py starting", flush=True)

# 2. Try/except on every import
try:
    import runpod
    print(f">>> runpod ok (version={getattr(runpod, '__version__', '?')})", flush=True)
except Exception as e:
    print(f">>> runpod FAILED: {e}", flush=True)
    sys.exit(1)

# 3. Heavy init BEFORE serverless.start()
ensure_weights()
ensure_comfy()
runpod.serverless.start({"handler": handler})

# 4. Ping op for health checks — no model loading
if op == "ping":
    return {"ok": True, "volume_mounted": ..., "free_gb": ...}
```

---

## Endpoint Creation Checklist

- [ ] Network volume created in target datacenter
- [ ] Endpoint Data Centers set to **same DC as volume only**
- [ ] GPU type selected (24GB for most diffusion models)
- [ ] Idle timeout set (60s+ for dev, 5s for prod cost savings)
- [ ] FlashBoot enabled (free, reduces cold starts)
- [ ] Execution timeout set (600s for first run with model download)
- [ ] R2/S3 env vars set (R2_ACCOUNT_ID, R2_ACCESS_KEY, R2_SECRET_KEY, R2_BUCKET)
- [ ] Base image has gcc + build-essential
- [ ] ComfyUI pinned to specific tag
- [ ] PyTorch version compatible with ComfyUI version
- [ ] Worker init runs before `runpod.serverless.start()`

---

## Debug Commands

```bash
# Health check
curl -s https://api.runpod.ai/v2/{ENDPOINT_ID}/health \
  -H "Authorization: {API_KEY}"

# Ping (no model loading)
curl -s https://api.runpod.ai/v2/{ENDPOINT_ID}/runsync \
  -H "Authorization: {API_KEY}" \
  -H "Content-Type: application/json" \
  -d '{"input":{"op":"ping"}}'

# Purge stuck jobs
curl -s -X POST https://api.runpod.ai/v2/{ENDPOINT_ID}/purge-queue \
  -H "Authorization: {API_KEY}"

# Async generate (won't timeout)
curl -s https://api.runpod.ai/v2/{ENDPOINT_ID}/run \
  -H "Authorization: {API_KEY}" \
  -H "Content-Type: application/json" \
  -d '{"input":{"op":"generate","prompt":"...","steps":4}}'

# Check async job status
curl -s https://api.runpod.ai/v2/{ENDPOINT_ID}/status/{JOB_ID} \
  -H "Authorization: {API_KEY}"
```

---

## Live Endpoints

### KREA2-Turbo (Text-to-Image)

- **Endpoint ID**: `i3fvrhucaici89`
- **Repo**: `farhann-saleem/Krea-2-Turbo` (branch: main)
- **GPU**: 24GB, EU-RO-1
- **Models on R2**: `comfy-models/krea2-turbo/`

```bash
# Ping
curl -s https://api.runpod.ai/v2/i3fvrhucaici89/runsync \
  -H "Authorization: $RUNPOD_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"input":{"op":"ping"}}'

# Generate (sync — may timeout on cold start, use /run for async)
curl -s https://api.runpod.ai/v2/i3fvrhucaici89/runsync \
  -H "Authorization: $RUNPOD_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"input":{"op":"generate","prompt":"a cute ginger cat on a chair","steps":4}}'

# Generate (async — won't timeout)
curl -s https://api.runpod.ai/v2/i3fvrhucaici89/run \
  -H "Authorization: $RUNPOD_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"input":{"op":"generate","prompt":"a cute ginger cat on a chair","steps":4}}'
# Then poll: GET /v2/i3fvrhucaici89/status/{JOB_ID}
```

**Generate params:**
| Param | Default | Notes |
|-------|---------|-------|
| prompt | required | Text description |
| width | 1280 | Image width |
| height | 720 | Image height |
| steps | 4 | Inference steps (4 = turbo) |
| seed | random | Reproducibility |

**Performance (warm worker):**
| Metric | Value |
|--------|-------|
| Cold start (first request) | ~90s (Triton JIT compilation) |
| Warm request | **~10s** |
| Cost per warm image | ~$0.002 |

---

### Qwen-Image-Edit-2511 (Image-to-Image)

- **Endpoint ID**: `ko6zewns6wj3mj`
- **Repo**: `farhann-saleem/Qwen-and-QwenEdit` (branch: main)
- **GPU**: 24GB / 32GB Pro, EU-RO-1
- **Models on R2**: `comfy-models/qwen-image-edit-2511/`
- **Status**: Working (verified 2026-09-12)

```bash
# Ping
curl -s https://api.runpod.ai/v2/ko6zewns6wj3mj/runsync \
  -H "Authorization: $RUNPOD_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"input":{"op":"ping"}}'

# Generate (Python — image too large for curl CLI arg)
python3 << 'EOF'
import base64, json, requests

with open("input_image.png", "rb") as f:
    img_b64 = base64.b64encode(f.read()).decode()

r = requests.post(
    "https://api.runpod.ai/v2/ko6zewns6wj3mj/run",
    headers={"Authorization": "YOUR_RUNPOD_API_KEY"},
    json={"input": {
        "op": "generate",
        "prompt": "Make the cat wear a tiny top hat",
        "image_b64": img_b64,
        "steps": 20,
        "cfg": 4.0
    }},
    timeout=30
)
print(r.json())  # {"id": "...", "status": "IN_QUEUE"}
# Poll: GET /v2/ko6zewns6wj3mj/status/{JOB_ID}
EOF
```

**Generate params:**
| Param | Default | Notes |
|-------|---------|-------|
| prompt | required | Editing instruction (natural language) |
| image_b64 | required | Base64 PNG of source image |
| width | 1024 | Output width |
| height | 1024 | Output height |
| steps | 20 | Inference steps |
| cfg | 4.0 | Classifier-free guidance |
| seed | random | Reproducibility |

**Performance (measured 2026-09-12):**
| Metric | Value |
|--------|-------|
| Cold start (VRAM load + Triton JIT) | ~150s |
| Warm request (20 steps, 1024x1024) | ~10-30s est |
| Cost per cold edit | ~$0.03 |
| Cost per warm edit | ~$0.01 est |

**ComfyUI workflow nodes** (critical — don't substitute):
- `CLIPLoader` type: `"qwen_image"` (NOT `qwen2_5vl`)
- `TextEncodeQwenImageEditPlus` (NOT standard `CLIPTextEncode`)
- `ModelSamplingAuraFlow` shift=8.0 + `CFGNorm` strength=1.0
- `ImageScaleToTotalPixels` resolution_steps=64

---

## R2 Bucket Contents (bucket: comfy)

```
comfy-models/krea2-turbo/
  diffusion_models/krea2_turbo_fp8_scaled.safetensors     (13.14 GB)
  text_encoders/qwen3vl_4b_fp8_scaled.safetensors         (5.24 GB)
  vae/qwen_image_vae.safetensors                          (0.25 GB)

comfy-models/qwen-image-edit-2511/
  diffusion_models/qwen_image_edit_2511_fp8mixed.safetensors (20.53 GB)
  text_encoders/qwen_2.5_vl_7b_fp8_scaled.safetensors       (9.38 GB)
  vae/qwen_image_vae.safetensors                             (0.25 GB)

comfy-models/qwen-image-2512/
  diffusion_models/qwen_image_2512_fp8_e4m3fn.safetensors  (20.43 GB)
  text_encoders/qwen_2.5_vl_7b_fp8_scaled.safetensors      (9.38 GB)
  vae/qwen_image_vae.safetensors                            (0.25 GB)
```

---

## R2 Credentials

Stored in: `/home/farhann-saleem/Desktop/github/projects/hiigsfiled/marketing-studio-ie/.env`

Env vars for RunPod endpoints:
- `R2_ACCOUNT_ID` = adf3901c58de53d7b8001b6a93861096
- `R2_ENDPOINT` = https://adf3901c58de53d7b8001b6a93861096.r2.cloudflarestorage.com
- `R2_ACCESS_KEY` / `R2_SECRET_KEY` — in .env file above
- `R2_BUCKET` = comfy

---

## Cost Reference

| Event | Cost |
|-------|------|
| Worker idle (no FlashBoot) | GPU rate per second |
| Worker paused (FlashBoot) | Free |
| Cold start | ~2-3 min of GPU time |
| KREA warm generate (4 steps, 1280x720) | ~$0.002 |
| Qwen cold edit (20 steps, 1024x1024) | ~$0.03 (measured) |
| Qwen warm edit (20 steps, 1024x1024) | ~$0.01 est |
| Network volume (50GB) | $3.50/month |
