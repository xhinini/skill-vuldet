# Linux Kernel Skill Evaluation

This repository is the runnable public package for the 148-case Linux-kernel
security-review benchmark covering June 1, 2026 through July 1, 2026.

The benchmark uses **target files plus repository context**:

- `public/cases.csv` gives the opaque case ID and every file changed by the fix.
- The runner materializes the complete Linux source tree at the parent commit,
  which is the before-fix repository snapshot.
- The selected Claude Code skill starts with the listed target files and may
  inspect other files in that supplied repository for context.
- CVE IDs, CWE labels, commit hashes, patches, fixed files, and private ground
  truth are not supplied to the model.

## Important Privacy Boundary

The GitHub repository is intentionally public. It contains the runner and the
public target-file manifest, but it does **not** contain the parent-hash map or
the Linux mirror. Publishing those would make the evaluation labels and source
construction details available to the model or to anyone inspecting the repo.

Only one small operator-only input is required on the server:

```text
case_id,parent_hash
```

This is the minimum information needed to materialize a case. Do not place it
inside a case workspace or pass it in the prompt. The full private
`ground_truth.csv`, patches, fixed files, and semantic annotations are not
needed by the batch runner and should remain in a separate evaluator-only
location.

## Repository Layout

```text
public/cases.csv                         Public 148-case manifest
configs/security-review.json            Example skill configuration
configs/security-review.prompt.txt      Example skill-specific prompt
configs/vuln-scan.json                   vuln-scan configuration
configs/vuln-scan.prompt.txt             vuln-scan prompt template
configs/gsd-security-review.json        GSD security-review configuration
configs/gsd-security-review.prompt.txt  GSD skill-specific prompt template
scripts/setup_server.sh                 Shared Linux-mirror setup
scripts/install_skill.py                 Install one sparse skill checkout
scripts/create_runner_manifest.py       Reduce private ground truth to 2 columns
scripts/materialize_repository_case.py  Export one parent-commit source tree
scripts/run_skill_batch.py              Run one or all cases and save trajectories
docs/semantic_annotation_guide.md       Private-labeling guidance
```

The five sampling strata are:

| CWE | Cases |
|---|---:|
| CWE-416 | 46 |
| CWE-476 | 45 |
| CWE-401 | 22 |
| CWE-125 | 19 |
| CWE-787 | 16 |
| **Total** | **148** |

All 148 cases are positive CVE cases. Negative controls are needed for
precision, specificity, and false-positive metrics.

## Server Prerequisites

The server needs:

- Git
- Python 3.10 or newer
- Claude Code installed and authenticated according to the server policy
- Network access during setup, so Git can download the Linux mirror and selected skills
- A firewall, container, or network namespace that blocks model-run internet
  access if strict no-internet evaluation is required

Claude Code credentials are never committed to this repository.

## One-Time Server Setup

Clone this repository on the server:

```bash
git clone https://github.com/xhinini/skill-vuldet.git
cd skill-vuldet
```

Run the bootstrap script once. The Linux mirror is a bare filtered mirror, not
148 working-tree clones. It is downloaded from the configured Linux repository
and provides the Git objects needed for parent-commit exports.

```bash
bash scripts/setup_server.sh \
  --mirror /srv/skill-vuldet-data/linux-stable.git
```

The default Linux source is:

```text
https://github.com/gregkh/linux.git
```

Plan for tens of gigabytes for the Linux mirror. The existing local mirror used
to prepare this dataset is about 19 GB; actual size depends on Git server and
Git version. The runner materializes only one case at a time and deletes that
temporary tree after the run unless `--keep-workspaces` is used.

## Install Skills

Install each skill into its own checkout. The repository, Git ref, and path are
skill-specific; use the full skill directory so its supporting reference files
are available too.

For the first skill:

```bash
python3 scripts/install_skill.py \
  --repository https://github.com/joe-bell/cva.git \
  --ref main \
  --source-path .agents/skills/security-review \
  --output /srv/skill-vuldet-data/skills/security-review-cva
```

For another skill, run the same installer with that skill's repository, ref,
source path, and a different output directory. Pin `--ref` to a commit or tag
when you need runs to be exactly reproducible. The installer records the
resolved commit in the run metadata later.

## Prepare the Operator Manifest

On a trusted machine that has the private dataset, or on the server after
securely transferring the private ground truth, create the minimal manifest:

```bash
python3 scripts/create_runner_manifest.py \
  --ground-truth /secure/evaluator/private/ground_truth.csv \
  --output /secure/evaluator/runner_manifest.csv
```

Keep `/secure/evaluator/runner_manifest.csv` outside the cloned repository if
possible. Transfer it to the server with the normal secure method. It contains
148 `case_id,parent_hash` rows and no CVE, CWE, patch, or fixed-file columns.

## Run One Case First

Use one case to verify authentication, mirror access, skill discovery, and
output capture:

