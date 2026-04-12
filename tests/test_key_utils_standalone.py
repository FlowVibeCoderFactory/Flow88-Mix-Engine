"""Lightweight tests for key_utils module — runs with plain `python3`."""
from __future__ import annotations

import sys
from pathlib import Path

# Ensure the repo root is on the path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

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

_passed = 0
_failed = 0


def check(label: str, got, want) -> None:
    global _passed, _failed
    if got == want:
        _passed += 1
        print(f"  PASS  {label}")
    else:
        _failed += 1
        print(f"  FAIL  {label}")
        print(f"        got  = {got!r}")
        print(f"        want = {want!r}")


# ---------------------------------------------------------------------------
print("=== camelot_to_standard ===")
check("7A", camelot_to_standard("7A"), "D minor")
check("8A", camelot_to_standard("8A"), "A minor")
check("1A", camelot_to_standard("1A"), "G# minor")
check("12A", camelot_to_standard("12A"), "C# minor")
check("11B", camelot_to_standard("11B"), "A major")
check("8B", camelot_to_standard("8B"), "C major")
check("1B", camelot_to_standard("1B"), "B major")
check("12B", camelot_to_standard("12B"), "E major")
check("7a (case)", camelot_to_standard("7a"), "D minor")
check(" 7A  (ws)", camelot_to_standard(" 7A "), "D minor")
check("invalid ''", camelot_to_standard(""), None)
check("invalid 'XX'", camelot_to_standard("XX"), None)
check("invalid '13A'", camelot_to_standard("13A"), None)
check("invalid '7C'", camelot_to_standard("7C"), None)

# ---------------------------------------------------------------------------
print("\n=== standard_to_camelot ===")
check("D minor", standard_to_camelot("D minor"), "7A")
check("A minor", standard_to_camelot("A minor"), "8A")
check("G# minor", standard_to_camelot("G# minor"), "1A")
check("A major", standard_to_camelot("A major"), "11B")
check("C major", standard_to_camelot("C major"), "8B")
check("B major", standard_to_camelot("B major"), "1B")
check(" D minor  (ws)", standard_to_camelot(" D minor "), "7A")
check("invalid ''", standard_to_camelot(""), None)
check("invalid 'X minor'", standard_to_camelot("X minor"), None)
check("invalid 'D huge'", standard_to_camelot("D huge"), None)

# ---------------------------------------------------------------------------
print("\n=== note_mode_to_camelot ===")
check("D minor", note_mode_to_camelot("D", "minor"), "7A")
check("A Minor (case)", note_mode_to_camelot("A", "Minor"), "8A")
check("A major", note_mode_to_camelot("A", "major"), "11B")
check("C MAJOR (case)", note_mode_to_camelot("C", "MAJOR"), "8B")
check("F# minor", note_mode_to_camelot("F#", "minor"), "11A")
check("C# major", note_mode_to_camelot("C#", "major"), "3B")
check("D dorian (bad)", note_mode_to_camelot("D", "dorian"), None)

# ---------------------------------------------------------------------------
print("\n=== camelot_to_note_mode ===")
check("7A", camelot_to_note_mode("7A"), ("D", "minor"))
check("8A", camelot_to_note_mode("8A"), ("A", "minor"))
check("11B", camelot_to_note_mode("11B"), ("A", "major"))
check("8B", camelot_to_note_mode("8B"), ("C", "major"))
check("11A", camelot_to_note_mode("11A"), ("F#", "minor"))
check("3B", camelot_to_note_mode("3B"), ("C#", "major"))
check("invalid ''", camelot_to_note_mode(""), None)
check("invalid 'XX'", camelot_to_note_mode("XX"), None)

# ---------------------------------------------------------------------------
print("\n=== format_key_display ===")
check("both", format_key_display("7A", "D minor"), "7A \u00b7 D minor")
check("both 11B", format_key_display("11B", "A major"), "11B \u00b7 A major")
check("only camelot", format_key_display("7A", None), "7A")
check("only camelot (empty musical)", format_key_display("7A", ""), "7A")
check("only musical", format_key_display(None, "D minor"), "D minor")
check("only musical (empty camelot)", format_key_display("", "D minor"), "D minor")
check("neither", format_key_display(None, None), "")
check("both empty", format_key_display("", ""), "")
check("strips ws", format_key_display(" 7A ", " D minor "), "7A \u00b7 D minor")

