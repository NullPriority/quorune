import { useId, useRef, useState, type DragEvent } from "react";
import { CardChoiceGrid, projectedChoiceCards } from "./CardChoiceGrid";
import { CardInspector, FloatingTablePreview, cardImageUrl } from "./CardSurface";
import { CombatAssignment } from "./CombatAssignment";
import { ManaText, spokenMana } from "./ManaText";
import type { ChoiceForm, JsonValue } from "./generated/protocol";
import type { ProjectedView } from "./protocol";
import { modeLabel } from "./tablePresentation";
import { projectedCardSeat, SeatLabel, tableSeat } from "./SeatLabel";
import {
  activeFields,
  activeVariants,
  choicesWithDefaults,
  copyTargetGroups,
  copyTargetSlots,
  DAMAGE_ALLOCATION_FIELD,
  MANA_POOL_PAYMENT_FIELD,
  list,
  orderedPartitionNames,
  record,
  selectedDamageAllocation,
  targetGroupModes,
  targetGroups,
  selectedTargetCostLabel,
  updateModalTargetSelection,
  type ChoiceField,
  type ChoiceValues,
} from "./choices";

interface Props {
  form: ChoiceForm;
  values: ChoiceValues;
  onChange: (values: ChoiceValues) => void;
  labelFor: (value: string) => string;
  view?: ProjectedView;
  tableTargets?: { field: string; groupIds: ReadonlySet<string> };
  onInspect?: (card: JsonValue) => void;
  costLabels?: Readonly<Record<string, string>>;
}

function text(value: JsonValue | undefined): string {
  return value === undefined || value === null ? "" : String(value);
}

function testValue(value: JsonValue | undefined): string {
  return text(value).replace(/[^A-Za-z0-9_-]/g, "-");
}

function optionLabel(label: JsonValue | undefined, value: JsonValue | undefined, labelFor: (value: string) => string): string {
  const ref = text(value);
  const supplied = text(label);
  if (supplied && supplied !== ref) return supplied;
  if (/^(?:[ABCD]\d+|S\d+|[a-f0-9-]{24,})$/i.test(ref)) return labelFor(ref);
  const words = ref.replaceAll("_", " ").replaceAll("-", " ").trim();
  return words ? words[0].toUpperCase() + words.slice(1) : "Choose an option";
}

function FieldLabel({ field }: { field: ChoiceField }) {
  return <span>{fieldTitle(field)}{field.required ? " *" : ""}</span>;
}

function fieldTitle(field: ChoiceField): string {
  return optionLabel(field.label, field.name, () => "Choice");
}

function RefOptions({
  field,
  value,
  onValue,
  labelFor,
}: {
  field: ChoiceField;
  value: JsonValue | undefined;
  onValue: (value: JsonValue) => void;
  labelFor: (value: string) => string;
}) {
  const name = text(field.name);
  const selected = list(value).map(String);
  const options = list(field.options).map(record);
  const everyOptionRequired = Boolean(field.ordered) && Number(field.minimum) === options.length
    && Number(field.maximum) === options.length && selected.length === options.length;
  function toggle(ref: string, checked: boolean) {
    const next = checked
      ? [...selected, ref]
      : selected.filter((item) => item !== ref);
    const maximum = Number(field.maximum ?? options.length);
    if (next.length <= maximum) onValue(next);
    else if (checked && maximum === 1) onValue([ref]);
  }
  function move(index: number, offset: number) {
    const target = index + offset;
    if (target < 0 || target >= selected.length) return;
    const next = [...selected];
    [next[index], next[target]] = [next[target], next[index]];
    onValue(next);
  }
  return (
    <fieldset className="choice-field choice-refs">
      <legend><FieldLabel field={field} /></legend>
      {!everyOptionRequired && <div className="choice-options">
        {options.map((option) => {
          const ref = text(option.value);
          return (
            <label key={ref} className="choice-option">
              <input
                type="checkbox"
                data-testid={`choice-${name}-${testValue(option.value)}`}
                checked={selected.includes(ref)}
                disabled={option.available === false && !selected.includes(ref)}
                onChange={(event) => toggle(ref, event.target.checked)}
              />
              <span>{optionLabel(option.label, option.value, labelFor)}</span>
            </label>
          );
        })}
      </div>}
      {Boolean(field.ordered) && selected.length > 0 && (
        <ol className="choice-order">
          {selected.map((ref, index) => (
            <li key={ref}>
              <span>{labelFor(ref)}</span>
              <button
                type="button"
                aria-label={`Move ${labelFor(ref)}, item ${index + 1} of ${selected.length}, earlier in ${fieldTitle(field)}`}
                onClick={() => move(index, -1)}
                disabled={index === 0}
              >↑</button>
              <button
                type="button"
                aria-label={`Move ${labelFor(ref)}, item ${index + 1} of ${selected.length}, later in ${fieldTitle(field)}`}
                onClick={() => move(index, 1)}
                disabled={index === selected.length - 1}
              >↓</button>
            </li>
          ))}
        </ol>
      )}
    </fieldset>
  );
}

const orderedCardDragType = "application/x-quorune-ordered-card";

