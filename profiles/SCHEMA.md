# Profile Schema — Contributor documentation

🇬🇧 English · [🇫🇷 Français](SCHEMA.fr.md)

A profile is one JSON file per NVIDIA GPU model, stored in `profiles/`. At startup,
gpu-dashboard reads `nvidia-smi --query-gpu=name` and looks for the first profile
whose `match` patterns appear (case-insensitive substring) in the detected name.

## ✅ Validation

All profiles are **validated at load time** against
[`profiles/schema.json`](./schema.json) (JSON Schema draft 2020-12). An invalid profile
is skipped with a warning — the project doesn't crash, but your card won't get its
profile applied.

**To validate locally before opening a PR**:

```bash
python3 -c "
import json, jsonschema
schema = json.load(open('profiles/schema.json'))
profile = json.load(open('profiles/your-profile.json'))
jsonschema.validate(profile, schema)
print('OK')
"
```

You can also reference the schema in your profile to enable auto-completion + validation
in VS Code:

```jsonc
{
  "$schema": "./schema.json",
  "model": "RTX 4070",
  ...
}
```

The project also performs a few **cross-field checks** not expressible in JSON Schema:
- `power.min < power.max`
- `power.stock <= power.max`
- `power.sweet_spot` within `[min, max]`

## Schema

```jsonc
{
  "model": "RTX 3090",                // Marketing name, free-form
  "architecture": "Ampere (GA102)",   // Codename + die
  "release_year": 2020,
  "match": [                          // Patterns matched against nvidia-smi name (case-insensitive)
    "GeForce RTX 3090",
    "RTX 3090"
  ],
  "vram_gib": 24,
  "tdp_w": 350,

  "power": {
    "min": 100,                       // Min watts (below → throttle)
    "max": 350,                       // Max watts (= stock TDP for stock parts)
    "stock": 350,                     // Default hardware power-limit
    "sweet_spot": 250,                // Our recommended default

    // LLM-inference perf curve, linear interpolation between points
    // Format: [[watts, perf_pct], ...]
    "perf_curve": [
      [100, 31], [150, 56], [200, 76], [250, 89], [300, 95], [350, 100]
    ]
  },

  "clocks": {
    "gpu_offset_max": 200,            // Max GPU clock offset (Coolbits) — UI cap
    "mem_offset_max": 1500,           // Max memory transfer-rate offset — UI cap
    "gpu_zones": {                    // Risk zones for the GPU slider
      "safe": 50,                     // 0 ≤ x ≤ safe   : risk-free
      "moderate": 100,                // safe < x ≤ mod : typical sweet-spot
      "aggressive": 150,              // mod < x ≤ agg  : stability test required
      "danger": 200                   // agg < x ≤ dgr  : may freeze the GPU
    },
    "mem_zones": {
      "safe": 300,
      "moderate": 700,
      "aggressive": 1200,
      "danger": 1500
    },
    "sweet_spot": {                   // UI default recommendations
      "gpu": 100,
      "mem": 500
    }
  },

  "fans": {
    "controllers_expected": 2,        // Number of PWM controllers via nvidia-settings
    "physical_count": 3,              // Actual physical fan count (info)
    "rpm_max": 3000,                  // To normalize RPM curves
    "zero_rpm_cutoff_c": 50,          // Temp below → fans off (zero-RPM idle)
    "default_curve": [                // Default fan curve [tempC, fan_pct]
      [40, 30], [50, 40], [60, 55], [70, 70], [80, 85], [90, 100]
    ]
  },

  "notes": "Pure-attention MoE-friendly. Prefix-cache OK on llama.cpp."
}
```

## Match patterns

Matching is **case-insensitive** and uses **substring** matching:
- `"RTX 3090"` matches `"NVIDIA GeForce RTX 3090"` ✓
- `"RTX 3090"` matches `"NVIDIA GeForce RTX 3090 Ti"` ✓ ⚠ — hence the loading order

**Priority**: profiles are tried from **most specific to most generic**. The loader
sorts automatically by descending pattern length. If you add a variant, put the most
specific pattern (`"RTX 3090 Ti"`) first; the less specific one (`"RTX 3090"`) goes after.

## Contributing a profile

1. Fork the repo
2. Copy `_generic.json` as a template, rename to `<model-slug>.json` (kebab-case)
3. Fill in the fields with your card's specs
4. If you've benchmarked: replace `perf_curve` with your measurements
5. Open a PR with:
   - Proof of `nvidia-smi -q` showing the model
   - (Ideal) perf measurements at 3-4 different power-limits to calibrate the curve

## Uncertain values / contributions welcome

If you can't measure the `perf_curve`, copy the closest one from `_generic.json` matched
to your TDP and flag it in `notes`: `"perf_curve estimated — measurements welcome via PR"`.
