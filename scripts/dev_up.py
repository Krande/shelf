"""One-command local development stack — `pixi run up`.

Brings a fresh checkout all the way to a browsable app:

  1. `docker compose up -d --wait`  (Postgres + Redis + Gotenberg + object store)
  2. wait for the store's port      (from the host, not just in-container)
  3. bucket bootstrap               (only the backends that need one)
  4. `alembic upgrade head`         (schema)
  5. `npm install`                  (only when frontend/node_modules is absent)
  6. uvicorn + vite, together, with prefixed interleaved output

Steps 1-5 run to completion in order; the two servers then run until
Ctrl-C, at which point both are torn down. The compose containers are
deliberately left running — they are the slow part to start, and
`pixi run dev-down` stops them when you actually want them gone.

The object store is selectable, since the two implementations are
interchangeable as far as shelf is concerned:

    pixi run up --s3 garage    # default
    pixi run up --s3 rustfs
    pixi run up --s3 none      # metadata-only; uploads will fail

Both publish the S3 API on the same host port with the same bucket, region
and credentials, so switching is a restart and not a reconfiguration.

Every published port is probed before compose runs and bumped to the next
free number when something else on the machine holds it — a docker-desktop
Postgres on :5432, another project's store on :3900. Whatever is chosen is
threaded through to everything that has to agree with it: the compose file,
./.env for the compose commands this script doesn't run, and the backend's
SHELF_* config. A port one of our own containers already publishes is kept
rather than re-picked, so re-running doesn't recreate a working container.

Why a script rather than a chain of pixi tasks: pixi's `depends-on` runs
each dependency to completion, so it can start the services and migrate,
but it cannot hold two long-running servers side by side. Doing it here
also means one Ctrl-C stops both, on Windows as well as Unix.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import hmac
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import NamedTuple

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"
FRONTEND = ROOT / "frontend"

IS_WINDOWS = os.name == "nt"

# Must match the credentials in compose.yaml. Dev throwaways, committed on
# purpose so a fresh checkout needs no setup; both backends are configured
# with the same ones so SHELF_S3_* doesn't change when you switch.
S3_REGION = "us-east-1"
S3_BUCKET = "shelf"
S3_ACCESS_KEY = "SHELFDEVACCESSKEY"
S3_SECRET_KEY = "shelfdevsecretkey"

# Garage creates its bucket from GARAGE_DEFAULT_* on first boot; RustFS has
# no equivalent, so we PUT the bucket ourselves.
S3_BACKENDS = {"garage": False, "rustfs": True, "none": False}


class PortSpec(NamedTuple):
    """One published host port: where it wants to live, and where it lands."""

    service: str        # compose service publishing it
    env_var: str        # ${...} compose.yaml substitutes into the mapping
    default: int        # conventional host port, and compose's own default
    container_port: int # target side of the mapping, fixed by the image
    label: str          # what to call it when it moves


# Ports compose.yaml publishes, as ${VAR:-default} so a plain
# `docker compose up` is unchanged and only this script ever moves them.
#
# service + container_port identify the mapping in `docker compose ps`, which
# is how a port we are already publishing to ourselves is told apart from one
# a stranger holds. Without that distinction every run would find its own
# store on :3900, bump to :3901, then find :3900 free next time and bump back.
CORE_PORTS = (
    PortSpec("postgres", "SHELF_DEV_POSTGRES_PORT", 5432, 5432, "Postgres"),
    PortSpec("redis", "SHELF_DEV_REDIS_PORT", 6379, 6379, "Redis"),
    PortSpec("gotenberg", "SHELF_DEV_GOTENBERG_PORT", 3000, 3000, "Gotenberg"),
    PortSpec("nats", "SHELF_DEV_NATS_PORT", 4222, 4222, "NATS"),
    PortSpec(
        "nats", "SHELF_DEV_NATS_MONITOR_PORT", 8222, 8222, "the NATS monitor"
    ),
)

# Per-store ports, S3 API first — everything downstream (the endpoint, the
# reachability check, the bucket PUT) asks for that one by position. Both
# stores name the same SHELF_DEV_S3_PORT so a switch doesn't move the
# endpoint, even when the container side differs.
STORE_PORTS: dict[str, tuple[PortSpec, ...]] = {
    "garage": (
        PortSpec("garage", "SHELF_DEV_S3_PORT", 3900, 3900, "the S3 API"),
        PortSpec("garage", "SHELF_DEV_GARAGE_ADMIN_PORT", 3903, 3903,
                 "the garage admin API"),
    ),
    "rustfs": (
        PortSpec("rustfs", "SHELF_DEV_S3_PORT", 3900, 9000, "the S3 API"),
        PortSpec("rustfs", "SHELF_DEV_RUSTFS_CONSOLE_PORT", 9001, 9001,
                 "the rustfs console"),
    ),
    "none": (),
}

# What a moved port means for the backend, which reaches the same containers
# from the host. shelf.config and backend/.env both name the conventional
# ports, so without these the API and alembic keep dialling the port the
# container is no longer on.
BACKEND_URLS = {
    "SHELF_DEV_POSTGRES_PORT": (
        "SHELF_DATABASE_URL",
        "postgresql+asyncpg://shelf:shelf@localhost:{port}/shelf",
    ),
    "SHELF_DEV_REDIS_PORT": ("SHELF_REDIS_URL", "redis://localhost:{port}/0"),
    "SHELF_DEV_GOTENBERG_PORT": ("SHELF_GOTENBERG_URL", "http://localhost:{port}"),
}

# SHELF_NATS_URL is handled separately from BACKEND_URLS because that table
# only rewrites a var when its port *moved*, and the queue's config default
# is empty rather than localhost — so on the conventional port it would
# never be set at all, and the API would silently go on not publishing.

# compose reads ./.env by itself, so writing the chosen ports there is what
# keeps `pixi run dev-down`, `dev-logs` and `test-db-up` pointed at the
# containers this script started.
ENV_FILE = ROOT / ".env"
ENV_HEADER = "# Written by `pixi run up`: host ports for the compose services."

# ANSI colours for the two server prefixes. Windows 10+ terminals handle
# these; if the output is redirected to a file we drop them entirely.
_COLOR = sys.stdout.isatty()


def _paint(text: str, code: str) -> str:
    return f"\033[{code}m{text}\033[0m" if _COLOR else text


def _step(msg: str) -> None:
    print(_paint(f"==> {msg}", "1;36"), flush=True)


def _fail(msg: str) -> None:
    print(_paint(f"!!! {msg}", "1;31"), file=sys.stderr, flush=True)


def _note(msg: str) -> None:
    print(_paint(f"    {msg}", "0;33"), flush=True)


def _run(cmd: list[str], cwd: Path, env: dict[str, str] | None = None) -> int:
    """Run a step to completion, streaming its output straight through."""
    print(_paint(f"    $ {' '.join(cmd)}", "2;37"), flush=True)
    return subprocess.run(cmd, cwd=cwd, env=env).returncode


def _port_in_use(port: int) -> bool:
    """True if something is already listening on localhost:port.

    A connect probe rather than a trial bind, for two reasons. Windows lets
    you bind 127.0.0.1:8000 while another socket holds 0.0.0.0:8000, so a
    bind succeeds against a port that is very much in use. And every address
    localhost resolves to is tried, because vite binds [::1] only — checking
    IPv4 alone reports :5173 free while another project's dev server has it.
    """
    try:
        infos = socket.getaddrinfo("localhost", port, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        return False
    for family, socktype, proto, _canon, addr in infos:
        with socket.socket(family, socktype, proto) as s:
            s.settimeout(0.4)
            if s.connect_ex(addr) == 0:
                return True
    return False


def _free_port(start: int, tries: int = 20, avoid: set[int] | None = None) -> int:
    """First free port at or after `start`, skipping anything in `avoid`.

    Mirrors what vite does on its own when :5173 is busy, but doing it here
    means the port is known before launch — so the URL we print, the CORS
    origin and the proxy target all agree with where the servers actually
    end up. Falls back to `start` and lets the server report the conflict if
    the whole range is taken.

    `avoid` carries the ports already handed to an earlier service in this
    same run: nothing is listening on them yet, so the probe would happily
    hand the same number out twice and compose would fail on the second bind.
    """
    for port in range(start, start + tries):
        if (avoid is None or port not in avoid) and not _port_in_use(port):
            return port
    return start


def _available_consumers() -> tuple[list[str], str | None]:
    """Which worker consumers this machine can actually run.

    `extract` (pypdf) and `outline` (PyMuPDF) are pure-Python and always
    available. `ocr` shells out to Tesseract and Ghostscript, which
    pixi.toml only installs on linux-64 — the Python package is there on
    every platform, so the import succeeds and the failure would only
    show up mid-job, on the first scanned PDF someone tries.

    Returns the consumer list plus a note to print when OCR was left out,
    so "OCR did nothing" is answered before it's asked.
    """
    consumers = ["extract", "outline"]
    missing = [
        binary for binary in ("tesseract", "gs") if shutil.which(binary) is None
    ]
    if not missing:
        consumers.insert(1, "ocr")
        return consumers, None
    return consumers, (
        f"OCR consumer off — {' and '.join(missing)} not on PATH. Text "
        f"extraction and outlines still run; scanned PDFs stay un-OCR'd."
    )


def _docker_compose() -> list[str] | None:
    """Resolve the compose entrypoint: the v2 plugin, else the v1 binary."""
    if shutil.which("docker"):
        probe = subprocess.run(
            ["docker", "compose", "version"],
            capture_output=True,
        )
        if probe.returncode == 0:
            return ["docker", "compose"]
    if shutil.which("docker-compose"):
        return ["docker-compose"]
    return None


def _wait_for_port(port: int, *, want_open: bool, timeout: float) -> bool:
    """Block until localhost:port is (or isn't) accepting connections."""
    deadline = time.monotonic() + timeout
    while True:
        if _port_in_use(port) == want_open:
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.5)


