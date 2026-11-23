# Peta UI — Gemini & Flow

Diselaraskan dengan kode: 23 Nov 2026 (v3.5.0). Dokumen ini **tidak dibaca
kode** — hanya peta maintenance saat UI Google berubah. Sumber kebenaran ada di:

- `bits_affiliate/gemini_stage.py` — tahap Gemini
- `bits_affiliate/flow_stage.py` — tahap Flow

Kalau selector berubah: perbaiki kode dulu, baru perbarui tabel ini. Selector
memakai class/aria-label yang stabil; teks label bisa berubah bahasa.

## Gemini — `https://gemini.google.com/app`

| Elemen | Selector / cara |
|---|---|
| Composer | `[contenteditable="true"]` (kode tidak memakai aria-label) |
| Pemilih mode | `button` aria-label cocok `/pemilih mode\|mode picker/i` (label `Buka pemilih mode, saat ini X` / `Open mode picker, currently X`); item menu dicocokkan **persis** `auto\|hydrogen\|...` |
| Tombol upload | `button` dengan aria-label cocok `/upload (& )?alat\|upload .*(and )?tools?/i` (label `Upload & alat` / `Upload files and tools`) |
| Input file | `input[type=file][accept*="image"]` |
| Kirim | `button` aria-label cocok `/kirim pesan\|send message/i` |
| Menu upload | `Upload file`, `Tambahkan dari Drive`, `Upload lainnya`, `Buat Video`, `Alat lainnya`. Setelah menu diklik, `input[type=file]` muncul telat — dipolling 15×1 dtk |

**Penting:** `input[type=file]` **tidak ada** di DOM sampai menu upload diklik.
Jumlah dan urutan input tidak diandalkan — pilih yang `accept`-nya memuat
`image`. Pipeline hanya memakai item upload (`Upload file` / `Upload lainnya`);
item lain di menu ini tidak pernah diklik.

Alurnya: klik menu → `DOM.getDocument(depth=-1, pierce=True)` →
`DOM.querySelector('input[type=file][accept*="image"]')` → `DOM.setFileInputFiles`.
Jangan pakai file chooser: menunya tidak konsisten memicu dialog.

Kalau URL berakhir di `/signin`, script berhenti dan meminta login ulang.

### Mengambil hasil

| Hasil | Cara |
|---|---|
| Storyboard (utama) | tombol `Download gambar ukuran penuh` / `Download full-size image` — hanya muncul setelah **hover** gambar, dipolling 6×1 dtk; file baru dideteksi via **mtime** (bukan nama — Chrome menambah ` (1)`), tunggu ≤80 dtk di `~/.config/bits-affiliate/downloads/`, >20 KB, bukan `.crdownload` |
| Storyboard (fallback) | `img` disaring dari **ukuran TAMPIL** (`getBoundingClientRect`, ambang 200 px — thumbnail sumber 1024×1024 hanya tampil 112 px, storyboard 1024×572 tampil ~708 px), urut luas tampil terbesar; coba 4 teratas → `fetch(src)` → `btoa`; hasil <20 KB ditolak |
| Prompt Flow | `pre` terakhir dengan `innerText.length > 200`; fallback teks setelah `PROMPT GOOGLE FLOW` sampai tiga baris kosong |
| Judul | kecocokan **terakhir** `STORYBOARD + FLOW PROMPT —` (echo prompt di atas juga mengandung baris ini dengan placeholder); placeholder `[…]` / `{…}` / backtick ditolak -> jatuh ke nama berkas produk |

Tanda selesai: tidak ada tombol `Hentikan`/`Stop`/`Batalkan` **dan** panjang teks
stabil 4 putaran. Interval polling adaptif: 10 dtk selama "menulis", turun ke
3 dtk setelah Stop hilang (konfirmasi ~12 dtk). Abaikan sementara: `document.body` null saat
reload dan putus CDP -> putaran dilewati, bukan dianggap reset.

