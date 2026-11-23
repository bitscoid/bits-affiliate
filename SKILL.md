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

Two stages, fully driven over Chrome DevTools Protocol (CDP):

```
product.jpg (+ model.jpg?)
        |
        v
  [0] PROBE   --> account + duration (0 credits; 13 s preferred, 10 s fallback)
        |
        v
  [1] GEMINI  --> STORYBOARD.jpeg + FLOW.md   (0 credits)
        |
        v
  [2] FLOW    --> <slug>.mp4                  (9:16 Omni 1.2 720p x1; cost read
                                               from the composer chip)
```

## Install

```bash
pip install -e <skill-dir>
```

Requirements: Python >= 3.10, `websockets>=12` (installed by pip), Google
Chrome (binary path override: `FLOW_CHROME=/path/to/chrome`), `ffmpeg` and
`ffprobe` on PATH. Console scripts after install: `bits-affiliate`,
`bits-credits`, `bits-login`, `bits-publish`, `bits-sources`. Without install,
run from the skill folder: `python3 -m bits_affiliate ...`.

## Inputs

| User provides | Prompt built by | Images sent |
|---|---|---|
| product.jpg only | `prompts.render_prompt("product-only", s)` | 1 |
| product.jpg + model.jpg | `prompts.render_prompt("product+model", s)` | 2 |

The two prompt templates live in `bits_affiliate/prompts.py`; the duration-dependent
block (beat timecodes, word budget, VO end point) is `prompts.SPEC[seconds]`. Adding
a duration means adding one `SPEC` entry, not copying a file.

Identify images **by content**, never by argument order. Files are validated
by magic bytes and converted to JPEG when needed (a saved HTML page named
`.jpg` fails here, not after minutes inside Gemini).

## Usage

```bash
# Bahan dari /root/Projects/Sources, hasil ke /root/Projects/<slug>/<slug>.mp4
bits-affiliate --product /root/Projects/Sources/product.jpg [--model /root/Projects/Sources/model.jpg]
```

Run it from anywhere: results default to `/root/Projects` (`--out` overrides). There is
no `cd <workdir>` requirement and no nested `<slug>/<slug>/` folder.

| Flag | Meaning |
|---|---|
| `--title "text"` | video title; empty = AI picks it from the storyboard |
| `--slug name` | output folder; default derived from the title |
| `--account email` | account for stage 1; stage 1 auto-rotates up to 3 accounts when one fails (daily image quota is real); stage 2 auto-switches on credits |
| `--mode Auto` | Gemini model mode — only for accounts whose picker exposes it (A/B: some accounts only have Hydrogen / Otomatis); the code logs and continues with the account's setting when the item is missing |
| `--seconds 10\|13` | force the video duration, skipping the auto probe (use with `--skip-gemini` when the existing artifacts were built for that duration) |
| `--timeout 900` | per-stage wait budget in seconds; keep >= 900 |
| `--skip-gemini` | reuse existing STORYBOARD.jpeg + FLOW.md |
| `--skip-flow` | stop after Gemini (0 credits spent) |
| `--dry-run` | Flow stops right before spending credits |
| `--out DIR` | parent folder for results (default `/root/Projects`, or cwd when that folder does not exist) |
| `--vnc` | TEST MODE: visible Chrome on Xvfb `:99` + x11vnc :5900 (watch the run); default is headless |

Output in `<out>/<slug>/`: `STORYBOARD.jpeg`, `FLOW.md`, `<slug>.mp4`, `sumber.json`,
`publish.txt`.

**`publish.txt` dibuat otomatis** oleh `bits_affiliate/publish.py`, didaftarkan pada
`atexit` di pipeline — jadi setiap run (sukses, `--skip-flow`, `--dry-run`,
`SystemExit`) meninggalkan caption siap Buffer. Bentuknya sengaja sama dengan video
BITS lain (`/root/Videos/<slug>/output/publish.txt`): header 6 baris `# ` + blok
`=== TikTok`, `=== Instagram Reels`, `=== YouTube Shorts`. Dengan begitu
`buffer-rencana-penuh.py` membacanya tanpa perubahan apa pun.

