#!/usr/bin/env python3
from __future__ import annotations

import argparse
import itertools
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from video_task_service.pricing import PRICING_RULE_VERSION, quote_credit_cost
from video_task_service.schemas import TaskCreate
from video_task_service.seed_audio import (
    SEED_AUDIO_DEFAULT_VOICE_ID,
    SEED_AUDIO_MODEL,
    build_leonardo_seed_audio_request,
)

TERMINAL = {"COMPLETED", "FAILED", "CANCELLED"}


def payload(
    *,
    speed: float = 1.0,
    volume: float = 1.0,
    pitch: int = 0,
    quantity: int = 1,
) -> dict[str, Any]:
    return {
        "provider": "leonardo",
        "task_type": "AUDIO_GENERATION",
        "model": SEED_AUDIO_MODEL,
        "mode": "text-to-speech",
        "input": {
            "prompt": "欢迎使用 FRAME OPS。Seed Audio 一点零真实任务测试完成。",
            "voice_id": SEED_AUDIO_DEFAULT_VOICE_ID,
            "speed": speed,
            "volume": volume,
            "pitch": pitch,
            "quantity": quantity,
        },
    }


def contract_matrix() -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    for speed, volume, pitch, quantity in itertools.product(
        (0.5, 1.0, 2.0),
        (0.5, 1.0, 2.0),
        (-12, 0, 12),
        (1, 2, 3, 4),
    ):
        task = TaskCreate.model_validate(
            payload(speed=speed, volume=volume, pitch=pitch, quantity=quantity)
        )
        request = build_leonardo_seed_audio_request(
            model=task.model,
            mode=task.mode or "",
            task_input=task.input_document(),
        )
        expected = 350 * quantity
        checks = {
            "private": request["public"] is False,
            "controls": request["parameters"]
            == {
                "prompt": task.input_document()["prompt"],
                "voice_id": SEED_AUDIO_DEFAULT_VOICE_ID,
                "speed": speed,
                "volume": volume,
                "pitch": pitch,
                "quantity": quantity,
            },
            "estimated_credits": task.estimated_credit_cost == expected,
            "direct_quote": quote_credit_cost(task.model, task.input_document())
            == expected,
        }
        cases.append(
            {
                "speed": speed,
                "volume": volume,
                "pitch": pitch,
                "quantity": quantity,
                "expected_credits": expected,
                "checks": checks,
                "passed": all(checks.values()),
            }
        )
    return cases


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
        if not content:
            return exc.code, None
        try:
            return exc.code, json.loads(content)
        except json.JSONDecodeError:
            return exc.code, {"raw": content.decode(errors="replace")[:1000]}


def wait_for_terminal(
    base_url: str,
    api_key: str,
    task_uuid: str,
    timeout: float,
    poll_interval: float,
) -> tuple[dict[str, Any], list[str]]:
    deadline = time.monotonic() + timeout
    states: list[str] = []
    while time.monotonic() < deadline:
        status, body = api_request(base_url, api_key, "GET", f"/v1/tasks/{task_uuid}")
        if status == 200:
            if not states or states[-1] != body["status"]:
                states.append(body["status"])
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
    completed = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "stream=codec_type,codec_name,sample_rate,channels:format=duration,format_name",
            "-of",
            "json",
            str(destination),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    probe = json.loads(completed.stdout)
    audio = next(item for item in probe["streams"] if item["codec_type"] == "audio")
    return {
        "path": str(destination),
        "bytes": len(content),
        "content_type": content_type,
        "codec": audio.get("codec_name"),
        "sample_rate": int(audio["sample_rate"]),
        "channels": audio.get("channels"),
        "duration": float(probe["format"]["duration"]),
        "format": probe["format"].get("format_name"),
    }


