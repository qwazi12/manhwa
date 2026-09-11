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

    # One rule for "is this in the video", shared with the renderer/exporter:
    # ticked AND not rejected. Stamped per segment so the row controls, the
    # counters and the dead-air chip can never disagree with the export.
    for s in segs:
        s["in_video"] = bool(s.get("user_included")) and \
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

    rendered_scenes = set()
    scene_panels_count = {}
    scene_panel_index = {}
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
            first = seg_by_panel[pid][0]
            btxt = " ".join(b["text"] for b in first["beats"])[:300]
            if not btxt:
                script_cell = "<i>on screen as a silent hold (no narration)</i>"
            elif uid is not None and uid in rendered_scenes:
                idx = scene_panel_index.get(uid, 1) + 1
                scene_panel_index[uid] = idx
                tot = scene_panels_count.get(uid, 1)
                script_cell = f'<i>↳ shared narration <b class="ln">¶{uid}</b> (image {idx} of {tot})</i>'
            else:
                if uid is not None:
                    rendered_scenes.add(uid)
                    scene_panel_index[uid] = 1
                label = f'<b class="ln">¶{uid}</b> ' if uid is not None else ""
                tot = scene_panels_count.get(uid, 1)
                group_badge = f' <span class="b group" style="background:var(--sa-bg);color:var(--sa-ink);padding:1px 5px;border-radius:3px;font-size:10px;">{tot} images in group</span>' if (uid is not None and tot > 1) else ""
                script_cell = (label + html.escape(btxt) + ("…" if len(btxt) == 300 else "") + group_badge)
            cls = "sa"
        elif reason:
            script_cell = f"<i>LEFT OUT — {html.escape(reason)}</i>"
            cls = "omit"
        elif pid in unit_of:
            uid, utxt = unit_of[pid]
            script_cell = (f'<i>→ folded into <b class="ln">¶{uid}</b></i>'
                           f'<div class="unittxt">{html.escape(utxt[:220])}'
                           f'{"…" if len(utxt) > 220 else ""}</div>')
            cls = "fold"
        else:
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

    title = f"{meta.get('series','?')} Ch.{meta.get('chapter','?')}"
    u = usage_summary or {}
    life = u.get("lifetime", {})
    all_g = life.get("gemini_calls", 0) + u.get("gemini_calls", 0)
    all_t = life.get("tts_chars", 0) + u.get("tts_chars", 0)
    all_c = life.get("est_cost_usd", 0) + u.get("est_cost_usd", 0)
    mm = meta.get("match_method", "")
    return f"""<!doctype html><html><head><meta charset="utf-8">
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
{theme.TOKENS_CSS}{theme.CONTROLS_CSS}
body {{ font-family: -apple-system, Helvetica, sans-serif; margin: 0 0 0 64px; background:var(--bg); color:var(--ink); }}
header {{ position: sticky; top:0; z-index:5; background:var(--panel); color:var(--ink); padding:10px 18px; display:flex; gap:16px; align-items:center; flex-wrap:wrap; border-bottom:1px solid var(--rule); }}
header .stat b {{ display:block; font-size:15px; color:var(--ink); }} header .stat {{ font-size:11px; color:var(--ink3); }}
.usage {{ font-size:11px; color:var(--ok); line-height:1.5; }}
#approveBtn {{ margin-left:auto; background:#8d6e63; border:0; color:#fff; padding:10px 16px; border-radius:6px; font-weight:700; cursor:pointer; }}
#approveBtn.on {{ background:#2e7d32; }}
#pipebar {{ position:sticky; top:52px; z-index:49; background:var(--panel2); color:var(--ink2); font-size:12px;
  display:flex; gap:18px; align-items:center; padding:7px 18px; border-bottom:1px solid var(--rule); }}
#pipebar b {{ color:var(--ink); }}
#renderprog {{ flex:1; display:flex; align-items:center; gap:10px; }}
#renderbar {{ height:8px; background:var(--ok); border-radius:4px; width:0%; min-width:2px; transition:width .5s;
  box-shadow:0 0 8px rgba(57,192,127,.6); }}
#rendertxt {{ color:var(--ok); white-space:nowrap; }}
/* ---- left rail (ported from legacy UI) ---- */
#rail {{ position:fixed; left:0; top:0; bottom:0; width:64px; background:var(--panel); border-right:1px solid var(--rule); display:flex; flex-direction:column; align-items:center; gap:6px; padding-top:12px; z-index:20; }}
.navbtn {{ width:52px; height:56px; border:0; background:transparent; border-radius:9px; display:flex; flex-direction:column; gap:4px; align-items:center; justify-content:center; color:var(--ink3); font-size:10px; cursor:pointer; }}
.navbtn .ic {{ font-size:19px; line-height:1; }}
.navbtn:hover, .navbtn.active {{ background:var(--panel2); color:var(--accent); }}
/* Retractable rail. Retracted it is a 14px sliver; hovering it — or tabbing
   into it, so this is not mouse-only — slides it back to full width OVER the
   content instead of reflowing the board, so nothing shifts under the pointer
   as it opens. The pin keeps it out, and the choice is stored under the same
   localStorage key /review uses, so the rail behaves the same on both. */
{theme.rail_css('#rail')}
/* ---- drawers ---- */
.drawer {{ position:fixed; left:64px; top:86px; bottom:0; width:360px; background:var(--panel); color:var(--ink); border-right:1px solid var(--rule); z-index:70; padding:16px; overflow-y:auto; display:none; font-size:13px; box-shadow:4px 0 18px rgba(0,0,0,.5); }}
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
.segblock {{ background:var(--seg-bg); border:1px solid var(--seg-rule); border-radius:6px; padding:6px; margin-bottom:6px; position:relative; }}
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
.cropact {{ display:block; width:100%; margin-top:3px; font-size:10px; padding:2px 3px;
  border-radius:3px; }}

.cropact.alt {{ color:var(--ink3); }}
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
.acts {{ margin-top:6px; display:flex; gap:5px; flex-wrap:wrap; }}
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
</style>{theme.HEAD_THEME_JS}</head><body>
<div id="rail">
  <button class="navbtn active" title="Storyboard" onclick="location.reload()"><span class="ic">🎬</span>Board</button>
  <button class="navbtn" data-d="ingest" onclick="toggleDrawer('ingest')"><span class="ic">🔗</span>Ingest</button>
  <button class="navbtn" data-d="projects" onclick="toggleDrawer('projects')"><span class="ic">📚</span>Projects</button>
  <button class="navbtn" data-d="tracker" onclick="toggleDrawer('tracker')"><span class="ic">📡</span>Tracker</button>
  <button class="navbtn" data-d="logs" onclick="toggleDrawer('logs')"><span class="ic">📋</span>Logs</button>
  <button class="navbtn" data-d="exports" onclick="toggleDrawer('exports')"><span class="ic">📤</span>Exports</button>
  <a class="navbtn" href="/review" title="watch and rule on a rendered export"><span class="ic">📺</span>Review</a>
  {theme.RAIL_BUTTONS_HTML}
</div>
<div class="drawer" id="d_ingest">
  <h3>Ingest a chapter</h3>
  <div class="hint">Paste a chapter URL — it runs the whole pipeline
    (scrape → split → describe → narrate → voice → match → segment) and this
    board reloads on it when done. Clips render on demand after approval.</div>
  <input class="field" id="ingurl" placeholder="https://…/chapter/…"/>
  <label class="hint" style="display:flex;gap:6px;align-items:center;margin-bottom:8px">
    <input type="checkbox" id="ingfresh"> Fresh re-ingest (regenerate script,
    audio &amp; timeline — for re-running a chapter after a pipeline fix)</label>
  <button class="primary" onclick="runIngest()">▶ Run ingest</button>
  <div id="ingprog" style="margin-top:12px"></div>
</div>
<div class="drawer" id="d_projects">
  <h3>Projects</h3>
  <div id="projlist" class="hint">loading…</div>
</div>
<div class="drawer" id="d_tracker">
  <h3>NEW CHAPTERS</h3>
  <div class="hint" style="margin-bottom:8px">What the source has published since you last ingested each series.
  Checked at most twice an hour — press refresh to look again.</div>
  <button style="width:100%;margin-bottom:8px" onclick="loadTracker(1)">↻ Check for new chapters now</button>
  <div style="display:flex;gap:8px;align-items:center;margin-bottom:8px;font-size:12px;flex-wrap:wrap">
    <label style="cursor:pointer"><input type="checkbox" id="trkall" onchange="toggleAllTrk(this)"> select all next</label>
    <button id="trkqbtn" class="mini" disabled onclick="queueSelected()">▶ queue selected (<span id="trkseln">0</span>)</button>
    <span class="hint">queued chapters run one at a time</span>
  </div>
  <div id="trackerlist" class="hint">loading…</div>
</div>
<div class="drawer" id="d_exports">
  <h3>EXPORTS — final videos</h3>
  <div class="hint" style="margin-bottom:8px">Every exported MP4 for the active project. Click to watch / download.</div>
  <div id="exportlist">loading…</div>
</div>
<div class="drawer" id="d_logs">
  <h3 style="margin-bottom:4px">JOBS <span id="logstamp" class="hint" style="font-weight:400;font-size:11px"></span></h3>
  <div class="hint" style="margin-bottom:6px">Every ingest, render and export — newest first. Pause and stop take effect at
  the next step (between pipeline stages, or between clips), so a stop lands within seconds rather than instantly.</div>
  <div style="display:flex;gap:8px;align-items:center;margin-bottom:8px;font-size:12px;flex-wrap:wrap">
    <label style="cursor:pointer"><input type="checkbox" id="joball" onchange="toggleAllJobs(this)"> select all</label>
    <button id="jobstopbtn" onclick="bulkJobs('stop')" disabled class="mini">⏹ stop selected (<span id="jobseln">0</span>)</button>
    <button id="jobdelbtn" onclick="bulkJobs('delete')" disabled class="mini">🗑 delete selected</button>
    <button class="mini" onclick="loadLogs()">↻ refresh</button>
  </div>
  <div id="joblist">loading…</div>
  <div class="section"><h3>API usage</h3><div id="logusage" class="hint">loading…</div></div>
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
  <div class="usage">{_et_label()} — today: {u.get("gemini_calls", 0)} gemini · {u.get("tts_chars", 0)} tts · ~${u.get("est_cost_usd", 0):.2f}<br>
  all-time: {all_g} gemini · {all_t} tts · ~${all_c:.2f}</div>
  {_outdated_stat(n_outdated)}
  <label class="mini" style="display:inline-flex;gap:4px;align-items:center;cursor:pointer"
         title="rebuild EVERY ticked clip, even ones that already exist">
    <input type="checkbox" id="forceAll"> re-render all</label>
  <button id="approveBtn" class="{'on' if approved else ''}" onclick="toggleApproval()">
    {'✔ APPROVED — click to re-render &amp; re-export' if approved else 'APPROVE PROJECT FOR RENDER'}</button>
</header>
<div id="sentback" style="display:none;margin:0 14px 10px;padding:11px 14px;border-radius:8px;
  background:var(--warnb-bg);border-left:3px solid var(--warn);color:var(--warnb-ink);font-size:13px"></div>
<div id="pipebar">
  <span id="st_tick">① ticked <b>{n_included}/{n_segs}</b></span>
  <span id="st_appr">② approved <b>{'✓' if approved else '—'}</b></span>
  <span id="st_clips">③ clips <b id="clipcount">?</b></span>
  <span id="st_exp">④ export <b id="expstate">—</b></span>
  <span id="st_sil" style="{'color:var(--warn)' if _sil else ''}">🔇 dead air <b>{sil_str}</b></span>
  <div id="renderprog" style="display:none"><div id="renderbar"></div><span id="rendertxt"></span></div>
</div>
<div class="wrap">
<h1>{html.escape(title)} — combined: all {len(descs)} panels · story placement · render timing ({n_included} of {len(segs)} segments in the video, {_mmss(video_total)})</h1>
<p class="meta">Left half: system OCR/description and where each extracted panel lands in the script
(<b style="color:var(--sa-ink)">blue</b> carries narration unit ¶N on screen · <b style="color:var(--fold-ink)">yellow</b> folded — its
story is told in ¶N while another panel holds the screen · <b style="color:var(--omit-ink)">red</b> LEFT OUT, with the junk
filter's reason). Right column: the renderer's real timeline with LIVE EDITING — ✔ checkbox puts a panel on/off the
final video (folded panels get a slice of their unit's window; script-less panels get a silent hold), ⏱ sets a
segment's on-screen duration, "cut" buttons move the boundary between neighbours (narration audio slices seamlessly
if a cut lands mid-sentence ✂), ⠿ drag a seg card onto ANOTHER ROW to play that narration over that panel (shift-drop also moves it there in the story; card-on-card still reorders), ✚ adds a new narrated line (TTS). 🗑 rejects a segment (takes it OUT of the final video; ✅ puts it back — nothing is deleted). Badges: ⚠ hold &gt;12s ·
📜 tall strip (scroll-pan) · 🔇 silent hold · ✅/🗑 review status. Approving the project unlocks bulk rendering.</p>
<table>
<tr><th>#</th><th>Panel</th><th>System OCR</th><th>System description</th><th>Script placement</th><th>On-screen timing &amp; motion</th></tr>
{''.join(rows)}
</table></div>
<div id="cands" onclick="this.style.display='none'"><div class="inner" onclick="event.stopPropagation()"><h3>Pick replacement panel</h3><div id="candList"></div></div></div>
<dialog id="editDlg"><h3>Edit narration</h3>
<p class="hint">One box per spoken line. Edit the text and Save to re-voice just
that line (costs its TTS characters). 🗑 removes the line from the video
entirely — its audio file is kept, so re-adding the same sentence is free.</p>
<div id="editRows"></div>
<p><button onclick="saveEdit()">Save changes</button>
<button onclick="editDlg.close()">Cancel</button></p></dialog>
<script>document.querySelectorAll('input[data-partial]').forEach(function(c){{c.indeterminate=true;}});</script>
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
/* ---- R1/R5/R6: live finalize progress (render -> export -> link) ---- */
let finalizeTimer = null;
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
      txt.textContent = '❌ ' + (s.error || 'render failed');
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
        return `<div style="border-bottom:1px solid var(--rule);padding:6px 0;font-size:12px">
        <a href="${{e.url}}" target="_blank" style="color:var(--accent);font-weight:700">${{e.name}}</a>
        <button title="delete this export now" onclick="delExport('${{e.name}}','${{e.project}}')"
          style="float:right;background:none;border:1px solid var(--rule);color:var(--bad);border-radius:3px;cursor:pointer;font-size:11px;padding:1px 6px">✕</button><br>
        <span class="hint">${{e.duration ? (Math.floor(e.duration/60)+':'+String(Math.round(e.duration%60)).padStart(2,'0')) : '?'}} · ${{e.size_mb}} MB · ${{e.created}}</span><br>
        <span class="hint">${{e.project}}${{e.active_project ? ' · open' : ''}}</span>
        <a href="${{e.review_url || ('/review?project=' + e.project + '&name=' + e.name)}}"
           style="font-size:11px;margin-left:6px">review${{e.review_status && e.review_status !== 'review_pending' ? (' · ' + (e.superseded ? 'superseded' : e.review_status.replace('_',' '))) : ''}}</a>
        <span style="color:${{col}}"> · expires in ${{left < 1 ? (Math.round(left*24) + 'h') : (Math.round(left) + 'd')}}</span>
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
/* ---- drawers: ingest / projects / logs (ported from legacy UI) ---- */
{theme.SHARED_JS}

function toggleDrawer(name) {{
  for (const d of ['ingest','projects','tracker','logs','exports']) {{
    const el = document.getElementById('d_' + d);
    const btn = document.querySelector(`.navbtn[data-d="${{d}}"]`);
    const show = d === name && el.style.display !== 'block';
    el.style.display = show ? 'block' : 'none';
    if (btn) btn.classList.toggle('active', show);
  }}
  if (name === 'projects') loadProjects();
  if (name === 'tracker') loadTracker(0);
  if (name === 'logs') loadLogs();
  if (name === 'exports') loadExports();
  if (name === 'ingest') {{ paintIngest(); if (activeJob()) startIngestPoller(); }}
}}
/* Arriving from another page with ?open=<drawer> should land on that drawer,
   so the rail behaves the same wherever you clicked it. */
(function () {{
  var m = /[?&]open=([a-z]+)/.exec(location.search);
  if (m) setTimeout(function () {{ toggleDrawer(m[1]); }}, 60);
}})();
const ING_STAGES = ['scrape','split','describe','narrate','voice','match','segment'];
let ingestState = null, ingestPolling = false;
function activeJob() {{ return localStorage.getItem('activeIngestJob'); }}
function setActiveJob(id) {{ if (id) localStorage.setItem('activeIngestJob', id); else localStorage.removeItem('activeIngestJob'); }}
async function runIngest() {{
  const url = document.getElementById('ingurl').value.trim();
  if (!/^https?:\\/\\//.test(url)) {{ alert('Paste a full http(s) chapter URL'); return; }}
  const fresh = document.getElementById('ingfresh').checked;
  if (fresh && !confirm('Fresh re-ingest regenerates narration, TTS audio and the timeline for this chapter (cached descriptions and unchanged TTS lines are still reused). Continue?')) return;
  try {{
    const r = await j('/api/ingest', {{method:'POST', headers:{{'Content-Type':'application/json'}}, body: JSON.stringify({{url, fresh}})}});
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
  if (s.status === 'done' && s.project) {{
    box.innerHTML += `<button class="primary" style="margin-top:8px" onclick="activateProj('${{s.project.id}}')">Open “${{s.project.id}}” (${{s.project.n_segments}} segs)</button>`;
  }}
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
        if (s.status === 'done' && s.project) await activateProj(s.project.id);
        break;
      }}
      await new Promise(r => setTimeout(r, 1500));
    }}
  }} finally {{ ingestPolling = false; }}
}}
if (activeJob()) startIngestPoller();   // survive reloads mid-ingest
async function recoverActiveIngest() {{
  // Pick up any running ingest job started outside the UI (e.g. curl / restart)
  if (activeJob()) return;
  try {{
    const ij = await j('/api/logs/ingest');
    const running = (ij.jobs || []).find(x => x.status === 'running' || x.status === 'queued');
    if (running) {{ setActiveJob(running.job); startIngestPoller(); }}
  }} catch (e) {{}}
}}
recoverActiveIngest();
async function loadProjects() {{
  const box = document.getElementById('projlist');
  const d = await j('/api/projects');
  let htmlOut = '';
  if ((d.in_progress || []).length) {{
    htmlOut += '<div class="hint" style="text-transform:uppercase;font-weight:600;margin:4px 0">In progress</div>' +
      d.in_progress.map(p => `<div class="projrow">⏳ ${{p.slug}} <span class="hint">${{p.stage}} · ${{p.pct}}%</span>
        <button onclick="setActiveJob('${{p.job}}');toggleDrawer('ingest');startIngestPoller()">Watch</button></div>`).join('');
  }}
  const grouped = {{}};
  (d.projects || []).forEach(p => {{
    const s = p.series || 'Other';
    (grouped[s] = grouped[s] || []).push(p);
  }});
  for (const series in grouped) {{
    htmlOut += `<div class="projcard"><div class="ph">📚 ${{series}}</div>`;
    grouped[series].forEach(p => {{
      const label = p.chapter ? ('Chapter ' + p.chapter) : p.id;
      const _sel = (p.active || p.id === 'chapter-2 (current)')
        ? '<span style="display:inline-block;width:16px"></span>'
        : `<input type="checkbox" class="projsel" value="${{p.id}}" onchange="updateProjSel()" title="select for bulk delete">`;
      htmlOut += `<div class="projrow"><span>${{_sel}} ${{p.active ? '▶ ' : ''}}${{label}} <span class="hint">(${{p.n_segments}} segs${{p.duration ? ' · ' + p.duration + 's' : ''}})</span></span>
        <span>${{p.active ? '<span class="hint">active</span>' : `<button onclick="activateProj('${{p.id}}')">Open</button>`}}
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
async function loadTracker(refresh) {{
  const box = document.getElementById('trackerlist');
  box.innerHTML = refresh ? 'checking the source…' : 'loading…';
  try {{
    const d = await j('/api/tracker?refresh=' + (refresh ? 1 : 0));
    if (!(d.series || []).length) {{ box.innerHTML = 'No trackable series yet — ingest a chapter first.'; return; }}
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
        <div style="font-weight:700">${{sx.series}}</div>
        <div style="color:${{col}};font-size:12px">${{status}}</div>
        <div class="hint" style="font-size:11px">have ch ${{sx.highest_have || '—'}} · latest ch ${{sx.latest || '?'}} · ${{sx.have_count}} ingested${{sx.backfill_count ? (' · ' + sx.backfill_count + ' earlier chapters skipped') : ''}}</div>
        ${{next}}${{more}}
      </div>`;
    }}).join('');
  }} catch (e) {{ box.innerHTML = 'Failed to load tracker: ' + (e.message || e); }}
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
  await j('/api/activate', {{method:'POST', headers:{{'Content-Type':'application/json'}}, body: JSON.stringify({{id}})}});
  location.reload();
}}
let logsPolling = false;
async function loadLogs() {{
  // ONE list. Ingest jobs and render/export jobs used to live in separate
  // sections with different shapes, and only the ingest list auto-refreshed.
  const box = document.getElementById('joblist');
  const pill = (txt, bg, fg) => `<span style="background:${{bg}};color:${{fg}};font-size:10px;font-weight:700;
      padding:1px 7px;border-radius:99px;letter-spacing:.03em">${{txt}}</span>`;
  const STAT = {{
    running:['var(--sa-bg)','var(--sa-ink)'], queued:['var(--gray-bg)','var(--gray-ink)'], paused:['var(--warnb-bg)','var(--warn)'],
    pausing:['var(--warnb-bg)','var(--warn)'], done:['var(--okb-bg)','var(--okb-ink)'], error:['var(--badb-bg)','var(--bad)'],
    cancelled:['var(--gray-bg)','var(--gray-ink)']
  }};
  const live = st => ['running','queued','paused','pausing'].includes(st);

  async function rows() {{
    const [ij, rj] = await Promise.all([
      j('/api/logs/ingest').catch(() => ({{jobs:[]}})),
      j('/api/jobs').catch(() => ({{jobs:[]}})),
    ]);
    const out = [];
    for (const x of (ij.jobs || [])) out.push({{
      id: x.job, kind: 'ingest', status: x.status || 'queued',
      title: (x.url || '').replace('https://','').replace(/^www\\./,''),
      detail: [x.stage, (x.pct != null ? x.pct + '%' : null), x.msg].filter(Boolean).join(' · '),
      error: x.error, ts: x.ts || 0
    }});
    for (const x of (rj.jobs || [])) out.push({{
      id: x.job, kind: x.type || 'render', status: x.status || 'queued',
      title: x.project || '(project)',
      detail: [x.stage, (x.total ? ('clip ' + x.done + '/' + x.total) : null),
               (x.export ? ('→ ' + x.export) : null)].filter(Boolean).join(' · '),
      error: x.error, ts: x.ts || 0
    }});
    return out.sort((a,b) => (b.ts||0) - (a.ts||0)).slice(0, 30);
  }}

  const keep = new Set(_jobsChecked().map(c => c.value));   // survive a re-render
  try {{
    const list = await rows();
    box.innerHTML = list.length ? list.map(x => {{
      const [bg,fg] = STAT[x.status] || STAT.queued;
      const ctl = live(x.status)
        ? `<button class="mini" title="pause at the next step" onclick="jobCtl('${{x.id}}','pause')">⏸</button>
           <button class="mini" title="resume" onclick="jobCtl('${{x.id}}','resume')">▶</button>
           <button class="mini" title="stop this job" onclick="jobCtl('${{x.id}}','stop')">⏹</button>`
        : `<button class="mini" title="remove this record" onclick="jobCtl('${{x.id}}','delete')">🗑</button>`;
      return `<div style="border-bottom:1px solid var(--rule);padding:7px 0;display:flex;gap:8px;align-items:flex-start">
        <input type="checkbox" class="jobsel" value="${{x.id}}" data-live="${{live(x.status)?1:0}}" onchange="updateJobSel()" style="margin-top:3px">
        <div style="flex:1;min-width:0">
          <div style="display:flex;gap:6px;align-items:center;flex-wrap:wrap">
            ${{pill(x.kind, 'var(--sa-bg)', 'var(--sa-ink)')}} ${{pill(x.status, bg, fg)}}
            <span style="font-size:12px;font-weight:600;overflow:hidden;text-overflow:ellipsis">${{x.title}}</span>
          </div>
          <div class="hint" style="font-size:11px;word-break:break-all">${{x.detail || ''}}</div>
          ${{x.error ? `<div style="color:var(--bad);font-size:11px">⚠ ${{x.error}}</div>` : ''}}
        </div>
        <div style="display:flex;gap:3px;flex-shrink:0">${{ctl}}</div>
      </div>`;
    }}).join('') : 'No jobs yet.';
    if (keep.size) {{
      document.querySelectorAll('.jobsel').forEach(c => {{ c.checked = keep.has(c.value); }});
    }}
    updateJobSel();
  }} catch (e) {{ box.innerHTML = `<span style="color:var(--bad)">⚠ could not load jobs — ${{e.message || e}}</span>`; }}

  try {{
    const uj = await j('/api/logs/usage?limit=60');
    const su = uj.summary || {{}}, life = su.lifetime || {{}};
    document.getElementById('logusage').innerHTML =
      `<div style="font-size:12px;margin-bottom:6px"><b>${{su.gemini_calls || 0}}</b> Gemini · <b>${{(su.tts_chars || 0).toLocaleString()}}</b> TTS chars ·
       est <b>$${{(su.est_cost_usd || 0).toFixed(3)}}</b> on ${{su.date || ''}} (ET)<br>
       all-time: <b>${{(life.gemini_calls || 0) + (su.gemini_calls || 0)}}</b> Gemini ·
       <b>$${{((life.est_cost_usd || 0) + (su.est_cost_usd || 0)).toFixed(2)}}</b></div>` +
      (uj.calls || []).slice(-40).reverse().map(c =>
        `<div class="hint" style="border-bottom:1px solid var(--rule);padding:2px 0">
         ${{c.kind}} · ${{c.model || ''}} · ${{c.units}} ${{c.unit || ''}} · $${{(c.est_cost_usd || 0).toFixed(4)}}</div>`).join('');
  }} catch (e) {{ document.getElementById('logusage').innerHTML = 'usage unavailable'; }}

  const stamp = document.getElementById('logstamp');
  if (stamp) stamp.textContent = 'updated ' + new Date().toLocaleTimeString();

  if (!logsPolling) {{
    logsPolling = true;
    (async () => {{
      while (document.getElementById('d_logs').style.display === 'block') {{
        await new Promise(r => setTimeout(r, 5000));
        if (document.getElementById('d_logs').style.display !== 'block') break;
        // Never refresh out from under a selection — the re-render replaces
        // every row and the ticks vanish, which reads as boxes unticking
        // themselves. A selection means the user is mid-action; wait.
        if (_jobsChecked().length) continue;
        await loadLogs();
      }}
      logsPolling = false;
    }})();
  }}
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
