"""
Chapter-URL ingestion pipeline for the review UI.

Given a chapter URL, run the whole pipeline into a self-contained project dir
and produce a segments.json the review UI can open:

  scrape  → download the chapter's page images         (scraper.download_chapter)
  split   → cut pages into panels, vision-segment tall  (split_panels.py --batch)
  describe→ OCR + visual description per panel           (panel-describe/run.py)
  narrate → write narration FROM the panels              (narrate.generate_narration)
  voice   → segment into beats + TTS each beat           (beat_segmenter + REST TTS)
  match   → align beats → panels (DP aligner)            (matcher)
  segment → group into render segments                   (segments.build_segments)

Every stage reports progress through a callback so the UI can show a live
status. Clips are NOT rendered here — that stays on-demand ("Render missing" /
export), so ingestion finishes fast and the user reviews before paying render
time. All stages run under the recap venv (this interpreter).
"""

import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
RECAP = os.path.abspath(os.path.join(HERE, ".."))
ROOT = os.path.abspath(os.path.join(RECAP, ".."))
PROJECTS = os.path.join(HERE, "projects")
PY = sys.executable  # recap venv python (has google-genai, numpy, PIL, whisper)

STAGES = ["scrape", "split", "describe", "narrate", "voice", "match", "segment"]


def parse_series_chapter(url):
    """(series_slug, chapter_id) for a chapter URL.

    Providers get first refusal, because these regexes encode ONE site's URL
    shape. On a WEBTOON episode URL they returned
    ('episode-1', 'viewer?title_no=5988&episode_no=1') and the project id
    became 'episode-1_viewer-title_no-5988-episode_no-1' — no error, just
    nonsense, which is the worst failure mode available.

    The Asura path below is untouched and still handles every URL the
    providers do not claim, so nothing about today's working flow changes.
    """
    import hashlib
    try:
        import providers as _prov
        p = _prov.for_url(url)
        if p.name == "webtoon":
            _, _, slug, tno = p._parts(url)
            m = _prov.WebtoonProvider._EPISODE_NO.search(url or "")
            if m:
                return f"{slug}-{tno}" if tno else slug, m.group(1)
    except Exception:
        pass                              # fall through to the original rules
    url_clean = url.strip().rstrip("/").lower()
    m1 = re.search(r"/comics/([^/]+)/chapter/([^/]+)", url_clean)
    if m1:
        return m1.group(1), m1.group(2)
    m2 = re.search(r"/comics/([^/]+)/chapters?/([^/]+)", url_clean)
    if m2:
        return m2.group(1), m2.group(2)
    m3 = re.search(r"/([^/]+)/chapter/([^/]+)", url_clean)
    if m3:
        return m3.group(1), m3.group(2)
    parts = [p for p in url_clean.split("/") if p]
    if len(parts) >= 2:
        if "." in parts[-2] or len(parts[-2]) < 2:
            h = hashlib.md5(url.encode("utf-8")).hexdigest()[:8]
            return "series", h
        return parts[-2], parts[-1]
    h = hashlib.md5(url.encode("utf-8")).hexdigest()[:8]
    return "series", h


def clean_series_slug(slug):
    return re.sub(r"-[a-f0-9]{8}$", "", slug)


def to_title_case(slug):
    return slug.replace("-", " ").title()


def _slug(url):
    series, chapter = parse_series_chapter(url)
    series_slug = re.sub(r"[^a-z0-9_-]+", "-", clean_series_slug(series))
    chapter_slug = re.sub(r"[^a-z0-9_-]+", "-", chapter)
    return f"{series_slug}_{chapter_slug}"


ENGINES = ("gemini", "claude")


