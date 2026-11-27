import type { PublicGameEvent } from "./api";
import type { Decision, JsonValue, LegalAction } from "./generated/protocol";

const actionNames: Record<string, string> = {
  activate: "Activate ability", attack: "Declare attackers", block: "Declare blockers",
  bottom: "Put cards on the bottom", cast: "Cast spell", choose: "Make a choice",
  concede: "Concede game", discard: "Discard cards", keep: "Keep this hand",
  mulligan: "Take a mulligan", order: "Confirm order", pass: "Pass priority",
  play_land: "Play land", undo_mana: "Undo mana activation",
};

const manaColors: Record<string, string> = {
  W: "white", U: "blue", B: "black", R: "red", G: "green", C: "colorless",
};

type CardTransferVerb = "drew" | "milled";

function publicCardTransfer(
  event: Pick<PublicGameEvent, "code" | "summary">,
): { seat: string; verb: CardTransferVerb; count: number } | undefined {
  const verb = event.code === "card.draw" ? "drew" : event.code === "card.mill" ? "milled" : undefined;
  if (!verb) return undefined;
  const match = /^([ABCD]) (drew|milled) (\d+) card\(s\)\.$/.exec(event.summary);
  if (match?.[2] !== verb) return undefined;
  return { seat: match[1], verb, count: Number(match[3]) };
}

function cardTransferText(verb: CardTransferVerb, count: number): string {
  return `${verb} ${count} ${count === 1 ? "card" : "cards"}`;
}

export function summarizeTableActivity(events: readonly PublicGameEvent[], ownSeat: string) {
  const ownCounts = new Map<CardTransferVerb, number>();
  let latestPublic: PublicGameEvent | undefined;
  for (const event of events) {
    if (event.code !== "card.draw" && event.code !== "card.mill" && event.code !== "stack.cast") continue;
    const transfer = publicCardTransfer(event);
    if (transfer?.seat === ownSeat && transfer.count > 0) {
      ownCounts.set(transfer.verb, (ownCounts.get(transfer.verb) ?? 0) + transfer.count);
    } else {
      latestPublic = event;
    }
  }
  const transfers = [...ownCounts].map(([verb, count]) => cardTransferText(verb, count));
  return {
    ownText: transfers.length ? `You ${transfers.join(" and ")}.` : "",
    drew: ownCounts.has("drew"),
    milled: ownCounts.has("milled"),
    latestPublic,
  };
}

export function publicEventSummary(
  event: Pick<PublicGameEvent, "code" | "summary">,
  cardLabel?: (ref: string) => string | undefined,
): string {
  if (event.code === "stack.cast" && event.summary) return event.summary.replace(/^[ABCD] cast S\d+ /, "Cast ");
  const transfer = publicCardTransfer(event);
  if (transfer) return `Seat ${transfer.seat} ${cardTransferText(transfer.verb, transfer.count)}.`;
  if (event.code === "land.play" && event.summary) return event.summary.replace(/^[ABCD] played [ABCD]\d+ /, "Played ");
  if (event.code === "mana.ability") {
    const activation = /^[ABCD] activated (\S+) \S+ for mana\.$/.exec(event.summary);
    if (activation) {
      const source = cardLabel?.(activation[1]);
      return source ? `Activated ${source} for mana.` : "Activated a mana ability.";
    }
  }
  if (event.code === "mana.produce") {
    const tap = /^[ABCD] tapped (\S+) for \{(.*)\}\.$/.exec(event.summary);
    if (tap) {
      const [, ref, pool] = tap;
      const source = cardLabel?.(ref) || "a mana source";
      const produced = [...pool.matchAll(/'([WUBRGC])': (\d+)/g)].map(([, color, amount]) => `${amount} ${manaColors[color]}`);
      return `Tapped ${source} for ${produced.length ? produced.join(" and ") + " " : ""}mana.`;
    }
  }
  if (event.code === "mana.undo") {
    const undo = /^[ABCD] undid (\S+)'s mana activation\.$/.exec(event.summary);
    if (undo) {
      const source = cardLabel?.(undo[1]);
      return source ? `Undid mana from ${source}.` : "Undid a mana activation.";
    }
  }
  const legacyCopy = /^\S+ created (\d+) (storm|gravestorm) copy\/copies\.$/.exec(event.summary);
  if (legacyCopy && event.code === `stack.${legacyCopy[2]}.copy`) {
    const [, count, ability] = legacyCopy;
    return `Created ${count} ${count === "1" ? "copy" : "copies"} with ${ability}.`;
  }
  return event.summary || "Game event recorded.";
}

