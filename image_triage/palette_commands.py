"""Command descriptors and ranking for the QML command palette."""
from __future__ import annotations
from dataclasses import dataclass

@dataclass(slots=True)
class PaletteCommand:
    id: str
    title: str
    callback: callable
    subtitle: str = ""
    keywords: tuple[str, ...] = ()
    shortcut: str = ""
    section: str = ""


def _rank_commands(
    commands: list[PaletteCommand],
    query: str,
    recent_command_ids: tuple[str, ...],
) -> list[tuple[tuple[int, int, str], PaletteCommand]]:
    recent_positions = {command_id: index for index, command_id in enumerate(recent_command_ids)}
    ranked: list[tuple[tuple[int, int, str], PaletteCommand]] = []
    for command in commands:
        score = _command_match_score(command, query)
        if query and score <= 0:
            continue
        recent_position = recent_positions.get(command.id, 999)
        if not query:
            sort_key = (0 if command.id in recent_positions else 1, recent_position, command.title.casefold())
        else:
            sort_key = (-score, recent_position, command.title.casefold())
        ranked.append((sort_key, command))
    ranked.sort(key=lambda item: item[0])
    return ranked


def _command_match_score(command: PaletteCommand, query: str) -> int:
    if not query:
        return 1
    haystacks = [command.title, command.subtitle, command.section, *command.keywords]
    best = 0
    for haystack in haystacks:
        normalized = _normalize_query(haystack)
        if not normalized:
            continue
        if normalized == query:
            best = max(best, 900)
            continue
        if normalized.startswith(query):
            best = max(best, 780)
            continue
        word_parts = normalized.split()
        if any(part.startswith(query) for part in word_parts):
            best = max(best, 690)
            continue
        if query in normalized:
            best = max(best, 560 - min(200, normalized.index(query)))
            continue
        subsequence = _subsequence_score(query, normalized)
        if subsequence > 0:
            best = max(best, 340 + subsequence)
    return best


def _subsequence_score(query: str, haystack: str) -> int:
    if not query:
        return 0
    position = -1
    spread = 0
    for character in query:
        next_position = haystack.find(character, position + 1)
        if next_position < 0:
            return 0
        if position >= 0:
            spread += next_position - position - 1
        position = next_position
    return max(1, 90 - min(80, spread))


def _normalize_query(value: str) -> str:
    return " ".join((value or "").casefold().replace("&", " ").replace("...", "").split())
