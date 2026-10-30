"""The self-updater. GitHub is faked."""

import io
import os
import subprocess
import sys
import zipfile

import pytest
import requests

from spotarchive import cli, ui, update

from conftest import FakeResponse

# Captured before conftest's autouse fixture replaces it with a fake.
REAL_LATEST_VERSION = update.latest_version

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
    assert update.project_root(package) is None  # no version file
    (package / "__init__.py").write_text('__version__ = "0.1.0"\n')
    assert update.project_root(package) is None  # no pyproject.toml
    (tmp_path / "pyproject.toml").write_text("")
    assert update.project_root(package) == tmp_path  # no git needed


# --- install_update against a fake GitHub archive ----------------------------


def make_zip(version, pyproject="[project]\n", extra=None):
    files = {
        "src/spotarchive/__init__.py": f'__version__ = "{version}"\n',
        "pyproject.toml": pyproject,
        "install.sh": "#!/bin/sh\n",
        **(extra or {}),
    }
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, content in files.items():
            info = zipfile.ZipInfo(f"spotarchive-main/{name}")
            info.external_attr = (0o755 if name.endswith(".sh") else 0o644) << 16
            archive.writestr(info, content)
    return buffer.getvalue()


class ArchiveHttp:
    def __init__(self, data, status_code=200):
        self.response = FakeResponse(status_code)
        self.response.content = data
        self.urls = []

    def get(self, url, timeout=None):
        self.urls.append(url)
        return self.response


@pytest.fixture
def install(tmp_path):
    """An installed 0.1.0 checkout with user files that aren't in the repository."""
    root = tmp_path / "install"
    (root / "src" / "spotarchive").mkdir(parents=True)
    (root / "src" / "spotarchive" / "__init__.py").write_text('__version__ = "0.1.0"\n')
    (root / "pyproject.toml").write_text("[project]\n")
    (root / "spotarchive.toml").write_text("# mine\n")
    (root / "data").mkdir()
    (root / "data" / "session.json").write_text("{}")
    return root


class RecordingRun:
    def __init__(self, returncode=0):
        self.calls = []
        self.returncode = returncode

    def __call__(self, cmd, **kwargs):
        self.calls.append(cmd)
        return subprocess.CompletedProcess(cmd, self.returncode)


def test_install_update_replaces_files_and_keeps_user_files(install):
    http = ArchiveHttp(make_zip("0.2.0", extra={"docs/new.md": "new"}))
    run = RecordingRun()
    assert update.install_update(install, run=run, http=http) == "0.2.0"
    assert http.urls == [update.ARCHIVE_URL]
    assert (install / "docs" / "new.md").read_text() == "new"
    assert (install / "spotarchive.toml").read_text() == "# mine\n"
    assert (install / "data" / "session.json").read_text() == "{}"
    assert not list(install.rglob("*.update-new"))
    assert run.calls == []  # pyproject.toml unchanged


@pytest.mark.skipif(os.name != "posix", reason="file modes")
def test_install_update_keeps_executable_bit(install):
    update.install_update(install, run=RecordingRun(), http=ArchiveHttp(make_zip("0.2.0")))
    assert os.access(install / "install.sh", os.X_OK)


def test_install_update_reinstalls_when_dependencies_change(install):
    http = ArchiveHttp(make_zip("0.2.0", pyproject='[project]\ndependencies = ["new-dep"]\n'))
    run = RecordingRun()
    assert update.install_update(install, run=run, http=http) == "0.2.0"
    assert len(run.calls) == 1
    assert run.calls[0][3:5] == ["install", "--editable"]
    assert run.calls[0][5].startswith(str(install))


def test_install_update_reports_failed_reinstall(install):
    http = ArchiveHttp(make_zip("0.2.0", pyproject='[project]\ndependencies = ["new-dep"]\n'))
    with pytest.raises(update.UpdateError, match="reinstalling"):
        update.install_update(install, run=RecordingRun(returncode=1), http=http)


@pytest.mark.parametrize(
    "http",
    [
        ArchiveHttp(b"", status_code=404),
        ArchiveHttp(b"not a zip"),
        ArchiveHttp(make_zip("0.2.0", extra={"../evil.txt": "x"})),
    ],
)
def test_install_update_rejects_bad_downloads(install, http):
    with pytest.raises(update.UpdateError):
        update.install_update(install, run=RecordingRun(), http=http)
    assert (install / "src" / "spotarchive" / "__init__.py").read_text() == '__version__ = "0.1.0"\n'
    assert not (install.parent / "evil.txt").exists()


def test_install_update_rejects_other_projects(install):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("other-main/README.md", "hi")
    with pytest.raises(update.UpdateError, match="doesn't look like"):
        update.install_update(install, run=RecordingRun(), http=ArchiveHttp(buffer.getvalue()))


def test_install_update_reports_unreachable_github(install):
    class Offline:
        def get(self, url, timeout=None):
            raise requests.ConnectionError("offline")

    with pytest.raises(update.UpdateError, match="download"):
        update.install_update(install, run=RecordingRun(), http=Offline())


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


def test_update_flag_without_checkout_fails(newer, monkeypatch, tmp_path):
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
        raise update.UpdateError("download failed")

    monkeypatch.setattr(update, "install_update", broken)
    cli.main([], data_dir=tmp_path)
    assert login_reached == [True]


def test_plain_run_without_checkout_only_tells(newer, login_reached, monkeypatch, tmp_path):
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
