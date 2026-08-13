# ninjaclips

Personal-use ninja warrior content vault. Downloads source videos (YouTube) and produces clips for cross-platform posting.

## Vision

Guiding wedge: paste or download a WNL/ninja competition livestream, choose an
athlete, and get their full run plus obstacle-level clips and vertical
short-form highlights.

Build this as a personal dogfooding product first. The project should stay
CLI-first while workflows are still being discovered: commands collect
structured data and generate deterministic artifacts.

**Local-only is a hard requirement.** Family members compete in this sport, so
footage is processed on this machine and never sent to a third-party service —
even though the YouTube sources are themselves public. Every detector in the
roadmap runs locally: PySceneDetect, YOLO11-pose via `device="mps"`, librosa +
scipy audio template matching, and rife-ncnn-vulkan for frame interpolation.
The one place a hosted vision model would be convenient is bootstrapping the
labeled eval set; do that by hand instead.

The consequence: judgment has to live in code, config, and the eval harness
rather than in an agent's review loop. An agent may help *build* the pipeline,
but must not be a runtime dependency of it.

## Stage 1 — download (current)

CLI: `ninjaclips download` (Typer entry point).

```bash
# Args
ninjaclips download <url1> <url2> ...

# From a file (one URL per line, '#' comments allowed)
ninjaclips download --file urls.txt

# From stdin
cat urls.txt | ninjaclips download

# Useful flags
--output-dir / -o    # default ./downloads
--max-height N       # default 1080
--no-subs            # skip subtitles
--no-info-json       # skip metadata sidecar
--dry-run            # resolve metadata without downloading
--json               # one JSON record per video on stdout (human msgs to stderr)
```

Files land in `./downloads/` named `{uploader} - {title} [{id}].mp4` with sidecar `.vtt` (subtitles) and `.info.json`. `downloaded.txt` is the yt-dlp archive — re-running skips already-downloaded videos.

Format selection is deliberately codec-agnostic. Pinning `[ext=mp4]`/`[ext=m4a]`
restricts YouTube to AVC+AAC and hides the VP9/AV1+opus renditions, which are
smaller at equal quality and are the only source above 1080p. Sorting puts
`fps` ahead of `vcodec` so a 60fps rendition beats a better-codec 30fps one —
60fps source halves the interpolation factor needed for a given slow-motion
ramp.

Raise `--max-height` when the vertical export matters: a 9:16 crop of 1080p is
only 607px wide, versus 810px from 1440p.

## Stage 2 — rough cut (current)

