"""Grant (or revoke) the admin role from the command line.

The normal way to get a first admin is SHELF_ADMIN_EMAILS, which promotes
on next login. This is the escape hatch for when that isn't practical —
an instance already running with no admin, or one that demoted its last
one before the guard existed:

    pixi run grant-admin someone@example.com
    pixi run grant-admin someone@example.com --revoke

Operates on an existing user; it does not create one, because an admin
who has never logged in has no identity rows and couldn't sign in anyway.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from sqlalchemy import select

from ..db import session_factory
from ..models import ROLE_ADMIN, ROLE_USER, User


async def _grant(email: str, *, revoke: bool) -> int:
    target = ROLE_USER if revoke else ROLE_ADMIN
    async with session_factory() as db:
        user = (
            await db.execute(select(User).where(User.email == email))
        ).scalar_one_or_none()
        if user is None:
            sys.stderr.write(
                f"No user with email {email!r}. They need to log in once first.\n"
            )
            return 1
        if user.role == target:
            sys.stderr.write(f"{email} is already {target!r}; nothing to do.\n")
            return 0
        user.role = target
        await db.commit()

    sys.stderr.write(f"{email} is now {target!r}.\n")
    return 0


def main() -> None:
    p = argparse.ArgumentParser(
        prog="shelf-grant-admin",
        description="Set a user's instance role.",
    )
    p.add_argument("email", help="Email of an existing user")
    p.add_argument(
        "--revoke",
        action="store_true",
        help="Set the role back to 'user' instead of granting admin.",
    )
    args = p.parse_args()
    raise SystemExit(asyncio.run(_grant(args.email.strip(), revoke=args.revoke)))


if __name__ == "__main__":
    main()
