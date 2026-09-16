"""`python -m shelf_cli`, for running without the console script."""

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
