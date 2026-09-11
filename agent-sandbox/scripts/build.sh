#!/usr/bin/env bash
# Build the sandbox image. This is the ONLY networked step in the system.
#
# Note what this script does not do: it never pipes a download into a shell.
# Fetching and executing are separate steps here and everywhere else in this
# unit. The base image is pinned by digest in docker/Dockerfile, so what you
# reviewed is what gets built, and `docker build` verifies that digest
# against the registry rather than trusting a tag that upstream can repoint.
#
# Usage:
#   scripts/build.sh                  build agent-sandbox:v1
#   scripts/build.sh --check-digest   print the digest the tag resolves to
#                                     now, so you can compare it against the
#                                     pin before deciding to move the pin
#
# Refreshing the pin is a deliberate act. --check-digest only TELLS you; it
# does not edit the Dockerfile, because a script that silently updates its
# own supply-chain pin has defeated the point of having one.

set -euo pipefail

UNIT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
IMAGE="${IMAGE:-agent-sandbox:v1}"
BASE_REPO="library/python"
BASE_TAG="3.12-slim-bookworm"

if [[ "${1:-}" == "--check-digest" ]]; then
  echo "pinned in docker/Dockerfile:"
  grep -oE 'sha256:[0-9a-f]{64}' "$UNIT_ROOT/docker/Dockerfile" | sed 's/^/  /'

  echo "currently published for ${BASE_REPO}:${BASE_TAG}:"
  token="$(curl -fsS "https://auth.docker.io/token?service=registry.docker.io&scope=repository:${BASE_REPO}:pull" \
    | python3 -c 'import sys,json; print(json.load(sys.stdin)["token"])')"
  curl -fsS -o /dev/null -D - \
    -H "Authorization: Bearer ${token}" \
    -H 'Accept: application/vnd.oci.image.index.v1+json' \
    -H 'Accept: application/vnd.docker.distribution.manifest.list.v2+json' \
    "https://registry-1.docker.io/v2/${BASE_REPO}/manifests/${BASE_TAG}" \
    | tr -d '\r' | awk 'tolower($1)=="docker-content-digest:" {print "  "$2}'

  echo
  echo "If these differ, upstream moved the tag. Read what changed, then edit"
  echo "the FROM line in docker/Dockerfile by hand."
  exit 0
fi

echo "building ${IMAGE} from ${UNIT_ROOT}/docker/Dockerfile"
docker build \
  --file "$UNIT_ROOT/docker/Dockerfile" \
  --tag "$IMAGE" \
  "$UNIT_ROOT/docker"

echo
echo "built ${IMAGE}. Now prove the containment actually holds:"
echo "  tests/test_containment.sh"
