import type { ChoiceForm, JsonValue } from "./generated/protocol";

export type ChoiceValues = Record<string, JsonValue>;
export type ChoiceField = Record<string, JsonValue>;

export function record(value: JsonValue | undefined): Record<string, JsonValue> {
  return value && !Array.isArray(value) && typeof value === "object" ? value : {};
}

export function list(value: JsonValue | undefined): JsonValue[] {
  return Array.isArray(value) ? value : [];
}

function stringValue(value: JsonValue | undefined): string {
  return value === undefined || value === null ? "" : String(value);
}

export function orderedPartitionNames(field: ChoiceField): string[] {
  const groups = Object.entries(record(field.partitions));
  if (groups.length !== 2) return ["top", "bottom"];
  return groups
    .sort(([, left], [, right]) => {
      const leftIsLibraryTop = record(left).order === "top_to_bottom";
      const rightIsLibraryTop = record(right).order === "top_to_bottom";
      return Number(rightIsLibraryTop) - Number(leftIsLibraryTop);
    })
    .map(([name]) => name);
}

function initialField(field: ChoiceField): JsonValue | undefined {
  if (field.default !== undefined) return structuredClone(field.default);
  const control = stringValue(field.control);
  if (control === "boolean") {
    const legal = list(field.legal_values).filter(
      (candidate): candidate is boolean => typeof candidate === "boolean",
    );
    return legal.length ? legal[0] : false;
  }
  if (control === "integer") return Number(field.minimum ?? 0);
  if (control === "mana_modes") {
    return structuredClone(record(list(field.options)[0]).value ?? {});
  }
  if (control === "refs") {
    const options = list(field.options).map((option) => record(option).value ?? "");
    if (
      field.ordered
      && Number(field.minimum) === options.length
      && Number(field.maximum) === options.length
    ) return options;
    return [];
  }
  if (control === "ordered_partition") {
    const [primary, secondary] = orderedPartitionNames(field);
    return {
      [primary]: list(field.options).map((option) =>
        stringValue(record(option).value),
      ),
      [secondary]: [],
    };
  }
  if (control === "copy_targets") {
    return list(field.copies).map((copy) =>
      structuredClone(record(copy).default_targets ?? []),
    );
  }
  if (control === "damage_assignments") {
    const sources = record(record(field.combat).damage_sources);
    return Object.entries(sources).flatMap(([source, raw]) => {
      const sourceData = record(raw);
      const target = list(sourceData.targets)[0];
      const power = Number(sourceData.power ?? 0);
      return target !== undefined && power > 0 ? [{ source, target, amount: power }] : [];
    });
  }
  if (["object_map", "assignment_map", "targets"].includes(control)) return {};
  if (control === "select" && field.required) {
    return record(list(field.options)[0]).value;
  }
  if (control === "ref" && field.required) {
    return record(list(field.options)[0]).value;
  }
  if (control === "text") return "";
  return undefined;
}

export function activeFields(form: ChoiceForm, values: ChoiceValues): ChoiceField[] {
  const fields = form.fields.map((field) => record(field));
  const variants = record(form.variants);
  const options = list(variants.options);
  if (!options.length) return fields.map((field) => resolveDependentField(field, values));
  const selector = stringValue(variants.field || "cost_option");
  const selected = stringValue(values[selector] ?? variants.default);
  const variant = options
    .map((option) => record(option))
    .find((option) => stringValue(option.value) === selected);
  return [...fields, ...list(variant?.fields).map((field) => record(field))]
    .map((field) => resolveDependentField(field, values));
}

function resolveDependentField(field: ChoiceField, values: ChoiceValues): ChoiceField {
  const source = stringValue(field.options_from_map);
  if (!source) return field;
  const requiredValue = stringValue(field.required_value);
  const refs = Object.entries(record(values[source]))
    .filter(([, value]) => stringValue(value) === requiredValue)
    .map(([ref]) => ref);
  const wanted = new Set(refs);
  const selected = new Set(list(values[stringValue(field.name)]).map(String));
  return {
    ...field,
    required: refs.length > 0,
    minimum: refs.length,
    maximum: refs.length,
    legal_refs: refs,
    options: list(field.options)
      .map((rawOption) => {
        const option = record(rawOption);
        return {
          ...option,
          available: wanted.has(stringValue(option.value)),
        } as ChoiceField;
      })
      .filter((option) =>
        Boolean(option.available) || selected.has(stringValue(option.value)),
      ),
  };
}

export function choicesWithDefaults(
  form: ChoiceForm,
  current: ChoiceValues,
): ChoiceValues {
  const values: ChoiceValues = structuredClone(current);
  const variants = record(form.variants);
  if (list(variants.options).length && values[stringValue(variants.field || "cost_option")] === undefined) {
    const selector = stringValue(variants.field || "cost_option");
    values[selector] = variants.default ?? record(list(variants.options)[0]).value ?? "";
  }
  for (const field of activeFields(form, values)) {
    const name = stringValue(field.name);
    if (!name) continue;
    const initial = initialField(field);
    if (initial !== undefined) values[name] = initial;
  }
  return values;
}

