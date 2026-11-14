# First-channel strategy

Written 2026-11-14. Every dollar figure below is a **third-party estimate**, not a measurement and not a promise. YouTube does not publish RPM by niche. The system replaces these numbers with your own measured revenue once videos are published (`profit-report`).

## What the platforms actually pay for

- **YouTube Partner Program.** Full monetization needs 1,000 subscribers and either 4,000 public watch hours in 12 months or 10 million Shorts views in 90 days. Several outlets report the bar doubling on 1 February 2027 (8,000 hours or 20 million Shorts views). I could not confirm that on YouTube's own pages; treat it as possible. ([AIR Media-Tech](https://air.io/en/monetization/youtube-partner-program-requirements-2026-the-complete-guide), [vidIQ](https://vidiq.com/blog/post/youtube-partner-program-changes-2027/), [YouTube Blog](https://blog.youtube/news-and-events/youtube-monetization-qualified-watch-hours-shorts-views/))
- **Shorts pay a fraction of long-form.** AIR's 2026 data from 274 channels puts Shorts RPM at 3–14% of the same niche's long-form RPM. Third-party Shorts ranges cluster around $0.04–$0.40 per 1,000 views, with finance at the top. ([AIR Media-Tech](https://air.io/en/air-data-findings/youtube-monetization-statistics-2026), [OutlierKit](https://outlierkit.com/resources/youtube-rpm-benchmarks/))
- **Long-form by niche.** The most transparent dataset (AIR, 300 channels, 2026) puts Education & Science at a $10.22 median RPM against about $2.30 across all niches. Personal-finance long-form CPM estimates range from $15 to $45 depending on the source. ([AIR Media-Tech](https://air.io/en/air-data-findings/youtube-monetization-statistics-2026), [vidIQ](https://www.vidiq.com/blog/post/most-profitable-youtube-niches))
- **TikTok Creator Rewards** pays only for original videos longer than one minute, after 10,000 followers and 100,000 views in 30 days. Creator-reported US RPM is about $0.40–$2.00 per 1,000 qualified views. ([ttcalculator](https://ttcalculator.net/guides/creator-rewards-program/), [Influencer Marketing Hub](https://influencermarketinghub.com/?p=143981))
- **YouTube's inauthentic-content policy** (renamed from "repetitious content" in July 2025) is the main risk for this business. AI narration and faceless channels are allowed. What gets demonetized is a channel of near-identical, templated, mass-produced videos, judged at the channel level. ([vidIQ](https://vidiq.com/blog/post/youtube-ai-monetization/), [OutlierKit](https://outlierkit.com/resources/faceless-youtube-channel-demonetized/))

Consequences for this system:
1. **Revenue comes from long-form.** Shorts are the cheap way to find an audience and test topics. Long-form is where the money is.
2. **Variety and original analysis are a monetization requirement**, not decoration. That is why the scene engine varies layout, setting, camera, story structure and transitions, and why the editorial gate stays strict.
3. **Volume is not the goal.** One reviewed video a day beats ten similar ones, both for the policy and for learning what works.

## Niche comparison

Ratings are my judgement from the sources above and the production constraints of this system (stickman animation, local voice, research-based scripts). They are estimates.

| Niche | Ad demand (est.) | Audience | Competition | Fit for stickman + research | Advice / policy risk | Affiliate potential | Verdict |
|---|---|---|---|---|---|---|---|
| Personal finance | Highest | Large | Saturated | Medium | **High**: advice is regulated and YMYL | High | Too risky for unattended AI scripts |
| Psychology & behaviour | Medium | Very large | High | **High** | Medium (mental health drifts into medical) | Low–medium | Good reach, weaker revenue |
| Technology & AI | High | Large | Very high, news-driven | Medium | Low | High | Fast decay; hard to stand out |
| Science explainers | High (best measured median) | Large | High | High | Medium (health topics dominate feeds) | Low | Good, but the feeds are mostly health |
| Business & entrepreneurship | High | Medium | High | Medium | Medium | High | Good, but sources are opinion-heavy |
| History & stories | Low–medium | Large | Medium | Medium | Low | Low | Fun, low RPM |
| Productivity & self-improvement | Medium | Large | Saturated with generic content | Medium | Low | Medium | Generic-content risk |

## Recommendation: "Money Minds", the psychology of money

**What it is.** Research-based explainers on why people spend, save, tip, price, procrastinate and decide the way they do: behavioural economics and consumer psychology. It is education, never advice. No "buy this", no investment tips, no health claims.

**Why this beats the alternatives as a business:**
- **It borrows finance-level ad demand without finance-level risk.** Viewers who watch money-psychology content are the audience finance and fintech advertisers pay for, but the scripts explain research rather than recommend products. The existing financial-advice and medical holds stay on.
- **The sources are free, reliable and automatable.** University press releases via ScienceDaily's Economics and Consumer Behavior feeds come with a source page to check every claim against. Health stories are held by the stricter guardrail.
- **The stickman suits it.** Decisions, choices, comparisons and before/after are exactly what the duo, split, chart and metaphor layouts show well.
- **It is evergreen.** "Why we tip more when a screen asks" is still relevant in three years; AI news is not.
- **It has a clear revenue path:**
  1. Shorts for reach and topic testing.
  2. Long-form 6–10 minute explainers for YouTube revenue (Education/finance-adjacent RPM).
  3. Later, relevant book and budgeting-tool affiliate links, and sponsorships from finance-education brands. These are not automated until the audience exists.

**Why not the existing `everyday-decisions` brand?** It is close in spirit, but its only feed is health-dominated science news. Most of its opportunities were held or medical-adjacent, and its niche ("misread ordinary situations") has weaker advertiser demand. It is left untouched in the database.

## Operating plan

| Phase | When | What | Decision gate |
|---|---|---|---|
| 1. Review | First 2–3 weeks | 1 short a day, `approval` mode. You approve each video, and publishing runs with YouTube as private first. | You are happy with 10 consecutive videos. |
| 2. Reach | Weeks 3–8 | 1 short a day on YouTube Shorts, TikTok, Instagram and Facebook. Fetch analytics daily. | After 30 shorts: compare average view percentage by story structure; the chooser starts favouring the best. |
| 3. Revenue | After the long-form pipeline exists | 1 long-form explainer a week, built from the best-performing short topics. | YPP eligibility; measured RPM replaces these estimates. |
| 4. Second channel | After phase 3 has 2 months of data | Only if Money Minds is profitable or clearly trending there. | Measured profit per video, not views. |

Stop or pivot signals (measured, not guessed): average view percentage on Shorts staying under about 50% after 30 videos, or no subscriber growth after 60 days. In that case, change the structure mix first, then the niche.

## What the system does for this today

- **Discovery:** the brand cycle reads the three feeds, holds medical and financial-advice titles, and drops duplicate stories.
- **Format choice:** `app/services/strategy.py` rotates six story structures (explainer, myth vs fact, mystery, experiment, versus, three points). Once there is measured data, it shifts toward the structures with the best revenue per 1,000 views, or the best retention where no revenue is reported, while still exploring.
- **Quality:**
  - Each script is checked against its source.
  - Hedges, study models such as "in mice", and numbers must survive.
  - An editorial review with one revision round decides whether it renders.
- **Learning:** `fetch-analytics` stores daily YouTube metrics per post. `profit-report` shows views, reported revenue, production cost and profit by video, platform and structure.