function TriggerOrderCards({ field, value, onValue, labelFor, previews }: {
  field: ChoiceField; value: JsonValue | undefined;
  onValue: (value: JsonValue) => void; labelFor: (ref: string) => string;
  previews: JsonValue;
}) {
  const order = list(value).map(String).reverse();
  const artByRef = new Map(list(previews).map((card) => [text(record(card).id), cardImageUrl(card, "small")]));
  const names = new Map(list(field.options).map(record).map((row) => [
    text(row.value), optionLabel(row.label, row.value, labelFor),
  ]));
  function rearrange(from: number, to: number) {
    if (from < 0 || to < 0 || to >= order.length || from === to) return;
    const next = [...order];
    const [moved] = next.splice(from, 1);
    next.splice(to, 0, moved);
    onValue(next.reverse());
  }
  return <fieldset className="choice-field trigger-order-field">
    <legend className="sr-only">Trigger resolution order</legend>
    <ol className="trigger-order-cards">
      {order.map((ref, index) => {
        const art = artByRef.get(ref);
        return (
          <li key={ref} draggable
            onDragStart={(event) => event.dataTransfer.setData(orderedCardDragType, ref)}
            onDragOver={(event) => {
              if (event.dataTransfer.types.includes(orderedCardDragType)) event.preventDefault();
            }}
            onDrop={(event) => {
              event.preventDefault();
              rearrange(order.indexOf(event.dataTransfer.getData(orderedCardDragType)), index);
            }}>
            <span className="trigger-order-position" aria-hidden="true">
              {index + 1}
              {art && <img key={art} src={art} alt="" loading="lazy" draggable={false}
                onError={(event) => { event.currentTarget.hidden = true; }} />}
            </span>
            <div className="trigger-order-info">
              <small>{index === 0 ? "Resolves first" : index === order.length - 1 ? "Resolves last" : `Resolves #${index + 1}`}</small>
              <strong>{names.get(ref) ?? labelFor(ref)}</strong>
            </div>
            <div className="trigger-order-moves">
              <button type="button" className="link-button" disabled={index === 0}
                aria-label={`Move trigger ${index + 1} earlier in resolution`} onClick={() => rearrange(index, index - 1)}>↑</button>
              <button type="button" className="link-button" disabled={index === order.length - 1}
                aria-label={`Move trigger ${index + 1} later in resolution`} onClick={() => rearrange(index, index + 1)}>↓</button>
            </div>
          </li>
        );
      })}
    </ol>
  </fieldset>;
}

