import { useState } from "react";

import { COMBAT_PRIORITY_STOPS, type CombatPriorityStop } from "./tableAutomation";
import {
  presentTurn,
  type MacroPhase,
  type TurnPresentationInput,
} from "./tablePresentation";


export function CombatStopOptions({ selected, onToggle }: {
  selected: readonly CombatPriorityStop[];
  onToggle: (stop: CombatPriorityStop) => void;
}) {
  return (
    <div className="combat-stop-options" role="group" aria-label="Combat priority stops">
      {COMBAT_PRIORITY_STOPS.map(({ id, label }) => (
        <button key={id} type="button" aria-pressed={selected.includes(id)} onClick={() => onToggle(id)}>{label}</button>
      ))}
    </div>
  );
}

export function TurnStatus({ value, compact, combatStops, onCombatStop, holding }: {
  value: TurnPresentationInput;
  compact: boolean;
  combatStops: readonly CombatPriorityStop[];
  onCombatStop?: (stop: CombatPriorityStop) => void;
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
        <strong data-testid="active-turn-label">{presentation.headline}</strong>
        <span className="priority-holder" data-testid="priority-label"><span aria-hidden="true">◎</span>{presentation.priority}</span>
        <span className="exact-step" data-testid="exact-step-label">{presentation.exactStep}</span>
        {holding && <span className="priority-held" role="status" title={holding}>Holding priority</span>}
      </div>
      <ol className="phase-rail" data-testid="phase-rail" aria-label="Turn phases">
        {presentation.rail.map((entry) => (
          <li key={entry.id} className={`${entry.state}${entry.id === "combat" && combatStops.length ? " has-priority-stop" : ""}`}>
            <button
              type="button"
              aria-current={entry.state === "current" ? "step" : undefined}
              aria-expanded={expanded === entry.id}
              title={entry.id === "combat" && onCombatStop ? "Set combat priority stops" : undefined}
              onClick={() => setExpanded((current) => current === entry.id ? null : entry.id)}
            >
              <span className="phase-marker" aria-hidden="true">{entry.state === "past" ? "✓" : entry.state === "current" ? "●" : "○"}</span>
              {entry.label}
            </button>
            {expanded === entry.id && (entry.id === "combat" && onCombatStop
              ? <div className="phase-detail phase-stop-menu"><CombatStopOptions selected={combatStops} onToggle={onCombatStop} /></div>
              : <span className="phase-detail" role="status">
                  {entry.state === "current" ? presentation.exactStep : entry.state === "past" ? "Completed this turn" : "Upcoming this turn"}
                </span>)}
          </li>
        ))}
      </ol>
    </section>
  );
}
