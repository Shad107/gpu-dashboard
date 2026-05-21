# Profile Schema — Documentation pour contributeurs

Un profil = un fichier JSON dans `profiles/`, par modèle de carte NVIDIA. Au démarrage,
gpu-dashboard lit `nvidia-smi --query-gpu=name` et cherche le premier profil dont l'un
des patterns `match` apparaît (case-insensitive) dans le nom détecté.

## ✅ Validation

Tous les profils sont **validés à chaque chargement** contre [`profiles/schema.json`](./schema.json)
(JSON Schema draft 2020-12). Un profil invalide est ignoré avec un warning, le projet
ne crashe pas — mais ta carte n'aura pas son profil pris en compte.

**Pour valider localement avant PR** :

```bash
python3 -c "
import json, jsonschema
schema = json.load(open('profiles/schema.json'))
profile = json.load(open('profiles/ton-profil.json'))
jsonschema.validate(profile, schema)
print('OK')
"
```

Tu peux aussi ajouter `$schema` dans ton profil pour activer l'auto-complétion + validation
dans VS Code :

```jsonc
{
  "$schema": "./schema.json",
  "model": "RTX 4070",
  ...
}
```

Le projet effectue aussi des **checks cross-field** non exprimables en JSON Schema :
- `power.min < power.max`
- `power.stock <= power.max`
- `power.sweet_spot` dans `[min, max]`

## Schéma

```jsonc
{
  "model": "RTX 3090",                // Nom marketing, libre
  "architecture": "Ampere (GA102)",   // Codename + die
  "release_year": 2020,
  "match": [                          // Patterns matchés contre nvidia-smi name (case-insensitive)
    "GeForce RTX 3090",
    "RTX 3090"
  ],
  "vram_gib": 24,
  "tdp_w": 350,

  "power": {
    "min": 100,                       // Watts mini (en dessous → throttle)
    "max": 350,                       // Watts maxi (= TDP stock standard)
    "stock": 350,                     // Power-limit par défaut hardware
    "sweet_spot": 250,                // Notre reco par défaut

    // Courbe perf inférence LLM, interpolation linéaire entre points
    // Format : [[watts, perf_pct], ...]
    "perf_curve": [
      [100, 31], [150, 56], [200, 76], [250, 89], [300, 95], [350, 100]
    ]
  },

  "clocks": {
    "gpu_offset_max": 200,            // Offset GPU clock max (Coolbits) — borne UI
    "mem_offset_max": 1500,           // Offset memory transfer rate max — borne UI
    "gpu_zones": {                    // Zones de risque pour le slider GPU
      "safe": 50,                     // 0 ≤ x ≤ safe : sans risque
      "moderate": 100,                // safe < x ≤ moderate : sweet-spot
      "aggressive": 150,              // moderate < x ≤ aggressive : test stabilité
      "danger": 200                   // aggressive < x ≤ danger : peut figer
    },
    "mem_zones": {
      "safe": 300,
      "moderate": 700,
      "aggressive": 1200,
      "danger": 1500
    },
    "sweet_spot": {                   // Recommandations par défaut UI
      "gpu": 100,
      "mem": 500
    }
  },

  "fans": {
    "controllers_expected": 2,        // Nombre de contrôleurs PWM via nvidia-settings
    "physical_count": 3,              // Nombre réel de ventilos physiques (info)
    "rpm_max": 3000,                  // Pour normaliser les courbes RPM
    "zero_rpm_cutoff_c": 50,          // Temp en dessous → fans off (zero-RPM idle)
    "default_curve": [                // Courbe ventilo par défaut [tempC, fan_pct]
      [40, 30], [50, 40], [60, 55], [70, 70], [80, 85], [90, 100]
    ]
  },

  "notes": "Pure-attention MoE-friendly. Prefix-cache OK sur llama.cpp."
}
```

## Patterns de match

Le matching est **case-insensitive** et utilise des **substring** :
- `"RTX 3090"` matche `"NVIDIA GeForce RTX 3090"` ✓
- `"RTX 3090"` matche `"NVIDIA GeForce RTX 3090 Ti"` ✓ ⚠ — d'où l'ordre des profils chargés

**Ordre de priorité** : les profils sont tentés du **plus spécifique au plus générique**.
Le loader trie automatiquement par longueur de pattern décroissante. Si tu rajoutes une
variante, mets le pattern le plus précis (`"RTX 3090 Ti"`) avant le moins précis (`"RTX 3090"`).

## Contribuer un profil

1. Forke le repo
2. Copie `_generic.json` comme template, renomme en `<model-slug>.json` (kebab-case)
3. Remplis les champs avec les specs de ta carte
4. Si tu as benchmarké : remplace la `perf_curve` par tes mesures réelles
5. Ouvre une PR avec :
   - Une preuve de `nvidia-smi -q` montrant le modèle
   - (Idéal) tes mesures perf à 3-4 power-limits pour calibrer la courbe

## Valeurs incertaines / contributions bienvenues

Si tu ne peux pas mesurer la `perf_curve`, garde la valeur du `_generic.json` adaptée à
ton TDP et marque dans `notes`: `"perf_curve estimée — mesures bienvenues via PR"`.
