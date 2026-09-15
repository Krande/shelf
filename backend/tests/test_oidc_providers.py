"""Provider-agnostic behaviour of the OIDC layer.

Shelf shouldn't be tuned for one identity provider. The two places it
could have drifted that way are the subject claim and the `prompt` sent
when linking a second account: both are per-provider config with a
standards-compliant default, and both are exercised here against a
provider that takes the defaults and one that doesn't.
"""

import httpx
import pytest
from httpx import ASGITransport, AsyncClient

from shelf.auth.oidc import claims_to_subject, provider_config
from shelf.config import OIDCProvider, settings
from shelf.main import app


def _provider(name: str, **over: object) -> OIDCProvider:
    return OIDCProvider(
        name=name,
        issuer=f"https://{name}.example.com/",
        client_id="cid",
        client_secret="secret",
        **over,  # type: ignore[arg-type]
    )


@pytest.fixture
def providers(monkeypatch: pytest.MonkeyPatch) -> None:
    """A provider on every default, and one overriding both knobs."""
    monkeypatch.setattr(
        settings,
        "oidc_providers",
        [
            _provider("authentik"),
            _provider("entra", subject_claim="oid", link_prompt="login"),
            _provider("quirky", link_prompt=""),
        ],
    )


# ── Defaults suit a standards-compliant provider ─────────────────────────────


def test_defaults_need_no_configuration(providers: None) -> None:
    """A provider added with nothing but issuer and credentials gets the
    behaviour the spec describes."""
    p = provider_config("authentik")
    assert p is not None
    assert p.subject_claim == "sub"
    assert p.link_prompt == "select_account"


def test_subject_claim_is_per_provider(providers: None) -> None:
    claims = {"sub": "pairwise", "oid": "tenant-stable"}
    # The one on defaults reads `sub` even though `oid` is present…
    assert claims_to_subject(claims, "authentik") == "pairwise"
    # …and only the one configured for it reads `oid`.
    assert claims_to_subject(claims, "entra") == "tenant-stable"


def test_a_provider_without_the_configured_claim_still_works(
    providers: None,
) -> None:
    assert claims_to_subject({"sub": "only-sub"}, "entra") == "only-sub"


# ── The link prompt ──────────────────────────────────────────────────────────


async def _link_redirect(provider: str) -> httpx.Response:
    """Follow /auth/link far enough to see the authorize URL, with a
    session in place so it isn't rejected as unauthenticated."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        await c.post("/auth/dev-login", json={"email": "a@example.com"})
        return await c.get(f"/auth/link/{provider}", follow_redirects=False)


async def test_link_sends_select_account_by_default(
    providers: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without a prompt, a provider holding an active session signs the
    same account back in and linking a second is unreachable."""
    _stub_metadata(monkeypatch)
    r = await _link_redirect("authentik")
    assert r.status_code == 302
    assert "prompt=select_account" in r.headers["location"]


async def test_link_prompt_is_configurable(
    providers: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """For a provider that doesn't implement select_account, `login` is
    universally supported and achieves the same end."""
    _stub_metadata(monkeypatch)
    r = await _link_redirect("entra")
    assert r.status_code == 302
    assert "prompt=login" in r.headers["location"]
    assert "select_account" not in r.headers["location"]


async def test_empty_link_prompt_sends_none(
    providers: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_metadata(monkeypatch)
    r = await _link_redirect("quirky")
    assert r.status_code == 302
    assert "prompt=" not in r.headers["location"]


def _stub_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    """Register clients with explicit endpoints so the redirect can be
    built without fetching a discovery document — these tests are about
    the parameters shelf sends, not about authlib's plumbing.

    Patches the name in `shelf.api.auth`, not in `shelf.auth.oidc`: the
    router imported `oauth` at module load, so rebinding it at the source
    would not be seen.
    """
    from authlib.integrations.starlette_client import OAuth

    from shelf.api import auth as auth_api

    registry = OAuth()
    for p in settings.oidc_providers:
        registry.register(
            name=p.name,
            client_id=p.client_id,
            client_secret=p.client_secret,
            authorize_url="https://idp.example.com/authorize",
            access_token_url="https://idp.example.com/token",
            client_kwargs={"scope": " ".join(p.scopes)},
        )
    monkeypatch.setattr(auth_api, "oauth", registry)


# ── Provider errors are explained, not 500s ──────────────────────────────────


async def test_callback_reports_a_provider_error(
    providers: None,
) -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.get(
            "/auth/callback/authentik",
            params={"error": "access_denied", "error_description": "User said no"},
        )
    assert r.status_code == 400
    assert "User said no" in r.json()["detail"]


async def test_callback_explains_an_unsupported_prompt(
    providers: None,
) -> None:
    """The failure mode for a provider that can't do select_account. The
    message has to name the fix, or it's a dead end for whoever hits it."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.get(
            "/auth/callback/authentik",
            params={"error": "account_selection_required"},
        )
    assert r.status_code == 400
    detail = r.json()["detail"]
    assert "link_prompt" in detail
    assert "login" in detail
