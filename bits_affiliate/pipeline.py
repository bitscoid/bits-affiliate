#!/usr/bin/env python3
"""bits-affiliate: foto produk (+ model) -> storyboard Gemini -> video Flow.

Tahap 1  Gemini (/app): STORYBOARD.jpeg + FLOW.md
Tahap 2  Google Flow: video 9:16, Omni 1.2 Flash, 720p, x1; durasi
         adaptif — 13 dtk bila akun sanggup, selain itu 10 dtk (diukur
         probe Flow, fase 0, tanpa kredit).

Kredit kurang? Script otomatis pindah ke akun lain yang cukup.
Semua akun habis? Berhenti dengan peringatan + tanggal reset.

Contoh:
    bits-affiliate \
      --product /path/product.jpg \
      --model /path/model.jpg \
      --title "nama-video" \
      --account you@example.com

Hasil di /root/Projects/<slug>/ (atau --out): STORYBOARD.jpeg, FLOW.md,
<slug>.mp4, sumber.json, publish.txt
"""

import argparse
import asyncio
import atexit
import json
import os
import re
import shutil
import subprocess as sp
import sys
import tempfile
import time
from datetime import datetime

from . import check_credits as cc
from . import flow_cdp as fc
from . import flow_stage, gemini_stage, prompts, publish

# Spec video adaptif: Omni 1.2 Flash, 720p, x1; durasi 13 dtk (48 kredit)
# atau 10 dtk untuk akun ber-cap rendah — diukur via probe Flow (fase 0).
DL_DIR = os.path.join(fc.HOME, "downloads")


MAGIC = {
    b"\xff\xd8\xff": "jpeg",
    b"\x89PNG\r\n\x1a\n": "png",
    b"RIFF": "webp",  # plus "WEBP" at offset 8
    b"GIF8": "gif",
}


def sniff_image(path):
    """Return the real image type from magic bytes, or None."""
    with open(path, "rb") as f:
        head = f.read(16)
    for magic, kind in MAGIC.items():
        if head.startswith(magic):
            if kind == "webp" and head[8:12] != b"WEBP":
                continue
            return kind
    return None


def ensure_usable_image(path, workdir):
    """Validate a source image and normalise it to a real JPEG.

    Google accepts jpeg/png/webp, but a file can carry an image name while
    holding an HTML page (a saved login screen, a failed download). Sniffing the
    bytes catches that before a run burns minutes in Gemini.
    """
    if not os.path.exists(path):
        raise SystemExit(f"berkas tidak ada: {path}")
    kind = sniff_image(path)
    if kind is None:
        head = open(path, "rb").read(80).decode("utf-8", "replace")
        hint = ""
        if "<!doctype html" in head.lower() or "<html" in head.lower():
            hint = " (isinya halaman HTML, bukan gambar — unduhan gagal?)"
        raise SystemExit(f"{path} bukan gambar yang bisa dipakai{hint}")

    if kind == "jpeg":
        return path

    # conversion keeps Flow's ingredient upload predictable
    os.makedirs(workdir, exist_ok=True)
    base = os.path.splitext(os.path.basename(path))[0]
    out = os.path.join(workdir, f"{base}.jpg")
    r = sp.run(
        ["ffmpeg", "-y", "-i", path, "-frames:v", "1", "-q:v", "2", out],
        capture_output=True,
        text=True,
    )
    if r.returncode or not os.path.exists(out):
        raise SystemExit(f"gagal mengonversi {path} ke JPEG: {r.stderr[-200:]}")
    return out


def slugify(text):
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")[:60] or "project"


HASIL_DEFAULT = "/root/Projects"


def _hasil_induk(out=None):
    """Folder induk hasil video: --out, atau /root/Projects bila ada, atau cwd.

    Default /root/Projects disengaja: bahan tinggal di /root/Projects/Sources dan
    hasilnya di /root/Projects/<slug>/<slug>.mp4, jadi satu tempat untuk masuk
    dan keluar. Bila /root/Projects tidak ada (mis. mesin lain), pakai cwd.
    """
    if out:
        os.makedirs(out, exist_ok=True)
        return out
    if os.path.isdir(HASIL_DEFAULT):
        return HASIL_DEFAULT
    return os.getcwd()


