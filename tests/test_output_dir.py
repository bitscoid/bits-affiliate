"""Folder induk hasil: --out kalau diberikan, kalau tidak current directory.

Kunci perilaku portabel: tidak ada path statis di kode, dan tidak ada variabel
lingkungan yang menyembunyikan folder kerja. Pemanggil yang menentukan.
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
    try:
        with tempfile.TemporaryDirectory() as tmp:
            kerja = os.path.join(tmp, "kerja")
            os.makedirs(kerja)
            os.chdir(kerja)

            total += 1
            lulus += cek(
                "tanpa --out -> current directory",
                os.path.realpath(pipeline._hasil_induk()) == os.path.realpath(kerja),
                pipeline._hasil_induk(),
            )

            lain = os.path.join(tmp, "lain")
            total += 1
            lulus += cek(
                "--out dipakai kalau diberikan",
                os.path.realpath(pipeline._hasil_induk(lain)) == os.path.realpath(lain),
                pipeline._hasil_induk(lain),
            )
            total += 1
            lulus += cek("folder --out dibuat", os.path.isdir(lain))

            os.environ["BITS_AFFILIATE_OUT"] = "/tmp/tidak-boleh-dipakai"
            total += 1
            lulus += cek(
                "variabel lingkungan tidak lagi mempengaruhi",
                os.path.realpath(pipeline._hasil_induk()) == os.path.realpath(kerja),
                pipeline._hasil_induk(),
            )
            os.environ.pop("BITS_AFFILIATE_OUT", None)
    finally:
        os.chdir(asal)
        os.environ.pop("BITS_AFFILIATE_OUT", None)

    print(f"\n{lulus}/{total} lulus")
    return 0 if lulus == total else 1


if __name__ == "__main__":
    sys.exit(main())
