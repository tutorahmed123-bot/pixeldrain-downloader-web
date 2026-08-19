---
title: Pixeldrain Downloader
emoji: ⬇️
colorFrom: gray
colorTo: blue
sdk: gradio
sdk_version: 5.49.1
app_file: app.py
pinned: false
---

# Pixeldrain Downloader

A small web app that takes a `.dlc` container file, decrypts it, and
bulk-downloads every file inside in parallel — with a live progress bar
and tap-to-download links when it's done. Built to be used from a phone
browser, no terminal or install required once it's deployed.

**Live app:** https://huggingface.co/spaces/YOUR_USERNAME/pixeldrain-downloader

## What it does

1. **Decrypt** — the uploaded `.dlc` container is sent to dcrypt.it's
   public "Upload Container" feature, which extracts the plain multi-host
   link list inside it.
2. **Convert** — pixeldrain.com/u/... page links are converted into
   direct CDN download links (non-pixeldrain hosts in the container are
   filtered out).
3. **Download** — every link is downloaded in parallel using a thread
   pool, streamed to disk in chunks (safe for large video files), with
   automatic retries on failure.
4. **Deliver** — once finished, each downloaded file appears in the page
   with its own download link, ready to save to your phone or computer.

## Why a web app instead of just a script?

The core downloading logic (`PL.py` + `decrypt.py`) was originally a
command-line tool — see the companion repo [pixeldrain-downloader-cli] for
that version, meant to run on a machine you own. This repo wraps the same
logic in a [Gradio](https://gradio.app) interface so it can run on a
free-tier Hugging Face Space instead: open a link from any phone or
computer, upload a file, get results back, no Python or terminal needed
on the device you're using.

## Files in this repo

| File | Purpose |
|---|---|
| `app.py` | Gradio UI — upload button, Run button, status/progress display, downloadable file list. Wraps the pipeline below. |
| `PL.py` | Core download engine — link conversion, parallel downloading, retries, progress reporting. |
| `decrypt.py` | Handles `.dlc` container decryption via dcrypt.it. |
| `requirements.txt` | Python dependencies (deliberately does **not** pin `gradio` — see note below). |
| `README.md` | This file — also read directly by Hugging Face Spaces for its title/SDK metadata (the block at the very top). |
| `HOW_TO_DEPLOY.md` | Full step-by-step deployment walkthrough, including the specific issues hit and fixed while getting this running. |

## Deploying your own copy

See [`HOW_TO_DEPLOY.md`](./HOW_TO_DEPLOY.md) for the complete, no-terminal
walkthrough (create a Hugging Face account → new Space → upload these
files → done). It also documents a couple of gotchas worth knowing before
you start:

- `requirements.txt` intentionally doesn't list `gradio` — Spaces installs
  gradio's version based on the `sdk_version` line in this file's metadata
  block above, and listing a conflicting version in `requirements.txt`
  breaks the build.
- If your Space's hardware is set to Hugging Face's free ZeroGPU tier,
  it requires at least one function decorated with `@spaces.GPU` to start
  — even though this app does no GPU work at all. `app.py` already
  includes a harmless placeholder function for exactly this reason.

## Running it locally instead

```
pip install -r requirements.txt gradio
python app.py
```
Opens at `http://localhost:7860`.

## Limitations

- Free-tier Spaces sleep after a period of inactivity and take ~30-60
  seconds to wake back up on the next visit.
- Downloaded files are not stored permanently — they live in a `jobs/`
  folder on the Space for the duration of that run and are lost if the
  Space restarts, so grab your files soon after each run finishes.
- Very large batches in a single `.dlc` may hit a timeout on the free
  tier; splitting into smaller batches (12–16 files at a time has worked
  reliably) avoids this.

[pixeldrain-downloader-cli]: https://github.com/YOUR_USERNAME/pixeldrain-downloader-cli
