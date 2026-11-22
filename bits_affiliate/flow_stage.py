"""
Stage 2: Google Flow turns the storyboard image + Flow prompt into a video.

Spec final: Video · Bahan · 9:16 · 720p · x1 · Omni 1.2 Flash ·
13 dtk (48 kredit), dengan fallback 10 dtk untuk akun yang cap-nya
lebih rendah — durasi diUKUR dari slider Flow per akun (probe), bukan
ditebak dari label langganan.

Durasi lewat slider ``input.mdc-slider__input``; nilai mentah = detik - 3
(13 dtk -> nilai 10). Chip composer dibaca ulang setelah panel ditutup:
dialah sumber kebenaran.
"""

import asyncio
import json
import os
import re
import shutil
import subprocess
import sys
import time

from . import flow_cdp as fc

APP = "https://flow.google.com/"
MODEL_LABEL = "Omni 1.2 Flash"
SECONDS = 13
MIN_SECONDS = 10
ASPECT = "9:16"
RESOLUTION = "720p"
OUTPUTS = 1
COST = 48
STEP = [0]


def pick_seconds(slider_max):
    """Durasi terbaik yang sanggup dilakukan akun; 0 = tidak ada yang cocok."""
    for s in (SECONDS, MIN_SECONDS):
        if slider_max >= s - 3:
            return s
    return 0


def parse_cost(text):
    """'Generating will use 48 credits' / '48 kredit' -> 48; gagal -> 0."""
    m = re.search(r"(\d+)\s*(credits|kredit)", text or "", re.I)
    return int(m.group(1)) if m else 0


class FlowSetupError(SystemExit):
    """Gagal langkah Flow SEBELUM generate — aman untuk rotasi akun lain.

    Setelah generate (biaya durasi terpilih hangus), kegagalan
    render/unduh TIDAK boleh memicu rotasi: akun lain berarti membakar
    biaya yang sama sekali lagi.
    """


def log(msg):
    """Progress goes to stderr so piping stdout through tail still shows it."""
    STEP[0] += 1
    print(f"[flow {STEP[0]:02d}] {msg}", file=sys.stderr, flush=True)


# Flow localises every label; click by stable class plus these aliases.
ALIASES = {
    "Video": ["Video"],
    "Ingredients": ["Bahan", "Ingredients"],
    "x1": ["x1"],
    "Upload media": ["Upload media", "Unggah media"],
    "Start": ["Mulai pembuatan", "Start generation", "Mulai"],
}

TOGGLE_JS = r"""
(() => {
  const wants = %s;
  const norm = (s) => (s || '').replace(/\s+/g, ' ').trim();
  const btns = [...document.querySelectorAll(
    '.mat-button-toggle-button,button[radio],[role="menuitem"]')];
  const hit = (t) => wants.some((w) => t === w || t.endsWith(' ' + w) || t.includes(w));
  const b = btns.find((e) => hit(norm(e.innerText).replace(/^\S+\s+/, '')))
         || btns.find((e) => hit(norm(e.innerText)));
  if (!b) return JSON.stringify({ok: false,
    avail: btns.map((e) => norm(e.innerText)).slice(0, 20)});
  b.click();
  return JSON.stringify({ok: true, clicked: norm(b.innerText)});
})()
"""

MENU_JS = r"""
(() => {
  const wants = %s;
  const norm = (s) => (s || '').replace(/\s+/g, ' ').trim();
  const hit = (t) => wants.some((w) => t.includes(w));
  const items = [...document.querySelectorAll(
    '[role="menuitem"],.mat-mdc-menu-item,'
    + 'mat-mdc-menu-item,.mat-mdc-menu-panel button')];
  const item = items.find((e) => hit(norm(e.innerText)));
  if (!item) {
    return JSON.stringify({ok: false,
      avail: items.map((e) => norm(e.innerText)).filter(Boolean).slice(0, 12)});
  }
  item.click();
  return JSON.stringify({ok: true, clicked: norm(item.innerText).slice(0, 60)});
})()
"""


def _aliases(label):
    return ALIASES.get(label, [label])


