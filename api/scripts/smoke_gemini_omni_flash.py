#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from video_task_service.gemini_omni_flash import (
    GEMINI_OMNI_FLASH_DIMENSIONS,
    build_leonardo_gemini_omni_flash_request,
)
from video_task_service.h3 import ResolvedMedia
from video_task_service.pricing import PRICING_RULE_VERSION, quote_credit_cost
from video_task_service.schemas import TaskCreate

TERMINAL = {"COMPLETED", "FAILED", "CANCELLED"}
REFERENCE_URL = (
    "https://placehold.co/1024x1024/2563eb/ffffff.png"
    "?text=Gemini+Omni+Flash+Reference"
)


@dataclass(frozen=True)
class SmokeCase:
    name: str
    mode: str
    aspect_ratio: str
    duration: int

    @property
    def expected_dimensions(self) -> tuple[int, int]:
        return GEMINI_OMNI_FLASH_DIMENSIONS[self.aspect_ratio]

    @property
    def expected_credits(self) -> int:
        return self.duration * 100


LIVE_CASES = (
    SmokeCase("text-landscape-3s", "text-to-video", "16:9", 3),
    SmokeCase("reference-portrait-3s", "reference-to-video", "9:16", 3),
)


def all_contract_cases() -> list[SmokeCase]:
    return [
        SmokeCase(
            f"{mode}-{ratio.replace(':', '-')}-{duration}s",
            mode,
            ratio,
            duration,
        )
        for mode in ("text-to-video", "reference-to-video")
        for ratio in GEMINI_OMNI_FLASH_DIMENSIONS
        for duration in range(3, 11)
    ]


def payload(case: SmokeCase, reference_url: str = REFERENCE_URL) -> dict[str, Any]:
    task_input: dict[str, Any] = {
        "prompt": (
            "A cobalt-blue paper airplane glides above a quiet meadow at sunrise, "
            "gentle cinematic motion, no text."
        ),
        "duration": case.duration,
        "resolution": "720P",
        "aspect_ratio": case.aspect_ratio,
    }
    if case.mode == "reference-to-video":
        task_input["reference_image_urls"] = [reference_url]
    return {
        "provider": "leonardo",
        "task_type": "VIDEO_GENERATION",
        "model": "gemini-omni-flash",
        "mode": case.mode,
        "input": task_input,
    }


def resolved_assets(case: SmokeCase) -> list[ResolvedMedia]:
    if case.mode != "reference-to-video":
        return []
    return [
        ResolvedMedia(
            "IMAGE", "REFERENCE_IMAGE", 0, REFERENCE_URL, "contract-reference"
        )
    ]


def validate_contract(case: SmokeCase) -> dict[str, Any]:
    task = TaskCreate.model_validate(payload(case))
    document = task.input_document()
    request = build_leonardo_gemini_omni_flash_request(
        model=task.model,
        mode=task.mode or "",
        task_input=document,
        assets=resolved_assets(case),
    )
    parameters = request["parameters"]
    checks = {
        "dimensions": (parameters["width"], parameters["height"])
        == case.expected_dimensions,
        "estimated_credits": task.estimated_credit_cost == case.expected_credits,
        "direct_quote": quote_credit_cost(task.model, document)
        == case.expected_credits,
        "public_fixed": request["public"] is False,
        "quantity_fixed": parameters["quantity"] == 1,
        "legacy_mode_absent": "mode" not in parameters,
        "guidance": (
            "guidances" not in parameters
            if case.mode == "text-to-video"
            else len(parameters["guidances"]["image_reference"]) == 1
        ),
    }
    return {
        **asdict(case),
        "expected_dimensions": list(case.expected_dimensions),
        "expected_credits": case.expected_credits,
        "checks": checks,
        "passed": all(checks.values()),
    }


