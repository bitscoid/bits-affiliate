"""Uji unit login_bench: pembuatan akun baru + deteksi logout, tanpa Chrome.

python3 tests/test_login_bench.py
"""

import contextlib
import io
import json
import os
import stat
import tempfile
import time

from bits_affiliate import flow_cdp as fc
from bits_affiliate import login_bench as lb


@contextlib.contextmanager
def isolated():
    """HOME/profil sementara + stdout senyap, supaya disk asli tidak tersentuh."""
    with tempfile.TemporaryDirectory() as tmp:
        asli = (fc.HOME, fc.PROFILES, lb.STATE_FILE)
        fc.HOME = tmp
        fc.PROFILES = os.path.join(tmp, "profiles")
        os.makedirs(fc.PROFILES, exist_ok=True)
        lb.STATE_FILE = os.path.join(tmp, "login-state.json")
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                yield tmp
        finally:
            fc.HOME, fc.PROFILES, lb.STATE_FILE = asli


@contextlib.contextmanager
def stub_open():
    """Ganti open_account (yang menyalakan Chrome) dengan pencatat."""
    asli = lb.open_account
    lb.open_account = lambda acc, port=None: None
    try:
        yield
    finally:
        lb.open_account = asli


def test_add_account_creates_profile_and_state():
    with isolated() as tmp, stub_open():
        assert lb.add_account("Baru.User@Gmail.com") is True
        assert os.path.isdir(
            os.path.join(tmp, "profiles", "baru.user@gmail.com", "Default")
        )
        state = json.load(open(os.path.join(tmp, "login-state.json")))
        assert state["baru.user@gmail.com"]["ok"] is False
        assert lb.accounts() == ["baru.user@gmail.com"]


def test_add_account_rejects_bad_email():
    with isolated(), stub_open():
        assert lb.add_account("bukan-email") is False
        assert lb.add_account("dua@@spasi.com") is False
        assert lb.accounts() == []


def test_add_account_rejects_duplicate():
    with isolated(), stub_open():
        assert lb.add_account("a@b.co") is True
        assert lb.add_account("A@B.co") is False
        assert lb.accounts() == ["a@b.co"]


def test_safe_jobs_serial_on_port_collision():
    asli = lb.port_for
    lb.port_for = lambda acc: 9500  # dua akun -> satu port
    try:
        assert lb.safe_jobs(["a@x.id", "b@x.id"], 4) == 1
        lb.port_for = asli
        assert lb.safe_jobs(["a@x.id", "b@x.id"], 4) == 4
        # permintaan di atas batas dijepit ke MAX_JOBS
        assert lb.safe_jobs(["a@x.id"], 8) == lb.MAX_JOBS
        assert lb.safe_jobs(["a@x.id"], 0) == 1
    finally:
        lb.port_for = asli


def test_check_all_prints_in_account_order_when_parallel():
    with isolated(), stub_open():
        lb.add_account("b@x.id")
        lb.add_account("a@x.id")
        asli_accounts, asli_probe_one = lb.accounts, lb._probe_one
        lb.accounts = lambda: ["a@x.id", "b@x.id"]

        def cepat(acc):
            time.sleep(0.05 if acc == "a@x.id" else 0)
            return acc, True, {"url": "", "who": acc, "signed_out": False}

        lb._probe_one = cepat
        out = io.StringIO()
        try:
            with contextlib.redirect_stdout(out):
                lb.check_all(jobs=2)
        finally:
            lb.accounts, lb._probe_one = asli_accounts, asli_probe_one
        baris = [
            b for b in out.getvalue().splitlines() if b.startswith(("OK", "BELUM"))
        ]
        assert "a@x.id" in baris[0] and "b@x.id" in baris[1], baris


def test_status_peek_parallel_not_serial():
    """Banyak akun dengan peek lambat harus selesai jauh di bawah serial."""
    with isolated(), stub_open():
        accs = [f"u{i}@x.id" for i in range(16)]
        for a in accs:
            lb.add_account(a)
        asli_accounts, asli_peek = lb.accounts, lb._peek
        lb.accounts = lambda: accs

        def lambat(acc):
            time.sleep(0.2)
            return {"url": "", "who": acc, "login": False}

        lb._peek = lambat
        try:
            t = time.time()
            with contextlib.redirect_stdout(io.StringIO()):
                lb.status()
            lama = time.time() - t
        finally:
            lb.accounts, lb._peek = asli_accounts, asli_peek
        assert lama < 2.0, f"status masih serial: {lama:.1f} dtk"


