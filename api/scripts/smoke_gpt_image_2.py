#!/usr/bin/env python3
from __future__ import annotations

import argparse
import io
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from PIL import Image
from pydantic import ValidationError

from video_task_service.gpt_image_2 import (
    GPT_IMAGE_2_DIMENSIONS,
    GPT_IMAGE_2_NONE_STYLE_ID,
    build_leonardo_gpt_image_2_request,
)
from video_task_service.h3 import ResolvedMedia
from video_task_service.pricing import (
    GPT_IMAGE_2_CREDIT_TABLE,
    PRICING_RULE_VERSION,
    quote_credit_cost,
)
from video_task_service.schemas import TaskCreate

QUALITIES = ("LOW", "MEDIUM", "HIGH")
SIZES = ("SMALL", "MEDIUM", "LARGE")
ASPECT_RATIOS = (
    "21:9", "16:9", "3:2", "4:3", "5:4", "1:1", "4:5", "3:4", "2:3", "9:16"
)
TERMINAL = {"COMPLETED", "FAILED", "CANCELLED"}
REFERENCE_URL = (
    "https://placehold.co/1024x1024/2563eb/ffffff.png"
    "?text=GPT+Image+2+Smoke+Reference"
)


@dataclass(frozen=True)
class SmokeCase:
    name: str
    mode: str
    quality: str
    aspect_ratio: str
    size: str

    @property
    def expected_dimensions(self) -> tuple[int, int]:
        return GPT_IMAGE_2_DIMENSIONS[self.aspect_ratio][self.size]

    @property
    def expected_resolution(self) -> str:
        width, height = self.expected_dimensions
        return f"{width}x{height}"

    @property
    def expected_credits(self) -> int:
        return GPT_IMAGE_2_CREDIT_TABLE[self.quality][self.aspect_ratio][
            SIZES.index(self.size)
        ]


LIVE_CASES = (
    SmokeCase("t2i-low-small-square", "text-to-image", "LOW", "1:1", "SMALL"),
    SmokeCase("t2i-medium-medium-wide", "text-to-image", "MEDIUM", "16:9", "MEDIUM"),
    SmokeCase("t2i-high-small-portrait", "text-to-image", "HIGH", "2:3", "SMALL"),
    SmokeCase("t2i-low-large-ultrawide", "text-to-image", "LOW", "21:9", "LARGE"),
    SmokeCase("t2i-low-small-classic", "text-to-image", "LOW", "4:3", "SMALL"),
    SmokeCase("i2i-low-small-landscape", "image-to-image", "LOW", "3:2", "SMALL"),
    SmokeCase("i2i-medium-medium-portrait", "image-to-image", "MEDIUM", "4:5", "MEDIUM"),
    SmokeCase("i2i-high-small-vertical", "image-to-image", "HIGH", "9:16", "SMALL"),
    SmokeCase("i2i-low-large-landscape", "image-to-image", "LOW", "5:4", "LARGE"),
    SmokeCase("i2i-low-small-classic-portrait", "image-to-image", "LOW", "3:4", "SMALL"),
)


def all_contract_cases() -> list[SmokeCase]:
    return [
        SmokeCase(
            f"{mode}-{quality.lower()}-{ratio.replace(':', '-')}-{size.lower()}",
            mode,
            quality,
            ratio,
            size,
        )
        for mode in ("text-to-image", "image-to-image")
        for quality in QUALITIES
        for ratio in ASPECT_RATIOS
        for size in SIZES
    ]


def task_payload(case: SmokeCase, reference_url: str = REFERENCE_URL) -> dict[str, Any]:
    task_input: dict[str, Any] = {
        "prompt": (
            "A cobalt-blue paper airplane centered in a clean studio composition, "
            f"smoke case {case.name}."
        ),
        "quality": case.quality,
        "aspect_ratio": case.aspect_ratio,
        "size": case.size,
    }
    if case.mode == "image-to-image":
        task_input["reference_image_urls"] = [reference_url]
    return {
        "provider": "leonardo",
        "task_type": "IMAGE_GENERATION",
        "model": "gpt-image-2",
        "mode": case.mode,
        "input": task_input,
    }


