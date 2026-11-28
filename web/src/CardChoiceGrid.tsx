import { useState } from "react";
import { asList, asRecord, cardName, CardInspector, CardTile, printingLabel } from "./CardSurface";
import { CardPages } from "./CardPages";
import { list, record, type ChoiceField } from "./choices";
import type { JsonValue } from "./generated/protocol";
import type { ProjectedView } from "./protocol";

export function projectedLabels(view: ProjectedView): Map<string, string> {
  const labels = new Map<string, string>();
  function walk(value: JsonValue | undefined) {
    if (Array.isArray(value)) {
      value.forEach(walk);
      return;
    }
    if (!value || typeof value !== "object") return;
    const { id, label, n, name } = value;
    const display = label ?? (n === "?" ? "Face-down card" : n) ?? name;
    if (typeof id === "string" && typeof display === "string") labels.set(id, display);
    Object.values(value).forEach(walk);
  }
  walk(view.state);
  walk(view.decision?.ctx);
  for (const seat of ["A", "B", "C", "D"]) labels.set(seat, `Seat ${seat}`);
  return labels;
}

export function projectedChoiceCards(view: ProjectedView): Map<string, JsonValue> {
  const cards = new Map<string, JsonValue>();
  const rows = Object.values(asRecord(view.state.players)).flatMap((player) =>
    ["bf", "hand", "gy", "ex", "cmd", "known_top", "known_hand"].flatMap((zone) =>
      asList(asRecord(player)[zone])),
  );
  for (const card of [...rows, ...asList(view.decision?.ctx.preview_cards)]) {
    const ref = asRecord(card).id;
    if (typeof ref === "string") cards.set(ref, card);
  }
  return cards;
}

export function CardChoiceGrid({
  field,
  value,
  onValue,
  labelFor,
  view,
  cards,
}: {
  field: ChoiceField;
  value: JsonValue | undefined;
  onValue: (value: JsonValue) => void;
  labelFor: (ref: string) => string;
  view: ProjectedView;
  cards: Map<string, JsonValue>;
}) {
  const options = list(field.options).map(record);
  const singleChoice = field.control === "ref" || field.control === "select";
  const selected = singleChoice && typeof value === "string" && value ? [value] : list(value).map(String);
  const max = singleChoice ? 1 : Number(field.maximum ?? options.length);
  const min = singleChoice ? Number(Boolean(field.required)) : Number(field.minimum ?? 0);
  const [previewed, setPreviewed] = useState("");
  const [query, setQuery] = useState("");
  const preview = cards.get(previewed || selected[0] || String(options[0]?.value ?? ""));
  const countLabel = min === max ? `${selected.length} / ${max} selected` : `${selected.length} selected · choose ${min}–${max}`;
  const search = field.name === "search_cards" || field.name === "search_card";
  const source = search ? "library" : field.name === "cards" ? "hand" : "battlefield";
  const counts = new Map<string, number>();
  const labels = options.map((option) => {
    const ref = String(option.value);
    const card = cards.get(ref);
    const label = card ? cardName(card) : option.label && option.label !== ref
      ? String(option.label) : labelFor(ref);
    const copy = (counts.get(label) ?? 0) + 1;
    counts.set(label, copy);
    return { label, copy };
  });
  const filter = query.trim().toLowerCase();
  const results = options.flatMap((option, index) => {
    const label = labels[index];
    return !search || label.label.toLowerCase().includes(filter) ? [{ option, label }] : [];
  });

  return (
    <fieldset className="choice-field card-choice">
      <legend>{search ? "Available cards" : String(field.label)}</legend>
      <p className="card-choice-count" aria-live="polite">{countLabel}</p>
      {search && options.length > 0 && <label className="choice-filter">
        <span>Filter cards by name</span>
        <input type="search" value={query} onChange={(event) => setQuery(event.target.value)}
          onKeyDown={(event) => { if (event.key === "Enter") event.preventDefault(); }} />
        {filter && <span role="status">{results.length} of {options.length} cards</span>}
      </label>}
      <div className="card-choice-layout">
        {results.length > 0 ? <CardPages key={filter} items={results} label={search ? "Search results" : "Card choices"}
          className={`card-choice-pages from-${source}`} trackClassName="card-choice-options" layout="grid"
          render={({ option, label }) => {
            const ref = String(option.value);
            const card = cards.get(ref);
            const active = selected.includes(ref);
            const { label: name, copy: number } = label;
            const copies = counts.get(name) ?? 1;
            const copy = copies > 1 ? `Copy ${number} of ${copies}` : "";
            const printing = card ? printingLabel(card) : "";
            const cardState = asRecord(card);
            const designation = cardState.cmd ? "Commander" : cardState.tok ? "Token" : "";
            return (
              <button key={ref} type="button" className="card-choice-button"
                data-testid={`choice-${field.name}-${ref}`}
                aria-label={[name, copy, designation, printing].filter(Boolean).join(", ")}
                aria-pressed={active}
                disabled={!active && (option.available === false || max === 0 || (max !== 1 && selected.length >= max))}
                onMouseEnter={() => setPreviewed(ref)}
                onFocus={() => setPreviewed(ref)}
                onClick={() => {
                  setPreviewed(ref);
                  if (singleChoice) onValue(active && min === 0 ? "" : ref);
                  else onValue(active ? selected.filter((entry) => entry !== ref) : max === 1 ? [ref] : [...selected, ref]);
                }}>
                {designation && <small>{designation}</small>}
                {card
                  ? <CardTile value={card} view={view} table showPrintedCost={!search} />
                  : <strong>{name}</strong>}
                {copy && <small>{copy}</small>}
                {printing && <small>{printing}</small>}
              </button>
            );
          }} /> : <p>{filter ? "No eligible cards match this search." : "No eligible cards."}</p>}
        {preview && <div className="card-choice-preview">
          <CardInspector value={preview} view={view} />
        </div>}
      </div>
    </fieldset>
  );
}
