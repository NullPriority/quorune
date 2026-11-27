import {
  KeyboardEvent as ReactKeyboardEvent,
  type ReactNode,
  useId,
  useLayoutEffect,
  useRef,
  useState,
} from "react";
import { createPortal } from "react-dom";

import type { JsonValue, LegalAction } from "./generated/protocol";
import type { ProjectedView } from "./protocol";
import { ManaText, spokenMana } from "./ManaText";
import { actionTitle, cardActionCost } from "./tablePresentation";


export function asRecord(value: JsonValue | undefined): Record<string, JsonValue> {
  return value && !Array.isArray(value) && typeof value === "object" ? value : {};
}

export function asList(value: JsonValue | undefined): JsonValue[] {
  return Array.isArray(value) ? value : [];
}

export function cardName(value: JsonValue): string {
  const card = asRecord(value);
  const name = card.n ?? card.name ?? card.label;
  return name === "?" ? "Face-down card" : String(name ?? "Unknown card");
}

const languageNames = new Intl.DisplayNames(["en"], { type: "language", fallback: "none" });
const printingLanguages: Record<string, string> = {
  zhs: "Simplified Chinese", zht: "Traditional Chinese", ph: "Phyrexian",
};

function printingLanguage(language: JsonValue): string {
  if (typeof language !== "string" || !/^[a-z]{2,3}$/i.test(language)) return "";
  const key = language.toLowerCase();
  return printingLanguages[key] ?? languageNames.of(key) ?? "";
}

export function printingLabel(value: JsonValue): string {
  const printing = asRecord(asRecord(value).printing);
  const edition = String(printing.set_code ?? "").toUpperCase();
  const finishes: Record<string, string> = { foil: "Foil", nonfoil: "Nonfoil", etched: "Etched foil" };
  const finish = String(printing.finish ?? "");
  return [
    edition && printing.collector_number ? `${edition} #${printing.collector_number}` : edition,
    printingLanguage(printing.language),
    finishes[finish.toLowerCase()] ?? finish,
  ].filter(Boolean).join(" · ");
}

export function cardImageUrl(value: JsonValue, size: string, face = Number(asRecord(value).face ?? 0)): string | undefined {
  const card = asRecord(value);
  const cid = typeof card.cid === "string" ? card.cid : "";
  if (!cid || card.n === "?") return undefined;
  const query = new URLSearchParams({ size, face: String(face) });
  const printing = asRecord(card.printing);
  if (typeof printing.scryfall_id === "string") query.set("printing", printing.scryfall_id);
  else if (typeof printing.set_code === "string" && typeof printing.collector_number === "string") {
    query.set("edition", printing.set_code);
    query.set("collector", printing.collector_number);
    if (typeof printing.language === "string") query.set("language", printing.language);
  }
  return `/api/v1/cards/${cid}/image?${query}`;
}

function currentStats(card: Record<string, JsonValue>, definition: Record<string, JsonValue> = {}) {
  const power = card.p ?? (card.fd ? undefined : definition.p);
  const toughness = card.q ?? (card.fd ? undefined : definition.q);
  return power != null && toughness != null ? `${power}/${toughness}` : "";
}

