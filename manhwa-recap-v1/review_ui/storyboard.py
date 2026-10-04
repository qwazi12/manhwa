"""Storyboard v2 — THE main review surface (approved combined-table template
+ editor controls + legacy sidebar, per the P1-P13 plan, 2026-07-19).

Every extracted panel in reading order with: OCR, description, script
placement (blue on-screen / yellow folded / red left-out+reason), the real
render timeline, and DIRECT editing: include/exclude checkboxes, per-segment
duration + boundary control, drag-reorder, add narration line, edit + re-TTS,
swap panel, approve/reject. Sidebar ports the legacy UI's Ingest / Logs /
Projects (grouped by series) pages. Cost header is Eastern Time + all-time.
"""
import html
import json
import os
import re
import sys
from datetime import datetime

_RECAP = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
if _RECAP not in sys.path:
    sys.path.insert(0, _RECAP)
import theme                    # ONE palette + rail + theme switch, shared
from shot_planner import (crop_area, crop_status, is_sub_crop,  # SAME contract
                          png_size)                             # as the exporter

# Must match render_segments.TALL_AR — the board previously said "tall strip"
# at AR>=3 while the renderer switched at 2.2, so panels between the two were
# labelled wrongly (Session 25, P2: the board must describe the real path).
TALL_AR = 2.2


_DIM_CACHE = {}


def _real_dims(pdir, pid, fallback_w=0, fallback_h=0):
    """True (w, h) of the panel PNG, cached per render.

    P-dims (Session 25): descriptions.json fed the board's AR/label while
    segments.json fed other code and BOTH drift from the files on disk
    (page020_panel_003_shot_03: recorded 900x2582, real 900x811). The board
    then labelled panels "tall strip" that the renderer treats as ordinary
    cards. The image itself is now the only geometry source; the recorded
    numbers are a fallback for a missing/unreadable file.
    """
    key = (pdir, pid)
    if key not in _DIM_CACHE:
        w, h = png_size(os.path.join(pdir, "crops", f"{pid}.png"))
        _DIM_CACHE[key] = (w, h) if (w and h) else (fallback_w, fallback_h)
    return _DIM_CACHE[key]


def _seg_preview(s, si):
    """The frame the VIDEO will show, plus the reviewer's override controls.

    P2 (Session 25) put the exported crop on the board. P3 makes it
    ACTIONABLE: the badge grades how much of the panel survives, and a
    reviewer who disagrees with the planner can take the whole panel back in
    one click. Below shot_planner.CROP_MIN_AREA the box is not framing at all
    and the full panel renders automatically — the badge says so.
    """
    box = s.get("crop_bbox_norm")
    st = crop_status(box)
    pct = crop_area(box) * 100
    overridden = s.get("focus_source") == "manual_full"
    restorable = s.get("crop_bbox_norm_ai") is not None

    if st == "sub":
        if pct >= 60:
            cls, txt, tip = "ok", f"\u2702 keeps {pct:.0f}%", "most of the panel is kept"
        elif pct >= 30:
            cls, txt, tip = "rev", f"\u2702 keeps {pct:.0f}%", "under two-thirds of the panel is used \u2014 worth a look"
        else:
            cls, txt, tip = "tight", f"\u26a0 keeps {pct:.0f}%", "a small fragment of the panel \u2014 character art is probably lost"
    elif st == "tiny":
        cls, txt = "blocked", f"\u26d4 crop {pct:.0f}% \u2014 too small, full panel used"
        tip = "below the usable floor, so the exporter falls back to the whole panel"
    elif st == "invalid":
        cls, txt, tip = "blocked", "\u26d4 invalid crop \u2014 full panel used", "the stored box is malformed"
    else:
        cls = "full"
        txt = "\u25a3 full panel (your override)" if overridden else "\u25a3 full panel"
        tip = "the whole panel is used"

    acts = ""
    if st in ("sub", "tiny"):
        acts += (f'<button class="cropact" title="render the WHOLE panel for this '
                 f'segment instead of the planner\'s crop \u2014 changes the export too" '
                 f'onclick="useFullPanel({si})">use full panel</button>')
    if restorable:
        acts += (f'<button class="cropact alt" title="put the planner\'s crop back" '
                 f'onclick="restoreCrop({si})">restore crop</button>')

    return (f'<span class="segprev">'
            f'<a href="/segimg/{si}" target="_blank" title="exact exported frame '
            f'(full resolution)"><img src="/thumb/{si}" loading="lazy" alt=""></a>'
            f'<a class="orig" href="/segimg/{si}?full=1" target="_blank" '
            f'title="the original uncropped panel">original \u2197</a>'
            f'<span class="cropb {cls}" title="{tip}">{txt}</span>{acts}</span>')


def _natural(pid):
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", pid)]


def _part_tag(b):
    """V2: when a carve slices one sentence across two segments, both cards
    carry the same text. Label the halves so the board reads as one sentence
    continuing, not the line playing twice (it does not — the audio is split)."""
    p, of = b.get("part"), b.get("part_of")
    return f'<span class="bpart" title="this sentence is split across segments; ' \
           f'only this portion plays here">part {p}/{of}</span> ' if p and of else ""


def _mmss(t):
    return f"{int(t)//60}:{int(t)%60:02d}"


def _load(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def _outdated_stat(n):
    """Header chip: clips built by an older renderer. Non-zero means the
    next APPROVE rebuilds them (visual fixes only reach the video that way)."""
    if not n:
        return '<div class="stat"><b style="color:var(--ok)">0</b>clips outdated</div>'
    return (f'<div class="stat"><b style="color:var(--warn)">{n}</b>'
            f'clips outdated<br><span style="font-size:9px;opacity:.75">'
            f'renderer updated — APPROVE rebuilds them</span></div>')


def _coverage_stat(sc):
    """Header chip for split art-coverage (S2). Amber warning when any page
    lost >15% of its art; green when the whole chapter is fully cropped."""
    if not sc:
        return '<div class="stat"><b>?</b>split coverage</div>'
    bad = sc.get("pages_below_85", 0)
    color = "var(--warn)" if bad else "var(--ok)"
    warn = f' ⚠ {bad} page(s) &lt;85% (worst: {html.escape(str(sc.get("worst_page","")))})' if bad else ""
    return (f'<div class="stat"><b style="color:{color}">'
            f'{sc.get("min", 0):.0%} min / {sc.get("mean", 0):.0%} mean</b>'
            f'split coverage{warn}</div>')


def _et_label():
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("America/New_York")).strftime("%a %b %d, %-I:%M %p ET")
    except Exception:
        return "ET n/a"


def _built_with(meta):
    """One line saying HOW this project was built, so old and new work can be
    told apart at a glance (2026-09-30: the owner could not see which
    improvements a chapter had). Only facts recorded in project.json."""
    import html as _h
    bits = []
    eng = meta.get("engine")
    if eng:
        bits.append(f"engine <b>{_h.escape(str(eng))}</b>")
    if meta.get("variant"):
        bits.append(f"version <b>{_h.escape(str(meta['variant']))}</b>")
    sc = meta.get("split_coverage") or {}
    rf = sc.get("refine") if isinstance(sc, dict) else None
    if rf:
        bits.append(f"cutter refinement: <b>{rf.get('cuts', 0)}</b> missed splits cut, "
                    f"<b>{rf.get('text_slivers_merged', 0)}</b> text strips merged")
    elif sc:
        bits.append("cutter: before the refinement pass")
    ds = meta.get("direct_speech") or {}
    if ds.get("enabled"):
        bits.append(f"direct speech <b>on</b> ({ds.get('lines_quoted', 0)} line(s) quoted, "
                    f"{ds.get('quotes_unapproved', 0)} stray)")
    elif ds:
        bits.append("direct speech off")
    tl = meta.get("timeline") or {}
    if tl.get("checked"):
        bits.append(f"timeline check: <b>{tl.get('errors', 0)}</b> error(s)"
                    + (f", {tl.get('repaired')} repaired" if tl.get("repaired") else ""))
    if not bits:
        return ""
    return ('<div id="builtwith" class="hint" style="margin:0 14px 8px;font-size:11px" '
            'title="Recorded in this project\'s project.json when it was built">🧱 built with: '
            + " · ".join(bits) + "</div>")


def build_storyboard_html(pdir, matcher, review, usage_summary, approved):
    descs = _load(os.path.join(pdir, "descriptions.json"), [])
    descs.sort(key=lambda r: _natural(r["panel_id"]))
    segs = _load(os.path.join(pdir, "segments.json"), [])
    scenes = _load(os.path.join(pdir, "script.json"), [])
    meta = _load(os.path.join(pdir, "project.json"), {})

    seg_by_panel, pos_of = {}, {}
    for pos, s in enumerate(segs):
        seg_by_panel.setdefault(s["panel_id"], []).append(s)
        pos_of[s["seg_index"]] = pos
    unit_of = {}
    for sc in scenes:
        for pid in sc.get("panel_ids", []):
            unit_of[pid] = (sc["scene_id"], sc.get("text", ""))
    # Which images each sentence (beat) actually plays on. "Shared" is a fact
    # about the timeline — one sentence on 2+ images — never about sitting in
    # the same narration unit. (The old rule labelled every image after the
    # first in a unit "shared narration" and hid its own sentence: on ch.44 v2
    # that was ~40 rows, while only ONE sentence was really on two images.)
    beat_panels = {}
    for s_ in segs:
        for b in s_.get("beats", []):
            ps = beat_panels.setdefault(b["index"], [])
            if s_["panel_id"] not in ps:
                ps.append(s_["panel_id"])

    # One rule for "is this in the video", shared with the renderer/exporter:
    # ticked AND not rejected. Stamped per segment so the row controls, the
    # counters and the dead-air chip can never disagree with the export.
    for s in segs:
        _inc_default = not s.get("silent_hold", False)
        s["in_video"] = bool(s["user_included"] if "user_included" in s else _inc_default) and \
            review.get(str(s["seg_index"]), {}).get("status") != "rejected"

    # V1/V5 (Session 23 video review): every card used to show its slot on the
    # MASTER timeline (all segments, included or not). The export contains only
    # in_video segments, so a card's master time drifts from where you actually
    # hear it by the total duration of everything excluded before it. Stamp the
    # real video position here — that is the number a reviewer needs.
    _vt = 0.0
    for s in segs:
        if s.get("in_video"):
            s["video_start"] = round(_vt, 3)
            _vt = round(_vt + s["dur"], 3)
        else:
            s["video_start"] = None
    video_total = _vt

    # V6: a hold cap that only sees one segment cannot catch four consecutive
    # segments sharing one image (31.6s on a single panel in the reviewed cut).
    # Walk the video order and stamp each run's length on its first segment.
    _run = []
    def _flush(run):
        if len(run) > 1:
            span = round(sum(x["dur"] for x in run), 1)
            if span >= 15:
                run[0]["same_panel_run"] = (len(run), span)
    for s in segs:
        if not s.get("in_video"):
            continue
        if _run and _run[-1]["panel_id"] == s["panel_id"]:
            _run.append(s)
        else:
            _flush(_run)
            _run = [s]
    _flush(_run)

    total = (segs[-1]["start"] + segs[-1]["dur"]) if segs else 0
    holds = sum(1 for s in segs if s["dur"] > 12)
    n_included = sum(1 for s in segs if s.get("in_video"))
    n_segs = len(segs)
    try:
        import server as _srv
        n_outdated = len(_srv.needs_render([s for s in segs if s.get("in_video")]))
    except Exception:
        n_outdated = 0
    _sil = [s for s in segs if s.get("in_video")
            and (s.get("silent_hold") or not s.get("beats"))]
    sil_str = (f"{len(_sil)} holds · {sum(s.get('dur', 0) for s in _sil):.0f}s"
               if _sil else "none")
    n_approved = sum(1 for s in segs
                     if review.get(str(s["seg_index"]), {}).get("status") == "approved")

    # FOLDING DIAGNOSTICS. A "folded" row is one the script claimed but the
    # matcher never gave a beat — the panel is in the chapter and in a narration
    # unit, but never reaches the timeline. Counting it makes a starved script
    # visible instead of looking like a rendering choice.
    folded_rows, unplaced_rows = [], []
    scene_panels_count = {}
    for sc in scenes:
        sc_id = sc.get("scene_id")
        pids = [p for p in sc.get("panel_ids", []) if p in seg_by_panel]
        scene_panels_count[sc_id] = len(pids)

    # Phase 0 (Session 27): show HOW this chapter was matched. A run that
    # degraded to lexical token-overlap used to be invisible, and produced a
    # whole chapter of bag-of-words panel assignments (Iron-Blooded).
    _meta = {}
    try:
        _meta = json.load(open(os.path.join(pdir, "project.json")))
    except Exception:
        pass
    _mm = _meta.get("match_method") or "unknown"
    _semantic = "gemini-embeddings" in _mm or _mm.startswith("embeddings")
    _match_short = "semantic" if _semantic else ("lexical" if "lexical" in _mm else _mm)
    _match_col = "var(--ok)" if _semantic else "var(--bad)"
    _match_tip = f"match_method = {_mm}"
    if not _semantic:
        _match_tip += (" — semantic embeddings were NOT used for this chapter, "
                       "so panels were matched on word overlap. Re-ingest to fix.")
    if _meta.get("embed_fallback_reason"):
        _match_tip += f"  Reason: {_meta['embed_fallback_reason']}"
    _unsplit = _meta.get("unsplit_long_holds") or []
    if _unsplit:
        _match_tip += (f"  Also: {len(_unsplit)} long hold(s) exceeded the cap but "
                       f"could not be split (no free panel between).")

    rows = []
    for i, d in enumerate(descs, 1):
        pid = d["panel_id"]
        w, h = _real_dims(pdir, pid, d.get("width") or 0, d.get("height") or 0)
        ar = h / max(w, 1)
        ocr = html.escape((d.get("ocr_text") or "").strip()) or "<i>none</i>"
        vis = html.escape((d.get("visual_description") or "").strip())
        on_screen = pid in seg_by_panel
        reason = matcher.junk_reason(d)
        pid_js = pid.replace("'", "\\'")

        # ---- script placement cell ----------------------------------------
        if on_screen:
            uid = unit_of.get(pid, (None, None))[0]
            own, seen_b = [], set()
            for s_ in seg_by_panel[pid]:
                for b in s_.get("beats", []):
                    if b["index"] not in seen_b:
                        seen_b.add(b["index"])
                        own.append(b)
            btxt = " ".join(b["text"] for b in own)[:300]
            if not btxt:
                script_cell = "<i>on screen as a silent hold (no narration)</i>"
            else:
                label = f'<b class="ln">¶{uid}</b> ' if uid is not None else ""
                shared = []
                for b in own:
                    others = [q for q in beat_panels.get(b["index"], []) if q != pid]
                    if others:
                        part = beat_panels[b["index"]].index(pid) + 1
                        shared.append(f'sentence {b["index"]} spans {len(others) + 1} images '
                                      f'(part {part}) — also on {", ".join(others)}')
                badge = (' <span class="b group" style="background:var(--fold-bg);'
                         'color:var(--fold-ink);padding:1px 5px;border-radius:3px;'
                         f'font-size:10px;" title="{html.escape("; ".join(shared))}">'
                         '↔ shared sentence</span>') if shared else ""
                script_cell = (label + html.escape(btxt)
                               + ("…" if len(btxt) == 300 else "") + badge)
            cls = "sa"
        elif reason:
            script_cell = f"<i>LEFT OUT — {html.escape(reason)}</i>"
            cls = "omit"
        elif pid in unit_of:
            folded_rows.append(pid)
            uid, utxt = unit_of[pid]
            script_cell = (f'<i>→ folded into <b class="ln">¶{uid}</b></i>'
                           f'<div class="unittxt">{html.escape(utxt[:220])}'
                           f'{"…" if len(utxt) > 220 else ""}</div>')
            cls = "fold"
        else:
            unplaced_rows.append(pid)
            script_cell = "<i>unplaced (no provenance, no segment)</i>"
            cls = "gray"


        # ---- timing cell ---------------------------------------------------
        tcells = []
        for s in seg_by_panel.get(pid, []):
            si = s["seg_index"]
            pos = pos_of[si]
            silent = s.get("silent_hold") or not s["beats"]
            if silent:
                motion = "silent hold (no narration)"
            elif is_sub_crop(s.get("crop_bbox_norm")):
                motion = "planned sub-crop + Ken Burns"
            elif ar >= TALL_AR:
                motion = "tall strip → scroll-pan top→bottom"
            else:
                motion = "Ken Burns " + ("push-in" if si % 2 == 0 else "pull-out")
            badges = []
            if s["dur"] > 12:
                badges.append('<span class="b warn">⚠ long hold</span>')
            if ar >= TALL_AR and not is_sub_crop(s.get("crop_bbox_norm")):
                badges.append('<span class="b tall">📜 tall strip</span>')
            if silent:
                badges.append('<span class="b sil">🔇 silent</span>')
            st = review.get(str(si), {}).get("status", "pending")
            if st == "approved":
                badges.append('<span class="b ok">✅ approved</span>')
            elif st == "rejected":
                badges.append('<span class="b user">🗑 rejected</span>')
            run = s.get("same_panel_run")
            if run:
                badges.append(
                    f'<span class="b warn" title="this image stays on screen '
                    f'across {run[0]} consecutive segments — vary the crop, '
                    f'swap a panel in, or shorten it">🖼 same image '
                    f'{run[1]:.0f}s / {run[0]} segs</span>')
            beats = "".join(
                f'<div class="beat"><span class="bt">[{b["start"]-s["start"]:.1f}s]</span> '
                f'{_part_tag(b)}'
                f'{html.escape(b["text"][:160])}{"…" if len(b["text"]) > 160 else ""}'
                f'{"<span class=slice title=\'audio sliced at an image cut\'>✂</span>" if b.get("file") else ""}</div>'
                for b in s["beats"])
            not_last = pos + 1 < len(segs)
            tcells.append(f"""<div class="segblock" draggable="true" data-si="{si}" data-pos="{pos}"
  ondragstart="dragSeg(event)" ondragover="event.preventDefault();this.classList.add('over')"
  ondragleave="this.classList.remove('over')" ondrop="dropSeg(event,this)">
<span class="draghandle" title="drag to reorder">⠿</span>
{_seg_preview(s, si)}
<b>seg #{si}</b> · {(f'{_mmss(s["video_start"])}→{_mmss(s["video_start"]+s["dur"])}' if s.get("video_start") is not None else '<span class="off">not in video</span>')} <span class="mt" title="position on the master timeline (includes segments left out of the video)">[{_mmss(s["start"])}]</span> {' '.join(badges)}
<div class="timectl">
  ⏱ <input type="number" step="0.1" min="0.8" value="{s['dur']:.1f}" id="dur{si}"
     onkeydown="if(event.key==='Enter')setDur({si})"> s
  <button onclick="setDur({si})" title="set on-screen duration">set</button>
  <span class="bctl" title="move the cut between this segment and the next (audio slices if mid-sentence)">
    cut: <button onclick="nudge({si},-1)" {'disabled' if not not_last else ''}>−1s</button>
    <button onclick="nudge({si},-0.25)" {'disabled' if not not_last else ''}>−¼</button>
    <button onclick="nudge({si},0.25)" {'disabled' if not not_last else ''}>+¼</button>
    <button onclick="nudge({si},1)" {'disabled' if not not_last else ''}>+1s</button>
  </span>
</div>
<span class="mo">cut + 0.4s fade-in · {motion} · hard cut out</span>
{beats}
<div class="acts">
  <button onclick="swapPanel({si})">🔄 swap</button>
  <button onclick="editNarr({si})">✏️ edit narration</button>
  <button onclick="addLine({si})">✚ add line</button>
  <button onclick="setStatus({si},'approved')">✅</button>
  <button class="danger" title="DELETE this image slot (undoable)" onclick="delSeg({si})">🗑</button>
  <button class="undobtn" onclick="undoEdit()" disabled
    title="nothing to undo yet">↶</button>
  <button title="duplicate this image as a silent slot you can retime or drag" onclick="dupSeg({si})">⧉</button>
</div></div>""")
        timing_cell = "".join(tcells) or '<i class="off">— not on video timeline —</i>'

        # T3 (Session 23): the checkbox is the USER's inclusion decision for
        # the final video — never auto-checked. The system's proposal is the
        # timing column; the user ticks what actually renders/concats.
        if on_screen:
            row_segs = seg_by_panel.get(pid, [])
            # A row can hold several narrations. "all included" as the tick
            # test made a 5-segment row look EXCLUDED when one of its
            # segments was dropped — and re-ticking it would resurrect that
            # segment. Tick = ANY segment in the video; a partial row is
            # shown indeterminate with an n/m badge so it never misleads.
            _in_video = [s for s in row_segs if s.get("in_video")]
            _inc = " checked" if _in_video else ""
            _partial = 0 < len(_in_video) < len(row_segs)
            _badge = (f'<span class="part" title="narrations from this panel that '
                      f'are in the final video">{len(_in_video)}/{len(row_segs)}</span>'
                      if _partial else "")
            include_ctl = (
                f'<label class="inc" title="tick = include this panel\'s narrations in the FINAL video">'
                f'<input type="checkbox"{_inc}{" data-partial=1" if _partial else ""} '
                f'onchange="setIncluded(\'{pid_js}\',this.checked,this)"></label>{_badge}')
        else:
            include_ctl = (
                f'<button class="promote" title="give this panel its own slot on the timeline" '
                f'onclick="toggleInclude(\'{pid_js}\',null,{1 if cls=="omit" else 0})">➕</button>')

        rows.append(f"""<tr class="{cls}" id="row_{pid}">
<td class="n">{include_ctl}{i}<br><span class="pid">{pid}</span><br><span class="dim">{w}&times;{h} (AR {ar:.1f})</span></td>
<td class="img"><a href="/panelimg/{pid}" target="_blank"><img src="/panelimg/{pid}" loading="lazy"></a></td>
<td class="ocr">{ocr}</td><td class="vis">{vis}</td>
<td class="script">{script_cell}</td>
<td class="timing" data-pid="{pid}" ondragover="rowOver(event,this)"
  ondragleave="this.classList.remove('dropok')" ondrop="dropRow(event,this)">{timing_cell}</td></tr>""")

    # Units stretched over many panels: one line holding several images is
    # exactly where a recap stalls, and it is the same root cause as folding.
    wide_units = [uid for uid, n in scene_panels_count.items() if n > 3]

    title = f"{meta.get('series','?')} Ch.{meta.get('chapter','?')}"
    u = usage_summary or {}
    life = u.get("lifetime", {})
    all_g = life.get("gemini_calls", 0) + u.get("gemini_calls", 0)
    all_t = life.get("tts_chars", 0) + u.get("tts_chars", 0)
    all_c = life.get("est_cost_usd", 0) + u.get("est_cost_usd", 0)
    mm = meta.get("match_method", "")
    return f"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<link rel="manifest" href="/manifest.webmanifest" crossorigin="use-credentials">
<meta name="theme-color" content="#141824">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="Recap Studio">
<link rel="apple-touch-icon" href="/app-icon/180.png">
<title>{html.escape(title)} — storyboard: story + render plan</title>
<style>
/* ---------------------------------------------------------------- palette
   One token set, identical in name and value to review_page.py, so the board
   and /review finally share a palette instead of two hand-copied ones. The
   board used to be a LIGHT page (#fafafa) wearing dark chrome — the rail,
   header, pipebar and drawers were already dark — which is what made /review
   feel like a separate application. Everything below now reads from these
   tokens; re-theming is editing this block, not hunting 169 literals.
   color-scheme:dark also makes native checkboxes, scrollbars and date pickers
   render dark, which CSS alone cannot do. */
{theme.TOKENS_CSS}{theme.CONTROLS_CSS}{theme.SIDE_CSS}{theme.PANEL_CSS}{theme.JOBS_CSS}
body {{ font-family: -apple-system, Helvetica, sans-serif; margin: 0 0 0 var(--side-w); background:var(--bg); transition:margin-left .2s; color:var(--ink); }}
header {{ position: sticky; top:0; z-index:5; background:var(--top-bg); color:var(--ink); padding:10px 18px; display:flex; gap:16px; align-items:center; flex-wrap:wrap; border-bottom:1px solid var(--rule); }}
header .stat b {{ display:block; font-size:16px; font-weight:700; color:var(--ink); line-height:1.1; text-transform:none; letter-spacing:normal; }} header .stat {{ font-size:10px; color:var(--ink3); text-transform:uppercase; letter-spacing:.5px; }}
.usage {{ font-size:11px; color:var(--ok); line-height:1.5; }}
#approveBtn {{ margin-left:auto; background:var(--cta); border:1px solid var(--cta);
  color:var(--cta-ink); padding:11px 18px; border-radius:8px; font-weight:800;
  font-size:14px; cursor:pointer; box-shadow:var(--glow); letter-spacing:.01em; }}
#approveBtn:hover {{ background:var(--cta-hover); border-color:var(--cta-hover); }}
#approveBtn.on {{ background:var(--ok-cta); border-color:var(--ok-cta);
  color:var(--ok-cta-ink); box-shadow:none; }}
#pipebar {{ position:sticky; top:52px; z-index:49; background:var(--panel2); color:var(--ink2); font-size:12px;
  display:flex; gap:18px; align-items:center; padding:7px 18px; border-bottom:1px solid var(--rule); }}
#pipebar b {{ color:var(--ink); }}
#renderprog {{ flex:1; display:flex; align-items:center; gap:10px; }}
#renderbar {{ height:8px; background:var(--ok); border-radius:4px; width:0%; min-width:2px; transition:width .5s;
  box-shadow:0 0 8px rgba(57,192,127,.6); }}
#rendertxt {{ color:var(--ok); white-space:nowrap; }}
/* ---- navigation: the shared Scrapper-style sidebar (theme.SIDE_CSS) ---- */
/* ---- validation badges + findings ---- */
.vbadge {{ display:inline-block; margin-top:4px; padding:1px 6px; border-radius:9px;
  font-size:10px; font-weight:700; cursor:pointer; border:1px solid transparent; }}
