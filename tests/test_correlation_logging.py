from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

import httpx

from app import logging_config
from app.logging_config import scrub_event
from app.main import app
from app.middleware import resolve_correlation_id

CORRELATION_ID = re.compile(r"req-[0-9a-f]{8}")


def _post_chats(payloads: list[tuple[dict, dict]]) -> list[httpx.Response]:
    async def send_all() -> list[httpx.Response]:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return [
                await client.post("/chat", json=body, headers=headers)
                for body, headers in payloads
            ]

    return asyncio.run(send_all())


def _read_events(log_path: Path) -> list[dict]:
    return [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]


def _body(user_id: str, feature: str, message: str) -> dict:
    return {"user_id": user_id, "session_id": f"s-{user_id}", "feature": feature, "message": message}


def test_resolve_correlation_id_accepts_valid_and_replaces_invalid() -> None:
    assert resolve_correlation_id("req-1a2b3c4d") == "req-1a2b3c4d"
    for bad in (None, "", "abc", "req-XYZ12345", "req-1a2b3c4d5", "student@vinuni.edu.vn"):
        generated = resolve_correlation_id(bad)
        assert CORRELATION_ID.fullmatch(generated)
        assert generated != bad


def test_response_headers_carry_correlation_id_and_timing(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(logging_config, "LOG_PATH", tmp_path / "logs.jsonl")

    generated, forwarded = _post_chats(
        [
            (_body("u01", "qa", "Explain traces"), {}),
            (_body("u02", "qa", "Explain logs"), {"x-request-id": "req-0badc0de"}),
        ]
    )

    assert CORRELATION_ID.fullmatch(generated.headers["x-request-id"])
    assert generated.json()["correlation_id"] == generated.headers["x-request-id"]
    assert float(generated.headers["x-response-time-ms"]) >= 0
    assert forwarded.headers["x-request-id"] == "req-0badc0de"


def test_logs_are_enriched_and_context_does_not_leak(monkeypatch, tmp_path: Path) -> None:
    log_path = tmp_path / "logs.jsonl"
    monkeypatch.setattr(logging_config, "LOG_PATH", log_path)

    first, second = _post_chats(
        [
            (_body("u01", "qa", "Explain traces"), {}),
            (_body("u02", "summary", "Summarize monitoring"), {}),
        ]
    )

    events = [e for e in _read_events(log_path) if e.get("service") == "api"]
    by_id: dict[str, list[dict]] = {}
    for event in events:
        by_id.setdefault(event["correlation_id"], []).append(event)

    first_id = first.headers["x-request-id"]
    second_id = second.headers["x-request-id"]
    assert first_id != second_id
    assert {e["event"] for e in by_id[first_id]} == {"request_received", "response_sent"}
    assert {e["feature"] for e in by_id[first_id]} == {"qa"}
    assert {e["feature"] for e in by_id[second_id]} == {"summary"}
    for event in events:
        for field in ("ts", "level", "user_id_hash", "session_id", "feature", "model", "env"):
            assert event.get(field), f"{event['event']} thiếu {field}"


def test_raw_pii_never_reaches_log_file(monkeypatch, tmp_path: Path) -> None:
    log_path = tmp_path / "logs.jsonl"
    monkeypatch.setattr(logging_config, "LOG_PATH", log_path)
    raw_pii = ("student@vinuni.edu.vn", "0987654321", "079203001234", "4111 1111 1111 1111")

    _post_chats([(_body("u09", "qa", "PII: " + ", ".join(raw_pii)), {})])

    content = log_path.read_text(encoding="utf-8")
    for value in raw_pii:
        assert value not in content


def test_scrub_event_handles_nested_and_non_payload_fields() -> None:
    event = scrub_event(
        None,
        "error",
        {
            "event": "request_failed",
            "latency_ms": 120,
            "payload": {"detail": "timeout for 0987654321", "items": ["a@b.co"]},
            "exception": "ValueError: bad card 4111111111111111",
        },
    )

    assert event["latency_ms"] == 120
    assert "0987654321" not in event["payload"]["detail"]
    assert event["payload"]["items"] == ["[REDACTED_EMAIL]"]
    assert "4111111111111111" not in event["exception"]
