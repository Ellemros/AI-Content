# AI Content Factory

Automated faceless explainer videos: research a topic, write and check an original script, narrate it with a local voice, animate it with stickman scenes, render a vertical MP4, and (when you switch it on) publish it through the platforms' official APIs.

The recommended first channel and the reasoning are in [docs/strategy.md](docs/strategy.md).

## Windows quick start (CMD)

Run these from the project folder, one at a time. Python 3.12+ and FFmpeg on `PATH` are required; espeak-ng and administrator rights are not.

```
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
python -m app.cli install-voice
```

Put `OPENAI_API_KEY=...` in `.env` (Notepad: `notepad .env`). Then create the recommended channel (paused, publishing off), find topics, and render one video:

```
python -m app.cli create-recommended-brand
python -m app.cli run-cycle --slug money-minds
python -m app.cli produce-next --brand money-minds
```

The last command prints the MP4 path. `uvicorn app.main:app` serves the dashboard at http://127.0.0.1:8000/production .

## Publishing (off by default)

`PUBLISHING_ENABLED=false` refuses every upload. Videos still get per-platform titles, descriptions, and tags (`python -m app.cli plan-posts --video VIDEO_ID`), and `python -m app.cli posts` lists the queue.

Gates for every upload, in order:
1. `PUBLISHING_ENABLED=true`.
2. The emergency switch is on.
3. The brand's mode is `approval` (you approve each video) or `autonomous` (every `ready` video).
4. The video is `ready`; `needs_review` is never published.
5. The daily limit per platform is not reached.
6. The account is connected.

An upload that may have reached a platform without confirming is marked `needs_check` and is never retried automatically, so a post cannot go out twice.

