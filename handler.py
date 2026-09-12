"""Qwen-Image-Edit-2511 I2I on RunPod serverless (ComfyUI + R2 weights).

Image-to-image editing: input image + instruction prompt → edited image.
Models ~30GB total on R2, cached on network volume.

R2 layout (bucket comfy):
  comfy-models/qwen-image-edit-2511/diffusion_models/qwen_image_edit_2511_fp8mixed.safetensors
  comfy-models/qwen-image-edit-2511/text_encoders/qwen_2.5_vl_7b_fp8_scaled.safetensors
  comfy-models/qwen-image-edit-2511/vae/qwen_image_vae.safetensors
"""
from __future__ import annotations

import sys
print(">>> handler.py starting", flush=True)
print(f">>> Python {sys.version}", flush=True)

import base64
import json
import os
import shutil
import subprocess
import time
import uuid
from io import BytesIO
from pathlib import Path

try:
    import boto3
    print(">>> boto3 ok", flush=True)
except Exception as e:
    print(f">>> boto3 FAILED: {e}", flush=True)
    sys.exit(1)

try:
    import requests
    print(">>> requests ok", flush=True)
except Exception as e:
    print(f">>> requests FAILED: {e}", flush=True)
    sys.exit(1)

try:
    import runpod
    print(f">>> runpod ok (version={getattr(runpod, '__version__', '?')})", flush=True)
except Exception as e:
    print(f">>> runpod FAILED: {e}", flush=True)
    sys.exit(1)

from botocore.config import Config

WORKER = "qwen"
COMFY_DIR = Path(os.environ.get("COMFY_DIR", "/workspace/ComfyUI"))
COMFY_URL = "http://127.0.0.1:8188"
UNET = "qwen_image_edit_2511_fp8mixed.safetensors"
CLIP = "qwen_2.5_vl_7b_fp8_scaled.safetensors"
VAE = "qwen_image_vae.safetensors"
R2_PREFIX = "comfy-models/qwen-image-edit-2511"
USD_PER_HOUR = float(os.environ.get("RUNPOD_GPU_USD_PER_HR", "0.69"))

_comfy_proc: subprocess.Popen | None = None


def _env(*names: str, default: str = "") -> str:
    for name in names:
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return default


def _volume_root() -> Path:
    vol = Path("/runpod-volume")
    if vol.is_dir() and os.path.ismount(str(vol)):
        return vol / "qwen-image-edit-2511"
    return COMFY_DIR / "models"


def _assert_disk(path: Path, need_gb: float = 35) -> None:
    path.mkdir(parents=True, exist_ok=True)
    free = shutil.disk_usage(path).free
    if free < need_gb * 1e9:
        mounted = os.path.ismount("/runpod-volume")
        raise RuntimeError(
            f"Need {need_gb:.0f}GB free, have {free / 1e9:.1f}GB at {path}. "
            f"/runpod-volume mounted={mounted}. "
            "Attach a network volume (same DC), then STOP old workers."
        )


def _r2():
    account = _env("R2_ACCOUNT_ID")
    endpoint = _env("R2_ENDPOINT") or (f"https://{account}.r2.cloudflarestorage.com" if account else "")
    access = _env("R2_ACCESS_KEY")
    secret = _env("R2_SECRET_KEY")
    bucket = _env("R2_BUCKET_NAME", "R2_BUCKET", default="comfy")
    if not (endpoint and access and secret and bucket):
        raise RuntimeError("Missing R2 env (R2_ACCOUNT_ID, R2_ACCESS_KEY, R2_SECRET_KEY, R2_BUCKET_NAME).")
    client = boto3.client(
        "s3",
        aws_access_key_id=access,
        aws_secret_access_key=secret,
        endpoint_url=endpoint,
        region_name="auto",
        config=Config(signature_version="s3v4"),
    )
    return bucket, client


