import { useEffect, useRef, useState } from "react";
import type { PublicGameEvent } from "./api";
import type { ProjectedView } from "./protocol";
import { publicEventSummary, summarizeTableActivity } from "./tablePresentation";

export function projectedPublicEvents(view: ProjectedView): PublicGameEvent[] {
  return view.events.flatMap((row) => row.pub === true && typeof row.id === "number" && typeof row.c === "string"
    ? [{ id: row.id, code: row.c, actor: typeof row.a === "string" ? row.a : null,
        summary: typeof row.s === "string" ? row.s : "", importance: 1,
        cards: Array.isArray(row.cards) ? row.cards : undefined }]
    : []);
}

function animateLibraryActivity(ownSeat: string, drew: boolean, milled: boolean) {
  if ((!drew && !milled) || window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
  const library = document.querySelector(`[data-target-zone="${ownSeat}-known_top"]`);
  if (!library) return;
  if (milled) library.animate([
    { boxShadow: "0 0 0 2px var(--accent)" },
    { boxShadow: "0 0 0 7px transparent" },
  ], { duration: 550, easing: "ease-out" });
  if (!drew) return;
  const from = library.getBoundingClientRect();
  const to = document.querySelector("[data-testid=own-hand], [data-testid=hand-panel-collapsed]")?.getBoundingClientRect();
  if (!from?.width || !to?.width) return;
  const fromX = from.left + from.width / 2;
  const fromY = from.top + from.height / 2;
  const card = document.createElement("span");
  card.className = "card-transfer-animation";
  card.setAttribute("aria-hidden", "true");
  card.style.left = `${fromX - 14}px`;
  card.style.top = `${fromY - 20}px`;
  document.body.append(card);
  const animation = card.animate([
    { transform: "translate(0, 0)", opacity: 1 },
    { transform: `translate(${to.left + to.width / 2 - fromX}px, ${to.top + to.height / 2 - fromY}px)`, opacity: 0 },
  ], { duration: 420, easing: "ease-out" });
  animation.finished.then(() => card.remove(), () => card.remove());
}

export function TableMotion({ view, ownSeat }: { view: ProjectedView; ownSeat: string }) {
  const cursor = useRef<number | null>(null);
  const [notice, setNotice] = useState<{ id: number; text: string }>();
  const [ownNotice, setOwnNotice] = useState<{ id: number; text: string }>();
  useEffect(() => {
    const recent = projectedPublicEvents(view);
    const latest = Math.max(cursor.current ?? 0, ...recent.map((event) => event.id));
    const previous = cursor.current;
    cursor.current = latest;
    if (previous === null || latest <= previous) return;
    const activity = summarizeTableActivity(recent.filter((event) => event.id > previous), ownSeat);
    if (activity.ownText) {
      setOwnNotice({ id: latest, text: activity.ownText });
      animateLibraryActivity(ownSeat, activity.drew, activity.milled);
    }
    const last = activity.latestPublic;
    if (last) {
      const summary = publicEventSummary(last);
      setNotice({ id: last.id, text: last.code === "stack.cast" && last.actor
        ? `Seat ${last.actor} ${summary.charAt(0).toLowerCase()}${summary.slice(1)}` : summary });
    }
  }, [ownSeat, view]);
  useEffect(() => {
    if (!notice) return;
    const timer = window.setTimeout(() => setNotice(undefined), 3200);
    return () => window.clearTimeout(timer);
  }, [notice]);
  useEffect(() => {
    if (!ownNotice) return;
    const timer = window.setTimeout(() => setOwnNotice(undefined), 3200);
    return () => window.clearTimeout(timer);
  }, [ownNotice]);
  return <div className="table-move-notice" aria-live="polite">
    {ownNotice && <span key={`own-${ownNotice.id}`}>{ownNotice.text}</span>}
    {notice && <span key={notice.id}>{notice.text}</span>}
  </div>;
}
