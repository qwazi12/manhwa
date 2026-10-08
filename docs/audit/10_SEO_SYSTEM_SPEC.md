# Flamingo Recap — Per-Chapter SEO System: Complete Build Spec
**Version: FINAL (consolidated) — handoff document for the coding agent**
**Channel: Flamingo Recap — https://youtube.com/@flamingoremix**
**Date: 2026-10-08** · Owner note (2026-10-08): "no need to [do] thumbnails" — §4.5 and the
thumbnail parts of §3/§9 are out of scope for this build.

(Saved from the owner's message; the paste arrived with UTF-8 shown as Windows-1252, e.g.
"â€”" for "—" — restored here so the frozen boilerplate is byte-exact.)

Sections: [1] Channel context & frozen boilerplate · [2] Verified research findings · [3] Pipeline
architecture (event-driven) · [4] Templates · [5] The five hook mechanics · [6] Rulebook ·
[7] Data schema · [8] Worked examples · [9] Integration contract · [10] Legacy content handling ·
[11] Rejected designs.

---

## 1. Channel context & frozen boilerplate

Channel: **Flamingo Recap** (`@flamingoremix`). Format: manhwa/manhua recap narrations. New model:
**per-chapter uploads** (one chapter = one video) going forward; existing compilation uploads
remain as archive (see §10).

### 1.1 Frozen boilerplate blocks (byte-identical every upload — NEVER regenerated)

**Ko-fi / CTA block:**
```
❤️ Support the channel — Ko-fi: https://ko-fi.com/flamingorecap
Your support keeps the grind alive and the recaps coming. Drop a comment with any recaps you'd love to see — we're always listening!
```

**Subscribe line:**
```
🔥 New recaps every week! Subscribe: https://youtube.com/@flamingoremix?si=iE3SIai9AooDNePA
```

**Music credit (one line, injected from render config — the only variable inside an otherwise frozen block):**
```
🎵 Background music: {{music_credit}}
Licensed under Creative Commons Attribution License (reuse allowed)
```

**Disclaimer block (verbatim, never modified):**
```
© Disclaimer:
The content on Flamingo Recap is created to share and promote stories and creators through recaps and narrations. All rights to the featured content belong to their respective owners, and no ownership is claimed over content not created by this channel. If you have any concerns regarding the use of your content, please contact us at kymedia.mgmt@gmail.com, and we will promptly address the matter.
```

**Fair-use block (verbatim, never modified):**
```
© Fair Use:
This channel may utilize copyrighted material that falls under the fair use provisions for purposes such as criticism, commentary, news reporting, teaching, scholarship, or research. Fair use is permitted by copyright law and does not infringe on the rights of the original copyright holders.
```

---

## 2. Verified research findings (evidence base — do not re-derive; cite when justifying design)

All numbers below were retrieved live from YouTube search results and scraped pages during the design sessions.

### 2.1 Competitor title patterns (verified)

| Pattern | Example | Verified performance |
|---|---|---|
| Hook title, setup→twist | "He Trained Alone For 2000 Years, But Now In The Future Everyone Is Very Weak!" (Frieren Manhwa) | 1,296,437 views |
| Hook title, underdog reversal | "Everyone Calls Him WEAK But He STEALS Their TALENTS And Grows 100X FASTER!" (Frieren Manhwa) | 1,056,629 views |
| Hook title, contrast pairing | "#1 UFC FIGHTER Reincarnates As A SLAVE Into The STRONGEST MAGE FAMILY!" (Frieren Manhwa) | 1,223,712 views |
| Hook title, concrete numbers | "He Played 10,000 Hours in the Game That Turned REAL and Got RANK #1 on DAY 1!" | 679,956 views |
| Hook title (on Murim Psychopath itself) | "He Was a Psycho Killer in Real Life—Then Reincarnated Into Murim \| Manhwa Recap" (Manhwa Chatter) | 159,171 views |
| Hook title | "He Killed Criminals in Real Life… Now Every Kill in Murim Makes Him Stronger" (ManhwaAddict) | 25,092 views |
| Hook title | "He Thought Murim Was Just a Game... Unfortunately, He's a Psychopath" (Empty Manhwa) | 13,705 views |
| Bare series name | "Murim Psychopath \| Manhwa Recap" (Nezuko Recap Comic) | 31,386 views |
| Range bracket `[1-N]` chain | "{1-45} Murim Psychopath - Manhwa Recap" (Manhwa population) | Views DECAY down the chain: [1-30] 26,658 → [1-33] 21,709 → [1-35] 21,966 → [1-40] 13,849 → [1-45] 11,934. Authority stacking is NOT automatic. |
| Per-chapter bare number | "(44) Murim Psychopath - Manhwa Recap" (Daily Manhwa Trend) | 3,760 views |
| Per-chapter WITH hook | "(42) Yeon Has Become Obsessed With Sosam!" (Daily Manhwa Trend) | 5,690 views — beats the bare number on the same series |

