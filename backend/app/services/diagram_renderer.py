"""Safe, deterministic server-side diagram renderer (Mermaid subset -> standalone SVG)
and structured answer parser.

Security guarantees:
- Pure Python parser + SVG builder; never executes JavaScript or external binaries.
- Never loads remote fonts, images, scripts, or external stylesheets.
- Strips `click`, `href`, `callback` directives and escapes all text via XML entity escaping.
- Produces self-contained SVG compatible with both browser `<img>` and Qt `QSvgRenderer`.
"""
from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any
from xml.sax.saxutils import escape as xml_escape


def _esc(text: str) -> str:
    """Safely escape user/model text for SVG XML text nodes and attributes."""
    clean = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", str(text or "")).strip()
    return xml_escape(clean, {'"': "&quot;", "'": "&#39;"})


@dataclass
class FlowNode:
    id: str
    label: str
    shape: str = "rect"  # rect | cylinder | round | diamond
    subgraph: str | None = None


@dataclass
class FlowEdge:
    src: str
    dst: str
    label: str = ""
    style: str = "solid"  # solid | dashed | thick


@dataclass
class FlowSubgraph:
    id: str
    title: str
    node_ids: list[str] = field(default_factory=list)


@dataclass
class ErAttribute:
    col_type: str
    name: str
    key: str = ""  # PK | FK | UK
    comment: str = ""


@dataclass
class ErEntity:
    name: str
    attributes: list[ErAttribute] = field(default_factory=list)


@dataclass
class ErRelation:
    left: str
    right: str
    left_card: str
    right_card: str
    label: str
    identifying: bool = True


@dataclass
class DiagramResult:
    index: int
    kind: str  # flowchart | erDiagram | unknown
    source: str
    svg: str | None
    ok: bool
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "kind": self.kind,
            "source": self.source,
            "svg": self.svg or "",
            "ok": self.ok,
            "error": self.error,
        }


@dataclass
class StructuredAnswer:
    spoken_markdown: str
    spoken_clean: str
    technical_markdown: str
    how_to_draw_markdown: str
    diagrams: list[DiagramResult] = field(default_factory=list)
    pending_diagram: bool = False
    code_blocks: list[dict[str, str]] = field(default_factory=list)
    design_state: dict[str, Any] | None = None

    def __getitem__(self, key: str) -> Any:
        if key == "diagrams":
            return [d.to_dict() for d in self.diagrams]
        return getattr(self, key)

    def get(self, key: str, default: Any = None) -> Any:
        try:
            return self[key]
        except AttributeError:
            return default

    def to_dict(self) -> dict[str, Any]:
        return {
            "spoken_markdown": self.spoken_markdown,
            "spoken_clean": self.spoken_clean,
            "technical_markdown": self.technical_markdown,
            "how_to_draw_markdown": self.how_to_draw_markdown,
            "diagrams": [d.to_dict() for d in self.diagrams],
            "pending_diagram": self.pending_diagram,
            "code_blocks": self.code_blocks,
            "design_state": self.design_state,
        }


# ---------------------------------------------------------------------------
# Flowchart Parser & SVG Renderer
# ---------------------------------------------------------------------------

_NODE_TOKEN_RE = re.compile(
    r"^([A-Za-z0-9_.-]+)"
    r"(?:"
    r"\[\((.+?)\)\]"       # [(Cylinder)]
    r"|\(\[(.+?)\]\)"      # ([Stadium])
    r"|\[(.+?)\]"          # [Rectangle]
    r"|\((.+?)\)"          # (Rounded)
    r"|\{(.+?)\}"          # {Diamond}
    r")?$"
)

_EDGE_RE = re.compile(
    r"^(.+?)\s*"
    r"(?:"
    r"--\s*([^>-][^>-]*?)\s*-->"          # -- label -->
    r"|-\.\s*([^.>-][^>-]*?)\s*\.->"      # -. label .->
    r"|==\s*([^=>-][^>-]*?)\s*==>"        # == label ==>
    r"|(-->|-\.->|==>|---)"               # arrow without inline label
    r")"
    r"(?:\|([^|]+)\|)?\s*"                # optional |label|
    r"(.+)$"
)


def _strip_quotes(s: str) -> str:
    s = s.strip()
    if len(s) >= 2 and ((s[0] == '"' and s[-1] == '"') or (s[0] == "'" and s[-1] == "'")):
        return s[1:-1].strip()
    return s