async def _click(c, label, menu=False, tolerant=False, wait=0):
    """Klik toggle/menu; `wait` = percobaan ulang tiap 2 dtk sebelum menyerah.

    `tolerant=True` -> kontrol yang memang tidak ada dilewati (mis. pilihan
    resolusi terkunci di tier tertentu); chip tetap jadi sumber kebenaran.
    """
    js = MENU_JS if menu else TOGGLE_JS
    r = {}
    for _ in range(wait + 1):
        r = json.loads(await c.eval(js % json.dumps(_aliases(label))))
        if r.get("ok"):
            await asyncio.sleep(2)
            return r
        await asyncio.sleep(2)
    if tolerant:
        return None
    raise FlowSetupError(f"tidak bisa klik {label!r}; terlihat: {r.get('avail')}")


async def _dismiss(c):
    """Onboarding / changelog dialogs swallow clicks on a fresh project."""
    for _ in range(5):
        r = await c.eval(r"""
        (() => {
          const want = ['get started', 'close', 'dismiss', 'got it', 'ok', 'skip',
                        'mulai', 'tutup', 'mengerti', 'lewati'];
          const b = [...document.querySelectorAll('button')].find((e) =>
            want.includes((e.innerText || '').trim().toLowerCase()) && e.offsetParent);
          if (b) { b.click(); return 'clicked'; }
          return document.querySelector('mat-dialog-container') ? 'left' : 'clear';
        })()
        """)
        if r == "clear":
            return
        await asyncio.sleep(2)
    for ev in ("keyDown", "keyUp"):
        await c.cmd(
            "Input.dispatchKeyEvent",
            type=ev,
            key="Escape",
            code="Escape",
            windowsVirtualKeyCode=27,
        )
    await asyncio.sleep(1)


async def _new_project(c):
    await c.goto(APP, wait=12)
    loc = await c.eval("location.href") or ""
    if "/about" in loc or "/?pli=" in loc:
        raise FlowSetupError(
            "sesi Flow untuk akun ini logout. Jalankan: bits-login <email>"
        )
    await _dismiss(c)
    r = await c.eval(r"""
    (() => {
      const b = document.querySelector('.mdc-fab')
             || [...document.querySelectorAll('button,a')].find((e) =>
                  /project baru|proyek baru|projek baru|new project/i
                    .test(e.innerText || ''));
      if (!b) return null;
      b.click();
      return (b.innerText || '').replace(/\s+/g, ' ').trim();
    })()
    """)
    if not r:
        raise FlowSetupError("tombol project baru tidak ditemukan")
    await asyncio.sleep(14)
    url = await c.eval("location.href") or ""
    if "/project/" not in url:
        raise FlowSetupError(f"project baru tidak terbuka: {url}")
    return url


async def _open_settings(c):
    # panel bisa sudah terbuka dari percobaan sebelumnya — klik trigger
    # ulang justru MENUTUP panel; coba klik Video langsung sebagai tes
    r = json.loads(await c.eval(TOGGLE_JS % json.dumps(_aliases("Video"))))
    if r.get("ok"):
        await asyncio.sleep(2)
        return "panel terbuka"
    await _dismiss(c)
    r = None
    # tombol setelan bisa muncul telat setelah project baru dimuat;
    # cek sekali jalan pernah melewatkan halaman yang masih meload
    for _ in range(8):
        r = await c.eval(r"""
        (() => {
          const b = document.querySelector('.settings-trigger-button')
                 || [...document.querySelectorAll('button')].find((e) =>
                      /^(🍌\s*)?(Nano Banana|Omni|Veo)/
                        .test((e.innerText || '').trim()));
          if (!b) return null;
          b.click();
          return (b.innerText || '').replace(/\s+/g, ' ').trim();
        })()
        """)
        if r:
            break
        await asyncio.sleep(2)
    if not r:
        raise FlowSetupError("tombol setelan tidak ditemukan")
    await asyncio.sleep(3)
    return r


