# Promotion plan for GreenWatts

> Garage project, no marketing budget. Goal : let people who *would* find it useful actually discover it. Friendly tone, honest scope, no inventing terms.

---

## Tone of voice (always)

- Lowercase. No "revolutionary", no "blazing fast", no "powered by AI".
- "I built this for my own rig, sharing in case it helps someone."
- Acknowledge what already exists (nvtop, nvidia-smi, Grafana, MangoHud).
- Position as **complement**, not replacement.

---

## One-liner pitch (use everywhere, verbatim)

> A small Linux dashboard for NVIDIA GPUs, focused on LLM rigs : it tracks tokens-per-watt, fan curves with hysteresis, and attributes power to cgroups. stdlib only, no SaaS.

---

## Phase 1 — Soft launch (no Reddit yet)

### 1.1 Personal blog or dev.to

Article angle (NOT "look at my project") :

**Title** : *Measuring tokens-per-watt on an RTX 3090*

Outline :
- Why this metric matters (power costs add up on 24/7 LLM rigs)
- How to compute it from `nvidia-smi --query-gpu=power.draw` + llama.cpp server logs
- A short Python snippet
- "I bundled this in a small dashboard I run on my box — [link]"

Keep the project mention to ONE line at the bottom. The article must stand alone as useful even without your tool.

### 1.2 Twitter / Mastodon thread

- 1 thread, 3-4 tweets
- Tweet 1 : screenshot of the Électricité card showing tok/Wh + €/mois
- Tweet 2 : what tok/Wh means and why it's not the same as tok/s
- Tweet 3 : link to repo
- Tag : @ollama (if relevant), @ggerganov (llama.cpp), @vllm_project — they often retweet ecosystem tools

### 1.3 awesome-selfhosted PR

URL : `https://github.com/awesome-selfhosted/awesome-selfhosted`

Find the section "Monitoring" or "Systems Management". Add :
```
- [GreenWatts](https://github.com/Shad107/gpu-dashboard) - Linux dashboard for NVIDIA GPUs focused on LLM rigs (tokens-per-watt, fan curve, cgroup power attribution). stdlib only. `Python` `MIT`
```

Check the PR checklist : self-hosted ✓, OSS license ✓, source available ✓, working demo ✓. They're strict but fair.

### 1.4 awesome-llm-related lists

- `awesome-llm` : https://github.com/Hannibal046/Awesome-LLM
- `awesome-localllm` : there are a few forks
- `awesome-selfhosted-ai`

Same approach — find the "tools" or "monitoring" section, open PR with one line.

---

## Phase 2 — Curated communities (after Phase 1 had decent reception)

### 2.1 r/selfhosted

- 600k members, vibe is "homelab nerds"
- Less hostile than r/LocalLLaMA
- Post title : `GreenWatts — a tiny GPU dashboard I built for my LLM box (Linux/NVIDIA, MIT)`
- First line : "Hi, I built this for myself and decided to release it. Not trying to replace anything you already use."

### 2.2 r/homelab

- 1M+ members, same audience
- Same post + same tone

### 2.3 r/nvidia

- Smaller, but exact fit for the user persona
- Focus the post on Linux-specific pain (fan curves, persistence mode, idle power)

---

## Phase 3 — Larger audiences (only if Phase 1+2 went OK)

### 3.1 Hacker News (Show HN)

- Tuesday/Wednesday 8am-9am ET (~14h-15h CEST)
- Title format : `Show HN: GreenWatts – a Linux GPU dashboard for LLM rigs`
- HN is brutal about marketing-speak but rewards honest, technical posts.
- Make sure your README is excellent BEFORE posting (see "README checklist" below)

### 3.2 r/LocalLLaMA (LAST, optional)

User concern : they can pile on.

