"""The ``sp ec`` engineering-context module."""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

from spareparts.modules.ec import doctor, gitignore, installer, projects


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

    diagnose = commands.add_parser("doctor", help="Check work-management access.")
    diagnose.add_argument("--dir", default=".", help="Workspace or repo root.")

    project = commands.add_parser("project", help="Configure and sync huddle stores.")
    project_commands = project.add_subparsers(dest="project_command", required=True)
    setup = project_commands.add_parser("setup", help="Guided huddle-store setup.")
    setup.add_argument("--dir", default=".", help="Workspace root.")
    setup.add_argument("--provider", choices=["github", "linear"])
    setup.add_argument("--url", help="GitHub Project or Linear workspace URL.")
    setup.add_argument("--team", help="Linear team name or key.")
    setup.add_argument("--transport", choices=["auto", "mcp", "api"], default="auto")
    configure = project_commands.add_parser("configure", help="Set the huddle store.")
    configure.add_argument("url", help="GitHub Project URL.")
    configure.add_argument("--dir", default=".", help="Workspace root.")
    status = project_commands.add_parser("status", help="Check configured project access.")
    status.add_argument("--dir", default=".", help="Workspace root.")
    sync = project_commands.add_parser("sync", help="Create or update a huddle draft item.")
    sync.add_argument("huddle", help="Path to huddle.md.")
    sync.add_argument("--dry-run", action="store_true")


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
    try:
        if args.ec_command == "install":
            result = _install(args)
            if result == 0:
                print("Run sp ec doctor --dir " + str(args.dir) + " to configure integrations.")
            return result
        if args.ec_command == "doctor":
            print(doctor.render(Path(args.dir)))
            return 0
        if args.project_command == "setup":
            provider = args.provider or input("Huddle store [github/linear]: ").strip().lower()
            if provider == "github":
                url = args.url or input("GitHub Project URL: ").strip()
                path = projects.configure(Path(args.dir), url)
            elif provider == "linear":
                url = args.url or input("Linear workspace URL or slug: ").strip()
                workspace = url.rstrip("/").rsplit("/", 1)[-1]
                team = args.team
                if team is None:
                    team = input("Linear team name/key (optional): ").strip() or None
                path = projects.configure_linear(Path(args.dir), workspace, team, args.transport)
            else:
                raise projects.ProjectError("provider must be github or linear")
            print(f"Configured {provider} huddle store -> {path}")
            print(f"Run sp ec doctor --dir {args.dir} to verify access.")
            return 0
        if args.project_command == "configure":
            path = projects.configure(Path(args.dir), args.url)
            print(f"Configured GitHub Projects huddle store -> {path}")
            return 0
        if args.project_command == "status":
            configured = projects.find_config(Path(args.dir))
            if configured is None:
                print("No GitHub Projects huddle store configured.")
                return 2
            _, project = configured
            view = projects.project_view(project)
            print(f"GitHub Project {view.get('title', project.url)} is accessible.")
            return 0
        result = projects.sync_huddle(Path(args.huddle), dry_run=args.dry_run)
        print(
            f"{result['action'].capitalize()} huddle item "
            f"{result['title']!r} in {result['project']}"
        )
        return 0
    except projects.ProjectError as error:
        print(f"sp ec: {error}")
        return 2
