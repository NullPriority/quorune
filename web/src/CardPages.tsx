import { type CSSProperties, type ReactNode, useLayoutEffect, useRef, useState } from "react";

type PageLayout = "row" | "column" | "grid";

function fits(space: number, size: number, gap: number): number {
  return size > 0 ? Math.max(1, Math.floor((space + gap + 0.5) / (size + gap))) : 1;
}

function useCardPages(count: number, layout: PageLayout, overlaps?: string) {
  const track = useRef<HTMLDivElement>(null);
  const [first, setFirst] = useState(0);
  const [capacity, setCapacity] = useState(1);
  const page = Math.floor(Math.min(first, Math.max(0, count - 1)) / capacity);
  const pages = Math.max(1, Math.ceil(count / capacity));
  const start = page * capacity;

  useLayoutEffect(() => {
    const viewport = track.current;
    if (!viewport) return;
    const window = viewport.ownerDocument.defaultView!;
    let observedTile: HTMLElement | null = null;
    let frame = 0;

    const measure = () => {
      frame = 0;
      const tile = viewport.firstElementChild as HTMLElement | null;
      if (!tile) return;
      if (tile !== observedTile) {
        if (observedTile) observer.unobserve(observedTile);
        observer.observe(tile);
        observedTile = tile;
      }
      if (!viewport.clientWidth || !viewport.clientHeight) return;
      const style = window.getComputedStyle(viewport);
      const horizontal = viewport.clientWidth - (parseFloat(style.paddingLeft) || 0) - (parseFloat(style.paddingRight) || 0);
      const vertical = viewport.clientHeight - (parseFloat(style.paddingTop) || 0) - (parseFloat(style.paddingBottom) || 0);
      const { width, height } = tile.getBoundingClientRect();
      const gap = parseFloat(style.columnGap) || 0;
      const overlap = Math.min(width, Number(style.getPropertyValue("--card-page-overlap")) * width || 0);
      let columns = layout === "column" ? 1 : fits(horizontal - overlap, width - overlap, gap);
      if (layout === "row" && overlaps && width > 0) {
        // A staggered group uses less width. Use the most restrictive page
        // start so a later page cannot clip wider, unrelated land groups.
        const stagger = width * (Number(style.getPropertyValue("--stagger-fraction")) || 0);
        const maxColumns = fits(horizontal, width, gap);
        // A conservative lower bound also applies while the lane is hidden.
        columns = maxColumns;
        while (columns < count) {
          const next = columns + 1;
          const fitsEveryPage = Array.from({ length: Math.ceil(count / next) }, (_, page) => {
            const offset = page * next;
            const cards = overlaps.slice(offset, offset + next);
            const overlapsInPage = cards.slice(1).split("1").length - 1;
            return cards.length * width + (cards.length - 1) * gap - overlapsInPage * stagger <= horizontal + .5;
          }).every(Boolean);
          if (!fitsEveryPage) break;
          columns = next;
        }
      }
      const rows = layout === "row" ? 1 : fits(vertical, height, parseFloat(style.rowGap) || 0);
      setCapacity(columns * rows);
    };

    function scheduleMeasure() {
      if (!frame) frame = window.requestAnimationFrame(measure);
    }
    const observer = new ResizeObserver(scheduleMeasure);
    const cardsChanged = new MutationObserver(scheduleMeasure);
    observer.observe(viewport);
    cardsChanged.observe(viewport, { childList: true });
    measure();
    return () => {
      window.cancelAnimationFrame(frame);
      observer.disconnect();
      cardsChanged.disconnect();
    };
  }, [count, layout, overlaps, start]);

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

export function CardPages<T>({ items, label, layout = "row", className, trackClassName, style, testId, heading, render, overlaps }: {
  items: readonly T[];
  label: string;
  layout?: PageLayout;
  className?: string;
  trackClassName?: string;
  style?: CSSProperties;
  testId?: string;
  heading?: ReactNode;
  overlaps?: string;
  render: (item: T, index: number) => ReactNode;
}) {
  const { track, page, pages, start, end, goTo } = useCardPages(items.length, layout, overlaps);
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