export function initialChoices(form: ChoiceForm): ChoiceValues {
  return choicesWithDefaults(form, {});
}

function selectedTargetGroups(field: ChoiceField, values: ChoiceValues): ChoiceField[] {
  const schema = record(field.schema);
  const modeSchemas = record(schema.mode_schemas);
  if (Object.keys(modeSchemas).length) {
    return list(values.modes).flatMap((mode) =>
      list(record(modeSchemas[String(mode)]).groups).map((group) => record(group)),
    );
  }
  return list(schema.groups).map((group) => record(group));
}

export function updateModalTargetSelection(
  schema: ChoiceField,
  modes: string[],
  targets: ChoiceField,
  mode: string,
  checked: boolean,
): { modes: string[]; targets: ChoiceField } {
  const legalModes = list(schema.legal_modes).map(String);
  const maximum = Number(schema.max_modes ?? schema.mode_count ?? 1);
  let next = checked
    ? [...modes.filter((candidate) => candidate !== mode), mode]
    : modes.filter((candidate) => candidate !== mode);
  if (next.length > maximum) {
    next = maximum === 1 ? [mode] : [...next.slice(-(maximum - 1)), mode];
  }
  const printedOrder = new Map(legalModes.map((candidate, index) => [candidate, index]));
  next.sort((left, right) =>
    (printedOrder.get(left) ?? legalModes.length)
    - (printedOrder.get(right) ?? legalModes.length));

  const modeSchemas = record(schema.mode_schemas);
  const groups = next.flatMap((selectedMode) =>
    list(record(modeSchemas[selectedMode]).groups).map((group) => record(group)));
  const retained: ChoiceField = {};
  for (const group of groups) {
    const groupId = stringValue(group.id || "target");
    const legalRefs = group.legal_refs === undefined
      ? null
      : new Set(list(group.legal_refs).map(String));
    const selected = list(targets[groupId])
      .map(String)
      .filter((ref) => legalRefs === null || legalRefs.has(ref))
      .slice(0, Number(group.max ?? 1));
    if (selected.length) retained[groupId] = selected;
  }
  return { modes: next, targets: retained };
}

export function copyTargetGroups(copy: ChoiceField): ChoiceField[] {
  const schema = record(copy.target_schema);
  const modeSchemas = record(schema.mode_schemas);
  if (Object.keys(modeSchemas).length) {
    return list(copy.modes).flatMap((mode) =>
      list(record(modeSchemas[String(mode)]).groups).map((group) => record(group)),
    );
  }
  return list(schema.groups).map((group) => record(group));
}

function refsForCopyGroup(
  rawTargets: JsonValue | undefined,
  group: ChoiceField,
  groupCount: number,
): string[] {
  if (Array.isArray(rawTargets)) {
    if (groupCount === 1) return rawTargets.map(String);
    const legal = new Set(list(group.legal_refs).map(String));
    return rawTargets.map(String).filter((ref) => legal.has(ref));
  }
  return list(record(rawTargets)[stringValue(group.id || "target")]).map(String);
}

