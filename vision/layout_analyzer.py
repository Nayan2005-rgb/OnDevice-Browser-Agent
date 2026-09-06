"""Layout analysis: spatial relationships between visual UI elements."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

from vision.visual_element import (
    Box,
    VisualElement,
    center_distance,
    contains,
    iou,
)


class LayoutAnalyzer:
    """Detect approximate alignment, containment, rows/columns, and groups.

    Heuristic only — purpose is useful spatial structure, not perfect layout parsing.
    """

    def __init__(
        self,
        *,
        align_tolerance: float = 12.0,
        row_tolerance: float = 18.0,
        col_tolerance: float = 18.0,
        group_gap: float = 40.0,
    ):
        self.align_tolerance = align_tolerance
        self.row_tolerance = row_tolerance
        self.col_tolerance = col_tolerance
        self.group_gap = group_gap

    def analyze(self, elements: Sequence[VisualElement]) -> Dict[str, Any]:
        els = list(elements or [])
        if not els:
            return {
                "groups": [],
                "alignments": {"horizontal": [], "vertical": []},
                "containment": [],
                "rows": [],
                "columns": [],
            }

        alignments = self._find_alignments(els)
        containment = self._find_containment(els)
        rows = self._cluster_rows(els)
        columns = self._cluster_columns(els)
        groups = self._build_groups(els, rows, containment)

        return {
            "groups": groups,
            "alignments": alignments,
            "containment": containment,
            "rows": rows,
            "columns": columns,
        }

    def _find_alignments(self, els: List[VisualElement]) -> Dict[str, List[Dict]]:
        horizontal: List[Dict] = []
        vertical: List[Dict] = []
        tol = self.align_tolerance

        for i, a in enumerate(els):
            for b in els[i + 1 :]:
                ay = a.box.center[1]
                by = b.box.center[1]
                ax = a.box.center[0]
                bx = b.box.center[0]
                if abs(ay - by) <= tol:
                    horizontal.append({"elements": [a.id, b.id], "axis": "y"})
                if abs(ax - bx) <= tol:
                    vertical.append({"elements": [a.id, b.id], "axis": "x"})
                # Edge alignment
                if abs(a.box.x - b.box.x) <= tol:
                    vertical.append(
                        {"elements": [a.id, b.id], "axis": "left", "kind": "edge"}
                    )
                if abs(a.box.y - b.box.y) <= tol:
                    horizontal.append(
                        {"elements": [a.id, b.id], "axis": "top", "kind": "edge"}
                    )
        return {"horizontal": horizontal, "vertical": vertical}

    def _find_containment(self, els: List[VisualElement]) -> List[Dict[str, str]]:
        pairs: List[Dict[str, str]] = []
        for outer in els:
            for inner in els:
                if outer.id == inner.id:
                    continue
                if contains(outer.box, inner.box) and outer.box.area > inner.box.area * 1.05:
                    pairs.append({"parent": outer.id, "child": inner.id})
        return pairs

    def _cluster_rows(self, els: List[VisualElement]) -> List[Dict[str, Any]]:
        sorted_els = sorted(els, key=lambda e: e.box.center[1])
        rows: List[List[VisualElement]] = []
        for el in sorted_els:
            placed = False
            for row in rows:
                if abs(row[0].box.center[1] - el.box.center[1]) <= self.row_tolerance:
                    row.append(el)
                    placed = True
                    break
            if not placed:
                rows.append([el])
        result = []
        for i, row in enumerate(rows):
            if len(row) < 2:
                continue
            row_sorted = sorted(row, key=lambda e: e.box.x)
            result.append(
                {
                    "id": f"row_{i:03d}",
                    "elements": [e.id for e in row_sorted],
                }
            )
        return result

    def _cluster_columns(self, els: List[VisualElement]) -> List[Dict[str, Any]]:
        sorted_els = sorted(els, key=lambda e: e.box.center[0])
        cols: List[List[VisualElement]] = []
        for el in sorted_els:
            placed = False
            for col in cols:
                if abs(col[0].box.center[0] - el.box.center[0]) <= self.col_tolerance:
                    col.append(el)
                    placed = True
                    break
            if not placed:
                cols.append([el])
        result = []
        for i, col in enumerate(cols):
            if len(col) < 2:
                continue
            col_sorted = sorted(col, key=lambda e: e.box.y)
            result.append(
                {
                    "id": f"col_{i:03d}",
                    "elements": [e.id for e in col_sorted],
                }
            )
        return result

    def _build_groups(
        self,
        els: List[VisualElement],
        rows: List[Dict[str, Any]],
        containment: List[Dict[str, str]],
    ) -> List[Dict[str, Any]]:
        groups: List[Dict[str, Any]] = []
        used = set()

        # Dialog / container groups from containment
        children_of: Dict[str, List[str]] = {}
        for pair in containment:
            children_of.setdefault(pair["parent"], []).append(pair["child"])

        id_to_el = {e.id: e for e in els}
        for parent_id, kids in children_of.items():
            if len(kids) < 2:
                continue
            parent = id_to_el.get(parent_id)
            gtype = "dialog" if parent and parent.type == "dialog" else "container"
            # Prefer form if mixed inputs+buttons
            kid_els = [id_to_el[k] for k in kids if k in id_to_el]
            types = {k.type for k in kid_els}
            if "input" in types and ("button" in types or "link" in types):
                gtype = "form"
            groups.append(
                {
                    "id": f"group_{len(groups):03d}",
                    "type": gtype,
                    "elements": [parent_id] + kids,
                }
            )
            used.update([parent_id] + kids)

        # Row-based form groups for remaining interactive clusters
        for row in rows:
            members = [m for m in row["elements"] if m not in used]
            if len(members) < 2:
                continue
            member_els = [id_to_el[m] for m in members if m in id_to_el]
            if any(e.interactive for e in member_els):
                groups.append(
                    {
                        "id": f"group_{len(groups):03d}",
                        "type": "row",
                        "elements": members,
                    }
                )
                used.update(members)

        # Proximity clustering for leftovers
        remaining = [e for e in els if e.id not in used]
        clusters = self._proximity_clusters(remaining)
        for cluster in clusters:
            if len(cluster) < 2:
                continue
            groups.append(
                {
                    "id": f"group_{len(groups):03d}",
                    "type": "cluster",
                    "elements": [e.id for e in cluster],
                }
            )
        return groups

    def _proximity_clusters(
        self, els: List[VisualElement]
    ) -> List[List[VisualElement]]:
        if not els:
            return []
        clusters: List[List[VisualElement]] = [[els[0]]]
        for el in els[1:]:
            best = None
            best_dist = float("inf")
            for i, cluster in enumerate(clusters):
                d = min(center_distance(el.box, c.box) for c in cluster)
                if d < best_dist:
                    best_dist = d
                    best = i
            # Adaptive threshold based on element size
            thresh = self.group_gap + max(el.box.width, el.box.height) * 0.5
            if best is not None and best_dist <= thresh:
                clusters[best].append(el)
            else:
                clusters.append([el])
        return clusters
