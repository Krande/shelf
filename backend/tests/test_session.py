"""Unit tests for the JWT session module."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from joserfc import jwt
from joserfc.jwk import OctKey

from shelf.auth.session import InvalidSessionError, issue_session, parse_session
from shelf.config import settings


def test_session_roundtrip() -> None:
    user_id = uuid.uuid4()
    token = issue_session(user_id)
    claims = parse_session(token)
    assert claims.active_user_id == user_id
    assert claims.account_ids == (user_id,)


def test_session_tampered() -> None:
    user_id = uuid.uuid4()
    token = issue_session(user_id)
    # Flip a character in the signature segment
    tampered = token[:-2] + ("xx" if token[-2:] != "xx" else "yy")
    with pytest.raises(InvalidSessionError):
        parse_session(tampered)


def test_session_expired() -> None:
    user_id = uuid.uuid4()
    token = issue_session(user_id, ttl=timedelta(seconds=-1))
    with pytest.raises(InvalidSessionError):
        parse_session(token)


def test_session_garbage() -> None:
    with pytest.raises(InvalidSessionError):
        parse_session("not-a-jwt")


# ── Linked accounts ──────────────────────────────────────────────────────────


def test_linked_accounts_roundtrip() -> None:
    a, b = uuid.uuid4(), uuid.uuid4()
    token = issue_session(b, account_ids=(a, b))
    claims = parse_session(token)
    assert claims.active_user_id == b
    assert set(claims.account_ids) == {a, b}


def test_active_account_is_always_present() -> None:
    """Even if the caller passes a list that omits it."""
    a, b = uuid.uuid4(), uuid.uuid4()
    claims = parse_session(issue_session(b, account_ids=(a,)))
    assert b in claims.account_ids


def test_accounts_deduped() -> None:
    a = uuid.uuid4()
    claims = parse_session(issue_session(a, account_ids=(a, a, a)))
    assert claims.account_ids == (a,)


def test_accounts_capped(monkeypatch: pytest.MonkeyPatch) -> None:
    """The cap evicts the oldest link, never the account you just became."""
    monkeypatch.setattr(settings, "max_linked_accounts", 3)
    olds = [uuid.uuid4() for _ in range(5)]
    active = uuid.uuid4()
    claims = parse_session(issue_session(active, account_ids=(*olds, active)))
    assert len(claims.account_ids) == 3
    assert claims.account_ids[0] == active


def test_legacy_token_without_accts() -> None:
    """Sessions minted before linking existed must keep working — an
    upgrade shouldn't sign everybody out."""
    user_id = uuid.uuid4()
    now = datetime.now(UTC)
    legacy = jwt.encode(
        {"alg": "HS256"},
        {
            "sub": str(user_id),
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(hours=1)).timestamp()),
        },
        OctKey.import_key(settings.session_secret_key.encode()),
    )

    claims = parse_session(legacy)
    assert claims.active_user_id == user_id
    assert claims.account_ids == (user_id,)


def test_malformed_accts_entries_are_dropped() -> None:
    """A junk entry shouldn't invalidate an otherwise-valid session; it
    can't grant anything either, since every id is re-checked against the
    database before it's used."""
    user_id = uuid.uuid4()
    other = uuid.uuid4()
    now = datetime.now(UTC)
    token = jwt.encode(
        {"alg": "HS256"},
        {
            "sub": str(user_id),
            "accts": [str(user_id), "not-a-uuid", 42, str(other)],
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(hours=1)).timestamp()),
        },
        OctKey.import_key(settings.session_secret_key.encode()),
    )

    claims = parse_session(token)
    assert set(claims.account_ids) == {user_id, other}


def test_a_token_without_exp_is_rejected() -> None:
    """Otherwise it would never expire — a permanent pass rather than a
    session."""
    now = datetime.now(UTC)
    forever = jwt.encode(
        {"alg": "HS256"},
        {"sub": str(uuid.uuid4()), "iat": int(now.timestamp())},
        OctKey.import_key(settings.session_secret_key.encode()),
    )
    with pytest.raises(InvalidSessionError):
        parse_session(forever)


def test_the_algorithm_is_pinned() -> None:
    """The verifier must not take the token's word for how to verify it.
    A token signed with a different HMAC size, under the same secret, is
    still a forgery as far as shelf is concerned."""
    now = datetime.now(UTC)
    other_alg = jwt.encode(
        {"alg": "HS512"},
        {
            "sub": str(uuid.uuid4()),
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(hours=1)).timestamp()),
        },
        OctKey.import_key(settings.session_secret_key.encode()),
        algorithms=["HS512"],
    )
    with pytest.raises(InvalidSessionError):
        parse_session(other_alg)


def test_a_token_signed_with_another_secret_is_rejected() -> None:
    now = datetime.now(UTC)
    forged = jwt.encode(
        {"alg": "HS256"},
        {
            "sub": str(uuid.uuid4()),
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(hours=1)).timestamp()),
        },
        OctKey.import_key(b"not-the-shelf-secret-at-all-no-really"),
    )
    with pytest.raises(InvalidSessionError):
        parse_session(forged)


def test_expires_at_is_carried_not_slid() -> None:
    """Re-issuing with an explicit expiry must not extend the session —
    otherwise switching accounts once a day keeps it alive forever."""
    a, b = uuid.uuid4(), uuid.uuid4()
    original = parse_session(issue_session(a, account_ids=(a, b)))
    switched = parse_session(
        issue_session(b, account_ids=original.account_ids, expires_at=original.expires_at)
    )
    assert switched.expires_at == original.expires_at
    assert switched.active_user_id == b
