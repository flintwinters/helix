#!/usr/bin/env python3
"""Interactive viewer for the Mermaid graph in cpp_invo_doc.md."""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

try:
    from flask import Flask, jsonify, render_template_string
except ImportError as exc:  # pragma: no cover - exercised only without Flask.
    raise SystemExit(
        "Flask is required. Install it with: python3 -m pip install flask"
    ) from exc


MERMAID_FENCE_RE = re.compile(r"```mermaid\s*(.*?)```", re.DOTALL)
NODE_RE = re.compile(r'^\s*([A-Za-z_][\w]*)\["(.*)"\]\s*$')
EDGE_RE = re.compile(r"^\s*([A-Za-z_][\w]*)\s*-->\s*([A-Za-z_][\w]*)\s*$")
CLASS_RE = re.compile(r"^\s*class\s+([A-Za-z_][\w,]*)\s+([A-Za-z_][\w]*)\s*;\s*$")


@dataclass
class GraphNode:
    """A Mermaid node with display metadata and graph layout hints."""

    id: str
    title: str
    detail: str = ""
    classes: set[str] = field(default_factory=set)
    x: int = 0
    y: int = 0

    def as_json(self) -> dict[str, object]:
        return {
            "id": self.id,
            "title": self.title,
            "detail": self.detail,
            "classes": sorted(self.classes),
            "x": self.x,
            "y": self.y,
        }


@dataclass(frozen=True)
class GraphEdge:
    source: str
    target: str

    def as_json(self) -> dict[str, str]:
        return {"source": self.source, "target": self.target}


def extract_mermaid(markdown_path: Path) -> str:
    markdown = markdown_path.read_text(encoding="utf-8")
    match = MERMAID_FENCE_RE.search(markdown)
    if not match:
        raise ValueError(f"No Mermaid code fence found in {markdown_path}")
    return match.group(1)


def clean_label(raw_label: str) -> tuple[str, str]:
    parts = re.split(r"<br\s*/?>", raw_label, maxsplit=1, flags=re.IGNORECASE)
    title = html.unescape(parts[0]).strip()
    detail = html.unescape(parts[1]).strip() if len(parts) > 1 else ""
    return title, detail


