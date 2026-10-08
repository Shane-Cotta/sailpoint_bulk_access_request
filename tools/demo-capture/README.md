# Demo capture

Scripted captures of the UI plugin's **demo mode** (`?demo=<scenario>`, made-up data, no tenant):

- the plugin screenshots in `docs/screenshots/plugin-*.png` (1440×900 viewport, 2× scale, light theme), and
- a captioned demo video, `bulk-access-demo.mp4` (H.264, 1440×900, 30 fps, about 5 minutes), plus one clip per scene.

Nothing here calls a tenant or any external URL. The script only talks to the local demo server.

## Prerequisites

- Node 20+.
- Playwright and its Chromium, installed here (in `tools/demo-capture/`):

  ```bash
  npm install
  npx playwright install chromium
  ```

- `ffmpeg` with libx264 on the PATH (or pass `--ffmpeg <path>`), for the video only. Screenshots don't need it.
- The plugin's demo server. In `plugin/` (after `npx -y npm@11 install`):

  ```bash
  npm run start:demo        # serves http://localhost:4300
  ```

## Run

In `tools/demo-capture/`, with the demo server running:

```bash
npm run capture                                   # screenshots and video, into tools/demo-capture/out/
npm run capture:docs                              # screenshots only, straight into docs/screenshots/
npm run capture:video                             # video only
npm run capture -- --screenshots-only --only plugin-10,plugin-11   # just some screenshots (matched by name)
```

Options: `--out <dir>` (default `tools/demo-capture/out`), `--screenshots-dir <dir>` (default `<out>/screenshots`),
`--screenshots-only`, `--video-only`, `--only <name,…>`, `--base <url>` (default `http://localhost:4300`),
`--ffmpeg <path>`. Relative paths are relative to the repository root.

Output:

| Path | What |
|---|---|
| `<out>/screenshots/plugin-*.png` | Doc images (or wherever `--screenshots-dir` points; `capture:docs` writes `docs/screenshots/`) |
| `<out>/scenes/NN-<scene>.mp4` | One clip per scene, with a short fade in and out |
| `<out>/bulk-access-demo.mp4` | All scenes in order (faststart, for the web) |

`out/` and `node_modules/` are gitignored. The video isn't committed; it's attached to a GitHub release.

Screenshots take about 2 minutes and the video about 6, mostly because the Approvals scenes run at the
plugin's real pace (about 8 decisions a second).

## How it works

- **Screenshots** (`SHOTS` in `capture.mjs`): each one opens a fresh page on a demo scenario, optionally clicks
  its way to the state the image shows, waits for data and fonts, and takes a full-page screenshot (dialogs and
  progress: viewport only).
- **Video** (`SESSIONS`): each session drives one recorded browser page at a human pace (typing with a delay, a
  visible pointer, pauses after each step) and injects a caption banner at the bottom. `mark(id)` starts a new
  scene; afterwards ffmpeg cuts the recording at the marks, encodes each scene, and concatenates them. The title and
  closing cards are plain HTML rendered to a still and held.
- Selectors are labels, roles and visible text, so small layout changes don't break them. If a screenshot fails,
  the script logs it, carries on, and exits non-zero.

| Scene | Scenario | Shows |
|---|---|---|
| 00 title | – | What the solution is, and its parts |
| 1–6 | `new` | People (search + paste 597 lines), items, temporary access, approver + INC validation, review in 3 parts, submit, My bulk requests |
| 7 | `approvals` | Approvals tab as a non-admin item approver: groups by INC, drill-down, leave one out, approve with a comment, progress, result |
| 8 | `approvals-partial` | Throttling, failures, decided by someone else, still pending, "Partly done", Retry |
| 09 closing | – | The ISC features and APIs it uses |

The video's text (captions and cards) lives in `capture.mjs`. The pasted people list mirrors the demo fixtures in
`plugin/src/app/demo/fixtures.ts`; if those change, update `FIRST`/`LAST` here.