def classify(product, model, seconds=flow_stage.SECONDS):
    """Jenis storyboard + prompt untuk kombinasi bahan dan durasi.

    `product` tidak dipakai untuk memilih template (yang menentukan hanya ada
    atau tidaknya foto model), tapi tetap ada di tanda tangan karena pemanggil
    meneruskan bahan secara berurutan.
    """
    kind = "product+model" if model else "product-only"
    return kind, prompts.render_prompt(
        kind, seconds if seconds in prompts.SPEC else flow_stage.SECONDS
    )


def main():
    """Catat exit code sebenarnya lalu jalankan alurnya.

    atexit tidak bisa melihat pengecualian yang sedang naik (exc_info sudah
    bersih saat handler jalan), jadi kode keluar dicatat di sini sebelum
    dilempar lagi — sumber.json tidak berbohong soal hasil run.
    """
    try:
        _jalankan()
    except SystemExit as e:
        _EXIT["kode"] = e.code if isinstance(e.code, int) else 1
        raise
    except BaseException:
        _EXIT["kode"] = 1
        raise


_EXIT = {"kode": 0}


def _jalankan():
    p = argparse.ArgumentParser(
        description="Bahan produk (+ model) -> storyboard Gemini -> video Flow"
    )
    p.add_argument("--product", required=True, help="foto produk")
    p.add_argument("--model", help="foto model (opsional)")
    p.add_argument(
        "--title",
        help="judul video; kosongkan agar AI isi dari konten storyboard",
    )
    p.add_argument("--slug", help="folder hasil; default dari --title")
    p.add_argument(
        "--out",
        default=None,
        help="folder induk hasil (default: /root/Projects, atau cwd bila "
        "/root/Projects tidak ada)",
    )
    p.add_argument(
        "--account",
        default=None,
        help="akun awal stage 1 & Flow (default: akun pertama yang terdaftar)",
    )
    p.add_argument("--timeout", type=int, default=900)
    p.add_argument(
        "--skip-gemini",
        action="store_true",
        help="pakai STORYBOARD.jpeg + FLOW.md yang sudah ada",
    )
    p.add_argument(
        "--skip-flow", action="store_true", help="berhenti setelah tahap Gemini"
    )
    p.add_argument(
        "--dry-run", action="store_true", help="Flow berhenti sebelum bayar kredit"
    )
    p.add_argument(
        "--vnc",
        action="store_true",
        help="mode uji: Chrome tampil di Xvfb + VNC agar proses bisa dilihat "
        "(default: headless)",
    )
    p.add_argument(
        "--mode",
        default=None,
        help='mode model Gemini, mis. "Auto" (default: biarkan setelan akun)',
    )
    p.add_argument(
        "--seconds",
        type=int,
        default=None,
        choices=(10, 13),
        help="paksa durasi video (default: auto — probe Flow memilih "
        "13 dtk bila ada akun yang sanggup, kalau tidak 10 dtk)",
    )
    args = p.parse_args()
    args.visible = args.vnc
    if not args.account:
        accounts = fc.list_accounts()
        if not accounts:
            raise SystemExit(
                "Tidak ada akun terdaftar. Daftarkan dulu: bits-login --add <email>"
            )
        args.account = accounts[0]["account"]

    # Folder sementara: setelah Gemini memberi judul, folder bisa diganti nama.
    provisional = args.slug or (
        slugify(args.title)
        if args.title
        else slugify(os.path.splitext(os.path.basename(args.product))[0])
    )
    out_dir = os.path.join(_hasil_induk(args.out), provisional)
    # Jangan menimpa proyek lama tanpa disengaja. Lanjutan (--skip-gemini /
    # --skip-flow / --dry-run) memang memakai artefak sebelumnya, jadi diizinkan;
    # mulai dari nol dengan slug yang sudah terisi harus ditolak.
    lanjutan = args.skip_gemini or args.skip_flow or args.dry_run
    if os.path.isdir(out_dir) and os.listdir(out_dir) and not lanjutan:
        raise SystemExit(
            f"{out_dir} sudah ada dan tidak kosong — pakai --slug lain, "
            f"atau tambahkan --skip-gemini untuk melanjutkan tahap Flow"
        )
    os.makedirs(out_dir, exist_ok=True)
    # Catat bahan yang dipakai apa pun hasilnya (sukses, dry-run, SystemExit),
    # supaya `bersihkan` punya bukti dan tidak menghapus bahan yang masih
    # dibutuhkan proyek lain.
    atexit.register(catat_sumber, out_dir, args)
    # publish.txt ikut ditulis sendiri: tanpa itu video affiliate tidak bisa
    # dimasukkan ke rencana Buffer tanpa kerja manual. Didaftarkan setelah
    # catat_sumber karena atexit berjalan LIFO — publish.txt perlu sumber.json
    # (bahan + waktu) yang sudah ada.
    atexit.register(catat_publish, out_dir, args)

    # Bahan dipakai LANGSUNG dari tempatnya (biasanya /root/Projects/Sources).
    # Tidak ada folder `src/`: konversi PNG/WebP ditulis ke folder sementara di
    # luar hasil, lalu dibuang — CEO tidak mau `src/` muncul di folder proyek.
    work = tempfile.mkdtemp(prefix="bits-affiliate-")
    # Dibersihkan saat proses selesai, termasuk pada SystemExit / error: folder
    # ini hanya tempat konversi sementara, tidak boleh tertinggal di hasil.
    atexit.register(shutil.rmtree, work, True)
    # Chrome/Xvfb/x11vnc sering tidak tertutup sendiri (run sukses pun bisa
    # meninggalkan belasan proses). Daftarkan sekali di awal supaya SETIAP jalan
    # keluar — sukses, --skip-flow, --dry-run, SystemExit, Ctrl-C — bersih.
    atexit.register(fc.cleanup_all)
    try:
        product = ensure_usable_image(args.product, work)
        model = ensure_usable_image(args.model, work) if args.model else None
    except BaseException:
        shutil.rmtree(work, ignore_errors=True)
        raise

    # ---- fase 0: probe Flow — ukur cap durasi + biaya per akun, lalu
    # pilih akun + durasi SEBELUM stage Gemini (template prompt ikut
    # durasi). Tanpa kredit terbakar: berhenti sebelum upload/prompt.
    caps_file = os.path.join(fc.HOME, "caps.json")
    caps = {}
    if os.path.exists(caps_file):
        caps = json.load(open(caps_file, encoding="utf-8"))

    if args.seconds or args.skip_gemini or args.skip_flow:
        # durasi dipaksa manual, atau artefak sudah ada (diasumsikan
        # versi 13 dtk): tidak ada keputusan durasi otomatis
        if not args.seconds:
            args.seconds = flow_stage.SECONDS
        biaya = flow_stage.COST  # konservatif; biaya final dibaca chip
    else:
        urut0 = [args.account] + [
            a["account"] for a in fc.list_accounts() if a["account"] != args.account
        ]
        saldo0 = {a: cc.account_balance(a) for a in urut0}
        urut0.sort(
            key=lambda a: saldo0[a][0] if saldo0[a][0] is not None else -1,
            reverse=True,
        )
        kandidat_durasi = []  # (rank 0=13 dtk, acc, sec, cost)
        for acc in urut0:
            bal = saldo0[acc][0]
            info = caps.get(acc)
            if info is None:
                if bal is not None and bal < flow_stage.COST_MIN:
                    continue  # biaya termurah pun tak terjangkau
                print(f"probe    : {acc} (ukur cap durasi + biaya)...", flush=True)
                try:
                    info = flow_stage.probe_account(acc)
                except flow_stage.FlowSetupError as e:
                    print(f"[probe] {acc}: {e} — dilewati", flush=True)
                    continue
                caps[acc] = info
                with open(caps_file, "w", encoding="utf-8") as fh:
                    json.dump(caps, fh)
            sec = info["seconds"]
            biaya_acc = info["cost"] or flow_stage.COST
            if bal is not None and bal < biaya_acc:
                print(
                    f"[kredit] {acc}: {bal} < {biaya_acc} — dilewati",
                    flush=True,
                )
                continue
            kandidat_durasi.append(
                (0 if sec == flow_stage.SECONDS else 1, acc, sec, biaya_acc)
            )
            if sec == flow_stage.SECONDS:
                break  # 13 dtk + kredit cukup = jawaban terbaik
        if not kandidat_durasi:
            raise SystemExit(
                "TIDAK ADA AKUN yang lolos probe Flow (cap durasi/kredit). "
                "Coba lagi setelah kredit reset, atau paksa --seconds."
            )
        _, args.account, args.seconds, biaya = min(kandidat_durasi)
        print(
            f"probe    : pilih {args.account} — {args.seconds} dtk, {biaya} kredit",
            flush=True,
        )

    kind, prompt = classify(product, model, args.seconds)

    storyboard = os.path.join(out_dir, "STORYBOARD.jpeg")
    flow_md = os.path.join(out_dir, "FLOW.md")

    print(f"akun    : {args.account}")
    print(f"jenis   : {kind}  (prompt {args.seconds} dtk, {len(prompt)} char)")
    print(f"durasi  : {args.seconds} dtk (biaya ~{biaya} kredit)")
    print(f"folder  : {out_dir}", flush=True)

    if args.visible:
        fc.ensure_vnc()
        print(f"vnc     : {fc.vnc_hint()}", flush=True)

    summary = {"account": args.account, "kind": kind, "dir": out_dir}

    # ---- stage 1 (rotasi akun: kuota gambar harian tiap akun terbatas)
    if args.skip_gemini:
        for f in (storyboard, flow_md):
            if not os.path.exists(f):
                raise SystemExit(f"--skip-gemini tapi {f} tidak ada")
        summary["gemini"] = "dilewati"
        g = {}
        print("gemini  : dilewati (pakai berkas yang ada)", flush=True)
    else:
        images = [product] + ([model] if model else [])
        urutan1 = [args.account] + [
            a["account"] for a in fc.list_accounts() if a["account"] != args.account
        ]
        MAX_AKUN_GEMINI = 3  # tiap percobaan bisa ~7-15 mnt (2 attempt di dalam)
        dicoba1, gagal1 = set(), []
        g = None
        akun_gemini = None
        for acc in urutan1:
            if len(dicoba1) >= MAX_AKUN_GEMINI:
                break
            dicoba1.add(acc)
            print(f"gemini  : {acc}", flush=True)
            try:
                g = gemini_stage.run_stage(
                    acc,
                    images,
                    prompt,
                    out_dir,
                    timeout=args.timeout,
                    visible=args.visible,
                    mode=args.mode,
                )
                akun_gemini = acc
                break
            except SystemExit as e:
                gagal1.append((acc, str(e)))
                print(
                    f"[gemini] {acc}: {str(e)[:140]} — lanjut akun lain",
                    file=sys.stderr,
                    flush=True,
                )
        if g is None:
            raise SystemExit(
                "SEMUA AKUN GAGAL di tahap Gemini "
                f"({', '.join(a for a, _ in gagal1)}). "
                "Kemungkinan kuota gambar harian tiap akun sudah habis — "
                "coba lagi besok."
            )
        summary["gemini_account"] = akun_gemini
        summary["gemini"] = g
        print(
            f"gemini  : {g['images_attached']} gambar, storyboard "
            f"{g['storyboard']['bytes'] // 1024} KB "
            f"({g['storyboard']['w']}x{g['storyboard']['h']}), "
            f"prompt {g['prompt_chars']} char",
            flush=True,
        )

    # ---- judul otomatis dari konten storyboard Gemini
    title = args.title
    if not title:
        hint = (g.get("title_hint") or "").strip()
        title = hint or os.path.splitext(os.path.basename(product))[0]
    # Flow prompt tidak boleh kehilangan detail model; judul cukup deskriptif.
    print(f"judul   : {title}", flush=True)

    final_slug = args.slug or slugify(title)
    if final_slug != provisional and not args.slug:
        new_dir = os.path.join(_hasil_induk(args.out), final_slug)
        if os.path.exists(new_dir):
            final_slug = f"{final_slug}-{int(time.time())}"
            new_dir = os.path.join(_hasil_induk(args.out), final_slug)
        os.rename(out_dir, new_dir)
        out_dir = new_dir
        print(f"folder  : diganti nama -> {out_dir}", flush=True)
        # summary masih menunjuk folder lama; perbarui supaya JSON akhir benar
        if g.get("storyboard", {}).get("path"):
            g["storyboard"]["path"] = os.path.join(out_dir, "STORYBOARD.jpeg")
        if g.get("flow_md"):
            g["flow_md"] = os.path.join(out_dir, "FLOW.md")

    storyboard = os.path.join(out_dir, "STORYBOARD.jpeg")
    flow_md = os.path.join(out_dir, "FLOW.md")
    # nama berkas dari slug (bukan judul mentah: bisa memuat "/" atau ":")
    video = os.path.join(out_dir, f"{final_slug}.mp4")
    summary["dir"] = out_dir

    # ---- stage 2: pilih akun yang cukup kredit, auto-switch bila kurang
    if args.skip_flow:
        summary["title"] = title
        print("flow    : dilewati (--skip-flow)")
        print(json.dumps(summary, indent=1, default=str))
        return

    urutan = []  # akun yang sudah dicek saldonya
    gagal = []  # (akun, alasan)
    dicoba = set()
    saldo = {}  # acc -> (bal, reset, note); cek sekali, jangan spin Chrome ulang

    def kandidat_berikutnya():
        for acc in urutan:
            if acc in dicoba:
                continue
            info = caps.get(acc)
            if info and info["seconds"] < args.seconds:
                print(
                    f"[durasi] {acc}: cap {info['seconds']} dtk "
                    f"< {args.seconds} dtk — dilewati",
                    flush=True,
                )
                continue
            if acc not in saldo:
                saldo[acc] = cc.account_balance(acc)
            bal, reset, note = saldo[acc]
            if bal is None:
                print(f"[kredit] {acc}: {note} — dilewati", flush=True)
                continue
            if bal >= biaya:
                return acc, bal, reset
            print(
                f"[kredit] {acc}: {bal} kredit < {biaya} "
                f"(reset {reset or '?'}) — dilewati",
                flush=True,
            )
        return None

    # akun yang diminta dicek duluan, sisanya mengikuti
    urutan.append(args.account)
    urutan += [a["account"] for a in fc.list_accounts() if a["account"] != args.account]

    attempt = kandidat_berikutnya()
    if not attempt:
        raise SystemExit(
            "\nSEMUA AKUN KREDIT HABIS.\n"
            f"Butuh {biaya} kredit per video (Omni 1.2, {args.seconds} dtk, "
            "720p, x1).\n"
            "Kredit reset bulanan — tunggu tanggal reset, atau upgrade paket "
            "Google AI pada akun yang diinginkan."
        )

    f = None
    while attempt:
        acc, bal, reset = attempt
        dicoba.add(acc)
        print(f"flow    : {acc} ({bal} kredit)", flush=True)
        try:
            f = flow_stage.run_stage(
                acc,
                storyboard,
                flow_md,
                video,
                dry_run=args.dry_run,
                timeout=args.timeout,
                visible=args.visible,
                downloads_dir=DL_DIR,
                seconds=args.seconds,
            )
            summary["account"] = acc  # yang benar-benar membayar
            break
        except flow_stage.FlowSetupError as e:
            gagal.append((acc, str(e)))
            print(
                f"[flow-setup] {acc}: {e} — coba akun lain (belum ada kredit terbakar)",
                flush=True,
            )
            attempt = kandidat_berikutnya()
            continue
        except SystemExit as e:
            msg = str(e)
            if "kredit" in msg.lower():
                gagal.append((acc, msg))
                print(f"[kredit] {acc} ditolak Flow: {msg}", flush=True)
                attempt = kandidat_berikutnya()
                continue
            raise
        except (
            ConnectionError,
            TimeoutError,
            asyncio.TimeoutError,
            fc.ConnectionClosed,
        ) as e:
            print(f"[jaringan] {acc}: {e} — coba akun lain", flush=True)
            gagal.append((acc, str(e)))
            attempt = kandidat_berikutnya()
            continue

    if not f:
        raise SystemExit(
            "SEMUA AKUN GAGAL membuat video. Detail di atas. "
            "Cek jaringan/login, atau tunggu reset kredit."
        )

    summary["flow"] = f
    summary["title"] = title
    print(f"setelan : {f['settings']['chip']}  |  {f['settings']['cost']}", flush=True)
    print(f"bahan   : {f['chips']} chip menempel", flush=True)

    if args.dry_run:
        print("dry-run : berhenti sebelum generate", flush=True)
    else:
        v = f["video"]
        print(f"video   : {v['probe']}", flush=True)
        print(f"tersimpan: {v['path']}", flush=True)

    print(json.dumps(summary, indent=1, default=str))


