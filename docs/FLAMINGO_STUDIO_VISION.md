# Flamingo Recap Studio Vision

Flamingo is one creator's private research, production and audience-learning studio. Its purpose is excellent, accurate, entertaining manhwa recaps that attract the right viewers, sustain attention, earn return visits and subscriptions, and require fewer manual corrections. It is not a public SaaS, billing system or high-volume upload factory.

The creator is creative director and final publishing authority. Automation should act like a coordinated research team, story analyst, OCR specialist, writer/editor, voice director, visual editor, quality director, packaging strategist and audience analyst. It must make uncertainty and evidence visible, respect spending limits, and preserve meaningful human decisions.

## Integrated workflow

1. **Research opportunities:** discover series and aliases, assess recent demand, comparable recap supply, audience fit, release timing, available sources, production effort and uncertainty. Distinguish recent view changes from lifetime averages.
2. **Understand the source:** preserve artwork, reading order, dialogue, captions, actions, reactions, reveals, characters and uncertain speakers. Keep visible evidence separate from inference and canonical series context. Trace source lines and events into narration.
3. **Write compelling stories:** accurate openings, cause and effect, characterization, meaningful dialogue, natural transitions, suspense, emotional variation and appropriate personality. Avoid invented events, repeated explanations, inconsistent names and mechanical descriptions of artwork.
4. **Direct voice and sound:** clear, natural speech, stable voices, appropriate pronunciation, deliberate scene pauses and clean transitions. Measure actual audio; do not mask missing voice with music or apply global acceleration as a substitute for editing.
5. **Edit visually:** choose images and crops for the spoken event; differentiate action, dialogue, reactions and reveals; keep tall artwork readable; vary motion purposefully without excessive effects. Preserve artwork and the working rendering pipeline.
6. **Assure quality:** combine source, narrative, structural and delivered-media checks. Findings need severity, confidence, timestamps, affected source/beat/segment IDs and focused repair suggestions. A report must identify what it actually inspected. Incomplete checks are not passes.
7. **Package and approve:** generate distinct, accurate title/thumbnail promises supported by chapter evidence; maintain series identity, aliases, playlists and metadata. Treat predicted scores as hypotheses and compare with observed impressions, CTR, retention and subscriber conversion.
8. **Distribute approved assets:** long-form first, then original Shorts and community participation, followed by measured Instagram/Facebook Reels experiments and later X experiments. Vertical stories require their own hooks, structure, crops, captions and QA.
9. **Listen and learn:** summarize comments, requests and complaints; draft personalized replies for approval; connect editorial corrections and observed audience performance to future research, scripts, editing and packaging. Distinguish correlation from causation.

## Formats and operator experience

Support chapter recaps and multi-chapter compilations. Eventually, one approved recap can produce two distinct vertical mini-stories, a character discussion, a poll, reply suggestions and platform-specific metadata. Each asset needs an objective: discovery, long-form conversion, conversation or returning-viewer engagement.

Use the existing Home, Library, Chapter, Queue, Activity and Settings surfaces. A unified brief should summarize research evidence, production progress, actionable quality defects, packaging choices, assets awaiting approval and observed performance. Do not create another disconnected dashboard or queue.

Keep one main Flamingo channel as the baseline, with genre/series playlists. Additional channels need evidence about audience overlap and production capacity. Resolve canonical channel IDs and connected profiles before altering any destination; account labels alone are insufficient.

## Engineering and authority boundaries

Preserve working ingestion, source fallbacks, Series Bible, structured OCR, anchored scripts, matching, timing repair, renderers, packaging, usage limits, undo and publishing controls. Prefer focused improvements and shared interfaces to replacement subsystems.

Development may proceed autonomously on feature branches with offline tests. Significant work is appended to `manhwa-recap-v1/memory.md` and committed/pushed at checkpoints. Never stage scraped art, generated proprietary media, credentials or private account evidence.

Without separate owner authorization: no main push/merge, deployment, production-data mutation, paid AI request, real chapter regeneration, account change or public publishing/scheduling. No engagement bots or unsolicited automatic replies. Source permissions and publishing authority must remain explicit; boilerplate disclaimers do not establish asset rights.

New technical publishing enforcement remains off by default until calibrated against real Flamingo media and explicitly approved. Mark offline implementations Ready for Review, not production-complete. Preserve original media and corrections; make repairs targeted, reversible and cost-bounded.

Implementation tasks, evidence and unresolved decisions belong in [the development roadmap](FLAMINGO_DEVELOPMENT_ROADMAP.md). Detailed operational behavior belongs in `manhwa-recap-v1/PIPELINE_HANDBOOK.md`; append-only session history belongs in `memory.md`.