def _upsert_node(
    token: str,
    nodes: dict[str, FlowNode],
    current_sg: FlowSubgraph | None,
) -> str:
    token = token.strip()
    m = _NODE_TOKEN_RE.match(token)
    if not m:
        nid = re.sub(r"[^A-Za-z0-9_.-]", "_", token)[:40]
        if not nid:
            raise ValueError(f"Invalid node token: {token}")
        if nid not in nodes:
            nodes[nid] = FlowNode(id=nid, label=nid, shape="rect", subgraph=current_sg.id if current_sg else None)
            if current_sg and nid not in current_sg.node_ids:
                current_sg.node_ids.append(nid)
        return nid

    nid, cyl, stad, rect, rnd, dia = m.groups()
    shape = "rect"
    raw_label = None
    if cyl is not None:
        shape, raw_label = "cylinder", cyl
    elif stad is not None:
        shape, raw_label = "round", stad
    elif rect is not None:
        shape, raw_label = "rect", rect
    elif rnd is not None:
        shape, raw_label = "round", rnd
    elif dia is not None:
        shape, raw_label = "diamond", dia

    label = _strip_quotes(raw_label) if raw_label is not None else nid
    if nid not in nodes:
        nodes[nid] = FlowNode(id=nid, label=label, shape=shape, subgraph=current_sg.id if current_sg else None)
    else:
        if raw_label is not None:
            nodes[nid].label = label
            nodes[nid].shape = shape
        if current_sg and nodes[nid].subgraph is None:
            nodes[nid].subgraph = current_sg.id
    if current_sg and nid not in current_sg.node_ids:
        current_sg.node_ids.append(nid)
    return nid


_CHAIN_ARROW_RE = re.compile(r"\s*((?:--+>|-\.+->|==+>|---+)(?:\|[^|]*\|)?)\s*")


def _split_chained_edges(line: str) -> list[str]:
    """Mermaid allows `A --> B -->|x| C`; split chains into single edges (`A --> B`, `B -->|x| C`)."""
    if line.lower().startswith(("subgraph", "end", "classdef", "class ", "style", "click", "linkstyle")):
        return [line]
    parts = _CHAIN_ARROW_RE.split(line)
    if len(parts) < 5:  # node, arrow, node  -> already a single edge
        return [line]
    nodes, arrows = parts[0::2], parts[1::2]
    return [f"{nodes[k]} {arrows[k]} {nodes[k + 1]}" for k in range(len(arrows)) if nodes[k].strip() and nodes[k + 1].strip()]


