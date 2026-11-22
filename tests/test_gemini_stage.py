"""Uji unit gemini_stage tanpa Chrome: pengambilan storyboard + metadata.

python3 tests/test_gemini_stage.py
"""

import base64
import os
import tempfile

from bits_affiliate import gemini_stage as gs


class FakeCDP:
    """Hanya bagian yang dipakai _grab_image: eval/tap/cmd."""

    def __init__(self, payload_b64: str | None = None):
        self.payload = payload_b64
        self.tapped = []

    async def eval(self, script, *a, **k):
        if "fetch(" in script:
            return self.payload
        return "{}"

    async def tap(self, x, y):
        self.tapped.append((x, y))

    async def cmd(self, *a, **k):
        return {}


def _cand(nw=1080, nh=1920, src="blob:https://gemini/x", rw=708, rh=395):
    return {"src": src, "nw": nw, "nh": nh, "rw": rw, "rh": rh}


async def _run_download_button(tmp):
    """Tombol download menghasilkan file di folder downloads."""
    dl = os.path.join(tmp, "downloads")
    os.makedirs(dl, exist_ok=True)
    out = os.path.join(tmp, "STORYBOARD.jpeg")

    async def fakeshot(c):
        return [_cand()]

    async def fake_dl(c, src=None):
        # tiru Chrome: file baru muncul SETELAH folder sudah dipindai
        with open(os.path.join(dl, "Gemini.png"), "wb") as f:
            f.write(b"\x89PNG\r\n\x1a\n" + b"0" * 30000)
        return True

    asli = (gs._storyboard_images, gs._click_download)
    gs._storyboard_images, gs._click_download = fakeshot, fake_dl
    try:
        return await gs._grab_image(FakeCDP(), out, downloads_dir=dl)
    finally:
        gs._storyboard_images, gs._click_download = asli


async def _run_blob_fetch(tmp):
    """Fallback: b64 hasil fetch(src) ditulis ke out_path."""
    raw = b"\xff\xd8\xff" + b"1" * 25000
    out = os.path.join(tmp, "STORYBOARD.jpeg")

    async def fakeshot(c):
        return [_cand(1600, 900)]

    async def fake_dl(c, src=None):
        return False

    asli = (gs._storyboard_images, gs._click_download)
    gs._storyboard_images, gs._click_download = fakeshot, fake_dl
    try:
        return await gs._grab_image(
            FakeCDP(base64.b64encode(raw).decode()), out, downloads_dir=tmp
        )
    finally:
        gs._storyboard_images, gs._click_download = asli


def test_download_button_returns_size():
    import asyncio

    with tempfile.TemporaryDirectory() as tmp:
        r = asyncio.run(_run_download_button(tmp))
        assert r["via"] == "download button", r
        assert (r["w"], r["h"]) == (1080, 1920), r  # pipeline mencetak w x h
        assert r["bytes"] > 20000
        assert os.path.exists(r["path"])


def test_blob_fetch_returns_size():
    import asyncio

    with tempfile.TemporaryDirectory() as tmp:
        r = asyncio.run(_run_blob_fetch(tmp))
    assert r["via"] == "blob fetch", r
    assert (r["w"], r["h"]) == (1600, 900), r
    assert r["bytes"] == 25003


def test_click_download_targets_requested_src():
    """Tombol harus dicari di wadah gambar `src`, bukan tombol global pertama."""
    import asyncio

    class C(FakeCDP):
        def __init__(self):
            super().__init__()
            self.hover = None

        async def cmd(self, cmd, **kw):
            if cmd == "Input.dispatchMouseEvent":
                self.hover = (kw["x"], kw["y"])
            return {}

    c = C()
    dipanggil = []

    async def fake_eval(script, *a, **k):
        dipanggil.append(script)
        if "getBoundingClientRect" in script and "mouse" not in script:
            return {"x": 10, "y": 20}
        return None

    c.eval = fake_eval
    asyncio.run(gs._click_download(c, "blob:target"))
    gabung = "\n".join(dipanggil)
    assert "blob:target" in gabung, "src tidak dipakai saat mencari gambar"
    assert c.hover == (10, 20), c.hover


