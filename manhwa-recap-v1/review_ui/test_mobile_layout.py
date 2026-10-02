"""The phone layout must actually apply. It once sat ahead of the base rules
it overrode, so phones silently got the desktop layout. These checks pin the
order and the pieces a phone needs. No network."""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
R = []


def check(name, ok):
    R.append((name, bool(ok)))


def main():
    sb = open(os.path.join(HERE, "storyboard.py"), encoding="utf-8").read()
    rp = open(os.path.join(HERE, "review_page.py"), encoding="utf-8").read()

    phone = sb.index("PHONE\n   Last in the sheet")
    end = sb.index("</style>", phone)
    for base in (".drawer {{ position:fixed", "th, td {{ border:", "#rail {{ position:fixed",
                 "td.img {{ width: 185px"):
        check("board phone rules come after the base rule %r" % base[:18],
              base in sb and sb.index(base) < phone)
    block = sb[phone:end]
    for need in ("#rail {{", "#approveBtn {{", ".drawer, .drawer.wide {{",
                 "grid-template-areas", "font-size:16px"):
        check("board phone block sets %s" % need.strip("{ "), need in block)

    check("review page has a viewport meta", 'name="viewport"' in rp)
    rphone = rp.index("/* PHONE")
    check("review phone rules come after the base .nav rule",
          rp.index(".nav { position:fixed") < rphone < rp.index("</style>__HEADJS__"))

    for page, src in (("board", sb), ("review", rp)):
        check(page + " links the web app manifest", 'rel="manifest"' in src)
        check(page + " has an apple touch icon", 'rel="apple-touch-icon"' in src)

    import server
    from fastapi.testclient import TestClient
    c = TestClient(server.app)
    m = c.get("/manifest.webmanifest")
    check("manifest is served", m.status_code == 200 and m.json()["display"] == "standalone")
    for size in (180, 192, 512):
        r = c.get("/app-icon/%d.png" % size)
        check("icon %d is a PNG" % size, r.status_code == 200 and r.content[:4] == b"\x89PNG")
    check("unknown icon size is 404", c.get("/app-icon/77.png").status_code == 404)

    for n, ok in R:
        print(("PASS " if ok else "FAIL ") + n)
    n_ok = sum(1 for _, ok in R if ok)
    print("\n%d/%d passed" % (n_ok, len(R)))
    return 0 if n_ok == len(R) else 1


if __name__ == "__main__":
    sys.exit(main())
