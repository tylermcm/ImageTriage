"""Day and hour sections for a list of photos in time order (the grid's headers when it is sorted by capture time or date modified).

`sections_for` takes the photos' times in the order they are shown and answers where each section starts, how many photos it
holds and what its header says. A section is a day; a day with more than `HOURS_ABOVE` photos taken in more than one hour is
divided by the hour. Photos next to each other with the same day (and hour) are one section, so the function does not care which
way the list is sorted. A time of zero or less is "not known" and those photos share one section.

`layout` puts sections into a grid of `columns` cells: each section starts a new row with a header cell, and the row a section
ends in is filled out with blanks. The grid's cells stay one size, so the view that shows them stays an ordinary uniform grid.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

HOURS_ABOVE = 100           # a day with more photos than this is divided by the hour

_DAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")

FILLER = -1                 # a cell with nothing in it


@dataclass(frozen=True, slots=True)
class Section:
    start: int              # the first photo's row
    count: int
    title: str              # "Sat 14 Nov 2026"
    detail: str = ""        # "09:00 – 10:00" when the day is divided by the hour


def _stamp(seconds: float):
    """(year, month, day, hour, weekday) in local time, or None for a time that is not known or not representable."""
    if not seconds or seconds <= 0:
        return None
    try:
        local = time.localtime(seconds)
    except (OverflowError, OSError, ValueError):
        return None
    return local.tm_year, local.tm_mon, local.tm_mday, local.tm_hour, local.tm_wday


def _title(stamp) -> str:
    if stamp is None:
        return "No date"
    year, month, day, _hour, weekday = stamp
    return f"{_DAYS[weekday]} {day} {_MONTHS[month - 1]} {year}"


def _hours(first: int, last: int) -> str:
    return f"{first:02d}:00 – {(last + 1) % 24:02d}:00"


def sections_for(times, hours_above: int = HOURS_ABOVE) -> list[Section]:
    """The sections of photos whose times are `times`, in the order shown. [] when no photo has a known time."""
    stamps = []
    known = False
    cache: dict[int, tuple | None] = {}
    for seconds in times:
        minute = int(seconds // 60) if seconds and seconds > 0 else 0       # photos of one minute share a day and an hour
        stamp = cache.get(minute, False)
        if stamp is False:
            stamp = cache[minute] = _stamp(seconds)
        known = known or stamp is not None
        stamps.append(stamp)
    if not known:
        return []
    out: list[Section] = []
    total = len(stamps)
    start = 0
    while start < total:
        day = stamps[start][:3] if stamps[start] is not None else None
        end = start + 1
        while end < total and (stamps[end][:3] if stamps[end] is not None else None) == day:
            end += 1
        title = _title(stamps[start])
        if day is not None and end - start > hours_above and stamps[start][3] != stamps[end - 1][3]:
            at = start
            while at < end:
                hour = stamps[at][3]
                until = at + 1
                while until < end and stamps[until][3] == hour:
                    until += 1
                out.append(Section(at, until - at, title, _hours(hour, hour)))
                at = until
        else:
            out.append(Section(start, end - start, title))
        start = end
    return out


def layout(sections, columns: int, lead: int = 0) -> tuple[list[int], list[int]]:
    """Cells for `sections` in a grid `columns` wide that already holds `lead` cells (the folder cards).

    Returns (cells, index_of_row). `cells[i]` is what the cell `lead + i` shows: a photo's row (0 or more), `FILLER`, or a
    section's header as `-2 - n` for section n. `index_of_row[row]` is the cell index (counted from 0, `lead` included) of a photo."""
    columns = max(1, int(columns))
    cells: list[int] = []
    index_of_row: list[int] = []
    for number, section in enumerate(sections):
        used = (lead + len(cells)) % columns
        if used:
            cells.extend([FILLER] * (columns - used))            # the row the folders or the section before ended in
        cells.append(-2 - number)
        first = lead + len(cells)
        cells.extend(range(section.start, section.start + section.count))
        index_of_row.extend(range(first, first + section.count))
    return cells, index_of_row
