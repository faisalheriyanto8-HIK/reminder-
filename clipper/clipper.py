#!/usr/bin/env python3
"""
RAFAKA Clipper — video panjang -> klip vertikal 9:16 dengan subtitle animasi.

Pipeline:
  1. fetch       : download video (yt-dlp) atau salin file lokal
  2. transcribe  : whisper.cpp -> timestamp per kata (words.json)
  3. pick        : Claude Code (claude -p) pilih momen terbaik -> clips.json
  4. render      : ffmpeg potong + 9:16 + subtitle kata-per-kata -> out/*.mp4

Pemakaian cepat:
  python3 clipper.py run "https://youtube.com/watch?v=..." -n 5
  python3 clipper.py run video.mp4 --mode blur --lang id
  python3 clipper.py render jobs/<nama>        # render ulang setelah edit clips.json

Hanya pakai library standar Python. Butuh: ffmpeg, yt-dlp, whisper-cli, claude.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
# Data besar (video & model) disimpan di luar Google Drive supaya tidak ikut ter-sync.
DATA = Path(os.environ.get("CLIPPER_DATA", "~/Movies/RAFAKA-Clipper")).expanduser()
JOBS = DATA / "jobs"
MODELS = DATA / "models"

WHISPER_MODELS = {
    # nama: (file, ukuran kira-kira)
    "turbo": ("ggml-large-v3-turbo-q5_0.bin", "~550MB"),
    "small": ("ggml-small-q5_1.bin", "~190MB"),
    "medium": ("ggml-medium-q5_0.bin", "~540MB"),
}
HF_BASE = "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/"

W, H = 1080, 1920


# ───────────────────────── util ─────────────────────────

def log(msg: str) -> None:
    print(f"\033[1;36m▶\033[0m {msg}", flush=True)


def die(msg: str) -> None:
    print(f"\033[1;31m✖ {msg}\033[0m", file=sys.stderr)
    sys.exit(1)


def need(tool: str, hint: str) -> str:
    path = shutil.which(tool) or (
        f"/opt/homebrew/bin/{tool}" if Path(f"/opt/homebrew/bin/{tool}").exists() else None
    )
    if not path:
        die(f"'{tool}' tidak ditemukan. {hint}")
    return path


def need_ffmpeg_ass() -> str:
    """ffmpeg biasa dari Homebrew tidak punya libass; pakai ffmpeg-full (keg-only)."""
    for p in ("/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg", "/usr/local/opt/ffmpeg-full/bin/ffmpeg",
              shutil.which("ffmpeg") or ""):
        if p and Path(p).exists():
            out = subprocess.run([p, "-hide_banner", "-filters"], capture_output=True, text=True).stdout
            if re.search(r"\sass\s", out):
                return p
    die("ffmpeg dengan libass tidak ditemukan. Install: brew install ffmpeg-full")
    return ""


def run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, check=True, **kw)


def fmt_ts(sec: float) -> str:
    m, s = divmod(max(sec, 0), 60)
    return f"{int(m):02d}:{s:05.2f}"


def slugify(text: str) -> str:
    text = re.sub(r"[^\w\s-]", "", text, flags=re.UNICODE).strip().lower()
    return re.sub(r"[\s_-]+", "-", text)[:60] or "clip"


# ───────────────────────── 1. fetch ─────────────────────────

def fetch(src: str, job: Path) -> Path:
    out = job / "source.mp4"
    if out.exists():
        log("source.mp4 sudah ada, lewati download")
        return out
    if re.match(r"^https?://", src):
        ytdlp = need("yt-dlp", "Install: brew install yt-dlp")
        log("Download video (maks 1080p)…")
        run([
            ytdlp, "-f", "bv*[height<=1080][ext=mp4]+ba[ext=m4a]/b[height<=1080][ext=mp4]/b",
            "--merge-output-format", "mp4", "--no-playlist",
            "-o", str(job / "source.%(ext)s"), src,
        ])
        if not out.exists():
            cands = sorted(job.glob("source.*"))
            if not cands:
                die("Download gagal, file source tidak ditemukan.")
            cands[0].rename(out)
    else:
        p = Path(src).expanduser().resolve()
        if not p.exists():
            die(f"File tidak ditemukan: {p}")
        log(f"Salin {p.name}")
        shutil.copy2(p, out)
    return out


# ───────────────────────── 2. transcribe ─────────────────────────

def ensure_model(name: str) -> Path:
    fname, size = WHISPER_MODELS[name]
    path = MODELS / fname
    if path.exists():
        return path
    MODELS.mkdir(parents=True, exist_ok=True)
    log(f"Download model Whisper '{name}' ({size}) — sekali saja…")
    tmp = path.with_suffix(".part")
    urllib.request.urlretrieve(HF_BASE + fname, tmp)
    tmp.rename(path)
    return path


def transcribe(video: Path, job: Path, lang: str, model: str) -> list[dict]:
    words_file = job / "words.json"
    if words_file.exists():
        log("words.json sudah ada, lewati transkrip")
        return json.loads(words_file.read_text())

    ffmpeg = need("ffmpeg", "Install: brew install ffmpeg")
    whisper = need("whisper-cli", "Install: brew install whisper-cpp")
    model_path = ensure_model(model)

    wav = job / "audio.wav"
    log("Ekstrak audio 16kHz…")
    run([ffmpeg, "-y", "-loglevel", "error", "-i", str(video),
         "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(wav)])

    log(f"Transkrip dengan Whisper ({model}, bahasa={lang})… ini yang paling lama")
    base = job / "whisper"
    run([whisper, "-m", str(model_path), "-f", str(wav), "-l", lang,
         "-ml", "1", "-sow", "-oj", "-of", str(base), "-np"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    raw = json.loads((base.with_suffix(".json")).read_text(errors="replace"))
    words: list[dict] = []
    for seg in raw.get("transcription", []):
        text = seg.get("text", "").strip()
        if not text or re.fullmatch(r"[\[\(].*[\]\)]", text):  # [MUSIC], (tertawa)
            continue
        words.append({
            "w": text,
            "s": seg["offsets"]["from"] / 1000,
            "e": seg["offsets"]["to"] / 1000,
        })
    words_file.write_text(json.dumps(words, ensure_ascii=False))
    (job / "transcript.txt").write_text(transcript_lines(words))
    wav.unlink(missing_ok=True)
    log(f"Transkrip selesai: {len(words)} kata")
    return words


def transcript_lines(words: list[dict], max_words: int = 14) -> str:
    """Kelompokkan kata jadi baris bertimestamp untuk dibaca AI/manusia."""
    lines, cur = [], []
    for w in words:
        cur.append(w)
        if len(cur) >= max_words or w["w"][-1:] in ".?!":
            lines.append(f"[{cur[0]['s']:.1f}-{cur[-1]['e']:.1f}] " + " ".join(x["w"] for x in cur))
            cur = []
    if cur:
        lines.append(f"[{cur[0]['s']:.1f}-{cur[-1]['e']:.1f}] " + " ".join(x["w"] for x in cur))
    return "\n".join(lines)


# ───────────────────────── 3. pick highlights ─────────────────────────

PICK_PROMPT = """Kamu editor short-form video profesional (TikTok/Reels/Shorts) untuk program clipping berbayar per views.

