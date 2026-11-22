"""Baca saldo kredit Google Flow tiap akun (lewat CDP, bukan API publik).

Saldo tidak ada di DOM sampai avatar header diklik. Panelnya bisa berbahasa
Indonesia atau Inggris, tergantung setelan bahasa akun Google:

    50 kredit Google Flow           / 50 Google Flow credits
    Direset pada 23 Nov pukul 07.54 /  Resets Nov 23 at 7:54 AM

Script mengklik avatar, membaca teks panel, lalu menarik angka saldo dan
tanggal reset dari baris yang memuat kata kredit.
"""

import argparse
import asyncio
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from . import flow_cdp as fc
from .flow_stage import COST as COST_NEEDED  # satu sumber biaya video

BALANCE = re.compile(r"(\d[\d.,]*)\s*(?:google flow\s*)?(?:kredit|credit)", re.I)
RESET = re.compile(r"(?:direset\s+pada|resets?)\s+([^\n|]*\d[^\n|]*)", re.I)

# Nama bulan ID + EN -> nomor, untuk mengurutkan tanggal reset lintas bahasa.
BULAN = {
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "mei": 5,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "agu": 8,
    "aug": 8,
    "sep": 9,
    "okt": 10,
    "oct": 10,
    "nov": 11,
    "des": 12,
    "dec": 12,
}
RESET_ID = re.compile(r"(\d{1,2})\s+([a-z]{3,9})\s+pukul\s+(\d{1,2})[.:](\d{2})", re.I)
RESET_EN = re.compile(
    r"([a-z]{3,9})\s+(\d{1,2})\s+at\s+(\d{1,2})[.:](\d{2})\s*(am|pm)?", re.I
)

# Avatar header dipakai 2 tempat (tunggu + klik); satu definisi biar tidak drift.
_AVATAR = (
    "document.querySelector('.header-user-button')"
    "||document.querySelector('[aria-label*=\"Detail akun\"]')"
    "||document.querySelector('[aria-label*=\"Account details\"]')"
    "||document.querySelector('[aria-label*=\"Akun Google\"]')"
)
_HAS_AVATAR = f"!!({_AVATAR})"
_CLICK = r"""
(() => {
  const b = __AVATAR__
         || [...document.querySelectorAll('div[role="button"],button')].find((e) =>
              /\bPLUS\b|kredit|credit/i.test(e.innerText || ''));
  if (!b) return 'no avatar';
  b.click();
  return 'clicked';
})()
""".replace("__AVATAR__", _AVATAR)

# Batas tunggu (detik) dan ukuran teks yang dibaca dari halaman.
WAIT_AVATAR_TRIES = 24  # × 1.5 dtk = ~36 dtk menunggu avatar
WAIT_AVATAR_STEP = 1.5
RETRY_AVATAR_TRIES = 6  # klik ulang setelah avatar telat
WAIT_PANEL_TRIES = 4  # × 2 dtk menunggu panel kredit
WAIT_PANEL_STEP = 2.0
PANEL_CHARS = 500
BODY_CHARS = 600
SIGNED_OUT_BODY_CHARS = 400

_PANEL_JS = r"""
(() => {
  const panes = [...document.querySelectorAll(
    '.cdk-overlay-pane, mat-menu, mat-mdc-menu-panel, [role="menu"], [role="dialog"]')];
  const texts = panes.map((p) => (p.innerText || '').trim()).filter(Boolean);
  const best = texts.sort((a, b) => b.length - a.length)[0] || '';
  return JSON.stringify({panel: best.slice(0, __PANEL__),
                         body: (document.body.innerText || '').slice(0, __BODY__)});
})()
""".replace("__PANEL__", str(PANEL_CHARS)).replace("__BODY__", str(BODY_CHARS))

_BODY_JS = "(document.body.innerText || '').slice(0, %d)" % SIGNED_OUT_BODY_CHARS


