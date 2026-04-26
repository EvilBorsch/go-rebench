PYTHON ?= python
HF_DATASET ?= nebius/SWE-rebench-V2
HF_CONFIG ?= default
HF_SPLIT ?= train
FROM_DATE ?= 2026-01-01
TO_DATE ?= $(shell $(PYTHON) -c "from datetime import date; print(date.today().isoformat())")
OPENROUTER_KEY ?= my_key
openrouter-key ?= $(OPENROUTER_KEY)
MODELS ?= z-ai/glm-5.1,moonshotai/kimi-k2.6,minimax/minimax-m2.7
models ?= $(MODELS)
EVAL_MAX_WORKERS ?= 1
OUTPUT_DIR ?= benchmark_runs
MAX_TASKS ?= 0

.PHONY: install run-from-date-dry-plan run-from-date-dry run-from-date-real

install:
	$(PYTHON) -m pip install -r requirements.txt

run-from-date-dry-plan:
	$(PYTHON) scripts/golang_benchmark.py \
		--hf-dataset $(HF_DATASET) \
		--hf-config $(HF_CONFIG) \
		--hf-split $(HF_SPLIT) \
		--from-date $(FROM_DATE) \
		--to-date $(TO_DATE) \
		--mode dry-run \
		--max-tasks $(MAX_TASKS) \
		--dry-run-details-limit 0 \
		--skip-eval \
		--output-dir $(OUTPUT_DIR)

run-from-date-dry:
	$(PYTHON) scripts/golang_benchmark.py \
		--hf-dataset $(HF_DATASET) \
		--hf-config $(HF_CONFIG) \
		--hf-split $(HF_SPLIT) \
		--from-date $(FROM_DATE) \
		--to-date $(TO_DATE) \
		--mode dry-run \
		--max-tasks $(MAX_TASKS) \
		--dry-run-details-limit 0 \
		--eval-max-workers $(EVAL_MAX_WORKERS) \
		--output-dir $(OUTPUT_DIR)

run-from-date-real:
	$(PYTHON) scripts/golang_benchmark.py \
		--hf-dataset $(HF_DATASET) \
		--hf-config $(HF_CONFIG) \
		--hf-split $(HF_SPLIT) \
		--from-date $(FROM_DATE) \
		--to-date $(TO_DATE) \
		--mode real \
		--max-tasks $(MAX_TASKS) \
		--openrouter-api-key $(openrouter-key) \
		--models $(models) \
		--eval-max-workers $(EVAL_MAX_WORKERS) \
		--output-dir $(OUTPUT_DIR)
