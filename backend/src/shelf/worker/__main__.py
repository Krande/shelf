"""Entrypoint: ``python -m shelf.worker``.

Connects to NATS and runs one or more durable JetStream consumers
in parallel via ``asyncio.gather``. The set of consumers is chosen
by the ``SHELF_WORKER_CONSUMERS`` env var (comma-separated) so the
same image can run as the CPU pod (extract + ocr) or the GPU pod
(outline) without a custom command line.

Default = ``extract,ocr`` so existing CPU pods keep their behaviour.

Each consumer loop fetches its own subject and dispatches to the
matching ``*_attachment`` coroutine. The actual handler logic lives
in ``shelf.worker.{extract,ocr,outline}`` so this module stays small
+ testable.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import sys
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from .. import config
from ..services import queue as queue_svc

log = logging.getLogger("shelf.worker")

FETCH_TIMEOUT = 5.0
# Caps redelivery for poison-pill messages. At MAX_DELIVERIES the
# application acks the message and marks the row terminal-failed
# (see ``_process`` below). The same value is also baked into the
# JetStream ConsumerConfig so the broker stops redelivering even if
# the worker pod crashed before reaching the application-level cap.
MAX_DELIVERIES = 3
# AckWait controls how long the broker holds a message as in-flight
# before redelivering. Long enough for OCR / outline runs to finish
# (multi-minute on big PDFs), short enough that a crashed pod
# doesn't sit on a message for an hour.
ACK_WAIT_SECONDS = 60 * 60


Handler = Callable[[str], Awaitable[None]]
TerminalMarker = Callable[[str], Awaitable[None]]


@dataclass(frozen=True)
class ConsumerSpec:
    """Static config for one durable JetStream consumer.

    Module imports for the handler/terminal pair are deferred until
    the consumer is actually selected — that way a pod that only
    runs ``outline`` doesn't pay the OCR import cost (Tesseract
    bindings via OCRmyPDF), and the CPU pod doesn't pay Marker's.
    """

    name: str
    durable: str
    subject: str
    fetch_batch: int
    handler_path: str  # "shelf.worker.extract:extract_attachment"
    terminal_path: str  # "shelf.worker.extract:mark_failed_terminal"


# Registry of consumer kinds. Keys match the values you put in
# ``SHELF_WORKER_CONSUMERS``.
SPECS: dict[str, ConsumerSpec] = {
    "extract": ConsumerSpec(
        name="extract",
        durable="extract-text",
        subject=queue_svc.SUBJECT_EXTRACT,
        fetch_batch=5,
        handler_path="shelf.worker.extract:extract_attachment",
        terminal_path="shelf.worker.extract:mark_failed_terminal",
    ),
    "ocr": ConsumerSpec(
        name="ocr",
        durable="ocr",
        subject=queue_svc.SUBJECT_OCR,
        # OCR is heavier than extract. Pull one job at a time so a
        # slow run (multi-hundred-page scan) doesn't block extracts
        # that completed long since.
        fetch_batch=1,
        handler_path="shelf.worker.ocr:ocr_attachment",
        terminal_path="shelf.worker.ocr:mark_failed_terminal",
    ),
    "ocr-gpu": ConsumerSpec(
        name="ocr-gpu",
        durable="ocr-gpu",
        subject=queue_svc.SUBJECT_OCR_GPU,
        # GPU runs are even more "one job, one slot" than CPU OCR —
        # Qwen2.5-VL-7B owns the GPU's VRAM exclusively while it runs.
        fetch_batch=1,
        handler_path="shelf.worker.olmocr:olmocr_attachment",
        terminal_path="shelf.worker.olmocr:mark_failed_terminal",
    ),
    "outline": ConsumerSpec(
        name="outline",
        durable="outline",
        subject=queue_svc.SUBJECT_OUTLINE,
        # Same reasoning as OCR: Marker on a 200-page doc is the
        # whole consumer slot for minutes; pull one at a time so a
        # second job doesn't sit in the worker's prefetch buffer
        # while the broker's other consumers stay idle.
        fetch_batch=1,
        handler_path="shelf.worker.outline:outline_attachment",
        terminal_path="shelf.worker.outline:mark_failed_terminal",
    ),
}


def _load_callable(path: str) -> Callable[..., Awaitable[None]]:
    module_name, attr = path.split(":", 1)
    import importlib

    module = importlib.import_module(module_name)
    # getattr on a module is Any; the annotation is what stops that Any
    # escaping into the declared return type.
    fn: Callable[..., Awaitable[None]] = getattr(module, attr)
    return fn


def _selected_consumers() -> list[ConsumerSpec]:
    # Default for the CPU pod: extract + tesseract OCR + font-heuristic
    # outline. The outline consumer used to live on the GPU pod (back
    # when it was Marker), but the new font-heuristic engine has no
    # GPU need and runs cheaply alongside extract / ocr. The GPU pod
    # overrides this with SHELF_WORKER_CONSUMERS=ocr-gpu.
    raw = os.environ.get("SHELF_WORKER_CONSUMERS", "extract,ocr,outline")
    names = [n.strip() for n in raw.split(",") if n.strip()]
    out: list[ConsumerSpec] = []
    for n in names:
        spec = SPECS.get(n)
        if spec is None:
            log.warning(
                "unknown consumer %r in SHELF_WORKER_CONSUMERS; skipping",
                n,
            )
            continue
        out.append(spec)
    return out


async def _run() -> None:
    if not config.settings.nats_url:
        log.error(
            "SHELF_NATS_URL is not set; the worker cannot run without "
            "a queue. Configure it on the worker pod's env."
        )
        sys.exit(2)

    selected = _selected_consumers()
    if not selected:
        log.error(
            "no consumers selected (SHELF_WORKER_CONSUMERS=%r). Set the "
            "env var to e.g. 'extract,ocr' or 'outline'.",
            os.environ.get("SHELF_WORKER_CONSUMERS"),
        )
        sys.exit(2)

    js = await queue_svc._connect()
    if js is None:
        log.error("queue connect returned None despite nats_url being set")
        sys.exit(2)

    # Build (psub, handler, terminal, spec) up front so an import
    # error in any selected handler module fails fast at startup
    # rather than mid-fetch.
    #
    # We assert each consumer with max_deliver = MAX_DELIVERIES as a
    # *server-side* backstop. The application loop (`_process` below)
    # also caps at MAX_DELIVERIES, but if the pod crashes between
    # dispatch and ack/nak the broker would otherwise redeliver
    # forever — JetStream's max_deliver is what makes the cap real
    # across pod restarts. add_consumer is create-or-update, so
    # bumping MAX_DELIVERIES here updates existing durables on next
    # boot without manual cleanup.
    from nats.js.api import AckPolicy, ConsumerConfig

    runtime: list[
        tuple[ConsumerSpec, object, Handler, TerminalMarker]
    ] = []
    for spec in selected:
        handler = _load_callable(spec.handler_path)
        terminal = _load_callable(spec.terminal_path)
        await js.add_consumer(
            stream=queue_svc.STREAM_NAME,
            config=ConsumerConfig(
                durable_name=spec.durable,
                filter_subject=spec.subject,
                ack_policy=AckPolicy.EXPLICIT,
                max_deliver=MAX_DELIVERIES,
                ack_wait=ACK_WAIT_SECONDS,
            ),
        )
        psub = await js.pull_subscribe_bind(
            consumer=spec.durable,
            stream=queue_svc.STREAM_NAME,
        )
        runtime.append((spec, psub, handler, terminal))

    stop = asyncio.Event()

    def _on_signal(*_args: object) -> None:
        log.info("shutdown signal received; draining current batch")
        stop.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _on_signal)
        except NotImplementedError:
            # Windows' ProactorEventLoop doesn't implement it at all —
            # this raises rather than degrading, so without the fallback
            # the worker cannot start on Windows, which is where it gets
            # developed against. `signal.signal` works there; the handler
            # runs on the main thread between bytecodes, so it hops back
            # onto the loop rather than touching the Event directly.
            #
            # Shutdown is delayed until the current `fetch` times out
            # (FETCH_TIMEOUT), since the handler only runs once the
            # interpreter regains control.
            signal.signal(
                sig, lambda *_a: loop.call_soon_threadsafe(_on_signal)
            )

    log.info(
        "worker ready: %s",
        ", ".join(
            f"{s.name}(subject={s.subject} durable={s.durable})"
            for s, _, _, _ in runtime
        ),
    )

    await asyncio.gather(
        *[
            _consume_loop(
                spec.name,
                psub,
                spec.fetch_batch,
                handler,
                terminal,
                stop,
            )
            for spec, psub, handler, terminal in runtime
        ]
    )

    await queue_svc.close()


async def _consume_loop(
    name: str,
    psub: object,
    batch: int,
    handler: Handler,
    terminal: TerminalMarker,
    stop: asyncio.Event,
) -> None:
    while not stop.is_set():
        try:
            msgs = await psub.fetch(  # type: ignore[attr-defined]
                batch=batch, timeout=FETCH_TIMEOUT
            )
        except TimeoutError:
            continue
        except Exception:
            log.exception("[%s] fetch failed", name)
            await asyncio.sleep(1.0)
            continue
        for msg in msgs:
            await _process(name, msg, handler, terminal)


async def _process(
    name: str,
    msg: object,
    handler: Handler,
    terminal: TerminalMarker,
) -> None:
    """Decode + dispatch one JetStream message; ack/nak based on
    outcome. Crashes inside the handler are caught and nak-ed so
    JetStream redelivers up to ``MAX_DELIVERIES``."""
    try:
        data = json.loads(msg.data.decode("utf-8"))  # type: ignore[attr-defined]
        attachment_id = data["attachment_id"]
    except Exception:
        log.exception("[%s] malformed message; ack-and-drop", name)
        await msg.ack()  # type: ignore[attr-defined]
        return

    metadata = msg.metadata  # type: ignore[attr-defined]
    delivered = getattr(metadata, "num_delivered", 1) or 1

    try:
        await handler(attachment_id)
        await msg.ack()  # type: ignore[attr-defined]
    except Exception:
        log.exception(
            "[%s] failed for %s (delivery %d)",
            name,
            attachment_id,
            delivered,
        )
        if delivered >= MAX_DELIVERIES:
            try:
                await terminal(attachment_id)
            except Exception:
                log.exception(
                    "[%s] terminal-fail mark failed for %s",
                    name,
                    attachment_id,
                )
            await msg.ack()  # type: ignore[attr-defined]
        else:
            await msg.nak(delay=10)  # type: ignore[attr-defined]


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    asyncio.run(_run())


if __name__ == "__main__":
    main()
