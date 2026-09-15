"""Authentication routes.

OIDC code-flow is the production path: `/auth/login/{provider}` kicks off
the redirect, `/auth/callback/{provider}` lands the token exchange and
sets the session cookie. `/auth/dev-login` stays around as a guarded
shortcut for local development and tests.

One browser session can hold several identities. `/auth/link/{provider}`
runs the same code flow but appends to the existing session instead of
replacing it, and `/auth/switch` flips which of the linked accounts is
active. The set lives in the signed session cookie, so the only way in
is a completed login in this browser — see `auth/session.py`.
"""

import logging
import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from ..auth.deps import SessionCookie, read_session
from ..auth.oidc import (
    claims_to_display_name,
    claims_to_email,
    claims_to_subject,
    is_known_provider,
    oauth,
    provider_config,
    provider_names,
    upsert_user_from_claims,
)
from ..auth.roles import apply_admin_bootstrap
from ..auth.session import InvalidSessionError, issue_session, parse_session
from ..config import settings
from ..db import get_session
from ..models import Space, User

log = logging.getLogger(__name__)

router = APIRouter(tags=["auth"])

# Key under which the OAuth-handshake session (Starlette's
# SessionMiddleware, already mounted for PKCE/state) carries the intent to
# link rather than replace. Popped by the callback.
_LINK_INTENT = "shelf_link_intent"


def _set_session_cookie(response: Response, token: str, *, max_age: int) -> None:
    response.set_cookie(
        settings.session_cookie_name,
        token,
        httponly=True,
        samesite="lax",
        secure=settings.session_cookie_secure,
        max_age=max_age,
    )


# ── OIDC code flow ───────────────────────────────────────────────────────────


class ProviderListResponse(BaseModel):
    providers: list[str]
    # Whether /auth/dev-login will actually mint a session. The SPA needs
    # this to decide if it can offer the local shortcut: with no provider
    # configured (the default for a fresh checkout) the login page would
    # otherwise render no way in at all.
    dev_login: bool


@router.get("/auth/providers", response_model=ProviderListResponse)
async def list_providers() -> ProviderListResponse:
    return ProviderListResponse(
        providers=provider_names(),
        dev_login=settings.dev_login_enabled,
    )


@router.get("/auth/login/{provider}")
async def login(provider: str, request: Request) -> Response:
    if not is_known_provider(provider):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown OIDC provider")
    request.session.pop(_LINK_INTENT, None)
    client = oauth.create_client(provider)
    redirect_uri = f"{settings.public_base_url.rstrip('/')}/auth/callback/{provider}"
    response: Response = await client.authorize_redirect(request, redirect_uri)
    return response


@router.get("/auth/link/{provider}")
async def link(
    provider: str,
    request: Request,
    session_token: SessionCookie = None,
) -> Response:
    """Start a code flow that *adds* an identity to the current session.

    The `prompt` is load-bearing: without it a provider that already has
    an active browser session signs the same account straight back in,
    and linking a second one is unreachable through the UI. It defaults
    to the standard `select_account` and is per-provider configurable,
    since not every provider implements that value — see
    `OIDCProvider.link_prompt`.
    """
    if not is_known_provider(provider):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown OIDC provider")
    read_session(session_token)  # 401 if there's nothing to link to

    request.session[_LINK_INTENT] = True
    client = oauth.create_client(provider)
    redirect_uri = f"{settings.public_base_url.rstrip('/')}/auth/callback/{provider}"

    config = provider_config(provider)
    prompt = (config.link_prompt if config is not None else "select_account").strip()
    extra = {"prompt": prompt} if prompt else {}

    response: Response = await client.authorize_redirect(
        request, redirect_uri, **extra
    )
    return response