Gagal yang dibedakan (semua bisa di-retry `ATTEMPTS=2`):
- **Macet**: teks diam + tombol Stop masih ada, 36 putaran (6 mnt). Gemini bisa
  diam >5 mnt sebelum token pertama; gambar muncul duluan dihitung progres.
- **Reset percakapan**: halaman menyusut >50% (~310 dtk di lapangan).
- **Balasan tanpa gambar / tanpa blok Flow**: pesan error menyertakan ekor
  balasan Gemini (batas/penolakan biasanya tertulis di situ).

## Google Flow — `https://flow.google.com/`

| Halaman | URL | Penanda |
|---|---|---|
| Beranda | `/` | `.mdc-fab` atau tombol `Project baru` / `Proyek baru` / `New project` |
| Project | `/project/<uuid>` | `.settings-trigger-button` |
| Signed out | `/about` atau `/?pli=` | tidak ada `.mdc-fab` |

Catatan `/about`: dipakai `flow_stage._new_project` dan `login_bench.probe_live`
sebagai tanda logout. `check_credits` sengaja **tidak** memakainya — halaman
marketing bisa muncul sesaat sebelum app terbaca, dan itu bukan logout. Saat
render, logout juga dideteksi dari teks `Buat dengan Google Flow`.

Dialog onboarding/changelog ditutup dulu (`_dismiss`) sebelum klik apa pun.

### Panel setelan (klik `.settings-trigger-button`)

| Kontrol | Nilai |
|---|---|
| Mode | `video Video` / `image Gambar` |
| Sumber | `crop_free Frame` / `chrome_extension Bahan` (skill memakai `Bahan`) |
| Aspect | `crop_16_9 16:9`, `crop_landscape 4:3`, `crop_square 1:1`, `crop_portrait 3:4`, `crop_9_16 9:16` |
| Model | dropdown `Pilih rangkaian model` / `Select model series` → **`Omni 1.2 Flash`**; fallback tombol ber-`arrow_drop_down` dengan teks Omni/Veo/Nano |
| Resolusi | `360p info` / `720p` (skill memakai 720p) |
| Durasi | slider `input.mdc-slider__input`, nilai mentah = **detik − 3** (13 dtk → nilai 10). Atribut `max` slider = cap durasi akun; dibaca dulu — di bawah durasi target → `FlowSetupError` (rotasi akun, 0 kredit) |
| Output | `x1` (default `x2`), `x2`, `x3`, `x4` |
| Biaya | `.credit-cost-label` → `Pembuatan akan menggunakan N kredit` / `Generating will use N credits` — di-parse `parse_cost()` untuk gate kredit |

Setelah `Escape`, chip `.settings-trigger-button` harus memuat durasi yang
dipilih (`13 dtk` atau `10 dtk`). Kode hanya memvalidasi angka detiknya,
bukan seluruh teks chip.

Alias terpusat di `ALIASES` hanya untuk `Ingredients` (`Bahan`), `Upload media`
(`Unggah media`), dan `Start` (`Mulai pembuatan` / `Start generation` / `Mulai`).
Label lain dicocokkan inline ID+EN (regex `…|…`), mis. `Buat dengan|Create with`.
Aspect, resolusi, `x1`, dan model diklik dengan label persis.

### Composer

| Elemen | Selector |
|---|---|
| Prompt | `[contenteditable="true"]` dengan aria-label `what do you want to create` / `apa yang ingin anda buat`; fallback contenteditable terakhir |
| Tombol bahan | `button` aria-label cocok `/add ingredients|tambahkan bahan|bahan ke kotak/i` |
| Kirim | `button` aria-label cocok `/mulai pembuatan|start generation/i` |

`_credit_state` membaca `.prompt-warning-button` dan status tombol kirim: kalau
Flow menampilkan `Peringatan poin tidak cukup`, script berhenti sebelum generate.

### Panel bahan

