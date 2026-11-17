# RAFAKA Clipper

Mengubah video panjang (podcast, stream, webinar) menjadi klip vertikal **9:16** siap posting ke TikTok / Reels / Shorts, lengkap dengan **subtitle animasi kata per kata**. Semua proses berjalan lokal di Mac.

```
URL / file video
   │  yt-dlp           download (maks 1080p)
   ▼
whisper.cpp            transkrip + timestamp per kata (Metal, cepat di M4)
   ▼
Claude Code (claude -p)  memilih momen paling "viral": hook kuat, utuh, 20–60 detik
   ▼
ffmpeg + libass        potong → 9:16 (crop / blur) → subtitle kuning aktif → MP4
```

## Setup (sekali saja)

```bash
brew install yt-dlp whisper-cpp ffmpeg-full
```

- `ffmpeg-full` wajib, karena `ffmpeg` biasa dari Homebrew tidak menyertakan libass (subtitle).
- Model Whisper (~550MB) terunduh otomatis ke `models/` saat pertama kali jalan.
- Pemilihan highlight memakai Claude Code yang sudah login, jadi **tidak perlu API key**.

## Pemakaian

```bash
cd clippers   # folder hasil git clone

# pipeline lengkap dari link
python3 clipper.py run "https://www.youtube.com/watch?v=XXXX" -n 5

# file lokal, bahasa Indonesia, latar blur, teks hook di atas
python3 clipper.py run ~/Downloads/podcast.mp4 --lang id --mode blur --hook

# lihat transkrip bertimestamp
python3 clipper.py transcript <nama-job>

# render ulang setelah mengedit clips.json (geser waktu, ganti fokus crop, dll.)
python3 clipper.py render <nama-job> --only 2 4
```

