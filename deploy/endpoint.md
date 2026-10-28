# Deploying the public demo on Nebius

Nothing here has been run: there was no Nebius account, CLI or Docker on the machine this project was developed on. The flags are taken from the Nebius CLI documentation (`nebius ai endpoint create`). Verify each with `--dry-run` before creating anything.

The demo is **one container**: FastAPI plus the built web UI on port 8000 (see the `Dockerfile`). It needs no GPU.

## 1. Build and push the image

```bash
IMAGE=ghcr.io/<you>/green-computer:0.6.0 deploy/build_push.sh
```

Make the image public, or give the endpoint registry credentials (`--registry-secret`). The Nebius docs say images from a Nebius Container Registry in the same project need no credentials but do not explain how to push to one.

## 2. Keep the keys out of the image

Create MysteryBox secrets for `TOKEN_FACTORY_API_KEY` (and `TAVILY_API_KEY` if you want it) and pass their selectors to `--env-secret`. Do not put keys in `--env`: they would appear in the endpoint's configuration.

## 3. Create the endpoint

```bash
IMAGE=... PLATFORM=<cpu-platform> PRESET=<preset> SUBNET_ID=<id> \
TOKEN_FACTORY_SECRET=<selector> deploy/endpoint_create.sh          # dry run first
DRY_RUN=0 IMAGE=... deploy/endpoint_create.sh                      # then create
```

- `--auth none` makes the URL public, as the competition requires ("free and unrestricted for judges").
- `GC_PUBLIC_DEMO=1` turns off the actions that spend money (GPU jobs, datasheet search), rate limits the Green Agent and the analysis, caps uploads and gives each visitor their own project. `GC_TRUST_PROXY=1` makes the limits use the visitor's address from `X-Forwarded-For` (set it only behind Nebius's proxy).
- **Choose a CPU platform.** The CLI docs list only GPU platforms (`gpu-h100-sxm`, `gpu-l40s-d`, ...) and the default is a GPU one, which is billed while the endpoint exists. The web console lets you create a VM without GPUs.
- Creation takes about five minutes. The public URL appears in `status.public_endpoints`.

## 4. Check it as a judge would

From a clean browser (no login): open the URL, click an example, press **Analyse**, open **Export**. Then, with the agent key set, ask the Green Agent a question. `curl <url>/api/v1/health` should report `"demo_mode": true`.

## 5. Keep it alive until judging ends, then stop paying

The rules require the demo to stay up free for judges until 15 Dec. Token Factory credits expire 90 days after issue, so claim them no earlier than needed. Afterwards: `nebius ai endpoint stop --id <ID>` (keeps it) or `nebius ai endpoint delete --id <ID>` (removes the VM and boot disk).

## Known limits of this deployment

- State lives in SQLite inside the container: restarts reset everyone's projects (the example project and the committed measurements are re-seeded).
- Uploaded models are visible to all visitors of the demo (names only); uploads are capped at 10 MB and 50 items.
- One worker process (rate limits and SQLite are per process).
- The Nebius GPU benchmark launcher is disabled in the public demo; the committed CPU measurements remain visible on the Real measurements page.
