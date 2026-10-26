"""Live, human-readable progress for long chat jobs (news scan, image generation).

The browser sends a job id with its chat request and polls GET /api/v1/progress/{job_id}.
Deep code calls report("...") without passing the id around: the current job lives in a
contextvar, which asyncio.to_thread copies into worker threads automatically.
"""

import time
from contextvars import ContextVar
from typing import Dict, Optional, Set

_current_job: ContextVar[Optional[str]] = ContextVar("progress_job", default=None)
_jobs: Dict[str, dict] = {}
_cancelled: Set[str] = set()
_MAX_AGE = 30 * 60


class Cancelled(BaseException):
    """He pressed Stop. A BaseException, so the `except Exception` fallbacks don't swallow it."""


def cancel(job_id: str) -> None:
    _cancelled.add(job_id)


def check() -> None:
    """Raises Cancelled if the current job was stopped (worker threads call this between steps)."""
    if _current_job.get() in _cancelled:
        raise Cancelled()


def sleep(seconds: float) -> None:
    """time.sleep that wakes up within half a second if the job is stopped."""
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        check()
        time.sleep(min(0.5, end - time.monotonic()))


def start(job_id: Optional[str]) -> None:
    _current_job.set(job_id)
    if job_id:
        _cleanup()
        _jobs[job_id] = {"message": "Getting started...", "updated": time.time()}


def report(message: str) -> None:
    check()
    job_id = _current_job.get()
    if job_id and job_id in _jobs:
        _jobs[job_id] = {"message": message, "updated": time.time()}


def get(job_id: str) -> Optional[str]:
    job = _jobs.get(job_id)
    return job["message"] if job else None


def finish(job_id: Optional[str]) -> None:
    if job_id:
        _jobs.pop(job_id, None)
        _cancelled.discard(job_id)


def _cleanup() -> None:
    cutoff = time.time() - _MAX_AGE
    for jid in [j for j, v in _jobs.items() if v["updated"] < cutoff]:
        _jobs.pop(jid, None)
