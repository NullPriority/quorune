import { useState } from "react";
import { asRecord, CardTile } from "./CardSurface";
import { projectedChoiceCards } from "./CardChoiceGrid";
import { ChoiceFormView } from "./ChoiceForm";
import { executableChoices, initialChoices, orderedPartitionNames, validateChoices, type ChoiceValues } from "./choices";
import type { JsonValue, LegalAction } from "./generated/protocol";
import type { ProjectedView } from "./protocol";

export function contextualDecisionAction(view: ProjectedView): LegalAction | undefined {
  const decision = view.decision;
  if (!decision || decision.legal_actions.length !== 1) return undefined;
  const action = decision.legal_actions[0];
  const field = action.form?.fields[0];
  const operation = decision.ctx.operation;
  const singleScry = operation === "scry" && field?.control === "ordered_partition"
    && Array.isArray(field.options) && field.options.length === 1;
  if (decision.kind === "combat.attackers" || decision.kind === "combat.blockers") return action;
  if (!asRecord(decision.ctx.presentation).title) return undefined;
  if (decision.kind === "semantic.search" || operation === "offer_optional_effect" || operation === "offer_draw" || singleScry) return action;
  return undefined;
}

export function ContextualDecision({
  action, view, disabled, submitting, labelFor, onSubmit,
}: {
  action: LegalAction;
  view: ProjectedView;
  disabled: boolean;
  submitting: boolean;
  labelFor: (ref: string) => string;
  onSubmit: (action: LegalAction, choices: ChoiceValues) => void;
}) {
  const decision = view.decision!;
  const form = action.form!;
  const [values, setValues] = useState(() => initialChoices(form));
  const [errors, setErrors] = useState<string[]>([]);
  const context = asRecord(decision.ctx.presentation);
  const isCombat = decision.kind.startsWith("combat.");
  const isAttack = decision.kind === "combat.attackers";
  const isOptional = decision.ctx.operation === "offer_optional_effect" || decision.ctx.operation === "offer_draw";
  const isScry = decision.ctx.operation === "scry";
  const source = typeof context.source === "string" ? context.source : "";
  const title = isCombat ? isAttack ? "Declare attackers" : "Declare blockers" : String(context.title);
  const detail = isCombat
    ? isAttack ? "Choose a creature, then its defender." : `Defending seat ${decision.actor}. Assign each blocker to an incoming attacker.`
    : String(context.detail ?? "");
  function commit(next: ChoiceValues) {
    const problems = validateChoices(form, next);
    setErrors(problems);
    if (!disabled && problems.length === 0) onSubmit(action, executableChoices(form, next));
  }
  const save = (next: ChoiceValues) => { setValues(next); setErrors([]); };
  const field = form.fields[0];
  const option = isScry && Array.isArray(field.options) ? asRecord(field.options[0]) : {};
  const scryRef = String(option.value ?? "");
  const scryCard = isScry ? projectedChoiceCards(view).get(scryRef) : undefined;
  const scryGroups = isScry ? orderedPartitionNames(field) : [];
  const chooseOptions = Array.isArray(field.options) ? field.options.map(asRecord) : [];
  const isSearchWithoutSelection = !isCombat && !isScry && !isOptional
    && Number(field.minimum) === 0 && Array.isArray(values.search_cards) && values.search_cards.length === 0;

  return <form className={`contextual-decision${isCombat ? " combat-decision" : ""}`}
    data-testid="contextual-decision" aria-label={title}
    onSubmit={(event) => { event.preventDefault(); commit(values); }}>
    <header>
      {source && <span>{source}</span>}
      <h2>{title}</h2>
      {detail && <p>{detail}</p>}
    </header>
    {isScry && <div className="scry-preview">
      {scryCard ? <CardTile value={scryCard} view={view} compact /> : <strong>{String(option.label ?? labelFor(scryRef))}</strong>}
    </div>}
    {!isOptional && !isScry && <div className="contextual-fields">
      <ChoiceFormView form={form} values={values} onChange={save} labelFor={labelFor} view={view} />
    </div>}
    {errors.length > 0 && <p className="choice-warning" role="alert">{errors.join(" ")}</p>}
    <footer>
      {isOptional && chooseOptions.map((option) =>
        <button key={String(option.value)} data-testid={`decision-${option.value}`} type="button"
          className={option.value === "decline" ? "secondary-button" : ""}
          disabled={disabled} onClick={() => commit({ choice: option.value as JsonValue })}>
          {option.value === "decline" ? "Decline" : String(context.accept ?? "Apply")}
        </button>)}
      {isScry && scryGroups.map((group, index) =>
        <button key={group} type="button" disabled={disabled}
          onClick={() => commit({ [String(field.name)]: Object.fromEntries(scryGroups.map((name) =>
            [name, name === group ? [scryRef] : []])) })}>
          {index === 0 ? "Top" : "Bottom"}
        </button>)}
      {!isOptional && !isScry && <button type="submit" data-testid="submit-choice" disabled={disabled}>
        {submitting ? "Submitting…" : isCombat ? (isAttack ? "Confirm attackers" : "Confirm blockers")
          : isSearchWithoutSelection ? "Find no card" : String(context.accept ?? "Confirm selection")}
      </button>}
      {submitting && (isScry || isOptional) && <small role="status">Submitting…</small>}
    </footer>
  </form>;
}
