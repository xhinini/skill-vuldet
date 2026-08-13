#!/usr/bin/env python3
"""Materialize one clean parent-commit Linux repository for evaluation."""

from __future__ import annotations

import argparse
import posixpath
import csv
import shutil
import subprocess
import tarfile
from pathlib import Path, PurePosixPath


def read_manifest(path: Path) -> dict[str, dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return {row["case_id"]: row for row in csv.DictReader(handle)}


def safe_member_path(name: str) -> Path:
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts:
        raise RuntimeError(f"unsafe archive member: {name}")
    return Path(*path.parts)


def extract_archive(stream, destination: Path) -> None:
    with tarfile.open(fileobj=stream, mode="r|") as archive:
        for member in archive:
            relative = safe_member_path(member.name)
            target = destination / relative
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            if member.issym():
                link = PurePosixPath(member.linkname)
                resolved_link = PurePosixPath(
                    posixpath.normpath(str(PurePosixPath(member.name).parent / link))
                )
                if link.is_absolute() or resolved_link == PurePosixPath("..") or ".." in resolved_link.parts:
                    raise RuntimeError(f"unsafe symlink in archive: {member.name} -> {member.linkname}")
                target.parent.mkdir(parents=True, exist_ok=True)
                target.symlink_to(member.linkname)
                continue
            if not member.isfile():
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            source = archive.extractfile(member)
            if source is None:
                raise RuntimeError(f"cannot read archive member: {member.name}")
            with target.open("wb") as output:
                shutil.copyfileobj(source, output)
            target.chmod(member.mode & 0o7777)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True, help="Local bare Linux Git mirror.")
    parser.add_argument("--manifest", type=Path, required=True, help="Private ground_truth.csv for this sample.")
    parser.add_argument("--case-id", required=True)
    parser.add_argument("--output", type=Path, required=True, help="New directory for the clean source tree.")
    args = parser.parse_args()

    manifest = read_manifest(args.manifest)
    if args.case_id not in manifest:
        raise SystemExit(f"unknown case ID: {args.case_id}")
    if args.output.exists():
        raise SystemExit(f"refusing to overwrite existing output: {args.output}")
    args.output.mkdir(parents=True)
    parent = manifest[args.case_id]["parent_hash"]
    proc = subprocess.Popen(
        ["git", "-C", str(args.repo), "archive", "--format=tar", parent],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert proc.stdout is not None
    try:
        extract_archive(proc.stdout, args.output)
    except Exception:
        proc.kill()
        shutil.rmtree(args.output)
        raise
    stderr = proc.stderr.read().decode("utf-8", errors="replace") if proc.stderr else ""
    return_code = proc.wait()
    if return_code:
        shutil.rmtree(args.output)
        raise SystemExit(f"git archive failed for {args.case_id}: {stderr.strip()}")
    if (args.output / ".git").exists():
        shutil.rmtree(args.output / ".git")
    print(f"materialized {args.case_id} at {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
