import type { PublicGameEvent } from "./api";
import { SeatLabel, tableSeat } from "./SeatLabel";
import { asRecord, cardName } from "./CardSurface";
import type { JsonValue } from "./generated/protocol";
import { useMemo, useState } from "react";

const lowDetailEvents = new Set(["priority.pass", "combat.remove", "combat.damage.assigned"]);

function showActivity(event: PublicGameEvent, events: PublicGameEvent[]): boolean {
  if (lowDetailEvents.has(event.code) || event.code.startsWith("decision.")) return false;
  // A cast from a keyword is already represented by its corresponding cast
  // entry. Keep the keyword's own outcome when it did not cast anything.
  if (["suspend.cast.resolve", "cascade.resolve"].includes(event.code) && event.summary.startsWith("Cast ")) return false;
  if (event.code === "stack.trigger" && event.summary.endsWith(" — cast from Suspend.")) return false;
  if (event.code !== "state.creatures_died" || !event.cards?.length || event.summary.includes("more permanents")) return true;
  const destroyed = events.filter((row) => row.code === "permanent.destroyed"
    && row.id < event.id && event.id - row.id <= 8).map((row) => asRecord(row.cards?.[0]).id);
  return !event.cards.every((card) => destroyed.includes(asRecord(card).id));
}

export function RecentActivity({ events, cards, describeEvent, onInspect, onOpenLog, collapsed, onToggle }: {
  events: PublicGameEvent[];
  cards: ReadonlyMap<string, JsonValue>;
  describeEvent: (event: PublicGameEvent) => string;
  onInspect: (card: JsonValue, wasPubliclyReferenced: boolean) => void;
  onOpenLog: () => void;
  collapsed: boolean;
  onToggle: () => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const [byName, names] = useMemo(() => {
    const found = new Map<string, JsonValue>();
    for (const card of cards.values()) {
      const name = cardName(card);
      if (name.length >= 3 && !found.has(name)) found.set(name, card);
    }
    return [found, [...found.keys()].sort((a, b) => b.length - a.length)] as const;
  }, [cards]);
  const visibleEvents = useMemo(() => events.filter((event) => showActivity(event, events)), [events]);
  function describe(event: PublicGameEvent) {
    const summary = describeEvent(event);
    const actor = tableSeat(event.actor);
    const speaker = actor ? `Seat ${actor} ` : "";
    const text = speaker && summary.startsWith(speaker) ? summary.slice(speaker.length) : summary;
    const matches: { index: number; name: string; card: JsonValue }[] = [];
    const eventCards = new Map((event.cards ?? []).map((card) => [cardName(card), card]));
    const candidates = [...new Set([...eventCards.keys(), ...names])].sort((a, b) => b.length - a.length);
    for (const name of candidates) {
      const card = eventCards.get(name) ?? byName.get(name);
      if (!card) continue;
      let index = text.indexOf(name);
      while (index >= 0) {
        const end = index + name.length;
        if (!/[\p{L}\p{N}]/u.test(text[index - 1] ?? "") && !/[\p{L}\p{N}]/u.test(text[end] ?? "")
          && !matches.some((match) => index < match.index + match.name.length && match.index < end)) {
          matches.push({ index, name, card });
        }
        index = text.indexOf(name, end);
      }
    }
    matches.sort((a, b) => a.index - b.index);
    const parts = [];
    let cursor = 0;
    for (const { index, name, card } of matches) {
      parts.push(text.slice(cursor, index));
      const inspect = () => onInspect(card, eventCards.has(name));
      parts.push(<button key={index} type="button" className="activity-card-link"
        onMouseEnter={inspect} onFocus={inspect} onClick={inspect}>{name}</button>);
      cursor = index + name.length;
    }
    parts.push(text.slice(cursor));
    return parts;
  }
  return (
    <section className={`activity-panel${expanded ? " activity-panel-expanded" : ""}`} data-testid="activity-panel">
      <header>
        <button type="button" className="link-button" aria-expanded={!collapsed} onClick={onToggle}>
          {collapsed ? "Show recent activity" : "Collapse recent activity"}
        </button>
        {!collapsed && <button type="button" className="link-button" aria-pressed={expanded}
          onClick={() => setExpanded((value) => !value)}>{expanded ? "Compact" : "Enlarge"}</button>}
        <button type="button" className="link-button" onClick={onOpenLog}>Full log</button>
      </header>
      {!collapsed && <ol aria-label="Recent activity" tabIndex={0}>
        {visibleEvents.slice(-30).reverse().map((event) => <li key={event.id}>
          {tableSeat(event.actor) ? <SeatLabel seat={event.actor} /> : <span>Game</span>}<span>{describe(event)}</span>
        </li>)}
        {!visibleEvents.length && <li className="empty-activity">Game events will appear here.</li>}
      </ol>}
    </section>
  );
}