```bash
python3 scripts/run_skill_batch.py \
  --repo-mirror /srv/skill-vuldet-data/linux-stable.git \
  --private-manifest /secure/evaluator/runner_manifest.csv \
  --skill-config configs/security-review.json \
  --skill-cache /srv/skill-vuldet-data/skills/security-review-cva \
  --output-root /srv/skill-vuldet-results \
  --work-root /srv/skill-vuldet-work \
  --case-id case_001
```

The example configuration's Claude tool allow-list is deliberately narrow:

```text
Read Grep Glob
```

This lets the skill read the target files and repository context without giving
it Bash, Task, network, or file-writing tools. The runner also uses:

```text
--bare --print --permission-mode dontAsk \
--output-format stream-json --no-session-persistence
```

`--bare` prevents user memory, `CLAUDE.md`, plugins, hooks, and normal settings
from entering the run. `--no-session-persistence` prevents session storage.
The selected skill is copied into the temporary repository under
`.claude/skills/<skill-name>`; this is the only non-kernel content added to the
model workspace.

## Run All 148 Cases

After the one-case check succeeds, omit `--case-id`:

```bash
python3 scripts/run_skill_batch.py \
  --repo-mirror /srv/skill-vuldet-data/linux-stable.git \
  --private-manifest /secure/evaluator/runner_manifest.csv \
  --skill-config configs/security-review.json \
  --skill-cache /srv/skill-vuldet-data/skills/security-review-cva \
  --output-root /srv/skill-vuldet-results \
  --work-root /srv/skill-vuldet-work
```

The runner reads target files automatically from `public/cases.csv`; no prompt
needs to be edited for each CVE. It materializes, runs, records, and removes
each case workspace sequentially. Existing cases with `run.json` are skipped,
so a stopped batch can be resumed. Use `--rerun` to run them again.

To run a small smoke test, use `--limit 3`. To preserve a source tree for
manual inspection, use `--keep-workspaces`; do this sparingly because a Linux
source tree is large.

## Run Another Skill

The Linux mirror, public manifest, private runner manifest, and case-selection
logic are shared across skills. Only the skill checkout, skill configuration,
prompt template, tool allow-list, and result directory change.

Create a JSON configuration for each additional skill, for example:

```json
{
  "skill_name": "my-skill",
  "skill_invocation": "/my-skill",
  "skill_repository": "https://github.com/example/skills.git",
  "skill_ref": "<pinned-commit-or-tag>",
  "skill_source_path": ".agents/skills/my-skill",
  "default_tools": ["Read", "Grep", "Glob"],
  "prompt_template": "my-skill.prompt.txt"
}
```

The prompt template is skill-specific and uses these placeholders:

```text
{SKILL_INVOCATION}
{TARGET_FILES}
{TARGET_FOCUS_ARGS}
```

Then install and run it:

```bash
python3 scripts/install_skill.py \
  --repository https://github.com/example/skills.git \
  --ref <pinned-commit-or-tag> \
  --source-path .agents/skills/my-skill \
  --output /srv/skill-vuldet-data/skills/my-skill

python3 scripts/run_skill_batch.py \
  --repo-mirror /srv/skill-vuldet-data/linux-stable.git \
  --private-manifest /secure/evaluator/runner_manifest.csv \
  --skill-config configs/my-skill.json \
  --skill-cache /srv/skill-vuldet-data/skills/my-skill \
  --output-root /srv/skill-vuldet-results \
  --work-root /srv/skill-vuldet-work
```

Results are automatically separated under
`/srv/skill-vuldet-results/my-skill/`. Run each skill independently over the
same 148 cases; trajectories from one skill are saved for analysis and are
never fed into another skill's run.

## Run `vuln-scan`

The Anthropic `vuln-scan` skill is directory-oriented. Its configuration passes
the materialized repository root (`.`) as the target directory and automatically
adds one `--focus <target-file>` argument for every file in the public manifest.
This skips broad repository reconnaissance while still allowing the skill to
read repository context for the selected files. The native skill also creates
`VULN-FINDINGS.json` and `VULN-FINDINGS.md`; the runner copies those files into
the per-case results directory before deleting the temporary workspace.

Install the skill:

```bash
python3 scripts/install_skill.py \
  --repository https://github.com/anthropics/defending-code-reference-harness.git \
  --ref d3bea6b5793b5f3d59a75ebe69a58efa88383145 \
  --source-path .claude/skills/vuln-scan \
  --output /srv/skill-vuldet-data/skills/vuln-scan
```

Run it over one case first:

```bash
python3 scripts/run_skill_batch.py \
  --repo-mirror /srv/skill-vuldet-data/linux-stable.git \
  --private-manifest /secure/evaluator/runner_manifest.csv \
  --skill-config configs/vuln-scan.json \
  --skill-cache /srv/skill-vuldet-data/skills/vuln-scan \
  --output-root /srv/skill-vuldet-results \
  --work-root /srv/skill-vuldet-work \
  --case-id case_001
```

