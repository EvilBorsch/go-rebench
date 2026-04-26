# SWE-rebench-v2 builder

Tools and prompt templates used to build and evaluate SWE-rebench-v2 tasks for the paper. This repo covers:

- **Labeling prompts** used for annotations (PR description, meta info, interfaces, log parser tasks)
- **Dockerfile generation** (base images + per-task instance images)
- **Task evaluation** using generated instance images and log parsers

## How to Run Eval with Existing Docker Images

1. Using a local sample JSON for debugging:

```bash
python3 scripts/eval.py \
  --json sample.json \
  --max-workers 1 \
  --golden-eval \
  --report-json eval_report.json
```

2. Using the 20-task sample from Hugging Face:

```bash
python3 scripts/eval.py \
  --hf-dataset ibragim-bad/SWE-rebench-V2-sample \
  --hf-config default \
  --hf-split train \
  --max-workers 8 \
  --golden-eval \
  --report-json eval_report.json
```

## Repository layout

- `prompts/annotations/` — main labeling prompts (Jinja templates)
  - `pr_description.j2` — PR description generation prompt
  - `meta_info.j2` — meta info classification prompt
  - `interfaces.j2` — interface extraction prompt
- `prompts/installer/` — builder/installer prompts
  - `log_parser.j2` — log parser synthesis prompt
  - `base_dockerfile.j2` — base image generation prompt
  - `agent.j2` — installer agent prompt
- `prompts/issue_clarity_ablation/` — issue clarity ablation variants
- `combine.Dockerfile.j2` — Jinja template for per-task instance Dockerfiles
- `scripts/annotation_script.py` — render prompts from JSON to `prompt` / `meta_prompt`
- `scripts/build_base_images.py` — build base Docker images from `base_dockerfiles/`
- `scripts/build_instance_images.py` — render + build per-task images
- `scripts/eval.py` — run tasks in Docker and parse test logs
- `lib/agent/log_parsers.py` — parsers referenced by tasks
- `sample.json` — example task list
- `issue_based_tasks_sample.json` — issue-based task samples
- `pr_based_tasks_sample.json` — PR-based task samples

## Requirements

- Python 3.10+ recommended
- Docker
- Python deps:

```bash
pip install -r requirements.txt
```

Note: `openai` is required for the `--send` mode in `scripts/annotation_script.py`.

## Labeling prompt generation

Render both PR description prompt and meta prompt into a single JSON:

```bash
python3 scripts/annotation_script.py \
  --input sample.json \
  --prompt-template prompts/annotations/pr_description.j2 \
  --meta-template prompts/annotations/meta_info.j2 \
  --output rendered_prompts.json
```

The output keeps original fields and adds:
- `prompt` — rendered PR description prompt
- `meta_prompt` — rendered meta info prompt

Optional: send rendered prompts to OpenAI API and store responses:

```bash
python3 scripts/annotation_script.py \
  --input sample.json \
  --prompt-template prompts/annotations/pr_description.j2 \
  --meta-template prompts/annotations/meta_info.j2 \
  --output rendered_prompts.json \
  --send \
  --field both \
  --model gpt-4.1-mini \
  --api-base https://api.openai.com/v1 \
  --api-key $OPENAI_API_KEY
```

Additional output fields when `--send` is used:
- `prompt_response` — OpenAI response (includes `raw`, `error`, and full response payload)
- `meta_prompt_response` — same for `meta_prompt` when `--field meta_prompt` or `--field both`

## Build base Docker images

Use the prebuilt Dockerfiles under `base_dockerfiles/`:

```bash
python3 scripts/build_base_images.py --dockerfiles-dir base_dockerfiles
```

## Build per-task Docker images

Render and build instance images for all tasks in `sample.json`:

```bash
python3 scripts/build_instance_images.py \
  --json sample.json \
  --template combine.Dockerfile.j2 \
  --output-dir dockerfiles
```

Optional flags:
- `--base-image-registry` for base image registry prefix
- `--image-registry` and `--tag-prefix` for instance image tags

## Run evaluation on all tasks (sample.json)

