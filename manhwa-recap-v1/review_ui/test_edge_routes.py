"""Every browser-reachable backend route must also be routed by the edge.

The live domain is Vercel in front of Railway, and the Vercel rewrite list is
an ALLOWLIST — the domain-owning config has no catch-all. So a new non-/api
route on the backend is invisible on the real site until BOTH vercel.json
files list it, and it fails as a plain 404 with no hint of the cause.

This has now happened twice: /segimg (Session 26) and /review (Session 27),
the second time after the lesson was already written down. Nothing compared
the two lists, so this test does.

Run: python3 test_edge_routes.py
"""
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
CONFIGS = [os.path.join(ROOT, "vercel.json"),
           os.path.join(HERE, "static", "vercel.json")]

# Routes that must NOT be exposed through the public domain. Health checks are
# Railway-internal; adding them to the edge would publish them for no reason.
EXEMPT = {"health", "ready"}


def backend_routes():
    """First path segment of every GET/POST route declared in server.py."""
    src = open(os.path.join(HERE, "server.py"), encoding="utf-8").read()
    out = set()
    for path in re.findall(r'@app\.(?:get|post)\("(/[^"]*)"\)', src):
        if path.startswith("/api"):
            continue                     # /api/:path* is a single wildcard rule
        seg = path.strip("/").split("/")[0]
        if seg:
            out.add(seg)
    return out


def edge_sources(cfg):
    d = json.load(open(cfg, encoding="utf-8"))
    return {r["source"].strip("/").split("/")[0] for r in d.get("rewrites", [])}


def main():
    r = []
    routes = backend_routes()
    r.append(("server.py routes were discovered", len(routes) >= 5))

    per_cfg = {}
    for cfg in CONFIGS:
        exists = os.path.exists(cfg)
        r.append((f"{os.path.relpath(cfg, ROOT)} exists", exists))
        if exists:
            per_cfg[cfg] = edge_sources(cfg)

    for seg in sorted(routes - EXEMPT):
        for cfg, srcs in per_cfg.items():
            r.append((f"/{seg} is routed by {os.path.basename(os.path.dirname(cfg)) or 'repo root'}",
                      seg in srcs))

    # /api must be forwarded, or the whole board dies
    for cfg, srcs in per_cfg.items():
        r.append((f"/api is routed by {os.path.basename(os.path.dirname(cfg)) or 'repo root'}",
                  "api" in srcs))

    # the domain-owning config has no catch-all, so its allowlist is the whole
    # story — state that explicitly so nobody assumes a fallback exists
    static_cfg = CONFIGS[1]
    if static_cfg in per_cfg:
        d = json.load(open(static_cfg, encoding="utf-8"))
        has_catch_all = any(r_["source"] in ("/(.*)", "/:path*")
                            for r_ in d.get("rewrites", []))
        r.append(("the domain-owning config still has NO catch-all "
                  "(so the allowlist must stay complete)", not has_catch_all))

    # ---- middleware invariants. The documented failure mode: an early
    # `return next()` that skipped the header meant the page shell loaded
    # while every image and API call 401'd, which looks like a broken app
    # rather than a config problem. One exit, and the header set before it.
    mw = [os.path.join(ROOT, "middleware.js"),
          os.path.join(HERE, "static", "middleware.js")]
    bodies = {}
    for f in mw:
        r.append((f"{os.path.relpath(f, ROOT)} exists", os.path.exists(f)))
        if os.path.exists(f):
            bodies[f] = open(f, encoding="utf-8").read()
    if len(bodies) == 2:
        a, b = list(bodies.values())
        r.append(("both middleware copies are identical", a == b))
    for f, src in bodies.items():
        tag = os.path.basename(os.path.dirname(f)) or "root"
        r.append((f"[{tag}] injects x-shared-secret", "x-shared-secret" in src))
        r.append((f"[{tag}] has exactly ONE next() exit, so no path can skip "
                  f"the header", src.count("return next(") == 1))
        i_hdr = src.find("x-shared-secret")
        i_next = src.find("return next(")
        r.append((f"[{tag}] sets the header BEFORE returning",
                  i_hdr != -1 and i_next != -1 and i_hdr < i_next))
        r.append((f"[{tag}] challenges with WWW-Authenticate on refusal",
                  "WWW-Authenticate" in src))
        r.append((f"[{tag}] only gates when credentials are configured "
                  f"(never locks the owner out)", "USER && PASS" in src))

    for name, ok in r:
        print(("PASS " if ok else "FAIL ") + name)
    n = sum(1 for _, ok in r if ok)
    print(f"\n{n}/{len(r)} passed")
    if n != len(r):
        print("\nA failing route means the live domain will 404 it. Add the "
              "rewrite to BOTH vercel.json files and redeploy Vercel.")
    return 0 if n == len(r) else 1


if __name__ == "__main__":
    sys.exit(main())