**Conclusions:**
- C1: Hook titles outperform bare/range titles. Setup→twist and contrast-pairing hooks have 1M+ verified precedent.
- C2: Even in per-chapter format, a story hook beats a bare chapter number (5,690 vs 3,760 on the same series).
- C3: The range-bracket chain format decays; do not adopt it for per-chapter uploads.
- C4: Nobody in the niche does "exact series name + clean per-chapter metadata + hook" — that is the open slot.

### 2.2 Competitor weakness signals (differentiation checklist — detect these in Phase 3 recon)
- Polluted boilerplate: Manhwa population's {1-45} description contains leftover template text from a different channel (Hindi-explainer template, "#ManhwaHindi").
- Title/description mismatch: same video's title says [1-45], description says "chapter = 0-34".
- Vague timecodes: "part 4 / part 5 / old part / new part" with no chapter labels.
- Missing chapter numbers anywhere in metadata.

### 2.3 Series-data conflicts found while cross-referencing Murim Psychopath (proof the reconciliation rules are needed)
- English title: "Murim Psychopath" (Asura) vs "Psychopath in Murim" (MangaUpdates canonical) — both valid, both are Associated Names.
- Korean title: 무림 사이코패스 (Fandom, loose) vs 싸이코패스 in 무림 (Naver official) — official wins.
- Author: "Kim Eon" vs "Kim Yeon" (romanization split of same name); **Gonbung = original novel author**.
- Artist: Song Beom-Gyu / Song Beomgyu (agree, romanization only).
- Novel chapter count: 234 (MangaUpdates) vs 282 (Naver revised edition) — unstable number, never cite in descriptions.
- MangaUpdates status field says "38+ chapters" while its own release log shows "c.46 by Asura 6 hours ago" — status fields go stale; release logs don't.
- Ch. 45 official English title: "Intensifying by the Minute" (WebNovel official translation).

### 2.4 YouTube hard rules (platform constraints the system must enforce)
- Hidden tags field: max **500 characters**.
- Title truncation: ~**70 characters** on most surfaces — design to ≤ 72 with the suffix never cut.
- Hashtags: **> 60 hashtags on a video = ALL ignored** (current live channel videos violate this — fix on next edit pass).
- First **3** hashtags display above the title.
- Description line 1–2 carries the highest search weight.
- Chapters/timecodes: must start at 0:00, ≥3 timestamps, each ≥10s.

---

## 3. Pipeline architecture (event-driven — answers the "parallel or after?" question definitively)

**Decision: staged generation — parallel where inputs exist, gated at human review.** The SEO module is a **listener** on the existing automation's events, not a separate post-render step. Rationale: timecodes don't exist until render completes; the competitor landscape for a chapter can change between ingest and publish (a too-early check is stale exactly where it matters); running everything post-render wastes the render wait and pushes publish past the freshness window.

### 3.1 Event map

| Event (from existing automation) | SEO stage triggered | What runs |
|---|---|---|
| `ingest_complete` (chapter downloaded/scraped) | **Stage 1 — parallel with scripting/render prep** | Load series cache; run chapter scrape for `chapter_title` + release date; generate `tags_chapter_block`; assemble description skeleton with prev/playlist links |
| `script_locked` (recap script approved content-locked) | **Stage 2 — parallel with render** | Extract `hook_beat` (dramatic beat, non-spoiler tease, 1–2 new character names) from the approved script; draft `description_tease`; draft `thumbnail_text` (≤4 words) |
| `render_complete` | **Stage 3 — pre-approval** | Fresh competitor check for this exact chapter + series on YouTube; validate `{{timecodes}}` (≥3, first 0:00, each ≥10s); inject `{{music_credit}}`; assemble final package |
| **Human approval gate** (user reviews) | — | User sees: rendered video · title (auto-filled from series `title_lock` — confirm chapter number only) · thumbnail preview · description line 1 + navigation links · tags/hashtags block (collapsed) |
| approval received (`status: approved`) | **Stage 4 — pre-queue** | Final validation pass (char counts, tag ≤500, hashtags = 3, exact phrase present in description line 1); write `status: queued` |
| publish (queued → YouTube) | — | Post-publish: playlist add in order; end screens prev+next; pinned comment; record `published_at` |

