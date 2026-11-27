import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { CardInspector } from "./CardSurface";
import type { JsonValue } from "./generated/protocol";
import type { ProjectedView } from "./protocol";

export function TableCardViewer({ value, view, collapsed, onToggle, onExpand }: {
  value: JsonValue | null;
  view: ProjectedView;
  collapsed: boolean;
  onToggle: () => void;
  onExpand: () => void;
}) {
  const companion = useRef<Window | null>(null);
  const [destination, setDestination] = useState<HTMLElement | null>(null);
  useEffect(() => {
    const close = () => companion.current?.close();
    window.addEventListener("pagehide", close);
    return () => {
      window.removeEventListener("pagehide", close);
      close();
    };
  }, []);

  function openViewer() {
    if (companion.current && !companion.current.closed) {
      companion.current.focus();
      return;
    }
    const viewer = window.open("", "_blank", "popup,width=540,height=840");
    if (!viewer) return;
    companion.current = viewer;
    viewer.document.title = "Quorune card viewer";
    for (const source of document.querySelectorAll<HTMLLinkElement | HTMLStyleElement>("link[rel=stylesheet], style")) {
      const style = source.cloneNode(true) as HTMLLinkElement | HTMLStyleElement;
      if (style instanceof HTMLLinkElement && source instanceof HTMLLinkElement) style.href = source.href;
      viewer.document.head.append(style);
    }
    const host = viewer.document.createElement("main");
    host.className = "popped-card-viewer";
    viewer.document.body.append(host);
    viewer.addEventListener("pagehide", () => {
      if (companion.current === viewer) {
        companion.current = null;
        setDestination(null);
      }
    }, { once: true });
    setDestination(host);
  }

  return <section className="table-card-viewer" aria-label="Card viewer">
    <div className="table-viewer-tools" data-testid={collapsed ? "card-inspector-collapsed" : undefined}>
      <button type="button" className="link-button" aria-expanded={!collapsed} onClick={onToggle}>
        {collapsed ? "Open card viewer" : "Collapse viewer"}
      </button>
      <button type="button" className="link-button" onClick={openViewer}>Pop out viewer</button>
    </div>
    {!collapsed && <CardInspector value={value} view={view} onExpand={onExpand} />}
    {destination && createPortal(<CardInspector value={value} view={view} expanded />, destination)}
  </section>;
}
