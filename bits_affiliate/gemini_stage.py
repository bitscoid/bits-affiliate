"""
Stage 1: Gemini writes the storyboard image and the Google Flow prompt.

Flow of events, all over CDP:
  1. open gemini.google.com/app
  2. click "Upload & alat" -- this is what instantiates the file inputs; they do
     not exist in the DOM before that click
  3. feed the images straight into input[type=file][accept*="image"]
  4. paste the reference prompt into the ProseMirror editor and send
  5. wait for the reply to settle
  6. pull the storyboard image bytes and the fenced PROMPT GOOGLE FLOW block

No credentials are handled here; the Chrome profile is already signed in.
"""

import asyncio
import base64
import json
import os
import re
import shutil
import sys
import time

from websockets.exceptions import ConnectionClosed

from . import flow_cdp as fc

APP = "https://gemini.google.com/app"  # terbukti bisa generate gambar

# Batas tunggu. Chrome lambat pada mesin tanpa GPU, jadi semua jeda diberi nama.
COMPOSER_WAIT = 18  # dtk: composer siap setelah navigasi
FILE_INPUT_WAIT = 15  # dtk: input[type=file] muncul setelah menu diklik
PREVIEW_WAIT = 60  # dtk: thumbnail blob muncul (30 x 2)
REPLY_POLL = 10  # dtk: interval cek balasan
REPLY_QUIET = 4  # putaran stabil sebelum dianggap selesai
DOWNLOAD_WAIT = 80  # dtk: file storyboard selesai ditulis (40 x 2)
STALL_ROUNDS = 36  # putaran diam SAAT 'menulis' -> macet (6 mnt: Gemini bisa
#           diam >5 mnt sebelum token pertama / gambar muncul; reset baru
#           ketahuan ~310 dtk lewat penyusutan halaman)
ATTEMPTS = 2  # percobaan percakapan; Gemini kadang tidak pernah mulai
MIN_IMAGE_BYTES = 20000  # di bawah ini: ikon/preview, bukan storyboard
MIN_RENDER_PX = 200  # lebar/tinggi TAMPIL minimum; thumbnail sumber ~112 px
TOOL_MENU_WAIT = 5  # putaran menunggu item "Buat gambar" muncul di menu plus
TOOL_POLL = 1  # dtk per putaran menu (test menyetel 0)


class GeminiStalled(SystemExit):
    """Gemini menampilkan tombol Stop tapi tidak pernah mulai menjawab."""


class ReplyIncomplete(SystemExit):
    """Balasan datang tapi bagian wajib (blok PROMPT GOOGLE FLOW) tidak ada."""


# --------------------------------------------------------------- ui helpers


SIGNED_OUT_URL = ("/signin", "accounts.google.com", "/ServiceLogin")


async def _open_app(c, timeout=COMPOSER_WAIT):
    await c.goto(APP, wait=timeout)
    url = await c.eval("location.href") or ""
    if any(m in url for m in SIGNED_OUT_URL):
        raise SystemExit(
            f"Gemini belum login di profil ini ({url[:60]}). "
            "Jalankan bits-login <email> dulu."
        )
    for _ in range(20):
        if await c.eval("!!document.querySelector('[contenteditable=\"true\"]')"):
            return
        await asyncio.sleep(1)
    raise SystemExit("composer Gemini tidak muncul")


async def _reveal_file_inputs(c):
    """The upload menu creates the hidden <input type=file> elements."""
    n = await c.eval("document.querySelectorAll('input[type=file]').length")
    if n:
        return n
    await c.eval(r"""
    (() => {
      const b = [...document.querySelectorAll('button')].find((e) =>
        /upload|unggah/i.test(e.getAttribute('aria-label') || ''));
      if (b) b.click();
      return true;
    })()
    """)
    for _ in range(FILE_INPUT_WAIT):
        await asyncio.sleep(1)
        n = await c.eval("document.querySelectorAll('input[type=file]').length")
        if n:
            return n
    raise SystemExit("input[type=file] Gemini tidak muncul setelah menu Upload")


