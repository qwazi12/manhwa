import os
import re
import ssl
import urllib.request
import html
import shutil


def find_page_urls(html_unescaped):
    r"""Every chapter-page image URL on the page, in reading order.

    Split out of download_chapter so the extraction — the part that silently
    truncated Doctors Rebirth ch1 — is unit-testable without the network.

    HISTORY (Session 26): the old code tried a list of SPECIFIC url patterns
    and stopped at the first that hit. One required a bare three-digit
    filename (/\d{3}\.webp), so on a chapter whose scanlator had split pages
    into parts — 001.webp, 002_p1.webp, 002_p2.webp, 003_p1.webp … — it
    matched only 001, 005 and 006. The generic fallback that would have found
    the rest was gated on `if not image_urls`, so a PARTIAL match suppressed
    it. Result: 3 of 11 pages, no error, and a 66-second "chapter".

    The reliable signal is not the filename shape but that every page of a
    chapter is served from the SAME directory, and there are more of them
    than of any other image on the page. So: take every image in document
    order, drop obvious furniture, keep the directory holding the most.
    Filename conventions can then change without truncating a chapter.
    """
    junk = ("logo", "avatar", "icon", "theme", "plugin", "ad-", "banner",
            "footer", "header", "widget", "comment", "/covers/", "cover.",
            "thumbnail", "sprite", "placeholder")
    generic = r'https://[^\s"\'>]+?\.(?:webp|jpg|jpeg|png)'

    ordered = {}                       # url -> first offset in the document
    for m in re.finditer(generic, html_unescaped):
        u = m.group(0)
        if any(j in u.lower() for j in junk):
            continue
        ordered.setdefault(u, m.start())

    image_urls = []
    if ordered:
        by_dir = {}
        for u, pos in ordered.items():
            by_dir.setdefault(u.rsplit("/", 1)[0], []).append((u, pos))
        best = max(by_dir.values(), key=len)        # the chapter directory
        image_urls = [u for u, _ in sorted(best, key=lambda t: t[1])]

    # Last resort: the old explicit patterns, in case a site serves its pages
    # from several directories and the majority rule picks the wrong one.
    if len(image_urls) < 2:
        for pattern in (
            r'https://cdn\.asurascans\.com/asura-images/chapters-restored/[^\s"\'>]+?\.(?:webp|jpg|jpeg|png)',
            r'https://[^\s"\'>]+?/chapters-restored/[^\s"\'>]+?\.(?:webp|jpg|jpeg|png)',
            r'https://[^\s"\'>]+?/\d{3}[^/\s"\'>]*\.(?:webp|jpg|jpeg|png)',
            r'https://[^\s"\'>]+?/page[^\s"\'>]*?\.(?:webp|jpg|jpeg|png)',
        ):
            for match in re.findall(pattern, html_unescaped):
                if match not in image_urls:
                    image_urls.append(match)

    return image_urls


def _sequence_warning(image_urls):
    """Human-readable warning if the page numbers have gaps, else ''.

    Filenames look like 001.webp or 002_p1.webp; a missing number means the
    URL extraction dropped a page, which is exactly the failure this scraper
    used to hide.
    """
    nums = []
    for u in image_urls:
        m = re.search(r"/(\d{2,4})(?:[_-]p?\d+)?\.[A-Za-z]+$", u.split("?")[0])
        if m:
            nums.append(int(m.group(1)))
    if not nums:
        return ""
    missing = [n for n in range(min(nums), max(nums) + 1) if n not in nums]
    if missing:
        return (f"page numbering has GAPS — got {len(set(nums))} distinct page "
                f"numbers spanning {min(nums)}-{max(nums)}, missing {missing}. "
                f"The chapter is probably incomplete.")
    return ""


def download_chapter(url: str, output_dir: str):
    """
    Fetches the chapter HTML from the URL, extracts all panel image links in reading
    order, cleans the output directory, downloads the images sequentially, and names
    them 001.webp, 002.webp, etc.
    """
    # Make sure output_dir exists and is clean
    if os.path.exists(output_dir):
        shutil.rmtree(output_dir)
    os.makedirs(output_dir, exist_ok=True)

    print(f"[*] Fetching chapter page: {url}")
    ctx = ssl._create_unverified_context()
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}
    )
    
    with urllib.request.urlopen(req, context=ctx) as response:
        html_content = response.read().decode('utf-8')

    # Unescape HTML entities (e.g. &quot; to ")
    html_unescaped = html.unescape(html_content)

    image_urls = find_page_urls(html_unescaped)

    print(f"[*] Found {len(image_urls)} unique panel images.")

    # Download each image sequentially
    downloaded_paths = []
    for idx, img_url in enumerate(image_urls, 1):
        ext = os.path.splitext(img_url.split('?')[0])[1] or '.webp'
        filename = f"{idx:03d}{ext}"
        filepath = os.path.join(output_dir, filename)

        print(f"    [{idx}/{len(image_urls)}] Downloading: {img_url}")
        try:
            img_req = urllib.request.Request(img_url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(img_req, context=ctx) as img_resp, open(filepath, "wb") as out_file:
                out_file.write(img_resp.read())
            downloaded_paths.append(filepath)
        except Exception as e:
            print(f"    [WARN] Failed to download {img_url}: {e}")

    if not downloaded_paths:
        raise RuntimeError("No images were successfully downloaded.")

    print(f"[*] Successfully downloaded {len(downloaded_paths)} images to {output_dir}")
    return downloaded_paths
