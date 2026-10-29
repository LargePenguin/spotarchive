"""The self-updater. GitHub is faked; git runs against local repositories only."""

import shutil
import subprocess
import sys

import pytest
import requests

from spotarchive import cli, ui, update

from conftest import FakeResponse

# Captured before conftest's autouse fixture replaces it with a fake.
REAL_LATEST_VERSION = update.latest_version

needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="git isn't installed")


class FakeGet:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.urls = []

    def get(self, url, timeout=None):
        self.urls.append(url)
        if self.error:
            raise self.error
        return self.response


def text_response(text, status_code=200):
    response = FakeResponse(status_code)
    response.text = text
    return response


@pytest.mark.parametrize(
    "remote, local, newer",
    [
        ("0.2.0", "0.1.0", True),
        ("0.10.0", "0.9.1", True),
        ("1.0", "0.9.9", True),
        ("0.1.1", "0.1", True),
        ("0.1.0", "0.1.0", False),
        ("0.1.0", "0.2.0", False),
        ("0.2.0rc1", "0.2.0", False),
    ],
)
def test_is_newer(remote, local, newer):
    assert update.is_newer(remote, local) is newer


def test_read_version():
    assert update.read_version('"""Doc."""\n\n__version__ = "1.2.3"\n') == "1.2.3"
    with pytest.raises(update.UpdateError):
        update.read_version("nothing here")


def test_latest_version_reads_main_branch():
    http = FakeGet(text_response('__version__ = "9.9.9"\n'))
    assert REAL_LATEST_VERSION(http=http) == "9.9.9"
    assert http.urls == [update.REMOTE_VERSION_URL]


@pytest.mark.parametrize(
    "http",
    [
        FakeGet(error=requests.ConnectionError("offline")),
        FakeGet(text_response("not found", status_code=404)),
        FakeGet(text_response("<html>no version</html>")),
    ],
)
def test_latest_version_errors(http):
    with pytest.raises(update.UpdateError):
        REAL_LATEST_VERSION(http=http)


def test_project_root(tmp_path):
    package = tmp_path / "src" / "spotarchive"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text('__version__ = "0.1.0"\n')
    (tmp_path / "pyproject.toml").write_text("")
    assert update.project_root(package) is None  # not a git clone
    (tmp_path / ".git").mkdir()
    assert update.project_root(package) == tmp_path


# --- install_update against local git repositories ---------------------------


def git(cwd, *args):
    subprocess.run(
        ["git", "-c", "user.name=Test", "-c", "user.email=test@example.com", *args],
        cwd=cwd, check=True, capture_output=True,
    )


def commit_version(repo, version, pyproject="[project]\n"):
    (repo / "src" / "spotarchive").mkdir(parents=True, exist_ok=True)
    (repo / "src" / "spotarchive" / "__init__.py").write_text(f'__version__ = "{version}"\n')
    (repo / "pyproject.toml").write_text(pyproject)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", f"release {version}")


@pytest.fixture
def clone(tmp_path):
    """An "upstream" repo at 0.1.0 and a user clone of it; returns (upstream, clone)."""
    upstream = tmp_path / "upstream"
    upstream.mkdir()
    git(upstream, "init", "-q", "-b", "main")
    commit_version(upstream, "0.1.0")
    git(tmp_path, "clone", "-q", str(upstream), "clone")
    return upstream, tmp_path / "clone"


class RecordingRun:
    """Runs git for real but records (and fakes) pip."""

    def __init__(self, pip_returncode=0):
        self.pip_calls = []
        self.pip_returncode = pip_returncode

    def __call__(self, cmd, **kwargs):
        if cmd[:3] == [sys.executable, "-m", "pip"]:
            self.pip_calls.append(cmd)
            return subprocess.CompletedProcess(cmd, self.pip_returncode)
        return subprocess.run(cmd, **kwargs)


@needs_git
def test_install_update_pulls_without_reinstalling(clone):
    upstream, repo = clone
    commit_version(upstream, "0.2.0")
    run = RecordingRun()
    assert update.install_update(repo, run=run) == "0.2.0"
    assert run.pip_calls == []


@needs_git
def test_install_update_reinstalls_when_dependencies_change(clone):
    upstream, repo = clone
    commit_version(upstream, "0.2.0", pyproject='[project]\ndependencies = ["new-dep"]\n')
    run = RecordingRun()
    assert update.install_update(repo, run=run) == "0.2.0"
    assert len(run.pip_calls) == 1
    assert run.pip_calls[0][3:5] == ["install", "--editable"]
    assert run.pip_calls[0][5].startswith(str(repo))


@needs_git
def test_install_update_reports_failed_reinstall(clone):
    upstream, repo = clone
    commit_version(upstream, "0.2.0", pyproject='[project]\ndependencies = ["new-dep"]\n')
    with pytest.raises(update.UpdateError, match="reinstalling"):
        update.install_update(repo, run=RecordingRun(pip_returncode=1))


@needs_git
def test_install_update_without_new_commits_fails(clone):
    _, repo = clone
    with pytest.raises(update.UpdateError, match="nothing new"):
        update.install_update(repo, run=RecordingRun())


@needs_git
def test_install_update_reports_git_errors(clone):
    upstream, repo = clone
    commit_version(upstream, "0.2.0")
    # A local commit that diverges from upstream: no fast-forward possible.
    (repo / "local.txt").write_text("local change")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "local change")
    with pytest.raises(update.UpdateError, match="git pull"):
        update.install_update(repo, run=RecordingRun())