### 3.2 Series setup (runs ONCE per series — triggered on first chapter of a new series)

Phase A — **Seed scrape**: series page (e.g., Asura) → series title, synopsis, MC name. Expected limitation: Asura pages often don't expose genres/authors/alternates to scrapers — the seed is never treated as complete.

Phase B — **Parallel web enrichment**:
| Query target | Yields | Priority |
|---|---|---|
| Korean original title (e.g. 싸이코패스 in 무림) | Official title, novel author | Official (Naver) first |
| MangaUpdates | Alternate titles (EN/VN/CN), publisher, season structure, release log | Release log, never the static status field |
| Fandom wiki | Plot, artist, character names | Below databases |
| Scanlation sites | Genres, author/artist romanizations | Lowest, cross-check only |

Reconciliation rules: sources agree → use; conflict → store both with sources (never cite unstable numbers); stale field → newest timestamp wins; missing → flag, never guess. Everything cached per series with provenance URLs.

Phase C — **YouTube recon** (refreshed weekly per active series): search `"<series> recap"` → log every competitor video (title style, views, length, date, coverage). Deep-read top 2–3 for weakness signals (§2.2). Output: gap analysis + the differentiation checklist.

Phase D — **Hook selection (the one creative decision per series)**: generate 3 hook candidates using the five mechanics (§5), every clause sourced from the cached synopsis (provenance URLs required). User approves one. It becomes `title_lock.series_hook` — locked forever for that series.

Phase E — **Playlist creation**: create/confirm the series playlist, store `playlist_id` in cache.

### 3.3 Freshness timing (per chapter)
- Publish within **48–72h** of the chapter's English release (search volume for "chapter N" peaks and decays inside that window).
- `release_date_utc` is captured at Stage 1 from the release log; the scheduler targets the window automatically.

---

## 4. Templates

### 4.1 TITLE — hybrid, series-locked (the core fix)
**Decision record:** the system went through three iterations: (v1) keyword-first chapter title → rejected as flat; (v2) per-chapter hooks → rejected: every chapter restarts search authority from zero and creates weekly hook-generation risk; (v3, FINAL) **hook-first hybrid with a series-locked hook** — combines the verified 1M+-view hook mechanics (borrowed from what works in the niche) with series consistency (only the chapter number changes per upload).

**Locked skeleton:**
```
[{{chapter_number}}] {{series_hook}} — {{series_name_en}} | Manhwa Recap
```

**Why this construction (verified):**
- Hook first → front-loaded emotion for browse/suggested-feed clicks (the 1M+ pattern).
- Series anchor second → search matching + shelf consistency across the playlist.
- Bracketed chapter number → preserves the channel's compilation-era brand look.
- `| Manhwa Recap` suffix → constant channel keyword, ends every title.

**Constraints:**
- Total ≤ **72 chars**, so the suffix never truncates.
- Hook budget = `72 − (5 + 3 + len(series_name_en) + 15)`. For "Murim Psychopath" (16 chars) → **hook ≤ 33 chars**.
- If hook budget < 20 chars (long series name), fall back to `[{{N}}] {{series_hook}} | Manhwa Recap` and let the series name live in tags + description + thumbnail.
- The chapter number is the ONLY variable per upload (rule T6). If a chapter "needs a better hook," the fix goes in the thumbnail or description — never the title.
- Do NOT mix title variants within one series' playlist.

**The hook is a series-level asset** chosen once at series setup (Phase D), locked in `title_lock`, never regenerated per chapter.

### 4.2 DESCRIPTION — full template (boilerplate filled, slots marked)

