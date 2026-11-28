type AutoPassAction = {
  action: string;
  id?: string;
  mana_ability?: boolean;
  form?: unknown;
  cost_summary?: unknown;
  expiry_revision?: number;
  proposal_fingerprint?: string;
};

export type AutoPassWindow = {
  activePlayer?: string;
  phase?: string;
  principalSeat?: string;
  stackDepth?: number;
  allowActiveMain?: boolean;
  passWithOptionalActivations?: boolean;
  ignoredActivations?: ReadonlyMap<string, string>;
};

export function activationStopSignature(action: AutoPassAction): string {
  // The server renews these fields even when the offered activation is unchanged.
  const offer = { ...action };
  delete offer.expiry_revision;
  delete offer.proposal_fingerprint;
  return JSON.stringify(offer);
}

export function isIgnoredActivation(action: AutoPassAction, offers: ReadonlyMap<string, string> | undefined): boolean {
  return action.action === "activate" && action.mana_ability !== true && action.id !== undefined
    && offers?.get(action.id) === activationStopSignature(action);
}

export function skipsOptionalActivation(action: AutoPassAction, window: AutoPassWindow): boolean {
  return action.action === "activate" && action.mana_ability !== true
    && (window.passWithOptionalActivations === true || isIgnoredActivation(action, window.ignoredActivations));
}

export function findSafeAutoPass<T extends AutoPassAction>(
  actions: readonly T[],
  window: AutoPassWindow = {},
): T | null {
  const activeEmptyMain = window.principalSeat !== undefined
    && window.principalSeat === window.activePlayer
    && (window.stackDepth ?? 0) === 0
    && ["precombat_main", "postcombat_main"].includes(window.phase ?? "");
  const pass = actions.find((action) => action.action === "pass") ?? null;
  if (!pass) return null;
  if (activeEmptyMain && !window.allowActiveMain && !actions.some((action) => skipsOptionalActivation(action, window))) {
    return null;
  }
  const hasMeaningfulAction = actions.some(
    (action) => !["pass", "concede"].includes(action.action)
      && !skipsOptionalActivation(action, window)
      && !(action.mana_ability === true && !action.form
        && (!action.cost_summary || Object.keys(action.cost_summary).every((key) => key === "tap"))),
  );
  return hasMeaningfulAction ? null : pass;
}

export type PriorityActivity = {
  stack: readonly string[];
  graveyard: string;
  graveyardStack: readonly string[];
};

export const PRIORITY_STOPS = [
  { id: "upkeep", phase: "upkeep", label: "Upkeep" },
  { id: "draw", phase: "draw", label: "Draw step" },
  { id: "precombat_main", phase: "main1", label: "First main phase" },
  { id: "combat", phase: "combat", label: "Every combat step" },
  { id: "beginning_combat", phase: "combat", label: "Beginning of combat" },
  { id: "declare_attackers", phase: "combat", label: "After attackers are declared" },
  { id: "declare_blockers", phase: "combat", label: "After blockers are declared" },
  { id: "first_strike_damage", phase: "combat", label: "After first-strike damage" },
  { id: "combat_damage", phase: "combat", label: "After combat damage" },
  { id: "end_combat", phase: "combat", label: "End of combat" },
  { id: "postcombat_main", phase: "main2", label: "Second main phase" },
  { id: "end_step", phase: "end", label: "End step" },
  { id: "before_my_turn", phase: "end", label: "End step before my turn" },
  { id: "cleanup", phase: "end", label: "Cleanup priority" },
] as const;

export type PriorityStop = typeof PRIORITY_STOPS[number]["id"];
export type EmptyPriorityHolds = { steps: PriorityStop[]; preserve_empty: boolean };

export function selectedPriorityStop(
  phase: string, step: string, firstStrike: boolean, damageStep: number, selected: readonly PriorityStop[],
  nextIsMine = false, hasAttackers = true,
): string | undefined {
  if (selected.length === 0) return undefined;
  if (step === "end_step" && nextIsMine && selected.includes("before_my_turn")) return "End step before your turn";
  const currentStep = step === "combat_damage" && firstStrike && damageStep === 1 ? "first_strike_damage" : step;
  const allCombat = phase === "combat" && selected.includes("combat");
  const option = PRIORITY_STOPS.find((stop) => stop.id === currentStep)
    ?? (phase === "combat" ? undefined : PRIORITY_STOPS.find((stop) => stop.id === phase));
  if (option && (selected.includes(option.id) || allCombat)
    && (option.id !== "declare_attackers" || hasAttackers || allCombat)) return option.label;
  return allCombat ? "Combat" : undefined;
}

export function priorityActivityHold(
  acknowledged: PriorityActivity,
  current: PriorityActivity,
  holds: { stack: boolean; graveyard: boolean },
): string | undefined {
  if (holds.graveyard && (current.graveyard !== acknowledged.graveyard
    || current.graveyardStack.some((ref) => !acknowledged.graveyardStack.includes(ref)))) return "Graveyard activity";
  if (holds.stack && current.stack.some((ref) => !acknowledged.stack.includes(ref))) return "New object on the stack";
  return undefined;
}
