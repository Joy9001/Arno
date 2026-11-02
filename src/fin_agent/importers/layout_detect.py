"""Small host-side text helpers used before a parser exists."""

from __future__ import annotations

from typing import Any


def text_lines(words: list[dict[str, Any]], tolerance: float | None = None) -> list[list[dict]]:
    if not words:
        return []
    if tolerance is None:
        heights = sorted(word["bottom"] - word["top"] for word in words)
        tolerance = 0.6 * heights[len(heights) // 2]
    ordered = sorted(words, key=lambda word: (word["top"], word["x0"]))
    lines: list[list[dict]] = []
    current: list[dict] = []
    current_y: float | None = None
    for word in ordered:
        y = (word["top"] + word["bottom"]) / 2
        if current_y is not None and abs(y - current_y) <= tolerance:
            current.append(word)
        else:
            if current:
                lines.append(sorted(current, key=lambda item: item["x0"]))
            current = [word]
            current_y = y
    if current:
        lines.append(sorted(current, key=lambda item: item["x0"]))
    return lines


def line_text(line: list[dict[str, Any]]) -> str:
    return " ".join(str(word["text"]) for word in line)


__all__ = ["line_text", "text_lines"]
