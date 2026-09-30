# Craft reconciliation — storytelling guide × narration pipeline × current scripting

**Status:** APPROVED 2026-09-29 with the owner's answers in §12 (they
override anything earlier in this doc that disagrees). **P0 and P1 are
implemented** (see §12). P2–P4 are not started.

**Inputs reconciled**
- `docs/storytelling-guide.md` and `docs/narration-pipeline.md` (the owner's
  specs, copied here from the Desktop on 2026-09-29).
- The current contract in `manhwa-recap-v1/narrate.py` (Gemini engine) and
  `review_ui/claude_plus.py` (Claude engine) — two separate copies.
- The owner's decisions: one narrator voice only; meaningful dialogue becomes
  DIRECT speech performed in the narrator's own voice.
- A reference recap the owner supplied (`recap-script-clean-notimestamps.txt`,
  a published third-party transcript — **not committed**, only measured here).

---

## 0. Order of work

| Phase | What | Depends on | Touches |
|---|---|---|---|
| **P0** | **Sound-effect lettering defect** (§6) — a live defect, fixed regardless of the rest | nothing | `describe.py`, `narrate.dialogue_lines`, `claude_plus` reader |
| **P1** | **Per-bubble reading (prerequisite)** (§5) — text, type, speaker per bubble | P0 (same prompt) | `describe.py`, `claude_plus.describe_plus`, `narrate.build_prompt` |
| **P2** | **Direct-speech rule 3** (§3) plus every dependent fix (§7) | **P1 — hard prerequisite** | both contracts, both critiques, segmenter, TTS, eval, tests |
| **P3** | Craft rules 8–14 (§4) — prompt-only | none (can ship with or before P2) | both contracts |
| **P4** | Optional prosody experiment (§8) | P2, owner approval of spend | TTS path only |

**Why P1 comes first.** Today the reader returns one string per panel
(`"HEY, JIN! / 타앙..."`): no speaker, no bubble type. Reported speech hides
that ("he demanded to know…"). Direct speech exposes it — `"Run," the old man
said` is worse than useless if the boy said it. Direct speech without
attributed bubbles would ship confident misattributions.

---

## 1. Evidence behind the proposals

### 1a. The owner's reference recap (measured, 41,655 words)

| Measure | Value | What it tells us |
|---|---|---|
| Reported-speech verbs (asked, told, claimed, insisted…) | **129** | The reference carries dialogue as reported speech almost everywhere |
| Real direct quotations | **2** (`"Nah, I'd win."`, `"I don't care if there's too much hair."`) | Direct speech is used as a rare **punchline**, not the default |
| Narrator asides / meta lines ("I know some of you would…", "let's find out", "everyone who has ever played a Pokémon game…") | at least 6 found by pattern | Conflicts with the guide's "no asides, no meta" (see open question Q1) |
| Questions | 29 | Mostly free-indirect character thought ("How strong was Jyn exactly?"), a few addressed to the listener |
| Tense | overwhelmingly past, with present for "rules of the world" | Matches our past-tense contract |
| Bleeped profanity `[ __ ]` | 42 | The reference swears; our contract is silent on it |

**Consequence for rule 3:** the decision (direct speech for meaningful lines)
stands. The data argues that "meaningful" must be defined **narrowly** — the
line the scene turns on — or the output will read very differently from the
reference the owner likes. §3 proposes a per-scene cap.

### 1b. Current contract gaps (from the review on 2026-09-29)
- `NAME_HINT` (label rotation) is defined in `narrate.py` and never used in any prompt.
- `critique_units` defines `redundancy` but omits it from the JSON `type` list it asks for.
- `narrate.py`'s docstring still says "no metaphor" — superseded by the contract.
- No rule for sound effects, reveals, audience tone, endings, or pacing.

---

## 2. Reconciliation table — the guide's rules against ours

Verdicts: **✅ compatible** (fold in as written or already covered) ·
**🔧 adapt** (keep the intent, change the form) · **⛔ reject** (with reason).

