#!/usr/bin/env python3
"""
pd_download.py — Bulk parallel downloader for Pixeldrain (and similar) direct links.

Usage:
    python pd_download.py
        0. If any .dlc container files sit next to this script, decrypts each via
           dcrypt.it and appends their links to 'normal_link.txt' automatically
           (requires decrypt_dlc.py in the same folder). Processed .dlc files are
           renamed to *.dlc.processed so they aren't re-uploaded next time.
        1. Looks for 'normal_link.txt' next to this script (plain pixeldrain.com/u/... links,
           one per line, or mixed multi-host output — non-pixeldrain hosts get filtered out).
           If found, converts them into 'cache/bypassed_link.txt' (direct CDN links, tucked
           away since you never need to look at it).
        2. If 'normal_link.txt' isn't found, falls back to 'cache/bypassed_link.txt' directly
           (useful if you already have CDN links, or edited that file by hand).
        3. Downloads everything in bypassed_link.txt.

    python pd_download.py my_other_links.txt -o downloads -w 5
        (bypasses the .dlc step and conversion step entirely — treats the given file as
        a ready link list, same as before)

- Reads one URL per line from a text file (blank lines / lines starting with # are ignored).
- Downloads files in parallel using a thread pool.
- Streams to disk in chunks (safe for large files, low memory use).
- Auto-resumes partially downloaded files if the server supports HTTP Range.
- Shows a live progress bar per file.
- Retries failed downloads a few times before giving up.
- Prints a summary at the end (done / failed).
"""

import argparse
import os
import re
import sys
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlparse, unquote

import requests
from tqdm import tqdm

try:
    from decrypt_dlc import process_pending_dlc_files
except ImportError:
    try:
        from decrypt import process_pending_dlc_files
    except ImportError:
        process_pending_dlc_files = None  # neither module present — .dlc step just gets skipped

DEFAULT_WORKERS = 4          # 3-5 range, safe default
CHUNK_SIZE = 1024 * 1024     # 1 MB per chunk
MAX_RETRIES = 3
RETRY_BACKOFF = 3            # seconds, multiplied by attempt number
TIMEOUT = 30                 # seconds for connect/read

NORMAL_LINKS_FILENAME = "normal_link.txt"      # plain pixeldrain.com/u/... links go here — your main file
BYPASSED_LINKS_FILENAME = "bypassed_link.txt"  # converted direct CDN links — internal, tucked into cache/
CACHE_DIRNAME = "cache"

PIXELDRAIN_PAGE_PATTERN = re.compile(r'pixeldrain\.com/([ul])/([a-zA-Z0-9_-]+)')
CDN_LINK_PATTERN = re.compile(r'^https?://cdn\.pixeldrain\.[a-z.]+/', re.IGNORECASE)
GENERIC_URL_PATTERN = re.compile(r'^https?://', re.IGNORECASE)
SOURCE_HEADER_PATTERN = re.compile(r'^#\s*from\s+(.+)$', re.IGNORECASE)
UNSAFE_FOLDER_CHARS = re.compile(r'[<>:"/\\|?*]')


def sanitize_folder_name(name):
    """Turn a '# from <name>' source tag into a safe Windows/POSIX folder name."""
    name = re.sub(r'\.dlc(\.processed)?$', '', name.strip(), flags=re.IGNORECASE)
    name = UNSAFE_FOLDER_CHARS.sub('_', name)
    name = name.strip(' .')  # Windows disallows trailing dots/spaces
    return name or "downloads"

print_lock = threading.Lock()
stop_event = threading.Event()


