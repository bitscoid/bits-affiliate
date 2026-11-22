---
name: bits-affiliate
version: 3.2.0
author: BITS
license: internal
description: >
  Affiliate video pipeline in two stages: Gemini turns a product photo (and an
  optional model photo) into a storyboard image + a Google Flow prompt, then
  Google Flow renders a 9:16 video. Use when the user says "pakai
  bits-affiliate", "buatkan video dengan bahan <path>", mentions product.jpg /
  model.jpg, or asks for the affiliate video skill. Outputs:
  ./<slug>/STORYBOARD.jpeg, ./<slug>/FLOW.md, ./<slug>/<slug>.mp4.
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
  [1] GEMINI  --> STORYBOARD.jpeg + FLOW.md   (0 credits)
        |
        v
  [2] FLOW    --> <slug>.mp4                  (48 credits, 9:16 Omni 1.2 720p 13s x1)
```

## Install

```bash
pip install -e <skill-dir>
```

Requirements: Python >= 3.10, `websockets>=12` (installed by pip), Google
Chrome (binary path override: `FLOW_CHROME=/path/to/chrome`), `ffmpeg` and
`ffprobe` on PATH. Console scripts after install: `bits-affiliate`,
`bits-credits`, `bits-login`. Without install, run from the skill folder:
`python3 -m bits_affiliate ...`.

## Inputs

| User provides | Gemini prompt used | Images sent |
|---|---|---|
| product.jpg only | `references/PROMPT-GEMINI-PRODUCT-ONLY.md` | 1 |
| product.jpg + model.jpg | `references/PROMPT-GEMINI-PRODUCT-MODEL.md` | 2 |

Identify images **by content**, never by argument order. Files are validated
by magic bytes and converted to JPEG when needed (a saved HTML page named
`.jpg` fails here, not after minutes inside Gemini).

## Usage

```bash
cd <workdir>
bits-affiliate --product /path/product.jpg [--model /path/model.jpg]
```

| Flag | Meaning |
|---|---|
| `--title "text"` | video title; empty = AI picks it from the storyboard |
| `--slug name` | output folder; default derived from the title |
| `--account email` | account for stage 1; default constant in `pipeline.py` |
| `--mode Auto` | Gemini model mode; default = leave the account's setting |
| `--timeout 900` | per-stage wait budget in seconds; keep >= 900 |
| `--skip-gemini` | reuse existing STORYBOARD.jpeg + FLOW.md |
| `--skip-flow` | stop after Gemini (0 credits spent) |
| `--dry-run` | Flow stops right before spending credits |
| `--vnc` | TEST MODE: visible Chrome on Xvfb `:99` + x11vnc :5900 (watch the run); default is headless |

Output in `./<slug>/`: `STORYBOARD.jpeg`, `FLOW.md`, `<slug>.mp4`, `src/`
(normalized input images). The folder is renamed once the AI title is known.
The final summary is printed as JSON (paths are updated after the rename).

## Stage 1 — Gemini (0 credits)

1. Open `https://gemini.google.com/app` in the account's profile.
2. (`--mode`) Open the mode picker, select the requested mode (bilingual
   labels: `pemilih mode` / `mode picker`).
3. Click the **Upload & tools** button (`Upload & alat` /
   `Upload files and tools`) — this is what instantiates the file inputs;
   they do not exist in the DOM before that click.
4. Feed images into `input[type=file][accept*="image"]` via CDP
   `DOM.setFileInputFiles`. Never use the native file chooser: inconsistent.
5. Click the plus menu item **Buat gambar / Create images** — forces an image
   in the reply (attempt 1). Poll for it: the item renders late.
6. Paste the reference prompt into the `[contenteditable="true"]` editor via
   `Input.insertText`, then click **Kirim pesan / Send message**.
7. Wait for completion: text stops growing AND the Stop button is gone.
   Silence of >5 minutes before the first token is NORMAL (server queue);
   images appearing before text also counts as progress. A page that shrinks
   by >50% means Gemini reset the conversation — a failure.
8. Grab the storyboard: `img` with the largest **rendered** size (source
   thumbnail ~112 px, storyboard ~708 px; `naturalWidth` lies), click
   **Download full-size image** (hover to reveal, poll up to 6 s), detect the
   new file by **mtime** in the profile's downloads folder, save as
   `STORYBOARD.jpeg`.
