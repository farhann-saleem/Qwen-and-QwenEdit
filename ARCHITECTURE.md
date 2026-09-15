# Architecture — Qwen Image Edit

## Role

Image-to-image / edit leg. Avatar provider id `qwen` and Images desk I2I.

## Boot sequence

Same pattern as Krea: mount check → R2 weight ensure → symlink → Comfy up → `serverless.start()`. Cache root `/runpod-volume/qwen-image-edit-2511` when mounted.

## Comfy graph

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

`width × height` → megapixel target for `ImageScaleToTotalPixels` (aspect preserved).

## Performance

| Metric | Value |
| --- | --- |
| Cold (VRAM + Triton JIT) | ~150s |
| Warm 20-step 1024² | 10–30s |
| Warm cost | ~$0.03 |
| Internal Comfy timeout | 300s / prompt |

## I/O

- Input: `prompt` + `image_b64` (alias `image`)
- Output: inline `png_b64`
- Ops: `generate` / `edit` / `sample` aliases; default `ping` unless both prompt and image present

## Security

Dockerfile copies handler only. No weights in image. Runtime R2 creds on endpoint.
