from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from .tracing import observe


DEFAULT_PROMPT_TEMPLATE = "Feature={{feature}}\nDocs={{docs}}\nQuestion={{message}}"


@dataclass(frozen=True)
class ResolvedPrompt:
    text: str
    name: str
    label: str
    version: str
    source: str
    managed_prompt: Any | None = None
    fetch_error: str | None = None


def _compile_local_prompt(*, feature: str, docs: list[str], message: str) -> str:
    return (
        DEFAULT_PROMPT_TEMPLATE.replace("{{feature}}", feature)
        .replace("{{docs}}", "\n".join(docs))
        .replace("{{message}}", message)
    )


def _fetch_managed_prompt(client: Any, name: str, label: str) -> Any:
    return client.get_prompt(
        name,
        label=label,
        type="text",
        fallback=DEFAULT_PROMPT_TEMPLATE,
        cache_ttl_seconds=60,
        fetch_timeout_seconds=2,
        max_retries=0,
    )


def warm_prompt_cache(client: Any, attempts: int = 3) -> str | None:
    """Fetch prompt lúc khởi động để request đầu tiên không chịu cache miss.

    Trace cho thấy lần fetch đầu tới Langfuse mất ~1.1 s (span prompt-resolve),
    đẩy P95/P99 lên dù không có sự cố. SDK không raise khi fetch lỗi mà trả
    prompt fallback, nên phải kiểm tra `is_fallback`. Trả về tên lỗi cuối cùng
    nếu mọi lần thử đều thất bại.
    """
    name = os.getenv("LANGFUSE_PROMPT_NAME", "day13-chat")
    label = os.getenv("LANGFUSE_PROMPT_LABEL", "production")
    error: str | None = None
    for _ in range(attempts):
        try:
            prompt = _fetch_managed_prompt(client, name, label)
        except Exception as exc:  # warm-up lỗi không được chặn API khởi động
            error = type(exc).__name__
            continue
        if not getattr(prompt, "is_fallback", False):
            return None
        error = "LangfuseFallback"
    return error


# Span riêng cho bước lấy prompt: lần fetch đầu (cache miss) đi qua mạng tới
# Langfuse và có thể chiếm phần lớn duration của root. Không capture input vì
# message thô có thể chứa PII.
@observe(name="prompt-resolve", as_type="span", capture_input=False, capture_output=False)
def resolve_prompt(
    client: Any,
    *,
    feature: str,
    docs: list[str],
    message: str,
    enabled: bool,
) -> ResolvedPrompt:
    name = os.getenv("LANGFUSE_PROMPT_NAME", "day13-chat")
    label = os.getenv("LANGFUSE_PROMPT_LABEL", "production")
    text = _compile_local_prompt(feature=feature, docs=docs, message=message)
    if enabled:
        try:
            managed_prompt = _fetch_managed_prompt(client, name, label)
            if getattr(managed_prompt, "is_fallback", False):
                return ResolvedPrompt(
                    text=text,
                    name=name,
                    label=label,
                    version="local-v1",
                    source="local-fallback",
                    fetch_error="LangfuseFallback",
                )
            return ResolvedPrompt(
                text=managed_prompt.compile(
                    feature=feature,
                    docs="\n".join(docs),
                    message=message,
                ),
                name=name,
                label=label,
                version=str(managed_prompt.version),
                source="langfuse",
                managed_prompt=managed_prompt,
            )
        except Exception as exc:  # Langfuse là dependency ngoài; app phải có fallback local
            return ResolvedPrompt(
                text=text,
                name=name,
                label=label,
                version="local-v1",
                source="local-fallback",
                fetch_error=type(exc).__name__,
            )

    return ResolvedPrompt(
        text=text,
        name=name,
        label=label,
        version="local-v1",
        source="local",
    )
