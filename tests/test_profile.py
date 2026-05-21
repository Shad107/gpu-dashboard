"""Tests pour gpu_dashboard.profile — chargement et matching des profils GPU.

Le module charge les fichiers profiles/*.json et matche le nom GPU (depuis
nvidia-smi --query-gpu=name) contre les patterns `match` de chaque profil.

Le matching est :
- Case-insensitive
- Substring (le pattern apparaît dans le nom complet)
- Priorisé par longueur de pattern décroissante (« RTX 3090 Ti » avant « RTX 3090 »)

Si rien ne matche, on retombe sur le profil `_generic.json`.
"""
import json
import pytest

from gpu_dashboard.profile import (
    load_profile_file,
    load_profiles,
    load_schema,
    match_profile,
    get_profile_for_gpu,
    validate_profile,
)


# Schéma minimal réutilisable dans les tests
_MINIMAL_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "required": ["model", "match", "power"],
    "properties": {
        "model": {"type": "string"},
        "match": {"type": "array", "items": {"type": "string"}},
        "power": {
            "type": "object",
            "required": ["min", "max", "stock", "perf_curve"],
            "properties": {
                "min": {"type": "integer", "minimum": 30, "maximum": 1500},
                "max": {"type": "integer", "minimum": 30, "maximum": 1500},
                "stock": {"type": "integer", "minimum": 30, "maximum": 1500},
                "perf_curve": {"type": "array", "minItems": 1},
            },
        },
    },
}


def _valid_profile():
    """Retourne un profil minimal qui passe la validation."""
    return {
        "model": "Test",
        "match": ["Test"],
        "power": {
            "min": 100,
            "max": 350,
            "stock": 350,
            "perf_curve": [[100, 30], [350, 100]],
        },
    }


# ── Fixtures : on construit des profils dans tmp_path ──────────────────────


@pytest.fixture
def profiles_dir(tmp_path):
    """Crée un répertoire de profils factices pour les tests."""
    p = tmp_path / "profiles"
    p.mkdir()
    (p / "rtx-3090.json").write_text(json.dumps({
        "model": "RTX 3090",
        "match": ["RTX 3090"],
        "tdp_w": 350,
    }))
    (p / "rtx-3090-ti.json").write_text(json.dumps({
        "model": "RTX 3090 Ti",
        "match": ["RTX 3090 Ti"],
        "tdp_w": 450,
    }))
    (p / "rtx-4090.json").write_text(json.dumps({
        "model": "RTX 4090",
        "match": ["RTX 4090"],
        "tdp_w": 450,
    }))
    (p / "_generic.json").write_text(json.dumps({
        "model": "Generic NVIDIA GPU",
        "match": [],
        "_fallback": True,
        "tdp_w": None,
    }))
    return str(p)


# ────────────────────────── load_profile_file ──────────────────────────────


class TestLoadProfileFile:
    def test_loads_valid_json(self, tmp_path):
        f = tmp_path / "a.json"
        f.write_text('{"model": "X", "match": ["X"]}')
        assert load_profile_file(str(f)) == {"model": "X", "match": ["X"]}

    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            load_profile_file(str(tmp_path / "nope.json"))

    def test_invalid_json_raises(self, tmp_path):
        f = tmp_path / "bad.json"
        f.write_text("not json {{")
        with pytest.raises(json.JSONDecodeError):
            load_profile_file(str(f))


# ──────────────────────────── load_profiles ────────────────────────────────


