"""Print the Border Bridge data join report.

    python scripts/verify_border_data.py

Lists shapes with no graph node (and why), graph nodes with no shape or no display
name, asymmetric borders in the source data, and every excluded border.
Exits non-zero if a playable node is missing a shape or a name.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.games.border_bridge.graph import data_report, get_graph  # noqa: E402


def main() -> int:
    report = data_report()
    graph = get_graph()
    edges = sum(len(n) for n in graph.adj.values()) // 2
    print(f"Graph: {len(graph.nodes)} countries, {edges} land borders\n")

    print(f"Shapes without a graph node ({len(report['shapes_without_node'])}), drawn as background:")
    by_reason: dict[str, list[str]] = {}
    for fid, name, reason in report["shapes_without_node"]:
        by_reason.setdefault(reason, []).append(f"{name or '?'} ({fid})")
    for reason, items in sorted(by_reason.items()):
        print(f"  {reason}: {len(items)}")
        print("    " + ", ".join(items))

    print(f"\nGraph nodes without a shape: {report['nodes_without_shape'] or 'none'}")
    print(f"Graph nodes without a name in countries.json: {report['nodes_without_name'] or 'none'}")
    print(f"Asymmetric borders in source (made symmetric): {report['asymmetries'] or 'none'}")
    print("Excluded borders:")
    for a, b, why in report["excluded_borders"]:
        print(f"  {a}-{b}: {why}")
    dropped = report["territory_borders_dropped"]
    print(f"Borders dropped because one side is a territory ({len(dropped)}):")
    print("  " + ", ".join(f"{a}-{b}" for a, b, _ in dropped))
    return 1 if report["nodes_without_shape"] or report["nodes_without_name"] else 0


if __name__ == "__main__":
    sys.exit(main())
