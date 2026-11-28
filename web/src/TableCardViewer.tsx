import { useCompanionWindow } from "./useCompanionWindow";
import { createPortal } from "react-dom";
import { CardInspector } from "./CardSurface";
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
