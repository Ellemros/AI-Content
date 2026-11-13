"""Scene motion, narration, original music, and burned subtitles. One vertical MP4."""

from pathlib import Path

from app.services.fonts import find_font
from app.services.probes import run_ffmpeg
from app.services.subtitles import render_srt, time_cues
from app.services.video import scene_durations


def _escape(path: Path) -> str:
    text = path.resolve().as_posix()
    return text.replace("\\", r"\\").replace(":", r"\:").replace("'", r"\'")


def write_subtitles(narration: str, duration: float, path: Path) -> Path:
    cues = time_cues(narration, duration, max_chars=40)
    path.write_text(render_srt(cues), encoding="utf-8")
    return path


def render_film(
    *,
    images: list[Path],
    durations: list[float],
    voice: Path,
    music: Path,
    srt: Path,
    output: Path,
) -> None:
    if len(images) != len(durations) or not images:
        raise ValueError("Each scene image needs a duration.")
    _font_path, family = find_font(False)
    fonts_dir = _font_path.parent
    args: list[str] = []
    filters: list[str] = []
    for index, (image, duration) in enumerate(zip(images, durations, strict=True)):
        args.extend(["-loop", "1", "-t", f"{duration:.3f}", "-i", str(image)])
        progress = f"t/{duration:.3f}" if index % 2 == 0 else f"(1-t/{duration:.3f})"
        fade = 0.12 if duration > 0.5 else 0.0
        fade_out = max(duration - fade, 0)
        fades = ""
        if fade:
            fades = (
                f",fade=t=in:st=0:d={fade}:color=0x101820,"
                f"fade=t=out:st={fade_out:.3f}:d={fade}:color=0x101820"
            )
        filters.append(
            f"[{index}:v]scale=1240:2204,crop=1080:1920:"
            f"x='(in_w-out_w)*{progress}':y='(in_h-out_h)*{progress}',"
            f"fps=30,format=yuv420p,setsar=1{fades}[v{index}]"
        )
    count = len(images)
    concat = "".join(f"[v{index}]" for index in range(count))
    filters.append(f"{concat}concat=n={count}:v=1:a=0[vcat]")
    style = (
        f"Fontname={family},FontSize=36,PrimaryColour=&H00FFFFFF,"
        "OutlineColour=&H00101820,BorderStyle=1,Outline=3,Alignment=2,MarginV=180"
    )
    filters.append(
        f"[vcat]subtitles='{_escape(srt)}':fontsdir='{_escape(fonts_dir)}':force_style='{style}'[vout]"
    )
    args.extend(["-i", str(voice), "-i", str(music)])
    filters.append(f"[{count}:a]loudnorm=I=-16:TP=-1.5:LRA=11,aresample=48000,asplit=2[va][vb]")
    filters.append(f"[{count + 1}:a]aresample=48000,volume=0.18[mbed]")
    filters.append("[mbed][vb]sidechaincompress=threshold=0.03:ratio=8:attack=20:release=300[ducked]")
    filters.append("[va][ducked]amix=inputs=2:duration=first:normalize=0[aout]")
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        output.unlink()
    total = sum(durations)
    args.extend(
        [
            "-filter_complex",
            ";".join(filters),
            "-map",
            "[vout]",
            "-map",
            "[aout]",
            "-t",
            f"{total:.3f}",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-crf",
            "23",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "160k",
            "-ar",
            "48000",
            "-movflags",
            "+faststart",
            str(output),
        ]
    )
    run_ffmpeg(args)


def durations_for(scenes: list[dict], total: float) -> list[float]:
    return scene_durations(scenes, total)