def _run_claude_engine(url, progress, job_id=None, fresh=False):
    """The Claude pipeline, reached through the SAME entry point as Gemini.

    Deliberately a DISPATCH, not a stage-level merge. `claude_lab.run_lab` is
    monolithic — it does its own scrape, split, read, script, place and
    segment — so interleaving its stages with this module's would mean
    refactoring it. That is a large change to a paid path, and the win here is
    the operator-facing one: one entry point, one projects list, one engine
    field. The internals stay where they are until there is a reason to move
    them.

    run_lab reports progress as a single string; this module's callers expect
    (stage, msg, pct), so the two are adapted here rather than changing either.
    """
    import claude_lab

    stages = claude_lab.STAGES
    seen = {"i": 0}

    def adapt(line):
        stage = str(line).split(":", 1)[0].strip()
        if stage in stages:
            seen["i"] = max(seen["i"], stages.index(stage))
        pct = int(100 * seen["i"] / max(len(stages) - 1, 1))
        progress(stage if stage in stages else "segment", str(line), pct)

    man = claude_lab.run_lab(url, progress=adapt, job_id=job_id, fresh=fresh)
    if man.get("status") != "ok":
        raise RuntimeError(man.get("error") or "claude engine failed")

    # Stamp the engine onto whatever project.json run_lab wrote, so the field
    # is present regardless of which path produced the project.
    pdir = os.path.join(PROJECTS, man.get("project") or "")
    pj = os.path.join(pdir, "project.json")
    try:
        with open(pj, encoding="utf-8") as f:
            meta = json.load(f)
    except (OSError, ValueError):
        meta = dict(man)
    meta["engine"] = "claude"
    with open(pj, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2)
    return meta


