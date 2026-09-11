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

## Issue 7: Disk Space on Container Without Volume

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
  -H "Authorization: Bearer {API_KEY}"

# Ping (no model loading)
curl -s https://api.runpod.ai/v2/{ENDPOINT_ID}/runsync \
  -H "Authorization: Bearer {API_KEY}" \
  -H "Content-Type: application/json" \
  -d '{"input":{"op":"ping"}}'

# Purge stuck jobs
curl -s -X POST https://api.runpod.ai/v2/{ENDPOINT_ID}/purge-queue \
  -H "Authorization: Bearer {API_KEY}"

# Async generate (won't timeout)
curl -s https://api.runpod.ai/v2/{ENDPOINT_ID}/run \
  -H "Authorization: Bearer {API_KEY}" \
  -H "Content-Type: application/json" \
  -d '{"input":{"op":"generate","prompt":"...","steps":4}}'

# Check async job status
curl -s https://api.runpod.ai/v2/{ENDPOINT_ID}/status/{JOB_ID} \
  -H "Authorization: Bearer {API_KEY}"
```

---

## Cost Reference

| Event | Cost |
|-------|------|
| Worker idle (no FlashBoot) | GPU rate per second |
| Worker paused (FlashBoot) | Free |
| Cold start | ~2-3 min of GPU time |
| Warm generate (4 steps, 1280x720) | ~$0.02 |
| Network volume (50GB) | $3.50/month |