def test_install_update_refuses_worktrees(tmp_path):
    (tmp_path / ".git").write_text("gitdir: elsewhere\n")
    with pytest.raises(update.UpdateError, match="worktree"):
        update.install_update(tmp_path, run=RecordingRun())


# --- CLI ---------------------------------------------------------------------


@pytest.fixture
def newer(monkeypatch, tmp_path):
    """GitHub has a newer version; returns the list of installs performed."""
    installs = []
    monkeypatch.setattr(update, "latest_version", lambda http=None, timeout=10: "99.0.0")
    monkeypatch.setattr(update, "project_root", lambda: tmp_path)

    def fake_install(root):
        installs.append(root)
        return "99.0.0"

    monkeypatch.setattr(update, "install_update", fake_install)
    return installs


@pytest.fixture
def login_reached(monkeypatch):
    """Records whether the run went on to the login step, then stops it there."""
    reached = []

    def stop(store):
        reached.append(True)
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "ensure_login", stop)
    return reached


def answer(monkeypatch, value, interactive=True):
    monkeypatch.setattr(ui, "can_prompt", lambda: interactive)
    monkeypatch.setattr(ui, "confirm", lambda question, default=True: value)


def offline(http=None, timeout=10):
    raise update.UpdateError("Couldn't reach GitHub: offline")


def test_update_flag_installs_newer_version(newer, tmp_path):
    assert cli.main(["--update"], data_dir=tmp_path) == 0
    assert newer == [tmp_path]


def test_update_flag_when_up_to_date(monkeypatch, tmp_path):
    monkeypatch.setattr(update, "install_update", lambda root: pytest.fail("should not update"))
    assert cli.main(["-u"], data_dir=tmp_path) == 0


def test_update_flag_fails_when_github_is_unreachable(monkeypatch, tmp_path):
    monkeypatch.setattr(update, "latest_version", offline)
    assert cli.main(["-u"], data_dir=tmp_path) == 1


def test_update_flag_without_git_clone_fails(newer, monkeypatch, tmp_path):
    monkeypatch.setattr(update, "project_root", lambda: None)
    assert cli.main(["-u"], data_dir=tmp_path) == 1
    assert newer == []


def test_update_flag_cannot_be_combined(tmp_path):
    with pytest.raises(SystemExit):
        cli.main(["-uf"], data_dir=tmp_path)


def test_plain_run_updates_and_relaunches(newer, login_reached, monkeypatch, tmp_path):
    answer(monkeypatch, True)
    relaunched = []
    monkeypatch.setattr(cli, "relaunch", lambda argv: relaunched.append(argv) or 7)
    assert cli.main([], data_dir=tmp_path) == 7
    assert newer == [tmp_path]
    assert relaunched == [[]]
    assert login_reached == []  # the old code doesn't carry on


def test_relaunch_skips_the_update_check(monkeypatch):
    calls = []

    def fake_run(cmd, env=None):
        calls.append((cmd, env))
        return subprocess.CompletedProcess(cmd, 3)

    monkeypatch.setattr(cli.subprocess, "run", fake_run)
    assert cli.relaunch([]) == 3
    cmd, env = calls[0]
    assert cmd == [sys.executable, "-m", "spotarchive"]
    assert env[update.SKIP_CHECK_ENV] == "1"


@pytest.mark.parametrize("value, interactive", [(False, True), (True, False)])
def test_plain_run_continues_when_declined_or_not_interactive(newer, login_reached, monkeypatch, tmp_path,
                                                              value, interactive):
    answer(monkeypatch, value, interactive)
    cli.main([], data_dir=tmp_path)
    assert login_reached == [True]
    assert newer == []


def test_plain_run_cancelled_at_prompt(newer, login_reached, monkeypatch, tmp_path):
    answer(monkeypatch, None)
    assert cli.main([], data_dir=tmp_path) == 130
    assert login_reached == []


def test_plain_run_continues_when_update_fails(newer, login_reached, monkeypatch, tmp_path):
    answer(monkeypatch, True)

    def broken(root):
        raise update.UpdateError("git pull failed")

    monkeypatch.setattr(update, "install_update", broken)
    cli.main([], data_dir=tmp_path)
    assert login_reached == [True]


def test_plain_run_without_git_clone_only_tells(newer, login_reached, monkeypatch, tmp_path):
    monkeypatch.setattr(update, "project_root", lambda: None)
    monkeypatch.setattr(ui, "confirm", lambda *a, **kw: pytest.fail("should not ask"))
    cli.main([], data_dir=tmp_path)
    assert login_reached == [True]
    assert newer == []


def test_plain_run_continues_when_github_is_unreachable(login_reached, monkeypatch, tmp_path):
    monkeypatch.setattr(update, "latest_version", offline)
    cli.main([], data_dir=tmp_path)
    assert login_reached == [True]


def test_update_check_can_be_skipped(login_reached, monkeypatch, tmp_path):
    monkeypatch.setenv(update.SKIP_CHECK_ENV, "1")
    monkeypatch.setattr(update, "latest_version", lambda **kw: pytest.fail("should not check"))
    cli.main([], data_dir=tmp_path)
    assert login_reached == [True]


def test_steps_alone_skip_the_update_check(monkeypatch, tmp_path):
    monkeypatch.setattr(update, "latest_version", lambda **kw: pytest.fail("should not check"))
    monkeypatch.setattr(cli, "run_login", lambda store: None)
    assert cli.main(["--login"], data_dir=tmp_path) == 0
