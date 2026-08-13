"""Parse the timecode forms an operator actually types.

Cut points come from watching YouTube, so they arrive as `1:14:55` from the
progress bar or as `4243` from a `t=` URL parameter. Accept both rather than
making the operator convert.
"""

from __future__ import annotations


class TimecodeError(ValueError):
    """A timecode string could not be parsed."""


def parse_timecode(value: str | float | int) -> float:
    """Parse `SS`, `MM:SS`, or `HH:MM:SS` (with optional fractional seconds).

    Bare numbers are seconds, so a `t=4243` URL parameter can be passed
    through unchanged.
    """
    if isinstance(value, (int, float)):
        seconds = float(value)
        if seconds < 0:
            raise TimecodeError(f"timecode cannot be negative: {value}")
        return seconds

    text = str(value).strip()
    if not text:
        raise TimecodeError("empty timecode")

    negative = text.startswith("-")
    if negative:
        raise TimecodeError(f"timecode cannot be negative: {value}")

    parts = text.split(":")
    if len(parts) > 3:
        raise TimecodeError(f"too many ':' separated fields in {value!r}")

    try:
        numbers = [float(p) for p in parts]
    except ValueError as exc:
        raise TimecodeError(f"non-numeric field in {value!r}") from exc

    # Only the seconds field may be fractional; H and M must be whole.
    for number in numbers[:-1]:
        if not number.is_integer():
            raise TimecodeError(f"only the seconds field may be fractional: {value!r}")

    total = 0.0
    for number in numbers:
        total = total * 60 + number
    return total


def format_timecode(seconds: float) -> str:
    """Render seconds as `H:MM:SS`, matching what YouTube displays."""
    if seconds < 0:
        raise TimecodeError(f"timecode cannot be negative: {seconds}")
    whole = int(seconds)
    hours, remainder = divmod(whole, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}"