def api_request(
    base_url: str,
    api_key: str,
    method: str,
    path: str,
    *,
    body: dict[str, Any] | None = None,
    idempotency_key: str | None = None,
) -> tuple[int, Any]:
    data = None if body is None else json.dumps(body).encode()
    headers = {"Accept": "application/json", "X-API-Key": api_key}
    if data is not None:
        headers["Content-Type"] = "application/json"
    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key
    request = urllib.request.Request(
        base_url.rstrip("/") + path,
        data=data,
        headers=headers,
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        content = exc.read()
        return exc.code, json.loads(content) if content else None


def wait_for_terminal(
    base_url: str,
    api_key: str,
    task_uuid: str,
    timeout: float,
    poll_interval: float,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    deadline = time.monotonic() + timeout
    states: list[dict[str, Any]] = []
    previous: str | None = None
    while time.monotonic() < deadline:
        status, body = api_request(base_url, api_key, "GET", f"/v1/tasks/{task_uuid}")
        if status != 200:
            states.append({"http_status": status, "body": body})
            time.sleep(poll_interval)
            continue
        if body["status"] != previous:
            states.append(
                {
                    "at": datetime.now(UTC).isoformat(),
                    "status": body["status"],
                    "phase": body["progress"]["phase"],
                    "error_code": body.get("error_code"),
                }
            )
            previous = body["status"]
            print(f"{task_uuid} -> {body['status']}", flush=True)
        if body["status"] in TERMINAL:
            return body, states
        time.sleep(poll_interval)
    raise TimeoutError(f"task {task_uuid} did not finish in {timeout} seconds")


def download_and_probe(url: str, destination: Path) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 Chrome/150 Safari/537.36",
            "Referer": "https://app.leonardo.ai/",
        },
    )
    with urllib.request.urlopen(request, timeout=180) as response:
        content = response.read()
        content_type = response.headers.get_content_type()
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(content)
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "stream=codec_type,codec_name,width,height:format=duration",
            "-of",
            "json",
            str(destination),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    probe = json.loads(result.stdout)
    video = next(item for item in probe["streams"] if item["codec_type"] == "video")
    return {
        "path": str(destination),
        "bytes": len(content),
        "content_type": content_type,
        "width": video["width"],
        "height": video["height"],
        "codec": video["codec_name"],
        "duration": float(probe["format"]["duration"]),
        "audio_streams": sum(
            1 for item in probe["streams"] if item["codec_type"] == "audio"
        ),
    }


def run_live_case(
    case: SmokeCase,
    *,
    base_url: str,
    api_key: str,
    reference_url: str,
    output_dir: Path,
    run_id: str,
    timeout: float,
    poll_interval: float,
) -> dict[str, Any]:
    status, created = api_request(
        base_url,
        api_key,
        "POST",
        "/v1/tasks",
        body=payload(case, reference_url),
        idempotency_key=f"gemini-omni-{run_id}-{case.name}-{uuid4().hex[:8]}",
    )
    result: dict[str, Any] = {
        **asdict(case),
        "http_status": status,
        "created": created,
        "expected_dimensions": list(case.expected_dimensions),
        "expected_credits": case.expected_credits,
        "checks": {"accepted": status == 202},
        "passed": False,
    }
    if status != 202:
        return result
    terminal, states = wait_for_terminal(
        base_url, api_key, created["task_uuid"], timeout, poll_interval
    )
    result["states"] = states
    result["terminal"] = terminal
    result["checks"].update(
        {
            "schema": terminal["input_schema_version"] == "gemini-omni-flash.v1",
            "completed": terminal["status"] == "COMPLETED",
            "estimate": terminal["estimated_credit_cost"] == case.expected_credits,
            "actual_credits": terminal.get("actual_credit_cost")
            == case.expected_credits,
        }
    )
    media = ((terminal.get("output") or {}).get("media") or [])
    if terminal["status"] == "COMPLETED" and media:
        probe = download_and_probe(
            media[0]["url"], output_dir / f"{case.name}-{created['task_uuid']}.mp4"
        )
        result["probe"] = probe
        result["checks"].update(
            {
                "output_metadata_dimensions": (
                    media[0].get("width"),
                    media[0].get("height"),
                )
                == case.expected_dimensions,
                "file_dimensions": (probe["width"], probe["height"])
                == case.expected_dimensions,
                "mime": probe["content_type"] == "video/mp4",
                "duration": abs(probe["duration"] - case.duration) <= 1.5,
            }
        )
    result["passed"] = all(result["checks"].values())
    return result