def _download(key: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    _assert_disk(dest.parent)
    if dest.exists() and dest.stat().st_size > 1_000_000:
        print(f"cached {dest.name} ({dest.stat().st_size} bytes)", flush=True)
        return
    bucket, client = _r2()
    print(f"downloading s3://{bucket}/{key} -> {dest}", flush=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    client.download_file(bucket, key, str(tmp))
    tmp.replace(dest)
    print(f"ok {dest.name} ({dest.stat().st_size} bytes)", flush=True)


def ensure_weights() -> dict[str, Path]:
    root = _volume_root()
    files = {
        "unet": (f"{R2_PREFIX}/diffusion_models/{UNET}", root / "diffusion_models" / UNET),
        "clip": (f"{R2_PREFIX}/text_encoders/{CLIP}", root / "text_encoders" / CLIP),
        "vae": (f"{R2_PREFIX}/vae/{VAE}", root / "vae" / VAE),
    }
    t0 = time.time()
    for _kind, (key, dest) in files.items():
        _download(key, dest)
    for sub, dest in (
        ("diffusion_models", files["unet"][1]),
        ("text_encoders", files["clip"][1]),
        ("vae", files["vae"][1]),
    ):
        target_dir = COMFY_DIR / "models" / sub
        target_dir.mkdir(parents=True, exist_ok=True)
        link = target_dir / dest.name
        if dest.resolve() != link.resolve():
            if link.exists() or link.is_symlink():
                link.unlink()
            try:
                link.symlink_to(dest)
            except OSError:
                shutil.copy2(dest, link)
    return {"download_ms": int((time.time() - t0) * 1000)}


def _comfy_up() -> bool:
    try:
        r = requests.get(f"{COMFY_URL}/system_stats", timeout=3)
        return r.status_code == 200
    except requests.RequestException:
        return False


def ensure_comfy() -> None:
    global _comfy_proc
    if _comfy_up():
        return
    log = Path("/tmp/comfy.log")
    log.write_text("")
    _comfy_proc = subprocess.Popen(
        [
            sys.executable,
            "main.py",
            "--listen",
            "127.0.0.1",
            "--port",
            "8188",
            "--highvram",
            "--disable-auto-launch",
        ],
        cwd=str(COMFY_DIR),
        stdout=open(log, "ab"),
        stderr=subprocess.STDOUT,
    )
    deadline = time.time() + 180
    while time.time() < deadline:
        if _comfy_up():
            print("ComfyUI ready", flush=True)
            return
        if _comfy_proc.poll() is not None:
            raise RuntimeError(f"ComfyUI exited: {log.read_text()[-2000:]}")
        time.sleep(2)
    raise RuntimeError(f"ComfyUI did not start: {log.read_text()[-2000:]}")


def _save_input_image(image_b64: str) -> str:
    """Save base64 image to ComfyUI input dir, return filename."""
    raw = base64.b64decode(image_b64)
    fname = f"input_{uuid.uuid4().hex[:8]}.png"
    input_dir = COMFY_DIR / "input"
    input_dir.mkdir(parents=True, exist_ok=True)
    (input_dir / fname).write_bytes(raw)
    return fname


def build_workflow(prompt: str, input_image: str, width: int, height: int,
                   seed: int, steps: int, cfg: float) -> dict:
    """Build ComfyUI API workflow for Qwen Image Edit 2511.

    Matches official workflow: UNETLoader → ModelSamplingAuraFlow → CFGNorm → KSampler,
    CLIPLoader(qwen_image) → TextEncodeQwenImageEditPlus for edit-aware conditioning.
    """
    return {
        "1": {
            "class_type": "UNETLoader",
            "inputs": {"unet_name": UNET, "weight_dtype": "fp8_e4m3fn"},
        },
        "2": {
            "class_type": "CLIPLoader",
            "inputs": {"clip_name": CLIP, "type": "qwen_image"},
        },
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": VAE}},
        "4": {
            "class_type": "LoadImage",
            "inputs": {"image": input_image},
        },
        # Scale input image to target resolution
        "5": {
            "class_type": "ImageScaleToTotalPixels",
            "inputs": {"image": ["4", 0], "upscale_method": "bicubic", "megapixels": round(width * height / 1e6, 2)},
        },
        # Encode scaled image to latent space
        "6": {
            "class_type": "VAEEncode",
            "inputs": {"vae": ["3", 0], "pixels": ["5", 0]},
        },
        # Edit-aware text encoding (positive) — takes CLIP + VAE + source image + prompt
        "7": {
            "class_type": "TextEncodeQwenImageEditPlus",
            "inputs": {"clip": ["2", 0], "vae": ["3", 0], "image1": ["5", 0], "text": prompt},
        },
        # Edit-aware text encoding (negative)
        "8": {
            "class_type": "TextEncodeQwenImageEditPlus",
            "inputs": {"clip": ["2", 0], "vae": ["3", 0], "image1": ["5", 0], "text": ""},
        },
        # Model sampling config for Qwen diffusion
        "11": {
            "class_type": "ModelSamplingAuraFlow",
            "inputs": {"model": ["1", 0], "shift": 8.0},
        },
        # CFG normalization
        "12": {
            "class_type": "CFGNorm",
            "inputs": {"model": ["11", 0]},
        },
        # Sampler
        "9": {
            "class_type": "KSampler",
            "inputs": {
                "model": ["12", 0],
                "positive": ["7", 0],
                "negative": ["8", 0],
                "latent_image": ["6", 0],
                "seed": seed,
                "steps": steps,
                "cfg": cfg,
                "sampler_name": "euler",
                "scheduler": "simple",
                "denoise": 1.0,
            },
        },
        "10": {"class_type": "VAEDecode", "inputs": {"vae": ["3", 0], "samples": ["9", 0]}},
        "13": {
            "class_type": "SaveImage",
            "inputs": {"images": ["10", 0], "filename_prefix": "ms_qwen_edit"},
        },
    }


def _queue(workflow: dict, timeout_s: int = 300) -> bytes:
    resp = requests.post(f"{COMFY_URL}/prompt", json={"prompt": workflow}, timeout=60)
    if resp.status_code != 200:
        raise RuntimeError(f"Comfy queue {resp.status_code}: {resp.text[:1500]}")
    data = resp.json()
    if data.get("node_errors"):
        raise RuntimeError(f"Comfy node_errors: {json.dumps(data['node_errors'])[:2000]}")
    prompt_id = data.get("prompt_id")
    if not prompt_id:
        raise RuntimeError(f"no prompt_id: {data}")
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        hist = requests.get(f"{COMFY_URL}/history/{prompt_id}", timeout=15).json()
        if prompt_id in hist:
            outputs = hist[prompt_id].get("outputs") or {}
            for node in outputs.values():
                for img in node.get("images") or []:
                    params = {
                        "filename": img["filename"],
                        "subfolder": img.get("subfolder", ""),
                        "type": img.get("type", "output"),
                    }
                    raw = requests.get(f"{COMFY_URL}/view", params=params, timeout=60)
                    raw.raise_for_status()
                    return raw.content
        time.sleep(1)
    raise TimeoutError(f"Comfy timed out after {timeout_s}s for {prompt_id}")


def generate(inp: dict) -> dict:
    prompt = (inp.get("prompt") or "").strip()
    if not prompt:
        raise ValueError("prompt (editing instruction) is required")
    image_b64 = inp.get("image_b64") or inp.get("image") or ""
    if not image_b64:
        raise ValueError("image_b64 (base64 PNG of source image) is required")
    width = int(inp.get("width") or 1024)
    height = int(inp.get("height") or 1024)
    steps = int(inp.get("steps") or 20)
    cfg = float(inp.get("cfg") or 4.0)
    seed = int(inp.get("seed") or int(time.time()) % 2_147_483_647)

    t0 = time.time()
    dl = ensure_weights()
    ensure_comfy()
    input_fname = _save_input_image(image_b64)
    png = _queue(build_workflow(prompt, input_fname, width, height, seed, steps, cfg))
    duration_ms = int((time.time() - t0) * 1000)
    estimated_usd = round((duration_ms / 1000 / 3600) * USD_PER_HOUR, 6)
    return {
        "ok": True,
        "worker": WORKER,
        "width": width,
        "height": height,
        "steps": steps,
        "cfg": cfg,
        "seed": seed,
        "duration_ms": duration_ms,
        "download_ms": dl["download_ms"],
        "estimated_usd": estimated_usd,
        "usd_per_hour_assumed": USD_PER_HOUR,
        "png_b64": base64.b64encode(png).decode(),
    }


def handler(job):
    inp = job.get("input") or {}
    prompt = (inp.get("prompt") or "").strip()
    image = inp.get("image_b64") or inp.get("image") or ""
    op = str(inp.get("op") or ("generate" if prompt and image else "ping")).lower()
    if op == "ping":
        vol = Path("/runpod-volume")
        root = _volume_root()
        try:
            free_gb = round(shutil.disk_usage(root if root.exists() else Path("/")).free / 1e9, 2)
        except OSError:
            free_gb = None
        return {
            "ok": True,
            "worker": WORKER,
            "comfy_up": _comfy_up(),
            "volume_mounted": os.path.ismount(str(vol)),
            "weight_root": str(root),
            "free_gb": free_gb,
        }
    if op in {"generate", "edit", "sample"}:
        try:
            return generate(inp)
        except Exception as exc:
            return {"ok": False, "worker": WORKER, "error": f"{type(exc).__name__}: {exc}"}
    return {"error": f"unknown op {op}. Use ping or generate."}


# --- Worker init: download weights + start ComfyUI BEFORE accepting jobs (Lesson #6) ---
print(">>> worker init: downloading weights", flush=True)
try:
    ensure_weights()
    print(">>> weights ready, starting ComfyUI", flush=True)
    ensure_comfy()
    print(">>> ComfyUI ready, accepting jobs", flush=True)
except Exception as e:
    print(f">>> init warning (will retry on first job): {e}", flush=True)

runpod.serverless.start({"handler": handler})
