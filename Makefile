PYTHON ?= python
HF_DATASET ?= nebius/SWE-rebench-V2
HF_CONFIG ?= default
# Hugging Face dataset partition name. This is not model training.
DATASET_SPLIT ?= train
FROM_DATE ?= 2020-01-01
TO_DATE ?= $(shell $(PYTHON) -c "from datetime import date; print(date.today().isoformat())")
OPENROUTER_KEY ?= my_key
openrouter-key ?= $(OPENROUTER_KEY)
MODELS ?= z-ai/glm-5.1,moonshotai/kimi-k2.6,minimax/minimax-m2.7
models ?= $(MODELS)
EVAL_MAX_WORKERS ?= 1
OUTPUT_DIR ?= benchmark_runs
MAX_TASKS ?= 0
K ?= 5
AGENT_BACKEND ?= swe-agent
SWE_AGENT_COMMAND ?= sweagent

.PHONY: install run-from-date-dry-plan run-from-date-dry run-from-date-real run-last-k-dry-plan run-last-k-dry run-last-k-real

install:
	$(PYTHON) -m pip install -r requirements.txt

run-from-date-dry-plan:
	$(PYTHON) scripts/golang_benchmark.py \
		--hf-dataset $(HF_DATASET) \
		--hf-config $(HF_CONFIG) \
		--hf-split $(DATASET_SPLIT) \
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
		--hf-split $(DATASET_SPLIT) \
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
		--hf-split $(DATASET_SPLIT) \
		--from-date $(FROM_DATE) \
		--to-date $(TO_DATE) \
		--mode real \
		--max-tasks $(MAX_TASKS) \
		--agent-backend $(AGENT_BACKEND) \
		--swe-agent-command $(SWE_AGENT_COMMAND) \
		--openrouter-api-key $(openrouter-key) \
		--models $(models) \
		--eval-max-workers $(EVAL_MAX_WORKERS) \
		--output-dir $(OUTPUT_DIR)

run-last-k-dry-plan:
	$(PYTHON) scripts/golang_benchmark.py \
		--hf-dataset $(HF_DATASET) \
		--hf-config $(HF_CONFIG) \
		--hf-split $(DATASET_SPLIT) \
		--all-tasks \
		--last-k $(K) \
		--mode dry-run \
		--dry-run-details-limit 0 \
		--skip-eval \
		--output-dir $(OUTPUT_DIR)

run-last-k-dry:
	$(PYTHON) scripts/golang_benchmark.py \
		--hf-dataset $(HF_DATASET) \
		--hf-config $(HF_CONFIG) \
		--hf-split $(DATASET_SPLIT) \
		--all-tasks \
		--last-k $(K) \
		--mode dry-run \
		--dry-run-details-limit 0 \
		--eval-max-workers $(EVAL_MAX_WORKERS) \
		--output-dir $(OUTPUT_DIR)

run-last-k-real:
	$(PYTHON) scripts/golang_benchmark.py \
		--hf-dataset $(HF_DATASET) \
		--hf-config $(HF_CONFIG) \
		--hf-split $(DATASET_SPLIT) \
		--all-tasks \
		--last-k $(K) \
		--mode real \
		--agent-backend $(AGENT_BACKEND) \
		--swe-agent-command $(SWE_AGENT_COMMAND) \
		--openrouter-api-key $(openrouter-key) \
		--models $(models) \
		--eval-max-workers $(EVAL_MAX_WORKERS) \
		--output-dir $(OUTPUT_DIR)
