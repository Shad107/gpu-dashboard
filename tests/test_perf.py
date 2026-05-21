"""Tests pour gpu_dashboard.perf — estimation perf % selon le power-limit.

L'estimation prend un profil GPU (qui contient une perf_curve : liste de points
[watts, perf_pct]) et un power-limit en watts, et retourne le perf % estimé par
interpolation linéaire entre les points connus.
"""
import pytest

from gpu_dashboard.perf import estimate_perf


# Profil RTX 3090 — courbe perf typique pour inférence LLM, calibrée sur l'expérience
RTX_3090 = {
    "power": {
        "min": 100,
        "max": 350,
        "stock": 350,
        "perf_curve": [
            [100, 31],
            [150, 56],
            [200, 76],
            [220, 83],
            [250, 89],
            [280, 92],
            [300, 95],
            [340, 100],
            [350, 100],
        ],
    }
}


class TestEstimatePerf:
    def test_at_stock(self):
        """À la puissance stock, perf = 100 %."""
        assert estimate_perf(RTX_3090, 350) == 100

    def test_at_min(self):
        """Au minimum de la courbe, perf = la valeur du point min."""
        assert estimate_perf(RTX_3090, 100) == 31

    def test_at_sweet_spot(self):
        """À 250 W (notre choix), perf = 89 % (valeur connue)."""
        assert estimate_perf(RTX_3090, 250) == 89

    def test_interpolation_between_points(self):
        """Entre 250 W (89 %) et 280 W (92 %), 265 W ≈ 90-91 %."""
        result = estimate_perf(RTX_3090, 265)
        assert 89 <= result <= 91, f"interpolation hors plage : {result}"

    def test_interpolation_midway(self):
        """Pile au milieu de deux points : moyenne."""
        # entre 100 (31) et 150 (56), 125 ≈ (31+56)/2 = 43.5 → 43 ou 44
        result = estimate_perf(RTX_3090, 125)
        assert 42 <= result <= 45, f"interpolation midway : {result}"

    def test_clamp_above_max(self):
        """Au-dessus du dernier point → renvoie la valeur du dernier point."""
        assert estimate_perf(RTX_3090, 999) == 100

    def test_clamp_below_min(self):
        """En dessous du premier point → renvoie la valeur du premier point."""
        assert estimate_perf(RTX_3090, 50) == 31

    def test_monotonic_increasing(self):
        """La perf doit toujours croître (ou rester égale) quand W augmente."""
        prev = -1
        for w in range(100, 351, 10):
            p = estimate_perf(RTX_3090, w)
            assert p >= prev, f"non monotone à {w}W : {p} < précédent {prev}"
            prev = p

    def test_empty_curve_returns_zero(self):
        """Profil sans courbe → 0 (cas dégradé, ne crash pas)."""
        empty = {"power": {"perf_curve": []}}
        assert estimate_perf(empty, 250) == 0

    def test_unsorted_curve_handled(self):
        """Une courbe dans le désordre doit quand même fonctionner."""
        unsorted_profile = {
            "power": {
                "perf_curve": [[300, 95], [100, 31], [350, 100], [200, 76]],
            }
        }
        assert estimate_perf(unsorted_profile, 350) == 100
        assert estimate_perf(unsorted_profile, 100) == 31
        # entre 200 (76) et 300 (95), 250 ≈ 85-86
        result = estimate_perf(unsorted_profile, 250)
        assert 84 <= result <= 87, f"après tri : {result}"

    def test_returns_int(self):
        """L'estimation doit retourner un int (pas float)."""
        result = estimate_perf(RTX_3090, 237)
        assert isinstance(result, int)

    def test_single_point_curve(self):
        """Une courbe à un seul point retourne toujours cette valeur."""
        single = {"power": {"perf_curve": [[250, 89]]}}
        assert estimate_perf(single, 100) == 89
        assert estimate_perf(single, 250) == 89
        assert estimate_perf(single, 500) == 89
