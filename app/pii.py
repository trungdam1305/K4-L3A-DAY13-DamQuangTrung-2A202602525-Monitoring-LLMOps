from __future__ import annotations

import hashlib
import re

# Thứ tự có ý nghĩa: chuỗi số dài (thẻ, CCCD) được che trước số điện thoại để
# một số thẻ/CCCD không bị che dở dang thành "số điện thoại" + phần dư.
PII_PATTERNS: dict[str, str] = {
    "email": r"[\w.+-]+@[\w.-]+\.\w+",
    # Visa/Master/JCB 16 số (4-4-4-4) và Amex 15 số (4-6-5), cho phép space/dash.
    "credit_card": (
        r"(?<!\d)(?:\d{4}[- ]?){3}\d{4}(?!\d)"
        r"|(?<!\d)\d{4}[- ]?\d{6}[- ]?\d{5}(?!\d)"
    ),
    # CCCD gắn chip: đúng 12 chữ số liên tiếp.
    "cccd": r"(?<!\d)\d{12}(?!\d)",
    # Di động VN: 0xxxxxxxxx, +84xxxxxxxxx hoặc 84xxxxxxxxx; cho phép space/dot/dash.
    "phone_vn": r"(?<!\d)(?:\+84|84|0)(?:[ .-]?\d){9}(?!\d)",
    # Hộ chiếu VN: 1 chữ cái in hoa + 7 chữ số, ví dụ C1234567.
    "passport_vn": r"\b[A-Z]\d{7}\b",
}

_COMPILED_PATTERNS = {name: re.compile(pattern) for name, pattern in PII_PATTERNS.items()}


def scrub_text(text: str) -> str:
    safe = text
    for name, pattern in _COMPILED_PATTERNS.items():
        safe = pattern.sub(f"[REDACTED_{name.upper()}]", safe)
    return safe


def summarize_text(text: str, max_len: int = 80) -> str:
    safe = scrub_text(text).strip().replace("\n", " ")
    return safe[:max_len] + ("..." if len(safe) > max_len else "")


def hash_user_id(user_id: str) -> str:
    return hashlib.sha256(user_id.encode("utf-8")).hexdigest()[:12]
