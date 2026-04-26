#!/usr/bin/env python3
"""Build and run a Go-only SWE-rebench V2 benchmark."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import UTC, datetime, time
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import textwrap
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen


REPO_ROOT = Path(__file__).resolve().parents[1]
HF_ROWS_ENDPOINT = "https://datasets-server.huggingface.co/rows"
HF_PARQUET_ENDPOINT = "https://datasets-server.huggingface.co/parquet"
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_HF_DATASET = "nebius/SWE-rebench-V2"
GO_LANGUAGES = {"go", "golang"}


@dataclass
class TaskDecision:
    spec: dict[str, Any]
    created_at: datetime | None
    reasons: list[str]


class PrettyLog:
    def __init__(self, no_color: bool = False) -> None:
        self.no_color = no_color or bool(os.environ.get("NO_COLOR"))

    def color(self, text: str, code: str) -> str:
        if self.no_color:
            return text
        return f"\033[{code}m{text}\033[0m"

    def title(self, text: str) -> None:
        print()
        print(self.color("=" * 78, "36"))
        print(self.color(text.upper(), "1;36"))
        print(self.color("=" * 78, "36"))

    def section(self, text: str) -> None:
        print()
        print(self.color(f"-- {text}", "1;34"))

    def info(self, text: str) -> None:
        print(f"  {text}")

    def ok(self, text: str) -> None:
        print(self.color(f"  [ok] {text}", "32"))

    def warn(self, text: str) -> None:
        print(self.color(f"  [warn] {text}", "33"))

    def error(self, text: str) -> None:
        print(self.color(f"  [error] {text}", "31"))


def load_json_records(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict):
        data = [data]
    if not isinstance(data, list):
        raise ValueError(f"JSON root must be a list or object: {path}")
    records = []
    for idx, item in enumerate(data):
        if not isinstance(item, dict):
            raise ValueError(f"JSON item {idx} must be an object: {path}")
        records.append(item)
    return records


def cache_key(dataset: str, config: str, split: str) -> str:
    raw = f"{dataset}__{config}__{split}"
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", raw)


def load_jsonl_records(path: Path) -> list[dict[str, Any]]:
    records = []
    with path.open("r", encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, start=1):
            line = line.strip()
            if not line:
                continue
            item = json.loads(line)
            if not isinstance(item, dict):
                raise ValueError(f"JSONL line {line_no} must be an object: {path}")
            records.append(item)
    return records


def request_json(url: str) -> dict[str, Any]:
    req = Request(url, headers={"User-Agent": "swe-rebench-go-benchmark/1.0"})
    with urlopen(req, timeout=60) as resp:  # nosec: trusted HF endpoint
        return json.loads(resp.read().decode("utf-8"))


def fetch_hf_parquet_files(dataset: str, config: str, split: str) -> list[dict[str, Any]]:
    query = urlencode({"dataset": dataset})
    payload = request_json(f"{HF_PARQUET_ENDPOINT}?{query}")
    files = []
    for item in payload.get("parquet_files", []):
        if (
            isinstance(item, dict)
            and item.get("config") == config
            and item.get("split") == split
            and item.get("url")
        ):
            files.append(item)
    if not files:
        raise ValueError(f"No Parquet export found for {dataset}/{config}/{split}.")
    return files


def download_file(url: str, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    req = Request(url, headers={"User-Agent": "swe-rebench-go-benchmark/1.0"})
    with urlopen(req, timeout=120) as resp, tmp_path.open("wb") as out:  # nosec: trusted HF URL
        while True:
            chunk = resp.read(1024 * 1024)
            if not chunk:
                break
            out.write(chunk)
    tmp_path.replace(path)


def ensure_hf_dataset_cache(
    *,
    dataset: str,
    config: str,
    split: str,
    cache_dir: Path,
    refresh: bool,
    logger: PrettyLog,
) -> Path:
    key = cache_key(dataset, config, split)
    dataset_dir = cache_dir / key
    jsonl_path = dataset_dir / "rows.jsonl"
    meta_path = dataset_dir / "metadata.json"
    if jsonl_path.is_file() and not refresh:
        logger.ok(f"using cached dataset rows: {jsonl_path}")
        return jsonl_path

    try:
        import pyarrow.parquet as pq
    except ImportError as exc:
        raise RuntimeError("pyarrow is required to cache HF datasets. Run: pip install -r requirements.txt") from exc

    logger.info(f"building local dataset cache: {jsonl_path}")
    parquet_files = fetch_hf_parquet_files(dataset, config, split)
    parquet_dir = dataset_dir / "parquet"
    parquet_paths = []
    for item in parquet_files:
        filename = str(item.get("filename") or Path(str(item["url"])).name)
        parquet_path = parquet_dir / filename
        if refresh or not parquet_path.is_file():
            size = item.get("size")
            size_text = f" ({size / (1024 * 1024):.1f} MiB)" if isinstance(size, int) else ""
            logger.info(f"downloading {filename}{size_text}")
            download_file(str(item["url"]), parquet_path)
        else:
            logger.info(f"using cached parquet: {parquet_path}")
        parquet_paths.append(parquet_path)

    dataset_dir.mkdir(parents=True, exist_ok=True)
    tmp_jsonl = jsonl_path.with_suffix(".jsonl.tmp")
    total_rows = 0
    with tmp_jsonl.open("w", encoding="utf-8") as out:
        for parquet_path in parquet_paths:
            table = pq.read_table(parquet_path)
            rows = table.to_pylist()
            for row in rows:
                out.write(json.dumps(row, ensure_ascii=False) + "\n")
            total_rows += len(rows)
    tmp_jsonl.replace(jsonl_path)
    write_json(
        meta_path,
        {
            "dataset": dataset,
            "config": config,
            "split": split,
            "rows": total_rows,
            "parquet_files": parquet_files,
            "created_at": datetime.now(tz=UTC).isoformat().replace("+00:00", "Z"),
        },
    )
    logger.ok(f"cached {total_rows} rows locally")
    return jsonl_path


def fetch_hf_rows(
    dataset: str,
    config: str,
    split: str,
    offset: int,
    length: int,
) -> dict[str, Any]:
    query = urlencode(
        {
            "dataset": dataset,
            "config": config,
            "split": split,
            "offset": offset,
            "length": length,
        }
    )
    req = Request(
        f"{HF_ROWS_ENDPOINT}?{query}",
        headers={"User-Agent": "swe-rebench-go-benchmark/1.0"},
    )
    with urlopen(req, timeout=60) as resp:  # nosec: fixed trusted endpoint
        return json.loads(resp.read().decode("utf-8"))


def load_hf_records(
    dataset: str,
    config: str,
    split: str,
    offset: int,
    length: int,
) -> list[dict[str, Any]]:
    first_length = length if length > 0 else 100
    payload = fetch_hf_rows(dataset, config, split, offset, first_length)
    rows = payload.get("rows")
    if not isinstance(rows, list):
        raise ValueError("Hugging Face response did not include a rows list.")

    records = [item["row"] for item in rows if isinstance(item, dict) and isinstance(item.get("row"), dict)]
    if length > 0:
        return records

    total = payload.get("num_rows_total")
    per_page = payload.get("num_rows_per_page")
    if not isinstance(total, int) or not isinstance(per_page, int):
        return records

    loaded = len(records)
    while loaded < total:
        page = fetch_hf_rows(dataset, config, split, offset + loaded, per_page)
        page_rows = page.get("rows")
        if not isinstance(page_rows, list) or not page_rows:
            break
        records.extend(
            item["row"]
            for item in page_rows
            if isinstance(item, dict) and isinstance(item.get("row"), dict)
        )
        loaded = len(records)
    return records


def load_cached_hf_records(
    *,
    dataset: str,
    config: str,
    split: str,
    cache_dir: Path,
    refresh: bool,
    logger: PrettyLog,
) -> list[dict[str, Any]]:
    jsonl_path = ensure_hf_dataset_cache(
        dataset=dataset,
        config=config,
        split=split,
        cache_dir=cache_dir,
        refresh=refresh,
        logger=logger,
    )
    return load_jsonl_records(jsonl_path)


def parse_date(value: str | None, *, end_of_day: bool = False) -> datetime | None:
    if not value:
        return None
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            day = datetime.strptime(value, "%Y-%m-%d").date()
            clock = time.max if end_of_day else time.min
            return datetime.combine(day, clock, tzinfo=UTC)
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)
    except ValueError as exc:
        raise ValueError(f"Invalid date '{value}'. Use YYYY-MM-DD or ISO-8601.") from exc


def parse_created_at(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        timestamp = float(value)
        if timestamp > 10_000_000_000:
            timestamp /= 1000.0
        return datetime.fromtimestamp(timestamp, tz=UTC)
    if isinstance(value, str) and value.strip():
        return parse_date(value.strip())
    return None


def is_go_task(spec: dict[str, Any]) -> bool:
    return str(spec.get("language", "")).strip().lower() in GO_LANGUAGES


def select_tasks(
    specs: list[dict[str, Any]],
    *,
    from_date: datetime | None,
    to_date: datetime | None,
    all_tasks: bool,
    instance_ids: set[str],
    max_tasks: int,
) -> list[TaskDecision]:
    decisions: list[TaskDecision] = []
    for spec in specs:
        instance_id = str(spec.get("instance_id", ""))
        if instance_ids and instance_id not in instance_ids:
            continue
        if not is_go_task(spec):
            continue

        created_at = parse_created_at(spec.get("created_at"))
        reasons = ["language is Go"]
        if all_tasks:
            reasons.append("date filter disabled by --all-tasks")
        else:
            if created_at is None:
                continue
            if from_date and created_at < from_date:
                continue
            if to_date and created_at > to_date:
                continue
            reasons.append(
                "created_at is inside selected period "
                f"({created_at.isoformat().replace('+00:00', 'Z')})"
            )
        decisions.append(TaskDecision(spec=spec, created_at=created_at, reasons=reasons))
        if max_tasks > 0 and len(decisions) >= max_tasks:
            break
    return decisions


def parse_instance_ids(value: str) -> set[str]:
    return {part.strip() for part in value.split(",") if part.strip()}


def patch_files(patch: str) -> list[str]:
    files = []
    for line in patch.splitlines():
        match = re.match(r"diff --git a/(.*?) b/(.*?)$", line)
        if match:
            files.append(match.group(2))
    return files


def patch_excerpt(patch: str, max_lines: int = 14) -> str:
    lines = [line for line in patch.splitlines() if line.strip()]
    if not lines:
        return ""
    excerpt = "\n".join(lines[:max_lines])
    if len(lines) > max_lines:
        excerpt += f"\n... ({len(lines) - max_lines} more non-empty patch lines)"
    return excerpt


def summarize_text(value: Any, limit: int = 280) -> str:
    text = str(value or "").strip().replace("\r\n", "\n")
    text = re.sub(r"\s+", " ", text)
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def install_config(spec: dict[str, Any]) -> dict[str, Any]:
    value = spec.get("install_config")
    return value if isinstance(value, dict) else {}


def expected_tests(spec: dict[str, Any]) -> list[str]:
    pass_to_pass = spec.get("PASS_TO_PASS")
    fail_to_pass = spec.get("FAIL_TO_PASS")
    tests = []
    if isinstance(pass_to_pass, list):
        tests.extend(str(item) for item in pass_to_pass)
    if isinstance(fail_to_pass, list):
        tests.extend(str(item) for item in fail_to_pass)
    return sorted(tests)


def evaluation_rule(spec: dict[str, Any]) -> str:
    cfg = install_config(spec)
    parser = cfg.get("log_parser") or "<missing log parser>"
    expected_count = len(expected_tests(spec))
    return (
        "apply candidate patch + dataset test_patch in Docker; run "
        f"install_config.test_cmd; parse with {parser}; OK when parsed passed tests "
        f"exactly match PASS_TO_PASS + FAIL_TO_PASS ({expected_count} expected)"
    )


def validate_for_real_run(spec: dict[str, Any]) -> list[str]:
    missing = []
    for field in ("instance_id", "repo", "base_commit", "problem_statement", "test_patch"):
        if not spec.get(field):
            missing.append(field)
    cfg = install_config(spec)
    if not cfg.get("test_cmd"):
        missing.append("install_config.test_cmd")
    if not cfg.get("log_parser"):
        missing.append("install_config.log_parser")
    if not expected_tests(spec):
        missing.append("PASS_TO_PASS or FAIL_TO_PASS")
    return missing


def safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("_") or "model"


def build_prompt(spec: dict[str, Any], *, include_test_patch: bool) -> str:
    cfg = install_config(spec)
    parts = [
        "You are solving a SWE-rebench V2 Go task.",
        "Return only a unified git diff that can be applied with git apply.",
        "Do not include markdown fences or explanations.",
        "",
        f"Repository: {spec.get('repo')}",
        f"Base commit: {spec.get('base_commit')}",
        f"Instance id: {spec.get('instance_id')}",
        "",
        "Problem statement:",
        str(spec.get("problem_statement", "")).strip(),
    ]
    if spec.get("interface"):
        parts.extend(["", "Relevant interface notes:", str(spec.get("interface", "")).strip()])
    if spec.get("manual_instructions"):
        parts.extend(["", "Manual benchmark instructions:", str(spec.get("manual_instructions", "")).strip()])
    if spec.get("pr_description"):
        parts.extend(["", "PR description context:", str(spec.get("pr_description", "")).strip()])
    if cfg.get("test_cmd"):
        parts.extend(["", "Evaluation commands:", json.dumps(cfg.get("test_cmd"), ensure_ascii=False, indent=2)])
    if include_test_patch:
        parts.extend(["", "Test patch used by the benchmark:", str(spec.get("test_patch", "")).strip()])
    return "\n".join(parts).strip() + "\n"


def call_openrouter(
    *,
    model: str,
    prompt: str,
    api_key: str,
    base_url: str,
    temperature: float,
    max_tokens: int,
) -> dict[str, Any]:
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise RuntimeError("Install requirements first: pip install -r requirements.txt") from exc

    client = OpenAI(
        api_key=api_key,
        base_url=base_url.rstrip("/"),
        default_headers={
            "HTTP-Referer": "https://github.com/SWE-rebench/SWE-rebench-V2",
            "X-Title": "SWE-rebench V2 Go benchmark",
        },
    )
    completion = client.chat.completions.create(
        model=model,
        messages=[
            {
                "role": "system",
                "content": "You produce minimal, correct source code patches as unified git diffs.",
            },
            {"role": "user", "content": prompt},
        ],
        temperature=temperature,
        max_tokens=max_tokens,
    )
    content = ""
    if completion.choices and completion.choices[0].message:
        content = completion.choices[0].message.content or ""
    return {"raw": content, "response": completion.to_dict()}


def extract_patch(raw: str) -> str:
    fence = re.search(r"```(?:diff|patch)?\s*(.*?)```", raw, flags=re.DOTALL | re.IGNORECASE)
    text = fence.group(1) if fence else raw
    diff_start = text.find("diff --git ")
    if diff_start >= 0:
        return text[diff_start:].strip() + "\n"
    return text.strip() + ("\n" if text.strip() else "")


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def log_task_plan(logger: PrettyLog, decisions: list[TaskDecision], *, limit_details: int = 50) -> None:
    visible_decisions = decisions if limit_details <= 0 else decisions[:limit_details]
    for idx, decision in enumerate(visible_decisions, start=1):
        spec = decision.spec
        cfg = install_config(spec)
        logger.section(f"Task {idx}: {spec.get('instance_id')}")
        logger.info(f"repo: {spec.get('repo')}")
        logger.info(f"base_commit: {spec.get('base_commit')}")
        created = decision.created_at.isoformat().replace("+00:00", "Z") if decision.created_at else "unknown"
        logger.info(f"created_at: {created}")
        logger.info(f"selection: {'; '.join(decision.reasons)}")
        logger.info(f"PR: {spec.get('meta', {}).get('pr_url') if isinstance(spec.get('meta'), dict) else None}")
        logger.info(f"problem: {summarize_text(spec.get('problem_statement'))}")
        logger.info(f"test command: {cfg.get('test_cmd', '<missing>')}")
        logger.info(f"log parser: {cfg.get('log_parser', '<missing>')}")
        logger.info("LLM prompt context: problem_statement, interface, manual_instructions, PR description, test commands")
        files = patch_files(str(spec.get("patch", "")))
        logger.info(f"golden patch files: {', '.join(files[:8]) if files else '<not shown or missing>'}")
        if len(files) > 8:
            logger.info(f"golden patch files omitted: {len(files) - 8}")
        excerpt = patch_excerpt(str(spec.get("patch", "")))
        if excerpt:
            logger.info("golden patch preview:")
            print(textwrap.indent(excerpt, "    "))
        logger.info(f"decision rule: {evaluation_rule(spec)}")
        missing = validate_for_real_run(spec)
        if missing:
            logger.warn(f"real-run/eval missing fields: {', '.join(missing)}")
        else:
            logger.ok("real-run/eval fields are present")
    if limit_details > 0 and len(decisions) > limit_details:
        logger.warn(f"showing first {limit_details} of {len(decisions)} tasks")


def run_eval(
    *,
    selected_tasks_path: Path,
    patches_path: Path,
    report_path: Path,
    max_workers: int,
    image_registry: str,
    tag_prefix: str,
    logger: PrettyLog,
) -> int:
    cmd = [
        sys.executable,
        str(REPO_ROOT / "scripts" / "eval.py"),
        "--json",
        str(selected_tasks_path),
        "--patches",
        str(patches_path),
        "--report-json",
        str(report_path),
        "--max-workers",
        str(max_workers),
    ]
    if image_registry:
        cmd.extend(["--image-registry", image_registry])
    if tag_prefix:
        cmd.extend(["--tag-prefix", tag_prefix])

    logger.info("running evaluator: " + " ".join(cmd))
    result = subprocess.run(cmd, check=False, text=True, cwd=REPO_ROOT)
    return result.returncode


def log_eval_report_summary(report_path: Path, logger: PrettyLog) -> None:
    if not report_path.is_file():
        logger.warn(f"eval report not found: {report_path}")
        return
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warn(f"could not read eval report {report_path}: {exc}")
        return
    items = report.get("items", [])
    if not isinstance(items, list):
        logger.warn(f"eval report has unexpected shape: {report_path}")
        return
    errors = [item for item in items if item.get("error")]
    passed = [item for item in items if item.get("passed_match") is True and not item.get("error")]
    mismatches = [
        item
        for item in items
        if not item.get("error") and item.get("passed_match") is not True
    ]
    logger.info(
        f"eval summary: total {len(items)} | passed {len(passed)} | "
        f"mismatch {len(mismatches)} | error {len(errors)}"
    )
    for item in errors[:3]:
        logger.error(f"{item.get('instance_id')}: {summarize_text(item.get('error'), 220)}")
        if item.get("log_path"):
            logger.info(f"log: {item.get('log_path')}")
    for item in mismatches[:3]:
        missing = item.get("failed_from_pass_to_pass") or []
        logger.warn(
            f"{item.get('instance_id')}: test mismatch; "
            f"{len(missing)} PASS_TO_PASS tests missing"
        )
        if item.get("log_path"):
            logger.info(f"log: {item.get('log_path')}")


def build_mock_patches(
    selected: list[dict[str, Any]],
    *,
    source: str,
) -> list[dict[str, Any]]:
    patches = []
    for spec in selected:
        instance_id = str(spec.get("instance_id", ""))
        if source == "empty":
            patch = ""
        else:
            patch = str(spec.get("patch", ""))
        patches.append(
            {
                "instance_id": instance_id,
                "patch": patch,
                "model": f"mock-{source}",
                "mock": True,
                "mock_source": source,
            }
        )
    return patches


def write_manual_template(path: Path) -> None:
    payload = [
        {
            "instance_id": "manual__owner-repo-001",
            "language": "go",
            "repo": "owner/repo",
            "base_commit": "0123456789abcdef0123456789abcdef01234567",
            "created_at": "2026-01-15T00:00:00Z",
            "problem_statement": "Describe the bug or feature request the model must solve.",
            "manual_instructions": "Extra instructions only for this manually-added task.",
            "interface": "Optional API/interface notes the model can use.",
            "image_name": "docker.io/your-org/owner-repo:manual-001",
            "install_config": {
                "test_cmd": ["go test ./..."],
                "log_parser": "parse_log_gotest",
            },
            "PASS_TO_PASS": [],
            "FAIL_TO_PASS": ["TestNameThatShouldPassAfterFix"],
            "test_patch": "diff --git a/example_test.go b/example_test.go\n--- a/example_test.go\n+++ b/example_test.go\n@@ -1 +1,2 @@\n package example\n+// Add benchmark tests here.\n",
        }
    ]
    write_json(path, payload)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create a Go-only benchmark slice from SWE-rebench V2 and run it on OpenRouter models.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--json", help="Local SWE-rebench JSON file.")
    source.add_argument("--hf-dataset", help="Hugging Face dataset id.")
    parser.add_argument("--hf-config", default="default")
    parser.add_argument("--hf-split", default="train")
    parser.add_argument("--hf-offset", type=int, default=0)
    parser.add_argument("--hf-length", type=int, default=0, help="0 loads all available rows.")
    parser.add_argument("--dataset-cache-dir", default="datasets_cache")
    parser.add_argument("--refresh-dataset-cache", action="store_true")
    parser.add_argument("--no-dataset-cache", action="store_true", help="Use HF rows API directly instead of local cache.")
    parser.add_argument("--manual-json", action="append", default=[], help="Extra manual task JSON file. Can be repeated.")
    parser.add_argument("--write-manual-template", help="Write a manual task template JSON and exit.")

    parser.add_argument("--from-date", help="Inclusive UTC start date, YYYY-MM-DD or ISO-8601.")
    parser.add_argument("--to-date", help="Inclusive UTC end date, YYYY-MM-DD or ISO-8601.")
    parser.add_argument("--all-tasks", action="store_true", help="Use all Go tasks and ignore date filters.")
    parser.add_argument("--instance-ids", default="", help="Comma-separated instance ids.")
    parser.add_argument("--max-tasks", type=int, default=0, help="0 means no limit.")
    parser.add_argument(
        "--dry-run-details-limit",
        type=int,
        default=50,
        help="Number of selected tasks to print in dry-run logs; 0 prints all tasks.",
    )

    parser.add_argument("--mode", choices=["dry-run", "real"], default="dry-run")
    parser.add_argument("--models", default="", help="Comma-separated OpenRouter model ids for --mode real.")
    parser.add_argument("--openrouter-api-key", default=os.environ.get("OPENROUTER_API_KEY", ""))
    parser.add_argument("--openrouter-base-url", default=OPENROUTER_BASE_URL)
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--max-tokens", type=int, default=12000)
    parser.add_argument("--include-test-patch-in-prompt", action="store_true")
    parser.add_argument("--skip-eval", action="store_true", help="Generate patches but do not run Docker eval.")
    parser.add_argument(
        "--mock-patch-source",
        choices=["golden", "empty"],
        default="golden",
        help="Patch source used by dry-run's mocked LLM.",
    )
    parser.add_argument("--eval-max-workers", type=int, default=1)
    parser.add_argument("--image-registry", default="")
    parser.add_argument("--tag-prefix", default="")

    parser.add_argument("--output-dir", default="benchmark_runs")
    parser.add_argument("--no-color", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    logger = PrettyLog(no_color=args.no_color)

    if args.write_manual_template:
        path = Path(args.write_manual_template)
        write_manual_template(path)
        logger.ok(f"manual task template written: {path}")
        return 0

    if not args.json and not args.hf_dataset and not args.manual_json:
        args.hf_dataset = DEFAULT_HF_DATASET
        logger.warn(f"no dataset source supplied; defaulting to --hf-dataset {DEFAULT_HF_DATASET}")
    if args.hf_offset < 0 or args.hf_length < 0:
        logger.error("--hf-offset and --hf-length must be >= 0")
        return 2
    if args.max_tasks < 0:
        logger.error("--max-tasks must be >= 0")
        return 2

    try:
        from_date = parse_date(args.from_date)
        to_date = parse_date(args.to_date, end_of_day=True)
    except ValueError as exc:
        logger.error(str(exc))
        return 2
    if not args.all_tasks and not from_date and not to_date:
        logger.warn("no date filter supplied; using all Go tasks. Pass --all-tasks to make this explicit.")
        args.all_tasks = True
    if from_date and to_date and from_date > to_date:
        logger.error("--from-date must be before --to-date")
        return 2

    logger.title("SWE-rebench V2 Go benchmark")
    specs: list[dict[str, Any]] = []
    try:
        if args.json:
            path = Path(args.json)
            logger.info(f"loading local dataset: {path}")
            specs.extend(load_json_records(path))
        if args.hf_dataset:
            logger.info(f"loading Hugging Face dataset: {args.hf_dataset}")
            if args.no_dataset_cache:
                hf_records = load_hf_records(
                    args.hf_dataset,
                    args.hf_config,
                    args.hf_split,
                    args.hf_offset,
                    args.hf_length,
                )
            else:
                hf_records = load_cached_hf_records(
                    dataset=args.hf_dataset,
                    config=args.hf_config,
                    split=args.hf_split,
                    cache_dir=Path(args.dataset_cache_dir),
                    refresh=args.refresh_dataset_cache,
                    logger=logger,
                )
                if args.hf_offset:
                    hf_records = hf_records[args.hf_offset :]
                if args.hf_length > 0:
                    hf_records = hf_records[: args.hf_length]
            specs.extend(hf_records)
        for manual in args.manual_json:
            path = Path(manual)
            logger.info(f"loading manual tasks: {path}")
            specs.extend(load_json_records(path))
    except Exception as exc:
        logger.error(f"failed to load tasks: {exc}")
        return 1

    wanted_ids = parse_instance_ids(args.instance_ids)
    decisions = select_tasks(
        specs,
        from_date=from_date,
        to_date=to_date,
        all_tasks=args.all_tasks,
        instance_ids=wanted_ids,
        max_tasks=args.max_tasks,
    )
    selected = [decision.spec for decision in decisions]

    logger.section("Selection summary")
    logger.info(f"input tasks: {len(specs)}")
    logger.info(f"selected Go tasks: {len(selected)}")
    if not args.all_tasks:
        logger.info(
            "date window: "
            f"{from_date.isoformat().replace('+00:00', 'Z') if from_date else '-inf'} -> "
            f"{to_date.isoformat().replace('+00:00', 'Z') if to_date else '+inf'}"
        )
    if not selected:
        logger.warn("no Go tasks matched the selected filters")

    run_id = datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%SZ")
    output_dir = Path(args.output_dir) / run_id
    selected_tasks_path = output_dir / "selected_go_tasks.json"
    write_json(selected_tasks_path, selected)
    logger.ok(f"selected tasks written: {selected_tasks_path}")

    if args.mode == "dry-run":
        logger.title("Dry run plan")
        log_task_plan(logger, decisions, limit_details=args.dry_run_details_limit)
        patches_path = output_dir / "mock_llm_patches.json"
        report_path = output_dir / "dry_run_eval_report.json"
        plan = {
            "mode": "dry-run",
            "created_at": run_id,
            "mock_patch_source": args.mock_patch_source,
            "selected_count": len(selected),
            "selected_task_file": str(selected_tasks_path),
            "mock_patches_file": str(patches_path),
            "eval_report_file": str(report_path),
            "tasks": [
                {
                    "instance_id": spec.get("instance_id"),
                    "repo": spec.get("repo"),
                    "base_commit": spec.get("base_commit"),
                    "created_at": decision.created_at.isoformat().replace("+00:00", "Z") if decision.created_at else None,
                    "selection_reasons": decision.reasons,
                    "evaluation_rule": evaluation_rule(spec),
                    "golden_patch_files": patch_files(str(spec.get("patch", ""))),
                    "golden_patch_excerpt": patch_excerpt(str(spec.get("patch", ""))),
                    "llm_prompt_context": [
                        "problem_statement",
                        "interface",
                        "manual_instructions",
                        "pr_description",
                        "install_config.test_cmd",
                    ],
                    "missing_for_real_run": validate_for_real_run(spec),
                }
                for decision in decisions
                for spec in [decision.spec]
            ],
        }
        plan_path = output_dir / "dry_run_plan.json"
        write_json(plan_path, plan)
        logger.ok(f"dry run plan written: {plan_path}")

        logger.title("Dry run mocked LLM")
        mock_patches = build_mock_patches(selected, source=args.mock_patch_source)
        write_json(patches_path, mock_patches)
        logger.ok(f"mock LLM patches written: {patches_path}")
        if args.mock_patch_source == "golden":
            logger.info("mock LLM is returning each task's dataset golden patch")
        else:
            logger.info("mock LLM is returning empty patches")

        invalid = {str(spec.get("instance_id")): validate_for_real_run(spec) for spec in selected}
        invalid = {key: value for key, value in invalid.items() if value}
        if invalid and not args.skip_eval:
            for instance_id, missing in invalid.items():
                logger.error(f"{instance_id} missing fields for dry-run eval: {', '.join(missing)}")
            return 1

        if args.skip_eval:
            logger.warn("skipping Docker eval by request; dry run only verified selection and mocked patch generation")
            return 0
        if not selected:
            logger.warn("no selected tasks to evaluate")
            return 0

        logger.title("Dry run evaluator")
        eval_returncode = run_eval(
            selected_tasks_path=selected_tasks_path,
            patches_path=patches_path,
            report_path=report_path,
            max_workers=args.eval_max_workers,
            image_registry=args.image_registry,
            tag_prefix=args.tag_prefix,
            logger=logger,
        )
        if eval_returncode == 0:
            logger.ok("dry-run evaluator passed with mocked LLM patches")
        else:
            logger.warn(f"dry-run evaluator returned {eval_returncode}; see {report_path}")
        log_eval_report_summary(report_path, logger)
        return eval_returncode

    models = [model.strip() for model in args.models.split(",") if model.strip()]
    if not models:
        logger.error("--models is required in --mode real")
        return 2
    if not args.openrouter_api_key:
        logger.error("OpenRouter API key missing. Set OPENROUTER_API_KEY or pass --openrouter-api-key.")
        return 2

    invalid = {str(spec.get("instance_id")): validate_for_real_run(spec) for spec in selected}
    invalid = {key: value for key, value in invalid.items() if value}
    if invalid and not args.skip_eval:
        for instance_id, missing in invalid.items():
            logger.error(f"{instance_id} missing fields for real eval: {', '.join(missing)}")
        return 1

    logger.title("Real run")
    run_summary: dict[str, Any] = {
        "mode": "real",
        "created_at": run_id,
        "selected_task_file": str(selected_tasks_path),
        "models": [],
    }

    for model in models:
        logger.section(f"Model: {model}")
        patches = []
        raw_dir = output_dir / "raw_responses" / safe_name(model)
        for idx, spec in enumerate(selected, start=1):
            instance_id = str(spec.get("instance_id"))
            logger.info(f"[{idx}/{len(selected)}] requesting patch for {instance_id}")
            prompt = build_prompt(spec, include_test_patch=args.include_test_patch_in_prompt)
            try:
                response = call_openrouter(
                    model=model,
                    prompt=prompt,
                    api_key=args.openrouter_api_key,
                    base_url=args.openrouter_base_url,
                    temperature=args.temperature,
                    max_tokens=args.max_tokens,
                )
                patch = extract_patch(response["raw"])
                raw_path = raw_dir / f"{safe_name(instance_id)}.json"
                write_json(raw_path, response)
                patches.append(
                    {
                        "instance_id": instance_id,
                        "patch": patch,
                        "model": model,
                        "raw_response_path": str(raw_path),
                    }
                )
                files = patch_files(patch)
                if files:
                    logger.ok(f"patch received; files: {', '.join(files[:6])}")
                else:
                    logger.warn("model response did not contain a diff --git patch")
            except Exception as exc:
                logger.error(f"OpenRouter request failed for {instance_id}: {exc}")
                patches.append({"instance_id": instance_id, "patch": "", "model": model, "error": str(exc)})

        model_name = safe_name(model)
        patches_path = output_dir / f"{model_name}_patches.json"
        report_path = output_dir / f"{model_name}_eval_report.json"
        write_json(patches_path, patches)
        logger.ok(f"patches written: {patches_path}")

        eval_returncode = None
        if args.skip_eval:
            logger.warn("skipping Docker eval by request")
        elif selected:
            eval_returncode = run_eval(
                selected_tasks_path=selected_tasks_path,
                patches_path=patches_path,
                report_path=report_path,
                max_workers=args.eval_max_workers,
                image_registry=args.image_registry,
                tag_prefix=args.tag_prefix,
                logger=logger,
            )
            if eval_returncode == 0:
                logger.ok(f"evaluation passed for {model}")
            else:
                logger.warn(f"evaluation returned {eval_returncode} for {model}; see {report_path}")
            log_eval_report_summary(report_path, logger)

        run_summary["models"].append(
            {
                "model": model,
                "patches_path": str(patches_path),
                "eval_report_path": str(report_path) if eval_returncode is not None else "",
                "eval_returncode": eval_returncode,
            }
        )

    summary_path = output_dir / "run_summary.json"
    write_json(summary_path, run_summary)
    logger.ok(f"run summary written: {summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