This runs each task with some predicted patches in its instance Docker image, applies patches, runs tests, and parses logs:

```bash
python3 scripts/eval.py --json sample.json --patches patches.json
```

Notes:
- Instance image tag is derived from each `instance_id`.
- Ensure you built the instance images first (see above).
- Logs are saved as `<instance_id>_log.txt` in the repo root.

## Build a Go-only benchmark slice

Use `scripts/golang_benchmark.py` to select only Go tasks, inspect them in dry-run
mode, or run models through OpenRouter and then evaluate generated patches with
the existing Docker evaluator.

Dry run from the SWE-rebench V2 Hugging Face dataset:

```bash
python3 scripts/golang_benchmark.py \
  --hf-dataset nebius/SWE-rebench-V2 \
  --all-tasks \
  --mode dry-run \
  --max-tasks 5
```

Dry run from the SWE-rebench V2 Hugging Face dataset for a date window:

```bash
python3 scripts/golang_benchmark.py \
  --hf-dataset nebius/SWE-rebench-V2 \
  --hf-config default \
  --hf-split train \
  --from-date 2024-01-01 \
  --to-date 2026-12-31 \
  --mode dry-run
```

On first run, install dependencies:

```bash
python3 -m pip install -r requirements.txt
```

On Windows PowerShell, use `python` instead of `python3` if that is how Python
is installed:

```powershell
python -m pip install -r requirements.txt
```

### Makefile shortcuts

The Makefile includes four convenience commands:

```bash
make install
make run-from-date-dry-plan
make run-from-date-dry
make run-from-date-real openrouter-key=my_key
```

All date-window commands use Go tasks from `2026-01-01` through today by
default. The real run target uses these OpenRouter models by default:

```text
z-ai/glm-5.1,moonshotai/kimi-k2.6,minimax/minimax-m2.7
```

To override the key or models:

```bash
make run-from-date-real \
  openrouter-key=sk-or-v1-your-key \
  models=z-ai/glm-5.1,moonshotai/kimi-k2.6,minimax/minimax-m2.7
```

To test with only a few tasks before running the whole date window, add
`MAX_TASKS=5` to any run target.

After `make run-from-date-dry-plan`, you should see the dataset cache status,
a selection summary, and a printed block for every selected Go task. Each task
block shows the instance id, repo, base commit, created date, problem summary,
test command, log parser, golden patch files, and the pass/fail decision rule.
This command adds `--skip-eval`, so it is the fastest way to inspect all tasks
the LLM would be benchmarked on.

If the selected January 2026-to-today window has no Go tasks, the summary will
say `selected Go tasks: 0`. To smoke-test with older tasks, override the date:

```bash
make run-from-date-dry-plan FROM_DATE=2020-01-01 MAX_TASKS=5
```

After `make run-from-date-dry`, you should see the same full task list, then a
`DRY RUN MOCKED LLM` section and a `DRY RUN EVALUATOR` section. This mocks only
the LLM response with golden patches and runs the real Docker evaluator. If
Docker is healthy, the run writes `dry_run_eval_report.json`; if Docker or an
image fails, the report shows that as an error instead of hiding it as a model
failure.

After `make run-from-date-real openrouter-key=my_key`, you should see a section
for each OpenRouter model, patch requests for every selected task, patch files
written under `benchmark_runs/<timestamp>/`, and evaluator summaries per model.
Use a real OpenRouter key instead of `my_key`.

Dry run with real dataset/cache/evaluation and mocked LLM output:

```bash
python3 scripts/golang_benchmark.py \
  --hf-dataset nebius/SWE-rebench-V2 \
  --all-tasks \
  --mode dry-run \
  --max-tasks 5 \
  --eval-max-workers 1
```

Dry-run mode mocks only the LLM response. By default it writes
`mock_llm_patches.json` from each task's golden patch, then runs the same Docker
evaluator as real mode. Docker must be running and able to use the task images.
For a faster selection/logging-only check, add `--skip-eval`.