def resolved_assets(case: SmokeCase) -> list[ResolvedMedia]:
    if case.mode == "text-to-image":
        return []
    return [
        ResolvedMedia(
            "IMAGE",
            "REFERENCE_IMAGE",
            0,
            REFERENCE_URL,
            "contract-reference-image",
        )
    ]


def validate_contract_case(case: SmokeCase) -> dict[str, Any]:
    task = TaskCreate.model_validate(task_payload(case))
    document = task.input_document()
    request = build_leonardo_gpt_image_2_request(
        model=task.model,
        mode=task.mode or "",
        task_input=document,
        assets=resolved_assets(case),
    )
    parameters = request["parameters"]
    checks = {
        "input_resolution": document["resolution"] == case.expected_resolution,
        "request_dimensions": (
            parameters["width"],
            parameters["height"],
        )
        == case.expected_dimensions,
        "estimated_credits": task.estimated_credit_cost == case.expected_credits,
        "direct_quote": quote_credit_cost(task.model, document) == case.expected_credits,
        "quantity_fixed": parameters["quantity"] == 1,
        "prompt_enhance_fixed": parameters["prompt_enhance"] == "OFF",
        "style_fixed": parameters["style_ids"] == [GPT_IMAGE_2_NONE_STYLE_ID],
        "reference_guidance": (
            "guidances" not in parameters
            if case.mode == "text-to-image"
            else len(parameters["guidances"]["image_reference"]) == 1
        ),
    }
    return {
        **asdict(case),
        "expected_resolution": case.expected_resolution,
        "expected_credits": case.expected_credits,
        "checks": checks,
        "passed": all(checks.values()),
    }


def validate_fixed_field_contracts() -> list[dict[str, Any]]:
    cases: list[tuple[str, Any]] = [
        ("prompt_enhance", "AUTO"),
        ("style", "Dynamic"),
        ("style_ids", ["override"]),
        ("quantity", 2),
        ("resolution", "2048x2048"),
    ]
    results = []
    for field, value in cases:
        payload = task_payload(LIVE_CASES[0])
        payload["input"][field] = value
        try:
            TaskCreate.model_validate(payload)
            results.append({"field": field, "passed": False, "error": None})
        except ValidationError as exc:
            results.append(
                {"field": field, "passed": True, "error": str(exc).splitlines()[0]}
            )
    return results


def api_request(
    base_url: str,
    api_key: str,
    method: str,
    path: str,
    *,
    payload: dict[str, Any] | None = None,
    idempotency_key: str | None = None,
) -> tuple[int, Any]:
    data = None if payload is None else json.dumps(payload).encode()
    headers = {"Accept": "application/json", "X-API-Key": api_key}
    if data is not None:
        headers["Content-Type"] = "application/json"
    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key
    request = urllib.request.Request(
        f"{base_url.rstrip('/')}{path}", data=data, headers=headers, method=method
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            content = response.read()
            return response.status, json.loads(content) if content else None
    except urllib.error.HTTPError as exc:
        content = exc.read()
        return exc.code, json.loads(content) if content else None


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def wait_for_terminal(
    base_url: str,
    api_key: str,
    task_uuid: str,
    poll_interval: float,
    timeout: float,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    deadline = time.monotonic() + timeout
    states: list[dict[str, Any]] = []
    previous: tuple[Any, ...] | None = None
    while time.monotonic() < deadline:
        status, body = api_request(base_url, api_key, "GET", f"/v1/tasks/{task_uuid}")
        if status != 200:
            states.append({"http_status": status, "body": body})
            time.sleep(poll_interval)
            continue
        signature = (
            body.get("status"),
            (body.get("progress") or {}).get("phase"),
            body.get("error_code"),
        )
        if signature != previous:
            states.append(
                {
                    "observed_at": datetime.now(UTC).isoformat(),
                    "status": signature[0],
                    "phase": signature[1],
                    "error_code": signature[2],
                }
            )
            previous = signature
            print(
                f"task={task_uuid} status={signature[0]} phase={signature[1]} "
                f"error={signature[2]}",
                flush=True,
            )
        if str(body.get("status")).upper() in TERMINAL:
            return body, states
        time.sleep(poll_interval)
    raise TimeoutError(f"task {task_uuid} timed out after {timeout} seconds")


def inspect_download(url: str, destination: Path) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 Chrome/150 Safari/537.36"
            ),
            "Referer": "https://app.leonardo.ai/",
        },
    )
    with urllib.request.urlopen(request, timeout=180) as response:
        content = response.read()
        content_type = response.headers.get_content_type()
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(content)
    with Image.open(io.BytesIO(content)) as image:
        width, height = image.size
        image_format = image.format
    return {
        "path": str(destination),
        "bytes": len(content),
        "content_type": content_type,
        "image_format": image_format,
        "width": width,
        "height": height,
    }


