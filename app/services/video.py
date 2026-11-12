"""FFmpeg render for vertical shorts and a 16:9 long profile."""

from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.core.jobs import log_event, set_progress
from app.core.retry import call_with_retry
from app.domain import CATEGORY_LABELS, PROFILE_SIZE
from app.models import Asset, AudioFile, Job, PublishedPost, Script, ScriptVersion, Topic, Video
from app.services.assets import resolve_music, resolve_visuals
from app.services.fonts import find_font
from app.services.probes import MediaError, media_summary, run_ffmpeg
from app.services.quality import evaluate
from app.services.subtitles import parse_srt, render_srt, time_cues, validate_cue_coverage
from app.services.thumbnail import build_thumbnail
from app.services.voice import ensure_audio


def scene_durations(scenes: list[dict], total: float) -> list[float]:
    weights = [max(len(str(scene.get("narration") or "")), 1) for scene in scenes]
    durations = [total * weight / sum(weights) for weight in weights]
    floor = 0.35
    if durations and min(durations) < floor and floor * len(durations) < total:
        lifted = [max(item, floor) for item in durations]
        overflow = sum(lifted) - total
        adjustable = [item - floor for item in lifted]
        pool = sum(adjustable)
        if pool > 0:
            lifted = [
                item - overflow * (extra / pool) for item, extra in zip(lifted, adjustable, strict=True)
            ]
        durations = lifted
    durations[-1] = total - sum(durations[:-1])
    return durations


def _escape(path: Path) -> str:
    text = path.resolve().as_posix()
    return text.replace("\\", r"\\").replace(":", r"\:").replace("'", r"\'")


def _subtitle_filter(srt: Path, size: tuple[int, int], font_family: str, fonts_dir: Path) -> str:
    _width, height = size
    margin = 110 if height > _width else 64
    style = (
        f"Fontname={font_family},FontSize=18,PrimaryColour=&H00FFFFFF,"
        "OutlineColour=&H00101820,BorderStyle=1,Outline=2,Alignment=2,"
        f"MarginV={margin}"
    )
    return (
        f"subtitles='{_escape(srt)}':fontsdir='{_escape(fonts_dir)}':force_style='{style}'"
    )


