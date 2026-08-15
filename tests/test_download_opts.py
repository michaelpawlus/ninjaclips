"""Guard the yt-dlp options that silently degrade a download when they regress.

Every assertion here corresponds to a failure seen against real WNL sources in
the season-archive sprint. All of them either produced a lower-quality file that
looked like a success, or a hard 403 — so none of them are theoretical.
"""

from pathlib import Path

from ninjaclips.download import DownloadConfig, _ydl_opts, write_section_sidecar


def _opts(**kw):
    return _ydl_opts(DownloadConfig(output_dir=Path("/tmp/out"), **kw))


def test_resolution_cap_is_short_side_not_height():
    """A height filter downgrades portrait sources; `res:N` caps the short side.

    Against a real 1080x1920 source, `height<=1440` excluded the native
    rendition and selected 720x1280 instead.
    """
    opts = _opts(max_height=1440)
    assert opts["format_sort"][0] == "res:1440"
    assert "height<=" not in opts["format"]


def test_format_does_not_pin_container():
    """Pinning mp4/m4a hides VP9/AV1+opus, the only renditions above 1080p."""
    fmt = _opts(max_height=1440)["format"]
    assert "ext=mp4" not in fmt
    assert "ext=m4a" not in fmt


def test_fps_sorts_ahead_of_vcodec():
    """60fps source halves the interpolation factor for a slow-motion ramp."""
    sort = _opts(max_height=1440)["format_sort"]
    assert sort.index("fps") < sort.index("vcodec")


def test_sectioned_download_sets_ranges():
    opts = _opts(max_height=1440, section_start=100.0, section_end=200.0)
    assert "download_ranges" in opts


def test_sectioned_download_never_forces_keyframes_at_cuts():
    """That option routes the fetch through ffmpeg, which gets a hard 403.

    ffmpeg requests the googlevideo URL without the extracting client's
    identity. Dropping it still trims exactly and decodes cleanly from frame 0.
    """
    opts = _opts(max_height=1440, section_start=100.0, section_end=200.0)
    assert not opts.get("force_keyframes_at_cuts")


def test_sectioned_download_drops_the_archive():
    """The archive records a bare video id, so a partial would make a later
    full download silently no-op."""
    opts = _opts(max_height=1440, section_start=100.0, section_end=200.0)
    assert "download_archive" not in opts
    assert "download_archive" in _opts(max_height=1440)


def test_sectioned_filename_records_the_range():
    """A partial file must never be mistaken for the full video."""
    opts = _opts(max_height=1440, section_start=100.0, section_end=200.0)
    assert "[sec 100-200]" in opts["outtmpl"]


def test_section_sidecar_records_offset(tmp_path):
    """A partial file's t=0 is not the source's t=0."""
    media = tmp_path / "video.mp4"
    media.write_bytes(b"")
    sidecar = write_section_sidecar(media, "abc123", 1464.0, 2019.0)

    import json

    data = json.loads(sidecar.read_text())
    assert data["section_start"] == 1464.0
    assert data["section_end"] == 2019.0
    assert data["youtube_id"] == "abc123"
