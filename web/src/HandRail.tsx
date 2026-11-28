import { type CSSProperties, useRef } from "react";
import { MAX_HAND_PANEL_HEIGHT, MIN_HAND_PANEL_HEIGHT } from "./tablePreferences";
import { asRecord, CardTile } from "./CardSurface";
import { CardPages } from "./CardPages";
import type { JsonValue, LegalAction } from "./generated/protocol";
import type { HandZoneOffer } from "./handZoneOffers";
import type { ProjectedView } from "./protocol";
import type { TableObjectSelection } from "./PublicActionDraft";

function HandResizeHandle({ onResize }: { onResize: (height: number) => void }) {
  const drag = useRef<{ panel: HTMLElement; y: number; height: number } | null>(null);
  const heightProperty = "--hand-panel-height";
  function preview(panel: HTMLElement, height: number) {
    const bounded = Math.max(MIN_HAND_PANEL_HEIGHT, Math.min(MAX_HAND_PANEL_HEIGHT, height));
    panel.style.setProperty(heightProperty, bounded + "px");
  }
  function commit(panel: HTMLElement) {
    const height = Math.round(panel.getBoundingClientRect().height);
    panel.style.removeProperty(heightProperty);
    onResize(height);
  }
  return <button type="button" className="hand-resize-handle" aria-label="Resize hand"
    title="Drag up or down, or use the arrow keys"
    onPointerDown={(event) => {
      const panel = event.currentTarget.closest<HTMLElement>(".hand-panel");
      if (!panel || event.button !== 0 || drag.current) return;
      event.currentTarget.setPointerCapture(event.pointerId);
      drag.current = { panel, y: event.clientY, height: panel.getBoundingClientRect().height };
    }}
    onPointerMove={(event) => {
      if (!event.currentTarget.hasPointerCapture(event.pointerId) || !drag.current) return;
      const { panel, y, height } = drag.current;
      preview(panel, height + y - event.clientY);
    }}
    onPointerUp={(event) => {
      if (!event.currentTarget.hasPointerCapture(event.pointerId) || !drag.current) return;
      const { panel } = drag.current;
      drag.current = null;
      event.currentTarget.releasePointerCapture(event.pointerId);
      commit(panel);
    }}
    onLostPointerCapture={() => {
      drag.current?.panel.style.removeProperty(heightProperty);
      drag.current = null;
    }}
    onKeyDown={(event) => {
      const offset = event.key === "ArrowUp" ? 24 : event.key === "ArrowDown" ? -24 : 0;
      const panel = event.currentTarget.closest<HTMLElement>(".hand-panel");
      if (!offset || !panel) return;
      event.preventDefault();
      preview(panel, panel.getBoundingClientRect().height + offset);
      commit(panel);
    }} />;
}

export function HandRail({
  cards, zoneOffers, view, selection, selectedRef, actionsForCard, onIntent, onInspect, onDrag, onDrop, artFocused, onResize,
}: {
  cards: JsonValue[];
  artFocused: boolean;
  onResize?: (height: number) => void;
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
    return <CardTile key={ref || index} value={card} view={view} origin={origin} artCrop={artFocused}
      actions={selection ? [] : actionsForCard(ref)} onIntent={selection ? undefined : onIntent}
      onInspect={onInspect} onDragCard={onDrag} onDropCard={onDrop}
      selected={selectedRef === ref || chosen} choice={choice} explainUnavailable={!origin} />;
  }

  return <>
    {onResize && <HandResizeHandle onResize={onResize} />}
    <div className={`hand-scroll${artFocused ? " art-focused" : ""}`}>
      {zoneOffers.size > 0 && <CardPages items={[...zoneOffers.values()]} label="Available from other zones"
        className="hand-other-pages" trackClassName="hand-cards hand-zone-offers" testId="hand-zone-offers"
        style={{ "--hand-card-count": zoneOffers.size } as CSSProperties}
        render={(offer, index) => renderCard(offer.card, index, offer.origin)} />}
      <CardPages items={cards} label="Hand" className="hand-main-pages" trackClassName="hand-cards"
        testId="own-hand" style={{ "--hand-card-count": Math.max(1, cards.length) } as CSSProperties}
        render={renderCard} />
    </div>
  </>;
}
