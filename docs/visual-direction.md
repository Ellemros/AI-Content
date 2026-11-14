# Visual direction

Written 2026-11-14. Status: **prototype for review**. Nothing in this direction is approved until you have watched the prototype.

## Why the old videos failed

I looked at `48df1f0d…mp4` (the Phase 1 card renderer) and `dbf23f29…mp4` (the Phase 2 stickman engine). Their problems come from how they were built, not from small bugs:

1. **Raster drawing with no layout engine.** Both renderers draw with Pillow primitives. Nothing measures text, so a headline can be larger than the frame and overlap shapes (card renderer). Lines are aliased, with no type hierarchy.
2. **Randomness instead of direction.** The stickman engine forced every neighbouring scene to change layout, setting and camera. That produces variety, not storytelling: a lab background for a food-logistics sentence, a forest for warehouses.
3. **Pictures decorate words instead of showing claims.** Props were picked from a generic list, so a heart stood for "waste". No scene type existed for the shapes evidence actually takes: a number, a comparison, a study and its limits.
4. **Characters without acting.** Stick figures with fixed proportions and no expressions or interactions read as clip art.
5. **Captions not designed with the picture.** On the dark style, dark-ink captions became unreadable on dark blue, because caption colour was not tied to the background.
6. **The quality gate measured encodes, not videos.** "ready" meant FFmpeg succeeded and frames differed, not that anyone looked.
7. **Scripts from a small model.** gpt-4o-mini produced short, generic narration, and contradicted its own source in the card-vs-cash test.

## Three directions

### A. Editorial motion graphics (recommended, built)

- **What viewers see:** a warm paper background with a dot grid, navy ink, money green for gains, coral for costs, and gold for reward. Bold editorial type (Bricolage Grotesque) and flat vector objects in one consistent outline style: cards, cash, terminals, carts, brains, gauges, receipts, people.
- **How it explains:** each scene is one of 11 *semantic* templates that match the shape of the claim:
  - hook and question;
  - study card with a caveat;
  - stat count-up;
  - bar comparison from source numbers;
  - versus with a winner reveal;
  - steps;
  - brain region (lit, or neutral when nothing differed);
  - gauge;
  - object;
  - takeaway with a ✓ or a ? matching certainty.
- **Motion:**
  - springs for entrances, and in-scene actions keyed to the spoken word (a winner card highlights on its word, numbers count up, a cart fills);
  - a slow camera push on every scene, so nothing freezes;
  - three transition types in a fixed rhythm.
- **Typography:** the font size is computed so text always fits its zone. Captions sit in a fixed safe band as a white pill, at most 3 words, never crossing a sentence end, with the spoken word highlighted.
- **Identity:** one palette, one illustration style, one type system, and a brand mark with a progress bar. Variety comes from the content of each scene, not from random backgrounds.
- **Automation:** the script model writes the storyboard (template + fields) together with the narration. Python validates every field: numbers on screen must appear in a source sentence about the same thing. Remotion renders it headlessly.
- **Cost:**
  - Render: $0. Remotion is free for an individual or a company of up to 3 people ([license](https://github.com/remotion-dev/remotion/blob/main/LICENSE.md)).
  - Script: about $0.09–$0.45, depending on rewrites (GPT-5.4 writer plus GPT-5.4 mini fact-check; prices from third-party listings).
  - Voice: $0 (Kokoro, local).
- **Render time:** about 60–90 s for a 45 s short on this PC (CPU).
- **Limits:**
  - No character acting yet.
  - Illustrations come from a fixed library of 17 objects.
  - Scenes are diagrammatic, so stories about people (an argument at a checkout) are weaker than in a character style.
  - Retention against top creators is unproven until real data exists.

### B. AI-illustrated storytelling

- **What viewers see:** a consistent illustrated world (e.g. warm gouache characters in shops and kitchens) generated per scene, animated with parallax and camera moves, plus motion-graphic overlays for numbers and studies.
- **Strengths:** cinematic, with people and places, so it is stronger for story-driven topics.
- **Automation:** prompt per scene from the storyboard with a fixed style prompt; image review is needed because models drift.
- **Cost:** 8–12 images per short at about $0.02–$0.25 each, depending on quality tier ([OpenAI model pages](https://developers.openai.com/api/docs/models/gpt-image-1)): roughly $0.20–$3 per short, plus re-rolls.
- **Limits:**
  - Character consistency between scenes is unreliable.
  - Text inside images is unreliable.
  - Every frame needs a check for wrong hands, fake text and off-brand style.
  - Local generation is not practical on a 4 GB GPU.
  - Image-to-video models (Wan, LTX-Video) need roughly 8–24 GB of VRAM, or paid APIs at about $0.10–$1 per second of footage.

### C. Character animation (rigged 2D cast)

- **What viewers see:** a small recurring cast (the spender, the saver, the narrator), built as SVG rigs with expressions, walking and gesturing in simple sets, with motion-graphic overlays.
- **Strengths:** the strongest identity and acting; people remember characters, which suits a series.
- **Automation:** possible in the same Remotion stack (rig components with pose and expression keyframes chosen by the storyboard), but the rig and action library is weeks of design work. A weak rig looks worse than no character, as the stickman showed.
- **Cost:** $0 per video after the rigs are built; the build cost is design time.
- **Limits:** the acting vocabulary limits what stories can be told. It needs a person with illustration taste to design the cast.

### Comparison

| | A. Motion graphics | B. AI illustration | C. Rigged characters |
|---|---|---|---|
| Looks professional today | Yes | Varies per image | Only after a big build |
| Shows the actual claim | Strong (semantic templates, sourced numbers) | Medium (images can mislead) | Medium |
| Consistency | Strong | Weak to medium | Strong |
| Automation reliability | High (deterministic) | Medium (needs image review) | High once built |
| Cost per short | $0.10–$0.45 (script only) | $0.40–$3.50 | $0.10–$0.45 after the build |
| Hardware | CPU | API (local not viable on 4 GB) | CPU |
| Licensing | Remotion free ≤3 people; OFL fonts; own illustrations | OpenAI terms allow commercial use of outputs | Own art |

**Recommendation: A now, then add C's cast inside A.** A is the only direction that already produces clean, consistent, honest videos unattended. Its weakness, no people, is best fixed by a small recurring cast inside the same Remotion system (direction C), not by switching to AI images, whose drift would break the brand. B can supply occasional hero images for long-form, reviewed by a person.

## What the prototype is and is not

- **It is:** a real run.
  - The topic is a viewer question.
  - Two sources were fetched politely (the paper's open-access page and Wikipedia's REST API).
  - GPT-5.4 wrote the storyboard, and GPT-5.4 mini fact-checked it and forced two rewrites.
  - Kokoro narrated, Remotion rendered, FFmpeg mixed, and the visual QA inspected the MP4.
- **It is not:**
  - **Approved:** the status is `needs_review` by design until you watch it.
  - **Retention-tested:** no viewer data exists.
  - **Better than a skilled human editor:** it isn't. Its hook writing and pacing are competent but not exceptional.
