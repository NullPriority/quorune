import type { JsonValue, LegalAction } from "./generated/protocol";


function asRecord(value: JsonValue): Record<string, JsonValue> {
  return value && !Array.isArray(value) && typeof value === "object" ? value : {};
}


export function CommanderDamage({ seat, value }: { seat: string; value: JsonValue }) {
  const sources = (Array.isArray(value) ? value.map(asRecord) : [])
    .filter((source) => typeof source.amount === "number" && source.amount > 0);
  if (!sources.length) return null;

  return (
    <span className="commander-damage" role="group" aria-label="Commander damage received" data-testid={"commander-damage-" + seat}>
      {sources.map((source, index) => {
        const owner = typeof source.owner === "string" ? source.owner : "";
        const name = String(source.n ?? "Commander");
        const amount = String(source.amount);
        return (
          <span
            key={String(source.cid ?? index)}
            className="commander-damage-chip"
            data-seat-accent={owner || undefined}
            data-commander-id={String(source.cid ?? "")}
            role="group"
            tabIndex={0}
            aria-label={amount + " commander damage from " + name + (owner ? ", Seat " + owner : "")}
          >
            <strong className="commander-damage-amount">{amount}</strong>
            <span className="commander-damage-tooltip" aria-hidden="true">{name}</span>
          </span>
        );
      })}
    </span>
  );
}


export function PlayerIdentity({ seat, mine, damage }: { seat: string; mine: boolean; damage: JsonValue }) {
  return (
    <div className="player-name">
      <strong>Seat {seat}</strong>
      {mine && <span className="you-chip">You</span>}
      <CommanderDamage seat={seat} value={damage} />
    </div>
  );
}


export function ManaHelp({
  actions,
  disabled,
  onSelect,
}: {
  actions: LegalAction[];
  disabled: boolean;
  onSelect: (action: LegalAction) => void;
}) {
  return (
    <div className="mana-help" role="status">
      <span>Click a highlighted untapped permanent to add mana in the order you choose. Before spending or passing, click that tapped source again to remove its mana and untap it.</span>
      {actions.map((action) => (
        <button type="button" key={action.id} disabled={disabled} onClick={() => onSelect(action)}>
          {action.label ?? "Undo last mana activation"}
        </button>
      ))}
    </div>
  );
}


export function AutomationControls({
  autoPass,
  autoMana,
  onAutoPass,
  onAutoMana,
}: {
  autoPass: boolean;
  autoMana: boolean;
  onAutoPass: () => void;
  onAutoMana: () => void;
}) {
  return (
    <>
      <button
        type="button"
        className={`table-control-toggle${autoPass ? " active" : " full-control"}`}
        aria-pressed={autoPass}
        data-testid="auto-pass-toggle"
        title="Automatically submit pass-only priority capabilities. Turn this off to hold every priority stop, including stops during another player's turn."
        onClick={onAutoPass}
      >{autoPass ? "Auto-pass enabled" : <>Full control<span className="priority-control-description"> · Hold every priority</span></>}</button>
      <button
        type="button"
        className={`table-control-toggle${autoMana ? " active" : " manual-control"}`}
        aria-pressed={autoMana}
        data-testid="auto-mana-toggle"
        title="Automatically derive routine mana payments. Turn this off to tap mana sources yourself."
        onClick={onAutoMana}
      >{autoMana ? "Auto-mana on" : "Manual mana on"}</button>
    </>
  );
}
