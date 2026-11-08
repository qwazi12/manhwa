"""Posting gate + sheet control (owner, 2026-11-07, "POSTING GATE + SHEET CONTROL").

The "Posting Review (next 7 days)" Google Sheet, Flamingo tab, controls each video:

  Approve  -> the sheet's title, description (the lines written) and hashtags are used
              exactly as written, at the sheet's slot (YouTube scheduled publish HH:00:00).
              On a video still in Video review, Approve moves it into the queue at that slot.
  Skip     -> not published.
  Edit     -> held, listed under NEEDS OWNER.
  (blank)  -> publishes at its slot only when it is still blank 2 h before the slot.

A video that is not in the sheet does not publish. Slot edits that are off the hour, or
that put two videos in one hour, are refused (the earlier accepted slot stays) and listed
under NEEDS OWNER. A video already uploaded is never changed from the sheet: a different
slot or text for it is listed under NEEDS OWNER (the owner OKs it in chat first).

Off switch: POST /api/sheet_gate {"on": false} (kept on the volume, takes effect on the next
scheduler pass), or SHEET_GATE_OFF=1 on Railway (overrides the stored switch). Off = the
schedule works exactly as before the gate (posting times, queue order).

Pure logic here; server.py reads the sheet and applies the results.
"""
import io
import json
import os
import re
import threading
import time
import zipfile
import xml.etree.ElementTree as ET
from datetime import datetime
from zoneinfo import ZoneInfo

SHEET_ID = os.environ.get("POSTING_SHEET_ID") or "1cTo8P43DiqqxV7oAtavHFOVBzm3vmYjEzVjA8q4CQsU"
TAB = os.environ.get("POSTING_SHEET_TAB") or "Flamingo"
BLANK_OK_S = 2 * 3600             # blank Decision 2 h before the slot = publish
NAME = "_sheet_gate.json"
TZ = "America/New_York"
_lock = threading.Lock()


# ------------------------------------------------------------------ switch + state
def _path(root):
    return os.path.join(root, NAME)


def load(root):
    try:
        with open(_path(root), encoding="utf-8") as f:
            d = json.load(f)
        if isinstance(d, dict):
            return d
    except (OSError, ValueError):
        pass
    return {}


def _save(root, d):
    tmp = _path(root) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, indent=1)
    os.replace(tmp, _path(root))


def env_off():
    return os.environ.get("SHEET_GATE_OFF", "").strip().lower() in ("1", "true", "yes", "on")


def enabled(root):
    """On by default (owner's rule); off by the stored switch or SHEET_GATE_OFF=1."""
    return not env_off() and load(root).get("on", True) is not False


def set_on(root, on, by="api"):
    with _lock:
        d = load(root)
        before = d.get("on", True) is not False
        d.update(on=bool(on), set_at=time.time(), set_by=by)
        d.setdefault("switch_log", []).append({"at": d["set_at"], "by": by, "from": before, "to": bool(on)})
        d["switch_log"] = d["switch_log"][-50:]
        _save(root, d)
        return d


def add_log(root, entries):
    """Append applied changes with increasing seq numbers (the watcher writes APPLIED csv from them)."""
    if not entries:
        return []
    with _lock:
        d = load(root)
        seq = int(d.get("log_seq") or 0)
        out = []
        for e in entries:
            seq += 1
            out.append({"seq": seq, "at": time.time(), **e})
        d["log"] = ((d.get("log") or []) + out)[-300:]
        d["log_seq"] = seq
        _save(root, d)
        return out


# ------------------------------------------------------------------ reading the sheet
_NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
       "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships"}