def _render_flowchart(source: str) -> DiagramResult:
    lines = [ln.strip() for ln in source.splitlines() if ln.strip() and not ln.strip().startswith("%%")]
    lines = lines[:1] + [piece for ln in lines[1:] for piece in _split_chained_edges(ln)]
    if not lines:
        return DiagramResult(0, "flowchart", source, None, False, "Empty diagram")

    header = lines[0]
    hm = re.match(r"^(?:flowchart|graph)\s+(LR|RL|TD|TB|BT)\b", header, re.I)
    direction = hm.group(1).upper() if hm else "LR"
    is_horizontal = direction in ("LR", "RL")

    nodes: dict[str, FlowNode] = {}
    edges: list[FlowEdge] = []
    subgraphs: list[FlowSubgraph] = []
    sg_stack: list[FlowSubgraph] = []

    for raw_ln in lines[1:]:
        ln = raw_ln.rstrip(";").strip()
        if not ln:
            continue
        low = ln.lower()
        # Security: ignore interactive / styling directives
        if low.startswith(("click ", "classdef ", "class ", "style ", "linkstyle ", "direction ")):
            continue
        if low == "end":
            if sg_stack:
                sg_stack.pop()
            continue
        if low.startswith("subgraph "):
            rest = ln[9:].strip()
            sm = re.match(r"^([A-Za-z0-9_.-]+)\s*\[(.+)\]$", rest)
            if sm:
                sg_id, sg_title = sm.group(1), _strip_quotes(sm.group(2))
            else:
                sg_title = _strip_quotes(rest)
                sg_id = re.sub(r"[^A-Za-z0-9_]", "_", sg_title) or f"sg_{len(subgraphs)}"
            sg = FlowSubgraph(id=sg_id, title=sg_title)
            subgraphs.append(sg)
            sg_stack.append(sg)
            continue

        cur_sg = sg_stack[-1] if sg_stack else None
        em = _EDGE_RE.match(ln)
        if em:
            lhs, lbl_solid, lbl_dash, lbl_thick, arrow_tok, pipe_lbl, rhs = em.groups()
            if not lhs.strip() or not rhs.strip():
                return DiagramResult(0, "flowchart", source, None, False, f"Incomplete edge: {ln}")
            # Reject clearly broken syntax in endpoints
            if any(bad in lhs or bad in rhs for bad in ("(((", ")))", "-->", "<--")):
                return DiagramResult(0, "flowchart", source, None, False, f"Invalid node syntax in: {ln}")

            style = "solid"
            if lbl_dash is not None or (arrow_tok and "." in arrow_tok):
                style = "dashed"
            elif lbl_thick is not None or (arrow_tok and "=" in arrow_tok):
                style = "thick"
            label = _strip_quotes(pipe_lbl or lbl_solid or lbl_dash or lbl_thick or "")

            src_id = _upsert_node(lhs, nodes, cur_sg)
            dst_id = _upsert_node(rhs, nodes, cur_sg)
            edges.append(FlowEdge(src=src_id, dst=dst_id, label=label, style=style))
        else:
            if any(bad in ln for bad in ("-->", "-.->", "==>", "(((", ")))")):
                return DiagramResult(0, "flowchart", source, None, False, f"Malformed flowchart line: {ln}")
            _upsert_node(ln, nodes, cur_sg)

    if not nodes:
        return DiagramResult(0, "flowchart", source, None, False, "No valid nodes found in flowchart")

    # Compute topological layers (ranks)
    indeg: dict[str, int] = {nid: 0 for nid in nodes}
    adj: dict[str, list[str]] = defaultdict(list)
    for e in edges:
        if e.src != e.dst:
            adj[e.src].append(e.dst)
            indeg[e.dst] = indeg.get(e.dst, 0) + 1

    rank: dict[str, int] = {nid: 0 for nid in nodes}
    queue = [nid for nid, d in indeg.items() if d == 0]
    visited = set()
    while queue:
        curr = queue.pop(0)
        visited.add(curr)
        for nxt in adj[curr]:
            if rank[nxt] < rank[curr] + 1:
                rank[nxt] = min(rank[curr] + 1, len(nodes))
            indeg[nxt] -= 1
            if indeg[nxt] <= 0 and nxt not in visited:
                queue.append(nxt)

    layers: dict[int, list[str]] = defaultdict(list)
    for nid in nodes:
        layers[rank[nid]].append(nid)

    sorted_ranks = sorted(layers.keys())
    if direction in ("RL", "BT"):
        sorted_ranks.reverse()

    # Node sizing and coordinates
    node_w, node_h = 156, 52
    col_gap = 92 if is_horizontal else 56
    row_gap = 42 if is_horizontal else 74
    pad_x, pad_y = 44, 48

    max_per_layer = max(len(layers[r]) for r in sorted_ranks)
    pos: dict[str, tuple[float, float]] = {}

    for c_idx, r in enumerate(sorted_ranks):
        nids = layers[r]
        count = len(nids)
        for r_idx, nid in enumerate(nids):
            if is_horizontal:
                x = pad_x + c_idx * (node_w + col_gap)
                total_span = max_per_layer * node_h + (max_per_layer - 1) * row_gap
                layer_span = count * node_h + (count - 1) * row_gap
                offset_y = pad_y + max(0.0, (total_span - layer_span) / 2.0)
                y = offset_y + r_idx * (node_h + row_gap)
            else:
                total_span = max_per_layer * node_w + (max_per_layer - 1) * col_gap
                layer_span = count * node_w + (count - 1) * col_gap
                offset_x = pad_x + max(0.0, (total_span - layer_span) / 2.0)
                x = offset_x + r_idx * (node_w + col_gap)
                y = pad_y + c_idx * (node_h + row_gap)
            pos[nid] = (x, y)

    max_x = max(x + node_w for x, _ in pos.values()) + pad_x
    max_y = max(y + node_h for _, y in pos.values()) + pad_y
    svg_w = int(max(360, max_x))
    svg_h = int(max(160, max_y))

    parts: list[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {svg_w} {svg_h}" width="{svg_w}" height="{svg_h}" '
        f'style="background:#ffffff;font-family:Segoe UI,system-ui,sans-serif;">',
        "<defs>",
        '<marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">'
        '<path d="M 0 1 L 10 5 L 0 9 z" fill="#475569"/></marker>',
        "</defs>",
    ]

    # Render subgraph boundary boxes
    for sg in subgraphs:
        pts = [pos[nid] for nid in sg.node_ids if nid in pos]
        if not pts:
            continue
        sx0 = min(p[0] for p in pts) - 16
        sy0 = min(p[1] for p in pts) - 28
        sx1 = max(p[0] + node_w for p in pts) + 16
        sy1 = max(p[1] + node_h for p in pts) + 14
        parts.append(
            f'<rect x="{sx0:.1f}" y="{sy0:.1f}" width="{sx1 - sx0:.1f}" height="{sy1 - sy0:.1f}" '
            f'rx="8" fill="#f8fafc" stroke="#94a3b8" stroke-dasharray="5,4" stroke-width="1.2"/>'
        )
        parts.append(
            f'<text x="{sx0 + 10:.1f}" y="{sy0 + 16:.1f}" font-size="11" font-weight="700" fill="#475569">'
            f"{_esc(sg.title)}</text>"
        )

    # Render edges
    for e in edges:
        x1, y1 = pos[e.src]
        x2, y2 = pos[e.dst]
        cx1, cy1 = x1 + node_w / 2, y1 + node_h / 2
        cx2, cy2 = x2 + node_w / 2, y2 + node_h / 2

        if is_horizontal:
            if cx2 >= cx1:
                sx, sy = x1 + node_w, cy1
                tx, ty = x2, cy2
            else:
                sx, sy = x1, cy1
                tx, ty = x2 + node_w, cy2
            mx = (sx + tx) / 2
            path_d = f"M {sx:.1f} {sy:.1f} C {mx:.1f} {sy:.1f}, {mx:.1f} {ty:.1f}, {tx:.1f} {ty:.1f}"
        else:
            if cy2 >= cy1:
                sx, sy = cx1, y1 + node_h
                tx, ty = cx2, y2
            else:
                sx, sy = cx1, y1
                tx, ty = cx2, y2 + node_h
            my = (sy + ty) / 2
            path_d = f"M {sx:.1f} {sy:.1f} C {sx:.1f} {my:.1f}, {tx:.1f} {my:.1f}, {tx:.1f} {ty:.1f}"

        dash = ' stroke-dasharray="5,4"' if e.style == "dashed" else ""
        sw = "2.4" if e.style == "thick" else "1.6"
        parts.append(
            f'<path d="{path_d}" fill="none" stroke="#475569" stroke-width="{sw}"{dash} marker-end="url(#arrow)"/>'
        )
        if e.label:
            lx = (sx + tx) / 2
            ly = (sy + ty) / 2 - 6
            lbl_esc = _esc(e.label)
            bw = max(36, len(e.label) * 6.5 + 10)
            parts.append(
                f'<rect x="{lx - bw / 2:.1f}" y="{ly - 11:.1f}" width="{bw:.1f}" height="16" rx="4" '
                f'fill="#ffffff" stroke="#cbd5e1" stroke-width="0.8"/>'
            )
            parts.append(
                f'<text x="{lx:.1f}" y="{ly + 1:.1f}" text-anchor="middle" font-size="10.5" fill="#334155">'
                f"{lbl_esc}</text>"
            )

    # Render nodes
    for nid, node in nodes.items():
        x, y = pos[nid]
        cx, cy = x + node_w / 2, y + node_h / 2
        if node.shape == "cylinder":
            ry = 7
            parts.append(
                f'<path d="M {x:.1f} {y + ry:.1f} L {x:.1f} {y + node_h - ry:.1f} '
                f'A {node_w / 2:.1f} {ry} 0 0 0 {x + node_w:.1f} {y + node_h - ry:.1f} '
                f'L {x + node_w:.1f} {y + ry:.1f}" fill="#eff6ff" stroke="#2563eb" stroke-width="1.6"/>'
            )
            parts.append(
                f'<ellipse cx="{cx:.1f}" cy="{y + ry:.1f}" rx="{node_w / 2:.1f}" ry="{ry}" '
                f'fill="#dbeafe" stroke="#2563eb" stroke-width="1.6"/>'
            )
        elif node.shape == "round":
            parts.append(
                f'<rect x="{x:.1f}" y="{y:.1f}" width="{node_w}" height="{node_h}" rx="24" '
                f'fill="#f0fdf4" stroke="#16a34a" stroke-width="1.6"/>'
            )
        elif node.shape == "diamond":
            pts_str = f"{cx:.1f},{y - 4:.1f} {x + node_w + 6:.1f},{cy:.1f} {cx:.1f},{y + node_h + 4:.1f} {x - 6:.1f},{cy:.1f}"
            parts.append(f'<polygon points="{pts_str}" fill="#fffbeb" stroke="#d97706" stroke-width="1.6"/>')
        else:
            parts.append(
                f'<rect x="{x:.1f}" y="{y:.1f}" width="{node_w}" height="{node_h}" rx="6" '
                f'fill="#f8fafc" stroke="#1e40af" stroke-width="1.6"/>'
            )

        # Wrap long node labels onto at most 2 lines
        words = node.label.split()
        lines_out: list[str] = []
        cur = ""
        for w in words:
            if len(cur) + len(w) + 1 <= 20:
                cur = f"{cur} {w}".strip()
            else:
                if cur:
                    lines_out.append(cur)
                cur = w
        if cur:
            lines_out.append(cur)
        if not lines_out:
            lines_out = [node.id]
        lines_out = lines_out[:2]

        if len(lines_out) == 1:
            parts.append(
                f'<text x="{cx:.1f}" y="{cy + 4:.1f}" text-anchor="middle" font-size="12" font-weight="600" fill="#0f172a">'
                f"{_esc(lines_out[0])}</text>"
            )
        else:
            parts.append(
                f'<text x="{cx:.1f}" y="{cy - 3:.1f}" text-anchor="middle" font-size="11.5" font-weight="600" fill="#0f172a">'
                f"{_esc(lines_out[0])}</text>"
            )
            parts.append(
                f'<text x="{cx:.1f}" y="{cy + 13:.1f}" text-anchor="middle" font-size="11.5" font-weight="600" fill="#0f172a">'
                f"{_esc(lines_out[1])}</text>"
            )

    parts.append("</svg>")
    return DiagramResult(0, "flowchart", source, "".join(parts), True, None)