def write_markdown(path: Path, report: dict[str, Any]) -> None:
    live = report.get("live") or []
    lines = [
        "# Gemini Omni Flash 真实冒烟测试报告",
        "",
        f"- 执行时间：`{report['generated_at']}`",
        f"- API：`{report.get('base_url', 'offline')}`",
        f"- 定价规则：`{report['pricing_rule_version']}`",
        f"- 契约矩阵：`{report['contract_passed']}/{report['contract_total']}` 通过",
        f"- 线上任务：`{sum(1 for item in live if item['passed'])}/{len(live)}` 通过",
        "",
        "| 用例 | 模式 | 比例 | 时长 | 预期积分 | 任务状态 | 实际积分 | 文件尺寸 | 结果 |",
        "| --- | --- | --- | ---: | ---: | --- | ---: | --- | --- |",
    ]
    for item in live:
        terminal = item.get("terminal") or {}
        probe = item.get("probe") or {}
        lines.append(
            "| {name} | {mode} | {ratio} | {duration} | {expected} | {status} | "
            "{actual} | {width}×{height} | {passed} |".format(
                name=item["name"],
                mode=item["mode"],
                ratio=item["aspect_ratio"],
                duration=item["duration"],
                expected=item["expected_credits"],
                status=terminal.get("status", "—"),
                actual=terminal.get("actual_credit_cost", "—"),
                width=probe.get("width", "—"),
                height=probe.get("height", "—"),
                passed="PASS" if item["passed"] else "FAIL",
            )
        )
    lines.extend(
        [
            "",
            (
                "每个真实任务均检查 HTTP 202、schema、终态、输入/输出尺寸、"
                "下载文件流尺寸、MIME、时长以及预估/实际积分。"
                "完整原始数据见同目录 JSON 报告。"
            ),
            "",
        ]
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--base-url", default="https://api-leo.clawsea.ai")
    parser.add_argument("--api-key", default=os.getenv("LEO_API_KEY"))
    parser.add_argument("--reference-url", default=REFERENCE_URL)
    parser.add_argument("--output-dir", type=Path, default=Path("artifacts/gemini-omni-flash"))
    parser.add_argument("--timeout", type=float, default=1200)
    parser.add_argument("--poll-interval", type=float, default=10)
    parser.add_argument("--max-credits", type=int, default=600)
    args = parser.parse_args()

    contracts = [validate_contract(case) for case in all_contract_cases()]
    report: dict[str, Any] = {
        "generated_at": datetime.now(UTC).isoformat(),
        "pricing_rule_version": PRICING_RULE_VERSION,
        "contract_total": len(contracts),
        "contract_passed": sum(1 for item in contracts if item["passed"]),
        "contracts": contracts,
        "live": [],
    }
    if args.live:
        if not args.api_key:
            print("--api-key or LEO_API_KEY is required for --live", file=sys.stderr)
            return 2
        budget = sum(case.expected_credits for case in LIVE_CASES)
        if budget > args.max_credits:
            print(f"live budget {budget} exceeds --max-credits {args.max_credits}", file=sys.stderr)
            return 2
        run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        report["base_url"] = args.base_url
        report["live_budget"] = budget
        report["live"] = [
            run_live_case(
                case,
                base_url=args.base_url,
                api_key=args.api_key,
                reference_url=args.reference_url,
                output_dir=args.output_dir,
                run_id=run_id,
                timeout=args.timeout,
                poll_interval=args.poll_interval,
            )
            for case in LIVE_CASES
        ]

    args.output_dir.mkdir(parents=True, exist_ok=True)
    json_path = args.output_dir / "gemini-omni-flash-smoke-report.json"
    md_path = args.output_dir / "gemini-omni-flash-smoke-report.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    write_markdown(md_path, report)
    print(json.dumps({"json": str(json_path), "markdown": str(md_path)}, indent=2))
    contract_ok = report["contract_passed"] == report["contract_total"]
    live_ok = not args.live or all(item["passed"] for item in report["live"])
    return 0 if contract_ok and live_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
