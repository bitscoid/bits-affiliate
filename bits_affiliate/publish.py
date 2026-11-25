"""Tulis `publish.txt` untuk proyek affiliate — siap masuk rencana Buffer.

Kenapa ada di paket skill, bukan skrip lepas: setiap video affiliate yang selesai
harus bisa dijadwalkan tanpa kerja manual, jadi pencatatannya bagian dari pipeline
(bukan tugas pembungkus luar). Dipanggil pipeline lewat `atexit` bersama
`sumber.json`, dan bisa dijalankan ulang sendiri bila caption perlu diganti.

Bentuk berkasnya sama dengan publish.txt video BITS lain (folder proyek video):
header 6 baris `# `, lalu blok `=== TikTok`, `=== Instagram Reels`,
`=== YouTube Shorts`. Dengan begitu `buffer-rencana-penuh.py` dan skrip Buffer
lain membacanya tanpa perubahan.

Naskah VO dipakai sebagai bahan caption supaya janji video tidak melenceng dari
yang diucapkan. Produk/tema diambil dari `sumber.json` + nama berkas bahan.
"""
import json
import os
import re
import subprocess
from datetime import datetime

# Tagar per tema. Dicocokkan ke nama berkas/slug produk; yang tidak cocok
# jatuh ke `umum` supaya caption tetap terbit (bukan gagal senyap).
TEMA = {
    "parfum": (["parfum", "wangi", "fragrance", "scent"],
               ["parfum", "wangi", "fragrance", "beautyreviews",
                "rekomendasiparfum", "affiliate"]),
    "perawatan": (["skincare", "serum", "sabun", "lotion", "krim", "pelembab"],
                  ["skincare", "perawatankulit", "beautyreviews",
                   "reviewproduk", "affiliate"]),
    "fashion": (["baju", "hijab", "kaos", "celana", "tas", "sepatu", "sandal"],
                ["fashion", "ootd", "rekomendasifashion", "reviewproduk",
                 "affiliate"]),
    "rumah": (["dapur", "kompor", "kulkas", "rak", "sapu", "lampu", "rumah"],
              ["peralatanrumah", "tipsrumah", "reviewproduk", "rumah tangga",
               "affiliate"]),
    "gadget": (["hp", "charger", "kabel", "headset", "earphone", "laptop", "mouse"],
               ["gadget", "aksesorishp", "reviewproduk", "tipsgadget", "affiliate"]),
    "umum": ([], ["reviewproduk", "rekomendasi", "affiliate", "belanjaonline",
                  "unboxing"]),
}


def _tema(teks: str):
    t = (teks or "").lower()
    for nama, (kunci, tag) in TEMA.items():
        if any(k in t for k in kunci):
            return nama, tag
    return "umum", TEMA["umum"][1]


def _baca(path, default=""):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return default


def _kalimat(teks: str):
    """Pecah naskah jadi kalimat utuh, buang yang terlalu pendek/menggantung."""
    if not teks:
        return []
    bersih = re.sub(r"\s+", " ", teks).strip()
    bagian = re.split(r"(?<=[.!?])\s+", bersih)
    return [b.strip() for b in bagian if len(b.strip()) > 12]


def _judul(naskah: str, produk: str, slug: str) -> str:
    """Judul = kalimat penutup naskah (biasanya berisi nama produk), atau slug."""
    kal = _kalimat(naskah)
    if kal:
        j = kal[-1].rstrip(".!?")
        if 12 <= len(j) <= 70:
            return j
    nama = os.path.splitext(os.path.basename(produk or ""))[0] if produk else ""
    nama = nama.replace("-", " ").replace("_", " ").strip()
    return (nama or slug.replace("-", " ")).title()


def _durasi(video, detik=None):
    """Durasi dari ffprobe; kalau gagal pakai nilai yang diberikan.

    `video` boleh path absolut atau relatif terhadap CWD pemanggil — coba
    keduanya, karena publish.txt menyimpan path relatif proyek.
    """
    if detik:
        return f"{float(detik):.1f}"
    if not video:
        return "0.0"
    for kandidat in (video, os.path.join(os.getcwd(), video)):
        try:
            out = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                 "-of", "csv=p=0", kandidat],
                capture_output=True, text=True, timeout=30,
            ).stdout.strip()
            if out:
                return f"{float(out):.1f}"
        except (OSError, ValueError, subprocess.SubprocessError):
            continue
    return "0.0"


