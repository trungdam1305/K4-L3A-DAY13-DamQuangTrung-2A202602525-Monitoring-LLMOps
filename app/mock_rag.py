from __future__ import annotations

import time

from .incidents import STATE
from .pii import summarize_text
from .tracing import get_langfuse_client, observe

CORPUS = {
    "refund": ["Refunds are available within 7 days with proof of purchase."],
    "monitoring": ["Metrics detect incidents, logs identify affected requests, traces localize the root cause."],
    "policy": ["Do not expose PII in logs. Use sanitized summaries only."],
}
FALLBACK_DOCS = ["No domain document matched. Use general fallback answer."]


def _search(message: str) -> list[str]:
    if STATE["tool_fail"]:
        raise RuntimeError("Vector store timeout")
    if STATE["rag_slow"]:
        time.sleep(2.5)
    lowered = message.lower()
    for key, docs in CORPUS.items():
        if key in lowered:
            return docs
    return FALLBACK_DOCS


# capture_input/output=False: message thô có thể chứa PII, chỉ gửi preview đã scrub.
@observe(name="rag-retrieval", as_type="retriever", capture_input=False, capture_output=False)
def retrieve(message: str) -> list[str]:
    langfuse_client = get_langfuse_client()
    langfuse_client.update_current_span(
        input={"query_preview": summarize_text(message)},
        metadata={"tool_name": "retrieval", "corpus_size": len(CORPUS)},
    )
    docs = _search(message)
    langfuse_client.update_current_span(
        output={"doc_count": len(docs), "fallback": docs is FALLBACK_DOCS},
    )
    return docs