def run_ingest(url, progress, tts_key=None, job_id=None, fresh=False,
               engine="gemini"):
    """Run the pipeline for one chapter URL. `progress(stage, msg, pct)` is
    called as it advances. Returns the finished project dict.

    `job_id` scopes the cost/abuse guardrails (usage.py): it's set for this
    thread (in-process narrate/matcher/TTS calls) and passed via RECAP_JOB_ID
    env var to the describe subprocess, so every external API call this
    ingestion makes is attributed to the same job for the per-job cap.

    `fresh=True` (S4) clears the project's DERIVED artifacts so every stage
    regenerates: cached script/provenance, segments, review state, per-beat
    audio files and clips. Kept: descriptions.json (describe --merge re-does
    only changed panels) and the hash-keyed TTS cache (unchanged sentences
    re-synth for free). Needed to re-run a chapter after a pipeline fix —
    without it the script cache happily replays the old cut."""
    if engine not in ENGINES:
        raise ValueError(f"engine must be one of {ENGINES}")
    if engine == "claude":
        return _run_claude_engine(url, progress, job_id=job_id, fresh=fresh)

    sys.path.insert(0, RECAP)
    sys.path.insert(0, os.path.join(RECAP, "hyperframes"))
    import scraper, narrate, beat_segmenter, matcher
    from segments import build_segments
    import usage
    job_id = job_id or "unknown"
    usage.set_job(job_id)

    proj_id = _slug(url)
    proj = os.path.join(PROJECTS, proj_id)
    pages = os.path.join(proj, "pages")
    crops = os.path.join(proj, "crops")
    audio = os.path.join(proj, "audio")
    for d in (pages, crops, audio):
        os.makedirs(d, exist_ok=True)

    if fresh:
        import glob as _glob
        import shutil as _shutil
        progress("scrape", "fresh=1 — clearing derived artifacts…", 2)
        for f in ("script.txt", "script.json", "segments.json", "review.json",
                  "storyboard.json", "edits.log.jsonl", "beatsheet.json"):
            try:
                os.remove(os.path.join(proj, f))
            except FileNotFoundError:
                pass
        for f in _glob.glob(os.path.join(audio, "beat_*.mp3")) + \
                 _glob.glob(os.path.join(audio, "slices", "*.mp3")):
            os.remove(f)
        _shutil.rmtree(os.path.join(proj, "clips"), ignore_errors=True)
    desc_path = os.path.join(proj, "descriptions.json")

    # 1. scrape -----------------------------------------------------------
    progress("scrape", "Downloading chapter images…", 5)
    imgs = scraper.download_chapter(url, pages)
    if not imgs:
        raise RuntimeError("scraper downloaded no images (blocked or bad URL)")
    # Surface a short/gappy scrape where a human will actually see it. The
    # Doctors Rebirth truncation (3 of 11 pages) sat in split.log and nothing
    # else ever mentioned it, so the run looked entirely successful.
    scrape_warning = getattr(scraper, "LAST_WARNING", "") or ""
    if scrape_warning:
        progress("scrape", f"\u26a0 {len(imgs)} pages — {scrape_warning}", 12)
    else:
        progress("scrape", f"Downloaded {len(imgs)} pages.", 12)

    # 2. split (with vision segmentation of tall panels) ------------------
    progress("split", "Splitting pages into panels…", 18)
    # Wipe stale crops first: split_panels writes into the dir WITHOUT
    # cleaning it, so a splitter change (e.g. YOLO weights appearing) leaves
    # old crops mixed with new ones and describe/match then run on a corrupt
    # union. Descriptions are safe to keep — describe runs in --merge mode
    # and re-describes any panel whose dimensions no longer match.
    import shutil
    shutil.rmtree(crops, ignore_errors=True)
    os.makedirs(crops, exist_ok=True)
    subp_env = {**os.environ, "RECAP_JOB_ID": job_id}
    split_log = os.path.join(proj, "split.log")

    # SPLITTER. The background-registration splitter is now the production
    # path. It registers the gutter colour PER PAGE from the modal row-mean
    # instead of voting near-white against near-black, and assembles WEBTOON
    # CDN tiles into one scroll before cutting — a tile is an arbitrary slice,
    # not a page, and per-tile registration produced sliver fragments.
    #
    # Evidence for the swap is the cut arbitration on the pinned Murim ch.43
    # fixture: of 161 cuts the two splitters disagree on, 143 are interior
    # features the legacy splitter was shredding (bubble bands, frame strokes,
    # tonal transitions) and 18 are real gutters this one misses. Better on
    # net, not strictly better — the 18 are a known cost.
    #
    # SPLIT_ENGINE=legacy restores the old subprocess in one env var.
    use_legacy = os.environ.get("SPLIT_ENGINE", "new").lower() == "legacy"
    split_stats = None
    if not use_legacy:
        try:
            import splitlab
            page_files = sorted(
                os.path.join(pages, f) for f in os.listdir(pages)
                if os.path.splitext(f)[1].lower()
                in (".png", ".jpg", ".jpeg", ".webp"))
            n_crops, split_stats = splitlab.split_into(
                page_files, crops, slug=proj_id,
                on_progress=lambda m: progress("split", m, 20))
            with open(split_log, "w", encoding="utf-8") as f_log:
                json.dump(split_stats, f_log, indent=2)
            if not n_crops:
                raise RuntimeError("the splitter produced no panels")
        except Exception as e:
            # Never leave a chapter unsplit because the new path failed:
            # fall back loudly rather than aborting the ingest.
            progress("split", f"new splitter failed ({e}) — using legacy", 19)
            use_legacy = True
            split_stats = None

    if use_legacy:
        with open(split_log, "w", encoding="utf-8") as f_log:
            split_p = subprocess.run(
                [PY, os.path.join(ROOT, "panel-split", "split_panels.py"),
                 "--input", pages, "--out", crops, "--batch"],
                cwd=os.path.join(ROOT, "panel-split"),
                env=subp_env, stdout=f_log, stderr=f_log)
        if split_p.returncode != 0:
            err_text = open(split_log, encoding="utf-8").read()
            if "USAGE CAP EXCEEDED" in err_text:
                raise usage.UsageCapExceeded(err_text.strip().splitlines()[-1])
            raise subprocess.CalledProcessError(split_p.returncode, split_p.args,
                                                "", err_text)
    n_crops = len([f for f in os.listdir(crops) if f.lower().endswith(".png")])
    if not n_crops:
        raise RuntimeError("the splitter produced no panel crops")
    # S2: read the splitter's per-page coverage stats and surface them —
    # a page whose art wasn't fully cropped must be VISIBLE, not a log line.
    split_coverage = None
    if split_stats is not None:
        # The new splitter reports format and per-page panel counts rather
        # than the legacy coverage ratio; surface what it does measure so
        # the record does not go dark.
        split_coverage = {"engine": "background-registration",
                          "format": split_stats.get("format"),
                          "pages": split_stats.get("pages"),
                          "panels": split_stats.get("panels")}
    try:
        pj = json.load(open(os.path.join(crops, "panels.json")))
        covs = [(pg["prefix"], pg.get("coverage", {}).get("coverage_final"))
                for pg in pj.get("pages", []) if pg.get("coverage")]
        vals = [c for _, c in covs if c is not None]
        if vals:
            worst_page, worst = min(covs, key=lambda t: t[1] if t[1] is not None else 1)
            split_coverage = {
                "min": round(min(vals), 3), "mean": round(sum(vals) / len(vals), 3),
                "worst_page": worst_page,
                "pages_below_85": sum(1 for v in vals if v < 0.85),
            }
    except (FileNotFoundError, json.JSONDecodeError, KeyError):
        pass
    # TWO SHAPES LIVE HERE. The legacy splitter reports an art-coverage ratio
    # per page; the background-registration splitter reports format/pages/
    # panels instead, because "coverage" is not a quantity it computes. Reading
    # a legacy-only key unconditionally is what made a successful split fail
    # the whole ingest with KeyError: 'pages_below_85' AFTER every page had
    # already been cut.
    if split_coverage and "pages_below_85" in split_coverage:
        warn = (f" ⚠ {split_coverage['pages_below_85']} page(s) under 85%"
                if split_coverage["pages_below_85"] else "")
        progress("split", f"{n_crops} panel crops · art coverage "
                          f"min {split_coverage['min']:.0%} / "
                          f"mean {split_coverage['mean']:.0%}{warn}", 30)
    elif split_coverage:
        progress("split", f"{n_crops} panel crops · "
                          f"{split_coverage.get('format')} format · "
                          f"{split_coverage.get('pages')} page(s)", 30)
    else:
        progress("split", f"{n_crops} panel crops.", 30)

    # 3. describe ---------------------------------------------------------
    progress("describe", "Describing panels (Gemini vision)…", 35)
    desc_log = os.path.join(proj, "describe.log")
    with open(desc_log, "w", encoding="utf-8") as f_log:
        desc_p = subprocess.run(
            [PY, os.path.join(ROOT, "panel-describe", "run.py"),
             "--input", crops, "--out", desc_path, "--model", "gemini-3.5-flash",
             "--merge"],
            cwd=os.path.join(ROOT, "panel-describe"),
            env=subp_env, stdout=f_log, stderr=f_log)
    if desc_p.returncode != 0:
        err_text = open(desc_log, encoding="utf-8").read()
        if "USAGE CAP EXCEEDED" in err_text:
            raise usage.UsageCapExceeded(err_text.strip().splitlines()[-1])
        raise subprocess.CalledProcessError(desc_p.returncode, desc_p.args,
                                            "", err_text)
    progress("describe", "Descriptions ready.", 55)

    # 4. narrate (write narration FROM the panels) -----------------------
    # script.txt = plain narration (humans, TTS); script.json = the same
    # narration WITH provenance [{scene_id, panel_ids, text}] (B1) so the
    # matcher is constrained to the panels each line was written about
    # instead of reverse-engineering the mapping statistically.
    script_path = os.path.join(proj, "script.txt")
    prov_path = os.path.join(proj, "script.json")
    _cached_script = open(script_path).read().strip() if os.path.exists(script_path) else ""
    scenes = None
    if _cached_script:
        progress("narrate", "Script exists, loading cached narration…", 60)
        script = _cached_script
        if os.path.exists(prov_path):
            try:
                scenes = json.load(open(prov_path))
            except json.JSONDecodeError:
                scenes = None
    else:
        progress("narrate", "Writing narration from panels…", 60)
        panels = narrate.load_panels(desc_path)
        # D2: per-unit progress so a long narrate is visible, not a silent 60%
        script, results = narrate.generate_narration(
            panels, verbose=False,
            progress_cb=lambda i, n, phase: progress(
                "narrate", f"narration {phase} — unit {i}/{n}…",
                60 + int(8 * i / max(n, 1))))
        open(script_path, "w").write(script)
        scenes = narrate.provenance(results)
        json.dump(scenes, open(prov_path, "w"), indent=1)
    if scenes:
        beats = beat_segmenter.segment_beats_scenes(scenes)
    else:
        beats = beat_segmenter.segment_beats(script)
    if not beats:
        # An empty/garbage script must stop the job HERE with a clear error —
        # letting it flow on poisons voice/match (job 4bfca87af66a died at
        # match with an opaque numpy crash caused by exactly this).
        raise RuntimeError(
            "narrate produced 0 beats (empty or unusable script.txt) — "
            "aborting before voice/match")
    progress("narrate", f"{len(beats)} narration beats.", 70)

    # 5. voice (TTS each beat via REST; recompute timeline) --------------
    progress("voice", f"Synthesizing {len(beats)} beats (TTS)…", 72)
    import server as srv  # reuse the REST TTS helper (certifi CA, no SDK)
    t = 0.0
    for i, b in enumerate(beats):
        out = os.path.join(audio, f"beat_{b['index']:03d}.mp3")
        if not os.path.exists(out):
            srv._synth_rest(b["text"], out)
        d = _dur(out)
        b["start"], b["end"] = round(t, 3), round(t + d, 3)
        # E3: scene-aware rhythm — a longer breath at scene boundaries,
        # tighter flow within a scene (flat 0.35s when no provenance).
        nxt = beats[i + 1] if i + 1 < len(beats) else None
        if nxt is not None and "scene_id" in b and "scene_id" in nxt:
            t += d + (0.6 if nxt["scene_id"] != b["scene_id"] else 0.25)
        else:
            t += d + 0.35
        if i % 10 == 0:
            progress("voice", f"beat {i+1}/{len(beats)} · {t:.0f}s", 72 + int(15 * i / len(beats)))
    progress("voice", f"Narration timeline {t:.0f}s.", 88)

    # 6. match (DP aligner) ----------------------------------------------
    progress("match", "Matching beats to panels…", 90)
    pj = json.load(open(desc_path))
    for p in pj:
        if p.get("file") and not os.path.isabs(p["file"]):
            p["file"] = os.path.join(crops, p["file"])
    pj = [p for p in pj if p.get("ok", True) and p.get("width") and p.get("height")]
    assigns, method = matcher.match_beats_to_panels(beats, pj)
    shots = matcher.build_timeline(beats, pj, assigns)
    progress("match", f"{len({s['panel_id'] for s in shots})} distinct panels "
                      f"({method}).", 95)
    # Persist the match method so anyone can verify (via project.json /
    # /api/projects) whether provenance-constrained matching actually ran —
    # not just infer it from the code that was deployed.
    match_method = method

    import shot_planner
    progress("match", "Planning precise shot crops…", 96)
    shots = shot_planner.plan_shots(shots, desc_path, crops)

    # 7. segment ----------------------------------------------------------
    progress("segment", "Building render segments…", 97)
    segs = build_segments(shots)
    for s in segs:
        s["clip"] = f"clips/seg_{s['seg_index']:03d}.mp4"
    json.dump(segs, open(os.path.join(proj, "segments.json"), "w"), indent=2)
    os.makedirs(os.path.join(proj, "clips"), exist_ok=True)
    series, chapter = parse_series_chapter(url)
    series_title = to_title_case(clean_series_slug(series))
    chapter_title = to_title_case(chapter)
    meta = {"id": proj_id, "url": url, "crops": crops, "audio": audio,
            "descriptions": desc_path, "n_segments": len(segs),
            "duration": round(shots[-1]["end"], 1) if shots else 0,
            "series": series_title, "chapter": chapter_title,
            "match_method": match_method,
            "engine": "gemini",
            "split_coverage": split_coverage,
            # Persisted so the whole library stays auditable: a truncated
            # chapter is now visible in project.json / /api/projects instead
            # of only in split.log.
            "n_pages": len(imgs),
            "scrape_warning": scrape_warning,
            # Phase 0 (Session 27): a run that fell back to lexical matching, or
            # a long hold the cap could not split, must be visible in the
            # project record — both used to vanish without trace.
            "embed_fallback_reason": (matcher.EMBED_FALLBACK_REASON[-1]
                                      if getattr(matcher, "EMBED_FALLBACK_REASON", None)
                                      else ""),
            "unsplit_long_holds": list(getattr(matcher, "HOLD_CAP_REPORT", []))}
    json.dump(meta, open(os.path.join(proj, "project.json"), "w"), indent=2)
    progress("segment", f"Done — {len(segs)} segments, {meta['duration']}s.", 100)
    return meta


