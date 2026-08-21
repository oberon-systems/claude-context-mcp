"""Run summarizing work on this machine, for a stack that is on another.

Claims a batch, runs the model on each file, sends back one sentence per
file, and asks for more until the job is drained. Nothing about the graph is
known here: the API decides what to describe and what a summary may look
like, and this process only owns the GPU.
"""

from __future__ import annotations

import argparse
import logging
import os
import platform
import signal
import threading
import time
from pathlib import Path
from types import FrameType
from typing import Any

from ctxworker.catalogue import DEFAULT_MODEL, default_dir, file_name
from ctxworker.client import ApiError, Client
from ctxworker.runner import Runner
from ctxworker.server import ServerRunner

LOG = logging.getLogger("ctxworker")

IDLE_SLEEP = 10.0
# Left for the answer when asking whether a job's text fits the window.
RESERVED_TOKENS = 64
WORKER_ID_LIMIT = 64

_stopping = threading.Event()


def stop(signum: int, frame: FrameType | None) -> None:
    """Finish the file in hand, hand the rest back, and exit."""
    if _stopping.is_set():
        raise SystemExit(130)
    LOG.info("Stopping after the current file. Interrupt again to drop the batch.")
    _stopping.set()


def parse_args() -> argparse.Namespace:
    """Read the configuration, with the command line winning over the env."""
    parser = argparse.ArgumentParser(
        prog="ctxworker", description="Summarize a project for a remote context stack."
    )
    parser.add_argument(
        "--api",
        default=os.getenv("WORKER_API_URL", "http://127.0.0.1:3000/worker"),
        help="base URL of the worker API",
    )
    parser.add_argument("--token", default=os.getenv("WORKER_API_TOKEN", ""))
    parser.add_argument("--project", default=os.getenv("WORKER_PROJECT", ""))
    parser.add_argument("--job-id", type=int, default=0, help="join an open job")
    parser.add_argument("--refresh", action="store_true", help="re-describe every file")
    parser.add_argument("--model", default=os.getenv("WORKER_MODEL", DEFAULT_MODEL))
    parser.add_argument("--model-path", default=os.getenv("WORKER_MODEL_PATH", ""))
    parser.add_argument("--model-dir", default=os.getenv("WORKER_MODEL_DIR", ""))
    parser.add_argument(
        "--batch", type=int, default=int(os.getenv("WORKER_BATCH", "4"))
    )
    parser.add_argument(
        "--gpu-layers", type=int, default=int(os.getenv("WORKER_GPU_LAYERS", "-1"))
    )
    parser.add_argument("--ctx", type=int, default=int(os.getenv("WORKER_CTX", "8192")))
    parser.add_argument("--worker-id", default=os.getenv("WORKER_ID", ""))
    parser.add_argument("--once", action="store_true", help="run one batch and stop")
    parser.add_argument(
        "--llama-server",
        default=os.getenv("WORKER_LLAMA_SERVER", ""),
        help="URL of a llama.cpp server to use instead of loading the model",
    )
    parser.add_argument(
        "--llama-server-key", default=os.getenv("WORKER_LLAMA_SERVER_KEY", "")
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="show what llama.cpp prints: the CPU features and the GPU it found",
    )
    return parser.parse_args()


def resolve_weights(args: argparse.Namespace) -> str:
    """Settle on which file to load."""
    if args.model_path:
        return args.model_path
    directory = Path(args.model_dir) if args.model_dir else default_dir()
    return str(directory / file_name(args.model))


def build_runner(args: argparse.Namespace) -> Runner | ServerRunner:
    """Settle on where the model runs: in this process, or over HTTP."""
    if args.llama_server:
        return ServerRunner(args.llama_server, api_key=args.llama_server_key)
    return Runner(
        resolve_weights(args),
        n_ctx=args.ctx,
        gpu_layers=args.gpu_layers,
        verbose=args.verbose,
    )


def beat(client: Client, job_id: int, worker_id: str, token: str, every: float) -> None:
    """Keep the lease alive while a batch is being worked through."""
    while not _stopping.wait(every):
        try:
            client.heartbeat(job_id, worker_id, token)
        except (ApiError, OSError) as error:
            LOG.warning("Heartbeat failed: %s", error)
            return


