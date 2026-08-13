#!/usr/bin/env python3
"""Compatibility wrapper for the all-target-file dataset generator.

The former version ranked changed files and exposed only one target. The
current protocol exposes every patch-touched file; use the new implementation
directly when possible.
"""

from __future__ import annotations

from prepare_all_target_files_eval_dataset import main


if __name__ == "__main__":
    raise SystemExit(main())