If you go there, ground rules :
- Lead with a CONCRETE benchmark : "Here's the tok/Wh I'm getting on 70B Q4 vs Q8"
- Show the tool only as the means to measure it
- Acknowledge nvtop / nvitop explicitly
- Don't claim it's better — claim it's different (focuses on the energy axis)
- Be present in the thread for the first 6 hours

If your post smells of "look at my project" → expect downvotes.
If it smells of "I measured X and here are the numbers, btw I used this" → mostly upvotes.

---

## README checklist (before any Phase 3 post)

- [ ] Hero GIF, 5-8 seconds, shows the dashboard live (record with OBS, convert with `ffmpeg -i input.mkv -vf "fps=10,scale=900:-1" out.gif`)
- [ ] One-liner pitch at the top (the one above)
- [ ] "What this does / doesn't do" honest section
- [ ] Install in <30 seconds : copy-paste block
- [ ] 3 screenshots : dashboard, About → Drift detector, Settings → fan curve hysteresis
- [ ] "Comparison" table : GreenWatts vs nvtop vs Grafana — what each is best for
- [ ] License badge, CI badge, Python versions
- [ ] Acknowledgments : llama.cpp, NVML, lm-sensors, NUT

---

## Things NOT to do

- Don't crosspost the same text to 5 subreddits at once → mods notice
- Don't reply to criticism with "you didn't read the README" → reply with what you'll change
- Don't claim things the tool doesn't do
- Don't pay for promotion
- Don't auto-follow people on Twitter
- Don't email tech journalists. They're swamped. Phoronix sometimes covers Linux GPU stuff organically if r/Linux picks it up

---

## Comparison table to put in README

| Tool | Best for | Comparison axis |
|---|---|---|
| **nvtop / nvitop** | Real-time TUI, per-process VRAM | terminal-only, no history |
| **Grafana + DCGM exporter** | Production fleets, long-term metrics | heavy setup, not LLM-aware |
| **MangoHud** | In-game OSD | game-time only, no history |
| **GreenWatts** | LLM-rig dashboards : tok/Wh, fan-curve tuning, alerts | web UI, single-host, MIT, stdlib |

---

## Numbers worth quoting in posts

(verify these are still true at post time)

- Tests passing : 784
- Bundle size : 99 KB gzip frontend
- Python deps : zero (jsonschema is the only one, optional)
- Install time : ~30 seconds on a fresh box
- RAM footprint : tiny, single process
- Update mechanism : 1-click `git pull + restart` from the About tab

---

## Drafts you can copy-paste

### dev.to article opening

> I have an RTX 3090 in a Linux box that runs Ollama 24/7. Last month my electricity bill jumped 18 €. I wanted to know which models were actually pulling their weight per watt, not just per second. Here's how I started measuring tokens-per-watt — and the small dashboard I built along the way.

### r/selfhosted post body

> Hi everyone, I built a small dashboard for my GPU rig over the past few weeks and finally cleaned it up enough to share. It's Linux + NVIDIA only (sorry), Python stdlib + Svelte, MIT licensed. Main features that may interest this sub :
>
> - Tokens-per-watt tracking (if you run a local LLM)
> - Fan curve editor with hysteresis (no more fan oscillation on bursty loads)
> - Power attribution to cgroup / systemd unit
> - Alerts via Telegram / Discord / ntfy / Pushover / Slack / Matrix / SMTP
> - Optional InfluxDB + Prometheus exports if you already have Grafana
>
> Not trying to replace anything you use. Code : github.com/Shad107/gpu-dashboard

### Hacker News title options

1. `Show HN: GreenWatts – a Linux GPU dashboard for LLM rigs`
2. `Show HN: Measuring tokens-per-watt on consumer NVIDIA GPUs`
3. `Show HN: A tiny self-hosted dashboard for LLM rigs`

#1 is safest. #2 is more clickable but expects a benchmark in the post.

---

End of plan. Adjust phasing to your comfort level — if Phase 1 doesn't get traction, refine the README before moving to Phase 2.
