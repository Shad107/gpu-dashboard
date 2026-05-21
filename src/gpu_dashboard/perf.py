"""Estimation du perf % pour inférence LLM selon le power-limit.

L'estimation se fait par **interpolation linéaire** sur la `perf_curve` du profil
GPU. La courbe est une liste de points [watts, perf_pct]. Au-dessus du dernier
point ou en dessous du premier, on clamp (pas d'extrapolation).
"""
from __future__ import annotations

from typing import Mapping


def estimate_perf(profile: Mapping, watts: int) -> int:
    """Estime le perf % à `watts` selon la perf_curve de `profile`.

    Args:
        profile: dict avec profile["power"]["perf_curve"] = [[W, %], ...]
        watts:   power-limit visé, en watts

    Returns:
        Perf % estimé (0-100), entier. Retourne 0 si la courbe est vide.
        Clampé aux bornes de la courbe au-dessus/en dessous des points connus.
    """
    curve = list(profile.get("power", {}).get("perf_curve", []))
    if not curve:
        return 0

    # Trie par watts au cas où le profil JSON ait été écrit dans le désordre
    curve.sort(key=lambda p: p[0])

    # Clamp aux bornes (pas d'extrapolation)
    if watts <= curve[0][0]:
        return int(curve[0][1])
    if watts >= curve[-1][0]:
        return int(curve[-1][1])

    # Interpolation linéaire entre deux points adjacents
    for i in range(len(curve) - 1):
        w0, p0 = curve[i]
        w1, p1 = curve[i + 1]
        if w0 <= watts <= w1:
            if w1 == w0:  # garde-fou : deux points sur le même W
                return int(p0)
            t = (watts - w0) / (w1 - w0)
            return round(p0 + t * (p1 - p0))

    return 0  # unreachable normalement
