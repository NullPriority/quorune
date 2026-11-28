import { CardTile, asList, asRecord } from "./CardSurface";
import { projectedChoiceCards } from "./CardChoiceGrid";
import { activeFields, choiceCompatibilityErrors, executableChoices, initialChoices, validateChoices, type ChoiceValues } from "./choices";
import type { JsonValue, LegalAction } from "./generated/protocol";
import type { ProjectedView } from "./protocol";

export function immediateExileCastChoices(action: LegalAction): ChoiceValues | null {
  if (!action.form) return {};
  const choices = initialChoices(action.form);
  if (activeFields(action.form, choices).length || asList(asRecord(action.form.variants).options).length > 1
    || choiceCompatibilityErrors(action.form, choices).length || validateChoices(action.form, choices).length) return null;
  return executableChoices(action.form, choices);
}

export function ExileCastChoice({ view, disabled, onAnswer, onInspect }: {
  view: ProjectedView;
  disabled: boolean;
  onAnswer: (action: LegalAction, choices: ChoiceValues | null) => void;
  onInspect: (card: JsonValue) => void;
}) {
  const decision = view.decision;
  if (decision?.kind !== "selection.exile_cast") return null;
  const cast = decision.legal_actions.find((action) => action.action === "choose" && action.id === "cast");
  const decline = decision.legal_actions.find((action) => action.action === "choose" && action.id === "decline");
  if (!cast || !decline) return null;
  const card = projectedChoiceCards(view).get(String(decision.ctx.card ?? ""));
  return <div className="exile-cast-choice" data-testid="exile-cast-choice">
    {card !== undefined && <CardTile value={card} view={view} compact onInspect={onInspect} />}
    <div className="action-row">{[cast, decline].map((action) => {
      const immediate = immediateExileCastChoices(action);
      return <button key={action.id} type="button" className={action === decline ? "secondary-button" : ""}
        data-testid={`action-${action.id}`} disabled={disabled} onClick={() => onAnswer(action, immediate)}>
        {action === decline ? "No, decline" : immediate ? "Yes, cast" : "Yes, choose how to cast"}
      </button>;
    })}</div>
  </div>;
}
