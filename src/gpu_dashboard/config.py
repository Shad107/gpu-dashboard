"""Loader/saver de fichiers .env + classe Config layérée.

Choix de format : .env (KEY=VALUE) plutôt que TOML/YAML pour rester :
- Lisible/éditable par les non-devs
- Sourceable nativement par systemd (`EnvironmentFile=`)
- Sans parser tiers (stdlib uniquement)

La classe `Config` empile :
  defaults  →  fichiers (ordre)  →  variables d'environnement  →  runtime (set)
Avec priorité croissante (le runtime gagne sur tout).
"""
from __future__ import annotations

import os
import shlex
from typing import Iterable, Mapping, Optional


# ────────────────────────────────────────────────────────────────────────────
# Parsing / écriture de fichiers .env
# ────────────────────────────────────────────────────────────────────────────


def _unquote(value: str) -> str:
    """Enlève les guillemets autour d'une valeur et déséchape les apostrophes.

    Gère les 2 conventions shell pour insérer une apostrophe dans une chaîne
    single-quoted :
      - `'\\''`  (la plus commune, lisible)
      - `'"'"'`  (utilisée par shlex.quote en Python)
    """
    v = value.strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
        inner = v[1:-1]
        if v[0] == "'":
            inner = inner.replace("'\\''", "'").replace("'\"'\"'", "'")
        return inner
    return v


def parse_env_file(path: str) -> dict:
    """Parse un fichier .env → dict[str, str]. Renvoie {} si fichier absent.

    - Lignes commençant par # ou vides : ignorées
    - Seul le premier `=` sépare clé et valeur
    - Lignes sans `=` ignorées silencieusement
    - Valeurs entre quotes (single ou double) : guillemets enlevés
    - Convention shell : `'\\''` à l'intérieur de single-quotes = apostrophe littérale
    """
    result: dict = {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            for raw in f:
                line = raw.rstrip("\n").rstrip("\r")
                stripped = line.lstrip()
                if not stripped or stripped.startswith("#"):
                    continue
                if "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key = key.strip()
                if not key:
                    continue
                result[key] = _unquote(value)
    except FileNotFoundError:
        pass
    return result


def write_env_file(path: str, data: Mapping[str, str], header: Optional[str] = None) -> None:
    """Écrit un dict en .env, atomiquement, avec quoting sûr via shlex.quote.

    Les valeurs sont auto-quotées (single quotes) seulement si nécessaire — par
    exemple les valeurs numériques simples restent sans quotes.
    """
    lines = []
    if header:
        lines.append(header.rstrip())
    for k, v in data.items():
        sv = str(v)
        # shlex.quote produit 'value' avec escape '\'' si nécessaire
        # Pour les valeurs simples sans caractères spéciaux, il renvoie tel quel
        quoted = shlex.quote(sv) if sv else "''"
        lines.append(f"{k}={quoted}")
    # Atomic write : .tmp puis rename
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
        if lines:
            f.write("\n")
    os.replace(tmp, path)


# ────────────────────────────────────────────────────────────────────────────
# Config — empilage defaults → fichiers → env vars → runtime (set)
# ────────────────────────────────────────────────────────────────────────────


_TRUTHY = {"1", "true", "yes", "on"}
_FALSY = {"0", "false", "no", "off", ""}


class Config:
    """Configuration layérée. Chaque couche écrase la précédente :

    1. `defaults` (dict initial)
    2. Fichiers .env (dans l'ordre, le dernier gagne)
    3. Variables d'environnement (uniquement pour les clés déjà connues)
    4. `set()` runtime (gagne sur tout)
    """

    def __init__(
        self,
        defaults: Optional[Mapping[str, str]] = None,
        files: Optional[Iterable[str]] = None,
    ):
        self._data: dict = dict(defaults or {})
        for path in files or []:
            self._data.update(parse_env_file(path))
        # Env vars : on n'override que les clés déjà connues (pas de fuite arbitraire)
        for k in list(self._data.keys()):
            if k in os.environ:
                self._data[k] = os.environ[k]

    # ── lecture ─────────────────────────────────────────────────────────────

    def get(self, key: str, default=None):
        return self._data.get(key, default)

    def get_bool(self, key: str, default: bool = False) -> bool:
        v = self._data.get(key)
        if v is None:
            return default
        lv = str(v).strip().lower()
        if lv in _TRUTHY:
            return True
        if lv in _FALSY:
            return False
        return default

    def get_int(self, key: str, default: int = 0) -> int:
        v = self._data.get(key)
        if v is None:
            return default
        try:
            return int(str(v).strip())
        except (ValueError, TypeError):
            return default

    # ── écriture ────────────────────────────────────────────────────────────

    def set(self, key: str, value) -> None:
        self._data[key] = str(value)

    def save(self, path: str, header: Optional[str] = None) -> None:
        write_env_file(path, self._data, header=header)

    # ── introspection ───────────────────────────────────────────────────────

    def as_dict(self) -> dict:
        return dict(self._data)

    def __contains__(self, key: str) -> bool:
        return key in self._data
