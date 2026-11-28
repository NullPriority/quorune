import { useCompanionWindow } from "./useCompanionWindow";
import { createPortal } from "react-dom";
import { useEffect, useState } from "react";
import { asList, asRecord, cardName, CardInspector } from "./CardSurface";
import type { JsonValue } from "./generated/protocol";
import type { ProjectedView } from "./protocol";

export function TableCardViewer({ value, view, collapsed, onToggle, onExpand, onPopOut }: {
  value: JsonValue | null;
  view: ProjectedView;
  collapsed: boolean;
  onToggle: () => void;
  onExpand: () => void;
  onPopOut: () => void;
}) {
  const { host, open } = useCompanionWindow("Quorune card viewer", "width=540,height=840");

  return <section className="table-card-viewer" aria-label="Card viewer">
    <div className="table-viewer-tools" data-testid={collapsed ? "card-inspector-collapsed" : undefined}>
      <button type="button" className="link-button" aria-expanded={!collapsed} onClick={onToggle}>
        {collapsed ? "Open card viewer" : "Collapse viewer"}
      </button>
      <button type="button" className="link-button" onClick={() => { if (open() && !host) onPopOut(); }}>Pop out viewer</button>
    </div>
    {!collapsed && !host && <CardInspector value={value} view={view} onExpand={onExpand} />}
    {host && createPortal(<main className="popped-card-viewer"><CardInspector value={value} view={view} expanded /></main>, host)}
  </section>;
}


export function usePinnedTableViewer(view: ProjectedView | null, visible: Map<string, JsonValue>, inspected: JsonValue | null) {
  const [ref, setRef] = useState<string | null>(null);
  const stack = asList(view?.state.stack);
  const findVisible = (cardRef: string) => visible.get(cardRef) ?? stack.find((card) => asRecord(card).id === cardRef);
  const pinned = ref ? findVisible(ref) : undefined;
  const hovered = asRecord(inspected ?? undefined).id;
  const ownSeat = view?.principal.startsWith("pilot:") ? view.principal.slice(6) : "";
  const fallback = asList(asRecord(asRecord(view?.state.players)[ownSeat]).hand)[0];
  const selected = typeof hovered === "string" && findVisible(hovered) ? hovered : asRecord(fallback).id;

  useEffect(() => {
    let plainAlt = false;
    const modalOpen = () => Boolean(document.querySelector("dialog[open], [aria-modal='true']"));
    function keydown(event: KeyboardEvent) {
      if (event.key === "Escape" && ref && !modalOpen()) {
        event.preventDefault();
        setRef(null);
      }
      if (event.key !== "Alt") {
        plainAlt = false;
        return;
      }
      if (event.repeat) return;
      plainAlt = !event.ctrlKey && !event.metaKey && !event.shiftKey && !event.isComposing && !modalOpen();
    }
    function keyup(event: KeyboardEvent) {
      if (event.key !== "Alt") return;
      if (plainAlt && (ref || typeof selected === "string")) {
        event.preventDefault();
        setRef(ref ? null : typeof selected === "string" ? selected : null);
      }
      plainAlt = false;
    }
    const cancel = () => { plainAlt = false; };
    window.addEventListener("keydown", keydown);
    window.addEventListener("keyup", keyup);
    window.addEventListener("blur", cancel);
    return () => {
      window.removeEventListener("keydown", keydown);
      window.removeEventListener("keyup", keyup);
      window.removeEventListener("blur", cancel);
    };
  }, [ref, selected]);

  const close = () => setRef(null);
  return { pinned, close };
}

export function PinnedTableViewer({ value, view, onClose }: {
  value: JsonValue;
  view: ProjectedView;
  onClose: () => void;
}) {
  return <aside className="pinned-table-viewer" aria-label={["Pinned card viewer", cardName(value)].join(" · ")}>
    <div className="pinned-viewer-toolbar">
      <strong>Pinned card viewer</strong>
      <button type="button" className="secondary-button" aria-keyshortcuts="Alt" onClick={onClose}>Unpin</button>
    </div>
    <CardInspector value={value} view={view} />
  </aside>;
}