.vbadge.high {{ background:var(--bad); color:#fff; }}
.vbadge.medium {{ background:var(--warn); color:#1a1205; }}
.vbadge.low {{ background:var(--panel2); color:var(--ink3); border-color:var(--rule); }}
tr.vflag td.n {{ box-shadow:inset 3px 0 0 var(--bad); }}
tr.vflag-medium td.n {{ box-shadow:inset 3px 0 0 var(--warn); }}
tr.vflag-low td.n {{ box-shadow:inset 3px 0 0 var(--rule); }}
.vfind {{ border:1px solid var(--rule); border-left-width:3px; border-radius:7px;
  padding:7px 9px; margin-bottom:7px; background:var(--panel2); font-size:12px; }}
.vfind.high {{ border-left-color:var(--bad); }}
.vfind.medium {{ border-left-color:var(--warn); }}
.vfind.low {{ border-left-color:var(--rule); opacity:.8; }}
.vfind .vh {{ display:flex; gap:6px; align-items:baseline; margin-bottom:3px; }}
.vfind .vcol {{ color:var(--ink3); font-size:10px; text-transform:uppercase;
  letter-spacing:.5px; }}
.vfind a.vrow {{ font-weight:700; text-decoration:none; }}
.vfind .vfix {{ color:var(--ok); margin-top:3px; }}
.vfind .vsrc {{ color:var(--ink3); font-size:10px; margin-top:3px; }}
.vmode {{ display:flex; gap:5px; margin:8px 0; }}
.vmode button {{ flex:1; font-size:11px; padding:6px 4px; }}
.vmode button.on {{ background:var(--accent); color:var(--cta-ink); border-color:var(--accent); }}
/* Category, confidence and scene are three different facts about a finding and
   are deliberately not merged into one badge: what kind of problem, how sure
   the checker is, and where in the chapter it sits. */
.vfind .vcat {{ font-size:10px; text-transform:uppercase; letter-spacing:.5px;
  color:var(--ink3); }}
.vfind .vconf {{ font-size:10px; color:var(--ink3); margin-left:auto; }}
.vfind .vscene {{ font-size:10px; color:var(--ai); }}
.vfind.accepted {{ opacity:.65; }}
.vfind .vcorr {{ color:var(--warn); }}
.vgroup {{ font-size:11px; font-weight:700; text-transform:uppercase;
  letter-spacing:.6px; margin:14px 0 6px; padding-bottom:3px;
  border-bottom:1px solid var(--rule); }}
.vgroup.high {{ color:var(--bad); }}
.vgroup.medium {{ color:var(--warn); }}
.vgroup.low {{ color:var(--ink3); }}
/* Actions are the point of the drawer, so they are full-contrast controls,
   not subdued links. Client-only actions (jump, open image) are ghosted so the
   ones that CHANGE the board read as the weightier choice. */
.vacts {{ display:flex; flex-wrap:wrap; gap:5px; margin-top:8px; }}
.vact {{ font-size:11px; padding:4px 8px; border-radius:6px;
  background:var(--btn); color:var(--btn-ink); border:1px solid var(--btn-edge);
  cursor:pointer; }}
.vact:hover {{ background:var(--btn-hover); }}
.vact.ghost {{ background:transparent; color:var(--ink3); }}
.vact:disabled {{ opacity:.6; cursor:default; }}
.vstale {{ margin-top:8px; padding:8px 10px; border-radius:7px;
  background:var(--warnb-bg); color:var(--warnb-ink); font-size:11.5px; }}
.vstale button {{ margin-top:6px; }}
.vchapter {{ border:1px solid var(--rule); border-radius:7px; padding:8px 10px;
  margin-bottom:10px; background:var(--panel2); font-size:11.5px; }}
.vchapter summary {{ cursor:pointer; color:var(--ink2); font-weight:600; }}
.vscenerow {{ color:var(--ink3); margin-top:4px; }}
.vscenerow.vreveal {{ color:var(--ai); }}
.labdiag {{ margin-top:8px; padding:8px 10px; border-radius:7px;
  background:var(--panel2); border:1px solid var(--rule); }}
.labdiag .tl {{ margin-top:8px; }}
.vrecheck {{ display:flex; gap:6px; align-items:center; font-size:11px;
  color:var(--ink3); margin-top:8px; }}
/* ---- the running indicator ----
   The checker can run for a minute or more. Before this, the only sign it was
   working was a line of grey text that also looked like every other line of
   grey text, so "is it running?" was a guess. State is now carried by COLOUR
   and a moving bar, not by wording. */
.vstate {{ border:1px solid var(--rule); border-left:3px solid var(--rule);
  border-radius:8px; padding:9px 11px; margin:2px 0 4px; background:var(--panel2); }}
.vsrow {{ display:flex; align-items:center; gap:8px; }}
.vspill {{ font-size:11px; font-weight:800; letter-spacing:.04em;
  text-transform:uppercase; padding:2px 9px; border-radius:10px;
  background:var(--rule); color:var(--ink2); white-space:nowrap; }}
.vsmode {{ font-size:11px; color:var(--ink3); margin-left:auto;
  text-align:right; }}
.vsstage {{ font-size:11.5px; color:var(--ink2); margin-top:6px;
  min-height:1.3em; }}
.vsbar {{ height:6px; border-radius:3px; background:var(--rule); margin-top:7px;
  overflow:hidden; display:none; }}
.vsbar > i {{ display:block; height:100%; width:0%; border-radius:3px;
  background:var(--accent); transition:width .4s ease; }}
.vstate.idle {{ opacity:.75; }}
.vstate.queued {{ border-left-color:var(--warn); }}
.vstate.queued .vspill {{ background:var(--warn); color:#1a1205; }}
.vstate.running {{ border-left-color:var(--accent); background:var(--sa-bg); }}
.vstate.running .vspill {{ background:var(--accent); color:var(--accent-ink); }}
.vstate.running .vsbar {{ display:block; }}
.vstate.done {{ border-left-color:var(--ok); }}
.vstate.done .vspill {{ background:var(--ok-cta); color:var(--ok-cta-ink); }}
.vstate.error {{ border-left-color:var(--bad); background:var(--badb-bg); }}
.vstate.error .vspill {{ background:var(--bad-cta); color:var(--bad-cta-ink); }}
/* An indeterminate bar for the stages that cannot report a fraction, so the
   bar never sits frozen at 0% looking stalled. */
.vstate.running .vsbar.indet > i {{ width:35%; animation:vslide 1.1s infinite ease-in-out; }}
@keyframes vslide {{ 0% {{ margin-left:-35%; }} 100% {{ margin-left:100%; }} }}
.vspin {{ display:inline-block; width:10px; height:10px; border-radius:50%;
  border:2px solid var(--accent); border-top-color:transparent;
  animation:vspin .8s linear infinite; }}
@keyframes vspin {{ to {{ transform:rotate(360deg); }} }}
/* The rail button carries the same state, so a run is visible with the drawer
   shut — otherwise you have to open Check to find out Check is busy. */
.navbtn .vdot {{ position:absolute; left:28px; top:8px; margin:0; width:8px; height:8px;
  border-radius:50%; background:var(--accent); box-shadow:0 0 0 2px var(--panel);
  animation:vpulse 1.2s infinite; }}
.navbtn .vdot.error {{ background:var(--bad); animation:none; }}
.navbtn .vdot.done {{ background:var(--ok); animation:none; }}
@keyframes vpulse {{ 50% {{ opacity:.35; }} }}
/* The phone layout lives at the END of this stylesheet (search "PHONE").
   It used to sit here, ahead of the base rules it was meant to override, so
   the later .drawer / td / #rail rules silently won and phones got the
   desktop layout squeezed into 390px. */
@media (prefers-reduced-motion: reduce) {{
  .vspin, .navbtn .vdot, .vstate.running .vsbar.indet > i {{ animation:none; }}
}}
/* The row a finding points at, flashed after a jump. Scrolling a 138-row table
   to the right place is no use if you cannot tell which row it stopped on. */
tr.vfocus td {{ background:var(--sa-bg) !important; transition:background .3s; }}
/* ---- exports: identified by manhwa + chapter, not by filename ---- */
.exprow {{ border-bottom:1px solid var(--rule); padding:9px 0; }}
.exphead {{ display:flex; align-items:baseline; gap:7px; flex-wrap:wrap; }}
.exptitle {{ font-size:13.5px; font-weight:800; color:var(--ink);
  text-decoration:none; letter-spacing:-.01em; }}
.exptitle:hover {{ color:var(--accent); text-decoration:underline; }}
.expch {{ font-size:11px; font-weight:700; color:var(--accent-ink);
  background:var(--accent); border-radius:9px; padding:1px 7px; }}
.expopen {{ font-size:10px; font-weight:700; color:var(--ok-cta-ink);
  background:var(--ok-cta); border-radius:9px; padding:1px 6px; }}
.expverdict {{ font-size:10px; color:var(--ink3); border:1px solid var(--rule);
  border-radius:9px; padding:1px 6px; }}
.expdel {{ margin-left:auto; background:none; border:1px solid var(--rule);
  color:var(--bad); border-radius:3px; cursor:pointer; font-size:11px;
  padding:1px 6px; }}
.expmeta {{ font-size:11px; color:var(--ink3); margin-top:3px; }}
.expfile {{ font-size:10px; opacity:.7; overflow:hidden; text-overflow:ellipsis;
  white-space:nowrap; }}
.expreview {{ display:inline-block; margin-top:5px; font-size:11px;
  font-weight:600; }}
/* ---- the experiment tab ---- */
.navbtn.navtest .ic {{ filter:saturate(1.3); }}
.navbtn.navtest {{ color:var(--ai); }}
.drawer.wide {{ width:520px; }}
.expbanner {{ background:var(--tall-bg); color:var(--tall-ink);
  border:1px solid var(--ai); border-radius:7px; padding:7px 10px;
  font-size:11px; font-weight:600; margin-bottom:9px; line-height:1.45; }}
.expbanner code {{ background:transparent; color:var(--ai); }}
.cmprow {{ display:grid; grid-template-columns:1fr 72px 72px 58px; gap:6px;
  align-items:baseline; padding:5px 0; border-bottom:1px solid var(--rule-soft,var(--rule));
  font-size:11.5px; }}
.cmphead {{ font-weight:700; color:var(--ink3); font-size:10px;
  text-transform:uppercase; letter-spacing:.5px; }}
.cmpnum {{ text-align:right; font-variant-numeric:tabular-nums; }}
.cmpwin {{ font-size:10px; font-weight:700; text-align:center; border-radius:9px;
  padding:1px 5px; }}
.cmpwin.claude {{ background:var(--ai-cta); color:var(--ai-ink); }}
.cmpwin.baseline {{ background:var(--ok-cta); color:var(--ok-cta-ink); }}
.cmpwin.tie {{ background:var(--rule); color:var(--ink2); }}
.cmpwin.none {{ color:var(--ink3); }}
.cmpnote {{ grid-column:1/-1; color:var(--ink3); font-size:10.5px;
  margin-top:2px; }}
.cmpscore {{ display:flex; gap:8px; margin:10px 0; }}
.cmpscore div {{ flex:1; text-align:center; border:1px solid var(--rule);
  border-radius:8px; padding:8px; }}
.cmpscore b {{ display:block; font-size:19px; }}
.cmpcaveat {{ font-size:10.5px; color:var(--ink3); margin-top:3px;
  padding-left:12px; text-indent:-12px; }}
.trow {{ border-bottom:1px solid var(--rule); padding:7px 0; font-size:11.5px; }}
.trow .tn {{ font-weight:700; color:var(--accent); }}
.trow .tl {{ color:var(--ink3); font-size:10px; text-transform:uppercase;
  letter-spacing:.4px; margin-top:4px; }}
.tside {{ display:grid; grid-template-columns:1fr 1fr; gap:8px; margin-top:3px; }}
.tside > div {{ background:var(--panel2); border-radius:6px; padding:5px 7px; }}
.tside .th {{ font-size:9.5px; text-transform:uppercase; letter-spacing:.5px;
  color:var(--ink3); margin-bottom:2px; }}
/* ---- drawers ---- */
.drawer {{ position:fixed; left:var(--side-w); top:86px; bottom:0; width:360px; background:var(--panel); color:var(--ink); border-right:1px solid var(--rule); z-index:70; padding:16px; overflow-y:auto; display:none; font-size:13px; box-shadow:4px 0 18px rgba(0,0,0,.5); }}
.drawer h3 {{ font-size:12px; text-transform:uppercase; letter-spacing:.6px; color:var(--ink3); margin:0 0 10px; }}
.drawer .hint {{ color:var(--ink3); font-size:11px; }}
.drawer input.field {{ width:100%; background:var(--panel2); border:1px solid var(--rule); border-radius:7px; color:var(--ink); padding:8px; font:inherit; margin:10px 0; }}
.drawer button {{ border-radius:7px; padding:6px 10px; }}
.drawer button.primary {{ width:100%; }}
.drawer .section {{ border-top:1px solid var(--rule); margin-top:12px; padding-top:12px; }}
.projcard {{ margin-top:8px; background:var(--panel2); border:1px solid var(--rule); border-radius:8px; overflow:hidden; }}
.projcard .ph {{ font-weight:600; font-size:12px; padding:8px 12px; background:var(--panel); border-bottom:1px solid var(--rule); }}
.projrow {{ display:flex; justify-content:space-between; align-items:center; padding:6px 10px; font-size:12px; }}
.wrap {{ padding: 14px 18px; }}
h1 {{ font-size: 19px; margin: 8px 0; }} p.meta {{ color:var(--ink2); max-width: 1200px; font-size: 13px; }}
table {{ border-collapse: collapse; width: 100%; }}
th, td {{ border: 1px solid var(--rule); padding: 8px; vertical-align: top; text-align: left; }}
th {{ background: var(--panel2); color: var(--ink); position: sticky; top: 58px; z-index: 2; }}
td.n {{ width: 54px; font-weight: 700; }} .pid {{ font-weight:400; font-size:10px; color:var(--ink3); word-break:break-all; }}
.dim {{ font-size:10px; color:var(--ink3); }}
.inc {{ display:block; margin-bottom:4px; }} .inc input {{ width:16px; height:16px; cursor:pointer; accent-color:var(--accent); }}
td.img {{ width: 185px; }} td.img img {{ max-width: 175px; max-height: 320px; object-fit: contain; border-radius:4px; box-shadow:0 1px 4px rgba(0,0,0,.55); }}
td.ocr {{ width: 11%; font-size: 11px; color:var(--ink2); }}
td.vis {{ width: 16%; font-size: 12px; }}
td.script {{ width: 21%; font-size: 12px; }}
td.timing {{ width: 26%; font-size: 12px; }}
tr.sa td.script {{ background:var(--sa-bg); }} .ln {{ color:var(--sa-ink); }}
tr.fold td.script {{ background:var(--fold-bg); color:var(--fold-ink); }} .unittxt {{ color:var(--fold-ink); font-size:11px; margin-top:4px; }}
tr.omit td.script {{ background:var(--omit-bg); color:var(--omit-ink); }}
tr.gray td.script {{ background:var(--gray-bg); color:var(--gray-ink); }}
/* display:flow-root contains the floated .segprev. Without it a preview
   taller than the card's text — which happens as soon as it carries badges
   like "original", "keeps 38%" or "crop 12% - too small" — escapes the card
   entirely and lands on top of the NEXT segment. flow-root rather than
   overflow:hidden because hidden would clip those same badges. */
.segblock {{ background:var(--seg-bg); border:1px solid var(--seg-rule); border-radius:6px; padding:6px; margin-bottom:6px; position:relative; display:flow-root; }}
.segprev {{ float:right; width:118px; margin:0 0 4px 8px; text-align:center; }}
.segprev img {{ width:118px; max-height:150px; object-fit:contain; border-radius:4px;
  background:var(--panel2); box-shadow:0 1px 4px rgba(0,0,0,.5); display:block; }}
.segprev .orig {{ display:block; font-size:10px; color:var(--ink3); text-decoration:none; margin-top:2px; }}
.segprev .orig:hover {{ text-decoration:underline; }}
.cropb {{ display:block; font-size:10px; margin-top:2px; padding:1px 4px; border-radius:3px;
  background:var(--panel2); color:var(--ink2); }}
.cropb.ok {{ background:var(--okb-bg); color:var(--okb-ink); }}
.cropb.rev {{ background:var(--warnb-bg); color:var(--warnb-ink); }}
.cropb.tight {{ background:var(--tight-bg); color:var(--tight-ink); font-weight:600; }}
.cropb.blocked {{ background:var(--badb-bg); color:var(--badb-ink); font-weight:700; }}
.cropb.full {{ background:var(--panel2); color:var(--ink3); }}
.cropact {{ display:block; width:100%; margin-top:3px; font-size:10px; padding:3px 4px;
  border-color:var(--warn); color:var(--warn); background:transparent; font-weight:700;
  border-radius:3px; }}

.cropact.alt {{ color:var(--ink3); border-color:var(--rule); font-weight:600; }}
.segblock.over {{ outline:2px dashed var(--accent); }}
td.timing.dropok {{ outline:3px dashed var(--ok); outline-offset:-3px; background:var(--okb-bg); }}
.segblock[draggable] {{ cursor:grab; }}
.part {{ display:block; font-size:10px; color:var(--warn); font-weight:700; }}
.editrow {{ display:flex; gap:6px; margin-bottom:8px; }}
.editrow textarea {{ flex:1; min-height:60px; }}
.delline {{ align-self:flex-start; }}
.hint {{ color:var(--ink2); font-size:12px; max-width:640px; }}
.draghandle {{ position:absolute; right:6px; top:6px; cursor:grab; color:var(--ink3); }}
.timectl {{ margin:5px 0; font-size:11px; display:flex; gap:6px; align-items:center; flex-wrap:wrap; }}
.timectl input {{ width:52px; font:inherit; padding:2px 4px; background:var(--panel2); color:var(--ink); border:1px solid var(--rule); border-radius:4px; }}
.timectl button {{ font-size:10px; padding:2px 6px; border-radius:4px; }}
.bctl {{ white-space:nowrap; }}
.mo {{ color:var(--ink3); font-size:11px; }} .beat {{ margin-top:4px; }} .bt {{ color:var(--sa-ink); font-size:10px; font-weight:600; }}
.slice {{ color:var(--warn); margin-left:4px; }}
.b {{ font-size:10px; padding:1px 6px; border-radius:8px; }}
.b.warn {{ background:var(--warnb-bg); color:var(--warnb-ink); }} .b.tall {{ background:var(--tall-bg); color:var(--tall-ink); }}
.b.user {{ background:var(--badb-bg); color:var(--badb-ink); font-weight:700; }} .b.ok {{ background:var(--okb-bg); color:var(--okb-ink); }}
.bpart {{ background:var(--panel2); color:var(--ink2); border-radius:3px; padding:0 4px;
  font-size:9px; font-weight:700; letter-spacing:.3px; }}
.mt {{ color:var(--ink3); font-size:10px; }}
.b.sil {{ background:var(--panel2); color:var(--ink3); }}
.off {{ color:var(--ink3); }}
/* clear:both keeps the action row BELOW the preview. Flowing beside a tall
   float squeezed it into two ragged rows ("swap / edit narration" then
   "add line / approve / delete"), which read as the controls colliding. */
.acts {{ margin-top:6px; display:flex; gap:5px; flex-wrap:wrap; clear:both; }}
.acts button {{ font-size:11px; padding:4px 7px; border-radius:5px; }}

#cands {{ position:fixed; inset:0; background:rgba(0,0,0,.65); display:none; overflow:auto; padding:30px; z-index:30; }}
#cands .inner {{ background:var(--panel); color:var(--ink); border-radius:10px; padding:16px; max-width:1100px; margin:0 auto; }}
#cands img {{ max-width:150px; max-height:240px; margin:6px; cursor:pointer; border:3px solid transparent; border-radius:4px; }}
#cands img:hover {{ border-color:var(--accent); }}
dialog {{ border:1px solid var(--rule); border-radius:10px; padding:18px; width:640px; background:var(--panel); color:var(--ink); box-shadow:0 20px 60px rgba(0,0,0,.6); }}
dialog::backdrop {{ background:rgba(0,0,0,.6); }}
textarea, input[type=text], input[type=number], select {{ background:var(--panel2); color:var(--ink); border:1px solid var(--rule); border-radius:5px; }}
textarea {{ width:100%; min-height:110px; font:13px/1.5 -apple-system; padding:6px; }}
a {{ color:var(--accent); }}
#busy {{ position:fixed; bottom:16px; left:50%; transform:translateX(-50%); background:var(--panel2); color:var(--ink); border:1px solid var(--rule); padding:8px 16px; border-radius:8px; display:none; z-index:40; font-size:12px; }}
/* ---- sections are PAGES (Scrapper logic), not overlays ----
   The section containers keep their old ids (d_ingest, d_logs…) so every
   loader still finds its elements; they simply render in the main area as a
   titled card, one at a time, and the board page hides while one is open. */
.drawer, .drawer.wide {{ position:static; left:auto; top:auto; bottom:auto; width:auto;
  margin:16px 24px; padding:0 16px 16px; background:var(--panel); color:var(--ink);
  border:1px solid var(--rule); border-radius:10px; box-shadow:none; overflow:visible;
  z-index:auto; font-size:13px; }}
.drawer > h3:first-of-type {{ margin:0 -16px 14px; padding:11px 16px; background:var(--panel2);
  border-bottom:1px solid var(--rule); font-size:12px; letter-spacing:.8px; color:var(--ink3); }}
.drawer .hint {{ font-size:12px; }}
.calbar {{ display:flex; gap:10px; align-items:center; flex-wrap:wrap; margin-bottom:10px; }}
.drawer .calbar button.primary {{ width:auto; }}
.calchips {{ display:flex; gap:6px; flex-wrap:wrap; align-items:center; margin-bottom:8px; }}
.calchips button {{ font-size:11px; padding:2px 9px; background:transparent; box-shadow:none; font-weight:600; }}
.calchips button.on {{ background:var(--btn); color:var(--ink); }}
.calgrid {{ display:grid; grid-template-columns:repeat(auto-fill, minmax(150px, 1fr)); gap:10px; margin-top:6px; }}
.calmonth {{ grid-column:1/-1; font-size:12px; font-weight:700; border-bottom:1px solid var(--rule);
  padding-bottom:4px; margin-top:8px; color:var(--ink); }}
.calcard {{ background:var(--seg-bg); border:1px solid var(--rule); border-radius:8px; padding:8px;
  display:flex; flex-direction:column; gap:4px; min-width:0; }}
.calcard b {{ font-size:12px; line-height:1.3; }}
.calcover {{ width:100%; aspect-ratio:3/4; object-fit:cover; border-radius:6px; background:var(--btn); display:block; }}
.calmeta {{ font-size:11px; color:var(--ink3); }}
.calst {{ font-size:11px; }} .calst.made {{ color:var(--ok); }} .calst.new {{ color:var(--accent); }}
.calcard button {{ font-size:11px; margin-top:auto; }}
.calpick {{ width:100%; font-size:12px; padding:5px 6px; margin-top:2px; }}
.voicebox {{ border:1px solid var(--rule); border-radius:8px; padding:10px 12px; margin:10px 0 12px;
  background:var(--panel2); display:flex; flex-direction:column; gap:8px; }}
.voicebox .vlabel {{ font-size:11px; font-weight:700; text-transform:uppercase; letter-spacing:.6px; color:var(--ink3); }}
.voicebox .vrow {{ display:flex; gap:8px; flex-wrap:wrap; align-items:center; }}
.voicebox select {{ flex:1 1 200px; min-width:0; padding:6px 8px; }}
body.not-board #v_board {{ display:none; }}
/* off the board, the status bar shows the chapter's numbers only (as the
   Scrapper's StatusBar does); the board's tools stay on the board page */
body.not-board header button, body.not-board header label {{ display:none !important; }}
.jobsbar {{ margin:12px 24px 0; }}
.wrap {{ padding:16px 24px; }}
.boardpanel table {{ border-style:hidden; }}
#sentback {{ margin:12px 24px 0 !important; }}
/* ================================================================ PHONE
   Last in the sheet on purpose, so it wins over every base rule above with
   the same specificity. Phones (<=760px, the Scrapper studio's breakpoint) get:
   - the sidebar as a slide-out menu behind a ☰ in a slim top bar (theme.SIDE_CSS)
   - the header as a swipeable stat strip, not a sticky wall of numbers
   - APPROVE as a full-width bar pinned above the tabs
   - each panel row as a card: image beside its narration, timing below
   - drawers and dialogs as full-screen sheets
   - 40px+ touch targets and 16px inputs (iOS zooms anything smaller) */
@media (max-width: 760px) {{
  :root {{ --tabbar: env(safe-area-inset-bottom, 0px); }}
  body {{ margin:0; padding-bottom:calc(var(--tabbar) + 76px);
          -webkit-text-size-adjust:100%; }}

  /* header -> one swipeable strip of stats and tools */
  header {{ position:static; flex-wrap:nowrap; overflow-x:auto; gap:14px;
            padding:10px 12px; scrollbar-width:none; }}
  header::-webkit-scrollbar {{ display:none; }}
  header > * {{ flex:0 0 auto; }}
  header .usage {{ white-space:nowrap; }}
  #approveBtn {{ position:fixed; left:10px; right:10px; bottom:calc(var(--tabbar) + 8px);
                 margin:0; z-index:79; padding:12px; font-size:14px; border-radius:12px; }}

  #pipebar {{ position:static; overflow-x:auto; white-space:nowrap; gap:14px; padding:8px 12px;
              scrollbar-width:none; }}
  #pipebar::-webkit-scrollbar {{ display:none; }}
  #pipebar > * {{ flex:0 0 auto; }}
  #renderprog {{ min-width:220px; }}

  .wrap {{ padding:10px 8px; }}
  .boardpanel {{ background:transparent; border:none; overflow:visible; }}
  .boardpanel > .panel-h {{ border:1px solid var(--rule); border-radius:10px 10px 0 0; }}
  details.legend {{ background:var(--panel); border:1px solid var(--rule); border-top:none;
                    border-radius:0 0 10px 10px; margin-bottom:12px; }}

  /* table -> one card per panel */
  table, tbody, tr, td {{ display:block; width:auto; }}
  table tr:first-child {{ display:none; }}
  tr {{ display:grid; grid-template-columns:112px minmax(0,1fr);
        grid-template-areas:"n n" "img script" "ocr ocr" "vis vis" "timing timing";
        border:1px solid var(--rule); border-radius:12px; margin:0 0 12px; padding:0;
        background:var(--panel); overflow:hidden; }}
  td {{ border:none; padding:8px 10px; min-width:0; }}
  td.n {{ grid-area:n; width:auto; display:flex; align-items:center; gap:10px; flex-wrap:wrap;
          border-bottom:1px solid var(--rule); background:var(--panel2); }}
  td.n .inc {{ margin:0; }}
  td.n .inc input {{ width:24px; height:24px; }}
  td.img {{ grid-area:img; width:auto; padding-right:0; }}
  td.img img {{ max-width:100%; max-height:220px; }}
  td.script {{ grid-area:script; width:auto; font-size:14px; line-height:1.45; }}
  td.ocr {{ grid-area:ocr; width:auto; }}
  td.vis {{ grid-area:vis; width:auto; }}
  td.timing {{ grid-area:timing; width:auto; border-top:1px solid var(--rule); }}
  /* OCR and description: two lines each until tapped */
  td.ocr, td.vis {{ font-size:12px; color:var(--ink2); padding-top:0; padding-bottom:0; margin:5px 0;
                    display:-webkit-box; -webkit-line-clamp:2; -webkit-box-orient:vertical;
                    overflow:hidden; cursor:pointer; }}
  td.ocr.open, td.vis.open {{ display:block; }}
  td.ocr::before, td.vis::before, td.script::before, td.timing::before {{
    display:block; font-size:9px; letter-spacing:.08em; text-transform:uppercase;
    color:var(--ink3); margin-bottom:3px; font-weight:700; }}
  td.ocr::before {{ content:"Bubble text"; }}
  td.vis::before {{ content:"What the AI saw"; }}
  td.script::before {{ content:"Narration"; }}
  td.timing::before {{ content:"On screen"; }}

  /* segment cards */
  .segblock {{ padding:10px; border-radius:10px; }}
  .segprev {{ width:92px; }}
  .segprev img {{ width:92px; max-height:130px; }}
  .draghandle {{ display:none; }}
  .timectl {{ gap:6px; font-size:12px; }}
  .timectl input {{ width:64px; min-height:38px; font-size:16px; }}
  .timectl button {{ min-height:38px; min-width:42px; font-size:13px; }}
  .bctl {{ white-space:normal; display:flex; flex-wrap:wrap; gap:6px; align-items:center; }}
  .acts {{ gap:6px; }}
  .acts button {{ flex:1 1 auto; min-height:42px; font-size:13px; padding:6px 10px; }}

  /* drawers, pickers and dialogs -> full-screen sheets */
  .drawer, .drawer.wide {{ margin:10px 8px; padding:0 12px 14px; font-size:14px; }}
  .drawer > h3:first-of-type {{ margin:0 -12px 12px; padding:11px 12px; }}
  body.not-board header {{ display:none; }}
  body.not-board {{ padding-bottom:calc(var(--tabbar) + 16px); }}
  .jobsbar {{ margin:10px 8px 0; }}
  #sentback {{ margin:10px 8px 0 !important; }}
  .drawer .hint {{ font-size:12.5px; }}
  .drawer button {{ min-height:42px; }}
  .projrow {{ padding:10px; gap:8px; }}
  #cands {{ padding:10px 6px calc(var(--tabbar) + 10px); }}
  #cands .inner {{ padding:12px; }}
  #cands img {{ max-width:46%; max-height:200px; margin:2%; }}
  dialog {{ width:calc(100vw - 20px); max-width:none; max-height:86vh; padding:14px; }}
  dialog textarea {{ font-size:16px; }}
  #busy {{ bottom:calc(var(--tabbar) + 72px); }}

  /* touch sizes; 16px stops iOS zooming into a field */
  input, select, textarea {{ font-size:16px; }}
  button, select {{ min-height:40px; }}
  .mini {{ min-height:36px; }}
}}
@media (max-width: 380px) {{
  tr {{ grid-template-columns:92px minmax(0,1fr); }}
  td.img img {{ max-height:180px; }}
}}
</style>{theme.HEAD_THEME_JS}{theme.HEAD_SIDE_JS}</head><body>
{theme.sidebar_html(theme.nav_items('board', f"{n_included}/{n_segs} in video"), foot_rows=[("Spend today", "~$%.2f" % u.get("est_cost_usd", 0)), ("All-time", "~$%.2f" % all_c)])}
  <!-- ARCHIVED 2026-09-23. Both features graduated into Ingest: the Claude
       engine is now an engine choice, and the background-registration
       splitter is the production split path. The drawers and their routes
       remain so existing runs stay openable; only the rail entries are gone.
       Re-enable by restoring these two buttons.
  <button class="navbtn navtest" data-d="test" onclick="toggleDrawer('test')"><span class="ic">🧪</span>TEST</button>
  <button class="navbtn" data-d="split" onclick="toggleDrawer('split')"><span class="ic">✂️</span>Split</button>
  -->
<style>
.term {{ background:#0b0f0d; color:#d6e2da; border:1px solid var(--rule); border-radius:8px; padding:10px 12px;
         font:11.5px/1.65 ui-monospace,SFMono-Regular,Menlo,monospace; height:52vh; overflow-y:auto; }}
.term .t {{ color:#7e8f85; }} .term .k {{ color:#8fb8ff; }} .term .ok {{ color:#5fd39a; }}
.term .warn {{ color:#f0b35a; }} .term .error {{ color:#f08a80; }}
.jgrp {{ margin:12px 0 4px; font-size:11px; letter-spacing:.08em; text-transform:uppercase; color:var(--ink3); font-weight:700; }}
.spendcard {{ border:1px solid var(--rule); border-radius:8px; padding:10px 12px; margin-bottom:10px; background:var(--panel); }}
.meter {{ height:8px; background:var(--rule); border-radius:4px; overflow:hidden; margin:4px 0; }}
.meter i {{ display:block; height:100%; background:var(--accent); }}
.daybars {{ display:flex; align-items:flex-end; gap:3px; height:70px; margin-top:6px; }}
.daybars i {{ flex:1; background:var(--accent); border-radius:2px 2px 0 0; min-height:1px; }}
</style>
<div class="drawer" id="d_ingest">
  <style>
  .apcard {{ border:1px solid var(--rule); border-radius:10px; padding:12px 14px; margin:0 0 18px; background:var(--panel); }}
  .apcard .aph {{ display:flex; gap:8px; align-items:center; flex-wrap:wrap; margin-bottom:8px; }}
  .apcard .aph b {{ font-size:14px; }}
  .apcard .aph button {{ width:auto; flex:0 0 auto; margin:0; }}
  .apcard .apr {{ display:grid; grid-template-columns:120px 1fr; gap:3px 12px; font-size:12px; }}
  .apcard .apr > span:nth-child(odd) {{ color:var(--ink3); }}
  .apser {{ display:flex; gap:8px; align-items:flex-start; justify-content:space-between; border-top:1px solid var(--rule); padding:6px 0; font-size:12px; }}
  .apser .r {{ display:flex; gap:4px; flex-shrink:0; }}
  @media (max-width:560px) {{ .apcard .apr {{ grid-template-columns:1fr; }} .apser {{ flex-wrap:wrap; }} }}
  </style>
  <div id="apcard" class="apcard hint">loading autopilot…</div>
  <h3>Ingest a chapter</h3>
  <div class="hint">Paste a chapter URL — it runs the whole pipeline
    (scrape → split → describe → narrate → voice → match → segment) and this
    board reloads on it when done. Clips render on demand after approval.</div>
  <input class="field" id="ingurl" placeholder="https://…/chapter/…"/>
  <label class="hint" style="display:flex;gap:6px;align-items:center;margin-bottom:8px">
    <input type="checkbox" id="ingfresh"> Fresh re-ingest (regenerate script,
    audio &amp; timeline — for re-running a chapter after a pipeline fix)</label>
  <label class="hint" style="display:flex;gap:6px;align-items:center;margin-bottom:8px">
    Engine
    <select id="ingengine" style="flex:0 0 auto">
      <option value="gemini" selected>Gemini (default)</option>
      <option value="claude">Claude</option>
    </select>
    <span style="opacity:.75">Gemini is the established path. Claude is
      opt-in per ingest and recorded on the project.</span></label>
  <label class="hint" style="display:flex;gap:8px;align-items:center;margin:6px 0"
    title="Build this chapter as a separate project beside the original (e.g. v2 -> 'Chapter 44 (v2)'). The original project is not touched. Leave empty for the chapter's own project.">
    Save as version <input class="field" id="ingvariant" placeholder="e.g. v2" maxlength="16"
      style="flex:0 0 90px"/>
    <input type="checkbox" id="ingdirect"> Direct speech
    <span style="opacity:.75">(2–3 pivotal lines quoted, in the narrator's own voice — Gemini only)</span></label>
  <div class="voicebox">
    <div class="vlabel">Narrator voice</div>
    <div class="vrow">
      <select id="ingvoice" onchange="voiceChanged()"><option>loading voices…</option></select>
      <select id="ingstyle"></select>
    </div>
    <div class="vrow">
      <button type="button" onclick="previewVoice()" id="vprevbtn">▶ Preview</button>
      <button type="button" onclick="saveDefaultVoice()" id="vdefbtn">Make this the default</button>
      <span class="hint" id="voicestatus"></span>
    </div>
    <audio id="vprev" controls preload="none" style="display:none;width:100%;margin-top:6px"></audio>
    <div class="hint">Used when this chapter is first voiced, or with Fresh re-ingest. A chapter
      that already has a voice keeps it, so a video never switches narrator. Chapters started
      from the Tracker use the default.</div>
  </div>
  <button class="primary" onclick="runIngest()">▶ Run ingest</button>
  <div id="ingprog" style="margin-top:12px"></div>
</div>
<div class="drawer" id="d_validate">
  <h3>Pre-review check</h3>
  <div class="hint">Validates the chapter as a story, not row by row.
    <b>Rules</b> is free and instant (flash panels, dead air, story-order jumps,
    credit pages, missing descriptions, narration with no panel).
    <b>+ Claude</b> reads the whole chapter first — scenes, reveals, turns —
    then checks every row against its place in that sequence, and separately
    checks whether the descriptions themselves can be trusted.
    <b>+ vision</b> opens the real panel art for flagged rows AND for a sample
    of rows nothing flagged, which is the only way to catch a description that
    is wrong but plausible. Flagged rows get a badge on the board, and every
    finding carries buttons that fix it here.</div>
  <div id="vstate" class="vstate idle">
    <div class="vsrow"><span class="vspill">Not running</span>
      <span class="vsmode"></span></div>
    <div class="vsstage"></div>
    <div class="vsbar"><i></i></div>
  </div>
  <div class="vmode">
    <button id="vm_rules" onclick="setVMode('rules')">Rules<br>free</button>
    <button id="vm_text" onclick="setVMode('text')">+ Claude<br>text</button>
    <button id="vm_full" onclick="setVMode('full')">+ vision<br>full</button>
  </div>
  <button class="primary" onclick="runValidation()">Run check</button>
  <label class="vrecheck"><input type="checkbox" id="vrecheck">
    Re-run the check automatically after applying a fix</label>
  <div id="vstatus" class="hint" style="margin-top:10px">loading…</div>
  <div id="vfindings" style="margin-top:10px"></div>
</div>
<div class="drawer wide" id="d_test">
  <h3>🧪 TEST LAB — an independent Claude pipeline</h3>
  <div class="expbanner">EXPERIMENTAL · this is its OWN system. Paste a chapter
    link and it builds the whole chapter from scratch — its own download, its
    own panels, its own reading, script and sequencing — into its own project.
    It never touches your main chapters.</div>
  <div class="hint">Claude does the judgement work: cutting pages into panels
    (optional), reading the text off each panel, describing it, choosing the
    crop framing, writing the narration, and deciding which line plays over
    which panel and in what order. Only the page download and the voice are
    shared with the main system. When it finishes you get a normal project —
    open it on the board, tick, approve, export, watch.</div>

  <input class="field" id="laburl" placeholder="https://…/chapter/…"/>
  <div class="hint" style="margin:-4px 0 8px">Who cuts the pages into panels?</div>
  <div class="vmode">
    <button id="lb_claude" onclick="setLabSplit('claude')">Claude<br>cuts panels</button>
    <button id="lb_yolo" onclick="setLabSplit('yolo')">YOLO<br>cuts panels</button>
  </div>
  <div id="teststate" class="vstate idle">
    <div class="vsrow"><span class="vspill">Not running</span>
      <span class="vsmode"></span></div>
    <div class="vsstage"></div>
    <div class="vsbar"><i></i></div>
  </div>
  <button class="primary" style="width:100%;margin-top:10px"
    onclick="runLab()">Build this chapter with Claude</button>
  <label class="vrecheck"><input type="checkbox" id="labfresh">
    Start over (re-download and re-cut, ignoring anything already built)</label>

  <div id="testinfo" class="hint" style="margin-top:10px">loading…</div>
  <div id="testbody"></div>
</div>
<div class="drawer" id="d_projects">
  <h3>Projects</h3>
  <div id="projlist" class="hint">loading…</div>
</div>
<div class="drawer" id="d_split">
  <h3>SPLIT LAB</h3>
  <div class="hint" style="margin-bottom:8px">Preview how a chapter cuts into panels, before spending anything.
  Scraping and splitting cost <b>no</b> Gemini/TTS/Claude credit.</div>
  <div style="display:flex;flex-direction:column;gap:5px;margin-bottom:9px">
    <input id="spUrl" placeholder="chapter URL (Asura or WEBTOON)">
    <div style="display:flex;gap:5px">
      <select id="spProj" style="flex:1"><option value="">— or an ingested project —</option></select>
      <label class="hint" style="display:inline-flex;gap:3px;align-items:center;cursor:pointer"
             title="also write a bubble-blurred copy of each panel"><input type="checkbox" id="spBlurRun"> blur bubbles</label>
      <button class="primary" onclick="spRun()">Split</button>
      <button id="spStop" class="mini danger" onclick="spStop()" disabled title="stop this split">■</button>
    </div>
  </div>
  <div id="spstate" class="hint" style="margin-bottom:8px"></div>
  <div style="display:flex;gap:6px;flex-wrap:wrap;margin-bottom:8px;font-size:12px">
    <select id="spPick" onchange="spShow(this.value)"><option value="">— past runs —</option></select>
    <label class="hint" style="display:inline-flex;gap:3px;align-items:center;cursor:pointer">
      <input type="checkbox" id="spTall" onchange="spPaint()"> only taller than 3:1</label>
    <label class="hint" style="display:inline-flex;gap:3px;align-items:center;cursor:pointer"
           title="show the blurred copies where they exist">
      <input type="checkbox" id="spBlurView" onchange="spPaint()"> show blurred</label>
  </div>
  <div id="spmeta" class="hint" style="margin-bottom:8px"></div>
  <div id="spgrid" style="display:flex;flex-wrap:wrap;gap:8px"></div>
</div>
<div class="drawer" id="d_tracker">
  <h3>TRACKER</h3>
  <div class="segtabs">
    <button type="button" id="tks" onclick="trkTab('series')">📚 Series</button>
    <button type="button" id="tkw" onclick="trkTab('watch')">✏️ Manage list</button>
  </div>

  <!-- ============ SERIES BOARD: the release calendar + watchlist, one card per series -->
  <div id="trk_series">
    <div class="calbar">
      <button type="button" class="primary" id="calcheck" onclick="calCheckAll()">↻ Check all series</button>
      <button type="button" class="mini" onclick="bibleResearchAll()" title="research every series that has no cast list yet (~$0.05 each)">📖 research missing</button>
      <span class="hint" id="calnote">Reads each series page for new chapters, dates and covers — no AI cost.</span>
    </div>
    <div class="calchips" id="sbfilters">
      <span class="hint">Show:</span>
      <button type="button" class="on" data-f="all" onclick="sbFilter('all', this)">All</button>
      <button type="button" data-f="ready" onclick="sbFilter('ready', this)">Next up for autopilot</button>
      <button type="button" data-f="new" onclick="sbFilter('new', this)">New since last made</button>
      <button type="button" data-f="attn" onclick="sbFilter('attn', this)">Needs you</button>
      <button type="button" data-f="paused" onclick="sbFilter('paused', this)">Paused</button>
      <button type="button" data-f="done" onclick="sbFilter('done', this)">Caught up</button>
    </div>
    <div class="calchips"><span class="hint">Sort:</span>
      <button type="button" class="on" data-s="release" onclick="sbSort('release', this)">Latest release</button>
      <button type="button" data-s="plan" onclick="sbSort('plan', this)">Plan order (tier, rank)</button>
    </div>
    <div class="calgrid" id="sbgrid"><div class="hint">loading…</div></div>
  </div>

  <!-- ============ WATCHLIST: what to make next, before any money is spent -->
  <div id="trk_watch" style="display:none">
    <div class="hint" style="margin-bottom:8px">One row per story, however many sites carry it.
    Add a title here <em>before</em> ingesting it, then pick a source and a chapter.</div>
    <details style="margin-bottom:8px">
      <summary class="mini" style="cursor:pointer">+ add a title</summary>
      <div style="display:flex;flex-direction:column;gap:5px;margin-top:6px">
        <input id="wlTitle" placeholder="Title (e.g. The Stellar Swordmaster)">
        <input id="wlUrl" placeholder="Series URL (optional — any supported site)">
        <div style="display:flex;gap:5px">
          <select id="wlTier" style="flex:1">
            <option value="greenlight">Make now</option>
            <option value="high_upside">Next up</option>
            <option value="watchlist" selected>Watching</option>
          </select>
          <button class="primary" onclick="wlAdd()">Add</button>
        </div>
        <div class="hint" style="font-size:11px">A title with no source yet is fine — that is
        "we want this, we have not found where to read it".</div>
      </div>
    </details>
    <div style="display:flex;gap:5px;flex-wrap:wrap;margin-bottom:8px;font-size:12px">
      <input id="wlQ" placeholder="search title / alias / keyword" style="flex:1;min-width:130px"
             oninput="wlRender()">
      <select id="wlTierF" onchange="wlRender()" title="tier">
        <option value="">all tiers</option><option value="greenlight">greenlight</option>
        <option value="high_upside">high-upside</option><option value="watchlist">watchlist</option>
      </select>
      <select id="wlSrcF" onchange="wlRender()" title="source"><option value="">all sources</option></select>
      <select id="wlSort" onchange="wlRender()" title="sort">
        <option value="tier">by tier</option><option value="backlog">by backlog</option>
        <option value="new">by unmade chapters</option><option value="title">by title</option>
      </select>
      <label class="hint" style="display:inline-flex;gap:3px;align-items:center;cursor:pointer">
        <input type="checkbox" id="wlOnlyIng" onchange="wlRender()"> ingestable only</label>
      <button class="mini" onclick="wlSeed()" title="load the demand research as a starting watchlist">⤓ seed research</button>
    </div>
    <div id="wllist" class="hint">loading…</div>
  </div>

</div>
<div class="drawer" id="d_exports">
  <h3>EXPORTS — final videos</h3>
  <div class="hint" style="margin-bottom:8px">Every exported MP4 for the active project. Click to watch / download.</div>
  <div id="exportlist">loading…</div>
</div>
<div class="drawer" id="d_settings">
  <h3>⚙️ SETTINGS &amp; CHANNELS</h3>
  <style>
  .setgrid {{ display:grid; grid-template-columns:repeat(auto-fit, minmax(280px, 1fr)); gap:10px; }}
  .setcard {{ border:1px solid var(--rule); border-radius:10px; padding:10px 12px; background:var(--panel); font-size:12.5px; }}
  .setcard h4 {{ margin:0 0 6px; font-size:13px; }}
  .setcard .r {{ display:flex; justify-content:space-between; gap:8px; border-bottom:1px dashed var(--rule); padding:3px 0; }}
  .setcard .r:last-child {{ border-bottom:0; }}
  .setcard .r span:first-child {{ color:var(--ink3); }}
  </style>
  <div id="setbox" class="setgrid"><div class="hint">loading…</div></div>
</div>
<div class="drawer" id="d_logs">
  <h3 style="margin-bottom:4px">LOGS &amp; ACTIVITY <span id="logstamp" class="hint" style="font-weight:400;font-size:11px"></span></h3>
  <div class="segtabs">
    <button type="button" id="lt_live" class="on" onclick="logsTab('live')">🔴 Live</button>
    <button type="button" id="lt_jobs" onclick="logsTab('jobs')">⏳ Jobs</button>
    <button type="button" id="lt_spend" onclick="logsTab('spend')">💰 Spend</button>
    <button type="button" id="lt_work" onclick="logsTab('work')">🗒 What changed</button>
  </div>
  <div id="logs_live">
    <div class="calchips" id="livef" style="margin-bottom:6px"><span class="hint">Show:</span>
      <button type="button" class="on" data-k="" onclick="liveFilter('', this)">All</button>
      <button type="button" data-k="autopilot" onclick="liveFilter('autopilot', this)">Autopilot</button>
      <button type="button" data-k="ingest" onclick="liveFilter('ingest', this)">Ingest</button>
      <button type="button" data-k="render" onclick="liveFilter('render', this)">Render</button>
      <button type="button" data-k="research" onclick="liveFilter('research', this)">Research</button>
      <button type="button" data-k="!error" onclick="liveFilter('!error', this)">Errors</button>
      <input id="liveq" placeholder="search" oninput="livePaint()" style="width:110px">
    </div>
    <div id="livefeed" class="term">loading…</div>
  </div>
  <div id="logs_work" style="display:none">
    <div class="hint" style="margin-bottom:8px">Every change made to the system, newest first — what changed, why,
    what was tested, and the evidence (cut sheets, listening clips, before/after tables). It is read from the
    project log that ships with each deploy, so it always matches what is live. <span id="workstamp"></span></div>
    <div id="worklist" style="font-size:12px">loading…</div>
  </div>
  <div id="logs_jobs" style="display:none">
  <div class="hint" style="margin-bottom:6px">Every ingest, autopilot chapter, research, render and export. Pause and stop
  land at the next step (between pipeline stages, or between clips).</div>
  <div style="display:flex;gap:8px;align-items:center;margin-bottom:8px;font-size:12px;flex-wrap:wrap">
    <label style="cursor:pointer"><input type="checkbox" id="joball" onchange="toggleAllJobs(this)"> select all</label>
    <button id="jobstopbtn" onclick="bulkJobs('stop')" disabled class="mini">⏹ stop selected (<span id="jobseln">0</span>)</button>
    <button id="jobdelbtn" onclick="bulkJobs('delete')" disabled class="mini">🗑 delete selected</button>
    <button class="mini" onclick="loadLogs()">↻ refresh</button>
  </div>
  <div id="joblist">loading…</div>
  </div>
  <div id="logs_spend" style="display:none">
    <div id="spendbox">loading…</div>
    <div class="section"><h3>Every call</h3><div id="logusage" class="hint">loading…</div></div>
  </div>
</div></div>
</div>
<header>
  <div class="stat"><b>{html.escape(title)}</b>storyboard</div>
  <div class="stat"><b>{len(descs)}</b>panels extracted</div>
  <div class="stat"><b>{len(segs)}</b>segments</div>
  <div class="stat"><b>{_mmss(video_total)}</b>video runtime</div>
  <div class="stat"><b>{holds}</b>holds &gt;12s</div>
  <div class="stat" title="{html.escape(_match_tip)}"><b style="color:{_match_col}">{html.escape(_match_short)}</b>matching</div>
  <div class="stat"><b>{n_approved}</b>approved</div>
  <div class="stat"><b>{n_included}/{n_segs}</b>in final video</div>
  <button onclick="setIncludedAll(true)" class="mini">☑ all</button>
  <button onclick="setIncludedAll(false)" class="mini">☐ none</button>
  <div class="stat"><b>{html.escape(mm) or "?"}</b>match method</div>
  {_coverage_stat(meta.get("split_coverage"))}
  <div class="usage" id="usagebox" title="click for the rate card">{_et_label()} — today: {u.get("gemini_calls", 0)} gemini · {u.get("tts_chars", 0)} tts · ~${u.get("est_cost_usd", 0):.2f}<br>
  all-time: {all_g} gemini · {all_t} tts · ~${all_c:.2f}</div>
  {_outdated_stat(n_outdated)}
  <label class="mini" style="display:inline-flex;gap:4px;align-items:center;cursor:pointer"
         title="rebuild EVERY ticked clip, even ones that already exist">
    <input type="checkbox" id="forceAll"> re-render all</label>
  <button class="mini" onclick="repairTimeline()"
    title="fix the timing faults that block a render: swapped or overlapping slices, beats outside their segment, shared sentences, and audio longer than its window">🔧 repair timeline</button>
  <button id="approveBtn" class="{'on' if approved else ''}" onclick="toggleApproval()">
    {'✔ APPROVED — click to re-render &amp; re-export' if approved else 'APPROVE PROJECT FOR RENDER'}</button>
</header>
<div id="jobsbar" class="jobsbar"></div>
<div id="views"></div>
<script>
// Section pages live in the main column, under the status bar and jobs bar.
document.querySelectorAll('.drawer').forEach(function (d) {{
  document.getElementById('views').appendChild(d);
}});
</script>
<section id="v_board">
<div id="sentback" style="display:none;margin:0 14px 10px;padding:11px 14px;border-radius:8px;
  background:var(--warnb-bg);border-left:3px solid var(--warn);color:var(--warnb-ink);font-size:13px"></div>
{_built_with(meta)}
<div id="pipebar">
  <span id="st_tick">① ticked <b>{n_included}/{n_segs}</b></span>
  <span id="st_appr">② approved <b>{'✓' if approved else '—'}</b></span>
  <span id="st_clips">③ clips <b id="clipcount">?</b></span>
  <span id="st_exp">④ export <b id="expstate">—</b></span>
  <span id="st_sil" style="{'color:var(--warn)' if _sil else ''}">🔇 dead air <b>{sil_str}</b></span>
  <span id="st_fold" style="{'color:var(--warn)' if folded_rows else 'color:var(--ink3)'}"
    title="Panels a narration unit claimed but that never received a beat, so they never reach the timeline. A high count means the script is too coarse for the number of panels — the fix is finer narration units, not the matcher.">📎 folded <b>{len(folded_rows)}</b></span>
  <span id="st_unpl" style="{'color:var(--bad)' if unplaced_rows else 'color:var(--ink3)'}"
    title="Panels with no narration provenance and no segment — not on the video timeline at all.">🚫 not on timeline <b>{len(unplaced_rows)}</b></span>
  <span id="st_wide" style="{'color:var(--warn)' if wide_units else 'color:var(--ink3)'}"
    title="Narration units covering more than three panels. One line held over many images is where a recap stalls.">🧵 wide units <b>{len(wide_units)}</b></span>
  <div id="renderprog" style="display:none"><div id="renderbar"></div><span id="rendertxt"></span>
    <button id="renderstop" class="danger mini" onclick="stopRender()" title="stop after the clip currently rendering">⏹ Stop</button></div>
</div>
<div class="wrap">
<section class="panel boardpanel">
<div class="panel-h"><h2>Storyboard — {len(descs)} panels · {n_included} of {len(segs)} segments in the video · {_mmss(video_total)}</h2></div>
<details class="legend"><summary>How to read this board</summary>
<p class="meta">Left half: system OCR/description and where each extracted panel lands in the script
(<b style="color:var(--sa-ink)">blue</b> carries narration unit ¶N on screen · <b style="color:var(--fold-ink)">yellow</b> folded — its
story is told in ¶N while another panel holds the screen · <b style="color:var(--omit-ink)">red</b> LEFT OUT, with the junk
filter's reason). Right column: the renderer's real timeline with LIVE EDITING — ✔ checkbox puts a panel on/off the
final video (folded panels get a slice of their unit's window; script-less panels get a silent hold), ⏱ sets a
segment's on-screen duration, "cut" buttons move the boundary between neighbours (narration audio slices seamlessly
if a cut lands mid-sentence ✂), ⠿ drag a seg card onto ANOTHER ROW to play that narration over that panel (shift-drop also moves it there in the story; card-on-card still reorders), ✚ adds a new narrated line (TTS). 🗑 rejects a segment (takes it OUT of the final video; ✅ puts it back — nothing is deleted). Badges: ⚠ hold &gt;12s ·
📜 tall strip (scroll-pan) · 🔇 silent hold · ✅/🗑 review status. Approving the project unlocks bulk rendering.</p>
</details>
<table>
<tr><th>#</th><th>Panel</th><th>System OCR</th><th>System description</th><th>Script placement</th><th>On-screen timing &amp; motion</th></tr>
{''.join(rows)}
</table></section></div>
</section>
<div id="cands" onclick="this.style.display='none'"><div class="inner" onclick="event.stopPropagation()"><h3>Pick replacement panel</h3><div id="candList"></div></div></div>
<dialog id="bibleDlg" style="max-width:760px;width:94vw"><div id="bibleBody">loading…</div>
<p style="display:flex;gap:6px;flex-wrap:wrap"><button onclick="bibleResearch()">↻ research again</button>
<button onclick="bibleEdit()">✏️ edit</button><button id="bibleSave" style="display:none" onclick="bibleSaveEdit()">save edits</button>
<button onclick="bibleDlg.close()">close</button></p></dialog>
<dialog id="editDlg"><h3>Edit narration</h3>
<p class="hint">One box per spoken line. Edit the text and Save to re-voice just
that line (costs its TTS characters). 🗑 removes the line from the video
entirely — its audio file is kept, so re-adding the same sentence is free.</p>
<div id="editRows"></div>
<p><button onclick="saveEdit()">Save changes</button>
<button onclick="editDlg.close()">Cancel</button></p></dialog>
<script>document.querySelectorAll('input[data-partial]').forEach(function(c){{c.indeterminate=true;}});</script>
<script>
// Phone: the legend, bubble text and AI description are clamped to a few
// lines; a tap opens them. Desktop shows them in full, so this does nothing.
document.addEventListener('click', function (e) {{
  if (!window.matchMedia('(max-width: 760px)').matches) return;
  var el = e.target.closest && e.target.closest('td.ocr, td.vis');
  if (el && !e.target.closest('a, button, input')) el.classList.toggle('open');
}});
</script>
<div id="busy">working…</div>
<script>
let editing = null, dragFrom = null;
const APPROVED = {str(bool(approved)).lower()};
function busy(on, msg) {{ const b = document.getElementById('busy'); b.textContent = msg || 'working…'; b.style.display = on ? 'block' : 'none'; }}
async function j(u, opt) {{ const r = await fetch(u, opt); if (!r.ok) {{ let m = await r.text(); try {{ m = JSON.parse(m).detail || m; }} catch(e) {{}} throw new Error(m); }} return r.json(); }}
async function post(u, body, msg) {{
  busy(true, msg);
  try {{ const r = await j(u, {{method:'POST', headers:{{'Content-Type':'application/json'}}, body: JSON.stringify(body)}}); location.reload(); return r; }}
  catch (e) {{ busy(false); alert(e.message); }}
}}
/* ---- editor ops ---- */
function toggleInclude(pid, cb, isJunk) {{
  /* promote a folded/left-out panel onto the timeline (its checkbox starts
     UNTICKED — the user still decides final-video inclusion, T3) */
  let hold = 2.5;
  if (isJunk) {{
    const v = prompt('This panel has no narration — it gets a SILENT hold (extends runtime). Seconds on screen:', '2.5');
    if (v === null) return;
    hold = parseFloat(v) || 2.5;
  }}
  post('/api/storyboard/include', {{panel_id: pid, hold}}, 'placing panel on the timeline…');
}}
async function setIncluded(pid, on, el) {{
  // NO page reload. post() reloads on success, so ticking several boxes in a
  // row reloaded the page out from under you and threw away the clicks that
  // had not posted yet — which looked like boxes unticking themselves.
  if (el) el.disabled = true;
  try {{
    const r = await j('/api/storyboard/set_included',
      {{method:'POST', headers:{{'Content-Type':'application/json'}},
        body: JSON.stringify({{panel_id: pid, included: on}})}});
    if (typeof r.included === 'number') {{
      const t = document.querySelector('#st_tick b');
      if (t) t.textContent = r.included + '/' + (t.textContent.split('/')[1] || '');
    }}
  }} catch (e) {{
    if (el) el.checked = !on;            // the server said no — show the truth
    alert(e.message);
  }} finally {{
    if (el) el.disabled = false;
  }}
}}
function setIncludedAll(on) {{
  post('/api/storyboard/set_included', {{all: true, included: on}}, 'updating all…');
}}
function setDur(si) {{
  const v = parseFloat(document.getElementById('dur' + si).value);
  if (!v || v <= 0) return alert('enter seconds');
  post('/api/storyboard/duration', {{seg_index: si, dur: v}}, 'retiming…');
}}
function nudge(si, delta) {{ post('/api/storyboard/boundary', {{seg_index: si, delta}}, 'moving cut…'); }}
function useFullPanel(si) {{
  if (!confirm('Render the WHOLE panel for segment ' + si + '? This changes the exported video too, not just this preview.')) return;
  post('/api/storyboard/use_full_panel', {{seg_index: si}}, 'switching to full panel…');
}}
function restoreCrop(si) {{ post('/api/storyboard/restore_crop', {{seg_index: si}}, 'restoring planner crop…'); }}
function addLine(si) {{
  const t = prompt('New narration sentence (costs its TTS characters):');
  if (t && t.trim()) post('/api/storyboard/addline', {{seg_index: si, text: t.trim()}}, 'synthesizing…');
}}
function dragSeg(ev) {{ dragFrom = parseInt(ev.target.closest('.segblock').dataset.si); }}
function dropSeg(ev, el) {{
  ev.stopPropagation();               // a card-on-card drop REORDERS...
  el.classList.remove('over');
  const to = parseInt(el.dataset.pos);
  if (dragFrom === null || isNaN(to)) return;
  post('/api/storyboard/move', {{seg_index: dragFrom, to}}, 'reordering…');
}}
/* ...while a drop anywhere else in a row REASSIGNS the segment to that row's
   panel: the narration and its timing stay put, only the artwork changes.
   Shift-drop ALSO moves it to that panel's place in the story. */
function rowOver(ev, el) {{
  if (dragFrom === null) return;
  ev.preventDefault();
  el.classList.add('dropok');
}}
function dropRow(ev, el) {{
  ev.preventDefault();
  el.classList.remove('dropok');
  const pid = el.dataset.pid;
  if (dragFrom === null || !pid) return;
  const also = ev.shiftKey;
  post('/api/storyboard/assign',
       {{seg_index: dragFrom, panel_id: pid, move_here: also}},
       also ? 'moving here + re-sequencing…' : 'placing on this panel…');
}}
/* ---- existing controls ---- */
async function delSeg(i) {{
  if (!confirm('Delete segment #' + i + '?\\n\\nThe image slot is removed. If its sentence is shared with sibling images, the narration is kept and moves to a sibling; if this segment owns the sentence outright, that narration leaves the video too.\\n\\nUndo is available.')) return;
  const r = await post('/api/storyboard/delete', {{seg_index: i}}, 'deleting…');
}}
function dupSeg(i) {{
  post('/api/storyboard/duplicate', {{seg_index: i}}, 'duplicating…');
}}
async function swapPanel(i) {{
  const c = await j(`/api/segments/${{i}}/candidates`);
  const list = document.getElementById('candList');
  list.innerHTML = '';
  for (const p of (c.candidates || [])) {{
    const im = document.createElement('img');
    im.src = `/panelimg/${{encodeURIComponent(p.panel_id)}}`; im.title = p.panel_id;
    im.onclick = () => post(`/api/segments/${{i}}/panel`, {{panel_id: p.panel_id}}, 'swapping…');
    list.appendChild(im);
  }}
  document.getElementById('cands').style.display = 'block';
}}
async function editNarr(i) {{
  editing = i;
  const d = await j('/api/project');
  const s = (d.segments || []).find(x => x.seg_index === i);
  const rows = document.getElementById('editRows');
  rows.innerHTML = '';
  // One row per beat. The old dialog joined every beat into a single box and
  // saved the result back into beat[0], so edits to (or deletions of) the
  // 2nd/3rd line silently did nothing and their original audio kept playing.
  for (const b of (s.beats || [])) {{
    const row = document.createElement('div');
    row.className = 'editrow';
    row.innerHTML = `<textarea data-bi="${{b.index}}"></textarea>
      <button class="delline" title="remove this line from the video"
              onclick="delLine(${{b.index}})">🗑</button>`;
    row.querySelector('textarea').value = b.text || '';
    rows.appendChild(row);
  }}
  if (!(s.beats || []).length)
    rows.innerHTML = '<i>silent hold — no narration on this segment. Use ✚ add line.</i>';
  document.getElementById('editDlg').showModal();
}}
async function delLine(bi) {{
  if (!confirm('Remove this line from the video? The segment shortens by its length.')) return;
  document.getElementById('editDlg').close();
  await post('/api/storyboard/delline', {{seg_index: editing, beat_index: bi}}, 'removing line…');
}}
async function saveEdit() {{
  const boxes = [...document.querySelectorAll('#editRows textarea')];
  const beats = boxes.map(t => ({{index: parseInt(t.dataset.bi), text: t.value.trim()}}))
                     .filter(b => b.text);
  document.getElementById('editDlg').close();
  if (!beats.length) return alert('no lines to save — use 🗑 to remove a line, or ✚ add line');
  await post(`/api/segments/${{editing}}/narration`, {{beats}}, 're-synthesizing…');
}}
async function setStatus(i, st) {{
  await post(`/api/segments/${{i}}/status`, {{status: st, note: ''}});
}}
/* ---- Work log: the system's own record of every change, with evidence ---- */
function workMd(t) {{
  const e = String(t).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
  return e.replace(/\*\*([^*]+)\*\*/g, '<b>$1</b>').replace(/`([^`]+)`/g, '<code>$1</code>');
}}
function workEvidence(files) {{
  let h = '';
  for (const slug in files) {{
    for (const f of files[slug]) {{
      const u = '/api/evidence/' + encodeURIComponent(slug) + '/' + encodeURIComponent(f);
      if (/\.(png|jpe?g|webp)$/i.test(f))
        h += `<a href="${{u}}" target="_blank"><img src="${{u}}" loading="lazy" title="${{f}}" style="max-width:100%;max-height:360px;margin:6px 6px 0 0;border:1px solid var(--line);border-radius:4px"></a>`;
      else if (/\.(mp3|wav)$/i.test(f))
        h += `<div style="margin-top:6px"><span class="hint">${{f}}</span><br><audio controls preload="none" src="${{u}}" style="width:100%"></audio></div>`;
      else
        h += `<div><a href="${{u}}" target="_blank">📄 ${{f}}</a></div>`;
    }}
  }}
  return h;
}}
async function loadWork() {{
  const box = document.getElementById('worklist');
  try {{
    const d = await j('/api/worklog');
    document.getElementById('workstamp').textContent =
      (d.deployed_commit ? 'live build ' + d.deployed_commit + ' · ' : '') + d.entries.length + ' most recent entries';
    box.innerHTML = d.entries.map(function (e, i) {{
      const nd = /NOT (yet )?deployed/.test(e.body);
      const ev = workEvidence(e.evidence_files || {{}});
      return `<details ${{i === 0 ? 'open' : ''}} style="border-bottom:1px solid var(--line);padding:6px 0">
        <summary style="cursor:pointer"><b>${{workMd(e.title)}}</b>
          ${{nd ? '<span class="b" style="background:var(--warn);color:#1a1205;padding:1px 5px;border-radius:3px;font-size:10px;margin-left:6px">mentions not-deployed work</span>' : ''}}
          ${{ev ? '<span class="b" style="background:var(--sa-bg);color:var(--sa-ink);padding:1px 5px;border-radius:3px;font-size:10px;margin-left:6px">📎 evidence</span>' : ''}}</summary>
        <div style="white-space:pre-wrap;margin:6px 0 0 12px;line-height:1.45">${{workMd(e.body)}}</div>
        ${{ev ? '<div style="margin:4px 0 0 12px">' + ev + '</div>' : ''}}
      </details>`;
    }}).join('') || '<i>no entries</i>';
  }} catch (e) {{ box.textContent = 'Could not load the work log: ' + e.message; }}
}}
async function toggleApproval() {{
  if (APPROVED && !confirm('Project is already approved. Approve again to re-render ticked clips and re-export the final video?')) return;
  const r = await fetch('/api/storyboard/approve', {{method:'POST',
    headers:{{'Content-Type':'application/json'}}, body: JSON.stringify({{approved: true,
      rerender_all: !!(document.getElementById('forceAll') || {{}}).checked}})}});
  const jr = await r.json();
  if (jr.note) {{ alert(jr.note); return; }}
  if (jr.job) {{
    localStorage.setItem('finalizeJob', jr.job);
    document.getElementById('approveBtn').textContent = '✔ APPROVED — rendering…';
    document.getElementById('approveBtn').classList.add('on');
    pollFinalize(jr.job);
  }}
}}
/* The render error names /api/storyboard/repair_slices; this is the button
   for it, so fixing a blocked timeline needs no hand-made API call. Dry run
   first, so the operator sees what will change before anything is written. */
async function repairTimeline() {{
  let d;
  try {{
    d = await j('/api/storyboard/repair_slices', {{method:'POST',
      headers:{{'Content-Type':'application/json'}}, body: JSON.stringify({{dry_run: true}})}});
  }} catch (e) {{ alert('Could not check the timeline: ' + e.message); return; }}
  if (!d.total) {{ alert('Nothing to repair — no timing faults the repair can fix.'); return; }}
  const n = function (x) {{ return Array.isArray(x) ? x.length : ((x && x.n) || 0); }};
  const grown = (d.truncated || []).map(function (t) {{
    return 'seg ' + t.seg + ' ' + t.dur_before + 's -> ' + t.dur_after + 's'; }}).join(', ');
  if (!confirm('Repair ' + d.total + ' timing fault(s)?  ' +
               'swapped: ' + n(d.rebound) + ' · overlapping: ' + n(d.overlaps) +
               ' · outside window: ' + n(d.orphaned) + ' · shared: ' + n(d.shared_beats) +
               ' · audio longer than window: ' + n(d.truncated) +
               (grown ? ' (' + grown + ')' : '') +
               '.  Affected clips will need re-rendering. Undo is available.')) return;
  post('/api/storyboard/repair_slices', {{dry_run: false}}, 'repairing timeline…');
}}
/* ---- R1/R5/R6: live finalize progress (render -> export -> link) ---- */
let finalizeTimer = null;
async function stopRender() {{
  const id = localStorage.getItem('finalizeJob');
  if (!id) return;
  if (!confirm('Stop this render/export? It stops after the clip currently ' +
               'rendering; clips already built are kept and re-rendering ' +
               'resumes from there.')) return;
  try {{
    await j('/api/jobs/control', {{method:'POST',
      headers:{{'Content-Type':'application/json'}},
      body: JSON.stringify({{job_id: id, action: 'stop'}})}});
    const t = document.getElementById('rendertxt');
    if (t) t.textContent = 'stopping after this clip…';
  }} catch (e) {{ alert('Could not stop: ' + (e.message || e)); }}
}}

async function pollFinalize(id) {{
  clearInterval(finalizeTimer);
  const strip = document.getElementById('renderprog');
  strip.style.display = 'flex';
  finalizeTimer = setInterval(async () => {{
    let s;
    try {{ s = await j('/api/jobs/' + id); }} catch (e) {{ return; }}
    const bar = document.getElementById('renderbar'), txt = document.getElementById('rendertxt');
    if (s.stage === 'render') {{
      const pct = s.total ? Math.round(100 * s.done / s.total) : 0;
      bar.style.width = pct + '%';
      txt.textContent = `rendering clip ${{s.done}}/${{s.total}}` + (s.current_seg != null ? ` (seg #${{s.current_seg}})` : '') + '…';
      document.getElementById('clipcount').textContent = `${{s.done}}/${{s.total}}`;
    }} else if (s.stage === 'export') {{
      bar.style.width = '100%';
      txt.textContent = 'stitching final video (export)…';
      document.getElementById('expstate').textContent = '⏳';
    }}
    if (s.status === 'done') {{
      clearInterval(finalizeTimer);
      localStorage.removeItem('finalizeJob');
      txt.innerHTML = `✅ done — <a href="${{s.url}}" style="color:var(--ok)" target="_blank">download ${{s.export}}</a>`;
      document.getElementById('expstate').textContent = '✅';
      document.getElementById('approveBtn').textContent = '✔ APPROVED — export ready';
      loadExports();
      toggleDrawer('exports');
    }} else if (s.status === 'error') {{
      clearInterval(finalizeTimer);
      localStorage.removeItem('finalizeJob');
      txt.textContent = '❌ ' + (s.error || 'render failed') +
        (/repair_slices/.test(s.error || '') ? '  →  click 🔧 repair timeline, then approve again' : '');
      txt.style.color = 'var(--bad)';
    }}
  }}, 3000);
}}
async function loadExports() {{
  try {{
    const ex = await j('/api/exports');
    const days = ex.retention_days || 7;
    document.getElementById('exportlist').innerHTML =
      `<div class="hint" style="margin-bottom:6px">Exports are kept ${{days}} days, then deleted automatically. Delete sooner with ✕.</div>` +
      (ex.exports || []).map(e => {{
        const left = e.expires_in_days;
        const col = left <= 1 ? 'var(--bad)' : left <= 3 ? 'var(--warn)' : 'var(--ink3)';
        const mins = e.duration ? (Math.floor(e.duration/60)+':'+String(Math.round(e.duration%60)).padStart(2,'0')) : '?';
        const title = e.title || e.project;
        const ch = e.chapter ? ('Ch.' + e.chapter) : '';
        const part = e.part ? (' · Part ' + e.part) : '';
        const verdict = (e.review_status && e.review_status !== 'review_pending')
          ? (e.superseded ? 'superseded' : e.review_status.replace('_',' ')) : '';
        return `<div class="exprow">
        <div class="exphead">
          <a href="${{e.url}}" target="_blank" class="exptitle">${{e.series || title}}</a>
          ${{ch ? `<span class="expch">${{ch}}${{part}}</span>` : ''}}
          ${{e.active_project ? '<span class="expopen">open</span>' : ''}}
          ${{verdict ? `<span class="expverdict">${{verdict}}</span>` : ''}}
          <button title="delete this export now" class="expdel"
            onclick="delExport('${{e.name}}','${{e.project}}')">✕</button>
        </div>
        <div class="expmeta">${{mins}} · ${{e.size_mb}} MB · ${{e.created}}
          · <span style="color:${{col}}">expires in ${{left < 1 ? (Math.round(left*24) + 'h') : (Math.round(left) + 'd')}}</span></div>
        <div class="expmeta expfile" title="${{e.name}}">${{e.name}}</div>
        <a href="${{e.review_url || ('/review?project=' + e.project + '&name=' + e.name)}}"
           class="expreview">▶ watch &amp; review</a>
      </div>`;
      }}).join('') || 'No exports yet — tick segments and APPROVE.';
  }} catch (e) {{ document.getElementById('exportlist').textContent = 'failed to load'; }}
}}
async function delExport(name, project) {{
  if (!confirm('Delete export ' + name + '? This cannot be undone.')) return;
  try {{
    await j('/api/exports/delete', {{method:'POST', headers:{{'Content-Type':'application/json'}},
      body: JSON.stringify({{name, project}})}});
    loadExports();
  }} catch (e) {{ alert('Delete failed: ' + (e.message || e)); }}
}}
/* resume progress strip after refresh */
/* A "send back" verdict on /review has to reach the person editing, or the
   button is decoration. Surface it at the top of the board with its notes. */
fetch('/api/review').then(r => r.ok ? r.json() : null).then(d => {{
  if (!d || !d.review) return;
  const el = document.getElementById('sentback');
  if (!el) return;
  if (d.review.status === 'sent_back') {{
    el.style.display = 'block';
    el.innerHTML = '<b>↩ This render was sent back for revision.</b> ' +
      (d.review.notes ? ('<br>' + d.review.notes.replace(/</g, '&lt;')) : '') +
      '<br><span style="opacity:.85">Fix it below, re-render, then ' +
      '<a href="/review" style="color:var(--warnb-ink)">review the new export</a>.</span>';
  }} else if (d.review.superseded && d.review.status === 'approved') {{
    el.style.display = 'block';
    el.innerHTML = '<b>⚠ The approved export no longer matches this cut.</b> ' +
      'Re-render and <a href="/review" style="color:var(--warnb-ink)">review the new one</a> ' +
      'before publishing.';
  }}
}}).catch(() => {{}});
(function () {{
  const id = localStorage.getItem('finalizeJob');
  if (id) pollFinalize(id);
  fetch('/api/project').then(r => r.json()).then(p => {{
    const done = (p.segments || []).filter(s => s.user_included && s.clip_exists).length;
    const tot = (p.segments || []).filter(s => s.user_included).length;
    document.getElementById('clipcount').textContent = done + '/' + tot;
    fetch('/api/exports').then(r => r.json()).then(ex => {{
      if ((ex.exports || []).length) document.getElementById('expstate').textContent = '✅';
    }});
  }}).catch(() => {{}});
}})();
/* The cost header used to be baked in at render time: its clock was the BUILD
   time, so it was stale the instant the page loaded and never moved again.
   Poll the same endpoint the Logs tab uses. Payload is ~1 KB at n=1. */
function _money(v) {{ return '$' + (Number(v) || 0).toFixed(2); }}
async function refreshUsage() {{
  const box = document.getElementById('usagebox');
  if (!box) return;
  try {{
    const d = await j('/api/logs/usage?n=1');
    const su = d.summary || {{}}, life = su.lifetime || {{}}, rates = su.rates || {{}};
    const g = (life.gemini_calls || 0) + (su.gemini_calls || 0);
    const t = (life.tts_chars || 0) + (su.tts_chars || 0);
    const cl = (life.claude_calls || 0) + (su.claude_calls || 0);
    const c = (life.est_cost_usd || 0) + (su.est_cost_usd || 0);
    const now = new Date().toLocaleString('en-US', {{ timeZone: 'America/New_York',
      weekday: 'short', month: 'short', day: '2-digit',
      hour: 'numeric', minute: '2-digit' }});
    // Say WHICH rates produced the number rather than presenting an
    // assumption as a bill.
    const claudeNote = rates.claude_rates_published
      ? ' Claude is priced at published Anthropic list rates.'
      : ' Claude is priced at the PRICE_CLAUDE_* rates set here.';
    const tag = rates.gemini_rates_published && rates.defaults
      ? '<span title="Measured tokens priced at Google’s published Gemini rates (read ' + rates.gemini_prices_read +
        '), thinking included.' + claudeNote + ' Before 2026-10-03 Gemini Flash calls were under-counted (logged as $0).">at published rates</span>'
      : rates.defaults
      ? '<span title="Gemini/TTS are priced at built-in default rates — set PRICE_* env vars from your Google rate card.' + claudeNote + '">≈ at default rates</span>'
      : '<span title="priced at the PRICE_* rates configured for this deployment.' + claudeNote + '">at configured rates</span>';
    box.innerHTML = now + ' ET — today: ' + (su.gemini_calls || 0) + ' gemini · ' +
      (su.claude_calls || 0) + ' claude · ' +
      (su.tts_chars || 0).toLocaleString() + ' tts · ' + _money(su.est_cost_usd) +
      '<br>all-time: ' + g + ' gemini · ' + cl + ' claude · ' +
      t.toLocaleString() + ' tts · ' + _money(c) + ' ' + tag;
  }} catch (e) {{ /* leave the last good value on screen */ }}
}}
refreshUsage();
setInterval(refreshUsage, 15000);

/* ---- drawers: ingest / projects / logs (ported from legacy UI) ---- */
{theme.SHARED_JS}
{theme.SIDE_JS}
{theme.JOBS_JS}

/* Each sidebar section is a PAGE, the Scrapper studio's logic: choosing one
   switches the main area to it, the sidebar (or ☰ on a phone) is how you
   move on, and the browser's Back button returns. Nothing slides over the
   board and nothing needs closing. 'board' is the storyboard page itself. */
let CURRENT_VIEW = 'board', _viewFromHistory = false;
const VIEWS = ['ingest','projects','tracker','validate','logs','exports','settings','test','split'];
function toggleDrawer(name) {{
  // Work was merged into Logs: old links to it land on the "What changed" tab.
  let logsWant = name === 'work' ? 'work' : 'live';
  if (name === 'work') name = 'logs';
  if (VIEWS.indexOf(name) < 0) name = 'board';
  CURRENT_VIEW = name;
  for (const d of VIEWS) {{
    const el = document.getElementById('d_' + d);
    const btn = document.querySelector(`.navbtn[data-d="${{d}}"]`);
    if (el) el.style.display = d === name ? 'block' : 'none';
    if (btn) btn.classList.toggle('active', d === name);
  }}
  const bb = document.querySelector('.navitem[data-v="board"]');
  if (bb) bb.classList.toggle('active', name === 'board');
  document.body.classList.toggle('not-board', name !== 'board');
  if (!_viewFromHistory) {{
    const url = location.pathname + location.search + (name === 'board' ? '' : '#' + name);
    if (url !== location.pathname + location.search + location.hash) history.pushState({{v: name}}, '', url);
  }}
  window.scrollTo(0, 0);
  if (name === 'projects') loadProjects();
  if (name === 'tracker') trkTab('series');     // the release calendar first, as in the Scrapper; Watchlist and New chapters are a click away
  if (name === 'logs') {{ logsTab(logsWant); loadLogs(); }}
  if (name === 'work') loadWork();
  if (name === 'exports') loadExports();
  if (name === 'settings') loadSettings();
  if (name === 'validate') loadValidation();
  if (name === 'test') loadLab();
  if (name === 'split') loadSplit();
  if (name === 'ingest') {{ paintIngest(); loadVoices(); if (activeJob()) startIngestPoller(); }}
  apPolling(name === 'ingest');
}}
function showView(name) {{ toggleDrawer(name); }}
let LOGS_TAB = 'live';
function logsTab(which) {{
  LOGS_TAB = which;
  ['live', 'jobs', 'spend', 'work'].forEach(t => {{
    document.getElementById('logs_' + t).style.display = t === which ? 'block' : 'none';
    document.getElementById('lt_' + t).classList.toggle('on', t === which);
  }});
  if (which === 'work') loadWork(); else loadLogs();
}}
window.addEventListener('popstate', function () {{
  _viewFromHistory = true;
  try {{ toggleDrawer((location.hash || '').slice(1) || 'board'); }} finally {{ _viewFromHistory = false; }}
}});
(function () {{
  var h = (location.hash || '').slice(1);
  if (h && VIEWS.indexOf(h) >= 0) setTimeout(function () {{
    _viewFromHistory = true;
    try {{ toggleDrawer(h); }} finally {{ _viewFromHistory = false; }}
  }}, 60);
}})();
/* Arriving from another page with ?open=<drawer> should land on that drawer,
   so the rail behaves the same wherever you clicked it.

   The param is CONSUMED — stripped from the URL as soon as it is read. It used
   to be left in place, and because most board actions end in location.reload()
   (which preserves the query string), the drawer reopened on EVERY action for
   the rest of the session: click anything, and the last tab you came in on
   popped back out with its details showing. Arriving from /review is a
   one-time instruction, not a sticky mode. */
(function () {{
  var m = /[?&]open=([a-z]+)/.exec(location.search);
  if (!m) return;
  try {{
    var clean = location.pathname +
      location.search.replace(/([?&])open=[a-z]+&?/, '$1').replace(/[?&]$/, '');
    history.replaceState(null, '', clean + location.hash);
  }} catch (e) {{ /* a stale URL is survivable; a stuck drawer is not */ }}
  setTimeout(function () {{ toggleDrawer(m[1]); }}, 60);
}})();
/* ---- Check: the chapter-level review chain -------------------------
   Findings are painted from /api/validation AFTER load rather than baked into
   the page, so running a check — or applying a fix — updates the board in
   place. Nothing here reloads the page; a reload is what used to make the rail
   flash open and the last drawer pop back out.

   Every node is built with DOM calls rather than HTML strings: findings carry
   model-written text, and this file has a long history of escaping bugs. */
let vMode = 'full', vPoll = null, vReport = null;

/* ---- running indicator -------------------------------------------------
   Four states, carried by colour and motion rather than by wording: idle,
   queued, running, done, error. The rail button mirrors it, so a run is
   visible with the drawer shut. The job id is remembered, so closing the
   drawer — or reloading the page — reattaches to a run still in flight
   instead of leaving the tab looking idle while the server works. */
const V_MODE_LABEL = {{
  rules: 'Rules only — free, no Claude',
  text: 'Rules + Claude (chapter, sequence, descriptions)',
  full: 'Rules + Claude + vision (images)'
}};
const V_PILL = {{ idle: 'Not running', queued: 'Queued', running: 'Running',
                 done: 'Finished', error: 'Failed' }};

function vSetState(state, o, boxId, railTab) {{
  o = o || {{}};
  const box = document.getElementById(boxId || 'vstate');
  if (box) {{
    box.className = 'vstate ' + state;
    const pill = box.querySelector('.vspill');
    pill.textContent = '';
    if (state === 'running') pill.appendChild(vEl('span', 'vspin'));
    pill.appendChild(document.createTextNode(
      (state === 'running' ? ' ' : '') + (V_PILL[state] || state)));
    box.querySelector('.vsmode').textContent =
      o.mode ? V_MODE_LABEL[o.mode] || o.mode : '';
    box.querySelector('.vsstage').textContent = o.stage || '';
    const bar = box.querySelector('.vsbar');
    const fill = bar.querySelector('i');
    // A fraction when the job reports one; an indeterminate sweep otherwise,
    // so the bar never sits frozen at 0% looking stalled.
    if (o.total && o.done !== undefined && o.done !== null && o.total > 0) {{
      bar.classList.remove('indet');
      fill.style.width = Math.max(3, Math.round(100 * o.done / o.total)) + '%';
    }} else {{
      bar.classList.add('indet');
      fill.style.width = '';
    }}
  }}
  const btn = document.querySelector(
    '.navbtn[data-d="' + (railTab || 'validate') + '"]');
  if (btn) {{
    const old = btn.querySelector('.vdot');
    if (old) old.remove();
    if (state === 'running' || state === 'queued' || state === 'error') {{
      const d = vEl('span', 'vdot' + (state === 'error' ? ' error' : ''));
      d.title = 'Check: ' + (V_PILL[state] || state);
      btn.appendChild(d);
    }}
  }}
}}

function vJob(id) {{
  try {{
    if (id) localStorage.setItem('validateJob', id);
    else localStorage.removeItem('validateJob');
  }} catch (e) {{}}
  return id;
}}
function vActiveJob() {{
  try {{ return localStorage.getItem('validateJob'); }} catch (e) {{ return null; }}
}}

function setVMode(m) {{
  vMode = m;
  for (const k of ['rules','text','full']) {{
    const b = document.getElementById('vm_' + k);
    if (b) b.classList.toggle('on', k === m);
  }}
}}

function vClearBadges() {{
  document.querySelectorAll('tr.vflag,tr.vflag-medium,tr.vflag-low')
    .forEach(function (tr) {{
      tr.classList.remove('vflag', 'vflag-medium', 'vflag-low');
    }});
  document.querySelectorAll('.vbadge').forEach(function (b) {{ b.remove(); }});
}}

const V_RANK = {{ high: 0, medium: 1, low: 2 }};

/* Jumping to a row is the most-used action in the drawer, so it also FLASHES
   the row — scrolling a 138-row table to the right place is no use if you then
   have to work out which row it stopped on. */
function vJumpTo(pid) {{
  const tr = document.getElementById('row_' + pid);
  if (!tr) return;
  if (CURRENT_VIEW !== 'board') toggleDrawer('board');
  tr.scrollIntoView({{ behavior: 'smooth', block: 'center' }});
  tr.classList.add('vfocus');
  setTimeout(function () {{ tr.classList.remove('vfocus'); }}, 2200);
}}

function vEl(tag, cls, text) {{
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined && text !== null) e.textContent = text;
  return e;
}}

/* ---- actions: these change the REAL board, so they confirm first ---- */
async function vRunAction(f, act, btn) {{
  if (act.kind === 'client') {{
    if (act.id === 'goto') return vJumpTo(f.panel_id);
    if (act.id === 'jump_to_source') {{
      window.open('/panelimg/' + encodeURIComponent(f.panel_id), '_blank');
      return;
    }}
    return;
  }}
  // The preview is what WILL happen, shown before it happens — the confirm is
  // the "preview before/after" step, not a generic are-you-sure.
  if (act.preview && !confirm(act.label + ' — ' + act.preview + '.' +
      String.fromCharCode(10) + String.fromCharCode(10) +
      'Apply this to the board?')) return;
  const recheck = (document.getElementById('vrecheck') || {{}}).checked;
  if (btn) {{ btn.disabled = true; btn.textContent = 'working…'; }}
  try {{
    const params = Object.assign({{}}, act.params || {{}},
      {{ finding_id: f.id, panel_id: f.panel_id, category: f.category }});
    const r = await j('/api/validate/action', {{
      method: 'POST', headers: {{ 'Content-Type': 'application/json' }},
      body: JSON.stringify({{ action: act.id, params: params }})
    }});
    let msg = r.detail || 'done';
    if (r.before !== undefined && r.after !== undefined) {{
      msg += String.fromCharCode(10) + 'before: ' + (r.before || '(empty)') +
             String.fromCharCode(10) + 'after: ' + (r.after || '(empty)');
    }}
    if (btn) {{ btn.textContent = 'done'; btn.title = msg; }}
    if (r.changed === 'segments' || r.changed === 'descriptions') {{
      // The board on screen no longer matches the project. Reloading is the
      // honest move here — this is the one place a reload is correct, because
      // the rows themselves changed.
      if (recheck) {{ await runValidation(true); }}
      else {{ vMarkStale(msg); }}
    }} else {{
      await loadValidation();
    }}
    refreshUsage();
  }} catch (e) {{
    if (btn) {{ btn.disabled = false; btn.textContent = act.label; }}
    alert('Could not apply: ' + e.message);
  }}
}}

function vMarkStale(msg) {{
  const st = document.getElementById('vstatus');
  if (!st) return;
  const w = vEl('div', 'vstale');
  w.appendChild(vEl('div', null, msg || 'The board changed.'));
  w.appendChild(vEl('div', null,
    'These findings describe the board BEFORE that change.'));
  const b = vEl('button', 'primary', 'Reload board & re-check');
  b.onclick = function () {{ location.reload(); }};
  w.appendChild(b);
  st.appendChild(w);
}}

function vActionBar(f) {{
  const bar = vEl('div', 'vacts');
  (f.actions || []).forEach(function (act) {{
    const b = vEl('button', 'vact' + (act.kind === 'client' ? ' ghost' : ''),
                  act.label);
    if (act.preview) b.title = act.preview;
    b.onclick = function () {{ vRunAction(f, act, b); }};
    bar.appendChild(b);
  }});
  return bar;
}}

function vFindingCard(f) {{
  const d = vEl('div', 'vfind ' + f.severity + (f.accepted ? ' accepted' : ''));

  const h = vEl('div', 'vh');
  const a = vEl('a', 'vrow', 'row ' + f.row);
  a.href = 'javascript:void(0)';
  a.onclick = function () {{ vJumpTo(f.panel_id); }};
  h.appendChild(a);
  h.appendChild(vEl('span', 'vcat', f.category_label || f.category));
  if (typeof f.confidence === 'number') {{
    const c = vEl('span', 'vconf', Math.round(f.confidence * 100) + '% sure');
    c.title = 'How confident this pass is that the row is wrong';
    h.appendChild(c);
  }}
  if (f.scene !== null && f.scene !== undefined) {{
    h.appendChild(vEl('span', 'vscene', 'scene ' + f.scene));
  }}
  d.appendChild(h);

  d.appendChild(vEl('div', null, f.issue));
  if (f.suggestion) d.appendChild(vEl('div', 'vfix', 'Fix: ' + f.suggestion));

  let who = ({{ 'rules': 'rule check',
               'claude-sequence': 'sequence check',
               'claude-description': 'description check',
               'claude-vision-spot': 'image spot check' }})[f.source] || f.source;
  if (f.vision && f.vision.verdict) {{
    who += ' + image check: ' + f.vision.verdict;
    if (f.vision.reason) who += ' — ' + f.vision.reason;
  }}
  if (f.cleared) who += ' (cleared by the image — kept so you can see it ran)';
  if (f.from_spot_check) {{
    who += ' — nothing else flagged this row; the sample caught it';
  }}
  if (f.accepted) who += ' · you accepted this before';
  d.appendChild(vEl('div', 'vsrc', who));

  if (f.vision && f.vision.corrected_description) {{
    const cd = vEl('div', 'vsrc vcorr',
      'The panel actually shows: ' + f.vision.corrected_description);
    d.appendChild(cd);
  }}
  d.appendChild(vActionBar(f));
  return d;
}}

function paintValidation(rep) {{
  const st = document.getElementById('vstatus');
  const box = document.getElementById('vfindings');
  if (!st || !box) return;
  vReport = rep;
  vClearBadges();
  box.textContent = '';
  st.textContent = '';
  if (!rep) {{
    st.textContent = 'Never checked. Pick a mode and run.';
    return;
  }}

  const fs = rep.findings || [];
  const live = fs.filter(function (f) {{ return !f.accepted; }});
  const c = rep.counts || {{}};
  const when = rep.ts ? new Date(rep.ts).toLocaleString('en-US',
    {{ timeZone: 'America/New_York', month: 'short', day: '2-digit',
       hour: 'numeric', minute: '2-digit' }}) + ' ET' : 'unknown time';
  const bits = [when, rep.mode + ' mode',
                live.length + ' finding' + (live.length === 1 ? '' : 's'),
                (c.high || 0) + ' definite / ' + (c.medium || 0) + ' likely / ' +
                  (c.low || 0) + ' to review',
                (rep.clean_rows || 0) + ' of ' + (rep.rows || 0) + ' rows clean'];
  if (rep.calls) {{
    bits.push(rep.calls + ' Claude call' + (rep.calls === 1 ? '' : 's') +
              ' · $' + (rep.cost_usd || 0).toFixed(4) + ' · ' + rep.model);
  }} else {{
    bits.push('no Claude calls — rules only, $0.00');
  }}
  if (rep.accepted_suppressed) {{
    bits.push(rep.accepted_suppressed + ' previously accepted');
  }}
  st.appendChild(vEl('div', null, bits.join(' · ')));
  st.style.color = rep.status === 'ok' ? 'var(--ink3)' : 'var(--bad)';

  if (rep.status !== 'ok' && rep.error) {{
    // An errored run checked LESS than it claims to. Say so loudly rather than
    // letting a short findings list read as a clean board.
    const e = vEl('div', null, 'This run did not finish: ' + rep.error +
      ' — the rows it never reached are unchecked, not clean.');
    e.style.color = 'var(--bad)';
    e.style.marginTop = '5px';
    st.appendChild(e);
  }}
  if (rep.stale) vMarkStale('The board was edited after this check ran.');

  // What the chapter pass understood. Showing it is not decoration: if the
  // scene map is wrong, every sequence finding built on it is suspect, and
  // this is the only place that is visible.
  const ch = rep.chapter;
  if (ch && (ch.scenes || []).length) {{
    const det = document.createElement('details');
    det.className = 'vchapter';
    det.appendChild(vEl('summary', null,
      'Chapter as the checker read it — ' + ch.scenes.length + ' scenes, ' +
      ((ch.reveals || []).length) + ' reveals'));
    if (ch.premise) det.appendChild(vEl('div', 'vsrc', ch.premise));
    ch.scenes.forEach(function (sc) {{
      det.appendChild(vEl('div', 'vscenerow',
        'Scene ' + sc.scene + ' [' + sc.phase + '] units ' +
        (sc.units || []).join(', ') + ' — ' + sc.title));
    }});
    (ch.reveals || []).forEach(function (rv) {{
      det.appendChild(vEl('div', 'vscenerow vreveal',
        'Reveal at unit ' + rv.unit + ': ' + rv.what));
    }});
    box.appendChild(det);
  }}

  (rep.vision_skipped || []).forEach(function (sk) {{
    box.appendChild(vEl('div', 'hint',
      'row ' + sk.row + ': image check skipped (' + sk.why + ')'));
  }});

  if (!fs.length) {{
    const ok = vEl('div', 'hint', rep.status === 'ok'
      ? 'Nothing flagged. The board is clean by these checks.'
      : 'Nothing flagged in the part that ran.');
    ok.style.color = 'var(--ok)';
    box.appendChild(ok);
    return;
  }}

  // ---- badges on the board itself
  const worst = {{}}, counts = {{}};
  live.forEach(function (f) {{
    const r = V_RANK[f.severity] === undefined ? 2 : V_RANK[f.severity];
    if (worst[f.panel_id] === undefined || r < worst[f.panel_id]) {{
      worst[f.panel_id] = r;
    }}
    counts[f.panel_id] = (counts[f.panel_id] || 0) + 1;
  }});
  Object.keys(worst).forEach(function (pid) {{
    const tr = document.getElementById('row_' + pid);
    if (!tr) return;
    const sev = ['high', 'medium', 'low'][worst[pid]];
    tr.classList.add(sev === 'high' ? 'vflag' : 'vflag-' + sev);
    const cell = tr.querySelector('td.n');
    if (!cell) return;
    const b = vEl('span', 'vbadge ' + sev,
                  (sev === 'high' ? 'FIX ' : 'check ') + counts[pid]);
    b.title = live.filter(function (f) {{ return f.panel_id === pid; }})
                  .map(function (f) {{ return f.category_label + ': ' + f.issue; }})
                  .join(' | ');
    b.onclick = function () {{ toggleDrawer('validate'); }};
    cell.appendChild(b);
  }});

  // ---- findings grouped by how sure the checker is
  ['high', 'medium', 'low'].forEach(function (sev) {{
    const group = live.filter(function (f) {{ return f.severity === sev; }});
    if (!group.length) return;
    const label = ({{ high: 'Definitely wrong', medium: 'Likely wrong',
                     low: 'Worth review' }})[sev];
    box.appendChild(vEl('div', 'vgroup ' + sev, label + ' (' + group.length + ')'));
    group.forEach(function (f) {{ box.appendChild(vFindingCard(f)); }});
  }});

  const acc = fs.filter(function (f) {{ return f.accepted; }});
  if (acc.length) {{
    const det = document.createElement('details');
    det.className = 'vchapter';
    det.appendChild(vEl('summary', null,
      'Previously accepted (' + acc.length + ') — kept so you can reconsider'));
    acc.forEach(function (f) {{ det.appendChild(vFindingCard(f)); }});
    box.appendChild(det);
  }}
}}

async function loadValidation() {{
  try {{
    const d = await j('/api/validation');
    setVMode(vMode);
    paintValidation(d.report);
    // Only describe a resting state here; a live run owns the indicator.
    if (!vPoll && !vActiveJob()) {{
      const rep = d.report;
      if (!rep) vSetState('idle', {{ stage: 'This chapter has never been checked.' }});
      else if (rep.status !== 'ok') {{
        vSetState('error', {{ mode: rep.mode,
          stage: 'Last run did not finish: ' + (rep.error || 'unknown') }});
      }} else {{
        const live = (rep.findings || []).filter(function (f) {{ return !f.accepted; }});
        vSetState('done', {{ mode: rep.mode,
          stage: 'Last run: ' + live.length + ' finding' +
                 (live.length === 1 ? '' : 's') +
                 (rep.stale ? ' — board edited since' : '') }});
      }}
    }}
  }} catch (e) {{
    const st = document.getElementById('vstatus');
    if (st) st.textContent = 'Could not load the last check: ' + e.message;
  }}
}}

async function runValidation(quiet) {{
  const st = document.getElementById('vstatus');
  if (!quiet && vMode !== 'rules' &&
      !confirm('The Claude passes spend credit (metered and capped like every ' +
               'other call; the cost shows here and in the header when the run ' +
               'finishes). Continue?')) return;
  try {{
    const r = await j('/api/validate', {{
      method: 'POST', headers: {{ 'Content-Type': 'application/json' }},
      body: JSON.stringify({{ mode: vMode }})
    }});
    vJob(r.job);
    vSetState('queued', {{ mode: vMode,
                          stage: 'Queued — ' + r.rows + ' rows to check' }});
    vWatch(r.job, vMode);
  }} catch (e) {{
    vSetState('error', {{ mode: vMode, stage: 'Could not start: ' + e.message }});
    if (st) st.textContent = 'Could not start: ' + e.message;
  }}
}}

/* Poll one run to completion. Separate from runValidation so a run already in
   flight can be picked back up on load. */
function vWatch(jobId, mode) {{
  if (vPoll) clearInterval(vPoll);
  const tick = async function () {{
    try {{
      const s = await j('/api/jobs/' + jobId);
      const m = s.mode || mode || vMode;
      if (s.status === 'done' || s.status === 'error') {{
        clearInterval(vPoll);
        vPoll = null;
        vJob(null);
        await loadValidation();
        refreshUsage();   // the spend just changed; do not wait 15s to say so
        vSetState(s.status === 'done' ? 'done' : 'error',
                  {{ mode: m, stage: s.status === 'done'
                      ? (s.stage || 'Finished')
                      : ('Failed: ' + (s.error || s.stage || 'unknown')) }});
      }} else {{
        vSetState(s.status === 'queued' ? 'queued' : 'running',
                  {{ mode: m, stage: s.stage || 'Working…',
                    done: s.done, total: s.total }});
      }}
    }} catch (e) {{
      clearInterval(vPoll);
      vPoll = null;
      vJob(null);
      vSetState('error', {{ mode: mode, stage: 'Lost contact with the run: '
                                               + e.message }});
    }}
  }};
  tick();
  vPoll = setInterval(tick, 2000);
}}

/* Badges are useful without opening the drawer, so the last report is painted
   on every load. Costs one small request and no API spend. */
loadValidation().then(function () {{
  // A run survives the page; the indicator must too, or a reload makes a
  // working checker look idle.
  const live = vActiveJob();
  if (live) vWatch(live, vMode);
}});

/* ---- 🧪 TEST LAB: an independent Claude-driven chapter pipeline ----
   This is NOT a view onto the board. It builds its own chapter from a URL and
   ends in a REAL project, which is why "see the result like the board" needs no
   special viewer: you open the lab project ON the board. */
let labSplit = 'claude', testPoll = null;

function tSetState(state, o) {{ vSetState(state, o, 'teststate', 'test'); }}

function setLabSplit(v) {{
  labSplit = v;
  ['claude', 'yolo'].forEach(function (k) {{
    const b = document.getElementById('lb_' + k);
    if (b) b.classList.toggle('on', k === v);
  }});
}}

async function runLab() {{
  const url = (document.getElementById('laburl').value || '').trim();
  if (!/^https?:\/\//.test(url)) {{
    alert('Paste a full http(s) chapter URL.');
    return;
  }}
  const fresh = document.getElementById('labfresh').checked;
  if (!confirm('Build this chapter from scratch with Claude?'
      + String.fromCharCode(10) + String.fromCharCode(10)
      + 'Panels cut by: ' + (labSplit === 'claude' ? 'Claude' : 'YOLO')
      + String.fromCharCode(10)
      + 'This downloads the chapter, then spends Claude credit reading every '
      + 'panel and writing the script, then TTS characters voicing it. It '
      + 'creates its OWN project and does not touch your existing chapters.'))
    return;
  try {{
    const r = await j('/api/lab/run', {{
      method: 'POST', headers: {{ 'Content-Type': 'application/json' }},
      body: JSON.stringify({{ url: url, splitter: labSplit, fresh: fresh }})
    }});
    tSetState('queued', {{ mode: 'panels cut by ' + r.splitter,
                          stage: 'Queued — building ' + r.project }});
    tWatch(r.job, 'panels cut by ' + r.splitter);
  }} catch (e) {{
    tSetState('error', {{ stage: 'Could not start: ' + e.message }});
  }}
}}

function tWatch(jobId, mode) {{
  if (testPoll) clearInterval(testPoll);
  const tick = async function () {{
    try {{
      const s = await j('/api/jobs/' + jobId);
      if (s.status === 'done' || s.status === 'error') {{
        clearInterval(testPoll);
        testPoll = null;
        tSetState(s.status === 'done' ? 'done' : 'error',
                  {{ mode: mode, stage: s.stage || s.status }});
        await loadLab();
        refreshUsage();
      }} else {{
        tSetState(s.status === 'queued' ? 'queued' : 'running',
                  {{ mode: mode, stage: s.stage || 'Working…',
                    done: s.done, total: s.total }});
      }}
    }} catch (e) {{
      clearInterval(testPoll);
      testPoll = null;
      tSetState('error', {{ stage: 'Lost contact with the run: ' + e.message }});
    }}
  }};
  tick();
  testPoll = setInterval(tick, 3000);
}}

/* Opening a lab chapter just ACTIVATES it. From that moment the board, the
   Check tab, approve, export and Review are all looking at Claude's chapter,
   because it is an ordinary project. */
async function openLab(pid) {{
  if (!confirm('Open ' + pid + ' on the board?' + String.fromCharCode(10)
      + 'This switches the studio to that chapter. Your other chapters are '
      + 'untouched and you can switch back from Projects.')) return;
  try {{
    await j('/api/activate', {{
      method: 'POST', headers: {{ 'Content-Type': 'application/json' }},
      body: JSON.stringify({{ id: pid }})
    }});
    location.href = '/storyboard';
  }} catch (e) {{ alert('Could not open: ' + e.message); }}
}}

/* Per-stage diagnostics: what the pipeline measured about its own run.
   Deliberately reports rates rather than scoring them — whether a given
   full-frame rate is good depends on the chapter, and pretending otherwise
   would be the fabricated-win problem in a new costume. */
function _row(box, label, value, tone) {{
  const d = vEl('div', 'expmeta');
  const b = vEl('b', null, label + ': ');
  d.appendChild(b);
  d.appendChild(document.createTextNode(String(value)));
  if (tone) d.style.color = tone;
  box.appendChild(d);
}}

async function showLabReport(pid, host) {{
  let box = host.querySelector('.labdiag');
  if (box) {{ box.remove(); return; }}
  box = vEl('div', 'labdiag');
  host.appendChild(box);
  box.appendChild(vEl('div', 'expmeta', 'loading diagnostics…'));
  try {{
    const r = await j('/api/lab/report?project=' + encodeURIComponent(pid));
    box.textContent = '';

    box.appendChild(vEl('div', 'tl', 'who decided what'));
    _row(box, 'model-driven', (r.model_driven_stages || []).join(', '));
    _row(box, 'deterministic', (r.deterministic_stages || []).join(', '));

    const sp = r.splitting || {{}};
    box.appendChild(vEl('div', 'tl', 'panel cutting'));
    _row(box, 'ink coverage (mean/min)',
         (sp.coverage_mean === null || sp.coverage_mean === undefined)
           ? 'n/a (YOLO)' : (sp.coverage_mean + ' / ' + sp.coverage_min));
    if (sp.pages_below_target) {{
      _row(box, 'pages under target', sp.pages_below_target, 'var(--warn)');
    }}
    if (sp.retried_pages) _row(box, 'pages retried', sp.retried_pages);
    if (sp.recovered_pages) _row(box, 'pages geometrically recovered', sp.recovered_pages);
    if (sp.blanks_dropped) _row(box, 'blank crops dropped', sp.blanks_dropped);
    if (sp.fell_back_to_yolo) {{
      _row(box, 'fell back to YOLO', 'yes — Claude coverage was short', 'var(--warn)');
    }}

    const rd = r.reading || {{}};
    box.appendChild(vEl('div', 'tl', 'reading'));
    _row(box, 'panels', rd.panels);
    _row(box, 'missing OCR', rd.missing_ocr + ' (' + rd.missing_ocr_pct + '%)');
    _row(box, 'low-confidence OCR',
         rd.low_confidence_ocr + ' (' + rd.low_confidence_ocr_pct + '%)',
         rd.low_confidence_ocr ? 'var(--warn)' : null);
    _row(box, 'generic descriptions', rd.generic_descriptions + ' (' + rd.generic_pct + '%)');
    _row(box, 'needs review', rd.needs_review);
    _row(box, 'contract violations', rd.contract_violations);

    const sc = r.script || {{}};
    box.appendChild(vEl('div', 'tl', 'script'));
    _row(box, 'scenes', sc.scenes);
    _row(box, 'critique issues', sc.critique_issues + ' ' +
         JSON.stringify(sc.critique_by_type || {{}}));
    _row(box, 'units revised', sc.units_revised);
    (sc.dense_allowances || []).forEach(function (a) {{
      _row(box, 'dense allowance · scene ' + a.scene,
           a.base + ' -> ' + a.granted + ' words (' + (a.reasons || []).join('; ') + ')',
           'var(--ai)');
    }});

    const pl = r.placement || {{}};
    box.appendChild(vEl('div', 'tl', 'placement (deterministic)'));
    _row(box, 'distinct panels used', pl.distinct_panels);
    _row(box, 'junk panels avoided', pl.junk_panels_avoided);
    _row(box, 'order inversions repaired', pl.order_inversions_repaired);
    _row(box, 'over-holds', pl.over_holds);
    _row(box, 'ambiguous pairs', pl.ambiguous_pairs);
    _row(box, 'provenance escapes', pl.provenance_escapes,
         pl.provenance_escapes ? 'var(--ai)' : null);
    (pl.provenance_escape_detail || []).slice(0, 5).forEach(function (e) {{
      _row(box, '  beat ' + e.beat, 'left scene ' + e.scene +
           ' for ' + e.panel_id + ' (margin ' + e.margin + ')');
    }});

    const fr = r.framing || {{}};
    box.appendChild(vEl('div', 'tl', 'framing (after placement)'));
    _row(box, 'panels framed', fr.panels_framed);
    _row(box, 'full frame', fr.full_frame + ' (' + fr.full_frame_pct + '%)');
    _row(box, 'cropped in', fr.cropped);
    _row(box, 'refused to full frame', fr.rejected_to_full_frame);

    const ck = r.checker || {{}};
    box.appendChild(vEl('div', 'tl', 'checker audit'));
    _row(box, 'findings', ck.findings + ' ' + JSON.stringify(ck.by_severity || {{}}));
    _row(box, 'rows clean', ck.rows_clean_pct + '%');

    box.appendChild(vEl('div', 'tl', 'timing'));
    _row(box, 'source', (r.timing || {{}}).source);

    (r.caveats || []).forEach(function (c) {{
      box.appendChild(vEl('div', 'cmpcaveat', '• ' + c));
    }});
  }} catch (e) {{
    box.textContent = '';
    box.appendChild(vEl('div', 'expmeta', 'no diagnostics: ' + e.message));
  }}
}}

async function loadLab() {{
  const info = document.getElementById('testinfo');
  const box = document.getElementById('testbody');
  if (!info || !box) return;
  box.textContent = '';
  try {{
    const d = await j('/api/lab/projects');
    // Which chapters are being built RIGHT NOW. Without this the card reads
    // the half-written manifest and reports a live run as unfinished-and-
    // unopenable, which is indistinguishable from a run that died.
    let liveByProject = {{}};
    try {{
      const jd = await j('/api/jobs?limit=200');
      (jd.jobs || []).forEach(function (x) {{
        if (x.kind === 'lab' && (x.status === 'running' || x.status === 'queued')) {{
          liveByProject[x.project || ''] = x;
          if (x.url) liveByProject['url:' + x.url] = x;
        }}
      }});
    }} catch (e) {{}}
    setLabSplit(labSplit);
    if (!d.key_configured) {{
      info.textContent = 'NO CLAUDE KEY on this server — set CLAUDE_API_KEY '
        + 'or ANTHROPIC_API_KEY.';
      info.style.color = 'var(--bad)';
    }} else {{
      info.textContent = (d.projects || []).length
        ? (d.projects.length + ' lab chapter(s) built so far')
        : 'No lab chapters yet. Paste a link above and build one.';
      info.style.color = 'var(--ink3)';
    }}
    (d.projects || []).forEach(function (p) {{
      const w = vEl('div', 'trow');
      const h = vEl('div', 'exphead');
      h.appendChild(vEl('span', 'exptitle', p.title || p.project));
      h.appendChild(vEl('span', 'expch',
        'cut by ' + (p.splitter || '?')));
      if (p.active) h.appendChild(vEl('span', 'expopen', 'open now'));
      if (p.status === 'error') {{
        const e = vEl('span', 'expverdict', 'failed');
        e.style.color = 'var(--bad)';
        h.appendChild(e);
      }}
      w.appendChild(h);
      w.appendChild(vEl('div', 'expmeta',
        [p.panels ? p.panels + ' panels' : null,
         p.units ? p.units + ' lines' : null,
         p.segments ? p.segments + ' segments' : null,
         p.duration ? Math.round(p.duration) + 's' : null,
         p.calls ? p.calls + ' Claude calls' : null,
         (p.cost_usd !== null && p.cost_usd !== undefined)
           ? '$' + Number(p.cost_usd).toFixed(4) : null
        ].filter(Boolean).join(' · ') || '—'));
      if (p.error) {{
        const er = vEl('div', 'expmeta', p.error);
        er.style.color = 'var(--bad)';
        w.appendChild(er);
      }}
      const acts = vEl('div', 'vacts');
      if (p.ready) {{
        const b = vEl('button', 'vact', 'Open on the board');
        b.onclick = function () {{ openLab(p.project); }};
        acts.appendChild(b);
      }} else {{
        const live = liveByProject[p.project] || liveByProject['url:' + (p.url || '')];
        if (live) {{
          const b = vEl('div', 'expmeta',
            '⏳ building now — ' + (live.stage || 'working…'));
          b.style.color = 'var(--warn)';
          acts.appendChild(b);
        }} else {{
          acts.appendChild(vEl('div', 'expmeta',
            'not finished — nothing to open yet'));
        }}
      }}
      if (p.ready) {{
        const dg = vEl('button', 'vact ghost', 'Diagnostics');
        dg.onclick = function () {{ showLabReport(p.project, w); }};
        acts.appendChild(dg);
      }}
      if (p.url) {{
        const rr = vEl('button', 'vact ghost', 'Rebuild');
        rr.onclick = function () {{
          document.getElementById('laburl').value = p.url;
          setLabSplit(p.splitter || 'claude');
        }};
        acts.appendChild(rr);
      }}
      w.appendChild(acts);
      box.appendChild(w);
    }});
  }} catch (e) {{
    info.textContent = 'Could not load: ' + e.message;
  }}
}}

const ING_STAGES = ['scrape','split','describe','narrate','voice','match','segment'];
let ingestState = null, ingestPolling = false;
/* ---- narrator voice picker (Ingest page) ---- */
let VOICE_DATA = null;
async function loadVoices() {{
  if (VOICE_DATA) return;
  try {{ VOICE_DATA = await j('/api/voices'); }} catch (e) {{
    document.getElementById('voicestatus').textContent = 'could not load voices'; return; }}
  const d = VOICE_DATA.default || {{}};
  document.getElementById('ingvoice').innerHTML = VOICE_DATA.voices.map(function (v) {{
    return '<option value="' + v.id + '"' + (v.id === d.id ? ' selected' : '') + '>' +
      v.label + (v.id === d.id ? '  ★ default' : '') + '</option>';
  }}).join('');
  const styles = VOICE_DATA.styles.slice();
  if (d.style && !styles.some(function (x) {{ return x.style === d.style; }}))
    styles.push({{style: d.style, label: d.style}});
  document.getElementById('ingstyle').innerHTML = styles.map(function (x) {{
    return '<option value="' + x.style.replace(/"/g, '&quot;') + '"' + (x.style === (d.style || '') ? ' selected' : '') + '>' +
      'Style: ' + x.label + '</option>';
  }}).join('');
  voiceChanged();
}}
function voiceChanged() {{
  const v = document.getElementById('ingvoice').value || '';
  // the classic Chirp voice has no style control
  document.getElementById('ingstyle').disabled = v.indexOf('chirp:') === 0;
  const a = document.getElementById('vprev'); a.style.display = 'none'; a.removeAttribute('src');
  document.getElementById('voicestatus').textContent = '';
}}
async function previewVoice() {{
  const b = document.getElementById('vprevbtn'), st = document.getElementById('voicestatus');
  b.disabled = true; st.textContent = 'recording a sample…';
  try {{
    const r = await j('/api/voices/preview', {{method:'POST', headers:{{'Content-Type':'application/json'}},
      body: JSON.stringify({{voice: document.getElementById('ingvoice').value,
                            style: document.getElementById('ingstyle').value}})}});
    const a = document.getElementById('vprev');
    a.src = r.url; a.style.display = 'block'; st.textContent = '';
    try {{ await a.play(); }} catch (e) {{ /* the player is there to press */ }}
  }} catch (e) {{ st.textContent = 'preview failed: ' + (e.message || e); }}
  b.disabled = false;
}}
async function saveDefaultVoice() {{
  const st = document.getElementById('voicestatus');
  try {{
    await j('/api/voices/default', {{method:'POST', headers:{{'Content-Type':'application/json'}},
      body: JSON.stringify({{voice: document.getElementById('ingvoice').value,
                            style: document.getElementById('ingstyle').value}})}});
    VOICE_DATA = null; await loadVoices();
    st.textContent = '✓ saved as the default for new chapters';
  }} catch (e) {{ st.textContent = 'could not save: ' + (e.message || e); }}
}}
function activeJob() {{ return localStorage.getItem('activeIngestJob'); }}
function setActiveJob(id) {{ if (id) localStorage.setItem('activeIngestJob', id); else localStorage.removeItem('activeIngestJob'); }}
async function runIngest() {{
  const url = document.getElementById('ingurl').value.trim();
  if (!/^https?:\\/\\//.test(url)) {{ alert('Paste a full http(s) chapter URL'); return; }}
  const fresh = document.getElementById('ingfresh').checked;
  const engine = document.getElementById('ingengine').value;
  const variant = (document.getElementById('ingvariant').value || '').trim().toLowerCase();
  const direct = document.getElementById('ingdirect').checked;
  if (fresh && !confirm('Fresh re-ingest regenerates narration, TTS audio and the timeline for this chapter (cached descriptions and unchanged TTS lines are still reused). Continue?')) return;
  if (engine === 'claude' && !confirm('Run this chapter through the CLAUDE engine?' + String.fromCharCode(10) + String.fromCharCode(10) + 'Gemini is the established path; Claude is newer and its cost profile differs.' + String.fromCharCode(10) + 'The choice is recorded on the project.')) return;
  try {{
    // direct_speech is only sent when ticked, so an unticked box keeps the
    // server's default (DIRECT_SPEECH env, off) instead of forcing it off
    const body = {{url, fresh, engine}};
    if (variant) body.variant = variant;
    if (direct) body.direct_speech = true;
    const vv = document.getElementById('ingvoice');
    if (vv && vv.value && vv.value.indexOf(':') > 0) {{
      body.voice = vv.value;
      body.voice_style = document.getElementById('ingstyle').value;
    }}
    const r = await j('/api/ingest', {{method:'POST', headers:{{'Content-Type':'application/json'}}, body: JSON.stringify(body)}});
    setActiveJob(r.job); startIngestPoller();
  }} catch (e) {{ alert(e.message); }}
}}
function stageBar(cur, pct, msg, err) {{
  return `<div style="font-size:12px">${{ING_STAGES.map(s => {{
    const done = ING_STAGES.indexOf(s) < ING_STAGES.indexOf(cur), on = s === cur;
    return `<div style="display:flex;align-items:center;gap:6px;margin:2px 0;color:${{done ? 'var(--ok)' : on ? 'var(--accent)' : 'var(--ink3)'}}">
      <span>${{done ? '✓' : on ? '●' : '○'}}</span>${{s}}</div>`; }}).join('')}}</div>
    <div style="height:6px;background:var(--rule);border-radius:3px;margin:8px 0;overflow:hidden">
      <div style="height:100%;width:${{pct}}%;background:${{err ? 'var(--bad)' : 'var(--accent)'}}"></div></div>
    <div class="hint">${{err ? ('⚠ ' + err) : (msg || '')}}</div>`;
}}
function paintIngest() {{
  const box = document.getElementById('ingprog'); if (!box || !ingestState) return;
  const s = ingestState;
  box.innerHTML = stageBar(s.stage, s.pct, s.msg, s.status === 'error' ? s.error : null);
  // Stop is offered only while there is something to stop. It is cooperative:
  // the worker checks between pipeline stages, so it lands within seconds
  // rather than instantly — that is the honest trade for not killing a
  // half-written project.
  if (s.status === 'running' || s.status === 'queued' || s.status === 'pausing') {{
    box.innerHTML += `<div style="margin-top:8px;display:flex;gap:6px;align-items:center">
      <button class="danger" onclick="stopIngest()">⏹ Stop ingest</button>
      <span class="hint">stops after the current step finishes</span></div>`;
  }}
  if (s.status === 'done') {{
    box.innerHTML += `<div class="hint" style="margin-top:8px">
      ✅ Finished${{s.project && s.project.id ? ' — ' + s.project.id : ''}}.
      This is the LAST run, not a live one.
      <button class="mini" style="margin-left:6px" onclick="ingestState=null;
        document.getElementById('ingprog').innerHTML='';">clear</button></div>`;
  }}
  if (s.status === 'cancelled') {{
    box.innerHTML += `<div class="hint" style="margin-top:8px">Stopped. Whatever
      finished is kept; re-run the ingest to continue.</div>`;
  }}
  if (s.status === 'done' && s.project) {{
    box.innerHTML += `<button class="primary" style="margin-top:8px" onclick="activateProj('${{s.project.id}}')">Open “${{s.project.id}}” (${{s.project.n_segments}} segs)</button>`;
  }}
}}
async function stopIngest() {{
  const id = activeJob();
  if (!id) return;
  if (!confirm('Stop this ingest? It stops after the current step; anything ' +
               'already downloaded or described is kept.')) return;
  try {{
    await j('/api/jobs/control', {{method:'POST',
      headers:{{'Content-Type':'application/json'}},
      body: JSON.stringify({{job_id: id, action: 'stop'}})}});
    if (ingestState) {{ ingestState.status = 'pausing'; paintIngest(); }}
  }} catch (e) {{ alert('Could not stop: ' + (e.message || e)); }}
}}

async function startIngestPoller() {{
  if (ingestPolling) return; ingestPolling = true;
  try {{
    while (activeJob()) {{
      let s;
      try {{ s = await j('/api/ingest/status/' + activeJob()); }}
      catch (e) {{ await new Promise(r => setTimeout(r, 2500)); continue; }}
      ingestState = s; paintIngest();
      if (s.status === 'done' || s.status === 'error') {{
        setActiveJob(null);
        if (s.status === 'done' && s.project && s.source !== 'autopilot') await activateProj(s.project.id);
        break;
      }}
      await new Promise(r => setTimeout(r, 1500));
    }}
  }} finally {{ ingestPolling = false; }}
}}
if (activeJob()) startIngestPoller();   // survive reloads mid-ingest
async function recoverActiveIngest() {{
  // A finished job left in localStorage used to repaint its progress bar on
  // every load, so the drawer showed a run that ended hours ago as though it
  // were live. Validate the stored id before trusting it.
  const stored = activeJob();
  if (stored) {{
    try {{
      const s = await j('/api/ingest/status/' + stored);
      if (s.status === 'done' || s.status === 'error' || s.status === 'cancelled') {{
        setActiveJob(null); ingestState = s; paintIngest();
      }} else {{ startIngestPoller(); }}
    }} catch (e) {{ setActiveJob(null); }}
    return;
  }}
  try {{
    const ij = await j('/api/logs/ingest');
    // Autopilot chapters run in the background: adopting one here would end
    // with activateProj() switching the board away from what you are reviewing.
    const running = (ij.jobs || []).find(x => (x.status === 'running' || x.status === 'queued')
                                              && x.source !== 'autopilot');
    if (running) {{ setActiveJob(running.job); startIngestPoller(); }}
  }} catch (e) {{}}
}}
recoverActiveIngest();

async function recoverActiveLab() {{
  // A lab run lives in a background thread on the server, but the only thing
  // watching it was tWatch's setInterval — which dies with the page. Reload,
  // navigate away, or open the board on a phone and the run kept spending
  // money with nothing reporting on it, while the card sat on the stale
  // manifest saying "not finished — nothing to open yet".
  if (testPoll) return;
  try {{
    const d = await j('/api/jobs?limit=200');
    const live = (d.jobs || []).find(x => x.kind === 'lab' &&
      (x.status === 'running' || x.status === 'queued'));
    if (live) tWatch(live.job, 'panels cut by ' + (live.splitter || '?'));
  }} catch (e) {{}}
}}
recoverActiveLab();
// ================= AUTOPILOT =================
// Chapter Autopilot (owner request 2026-10-03): ingests new chapters of every
// Tracker series by itself, one at a time, round robin, under the daily cap.
// The card follows rule 40: state, what happens next, last run, how to undo.
let apTimer = null;
function apPolling(on) {{
  if (on && !apTimer) {{ loadAutopilot(); apTimer = setInterval(loadAutopilot, 30000); }}
  if (!on && apTimer) {{ clearInterval(apTimer); apTimer = null; }}
}}
function apAgo(ts) {{
  if (!ts) return 'never';
  const s = Math.max(0, Math.round(Date.now() / 1000 - ts));
  return s < 90 ? s + ' s ago' : s < 5400 ? Math.round(s / 60) + ' min ago' : Math.round(s / 3600) + ' h ago';
}}
const AP_STATE = {{
  ready: ['var(--okb-bg)', 'var(--okb-ink)', 'ready'], running: ['var(--sa-bg)', 'var(--sa-ink)', 'ingesting'],
  up_to_date: ['var(--gray-bg)', 'var(--gray-ink)', 'caught up'], paused: ['var(--warnb-bg)', 'var(--warn)', 'paused'],
  stopped: ['var(--warnb-bg)', 'var(--warn)', 'stopped by you'], cooldown: ['var(--warnb-bg)', 'var(--warn)', 'retrying soon'],
  blocked: ['var(--badb-bg)', 'var(--bad)', 'needs you'], no_source: ['var(--badb-bg)', 'var(--bad)', 'no source']
}};
function apPill(t, bg, fg) {{
  return `<span style="background:${{bg}};color:${{fg}};font-size:10px;font-weight:700;padding:1px 7px;border-radius:99px;white-space:nowrap">${{t}}</span>`;
}}
async function loadAutopilot() {{
  const box = document.getElementById('apcard');
  if (!box) return;
  // The 30 s refresh used to rebuild the card from scratch: it collapsed the
  // open "All series" list (the page shrank by ~1,500 px and a phone's scroll
  // jumped toward the top) and overwrote a number being typed. Never refresh
  // under a box you are typing in; keep the list open and the page in place.
  const ae = document.activeElement;
  if (ae && box.contains(ae) && ae.tagName === 'INPUT') return;
  const wasOpen = !!(box.querySelector('details') || {{}}).open;
  const keepY = window.scrollY;
  let d;
  try {{ d = await j('/api/autopilot'); }}
  catch (e) {{ box.innerHTML = `<span style="color:var(--bad)">⚠ could not load autopilot — ${{e.message || e}}</span>`; return; }}
  box.classList.remove('hint');
  const names = {{}};
  (d.series || []).forEach(r => {{ names[r.series_id] = r.title; }});
  const on = d.enabled;
  const nxt = d.next ? `${{d.next.series}} · ch.${{d.next.chapter}}` : '—';
  const r0 = (d.recent || [])[0];
  const last = r0 ? `${{names[r0.series_id] || r0.series_id}} ch.${{r0.chapter}} · ${{r0.status.replace('_', ' ')}} · ${{apAgo(r0.updated_at)}}` : 'nothing yet';
  const wj = d.waiting_jobs || {{}};
  const waitingJobs = (wj.budget_paused || wj.interrupted)
    ? `<span>Waiting jobs</span><span>${{wj.budget_paused || 0}} paused by the cap · ${{wj.interrupted || 0}} cut off by a restart — see <a href="#logs" onclick="toggleDrawer('logs');return false">Logs</a></span>` : '';
  const sched = d.scheduler || {{}};
  const rows = (d.series || []).map(r => {{
    const [bg, fg, label] = AP_STATE[r.state] || AP_STATE.ready;
    const btns = [];
    if (r.state === 'paused') btns.push(`<button class="mini" onclick="apSeries('${{r.series_id}}','resume')" title="let autopilot pick this series again">▶ resume</button>`);
    else if (r.state !== 'no_source' && r.state !== 'up_to_date') btns.push(`<button class="mini" onclick="apSeries('${{r.series_id}}','pause')" title="autopilot skips this series until you resume it">⏸ pause</button>`);
    if (['stopped', 'blocked', 'cooldown'].includes(r.state)) btns.push(`<button class="mini" onclick="apSeries('${{r.series_id}}','retry')" title="make the chapter pickable again now">↻ retry</button>`);
    if (r.mirror) btns.push(`<button class="mini" onclick="apRunNow('${{r.series_id}}', '${{r.next || ''}}')" title="make one chapter of this series now (skips today's chapter limit, not the budget)">▶ make now</button>`);
    btns.push(`<button class="mini" onclick="bibleOpen('${{r.series_id}}')" title="who is who: the researched cast & world the AI writes with">📖 cast</button>`);
    const more = r.remaining && r.remaining.length ? ` · ${{r.remaining.length}} to make` : '';
    return `<div class="apser"><div style="min-width:0">
        <div style="display:flex;gap:6px;align-items:center;flex-wrap:wrap"><span class="hint">#${{r.rank || '—'}}</span>
          <b>${{r.title}}</b> ${{apPill(label, bg, fg)}}</div>
        <div class="hint">${{r.reason || ''}}${{r.start_from ? ' · from ch.' + r.start_from : ''}}${{more}} · made by autopilot: ${{r.made_by_autopilot}}</div></div>
      <div class="r">${{btns.join('')}}</div></div>`;
  }}).join('');
  box.innerHTML = `<div class="aph"><b>🤖 Autopilot</b>
      ${{on ? apPill('ON', 'var(--okb-bg)', 'var(--okb-ink)') : apPill('OFF', 'var(--gray-bg)', 'var(--gray-ink)')}}
      <button class="${{on ? 'danger' : 'primary'}} mini" onclick="apSet({{enabled: ${{!on}}}})">${{on ? '⏹ Switch off' : '▶ Switch on'}}</button>
      <button class="mini" onclick="apCheck()" title="re-read every series page now (free) and run one check">↻ check now</button></div>
    <div class="hint" style="margin-bottom:8px">Makes the next chapter of every Tracker series by itself — round robin,
      highest rank first, starting from each series' latest ${{(d.settings || {{}}).window || 3}} chapters, Gemini, one at a time.
      Finished chapters land in <a href="#projects" onclick="toggleDrawer('projects');return false">Projects</a> as “Ready for review”.</div>
    <div class="apr">
      <span>Next</span><span>${{on ? nxt : '— (off)'}}</span>
      <span>Waiting because</span><span>${{on ? (d.waiting || '— starting it now') : 'switched off'}}</span>
      <span>Today (ET)</span><span>${{d.today}} of
        <input id="apday" type="number" min="0" max="20" value="${{d.per_day}}" style="width:52px;padding:1px 4px"> chapters
        <button class="mini" onclick="apSet({{per_day: parseInt(document.getElementById('apday').value || '0', 10)}})">save</button>
        · ~$${{(d.estimate_usd || 0).toFixed(2)}} a chapter</span>
      <span>Spend today (ET)</span><span>autopilot $${{(d.ap_spent_usd || 0).toFixed(2)}} of
        $<input id="apbudget" type="number" min="0" max="100" step="0.5" value="${{d.budget_usd}}" style="width:60px;padding:1px 4px">
        <button class="mini" onclick="apSet({{budget_usd: parseFloat(document.getElementById('apbudget').value || '0')}})">save</button>
        · whole site $${{(d.spent_usd || 0).toFixed(2)}} of $${{(d.cap_usd || 0).toFixed(2)}} (hard stop)</span>
      <span>Last chapter</span><span>${{last}}</span>
      <span>Last check</span><span>${{apAgo(d.last_tick || sched.last_run)}}${{d.last_result ? ' · ' + d.last_result : ''}} · checks every ${{Math.round((d.tick_seconds || 600) / 60)}} min${{sched.running ? '' : ' · <b style="color:var(--bad)">scheduler not running</b>'}}${{sched.last_error ? ' · <span style="color:var(--bad)">' + sched.last_error + '</span>' : ''}}</span>
      ${{waitingJobs}}
      <span>Undo</span><span>${{d.undo}}</span>
    </div>
    <details style="margin-top:10px"${{wasOpen ? ' open' : ''}}><summary class="hint" style="cursor:pointer">All series, in the order autopilot serves them (${{(d.series || []).length}})</summary>
      <div style="margin:6px 0"><button class="mini" onclick="bibleResearchAll()" title="research every series that has no cast & world yet (~$0.02 each)">📖 research series without a cast list</button></div>${{rows}}</details>`;
  if (Math.abs(window.scrollY - keepY) > 1) window.scrollTo(0, keepY);
}}
async function apSet(patch) {{
  try {{ await j('/api/autopilot/settings', {{method:'POST', headers:{{'Content-Type':'application/json'}}, body: JSON.stringify(patch)}}); }}
  catch (e) {{ alert('Could not save: ' + (e.message || e)); }}
  loadAutopilot();
}}
async function apSeries(id, action) {{
  try {{ await j('/api/autopilot/series', {{method:'POST', headers:{{'Content-Type':'application/json'}}, body: JSON.stringify({{series_id: id, action}})}}); }}
  catch (e) {{ alert('Could not ' + action + ': ' + (e.message || e)); }}
  loadAutopilot();
}}
async function apRunNow(id, next) {{
  const ch = prompt('Which chapter should autopilot make now?', next || '');
  if (!ch) return;
  try {{
    const r = await j('/api/autopilot/run', {{method:'POST', headers:{{'Content-Type':'application/json'}},
      body: JSON.stringify({{series_id: id, chapter: ch.trim()}})}});
    alert('Queued ' + r.series + ' ch.' + r.chapter + ' — it starts when the line is free.');
  }} catch (e) {{ alert('Could not start it: ' + (e.message || e)); }}
  loadAutopilot();
}}
// ================= SERIES BIBLE (story research) =================
let BIBLE_SID = null, BIBLE = null;
async function bibleOpen(sid) {{
  BIBLE_SID = sid;
  const box = document.getElementById('bibleBody');
  box.innerHTML = 'loading…';
  document.getElementById('bibleSave').style.display = 'none';
  bibleDlg.showModal();
  let d;
  try {{ d = await j('/api/series/bible?series_id=' + encodeURIComponent(sid)); }}
  catch (e) {{ box.innerHTML = '<span style="color:var(--bad)">' + esc(e.message || e) + '</span>'; return; }}
  BIBLE = d.bible;
  const b = d.bible, st = d.status;
  if (!b) {{
    box.innerHTML = `<h3>${{esc(d.title)}}</h3><p class="hint">No cast &amp; world yet — chapters of this series are written without names or pronouns.
      Research it (~$0.02): sourced cast, pronouns, looks, factions and world.</p>` +
      (st ? `<p class="hint">Research: ${{esc(st.status)}} ${{esc(st.error || st.summary || '')}}</p>` : '');
    return;
  }}
  const r = b.research || {{}};
  const tiers = {{}}; (r.sources || []).forEach(x => {{ tiers[x.tier] = (tiers[x.tier] || 0) + 1; }});
  const ORIG = {{research: 'researched', owner: 'yours', undefined: 'hand-written'}};
  const chars = (b.characters || []).map(c => `<tr><td><b>${{esc(c.name)}}</b>${{(c.aliases || []).length ? '<br><span class="hint">' + esc(c.aliases.join(', ')) + '</span>' : ''}}</td>
      <td>${{esc(c.pronouns || c.gender || '')}}</td><td>${{esc(c.role || '')}}</td><td class="hint">${{esc(c.visual_cues || '')}}</td>
      <td class="hint">${{esc(ORIG[c.origin] || c.origin || 'hand-written')}}${{(c.source_tiers || []).length ? '<br>' + esc(c.source_tiers.join(', ')) : ''}}</td></tr>`).join('');
  const ws = b.world_setting || {{}};
  const src = (r.sources || []).map((x, i) => `<li><a href="${{esc(x.url)}}" target="_blank" rel="noreferrer">${{esc(x.domain || x.title)}}</a> <span class="hint">${{esc(x.tier)}}</span></li>`).join('');
  box.innerHTML = `<h3>📖 ${{esc(b.canonical_title || d.title)}} — cast &amp; world</h3>
    <p class="hint">${{r.at ? 'Researched ' + new Date(r.at * 1000).toLocaleString() + ' · ' + (r.sources || []).length + ' sources (' + Object.entries(tiers).map(([k, v]) => v + ' ' + k).join(', ') + ')' : 'Hand-written (no research yet)'}}
      · names and pronouns may come from fan wikis; world and story facts only from official or trusted sources. Your edits always win.</p>
    <div style="overflow-x:auto"><table style="font-size:12px;width:100%"><tr><th>Character</th><th>Pronouns</th><th>Role</th><th>Look</th><th>From</th></tr>${{chars}}</table></div>
    <p><b>World:</b> ${{esc([ws.universe, ws.premise].filter(Boolean).join(' — ') || '—')}}${{ws.costume_vs_monster_rule ? '<br><b>Rule:</b> ' + esc(ws.costume_vs_monster_rule) : ''}}</p>
    ${{r.story_so_far ? '<p><b>Story so far' + (r.story_verified ? '' : ' (unverified — not used in scripts)') + ':</b> ' + esc(r.story_so_far) + '</p>' : ''}}
    ${{(r.disputes || []).length ? '<p><b>⚠ Sources disagree:</b><br>' + r.disputes.map(esc).join('<br>') + '</p>' : ''}}
    ${{(b.suggested_characters || []).length ? '<p><b>Names our scripts use that aren’t listed yet:</b> ' + b.suggested_characters.map(esc).join(', ') + ' <span class="hint">(add them with ✏️ edit)</span></p>' : ''}}
    ${{src ? '<details><summary class="hint" style="cursor:pointer">Sources</summary><ol style="font-size:12px">' + src + '</ol></details>' : ''}}
    <textarea id="bibleText" style="display:none;width:100%;height:320px;font:12px ui-monospace,monospace"></textarea>`;
}}
async function bibleResearch() {{
  if (!BIBLE_SID) return;
  try {{
    await j('/api/series/research', {{method:'POST', headers:{{'Content-Type':'application/json'}}, body: JSON.stringify({{series_id: BIBLE_SID}})}});
    document.getElementById('bibleBody').innerHTML = '<p class="hint">Researching… (about a minute). It shows in Logs; reopen 📖 when done.</p>';
  }} catch (e) {{ alert('Could not start research: ' + (e.message || e)); }}
}}
async function bibleResearchAll() {{
  try {{
    const r = await j('/api/series/research', {{method:'POST', headers:{{'Content-Type':'application/json'}}, body: JSON.stringify({{missing_only: true}})}});
    alert(r.job ? 'Researching ' + r.series.length + ' series, one at a time — progress in Logs.' : r.note);
  }} catch (e) {{ alert('Could not start research: ' + (e.message || e)); }}
}}
function bibleEdit() {{
  const t = document.getElementById('bibleText');
  if (!t) return;
  t.value = JSON.stringify(BIBLE || {{canonical_title: '', characters: [], world_setting: {{}}}}, null, 2);
  t.style.display = 'block';
  document.getElementById('bibleSave').style.display = '';
}}
async function bibleSaveEdit() {{
  let b;
  try {{ b = JSON.parse(document.getElementById('bibleText').value); }}
  catch (e) {{ alert('That is not valid JSON: ' + e.message); return; }}
  try {{
    await j('/api/series/bible', {{method:'POST', headers:{{'Content-Type':'application/json'}}, body: JSON.stringify({{series_id: BIBLE_SID, bible: b}})}});
    bibleOpen(BIBLE_SID);
  }} catch (e) {{ alert('Could not save: ' + (e.message || e)); }}
}}
// ================= SETTINGS & CHANNELS =================
async function loadSettings() {{
  const box = document.getElementById('setbox');
  let d;
  try {{ d = await j('/api/settings/overview'); }}
  catch (e) {{ box.innerHTML = '<span style="color:var(--bad)">' + esc(e.message || e) + '</span>'; return; }}
  const R = (k, v) => `<div class="r"><span>${{k}}</span><span>${{v}}</span></div>`;
  const sch = d.connection.scheduler || {{}};
  const ch = d.channels, defs = ch.defaults || {{}};
  const accts = (ch.accounts || []).map(a => `<label style="display:flex;gap:6px;align-items:center;padding:2px 0">
      <input type="checkbox" class="settgt" value="${{esc(a.account_id)}}" ${{(defs.targets || []).includes(a.account_id) ? 'checked' : ''}}>
      ${{a.network === 'youtube' ? '▶️' : '•'}} ${{esc(a.username || a.account_id)}} <span class="hint">${{esc(a.account_id)}}${{a.active ? '' : ' · inactive'}}</span></label>`).join('');
  const st = d.storage, disk = st.disk || {{}};
  box.innerHTML = `
    <div class="setcard"><h4>🟢 Connection</h4>
      ${{R('Server', 'live · deploy ' + esc(d.connection.commit || 'local'))}}
      ${{R('Scheduler', sch.running ? 'running · last check ' + (sch.last_run ? apAgo(sch.last_run) : 'not yet') : '<b style="color:var(--bad)">not running</b>')}}
      ${{sch.last_error ? R('Last error', '<span style="color:var(--bad)">' + esc(sch.last_error) + '</span>') : ''}}</div>
    <div class="setcard"><h4>💰 Spending</h4>
      ${{R('Whole site today', '$' + d.spending.today.toFixed(2) + ' of $' + (d.spending.cap || 0).toFixed(2) + ' (hard stop)')}}
      ${{R('Autopilot today', '$' + (d.spending.autopilot_spent || 0).toFixed(2) + ' of $' + (d.spending.autopilot_budget || 0).toFixed(2))}}
      ${{R('Prices', "Google's published rates, read " + esc(d.spending.prices_read))}}
      <div class="hint" style="margin-top:4px">Change the autopilot budget on the Autopilot card (Ingest). The $${{(d.spending.cap || 0).toFixed(0)}} site cap is the Railway setting MAX_DAILY_SPEND_USD. Details: Logs → 💰 Spend.</div></div>
    <div class="setcard"><h4>🤖 Autopilot</h4>
      ${{R('State', d.autopilot.enabled ? '<b>ON</b>' : 'OFF')}}
      ${{R('Chapters a day', esc(d.autopilot.per_day))}}
      ${{R('Starts each series at', 'its latest ' + esc(d.autopilot.window) + ' chapters, then in order')}}
      ${{R('Model / tier', esc(d.autopilot.model) + ' · ' + esc(d.autopilot.tier) + ' (half price)')}}
      <div class="hint" style="margin-top:4px"><a href="#ingest" onclick="toggleDrawer('ingest');return false">Switch and limits</a> · <a href="#tracker" onclick="toggleDrawer('tracker');return false">Series board</a></div></div>
    <div class="setcard"><h4>🔗 Channels</h4>
      ${{R('Upload-Post', esc(ch.status.detail || ch.status.state || ''))}}
      <div class="hint" style="margin:6px 0 2px">New videos post to:</div>${{accts || '<div class="hint">no connected accounts</div>'}}
      <div style="display:flex;gap:6px;align-items:center;margin-top:6px">Privacy
        <select id="setpriv">${{['public', 'unlisted', 'private'].map(p => `<option ${{defs.privacy === p ? 'selected' : ''}}>${{p}}</option>`).join('')}}</select>
        <button class="mini" onclick="saveChannels()">save</button></div>
      <div class="hint" style="margin-top:4px">Videos stay private until you save “public” here. You still press Publish on each video; the posting schedule is off.</div></div>
    <div class="setcard"><h4>🎬 Export</h4>
      ${{R('Video speed', d.export.speed + '×' + (d.export.env_override ? ' (set by Railway EXPORT_SPEED)' : ''))}}
      <div style="display:flex;gap:6px;align-items:center;margin-top:6px">Speed
        <select id="setspeed" ${{d.export.env_override ? 'disabled' : ''}}>${{[1.0, 1.1, 1.25, 1.5].map(v => `<option value="${{v}}" ${{Math.abs(v - d.export.speed) < 1e-6 ? 'selected' : ''}}>${{v}}×</option>`).join('')}}</select>
        <button class="mini" onclick="saveSpeed()">save</button></div>
      <div class="hint" style="margin-top:4px">Approve renders and exports at this speed; only that file is kept.</div></div>
    <div class="setcard"><h4>🎙️ Voice</h4>
      ${{R('Studio narrator', esc(d.voice.voice || 'Charon') + ' · ' + esc(d.voice.model || ''))}}
      ${{R('Style', esc(d.voice.style || '—'))}}
      <div class="hint" style="margin-top:4px">New chapters use it; approving an older chapter re-voices it in this voice first. <a href="#ingest" onclick="toggleDrawer('ingest');return false">Change on Ingest</a>.</div></div>
    <div class="setcard"><h4>🗄️ Storage &amp; retention</h4>
      ${{R('Disk', disk.used_gb != null ? disk.used_gb + ' GB of ' + disk.total_gb + ' GB' : '?')}}
      ${{R('Projects', st.n_projects)}}
      ${{(st.projects || []).slice(0, 5).map(p => R(esc(p.name), p.mb + ' MB')).join('')}}
      ${{R('Exports kept', st.exports_kept_days + ' days')}}
      ${{R('Published chapters', 'archived, deleted ' + st.archive_days + ' days later unless Keep')}}</div>
    <div class="setcard"><h4>💾 Backups</h4>
      <div class="hint">Railway keeps volume backups, but a restore replaces the <b>whole</b> disk (every project at once). To keep one chapter safe, download it:</div>
      ${{(st.projects || []).slice(0, 5).map(p => `<div class="r"><span>${{esc(p.name)}}</span><a href="/api/backup/${{encodeURIComponent(p.id)}}">download</a></div>`).join('')}}</div>`;
}}
async function saveChannels() {{
  const targets = Array.from(document.querySelectorAll('.settgt')).filter(c => c.checked).map(c => c.value);
  try {{ await j('/api/settings', {{method:'POST', headers:{{'Content-Type':'application/json'}},
    body: JSON.stringify({{publish: {{targets, privacy: document.getElementById('setpriv').value}}}})}}); loadSettings(); }}
  catch (e) {{ alert('Could not save: ' + (e.message || e)); }}
}}
async function saveSpeed() {{
  try {{ await j('/api/settings', {{method:'POST', headers:{{'Content-Type':'application/json'}},
    body: JSON.stringify({{export_speed: parseFloat(document.getElementById('setspeed').value)}})}}); loadSettings(); }}
  catch (e) {{ alert('Could not save: ' + (e.message || e)); }}
}}
async function apCheck() {{
  try {{ await j('/api/autopilot/check', {{method:'POST'}}); }}
  catch (e) {{ alert('Could not start a check: ' + (e.message || e)); }}
  setTimeout(loadAutopilot, 5000);
}}

// ================= PROJECTS (review inbox) =================
var PROJ_FILTER = 'all';
const PROJ_STATUS = {{
  ready: ['Ready for review', 'var(--warnb-bg)', 'var(--warn)'], approved: ['Approved', 'var(--sa-bg)', 'var(--sa-ink)'],
  rendering: ['Rendering', 'var(--sa-bg)', 'var(--sa-ink)'], rendered: ['Rendered', 'var(--okb-bg)', 'var(--okb-ink)'],
  published: ['Published', 'var(--okb-bg)', 'var(--okb-ink)'], archived: ['Archived', 'var(--gray-bg)', 'var(--gray-ink)']
}};
function projFilter(f) {{ PROJ_FILTER = f; loadProjects(); }}
async function projArchive(id, action) {{
  try {{ await j('/api/projects/archive', {{method:'POST', headers:{{'Content-Type':'application/json'}}, body: JSON.stringify({{id, action}})}}); }}
  catch (e) {{ alert('Could not ' + action + ': ' + (e.message || e)); }}
  loadProjects();
}}
function projNavCount(n) {{
  const tr = document.querySelector('.navitem[data-d="projects"] .tr');
  if (!tr) return;
  let b = tr.querySelector('.bd.projready');
  if (!n) {{ if (b) b.remove(); return; }}
  if (!b) {{ b = document.createElement('span'); b.className = 'bd projready'; tr.appendChild(b); }}
  b.textContent = n + ' to review';
}}
async function loadProjects() {{
  const box = document.getElementById('projlist');
  const d = await j('/api/projects');
  let htmlOut = '';
  if ((d.in_progress || []).length) {{
    htmlOut += '<div class="hint" style="text-transform:uppercase;font-weight:600;margin:4px 0">In progress</div>' +
      d.in_progress.map(p => `<div class="projrow">⏳ ${{p.slug}} ${{p.source === 'autopilot' ? apPill('autopilot', 'var(--gray-bg)', 'var(--gray-ink)') : ''}} <span class="hint">${{p.stage}} · ${{p.pct}}%</span>
        <button onclick="setActiveJob('${{p.job}}');toggleDrawer('ingest');startIngestPoller()">Watch</button></div>`).join('');
  }}
  const all = d.projects || [];
  const count = st => all.filter(p => st === 'all' ? p.review_status !== 'archived' : p.review_status === st).length;
  projNavCount(count('ready'));
  const tabs = [['all', 'All'], ['ready', 'Ready for review'], ['approved', 'Approved'], ['rendered', 'Rendered'],
                ['published', 'Published'], ['archived', 'Archived']];
  htmlOut += `<div class="segtabs" style="margin-top:8px">${{tabs.map(([k, l]) =>
    `<button type="button" class="${{PROJ_FILTER === k ? 'on' : ''}}" onclick="projFilter('${{k}}')">${{l}} (${{count(k)}})</button>`).join('')}}</div>`;
  const shown = all.filter(p => PROJ_FILTER === 'all' ? (p.review_status !== 'archived' || p.active)
                                                     : p.review_status === PROJ_FILTER);
  const grouped = {{}};
  shown.forEach(p => {{
    const s = p.series || 'Other';
    (grouped[s] = grouped[s] || []).push(p);
  }});
  for (const series in grouped) {{
    htmlOut += `<div class="projcard"><div class="ph">📚 ${{series}}</div>`;
    grouped[series].forEach(p => {{
      // a Claude-lab copy and the normal chapter used to read identically
      const lab = /-lab-/.test(p.id) ? (' · ' + (p.engine === 'claude' ? 'Claude lab' : 'lab')) : '';
      const label = (p.chapter ? ('Chapter ' + p.chapter) : p.id) + lab;
      const ps = PROJ_STATUS[p.review_status];
      const tags = (ps ? apPill(ps[0], ps[1], ps[2]) : '') + (p.auto ? ' ' + apPill('auto', 'var(--gray-bg)', 'var(--gray-ink)') : '') +
        (p.video_missing ? ' <span class="hint" style="color:var(--warn)" title="the exported video was deleted; open it and press Approve to export again (clips are reused)">no video — open and approve to export again</span>' : '') +
        (p.checks ? ` <span class="hint" title="free story check found these — open Check after opening the chapter">${{p.checks}} to check</span>` : '');
      const arch = p.archive;
      const archInfo = arch ? (arch.keep ? ' <span class="hint">kept</span>'
        : ` <span class="hint" title="the folder is deleted then, to free space; Keep cancels">deletes in ${{arch.days_left}} day(s)</span>`) : '';
      const archBtns = arch ? `${{arch.keep ? '' : `<button class="mini" onclick="projArchive('${{p.id}}','keep')" title="never delete this archived chapter">Keep</button>`}}
        <button class="mini" onclick="projArchive('${{p.id}}','unarchive')" title="back to the main list; cancels the delete">Unarchive</button>` : '';
      const _sel = (p.active || p.id === 'chapter-2 (current)')
        ? '<span style="display:inline-block;width:16px"></span>'
        : `<input type="checkbox" class="projsel" value="${{p.id}}" onchange="updateProjSel()" title="select for bulk delete">`;
      htmlOut += `<div class="projrow"><span>${{_sel}} ${{p.active ? '▶ ' : ''}}${{label}} ${{tags}}${{archInfo}} <span class="hint">(${{p.n_segments}} segs${{p.duration ? ' · ' + p.duration + 's' : ''}})</span></span>
        <span>${{archBtns}}${{p.active ? '<span class="hint">active</span>' : `<button onclick="activateProj('${{p.id}}')">Open</button>`}}
        ${{p.active || p.id === 'chapter-2 (current)' ? '' : `<button title="delete this project and everything in it" onclick="delProject('${{p.id}}')" style="border:1px solid var(--rule);color:var(--bad);background:none;border-radius:3px;cursor:pointer;padding:1px 6px">🗑</button>`}}</span></div>`;
    }});
    htmlOut += '</div>';
  }}
  const bar = `<div class="projbulk" style="display:flex;gap:8px;align-items:center;margin:4px 0 8px;font-size:12px">
      <label style="cursor:pointer"><input type="checkbox" id="projall" onchange="toggleAllProj(this)"> select all</label>
      <button id="projdelbtn" onclick="delSelectedProjects()" disabled
        style="border:1px solid var(--rule);color:var(--bad);background:none;border-radius:3px;cursor:pointer;padding:2px 8px">
        🗑 delete selected (<span id="projseln">0</span>)</button>
      <span class="hint">the open project cannot be deleted</span>
    </div>`;
  box.innerHTML = htmlOut ? (bar + htmlOut) : 'No projects yet.';
  updateProjSel();
}}
async function refreshProjCount() {{
  try {{
    const d = await j('/api/projects');
    projNavCount((d.projects || []).filter(p => p.review_status === 'ready').length);
  }} catch (e) {{}}
}}
function _projChecked() {{
  return Array.from(document.querySelectorAll('.projsel')).filter(c => c.checked).map(c => c.value);
}}
function updateProjSel() {{
  const n = _projChecked().length;
  const el = document.getElementById('projseln'); if (el) el.textContent = n;
  const b = document.getElementById('projdelbtn'); if (b) b.disabled = n === 0;
}}
function toggleAllProj(src) {{
  document.querySelectorAll('.projsel').forEach(c => {{ c.checked = src.checked; }});
  updateProjSel();
}}
async function delSelectedProjects() {{
  const ids = _projChecked();
  if (!ids.length) return;
  if (!confirm('DELETE ' + ids.length + ' project(s): ' + ids.join(', ') + ' — this removes their crops, audio, clips and exports permanently and cannot be undone.')) return;
  if (!confirm('Really delete ' + ids.length + ' project(s)? Last chance.')) return;
  try {{
    const r = await j('/api/projects/delete', {{method:'POST', headers:{{'Content-Type':'application/json'}},
      body: JSON.stringify({{ids}})}});
    let msg = 'Deleted ' + (r.deleted || []).length + ' project(s), freed ' + (r.freed_mb || 0) + ' MB';
    if ((r.skipped || []).length) msg += '. Skipped: ' + r.skipped.map(x => x.id + ' (' + x.reason + ')').join('; ');
    alert(msg);
    loadProjects();
  }} catch (e) {{ alert('Bulk delete failed: ' + (e.message || e)); }}
}}
async function delProject(id) {{
  if (!confirm('DELETE project ' + id + '?\\n\\nThis removes its crops, audio, clips and exports permanently. It cannot be undone.')) return;
  if (!confirm('Really delete ' + id + '? Last chance.')) return;
  try {{
    const r = await j('/api/projects/delete', {{method:'POST', headers:{{'Content-Type':'application/json'}},
      body: JSON.stringify({{id}})}});
    alert('Deleted ' + id + ' — freed ' + (r.freed_mb || 0) + ' MB');
    loadProjects();
  }} catch (e) {{ alert('Delete failed: ' + (e.message || e)); }}
}}
// ================= WATCHLIST =================
// The planning layer. loadTracker() below is the library layer and is
// untouched: it answers "what is new in what I own", this answers "what
// should I make next, and where can it be read".
var WL = {{series: []}};
var WLCH = {{}};      // series_id -> {{series_key: chapter payload}}

function trkTab(which) {{
  // Release calendar + Watchlist merged into ONE Series board (owner,
  // 2026-10-04); "New chapters" is now a filter on it. Manage list keeps the
  // add-a-title / sources / tier / rank tools.
  const w = which === 'watch';
  document.getElementById('trk_watch').style.display = w ? '' : 'none';
  document.getElementById('trk_series').style.display = w ? 'none' : '';
  document.getElementById('tkw').classList.toggle('on', w);
  document.getElementById('tks').classList.toggle('on', !w);
  if (w) {{ if (!WL.series.length) wlLoad(); }} else sbLoad();
}}

/* ---- SERIES BOARD ---- */
let SB = {{ data: null, f: 'all', sort: 'release' }};
async function sbLoad() {{
  const grid = document.getElementById('sbgrid');
  try {{ SB.data = await j('/api/series/board'); }}
  catch (e) {{ grid.innerHTML = '<div class="hint">Could not load the series: ' + esc(e.message || e) + '</div>'; return; }}
  if (!(WL.series || []).length) {{ try {{ WL = await j('/api/watchlist'); }} catch (e) {{}} }}
  sbPaint();
}}
function sbFilter(f, btn) {{
  SB.f = f;
  document.querySelectorAll('#sbfilters button').forEach(b => b.classList.toggle('on', b === btn));
  sbPaint();
}}
function sbSort(k, btn) {{
  SB.sort = k;
  btn.parentNode.querySelectorAll('button').forEach(b => b.classList.toggle('on', b === btn));
  sbPaint();
}}
function sbMatch(x) {{
  if (SB.f === 'ready') return x.state === 'ready';
  if (SB.f === 'new') return (x.new_since_made || []).length > 0;
  if (SB.f === 'attn') return ['blocked', 'stopped', 'no_source'].includes(x.state);
  if (SB.f === 'paused') return x.state === 'paused';
  if (SB.f === 'done') return x.state === 'up_to_date';
  return true;
}}
function sbPaint() {{
  const grid = document.getElementById('sbgrid');
  const all = (SB.data && SB.data.series) || [];
  if (!all.length) {{ grid.innerHTML = '<div class="hint" style="grid-column:1/-1">No series yet — add one under ✏️ Manage list.</div>'; return; }}
  const list = all.filter(sbMatch).slice();
  if (SB.sort === 'release') list.sort((a, b) => (b.latest_date || '') < (a.latest_date || '') ? -1 : (b.latest_date || '') > (a.latest_date || '') ? 1 : 0);
  if (!list.length) {{ grid.innerHTML = '<div class="hint" style="grid-column:1/-1">Nothing matches this filter.</div>'; return; }}
  const [rbg, rfg] = ['var(--okb-bg)', 'var(--okb-ink)'];
  grid.innerHTML = list.map(x => {{
    const st = AP_STATE[x.state] || AP_STATE.ready;
    const made = x.made || [];
    const left = x.left_in_plan || [];
    const plan = x.next
      ? `Made: <b>${{made.length ? esc(made.slice(-3).join(', ')) + (made.length > 3 ? ' +' + (made.length - 3) : '') : 'none yet'}}</b> · Next: <b>ch.${{esc(x.next)}}</b>${{left.length > 1 ? ' · ' + (left.length - 1) + ' more in the plan' : ''}}`
      : esc(x.reason || '');
    const bib = x.bible
      ? `📖 ${{x.bible.characters}} characters${{x.bible.disputes ? ' · ' + x.bible.disputes + ' disputes' : ''}}${{x.bible.suggested ? ' · ' + x.bible.suggested + ' new names' : ''}}`
      : '<span style="color:var(--warn)">📖 no cast list yet — research it</span>';
    const paused = x.state === 'paused';
    return `<div class="calcard">
      ${{x.cover ? '<img class="calcover" loading="lazy" alt="" src="/api/watchlist/cover/' + encodeURIComponent(x.id) + '" onerror="this.outerHTML=&quot;<div class=calcover></div>&quot;">' : '<div class="calcover"></div>'}}
      <b>${{esc(x.title)}}</b>
      <span class="calmeta">${{esc(x.tier_label || '')}}${{x.rank ? ' · #' + x.rank : ''}} · ${{esc(x.source || '')}}</span>
      <span class="calmeta">Latest ch.${{esc(x.latest || '?')}}${{x.latest_date ? ' · ' + (x.latest_approx ? '≈ ' : '') + calDay(x.latest_date) : ''}}${{(x.new_since_made || []).length ? ' · <b>' + x.new_since_made.length + ' new since last made</b>' : ''}}</span>
      <span>${{apPill(st[2], st[0], st[1])}}</span>
      <span class="calmeta">${{plan}}</span>
      ${{x.earlier_not_planned ? '<span class="calmeta">Earlier chapters not planned: ' + x.earlier_not_planned + '</span>' : ''}}
      <span class="calmeta">${{bib}}</span>
      <div style="display:flex;gap:4px;flex-wrap:wrap">
        <button class="mini" onclick="apRunNow('${{esc(x.id)}}', '${{esc(x.next || '')}}')" title="make one chapter now (skips today's limit, not the budget)">▶ make now</button>
        <button class="mini" onclick="apSeries('${{esc(x.id)}}', '${{paused ? 'resume' : 'pause'}}').then(sbLoad)">${{paused ? '▶ resume' : '⏸ pause'}}</button>
        ${{['blocked', 'stopped', 'cooldown'].includes(x.state) ? '<button class="mini" onclick="apSeries(&quot;' + esc(x.id) + '&quot;, &quot;retry&quot;).then(sbLoad)">↻ retry</button>' : ''}}
        <button class="mini" onclick="bibleOpen('${{esc(x.id)}}')">📖 cast</button>
        <button class="mini" onclick="sbChapters('${{esc(x.id)}}', this)">chapters ▾</button>
      </div>
      <div id="sbch_${{esc(x.id)}}"></div>
    </div>`;
  }}).join('');
}}
async function sbChapters(sid, btn) {{
  const box = document.getElementById('sbch_' + sid);
  if (box.innerHTML) {{ box.innerHTML = ''; return; }}
  box.innerHTML = '<span class="hint">loading chapters…</span>';
  let d;
  try {{ d = await j('/api/series/chapters?series_id=' + encodeURIComponent(sid)); }}
  catch (e) {{ box.innerHTML = '<span class="hint">' + esc(e.message || e) + '</span>'; return; }}
  const x = ((SB.data && SB.data.series) || []).find(s => s.id === sid) || {{}};
  const pick = x.next || (d.chapters[0] || {{}}).ch;
  const opts = d.chapters.map(c => '<option value="' + esc(c.ch) + '"' + (c.ch === pick ? ' selected' : '') + '>Ch.' + esc(c.ch) +
    (c.date ? ' · ' + (c.approx ? '≈ ' : '') + calDay(c.date) : '') + (c.made ? ' · ✓ made' : '') + '</option>').join('');
  box.innerHTML = `<select class="calpick" id="sbpick_${{esc(sid)}}">${{opts}}</select>
    <div style="display:flex;gap:4px;flex-wrap:wrap">
      <button class="primary mini" onclick="wlIngest('${{esc(sid)}}', '${{esc(d.series_key || '')}}', document.getElementById('sbpick_${{esc(sid)}}').value)">Ingest this chapter</button>
      <button class="mini" onclick="sbBackfill('${{esc(sid)}}')" title="autopilot will make the back catalogue from the picked chapter, in story order">plan backfill from here</button>
    </div>`;
}}
async function sbBackfill(sid) {{
  const ch = document.getElementById('sbpick_' + sid).value;
  if (!confirm('Plan this series from ch.' + ch + '? Autopilot will make ch.' + ch + ' onward, in story order, before newer chapters of this series.')) return;
  try {{
    await j('/api/autopilot/backfill', {{method:'POST', headers:{{'Content-Type':'application/json'}}, body: JSON.stringify({{series_id: sid, from_chapter: ch}})}});
    sbLoad();
  }} catch (e) {{ alert('Could not plan the backfill: ' + (e.message || e)); }}
}}

/* ---- release calendar (the Scrapper studio's calendar, for chapters) ----
   Chapters grouped by the month they came out, newest first, as cover cards
   with an Ingest button. Dates come from each series page (watchlist check);
   "≈" marks a date the site only gave as "3 days ago". */
let CAL = {{ month: 'all', st: 'all', items: [] }};
function esc(x) {{
  return String(x == null ? '' : x).replace(/[&<>"']/g, function (c) {{
    return {{ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }}[c]; }});
}}
/* ONE card per series (owner, 2026-10-02): cover, latest release, how many
   chapters are still unmade, and a dropdown of every chapter (newest first,
   with its date and whether it is made) to ingest the one picked. Cards are
   grouped by the month of the series' latest release. */
async function calLoad() {{
  const grid = document.getElementById('calgrid');
  try {{ WL = await j('/api/watchlist'); }} catch (e) {{
    grid.innerHTML = '<div class="hint">Could not load the watchlist: ' + esc(e.message || e) + '</div>'; return; }}
  const items = [];
  (WL.series || []).forEach(function (sx) {{
    const made = {{}};
    (sx.mirrors || []).forEach(function (m) {{ (m.ingested || []).forEach(function (c) {{ made[String(c.chapter)] = true; }}); }});
    const key = sx.preferred_mirror || sx.best_mirror;
    const m = (sx.mirrors || []).find(function (x) {{ return x.series_key === key; }}) || (sx.mirrors || [])[0];
    if (!m) return;
    const dates = m.release_dates || {{}};
    const chs = (m.chapters || Object.keys(dates)).map(String)
      .sort(function (a, b) {{ return parseFloat(b) - parseFloat(a); }});
    if (!chs.length) return;
    const latest = chs[0], ld = dates[latest];
    const unmade = chs.filter(function (c) {{ return !made[c]; }});
    // pre-select the next chapter to make: the first unmade one after the
    // highest made chapter, else the latest unmade, else the latest
    const madeNums = chs.filter(function (c) {{ return made[c]; }}).map(parseFloat);
    const top = madeNums.length ? Math.max.apply(null, madeNums) : -Infinity;
    const after = unmade.filter(function (c) {{ return parseFloat(c) > top; }});
    const pick = after.length ? after[after.length - 1] : (unmade[0] || latest);
    items.push({{ sid: sx.id, title: sx.title, key: m.series_key, src: m.label || m.source,
                 tier: sx.tier_label || '', cover: !!m.cover, chs: chs, dates: dates, made: made,
                 latest: latest, day: ld ? ld[0] : '', approx: ld ? !!ld[1] : false,
                 unmade: unmade.length, pick: pick }});
  }});
  items.sort(function (a, b) {{ return (b.day || '') < (a.day || '') ? -1 : (b.day || '') > (a.day || '') ? 1 : 0; }});
  CAL.items = items;
  const months = Array.from(new Set(items.filter(function (i) {{ return i.day; }}).map(function (i) {{ return i.day.slice(0, 7); }})));
  document.getElementById('calmonths').innerHTML = '<span class="hint">Latest release:</span>' +
    ['all'].concat(months).map(function (mo) {{
      return '<button type="button" class="' + (CAL.month === mo ? 'on' : '') + '" data-v="' + mo + '" onclick="calFilter(&quot;month&quot;, this.dataset.v, this)">' +
        (mo === 'all' ? 'All' : calMonth(mo).split(' ')[0]) + '</button>';
    }}).join('');
  calPaint();
}}
function calMonth(mo) {{
  return new Date(mo + '-15T12:00:00Z').toLocaleString('en-US', {{ month: 'long', year: 'numeric' }});
}}
function calDay(d) {{
  return new Date(d + 'T12:00:00Z').toLocaleDateString('en-US', {{ month: 'short', day: 'numeric', year: 'numeric' }});
}}
function calFilter(kind, val, btn) {{
  CAL[kind] = val;
  btn.parentNode.querySelectorAll('button').forEach(function (b) {{ b.classList.toggle('on', b === btn); }});
  calPaint();
}}
function calPaint() {{
  const grid = document.getElementById('calgrid');
  if (!CAL.items.length) {{
    grid.innerHTML = '<div class="hint" style="grid-column:1/-1">Nothing on the watchlist has chapters yet. Press <b>↻ Check all series</b>.</div>';
    return;
  }}
  const list = CAL.items.filter(function (i) {{
    return (CAL.month === 'all' || (i.day || '').slice(0, 7) === CAL.month) &&
           (CAL.st === 'all' || (CAL.st === 'new') === (i.unmade > 0));
  }});
  if (!list.length) {{ grid.innerHTML = '<div class="hint" style="grid-column:1/-1">Nothing matches these filters.</div>'; return; }}
  let html = '', prev = null;
  list.forEach(function (i) {{
    const mo = i.day ? i.day.slice(0, 7) : '';
    if (mo !== prev) {{ html += '<div class="calmonth">' + (mo ? calMonth(mo) : 'No release dates yet') + '</div>'; prev = mo; }}
    const opts = i.chs.map(function (c) {{
      const d = i.dates[c];
      return '<option value="' + esc(c) + '"' + (c === i.pick ? ' selected' : '') + '>' +
        'Ch.' + esc(c) + (d ? ' · ' + (d[1] ? '≈ ' : '') + calDay(d[0]) : '') + (i.made[c] ? ' · ✓ made' : '') + '</option>';
    }}).join('');
    html += '<div class="calcard">' +
      (i.cover ? '<img class="calcover" loading="lazy" alt="" src="/api/watchlist/cover/' + encodeURIComponent(i.sid) +
        '" onerror="this.outerHTML=&quot;<div class=calcover></div>&quot;">' : '<div class="calcover"></div>') +
      '<b>' + esc(i.title) + '</b>' +
      '<span class="calmeta">Latest Ch.' + esc(i.latest) + (i.day ? ' · ' + (i.approx ? '≈ ' : '') + calDay(i.day) : '') + ' · ' + esc(i.src) + '</span>' +
      (i.unmade ? '<span class="calst new">● ' + i.unmade + ' not made yet</span>' : '<span class="calst made">✓ all made</span>') +
      '<select class="calpick" id="calpick_' + esc(i.sid) + '">' + opts + '</select>' +
      '<button type="button" class="primary" data-sid="' + esc(i.sid) + '" data-key="' + esc(i.key) + '" onclick="calIngest(this)">Ingest chapter</button>' +
      '</div>';
  }});
  grid.innerHTML = html;
}}
function calIngest(btn) {{
  const sel = document.getElementById('calpick_' + btn.dataset.sid);
  if (sel && sel.value) wlIngest(btn.dataset.sid, btn.dataset.key, sel.value);
}}
async function calCheckAll() {{
  const b = document.getElementById('calcheck'), note = document.getElementById('calnote');
  b.disabled = true; note.textContent = 'checking every series… (one page each)';
  try {{
    const r = await j('/api/watchlist/refresh_all', {{ method: 'POST' }});
    note.textContent = 'checked ' + r.checked + ' series' + (r.failed.length ? ' · ' + r.failed.length + ' could not be read' : '');
  }} catch (e) {{ note.textContent = 'check failed: ' + (e.message || e); }}
  b.disabled = false;
  sbLoad();
}}

async function wlLoad() {{
  const box = document.getElementById('wllist');
  try {{
    WL = await j('/api/watchlist');
    const srcs = new Set();
    WL.series.forEach(sx => (sx.mirrors || []).forEach(m => srcs.add(m.source)));
    const sel = document.getElementById('wlSrcF');
    const cur = sel.value;
    sel.innerHTML = '<option value="">all sources</option>' +
      Array.from(srcs).sort().map(x => `<option value="${{x}}">${{x}}</option>`).join('');
    sel.value = cur;
    wlRender();
  }} catch (e) {{ box.innerHTML = 'Failed to load the watchlist: ' + (e.message || e); }}
}}

function _supBadge(level) {{
  // Honest about what each source can actually do, so nothing silently
  // half-works the way a WEBTOON URL used to.
  const map = {{supported: ['var(--ok)', 'full support'],
                partial: ['var(--warn)', 'partial — chapter list may be incomplete'],
                fallback: ['var(--warn)', 'generic fallback — may not extract cleanly'],
                unsupported: ['var(--bad)', 'no reader for this site yet']}};
  const [c, t] = map[level] || ['var(--bad)', level];
  return `<span title="${{t}}" style="color:${{c}};font-size:11px">●&nbsp;${{level}}</span>`;
}}

function wlRender() {{
  const box = document.getElementById('wllist');
  const q = (document.getElementById('wlQ').value || '').toLowerCase().trim();
  const tier = document.getElementById('wlTierF').value;
  const src = document.getElementById('wlSrcF').value;
  const sort = document.getElementById('wlSort').value;
  const onlyIng = document.getElementById('wlOnlyIng').checked;

  let rows = (WL.series || []).filter(sx => {{
    if (tier && sx.tier !== tier) return false;
    if (onlyIng && !sx.ingestable) return false;
    if (src && !(sx.mirrors || []).some(m => m.source === src)) return false;
    if (!q) return true;
    const hay = [sx.title, ...(sx.aliases || []), ...(sx.keywords || [])].join(' ').toLowerCase();
    return hay.indexOf(q) >= 0;
  }});
  if (sort === 'backlog') rows.sort((a, b) => (b.backlog || 0) - (a.backlog || 0));
  else if (sort === 'new') rows.sort((a, b) => (b.unmade_count || 0) - (a.unmade_count || 0));
  else if (sort === 'title') rows.sort((a, b) => a.title.localeCompare(b.title));

  if (!rows.length) {{
    box.innerHTML = (WL.series || []).length
      ? 'Nothing matches those filters.'
      : 'Nothing on the watchlist yet — add a title above, or seed the research.';
    return;
  }}
  const tcol = {{greenlight: 'var(--ok)', high_upside: 'var(--warn)', watchlist: 'var(--fg2)'}};
  box.innerHTML = rows.map(sx => {{
    // Comes from the server as a SET difference, not backlog minus count —
    // subtracting counts reports "up to date" while a middle chapter is
    // missing. Never recompute it here.
    const unmade = sx.unmade_count || 0;
    const mirrors = (sx.mirrors || []).map(m => {{
      const pref = m.series_key === sx.preferred_mirror;
      return `<div style="display:flex;gap:6px;align-items:center;padding:3px 0;flex-wrap:wrap">
        <span style="font-weight:600">${{m.label}}</span>
        ${{_supBadge(m.support)}}
        <span class="hint" style="font-size:11px">${{m.status === 'ok'
            ? (m.chapter_count + ' chapters · latest ' + (m.latest || '?'))
            : (m.status === 'error' ? '⚠ could not check' : 'not checked yet')}}</span>
        ${{pref ? '<span class="hint" style="font-size:11px">★ preferred</span>'
                : `<button class="mini" onclick="wlPrefer('${{sx.id}}','${{m.series_key}}')">set preferred</button>`}}
        <button class="mini" onclick="wlChapters('${{sx.id}}','${{m.series_key}}',0)">chapters</button>
        <button class="mini" onclick="wlChapters('${{sx.id}}','${{m.series_key}}',1)" title="re-check this source now">↻</button>
        ${{m.error ? `<div class="hint" style="color:var(--bad);font-size:11px;width:100%">${{m.error}}</div>` : ''}}
      </div>`;
    }}).join('') || '<div class="hint" style="font-size:11px">No source attached yet.</div>';

    return `<div style="border-bottom:1px solid var(--rule);padding:8px 0">
      <div style="display:flex;gap:6px;align-items:center;flex-wrap:wrap">
        <div style="font-weight:700;flex:1">${{sx.title}}</div>
        <span style="color:${{tcol[sx.tier] || 'var(--fg2)'}};font-size:11px">${{sx.tier_label}}</span>
        <button class="mini danger" onclick="wlRemove('${{sx.id}}','${{(sx.title||'').replace(/'/g,'')}}')"
          title="remove from the watchlist — ingested chapters and exports are NOT deleted">🗑</button>
      </div>
      ${{(sx.aliases || []).length ? `<div class="hint" style="font-size:11px">also: ${{sx.aliases.join(' · ')}}</div>` : ''}}
      <div class="hint" style="font-size:11px">${{sx.ingested_count}} ingested${{sx.backlog ? ' of ' + sx.backlog : ''}}${{unmade ? ' · ' + unmade + ' unmade' : ''}}${{sx.checked ? '' : ' · not checked yet'}} · ${{sx.mirror_count}} source${{sx.mirror_count === 1 ? '' : 's'}}</div>
      <div style="margin-top:4px">${{mirrors}}</div>
      <div id="wlch_${{sx.id}}"></div>
      <details style="margin-top:4px"><summary class="hint" style="cursor:pointer;font-size:11px">+ another source for this story</summary>
        <div style="display:flex;gap:5px;margin-top:5px">
          <input id="wlm_${{sx.id}}" placeholder="series URL on another site" style="flex:1">
          <button class="mini" onclick="wlAddMirror('${{sx.id}}')">attach</button>
        </div></details>
    </div>`;
  }}).join('');
}}

async function wlChapters(sid, key, refresh) {{
  const box = document.getElementById('wlch_' + sid);
  box.innerHTML = '<div class="hint" style="font-size:11px">' +
    (refresh ? 'checking the source…' : 'loading chapters…') + '</div>';
  try {{
    const d = await j(`/api/watchlist/chapters?series_id=${{encodeURIComponent(sid)}}` +
                      `&series_key=${{encodeURIComponent(key)}}&refresh=${{refresh ? 1 : 0}}`);
    WLCH[sid] = WLCH[sid] || {{}}; WLCH[sid][key] = d;
    if (d.status === 'error') {{
      // A failed check must never render as "this source has nothing".
      box.innerHTML = `<div class="hint" style="color:var(--bad);font-size:11px">⚠ could not check ${{d.label}} — ${{d.error}}</div>`;
      return;
    }}
    wlChapRender(sid, key);
    if (refresh) wlLoad();
  }} catch (e) {{
    box.innerHTML = `<div class="hint" style="color:var(--bad);font-size:11px">${{e.message || e}}</div>`;
  }}
}}

function wlChapRender(sid, key, showAll) {{
  const d = (WLCH[sid] || {{}})[key];
  if (!d) return;
  const box = document.getElementById('wlch_' + sid);
  const filt = (document.getElementById('wlcf_' + sid) || {{}}).value || '';
  let list = d.chapters.slice().reverse();          // newest first
  if (filt) list = list.filter(c => String(c.id).indexOf(filt) >= 0);
  const CAP = 60;
  const shown = (showAll || filt) ? list : list.slice(0, CAP);
  const chips = shown.map(c => `<button class="mini" onclick="wlIngest('${{sid}}','${{key}}','${{c.id}}')"
      title="${{c.ingested ? 'already ingested — runs again' : 'ingest this chapter'}}"
      style="${{c.ingested ? 'opacity:.45' : ''}}">${{c.ingested ? '✓ ' : ''}}${{c.id}}</button>`).join(' ');
  box.innerHTML = `<div style="margin-top:6px;padding:6px;border:1px solid var(--rule);border-radius:4px">
    <div style="display:flex;gap:5px;align-items:center;margin-bottom:5px;flex-wrap:wrap">
      <span class="hint" style="font-size:11px">${{d.label}} · ${{d.chapters.length}} chapters</span>
      <input id="wlcf_${{sid}}" value="${{filt}}" placeholder="jump to #" style="width:80px"
             oninput="wlChapRender('${{sid}}','${{key}}',1)">
      ${{(!showAll && !filt && list.length > CAP)
        ? `<button class="mini" onclick="wlChapRender('${{sid}}','${{key}}',1)">show all ${{list.length}}</button>` : ''}}
      <button class="mini" onclick="document.getElementById('wlch_${{sid}}').innerHTML=''">close</button>
      <label class="hint" style="font-size:11px" title="Which pipeline a chapter you click here goes through — the same choice as the manual Ingest drawer. Recorded on the project.">engine
        <select id="wleng_${{sid}}" style="font-size:11px" onchange="WLENG[this.id.slice(6)]=this.value">
          <option value="gemini">Gemini</option>
          <option value="claude" ${{WLENG[sid] === 'claude' ? 'selected' : ''}}>Claude</option>
        </select></label>
    </div>
    <div style="display:flex;flex-wrap:wrap;gap:3px;max-height:190px;overflow-y:auto">${{chips || '<span class="hint" style="font-size:11px">no match</span>'}}</div>
  </div>`;
  const f = document.getElementById('wlcf_' + sid);
  if (f && filt) {{ f.focus(); f.setSelectionRange(filt.length, filt.length); }}
}}

/* Engine chosen per series in the chapter list. Kept outside the DOM because
   typing in "jump to #" re-renders that header, which would silently reset
   the picker to Gemini. */
const WLENG = {{}};
async function wlIngest(sid, key, chapter) {{
  const sx = (WL.series || []).find(x => x.id === sid) || {{}};
  const sel = document.getElementById('wleng_' + sid);
  const engine = sel ? sel.value : 'gemini';
  const NL = String.fromCharCode(10);
  const cost = engine === 'claude' ? 'Claude/TTS' : 'Gemini/TTS';
  if (!confirm(`Ingest ${{sx.title || sid}} chapter ${{chapter}} with ${{engine === 'claude' ? 'CLAUDE' : 'Gemini'}}?` + NL + NL +
               `This spends ${{cost}} credit and is queued behind any run already going.`)) return;
  // Same second confirm as the manual Ingest drawer: Claude is the newer path.
  if (engine === 'claude' && !confirm('Run this chapter through the CLAUDE engine?' + NL + NL +
      'Gemini is the established path; Claude is newer and its cost profile differs.' + NL +
      'The choice is recorded on the project.')) return;
  try {{
    const r = await j('/api/watchlist/ingest', {{method: 'POST',
      headers: {{'Content-Type': 'application/json'}},
      body: JSON.stringify({{series_id: sid, series_key: key, chapter: String(chapter), queue: true, engine}})}});
    // Hand off to the SAME ingest console the manual path uses, so there is
    // one place to watch a run — no second progress UI to keep in sync.
    setActiveJob(r.job); toggleDrawer('ingest'); startIngestPoller();
  }} catch (e) {{ alert('Could not start that ingest: ' + (e.message || e)); }}
}}

async function wlPost(path, body, msg) {{
  try {{
    const r = await j(path, {{method: 'POST', headers: {{'Content-Type': 'application/json'}},
                             body: JSON.stringify(body)}});
    WL = r.watchlist || r;
    wlRender();
    return r;
  }} catch (e) {{ alert((msg || 'That did not work') + ': ' + (e.message || e)); }}
}}

async function wlAdd() {{
  const t = document.getElementById('wlTitle').value.trim();
  if (!t) {{ alert('Give the title a name.'); return; }}
  const r = await wlPost('/api/watchlist/series', {{
    title: t, url: document.getElementById('wlUrl').value.trim(),
    tier: document.getElementById('wlTier').value}}, 'Could not add that title');
  if (r) {{ document.getElementById('wlTitle').value = ''; document.getElementById('wlUrl').value = ''; wlLoad(); }}
}}
async function wlAddMirror(sid) {{
  const el = document.getElementById('wlm_' + sid);
  const u = (el.value || '').trim();
  if (!u) return;
  const r = await wlPost('/api/watchlist/mirror', {{series_id: sid, url: u}},
                         'Could not attach that source');
  if (r) wlLoad();
}}
async function wlPrefer(sid, key) {{
  await wlPost('/api/watchlist/preferred', {{series_id: sid, series_key: key}},
               'Could not set the preferred source');
}}
async function wlRemove(sid, label) {{
  if (!confirm(`Remove ${{label || sid}} from the watchlist?\n\nIngested chapters, clips and exports are NOT deleted — this only drops it from the planning list.`)) return;
  await wlPost('/api/watchlist/remove', {{series_id: sid, series_key: ''}}, 'Could not remove that title');
}}
async function wlSeed() {{
  const box = document.getElementById('wllist');
  box.innerHTML = 'seeding…';
  const r = await wlPost('/api/watchlist/seed', {{}}, 'Could not seed the watchlist');
  if (r && r.seeded) {{
    const a = r.seeded.added.length, k = r.seeded.skipped.length;
    if (k) alert(`Added ${{a}} · left ${{k}} already on the list untouched.`);
  }}
  wlLoad();
}}

// ---------------- undo (timing & motion) ----------------
// Every mutating op snapshots segments.json before it writes, so undo restores
// the previous manifest rather than trying to invert the op. Carving slices an
// mp3 and including a panel synthesises audio — inverses would be fifteen
// chances to drift; a restore cannot.
async function refreshUndo() {{
  // One button per card, but ONE shared history: the snapshot stack is
  // timeline-wide, so this undoes the last edit made anywhere — which the
  // tooltip states, because a button sitting on seg #9 that reverts an edit
  // to seg #3 would otherwise be a nasty surprise.
  const btns = document.querySelectorAll('.undobtn');
  if (!btns.length) return;
  let st = [];
  try {{
    st = (await j('/api/storyboard/undo')).stack || [];
  }} catch (e) {{ st = []; }}
  const label = st.length ? st[0].op.replace(/_/g, ' ') : '';
  btns.forEach(function (b) {{
    b.disabled = st.length === 0;
    b.title = st.length
      ? ('undo the last edit on this timeline: ' + label +
         '  (' + st.length + ' can be walked back)')
      : 'nothing to undo yet';
  }});
}}

async function undoEdit() {{
  // every segment card has its own ↶ (class undobtn); show progress on all
  document.querySelectorAll('.undobtn').forEach(function (b) {{ b.disabled = true; b.textContent = 'undoing…'; }});
  try {{
    const r = await j('/api/storyboard/undo', {{method: 'POST'}});
    // Clips for the segments that actually changed were deleted, so the board
    // must reload to show the restored timeline rather than the edited one.
    location.reload();
  }} catch (e) {{
    alert('Could not undo: ' + (e.message || e));
    refreshUndo();
  }}
}}
refreshUndo();

// ---------------- Split Lab ----------------
// Panel boundaries are a VISUAL judgement — "did this cut land on a gutter or
// through a face?" cannot be read off a count — so the results belong in the
// UI rather than in a crop folder on the server the operator cannot reach.
var SPCUR = null, spPoll = null, spJob = null;

async function spStop() {{
  if (!spJob) return;
  try {{
    await j('/api/jobs/control', {{method:'POST', headers:{{'Content-Type':'application/json'}},
      body: JSON.stringify({{job: spJob, action: 'stop'}})}});
    document.getElementById('spstate').textContent = 'stopping…';
  }} catch (e) {{ alert('Could not stop: ' + (e.message || e)); }}
}}

async function loadSplit() {{
  try {{
    const d = await j('/api/projects');
    const sel = document.getElementById('spProj');
    const cur = sel.value;
    sel.innerHTML = '<option value="">— or an ingested project —</option>' +
      (d.projects || []).filter(p => p.id && p.id.indexOf('(current)') < 0)
        .map(p => `<option value="${{p.id}}">${{p.id}}</option>`).join('');
    sel.value = cur;
  }} catch (e) {{}}
  try {{
    const r = await j('/api/split/runs');
    const sel = document.getElementById('spPick');
    sel.innerHTML = '<option value="">— past runs —</option>' +
      (r.runs || []).map(m => `<option value="${{m.slug}}">${{m.slug}} · ${{m.panels}} panels</option>`).join('');
    if (!SPCUR && (r.runs || []).length) spShow(r.runs[0].slug);
  }} catch (e) {{}}
}}

async function spRun() {{
  const url = document.getElementById('spUrl').value.trim();
  const proj = document.getElementById('spProj').value;
  if (!url && !proj) {{ alert('Paste a chapter URL or pick a project.'); return; }}
  const st = document.getElementById('spstate');
  st.textContent = 'starting…';
  try {{
    const r = await j('/api/split/run', {{method:'POST',
      headers:{{'Content-Type':'application/json'}},
      body: JSON.stringify({{url: url, project: proj,
                             blur: document.getElementById('spBlurRun').checked}})}});
    spJob = r.job;
    document.getElementById('spStop').disabled = false;
    if (spPoll) clearInterval(spPoll);
    spPoll = setInterval(async function () {{
      let s;
      try {{ s = await j('/api/jobs/' + r.job); }} catch (e) {{ return; }}
      st.textContent = (s.status === 'running' ? '⏳ ' : '') + (s.stage || s.status)
        + (s.total > 1 ? `  (${{s.done}}/${{s.total}})` : '');
      if (s.status === 'done' || s.status === 'error' || s.status === 'cancelled') {{
        clearInterval(spPoll); spPoll = null;
        spJob = null;
        document.getElementById('spStop').disabled = true;
        if (s.status === 'error') st.textContent = '⚠ ' + (s.error || 'failed');
        await loadSplit();
        if (s.slug) spShow(s.slug);
      }}
    }}, 1500);
  }} catch (e) {{ st.textContent = '⚠ ' + (e.message || e); }}
}}

async function spShow(slug) {{
  if (!slug) return;
  try {{
    SPCUR = await j('/api/split/run/' + encodeURIComponent(slug));
    document.getElementById('spPick').value = slug;
    spPaint();
  }} catch (e) {{
    document.getElementById('spmeta').textContent = 'could not load that run: ' + (e.message || e);
  }}
}}

function spPaint() {{
  const m = SPCUR; if (!m) return;
  const onlyTall = document.getElementById('spTall').checked;
  const rows = (m.stats || []).map(s =>
    `${{s.page}}: bg ${{s.bg}} · tol ${{s.tol}} · ${{s.gaps}} gaps → <b>${{s.panels}}</b>`).join(' &nbsp;|&nbsp; ');
  document.getElementById('spmeta').innerHTML =
    `<b>${{m.panels}} panels</b> from ${{m.pages}} ${{m.format}} image(s) · median AR ${{m.median_ar}}`
    + ` · ${{m.over_3}} taller than 3:1`
    + (m.blur ? ` · <b>${{m.blurred_panels}}</b> with bubbles blurred (mean ${{(m.bubble_cov_mean*100).toFixed(1)}}% of panel)` : '')
    + `<div style="margin-top:5px;font-size:11px">${{rows}}</div>`;
  const showBlur = document.getElementById('spBlurView').checked;
  const list = (m.panel_list || []).filter(p => !onlyTall || p.ar > 3);
  document.getElementById('spgrid').innerHTML = list.map(p => `
    <figure style="margin:0;width:132px">
      <a href="/splitimg/${{m.slug}}/${{(showBlur && p.blurred) ? p.name.replace('.png','_blur.png') : p.name}}" target="_blank">
        <img src="/splitimg/${{m.slug}}/${{(showBlur && p.blurred) ? p.name.replace('.png','_blur.png') : p.name}}" loading="lazy"
             style="display:block;width:132px;border:1px solid var(--rule);border-radius:3px${{p.ar > 3 ? ';outline:2px solid var(--warn)' : ''}}"></a>
      <figcaption class="hint" style="font-size:10px;text-align:center;margin-top:3px">
        ${{p.w}}×${{p.h}} · AR ${{p.ar}}${{p.blurred ? ' · ' + (p.bubble_frac*100).toFixed(0) + '% bubble' : ''}}</figcaption>
    </figure>`).join('') || '<span class="hint">nothing to show</span>';
}}

async function loadTracker(refresh) {{
  const box = document.getElementById('trackerlist');
  box.innerHTML = refresh ? 'checking the source…' : 'loading…';
  try {{
    const d = await j('/api/tracker?refresh=' + (refresh ? 1 : 0));
    const hidden = (d.untracked || []).length
      ? `<details style="margin-top:10px"><summary class="hint" style="cursor:pointer">
           ${{d.untracked.length}} series not tracked</summary>` +
        d.untracked.map(u => `<div style="display:flex;gap:6px;align-items:center;padding:4px 0">
           <span class="hint" style="flex:1">${{u.series}}</span>
           <button class="mini" onclick="untrackSeries('${{u.series_url}}','',1)">↩ track again</button>
         </div>`).join('') + `</details>`
      : '';
    if (!(d.series || []).length) {{
      box.innerHTML = 'No trackable series yet — ingest a chapter first.' + hidden; return;
    }}
    box.innerHTML = d.series.map(sx => {{
      const behind = sx.behind || 0;
      const col = sx.error ? 'var(--bad)' : (behind ? 'var(--warn)' : 'var(--ok)');
      const status = sx.error
        ? ('⚠ could not check — ' + sx.error + (sx.stale ? ' (showing last known)' : ''))
        : (behind ? (behind + ' new chapter' + (behind === 1 ? '' : 's') + ' available')
                  : 'up to date');
      const lbl = (sx.series || '').replace(/'/g,"");
      const next = sx.next
        ? `<div style="margin-top:6px;display:flex;gap:6px;align-items:center">
             <input type="checkbox" class="trksel" value="${{sx.next_url}}" data-label="${{lbl}} ch ${{sx.next}}" onchange="updateTrkSel()">
             <button class="primary" onclick="ingestChapter('${{sx.next_url}}','${{lbl}} ch ${{sx.next}}')">
               ▶ Ingest chapter ${{sx.next}}</button></div>`
        : '';
      const more = (sx.upcoming || []).length > 1
        ? `<details style="margin-top:6px"><summary class="hint" style="cursor:pointer">pick a different chapter</summary>
             <div style="display:flex;flex-wrap:wrap;gap:4px;margin-top:6px">` +
           sx.upcoming.map(u => `<label class="mini" style="display:inline-flex;gap:3px;align-items:center;cursor:pointer">
             <input type="checkbox" class="trksel" value="${{u.url}}" data-label="${{lbl}} ch ${{u.chapter}}" onchange="updateTrkSel()">${{u.chapter}}</label>`).join('') +
           `</div></details>`
        : '';
      return `<div style="border-bottom:1px solid var(--rule);padding:8px 0">
        <div style="display:flex;gap:6px;align-items:center">
          <div style="font-weight:700;flex:1">${{sx.series}}</div>
          <button class="mini danger" title="stop tracking this series — ingested chapters and exports are NOT deleted"
            onclick="untrackSeries('${{sx.series_url}}','${{lbl}}')">🗑 remove</button>
        </div>
        <div style="color:${{col}};font-size:12px">${{status}}</div>
        <div class="hint" style="font-size:11px">have ch ${{sx.highest_have || '—'}} · latest ch ${{sx.latest || '?'}} · ${{sx.have_count}} ingested${{sx.backfill_count ? (' · ' + sx.backfill_count + ' earlier chapters skipped') : ''}}</div>
        ${{next}}${{more}}
      </div>`;
    }}).join('') + hidden;
  }} catch (e) {{ box.innerHTML = 'Failed to load tracker: ' + (e.message || e); }}
}}

async function untrackSeries(url, label, restore) {{
  // Removing a series from the WATCHLIST only. Its ingested chapters, clips and
  // exports stay exactly where they are — deleting those is the Projects tab's
  // job, and that one asks before destroying anything.
  if (!restore && !confirm('Stop tracking ' + (label || 'this series') +
      '? Its ingested chapters and exports are NOT deleted — this only stops ' +
      'checking for new chapters, and you can track it again later.')) return;
  try {{
    await j('/api/tracker/untrack', {{method:'POST',
      headers:{{'Content-Type':'application/json'}},
      body: JSON.stringify({{series_url: url, restore: !!restore}})}});
    await loadTracker(0);
  }} catch (e) {{ alert('Could not update the tracker: ' + (e.message || e)); }}
}}
function _trkChecked() {{ return Array.from(document.querySelectorAll('.trksel')).filter(c => c.checked); }}
function updateTrkSel() {{
  const n = _trkChecked().length;
  const el = document.getElementById('trkseln'); if (el) el.textContent = n;
  const b = document.getElementById('trkqbtn'); if (b) b.disabled = n === 0;
}}
function toggleAllTrk(src) {{
  // "select all next" ticks each series' next chapter, not its whole backlog
  document.querySelectorAll('#trackerlist > div > div > .trksel').forEach(c => {{ c.checked = src.checked; }});
  updateTrkSel();
}}
async function queueSelected() {{
  const sel = _trkChecked();
  if (!sel.length) return;
  const names = sel.map(c => c.dataset.label);
  if (!confirm('Queue ' + sel.length + ' chapter(s)? ' + names.join(', ') +
      ' — each runs the full pipeline and spends Gemini + TTS credit. They run one at a time.')) return;
  let ok = 0;
  for (const c of sel) {{
    try {{
      await j('/api/ingest', {{method:'POST', headers:{{'Content-Type':'application/json'}},
        body: JSON.stringify({{url: c.value, fresh: false, queue: true}})}});
      ok++;
    }} catch (e) {{ /* keep going; report at the end */ }}
  }}
  alert('Queued ' + ok + ' of ' + sel.length + ' chapter(s). Watch them in the Jobs tab.');
  toggleDrawer('logs');
}}
async function ingestChapter(url, label) {{
  if (!confirm('Ingest ' + label + '?' + String.fromCharCode(10) + String.fromCharCode(10) +
      'This runs the full pipeline (10-20 min) and spends Gemini + TTS credit. Do not redeploy while it runs.')) return;
  try {{
    const r = await j('/api/ingest', {{method:'POST', headers:{{'Content-Type':'application/json'}},
      body: JSON.stringify({{url: url, fresh: false}})}});
    setActiveJob(r.job); toggleDrawer('ingest'); startIngestPoller();
  }} catch (e) {{ alert('Could not start ingest: ' + (e.message || e)); }}
}}
async function activateProj(id) {{
  // Open = switch the studio to that chapter AND go to its board. A plain
  // reload kept the #projects page address, so it landed back on Projects.
  try {{
    await j('/api/activate', {{method:'POST', headers:{{'Content-Type':'application/json'}}, body: JSON.stringify({{id}})}});
  }} catch (e) {{ alert('Could not open that chapter: ' + (e.message || e)); return; }}
  location.href = '/storyboard';
}}
let logsPolling = false;
/* ---- LOGS (owner, 2026-10-04: like Scrapper's — one live feed, named jobs, spend) ---- */
let LIVE = {{ last: 0, lines: [], f: '' }};
function liveFilter(k, btn) {{
  LIVE.f = k;
  document.querySelectorAll('#livef button').forEach(b => b.classList.toggle('on', b === btn));
  livePaint();
}}
function livePaint() {{
  const box = document.getElementById('livefeed');
  if (!box) return;
  const q = (document.getElementById('liveq') || {{}}).value || '';
  const near = box.scrollHeight - box.scrollTop - box.clientHeight < 40;
  const rows = LIVE.lines.filter(e => (!LIVE.f || (LIVE.f === '!error' ? e.level === 'error' : e.kind === LIVE.f)) &&
    (!q || (e.msg || '').toLowerCase().includes(q.toLowerCase())));
  box.innerHTML = rows.length ? rows.map(e => {{
    const t = new Date(e.ts * 1000).toLocaleTimeString([], {{ hour: '2-digit', minute: '2-digit', second: '2-digit' }});
    return `<div><span class="t">${{t}}</span> <span class="k">[${{esc(e.kind)}}]</span> <span class="${{esc(e.level)}}">${{esc(e.msg)}}</span></div>`;
  }}).join('') : '<span class="t">waiting for activity…</span>';
  if (near) box.scrollTop = box.scrollHeight;     // follow, unless you scrolled up to read
}}
async function liveLoad() {{
  try {{
    const d = await j('/api/events?after=' + LIVE.last + '&limit=400');
    if ((d.events || []).length) {{
      LIVE.lines = LIVE.lines.concat(d.events).slice(-1000);
      LIVE.last = d.last;
      livePaint();
    }} else if (!LIVE.lines.length) livePaint();
  }} catch (e) {{}}
}}
const JK = {{ ingest: '📥', autopilot: '🤖', export: '📤', finalize: '🎬', render: '🎬', publish: '📺', research: '📖', validate: '🛡', lab: '🧪' }};
function jobRow(x) {{
  const STAT = {{ running: ['var(--sa-bg)', 'var(--sa-ink)'], queued: ['var(--gray-bg)', 'var(--gray-ink)'],
    paused: ['var(--warnb-bg)', 'var(--warn)'], pausing: ['var(--warnb-bg)', 'var(--warn)'], done: ['var(--okb-bg)', 'var(--okb-ink)'],
    error: ['var(--badb-bg)', 'var(--bad)'], cancelled: ['var(--gray-bg)', 'var(--gray-ink)'],
    budget_paused: ['var(--warnb-bg)', 'var(--warn)'], interrupted: ['var(--warnb-bg)', 'var(--warn)'] }};
  const [bg, fg] = STAT[x.status] || STAT.queued;
  const live = ['running', 'queued', 'paused', 'pausing'].includes(x.status);
  const waiting = ['budget_paused', 'interrupted'].includes(x.status);
  const resumable = ['ingest', 'autopilot', 'finalize'].includes(x.kind) && ['cancelled', 'error', 'budget_paused', 'interrupted'].includes(x.status);
  const el = x.elapsed != null ? (x.elapsed >= 3600 ? Math.round(x.elapsed / 360) / 10 + ' h' : x.elapsed >= 60 ? Math.round(x.elapsed / 60) + ' min' : x.elapsed + ' s') : '';
  const btns = live
    ? `<button class="mini" title="pause at the next step" onclick="jobCtl('${{x.id}}','pause')">⏸</button>
       <button class="mini" title="resume" onclick="jobCtl('${{x.id}}','resume')">▶</button>
       <button class="mini" title="stop" onclick="stopTwoTap(this, '${{x.id}}')">⏹</button>`
    : (resumable ? `<button class="mini" title="continue it — cached work is reused" onclick="jobResume('${{x.id}}')">▶</button>` : '') +
      (waiting ? `<button class="mini" title="stop this waiting job" onclick="stopTwoTap(this, '${{x.id}}')">⏹</button>` : '') +
      `<button class="mini" title="remove this record" onclick="jobCtl('${{x.id}}','delete')">🗑</button>`;
  return `<div style="border-bottom:1px solid var(--rule);padding:7px 0;display:flex;gap:8px;align-items:flex-start">
    <input type="checkbox" class="jobsel" value="${{x.id}}" data-live="${{live ? 1 : 0}}" onchange="updateJobSel()" style="margin-top:3px">
    <div style="flex:1;min-width:0">
      <div style="display:flex;gap:6px;align-items:center;flex-wrap:wrap">
        <span>${{JK[x.kind] || '⚙'}}</span><b style="font-size:12.5px">${{esc(x.name)}}</b>
        ${{apPill(String(x.status).replace('_', ' '), bg, fg)}} <span class="hint">${{esc(x.kind)}}</span>
        ${{x.deleted ? '<span class="hint">(deleted)</span>' : ''}}
      </div>
      <div class="hint" style="font-size:11px">${{[x.stage, x.pct != null ? x.pct + '%' : null, x.msg, el, x.cost ? '$' + x.cost.toFixed(2) : null, x.export ? '→ ' + x.export : null].filter(Boolean).map(esc).join(' · ')}}</div>
      ${{x.error ? `<div style="color:var(--bad);font-size:11px">⚠ ${{esc(x.error)}}</div>` : ''}}
    </div>
    <div style="display:flex;gap:3px;flex-shrink:0">${{btns}}</div>
  </div>`;
}}
async function jobsLoad() {{
  const box = document.getElementById('joblist');
  const keep = new Set(_jobsChecked().map(c => c.value));
  try {{
    const g = await j('/api/logs/jobs');
    const sec = (label, rows) => rows.length ? `<div class="jgrp">${{label}} (${{rows.length}})</div>` + rows.map(jobRow).join('') : '';
    const html = sec('Running', g.running) + sec('Waiting', g.waiting) + sec('Finished today', g.today) + sec('Earlier', g.earlier) || 'No jobs yet.';
    if (html !== box.dataset.last) {{ box.innerHTML = html; box.dataset.last = html; }}
    if (keep.size) document.querySelectorAll('.jobsel').forEach(c => {{ c.checked = keep.has(c.value); }});
    updateJobSel();
  }} catch (e) {{ box.innerHTML = `<span style="color:var(--bad)">⚠ could not load jobs — ${{esc(e.message || e)}}</span>`; }}
}}
async function spendLoad() {{
  const box = document.getElementById('spendbox');
  try {{
    const d = await j('/api/spend');
    const pct = (a, b) => b ? Math.min(100, Math.round(100 * a / b)) : 0;
    const max = Math.max(0.01, ...d.month.days.map(x => x[1]));
    box.innerHTML = `<div class="spendcard"><b>Today (ET ${{esc(d.day)}})</b>
        <div class="hint">Whole site $${{d.today.spent.toFixed(2)}} of $${{(d.today.cap || 0).toFixed(2)}} (hard stop)</div>
        <div class="meter"><i style="width:${{pct(d.today.spent, d.today.cap)}}%"></i></div>
        <div class="hint">Autopilot $${{(d.autopilot.spent || 0).toFixed(2)}} of $${{(d.autopilot.budget || 0).toFixed(2)}}</div>
        <div class="meter"><i style="width:${{pct(d.autopilot.spent, d.autopilot.budget)}}%"></i></div></div>
      <div class="spendcard"><b>${{esc(d.month.label)}} so far: $${{d.month.total.toFixed(2)}}</b>
        <div class="daybars" title="spend per day">${{d.month.days.map(x => `<i title="${{esc(x[0])}}: $${{x[1].toFixed(2)}}" style="height:${{Math.max(2, Math.round(70 * x[1] / max))}}px"></i>`).join('')}}</div>
        <div class="hint">${{Object.entries(d.month.by_provider).map(([k, v]) => esc(k) + ' $' + v.toFixed(2)).join(' · ')}}</div></div>
      <div class="spendcard"><b>Recent chapters</b>${{d.chapters.length ? d.chapters.map(c => `<div class="hint">${{esc(c.name)}} — $${{(c.cost || 0).toFixed(2)}}</div>`).join('') : '<div class="hint">none yet</div>'}}</div>
      <div class="hint">${{esc(d.note)}}</div>`;
  }} catch (e) {{ box.innerHTML = 'spend unavailable: ' + esc(e.message || e); }}
  try {{
    const uj = await j('/api/logs/usage?limit=60');
    document.getElementById('logusage').innerHTML = (uj.calls || []).slice(-60).reverse().map(c =>
      `<div class="hint" style="border-bottom:1px solid var(--rule);padding:2px 0">
       ${{c.kind}} · ${{c.model || ''}} · ${{c.units}} ${{c.unit || ''}}${{c.metered
         ? ` · ${{(c.prompt_tokens || 0).toLocaleString()}} in / ${{(c.output_tokens || 0).toLocaleString()}} out` +
           (c.thought_tokens ? ` (${{c.thought_tokens.toLocaleString()}} thinking)` : '') +
           (c.service_tier && c.service_tier !== 'standard' ? ` · ${{c.service_tier}}` : '')
         : (c.unit === 'chars' ? '' : ' · <span style="color:var(--warn)">estimate</span>')}}
       · $${{(c.est_cost_usd || 0).toFixed(4)}}</div>`).join('');
  }} catch (e) {{ document.getElementById('logusage').innerHTML = 'usage unavailable'; }}
}}
async function loadLogs() {{
  if (LOGS_TAB === 'live') await liveLoad();
  if (LOGS_TAB === 'jobs') await jobsLoad();
  if (LOGS_TAB === 'spend') await spendLoad();
  const stamp = document.getElementById('logstamp');
  if (stamp) stamp.textContent = 'updated ' + new Date().toLocaleTimeString();
  if (!logsPolling) {{
    logsPolling = true;
    (async () => {{
      while (document.getElementById('d_logs').style.display === 'block') {{
        await new Promise(r => setTimeout(r, LOGS_TAB === 'live' ? 3000 : 6000));
        if (document.getElementById('d_logs').style.display !== 'block') break;
        if (_jobsChecked().length || document.hidden) continue;   // never re-render under a selection
        await loadLogs();
      }}
      logsPolling = false;
    }})();
  }}
}}
/* Two-tap Stop (Scrapper lesson): confirm() can be silently blocked in in-app
   browsers, so the first tap arms the button and the second, within 3 s, stops. */
function stopTwoTap(btn, id) {{
  if (btn.dataset.armed) {{
    delete btn.dataset.armed;
    j('/api/jobs/control', {{method:'POST', headers:{{'Content-Type':'application/json'}}, body: JSON.stringify({{job_id: id, action: 'stop'}})}})
      .then(() => {{ loadLogs(); if (window.jobsBarLoad) window.jobsBarLoad(); }}).catch(e => alert('Could not stop: ' + (e.message || e)));
    btn.textContent = '…';
    return;
  }}
  btn.dataset.armed = '1';
  const was = btn.textContent;
  btn.textContent = 'tap again to stop';
  setTimeout(() => {{ if (btn.dataset.armed) {{ delete btn.dataset.armed; btn.textContent = was; }} }}, 3000);
}}
function _jobsChecked() {{
  return Array.from(document.querySelectorAll('.jobsel')).filter(c => c.checked);
}}
function updateJobSel() {{
  const sel = _jobsChecked();
  const el = document.getElementById('jobseln'); if (el) el.textContent = sel.length;
  const sb = document.getElementById('jobstopbtn');
  const db = document.getElementById('jobdelbtn');
  if (sb) sb.disabled = !sel.some(c => c.dataset.live === '1');
  if (db) db.disabled = !sel.some(c => c.dataset.live === '0');
}}
function toggleAllJobs(src) {{
  document.querySelectorAll('.jobsel').forEach(c => {{ c.checked = src.checked; }});
  updateJobSel();
}}
async function jobCtl(id, action) {{
  if (action === 'stop' && !confirm('Stop this job? Work already paid for is kept, but it will not finish.')) return;
  if (action === 'delete' && !confirm('Remove this job record?')) return;
  try {{
    if (action === 'delete') {{
      await j('/api/jobs/delete', {{method:'POST', headers:{{'Content-Type':'application/json'}},
        body: JSON.stringify({{job_id: id}})}});
    }} else {{
      await j('/api/jobs/control', {{method:'POST', headers:{{'Content-Type':'application/json'}},
        body: JSON.stringify({{job_id: id, action}})}});
    }}
    loadLogs();
  }} catch (e) {{ alert('Could not ' + action + ': ' + (e.message || e)); }}
}}
async function jobResume(id) {{
  try {{
    const r = await j('/api/jobs/resume', {{method:'POST', headers:{{'Content-Type':'application/json'}},
      body: JSON.stringify({{job_id: id}})}});
    if (r.kind === 'finalize' && r.job) pollFinalize(r.job);
    loadLogs();
  }} catch (e) {{ alert('Could not resume: ' + (e.message || e)); }}
}}
async function bulkJobs(action) {{
  const sel = _jobsChecked().filter(c => c.dataset.live === (action === 'stop' ? '1' : '0'));
  const ids = sel.map(c => c.value);
  if (!ids.length) return;
  const verb = action === 'stop' ? 'Stop' : 'Delete';
  if (!confirm(verb + ' ' + ids.length + ' job(s)?')) return;
  try {{
    if (action === 'delete') {{
      const r = await j('/api/jobs/delete', {{method:'POST', headers:{{'Content-Type':'application/json'}},
        body: JSON.stringify({{job_ids: ids}})}});
      if ((r.skipped || []).length) alert('Skipped: ' + r.skipped.map(x => x.id + ' (' + x.reason + ')').join('; '));
    }} else {{
      for (const id of ids) {{
        await j('/api/jobs/control', {{method:'POST', headers:{{'Content-Type':'application/json'}},
          body: JSON.stringify({{job_id: id, action: 'stop'}})}}).catch(() => {{}});
      }}
    }}
    loadLogs();
  }} catch (e) {{ alert('Bulk ' + action + ' failed: ' + (e.message || e)); }}
}}
</script></body></html>"""
