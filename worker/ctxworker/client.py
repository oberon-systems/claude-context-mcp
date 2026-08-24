"""Talk to the worker API, over stdlib HTTP.

urllib rather than a client library: the point of this package is that a
Windows machine with a GPU can run it after installing one wheel, and every
dependency that is not llama-cpp-python is one more thing to go wrong there.
"""

from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request
from typing import Any

LOG = logging.getLogger(__name__)

BACKOFF_START = 1.0
BACKOFF_CAP = 60.0


class ApiError(RuntimeError):
    """An answer from the API that was not what was asked for."""

    def __init__(self, status: int, detail: str) -> None:
        """Keep the status apart from the message, so callers can branch."""
        super().__init__(f"{status}: {detail}")
        self.status = status
        self.detail = detail


class Client:
    """One worker's view of one API."""

    def __init__(self, base_url: str, token: str, timeout: float = 30.0) -> None:
        """Point the client at an API. Nothing is contacted until it is used."""
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout

    def call(
        self, path: str, body: dict[str, Any] | None = None, retries: int = 0
    ) -> dict[str, Any]:
        """Make one request, retrying only what is worth retrying.

        A 4xx is never retried: a stale lease means the work has already gone
        to someone else, and repeating the call cannot change that.
        """
        data = json.dumps(body).encode("utf-8") if body is not None else None
        request = urllib.request.Request(
            f"{self.base_url}{path}",
            data=data,
            method="POST" if data is not None else "GET",
            headers={
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
            },
        )

        delay = BACKOFF_START
        for attempt in range(retries + 1):
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    return json.load(response)
            except urllib.error.HTTPError as error:
                detail = error.read().decode("utf-8", "ignore")[:200]
                raise ApiError(error.code, detail) from None
            except (urllib.error.URLError, TimeoutError, ConnectionError) as error:
                if attempt == retries:
                    raise
                LOG.warning(
                    "%s unreachable (%s), retrying in %.0fs", path, error, delay
                )
                time.sleep(delay)
                delay = min(delay * 2, BACKOFF_CAP)
        raise RuntimeError("unreachable")

    def health(self) -> dict[str, Any]:
        """Ask whether the API and its database are up."""
        return self.call("/health")

    def create_job(self, project: str, refresh: bool = False) -> dict[str, Any]:
        """Open a job, or join the one already running for the project."""
        try:
            return self.call("/jobs", {"project": project, "refresh": refresh})
        except ApiError as error:
            if error.status != 409:
                raise
            running = self.call(f"/jobs?project={project}&status=running")["jobs"]
            if not running:
                raise
            LOG.info("Joining job %s, already running", running[0]["id"])
            return running[0]

    def lease(self, job_id: int, worker_id: str, batch: int) -> dict[str, Any]:
        """Claim the next batch of files."""
        return self.call(
            f"/jobs/{job_id}/lease",
            {"worker_id": worker_id, "batch": batch},
            retries=6,
        )

    def heartbeat(self, job_id: int, worker_id: str, token: str) -> None:
        """Push back the deadline of the batch being worked on."""
        self.call(
            f"/jobs/{job_id}/heartbeat",
            {"worker_id": worker_id, "lease_token": token},
        )

    def release(self, job_id: int, worker_id: str, token: str) -> int:
        """Hand back whatever of the batch is unfinished."""
        answer = self.call(
            f"/jobs/{job_id}/release",
            {"worker_id": worker_id, "lease_token": token},
        )
        return int(answer.get("released", 0))

    def result(
        self, task_id: int, worker_id: str, token: str, summary: str, elapsed_ms: int
    ) -> dict[str, Any]:
        """Send back one file's sentence."""
        return self.call(
            f"/tasks/{task_id}/result",
            {
                "worker_id": worker_id,
                "lease_token": token,
                "summary": summary,
                "elapsed_ms": elapsed_ms,
            },
            retries=3,
        )

    def failure(
        self, task_id: int, worker_id: str, token: str, error: str
    ) -> dict[str, Any]:
        """Report that one file could not be described."""
        return self.call(
            f"/tasks/{task_id}/failure",
            {"worker_id": worker_id, "lease_token": token, "error": error},
            retries=3,
        )

    def job(self, job_id: int) -> dict[str, Any]:
        """Read a job and its progress."""
        return self.call(f"/jobs/{job_id}")

    def projects(self) -> list[dict[str, Any]]:
        """List what the stack has indexed, and what each still needs."""
        return self.call("/projects")["projects"]