def test_newest_download_skips_partial_and_vanished(tmp_path=None):
    """File .crdownload, file kecil, dan file lama diabaikan."""
    with tempfile.TemporaryDirectory() as tmp:
        lama = os.path.join(tmp, "lama.png")
        open(lama, "wb").write(b"x" * 90000)
        os.utime(lama, (1000, 1000))  # mtime jauh di masa lalu
        open(os.path.join(tmp, "icon.png"), "wb").write(b"x" * 100)
        open(os.path.join(tmp, "big.png.crdownload"), "wb").write(b"x" * 90000)
        besar = os.path.join(tmp, "story.png")
        open(besar, "wb").write(b"x" * 90000)
        assert gs._newest_download(tmp, 2000) == besar
        # tidak ada file sejak waktu yang diminta
        assert gs._newest_download(tmp, 1e12) is None


def test_newest_download_accepts_chrome_suffix(tmp_path=None):
    """Nama sama -> Chrome menulis ' (1)'. Filter berbasis nama akan melewatkannya."""
    with tempfile.TemporaryDirectory() as tmp:
        for name in (
            "Gemini_Generated_Image_x.jpeg",
            "Gemini_Generated_Image_x (1).jpeg",
        ):
            open(os.path.join(tmp, name), "wb").write(b"x" * 90000)
        hasil = gs._newest_download(tmp, 0)
        assert hasil is not None, "file hasil unduhan ulang tidak terdeteksi"


def test_storyboard_filter_ignores_small_render():
    """Foto sumber 1024x1024 tampil 112 px; storyboard 708 px -> pilih storyboard."""
    import asyncio

    class C(FakeCDP):
        async def eval(self, script, *a, **k):
            return None

    # filter terjadi di JS; yang diuji di sini hanya kontrak: kandidat terkecil
    # yang dirender (112 px) bukan storyboard, jadi ambang 200 px memisahkannya
    assert gs.MIN_RENDER_PX == 200
    assert 112 < gs.MIN_RENDER_PX < 708
    asyncio.run(C().eval("1"))


def test_pick_mode_already_active_skips():
    """Pemilih bilang 'saat ini Auto' -> True tanpa menyentuh menu."""
    import asyncio

    class C(FakeCDP):
        async def eval(self, script, *a, **k):
            if "picked" in script:
                return "picked"
            if "click" in script:
                return ""
            return "Buka pemilih mode, saat ini Auto"

    assert asyncio.run(gs._pick_mode(C(), "Auto")) is True


def test_pick_mode_switches_when_different():
    """Mode akun Hydrogen -> klik pemilih, pilih item Auto."""
    import asyncio

    class C(FakeCDP):
        async def eval(self, script, *a, **k):
            if "picked" in script:
                return "picked"
            if "click" in script:
                return ""
            return "Buka pemilih mode, saat ini Hydrogen"

    asli = gs.TOOL_POLL
    gs.TOOL_POLL = 0
    try:
        assert asyncio.run(gs._pick_mode(C(), "Auto")) is True
    finally:
        gs.TOOL_POLL = asli


def test_grab_title_hint_rejects_placeholder():
    """JS mengambil kecocokan terakhir; Python menolak placeholder/template."""
    import asyncio

    class C(FakeCDP):
        def __init__(self, hasil):
            self.hasil = hasil

        async def eval(self, script, *a, **k):
            return self.hasil

    # judul asli dari balasan -> diterima
    asli = asyncio.run(gs._grab_title_hint(C("Abaya Elegan Hitam")))
    assert asli == "Abaya Elegan Hitam", asli
    # placeholder dari echo prompt -> ditolak, pipeline jatuh ke nama berkas
    assert asyncio.run(gs._grab_title_hint(C("[product name]`."))) == ""
    # template {..} -> ditolak
    assert asyncio.run(gs._grab_title_hint(C("{nama produk}"))) == ""