def test_probe_kills_orphan_before_start():
    """Port CDP mati tapi proses Chrome masih ada -> stop_chrome dulu."""
    with isolated(), stub_open():
        lb.add_account("a@b.co")
        asli = (lb.fc._cdp_alive, lb.fc._pids_on_port,
                lb.fc.stop_chrome, lb.fc.ensure_chrome, lb.fc.run)
        urutan = []
        try:
            lb.fc._cdp_alive = lambda port: False
            lb.fc._pids_on_port = lambda port: [1234]
            lb.fc.stop_chrome = lambda port: urutan.append("stop") or True
            lb.fc.ensure_chrome = lambda *a, **k: urutan.append("start") or 9500
            lb.fc.run = lambda fn, port: {"url": "", "who": "a@b.co"}
            lb.probe("a@b.co")
        finally:
            (lb.fc._cdp_alive, lb.fc._pids_on_port,
             lb.fc.stop_chrome, lb.fc.ensure_chrome, lb.fc.run) = asli
        assert urutan[:2] == ["stop", "start"], urutan


def test_status_uses_live_peek(monkeypatch=None):
    with isolated(), stub_open():
        lb.add_account("a@b.co")
        lb.add_account("c@d.co")
        # state: a@b.co OK, c@d.co BELUM; peek: c@d.co sedang di halaman login
        asli_peek, asli_accounts = lb._peek, lb.accounts
        lb.accounts = lambda: ["a@b.co", "c@d.co"]
        lb._peek = lambda acc: (
            {"url": "https://accounts.google.com/signin", "who": "", "login": True}
            if acc == "c@d.co"
            else {"url": "https://flow.google.com/", "who": "a@b.co", "login": False}
        )
        out = io.StringIO()
        try:
            with contextlib.redirect_stdout(out):
                lb.status()
        finally:
            lb._peek, lb.accounts = asli_peek, asli_accounts
        teks = out.getvalue()
        assert "OK    a@b.co" in teks, teks
        assert "LOGIN c@d.co" in teks, teks


def test_status_flags_wrong_account():
    with isolated(), stub_open():
        lb.add_account("a@b.co")
        asli_peek = lb._peek
        lb._peek = lambda acc: {
            "url": "https://myaccount.google.com/",
            "who": "orang.lain@gmail.com",
            "login": False,
        }
        out = io.StringIO()
        try:
            with contextlib.redirect_stdout(out):
                lb.status()
        finally:
            lb._peek = asli_peek
        assert "SALAH a@b.co" in out.getvalue()


def test_done_returns_false_and_records_when_signed_out():
    with isolated(), stub_open():
        lb.add_account("a@b.co")
        asli = lb.probe_live
        lb.probe_live = lambda acc, port=None: {
            "signed_out": True,
            "who": "",
            "url": "https://accounts.google.com/signin",
        }
        try:
            assert lb.done("a@b.co") is False
        finally:
            lb.probe_live = asli
        assert json.load(open(lb.STATE_FILE))["a@b.co"]["ok"] is False


def test_done_returns_false_on_wrong_account():
    with isolated(), stub_open():
        lb.add_account("a@b.co")
        asli = lb.probe_live
        lb.probe_live = lambda acc, port=None: {
            "signed_out": False,
            "who": "orang.lain@gmail.com",
            "url": "https://myaccount.google.com/",
        }
        try:
            assert lb.done("a@b.co") is False
        finally:
            lb.probe_live = asli
        assert json.load(open(lb.STATE_FILE))["a@b.co"]["ok"] is False


def test_done_returns_true_on_matching_email():
    with isolated(), stub_open():
        lb.add_account("a@b.co")
        asli = lb.probe_live
        lb.probe_live = lambda acc, port=None: {
            "signed_out": False,
            "who": "a@b.co",
            "url": "https://myaccount.google.com/",
        }
        try:
            assert lb.done("a@b.co") is True
        finally:
            lb.probe_live = asli
        assert json.load(open(lb.STATE_FILE))["a@b.co"]["ok"] is True


def test_save_state_is_private():
    """State memuat email akun -> mode 600, juga saat menimpa file lama."""
    with isolated() as tmp:
        path = os.path.join(tmp, "login-state.json")
        if os.path.exists(path):
            os.chmod(path, 0o644)
        lb.save_state({"a@b.co": {"ok": True}})
        assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
        lb.save_state({"a@b.co": {"ok": False}})
        assert stat.S_IMODE(os.stat(path).st_mode) == 0o600


def test_signed_out_detection():
    # redirect ke halaman login: pasti logout
    assert lb._is_signed_out({"url": "https://accounts.google.com/signin", "who": ""})
    # masih di myaccount + email terbaca: sesi hidup
    assert not lb._is_signed_out(
        {"url": "https://myaccount.google.com/", "who": "a@b.c"}
    )
    # masih di myaccount tapi email tidak terbaca: logout
    assert lb._is_signed_out({"url": "https://myaccount.google.com/", "who": ""})


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
