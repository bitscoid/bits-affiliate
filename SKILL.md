---
name: bits-affiliate
version: 3.5.0
author: BITS
license: MIT
description: >
  Affiliate video pipeline in two stages: Gemini turns a product photo (and an
  optional model photo) into a storyboard image + a Google Flow prompt, then
  Google Flow renders a 9:16 video. Use when the user says "pakai
  bits-affiliate", "buatkan video dengan bahan <path>", mentions product.jpg /
  model.jpg, or asks for the affiliate video skill. Outputs:
  <out>/<slug>/STORYBOARD.jpeg, FLOW.md, <slug>.mp4, sumber.json, publish.txt.
metadata:
  hermes:
    tags: [video, affiliates, gemini, google-flow, omni, storyboard, cdp]
    related_skills: [bits-hyperframes, bits-motion, paw-vid-agent-director]
---

# bits-affiliate

Product photo (+ optional model photo) → Gemini storyboard → Google Flow
9:16 video. Everything is driven over Chrome DevTools Protocol (CDP).

```
[0] PROBE   → account + duration (0 credits; 13 s preferred, 10 s fallback)
[1] GEMINI  → STORYBOARD.jpeg + FLOW.md        (0 credits)
[2] FLOW    → <slug>.mp4                       (9:16 Omni 1.2 720p x1)
```

## Install

```bash
pip install -e <skill-dir>
```

Python ≥ 3.10, Google Chrome (`FLOW_CHROME=/path/to/chrome` to override),
`ffmpeg`/`ffprobe` on PATH. Console scripts: `bits-affiliate`, `bits-credits`,
`bits-login`, `bits-publish`, `bits-sources`.

## Usage

```bash
cd <workdir>                                  # results land here
bits-affiliate --product product.jpg [--model model.jpg]
```

Results go to `<cwd>/<slug>/` (`--out DIR` overrides). No static paths;
`BITS_PROJECTS=/some/root` moves the `bits-sources` root.

| Flag | Meaning |
|---|---|
| `--product path` | product photo (**required**) |
| `--model path` | model photo → product+model storyboard |
| `--title "text"` | video title; empty = AI picks it from the storyboard |
| `--slug name` | output folder; default derived from the title |
| `--account email` | starting account (default: first registered) |
| `--seconds 10\|13` | force duration, skipping the auto probe |
| `--mode Auto` | Gemini mode, only when the account's picker exposes it |
| `--timeout 900` | per-stage wait budget in seconds; keep ≥ 900 |
| `--skip-gemini` | reuse existing STORYBOARD.jpeg + FLOW.md |
| `--skip-flow` | stop after Gemini (0 credits) |
| `--dry-run` | Flow stops right before spending credits |
| `--out DIR` | parent folder for results (default: current directory) |
| `--vnc` | visible Chrome on Xvfb `:99` + VNC (watch the run) |

Identify images **by content**, never by argument order. Files are validated
by magic bytes and converted to JPEG when needed. Prompt templates live in
`bits_affiliate/prompts.py`; the duration-dependent block is
`prompts.SPEC[seconds]` — adding a duration = adding one `SPEC` entry.

## Output

`<out>/<slug>/`: `STORYBOARD.jpeg`, `FLOW.md`, `<slug>.mp4`, `sumber.json`,
`publish.txt`.

- `publish.txt` — Buffer-ready caption (header `# ` lines + `=== TikTok`,
  `=== Instagram Reels`, `=== YouTube Shorts` blocks), written automatically
  on every exit. Description comes from the VO script (`vo/naskah.txt`, if
  present), tags from the detected theme, never prices or "contact us" CTA.
  `# STATUS` is honest: `BELUM-ADA-VIDEO` when the mp4 does not exist.
  Rewrite anytime: `bits-publish <slug> [--channel C] [--status S]`.
- `sumber.json` — provenance written on every exit: `slug`, `waktu`, `berkas`,
  `perintah`, `exit`. `bits-sources bersihkan` reads it — never guess from
  folder names.
- No `src/`: inputs are read where they live; conversions happen in a temp dir
  deleted on exit. The folder is renamed once the AI title is known.

