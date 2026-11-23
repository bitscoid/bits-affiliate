#!/usr/bin/env python3
"""Inbox bahan: cache WhatsApp -> <BITS_PROJECTS>/Sources -> bersih.

    bits-sources ambil [--dry-run]     # pindahkan bahan baru ke Sources
    bits-sources bersihkan [--dry-run] # kosongkan Sources setelah proyek selesai
    bits-sources status

Kenapa ada: gambar yang dikirim lewat WhatsApp Document mendarat di
`/root/.hermes/cache/documents` dan HILANG setelah 24 jam
(`MEDIA_CACHE_MAX_AGE_HOURS`). Sources menyimpan bahan sampai proyeknya jadi.

Aturan (terbukti dengan uji; jangan dilonggarkan tanpa alasan):

1. `bersihkan` mengosongkan SELURUH isi Sources begitu ada proyek selesai
   (aturan CEO 2026-11-23). Berkas yang belum tercatat dipakai dilaporkan dulu,
   tapi tetap dihapus — operator bisa Ctrl-C kalau itu keliru.
2. Tidak ada flag `--project`. Versi lama punya flag itu tetapi tetap menghapus
   seluruh Sources: menyesatkan dan berbahaya (temuan Farhan, t_3efd75d1).
3. Tidak ada folder `src/`. Bukti pemakaian = `<proyek>/sumber.json`, ditulis
   pipeline sendiri lewat `atexit` (`pipeline.catat_sumber`).
4. Proyek selesai = punya `.mp4` > 10 KB tanpa penanda sementara
   (`.part`/`.tmp`/`.lock`/`.crdownload`).
5. `ambil` menolak HEIC/HEIF dengan pesan jelas: pipeline hanya mengenal
   jpeg/png/webp/gif, jadi meloloskannya berarti gagal 10 menit kemudian di
   `ensure_usable_image`.
6. `ambil` mencatat asal tiap berkas di `Sources/manifest.json`
   (document = kualitas utuh, foto-whatsapp = sudah dikompres) supaya operator
   tidak menebak mana product dan mana model.
"""

import argparse
import hashlib
import json
import os
import shutil
import sys
import time
from pathlib import Path

_HASH_CACHE = {}

# Portabel: root proyek bisa digeser lewat BITS_PROJECTS; cache mengikuti
# home Hermes pengguna (bukan /root literal), jadi jalan di mesin mana pun.
PROJECTS = Path(os.environ.get("BITS_PROJECTS", str(Path.home() / "Projects")))
SOURCES = PROJECTS / "Sources"
MANIFEST = SOURCES / "manifest.json"
_HERMES_CACHE = Path.home() / ".hermes" / "cache"
CACHE_DOKUMEN = _HERMES_CACHE / "documents"  # kiriman Document: utuh
CACHE_GAMBAR = _HERMES_CACHE / "images"  # kiriman Foto: dikompres WhatsApp

OK_EXT = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
TOLAK_EXT = {".heic", ".heif"}
BELUM_SELESAI = {".part", ".tmp", ".partial", ".crdownload", ".lock"}


def proyek_selesai():
    """[(folder, mp4)] untuk proyek yang sudah menghasilkan video final."""
    hasil = []
    if not PROJECTS.is_dir():
        return hasil
    for d in sorted(PROJECTS.iterdir()):
        if not d.is_dir() or d.resolve() == SOURCES.resolve():
            continue
        files = [f for f in d.rglob("*") if f.is_file()]
        if any(f.suffix.lower() in BELUM_SELESAI for f in files):
            continue
        mp4 = [
            f for f in files if f.suffix.lower() == ".mp4" and f.stat().st_size > 10_000
        ]
        if mp4:
            hasil.append((d, mp4[0]))
    return hasil


