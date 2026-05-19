# ninjaclips

Personal-use ninja warrior content vault. Downloads source videos (YouTube) and produces clips for cross-platform posting.

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

### Dependency: WNL-Athlete-Video-Index

Stage 2 reads athlete→timestamp data from WNL's SQLite DB. Resolution order:
`--db-path` arg → `$WNL_DB_PATH` env var → `~/projects/WNL-Athlete-Video-Index/data/wnl_athlete_video_index.db`.
Missing DB exits with code 2.

## Roadmap

- Stage 2b: highlight detection inside a rough cut ("find the cool moves")
- Stage 3: per-platform reformatting (vertical 9:16 for Shorts/TikTok, 1:1 for IG, etc.)
- Stage 4: caption / metadata generation for posting

## Conventions

- CLI-by-default; every command supports `--json`
- Video assets live in `./downloads/` (gitignored — large binary files)
- Notes / briefings about clips go to the Obsidian vault, not this repo
- ffmpeg is required to merge best video+audio at >720p; without it the format fallback drops to a pre-merged mp4 (typically 720p max)
