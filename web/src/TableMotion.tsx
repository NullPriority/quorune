import { useEffect, useRef, useState } from "react";
import type { PublicGameEvent } from "./api";
import type { ProjectedView } from "./protocol";
import { publicEventSummary } from "./tablePresentation";

export function projectedPublicEvents(view: ProjectedView): PublicGameEvent[] {
  return view.events.flatMap((row) => row.pub === true && typeof row.id === "number" && typeof row.c === "string"
    ? [{ id: row.id, code: row.c, actor: typeof row.a === "string" ? row.a : null,
        summary: typeof row.s === "string" ? row.s : "", importance: 1 }]
    : []);
}

function animateTransfer(ownSeat: string, mill: boolean) {
  if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
  const from = document.querySelector(`[data-target-zone="${ownSeat}-known_top"]`)?.getBoundingClientRect();
  const to = document.querySelector(mill ? `[data-target-zone="${ownSeat}-gy"]` : "[data-testid=own-hand]")?.getBoundingClientRect();
  if (!from?.width || !to?.width) return;
  const card = document.createElement("span");
  card.className = "card-transfer-animation";
  card.setAttribute("aria-hidden", "true");
  card.style.left = `${from.left + (from.width - 28) / 2}px`;
  card.style.top = `${from.top + (from.height - 40) / 2}px`;
  document.body.append(card);
  const animation = card.animate([
    { transform: "translate(0, 0)", opacity: 1 },
    { transform: `translate(${to.left + (to.width - 28) / 2 - parseFloat(card.style.left)}px, ${to.top + (to.height - 40) / 2 - parseFloat(card.style.top)}px)`, opacity: 0 },
  ], { duration: 420, easing: "ease-out" });
  animation.finished.then(() => card.remove(), () => card.remove());
}

export function TableMotion({ view, ownSeat }: { view: ProjectedView; ownSeat: string }) {
  const cursor = useRef<number | null>(null);
  const [notice, setNotice] = useState<{ id: number; text: string }>();
  useEffect(() => {
    const recent = projectedPublicEvents(view);
    const latest = Math.max(cursor.current ?? 0, ...recent.map((event) => event.id));
    const previous = cursor.current;
    cursor.current = latest;
    if (previous === null || latest <= previous) return;
    const activity = recent.filter((event) => event.id > previous
      && ["card.draw", "card.mill", "stack.cast"].includes(event.code));
    const last = activity.at(-1);
    if (!last) return;
    const transfer = activity.filter((event) => (event.code === "card.draw" || event.code === "card.mill")
      && event.summary.startsWith(`${ownSeat} `)).at(-1);
    if (transfer && ownSeat) animateTransfer(ownSeat, transfer.code === "card.mill");
    const summary = publicEventSummary(last);
    setNotice({ id: last.id, text: last.code === "stack.cast" ? `Seat ${last.actor} ${summary.charAt(0).toLowerCase()}${summary.slice(1)}` : summary });
  }, [ownSeat, view]);
  useEffect(() => {
    if (!notice) return;
    const timer = window.setTimeout(() => setNotice(undefined), 3200);
    return () => window.clearTimeout(timer);
  }, [notice]);
  return <div className="table-move-notice" aria-live="polite">{notice && <span key={notice.id}>{notice.text}</span>}</div>;
}