| # | Guide rule | Current state | Verdict | Final wording / where it lands |
|---|---|---|---|---|
| G1 | One seamless story; never "panel, bubble, caption, page, layout" | Rule 4 bans panel/camera/close-up/we see | ✅ extend | Rule 4 gains: `"page", "caption", "speech bubble", "the scene shifts", "cut to", "next image"` |
| G2 | Page text is fixed and verbatim | Reported speech; recap compresses on purpose | 🔧 adapt | Only lines chosen for direct speech are taken from the panel text (trimmed, never added to). Everything else stays reported or dropped. Blanket verbatim would break the word budget and the 6–10 min recap target. |
| G3 | Map arc, protected reveals, callbacks before drafting | Beatsheet maps arc + pacing only | 🔧 adapt | Beatsheet prompt gains a REVEALS/CALLBACKS section; new rule 11 (§4) |
| G4 | Turn pictures into verbs; color into mood | Rules 2, 5 | ✅ covered | No change |
| G5 | Bridge gaps with motion, sound, time — never layout | Rule 4 (bans layout) + 4b (continuity) | ✅ extend 4b, no new rule | 4b gains one sentence (§4 rule 9 text is appended to 4b, not numbered separately) |
| G6 | Bubble types performed (shout loud, whisper quiet…) | Not representable: one voice, plain text to TTS | 🔧 adapt | Type informs **word choice and attribution**, never a costume: a shout gets a short line and a hard tag ("he snapped"); a whisper a soft tag ("she breathed"). No stage directions. |
| G7 | "Never announce a type" ("she shouts in a jagged bubble") | Covered by rule 4 | ✅ | No change |
| G8 | Action-beat attribution; emotional lead-ins | Nothing | ✅ fold into rule 3 | See rule 3 text |
| G9 | Captions become narration, unintroduced | Implicit | ✅ | Rule 3 last clause |
| G10 | Sound effects performed as sound; non-English lettering never read/romanized | Nothing — and the reader transcribes Korean SFX (§6) | ✅ + defect fix | New rule 12 + P0 |
| G11 | Collapse repeated noises; perform one | Rule 7 (density) | ✅ | Rule 8 covers rhythm |
| G12 | Free indirect narration (narration takes the character's attitude) | Rule 6 allows inferred emotion | ✅ | Named in rule 13 |
| G13 | Minimal tags; anchor once then ping-pong | Nothing | 🔧 adapt | Single narrator ⇒ every direct line keeps a tag unless the speaker is unmistakable from the previous sentence. Ping-pong without tags is a multi-voice technique. |
| G14 | Keep exact words, stutters, ellipses | — | 🔧 adapt | Keep ellipses and stutters that carry meaning; fix ALL-CAPS; may trim to the core clause; never add words. |
| G15 | Voice dials per character (pitch, pace, texture…) | One voice | ⛔ reject | Owner decision: one narrator. Characters are distinguished by tags, action and diction only. |
| G16 | No accents/stereotypes | — | ✅ | Rule 3: "no dialect spelling, no phonetic accents" |
| G17 | Label unnamed characters; bridge label→name once; vary labels | `NAME_HINT` unused | ✅ + fix | Wire `NAME_HINT` into `build_prompt`; new clause in rule 6 |
| G18 | Never use a name the pages haven't given | Rule 6 | ✅ covered | — |
| G19 | One non-visual sense per new location; texture only | Gemini: none (original rules banned embellishment). Claude: rules 10–11 world-building | 🔧 adapt | New rule 10 (§4), both engines |
| G20 | Slow for reveals/grief, fast for action; silences | Fixed 0.25/0.6s gaps only | 🔧 adapt | New rule 8 shapes **sentences**; real audible pauses need P4 (`[pause]` markup) |
| G21 | Scroll breaks as held pauses; end sections on the most open beat | Nothing | 🔧 adapt | Rule 8, second half |
| G22 | Escalate one variable per beat; save the peak | Beatsheet marks peaks | ✅ | Rule 8 |
| G23 | End on an image or line, then silence; no summary/outro | Nothing | ✅ | New rule 14 |
| G24 | Talk across, not down; stakes serious; cool not cute; light slang | Nothing | 🔧 adapt | New rule 13 — **but see Q1: the reference recap is jokier than the guide** |
| G25 | Never ask the audience questions; no asides | Nothing | 🔧 adapt — **Q1** | Rule 13 as proposed bans questions to the listener; the reference does use a few asides. Owner to decide. |
| G26 | Page profanity verbatim; narrator's own additions clean | Nothing | ✅ | Rule 13 (TTS reads profanity literally; the reference bleeps) — **Q4** |
| G27 | Credits/watermarks/promo never narrated | Junk filter + `is_credits` | ✅ covered | — |
| G28 | Flashbacks: softer past tense, firm present anchor | Past tense throughout | 🔧 adapt | Rule 9 note: mark flashbacks with a time phrase ("Years earlier…", "Back then…") and return with one ("Now…") |
| G29 | Delivery notes in italic parentheses `(whisper)` | Script text goes straight to TTS | ⛔ reject as written | TTS would read "(whisper)" aloud. Any delivery control goes through P4 markup, never through script text. |
| G30 | Present-tense performance | Past-tense contract | ⛔ reject | Keep past tense; the reference is past tense too. Borrow cadence, not tense. |

### From `narration-pipeline.md`

| # | Pipeline rule | Verdict | Why |
|---|---|---|---|
| N1 | Line record per bubble: text, type, speaker, basis | ✅ **P1** (reduced) | The single most useful idea; prerequisite for direct speech. We keep panel-level records and add a `lines` array. |
| N2 | Line IDs `p07-c2-b3` everywhere | 🔧 partial | `panel_id` + line index is enough to trace a quote; full ID plumbing is not worth it now. |
| N3 | Deterministic verbatim QA (string-match quotes vs source) | ✅ adopt | §7a — cheap, no model, catches invented quotes. |
| N4 | Forbidden-term scan, no model | ✅ already in `eval/run_eval.py` | Extend the list (G1). |
| N5 | Multi-provider verification / tiebreak / human review | ⛔ reject for now | Cost and turnaround; no human in the ingest loop. The Claude reader's `ocr_confidence` already exists; Gemini's does not (filed). |
| N6 | `unresolved` lines omitted, never paraphrased | 🔧 adapt | Rule 3: quote only lines whose speaker is identified; otherwise report or omit. |
| N7 | Reading-format config (manga RTL vs webtoon) | ✅ covered | All current sources are webtoon/manhwa; the splitter orders top-to-bottom. |
| N8 | Title cards as pauses | ✅ covered | Junk filter drops them; P4 could add a pause. |
| N9 | Resumable per-stage runs, per-run logs | ✅ mostly covered | Describe cache + usage ledger; not reworked here. |

---

## 3. Rule 3 rewrite — the full text

### 3a. `narrate.py` STYLE CONTRACT, rule 3 (replaces the current rule 3 in place)

> **3. DIALOGUE IS PERFORMED BY THE ONE NARRATOR — NEVER BY A SECOND VOICE.**
> When a line of dialogue carries the scene — a threat, a reveal, a decision, a
> refusal, a punchline, the line the moment turns on — render it as DIRECT
> speech: the character's own words, in double quotation marks, with a brief
> attribution tag that names who speaks and at most one physical action
> (*he said* / *she snapped* / *the old man muttered, already reaching for the
> crossbow*).
>
> You are a single storyteller saying that line aloud in YOUR OWN voice. The
> line is carried ONLY by three things: the attribution tag, a pacing beat
> around it (a short sentence before it, or the line standing alone as its own
> sentence), and the words themselves. Never write it as if a different voice,
> an impression, an accent or a character performance delivers it — no "in a
> deep growl", "in a squeaky voice", "mimicking", "doing his best impression",
> no bracketed or parenthesised stage directions, no dialect spelling, no
> phonetic accents, no stretched letters ("Nooooo"). The listener must always
> hear the narrator reporting what was said, in the narrator's voice.
>
> The quotation marks are for the script only: the pipeline removes them before
> the narration is voiced. Never write the words "quote", "unquote" or "in
> quotes", and never announce that someone is being quoted.
>
> Direct speech is RARE: at most TWO direct lines per scene, and only lines that
> earn it. Every other exchange stays reported narration exactly as rule 2b
> requires, and trivial one-word reactions, grunts and repeated shouts fold into
> reported narration or are dropped. Take the words from the panel text of the
> panel they belong to: you may fix ALL-CAPS, keep a meaningful ellipsis or
> stutter, and trim the line to its core clause, but never add a word the
> character did not say. Quote a line ONLY when the panel facts identify its
> speaker; if the speaker is uncertain, report it or leave it out. Narration
> boxes are never quoted — they become your own narration, unannounced.

**Rule 2b, one clause (needed for consistency, not a rule change):** "gets its
own reported-speech sentence" → "gets its own sentence — direct speech for the
lines rule 3 selects, reported speech for the rest".

### 3b. `claude_plus.SCRIPT_SYSTEM` rule 4 — the same text

Claude's contract numbers it rule 4 (its rule 3 is dialogue fidelity). Replace
rule 4 with the §3a text verbatim, and apply the same 2b clause to its rule 3.
**Both engines must change together**, or Gemini and Claude chapters will voice
dialogue differently.

### 3c. VOICE ANCHOR — one new example (added, existing three kept)

> - "The old man did not lower the crossbow. \"One more step,\" he said, \"and
>   you leave through the window.\" Nobody moved."

This demonstrates: a short pacing sentence before, one direct line in the
narrator's voice, a plain tag, and a consequence after. The guide's cadence
("Get down!" Mia screamed, already diving) is the model; the content is new.

---

## 4. New rules 8–14 (appended after rule 7; rules 1–7 keep their numbers)

> **8. PACE FOLLOWS THE PULSE.** Slow down for reveals, grief, awe and the beat
> before danger: longer sentences, one detail at a time. Speed up for fights,
> chases and arguments: short sentences, fewer clauses. Three identical hits
> become one rhythmic line ("He struck. He struck again. The wall held.").
> Escalate one thing per beat — speed, stakes or scale — and save all three for
> the peak. End a scene on its most open beat: an unanswered line, a reaching
> hand, a sound with no source.
>
> **9.** *(Appended to rule 4b, not a new number.)* Carry the reader across a
> cut with motion, time or sound — "by the time he reached the door…", a noise
> that starts in one moment and lands in the next — never with layout language.
> Mark a flashback with a time phrase ("Years earlier…") and return with one
> ("Now…").
>
> **10. ONE SENSE PER NEW PLACE.** The first time the story arrives somewhere
> new, give it ONE strong non-visual detail the art clearly implies — heat,
> damp, a smell, a sound. Texture only: never a new object, event, person or
> rule. Then refer back to the place in a phrase; do not describe it again.
>
> **11. PROTECT THE REVEALS.** The chapter summary marks reveals and callbacks.
> Never hint at a marked reveal before the scene where it lands. When an object
> or line will pay off later, give it one clean mention the first time so the
> payoff clicks.
>
> **12. SOUND EFFECTS ARE EVENTS, NOT TEXT.** Perform a big sound effect as the
> event it marks ("the door came off its hinges"), fold small ones into the
> action, and never read out, spell, romanize or translate sound-effect
> lettering — above all lettering in Korean, Japanese or Chinese script.
>
> **13. CONFIDENT, NOT CUTE.** Talk across to a sharp older-teen listener: cut
> what the art already makes obvious, play fear, loss and menace straight, and
> let humor come from timing and understatement — the narrator's dry view of a
> character, never a joke that breaks a heavy moment. Let the narration take on
> the attitude of the character in focus when it fits. Do not ask the listener
> questions or address them directly; a question for suspense belongs to a
> character. *(Wording pending Q1.)*
>
> **14. LAND THE ENDING.** The final scene slows down and ends on a concrete
> image or a line, then stops. No summary, no outro, no "what will happen next".

Also: `NAME_HINT` is added to the `build_prompt` text under rule 6 ("refer to
characters by rotating labels…"), which it was always meant to be.

---

## 5. P1 — per-bubble reading (the prerequisite)

### 5a. `panel-describe/describe.py` VISION_PROMPT — schema change

Keep `ocr_text` (the matcher and every existing project depend on it), add
`lines`:

```json
{
  "visual_description": "...unchanged rules...",
  "ocr_text": "<English dialogue/caption text only, joined with ' / ' — see lines>",
  "lines": [
    {"text": "<exact words, as written>",
     "type": "speech|shout|whisper|thought|caption|sfx",
     "speaker": "<name if the panels show it, else a short visual label like 'the old man'; null for captions and sfx>",
     "speaker_basis": "tail|position|context|guess",
     "lang": "en|ko|ja|zh|other"}
  ]
}
```

New prompt lines (added after the OCR rules):

> List every speech bubble, thought bubble, caption box and sound effect in
> reading order (top to bottom, then left to right). For each, give its type
> from the outline (smooth oval = speech, spiky = shout, dotted or tiny =
> whisper, cloud or inner-voice box = thought, rectangle = caption, lettering
> drawn into the art = sfx) and who says it: follow the bubble's tail; if there
> is no tail, use position; say which you used. Use a name only if these panels
> show it. For sound-effect lettering in Korean, Japanese or Chinese script, do
> NOT copy the characters: set `lang` to its language and put a two-to-four
> word English description of the sound in `text` (e.g. "heavy metal impact").

### 5b. `claude_plus.describe_plus` — the same `lines` array

Its JSON schema gains the same `lines` property. `ocr_confidence` stays.

### 5c. `narrate.build_prompt` — consume `lines`

```
Panel 3 (ID: page004_panel_002): <description>
  Lines:
    [shout] the old man (tail): "GET OUT OF MY HOUSE!"
    [thought] Jin (context): "He won't shoot."
    [sfx ko] creaking wood
```

Rule 3's "speaker identified" test becomes mechanical: a line is quotable only
when `speaker_basis` is `tail` or `position`. Old projects without `lines` fall
back to today's `ocr_text` block, and rule 3 then allows **no** direct speech
(no attribution to trust) — so un-re-described projects keep today's behaviour
exactly.

**Cost:** a longer JSON reply per panel (more output tokens), no extra calls.
Not yet measured — to be measured on the fixture before approval of P1.

---

## 6. P0 — SEPARATE DEFECT: the panel reader copies Korean sound-effect lettering

**Independent of everything above; worth fixing even if nothing else ships.**

`describe.py` tells the reader to transcribe "sound effects… EXACTLY as
written". Korean lettering is copied verbatim into `ocr_text`. Measured on the
projects on this machine:

| Project | Segments counted as "dialogue" | Of those, Korean-only SFX | Panels affected |
|---|---|---|---|
| dungeon-odyssey-ch1 (eval fixture) | 265 | **77 (29%)** | 38 / 126 |
| swordmasters-youngest-son_1 | 223 | **52 (23%)** | 41 / 138 |

Examples: `탕 / HEY, JIN! / 타앙...`, `ARE ASKING YOU A QUESTION! / 퍼억 / KUGH!`.

**What it does today (verified):**
1. **Inflates the word budget.** `dialogue_lines()` counts every `/` segment,
   so each Korean SFX adds 7 words of budget — padding the density rule exists
   to prevent.
2. **Pollutes the matcher's strongest signal.** OCR is "the highest-priority
   match signal"; Korean glyphs are noise there.
3. **Relies on the writer ignoring it.** The finished Swordmasters script
   contains **0** Korean characters, and a spot check for a few common
   romanizations (tang, kwaang) found none — so no audible damage has been
   observed yet. That spot check is not exhaustive, and nothing prevents it.

**Proposed fix (two layers):**
- **Code, immediate, no re-describe needed:** `narrate.dialogue_lines` and the
  Claude equivalent skip segments with no Latin letters that contain
  Hangul/Kana/Han; the scene prompt shows such segments as `[sound effect]`
  instead of the glyphs. Existing projects benefit at once.
- **Reader, going forward:** the §5a prompt text (describe the sound, record
  `lang`, don't copy the script).
- Test: a descriptions fixture with `"탕 / HEY, JIN!"` counts ONE dialogue line
  and the rendered prompt contains no Hangul.

---

## 7. Every dependent file that breaks — with the proposed change

### 7a. `manhwa-recap-v1/eval/run_eval.py`
**Breaks:** `BANNED` fails any `"…"` span as "quoted dialogue (contract:
reported speech only)".
**Change:** replace that entry and add three checks.

```python
BANNED = [
    # (the quoted-dialogue ban is removed: rule 3 now requires direct speech)
    (r"\b(the panel|this panel|the image|the frame|the camera|on screen|"
     r"in the foreground|in the background of the panel|the page|the caption|"
     r"speech bubble|the scene shifts|cut to|next image)\b", 'layout language'),
    (r"\b(speed lines|sound effect|sfx|text box)\b", 'comic-mechanics language'),
    (r"\bwe (see|watch|observe)\b", 'viewer language'),
    # single-narrator rule: no voice costume in the text
    (r"\b(in a (deep|squeaky|high|low|gravelly|booming) voice|mimick\w*|"
     r"impersonat\w*|\bquote\b|\bunquote\b)", 'second-voice / quote marker'),
    (r"\((whisper|shout|softly|loud|pause)[^)]*\)", 'stage direction in text'),
    (r"[ᄀ-ᇿ㄰-㆏가-힯぀-ヿ一-鿿]",
     'untranslated sound-effect lettering'),
]

def quote_checks(scenes, panels):
    """Every quoted span must come from its scene's panel text (case- and
    punctuation-insensitive, may be a trimmed substring), and a scene may hold
    at most 2 quoted lines (rule 3)."""
```

The approved fixture scripts contain no quotes, so they still pass.

### 7b. `narrate.py` — `critique_units` (the fact-check pass)
**Breaks:** `style_violation` defines "quoted dialogue" as a violation, so the
reviewer would flag every correct direct line and the reviser would undo it.
**Change:**
- `style_violation`: "present tense, ANY mention of the camera/panel/image/frame
  or other layout language, a stage direction in parentheses, or dialogue
  written as a character impression" — "quoted dialogue" removed.
- Add types (and fix the JSON type list to include `redundancy`):
  - `flat_dialogue` — a line the scene turns on is reported when rule 3 makes it
    a direct-speech line.
  - `misattributed_dialogue` — a direct line credited to someone the panel facts
    do not identify as its speaker.
  - `spoiled_reveal` — a marked reveal hinted at before its scene.
- `revise_unit` needs no change: it already appends typed notes.

### 7c. `review_ui/claude_plus.py` — `CRITIQUE_SYSTEM` and its schema enum
Same edits as 7b to the prose; add `flat_dialogue`, `misattributed_dialogue`,
`spoiled_reveal` to the `enum` at the `issues.type` property (currently
`hallucination … missing_worldbuilding`).

### 7d. `review_ui/claude_plus.py` — `SCRIPT_SYSTEM` rule 4 (+ rule 3 clause)
§3b.

### 7e. `review_ui/test_claude_plus.py:409`
**Breaks:** asserts `"NEVER use quotation marks" in PLUS.SCRIPT_SYSTEM`.
**Change:**
```python
check("...and the single-narrator direct-speech rule",
      "NEVER BY A SECOND VOICE" in PLUS.SCRIPT_SYSTEM
      and "removes them before" in PLUS.SCRIPT_SYSTEM)
check("...and the critique no longer bans correct direct speech",
      "quoted dialogue" not in PLUS.CRITIQUE_SYSTEM
      and "flat_dialogue" in PLUS.CRITIQUE_SYSTEM)
```

### 7f. `manhwa-recap-v1/beat_segmenter.py`
**Breaks (two ways):**
1. The split `(?<=[.!?])\s+` does not split after a closing quote, so
   `She ran. "Get down!" Then she dove.` never splits after `!"`.
2. Short-fragment merging does `merged[-1].rstrip(".!?") + ". " + s`, which
   strips a shouted line's `!`/`?` and produces `"Get down!". Mia…`.

**Change:**
```python
parts = re.split(r'(?<=[.!?])\s+|(?<=[.!?]["”])\s+', text)
...
if merged and len(s.split()) < MIN_BEAT_WORDS:
    prev = merged[-1]
    merged[-1] = prev + ("" if prev.rstrip()[-1:] in '.!?"”' else ".") + " " + s
```
Test: `"Get down!" Mia screamed.` stays one beat; `He ran. "Go!" She followed.`
splits into sensible beats without losing `!`.

### 7g. TTS — `review_ui/server._synth_rest` and `manhwa-recap-v1/tts.py`
**Needs adding:** the quotes must never reach the voice.
```python
_QUOTES = str.maketrans({'"': None, '“': None, '”': None})

def speakable(text):
    """Script text -> what the narrator says. Quotation marks are a page
    convention with nothing to sound like; the attribution tag and the
    sentence rhythm carry the line."""
    return re.sub(r"\s{2,}", " ", text.translate(_QUOTES)).strip()
```
Both call sites synthesize `speakable(text)`; the TTS cache key uses the
speakable form (so a quote-only change costs nothing). Single quotes are left
alone — they are apostrophes. Test: no `"`/`“`/`”` in any request body.

### 7h. Knock-on effects to watch (no change proposed now)
- `shot_planner.should_crop_close` fires on "shout", "scream", "cry" — more
  attribution tags will trigger more crop-planning calls. Measure on the fixture.
- The matcher will likely score better: direct lines share words with the OCR.

---

## 8. SSML / prosody for Chirp 3 HD — verified against Google's documentation

Source: Google Cloud Text-to-Speech, *Chirp 3: HD voices*
(docs.cloud.google.com/text-to-speech/docs/chirp3-hd), page last updated
2026-09-24, read 2026-09-29.

**What the documentation states**
- SSML is supported for Chirp 3: HD **in Preview**, **synchronous requests
  only** ("SSML tags are not currently supported for streaming requests").
  Ours are synchronous (`v1/text:synthesize`, plain `input.text`, no
  `audioConfig` beyond MP3).
- Supported tags include `<speak>`, `<p>`, `<s>`, `<break>` ("controls pausing
  between words"), `<prosody>` ("customizes the pitch, speaking rate, and volume
  of the contained text"), `<say-as>` (but not `interpret-as="expletive"` /
  `"bleep"`), `<phoneme>`, `<sub>`, `<audio>`, `<voice>`.
- Separately, voice controls (also Preview): **pace** via `speaking_rate`
  0.25×–2×; **pauses** via `[pause short]`, `[pause]`, `[pause long]` in the
  `markup` input field — "the precise length… isn't fixed", and the model
  "might occasionally disregard the pause tags".
- The page gives **no allowed values or examples** for `<prosody>` with Chirp 3:
  HD, and does not say whether SSML and `markup` can be combined.

**What that means for us**
- A momentary change on a direct line (e.g. `<prosody rate="95%">` or a small
  pitch offset) is **documented as possible but unverified** for this voice.
  The only way to know is a listening test.
- **`<voice>` must never be used** — it is exactly the second voice the owner
  ruled out.
- A pitch lift is the closest thing to the "tonal costume" rule 3 forbids. If
  anything is tried, prefer a **pause before the line** (`<break time="250ms"/>`
  or `[pause short]`) and at most a slight rate change; treat pitch as
  excluded unless the owner hears it and approves.
- Preview features can change. Anything we build on them needs a flag and a
  flat fallback — the flat fallback being exactly P2 as written.
- Pauses (`[pause]` / `<break>`) are also the real way to make rule 8's
  silences audible.

**Recommendation:** ship P2 flat. Run P4 as a separate listening test: the same
3 direct lines voiced flat, with a pre-line pause, and with a pause plus
`rate="95%"`, played to the owner. That is a few hundred TTS characters,
through the usage guardrail — needs a go-ahead.

---

## 9. Rejected, with reasons

| Item | Source | Reason |
|---|---|---|
| Per-character voice dials | Guide §4 | One narrator (owner decision). |
| Delivery notes in the script text | Guide §3, pipeline stage 9 | TTS reads them aloud. Delivery goes through P4 markup or nowhere. |
| Every bubble verbatim | Guide core, pipeline principle 1 | A recap compresses; verbatim would blow the budget and runtime. Kept for quoted lines only. |
| Present-tense performance | Guide examples | Past-tense contract, matches the reference. |
| Three-provider verification, human review, hold-on-unresolved | Pipeline stages 6, 12 | Cost and turnaround; no human in the ingest loop. |
| Full line-ID data model | Pipeline §2 | `panel_id` + line index suffices for traceability. |
| Ping-pong untagged exchanges | Guide §3 | Needs distinct voices to stay clear. |

---

## 10. Open questions for the owner

- **Q1 — Tone: guide vs reference.** The guide bans asides, questions to the
  listener and meta jokes; the reference recap you like uses several ("I know
  some of you would…", "let's find out", pop-culture comparisons). Rule 13 is
  written to the guide. Keep it, or allow a light aside?
- **Q2 — Direct-speech density.** Proposed cap: 2 per scene. The reference uses
  2 in 41,655 words. Keep 2, lower it, or leave it to the model?
- **Q3 — Test fixture and spend.** Both fixtures need a paid re-describe to get
  `lines` (P1): `eval/fixtures/dungeon-odyssey-ch1` (126 panels, already has an
  approved script — the better before/after) or `fixtures/murim_ch43`
  (pages only). Then one narrate run for the before/after. Approve which, and
  the spend?
- **Q4 — Profanity.** TTS reads page profanity as written; the reference
  bleeps. Keep, soften, or skip?
- **Q5 — P4 listening test** (§8): go or no-go.

---

## 11. Test plan after approval

1. P0 unit tests (dialogue count, no Hangul in prompts), fixture budget before/after.
2. P1: re-describe the approved fixture; check that `lines` speakers match the art on a sampled page.
3. P2: segmenter, `speakable`, eval quote checks, updated `test_claude_plus`;
   then one narrate run on the fixture. **Deliverable to the owner:** before and
   after text for at least one dialogue-heavy scene, plus the voiced mp3 of that
   scene so the direct line can be *heard*.
4. Everything logged to `manhwa-recap-v1/memory.md`; every push preceded by an
   in-flight job check.

---

## 12. Owner decisions (2026-09-29) and P0/P1 results

### Decisions — these override the proposals above
- **Q1 Tone:** follow the guide. No meta jokes, no asides, no questions to the
  listener. The reference recap's tone is rejected. Rule 13 stands as written.
- **Q2 Direct-speech cap: 2–3 lines per CHAPTER, not per scene** — the
  reference's actual density — reserved for the most pivotal lines. Rule 3's
  "at most TWO direct lines per scene" becomes: *"Direct speech is RARE: the
  whole chapter gets two or three direct lines, no more, and only the most
  pivotal ones — the line the chapter turns on. The chapter summary marks which
  lines those are; every other line is reported."* Because the cap is
  chapter-wide and scenes are written separately, it cannot be left to each
  scene's writer: the beatsheet pass selects the 2–3 lines (by panel and
  text), each scene prompt is told which of them fall in its scene, and a code
  check counts quotes across the whole script. The eval check in §7a changes
  from "at most 2 per scene" to "2–3 per chapter".
- **Q3 Test chapter:** Murim Psychopath ch.44 (rerun if needed).
- **Q4 Profanity:** keep mild profanity verbatim; soften or bleep harsher words
  for platform safety. This needs a word list (mild/harsh) applied in the
  `speakable()` step (§7g), so the script keeps the page's words while the
  audio is safe. The list is to be proposed with P2.
- **Q5 Listening test:** yes. Pause/pacing variants are played to the owner
  before anything is kept.

### P0 — implemented
`manhwa-recap-v1/ocr_lines.py` (one module used by both engines and the
reader): drawn non-English SFX never count toward the word budget, and the
writer sees them as `[sound effect]`, never as glyphs. The Gemini reader
(`describe.py`) and the Claude reader (`claude_plus.DESCRIBE_SYSTEM`) are told
to describe such SFX in English instead of copying them. `ocr_text` no longer
carries them.

### P1 — implemented, and measured on real ch.44 art
Both readers return `lines` (text, type, speaker, speaker_basis, lang),
normalised by `ocr_lines.normalize_lines`. `ocr_text` is rebuilt from `lines`,
so the two cannot disagree. Old projects without `lines` render exactly as
before (tested). The writer's prompt now shows each bubble with its type,
speaker and basis.

Run of the new Gemini reader on 32 ch.44 panels (pages 2 and 13):
- **SFX: every Korean sound effect came back as an English description**
  ("whoosh of fire", "loud explosion", "heavy drum thud"). No glyphs.
- **Speakers:** where a basis of `tail`/`position` was given, the speakers
  checked against the art were correct or reasonable (e.g. a tail-less burst
  bubble over the protagonist credited to him).
- **Finding 1 — labels drift between panels.** The reader sees one panel at a
  time, so the same man was "the wild-haired man", "the curly-haired man"
  and possibly "the dark-haired warrior" on nearby pages. Labels are
  descriptions, not identities. **Added guard for P2:** a line is quotable
  only if its basis is `tail`/`position` AND its label can be matched to a
  named character in the chapter summary. Otherwise it is reported. (A
  chapter-level character list — the pipeline doc's "character bible" — is
  the durable fix; it is deferred.)
- **Finding 2 — a monologue split across panels gets inconsistent types.**
  "HOW DID / YEON YOUNGHA KNOW / I'D PASS THROUGH / HERE?" came back as
  sfx/caption/caption/shout, with no speaker. That is safe (none of it is
  quotable), but it means `type` is advisory, not reliable.
- **Type errors seen:** a normal speech bubble labelled `thought` (1 of 3 on
  page 13).
- Budget count: captions still count (as they did before); only sound effects
  are excluded.

---

## 13. P2 results on Murim ch.44 (2026-09-30) — branch `p2-direct-speech`, NOT deployed

**Setup:** all 154 ch.44 panels re-read with the P1 reader (154/154 ok), into a
scratch copy — the live project was not touched. "Before" = production
`narrate.py` (main @ 9dea88c), "after" = the P2 branch, both on the SAME new
panel reads.

**First run found a guard bug (mine):** 0 lines approved. The model picked 3
good lines but wrote speakers as compound labels ("The Masked Ninja (The
Spy)"), and the guard needed the whole string in the summary. This chapter
also never names most of its cast — its own summary says "the protagonist",
"a spy". Fix: the speaker passes when ANY part of it is a character the
summary refers to; bare generic nouns ("man", "woman") never pass.

**Second run:** 3 lines approved, 3 used, 0 stray quotes, 0 unresolved:
- The Protagonist: "Is this the only option I have...?" — muttered, reaching into the void
- The Spy: "C-Cursed Killing Star? Why are you...?" — stammered
- The Cursed Killing Star: "You were looking at that man rather strangely just now, weren't you?" — asked
All three in the narrator's voice: a plain tag and one action, no costume.
Word count 1,178 → 1,238. The eval passes both scripts.

**Listening test (P4), measured with silence detection + Whisper:**
| Variant | Gap before the line | Quoted words | Verdict |
|---|---|---|---|
| A plain | 0.87 s (natural) | 2.92 s | already a clear beat |
| B `[pause short]` markup | 0.39 s | 2.22 s (−24%) | **backfires** — markup mode speeds up the whole delivery |
| C SSML `<break 300ms>` + `<prosody rate=95%>` on the quote only | 1.20 s | 3.04 s (+4%) | works as designed; subtle |
No variant dropped words or spoke "quote". Owner to choose A or C by ear.

**Audio defect found and fixed:** a written stutter "C-Cursed" was voiced
as roughly "see, Cursed". `speakable()` now drops a stuttered letter-prefix
for the AUDIO only (the script keeps it); real hyphenated words are
untouched. Re-voiced and confirmed: the stray "see" is gone.

**Existing projects:** a project read before P1 has no `lines`, so it has no
candidates and quotes nothing (identical to today) until it is re-read.