function fieldErrors(field: ChoiceField, values: ChoiceValues): string[] {
  const name = stringValue(field.name);
  const label = stringValue(field.label || name);
  const control = stringValue(field.control);
  const value = values[name];
  const required = Boolean(field.required);
  const errors: string[] = [];
  if (["text", "select", "ref"].includes(control)) {
    if (required && (value === undefined || value === null || value === "")) {
      errors.push(`${label} is required.`);
    }
  } else if (control === "boolean") {
    const legal = list(field.legal_values).filter(
      (candidate): candidate is boolean => typeof candidate === "boolean",
    );
    if (required && value === undefined) {
      errors.push(`${label} is required.`);
    } else if (typeof value !== "boolean" || (legal.length && !legal.includes(value))) {
      errors.push(`${label} requires explicit confirmation.`);
    }
  } else if (control === "mana_modes") {
    const serialized = JSON.stringify(value ?? {});
    const legal = new Set(
      list(field.options).map((option) =>
        JSON.stringify(record(option).value ?? {}),
      ),
    );
    if (required && !legal.has(serialized)) {
      errors.push(`${label} requires a legal mana choice.`);
    }
  } else if (control === "integer") {
    const number = Number(value);
    if (!Number.isInteger(number)) errors.push(`${label} must be an integer.`);
    if (field.minimum !== undefined && number < Number(field.minimum)) {
      errors.push(`${label} must be at least ${field.minimum}.`);
    }
    if (field.maximum !== undefined && number > Number(field.maximum)) {
      errors.push(`${label} must be at most ${field.maximum}.`);
    }
  } else if (control === "refs") {
    const selected = list(value).map(String);
    const count = selected.length;
    if (field.minimum !== undefined && count < Number(field.minimum)) {
      errors.push(`${label} requires at least ${field.minimum} selection(s).`);
    }
    if (field.maximum !== undefined && count > Number(field.maximum)) {
      errors.push(`${label} allows at most ${field.maximum} selection(s).`);
    }
    const legalValues = field.legal_refs === undefined
      ? list(field.options).map((option) => record(option).value)
      : list(field.legal_refs);
    const legal = new Set(legalValues.map(stringValue));
    if (selected.some((ref) => !legal.has(ref))) {
      errors.push(`${label} contains a selection that is no longer available.`);
    }
  } else if (control === "ordered_partition") {
    const partition = record(value);
    const groups = orderedPartitionNames(field);
    const selected = groups.flatMap((group) =>
      list(partition[group]).map(String),
    );
    const legal = list(field.options).map((option) =>
      stringValue(record(option).value),
    );
    if (selected.length !== legal.length) {
      errors.push(`${label} must place every card in one destination group.`);
    }
    if (new Set(selected).size !== selected.length) {
      errors.push(`${label} cannot place the same card twice.`);
    }
    const legalSet = new Set(legal);
    if (selected.some((ref) => !legalSet.has(ref))) {
      errors.push(`${label} contains a card that is no longer available.`);
    }
  } else if (control === "object_map") {
    const count = Object.keys(record(value)).length;
    if (field.minimum !== undefined && count < Number(field.minimum)) {
      errors.push(`${label} requires ${field.minimum} choice(s).`);
    }
  } else if (control === "assignment_map") {
    const assignments = Object.values(record(value)).map(stringValue);
    for (const [group, rawMinimum] of Object.entries(
      record(field.minimum_group_sizes),
    )) {
      const minimum = Number(rawMinimum);
      const count = assignments.filter((target) => target === group).length;
      if (count > 0 && count < minimum) {
        errors.push(
          `${label} requires either no assignments or at least ${minimum} assignments to ${group}.`,
        );
      }
    }
  } else if (control === "targets") {
    const schema = record(field.schema);
    const modes = list(values.modes);
    if (schema.min_modes !== undefined && modes.length < Number(schema.min_modes)) {
      errors.push(`Choose at least ${schema.min_modes} mode(s).`);
    }
    if (schema.max_modes !== undefined && modes.length > Number(schema.max_modes)) {
      errors.push(`Choose at most ${schema.max_modes} mode(s).`);
    }
    const targets = record(value);
    for (const group of selectedTargetGroups(field, values)) {
      const groupId = stringValue(group.id || "target");
      const count = list(targets[groupId]).length;
      if (count < Number(group.min ?? 1) || count > Number(group.max ?? 1)) {
        errors.push(
          `${stringValue(group.label || groupId)} requires between ${group.min ?? 1} and ${group.max ?? 1} target(s).`,
        );
      }
    }
  } else if (control === "copy_targets") {
    const copies = list(field.copies).map(record);
    const submitted = list(value);
    if (submitted.length !== Number(field.copy_count ?? copies.length)) {
      errors.push(`${label} requires one target selection per copy.`);
    }
    copies.forEach((copy, index) => {
      const groups = copyTargetGroups(copy);
      groups.forEach((group) => {
        const count = refsForCopyGroup(submitted[index], group, groups.length).length;
        const minimum = Number(group.min ?? 1);
        const maximum = Number(group.max ?? 1);
        if (count < minimum || count > maximum) {
          errors.push(
            `Copy ${index + 1} ${stringValue(group.label || group.id || "target")} requires between ${minimum} and ${maximum} target(s).`,
          );
        }
      });
    });
  } else if (control === "damage_assignments") {
    const sources = record(record(field.combat).damage_sources);
    const assignments = list(value).map(record);
    for (const [source, rawSource] of Object.entries(sources)) {
      const required = Number(record(rawSource).power ?? 0);
      const assigned = assignments
        .filter((row) => row.source === source)
        .reduce((total, row) => total + Number(row.amount ?? 0), 0);
      if (assigned !== required) {
        errors.push(`${source} must assign exactly ${required} damage.`);
      }
    }
  }
  return errors;
}

export function validateChoices(form: ChoiceForm, values: ChoiceValues): string[] {
  return activeFields(form, values).flatMap((field) => fieldErrors(field, values));
}

export function executableChoices(form: ChoiceForm, values: ChoiceValues): ChoiceValues {
  const permitted = new Set<string>();
  for (const field of activeFields(form, values)) {
    const name = stringValue(field.name);
    if (name) permitted.add(name);
    if (field.control === "targets") permitted.add("modes");
  }
  const variants = record(form.variants);
  if (list(variants.options).length) permitted.add(stringValue(variants.field || "cost_option"));
  const result: ChoiceValues = {};
  for (const [name, value] of Object.entries(values)) {
    if (!permitted.has(name) || value === undefined || value === null) continue;
    if (name === "yield" && value === "none") continue;
    result[name] = structuredClone(value);
  }
  return result;
}

export function targetGroups(field: ChoiceField, values: ChoiceValues): ChoiceField[] {
  return selectedTargetGroups(field, values);
}
