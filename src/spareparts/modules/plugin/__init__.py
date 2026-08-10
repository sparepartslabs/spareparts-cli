"""Commands for discovering and installing curated agent plugins."""
from __future__ import annotations
import argparse, sys
from pathlib import Path
from spareparts.modules.ec import installer as ec_installer
from .catalog import CatalogError, find_plugin, load_catalog
from .installer import InstallError, command_asset, install, prepare

def register(parser: argparse.ArgumentParser) -> None:
    commands = parser.add_subparsers(dest="plugin_command", required=True); commands.add_parser("list", help="List plugins pinned by this sp release")
    install_parser = commands.add_parser("install", help="Install a pinned plugin for an agent"); install_parser.add_argument("name", choices=tuple(e.name for e in load_catalog())); install_parser.add_argument("--refresh", action="store_true", help="Download and verify the same pinned version again")
    targets = install_parser.add_mutually_exclusive_group()
    targets.add_argument("--agent", action="append", choices=sorted(ec_installer.AGENTS), help="Native agent target (repeatable)")
    targets.add_argument("--all", action="store_true", help="Install for every supported agent")
    install_parser.add_argument("--dir", default=".", help="Project root for native installation")
    install_parser.add_argument("--force", action="store_true", help="Replace existing native LGTM files")

def run(args: argparse.Namespace) -> int:
    try:
        if args.plugin_command == "list":
            for entry in load_catalog(): print(f"{entry.name} {entry.version} ({entry.install_identity})")
            return 0
        entry = find_plugin(args.name)
        agents = sorted(ec_installer.AGENTS) if args.all else list(dict.fromkeys(args.agent or []))
        if agents:
            marketplace_root = prepare(entry)
            source = command_asset(marketplace_root, entry)
            results = ec_installer.install_command_source(Path(args.dir), "lgtm", source, agents, args.force)
            written = sum((paths for _agent, paths, _skipped in results), [])
            skipped = sum((paths for _agent, _written, paths in results), [])
            for path in written: print(f"installed: {path}")
            for path in skipped: print(f"unchanged: {path} (use --force to overwrite)")
            print(f"{entry.install_identity} {entry.version}; start a new agent session to use it.")
            return 0
        result = install(entry, refresh=args.refresh)
    except (CatalogError, InstallError) as exc: print(f"sp plugin: {exc}", file=sys.stderr); return 1
    print(f"{result.outcome}: {entry.install_identity} {entry.version}"); print("Start a new Codex session to use the installed plugin."); return 0
