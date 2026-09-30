# Narration Pipeline: Comic/Webtoon Pages to Performed Story

System spec for an automated narration system. It turns comic, manga, webtoon or picture-book pages into a performance-ready narration script.

This file defines **what the system does and in what order**. The companion file, `storytelling-guide.md`, defines **how the narration is written**. The writing and QA stages must load that guide.

---

## 0. Design principles

1. **Page text is sacred.** Every speech bubble, thought bubble and caption reaches the script verbatim, or doesn't reach it at all. Omitting a line is allowed. Rewording one is not.
2. **Uncertainty stays visible.** No stage may quietly upgrade an uncertain reading to confirmed. Flags carry through to delivery.
3. **The storyboard is the single source of truth.** The narration is written from structured, verified data, never from a model's memory of the images.
4. **Every line has an ID.** Transcription, verification, storyboard, script and QA all point at the same line.
5. **Everything is resumable.** A failure on page 14 doesn't restart the run from page 1.

---

## 1. Capabilities and providers

| Capability | What it does | Implementation |
|---|---|---|
| Retrieval | Get the image files in page order | HTTP or file access (`requests`, `curl`, a local folder). No AI involved. |
| Vision reading | Read the text and understand the art | Anthropic (Claude), OpenAI, or Google (Gemini) vision-capable models, each through its own API and your own key |
| Writing | Draft and edit the narration | Any of the three providers' models, loaded with `storytelling-guide.md` |
| Delivery | Output the script | A file on disk, or your pipeline's API (Airtable, Google Drive, Notion) |

**Provider roles** (set in config):
- **Primary reader:** transcribes every page (stage 2).
- **Verifier:** a *different* provider that re-checks flagged lines (stage 6). Using a second provider catches misreads that repeating the same model would miss.
- **Tiebreaker:** the third provider, used only when the first two disagree.
- **Writer:** any provider. It never sees raw text from the images, only the storyboard.

**Plain OCR (Tesseract and similar) is not a reader.** It can't identify speakers, bubble types or art, and it struggles with hand-lettered fonts. At most, use it as a cheap cross-check on clean caption text.

Keep model names in config, not code. Models get updated, and every reading records which one produced it.

---

## 2. Data model

### Line record

One record per bubble, caption or sound effect.

```json
{
  "id": "p07-c2-b3",
  "page": 7,
  "crop": 2,
  "order": 3,
  "text": "exact text as it appears on the page",
  "type": "speech | shout | whisper | thought | caption | sfx",
  "sfx_language": "en | ko | ja | zh | other | null",
  "speaker": "character label from the character bible, or null",
  "speaker_basis": "tail | position | context | guess",
  "art_notes": "what the image shows at this moment",
  "beat": "setup | rising | climax | resolution",
  "readings": [
    {"provider": "anthropic", "model": "<from config>", "role": "primary", "text": "..."},
    {"provider": "openai", "model": "<from config>", "role": "verifier", "text": "..."}
  ],
  "status": "clear",
  "flags": ["reordered"],
  "notes": "free text"
}
```

- **ID format:** `p{page}-c{crop}-b{order}`. Crops exist because tall pages get cut into sections (stage 1).
- **Order** is the verified reading order within the page, not the order the model listed the lines.

### Status values

| Status | Meaning | Allowed in final script? |
|---|---|---|
| `clear` | Primary and verifier agree, or the line wasn't flagged and has no warning signs | Yes |
| `human_checked` | A person confirmed it against the page | Yes |
| `majority` | Two of three providers agree | Yes, but listed in the QA report. Two models can share a misread. |
| `judgment_call` | Text is clear, but an interpretation (speaker, tone) was inferred | Yes, listed in the QA report |
| `disputed` | Readings differ and no tiebreak has run yet | No |
| `unresolved` | Still disputed after the tiebreak | No. Omit the line, or hold the run for human review. |

**Flags** (can combine with any status): `reordered`, `proper_noun`, `stylized_lettering`, `low_resolution`, `speaker_guess`.

### Character bible record

