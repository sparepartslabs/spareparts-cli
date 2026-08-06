import os
import subprocess
import sys
from pathlib import Path

import pytest

from spareparts.modules.lgtm import hook
from spareparts.modules.lgtm.git import GitError, hooks_dir


@pytest.fixture
def repo(tmp_path, monkeypatch):
    run = lambda *a: subprocess.run(a, cwd=tmp_path, check=True, capture_output=True)
    run("git", "init", "-q", "-b", "main")
    run("git", "config", "user.email", "t@t.t")
    run("git", "config", "user.name", "T")
    (tmp_path / "a.txt").write_text("hello\n")
    run("git", "add", "-A")
    run("git", "commit", "-q", "-m", "init")
    monkeypatch.chdir(tmp_path)
    return tmp_path


# --- the script ------------------------------------------------------------


def test_the_script_is_valid_posix_sh(repo):
    for blocking in (True, False):
        path = repo / "h.sh"
        path.write_text(hook.script("/bin/sp", blocking))
        # `sh -n` parses without executing. A hook with a syntax error fails
        # every commit, and nothing else in the suite would catch it.
        subprocess.run(["sh", "-n", str(path)], check=True)


def test_the_tty_probe_silences_stderr_before_it_opens(repo):
    """
    Redirections apply left to right, so `: < /dev/tty 2>/dev/null` prints
    "Device not configured" during an otherwise fine commit. The subshell form
    silences first.
    """
    # Comment lines are skipped: the naive form is quoted in the comment that
    # explains why it is wrong, and matching that is not a finding.
    code = [
        line
        for line in hook.script("/bin/sp", blocking=False).splitlines()
        if not line.lstrip().startswith("#")
    ]
    body = "\n".join(code)
    assert "(exec < /dev/tty) 2>/dev/null" in body
    assert ": < /dev/tty 2>/dev/null" not in body


def test_no_terminal_exits_zero_even_when_blocking(repo, tmp_path):
    # A rebase, a GUI client or CI must never be blocked by a question nobody
    # can see. Run with stdin closed and no controlling terminal.
    path = tmp_path / "hook.sh"
    path.write_text(hook.script("/bin/false", blocking=True))
    path.chmod(0o755)
    result = subprocess.run(
        ["sh", str(path)], stdin=subprocess.DEVNULL, capture_output=True, start_new_session=True
    )
    assert result.returncode == 0
    assert b"Device not configured" not in result.stderr


def test_the_skip_variable_short_circuits(repo, tmp_path):
    path = tmp_path / "hook.sh"
    # /bin/false stands in for sp: if it ever ran, a blocking hook would fail.
    path.write_text(hook.script("/bin/false", blocking=True))
    result = subprocess.run(
        ["sh", str(path)],
        env={**os.environ, "SP_LGTM_SKIP": "1"},
        stdin=subprocess.DEVNULL,
        capture_output=True,
    )
    assert result.returncode == 0


def test_only_a_wrong_answer_blocks(repo):
    blocking = hook.script("/bin/sp", blocking=True)
    # Exit 2 is "could not ask" — no API key, vendor outage, nothing quizzable.
    # It must never cost someone a commit.
    assert '"$status" -eq 1' in blocking
    assert "-eq 2" not in blocking


def test_advisory_never_blocks(repo):
    advisory = hook.script("/bin/sp", blocking=False)
    assert "exit 1" not in advisory


def test_the_script_uses_an_absolute_executable(repo):
    # A GUI client's PATH is not your shell's, and sp usually lives in a venv.
    body = hook.script(hook._executable(), blocking=False)
    invocation = next(l for l in body.splitlines() if "lgtm --staged" in l)
    assert invocation.lstrip().startswith(("/", '"/'))


def test_the_script_quizzes_the_index(repo):
    assert "lgtm --staged" in hook.script("/bin/sp", blocking=False)


# --- installing ------------------------------------------------------------


def test_install_writes_an_executable_hook(repo):
    result = hook.install()
    assert result.action == "installed"
    assert result.path == repo / ".git" / "hooks" / "pre-commit"
    assert os.access(result.path, os.X_OK)
    assert hook.MARKER in result.path.read_text()


def test_install_refuses_to_clobber_a_foreign_hook(repo):
    path = hooks_dir()
    path.mkdir(parents=True, exist_ok=True)
    (path / "pre-commit").write_text("#!/bin/sh\necho someone else's\n")

    with pytest.raises(GitError) as caught:
        hook.install()
    assert "--force" in str(caught.value)
    # And it really is untouched.
    assert "someone else's" in (path / "pre-commit").read_text()


def test_force_replaces_a_foreign_hook(repo):
    path = hooks_dir() / "pre-commit"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\necho mine\n")
    assert hook.install(force=True).action == "replaced"
    assert hook.MARKER in path.read_text()


def test_reinstalling_our_own_hook_needs_no_force(repo):
    hook.install()
    assert hook.install(blocking=True).action == "replaced"


def test_an_unknown_hook_name_is_rejected(repo):
    with pytest.raises(GitError):
        hook.install(hook="post-receive")


def test_pre_push_installs_too(repo):
    assert hook.install(hook="pre-push").path.name == "pre-push"


# --- uninstalling ----------------------------------------------------------


def test_uninstall_removes_ours(repo):
    hook.install()
    removed = hook.uninstall()
    assert removed is not None and removed.action == "removed"
    assert not removed.path.exists()


def test_uninstall_is_quiet_when_there_is_nothing(repo):
    assert hook.uninstall() is None


def test_uninstall_leaves_a_foreign_hook_alone(repo):
    path = hooks_dir() / "pre-commit"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\necho theirs\n")
    with pytest.raises(GitError):
        hook.uninstall()
    assert path.exists()


# --- where hooks live ------------------------------------------------------


def test_hooks_dir_honours_core_hooks_path(repo):
    # Writing to the assumed .git/hooks when core.hooksPath is set installs a
    # hook that never runs — which looks exactly like success.
    subprocess.run(["git", "config", "core.hooksPath", "myhooks"], cwd=repo, check=True)
    assert hooks_dir() == repo / "myhooks"
    installed = hook.install()
    assert installed.path == repo / "myhooks" / "pre-commit"


def test_hooks_dir_handles_an_absolute_hooks_path(repo, tmp_path):
    elsewhere = tmp_path / "shared-hooks"
    subprocess.run(
        ["git", "config", "core.hooksPath", str(elsewhere)], cwd=repo, check=True
    )
    assert hooks_dir() == elsewhere


def test_hooks_dir_defaults_to_the_git_dir(repo):
    assert hooks_dir() == repo / ".git" / "hooks"
