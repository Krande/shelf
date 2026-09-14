"""Startup dependency checks.

shelf depends on a database, an object store and one OIDC provider per
configured login button. All three are reachable-or-not the moment the
process starts, but nothing used to look: the app booted and served
/health unconditionally, so a misconfigured deployment reported itself
healthy and then failed later, in a browser, far from the cause - a 500
on the login redirect, an upload blocked by CORS, a presigned URL naming
a host the browser cannot load.

These checks run once at startup and refuse to serve when the problem is
one a restart cannot fix, naming the setting to change. Failures that
look transient (connection refused, timeout, 5xx) are retried first, so
a dependency that is merely slow to come up does not become a crash
loop.

The object-store check deliberately includes a real CORS preflight from
the browser's point of view: a presigned URL is only useful if a browser
can actually use it, and that is exactly the part no server-side call
would otherwise exercise.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx
from obstore import list_with_delimiter_async
from sqlalchemy import text

from .config import settings
from .db import engine
from .services import storage

logger = logging.getLogger(__name__)

# Object key used only as a CORS preflight target. Never created: a
# preflight is unauthenticated and is answered from the bucket's CORS
# configuration without the key being looked up.
CORS_PROBE_KEY = "_shelf-preflight-probe"

DEV_SESSION_SECRET = "dev-secret-do-not-use-in-production"


class PreflightError(RuntimeError):
    """Configuration is wrong in a way restarting will not fix."""


@dataclass(frozen=True)
class CheckResult:
    name: str
    detail: str


Check = Callable[[], Awaitable[str]]


def _is_transient(exc: BaseException) -> bool:
    """Whether a failure is worth retrying rather than failing on.

    Connect and timeout errors and 5xx responses mean "not up yet".
    Anything else - a bad URL, a 403, a missing bucket - is a
    configuration fault that fails identically on every retry.
    """
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code >= 500
    # Deliberately ConnectionError and not OSError: PermissionError is an
    # OSError too, and retrying a denial just delays the same failure.
    if isinstance(exc, httpx.TransportError | ConnectionError | TimeoutError):
        return True
    # SQLAlchemy wraps connection refusal from every driver in
    # OperationalError; the type name is the portable discriminator.
    return type(exc).__name__ == "OperationalError"


async def _retrying(name: str, check: Check) -> CheckResult:
    """Run one check, retrying only while the failure looks transient."""
    attempts = max(1, settings.preflight_retries)
    last: BaseException | None = None
    for attempt in range(1, attempts + 1):
        try:
            return CheckResult(name=name, detail=await check())
        except PreflightError:
            raise
        except Exception as exc:
            if not _is_transient(exc):
                raise PreflightError(f"{name}: {exc}") from exc
            last = exc
            if attempt < attempts:
                logger.warning(
                    "preflight %s not ready (attempt %d/%d): %s",
                    name,
                    attempt,
                    attempts,
                    exc,
                )
                await asyncio.sleep(settings.preflight_retry_delay_seconds)
    raise PreflightError(f"{name}: still unreachable after {attempts} attempts: {last}") from last


async def check_database() -> str:
    """Connect, and confirm the migrations have been applied.

    An unmigrated database is a configuration fault rather than a
    transient one: the schema is created by a separate migration step,
    so serving against an empty database only produces confusing errors
    further in.
    """
    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))
        revision = (await conn.execute(text("SELECT version_num FROM alembic_version"))).scalar()
    if not revision:
        raise PreflightError(
            "database has no alembic_version row - run the migrations "
            "('alembic upgrade head') before starting the API"
        )
    return f"connected, schema at {revision}"


async def check_object_store() -> str:
    """Prove the bucket exists and the credentials can address it.

    A one-shot list is the cheapest call that distinguishes a missing
    bucket from a key that cannot reach it, and it is indifferent to the
    bucket being empty.
    """
    await list_with_delimiter_async(storage.get_store(), prefix=CORS_PROBE_KEY)
    return f"bucket {settings.s3_bucket!r} reachable at {settings.s3_endpoint}"


def _browser_endpoint() -> str:
    return settings.s3_endpoint_public or settings.s3_endpoint


async def check_browser_upload() -> str:
    """Check the store from the browser's side of a presigned upload.

    Two failures live here and neither is visible to a server-side call:
    an http:// endpoint is refused outright by an https:// page as mixed
    content, and a bucket with no CORS rule fails the preflight before
    the PUT is ever sent.
    """
    endpoint = _browser_endpoint().rstrip("/")
    origin = settings.public_base_url.rstrip("/")

    if urlparse(origin).scheme == "https" and urlparse(endpoint).scheme != "https":
        raise PreflightError(
            f"browser-facing store endpoint {endpoint!r} is not https, but the "
            f"app is served from {origin!r} - browsers block the upload as "
            "mixed content. Set s3_endpoint_public to an https URL"
        )

    url = f"{endpoint}/{settings.s3_bucket}/{CORS_PROBE_KEY}"
    async with httpx.AsyncClient(timeout=settings.preflight_timeout_seconds) as c:
        resp = await c.request(
            "OPTIONS",
            url,
            headers={
                "Origin": origin,
                "Access-Control-Request-Method": "PUT",
                "Access-Control-Request-Headers": "content-type",
            },
        )
    if resp.status_code >= 500:
        resp.raise_for_status()

    allow_origin = resp.headers.get("access-control-allow-origin")
    allow_methods = resp.headers.get("access-control-allow-methods", "")
    allow_headers = resp.headers.get("access-control-allow-headers", "")

    if allow_origin not in (origin, "*"):
        raise PreflightError(
            f"the store did not accept {origin!r} as a CORS origin for bucket "
            f"{settings.s3_bucket!r} (returned {allow_origin!r}). Presigned "
            "uploads will fail. Add a bucket CORS rule allowing that origin - "
            "a bucket carrying no rule at all answers like this too"
        )
    if "PUT" not in allow_methods.upper() and "*" not in allow_methods:
        raise PreflightError(
            f"the bucket CORS rule for {origin!r} does not allow PUT "
            f"(allows {allow_methods!r}); presigned uploads will fail"
        )
    if "content-type" not in allow_headers.lower() and "*" not in allow_headers:
        raise PreflightError(
            f"the bucket CORS rule for {origin!r} does not allow the "
            f"content-type request header (allows {allow_headers!r}); presigned "
            "uploads will fail their preflight"
        )
    return f"CORS on {endpoint} accepts PUT from {origin}"


async def check_oidc_providers() -> str:
    """Resolve each provider's discovery document.

    The client fetches <issuer>/.well-known/openid-configuration on the
    first login, so an issuer that is a bare id, a typo, or merely
    unreachable surfaces as a 500 on the login route. Resolving it here
    moves that to startup, where the message can name the provider.
    """
    if not settings.oidc_providers:
        return "none configured"
    resolved = []
    async with httpx.AsyncClient(
        timeout=settings.preflight_timeout_seconds, follow_redirects=True
    ) as c:
        for provider in settings.oidc_providers:
            parsed = urlparse(provider.issuer)
            if parsed.scheme not in ("http", "https") or not parsed.netloc:
                raise PreflightError(
                    f"OIDC provider {provider.name!r} has issuer "
                    f"{provider.issuer!r}, which is not an absolute http(s) "
                    "URL - it must be the full issuer URL, not a bare id"
                )
            url = provider.issuer.rstrip("/") + "/.well-known/openid-configuration"
            resp = await c.get(url)
            if resp.status_code >= 500:
                resp.raise_for_status()
            if resp.status_code != 200:
                raise PreflightError(
                    f"OIDC provider {provider.name!r}: {url} returned "
                    f"{resp.status_code}; check the issuer"
                )
            try:
                metadata = resp.json()
            except ValueError as exc:
                raise PreflightError(
                    f"OIDC provider {provider.name!r}: {url} did not return JSON"
                ) from exc
            if not metadata.get("authorization_endpoint"):
                raise PreflightError(
                    f"OIDC provider {provider.name!r}: {url} carries no "
                    "authorization_endpoint; it is not a discovery document"
                )
            resolved.append(provider.name)
    return f"discovery resolved for {', '.join(resolved)}"


async def check_session_secret() -> str:
    """Refuse to run a real deployment on the documented dev secret."""
    if settings.dev_login_enabled:
        return "skipped (dev login enabled)"
    if settings.session_secret_key == DEV_SESSION_SECRET:
        raise PreflightError(
            "session_secret_key is still the built-in development value; "
            "set it to a generated secret"
        )
    return "set"


# Cheap and side-effect-free enough to re-run on every readiness probe.
READINESS_CHECKS: tuple[tuple[str, Check], ...] = (
    ("database", check_database),
    ("object-store", check_object_store),
)

STARTUP_CHECKS: tuple[tuple[str, Check], ...] = (
    *READINESS_CHECKS,
    ("browser-upload", check_browser_upload),
    ("oidc", check_oidc_providers),
    ("session-secret", check_session_secret),
)


async def run_startup_checks() -> list[CheckResult]:
    """Run every check, raising PreflightError on the first hard failure."""
    results = []
    for name, check in STARTUP_CHECKS:
        result = await _retrying(name, check)
        logger.info("preflight %s: %s", result.name, result.detail)
        results.append(result)
    return results


async def run_readiness_checks() -> list[CheckResult]:
    """Run the cheap subset once, without retries, for the readiness probe."""
    results = []
    for name, check in READINESS_CHECKS:
        try:
            results.append(CheckResult(name=name, detail=await check()))
        except PreflightError:
            raise
        except Exception as exc:
            raise PreflightError(f"{name}: {exc}") from exc
    return results


__all__ = [
    "CheckResult",
    "PreflightError",
    "run_readiness_checks",
    "run_startup_checks",
]
