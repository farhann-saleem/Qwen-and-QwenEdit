# Setup — Qwen Image Edit

## Prerequisites

- Docker
- RunPod API key
- R2 credentials for `comfy` (~30 GB weights)
- Network volume in **EU-RO-1** with `free_gb` comfortably above **35**

## Build locally

```bash
docker build -t ms-runpod-qwen:local .

docker run --rm ms-runpod-qwen:local \
  python /handler.py --test_input '{"input":{"op":"ping"}}'
```

Local: expect `volume_mounted: false`. Correct.

## Deploy

1. Push `main` → GitHub **Release** (RunPod rebuild).
2. Purge queue + **stop old workers**.
3. Confirm **worker id** changed.

### Endpoint settings

| Setting | Value |
| --- | --- |
| Network volume | EU-RO-1 |
| Data centers | **EU-RO-1 only** |
| GPU | 24 GB or 32 GB Pro |
| Min / max | 0 / 1 |
| Idle timeout | 5s prod · 60s+ iterating |
| Execution timeout | 600s |

## Environment (names only)

[`.env.example`](.env.example) — values on the endpoint only.

| Name | Purpose |
| --- | --- |
| `ALLOW_GENERATE` | `0` for first console tests |
| `R2_*` | same pattern as Krea |
| `RUNPOD_GPU_USD_PER_HR` | default `0.69` |
| `COMFY_DIR` | `/workspace/ComfyUI` |

## Smoke sequence

```
GET /health → stop if throttled > 0
POST ping   → wakes worker, confirms volume / comfy / free_gb
POST generate via /run + poll
```

Cold generate without ping often hits `Comfy timed out after 300s` (~$0.06 wasted).

## Generate example

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
        "prompt": "Make the cat wear a tiny top hat",
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
        open("output.png", "wb").write(base64.b64decode(s["output"]["png_b64"]))
        break
    if s["status"] == "FAILED":
        raise SystemExit(s.get("error"))
```

## Debug helpers

```bash
curl -s https://api.runpod.ai/v2/ko6zewns6wj3mj/health \
  -H "Authorization: Bearer $RUNPOD_API_KEY"

curl -s -X POST https://api.runpod.ai/v2/ko6zewns6wj3mj/purge-queue \
  -H "Authorization: Bearer $RUNPOD_API_KEY"
```

## Troubleshooting (short)

| Symptom | Fix |
| --- | --- |
| CLIP `value_not_in_list` | type is **`qwen_image`**, not `qwen2_5vl` |
| Ignored edits | use `TextEncodeQwenImageEditPlus` |
| Cold timeout 300s | health → ping → generate |
| `throttled > 0` | fix crash, purge, wait 10–15 min — do not spam |

Full write-ups: [`docs/RUNPOD.md`](https://github.com/farhann-saleem/sixeyes/blob/main/docs/RUNPOD.md).
