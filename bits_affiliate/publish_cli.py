"""CLI `bits-publish` — tulis ulang publish.txt proyek affiliate tanpa render ulang.

Dipakai saat caption perlu diganti (channel, status, tema salah) setelah video
sudah jadi. Tidak menyentuh mp4, tidak memanggil Flow, nol kredit.
"""
import argparse
import os
import sys

from . import publish as pub

DEFAULT_ROOT = os.getcwd()


def main():
    ap = argparse.ArgumentParser(
        prog="bits-publish",
        description="Tulis ulang publish.txt untuk proyek affiliate.",
    )
    ap.add_argument("slug", help="nama folder proyek (atau path lengkapnya)")
    ap.add_argument("--out", default=DEFAULT_ROOT,
                    help="folder induk proyek (default: current directory)")
    ap.add_argument("--channel", default="BITS Style",
                    help="header # CHANNEL (default: BITS Style)")
    ap.add_argument("--status", default=None,
                    help="header # STATUS (default: SIAP bila mp4 ada, "
                         "BELUM-ADA-VIDEO bila belum)")
    ap.add_argument("--seconds", type=int, default=None,
                    help="paksa durasi (default: probe file mp4)")
    a = ap.parse_args()

    out_dir = a.slug if os.path.isdir(a.slug) else os.path.join(a.out, a.slug)
    if not os.path.isdir(out_dir):
        sys.exit(f"folder proyek tidak ada: {out_dir}")

    kw = {}
    if a.status:
        kw["status"] = a.status
    path = pub.dari_proyek(out_dir, channel=a.channel, detik=a.seconds, **kw)
    print(path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
