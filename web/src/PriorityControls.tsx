import type { LegalAction } from "./generated/protocol";
import type { PriorityStop } from "./tableAutomation";
import type { TablePreferences } from "./tablePreferences";
import { PriorityStopOptions } from "./TurnStatus";
import type { useTablePriority } from "./useTablePriority";
import { useState, type ReactNode } from "react";
import { tableSeat } from "./SeatLabel";

export function PriorityControls({ priority, preferences, activations, onToggleStop, onChange, loopControls, opponentSeats = [] }: {
  priority: ReturnType<typeof useTablePriority>;
  preferences: TablePreferences;
  activations: LegalAction[];
  onToggleStop: (stop: PriorityStop) => void;
  onChange: <K extends keyof TablePreferences>(key: K, value: TablePreferences[K]) => void;
  loopControls?: ReactNode;
  opponentSeats?: readonly string[];
}) {
  const [scope, setScope] = useState("all");
  const seat = tableSeat(scope);
  const selected = seat ? preferences.seatPriorityStops[seat] ?? [] : preferences.priorityStops;
  const count = preferences.priorityStops.length + Object.values(preferences.seatPriorityStops).flat().length;
  function toggle(stop: PriorityStop) {
    if (!seat) { onToggleStop(stop); return; }
    const next = selected.includes(stop) ? selected.filter((entry) => entry !== stop) : [...selected, stop];
    onChange("seatPriorityStops", { ...preferences.seatPriorityStops, [seat]: next });
  }
  return <div className="turn-yield turn-yield-dock">
    {loopControls}
    <details className="priority-controls">
      <summary>Priority stops{count > 0 &&
        <span className="priority-stop-count" aria-label={`${count} selected`}>{count}</span>}</summary>
      <div className="priority-controls-panel">
        {opponentSeats.length > 0 && <label>Apply stops during
          <select aria-label="Priority stop turn" value={scope} onChange={(event) => setScope(event.target.value)}>
            <option value="all">Any seat’s turn</option>
            {opponentSeats.map((id) => <option key={id} value={id}>Seat {id}’s turn</option>)}
          </select>
        </label>}
        <PriorityStopOptions selected={selected} onToggle={toggle} seatScoped={Boolean(seat)} />
        <label><input type="checkbox" checked={preferences.holdNewStack}
          onChange={(event) => onChange("holdNewStack", event.target.checked)} /> Hold for new spells and abilities</label>
        <label><input type="checkbox" checked={preferences.holdGraveyard}
          onChange={(event) => onChange("holdGraveyard", event.target.checked)} /> Hold for graveyard activity</label>
        <label><input type="checkbox" checked={preferences.passWithOptionalActivations}
          onChange={(event) => onChange("passWithOptionalActivations", event.target.checked)} /> Auto-pass through optional activations</label>
        {activations.map((action) => <label key={action.id}>
          <input type="checkbox" checked={priority.isSkippingActivation(action)}
            onChange={(event) => priority.skipActivation(action, event.target.checked)} />
          Skip unchanged this game · {action.label ?? "Ability"}
        </label>)}
      </div>
    </details>
    {priority.sync.error
      ? <button type="button" className="link-button" onClick={priority.sync.retry}>Retry syncing priority stops</button>
      : !priority.sync.synced && <small role="status">Syncing priority stops…</small>}
    {priority.held ? <small role="status">Priority held · {priority.held}</small>
      : priority.armed && <small role="status">{priority.waitingOnRequiredChoice ? "A decision is required" : "Passing priority for this turn"}</small>}
    {priority.advancingToFirstMain && <small role="status">{priority.waitingOnRequiredChoice ? "A decision is required" : "Passing to first main"}</small>}
    {priority.turnNumber > 0 && <button type="button" className="table-control-toggle" aria-pressed={priority.armed}
      onClick={priority.toggleTurnYield}>{priority.armed ? "Cancel pass turn" : priority.advancingToFirstMain ? "Cancel advance" : "Pass turn"}</button>}
  </div>;
}