export function modeLabel(mode: JsonValue, index: number): string {
  if (typeof mode !== "string" || !mode.trim() || /^[a-f\d-]{20,}$/i.test(mode)) return `Mode ${index + 1}`;
  const text = mode.replace(/[_-]/g, " ").replace(/^mode(\d+)/i, "mode $1").trim();
  return text ? text[0].toUpperCase() + text.slice(1) : `Mode ${index + 1}`;
}

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
    case "battle.enter_protector":
    case "state.battle_protector": return "Choose a battle protector";
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
    case "battle.enter_protector":
    case "state.battle_protector":
      return typeof decision.ctx.instruction === "string" && decision.ctx.instruction.trim()
        ? decision.ctx.instruction : "Choose an eligible player to protect this battle.";
    case "trigger.order": {
      const triggers = Array.isArray(decision.ctx.triggers) ? decision.ctx.triggers.length : 0;
      const direction = typeof decision.ctx.instruction === "string" ? decision.ctx.instruction.trim() : "";
      const instruction = direction || "Choose the order in which the abilities go on the stack.";
      return triggers ? `${triggers} triggered ${triggers === 1 ? "ability" : "abilities"}. ${instruction}` : instruction;
    }
    default: return "";
  }
}

export function isMandatorySelection(kind: string): boolean {
  return ["mulligan.bottom", "cleanup.discard", "state.legend", "trigger.order", "combat.damage"].includes(kind);
}

export function canMinimizeChoice(decision: Decision | null | undefined): boolean {
  return Boolean(decision && (isMandatorySelection(decision.kind)
    || ["scry", "surveil"].includes(String(decision.ctx.operation ?? ""))));
}

function manaRequirementSymbols(requirements: JsonValue | undefined, snow = 0): string {
  const mana = (requirements ?? {}) as Record<string, JsonValue>;
  const generic = Number(mana.GENERIC ?? 0);
  return (generic > 0 ? `{${generic}}` : "")
    + [..."WUBRGC"].map((color) => `{${color}}`.repeat(Number(mana[color] ?? 0))).join("")
    + "{S}".repeat(snow);
}

export function phyrexianCastCostLabels(action: LegalAction): Record<string, string> {
  const options = action.cost_options;
  if (action.action !== "cast" || !Array.isArray(options)) return {};
  return Object.fromEntries(options.flatMap((raw) => {
    const option = raw as Record<string, JsonValue>;
    if (typeof option.id !== "string" || !option.id.startsWith("printed-phyrexian-")) return [];
    const mana = manaRequirementSymbols(option.requirements, Number(option.snow ?? 0));
    const life = Number(option.additional_life ?? 0);
    const parts = [mana, life > 0 ? `${life} life` : ""].filter(Boolean);
    const variable = (option.choice_schema as Record<string, JsonValue> | undefined)?.x;
    return [[option.id, `${variable ? "Choose X · " : ""}Pay ${parts.join(" and ") || "no mana"}`]];
  }));
}

export function activationCostLabel(action: LegalAction): string {
  const price = (action.cost_summary ?? {}) as Record<string, JsonValue>;
  const loyalty = typeof price.loyalty === "number" ? price.loyalty : null;
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
    loyalty === null ? "" : `${loyalty > 0 ? "+" : loyalty < 0 ? "−" : ""}${Math.abs(loyalty)} loyalty`,
    price.random_discard ? `Discard ${price.random_discard} ${price.random_discard === 1 ? "card" : "cards"} at random` : "",
    selectedCost?.label ? String(selectedCost.label) : "",
  ].filter(Boolean).join(" · ");
}

export function needsActivationConfirmation(action: LegalAction): boolean {
  if (action.action !== "activate" || action.form) return false;
  const price = action.cost_summary as Record<string, JsonValue> | undefined;
  return Boolean(price?.sac_self || price?.discard_self || price?.exile_self
    || price?.life || price?.energy || price?.random_discard);
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
  personalTurn?: number;
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
    return input.firstStrikeStep && input.combatDamageStep === 1 ? "First-Strike Damage" : "Combat Damage";
  }
  return labels[key] ?? "Game setup";
}

export function presentTurn(input: TurnPresentationInput): TurnPresentation {
  const terminal = ["complete", "aborted"].includes(input.lifecycleStatus);
  const opening = !terminal && input.turnSequence === 0;
  const current = macroPhase(input.phase, input.step);
  const currentIndex = opening ? -1 : MACRO_PHASES.findIndex((entry) => entry.id === current);
  const turnNumber = input.personalTurn ?? 0;
  const lastTwo = turnNumber % 100;
  const suffix = lastTwo >= 11 && lastTwo <= 13 ? "th" : ({ 1: "st", 2: "nd", 3: "rd" } as Record<number, string>)[turnNumber % 10] ?? "th";
  const liveHeadline = input.activeSeat && turnNumber > 0
    ? `Seat ${input.activeSeat}'s ${turnNumber}${suffix} turn`
    : `${input.activeSeat ? `Seat ${input.activeSeat}'s Turn · ` : ""}Turn ${input.turnSequence}`;
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
