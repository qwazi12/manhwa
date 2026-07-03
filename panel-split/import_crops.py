import os
import glob
import shutil
import re
import argparse

def natural_key(name: str):
    return [int(t) if t.isdigit() else t.lower()
            for t in re.split(r"(\d+)", name)]

def main():
    ap = argparse.ArgumentParser(description="Rename and import reviewed crops to the recap pipeline")
    ap.add_argument("--from-dir", default="review_crops", help="directory containing reviewed crops")
    ap.add_argument("--to-dir", default="../manhwa-recap-v1/input/images", help="destination input directory")
    args = ap.parse_args()

    src_dir = os.path.abspath(args.from_dir)
    dst_dir = os.path.abspath(args.to_dir)

    if not os.path.exists(src_dir):
        print(f"[-] Source directory '{src_dir}' does not exist. Run split_panels.py first.")
        return

    # Find all PNG/JPG/WEBP crops in source directory (non-recursively)
    exts = ["*.png", "*.jpg", "*.jpeg", "*.webp"]
    files = []
    for ext in exts:
        files.extend(glob.glob(os.path.join(src_dir, ext)))

    # Filter out directories
    files = [f for f in files if os.path.isfile(f)]

    if not files:
        print(f"[-] No crops found in '{src_dir}' to import.")
        return

    # Sort files naturally so they are in reading order
    files.sort(key=natural_key)

    # Clean the destination directory to avoid mixing old/stale images
    if os.path.exists(dst_dir):
        shutil.rmtree(dst_dir)
    os.makedirs(dst_dir, exist_ok=True)

    print(f"[*] Importing {len(files)} crops from '{src_dir}' to '{dst_dir}'...")

    for idx, filepath in enumerate(files, 1):
        ext = os.path.splitext(filepath)[1].lower()
        new_filename = f"{idx:03d}{ext}"
        new_filepath = os.path.join(dst_dir, new_filename)
        # Copy the file
        shutil.copy2(filepath, new_filepath)
        # print(f"    Copying: {os.path.basename(filepath)} -> {new_filename}")

    print(f"[+] Successfully imported and renamed {len(files)} crops to '{dst_dir}'!")

if __name__ == "__main__":
    main()
