# Semantic Ground-Truth Annotation Guide

The patch tables are exact structural facts. This file explains how to curate
the semantic fields in `private/semantic_review_queue.csv` before strict
root-cause metrics are reported.

## File-Level Label

Set `file_contains_root_cause` to `true` only when the before-fix file contains
the behavior that enables the CVE, not merely a declaration, API migration,
caller update, or documentation change. Set it to `false` for a supporting-only
file. Use `uncertain` when the evidence does not support a confident decision.

## Location Labels

Record the exact before-fix function symbol in `vulnerable_function`. Record the
smallest before-fix line set that expresses the root cause in
`root_cause_before_line_numbers`; do not automatically copy every patch line.
Record the unsafe dereference, copy, indexing, free, arithmetic, or other
impact operation separately in `sink_before_line_numbers` when it is distinct.

## Review Quality

Use `semantic_review_status=reviewed` only after inspecting the parent source
and patch. Prefer two independent reviews for disputed multi-file cases and
keep an adjudication note. Score strict file/function/statement/sink metrics
only on reviewed rows. Use `patch_lines.csv` for the separate structural
patch-localization metric.
