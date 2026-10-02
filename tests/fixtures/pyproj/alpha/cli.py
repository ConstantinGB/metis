"""Command line entry point."""
import sys

from alpha.db import fetch_customers

from .core import Loader, load


def main(argv=None):
    argv = argv or sys.argv[1:]
    data = load(argv[0]) if argv else Loader().load()
    print(fetch_customers(data))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
