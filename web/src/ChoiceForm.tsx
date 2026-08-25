import type { ChoiceForm, JsonValue } from "./generated/protocol";
import {
  activeFields,
  choicesWithDefaults,
  copyTargetGroups,
  list,
  orderedPartitionNames,
  record,
  targetGroups,
  updateModalTargetSelection,
  type ChoiceField,
  type ChoiceValues,
} from "./choices";

interface Props {
  form: ChoiceForm;
  values: ChoiceValues;
  onChange: (values: ChoiceValues) => void;
  labelFor: (value: string) => string;
}

function text(value: JsonValue | undefined): string {
  return value === undefined || value === null ? "" : String(value);
}

function testValue(value: JsonValue | undefined): string {
  return text(value).replace(/[^A-Za-z0-9_-]/g, "-");
}

function FieldLabel({ field }: { field: ChoiceField }) {
  return <span>{text(field.label || field.name)}{field.required ? " *" : ""}</span>;
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
      <div className="choice-options">
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
              <span>{text(option.label) || labelFor(ref)}</span>
            </label>
          );
        })}
      </div>
      {Boolean(field.ordered) && selected.length > 0 && (
        <ol className="choice-order">
          {selected.map((ref, index) => (
            <li key={ref}>
              <span>{labelFor(ref)}</span>
              <button
                type="button"
                aria-label={`Move ${labelFor(ref)}, item ${index + 1} of ${selected.length}, earlier in ${text(field.label || field.name)}`}
                onClick={() => move(index, -1)}
                disabled={index === 0}
              >↑</button>
              <button
                type="button"
                aria-label={`Move ${labelFor(ref)}, item ${index + 1} of ${selected.length}, later in ${text(field.label || field.name)}`}
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

function OrderedPartition({
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
      text(option.label) || labelFor(text(option.value)),
    ]),
  );

  function cardLabel(ref: string): string {
    return optionLabels.get(ref) || labelFor(ref);
  }

  function groupLabel(name: string, descriptor: ChoiceField): string {
    return text(descriptor.label) || name.replaceAll("_", " ");
  }

  function groupRefs(name: string): string[] {
    return list(partition[name]).map(String);
  }

  function moveBetween(ref: string, destination: string) {
    const next: Record<string, JsonValue> = {};
    for (const [name] of groups) {
      next[name] = groupRefs(name).filter((candidate) => candidate !== ref);
    }
    next[destination] = [...list(next[destination]).map(String), ref];
    onValue(next);
  }

  function reorder(group: string, index: number, offset: number) {
    const values = [...groupRefs(group)];
    const target = index + offset;
    if (target < 0 || target >= values.length) return;
    [values[index], values[target]] = [values[target], values[index]];
    onValue({ ...partition, [group]: values });
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
      <div className="choice-partition-group">
        <strong>{label}</strong>
        <span className="choice-help">{orderLabel}</span>
        <ol
          className="choice-order"
          aria-label={`${label} group order. ${orderLabel}`}
        >
          {refs.map((ref, index) => (
            <li key={ref} data-card-ref={ref}>
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
      <div className="choice-options">
        {options.map((option, optionIndex) => {
          const ref = text(option.value);
          const selectedGroup = groups.find(([name]) =>
            groupRefs(name).includes(ref),
          )?.[0] ?? groups[0][0];
          return (
            <label key={ref} className="choice-option">
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
      <div className="choice-partition-orders">
        {groups.map(([name, descriptor]) => (
          <div key={name}>{orderedGroup(name, descriptor)}</div>
        ))}
      </div>
    </fieldset>
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

function TargetControl({
  field,
  values,
  onChange,
  labelFor,
}: {
  field: ChoiceField;
  values: ChoiceValues;
  onChange: (values: ChoiceValues) => void;
  labelFor: (value: string) => string;
}) {
  const schema = record(field.schema);
  const name = text(field.name || "targets");
  const modes = list(values.modes).map(String);
  const targets = record(values[name]);
  const legalModes = list(schema.legal_modes).map(String);
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
    let next = checked ? [...selected, ref] : selected.filter((item) => item !== ref);
    if (next.length > maximum) {
      next = maximum === 1 ? [ref] : [...next.slice(-(maximum - 1)), ref];
    }
    onChange({ ...values, [name]: { ...targets, [id]: next } });
  }
  return (
    <fieldset className="choice-field target-control">
      <legend><FieldLabel field={field} /></legend>
      {legalModes.length > 0 && (
        <div className="choice-options">
          {legalModes.map((mode) => (
            <label key={mode} className="choice-option">
              <input
                type="checkbox"
                data-testid={`choice-mode-${testValue(mode)}`}
                checked={modes.includes(mode)}
                onChange={(event) => toggleMode(mode, event.target.checked)}
              />
              <span>{mode}</span>
            </label>
          ))}
        </div>
      )}
      {targetGroups(field, values).map((group) => {
        const id = text(group.id || "target");
        const selected = list(targets[id]).map(String);
        return (
          <div key={id} className="target-group">
            <strong>{text(group.label || id)} ({text(group.min ?? 1)}–{text(group.max ?? 1)})</strong>
            <div className="choice-options">
              {list(group.legal_refs).map((rawRef) => {
                const ref = text(rawRef);
                return (
                  <label key={ref} className="choice-option">
                    <input
                      type="checkbox"
                      data-testid={`choice-target-${testValue(ref)}`}
                      checked={selected.includes(ref)}
                      onChange={(event) => toggleTarget(group, ref, event.target.checked)}
                    />
                    <span>{labelFor(ref)}</span>
                  </label>
                );
              })}
            </div>
          </div>
        );
      })}
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
        return (
          <label key={key}>
            {text(row.label) || labelFor(key)}
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
                return <option key={text(option.value)} value={text(option.value)}>{text(option.label) || labelFor(text(option.value))}</option>;
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
        return (
          <div key={source} className="damage-source">
            <strong>{labelFor(source)} assigns {text(sourceData.power)} damage</strong>
            {list(sourceData.targets).map((rawTarget) => {
              const target = text(rawTarget);
              return (
                <label key={target}>
                  {labelFor(target)}
                  <input
                    type="number"
                    min={0}
                    max={Number(sourceData.power ?? 0)}
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
  const submitted = list(value);
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
    onValue(nextCopies);
  }
  return (
    <fieldset className="choice-field copy-targets">
      <legend><FieldLabel field={field} /></legend>
      {copies.map((copy, index) => {
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
                  <span>{text(group.label || id)}</span>
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
}: {
  field: ChoiceField;
  values: ChoiceValues;
  onChange: (values: ChoiceValues) => void;
  labelFor: (value: string) => string;
}) {
  const name = text(field.name);
  const control = text(field.control);
  const value = values[name];
  const set = (next: JsonValue) => onChange({ ...values, [name]: next });
  if (control === "mana_modes") return <ManaModes field={field} value={value} onValue={set} />;
  if (control === "refs") return <RefOptions field={field} value={value} onValue={set} labelFor={labelFor} />;
  if (control === "ordered_partition") return <OrderedPartition field={field} value={value} onValue={set} labelFor={labelFor} />;
  if (control === "targets") return <TargetControl field={field} values={values} onChange={onChange} labelFor={labelFor} />;
  if (control === "assignment_map") return <AssignmentMap field={field} value={value} onValue={set} labelFor={labelFor} />;
  if (control === "damage_assignments") return <DamageAssignments field={field} value={value} onValue={set} labelFor={labelFor} />;
  if (control === "object_map") return <ObjectMap field={field} value={value} onValue={set} labelFor={labelFor} />;
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
        <input data-testid={`choice-${name}`} type="number" min={Number(field.minimum ?? 0)} max={field.maximum === undefined ? undefined : Number(field.maximum)} value={Number(value ?? 0)} onChange={(event) => set(Number(event.target.value))} />
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
            return <option key={text(rawValue)} value={text(rawValue)}>{text(option.label) || labelFor(text(rawValue))}</option>;
          })}
        </select>
      </label>
    );
  }
  if (control === "copy_targets") return <CopyTargets field={field} value={value} onValue={set} labelFor={labelFor} />;
  return <p className="choice-warning">This server-issued control is not renderable by this browser version.</p>;
}

export function ChoiceFormView({ form, values, onChange, labelFor }: Props) {
  const variants = record(form.variants);
  const selector = text(variants.field || "cost_option");
  const variantOptions = list(variants.options).map(record);
  return (
    <div className="choice-form-fields">
      {variantOptions.length > 0 && (
        <label className="choice-field">
          {text(variants.label || "Casting cost")}
          <select
            data-testid={`choice-${selector}`}
            value={text(values[selector] ?? variants.default)}
            onChange={(event) => onChange(choicesWithDefaults(form, { ...values, [selector]: event.target.value }))}
          >
            {variantOptions.map((option) => <option key={text(option.value)} value={text(option.value)}>{text(option.label)}</option>)}
          </select>
        </label>
      )}
      {activeFields(form, values).map((field) => (
        <ChoiceControl key={text(field.name)} field={field} values={values} onChange={onChange} labelFor={labelFor} />
      ))}
    </div>
  );
}
