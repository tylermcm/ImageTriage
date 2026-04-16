"""Reusable HTML reporting for ranked culling outputs."""

from __future__ import annotations

from html import escape
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from app.engine.ranking.exports import RankedExportRow, group_ranked_export_rows


def build_cluster_report(
    output_path: Path,
    rows: Iterable[RankedExportRow],
    *,
    summary: Dict[str, Any],
    include_singletons: bool = False,
    max_clusters: Optional[int] = None,
) -> Path:
    """Build a human-inspectable HTML report grouped by ranked cluster."""

    grouped = group_ranked_export_rows(rows)
    ordered_cluster_ids = [
        cluster_id
        for cluster_id in sorted(
            grouped.keys(),
            key=lambda cluster_id: (
                0 if grouped[cluster_id][0].cluster_has_human_best else 1,
                -grouped[cluster_id][0].cluster_size,
                cluster_id,
            ),
        )
        if include_singletons or grouped[cluster_id][0].cluster_size > 1
    ]
    if max_clusters is not None:
        ordered_cluster_ids = ordered_cluster_ids[:max_clusters]

    sections: List[str] = []
    for cluster_id in ordered_cluster_ids:
        members = grouped[cluster_id]
        top_pick = members[0]
        top_pick_status = _top_pick_status(top_pick)
        sections.append(
            """
<section class="cluster">
  <header class="cluster-header">
    <div>
      <h2>{cluster_id}</h2>
      <p class="cluster-meta">
        size {cluster_size} | reason: {cluster_reason} | top pick: {top_file}
      </p>
    </div>
    <div class="cluster-status {status_class}">{status_text}</div>
  </header>
  <div class="member-grid">
    {member_cards}
  </div>
</section>
""".format(
                cluster_id=escape(cluster_id),
                cluster_size=members[0].cluster_size,
                cluster_reason=escape(members[0].cluster_reason),
                top_file=escape(top_pick.file_name),
                status_class=top_pick_status["status_class"],
                status_text=escape(top_pick_status["status_text"]),
                member_cards="\n".join(_member_card(member) for member in members),
            )
        )

    summary_items = [
        ("Checkpoint", str(summary.get("checkpoint_path", ""))),
        ("Total images", str(summary.get("total_images", 0))),
        ("Total clusters", str(summary.get("total_clusters", 0))),
        ("Singleton clusters", str(summary.get("singleton_clusters", 0))),
        ("Largest cluster", str(summary.get("largest_cluster_size", 0))),
        ("Labeled clusters", str(summary.get("labeled_clusters", 0))),
        ("Clusters with human best", str(summary.get("clusters_with_human_best", 0))),
        (
            "Top-1 human-best match rate",
            _format_optional_percentage(summary.get("model_top1_human_best_match_rate")),
        ),
        (
            "Top-1 non-reject rate",
            _format_optional_percentage(summary.get("model_top1_non_reject_rate")),
        ),
        ("Model architecture", str(summary.get("model_architecture", ""))),
    ]

    html = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Ranked Cluster Report</title>
  <style>
    :root {{
      color-scheme: dark;
      --bg: #101010;
      --panel: #1a1a1a;
      --panel-2: #202020;
      --text: #f5f5f5;
      --muted: #c0c0c0;
      --line: #333333;
      --good: #1d6b3b;
      --warn: #7b5b16;
      --bad: #6c1d1d;
      --info: #1e4f84;
    }}
    body {{
      margin: 0;
      background: var(--bg);
      color: var(--text);
      font-family: "Segoe UI", Tahoma, sans-serif;
    }}
    main {{
      max-width: 1680px;
      margin: 0 auto;
      padding: 24px;
    }}
    .summary {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 12px;
      padding: 20px;
      margin-bottom: 24px;
    }}
    .summary-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
      gap: 10px 18px;
      margin-top: 14px;
    }}
    .summary-item {{
      background: var(--panel-2);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 10px 12px;
    }}
    .summary-item strong {{
      display: block;
      color: var(--muted);
      font-size: 12px;
      margin-bottom: 4px;
      text-transform: uppercase;
      letter-spacing: 0.04em;
    }}
    .cluster {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 12px;
      padding: 18px;
      margin-bottom: 20px;
    }}
    .cluster-header {{
      display: flex;
      justify-content: space-between;
      gap: 16px;
      align-items: flex-start;
      margin-bottom: 14px;
    }}
    .cluster-header h2 {{
      margin: 0 0 6px 0;
      font-size: 20px;
    }}
    .cluster-meta {{
      margin: 0;
      color: var(--muted);
      font-size: 13px;
    }}
    .cluster-status {{
      border-radius: 999px;
      padding: 8px 12px;
      font-size: 13px;
      font-weight: 600;
      white-space: nowrap;
    }}
    .cluster-status.match {{
      background: var(--good);
    }}
    .cluster-status.neutral {{
      background: var(--info);
    }}
    .cluster-status.miss {{
      background: var(--bad);
    }}
    .member-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
      gap: 14px;
    }}
    .member-card {{
      background: var(--panel-2);
      border: 1px solid var(--line);
      border-radius: 10px;
      overflow: hidden;
    }}
    .member-card.top-pick {{
      border-color: #4da3ff;
      box-shadow: 0 0 0 1px #4da3ff;
    }}
    .member-image {{
      height: 240px;
      background: #0b0b0b;
      display: flex;
      align-items: center;
      justify-content: center;
      overflow: hidden;
    }}
    .member-image img {{
      width: 100%;
      height: 100%;
      object-fit: contain;
      display: block;
      background: #0b0b0b;
    }}
    .member-body {{
      padding: 12px;
    }}
    .member-body p {{
      margin: 0 0 6px 0;
      font-size: 13px;
      word-break: break-word;
    }}
    .badges {{
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
      margin-bottom: 8px;
    }}
    .badge {{
      border-radius: 999px;
      padding: 4px 8px;
      font-size: 12px;
      font-weight: 600;
    }}
    .badge.rank {{
      background: var(--info);
    }}
    .badge.best {{
      background: var(--good);
    }}
    .badge.acceptable {{
      background: var(--warn);
    }}
    .badge.reject {{
      background: var(--bad);
    }}
    .badge.top-pick {{
      background: #4da3ff;
      color: #081018;
    }}
  </style>
