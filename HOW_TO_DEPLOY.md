# How to deploy this on Hugging Face Spaces (free)

## Step 1 — Account
Sign up free at https://huggingface.co/join (skip if you already have one).

## Step 2 — Create the Space
1. Go to https://huggingface.co/new-space
2. Fill in:
   - **Space name**: anything, e.g. `pixeldrain-downloader`
   - **Select the Space SDK**: **Gradio**
   - **Space hardware**: leave the default free option selected
   - **Visibility**: Private if you don't want strangers finding it (no
     login on the app itself). If Private, you'll need to be logged into
     Hugging Face in your phone's browser too when you open the link.
3. Click **Create Space**.

## Step 3 — Upload the files
Files tab → "Add file" → "Upload files". Upload these five (flat, no
subfolders needed):
```
app.py
PL.py
decrypt.py
requirements.txt
README.md
```
Do **not** upload `HOW_TO_DEPLOY.md` or `VPS_DEPLOY.md` — those are just
notes for you, Hugging Face doesn't need them.

Commit the upload.

## Step 4 — Wait for it to build
App tab → watch it go from "Building" to "Running" (a couple of minutes).

## Step 5 — Use it
```
https://huggingface.co/spaces/YOUR_USERNAME/pixeldrain-downloader
```
Open on your phone → upload a `.dlc` → tap **Run** → wait → tap the ↓
next to each finished file to save it to your phone.

## Known gotchas already fixed in the files I gave you
These bit us during setup — they're already handled in the current
`app.py`/`requirements.txt`/`README.md`, just noting them here in case a
future re-upload or edit reintroduces one:

- **`requirements.txt` must NOT list `gradio`.** Spaces installs gradio's
  version itself based on the `sdk_version` line in `README.md`'s metadata
  block — listing a different version in `requirements.txt` causes a
  dependency conflict that fails the build.
- **`README.md`'s `sdk_version` must be a modern 5.x+ release** (currently
  set to `5.49.1`). Older versions (4.44.0 and below) have a bug that
  crashes the app on startup with a `TypeError: argument of type 'bool' is
  not iterable`.
- **If the Space's hardware is set to ZeroGPU** (Hugging Face's free
  shared-GPU tier), it refuses to start unless at least one function in
  `app.py` is decorated with `@spaces.GPU`. This app doesn't need a GPU —
  `app.py` already includes a harmless placeholder function
  (`_zerogpu_placeholder`) just to satisfy that check.

## Free tier limits worth knowing
- Sleeps after inactivity, ~30-60 seconds to wake back up on your next visit.
- Downloaded files aren't permanent — they live in `jobs/` on the Space and
  disappear if it restarts, so download to your phone right after each run.
- Keep the browser tab open until a run finishes, or you'll lose the
  connection back to the results.
- Very large batches may hit a timeout — split a `.dlc` into smaller
  batches if that happens (12-16 episodes at a time has worked fine).

## Updating the code later
Files tab → open the file → pencil icon → edit → commit. Rebuilds
automatically within a minute or two.
