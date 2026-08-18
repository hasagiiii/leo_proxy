#!/usr/bin/env python3
"""Direct API smoke test for Seedance 2.5 reference limits.

The live matrix verifies the exact accepted boundary (30 images, 10 videos,
10 audio files), three +1 validation failures, asset resolution progress,
provider completion, and credit settlement.  It never reads browser state.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any
from uuid import uuid4

MODEL = "bytedance/seedance-2.5"
TERMINAL_STATES = {"COMPLETED", "FAILED", "CANCELLED"}
EXPECTED_LIMITS = {
    "reference_images": 30,
    "reference_video_urls": 10,
    "reference_audio_urls": 10,
}
EXPECTED_ASSET_COUNT = sum(EXPECTED_LIMITS.values())
EXPECTED_CREDITS = 720  # 480P * 4 seconds, pricing rule v9.
UTC = getattr(dt, "UTC", dt.timezone.utc)  # noqa: UP017 -- supports host Python 3.9


def read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object in {path}")
    return value


def first_media_url(path: Path, media_type: str) -> str:
    value = read_json(path)
    media = ((value.get("output") or {}).get("media") or [])
    for item in media:
        if item.get("type") == media_type and item.get("url"):
            return str(item["url"])
    raise ValueError(f"{path} has no {media_type} output URL")


def tagged_url(url: str, kind: str, ordinal: int) -> str:
    parts = urllib.parse.urlsplit(url)
    query = urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
    query.append(("frame_ops_limit_probe", f"{kind}-{ordinal}"))
    return urllib.parse.urlunsplit(
        (parts.scheme, parts.netloc, parts.path, urllib.parse.urlencode(query), parts.fragment)
    )


def api_request(
    base_url: str,
    api_key: str,
    method: str,
    path: str,
    *,
    payload: dict[str, Any] | None = None,
    idempotency_key: str | None = None,
    timeout: float = 90,
) -> tuple[int, Any]:
    data = None if payload is None else json.dumps(payload).encode()
    headers = {"Accept": "application/json"}
    if api_key:
        headers["X-API-Key"] = api_key
    if data is not None:
        headers["Content-Type"] = "application/json"
    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key
    request = urllib.request.Request(
        f"{base_url.rstrip('/')}{path}",
        data=data,
        headers=headers,
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            content = response.read()
            return response.status, json.loads(content) if content else None
    except urllib.error.HTTPError as exc:
        content = exc.read()
        try:
            body = json.loads(content) if content else None
        except json.JSONDecodeError:
            body = content.decode(errors="replace")
        return exc.code, body
    except urllib.error.URLError as exc:
        return 0, {
            "error": "NETWORK_ERROR",
            "reason": str(exc.reason),
        }


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def openapi_limits(openapi: dict[str, Any]) -> dict[str, Any]:
    schema = (
        ((openapi.get("components") or {}).get("schemas") or {}).get(
            "Seedance25ReferenceToVideoInput"
        )
        or {}
    )
    properties = schema.get("properties") or {}
    return {
        field: (properties.get(field) or {}).get("maxItems")
        for field in EXPECTED_LIMITS
    }


def base_payload() -> dict[str, Any]:
    return {
        "provider": "leonardo",
        "task_type": "VIDEO_GENERATION",
        "model": MODEL,
        "mode": "reference-to-video",
        "input": {
            "prompt": (
                "Reference capacity verification: a blue paper airplane glides "
                "over a quiet sunrise lake, stable cinematic camera, family-safe."
            ),
            "duration": 4,
            "resolution": "480P",
            "aspect_ratio": "16:9",
            "audio": True,
        },
    }


def max_payload(image_url: str, video_url: str, audio_url: str) -> dict[str, Any]:
    payload = base_payload()
    payload["input"].update(
        {
            "reference_images": [
                {
                    "url": tagged_url(image_url, "image", index),
                    "strength": "MID",
                }
                for index in range(EXPECTED_LIMITS["reference_images"])
            ],
            "reference_video_urls": [
                tagged_url(video_url, "video", index)
                for index in range(EXPECTED_LIMITS["reference_video_urls"])
            ],
            "reference_audio_urls": [
                tagged_url(audio_url, "audio", index)
                for index in range(EXPECTED_LIMITS["reference_audio_urls"])
            ],
        }
    )
    return payload


def overflow_payload(
    field: str,
    image_url: str,
    video_url: str,
    audio_url: str,
) -> dict[str, Any]:
    payload = base_payload()
    task_input = payload["input"]
    if field == "reference_images":
        task_input[field] = [
            {"url": tagged_url(image_url, "overflow-image", index), "strength": "MID"}
            for index in range(31)
        ]
    else:
        task_input["reference_images"] = [{"url": image_url, "strength": "MID"}]
        url = video_url if field == "reference_video_urls" else audio_url
        task_input[field] = [tagged_url(url, f"overflow-{field}", index) for index in range(11)]
    return payload


def poll_task(
    base_url: str,
    api_key: str,
    task_uuid: str,
    poll_interval: float,
    timeout: float,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    deadline = time.monotonic() + timeout
    events: list[dict[str, Any]] = []
    previous: tuple[Any, ...] | None = None
    while time.monotonic() < deadline:
        status, body = api_request(base_url, api_key, "GET", f"/v1/tasks/{task_uuid}")
        if status != 200 or not isinstance(body, dict):
            events.append(
                {
                    "observed_at": dt.datetime.now(UTC).isoformat(),
                    "http_status": status,
                }
            )
            time.sleep(poll_interval)
            continue
        progress = body.get("progress") or {}
        signature = (
            body.get("status"),
            progress.get("phase"),
            progress.get("resolved_assets"),
            progress.get("total_assets"),
            body.get("error_code"),
        )
        if signature != previous:
            event = {
                "observed_at": dt.datetime.now(UTC).isoformat(),
                "status": signature[0],
                "phase": signature[1],
                "resolved_assets": signature[2],
                "total_assets": signature[3],
                "error_code": signature[4],
            }
            events.append(event)
            print(json.dumps(event, ensure_ascii=False), flush=True)
            previous = signature
        if str(body.get("status", "")).upper() in TERMINAL_STATES:
            return body, events
        time.sleep(poll_interval)
    raise TimeoutError(f"task {task_uuid} exceeded {timeout:.0f}s polling timeout")


def markdown_report(report: dict[str, Any]) -> str:
    lines = [
        "# Seedance 2.5 参考素材上限线上测试报告",
        "",
        f"- Run ID: `{report['run_id']}`",
        f"- API: `{report['base_url']}`",
        f"- 结果: **{report['result']}**",
        f"- OpenAPI 上限: `{json.dumps(report['openapi_limits'], ensure_ascii=False)}`",
        "",
        "## 越界校验",
        "",
        "| 字段 | 请求数 | HTTP | 预期 | 结果 |",
        "|---|---:|---:|---:|---|",
    ]
    for field, item in report.get("overflow", {}).items():
        lines.append(
            f"| `{field}` | {item['count']} | {item['http_status']} | 422 | "
            f"{'PASS' if item['passed'] else 'FAIL'} |"
        )
    maximum = report.get("maximum") or {}
    lines.extend(
        [
            "",
            "## 上限组合真实任务",
            "",
            f"- task_uuid: `{maximum.get('task_uuid', '—')}`",
            f"- 创建 HTTP: `{maximum.get('create_http_status', '—')}`",
            f"- 终态: `{maximum.get('status', '—')}`",
            "- 素材进度: "
            f"`{maximum.get('resolved_assets', '—')}/"
            f"{maximum.get('total_assets', '—')}`",
            f"- 预计积分: `{maximum.get('estimated_credit_cost', '—')}`",
            f"- 实际积分: `{maximum.get('actual_credit_cost', '—')}`",
            f"- 输出: `{maximum.get('output_type', '—')}` "
            f"`{maximum.get('output_dimensions', '—')}`",
            "",
            "## 失败项",
            "",
            *(f"- {item}" for item in report.get("failures") or ["无"]),
            "",
        ]
    )
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="perform POSTs against the target API")
    parser.add_argument(
        "--base-url",
        default=os.getenv("VIDEO_SERVICE_BASE_URL", "http://127.0.0.1:18080"),
    )
    parser.add_argument(
        "--env-file",
        type=Path,
        default=None,
    )
    parser.add_argument("--api-key", default=os.getenv("VIDEO_SERVICE_API_AUTH_KEY", ""))
    parser.add_argument(
        "--image-json",
        type=Path,
        required=True,
    )
    parser.add_argument(
        "--video-json",
        type=Path,
        required=True,
    )
    parser.add_argument(
        "--audio-json",
        type=Path,
        required=True,
    )
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--poll-interval", type=float, default=5)
    parser.add_argument("--timeout", type=float, default=1800)
    parser.add_argument("--max-credits", type=int, default=EXPECTED_CREDITS)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    started = dt.datetime.now(UTC)
    run_id = f"{started.strftime('%Y%m%dT%H%M%SZ')}-{uuid4().hex[:8]}"
    output_dir = args.output_dir or Path("artifacts") / f"seedance25-limit-{run_id}"
    output_dir.mkdir(parents=True, exist_ok=True)

    env = read_env(args.env_file) if args.env_file is not None else {}
    api_key = args.api_key or env.get("VIDEO_SERVICE_API_AUTH_KEY", "")
    if args.live and not api_key:
        raise SystemExit("missing VIDEO_SERVICE_API_AUTH_KEY")
    if args.live and args.max_credits < EXPECTED_CREDITS:
        raise SystemExit(
            f"maximum task expects {EXPECTED_CREDITS} credits; --max-credits is {args.max_credits}"
        )

    image_url = first_media_url(args.image_json, "image/png")
    video_url = first_media_url(args.video_json, "video/mp4")
    audio_url = first_media_url(args.audio_json, "audio/mpeg")
    prepared = max_payload(image_url, video_url, audio_url)
    write_json(output_dir / "maximum-request.json", prepared)

    report: dict[str, Any] = {
        "run_id": run_id,
        "started_at": started.isoformat(),
        "base_url": args.base_url,
        "live": args.live,
        "expected_limits": EXPECTED_LIMITS,
        "expected_asset_count": EXPECTED_ASSET_COUNT,
        "expected_credits": EXPECTED_CREDITS,
        "openapi_limits": {},
        "overflow": {},
        "maximum": {},
        "failures": [],
    }
    if not args.live:
        report["result"] = "PREPARED"
        report["finished_at"] = dt.datetime.now(UTC).isoformat()
        write_json(output_dir / "report.json", report)
        (output_dir / "report.md").write_text(markdown_report(report))
        print(f"prepared={output_dir}")
        return 0

    openapi_status, openapi = api_request(args.base_url, "", "GET", "/openapi.json")
    report["openapi_http_status"] = openapi_status
    if openapi_status == 200 and isinstance(openapi, dict):
        report["openapi_limits"] = openapi_limits(openapi)
    else:
        report["failures"].append(f"OpenAPI HTTP {openapi_status}")
    write_json(
        output_dir / "openapi-limits.json",
        {"http_status": openapi_status, "limits": report["openapi_limits"]},
    )
    if report["openapi_limits"] != EXPECTED_LIMITS:
        report["failures"].append(
            f"target OpenAPI limits are {report['openapi_limits']}, expected {EXPECTED_LIMITS}"
        )

    for field, expected in EXPECTED_LIMITS.items():
        payload = overflow_payload(field, image_url, video_url, audio_url)
        count = expected + 1
        status, body = api_request(
            args.base_url,
            api_key,
            "POST",
            "/v1/tasks",
            payload=payload,
            idempotency_key=f"seedance25-limit-{run_id}-{field}-{count}",
        )
        item = {"count": count, "http_status": status, "body": body, "passed": status == 422}
        report["overflow"][field] = item
        write_json(output_dir / f"overflow-{field}.json", item)
        print(f"overflow field={field} count={count} http={status}", flush=True)
        if status != 422:
            report["failures"].append(f"{field}={count} returned HTTP {status}, expected 422")

    boundary_ready = (
        report["openapi_limits"] == EXPECTED_LIMITS
        and all(item["passed"] for item in report["overflow"].values())
    )
    if boundary_ready:
        status, created = api_request(
            args.base_url,
            api_key,
            "POST",
            "/v1/tasks",
            payload=prepared,
            idempotency_key=f"seedance25-limit-{run_id}-30-10-10",
        )
        write_json(output_dir / "maximum-create.json", {"http_status": status, "body": created})
        maximum = report["maximum"]
        maximum["create_http_status"] = status
        if status != 202 or not isinstance(created, dict):
            report["failures"].append(f"30/10/10 create returned HTTP {status}")
        else:
            task_uuid = str(created["task_uuid"])
            maximum["task_uuid"] = task_uuid
            final, events = poll_task(
                args.base_url,
                api_key,
                task_uuid,
                args.poll_interval,
                args.timeout,
            )
            write_json(output_dir / "maximum-events.json", events)
            write_json(output_dir / "maximum-final.json", final)
            progress = final.get("progress") or {}
            media = ((final.get("output") or {}).get("media") or [])
            first = media[0] if media else {}
            maximum.update(
                {
                    "status": final.get("status"),
                    "error_code": final.get("error_code"),
                    "error_message": final.get("error_message"),
                    "resolved_assets": progress.get("resolved_assets"),
                    "total_assets": progress.get("total_assets"),
                    "estimated_credit_cost": final.get("estimated_credit_cost"),
                    "actual_credit_cost": final.get("actual_credit_cost"),
                    "output_type": first.get("type"),
                    "output_dimensions": (
                        f"{first.get('width')}x{first.get('height')}" if first else None
                    ),
                    "output_url": first.get("url"),
                }
            )
            if final.get("status") != "COMPLETED":
                report["failures"].append(
                    f"30/10/10 terminal state {final.get('status')}: {final.get('error_code')}"
                )
            if progress.get("resolved_assets") != EXPECTED_ASSET_COUNT:
                report["failures"].append(
                    f"resolved_assets={progress.get('resolved_assets')}, "
                    f"expected {EXPECTED_ASSET_COUNT}"
                )
            if progress.get("total_assets") != EXPECTED_ASSET_COUNT:
                report["failures"].append(
                    f"total_assets={progress.get('total_assets')}, expected {EXPECTED_ASSET_COUNT}"
                )
            if final.get("estimated_credit_cost") != EXPECTED_CREDITS:
                report["failures"].append(
                    f"estimated_credit_cost={final.get('estimated_credit_cost')}, "
                    f"expected {EXPECTED_CREDITS}"
                )
            if (
                final.get("status") == "COMPLETED"
                and final.get("actual_credit_cost") != EXPECTED_CREDITS
            ):
                report["failures"].append(
                    f"actual_credit_cost={final.get('actual_credit_cost')}, "
                    f"expected {EXPECTED_CREDITS}"
                )
    else:
        report["maximum"]["skipped"] = "boundary contract verification failed"

    report["finished_at"] = dt.datetime.now(UTC).isoformat()
    report["result"] = "PASS" if not report["failures"] else "FAIL"
    write_json(output_dir / "report.json", report)
    (output_dir / "report.md").write_text(markdown_report(report))
    print(f"report={output_dir / 'report.md'}")
    print(f"result={report['result']}")
    return 0 if report["result"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
