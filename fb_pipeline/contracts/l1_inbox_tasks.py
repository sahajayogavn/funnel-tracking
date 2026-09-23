"""Task/result contracts for the inbox parallel-fetch pipeline.

Pure data (no Playwright, no sqlite handles) so instances can be logged as
JSON at DEBUG with the universal ID `logs:inbox-parallel-fetch-001:<task|result>`.

# code:inbox-parallel-fetch-001:contracts
"""
from dataclasses import dataclass
from typing import Literal

from fb_pipeline.contracts.l1_inbox import ThreadRecord


@dataclass(frozen=True)
class ThreadTask:
    ordinal: int                 # Stage 1 global ordinal -> inbox_sort_index
    record: ThreadRecord         # provisional identity from the sidebar card
    absolute_top: float          # scroller offset hint from Stage 1
    psid_hint: str                # "" or a PSID resolved from users.fb_url
    is_new: bool                  # no threads row matched in Stage 1
    attempt: int = 1              # incremented on re-queue (tab loss or locate failure)
    failed_by: str = ""           # worker that failed the previous attempt ("" on first try)

    def to_log_dict(self) -> dict:
        return {
            "ordinal": self.ordinal,
            "record": {
                "thread_id": self.record.thread_id,
                "thread_name": self.record.thread_name,
                "ordinal": self.ordinal,
            },
            "absolute_top": self.absolute_top,
            "psid_hint": self.psid_hint,
            "is_new": self.is_new,
            "attempt": self.attempt,
            "failed_by": self.failed_by,
        }


@dataclass
class ThreadResult:
    ordinal: int
    thread_id: str                # final id (after PSID recompute), "" if failed
    status: Literal["persisted", "needs_review", "no_messages", "click_verify_failed",
                    "locate_failed", "facebook_temporarily_blocked", "error", "skipped"]
    messages_added: int = 0
    locate_method: str = ""       # "direct_url" | "sidebar_identity" | ...
    elapsed_ms: int = 0
    worker: str = ""              # "orchestrator" | "worker:1" ...
    error: str = ""
    thread_name: str = ""         # for the assignment review log
    attempt: int = 1              # which attempt of the task produced this result
    requeued: bool = False        # True when this failure was handed to another worker
    history_complete: bool = True  # A persisted partial history is not a complete fetch.

    def to_log_dict(self) -> dict:
        return {
            "ordinal": self.ordinal,
            "thread_id": self.thread_id,
            "thread_name": self.thread_name,
            "status": self.status,
            "messages_added": self.messages_added,
            "locate_method": self.locate_method,
            "elapsed_ms": self.elapsed_ms,
            "worker": self.worker,
            "error": self.error,
            "attempt": self.attempt,
            "requeued": self.requeued,
            "history_complete": self.history_complete,
        }


__all__ = ["ThreadTask", "ThreadResult"]