async def _set_duration(c, seconds):
    """Slider durasi Omni 1.2: nilai mentah = detik - 3 (10 -> 13 dtk).

    Cap durasi beda-beda per akun (tier/eksperimen) — jangan ditebak dari
    label langganan: baca atribut max slider (kebenaran mutlak) dan tolak
    akun yang mentok SEBELUM ada biaya.
    """
    cap = await c.eval(
        "(() => { const i = document.querySelector("
        "'input.mdc-slider__input');"
        " return i ? Number(i.max || 0) : null; })()"
    )
    if cap is None:
        raise FlowSetupError("slider durasi tidak ditemukan (mode Video?)")
    if cap < seconds - 3:
        raise FlowSetupError(
            f"akun ini durasi maksimal {cap + 3} dtk "
            f"(butuh {seconds} dtk — tier akun tidak mendukung)"
        )
    ok = await c.eval(
        r"""
    (() => {
      const i = document.querySelector('input.mdc-slider__input');
      if (!i) return false;
      const set = Object.getOwnPropertyDescriptor(
        window.HTMLInputElement.prototype, 'value').set;
      set.call(i, %d);
      i.dispatchEvent(new Event('input', {bubbles: true}));
      i.dispatchEvent(new Event('change', {bubbles: true}));
      return true;
    })()
    """
        % (seconds - 3)
    )
    if not ok:
        raise FlowSetupError("slider durasi tidak ditemukan (mode Video?)")
    await asyncio.sleep(2)


async def _apply_settings(c, seconds=SECONDS):
    """Video, Bahan, 9:16, 720p, x1, Omni 1.2 Flash, durasi `seconds`."""
    row = await _open_settings(c)
    await _click(c, "Video", wait=3)
    await _click(c, "Ingredients", wait=3)
    await _click(c, ASPECT, wait=3)
    await _click(c, RESOLUTION, tolerant=True, wait=5)
    await _click(c, f"x{OUTPUTS}", wait=3)
    # model picker ada di balik dropdown
    await c.eval(r"""
    (() => {
      const b = [...document.querySelectorAll('button')].find((e) =>
        /rangkaian model|model series|pilih model|select model/i
          .test(e.getAttribute('aria-label') || ''))
        || [...document.querySelectorAll('button')].find((e) =>
             /arrow_drop_down/.test(e.innerText || '')
             && /(Omni|Veo|Nano)/.test(e.innerText || ''));
      if (b) b.click();
      return true;
    })()
    """)
    await asyncio.sleep(2)
    await _click(c, MODEL_LABEL, menu=True)
    await asyncio.sleep(2)
    await _set_duration(c, seconds)
    cost = await c.eval(
        "(document.querySelector('.credit-cost-label') || {}).innerText || ''"
    )
    for ev in ("keyDown", "keyUp"):
        await c.cmd(
            "Input.dispatchKeyEvent",
            type=ev,
            key="Escape",
            code="Escape",
            windowsVirtualKeyCode=27,
        )
    await asyncio.sleep(2)
    chip = await c.eval(
        "(document.querySelector('.settings-trigger-button') || {}).innerText || ''"
    )
    return {"row": row, "cost": cost, "chip": chip.replace("\n", " ")}


async def _ingredients_button_box(c):
    return await c.eval(r"""
    (() => {
      const b = [...document.querySelectorAll('button')].find((e) =>
        /add ingredients|tambahkan bahan|bahan ke kotak/i
          .test(e.getAttribute('aria-label') || ''));
      if (!b) return null;
      const r = b.getBoundingClientRect();
      return [Math.round(r.x + r.width / 2), Math.round(r.y + r.height / 2)];
    })()
    """)


