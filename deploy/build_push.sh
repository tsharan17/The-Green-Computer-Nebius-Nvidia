#!/usr/bin/env bash
# Build the image and push it to a registry Nebius can pull from.
#
#   IMAGE=ghcr.io/<you>/green-computer:0.6.0 deploy/build_push.sh
#
# STATUS: never run (no Docker on the development machine). Log in first (`docker login ...`).
# Use a PUBLIC image, or pass registry credentials to the endpoint (see deploy/endpoint.md). The
# Nebius docs say images from a Nebius Container Registry in the same project need no credentials,
# but do not describe how to push to one; follow the Nebius registry docs if you use it.
set -euo pipefail
: "${IMAGE:?set IMAGE, for example IMAGE=ghcr.io/you/green-computer:0.6.0}"
cd "$(dirname "$0")/.."
docker build -t "$IMAGE" .
docker push "$IMAGE"
echo "pushed $IMAGE"
