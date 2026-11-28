import { useRef, useState } from "react";
import { api, type DeckBoard, type DeckPrinting, type DeckPrintingPage, type EditableDeckEntry } from "./api";
import { cardImageUrl, printingLabel } from "./CardSurface";

const boards: Record<DeckBoard, string> = {
  commander: "Commander", mainboard: "Mainboard", sideboard: "Sideboard",
  companion: "Companion", maybeboard: "Maybeboard",
};

export function deckEditorText(entries: EditableDeckEntry[]): string {
  return Object.entries(boards).flatMap(([board, title]) => {
    const lines = entries.filter((entry) => entry.board === board).map(({ quantity, name, printing, tags }) => {
      const edition = printing?.set_code && printing.collector_number ? ` (${printing.set_code}) ${printing.collector_number}` : "";
      const finish = printing?.finish?.toLowerCase() === "foil" ? " *F*" : "";
      const suffix = tags.map((tag) => tag.trim().replace(/\s+/g, "-")).filter(Boolean)
        .map((tag) => ` #${tag.replace(/^#/, "")}`).join("");
      return `${quantity} ${name}${edition}${finish}${suffix}`;
    });
    return lines.length ? [`${title}:\n${lines.join("\n")}`] : [];
  }).join("\n\n");
}

export function DeckEditor({ entries, cardRefs, onChange }: {
  entries: EditableDeckEntry[];
  cardRefs: Record<string, string>;
  onChange: (next: EditableDeckEntry[]) => void;
}) {
  const [selected, setSelected] = useState(0);
  const [sort, setSort] = useState("list");
  const [query, setQuery] = useState("");
  const [references, setReferences] = useState(cardRefs);
  const [lookup, setLookup] = useState<{ index: number; source: string; page: number; result: DeckPrintingPage }>();
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const sequence = useRef(0);
  const entry = entries[selected];
  const search = query.trim().toLocaleLowerCase();
  const listing = entries.map((value, index) => ({ value, index })).filter(({ value }) =>
    [value.name, ...value.tags].some((text) => text.toLocaleLowerCase().includes(search)));
  if (sort === "name") listing.sort((left, right) => left.value.name.localeCompare(right.value.name));
  if (sort === "tag") listing.sort((left, right) =>
    (left.value.tags[0] ?? "~").localeCompare(right.value.tags[0] ?? "~") || left.value.name.localeCompare(right.value.name));
  const options = lookup?.index === selected && lookup.source === entry?.name ? lookup : undefined;
  const image = entry ? cardImageUrl({ n: entry.name, cid: references[entry.name] ?? "", printing: entry.printing ?? {} }, "normal") : undefined;

  function change(part: Partial<EditableDeckEntry>) {
    onChange(entries.map((value, index) => index === selected ? { ...value, ...part } : value));
  }

  function show(index: number) {
    sequence.current += 1;
    setSelected(index);
    setError("");
    setPending(false);
  }

  async function findPrintings(page = 1) {
    if (!entry?.name.trim()) return;
    const number = ++sequence.current;
    const source = entry.name;
    setError("");
    setPending(true);
    try {
      const result = await api.deckPrintings(source.trim(), page);
      if (number !== sequence.current) return;
      setReferences((known) => ({ ...known, [source]: result.cid }));
      setLookup({ index: selected, source, page, result });
    } catch (caught) {
      if (number === sequence.current) setError(caught instanceof Error ? caught.message : "Could not load printings.");
    } finally {
      if (number === sequence.current) setPending(false);
    }
  }

  function setManualPrinting(fields: Partial<DeckPrinting>) {
    change({ printing: { ...entry?.printing, scryfall_id: undefined, ...fields } });
  }

  return <section className="visual-deck-editor wide" aria-label="Visual deck editor">
    <header>
      <h3>Deck editor</h3>
      <div className="deck-editor-controls">
        <label>Filter cards and tags<input value={query} onChange={(event) => setQuery(event.target.value)} /></label>
        <label>Order cards<select value={sort} onChange={(event) => setSort(event.target.value)}>
          <option value="list">List order</option><option value="name">Card name</option><option value="tag">First tag</option>
        </select></label>
        <button type="button" onClick={() => {
          onChange([...entries, { name: "", quantity: 1, board: "mainboard", tags: [] }]);
          setQuery("");
          show(entries.length);
        }}>Add card</button>
      </div>
    </header>
    <div className="deck-editor-body">
      <ol className="deck-editor-list" aria-label="Deck entries">
        {listing.map(({ value, index }) => <li key={index}>
          <button type="button" aria-pressed={index === selected} onClick={() => show(index)}>
            <strong>{value.quantity} × {value.name || "New card"}</strong>
            <small>{[boards[value.board], ...value.tags.map((value) => value.trim()).filter(Boolean)].join(" · ")}</small>
          </button>
        </li>)}
        {!listing.length && <li>No cards match.</li>}
      </ol>
      {entry && <div className="deck-entry-editor">
        <div className="deck-entry-fields">
          <label>Card name<input value={entry.name} required onChange={(event) => {
            show(selected);
            change({ name: event.target.value, printing: undefined });
          }} /></label>
          <label>Quantity<input type="number" min={1} step={1} required value={entry.quantity}
            onChange={(event) => change({ quantity: Number(event.target.value) })} /></label>
          <label>Board<select value={entry.board} onChange={(event) => change({ board: event.target.value as DeckBoard })}>
            {Object.entries(boards).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
          </select></label>
          <label>Tags<input value={entry.tags.join(",")} placeholder="Ramp, Draw"
            onChange={(event) => change({ tags: event.target.value.split(",") })} /></label>
        </div>
        <div className="deck-editor-printing">
          {image && <img key={image} src={image} alt={entry.name} onError={(event) => { event.currentTarget.hidden = true; }} />}
          <div>
            <strong>{printingLabel({ printing: entry.printing ?? {} }) || "Default printing"}</strong>
            <button type="button" className="link-button" onClick={() => void findPrintings()} disabled={pending || !entry.name.trim()}>
              {pending ? "Loading printings…" : "Find printings"}
            </button>
            {error && <p role="status">{error}</p>}
            {options && <>
              <label>Choose printing<select value={entry.printing?.scryfall_id ?? ""}
                onChange={(event) => {
                  const choice = options.result.printings.find((row) => row.printing.scryfall_id === event.target.value);
                  if (choice) change({ printing: { ...choice.printing,
                    finish: choice.finishes.includes(entry.printing?.finish ?? "") ? entry.printing?.finish : choice.finishes[0] } });
                }}>
                <option value="">Choose a set and collector number</option>
                {options.result.printings.map((row) => <option key={row.printing.scryfall_id} value={row.printing.scryfall_id}>
                  {row.set_name} #{row.printing.collector_number} · {row.printing.language}
                </option>)}
              </select></label>
              <div className="deck-printing-pages">
                {options.page > 1 && <button type="button" className="link-button" disabled={pending} onClick={() => void findPrintings(options.page - 1)}>Previous printings</button>}
                {options.result.has_more && <button type="button" className="link-button" disabled={pending} onClick={() => void findPrintings(options.page + 1)}>More printings</button>}
              </div>
            </>}
            <details>
              <summary>Enter printing details</summary>
              <div className="deck-entry-fields">
                <label>Set code<input value={entry.printing?.set_code ?? ""} maxLength={16}
                  onChange={(event) => setManualPrinting({ set_code: event.target.value })} /></label>
                <label>Collector number<input value={entry.printing?.collector_number ?? ""} maxLength={25}
                  onChange={(event) => setManualPrinting({ collector_number: event.target.value })} /></label>
                <label>Finish<select value={entry.printing?.finish ?? ""}
                  onChange={(event) => change({ printing: { ...entry.printing, finish: event.target.value || undefined } })}>
                  <option value="">Default</option><option value="nonfoil">Nonfoil</option>
                  <option value="foil">Foil</option><option value="etched">Etched foil</option>
                </select></label>
              </div>
            </details>
          </div>
        </div>
        <button type="button" className="secondary-button" onClick={() => {
          onChange(entries.filter((_, index) => index !== selected));
          show(Math.max(0, Math.min(selected, entries.length - 2)));
        }}>Remove card</button>
      </div>}
    </div>
  </section>;
}
