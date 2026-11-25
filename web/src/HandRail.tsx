import { asRecord, CardTile } from "./CardSurface";
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
    {zoneOffers.size > 0 && <div className="hand-cards hand-zone-offers" role="group"
      aria-label="Available from other zones" data-testid="hand-zone-offers">
      {Array.from(zoneOffers.values(), (offer, index) => renderCard(offer.card, index, offer.origin))}
    </div>}
    <div className="hand-cards" data-testid="own-hand">
      {cards.map((card, index) => renderCard(card, index))}
    </div>
  </div>;
}