def _wait_with(states, timeout=600):
    """Jalankan _wait_reply dengan urutan keadaan palsu; kembalikan exception."""
    import asyncio

    class C(FakeCDP):
        pass

    class StatesHabis(BaseException):
        """State palsu habis: gagal TEST, bukan ditelan `except Exception`.

        _reply_state menelan Exception (CDP putus). Kalau kehabisan state
        juga menelan, _wait_reply loop abadi saat REPLY_POLL=0 -- sudah
        pernah menggantung session shell. BaseException lolos dari penangkap.
        """

    c = C()
    urut = iter(states)

    async def fake_eval(script, *a, **k):
        try:
            return __import__("json").dumps(next(urut))
        except StopIteration:
            raise StatesHabis(
                "daftar state habis sebelum _wait_reply selesai"
            ) from None

    c.eval = fake_eval
    asli = gs.REPLY_POLL
    gs.REPLY_POLL = 0  # jangan benar-benar tidur
    try:
        return asyncio.run(gs._wait_reply(c, timeout=timeout))
    finally:
        gs.REPLY_POLL = asli


def test_wait_reply_returns_when_stable():
    states = [{"len": 900, "busy": True}] * 2 + [{"len": 900, "busy": False}] * 4
    assert _wait_with(states) is True


def test_wait_reply_detects_reset():
    """Halaman mengecil drastis = Gemini reset percakapan, bukan selesai."""
    states = [{"len": 8680, "busy": True}] * 3 + [{"len": 255, "busy": False}] * 5
    try:
        _wait_with(states)
        raise AssertionError("reset tidak terdeteksi")
    except SystemExit as e:
        assert "mereset" in str(e), e


def test_reply_state_survives_null_body():
    """document.body null / CDP putus -> None (putaran dilewati), bukan error."""
    import asyncio

    class C(FakeCDP):
        async def eval(self, script, *a, **k):
            raise RuntimeError("TypeError: Cannot read properties of null")

    assert asyncio.run(gs._reply_state(C())) is None


def test_wait_reply_detects_stall():
    """Tombol Stop tetap ada tapi teks tak tumbuh -> macet, bukan menunggu terus."""
    states = [{"len": 8736, "busy": True}] * (gs.STALL_ROUNDS + 2)
    try:
        _wait_with(states)
        raise AssertionError("macet tidak terdeteksi")
    except gs.GeminiStalled as e:
        assert "tidak mulai menjawab" in str(e), e


def test_stall_does_not_fire_when_growing():
    """Teks bertambah tiap putaran = masih bekerja, jangan dianggap macet."""
    states = [{"len": 8000 + i * 100, "busy": True} for i in range(gs.STALL_ROUNDS + 2)]
    # len pertama False berbeda dari putaran terakhir (dianggap tumbuh lagi),
    # jadi butuh REPLY_QUIET + 2 putaran stabil supaya cukup
    states += [{"len": 9700, "busy": False}] * (gs.REPLY_QUIET + 2)
    assert _wait_with(states) is True


def test_result_keys_match_pipeline():
    """pipeline.py membaca w, h, bytes — kunci itu wajib ada."""
    import asyncio

    with tempfile.TemporaryDirectory() as tmp:
        r = asyncio.run(_run_download_button(tmp))
    assert set(r) >= {"path", "bytes", "w", "h", "via"}, r


def main():
    tests = [(k, v) for k, v in sorted(globals().items()) if k.startswith("test_")]
    gagal = 0
    for name, fn in tests:
        try:
            fn()
            print(f"ok    {name}")
        except Exception as e:
            gagal += 1
            print(f"FAIL  {name}: {type(e).__name__}: {e}")
    print(f"\n{len(tests) - gagal}/{len(tests)} lulus")
    return 1 if gagal else 0


if __name__ == "__main__":
    raise SystemExit(main())
