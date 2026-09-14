"""Preflight check tests.

Each case pins one misconfiguration that previously reached a user as a
runtime failure - a login 500, a blocked upload - and asserts it is now a
startup error naming the setting. The retry cases pin the other half of
the contract: a dependency that is merely slow must not be treated as
misconfigured.
"""

from typing import Any

import httpx
import pytest

from shelf import preflight
from shelf.config import OIDCProvider, settings


def _cors_headers(
    origin: str = "https://app.example.com",
    methods: str = "GET, HEAD, PUT, POST, DELETE",
    headers: str = "*",
) -> dict[str, str]:
    return {
        "access-control-allow-origin": origin,
        "access-control-allow-methods": methods,
        "access-control-allow-headers": headers,
    }


def _stub_options(
    monkeypatch: pytest.MonkeyPatch,
    *,
    status: int = 200,
    headers: dict[str, str] | None = None,
) -> None:
    """Answer the CORS preflight without a network call."""

    async def fake_request(self: Any, method: str, url: str, **_: Any) -> httpx.Response:
        return httpx.Response(status, headers=headers or {}, request=httpx.Request(method, url))

    monkeypatch.setattr(httpx.AsyncClient, "request", fake_request)


def _stub_get(
    monkeypatch: pytest.MonkeyPatch,
    *,
    status: int = 200,
    payload: Any = None,
    text: str | None = None,
) -> None:
    """Answer the OIDC discovery fetch without a network call."""

    async def fake_get(self: Any, url: str, **_: Any) -> httpx.Response:
        req = httpx.Request("GET", url)
        if text is not None:
            return httpx.Response(status, text=text, request=req)
        return httpx.Response(status, json=payload or {}, request=req)

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)


# ── browser upload ───────────────────────────────────────────────────────────


async def test_https_page_rejects_plaintext_store_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A presigned URL on an http:// host is blocked by the browser as mixed
    content before the request is sent, so nothing reaches the server."""
    monkeypatch.setattr(settings, "public_base_url", "https://app.example.com")
    monkeypatch.setattr(settings, "s3_endpoint_public", "http://store.internal:3900")

    with pytest.raises(preflight.PreflightError, match="mixed content"):
        await preflight.check_browser_upload()


async def test_missing_cors_rule_is_a_startup_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A bucket with no rule answers a preflight generically, which the
    browser rejects. That has to fail here, not on the user's upload."""
    monkeypatch.setattr(settings, "public_base_url", "https://app.example.com")
    monkeypatch.setattr(settings, "s3_endpoint_public", "https://store.example.com")
    _stub_options(monkeypatch, headers={"access-control-allow-origin": "*"})

    with pytest.raises(preflight.PreflightError, match="does not allow PUT"):
        await preflight.check_browser_upload()


async def test_cors_rule_omitting_content_type_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The upload sets Content-Type, so a rule that does not allow that
    header fails the preflight even though the origin matches."""
    monkeypatch.setattr(settings, "public_base_url", "https://app.example.com")
    monkeypatch.setattr(settings, "s3_endpoint_public", "https://store.example.com")
    _stub_options(monkeypatch, headers=_cors_headers(headers="x-amz-date"))

    with pytest.raises(preflight.PreflightError, match="content-type"):
        await preflight.check_browser_upload()


async def test_cors_rule_for_another_origin_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "public_base_url", "https://app.example.com")
    monkeypatch.setattr(settings, "s3_endpoint_public", "https://store.example.com")
    _stub_options(monkeypatch, headers=_cors_headers(origin="https://other.example.com"))

    with pytest.raises(preflight.PreflightError, match="CORS origin"):
        await preflight.check_browser_upload()


async def test_correct_cors_rule_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "public_base_url", "https://app.example.com")
    monkeypatch.setattr(settings, "s3_endpoint_public", "https://store.example.com")
    _stub_options(monkeypatch, headers=_cors_headers())

    assert "accepts PUT" in await preflight.check_browser_upload()


async def test_browser_check_falls_back_to_the_server_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With no public endpoint set, the browser check must exercise the
    single endpoint rather than silently skip."""
    monkeypatch.setattr(settings, "public_base_url", "https://app.example.com")
    monkeypatch.setattr(settings, "s3_endpoint_public", "")
    monkeypatch.setattr(settings, "s3_endpoint", "https://store.example.com")
    _stub_options(monkeypatch, headers=_cors_headers())

    assert "store.example.com" in await preflight.check_browser_upload()


# ── OIDC ─────────────────────────────────────────────────────────────────────


def _provider(issuer: str) -> OIDCProvider:
    return OIDCProvider(name="idp", issuer=issuer, client_id="cid", client_secret="secret")


