"""Jaga label UI Gemini/Flow tetap dua bahasa (ID + EN).

Regex label hidup di dalam string JS, jadi yang diuji: ALIASES (data) dan
sumber fungsi (teks regex). Kalau salah satu bahasa hilang saat refactor,
test ini gagal sebelum UI-nya sempat rusak.

    python3 tests/test_ui_labels.py
"""

import inspect

from bits_affiliate import flow_stage as fs
from bits_affiliate import gemini_stage as gs


def test_aliases_bilingual():
    assert {"Bahan", "Ingredients"} <= set(fs.ALIASES["Ingredients"])
    assert {"Upload media", "Unggah media"} <= set(fs.ALIASES["Upload media"])
    assert {"Mulai pembuatan", "Start generation"} <= set(fs.ALIASES["Start"])


def _source(*fns):
    return "\n".join(inspect.getsource(f) for f in fns)


def test_gemini_labels_bilingual():
    src = _source(
        gs._reveal_file_inputs,
        gs._send,
        gs._reply_state,
        gs._pick_create_image,
        gs._pick_mode,
    )
    for id_label, en_label in (
        ("unggah", "upload"),
        ("kirim pesan", "send message"),
        ("hentikan", "stop"),
        ("alat", "tools"),
        ("buat gambar", "create image"),
        ("pemilih mode", "mode picker"),
    ):
        assert id_label in src, id_label
        assert en_label in src, en_label


def test_flow_labels_bilingual():
    src = _source(
        fs._new_project,
        fs._ingredients_button_box,
        fs._upload,
        fs._attach,
        fs._apply_settings,
        fs._credit_state,
        fs._generate,
        fs._download_clip,
        fs._open_clip_editor,
        fs._wait_render,
    )
    for id_label, en_label in (
        ("proyek baru", "new project"),
        ("unggah media", "upload media"),
        ("tambahkan ke perintah", "add to prompt"),
        ("tambahkan bahan", "add ingredients"),
        ("mulai pembuatan", "start generation"),
        ("unduh media", "download media"),
        ("ukuran asli", "original size"),
        ("rangkaian model", "model series"),
        ("Buat dengan", "Create with"),  # penanda logout saat render
        ("otomatis", "auto"),  # nama item pemilih mode
    ):
        assert id_label in src, id_label
        assert en_label in src, en_label


def test_logout_text_bilingual():
    src = inspect.getsource(fs._wait_render)
    assert "Buat" in src and "Create" in src


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
