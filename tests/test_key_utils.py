"""Lightweight tests for key_utils module."""
from __future__ import annotations

import pytest

from key_utils import (
    CAMELOT_TO_STANDARD_MAJOR,
    CAMELOT_TO_STANDARD_MINOR,
    CAMELOT_WHEEL_ORDER,
    NOTE_TO_CAMELOT_MAJOR,
    NOTE_TO_CAMELOT_MINOR,
    STANDARD_TO_CAMELOT,
    camelot_to_note_mode,
    camelot_to_standard,
    format_key_display,
    note_mode_to_camelot,
    normalize_key_from_detector,
    parse_musical_key,
    sort_key_camelot,
    standard_to_camelot,
)


# ---------------------------------------------------------------------------
# camelot_to_standard
# ---------------------------------------------------------------------------

class TestCamelotToStandard:
    def test_minor_keys(self):
        assert camelot_to_standard("7A") == "D minor"
        assert camelot_to_standard("8A") == "A minor"
        assert camelot_to_standard("1A") == "G# minor"
        assert camelot_to_standard("12A") == "C# minor"

    def test_major_keys(self):
        assert camelot_to_standard("11B") == "A major"
        assert camelot_to_standard("8B") == "C major"
        assert camelot_to_standard("1B") == "B major"
        assert camelot_to_standard("12B") == "E major"

    def test_case_insensitive(self):
        assert camelot_to_standard("7a") == "D minor"
        assert camelot_to_standard("11b") == "A major"

    def test_strips_whitespace(self):
        assert camelot_to_standard(" 7A ") == "D minor"

    def test_invalid_returns_none(self):
        assert camelot_to_standard("") is None
        assert camelot_to_standard("XX") is None
        assert camelot_to_standard("13A") is None
        assert camelot_to_standard("7C") is None


# ---------------------------------------------------------------------------
# standard_to_camelot
# ---------------------------------------------------------------------------

class TestStandardToCamelot:
    def test_minor_keys(self):
        assert standard_to_camelot("D minor") == "7A"
        assert standard_to_camelot("A minor") == "8A"
        assert standard_to_camelot("G# minor") == "1A"

    def test_major_keys(self):
        assert standard_to_camelot("A major") == "11B"
        assert standard_to_camelot("C major") == "8B"
        assert standard_to_camelot("B major") == "1B"

    def test_strips_whitespace(self):
        assert standard_to_camelot(" D minor ") == "7A"

    def test_invalid_returns_none(self):
        assert standard_to_camelot("") is None
        assert standard_to_camelot("X minor") is None
        assert standard_to_camelot("D huge") is None


# ---------------------------------------------------------------------------
# note_mode_to_camelot
# ---------------------------------------------------------------------------

class TestNoteModeToCamelot:
    def test_minor(self):
        assert note_mode_to_camelot("D", "minor") == "7A"
        assert note_mode_to_camelot("A", "Minor") == "8A"

    def test_major(self):
        assert note_mode_to_camelot("A", "major") == "11B"
        assert note_mode_to_camelot("C", "MAJOR") == "8B"

    def test_sharps(self):
        assert note_mode_to_camelot("F#", "minor") == "11A"
        assert note_mode_to_camelot("C#", "major") == "3B"

    def test_invalid_mode(self):
        assert note_mode_to_camelot("D", "dorian") is None


# ---------------------------------------------------------------------------
# camelot_to_note_mode
# ---------------------------------------------------------------------------

class TestCamelotToNoteMode:
    def test_minor(self):
        assert camelot_to_note_mode("7A") == ("D", "minor")
        assert camelot_to_note_mode("8A") == ("A", "minor")

    def test_major(self):
        assert camelot_to_note_mode("11B") == ("A", "major")
        assert camelot_to_note_mode("8B") == ("C", "major")

    def test_sharps(self):
        assert camelot_to_note_mode("11A") == ("F#", "minor")
        assert camelot_to_note_mode("3B") == ("C#", "major")

    def test_invalid(self):
        assert camelot_to_note_mode("") is None
        assert camelot_to_note_mode("XX") is None


# ---------------------------------------------------------------------------
# format_key_display
# ---------------------------------------------------------------------------

class TestFormatKeyDisplay:
    def test_both_present(self):
        assert format_key_display("7A", "D minor") == "7A \u00b7 D minor"
        assert format_key_display("11B", "A major") == "11B \u00b7 A major"

    def test_only_camelot(self):
        assert format_key_display("7A", None) == "7A"
        assert format_key_display("7A", "") == "7A"

    def test_only_musical(self):
        assert format_key_display(None, "D minor") == "D minor"
        assert format_key_display("", "D minor") == "D minor"

    def test_neither(self):
        assert format_key_display(None, None) == ""
        assert format_key_display("", "") == ""

    def test_strips_whitespace(self):
        assert format_key_display(" 7A ", " D minor ") == "7A \u00b7 D minor"


