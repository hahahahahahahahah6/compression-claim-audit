"""CLI entry point for compression-claim-audit."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .audit import VERSION, audit


class _Parser(argparse.ArgumentParser):
    def error(self, message):  # noqa: D102 - usage errors exit 1 by spec
        self.print_usage(sys.stderr)
        print(f"{self.prog}: error: {message}", file=sys.stderr)
        raise SystemExit(1)


def build_parser() -> argparse.ArgumentParser:
    parser = _Parser(
        prog="compression-claim-audit",
        description="Audit documented size-reduction claims against Python source.",
    )
    parser.add_argument("root", nargs="?", default=".", help="Project root to audit")
    parser.add_argument("--json", action="store_true", help="Print JSON output")
    parser.add_argument("--verbose", action="store_true", help="Print claims found")
    parser.add_argument(
        "--ignore", action="append", default=[], help="Glob to ignore (repeatable)"
    )
    parser.add_argument(
        "--fail-on",
        choices=["HIGH", "MEDIUM"],
        default="MEDIUM",
        help="Minimum severity that fails (default MEDIUM)",
    )
    parser.add_argument("--version", action="store_true", help="Print version")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.version:
        print(VERSION)
        return 0
    root = Path(args.root)
    if not root.is_dir():
        print(f"error: not a directory: {args.root}", file=sys.stderr)
        return 1
    result = audit(root, ignore=args.ignore)
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(f"{result['status']}")
        if args.verbose:
            print(f"claims: {json.dumps(result['claims'], sort_keys=True)}")
            print(f"verified_claims: {json.dumps(result['verified_claims'], sort_keys=True)}")
        for finding in result["findings"]:
            print(f"{finding['severity']} {finding['rule']}")
            for item in finding["evidence"]:
                print(f"  {item}")
    if result["status"] == "PASS":
        return 0
    if args.fail_on == "HIGH":
        return 0 if all(f["severity"] == "MEDIUM" for f in result["findings"]) else 2
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
