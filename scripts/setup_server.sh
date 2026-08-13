#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  bash scripts/setup_server.sh \
    --mirror /srv/skill-vuldet-data/linux-stable.git \
    --skill-cache /srv/skill-vuldet-data/skills/security-review

The script downloads the filtered Linux stable mirror and the selected skill.
Claude Code itself and its authentication are intentionally managed by the
server administrator, not stored in this repository.
EOF
}

LINUX_MIRROR=""
SKILL_CACHE=""
LINUX_REPOSITORY="https://github.com/gregkh/linux.git"
SKILL_REPOSITORY="https://github.com/joe-bell/cva.git"
SKILL_REF="main"
SKILL_PATH=".agents/skills/security-review"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --mirror)
      LINUX_MIRROR="$2"
      shift 2
      ;;
    --skill-cache)
      SKILL_CACHE="$2"
      shift 2
      ;;
    --linux-repository)
      LINUX_REPOSITORY="$2"
      shift 2
      ;;
    --skill-repository)
      SKILL_REPOSITORY="$2"
      shift 2
      ;;
    --skill-ref)
      SKILL_REF="$2"
      shift 2
      ;;
    --skill-path)
      SKILL_PATH="$2"
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

if [[ -z "$LINUX_MIRROR" || -z "$SKILL_CACHE" ]]; then
  usage >&2
  exit 2
fi

command -v git >/dev/null
command -v python3 >/dev/null
command -v claude >/dev/null

mkdir -p "$(dirname "$LINUX_MIRROR")" "$(dirname "$SKILL_CACHE")"

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

if [[ -f "$SKILL_CACHE/$SKILL_PATH/SKILL.md" ]]; then
  echo "using existing skill cache: $SKILL_CACHE/$SKILL_PATH"
else
  if [[ -e "$SKILL_CACHE" ]]; then
    echo "skill cache exists but does not contain $SKILL_PATH/SKILL.md: $SKILL_CACHE" >&2
    exit 1
  fi
  echo "cloning selected skill into: $SKILL_CACHE"
  git clone --depth 1 --filter=blob:none --sparse --branch "$SKILL_REF" \
    "$SKILL_REPOSITORY" "$SKILL_CACHE"
  git -C "$SKILL_CACHE" sparse-checkout set "$SKILL_PATH"
fi

python3 -m py_compile scripts/materialize_repository_case.py scripts/create_runner_manifest.py scripts/run_skill_batch.py

echo
echo "Server prerequisites are ready."
echo "Linux mirror: $LINUX_MIRROR"
echo "Skill directory: $SKILL_CACHE/$SKILL_PATH"
echo "Claude Code: $(claude --version 2>/dev/null || true)"