def bangun(slug: str, produk: str = "", naskah: str = "", detik=None,
           channel: str = "BITS Style", status: str = "SIAP",
           video=None, sumber=None) -> str:
    """Hasilkan isi publish.txt (string). Tidak menulis apa pun."""
    naskah = naskah or ""
    kal = _kalimat(naskah)
    tema, tagar = _tema(" ".join([slug, produk or "", naskah[:200]]))

    judul = _judul(naskah, produk, slug)

    # Deskripsi: dua kalimat pertama naskah (isi video), lalu satu kalimat
    # ajakan generik. Dijaga 30–45 kata seperti caption BITS lain.
    inti = " ".join(kal[:3]) if kal else f"Video singkat tentang {judul.lower()}."
    inti = re.sub(r"\s+", " ", inti).strip()
    kata = inti.split()
    if len(kata) > 42:
        inti = " ".join(kata[:42]).rstrip(",;:") + "."
    penutup = "Cek detailnya di video, lalu simpan kalau berguna."
    desk = f"{inti} {penutup}".strip()

    tag = " ".join("#" + t.replace(" ", "") for t in tagar)

    if not video:
        video = f"{slug}/{slug}.mp4"

    sumber = sumber or {}
    bahan = ", ".join(sumber.get("berkas") or []) or (os.path.basename(produk) if produk else "-")
    waktu = sumber.get("waktu") or datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    return f"""# PROYEK : {slug}
# VIDEO  : {video}
# CHANNEL: {channel}
# DURASI : {_durasi(video, detik)}
# STATUS : {status}
# UPDATE : {datetime.now().strftime('%Y-%m-%d')} (publish.txt dibuat pipeline bits-affiliate)

=== TikTok
{desk}

{tag}

=== Instagram Reels
{desk}

{tag}

=== YouTube Shorts
Judul: {judul}
{desk}

{tag} #Shorts

CATATAN
- Tema terdeteksi: {tema}. Tagar mengikuti tema; ganti bila produk sebenarnya lain.
- Judul diambil dari kalimat penutup naskah VO, supaya caption tidak menjanjikan hal yang tidak diucapkan video.
- Bahan: {bahan}. Proyek dibuat {waktu}.
- Caption ini untuk video affiliate. Nominal harga dan klaim harga tidak ditulis (aturan BITS): CTA memakai "Cek detailnya di video".
"""


def tulis(out_dir: str, **kw) -> str:
    """Bangun lalu tulis `<out_dir>/output/publish.txt` (struktur standar BITS)."""
    kw.setdefault("slug", os.path.basename(os.path.abspath(out_dir).rstrip("/")))
    isi = bangun(**kw)
    os.makedirs(os.path.join(out_dir, "output"), exist_ok=True)
    path = os.path.join(out_dir, "output", "publish.txt")
    with open(path, "w", encoding="utf-8") as f:
        f.write(isi)
    return path


def dari_proyek(out_dir: str, channel: str = "BITS Style", detik=None,
                status: str = "SIAP") -> str:
    """Cara pakai paling ringkas: baca `sumber.json` + `script.txt` di proyek.

    Ini yang dipanggil pipeline pada `atexit`, jadi setiap video affiliate
    langsung punya publish.txt tanpa langkah tambahan. Hanya mengenal struktur
    standar: `script.txt` dan `output/<slug>.mp4`. Tidak ada lokasi cadangan —
    proyek yang menyimpang harus dirapikan, bukan diakomodasi.
    """
    out_dir = os.path.abspath(out_dir)  # "." -> path absolut, cegah slug "."
    slug = os.path.basename(out_dir.rstrip("/"))
    sumber = {}
    try:
        with open(os.path.join(out_dir, "sumber.json"), encoding="utf-8") as f:
            sumber = json.load(f)
    except (OSError, ValueError):
        pass
    naskah = _baca(os.path.join(out_dir, "script.txt"))
    bahan = (sumber.get("berkas") or [None])[0] if sumber else None
    # judul dari sumber.json menang atas nama berkas bahan
    produk = (sumber.get("judul") if sumber else None) or bahan or ""
    out_video = os.path.join(out_dir, "output")
    nama_mp4 = f"{slug}.mp4"
    if not os.path.exists(os.path.join(out_video, nama_mp4)):
        # nama video bisa beda dari slug; pakai mp4 apa pun di output/
        lain = [x for x in sorted(os.listdir(out_video)) if x.endswith(".mp4")] \
            if os.path.isdir(out_video) else []
        if lain:
            nama_mp4 = lain[0]
    video = f"{slug}/output/{nama_mp4}"
    # Video belum ada (mis. run berhenti sebelum Flow) JANGAN ditandai SIAP:
    # rencana Buffer akan menjadwalkan berkas yang tidak ada dan post 404.
    if not os.path.exists(os.path.join(out_video, nama_mp4)):
        status = "BELUM-ADA-VIDEO"
    if detik is None:
        detik = _durasi(os.path.join(out_video, nama_mp4))
    return tulis(out_dir, slug=slug, produk=produk, naskah=naskah,
                 detik=detik, channel=channel, status=status,
                 video=video, sumber=sumber)
