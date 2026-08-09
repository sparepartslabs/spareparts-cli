"""Install Spec Kit-derived slash commands into a repo.

The packaged command bodies are spec-driven-development
slash-commands adapted from GitHub's spec-kit (https://github.com/github/spec-kit).
At install time we render each agent-agnostic body into the conventional command
file for whichever agentic coding tool the user runs (Claude Code, Codex, Cursor, GitHub Copilot, Gemini
CLI, OpenCode), and drop a shared ``.sp/`` working area (scripts +
templates) that those commands drive.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

# --- per-agent adapter table -------------------------------------------------


@dataclass(frozen=True)
class AgentSpec:
    dir: str  # command directory relative to repo root
    ext: str  # command filename suffix
    fmt: str  # "md", "toml", or "skill"
    arg: str  # token the user's argument text is substituted with
    markers: tuple[str, ...]  # paths whose presence means "this tool is used here"


# Copilot deliberately does NOT key off a bare ".github/" (almost every repo
# has one for CI); it needs a Copilot-specific signal — a prompts dir or a
# copilot-instructions file — so plain CI repos aren't misclassified.
AGENTS: dict[str, AgentSpec] = {
    "claude": AgentSpec(".claude/commands", ".md", "md", "$ARGUMENTS", (".claude",)),
    "codex": AgentSpec(
        ".agents/skills", "/SKILL.md", "skill", "the user request that invoked this skill",
        ("AGENTS.md", ".agents", ".codex"),
    ),
    "cursor": AgentSpec(".cursor/commands", ".md", "md", "$ARGUMENTS", (".cursor",)),
    "copilot": AgentSpec(
        ".github/prompts", ".prompt.md", "md", "$ARGUMENTS",
        (".github/prompts", ".github/copilot-instructions.md"),
    ),
    "gemini": AgentSpec(".gemini/commands", ".toml", "toml", "{{args}}", (".gemini",)),
    "opencode": AgentSpec(".opencode/command", ".md", "md", "$ARGUMENTS", (".opencode",)),
}

WORKDIR = ".sp"

_COMMANDS_PKG = "spareparts.modules.ec.commands"
# Workspace-only commands (e.g. /huddle) install into a *folder of repos*, never
# into an individual repo — they orchestrate across the repos' .sp/ dirs.
_WORKSPACE_PKG = "spareparts.modules.ec.workspace"

# Spec Kit cross-references commands as ``__SPECKIT_COMMAND_<NAME>__``.

InstallResult = tuple[str, list[str], list[str]]  # (agent, written, skipped)


# --- command rendering -------------------------------------------------------


def command_names(package: str = _COMMANDS_PKG) -> list[str]:
    """Every packaged command body, by file stem (e.g. "specify", "constitution")."""
    root = resources.files(package)
    return sorted(
        entry.name[:-3]
        for entry in root.iterdir()
        if entry.is_file() and entry.name.endswith(".md")
    )


def _cmd_ref(name: str) -> str:
    return "/" + name


def _split_frontmatter(text: str) -> tuple[str, str]:
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        return "", text
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            return "".join(lines[1:i]), "".join(lines[i + 1 :]).lstrip("\n")
    return "", text


def _frontmatter_value(fm: str, key: str) -> str:
    m = re.search(rf"^\s*{re.escape(key)}:\s*(.+)$", fm, re.M)
    return m.group(1).strip() if m else ""


def _rewrite_paths(text: str) -> str:
    # Applied to both command bodies and scaffold files so command cross-refs
    # (__SPECKIT_COMMAND_PLAN__ -> /plan) and stale paths resolve everywhere,
    # including the plan/tasks templates copied verbatim by install_scaffold.
    text = re.sub(
        r"__SPECKIT_COMMAND_([A-Z]+)__",
        lambda m: _cmd_ref(m.group(1).lower()),
        text,
    )
    text = text.replace(".specify", WORKDIR)
    return text


def _render_body(body: str, sh_script: str, arg_token: str) -> str:
    if sh_script:
        body = body.replace("{SCRIPT}", f"{WORKDIR}/{sh_script.strip()}")
    body = body.replace("$ARGUMENTS", arg_token).replace("{ARGS}", arg_token)
    return _rewrite_paths(body)


def _wrap(fmt: str, name: str, description: str, body: str) -> str:
    if fmt == "toml":
        desc = description.replace("\\", "\\\\").replace('"', '\\"')
        prompt = body.replace("\\", "\\\\").replace('"""', '\\"\\"\\"')
        return f'description = "{desc}"\n\nprompt = """\n{prompt}\n"""\n'
    if fmt == "skill":
        safe_description = description.replace("\n", " ").replace(chr(34), "\\\"")
        return f"---\nname: {name}\ndescription: \"{safe_description}\"\n---\n\n{body}"
    if description:
        return f"---\ndescription: {description}\n---\n\n{body}"
    return body


def render(command: str, agent: str, package: str = _COMMANDS_PKG) -> str:
    """Render one packaged command body into ``agent``'s command-file format."""
    spec = AGENTS[agent]
    raw = (
        resources.files(package)
        .joinpath(f"{command}.md")
        .read_text(encoding="utf-8")
    )
    fm, body = _split_frontmatter(raw)
    description = _frontmatter_value(fm, "description")
    sh_script = ""
    scripts_m = re.search(r"^scripts:\s*$", fm, re.M)
    if scripts_m:
        sh_script = _frontmatter_value(fm[scripts_m.end() :], "sh")
    body = _render_body(body, sh_script, spec.arg)
    return _wrap(spec.fmt, command, description, body)


