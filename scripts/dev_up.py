"""One-command local development stack — `pixi run up`.

Brings a fresh checkout all the way to a browsable app:

  1. `docker compose up -d --wait`  (Postgres + Redis + Gotenberg + object store)
  2. bucket bootstrap               (only the backends that need one)
  3. `alembic upgrade head`         (schema)
  4. `npm install`                  (only when frontend/node_modules is absent)
  5. uvicorn + vite, together, with prefixed interleaved output

Steps 1-4 run to completion in order; the two servers then run until
Ctrl-C, at which point both are torn down. The compose containers are
deliberately left running — they are the slow part to start, and
`pixi run dev-down` stops them when you actually want them gone.

The object store is selectable, since the two implementations are
interchangeable as far as shelf is concerned:

    pixi run up --s3 garage    # default
    pixi run up --s3 rustfs
    pixi run up --s3 none      # metadata-only; uploads will fail

Both publish the S3 API on :3900 with the same bucket, region and
credentials, so switching is a restart and not a reconfiguration.

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

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"
FRONTEND = ROOT / "frontend"

IS_WINDOWS = os.name == "nt"

# Must match the credentials in compose.yaml. Dev throwaways, committed on
# purpose so a fresh checkout needs no setup; both backends are configured
# with the same ones so SHELF_S3_* doesn't change when you switch.
S3_ENDPOINT = "http://localhost:3900"
S3_REGION = "us-east-1"
S3_BUCKET = "shelf"
S3_ACCESS_KEY = "SHELFDEVACCESSKEY"
S3_SECRET_KEY = "shelfdevsecretkey"

# Garage creates its bucket from GARAGE_DEFAULT_* on first boot; RustFS has
# no equivalent, so we PUT the bucket ourselves.
S3_BACKENDS = {"garage": False, "rustfs": True, "none": False}

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


def _run(cmd: list[str], cwd: Path) -> int:
    """Run a step to completion, streaming its output straight through."""
    print(_paint(f"    $ {' '.join(cmd)}", "2;37"), flush=True)
    return subprocess.run(cmd, cwd=cwd).returncode


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


def _free_port(start: int, tries: int = 20) -> int:
    """First free port at or after `start`.

    Mirrors what vite does on its own when :5173 is busy, but doing it here
    means the port is known before launch — so the URL we print, the CORS
    origin and the proxy target all agree with where the servers actually
    end up. Falls back to `start` and lets the server report the conflict if
    the whole range is taken.
    """
    for port in range(start, start + tries):
        if not _port_in_use(port):
            return port
    return start


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


# ── S3 bucket bootstrap ──────────────────────────────────────────────────────
# A `PUT /{bucket}` signed with SigV4. Hand-rolled rather than pulling in
# boto3 for one request: shelf's own S3 access goes through obstore, which
# has no create-bucket call, and a dev-only bootstrap doesn't justify a new
# dependency. Only the empty-payload, no-query case is handled, which is all
# CreateBucket needs.


def _sigv4_headers(
    *, method: str, host: str, path: str, region: str, access_key: str, secret_key: str
) -> dict[str, str]:
    now = dt.datetime.now(dt.timezone.utc)
    amz_date = now.strftime("%Y%m%dT%H%M%SZ")
    datestamp = now.strftime("%Y%m%d")
    payload_hash = hashlib.sha256(b"").hexdigest()

    canonical_headers = (
        f"host:{host}\n"
        f"x-amz-content-sha256:{payload_hash}\n"
        f"x-amz-date:{amz_date}\n"
    )
    signed_headers = "host;x-amz-content-sha256;x-amz-date"
    canonical_request = (
        f"{method}\n{path}\n\n{canonical_headers}\n{signed_headers}\n{payload_hash}"
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
        "x-amz-date": amz_date,
        "x-amz-content-sha256": payload_hash,
        "Authorization": (
            f"AWS4-HMAC-SHA256 Credential={access_key}/{scope}, "
            f"SignedHeaders={signed_headers}, Signature={signature}"
        ),
    }


def _ensure_bucket() -> bool:
    """Create the dev bucket, tolerating the already-exists responses."""
    host = S3_ENDPOINT.split("://", 1)[1]
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
        f"{S3_ENDPOINT}{path}", method="PUT", data=b"", headers=headers
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
        _fail(f"could not reach the object store at {S3_ENDPOINT}: {e}")
        return False


# ── Long-running servers ─────────────────────────────────────────────────────


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

    def start(self) -> None:
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

    def _pump(self) -> None:
        assert self.proc is not None and self.proc.stdout is not None
        announced = False
        for line in self.proc.stdout:
            print(f"{self.prefix} {line.rstrip()}", flush=True)
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
    args = parser.parse_args()

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

    # 1. Services. `--wait` blocks on the healthchecks in compose.yaml, so
    #    the migration below never races a Postgres that is still booting.
    #
    #    The unselected store is stopped first: both publish :3900, so leaving
    #    the previous one up makes the new one fail to bind — with a port
    #    conflict rather than anything that points at the actual cause.
    for other in S3_BACKENDS:
        if other not in ("none", args.s3):
            subprocess.run(
                [*compose, "--profile", other, "stop", other],
                cwd=ROOT,
                capture_output=True,
            )

    profile = [] if args.s3 == "none" else ["--profile", args.s3]
    store_label = "no object store" if args.s3 == "none" else args.s3
    _step(f"Starting Postgres, Redis, Gotenberg and {store_label}")
    if _run([*compose, *profile, "up", "-d", "--wait"], cwd=ROOT) != 0:
        _fail("compose failed — is the Docker daemon running?")
        return 1

    # 2. Bucket. Garage's --default-bucket already made one; RustFS starts
    #    empty, and a presigned PUT into a missing bucket just 404s.
    if S3_BACKENDS[args.s3]:
        _step(f"Creating the '{S3_BUCKET}' bucket")
        if not _ensure_bucket():
            return 1

    # 3. Schema.
    _step("Applying migrations")
    if _run(["alembic", "upgrade", "head"], cwd=BACKEND) != 0:
        _fail("alembic upgrade failed")
        return 1

    # 4. Frontend deps, only when they are missing. Re-running npm install
    #    on every `up` would add ~10s to a loop that is otherwise instant.
    npm = "npm.cmd" if IS_WINDOWS else "npm"
    if not (FRONTEND / "node_modules").is_dir():
        _step("Installing frontend dependencies (first run)")
        if _run([npm, "install"], cwd=FRONTEND) != 0:
            _fail("npm install failed")
            return 1

    # 5. Both servers, until Ctrl-C.
    #
    # The API child is told where the object store is. setdefault, not
    # assignment: an SHELF_S3_* already exported in the calling shell (or set
    # in backend/.env) is the developer pointing at their own store, and it
    # wins. With --s3 none nothing is set, so the config defaults stand and
    # upload calls fail at request time rather than at startup.
    api_env = os.environ.copy()
    if args.s3 != "none":
        for var, value in (
            ("SHELF_S3_ENDPOINT", S3_ENDPOINT),
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

    # vite.config.ts reads this to point its /api and /auth proxy at the
    # backend; without it a moved api port would leave the proxy talking to
    # :8000 and every request 502-ing.
    web_env = os.environ.copy()
    web_env["SHELF_DEV_API_PORT"] = str(api_port)

    servers = [
        Server(
            "api",
            [
                "uvicorn",
                "shelf.main:app",
                "--reload",
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

    _step(
        f"Starting backend (:{api_port}) and frontend "
        f"(:{web_port}) — Ctrl-C to stop"
    )

    for s in servers:
        s.start()

    stopping = threading.Event()

    def _shutdown(*_: object) -> None:
        if stopping.is_set():
            return
        stopping.set()
        print()
        _step("Shutting down servers (containers stay up; `pixi run dev-down`)")
        for s in servers:
            s.stop()

    signal.signal(signal.SIGINT, _shutdown)
    if IS_WINDOWS:
        signal.signal(signal.SIGBREAK, _shutdown)
    else:
        signal.signal(signal.SIGTERM, _shutdown)

    try:
        # If either server dies on its own, take the other one down with it
        # rather than leaving half a stack running and looking healthy.
        while not stopping.is_set():
            for s in servers:
                if s.proc is not None and s.proc.poll() is not None:
                    _fail(f"{s.name} exited with code {s.proc.returncode}")
                    _shutdown()
                    return s.proc.returncode or 1
            time.sleep(0.3)
    except KeyboardInterrupt:
        _shutdown()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
