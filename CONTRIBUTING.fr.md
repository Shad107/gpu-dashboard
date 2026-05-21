# Contributing to gpu-dashboard

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
4. If possible, include a measured `perf_curve`. Otherwise, mark the profile with
   `"notes": "perf_curve estimated — measurements welcome"`.
5. Open a PR with:
   - A `nvidia-smi -q | head -20` output for your card (so we know the exact name string)
   - Brief description of your test setup (driver version, distro)

That's it. Profiles don't require Python code changes.

## 🧪 Running the test suite

```bash
pip install -e .[dev]
pytest
```

The test suite is fast (~0.5s) and uses no external services — `subprocess` calls are
mocked with `monkeypatch`. Aim for **161+ tests passing** before any PR is merged.

## 🐍 Code contributions

- Stick to **stdlib Python** unless you have a strong reason. We allow `jsonschema` because
  schema validation matters; we resist adding `requests`, `pydantic`, etc.
- Write **tests first** (TDD). All Python modules have a corresponding `test_*.py`.
- Module conventions:
  - Each optional feature lives in `src/gpu_dashboard/modules/<name>.py`
  - Modules expose `NAME`, `can_enable(...)`, plus their specific verbs
  - Tests in `tests/test_modules_<name>.py`
- Subprocess calls go through `subprocess.run(cmd, capture_output=True, text=True, timeout=N)`.
  Always pass `timeout=`. Never block indefinitely.

## 🚫 What we don't accept

- New runtime dependencies (other than `jsonschema`) without prior discussion
- Untested code
- Features that require root by default (we prefer narrow sudoers wrappers)
- AMD/Intel support PRs **for now** — see issue tracker for the planned backend
  abstraction; coming in v1.0
- Windows/macOS — Linux-only by design