async def _upload(c, image_path):
    await c.cmd("Page.setInterceptFileChooserDialog", enabled=True)
    box = await _ingredients_button_box(c)
    if not box:
        raise FlowSetupError("tombol bahan tidak ditemukan")
    await c.tap(*box)
    # panel library lambat, dan tap bisa salah kirim (menutup panel yang
    # terlanjur terbuka, mis. setelah panel setelan) -> tunggu .upload-text
    # dengan tap ulang, jangan sekali jalan lalu menyerah
    ok = ""
    for _ in range(6):
        await asyncio.sleep(2)
        ok = await c.eval("document.querySelector('.upload-text') ? 'yes' : 'no'")
        if ok == "yes":
            break
        box = await _ingredients_button_box(c)
        if box:
            await c.tap(*box)
    if ok != "yes":
        raise FlowSetupError("panel bahan tidak menampilkan Upload media")
    r = await c.eval(r"""
    (() => {
      // the label sits in <span class="upload-text">; click its clickable parent
      const el = document.querySelector('.upload-text')
             || [...document.querySelectorAll('span,div,button')].find((n) =>
                  /^(upload media|unggah media)$/i
                    .test((n.innerText || '').replace(/\s+/g, ' ').trim()));
      if (!el) return 'missing';
      const target = el.closest(
        'button,[role="button"],mat-list-item,.upload-item') || el;
      (target.querySelector('button') || target).click();
      return 'clicked';
    })()
    """)
    if r != "clicked":
        raise FlowSetupError("menu Upload media tidak ditemukan")
    ev = await c.event("Page.fileChooserOpened", timeout=20)
    if not ev:
        raise FlowSetupError("file chooser tidak terbuka")
    await c.cmd(
        "DOM.setFileInputFiles",
        files=[os.path.abspath(image_path)],
        backendNodeId=ev["backendNodeId"],
    )
    await asyncio.sleep(12)


async def _attach(c):
    """Uploaded assets are only in the library; click to put them in the prompt."""
    if not await c.eval("document.querySelectorAll('.asset-thumbnail').length"):
        box = await _ingredients_button_box(c)
        if box:
            await c.tap(*box)
            await asyncio.sleep(4)
    r = await c.eval(r"""
    (() => {
      const add = [...document.querySelectorAll('button,a,[role="button"]')].find((e) =>
        /tambahkan ke perintah|add to prompt/i
          .test(e.innerText || e.getAttribute('aria-label') || ''));
      if (add) { add.click(); return 'via add button'; }
      const th = document.querySelector('.asset-thumbnail, img.thumbnail');
      if (th) { th.click(); return 'via thumbnail'; }
      return 'none';
    })()
    """)
    if r == "none":
        raise FlowSetupError("aset tidak bisa ditempel ke prompt")
    await asyncio.sleep(3)
    chips = await c.eval(
        "document.querySelectorAll('flow-ingredient-chip,"
        ".reference-ingredient-chip').length"
    )
    await c.eval(r"""
    (() => {
      const b = [...document.querySelectorAll('button')].find((e) =>
        /^(cancel|batal|tutup|close)$/i.test((e.innerText || '').trim()));
      if (b) b.click();
      return true;
    })()
    """)
    await asyncio.sleep(2)
    return chips


async def _type_prompt(c, text):
    box = await c.eval(r"""
    (() => {
      const cands = [...document.querySelectorAll('[contenteditable="true"]')];
      const e = cands.find((n) => /what do you want to create|apa yang ingin anda buat/i
        .test(n.getAttribute('aria-label') || '')) || cands[cands.length - 1];
      if (!e) return null;
      const r = e.getBoundingClientRect();
      return {x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2)};
    })()
    """)
    if not box:
        raise FlowSetupError("kotak prompt Flow tidak ditemukan")
    await c.tap(box["x"], box["y"])
    await asyncio.sleep(1)
    await c.eval(r"""
    (() => {
      const cands = [...document.querySelectorAll('[contenteditable="true"]')];
      const e = cands.find((n) => /what do you want to create|apa yang ingin anda buat/i
        .test(n.getAttribute('aria-label') || '')) || cands[cands.length - 1];
      e.focus();
      const rng = document.createRange();
      const sel = window.getSelection();
      rng.selectNodeContents(e);
      rng.collapse(false);
      sel.removeAllRanges();
      sel.addRange(rng);
      return true;
    })()
    """)
    await c.cmd("Input.insertText", text=text)
    await asyncio.sleep(2)


