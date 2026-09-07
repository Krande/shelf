"""Unit tests for the JWT session module."""

import uuid
from datetime import timedelta

import pytest

from shelf.auth.session import InvalidSessionError, issue_session, parse_session


def test_session_roundtrip() -> None:
    user_id = uuid.uuid4()
    token = issue_session(user_id)
    assert parse_session(token) == user_id


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