| Elemen | Selector |
|---|---|
| Menu upload | `.upload-text` atau teks `Upload media` / `Unggah media` |
| File chooser | `Page.setInterceptFileChooserDialog` → `Page.fileChooserOpened` → `DOM.setFileInputFiles(backendNodeId)` |
| Thumbnail aset | `.asset-thumbnail`; **siap diklik** = `<img>`-nya termuat (`img.complete && naturalWidth > 0`) — thumbnail muncul sejak upload MULAI, klik sebelum img termuat adalah no-op (dipolling 15×1 dtk) |
| Tempel ke prompt | **klik `.asset-thumbnail`** (closest `button,[role="button"],.upload-item`) — tombol `Tambahkan ke perintah / Add to prompt` bisa no-op, jangan diandalkan; chip belum mendarat → klik ulang di detik ke-9/18 (aset lambat diproses) |
| Bukti menempel | chip di `.prompt-ingredient-bar` (`flow-ingredient-chip`) **yang mengandung `<img>`** — chip progress-upload (innerText `cancel`, tanpa gambar) bukan bukti |

Upload hanya menaruh berkas di library project; klik thumbnail untuk
menjadikannya bahan. Panel ditutup dengan **Escape** — tombol `Cancel` bisa
membuang chip yang baru menempel.

### Hasil

| Keadaan | Penanda |
|---|---|
| Sedang render | polling thumbnail tiap 4 dtk; logout dideteksi dari URL `/about` atau teks `Buat/Create with Google Flow` |
| Selesai | `img` dengan `/asb/` di `src`, `naturalHeight > naturalWidth` (9:16), **di dalam container tile** — chip storyboard di prompt bar juga `/asb/` dan tidak boleh ikut terhitung |
| Unduh | klik tombol `⋮ More options / Opsi lainnya` di tile → item `Download / Unduh` → menu kualitas `720p` (regex `^\s*720p`, fallback `Ukuran asli / Original size`) → tunggu file >100 KB dengan ukuran stabil 2 polling → ambil terbesar. Tidak ada jalur editor — tile click hanya membuka preview overlay |

Unduhan **bukan** `fetch(video.src)`: Flow menulis file sendiri ke folder
download, script **memindahkannya** (bukan menyalin — folder download adalah
perantara, bukan gudang) ke folder proyek begitu ukurannya stabil. Menu 1080p
berbayar (+3 kredit saat dicek) — jangan dipilih.

### Yang tidak ada di DOM

- Flow: `<input type="file">` biasa — memakai file chooser native
  (`Page.setInterceptFileChooserDialog` + `DOM.setFileInputFiles`).
- Gemini: input file tidak ada sampai menu upload diklik.

---

## Kode terkait

| Bagian | Fungsi |
|---|---|
| Gemini: buka app / cek login / tunggu composer | `gemini_stage._open_app` |
| Gemini: munculkan input file | `gemini_stage._reveal_file_inputs` |
| Gemini: tempel gambar / prompt / kirim | `gemini_stage._attach_images`, `_type_prompt`, `_send` |
| Gemini: tunggu balasan | `gemini_stage._wait_reply` |
| Gemini: storyboard | `gemini_stage._click_download`, `_grab_image` |
| Gemini: prompt Flow / judul | `gemini_stage._grab_flow_prompt`, `_grab_title_hint` |
| Flow: tutup dialog onboarding | `flow_stage._dismiss` |
| Flow: project baru / cek logout | `flow_stage._new_project` |
| Flow: setelan + chip + biaya | `flow_stage._apply_settings`, `_set_duration` |
| Flow: upload + tempel bahan | `flow_stage._upload`, `_attach` |
| Flow: prompt + generate | `flow_stage._type_prompt`, `_generate` |
| Flow: peringatan kredit composer | `flow_stage._credit_state` |
| Flow: tunggu clip (tile, bukan chip) | `flow_stage._wait_render` |
| Flow: unduh 720p via menu tile | `flow_stage._download_from_tile` |