function OrderedPartition({
  field,
  value,
  onValue,
  labelFor,
  view,
  onInspect,
}: {
  field: ChoiceField;
  value: JsonValue | undefined;
  onValue: (value: JsonValue) => void;
  labelFor: (value: string) => string;
  view?: ProjectedView;
  onInspect?: Props["onInspect"];
}) {
  const draggedCard = useRef("");
  const [preview, setPreview] = useState<{ ref: string; anchor: HTMLElement } | null>(null);
  const visible = view && projectedChoiceCards(view);
  const previewCard = preview && visible?.get(preview.ref);
  const partition = record(value);
  const configured = record(field.partitions);
  const names = orderedPartitionNames(field);
  const groups = Object.keys(configured).length === 2
    ? names.map((name) => [name, record(configured[name])] as const)
    : [
        ["top", { label: "Top of library", order: "top_to_bottom" }],
        ["bottom", { label: "Bottom of library", order: "bottom_to_top" }],
      ] as const;
  const options = list(field.options).map(record);
  const optionLabels = new Map(
    options.map((option) => [
      text(option.value),
      optionLabel(option.label, option.value, labelFor),
    ]),
  );

  function cardLabel(ref: string): string {
    return optionLabels.get(ref) || labelFor(ref);
  }

  function inspect(ref: string, anchor: HTMLElement) {
    const card = visible?.get(ref);
    if (card === undefined) return;
    onInspect?.(card);
    setPreview({ ref, anchor });
  }

  function previewEvents(ref: string) {
    return {
      onMouseEnter: (event: React.MouseEvent<HTMLElement>) => inspect(ref, event.currentTarget),
      onMouseLeave: () => setPreview(null),
      onFocus: (event: React.FocusEvent<HTMLElement>) => inspect(ref, event.currentTarget),
      onBlur: (event: React.FocusEvent<HTMLElement>) => {
        if (!event.currentTarget.contains(event.relatedTarget)) setPreview(null);
      },
    };
  }

  function groupLabel(name: string, descriptor: ChoiceField): string {
    return text(descriptor.label) || name.replaceAll("_", " ");
  }

  function groupRefs(name: string): string[] {
    return list(partition[name]).map(String);
  }

  function moveBetween(ref: string, destination: string, index?: number) {
    const next: Record<string, JsonValue> = {};
    for (const [name] of groups) {
      next[name] = groupRefs(name).filter((candidate) => candidate !== ref);
    }
    const destinationRefs = list(next[destination]).map(String);
    destinationRefs.splice(index ?? destinationRefs.length, 0, ref);
    next[destination] = destinationRefs;
    onValue(next);
  }

  function reorder(group: string, index: number, offset: number) {
    const values = groupRefs(group);
    const target = index + offset;
    if (target < 0 || target >= values.length) return;
    moveBetween(values[index], group, target);
  }

  function allowCardDrop(event: DragEvent<HTMLElement>) {
    if (!draggedCard.current) return;
    event.preventDefault();
    event.dataTransfer.dropEffect = "move";
  }

  function dropCard(event: DragEvent<HTMLElement>, group: string, rowRef?: string) {
    const ref = event.dataTransfer.getData(orderedCardDragType);
    if (!ref || ref !== draggedCard.current || !optionLabels.has(ref)) return;
    event.preventDefault();
    event.stopPropagation();
    draggedCard.current = "";
    if (ref === rowRef) return;
    const otherRefs = groupRefs(group).filter((candidate) => candidate !== ref);
    const rowIndex = rowRef ? otherRefs.indexOf(rowRef) : -1;
    const rect = event.currentTarget.getBoundingClientRect();
    const insertAfter = event.clientY >= rect.top + rect.height / 2;
    moveBetween(ref, group, rowIndex < 0 ? undefined : rowIndex + Number(insertAfter));
  }

  function orderedGroup(
    group: string,
    descriptor: ChoiceField,
  ) {
    const refs = groupRefs(group);
    const order = text(descriptor.order);
    const orderLabel =
      order === "top_to_bottom"
        ? "First row becomes the next card drawn; the last row is deepest in this top group."
        : order === "bottom_to_top"
          ? "First row becomes the library's bottom card; the last row is nearest the top of this bottom group."
          : order === "graveyard_top_to_bottom"
            ? "First row is the top card of the graveyard."
            : "The listed order is the submitted destination order.";
    const label = groupLabel(group, descriptor);
    return (
      <div className="choice-partition-group" onDragOver={allowCardDrop} onDrop={(event) => dropCard(event, group)}>
        <strong>{label}</strong>
        <span className="choice-help">{orderLabel}</span>
        <ol
          className="choice-order"
          aria-label={`${label} group order. ${orderLabel}`}
        >
          {refs.map((ref, index) => (
            <li key={ref} data-card-ref={ref} draggable
              onDragStart={(event) => {
                draggedCard.current = ref;
                event.dataTransfer.effectAllowed = "move";
                event.dataTransfer.setData(orderedCardDragType, ref);
              }}
              onDragEnd={() => { draggedCard.current = ""; }}
              onDrop={(event) => dropCard(event, group, ref)} {...previewEvents(ref)}>
              <span>{cardLabel(ref)}</span>
              <button
                type="button"
                aria-label={`Move ${cardLabel(ref)}, item ${index + 1} of ${refs.length}, earlier in ${label}`}
                onClick={() => reorder(group, index, -1)}
                disabled={index === 0}
              >↑</button>
              <button
                type="button"
                aria-label={`Move ${cardLabel(ref)}, item ${index + 1} of ${refs.length}, later in ${label}`}
                onClick={() => reorder(group, index, 1)}
                disabled={index === refs.length - 1}
              >↓</button>
            </li>
          ))}
        </ol>
      </div>
    );
  }

  return (
    <fieldset className="choice-field choice-partition">
      <legend><FieldLabel field={field} /></legend>
      {options.length > 0 && <p className="choice-help">Drag a card to a destination or use its destination menu.</p>}
      <div className="choice-options">
        {options.map((option, optionIndex) => {
          const ref = text(option.value);
          const selectedGroup = groups.find(([name]) =>
            groupRefs(name).includes(ref),
          )?.[0] ?? groups[0][0];
          return (
            <label key={ref} className="choice-option" {...previewEvents(ref)}>
              <span>{cardLabel(ref)}</span>
              <select
                data-testid={`choice-${text(field.name)}-${testValue(ref)}`}
                aria-label={`Choose a destination for ${cardLabel(ref)}, looked-at card ${optionIndex + 1} of ${options.length}`}
                value={selectedGroup}
                onChange={(event) => moveBetween(ref, event.target.value)}
              >
                {groups.map(([name, descriptor]) => (
                  <option key={name} value={name}>{groupLabel(name, descriptor)}</option>
                ))}
              </select>
            </label>
          );
        })}
      </div>
      {view && preview && previewCard !== undefined && <FloatingTablePreview anchor={preview.anchor}>
        <CardInspector value={previewCard} view={view} previewOnly />
      </FloatingTablePreview>}
      <div className="choice-partition-orders">
        {groups.map(([name, descriptor]) => (
          <div key={name}>{orderedGroup(name, descriptor)}</div>
        ))}
      </div>
    </fieldset>
  );
}

function ManaPoolPayment({ field, value, onValue }: {
  field: ChoiceField;
  value: JsonValue | undefined;
  onValue: (value: JsonValue) => void;
}) {
  const allocation = record(value);
  return (
    <details className="choice-field">
      <summary><FieldLabel field={field} /></summary>
      <p>Choose the exact mana to spend. Leave all amounts at zero for automatic payment.</p>
      {list(field.rows).map(record).map((row) => {
        const id = text(row.id);
        return (
          <label key={id} className="choice-option">
            <input type="number" min={0} max={Number(row.maximum)} step={1}
              value={Number(allocation[id] ?? 0)} aria-label={text(row.label)}
              onChange={(event) => {
                const next = { ...allocation };
                const amount = Number(event.target.value);
                if (amount === 0) delete next[id];
                else next[id] = amount;
                onValue(next);
              }} />
            <span>{text(row.label)}</span>
          </label>
        );
      })}
    </details>
  );
}

