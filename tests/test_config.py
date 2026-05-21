"""Tests pour gpu_dashboard.config — loader/saver de fichiers .env.

Le module gère :
- Parsing d'un fichier .env (KEY=VALUE, commentaires, blank lines, quoting)
- Écriture d'un dict en .env avec quoting sûr
- Une classe Config qui empile defaults → fichiers → env vars (priorité croissante)
- Accesseurs typés (get_bool, get_int, get_str)
"""
import os
import pytest

from gpu_dashboard.config import (
    parse_env_file,
    write_env_file,
    Config,
)


# ───────────────────────────── parse_env_file ─────────────────────────────

class TestParseEnvFile:
    def test_missing_file_returns_empty(self, tmp_path):
        assert parse_env_file(str(tmp_path / "missing.env")) == {}

    def test_basic_key_value(self, tmp_path):
        f = tmp_path / "a.env"
        f.write_text("PORT=9999\nHOST=localhost\n")
        assert parse_env_file(str(f)) == {"PORT": "9999", "HOST": "localhost"}

    def test_comments_and_blank_lines_ignored(self, tmp_path):
        f = tmp_path / "a.env"
        f.write_text("# header comment\n\nPORT=9999\n# inline-like\nMODE=prod\n\n")
        assert parse_env_file(str(f)) == {"PORT": "9999", "MODE": "prod"}

    def test_single_quoted_value(self, tmp_path):
        f = tmp_path / "a.env"
        f.write_text("MSG='hello world'\n")
        assert parse_env_file(str(f)) == {"MSG": "hello world"}

    def test_double_quoted_value(self, tmp_path):
        f = tmp_path / "a.env"
        f.write_text('MSG="hello world"\n')
        assert parse_env_file(str(f)) == {"MSG": "hello world"}

    def test_escaped_single_quote_in_single_quoted(self, tmp_path):
        # Convention shell : '\'' permet d'inclure une apostrophe
        f = tmp_path / "a.env"
        f.write_text("MSG='it'\\''s ok'\n")
        assert parse_env_file(str(f)) == {"MSG": "it's ok"}

    def test_value_with_equals_inside(self, tmp_path):
        # Seul le premier = sépare clé et valeur
        f = tmp_path / "a.env"
        f.write_text("FORMULA=a=b+c\n")
        assert parse_env_file(str(f)) == {"FORMULA": "a=b+c"}

    def test_empty_value(self, tmp_path):
        f = tmp_path / "a.env"
        f.write_text("EMPTY=\nNOT_EMPTY=x\n")
        assert parse_env_file(str(f)) == {"EMPTY": "", "NOT_EMPTY": "x"}

    def test_value_with_leading_trailing_spaces_stripped(self, tmp_path):
        f = tmp_path / "a.env"
        f.write_text("KEY=  value  \n")
        # Les espaces hors quotes sont strippés
        assert parse_env_file(str(f)) == {"KEY": "value"}

    def test_quoted_value_preserves_internal_spaces(self, tmp_path):
        f = tmp_path / "a.env"
        f.write_text("KEY='  with spaces  '\n")
        assert parse_env_file(str(f)) == {"KEY": "  with spaces  "}

    def test_malformed_line_skipped(self, tmp_path):
        # Une ligne sans = (et pas un commentaire) est ignorée silencieusement
        f = tmp_path / "a.env"
        f.write_text("VALID=1\ngarbage line no equals\nANOTHER=2\n")
        assert parse_env_file(str(f)) == {"VALID": "1", "ANOTHER": "2"}


# ───────────────────────────── write_env_file ─────────────────────────────

