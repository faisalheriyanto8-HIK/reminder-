#!/usr/bin/env python3
"""
RAFAKA Promo — susun video promo vertikal 9:16 dari materi campaign (footage + banner + teks).

Untuk campaign tipe event/brand (mis. konten.com "Miranda Color Corner") yang mewajibkan
banner resmi tampil utuh, teks hook, dan CTA — bukan memotong video panjang.

Pemakaian:
  python3 promo.py campaigns/miranda-promo.json            # render semua video di spec
  python3 promo.py campaigns/miranda-promo.json --only 2   # video ke-2 saja

Spec JSON (path materi relatif ke "base"):
{
  "name": "miranda-color-corner",          # nama folder output (Drive CLIPPERS/<name>/)
  "base": "~/Movies/RAFAKA-Clipper/jobs/miranda-color-corner",
  "audio": {"src": "materi/x.mp4", "start": 0, "volume": 1.0},   # musik latar semua video
  "pad_color": "#F6E9E1",                   # warna latar untuk fit "pad"
  "caption_tags": "@brand #tag",            # ditambahkan ke captions.txt
  "videos": [
    {"title": "...", "caption": "...",
     "segments": [
        {"src": "materi/a.mp4", "start": 3.3, "dur": 4, "fit": "fill"},   # fill | blur | pad
        {"image": "materi/banner.jpg", "dur": 4.5, "fit": "pad"}],
     "texts": [
        {"t0": 0, "t1": 4, "style": "Hook", "text": "BARIS 1\\NBARIS 2"},
        {"t0": 4, "t1": 9, "style": "Top", "text": "..."},       # Hook | Top | Bottom | Label | CtaTop | CtaBottom
     ]}
  ]
}
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from clipper import need_ffmpeg_ass, log, die, slugify, ass_escape, ass_time  # noqa: E402

W, H = 1080, 1920
FPS = 30


def hex_to_ass(c: str) -> str:
    c = c.lstrip("#")
    r, g, b = c[0:2], c[2:4], c[4:6]
    return f"&H00{b}{g}{r}&".upper()


def build_ass(texts: list[dict], accent: str, font: str) -> str:
    acc = hex_to_ass(accent)
    # Name, size, primary, outline/box colour, borderstyle(1=outline,3=box), outline, shadow, align, marginV
    styles = [
        ("Hook", 74, "&H00FFFFFF&", acc, 3, 22, 0, 8, 230),
        ("Top", 52, "&H00FFFFFF&", "&H00000000&", 1, 6, 2, 8, 120),
        ("Bottom", 48, "&H00FFFFFF&", "&H00000000&", 1, 6, 2, 2, 110),
        ("Label", 34, "&H00FFFFFF&", "&H99000000&", 3, 10, 0, 7, 60),
        ("CtaTop", 64, "&H00FFFFFF&", acc, 3, 20, 0, 8, 105),
        ("CtaBottom", 44, "&H00FFFFFF&", acc, 3, 14, 0, 2, 70),
    ]
    lines = []
    for n, size, prim, out, bs, ol, sh, al, mv in styles:
        back = out if bs == 3 else "&H80000000&"
        lines.append(f"Style: {n},{font},{size},{prim},{prim},{out},{back},1,0,0,0,100,100,0,0,"
                     f"{bs},{ol},{sh},{al},70,70,{mv},1")
    ev = []
    for t in texts:
        style = t.get("style", "Top")
        txt = ass_escape(t["text"]).replace("\\\\N", "\\N")  # izinkan \N untuk ganti baris
        fx = "" if style == "Label" else r"{\fad(180,180)}"
        if style in ("Hook", "CtaTop"):
            fx = r"{\fad(120,180)\fscx85\fscy85\t(0,160,\fscx100\fscy100)}"
        ev.append(f"Dialogue: 0,{ass_time(t['t0'])},{ass_time(t['t1'])},{style},,0,0,0,,{fx}{txt}")
    return f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
""" + "\n".join(lines) + """

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
""" + "\n".join(ev) + "\n"


