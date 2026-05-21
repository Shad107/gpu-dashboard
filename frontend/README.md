# Frontend — Svelte 5 + Vite

The dashboard UI, written in Svelte 5 with the runes API. Built artifacts go
into `../src/gpu_dashboard/static/`, which is what `server.py` serves.

## Development

```bash
cd frontend
pnpm install            # or: npm install
pnpm dev                # dev server on http://localhost:5173 (proxies /api to :9999)
```

You still need the Python backend running for the API:

```bash
# In another terminal, from repo root:
python3 -m gpu_dashboard
```

## Build for production

```bash
cd frontend
pnpm build              # writes to ../src/gpu_dashboard/static/
```

After building, `python3 -m gpu_dashboard` serves the bundled artefacts directly.

## Project layout

```
frontend/
├── index.html
├── package.json
├── vite.config.ts            # outputs to ../src/gpu_dashboard/static
├── svelte.config.js
├── tsconfig.json
└── src/
    ├── main.ts               # mount App
    ├── App.svelte
    ├── app.css               # global styles
    ├── components/
    │   ├── Header.svelte
    │   ├── Cards.svelte
    │   ├── CoolingChart.svelte
    │   ├── PowerChart.svelte
    │   ├── SettingsModal.svelte
    │   └── Toast.svelte
    └── lib/
        ├── api.ts            # typed wrappers for /api/*
        ├── charts.ts         # SVG renderers (Catmull-Rom + helpers)
        ├── stores.svelte.ts  # $state stores (live, toast, modal)
        └── i18n/
            ├── index.svelte.ts  # typed t() + lang reactive store
            ├── en.json
            └── fr.json
```

## Adding a new language

1. Copy `src/lib/i18n/en.json` to `src/lib/i18n/<code>.json`
2. Translate every string (TypeScript will refuse to compile if keys are missing)
3. Import it in `src/lib/i18n/index.svelte.ts` and add to the `DICTS` record
4. Add a radio button entry in `components/SettingsModal.svelte` (Language section)
