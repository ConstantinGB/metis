"""Command line tool for maintenance tasks."""
import argparse


def cmd_sync(args):
    """Sync customers from the upstream system."""
    return 0


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd")
    s = sub.add_parser("sync", help="sync customers")
    s.set_defaults(fn=cmd_sync)
    args = parser.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
