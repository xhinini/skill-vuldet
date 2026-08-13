# Linux Vulnerability Skill Evaluation Dataset

This repository contains the public part of a 148-case Linux-kernel security-review benchmark covering June 2026 CVE cases.

The benchmark uses a **target-files plus repository-context** protocol:

- Each case provides every file modified by its fixing patch in `target_files`.
- The evaluated skill starts with those files.
- The complete before-fix Linux repository snapshot is available for headers, callers, callees, and related context.
- The model must not receive the patch, fixed files, CVE/CWE metadata, commit hashes, or private ground truth.

## Repository Layout

```text
public/cases.csv                         Public 148-case manifest
scripts/materialize_repository_case.py   Export one before-fix repository
scripts/prepare_all_target_files_eval_dataset.py
                                           Regenerate the all-target-file dataset
scripts/prepare_target_context_eval_dataset.py
                                           Compatibility wrapper for the current generator
docs/semantic_annotation_guide.md        Manual semantic-labeling guidance
```

The repository does not contain 148 full Linux source trees. Trees are materialized on demand from a local bare Git mirror.

## Public Manifest

`public/cases.csv` has three columns:

| Column | Meaning |
|---|---|
| `case_id` | Opaque identifier such as `case_001`. |
| `repository_local_path` | Where the runner should place the materialized tree. |
| `target_files` | Semicolon-separated paths of all files changed by the fix. |

Example:

```text
case_003,materialized/case_003,net/ipv4/inet_fragment.c;net/ipv4/ip_fragment.c
```

The public manifest intentionally does not contain CVE IDs, CWE IDs, commit hashes, patch paths, or fixed-file paths.

## Dataset Distribution

The 148 cases use the following June 2026 CWE strata:

| CWE | Cases |
|---|---:|
| CWE-416 | 46 |
| CWE-476 | 45 |
| CWE-401 | 22 |
| CWE-125 | 19 |
| CWE-787 | 16 |
| **Total** | **148** |

All 148 cases are positive CVE cases. This supports vulnerability-recall, CWE, and patch-localization measurements. Clean, manually reviewed negative controls are required for precision, specificity, and false-positive measurements.

## Required Local Inputs

The following evaluator-only inputs are intentionally not committed to this public repository:

- The private `ground_truth.csv` manifest containing parent and fixing hashes
- The downloaded patch and fixed-file artifacts
- The local Linux bare Git mirror
- Semantic-review annotations

Keep them in a separate operator-controlled directory. Do not mount them in the model environment.

The materializer expects a private manifest with at least these columns:

```text
case_id,parent_hash
```

The full private manifest also stores CVE/CWE labels, commit URLs, changed files, and artifact paths for post-run scoring.

## Materialize One Case

Run this from the repository root. Replace the placeholder paths with the operator-only locations:

```bash
python3 scripts/materialize_repository_case.py \
  --repo /path/to/linux-stable.git \
  --manifest /secure/evaluator/private/ground_truth.csv \
  --case-id case_003 \
  --output materialized/case_003
```

The command uses the private `parent_hash` to export the before-fix tree with `git archive`. The output is a clean source tree with no `.git` directory.

The runner then reads the public row for `case_003` and resolves:

```text
repository root: materialized/case_003
target files:    net/ipv4/inet_fragment.c
                 net/ipv4/ip_fragment.c
```

## Model Input

Give the evaluated skill only:

- The materialized repository directory
- The `target_files` paths from the public manifest
- The fixed evaluation prompt
- The selected skill

Use a prompt equivalent to:

```text
Review all target files listed below for security vulnerabilities:
<TARGET_FILES>

Inspect these target files first. You may inspect the supplied repository for
headers, callers, callees, types, macros, control flow, and data flow needed to
understand them. Do not use the internet, Git history, CVE databases, patches,
fixed files, private metadata, or anything outside the supplied repository and
selected skill. Report whether a vulnerability is present, the CWE, affected
functions, statements, and exact before-fix line numbers. Do not modify the
repository.
```

The model should run in an isolated directory or container. A prompt is not a complete access-control boundary; the private manifest and artifact store must be unavailable at the filesystem level.

## Evaluation Workflow

```text
private manifest + Git mirror
            |
            v
operator runner materializes parent snapshot
            |
            v
public target_files + clean repository
            |
            v
selected skill produces report
            |
            v
operator joins report with private ground truth
```

Recommended metrics:

1. Case detection: compare the reported vulnerability decision with the case-level label.
2. CWE classification: compare the reported CWE with the NVD label set, allowing multi-label set matches.
3. Patch localization: compare reported paths and before-fix lines with the private patch-line table.
4. Function and statement localization: use manually reviewed semantic labels, not unverified patch hunk guesses.

The model output should be stored using the opaque `case_id`, for example:

```text
results/case_003/<skill-name>.json
```

## Ground Truth Layers

The private evaluator package should keep these separate:

- **Case metadata:** case ID, CVE, CWE, parent hash, fixing hash, and repository information.
- **Structural patch ground truth:** exact changed paths, hunk ranges, deleted before-fix lines, and added after-fix lines.
- **Function-context candidates:** Git hunk-context suggestions that require verification.
- **Semantic ground truth:** manually reviewed root-cause files, functions, statements, and sink lines.

Not every changed line or changed file necessarily contains the vulnerability. Supporting headers, callers, API migrations, and cleanup must not automatically be treated as root-cause locations.

## Regenerating the Dataset

The all-target-file generator operates in the operator environment, where the original collection and private manifest are available:

```bash
python3 scripts/prepare_all_target_files_eval_dataset.py \
  --source-dataset /path/to/previous/sample \
  --artifact-root /path/to/collection \
  --output /path/to/new/sample
```

The generator creates the public manifest, structural patch tables, a function-context candidate table, and a semantic-review queue. The semantic queue must be manually reviewed before strict root-cause metrics are reported.

## Annotation

See [`docs/semantic_annotation_guide.md`](docs/semantic_annotation_guide.md) for the file-level root-cause review process. Semantic annotations remain private and must be frozen before model results are scored.