@router.get("/auth/callback/{provider}")
async def callback(
    provider: str,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_session)],
    session_token: SessionCookie = None,
) -> Response:
    if not is_known_provider(provider):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown OIDC provider")
    linking = bool(request.session.pop(_LINK_INTENT, False))
    client = oauth.create_client(provider)

    # The provider can decline instead of returning a code — the user
    # cancelled at the consent screen, or the provider rejected something
    # we asked for. `account_selection_required` is the one to expect
    # here: a spec-compliant provider that cannot honour
    # prompt=select_account returns exactly that, and the fix is to set a
    # different `link_prompt` for it. Let that say so rather than
    # surfacing as an unhandled exception and a 500.
    error = request.query_params.get("error")
    if error:
        detail = request.query_params.get("error_description") or error
        if error == "account_selection_required":
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"{provider} does not support prompt=select_account. Set "
                f'"link_prompt": "login" (or "") for it in '
                f"SHELF_OIDC_PROVIDERS.",
            )
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, f"{provider} returned an error: {detail}"
        )

    token = await client.authorize_access_token(request)

    # authlib inlines the id_token's claims as `userinfo` when the
    # provider returns one. Not all do — and some return an id_token too
    # thin to identify the user — so fall back to the userinfo endpoint
    # rather than failing a provider that is behaving perfectly legally.
    claims = dict(token.get("userinfo") or {})
    if claims_to_subject(claims, provider) is None:
        try:
            claims = dict(await client.userinfo(token=token))
        except Exception:
            # Leave `claims` as-is; the subject check below reports it.
            log.warning("userinfo lookup failed for provider %r", provider)

    sub = claims_to_subject(claims, provider)
    if sub is None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Provider returned no usable subject claim",
        )

    email = claims_to_email(claims, idp=provider, sub=sub)
    display_name = claims_to_display_name(claims, email=email)

    user = await upsert_user_from_claims(
        db, idp=provider, sub=sub, email=email, display_name=display_name
    )
    user = await apply_admin_bootstrap(db, user)

    # Linking keeps the accounts already in the cookie and the expiry they
    # were issued with; a plain login starts a fresh single-account
    # session. A link intent with an unusable cookie (expired while the
    # user was at the provider) degrades to a plain login rather than
    # erroring — they end up signed in as the account they just picked,
    # which is the sane reading of what they asked for.
    existing: tuple[uuid.UUID, ...] = ()
    expires_at: datetime | None = None
    if linking and session_token:
        try:
            prev = parse_session(session_token)
        except InvalidSessionError:
            pass
        else:
            existing = prev.account_ids
            expires_at = prev.expires_at

    new_token = issue_session(
        user.id,
        account_ids=(*existing, user.id),
        expires_at=expires_at,
    )
    redirect = RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)
    _set_session_cookie(redirect, new_token, max_age=_remaining(expires_at))
    return redirect


def _remaining(expires_at: datetime | None) -> int:
    """Cookie max-age for a session that may be carrying an inherited
    expiry. Floors at 1s so a nearly-expired session still round-trips
    and gets rejected on the next request by the JWT's own `exp`, rather
    than being handed a negative max-age."""
    if expires_at is None:
        return settings.session_ttl_seconds
    return max(1, int((expires_at - datetime.now(UTC)).total_seconds()))


# ── Local dev shortcut ───────────────────────────────────────────────────────


class DevLoginRequest(BaseModel):
    email: str
    display_name: str | None = None
    # Append to the current session instead of replacing it — the
    # dev-login equivalent of /auth/link/{provider}. Gives the account
    # switcher a way to be exercised without an identity provider.
    link: bool = False


class DevLoginResponse(BaseModel):
    user_id: str