def _dur(path):
    out = subprocess.check_output(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", path], text=True)
    return float(out.strip())


def _derive_series_chapter(pid, data):
    """Series + chapter titles by the NEW ingest naming convention.

    Prefer the project's stored `url` (authoritative — this is what a fresh
    ingest parses), so legacy projects whose folder id predates the convention
    (e.g. "3") still resolve to a proper name like "Nano Machine" / "Chapter 3".
    Fall back to the composite folder id, then the raw id, if there is no url.
    """
    url = data.get("url")
    if url and url not in ("loaded",) and "/" in url:
        s, c = parse_series_chapter(url)
        return to_title_case(clean_series_slug(s)), to_title_case(c)
    parts = pid.split("_")
    series = to_title_case(clean_series_slug(parts[0]))
    chapter = to_title_case(parts[1]) if len(parts) > 1 else pid
    return series, chapter


def list_projects():
    if not os.path.isdir(PROJECTS):
        return []
    out = []
    for pid in sorted(os.listdir(PROJECTS)):
        pj = os.path.join(PROJECTS, pid, "project.json")
        if not os.path.exists(pj):
            continue
        try:
            data = json.load(open(pj))
        except Exception:
            continue
        # Engine backfill, same self-healing idiom as series/chapter below.
        # Projects built before the engine field existed carry it in their
        # FOLDER NAME ("-lab-"), which is exactly the kind of meaning-encoded-
        # in-a-filename the merge exists to retire. Recorded as a field; the
        # folder is NOT renamed, because the board, clips and exports all
        # reference these paths and a rename would have to be atomic with them.
        if not data.get("engine"):
            data["engine"] = "claude" if "-lab-" in pid else "gemini"
            try:
                json.dump(data, open(pj, "w"), indent=2)
            except OSError:
                pass
        series, chapter = _derive_series_chapter(pid, data)
        # Self-healing backfill: persist the proper names for any legacy project
        # whose stored series/chapter are missing or folder-id-derived, so it's
        # durable and every future read (and the folder grouping) is consistent.
        if data.get("series") != series or data.get("chapter") != chapter:
            data["series"], data["chapter"] = series, chapter
            try:
                with open(pj, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2)
            except Exception:
                pass
        out.append(data)
    return out
