"""In-process background jobs (clone, scan, AI scan) with progress for the UI to poll."""

from __future__ import annotations

import asyncio
import inspect
import traceback
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from metis.model import now


@dataclass
class Job:
    id: str
    kind: str
    project: str
    repo: str | None = None
    branch: str | None = None
    status: str = "queued"  # queued | running | done | failed
    progress: float = 0.0
    message: str = ""
    error: str | None = None
    started: datetime = field(default_factory=now)
    finished: datetime | None = None
    result: dict[str, Any] = field(default_factory=dict)

    @property
    def active(self) -> bool:
        return self.status in ("queued", "running")

    def set_progress(self, done: int, total: int, message: str) -> None:
        self.progress = (done / total) if total else 0.0
        self.message = message


class JobManager:
    def __init__(self) -> None:
        self.jobs: dict[str, Job] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}

    def start(
        self,
        kind: str,
        project: str,
        fn: Callable[[Job], Any],
        repo: str | None = None,
        branch: str | None = None,
    ) -> Job:
        job = Job(id=uuid.uuid4().hex[:10], kind=kind, project=project, repo=repo, branch=branch)
        self.jobs[job.id] = job
        loop = asyncio.get_running_loop()

        async def run() -> None:
            job.status = "running"
            try:
                if inspect.iscoroutinefunction(fn):
                    await fn(job)
                else:
                    await loop.run_in_executor(None, fn, job)
                job.status = "done"
                job.progress = 1.0
            except Exception as e:  # noqa: BLE001 - surfaced to the UI
                job.status = "failed"
                job.error = f"{type(e).__name__}: {e}"
                job.message = traceback.format_exc().splitlines()[-1]
            finally:
                job.finished = now()
                self._tasks.pop(job.id, None)

        self._tasks[job.id] = loop.create_task(run())
        self._trim()
        return job

    def get(self, job_id: str) -> Job | None:
        return self.jobs.get(job_id)

    def for_project(self, project: str, limit: int = 20) -> list[Job]:
        jobs = [j for j in self.jobs.values() if j.project == project]
        return sorted(jobs, key=lambda j: j.started, reverse=True)[:limit]

    def any_active(self, project: str) -> bool:
        return any(j.active for j in self.jobs.values() if j.project == project)

    def active_for(self, project: str, kind: str, repo: str | None = None) -> Job | None:
        for j in self.jobs.values():
            if (
                j.project == project
                and j.kind == kind
                and j.active
                and (repo is None or j.repo == repo)
            ):
                return j
        return None

    def _trim(self, keep: int = 200) -> None:
        if len(self.jobs) <= keep:
            return
        finished = sorted((j for j in self.jobs.values() if not j.active), key=lambda j: j.started)
        for j in finished[: len(self.jobs) - keep]:
            self.jobs.pop(j.id, None)
