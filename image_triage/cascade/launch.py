"""Decide whether the rankers run for an Analyze, and build them. No Qt, and no torch: the models live in worker processes."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Callable

from .task import ScorerFactory, Scorers

# Seconds per frame for both rankers together, measured. A 64-thread server CPU: MANIQA 3.6 s + Q-ReAlign 3.4 s. A 4-core i5 laptop (Core i5-8265U): MANIQA 56 s
# + Q-ReAlign 25 s. The spread is why the prompt gives a range and never a single number.
CPU_SECONDS_PER_FRAME_FAST = 7
CPU_SECONDS_PER_FRAME_LAPTOP = 80


@dataclass(slots=True, frozen=True)
class RankingDecision:
    use_rankers: bool
    device: str = ""  # "gpu" or "cpu" when rankers run
    use_blink: bool = False  # the eyes-closed check: CPU only and cheap, so it runs whenever the AI runtime is installed
    note: str = ""  # shown to the person when ranking is skipped
    ask_cpu_confirmation: bool = False
    confirmation_text: str = ""

    def without_ranking(self, note: str) -> "RankingDecision":
        return replace(self, use_rankers=False, device="", note=note, ask_cpu_confirmation=False, confirmation_text="")


def decide_ranking(runtime_device: str | None, rank_on_cpu: bool) -> RankingDecision:
    """``runtime_device`` is the managed AI runtime's device ("cuda", "cuda:1", "cpu"), or None when the runtime is not installed or not usable."""
    if runtime_device is None:
        return RankingDecision(False, note="Ranking and the eyes-closed check skipped: the AI runtime is not installed. Cleanup ran without them; AI setup can add it.")
    if runtime_device.strip().lower().startswith(("cuda", "gpu")):
        return RankingDecision(True, "gpu", use_blink=True)
    if rank_on_cpu:
        return RankingDecision(
            True,
            "cpu",
            use_blink=True,
            ask_cpu_confirmation=True,
            confirmation_text=(
                "Ranking runs two AI models. Without a usable GPU they are slow: measured at about "
                f"{CPU_SECONDS_PER_FRAME_FAST} seconds per frame on a fast workstation and over a minute per frame ({CPU_SECONDS_PER_FRAME_LAPTOP} seconds) on a laptop. "
                "They score a quarter to a half of the folder, so a large folder can take hours.\n\n"
                "Rank on the CPU? Choose No to run the quick cleanup without ranking."
            ),
        )
    return RankingDecision(False, use_blink=True, note="Ranking skipped: no usable GPU. Turn on ranking on the CPU in settings to include it (slow).")


def default_scorer_factory(device: str, *, rankers: bool = True, blink: bool = True) -> ScorerFactory:
    """Build the worker scorers for one run: the two rankers (when ``rankers``) and the eyes-closed check (when ``blink``)."""

    def factory(cancel: Callable[[], bool], progress: Callable[[int, int, str], None]) -> Scorers:
        from .rankers import blink_ranker, maniqa_ranker, qrealign_ranker

        made: list = []
        maniqa = aesthetic = eyes = None
        names: list[str] = []
        if rankers:
            maniqa = maniqa_ranker(device=device, cancel_check=cancel, progress=lambda done, total: progress(done, total, "Ranking (quality)"))
            aesthetic = qrealign_ranker(device=device, cancel_check=cancel, progress=lambda done, total: progress(done, total, "Ranking (aesthetic)"))
            made += [maniqa, aesthetic]
            names += ["maniqa", "q-realign aesthetic"]
        if blink:
            eyes = blink_ranker(device="cpu", cancel_check=cancel, progress=lambda done, total: progress(done, total, "Checking for closed eyes"))
            made.append(eyes)

        def close() -> None:
            for worker in made:
                worker.close()

        return Scorers(maniqa=maniqa, aesthetic=aesthetic, blink=eyes, names=tuple(names), close=close)

    return factory
