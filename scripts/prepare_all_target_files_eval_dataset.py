#!/usr/bin/env python3
"""Regenerate a target-context sample with every patch-touched file exposed.

This script deliberately separates exact structural patch facts from semantic
vulnerability labels. Diff hunks, old/new line numbers, and line text are
mechanically recoverable. A patch alone does not prove that every touched line
or supporting file is the root cause, so semantic file labels are emitted as a
manual-review queue instead of being guessed.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path


HUNK_RE = re.compile(
    r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(?: ?(.*))?$"
)
DIFF_RE = re.compile(r"^diff --git a/(.+?) b/(.+?)$", re.MULTILINE)
CONTROL_CONTEXT_NAMES = {"if", "for", "while", "switch", "catch"}


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, fields: list[str], rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def json_value(value: object) -> str:
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"))


def compact_ranges(numbers: list[int]) -> str:
    if not numbers:
        return ""
    values = sorted(set(numbers))
    ranges: list[str] = []
    start = previous = values[0]
    for value in values[1:]:
        if value == previous + 1:
            previous = value
            continue
        ranges.append(str(start) if start == previous else f"{start}-{previous}")
        start = previous = value
    ranges.append(str(start) if start == previous else f"{start}-{previous}")
    return ";".join(ranges)


def diff_chunks(patch_text: str) -> dict[str, str]:
    matches = list(DIFF_RE.finditer(patch_text))
    chunks: dict[str, str] = {}
    for index, match in enumerate(matches):
        left, right = match.group(1), match.group(2)
        path = right if right != "/dev/null" else left
        end = matches[index + 1].start() if index + 1 < len(matches) else len(patch_text)
        chunks[path] = patch_text[match.start() : end]
    return chunks


def function_candidate(context: str) -> str:
    """Extract a conservative function-name candidate from Git's hunk label."""

    names = re.findall(r"([A-Za-z_]\w*)\s*\(", context)
    for name in reversed(names):
        if name not in CONTROL_CONTEXT_NAMES:
            return name
    return ""


