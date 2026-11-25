import { asList, asRecord } from "./CardSurface";
import { projectedChoiceCards } from "./CardChoiceGrid";
import { ChoiceFormView } from "./ChoiceForm";
import {
  activeFields, activeVariants, list, record, selectedTargetCostLabel, targetGroupModes, targetGroups, type ChoiceValues,
} from "./choices";
import type { JsonValue, LegalAction } from "./generated/protocol";
import type { ProjectedView } from "./protocol";
import { actionTitle, cardActionCost, modeLabel, phyrexianCastCostLabels } from "./tablePresentation";
import { ManaText } from "./ManaText";
import { SeatLabel } from "./SeatLabel";

type Step = {
  id: string;
  label: string;
  field: string;
  group?: string;
  refs: string[];
  selected: string[];
  minimum: number;
  maximum: number;
};

export type TableObjectSelection = {
  refs: string[];
  selected: string[];
  select: (ref: string) => void;
};

export function usesModeTargetDraft(action: LegalAction | null, values: ChoiceValues): boolean {
  if (!action?.form || !["activate", "cast"].includes(action.action)) return false;
  const target = activeFields(action.form, values).find((field) => field.control === "targets");
  const schema = record(target?.schema);
  return list(schema.legal_modes).length > 0 && !schema.damage_allocation;
}

export function publicObjectSteps(action: LegalAction | null, view: ProjectedView, values: ChoiceValues): Step[] {
  if (!action?.form || !["activate", "cast"].includes(action.action)) return [];
  const players = asRecord(view.state.players);
  const cardRows = [...asList(view.state.stack), ...Object.values(players).flatMap((player) =>
    ["bf", "hand", "gy", "ex", "cmd", "known_top"].flatMap((zone) => asList(asRecord(player)[zone])))];
  const visible = new Set([...Object.keys(players), ...cardRows.map((card) => String(asRecord(card).id))]);
  const fields = activeFields(action.form, values);
  const result: Step[] = [];
  const target = fields.find((field) => field.control === "targets");
  if (target) {
    const schema = record(target.schema);
    if (schema.damage_allocation) return [];
    const modes = targetGroupModes(target);
    const modeLabels = record(schema.mode_labels);
    const offeredModes = list(schema.legal_modes).map(String);
    for (const group of targetGroups(target, values)) {
      const id = String(group.id ?? "target");
      const mode = modes.get(id);
      const modeTitle = mode ? modeLabels[mode] || modeLabel(mode, offeredModes.indexOf(mode)) : "";
      const label = String(group.label ?? "Choose target");
      result.push({
        id: `target-${id}`, label: modeTitle ? `${modeTitle} · ${label}` : label, field: String(target.name),
        group: id, refs: list(group.legal_refs).map(String), selected: list(record(values[String(target.name)])[id]).map(String),
        minimum: Number(group.min ?? 1), maximum: Number(group.max ?? 1),
      });
    }
  }
  const cost = fields.find((field) => field.name === "cost_cards" && field.control === "refs" && !field.ordered);
  if (cost) {
    result.push({
      id: "cost", label: String(cost.label), field: "cost_cards",
      refs: list(cost.options).map((option) => String(record(option).value)), selected: list(values.cost_cards).map(String),
      minimum: Number(cost.minimum ?? 0), maximum: Number(cost.maximum ?? 1),
    });
  }
  return result.length && result.every((step) => step.refs.length > 0 && step.refs.every((ref) => visible.has(ref)))
    ? result : [];
}

export function stageTableObject(step: Step, ref: string, values: ChoiceValues): ChoiceValues {
  if (!step.refs.includes(ref) || step.maximum <= 0) return values;
  const hasRef = step.selected.includes(ref);
  const selected = hasRef ? step.selected.filter((value) => value !== ref)
    : step.maximum === 1 ? [ref] : [...step.selected, ref].slice(0, step.maximum);
  const value: JsonValue = step.group ? { ...record(values[step.field]), [step.group]: selected } : selected;
  return { ...values, [step.field]: value };
}

