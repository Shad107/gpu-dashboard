# Promo screenshots — usage guide

Generated automatically. Re-run the snapshot block in PROMOTE.md if you want fresh shots after a UI update.

## Files

| # | File | Use case | Where to upload it |
|---|------|----------|--------------------|
| 01 | `01-dashboard-main.png` | Hero shot — main dashboard with 4 groups visible (GPU LIVE / TUNING / LLM / COÛT) | Hacker News, r/selfhosted (post image), README hero, Twitter thread tweet 1 |
| 02 | `02-about-diagnostics.png` | Killer-differentiator shot — About tab with Records, Idle audit, Drift detector visible | dev.to article body, r/LocalLLaMA benchmark post, README "diagnostics" section |
| 03 | `03-fan-curve.png` | Anti-oscillation hysteresis shot — fan curve editor with 2 sliders + curve graph | r/homelab post image, README "fan curve" section, Twitter tweet 2 |

## Upload destinations

### Reddit posts (r/selfhosted, r/homelab, r/nvidia, r/LocalLLaMA)

Reddit accepts direct image uploads when creating a post :
1. New Post → "Image" tab (NOT "Link")
2. Drag the file from `docs/promo/`
3. Add the title + body text
4. Submit

For multi-image posts (r/selfhosted now allows up to 20 images per post), drag 01 + 03 + 02 in that order.

### Hacker News (Show HN)

HN does NOT accept image uploads. Strategy :
1. Push the screenshots into the public `docs/promo/` folder of the repo (it'll be there once you `git add docs/promo/ && git commit && git push`)
2. Reference them in the README hero
3. The HN post links to the repo ; first-time visitors see the README with the screenshots

### dev.to article

Upload to dev.to's media library when writing the article. Drag-drop from `docs/promo/`. Markdown :

```markdown
![Main dashboard](https://dev.to/uploads/abc123.png)
```

(dev.to gives you the URL after upload.)

### Twitter / X

Attach as media in the tweet composer. Each tweet allows up to 4 images.
- Tweet 1 : `01-dashboard-main.png`
- Tweet 2 : `02-about-diagnostics.png` (or `03-fan-curve.png` depending on the tweet content)

### awesome-* PRs

Don't include screenshots in the PR description. The repo links are sufficient. Reviewers will visit the repo if they want to see UI.

## Regenerate screenshots

Run from the project root :

```bash
# Stop service so we can run on port 9998 isolated
systemctl --user stop gpu-dashboard.service
mkdir -p /tmp/shot/.config/gpu-dashboard
cp ~/.config/gpu-dashboard/config.env /tmp/shot/.config/gpu-dashboard/config.env
sed -i 's/DASHBOARD_PORT=9999/DASHBOARD_PORT=9998/' /tmp/shot/.config/gpu-dashboard/config.env
HOME=/tmp/shot PYTHONPATH=src DASHBOARD_PORT=9998 PROFILES_DIR=profiles \
  python3 -m gpu_dashboard > /tmp/srv.log 2>&1 &
sleep 6

# Main dashboard
google-chrome --headless=new --disable-gpu --no-sandbox --hide-scrollbars \
  --window-size=1920,800 --virtual-time-budget=6000 \
  --screenshot=docs/promo/01-dashboard-main.png \
  http://127.0.0.1:9998/

# About tab
google-chrome --headless=new --disable-gpu --no-sandbox --hide-scrollbars \
  --window-size=1280,1100 --virtual-time-budget=8000 \
  --screenshot=docs/promo/02-about-diagnostics.png \
  "http://127.0.0.1:9998/?modal=about"

# Fan curve editor
google-chrome --headless=new --disable-gpu --no-sandbox --hide-scrollbars \
  --window-size=1280,1100 --virtual-time-budget=8000 \
  --screenshot=docs/promo/03-fan-curve.png \
  "http://127.0.0.1:9998/?modal=fancurve"

# Cleanup
pkill -f 'python3 -m gpu_dashboard'
rm -rf /tmp/shot
systemctl --user start gpu-dashboard.service
```

## Optional : animated GIF for HN / README hero

Quality > quantity. 5-8 seconds, ~10 fps, < 2 MB.

1. Start `obs-studio`, record the dashboard browser tab for 6 seconds while clicking through cards
2. Save as `.mkv`
3. Convert :
   ```bash
   ffmpeg -i input.mkv -vf "fps=10,scale=900:-1:flags=lanczos" -gifflags +transdiff -y docs/promo/00-hero.gif
   ```
4. If too big : drop fps to 8 or scale to 720
5. Commit + reference in README :
   ```markdown
   ![GreenWatts dashboard](docs/promo/00-hero.gif)
   ```
