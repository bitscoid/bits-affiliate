# 🎬 bits-affiliate

> **Product photo → Gemini storyboard → Google Flow 9:16 video.**
> Fully automated over Chrome DevTools Protocol. One command, one video.

![Version](https://img.shields.io/badge/version-3.5.0-blue)
![Python](https://img.shields.io/badge/python-3.10%2B-informational)
![Tests](https://img.shields.io/badge/tests-128%2F128-brightgreen)
![License](https://img.shields.io/badge/license-MIT-green)
![Status](https://img.shields.io/badge/status-production%20ready-success)

A production-grade pipeline that turns a product photo — with an optional
model photo — into a ready-to-post vertical video. Gemini designs the
storyboard and writes the Flow prompt; Google Flow renders the video; the
pipeline drives both through Chrome DevTools Protocol with zero-credit
failure safety and multi-account rotation.

---

## ✨ Features

- 🎯 **One command** — product photo in, 9:16 video out
- 📐 **Adaptive duration** — 13 s when the account supports it, 10 s fallback,
  decided by *measuring* Flow's slider (never by guessing the plan tier)
- 🔁 **Multi-account rotation** — daily Gemini image quota exhausted? Flow
  account out of credits? Flaky UI? The pipeline rotates accounts
  automatically and reports what happened
- 💸 **Zero-credit failure safety** — every pre-billing failure (settings,
  upload, attach) rotates away *before* a single credit is spent; credits
  are only charged when the storyboard chip is verified inside the prompt
- 🧠 **Agent-ready** — ships as a skill for [OpenCode](https://opencode.ai)
  and [Hermes Agent](https://hermes-agent.nousresearch.com): describe the
  job in natural language, the agent runs the pipeline
- 🖥️ **Watchable** — headless by default; `--vnc` streams the live Chrome
  session over VNC while the run progresses

---

## 🚀 Quick Start

### 1. Install

```bash
git clone https://github.com/bitscoid/bits-affiliate.git
cd bits-affiliate
pip install -e .
```

Requirements: Python ≥ 3.10, Google Chrome (`FLOW_CHROME=/path/to/chrome`
to override), `ffmpeg` + `ffprobe` on PATH.

### 2. Log in once (per Google account)

```bash
bits-login --add you@gmail.com   # creates an isolated Chrome profile
# …sign in over VNC / the visible window…
bits-login --done you@gmail.com  # verifies the session email
```

Sessions persist forever — Google binds them to the device, so this is a
one-time step per account.

### 3. Make a video 🎥

```bash
cd my-videos                       # results land here — no static paths
bits-affiliate --product product.jpg --model model.jpg
```

Results land in `./<slug>/` — the **current directory**, like any well-behaved
CLI (`--out DIR` overrides). No nested `<slug>/<slug>/`.

Output:

```
./<slug>/
├── STORYBOARD.jpeg    # AI-designed storyboard sheet
├── FLOW.md            # the Flow prompt Gemini wrote
├── <slug>.mp4         # 🎬 the video (720×1280, 13 s or 10 s)
├── sumber.json        # provenance: which input files were used (exit code included)
└── publish.txt        # Buffer-ready caption (TikTok / Reels / Shorts blocks)
```

Input images are read straight from where they live — there is no `src/`
folder in the result. PNG/WebP conversion happens in a throwaway temp dir that is
deleted when the process exits.

---

## ⚙️ CLI Reference

| Flag | Meaning |
|---|---|
| `--product path` | product photo (**required**) |
| `--model path` | model photo — enables the product+model storyboard |
| `--title "text"` | video title; empty = AI picks it from the storyboard |
| `--slug name` | output folder name |
| `--out DIR` | parent folder for results (default: current directory) |
| `--account email` | starting account (default: first registered) |
| `--seconds 10\|13` | force duration, skipping the auto probe |
| `--mode Auto` | Gemini model mode, when the account exposes it |
| `--timeout 900` | per-stage wait budget (seconds) |
| `--skip-gemini` | reuse an existing `STORYBOARD.jpeg` + `FLOW.md` |
| `--skip-flow` | stop after Gemini — zero credits spent |
| `--dry-run` | Flow stops right before billing |
| `--vnc` | visible Chrome on Xvfb + VNC (watch the run) |

Companion tools:

```bash
bits-sources status    # WhatsApp attachments waiting in the cache, finished projects
bits-sources ambil     # move them into $BITS_PROJECTS/Sources (+ manifest.json)
bits-sources bersihkan # empty Sources once a project has finished
bits-publish <slug>    # rewrite publish.txt (caption) without re-rendering
bits-credits 48        # which accounts can pay for one video?
bits-login             # account status board
```

Every run stops its own Chrome/Xvfb processes on exit — no leftovers.

---

## 🎥 How It Works

```text
product.jpg (+ model.jpg?)
        │
        ▼
  [0] PROBE ──── measures each account's Flow duration cap + credit cost
  │              (0 credits · cached in caps.json)
  ▼
  [1] GEMINI ─── storyboard sheet + Flow prompt written by AI  (0 credits)
  │              retries with a fresh conversation · rotates accounts
  ▼
  [2] FLOW ───── settings → upload → verified attach → generate → download
  │              rotates on any pre-billing failure (0 credits burned)
  ▼
  <slug>/<slug>.mp4 ── 720×1280 · 13 s (48 credits) or 10 s (30 credits)
```

### The probe: measure, don't guess 📏

Flow caps duration per account tier and no plan label reveals it, so Phase 0
reads the slider's `max` + the cost chip and caches it per account. 13 s wins
when an affordable account supports it — otherwise the best 10 s account is
used with a matching 10 s prompt template, so pacing always matches the render.

### Safety layers 🛡️

| Failure | Response | Credits burned |
|---|---|---|
| Gemini image quota / stall / reset | fresh conversation ×2, then rotate account | 0 |
| Flow settings / upload / attach broken | `FlowSetupError` → rotate account | 0 |
| Duration cap below target | reject account at the slider gate | 0 |
| Storyboard chip not verified in the prompt bar | reject account | 0 |
| Render/download dies | **hard stop** (rotating would pay twice) | only the one video |

Credits are charged exclusively at **Mulai pembuatan** — and only after the
storyboard chip has been verified inside the prompt bar with a loaded image.

---

## 🧪 Testing

No Chrome required — every test runs offline:

```bash
python3 tests/test_check_credits.py    # ID/EN credit parsers + CLI
python3 tests/test_flow_duration.py    # adaptive duration + template selection
python3 tests/test_gemini_stage.py     # storyboard grab, waits, retries
python3 tests/test_login_bench.py      # login workbench contract
python3 tests/test_prompt_render.py    # prompts byte-identical to the legacy files
python3 tests/test_publish.py          # publish.txt shape, tags, honest status
python3 tests/test_ui_labels.py        # bilingual UI label guard
```

`test_ui_labels` is the drift guard: any UI label the code matches must
exist in **both** Indonesian and English.

---

## 🗺️ Project Structure

```
bits-affiliate/
├── bits_affiliate/
│   ├── pipeline.py          # two-stage orchestrator + CLI + sumber.json
│   ├── prompts.py           # storyboard prompts: 2 templates + SPEC[seconds]
│   ├── publish.py           # publish.txt builder (+ publish_cli.py = bits-publish)
│   ├── gemini_stage.py      # stage 1: storyboard + Flow prompt
│   ├── flow_stage.py        # stage 2: settings → render → download
│   ├── flow_cdp.py          # CDP transport, profiles, Chrome, VNC, cleanup_all()
│   ├── sources.py           # bits-sources: Sources inbox + cleanup
│   ├── check_credits.py     # credit balance reader (ID/EN)
│   └── login_bench.py       # login workbench
├── references/
│   └── UI-GEMINI-FLOW.md    # selector map for UI-drift maintenance
├── tests/                   # offline unit tests
├── SKILL.md                 # agent skill contract (OpenCode / Hermes)
└── pyproject.toml
```

---

## 🤖 Using as an Agent Skill

The repo ships a complete `SKILL.md`, so coding agents can operate the
pipeline from natural language. Link it into your agent's skill folder:

```bash
ln -sfn /path/to/bits-affiliate ~/.config/opencode/skills/bits-affiliate   # OpenCode
ln -sfn /path/to/bits-affiliate ~/.hermes/skills/bits-affiliate            # Hermes (repeat per profile)
```

Then just say: *"pakai bits-affiliate, buatkan video dari product.jpg dan
model.jpg"*.

---

## 📝 Notes

- Flow's UI follows the profile language — selectors cover **ID 🇮🇩 + EN 🇬🇧**
- Probe cache lives at `~/.config/bits-affiliate/caps.json` (delete to re-probe)

---

## 📄 License

[MIT](LICENSE) © 2026 BITS
