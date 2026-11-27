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
  const preview = cards.get(previewed || selected[0] || String(options[0]?.value ?? ""));
  const countLabel = min === max ? `${selected.length} / ${max} selected` : `${selected.length} selected · choose ${min}–${max}`;
  const source = field.name === "search_cards" ? "library" : field.name === "cards" ? "hand" : "battlefield";
  const labels = options.map((option) => {
    const ref = String(option.value);
    const card = cards.get(ref);
    return card ? cardName(card) : option.label && option.label !== ref
      ? String(option.label) : labelFor(ref);
  });

  return (
    <fieldset className="choice-field card-choice">
      <legend>{field.name === "search_cards" ? "Available cards" : String(field.label)}</legend>
      <p className="card-choice-count" aria-live="polite">{countLabel}</p>
      <div className="card-choice-layout">
        {options.length > 0 ? <CardPages items={options} label={field.name === "search_cards" ? "Search results" : "Card choices"}
          className={`card-choice-pages from-${source}`} trackClassName="card-choice-options" layout="grid"
          render={(option, index) => {
            const ref = String(option.value);
            const card = cards.get(ref);
            const active = selected.includes(ref);
            const name = labels[index];
            const copies = labels.filter((label) => label === name).length;
            const copy = copies > 1
              ? `Copy ${labels.slice(0, index + 1).filter((label) => label === name).length} of ${copies}`
              : "";
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
                  if (singleChoice) onValue(ref);
                  else onValue(active ? selected.filter((entry) => entry !== ref) : max === 1 ? [ref] : [...selected, ref]);
                }}>
                {designation && <small>{designation}</small>}
                {card
                  ? <CardTile value={card} view={view} table />
                  : <strong>{name}</strong>}
                {copy && <small>{copy}</small>}
                {printing && <small>{printing}</small>}
              </button>
            );
          }} /> : <p>No eligible cards.</p>}
        {preview && <div className="card-choice-preview">
          <CardInspector value={preview} view={view} />
        </div>}
      </div>
    </fieldset>
  );
}
