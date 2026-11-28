import { type CSSProperties, type DragEvent, useEffect, useMemo, useState } from "react";
import { asList, asRecord, cardName, CardTile } from "./CardSurface";
import { CardPages } from "./CardPages";
import { type TableZone, ZonePiles } from "./TableZones";
import { CommanderDamage } from "./TableStatus";
import type { JsonValue, LegalAction } from "./generated/protocol";
import type { ProjectedView } from "./protocol";
import type { TableObjectSelection } from "./PublicActionDraft";

import type { BattlefieldLane } from "./tablePreferences";
type BattlefieldStack = { key: string; cards: JsonValue[] };

export function attachedBattlefieldCards(players: Record<string, JsonValue>) {
  const cards = Object.values(players).flatMap((player) => asList(asRecord(player).bf));
  const refs = new Set(cards.map((card) => asRecord(card).id));
  const byTarget = new Map<string, JsonValue[]>();
  const hiddenFromLanes = new Set<string>();
  for (const card of cards) {
    const { id, at } = asRecord(card);
    if (typeof id !== "string" || typeof at !== "string" || !refs.has(at) || at === id) continue;
    if (!byTarget.has(at)) byTarget.set(at, []);
    byTarget.get(at)!.push(card);
    hiddenFromLanes.add(id);
  }
  const byHost = new Map<string, JsonValue[]>();
  const placed = new Set<string>();
  for (const [target, attached] of byTarget) {
    if (hiddenFromLanes.has(target)) continue;
    const children = [...attached];
    const cardsBelow = [];
    while (children.length > 0) {
      const card = children.pop()!;
      const ref = String(asRecord(card).id);
      if (placed.has(ref)) continue;
      placed.add(ref);
      cardsBelow.push(card);
      for (const child of byTarget.get(ref) ?? []) children.push(child);
    }
    byHost.set(target, cardsBelow);
  }
  for (const ref of hiddenFromLanes) if (!placed.has(ref)) hiddenFromLanes.delete(ref);
  return { byTarget: byHost, hiddenFromLanes };
}