# ── Host ports ───────────────────────────────────────────────────────────────


def _compose_ps(compose: list[str]) -> list[dict]:
    """This project's running containers, as compose describes them.

    Best-effort: compose v1 has no `--format json`, and a daemon that isn't
    running fails here just as it will fail louder at `up`. Both cases return
    nothing, which only means every port gets picked from scratch.
    """
    proc = subprocess.run(
        [*compose, "--profile", "garage", "--profile", "rustfs",
         "ps", "--format", "json"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    out = proc.stdout.strip()
    if proc.returncode != 0 or not out:
        return []
    try:
        parsed = json.loads(out)
    except json.JSONDecodeError:
        # Compose used to print one object per line instead of an array.
        rows = []
        for line in out.splitlines():
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                pass
        return rows
    if isinstance(parsed, dict):
        return [parsed]
    return parsed if isinstance(parsed, list) else []


def _published_ports(rows: list[dict]) -> dict[tuple[str, int], int]:
    """{(service, container port): host port} for what is running right now."""
    published: dict[tuple[str, int], int] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        if str(row.get("State", "")).lower() != "running":
            continue
        service = row.get("Service")
        for pub in row.get("Publishers") or []:
            host, target = pub.get("PublishedPort"), pub.get("TargetPort")
            # An unpublished port shows up with PublishedPort 0.
            if service and host and target:
                published[(str(service), int(target))] = int(host)
    return published


def _resolve_ports(
    specs: tuple[PortSpec, ...], published: dict[tuple[str, int], int]
) -> dict[PortSpec, int]:
    """Pick a host port per spec: keep ours, bump past everyone else's."""
    chosen: dict[PortSpec, int] = {}
    taken: set[int] = set()
    for spec in specs:
        ours = published.get((spec.service, spec.container_port))
        if ours is not None and ours not in taken:
            # In use, but by the container we are about to reuse. Re-picking
            # would recreate a perfectly good container onto a new port.
            port = ours
        else:
            port = _free_port(spec.default, avoid=taken)
        chosen[spec] = port
        taken.add(port)
    return chosen


def _write_compose_env(port_env: dict[str, str]) -> None:
    """Record the chosen ports in ./.env, which compose loads on its own.

    Otherwise only the compose commands *this* script runs know where the
    containers are: `pixi run dev-down`, `dev-logs` and `test-db-up` would
    substitute the defaults back in and address containers that don't exist,
    or try to recreate them onto the ports that were busy to begin with.

    Lines we don't own are kept — the file is gitignored and may hold other
    local settings. A key left behind by the store that isn't running now is
    one of them: it names a port nothing publishes, which costs nothing, and
    it is rewritten the next time that store is selected.
    """
    managed = set(port_env)
    kept: list[str] = []
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            key = line.split("=", 1)[0].strip()
            if key in managed or line.strip() == ENV_HEADER:
                continue
            kept.append(line)
    while kept and not kept[-1].strip():
        kept.pop()
    if kept:
        kept.append("")
    body = [ENV_HEADER] + [f"{k}={v}" for k, v in sorted(port_env.items())]
    ENV_FILE.write_text("\n".join(kept + body) + "\n", encoding="utf-8")


# ── S3 bucket bootstrap ──────────────────────────────────────────────────────
# Two signed requests — `PUT /{bucket}` and `PUT /{bucket}?cors` — hand-rolled
# rather than pulling in boto3 for them: shelf's own S3 access goes through
# obstore, which has neither call, and a dev-only bootstrap doesn't justify a
# new dependency. Only single-valued queries and an in-memory payload are
# handled, which is all these two need.


def _sigv4_headers(
    *,
    method: str,
    host: str,
    path: str,
    region: str,
    access_key: str,
    secret_key: str,
    query: str = "",
    payload: bytes = b"",
) -> dict[str, str]:
    now = dt.datetime.now(dt.timezone.utc)
    amz_date = now.strftime("%Y%m%dT%H%M%SZ")
    datestamp = now.strftime("%Y%m%d")
    payload_hash = hashlib.sha256(payload).hexdigest()

    headers = {
        "host": host,
        "x-amz-content-sha256": payload_hash,
        "x-amz-date": amz_date,
    }
    signed_headers = ";".join(sorted(headers))
    canonical_headers = "".join(f"{k}:{headers[k]}\n" for k in sorted(headers))
    canonical_request = (
        f"{method}\n{path}\n{query}\n"
        f"{canonical_headers}\n{signed_headers}\n{payload_hash}"
    )

    scope = f"{datestamp}/{region}/s3/aws4_request"
    string_to_sign = (
        "AWS4-HMAC-SHA256\n"
        f"{amz_date}\n"
        f"{scope}\n"
        f"{hashlib.sha256(canonical_request.encode()).hexdigest()}"
    )

    def _sign(key: bytes, msg: str) -> bytes:
        return hmac.new(key, msg.encode(), hashlib.sha256).digest()

    k = _sign(f"AWS4{secret_key}".encode(), datestamp)
    k = _sign(k, region)
    k = _sign(k, "s3")
    k = _sign(k, "aws4_request")
    signature = hmac.new(k, string_to_sign.encode(), hashlib.sha256).hexdigest()

    return {
        # host is signed but not returned: urllib sets it from the URL, and it
        # has to stay byte-identical to what went into the signature.
        "x-amz-date": amz_date,
        "x-amz-content-sha256": payload_hash,
        "Authorization": (
            f"AWS4-HMAC-SHA256 Credential={access_key}/{scope}, "
            f"SignedHeaders={signed_headers}, Signature={signature}"
        ),
    }


def _ensure_bucket(endpoint: str) -> bool:
    """Create the dev bucket, tolerating the already-exists responses."""
    host = endpoint.split("://", 1)[1]
    path = f"/{S3_BUCKET}"
    headers = _sigv4_headers(
        method="PUT",
        host=host,
        path=path,
        region=S3_REGION,
        access_key=S3_ACCESS_KEY,
        secret_key=S3_SECRET_KEY,
    )
    req = urllib.request.Request(
        f"{endpoint}{path}", method="PUT", data=b"", headers=headers
    )
    try:
        with urllib.request.urlopen(req, timeout=15):
            return True
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        # S3 semantics: re-creating your own bucket is a success, and both
        # backends report it as one of these rather than 200.
        if e.code == 409 or "BucketAlreadyOwnedByYou" in body or (
            "BucketAlreadyExists" in body
        ):
            return True
        _fail(f"could not create bucket '{S3_BUCKET}': HTTP {e.code} {body[:300]}")
        return False
    except OSError as e:
        _fail(f"could not reach the object store at {endpoint}: {e}")
        return False


# Uploads and downloads go straight from the SPA to a presigned URL, which is
# cross-origin however the stack is arranged: vite and the store are different
# ports, and a moved port only makes it more so. Garage starts with no CORS
# rules at all, so the browser's preflight OPTIONS comes back 403 and the
# upload fails as an opaque "Failed to fetch" before any of shelf's own code
# runs. `*` is defensible only because this store is a localhost throwaway.
CORS_RULES = b"""<?xml version="1.0" encoding="UTF-8"?>
<CORSConfiguration>
  <CORSRule>
    <AllowedOrigin>*</AllowedOrigin>
    <AllowedMethod>GET</AllowedMethod>
    <AllowedMethod>PUT</AllowedMethod>
    <AllowedMethod>POST</AllowedMethod>
    <AllowedMethod>HEAD</AllowedMethod>
    <AllowedMethod>DELETE</AllowedMethod>
    <AllowedHeader>*</AllowedHeader>
    <ExposeHeader>ETag</ExposeHeader>
    <MaxAgeSeconds>3000</MaxAgeSeconds>
  </CORSRule>
</CORSConfiguration>
"""


def _ensure_cors(endpoint: str) -> None:
    """Put the browser-upload CORS rule on the dev bucket.

    Advisory: a store that answers preflights permissively on its own (or
    doesn't implement PutBucketCors) is not a reason to refuse to start, and
    the symptom — should there be one — shows up at the first upload with a
    message that now has something to point at.
    """
    host = endpoint.split("://", 1)[1]
    path = f"/{S3_BUCKET}"
    headers = _sigv4_headers(
        method="PUT",
        host=host,
        path=path,
        region=S3_REGION,
        access_key=S3_ACCESS_KEY,
        secret_key=S3_SECRET_KEY,
        query="cors=",
        payload=CORS_RULES,
    )
    headers["Content-Type"] = "application/xml"
    req = urllib.request.Request(
        f"{endpoint}{path}?cors", method="PUT", data=CORS_RULES, headers=headers
    )
    try:
        with urllib.request.urlopen(req, timeout=15):
            return
    except urllib.error.HTTPError as e:
        detail = f"HTTP {e.code} {e.read().decode('utf-8', 'replace')[:200]}"
    except OSError as e:
        detail = str(e)
    _note(
        f"could not set the bucket CORS rule ({detail}) — browser uploads may "
        f"fail their preflight"
    )


# ── Long-running servers ─────────────────────────────────────────────────────


# How many times a server may die and be brought back, and the window the
# count decays over. Generous enough to absorb a reloader that exits on a
# bad edit, tight enough that a server failing on startup — a port that
# really is taken, a missing dependency — still stops instead of spinning.
RESTART_LIMIT = 5
RESTART_WINDOW = 60.0

# How long after a child announces a reload we treat an incoming SIGINT as
# that reload's doing rather than the user's.
#
# On Windows, uvicorn's reloader stops its worker with a console control
# event, and `GenerateConsoleCtrlEvent` goes to every process attached to
# the console — this script included. So every single `--reload` cycle
# delivered us a SIGINT and took the whole stack down with it, which is
# exactly what a Ctrl-C looks like from here.
#
# The two are genuinely indistinguishable by signal alone, so we go on
# timing: a SIGINT within this window of "Reloading..." is the reloader.
# A user who really did mean it presses Ctrl-C again a moment later and
# gets what they asked for.
RELOAD_SIGINT_GRACE = 5.0
_RELOAD_ANNOUNCEMENTS = ("Reloading...", "WatchFiles detected changes")


class Server:
    """A child process whose output is echoed with a coloured prefix."""

    def __init__(
        self,
        name: str,
        cmd: list[str],
        cwd: Path,
        color: str,
        env: dict[str, str] | None = None,
        announce: re.Pattern[str] | None = None,
    ) -> None:
        self.name = name
        self.cmd = cmd
        self.cwd = cwd
        self.env = env
        self.announce = announce
        self.prefix = _paint(f"[{name}]", color)
        self.proc: subprocess.Popen[str] | None = None
        # Timestamps of recent restarts, used to decide whether this one is
        # a blip worth absorbing or a server that cannot start at all.
        self.restarts: list[float] = []
        # When this server last said it was reloading. Read by the SIGINT
        # handler — see RELOAD_SIGINT_GRACE.
        self.reload_announced_at: float | None = None
        # True while the watcher is deliberately cycling this server.
        # Without it the supervisor sees the stopped process, calls that
        # a crash, and restarts it as well — two restarts per edit, and
        # the restart budget exhausted in a handful of saves.
        self.restarting = False

    def start(self) -> None:
        # Never leak the previous process. Overwriting `self.proc` while
        # the old one is still alive loses the only handle we have on it,
        # and it goes on holding the port — which then bumps the next run
        # to :8001 and leaves a stale server answering on :8000.
        if self.proc is not None and self.proc.poll() is None:
            self.stop()

        # A new process group (Unix) / no CTRL_C propagation (Windows) keeps
        # our own Ctrl-C handler in charge of the shutdown order instead of
        # the console signalling every child at once.
        kwargs: dict[str, object] = {}
        if IS_WINDOWS:
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            kwargs["start_new_session"] = True

        self.proc = subprocess.Popen(
            self.cmd,
            cwd=self.cwd,
            env=self.env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            encoding="utf-8",
            errors="replace",
            **kwargs,  # type: ignore[arg-type]
        )
        threading.Thread(target=self._pump, daemon=True).start()

    def may_restart(self) -> bool:
        """True if this server has restarts left in the current window."""
        now = time.monotonic()
        self.restarts = [t for t in self.restarts if now - t < RESTART_WINDOW]
        if len(self.restarts) >= RESTART_LIMIT:
            return False
        self.restarts.append(now)
        return True

    def _pump(self) -> None:
        assert self.proc is not None and self.proc.stdout is not None
        announced = False
        for line in self.proc.stdout:
            print(f"{self.prefix} {line.rstrip()}", flush=True)
            if any(marker in line for marker in _RELOAD_ANNOUNCEMENTS):
                self.reload_announced_at = time.monotonic()
            if self.announce is not None and not announced:
                m = self.announce.search(line)
                if m:
                    announced = True
                    url = m.group(1).rstrip("/")
                    print(
                        _paint(
                            f"\n==> Open {url} — with no OIDC provider configured, "
                            "the login page offers the dev-login form.\n",
                            "1;36",
                        ),
                        flush=True,
                    )

    def stop(self) -> None:
        proc = self.proc
        if proc is None or proc.poll() is not None:
            return
        if IS_WINDOWS:
            # npm/vite and uvicorn's reloader both spawn grandchildren, and
            # terminate() only reaches the direct child. taskkill /T walks
            # the tree so nothing is left holding :5173 or :8000.
            subprocess.run(
                ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                capture_output=True,
            )
        else:
            proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


def _watch_and_restart(
    server: Server, root: Path, port: int, stopping: threading.Event
) -> None:
    """Restart `server` whenever anything under `root` changes.

    This script owns the file watching rather than delegating it to
    `uvicorn --reload`, for one reason: when uvicorn's watcher stops —
    which it does on Windows, and did repeatedly here — the server goes
    on serving happily with the code it had. Nothing looks wrong. You
    edit a file, reload the page, and read a stale answer, and the only
    way to find out is to notice a route you just wrote returning 404.

    Owning it costs a full process restart per edit instead of an
    in-process one (a second or so), and buys a reload that either
    announces itself or has visibly crashed. Every restart prints what
    triggered it, so a reload storm names the file causing it.
    """
    from watchfiles import watch

    def is_source(_change: object, path: str) -> bool:
        """Only Python sources. watchfiles already skips `__pycache__`,
        but an editor's swap file or a stray artifact under src/ would
        otherwise restart the API for nothing — and a restart triggered
        by a restart is an infinite loop, not a slow reload."""
        return path.endswith(".py")

    try:
        for changes in watch(
            root,
            watch_filter=is_source,
            stop_event=stopping,
            debounce=400,
            step=50,
        ):
            if stopping.is_set():
                return
            names = sorted({Path(p).name for _kind, p in changes})
            shown = ", ".join(names[:3]) + ("…" if len(names) > 3 else "")
            _note(f"{shown} changed — restarting the API")
            # Tell the supervisor this stop is on purpose, or it counts
            # the exit as a crash and restarts the server a second time.
            server.restarting = True
            try:
                server.stop()
                # taskkill returns before Windows has torn the listener
                # down, and the replacement would then fail to bind. Wait
                # for the port to go quiet; carry on regardless if it
                # doesn't, so a stuck socket doesn't end the watch loop.
                _wait_for_port(port, want_open=False, timeout=10)
                server.start()
            finally:
                server.restarting = False
    except Exception as e:
        # A dead watcher must not be silent — that is the whole bug this
        # replaced. The stack stays up and usable; it just stops
        # reloading, and now says so.
        _fail(
            f"file watcher stopped ({type(e).__name__}: {e}). The API will "
            f"keep serving but will no longer pick up edits — restart "
            f"`pixi run up` to get reloading back."
        )


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="pixi run up", description="Start the full local development stack."
    )
    parser.add_argument(
        "--s3",
        choices=sorted(S3_BACKENDS),
        default="garage",
        help=(
            "object store to run: 'garage' (default), 'rustfs', or 'none' to "
            "skip it — metadata works, uploads and downloads don't."
        ),
    )
    # Defaults are None so an explicit port can be told apart from an
    # unspecified one: given a port we use exactly it and let the server
    # complain if it's taken, otherwise we scan upward from the usual one.
    # :5173 is *the* vite port, so a second frontend checkout is often
    # already sitting on it.
    parser.add_argument(
        "--web-port",
        type=int,
        help="vite dev server port (default: first free from 5173)",
    )
    parser.add_argument(
        "--api-port",
        type=int,
        help="backend port (default: first free from 8000)",
    )
    parser.add_argument(
        "--no-worker",
        action="store_true",
        help=(
            "don't run the background worker. Uploads still work; PDFs just "
            "never get their text extracted, so full-text search won't see "
            "them."
        ),
    )
    parser.add_argument(
        "--no-watch",
        action="store_true",
        help=(
            "don't restart the API when backend source changes. Useful when "
            "running a long job you don't want interrupted by an editor save."
        ),
    )
    parser.add_argument(
        "--consumers",
        help=(
            "comma-separated worker consumers (extract, ocr, outline). "
            "Default: everything this machine has the binaries for."
        ),
    )
    args = parser.parse_args()

    # vite announces itself with a U+279C arrow. On Windows a redirected
    # stdout defaults to cp1252, which raises on it — inside the pump thread,
    # where it kills the echo for that server and leaves it running blind.
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8", errors="replace")

    api_port = args.api_port if args.api_port else _free_port(8000)
    web_port = args.web_port if args.web_port else _free_port(5173)
    for label, chosen, default in (
        ("frontend", web_port, 5173),
        ("backend", api_port, 8000),
    ):
        if chosen != default:
            _note(f"{default} is in use — running the {label} on {chosen}")

    compose = _docker_compose()
    if compose is None:
        _fail("docker not found on PATH — install Docker and start the daemon.")
        return 1

    # What the project is publishing right now, read before anything is
    # stopped or started. Used twice below: to keep the ports that are already
    # ours, and to know which ones a stopped store still has to release.
    published = _published_ports(_compose_ps(compose))

    # 1. Services. `--wait` blocks on the healthchecks in compose.yaml, so
    #    the migration below never races a Postgres that is still booting.
    #
    #    The unselected store is stopped first: both publish the same S3 port,
    #    so leaving the previous one up makes the new one fail to bind — with
    #    a port conflict rather than anything that points at the actual cause.
    releasing: list[int] = []
    for other in S3_BACKENDS:
        if other not in ("none", args.s3):
            subprocess.run(
                [*compose, "--profile", other, "stop", other],
                cwd=ROOT,
                capture_output=True,
            )
            for key in [k for k in published if k[0] == other]:
                releasing.append(published.pop(key))

    # `docker compose stop` returns before the host-side port forward is
    # necessarily gone. Starting the replacement into that window leaves it
    # running and passing its healthcheck with no published port at all —
    # the check after `up` catches that, but waiting avoids provoking it.
    # It also keeps the scan below from stepping around a port that is on its
    # way to being free and bumping the new store for no reason.
    if args.s3 != "none":
        for port in releasing:
            _wait_for_port(port, want_open=False, timeout=15)

    # Host ports, decided before compose gets a chance to fail on a busy one.
    # Anything held by something else on the machine — another project's
    # Postgres, a system Redis — moves up to the next free number.
    chosen = _resolve_ports((*CORE_PORTS, *STORE_PORTS[args.s3]), published)
    for spec, port in chosen.items():
        if port != spec.default:
            _note(f"{spec.default} is in use — publishing {spec.label} on {port}")

    # The one moved port this script can't paper over: `pixi run test` reads
    # backend/.env directly, and its DSN still names the port compose gave up.
    pg = CORE_PORTS[0]
    if chosen[pg] != pg.default:
        _note(f"`pixi run test` reads backend/.env — set SHELF_DATABASE_URL "
              f"there to :{chosen[pg]} before running the suite")

    port_env = {spec.env_var: str(port) for spec, port in chosen.items()}
    _write_compose_env(port_env)
    compose_env = {**os.environ, **port_env}

    # The S3 API is the first spec of whichever store was selected.
    s3_port = chosen[STORE_PORTS[args.s3][0]] if args.s3 != "none" else 0
    s3_endpoint = f"http://localhost:{s3_port}"

    profile = [] if args.s3 == "none" else ["--profile", args.s3]
    store_label = "no object store" if args.s3 == "none" else args.s3
    _step(f"Starting Postgres, Redis, Gotenberg and {store_label}")
    if _run([*compose, *profile, "up", "-d", "--wait"], cwd=ROOT, env=compose_env) != 0:
        _fail("compose failed — is the Docker daemon running?")
        return 1

    # 2. The store has to be reachable *from the host*, which is not what
    #    `--wait` proves: the healthchecks run inside the container against
    #    127.0.0.1, so one whose published port never got established still
    #    reports healthy. That happens when it starts while the previous
    #    store is still releasing :3900 — compose reuses the existing
    #    container and the binding is silently lost. Recreating re-establishes
    #    it. Every presigned URL points at the host, so without this check the
    #    failure surfaces much later as a browser upload error.
    if args.s3 != "none" and not _wait_for_port(s3_port, want_open=True, timeout=30):
        _note(f"{args.s3} is up but :{s3_port} isn't — recreating it")
        recreate = [*compose, "--profile", args.s3, "up", "-d", "--wait",
                    "--force-recreate", args.s3]
        if _run(recreate, cwd=ROOT, env=compose_env) != 0 or not _wait_for_port(
            s3_port, want_open=True, timeout=30
        ):
            _fail(
                f"{args.s3} never published :{s3_port}. "
                f"Try `pixi run dev-down`, then run this again."
            )
            return 1

    # 3. Bucket. Garage's --default-bucket already made one; RustFS starts
    #    empty, and a presigned PUT into a missing bucket just 404s.
    if S3_BACKENDS[args.s3]:
        _step(f"Creating the '{S3_BUCKET}' bucket")
        if not _ensure_bucket(s3_endpoint):
            return 1

    # Not gated on the bucket bootstrap above: garage makes its own bucket and
    # still needs the rule, and both stores need it again if it was ever lost
    # with the volume.
    if args.s3 != "none":
        _step("Allowing browser uploads straight to the store (bucket CORS)")
        _ensure_cors(s3_endpoint)

    # A moved container port has to reach the backend's config too: both
    # shelf.config's defaults and backend/.env name the conventional ones, so
    # alembic and uvicorn would otherwise dial a port nothing is on any more.
    # Only the ports that actually moved are set, leaving an unconflicted
    # stack configured exactly as it was before. An environment variable
    # outranks backend/.env in pydantic-settings, so these win where it counts
    # — but one exported in the calling shell outranks us in turn, which is
    # worth saying out loud rather than letting it fail at the first query.
    backend_env = os.environ.copy()
    for spec, port in chosen.items():
        override = BACKEND_URLS.get(spec.env_var)
        if override is None or port == spec.default:
            continue
        var, template = override
        exported = os.environ.get(var)
        if exported is not None:
            if f":{port}" not in exported:
                _note(f"{var} is set in your environment and doesn't name "
                      f":{port} — leaving it as you set it")
            continue
        backend_env[var] = template.format(port=port)

    # The job queue. Unlike the vars above this is set whether or not the
    # port moved: shelf.config defaults `nats_url` to "" and treats empty
    # as "don't publish", so leaving it unset means uploads enqueue
    # nothing and every PDF sits at extraction_status='pending' forever.
    nats_port = chosen[CORE_PORTS[3]]
    nats_url = f"nats://localhost:{nats_port}"
    if os.environ.get("SHELF_NATS_URL"):
        _note("SHELF_NATS_URL is set in your environment — leaving it alone")
    else:
        backend_env["SHELF_NATS_URL"] = nats_url

    # 4. Schema.
    _step("Applying migrations")
    if _run(["alembic", "upgrade", "head"], cwd=BACKEND, env=backend_env) != 0:
        _fail("alembic upgrade failed")
        return 1

    # 5. Frontend deps, only when they are missing. Re-running npm install
    #    on every `up` would add ~10s to a loop that is otherwise instant.
    npm = "npm.cmd" if IS_WINDOWS else "npm"
    if not (FRONTEND / "node_modules").is_dir():
        _step("Installing frontend dependencies (first run)")
        if _run([npm, "install"], cwd=FRONTEND) != 0:
            _fail("npm install failed")
            return 1

    # 6. Both servers, until Ctrl-C.
    #
    # The API child is told where the object store is. setdefault, not
    # assignment: an SHELF_S3_* already exported in the calling shell (or set
    # in backend/.env) is the developer pointing at their own store, and it
    # wins. With --s3 none nothing is set, so the config defaults stand and
    # upload calls fail at request time rather than at startup.
    api_env = backend_env.copy()
    if args.s3 != "none":
        for var, value in (
            ("SHELF_S3_ENDPOINT", s3_endpoint),
            ("SHELF_S3_REGION", S3_REGION),
            ("SHELF_S3_BUCKET", S3_BUCKET),
            ("SHELF_S3_ACCESS_KEY_ID", S3_ACCESS_KEY),
            ("SHELF_S3_SECRET_ACCESS_KEY", S3_SECRET_KEY),
        ):
            api_env.setdefault(var, value)
    # Only matters if the SPA ever calls the API cross-origin; through the
    # vite proxy it's same-origin. Kept in step with --web-port so a moved
    # frontend doesn't leave a stale allowlist behind either way.
    api_env.setdefault("SHELF_CORS_ORIGINS", f'["http://localhost:{web_port}"]')

    # A fresh checkout has no OIDC provider and therefore no way to reach
    # SHELF_ADMIN_EMAILS, so every dev-login account would be a plain user
    # and the Admin tab would never appear. Local dev is exactly where you
    # want to see it. setdefault, so an explicit "user" in the shell or in
    # backend/.env still wins — and the shipped default stays "user", since
    # dev login mints a session for any address presented.
    api_env.setdefault("SHELF_DEV_LOGIN_ROLE", "admin")
    _note(
        f"Dev-login accounts get the "
        f"{api_env['SHELF_DEV_LOGIN_ROLE']!r} role "
        f"(SHELF_DEV_LOGIN_ROLE)"
    )

    # vite.config.ts reads this to point its /api and /auth proxy at the
    # backend; without it a moved api port would leave the proxy talking to
    # :8000 and every request 502-ing.
    web_env = os.environ.copy()
    web_env["SHELF_DEV_API_PORT"] = str(api_port)

    # The worker reads the same database and object store as the API —
    # SHELF_WORKER_BACKEND defaults to "sql", which is the in-cluster
    # mode and exactly right for a local stack. api_env already carries
    # the store credentials and the queue URL, so it's the right base.
    worker_env = api_env.copy()
    consumers, ocr_note = (
        ([c.strip() for c in args.consumers.split(",") if c.strip()], None)
        if args.consumers
        else _available_consumers()
    )
    worker_env["SHELF_WORKER_CONSUMERS"] = ",".join(consumers)

    servers = [
        Server(
            "api",
            [
                "uvicorn",
                "shelf.main:app",
                # Deliberately NOT --reload; this script does the watching
                # (see _watch_and_restart). uvicorn's own reloader has a
                # failure mode on Windows where the watcher thread stops
                # but the server keeps serving, so the stack looks healthy
                # and quietly answers with stale code — which is far worse
                # than a reload that visibly doesn't happen.
                "--host",
                "0.0.0.0",
                "--port",
                str(api_port),
            ],
            BACKEND,
            "1;32",
            env=api_env,
        ),
        Server(
            "web",
            [npm, "run", "dev", "--", "--port", str(web_port)],
            FRONTEND,
            "1;35",
            env=web_env,
            # vite still picks the next port itself if ours was taken in the
            # gap between the scan and the bind. Echo the URL from its own
            # ready line so what we print is what actually happened.
            announce=re.compile(r"Local:\s+(http://\S+)"),
        ),
    ]

    if not args.no_worker:
        servers.append(
            Server(
                "worker",
                [sys.executable, "-m", "shelf.worker"],
                BACKEND,
                "1;34",
                env=worker_env,
            )
        )

    if args.no_worker:
        _note(
            "worker disabled — uploads will sit at extraction_status="
            "'pending' and full-text search won't see them"
        )
    else:
        _note(f"worker consumers: {', '.join(consumers)}")
        if ocr_note:
            _note(ocr_note)

    # Anything that was uploaded while no queue was running has a row but
    # no message — nothing ever told the worker about it. Re-publishing is
    # idempotent (WorkQueue retention drops the duplicate once acked), so
    # it's safe to do on every start and it means a stack that gains a
    # worker catches up on its backlog instead of quietly ignoring it.
    if not args.no_worker:
        _step("Re-queueing attachments that never got extracted")
        _run(
            [sys.executable, "-m", "shelf.scripts.backfill_extract"],
            cwd=BACKEND,
            env=worker_env,
        )

    running = ", ".join(
        [f"backend (:{api_port})", f"frontend (:{web_port})"]
        + ([] if args.no_worker else ["worker"])
    )
    _step(f"Starting {running} — Ctrl-C to stop")

    for s in servers:
        s.start()

    stopping = threading.Event()

    # Watch the backend source and restart the API on change. Started
    # after the servers so the first restart can't race the first start,
    # and daemon so Ctrl-C doesn't wait on it.
    if not args.no_watch:
        api_server = servers[0]
        threading.Thread(
            target=_watch_and_restart,
            args=(api_server, BACKEND / "src", api_port, stopping),
            daemon=True,
        ).start()
        _note(f"watching {BACKEND / 'src'} — the API restarts on edit")
    else:
        _note("--no-watch: the API will not pick up code edits")

    def _reloading_now() -> Server | None:
        """The server that announced a reload within the grace window."""
        now = time.monotonic()
        for s in servers:
            at = s.reload_announced_at
            if at is not None and now - at < RELOAD_SIGINT_GRACE:
                return s
        return None

    def _shutdown(reason: object = None, *_: object) -> None:
        if stopping.is_set():
            return

        # A SIGINT that lands in the shadow of a reload is the reloader's
        # console control event reaching us by accident, not the user.
        # Swallow it — taking the stack down on every edit is the bug this
        # exists to stop.
        if isinstance(reason, int) and reason == int(signal.SIGINT):
            culprit = _reloading_now()
            if culprit is not None:
                culprit.reload_announced_at = None
                _note(
                    f"ignoring the SIGINT from {culprit.name}'s reload "
                    f"(press Ctrl-C again to stop)"
                )
                return

        stopping.set()
        print()
        if isinstance(reason, int):
            try:
                name = signal.Signals(reason).name
            except ValueError:
                name = str(reason)
            _note(f"received {name}")
        _step("Shutting down servers (containers stay up; `pixi run dev-down`)")
        for s in servers:
            s.stop()

    signal.signal(signal.SIGINT, _shutdown)
    if IS_WINDOWS:
        signal.signal(signal.SIGBREAK, _shutdown)
    else:
        signal.signal(signal.SIGTERM, _shutdown)

    try:
        # A server that dies on its own is brought back rather than taking
        # the other one with it. `uvicorn --reload` handles a bad edit by
        # itself and its parent is supposed to outlive every reload, but
        # when it doesn't — and on Windows it sometimes doesn't — losing
        # vite, the vite port and every bit of browser state along with it
        # is a bad trade for a typo you're about to fix anyway.
        #
        # Repeated failures inside RESTART_WINDOW do stop the stack: that
        # is a server which cannot start, and restarting it forever would
        # bury the reason under its own output.
        while not stopping.is_set():
            for s in servers:
                # Mid-restart by the watcher: the exit is expected and
                # the replacement is already on its way.
                if s.restarting:
                    continue
                if s.proc is None or s.proc.poll() is None:
                    continue
                code = s.proc.returncode
                if not s.may_restart():
                    _fail(
                        f"{s.name} exited with code {code} and has restarted "
                        f"{RESTART_LIMIT} times in the last "
                        f"{int(RESTART_WINDOW)}s — giving up"
                    )
                    _shutdown()
                    return code or 1
                _note(f"{s.name} exited with code {code} — restarting it")
                s.start()
            time.sleep(0.3)
    except KeyboardInterrupt:
        _shutdown()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
