# Models — Qwen Image Edit

## Primary model

| | |
| --- | --- |
| Name | Qwen Image Edit 2511 |
| Precision | fp8mixed |
| Steps | **20** |
| CFG | **4.0** |
| AuraFlow shift | **8.0** |
| CLIP type | **`qwen_image`** |
| Encoder node | **`TextEncodeQwenImageEditPlus`** |

## R2 layout

~30 GB under `comfy-models/qwen-image-edit-2511/` (UNET, CLIP, VAE — streamed to volume on first boot).

## API parameters

| Param | Default | Notes |
| --- | --- | --- |
| `prompt` | required | editing instruction |
| `image_b64` | required | source PNG base64 (`image` alias) |
| `width` / `height` | 1024 / 1024 | megapixel scale target |
| `steps` | 20 | |
| `cfg` | 4.0 | |
| `seed` | time-derived | |

## Product UI

- Avatar picker: **Qwen Image Edit** (`provider: qwen`)
- Images strip: I2I rewrite

Do not confuse with plain Qwen Image T2I — that path is Krea / OpenRouter elsewhere.
