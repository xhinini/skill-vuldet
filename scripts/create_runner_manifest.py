#!/usr/bin/env python3
"""Create the smallest operator-only manifest needed to materialize cases."""

from __future__ import annotations

import argparse
import csv
import re
from pathlib import Path


COMMIT_RE = re.compile(r"^[0-9a-fA-F]{40}$")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--ground-truth",
        type=Path,
        required=True,
        help="Private ground_truth.csv containing case_id and parent_hash.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="Private output CSV; keep this outside the model workspace.",
    )
    args = parser.parse_args()

    with args.ground_truth.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))

    if not rows:
        raise SystemExit("ground-truth manifest is empty")
    required = {"case_id", "parent_hash"}
    missing = required.difference(rows[0])
    if missing:
        raise SystemExit(f"ground-truth manifest is missing columns: {sorted(missing)}")

    seen: set[str] = set()
    minimal_rows: list[dict[str, str]] = []
    for row in rows:
        case_id = (row.get("case_id") or "").strip()
        parent_hash = (row.get("parent_hash") or "").strip()
        if not case_id or case_id in seen:
            raise SystemExit(f"duplicate or empty case_id: {case_id!r}")
        if not COMMIT_RE.fullmatch(parent_hash):
            raise SystemExit(f"invalid parent_hash for {case_id}: {parent_hash!r}")
        seen.add(case_id)
        minimal_rows.append({"case_id": case_id, "parent_hash": parent_hash})

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["case_id", "parent_hash"])
        writer.writeheader()
        writer.writerows(minimal_rows)

    print(f"wrote {len(minimal_rows)} operator-only rows to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