class TestLoadProfiles:
    def test_loads_all_non_fallback(self, profiles_dir):
        profs = load_profiles(profiles_dir)
        models = sorted(p["model"] for p in profs)
        assert models == ["RTX 3090", "RTX 3090 Ti", "RTX 4090"]

    def test_fallback_excluded_from_main_list(self, profiles_dir):
        profs = load_profiles(profiles_dir)
        assert all(p["model"] != "Generic NVIDIA GPU" for p in profs)

    def test_sorted_by_specificity_desc(self, profiles_dir):
        """Les patterns les plus longs doivent venir en premier."""
        profs = load_profiles(profiles_dir)
        # "RTX 3090 Ti" (11 chars) doit être avant "RTX 3090" (8 chars)
        idx_ti = next(i for i, p in enumerate(profs) if p["model"] == "RTX 3090 Ti")
        idx_3090 = next(i for i, p in enumerate(profs) if p["model"] == "RTX 3090")
        assert idx_ti < idx_3090

    def test_empty_dir_returns_empty(self, tmp_path):
        empty = tmp_path / "empty"
        empty.mkdir()
        assert load_profiles(str(empty)) == []

    def test_missing_dir_returns_empty(self, tmp_path):
        assert load_profiles(str(tmp_path / "nope")) == []

    def test_ignores_underscore_prefix_except_generic(self, tmp_path):
        p = tmp_path / "profiles"
        p.mkdir()
        (p / "valid.json").write_text(json.dumps({"model": "V", "match": ["V"]}))
        (p / "_draft.json").write_text(json.dumps({"model": "D", "match": ["D"]}))
        (p / "_generic.json").write_text(json.dumps({"model": "G", "match": [], "_fallback": True}))
        profs = load_profiles(str(p))
        models = [pr["model"] for pr in profs]
        assert "V" in models
        assert "D" not in models
        assert "G" not in models  # _generic est exclu du main list

    def test_invalid_json_skipped_with_warning(self, tmp_path, capsys):
        p = tmp_path / "profiles"
        p.mkdir()
        (p / "good.json").write_text(json.dumps({"model": "OK", "match": ["OK"]}))
        (p / "bad.json").write_text("garbage {{")
        profs = load_profiles(str(p))
        # Le bon profil est chargé, le mauvais ignoré
        assert len(profs) == 1
        assert profs[0]["model"] == "OK"


# ──────────────────────────── match_profile ────────────────────────────────


class TestMatchProfile:
    def test_exact_match(self, profiles_dir):
        profs = load_profiles(profiles_dir)
        m = match_profile(profs, "NVIDIA GeForce RTX 3090")
        assert m["model"] == "RTX 3090"

    def test_case_insensitive(self, profiles_dir):
        profs = load_profiles(profiles_dir)
        assert match_profile(profs, "nvidia geforce rtx 3090")["model"] == "RTX 3090"
        assert match_profile(profs, "RTX 3090")["model"] == "RTX 3090"

    def test_ti_wins_over_3090(self, profiles_dir):
        """Pattern plus spécifique doit l'emporter."""
        profs = load_profiles(profiles_dir)
        m = match_profile(profs, "NVIDIA GeForce RTX 3090 Ti")
        assert m["model"] == "RTX 3090 Ti"

    def test_unknown_returns_none(self, profiles_dir):
        profs = load_profiles(profiles_dir)
        assert match_profile(profs, "AMD Radeon RX 7900 XTX") is None

    def test_empty_name_returns_none(self, profiles_dir):
        profs = load_profiles(profiles_dir)
        assert match_profile(profs, "") is None

    def test_no_profiles_returns_none(self):
        assert match_profile([], "RTX 3090") is None

    def test_multiple_match_patterns(self, tmp_path):
        """Un profil avec plusieurs patterns matche n'importe lequel."""
        p = tmp_path / "profiles"
        p.mkdir()
        (p / "multi.json").write_text(json.dumps({
            "model": "Multi",
            "match": ["Foo", "Bar", "Baz"],
        }))
        profs = load_profiles(str(p))
        assert match_profile(profs, "something with Bar in it")["model"] == "Multi"


# ───────────────────────── get_profile_for_gpu ─────────────────────────────