def reset_dt(text: str, now: datetime | None = None) -> datetime | None:
    """Tanggal reset -> datetime (tahun berjalan), atau None kalau tak terbaca.

    Reset yang sudah lewat (dibaca akhir Des, jatuh awal Jan) = tahun depan,
    bukan masa lampau. `now` bisa disuntik untuk test.
    """
    now = now or datetime.now()
    if not text:
        return None
    t = text.strip()
    year = now.year
    hasil = _reset_dt_tahun_ini(t, year)
    if hasil and hasil < now:
        # ponytail: reset harian yang sudah lewat (jam) tak tahu bulan depannya;
        # +1 tahun cukup untuk urutan "soonest" yang benar pada kasus lintas tahun
        hasil = _reset_dt_tahun_ini(t, year + 1)
    return hasil


def _reset_dt_tahun_ini(t, year):
    m = RESET_ID.search(t)
    if m:
        day, mon_txt, hh, mm = m.groups()
        mon = BULAN.get(mon_txt.lower()[:3])
        if mon:
            try:
                return datetime(year, mon, int(day), int(hh), int(mm))
            except ValueError:  # tanggal mustahil (mis. 32 Nov)
                pass

    m = RESET_EN.search(t)
    if m:
        mon_txt, day, hh, mm, ampm = m.groups()
        mon = BULAN.get(mon_txt.lower()[:3])
        if mon:
            h = int(hh) % 12
            if (ampm or "").lower() == "pm":
                h += 12
            try:
                return datetime(year, mon, int(day), h, int(mm))
            except ValueError:
                pass
    return None


def read_balance(account: str, port: int) -> dict:
    """Buka Chrome headless akun itu, klik avatar, baca panel kreditnya."""
    if not fc._cdp_alive(port):
        # Chrome hidup = mungkin sesi login manual sedang berlangsung di VNC;
        # jangan bunuh, tempel saja ke yang ada (login_bench memakai guard sama)
        fc.stop_chrome(port)
        time.sleep(1)
        fc.ensure_chrome(account, port, headless=True)

    async def m(c):
        await c.cmd("Page.navigate", url="https://flow.google.com/")
        # Flow menyajikan halaman marketing dulu; app + avatar menyusul setelah
        # sesi terbaca. Tunggu sebelum menyimpulkan "logout".
        for _ in range(WAIT_AVATAR_TRIES):
            await asyncio.sleep(WAIT_AVATAR_STEP)
            if await c.eval(_HAS_AVATAR):
                break
        clicked = await c.eval(_CLICK)
        if clicked == "no avatar":
            # sekali lagi: app kadang baru memasang avatar setelah jeda
            for _ in range(RETRY_AVATAR_TRIES):
                await asyncio.sleep(WAIT_AVATAR_STEP)
                clicked = await c.eval(_CLICK)
                if clicked != "no avatar":
                    break
        if clicked == "no avatar":
            return {"signed_out": True, "body": await c.eval(_BODY_JS) or ""}
        data = {}
        for _ in range(WAIT_PANEL_TRIES):
            await asyncio.sleep(WAIT_PANEL_STEP)
            data = json.loads(await c.eval(_PANEL_JS))
            blob = (data.get("panel", "") + " " + data.get("body", "")).lower()
            if "kredit" in blob or "credit" in blob:
                break
            await c.eval(_CLICK)  # panel kadang perlu diklik ulang
        return {"clicked": clicked, **data}

    try:
        return fc.run(m, port)
    finally:
        fc.stop_chrome(port)


def parse(state: dict) -> tuple[int | None, str]:
    """Ambil saldo dari baris yang memuat kata kredit, dan tanggal reset."""
    blob = "\n".join(str(state.get(k, "")) for k in ("panel", "body"))
    bal = None
    for line in blob.splitlines():
        m = BALANCE.search(line)
        if not m:
            continue
        digits = m.group(1).replace(".", "").replace(",", "")
        if digits.isdigit():
            bal = int(digits)
            break
    m = RESET.search(blob)
    return bal, (m.group(1).strip() if m else "")