def xlsx_tabs(b):
    """{tab name: [[cell text, ...], ...]} from an .xlsx export (no openpyxl)."""
    z = zipfile.ZipFile(io.BytesIO(b))
    ss = []
    if "xl/sharedStrings.xml" in z.namelist():
        for si in ET.fromstring(z.read("xl/sharedStrings.xml")).findall("m:si", _NS):
            ss.append("".join(t.text or "" for t in si.iter("{%s}t" % _NS["m"])))
    wb = ET.fromstring(z.read("xl/workbook.xml"))
    rels = ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
    tgt = {r.get("Id"): r.get("Target") for r in rels}
    out = {}
    for s in wb.find("m:sheets", _NS):
        path = "xl/" + tgt[s.get("{%s}id" % _NS["r"])].lstrip("/").replace("xl/", "")
        rows = []
        for row in ET.fromstring(z.read(path)).iter("{%s}row" % _NS["m"]):
            cells = {}
            for c in row.findall("m:c", _NS):
                col = re.match(r"[A-Z]+", c.get("r")).group(0)
                n = 0
                for ch in col:
                    n = n * 26 + ord(ch) - 64
                v, t = c.find("m:v", _NS), c.get("t")
                if t == "s" and v is not None:
                    val = ss[int(v.text)]
                elif t == "inlineStr":
                    val = "".join(x.text or "" for x in c.iter("{%s}t" % _NS["m"]))
                else:
                    val = v.text if v is not None else ""
                cells[n - 1] = val or ""
            rows.append([cells.get(i, "") for i in range(max(cells) + 1)] if cells else [])
        out[s.get("name")] = rows
    return out


def fetch_xlsx(ds, sheet_id=None):
    """The sheet as .xlsx bytes through the Drive API (service account)."""
    import urllib.parse
    import urllib.request
    u = (f"https://www.googleapis.com/drive/v3/files/{sheet_id or SHEET_ID}/export?"
         + urllib.parse.urlencode({"mimeType": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}))
    req = urllib.request.Request(u, headers={"Authorization": "Bearer " + ds._token()})
    return urllib.request.urlopen(req, context=ds._ctx(), timeout=60).read()


def parse_rows(grid):
    """Flamingo tab grid -> [{queue_id, slot, posts_to, title, desc, hashtags (None = no column),
    decision, notes, row}]. Raises ValueError when the header is missing."""
    hdr_i = next((i for i, r in enumerate(grid) if "Decision" in r and "Queue ID" in r), None)
    if hdr_i is None:
        raise ValueError(f"the {TAB} tab has no 'Queue ID' / 'Decision' header")
    hdr = [h.strip() for h in grid[hdr_i]]

    def col(*starts):
        return next((i for i, h in enumerate(hdr) if any(h.lower().startswith(s.lower()) for s in starts)), None)

    c = {"queue_id": col("Queue ID"), "slot": col("Slot"), "posts_to": col("Posts to"),
         "title": col("Proposed YouTube title"), "desc": col("Proposed YouTube description"),
         "hashtags": next((i for i, h in enumerate(hdr) if "hashtag" in h.lower()), None),
         "decision": col("Decision"), "notes": col("Your edits")}
    out = []
    for n, r in enumerate(grid[hdr_i + 1:], start=hdr_i + 2):
        r = list(r) + [""] * (len(hdr) - len(r))
        g = lambda k: (r[c[k]] if c[k] is not None else "").strip()
        if not g("queue_id"):
            continue
        out.append({"queue_id": g("queue_id"), "slot": g("slot"), "posts_to": g("posts_to"),
                    "title": g("title"), "desc": (r[c["desc"]] if c["desc"] is not None else "").strip("\n"),
                    "hashtags": g("hashtags") if c["hashtags"] is not None else None,
                    "decision": g("decision"), "notes": g("notes"), "row": n})
    return out


# ------------------------------------------------------------------ slots
_DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
_MONTHS = ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec")
_SLOT = re.compile(r"^\s*(?:(mon|tue|wed|thu|fri|sat|sun)[a-z]*\.?,?\s+)?"
                   r"(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+(\d{1,2})(?:,?\s*(\d{4}))?,?\s+"
                   r"(?:at\s+)?(\d{1,2})(?::(\d{2}))?\s*([ap])\.?m\.?\s*(?:et|est|edt)?\s*$", re.I)


