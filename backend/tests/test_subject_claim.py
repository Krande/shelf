"""Per-provider subject claim.

Azure AD's `sub` is pairwise — a different value per application
registration — so it can't be the identity key if the same person is to
look like the same person across apps. `oid` is tenant-stable. Providers
declare which claim to read; `sub` stays the default.
"""

import pytest

from shelf.auth.oidc import claims_to_subject, provider_config
from shelf.config import OIDCProvider, settings


def _provider(name: str, subject_claim: str = "sub") -> OIDCProvider:
    return OIDCProvider(
        name=name,
        issuer="https://idp.example.com/",
        client_id="cid",
        client_secret="secret",
        subject_claim=subject_claim,
    )


@pytest.fixture
def providers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        settings,
        "oidc_providers",
        [_provider("authentik"), _provider("entra", "oid")],
    )


def test_defaults_to_sub(providers: None) -> None:
    claims = {"sub": "authentik-sub", "oid": "should-be-ignored"}
    assert claims_to_subject(claims, "authentik") == "authentik-sub"


def test_reads_the_configured_claim(providers: None) -> None:
    claims = {"sub": "pairwise-per-app", "oid": "tenant-stable"}
    assert claims_to_subject(claims, "entra") == "tenant-stable"


def test_falls_back_to_sub_when_the_claim_is_missing(providers: None) -> None:
    """A provider that only sometimes emits the richer claim should not
    lock those users out."""
    assert claims_to_subject({"sub": "only-sub"}, "entra") == "only-sub"


def test_ignores_a_non_string_claim(providers: None) -> None:
    assert claims_to_subject({"oid": 42, "sub": "fallback"}, "entra") == "fallback"


def test_ignores_an_empty_claim(providers: None) -> None:
    assert claims_to_subject({"oid": "", "sub": "fallback"}, "entra") == "fallback"


def test_no_usable_claim(providers: None) -> None:
    assert claims_to_subject({"email": "a@example.com"}, "entra") is None
    assert claims_to_subject({"sub": ""}, "authentik") is None


def test_unknown_provider_uses_sub(providers: None) -> None:
    """Reachable only if a provider is removed from config mid-flight;
    reading `sub` is the safe default rather than raising."""
    assert claims_to_subject({"sub": "s"}, "not-configured") == "s"
    assert provider_config("not-configured") is None


def test_default_is_sub_without_config() -> None:
    assert _provider("anything").subject_claim == "sub"
