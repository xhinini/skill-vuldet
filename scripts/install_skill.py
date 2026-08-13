#!/usr/bin/env python3
"""Install one GitHub-hosted Claude skill into a sparse local checkout."""

from __future__ import annotations

import argparse
import subprocess
from pathlib import Path, PurePosixPath


def validate_source_path(raw: str) -> str:
    path = PurePosixPath(raw)
    if path.is_absolute() or ".." in path.parts:
        raise SystemExit(f"skill source path must be relative and safe: {raw}")
    return raw or "."


def run(command: list[str]) -> None:
    subprocess.run(command, check=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", required=True, help="Git repository containing the skill.")
    parser.add_argument("--ref", default="main", help="Branch, tag, or commit to pin.")
    parser.add_argument(
        "--source-path",
        default=".agents/skills/security-review",
        help="Repository-relative directory containing SKILL.md.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="Dedicated sparse Git checkout for this skill.",
    )
    args = parser.parse_args()
    source_path = validate_source_path(args.source_path)
    skill_file = args.output / source_path / "SKILL.md"

    if skill_file.is_file():
        revision = subprocess.run(
            ["git", "-C", str(args.output), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        print(f"using existing skill checkout: {skill_file}")
        print(f"skill revision: {revision}")
        return 0
    if args.output.exists():
        raise SystemExit(
            f"output exists but does not contain {source_path}/SKILL.md: {args.output}"
        )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    run(
        [
            "git",
            "clone",
            "--filter=blob:none",
            "--depth",
            "1",
            "--no-checkout",
            args.repository,
            str(args.output),
        ]
    )
    run(["git", "-C", str(args.output), "fetch", "--depth", "1", "origin", args.ref])
    run(["git", "-C", str(args.output), "checkout", "--detach", "FETCH_HEAD"])
    run(["git", "-C", str(args.output), "sparse-checkout", "init", "--no-cone"])
    run(["git", "-C", str(args.output), "sparse-checkout", "set", "--no-cone", source_path])

    if not skill_file.is_file():
        raise SystemExit(f"installed checkout does not contain {source_path}/SKILL.md")
    revision = subprocess.run(
        ["git", "-C", str(args.output), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    print(f"installed skill at: {skill_file}")
    print(f"skill revision: {revision}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
