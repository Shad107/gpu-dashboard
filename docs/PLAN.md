# GPU Dashboard OSS — Plan v0.1

Plan d'attaque pour passer du dashboard mono-utilisateur actuel à un projet open-source community-ready.
Discipline : **TDD** pour tout module Python pur, tests shell pour `install.sh`.

---

## 🏗️ Architecture cible

```
gpu-dashboard-oss/
├── README.md  LICENSE  CONTRIBUTING.md  pyproject.toml
├── install.sh                       # detect → recommend → setup
│
├── src/gpu_dashboard/
│   ├── __main__.py                  # entrypoint CLI
│   ├── config.py                    # load/save .env + defaults
│   ├── detect.py                    # env probing (OS, X, nvidia, OcuLink…)
│   ├── profile.py                   # match GPU name → profile JSON
│   ├── perf.py                      # ✅ DONE — interpolation perf curve
│   ├── metrics.py                   # sampler thread, deque
│   ├── server.py                    # HTTP server (stdlib only)
│   ├── api.py                       # route handlers
│   ├── modules/                     # opt-in, chacun expose can_enable() + register()
│   │   ├── power_limit.py
│   │   ├── clock_offsets.py
│   │   ├── fan_curve.py
│   │   ├── oculink_watchdog.py
│   │   └── telegram_alerts.py
│   └── static/                      # html, css, js (embarqué)
│
├── profiles/
│   ├── SCHEMA.md
│   ├── _generic.json                # fallback prudent
│   ├── rtx-3090.json
│   └── (RTX 4090, 3080, 4070… via PR communautaires)
│
├── systemd/                         # templates avec @USER@, @HOME@
│   ├── gpu-dashboard.service
│   ├── gpu-watchdog.service
│   └── gpu-fan-curve.service
│
├── helpers/                         # wrappers root + sudoers
│   ├── set-power-limit.sh
│   └── sudoers.d/gpu-dashboard
│
└── tests/                           # pytest + bats pour shell
    ├── conftest.py
    ├── test_perf.py                 # ✅ DONE — 12 tests GREEN
    ├── test_config.py
    ├── test_detect.py
    ├── test_profile.py
    ├── test_modules_*.py
    ├── test_install_sh.bats
    └── fixtures/
        ├── nvidia-smi-outputs/
        └── profiles-sample/
```

---

## ⚙️ Format de config — `.env` (pas TOML)

**Choix** : fichier `.env` plat avec namespacing par underscore.

**Raisons** :
- Format universel lu par n'importe quel non-dev
- Sourceable nativement par systemd (`EnvironmentFile=`)
- Cohérent avec l'existant (`/home/olivier/.gpu-alerts.env`)
- Pas de parser custom à maintenir

**Deux fichiers** :
- `~/.config/gpu-dashboard/config.env` — config visible (port, modules activés, defaults)
- `~/.config/gpu-dashboard/secrets.env` — token Telegram, chmod 600, dans `.gitignore`

**Exemple** :

```bash
# ~/.config/gpu-dashboard/config.env
DASHBOARD_PORT=9999
DASHBOARD_BIND=0.0.0.0
DASHBOARD_REFRESH_INTERVAL=5
DASHBOARD_SAMPLE_KEEP=720

GPU_INDEX=0
GPU_PROFILE_OVERRIDE=           # vide = auto-detect

MODULE_POWER_LIMIT=1
MODULE_CLOCK_OFFSETS=1
MODULE_FAN_CURVE=0
MODULE_OCULINK_WATCHDOG=0
MODULE_TELEGRAM_ALERTS=0

POWER_LIMIT_DEFAULT=250
CLOCK_OFFSETS_DISPLAY=:0
CLOCK_OFFSETS_XAUTHORITY=/home/USER/.Xauthority-nvidia
FAN_CURVE_LOG=/home/USER/gpu-fan-curve.log
OCULINK_POLL_INTERVAL=60
ALERT_DROP=1
ALERT_RECOVER=1
```

```bash
# ~/.config/gpu-dashboard/secrets.env (chmod 600)
TG_TOKEN=
TG_CHAT=
```

---

## 🔢 Séquençage TDD

Chaque étape débloque la suivante. RED → GREEN → commit (mental, pas de git init avant la fin de v0.1).

