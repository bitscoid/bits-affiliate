"""Uji unit check_credits: parser saldo/tanggal dan argumen CLI.

Tidak menyentuh Chrome/CDP. Jalankan (setelah `pip install -e .`):

    python3 tests/test_check_credits.py     # assert bawaan, tanpa dependensi
    python3 -m pytest tests -q              # kalau pytest terpasang

Parser diuji dengan teks panel asli dari Flow (ID dan EN).
"""

from bits_affiliate import check_credits as cc


def test_parse_id():
    st = {"panel": "Akun Google\n2 kredit Google Flow\nDireset pada 23 Nov pukul 05.54"}
    assert cc.parse(st) == (2, "23 Nov pukul 05.54")


def test_parse_en():
    st = {"panel": "Switch account\n50 Google Flow credits\nResets Nov 23 at 7:54 AM"}
    assert cc.parse(st) == (50, "Nov 23 at 7:54 AM")


def test_parse_zero_and_no_reset():
    assert cc.parse({"panel": "0 Google Flow credits"}) == (0, "")


def test_parse_thousands_separator():
    assert cc.parse({"panel": "1.000 kredit Google Flow"})[0] == 1000
    assert cc.parse({"panel": "1,000 Google Flow credits"})[0] == 1000


def test_parse_ignores_unrelated_numbers():
    # Angka lain di panel tidak boleh dianggap saldo.
    st = {"panel": "Upgrade plan\n5 anggota\n50 Google Flow credits"}
    assert cc.parse(st)[0] == 50


def test_parse_missing_balance():
    assert cc.parse({"panel": "Upgrade plan"}) == (None, "")


def test_parse_reset_button_not_a_date():
    # Tombol "Reset ..." tidak boleh dianggap tanggal reset.
    st = {"panel": "Reset all settings\n50 Google Flow credits"}
    assert cc.parse(st) == (50, "")


def test_reset_dt_id():
    now = cc.datetime(2026, 11, 22, 10, 0)
    assert cc.reset_dt("23 Nov pukul 05.53", now).isoformat() == "2026-11-23T05:53:00"


def test_reset_dt_en_am_pm():
    now = cc.datetime(2026, 11, 22, 10, 0)
    assert cc.reset_dt("Nov 23 at 7:54 AM", now).isoformat() == "2026-11-23T07:54:00"
    assert cc.reset_dt("Dec 1 at 12:05 PM", now).isoformat() == "2026-12-01T12:05:00"
    assert cc.reset_dt("Dec 1 at 12:05 AM", now).isoformat() == "2026-12-01T00:05:00"


def test_reset_dt_unreadable():
    assert cc.reset_dt("") is None
    assert cc.reset_dt("besok pagi") is None
    assert cc.reset_dt("32 Nov pukul 05.00") is None  # tanggal mustahil


def test_reset_dt_menyeberang_tahun():
    """Dibaca akhir Des, reset '5 Jan' = Jan tahun DEPAN, bukan masa lampau."""
    now = cc.datetime(2026, 12, 28, 10, 0)
    assert cc.reset_dt("5 Jan pukul 05.54", now).isoformat() == "2027-01-05T05:54:00"
    assert cc.reset_dt("Jan 5 at 5:54 AM", now).isoformat() == "2027-01-05T05:54:00"


def test_reset_soonest_by_date_not_alphabet():
    # Alfabet menaruh "23 Dec" sebelum "23 Nov"; urutan tanggal yang benar Nov.
    # `now` disuntik: tanpa itu tes rapuh — begitu tanggal sistem melewati 23 Nov,
    # reset itu dianggap tahun depan dan urutannya terbalik secara sah.
    from datetime import datetime
    now = datetime(2026, 11, 1, 9, 0)
    assert cc.reset_dt("23 Dec pukul 05.00", now) > cc.reset_dt(
        "23 Nov pukul 05.00", now)
    assert cc.reset_dt("Dec 1 at 7:00 AM", now) > cc.reset_dt("Nov 23 at 8:22 AM", now)


def test_parse_cli_positional_compat():
    assert cc.parse_cli(["48", "a@x"]) == (48, ["a@x"], 1, False)


def test_parse_cli_defaults():
    assert cc.parse_cli([]) == (cc.COST_NEEDED, None, 1, False)
    # Argumen pertama non-angka = email, bukan ambang.
    assert cc.parse_cli(["a@x"]) == (cc.COST_NEEDED, ["a@x"], 1, False)


def test_parse_cli_flags():
    assert cc.parse_cli(["-j", "3", "--json", "40", "a@x"]) == (40, ["a@x"], 3, True)
    # -j 0 / negatif dijepit ke 1 (paralel minimal satu).
    assert cc.parse_cli(["-j", "0"]) == (cc.COST_NEEDED, None, 1, False)


def test_account_balance_mapping():
    """account_balance meringkas read_one tanpa Chrome (read_balance dipalsukan)."""
    cases = {
        "ok": {"panel": "50 Google Flow credits\nResets Nov 23 at 7:54 AM"},
        "tidak login": {"signed_out": True, "body": ""},
        "tidak terbaca": {"panel": "Upgrade plan"},
    }
    asli = cc.read_balance
    try:
        for note, state in cases.items():
            cc.read_balance = lambda a, p, s=state: s
            bal, reset, got = cc.account_balance("a@x")
            assert got == note, (note, got)
            if note == "ok":
                assert (bal, reset) == (50, "Nov 23 at 7:54 AM")
            else:
                assert bal is None
        cc.read_balance = lambda a, p: (_ for _ in ()).throw(RuntimeError("boom"))
        bal, reset, note = cc.account_balance("a@x")
        assert bal is None and note.startswith("ERR boom")
    finally:
        cc.read_balance = asli


def test_avatar_js_built_correctly():
    # Konstanta avatar dipakai di JS tunggu dan JS klik.
    assert ".header-user-button" in cc._HAS_AVATAR
    assert "__AVATAR__" not in cc._CLICK
    assert "no avatar" in cc._CLICK
    assert "kredit" in cc._CLICK


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