</head>
<body>
  <main>
    <section class="summary">
      <h1>Ranked Cluster Report</h1>
      <p>Images are grouped by cluster and sorted by model score from strongest to weakest.</p>
      <div class="summary-grid">
        {summary_items}
      </div>
    </section>
    {cluster_sections}
  </main>
</body>
</html>
""".format(
        summary_items="\n".join(
            '<div class="summary-item"><strong>{}</strong>{}</div>'.format(
                escape(label),
                escape(value),
            )
            for label, value in summary_items
        ),
        cluster_sections="\n".join(sections),
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html, encoding="utf-8")
    return output_path


def _member_card(row: RankedExportRow) -> str:
    """Render one ranked image card for the HTML report."""

    image_uri = Path(row.file_path).as_uri()
    badges = [
        '<span class="badge rank">Rank #{}</span>'.format(row.rank_in_cluster),
        '<span class="badge top-pick">Model Top Pick</span>' if row.rank_in_cluster == 1 else "",
        '<span class="badge best">Human Best</span>' if row.is_human_best else "",
        '<span class="badge acceptable">Human Acceptable</span>' if row.is_human_acceptable else "",
        '<span class="badge reject">Human Reject</span>' if row.is_human_reject else "",
    ]
    return """
<article class="member-card {top_pick_class}">
  <div class="member-image">
    <img src="{image_uri}" alt="{file_name}" loading="lazy">
  </div>
  <div class="member-body">
    <div class="badges">
      {badges}
    </div>
    <p><strong>{file_name}</strong></p>
    <p>score: {score:.4f}</p>
    <p>timestamp: {timestamp}</p>
    <p>{relative_path}</p>
  </div>
</article>
""".format(
        top_pick_class="top-pick" if row.rank_in_cluster == 1 else "",
        image_uri=escape(image_uri),
        file_name=escape(row.file_name),
        badges="\n".join(badge for badge in badges if badge),
        score=row.score,
        timestamp=escape(row.capture_timestamp or "missing"),
        relative_path=escape(row.relative_path),
    )


def _top_pick_status(row: RankedExportRow) -> Dict[str, str]:
    """Build one status badge for the cluster top pick."""

    if row.model_top1_matches_human_best is True:
        return {"status_class": "match", "status_text": "Top pick matches human best"}
    if row.model_top1_is_human_non_reject is True:
        return {"status_class": "neutral", "status_text": "Top pick is human keep"}
    if row.model_top1_is_human_non_reject is False:
        return {"status_class": "miss", "status_text": "Top pick is human reject"}
    return {"status_class": "neutral", "status_text": "No human winner label available"}


def _format_optional_percentage(value: Any) -> str:
    """Format optional percentage values for the report summary."""

    if value is None:
        return "n/a"
    return f"{float(value) * 100.0:.1f}%"
