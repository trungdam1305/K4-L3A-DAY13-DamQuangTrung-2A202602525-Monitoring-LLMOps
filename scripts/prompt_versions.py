"""Quản lý prompt day13-chat trên project Langfuse cá nhân (docs/PROMPT_VERSIONING.md).

    python scripts/prompt_versions.py status
    python scripts/prompt_versions.py create-v1        # labels: baseline, production
    python scripts/prompt_versions.py create-v2        # label: candidate
    python scripts/prompt_versions.py promote 2        # production -> version 2
    python scripts/prompt_versions.py rollback 1       # production -> version 1

Đọc LANGFUSE_* từ .env. Label trong Langfuse là duy nhất trên các version, nên gán
`production` cho một version sẽ tự gỡ label đó khỏi version cũ.
App cache prompt 60 giây: chờ >60s hoặc restart API sau khi promote/rollback.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.cli import configure_utf8_stdio  # noqa: E402

PROMPT_V1 = "Feature={{feature}}\nDocs={{docs}}\nQuestion={{message}}"
# v2: thay đổi nhỏ về format/độ dài câu trả lời, vẫn giữ đủ ba biến.
PROMPT_V2 = (
    "Feature={{feature}}\nDocs={{docs}}\nQuestion={{message}}\n"
    "Answer in at most 3 concise bullet points, using only the Docs above."
)


def _client():
    load_dotenv(REPO_ROOT / ".env")
    from langfuse import get_client

    client = get_client()
    if not client.auth_check():
        raise SystemExit("Langfuse auth thất bại: kiểm tra LANGFUSE_PUBLIC_KEY/SECRET_KEY/BASE_URL trong .env")
    return client


def _name() -> str:
    import os

    return os.getenv("LANGFUSE_PROMPT_NAME", "day13-chat")


def status(client) -> None:
    name = _name()
    print(f"Prompt '{name}':")
    version = 1
    while True:
        try:
            prompt = client.get_prompt(name, version=version, cache_ttl_seconds=0)
        except Exception:
            break
        labels = ", ".join(prompt.labels) or "-"
        first_line = prompt.prompt.splitlines()[-1]
        print(f"  v{prompt.version}: labels=[{labels}] | last line: {first_line}")
        version += 1
    if version == 1:
        print("  (chưa có version nào)")


def main() -> int:
    configure_utf8_stdio()
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status")
    sub.add_parser("create-v1")
    sub.add_parser("create-v2")
    for command in ("promote", "rollback"):
        cmd = sub.add_parser(command)
        cmd.add_argument("version", type=int)
    args = parser.parse_args()

    client = _client()
    name = _name()
    if args.command == "create-v1":
        prompt = client.create_prompt(
            name=name,
            prompt=PROMPT_V1,
            labels=["baseline", "production"],
            type="text",
            commit_message="v1: baseline template",
        )
        print(f"Đã tạo {name} v{prompt.version} labels={prompt.labels}")
    elif args.command == "create-v2":
        prompt = client.create_prompt(
            name=name,
            prompt=PROMPT_V2,
            labels=["candidate"],
            type="text",
            commit_message="v2: concise bullet-point answer format",
        )
        print(f"Đã tạo {name} v{prompt.version} labels={prompt.labels}")
    elif args.command in ("promote", "rollback"):
        current = client.get_prompt(name, version=args.version, cache_ttl_seconds=0)
        # Giữ các label khác của version đích (vd. baseline/candidate), thêm production.
        labels = sorted({*current.labels, "production"} - {"latest"})
        client.update_prompt(name=name, version=args.version, new_labels=labels)
        print(f"{args.command}: production -> {name} v{args.version}")
    status(client)
    client.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
