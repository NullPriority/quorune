import { type CSSProperties, type ReactNode, useLayoutEffect, useRef, useState } from "react";

type PageLayout = "row" | "column" | "grid";

function fits(space: number, size: number, gap: number): number {
  return size > 0 ? Math.max(1, Math.floor((space + gap + 0.5) / (size + gap))) : 1;
}

function useCardPages(count: number, layout: PageLayout) {
  const track = useRef<HTMLDivElement>(null);
  const [first, setFirst] = useState(0);
  const [capacity, setCapacity] = useState(1);
  const page = Math.floor(Math.min(first, Math.max(0, count - 1)) / capacity);
  const pages = Math.max(1, Math.ceil(count / capacity));
  const start = page * capacity;

  useLayoutEffect(() => {
    const viewport = track.current;
    if (!viewport) return;
    let observedTile: HTMLElement | null = null;

    const measure = () => {
      const tile = viewport.firstElementChild;
      if (!(tile instanceof HTMLElement)) return;
      if (tile !== observedTile) {
        if (observedTile) observer.unobserve(observedTile);
        observer.observe(tile);
        observedTile = tile;
      }
      if (!viewport.clientWidth || !viewport.clientHeight) return;
      const style = getComputedStyle(viewport);
      const horizontal = viewport.clientWidth - (parseFloat(style.paddingLeft) || 0) - (parseFloat(style.paddingRight) || 0);
      const vertical = viewport.clientHeight - (parseFloat(style.paddingTop) || 0) - (parseFloat(style.paddingBottom) || 0);
      const { width, height } = tile.getBoundingClientRect();
      const columns = layout === "column" ? 1 : fits(horizontal, width, parseFloat(style.columnGap) || 0);
      const rows = layout === "row" ? 1 : fits(vertical, height, parseFloat(style.rowGap) || 0);
      setCapacity(columns * rows);
    };

    const observer = new ResizeObserver(measure);
    const cardsChanged = new MutationObserver(measure);
    observer.observe(viewport);
    cardsChanged.observe(viewport, { childList: true });
    measure();
    return () => {
      observer.disconnect();
      cardsChanged.disconnect();
    };
  }, [count, layout, start]);

  return { track, page, pages, start, end: start + capacity, goTo: (next: number) => setFirst(next * capacity) };
}

export function CardPageControls({ label, page, pages, onPage }: {
  label: string;
  page: number;
  pages: number;
  onPage: (next: number) => void;
}) {
  if (pages <= 1) return null;
  return <div className="card-page-controls" aria-label={`${label} pages`}>
    <button type="button" className="link-button" aria-label={`Previous ${label} page`}
      aria-disabled={page === 0} onClick={() => { if (page > 0) onPage(page - 1); }}>‹</button>
    <span aria-live="polite">{page + 1}/{pages}</span>
    <button type="button" className="link-button" aria-label={`Next ${label} page`}
      aria-disabled={page === pages - 1} onClick={() => { if (page < pages - 1) onPage(page + 1); }}>›</button>
  </div>;
}

export function CardPages<T>({ items, label, layout = "row", className, trackClassName, style, testId, heading, render }: {
  items: readonly T[];
  label: string;
  layout?: PageLayout;
  className?: string;
  trackClassName?: string;
  style?: CSSProperties;
  testId?: string;
  heading?: ReactNode;
  render: (item: T, index: number) => ReactNode;
}) {
  const { track, page, pages, start, end, goTo } = useCardPages(items.length, layout);
  return <div className={["card-pages", className].filter(Boolean).join(" ")} role="group" aria-label={label} style={style}>
    {(heading || pages > 1) && <div className="card-page-heading">
      {heading}
      <CardPageControls label={label} page={page} pages={pages} onPage={goTo} />
    </div>}
    <div ref={track} className={["card-page-track", `card-page-${layout}`, trackClassName].filter(Boolean).join(" ")}
      data-testid={testId}>
      {items.slice(start, end).map((item, index) => render(item, start + index))}
    </div>
  </div>;
}
