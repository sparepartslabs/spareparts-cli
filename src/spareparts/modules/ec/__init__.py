"""The ``sp ec`` engineering-context module."""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

from spareparts.modules.ec import gitignore, installer


def register(parser: argparse.ArgumentParser) -> None:
    commands = parser.add_subparsers(dest="ec_command", required=True)
    install = commands.add_parser(
        "install", help="Install Spec Kit commands and the .sp working area."
    )
    install.add_argument("--dir", default=".", help="Repo or workspace root.")
    install.add_argument(
        "--agent", action="append", choices=sorted(installer.AGENTS),
        help="Agent tool to target (repeatable). Default: auto-detect.",
    )
    install.add_argument("--all", action="store_true", help="Target every agent tool.")
    install.add_argument("--each", action="store_true", help="Install in each child repo.")
    install.add_argument("--root", action="store_true", help="Install at --dir itself.")
    install.add_argument("--ignore", action="append", default=[], metavar="PATH")
    install.add_argument("--force", action="store_true", help="Refresh installed files.")


def _migrate_working_area(dest: Path) -> None:
    old = dest / ".blitz"
    new = dest / installer.WORKDIR
    if old.exists() and not new.exists():
        old.rename(new)
        print(f"Migrated {old} -> {new}")
    elif old.exists():
        for source in sorted(old.rglob("*")):
            if not source.is_file():
                continue
            target = new / source.relative_to(old)
            if not target.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(source), target)

    if new.exists():
        for playbook in new.rglob("playbook.md"):
            constitution = playbook.with_name("constitution.md")
            if not constitution.exists():
                playbook.rename(constitution)
                print(f"Migrated {playbook} -> {constitution}")


def _resolve_agents(dest: Path, args: argparse.Namespace) -> list[str]:
    if args.all:
        return sorted(installer.AGENTS)
    if args.agent:
        return list(dict.fromkeys(args.agent))
    return installer.detect_agents(dest)


def _install_one(dest: Path, agents: list[str], force: bool) -> None:
    _migrate_working_area(dest)
    for agent, written, skipped in installer.install_commands(dest, agents, force):
        if written:
            print(f"Installed {len(written)} {agent} command(s) -> {dest / installer.AGENTS[agent].dir}/")
        if skipped:
            print(f"Skipped {len(skipped)} existing {agent} file(s); use --force to overwrite.")
    _, written, skipped = installer.install_scaffold(dest, force)
    if written:
        print(f"Installed {len(written)} file(s) into {dest / installer.WORKDIR}/")
    if skipped:
        print(f"Skipped {len(skipped)} existing {installer.WORKDIR}/ file(s).")
    rules = gitignore.ensure_rules(dest)
    if rules.status in {"created", "appended"}:
        print(f"Added {len(rules.added)} .sp ignore rule(s) -> {rules.path}")
    elif rules.status == "failed":
        print(f"warning: could not update {rules.path}: {rules.error}")
    if rules.blanket is not None:
        blanket = rules.blanket
        print(
            f"warning: {blanket.source}:{blanket.line} ignores the .sp working "
            f"area with pattern {blanket.pattern!r}."
        )
        print("warning: .sp/memory/constitution.md must remain trackable.")


def _install(args: argparse.Namespace) -> int:
    dest = Path(args.dir)
    repos = installer.find_git_repos(dest, ignore=[Path(path) for path in args.ignore])
    dest_is_repo = (dest / ".git").exists()
    each = args.each or (not args.root and bool(repos) and not dest_is_repo)
    if each:
        if not repos:
            print(f"No git repos found under {dest}/")
            return 2
        workspace_agents: list[str] = []
        for repo in repos:
            agents = _resolve_agents(repo, args)
            if not agents:
                print(f"[{repo.name}] no agent tool detected; skipping (pass --agent/--all).")
                continue
            print(f"[{repo.name}] installing for: {', '.join(agents)}")
            _install_one(repo, agents, args.force)
            workspace_agents.extend(agent for agent in agents if agent not in workspace_agents)
        if not dest_is_repo and workspace_agents:
            _migrate_working_area(dest)
            seeded = installer.install_workspace_scaffold(dest)
            if seeded:
                print(f"[workspace] seeded {seeded}")
            for agent, written, skipped in installer.install_workspace_commands(
                dest, workspace_agents, args.force
            ):
                if written:
                    print(f"[workspace] installed /huddle for {agent} -> {dest / installer.AGENTS[agent].dir}/")
                if skipped:
                    print(f"[workspace] skipped existing /huddle for {agent}.")
        return 0

    agents = _resolve_agents(dest, args)
    if not agents:
        print(
            "No agent tool detected in this repo. Re-run with --agent "
            f"({'/'.join(sorted(installer.AGENTS))}) or --all."
        )
        return 2
    _install_one(dest, agents, args.force)
    return 0


def run(args: argparse.Namespace) -> int:
    return _install(args)