def start_stop_listener():
    """Background thread: watches for 'q' + Enter (or bare 'q' on Windows) to trigger
    a graceful stop. Sets stop_event, which download_one() checks between chunks so
    files close cleanly and stay resumable instead of getting cut mid-write."""

    def listen():
        try:
            import msvcrt  # Windows: single keypress, no Enter needed
            while not stop_event.is_set():
                if msvcrt.kbhit():
                    ch = msvcrt.getch()
                    try:
                        ch = ch.decode(errors="ignore")
                    except Exception:
                        ch = ""
                    if ch.lower() == "q":
                        stop_event.set()
                        with print_lock:
                            tqdm.write("\n[!] Stop requested — finishing current chunks and "
                                       "closing files safely (re-run later to resume)...")
                        break
                time.sleep(0.1)
        except ImportError:
            # Non-Windows fallback: line-based, needs Enter after 'q'
            while not stop_event.is_set():
                try:
                    line = sys.stdin.readline()
                except Exception:
                    break
                if line.strip().lower() == "q":
                    stop_event.set()
                    with print_lock:
                        tqdm.write("\n[!] Stop requested — finishing current chunks and "
                                   "closing files safely (re-run later to resume)...")
                    break

    t = threading.Thread(target=listen, daemon=True)
    t.start()
    return t


def convert_links_file(input_path, output_path):
    """Convert plain pixeldrain.com/u/... links into direct CDN links.

    Handles mixed multi-host input too (e.g. output from a DLC/container decrypt
    site listing pixeldrain alongside gofile, filekeeper, send.now, etc.) —
    only pixeldrain links are kept; other hosts are filtered out silently since
    that's expected input, not an error. Lines that are already direct CDN links
    pass through unchanged. Lines that aren't URLs at all are flagged as
    unrecognized so you can spot genuine typos/garbage.

    '# from <file>.dlc' source-tag lines (written by decrypt.py) are preserved
    in the output so downloads can later be grouped into a per-season folder.
    """
    converted = []       # actual link count, for reporting
    output_lines = []    # what actually gets written (links + preserved source tags)
    skipped = []
    other_host_count = 0

    with open(input_path, "r", encoding="utf-8") as f:
        raw_lines = f.readlines()

    for line in raw_lines:
        stripped = line.strip()
        if not stripped:
            continue

        if SOURCE_HEADER_PATTERN.match(stripped):
            output_lines.append(stripped)  # preserve source tag so downloads can be grouped by season
            continue

        if stripped.startswith("#"):
            continue

        match = PIXELDRAIN_PAGE_PATTERN.search(stripped)
        if match:
            link_type, file_id = match.groups()
            cdn_link = f"https://cdn.pixeldrain.eu.cc/{file_id}"
            converted.append(cdn_link)
            output_lines.append(cdn_link)
            if link_type == "l":
                with print_lock:
                    print(f"  Note: '{stripped}' is an album (/l/) link — "
                          f"converted CDN link may not work, verify it downloads correctly.")
            continue

        if CDN_LINK_PATTERN.match(stripped):
            converted.append(stripped)
            output_lines.append(stripped)  # already a direct link, pass through as-is
            continue

        if GENERIC_URL_PATTERN.match(stripped):
            other_host_count += 1  # some other file host — expected in mixed input, not an error
            continue

        skipped.append(stripped)

    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(output_lines) + ("\n" if output_lines else ""))

    return converted, skipped, other_host_count


def read_links(path):
    """Reads links from the file, tracking any '# from <file>.dlc' source tags
    along the way. Returns a list of (url, folder_or_None) tuples — folder is a
    sanitized per-season subfolder name if a source tag preceded the link,
    otherwise None (goes straight into the base output folder)."""
    links = []
    current_source = None
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            header_match = SOURCE_HEADER_PATTERN.match(line)
            if header_match:
                current_source = sanitize_folder_name(header_match.group(1))
                continue
            if line.startswith("#"):
                continue
            links.append((line, current_source))
    return links


def guess_filename(url, resp=None):
    """Try Content-Disposition first, then fall back to the URL path."""
    if resp is not None:
        cd = resp.headers.get("Content-Disposition", "")
        match = re.search(r'filename\*?=(?:UTF-8\'\')?"?([^\";]+)"?', cd)
        if match:
            return unquote(match.group(1))
    name = os.path.basename(urlparse(url).path)
    return unquote(name) if name else "downloaded_file"


def human(n):
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if n < 1024:
            return f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}PB"


