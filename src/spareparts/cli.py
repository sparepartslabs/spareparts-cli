"""
`sp` — the front door.

The only thing this file knows about a module is its name, its one-line help,
and where to import it from. Modules are imported lazily: the module named on
the command line is the only one loaded, so `sp lgtm` never pays for another
module's imports, and a module with a broken dependency cannot stop the rest of
the CLI from running. Top-level help is rendered from `MODULES` alone, without
importing anything.

Adding a module is one row in `MODULES` plus a package exposing `register(parser)`
and `run(args) -> int`. See `spareparts.modules.lgtm`.
"""

from __future__ import annotations

import argparse
import importlib
import sys
from dataclasses import dataclass
from typing import Sequence

from spareparts import __version__


@dataclass(frozen=True)
class Module:
    name: str
    summary: str
    package: str


MODULES: tuple[Module, ...] = (
    Module(
        name="ec",
        summary="Install engineering-context commands for coding agents",
        package="spareparts.modules.ec",
    ),
    Module(
        name="lgtm",
        summary="Prove you read a diff before you merge it",
        package="spareparts.modules.lgtm",
    ),
)

_INDEX = {m.name: m for m in MODULES}


def _usage() -> str:
    width = max(len(m.name) for m in MODULES)
    lines = [
        "sp — the Spare Parts command line.",
        "",
        "Usage:",
        "  sp <module> [options]",
        "",
        "Modules:",
    ]
    lines += [f"  {m.name.ljust(width)}  {m.summary}" for m in MODULES]
    lines += ["", "Run `sp <module> --help` for a module's own options."]
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)

    # `sp` alone is a question, not an error: print the module list and exit
    # clean, so the exit code stays meaningful to anything scripting this.
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(_usage())
        return 0

    if argv[0] in ("-V", "--version"):
        print(f"sp {__version__}")
        return 0

    module = _INDEX.get(argv[0])
    if module is None:
        print(f"sp: unknown module {argv[0]!r}\n", file=sys.stderr)
        print(_usage(), file=sys.stderr)
        return 2

    impl = importlib.import_module(module.package)

    parser = argparse.ArgumentParser(prog=f"sp {module.name}", description=module.summary)
    impl.register(parser)
    args = parser.parse_args(argv[1:])

    try:
        return impl.run(args)
    except KeyboardInterrupt:
        # Ctrl-C mid-quiz is a normal way to leave. It is not a crash, and it
        # should not print a traceback at someone.
        print()
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
