# Money Minds production pilot

Status: **for your review**. Nothing has been published, and no video is approved until you approve it.

## The workflow (one command)

```
python -m app.cli pilot --brand money-minds --videos 5 --budget 5
```

| Step | What runs | Where | Paid? |
|---|---|---|---|
| 1. Discover | The brand's three ScienceDaily feeds, plus the evergreen backlog `topics/money-minds.json` (viewer questions with open-access primary sources). The medical and financial-advice holds apply to every topic. | `pilot.discover`, `pilot.seed_backlog`, `strategy.add_topic` | No |
| 2. Rank | One gpt-5.4-mini call scores each eligible topic from 1 to 5 on niche fit, audience interest, retention, advertiser friendliness, research quality, and visual potential. It also assigns a theme and a story structure. A topic qualifies only with niche fit ≥4, research ≥3, advertiser friendliness ≥3, and no criterion below 2. | `pilot.rank` | about $0.01 |
| 3. Choose | The best qualified topics, one per theme. Titles close to an already-made video are skipped, and the story structures differ. | `pilot.choose` | No |
| 4. Research dossier | Each source is fetched politely with a descriptive User-Agent; Wikipedia goes through its REST API, and PDFs are read with pypdf. Site furniture is removed and each source is labelled [S1], [S2]. Sites that refuse bots are skipped, not bypassed. | `research/dossier.py` | No |
| 5. Script and storyboard | gpt-5.4 writes the narration and an 11-template storyboard together, citing sources. | `pipeline._model_writer`, `_motion_system` | yes |
| 6. Checks on each draft | <ul><li>Editorial heuristics: hook, originality against earlier videos, banned phrases, number grounding, hedges, study model.</li><li>An independent gpt-5.4-mini fact check of every sentence against the sources (unsupported, contradicted, overstated, missing caveat).</li><li>A gpt-5.4-mini story editor: does the hook deliver, is there a real payoff, does each picture show its sentence, is there filler, is a correlation presented as a cause.</li></ul>Any failure sends specific feedback for a rewrite, with at most 3 drafts. | `pipeline._write_script`, `_fact_check`, `_story_review` | yes |
| 7. Storyboard validation | Numbers on screen must appear in a source sentence about the same thing. Unknown templates or objects are replaced, and each replacement is recorded for QA. | `motion.normalise` | No |
| 8. Narration | Kokoro (local) synthesises each sentence, giving word timings. | `voice.ensure_audio` | No |
| 9. Render | Remotion renders the approved visual style; FFmpeg mixes voice, music, and sound effects with loudness normalisation. | `motion.render`, `motion.mix` | No |
| 10. Subtitles and metadata | Captions come from the word timings and are burned in; an SRT file is also written. The title, description, and hashtags are stored but not published. | `motion.captions_from_words`, `subtitles.py` | No |
| 11. QA on the MP4 | **Required:** full decode, format, A/V sync, duration matches the voice, no empty scenes, caption readability, captions cover the speech, loudness. **Flagged for a person:** safe-area strips, scenes that look alike, storyboard variety, illustrations replaced by the validator. | `visual_qa.py` | No |
| 12. Save for approval | <ul><li>All checks pass: status `ready`, awaiting human approval.</li><li>A flag: status `needs_review`, with the reason.</li><li>A required failure: status `blocked`.</li></ul>The publishing queue now refuses any unapproved video whose report requires human review, even in autonomous mode. | `pipeline._run`, `publishing/queue.py` | No |
| 13. Package | Each attempt gets a folder: `video.mp4`, `contact-sheet.png`, `script.md`, `storyboard.json`, `sources.json`, `verification.json`, `qa.json`, `cost.json`, `summary.json`. There is also one `pilot-report.md` and one `pilot-report.json`. | `pilot.package` | No |

## Cost control