async def _credit_state(c):
    """Read the composer's generate control and any credit warning.

    Flow swaps the send button for a `prompt-warning-button` ("Peringatan poin
    tidak cukup") when the account cannot pay for the selected settings. That is
    a hard stop, not a missing element.
    """
    return json.loads(
        await c.eval(r"""
    (() => {
      const warn = document.querySelector('.prompt-warning-button');
      const start = [...document.querySelectorAll('button')].find((e) =>
        /mulai pembuatan|start generation|kirim|send/i
          .test(e.getAttribute('aria-label') || ''));
      return JSON.stringify({
        warning: warn ? (warn.getAttribute('aria-label') || 'peringatan') : '',
        startFound: !!start,
        startDisabled: start
          ? !!(start.disabled || start.getAttribute('aria-disabled') === 'true')
          : null
      });
    })()
    """)
    )


async def _generate(c, cost=""):
    st = await _credit_state(c)
    if st["warning"]:
        raise SystemExit(
            f"kredit tidak cukup untuk {COST} kredit "
            f"({st['warning']}; panel: {cost or 'biaya tidak terbaca'})"
        )
    if not st["startFound"]:
        raise FlowSetupError("tombol Mulai pembuatan tidak muncul (prompt kosong?)")
    if st["startDisabled"]:
        raise FlowSetupError(
            "tombol Mulai pembuatan tidak aktif (prompt belum terisi?)"
        )
    r = await c.eval(r"""
    (() => {
      const b = [...document.querySelectorAll('button')].find((e) =>
        /mulai pembuatan|start generation/i.test(e.getAttribute('aria-label') || ''));
      if (!b) return 'missing';
      b.click();
      return 'clicked';
    })()
    """)
    if r != "clicked":
        raise FlowSetupError(f"tidak bisa mulai pembuatan: {r}")


async def _wait_render(c, timeout=900, interval=15):
    """Poll the project page until the generated 9:16 clip thumbnail appears.

    The clip's thumbnail is an ``/asb/`` image taller than wide. While waiting,
    watch for a logout: an abandoned session must not look like a slow render.
    """
    elapsed = 0
    while elapsed < timeout:
        st = json.loads(
            await c.eval(r"""
        (() => {
          const th = [...document.querySelectorAll('img')]
            .find((i) => /\/asb\//.test(i.src || '')
                   && i.naturalHeight > i.naturalWidth);
          const out = /flow\.google\.com\/(about|$)/.test(location.href)
            || /(Buat dengan|Create with) Google Flow/i
                 .test(document.body.innerText || '');
          return JSON.stringify({thumb: th ? (th.src || '').slice(0, 90) : null,
                                 out});
        })()
        """)
        )
        if st.get("out"):
            raise SystemExit(
                "sesi Flow logout di tengah render. Login ulang: bits-login <email>"
            )
        if st.get("thumb"):
            return st["thumb"]
        await asyncio.sleep(interval)
        elapsed += interval
    raise SystemExit(f"render tidak selesai dalam {timeout}s")


async def _open_clip_editor(c, tries=6):
    """Click the clip thumbnail to land on /edit/<clip-id>.

    Tanpa fallback "Semua media": daftar itu memuat clip project LAIN juga,
    dan salah klik berarti mengunduh video yang salah setelah kredit terbakar.
    _wait_render sudah memastikan thumbnail ada di halaman project, jadi kalau
    belum ketemu cukup tunggu dan ulang di halaman yang sama.
    """
    for _ in range(tries):
        clicked = await c.eval(r"""
        (() => {
          const th = [...document.querySelectorAll('img')]
            .find((i) => /\/asb\//.test(i.src || '')
                   && i.naturalHeight > i.naturalWidth);
          if (!th) return 'no thumb';
          const tile = th.closest(
            '[class*=tile],[class*=card],[class*=item],button,a') || th;
          tile.click();
          return 'clicked';
        })()
        """)
        if clicked == "clicked":
            for _ in range(10):
                await asyncio.sleep(2)
                url = await c.eval("location.href") or ""
                if "/edit/" in url:
                    return url
        await asyncio.sleep(5)
    raise SystemExit("clip editor tidak terbuka dari thumbnail project ini")