def parse_mermaid_graph(mermaid: str) -> tuple[list[GraphNode], list[GraphEdge]]:
    nodes: dict[str, GraphNode] = {}
    edges: list[GraphEdge] = []

    for line_number, line in enumerate(mermaid.splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("flowchart ") or stripped.startswith("classDef "):
            continue

        node_match = NODE_RE.match(line)
        if node_match:
            node_id, raw_label = node_match.groups()
            title, detail = clean_label(raw_label)
            nodes[node_id] = GraphNode(id=node_id, title=title, detail=detail)
            continue

        edge_match = EDGE_RE.match(line)
        if edge_match:
            source, target = edge_match.groups()
            nodes.setdefault(source, GraphNode(id=source, title=source))
            nodes.setdefault(target, GraphNode(id=target, title=target))
            edges.append(GraphEdge(source=source, target=target))
            continue

        class_match = CLASS_RE.match(line)
        if class_match:
            node_ids, class_name = class_match.groups()
            for node_id in node_ids.split(","):
                nodes.setdefault(node_id, GraphNode(id=node_id, title=node_id))
                nodes[node_id].classes.add(class_name)
            continue

        raise ValueError(f"Unsupported Mermaid line {line_number}: {line}")

    assign_layout(nodes, edges)
    return list(nodes.values()), edges


def assign_layout(nodes: dict[str, GraphNode], edges: list[GraphEdge]) -> None:
    incoming: dict[str, int] = {node_id: 0 for node_id in nodes}
    outgoing: dict[str, list[str]] = {node_id: [] for node_id in nodes}

    for edge in edges:
        incoming[edge.target] += 1
        outgoing[edge.source].append(edge.target)

    roots = sorted(node_id for node_id, count in incoming.items() if count == 0)
    queue = list(roots or sorted(nodes))
    depth = {node_id: 0 for node_id in queue}

    seen = set(queue)

    while queue:
        node_id = queue.pop(0)
        for target in outgoing[node_id]:
            if target not in seen:
                seen.add(target)
                depth[target] = depth[node_id] + 1
                queue.append(target)

    layers: dict[int, list[str]] = {}
    for node_id in sorted(nodes):
        layers.setdefault(depth.get(node_id, 0), []).append(node_id)

    for layer, node_ids in layers.items():
        for row, node_id in enumerate(node_ids):
            nodes[node_id].x = 80 + layer * 290
            nodes[node_id].y = 80 + row * 86


def graph_payload(markdown_path: Path) -> dict[str, object]:
    nodes, edges = parse_mermaid_graph(extract_mermaid(markdown_path))
    return {
        "nodes": [node.as_json() for node in nodes],
        "edges": [edge.as_json() for edge in edges],
    }


HTML = """
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>C++ Invocation Graph</title>
  <style>
    :root {
      color-scheme: dark;
      --bg: #111418;
      --panel: #1a2027;
      --text: #ecf2f8;
      --muted: #9aa9b7;
      --edge: #53616f;
      --runtime: #ff5c8a;
      --entry: #58a6ff;
      --leaf: #3fb950;
      --recurse: #d29922;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      overflow: hidden;
      background: var(--bg);
      color: var(--text);
      font: 14px/1.4 system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }
    header {
      position: fixed;
      z-index: 2;
      inset: 12px auto auto 12px;
      display: flex;
      gap: 8px;
      align-items: center;
      padding: 8px 10px;
      border: 1px solid #303946;
      border-radius: 6px;
      background: color-mix(in srgb, var(--panel) 92%, transparent);
      box-shadow: 0 12px 32px rgb(0 0 0 / 24%);
    }
    h1 {
      margin: 0 10px 0 0;
      font-size: 14px;
      font-weight: 700;
      letter-spacing: 0;
    }
    button {
      height: 30px;
      border: 1px solid #3b4654;
      border-radius: 5px;
      background: #232b35;
      color: var(--text);
      cursor: pointer;
      font: inherit;
    }
    button:hover { background: #2b3541; }
    .hint { color: var(--muted); font-size: 12px; }
    svg {
      width: 100vw;
      height: 100vh;
      display: block;
      cursor: grab;
      background-image:
        linear-gradient(rgb(255 255 255 / 4%) 1px, transparent 1px),
        linear-gradient(90deg, rgb(255 255 255 / 4%) 1px, transparent 1px);
      background-size: 24px 24px;
    }
    svg.panning { cursor: grabbing; }
    .edge {
      fill: none;
      stroke: var(--edge);
      stroke-width: 1.4;
      marker-end: url(#arrow);
    }
    .node { cursor: move; }
    .node rect {
      width: 230px;
      height: 56px;
      rx: 6px;
      fill: #202833;
      stroke: #3c4654;
      stroke-width: 1.5;
    }
    .node.runtime rect { stroke: var(--runtime); }
    .node.entry rect { stroke: var(--entry); stroke-width: 2.2; }
    .node.leaf rect { stroke: var(--leaf); stroke-width: 2.2; }
    .node.recurse rect { stroke: var(--recurse); stroke-width: 2.2; }
    .node text {
      pointer-events: none;
      fill: var(--text);
    }
    .node .title { font-size: 12px; font-weight: 700; }
    .node .detail { font-size: 11px; fill: var(--muted); }
  </style>
</head>
<body>
  <header>
    <h1>C++ Invocation Graph</h1>
    <button id="zoom-in" type="button">+</button>
    <button id="zoom-out" type="button">-</button>
    <button id="reset" type="button">Reset</button>
    <span class="hint">Drag nodes. Drag background to pan. Wheel to zoom.</span>
  </header>
  <svg id="graph" aria-label="Interactive C++ invocation graph">
    <defs>
      <marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5"
              markerWidth="6" markerHeight="6" orient="auto-start-reverse">
        <path d="M 0 0 L 10 5 L 0 10 z" fill="#53616f"></path>
      </marker>
    </defs>
    <g id="viewport"></g>
  </svg>
  <script>
    const graph = {{ graph_json | safe }};
    const svg = document.querySelector("#graph");
    const viewport = document.querySelector("#viewport");
    const nodeWidth = 230;
    const nodeHeight = 56;
    const state = { scale: 0.85, tx: 30, ty: 30 };
    const nodeById = new Map(graph.nodes.map(node => [node.id, node]));

    function transformPoint(clientX, clientY) {
      const rect = svg.getBoundingClientRect();
      return {
        x: (clientX - rect.left - state.tx) / state.scale,
        y: (clientY - rect.top - state.ty) / state.scale
      };
    }

    function applyTransform() {
      viewport.setAttribute("transform", `translate(${state.tx} ${state.ty}) scale(${state.scale})`);
    }

    function makeSvg(name, attrs = {}) {
      const element = document.createElementNS("http://www.w3.org/2000/svg", name);
      for (const [key, value] of Object.entries(attrs)) {
        element.setAttribute(key, value);
      }
      return element;
    }

    function edgePath(edge) {
      const source = nodeById.get(edge.source);
      const target = nodeById.get(edge.target);
      const sx = source.x + nodeWidth;
      const sy = source.y + nodeHeight / 2;
      const tx = target.x;
      const ty = target.y + nodeHeight / 2;
      const mid = Math.max(40, Math.abs(tx - sx) / 2);
      return `M ${sx} ${sy} C ${sx + mid} ${sy}, ${tx - mid} ${ty}, ${tx} ${ty}`;
    }

    function updateEdges() {
      for (const path of viewport.querySelectorAll(".edge")) {
        path.setAttribute("d", edgePath(path.__edge));
      }
    }

    function render() {
      for (const edge of graph.edges) {
        const path = makeSvg("path", { class: "edge", d: edgePath(edge) });
        path.__edge = edge;
        viewport.append(path);
      }

      for (const node of graph.nodes) {
        const group = makeSvg("g", {
          class: `node ${node.classes.join(" ")}`,
          transform: `translate(${node.x} ${node.y})`
        });
        group.__node = node;
        group.append(makeSvg("rect"));

        const title = makeSvg("text", { class: "title", x: 12, y: 23 });
        title.textContent = node.title;
        group.append(title);

        const detail = makeSvg("text", { class: "detail", x: 12, y: 42 });
        detail.textContent = node.detail;
        group.append(detail);

        group.addEventListener("pointerdown", event => {
          event.stopPropagation();
          group.setPointerCapture(event.pointerId);
          const start = transformPoint(event.clientX, event.clientY);
          const origin = { x: node.x, y: node.y };

          function move(moveEvent) {
            const point = transformPoint(moveEvent.clientX, moveEvent.clientY);
            node.x = origin.x + point.x - start.x;
            node.y = origin.y + point.y - start.y;
            group.setAttribute("transform", `translate(${node.x} ${node.y})`);
            updateEdges();
          }

          function up() {
            group.removeEventListener("pointermove", move);
            group.removeEventListener("pointerup", up);
            group.removeEventListener("pointercancel", up);
          }

          group.addEventListener("pointermove", move);
          group.addEventListener("pointerup", up);
          group.addEventListener("pointercancel", up);
        });

        viewport.append(group);
      }
      applyTransform();
    }

    svg.addEventListener("pointerdown", event => {
      svg.classList.add("panning");
      svg.setPointerCapture(event.pointerId);
      const start = { x: event.clientX, y: event.clientY, tx: state.tx, ty: state.ty };

      function move(moveEvent) {
        state.tx = start.tx + moveEvent.clientX - start.x;
        state.ty = start.ty + moveEvent.clientY - start.y;
        applyTransform();
      }

      function up() {
        svg.classList.remove("panning");
        svg.removeEventListener("pointermove", move);
        svg.removeEventListener("pointerup", up);
        svg.removeEventListener("pointercancel", up);
      }

      svg.addEventListener("pointermove", move);
      svg.addEventListener("pointerup", up);
      svg.addEventListener("pointercancel", up);
    });

    svg.addEventListener("wheel", event => {
      event.preventDefault();
      const before = transformPoint(event.clientX, event.clientY);
      const factor = event.deltaY < 0 ? 1.12 : 0.88;
      state.scale = Math.min(2.5, Math.max(0.18, state.scale * factor));
      const rect = svg.getBoundingClientRect();
      state.tx = event.clientX - rect.left - before.x * state.scale;
      state.ty = event.clientY - rect.top - before.y * state.scale;
      applyTransform();
    }, { passive: false });

    function zoomBy(factor) {
      const rect = svg.getBoundingClientRect();
      const center = transformPoint(rect.left + rect.width / 2, rect.top + rect.height / 2);
      state.scale = Math.min(2.5, Math.max(0.18, state.scale * factor));
      state.tx = rect.width / 2 - center.x * state.scale;
      state.ty = rect.height / 2 - center.y * state.scale;
      applyTransform();
    }

    document.querySelector("#zoom-in").addEventListener("click", () => zoomBy(1.18));
    document.querySelector("#zoom-out").addEventListener("click", () => zoomBy(0.82));
    document.querySelector("#reset").addEventListener("click", () => {
      state.scale = 0.85;
      state.tx = 30;
      state.ty = 30;
      applyTransform();
    });

    render();
  </script>
</body>
</html>
"""


def create_app(markdown_path: Path) -> Flask:
    app = Flask(__name__)

    @app.get("/")
    def index() -> str:
        payload = graph_payload(markdown_path)
        return render_template_string(HTML, graph_json=json.dumps(payload))

    @app.get("/data")
    def data():
        return jsonify(graph_payload(markdown_path))

    return app


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Serve an interactive view of the Mermaid graph in cpp_invo_doc.md."
    )
    parser.add_argument(
        "markdown",
        nargs="?",
        default="cpp_invo_doc.md",
        type=Path,
        help="Markdown file containing a Mermaid code fence.",
    )
    parser.add_argument("--host", default="127.0.0.1", help="Flask host.")
    parser.add_argument("--port", default=5000, type=int, help="Flask port.")
    parser.add_argument("--debug", action="store_true", help="Enable Flask debug mode.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    markdown_path = args.markdown.resolve()
    if not markdown_path.exists():
        print(f"Missing markdown file: {markdown_path}", file=sys.stderr)
        return 1

    app = create_app(markdown_path)
    app.run(host=args.host, port=args.port, debug=args.debug)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