Cleanup is automatic: Chrome/Xvfb/x11vnc started by a run are stopped on every
exit path (`flow_cdp.cleanup_all` via `atexit`). It only kills this skill's
own processes.

## Sources (WhatsApp attachments)

```bash
bits-sources status             # what is waiting, what is finished
bits-sources ambil [--dry-run]  # move new cache images into Sources
bits-sources bersihkan [--dry-run]  # empty Sources once a project finished
```

Each file is recorded in `Sources/manifest.json` with its origin (`document`
= untouched, `foto-whatsapp` = compressed). `.heic/.heif` are refused.
`bersihkan` refuses to run while no project has finished.

## Credits — one rule

Gemini is free. Flow charges only when **Mulai pembuatan / Start generation**
is clicked: 30 credits for 10 s, 48 for 13 s. Probe, `--dry-run`,
`--skip-flow` all cost 0.

Duration is **measured, never assumed** (caps vary per account tier): accounts
are walked richest-first, each cap is read from `~/.config/bits-affiliate/
caps.json` or probed (slider `max` + cost chip, before billing). 13 s wins
when an affordable account supports it, else the best 10 s account; the chosen
duration selects `prompts.SPEC[seconds]` so pacing matches the render. Locked
regardless of duration: Ingredients mode, 9:16, 720p, x1, Omni 1.2 Flash.

```bash
bits-credits 48          # which accounts can pay for one video? (exit 1 = none)
bits-login               # account status board
```

## Stage 1 — Gemini (0 credits)

1. Open `https://gemini.google.com/app` in the account's profile.
2. (`--mode`) pick the mode; bilingual labels (`pemilih mode` / `mode picker`),
   missing item = log + continue with the account's setting.
3. Click **Upload & tools** (`Upload & alat`) — file inputs do not exist in
   the DOM before this click.
4. Feed images via CDP `DOM.setFileInputFiles` into
   `input[type=file][accept*="image"]`. Never the native file chooser.
5. Paste the prompt into `[contenteditable="true"]` via `Input.insertText`,
   click **Kirim pesan / Send message**.
6. Wait: text stops growing AND the Stop button is gone. >5 min silence before
   the first token is normal (server queue); images before text = progress.
   Page shrinking >50% = conversation reset = failure.
7. Grab the storyboard: largest **rendered** `img` (`naturalWidth` lies) →
   hover → **Download full-size image** → detect by mtime in
   `~/.config/bits-affiliate/downloads` → `STORYBOARD.jpeg` (file is moved,
   not copied).
8. Extract the fenced block under `PROMPT GOOGLE FLOW` → `FLOW.md`; title
   hint from the LAST `STORYBOARD + FLOW PROMPT —` line.

