"""Chargement et matching des profils GPU.

Un profil est un JSON dans `profiles/`. Le matching prend un nom de GPU
(typiquement issu de `nvidia-smi --query-gpu=name`) et cherche le premier
profil dont l'un des patterns `match` apparaît (case-insensitive) dans le nom.

Priorité : les profils sont triés par longueur de pattern décroissante, donc
un pattern spécifique (« RTX 3090 Ti ») gagne sur un pattern générique (« RTX 3090 »).

Si rien ne matche, fallback sur `_generic.json`.
"""
from __future__ import annotations

import json
import os
import sys
from typing import Optional


GENERIC_FILENAME = "_generic.json"
SCHEMA_FILENAME = "schema.json"


# ──────────────────────────── validation JSON Schema ───────────────────────


def load_schema(profiles_dir: str) -> Optional[dict]:
    """Charge `profiles/schema.json`. Retourne None s'il est absent."""
    schema_path = os.path.join(profiles_dir, SCHEMA_FILENAME)
    if not os.path.isfile(schema_path):
        return None
    try:
        with open(schema_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def validate_profile(profile: dict, schema: dict) -> None:
    """Valide un profil contre un schéma JSON Schema (draft 2020-12).

    Lève ValueError avec un message clair (chemin de l'erreur + raison)
    si le profil est invalide. Effectue aussi quelques checks cross-field
    qui ne s'expriment pas en JSON Schema (ex: power.min < power.max).
    """
    import jsonschema  # import paresseux : permet d'utiliser profile.py sans dep si validation désactivée
    try:
        jsonschema.validate(profile, schema)
    except jsonschema.ValidationError as e:
        path = "$" + "".join(f"[{p!r}]" if isinstance(p, int) else f".{p}" for p in e.absolute_path)
        raise ValueError(f"invalid profile at {path}: {e.message}") from e

    # Cross-field checks
    pw = profile.get("power", {})
    if pw.get("min") is not None and pw.get("max") is not None:
        if pw["min"] >= pw["max"]:
            raise ValueError(
                f"invalid profile at $.power: min ({pw['min']}) must be < max ({pw['max']})"
            )
    if pw.get("stock") is not None and pw.get("max") is not None:
        if pw["stock"] > pw["max"]:
            raise ValueError(
                f"invalid profile at $.power: stock ({pw['stock']}) must be <= max ({pw['max']})"
            )
    if pw.get("sweet_spot") is not None and pw.get("min") is not None and pw.get("max") is not None:
        if not (pw["min"] <= pw["sweet_spot"] <= pw["max"]):
            raise ValueError(
                f"invalid profile at $.power.sweet_spot ({pw['sweet_spot']}): "
                f"must be within [min={pw['min']}, max={pw['max']}]"
            )


def load_profile_file(path: str) -> dict:
    """Charge un fichier JSON de profil. Lève FileNotFoundError ou JSONDecodeError."""
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_profiles(profiles_dir: str, validate: bool = True) -> list:
    """Charge tous les profils du répertoire, triés par spécificité décroissante.

    Sont exclus :
    - Les fichiers commençant par `_` (drafts, conventions, fallback `_generic.json`)
    - Le fichier `schema.json` (c'est le schéma, pas un profil)
    - Les JSON invalides (warning sur stderr, fichier ignoré)
    - Si `validate=True` : les profils qui échouent la validation contre `schema.json`

    Le tri par `max(len(pattern))` décroissant garantit qu'un pattern plus
    spécifique (« RTX 3090 Ti ») est essayé avant un moins spécifique (« RTX 3090 »).
    """
    if not os.path.isdir(profiles_dir):
        return []

    schema = load_schema(profiles_dir) if validate else None

    profiles: list = []
    for filename in sorted(os.listdir(profiles_dir)):
        if not filename.endswith(".json"):
            continue
        if filename.startswith("_") or filename == SCHEMA_FILENAME:
            continue
        path = os.path.join(profiles_dir, filename)
        try:
            data = load_profile_file(path)
        except (json.JSONDecodeError, OSError) as e:
            print(f"warning: skipping invalid profile '{filename}': {e}", file=sys.stderr)
            continue

        if schema is not None:
            try:
                validate_profile(data, schema)
            except ValueError as e:
                print(f"warning: schema validation failed for '{filename}': {e}", file=sys.stderr)
                continue

        profiles.append(data)

    def _max_pattern_len(p: dict) -> int:
        patterns = p.get("match", []) or []
        return max((len(s) for s in patterns if s), default=0)

    profiles.sort(key=_max_pattern_len, reverse=True)
    return profiles


def match_profile(profiles: list, gpu_name: str) -> Optional[dict]:
    """Retourne le premier profil dont un pattern matche `gpu_name`, ou None.

    Le matching est case-insensitive substring : un pattern matche s'il apparaît
    quelque part dans le nom GPU complet (typiquement « NVIDIA GeForce RTX 3090 »).
    """
    if not gpu_name:
        return None
    name_lower = gpu_name.lower()
    for p in profiles:
        for pattern in p.get("match", []) or []:
            if pattern and pattern.lower() in name_lower:
                return p
    return None


def get_profile_for_gpu(profiles_dir: str, gpu_name: str) -> Optional[dict]:
    """Charge les profils + matche `gpu_name` + fallback sur `_generic.json`.

    Retourne None uniquement si aucun match ET pas de `_generic.json`.
    """
    matched = match_profile(load_profiles(profiles_dir), gpu_name)
    if matched is not None:
        return matched
    generic_path = os.path.join(profiles_dir, GENERIC_FILENAME)
    if os.path.isfile(generic_path):
        try:
            return load_profile_file(generic_path)
        except (json.JSONDecodeError, OSError):
            return None
    return None