def render_mp4(
    *,
    images: list[Path],
    durations: list[float],
    voice: Path,
    music: Path,
    srt: Path,
    output: Path,
    size: tuple[int, int],
    font_family: str,
    fonts_dir: Path,
    kinds: list[str] | None = None,
    music_loop: bool = False,
    burn_subs: bool = True,
) -> None:
    width, height = size
    fade = 0.15
    fade_color = "0x243044"
    visual_kinds = kinds or ["image"] * len(images)
    args: list[str] = []
    for image, duration, kind in zip(images, durations, visual_kinds, strict=True):
        if kind == "clip":
            args.extend(["-stream_loop", "-1", "-t", f"{duration:.3f}", "-i", str(image)])
        else:
            args.extend(["-loop", "1", "-t", f"{duration:.3f}", "-i", str(image)])
    args.extend(["-i", str(voice)])
    if music_loop:
        args.extend(["-stream_loop", "-1", "-i", str(music)])
    else:
        args.extend(["-i", str(music)])
    count = len(images)
    filters: list[str] = []
    for index, duration in enumerate(durations):
        fade_out = max(duration - fade, 0)
        filters.append(
            f"[{index}:v]scale={width}:{height}:force_original_aspect_ratio=increase,"
            f"crop={width}:{height},fps=30,format=yuv420p,setsar=1,"
            f"fade=t=in:st=0:d={fade}:color={fade_color},"
            f"fade=t=out:st={fade_out:.3f}:d={fade}:color={fade_color}[v{index}]"
        )
    concat = "".join(f"[v{index}]" for index in range(count))
    filters.append(f"{concat}concat=n={count}:v=1:a=0[vcat]")
    video_label = "[vcat]"
    if burn_subs:
        filters.append(f"[vcat]{_subtitle_filter(srt, size, font_family, fonts_dir)}[vout]")
        video_label = "[vout]"
    # loudnorm on this ffmpeg build emits 192 kHz. Resample before the mix or amix
    # drops every frame and the MP4 is written without an audio stream.
    filters.append(f"[{count}:a]loudnorm=I=-16:TP=-1.5:LRA=11,aresample=48000,asplit=2[va][vb]")
    filters.append(f"[{count + 1}:a]aresample=48000,volume=0.22[mbed]")
    filters.append("[mbed][vb]sidechaincompress=threshold=0.03:ratio=8:attack=20:release=300[ducked]")
    filters.append("[va][ducked]amix=inputs=2:duration=first:normalize=0[aout]")
    total = sum(durations)
    args.extend(
        [
            "-filter_complex",
            ";".join(filters),
            "-map",
            video_label,
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
    output.parent.mkdir(parents=True, exist_ok=True)
    run_ffmpeg(args)


def burn_subtitles(
    *,
    master: Path,
    srt: Path,
    output: Path,
    size: tuple[int, int],
    font_family: str,
    fonts_dir: Path,
) -> None:
    """Re-burn captions onto a finished master. Voice, cards, and music stay as they are."""
    output.parent.mkdir(parents=True, exist_ok=True)
    run_ffmpeg(
        [
            "-i",
            str(master),
            "-vf",
            _subtitle_filter(srt, size, font_family, fonts_dir),
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-crf",
            "23",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "copy",
            "-movflags",
            "+faststart",
            str(output),
        ]
    )


def _scene_visuals(session: Session, video: Video) -> list[Asset]:
    visuals: list[Asset] = []
    for asset_id in video.asset_ids or []:
        asset = session.get(Asset, asset_id)
        if asset is not None and asset.kind in {"graphic", "image", "clip"}:
            visuals.append(asset)
    return visuals


def _music_asset(session: Session, video: Video) -> Asset | None:
    for asset_id in video.asset_ids or []:
        asset = session.get(Asset, asset_id)
        if asset is not None and asset.kind == "music":
            return asset
    return None


def _exported_checksums(session: Session, video_id: str) -> set[str]:
    rows = session.scalars(
        select(Video.checksum)
        .join(PublishedPost, PublishedPost.video_id == Video.id)
        .where(Video.id != video_id, Video.checksum != "")
    )
    return {row for row in rows if row}


def _duplicate_of(session: Session, video: Video) -> str | None:
    return session.scalar(
        select(Video.id)
        .join(PublishedPost, PublishedPost.video_id == Video.id)
        .where(
            Video.script_version_id == video.script_version_id,
            Video.profile == video.profile,
            Video.settings_hash == video.settings_hash,
            Video.id != video.id,
        )
    )


def _apply_gate(
    session: Session,
    video: Video,
    version: ScriptVersion,
    output: Path,
    size: tuple[int, int],
) -> None:
    asset_paths = []
    for asset_id in video.asset_ids or []:
        asset = session.get(Asset, asset_id)
        if asset is not None:
            asset_paths.append(Path(asset.path))
    report = evaluate(
        video_path=output,
        subtitle_path=Path(video.subtitle_path),
        asset_paths=asset_paths,
        claims=list(version.factual_claims or []),
        expected_size=size,
        exported_checksums=_exported_checksums(session, video.id),
        duplicate_of=_duplicate_of(session, video),
        subtitles_burned=True,
    )
    video.path = str(output)
    video.subtitles_burned = True
    video.quality_report = {"passed": report["passed"], "checks": report["checks"]}
    video.checksum = report["checksum"]
    video.approved = False
    video.approved_at = None
    video.status = "ready" if report["passed"] else "blocked"
    video.error = "" if report["passed"] else "Quality gate did not pass."


def _write_cues(version: ScriptVersion, audio: AudioFile, work: Path, video: Video) -> Path:
    cues = time_cues(version.narration, audio.duration_seconds)
    srt_path = work / "captions.srt"
    srt_path.write_text(render_srt(cues), encoding="utf-8")
    video.subtitle_path = str(srt_path)
    video.subtitle_edited = False
    return srt_path


def render_video(session: Session, video: Video, job: Job | None) -> Video:
    component = (job.component if job else "all") or "all"
    version = session.get(ScriptVersion, video.script_version_id)
    script = session.get(Script, video.script_id)
    if version is None or script is None:
        raise AppError("Script version is missing.")
    topic = session.get(Topic, script.topic_id)
    work = Path(video.work_dir)
    work.mkdir(parents=True, exist_ok=True)
    size = PROFILE_SIZE[video.profile]
    video.width, video.height = size
    master = Path(video.master_path) if video.master_path else work / "master.mp4"
    reburn_only = component == "subtitles" and master.is_file()

    set_progress(session, job, "voice", 15)
    audio: AudioFile | None = None
    if reburn_only and video.audio_file_id:
        audio = session.get(AudioFile, video.audio_file_id)
        if audio is None or not Path(audio.path).is_file():
            reburn_only = False
            audio = None
        else:
            log_event(session, job, "voice", "Voice already exists. Subtitle burn skipped synthesis.")
    if audio is None:
        audio = ensure_audio(
            session,
            version,
            language=script.language,
            stability=video.stability,
            similarity=video.similarity,
            voice_id=video.voice_id,
            engine=video.tts_provider or None,
            force=component == "audio",
        )
        video.audio_file_id = audio.id
        video.duration_seconds = audio.duration_seconds
        video.settings_hash = audio.settings_hash

    set_progress(session, job, "subtitles", 35)
    srt_path = Path(video.subtitle_path) if video.subtitle_path else work / "captions.srt"
    keep_edit = bool(video.subtitle_edited) and srt_path.is_file() and component == "subtitles"
    if keep_edit:
        try:
            validate_cue_coverage(parse_srt(srt_path.read_text(encoding="utf-8")), audio.duration_seconds)
        except ValueError as exc:
            raise AppError(str(exc)) from exc
        log_event(session, job, "subtitles", "Using the edited subtitle file.")
        video.subtitle_path = str(srt_path)
    elif component in {"all", "audio", "subtitles"} or not srt_path.is_file():
        srt_path = _write_cues(version, audio, work, video)
        log_event(session, job, "subtitles", "Subtitles timed from the narration.")

    font_file, family = find_font(False)
    output = work / "video.mp4"
    if reburn_only:
        set_progress(session, job, "render", 75)
        log_event(session, job, "render", "Burning subtitles onto the existing master.")
        call_with_retry(
            lambda: burn_subtitles(
                master=master,
                srt=Path(video.subtitle_path),
                output=output,
                size=size,
                font_family=family,
                fonts_dir=font_file.parent,
            ),
            retries=2,
            base_delay=0.5,
            retry_on=(MediaError,),
        )
        set_progress(session, job, "quality", 90)
        _apply_gate(session, video, version, output, size)
        return video

    set_progress(session, job, "visuals", 55)
    visuals = _scene_visuals(session, video)
    scenes = list(version.scenes)
    rebuild_visuals = component in {"all", "visuals"} or len(visuals) != len(scenes)
    music_loop = False
    if rebuild_visuals:
        if component == "render":
            raise AppError("Visuals are missing. Regenerate the visuals component first.")
        label = CATEGORY_LABELS.get(topic.category, topic.category) if topic else "AI"
        visuals, kinds = resolve_visuals(
            session,
            scenes=scenes,
            mood=version.music_mood,
            label=label,
            size=size,
            work_dir=work,
        )
        music, music_loop = resolve_music(
            session,
            scenes=scenes,
            mood=version.music_mood,
            duration=audio.duration_seconds,
            work_dir=work,
        )
        thumb = build_thumbnail(
            session,
            text=version.thumbnail_text,
            video_id=video.id,
            size=size,
            mood=version.music_mood,
        )
        video.thumbnail_path = thumb.path
        video.asset_ids = [item.id for item in visuals] + [music.id, thumb.id]
        reused = sum(1 for item in visuals if item.library)
        log_event(
            session,
            job,
            "visuals",
            f"{reused} library visual(s), {len(visuals) - reused} generated card(s).",
        )
    else:
        kinds = ["clip" if item.kind == "clip" else "image" for item in visuals]
        music = _music_asset(session, video)
        music_loop = bool(music and music.library)
        long_enough = music_loop
        if music is not None and Path(music.path).is_file() and not music_loop:
            try:
                long_enough = media_summary(Path(music.path))["duration"] + 0.05 >= audio.duration_seconds
            except MediaError:
                long_enough = False
        if music is None or not long_enough:
            music, music_loop = resolve_music(
                session,
                scenes=scenes,
                mood=version.music_mood,
                duration=audio.duration_seconds,
                work_dir=work,
            )
            kept = [
                asset_id
                for asset_id in video.asset_ids
                if (row := session.get(Asset, asset_id)) is not None and row.kind != "music"
            ]
            video.asset_ids = kept + [music.id]

    set_progress(session, job, "render", 75)
    images = [Path(item.path) for item in visuals]
    durations = scene_durations(scenes, audio.duration_seconds)
    if len(images) != len(durations):
        raise AppError("Scene visuals do not match the script scenes.")
    if music is None:
        raise AppError("Music is missing.")
    master = work / "master.mp4"

    def _encode() -> None:
        render_mp4(
            images=images,
            durations=durations,
            voice=Path(audio.path),
            music=Path(music.path),
            srt=Path(video.subtitle_path),
            output=master,
            size=size,
            font_family=family,
            fonts_dir=font_file.parent,
            kinds=kinds,
            music_loop=music_loop,
            burn_subs=False,
        )
        burn_subtitles(
            master=master,
            srt=Path(video.subtitle_path),
            output=output,
            size=size,
            font_family=family,
            fonts_dir=font_file.parent,
        )

    call_with_retry(_encode, retries=2, base_delay=0.5, retry_on=(MediaError,))
    video.master_path = str(master)
    set_progress(session, job, "quality", 90)
    _apply_gate(session, video, version, output, size)
    return video
