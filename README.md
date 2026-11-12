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

`--export` requires `--approve`. The default is both. `--no-approve` leaves the file unexported until you approve it in the dashboard.

## Dashboard

```bash
uvicorn app.main:app --reload
```

Open http://127.0.0.1:8000 . From there you can add a topic, generate or edit a script, compare English and Czech, render a video, watch progress, approve it, and record a local export. The publishing page refuses any upload.

## Research, no API keys

The brand is AI & Future. English is the channel. Czech stays a separate narration, not a second channel and not translated subtitles on the English cut.

```bash
python -m app.cli discover
```

That fetches the configured public feeds (arXiv cs.AI, the Google AI blog, and OpenAI News), stores each item, and scores it. A strong on-niche item with a confirmed original URL becomes a topic in `Researching`. A trusted site is not enough when the title is outside AI and future technology. Nothing is rendered or uploaded. Run it again and unchanged feeds are skipped. Some publishers answer 403 to this client. Those items stay in the queue instead of being treated as verified.

The Research page (`/research`) shows the same queue. Add another HTTPS feed there, or turn one off. Scores are editorial heuristics. Search volume and competition stay empty until a real data source is connected. A feed summary is labeled as a lead, not a verified fact. The original URL is checked with a short request. The page body is not turned into claims.

On a script page, **Run editorial review** stores an audience, a central question, a takeaway, the evidence, and a score from 0 to 100. A specific unsourced claim fails the review. `EDITORIAL_GATE` defaults to off, so `render-short` behaves as before. Set `EDITORIAL_GATE=true` to refuse a render below `EDITORIAL_QUALITY_MINIMUM` (default 70).

```bash
python -m app.cli review-script --id SCRIPT_ID
```

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
| `TOPIC_SCORE_MINIMUM` | Auto-promote cutoff, 0–100. Default `55`. |
| `EDITORIAL_QUALITY_MINIMUM` | Editorial reject cutoff. Default `70`. |
| `EDITORIAL_GATE` | `true` blocks a render below the editorial cutoff. Default `false`. |
| `RESEARCH_MAX_ITEMS` | Items read from each feed. Default `8`. |

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