```json
{
  "label": "the watcher",
  "name": null,
  "name_first_seen": null,
  "appearance": "long black hair, orange robe, bare legs, boots",
  "first_seen": "p01-c3",
  "voice": {"pitch": "mid", "pace": "lazy", "rhythm": "drawn out", "texture": "bright", "attitude": "playful menace"},
  "pronunciation": null,
  "aliases": []
}
```

When a character's name is revealed later, record it and set `name_first_seen`. The narration may bridge the label to the name once, at that point.

### Suggested run layout

```
runs/<title>-<chapter>/
  config.json            providers, models, reading format, audience settings
  pages/                 original images, numbered
  crops/                 processed crops sent to the APIs
  transcript.jsonl       raw primary readings (stage 2)
  verification.jsonl     verifier and tiebreaker readings (stage 6)
  characters.json        character bible (stage 5)
  storyboard.json        merged, verified line records (stage 8)
  script.draft.md        narration draft (stage 9)
  script.final.md        after edit and QA (stages 10-11)
  qa_report.md           QA findings and all non-clear lines
  run.log                per-page, per-stage log for resuming
```

---

## 3. Operational basics

- **Rate limits and retries.** All three providers throttle requests. Use exponential backoff on rate-limit errors (wait, double the wait, retry, cap the attempts), and queue requests instead of firing them all at once.
- **Log per page and per stage.** Record page number, stage, provider, model, status and error. A partial run resumes from the last completed unit.
- **Batching.**
  - **Transcription (stage 2):** 3 to 5 crops per request. The model can track characters and context across them.
  - **Verification (stage 6):** always one cropped bubble per request, never batched. Batching hides exactly the misreads this stage exists to catch.
  - Providers cap images per request and total request size. Read current limits from config; don't hard-code them.
- **Image handling.** All three APIs downscale images that are too large, which can make small or stylized text unreadable. Crop before sending, don't rely on the API to resize.
- **Costs.** Image input is billed by size, so cropping also cuts cost. Log token and image usage per run.
- **Keys.** API keys live in environment variables, never in code, config files, logs or shared docs.
- **Idempotence.** Re-running a stage on the same inputs overwrites that stage's output only. It never touches human-checked records.

---

## 4. Stages

### Stage 1: Ingest

- Collect every page in reading order and number them.
- **Crop tall pages** (especially vertical webtoon strips) into sections at natural gutters, the white space between panels. Never cut through a bubble. Overlap crops slightly if a gutter can't be found, and de-duplicate lines that appear in both crops.
- **Detect and skip non-story images:** credit pages, scan-site watermark banners, "read at..." promo panels. Log them as skipped; don't transcribe them.
- **Keep title cards** (a series logo mid-chapter), but mark them as `beat_marker`. They become a pause in the narration, not read text.

**Gate:** every page is numbered, every crop is saved, and skipped images are logged with a reason.

### Stage 2: Transcribe (primary reader)

For each crop, get every speech bubble, thought bubble, caption and sound effect verbatim, plus art notes (characters, action, setting, mood, what changed from the previous crop).

Prompt rules:
- **Images first, instructions after.**
- **State the reading format explicitly** (from config and stage 4).
- **Require structured output** matching the line record: text, type, speaker, `speaker_basis`, art notes, order.
- **Require verbatim text,** with no correction, translation or cleanup. Ellipses, stutters ("I-is"), bold emphasis and trailing punctuation are part of the text.
- **Mark unreadable text as unreadable.** Never guess. Mark untranslated sound effects with their language instead of transcribing the script.
- **Separate what's on the page from what's inferred.** Speaker attribution must say its basis: `tail`, `position`, `context` or `guess`.

**Gate:** every crop has a transcript, or a logged failure awaiting retry.

### Stage 3: Tag bubble types

Normalize each line's type. The type decides how it gets performed.

| Visual | Type |
|---|---|
| Smooth oval | `speech` |
| Jagged, spiky or burst outline | `shout` |
| Dotted or dashed outline, or tiny text | `whisper` |
| Cloud with a bubble trail, or dark bubble with light text used for inner voice | `thought` |
| Rectangle or box | `caption` |
| Lettering drawn into the art | `sfx` |

