# Contributing to gpu-dashboard

🇬🇧 English · [🇫🇷 Français](CONTRIBUTING.fr.md)

Thanks for considering a contribution. The project values **focused PRs, simple stdlib
Python, and tested code**.

## 🎮 Adding a GPU profile (most welcome contribution)

The fastest way to make this project useful to more people is to **add a profile for your
card**. Profiles are JSON files in [`profiles/`](profiles/).

1. Copy `_generic.json` and rename to `<your-card-slug>.json` (e.g. `rtx-4070-ti.json`).
2. Fill in the fields. See [`profiles/SCHEMA.md`](profiles/SCHEMA.md) for the full spec.
3. Validate locally before pushing:
   ```bash
   python3 -c "
   import json, jsonschema
   schema = json.load(open('profiles/schema.json'))
   profile = json.load(open('profiles/<your-card-slug>.json'))
   jsonschema.validate(profile, schema)
   print('OK')
   "
   ```
4. If you can, include a measured `perf_curve`. Otherwise, flag the profile with
   `"notes": "perf_curve estimated — measurements welcome"`.
5. Open a PR with:
   - A `nvidia-smi -q | head -20` excerpt for your card (so we know the exact name string)
   - Brief description of your test setup (driver version, distro)

That's it. Profiles don't require Python code changes.

## 🧪 Running the test suite

```bash
pip install -e .[dev]
pytest
```

The test suite is fast (~4s for 420+ tests) and uses no external services —
`subprocess` calls are mocked with `monkeypatch`, `urllib.request.urlopen` is
mocked for HTTP fetchers, `os.execv` and `sys.exit` are monkeypatched away in
lifecycle tests. **CI runs the suite on Python 3.9 → 3.13**.

Aim for **all tests green** before any PR is merged. Check the badge at the
top of the README, or run locally :

```bash
PYTHONPATH=src pytest tests/ -v
```

## 🐍 Code contributions

- Stick to **stdlib Python** unless you have a strong reason. We allow `jsonschema`
  because schema validation matters; we resist adding `requests`, `pydantic`, etc.
- Write **tests first** (TDD). Every Python module has a corresponding `test_*.py`.
- Module conventions :
  - Each optional feature lives in `src/gpu_dashboard/modules/<name>.py`
  - Modules expose `NAME`, `can_enable(...)`, plus their feature-specific verbs
  - Daemons (long-running threads) follow the `start() / stop() / _loop()` pattern,
    see `retention.py`, `fan_curve.py`, `auto_profile.py`, `alert_monitor.py`
  - Tests in `tests/test_modules_<name>.py`
- Subprocess calls go through `subprocess.run(cmd, capture_output=True, text=True, timeout=N)`.
  Always pass `timeout=`. Never block indefinitely.
- HTTP API handlers in `api.py` follow `handle_<verb>(ctx, [params]) → (code, body)`.
  Wire the new route in `server.py do_GET()` / `do_POST()`.

### Adding a new opt-in module

1. Create `src/gpu_dashboard/modules/<name>.py` with `NAME`, `can_enable()`, your verbs.
2. Tests in `tests/test_modules_<name>.py` (target 6+ tests covering happy path + edge cases).
3. Add a `MODULE_<NAME>=0` default to `DEFAULTS` in `server.py`.
4. If it's a daemon, wire start/stop in `_load_context()`.
5. If it has an API surface, add `handle_<name>_*` in `api.py` + routes in `server.py`.
6. Optional : a section in the modal sidebar — extend `sections` in `SettingsModal.svelte`.

### Adding a new API endpoint

1. Add `handle_<name>(ctx, [params]) → Response` in `api.py`.
2. Wire the route in `server.py` (inside `do_GET` or `do_POST`).
3. Add a typed wrapper in `frontend/src/lib/api.ts` if the UI needs it.
4. Write at least 4 tests covering : happy path, missing data, invalid input,
   no-storage case if storage-backed.

### Database schema changes

1. Bump `CURRENT_SCHEMA_VERSION` in `storage.py`.
2. Add the column in `_SCHEMA_SQL` (idempotent `CREATE TABLE IF NOT EXISTS`).
3. Add an idempotent migration function `_migrate_vN_to_vN+1(conn)` using
   `PRAGMA table_info` + `ALTER TABLE ADD COLUMN`.
4. Call it from `Storage.__init__` after `executescript(_SCHEMA_SQL)`.
5. Test that an old-schema DB created manually gets migrated on open
   (see `test_storage_migration.py` for the pattern).

## 🚫 What we don't accept

- New runtime dependencies (other than `jsonschema`) without prior discussion
- Untested code
- Features that require root by default (we prefer narrow sudoers wrappers)
- AMD/Intel support PRs **for now** — see issue tracker for the planned backend
  abstraction; coming in v1.0
- Windows/macOS — Linux-only by design

## 🌐 Languages

User-facing strings (UI, install prompts, errors) are **English by default**, with
French as a maintained second language. Other languages welcome via PR — open an issue
first so we can discuss the i18n approach (currently a simple dual-doc setup, may move
to gettext if more languages are added).

## 🎨 Frontend (Svelte 5 + Vite, v0.2+)

The UI lives in `frontend/` and is built with Svelte 5 + TypeScript + Vite.
Build artifacts are committed to `src/gpu_dashboard/static/` so end-users don't
need Node to run the dashboard.

```bash
cd frontend
pnpm install            # or: npm install
pnpm build              # rebuild after any change
pnpm dev                # dev server :5173 (proxies /api to :9999)
```

When you submit a PR that touches `frontend/src/`, **always commit the rebuilt
`src/gpu_dashboard/static/` along with your source changes** so the project
stays installable without Node.

See `frontend/README.md` for the full layout + how to add a language.
