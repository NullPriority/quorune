import { Fragment, useLayoutEffect, useRef, useState } from "react";
import { asList, asRecord, CardTile } from "./CardSurface";
import { CardPageControls } from "./CardPages";
import type { JsonValue } from "./generated/protocol";
import type { ProjectedView } from "./protocol";
import type { TableObjectSelection } from "./PublicActionDraft";
import { modeLabel } from "./tablePresentation";
import { SeatLabel, tableSeat } from "./SeatLabel";

export function StackPanel({ stack, view, labelFor, positions, targets, onFocusItem, onMinimize, onInspect, onOpenInspector, selection }: {
  stack: JsonValue[];
  view: ProjectedView;
  labelFor: (ref: string) => string;
  positions: ReadonlyMap<string, number>;
  targets: string[];
  onFocusItem: (ref: string) => void;
  onMinimize: () => void;
  onInspect: (card: JsonValue) => void;
  onOpenInspector: (card: JsonValue) => void;
  selection?: TableObjectSelection | null;
}) {
  const rows = useRef(new Map<string, HTMLLIElement>());
  const list = useRef<HTMLOListElement>(null);
  const focusAfterPaging = useRef<{ ref: string; source: string } | null>(null);
  const [pageStarts, setPageStarts] = useState<number[]>([0]);
  const [pageAnchor, setPageAnchor] = useState(0);
  const ids = stack.map((item, index) => String(asRecord(item).id ?? index));
  const order = ids.join(",");
  const top = ids[0];
  const pages = pageStarts.filter((offset) => offset === 0 || offset < stack.length);
  const page = pages.filter((offset) => offset <= pageAnchor).length - 1;
  const first = pages[page];
  const last = pages[page + 1] ?? stack.length;
  const incoming = new Map<string, string[]>();
  const controllers = new Map<string, string>();
  for (const item of stack) {
    const object = asRecord(item);
    const controller = tableSeat(object.ctl);
    if (controller) controllers.set(String(object.id), controller);
    for (const ref of new Set(asList(object.targets).map(String))) {
      if (!positions.has(ref)) continue;
      const sources = incoming.get(ref) ?? [];
      sources.push(String(object.id));
      incoming.set(ref, sources);
    }
  }

  useLayoutEffect(() => setPageAnchor(0), [top]);

  useLayoutEffect(() => {
    const viewport = list.current;
    if (!viewport) return;
    const update = () => {
      if (!viewport.clientHeight) return;
      const style = getComputedStyle(viewport);
      const available = viewport.clientHeight - (parseFloat(style.paddingTop) || 0) - (parseFloat(style.paddingBottom) || 0);
      const gap = parseFloat(style.rowGap) || 0;
      const starts = [0];
      let used = 0;
      order.split(",").forEach((ref, index) => {
        const height = rows.current.get(ref)?.offsetHeight ?? 0;
        if (used > 0 && used + gap + height > available + 0.5) {
          starts.push(index);
          used = 0;
        }
        used += height + (used > 0 ? gap : 0);
      });
      setPageStarts((current) => current.length === starts.length && current.every((start, index) => start === starts[index])
        ? current : starts);
    };
    const observer = new ResizeObserver(update);
    observer.observe(viewport);
    rows.current.forEach((row) => observer.observe(row));
    update();
    return () => observer.disconnect();
  }, [order]);

  useLayoutEffect(() => {
    const focus = focusAfterPaging.current;
    if (!focus) return;
    const row = rows.current.get(focus.ref);
    if (row && !row.classList.contains("stack-off-page")) {
      row.focus({ preventScroll: true });
      onFocusItem(focus.source);
      focusAfterPaging.current = null;
    }
  }, [first, last, onFocusItem]);

  function showItem(ref: string, source: string) {
    const row = rows.current.get(ref);
    const position = positions.get(ref);
    if (!row || !position) return;
    if (position > first && position <= last) {
      row.focus({ preventScroll: true });
      onFocusItem(source);
    } else {
      focusAfterPaging.current = { ref, source };
      setPageAnchor(position - 1);
    }
  }

  function links(refs: string[], source: string, reverse = false) {
    return refs.map((ref, index) => <Fragment key={`${ref}:${index}`}>
      {index > 0 && ", "}
      {positions.has(ref) ? <button type="button" className="stack-link" data-seat={controllers.get(ref)}
        onClick={() => showItem(ref, reverse ? ref : source)}>{labelFor(ref)}</button>
        : tableSeat(ref) ? <SeatLabel seat={ref} /> : labelFor(ref)}
    </Fragment>);
  }

  return <section className="stack-panel" id="match-stack" aria-label="Stack" data-testid="stack-panel">
    <header>
      <strong>Stack</strong>
      <button type="button" className="link-button" aria-label="Minimize stack" onClick={onMinimize}>Minimize</button>
    </header>
    <CardPageControls label="Stack" page={page} pages={pages.length} onPage={(next) => setPageAnchor(pages[next])} />
    {stack.length > 0 ? <ol ref={list} className="stack-items" aria-label="Next to resolve first">
      {stack.map((item, index) => {
        const object = asRecord(item);
        const refs = asList(object.targets).map(String);
        const modes = asList(object.modes).map(modeLabel);
        const id = String(object.id ?? index);
        const sources = incoming.get(id) ?? [];
        const kind = typeof object.kind === "string" ? object.kind.replaceAll("_", " ") : "stack item";
        const offPage = index < first || index >= last;
        return <li className={`stack-row${index === 0 ? " resolves-next" : ""}${targets.includes(id) ? " targeted-stack-row" : ""}${offPage ? " stack-off-page" : ""}`}
          key={id} tabIndex={-1} aria-hidden={offPage || undefined} data-stack-ref={id} data-seat={controllers.get(id)} ref={(node) => {
            if (node) rows.current.set(id, node);
            else rows.current.delete(id);
          }}
          onMouseEnter={() => onFocusItem(refs.length ? id : "")}
          onMouseLeave={() => onFocusItem("")}
          onFocus={() => onFocusItem(refs.length ? id : "")}
          onBlur={(event) => {
            if (!event.currentTarget.contains(event.relatedTarget)) onFocusItem("");
          }}>
          <div className="stack-row-caption">
            <span className="stack-position">#{index + 1}</span>
            <SeatLabel seat={controllers.get(id)} />
            <small>{kind}</small>
            {index === 0 && <strong>Next to resolve</strong>}
          </div>
          <CardTile value={item} view={view} compact targetLabel={labelFor}
            onInspect={onInspect} onOpenInspector={onOpenInspector} inspectOnActivate={!selection}
            highlight={targets.includes(id)} previewOnHover={refs.length === 0}
            selected={selection?.selected.includes(id)}
            choice={selection?.refs.includes(id) ? {
              label: labelFor(id), selected: selection.selected.includes(id), select: () => selection.select(id),
            } : undefined} />
          {object.label && object.label !== object.n
            && !(object.kind === "spell_copy" && object.label === `${object.n} copy`)
            ? <strong>{String(object.label)}</strong> : null}
          {refs.length > 0 && <p className="stack-relationship">Targets {links(refs, id)}</p>}
          {sources.length > 0 && <p className="stack-relationship">Targeted by {links(sources, id, true)}</p>}
          {modes.length > 0 && <p>{modes.join(" · ")}</p>}
          {object.x != null && <p>X = {String(object.x)}</p>}
        </li>;
      })}
    </ol> : <p className="stack-empty">The stack is empty</p>}
  </section>;
}