def run_batch(
    client: Client,
    runner: Runner | ServerRunner,
    worker_id: str,
    job_id: int,
    lease: dict[str, Any],
) -> int:
    """Describe every file of one batch. Returns how many were answered."""
    token = lease["lease_token"]
    tasks = lease["tasks"]
    system_prompt = lease["system_prompt"]
    max_tokens = int(lease["max_tokens"])

    heartbeat = threading.Thread(
        target=beat,
        args=(client, job_id, worker_id, token, max(lease["lease_seconds"] / 3, 5)),
        daemon=True,
    )
    heartbeat.start()

    answered = 0
    unfinished = False
    for task in tasks:
        if _stopping.is_set():
            unfinished = True
            break
        started = time.monotonic()
        try:
            reply = runner.summarize(system_prompt, task["prompt"], max_tokens)
        except Exception as error:  # noqa: BLE001 - the model raises anything
            LOG.warning("%s: model failed (%s)", task["file_path"], error)
            client.failure(task["task_id"], worker_id, token, str(error)[:400])
            continue
        elapsed = int((time.monotonic() - started) * 1000)
        try:
            answer = client.result(task["task_id"], worker_id, token, reply, elapsed)
        except ApiError as error:
            if error.status == 409:
                LOG.warning("%s: lease had expired, dropping", task["file_path"])
                unfinished = True
                break
            raise
        answered += 1
        applied = "applied" if answer["applied"] else f"rejected ({answer['reason']})"
        LOG.info("%s: %s in %d ms", task["file_path"], applied, elapsed)

    if unfinished:
        released = client.release(job_id, worker_id, token)
        if released:
            LOG.info("Handed %d file(s) back to the queue", released)
    return answered


def main() -> None:
    """Claim and describe until the job is drained, or until interrupted."""
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    args = parse_args()
    if not args.token:
        raise SystemExit("--token or WORKER_API_TOKEN is required")
    if not args.project and not args.job_id:
        raise SystemExit("--project or --job-id is required")

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)

    worker_id = (args.worker_id or f"{platform.node()}-{os.getpid()}")[:WORKER_ID_LIMIT]
    client = Client(args.api, args.token)
    LOG.info("API at %s is %s", args.api, client.health()["status"])

    runner = build_runner(args)

    job = (
        client.job(args.job_id)
        if args.job_id
        else client.create_job(args.project, args.refresh)
    )
    job_id = int(job["id"])
    input_chars = int(job["input_chars"])
    LOG.info(
        "Job %d over %s: %d file(s) to describe, %d characters each",
        job_id,
        job["project"],
        job["progress"]["pending"],
        input_chars,
    )

    if not runner.fits(input_chars, RESERVED_TOKENS):
        raise SystemExit(
            f"a context window of {runner.n_ctx} cannot hold {input_chars} "
            f"characters; {runner.widen_hint}, or create the job with a "
            "smaller input_chars"
        )

    described = 0
    try:
        while not _stopping.is_set():
            try:
                lease = client.lease(job_id, worker_id, args.batch)
            except ApiError as error:
                # A job that drained while this worker was mid-batch is the
                # normal end of a run, not a failure to report.
                if error.status != 409:
                    raise
                LOG.info("Job %d is finished", job_id)
                break
            if not lease["tasks"]:
                if lease["job"]["status"] != "running" or not lease["remaining"]:
                    LOG.info("Job %d is %s", job_id, lease["job"]["status"])
                    break
                LOG.info("Nothing to claim, waiting")
                if _stopping.wait(IDLE_SLEEP):
                    break
                continue
            described += run_batch(client, runner, worker_id, job_id, lease)
            if args.once:
                break
    finally:
        runner.close()
        progress = client.job(job_id)["progress"]
        LOG.info(
            "Described %d file(s). Job %d: %d done, %d pending, %d failed",
            described,
            job_id,
            progress["done"],
            progress["pending"],
            progress["failed"],
        )


if __name__ == "__main__":
    main()