9. Extract the fenced block under `PROMPT GOOGLE FLOW` into `FLOW.md`, and
   the title hint from the LAST `STORYBOARD + FLOW PROMPT —` line (earlier
   matches are the echoed prompt's placeholder).

Failure handling, `ATTEMPTS = 2` with a fresh conversation each attempt:

- **Stalled** (`GeminiStalled`): Stop button present, no growth for 36 polls
  (= 6 min) across text and image counts.
- **Conversation reset** (`GeminiStalled`): page shrank >50%.
- **Incomplete reply** (`ReplyIncomplete`): no storyboard image, or the Flow
  block is missing. The error message includes the tail of Gemini's reply
  (limits/refusals are written there). Attempt 2 drops the Create-image tool,
  because forcing image mode can swallow the text block.

## Stage 2 — Flow (48 credits)

1. `https://flow.google.com/` → **New project** (`.mdc-fab`); `/about` or
   `/?pli=` means the session is logged out.
2. Settings via `.settings-trigger-button`: `Video` → `Ingredients (Bahan)`
   → `9:16` → `720p` → `x1` → model `Omni 1.2 Flash`.
3. Duration slider `input.mdc-slider__input`: raw value = seconds − 3
   (13 s → set **10**). Verify the composer chip says `13 dtk`.
4. Upload `STORYBOARD.jpeg` via the ingredients panel, then **click the
   thumbnail / "Add to prompt"** — uploading alone attaches nothing; verify
   the ingredient chip exists.
5. Paste `FLOW.md` into the prompt editor with `Input.insertText`.
6. Click **Mulai pembuatan / Start generation**. A `prompt-warning-button`
   means the account cannot pay — a hard stop, then auto-switch accounts.
7. Poll until the 9:16 clip thumbnail (`/asb/`, taller than wide) appears;
   watch for logout the whole time. Click it → `/edit/<clip-id>`.
8. **Download media** opens a quality menu (`270p GIF` / `720p Original size`
   / `1080p · 3 credits` / `4K`); pick **720p** (native resolution, no extra
   credits). The finished file is copied from the downloads folder to
   `./<slug>/<slug>.mp4` (detected by stable size across two polls).

Locked video spec — no fallbacks on model, resolution, or duration:

| Rule | Value |
|---|---|
| Mode | Ingredients |
| Aspect | 9:16 |
| Model | Omni 1.2 Flash |
| Resolution | 720p |
| Duration | 13 s (slider raw = seconds − 3) |
| Outputs | x1 (Flow defaults to x2; must be zeroed) |
| Cost | 48 credits |

Gemini writes six 10-second beats in its prompt; Flow renders 13 s and
decides its own pacing.

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
python3 tests/test_gemini_stage.py    # storyboard grab, waits, retries
python3 tests/test_login_bench.py     # --add/--done/--check behavior
python3 tests/test_ui_labels.py       # every UI label exists in ID AND EN
```

Also runnable via `pytest`. `test_ui_labels` is the guard rail for UI drift:
any label used by the code must have both language variants.

## Pitfalls (all happened in the field)

- Gemini stage costs 0 credits; the 48 credits are Flow-only. `--skip-flow`
  exits before any credit check.
- Gemini's file inputs are lazy — nothing in the DOM until "Upload & tools"
  is clicked.
- Pick the storyboard by **rendered** size, not `naturalWidth` — the source
  photo (1024×1024) renders as a 112 px thumbnail and beats the real
  storyboard on natural size.
- Detect downloads by **mtime**, not by filename — Chrome appends ` (1)` when
  the same name already exists.
- Gemini can stay silent for >5 minutes before the first token, and images
  can appear before text grows. Both are progress; `STALL_ROUNDS = 36`
  (6 min) and `--timeout >= 900` exist because of this.
- Gemini sometimes resets the conversation instead of answering (page
  collapses to ~300 chars). Detected, retried, then reported.
- Replies are sometimes text-only (no image). Attempt 1 selects "Create
  images" in the plus menu to prevent this; attempt 2 runs without it in
  case the tool eats the Flow text block.
- Headless is the default. `--vnc` exists only to watch a run while
  debugging; VNC credentials live in `~/.config/bits-affiliate/vnc.passwd`
  and the runtime prints the connect hint.
- Flow's file chooser is native: use `Page.setInterceptFileChooserDialog` +
  `DOM.setFileInputFiles`; there is no plain `<input type=file>`.
- Upload ≠ attach in Flow: after uploading, click the thumbnail or "Add to
  prompt" and confirm the ingredient chip.
- The duration slider's raw value is seconds − 3, and the composer chip must
  be re-read after the settings panel closes — it is the source of truth.
- Flow's UI follows the profile language. Selectors use stable classes
  (`.settings-trigger-button`, `.mdc-fab`, `.mat-button-toggle-button`,
  `.asset-thumbnail`) plus label alias tables; adding a language = adding
  aliases.
- Prompts sent to Flow must be in English, even when storyboard overlay text
  is Indonesian.
- Flow logout during render is detected from `/about` and the
  "Buat dengan / Create with Google Flow" text — a session that dies mid-run
  must not look like a slow render.

## File map

| File | Role |
|---|---|
| `bits_affiliate/pipeline.py` | two-stage orchestrator, CLI, account fallback |
| `bits_affiliate/gemini_stage.py` | stage 1: upload, prompt, waits, retries, storyboard + Flow prompt |
| `bits_affiliate/flow_stage.py` | stage 2: settings, ingredients, generate, download; locked spec + `COST` |
| `bits_affiliate/flow_cdp.py` | CDP transport, per-account profiles, Chrome launch/kill, VNC |
| `bits_affiliate/check_credits.py` | per-account credit balance reader |
| `bits_affiliate/login_bench.py` | login workbench: status, open, add, verify, probe |
| `bits_affiliate/__init__.py` | package version (read by pyproject) |
| `bits_affiliate/__main__.py` | `python3 -m bits_affiliate` entry |
| `tests/test_*.py` | unit tests, no Chrome needed |
| `references/PROMPT-GEMINI-*.md` | the two Gemini prompts |
| `references/UI-GEMINI-FLOW.md` | selector map for maintenance when Google changes the UI |