# ---------------------------------------------------------------------------
# parse_musical_key
# ---------------------------------------------------------------------------

class TestParseMusicalKey:
    def test_valid_minor(self):
        assert parse_musical_key("D minor") == ("D", "minor")
        assert parse_musical_key("F# minor") == ("F#", "minor")

    def test_valid_major(self):
        assert parse_musical_key("A major") == ("A", "major")
        assert parse_musical_key("C major") == ("C", "major")

    def test_flats(self):
        assert parse_musical_key("Bb minor") == ("Bb", "minor")
        assert parse_musical_key("Eb major") == ("Eb", "major")

    def test_invalid(self):
        assert parse_musical_key("") is None
        assert parse_musical_key("D") is None
        assert parse_musical_key("D dorian") is None
        assert parse_musical_key("X minor") is None


# ---------------------------------------------------------------------------
# normalize_key_from_detector
# ---------------------------------------------------------------------------

class TestNormalizeKeyFromDetector:
    def test_minor(self):
        musical, camelot = normalize_key_from_detector("D", "minor")
        assert musical == "D minor"
        assert camelot == "7A"

    def test_major(self):
        musical, camelot = normalize_key_from_detector("A", "major")
        assert musical == "A major"
        assert camelot == "11B"

    def test_sharp(self):
        musical, camelot = normalize_key_from_detector("F#", "minor")
        assert musical == "F# minor"
        assert camelot == "11A"

    def test_strips_whitespace(self):
        musical, camelot = normalize_key_from_detector(" D ", " minor ")
        assert musical == "D minor"
        assert camelot == "7A"


# ---------------------------------------------------------------------------
# sort_key_camelot
# ---------------------------------------------------------------------------

class TestSortKeyCamelot:
    def test_raw_camelot(self):
        assert sort_key_camelot("7A") == (7, "A")
        assert sort_key_camelot("11B") == (11, "B")

    def test_display_format(self):
        assert sort_key_camelot("7A · D minor") == (7, "A")
        assert sort_key_camelot("11B · A major") == (11, "B")

    def test_none_and_empty(self):
        assert sort_key_camelot(None) == (99, "Z")
        assert sort_key_camelot("") == (99, "Z")

    def test_unparseable(self):
        assert sort_key_camelot("unknown") == (99, "Z")


# ---------------------------------------------------------------------------
# Consistency: round-trip camelot -> standard -> camelot
# ---------------------------------------------------------------------------

class TestRoundTrip:
    def test_all_minor(self):
        for camelot, standard in CAMELOT_TO_STANDARD_MINOR.items():
            assert standard_to_camelot(standard) == camelot

    def test_all_major(self):
        for camelot, standard in CAMELOT_TO_STANDARD_MAJOR.items():
            assert standard_to_camelot(standard) == camelot

    def test_all_note_names(self):
        for note in NOTE_TO_CAMELOT_MAJOR:
            result = note_mode_to_camelot(note, "major")
            assert result == NOTE_TO_CAMELOT_MAJOR[note]
        for note in NOTE_TO_CAMELOT_MINOR:
            result = note_mode_to_camelot(note, "minor")
            assert result == NOTE_TO_CAMELOT_MINOR[note]


# ---------------------------------------------------------------------------
# Camelot wheel ordering
# ---------------------------------------------------------------------------

class TestCamelotWheelOrder:
    def test_length(self):
        assert len(CAMELOT_WHEEL_ORDER) == 24

    def test_all_unique(self):
        assert len(CAMELOT_WHEEL_ORDER) == len(set(CAMELOT_WHEEL_ORDER))

    def test_all_map_to_standard(self):
        for c in CAMELOT_WHEEL_ORDER:
            assert camelot_to_standard(c) is not None


# ---------------------------------------------------------------------------
# STANDARD_TO_CAMELOT completeness
# ---------------------------------------------------------------------------

class TestStandardToCamelotCompleteness:
    def test_all_minor_entries(self):
        for standard in CAMELOT_TO_STANDARD_MINOR.values():
            assert standard in STANDARD_TO_CAMELOT

    def test_all_major_entries(self):
        for standard in CAMELOT_TO_STANDARD_MAJOR.values():
            assert standard in STANDARD_TO_CAMELOT

    def test_total_entries(self):
        assert len(STANDARD_TO_CAMELOT) == 24
