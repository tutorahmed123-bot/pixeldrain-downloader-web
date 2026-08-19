#!/usr/bin/env python3
"""
app.py — Gradio version, for Hugging Face Spaces (free Gradio SDK tier).

Same pipeline as the original PL.py / decrypt.py, wrapped in a Gradio UI:
upload a .dlc file, click Run, get a status log + downloadable file links —
Gradio's default UI is already phone-friendly, no extra template work needed.
"""

import os
import shutil
import uuid

import gradio as gr
import spaces

import PL
import decrypt

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
JOBS_DIR = os.path.join(BASE_DIR, "jobs")
os.makedirs(JOBS_DIR, exist_ok=True)


@spaces.GPU(duration=1)
def _zerogpu_placeholder():
    """This app doesn't use a GPU — downloading files is pure CPU work.
    This function exists only because Hugging Face's free ZeroGPU hardware
    requires at least one @spaces.GPU-decorated function to be present at
    startup. It's never actually called."""
    return None


def run_pipeline(dlc_file, progress=gr.Progress(track_tqdm=False)):
    """dlc_file is a Gradio temp-file path (str) for the uploaded .dlc.
    Returns (status_text, list_of_downloaded_file_paths)."""
    if dlc_file is None:
        return "Please upload a .dlc file first.", None

    job_id = uuid.uuid4().hex[:10]
    job_dir = os.path.join(JOBS_DIR, job_id)
    os.makedirs(job_dir, exist_ok=True)

    dest = os.path.join(job_dir, os.path.basename(dlc_file))
    shutil.copy(dlc_file, dest)

    progress(0.05, desc="Decrypting container...")
    decrypt.process_pending_dlc_files(job_dir)

    normal_path = os.path.join(job_dir, PL.NORMAL_LINKS_FILENAME)
    if not os.path.exists(normal_path):
        return ("No links were extracted from that .dlc file. The container may be "
                 "empty, or dcrypt.it may have changed its response format."), None

    progress(0.15, desc="Converting links...")
    cache_dir = os.path.join(job_dir, PL.CACHE_DIRNAME)
    os.makedirs(cache_dir, exist_ok=True)
    bypassed_path = os.path.join(cache_dir, PL.BYPASSED_LINKS_FILENAME)
    PL.convert_links_file(normal_path, bypassed_path)

    links = PL.read_links(bypassed_path)
    if not links:
        return "No pixeldrain links found after conversion.", None

    progress(0.25, desc=f"Downloading {len(links)} file(s)...")
    out_dir = os.path.join(job_dir, "downloads")
    os.makedirs(out_dir, exist_ok=True)
    session = PL.requests.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0 (pd_download.py gradio)"})

    results = []
    with PL.ThreadPoolExecutor(max_workers=PL.DEFAULT_WORKERS) as executor:
        futures = {
            executor.submit(
                PL.download_one, url, (os.path.join(out_dir, source) if source else out_dir),
                i + 1, len(links), session
            ): url
            for i, (url, source) in enumerate(links)
        }
        done_count = 0
        for future in PL.as_completed(futures):
            url, success, msg = future.result()
            results.append((url, success, msg))
            done_count += 1
            progress(0.25 + 0.7 * (done_count / len(links)),
                     desc=f"Downloaded {done_count}/{len(links)}")

    files = []
    for root, _, filenames in os.walk(out_dir):
        for fn in filenames:
            files.append(os.path.join(root, fn))

    ok = [r for r in results if r[1]]
    failed = [r for r in results if not r[1]]

    status_lines = [f"Done: {len(ok)} succeeded, {len(failed)} failed (of {len(links)})."]
    if failed:
        status_lines.append("\nFailed links:")
        for url, _, msg in failed:
            status_lines.append(f"  - {url} ({msg})")

    return "\n".join(status_lines), (files if files else None)


with gr.Blocks(title="Pixeldrain Downloader") as demo:
    gr.Markdown("# Pixeldrain Downloader\nUpload a `.dlc` file, then tap **Run**.")
    dlc_input = gr.File(label="Upload .dlc file", file_types=[".dlc"], type="filepath")
    run_btn = gr.Button("Run", variant="primary")
    status_output = gr.Textbox(label="Status", lines=6)
    files_output = gr.Files(label="Downloaded files")

    run_btn.click(fn=run_pipeline, inputs=dlc_input, outputs=[status_output, files_output])

if __name__ == "__main__":
    demo.launch()