export function CardTile({
  value,
  view,
  compact = false,
  targetLabel,
  table = false,
  actions = [],
  onIntent,
  onInspect,
  onOpenInspector,
  inspectOnActivate = true,
  onDragCard,
  onDropCard,
  manualMana = false,
  selected = false,
  explainUnavailable = false,
  highlight = false,
  previewOnHover = true,
  artCrop = false,
  origin,
  attachment,
  showFaceStatus = false,
  choice,
}: {
  value: JsonValue;
  view: ProjectedView;
  compact?: boolean;
  targetLabel?: (ref: string) => string;
  table?: boolean | "battlefield";
  actions?: LegalAction[];
  onIntent?: (actions: LegalAction[], card: JsonValue) => void;
  onInspect?: (card: JsonValue) => void;
  onOpenInspector?: (card: JsonValue) => void;
  inspectOnActivate?: boolean;
  onDragCard?: (cardRef: string | null) => void;
  onDropCard?: (cardRef: string) => void;
  manualMana?: boolean;
  selected?: boolean;
  explainUnavailable?: boolean;
  highlight?: boolean;
  previewOnHover?: boolean;
  artCrop?: boolean;
  origin?: string;
  attachment?: string;
  showFaceStatus?: boolean;
  choice?: { selected: boolean; select: () => void; label?: string };
}) {
  const card = asRecord(value);
  const targetRefs = asList(card.targets);
  const damageDetail = asList(card.damage_shares).map((share, slot) => {
    const ref = targetRefs[slot];
    const label = typeof ref === "string" ? targetLabel?.(ref) ?? "Target" : "Unavailable target";
    return `${share} to ${label}`;
  }).join(" · ");
  const cid = typeof card.cid === "string" && card.n !== "?" ? card.cid : "";
  const definition = cid ? view.definitions[cid] : undefined;
  const [failedImages, setFailedImages] = useState<string[]>([]);
  const [previewAnchor, setPreviewAnchor] = useState<HTMLElement | null>(null);
  const costDescription = useId();
  const loyaltyDescription = useId();
  const attachmentDescription = useId();
  const tappedDescription = useId();
  const interactive = Boolean(choice) || Boolean(onIntent) && (actions.length > 0 || explainUnavailable);
  const actionable = Boolean(choice) || Boolean(onIntent) && actions.length > 0;
  const inspectable = Boolean(onInspect || onOpenInspector);
  const ref = String(card.id ?? "");
  const face = Number(card.face ?? 0);
  const imageSizes = artCrop ? ["art_crop", "normal"] : [compact || table ? "small" : "normal"];
  const image = imageSizes.map((size) => cardImageUrl(value, size, face))
    .find((source) => source !== undefined && !failedImages.includes(source));
  const battlefield = table === "battlefield";
  const printedImage = image && (
    <img
      key={image}
      src={image}
      alt=""
      loading="lazy"
      onError={() => setFailedImages((failed) => failed.includes(image) ? failed : [...failed, image])}
    />
  );
  const stats = currentStats(card, definition);
  const counters = asRecord(card.ctr);
  const loyalty = typeof counters.loyalty === "number" ? counters.loyalty : undefined;
  const otherCounters = Object.entries(counters).filter(([key]) => key !== "loyalty");
  const damage = Number(card.dmg ?? 0);
  const canDrag = interactive && !choice && actions.some((action) => ["play_land", "cast"].includes(action.action));
  const actionKinds = new Set(actions.map((action) => action.action));
  const offerCost = !choice && actionable ? cardActionCost(actions) : "";
  const printedCost = String(card.m ?? definition?.m ?? "");
  const actionHint = choice || !actionable ? "" : manualMana
    ? actionKinds.has("undo_mana") ? "Untap" : "Tap"
    : actions.length > 1
      ? "Choose"
      : actionKinds.has("cast")
        ? "Cast"
        : actionKinds.has("activate")
          ? "Activate"
        : "Play";
  function activate() {
    onInspect?.(value);
    setPreviewAnchor(null);
    if (choice) choice.select();
    else if (interactive) onIntent?.(actions, value);
    else if (inspectOnActivate) onOpenInspector?.(value);
  }
  function keydown(event: ReactKeyboardEvent<HTMLElement>) {
    if (onOpenInspector && event.key.toLowerCase() === "i" && !event.altKey && !event.ctrlKey && !event.metaKey) {
      event.preventDefault();
      setPreviewAnchor(null);
      onOpenInspector(value);
      return;
    }
    if ((!interactive && !inspectable) || !["Enter", " "].includes(event.key)) return;
    event.preventDefault();
    activate();
  }
  return (
    <article
      className={`card-tile${compact ? " compact" : table ? " table-card" : " hand-card"}${battlefield ? " battlefield-card" : ""}${image ? " has-image" : ""}${card.tap ? " tapped" : ""}${actionable ? " actionable" : ""}${inspectable ? " inspectable" : ""}${manualMana ? " mana-source" : ""}${selected ? " selected-card" : ""}${highlight ? " target-highlight" : ""}${offerCost ? " has-offered-cost" : ""}`}
      role={interactive || inspectable ? "button" : undefined}
      tabIndex={interactive || inspectable ? 0 : undefined}
      aria-label={choice ? `Select ${choice.label ?? cardName(value)}` : actionable
        ? [actions.map((action) => spokenMana(action.label ? actionTitle(action) : `${actionHint} ${cardName(value)}`)).join(" or "), origin].filter(Boolean).join(" · ")
        : interactive || inspectable ? `Inspect ${cardName(value)}` : undefined}
      aria-describedby={[
        offerCost ? costDescription : "", loyalty === undefined ? "" : loyaltyDescription,
        attachment ? attachmentDescription : "", card.tap && table ? tappedDescription : "",
      ].filter(Boolean).join(" ") || undefined}
      aria-pressed={choice?.selected}
      aria-keyshortcuts={onOpenInspector ? "I" : undefined}
      draggable={canDrag}
      data-card-ref={ref}
      data-zone-offer={origin ? "true" : undefined}
      data-choice-candidate={choice ? "true" : undefined}
      data-tapped={card.tap ? "true" : "false"}
      data-concealed={showFaceStatus && card.n === "?" ? "true" : undefined}
      onClick={activate}
      onKeyDown={keydown}
      onContextMenu={onOpenInspector ? (event) => {
        event.preventDefault();
        setPreviewAnchor(null);
        onOpenInspector(value);
      } : undefined}
      onMouseEnter={(event) => {
        onInspect?.(value);
        if (inspectable && previewOnHover && !choice) setPreviewAnchor(event.currentTarget);
      }}
      onMouseLeave={() => setPreviewAnchor(null)}
      onFocus={(event) => {
        onInspect?.(value);
        if (inspectable && previewOnHover && !choice && event.currentTarget.matches(":focus-visible")) setPreviewAnchor(event.currentTarget);
      }}
      onBlur={() => setPreviewAnchor(null)}
      onPointerDown={(event) => {
        if (canDrag && event.button === 0) onDragCard?.(ref);
      }}
      onDragStart={(event) => {
        if (!canDrag) return;
        onDragCard?.(ref);
        event.dataTransfer.effectAllowed = "move";
        event.dataTransfer.setData("application/x-commander-card", ref);
        event.dataTransfer.setData("text/plain", ref);
      }}
      onDragEnd={(event) => {
        const battlefield = document.querySelector<HTMLElement>("[data-own-battlefield='true']");
        const bounds = battlefield?.getBoundingClientRect();
        if (
          canDrag
          && ref
          && bounds
          && event.clientX >= bounds.left
          && event.clientX <= bounds.right
          && event.clientY >= bounds.top
          && event.clientY <= bounds.bottom
        ) {
          onDropCard?.(ref);
        }
        window.setTimeout(() => onDragCard?.(null), 0);
      }}
    >
      {battlefield ? <div className="battlefield-card-art" aria-hidden="true">
        <div className="battlefield-printed-face">{printedImage}</div>
      </div> : printedImage}
      <div className="card-copy">
        <strong>{cardName(value)}</strong>
        {showFaceStatus && card.fd && card.n !== "?" && <small className="zone-face-status">Face down</small>}
        {attachment && <small id={attachmentDescription} className="attachment-label" title={`Attached to ${attachment}`}>
          <span className="attachment-prefix">Attached to </span>{attachment}
        </small>}
        {damageDetail && <span>{damageDetail}</span>}
        {!compact && (card.t ?? definition?.t) && <span>{String(card.t ?? definition?.t)}</span>}
        {otherCounters.length > 0 && <span>{otherCounters.map(([key, amount]) => `${key} ${amount}`).join(" · ")}</span>}
        {Boolean(card.prepared) && <span>PREPARED</span>}
      </div>
      {(stats || loyalty !== undefined || damage > 0) && <div className="card-stats">
        {stats && <strong data-testid="card-current-stats" aria-label={`Current power and toughness ${stats}`}>{stats}</strong>}
        {loyalty !== undefined && <strong id={loyaltyDescription} data-testid="card-current-loyalty">{loyalty} loyalty</strong>}
        {damage > 0 && <span data-testid="card-marked-damage" aria-label={`${damage} damage marked`}>{damage} dmg</span>}
      </div>}
      {card.tap && table && <span hidden id={tappedDescription}>Tapped</span>}
      {origin ? <span className="card-origin" title={origin}>{origin}</span> : actionHint && <span className="card-action-hint">{actionHint}</span>}
      {offerCost && <span id={costDescription} hidden>{spokenMana(offerCost)}</span>}
      {(offerCost || printedCost) && <span className="card-cost-overlay"
        data-testid={offerCost ? "card-offer-cost" : undefined} aria-hidden="true"><ManaText text={offerCost || printedCost} /></span>}
      {previewAnchor && <FloatingCardPreview anchor={previewAnchor} value={value} view={view} />}
    </article>
  );
}