export function PublicActionDraft({
  action, values, steps, activeStep, view, onStage, onValues, onCancel, onSubmit, onPayManually, labelFor, errors, disabled, commitBlocked,
}: {
  action: LegalAction; values: ChoiceValues; steps: Step[]; activeStep: string; view: ProjectedView;
  onStage: (step: string) => void; onValues: (values: ChoiceValues) => void;
  onCancel: () => void; onSubmit: () => void; labelFor: (ref: string) => string;
  onPayManually?: () => void;
  errors: string[]; disabled: boolean; commitBlocked: boolean;
}) {
  const form = action.form!;
  const onBoard = new Set(steps.map((step) => step.field));
  const target = activeFields(form, values).find((field) => field.control === "targets");
  const schema = record(target?.schema);
  const modes = list(schema.legal_modes).map(String);
  const modeLabels = record(schema.mode_labels);
  const hasModes = modes.length > 0;
  const sourceRef = String(action.card ?? action.source ?? "");
  const source = modes.some((mode) => !modeLabels[mode]) ? asRecord(projectedChoiceCards(view).get(sourceRef)) : {};
  const definition = view.definitions[String(source.cid ?? "")];
  const face = action.face ? asList(definition?.faces).map(asRecord).find((entry) => entry.n === action.face) : undefined;
  const spellText = face?.o ?? source.o ?? definition?.o;
  const modesReady = !hasModes || list(values.modes).length >= Number(schema.min_modes ?? schema.mode_count ?? 1);
  const targetsReady = !target || targetGroups(target, values).every((group) =>
    list(record(values[String(target.name)])[String(group.id ?? "target")]).length >= Number(group.min ?? 1));
  const quotedPrice = target && modesReady && targetsReady ? selectedTargetCostLabel(target, values) : "";
  const tableTargets = target && hasModes ? {
    field: String(target.name),
    groupIds: new Set(steps.flatMap((step) => step.field === target.name && step.group ? [step.group] : [])),
  } : undefined;
  if (tableTargets) onBoard.delete(tableTargets.field);
  const remaining = {
    ...form,
    fields: form.fields.filter((field) => !onBoard.has(String(field.name))),
    variants: form.variants ? {
      ...form.variants,
      options: list(form.variants.options).map((raw) => {
        const option = record(raw);
        return { ...option, fields: list(option.fields).filter((field) => !onBoard.has(String(record(field).name))) };
      }),
    } : undefined,
  };
  const hasRemainingChoices = activeFields(remaining, values).length > 0
    || list(activeVariants(remaining, values).options).length > 1;
  const cost = cardActionCost([action]);
  return <form className="public-action-draft" data-testid="public-action-draft" aria-label="Prepare action"
    onSubmit={(event) => { event.preventDefault(); onSubmit(); }}>
    <header data-action-source={sourceRef}>
      <SeatLabel seat={view.decision?.actor} />
      <h2><ManaText text={actionTitle(action)} /></h2>{(quotedPrice || cost) && <p aria-live="polite"><ManaText text={quotedPrice || cost} /></p>}
    </header>
    <div className="draft-steps">
      {steps.map((step) =>
        <button key={step.id} type="button" className="secondary-button"
          aria-pressed={activeStep === step.id} onClick={() => onStage(step.id)}>
          <strong>{step.label}</strong>
          <span>{step.selected.length} / {step.maximum}{step.selected.length ? ` · ${step.selected.map(labelFor).join(", ")}` : ""}</span>
        </button>)}
    </div>
    {hasRemainingChoices && <div className="contextual-fields">
      {typeof spellText === "string" && spellText && <details className="draft-spell-text">
        <summary>Spell text</summary>
        <p><ManaText text={spellText} /></p>
      </details>}
      <ChoiceFormView form={remaining} values={values} onChange={onValues} labelFor={labelFor} view={view}
        tableTargets={tableTargets} costLabels={phyrexianCastCostLabels(action)} />
    </div>}
    {errors.length > 0 && <p className="choice-warning" role="alert">{errors.join(" ")}</p>}
    <footer>
      {onPayManually && <button type="button" className="secondary-button" onClick={onPayManually} disabled={disabled}>Use manual mana</button>}
      <button type="button" className="secondary-button" onClick={onCancel} disabled={disabled}>Cancel</button>
      <button type="submit" data-testid="submit-choice"
        disabled={disabled || commitBlocked || !modesReady || !targetsReady || steps.some((step) => step.selected.length < step.minimum)}>
        {action.action === "activate" ? "Pay and activate" : "Cast spell"}
      </button>
    </footer>
  </form>;
}
