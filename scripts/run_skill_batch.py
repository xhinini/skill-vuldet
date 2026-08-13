#!/usr/bin/env python3
"""Run a selected Claude Code skill over the public 148-case manifest."""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_SKILL_NAME = "security-review"
DEFAULT_SKILL_INVOCATION = "/security-review"
DEFAULT_SKILL_REPOSITORY = "https://github.com/joe-bell/cva.git"
DEFAULT_SKILL_REF = "main"
DEFAULT_SKILL_SOURCE_PATH = ".agents/skills/security-review"
DEFAULT_TOOLS = ["Read", "Grep", "Glob"]
CASE_ID_RE = re.compile(r"^[A-Za-z0-9_.-]+$")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise SystemExit(f"CSV has no header: {path}")
        return list(reader)


def index_rows(rows: list[dict[str, str]], label: str) -> dict[str, dict[str, str]]:
    indexed: dict[str, dict[str, str]] = {}
    for row in rows:
        case_id = (row.get("case_id") or "").strip()
        if not case_id:
            raise SystemExit(f"{label} contains an empty case_id")
        if not CASE_ID_RE.fullmatch(case_id):
            raise SystemExit(f"{label} contains an unsafe case_id: {case_id!r}")
        if case_id in indexed:
            raise SystemExit(f"{label} contains duplicate case_id: {case_id}")
        indexed[case_id] = row
    return indexed


def target_paths(raw: str, case_id: str) -> list[str]:
    paths = [item.strip() for item in raw.split(";") if item.strip()]
    if not paths:
        raise SystemExit(f"public manifest has no target files for {case_id}")
    for item in paths:
        candidate = Path(item)
        if candidate.is_absolute() or ".." in candidate.parts:
            raise SystemExit(f"unsafe target file for {case_id}: {item}")
    return paths


def ensure_skill_cache(
    cache: Path,
    repository: str,
    ref: str,
    source_path: str,
) -> Path:
    skill_path = cache / source_path
    if (skill_path / "SKILL.md").is_file():
        return skill_path

    installer = Path(__file__).with_name("install_skill.py")
    subprocess.run(
        [
            sys.executable,
            str(installer),
            "--repository",
            repository,
            "--ref",
            ref,
            "--source-path",
            source_path,
            "--output",
            str(cache),
        ],
        check=True,
    )
    return skill_path


