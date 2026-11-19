"""A scroll whose first tile has another width must not pad the art black.

Live 2026-11-19: Dark Mage 111 opened on a 1532-wide scan-group page over
899-wide art, Regressor 37 on a 1920-wide one. The scroll took the FIRST
tile's width, so every panel came out that wide with the art on the left and
black on the right (owner sent Regressor 30-38 back). The scroll's width is
now the width most tiles share; a tile of another width is scaled to it.
"""
import os
import sys
import tempfile

import numpy as np
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))


def tile(path, w, h=1280):
    """White tile with two grey art blocks across nearly the full width."""
    a = np.full((h, w), 250, np.uint8)
    a[80:560, 10:w - 10] = 90
    a[700:1200, 10:w - 10] = 120
    Image.fromarray(a).save(path)
    return path


def _pages(first_w=1532, w=899, n=14):
    d = tempfile.mkdtemp(prefix="sw_")
    return [tile(os.path.join(d, "%03d.png" % i), first_w if i == 1 else w) for i in range(1, n + 1)]


def test_strip_width_is_the_common_width():
    import splitlab as SL
    pages = _pages()
    assert SL.is_strip(pages)
    assert SL._strip_width(pages) == 899


def test_odd_tile_is_scaled_not_padded():
    import splitlab as SL
    pages = _pages()
    im = SL._tile(pages[0], 899)
    assert im.width == 899 and im.height == round(1280 * 899 / 1532)
    assert SL._tile(pages[1], 899).size == (899, 1280)


def test_split_into_panels_have_no_black_band():
    import splitlab as SL
    pages = _pages()
    out = tempfile.mkdtemp(prefix="swo_")
    SL.split_into(pages, out, slug="t", vision=False)
    names = sorted(f for f in os.listdir(out) if f.endswith(".png"))
    assert names, "no panels were cut"
    for f in names:
        a = np.array(Image.open(os.path.join(out, f)).convert("L"))
        assert a.shape[1] == 899, f"{f} is {a.shape[1]} wide"
        assert a[:, -5:].mean() > 60, f"{f} has a black right edge"


def test_uniform_scroll_unchanged():
    import splitlab as SL
    pages = _pages(first_w=800, w=800)
    assert SL._strip_width(pages) == 800
    assert SL._tile(pages[0], 800).size == (800, 1280)