Real run on OpenRouter models. Put your OpenRouter key in the
`OPENROUTER_API_KEY` environment variable, and write the model list in
`--models` as comma-separated OpenRouter model ids:

```bash
export OPENROUTER_API_KEY=...
python3 scripts/golang_benchmark.py \
  --hf-dataset nebius/SWE-rebench-V2 \
  --all-tasks \
  --mode real \
  --models openai/gpt-4.1-mini,anthropic/claude-3.7-sonnet \
  --eval-max-workers 2
```

Windows PowerShell equivalent:

```powershell
$env:OPENROUTER_API_KEY = "sk-or-v1-..."
python scripts\golang_benchmark.py `
  --hf-dataset nebius/SWE-rebench-V2 `
  --all-tasks `
  --mode real `
  --models openai/gpt-4.1-mini,anthropic/claude-3.7-sonnet `
  --eval-max-workers 2
```

Change the `--models` value to benchmark different OpenRouter models, for
example `google/gemini-2.5-pro`, `deepseek/deepseek-chat`, or any other model id
available in your OpenRouter account.

The script writes each run under `benchmark_runs/<timestamp>/`, including:

- `selected_go_tasks.json` - the filtered Go task list.
- `dry_run_plan.json` - dry-run task decisions and evaluation rules.
- `<model>_patches.json` - generated patches for `scripts/eval.py`.
- `<model>_eval_report.json` - evaluator results, when eval is not skipped.
- `run_summary.json` - per-model output paths and eval return codes.

For Hugging Face datasets, the first run downloads the dataset Parquet export
into `datasets_cache/` and writes a local `rows.jsonl` cache. Later runs reuse
that local cache unless `--refresh-dataset-cache` is passed, so filtering by
period, language, or instance id does not repeatedly hit the network.

Dry-run mode does not call any LLMs. It verifies dataset selection, patch
handoff, image/test execution, log parsing, and result reporting while replacing
only the LLM call. Use `--mock-patch-source empty` to check expected failure
handling.

Real-run mode asks each selected OpenRouter model for a unified diff and then
evaluates that candidate patch by applying it with the task `test_patch`,
running `install_config.test_cmd`, parsing logs with
`install_config.log_parser`, and checking that parsed passed tests match
`PASS_TO_PASS + FAIL_TO_PASS`.

### Manually add Go tasks

Create a starter manual task file:

```bash
python3 scripts/golang_benchmark.py \
  --write-manual-template my_manual_go_tasks.json
```

Then include it in a dry or real run:

```bash
python3 scripts/golang_benchmark.py \
  --manual-json my_manual_go_tasks.json \
  --all-tasks \
  --mode dry-run
```

Manual tasks use the same core schema as dataset tasks. For real evaluation,
include `language: "go"`, `repo`, `base_commit`, `problem_statement`,
`test_patch`, `install_config.test_cmd`, `install_config.log_parser`,
`PASS_TO_PASS` or `FAIL_TO_PASS`, and either `image_name` or a locally
resolvable instance image tag.

## Prompts used for labeling

Primary prompts live in `prompts/annotations/`:
- `pr_description.j2` — PR description generation
- `meta_info.j2` — meta classification (A/B codes + difficulty)
- `interfaces.j2` — interface extraction tied to tests

Installer/build prompts live in `prompts/installer/`:
- `log_parser.j2` — log parser synthesis
- `base_dockerfile.j2` — base image generation
- `agent.j2` — installer agent prompt

Issue clarity ablations are under `prompts/issue_clarity_ablation/`:
- `verified.j2`, `verified_plus.j2`, `verified_extra.j2`, `spice.j2`, `rebench_v1.j2`

# Citation

```bibtex
@misc{badertdinov2026swerebenchv2languageagnosticswe,
      title={SWE-rebench V2: Language-Agnostic SWE Task Collection at Scale}, 
      author={Ibragim Badertdinov and Maksim Nekrashevich and Anton Shevtsov and Alexander Golubev},
      year={2026},
      eprint={2602.23866},
      archivePrefix={arXiv},
      primaryClass={cs.SE},
      url={https://arxiv.org/abs/2602.23866}, 
}
