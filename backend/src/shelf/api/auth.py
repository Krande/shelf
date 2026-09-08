"""Authentication routes.

OIDC code-flow is the production path: `/auth/login/{provider}` kicks off
the redirect, `/auth/callback/{provider}` lands the token exchange and
sets the session cookie. `/auth/dev-login` stays around as a guarded
shortcut for local development and tests.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from ..auth.oidc import (
    claims_to_display_name,
    claims_to_email,
    is_known_provider,
    oauth,
    provider_names,
    upsert_user_from_claims,
)
from ..auth.session import issue_session
from ..config import settings
from ..db import get_session
from ..models import Space, User

router = APIRouter(tags=["auth"])


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
    client = oauth.create_client(provider)
    redirect_uri = f"{settings.public_base_url.rstrip('/')}/auth/callback/{provider}"
    response: Response = await client.authorize_redirect(request, redirect_uri)
    return response


@router.get("/auth/callback/{provider}")
async def callback(
    provider: str,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_session)],
) -> Response:
    if not is_known_provider(provider):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown OIDC provider")
    client = oauth.create_client(provider)

    token = await client.authorize_access_token(request)
    claims = token.get("userinfo") or {}
    sub = claims.get("sub")
    if not isinstance(sub, str):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Provider returned no sub claim")

    email = claims_to_email(claims, idp=provider, sub=sub)
    display_name = claims_to_display_name(claims, email=email)

    user = await upsert_user_from_claims(
        db, idp=provider, sub=sub, email=email, display_name=display_name
    )

    session_token = issue_session(user.id)
    redirect = RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)
    redirect.set_cookie(
        settings.session_cookie_name,
        session_token,
        httponly=True,
        samesite="lax",
        secure=settings.session_cookie_secure,
        max_age=settings.session_ttl_seconds,
    )
    return redirect


# ── Local dev shortcut ───────────────────────────────────────────────────────


class DevLoginRequest(BaseModel):
    email: str
    display_name: str | None = None


class DevLoginResponse(BaseModel):
    user_id: str


@router.post("/auth/dev-login", response_model=DevLoginResponse)
async def dev_login(
    payload: DevLoginRequest,
    response: Response,
    db: Annotated[AsyncSession, Depends(get_session)],
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

    token = issue_session(user.id)
    response.set_cookie(
        settings.session_cookie_name,
        token,
        httponly=True,
        samesite="lax",
        secure=settings.session_cookie_secure,
        max_age=settings.session_ttl_seconds,
    )
    return DevLoginResponse(user_id=str(user.id))


@router.post("/auth/logout")
async def logout(response: Response) -> dict[str, str]:
    response.delete_cookie(settings.session_cookie_name)
    return {"status": "ok"}