Wavy outlines (weak, sick, unsettled) and bold or oversized words are **delivery notes** on the line, not separate types.

### Stage 4: Set the reading format and verify the order

Set the format **before** stage 2 runs, and state it in every transcription prompt. A wrong format scrambles dialogue silently: the model narrates lines in the wrong sequence with full confidence, and nothing flags it.

| Format | Reading order | Watch for |
|---|---|---|
| **Manga** (Japanese print) | Panels right-to-left, top-to-bottom. Bubbles right-to-left within a panel. | Some translations are flipped to left-to-right. Check the credits page, or whether most characters look left-handed. |
| **Webtoon** (Korean/Chinese vertical scroll) | Top-to-bottom as you scroll. Higher bubbles are spoken first. | Wide panels still read left-to-right within the row. Some manhwa were first printed as books and read left-to-right. |
| **Western comics** | Panels left-to-right, top-to-bottom. Bubbles left-to-right, then top-to-bottom. | Splash pages and diagonal layouts break the grid. Follow the art's flow and the bubble tails. |
| **Chinese manhua** | Varies: vertical scroll online, left-to-right in print, sometimes right-to-left in older editions. | Confirm per title, never by genre. |

Automated order checks, which flag lines for review rather than reordering them silently:
- **Sequence logic:** an answer appearing before its question, or a reaction before its cause.
- **Speaker position:** the first speaker in a panel usually sits on the side the format reads from.
- **Joined bubbles** are one speaker speaking in sequence. Keep their order.
- **Bubbles overlapping a character** happen during that moment of action.

Any line whose order is changed gets the `reordered` flag.

### Stage 5: Build the character bible

- Track recurring characters by appearance (hair, clothing, colors, props), since names often come late or never.
- Give each character a stable label, voice settings, pronunciation and first appearance.
- **Never import names from outside the pages.** If a name doesn't appear in the pages being narrated, the character stays under their label.
- Every speaker attribution with `speaker_basis: guess` also gets the `speaker_guess` flag.

### Stage 6: Verify flagged lines (verifier provider)

**Lines to verify:**
- Every proper noun, name, technique or skill name.
- Stylized or distorted lettering.
- Low-resolution crops.
- Any `speaker_guess`.
- Any `reordered` line.
- Lines central to the climax or a reveal.
- **Optional:** a random sample of clear lines, to estimate the primary reader's error rate.

**Method:** one cropped bubble per request, sent to a different provider from the primary reader. Ask narrow questions: "Quote the text in this bubble exactly." or "Which character does this bubble's tail point to?"

**Resolution:**
- **Readings match:** `clear`.
- **Readings differ:** `disputed`. Send it to the tiebreaker, preferably with a differently worded question.
  - **Tiebreaker matches one reading:** `majority`. Record all three readings.
  - **Three different readings:** `unresolved`.
- **Human review** (when available): a person checks every `disputed`, `unresolved`, `majority` and `proper_noun` line against the page. Their answer overrides every model and sets `human_checked`.

**Fully automated runs (no human):**
- Never resolve a disputed line by picking one provider's answer.
- An `unresolved` line is either **omitted** from the script if the story holds without it, or the run is **held** for human review if it's essential (plot, name, climax, reveal).
- **Never paraphrase around a disputed line.** Rewording page text breaks the verbatim rule. Omission leaves a gap; paraphrase creates a wrong line.
- Consistency is not proof. Repeating one model can reproduce the same misread every time. Only cross-provider agreement or a human upgrades a line.

### Stage 7: Map the story

Across the chapter, mark:
- **Arc:** setup, rising action, climax, resolution (the `beat` field).
- **Emotional peaks,** where delivery should be at its loudest, quietest or longest pause.
- **Protected reveals:** twists, identities and hidden abilities that must not be hinted at early.
- **Callbacks:** objects, phrases or setups that pay off later.

### Stage 8: Build the storyboard