Dari transkrip bertimestamp di bawah, pilih {n} momen TERBAIK untuk dijadikan klip vertikal berdiri sendiri.

Kriteria:
- Hook kuat di 1-3 detik pertama (pernyataan mengejutkan, pertanyaan, opini kontroversial, angka, cerita lucu/emosional).
- Utuh: penonton paham tanpa konteks sebelumnya; mulai di awal kalimat, selesai di akhir kalimat/punchline.
- Durasi {min_s}-{max_s} detik.
- Tidak saling tumpang tindih.
- Urutkan dari skor viral tertinggi.

{brief}
Balas HANYA JSON array, tanpa teks lain, format:
[{{"start": 12.3, "end": 48.9, "title": "judul hook singkat untuk caption posting", "hook": "teks hook 3-6 kata untuk overlay", "reason": "kenapa momen ini kuat", "score": 9}}]

TRANSKRIP:
{transcript}
"""


def pick(job: Path, words: list[dict], n: int, min_s: int, max_s: int, model: str,
         brief: str = "") -> list[dict]:
    clips_file = job / "clips.json"
    if clips_file.exists():
        log("clips.json sudah ada, lewati pemilihan (hapus file ini untuk pilih ulang)")
        return json.loads(clips_file.read_text())

    claude = need("claude", "Install Claude Code, atau tulis clips.json manual.")
    transcript = (job / "transcript.txt").read_text()
    brief_txt = (f"\nATURAN CAMPAIGN (WAJIB dipatuhi; JANGAN pilih momen yang melanggar):\n{brief.strip()}\n"
                 if brief.strip() else "")
    prompt = PICK_PROMPT.format(n=n, min_s=min_s, max_s=max_s, transcript=transcript, brief=brief_txt)

    log(f"Claude memilih {n} momen terbaik…")
    with tempfile.TemporaryDirectory() as tmp:
        res = subprocess.run(
            [claude, "-p", "--output-format", "json", "--model", model,
             "--tools", "", "--strict-mcp-config", "--no-session-persistence"],
            input=prompt, capture_output=True, text=True, cwd=tmp,
        )
    try:
        payload = json.loads(res.stdout)
        text = payload.get("result", "")
        is_err = payload.get("is_error", False)
    except json.JSONDecodeError:
        text, is_err = res.stdout, res.returncode != 0
    if res.returncode != 0 or is_err:
        msg = text or res.stderr.strip() or res.stdout[:300]
        hint = ""
        if re.search(r"auth|login|OAuth", msg, re.I):
            hint = "\n  → Login ulang: jalankan `claude` di Terminal lalu ketik /login, kemudian ulangi perintah ini."
        die(f"claude gagal: {msg}{hint}\n  → Atau tulis {clips_file} manual (lihat README), lalu: "
            f"python3 clipper.py render {job}")
    m = re.search(r"\[.*\]", text, re.S)
    if not m:
        die(f"Balasan Claude bukan JSON:\n{text[:800]}")
    clips = json.loads(m.group(0))

    clips = [snap(c, words) for c in clips]
    for c in clips:
        c.setdefault("focus_x", 0.5)
    clips_file.write_text(json.dumps(clips, ensure_ascii=False, indent=2))
    for i, c in enumerate(clips, 1):
        print(f"  {i}. [{fmt_ts(c['start'])}–{fmt_ts(c['end'])}] ({c['end']-c['start']:.0f}s) "
              f"skor {c.get('score','?')} — {c.get('title','')}")
    return clips


def snap(c: dict, words: list[dict]) -> dict:
    """Rapatkan start/end ke batas kata terdekat supaya tidak memotong di tengah kata."""
    s, e = float(c["start"]), float(c["end"])
    inside = [w for w in words if w["e"] > s and w["s"] < e]
    if inside:
        s = max(inside[0]["s"] - 0.15, 0)
        e = inside[-1]["e"] + 0.35
    return {**c, "start": round(s, 2), "end": round(e, 2)}


# ───────────────────────── 4. render ─────────────────────────

def ass_escape(t: str) -> str:
    return t.replace("\\", "\\\\").replace("{", "(").replace("}", ")").replace("\n", " ")


def ass_time(t: float) -> str:
    t = max(t, 0)
    h, r = divmod(t, 3600)
    m, s = divmod(r, 60)
    return f"{int(h)}:{int(m):02d}:{s:05.2f}"


def build_ass(clip: dict, words: list[dict], opt: argparse.Namespace) -> str:
    s0, e0 = clip["start"], clip["end"]
    ws = [{**w, "s": w["s"] - s0, "e": w["e"] - s0} for w in words if w["s"] >= s0 - 0.05 and w["e"] <= e0 + 0.05]

    # kelompokkan per 1-3 kata (maks ~16 huruf) biar nyaman dibaca
    chunks, cur = [], []
    for w in ws:
        cur.append(w)
        n_chars = sum(len(x["w"]) for x in cur)
        if len(cur) >= opt.words_per_line or n_chars >= 16 or w["w"][-1:] in ".?!,":
            chunks.append(cur)
            cur = []
    if cur:
        chunks.append(cur)

    hl = opt.highlight  # BGR ASS
    events = []
    for ci, ch in enumerate(chunks):
        nxt = chunks[ci + 1][0]["s"] if ci + 1 < len(chunks) else ch[-1]["e"] + 0.6
        for wi, w in enumerate(ch):
            start = w["s"] if wi else ch[0]["s"]
            end = ch[wi + 1]["s"] if wi + 1 < len(ch) else min(nxt, ch[-1]["e"] + 0.6)
            if end <= start:
                end = start + 0.05
            parts = []
            for k, x in enumerate(ch):
                word = x["w"].upper() if opt.upper else x["w"]
                word = ass_escape(word)
                if k == wi:
                    parts.append(r"{\c" + hl + r"\fscx112\fscy112}" + word + r"{\c&HFFFFFF&\fscx100\fscy100}")
                else:
                    parts.append(word)
            pop = r"{\fscx80\fscy80\t(0,90,\fscx100\fscy100)}" if wi == 0 else ""
            events.append(f"Dialogue: 0,{ass_time(start)},{ass_time(end)},Cap,,0,0,0,,{pop}{' '.join(parts)}")

    if opt.hook and clip.get("hook"):
        hook = ass_escape(clip["hook"].upper())
        events.append(f"Dialogue: 1,{ass_time(0)},{ass_time(3.5)},Hook,,0,0,0,,{{\\fad(150,250)}}{hook}")

    margin_v = int(H * (1 - opt.caption_y))
    return f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,{opt.font},{opt.font_size},&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,1,0,0,0,100,100,1,0,1,7,3,2,80,80,{margin_v},1
Style: Hook,{opt.font},76,&H00000000,&H00000000,&H00FFFFFF,&H00FFFFFF,1,0,0,0,100,100,0,0,3,18,0,8,90,90,260,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
""" + "\n".join(events) + "\n"


