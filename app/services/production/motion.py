"""Motion-graphics renderer: storyboard -> Remotion props -> MP4 -> audio mix -> visual QA.

The picture is drawn by the Remotion project in `renderer/` (React, headless Chrome).
Each scene is one of a fixed set of purpose-built templates; the script chooses the
template and fills its fields. Numbers shown on screen must exist in the sources.
Captions are burned by Remotion from Kokoro's sentence-exact timings.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

from app.core.errors import AppError
from app.services.probes import MediaError, media_summary, run_ffmpeg

RENDERER = Path(__file__).resolve().parents[3] / "renderer"
FPS = 30
TRANSITION_FRAMES = 12

OBJECTS = (
    "card", "cash", "coin", "wallet", "receipt", "brain", "cart", "phone", "terminal", "tag", "gauge",
    "piggy", "clock", "calendar", "person", "chart", "lightbulb", "wine", "bottle", "jar", "box", "chair",
    "taxi", "mug", "house", "menu", "gift",
)  # fmt: skip
# What the less obvious objects look like, so the writer picks the one that shows its sentence.
OBJECT_NOTES = (
    "tag = price tag; terminal = card payment terminal or tip screen; gauge = dial from low to high; "
    "wine = wine glass; jar = jam jar; box = flat-pack cardboard box; chair = a chair being assembled; "
    "mug = coffee mug labelled MINE; menu = price list with three options; chart = a generic rising line, "
    "only for a trend in general, never to picture a specific study result (use bars or stat for that)"
)

# Template -> what it is for, and its fields. This text goes into the writer's prompt.
TEMPLATES: dict[str, str] = {
    "hook": "opening statement. params: headline (max 9 words, the hook itself), highlight (one word), "
    "object, object2 (optional)",
    "question": "a question the viewer now wants answered. params: question (max 12 words), highlight, "
    "object, object2 (optional illustration of the two possibilities)",
    "versus": "two things compared. params: title (optional), left {label (1-2 words), object, note}, "
    "right {label, object, note}, winner (left|right|none), winner_word (a narration word when the "
    "winner is revealed), winner_tone (good|bad)",
    "stat": "one number from the sources. params: value (number only), prefix, suffix (e.g. % or x), "
    "label (max 10 words), object (optional), value_word (narration word when it appears), tone (good|bad)",
    "bars": "2-4 numbers from the sources compared. params: title, bars [{label, value}], prefix, unit, tone",
    "steps": "a mechanism or list in 2-4 short steps. params: title (optional), "
    "steps [{text (max 7 words), object}]",
    "object": "one idea shown as one object. params: label (max 9 words), highlight, object, "
    "sublabel (optional)",
    "study": "the evidence. params: source (e.g. 'Scientific Reports, 2021'), finding (max 18 words, "
    "hedged as the source is), caveat (the limit that matters, max 14 words), highlight",
    "brain": "what happens in the brain. params: label (max 9 words), tag (1-2 words on the lit region), "
    "tone (reward|pain|neutral; neutral when the region did NOT respond or differ), "
    "light_word (narration word when it lights), sublabel",
    "gauge": "a level that rises or falls. params: label, from (0-1), to (0-1), low (1 word), high (1 word)",
    "takeaway": "the closing idea. params: line (max 12 words), highlight, object (optional), "
    "mark (check for a settled idea | question when the honest answer is still open)",
}
_NUMBER = re.compile(r"\d+(?:\.\d+)?")
# Words that describe the script's structure, not its content. A viewer never sees them.
_STRUCTURAL = {
    "hook", "question", "evidence", "takeaway", "intro", "introduction", "outro", "conclusion", "cta",
    "summary", "setup", "payoff", "reveal", "beat", "scene", "the hook", "the question", "the takeaway",
}  # fmt: skip


def available() -> bool:
    return (RENDERER / "node_modules" / "remotion").is_dir() and shutil.which("npx") is not None


def brief() -> str:
    names = ", ".join(OBJECTS)
    lines = "\n".join(f"- {name}: {text}" for name, text in TEMPLATES.items())
    return f"Scene templates:\n{lines}\nObjects (only these): {names}.\nObject notes: {OBJECT_NOTES}."


def _numbers(value) -> set[str]:
    return set(_NUMBER.findall(json.dumps(value)))


def number_in_context(value, context: str, source_text: str) -> bool:
    """The number appears in a source sentence that talks about the same thing as the scene."""
    from app.services.textutil import split_sentences, tokens

    number = str(value).strip()
    if not number:
        return False
    wanted = tokens(context)
    for sentence in split_sentences(source_text):
        if (
            re.search(rf"(?<![\d.]){re.escape(number)}(?![\d])", sentence)
            and len(wanted & tokens(sentence)) >= 2
        ):
            return True
    return False


def normalise(beat: dict, allowed_numbers: set[str], source_text: str = "") -> dict:
    """Keep a valid template and its fields. Unsourced numbers demote the scene to an object."""
    template = str(beat.get("template") or "").strip().lower()
    requested = template
    params = beat.get("params") if isinstance(beat.get("params"), dict) else {}
    asked_objects = _object_names(params)
    if template not in TEMPLATES:
        template = "object"
    shown = {key: value for key, value in params.items() if not key.endswith("_word")}
    if _numbers(shown) - allowed_numbers:
        template, params = "object", {"label": _short(beat.get("text", "")), "object": params.get("object")}
    params = _clean_objects(params)
    if template == "bars" and len(params.get("bars") or []) < 2:
        template, params = (
            "object",
            {"label": params.get("title") or _short(beat.get("text", "")), "object": "chart"},
        )
    if template == "stat":
        try:
            float(str(params.get("value")))
            context = f"{params.get('label', '')} {beat.get('text', '')}"
            meaningful = float(str(params.get("value"))) >= 2 or params.get("suffix") or params.get("prefix")
            if source_text and not (
                meaningful and number_in_context(params.get("value"), context, source_text)
            ):
                raise ValueError("number out of context")
        except ValueError:
            template, params = (
                "object",
                {
                    "label": params.get("label") or _short(beat.get("text", "")),
                    "object": params.get("object"),
                },
            )
    if template == "versus" and not (
        isinstance(params.get("left"), dict) and isinstance(params.get("right"), dict)
    ):
        template, params = "object", {"label": _short(beat.get("text", "")), "object": "cart"}
    if template == "brain" and re.search(
        r"\b(not|no|never|didn't|did not|doesn't|wasn't)\b", str(beat.get("text", "")).lower()
    ):
        # A sentence that says a region did not respond must not show it lit.
        params = {**params, "tone": "neutral"}
    if template == "takeaway" and not params.get("mark"):
        line = f"{params.get('line', '')} {beat.get('text', '')}".lower()
        open_words = r"\b(open|unclear|unknown|unsettled|conjectural|may|might|possible|not yet|still)\b"
        params = {**params, "mark": "question" if re.search(open_words, line) else "check"}
    kicker = str(beat.get("kicker") or "").strip()[:28]
    if kicker.lower().rstrip("?!:. ") in _STRUCTURAL:
        kicker = ""
    scene = {"template": template, "kicker": kicker, "params": params}
    replaced = sorted(name for name in asked_objects if name not in OBJECTS)
    if requested != template or replaced:
        # Kept for QA: a storyboard that lost many of its pictures no longer illustrates its narration.
        scene["demoted"] = {"requested": requested, "unknown_objects": replaced}
    return scene


def _object_names(value) -> set[str]:
    found: set[str] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            if key in {"object", "object2"} and item:
                found.add(str(item).strip().lower())
            else:
                found |= _object_names(item)
    elif isinstance(value, list):
        for item in value:
            found |= _object_names(item)
    return found


def _short(text: str, words: int = 9) -> str:
    """A whole short sentence from the narration for a replaced picture, or '' (caption only).

    Never a sentence cut off with an ellipsis: the viewer would read half a thought.
    """
    from app.services.textutil import split_sentences

    sentences = [s.strip() for s in split_sentences(str(text)) if s.strip()]
    fitting = [s for s in sentences if len(s.split()) <= words]
    return max(fitting, key=lambda s: len(s.split())) if fitting else ""


def _clean_objects(value):
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            if key in {"object", "object2"}:
                name = str(item or "").strip().lower()
                out[key] = name if name in OBJECTS else ("lightbulb" if key == "object" else None)
            else:
                out[key] = _clean_objects(item)
        return {key: item for key, item in out.items() if item is not None}
    if isinstance(value, list):
        return [_clean_objects(item) for item in value]
    return value


def words_from_alignment(alignment: dict) -> list[tuple[str, float, float]]:
    chars = alignment.get("characters") or []
    starts = alignment.get("character_start_times_seconds") or []
    ends = alignment.get("character_end_times_seconds") or []
    words: list[tuple[str, float, float]] = []
    current, start, end = "", 0.0, 0.0
    for char, s, e in zip(chars, starts, ends, strict=False):
        if char.isspace():
            if current:
                words.append((current, start, end))
            current = ""
            continue
        if not current:
            start = float(s)
        current += char
        end = float(e)
    if current:
        words.append((current, start, end))
    return words


def captions_from_words(words: list[tuple[str, float, float]], fps: int = FPS) -> list[dict]:
    """Short caption groups: at most 3 words or 20 characters, never across a sentence end."""
    groups: list[list[tuple[str, float, float]]] = []
    current: list[tuple[str, float, float]] = []
    for word in words:
        trial = " ".join(w for w, _s, _e in [*current, word])
        if current and (len(current) >= 3 or len(trial) > 20):
            groups.append(current)
            current = []
        current.append(word)
        if word[0][-1:] in ".!?":
            groups.append(current)
            current = []
    if current:
        groups.append(current)
    captions = []
    for index, group in enumerate(groups):
        start = group[0][1]
        nxt = groups[index + 1][0][1] if index + 1 < len(groups) else group[-1][2] + 0.4
        end = min(nxt, group[-1][2] + 0.6)
        captions.append(
            {
                "from": round(start * fps),
                "to": max(round(end * fps), round(start * fps) + 6),
                "words": [{"w": w, "f0": round(s * fps), "f1": round(e * fps)} for w, s, e in group],
            }
        )
    return captions


def build_props(
    scenes: list[dict], durations: list[float], alignment: dict, *, brand: str, theme: str
) -> dict:
    words = words_from_alignment(alignment)
    frames = [max(round(d * FPS), 12) for d in durations]
    hook = next((scene["params"] for scene in scenes if scene["template"] == "hook"), {})
    for scene in scenes:
        if scene["template"] == "question" and not scene["params"].get("object") and hook.get("object"):
            scene["params"] = {**scene["params"], "object": hook["object"], "object2": hook.get("object2")}
            scene["params"] = {key: value for key, value in scene["params"].items() if value is not None}
    out_scenes = []
    cursor = 0.0
    for scene, duration, count in zip(scenes, durations, frames, strict=True):
        start, end = cursor, cursor + duration
        local = [
            {"w": w, "f0": round((s - start) * FPS), "f1": round((e - start) * FPS)}
            for w, s, e in words
            if start - 0.05 <= s < end
        ]
        out_scenes.append({**scene, "durationInFrames": count, "words": local})
        cursor = end
    total = sum(frames)
    captions = captions_from_words(words)
    for caption in captions:
        caption["to"] = min(caption["to"], total)
    return {
        "theme": theme,
        "brand": brand,
        "durationInFrames": total,
        "transitionFrames": TRANSITION_FRAMES,
        "scenes": out_scenes,
        "captions": captions,
    }


def render(props: dict, work: Path, output: Path) -> Path:
    if not available():
        raise AppError("The motion renderer is not installed. Run `npm install` in the renderer folder.")
    work.mkdir(parents=True, exist_ok=True)
    props_path = work / "motion-props.json"
    props_path.write_text(json.dumps(props), encoding="utf-8")
    silent = work / "motion-silent.mp4"
    command = [
        shutil.which("npx") or "npx",
        "remotion",
        "render",
        "src/index.ts",
        "Short",
        str(silent.resolve()),
        f"--props={props_path.resolve()}",
        "--codec=h264",
        "--crf=18",
        "--log=error",
    ]
    proc = subprocess.run(
        command, cwd=RENDERER, capture_output=True, text=True, encoding="utf-8", errors="replace"
    )
    if proc.returncode != 0 or not silent.is_file():
        raise MediaError((proc.stderr or proc.stdout or "Remotion render failed.")[-1200:])
    return silent


def sfx_track(path: Path, props: dict) -> Path:
    """A whoosh on every cut, a soft pop when a number or a winner lands, a chime on the takeaway."""
    import numpy as np
    import soundfile

    from app.services.production.scene_engine import _noise_sweep, _tone

    rate = 44100
    total = props["durationInFrames"] / FPS
    track = np.zeros(int(total * rate) + rate)
    cursor = 0
    for index, scene in enumerate(props["scenes"]):
        start = int(cursor / FPS * rate)
        if index:
            sweep = _noise_sweep(rate, 0.34, rising=index % 2 == 0) * 0.16
            at = max(start - int(0.2 * rate), 0)
            track[at : at + len(sweep)] += sweep[: max(len(track) - at, 0)]
        template = scene["template"]
        if template in {"stat", "versus", "bars", "brain"}:
            p = (_tone(rate, 880, 0.18, 0.04) + 0.5 * _tone(rate, 1320, 0.18, 0.03)) * 0.12
            at = start + int(0.6 * rate)
            track[at : at + len(p)] += p[: max(len(track) - at, 0)]
        if template == "takeaway":
            chime = sum(_tone(rate, f, 0.7, 0.3) for f in (523.25, 659.25, 783.99)) * 0.05
            at = start + int(0.5 * rate)
            track[at : at + len(chime)] += chime[: max(len(track) - at, 0)]
        cursor += scene["durationInFrames"]
    track = np.clip(track[: int(total * rate)], -1, 1)
    soundfile.write(str(path), track.astype("float32"), rate, subtype="PCM_16")
    return path


def mix(silent: Path, voice: Path, music: Path, sfx: Path, output: Path) -> Path:
    duration = media_summary(silent)["duration"]
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        output.unlink()
    filters = (
        "[1:a]loudnorm=I=-16:TP=-1.5:LRA=11,aresample=48000,asplit=2[va][vb];"
        "[2:a]aresample=48000,volume=0.10[mbed];"
        "[mbed][vb]sidechaincompress=threshold=0.03:ratio=8:attack=20:release=300[ducked];"
        "[3:a]aresample=48000,volume=0.5[fx];"
        f"[va][ducked][fx]amix=inputs=3:duration=first:normalize=0,apad,atrim=0:{duration:.3f},"
        f"afade=t=out:st={max(duration - 0.3, 0):.3f}:d=0.3[aout]"
    )
    run_ffmpeg(
        [
            "-i", str(silent), "-i", str(voice), "-i", str(music), "-i", str(sfx),
            "-filter_complex", filters, "-map", "0:v", "-map", "[aout]",
            "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-movflags", "+faststart",
            "-t", f"{duration:.3f}", str(output),
        ]
    )  # fmt: skip
    return output