function ManaModes({
  field,
  value,
  onValue,
}: {
  field: ChoiceField;
  value: JsonValue | undefined;
  onValue: (value: JsonValue) => void;
}) {
  const selected = JSON.stringify(value ?? {});
  const allowed = list(field.allowed_colors).map(String);
  if (allowed.length) {
    const total = Number(field.total ?? 0);
    const allocation = record(value);
    return (
      <fieldset className="choice-field mana-modes">
        <legend><FieldLabel field={field} /></legend>
        <p>Add {total} mana{field.single_color ? " of one color" : " in any combination"}.</p>
        {allowed.map((color) => (
          <label key={color} className="choice-option">
            {field.single_color ? (
              <input type="radio" name={text(field.name)}
                checked={Number(allocation[color] ?? 0) === total && Object.keys(allocation).includes(color)}
                onChange={() => onValue({ [color]: total })} />
            ) : (
              <input type="number" min={0} max={total} step={1}
                value={Number(allocation[color] ?? 0)} aria-label={`${color} mana to add`}
                onChange={(event) => onValue({ ...allocation, [color]: Number(event.target.value) })} />
            )}
            <span>{color}</span>
          </label>
        ))}
      </fieldset>
    );
  }
  return (
    <fieldset className="choice-field mana-modes">
      <legend><FieldLabel field={field} /></legend>
      <div className="choice-options">
        {list(field.options).map((rawOption, index) => {
          const option = record(rawOption);
          const optionValue = option.value ?? {};
          return (
            <label key={JSON.stringify(optionValue)} className="choice-option">
              <input
                type="radio"
                name={text(field.name)}
                data-testid={`choice-${text(field.name)}-${index}`}
                checked={selected === JSON.stringify(optionValue)}
                onChange={() => onValue(structuredClone(optionValue))}
              />
              <span>{text(option.label)}</span>
            </label>
          );
        })}
      </div>
    </fieldset>
  );
}

export function TargetControl({
  field,
  values,
  onChange,
  labelFor,
  tableTargetGroups,
  view,
  onInspect,
}: {
  field: ChoiceField;
  values: ChoiceValues;
  onChange: (values: ChoiceValues) => void;
  labelFor: (value: string) => string;
  tableTargetGroups?: ReadonlySet<string>;
  view?: ProjectedView;
  onInspect?: Props["onInspect"];
}) {
  const cards = view && projectedChoiceCards(view);
  const schema = record(field.schema);
  const name = text(field.name || "targets");
  const modes = list(values.modes).map(String);
  const targets = record(values[name]);
  const legalModes = list(schema.legal_modes).map(String);
  const modeLabels = record(schema.mode_labels);
  const targetModes = targetGroupModes(field);
  const minimumModes = Number(schema.min_modes ?? schema.mode_count ?? 1);
  const maximumModes = Number(schema.max_modes ?? schema.mode_count ?? 1);
  const allocation = selectedDamageAllocation(field, values);
  function toggleMode(mode: string, checked: boolean) {
    const next = updateModalTargetSelection(
      schema,
      modes,
      targets,
      mode,
      checked,
    );
    onChange({ ...values, modes: next.modes, [name]: next.targets });
  }
  function toggleTarget(group: ChoiceField, ref: string, checked: boolean) {
    const id = text(group.id || "target");
    const selected = list(targets[id]).map(String);
    const maximum = Number(group.max ?? 1);
    if (checked && maximum === 0) return;
    let next = checked ? [...selected, ref] : selected.filter((item) => item !== ref);
    if (checked && next.length > maximum) {
      next = maximum === 1 ? [ref] : [...next.slice(-(maximum - 1)), ref];
    }
    const updated = { ...values, [name]: { ...targets, [id]: next } };
    if (allocation) delete updated[DAMAGE_ALLOCATION_FIELD];
    onChange(updated);
  }
  return (
    <fieldset className="choice-field target-control">
      <legend>{legalModes.length > 0 ? "Modes" : <FieldLabel field={field} />}</legend>
      {legalModes.length > 0 && (
        <>
          <p className="choice-help" aria-live="polite">
            {modes.length} selected · Choose {minimumModes === maximumModes ? minimumModes : `${minimumModes}–${maximumModes}`}
          </p>
          <div className="choice-options">
            {legalModes.map((mode, index) => (
              <label key={mode} className="choice-option">
                <input
                  type="checkbox"
                  data-testid={`choice-mode-${testValue(mode)}`}
                  checked={modes.includes(mode)}
                  onChange={(event) => toggleMode(mode, event.target.checked)}
                />
                <span>{text(modeLabels[mode]) || modeLabel(mode, index)}</span>
              </label>
            ))}
          </div>
        </>
      )}
      {targetGroups(field, values).map((group) => {
        const id = text(group.id || "target");
        if (tableTargetGroups?.has(id)) return null;
        const selected = list(targets[id]).map(String);
        const maximum = Number(group.max ?? 1);
        const full = maximum === 0
          || group.maximum_from === "x" && maximum > 1 && selected.length >= maximum;
        const mode = targetModes.get(id);
        const targetLabel = text(group.label || "Targets");
        const label = mode ? `${text(modeLabels[mode]) || modeLabel(mode, legalModes.indexOf(mode))} · ${targetLabel}` : targetLabel;
        const bySeat = new Map<string, string[]>();
        for (const ref of list(group.legal_refs).map(text)) {
          const seat = tableSeat(ref) ?? (view ? projectedCardSeat(view, cards?.get(ref) ?? null) : undefined) ?? "";
          bySeat.set(seat, [...(bySeat.get(seat) ?? []), ref]);
        }
        return (
          <div key={id} className="target-group" role="group" aria-label={label}>
            <strong>{label} ({text(group.min ?? 1)}–{text(group.max ?? 1)})</strong>
            {group.maximum_from === "x" && group.max === 0 && selected.length === 0
              && <p className="choice-help">Set X above 0 to select targets.</p>}
            <div className="seat-target-options">
              {[...bySeat].sort(([a], [b]) => a.localeCompare(b)).map(([seat, refs]) =>
                <div key={seat} className="seat-target-group" role={seat ? "group" : undefined} data-seat={seat || undefined}
                  aria-label={seat ? `Seat ${seat} targets` : undefined}>
                  <SeatLabel seat={seat} />
                  {refs.map((ref) => {
                    const card = cards?.get(ref);
                    const inspect = card !== undefined && onInspect ? () => onInspect(card) : undefined;
                    return <label key={ref} className="choice-option" onMouseEnter={inspect} onFocus={inspect}>
                    <input
                      type="checkbox"
                      data-testid={`choice-target-${testValue(ref)}`}
                      checked={selected.includes(ref)}
                      disabled={full && !selected.includes(ref)}
                      onChange={(event) => toggleTarget(group, ref, event.target.checked)}
                    />
                    <span>{labelFor(ref)}</span>
                    </label>;
                  })}
                </div>)}
            </div>
          </div>
        );
      })}
      {allocation && allocation.refs.length > 0 && (
        <fieldset className="choice-field">
          <legend>Divide {allocation.total} damage</legend>
          {allocation.refs.map((ref) => (
            <label key={ref} className="choice-option">
              <span>{labelFor(ref)}</span>
              <input
                type="number"
                min={1}
                max={allocation.total}
                value={text(allocation.shares[ref])}
                onChange={(event) => onChange({
                  ...values,
                  [DAMAGE_ALLOCATION_FIELD]: {
                    ...allocation.shares,
                    [ref]: event.target.value === "" ? "" : Number(event.target.value),
                  },
                })}
              />
            </label>
          ))}
        </fieldset>
      )}
    </fieldset>
  );
}