export function PlayerBoard({
  seat,
  player,
  active,
  priority,
  mine,
  hidden,
  view,
  labelFor,
  cardActions,
  attachments,
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
  hidden: boolean;
  view: ProjectedView;
  labelFor: (ref: string) => string;
  cardActions: LegalAction[];
  attachments: ReturnType<typeof attachedBattlefieldCards>;
  onCardIntent?: (actions: LegalAction[], card: JsonValue) => void;
  onManaIntent?: (actions: LegalAction[], card: JsonValue) => void;
  onInspectCard: (card: JsonValue) => void;
  onOpenInspector?: (card: JsonValue) => void;
  highlightedTargets: string[];
  selection?: TableObjectSelection | null;
  selectedCardRef: string;
  onOpenZone?: (seat: string, zone: TableZone) => void;
  dropEnabled: boolean;
  onCardDrop?: (cardRef: string) => void;
  onCardDrag?: (cardRef: string | null) => void;
  getDraggedCard: () => string;
  dragRelease: number;
}) {
  const [dragActive, setDragActive] = useState(false);
  const [arranging, setArranging] = useState(false);
  const [arrangement, setArrangement] = useState<string[]>([]);
  const [laneOverrides, setLaneOverrides] = useState<Record<string, BattlefieldLane>>({});
  const [expandedGroup, setExpandedGroup] = useState("");
  useEffect(() => setDragActive(false), [dragRelease]);
  const battlefield = useMemo(() => asList(player.bf), [player.bf]);
  const battlefieldRefs = useMemo(() => battlefield.map((card) => String(asRecord(card).id ?? "")).join(","), [battlefield]);
  useEffect(() => {
    const current = new Set(battlefieldRefs.split(","));
    setArrangement((prior) => {
      const retained = prior.filter((ref) => current.has(ref));
      return prior.length === retained.length ? prior : retained;
    });
    setLaneOverrides((prior) => Object.keys(prior).some((ref) => !current.has(ref))
      ? Object.fromEntries(Object.entries(prior).filter(([ref]) => current.has(ref))) : prior);
  }, [battlefieldRefs]);
  const { groups, landMana } = useMemo(() => {
    const result: Record<BattlefieldLane, JsonValue[]> = { creatures: [], support: [], lands: [] };
    const positions = new Map(arrangement.map((ref, index) => [ref, index]));
    const ranks = new Map<JsonValue, number>();
    const landMana = new Map<JsonValue, string>();
    for (const card of battlefield) {
      const data = asRecord(card);
      const ref = String(data.id ?? "");
      if (attachments.hiddenFromLanes.has(ref)) continue;
      const definition = view.definitions[String(data.cid ?? "")];
      const types = String(data.t ?? definition?.t ?? "").split(" — ")[0].split(" ");
      const group = laneOverrides[ref] ?? (types.includes("Creature") ? "creatures" : types.includes("Land") ? "lands" : "support");
      ranks.set(card, positions.get(ref) ?? Number.MAX_SAFE_INTEGER);
      if (group === "lands") landMana.set(card, [...new Set(asList(definition?.produced_mana).map(String))].sort().join(","));
      result[group].push(card);
    }
    for (const [groupName, group] of Object.entries(result)) group.sort((left, right) =>
      ranks.get(left)! - ranks.get(right)! || (groupName === "lands"
        ? (landMana.get(left) ?? "").localeCompare(landMana.get(right) ?? "") : 0));
    return { groups: result, landMana };
  }, [arrangement, attachments, battlefield, laneOverrides, view.definitions]);
  const actionsByRef = new Map<string, LegalAction[]>();
  for (const action of cardActions) {
    const ref = String(action.card ?? action.source ?? "");
    if (!actionsByRef.has(ref)) actionsByRef.set(ref, []);
    actionsByRef.get(ref)!.push(action);
  }
  const command = asList(player.cmd);
  const mana = asRecord(player.mana);
  const selectableRefs = useMemo(() => new Set(selection?.refs), [selection?.refs]);
  const highlighted = useMemo(() => new Set(highlightedTargets), [highlightedTargets]);
  const stackGroups = useMemo(() => Object.fromEntries(Object.entries(groups).map(([group, cards]) => {
    const stacks = new Map<string, BattlefieldStack>();
    for (const card of cards) {
      const row = asRecord(card);
      const ref = String(row.id);
      const canStack = !arranging && row.cid && row.n !== "?" && (row.tok || group === "lands")
        && !row.cmd && !row.at && !row.atk && !row.ctr && !row.dmg && !attachments.byTarget.has(ref)
        && !selectableRefs.has(ref) && !highlighted.has(ref) && selectedCardRef !== ref;
      const key = canStack ? JSON.stringify([row.cid, row.face, row.tap, row.t, row.n, row.p, row.q, row.ctl]) : ref;
      if (!stacks.has(key)) stacks.set(key, { key, cards: [] });
      stacks.get(key)!.cards.push(card);
    }
    return [group, stacks];
  })) as Record<BattlefieldLane, Map<string, BattlefieldStack>>,
  [arranging, attachments, groups, highlighted, selectedCardRef, selectableRefs]);
  const isZoneTarget = (cards: JsonValue[]) => cards.some((card) => {
    const ref = String(asRecord(card).id ?? "");
    return selection?.refs.includes(ref) || highlighted.has(ref);
  });
  function moveInRow(row: JsonValue[], index: number, offset: number) {
    const refs = row.map((card) => String(asRecord(card).id));
    const to = index + offset;
    if (to < 0 || to >= refs.length) return;
    [refs[index], refs[to]] = [refs[to], refs[index]];
    setArrangement((prior) => [...prior.filter((ref) => !refs.includes(ref)), ...refs]);
  }
  function permanent(card: JsonValue, index: number, row: JsonValue[], group: BattlefieldLane,
    stack: BattlefieldStack, overlapLand: boolean) {
    const data = asRecord(card);
    const ref = String(data.id ?? "");
    const attached = typeof data.at === "string" && data.at ? labelFor(data.at) : "";
    const actions = !arranging && !selection ? actionsByRef.get(ref) ?? [] : [];
    const manaOnly = actions.length > 0 && actions.every((action) =>
      action.mana_ability === true || action.action === "undo_mana");
    const attachedCards = attachments.byTarget.get(ref) ?? [];
    return <div className={`battlefield-card-slot${attachedCards.length ? " has-attached-cards" : ""}${stack.cards.length > 1 ? " stacked-cards" : ""}${overlapLand ? " same-land-mana" : ""}`}
      key={stack.key}>
      <CardTile value={card} view={view} table="battlefield" actions={actions} onOpenInspector={onOpenInspector}
        showPrintedCost={false}
        inspectOnActivate={!selection && !arranging}
        attachment={attached}
        onIntent={arranging || selection ? undefined : manaOnly ? onManaIntent : onCardIntent} onInspect={onInspectCard}
        manualMana={manaOnly} selected={selectedCardRef === ref || selection?.selected.includes(ref)}
        explainUnavailable={mine && !arranging && !selection}
        choice={selection?.refs.includes(ref) ? { selected: selection.selected.includes(ref), select: () => selection.select(ref) } : undefined}
        highlight={highlightedTargets.includes(ref)} />
      {stack.cards.length > 1 && <button type="button" className="battlefield-stack-count"
        aria-label={`Show all ${stack.cards.length} ${cardName(card)} cards`}
        onClick={() => setExpandedGroup(stack.key)}>×{stack.cards.length.toLocaleString()}</button>}
      {attachedCards.length > 0 && <div className="attachment-rail">
        <span className="attachment-connector" aria-hidden="true" />
        <div className="permanent-attachments" role="group" aria-label={`Attached to ${cardName(card)}`}>
          {attachedCards.map((attachment) => {
            const attachedRef = String(asRecord(attachment).id);
            const available = !selection && !arranging ? actionsByRef.get(attachedRef) ?? [] : [];
            const manaOnly = available.length > 0 && available.every((action) =>
              action.mana_ability === true || action.action === "undo_mana");
            return <CardTile key={attachedRef} value={attachment} view={view} compact showPrintedCost={false}
              attachment={cardName(card)} highlight={highlighted.has(attachedRef)} selected={selectedCardRef === attachedRef}
              actions={available} manualMana={manaOnly} onIntent={onCardIntent} onOpenInspector={onOpenInspector}
              onInspect={onInspectCard} inspectOnActivate
              choice={selection?.refs.includes(attachedRef) ? {
                selected: selection.selected.includes(attachedRef), select: () => selection.select(attachedRef),
              } : undefined} />;
          })}
        </div>
      </div>}
      {arranging && !selection
        ? <div className="layout-moves">
          <button type="button" className="link-button" disabled={index === 0}
            aria-label={`Move ${cardName(card)} earlier`} onClick={() => moveInRow(row, index, -1)}>←</button>
          <button type="button" className="link-button" disabled={index === row.length - 1}
            aria-label={`Move ${cardName(card)} later`} onClick={() => moveInRow(row, index, 1)}>→</button>
          <select aria-label={`Lane for ${cardName(card)}`} value={group}
            onChange={(event) => setLaneOverrides((prior) => ({ ...prior, [ref]: event.target.value as BattlefieldLane }))}>
            <option value="creatures">Creatures</option><option value="support">Other</option><option value="lands">Lands</option>
          </select>
        </div>
        : null}
    </div>;
  }
  function lane(group: keyof typeof groups, label: string) {
    const cards = groups[group];
    const stacks = stackGroups[group];
    const expanded = stacks.get(expandedGroup);
    const items = expanded ? expanded.cards.map((card) => ({ key: String(asRecord(card).id), cards: [card] })) : [...stacks.values()];
    const hasAttachmentRails = cards.some((card) => attachments.byTarget.has(String(asRecord(card).id ?? "")));
    const overlaps = group === "lands" && !arranging && !expanded && !hasAttachmentRails
      ? items.map(({ cards: [card] }, index) => {
        const previous = items[index - 1]?.cards[0];
        const mana = landMana.get(card);
        return previous && mana && mana === landMana.get(previous) ? "1" : "0";
      }).join("") : undefined;
    const rows = Math.min(3, Math.ceil(Math.max(1, items.length) / 5));
    const laneWeight = group === "creatures" ? 2.25 + rows * 0.4
      : group === "lands" ? 0.9 + rows * 0.12 : 1 + rows * 0.19;
    return cards.length > 0 && <CardPages
      items={items} label={label} heading={expanded
        ? <button type="button" className="link-button" onClick={() => setExpandedGroup("")}>Back to {label.toLowerCase()}</button>
        : <span className="battlefield-lane-label">{label}</span>}
      className={`battlefield-lane lane-${group}`} layout={group === "lands" ? "row" : "grid"}
      style={{ "--battlefield-default-lane-weight": laneWeight } as CSSProperties} overlaps={overlaps}
      trackClassName={`battlefield-row battlefield-${group}${hasAttachmentRails ? " has-attachment-rails" : ""}`}
      render={(stack, index) => {
        const representative = actionsByRef.size > 0 && !selection
          ? stack.cards.find((card) => actionsByRef.has(String(asRecord(card).id))) : undefined;
        const card = representative ?? stack.cards[0];
        return permanent(card, index, cards, group, stack, overlaps?.[index] === "1");
      }} />;
  }
  const lands = lane("lands", "Lands");
  return (
    <article className={`player-board${active ? " active-player" : ""}${priority ? " has-priority" : ""}${mine ? " own-board" : ""}`}
      hidden={hidden} data-seat={seat} data-testid={`player-${seat}`}>
      <header>
        <span className="seat-letter small">{seat}</span>
        <div className="player-name">
          <strong>Seat {seat}</strong>{mine && <span className="you-chip">You</span>}
          <CommanderDamage seat={seat} value={player.cmd_dmg ?? []} />
          <small>{String(player.hand_n ?? 0)} in hand</small>
          {!mine && onOpenZone && asList(player.known_hand).length > 0 && <button type="button" className="link-button"
            aria-label={`View revealed cards in Seat ${seat}'s hand`} onClick={() => onOpenZone(seat, "known_hand")}>
            Revealed hand
          </button>}
        </div>
        {selection?.refs.includes(seat) ? <button type="button" className="life player-selection" data-player-ref={seat}
          aria-label={`Select Seat ${seat}, ${String(player.life ?? 0)} life`} aria-pressed={selection.selected.includes(seat)}
          onClick={() => selection.select(seat)}><span>{String(player.life ?? 0)}</span><small>LIFE</small></button>
          : <div className={`life${highlightedTargets.includes(seat) ? " target-highlight" : ""}`} data-player-ref={seat} aria-label={`${String(player.life ?? 0)} life`}><span>{String(player.life ?? 0)}</span><small>Life</small></div>}
      </header>
      {(active || priority) && <div className="turn-flags" aria-label={`Seat ${seat} turn status`}>{active && <span className="active-flag"><span aria-hidden="true">▶</span> Active turn</span>}{priority && <span className="priority-flag"><span aria-hidden="true">◎</span> Priority</span>}</div>}
      <div className={`card-strip command-zone${isZoneTarget(command) ? " zone-target" : ""}`} data-target-zone={`${seat}-cmd`} aria-label={`Seat ${seat} command zone`}>
        <CardPages items={command} label="Command zone" layout="column" trackClassName="command-cards"
          heading={<span className="command-zone-label">Command zone</span>}
          render={(card, index) => {
        const ref = String(asRecord(card).id ?? "");
        const actions = mine && !selection ? actionsByRef.get(ref) ?? [] : [];
        return <CardTile key={ref || index} value={card} view={view} compact actions={actions}
          commandZone showPrintedCost onOpenInspector={onOpenInspector} inspectOnActivate={false}
          onIntent={onCardIntent} onInspect={onInspectCard} onDragCard={onCardDrag} onDropCard={onCardDrop}
          selected={selectedCardRef === ref || selection?.selected.includes(ref)} explainUnavailable={mine && !selection}
          highlight={highlightedTargets.includes(ref)}
          choice={selection?.refs.includes(ref) ? { selected: selection.selected.includes(ref), select: () => selection.select(ref) } : undefined} />;
      }} />
        {!command.length && <em>Empty</em>}
      </div>
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
          if (event.relatedTarget && event.currentTarget.contains(event.relatedTarget as Node)) return;
          setDragActive(false);
        }}
        onDrop={(event: DragEvent<HTMLDivElement>) => {
          if (!mine || !dropEnabled) return;
          event.preventDefault();
          setDragActive(false);
          const ref = event.dataTransfer.getData("application/x-commander-card") || event.dataTransfer.getData("text/plain") || getDraggedCard();
          if (ref) onCardDrop?.(ref);
        }}
        onPointerUp={(event) => {
          if (!mine || !dropEnabled || event.button !== 0) return;
          const ref = getDraggedCard();
          if (ref) onCardDrop?.(ref);
        }}
      >
        {battlefield.length ? <div className="battlefield-lanes">
          {!mine && lands}
          {lane("creatures", "Creatures")}
          {lane("support", "Other permanents")}
          {mine && lands}
        </div> : <em>{mine && dropEnabled ? "Drop a playable card here" : "Empty battlefield"}</em>}
      </div>
      <footer className="zone-summary">
        {!mine && onOpenZone && <ZonePiles seat={seat} player={player} hidden={hidden} selection={selection}
          highlightedTargets={highlightedTargets} onOpen={onOpenZone} />}
        <span>Land plays left <strong>{String(player.lands ?? 0)}</strong></span>
        {!mine && Object.keys(mana).length > 0 && <span>Mana <strong>{Object.entries(mana).map(([color, amount]) => `${color}${amount}`).join(" ")}</strong></span>}
        {mine && <button type="button" className="link-button" aria-pressed={arranging} disabled={Boolean(selection)}
          onClick={() => setArranging((active) => !active)}>{arranging ? "Done arranging" : "Arrange"}</button>}
        {mine && (arranging || arrangement.length > 0 || Object.keys(laneOverrides).length > 0) && <button type="button" className="link-button"
          onClick={() => { setArrangement([]); setLaneOverrides({}); }}>Reset layout</button>}
      </footer>
    </article>
  );
}
