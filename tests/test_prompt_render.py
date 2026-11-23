"""Prompt tersusun benar: 4 kombinasi, isi sesuai kontrak.

Menggantikan empat file `references/PROMPT-GEMINI-*.md`. Yang diuji bukan cuma
"ada teks", tapi angka yang mudah salah: durasi, anggaran kata per beat, batas
akhir kalimat, dan frasa yang hanya boleh muncul di salah satu varian.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bits_affiliate import flow_stage, prompts  # noqa: E402

lulus = 0


def ok(kondisi, nama):
    global lulus
    assert kondisi, nama
    lulus += 1


# ── keempat kombinasi bisa dirender ─────────────────────────────────────────
for kind in ("product-only", "product+model"):
    for detik in (13, 10):
        t = prompts.render_prompt(kind, detik)
        ok(len(t) > 5000, f"{kind} {detik}s: prompt utuh ({len(t)} char)")
        ok(t == prompts.render_prompt(kind, detik), f"{kind} {detik}s: deterministik")

# ── durasi & timing hadir persis ────────────────────────────────────────────
t13 = prompts.render_prompt("product-only", 13)
t10 = prompts.render_prompt("product-only", 10)
ok("exactly 13 seconds" in t13, "13s: durasi tertulis")
ok("exactly 10 seconds" in t10, "10s: durasi tertulis")
ok("00:11–00:13" in t13, "13s: beat terakhir")
ok("00:08.0–00:10.0" in t10, "10s: beat terakhir")
ok("13 seconds" not in t10, "10s: tidak menyebut 13 dtk")
ok("10 seconds" not in t13, "13s: tidak menyebut 10 dtk")

# ── anggaran kata: hard limit yang pernah bikin kata terpotong ──────────────
ok("26 words maximum" in t13, "13s: total 26 kata")
ok("20 words maximum" in t10, "10s: total 20 kata")
ok("00:12.65" in t13, "13s: VO berhenti 00:12.65 (0,35 dtk hening)")
ok("00:09.65" in t10, "10s: VO berhenti 00:09.65")
for pola, teks in (("4/4/5/4/5/4", t13), ("3/3/3/4/3/4", t10)):
    ok(pola in teks, f"pola anggaran {pola}")

# ── larangan yang wajib ada di semua varian ────────────────────────────────
for kind in ("product-only", "product+model"):
    for detik in (13, 10):
        t = prompts.render_prompt(kind, detik)
        for frasa in (
            "No music, captions, subtitles, or CTA",
            "Never invent specifications",
            "PROMPT GOOGLE FLOW",
            "vertical 9:16, 720×1280",
        ):
            ok(frasa in t, f"{kind} {detik}s: wajib ada {frasa!r}")

# ── varian model: aturan wardrobe hanya di sana ────────────────────────────
m13 = prompts.render_prompt("product+model", 13)
ok("Wardrobe continuity lock" in m13, "model: wardrobe lock ada")
ok("Muslimah product exception" in m13, "model: pengecualian Muslimah ada")
ok("Wardrobe continuity lock" not in t13, "hanya-produk: tanpa aturan wardrobe")

# ── jenis/durasi tak dikenal ditolak ───────────────────────────────────────
for arg, pesan in (
    (("produk-saja", 13), "kind tidak dikenal"),
    (("product-only", 15), "durasi tidak didukung"),
):
    try:
        prompts.render_prompt(*arg)
        ok(False, f"{arg}: harus menolak")
    except ValueError as e:
        ok(pesan in str(e), f"{arg}: menolak dengan pesan jelas")

# ── flow_stage.SECONDS harus punya spec ────────────────────────────────────
ok(flow_stage.SECONDS in prompts.SPEC, f"default {flow_stage.SECONDS}s punya spec")
ok(set(prompts.SPEC) == set(prompts.ANGGARAN), "SPEC dan ANGGARAN sinkron")

print(f"{lulus}/{lulus} lulus")
