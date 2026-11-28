import { type RefObject, useState } from "react";
import { asList, asRecord, cardImageUrl, cardName, CardInspector, CardTile, FloatingTablePreview } from "./CardSurface";
import { CardPages } from "./CardPages";
import type { JsonValue, LegalAction } from "./generated/protocol";
import type { ProjectedView } from "./protocol";
import type { TableObjectSelection } from "./PublicActionDraft";
import { SeatLabel } from "./SeatLabel";

const labels = { known_top: "Library", gy: "Graveyard", ex: "Exile", known_hand: "Revealed hand" } as const;
export type TableZone = keyof typeof labels;
export type OpenTableZone = { seat: string; zone: TableZone };

const zones = ["known_top", "gy", "ex"] as const;
const dockZones = ["ex", "known_top", "gy"] as const;

function zoneCards(player: Record<string, JsonValue>, zone: TableZone) {
  const cards = asList(player[zone]);
  return zone === "gy" ? [...cards].reverse() : cards;
}

function zoneCount(player: Record<string, JsonValue>, zone: TableZone, cards: JsonValue[]) {
  return zone === "known_top" ? Number(player.lib_n ?? 0) : cards.length;
}

function zoneCardLabel(card: JsonValue): string {
  const data = asRecord(card);
  return data.fd && data.n !== "?" ? `${cardName(card)} · Face down` : cardName(card);
}

export function ZonePiles({ seat, player, hidden, selection, highlightedTargets, onOpen, localDock = false }: {
  seat: string;
  player: Record<string, JsonValue>;
  hidden: boolean;
  selection?: TableObjectSelection | null;
  highlightedTargets: string[];
  onOpen: (seat: string, zone: TableZone) => void;
  localDock?: boolean;
}) {
  const [collapsed, setCollapsed] = useState(false);
  const [peek, setPeek] = useState<{ zone: TableZone; anchor: HTMLElement } | null>(null);
  const [failedArt, setFailedArt] = useState<string[]>([]);
  const container = `seat-${seat}-zone-piles`;
  return <div className={`seat-zones${collapsed ? " collapsed" : ""}${localDock ? " local-zone-dock" : ""}`}>
    <div className="zone-piles" id={container} role="group" aria-label={`Seat ${seat} card zones`}>
      {(localDock ? dockZones : zones).map((zone) => {
        const cards = zoneCards(player, zone);
        const count = zoneCount(player, zone, cards);
        const title = labels[zone];
        const representative = zone === "ex" ? cards.find((card) => !asRecord(card).fd) ?? cards[0] : cards[0];
        const subtitle = representative ? zoneCardLabel(representative) : count === 0 ? "Empty" : "";
        const art = representative && cardImageUrl(representative, "small");
        const targeted = cards.some((card) => {
          const ref = String(asRecord(card).id ?? "");
          return selection?.refs.includes(ref) || highlightedTargets.includes(ref);
        });
        return <button type="button" key={zone} className={`zone-pile${targeted ? " zone-target" : ""}`}
          data-testid={`zone-${seat}-${title.toLowerCase()}`} data-target-zone={`${seat}-${zone}`}
          aria-label={`${title} ${count}`} aria-haspopup="dialog" disabled={zone !== "known_top" && count === 0}
          onMouseEnter={(event) => setPeek({ zone, anchor: event.currentTarget })}
          onMouseLeave={() => setPeek(null)}
          onFocus={(event) => {
            if (event.currentTarget.matches(":focus-visible")) setPeek({ zone, anchor: event.currentTarget });
          }}
          onBlur={() => setPeek(null)}
          onClick={() => { setPeek(null); onOpen(seat, zone); }}>
          {!collapsed && <span className={`zone-pile-thumbnail${count === 0 ? " empty" : ""}`} aria-hidden="true">
            {art && !failedArt.includes(art) && <img key={art} src={art} alt="" loading="lazy"
              onError={() => setFailedArt((prior) => prior.includes(art) ? prior : [...prior, art])} />}
          </span>}
          <span className="zone-pile-copy">
            <span>{title} <strong>{count}</strong></span>
            {!collapsed && subtitle && <small>{subtitle}</small>}
          </span>
        </button>;
      })}
    </div>
    {!localDock && <button type="button" className="zone-pile-toggle link-button" aria-controls={container}
      aria-expanded={!collapsed} aria-label={collapsed ? "Expand zones" : "Collapse zones"}
      title={collapsed ? "Expand zones" : "Collapse zones"}
      onClick={() => { setPeek(null); setCollapsed((current) => !current); }}>
      {collapsed ? "+" : "−"}
    </button>}
    {peek && !hidden && <ZonePeek anchor={peek.anchor} player={player} zone={peek.zone} />}
  </div>;
}