Isinya diturunkan dari bahan yang ada, bukan dikarang: deskripsi dari naskah VO
(`vo/naskah.txt`, inti dibatasi 42 kata + satu kalimat penutup = maksimal 50
kata), judul dari kalimat penutup naskah, dan tagar
dari tema yang Terdeteksi (parfum, perawatan, fashion, rumah, gadget) dengan
`umum` sebagai cadangan supaya caption tidak pernah gagal terbit. Nominal harga
dan CTA "Hubungi kami" tidak pernah ditulis — aturan BITS.

`# STATUS` jujur: `BELUM-ADA-VIDEO` bila mp4 belum ada (mis. run berhenti sebelum
Flow), supaya rencana Buffer tidak menjadwalkan berkas yang tidak ada. Tulis ulang
caption kapan saja tanpa render ulang:

```bash
bits-publish <slug>              # tulis ulang publish.txt dari sumber.json + naskah
bits-publish <slug> --channel "BITS Living" --status SIAP
```

Video affiliate ikut masuk rencana Buffer otomatis: `buffer-rencana-penuh.py`
memindai `/root/Videos` DAN `/root/Projects`, dan URL asetnya memakai jalur
`https://videos.bits.co.id/affiliates/<slug>/<slug>.mp4`.
**No `src/`** — input images are read straight from where they live (normally
`/root/Projects/Sources/`), never copied or modified in place; any PNG/WebP
conversion happens in a throwaway temp dir that is deleted on exit. The folder is
renamed once the AI title is known. The final summary is printed as JSON (paths
are updated after the rename).

`sumber.json` is written by the pipeline itself on every exit (success, `--dry-run`,
`SystemExit`): it records `slug`, `waktu`, `berkas` (the basenames actually used),
`perintah`, and `exit`. `bits-sources bersihkan` reads it to decide which materials
are safe to delete — never guess from folder names.

### Cleanup is automatic

The pipeline registers `fc.cleanup_all` via `atexit`, so Chrome, Xvfb `:99`, x11vnc,
and the keyring dbus daemon left behind by the run are stopped on **every** exit
path. Field evidence: a *successful* `--skip-flow` run left 12 Chrome processes
alive. The cleanup only touches this skill's own processes — it refuses to kill any
command line containing `hermes-snap` or `cache/scratch` (those are the operator's
own shell). Call it by hand only if you drove `flow_cdp` directly:

```bash
python3 -c "from bits_affiliate import flow_cdp as fc; fc.cleanup_all()"
```

## Sources (WhatsApp attachments → `/root/Projects/Sources`)

```bash
bits-sources status             # what is waiting, what is finished
bits-sources ambil              # move new cache images into Sources
bits-sources ambil --dry-run    # report only
bits-sources bersihkan          # delete Sources contents once a project finished
bits-sources bersihkan --dry-run
```

Each file lands with an entry in `Sources/manifest.json` recording its origin
(`document` = untouched quality, `foto-whatsapp` = WhatsApp-compressed).
`.heic/.heif` are refused. `bersihkan` refuses to run while no project has
finished, so materials are never wiped mid-production.

## Credits — one rule, stated once

Gemini is free; Flow charges only when **Mulai pembuatan / Start generation** is
clicked: **30 credits for 10 s, 48 credits for 13 s**. Probe and `--dry-run` stop
before that click, and `--skip-flow` never reaches it — so all three cost 0.

DURATION is measured, never assumed: caps vary per account tier and no plan
label reveals them. Accounts are walked richest-first; each is either read
from the cache `~/.config/bits-affiliate/caps.json` or probed (new Flow
project → settings → read the slider's `max` + the cost chip; stops before
upload/prompt/billing). 13 s wins when any affordable account supports it,
otherwise the best 10 s account. The chosen duration selects the matching
`prompts.SPEC[seconds]` block (beat timecodes + word budget) so the
storyboard is always designed for the duration Flow will actually render.
Delete `caps.json` to re-probe.

Locked regardless of duration: Ingredients mode, 9:16, 720p, x1, Omni 1.2
Flash.

## Stage 1 — Gemini (0 credits)

1. Open `https://gemini.google.com/app` in the account's profile.
2. (`--mode`) Open the mode picker, select the requested mode (bilingual
   labels: `pemilih mode` / `mode picker`).
3. Click the **Upload & tools** button (`Upload & alat` /
   `Upload files and tools`) — this is what instantiates the file inputs;
   they do not exist in the DOM before that click.
4. Feed images into `input[type=file][accept*="image"]` via CDP
   `DOM.setFileInputFiles`. Never use the native file chooser: inconsistent.
