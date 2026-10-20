"""Which editor tools need which models, and what to tell the person when one is missing. Pure apart from asking the model store what is installed.

The Camera Raw masks ask Image Triage for the heavy lifting through host jobs: Subject and Background (BiRefNet), Landscape (OneFormer), People (OneFormer finds them, SAM splits
them, BiRefNet polishes the edges), click selection (SAM). Each is one of the optional models of the "Editor tools" group in Set Up AI, and each tool is gated on its own models, so any
combination of installed models works: a tool is available exactly when every model it needs is installed.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable

from . import ai_setup_plan as plan


@dataclass(frozen=True)
class EditorTool:
    kind: str  # the host-job kind the editor sends
    label: str  # what the person calls it
    models: tuple[str, ...]  # keys of the optional models it needs (``ai_setup_plan``)


TOOLS: tuple[EditorTool, ...] = (
    EditorTool("subjectMasks", "Subject and background masks", ("birefnet",)),
    EditorTool("sceneMasks", "Landscape masks", ("oneformer",)),
    EditorTool("peopleMasks", "People masks", ("oneformer", "sam", "birefnet")),
    EditorTool("clickSelect", "Click selection", ("sam",)),
    EditorTool("depthMap", "Depth maps", ("depth",)),
)
_BY_KIND = {tool.kind: tool for tool in TOOLS}


def tool_for(kind: str) -> EditorTool | None:
    return _BY_KIND.get(kind)


def installed_models() -> frozenset[str]:
    """The optional models whose files are installed and verified."""
    from .ai_model_store import bundle_status

    ready: set[str] = set()
    for item in plan.OPTIONAL_MODELS:
        try:
            if bundle_status(item.bundle).is_ready:
                ready.add(item.key)
        except Exception:  # an unreadable bundle is a missing one
            continue
    return frozenset(ready)


def missing_models(kind: str, installed: Iterable[str] | None = None) -> tuple[plan.OptionalModel, ...]:
    """The models ``kind`` still needs, or () when it has them all (or is not an editor tool)."""
    tool = _BY_KIND.get(kind)
    if tool is None:
        return ()
    have = installed_models() if installed is None else frozenset(installed)
    return tuple(plan.model(key) for key in tool.models if key not in have)


def available_kinds(installed: Iterable[str] | None = None) -> frozenset[str]:
    have = installed_models() if installed is None else frozenset(installed)
    return frozenset(tool.kind for tool in TOOLS if all(key in have for key in tool.models))


def unavailable_message(kind: str, missing: Iterable[plan.OptionalModel]) -> str:
    missing = tuple(missing)
    tool = _BY_KIND.get(kind)
    if tool is None or not missing:
        return ""
    names = [item.label for item in missing]
    listed = names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]
    size = plan.format_mb(sum(plan.model_mb(item.key) for item in missing))
    return f"{tool.label} need{'s' if not tool.label.endswith('s') else ''} {listed} ({size}). Open the AI chip, choose Set up AI and turn on Editor tools."


def block_reason(kind: str, installed: Iterable[str] | None = None) -> str:
    """Why ``kind`` cannot run right now, as one plain sentence; "" when it can run (or is not an editor tool)."""
    return unavailable_message(kind, missing_models(kind, installed))


Gate = Callable[[str], str]