Failures — `ATTEMPTS = 2`, fresh conversation each attempt, then rotate
account: stalled (Stop present, 36 silent polls), reset (page shrank >50%),
incomplete (`ReplyIncomplete` — error carries the tail of Gemini's reply).
Failure just retries the identical plain process — no plus-menu tools.

## Stage 2 — Flow

1. `https://flow.google.com/` → **New project** (`.mdc-fab`); `/about` or
   `/?pli=` = logged out → rotate.
2. Settings (`.settings-trigger-button`): Video → Ingredients → 9:16 → 720p
   → x1 → Omni 1.2 Flash.
3. Duration slider `input.mdc-slider__input`: raw value = seconds − 3. Read
   `max` first — cap below the target → `FlowSetupError` → rotate (0 credits)
   BEFORE setting the value. Verify the composer chip shows the chosen
   duration (`13 dtk` / `10 dtk`).
4. Upload `STORYBOARD.jpeg` in the ingredients panel, then **click the asset
   thumbnail itself** — upload alone attaches nothing; the click only happens
   once the thumbnail's `<img>` is fully loaded (poll `img.complete &&
   naturalWidth > 0`; re-click at +9 s/+18 s if the chip is slow).
5. Paste `FLOW.md` via `Input.insertText`.
6. Click **Mulai pembuatan / Start generation** — the ONLY credit-spending
   click. `prompt-warning-button` = cannot pay → switch accounts.
7. Poll (4 s) until the clip tile appears: a `/asb/` image taller than wide
   INSIDE a tile container (the prompt-bar chip also uses `/asb/` — must not
   match). Watch for logout throughout.
8. Tile ⋮ **More options** → **Download** → **720p Original size** (skip
   paid 1080p/4K). Stable-size file is **moved** to `<slug>.mp4`.

All page calls go through the reconnecting CDP wrapper (`r.call`): a dropped
debugger socket retries the step, it never kills the run.

Pre-billing failures (settings/upload/attach/network) → rotate account, 0
credits. Render/download failure → hard stop (rotating would pay twice).

## Accounts & login

One account = one Chrome profile (`~/.config/bits-affiliate/profiles/<email>/`,
chmod 700) = one CDP port (`9500 + crc32(email) % 400`). Sessions are bound to
the device — login once, manually, through VNC:

```bash
bits-login                  # status of all accounts
bits-login --add <email>    # create profile + open login page
bits-login --done <email>   # verify session email (exit 1 on mismatch)
bits-login --check [-j N]   # headless probe, -j clamped to 4
```

Never open the same profile twice — two Chromes corrupt the cookie DB.
`account_balance()` (reads the avatar credit panel, ID + EN) powers auto
account-switching; balances are cached per run.

## Testing

All offline, no Chrome:

```bash
python3 tests/test_check_credits.py   # ID/EN parsers + CLI contract
python3 tests/test_flow_duration.py   # adaptive duration: pick/parse/template
python3 tests/test_gemini_stage.py    # storyboard grab, waits, retries
python3 tests/test_login_bench.py     # --add/--done/--check behavior
python3 tests/test_prompt_render.py   # rendered prompts match SPEC byte-for-byte
python3 tests/test_publish.py         # publish.txt shape, tags, honest status
python3 tests/test_ui_labels.py       # every UI label exists in ID AND EN
```

`test_ui_labels` guards UI drift: labels used by code must have both language
variants.

## Pitfalls (all happened in the field)

- Headless is the default; `--vnc` only to watch a run. VNC credentials:
  `~/.config/bits-affiliate/vnc.passwd`.
- Flow's file chooser is native: `Page.setInterceptFileChooserDialog` +
  `DOM.setFileInputFiles`.
- A real attachment chip is inside `.prompt-ingredient-bar` AND contains an
  `<img>`. The upload-progress chip (innerText `cancel`, no image) is NOT
  proof — the pipeline once paid for a prompt-only generation.
- Closing the library panel with Cancel can DISCARD the chip; Escape is safe.
  "Add to prompt" menu item can be a no-op — click the asset tile instead.
- Flow's UI follows the profile language: stable classes + ID/EN label alias
  tables; adding a language = adding aliases.
- Prompts sent to Flow must be English, even with Indonesian overlay text.

## File map

| File | Role |
|---|---|
| `bits_affiliate/pipeline.py` | orchestrator, CLI, account fallback, `sumber.json`, cleanup hook |
| `bits_affiliate/prompts.py` | storyboard prompts: 2 templates + `SPEC[seconds]` |
| `bits_affiliate/publish.py` | `publish.txt` builder (`publish_cli.py` = `bits-publish`) |
| `bits_affiliate/gemini_stage.py` | stage 1: upload, prompt, waits, retries, grab |
| `bits_affiliate/flow_stage.py` | stage 2: settings → render → download, probe |
| `bits_affiliate/flow_cdp.py` | CDP transport, profiles, Chrome, VNC, `cleanup_all()` |
| `bits_affiliate/sources.py` | `bits-sources` inbox + cleanup |
| `bits_affiliate/check_credits.py` | credit balance reader (ID/EN) |
| `bits_affiliate/login_bench.py` | login workbench |
| `references/UI-GEMINI-FLOW.md` | UI selector reference (read when UI drifts) |
| `tests/test_*.py` | offline unit tests |
| `~/.config/bits-affiliate/caps.json` | cached probe: duration cap + cost per account |