def git_revision(path: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return result.stdout.strip()


def build_prompt(
    invocation: str,
    files: list[str],
    template: str | None,
) -> str:
    listed = "\n".join(f"- {path}" for path in files)
    focus_args = " ".join(f"--focus {path}" for path in files)
    rendered_invocation = invocation.replace("{TARGET_FOCUS_ARGS}", focus_args)
    if template is not None:
        return (
            template.replace("{SKILL_INVOCATION}", rendered_invocation)
            .replace("{TARGET_FILES}", listed)
            .replace("{TARGET_FOCUS_ARGS}", focus_args)
        )
    return f"""{rendered_invocation}

Review all target files listed below according to the selected skill:

{listed}

Inspect these target files first. You may inspect other files inside the supplied
repository only when needed to understand types, macros, callers, callees,
control flow, and data flow.

Do not use the internet, external files, Git history, CVE databases, patches,
fixed files, private metadata, or anything outside the supplied repository and
the selected skill. Do not modify files.

Follow the selected skill's normal output format. Do not modify files.
"""


def load_skill_config(path: Path | None) -> tuple[dict[str, Any], str | None]:
    if path is None:
        return {}, None
    with path.open(encoding="utf-8") as handle:
        config = json.load(handle)
    if not isinstance(config, dict):
        raise SystemExit(f"skill config must contain a JSON object: {path}")
    prompt_template = config.get("prompt_template")
    if prompt_template is None:
        return config, None
    if not isinstance(prompt_template, str):
        raise SystemExit(f"prompt_template must be a string: {path}")
    template_path = Path(prompt_template)
    if not template_path.is_absolute():
        template_path = path.parent / template_path
    try:
        return config, template_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise SystemExit(f"cannot read prompt template {template_path}: {exc}") from exc


def extract_final_events(path: Path) -> dict[str, Any]:
    last_result: dict[str, Any] | None = None
    last_assistant: dict[str, Any] | None = None
    malformed_lines = 0
    with path.open(encoding="utf-8", errors="replace") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                malformed_lines += 1
                continue
            if not isinstance(event, dict):
                continue
            if event.get("type") == "result":
                last_result = event
            if event.get("type") == "assistant":
                last_assistant = event
    return {
        "result_event": last_result,
        "last_assistant_event": last_assistant,
        "malformed_lines": malformed_lines,
    }


def terminate_process(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=5)
    except (ProcessLookupError, subprocess.TimeoutExpired):
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def collect_skill_artifacts(
    repository_root: Path,
    case_output: Path,
    artifact_names: list[str],
) -> tuple[list[str], list[str]]:
    copied: list[str] = []
    errors: list[str] = []
    artifact_root = case_output / "skill_artifacts"
    for raw_name in artifact_names:
        relative = Path(raw_name)
        if relative.is_absolute() or ".." in relative.parts:
            errors.append(f"unsafe configured artifact path: {raw_name}")
            continue
        source = repository_root / relative
        if not source.is_file():
            errors.append(f"artifact was not produced: {raw_name}")
            continue
        destination = artifact_root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        copied.append(raw_name)
    return copied, errors


def run_case(
    *,
    args: argparse.Namespace,
    public_row: dict[str, str],
    private_row: dict[str, str],
    skill_source: Path,
    skill_revision: str | None,
    prompt_template: str | None,
    skill_artifacts: list[str],
    output_root: Path,
    work_root: Path,
) -> str:
    case_id = public_row["case_id"].strip()
    files = target_paths(public_row.get("target_files", ""), case_id)
    parent_hash = (private_row.get("parent_hash") or "").strip()
    if not re.fullmatch(r"[0-9a-fA-F]{40}", parent_hash):
        raise RuntimeError(f"invalid parent_hash for {case_id}")

    case_output = output_root / case_id
    case_output.mkdir(parents=True, exist_ok=True)
    run_metadata_path = case_output / "run.json"
    if run_metadata_path.exists() and not args.rerun:
        print(f"skip {case_id}: {run_metadata_path} already exists")
        return "skipped"

    prompt = build_prompt(args.skill_invocation, files, prompt_template)
    (case_output / "prompt.txt").write_text(prompt, encoding="utf-8")
    trajectory_path = case_output / "trajectory.jsonl"
    stderr_path = case_output / "stderr.log"
    materialize_log_path = case_output / "materialize.log"
    final_path = case_output / "final.json"
    started_at = utc_now()
    started_clock = time.monotonic()
    command = [
        args.claude_command,
        "--bare",
        "--print",
        "--verbose",
        "--tools",
        *args.tools,
        "--permission-mode",
        args.permission_mode,
        "--output-format",
        "stream-json",
        "--no-session-persistence",
    ]
    if args.model:
        command.extend(["--model", args.model])
    if args.max_budget_usd is not None:
        command.extend(["--max-budget-usd", str(args.max_budget_usd)])
    command.extend(["-p", prompt])

    workspace = Path(tempfile.mkdtemp(prefix=f"{case_id}-", dir=work_root))
    repository_root = workspace / "repository"
    status = "failed"
    return_code: int | None = None
    error_message: str | None = None
    timed_out = False
    copied_artifacts: list[str] = []
    artifact_errors: list[str] = []
    try:
        materializer = Path(__file__).with_name("materialize_repository_case.py")
        materialize = subprocess.run(
            [
                sys.executable,
                str(materializer),
                "--repo",
                str(args.repo_mirror),
                "--manifest",
                str(args.private_manifest),
                "--case-id",
                case_id,
                "--output",
                str(repository_root),
            ],
            capture_output=True,
            text=True,
        )
        materialize_log_path.write_text(
            materialize.stdout + materialize.stderr,
            encoding="utf-8",
        )
        if materialize.returncode:
            raise RuntimeError(
                f"materialization failed with exit code {materialize.returncode}"
            )

        skill_destination = repository_root / ".claude" / "skills" / args.skill_name
        skill_destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(skill_source, skill_destination)

        with trajectory_path.open("w", encoding="utf-8") as trajectory, stderr_path.open(
            "w", encoding="utf-8"
        ) as stderr:
            process = subprocess.Popen(
                command,
                cwd=repository_root,
                stdout=trajectory,
                stderr=stderr,
                text=True,
                start_new_session=True,
            )
            try:
                return_code = process.wait(timeout=args.timeout_seconds)
            except subprocess.TimeoutExpired:
                timed_out = True
                terminate_process(process)
                return_code = process.returncode

        status = "timeout" if timed_out else ("completed" if return_code == 0 else "failed")
        final_payload = extract_final_events(trajectory_path)
        write_json(
            final_path,
            {
                "case_id": case_id,
                "skill_name": args.skill_name,
                "status": status,
                "exit_code": return_code,
                **final_payload,
            },
        )
    except Exception as exc:  # Keep the batch moving and record the failed case.
        error_message = str(exc)
        write_json(
            final_path,
            {
                "case_id": case_id,
                "skill_name": args.skill_name,
                "status": status,
                "exit_code": return_code,
                "error": error_message,
            },
        )
    finally:
        if repository_root.exists():
            try:
                copied_artifacts, artifact_errors = collect_skill_artifacts(
                    repository_root,
                    case_output,
                    skill_artifacts,
                )
            except Exception as exc:
                artifact_errors.append(str(exc))
        if not args.keep_workspaces:
            shutil.rmtree(workspace, ignore_errors=True)

    metadata = {
        "case_id": case_id,
        "skill_name": args.skill_name,
        "target_files": files,
        "started_at": started_at,
        "finished_at": utc_now(),
        "duration_seconds": round(time.monotonic() - started_clock, 3),
        "status": status,
        "exit_code": return_code,
        "error": error_message,
        "skill_repository": args.skill_repository,
        "skill_ref": args.skill_ref,
        "skill_revision": skill_revision,
        "tool_allowlist": args.tools,
        "permission_mode": args.permission_mode,
        "workspace_deleted": not args.keep_workspaces,
        "skill_artifact_files": copied_artifacts,
        "skill_artifact_errors": artifact_errors,
        "trajectory_file": str(trajectory_path.name),
        "final_file": str(final_path.name),
    }
    write_json(run_metadata_path, metadata)
    print(f"{status:9} {case_id}  " + "; ".join(files))
    return status


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo-mirror",
        type=Path,
        required=True,
        help="Local bare Linux mirror created by setup_server.sh.",
    )
    parser.add_argument(
        "--private-manifest",
        type=Path,
        required=True,
        help="Operator-only CSV with at least case_id,parent_hash.",
    )
    parser.add_argument(
        "--public-manifest",
        type=Path,
        default=Path("public/cases.csv"),
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=Path("results"),
        help="Directory for trajectories and run metadata.",
    )
    parser.add_argument(
        "--work-root",
        type=Path,
        default=Path(".runtime/workspaces"),
        help="Scratch directory; each case is deleted after completion by default.",
    )
    parser.add_argument("--case-id", action="append", help="Run only this case; repeatable.")
    parser.add_argument("--limit", type=int, help="Run at most this many cases in manifest order.")
    parser.add_argument("--rerun", action="store_true", help="Rerun cases with existing run.json files.")
    parser.add_argument("--keep-workspaces", action="store_true", help="Keep materialized trees for inspection.")
    parser.add_argument("--timeout-seconds", type=int, default=1800)
    parser.add_argument("--claude-command", default="claude")
    parser.add_argument("--model")
    parser.add_argument("--max-budget-usd", type=float)
    parser.add_argument("--permission-mode", default="dontAsk")
    parser.add_argument("--skill-config", type=Path, help="JSON config for one skill experiment.")
    parser.add_argument("--skill-name")
    parser.add_argument("--skill-invocation")
    parser.add_argument("--skill-repository")
    parser.add_argument("--skill-ref")
    parser.add_argument("--skill-source-path")
    parser.add_argument("--prompt-template", type=Path)
    parser.add_argument(
        "--skill-cache",
        type=Path,
        default=None,
        help="Sparse checkout containing the selected skill.",
    )
    parser.add_argument(
        "--tools",
        nargs="+",
        default=None,
        help="Allowed Claude tools. Default is Read Grep Glob.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    skill_config, config_template = load_skill_config(args.skill_config)
    args.skill_name = args.skill_name or skill_config.get("skill_name") or DEFAULT_SKILL_NAME
    args.skill_invocation = (
        args.skill_invocation
        or skill_config.get("skill_invocation")
        or DEFAULT_SKILL_INVOCATION
    )
    args.skill_repository = (
        args.skill_repository
        or skill_config.get("skill_repository")
        or DEFAULT_SKILL_REPOSITORY
    )
    args.skill_ref = args.skill_ref or skill_config.get("skill_ref") or DEFAULT_SKILL_REF
    args.skill_source_path = (
        args.skill_source_path
        or skill_config.get("skill_source_path")
        or DEFAULT_SKILL_SOURCE_PATH
    )
    args.tools = args.tools or skill_config.get("default_tools") or DEFAULT_TOOLS
    if not isinstance(args.tools, list) or not all(isinstance(tool, str) for tool in args.tools):
        raise SystemExit("skill tools must be a list of strings")
    if args.prompt_template is not None:
        try:
            prompt_template = args.prompt_template.read_text(encoding="utf-8")
        except OSError as exc:
            raise SystemExit(f"cannot read prompt template {args.prompt_template}: {exc}") from exc
    else:
        prompt_template = config_template
    configured_artifacts = skill_config.get("output_files", [])
    if not isinstance(configured_artifacts, list) or not all(
        isinstance(item, str) for item in configured_artifacts
    ):
        raise SystemExit("output_files must be a list of strings")
    args.skill_artifacts = configured_artifacts
    if args.skill_cache is None:
        args.skill_cache = Path(".runtime/skill-cache") / args.skill_name
    if not CASE_ID_RE.fullmatch(args.skill_name):
        raise SystemExit(f"unsafe skill name: {args.skill_name}")
    if args.limit is not None and args.limit < 1:
        raise SystemExit("--limit must be positive")
    if args.timeout_seconds < 1:
        raise SystemExit("--timeout-seconds must be positive")
    if not args.repo_mirror.is_dir():
        raise SystemExit(f"Linux mirror directory does not exist: {args.repo_mirror}")
    if not args.private_manifest.is_file():
        raise SystemExit(f"private manifest does not exist: {args.private_manifest}")
    if not args.public_manifest.is_file():
        raise SystemExit(f"public manifest does not exist: {args.public_manifest}")

    public_rows = read_csv_rows(args.public_manifest)
    public_index = index_rows(public_rows, "public manifest")
    private_rows = read_csv_rows(args.private_manifest)
    if not private_rows:
        raise SystemExit("private manifest is empty")
    private_index = index_rows(private_rows, "private manifest")
    if "parent_hash" not in private_rows[0]:
        raise SystemExit("private manifest must contain parent_hash")

    requested = set(args.case_id or public_index)
    unknown = requested.difference(public_index)
    if unknown:
        raise SystemExit(f"unknown case IDs: {sorted(unknown)}")
    missing_private = requested.difference(private_index)
    if missing_private:
        raise SystemExit(f"private manifest is missing case IDs: {sorted(missing_private)}")
    ordered_rows = [row for row in public_rows if row["case_id"] in requested]
    if args.limit is not None:
        ordered_rows = ordered_rows[: args.limit]

    args.output_root = args.output_root / args.skill_name
    args.output_root.mkdir(parents=True, exist_ok=True)
    args.work_root.mkdir(parents=True, exist_ok=True)
    skill_source = ensure_skill_cache(
        args.skill_cache,
        args.skill_repository,
        args.skill_ref,
        args.skill_source_path,
    )
    skill_revision = git_revision(args.skill_cache)

    counts: dict[str, int] = {}
    for public_row in ordered_rows:
        case_id = public_row["case_id"]
        status = run_case(
            args=args,
            public_row=public_row,
            private_row=private_index[case_id],
            skill_source=skill_source,
            skill_revision=skill_revision,
            prompt_template=prompt_template,
            skill_artifacts=args.skill_artifacts,
            output_root=args.output_root,
            work_root=args.work_root,
        )
        counts[status] = counts.get(status, 0) + 1

    print(f"summary: {counts}")
    return 0 if not counts.get("failed") and not counts.get("timeout") else 1


if __name__ == "__main__":
    raise SystemExit(main())
