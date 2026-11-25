import { useLayoutEffect, useRef } from "react";
import { createPortal } from "react-dom";
import { asList, asRecord } from "./CardSurface";
import type { ProjectedView } from "./protocol";

type Point = { x: number; y: number };
type Bounds = { left: number; top: number; right: number; bottom: number };

export function targetZones(view: ProjectedView): Map<string, string> {
  const result = new Map<string, string>();
  for (const [seat, player] of Object.entries(asRecord(view.state.players))) {
    for (const [zone, rows] of Object.entries(asRecord(player))) {
      if (!["gy", "ex", "cmd", "known_top"].includes(zone)) continue;
      for (const item of asList(rows)) {
        const ref = asRecord(item).id;
        if (typeof ref === "string") result.set(ref, `${seat}-${zone}`);
      }
    }
  }
  return result;
}

function visibleBounds(node: Element): Bounds | null {
  if (getComputedStyle(node).visibility !== "visible") return null;
  const box = node.getBoundingClientRect();
  let left = Math.max(0, box.left);
  let right = Math.min(window.innerWidth, box.right);
  let top = Math.max(0, box.top);
  let bottom = Math.min(window.innerHeight, box.bottom);
  for (let parent = node.parentElement; parent && parent !== document.body; parent = parent.parentElement) {
    const style = getComputedStyle(parent);
    if (style.overflowX !== "visible") {
      const clip = parent.getBoundingClientRect();
      left = Math.max(left, clip.left);
      right = Math.min(right, clip.right);
    }
    if (style.overflowY !== "visible") {
      const clip = parent.getBoundingClientRect();
      top = Math.max(top, clip.top);
      bottom = Math.min(bottom, clip.bottom);
    }
  }
  return right > left && bottom > top ? { left, right, top, bottom } : null;
}

function anchor(ref: string, fallback?: string, source = false): Bounds | null {
  const id = CSS.escape(ref);
  const selectors = [`[data-card-ref="${id}"]`, `[data-player-ref="${id}"]`];
  if (source) selectors.unshift(`[data-zone-offer][data-card-ref="${id}"].selected-card`);
  if (source) selectors.push(`[data-action-source="${id}"]`);
  if (fallback) selectors.push(`[data-target-zone="${CSS.escape(fallback)}"]`);
  for (const selector of selectors) {
    for (const node of document.querySelectorAll(selector)) {
      const bounds = visibleBounds(node);
      if (bounds) return bounds;
    }
  }
  return null;
}

function center(bounds: Bounds): Point {
  return { x: (bounds.left + bounds.right) / 2, y: (bounds.top + bounds.bottom) / 2 };
}

function edge(bounds: Bounds, towards: Point, padding: number): Point {
  const start = center(bounds);
  const x = towards.x - start.x;
  const y = towards.y - start.y;
  if (!x && !y) return start;
  const scale = Math.min(
    ((bounds.right - bounds.left) / 2 + padding) / Math.abs(x),
    ((bounds.bottom - bounds.top) / 2 + padding) / Math.abs(y),
  );
  return { x: start.x + x * scale, y: start.y + y * scale };
}

function arrow(ctx: CanvasRenderingContext2D, from: Bounds, target: Bounds, aiming: boolean, stackLink = false) {
  const origin = center(from);
  const destination = center(target);
  const adjacent = Math.abs(origin.y - destination.y) < 24 && Math.abs(origin.x - destination.x) < 150;
  const start = stackLink ? { x: from.right + 1, y: origin.y }
    : adjacent ? { x: origin.x, y: from.top - 3 } : edge(from, destination, 3);
  const to = stackLink ? { x: target.right + 1, y: destination.y }
    : adjacent ? { x: destination.x, y: target.top - 3 } : edge(target, origin, 3);
  if (Math.hypot(to.x - start.x, to.y - start.y) < 8) return false;
  const bend = adjacent ? 40 : Math.min(66, Math.abs(to.x - start.x) * 0.16 + 12);
  const gutter = Math.min(window.innerWidth - 4, Math.max(start.x, to.x) + 11);
  const endControl = stackLink ? { x: gutter, y: to.y }
    : { x: (start.x + to.x) / 2, y: (start.y + to.y) / 2 - bend };
  ctx.strokeStyle = aiming || stackLink ? "#efd58d" : "#c8f3a8";
  ctx.fillStyle = ctx.strokeStyle;
  ctx.lineWidth = 2.4;
  ctx.lineCap = "round";
  ctx.setLineDash(aiming ? [6, 5] : []);
  ctx.beginPath();
  ctx.moveTo(start.x, start.y);
  if (stackLink) ctx.bezierCurveTo(gutter, start.y, endControl.x, endControl.y, to.x, to.y);
  else ctx.quadraticCurveTo(endControl.x, endControl.y, to.x, to.y);
  ctx.stroke();
  const angle = Math.atan2(to.y - endControl.y, to.x - endControl.x);
  ctx.beginPath();
  ctx.moveTo(to.x, to.y);
  for (const offset of [-0.46, 0.46]) {
    ctx.lineTo(to.x - Math.cos(angle + offset) * 11, to.y - Math.sin(angle + offset) * 11);
  }
  ctx.closePath();
  ctx.fill();
  return true;
}

