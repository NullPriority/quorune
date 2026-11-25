import type { CSSProperties } from "react";
import { asRecord, CardTile } from "./CardSurface";
import { CardPages } from "./CardPages";
import type { JsonValue, LegalAction } from "./generated/protocol";
import type { HandZoneOffer } from "./handZoneOffers";
import type { ProjectedView } from "./protocol";
import type { TableObjectSelection } from "./PublicActionDraft";

export function HandRail({
  cards, zoneOffers, view, selection, selectedRef, actionsForCard, onIntent, onInspect, onDrag, onDrop,
}: {
  cards: JsonValue[];
  zoneOffers: ReadonlyMap<string, HandZoneOffer>;
  view: ProjectedView;
  selection: TableObjectSelection | null;
  selectedRef: string;
  actionsForCard: (ref: string) => LegalAction[];
  onIntent: (actions: LegalAction[], card: JsonValue) => void;
  onInspect: (card: JsonValue) => void;
  onDrag: (ref: string | null) => void;
  onDrop: (ref: string) => void;
}) {
  function renderCard(card: JsonValue, index: number, origin?: string) {
    const ref = String(asRecord(card).id ?? "");
    const chosen = selection?.selected.includes(ref) ?? false;
    const choice = selection?.refs.includes(ref)
      ? { selected: chosen, select: () => selection.select(ref) } : undefined;
    return <CardTile key={ref || index} value={card} view={view} origin={origin} artCrop
      actions={selection ? [] : actionsForCard(ref)} onIntent={selection ? undefined : onIntent}
      onInspect={onInspect} onDragCard={onDrag} onDropCard={onDrop}
      selected={selectedRef === ref || chosen} choice={choice} explainUnavailable={!origin} />;
  }

  return <div className="hand-scroll">
    {zoneOffers.size > 0 && <CardPages items={[...zoneOffers.values()]} label="Available from other zones"
      className="hand-other-pages" trackClassName="hand-cards hand-zone-offers" testId="hand-zone-offers"
      style={{ "--offered-card-count": zoneOffers.size } as CSSProperties}
      render={(offer, index) => renderCard(offer.card, index, offer.origin)} />}
    <CardPages items={cards} label="Hand" className="hand-main-pages" trackClassName="hand-cards"
      testId="own-hand" render={renderCard} />
  </div>;
}
