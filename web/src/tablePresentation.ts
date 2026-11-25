import type { Decision, JsonValue, LegalAction } from "./generated/protocol";

const actionNames: Record<string, string> = {
  activate: "Activate ability", attack: "Declare attackers", block: "Declare blockers",
  bottom: "Put cards on the bottom", cast: "Cast spell", choose: "Make a choice",
  concede: "Concede game", discard: "Discard cards", keep: "Keep this hand",
  mulligan: "Take a mulligan", order: "Confirm order", pass: "Pass priority",
  play_land: "Play land", undo_mana: "Undo mana activation",
};

export function actionTitle(action: LegalAction): string {
  const label = action.label?.trim();
  const printedPrice = action.action === "cast" && typeof action.cost === "string" && action.cost
    ? ` — ${action.cost}` : "";
  if (label && printedPrice && label.endsWith(printedPrice)) return label.slice(0, -printedPrice.length);
  return label && ![action.id, action.kind, action.action].includes(label)
    ? label : actionNames[action.action] ?? "Make a choice";
}

export function decisionHeading(decision: Pick<Decision, "kind" | "ctx">): string {
  if (decision.kind === "priority") return "Your priority";
  if (typeof decision.ctx.prompt === "string" && decision.ctx.prompt.trim()) return decision.ctx.prompt;
  switch (decision.kind) {
    case "mulligan":
    case "mulligan.declare": return "Keep this hand?";
    case "mulligan.bottom": return "Choose cards to put on the bottom";
    case "cleanup.discard": return "Discard to your hand size";
    case "state.legend": return "Choose the permanent to keep";
    case "trigger.order": return "Order your triggers";
    case "combat.damage": return "Assign combat damage";
    case "semantic.target": return "Choose targets";
    default: return "Make a choice";
  }
}

export function decisionDetail(decision: Pick<Decision, "kind" | "ctx">): string {
  const count = typeof decision.ctx.count === "number" ? decision.ctx.count : 0;
  switch (decision.kind) {
    case "mulligan.declare": {
      const redraw = decision.ctx.if_mulligan;
      if (!redraw || typeof redraw !== "object" || Array.isArray(redraw) || typeof redraw.draw !== "number") return "";
      return typeof redraw.bottom === "number" && redraw.bottom > 0
        ? `A mulligan draws ${redraw.draw} new cards. Put ${redraw.bottom} on the bottom afterward.`
        : `A mulligan draws ${redraw.draw} new cards. This redraw is free.`;
    }
    case "mulligan.bottom": return `Put ${count} ${count === 1 ? "card" : "cards"} from your hand on the bottom of your library.`;
    case "cleanup.discard": return `Discard ${count} ${count === 1 ? "card" : "cards"} to reach your maximum hand size.`;
    case "state.legend": return "Keep one of the matching legendary permanents. Put the others into their owners’ graveyards.";
    case "trigger.order": return "Choose the order in which to put these triggered abilities on the stack.";
    default: return "";
  }
}

export function canMinimizeChoice(decision: Decision | null | undefined): boolean {
  return Boolean(decision && (["mulligan.bottom", "cleanup.discard", "state.legend", "trigger.order"].includes(decision.kind)
    || ["scry", "surveil"].includes(String(decision.ctx.operation ?? ""))));
}

function manaRequirementSymbols(requirements: JsonValue | undefined, snow = 0): string {
  const mana = (requirements ?? {}) as Record<string, JsonValue>;
  const generic = Number(mana.GENERIC ?? 0);
  return (generic > 0 ? `{${generic}}` : "")
    + [..."WUBRGC"].map((color) => `{${color}}`.repeat(Number(mana[color] ?? 0))).join("")
    + "{S}".repeat(snow);
}

export function activationCostLabel(action: LegalAction): string {
  const price = (action.cost_summary ?? {}) as Record<string, JsonValue>;
  const symbols = manaRequirementSymbols(price.m);
  const selectedCost = action.form?.fields.find((field) => field.name === "cost_cards");
  const options = action.form?.variants?.options;
  const choices = Array.isArray(options) ? options.map((option) =>
    String((option as Record<string, JsonValue>).label ?? "")) : [];
  return [
    symbols || choices.join(" / "),
    price.tap ? "Tap this card" : "",
    price.sac_self ? "Sacrifice this card" : "",
    price.discard_self ? "Discard this card" : "",
    price.exile_self ? "Exile this card" : "",
    price.life ? `Pay ${price.life} life` : "",
    price.energy ? `Pay ${price.energy} energy` : "",
    selectedCost?.label ? String(selectedCost.label) : "",
  ].filter(Boolean).join(" · ");
}

export function cardActionCost(actions: readonly LegalAction[]): string {
  if (actions.length > 1) return "Choose an action";
  const action = actions[0];
  if (!action) return "";
  if (Array.isArray(action.cost_options) && action.cost_options.length > 1) return "Choose a cost";
  if (action.action === "activate") return activationCostLabel(action);
  if (action.action !== "cast" || !Array.isArray(action.cost_options)) return "";
  const cost = action.cost_options[0] as Record<string, JsonValue> | undefined;
  if (!cost?.requirements) return "";
  if (cost.target_costs) return "Cost depends on targets";
  const options = action.form?.variants?.options;
  const variant = Array.isArray(options) ? (options as Record<string, JsonValue>[]).find((row) => row.value === cost.id) : undefined;
  const variantFields = Array.isArray(variant?.fields) ? variant.fields as Record<string, JsonValue>[] : [];
  const fields = [...(action.form?.fields ?? []), ...variantFields];
  const mana = (fields.some((field) => field.name === "x") ? "{X}" : "")
    + manaRequirementSymbols(cost.requirements, Number(cost.snow ?? 0));
  return [
    mana || "{0}",
    cost.additional_life ? `Pay ${cost.additional_life} life` : "",
    ...fields.filter((field) => field.name === "cost_cards" && field.required && typeof field.label === "string")
      .map((field) => String(field.label)),
  ].filter(Boolean).join(" · ");
}