async def _download_clip(c, downloads_dir, out_path, before, timeout=240):
    """Unduh clip lewat tombol Download media -> pilih 720p.

    Tombol "Download media" tidak langsung mengunduh: ia membuka menu kualitas
    (270p GIF / 720p Ukuran asli / 1080p berbayar / 4K). Skill selalu memilih
    720p -- resolusi asli hasil render, tanpa kredit tambahan.
    """
    r = await c.eval(r"""
    (() => {
      const b = [...document.querySelectorAll('button,[role="button"]')].find((e) =>
        /unduh media|download media/i.test(e.getAttribute('aria-label') || ''));
      if (!b) return 'no download button';
      b.click();
      return 'clicked';
    })()
    """)
    if r != "clicked":
        raise SystemExit(f"tombol Download media tidak ditemukan: {r}")

    await asyncio.sleep(3)
    q = await c.eval(r"""
    (() => {
      const btns = [...document.querySelectorAll(
        'button,[role="menuitem"],.mat-mdc-menu-item')];
      const opt = btns.find((e) =>
        /^\s*720p/.test((e.innerText || '').replace(/\s+/g, ' ')))
               || btns.find((e) => /ukuran asli|original size/i
                    .test(e.innerText || e.getAttribute('aria-label') || ''));
      if (!opt) {
        return JSON.stringify({ok: false,
          avail: btns.map((e) => (e.innerText || '').replace(/\s+/g, ' ').trim())
                      .filter(Boolean).slice(0, 10)});
      }
      opt.click();
      return JSON.stringify({ok: true,
        picked: (opt.innerText || '').replace(/\s+/g, ' ').trim().slice(0, 40)});
    })()
    """)
    info = json.loads(q)
    if not info.get("ok"):
        raise SystemExit(f"opsi 720p tidak ada di menu unduh: {info.get('avail')}")

    deadline = time.time() + timeout
    last_size = {}
    while time.time() < deadline:
        await asyncio.sleep(3)
        new_files = [f for f in os.listdir(downloads_dir) if f not in before]
        cands = []
        for f in new_files:
            if f.endswith((".crdownload", ".tmp")):
                continue
            path = os.path.join(downloads_dir, f)
            try:
                size = os.path.getsize(path)
            except OSError:
                # file bisa menghilang antara listdir dan stat (rename .crdownload)
                continue
            if size <= 100000:
                continue
            # ukuran harus sama di dua polling berturut-turut = file selesai
            if last_size.get(f) == size:
                cands.append(path)
            else:
                last_size[f] = size
        if cands:
            src = max(cands, key=os.path.getsize)
            shutil.copyfile(src, out_path)
            return {
                "via": "Download media -> 720p",
                "file": os.path.basename(src),
                "picked": info.get("picked"),
                "bytes": os.path.getsize(out_path),
            }
    raise SystemExit(
        f"unduhan 720p tidak muncul dalam {timeout}s "
        f"(folder: {sorted(os.listdir(downloads_dir))[-3:]})"
    )


async def _download_from_tile(c, downloads_dir, out_path, before, timeout=240):
    """Unduh tanpa masuk editor: menu tile 'Opsi lainnya' -> Download -> 720p.

    Klik thumbnail di halaman project sering sekadar membuka preview, bukan
    editor -- jalur menu tile inilah yang terbukti berjalan di lapangan.
    """
    r = await c.eval(r"""
    (() => {
      const th = [...document.querySelectorAll('img')]
        .find((i) => /\/asb\//.test(i.src || '') && i.naturalHeight > i.naturalWidth);
      if (!th) return 'no thumb';
      const tile = th.closest('[class*=tile],[class*=card],[class*=item]') || th;
      const btn = [...tile.querySelectorAll('button')]
        .find((e) => /opsi lainnya|more options/i
          .test(e.getAttribute('aria-label') || ''));
      if (!btn) return 'no menu button';
      btn.click();
      return 'open';
    })()
    """)
    if r != "open":
        raise SystemExit(f"menu tile tidak terbuka: {r}")
    await asyncio.sleep(2)
    klik = await c.eval(r"""
    (() => {
      const items = [...document.querySelectorAll(
          '[role="menuitem"],.mat-mdc-menu-item')]
        .filter((e) => /download|unduh/i.test(e.innerText || ''));
      if (!items.length) return 'no download item';
      items[0].click();
      return 'clicked';
    })()
    """)
    if klik != "clicked":
        raise SystemExit(f"item Download tidak ada di menu tile: {klik}")

    # menu kualitas bisa muncul; pilih 720p kalau ada, kalau tidak biarkan
    # unduhan langsung berjalan
    deadline_menu = time.time() + 30
    while time.time() < deadline_menu:
        await asyncio.sleep(2)
        picked = await c.eval(r"""
        (() => {
          const opt = [...document.querySelectorAll(
              'button,[role="menuitem"],.mat-mdc-menu-item')]
            .find((e) => /^\s*720p/.test((e.innerText || '')
                      .replace(/\s+/g, ' '))
                   || /ukuran asli|original size/i.test(
                        e.innerText || e.getAttribute('aria-label') || ''));
          if (!opt) return null;
          opt.click();
          return (opt.innerText || '').replace(/\s+/g, ' ').trim().slice(0, 40);
        })()
        """)
        if picked:
            break

    src = await _await_new_file(downloads_dir, before, timeout)
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    shutil.copyfile(src, out_path)
    return {
        "via": "menu tile -> Download -> 720p",
        "file": os.path.basename(src),
        "picked": picked or "direct",
        "bytes": os.path.getsize(out_path),
    }


