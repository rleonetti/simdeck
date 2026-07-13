"""Shared constants for the SimDeck UI."""
from __future__ import annotations

# ── Version ───────────────────────────────────────────────────────────────────
__version__    = "1.4.0"
_RELEASES_URL  = "https://api.github.com/repos/rleonetti/simdeck/releases/latest"
_RELEASES_PAGE = "https://github.com/rleonetti/simdeck/releases/latest"

# ── Colors ────────────────────────────────────────────────────────────────────
_GREEN   = "#2ecc71"
_YELLOW  = "#f0a500"
_GREY    = "#484848"
_MUTED   = "#888888"
_AMBER   = "#c07800"

# ── Polling ───────────────────────────────────────────────────────────────────
_POLL_MS = 500
_SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

# ── LIFX effects ──────────────────────────────────────────────────────────────
_ALL_EFFECTS = ["rev_counter", "brake_lights", "flag_effect", "pit_limiter"]

_MODE_LABELS = {"left_right": "Left → Right", "center": "Center Fill", "full": "Full Strip"}
_MODE_VALUES = {v: k for k, v in _MODE_LABELS.items()}

_SCHEME_LABELS = {
    "classic": "Classic  (green → red, red flash)",
    "porsche": "Porsche  (green → red, blue flash)",
    "formula": "Formula  (red only, blue flash)",
    "icy":     "Icy      (green → blue, white flash)",
}
_SCHEME_VALUES = {v: k for k, v in _SCHEME_LABELS.items()}

_FLAG_ORDER: list[str] = [
    "flag_yellow", "flag_red", "flag_blue", "flag_white",
    "flag_green", "flag_checkered", "flag_black",
]
_FLAG_DISPLAY: dict[str, str] = {
    "flag_yellow":    "Yellow",
    "flag_red":       "Red",
    "flag_blue":      "Blue",
    "flag_white":     "White",
    "flag_green":     "Green",
    "flag_checkered": "Checkered",
    "flag_black":     "Black (penalty)",
}
_FLAG_DESC: dict[str, str] = {
    "flag_yellow":    "Caution, no overtaking",
    "flag_red":       "Session stopped",
    "flag_blue":      "Let faster car through",
    "flag_white":     "Slow vehicle / last lap",
    "flag_green":     "Race start or restart",
    "flag_checkered": "Session finished",
    "flag_black":     "Penalty / disqualification",
}
_FLAG_DOT_COLOR: dict[str, str] = {
    "flag_yellow":    "#f0c000",
    "flag_red":       "#e74c3c",
    "flag_blue":      "#3b82f6",
    "flag_white":     "#cccccc",
    "flag_green":     "#2ecc71",
    "flag_checkered": "#cccccc",
    "flag_black":     "#e74c3c",
}

# ── Game auto-target ──────────────────────────────────────────────────────────
_AUTO_GAME_TARGETS = True

_KNOWN_GAMES: list[dict[str, str]] = [
    {"name": "Forza Horizon 6",            "exe": "forzahorizon6.exe"},
    {"name": "Forza Motorsport",           "exe": "forzamotorsport.exe"},
    {"name": "Assetto Corsa EVO",          "exe": "AssettoCorsaEVO.exe"},
    {"name": "Assetto Corsa Rally",        "exe": "acr.exe"},
    {"name": "Assetto Corsa Competizione", "exe": "AC2-Win64-Shipping.exe"},
    {"name": "Assetto Corsa",              "exe": "acs.exe"},
    {"name": "BeamNG.drive",               "exe": "beamng.drive.exe"},
    {"name": "iRacing",                    "exe": "iracingsim64dx11.exe"},
    {"name": "rFactor 2",                  "exe": "rfactor2.exe"},
]

_EXE_TO_NAME: dict[str, str] = {g["exe"]: g["name"] for g in _KNOWN_GAMES}
