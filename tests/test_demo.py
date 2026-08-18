"""Tests for qresp demo (pack 3, C1): cwd independence and the install boundary.

The demo assets (demo_store/, the eval questionnaire) ship with the repository
clone and the published Docker image — not with a bare 'pip install' of the
package (pyproject packages only src*/qresp*, so a wheel contains neither). A
missing store must therefore say so with the two supported paths and their
commands, never read like a broken build. And the command must work from any
working directory: its subprocess re-runs the real CLI and must resolve src/ and
the demo assets against the repo root, not the caller's cwd.
"""

from pathlib import Path

from click.testing import CliRunner

from src import data_dir
from src.pipeline import cli

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_demo_works_from_foreign_cwd(tmp_path, monkeypatch):
    """Running qresp demo from a directory that is not the repo root must work:
    the demo copies the store, runs the real CLI as a subprocess (cwd=REPO_ROOT),
    and writes the filled workbook into the repo's out/."""
    monkeypatch.chdir(tmp_path)  # a foreign cwd
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    result = CliRunner().invoke(cli, ["demo", "--no-ui"])
    assert result.exit_code == 0, result.output
    assert (REPO_ROOT / "out" / "demo" / "demo_filled.xlsx").exists()


def test_demo_missing_store_names_supported_paths(tmp_path, monkeypatch):
    """A non-editable install has no demo_store/ (and no .git): the error must
    name the two supported paths with their commands — and must NOT advise
    'qresp ingest', which needs fixtures a wheel does not ship either."""
    fake_site = tmp_path / "site-packages"
    fake_site.mkdir()
    monkeypatch.setattr(data_dir, "REPO_ROOT", fake_site)
    result = CliRunner().invoke(cli, ["demo", "--no-ui"])
    assert result.exit_code != 0
    assert "git clone" in result.output
    assert "docker run -p 8501:8501 ghcr.io/patricr73/qresp-demo:latest" in result.output
    assert "qresp ingest" not in result.output


def test_demo_missing_store_in_clone_suggests_rebuild(tmp_path, monkeypatch):
    """A checkout missing its committed demo_store/ (a .git dir is present) is a
    broken/partial clone, not an install problem: point at re-cloning or at the
    rebuild script."""
    fake_clone = tmp_path / "clone"
    (fake_clone / ".git").mkdir(parents=True)
    monkeypatch.setattr(data_dir, "REPO_ROOT", fake_clone)
    result = CliRunner().invoke(cli, ["demo", "--no-ui"])
    assert result.exit_code != 0
    assert "build_demo_store.py" in result.output