The default allow-list for this configuration is `Read`, `Glob`, `Grep`,
`Write`, and `Task`. `Write` is present only because this skill requires its
two result files. The prompt forbids source edits and the result files are
copied out and then discarded with the temporary workspace. For a stronger
boundary, run the case inside a container or filesystem policy that allows
writes only to those two output paths and blocks network access.

This skill reports vulnerability categories such as `use-after-free` and
`heap-buffer-overflow`; those are not automatically equivalent to a CWE ID.
Use a separate evaluator mapping or add a carefully versioned post-processing
step before comparing its output with the dataset's CWE labels.

## Results

Each skill stores one directory per case:

```text
/srv/skill-vuldet-results/<skill-name>/case_001/
  prompt.txt           Exact generated prompt
  trajectory.jsonl     Raw Claude stream, one JSON event per line
  final.json            Extracted final result and last assistant event
  run.json              Run status and reproducibility metadata
  stderr.log            Claude CLI diagnostics
  materialize.log       Parent-tree export diagnostics
```

Trajectories are saved for later analysis and are not fed into later model
runs. The batch runner does not join results with private CVE/CWE labels. That
join belongs in a separate evaluator process after all runs finish.

The exact prompt is generated from the public target-file list and the
skill-specific prompt template. The security-review example is:

```text
/security-review

Review all target files listed below for security vulnerabilities:
- <target file 1>
- <target file 2>
...
```

The prompt permits repository-context inspection for types, macros, callers,
callees, control flow, and data flow. It explicitly excludes internet access,
Git history, patches, fixed files, CVE/CWE metadata, and private metadata.

## Run GSD `security-review`

The GSD skill is a Markdown-report skill. It uses the native STRIDE-oriented
report structure rather than the `vuln-scan` JSON/Markdown artifact pair. The
configuration keeps the installed skill directory named `security-review` so
the `/security-review` invocation resolves correctly, while using the distinct
result name `gsd-security-review` to keep its runs separate from the other
security-review skill.

Install the pinned GSD skill checkout:

```bash
python3 scripts/install_skill.py \
  --repository https://github.com/gsd-build/gsd-2.git \
  --ref 33c00aaffa56e5d394bccce1c8df59fb842e84c5 \
  --source-path src/resources/skills/security-review \
  --output /srv/skill-vuldet-data/skills/gsd-security-review
```

Run one case:

```bash
python3 scripts/run_skill_batch.py \
  --repo-mirror /srv/skill-vuldet-data/linux-stable.git \
  --private-manifest /secure/evaluator/runner_manifest.csv \
  --skill-config configs/gsd-security-review.json \
  --skill-cache /srv/skill-vuldet-data/skills/gsd-security-review \
  --output-root /srv/skill-vuldet-results \
  --work-root /srv/skill-vuldet-work \
  --case-id case_001
```

The configuration enables `Read`, `Grep`, `Glob`, and `Task` so the skill can
read repository context and use its suggested Explore-style subagent pass. It
does not enable Bash, Write, network tools, or outward-action tools. The prompt
preserves the skill's native STRIDE report while requesting optional CWE,
affected-function, and vulnerable-line fields for benchmark extraction.

## Tool Policy

Each skill configuration declares its own tool allow-list. Keep the default
read-only set unless the experiment explicitly requires broader capabilities.
For the security-review skill, the source declaration includes Bash and Task;
to enable them, use:

```bash
--tools Read Grep Glob Bash Task
```

Only do this inside a read-only, network-isolated container or equivalent
server boundary. A prompt is not an access-control mechanism by itself.

## Ground Truth and Scoring

The operator-side private package has separate layers:

- Case metadata: CVE/CWE labels, parent hash, fixing hash, and repository data.
- Structural patch data: changed paths, hunks, before-fix deleted lines, and
  after-fix added lines.
- Function-context candidates: hunk-context suggestions that still require
  verification.
- Semantic ground truth: manually reviewed vulnerable files, functions,
  statements, and root-cause lines.

Not every changed file or changed line is necessarily the vulnerability's root
cause. Use the semantic review queue before reporting strict function,
statement, or root-cause-line metrics. The public runner must never receive
these private tables.

Recommended evaluation joins reports to private labels by opaque `case_id`:

1. Vulnerability detection: reported finding versus the case-level label.
2. CWE classification: reported CWE versus the private CWE label set.
3. File and line localization: report versus reviewed root-cause files and
   before-fix lines.
4. Function and statement localization: report versus manually reviewed
   semantic annotations, not raw hunk context alone.

## Generating the Dataset

Dataset generation remains an operator-only task. The public generator can be
run where the original collection, artifacts, and private manifest are
available:

```bash
python3 scripts/prepare_all_target_files_eval_dataset.py \
  --source-dataset /path/to/previous/sample \
  --artifact-root /path/to/collection \
  --output /path/to/new/sample
```

It creates the public manifest, structural patch tables, function-context
candidates, and semantic-review queue. Do not commit the private output to this
public repository.

See [`docs/semantic_annotation_guide.md`](docs/semantic_annotation_guide.md) for
the manual semantic-labeling process.
