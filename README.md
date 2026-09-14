# Qwen Image Edit 2511 — image-to-image GPU worker

RunPod serverless worker that rewrites an image from a natural-language instruction. ComfyUI + Qwen Image Edit
2511 fp8mixed, weights streamed from Cloudflare R2 onto a network volume.

One of four repositories in **Marketing Studio**, the 8x hiring assignment. This worker is the **I2I** leg —
the most exercised GPU worker in the system.

| | |
| --- | --- |
| **Role** | `source image + editing instruction → edited image` |
| **Runtime** | RunPod serverless GPU, ComfyUI v0.35.1, PyTorch 2.7.0, CUDA 12.8 |
| **Model** | Qwen Image Edit 2511, fp8mixed, 20 steps, cfg 4.0, AuraFlow shift 8.0 |
| **Endpoint** | `ko6zewns6wj3mj` — **EU-RO-1 only**, 24 GB / 32 GB Pro |
| **Weights** | ~30 GB on R2 under `comfy-models/qwen-image-edit-2511/`. Never in git, never in the image. |
| **Cost** | ~$0.03 per warm edit at the assumed $0.69/GPU-hour |
| **Status** | Working. Endpoint live and generating. Proven on a real edit in ~150s cold / ~$0.03. |

> Plain **Qwen Image 2512** is text-to-image — that is the [Krea worker's](https://github.com/farhann-saleem/Krea-2-Turbo)
> job, not this one. This repo only does edit-aware I2I.

---

## Where this fits

```
Marketing Studio backend
   │
   ├── text → image  ──────────►  Krea-2-Turbo      (RunPod GPU)
   ├── image → image ──────────►  THIS WORKER       (RunPod GPU)
   ├── face swap / stitch ─────►  Faceswap-and-FF   (RunPod CPU)
   └── image → video  ─────────►  Modal LTX-2.5     (H200)
                                        │
                                  Cloudflare R2
                              weights + product media
```

| Sibling | Repo |
| --- | --- |
| Application tier | [`farhann-saleem/sixeyes`](https://github.com/farhann-saleem/sixeyes) |
| Text → image | [`farhann-saleem/Krea-2-Turbo`](https://github.com/farhann-saleem/Krea-2-Turbo) |
| CPU swap + stitch | [`farhann-saleem/Faceswap-and-FF`](https://github.com/farhann-saleem/Faceswap-and-FF) |

In the product this worker backs the **image-to-image** desk and is one of the selectable avatar providers
(`qwen`). The app's default avatar path is OpenRouter FLUX.2 Klein 4B; Qwen is the self-hosted alternative.

---

## API

Two ops. `op` defaults to `generate` when both a `prompt` and an image are present, otherwise `ping`.
`generate`, `edit` and `sample` are accepted as aliases.

### `ping` — readiness, no model work

```bash
curl -s https://api.runpod.ai/v2/ko6zewns6wj3mj/runsync \
  -H "Authorization: Bearer $RUNPOD_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"input":{"op":"ping"}}'
```

```json
{
  "ok": true,
  "worker": "qwen",
  "comfy_up": true,
  "volume_mounted": true,
  "weight_root": "/runpod-volume/qwen-image-edit-2511",
  "free_gb": 506540.0
}
```

Read the fields, not just `ok`. `ok` only means the handler answered.

- `volume_mounted: false` → no network volume. Weight downloads will hit `Errno 28` on the ~5 GB container disk.
- `free_gb` must be comfortably above **35 GB**.
- `comfy_up: false` shortly after boot is normal; persistent `false` means ComfyUI died — check the logs.

**Always `GET /health` first.** It reports queue and worker counts *without starting a GPU*, and it is the only
way to see `throttled` before you spend money.

### `generate` — edit an image

Use async `/run` and poll `/status/{id}`. `/runsync` will time out on a cold start. Image payloads are base64,
so this is a Python (or any HTTP client) call rather than a hand-typed curl.

```python
import base64, os, time, requests

ENDPOINT = "ko6zewns6wj3mj"
HEADERS = {"Authorization": f"Bearer {os.environ['RUNPOD_API_KEY']}"}

with open("input_image.png", "rb") as f:
    img_b64 = base64.b64encode(f.read()).decode()

job = requests.post(
    f"https://api.runpod.ai/v2/{ENDPOINT}/run",
    headers=HEADERS,
    json={"input": {
        "op": "generate",
        "prompt": "Make the cat wear a tiny top hat, steampunk style",
        "image_b64": img_b64,
        "steps": 20,
        "cfg": 4.0,
    }},
    timeout=30,
).json()["id"]

while True:
    time.sleep(10)
    s = requests.get(f"https://api.runpod.ai/v2/{ENDPOINT}/status/{job}", headers=HEADERS).json()
    if s["status"] == "COMPLETED":
        out = s["output"]
        open("output.png", "wb").write(base64.b64decode(out["png_b64"]))
        print(f"done in {out['duration_ms']}ms, ${out['estimated_usd']}")
        break
    if s["status"] == "FAILED":
        print("failed:", s.get("error"))
        break
    print("status:", s["status"])
```

Never hardcode the API key. Read it from the environment.

#### Parameters

| Param | Type | Default | Notes |
| --- | --- | --- | --- |
| `prompt` | string | **required** | the editing instruction, in natural language |
| `image_b64` | string | **required** | base64 PNG of the source image. `image` is accepted as an alias. |
| `width` | int | `1024` | with `height`, sets the megapixel target the source is scaled to |
| `height` | int | `1024` | |
| `steps` | int | `20` | |
| `cfg` | float | `4.0` | classifier-free guidance |
| `seed` | int | time-derived | pass a value for reproducibility |

`width × height` is converted to megapixels for `ImageScaleToTotalPixels`, so these control output *scale*
rather than forcing an exact crop — the source aspect ratio is preserved.

#### Response

| Field | Meaning |
| --- | --- |
| `ok` | `true` on success; on failure `false` plus `error` as `TypeName: message` |
| `png_b64` | base64 PNG of the edited image, returned inline (not written to R2) |
| `duration_ms` | wall time inside the handler, including any weight download |
| `download_ms` | time spent fetching weights (`0` once the volume is warm) |
| `estimated_usd` | `duration_ms × usd_per_hour_assumed`; an estimate, not a RunPod invoice |
| `usd_per_hour_assumed` | from `RUNPOD_GPU_USD_PER_HR`, default `0.69` |
| `width` `height` `steps` `cfg` `seed` | echoed back for the caller's ledger |

Model-level failures come back as `200` with `ok: false`, so a polling client never has to distinguish a
RunPod error from a Comfy error.

---

## Performance

| Metric | Value |
| --- | --- |
| Cold start (VRAM load + Triton JIT) | ~150s |
| Warm request (20 steps, 1024²) | 10–30s |
| Cost per warm edit | ~$0.03 |
| Internal Comfy timeout | 300s per prompt |
| Model total on R2 | ~30 GB |

The first ever smoke test on this endpoint **failed** at 327s with `Comfy timed out after 300s`, costing
~$0.063 — because `ping` was skipped and a cold generate tried to do ComfyUI init *and* 20 sampling steps
inside the handler's 300s queue window. The sequence that works:

```
GET /health            → stop if throttled > 0
POST /run {op: ping}   → wakes the worker, confirms volume_mounted / comfy_up / free_gb
POST /run {op: generate}
```

---

## The ComfyUI graph

Built in `build_workflow()` to match the official `Qwen_Image_Edit_2511` workflow.

```
LoadImage ──► ImageScaleToTotalPixels ──► VAEEncode ──────────────────────► KSampler ──► VAEDecode ──► SaveImage
                        │                                                      ▲
                        ├──────────────► TextEncodeQwenImageEditPlus(pos) ──────┤
                        └──────────────► TextEncodeQwenImageEditPlus(neg) ──────┤
CLIPLoader(type="qwen_image") ──────────────────┘                               │
UNETLoader(fp8_e4m3fn) ──► ModelSamplingAuraFlow(shift=8.0) ──► CFGNorm(1.0) ───┘
VAELoader ──────────────────────────────────────────────────────────────────────┘

KSampler: steps=20, cfg=4.0, sampler=euler, scheduler=simple, denoise=1.0
```

Every node choice here was paid for in failed jobs:

| Node | The trap |
| --- | --- |
| `CLIPLoader` | `type` is a strict enum and Qwen is **`qwen_image`**, *not* `qwen2_5vl`. The plausible-looking guess returns `value_not_in_list`. |
| `TextEncodeQwenImageEditPlus` | Edit 2511 needs this edit-aware encoder (CLIP + VAE + source image + prompt). A generic `CLIPTextEncode` produces garbage edits. Its input is named **`prompt`**, not `text`. |
| `ModelSamplingAuraFlow` | `shift: 8.0`. |
| `CFGNorm` | input is named **`strength`**, not `scale`. |
| `ImageScaleToTotalPixels` | needs **`resolution_steps: 64`**; omitting it raises `required_input_missing`. |

API input names are not the UI widget labels. Read `/object_info/{node_type}` or an official workflow JSON —
do not hand-write a graph from memory.

---

## Weights on R2

Bucket `comfy`, prefix `comfy-models/qwen-image-edit-2511/`:

```
comfy-models/qwen-image-edit-2511/
  diffusion_models/qwen_image_edit_2511_fp8mixed.safetensors   20.53 GB
  text_encoders/qwen_2.5_vl_7b_fp8_scaled.safetensors           9.38 GB
  vae/qwen_image_vae.safetensors                                0.25 GB
```

Lifecycle on boot:

1. If `/runpod-volume` is a **real mount** (`os.path.ismount`), the cache root is
   `/runpod-volume/qwen-image-edit-2511`. Otherwise it falls back to `ComfyUI/models` on the ~5 GB container
   disk, which is also wiped on scale-to-zero.
2. Each file is checked for a cached copy, downloaded to a `.part` file, then atomically renamed.
3. Files are symlinked into `ComfyUI/models/{diffusion_models,text_encoders,vae}`.
4. ComfyUI starts with `--highvram --disable-auto-launch`.
5. **Only then** does `runpod.serverless.start()` run — so cold jobs do not pay init inside their own timeout.

Never re-download from Hugging Face; the weights are staged on R2. Never `COPY` a `.safetensors` into the
image, and never bake `HF_TOKEN` into a layer.

---

## Build and test locally

```bash
docker build -t ms-runpod-qwen:local .
```

The build clones ComfyUI at the pinned tag `v0.35.1`, strips torch/torchvision/torchaudio from Comfy's
requirements (the base image already carries matching versions), then installs `runpod`, `boto3`, `requests`.

A local container has no GPU and no volume, so only imports and `ping` are meaningful:

```bash
docker run --rm ms-runpod-qwen:local \
  python /handler.py --test_input '{"input":{"op":"ping"}}'
```

`volume_mounted: false` with a small `free_gb` locally is the correct answer — that is the signal the worker
exists to surface.

---

## Deploy to RunPod

1. Push to `main`.
2. Create a GitHub **Release** — that triggers the RunPod rebuild.
3. Purge the queue and **stop old workers.** Running and paused workers keep the old image; FlashBoot resuming
   a paused worker will not pick up a new build or a newly attached volume.
4. Confirm the **worker id changed** before trusting the fix.

### Endpoint settings

| Setting | Value | Why |
| --- | --- | --- |
| Network volume | EU-RO-1 | must hold ~30 GB of weights |
| Data centers | **EU-RO-1 only** | a volume cannot be mounted cross-datacenter, and it fails *silently* — workers sit in `Initializing` with no runtime logs |
| GPU | 24 GB, or 32 GB Pro | fp8mixed Qwen Edit |
| Min / max workers | 0 / 1 | cost control |
| Idle timeout | 5s in production, 60s+ while iterating | cold start is ~150s |
| FlashBoot | on | still recycle after any rebuild |
| Execution timeout | 600s | first pull on a cold volume is slow |

Credentials go in the endpoint's **Environment** tab: `R2_ACCOUNT_ID`, `R2_ACCESS_KEY`, `R2_SECRET_KEY`,
`R2_BUCKET`. Nothing in git.

---

## Environment variables

Names only — see [`.env.example`](.env.example). Values belong on the RunPod endpoint.

| Name | Purpose |
| --- | --- |
| `ALLOW_GENERATE` | Keep `0` for the first console test. |
| `R2_ACCOUNT_ID` | used to derive the endpoint URL when `R2_ENDPOINT` is absent |
| `R2_ENDPOINT` | `https://<account-id>.r2.cloudflarestorage.com` |
| `R2_ACCESS_KEY` / `R2_SECRET_KEY` | R2 S3-compatible credentials |
| `R2_BUCKET` | default `comfy`. `R2_BUCKET_NAME` is a higher-priority alias. |
| `RUNPOD_GPU_USD_PER_HR` | cost estimate basis, default `0.69` |
| `COMFY_DIR` | default `/workspace/ComfyUI` |
| `HF_TOKEN` | not needed at runtime — weights come from R2. Do not bake it into the image. |

---

## Debugging

```bash
# Health — does NOT start a GPU. Read this before anything else.
curl -s https://api.runpod.ai/v2/ko6zewns6wj3mj/health \
  -H "Authorization: Bearer $RUNPOD_API_KEY"

# Purge a stuck queue
curl -s -X POST https://api.runpod.ai/v2/ko6zewns6wj3mj/purge-queue \
  -H "Authorization: Bearer $RUNPOD_API_KEY"

# Poll an async job
curl -s https://api.runpod.ai/v2/ko6zewns6wj3mj/status/$JOB_ID \
  -H "Authorization: Bearer $RUNPOD_API_KEY"
```

| Symptom | Cause | Fix |
| --- | --- | --- |
| Workers stuck `Initializing`, no logs | volume is in a different datacenter than the GPU | pin endpoint data centers to the volume's DC only |
| `Errno 28` / `free_gb: 4.98` | `/runpod-volume` is an empty directory, not a mount | attach a real volume in the same DC, then **stop old workers** so a new one mounts it |
| `Failed to find C compiler` | Triton compiles kernels at runtime; the base image has no gcc on PATH | already fixed here: `gcc g++ build-essential` + `CC`/`CXX` |
| `infer_schema(...) unsupported type list[int]` | PyTorch 2.6 against ComfyUI 0.35.x | base image is pinned to PyTorch **2.7.0** — do not downgrade |
| `value_not_in_list — type: 'qwen2_5vl'` | wrong CLIP enum | it is `qwen_image` |
| `required_input_missing` | API input names differ from UI labels | `prompt` not `text`; `strength` not `scale`; include `resolution_steps` |
| Garbage or ignored edits | generic `CLIPTextEncode` instead of the edit-aware encoder | use `TextEncodeQwenImageEditPlus` |
| `Comfy timed out after 300s` on a cold job | generate ran before the worker was warm | health → ping → generate |
| `throttled: 1`/`2`, jobs queued forever | account-level exponential backoff after repeated crashes. **Survives deleting the endpoint.** | fix the crash, purge, wait 10–15 min. Never spam retries. Do not generate while `throttled > 0`. |
| New build, same error, same worker id | old workers serving the old image | idle 5s, purge, stop them, verify a new worker id |

Local notes live in [`RUNPOD_LESSONS.md`](RUNPOD_LESSONS.md). The canonical, maintained write-ups — symptom,
cause, fix, and what each failure cost — are in the application repo at
[`docs/RUNPOD.md`](https://github.com/farhann-saleem/sixeyes/blob/main/docs/RUNPOD.md). That file is
deliberately never thinned.

---

## Security

- No credentials in this repo. `.env.example` is names-only; `.gitignore` blocks `.env`, `*.pem`, `*.key`,
  `credentials.json`, `secrets.json` and `rclone.conf`.
- The Dockerfile copies **only `handler.py`**. Never `COPY .` — that is how a `.env` reaches a published layer.
- No weights in git: `*.safetensors`, `*.onnx`, `*.bin`, `*.ckpt` and `comfy-models/` are all ignored.
- R2 is read with S3v4-signed requests using credentials supplied at runtime by the endpoint.
- Decoded input images are written under ComfyUI's `input/` with a random `uuid4`-derived filename, so a
  caller cannot choose a path.
- Errors surface as `TypeName: message`, which may include an object key or a path but never a credential.

---

## Known gaps

- The Dockerfile installs `runpod>=1.7.0,<2`. The house rule is **`runpod>=1.10.1,<2`**, because 1.7.11–1.10.0
  corrupts job tracking on network-volume endpoints. Worth tightening.
- Output is inline base64 rather than an R2 key, unlike the CPU worker. Fine for one ~1 MP PNG; it would not
  scale to batches or to video.
- Decoded input images accumulate in ComfyUI's `input/` directory; nothing prunes them.
- `image_b64` is decoded without a size cap. The CPU worker has `MAX_INPUT_BYTES`; this one does not.
- `ALLOW_GENERATE` is set in the environment but this handler does not gate on it — generate is reachable
  whenever the endpoint is.
- Single reference image only (`image1`). `TextEncodeQwenImageEditPlus` supports more.