function AssignmentMap({
  field,
  value,
  onValue,
  labelFor,
}: {
  field: ChoiceField;
  value: JsonValue | undefined;
  onValue: (value: JsonValue) => void;
  labelFor: (value: string) => string;
}) {
  const selected = record(value);
  const minimumGroups = record(field.minimum_group_sizes);
  return (
    <fieldset className="choice-field assignment-map">
      <legend><FieldLabel field={field} /></legend>
      {Object.entries(minimumGroups).map(([target, rawMinimum]) => (
        <p className="choice-help" key={target}>
          {labelFor(target)} requires either no assignments or at least{" "}
          {text(rawMinimum)}.
        </p>
      ))}
      {list(field.rows).map((rawRow) => {
        const row = record(rawRow);
        const key = text(row.value);
        const maximum = Number(row.maximum ?? 1);
        if (maximum > 1) {
          const chosen = Array.isArray(selected[key]) ? list(selected[key]).map(text) : text(selected[key]) ? [text(selected[key])] : [];
          return (
            <fieldset key={key} className="choice-field">
              <legend>{optionLabel(row.label, row.value, labelFor)}</legend>
              <p className="choice-help">Choose up to {maximum} {text(field.choice_label) || "attackers"}.</p>
              {list(row.options).map((rawOption) => {
                const option = record(rawOption);
                const ref = text(option.value);
                return <label key={ref} className="choice-option">
                  <input type="checkbox" data-testid={`choice-${text(field.name)}-${testValue(key)}-${testValue(ref)}`}
                    checked={chosen.includes(ref)} disabled={!chosen.includes(ref) && chosen.length >= maximum}
                    onChange={(event) => {
                      const refs = event.target.checked ? [...chosen, ref] : chosen.filter((value) => value !== ref);
                      const next = { ...selected };
                      if (refs.length) next[key] = refs;
                      else delete next[key];
                      onValue(next);
                    }} />
                  <span>{optionLabel(option.label, option.value, labelFor)}</span>
                </label>;
              })}
            </fieldset>
          );
        }
        return (
          <label key={key}>
            {optionLabel(row.label, row.value, labelFor)}
            <select
              data-testid={`choice-${text(field.name)}-${testValue(key)}`}
              value={text(selected[key])}
              onChange={(event) => {
                const next = { ...selected };
                if (event.target.value) next[key] = event.target.value;
                else delete next[key];
                onValue(next);
              }}
            >
              <option value="">Do not assign</option>
              {list(row.options).map((rawOption) => {
                const option = record(rawOption);
                return <option key={text(option.value)} value={text(option.value)}>{optionLabel(option.label, option.value, labelFor)}</option>;
              })}
            </select>
          </label>
        );
      })}
    </fieldset>
  );
}

