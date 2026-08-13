#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  bash scripts/setup_server.sh \
    --mirror /srv/skill-vuldet-data/linux-stable.git

This script installs the shared filtered Linux mirror and checks the server
runtime. Skills are installed separately with install_skill.py, so multiple
skills can share the same mirror without sharing their checkouts.

Claude Code itself and its authentication are intentionally managed by the
server administrator, not stored in this repository.
EOF
}

LINUX_MIRROR=""
LINUX_REPOSITORY="https://github.com/gregkh/linux.git"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --mirror)
      LINUX_MIRROR="$2"
      shift 2
      ;;
    --linux-repository)
      LINUX_REPOSITORY="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if [[ -z "$LINUX_MIRROR" ]]; then
  usage >&2
  exit 2
fi

command -v git >/dev/null
command -v python3 >/dev/null
command -v claude >/dev/null

mkdir -p "$(dirname "$LINUX_MIRROR")"

if [[ -e "$LINUX_MIRROR" ]]; then
  if [[ ! -d "$LINUX_MIRROR" ]] || [[ "$(git -C "$LINUX_MIRROR" rev-parse --is-bare-repository 2>/dev/null || true)" != "true" ]]; then
    echo "existing Linux mirror is not a bare Git repository: $LINUX_MIRROR" >&2
    exit 1
  fi
  echo "using existing Linux mirror: $LINUX_MIRROR"
else
  echo "cloning filtered Linux mirror into: $LINUX_MIRROR"
  git clone --mirror --filter=blob:none "$LINUX_REPOSITORY" "$LINUX_MIRROR"
fi

python3 -m py_compile \
  scripts/materialize_repository_case.py \
  scripts/create_runner_manifest.py \
  scripts/install_skill.py \
  scripts/run_skill_batch.py

echo
echo "Shared server setup is ready."
echo "Linux mirror: $LINUX_MIRROR"
echo "Claude Code: $(claude --version 2>/dev/null || true)"
echo
echo "Install each skill into its own checkout, for example:"
cat <<'EOF'
python3 scripts/install_skill.py \
  --repository https://github.com/joe-bell/cva.git \
  --ref main \
  --source-path .agents/skills/security-review \
  --output /srv/skill-vuldet-data/skills/security-review-cva
EOF
