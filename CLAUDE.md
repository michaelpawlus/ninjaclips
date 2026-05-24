# ninjaclips

Personal-use ninja warrior content vault. Downloads source videos (YouTube) and produces clips for cross-platform posting.

## Vision

Guiding wedge: paste or download a WNL/ninja competition livestream, choose an
athlete, and get their full run plus obstacle-level clips and vertical
short-form highlights.

Build this as a personal dogfooding product first. The project should stay
agent-driven and CLI-first while workflows are still being discovered: commands
collect structured data and generate deterministic artifacts, while the agent
handles judgment, review, and iteration. If the workflow proves valuable beyond
personal use, the product version can add API-backed vision/summarization,
hosted storage, accounts, queues, cost controls, publishing integrations, and
rights/copyright guardrails.

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

# Useful flags
--pre-pad N       # seconds before the timestamp (default 5)
--duration N      # max clip length in seconds (default 90, capped at next appearance)
--reencode        # decode/re-encode instead of stream-copy (slower, no frozen leader)
--force           # overwrite existing clip files (default: skip)
--dry-run         # resolve everything, skip ffmpeg
--json            # JSON array on stdout (one record per appearance)
--db-path PATH    # WNL SQLite DB path (default $WNL_DB_PATH)
```

Clips land in `./clips/` named `{athlete-slug} - {title-fragment} [{youtube_id}] [{start}].mp4`.

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
  --columns N        # contact strip frame count, default 5
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

- Stage 2b: highlight detection inside a rough cut ("find the cool moves")
- Stage 3: tracked crop / per-platform reformatting beyond center-crop vertical
- Stage 4: caption / metadata generation for posting

## Conventions

- CLI-by-default; every command supports `--json`
- Video assets live in `./downloads/` (gitignored — large binary files)
- Generated clips and review sheets live in `./clips/`, `./obstacle-clips/`,
  `./vertical-clips/`, and `./review-sheets/` (gitignored)
- Notes / briefings about clips go to the Obsidian vault, not this repo
- ffmpeg is required to merge best video+audio at >720p; without it the format fallback drops to a pre-merged mp4 (typically 720p max)