async def test_issuer_without_a_scheme_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A bare id in the issuer field previously surfaced only as a 500 on
    the login route, from a URL the HTTP client refused to parse."""
    monkeypatch.setattr(
        settings, "oidc_providers", [_provider("11111111-2222-3333-4444-555555555555")]
    )

    with pytest.raises(preflight.PreflightError, match="absolute http"):
        await preflight.check_oidc_providers()


async def test_discovery_document_must_exist(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "oidc_providers", [_provider("https://idp.example.com")])
    _stub_get(monkeypatch, status=404)

    with pytest.raises(preflight.PreflightError, match="returned 404"):
        await preflight.check_oidc_providers()


async def test_discovery_document_must_be_a_discovery_document(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A 200 that is not OIDC metadata - a login page, a proxy error page -
    is just as broken as a 404, and fails later in a stranger way."""
    monkeypatch.setattr(settings, "oidc_providers", [_provider("https://idp.example.com")])
    _stub_get(monkeypatch, payload={"issuer": "https://idp.example.com"})

    with pytest.raises(preflight.PreflightError, match="authorization_endpoint"):
        await preflight.check_oidc_providers()


async def test_valid_provider_resolves(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "oidc_providers", [_provider("https://idp.example.com")])
    _stub_get(
        monkeypatch,
        payload={"authorization_endpoint": "https://idp.example.com/authorize"},
    )

    assert "idp" in await preflight.check_oidc_providers()


async def test_no_providers_is_not_a_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "oidc_providers", [])
    assert await preflight.check_oidc_providers() == "none configured"


# ── session secret ───────────────────────────────────────────────────────────


async def test_dev_session_secret_rejected_outside_dev(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "dev_login_enabled", False)
    monkeypatch.setattr(settings, "session_secret_key", preflight.DEV_SESSION_SECRET)

    with pytest.raises(preflight.PreflightError, match="development value"):
        await preflight.check_session_secret()


async def test_dev_session_secret_allowed_with_dev_login(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "dev_login_enabled", True)
    monkeypatch.setattr(settings, "session_secret_key", preflight.DEV_SESSION_SECRET)

    assert "skipped" in await preflight.check_session_secret()


# ── retry policy ─────────────────────────────────────────────────────────────


async def test_transient_failure_is_retried_then_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A store that is slow to come up must not be read as misconfigured."""
    monkeypatch.setattr(settings, "preflight_retries", 3)
    monkeypatch.setattr(settings, "preflight_retry_delay_seconds", 0)
    calls = {"n": 0}

    async def flaky() -> str:
        calls["n"] += 1
        if calls["n"] < 3:
            raise httpx.ConnectError("connection refused")
        return "up"

    assert (await preflight._retrying("dep", flaky)).detail == "up"
    assert calls["n"] == 3


async def test_transient_failure_gives_up_eventually(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "preflight_retries", 2)
    monkeypatch.setattr(settings, "preflight_retry_delay_seconds", 0)

    async def down() -> str:
        raise httpx.ConnectError("connection refused")

    with pytest.raises(preflight.PreflightError, match="after 2 attempts"):
        await preflight._retrying("dep", down)


async def test_configuration_failure_is_not_retried(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Retrying a 403 or a missing bucket just delays the same error, so a
    non-transient fault must fail on the first attempt."""
    monkeypatch.setattr(settings, "preflight_retries", 5)
    monkeypatch.setattr(settings, "preflight_retry_delay_seconds", 0)
    calls = {"n": 0}

    async def denied() -> str:
        calls["n"] += 1
        raise PermissionError("access denied")

    with pytest.raises(preflight.PreflightError, match="access denied"):
        await preflight._retrying("dep", denied)
    assert calls["n"] == 1


async def test_preflight_error_passes_through_unretried(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "preflight_retries", 5)
    monkeypatch.setattr(settings, "preflight_retry_delay_seconds", 0)
    calls = {"n": 0}

    async def bad_config() -> str:
        calls["n"] += 1
        raise preflight.PreflightError("issuer is not a URL")

    with pytest.raises(preflight.PreflightError, match="issuer is not a URL"):
        await preflight._retrying("dep", bad_config)
    assert calls["n"] == 1


@pytest.mark.parametrize(
    ("exc", "transient"),
    [
        (httpx.ConnectError("refused"), True),
        (TimeoutError(), True),
        (ConnectionRefusedError("refused"), True),
        # An OSError subclass, but a denial - retrying only delays it.
        (PermissionError("denied"), False),
        (ValueError("bad config"), False),
        (preflight.PreflightError("bad config"), False),
    ],
)
def test_transient_classification(exc: BaseException, transient: bool) -> None:
    assert preflight._is_transient(exc) is transient
