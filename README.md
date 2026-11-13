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

On Windows, use `python -m venv .venv`, `.venv\Scripts\activate`, and `copy .env.example .env`. Install FFmpeg and espeak-ng so both are on `PATH`.

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

## Brand cycle

Brands are separate from the AI & Future discovery channel. The seeded experiment is Everyday decisions. Its niche is stored on the brand row. Scoring does not assume that niche.

```bash
python -m app.cli run-cycle
python -m app.cli run-cycle --slug everyday-decisions
```

The cycle reads that brand's feeds, including a feed that is turned off for the global `discover` command, and writes a partial opportunity score. A factor with no source stays unknown. The total is not scaled up to hide the gap. A YouTube view sample is stored only when `YOUTUBE_API_KEY` is set, and it is not converted into a demand score or a dollar amount. Advertising revenue stays empty.

The cycle does not render a video and does not upload one. `publishing_mode` can be recorded as `test`, `approval`, or `autonomous`, and autonomous mode does not upload in this build. The daily publish limit defaults to 0. Pause a brand on `/business` and the next cycle skips it.

Affiliate programs, products, and sponsorships are whatever you type in. An active program needs a commission rate. An agreed sponsorship needs a rate. A lead is not an agreement. `/go/{product_id}` counts a click and redirects only while the program is active. Conversions are not measured. Nothing inserts those links into a video.

`BRAND_CYCLE_POLL_SECONDS` defaults to 0. That optional poller only scores. It does not render. Brand production runs from the scheduler poller, which is off when `SCHEDULER_POLL_SECONDS` is 0. Neither poller runs while the machine is off.

## Autonomous brands

The Production page is the operating dashboard. One process runs the poller. That poller is the only worker: it runs due topic schedules, and it runs each active brand. Topic schedules still stop before approval. A brand run does not. It discovers, selects, writes, renders, checks, and stores. It does not publish, and it does not open a social account.

This keeps running only while the server process is running. A laptop that is asleep or switched off does not produce anything. Use a machine or a container that stays on.

