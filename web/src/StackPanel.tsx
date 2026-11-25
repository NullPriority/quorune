import { asList, asRecord, CardTile } from "./CardSurface";
import type { JsonValue } from "./generated/protocol";
import type { ProjectedView } from "./protocol";
import type { TableObjectSelection } from "./PublicActionDraft";

function modeLabel(mode: JsonValue, index: number): string {
  if (typeof mode !== "string" || !mode.trim() || /^[a-f\d-]{20,}$/i.test(mode)) return `Mode ${index + 1}`;
  const text = mode.replace(/[_-]/g, " ").replace(/^mode(\d+)/i, "mode $1").trim();
  return text ? text[0].toUpperCase() + text.slice(1) : `Mode ${index + 1}`;
}

export function StackPanel({ stack, view, labelFor, targets, onTargets, onInspect, onOpenInspector, selection }: {
  stack: JsonValue[];
  view: ProjectedView;
  labelFor: (ref: string) => string;
  targets: string[];
  onTargets: (source: string, refs: string[]) => void;
  onInspect: (card: JsonValue) => void;
  onOpenInspector: (card: JsonValue) => void;
  selection?: TableObjectSelection | null;
}) {
  return <section className="stack-panel" data-testid="stack-panel">
    <header><strong>Stack</strong><span>{stack.length}</span></header>
    <div className="stack-items">
      {stack.length > 0 ? stack.map((item, index) => {
        const object = asRecord(item);
        const refs = asList(object.targets).map(String);
        const modes = asList(object.modes).map(modeLabel);
        const id = String(object.id ?? index);
        return <article className={`stack-row${index === 0 ? " resolves-next" : ""}`}
          key={id} onMouseEnter={() => onTargets(id, refs)} onMouseLeave={() => onTargets("", [])}
          onFocus={() => onTargets(id, refs)} onBlur={(event) => {
            if (!event.currentTarget.contains(event.relatedTarget)) onTargets("", []);
          }}>
          <small>{index === 0 ? "Next to resolve · " : ""}
            {typeof object.ctl === "string" ? `Seat ${object.ctl} · ` : ""}
            {typeof object.kind === "string" ? object.kind.replaceAll("_", " ") : "Stack item"}
          </small>
          <CardTile value={item} view={view} compact targetLabel={labelFor}
            onInspect={onInspect} highlight={targets.includes(id)} previewOnHover={refs.length === 0}
            selected={selection?.selected.includes(id)}
            choice={selection?.refs.includes(id) ? { selected: selection.selected.includes(id), select: () => selection.select(id) } : undefined} />
          {object.label && object.label !== object.n ? <strong>{String(object.label)}</strong> : null}
          {refs.length > 0 && <p>Targets {refs.map(labelFor).join(", ")}</p>}
          {modes.length > 0 && <p>{modes.join(" · ")}</p>}
          {object.x != null && <p>X = {String(object.x)}</p>}
          <button type="button" className="link-button" onClick={() => onOpenInspector(item)}>Inspect</button>
        </article>;
      }) : <em>The stack is empty</em>}
    </div>
  </section>;
}
