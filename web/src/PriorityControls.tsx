import type { LegalAction } from "./generated/protocol";
import type { PriorityStop } from "./tableAutomation";
import type { TablePreferences } from "./tablePreferences";
import { PriorityStopOptions } from "./TurnStatus";
import type { useTablePriority } from "./useTablePriority";

export function PriorityControls({ priority, preferences, activations, onToggleStop, onChange }: {
  priority: ReturnType<typeof useTablePriority>;
  preferences: TablePreferences;
  activations: LegalAction[];
  onToggleStop: (stop: PriorityStop) => void;
  onChange: <K extends keyof TablePreferences>(key: K, value: TablePreferences[K]) => void;
}) {
  return <div className="turn-yield turn-yield-dock">
    <details className="priority-controls">
      <summary>Priority stops</summary>
      <div className="priority-controls-panel">
        <PriorityStopOptions selected={preferences.priorityStops} onToggle={onToggleStop} />
        <label><input type="checkbox" checked={preferences.holdNewStack}
          onChange={(event) => onChange("holdNewStack", event.target.checked)} /> Hold for new spells and abilities</label>
        <label><input type="checkbox" checked={preferences.holdGraveyard}
          onChange={(event) => onChange("holdGraveyard", event.target.checked)} /> Hold for graveyard activity</label>
        <label><input type="checkbox" checked={preferences.passWithOptionalActivations}
          onChange={(event) => onChange("passWithOptionalActivations", event.target.checked)} /> Auto-pass through optional activations</label>
        {activations.map((action) => <label key={action.id}>
          <input type="checkbox" checked={priority.isIgnored(action)} onChange={(event) => priority.setIgnored(action, event.target.checked)} />
          Skip {action.label ?? "this ability"} this turn
        </label>)}
      </div>
    </details>
    {priority.sync.error
      ? <button type="button" className="link-button" onClick={priority.sync.retry}>Retry syncing priority stops</button>
      : !priority.sync.synced && <small role="status">Syncing priority stops…</small>}
    {priority.held ? <small role="status">Priority held · {priority.held}</small>
      : priority.armed && <small role="status">{priority.waitingOnRequiredChoice ? "A decision is required" : "Passing priority for this turn"}</small>}
    {priority.turnNumber > 0 && <button type="button" className="table-control-toggle" aria-pressed={priority.armed}
      onClick={priority.toggleTurnYield}>{priority.armed ? "Cancel pass turn" : "Pass turn"}</button>}
  </div>;
}