```bash
cp .env.example .env
# SCHEDULER_POLL_SECONDS must be above 0 or the poller never starts.
# AUTOMATION_ENABLED=true seeds the emergency switch on a brand-new database.
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Docker is the same process, with the database and media on a volume so a restart keeps them:

```bash
cp .env.example .env
docker compose up --build
```

`./data` holds `app.db` and the MP4 files. Stopping the container stops automation. Starting it again marks an in-flight job interrupted and the next due pass can retry it. Failed jobs stay in the database with the reason.

On `/production`:

1. Save a brand with its niche, audience, language, feed, video cap, and daily spend cap. Everyday decisions is only the seeded experiment. A new brand does not require a code change.
2. Resume it so its status is active.
3. Turn automation on. Emergency stop turns it off for every brand. Pause stops one brand.
4. Leave the process running. The poller calls the brand on its interval.

`python -m app.cli run-automation --slug your-brand` runs one pass immediately. It uses the same function as the poller. `produce-next` still renders one opportunity and is unchanged.

A pass runs the brand cycle, then the existing production pipeline, until the daily video count or the daily spend cap says stop. A zero daily spend cap starts no new job. Paid calls still fail closed at the global per-video and daily caps. Costs are stored by brand, opportunity, video, provider, and operation. Revenue and audience size are not invented.

The same opportunity cannot be rendered twice, and a near-duplicate title is skipped after a success. Two workers cannot hold the same opportunity in `running`. A failed attempt stays visible and is retried until the brand's attempt limit.

Editorial and production checks still apply. A global `EDITORIAL_GATE`, or a gate stored on the brand, refuses a weak script before the render. A file that passes is `ready` and marked ready for a later publishing step. Nothing is uploaded.

Logs are JSON on the process stdout (`LOG_LEVEL`). There is no separate log file.

## Produce a video

`produce-next` takes one eligible opportunity for a brand, downloads the source page, and writes a vertical MP4. Held items and opportunities that already succeeded are skipped. A thin page fails the job and the reason is stored. The file is not published.

```bash
python -m app.cli produce-next --brand everyday-decisions
python -m app.cli produce-next --brand everyday-decisions --opportunity OPPORTUNITY_ID
```

Run `run-cycle` first so the brand has opportunities. With `OPENAI_API_KEY` set, the script is an original narration checked against the source page. Invented numbers, copied sentences, and generic openers are rejected. With no key, or when that check fails, the script is an explicit fallback built from source sentences. That fallback is not an original AI script, and the video is `needs_review`.

`TTS_PROVIDER=auto` uses ElevenLabs when `ELEVENLABS_API_KEY` is set. A brand can store its own voice id. `ELEVENLABS_DEFAULT_VOICE_ID` and `ELEVENLABS_MODEL_ID` override the language voices and the model. If ElevenLabs is the planned voice and the call fails, the job fails. It does not silently switch to espeak and mark the file ready. espeak-ng and Edge stay available for offline and `render-short` renders, and a production job that used them is `needs_review`.

Scenes use clips from `data/assets/stock`. Register each file on `/production` with its source, license, and tags, and mark commercial use only when that permission was recorded. A file without that permission is stored and is not eligible. A scene with no tagged match stays empty. The job is `needs_review`, and the report does not claim that every scene has footage. The same clip is not used twice in one video. Local selection costs 0 and does not call a paid API. `PRODUCTION_FOOTAGE=pexels` plus `PEXELS_API_KEY` is optional and off by default. No vision model scores whether a clip matches the sentence. The report says semantic visuals are unverified.

The edit is a 1080×1920 film: footage is cropped to fill the frame, scenes crossfade, captions sit above the short-form UI, and the music bed is ducked under the narration. Subtitles come from the spoken text. Character timing is used only when ElevenLabs returns alignment.

A file is `ready` only when the required technical checks pass, the script is original, the voice is ElevenLabs, and every scene uses a distinct eligible licensed clip. `needs_review` means the file plays but a script, voice, or footage gap remains. `failed` means the file is unusable. `ready_for_publishing` is set only for `ready`. Nothing is published.

`PRODUCTION_VIDEO_CAP_USD` (default 1) and `PRODUCTION_DAILY_CAP_USD` (default 5) block a paid call that would pass the limit. The estimate is checked before the call. Local steps cost 0. `render-short` is unchanged and still uses the older card renderer. This command does not.

The Production page (`/production`) lists the script provider, voice, visual provider, clip count, fallbacks, quality status, cost rows, and a player for `ready` and `needs_review` files.

## Environment

| Variable | Role |
| --- | --- |
| `DATABASE_URL` | SQLite by default (`sqlite:///./data/app.db`). Models stay portable to PostgreSQL. |
| `DATA_DIR` | Media and the SQLite file's sibling folders. |
| `OPENAI_API_KEY` | Optional. Topics, scripts, localization, and metadata. |
| `ELEVENLABS_API_KEY` | Optional. Preferred production voice. Also set a voice id. |
| `TTS_PROVIDER` | `auto`, `espeak`, `edge`, or `elevenlabs`. `auto` picks ElevenLabs, then Edge, then espeak-ng. |
| `MONTHLY_SPEND_CAP_USD` | Paid calls over this cap fail with a clear error. `0` blocks every paid call. |
| `NEAR_DUPLICATE_THRESHOLD` | Jaccard cutoff for topic titles. Default `0.82`. |
| `FONT_PATH` | Optional. Defaults to DejaVu Sans, then Arial. |
| `TOPIC_SCORE_MINIMUM` | Auto-promote cutoff, 0–100. Default `55`. |
| `EDITORIAL_QUALITY_MINIMUM` | Editorial reject cutoff. Default `70`. |
| `EDITORIAL_GATE` | `true` blocks a render below the editorial cutoff. Default `false`. |
| `RESEARCH_MAX_ITEMS` | Items read from each feed. Default `8`. |
| `PRODUCTION_VIDEO_CAP_USD` | Paid-call ceiling for one production job. Default `1`. `0` blocks paid calls. |
| `PRODUCTION_DAILY_CAP_USD` | Paid-call ceiling for the UTC day. Default `5`. |
| `PRODUCTION_STOCK` | `none` or `wikimedia`. Default `none`. |
| `PRODUCTION_FOOTAGE` | `local` or `pexels`. Default `local`. Pexels runs only when this is `pexels` and a key is set. |
| `PEXELS_API_KEY` | Optional. Unused unless `PRODUCTION_FOOTAGE=pexels`. |
| `ELEVENLABS_MODEL_ID` | Optional model override. Empty uses `ELEVENLABS_MODEL`. |
| `ELEVENLABS_DEFAULT_VOICE_ID` | Optional voice used when the brand has no voice id. |
| `SCHEDULER_POLL_SECONDS` | Seconds between poller ticks. `0` does not start the worker. The same tick runs brands. |
| `SCHEDULER_ENABLED` | Seeds the topic-schedule switch on a new database. |
| `AUTOMATION_ENABLED` | Seeds the emergency switch on a new database. `false` produces no brand video until you turn it on. |

Leave the provider keys empty to stay on the explicit script, voice, and footage fallbacks. Those videos are `needs_review`. They are not marked ready.

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