CLI: `ninjaclips clip` — cuts rough `.mp4`s for a named athlete by looking up
appearance timestamps in the [WNL-Athlete-Video-Index](https://github.com/) SQLite DB
and slicing the matching source video(s) from `downloads/` with ffmpeg.

Before clipping a fresh competition, check whether the WNL index has the video
and athlete timestamp. Treat this as a soft requirement: if it is not ready,
manual start/end review is required before generating obstacle clips.

```bash
ninjaclips index-status --athlete "Esme Newton-Pawlus" --video M_ZNRp-UsdU
ninjaclips index-status --athlete "Esme Newton-Pawlus" --video M_ZNRp-UsdU --json
```

```bash
# Cut every appearance of an athlete
ninjaclips clip --athlete "Drew Drechsel"

# Limit to one source video
ninjaclips clip --athlete "drechsel" --video 9C8L1tQaYgs

# Manual mode — no WNL lookup. --at is the `t=` value from a YouTube URL.
ninjaclips clip --at 4243 --video AIYxvfxnbz8 --athlete "Esme Newton-Pawlus" --duration 180
ninjaclips clip --at 4243 --end 4285 --video AIYxvfxnbz8 --label "esme-t2"

# Useful flags
--at N            # manual mode: source-absolute start timestamp (requires --video)
--end N           # manual mode: source-absolute end; overrides --duration
--label NAME      # manual mode: clip name when no --athlete given
--pre-pad N       # seconds before the timestamp (default 5)
--duration N      # max clip length in seconds (default 90, capped at next appearance)
--fast-proxy      # stream-copy preview: fast but keyframe-snapped and VFR (blocks prune)
--force           # overwrite existing clip files (default: skip)
--dry-run         # resolve everything, skip ffmpeg
--json            # JSON array on stdout (one record per appearance)
--db-path PATH    # WNL SQLite DB path (default $WNL_DB_PATH)
```

Clips land in `./clips/` named `{athlete-slug} - {title-fragment} [{youtube_id}] [{start}].mp4`.

### Timestamps are source-absolute

Every timestamp the pipeline records is measured **from the start of the
original downloaded video**, never from the start of an intermediate clip. This
is what makes rough cuts composable — a segment offset stays meaningful no
matter which artifact it is measured against.

Rough cuts are therefore frame-accurate (`-ss` before `-i` for fast input seek,
combined with a re-encode) and forced to constant frame rate. Stream-copy is
available via `--fast-proxy` but snaps to the nearest keyframe, which on
YouTube encodes drifts by seconds; that error would otherwise be inherited by
every obstacle clip cut downstream. Proxy clips are recorded as such and are
refused by `prune`.

Each rough cut writes a JSON ledger sidecar next to itself (`clip.mp4` →
`clip.json`) recording the source file, source-absolute start, duration, probe
metadata, and the exact ffmpeg command.

## Season archive sprint (active priority)

WNL deletes the previous season's videos when a new season starts, and the new
season begins soon. Until every wanted run is captured, **archiving beats
polish** — a source that disappears is unrecoverable, and the saved rough cut
becomes the only copy that will ever exist.

Automatic boundary detection is not reliable yet and is explicitly deferred.
The operator supplies the cut points, in a batch file:

```csv
# jobs.csv — url, start, end, athlete. Times accept 4243 or 1:10:43.
url,start,end,athlete
https://www.youtube.com/live/AIYxvfxnbz8?t=4243,1:10:43,1:14:55,Esme Newton-Pawlus
```

```bash
# 1. Download and cut every row, unattended. Every row is validated up front,
#    so a typo in the last line fails before the first download starts.
ninjaclips batch jobs.csv                 # full downloads, prune afterwards
ninjaclips batch jobs.csv --sections      # fetch only each run's window

# 2. Operator watches each clip and verifies it.
ninjaclips confirm "clips/<clip>.mp4" --every-seconds 7 --columns 8 --rows 5

# 3. Only after verification:
ninjaclips confirm "clips/<clip>.mp4" --yes
ninjaclips prune --yes
```

`batch` defaults to `--pre-pad 30 --post-pad 30` and `--max-height 1440`. A
single run can still be done by hand with `download` + `clip --at/--end`.

### `--sections`: fetch only the window

`ninjaclips download --section 1:09:00-1:16:00` (or `batch --sections`) uses
yt-dlp's ranged download to fetch just that span — roughly 40MB rather than
5GB for a two-minute window. It is dramatically faster and removes the need to
prune at all.

The trade-off is permanent: **a sectioned window can never be widened later**,
because the rest of the video is gone once the season rolls over. `--sections`
therefore fetches an extra `--section-margin` (default 120s) either side of the
padded window. Use full downloads whenever the boundaries are uncertain.

A partial file's `t=0` is not the source's `t=0`, so the download writes a
`.section.json` sidecar recording the offset. `clip` reads it and seeks
`start - section_start` while still recording the **source-absolute** time in
the ledger, keeping timestamps comparable across full and partial downloads.
Requesting a time outside a partial file's window is a loud error, never a
silently wrong cut. When both a full and a partial copy exist, the full one
wins.

**Pad generously.** The source is deleted after confirmation, so the rough cut
is the only artifact later stages can be cut from. Head and tail padding is
cheap optionality; a short cut is a permanent loss. Runs on a challenge course
can exceed four minutes including resets and repeat attempts — do not assume a
run is under a minute.

**Never confirm on the operator's behalf.** Several competitors wear identical
team shirts, so contact sheets are not sufficient to identify a specific
athlete. Only the operator can verify a cut.

## Stage 2c — confirm and prune (current)

Sources are multi-gigabyte and disposable; the rough cut is the durable
artifact. `prune` reclaims the space, but only for sources whose every derived
clip has been confirmed by a human.

```bash
# Build a contact sheet and inspect. Without --yes this only reviews.
ninjaclips confirm "clips/esme... [004240].mp4" --every-seconds 2 --columns 6 --rows 5

# Mark it correct, unlocking its source for pruning
ninjaclips confirm "clips/esme... [004240].mp4" --yes

# Report what is eligible (dry-run by default — nothing is deleted)
ninjaclips prune

# Actually delete
ninjaclips prune --yes
```

`prune` refuses to delete a source when any derived clip is unconfirmed, is
missing from disk, or was cut with `--fast-proxy`. Sources with no derived
clips are reported but never deletable. `.info.json` / `.vtt` sidecars are kept
so provenance survives the media (`--delete-metadata` opts out).

## Stage 2b — obstacle candidates + short-form exports (current)

CLI tools capture the manual review workflow for turning one rough run clip into
short obstacle candidates and vertical variants.

```bash
# Create a heuristic segment manifest from a rough run clip
ninjaclips segment "clips/esme-run.mp4" --output manifests/esme.json

# Tune the manifest JSON if needed, then cut every segment
ninjaclips cut-manifest manifests/esme.json --output-dir obstacle-clips

# Create a contact-strip review sheet for a clip
ninjaclips review-sheet obstacle-clips/esme-01-obstacle-01.mp4

# Create contact-strip review sheets for every segment in a manifest
ninjaclips review-manifest manifests/esme.json

# Export a 9:16 center-crop version for short-form video
ninjaclips vertical obstacle-clips/esme-06-finish.mp4 --duration 15
```

Useful flags:

```bash
segment:
  --clip-duration N  # default 12 seconds
  --count N          # default 6 segments
  --start-offset N   # default 4 seconds into the rough clip
  --gap N            # default 1 second between segment windows
  --json             # emit manifest JSON to stdout

cut-manifest:
  --dry-run          # resolve output paths without ffmpeg
  --force            # overwrite existing segment clips
  --json             # emit result records

review-sheet:
  --every-seconds N  # frame sampling cadence, default 3
  --columns N        # frames per row, default 5
  --rows N           # rows in the grid, default 1
  --json

review-manifest:
  --output-dir DIR   # default ./review-sheets
  --every-seconds N  # frame sampling cadence, default 3
  --columns N        # frames per row, default 5
  --rows N           # rows in the grid, default 1
  --dry-run          # resolve output paths without ffmpeg
  --force            # overwrite existing sheets
  --json

vertical:
  --start N          # start inside the input clip
  --duration N       # output duration, e.g. 15 or 30
  --crop center      # current crop strategy
  --json
```

This stage is deliberately manifest-driven: use `segment` to get a repeatable
first pass, inspect review sheets, then adjust segment boundaries in JSON before
running `cut-manifest` again.

### Dependency: WNL-Athlete-Video-Index

Stage 2 reads athlete→timestamp data from WNL's SQLite DB. Resolution order:
`--db-path` arg → `$WNL_DB_PATH` env var → `~/projects/WNL-Athlete-Video-Index/data/wnl_athlete_video_index.db`.
Missing DB exits with code 2.

## Roadmap

The `segment` command is fixed-window arithmetic, not detection — it has no
signal input at all. Replacing it is the core of the remaining work, in this
order (each step is measurable only once the eval harness exists):

1. **Eval harness** — hand-label obstacle-clear timestamps, flight intervals,
   and chime times for 3–5 videos; score precision/recall at ±0.5s tolerance.
   Without this, "the cuts are inconsistent" is unfalsifiable.
2. **Audio template matching** — cross-correlate a stored per-league chime
   template against a log-mel spectrogram. Best precision-per-effort win.
3. **Pose layer** — YOLO11-pose (`device="mps"`) per shot, normalized by
   bounding-box height; derives `platform_contact` and flight `[release, catch]`
   intervals. Unlocks both better boundaries and slow-motion.
4. **Shot layer** — PySceneDetect `AdaptiveDetector` with a stats file, used
   only to avoid placing boundaries mid-transition. Never a boundary source on
   its own: camera cuts are not obstacle clears.
5. **Fusion → `timeline.json`** — merge signals with corroboration rules and
   `{timestamp, type, confidence, source}` provenance; becomes the single
   source of truth from which rendering is a pure function.
6. **Slow-motion** — eased speed ramps over pose-detected flight spans only,
   interpolated with rife-ncnn-vulkan.

Also outstanding: tracked (pose-driven) crop for vertical export, and
caption/metadata generation for posting.

## Conventions

- CLI-by-default; every command supports `--json`
- Video assets live in `./downloads/` (gitignored — large binary files)
- Generated clips and review sheets live in `./clips/`, `./obstacle-clips/`,
  `./vertical-clips/`, and `./review-sheets/` (gitignored)
- Notes / briefings about clips go to the Obsidian vault, not this repo
- ffmpeg is required to merge best video+audio at >720p; without it the format fallback drops to a pre-merged mp4 (typically 720p max)
