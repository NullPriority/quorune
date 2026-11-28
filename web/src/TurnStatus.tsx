import { useState, type ReactNode } from "react";

import { PRIORITY_STOPS, type PriorityStop } from "./tableAutomation";
import { tableSeat } from "./SeatLabel";
import {
  presentTurn,
  type MacroPhase,
  type TurnPresentationInput,
} from "./tablePresentation";

const combatRailSteps = [
  { id: "beginning_combat", label: "Start combat" },
  { id: "declare_attackers", label: "Attackers" },
  { id: "declare_blockers", label: "Blockers" },
  { id: "first_strike_damage", label: "First-strike damage" },
  { id: "combat_damage", label: "Damage" },
  { id: "end_combat", label: "End combat" },
] as const;

export function PriorityStopOptions({ selected, onToggle, phase, seatScoped }: {
  selected: readonly PriorityStop[];
  onToggle: (stop: PriorityStop) => void;
  phase?: MacroPhase;
  seatScoped?: boolean;
}) {
  const options = PRIORITY_STOPS.filter((stop) => (!phase || stop.phase === phase) && (!seatScoped || stop.id !== "before_my_turn"));
  const sorted = phase ? options : [
    ...options.filter(({ id }) => id === "before_my_turn"),
    ...options.filter(({ id }) => id !== "before_my_turn"),
  ];
  return (
    <div className="combat-stop-options" role="group" aria-label="Priority steps">
      {sorted.map(({ id, label }) => (
        <button key={id} type="button" className={!phase && id === "before_my_turn" ? "common-priority-stop" : undefined}
          aria-pressed={selected.includes(id)} onClick={() => onToggle(id)}>{label}</button>
      ))}
    </div>
  );
}

export function TurnStatus({ value, compact, priorityStops, onPriorityStop, holding, onAdvanceToFirstMain, toolbar }: {
  value: TurnPresentationInput;
  compact: boolean;
  priorityStops: readonly PriorityStop[];
  onPriorityStop?: (stop: PriorityStop) => void;
  holding?: string;
  onAdvanceToFirstMain?: () => void;
  toolbar?: ReactNode;
}) {
  const presentation = presentTurn(value);
  const [expanded, setExpanded] = useState<MacroPhase | null>(null);
  const inCombat = value.phase === "combat" && value.turnSequence > 0;
  const combatStep = value.step === "combat_damage" && value.firstStrikeStep && value.combatDamageStep === 1
    ? "first_strike_damage" : value.step;
  const combatSteps = combatRailSteps.filter((step) => step.id !== "first_strike_damage" || value.firstStrikeStep);
  const combatIndex = combatSteps.findIndex((step) => step.id === combatStep);
  const holdAllCombat = priorityStops.includes("combat");
  if (presentation.terminal) {
    return (
      <section className="turn-status terminal" data-testid="turn-status-terminal" aria-live="polite">
        <div><span className="eyebrow">MATCH STATUS</span><strong>{presentation.headline}</strong></div>
        <span>{presentation.priority}. Live turn controls are closed.</span>
        {toolbar}
      </section>
    );
  }
  return (
    <section className={`turn-status${compact ? " compact" : ""}`} data-testid="turn-status" aria-label="Current turn and priority">
      <div className="turn-status-copy">
        <strong data-testid="active-turn-label" data-seat={tableSeat(value.activeSeat)}>{presentation.headline}</strong>
        <span className="priority-holder" data-testid="priority-label" data-seat={tableSeat(value.prioritySeat)}><span aria-hidden="true">◎</span>{presentation.priority}</span>
        <span className="exact-step" data-testid="exact-step-label">{presentation.exactStep}</span>
        {holding && <span className="priority-held" role="status" title={holding}>Hold · {holding}</span>}
      </div>
      <ol className="phase-rail" data-testid="phase-rail" aria-label="Turn phases">
        {presentation.rail.map((entry) => {
          if (entry.id === "combat" && inCombat) return (
            <li key={entry.id} className={`combat-rail${holdAllCombat ? " hold-all-combat" : ""}`}>
              <ol aria-label="Combat steps">
                {combatSteps.map(({ id, label }, index) => {
                  const current = index === combatIndex;
                  const stop = priorityStops.includes(id);
                  const state = current ? "current" : index < combatIndex ? "past" : "future";
                  return <li key={id} className={`${state}${stop ? " has-priority-stop" : ""}`}>
                    <button type="button" aria-current={current ? "step" : undefined}
                      aria-pressed={onPriorityStop ? stop : undefined}
                      tabIndex={onPriorityStop ? undefined : -1}
                      title={onPriorityStop ? `Toggle priority stop for ${label.toLowerCase()}` : undefined}
                      onClick={onPriorityStop ? () => onPriorityStop(id) : undefined}>{label}</button>
                  </li>;
                })}
              </ol>
            </li>
          );
          const options = PRIORITY_STOPS.filter((stop) => stop.phase === entry.id);
          const selected = options.some((stop) => priorityStops.includes(stop.id));
          return <li key={entry.id} className={`${entry.state}${selected ? " has-priority-stop" : ""}`}>
            <button
              type="button"
              aria-current={entry.state === "current" ? "step" : undefined}
              aria-expanded={expanded === entry.id}
              title={options.length > 0 && onPriorityStop ? `Set ${entry.label} priority holds` : undefined}
              onClick={() => setExpanded((current) => current === entry.id ? null : entry.id)}
            >
              <span className="phase-marker" aria-hidden="true">{entry.state === "past" ? "✓" : entry.state === "current" ? "●" : "○"}</span>
              {entry.label}
              {selected && <span className="phase-stop-indicator" role="img" aria-label="Priority hold selected">●</span>}
            </button>
            {expanded === entry.id && (options.length > 0 && onPriorityStop
              ? <div className="phase-detail phase-stop-menu">
                {entry.id === "main1" && onAdvanceToFirstMain && <button type="button" className="secondary-button"
                  onClick={() => { onAdvanceToFirstMain(); setExpanded(null); }}>Advance to first main</button>}
                <PriorityStopOptions selected={priorityStops} onToggle={onPriorityStop} phase={entry.id} />
              </div>
              : <span className="phase-detail" role="status">
                  {entry.state === "current" ? presentation.exactStep : entry.state === "past" ? "Completed this turn" : "Upcoming this turn"}
                </span>)}
          </li>;
        })}
      </ol>
      {toolbar}
    </section>
  );
}
