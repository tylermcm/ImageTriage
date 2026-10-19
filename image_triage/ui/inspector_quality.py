"""Plain-language findings from the existing preview heuristics; no extra image work."""
from ..review_tools import InspectionStats, histogram_synopsis


def quality_readout(stats: InspectionStats) -> tuple[dict[str, str], str]:
    if stats.width <= 0 or stats.height <= 0:
        return ({name: "Pending" for name in ("Exposure", "Focus", "Noise", "Motion")},
                "Preview measurements appear here when the selected photo loads.")

    findings = [histogram_synopsis(stats)]
    if stats.highlight_clip_pct >= 2:
        exposure = f"Highlights clipped · {stats.highlight_clip_pct:.1f}%"
    elif stats.shadow_clip_pct >= 4:
        exposure = f"Shadows clipped · {stats.shadow_clip_pct:.1f}%"
    elif stats.median_luminance >= 190:
        exposure = "Bright tones"
    elif stats.median_luminance <= 55:
        exposure = "Dark tones"
    else:
        exposure = "Balanced tones"

    if stats.detail_valid_tile_count < 4:
        focus = "Inconclusive"
        findings.append("Too few textured regions to judge focus reliably.")
    else:
        score = stats.detail_score
        label = "Sharp detail" if score >= 70 else "Moderate detail" if score >= 40 else "Low detail"
        focus = f"{label} · {score:.0f}/100"
        if score < 40:
            findings.append("Low detail may indicate missed focus, blur, or a soft subject; inspect at 100%.")

    noise_label = "High" if stats.noise_score >= 65 else "Moderate" if stats.noise_score >= 32 else "Low"
    noise = f"{noise_label} · {stats.noise_score:.0f}/100"
    if stats.noise_score >= 65:
        findings.append("Fine-grained variation suggests visible noise.")
    motion = "Possible blur" if stats.motion_blur_score >= 60 else "No strong signal"
    if stats.motion_blur_score >= 60:
        findings.append("Directional softness suggests possible motion blur.")
    return ({"Exposure": exposure, "Focus": focus, "Noise": noise, "Motion": motion}, " ".join(findings))
