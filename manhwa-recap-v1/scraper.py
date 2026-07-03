import os
import re
import ssl
import urllib.request
import html
import shutil

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

    # Heuristics for finding manhwa panel images.
    # 1. Asura Scans specific pattern
    # 2. General scanlation patterns (numbered pages)
    patterns = [
        # Asura Scans CDN restored images
        r'https://cdn\.asurascans\.com/asura-images/chapters-restored/[^\s\"\'\>]+?\.(?:webp|jpg|jpeg|png)',
        r'https://[^\s\"\'\>]+?/chapters-restored/[^\s\"\'\>]+?\.(?:webp|jpg|jpeg|png)',
        # General pattern for sequential images
        r'https://[^\s\"\'\>]+?/\d{3}\.(?:webp|jpg|jpeg|png)',
        r'https://[^\s\"\'\>]+?/page[^\s\"\'\>]*?\.(?:webp|jpg|jpeg|png)'
    ]

    image_urls = []
    for pattern in patterns:
        matches = re.findall(pattern, html_unescaped)
        if matches:
            # Preserve order of first occurrence
            for match in matches:
                if match not in image_urls:
                    image_urls.append(match)
            # If we found matches with specific pattern, stop
            if len(image_urls) > 3:
                break

    # If no specific patterns matched, fall back to any image ending in webp/jpg/png that doesn't look like a layout/avatar/icon/logo
    if not image_urls:
        all_imgs = re.findall(r'https://[^\s\"\'\>]+?\.(?:webp|jpg|jpeg|png)', html_unescaped)
        for img_url in all_imgs:
            url_lower = img_url.lower()
            if any(x in url_lower for x in ["logo", "avatar", "icon", "theme", "plugin", "ad-", "banner", "footer", "header", "widget", "comment"]):
                continue
            if img_url not in image_urls:
                image_urls.append(img_url)

    if not image_urls:
        raise ValueError("Could not find any panel images on the page.")

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