```
{{series_name_en}} Chapter {{chapter_number}} recap (English) — "{{chapter_title}}"! {{description_tease}} Full chapter 45 recap on Flamingo Recap!
Watch Chapter {{prev_chapter_number}} here: {{prev_link}} • Start from Chapter 1: {{playlist_link}}

📖 Series: {{series_name_en}} ({{series_name_alt_joined}} / {{series_name_ko}})
✍️ Story: {{authors_story}} • 🎨 Art: {{authors_art}} • Original publisher: {{publisher}}
🔥 New recaps every week! Subscribe: https://youtube.com/@flamingoremix?si=iE3SIai9AooDNePA

{{timecodes}}

❤️ Support the channel — Ko-fi: https://ko-fi.com/flamingorecap
Your support keeps the grind alive and the recaps coming. Drop a comment with any recaps you'd love to see — we're always listening!

🎵 Background music: {{music_credit}}
Licensed under Creative Commons Attribution License (reuse allowed)

© Disclaimer:
The content on Flamingo Recap is created to share and promote stories and creators through recaps and narrations. All rights to the featured content belong to their respective owners, and no ownership is claimed over content not created by this channel. If you have any concerns regarding the use of your content, please contact us at kymedia.mgmt@gmail.com, and we will promptly address the matter.

© Fair Use:
This channel may utilize copyrighted material that falls under the fair use provisions for purposes such as criticism, commentary, news reporting, teaching, scholarship, or research. Fair use is permitted by copyright law and does not infringe on the rights of the original copyright holders.

#ManhwaRecap #{{series_hashtag}} #MangaRecap
```

**Line 1 must contain the verbatim exact-match phrase** `{{series_name_en}} Chapter {{chapter_number}}` plus `(English)` — this is where the release-day search query lives (the title's job is emotion; the description's job is literal matching; see the division-of-labor rationale in §8).

**`{{timecodes}}`** — only if the rendered video has ≥3 distinct sections; first at 0:00; each ≥10s; label by story beat or chapter range, NEVER "Part 1/Part 2". Example shape:
```
⏱️ Timestamps:
• 0:00 - Recap start
• 4:12 - {{story beat from script}}
• 8:40 - {{story beat from script}}
```

### 4.3 HIDDEN TAGS — three blocks, ≤ 500 chars total
Structure (in order): chapter block → series block → evergreen block. On overflow, trim from the evergreen tail first; the chapter block is never trimmed.

**Chapter block (generated fresh per chapter):**
```
murim psychopath chapter 45, murim psychopath ch 45, murim psychopath 45, murim psychopath chapter 45 english, murim psychopath new chapter, murim psychopath latest chapter
```
(pattern: `{series} chapter {N}`, `{series} ch {N}`, `{series} {N}`, `{series} chapter {N} english`, `{series} new chapter`, `{series} latest chapter`)

**Series block (frozen per series, from cache):**
```
murim psychopath manhwa, psychopath in murim, murim sociopath, welcome to murim online, 싸이코패스 in 무림, dong bongsu, gonbung, kim eon, song beom-gyu, madman's paradise, murim manhwa, murim manhwa recap, martial arts manhwa, action manhwa, isekai manhwa, reincarnation manhwa, psychological manhwa
```
(must include: series name + ALL alternate titles + Korean title + MC name + creators + season names + genres expanded to tag phrases)

**Evergreen block (frozen channel-wide):**
```
manhwa recap, manga recap, manhwa chapter recap, manhwa explained, webtoon recap, korean webtoon, manhwa summary, manhwa compilation, full manhwa recap, flamingo recap
```

### 4.4 HASHTAGS — exactly 3, never more
```
#ManhwaRecap #{{series_hashtag}} #MangaRecap
```
`#ManhwaRecap` always first (displays above title). `{{series_hashtag}}` = CamelCase series name (e.g. `#MurimPsychopath`). Exactly 3 → all display. Hard rule: never exceed 60 hashtags anywhere (current live videos violate this and have ALL hashtag features disabled — fix on next edit pass of old videos).

### 4.5 THUMBNAIL — the per-chapter creative slot  *(OUT OF SCOPE — owner, 2026-10-08)*
- `CH. {{chapter_number}}` large, mobile-readable.
- MC's face reacting (dramatic frame from the chapter).
- ≤ 4 words of text from the chapter's dramatic beat (`{{thumbnail_text}}`, drafted at Stage 2 from the approved script).
- **Identical visual template every chapter** — the playlist and channel feed must look unified; only the number and the beat text change.

---

## 5. The five hook mechanics (for series-hook generation at Phase D)

Generate 3 candidates, each labeled with its mechanic, every clause sourced from the cached synopsis:

| # | Mechanic | Verified precedent (views) |
|---|---|---|
| 1 | **Setup → Twist** — "He [X], But [Y]!" | 1,296,437 ("He Trained Alone For 2000 Years, But…") |
| 2 | **Underdog reversal** — "Everyone [dismisses him] But He [dominates]" | 1,056,629 |
| 3 | **Contrast pairing** — high-status → low / real → fantasy | 1,223,712; 159,171 on Murim Psychopath itself |
| 4 | **Concrete numbers** — "10,000 Hours", "2000 Years", "DAY 1" | 679,956 |
| 5 | **Identity/misconception reveal** — "He Thought [X]… Unfortunately, [Y]" | 13,705 (premise-matched to Murim Psychopath) |

**Generation rules:**
- Rank by mechanic strength (1–3 have 1M+ precedent) AND premise fit (mechanic 5 is ideal when the series premise is a misconception).
- Every clause must trace to a source URL (synopsis, official premise, chapter titles). No invented plot events.
- **Blacklist** (from user feedback): vague filler verb constructions — "shocks/stuns + [location]" (e.g., the rejected "His Class Change Shocks Murim").
- Hook length: must fit the budget in §4.1.

---

## 6. Rulebook (enforce in code; violations = validation failures)

### Research rules
- **R1.** Seed page is a seed, not a source of truth. Always enrich via web search.
- **R2.** Cross-reference minimum: 1 official/database source + 1 wiki/scanlation source + 1 YouTube recon pass, run in parallel.
- **R3.** Trust hierarchy: official publisher > database release logs > databases' static fields > wikis > social posts.
- **R4.** Conflicts are stored, not silently resolved. Newest timestamp wins. Missing fields are flagged, never guessed.
- **R5.** Series data is researched once and cached; only chapter data is re-researched per upload.

### Title rules
- **T1.** Skeleton locked per series: `[{{N}}] {{series_hook}} — {{series_name_en}} | Manhwa Recap` (fallback short form when hook budget < 20 chars). Exactly three constructions exist in the system (long, short-fallback, legacy compilation); a fourth is a bug.
- **T2.** Hard cap 72 chars; target ≤ 70. Trim priority: never the chapter number → series name → hook → suffix.
- **T3.** The title variant is chosen once per series and locked. Never mix variants within one series' playlist.
- **T4.** The hook is chosen once per series from series-level sources (ranked: official synopsis/premise > chapter titles as supporting material). Never from a single chapter's content. Provenance URL required.
- **T5.** Competitor/YouTube checks run at series setup and weekly per active series — not per chapter. Per-chapter urgency is purely the 48–72h publish window (P1).
- **T6.** The chapter number is the only variable in titles. A chapter needing a "better hook" is a thumbnail/description problem, not a title change.

### Description rules
- **D1.** Line 1 = exact phrase `{{series_name_en}} Chapter {{chapter_number}}` + `(English)` + `{{chapter_title}}` + non-spoiler tease + channel name.
- **D2.** Line 2 = previous chapter link + playlist link (navigation is per-chapter gold — sequential viewers).
- **D3.** Boilerplate blocks (§1.1) are frozen strings, byte-identical every upload.
- **D4.** `{{timecodes}}` only if ≥3 sections, first at 0:00, each ≥10s; label by story beat/chapter range, never "part 1/2".
- **D5.** Never cite unstable numbers (novel chapter counts, total chapter counts) — they conflict between sources and go stale (see §2.3).