# --- scaffold (shared .sp working area) ----------------------------------


def _walk(node, prefix: str = ""):
    """Yield (relative_path, Traversable) for every file under ``node``."""
    for entry in node.iterdir():
        if entry.name == "__init__.py" or entry.name == "__pycache__":
            continue
        rel = f"{prefix}{entry.name}"
        if entry.is_dir():
            yield from _walk(entry, prefix=f"{rel}/")
        else:
            yield rel, entry


# --- install -----------------------------------------------------------------


def detect_agents(dest_dir: Path) -> list[str]:
    """Agents with a tool-specific marker already present under ``dest_dir``."""
    return [
        key
        for key, spec in AGENTS.items()
        if any((dest_dir / m).exists() for m in spec.markers)
    ]


_SKIP_DIRS = {"node_modules", "__pycache__", ".venv", "venv", "vendor", "dist", "build"}


def find_git_repos(
    dest_dir: Path, max_depth: int = 4, ignore: list[Path] | None = None
) -> list[Path]:
    """Every git repo at or below ``dest_dir``, descending through non-git folders.

    Stops at each git repo (does not look for nested repos inside one), so a
    workspace like ``sparepartslabs/`` finds both top-level repos and repos
    nested inside plain folders. Skips
    hidden and heavy build directories; bounded by ``max_depth``. ``ignore``
    lists paths to prune — a directory matching (or nested under) any ignored
    path is neither installed into nor descended into.
    """
    ignore_set = {p.resolve() for p in (ignore or [])}

    def _ignored(path: Path) -> bool:
        rp = path.resolve()
        return any(rp == ig or ig in rp.parents for ig in ignore_set)

    def _walk(d: Path, depth: int) -> list[Path]:
        found: list[Path] = []
        for child in sorted(d.iterdir()):
            if not child.is_dir() or child.name.startswith(".") or child.name in _SKIP_DIRS:
                continue
            if _ignored(child):
                continue
            if (child / ".git").exists():
                found.append(child)  # a repo boundary — don't descend inside it
            elif depth < max_depth:
                found.extend(_walk(child, depth + 1))
        return found

    if not dest_dir.is_dir():
        return []
    return _walk(dest_dir, 0)


def _write(target: Path, text: str, force: bool, written: list, skipped: list) -> None:
    if target.exists() and not force:
        skipped.append(str(target))
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    written.append(str(target))


def install_commands(
    dest_dir: Path, agents: list[str], force: bool
) -> list[InstallResult]:
    commands = command_names()
    results: list[InstallResult] = []
    for agent in agents:
        spec = AGENTS[agent]
        written: list[str] = []
        skipped: list[str] = []
        for command in commands:
            target = dest_dir / spec.dir / f"{command}{spec.ext}"
            _write(target, render(command, agent), force, written, skipped)
        results.append((agent, written, skipped))
    return results


def workspace_command_names() -> list[str]:
    """Workspace-only command bodies (e.g. "huddle"), by file stem."""
    return command_names(_WORKSPACE_PKG)


def install_workspace_scaffold(dest_dir: Path) -> Path | None:
    """Seed the workspace-level ``.sp/memory/constitution.md`` from the
    default template. Unlike command installs this NEVER overwrites — the
    file holds user-authored operating knowledge, so even ``--force`` must
    not reset it. Returns the written path, or None when it already exists."""
    target = dest_dir / WORKDIR / "memory" / "constitution.md"
    if target.exists():
        return None
    template = (
        resources.files("spareparts.modules.ec")
        .joinpath("workspace_constitution.md.tmpl")
        .read_text(encoding="utf-8")
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(template, encoding="utf-8")
    return target


def install_workspace_commands(
    dest_dir: Path, agents: list[str], force: bool
) -> list[InstallResult]:
    """Install workspace-only commands (/huddle) into ``dest_dir``'s command dirs.

    ``dest_dir`` is a workspace root — a folder of repos, not a repo itself.
    No repo scaffold is installed here; /huddle creates ``.sp/huddles/``
    on first use.
    """
    commands = workspace_command_names()
    results: list[InstallResult] = []
    for agent in agents:
        spec = AGENTS[agent]
        written: list[str] = []
        skipped: list[str] = []
        for command in commands:
            target = dest_dir / spec.dir / f"{command}{spec.ext}"
            _write(target, render(command, agent, _WORKSPACE_PKG), force, written, skipped)
        results.append((agent, written, skipped))
    return results


def install_scaffold(dest_dir: Path, force: bool) -> InstallResult:
    """Copy the shared ``.sp/`` working area, rewriting stale paths.

    Files under ``.sp/memory/`` are seeded once and NEVER overwritten,
    even with ``force`` — they hold user-authored knowledge (a repo's
    constitution), and ``--force`` is for refreshing rendered
    commands and scaffold templates, not resetting memory."""
    root = resources.files("spareparts.modules.ec.scaffold")
    written: list[str] = []
    skipped: list[str] = []
    for rel, entry in _walk(root):
        target = dest_dir / rel
        text = _rewrite_paths(entry.read_text(encoding="utf-8"))
        existed = target.exists()
        is_memory = rel.startswith(f"{WORKDIR}/memory/")
        _write(target, text, force and not is_memory, written, skipped)
        if rel.endswith(".sh") and target.exists() and not (existed and not force):
            target.chmod(0o755)
    return ("scaffold", written, skipped)