| # | Module | Pourquoi cet ordre | Dépendances |
|---|---|---|---|
| ✅ | `perf.py` | Fonction pure, plus simple démonstrateur TDD | aucune |
| **1** | `config.py` | Tout le reste lit la config | aucune |
| 2 | `profile.py` | Tout module lit son profil | aucune |
| 3 | `detect.py` | Install.sh + modules.can_enable() en dépendent | subprocess |
| 4 | `profiles/rtx-3090.json` | Pour tester profile.py + modules avec données réelles | profile.py |
| 5 | `modules/power_limit.py` | Le plus simple des modules (slider + endpoint) | config + profile |
| 6 | `modules/clock_offsets.py` | Similaire + zones de risque | config + profile |
| 7 | `modules/telegram_alerts.py` | Pattern de send pour les autres modules | config |
| 8 | `modules/oculink_watchdog.py` | Dépend de telegram_alerts | config + telegram |
| 9 | `modules/fan_curve.py` | Plus complexe (Xorg headless) | config + profile |
| 10 | `metrics.py + api.py + server.py` | Le cœur — refactor de l'existant | tout le reste |
| 11 | `install.sh` | Orchestrer tout | tout |
| 12 | `static/` (html/js) | Refactor du frontend existant | api.py |
| 13 | docs (README, CONTRIBUTING, PROFILES) | Pour publier | tout |

---

## 🧪 Stratégie de test

**Tests Python** (`pytest`)

- Pures fonctions (perf, profile.match, config.parse) → assertions simples
- I/O fichiers → `tmp_path` fixture
- subprocess (nvidia-smi, lspci) → mock via `monkeypatch` + fixtures de sortie figées
- API endpoints → `http.client` localhost contre un serveur de test
- Modules opt-in → `pytest.skip` automatique si dépendance manque (ex: pas de X server)

**Tests shell** (`bats-core` si dispo, sinon scripts shell simples)

- install.sh phase detect : injecter des wrappers fake `nvidia-smi`, `nvidia-settings`, `lspci` dans PATH
- Vérifier que les bons modules sont proposés selon le mock d'env

**CI** (post-v0.1)

- GitHub Actions, matrice Ubuntu 22.04/24.04 + Fedora + Arch
- Tests Python toujours
- Tests shell sur Ubuntu only (autres distros via container)

---

## 📦 Périmètre v0.1 (publishable)

**Inclus (minimum viable)** :

- ✅ Code modulaire propre (`src/` + `modules/`)
- ✅ Profil RTX 3090 + `_generic.json` fallback
- ✅ `install.sh` fonctionnel (detect + recommend + setup)
- ✅ Au moins 3 modules : `power_limit`, `clock_offsets`, `telegram_alerts`
- ✅ Dashboard frontend identique à l'actuel (modal + graphiques)
- ✅ README EN avec captures + GIF démo, INSTALL.md, CONTRIBUTING.md, PROFILES.md
- ✅ LICENSE MIT
- ✅ Tests : ≥ 80 % de coverage sur les modules Python purs

**Reporté à v0.2** :

- `fan_curve.py` (Xorg headless = trop fragile pour v0.1, le doc dit "advanced")
- `oculink_watchdog.py` (mais le doc le mentionne avec "experimental")
- CI multi-distro
- Multi-GPU (un seul GPU géré en v0.1)
- AMD / Intel Arc

---

## ⏱️ Estimation effort

| Phase | Effort | Cumulé |
|---|---|---|
| ✅ perf.py | 30 min | 30 min |
| config.py + profile.py + detect.py | 2h | 2h30 |
| profiles/rtx-3090.json + schema | 30 min | 3h |
| modules/{power, clocks, telegram} | 2h | 5h |
| metrics + api + server (refactor) | 2h | 7h |
| install.sh + tests bats | 1h30 | 8h30 |
| frontend refactor (static/) | 1h30 | 10h |
| README + docs + screenshots | 1h | 11h |
| **v0.1 publishable** | | **~11h** |
| modules/{fan_curve, oculink} pour v0.2 | 2h | 13h |
| CI GitHub Actions | 1h | 14h |

Soit **~1,5 jour focus** pour v0.1, ~2 jours total pour v0.2.

---

## 🚀 Release roadmap

**v0.1.0 — « Foundation »**
- Monitoring + power-limit + clock-offsets + telegram-alerts
- Profil RTX 3090 + générique
- Doc complète
- Tag GitHub, pas de release builds (install via `git clone + install.sh`)

**v0.2.0 — « Advanced »**
- `fan_curve` module (headless Xorg)
- `oculink_watchdog` module
- ≥ 3 profils supplémentaires (4090, 4080, 3080)

**v0.3.0 — « Community »**
- CI multi-distro
- Multi-GPU
- Profile schema validation auto sur PR (GitHub Action)

**v1.0.0 — « Stable »**
- AMD via `radeontop` / `rocm-smi` (backend abstrait)
- Releases binaires (tarball avec systemd templates pré-générés)

---

## 🎯 État actuel

| Tâche | Statut |
|---|---|
| `perf.py` + 12 tests GREEN | ✅ |
| `config.py` (loader/saver .env + tests) | 🔜 prochaine étape |
| `profile.py` (match GPU + fallback) | pending |
| `detect.py` (env probing) | pending |
| `profiles/rtx-3090.json` | pending |
| modules opt-in | pending |
| `install.sh` | pending |
| docs + README + LICENSE | pending |
| Refactor frontend en `static/` | pending |
