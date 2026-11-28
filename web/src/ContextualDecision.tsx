import { useEffect, useRef, useState } from "react";
import { asList, asRecord, cardName, CardTile } from "./CardSurface";
import { projectedChoiceCards } from "./CardChoiceGrid";
import { ChoiceFormView } from "./ChoiceForm";
import { choiceCompatibilityErrors, executableChoices, initialChoices, orderedPartitionNames, validateChoices, type ChoiceValues } from "./choices";
import type { JsonValue, LegalAction } from "./generated/protocol";
import type { ProjectedView } from "./protocol";
import { decisionDetail, decisionHeading } from "./tablePresentation";
import { SeatLabel } from "./SeatLabel";
import { ManaText } from "./ManaText";

const TRIGGER_RESPONSE_PREFIX = "quorune:trigger-response:";

export function clearTriggerResponses(gameId: string) {
  const prefix = `${TRIGGER_RESPONSE_PREFIX}${gameId}:`;
  for (const key of Object.keys(sessionStorage)) if (key.startsWith(prefix)) sessionStorage.removeItem(key);
}

export function contextualDecisionAction(view: ProjectedView): LegalAction | undefined {
  const decision = view.decision;
  if (!decision || decision.kind === "priority" || decision.legal_actions.length !== 1) return undefined;
  const action = decision.legal_actions[0];
  return action.form ? action : undefined;
}