function ZonePeek({ anchor, player, zone }: {
  anchor: HTMLElement;
  player: Record<string, JsonValue>;
  zone: TableZone;
}) {
  const cards = zoneCards(player, zone);
  const shown = cards.slice(0, 4);
  if (!shown.length) return null;
  const count = zoneCount(player, zone, cards);
  return <FloatingTablePreview anchor={anchor}>
    <div className="zone-peek" data-testid="zone-peek">
      <strong>{labels[zone]} · {count}</strong>
      {zone === "known_top" && <small>Visible top cards</small>}
      <ul>
        {shown.map((card, index) => <li key={String(asRecord(card).id ?? index)}>{zoneCardLabel(card)}</li>)}
      </ul>
      {cards.length > shown.length && <small>{cards.length - shown.length} more</small>}
    </div>
  </FloatingTablePreview>;
}

export function ZoneBrowser({ opened, player, dialogRef, view, selection, selectedCardRef, actionDisabled,
  actionsForCard, onIntent, onClose }: {
  opened: OpenTableZone;
  player: Record<string, JsonValue>;
  dialogRef: RefObject<HTMLElement | null>;
  view: ProjectedView;
  selection: TableObjectSelection | null;
  selectedCardRef: string;
  actionDisabled: boolean;
  actionsForCard: (cardRef: string) => LegalAction[];
  onIntent: (actions: LegalAction[], card: JsonValue) => void;
  onClose: () => void;
}) {
  const { seat, zone } = opened;
  const cards = zoneCards(player, zone);
  const title = labels[zone];
  const count = zoneCount(player, zone, cards);
  const [previewRef, setPreviewRef] = useState("");
  const currentPreview = cards.find((card) => asRecord(card).id === previewRef) ?? cards[0] ?? null;
  const canChoose = cards.some((card) => selection?.refs.includes(String(asRecord(card).id ?? "")));
  return <div className="choice-overlay" role="presentation" onMouseDown={(event) => {
    if (event.target === event.currentTarget) onClose();
  }}>
    <section ref={dialogRef} className="choice-dialog zone-browser" data-seat={seat} role="dialog"
      aria-modal="true" aria-labelledby="zone-browser-title" data-testid="zone-browser">
      <header>
        <div>
          <SeatLabel seat={seat} /><h2 id="zone-browser-title">{title} · {count}</h2>
          {zone === "known_top" && cards.length > 0 && <p>Visible top cards</p>}
        </div>
        <button type="button" className="secondary-button" onClick={onClose}>{canChoose ? "Back to table" : "Close"}</button>
      </header>
      {count === 0 && <p className="zone-empty">Empty</p>}
      {cards.length > 0 && <div className="zone-browser-layout">
        <CardPages items={cards} className={`zone-card-pages ${zone === "gy" ? "graveyard" : zone === "ex" ? "exile" : "library"}-pages`}
          label={title} trackClassName="zone-card-grid" layout="grid" render={(card, index) => {
            const ref = String(asRecord(card).id ?? "");
            const actions = !selection && seat === view.principal.split(":").at(-1) ? actionsForCard(ref) : [];
            return <CardTile key={ref || index} value={card} view={view} actions={actions} showFaceStatus showPrintedCost={false}
              previewOnHover={false} onInspect={() => setPreviewRef(ref)}
              onIntent={onIntent}
              selected={selectedCardRef === ref || selection?.selected.includes(ref)}
              choice={!actionDisabled && selection?.refs.includes(ref) ? {
                selected: selection.selected.includes(ref), select: () => selection.select(ref),
              } : undefined} />;
          }} />
        <CardInspector value={currentPreview} view={view} expanded />
      </div>}
    </section>
  </div>;
}