def parse_hunks(path: str, chunk: str) -> list[dict[str, object]]:
    lines = chunk.splitlines()
    hunk_indexes = [index for index, line in enumerate(lines) if line.startswith("@@")]
    hunks: list[dict[str, object]] = []
    for hunk_number, start_index in enumerate(hunk_indexes, 1):
        match = HUNK_RE.match(lines[start_index])
        if not match:
            raise ValueError(f"unparseable hunk header for {path}: {lines[start_index]}")
        old_start = int(match.group(1))
        old_count = int(match.group(2) or "1")
        new_start = int(match.group(3))
        new_count = int(match.group(4) or "1")
        context = (match.group(5) or "").strip()
        old_line = old_start
        new_line = new_start
        deleted: list[dict[str, object]] = []
        added: list[dict[str, object]] = []
        context_lines: list[dict[str, int]] = []
        end_index = hunk_indexes[hunk_number] if hunk_number < len(hunk_indexes) else len(lines)
        for line in lines[start_index + 1 : end_index]:
            if line == "-- " or line.startswith("\\ No newline"):
                continue
            if line.startswith("-") and not line.startswith("---"):
                deleted.append({"line_number": old_line, "text": line[1:]})
                old_line += 1
            elif line.startswith("+") and not line.startswith("+++"):
                added.append({"line_number": new_line, "text": line[1:]})
                new_line += 1
            elif line.startswith(" "):
                context_lines.append({"old": old_line, "new": new_line})
                old_line += 1
                new_line += 1
        if old_line != old_start + old_count or new_line != new_start + new_count:
            raise ValueError(
                f"hunk line count mismatch for {path}: {lines[start_index]} "
                f"ended at old={old_line}, new={new_line}"
            )
        hunks.append(
            {
                "path": path,
                "hunk_index": hunk_number,
                "old_start": old_start,
                "old_count": old_count,
                "new_start": new_start,
                "new_count": new_count,
                "hunk_header": lines[start_index],
                "function_context_raw": context,
                "function_name_candidate": function_candidate(context),
                "deleted": deleted,
                "added": added,
                "context_lines": context_lines,
            }
        )
    return hunks


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dataset", type=Path, required=True)
    parser.add_argument("--artifact-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    if args.output.exists():
        raise SystemExit(f"refusing to overwrite existing output: {args.output}")
    source_public = args.source_dataset / "public" / "cases.csv"
    source_private = args.source_dataset / "private" / "ground_truth.csv"
    public_source = {row["case_id"]: row for row in read_csv(source_public)}
    private_source = read_csv(source_private)
    if not private_source:
        raise SystemExit(f"empty private manifest: {source_private}")

    public_rows: list[dict[str, str]] = []
    private_rows: list[dict[str, str]] = []
    hunk_rows: list[dict[str, str]] = []
    line_rows: list[dict[str, str]] = []
    function_rows: list[dict[str, str]] = []
    semantic_rows: list[dict[str, str]] = []
    cwe_counts: Counter[str] = Counter()
    file_counts: Counter[int] = Counter()

    for source_row in private_source:
        case_id = source_row["case_id"]
        if case_id not in public_source:
            raise SystemExit(f"private case missing from public manifest: {case_id}")
        changed_files = sorted(
            path for path in source_row["changed_files"].split(";") if path
        )
        if not changed_files:
            raise SystemExit(f"case has no changed files: {case_id}")
        patch_paths = [path for path in source_row["patch_artifact_path"].split(";") if path]
        patch_text = "\n".join(
            (args.artifact_root / path).read_text(errors="replace") for path in patch_paths
        )
        chunks = diff_chunks(patch_text)
        missing = [path for path in changed_files if path not in chunks]
        if missing:
            raise SystemExit(f"patch is missing changed paths for {case_id}: {missing}")

        target_files = ";".join(changed_files)
        public_rows.append(
            {
                "case_id": case_id,
                "repository_local_path": public_source[case_id]["repository_local_path"],
                "target_files": target_files,
            }
        )
        private_row = {
            key: value
            for key, value in source_row.items()
            if key
            not in {
                "target_file",
                "target_selection_method",
                "target_selection_reason",
                "target_candidates",
            }
        }
        private_row.update(
            {
                "target_files": target_files,
                "case_vulnerable": "true",
                "case_ground_truth_status": "confirmed_case_level",
            }
        )
        private_rows.append(private_row)
        cwe_counts[source_row["sampling_cwe"]] += 1
        file_counts[len(changed_files)] += 1

        for file_path in changed_files:
            hunks = parse_hunks(file_path, chunks[file_path])
            deleted_count = sum(len(hunk["deleted"]) for hunk in hunks)
            added_count = sum(len(hunk["added"]) for hunk in hunks)
            semantic_rows.append(
                {
                    "case_id": case_id,
                    "cve_id": source_row["cve_id"],
                    "sampling_cwe": source_row["sampling_cwe"],
                    "nvd_cwe_ids": source_row["cwe_ids"],
                    "file_path": file_path,
                    "patch_touched": "true",
                    "case_vulnerable": "true",
                    "file_contains_root_cause": "",
                    "vulnerable_function": "",
                    "root_cause_before_line_numbers": "",
                    "sink_before_line_numbers": "",
                    "semantic_review_status": "needs_manual_file_review",
                    "review_note": (
                        "Patch-touched file is structurally confirmed. Semantic root-cause "
                        "scope is intentionally not inferred from line counts."
                    ),
                }
            )
            for hunk in hunks:
                deleted = hunk["deleted"]
                added = hunk["added"]
                hunk_rows.append(
                    {
                        "case_id": case_id,
                        "cve_id": source_row["cve_id"],
                        "sampling_cwe": source_row["sampling_cwe"],
                        "file_path": file_path,
                        "hunk_index": str(hunk["hunk_index"]),
                        "old_start": str(hunk["old_start"]),
                        "old_count": str(hunk["old_count"]),
                        "new_start": str(hunk["new_start"]),
                        "new_count": str(hunk["new_count"]),
                        "hunk_header": str(hunk["hunk_header"]),
                        "function_context_raw": str(hunk["function_context_raw"]),
                        "function_name_candidate": str(hunk["function_name_candidate"]),
                        "pre_fix_deleted_line_numbers": compact_ranges(
                            [int(item["line_number"]) for item in deleted]
                        ),
                        "post_fix_added_line_numbers": compact_ranges(
                            [int(item["line_number"]) for item in added]
                        ),
                        "pre_fix_deleted_line_text": json_value(
                            [item["text"] for item in deleted]
                        ),
                        "post_fix_added_line_text": json_value(
                            [item["text"] for item in added]
                        ),
                        "context_line_numbers": json_value(hunk["context_lines"]),
                        "changed_line_count": str(len(deleted) + len(added)),
                    }
                )
                function_rows.append(
                    {
                        "case_id": case_id,
                        "cve_id": source_row["cve_id"],
                        "file_path": file_path,
                        "hunk_index": str(hunk["hunk_index"]),
                        "function_name_candidate": str(hunk["function_name_candidate"]),
                        "function_context_raw": str(hunk["function_context_raw"]),
                        "pre_fix_line_numbers": compact_ranges(
                            [int(item["line_number"]) for item in deleted]
                        ),
                        "function_ground_truth_status": "patch_context_candidate",
                        "review_note": (
                            "Candidate comes from Git's hunk function context; verify against "
                            "the before-fix source before treating it as an exact function label."
                        ),
                    }
                )
                for item in deleted:
                    line_rows.append(
                        {
                            "case_id": case_id,
                            "cve_id": source_row["cve_id"],
                            "sampling_cwe": source_row["sampling_cwe"],
                            "file_path": file_path,
                            "hunk_index": str(hunk["hunk_index"]),
                            "side": "before",
                            "line_number": str(item["line_number"]),
                            "line_kind": "deleted_pre_fix_line",
                            "line_text": str(item["text"]),
                        }
                    )
                for item in added:
                    line_rows.append(
                        {
                            "case_id": case_id,
                            "cve_id": source_row["cve_id"],
                            "sampling_cwe": source_row["sampling_cwe"],
                            "file_path": file_path,
                            "hunk_index": str(hunk["hunk_index"]),
                            "side": "after",
                            "line_number": str(item["line_number"]),
                            "line_kind": "added_post_fix_line",
                            "line_text": str(item["text"]),
                        }
                    )

    public_fields = ["case_id", "repository_local_path", "target_files"]
    private_fields = list(private_rows[0])
    hunk_fields = list(hunk_rows[0])
    line_fields = list(line_rows[0])
    function_fields = list(function_rows[0])
    semantic_fields = list(semantic_rows[0])
    write_csv(args.output / "public" / "cases.csv", public_fields, public_rows)
    write_csv(args.output / "private" / "ground_truth.csv", private_fields, private_rows)
    write_csv(args.output / "private" / "patch_hunks.csv", hunk_fields, hunk_rows)
    write_csv(args.output / "private" / "patch_lines.csv", line_fields, line_rows)
    write_csv(args.output / "private" / "function_context_candidates.csv", function_fields, function_rows)
    write_csv(args.output / "private" / "semantic_review_queue.csv", semantic_fields, semantic_rows)

    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_dataset": str(args.source_dataset),
        "case_count": len(public_rows),
        "protocol": "target-file-plus-repository-context",
        "target_policy": "Expose every changed file from the fixing patch as target_files; do not rank or select a primary file.",
        "cwe_counts": dict(cwe_counts),
        "changed_file_count_distribution": {str(k): v for k, v in sorted(file_counts.items())},
        "changed_file_count": len(semantic_rows),
        "patch_hunk_count": len(hunk_rows),
        "patch_changed_line_count": len(line_rows),
        "before_fix_deleted_line_count": sum(row["side"] == "before" for row in line_rows),
        "after_fix_added_line_count": sum(row["side"] == "after" for row in line_rows),
        "exact_structural_ground_truth": [
            "case-level vulnerability status",
            "NVD CWE labels",
            "patch-touched file paths",
            "before/after hunk ranges",
            "before-side deleted line numbers and text",
            "after-side added line numbers and text",
        ],
        "semantic_ground_truth_status": "file-level root-cause labels and sink lines require manual review; see private/semantic_review_queue.csv",
        "public_manifest": "public/cases.csv",
        "private_manifest": "private/ground_truth.csv",
        "leakage_policy": "Only public/ may be provided to the model. Keep private/, patches, fixed files, raw collection, and Git mirror unavailable.",
    }
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "dataset_summary.json").write_text(
        json.dumps(summary, indent=2) + "\n", encoding="utf-8"
    )
    (args.output / "README.md").write_text(
        "# June 2026 Target-Files Plus Repository-Context Sample\n\n"
        "This dataset contains the same 148 June 2026 Linux CVE cases and CWE quotas as the previous sample. "
        "For each case, `public/cases.csv` exposes every file touched by the fixing patch in `target_files`; "
        "there is no primary-file ranking. The complete parent repository is available for context.\n\n"
        "## Public protocol\n\n"
        "Give the evaluated skill the materialized parent repository and all paths in `target_files`. Ask it to "
        "inspect all listed target files first, then use repository context such as headers, callers, callees, and "
        "related definitions. Do not provide CVE IDs, CWE IDs, commit hashes, patches, fixed files, private manifests, "
        "or the Git mirror.\n\n"
        "## Ground-truth layers\n\n"
        "`private/ground_truth.csv` contains case identity, parent/commit metadata, NVD CWE labels, and all changed "
        "paths. `private/patch_hunks.csv` and `private/patch_lines.csv` are exact structural ground truth parsed from "
        "the unified diff. They support patch-localization metrics on before-side deleted lines and after-side added "
        "lines. `private/function_context_candidates.csv` contains patch hunk function-context candidates and must be verified "
        "against source before being treated as exact function ground truth.\n\n"
        "`private/semantic_review_queue.csv` is intentionally explicit about what remains to be curated: whether a "
        "specific changed file contains the root cause, the vulnerable function, and sink lines. Do not score those "
        "semantic fields until their review status is changed from `needs_manual_file_review`.\n\n"
        "## Recommended metrics\n\n"
        "1. Case detection: compare the model's vulnerability decision with `case_vulnerable`. This sample is "
        "positive-only, so it measures recall; add manually reviewed clean negative controls to measure precision, "
        "specificity, and false-positive rate.\n"
        "2. CWE classification: compare the reported CWE with the NVD CWE label set, treating multi-label cases as "
        "set matches.\n"
        "3. Patch localization: compare reported target paths and before-fix line numbers with `patch_lines.csv`; "
        "keep before-side deleted-line and after-side added-line metrics separate.\n"
        "4. Function and statement localization: use verified semantic labels, or report the result as structural "
        "patch-context matching rather than claiming root-cause accuracy.\n\n"
        "Only `public/` may be mounted in the model environment.\n",
        encoding="utf-8",
    )
    (args.output / "semantic_annotation_guide.md").write_text(
        "# Semantic Ground-Truth Annotation Guide\n\n"
        "The patch tables are exact structural facts. This file explains how to curate the semantic fields in "
        "`private/semantic_review_queue.csv` before strict root-cause metrics are reported.\n\n"
        "## File-level label\n\n"
        "Set `file_contains_root_cause` to `true` only when the before-fix file contains the behavior that enables "
        "the CVE, not merely a declaration, API migration, caller update, or documentation change. Set it to `false` "
        "for a supporting-only file. Use `uncertain` when the evidence does not support a confident decision.\n\n"
        "## Location labels\n\n"
        "Record the exact before-fix function symbol in `vulnerable_function`. Record the smallest before-fix line set "
        "that expresses the root cause in `root_cause_before_line_numbers`; do not automatically copy every patch line. "
        "Record the unsafe dereference, copy, indexing, free, arithmetic, or other impact operation separately in "
        "`sink_before_line_numbers` when it is distinct.\n\n"
        "## Review quality\n\n"
        "Use `semantic_review_status=reviewed` only after inspecting the parent source and patch. Prefer two independent "
        "reviews for disputed multi-file cases and keep an adjudication note. Score strict file/function/statement/sink "
        "metrics only on reviewed rows. Use `patch_lines.csv` for the separate structural patch-localization metric.\n",
        encoding="utf-8",
    )
    (args.output / "use_guide.md").write_text(
        "# Target-Files Plus Repository-Context Evaluation Guide\n\n"
        "For each public row, materialize the parent repository and pass every semicolon-separated path in "
        "`target_files` to the evaluated skill. The target list is the full set of patch-touched files; no file was "
        "selected by an arbitrary score.\n\n"
        "Use a fixed prompt such as:\n\n"
        "```text\n"
        "Review all target files listed below for security vulnerabilities:\n"
        "<TARGET_FILES>\n\n"
        "Inspect these target files first. You may inspect the supplied repository for context needed to understand "
        "their types, macros, callers, callees, control flow, and data flow. Do not use the internet, external files, "
        "Git history, CVE databases, patches, fixed files, private metadata, or anything outside the supplied "
        "repository and selected skill. Report whether a vulnerability is present, the CWE, affected functions, "
        "statements, and exact before-fix line numbers. Do not modify the repository.\n"
        "```\n\n"
        "The case-level vulnerability and NVD CWE labels are confirmed metadata. The exact changed files, hunk ranges, "
        "and before/after changed lines are mechanical patch facts. File-level root-cause labels, sink lines, and "
        "fully verified function/statement labels are separate semantic ground truth and must be manually reviewed "
        "before using them for strict accuracy claims.\n\n"
        "Only `public/` may be visible to the model. Keep `private/`, the artifact collection, patches, fixed files, and "
        "the Git mirror outside the model environment.\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