Merge transcripts, verification, the character bible and the story map into `storyboard.json`, ordered by verified reading order. This is the **only** input for words in stages 9 to 11.

**Gate:** no line is left at `disputed` status. Every line has resolved to a final status.

### Stage 9: Draft the narration

Inputs:
- `storyboard.json`, the source of every word of page text
- `storytelling-guide.md`, the method
- `characters.json`
- `config.json` audience settings
- **Optional:** the crops, for describing the art only. The storyboard wins on any text.

Rules (full detail in the guide):
- Weave dialogue, captions and sound effects into continuous prose. Never mention panels, bubbles, pages or layout.
- Every `speech`, `shout`, `whisper`, `thought` and `caption` line appears verbatim, or is omitted if `unresolved`.
- Sound effects are performed or folded into the action. Non-English sound effects are performed as sound, never read or romanized.
- Delivery notes in italic parentheses mark real shifts only: new voice, tempo, volume, pause.
- Build toward the climax. Pause at scroll breaks and cliffhangers.

**Output:** `script.draft.md`, with each page-text line traceable to its ID. Recommended: keep a hidden sidecar map of script position to line ID, or an HTML comment such as `<!-- p07-c2-b3 -->` in the draft that's stripped at delivery.

### Stage 10: Edit

- Cut repetition, meaning the same phrase or name within a short span.
- Cut delivery notes that repeat the previous tone.
- Bridge name changes once ("the watcher, the one they call the Cursed Killing Star").
- Break up info-dumps with pacing beats already in the material.
- **Do not touch page-text lines.** Editing happens only in the narration around them.

### Stage 11: QA

Run in this order:

1. **Deterministic verbatim check (no model).** Extract every quoted and italicized page-text span from the script and string-match it against the storyboard text for its ID. Normalize only whitespace and curly vs. straight quotes. Any mismatch is a failure. This catches paraphrases a model reviewer might let through.
2. **Coverage check (no model).** Every storyboard line is either present in the script or explicitly omitted with a logged reason.
3. **Forbidden-term check (no model).** Scan the narration for layout words: panel, bubble, caption, page, box, "the scene shifts," "cut to," "next image." Any hit is a failure.
4. **Model review,** given the storyboard and the script side by side, not the images:
   - Order matches the verified reading order, with extra attention to `reordered` lines.
   - Nothing contradicts the art notes. No invented events, characters or lore.
   - Each speaker keeps their voice, and each line is performed as its type.
   - No protected reveal is spoiled early.
5. **Write `qa_report.md`,** listing every failure plus every `majority`, `judgment_call` and omitted line.

### Stage 12: Deliver

**Delivery gate.** The script is `final` only if all of these are true:
- The QA checks in stage 11 pass.
- No placeholders or ID comments remain in the text.
- No `disputed` or essential `unresolved` lines exist.

Otherwise it ships as `draft`, with the QA report attached.

**Output:** `script.final.md` and `qa_report.md`, or pushed to the downstream pipeline (Airtable, Drive, Notion).

---

## 5. Config

```json
{
  "title": "",
  "chapter": "",
  "reading_format": "webtoon | manga | western | manhua",
  "providers": {
    "primary":    {"provider": "anthropic", "model": "<set here>"},
    "verifier":   {"provider": "openai",    "model": "<set here>"},
    "tiebreaker": {"provider": "google",    "model": "<set here>"},
    "writer":     {"provider": "anthropic", "model": "<set here>"}
  },
  "audience": {
    "age_range": "13-20",
    "lean": "older",
    "profanity": "verbatim",
    "narrator_slang": "light"
  },
  "batching": {"transcription_crops_per_request": 4},
  "automation": {"human_review": true, "hold_on_essential_unresolved": true}
}
```

Any provider can fill any role, as long as the verifier differs from the primary reader.

---

## 6. Outside the pipeline

- **Audience calls.** Profanity, gore and slang level are editorial choices, set once in config and applied consistently. The default is to keep page text verbatim.
- **Rights.** For published recaps, check how the platform and the source handle adaptation and monetization. That's a policy decision, not a pipeline stage.
