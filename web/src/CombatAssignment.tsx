import { useRef, useState } from "react";
import { asList, asRecord, cardName, CardTile } from "./CardSurface";
import { projectedChoiceCards } from "./CardChoiceGrid";
import { list, record, type ChoiceField } from "./choices";
import type { JsonValue } from "./generated/protocol";
import type { ProjectedView } from "./protocol";

const dragType = "application/x-quorune-combat-source";

export function CombatAssignment({
  field, value, onValue, view, labelFor,
}: {
  field: ChoiceField;
  value: JsonValue | undefined;
  onValue: (value: JsonValue) => void;
  view: ProjectedView;
  labelFor: (ref: string) => string;
}) {
  const [source, setSource] = useState("");
  const dragSource = useRef("");
  const selected = record(value);
  const players = asRecord(view.state.players);
  const cards = projectedChoiceCards(view);
  const isBlock = field.name === "blocks";
  const rows = list(field.rows).map(record).map((row) => ({
    ref: String(row.value),
    options: list(row.options).map((option) => String(record(option).value)),
    capacity: Number(row.maximum ?? 1),
  }));
  const destinations = isBlock
    ? asList(view.decision?.ctx.attackers).map(String)
    : [...new Set(rows.flatMap((row) => row.options))];
  const minimumGroups = record(field.minimum_group_sizes);
  const current = rows.find((row) => row.ref === source);
  const targetsOf = (ref: string): string[] => Array.isArray(selected[ref])
    ? list(selected[ref]).map(String) : selected[ref] ? [String(selected[ref])] : [];
  const unassigned = rows.filter((row) => targetsOf(row.ref).length === 0).length;
  const cardLabel = (ref: string) => cards.has(ref) ? cardName(cards.get(ref)!) : labelFor(ref);
  const targetLabel = (ref: string) => players[ref]
    ? `Seat ${ref}, ${asRecord(players[ref]).life} life` : cardLabel(ref);

  function assign(ref: string, target: string) {
    const row = rows.find((candidate) => candidate.ref === ref);
    if (!row?.options.includes(target)) return;
    const existing = targetsOf(ref);
    const next = { ...selected };
    if (existing.includes(target)) {
      const retained = existing.filter((entry) => entry !== target);
      if (retained.length > 0) next[ref] = retained;
      else delete next[ref];
    } else if (row.capacity === 1) {
      next[ref] = target;
    } else if (existing.length < row.capacity) {
      next[ref] = [...existing, target];
    } else {
      return;
    }
    onValue(next);
    setSource("");
  }

  return <div className="combat-assignment">
    <div className="combat-picker-header">
      <strong>{unassigned} of {rows.length} {isBlock ? "blockers" : "attackers"} unassigned</strong>
      <button type="button" className="link-button" disabled={!Object.keys(selected).length && !source}
        onClick={() => { onValue({}); setSource(""); }}>Clear assignments</button>
    </div>
    <div className="combat-source-bank" aria-label={isBlock ? "Eligible blockers" : "Eligible attackers"}>
      {rows.map((row) => {
        const card = cards.get(row.ref);
        const assigned = targetsOf(row.ref).length;
        return <button type="button" key={row.ref} className="combat-source" aria-pressed={source === row.ref}
          data-testid={`combat-source-${row.ref}`} aria-label={`Select ${cardLabel(row.ref)}`}
          draggable={row.options.length > 0}
          onDragStart={(event) => {
            dragSource.current = row.ref;
            event.dataTransfer.effectAllowed = "move";
            event.dataTransfer.setData(dragType, row.ref);
            setSource(row.ref);
          }}
          onDragEnd={() => { dragSource.current = ""; }}
          onClick={() => setSource(source === row.ref ? "" : row.ref)}>
          {card ? <CardTile value={card} view={view} compact /> : <strong>{cardLabel(row.ref)}</strong>}
          <small>{assigned ? `${assigned} assigned` : row.options.length ? "Unassigned" : "No legal assignment"}</small>
        </button>;
      })}
    </div>
    <div className="combat-lanes" aria-label={isBlock ? "Incoming attackers" : "Defenders"}>
      {destinations.map((target) => {
        const card = cards.get(target);
        const attackerSeat = isBlock ? Object.entries(players).find(([, player]) =>
          asList(asRecord(player).bf).some((object) => asRecord(object).id === target))?.[0] : undefined;
        const legal = current?.options.includes(target) ?? false;
        const attached = rows.filter((row) => targetsOf(row.ref).includes(target));
        const minimum = Number(minimumGroups[target] ?? 0);
        return <section className={`combat-lane${legal ? " legal-destination" : ""}`} key={target}
          onDragOver={(event) => {
            if (rows.some((row) => row.ref === dragSource.current && row.options.includes(target))) {
              event.preventDefault();
              event.dataTransfer.dropEffect = "move";
            }
          }}
          onDrop={(event) => {
            const dragged = event.dataTransfer.getData(dragType);
            if (!dragged) return;
            event.preventDefault();
            assign(dragged, target);
          }}>
          <button type="button" className="combat-destination" data-testid={`combat-destination-${target}`}
            aria-label={`Assign to ${targetLabel(target)}`} aria-disabled={!legal}
            onClick={() => assign(source, target)}>
            {card ? <CardTile value={card} view={view} compact /> : <>
              <span className="seat-letter small">{target}</span><strong>{targetLabel(target)}</strong>
            </>}
          </button>
          {attackerSeat && <small>Attacking from Seat {attackerSeat}</small>}
          {minimum > 1 && <small>Block with at least {minimum}, or leave unblocked</small>}
          {attached.length > 0 ? <ul className="combat-connections">
            {attached.map((row) =>
              <li key={row.ref}><button type="button" className="link-button"
                aria-label={`Remove ${cardLabel(row.ref)} from ${targetLabel(target)}`}
                onClick={() => assign(row.ref, target)}>{cardLabel(row.ref)} ×</button></li>,
            )}
          </ul> : <small>{isBlock ? "No blockers assigned" : "No attackers assigned"}</small>}
        </section>;
      })}
    </div>
  </div>;
}
