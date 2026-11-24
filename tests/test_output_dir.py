"""Folder induk hasil: --out menang, lalu BITS_AFFILIATE_OUT, lalu current directory.

Kunci perilaku portabel: tidak ada path statis di kode, tapi instalasi yang punya
satu root proyek tetap bisa menetapkan lewat variabel lingkungan.
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bits_affiliate import pipeline  # noqa: E402


def cek(nama, syarat, detail=""):
    status = "OK  " if syarat else "GAGAL"
    print(f"  [{status}] {nama}" + (f" — {detail}" if detail and not syarat else ""))
    return bool(syarat)


def main():
    lulus = total = 0
    asal = os.getcwd()
    simpan = os.environ.get("BITS_AFFILIATE_OUT")
    try:
        with tempfile.TemporaryDirectory() as tmp:
            kerja = os.path.join(tmp, "kerja")
            os.makedirs(kerja)
            os.chdir(kerja)

            os.environ.pop("BITS_AFFILIATE_OUT", None)
            total += 1
            lulus += cek(
                "tanpa env & tanpa --out -> current directory",
                os.path.realpath(pipeline._hasil_induk()) == os.path.realpath(kerja),
                pipeline._hasil_induk(),
            )

            root = os.path.join(tmp, "root-proyek")
            os.environ["BITS_AFFILIATE_OUT"] = root
            total += 1
            lulus += cek(
                "env BITS_AFFILIATE_OUT dipakai",
                os.path.realpath(pipeline._hasil_induk()) == os.path.realpath(root),
                pipeline._hasil_induk(),
            )
            total += 1
            lulus += cek("folder dari env dibuat", os.path.isdir(root))

            lain = os.path.join(tmp, "lain")
            total += 1
            lulus += cek(
                "--out menang atas env",
                os.path.realpath(pipeline._hasil_induk(lain)) == os.path.realpath(lain),
                pipeline._hasil_induk(lain),
            )

            os.environ.pop("BITS_AFFILIATE_OUT", None)
            total += 1
            lulus += cek(
                "env dihapus -> kembali ke current directory",
                os.path.realpath(pipeline._hasil_induk()) == os.path.realpath(kerja),
            )
    finally:
        os.chdir(asal)
        if simpan is None:
            os.environ.pop("BITS_AFFILIATE_OUT", None)
        else:
            os.environ["BITS_AFFILIATE_OUT"] = simpan

    print(f"\n{lulus}/{total} lulus")
    return 0 if lulus == total else 1


if __name__ == "__main__":
    sys.exit(main())