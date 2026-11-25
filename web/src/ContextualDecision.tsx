import { useState } from "react";
import { asRecord, CardTile } from "./CardSurface";
import { projectedChoiceCards } from "./CardChoiceGrid";
import { ChoiceFormView } from "./ChoiceForm";
import { choiceCompatibilityErrors, executableChoices, initialChoices, orderedPartitionNames, validateChoices, type ChoiceValues } from "./choices";
import type { JsonValue, LegalAction } from "./generated/protocol";
import type { ProjectedView } from "./protocol";
import { decisionDetail, decisionHeading } from "./tablePresentation";
import { SeatLabel } from "./SeatLabel";

export function contextualDecisionAction(view: ProjectedView): LegalAction | undefined {
  const decision = view.decision;
  if (!decision || decision.kind === "priority" || decision.legal_actions.length !== 1) return undefined;
  const action = decision.legal_actions[0];
  return action.form ? action : undefined;
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
  const [minimized, setMinimized] = useState(false);
  const compatibilityErrors = choiceCompatibilityErrors(form, values);
  const displayedErrors = compatibilityErrors.length ? compatibilityErrors : errors;
  const commitDisabled = disabled || compatibilityErrors.length > 0;
  const context = asRecord(decision.ctx.presentation);
  const isAttack = decision.kind === "combat.attackers";
  const isCombat = isAttack || decision.kind === "combat.blockers";
  const field = form.fields[0];
  const operation = decision.ctx.operation;
  const isOptional = Boolean(context.title) && (operation === "offer_optional_effect" || operation === "offer_draw")
    && field?.name === "choice" && Array.isArray(field.options) && field.options.length > 0;
  const singleScry = Boolean(context.title) && operation === "scry" && field?.control === "ordered_partition"
    && Array.isArray(field.options) && field.options.length === 1;
  const namedDecision = ["state.legend", "battle.enter_protector", "state.battle_protector"].includes(decision.kind);
  const source = typeof context.source === "string" ? context.source
    : namedDecision && typeof decision.ctx.name === "string" ? decision.ctx.name : "";
  const title = isCombat ? isAttack ? "Declare attackers" : "Declare blockers" : String(context.title ?? decisionHeading(decision));
  const detail = isCombat
    ? isAttack ? "Choose a creature, then its defender." : `Defending seat ${decision.actor}. Assign each blocker to an incoming attacker.`
    : String(context.detail ?? decisionDetail(decision));
  function commit(next: ChoiceValues) {
    if (minimized) return;
    const problems = validateChoices(form, next);
    setErrors(problems);
    if (!disabled && problems.length === 0) onSubmit(action, executableChoices(form, next));
  }
  const save = (next: ChoiceValues) => { setValues(next); setErrors([]); };
  const option = singleScry && Array.isArray(field.options) ? asRecord(field.options[0]) : {};
  const scryRef = String(option.value ?? "");
  const scryCard = singleScry ? projectedChoiceCards(view).get(scryRef) : undefined;
  const scryGroups = singleScry ? orderedPartitionNames(field) : [];
  const chooseOptions = isOptional && Array.isArray(field.options) ? field.options.map(asRecord) : [];
  const isSearchWithoutSelection = decision.kind === "semantic.search"
    && Number(field?.minimum) === 0 && Array.isArray(values.search_cards) && values.search_cards.length === 0;

  return <form className={`contextual-decision${isCombat ? " combat-decision" : ""}`}
    data-testid="contextual-decision" aria-label={title}
    onSubmit={(event) => { event.preventDefault(); commit(values); }}>
    <header>
      <div className="decision-context">
        <SeatLabel seat={decision.actor} />
        {source && source !== title && <span>{source}</span>}
      </div>
      <h2>{title}</h2>
      {detail && detail !== title && <p>{detail}</p>}
    </header>
    {!minimized && singleScry && <div className="scry-preview">
      {scryCard ? <CardTile value={scryCard} view={view} compact /> : <strong>{String(option.label ?? labelFor(scryRef))}</strong>}
    </div>}
    {!minimized && !isOptional && !singleScry && <div className="contextual-fields">
      <ChoiceFormView form={form} values={values} onChange={save} labelFor={labelFor} view={view} />
    </div>}
    {displayedErrors.length > 0 && <p className="choice-warning" role="alert">{displayedErrors.join(" ")}</p>}
    <footer>
      {!isCombat && <button type="button" className="secondary-button" aria-expanded={!minimized}
        disabled={submitting} onClick={() => setMinimized(!minimized)}>
        {minimized ? "Show choice" : "Minimize choice"}
      </button>}
      {!minimized && chooseOptions.map((option) =>
        <button key={String(option.value)} data-testid={`decision-${option.value}`} type="button"
          className={option.value === "decline" ? "secondary-button" : ""}
          disabled={commitDisabled} onClick={() => commit({ choice: option.value as JsonValue })}>
          {option.value === "decline" ? "Decline" : String(context.accept ?? "Apply")}
        </button>)}
      {!minimized && scryGroups.map((group, index) =>
        <button key={group} type="button" disabled={commitDisabled}
          onClick={() => commit({ [String(field.name)]: Object.fromEntries(scryGroups.map((name) =>
            [name, name === group ? [scryRef] : []])) })}>
          {index === 0 ? "Top" : "Bottom"}
        </button>)}
      {!minimized && !isOptional && !singleScry && <button type="submit" data-testid="submit-choice" disabled={commitDisabled}>
        {submitting ? "Submitting…" : isCombat ? (isAttack ? "Confirm attackers" : "Confirm blockers")
          : isSearchWithoutSelection ? "Find no card" : String(context.accept ?? form.submit_label ?? "Confirm selection")}
      </button>}
      {submitting && (singleScry || isOptional) && <small role="status">Submitting…</small>}
    </footer>
  </form>;
}