**Hasil klip** (`01-judul.mp4`, `02-…`, plus `captions.txt` berisi judul dan hashtag) otomatis masuk ke **Google Drive → My Drive/CLIPPERS/<nama-job>/** kalau folder `CLIPPERS` ada, jadi langsung bisa dibuka dari HP untuk diposting. Kalau folder itu tidak ada, hasil disimpan di `~/Movies/RAFAKA-Clipper/jobs/<nama-job>/out/`.

- `--out /folder/lain`: atur folder tujuan lain (atau pakai env `CLIPPER_OUT`).
- `--local`: paksa simpan di lokal, bukan di Drive.

**Bahan kerja** (video sumber, transkrip, `clips.json`, model Whisper) tetap di `~/Movies/RAFAKA-Clipper`, di luar Drive supaya file besar tidak ikut ter-sync. Lokasinya bisa diganti lewat `CLIPPER_DATA=/path/lain`. Perintah `render` dan `transcript` cukup diberi nama job, misalnya `python3 clipper.py render indodax-william`.

### Opsi penting

| Opsi | Fungsi |
|---|---|
| `-n 5` | jumlah klip |
| `--min 20 --max 60` | rentang durasi (detik) |
| `--lang id` / `en` / `auto` | bahasa audio |
| `--mode crop` | isi penuh 9:16, kiri-kanan dipotong (default) |
| `--mode blur` | video utuh di tengah + latar blur, cocok kalau ada 2 orang di frame |
| `--hook` | teks hook hitam-putih di atas selama 3,5 detik pertama |
| `--hook-margin 70` | jarak kotak hook dari atas layar (px, default 260); kecilkan kalau menutupi teks bawaan video |
| `--no-subs` | tanpa subtitle, untuk materi brand yang sudah punya subtitle bawaan (cukup hook saja) |
| `--words-per-line 3` | jumlah kata per baris subtitle |
| `--caption-y 0.70` | posisi subtitle (0 = atas, 1 = bawah) |
| `--highlight "&H0000E5FF&"` | warna kata aktif (format ASS `&HBBGGRR&`); hijau: `&H0000FF00&` |
| `--no-upper` | jangan pakai huruf kapital |
| `--fast` | encode memakai hardware Mac (lebih cepat) |
| `--whisper small` | model lebih kecil/cepat (akurasi turun) |

### Mengedit hasil AI

`~/Movies/RAFAKA-Clipper/jobs/<nama>/clips.json` bisa diedit langsung:

```json
{ "start": 312.4, "end": 355.1, "title": "...", "hook": "...", "focus_x": 0.3 }
```

- `start` / `end`: detik di video asli.
- `focus_x`: posisi crop horizontal (0 = kiri, 0,5 = tengah, 1 = kanan). Pakai ini kalau pembicara tidak di tengah.
- `segments` (opsional, untuk **jump cut**): buang bagian tengah yang bertele-tele dengan menyambung beberapa potongan, misalnya `"segments": [[827.8, 840.85], [858.1, 882.4]]`. Potongan harus urut dan tidak tumpang tindih, dan subtitle ikut tersambung otomatis. Potong di jeda antar kalimat (lihat timestamp di `words.json`) supaya sambungannya tidak terdengar.

- `shots` (opsional, untuk **podcast multi-kamera**): posisi crop yang berganti setiap kamera pindah, misalnya `"shots": [[1635.9, 0.44], [1640.91, 0.05], [1649.65, 0.41]]` (detik di video asli, lalu `focus_x` sejak detik itu). Waktu pergantian kamera bisa dicari dengan `ffmpeg -ss <mulai> -t <durasi> -i source.mp4 -vf "select='gt(scene,0.12)',metadata=print" -an -f null -`.

- `src_crop` (opsional): potong frame sumber dulu sebelum di-crop 9:16, dalam pecahan `[x, y, lebar, tinggi]`. Contoh `"src_crop": [0, 0, 1, 0.8]` membuang 20% bagian bawah, berguna kalau video sumber sudah punya subtitle bawaan supaya tidak dobel dengan subtitle kita.

Lalu jalankan `python3 clipper.py render <nama>`.

## Konten.com (marketplace clipping Indonesia)

Satu file aturan per campaign ada di `campaigns/`. Salin brief dari halaman campaign → `/brief`, tulis larangannya, dan tambahkan hashtag (satu per baris, diawali `#`). AI akan menghindari momen yang melanggar, dan hashtag otomatis masuk ke `captions.txt`.

```bash
python3 clipper.py run "https://www.youtube.com/watch?v=3bYn1Jlvlig" \
  --campaign campaigns/indodax.txt --lang id -n 8 --mode crop
```

Yang harus dilakukan sendiri di konten.com (sekali saja):
1. Daftar akun clipper dan hubungkan TikTok/Instagram.
2. Tulis **"Part of @Konten.com"** di bio akun (wajib untuk beberapa campaign).
3. Klik **Join Campaign**, lalu setelah posting klik **Submit Video** dan tempel link postingan.

Catatan pembayaran: views dihitung tiap pukul 24:00 WIB, lalu ada masa tunggu 5 hari (validasi anti-bot) sebelum masuk saldo. Saldo bisa dicairkan ke GoPay/OVO/DANA/bank. Kebanyakan campaign baru membayar setelah video tembus **1.000 views**.

Cocok untuk otomatis: campaign **podcast/edukasi** dengan satu video sumber (mis. Indodax, David Noah, Sulianto). Campaign yang mewajibkan gabungan beberapa footage atau rekaman layar aplikasi (mis. Mobee) tetap perlu sentuhan manual di CapCut.

## Campaign promo event (`promo.py`)

Beberapa campaign bukan potong podcast, melainkan **video promo dari materi brand**: footage, banner resmi yang wajib tampil utuh, teks hook, dan CTA (contoh: Miranda Color Corner). Untuk campaign seperti ini pakai `promo.py` dengan file spec JSON:

```bash
python3 promo.py campaigns/miranda-promo.json            # render semua video
python3 promo.py campaigns/miranda-promo.json --only 2   # video ke-2 saja
```

- **Segmen**: potongan video (`src`, `start`, `dur`) atau gambar diam (`image`, `dur`), disusun berurutan.
- **`fit`**: `fill` (isi penuh 9:16), `blur` (utuh di tengah dengan latar blur), atau `pad` (utuh di tengah dengan latar warna; dipakai untuk banner resmi supaya tidak ter-crop).
- **Teks**: style `Hook`, `Top`, `Bottom`, `Label`, `CtaTop`, `CtaBottom`, dengan waktu `t0`–`t1` dalam detik. Pakai `\N` untuk ganti baris.
- **Musik latar** diambil dari `audio.src`. Hasil masuk ke `My Drive/CLIPPERS/<name>/` beserta `captions.txt`.

Format lengkap spec ada di docstring `promo.py`.

## Alur kerja program clipping

1. **Cari campaign** di platform program clipping (misalnya Whop "Content Rewards"/clipping, atau program resmi kreator/brand). Baca aturannya: bayaran per 1.000 views, platform yang diizinkan, durasi, wajib tag/sound/watermark atau tidak.
2. **Ambil video sumber** dari link yang disediakan campaign.
3. **Jalankan clipper**, misalnya `-n 8`, lalu tonton semua hasilnya dan buang yang lemah. AI hanya memilih kandidat; kurasi tetap tugas kamu.
4. **Posting** 1–3 klip per hari per akun, dengan judul dari `captions.txt` dan hashtag sesuai aturan campaign.
5. **Submit** link postingan ke dashboard campaign untuk dihitung views-nya.

### Tips supaya views naik

- 1–2 detik pertama menentukan. Kalau hook-nya lemah, geser `start` ke kalimat paling kuat.
- Durasi 20–45 detik biasanya paling aman untuk retensi.
- Satu ide per klip. Jangan memotong di tengah punchline.
- Konsisten satu niche per akun (bisnis, motivasi, komedi, dll.).

## ⚠️ Hak cipta

Pakai tool ini hanya untuk konten yang **kamu punya izinnya**: video milik sendiri, campaign clipping resmi yang mengizinkan kreatornya diklip, atau klien yang meminta. Mengunggah ulang konten orang lain tanpa izin bisa kena klaim hak cipta, strike, sampai akun diblokir.
