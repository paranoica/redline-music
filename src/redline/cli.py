"""Command line entry point."""

import argparse
from collections.abc import Sequence

from redline import __version__


def build_parser() -> argparse.ArgumentParser:
    """Return the top-level parser with its subcommands."""
    parser = argparse.ArgumentParser(prog="redline", description=__doc__)
    parser.add_argument("--version", action="version", version=f"redline {__version__}")
    parser.add_subparsers(dest="command", metavar="command")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the command line and return the exit code."""
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
    return 0