def video_filter(mode: str, focus_x: float, ass_name: str) -> str:
    # ffmpeg dijalankan dengan cwd = folder out, jadi cukup nama file (aman dari spasi/@/: di path)
    ass = ass_name.replace("'", "")
    if mode == "blur":
        base = (f"[0:v]split[a][b];"
                f"[a]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},boxblur=25:3,eq=brightness=-0.08[bg];"
                f"[b]scale={W}:-2[fg];[bg][fg]overlay=(W-w)/2:(H-h)/2")
    else:  # crop
        fx = min(max(focus_x, 0.0), 1.0)
        base = (f"[0:v]scale=-2:{H},crop={W}:{H}:(iw-{W})*{fx:.3f}:0")
    return f"{base},setsar=1,ass='{ass}'[v]"


def render(job: Path, opt: argparse.Namespace) -> list[Path]:
    ffmpeg = need_ffmpeg_ass()
    src = job / "source.mp4"
    words = json.loads((job / "words.json").read_text())
    clips = json.loads((job / "clips.json").read_text())
    out_dir = job / "out"
    out_dir.mkdir(exist_ok=True)
    sel = set(opt.only) if getattr(opt, "only", None) else None

    outputs = []
    for i, c in enumerate(clips, 1):
        if sel and i not in sel:
            continue
        name = f"{i:02d}-{slugify(c.get('title', 'clip'))}"
        ass_path = out_dir / f"{name}.ass"
        ass_path.write_text(build_ass(c, words, opt), encoding="utf-8")
        out = out_dir / f"{name}.mp4"
        dur = c["end"] - c["start"]
        log(f"Render {i}/{len(clips)}: {name} ({dur:.0f}s, mode {opt.mode})")
        t0 = time.time()
        run([
            ffmpeg, "-y", "-loglevel", "error", "-stats",
            "-ss", f"{c['start']:.3f}", "-t", f"{dur:.3f}", "-i", str(src),
            "-filter_complex", video_filter(opt.mode, c.get("focus_x", 0.5), ass_path.name),
            "-map", "[v]", "-map", "0:a?",
            "-c:v", "h264_videotoolbox" if opt.fast else "libx264",
            *(["-b:v", "10M"] if opt.fast else ["-preset", "medium", "-crf", "19"]),
            "-pix_fmt", "yuv420p",  # fps ikut sumber (paksa 30 = frame dobel di video 25fps)
            "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
            "-movflags", "+faststart", str(out),
        ], cwd=out_dir)
        if not opt.keep_ass:
            ass_path.unlink(missing_ok=True)
        log(f"  ✓ {out.name}  ({time.time()-t0:.0f}s)")
        outputs.append(out)

    # caption posting siap copy (+ hashtag campaign kalau ada)
    tag_list = getattr(opt, "hashtags", None)
    if not tag_list and (job / "job.json").exists():
        tag_list = json.loads((job / "job.json").read_text()).get("hashtags", [])
    tags = " ".join(tag_list or [])
    cap = out_dir / "captions.txt"
    cap.write_text("\n\n".join(
        f"{i:02d}. {c.get('title','')} {tags}".rstrip()
        + f"\n   ({fmt_ts(c['start'])}–{fmt_ts(c['end'])}) {c.get('reason','')}"
        for i, c in enumerate(clips, 1)), encoding="utf-8")
    return outputs