def run_live_case(
    case: SmokeCase,
    *,
    base_url: str,
    api_key: str,
    reference_url: str,
    run_id: str,
    output_dir: Path,
    poll_interval: float,
    timeout: float,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        **asdict(case),
        "expected_resolution": case.expected_resolution,
        "expected_credits": case.expected_credits,
        "checks": {},
        "passed": False,
    }
    status, created = api_request(
        base_url,
        api_key,
        "POST",
        "/v1/tasks",
        payload=task_payload(case, reference_url),
        idempotency_key=f"gpt-image-2-smoke-{run_id}-{case.name}",
    )
    write_json(
        output_dir / "responses" / f"{case.name}-create.json",
        {"http_status": status, "body": created},
    )
    result["create_http_status"] = status
    if status != 202:
        result["error"] = f"create returned HTTP {status}"
        return result
    task_uuid = created["task_uuid"]
    result["task_uuid"] = task_uuid
    result["checks"]["create_input_resolution"] = (
        created["input"]["resolution"] == case.expected_resolution
    )
    result["checks"]["create_estimated_credits"] = (
        created["estimated_credit_cost"] == case.expected_credits
    )
    try:
        final, states = wait_for_terminal(
            base_url, api_key, task_uuid, poll_interval, timeout
        )
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
        return result
    write_json(output_dir / "responses" / f"{case.name}-final.json", final)
    write_json(output_dir / "responses" / f"{case.name}-states.json", states)
    result.update(
        {
            "status": final.get("status"),
            "upstream_task_id": final.get("upstream_task_id"),
            "estimated_credit_cost": final.get("estimated_credit_cost"),
            "reserved_credit_cost": final.get("reserved_credit_cost"),
            "actual_credit_cost": final.get("actual_credit_cost"),
        }
    )
    checks = result["checks"]
    checks["completed"] = final.get("status") == "COMPLETED"
    checks["final_input_resolution"] = (
        (final.get("input") or {}).get("resolution") == case.expected_resolution
    )
    checks["final_estimated_credits"] = (
        final.get("estimated_credit_cost") == case.expected_credits
    )
    checks["final_reserved_credits"] = (
        final.get("reserved_credit_cost") == case.expected_credits
    )
    checks["final_actual_credits"] = (
        final.get("actual_credit_cost") == case.expected_credits
    )
    media = ((final.get("output") or {}).get("media") or [])
    result["media_count"] = len(media)
    checks["one_output"] = len(media) == 1
    if len(media) == 1:
        item = media[0]
        result["output_media"] = {
            key: item.get(key) for key in ("id", "type", "width", "height", "url")
        }
        checks["metadata_dimensions"] = (
            item.get("width"),
            item.get("height"),
        ) == case.expected_dimensions
        suffix = Path(urllib.parse.urlsplit(item["url"]).path).suffix or ".image"
        try:
            download = inspect_download(
                item["url"], output_dir / "media" / f"{case.name}{suffix}"
            )
            result["download"] = download
            checks["download_dimensions"] = (
                download["width"],
                download["height"],
            ) == case.expected_dimensions
            checks["download_mime"] = item.get("type") == download["content_type"]
        except Exception as exc:
            result["download_error"] = f"{type(exc).__name__}: {exc}"
            checks["download_dimensions"] = False
            checks["download_mime"] = False
    result["passed"] = all(checks.values())
    return result


