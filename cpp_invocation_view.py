#!/usr/bin/env python3
"""Interactive viewer for the Mermaid graph in cpp_invo_doc.md."""

from __future__ import annotations

import argparse
import html
import json
import math
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
    x: float = 0
    y: float = 0

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
    node_ids = sorted(nodes)
    if not node_ids:
        return

    index = {node_id: offset for offset, node_id in enumerate(node_ids)}
    positions = initial_spring_positions(node_ids)
    velocities = {node_id: [0.0, 0.0] for node_id in node_ids}
    edge_pairs = [(edge.source, edge.target) for edge in edges]

    spring_length = 280.0
    spring_strength = 0.018
    repulsion_strength = 210_000.0
    center_strength = 0.002
    damping = 0.82

    for step in range(520):
        forces = {node_id: [0.0, 0.0] for node_id in node_ids}
        temperature = 1.0 - step / 520

        for offset, source in enumerate(node_ids):
            sx, sy = positions[source]
            for target in node_ids[offset + 1 :]:
                tx, ty = positions[target]
                dx = tx - sx
                dy = ty - sy
                distance_squared = max(dx * dx + dy * dy, 0.01)
                distance = math.sqrt(distance_squared)
                force = repulsion_strength / distance_squared
                fx = force * dx / distance
                fy = force * dy / distance
                forces[source][0] -= fx
                forces[source][1] -= fy
                forces[target][0] += fx
                forces[target][1] += fy

        for source, target in edge_pairs:
            sx, sy = positions[source]
            tx, ty = positions[target]
            dx = tx - sx
            dy = ty - sy
            distance = max(math.hypot(dx, dy), 0.01)
            force = spring_strength * (distance - spring_length)
            fx = force * dx / distance
            fy = force * dy / distance
            forces[source][0] += fx
            forces[source][1] += fy
            forces[target][0] -= fx
            forces[target][1] -= fy

        for node_id in node_ids:
            x, y = positions[node_id]
            rank_pull = (index[node_id] - len(node_ids) / 2) * 3.0
            forces[node_id][0] += -x * center_strength + rank_pull * center_strength
            forces[node_id][1] += -y * center_strength

            vx, vy = velocities[node_id]
            vx = (vx + forces[node_id][0]) * damping * temperature
            vy = (vy + forces[node_id][1]) * damping * temperature
            velocities[node_id] = [vx, vy]
            positions[node_id] = [x + vx, y + vy]

    resolve_collisions(node_ids, positions)
    snap_positions_to_grid(node_ids, positions)
    resolve_collisions(node_ids, positions)
    snap_positions_to_grid(node_ids, positions)

    min_x = min(position[0] for position in positions.values())
    min_y = min(position[1] for position in positions.values())

    for node_id, (x, y) in positions.items():
        nodes[node_id].x = round(x - min_x + 80, 2)
        nodes[node_id].y = round(y - min_y + 80, 2)


def initial_spring_positions(node_ids: list[str]) -> dict[str, list[float]]:
    radius = max(420.0, len(node_ids) * 11.0)
    positions: dict[str, list[float]] = {}

    for offset, node_id in enumerate(node_ids):
        angle = 2.0 * math.pi * offset / len(node_ids)
        shell = 1.0 + (offset % 7) * 0.075
        positions[node_id] = [
            math.cos(angle) * radius * shell,
            math.sin(angle) * radius * shell,
        ]

    return positions


def resolve_collisions(node_ids: list[str], positions: dict[str, list[float]]) -> None:
    minimum_x_gap = 260.0
    minimum_y_gap = 86.0

    for _ in range(90):
        moved = False
        for offset, source in enumerate(node_ids):
            sx, sy = positions[source]
            for target in node_ids[offset + 1 :]:
                tx, ty = positions[target]
                dx = tx - sx
                dy = ty - sy
                overlap_x = minimum_x_gap - abs(dx)
                overlap_y = minimum_y_gap - abs(dy)
                if overlap_x <= 0 or overlap_y <= 0:
                    continue

                moved = True
                if overlap_x < overlap_y:
                    push = overlap_x / 2.0
                    direction = 1.0 if dx >= 0 else -1.0
                    positions[source][0] -= push * direction
                    positions[target][0] += push * direction
                else:
                    push = overlap_y / 2.0
                    direction = 1.0 if dy >= 0 else -1.0
                    positions[source][1] -= push * direction
                    positions[target][1] += push * direction

        if not moved:
            return