- `--budget` is a hard limit for the whole run. Before every paid call, the run checks whether its spend so far plus that call's **worst case** would exceed the limit. The worst case is every output token the request allows: 16,000 for reasoning models. If it would, the call is not made.
- A new video only starts if at least $0.75 remains. That is the measured worst case of a three-draft video with a margin.
- Failed drafts and failed videos count in full.
- The monthly cap and the per-video and daily caps still apply as well.
- A failed topic is replaced by the next ranked topic, not retried, so one bad topic cannot drain the budget.
- **What "actual cost" means here:** each model response reports its token counts, and the cost is those counts multiplied by the per-token prices configured in `app/providers/openai_provider.py`. That is as close to actual as the system can measure. It is not reconciled against an OpenAI invoice. The configured prices come from third-party listings, so check them against your OpenAI usage page.

## Pilot results (2026-11-15)

Full per-video detail is in `output/review/money-minds-pilot/pilot-report.md`. Each video has its own folder there. That folder is not in Git, because it holds MP4s and a SQLite-derived copy of the scripts.

| | |
|---|---|
| Attempts | 11 (four runs of the same command under one budget) |
| Passed every automated check (`ready`, awaiting your approval) | 5: tip screens, decoy effect, IKEA effect, income and happiness, jam study |
| Rendered, then flagged by my review (`needs_review`) | 2: wine price (decorative chart that looks like data; made before the new objects), endowment effect (misattributed citation) |
| Failed, nothing rendered | 3: happiness attempt 1 (PMC served a CAPTCHA), jam attempt 1 (source condenser dropped the results paragraph), denomination effect (fact-checker rejected invented study details three times) |
| Interrupted by me | 1 (stopped run 1 to add illustrations) |
| Recorded API spend | $2.2291 (topic ranking $0.0449, the rest scripts, fact checks, story reviews; failed attempts included) |
| Counted against the $5 limit | $2.4791 (adds up to $0.25 for the interrupted call the app could not record) |
| Cost per passed video | $0.45 recorded / $0.50 counted (all spend divided by 5) |
| Cost of a video that passes on draft 1 | $0.13 |
| Production time | 3.5 to 6 minutes per video on this PC; 43 minutes of run time in total |
| Models | gpt-5.4 (writer), gpt-5.4-mini (topic ranking, fact check, story editor) |
| Published | nothing |

### Problems the pilot found, and what was changed

1. **The illustration library could not show the topics.** It had no wine, jar, box or taxi, so pictures fell back to generic tags and coins.
   - Ten objects were added in the approved style: wine glass, bottle, jam jar, flat-pack box, chair, taxi, mug, house, menu, gift.
   - The writer gets a note on what each object looks like.
2. **PubMed Central started answering with a CAPTCHA.** PMC articles are now fetched through NCBI's official E-utilities API (JATS XML). A CAPTCHA page is reported and the source is skipped; it is never bypassed.
3. **Wikipedia reference lists reached the writer as evidence.** This caused the misattributed "American Economic Review, 1991" card.
   - Reference entries are now removed from sources.
   - The fact checker reads the on-screen study card and flags misattribution.
4. **Long PDFs lost their results paragraph when condensed.** Paragraphs that report numbers (percentages, p-values, sample sizes) are now preferred.
5. **A replaced picture showed half a sentence ending in "…".** It now shows a whole short sentence or no label.
6. **A generic chart icon was used to picture a correlation.** The writer is told the chart icon is never a specific result.

### Known limitations

- **Feed discovery found no usable topic.** All 20 ScienceDaily items were correctly ranked off-niche or weakly sourced (supervolcanoes, crows, midlife strain). Every passing video came from the curated backlog. Unattended operation needs a better topic source: a research agent that proposes backlog entries, or more targeted feeds. Until then, a person adds 5 to 10 topics a week to `topics/money-minds.json`.
- **Automated checks cannot judge visual storytelling.** Two of the seven rendered videos passed every check and still had problems I found by looking at the contact sheets. Human approval stays mandatory.
- **Some sources are secondary.** The endowment and decoy videos rely on Wikipedia; the others use the primary paper.
- **Visual consistency is good, but object choices are sometimes literal or repetitive.** The decoy video is all phones, and the steps scenes use generic icons. There is no character acting.
- **Untested with real viewers.** Retention, revenue and audience response are unmeasured; no video has had a viewer.
- **Intelligibility of the narration is not checked automatically.** No speech recognition runs on the mix; loudness is checked.
- **Reported costs are computed, not reconciled.** They come from token counts and configured prices, not from an invoice.
