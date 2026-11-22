#!/usr/bin/env python3
"""Login workbench: satu jendela VNC, ganti profil per akun Google.

Akun baru tidak perlu didaftarkan manual: `--add` membuat folder profil lalu
membuka halaman login. Manusia menyelesaikan password/2FA/reCAPTCHA di VNC,
lalu `--done` memverifikasi email yang benar-benar login.

    bits-login                    # status (state + peek Chrome yang hidup)
    bits-login EMAIL              # buka akun (login manual)
    bits-login --add EMAIL        # akun baru: profil + buka login
    bits-login --done EMAIL       # verifikasi + catat
    bits-login --check [-j N]     # probe headless semua akun
"""

import argparse
import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from . import flow_cdp as fc

STATE_FILE = os.path.join(fc.HOME, "login-state.json")
port_for = fc.port_for

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[a-z]{2,}$", re.I)

MAX_JOBS = 4  # batas --check -j: 1 akun = 1 Chrome (±150-300 MB)
PEEK_WORKERS = 8  # status: peek semua akun sekaligus, bukan satu per satu

# Signed out = Google melempar ke halaman login (bahasa-agnostik).
SIGNED_OUT_URL_RE = re.compile(r"accounts\.google\.com|/ServiceLogin|/signin", re.I)

# Navigasi ke myaccount lalu baca email yang login.
_PROBE_JS = r"""
(() => {
  const t = document.body.innerText || '';
  const m = t.match(/[\w.+-]+@[\w.-]+\.[a-z]{2,}/);
  return JSON.stringify({url: location.href, who: m ? m[0] : ''});
})()
"""

# Baca halaman yang SUDAH terbuka tanpa navigasi (aman saat login manual).
_PEEK_JS = r"""
(() => {
  const t = document.body.innerText || '';
  const m = t.match(/[\w.+-]+@[\w.-]+\.[a-z]{2,}/);
  return JSON.stringify({
    url: location.href,
    who: m ? m[0] : '',
    login: /accounts\.google\.com|ServiceLogin|\/signin/i.test(location.href)
  });
})()
"""


def load_state() -> dict:
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_state(state: dict) -> None:
    """Tulis atomik + mode 600: file memuat email akun, bukan untuk dibaca umum."""
    os.makedirs(fc.HOME, exist_ok=True)
    tmp = STATE_FILE + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(state, f, indent=1)
    os.chmod(tmp, 0o600)  # kalau file lama sudah ada dengan mode lebih longgar
    os.replace(tmp, STATE_FILE)


def accounts() -> list[str]:
    return [a["account"] for a in fc.list_accounts()]


def add_account(email: str) -> bool:
    """Buat profil kosong untuk akun baru, lalu buka halaman login."""
    acc = email.strip().lower()
    if not EMAIL_RE.match(acc):
        print(f"bukan email yang valid: {email!r}")
        return False
    if acc in (a.lower() for a in accounts()):
        print(f"{acc} sudah terdaftar; buka dengan: bits-login {acc}")
        return False
    os.makedirs(fc.default_dir(acc), exist_ok=True)
    state = load_state()
    state.setdefault(acc, {"ok": False, "checked": "", "url": "", "who": ""})
    save_state(state)
    print(f"profil dibuat: {fc.profile_dir(acc)}")
    open_account(acc)
    return True


def _is_signed_out(st: dict) -> bool:
    if SIGNED_OUT_URL_RE.search(st.get("url") or ""):
        return True
    # masih di myaccount: sesi hidup kalau email akun terbaca
    return not st.get("who")


def probe_live(account: str) -> dict | None:
    """Baca akun yang login di Chrome akun ini (Chrome harus sudah hidup)."""
    acc = fc.resolve_account(account)
    port = port_for(acc)
    if not fc._cdp_alive(port):
        return None

    async def m(c):
        await c.goto("https://myaccount.google.com/", wait=10)
        return json.loads(await c.eval(_PROBE_JS))

    st = fc.run(m, port)
    st["signed_out"] = _is_signed_out(st)
    return st