# ---------------------------------------------------------------------------
# ER Diagram Parser & SVG Renderer
# ---------------------------------------------------------------------------

_ER_REL_RE = re.compile(
    r"^([A-Za-z0-9_.-]+)\s+"
    r"([|o}{]{1,2})(--|\.\.)([|o}{]{1,2})\s+"
    r"([A-Za-z0-9_.-]+)\s*:\s*(.+)$"
)


def _card_symbol(tok: str) -> str:
    if "{" in tok or "}" in tok:
        return "0..*" if "o" in tok else "1..*"
    if "o" in tok:
        return "0..1"
    return "1"


def _render_er_diagram(source: str) -> DiagramResult:
    lines = [ln.strip() for ln in source.splitlines() if ln.strip() and not ln.strip().startswith("%%")]
    if not lines or not lines[0].lower().startswith("erdiagram"):
        return DiagramResult(0, "erDiagram", source, None, False, "Not an erDiagram")

    entities: dict[str, ErEntity] = {}
    relations: list[ErRelation] = []
    current_ent: ErEntity | None = None

    for ln in lines[1:]:
        if current_ent is not None:
            if ln == "}":
                current_ent = None
                continue
            # Attribute line: type name [PK|FK|UK] ["comment"]
            cm = re.search(r'"([^"]*)"', ln)
            comment = cm.group(1) if cm else ""
            ln_no_comment = re.sub(r'"[^"]*"', "", ln).strip()
            toks = ln_no_comment.split()
            if len(toks) >= 2:
                col_type, col_name = toks[0], toks[1]
                key_flag = toks[2].upper() if len(toks) >= 3 and toks[2].upper() in ("PK", "FK", "UK") else ""
                current_ent.attributes.append(ErAttribute(col_type, col_name, key_flag, comment))
            continue

        if ln.endswith("{"):
            ename = ln[:-1].strip()
            if ename not in entities:
                entities[ename] = ErEntity(name=ename)
            current_ent = entities[ename]
            continue

        rm = _ER_REL_RE.match(ln)
        if rm:
            left, lcard, line_style, rcard, right, label = rm.groups()
            for en in (left, right):
                if en not in entities:
                    entities[en] = ErEntity(name=en)
            relations.append(
                ErRelation(
                    left=left,
                    right=right,
                    left_card=_card_symbol(lcard),
                    right_card=_card_symbol(rcard),
                    label=_strip_quotes(label),
                    identifying=(line_style == "--"),
                )
            )

    if not entities:
        return DiagramResult(0, "erDiagram", source, None, False, "No entities defined in erDiagram")

    ent_list = list(entities.values())
    cols = min(3, max(1, len(ent_list)))
    box_w = 230
    col_gap = 96
    row_gap = 64
    pad_x, pad_y = 40, 36

    pos: dict[str, tuple[float, float, float]] = {}  # name -> (x, y, h)
    row_heights: dict[int, float] = defaultdict(float)

    for idx, ent in enumerate(ent_list):
        r = idx // cols
        h = 34 + max(1, len(ent.attributes)) * 22 + 8
        row_heights[r] = max(row_heights[r], h)

    y_cursor = float(pad_y)
    for r in sorted(row_heights.keys()):
        for c in range(cols):
            idx = r * cols + c
            if idx >= len(ent_list):
                break
            ent = ent_list[idx]
            h = 34 + max(1, len(ent.attributes)) * 22 + 8
            x = pad_x + c * (box_w + col_gap)
            pos[ent.name] = (x, y_cursor, h)
        y_cursor += row_heights[r] + row_gap

    max_x = max(x + box_w for x, _, _ in pos.values()) + pad_x
    max_y = max(y + h for _, y, h in pos.values()) + pad_y
    svg_w = int(max(360, max_x))
    svg_h = int(max(180, max_y))

    parts: list[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {svg_w} {svg_h}" width="{svg_w}" height="{svg_h}" '
        f'style="background:#ffffff;font-family:Segoe UI,system-ui,sans-serif;">'
    ]

    # Draw relationship lines first
    for rel in relations:
        if rel.left not in pos or rel.right not in pos:
            continue
        x1, y1, h1 = pos[rel.left]
        x2, y2, h2 = pos[rel.right]
        cx1, cy1 = x1 + box_w / 2, y1 + h1 / 2
        cx2, cy2 = x2 + box_w / 2, y2 + h2 / 2

        if abs(cx2 - cx1) >= abs(cy2 - cy1):
            sx = x1 + box_w if cx2 >= cx1 else x1
            tx = x2 if cx2 >= cx1 else x2 + box_w
            sy, ty = cy1, cy2
        else:
            sx, tx = cx1, cx2
            sy = y1 + h1 if cy2 >= cy1 else y1
            ty = y2 if cy2 >= cy1 else y2 + h2

        dash = "" if rel.identifying else ' stroke-dasharray="5,4"'
        parts.append(
            f'<line x1="{sx:.1f}" y1="{sy:.1f}" x2="{tx:.1f}" y2="{ty:.1f}" '
            f'stroke="#475569" stroke-width="1.6"{dash}/>'
        )
        # Cardinality badges near endpoints
        lx = sx + (tx - sx) * 0.18
        ly = sy + (ty - sy) * 0.18 - 6
        rx = sx + (tx - sx) * 0.82
        ry = sy + (ty - sy) * 0.82 - 6
        parts.append(
            f'<text x="{lx:.1f}" y="{ly:.1f}" text-anchor="middle" font-size="10.5" font-weight="700" fill="#1e40af">'
            f"{_esc(rel.left_card)}</text>"
        )
        parts.append(
            f'<text x="{rx:.1f}" y="{ry:.1f}" text-anchor="middle" font-size="10.5" font-weight="700" fill="#1e40af">'
            f"{_esc(rel.right_card)}</text>"
        )
        if rel.label:
            mx, my = (sx + tx) / 2, (sy + ty) / 2
            bw = max(42, len(rel.label) * 6.5 + 12)
            parts.append(
                f'<rect x="{mx - bw / 2:.1f}" y="{my - 10:.1f}" width="{bw:.1f}" height="16" rx="4" '
                f'fill="#fffbeb" stroke="#fcd34d" stroke-width="0.9"/>'
            )
            parts.append(
                f'<text x="{mx:.1f}" y="{my + 2:.1f}" text-anchor="middle" font-size="10.5" fill="#92400e">'
                f"{_esc(rel.label)}</text>"
            )

    # Draw entity tables
    for ent in ent_list:
        x, y, h = pos[ent.name]
        parts.append(
            f'<rect x="{x:.1f}" y="{y:.1f}" width="{box_w}" height="{h:.1f}" rx="6" '
            f'fill="#ffffff" stroke="#1e3a8a" stroke-width="1.6"/>'
        )
        parts.append(
            f'<rect x="{x:.1f}" y="{y:.1f}" width="{box_w}" height="30" rx="6" fill="#1e3a8a"/>'
        )
        parts.append(
            f'<text x="{x + 12:.1f}" y="{y + 20:.1f}" font-size="12" font-weight="700" fill="#ffffff">'
            f"{_esc(ent.name)}</text>"
        )
        if not ent.attributes:
            parts.append(
                f'<text x="{x + 12:.1f}" y="{y + 50:.1f}" font-size="11" fill="#64748b">(no columns listed)</text>'
            )
        for idx, attr in enumerate(ent.attributes):
            ay = y + 48 + idx * 22
            if attr.key:
                badge_bg = "#fef3c7" if attr.key == "PK" else "#dbeafe"
                badge_fg = "#92400e" if attr.key == "PK" else "#1e40af"
                parts.append(
                    f'<rect x="{x + 8:.1f}" y="{ay - 11:.1f}" width="24" height="15" rx="3" fill="{badge_bg}"/>'
                )
                parts.append(
                    f'<text x="{x + 20:.1f}" y="{ay:.1f}" text-anchor="middle" font-size="9.5" font-weight="700" fill="{badge_fg}">'
                    f"{_esc(attr.key)}</text>"
                )
            parts.append(
                f'<text x="{x + 38:.1f}" y="{ay:.1f}" font-size="11.5" font-weight="600" fill="#0f172a">'
                f"{_esc(attr.name)}</text>"
            )
            parts.append(
                f'<text x="{x + box_w - 10:.1f}" y="{ay:.1f}" text-anchor="end" font-size="11" fill="#475569">'
                f"{_esc(attr.col_type)}</text>"
            )

    parts.append("</svg>")
    return DiagramResult(0, "erDiagram", source, "".join(parts), True, None)