def coverage_summary(cases: tuple[SmokeCase, ...]) -> dict[str, list[str]]:
    text = [case for case in cases if case.mode == "text-to-image"]
    image = [case for case in cases if case.mode == "image-to-image"]
    return {
        "text_qualities": sorted({case.quality for case in text}),
        "text_sizes": sorted({case.size for case in text}),
        "image_qualities": sorted({case.quality for case in image}),
        "image_sizes": sorted({case.size for case in image}),
        "aspect_ratios": sorted({case.aspect_ratio for case in cases}),
    }


def markdown_report(report: dict[str, Any]) -> str:
    lines = [
        "# GPT Image 2 完整冒泡测试报告",
        "",
        f"- Run ID：`{report['run_id']}`",
        f"- 时间：`{report['started_at']}` → `{report['finished_at']}`",
        f"- API：`{report['base_url']}`",
        f"- 定价版本：`{report['pricing_rule_version']}`",
        f"- 总结果：**{report['result']}**",
        "",
        "## 1. 契约矩阵",
        "",
        (
            f"验证 `{report['contract']['total']}` 个组合：2 模式 × 3 质量 × "
            "10 比例 × 3 Size。"
        ),
        f"通过 `{report['contract']['passed']}`，失败 `{report['contract']['failed']}`。",
        "",
        "| 固定/冲突字段 | 预期 | 结果 |",
        "| --- | --- | --- |",
    ]
    for item in report["fixed_field_contracts"]:
        outcome = "PASS" if item["passed"] else "FAIL"
        lines.append(f"| `{item['field']}` | 422/校验错误 | {outcome} |")
    lines.extend(
        [
            "",
            "## 2. 生产真实任务矩阵",
            "",
            f"计划积分：`{report['live']['planned_credits']}`；"
            f"实际积分：`{report['live']['actual_credits']}`。",
            "",
            (
                "| 案例 | 模式 | 质量 | 比例 | Size | 输入分辨率 | 输出元数据 | "
                "下载实测 | 预估/预留/实际 | 状态 | 结果 |"
            ),
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
    )
    for item in report["live"]["cases"]:
        media = item.get("output_media") or {}
        download = item.get("download") or {}
        credits = "/".join(
            str(item.get(key))
            for key in (
                "estimated_credit_cost",
                "reserved_credit_cost",
                "actual_credit_cost",
            )
        )
        outcome = "PASS" if item.get("passed") else "FAIL"
        lines.append(
            f"| `{item['name']}` | `{item['mode']}` | `{item['quality']}` | "
            f"`{item['aspect_ratio']}` | `{item['size']}` | "
            f"`{item['expected_resolution']}` | "
            f"`{media.get('width')}x{media.get('height')}` | "
            f"`{download.get('width')}x{download.get('height')}` | `{credits}` | "
            f"`{item.get('status')}` | {outcome} |"
        )
    lines.extend(
        [
            "",
            "## 3. 覆盖与检查项",
            "",
            f"- 文生图质量：`{', '.join(report['coverage']['text_qualities'])}`",
            f"- 文生图 Size：`{', '.join(report['coverage']['text_sizes'])}`",
            f"- 图生图质量：`{', '.join(report['coverage']['image_qualities'])}`",
            f"- 图生图 Size：`{', '.join(report['coverage']['image_sizes'])}`",
            f"- 真实任务比例：`{', '.join(report['coverage']['aspect_ratios'])}`",
            "- 逐任务核对输入分辨率、输出元数据、下载像素、MIME、预估/预留/实际积分。",
            "",
            "## 4. 证据",
            "",
            "- `report.json`：结构化结果",
            "- `responses/`：创建、状态和最终响应",
            "- `media/`：下载的真实输出图片",
            "",
        ]
    )
    if report["failures"]:
        lines.extend(["## 5. 失败项", ""])
        lines.extend(f"- `{item}`" for item in report["failures"])
        lines.append("")
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="GPT Image 2 contract/live smoke test")
    parser.add_argument("--base-url", default="http://127.0.0.1:18080")
    parser.add_argument("--api-key", default=os.getenv("VIDEO_SERVICE_API_AUTH_KEY", ""))
    parser.add_argument("--reference-url", default=REFERENCE_URL)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--max-credits", type=int, default=1100)
    parser.add_argument("--poll-interval", type=float, default=5)
    parser.add_argument("--timeout", type=float, default=900)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    started = datetime.now(UTC)
    run_id = f"{started.strftime('%Y%m%dT%H%M%SZ')}-{uuid4().hex[:8]}"
    output_dir = args.output_dir or Path("artifacts/gpt-image-2-smoke") / run_id
    output_dir.mkdir(parents=True, exist_ok=True)
    contract_results = [validate_contract_case(case) for case in all_contract_cases()]
    fixed_results = validate_fixed_field_contracts()
    contract_failed = [item["name"] for item in contract_results if not item["passed"]]
    fixed_failed = [item["field"] for item in fixed_results if not item["passed"]]
    planned = sum(case.expected_credits for case in LIVE_CASES) if args.live else 0
    if args.live and not args.api_key:
        raise SystemExit("--live requires --api-key or VIDEO_SERVICE_API_AUTH_KEY")
    if planned > args.max_credits:
        raise SystemExit(f"planned credits {planned} exceed limit {args.max_credits}")
    invalid_live = None
    live_results: list[dict[str, Any]] = []
    if args.live:
        payload = task_payload(LIVE_CASES[0])
        payload["input"]["quantity"] = 2
        status, body = api_request(
            args.base_url,
            args.api_key,
            "POST",
            "/v1/tasks",
            payload=payload,
            idempotency_key=f"gpt-image-2-smoke-{run_id}-invalid",
        )
        invalid_live = {"http_status": status, "body": body, "passed": status == 422}
        write_json(output_dir / "responses" / "invalid-quantity.json", invalid_live)
        print(f"invalid_quantity_http={status}", flush=True)
        for index, case in enumerate(LIVE_CASES, start=1):
            print(
                f"live_case={index}/{len(LIVE_CASES)} name={case.name} "
                f"planned_credits={case.expected_credits}",
                flush=True,
            )
            live_results.append(
                run_live_case(
                    case,
                    base_url=args.base_url,
                    api_key=args.api_key,
                    reference_url=args.reference_url,
                    run_id=run_id,
                    output_dir=output_dir,
                    poll_interval=args.poll_interval,
                    timeout=args.timeout,
                )
            )
    live_failed = [item["name"] for item in live_results if not item.get("passed")]
    if invalid_live is not None and not invalid_live["passed"]:
        live_failed.append("invalid-quantity-http-422")
    failures = [
        *[f"contract:{item}" for item in contract_failed],
        *[f"fixed-field:{item}" for item in fixed_failed],
        *[f"live:{item}" for item in live_failed],
    ]
    report = {
        "run_id": run_id,
        "started_at": started.isoformat(),
        "finished_at": datetime.now(UTC).isoformat(),
        "base_url": args.base_url,
        "pricing_rule_version": PRICING_RULE_VERSION,
        "result": "PASS" if not failures else "FAIL",
        "contract": {
            "total": len(contract_results),
            "passed": len(contract_results) - len(contract_failed),
            "failed": len(contract_failed),
            "cases": contract_results,
        },
        "fixed_field_contracts": fixed_results,
        "invalid_live_request": invalid_live,
        "live": {
            "enabled": args.live,
            "planned_credits": planned,
            "actual_credits": sum(
                int(item.get("actual_credit_cost") or 0) for item in live_results
            ),
            "cases": live_results,
        },
        "coverage": coverage_summary(LIVE_CASES),
        "failures": failures,
    }
    write_json(output_dir / "report.json", report)
    (output_dir / "report.md").write_text(markdown_report(report))
    print(f"report_json={output_dir / 'report.json'}")
    print(f"report_md={output_dir / 'report.md'}")
    print(f"result={report['result']}")
    print(f"planned_credits={planned}")
    print(f"actual_credits={report['live']['actual_credits']}")
    return 0 if report["result"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