def _peek(acc: str) -> dict | None:
    """Snapshot halaman Chrome akun ini tanpa navigasi; None kalau tak hidup."""
    port = port_for(acc)
    if not fc._cdp_alive(port):
        return None

    async def m(c):
        return json.loads(await c.eval(_PEEK_JS))

    try:
        return fc.run(m, port)
    except Exception:  # Chrome mati di tengah jalan / tab belum siap
        return None


def probe(account: str) -> tuple[bool | None, dict]:
    """Cek headless dengan Chrome sendiri; profil harus bebas.

    Menolak jalan kalau profil ini sedang dipakai Chrome lain: mematikannya
    bisa menutup jendela login yang sedang dibuka manusia. Return (None, info)
    untuk keadaan sibuk.
    """
    acc = fc.resolve_account(account)
    port = port_for(acc)
    if fc._cdp_alive(port):
        return None, {"busy": True, "port": port}
    if fc._pids_on_port(port):
        # CDP mati tapi proses Chrome masih hidup (yatim): bunuh dulu, kalau
        # tidak dua Chrome akan memakai profil yang sama.
        fc.stop_chrome(port)
    fc.ensure_chrome(acc, port, headless=True)

    async def m(c):
        await c.goto("https://myaccount.google.com/", wait=10)
        return json.loads(await c.eval(_PROBE_JS))

    try:
        st = fc.run(m, port)
    finally:
        fc.stop_chrome(port)
    st["signed_out"] = _is_signed_out(st)
    return (not st["signed_out"]) and st["who"] == acc, st


def _row(acc: str, state: dict, live: dict | None) -> tuple[str, str, str]:
    """Label status satu akun: state tersimpan, ditimpa peek Chrome yang hidup."""
    ok = bool(state.get(acc, {}).get("ok"))
    ts = state.get(acc, {}).get("checked", "")
    label, src = ("OK" if ok else "BELUM"), "state"
    if live and live.get("login"):
        return "LOGIN", "live", "-"
    if live and live.get("who"):
        label = "OK" if live["who"] == acc else "SALAH"
        return label, "live", time.strftime("%Y-%m-%d %H:%M")
    return label, src, ts


def status() -> None:
    """State tersimpan + peek Chrome yang sedang hidup (tanpa navigasi).

    Chrome yang terbuka di halaman login ditandai LOGIN, akun yang salah
    ditandai SALAH — keduanya menang atas state lama.
    """
    state = load_state()
    accs = accounts()
    with ThreadPoolExecutor(max_workers=min(PEEK_WORKERS, max(1, len(accs)))) as ex:
        lives = list(ex.map(_peek, accs))
    rows = [
        (acc, *_row(acc, state, live)) for acc, live in zip(accs, lives, strict=True)
    ]
    ready = sum(1 for _, label, _, _ in rows if label == "OK")
    print(f"{ready}/{len(rows)} akun siap\n")
    for acc, label, src, ts in rows:
        print(f"{label:5} {acc:42} {src:5} {ts}")


def open_account(account: str) -> None:
    """Chrome terlihat di Xvfb :99 (plus x11vnc) pada halaman login Google.

    Kalau Chrome akun ini sudah hidup, JANGAN dinavigasi ulang: halaman login
    yang sedang diisi manusia bisa terlempar. Cukup pastikan VNC hidup.
    """
    acc = fc.resolve_account(account)
    port = port_for(acc)
    if not fc.ensure_vnc():
        print("[!] VNC tidak siap (x11vnc/vnc.passwd hilang) — Chrome tetap dibuka")
    if fc._cdp_alive(port):
        print(f"Chrome untuk {acc} sudah terbuka (port {port}); tidak dinavigasi ulang")
    else:
        port = fc.open_visible(acc, "https://accounts.google.com/")
        print(f"Chrome untuk {acc} (port {port})")
    print(f"VNC: {fc.vnc_hint()}")
    print(f"Login di jendela VNC, lalu jalankan: bits-login --done {acc}")