def bahan_terpakai(selesai):
    """{nama berkas di Sources: [nama proyek]} untuk bahan yang sudah dipakai.

    Bukti pemakaian, berurutan dari paling kuat:
    1. `<proyek>/sumber.json` — ditulis `pipeline.catat_sumber` lewat `atexit`,
       jadi selalu ada walau run berhenti di tengah. Ini bukti utama.
    2. Nama berkas disebut di `FLOW.md`/JSON ringkasan proyek.

    Folder `src/` tidak pernah dipakai sebagai bukti: folder itu sudah dibuang
    dari pipeline (permintaan CEO, 2026-11-23). Jangan hidupkan lagi.
    """
    terpakai = {}
    for folder, _mp4 in selesai:
        catatan = folder / "sumber.json"
        if catatan.is_file():
            try:
                for nama in json.loads(catatan.read_text(encoding="utf-8")).get(
                    "berkas", []
                ):
                    terpakai.setdefault(nama, []).append(f"{folder.name} (sumber.json)")
            except (OSError, json.JSONDecodeError):
                pass
        # nama disebut di artefak teks proyek
        for nama in _nama_di_artefak(folder):
            for f in _berkas_sources():
                if f.name == nama and f.name not in terpakai:
                    terpakai.setdefault(f.name, []).append(f"{folder.name} (catatan)")
    return terpakai


def _berkas_sources():
    if not SOURCES.is_dir():
        return []
    return [f for f in SOURCES.iterdir() if f.is_file() and f.suffix.lower() != ".json"]


def _hash(path, blok=1 << 20):
    """SHA-256 isi berkas; dicache per (path, mtime, size) supaya tidak diulang."""
    st = path.stat()
    kunci = (str(path), st.st_mtime_ns, st.st_size)
    if kunci in _HASH_CACHE:
        return _HASH_CACHE[kunci]
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(blok), b""):
            h.update(chunk)
    _HASH_CACHE[kunci] = h.hexdigest()
    return _HASH_CACHE[kunci]


def _nama_di_artefak(folder):
    """Nama berkas yang disebut di FLOW.md atau ringkasan JSON proyek."""
    nama = set()
    for p in list(folder.glob("*.md")) + list(folder.glob("*.json")):
        try:
            teks = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for f in _berkas_sources():
            if f.name in teks:
                nama.add(f.name)
    return nama


def baca_manifest():
    if not MANIFEST.is_file():
        return {}
    try:
        return json.loads(MANIFEST.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(
            f"PERINGATAN: manifest tidak terbaca ({exc}) — dianggap kosong",
            file=sys.stderr,
        )
        return {}


def tulis_manifest(data):
    SOURCES.mkdir(parents=True, exist_ok=True)
    tmp = MANIFEST.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, MANIFEST)  # atomik: manifest tidak pernah setengah tertulis


def cmd_ambil(dry):
    SOURCES.mkdir(parents=True, exist_ok=True)
    manifest = baca_manifest()
    pindah, ditolak = 0, 0

    for cache, asal in ((CACHE_DOKUMEN, "document"), (CACHE_GAMBAR, "foto-whatsapp")):
        if not cache.is_dir():
            continue
        for f in sorted(cache.iterdir()):
            if not f.is_file():
                continue
            ext = f.suffix.lower()
            if ext in TOLAK_EXT:
                print(
                    f"TOLAK  {f.name}: {ext} tidak dikenali pipeline "
                    f"(hanya jpeg/png/webp/gif). Kirim ulang sebagai JPG/PNG.",
                    file=sys.stderr,
                )
                ditolak += 1
                continue
            if ext not in OK_EXT:
                continue
            tujuan = SOURCES / f.name
            if tujuan.exists():
                tujuan = SOURCES / f"{f.stem}-{f.stat().st_size}{f.suffix}"
                if tujuan.exists():
                    print(f"LEWATI {f.name}: sudah ada di Sources")
                    continue
            print(f"{'DRY  ' if dry else 'PINDAH'} {f}  ->  {tujuan}")
            if not dry:
                shutil.move(str(f), str(tujuan))
                manifest[tujuan.name] = {
                    "asal": asal,
                    "waktu": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "ukuran": tujuan.stat().st_size,
                }
            pindah += 1

    if not dry and pindah:
        tulis_manifest(manifest)
    if not pindah and not ditolak:
        print("Tidak ada gambar baru di cache WhatsApp.")
        return 0
    print(f"\n{pindah} berkas {'akan dipindah' if dry else 'dipindah'} ke {SOURCES}")
    if ditolak:
        print(
            f"{ditolak} berkas ditolak (format tidak didukung pipeline)",
            file=sys.stderr,
        )
        return 1
    return 0