def read_one(account: str, need: int = COST_NEEDED) -> dict:
    """Baca satu akun -> dict baris (dipakai serial maupun paralel)."""
    try:
        st = read_balance(account, fc.port_for(account))
    except Exception as e:  # akun rusak tidak boleh menghentikan akun lain
        return {"account": account, "balance": None, "state": f"ERR {e}", "reset": ""}
    if st.get("signed_out"):
        return {
            "account": account,
            "balance": None,
            "state": "tidak login",
            "reset": "",
        }
    bal, reset = parse(st)
    if bal is None:
        return {
            "account": account,
            "balance": None,
            "state": "tidak terbaca",
            "reset": reset,
        }
    return {
        "account": account,
        "balance": bal,
        "state": "cukup" if bal >= need else "KURANG",
        "reset": reset,
    }


def account_balance(account: str) -> tuple[int | None, str, str]:
    """(saldo|None, tanggal reset, catatan) untuk satu akun; dipakai bits_affiliate."""
    r = read_one(account)
    note = "ok" if r["balance"] is not None else r["state"]
    return r["balance"], r["reset"], note


def print_row(r: dict) -> None:
    if r["balance"] is None:
        print(f"      ?  {r['state']}  {r['account']}", flush=True)
    else:
        print(
            f"{r['balance']:>5} kredit  {r['state']:8} {r['account']:40} "
            f"reset: {r['reset'] or '-'}",
            flush=True,
        )


def parse_cli(argv: list[str] | None = None) -> tuple[int, list[str] | None, int, bool]:
    """Argumen -> (ambang, akun|None, jobs, json). None = semua profil."""
    p = argparse.ArgumentParser(description="Cek saldo kredit Google Flow per akun.")
    p.add_argument(
        "need", nargs="?", default=None, help=f"ambang kredit (default {COST_NEEDED})"
    )
    p.add_argument("emails", nargs="*", help="akun tertentu (kosong = semua profil)")
    p.add_argument(
        "-j",
        "--jobs",
        type=int,
        default=1,
        help="baca berapa akun sekaligus (default 1 = serial)",
    )
    p.add_argument("--json", action="store_true", help="keluarkan JSON")
    args = p.parse_args(argv)

    # Bentuk lama: "check_credits.py 48 a@x" -- argumen non-angka dianggap email.
    need, emails = COST_NEEDED, list(args.emails)
    if args.need is not None:
        if args.need.isdigit():
            need = int(args.need)
        else:
            emails.insert(0, args.need)
    return need, (emails or None), max(1, args.jobs), args.json


def main() -> None:
    need, emails, jobs, as_json = parse_cli()
    accounts = emails or [a["account"] for a in fc.list_accounts()]

    # crc32(email) % 400 bisa bentrok: dua akun satu port tak boleh paralel.
    seen = {}
    for acc in accounts:
        port = fc.port_for(acc)
        if port in seen:
            print(f"[!] port {port} bentrok: {seen[port]} & {acc}", flush=True)
            jobs = 1
        seen[port] = acc

    if jobs > 1 and len(accounts) > 1:
        with ThreadPoolExecutor(max_workers=jobs) as ex:
            rows = list(ex.map(lambda a: read_one(a, need), accounts))
        if not as_json:
            for r in rows:
                print_row(r)
    else:
        rows = []
        for acc in accounts:
            r = read_one(acc, need)
            rows.append(r)
            if not as_json:
                print_row(r)

    good = [r for r in rows if isinstance(r["balance"], int) and r["balance"] >= need]
    dated = [(dt, r["reset"]) for r in rows if (dt := reset_dt(r["reset"]))]
    soonest = min(dated, key=lambda x: x[0])[1] if dated else None

    if as_json:
        print(
            json.dumps(
                {
                    "need": need,
                    "capable": len(good),
                    "rows": rows,
                    "soonest_reset": soonest,
                },
                ensure_ascii=False,
                indent=1,
            )
        )
    else:
        print(f"\nbutuh {need} kredit; {len(good)} akun mampu")
        for r in sorted(good, key=lambda x: -x["balance"]):
            print(f"  {r['balance']:>5}  {r['account']}  (reset {r['reset'] or '-'})")
        if soonest:
            print(f"\nreset terdekat: {soonest}")

    raise SystemExit(0 if good else 1)


if __name__ == "__main__":
    main()