async def _attach_images(c, paths):
    """Push image files into Gemini's hidden image input."""
    await _reveal_file_inputs(c)
    await c.cmd("DOM.enable")
    doc = await c.cmd("DOM.getDocument", depth=-1, pierce=True)
    node = await c.cmd(
        "DOM.querySelector",
        nodeId=doc["root"]["nodeId"],
        selector='input[type=file][accept*="image"]',
    )
    node_id = node.get("nodeId")
    if not node_id:
        raise SystemExit("input gambar Gemini tidak ditemukan")
    await c.cmd(
        "DOM.setFileInputFiles",
        files=[os.path.abspath(p) for p in paths],
        nodeId=node_id,
    )
    # wait until each file shows up as a blob preview / upload chip
    for _ in range(PREVIEW_WAIT // 2):
        await asyncio.sleep(2)
        n = await c.eval("document.querySelectorAll('img[src^=\"blob:\"]').length")
        if n >= len(paths):
            return n
    raise SystemExit("gambar tidak menempel di composer Gemini")


async def _type_prompt(c, text):
    box = await c.eval(r"""
    (() => {
      const e = document.querySelector('[contenteditable="true"]');
      if (!e) return null;
      const r = e.getBoundingClientRect();
      return {x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2)};
    })()
    """)
    if not box:
        raise SystemExit("editor Gemini tidak ditemukan")
    await c.tap(box["x"], box["y"])
    await asyncio.sleep(1)
    await c.eval(r"""
    (() => {
      const e = document.querySelector('[contenteditable="true"]');
      e.focus();
      const rng = document.createRange();
      const sel = window.getSelection();
      // seleksi SELURUH isi (jangan collapse ke akhir): Input.insertText
      // menggantikan seleksi, jadi ulang langkah tidak menduplikasi prompt
      rng.selectNodeContents(e);
      sel.removeAllRanges();
      sel.addRange(rng);
      return true;
    })()
    """)
    await c.cmd("Input.insertText", text=text)
    await asyncio.sleep(1)


async def _send(c):
    r = await c.eval(r"""
    (() => {
      const b = [...document.querySelectorAll('button')].find((e) =>
        /kirim pesan|send message/i.test(e.getAttribute('aria-label') || ''));
      if (!b) return 'no send button';
      if (b.disabled || b.getAttribute('aria-disabled') === 'true') {
        // disabled SETELAH prompt ditempel biasanya berarti sudah terkirim;
        // retry _send jadi idempoten alih-alih membunuh percobaan yang sukses
        return 'sent';
      }
      b.click();
      return 'sent';
    })()
    """)
    if r != "sent":
        raise SystemExit(f"tidak bisa mengirim ke Gemini: {r}")


# Nama mode per bahasa UI. "Auto" versi Indonesia = "Otomatis"; item panel
# juga membawa ikon + deskripsi (mis. "Bantuan sehari-hari 4 Hydrogen"),
# jadi pencocokan pakai word-boundary, bukan teks persis.
MODE_ALIASES = {
    "auto": r"\b(?:auto|otomatis)\b",
    "otomatis": r"\b(?:auto|otomatis)\b",
    "hydrogen": r"\bhydrogen\b",
}


async def _pick_mode(c, mode):
    """Pilih mode model di pemilih mode (mis. 'Auto' alih-alih 'Hydrogen').

    Tombol pemilih membawa label 'Buka pemilih mode, saat ini X' / 'Open mode
    picker, currently X'. Sudah di mode tujuan -> tidak disentuh. Item tak
    ketemu -> False, jalankan dengan mode bawaan akun (bukan kegagalan).
    """
    label = await c.eval(r"""
    (() => {
      const b = [...document.querySelectorAll('button')].find((e) =>
        /pemilih mode|mode picker/i.test(e.getAttribute('aria-label') || ''));
      return b ? (b.getAttribute('aria-label') || '') : '';
    })()
    """)
    if not label:
        return False
    alias = MODE_ALIASES.get(mode.lower(), re.escape(mode))
    if re.search(r"saat ini \w+|currently \w+", label, re.I):
        if re.search(alias, label, re.I):
            return True
    await c.eval(r"""
    (() => {
      const b = [...document.querySelectorAll('button')].find((e) =>
        /pemilih mode|mode picker/i.test(e.getAttribute('aria-label') || ''));
      if (b) b.click();
      return true;
    })()
    """)
    item = r"""
    (() => {
      const pat = new RegExp(%s, 'i');
      const hit = (e) => {
        const t = ((e.getAttribute('aria-label') || '') + ' '
                  + (e.innerText || '')).replace(/\s+/g, ' ').trim();
        return pat.test(t);
      };
      const it = [...document.querySelectorAll(
          '[role="menuitem"],[role="menuitemradio"],[role="radio"],button')]
        .find((e) => hit(e) && e.offsetParent
                        && !/pemilih mode|mode picker/i.test(
                             e.getAttribute('aria-label') || ''));
      if (!it) return null;
      it.click();
      return 'picked';
    })()
    """ % json.dumps(alias)
    for _ in range(TOOL_MENU_WAIT):
        await asyncio.sleep(TOOL_POLL)
        if await c.eval(item) == "picked":
            await asyncio.sleep(1)
            return True
    return False


async def _pick_create_image(c):
    """Pilih 'Buat gambar' di menu plus agar balasan dijamin ada gambarnya.

    Tanpa ini Gemini kadang menjawab teks saja -- hanya prompt Flow, tanpa
    storyboard -- kegagalan yang paling sering. Item menu render lambat, jadi
    dipolling. False = item tidak ada (UI/mode berbeda) -> lanjut cara lama.
    """
    plus = r"""
    (() => {
      const b = [...document.querySelectorAll('button')].find((e) =>
        /upload (& )?alat|upload .*(and )?tools?/i.test(
          e.getAttribute('aria-label') || e.innerText || ''));
      if (!b) return 'no plus';
      b.click();
      return 'open';
    })()
    """
    if await c.eval(plus) != "open":
        return False
    item = r"""
    (() => {
      const hit = (e) => /buat gambar|create images?/i.test(
        e.getAttribute('aria-label') || e.innerText || '');
      const it = [...document.querySelectorAll(
          '[role="menuitem"],[role="menuitemradio"],button')]
        .find((e) => hit(e) && e.offsetParent);
      if (!it) return null;
      it.click();
      return 'picked';
    })()
    """
    for _ in range(TOOL_MENU_WAIT):
        await asyncio.sleep(TOOL_POLL)
        if await c.eval(item) == "picked":
            await asyncio.sleep(1)
            return True
    # tutup menu yang masih terbuka supaya tidak menutupi composer
    await c.eval(plus)
    return False


async def _reply_state(c):
    """Panjang teks + tombol Stop + jumlah gambar besar.

    Error JS sesaat (document.body null saat reload) -> None: putaran
    dilewati. Error TRANSPORT (CDP putus) -> diteruskan, supaya
    Reconnecting.call sambung ulang — kalau ditelan, loop akan mempoll
    socket mati sampai timeout percuma.
    """
    try:
        return json.loads(
            await c.eval(r"""
        (() => {
          const t = (document.body && document.body.innerText) || '';
          const busy = [...document.querySelectorAll('button')].some((e) =>
            /hentikan|stop|batalkan/i.test(e.getAttribute('aria-label') || ''));
          const imgs = [...document.querySelectorAll('img')]
            .filter((e) => e.naturalWidth >= 700).length;
          return JSON.stringify({len: t.length, busy, imgs});
        })()
        """)
        )
    except (ConnectionClosed, EOFError, OSError, asyncio.TimeoutError):
        raise  # transport mati -> r.call sambung ulang
    except Exception:
        # navigasi/reload di tengah eval. SystemExit (termasuk GeminiStalled)
        # bukan Exception, jadi tetap diteruskan.
        return None


async def _wait_reply(c, timeout=600, quiet_rounds=REPLY_QUIET):
    """Tunggu balasan berhenti tumbuh dan tombol Stop hilang.

    Gemini bisa diam ~40 dtk di tengah jawaban lalu lanjut menulis, jadi progres
    dicetak ke stderr supaya terlihat masih hidup, bukan menggantung.

    Halaman yang tiba-tiba mengecil drastis berarti percakapan di-reset Gemini
    (bukan balasan selesai) -- itu kegagalan, bukan keberhasilan.
    """
    elapsed = 0
    last = -1
    last_img = 0
    peak = 0
    stable = 0
    stalled = 0
    while elapsed < timeout:
        await asyncio.sleep(REPLY_POLL)
        elapsed += REPLY_POLL
        state = await _reply_state(c)
        if state is None:
            print(
                f"[gemini {elapsed:4d} dtk] halaman/ CDP sibuk, ulangi",
                file=sys.stderr,
                flush=True,
            )
            continue
        n = state["len"]
        n_img = state.get("imgs", 0)

        if n and peak and n < peak * 0.5:
            print(
                f"[gemini {elapsed:4d} dtk] halaman mengecil {peak} -> {n} char",
                file=sys.stderr,
                flush=True,
            )
            raise GeminiStalled(
                "Gemini mereset percakapan sebelum selesai "
                f"({peak} -> {n} char). Coba ulang, atau pakai akun lain."
            )
        peak = max(peak, n)

        # gambar bisa muncul duluan sebelum teks bertambah: itu juga progres
        if n == last and n_img == last_img:
            stable += 1
            stalled = stalled + 1 if state["busy"] else 0
        else:
            stable = 0
            stalled = 0
            last = n
            last_img = n_img
        if stalled >= STALL_ROUNDS:
            print(
                f"[gemini {elapsed:4d} dtk] macet: {n} char stabil"
                f" {stalled}x walau masih 'menulis'",
                file=sys.stderr,
                flush=True,
            )
            raise GeminiStalled(
                f"Gemini tidak mulai menjawab selama {elapsed} dtk "
                f"(teks tetap {n} char)"
            )
        print(
            f"[gemini {elapsed:4d} dtk] {n} char"
            f"{f'  {n_img} gambar' if n_img else ''}"
            f"{'  (masih menulis)' if state['busy'] else ''}"
            f"{f'  stabil {stable}x' if stable else ''}",
            file=sys.stderr,
            flush=True,
        )
        if not state["busy"] and stable >= quiet_rounds:
            return True
    raise SystemExit("balasan Gemini tidak selesai dalam batas waktu")


async def _storyboard_images(c):
    """Gambar hasil Gemini, terbesar menurut ukuran TAMPIL, bukan natural size.

    Foto yang ikut ter-upload juga muncul sebagai <img> dengan natural size
    1024x1024 -- LEBIH BESAR dari storyboard (mis. 1024x572). Jadi natural size
    tidak bisa dipakai memilih. Yang membedakan: ukuran render. Thumbnail
    sumber ~112 px, storyboard ~700 px.
    """
    return json.loads(
        await c.eval(
            r"""
    JSON.stringify([...document.querySelectorAll('img')].map((e) => {
      const r = e.getBoundingClientRect();
      return {src: e.src || '', nw: e.naturalWidth, nh: e.naturalHeight,
              rw: Math.round(r.width), rh: Math.round(r.height)};
    }).filter((o) => /^(blob:|https?:|data:image)/.test(o.src)
                    && o.rw >= %d && o.rh >= %d)
      .sort((a, b) => (b.rw * b.rh) - (a.rw * a.rh)))
    """
            % (MIN_RENDER_PX, MIN_RENDER_PX)
        )
    )


_DL_LABEL = (
    r"/download gambar ukuran penuh|download full.?size"
    r"|unduh gambar ukuran penuh/i"
)


async def _click_download(c, src=None):
    """Klik 'Download gambar ukuran penuh' milik gambar `src`.

    Tombol download hanya muncul saat kursor di atas gambarnya, dan balasan
    Gemini bisa memuat lebih dari satu gambar. Jadi: arahkan kursor ke gambar
    yang dituju, lalu cari tombol di dalam wadah gambar itu, baru fallback ke
    pencarian global.
    """
    r = await c.eval(
        r"""
    (() => {
      const label = %s;
      const want = %s;
      const imgs = [...document.querySelectorAll('img')];
      const target = want
        ? imgs.find((i) => i.src === want)
        : imgs.filter((i) => i.naturalWidth >= 700)
              .sort((a, b) => b.naturalWidth * b.naturalHeight
                            - a.naturalWidth * a.naturalHeight)[0];
      if (!target) return null;
      const box = target.getBoundingClientRect();
      const at = {x: Math.round(box.x + box.width / 2),
                  y: Math.round(box.y + box.height / 2)};
      return at;
    })()
    """
        % (_DL_LABEL, json.dumps(src) if src else "null")
    )
    if not r:
        return False

    # hover dulu: tombol download baru dirender setelah kursor menyentuh gambar
    await c.cmd("Input.dispatchMouseEvent", type="mouseMoved", x=r["x"], y=r["y"])

    btn = None
    for _ in range(6):
        await asyncio.sleep(1)
        btn = await c.eval(
            r"""
    (() => {
      const label = %s;
      const want = %s;
      const imgs = [...document.querySelectorAll('img')];
      const target = want ? imgs.find((i) => i.src === want) : null;
      const hit = (e) => label.test(e.getAttribute('aria-label') || e.innerText || '');
      // 1) tombol di dalam wadah gambar target
      if (target) {
        for (let n = target; n && n !== document.body; n = n.parentElement) {
          const b = [...n.querySelectorAll('button,[role="button"]')].find(hit);
          if (b) return rect(b);
        }
      }
      // 2) fallback: tombol mana pun yang terlihat
      const b = [...document.querySelectorAll('button,[role="button"]')]
        .find((e) => hit(e) && e.offsetParent);
      return b ? rect(b) : null;
      function rect(e) {
        const r = e.getBoundingClientRect();
        return {x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2)};
      }
    })()
    """
            % (_DL_LABEL, json.dumps(src) if src else "null")
        )
        if btn:
            break
    if not btn:
        return False
    await c.tap(btn["x"], btn["y"])
    return True


def _newest_download(downloads_dir, since):
    """File unduhan yang ditulis SETELAH `since` (detik epoch), bukan .crdownload.

    Memakai mtime, bukan nama: Chrome menambah akhiran " (1)" kalau nama sama
    sudah ada, dan nama lama bisa sudah terdaftar sebelum klik. os.path.getsize
    bisa balapan dengan Chrome yang masih menulis -> OSError diabaikan.
    """
    best, best_mtime = None, since
    for name in os.listdir(downloads_dir):
        if name.endswith(".crdownload"):
            continue
        path = os.path.join(downloads_dir, name)
        try:
            st = os.stat(path)
        except OSError:
            continue
        if st.st_size <= MIN_IMAGE_BYTES or st.st_mtime < since:
            continue
        if st.st_mtime >= best_mtime:
            best, best_mtime = path, st.st_mtime
    return best


async def _grab_image(c, out_path, downloads_dir=None):
    """Simpan storyboard. Tombol download Gemini diutamakan, blob fetch cadangan."""
    cands = await _storyboard_images(c)
    for _ in range(6):
        if cands:
            break
        await c.eval("window.scrollBy(0, -900); true")
        await asyncio.sleep(2)
        cands = await _storyboard_images(c)
    if not cands:
        # sertakan ekor balasan: batas/penolakan Gemini biasanya tertulis di
        # situ, dan tanpa itu kita menebak-nebak penyebabnya
        ekor = (
            await c.eval("(document.body && document.body.innerText || '').slice(-400)")
            or ""
        ).strip()
        pesan = (
            "Gemini tidak menghasilkan gambar storyboard. Ekor balasan: " + ekor[-300:]
        )
        if re.search(r"image generation is unavailable", ekor, re.I):
            pesan += (
                " — kemungkinan kuota gambar harian akun ini habis; "
                "pakai akun lain atau coba lagi besok."
            )
        raise ReplyIncomplete(pesan)

    downloads_dir = downloads_dir or os.path.join(fc.HOME, "downloads")
    os.makedirs(downloads_dir, exist_ok=True)
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    top = cands[0]
    since = time.time() - 1  # toleransi 1 dtk untuk resolusi mtime

    if await _click_download(c, top["src"]):
        for _ in range(DOWNLOAD_WAIT // 2):
            await asyncio.sleep(2)
            src = _newest_download(downloads_dir, since)
            if src:
                shutil.copyfile(src, out_path)
                return {
                    "path": out_path,
                    "bytes": os.path.getsize(out_path),
                    "w": top["nw"],
                    "h": top["nh"],
                    "via": "download button",
                    "file": os.path.basename(src),
                }

    # cadangan: fetch blob selagi URL-nya masih hidup
    for cand in cands[:4]:
        data = await c.eval(
            r"""
        (async () => {
          try {
            const r = await fetch(%s);
            const bytes = new Uint8Array(await (await r.blob()).arrayBuffer());
            let s = '';
            const chunk = 0x8000;
            for (let i = 0; i < bytes.length; i += chunk) {
              s += String.fromCharCode.apply(null, bytes.subarray(i, i + chunk));
            }
            return btoa(s);
          } catch (e) { return null; }
        })()
        """
            % json.dumps(cand["src"])
        )
        if not data:
            continue
        raw = base64.b64decode(data)
        if len(raw) < MIN_IMAGE_BYTES:
            continue
        with open(out_path, "wb") as f:
            f.write(raw)
        # w/h milik kandidat yang benar-benar tersimpan, bukan selalu yang terbesar
        return {
            "path": out_path,
            "bytes": len(raw),
            "w": cand["nw"],
            "h": cand["nh"],
            "via": "blob fetch",
        }

    raise SystemExit(
        "storyboard terlihat tapi tidak bisa diunduh "
        "(tombol download tidak ada dan blob sudah dicabut)"
    )


async def _grab_title_hint(c):
    """Gemini opens every reply with STORYBOARD + FLOW PROMPT — <name>.

    Ambil kecocokan TERAKHIR (echo prompt di atas balasan juga mengandung
    baris ini, dengan placeholder "[product name]"). Placeholder tidak valid
    sebagai judul -> kosong, pipeline jatuh ke nama berkas produk.
    """
    hint = (
        await c.eval(r"""
    (() => {
      const t = document.body.innerText || '';
      const semua = t.match(
        /STORYBOARD\s*\+\s*FLOW PROMPT\s*[\u2014\u2013-]\s*([^\n]+)/gi);
      if (!semua || !semua.length) return "";
      const m = semua[semua.length - 1].match(/[—–-]\s*(.+)$/);
      return m ? m[1].trim().slice(0, 80) : "";
    })()
    """)
        or ""
    )
    # placeholder/template bukan judul: "[product name]", "{...}", "`."
    if not hint or re.search(r"[\[{`]", hint) or not re.search(r"[A-Za-z]{3}", hint):
        return ""
    return hint


async def _grab_flow_prompt(c):
    """Read the fenced PROMPT GOOGLE FLOW block from the reply."""
    txt = await c.eval(r"""
    (() => {
      const blocks = [...document.querySelectorAll('pre')]
        .map((e) => (e.innerText || '').trim())
        .filter((t) => t.length > 200);
      if (blocks.length) return blocks[blocks.length - 1];
      const t = document.body.innerText || '';
      const after = t.split(/PROMPT GOOGLE FLOW/i)[1];
      if (!after) return '';
      return after.split(/\n\s*\n\s*\n/)[0].trim();
    })()
    """)
    return txt or ""


# ------------------------------------------------------------------- stage


def run_stage(
    account, images, prompt_file, out_dir, timeout=900, visible=True, mode=None
):
    """Upload images + prompt to Gemini, save STORYBOARD.jpeg and FLOW.md.

    visible=True keeps a real Chrome window on the Xvfb display so the run can
    be watched over VNC; the work itself is identical either way. mode=None
    keeps the account's current model mode; a value like "Auto" switches the
    picker before sending.
    """
    port = fc.port_for(account)
    os.makedirs(out_dir, exist_ok=True)
    with open(prompt_file, encoding="utf-8") as f:
        prompt = f.read()

    fc.stop_chrome(port)
    time.sleep(1)
    fc.ensure_chrome(account, port, headless=not visible)

    async def body(r, with_tool):
        # Chrome memutus koneksi CDP selama tunggu panjang (Gemini bisa diam
        # 5 menit). Setiap langkah lewat r.call supaya sambung ulang otomatis.
        await r.call(lambda c: _open_app(c), attempts=3)
        if mode:
            if await r.call(lambda c: _pick_mode(c, mode), attempts=3):
                print(f"[gemini] mode: {mode}", file=sys.stderr, flush=True)
            else:
                # jangan senyap: ekor balasan nanti membingungkan kalau mode
                # ternyata bukan yang diminta
                print(
                    f"[gemini] mode {mode!r} tidak bisa dipilih "
                    "(item tidak ada di menu) — pakai setelan akun",
                    file=sys.stderr,
                    flush=True,
                )
        n = await r.call(lambda c: _attach_images(c, images), attempts=3)
        if with_tool:
            # percobaan 1: paksa mode gambar lewat menu plus "Buat gambar"
            # supaya balasan pasti berisi storyboard. Percobaan 2 tanpa ini
            # (kalau mode gambar malah memakan blok teks Flow).
            await r.call(lambda c: _pick_create_image(c), attempts=3)
        await r.call(lambda c: _type_prompt(c, prompt), attempts=3)
        await r.call(lambda c: _send(c), attempts=3)

        # tunggu balasan = satu panggilan panjang; _wait_reply hanya membaca
        # (tanpa side-effect), jadi boleh diulang saat CDP putus di tengah
        await r.call(lambda c: _wait_reply(c, timeout=timeout), attempts=4)

        shot = os.path.join(out_dir, "STORYBOARD.jpeg")
        img = await r.call(lambda c: _grab_image(c, shot), attempts=3)

        flow_prompt = await r.call(lambda c: _grab_flow_prompt(c), attempts=3)
        title_hint = await r.call(lambda c: _grab_title_hint(c), attempts=3)
        if not flow_prompt or len(flow_prompt) < 200:
            raise ReplyIncomplete("blok PROMPT GOOGLE FLOW tidak ditemukan di balasan")
        flow_md = os.path.join(out_dir, "FLOW.md")
        with open(flow_md, "w", encoding="utf-8") as f:
            f.write(flow_prompt.rstrip() + "\n")
        return {
            "images_attached": n,
            "storyboard": img,
            "flow_md": flow_md,
            "prompt_chars": len(flow_prompt),
            "title_hint": title_hint,
        }

    for attempt in range(1, ATTEMPTS + 1):
        try:
            return fc.run_resilient(lambda r, a=attempt: body(r, a == 1), port)
        except (GeminiStalled, ReplyIncomplete) as e:
            if attempt >= ATTEMPTS:
                raise SystemExit(
                    f"{e}. Coba lagi nanti atau pakai akun lain."
                ) from None
            ekstra = " tanpa 'Buat gambar'" if isinstance(e, ReplyIncomplete) else ""
            print(
                f"[gemini] percobaan {attempt}/{ATTEMPTS} gagal: {e} "
                f"-> buka percakapan baru{ekstra}",
                file=sys.stderr,
                flush=True,
            )
            time.sleep(5)