def snap_positions_to_grid(node_ids: list[str], positions: dict[str, list[float]]) -> None:
    grid_x = 260.0
    grid_y = 92.0
    used: set[tuple[int, int]] = set()

    for node_id in sorted(node_ids, key=lambda current: (positions[current][1], positions[current][0])):
        x, y = positions[node_id]
        column = int(round(x / grid_x))
        row = int(round(y / grid_y))

        if (column, row) in used:
            placed = False
            for radius in range(1, len(node_ids) + 1):
                for row_delta in range(-radius, radius + 1):
                    for column_delta in range(-radius, radius + 1):
                        candidate = (column + column_delta, row + row_delta)
                        if candidate in used:
                            continue
                        column, row = candidate
                        placed = True
                        break
                    if placed:
                        break
                if placed:
                    break

        used.add((column, row))
        positions[node_id] = [column * grid_x, row * grid_y]


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
      --edge: #b8c7d6;
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
      stroke-width: 2.1;
      marker-end: url(#arrow);
      opacity: 0.92;
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
      <marker id="arrow" viewBox="0 0 14 14" refX="12" refY="7"
              markerWidth="10" markerHeight="10" orient="auto-start-reverse">
        <path d="M 0 0 L 14 7 L 0 14 z" fill="#d8e6f3"></path>
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
    const gridX = 260;
    const gridY = 92;
    const routeMargin = 26;
    const portPadding = 8;
    const laneSpacing = 12;
    const corridorSpacing = 10;
    const state = { scale: 0.85, tx: 30, ty: 30 };
    const nodeById = new Map(graph.nodes.map(node => [node.id, node]));
    const routesByEdgeKey = new Map();

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

    function nodeRect(node) {
      return {
        left: node.x,
        right: node.x + nodeWidth,
        top: node.y,
        bottom: node.y + nodeHeight
      };
    }

    function horizontalSegmentHitsNode(y, x1, x2, sourceId, targetId) {
      const left = Math.min(x1, x2);
      const right = Math.max(x1, x2);
      for (const node of graph.nodes) {
        if (node.id === sourceId || node.id === targetId) {
          continue;
        }
        const rect = nodeRect(node);
        if (y > rect.top - routeMargin && y < rect.bottom + routeMargin &&
            right > rect.left - routeMargin && left < rect.right + routeMargin) {
          return true;
        }
      }
      return false;
    }

    function chooseHorizontalLane(source, target, startX, endX, baseY) {
      const candidates = [baseY];
      const lower = Math.max(source.y, target.y) + nodeHeight + routeMargin;
      const upper = Math.min(source.y, target.y) - routeMargin;

      for (let step = 1; step <= 18; step += 1) {
        candidates.push(lower + step * gridY);
        candidates.push(upper - step * gridY);
      }

      for (const candidate of candidates) {
        if (!horizontalSegmentHitsNode(candidate, startX, endX, source.id, target.id)) {
          return candidate;
        }
      }
      return baseY;
    }

    function portOffset(index, total) {
      if (total <= 1) {
        return nodeHeight / 2;
      }
      const usable = nodeHeight - portPadding * 2;
      return portPadding + usable * index / (total - 1);
    }

    function edgeKey(edge) {
      return `${edge.source}->${edge.target}`;
    }

    function distributeOffset(index, spacing) {
      if (index === 0) {
        return 0;
      }
      const rank = Math.floor((index + 1) / 2);
      return (index % 2 === 1 ? 1 : -1) * rank * spacing;
    }

    function reserveTrack(trackUse, key) {
      const index = trackUse.get(key) || 0;
      trackUse.set(key, index + 1);
      return index;
    }

    function buildRoutes() {
      routesByEdgeKey.clear();
      const outgoing = new Map();
      const incoming = new Map();

      for (const edge of graph.edges) {
        if (!outgoing.has(edge.source)) {
          outgoing.set(edge.source, []);
        }
        if (!incoming.has(edge.target)) {
          incoming.set(edge.target, []);
        }
        outgoing.get(edge.source).push(edge);
        incoming.get(edge.target).push(edge);
      }

      for (const edges of outgoing.values()) {
        edges.sort((left, right) => {
          const leftTarget = nodeById.get(left.target);
          const rightTarget = nodeById.get(right.target);
          return leftTarget.y - rightTarget.y || leftTarget.x - rightTarget.x;
        });
      }

      for (const edges of incoming.values()) {
        edges.sort((left, right) => {
          const leftSource = nodeById.get(left.source);
          const rightSource = nodeById.get(right.source);
          return leftSource.y - rightSource.y || leftSource.x - rightSource.x;
        });
      }

      const horizontalTrackUse = new Map();
      const verticalTrackUse = new Map();

      for (const edge of graph.edges) {
        const key = edgeKey(edge);
        const source = nodeById.get(edge.source);
        const target = nodeById.get(edge.target);
        const sourceEdges = outgoing.get(edge.source) || [edge];
        const targetEdges = incoming.get(edge.target) || [edge];
        const sourceIndex = sourceEdges.findIndex(candidate => candidate === edge);
        const targetIndex = targetEdges.findIndex(candidate => candidate === edge);
        const sy = source.y + portOffset(sourceIndex, sourceEdges.length);
        const ty = target.y + portOffset(targetIndex, targetEdges.length);
        const sx = source.x + nodeWidth;
        const tx = target.x;
        const sourceStub = sx + routeMargin + sourceIndex * 4;
        const targetStub = tx - routeMargin - targetIndex * 4;

        let laneY = sy;
        let elbowX = Math.round((sx + tx) / (2 * gridX)) * gridX;
        let direct = false;
        const goingRight = tx >= sx + gridX / 2;

        if (goingRight && !horizontalSegmentHitsNode(sy, sx, tx, source.id, target.id)) {
          if (Math.abs(sy - ty) < 1) {
            const rowKey = `${Math.round(sy / laneSpacing)}:${Math.round(Math.min(sx, tx) / corridorSpacing)}:${Math.round(Math.max(sx, tx) / corridorSpacing)}`;
            const rowIndex = reserveTrack(horizontalTrackUse, rowKey);
            laneY = sy + distributeOffset(rowIndex, corridorSpacing);
            direct = rowIndex === 0;
          } else {
            const elbowKey = `${Math.round(elbowX / corridorSpacing)}:${Math.round(Math.min(sy, ty) / laneSpacing)}:${Math.round(Math.max(sy, ty) / laneSpacing)}`;
            const elbowIndex = reserveTrack(verticalTrackUse, elbowKey);
            elbowX += distributeOffset(elbowIndex, corridorSpacing);
          }
        } else {
          const baseY = Math.round((sy + ty) / (2 * laneSpacing)) * laneSpacing;
          laneY = chooseHorizontalLane(source, target, sourceStub, targetStub, baseY);

          const sourceColumnKey = `${Math.round(sourceStub / corridorSpacing)}:${Math.round(Math.min(sy, laneY) / laneSpacing)}:${Math.round(Math.max(sy, laneY) / laneSpacing)}`;
          const sourceColumnIndex = reserveTrack(verticalTrackUse, sourceColumnKey);
          const sourceColumn = sourceStub + distributeOffset(sourceColumnIndex, corridorSpacing);

          const targetColumnKey = `${Math.round(targetStub / corridorSpacing)}:${Math.round(Math.min(ty, laneY) / laneSpacing)}:${Math.round(Math.max(ty, laneY) / laneSpacing)}`;
          const targetColumnIndex = reserveTrack(verticalTrackUse, targetColumnKey);
          const targetColumn = targetStub + distributeOffset(targetColumnIndex, corridorSpacing);

          const leftSegmentKey = `${Math.round(sy / laneSpacing)}:${Math.round(Math.min(sx, sourceColumn) / corridorSpacing)}:${Math.round(Math.max(sx, sourceColumn) / corridorSpacing)}`;
          const leftSegmentIndex = reserveTrack(horizontalTrackUse, leftSegmentKey);
          const leftY = sy + distributeOffset(leftSegmentIndex, corridorSpacing);

          const centerSegmentKey = `${Math.round(laneY / laneSpacing)}:${Math.round(Math.min(sourceColumn, targetColumn) / corridorSpacing)}:${Math.round(Math.max(sourceColumn, targetColumn) / corridorSpacing)}`;
          const centerSegmentIndex = reserveTrack(horizontalTrackUse, centerSegmentKey);
          const centerY = laneY + distributeOffset(centerSegmentIndex, corridorSpacing);

          const rightSegmentKey = `${Math.round(ty / laneSpacing)}:${Math.round(Math.min(targetColumn, tx) / corridorSpacing)}:${Math.round(Math.max(targetColumn, tx) / corridorSpacing)}`;
          const rightSegmentIndex = reserveTrack(horizontalTrackUse, rightSegmentKey);
          const rightY = ty + distributeOffset(rightSegmentIndex, corridorSpacing);

          routesByEdgeKey.set(key, {
            sx,
            sy,
            tx,
            ty,
            sourceStub: sourceColumn,
            targetStub: targetColumn,
            laneY: centerY,
            leftY,
            rightY,
            elbowX,
            direct
          });
          continue;
        }

        routesByEdgeKey.set(key, {
          sx,
          sy,
          tx,
          ty,
          sourceStub,
          targetStub,
          laneY,
          leftY: sy,
          rightY: ty,
          elbowX,
          direct
        });
      }
    }

    function edgePath(edge) {
      const route = routesByEdgeKey.get(edgeKey(edge));
      if (!route) {
        return "";
      }
      const { sx, sy, tx, ty, sourceStub, targetStub, laneY, leftY, rightY, elbowX, direct } = route;
      if (direct) {
        return `M ${sx} ${sy} L ${tx} ${ty}`;
      }
      if (Math.abs(laneY - sy) < 1 && Math.abs(laneY - ty) < 1) {
        return `M ${sx} ${sy} L ${tx} ${ty}`;
      }
      if (Math.abs(sourceStub - targetStub) < gridX / 3) {
        return [
          `M ${sx} ${sy}`,
          `L ${sourceStub} ${sy}`,
          `L ${sourceStub} ${laneY}`,
          `L ${tx} ${laneY}`,
          `L ${tx} ${ty}`
        ].join(" ");
      }
      if (Math.abs(sy - laneY) < 1 && Math.abs(ty - laneY) >= 1) {
        return `M ${sx} ${sy} L ${elbowX} ${sy} L ${elbowX} ${ty} L ${tx} ${ty}`;
      }
      if (Math.abs(ty - laneY) < 1 && Math.abs(sy - laneY) >= 1) {
        return `M ${sx} ${sy} L ${sourceStub} ${sy} L ${sourceStub} ${ty} L ${tx} ${ty}`;
      }
      return [
        `M ${sx} ${sy}`,
        `L ${sx} ${leftY}`,
        `L ${sourceStub} ${leftY}`,
        `L ${sourceStub} ${laneY}`,
        `L ${targetStub} ${laneY}`,
        `L ${targetStub} ${rightY}`,
        `L ${tx} ${rightY}`,
        `L ${tx} ${ty}`
      ].join(" ");
    }

    function snapNode(node) {
      node.x = Math.round(node.x / gridX) * gridX;
      node.y = Math.round(node.y / gridY) * gridY;
    }

    function updateEdges() {
      buildRoutes();
      for (const path of viewport.querySelectorAll(".edge")) {
        path.setAttribute("d", edgePath(path.__edge));
      }
    }

    function render() {
      buildRoutes();
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
            snapNode(node);
            group.setAttribute("transform", `translate(${node.x} ${node.y})`);
            updateEdges();
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