export function TargetArrowLayer({
  sourceRef, targets, candidates, zones, stackPositions,
}: {
  sourceRef: string;
  targets: readonly string[];
  candidates: readonly string[];
  zones: Map<string, string>;
  stackPositions: ReadonlyMap<string, number>;
}) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const pointer = useRef<Point | null>(null);
  const active = Boolean(sourceRef && (targets.length || candidates.length));

  useLayoutEffect(() => {
    const surface = canvas.current;
    const context = surface?.getContext("2d");
    if (!active || !surface || !context) return;
    const candidateZones = new Set(candidates.map((ref) => zones.get(ref)));
    const selectedZones = new Set(targets.map((ref) => zones.get(ref)));
    let frame = 0;
    const draw = () => {
      frame = 0;
      const scale = window.devicePixelRatio || 1;
      const width = Math.round(window.innerWidth * scale);
      const height = Math.round(window.innerHeight * scale);
      if (surface.width !== width || surface.height !== height) {
        surface.width = width;
        surface.height = height;
      }
      context.setTransform(scale, 0, 0, scale, 0, 0);
      context.clearRect(0, 0, window.innerWidth, window.innerHeight);
      const source = anchor(sourceRef, zones.get(sourceRef), true);
      let count = 0;
      if (source) {
        for (const target of new Set(targets)) {
          const bounds = anchor(target, zones.get(target));
          const stackLink = stackPositions.has(sourceRef) && stackPositions.has(target);
          if (bounds && arrow(context, source, bounds, false, stackLink)) count++;
        }
        const point = pointer.current;
        const focused = document.activeElement;
        const hovered = point && document.elementFromPoint(point.x, point.y);
        const pointedAt = hovered ?? focused;
        const candidate = pointedAt?.closest("[data-card-ref], [data-player-ref]");
        const aimedAt = candidate?.getAttribute("data-card-ref") ?? candidate?.getAttribute("data-player-ref") ?? "";
        const zone = pointedAt?.closest("[data-target-zone]");
        const zoneId = zone?.getAttribute("data-target-zone") ?? "";
        if (candidates.includes(aimedAt) && !targets.includes(aimedAt)) {
          const bounds = anchor(aimedAt, zones.get(aimedAt));
          if (bounds && arrow(context, source, bounds, true)) count++;
        } else if (zone && candidateZones.has(zoneId) && !selectedZones.has(zoneId)) {
          const bounds = visibleBounds(zone);
          if (bounds && arrow(context, source, bounds, true)) count++;
        }
      }
      surface.dataset.arrowCount = String(count);
    };
    function schedule() {
      if (!frame) frame = requestAnimationFrame(draw);
    }
    function move(event: PointerEvent) {
      pointer.current = { x: event.clientX, y: event.clientY };
      schedule();
    }
    function focus() {
      pointer.current = null;
      schedule();
    }
    draw();
    if (candidates.length) {
      window.addEventListener("pointermove", move, { passive: true });
      window.addEventListener("focusin", focus);
    }
    window.addEventListener("resize", schedule);
    window.addEventListener("scroll", schedule, true);
    const table = document.querySelector(".table-workspace");
    const pageChanges = new MutationObserver(schedule);
    if (table) pageChanges.observe(table, { childList: true, subtree: true, attributes: true, attributeFilter: ["aria-hidden", "hidden"] });
    return () => {
      cancelAnimationFrame(frame);
      pageChanges.disconnect();
      window.removeEventListener("pointermove", move);
      window.removeEventListener("focusin", focus);
      window.removeEventListener("resize", schedule);
      window.removeEventListener("scroll", schedule, true);
    };
  }, [active, candidates, sourceRef, targets, zones, stackPositions]);

  return active ? createPortal(<canvas ref={canvas} className="target-arrow-layer"
    aria-hidden="true" data-testid="target-arrow-layer" />, document.body) : null;
}
