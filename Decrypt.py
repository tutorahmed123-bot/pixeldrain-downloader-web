#!/usr/bin/env python3
"""
decrypt_dlc.py — Finds .dlc container files sitting next to this script, sends each
to dcrypt.it's public "Upload Container" feature (the same one on their website),
and extracts the plain multi-host link list from the response.

All extracted links (from every .dlc processed) get appended to normal_link.txt,
so pd_download.py's existing pixeldrain-only filter + downloader picks them up
automatically on its next run — no manual copy-pasting needed.

Already-processed .dlc files are moved into a processed/ subfolder afterward (keeping
their original filename), so re-running doesn't hit dcrypt.it again for files you've
already extracted.

Can be run standalone (python decrypt_dlc.py) or imported — pd_download.py
calls process_pending_dlc_files() automatically before its normal conversion step.
"""

import os
import re
import glob
import requests

DCRYPT_UPLOAD_URL = "https://dcrypt.it/decrypt/upload"
TIMEOUT = 30
NORMAL_LINKS_FILENAME = "normal_link.txt"

URL_PATTERN = re.compile(r'https?://[^\s<>"]+')


def extract_links_from_dcrypt_response(html_text):
    """Pull plain URLs out of dcrypt.it's HTML response, dropping their own site
    links (FAQ, homepage, twitter, etc.) and de-duplicating while preserving order."""
    urls = URL_PATTERN.findall(html_text)
    urls = [u for u in urls if "dcrypt.it" not in u]
    seen = set()
    unique = [u for u in urls if not (u in seen or seen.add(u))]
    return unique


def decrypt_one(dlc_path, session):
    with open(dlc_path, "rb") as f:
        files = {"dlcfile": (os.path.basename(dlc_path), f)}
        resp = session.post(DCRYPT_UPLOAD_URL, files=files, timeout=TIMEOUT)
        resp.raise_for_status()
    return extract_links_from_dcrypt_response(resp.text)


def process_pending_dlc_files(folder):
    """Finds *.dlc files (not yet processed) in `folder`, decrypts each via dcrypt.it,
    and appends discovered links to normal_link.txt (with a '# from <file>' header
    per source so you can tell where each batch came from).

    Renames each successfully processed file to *.dlc.processed.
    Returns (processed_count, total_links_added).
    """
    dlc_files = glob.glob(os.path.join(folder, "*.dlc"))
    if not dlc_files:
        return 0, 0

    session = requests.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0 (pd_download.py / decrypt_dlc.py)"})

    normal_path = os.path.join(folder, NORMAL_LINKS_FILENAME)
    all_new_lines = []
    processed_count = 0
    total_links = 0

    for dlc_path in dlc_files:
        name = os.path.basename(dlc_path)
        print(f"Decrypting container: {name}")
        try:
            links = decrypt_one(dlc_path, session)
        except requests.exceptions.RequestException as e:
            print(f"  -> Network error decrypting {name}: {e}")
            continue
        except Exception as e:
            print(f"  -> Unexpected error decrypting {name}: {e}")
            continue

        if not links:
            print(f"  -> No links found in {name} (site may have changed, or container empty).")
            continue

        print(f"  -> Found {len(links)} link(s)")
        all_new_lines.append(f"# from {name}")
        all_new_lines.extend(links)
        processed_count += 1
        total_links += len(links)

        # Move to processed/ so we don't re-upload it next run — keeps original filename intact
        try:
            processed_dir = os.path.join(folder, "processed")
            os.makedirs(processed_dir, exist_ok=True)
            os.rename(dlc_path, os.path.join(processed_dir, name))
        except OSError as e:
            print(f"  -> Warning: couldn't rename {name} after processing: {e}")

    if all_new_lines:
        with open(normal_path, "a", encoding="utf-8") as f:
            f.write("\n" + "\n".join(all_new_lines) + "\n")

    return processed_count, total_links


if __name__ == "__main__":
    script_dir = os.path.dirname(os.path.abspath(__file__))
    count, total = process_pending_dlc_files(script_dir)
    if count:
        print(f"\nProcessed {count} .dlc file(s), added {total} link(s) to '{NORMAL_LINKS_FILENAME}'.")
    else:
        print("No new .dlc files to process.")