def parse_slot(text, now, tz=TZ):
    """'Sat Nov 7, 10:00 PM' (Eastern) -> (epoch, None) or (None, why). Off-hour times are refused."""
    m = _SLOT.match(text or "")
    if not m:
        return None, f"slot {text!r} is not like 'Sun Nov 8, 10:00 AM'"
    wd, mon, day, year, h, mi, ap = m.groups()
    h, mi = int(h), int(mi or 0)
    if not 1 <= h <= 12 or mi > 59:
        return None, f"slot {text!r} is not a real time"
    if mi != 0:
        return None, f"slot {text!r} is off the hour (videos go live at HH:00 only)"
    h = h % 12 + (12 if ap.lower() == "p" else 0)
    z = ZoneInfo(tz)
    here = datetime.fromtimestamp(now, z)
    best = None
    for y in ([int(year)] if year else (here.year - 1, here.year, here.year + 1)):
        try:
            dt = datetime(y, _MONTHS.index(mon.lower()) + 1, int(day), h, 0, tzinfo=z)
        except ValueError:
            continue
        if best is None or abs(dt.timestamp() - now) < abs(best.timestamp() - now):
            best = dt
    if best is None:
        return None, f"slot {text!r} is not a real date"
    if wd and _DAYS[best.weekday()] != wd.lower():
        return None, f"slot {text!r}: {best.strftime('%b')} {best.day} is a {best.strftime('%A')}"
    return best.timestamp(), None


def label(at, tz=TZ):
    d = datetime.fromtimestamp(at, ZoneInfo(tz))
    return f"{d.strftime('%a %b')} {d.day}, {d.hour % 12 or 12}:{d.minute:02d} {'AM' if d.hour < 12 else 'PM'}"


def slot_key(at, tz=TZ):
    """('YYYY-MM-DD', 'HH:MM') local: the schedule's slots_done keys."""
    d = datetime.fromtimestamp(at, ZoneInfo(tz))
    return d.strftime("%Y-%m-%d"), d.strftime("%H:%M")


# ------------------------------------------------------------------ the gate
def gate(row, slot_at, now):
    """-> ("post" | "hold" | "skip", why) for one video at its slot."""
    if row is None:
        return "hold", "not in the Posting Review sheet"
    d = (row.get("decision") or "").strip().lower()
    if d == "approve":
        return ("post", "Approve") if slot_at else ("hold", "Approve, but no accepted slot")
    if d == "skip":
        return "skip", "Decision is Skip"
    if d == "edit":
        return "hold", "Decision is Edit"
    if d:
        return "hold", f"Decision {row['decision']!r} is not Approve / Skip / Edit"
    if not slot_at:
        return "hold", "Decision blank and no accepted slot"
    if slot_at - now <= BLANK_OK_S:
        return "post", "Decision still blank 2 h before the slot"
    return "hold", "Decision blank (posts if still blank 2 h before the slot)"