def download_one(url, out_dir, index, total, session):
    """Download a single URL with resume + retry. Returns (url, success, message).

    Each concurrent download gets its own fixed terminal row (position=index-1)
    via tqdm, so parallel progress bars don't overwrite each other like plain
    \\r printing does.
    """
    if stop_event.is_set():
        return url, False, "stopped (not started)"

    os.makedirs(out_dir, exist_ok=True)

    for attempt in range(1, MAX_RETRIES + 1):
        bar = None
        try:
            # HEAD (fallback to GET) to get filename + size before committing to a name
            head = session.get(url, stream=True, timeout=TIMEOUT)
            head.raise_for_status()
            filename = guess_filename(url, head)
            filepath = os.path.join(out_dir, filename)
            total_size = int(head.headers.get("Content-Length", 0))

            existing = os.path.getsize(filepath) if os.path.exists(filepath) else 0

            # Already fully downloaded
            if total_size and existing == total_size:
                head.close()
                with print_lock:
                    tqdm.write(f"[{index}/{total}] SKIP (already complete): {filename}")
                return url, True, "already complete"

            headers = {}
            mode = "wb"
            if existing and head.headers.get("Accept-Ranges") == "bytes":
                headers["Range"] = f"bytes={existing}-"
                mode = "ab"
            else:
                existing = 0  # server doesn't support resume; restart

            head.close()

            if stop_event.is_set():
                return url, False, "stopped (not started)"

            resp = session.get(url, stream=True, headers=headers, timeout=TIMEOUT)
            resp.raise_for_status()

            short_name = filename if len(filename) <= 30 else filename[:27] + "..."
            bar = tqdm(
                total=total_size or None,
                initial=existing,
                unit="B",
                unit_scale=True,
                unit_divisor=1024,
                desc=f"[{index}/{total}] {short_name}",
                position=index - 1,
                leave=True,
                dynamic_ncols=True,
            )

            downloaded = existing
            stopped_mid_download = False
            with open(filepath, mode) as f:
                for chunk in resp.iter_content(chunk_size=CHUNK_SIZE):
                    if not chunk:
                        continue
                    f.write(chunk)
                    downloaded += len(chunk)
                    bar.update(len(chunk))
                    if stop_event.is_set():
                        stopped_mid_download = True
                        break

            bar.close()
            resp.close()

            if stopped_mid_download:
                with print_lock:
                    tqdm.write(f"[{index}/{total}] STOPPED (partial, resumable): {filename} "
                               f"({human(downloaded)}{'/' + human(total_size) if total_size else ''})")
                return url, False, "stopped (partial - resumable)"

            return url, True, "ok"

        except (requests.RequestException, IOError) as e:
            if bar is not None:
                bar.close()
            if stop_event.is_set():
                return url, False, "stopped (partial - resumable)"
            with print_lock:
                tqdm.write(f"[{index}/{total}] Attempt {attempt}/{MAX_RETRIES} failed for {url}: {e}")
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_BACKOFF * attempt)
            else:
                return url, False, str(e)


DEFAULT_LINKS_FILENAME = BYPASSED_LINKS_FILENAME