function DamageAssignments({
  field,
  value,
  onValue,
  labelFor,
}: {
  field: ChoiceField;
  value: JsonValue | undefined;
  onValue: (value: JsonValue) => void;
  labelFor: (value: string) => string;
}) {
  const sources = record(record(field.combat).damage_sources);
  const assignments = list(value).map(record);
  function amount(source: string, target: string): number {
    return Number(assignments.find((row) => row.source === source && row.target === target)?.amount ?? 0);
  }
  function update(source: string, target: string, nextAmount: number) {
    const next = assignments.filter((row) => !(row.source === source && row.target === target));
    if (nextAmount > 0) next.push({ source, target, amount: nextAmount });
    onValue(next);
  }
  return (
    <fieldset className="choice-field damage-assignments">
      <legend><FieldLabel field={field} /></legend>
      {Object.entries(sources).map(([source, rawSource]) => {
        const sourceData = record(rawSource);
        const power = Number(sourceData.power ?? 0);
        const assigned = assignments
          .filter((row) => row.source === source)
          .reduce((total, row) => total + Number(row.amount ?? 0), 0);
        const remaining = power - assigned;
        return (
          <div key={source} className="damage-source">
            <strong>{labelFor(source)} assigns {power} damage</strong>
            <p className="choice-help" role="status">
              {assigned} assigned · {remaining < 0 ? `${-remaining} over the limit` : `${remaining} remaining`}
            </p>
            {sourceData.as_unblocked_recipient && (
              <p>Assign all damage to {labelFor(text(sourceData.as_unblocked_recipient))}, or use the ordinary assignment to blockers.</p>
            )}
            {list(sourceData.targets).map((rawTarget) => {
              const target = text(rawTarget);
              return (
                <label key={target}>
                  {labelFor(target)}
                  <input
                    type="number"
                    min={0}
                    max={power}
                    value={amount(source, target)}
                    onChange={(event) => update(source, target, Number(event.target.value))}
                  />
                </label>
              );
            })}
          </div>
        );
      })}
    </fieldset>
  );
}

function QuantityMap({ field, value, onValue, labelFor }: {
  field: ChoiceField;
  value: JsonValue | undefined;
  onValue: (value: JsonValue) => void;
  labelFor: (value: string) => string;
}) {
  const amounts = record(value);
  const budget = Number(field.exact_total ?? field.maximum_total);
  const total = Object.values(amounts).reduce<number>((sum, amount) => sum + Number(amount), 0);
  return (
    <fieldset className="choice-field">
      <legend><FieldLabel field={field} /></legend>
      <p>{total} of {budget} assigned</p>
      {list(field.options).map((rawOption) => {
        const option = record(rawOption);
        const ref = text(option.value);
        return (
          <label key={ref} className="choice-option">
            <input type="number" min={0} max={Number(field.maximum_each ?? budget)} step={1}
              value={Number(amounts[ref] ?? 0)} aria-label={labelFor(ref)}
              onChange={(event) => {
                const next = { ...amounts };
                const amount = Number(event.target.value);
                if (amount === 0) delete next[ref];
                else next[ref] = amount;
                onValue(next);
              }} />
            <span>{labelFor(ref)}</span>
          </label>
        );
      })}
    </fieldset>
  );
}

function ObjectMap({
  field,
  value,
  onValue,
  labelFor,
}: {
  field: ChoiceField;
  value: JsonValue | undefined;
  onValue: (value: JsonValue) => void;
  labelFor: (value: string) => string;
}) {
  const selected = record(value);
  return (
    <fieldset className="choice-field object-map">
      <legend><FieldLabel field={field} /></legend>
      {list(field.keys).map((rawKey) => {
        const key = text(rawKey);
        return (
          <label key={key}>
            {labelFor(key)}
            <select
              value={text(selected[key])}
              onChange={(event) => {
                const next = { ...selected };
                if (event.target.value) next[key] = event.target.value;
                else delete next[key];
                onValue(next);
              }}
            >
              <option value="">Choose…</option>
              {list(field.options).map((rawOption) => {
                const option = record(rawOption);
                return <option key={text(option.value)} value={text(option.value)}>{text(option.label)}</option>;
              })}
            </select>
          </label>
        );
      })}
    </fieldset>
  );
}