class TestWriteEnvFile:
    def test_basic_round_trip(self, tmp_path):
        path = str(tmp_path / "out.env")
        write_env_file(path, {"PORT": "9999", "HOST": "localhost"})
        assert parse_env_file(path) == {"PORT": "9999", "HOST": "localhost"}

    def test_value_with_special_chars_quoted(self, tmp_path):
        path = str(tmp_path / "out.env")
        write_env_file(path, {"MSG": "hello world", "PATH": "/usr/local/bin"})
        reloaded = parse_env_file(path)
        assert reloaded == {"MSG": "hello world", "PATH": "/usr/local/bin"}

    def test_value_with_quotes_round_trip(self, tmp_path):
        path = str(tmp_path / "out.env")
        write_env_file(path, {"MSG": "it's a \"test\""})
        assert parse_env_file(path) == {"MSG": "it's a \"test\""}

    def test_header_written(self, tmp_path):
        path = str(tmp_path / "out.env")
        write_env_file(path, {"A": "1"}, header="# Auto-généré, ne pas éditer")
        content = open(path).read()
        assert "# Auto-généré, ne pas éditer" in content
        assert "A=1" in content

    def test_overwrites_existing(self, tmp_path):
        path = str(tmp_path / "out.env")
        write_env_file(path, {"A": "old"})
        write_env_file(path, {"A": "new"})
        assert parse_env_file(path) == {"A": "new"}

    def test_atomic_write_creates_file(self, tmp_path):
        # L'écriture doit être atomique (pas de fichier .tmp qui reste)
        path = str(tmp_path / "out.env")
        write_env_file(path, {"A": "1"})
        assert os.path.exists(path)
        assert not os.path.exists(path + ".tmp")


# ─────────────────────────────── Config class ──────────────────────────────

class TestConfig:
    def test_defaults_only(self):
        cfg = Config(defaults={"PORT": "9999", "HOST": "localhost"})
        assert cfg.get("PORT") == "9999"
        assert cfg.get("HOST") == "localhost"

    def test_file_overrides_defaults(self, tmp_path):
        f = tmp_path / "override.env"
        f.write_text("PORT=8080\n")
        cfg = Config(defaults={"PORT": "9999", "HOST": "localhost"}, files=[str(f)])
        assert cfg.get("PORT") == "8080"
        assert cfg.get("HOST") == "localhost"  # default préservé

    def test_later_file_overrides_earlier(self, tmp_path):
        f1 = tmp_path / "a.env"; f1.write_text("X=1\nY=10\n")
        f2 = tmp_path / "b.env"; f2.write_text("X=2\n")
        cfg = Config(defaults={"X": "0"}, files=[str(f1), str(f2)])
        assert cfg.get("X") == "2"   # b écrase a
        assert cfg.get("Y") == "10"  # a préservé

    def test_env_var_overrides_files(self, tmp_path, monkeypatch):
        f = tmp_path / "a.env"; f.write_text("PORT=8080\n")
        monkeypatch.setenv("PORT", "7777")
        cfg = Config(defaults={"PORT": "9999"}, files=[str(f)])
        assert cfg.get("PORT") == "7777"

    def test_missing_key_returns_default_arg(self):
        cfg = Config(defaults={})
        assert cfg.get("MISSING", "fallback") == "fallback"
        assert cfg.get("MISSING") is None

    def test_get_bool_truthy(self):
        cfg = Config(defaults={"A": "1", "B": "true", "C": "TRUE", "D": "yes", "E": "on"})
        for k in "ABCDE":
            assert cfg.get_bool(k) is True

    def test_get_bool_falsy(self):
        cfg = Config(defaults={"A": "0", "B": "false", "C": "no", "D": "off", "E": ""})
        for k in "ABCDE":
            assert cfg.get_bool(k) is False

    def test_get_bool_unknown_returns_default(self):
        cfg = Config(defaults={"X": "weird"})
        assert cfg.get_bool("X", default=True) is True
        assert cfg.get_bool("X", default=False) is False
        assert cfg.get_bool("MISSING", default=True) is True

    def test_get_int_valid(self):
        cfg = Config(defaults={"PORT": "9999"})
        assert cfg.get_int("PORT") == 9999

    def test_get_int_invalid_returns_default(self):
        cfg = Config(defaults={"X": "abc"})
        assert cfg.get_int("X", default=42) == 42

    def test_set_and_save(self, tmp_path):
        cfg = Config(defaults={"A": "1"})
        cfg.set("B", "value")
        path = str(tmp_path / "saved.env")
        cfg.save(path)
        assert parse_env_file(path) == {"A": "1", "B": "value"}

    def test_set_overrides_lookup(self):
        cfg = Config(defaults={"X": "old"})
        cfg.set("X", "new")
        assert cfg.get("X") == "new"