async def _await_new_file(downloads_dir, before, timeout):
    """File baru >100 KB yang ukurannya stabil dua polling berturut-turut."""
    deadline = time.time() + timeout
    last_size = {}
    while time.time() < deadline:
        await asyncio.sleep(3)
        for f in os.listdir(downloads_dir):
            if f in before or f.endswith((".crdownload", ".tmp")):
                continue
            path = os.path.join(downloads_dir, f)
            try:
                size = os.path.getsize(path)
            except OSError:
                # file bisa menghilang antara listdir dan stat (rename .crdownload)
                continue
            if size <= 100000:
                continue
            # ukuran harus sama di dua polling berturut-turut = file selesai
            if last_size.get(f) == size:
                return path
            last_size[f] = size
    raise SystemExit(
        f"unduhan tidak muncul dalam {timeout}s "
        f"(folder: {sorted(os.listdir(downloads_dir))[-3:]})"
    )


def ffprobe(path):
    try:
        r = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=width,height:format=duration",
                "-of",
                "json",
                path,
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
        d = json.loads(r.stdout or "{}")
        st = (d.get("streams") or [{}])[0]
        return {
            "w": st.get("width"),
            "h": st.get("height"),
            "seconds": round(float(d.get("format", {}).get("duration") or 0), 2),
        }
    except Exception as e:
        return {"error": str(e)}


# ------------------------------------------------------------------- stage


def probe_account(account, visible=False):
    """Ukur cap durasi + biaya akun di Flow. Return {"seconds", "cost"}.

    Berhenti sebelum upload/prompt/billing — tidak menyentuh kredit.
    Durasi terbaik yang didukung dipilih otomatis (13 dtk, kalau tidak
    10 dtk). Dipanggil pipeline sekali per akun; hasilnya dicache di
    caps.json sehingga run berikutnya tak probe ulang.
    """
    port = fc.port_for(account)
    fc.stop_chrome(port)
    time.sleep(1)
    fc.ensure_chrome(account, port, headless=not visible)

    async def body(r):
        await r.call(lambda conn: _new_project(conn), attempts=3)
        try:
            st = await r.call(lambda conn: _apply_settings(conn, SECONDS), attempts=3)
            return {"seconds": SECONDS, "cost": parse_cost(st["cost"])}
        except FlowSetupError as e:
            if "durasi maksimal" not in str(e):
                raise
        st = await r.call(lambda conn: _apply_settings(conn, MIN_SECONDS), attempts=3)
        return {"seconds": MIN_SECONDS, "cost": parse_cost(st["cost"])}

    try:
        return fc.run_resilient(body, port)
    finally:
        fc.stop_chrome(port)


