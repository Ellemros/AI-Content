# AI Content Factory

Local factory for faceless short videos in English and Czech. A topic becomes a script, narration, burned-in subtitles, and a vertical MP4. Nothing is uploaded. An approved export is a file on disk plus a database record.

Phase 1 runs with no paid API key. OpenAI and ElevenLabs are optional.

## Setup

Python 3.12+, FFmpeg, and espeak-ng. espeak-ng is the offline voice. Edge TTS is used when `TTS_PROVIDER=auto` and the machine can reach Microsoft's speech service.

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

On Windows, use `py -3.12 -m venv .venv`, `.venv\Scripts\activate`, and `copy .env.example .env`. Install FFmpeg and espeak-ng so both are on `PATH`.

## First video, no API keys

```bash
python -m app.cli render-short --tts espeak --language en --title "How machines learn from examples"
```

The same path as a script:

```bash
python scripts/render_first_video.py
```

Czech uses a separate narration, not a word-for-word translation:

```bash
python -m app.cli render-short --tts espeak --language cs --title "Jak se stroje učí z příkladů"
```

The command prints the MP4 path, `1080x1920 h264 / aac`, and an export path under `data/exports/`. A second run of the same title is rejected as a duplicate. Add `--reuse` to render again from the existing topic.

## Scheduler

The Production page is http://127.0.0.1:8000/production . Turn the poller on there. On a brand-new database, `SCHEDULER_ENABLED=true` in `.env` seeds that switch; after the row exists, the page is the control and a restart is not required.

A schedule runs topic → script → voice → render on its interval, or immediately with Run now. It always stops before approval. It does not export and it does not upload. Approve the video on its page, then record the local export.

```bash
python -m app.cli run-schedule --name "Morning short"
```

`SCHEDULER_POLL_SECONDS` is how often the process looks for a due schedule. `0` keeps the poller from starting, which is what the tests use. Run now and `run-schedule` still work.

## Library and subtitles

Register images, clips, and music on the Library page with a source and a license. On a script, attach one to a scene. That file is reused across videos. A scene without an attachment still gets a generated card, and a video without library music still gets the generated bed.

The video page can edit the SRT and re-burn captions onto the existing master, or rebuild the cues from the narration. Voice and scene art stay put when the master file is already there.

`--export` requires `--approve`. The default is both. `--no-approve` leaves the file unexported until you approve it in the dashboard.

## Dashboard

```bash
uvicorn app.main:app --reload
```

Open http://127.0.0.1:8000 . From there you can add a topic, generate or edit a script, compare English and Czech, render a video, watch progress, approve it, and record a local export. The publishing page refuses any upload.

## Environment

| Variable | Role |
| --- | --- |
| `DATABASE_URL` | SQLite by default (`sqlite:///./data/app.db`). Models stay portable to PostgreSQL. |
| `DATA_DIR` | Media and the SQLite file's sibling folders. |
| `OPENAI_API_KEY` | Optional. Topics, scripts, localization, and metadata. |
| `ELEVENLABS_API_KEY` | Optional. Also set `ELEVENLABS_VOICE_ID_EN` and `ELEVENLABS_VOICE_ID_CS`. |
| `TTS_PROVIDER` | `auto`, `espeak`, `edge`, or `elevenlabs`. `auto` picks ElevenLabs, then Edge, then espeak-ng. |
| `MONTHLY_SPEND_CAP_USD` | Paid calls over this cap fail with a clear error. `0` blocks every paid call. |
| `NEAR_DUPLICATE_THRESHOLD` | Jaccard cutoff for topic titles. Default `0.82`. |
| `FONT_PATH` | Optional. Defaults to DejaVu Sans, then Arial. |

Leave both keys empty to stay on offline script templates and a local voice. Offline drafts are marked as drafts and do not invent citations.

## Turn on OpenAI and ElevenLabs

Put the keys in `.env` and restart the app.

```
OPENAI_API_KEY=sk-...
ELEVENLABS_API_KEY=...
ELEVENLABS_VOICE_ID_EN=...
ELEVENLABS_VOICE_ID_CS=...
TTS_PROVIDER=auto
```

With a key set, script generation and Czech localization call OpenAI. Voice calls ElevenLabs when `TTS_PROVIDER` is `auto` or `elevenlabs`. Responses are cached. Estimated cost is stored on `api_usage` and checked against `MONTHLY_SPEND_CAP_USD` before the call. If a paid call would exceed the cap, the pipeline stops and tells you to raise the cap or clear the key. It does not silently fall back.

## Tests

```bash
pip install -r requirements-dev.txt
pytest
ruff check app tests scripts
```

## Docker

```bash
cp .env.example .env
docker compose up --build
```

The image includes FFmpeg, espeak-ng, and DejaVu. Media is stored in `./data`. An optional FFmpeg shell is `docker compose --profile tools run --rm ffmpeg -version`.
