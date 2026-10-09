"""Phase D — the series hook (docs/audit/10_SEO_SYSTEM_SPEC.md §3.2, §5, T4).

The one creative decision per series: three hook candidates, each built on one
of the five verified mechanics and each clause traced to a cached source (the
Series Bible's premise, the web research, the series page) — never a single
chapter's content. The owner picks one (or types their own); it becomes
`title_lock.series_hook` and every chapter's title is "[N] hook — series |
Manhwa Recap" from then on.

One metered model call (series_research._post → usage.gate). Nothing is
locked without the owner's choice.
"""
import json
import time

import chapter_seo as cs

MECHANICS = {
    1: "Setup → Twist — \"He [X], But [Y]!\" (1,296,437 views precedent)",
    2: "Underdog reversal — \"Everyone [dismisses him] But He [dominates]\" (1,056,629)",
    3: "Contrast pairing — high status → low / real → fantasy (1,223,712; 159,171 on Murim Psychopath)",
    4: "Concrete numbers — \"10,000 Hours\", \"2000 Years\", \"DAY 1\" (679,956)",
    5: "Identity/misconception reveal — \"He Thought [X]… Unfortunately, [Y]\" (13,705, premise-matched)",
}


def sources(pack, bible, web):
    """The cached series-level material a hook may draw on, with URLs."""
    out = []
    ws = (bible or {}).get("world_setting") or {}
    rs = ((bible or {}).get("research") or {}).get("sources") or []
    src_url = (pack or {}).get("source_url") or ""
    if ws.get("premise"):
        out.append({"text": ws["premise"], "url": (rs[0].get("url") if rs and isinstance(rs[0], dict) else "")
                    or src_url, "kind": "series premise (Series Bible)"})
    facts = (web or {}).get("facts") or {}
    if facts.get("keywords"):
        out.append({"text": "Keywords people use for it: " + ", ".join(facts["keywords"][:12]),
                    "url": next((x.get("url") for x in (web or {}).get("sources") or [] if x.get("url")), ""),
                    "kind": "web research"})
    mc = (pack or {}).get("characters_main") or []
    if mc:
        out.append({"text": "Main character: " + mc[0], "url": src_url, "kind": "cast"})
    return out


def prompt(series, budget, srcs):
    mech = "\n".join(f"{k}. {v}" for k, v in MECHANICS.items())
    src = "\n".join(f"[{i}] ({s['kind']}) {s['text']}  <{s['url']}>" for i, s in enumerate(srcs))
    return f"""You write the ONE title hook a YouTube manhwa-recap channel will use for EVERY chapter of the series "{series}".
Each video title will be: [chapter number] HOOK — {series} | Manhwa Recap

Write exactly 3 candidate hooks, each using a DIFFERENT mechanic from this list (these patterns have verified 1M+ view precedents in the niche):
{mech}

SOURCES (series-level only; every clause of a hook must come from one of these — invent nothing):
{src}

Rules:
- At most {budget} characters each (count carefully). Title Case, punctuation allowed.
- Series-level: the premise of the whole series, never a single chapter's event; no chapter numbers.
- Never vague filler verbs like "shocks", "stuns", "shakes" + a place ("His Class Change Shocks Murim" was rejected).
- Prefer mechanics 1–3 (strongest precedent) unless the premise is a misconception, where 5 fits best.
- Do not name the series in the hook (it is already in the title).

Return ONLY JSON: [{{"hook": "...", "mechanic": 1-5, "source_index": [0, ...], "why": "one sentence"}}]"""


def _loads(text):
    """A JSON list or object from a model reply (fences and chatter tolerated)."""
    import re
    t = re.sub(r"^```(?:json)?|```$", "", (text or "").strip(), flags=re.M).strip()
    try:
        return json.loads(t)
    except ValueError:
        pass
    for a, b in (("[", "]"), ("{", "}")):
        i, j = t.find(a), t.rfind(b)
        if i >= 0 and j > i:
            try:
                return json.loads(t[i:j + 1])
            except ValueError:
                continue
    return []


def generate(series, pack, bible, web, api_key, _post=None):
    """3 validated candidates [{hook, mechanic, mechanic_name, provenance, why, chars, problems}]."""
    import series_research as sr
    budget = max(cs.hook_budget(series, 999), cs.TITLE_MAX - len(cs.short_title(999, "")))
    long_budget = cs.hook_budget(series, 999)
    srcs = sources(pack, bible, web)
    if not srcs:
        raise ValueError("no series-level source to build a hook from — run the series research first")
    post = _post or (lambda body: sr._post(body, api_key))
    resp = post({"contents": [{"parts": [{"text": prompt(series, long_budget if long_budget >= cs.HOOK_MIN_BUDGET
                                                         else budget, srcs)}]}],
                 # 8192: a thinking model spends part of the budget before it
                 # writes; at 2048 the live call on 2026-10-08 returned nothing
                 "generationConfig": {"temperature": 0.7, "maxOutputTokens": 8192,
                                      "responseMimeType": "application/json"}})
    raw = ""
    try:
        raw = sr._text(resp) or ""
        rows = _loads(raw)
    except Exception:
        rows = []
    if isinstance(rows, dict):                 # {"candidates": [...]} or {"hooks": [...]}
        rows = next((v for v in rows.values() if isinstance(v, list)), [])
    out = []
    for r in rows if isinstance(rows, list) else []:
        h = " ".join(str(r.get("hook") or "").split()).strip(" \"'")
        if not h:
            continue
        idx = [i for i in (r.get("source_index") or []) if isinstance(i, int) and 0 <= i < len(srcs)]
        m = int(r.get("mechanic") or 0) if str(r.get("mechanic") or "").isdigit() else 0
        out.append({"hook": h, "mechanic": m, "mechanic_name": MECHANICS.get(m, "").split(" — ")[0],
                    "provenance": [srcs[i]["url"] for i in idx if srcs[i]["url"]] or [s["url"] for s in srcs if s["url"]][:1],
                    "why": str(r.get("why") or "")[:200], "chars": len(h),
                    "variant": cs.title_variant(series, h),
                    "example_title": cs.build_title(45, {"series_hook": h}, series),
                    "problems": cs.hook_problems(h, series)})
    res = {"at": time.time(), "candidates": out[:3], "sources": srcs}
    if not out:
        fin = ((resp or {}).get("candidates") or [{}])[0].get("finishReason")
        res["error"] = f"the model returned no usable hooks (finish: {fin}); reply started: {raw[:200]!r}"
    return res


def lock(series, hook, mechanic=None, provenance=None):
    """The owner's choice -> title_lock (T3: the variant is fixed with it)."""
    h = " ".join((hook or "").split())
    probs = cs.hook_problems(h, series)
    if probs:
        raise ValueError("; ".join(probs))
    return {"skeleton": "[{{chapter_number}}] {{series_hook}} — {{series_name_en}} | Manhwa Recap",
            "series_hook": h, "hook_mechanic": mechanic, "hook_provenance": list(provenance or []),
            "variant": cs.title_variant(series, h), "decided_at": time.time(), "approved_by_user": True}
