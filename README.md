# ninjaclips

Personal-use ninja warrior content vault. Downloads competition footage and cuts
it into shareable clips. Everything runs locally — no footage leaves the machine.

## Requirements

- Python 3.12 (pinned in `.python-version`)
- [uv](https://docs.astral.sh/uv/)
- ffmpeg — `brew install ffmpeg` recommended. Without it the bundled
  `static-ffmpeg` downloads a native build on first use.

## Setup

```bash
uv sync
```

## Batch archiving (the usual path)

Put one row per run in a CSV, then let it run unattended:

```csv
# jobs.csv — times accept 4243 or 1:10:43
url,start,end,athlete
https://www.youtube.com/live/AIYxvfxnbz8?t=4243,1:10:43,1:14:55,Esme Newton-Pawlus
```

```bash
uv run ninjaclips batch jobs.csv              # full downloads
uv run ninjaclips batch jobs.csv --sections   # fetch only each run's window
```

Every row is validated before the first download, so a typo in the last line
fails immediately rather than an hour in. Clips land UNCONFIRMED; review and
confirm each one, then `prune`.

`--sections` fetches only the needed span — roughly 40 MB instead of 5 GB for a
two-minute window — but that window **can never be widened later**. Use full
downloads when the boundaries are uncertain. See `jobs.example.csv`.

## The core workflow

Download a broadcast, cut one athlete's run, confirm it looks right, then
reclaim the disk the source was using.

```bash
# 1. Download. Raise --max-height if you care about the vertical export.
uv run ninjaclips download "https://www.youtube.com/watch?v=VIDEO_ID" --max-height 1440

# 2. Cut the run. --at is the `t=` value from a YouTube URL, in seconds.
uv run ninjaclips clip --at 4243 --end 4285 --video VIDEO_ID --athlete "Athlete Name"

# 3. Review the contact sheet, then confirm.
uv run ninjaclips confirm "clips/<clip>.mp4" --every-seconds 2 --columns 6 --rows 5
uv run ninjaclips confirm "clips/<clip>.mp4" --yes

# 4. Delete the source. Dry-run by default; --yes actually deletes.
uv run ninjaclips prune
uv run ninjaclips prune --yes
```

`prune` will not delete a source unless every clip derived from it is confirmed,
still on disk, and frame-accurate. Sources with no derived clips are reported
but never deleted.

If the athlete is in the [WNL-Athlete-Video-Index](https://github.com/) DB, step
2 can look up timestamps instead of taking `--at`:

```bash
uv run ninjaclips index-status --athlete "Athlete Name" --video VIDEO_ID
uv run ninjaclips clip --athlete "Athlete Name" --video VIDEO_ID
```

Resolution order for the DB: `--db-path` → `$WNL_DB_PATH` →
`~/projects/WNL-Athlete-Video-Index/data/wnl_athlete_video_index.db`. A missing
DB exits 2; use manual `--at` mode instead.

## Short-form output

```bash
uv run ninjaclips segment "clips/<clip>.mp4" --output manifests/run.json
uv run ninjaclips cut-manifest manifests/run.json --output-dir obstacle-clips
uv run ninjaclips vertical obstacle-clips/<clip>.mp4 --duration 15
```

`segment` is currently fixed-window arithmetic, not detection — see the roadmap
in `CLAUDE.md`. Expect to hand-edit the manifest JSON before cutting.

## Development

```bash
uv run pytest
uv run ruff check .
```

Every command supports `--json` for scripting; human-readable messages go to
stderr so stdout stays parseable.
