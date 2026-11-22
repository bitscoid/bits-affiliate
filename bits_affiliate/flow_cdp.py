"""
Local Chrome + CDP driver for Google Flow.

Each Google account gets its own Chrome profile, named after the account email,
under ~/.config/bits-affiliate/profiles/<email>/Default/. The profile is signed
in once by hand (Google binds a session to the device, so copied cookies do not
work) and reused from then on. One profile = one Chrome = one CDP port.
"""

import asyncio
import json
import os
import signal
import subprocess
import time
import urllib.request

import websockets
from websockets.exceptions import ConnectionClosed

HOME = os.path.expanduser("~/.config/bits-affiliate")
PROFILES = os.path.join(HOME, "profiles")
CHROME = os.environ.get("FLOW_CHROME", "google-chrome")
PORT = 9411
VNC_PASS_FILE = os.path.join(HOME, "vnc.passwd")


def port_for(account):
    """One CDP port per account, derived from its name.

    Sharing a port across accounts is what once made a visible window for
    account A answer for account B, and logged the wrong account in.
    """
    import zlib

    return 9500 + (zlib.crc32(account.encode()) % 400)


def profile_dir(account):
    d = os.path.join(PROFILES, account)
    # berisi cookie DB sesi Google: tutup dari user lain (multi-user machine)
    try:
        os.chmod(d, 0o700)
    except OSError:
        pass
    return d


def default_dir(account):
    return os.path.join(profile_dir(account), "Default")


# ------------------------------------------------------------- accounts


def list_accounts():
    """Accounts available locally: one profile dir per account email."""
    if not os.path.isdir(PROFILES):
        return []
    out = []
    for name in os.listdir(PROFILES):
        base = os.path.join(PROFILES, name)
        if not os.path.isdir(base):
            continue
        prefs = os.path.join(base, "Default", "Preferences")
        out.append(
            {
                "account": name,
                "path": base,
                "ready": os.path.exists(prefs),
                "mtime": os.path.getmtime(base),
            }
        )
    return sorted(out, key=lambda x: x["account"].lower())


def resolve_account(account):
    """Accept an email, an email prefix, or a unique fragment of one."""
    accounts = [a["account"] for a in list_accounts()]
    if not accounts:
        raise SystemExit("no local accounts yet; run: bits-login --add <email>")
    a = account.strip().lower()
    if a in (x.lower() for x in accounts):
        return next(x for x in accounts if x.lower() == a)
    hits = [x for x in accounts if a in x.lower()]
    if len(hits) == 1:
        return hits[0]
    if not hits:
        raise SystemExit(f"account {account!r} not set up. available: {accounts}")
    raise SystemExit(f"{account!r} is ambiguous: {hits}")


# ------------------------------------------------------- chrome control


def _ensure_display():
    """No X server here; a visible Chrome still needs one for manual login."""
    if os.environ.get("DISPLAY"):
        return os.environ["DISPLAY"]
    return _spawn_xvfb()