# ───────────────────────── CLI ─────────────────────────

def add_render_opts(p: argparse.ArgumentParser) -> None:
    g = p.add_argument_group("render")
    g.add_argument("--mode", choices=["crop", "blur"], default="crop",
                   help="crop = isi penuh 9:16 (potong kiri-kanan); blur = video utuh di tengah + latar blur")
    g.add_argument("--font", default="Arial Black")
    g.add_argument("--font-size", type=int, default=92)
    g.add_argument("--words-per-line", type=int, default=3)
    g.add_argument("--caption-y", type=float, default=0.70, help="posisi subtitle (0=atas, 1=bawah)")
    g.add_argument("--highlight", default="&H0000E5FF&", help="warna kata aktif, format ASS &HBBGGRR& (default kuning)")
    g.add_argument("--no-upper", dest="upper", action="store_false", help="jangan pakai huruf kapital")
    g.add_argument("--hook", action="store_true", help="tampilkan teks hook di atas selama 3.5 detik pertama")
    g.add_argument("--fast", action="store_true", help="encode pakai hardware Mac (lebih cepat, file lebih besar)")
    g.add_argument("--keep-ass", action="store_true", help="simpan file subtitle .ass")


def main() -> None:
    ap = argparse.ArgumentParser(description="RAFAKA Clipper — video panjang jadi klip 9:16 bersubtitle")
    sub = ap.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="pipeline lengkap: download → transkrip → pilih → render")
    r.add_argument("source", help="URL video atau path file lokal")
    r.add_argument("--name", help="nama folder job (default: otomatis)")
    r.add_argument("-n", "--clips", type=int, default=5, help="jumlah klip (default 5)")
    r.add_argument("--min", type=int, default=20, help="durasi minimal detik")
    r.add_argument("--max", type=int, default=60, help="durasi maksimal detik")
    r.add_argument("--lang", default="auto", help="bahasa audio: auto, id, en, …")
    r.add_argument("--whisper", choices=list(WHISPER_MODELS), default="turbo")
    r.add_argument("--claude-model", default="sonnet")
    r.add_argument("--campaign", help="file aturan campaign, mis. campaigns/indodax.txt "
                   "(baris '#hashtag' otomatis jadi hashtag caption)")
    add_render_opts(r)

    rr = sub.add_parser("render", help="render ulang job (setelah edit clips.json)")
    rr.add_argument("job", help="folder job, mis. jobs/podcast-abc")
    rr.add_argument("--only", type=int, nargs="+", help="nomor klip saja, mis. --only 1 3")
    add_render_opts(rr)

    t = sub.add_parser("transcript", help="cetak transkrip bertimestamp sebuah job")
    t.add_argument("job")

    opt = ap.parse_args()

    def job_dir(arg: str) -> Path:
        """Terima nama job ('indodax') atau path lengkap."""
        p = Path(arg).expanduser()
        return (p if p.exists() else JOBS / arg).resolve()

    if opt.cmd == "transcript":
        print((job_dir(opt.job) / "transcript.txt").read_text())
        return

    if opt.cmd == "render":
        job = job_dir(opt.job)
        if not (job / "clips.json").exists():
            die(f"{job}/clips.json tidak ada")
        outs = render(job, opt)
    else:
        name = opt.name or (slugify(Path(opt.source).stem) if not opt.source.startswith("http")
                            else time.strftime("%Y%m%d-%H%M%S"))
        job = JOBS / name
        job.mkdir(parents=True, exist_ok=True)
        brief, tags = "", []
        if opt.campaign:
            brief = Path(opt.campaign).expanduser().read_text(encoding="utf-8")
            tags = re.findall(r"(?m)^\s*(#\w+)\s*$", brief)
            if opt.mode == "blur" and re.search(r"blur", brief, re.I):
                log("⚠ Aturan campaign menyebut 'blur' — pastikan mode blur memang diizinkan!")
        opt.hashtags = tags
        (job / "job.json").write_text(json.dumps(
            {"source": opt.source, "campaign": opt.campaign, "hashtags": tags}, indent=2))
        video = fetch(opt.source, job)
        words = transcribe(video, job, opt.lang, opt.whisper)
        if not words:
            die("Transkrip kosong — apakah videonya ada suara orang bicara?")
        pick(job, words, opt.clips, opt.min, opt.max, opt.claude_model, brief)
        outs = render(job, opt)

    print()
    log(f"Selesai! {len(outs)} klip di: {job / 'out'}")
    subprocess.run(["open", str(job / "out")], check=False)


if __name__ == "__main__":
    main()