function CopyTargets({
  field,
  value,
  onValue,
  labelFor,
}: {
  field: ChoiceField;
  value: JsonValue | undefined;
  onValue: (value: JsonValue) => void;
  labelFor: (value: string) => string;
}) {
  const copies = list(field.copies).map(record);
  const submitted = field.single_copy ? [value ?? []] : list(value);
  function updateCopies(next: JsonValue[]) {
    onValue(field.single_copy ? next[0] ?? [] : next);
  }
  function selectedFor(
    entry: JsonValue | undefined,
    group: ChoiceField,
    groupCount: number,
  ): string[] {
    if (Array.isArray(entry)) {
      if (groupCount === 1) return entry.map(String);
      const legal = new Set(list(group.legal_refs).map(String));
      return entry.map(String).filter((ref) => legal.has(ref));
    }
    return list(record(entry)[text(group.id || "target")]).map(String);
  }
  function toggle(index: number, group: ChoiceField, ref: string, checked: boolean) {
    const groups = copyTargetGroups(copies[index]);
    const current = submitted[index];
    const grouped: Record<string, JsonValue> = {};
    for (const candidate of groups) {
      const id = text(candidate.id || "target");
      grouped[id] = selectedFor(current, candidate, groups.length);
    }
    const id = text(group.id || "target");
    const selected = list(grouped[id]).map(String);
    const maximum = Number(group.max ?? 1);
    let next = checked ? [...selected, ref] : selected.filter((item) => item !== ref);
    if (next.length > maximum) {
      next = maximum === 1 ? [ref] : [...next.slice(-(maximum - 1)), ref];
    }
    grouped[id] = next;
    const nextCopies = [...submitted];
    nextCopies[index] = grouped;
    updateCopies(nextCopies);
  }
  return (
    <fieldset className="choice-field copy-targets">
      <legend><FieldLabel field={field} /></legend>
      {copies.map((copy, index) => {
        if (field.may_retain_each_default) {
          const slots = copyTargetSlots(copy);
          const selected = list(submitted[index]);
          return <div key={index} className="target-group">
            <strong>Copy {index + 1}</strong>
            {slots.map((slot) => {
              const current = record(selected[slot.index]);
              const keeping = Object.hasOwn(current, "retain")
                || selected[slot.index] === slot.original;
              return <label key={slot.index} className="choice-option">
                Target {slot.index + 1}
                <select
                  data-testid={`choice-copy-${index}-slot-${slot.index}`}
                  value={keeping ? "__retain_original" : text(current.ref || selected[slot.index])}
                  onChange={(event) => {
                    const nextSelection = [...selected];
                    nextSelection[slot.index] = event.target.value === "__retain_original"
                      ? { retain: slot.index }
                      : { group: text(slot.group.id || "target"), ref: event.target.value };
                    const nextCopies = [...submitted];
                    nextCopies[index] = nextSelection;
                    updateCopies(nextCopies);
                  }}
                >
                  <option value="__retain_original">Keep original target ({labelFor(slot.original)})</option>
                  {list(slot.group.legal_refs).map((rawRef) => {
                    const ref = text(rawRef);
                    return <option key={ref} value={ref}>Choose current object ({labelFor(ref)})</option>;
                  })}
                </select>
              </label>;
            })}
            {slots.length === 0 && <span className="choice-help">This copy retains zero targets.</span>}
          </div>;
        }
        const groups = copyTargetGroups(copy);
        return (
          <div key={index} className="target-group">
            <strong>Copy {index + 1}</strong>
            {groups.length === 0 && <span className="choice-warning">This copy has no changeable targets.</span>}
            {groups.map((group) => {
              const id = text(group.id || "target");
              const selected = selectedFor(submitted[index], group, groups.length);
              return (
                <div key={id} className="target-group">
                  <span>{text(group.label || "Targets")}</span>
                  <div className="choice-options">
                    {list(group.legal_refs).map((rawRef) => {
                      const ref = text(rawRef);
                      return (
                        <label key={ref} className="choice-option">
                          <input
                            type="checkbox"
                            data-testid={`choice-copy-${index}-${testValue(id)}-${testValue(ref)}`}
                            checked={selected.includes(ref)}
                            onChange={(event) => toggle(index, group, ref, event.target.checked)}
                          />
                          <span>{labelFor(ref)}</span>
                        </label>
                      );
                    })}
                  </div>
                </div>
              );
            })}
          </div>
        );
      })}
    </fieldset>
  );
}

function ChoiceControl({
  field,
  values,
  onChange,
  labelFor,
  tableTargetGroups,
  view,
  onInspect,
}: {
  field: ChoiceField;
  values: ChoiceValues;
  onChange: (values: ChoiceValues) => void;
  labelFor: (value: string) => string;
  tableTargetGroups?: ReadonlySet<string>;
  view?: ProjectedView;
  onInspect?: Props["onInspect"];
}) {
  const name = text(field.name);
  const control = text(field.control);
  const value = values[name];
  const set = (next: JsonValue) => {
    const updated = { ...values, [name]: next };
    if (name === "x" && value !== next) delete updated[DAMAGE_ALLOCATION_FIELD];
    onChange(updated);
  };
  if (control === MANA_POOL_PAYMENT_FIELD) return <ManaPoolPayment field={field} value={value} onValue={set} />;
  if (control === "mana_modes") return <ManaModes field={field} value={value} onValue={set} />;
  if (control === "refs") return <RefOptions field={field} value={value} onValue={set} labelFor={labelFor} />;
  if (control === "ordered_partition") return <OrderedPartition field={field} value={value} onValue={set} labelFor={labelFor} view={view} onInspect={onInspect} />;
  if (control === "targets") return <>
    <TargetControl field={field} values={values} onChange={onChange} labelFor={labelFor} tableTargetGroups={tableTargetGroups}
      view={view} onInspect={onInspect} />
    {!tableTargetGroups && selectedTargetCostLabel(field, values) && <p role="status">{selectedTargetCostLabel(field, values)}</p>}
  </>;
  if (control === "assignment_map") return <AssignmentMap field={field} value={value} onValue={set} labelFor={labelFor} />;
  if (control === "damage_assignments") return <DamageAssignments field={field} value={value} onValue={set} labelFor={labelFor} />;
  if (control === "object_map") return <ObjectMap field={field} value={value} onValue={set} labelFor={labelFor} />;
  if (control === "quantity_map") return <QuantityMap field={field} value={value} onValue={set} labelFor={labelFor} />;
  if (control === "boolean") {
    const legalValues = list(field.legal_values).filter(
      (candidate): candidate is boolean => typeof candidate === "boolean",
    );
    const options = legalValues.length ? legalValues : [false, true];
    const selected = typeof value === "boolean" ? value : options[0];
    return (
      <label className="choice-field">
        <FieldLabel field={field} />
        <select data-testid={`choice-${name}`} value={String(selected)} onChange={(event) => set(event.target.value === "true")}>
          {options.map((option) => <option key={String(option)} value={String(option)}>{option ? "Yes" : "No"}</option>)}
        </select>
      </label>
    );
  }
  if (control === "integer") {
    return (
      <label className="choice-field"><FieldLabel field={field} />
        <input data-testid={`choice-${name}`} name={name} type="number" min={Number(field.minimum ?? 0)} max={field.maximum === undefined ? undefined : Number(field.maximum)} value={Number(value ?? 0)} onChange={(event) => set(Number(event.target.value))} />
      </label>
    );
  }
  if (control === "text") {
    return (
      <label className="choice-field"><FieldLabel field={field} />
        <input data-testid={`choice-${name}`} maxLength={field.max_length === undefined ? undefined : Number(field.max_length)} value={text(value)} onChange={(event) => set(event.target.value)} />
      </label>
    );
  }
  if (control === "select" || control === "ref") {
    return (
      <label className="choice-field"><FieldLabel field={field} />
        <select data-testid={`choice-${name}`} value={text(value)} onChange={(event) => set(event.target.value)}>
          {!field.required && <option value="">None</option>}
          {list(field.options).map((rawOption) => {
            const option = record(rawOption);
            const rawValue = option.value;
            return <option key={text(rawValue)} value={text(rawValue)}>{optionLabel(option.label, rawValue, labelFor)}</option>;
          })}
        </select>
      </label>
    );
  }
  if (control === "copy_targets") return <CopyTargets field={field} value={value} onValue={set} labelFor={labelFor} />;
  return field.required ? null
    : <p className="choice-warning">A newer browser build can show this optional control.</p>;
}