def catat_publish(out_dir, args=None):
    """Tulis `<proyek>/publish.txt` — caption siap Buffer (3 blok platform).

    Dijalankan otomatis pada akhir setiap run lewat `atexit`, jadi video affiliate
    langsung bisa masuk `buffer-rencana-penuh.py` seperti video BITS lain.
    Gagal menulis tidak boleh menggagalkan run: dicetak sebagai peringatan saja.
    """
    try:
        detik = getattr(args, "seconds", None) if args is not None else None
        path = publish.dari_proyek(out_dir, detik=detik)
        print(f"publish : {path}", flush=True)
        return path
    except Exception as e:  # noqa: BLE001 — jangan gagalkan run karena caption
        print(f"[publish] peringatan: gagal menulis publish.txt: {e}", flush=True)
        return None


def catat_sumber(out_dir, args, kode=None):
    """Tulis `<proyek>/sumber.json` — bukti bahan mana yang dipakai.

    Dulu ini tugas pembungkus `affiliate-jalan.py` di luar repo. Sekarang masuk
    pipeline supaya skill sendiri yang mencatat, dan `bits-sources
    bersihkan` bisa memutuskan bahan mana yang aman dibuang tanpa menebak dari
    nama folder.

    Isinya: slug, waktu, berkas bahan (basename), perintah, dan exit code.
    `kode` parameter tetap ada untuk pemanggilan manual; lewat `atexit` nilainya
    diambil dari `_EXIT` yang diisi `main()` (exc_info sudah kosong saat
    handler atexit berjalan, jadi tidak bisa dibaca langsung di sana).
    """
    if kode is None:
        kode = _EXIT["kode"]
    if not out_dir or not os.path.isdir(out_dir):
        return None
    bahan = [os.path.basename(args.product)]
    if getattr(args, "model", None):
        b = os.path.basename(args.model)
        if b not in bahan:
            bahan.append(b)
    catatan = {
        "slug": os.path.basename(out_dir.rstrip("/")),
        "waktu": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "berkas": bahan,
        "perintah": " ".join(sys.argv),
        "exit": kode,
    }
    path = os.path.join(out_dir, "sumber.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(catatan, f, indent=1, ensure_ascii=False)
    return path


if __name__ == "__main__":
    main()
