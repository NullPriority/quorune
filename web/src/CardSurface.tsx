import {
  KeyboardEvent as ReactKeyboardEvent,
  useEffect,
  useState,
} from "react";

import type { JsonValue, LegalAction } from "./generated/protocol";
import type { ProjectedView } from "./protocol";


export function asRecord(value: JsonValue | undefined): Record<string, JsonValue> {
  return value && !Array.isArray(value) && typeof value === "object" ? value : {};
}

export function asList(value: JsonValue | undefined): JsonValue[] {
  return Array.isArray(value) ? value : [];
}

export function cardName(value: JsonValue): string {
  const card = asRecord(value);
  return String(card.n ?? card.name ?? card.id ?? "Unknown card");
}

export function CardTile({
  value,
  view,
  compact = false,
  table = false,
  actions = [],
  onIntent,
  onInspect,
  onDragCard,
  onDropCard,
  manualMana = false,
  selected = false,
  explainUnavailable = false,
}: {
  value: JsonValue;
  view: ProjectedView;
  compact?: boolean;
  table?: boolean;
  actions?: LegalAction[];
  onIntent?: (actions: LegalAction[], card: JsonValue) => void;
  onInspect?: (card: JsonValue) => void;
  onDragCard?: (cardRef: string | null) => void;
  onDropCard?: (cardRef: string) => void;
  manualMana?: boolean;
  selected?: boolean;
  explainUnavailable?: boolean;
}) {
  const card = asRecord(value);
  const cid = typeof card.cid === "string" ? card.cid : "";
  const definition = cid ? view.definitions[cid] : undefined;
  const [showImage, setShowImage] = useState(Boolean(cid));
  const interactive = Boolean(onIntent) && (actions.length > 0 || explainUnavailable);
  const inspectable = Boolean(onInspect);
  const ref = String(card.id ?? "");
  const face = Number(card.face ?? 0);
  const canDrag = interactive && actions.some((action) => ["play_land", "cast"].includes(action.action));
  const actionKinds = new Set(actions.map((action) => action.action));
  const actionHint = manualMana
    ? actionKinds.has("undo_mana") ? "UNTAP" : "TAP"
    : actionKinds.size === 0
      ? "INFO"
      : actionKinds.size > 1
      ? "CHOOSE"
      : actionKinds.has("cast")
        ? "CAST"
        : actionKinds.has("activate")
          ? "ACTIVATE"
        : "PLAY";
  function activate() {
    onInspect?.(value);
    if (interactive) onIntent?.(actions, value);
  }
  function keydown(event: ReactKeyboardEvent<HTMLElement>) {
    if ((!interactive && !inspectable) || !["Enter", " "].includes(event.key)) return;
    event.preventDefault();
    activate();
  }
  return (
    <article
      className={`card-tile${compact ? " compact" : table ? " table-card" : " hand-card"}${showImage ? " has-image" : ""}${card.tap ? " tapped" : ""}${interactive ? " actionable" : ""}${inspectable ? " inspectable" : ""}${manualMana ? " mana-source" : ""}${selected ? " selected-card" : ""}`}
      title={String(card.o ?? definition?.o ?? cardName(value))}
      role={interactive || inspectable ? "button" : undefined}
      tabIndex={interactive || inspectable ? 0 : undefined}
      aria-label={interactive ? `${actions.map((action) => action.label ?? action.action).join(" or ")}: ${cardName(value)}` : inspectable ? `Inspect ${cardName(value)}` : undefined}
      draggable={canDrag}
      data-card-ref={ref}
      data-tapped={card.tap ? "true" : "false"}
      onClick={activate}
      onKeyDown={keydown}
      onMouseEnter={() => onInspect?.(value)}
      onFocus={() => onInspect?.(value)}
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
      {showImage && (
        <img
          src={`/api/v1/cards/${cid}/image?size=${compact || table ? "small" : "normal"}&face=${face}`}
          alt=""
          loading="lazy"
          onError={() => setShowImage(false)}
        />
      )}
      <div className="card-copy">
        <small>{String(card.m ?? definition?.m ?? card.id ?? "")}</small>
        <strong>{cardName(value)}</strong>
        {!compact && (card.t ?? definition?.t) && <span>{String(card.t ?? definition?.t)}</span>}
        {card.ctr && <span>{Object.entries(asRecord(card.ctr)).map(([key, amount]) => `${key} ${amount}`).join(" · ")}</span>}
        {Boolean(card.prepared) && <span>PREPARED</span>}
      </div>
      {card.tap && table && <span className="tapped-state">TAPPED</span>}
      {interactive && <span className="card-action-hint">{actionHint}</span>}
    </article>
  );
}

export function CardInspector({
  value,
  view,
  onExpand,
  expanded = false,
}: {
  value: JsonValue | null;
  view: ProjectedView;
  onExpand?: () => void;
  expanded?: boolean;
}) {
  const card = asRecord(value ?? undefined);
  const cid = typeof card.cid === "string" ? card.cid : "";
  const definition = cid ? view.definitions[cid] : undefined;
  const faces = asList(definition?.faces).map(asRecord);
  const projectedFace = Number(card.face ?? 0);
  const identity = `${String(card.id ?? cid)}:${projectedFace}`;
  const [faceIndex, setFaceIndex] = useState(projectedFace);
  const [showImage, setShowImage] = useState(Boolean(cid));

  useEffect(() => {
    setFaceIndex(projectedFace);
    setShowImage(Boolean(cid));
  }, [identity, cid, projectedFace]);

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
  const name = String(face.n ?? card.n ?? definition?.n ?? card.id ?? "Unknown card");
  const mana = String(face.m ?? card.m ?? definition?.m ?? "");
  const typeLine = String(face.t ?? card.t ?? definition?.t ?? "");
  const oracle = String(face.o ?? card.o ?? definition?.o ?? "No projected rules text.");
  return (
    <section className={`card-inspector-panel${expanded ? " expanded" : ""}`} data-testid={expanded ? "card-inspector-expanded" : "card-inspector"}>
      <header>
        <div><span className="eyebrow">CARD VIEWER</span><strong>{name}</strong></div>
        {onExpand && <button type="button" className="link-button" onClick={onExpand}>Enlarge</button>}
      </header>
      <div className="inspector-art">
        {showImage && cid ? (
          <img
            src={`/api/v1/cards/${cid}/image?size=large&face=${faceIndex}`}
            alt={name}
            onError={() => setShowImage(false)}
          />
        ) : (
          <div className="inspector-art-fallback"><strong>{name}</strong><span>{typeLine}</span></div>
        )}
      </div>
      {faces.length > 1 && (
        <div className="face-switcher" aria-label="Card faces">
          {faces.map((candidate, index) => (
            <button
              type="button"
              key={`${String(candidate.n ?? index)}-${index}`}
              className={faceIndex === index ? "active" : ""}
              aria-pressed={faceIndex === index}
              onClick={() => { setFaceIndex(index); setShowImage(Boolean(cid)); }}
            >
              {String(candidate.n ?? `Face ${index + 1}`)}
            </button>
          ))}
        </div>
      )}
      <div className="inspector-text">
        <div><strong>{name}</strong><span>{mana}</span></div>
        {typeLine && <small>{typeLine}</small>}
        <p>{oracle}</p>
      </div>
    </section>
  );
}