def main():
    parser = argparse.ArgumentParser(description="Bulk parallel downloader for Pixeldrain/direct links")
    parser.add_argument("links_file", nargs="?", default=None,
                         help="Text file with one URL per line. If omitted, auto-converts "
                              f"'{NORMAL_LINKS_FILENAME}' into '{BYPASSED_LINKS_FILENAME}' "
                              "(or uses an existing bypassed_link.txt) next to this script.")
    parser.add_argument("-o", "--output", default="downloads", help="Output directory (default: downloads)")
    parser.add_argument("-w", "--workers", type=int, default=DEFAULT_WORKERS,
                         help=f"Parallel downloads (default: {DEFAULT_WORKERS}, recommended 3-5)")
    args = parser.parse_args()

    script_dir = os.path.dirname(os.path.abspath(__file__))

    if args.links_file is None:
        normal_path = os.path.join(script_dir, NORMAL_LINKS_FILENAME)
        cache_dir = os.path.join(script_dir, CACHE_DIRNAME)
        os.makedirs(cache_dir, exist_ok=True)
        bypassed_path = os.path.join(cache_dir, BYPASSED_LINKS_FILENAME)

        if process_pending_dlc_files is not None:
            dlc_count, dlc_links = process_pending_dlc_files(script_dir)
            if dlc_count:
                print(f"Decrypted {dlc_count} .dlc file(s), added {dlc_links} link(s) "
                      f"to '{NORMAL_LINKS_FILENAME}'.\n")

        if os.path.exists(normal_path):
            converted, skipped, other_host_count = convert_links_file(normal_path, bypassed_path)
            print(f"Found {len(converted)} pixeldrain link(s) in '{NORMAL_LINKS_FILENAME}'"
                  + (f" ({other_host_count} other-host link(s) filtered out)" if other_host_count else ""))
            if skipped:
                print(f"Skipped {len(skipped)} unrecognized line(s):")
                for s in skipped:
                    print(f"  - {s}")
        elif not os.path.exists(bypassed_path):
            print(f"'{NORMAL_LINKS_FILENAME}' not found next to the script.")
            print(f"Create it with your pixeldrain.com/u/... links, "
                  f"or pass a file directly: python {os.path.basename(__file__)} path\\to\\your_links.txt")
            sys.exit(1)

        args.links_file = bypassed_path

    if not os.path.exists(args.links_file):
        print(f"Links file not found: {args.links_file}")
        sys.exit(1)

    links = read_links(args.links_file)
    if not links:
        print("No links found in the file.")
        sys.exit(1)

    os.makedirs(args.output, exist_ok=True)
    sources_in_use = sorted({src for _, src in links if src})
    if sources_in_use:
        print(f"Found {len(links)} link(s) across {len(sources_in_use)} season folder(s): "
              f"{', '.join(sources_in_use)}")
    else:
        print(f"Found {len(links)} link(s). Downloading into '{args.output}/'...")
    print(f"Using {args.workers} parallel workers.")
    print("(Note: total speed is shared across workers — your internet bandwidth is the ceiling, "
          "not the worker count. Each bar below updates independently.)")
    stop_key_hint = "Press 'q' to stop safely" if "msvcrt" in sys.modules or os.name == "nt" \
        else "Type 'q' and press Enter to stop safely"
    print(f"{stop_key_hint} — in-progress files stay resumable, just re-run afterward.\n")

    start_stop_listener()

    session = requests.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0 (pd_download.py)"})

    def out_dir_for(source):
        return os.path.join(args.output, source) if source else args.output

    results = []  # (url, success, message, source)
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {
            executor.submit(download_one, url, out_dir_for(source), i + 1, len(links), session): (url, source)
            for i, (url, source) in enumerate(links)
        }
        for future in as_completed(futures):
            url, success, msg = future.result()
            results.append((url, success, msg))
            if stop_event.is_set():
                for f in futures:
                    if not f.done():
                        f.cancel()

    ok = [r for r in results if r[1]]
    stopped = [r for r in results if not r[1] and r[2].startswith("stopped")]
    failed = [r for r in results if not r[1] and not r[2].startswith("stopped")]

    print("\n" * (args.workers + 1))
    print("=" * 50)
    if stop_event.is_set():
        print(f"Stopped by user. {len(ok)} succeeded, {len(stopped)} paused (resumable), "
              f"{len(failed)} failed (of {len(links)})")
    else:
        print(f"Done: {len(ok)} succeeded, {len(failed)} failed (of {len(links)})")

    if failed:
        print("\nFailed links:")
        for url, _, msg in failed:
            print(f"  - {url}  ({msg})")

    retry_links = [url for url, _, _ in failed] + [url for url, _, _ in stopped]
    if retry_links:
        retry_path = os.path.join(args.output, "failed_links.txt")
        with open(retry_path, "w") as f:
            for url in retry_links:
                f.write(url + "\n")
        note = "unfinished" if stopped and not failed else "failed/unfinished"
        print(f"\nSaved {note} links to: {retry_path}  (re-run with this file to resume/retry just those — "
              f"note: season folder grouping isn't preserved for manual retries, files land in "
              f"'{args.output}/' directly)")


if __name__ == "__main__":
    main()
