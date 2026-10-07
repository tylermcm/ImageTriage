"""Row sizes of the sidebar's Collections list."""
from __future__ import annotations

# Beyond this the Collections section scrolls rather than growing, so it can
# never crowd the folder tree out of the sidebar.
_MAX_VISIBLE_PROJECT_ROWS = 6
_PROJECT_ROW_PX = 34
# The empty row's stylesheet has a 32px minimum plus 3px vertical padding on
# each side. Its viewport must include that full 38px box or Qt clips glyphs.
_PROJECT_EMPTY_ROW_PX = 38