def done(account: str) -> bool:
    """Verifikasi akun di Chrome-nya yang masih hidup, lalu catat."""
    acc = fc.resolve_account(account)
    live = probe_live(acc)
    if live is None:
        print(f"tidak ada Chrome hidup untuk {acc}; buka dulu: bits-login {acc}")
        return False

    ok = (not live["signed_out"]) and live["who"] == acc
    state = load_state()
    state[acc] = {
        "ok": ok,
        "checked": time.strftime("%Y-%m-%d %H:%M"),
        "url": live["url"],
        "who": live["who"],
    }
    save_state(state)

    if ok:
        print(f"OK    {acc}")
    elif live["signed_out"]:
        print(f"BELUM {acc}  (sesi habis; login ulang: bits-login {acc})")
    elif live["who"] and live["who"] != acc:
        print(f"SALAH AKUN: profil ini login sebagai {live['who']}, bukan {acc}")
    else:
        print(f"BELUM {acc}  {live['url'][:70]}")
    return ok


def safe_jobs(accs: list[str], jobs: int) -> int:
    """Batasi paralelisme: tiap akun membuka Chrome sendiri (~150-300 MB).

    Juga turun ke serial kalau dua akun kebetulan berbagi port CDP.
    """
    ports = [port_for(a) for a in accs]
    if len(set(ports)) != len(ports):
        print("[!] port bentrok antar akun; probe dibuat serial", flush=True)
        return 1
    jobs = max(1, jobs)
    if jobs > MAX_JOBS:
        print(
            f"[!] -j {jobs} dibatasi {MAX_JOBS} (tiap akun = satu Chrome)",
            flush=True,
        )
        jobs = MAX_JOBS
    return jobs


def _probe_one(acc: str) -> tuple[str, bool | None, dict]:
    try:
        ok, st = probe(acc)
    except (Exception, SystemExit) as e:  # Chrome gagal start untuk akun ini
        return acc, None, {"err": str(e)}
    return acc, ok, st


def check_all(jobs: int = 1) -> None:
    """Probe headless tiap akun; satu akun rusak tidak menghentikan sisanya."""
    accs = accounts()
    jobs = safe_jobs(accs, jobs)
    if jobs > 1 and len(accs) > 1:
        with ThreadPoolExecutor(max_workers=jobs) as ex:
            futs = [ex.submit(_probe_one, a) for a in accs]
            hasil = [f.result() for f in as_completed(futs)]
        urut = {a: i for i, a in enumerate(accs)}
        hasil.sort(key=lambda r: urut[r[0]])  # cetak stabil, bukan urutan selesai
    else:
        hasil = [_probe_one(a) for a in accs]

    state = load_state()
    for acc, ok, st in hasil:
        if "err" in st:
            print(f"ERR   {acc}  {st['err']}", flush=True)
            continue
        if ok is None:
            print(f"BUSY  {acc}  (port {st['port']} dipakai; tutup dulu)", flush=True)
            continue
        state[acc] = {
            "ok": ok,
            "checked": time.strftime("%Y-%m-%d %H:%M"),
            "url": st["url"],
            "who": st.get("who", ""),
        }
        note = "" if ok or st.get("who") in ("", acc) else f"  sebagai {st['who']}"
        print(f"{'OK   ' if ok else 'BELUM'} {acc}{note}", flush=True)
    save_state(state)


def main() -> None:
    p = argparse.ArgumentParser(
        description="Workbench login Google: satu jendela VNC per akun."
    )
    p.add_argument("email", nargs="?", help="buka akun ini (login manual)")
    p.add_argument("--add", metavar="EMAIL", help="buat profil baru lalu buka login")
    p.add_argument("--done", metavar="EMAIL", help="verifikasi + catat hasil login")
    p.add_argument(
        "--check",
        "--check-all",
        dest="check",
        action="store_true",
        help="probe headless semua akun",
    )
    p.add_argument(
        "-j",
        "--jobs",
        type=int,
        default=1,
        metavar="N",
        help="banyak akun diprobe sekaligus saat --check (default 1)",
    )
    args = p.parse_args()

    aksi = [bool(args.email), bool(args.add), bool(args.done), args.check]
    if sum(aksi) > 1:
        p.error("pilih satu aksi: EMAIL, --add, --done, atau --check")

    if args.check:
        check_all(args.jobs)
    elif args.add:
        raise SystemExit(0 if add_account(args.add) else 1)
    elif args.email:
        open_account(args.email)
    elif args.done:
        raise SystemExit(0 if done(args.done) else 1)
    else:
        status()


if __name__ == "__main__":
    main()