# ---------------------------------------------------------------------------
print("\n=== parse_musical_key ===")
check("D minor", parse_musical_key("D minor"), ("D", "minor"))
check("F# minor", parse_musical_key("F# minor"), ("F#", "minor"))
check("A major", parse_musical_key("A major"), ("A", "major"))
check("C major", parse_musical_key("C major"), ("C", "major"))
check("Bb minor", parse_musical_key("Bb minor"), ("Bb", "minor"))
check("Eb major", parse_musical_key("Eb major"), ("Eb", "major"))
check("invalid ''", parse_musical_key(""), None)
check("invalid 'D'", parse_musical_key("D"), None)
check("invalid 'D dorian'", parse_musical_key("D dorian"), None)
check("invalid 'X minor'", parse_musical_key("X minor"), None)

# ---------------------------------------------------------------------------
print("\n=== normalize_key_from_detector ===")
musical, camelot = normalize_key_from_detector("D", "minor")
check("musical", musical, "D minor")
check("camelot", camelot, "7A")
musical, camelot = normalize_key_from_detector("A", "major")
check("musical", musical, "A major")
check("camelot", camelot, "11B")
musical, camelot = normalize_key_from_detector("F#", "minor")
check("musical", musical, "F# minor")
check("camelot", camelot, "11A")
musical, camelot = normalize_key_from_detector(" D ", " minor ")
check("musical (ws)", musical, "D minor")
check("camelot (ws)", camelot, "7A")

# ---------------------------------------------------------------------------
print("\n=== sort_key_camelot ===")
check("raw 7A", sort_key_camelot("7A"), (7, "A"))
check("raw 11B", sort_key_camelot("11B"), (11, "B"))
check("display fmt", sort_key_camelot("7A · D minor"), (7, "A"))
check("display 11B", sort_key_camelot("11B · A major"), (11, "B"))
check("None", sort_key_camelot(None), (99, "Z"))
check("empty", sort_key_camelot(""), (99, "Z"))
check("unknown", sort_key_camelot("unknown"), (99, "Z"))

# ---------------------------------------------------------------------------
print("\n=== round-trip consistency ===")
for camelot, standard in CAMELOT_TO_STANDARD_MINOR.items():
    check(f"{camelot} -> {standard} -> back", standard_to_camelot(standard), camelot)
for camelot, standard in CAMELOT_TO_STANDARD_MAJOR.items():
    check(f"{camelot} -> {standard} -> back", standard_to_camelot(standard), camelot)

# ---------------------------------------------------------------------------
print("\n=== camelot wheel order ===")
check("length 24", len(CAMELOT_WHEEL_ORDER), 24)
check("all unique", len(CAMELOT_WHEEL_ORDER), len(set(CAMELOT_WHEEL_ORDER)))
for c in CAMELOT_WHEEL_ORDER:
    check(f"{c} maps to standard", camelot_to_standard(c) is not None, True)

# ---------------------------------------------------------------------------
print("\n=== STANDARD_TO_CAMELOT completeness ===")
for standard in CAMELOT_TO_STANDARD_MINOR.values():
    check(f"'{standard}' in reverse", standard in STANDARD_TO_CAMELOT, True)
for standard in CAMELOT_TO_STANDARD_MAJOR.values():
    check(f"'{standard}' in reverse", standard in STANDARD_TO_CAMELOT, True)
check("total entries", len(STANDARD_TO_CAMELOT), 24)

# ---------------------------------------------------------------------------
print(f"\n{'='*50}")
print(f"Results: {_passed} passed, {_failed} failed, {_passed + _failed} total")
if _failed > 0:
    print("SOME TESTS FAILED")
    sys.exit(1)
else:
    print("ALL TESTS PASSED")
    sys.exit(0)
