"""Utility module for musical key conversion and display formatting.

Provides bidirectional mapping between Camelot notation (e.g. "7A", "8B")
and standard musical key names (e.g. "D minor", "A major"), plus helpers
for parsing detector output and building human-readable display labels.
"""

from __future__ import annotations


# ---------------------------------------------------------------------------
# Canonical lookup tables
# ---------------------------------------------------------------------------
# Camelot notation:
#   - "A" suffix = minor key
#   - "B" suffix = major key
# These are the standard Open Key / Camelot Wheel mappings.

CAMELOT_TO_STANDARD_MINOR: dict[str, str] = {
    "1A": "G# minor",
    "2A": "D# minor",
    "3A": "A# minor",
    "4A": "F minor",
    "5A": "C minor",
    "6A": "G minor",
    "7A": "D minor",
    "8A": "A minor",
    "9A": "E minor",
    "10A": "B minor",
    "11A": "F# minor",
    "12A": "C# minor",
}

CAMELOT_TO_STANDARD_MAJOR: dict[str, str] = {
    "1B": "B major",
    "2B": "F# major",
    "3B": "C# major",
    "4B": "G# major",
    "5B": "D# major",
    "6B": "A# major",
    "7B": "F major",
    "8B": "C major",
    "9B": "G major",
    "10B": "D major",
    "11B": "A major",
    "12B": "E major",
}

# Reverse lookups: standard name -> camelot
STANDARD_TO_CAMELOT: dict[str, str] = {}
for _c, _s in CAMELOT_TO_STANDARD_MINOR.items():
    STANDARD_TO_CAMELOT[_s] = _c
for _c, _s in CAMELOT_TO_STANDARD_MAJOR.items():
    STANDARD_TO_CAMELOT[_s] = _c

# Note-name -> Camelot mappings (used by the detector in analyzer.py)
NOTE_TO_CAMELOT_MINOR: dict[str, str] = {
    "A": "8A",
    "A#": "3A",
    "B": "10A",
    "C": "5A",
    "C#": "12A",
    "D": "7A",
    "D#": "2A",
    "E": "9A",
    "F": "4A",
    "F#": "11A",
    "G": "6A",
    "G#": "1A",
}

NOTE_TO_CAMELOT_MAJOR: dict[str, str] = {
    "A": "11B",
    "A#": "6B",
    "B": "1B",
    "C": "8B",
    "C#": "3B",
    "D": "10B",
    "D#": "5B",
    "E": "12B",
    "F": "7B",
    "F#": "2B",
    "G": "9B",
    "G#": "4B",
}

# ---------------------------------------------------------------------------
# Ordered list for dropdown / menu rendering (Camelot wheel order)
# ---------------------------------------------------------------------------
CAMELOT_WHEEL_ORDER = [
    "1A", "1B", "2A", "2B", "3A", "3B", "4A", "4B",
    "5A", "5B", "6A", "6B", "7A", "7B", "8A", "8B",
    "9A", "9B", "10A", "10B", "11A", "11B", "12A", "12B",
]


# ---------------------------------------------------------------------------
# Conversion helpers
# ---------------------------------------------------------------------------

def camelot_to_standard(camelot: str) -> str | None:
    """Convert a Camelot key like '7A' to a standard name like 'D minor'.

    Returns None if the input is not a recognised Camelot key.
    """
    normalised = camelot.strip().upper()
    if normalised in CAMELOT_TO_STANDARD_MINOR:
        return CAMELOT_TO_STANDARD_MINOR[normalised]
    if normalised in CAMELOT_TO_STANDARD_MAJOR:
        return CAMELOT_TO_STANDARD_MAJOR[normalised]
    return None


def standard_to_camelot(standard: str) -> str | None:
    """Convert a standard key name like 'D minor' to a Camelot key like '7A'.

    Returns None if the input is not recognised.
    """
    return STANDARD_TO_CAMELOT.get(standard.strip())


def note_mode_to_camelot(note_name: str, mode: str) -> str | None:
    """Given a note name (e.g. 'D') and mode ('major'/'minor'), return Camelot.

    This is the helper used by the detector in analyzer.py.
    """
    note = note_name.strip()
    mode_lower = mode.strip().lower()
    if mode_lower == "minor":
        return NOTE_TO_CAMELOT_MINOR.get(note)
    if mode_lower == "major":
        return NOTE_TO_CAMELOT_MAJOR.get(note)
    return None


def camelot_to_note_mode(camelot: str) -> tuple[str, str] | None:
    """Given a Camelot key like '7A', return (note_name, mode).

    E.g. ('D', 'minor'). Returns None if unrecognised.
    """
    normalised = camelot.strip()
    standard = camelot_to_standard(normalised)
    if standard is None:
        return None
    # standard is like "D minor"
    parts = standard.rsplit(" ", 1)
    if len(parts) == 2:
        return parts[0], parts[1]
    return None


# ---------------------------------------------------------------------------
# Display formatting
# ---------------------------------------------------------------------------

def format_key_display(camelot: str | None, musical_key: str | None) -> str:
    """Build a combined display label, e.g. '7A · D minor'.

    Falls back gracefully when one or both values are missing.
    """
    has_camelot = bool(camelot and camelot.strip())
    has_musical = bool(musical_key and musical_key.strip())

    if has_camelot and has_musical:
        return f"{camelot.strip()} \u00b7 {musical_key.strip()}"
    if has_camelot:
        return camelot.strip()
    if has_musical:
        return musical_key.strip()
    return ""


def parse_musical_key(text: str) -> tuple[str, str] | None:
    """Try to parse a string like 'D minor' or 'F# major' into (note, mode).

    Returns None if the text cannot be parsed.
    """
    text = text.strip()
    if not text:
        return None

    # Try splitting on last space
    parts = text.rsplit(" ", 1)
    if len(parts) != 2:
        return None

    note_name, mode = parts[0], parts[1].lower()
    if mode not in ("major", "minor"):
        return None

    valid_notes = {"C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B",
                    "Db", "Eb", "Gb", "Ab", "Bb"}
    if note_name not in valid_notes:
        return None

    return note_name, mode


def normalize_key_from_detector(
    note_name: str,
    mode: str,
) -> tuple[str, str]:
    """Normalize raw detector output into (musical_key, camelot_key).

    This is the canonical normalisation point.  The detector returns a
    note index + mode; this function turns it into both representations.
    """
    musical_key = f"{note_name.strip()} {mode.strip().lower()}"
    camelot = note_mode_to_camelot(note_name, mode)
    return musical_key, camelot or ""


def sort_key_camelot(key: str | None) -> tuple[int, str]:
    """Return a sort tuple for a key value, parsing Camelot if possible.

    Falls back to (99, 'Z') for unparseable keys so they sort to the end.
    Used by the frontend sortByKey function.
    """
    if not key:
        return (99, "Z")

    # Try to extract a Camelot pattern from the string.
    # The display format is like "7A · D minor", so we search for \d+[AB].
    import re
    match = re.search(r"(\d+)([AB])", key, re.IGNORECASE)
    if match:
        return (int(match.group(1)), match.group(2).upper())

    # Try raw Camelot
    match = re.match(r"^(\d+)([AB])$", key.strip(), re.IGNORECASE)
    if match:
        return (int(match.group(1)), match.group(2).upper())

    return (99, "Z")