def write_markdown(path: Path, report: dict[str, Any]) -> None:
    live = report.get("live") or {}
    terminal = live.get("terminal") or {}
    probe = live.get("probe") or {}
    lines = [
        "# Seed Audio 1.0 冒烟测试报告",
        "",
        f"- 执行时间：`{report['generated_at']}`",
        f"- API：`{report.get('base_url', 'offline')}`",
        f"- 定价规则：`{report['pricing_rule_version']}`",
        f"- 契约矩阵：`{report['contract_passed']}/{report['contract_total']}` 通过",
        f"- 真实任务：`{'PASS' if live.get('passed') else '未执行或失败'}`",
        "",
        "| 任务 UUID | 上游 ID | 状态 | 预估/预留/实际积分 | MIME | 编码 | 采样率 | 时长 |",
        "| --- | --- | --- | --- | --- | --- | ---: | ---: |",
        (
            f"| `{terminal.get('task_uuid', '—')}` | `{terminal.get('upstream_task_id', '—')}` "
            f"| {terminal.get('status', '—')} | {terminal.get('estimated_credit_cost', '—')}/"
            f"{terminal.get('reserved_credit_cost', '—')}/"
            f"{terminal.get('actual_credit_cost', '—')} "
            f"| {probe.get('content_type', '—')} | {probe.get('codec', '—')} "
            f"| {probe.get('sample_rate', '—')} | {probe.get('duration', '—')} |"
        ),
        "",
        "契约矩阵覆盖 Speed、Volume、Pitch 和 1–4 个生成数量；"
        "真实任务另外检查 HTTP 202、schema、终态、下载音频流和积分结算。",
        "",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--base-url", default="https://api-leo.clawsea.ai")
    parser.add_argument("--api-key", default=os.getenv("LEO_API_KEY"))
    parser.add_argument("--output-dir", type=Path, default=Path("artifacts/seed-audio"))
    parser.add_argument("--timeout", type=float, default=900)
    parser.add_argument("--poll-interval", type=float, default=8)
    parser.add_argument("--max-credits", type=int, default=350)
    args = parser.parse_args()

    contracts = contract_matrix()
    report: dict[str, Any] = {
        "generated_at": datetime.now(UTC).isoformat(),
        "pricing_rule_version": PRICING_RULE_VERSION,
        "contract_total": len(contracts),
        "contract_passed": sum(1 for case in contracts if case["passed"]),
        "contracts": contracts,
        "live": {},
    }
    if args.live:
        if not args.api_key:
            print("--api-key or LEO_API_KEY is required for --live", file=sys.stderr)
            return 2
        if 350 > args.max_credits:
            print("live budget exceeds --max-credits", file=sys.stderr)
            return 2
        status, created = api_request(
            args.base_url,
            args.api_key,
            "POST",
            "/v1/tasks",
            body=payload(),
            idempotency_key=f"seed-audio-{uuid4().hex}",
        )
        live: dict[str, Any] = {"http_status": status, "created": created}
        if status == 202:
            terminal, states = wait_for_terminal(
                args.base_url,
                args.api_key,
                created["task_uuid"],
                args.timeout,
                args.poll_interval,
            )
            live.update({"terminal": terminal, "states": states})
            media = ((terminal.get("output") or {}).get("media") or [])
            if terminal["status"] == "COMPLETED" and media:
                url_path = urllib.parse.urlparse(media[0]["url"]).path
                extension = Path(url_path).suffix or ".mp3"
                probe = download_and_probe(
                    media[0]["url"],
                    args.output_dir / f"{created['task_uuid']}{extension}",
                )
                live["probe"] = probe
                live["passed"] = all(
                    [
                        terminal["input_schema_version"] == "seed-audio-1.v1",
                        terminal["estimated_credit_cost"] == 350,
                        terminal["reserved_credit_cost"] == 350,
                        terminal["actual_credit_cost"] == 350,
                        probe["sample_rate"] > 0,
                        probe["duration"] > 0,
                        probe["content_type"].startswith("audio/"),
                    ]
                )
            else:
                live["passed"] = False
        else:
            live["passed"] = False
        report["base_url"] = args.base_url
        report["live"] = live

    args.output_dir.mkdir(parents=True, exist_ok=True)
    json_path = args.output_dir / "seed-audio-smoke-report.json"
    md_path = args.output_dir / "seed-audio-smoke-report.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    write_markdown(md_path, report)
    print(json.dumps({"json": str(json_path), "markdown": str(md_path)}, indent=2))
    contract_ok = report["contract_passed"] == report["contract_total"]
    live_ok = not args.live or bool(report["live"].get("passed"))
    return 0 if contract_ok and live_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
