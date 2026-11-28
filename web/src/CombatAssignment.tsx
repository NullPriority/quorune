import { useId, useMemo, useRef, useState } from "react";
import { asList, asRecord, cardName, CombatCardFace } from "./CardSurface";
import { projectedChoiceCards } from "./CardChoiceGrid";
import { CardPages } from "./CardPages";
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
  const statId = useId();
  const selected = record(value);
  const players = asRecord(view.state.players);
  const cards = useMemo(() => projectedChoiceCards(view), [view]);
  const isBlock = field.name === "blocks";
  const declaredBlocks = asRecord(asRecord(view.state.combat).blk);
  const rows = useMemo(() => list(field.rows).map(record).map((row) => ({
    ref: String(row.value),
    options: list(row.options).map((option) => String(record(option).value)),
    capacity: Number(row.maximum ?? 1),
  })), [field.rows]);
  const rowsByRef = useMemo(() => new Map(rows.map((row) => [row.ref, row])), [rows]);
  const destinations = isBlock
    ? asList(view.decision?.ctx.attackers).map(String)
    : [...new Set(rows.flatMap((row) => row.options))];
  const minimumGroups = record(field.minimum_group_sizes);
  const current = rowsByRef.get(source);
  const targetsOf = (ref: string): string[] => Array.isArray(selected[ref])
    ? list(selected[ref]).map(String) : selected[ref] ? [String(selected[ref])] : [];
  const unassigned = rows.filter((row) => targetsOf(row.ref).length === 0).length;
  const cardLabel = (ref: string) => cards.has(ref) ? cardName(cards.get(ref)!) : labelFor(ref);
  const targetLabel = (ref: string) => players[ref]
    ? `Seat ${ref}, ${asRecord(players[ref]).life} life` : cardLabel(ref);

  function assign(ref: string, target: string) {
    const row = rowsByRef.get(ref);
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

  function assignRemainingAttackers(target: string) {
    const next = { ...selected };
    for (const row of rows) {
      if (!Object.hasOwn(next, row.ref) && row.options.includes(target)) next[row.ref] = target;
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
    <CardPages items={rows} label={isBlock ? "Eligible blockers" : "Eligible attackers"}
      className="combat-source-bank" trackClassName="combat-source-paged" layout="grid" render={(row) => {
        const card = cards.get(row.ref);
        const assigned = targetsOf(row.ref).length;
        const statsId = statId + "-source-" + row.ref;
        return <button type="button" key={row.ref} className="combat-source" aria-pressed={source === row.ref}
          data-testid={`combat-source-${row.ref}`} aria-label={`Select ${cardLabel(row.ref)}`}
          aria-describedby={card ? statsId : undefined}
          draggable={row.options.length > 0}
          onDragStart={(event) => {
            dragSource.current = row.ref;
            event.dataTransfer.effectAllowed = "move";
            event.dataTransfer.setData(dragType, row.ref);
            setSource(row.ref);
          }}
          onDragEnd={() => { dragSource.current = ""; }}
          onClick={() => setSource(source === row.ref ? "" : row.ref)}>
          {card ? <CombatCardFace value={card} view={view} statsId={statsId} /> : <strong>{cardLabel(row.ref)}</strong>}
          <small className="combat-source-status">{assigned ? `${assigned} assigned` : row.options.length ? "Unassigned" : "No legal assignment"}</small>
        </button>;
      }} />
    <div className="combat-lanes" aria-label={isBlock ? "Incoming attackers" : "Defenders"}>
      {destinations.map((target) => {
        const card = cards.get(target);
        const controller = card ? Object.entries(players).find(([, player]) =>
          asList(asRecord(player).bf).some((object) => asRecord(object).id === target))?.[0] : undefined;
        const protector = asRecord(card).protect;
        const legal = current?.options.includes(target) ?? false;
        const attached = rows.filter((row) => targetsOf(row.ref).includes(target));
        const canAssignMore = !isBlock && rows.some((row) => !Object.hasOwn(selected, row.ref) && row.options.includes(target));
        const minimum = Number(minimumGroups[target] ?? 0);
        const statsId = statId + "-destination-" + target;
        const blocked = attached.length > 0 || Object.hasOwn(declaredBlocks, target);
        return <section className={`combat-lane${legal ? " legal-destination" : ""}`} key={target}
          onDragOver={(event) => {
            if (rowsByRef.get(dragSource.current)?.options.includes(target)) {
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
            aria-label={`Assign to ${targetLabel(target)}`} aria-describedby={card ? statsId : undefined} aria-disabled={!legal}
            onClick={() => assign(source, target)}>
            {card ? <CombatCardFace value={card} view={view} statsId={statsId} /> : <>
              <span className="seat-letter small">{target}</span><strong>{targetLabel(target)}</strong>
            </>}
          </button>
          {isBlock && <small className="attacker-block-status" data-blocked={blocked}>
            {blocked ? "Blocked" : "Unblocked"}{controller && ` · Seat ${controller}`}
          </small>}
          {!isBlock && typeof protector === "string" && <small>Protected by Seat {protector}</small>}
          {!isBlock && !protector && controller && <small>Controlled by Seat {controller}</small>}
          {minimum > 1 && <small>Block with at least {minimum}, or leave unblocked</small>}
          {canAssignMore && <button type="button" className="link-button"
            aria-label={`Assign all available attackers to ${targetLabel(target)}`}
            onClick={() => assignRemainingAttackers(target)}>Assign remaining attackers</button>}
          {attached.length > 0 ? <ul className="combat-connections">
            {attached.slice(0, 12).map((row) =>
              <li key={row.ref}><button type="button" className="link-button"
                aria-label={`Remove ${cardLabel(row.ref)} from ${targetLabel(target)}`}
                onClick={() => assign(row.ref, target)}>{cardLabel(row.ref)} ×</button></li>,
            )}
            {attached.length > 12 && <li><small>+{attached.length - 12} more assigned</small></li>}
          </ul> : <small>{isBlock ? "No blockers assigned" : "No attackers assigned"}</small>}
        </section>;
      })}
    </div>
  </div>;
}
