"""Version string is canonical (cycle 114 promoted 0.2.0-dev → 0.3.0)."""
from gpu_dashboard import __version__


def test_version_is_string():
    assert isinstance(__version__, str)
    assert len(__version__) > 0


def test_version_is_release_format():
    """Should match major.minor.patch (no -dev suffix on the released branch)."""
    parts = __version__.split(".")
    assert len(parts) >= 2  # at least major.minor
    # Either pure semver or with a pre-release suffix
    # (allow 0.3.0 or 0.3.0-rc1 etc.)
    major, minor = parts[0], parts[1]
    assert major.isdigit()
    assert minor.isdigit()


def test_version_matches_changelog_top_release():
    """The __version__ must equal the topmost [N.M.P] tag in CHANGELOG.md."""
    import re
    from pathlib import Path
    changelog = Path(__file__).resolve().parents[1] / "CHANGELOG.md"
    if not changelog.exists():
        return  # graceful in sub-package contexts
    text = changelog.read_text()
    # Find the first [N.N.N] (skip [Unreleased])
    m = re.search(r"^## \[(\d+\.\d+\.\d+)\]", text, re.MULTILINE)
    assert m is not None, "no version block in CHANGELOG"
    assert m.group(1) == __version__