export function CardInspector({
  value,
  view,
  onExpand,
  expanded = false,
  previewOnly = false,
}: {
  value: JsonValue | null;
  view: ProjectedView;
  onExpand?: () => void;
  expanded?: boolean;
  previewOnly?: boolean;
}) {
  const card = asRecord(value ?? undefined);
  const cid = typeof card.cid === "string" && card.n !== "?" ? card.cid : "";
  const definition = cid ? view.definitions[cid] : undefined;
  const faces = asList(definition?.faces).map(asRecord);
  const projectedFace = Number(card.face ?? 0);
  const identity = `${String(card.id ?? cid)}:${projectedFace}`;
  const [faceChoice, setFaceChoice] = useState<{ identity: string; index: number } | null>(null);
  const [failedImage, setFailedImage] = useState<string | null>(null);
  const faceIndex = faceChoice?.identity === identity ? faceChoice.index : projectedFace;
  const image = value == null ? undefined : cardImageUrl(value, "large", faceIndex);

  if (!value || !Object.keys(card).length) {
    return (
      <section className={`card-inspector-panel${expanded ? " expanded" : ""}`} data-testid={expanded ? "card-inspector-expanded" : "card-inspector"}>
        <div className="card-inspector-empty">
          <span className="eyebrow">CARD VIEWER</span>
          <strong>Point at a card</strong>
          <p>Hover, focus, or select any visible card to read it here.</p>
        </div>
      </section>
    );
  }

  const face = faces[faceIndex] ?? {};
  const isCurrentFace = faceIndex === projectedFace;
  const name = isCurrentFace ? cardName(value) : String(face.n ?? cardName(value));
  const mana = String((isCurrentFace ? card.m : face.m) ?? definition?.m ?? "");
  const typeLine = String((isCurrentFace ? card.t : face.t) ?? definition?.t ?? "");
  const oracle = card.n === "?" ? "This card’s identity is hidden." :
    String((isCurrentFace ? card.o : face.o) ?? definition?.o ?? "No rules text available.");
  const stats = isCurrentFace ? currentStats(card, definition) : "";
  const counters = Object.entries(asRecord(card.ctr));
  return (
    <section className={`card-inspector-panel${expanded ? " expanded" : ""}`} data-testid={previewOnly ? "hover-card-preview" : expanded ? "card-inspector-expanded" : "card-inspector"}>
      <header>
        <div><span className="eyebrow">CARD VIEWER</span><strong>{name}</strong></div>
        {onExpand && <button type="button" className="link-button" onClick={onExpand}>Enlarge</button>}
      </header>
      <div className="inspector-art">
        {image && image !== failedImage ? (
          <img
            key={image}
            src={image}
            alt={name}
            onError={() => setFailedImage(image)}
          />
        ) : (
          <div className="inspector-art-fallback"><strong>{name}</strong><span>{typeLine}</span>
            {printingLabel(value) && <span>Printing art unavailable</span>}
          </div>
        )}
      </div>
      {!previewOnly && faces.length > 1 && (
        <div className="face-switcher" aria-label="Card faces">
          {faces.map((candidate, index) => (
            <button
              type="button"
              key={`${String(candidate.n ?? index)}-${index}`}
              className={faceIndex === index ? "active" : ""}
              aria-pressed={faceIndex === index}
              onClick={() => { setFaceChoice({ identity, index }); setFailedImage(null); }}
            >
              {String(candidate.n ?? `Face ${index + 1}`)}
            </button>
          ))}
        </div>
      )}
      <div className="inspector-text">
        <div><strong>{name}</strong>{mana && <ManaText text={mana} />}</div>
        {typeLine && <small>{typeLine}</small>}
        {printingLabel(value) && <small className="printing-label">{printingLabel(value)}</small>}
        {isCurrentFace && <div className="inspector-condition">
          {stats && <strong>Current {stats}</strong>}
          {card.dmg ? <span>{String(card.dmg)} damage marked</span> : null}
          {card.tap ? <span>Tapped</span> : null}
          {counters.map(([key, count]) => <span key={key}>{String(count)} × {key} {Number(count) === 1 ? "counter" : "counters"}</span>)}
        </div>}
        <p>{oracle}</p>
      </div>
    </section>
  );
}

