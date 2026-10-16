#!/usr/bin/env python3
"""Render one frame directory under several frame-sequence settings and stitch
the results into slowed-down mp4s for side-by-side review.

    scripts/sweep.py FRAMES_DIR OUT_DIR [--fps 8] [--size 832] \
        dynamic:200:60:0.3 big:800:240:0.3 circles:500:150:0.3:4

Each variant is NAME:N:ADD:DROP[:MODE] -> `primitive -frames ... -n N
-new-shapes-per-frame ADD -drop-rate DROP [-m MODE]` (MODE is primitive's -m:
1 triangle (default), 2 rect, 3 ellipse, 4 circle, 5 rotated rect, 6 bezier,
7 rotated ellipse, 8 polygon, 9 stipple). Playback fps is independent of the
generation fps (24): lower it to slow the clip down. Writes OUT_DIR/<name>.mp4,
source.mp4 and compare.mp4 (source + variants in a 2-3 column grid). Needs
bin/primitive (make build) and ffmpeg. No shell: argv-list subprocesses only.
"""

import argparse
import math
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def run(argv: list[str], **kw) -> None:
    subprocess.run(argv, check=True, **kw)


def encode(frames: Path, out: Path, fps: int, ext: str) -> None:
    # pad: primitive's output height can be odd, which libx264 rejects.
    run(["ffmpeg", "-y", "-loglevel", "error", "-framerate", str(fps), "-i", str(frames / f"%04d.{ext}"),
         "-vf", "pad=ceil(iw/2)*2:ceil(ih/2)*2:0:0:white", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(out)])


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("frames", type=Path)
    ap.add_argument("out", type=Path)
    ap.add_argument("variants", nargs="+", help="NAME:N:ADD:DROP[:MODE]")
    ap.add_argument("--fps", type=int, default=8, help="playback fps (default 8; generation was 24)")
    ap.add_argument("--size", type=int, default=832, help="output width passed to primitive -s")
    args = ap.parse_args()

    binary = REPO / "bin" / "primitive"
    if not binary.exists():
        sys.exit(f"{binary} missing -- run `make build`")
    args.out.mkdir(parents=True, exist_ok=True)

    names = []
    for v in args.variants:
        name, n, add, drop, *mode = v.split(":")
        names.append(name)
        if not (args.out / name / "frames.json").exists():
            run([str(binary), "-frames", str(args.frames), "-out", str(args.out / name), "-s", str(args.size),
                 "-n", n, "-new-shapes-per-frame", add, "-drop-rate", drop, *(["-m", mode[0]] if mode else []), "-v"],
                stderr=open(args.out / f"{name}.log", "w"))
        encode(args.out / name, args.out / f"{name}.mp4", args.fps, "png")
    encode(args.frames, args.out / "source.mp4", args.fps, "png")

    # source + variants, 2-3 columns, padded to a full grid with the source.
    clips = ["source"] + names
    cols = 1 if len(clips) == 1 else 2 if len(clips) <= 4 else 3
    rows = math.ceil(len(clips) / cols)
    clips += ["source"] * (rows * cols - len(clips))
    layout = "|".join(f"{'+'.join(['w0'] * c) or '0'}_{'+'.join(['h0'] * r) or '0'}"
                      for r in range(rows) for c in range(cols))
    inputs = [a for c in clips for a in ("-i", str(args.out / f"{c}.mp4"))]
    run(["ffmpeg", "-y", "-loglevel", "error", *inputs, "-filter_complex",
         f"xstack=inputs={len(clips)}:layout={layout}", "-c:v", "libx264", "-pix_fmt", "yuv420p",
         str(args.out / "compare.mp4")])
    print(f"wrote {args.out}/compare.mp4 (order: {', '.join(clips)})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
