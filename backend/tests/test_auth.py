"""Integration tests for the auth routes: /auth/dev-login, /auth/logout,
/api/me, and the OIDC code flow's `next=` round trip."""

import pytest
from fastapi.responses import RedirectResponse
from httpx import AsyncClient
from starlette.requests import Request

from shelf.api import auth as auth_api
from shelf.api.auth import safe_next
from shelf.config import settings


async def test_providers_advertises_dev_login(client: AsyncClient) -> None:
    """The SPA gates its dev-login form on this flag. Without it, a checkout
    with no OIDC provider configured renders a login page with no way in."""
    r = await client.get("/auth/providers")
    assert r.status_code == 200
    assert r.json() == {"providers": [], "dev_login": True}


async def test_providers_hides_dev_login_when_disabled(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "dev_login_enabled", False)
    r = await client.get("/auth/providers")
    assert r.json()["dev_login"] is False


async def test_me_unauthenticated(client: AsyncClient) -> None:
    r = await client.get("/api/me")
    assert r.status_code == 401


async def test_me_with_garbage_cookie(client: AsyncClient) -> None:
    client.cookies.set("shelf_session", "not-a-real-token")
    r = await client.get("/api/me")
    assert r.status_code == 401


async def test_dev_login_creates_user_and_session(client: AsyncClient) -> None:
    r = await client.post("/auth/dev-login", json={"email": "alice@example.com"})
    assert r.status_code == 200, r.text
    assert "shelf_session" in r.cookies

    r2 = await client.get("/api/me")
    assert r2.status_code == 200
    body = r2.json()
    assert body["email"] == "alice@example.com"
    assert body["display_name"] == "alice"


async def test_dev_login_with_explicit_display_name(client: AsyncClient) -> None:
    r = await client.post(
        "/auth/dev-login",
        json={"email": "bob@example.com", "display_name": "Bobby"},
    )
    assert r.status_code == 200
    me = (await client.get("/api/me")).json()
    assert me["display_name"] == "Bobby"


async def test_dev_login_idempotent_for_same_email(client: AsyncClient) -> None:
    r1 = await client.post("/auth/dev-login", json={"email": "carol@example.com"})
    user_id_1 = r1.json()["user_id"]

    r2 = await client.post(
        "/auth/dev-login",
        json={"email": "carol@example.com", "display_name": "Carol the Second"},
    )
    user_id_2 = r2.json()["user_id"]
    assert user_id_1 == user_id_2


async def test_logout_clears_cookie(client: AsyncClient) -> None:
    await client.post("/auth/dev-login", json={"email": "dan@example.com"})
    assert (await client.get("/api/me")).status_code == 200

    await client.post("/auth/logout")
    assert (await client.get("/api/me")).status_code == 401


# ── `next=` handling ────────────────────────────────────────────────────────
#
# The SPA sends users off to /auth/login/{provider}?next=<where they were>
# when a session expires under them, and the callback redirects there
# instead of "/". That value reaches a Location header, so what counts as
# acceptable is pinned down on its own, ahead of the round trip below.


@pytest.mark.parametrize(
    "value",
    [
        "/",
        "/library",
        "/reader/33333333-4444-5555-6666-777777777777?page=57",
        "/settings/account#tokens",
    ],
)
def test_safe_next_accepts_same_origin_paths(value: str) -> None:
    assert safe_next(value) == value


@pytest.mark.parametrize(
    "value",
    [
        None,
        "",
        "https://evil.example/phish",
        "//evil.example/phish",
        r"/\evil.example/phish",
        "javascript:alert(1)",
        "library",  # relative — resolves against /auth/login/, not the SPA
        "/library\nLocation: https://evil.example",
        "/" + "a" * 4096,
    ],
)
def test_safe_next_rejects_anything_off_origin(value: str | None) -> None:
    assert safe_next(value) is None


# ── The round trip that puts a reader back where they were ──────────────────


class _FakeOAuthClient:
    """Enough of an authlib client to walk the code flow without a provider.

    The redirect a real provider would send the browser on is short-circuited
    to the callback, and the token exchange hands back fixed claims.
    """

    def __init__(self, claims: dict[str, str]) -> None:
        self.claims = claims

    async def authorize_redirect(
        self, request: Request, redirect_uri: str, **_: object
    ) -> RedirectResponse:
        return RedirectResponse(url=f"{redirect_uri}?code=fake", status_code=302)

    async def authorize_access_token(self, request: Request) -> dict[str, object]:
        return {"userinfo": self.claims}


@pytest.fixture
def fake_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make "entra" a working OIDC provider for the length of a test."""
    claims = {"sub": "erin-sub", "email": "erin@example.com", "name": "Erin"}

    class _FakeOAuth:
        def create_client(self, provider: str) -> _FakeOAuthClient:
            return _FakeOAuthClient(claims)

    monkeypatch.setattr(auth_api, "oauth", _FakeOAuth())
    monkeypatch.setattr(auth_api, "is_known_provider", lambda _name: True)


async def _sign_in(client: AsyncClient, *, next_param: str | None) -> str:
    """Walk login → callback, returning where the callback sent the browser."""
    params = {} if next_param is None else {"next": next_param}
    started = await client.get("/auth/login/entra", params=params)
    assert started.status_code in (302, 303), started.text
    landed = await client.get("/auth/callback/entra", params={"code": "fake"})
    assert landed.status_code == 303, landed.text
    assert "shelf_session" in landed.cookies
    return landed.headers["location"]


async def test_login_returns_to_the_page_it_was_started_from(
    client: AsyncClient, fake_provider: None
) -> None:
    """The point of the whole `next` plumbing: a session that expires while
    someone is reading costs them a sign-in, not their place in the document."""
    where = await _sign_in(client, next_param="/reader/abc?page=57")
    assert where == "/reader/abc?page=57"


async def test_login_without_a_target_lands_on_the_root(
    client: AsyncClient, fake_provider: None
) -> None:
    assert await _sign_in(client, next_param=None) == "/"


async def test_login_will_not_be_redirected_off_site(
    client: AsyncClient, fake_provider: None
) -> None:
    assert await _sign_in(client, next_param="https://evil.example/phish") == "/"


async def test_a_second_login_does_not_inherit_the_first_target(
    client: AsyncClient, fake_provider: None
) -> None:
    """The target lives in the handshake session, so an abandoned attempt
    must not redirect the next one."""
    await client.get("/auth/login/entra", params={"next": "/reader/abc?page=57"})
    assert await _sign_in(client, next_param=None) == "/"


async def test_linking_an_account_still_lands_on_the_root(
    client: AsyncClient, fake_provider: None
) -> None:
    """Linking reloads the app from "/" on purpose — the active account may
    have changed, so nothing cached under the old one survives. An abandoned
    login's target must not divert that."""
    await client.post("/auth/dev-login", json={"email": "frank@example.com"})
    await client.get("/auth/login/entra", params={"next": "/reader/abc?page=57"})

    started = await client.get("/auth/link/entra")
    assert started.status_code in (302, 303), started.text
    landed = await client.get("/auth/callback/entra", params={"code": "fake"})
    assert landed.status_code == 303
    assert landed.headers["location"] == "/"
