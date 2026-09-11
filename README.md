# ms-runpod-qwen

Marketing Studio — **Qwen Image Edit 2511 image-to-image** on RunPod serverless GPU.

Ping skeleton. No weights in git. Public GitHub is fine.

**Job:** image + prompt → image (I2I). R2 later: `comfy/comfy-models/qwen-image-edit-2511/` (~30 GB). Plain **Qwen Image 2512** is T2I — that is Krea’s job here, not this worker.

## You do

1. Create a GitHub repo (public is OK). Empty.
2. From this folder:

```bash
git init
git add .
git commit -m "ping skeleton for RunPod Qwen I2I"
git branch -M main
git remote add origin git@github.com:<you>/ms-runpod-qwen.git
git push -u origin main
```

3. RunPod → connect GitHub → New Endpoint → this repo. Dockerfile: `Dockerfile`.
4. Cheap settings: GPU smallest **24GB**, min workers **0**, max **1**, idle **5s**, timeout 600s.
5. Paste secrets on the **endpoint** (Environment), not in git: copy `.env.example` keys. Keep `ALLOW_GENERATE=0`.
6. Test only `{"input":{"op":"ping"}}`. Then let it scale to zero.
7. Later updates: GitHub **Release**.

Do not `COPY .` in the Dockerfile. Do not put `HF_TOKEN` in the image.