5. Paste the reference prompt into the `[contenteditable="true"]` editor via
   `Input.insertText`, then click **Kirim pesan / Send message**.
6. Wait for completion: text stops growing AND the Stop button is gone.
   Silence of >5 minutes before the first token is NORMAL (server queue);
   images appearing before text also counts as progress. A page that shrinks
   by >50% means Gemini reset the conversation — a failure.
7. Grab the storyboard: `img` with the largest **rendered** size (source
   thumbnail ~112 px, storyboard ~708 px; `naturalWidth` lies), click
   **Download full-size image** (hover to reveal, poll up to 6 s), detect the
   new file by **mtime** in the skill's downloads folder
   (`~/.config/bits-affiliate/downloads`), save as `STORYBOARD.jpeg`.
8. Extract the fenced block under `PROMPT GOOGLE FLOW` into `FLOW.md`, and
   the title hint from the LAST `STORYBOARD + FLOW PROMPT —` line (earlier
   matches are the echoed prompt's placeholder).

Failure handling, `ATTEMPTS = 2` with a fresh conversation each attempt:

- **Stalled** (`GeminiStalled`): Stop button present, no growth for 36 polls
  (= 6 min) across text and image counts.
- **Conversation reset** (`GeminiStalled`): page shrank >50%.
- **Incomplete reply** (`ReplyIncomplete`): no storyboard image, or the Flow
  block is missing. The error message includes the tail of Gemini's reply
  (limits/refusals are written there). The "Buat gambar / Create images"
  plus-menu tool is NOT used at all: in the field it broke more replies
  than it fixed, so a failure simply retries the identical plain process.

## Stage 2 — Flow (13 s ≈ 48 credits; 10 s cheaper, measured at probe)

1. `https://flow.google.com/` → **New project** (`.mdc-fab`); `/about` or
   `/?pli=` means the session is logged out.
2. Settings via `.settings-trigger-button`: `Video` → `Ingredients (Bahan)`
   → `9:16` → `720p` → `x1` → model `Omni 1.2 Flash`.
3. Duration slider `input.mdc-slider__input`: raw value = seconds − 3
   (13 s → set **10**). The slider's `max` attribute is read first —
   duration caps vary per account tier (not visible from any plan
   label); an account whose max is below the chosen duration is
   rejected via `FlowSetupError` (rotated away, zero credits) BEFORE
   the value is set. Verify the composer chip says the chosen
   duration (`13 dtk` or `10 dtk`).
4. Upload `STORYBOARD.jpeg` via the ingredients panel, then **click the
   asset thumbnail itself** — uploading alone attaches nothing; verify the
   chip (see pitfalls).
5. Paste `FLOW.md` into the prompt editor with `Input.insertText`.
6. Click **Mulai pembuatan / Start generation**. A `prompt-warning-button`
   means the account cannot pay — a hard stop, then auto-switch accounts.
7. Poll until the clip tile appears in the project grid: a `/asb/` image
   taller than wide INSIDE a tile container (the storyboard chip in the
   prompt bar uses `/asb/` too and must not match). Watch for logout
   the whole time.
8. Click the tile's **More options / Opsi lainnya** (⋮) → **Download** →
   **720p Original size** (`270p GIF` / `720p` / `1080p Upscaled` /
   `4K Upscaled`). The finished file is **moved** from the downloads folder to
   `./<slug>/<slug>.mp4` (detected by stable size across two polls).

Every step that talks to the page goes through the reconnecting CDP wrapper
(`r.call`): Chrome drops the debugger socket during long waits, and a drop
must retry the step, not kill the run.

## Accounts & login

One account = one Chrome profile (`~/.config/bits-affiliate/profiles/<email>/`,
chmod 700) = one CDP port (`9500 + crc32(email) % 400`). Google binds sessions
to the device: copied cookies do not work, so login is done once, manually,
through VNC — then the session is reused forever.

```bash
bits-login                  # status of all accounts (default)
bits-login <email>          # open / manual login over VNC
bits-login --add <email>    # NEW account: create profile, open login page
bits-login --done <email>   # verify the logged-in email, mark ready
bits-login --check [-j N]   # headless probe (parallel, -j clamped to 4)
```

`--add` creates the profile and opens the login page; after signing in
manually, `--done` verifies the actual session email (exit 1 on mismatch or
no session). Status shows stored state plus a live Chrome snapshot
(`LOGIN` = sitting on the login page, `SALAH` = profile logged in as a
different account). Two accounts colliding on one port probe serially.
Never open the same profile twice: two Chromes on one profile corrupt the
cookie DB and can log the wrong account in.

## Credits

```bash
bits-credits 48          # which accounts can pay for one video?
```

Positional args: `[threshold] [email...]`, plus `-j N` (parallel reads) and
`--json`. Exit 1 when no account can pay, 0 otherwise. The credit panel is
read by clicking the header avatar; it exists in two languages and both are
parsed (`50 kredit Google Flow` / `Direset pada 23 Nov pukul 05.54` vs
`50 Google Flow credits` / `Resets Nov 23 at 5:54 AM`). The avatar appears
slowly on some accounts — the reader waits for it before concluding
"logged out". The same reader (`account_balance()`) powers pipeline's
auto account-switching; balances are cached per run so a failing account is
not re-probed.

## Testing

All tests run without Chrome:

```bash
python3 tests/test_check_credits.py   # ID/EN parsers + CLI contract
python3 tests/test_flow_duration.py   # adaptive duration: pick/parse/template
python3 tests/test_gemini_stage.py    # storyboard grab, waits, retries
python3 tests/test_login_bench.py     # --add/--done/--check behavior
python3 tests/test_prompt_render.py   # rendered prompts == the legacy 4 files
python3 tests/test_publish.py         # publish.txt shape, tags, honest status
python3 tests/test_ui_labels.py       # every UI label exists in ID AND EN
```

Also runnable via `pytest`. `test_ui_labels` is the guard rail for UI drift:
any label used by the code must have both language variants.

## Pitfalls (all happened in the field)

- Headless is the default. `--vnc` exists only to watch a run while
  debugging; VNC credentials live in `~/.config/bits-affiliate/vnc.passwd`
  and the runtime prints the connect hint.
- Flow's file chooser is native: use `Page.setInterceptFileChooserDialog` +
  `DOM.setFileInputFiles`; there is no plain `<input type=file>`.
- Ingredient chips: only a chip inside `.prompt-ingredient-bar` that
  CONTAINS an `<img>` is a real attachment. Flow also renders an
  upload-progress chip (innerText 'cancel', no image) that fooled the
  old global count — the pipeline paid for a prompt-only generation.
  Closing the library panel with the Cancel button can DISCARD the
  chip; Escape is safe. Attach by clicking the asset tile itself —
  the "Add to prompt" menu item can be a no-op.
- Flow's UI follows the profile language. Selectors use stable classes
  (`.settings-trigger-button`, `.mdc-fab`, `.mat-button-toggle-button`,
  `.asset-thumbnail`) plus label alias tables; adding a language = adding
  aliases.
- Prompts sent to Flow must be in English, even when storyboard overlay text
  is Indonesian.

## File map

| File | Role |
|---|---|
| `bits_affiliate/pipeline.py` | two-stage orchestrator, CLI, account fallback, `sumber.json`, cleanup hook |
| `bits_affiliate/prompts.py` | storyboard prompts: 2 templates + `SPEC[seconds]` (duration block) |
| `bits_affiliate/publish.py` | `publish.txt` builder (Buffer caption); `publish_cli.py` = `bits-publish` |
| `bits_affiliate/gemini_stage.py` | stage 1: upload, prompt, waits, retries, storyboard + Flow prompt |
| `bits_affiliate/flow_stage.py` | stage 2: settings, ingredients, generate, download; adaptive duration (`pick_seconds`, `parse_cost`, `probe_account`) |
| `bits_affiliate/flow_cdp.py` | CDP transport, per-account profiles, Chrome launch/kill, VNC, `cleanup_all()` |
| `bits_affiliate/sources.py` | `bits-sources`: move WhatsApp attachments into `Sources/`, clean up when done |
| `bits_affiliate/check_credits.py` | per-account credit balance reader |
| `bits_affiliate/login_bench.py` | login workbench: status, open, add, verify, probe |
| `bits_affiliate/__init__.py` | package version (read by pyproject) |
| `bits_affiliate/__main__.py` | `python3 -m bits_affiliate` entry |
| `references/UI-GEMINI-FLOW.md` | UI selector reference for Gemini and Flow pages |
| `tests/test_*.py` | unit tests, no Chrome needed |
| `~/.config/bits-affiliate/caps.json` | cached per-account Flow probe: supported duration + cost (delete to re-probe) |
