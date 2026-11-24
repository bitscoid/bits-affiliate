"""publish.txt untuk proyek affiliate: bentuk header, tagar per tema, status jujur."""

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bits_affiliate import publish  # noqa: E402


def _proyek(
    tmp,
    slug="demo-parfum-13s",
    naskah="Kenali wanginya dulu. Semprotan halus. Nyaman sepanjang "
    "hari. Parfum ini pilihan harianmu.",
    mp4=True,
    bahan="produk-parfum.jpg",
):
    d = os.path.join(tmp, slug)
    os.makedirs(os.path.join(d, "output"), exist_ok=True)
    with open(os.path.join(d, "script.txt"), "w") as f:
        f.write(naskah)
    with open(os.path.join(d, "sumber.json"), "w") as f:
        json.dump(
            {
                "slug": slug,
                "waktu": "2026-11-23 15:00:00",
                "berkas": [bahan],
                "perintah": "bits-affiliate",
                "exit": 0,
            },
            f,
        )
    if mp4:
        with open(os.path.join(d, "output", f"{slug}.mp4"), "wb") as f:
            f.write(b"\x00" * 1024)
    return d


def cek(nama, syarat, detail=""):
    status = "OK  " if syarat else "GAGAL"
    print(f"  [{status}] {nama}" + (f" — {detail}" if detail and not syarat else ""))
    return bool(syarat)


def main():
    lulus = total = 0
    with tempfile.TemporaryDirectory() as tmp:
        # 1. header 6 baris, berawalan "# "
        d = _proyek(tmp)
        isi = publish.dari_proyek(d)
        teks = open(isi).read()
        header = [line for line in teks.splitlines() if line.startswith("# ")]
        total += 1
        lulus += cek("header 6 baris", len(header) == 6, f"dapat {len(header)}")
        total += 1
        lulus += cek(
            "header diawali '# '", all(line.startswith("# ") for line in header)
        )
        total += 1
        lulus += cek(
            "blok platform lengkap",
            all(
                b in teks
                for b in ("=== TikTok", "=== Instagram Reels", "=== YouTube Shorts")
            ),
        )

        # 2. nama proyek & video benar (bukan path relatif ".")
        total += 1
        lulus += cek("PROYEK = nama folder", "# PROYEK : demo-parfum-13s" in teks)
        total += 1
        lulus += cek(
            "VIDEO menunjuk mp4 di output/",
            "demo-parfum-13s/output/demo-parfum-13s.mp4" in teks,
        )

        # 3. tema parfum -> tagar parfum, bukan umum
        total += 1
        lulus += cek("tagar tema parfum", "#parfum" in teks, teks[:200])
        total += 1
        lulus += cek("tagar tidak jatuh ke umum", "#unboxing" not in teks)

        # 4. tidak ada nominal harga / CTA terlarang
        total += 1
        lulus += cek("tanpa 'Rp'", "Rp" not in teks.replace("Reels", ""))
        total += 1
        lulus += cek("tanpa CTA 'Hubungi kami'", "hubungi kami" not in teks.lower())
        total += 1
        lulus += cek("tanpa disclosure AI", "bantuan ai" not in teks.lower())

        # 5. YouTube punya baris Judul
        total += 1
        lulus += cek(
            "YouTube ada baris Judul:", "Judul:" in teks.split("=== YouTube Shorts")[1]
        )

        # 6. tema lain terdeteksi
        d2 = _proyek(
            tmp,
            slug="demo-skincare-13s",
            bahan="produk-serum.jpg",
            naskah="Kulit kusam bukan soal malas. Serum membantu menutrisi.",
        )
        t2 = open(publish.dari_proyek(d2)).read()
        blok2 = t2.split("=== TikTok")[1].split("\n\n")[1]
        total += 1
        lulus += cek("tema skincare", "#skincare" in blok2, blok2[:80])

        # 7. tanpa naskah tetap terbit, tidak crash
        d3 = _proyek(tmp, slug="demo-kosong-13s", naskah="")
        t3 = open(publish.dari_proyek(d3)).read()
        total += 1
        lulus += cek(
            "tanpa naskah tetap punya deskripsi", "=== TikTok" in t3 and len(t3) > 200
        )

        # 8. video belum ada -> status jujur, bukan SIAP
        d4 = _proyek(tmp, slug="demo-tanpavideo-13s", mp4=False)
        t4 = open(publish.dari_proyek(d4)).read()
        total += 1
        lulus += cek("tanpa mp4 -> BELUM-ADA-VIDEO", "STATUS : BELUM-ADA-VIDEO" in t4)
        d5 = _proyek(tmp, slug="demo-advideo-13s", mp4=True)
        t5 = open(publish.dari_proyek(d5)).read()
        total += 1
        lulus += cek("ada mp4 -> SIAP", "STATUS : SIAP" in t5)

        # 8b. lokasi berkas sesuai struktur standar BITS
        total += 1
        lulus += cek(
            "publish.txt ditulis ke output/",
            os.path.exists(os.path.join(d, "output", "publish.txt")),
        )
        total += 1
        lulus += cek(
            "naskah dibaca dari script.txt", "Kenali wanginya" in teks
        )

        # 9. deskripsi 30-45 kata (aturan caption BITS)
        d6 = _proyek(
            tmp,
            slug="demo-panjang-13s",
            naskah=". ".join(["Kalimat contoh yang cukup panjang untuk diuji."] * 8)
            + ".",
        )
        t6 = open(publish.dari_proyek(d6)).read()
        blok = t6.split("=== TikTok")[1].split("\n\n")[0].strip()
        n = len(blok.split())
        total += 1
        lulus += cek("deskripsi <= 45 kata", n <= 45, f"{n} kata")

    print()
    print(f"{lulus}/{total} lulus")
    return 0 if lulus == total else 1


if __name__ == "__main__":
    sys.exit(main())
