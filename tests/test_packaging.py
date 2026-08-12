"""Guards that report assets stay importable from an installed wheel.

templates/, static/, and content/ used to live at the repo root, anchored via
`Path(__file__).parent * 4`. That resolved to the repo in a source checkout but
to `<site-packages>/..` once installed, so `ctm-report` could not find its own
templates off a checkout. These tests fail if anything drifts back that way.
"""
from pathlib import Path

import ctm
from ctm.paths import DEFAULT_KB_PATH, cache_dir, cache_path
from ctm.reports.builder import METHODS_PATH, STATIC_DIR, TEMPLATES_DIR

PACKAGE_ROOT = Path(ctm.__file__).parent


def test_asset_paths_live_inside_the_package():
    """Anchored to ctm/, not the repo root — this is what survives packaging."""
    for path in (TEMPLATES_DIR, STATIC_DIR, METHODS_PATH):
        assert path.is_relative_to(PACKAGE_ROOT), f"{path} escapes {PACKAGE_ROOT}"


def test_assets_exist():
    assert TEMPLATES_DIR.is_dir()
    assert (STATIC_DIR / "report.css").is_file()
    assert METHODS_PATH.is_file()


def test_knowledge_base_ships_with_the_package():
    """--kb defaults to this, so a fresh install must be able to curate trials."""
    assert DEFAULT_KB_PATH.is_relative_to(PACKAGE_ROOT)
    assert DEFAULT_KB_PATH.is_file()

    from ctm.transformers.trials_curate import load_known_genes

    assert len(load_known_genes(DEFAULT_KB_PATH)) > 0


def test_cache_dir_precedence(monkeypatch, tmp_path):
    """CTM_CACHE_DIR beats XDG_CACHE_HOME beats ~/.cache — containers need the override."""
    monkeypatch.delenv("CTM_CACHE_DIR", raising=False)
    monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
    assert cache_dir() == Path.home() / ".cache" / "ctm"

    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg"))
    assert cache_dir() == tmp_path / "xdg" / "ctm"

    monkeypatch.setenv("CTM_CACHE_DIR", str(tmp_path / "explicit"))
    assert cache_dir() == tmp_path / "explicit"


def test_caches_never_land_in_the_repo(monkeypatch, tmp_path):
    """LLM caches reach hundreds of MB; they must not resolve into the checkout."""
    monkeypatch.setenv("CTM_CACHE_DIR", str(tmp_path / "c"))
    resolved = cache_path("x.json")
    assert resolved.parent.is_dir(), "cache_path should create its parent"
    assert not resolved.is_relative_to(PACKAGE_ROOT)


def test_every_included_template_is_present():
    """A packaged report.html is useless if its {% include %}s were left behind."""
    base = TEMPLATES_DIR / "report.html"
    includes = {
        line.split('"')[1]
        for line in base.read_text().splitlines()
        if "{% include" in line
    }
    assert includes, "expected report.html to include partials"
    for name in includes:
        assert (TEMPLATES_DIR / name).is_file(), f"missing partial: {name}"