def render_mermaid(source: str, index: int = 0) -> DiagramResult:
    """Render a complete Mermaid diagram source string into a safe standalone SVG."""
    cleaned = (source or "").strip()
    if not cleaned:
        return DiagramResult(index, "unknown", source, None, False, "Empty diagram block")

    first_line = next((ln.strip() for ln in cleaned.splitlines() if ln.strip() and not ln.strip().startswith("%%")), "")
    low = first_line.lower()
    try:
        if low.startswith(("flowchart", "graph")):
            res = _render_flowchart(cleaned)
        elif low.startswith("erdiagram"):
            res = _render_er_diagram(cleaned)
        else:
            return DiagramResult(index, "unknown", cleaned, None, False, f"Unsupported diagram type: {first_line[:30]}")
        res.index = index
        return res
    except Exception as exc:  # noqa: BLE001
        return DiagramResult(index, "unknown", cleaned, None, False, str(exc))


# ---------------------------------------------------------------------------
# Structured Answer Splitter (Spoken / Technical Details / How to draw / Design State)
# ---------------------------------------------------------------------------

_DESIGN_STATE_RE = re.compile(r"```design-state[^\n]*\n(.*?)```", re.S | re.I)
_HEADING_SPLIT_RE = re.compile(
    r"^#{2,3}\s+(Technical details|How to draw it|How to explain it)\s*:?\s*$",
    re.I | re.M,
)
_RAW_HTML_TAG_RE = re.compile(r"</?[A-Za-z!][^>]*>")
_MD_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")
_UNSAFE_MD_LINK_RE = re.compile(r"\[([^\]]+)\]\(\s*(?:javascript|data|vbscript|file):[^)]*\)", re.I)