def run_stage(
    account,
    storyboard,
    flow_md,
    out_path,
    dry_run=False,
    timeout=900,
    visible=True,
    downloads_dir=None,
    seconds=None,
):
    """Render video Flow dari storyboard + prompt (spec adaptif durasi)."""
    seconds = seconds or SECONDS
    port = fc.port_for(account)
    downloads_dir = downloads_dir or os.path.join(fc.HOME, "downloads")
    os.makedirs(downloads_dir, exist_ok=True)
    prompt = open(flow_md, encoding="utf-8").read().strip()

    fc.stop_chrome(port)
    time.sleep(1)
    fc.ensure_chrome(account, port, headless=not visible)

    async def body(r):
        # langkah ber-side-effect tetap satu percobaan per koneksi; kalau CDP
        # putus di tengah, r.call sambung ulang dan ulang langkah tsb
        project = await r.call(lambda conn: _new_project(conn), attempts=4)
        settings = await r.call(lambda conn: _apply_settings(conn, seconds), attempts=4)
        if str(seconds) not in settings["chip"]:
            raise FlowSetupError(
                f"durasi tidak tersimpan: chip berbunyi {settings['chip']!r}, "
                f"mau {seconds} dtk"
            )
        log(f"setelan jadi: {settings['chip']} | {settings['cost']}")
        log(f"upload storyboard: {os.path.basename(storyboard)}")
        await r.call(lambda conn: _upload(conn, storyboard), attempts=4)
        # tulis prompt SEBELUM tempel bahan: laporan lapangan, paste teks
        # bisa menghapus chip (editor Flow me-render ulang kotak prompt)
        log(f"tulis prompt ({len(prompt)} char)")
        await r.call(lambda conn: _type_prompt(conn, prompt), attempts=4)
        log("tempel bahan ke prompt")
        chips = await r.call(lambda conn: _attach(conn), attempts=4)
        if chips < 1:
            await asyncio.sleep(3)
            chips = await r.call(lambda conn: _attach(conn), attempts=4)
        if chips < 1:
            raise FlowSetupError("bahan tidak menempel ke prompt")
        log(f"bahan menempel: {chips} chip")
        # verifikasi akhir SEBELUM bayar: teks dan chip harus ada dua-duanya
        nchar = await r.call(
            lambda conn: conn.eval(
                "(document.querySelector('[contenteditable=\"true\"]')"
                "?.innerText || '').length"
            )
        )
        if nchar < 200:
            raise FlowSetupError(
                f"teks prompt hilang setelah tempel bahan ({nchar} char)"
            )

        result = {"project": project, "settings": settings, "chips": chips}
        if dry_run:
            result["stopped"] = "dry-run"
            return result

        log("klik Mulai pembuatan")
        await r.call(lambda conn: _generate(conn, settings.get("cost", "")), attempts=4)
        log(f"menunggu render selesai (polling tiap 15 dtk, maks {timeout} dtk)")

        async def wait(conn):
            return await _wait_render(conn, timeout=timeout)

        await r.call(wait, attempts=10)
        log("thumbnail clip muncul; buka editor")

        before = set(os.listdir(downloads_dir))

        async def open_edit(conn):
            return await _open_clip_editor(conn)

        try:
            clip_url = await r.call(open_edit, attempts=2)
            log(f"editor: {clip_url}")

            async def dl(conn):
                return await _download_clip(
                    conn, downloads_dir, out_path, before, timeout=240
                )

            dl_info = await r.call(dl, attempts=4)
        except SystemExit as e:
            # klik thumbnail sering sekadar membuka preview, bukan editor.
            # Jalur terbukti: menu tile "Opsi lainnya -> Download -> 720p".
            if "clip editor" not in str(e) and "thumbnail" not in str(e):
                raise
            log(
                f"editor tidak terbuka ({e}); unduh dari menu tile"
                " Opsi lainnya -> Download"
            )

            async def dl_tile(conn):
                return await _download_from_tile(
                    conn, downloads_dir, out_path, before, timeout=240
                )

            dl_info = await r.call(dl_tile, attempts=3)

        log(f"video terunduh: {dl_info['bytes'] // 1024} KB (via {dl_info['via']})")

        result["download"] = dl_info
        result["video"] = {"path": out_path, "probe": ffprobe(out_path)}
        return result

    try:
        return fc.run_resilient(body, port)
    finally:
        fc.stop_chrome(port)
