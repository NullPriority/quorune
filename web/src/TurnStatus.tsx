import { useState } from "react";

import { PRIORITY_STOPS, type PriorityStop } from "./tableAutomation";
import { tableSeat } from "./SeatLabel";
import {
  presentTurn,
  type MacroPhase,
  type TurnPresentationInput,
} from "./tablePresentation";


export function PriorityStopOptions({ selected, onToggle, phase }: {
  selected: readonly PriorityStop[];
  onToggle: (stop: PriorityStop) => void;
  phase?: MacroPhase;
}) {
  return (
    <div className="combat-stop-options" role="group" aria-label="Priority steps">
      {PRIORITY_STOPS.filter((stop) => !phase || stop.phase === phase).map(({ id, label }) => (
        <button key={id} type="button" aria-pressed={selected.includes(id)} onClick={() => onToggle(id)}>{label}</button>
      ))}
    </div>
  );
}

export function TurnStatus({ value, compact, priorityStops, onPriorityStop, holding }: {
  value: TurnPresentationInput;
  compact: boolean;
  priorityStops: readonly PriorityStop[];
  onPriorityStop?: (stop: PriorityStop) => void;
  holding?: string;
}) {
  const presentation = presentTurn(value);
  const [expanded, setExpanded] = useState<MacroPhase | null>(null);
  if (presentation.terminal) {
    return (
      <section className="turn-status terminal" data-testid="turn-status-terminal" aria-live="polite">
        <div><span className="eyebrow">MATCH STATUS</span><strong>{presentation.headline}</strong></div>
        <span>{presentation.priority}. Live turn controls are closed.</span>
      </section>
    );
  }
  return (
    <section className={`turn-status${compact ? " compact" : ""}`} data-testid="turn-status" aria-label="Current turn and priority">
      <div className="turn-status-copy">
        <span className="eyebrow">AUTHORITATIVE TURN STATE</span>
        <strong data-testid="active-turn-label" data-seat={tableSeat(value.activeSeat)}>{presentation.headline}</strong>
        <span className="priority-holder" data-testid="priority-label" data-seat={tableSeat(value.prioritySeat)}><span aria-hidden="true">◎</span>{presentation.priority}</span>
        <span className="exact-step" data-testid="exact-step-label">{presentation.exactStep}</span>
        {holding && <span className="priority-held" role="status" title={holding}>Holding priority</span>}
      </div>
      <ol className="phase-rail" data-testid="phase-rail" aria-label="Turn phases">
        {presentation.rail.map((entry) => {
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
              ? <div className="phase-detail phase-stop-menu"><PriorityStopOptions selected={priorityStops} onToggle={onPriorityStop} phase={entry.id} /></div>
              : <span className="phase-detail" role="status">
                  {entry.state === "current" ? presentation.exactStep : entry.state === "past" ? "Completed this turn" : "Upcoming this turn"}
                </span>)}
          </li>;
        })}
      </ol>
    </section>
  );
}
