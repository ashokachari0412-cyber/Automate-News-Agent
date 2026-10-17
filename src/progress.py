"""Live, human-readable progress for long chat jobs (news scan, image generation).

The browser sends a job id with its chat request and polls GET /api/v1/progress/{job_id}.
Deep code calls report("...") without passing the id around: the current job lives in a
contextvar, which asyncio.to_thread copies into worker threads automatically.
"""

import time
from contextvars import ContextVar
from typing import Dict, Optional

_current_job: ContextVar[Optional[str]] = ContextVar("progress_job", default=None)
_jobs: Dict[str, dict] = {}
_MAX_AGE = 30 * 60


def start(job_id: Optional[str]) -> None:
    _current_job.set(job_id)
    if job_id:
        _cleanup()
        _jobs[job_id] = {"message": "Getting started...", "updated": time.time()}


def report(message: str) -> None:
    job_id = _current_job.get()
    if job_id and job_id in _jobs:
        _jobs[job_id] = {"message": message, "updated": time.time()}


def get(job_id: str) -> Optional[str]:
    job = _jobs.get(job_id)
    return job["message"] if job else None


def finish(job_id: Optional[str]) -> None:
    if job_id:
        _jobs.pop(job_id, None)


def _cleanup() -> None:
    cutoff = time.time() - _MAX_AGE
    for jid in [j for j, v in _jobs.items() if v["updated"] < cutoff]:
        _jobs.pop(jid, None)