def evaluate(rows, videos, prev_slots, now, tz=TZ):
    """Pure. Which slot each video gets, what the sheet asks for, and what needs the owner.

    rows: parse_rows() output. videos: {project: {"lane": "queued"|"pending"|"uploaded",
    "publish_at": epoch for uploaded ones}}. prev_slots: {project: epoch} accepted earlier.
    Returns {"slots": {project: epoch}, "rows": [per row: queue_id, lane, decision, slot_at,
    slot_label, refused, gate, why], "needs_owner": [text, ...]}.
    """
    needs, out_rows, slots = [], [], {}
    seen = {}
    for r in rows:
        if r["queue_id"] in seen:
            needs.append(f"{r['queue_id']}: listed twice in the sheet (rows {seen[r['queue_id']]} and {r['row']}); "
                         "the first row is used")
            continue
        seen[r["queue_id"]] = r["row"]
    rows = [r for r in rows if seen.get(r["queue_id"]) == r["row"]]
    by_hour = {}
    # already uploaded videos hold their hour, whatever the sheet says
    for pid, v in videos.items():
        if v.get("lane") == "uploaded" and v.get("publish_at"):
            by_hour[int(v["publish_at"] // 3600)] = pid
    parsed = {}
    for r in rows:
        at, err = parse_slot(r["slot"], now, tz)
        parsed[r["queue_id"]] = (at, err)
    # unchanged slots first, so a new edit that collides is the one refused
    order = sorted(rows, key=lambda r: (parsed[r["queue_id"]][0] != prev_slots.get(r["queue_id"]), r["row"]))
    refused = {}
    for r in order:
        pid = r["queue_id"]
        v = videos.get(pid) or {}
        at, err = parsed[pid]
        if v.get("lane") == "uploaded":
            pa = v.get("publish_at")
            if pa:
                slots[pid] = pa
            if at and pa and int(at) != int(pa):
                refused[pid] = (f"already uploaded to go live {label(pa, tz)}; moving it to {label(at, tz)} "
                                "needs the owner's OK in chat")
            elif err:
                refused[pid] = err
            continue
        if (r["decision"] or "").strip().lower() == "skip":
            continue                                    # a Skip never holds an hour
        if err:
            refused[pid] = err
        elif by_hour.get(int(at // 3600)) not in (None, pid):
            other = by_hour[int(at // 3600)]
            refused[pid] = f"two videos in one hour: {label(at, tz)} is already {other}"
        else:
            by_hour[int(at // 3600)] = pid
            slots[pid] = at
            continue
        old = prev_slots.get(pid)
        if old and by_hour.get(int(old // 3600)) in (None, pid) and old > now:
            by_hour[int(old // 3600)] = pid
            slots[pid] = old                           # the earlier accepted slot stays
    for r in rows:
        pid = r["queue_id"]
        v = videos.get(pid)
        at = slots.get(pid)
        lane = (v or {}).get("lane") or "unknown"
        if v is None:
            g, why = "hold", "Queue ID not found in the studio (no queued or Video-review video)"
            needs.append(f"{pid}: {why}")
        elif lane == "uploaded":
            g, why = "uploaded", "already uploaded"
        else:
            g, why = gate(r, at, now)
            if g == "hold" and (r["decision"] or "").strip().lower() == "edit":
                needs.append(f"{pid}: Decision is Edit — held" + (f" (notes: {r['notes']})" if r["notes"] else ""))
            elif g == "hold" and (r["decision"] or "").strip() and "not Approve" in why:
                needs.append(f"{pid}: {why}")
        if pid in refused:
            needs.append(f"{pid}: slot refused — {refused[pid]}"
                         + (f"; keeps {label(slots[pid], tz)}" if slots.get(pid) and lane != "uploaded" else ""))
        out_rows.append({"queue_id": pid, "row": r["row"], "lane": lane, "decision": r["decision"] or "",
                         "slot_sheet": r["slot"], "slot_at": at, "slot_label": label(at, tz) if at else "",
                         "refused": refused.get(pid), "gate": g, "why": why})
    for pid, v in videos.items():
        if v.get("lane") == "queued" and pid not in seen:
            needs.append(f"{pid}: queued but not in the Posting Review sheet — held")
            out_rows.append({"queue_id": pid, "row": None, "lane": "queued", "decision": "", "slot_sheet": "",
                             "slot_at": None, "slot_label": "", "refused": None, "gate": "hold",
                             "why": "not in the Posting Review sheet"})
    return {"slots": slots, "rows": out_rows, "needs_owner": needs}


def next_free(now, min_lead, times, taken, cap=None, tz=TZ, days=14):
    """The first posting time (times: ["10:00", ...], local tz) at least min_lead seconds
    ahead whose hour no other video holds and whose day is under cap. taken: slot epochs
    of the other videos. -> epoch or None."""
    from datetime import timedelta
    z = ZoneInfo(tz)
    hours = {int(t // 3600) for t in taken}
    per_day = {}
    for t in taken:
        k = datetime.fromtimestamp(t, z).strftime("%Y-%m-%d")
        per_day[k] = per_day.get(k, 0) + 1
    hm = sorted({(int(x.split(":")[0]), int(x.split(":")[1])) for x in times or []})
    d0 = datetime.fromtimestamp(now, z).date()
    for i in range(days + 1):
        day = d0 + timedelta(days=i)
        for h, m in hm:
            at = datetime(day.year, day.month, day.day, h, m, tzinfo=z).timestamp()
            if at - now < min_lead or int(at // 3600) in hours:
                continue
            if cap and per_day.get(day.isoformat(), 0) >= cap:
                continue
            return at
    return None


def auto_move(ev, videos, moves, now, min_lead, times, cap=None, tz=TZ):
    """HEAD_RULES_20261107T1345Z rule 3 (owner, 2026-11-08): a queued video the gate lets
    post whose slot is too close to upload ahead (or has passed) moves to the next free
    top-of-hour posting time — never doubling up, never between slots. A move is kept
    while the sheet still shows the slot it was moved from; a new slot in the sheet wins.
    Pure. -> (slots, moves, moved [{queue_id, from, to}], needs_owner [text])."""
    slots, new_moves, moved, needs = dict(ev["slots"]), {}, [], []
    post = [x["queue_id"] for x in ev["rows"] if x["gate"] == "post"
            and (videos.get(x["queue_id"]) or {}).get("lane") == "queued" and slots.get(x["queue_id"])]
    for pid in sorted(post, key=lambda p: (slots[p], p)):
        at = slots[pid]
        m = moves.get(pid)
        cur = m["to"] if m and int(m["from"]) == int(at) else at
        if cur == at and at - now >= min_lead:
            continue                                   # its own sheet slot, still in time
        others = [v for p, v in slots.items() if p != pid]
        day = datetime.fromtimestamp(cur, ZoneInfo(tz)).date()
        day_n = sum(1 for v in others if datetime.fromtimestamp(v, ZoneInfo(tz)).date() == day)
        ok = (cur != at and cur - now >= min_lead and int(cur // 3600) not in {int(v // 3600) for v in others}
              and not (cap and day_n >= cap))
        to = cur if ok else next_free(now, min_lead, times, others, cap, tz)
        if to is None:
            needs.append(f"{pid}: missed {label(at, tz)} and there is no free posting time in the next "
                         "14 days — held")
            slots.pop(pid)
            continue
        if int(to) != int(cur) or not m or int(m["from"]) != int(at):
            moved.append({"queue_id": pid, "from": cur, "to": to, "sheet": at})
        slots[pid] = to
        new_moves[pid] = {"from": at, "to": to}
    for x in ev["rows"]:
        if x["queue_id"] in new_moves:
            to = new_moves[x["queue_id"]]["to"]
            x["slot_at"], x["slot_label"] = to, label(to, tz)
            x["why"] += f" — missed {label(new_moves[x['queue_id']]['from'], tz)}, moved to the next free slot"
    return slots, new_moves, moved, needs


# ------------------------------------------------------------------ text
_TAG = re.compile(r"#[^\s#]+")


def _is_tag_line(line):
    s = line.strip()
    return bool(s) and all(_TAG.fullmatch(w) for w in s.split())


def sheet_text(md, row):
    """The video's metadata with the sheet's title / description lines / hashtags as written.
    The description cell's text lines replace the description's first lines; its hashtag-only
    lines (or a Hashtags column, when the sheet has one) replace the description's hashtag
    line, so the tags are never doubled. -> (new metadata, [changed fields], [problems]).
    Problems mean: do not apply."""
    new, probs = dict(md), []
    title = (row.get("title") or "").strip()
    if title:
        if len(title) > 100:
            probs.append(f"sheet title is {len(title)} characters (YouTube allows 100)")
        if "<" in title or ">" in title:
            probs.append("sheet title has < or >, which YouTube refuses")
        new["title"] = title
    lines = (md.get("description") or "").split("\n")
    cell = [ln.rstrip() for ln in (row.get("desc") or "").replace("\r\n", "\n").split("\n")]
    text = [ln for ln in cell if not _is_tag_line(ln)]
    while text and not text[-1].strip():
        text.pop()
    if text:
        lines = text + lines[len(text):]
    col = row.get("hashtags")
    tags = (col.split() if col is not None and col.strip()
            else [w for ln in cell if _is_tag_line(ln) for w in ln.split()])
    if tags:
        bad = [w for w in tags if not _TAG.fullmatch(w)]
        if bad:
            probs.append(f"sheet hashtags not like #word: {' '.join(bad)}")
        if len(tags) > 15:
            probs.append(f"{len(tags)} hashtags (YouTube ignores them all over 15)")
        kept = [ln for ln in lines if not _is_tag_line(ln)]
        while kept and not kept[-1].strip():
            kept.pop()
        lines = kept + ["", " ".join(tags)]
    new["description"] = "\n".join(lines)
    if len(new["description"]) > 5000:
        probs.append(f"description would be {len(new['description'])} characters (YouTube allows 5000)")
    changed = [k for k in ("title", "description") if new.get(k) != md.get(k)]
    if tags:
        new["hashtags"] = tags
        if tags != re.findall(r"#[^\s#]+", "\n".join(ln for ln in (md.get("description") or "").split("\n")
                                                     if _is_tag_line(ln))):
            changed.append("hashtags")
    return new, changed, probs
