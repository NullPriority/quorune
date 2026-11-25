import { type DragEvent, useEffect, useState } from "react";
import { asList, asRecord, cardName, CardTile } from "./CardSurface";
import { CommanderDamage } from "./TableStatus";
import type { JsonValue, LegalAction } from "./generated/protocol";
import type { ProjectedView } from "./protocol";
import type { TableObjectSelection } from "./PublicActionDraft";

export function PlayerBoard({
  seat,
  player,
  active,
  priority,
  mine,
  view,
  cardActions,
  onCardIntent,
  onManaIntent,
  onInspectCard,
  onOpenInspector,
  highlightedTargets,
  selection,
  selectedCardRef,
  onOpenZone,
  dropEnabled,
  onCardDrop,
  onCardDrag,
  getDraggedCard,
  dragRelease,
}: {
  seat: string;
  player: Record<string, JsonValue>;
  active: boolean;
  priority: boolean;
  mine: boolean;
  view: ProjectedView;
  cardActions: LegalAction[];
  onCardIntent: (actions: LegalAction[], card: JsonValue) => void;
  onManaIntent: (actions: LegalAction[], card: JsonValue) => void;
  onInspectCard: (card: JsonValue) => void;
  onOpenInspector: (card: JsonValue) => void;
  highlightedTargets: string[];
  selection?: TableObjectSelection | null;
  selectedCardRef: string;
  onOpenZone: (seat: string, zone: "gy" | "ex") => void;
  dropEnabled: boolean;
  onCardDrop: (cardRef: string) => void;
  onCardDrag: (cardRef: string | null) => void;
  getDraggedCard: () => string;
  dragRelease: number;
}) {
  const [dragActive, setDragActive] = useState(false);
  const [arranging, setArranging] = useState(false);
  const [arrangement, setArrangement] = useState<string[]>([]);
  useEffect(() => setDragActive(false), [dragRelease]);
  const battlefield = asList(player.bf);
  const battlefieldRefs = battlefield.map((card) => String(asRecord(card).id ?? "")).join(",");
  useEffect(() => {
    const current = new Set(battlefieldRefs.split(","));
    setArrangement((prior) => {
      const retained = prior.filter((ref) => current.has(ref));
      return prior.length === retained.length ? prior : retained;
    });
  }, [battlefieldRefs]);
  const groups = { creatures: [] as JsonValue[], support: [] as JsonValue[], lands: [] as JsonValue[] };
  for (const card of battlefield) {
    const data = asRecord(card);
    const definition = view.definitions[String(data.cid ?? "")];
    const types = String(data.t ?? definition?.t ?? "").split(" — ")[0].split(" ");
    const group = types.includes("Creature") ? "creatures" : types.includes("Land") ? "lands" : "support";
    groups[group].push(card);
  }
  const positions = new Map(arrangement.map((ref, index) => [ref, index]));
  const rank = (card: JsonValue) => positions.get(String(asRecord(card).id ?? "")) ?? Number.MAX_SAFE_INTEGER;
  for (const group of Object.values(groups)) group.sort((left, right) => rank(left) - rank(right));
  const command = asList(player.cmd);
  const graveyard = asList(player.gy);
  const exile = asList(player.ex);
  const mana = asRecord(player.mana);
  const isZoneTarget = (cards: JsonValue[]) => cards.some((card) => {
    const ref = String(asRecord(card).id ?? "");
    return selection?.refs.includes(ref) || highlightedTargets.includes(ref);
  });
  function moveInRow(row: JsonValue[], index: number, offset: number) {
    const refs = row.map((card) => String(asRecord(card).id));
    const to = index + offset;
    if (to < 0 || to >= refs.length) return;
    [refs[index], refs[to]] = [refs[to], refs[index]];
    setArrangement((prior) => [...prior.filter((ref) => !refs.includes(ref)), ...refs]);
  }
  function permanent(card: JsonValue, index: number, row: JsonValue[]) {
    const ref = String(asRecord(card).id ?? "");
    const actions = mine && !arranging && !selection
      ? cardActions.filter((action) => String(action.card ?? action.source ?? "") === ref)
      : [];
    const manaOnly = actions.length > 0 && actions.every((action) =>
      action.mana_ability === true || action.action === "undo_mana");
    return <div className="battlefield-card-slot" key={ref || index}>
      <CardTile value={card} view={view} table actions={actions}
        onIntent={arranging || selection ? undefined : manaOnly ? onManaIntent : onCardIntent} onInspect={onInspectCard}
        manualMana={manaOnly} selected={selectedCardRef === ref || selection?.selected.includes(ref)}
        explainUnavailable={mine && !arranging && !selection}
        choice={selection?.refs.includes(ref) ? { selected: selection.selected.includes(ref), select: () => selection.select(ref) } : undefined}
        highlight={highlightedTargets.includes(ref)} />
      {arranging && !selection
        ? <div className="layout-moves">
          <button type="button" className="link-button" disabled={index === 0}
            aria-label={`Move ${cardName(card)} earlier`} onClick={() => moveInRow(row, index, -1)}>←</button>
          <button type="button" className="link-button" disabled={index === row.length - 1}
            aria-label={`Move ${cardName(card)} later`} onClick={() => moveInRow(row, index, 1)}>→</button>
        </div>
        : <button type="button" className="inspect-card-button" aria-label={`Inspect ${cardName(card)}`}
          onClick={() => onOpenInspector(card)}>Inspect</button>}
    </div>;
  }
  return (
    <article className={`player-board${active ? " active-player" : ""}${priority ? " has-priority" : ""}${mine ? " own-board" : ""}`} data-testid={`player-${seat}`}>
      <header>
        <span className="seat-letter small">{seat}</span>
        <div>
          <div className="player-name"><strong>Seat {seat}</strong>{mine && <span className="you-chip">You</span>}</div>
          <small>{String(player.hand_n ?? 0)} hand · {String(player.lib_n ?? 0)} library</small>
        </div>
        {selection?.refs.includes(seat) ? <button type="button" className="life player-selection" data-player-ref={seat}
          aria-label={`Select Seat ${seat}, ${String(player.life ?? 0)} life`} aria-pressed={selection.selected.includes(seat)}
          onClick={() => selection.select(seat)}><span>{String(player.life ?? 0)}</span><small>LIFE</small></button>
          : <div className={`life${highlightedTargets.includes(seat) ? " target-highlight" : ""}`} data-player-ref={seat} aria-label={`${String(player.life ?? 0)} life`}><span>{String(player.life ?? 0)}</span><small>Life</small></div>}
      </header>
      {(active || priority) && <div className="turn-flags" aria-label={`Seat ${seat} turn status`}>{active && <span className="active-flag"><span aria-hidden="true">▶</span> Active turn</span>}{priority && <span className="priority-flag"><span aria-hidden="true">◎</span> Priority</span>}</div>}
      <CommanderDamage seat={seat} value={player.cmd_dmg ?? []} />
      <div className={`card-strip command-zone${isZoneTarget(command) ? " zone-target" : ""}`} data-target-zone={`${seat}-cmd`} aria-label={`Seat ${seat} command zone`}>
        <span className="command-zone-label">Command zone</span>
        {command.length ? command.map((card, index) => {
        const ref = String(asRecord(card).id ?? "");
        const actions = mine && !selection
          ? cardActions.filter((action) => String(action.card ?? action.source ?? "") === ref)
          : [];
        return <CardTile key={ref || index} value={card} view={view} compact actions={actions}
          onIntent={onCardIntent} onInspect={onInspectCard} onDragCard={onCardDrag} onDropCard={onCardDrop}
          selected={selectedCardRef === ref || selection?.selected.includes(ref)} explainUnavailable={mine && !selection}
          highlight={highlightedTargets.includes(ref)}
          choice={selection?.refs.includes(ref) ? { selected: selection.selected.includes(ref), select: () => selection.select(ref) } : undefined} />;
      }) : <em>Empty</em>}</div>
      <div
        className={`card-strip battlefield${mine && dropEnabled ? " drop-target" : ""}${dragActive ? " drag-active" : ""}`}
        data-testid={mine ? "own-battlefield" : undefined}
        data-own-battlefield={mine ? "true" : undefined}
        onDragOver={(event) => {
          if (!mine || !dropEnabled) return;
          event.preventDefault();
          event.dataTransfer.dropEffect = "move";
        }}
        onDragEnter={(event) => {
          if (!mine || !dropEnabled) return;
          event.preventDefault();
          setDragActive(true);
        }}
        onDragLeave={(event) => {
          if (event.currentTarget.contains(event.relatedTarget as Node | null)) return;
          setDragActive(false);
        }}
        onDrop={(event: DragEvent<HTMLDivElement>) => {
          if (!mine || !dropEnabled) return;
          event.preventDefault();
          setDragActive(false);
          const ref = event.dataTransfer.getData("application/x-commander-card") || event.dataTransfer.getData("text/plain") || getDraggedCard();
          if (ref) onCardDrop(ref);
        }}
        onPointerUp={(event) => {
          if (!mine || !dropEnabled || event.button !== 0) return;
          const ref = getDraggedCard();
          if (ref) onCardDrop(ref);
        }}
      >
        {battlefield.length ? Object.entries(groups).map(([group, cards]) => cards.length > 0 &&
          <div className={`battlefield-row battlefield-${group}`} key={group} aria-label={group}>
            {cards.map((card, index) => permanent(card, index, cards))}
          </div>) : <em>{mine && dropEnabled ? "Drop a playable card here" : "Empty battlefield"}</em>}
      </div>
      <footer className="zone-summary">
        <span data-target-zone={`${seat}-known_top`} title={`Seat ${seat} library`}>Library <strong>{String(player.lib_n ?? 0)}</strong></span>
        <button type="button" data-testid={`zone-${seat}-graveyard`} data-target-zone={`${seat}-gy`} disabled={!graveyard.length}
          className={isZoneTarget(graveyard) ? "zone-target" : undefined} onClick={() => onOpenZone(seat, "gy")}>Graveyard <strong>{graveyard.length}</strong></button>
        <button type="button" data-testid={`zone-${seat}-exile`} data-target-zone={`${seat}-ex`} disabled={!exile.length}
          className={isZoneTarget(exile) ? "zone-target" : undefined} onClick={() => onOpenZone(seat, "ex")}>Exile <strong>{exile.length}</strong></button>
        <span>LAND <strong>{String(player.lands ?? 0)}</strong></span>
        <span>MANA <strong>{Object.entries(mana).map(([color, amount]) => `${color}${amount}`).join(" ") || "—"}</strong></span>
        {mine && <button type="button" className="link-button" aria-pressed={arranging} disabled={Boolean(selection)}
          onClick={() => setArranging((active) => !active)}>{arranging ? "Done arranging" : "Arrange"}</button>}
        {mine && (arranging || arrangement.length > 0) && <button type="button" className="link-button"
          onClick={() => setArrangement([])}>Reset layout</button>}
      </footer>
    </article>
  );
}