@router.post("/auth/dev-login", response_model=DevLoginResponse)
async def dev_login(
    payload: DevLoginRequest,
    response: Response,
    db: Annotated[AsyncSession, Depends(get_session)],
    session_token: SessionCookie = None,
) -> DevLoginResponse:
    if not settings.dev_login_enabled:
        raise HTTPException(status.HTTP_404_NOT_FOUND)

    result = await db.execute(select(User).where(User.email == payload.email))
    user = result.scalar_one_or_none()
    if user is None:
        user = User(
            email=payload.email,
            display_name=payload.display_name or payload.email.split("@", 1)[0],
        )
        db.add(user)
        await db.flush()
        space = Space(
            slug=f"u-{user.id.hex[:8]}",
            name=f"{user.display_name}'s shelf",
            owner_id=user.id,
        )
        db.add(space)
        await db.commit()
        await db.refresh(user)

    user = await apply_admin_bootstrap(db, user)

    existing: tuple[uuid.UUID, ...] = ()
    expires_at: datetime | None = None
    if payload.link and session_token:
        try:
            prev = parse_session(session_token)
        except InvalidSessionError:
            pass
        else:
            existing = prev.account_ids
            expires_at = prev.expires_at

    token = issue_session(
        user.id,
        account_ids=(*existing, user.id),
        expires_at=expires_at,
    )
    _set_session_cookie(response, token, max_age=_remaining(expires_at))
    return DevLoginResponse(user_id=str(user.id))


# ── Account switching ────────────────────────────────────────────────────────


class SwitchAccountRequest(BaseModel):
    user_id: uuid.UUID


class SwitchAccountResponse(BaseModel):
    user_id: str


@router.post("/auth/switch", response_model=SwitchAccountResponse)
async def switch_account(
    payload: SwitchAccountRequest,
    response: Response,
    db: Annotated[AsyncSession, Depends(get_session)],
    session_token: SessionCookie = None,
) -> SwitchAccountResponse:
    """Make one of the already-linked accounts the active one.

    The target has to be in the cookie's `accts`, which only a completed
    login in this browser can put there. Note the expiry is carried
    across unchanged: switching is not a fresh authentication and must
    not extend the session.
    """
    claims = read_session(session_token)
    if payload.user_id not in claims.account_ids:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Account not linked to this session")

    user = (
        await db.execute(select(User).where(User.id == payload.user_id))
    ).scalar_one_or_none()
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")

    token = issue_session(
        user.id,
        account_ids=claims.account_ids,
        expires_at=claims.expires_at,
    )
    _set_session_cookie(response, token, max_age=_remaining(claims.expires_at))
    return SwitchAccountResponse(user_id=str(user.id))


class UnlinkAccountRequest(BaseModel):
    user_id: uuid.UUID


class UnlinkAccountResponse(BaseModel):
    # None when the last account was removed and the session is gone.
    active_user_id: str | None


@router.post("/auth/accounts/unlink", response_model=UnlinkAccountResponse)
async def unlink_account(
    payload: UnlinkAccountRequest,
    response: Response,
    session_token: SessionCookie = None,
) -> UnlinkAccountResponse:
    """Drop an account from this browser session.

    Unlinking the active account falls through to whichever is left;
    unlinking the last one ends the session, which is the same thing as
    signing out.
    """
    claims = read_session(session_token)
    if payload.user_id not in claims.account_ids:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Account not linked to this session")

    remaining = tuple(i for i in claims.account_ids if i != payload.user_id)
    if not remaining:
        response.delete_cookie(settings.session_cookie_name)
        return UnlinkAccountResponse(active_user_id=None)

    active = (
        claims.active_user_id if claims.active_user_id in remaining else remaining[0]
    )
    token = issue_session(
        active,
        account_ids=remaining,
        expires_at=claims.expires_at,
    )
    _set_session_cookie(response, token, max_age=_remaining(claims.expires_at))
    return UnlinkAccountResponse(active_user_id=str(active))


@router.post("/auth/logout")
async def logout(response: Response) -> dict[str, str]:
    # Clears the whole session, every linked account with it. On a shared
    # machine "sign out" leaving other identities reachable would be a
    # nasty surprise; use /auth/accounts/unlink to drop just one.
    response.delete_cookie(settings.session_cookie_name)
    return {"status": "ok"}
