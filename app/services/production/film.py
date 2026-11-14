"""Scene motion, narration, original music, and burned subtitles. One vertical MP4."""

from pathlib import Path

from app.services.fonts import find_font
from app.services.probes import run_ffmpeg
from app.services.subtitles import render_srt, time_cues
from app.services.video import scene_durations


def _escape(path: Path) -> str:
    text = path.resolve().as_posix()
    return text.replace("\\", r"\\").replace(":", r"\:").replace("'", r"\'")


def write_subtitles(
    narration: str,
    duration: float,
    path: Path,
    alignment: dict | None = None,
    background: str = "dark",
) -> Path:
    from app.services.subtitles import cues_from_alignment, render_ass

    cues = None
    if alignment:
        try:
            cues = cues_from_alignment(alignment, duration)
        except ValueError:
            cues = None
    if cues is None:
        cues = time_cues(narration, duration, max_chars=32)
    path.write_text(render_srt(cues), encoding="utf-8")
    path.with_suffix(".ass").write_text(render_ass(cues, background), encoding="utf-8")
    return path


_XFADE = {"fade", "slideleft", "slideup", "circleopen", "wipeleft", "smoothright", "fadeblack", "radial"}


def _visual_input(path: Path, kind: str, duration: float) -> list[str]:
    if kind in {"clip", "stickman"}:
        return ["-stream_loop", "-1", "-t", f"{duration:.3f}", "-i", str(path)]
    return ["-loop", "1", "-t", f"{duration:.3f}", "-i", str(path)]


def _visual_filter(index: int, kind: str, duration: float) -> str:
    progress = f"min(t/{max(duration, 0.2):.3f}\\,1)"
    if kind == "stickman":
        body = (
            "scale=1080:1920:force_original_aspect_ratio=increase,"
            "crop=1080:1920,fps=30,format=yuv420p,setsar=1"
        )
        return f"[{index}:v]{body}[v{index}]"
    if kind == "clip":
        body = (
            f"scale=1296:2304:force_original_aspect_ratio=increase,"
            f"crop=1080:1920:x='(in_w-out_w)/2+(in_w-out_w)*0.06*{progress}':"
            "y='(in_h-out_h)/2',fps=30,format=yuv420p,setsar=1"
        )
    else:
        motion = f"t/{duration:.3f}" if index % 2 == 0 else f"(1-t/{duration:.3f})"
        body = (
            f"scale=1240:2204,crop=1080:1920:"
            f"x='(in_w-out_w)*{motion}':y='(in_h-out_h)*{motion}',"
            "fps=30,format=yuv420p,setsar=1"
        )
    return f"[{index}:v]{body}[v{index}]"


def _join_visuals(
    count: int, play: list[float], overlap: float, transitions: list[str] | None = None
) -> list[str]:
    if count == 1:
        return ["[v0]null[vcat]"]
    filters = []
    shown = play[0]
    for index in range(1, count):
        offset = shown - overlap
        source = "v0" if index == 1 else f"x{index - 1}"
        dest = "vcat" if index == count - 1 else f"x{index}"
        kind = (transitions or [])[index] if transitions and index < len(transitions) else "fade"
        if kind not in _XFADE:
            kind = "fade"
        filters.append(
            f"[{source}][v{index}]xfade=transition={kind}:duration={overlap:.3f}:offset={offset:.3f}[{dest}]"
        )
        shown = shown + play[index] - overlap
    return filters


def render_film(
    *,
    images: list[Path],
    durations: list[float],
    voice: Path,
    music: Path,
    srt: Path,
    output: Path,
    kinds: list[str] | None = None,
    sfx: Path | None = None,
    transitions: list[str] | None = None,
) -> None:
    if len(images) != len(durations) or not images:
        raise ValueError("Each scene image needs a duration.")
    scene_kinds = kinds or ["image"] * len(images)
    if len(scene_kinds) != len(images):
        raise ValueError("Each scene needs a visual kind.")
    _font_path, family = find_font(False)
    fonts_dir = _font_path.parent
    count = len(images)
    overlap = (0.28 if transitions else 0.12) if count > 1 else 0.0
    # A little extra on each input keeps the crossfade inside the clip. -t trims it back.
    play = [
        duration + (overlap if index < count - 1 else 0.0) + 0.05 for index, duration in enumerate(durations)
    ]
    args: list[str] = []
    filters: list[str] = []
    for index, (image, duration, kind) in enumerate(zip(images, play, scene_kinds, strict=True)):
        args.extend(_visual_input(image, kind, duration))
        filters.append(_visual_filter(index, kind, duration))
    filters.extend(_join_visuals(count, play, overlap, transitions))
    captions = srt.with_suffix(".ass")
    if captions.is_file():
        filters.append(f"[vcat]subtitles='{_escape(captions)}':fontsdir='{_escape(fonts_dir)}'[vout]")
    else:
        style = (
            f"Fontname={family},FontSize=54,PrimaryColour=&H00FFFFFF,"
            "OutlineColour=&H00101820,BorderStyle=1,Outline=4,Alignment=2,MarginV=320"
        )
        filters.append(
            f"[vcat]subtitles='{_escape(srt)}':fontsdir='{_escape(fonts_dir)}':force_style='{style}'[vout]"
        )
    args.extend(["-i", str(voice), "-i", str(music)])
    if sfx is not None:
        args.extend(["-i", str(sfx)])
    fade_out = max(sum(durations) - 0.15, 0)
    filters.append(f"[{count}:a]loudnorm=I=-16:TP=-1.5:LRA=11,aresample=48000,asplit=2[va][vb]")
    filters.append(f"[{count + 1}:a]aresample=48000,volume=0.12[mbed]")
    filters.append("[mbed][vb]sidechaincompress=threshold=0.03:ratio=8:attack=20:release=300[ducked]")
    if sfx is not None:
        filters.append(f"[{count + 2}:a]aresample=48000,volume=0.45[sfx]")
        mix = "[va][ducked][sfx]amix=inputs=3:duration=first:normalize=0,"
    else:
        mix = "[va][ducked]amix=inputs=2:duration=first:normalize=0,"
    filters.append(f"{mix}afade=t=in:st=0:d=0.08,afade=t=out:st={fade_out:.3f}:d=0.12[aout]")
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
