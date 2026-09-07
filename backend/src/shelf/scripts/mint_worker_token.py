"""Mint a worker-scoped API token.

The public token-creation endpoint refuses to issue ``worker``
tokens — they grant cross-user access to the processing table and
shouldn't be self-service. Operators provision them via this CLI:

    pixi run mint-worker-token "gpu-host"
    pixi run mint-worker-token "gpu-host" --email worker@shelf.local

The plaintext is printed once and never stored. Save it somewhere
the worker host can read (e.g. ``~/.config/shelf-gpu/env``); shelf
only keeps the SHA-256 hash and the 12-char prefix.

If ``--email`` resolves to no user, one is created. The "worker"
user has no spaces and serves only as the token's owner-of-record;
it never logs in.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from sqlalchemy import select

from ..auth.tokens import mint
from ..db import session_factory
from ..models import ApiToken, User

DEFAULT_WORKER_EMAIL = "worker@shelf.local"


async def _mint(name: str, email: str) -> None:
    async with session_factory() as db:
        user = (
            await db.execute(select(User).where(User.email == email))
        ).scalar_one_or_none()
        if user is None:
            user = User(email=email, display_name="Worker")
            db.add(user)
            await db.flush()
        minted = mint()
        token = ApiToken(
            user_id=user.id,
            name=name,
            token_hash=minted.token_hash,
            prefix=minted.prefix,
            scopes=["worker"],
            allowed_collection_ids=None,
            expires_at=None,
        )
        db.add(token)
        await db.commit()

    sys.stderr.write(
        f"Minted worker token '{name}' for {email}.\n"
        f"  prefix: {minted.prefix}\n"
        f"  scope:  worker\n"
        f"\n"
        f"Plaintext (printed once — copy it now):\n"
    )
    print(minted.plaintext)


def main() -> None:
    p = argparse.ArgumentParser(
        prog="shelf-mint-worker-token",
        description="Mint a worker-scoped API token. Plaintext is "
        "printed to stdout; everything else goes to stderr so you "
        "can pipe the plaintext directly into a config file.",
    )
    p.add_argument("name", help="Human label, e.g. 'gpu-host'")
    p.add_argument(
        "--email",
        default=DEFAULT_WORKER_EMAIL,
        help=(
            "Owner-of-record email. The user is created if missing. "
            f"Defaults to {DEFAULT_WORKER_EMAIL!r}; override if you "
            "want one user-of-record per host."
        ),
    )
    args = p.parse_args()
    asyncio.run(_mint(args.name.strip(), args.email.strip()))


if __name__ == "__main__":
    main()