export function FloatingTablePreview({
  anchor, children,
}: {
  anchor: HTMLElement;
  children: ReactNode;
}) {
  const [position, setPosition] = useState({ left: 0, top: 0, width: 0, maxHeight: 0 });
  const previewRef = useRef<HTMLDivElement>(null);
  useLayoutEffect(() => {
    function place() {
      const bounds = anchor.getBoundingClientRect();
      const width = Math.max(0, Math.min(280, window.innerWidth - 24));
      const maxHeight = Math.max(0, window.innerHeight - 24);
      const height = Math.min(previewRef.current?.scrollHeight || maxHeight, maxHeight);
      const x = bounds.right + width + 18 <= window.innerWidth
        ? bounds.right + 10 : bounds.left - width - 10;
      setPosition({
        left: Math.min(window.innerWidth - width - 12, Math.max(12, x)),
        top: Math.max(12, Math.min(window.innerHeight - height - 12, bounds.top - 12)),
        width,
        maxHeight,
      });
    }
    const observer = new ResizeObserver(place);
    if (previewRef.current) observer.observe(previewRef.current);
    place();
    window.addEventListener("resize", place);
    window.addEventListener("scroll", place, true);
    return () => {
      observer.disconnect();
      window.removeEventListener("resize", place);
      window.removeEventListener("scroll", place, true);
    };
  }, [anchor]);
  return createPortal(<div ref={previewRef} className="floating-card-preview" style={position} aria-hidden="true">
    {children}
  </div>, document.body);
}

function FloatingCardPreview({ anchor, value, view }: {
  anchor: HTMLElement;
  value: JsonValue;
  view: ProjectedView;
}) {
  return <FloatingTablePreview anchor={anchor}>
    <CardInspector value={value} view={view} previewOnly />
  </FloatingTablePreview>;
}