export function isRoutineCast(action: LegalAction): boolean {
  if (action.action !== "cast" || !action.form || action.form.fields.length > 0) return false;
  const variants = action.form.variants?.options;
  if (!Array.isArray(variants) || variants.length !== 1) return false;
  const variant = variants[0] as Record<string, JsonValue>;
  const costs = action.cost_options;
  if (!Array.isArray(costs) || costs.length !== 1) return false;
  const cost = costs[0] as Record<string, JsonValue>;
  return variant.value === "normal" && Array.isArray(variant.fields) && variant.fields.length === 0
    && !cost.additional_life && !cost.snow;
}

export function choiceSubmitLabel(
  action: LegalAction, manualMana: boolean, submitting: boolean, allocation: JsonValue | undefined,
): string {
  if (submitting) return "Submitting…";
  if (action.action === "activate") return "Pay cost and activate";
  if (action.action !== "cast") return action.form?.submit_label ?? "Submit";
  const selectedPool = allocation && typeof allocation === "object" && !Array.isArray(allocation)
    && Object.keys(allocation).length > 0;
  const label = actionTitle(action);
  return manualMana || selectedPool ? label : `Auto-mana & ${label}`;
}

export const MACRO_PHASES = [
  { id: "untap", label: "Untap" },
  { id: "upkeep", label: "Upkeep" },
  { id: "draw", label: "Draw" },
  { id: "main1", label: "Main 1" },
  { id: "combat", label: "Combat" },
  { id: "main2", label: "Main 2" },
  { id: "end", label: "End" },
] as const;

export type MacroPhase = typeof MACRO_PHASES[number]["id"];

export type TurnPresentationInput = {
  turnSequence: number;
  activeSeat: string;
  prioritySeat: string;
  phase: string;
  step: string;
  combatDamageStep: number;
  firstStrikeStep: boolean;
  lifecycleStatus: string;
};

export type PhaseRailEntry = {
  id: MacroPhase;
  label: string;
  state: "past" | "current" | "future";
};

export type TurnPresentation = {
  terminal: boolean;
  headline: string;
  priority: string;
  exactStep: string;
  macroPhase: MacroPhase;
  rail: PhaseRailEntry[];
};

function macroPhase(phase: string, step: string): MacroPhase {
  if (phase === "beginning") {
    if (step === "untap") return "untap";
    if (step === "upkeep") return "upkeep";
    return "draw";
  }
  if (phase === "precombat_main") return "main1";
  if (phase === "combat") return "combat";
  if (phase === "postcombat_main") return "main2";
  return "end";
}

function exactStepLabel(input: TurnPresentationInput): string {
  const key = `${input.phase}/${input.step}`;
  const labels: Record<string, string> = {
    "beginning/untap": "Untap",
    "beginning/upkeep": "Upkeep",
    "beginning/draw": "Draw",
    "precombat_main/main": "Main Phase 1",
    "combat/beginning_combat": "Beginning of Combat",
    "combat/declare_attackers": "Declare Attackers",
    "combat/declare_blockers": "Declare Blockers",
    "combat/end_combat": "End of Combat",
    "postcombat_main/main": "Main Phase 2",
    "ending/end_step": "End Step",
    "ending/cleanup": "Cleanup",
  };
  if (key === "combat/combat_damage") {
    return input.firstStrikeStep ? "First-Strike Damage" : "Combat Damage";
  }
  return labels[key] ?? "Game setup";
}

export function presentTurn(input: TurnPresentationInput): TurnPresentation {
  const terminal = ["complete", "aborted"].includes(input.lifecycleStatus);
  const opening = !terminal && input.turnSequence === 0;
  const current = macroPhase(input.phase, input.step);
  const currentIndex = opening ? -1 : MACRO_PHASES.findIndex((entry) => entry.id === current);
  const liveHeadline = `${input.activeSeat ? `Seat ${input.activeSeat}'s Turn · ` : ""}Turn ${input.turnSequence}`;
  const livePriority = input.prioritySeat ? `Priority: Seat ${input.prioritySeat}` : "Resolving game actions";
  const finalHeadline = input.lifecycleStatus === "aborted" ? "Game ended" : "Game complete";
  return {
    terminal,
    headline: terminal ? finalHeadline : opening ? "Choosing opening hands" : liveHeadline,
    priority: terminal ? "No live priority" : opening ? "Before the first turn" : livePriority,
    exactStep: terminal ? "Final result" : opening ? "" : exactStepLabel(input),
    macroPhase: current,
    rail: MACRO_PHASES.map((entry, index) => ({
      ...entry,
      state: index === currentIndex
        ? "current"
        : index < currentIndex
          ? "past"
          : "future",
    })),
  };
}

export type ActionWindow = {
  activeSeat: string;
  ownSeat: string;
  phase: string;
  stackDepth: number;
};

export function visibleActionLabel(
  action: LegalAction,
  window: ActionWindow,
): string {
  if (action.action === "pass" && window.stackDepth > 0) return "Resolve";
  if (
    action.action === "pass"
    && window.activeSeat === window.ownSeat
    && window.stackDepth === 0
  ) {
    if (window.phase === "precombat_main") return "Continue to combat";
    if (window.phase === "postcombat_main") return "End turn";
  }
  return actionTitle(action);
}