class TestValidateProfile:
    def test_valid_passes(self):
        validate_profile(_valid_profile(), _MINIMAL_SCHEMA)  # ne lève rien

    def test_missing_required_field_raises(self):
        p = _valid_profile()
        del p["model"]
        with pytest.raises(ValueError, match="model"):
            validate_profile(p, _MINIMAL_SCHEMA)

    def test_wrong_type_raises(self):
        p = _valid_profile()
        p["match"] = "should be a list"  # string au lieu de list
        with pytest.raises(ValueError) as exc:
            validate_profile(p, _MINIMAL_SCHEMA)
        assert "match" in str(exc.value)

    def test_nested_invalid_raises_with_path(self):
        p = _valid_profile()
        p["power"]["min"] = "not a number"
        with pytest.raises(ValueError) as exc:
            validate_profile(p, _MINIMAL_SCHEMA)
        # Le chemin doit pointer dans power
        assert "power" in str(exc.value) and "min" in str(exc.value)

    def test_cross_field_min_max(self):
        """power.min doit être < power.max (check non exprimable en JSON Schema)."""
        p = _valid_profile()
        p["power"]["min"] = 400
        p["power"]["max"] = 300
        with pytest.raises(ValueError, match="min.*max"):
            validate_profile(p, _MINIMAL_SCHEMA)

    def test_cross_field_stock_max(self):
        p = _valid_profile()
        p["power"]["stock"] = 500  # > max=350
        with pytest.raises(ValueError, match="stock"):
            validate_profile(p, _MINIMAL_SCHEMA)

    def test_cross_field_sweet_spot_in_range(self):
        p = _valid_profile()
        p["power"]["sweet_spot"] = 50  # < min=100
        with pytest.raises(ValueError, match="sweet_spot"):
            validate_profile(p, _MINIMAL_SCHEMA)


class TestLoadSchema:
    def test_loads_when_present(self, tmp_path):
        (tmp_path / "schema.json").write_text(json.dumps({"type": "object"}))
        s = load_schema(str(tmp_path))
        assert s == {"type": "object"}

    def test_returns_none_if_missing(self, tmp_path):
        assert load_schema(str(tmp_path)) is None

    def test_returns_none_if_invalid_json(self, tmp_path):
        (tmp_path / "schema.json").write_text("not json {{")
        assert load_schema(str(tmp_path)) is None


class TestLoadProfilesValidation:
    def test_invalid_profile_skipped_when_schema_present(self, tmp_path):
        (tmp_path / "schema.json").write_text(json.dumps(_MINIMAL_SCHEMA))
        # Profil valide
        (tmp_path / "good.json").write_text(json.dumps(_valid_profile()))
        # Profil invalide (manque power)
        (tmp_path / "bad.json").write_text(json.dumps({"model": "X", "match": ["X"]}))
        profs = load_profiles(str(tmp_path))
        assert len(profs) == 1
        assert profs[0]["model"] == "Test"

    def test_validate_false_skips_validation(self, tmp_path):
        (tmp_path / "schema.json").write_text(json.dumps(_MINIMAL_SCHEMA))
        (tmp_path / "bad.json").write_text(json.dumps({"model": "X", "match": ["X"]}))
        profs = load_profiles(str(tmp_path), validate=False)
        assert len(profs) == 1
        assert profs[0]["model"] == "X"

    def test_no_schema_skips_validation(self, tmp_path):
        # Pas de schema.json → on charge sans validation
        (tmp_path / "minimal.json").write_text(json.dumps({"model": "X", "match": ["X"]}))
        profs = load_profiles(str(tmp_path))
        assert len(profs) == 1


class TestGetProfileForGpu:
    def test_matched_card(self, profiles_dir):
        p = get_profile_for_gpu(profiles_dir, "NVIDIA GeForce RTX 3090")
        assert p["model"] == "RTX 3090"

    def test_unknown_card_returns_generic(self, profiles_dir):
        p = get_profile_for_gpu(profiles_dir, "AMD Radeon RX 7900 XTX")
        assert p.get("_fallback") is True
        assert p["model"] == "Generic NVIDIA GPU"

    def test_no_generic_returns_none(self, tmp_path):
        """Si pas de _generic.json et pas de match, on renvoie None (cas dégénéré)."""
        p = tmp_path / "profiles"
        p.mkdir()
        (p / "x.json").write_text(json.dumps({"model": "X", "match": ["X"]}))
        result = get_profile_for_gpu(str(p), "AMD Radeon")
        assert result is None
