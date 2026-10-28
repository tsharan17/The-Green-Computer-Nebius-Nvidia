#!/usr/bin/env bash
# Create the public demo as a Nebius Serverless Endpoint (documented `nebius ai endpoint create` flags).
#
#   IMAGE=... PLATFORM=<cpu-platform> PRESET=<preset> SUBNET_ID=<id> \
#   TOKEN_FACTORY_SECRET=<mysterybox selector> deploy/endpoint_create.sh            # dry run
#   DRY_RUN=0 ... deploy/endpoint_create.sh                                         # create it
#
# STATUS: never run. No Nebius account or CLI was available. Read deploy/endpoint.md first.
# COST: a GPU platform is billed while the endpoint exists. The app needs no GPU: choose a CPU
# platform in the console (the CLI docs name only GPU platforms), and delete the endpoint after judging.
set -euo pipefail
: "${IMAGE:?set IMAGE}"; : "${PLATFORM:?set PLATFORM (a CPU platform id from the console)}"
: "${PRESET:?set PRESET}"; : "${SUBNET_ID:?set SUBNET_ID}"
NAME="${NAME:-green-computer}"
DRY_RUN="${DRY_RUN:-1}"

args=(ai endpoint create
  --name "$NAME"
  --image "$IMAGE"
  --container-port 8000
  --platform "$PLATFORM" --preset "$PRESET" --subnet-id "$SUBNET_ID"
  --disk-size 50Gi
  --auth none
  --env GC_PUBLIC_DEMO=1
  --env GC_TRUST_PROXY=1
  --async --format json)

# Secrets come from MysteryBox, never from this file: --env-secret KEY=SECRET_SELECTOR
[ -n "${TOKEN_FACTORY_SECRET:-}" ] && args+=(--env-secret "TOKEN_FACTORY_API_KEY=${TOKEN_FACTORY_SECRET}")
[ -n "${TAVILY_SECRET:-}" ] && args+=(--env-secret "TAVILY_API_KEY=${TAVILY_SECRET}")
[ -n "${REGISTRY_SECRET:-}" ] && args+=(--registry-secret "$REGISTRY_SECRET")
[ "$DRY_RUN" = "1" ] && args+=(--dry-run)

echo "+ nebius ${args[*]}"
nebius "${args[@]}"
echo
echo "When ready (about 5 minutes), the public URL is in status.public_endpoints:"
echo "  nebius ai endpoint get <ENDPOINT_ID> --format json | jq -r '.status.public_endpoints[] | select(startswith(\"https://\"))' | head -1"