def _spawn_xvfb():
    """Nyalakan Xvfb :99 sekali; blokir sampai lock-nya muncul.

    1440x900 = ukuran jendela Chrome (--window-size), supaya VNC menampilkan
    persis jendela kerja tanpa ruang kosong.
    """
    lock = "/tmp/.X99-lock"
    if not os.path.exists(lock):
        subprocess.Popen(
            ["Xvfb", ":99", "-screen", "0", "1440x900x24"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        for _ in range(40):
            if os.path.exists(lock):
                break
            time.sleep(0.25)
    os.environ["DISPLAY"] = ":99"
    return ":99"


def ensure_vnc():
    """Xvfb :99 + x11vnc :5900 menyala, supaya login manual bisa ditonton."""
    _spawn_xvfb()

    def listening():
        try:
            out = subprocess.run(
                ["ss", "-ltn"], capture_output=True, text=True, timeout=10
            ).stdout
        except (OSError, subprocess.SubprocessError):
            out = ""
        return ":5900" in out

    if listening():
        return True
    if not os.path.exists(VNC_PASS_FILE):
        return False
    subprocess.Popen(
        [
            "x11vnc",
            "-display",
            ":99",
            "-rfbauth",
            VNC_PASS_FILE,
            "-rfbport",
            "5900",
            "-forever",
            "-shared",
            "-nolookup",
            "-noxdamage",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    for _ in range(40):
        time.sleep(0.25)
        if listening():
            return True
    return False


def vnc_hint():
    import socket

    try:
        ip = socket.gethostbyname(socket.gethostname())
    except OSError:
        ip = "<ip-mesin-ini>"
    return f"{ip}:5900  (tonton prosesnya di sini)"


def open_visible(account, url):
    """Chrome terlihat di Xvfb :99 (plus x11vnc), dinavigasi ke url.

    Chrome yang sudah hidup TIDAK disentuh: halaman login yang sedang diisi
    manusia tidak boleh terlempar. Caller bisa cek `_cdp_alive()` dulu.
    """
    ensure_vnc()
    port = port_for(account)
    if _cdp_alive(port):
        return port
    ensure_chrome(account, port, headless=False)

    async def nav(c):
        await c.goto(url, wait=6)

    run(nav, port)
    return port


def ensure_chrome(account, port=None, headless=True):
    """Start a dedicated Chrome for this account, return the port."""
    port = port or PORT
    if not headless:
        _ensure_display()
    d = profile_dir(account)
    os.makedirs(d, exist_ok=True)
    if _cdp_alive(port):
        return port
    # a stale SingletonLock from a killed Chrome makes the next launch abort
    for junk in ("SingletonLock", "SingletonSocket", "SingletonCookie"):
        p = os.path.join(d, junk)
        if os.path.lexists(p):
            try:
                os.unlink(p)
            except OSError:
                pass
    args = [
        CHROME,
        "--password-store=basic",
        "--disable-features=DownloadBubble,DownloadBubbleV2",
        f"--user-data-dir={d}",
        "--profile-directory=Default",
        f"--remote-debugging-port={port}",
        "--remote-debugging-address=127.0.0.1",
        "--no-first-run",
        "--no-default-browser-check",
        "--window-size=1440,900",
        "--disable-blink-features=AutomationControlled",
        "about:blank",
    ]
    if headless:
        args.insert(1, "--headless=new")
    if os.geteuid() == 0:
        args.insert(1, "--no-sandbox")
    dl_dir = os.path.join(HOME, "downloads")
    os.makedirs(dl_dir, exist_ok=True)
    pref_dir = os.path.join(d, "Default")
    os.makedirs(pref_dir, exist_ok=True)
    pref_file = os.path.join(pref_dir, "Preferences")
    try:
        data = {}
        if os.path.exists(pref_file):
            with open(pref_file) as f:
                data = json.load(f) or {}
        data.setdefault("download", {})
        data["download"]["default_directory"] = dl_dir
        data["download"]["prompt_for_download"] = False
        data.setdefault("savefile", {})["default_directory"] = dl_dir
        with open(pref_file, "w") as f:
            json.dump(data, f)
    except Exception:
        pass

    # stdout/stderr ke DEVNULL + sesi sendiri: tanpa ini Chrome mewarisi pipe
    # shell pemanggil, sehingga pipeline (mis. `... | tail`) tak pernah EOF dan
    # shell background tampak menggantung walau kerjanya sudah selesai.
    try:
        subprocess.Popen(
            args,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except FileNotFoundError:
        raise SystemExit(
            f"Chrome tidak ditemukan: {CHROME!r}. Set FLOW_CHROME=/path/ke/chrome "
            "(di mesin ini: /opt/google/chrome/chrome) atau install Google Chrome."
        ) from None
    for _ in range(60):
        if _cdp_alive(port):
            return port
        time.sleep(0.5)
    raise SystemExit(f"chrome for {account} did not open CDP on {port}")


def _cdp_alive(port):
    try:
        urllib.request.urlopen(
            f"http://127.0.0.1:{port}/json/version", timeout=1
        ).read()
        return True
    except Exception:
        return False


def stop_chrome(port=None):
    """Terminate this Chrome and wait until it is really gone.

    Closing a CDP tab is not enough: the browser process keeps running and
    keeps the profile's cookie DB locked. Two Chrome processes on one profile
    corrupt cookies, so always kill by PID and wait for exit.
    """
    port = port or PORT
    # try a graceful CDP close first so cookies flush to disk
    try:
        tabs = json.load(
            urllib.request.urlopen(f"http://127.0.0.1:{port}/json", timeout=3)
        )
        for t in tabs:
            if t["type"] == "page":
                subprocess.run(
                    ["python3", "-c", _CLOSE_SNIPPET, t["webSocketDebuggerUrl"]],
                    capture_output=True,
                    timeout=20,
                )
                break
    except Exception:
        pass

    # then kill whatever still owns the port
    for _ in range(20):
        pids = _pids_on_port(port)
        if not pids:
            return True
        for pid in pids:
            try:
                os.kill(pid, signal.SIGTERM)
            except OSError:
                pass
        time.sleep(0.4)

    for _ in range(10):
        pids = _pids_on_port(port)
        if not pids:
            return True
        for pid in pids:
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass
        time.sleep(0.3)
    return not _pids_on_port(port)


def _pids_on_port(port):
    """PIDs of Chrome processes launched with this remote-debugging-port."""
    pids = []
    try:
        out = subprocess.run(
            ["ps", "-eo", "pid,args"], capture_output=True, text=True, timeout=10
        ).stdout
    except Exception:
        return pids
    needle = f"--remote-debugging-port={port}"
    for line in out.splitlines():
        if (
            # jangan seleksi berdasar nama binary (bisa chromium/dll);
            # port unik per akun, user-data-dir menandai proses skill ini
            "--user-data-dir" in line
            and "crashpad" not in line
            and "type=renderer" not in line
            and "--type=" not in line
            and needle in line
        ):
            try:
                pids.append(int(line.split(None, 1)[0]))
            except (ValueError, IndexError):
                pass
    return pids


_CLOSE_SNIPPET = """
import asyncio, json, sys, websockets
async def m():
    async with websockets.connect(sys.argv[1], max_size=10**7) as ws:
        await ws.send(json.dumps({"id": 1, "method": "Browser.close"}))
        try:
            await asyncio.wait_for(ws.recv(), 5)
        except Exception:
            pass
asyncio.run(m())
"""


# ----------------------------------------------------------------- CDP


def _http(path, port=None):
    port = port or PORT
    return json.load(
        urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=10)
    )


def page_ws(port=None):
    return [t for t in _http("/json", port) if t["type"] == "page"][0][
        "webSocketDebuggerUrl"
    ]


class CDP:
    def __init__(self, ws):
        self.ws = ws
        self.i = 0
        self.queue = []

    async def cmd(self, method, timeout=90, **params):
        self.i += 1
        mid = self.i
        await self.ws.send(json.dumps({"id": mid, "method": method, "params": params}))
        while True:
            m = json.loads(await asyncio.wait_for(self.ws.recv(), timeout))
            if m.get("id") == mid:
                if "error" in m:
                    raise RuntimeError(f"{method}: {m['error']}")
                return m.get("result", {})
            if "method" in m:
                self.queue.append(m)

    async def event(self, name, timeout=30):
        for m in self.queue:
            if m.get("method") == name:
                self.queue.remove(m)
                return m["params"]
        loop = asyncio.get_event_loop()
        deadline = loop.time() + timeout
        while True:
            left = deadline - loop.time()
            if left <= 0:
                return None
            try:
                m = json.loads(await asyncio.wait_for(self.ws.recv(), left))
            except asyncio.TimeoutError:
                return None
            if m.get("method") == name:
                return m["params"]
            if "method" in m:
                self.queue.append(m)

    async def eval(self, expr, await_=True):
        r = await self.cmd(
            "Runtime.evaluate",
            expression=expr,
            returnByValue=True,
            awaitPromise=await_,
            userGesture=True,
        )
        res = r.get("result", {})
        if res.get("subtype") == "error":
            raise RuntimeError(res.get("description", "js error"))
        return res.get("value")

    async def tap(self, x, y):
        await self.cmd("Input.dispatchMouseEvent", type="mouseMoved", x=x, y=y)
        await asyncio.sleep(0.15)
        await self.cmd(
            "Input.dispatchMouseEvent",
            type="mousePressed",
            x=x,
            y=y,
            button="left",
            clickCount=1,
            buttons=1,
        )
        await asyncio.sleep(0.08)
        await self.cmd(
            "Input.dispatchMouseEvent",
            type="mouseReleased",
            x=x,
            y=y,
            button="left",
            clickCount=1,
            buttons=0,
        )

    async def goto(self, url, wait=12):
        await self.cmd("Page.navigate", url=url)
        await asyncio.sleep(wait)
        return await self.eval("location.href")


def run(fn, port=None):
    async def wrapper():
        async with websockets.connect(page_ws(port), max_size=80 * 1024 * 1024) as ws:
            c = CDP(ws)
            for d in ("Page", "Runtime", "DOM", "Network"):
                try:
                    await c.cmd(f"{d}.enable")
                except Exception:
                    pass
            return await fn(c)

    return asyncio.run(wrapper())


class Reconnecting:
    """Re-open the CDP socket when Chrome drops it mid-wait.

    A 13 s render keeps the page busy for minutes, and Chrome sometimes closes
    the debugger connection in the middle. The page itself keeps working, so
    reconnecting and retrying the call is enough.
    """

    def __init__(self, port=None):
        self.port = port or PORT
        self.ws = None
        self.c = None

    async def __aenter__(self):
        await self._connect()
        return self

    async def __aexit__(self, *exc):
        try:
            if self.ws:
                await self.ws.close()
        except Exception:
            pass

    async def _connect(self):
        self.ws = await websockets.connect(
            page_ws(self.port), max_size=80 * 1024 * 1024
        )
        self.c = CDP(self.ws)
        for d in ("Page", "Runtime", "DOM"):
            try:
                await self.c.cmd(f"{d}.enable")
            except Exception:
                pass

    async def call(self, fn, attempts=8):
        last = None
        for i in range(attempts):
            try:
                return await fn(self.c)
            except (
                ConnectionClosed,
                EOFError,
                asyncio.TimeoutError,
                ConnectionResetError,
            ) as e:
                last = e
                try:
                    await self.ws.close()
                except Exception:
                    pass
                await asyncio.sleep(2 + i)
                try:
                    await self._connect()
                except Exception as e2:
                    last = e2
        raise last


def run_resilient(fn, port=None):
    """Run fn(reconnecting) and survive dropped CDP connections."""

    async def wrapper():
        async with Reconnecting(port) as r:
            return await fn(r)

    return asyncio.run(wrapper())