export function ContextualDecision({
  action, view, disabled, submitting, labelFor, onSubmit, onInspect,
}: {
  action: LegalAction;
  view: ProjectedView;
  disabled: boolean;
  submitting: boolean;
  labelFor: (ref: string) => string;
  onInspect?: (card: JsonValue) => void;
  onSubmit: (action: LegalAction, choices: ChoiceValues) => void;
}) {
  const decision = view.decision!;
  const form = action.form!;
  const [values, setValues] = useState(() => initialChoices(form));
  const [errors, setErrors] = useState<string[]>([]);
  const [minimized, setMinimized] = useState(false);
  const [remember, setRemember] = useState(false);
  const [reviewLandEntry, setReviewLandEntry] = useState(false);
  const automated = useRef(false);
  const compatibilityErrors = choiceCompatibilityErrors(form, values);
  const displayedErrors = compatibilityErrors.length ? compatibilityErrors : errors;
  const commitDisabled = disabled || compatibilityErrors.length > 0;
  const context = asRecord(decision.ctx.presentation);
  const isFetch = decision.kind === "search.fetch";
  const land = isFetch ? asList(decision.ctx.preview_cards).find((card) => asRecord(card).id === values.search_card) : undefined;
  const landEntryLife = Number(asRecord(land).entry_life ?? 0);
  const payer = asRecord(asRecord(view.state.players)[decision.actor ?? ""]);
  const checkingLandEntry = reviewLandEntry && Boolean(land) && landEntryLife > 0;
  const displayedForm = isFetch ? { ...form, fields: form.fields.filter((candidate) => candidate.name !== "entry_pay_life") } : form;
  const isAttack = decision.kind === "combat.attackers";
  const isCombat = isAttack || decision.kind === "combat.blockers";
  const field = form.fields[0];
  const operation = decision.ctx.operation;
  const isOptional = (operation === "offer_optional_effect" || operation === "offer_draw")
    && field?.name === "choice" && Array.isArray(field.options) && field.options.length > 0;
  const exactBoolean = asList(field?.legal_values).filter((value): value is boolean => typeof value === "boolean");
  const binary = form.fields.length === 1 && field?.control === "boolean";
  const radioOptions = exactBoolean.length ? exactBoolean : [true, false];
  const chooseOptions = isOptional && Array.isArray(field.options) ? field.options.map(asRecord) : [];
  const optionalValues = isOptional ? chooseOptions.map((option) => option.value) : exactBoolean;
  const optionalPayment = binary && field.name === "pay" && ["offer_optional_mana_payment", "remora_tax"].includes(String(operation));
  const optionalDraw = isOptional && operation === "offer_draw" && optionalValues.every((value) => value === "draw" || value === "decline");
  const trigger = asRecord(asList(view.state.stack).find((item) => asRecord(item).id === decision.ctx.stack));
  const gameId = asRecord(view.state.game).id;
  const preferenceKey = (optionalPayment || optionalDraw) && gameId && trigger.cid && trigger.kind === "triggered_ability"
    ? `${TRIGGER_RESPONSE_PREFIX}${gameId}:${JSON.stringify([view.principal, decision.actor, trigger.cid, trigger.ctl, trigger.label, operation, decision.ctx.cost])}`
    : "";
  const singleScry = Boolean(context.title) && operation === "scry" && field?.control === "ordered_partition"
    && Array.isArray(field.options) && field.options.length === 1;
  const namedDecision = ["state.legend", "battle.enter_protector", "state.battle_protector"].includes(decision.kind);
  const source = typeof context.source === "string" ? context.source
    : namedDecision && typeof decision.ctx.name === "string" ? decision.ctx.name : "";
  const title = checkingLandEntry && land ? `How should ${cardName(land)} enter?`
    : isCombat ? isAttack ? "Declare attackers" : "Select blockers" : String(context.title ?? decisionHeading(decision));
  const detail = isCombat
    ? isAttack ? "Choose a creature, then its defender." : `Defending seat ${decision.actor}. Assign each blocker to an incoming attacker.`
    : checkingLandEntry ? "" : decision.kind === "trigger.order"
      ? "Your top trigger resolves first within your group. Drag or use the arrows to reorder. Players add their groups in turn order, starting with the active player."
      : String(context.detail ?? decisionDetail(decision));
  function commit(next: ChoiceValues) {
    if (minimized) return;
    const problems = validateChoices(form, next);
    setErrors(problems);
    if (!disabled && problems.length === 0) {
      if (remember && preferenceKey) sessionStorage.setItem(preferenceKey, JSON.stringify(next[String(field.name)]));
      onSubmit(action, executableChoices(form, next));
    }
  }
  useEffect(() => {
    if (disabled || minimized || automated.current || (!optionalPayment && !optionalDraw)) return;
    const saved = preferenceKey ? sessionStorage.getItem(preferenceKey) : null;
    const answer = optionalValues.length === 1 ? optionalValues[0]
      : optionalValues.find((value) => JSON.stringify(value) === saved);
    if (answer === undefined) return;
    const next = { [String(field.name)]: answer };
    if (validateChoices(form, next).length) return;
    automated.current = true;
    onSubmit(action, executableChoices(form, next));
  }, [action, disabled, field, form, minimized, onSubmit, optionalDraw, optionalPayment, optionalValues, preferenceKey]);
  const save = (next: ChoiceValues) => { setValues(next); setErrors([]); };
  const option = singleScry && Array.isArray(field.options) ? asRecord(field.options[0]) : {};
  const scryRef = String(option.value ?? "");
  const scryCard = singleScry ? projectedChoiceCards(view).get(scryRef) : undefined;
  const scryGroups = singleScry ? orderedPartitionNames(field) : [];
  const isSearchWithoutSelection = decision.kind === "semantic.search"
    && Number(field?.minimum) === 0 && Array.isArray(values.search_cards) && values.search_cards.length === 0;
  function submitCurrentChoice() {
    if (isFetch && landEntryLife > 0) {
      setReviewLandEntry(true);
      return;
    }
    commit(values);
  }

  return <form className={`contextual-decision${isCombat ? " combat-decision" : ""}${binary || isOptional ? " compact-decision" : ""}${checkingLandEntry && !minimized ? " land-entry-decision" : ""}${decision.kind === "trigger.order" ? " trigger-order-decision" : ""}`}
    data-testid="contextual-decision" aria-label={title}
    onSubmit={(event) => { event.preventDefault(); if (!checkingLandEntry) submitCurrentChoice(); }}>
    <header>
      <div className="decision-context">
        <SeatLabel seat={decision.actor} />
        {source && source !== title && <span>{source}</span>}
      </div>
      <h2><ManaText text={title} /></h2>
      {detail && detail !== title && <p><ManaText text={detail} /></p>}
    </header>
    {!minimized && singleScry && <div className="scry-preview">
      {scryCard ? <CardTile value={scryCard} view={view} compact onInspect={onInspect} /> : <strong>{String(option.label ?? labelFor(scryRef))}</strong>}
    </div>}
    {!minimized && checkingLandEntry && land && <div className="land-entry-card">
      <CardTile value={land} view={view} compact showPrintedCost={false} />
    </div>}
    {!minimized && !checkingLandEntry && !isOptional && !singleScry && !binary && <div className="contextual-fields">
      <ChoiceFormView form={displayedForm} values={values} onChange={save} labelFor={labelFor} view={view} onInspect={onInspect} />
    </div>}
    {displayedErrors.length > 0 && <p className="choice-warning" role="alert">{displayedErrors.join(" ")}</p>}
    <footer>
      {!minimized && binary && <>
        <div className="binary-choice" role="radiogroup" aria-label={String(field.label ?? field.name)}>
          {radioOptions.map((value) => <label key={String(value)}>
            <input type="radio" name={String(field.name)} checked={values[String(field.name)] === value} disabled={disabled}
              onChange={() => save({ ...values, [String(field.name)]: value })} />
            {field.name === "pay" ? value ? "Pay" : "Decline" : value ? "Yes" : "No"}
          </label>)}
        </div>
        <button type="submit" data-testid="submit-choice" disabled={commitDisabled || typeof values[String(field.name)] !== "boolean"}>
          {submitting ? "Submitting…" : "Confirm"}
        </button>
      </>}
      {!minimized && chooseOptions.map((option) =>
        <button key={String(option.value)} data-testid={`decision-${option.value}`} type="button"
          className={option.value === "decline" ? "secondary-button" : ""}
          disabled={commitDisabled} onClick={() => commit({ choice: option.value as JsonValue })}>
          {option.value === "decline" ? "Decline" : String(context.accept ?? (operation === "offer_draw" ? "Draw" : "Apply"))}
        </button>)}
      {!minimized && scryGroups.map((group, index) =>
        <button key={group} type="button" disabled={commitDisabled}
          onClick={() => commit({ [String(field.name)]: Object.fromEntries(scryGroups.map((name) =>
            [name, name === group ? [scryRef] : []])) })}>
          {index === 0 ? "Top" : "Bottom"}
        </button>)}
      {!minimized && checkingLandEntry && <>
        <button type="button" disabled={commitDisabled || Number(payer.life) < landEntryLife}
          onClick={() => commit({ ...values, entry_pay_life: true })}>Pay {landEntryLife} life · Enter untapped</button>
        <button type="button" className="secondary-button" disabled={commitDisabled}
          onClick={() => commit({ ...values, entry_pay_life: false })}>Enter tapped</button>
        <button type="button" className="link-button" disabled={submitting} onClick={() => setReviewLandEntry(false)}>Choose another land</button>
      </>}
      {!minimized && !checkingLandEntry && !isOptional && !singleScry && !binary && <button type="submit" data-testid="submit-choice" disabled={commitDisabled}>
        {submitting ? "Submitting…" : isCombat ? (isAttack ? "Confirm attackers" : Object.keys(asRecord(values.blocks)).length ? "Confirm blockers" : "Confirm no blockers")
          : isFetch && !values.search_card ? "Find no land"
            : isSearchWithoutSelection ? "Find no card"
              : decision.kind === "trigger.order" ? "Confirm order" : String(context.accept ?? form.submit_label ?? "Confirm selection")}
      </button>}
      {!isCombat && <button type="button" className="secondary-button minimize-decision" aria-expanded={!minimized}
        disabled={submitting} onClick={() => setMinimized(!minimized)}>
        {minimized ? "Show choice" : "Minimize choice"}
      </button>}
      {submitting && (singleScry || isOptional) && <small role="status">Submitting…</small>}
      {!minimized && preferenceKey && optionalValues.length > 1 && <label className="remember-choice">
        <input type="checkbox" checked={remember} disabled={submitting} onChange={(event) => setRemember(event.target.checked)} />
        Remember for this trigger this game
      </label>}
    </footer>
  </form>;
}