One-time setup per platform. Tokens are saved under `data\secrets\`, which Git ignores.

- **YouTube (Shorts and long-form).**
  1. In Google Cloud Console, create a project and enable *YouTube Data API v3* and *YouTube Analytics API*.
  2. Configure the OAuth consent screen (External, add yourself as a test user), then create an OAuth client of type *Desktop app*.
  3. Download its JSON to `data\secrets\youtube_client_secret.json`.
  4. Run `python -m app.cli connect-youtube --brand money-minds` and approve in the browser.

  Google keeps uploads from an unverified API project **private** until the project passes Google's API audit; `YOUTUBE_PRIVACY=private` matches that. Apply for the audit when you are ready to go public.
- **TikTok.**
  1. Create an app at developers.tiktok.com with *Login Kit* and the *Content Posting API* (scopes `video.publish`, `user.info.basic`), and register a redirect URI.
  2. Put `TIKTOK_CLIENT_KEY` and `TIKTOK_CLIENT_SECRET` in `.env`.
  3. Open the authorization URL, approve, and run `python -m app.cli connect-tiktok --brand money-minds --code CODE --redirect-uri URI`.

  Until TikTok audits the app, posts are visible only to you (`SELF_ONLY`).
- **Instagram and Facebook Reels.**
  1. You need an Instagram *Business* account linked to a Facebook Page, and a Meta app with `instagram_business_content_publish` (or `instagram_content_publish`), `pages_manage_posts`, and `pages_read_engagement`.
  2. Generate a long-lived Page access token.
  3. In the same CMD window, run `set META_PAGE_TOKEN=...`, then `python -m app.cli connect-meta --brand money-minds --page-id PAGE_ID --ig-user-id IG_USER_ID`.

Then `python -m app.cli set-publishing --brand money-minds --mode approval --daily-limit 1`, set `PUBLISHING_ENABLED=true`, and the scheduler poller (or `python -m app.cli publish-due`) uploads approved videos at the brand's posting hour.

Analytics: `python -m app.cli fetch-analytics` stores daily YouTube metrics per post. `python -m app.cli profit-report --brand money-minds` shows views, reported revenue, production cost, and profit by video, platform, and story structure. TikTok and Meta insights are not wired yet.

## Housekeeping

`python -m app.cli cleanup-work --days 14` lists per-job work folders older than 14 days (scene clips and stems); add `--apply` to delete them. Final MP4s are kept.

## Setup

Python 3.12+ and FFmpeg. The production voice is Kokoro-82M, a local neural voice (Apache-2.0 weights, CPU only, about 350 MB). Download it once with `python -m app.cli install-voice`. espeak-ng is optional and only used by the older `render-short` path and the tests.

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

`test-production` is a separate one-shot. It does not read feeds, does not change the status or hold reason of existing opportunities, and does not upload. It sends one fixed everyday-decision article through the same production pipeline. OpenAI and ElevenLabs must already be set; otherwise the command stops before creating a row. With `PRODUCTION_FOOTAGE=stickman` the picture is an original animation, so the command does not wait for a licensed clip.

```bash
python -m app.cli test-production --slug everyday-decisions
```

Run `run-cycle` first so the brand has opportunities. With `OPENAI_API_KEY` set, the script is an original narration checked against the source page. Invented numbers, copied sentences, and generic openers are rejected. With no key, or when that check fails, the script is an explicit fallback built from source sentences. That fallback is not an original AI script, and the video is `needs_review`.

`TTS_PROVIDER=auto` uses Kokoro for English when the model is installed, then Edge, then espeak-ng. `auto` never picks a paid engine: ElevenLabs is used only with `TTS_PROVIDER=elevenlabs`. `KOKORO_VOICE` (default `af_heart`) and `KOKORO_SPEED` choose the voice. Kokoro speaks one sentence at a time, so every sentence has an exact start and end. Scene cuts land in the pause after a scene's last sentence, and a caption never runs across the end of a sentence. Edge and espeak-ng renders stay `needs_review`.

The script is a storyboard: 5 to 7 beats, each with the stickman's action and a 1 to 3 word on-screen keyword, plus every factual claim labelled `established` or `hedged`. A draft is rejected when it copies the source, adds a number, or states flatly what the source only suggests ("appear to", "may", "linked"). The editorial review then runs on the draft. A rejected draft goes back to the model once with the reasons (`SCRIPT_ATTEMPTS`, default 2). If it still fails, the job fails and nothing is rendered; stitched source sentences are not rendered in place of a rejected model script. Earlier drafts of the same topic do not count as repetition. Only other videos do.

`PRODUCTION_FOOTAGE=stickman` is the production picture. Each scene is an original stickman animation chosen from the narration: the same figure walks, compares, chooses, checks a clock, or notices a mark. Limb motion is drawn into the frames. A still card is not substituted, and a scene that does not move fails the job. The old licensed library stays on `/production` and is used only when `PRODUCTION_FOOTAGE=local`. `PRODUCTION_FOOTAGE=pexels` plus `PEXELS_API_KEY` remains optional. No vision model watches the finished frames. Stickman jobs record `directed-from-narration`; stock jobs stay `unverified`.

Stickman Video Director (MIT) was evaluated again on 2026-11-14 and is still not wired in. It is a set of agent instructions plus example Node renderers that an AI coding agent rewrites and reviews for every video; there is no command that turns a script into an MP4 unattended. Its B1 minimal line-art look is the reference for the built-in renderer, which now takes its action and keyword from the script. Open Generative AI is an MIT studio in front of image and video models; the application being open source does not make Flux, Kling, Sora, or Veo free, and a local weight is not practical here without a large GPU. AppFlowy is an AGPL workspace and is not part of rendering. VoiceStudio can be selected with `TTS_PROVIDER=voicestudio` only after `VOICESTUDIO_COMMERCIAL_USE=yes`, because the application is AGPL-3.0 and the default OmniVoice weights are CC-BY-NC. `auto` does not select it, and a failed VoiceStudio or ElevenLabs call does not fall through to espeak.

The edit is a 1080×1920 film: footage is cropped to fill the frame, scenes crossfade, captions sit above the short-form UI, and the music bed is ducked under the narration. Subtitles come from the spoken text. Character timing is used only when ElevenLabs returns alignment.

A file is `ready` only when the required technical checks pass, the script is original, the voice is Kokoro, ElevenLabs, or a confirmed VoiceStudio voice, the editorial review passed, and the picture is either a full set of distinct licensed clips or moving stickman scenes. espeak, Edge, a fallback script, a still frame, or a missing clip keeps the file at `needs_review` or `failed`. `ready_for_publishing` is set only for `ready`. Nothing is published.

`PRODUCTION_VIDEO_CAP_USD` (default 1) and `PRODUCTION_DAILY_CAP_USD` (default 5) block a paid call that would pass the limit. The estimate is checked before the call. Local steps cost 0. `render-short` is unchanged and still uses the older card renderer. This command does not.

The Production page (`/production`) lists the script provider, voice, visual provider, clip count, fallbacks, quality status, cost rows, and a player for `ready` and `needs_review` files.

## Environment

| Variable | Role |
| --- | --- |
| `DATABASE_URL` | SQLite by default (`sqlite:///./data/app.db`). Models stay portable to PostgreSQL. |
| `DATA_DIR` | Media and the SQLite file's sibling folders. |
| `OPENAI_API_KEY` | Optional. Topics, scripts, localization, and metadata. |
| `ELEVENLABS_API_KEY` | Optional. Preferred production voice. Also set a voice id. |
| `TTS_PROVIDER` | `auto`, `kokoro`, `espeak`, `edge`, `elevenlabs`, or `voicestudio`. `auto` picks Kokoro (English), then Edge, then espeak-ng. It never picks ElevenLabs. |
| `KOKORO_VOICE` | Kokoro voice. Default `af_heart`. Others include `am_michael`, `bf_emma`, `bm_george`. |
| `KOKORO_SPEED` | Speaking rate. Default `1.0`. |
| `SCRIPT_ATTEMPTS` | Model drafts per production job, the second with the reviewer's reasons. Default `2`. |
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
| `PRODUCTION_FOOTAGE` | `stickman`, `local`, or `pexels`. Default `stickman`. |
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

`pytest -m "not integration"` runs the unit tests. Platform uploads in them are mocked (`httpx.MockTransport`), and they check request order and safety gates, not live acceptance. `pytest -m integration` renders real MP4s with the real Kokoro voice and FFmpeg; those tests are skipped, not faked, when the Kokoro model is missing.

## Docker

```bash
cp .env.example .env
docker compose up --build
```

The image includes FFmpeg, espeak-ng, and DejaVu. Media is stored in `./data`. An optional FFmpeg shell is `docker compose --profile tools run --rm ffmpeg -version`.