def seg_filter(k: int, seg: dict, pad_color: str) -> str:
    """Filter untuk input ke-k -> [s{k}] 1080x1920, fps tetap, durasi pas."""
    fit = seg.get("fit", "fill")
    dur = float(seg["dur"])
    src = f"[{k}:v]"
    if fit == "fill":      # isi penuh 9:16 (crop tepi)
        body = f"{src}scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H}"
    elif fit == "blur":    # utuh di tengah, latar blur dari video yang sama
        body = (f"{src}split[b{k}][f{k}];"
                f"[b{k}]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
                f"boxblur=30:3,eq=brightness=-0.12[bg{k}];"
                f"[f{k}]scale={W}:{H}:force_original_aspect_ratio=decrease[fg{k}];"
                f"[bg{k}][fg{k}]overlay=(W-w)/2:(H-h)/2")
    elif fit == "pad":     # utuh di tengah, latar warna polos (untuk banner resmi)
        body = (f"{src}scale={W}:{H}:force_original_aspect_ratio=decrease,"
                f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color={pad_color}")
    else:
        die(f"fit tidak dikenal: {fit}")
    return f"{body},fps={FPS},setsar=1,format=yuv420p,trim=duration={dur:.3f},setpts=PTS-STARTPTS[s{k}]"


def render_video(i: int, v: dict, spec: dict, base: Path, out_dir: Path, ffmpeg: str) -> Path:
    segs = v["segments"]
    total = sum(float(s["dur"]) for s in segs)
    name = f"{i:02d}-{slugify(v.get('title', 'promo'))}"
    work = base / "work"          # file .ass sementara tetap lokal
    work.mkdir(exist_ok=True)
    ass = work / f"{name}.ass"
    ass.write_text(build_ass(v.get("texts", []), spec.get("accent", "#F0479A"),
                             spec.get("font", "Arial Black")), encoding="utf-8")

    cmd = [ffmpeg, "-y", "-loglevel", "error", "-stats"]
    for s in segs:
        if "image" in s:
            cmd += ["-loop", "1", "-framerate", str(FPS), "-t", f"{float(s['dur']):.3f}",
                    "-i", str(base / s["image"])]
        else:
            cmd += ["-ss", f"{float(s.get('start', 0)):.3f}", "-t", f"{float(s['dur']):.3f}",
                    "-i", str(base / s["src"])]
    a = spec.get("audio")
    if a:
        cmd += ["-ss", f"{float(a.get('start', 0)):.3f}", "-t", f"{total:.3f}", "-i", str(base / a["src"])]

    n = len(segs)
    fc = ";".join(seg_filter(k, s, spec.get("pad_color", "#F6E9E1")) for k, s in enumerate(segs))
    fc += ";" + "".join(f"[s{k}]" for k in range(n)) + f"concat=n={n}:v=1:a=0[cat];[cat]ass='{ass.name}'[v]"
    maps = ["-map", "[v]"]
    if a:
        vol = float(a.get("volume", 1.0))
        fc += (f";[{n}:a]atrim=duration={total:.3f},asetpts=PTS-STARTPTS,volume={vol},"
               f"afade=t=in:d=0.3,afade=t=out:st={max(total - 1.5, 0):.3f}:d=1.5[a]")
        maps += ["-map", "[a]"]

    out = out_dir / f"{name}.mp4"
    cmd += ["-filter_complex", fc, *maps,
            "-c:v", "libx264", "-preset", "medium", "-crf", "19", "-pix_fmt", "yuv420p", "-r", str(FPS),
            "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
            "-movflags", "+faststart", str(out)]
    log(f"Render {i}: {name} ({total:.1f}s, {n} bagian)")
    t0 = time.time()
    subprocess.run(cmd, check=True, cwd=work)
    log(f"  ✓ {out.name}  ({time.time() - t0:.0f}s)")
    return out


def output_root(name: str, out: str | None) -> Path:
    if out:
        return Path(out).expanduser() / name
    drives = sorted(Path("~/Library/CloudStorage").expanduser().glob("GoogleDrive-*/My Drive/CLIPPERS"))
    return (drives[0] if drives else Path("~/Movies/RAFAKA-Clipper/out").expanduser()) / name


def main() -> None:
    ap = argparse.ArgumentParser(description="RAFAKA Promo — video promo 9:16 dari footage + banner + teks")
    ap.add_argument("spec", help="file spec JSON, mis. campaigns/miranda-promo.json")
    ap.add_argument("--only", type=int, nargs="+", help="nomor video saja, mis. --only 1 3")
    ap.add_argument("--out", help="folder induk hasil (default: Google Drive My Drive/CLIPPERS)")
    opt = ap.parse_args()

    spec = json.loads(Path(opt.spec).read_text(encoding="utf-8"))
    base = Path(spec["base"]).expanduser()
    if not base.exists():
        die(f"folder materi tidak ada: {base}")
    out_dir = output_root(spec["name"], opt.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    log(f"Output ke: {out_dir}")
    ffmpeg = need_ffmpeg_ass()

    outs = []
    for i, v in enumerate(spec["videos"], 1):
        if opt.only and i not in opt.only:
            continue
        outs.append(render_video(i, v, spec, base, out_dir, ffmpeg))

    tags = spec.get("caption_tags", "")
    (out_dir / "captions.txt").write_text("\n\n".join(
        f"{i:02d}. {v.get('caption', v.get('title', ''))} {tags}".rstrip()
        for i, v in enumerate(spec["videos"], 1)), encoding="utf-8")
    log(f"Selesai! {len(outs)} video di: {out_dir}")
    subprocess.run(["open", str(out_dir)], check=False)


if __name__ == "__main__":
    main()
