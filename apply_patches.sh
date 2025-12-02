#!/usr/bin/env bash
# Apply patch files sequentially with resume support.

set -euo pipefail

usage() {
  cat <<EOF
Usage: $(basename "$0") PATCH_DIR [START_PATCH]

PATCH_DIR     Directory containing .patch files (processed in lexicographic order)
START_PATCH   Optional file name (or path) to resume from; if omitted, starts at first patch
EOF
}

if [[ ${1-} == "-h" || ${1-} == "--help" ]]; then
  usage
  exit 0
fi

if [[ $# -lt 1 || $# -gt 2 ]]; then
  usage
  exit 1
fi

PATCH_DIR=$1
START_BASENAME=${2-}

if [[ ! -d "$PATCH_DIR" ]]; then
  echo "Error: patch directory '$PATCH_DIR' does not exist" >&2
  exit 1
fi

mapfile -t PATCHES < <(find "$PATCH_DIR" -maxdepth 1 -type f -name '*.patch' | sort)

if [[ ${#PATCHES[@]} -eq 0 ]]; then
  echo "No .patch files found in $PATCH_DIR" >&2
  exit 1
fi

start_seen=false
if [[ -z "$START_BASENAME" ]]; then
  start_seen=true
fi

for patch_path in "${PATCHES[@]}"; do
  patch_name=$(basename "$patch_path")
  if ! $start_seen; then
    if [[ "$patch_name" == "$START_BASENAME" ]]; then
      start_seen=true
    else
      continue
    fi
  fi

  echo "Applying $patch_name..."
  if git apply --3way "$patch_path"; then
    echo "Applied $patch_name"
  else
    echo "Failed to apply $patch_name. Resolve issues and rerun starting from this file." >&2
    exit 1
  fi
done

echo "All patches applied."