def sanitize_markdown_for_qt(md: str) -> str:
    """Escape raw HTML tags, remote Markdown images, and unsafe link schemes outside code blocks."""
    if not md:
        return ""
    lines = md.split("\n")
    out: list[str] = []
    in_fence = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("```"):
            in_fence = not in_fence
            out.append(line)
            continue
        if in_fence:
            out.append(line)
            continue
        parts = re.split(r"(`[^`]+`)", line)
        safe_parts: list[str] = []
        for idx, part in enumerate(parts):
            if idx % 2 == 1:
                safe_parts.append(part)
            else:
                s = _RAW_HTML_TAG_RE.sub(lambda m: m.group(0).replace("<", "&lt;").replace(">", "&gt;"), part)
                s = _MD_IMAGE_RE.sub(r"[remote image blocked: \1]", s)
                s = _UNSAFE_MD_LINK_RE.sub(r"\1 (unsafe link blocked)", s)
                safe_parts.append(s)
        out.append("".join(safe_parts))
    return "\n".join(out)


def parse_structured_answer(raw_text: str, is_complete: bool = True) -> StructuredAnswer:
    """Splits a raw or streaming answer into spoken answer, technical details, how-to-draw,
    completed diagrams, and hidden design-state metadata.
    """
    text = raw_text or ""

    # 1. Extract and strip closed design-state JSON block
    design_state: dict[str, Any] | None = None
    dm = _DESIGN_STATE_RE.search(text)
    if dm:
        try:
            design_state = json.loads(dm.group(1).strip())
        except Exception:
            design_state = None
        text = _DESIGN_STATE_RE.sub("", text)

    # Also hide an in-flight unclosed ```design-state block during streaming
    open_ds = re.search(r"```design-state\b.*$", text, re.S | re.I)
    if open_ds:
        text = text[: open_ds.start()]

    # 2. Extract closed ```mermaid ... ``` blocks and replace unclosed ones with a safe placeholder
    diagrams: list[DiagramResult] = []
    diag_idx = 0

    def _replace_closed_mermaid(match: re.Match) -> str:
        nonlocal diag_idx
        m_src = match.group(1)
        res = render_mermaid(m_src, index=diag_idx)
        diagrams.append(res)
        placeholder = f"\n\n[[DIAGRAM_{diag_idx}]]\n\n"
        diag_idx += 1
        return placeholder

    text_with_placeholders = re.sub(r"```mermaid\s*\n(.*?)```", _replace_closed_mermaid, text, flags=re.S | re.I)

    # Check if there is an unclosed ```mermaid fence at the end during streaming
    pending_diagram = False
    open_m = re.search(r"```mermaid\b.*$", text_with_placeholders, re.S | re.I)
    if open_m:
        if not is_complete:
            pending_diagram = True
            text_with_placeholders = text_with_placeholders[: open_m.start()] + "\n\n*(Rendering diagram…)*\n\n"
        else:
            # Completed stream but fence was never closed: treat as failed diagram fallback
            raw_unclosed = open_m.group(0).lstrip("`")[7:].strip()
            res = DiagramResult(diag_idx, "unknown", raw_unclosed, None, False, "Unclosed mermaid code block")
            diagrams.append(res)
            text_with_placeholders = text_with_placeholders[: open_m.start()] + f"\n\n[[DIAGRAM_{diag_idx}]]\n\n"
            diag_idx += 1

    # 3. Extract code blocks (excluding mermaid / design-state) for Copy Code
    code_blocks: list[dict[str, str]] = []
    for cm in re.finditer(r"```([A-Za-z0-9_+-]*)\s*\n(.*?)```", text_with_placeholders, flags=re.S):
        lang = cm.group(1).strip().lower()
        if lang not in ("mermaid", "design-state"):
            code_blocks.append({"lang": lang or "text", "language": lang or "text", "code": cm.group(2).rstrip()})

    # 4. Split into Spoken Answer, Technical Details, and How to Draw It
    spoken = text_with_placeholders
    technical = ""
    how_to_draw = ""

    matches = list(_HEADING_SPLIT_RE.finditer(text_with_placeholders))
    if matches:
        spoken = text_with_placeholders[: matches[0].start()].strip()
        for i, m in enumerate(matches):
            sec_name = m.group(1).lower()
            start = m.end()
            end = matches[i + 1].start() if i + 1 < len(matches) else len(text_with_placeholders)
            body = text_with_placeholders[start:end].strip()
            if "explain" in sec_name:
                explain = f"#### How to explain it\n{body}"
                technical = f"{explain}\n\n{technical}".strip() if technical else explain
            elif "technical" in sec_name:
                technical = (technical + "\n\n" + body).strip() if technical else body
            elif "draw" in sec_name:
                how_to_draw = (how_to_draw + "\n\n" + body).strip() if how_to_draw else body
    else:
        spoken = text_with_placeholders.strip()
        if "[[DIAGRAM_" in spoken:
            first_diag = spoken.index("[[DIAGRAM_")
            technical = spoken[first_diag:].strip()
            spoken = spoken[:first_diag].strip()

    # Keep the Teleprompter (Suggested answer) free of large fenced code blocks when code_blocks are extracted
    if code_blocks and "```" in spoken:
        spoken_without_fences = re.sub(r"```[A-Za-z0-9_+-]*\s*\n.*?```", "", spoken, flags=re.S).strip()
        if spoken_without_fences:
            spoken = spoken_without_fences

    # Clean spoken text for "Copy spoken answer" (strip any diagram placeholders and markdown formatting)
    spoken_clean = re.sub(r"\[\[DIAGRAM_\d+\]\]", "", spoken)
    spoken_clean = re.sub(r"`([^`]+)`", r"\1", spoken_clean)
    spoken_clean = re.sub(r"\*\*([^*]+)\*\*", r"\1", spoken_clean).strip()

    return StructuredAnswer(
        spoken_markdown=spoken,
        spoken_clean=spoken_clean,
        technical_markdown=technical,
        how_to_draw_markdown=how_to_draw,
        diagrams=diagrams,
        pending_diagram=pending_diagram,
        code_blocks=code_blocks,
        design_state=design_state,
    )