export function ChoiceFormView({ form, values, onChange, labelFor, view, tableTargets, costLabels, onInspect }: Props) {
  const costGroup = useId();
  if (form.v !== 1) return null;
  const variants = activeVariants(form, values);
  const selector = text(variants.field || "cost_option");
  const variantOptions = list(variants.options).map(record);
  const showPrices = variantOptions.some((option) => costLabels?.[text(option.value)]);
  const selectedCost = text(values[selector] ?? variants.default);
  const changeCost = (value: string) => onChange(choicesWithDefaults(form, { ...values, [selector]: value }));
  const fields = activeFields(form, values);
  const decisionKind = view?.decision?.kind;
  const choosingHandCards = decisionKind === "mulligan.bottom" || decisionKind === "cleanup.discard";
  const cardPicker = (field: ChoiceField) => {
    if (decisionKind === "state.legend" && field.name === "card") {
      return field.control === "ref" || field.control === "select" || field.control === "refs";
    }
    if (decisionKind === "search.fetch" && field.name === "search_card") return field.control === "ref";
    return field.control === "refs" && (field.name === "cost_cards" || field.name === "search_cards"
      || (choosingHandCards && field.name === "cards"));
  };
  const cards = view && fields.some(cardPicker)
    ? projectedChoiceCards(view) : null;
  return (
    <div className="choice-form-fields">
      {variantOptions.length > 1 && (showPrices ? (
        <fieldset className="choice-field" data-testid={`choice-${selector}`}>
          <legend>{text(variants.label || "Casting cost")}</legend>
          <div className="choice-options">
            {variantOptions.map((option) => {
              const value = text(option.value);
              const label = costLabels?.[value] ?? text(option.label);
              return <label key={value} className="choice-option">
                <input type="radio" name={costGroup} value={value} checked={selectedCost === value}
                  aria-label={spokenMana(label)} onChange={() => changeCost(value)} />
                <ManaText text={label} />
              </label>;
            })}
          </div>
        </fieldset>
      ) : (
        <label className="choice-field">
          {text(variants.label || "Casting cost")}
          <select
            data-testid={`choice-${selector}`}
            value={selectedCost}
            onChange={(event) => changeCost(event.target.value)}
          >
            {variantOptions.map((option) => <option key={text(option.value)} value={text(option.value)}>{text(option.label)}</option>)}
          </select>
        </label>
      ))}
      {fields.map((field) => {
        const name = text(field.name);
        const onValue = (value: JsonValue) => onChange({ ...values, [name]: value });
        if (decisionKind === "trigger.order" && field.control === "refs" && field.ordered) {
          return <TriggerOrderCards key={name} field={field} value={values[name]} onValue={onValue} labelFor={labelFor}
            previews={view?.decision?.ctx.preview_cards ?? []} />;
        }
        if (view && cards && cardPicker(field)) {
          return <CardChoiceGrid key={name} field={field} value={values[name]}
            onValue={onValue} labelFor={labelFor} view={view} cards={cards} />;
        }
        if (view && field.control === "assignment_map" && (name === "attackers" || name === "blocks")) {
          return <CombatAssignment key={name} field={field} value={values[name]} onValue={onValue} view={view} labelFor={labelFor} />;
        }
        return <ChoiceControl key={name} field={field} values={values} onChange={onChange} labelFor={labelFor}
          view={view} onInspect={onInspect} tableTargetGroups={tableTargets?.field === name ? tableTargets.groupIds : undefined} />;
      })}
    </div>
  );
}