def cmd_bersihkan(dry):
    """Hapus SELURUH isi Sources setelah ada proyek yang selesai.

    Aturan CEO 2026-11-23: semua berkas di /root/Projects/Sources dihapus
    begitu proyeknya selesai, supaya folder bahan selalu bersih untuk batch
    berikutnya.

    Tidak ada pencadangan ke `src/`: CEO tidak mau folder itu ada di hasil,
    jadi jangan dihidupkan kembali di sini. Kalau sebuah bahan belum tercatat
    di `sumber.json` proyek mana pun, `bersihkan` melaporkannya lebih dulu
    supaya operator bisa membatalkan dengan Ctrl-C kalau itu keliru.
    """
    selesai = proyek_selesai()
    if not selesai:
        print(
            "BERHENTI: belum ada proyek yang selesai. Sources tidak disentuh.",
            file=sys.stderr,
        )
        return 1

    if not SOURCES.is_dir():
        print(f"{SOURCES} belum ada — tidak ada yang dibersihkan.")
        return 0

    isi = [f for f in SOURCES.iterdir() if f.is_file() and f.suffix.lower() != ".json"]
    if not isi:
        print(f"{SOURCES} sudah kosong.")
        return 0

    terpakai = bahan_terpakai(selesai)
    belum_tercatat = [f for f in isi if f.name not in terpakai]

    print("Proyek selesai:")
    for folder, mp4 in selesai:
        print(f"  {folder.name}  ({mp4.name}, {mp4.stat().st_size / 1e6:.1f} MB)")

    if belum_tercatat:
        print(
            f"\nPERHATIAN: {len(belum_tercatat)} berkas belum tercatat dipakai "
            f"proyek mana pun:"
        )
        for f in belum_tercatat:
            print(f"  {f.name}")
        print("  (tetap dihapus sesuai aturan. Ctrl-C sekarang kalau ini keliru.)")

    print(f"\nMenghapus seluruh isi {SOURCES}:")
    for f in isi:
        print(f"{'DRY  ' if dry else 'HAPUS'} {f.name}")
        if not dry:
            f.unlink()

    if not dry:
        tulis_manifest({})

    print(f"\n{len(isi)} berkas {'akan dihapus' if dry else 'dihapus'} dari {SOURCES}")
    return 0


def cmd_status():
    print(f"Sources : {SOURCES} {'(ada)' if SOURCES.is_dir() else '(BELUM ADA)'}")
    manifest = baca_manifest()
    isi = []
    if SOURCES.is_dir():
        isi = [
            f for f in SOURCES.iterdir() if f.is_file() and f.suffix.lower() != ".json"
        ]
        print(f"  {len(isi)} berkas, {sum(f.stat().st_size for f in isi) / 1e6:.1f} MB")

    selesai = proyek_selesai()
    terpakai = bahan_terpakai(selesai)
    for f in sorted(isi):
        m = manifest.get(f.name, {})
        tanda = (
            f"[terpakai: {', '.join(terpakai[f.name])}]"
            if f.name in terpakai
            else "[belum dipakai]"
        )
        print(f"    {f.name}  ({m.get('asal', '?')}, {m.get('waktu', '?')})  {tanda}")

    print("\nCache WhatsApp (hilang otomatis setelah 24 jam):")
    for c, label in (
        (CACHE_DOKUMEN, "Document (utuh)"),
        (CACHE_GAMBAR, "Foto (dikompres WhatsApp)"),
    ):
        n = (
            len([f for f in c.iterdir() if f.suffix.lower() in OK_EXT])
            if c.is_dir()
            else 0
        )
        print(f"  {label}: {n} gambar")

    print("\nProyek selesai:")
    if not selesai:
        print("  (belum ada)")
    for d, m in selesai:
        print(f"  {d.name}  ->  {m.name}")
    return 0


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("ambil").add_argument("--dry-run", action="store_true")
    sub.add_parser("bersihkan").add_argument("--dry-run", action="store_true")
    sub.add_parser("status")
    a = ap.parse_args()

    if a.cmd == "ambil":
        return cmd_ambil(a.dry_run)
    if a.cmd == "bersihkan":
        return cmd_bersihkan(a.dry_run)
    return cmd_status()


if __name__ == "__main__":
    if not sys.argv[1:]:
        print(__doc__, file=sys.stderr)
        sys.exit(1)
    sys.exit(main())