### Tags & hashtag rules
- **H1.** Hidden tags ≤ 500 chars; structure = chapter block + series block + evergreen block; overflow trims evergreen tail first.
- **H2.** All alternate titles + Korean title always included as tags.
- **H3.** Exactly 3 hashtags, `#ManhwaRecap` first.
- **H4.** Never > 60 hashtags anywhere (channel's current live videos violate this — remediate on next edit pass).

### Per-chapter strategy rules
- **P1.** Publish within 48–72h of the chapter's English release.
- **P2.** Every video → the series playlist, in order. No exceptions.
- **P3.** End screens: previous + next chapter (next when it exists). Pinned comment: `Start from Chapter 1: {{playlist_link}} | Chapter {{prev}}: {{prev_link}}`.
- **P4.** Thumbnail template identical across chapters; only number + beat text change.
- **P5.** Track views @48h and @7d per chapter per series. Drop a series after two consecutive underperforming chapters. The title lock is the one thing that may be revisited, and only on data (A/B on the next chapter, compare @48h).

### Compliance rules
- **C1.** © Disclaimer and Fair Use blocks in every description, verbatim (§1.1).
- **C2.** Music credit = one line from render config.
- **C3.** Hooks/teases compress sourced text only; provenance URL recorded per claim.

---

## 7. Data schema

```json
// SERIES CACHE — one per series, written once at series setup (Phase A–E), read forever
{
  "series_id": "murim-psychopath",
  "series_name_en": "Murim Psychopath",
  "series_name_alt": ["Psychopath in Murim", "Murim Sociopath", "Welcome to Murim Online"],
  "series_name_ko": "싸이코패스 in 무림",
  "series_hashtag": "MurimPsychopath",
  "authors": { "story": ["Gonbung", "Kim Eon"], "art": ["Song Beom-Gyu"] },
  "publisher": "Naver Webtoon / Naver Series",
  "genres": ["Action", "Martial Arts", "Fantasy", "Psychological", "Murim", "Reincarnation"],
  "characters": ["Dong Bongsu"],
  "seasons": [
    { "n": 1, "name": "Madman's Paradise", "chapters": "1-34" },
    { "n": 2, "chapters": "35-ongoing" }
  ],
  "title_lock": {
    "skeleton": "[{{chapter_number}}] {{series_hook}} — {{series_name_en}} | Manhwa Recap",
    "series_hook": "He Thought Murim Was Just a Game",   // chosen once, locked forever
    "hook_mechanic": 5,
    "hook_provenance": ["https://asurascans.com/comics/murim-psychopath-bd5bdaf8/", "..."],
    "decided_at": "series-setup",
    "approved_by_user": true
  },
  "playlist_id": "<created at Phase E>",
  "tags_series_block": "<frozen string, §4.3>",
  "evergreen_hook_full": "He Logged Into a VR Game — But Woke Up in the REAL Murim", // for descriptions
  "recon": { "last_checked": "<iso-date>", "competitors": [ /* title, channel, views, date, coverage */ ] },
  "provenance": { "<field>": "<source_url>" },
  "status": "active | dropped-per-P5"
}

// CHAPTER RECORD — one per upload
{
  "series_id": "murim-psychopath",
  "chapter_number": 45,
  "chapter_title": "Intensifying by the Minute",
  "chapter_title_provenance": "<url of release page / official translation>",
  "release_date_utc": "<iso-datetime>",
  "publish_window": { "opens": "<release+0h>", "closes": "<release+72h>" },
  "hook_beat": { "dramatic_beat": "...", "tease": "...", "new_entities": ["..."], "source": "approved script" },
  "thumbnail_text": "≤4 words from the beat",
  "description_tease": "...",
  "prev_link": "...", "playlist_link": "...",
  "timecodes": [ { "t": "0:00", "label": "Recap start" } ],
  "tags_chapter_block": "<generated per §4.3 pattern>",
  "final_title": "[45] He Thought Murim Was Just a Game — Murim Psychopath | Manhwa Recap",
  "final_description": "<assembled §4.2>",
  "final_tags": "<chapter + series + evergreen, ≤500 chars>",
  "music_credit": "<from render config>",
  "competitor_check": { "checked_at": "<iso-datetime>", "exact_phrase_taken": false },
  "status": "draft → awaiting_approval → approved → queued → published",
  "performance": { "views_48h": null, "views_7d": null }
}
```

---

## 8. Worked example — Murim Psychopath, Chapter 45 (fully filled)

**Title** (71 chars — inherits the locked skeleton, only "45" is per-chapter):
```
[45] He Thought Murim Was Just a Game — Murim Psychopath | Manhwa Recap
```

**Description, first two lines:**
```
Murim Psychopath Chapter 45 recap (English) — "Intensifying by the Minute"! His VR logout failed and dropped him into the real Murim — and now every kill makes him stronger. Full chapter 45 recap on Flamingo Recap!
Watch Chapter 44 here: {{prev_link}} • Start from Chapter 1: {{playlist_link}}
```
(then §4.2 boilerplate: series/credits line, subscribe, timecodes, Ko-fi, music, © blocks, 3 hashtags)

**Hashtags:** `#ManhwaRecap #MurimPsychopath #MangaRecap`

**Tags:** chapter block (`murim psychopath chapter 45, murim psychopath ch 45, murim psychopath 45, murim psychopath chapter 45 english, murim psychopath new chapter, murim psychopath latest chapter`) + series block + evergreen block per §4.3.

**Thumbnail:** `CH. 45` + MC reaction frame + ≤4-word beat from the ch. 45 script.

**Division-of-labor rationale (embed this in the codebase docs):** the title has 72 characters and its job is **emotion** (browse/suggested-feed clicks); the description and tags have ~5,000 characters and their job is **literal matching** (`"Murim Psychopath Chapter 45"` verbatim + variants capture release-day searchers). The old keyword-first title tried to do both in 72 characters and did neither well.

**Alternative hooks generated at setup (for reference / user re-pick):**
| Candidate | Mechanic | Provenance |
|---|---|---|
| He Thought Murim Was Just a Game ⭐ (locked) | 5 — misconception reveal | Asura synopsis ("When he tried returning to virtual reality…") |
| Psycho Killer Reborn in Murim | 3 — contrast pairing | Series premise (Manhwa Chatter's 159K-view construction, compressed) |
| Every Kill Makes Him Stronger | 1 — setup→twist | Series premise |

---

## 9. Integration contract (listener interface pseudocode)

```
class SeoModule:
    # Stage 1 — trigger: ingest_complete(series_id, chapter_number, release_date)
    on_ingest_complete:
        cache = series_cache.get_or_init(series_id)      # runs Phases A–E if new series
        scrape chapter release page -> chapter_title, provenance
        generate tags_chapter_block (§4.3 pattern)
        assemble description skeleton (prev_link, playlist_link)
        status = draft

    # Stage 2 — trigger: script_locked(series_id, chapter_number, script_text)
    on_script_locked:
        extract dramatic_beat, non-spoiler tease, new character names
        draft thumbnail_text (<= 4 words), description_tease
        status = draft (updated)

    # Stage 3 — trigger: render_complete(series_id, chapter_number, video_url, timecodes, music_credit)
    on_render_complete:
        competitor_check = youtube_search(f"{series_name_en} Chapter {N}")   # fresh, pre-approval
        validate timecodes (>=3, first 0:00, each >=10s) else timecodes = []
        assemble final_title from title_lock skeleton (confirm chapter number only)
        assemble final_description (§4.2), final_tags (<= 500 chars, H1 overflow rules)
        run validation suite (T2, D1, D4, D5, H1, H3, H4) — failures block approval
        status = awaiting_approval

    # Stage 4 — trigger: approval(user_choice) from the review screen
    on_approved:
        final validation pass; status = queued
        on publish: add to playlist in order, end screens prev+next, pinned comment,
        record published_at, schedule 48h/7d performance capture (P5)
```

**Review screen contents (one screen, approval gate):** rendered video · title (confirm number only — hook is pre-locked) · thumbnail preview · description line 1 + navigation links · tags/hashtags (collapsed, expandable).

---

## 10. Legacy content handling
- Existing bundled uploads (e.g., the 7-hour 12-part videos) keep the compilation bracket style `[1-12] Hook | Manhwa Recap` — do NOT retrofit to per-chapter format.
- Remediation pass on old videos (no re-upload): cut hashtags to ≤ 10 (they currently have 100+ — all hashtag features disabled), move keyword dump to the hidden tags field (≤ 500 chars), rewrite description line 1 with the story hook, relabel timecodes from "Part N" to `Ch. N: [chapter title]` where chapters are known.
- The two formats coexist: compilations = archive/discovery; per-chapter series-locked = everything new.

---

## 11. Explicitly rejected designs (do not reintroduce)
1. **Keyword-first chapter title** ("Murim Psychopath Chapter 45 — His Class Change Shocks Murim | Manhwa Recap") — flat, weak CTR, and "shocks/stuns + location" is blacklisted.
2. **Per-chapter hooks** — every chapter restarts search authority from zero; weekly hook-generation risk inside the 48h window; playlist looks chaotic.
3. **Range-bracket re-release chains** for new content — verified view decay (§2.1).
4. **Hashtag walls (>60)** — disables all hashtag features (current live videos).
5. **Chapter-count claims in descriptions** — unstable across sources (234 vs 282 novel chapters, §2.3).
6. **All-SEO-after-render** — wastes the render wait, misses the freshness window, stale competitor checks.
7. **All-SEO-parallel-from-ingest** — timecodes don't exist pre-render; competitor checks would be stale at publish.
