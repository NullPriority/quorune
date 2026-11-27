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

export function findSafeAutoPass<T extends AutoPassAction>(
  actions: readonly T[],
  window: AutoPassWindow = {},
): T | null {
  const activeEmptyMain = window.principalSeat !== undefined
    && window.principalSeat === window.activePlayer
    && (window.stackDepth ?? 0) === 0
    && ["precombat_main", "postcombat_main"].includes(window.phase ?? "");
  if (activeEmptyMain && !window.allowActiveMain) return null;
  const pass = actions.find((action) => action.action === "pass") ?? null;
  if (!pass) return null;
  const hasMeaningfulAction = actions.some(
    (action) => !["pass", "concede"].includes(action.action)
      && !isIgnoredActivation(action, window.ignoredActivations)
      && !(action.mana_ability === true && !action.form
        && (!action.cost_summary || Object.keys(action.cost_summary).every((key) => key === "tap"))),
  );
  return hasMeaningfulAction ? null : pass;
}

export type TurnYieldBoundary = {
  turn: number;
  phase: string;
  stack: string[];
  graveyard?: string;
};

export const COMBAT_PRIORITY_STOPS = [
  { id: "upkeep", label: "Upkeep" },
  { id: "draw", label: "Draw step" },
  { id: "precombat_main", label: "First main phase" },
  { id: "combat", label: "Every combat step" },
  { id: "beginning_combat", label: "Beginning of combat" },
  { id: "declare_attackers", label: "After attackers are declared" },
  { id: "declare_blockers", label: "After blockers are declared" },
  { id: "first_strike_damage", label: "After first-strike damage" },
  { id: "combat_damage", label: "After combat damage" },
  { id: "end_combat", label: "End of combat" },
  { id: "postcombat_main", label: "Second main phase" },
  { id: "end_step", label: "End step" },
] as const;

export type CombatPriorityStop = typeof COMBAT_PRIORITY_STOPS[number]["id"];

export function activeCombatPriorityStop(
  phase: string, step: string, firstStrike: boolean, selected: readonly CombatPriorityStop[],
): string | undefined {
  if (selected.length === 0) return undefined;
  const currentStep = step === "combat_damage" && firstStrike ? "first_strike_damage" : step;
  const allCombat = phase === "combat" && selected.includes("combat");
  const option = COMBAT_PRIORITY_STOPS.find((stop) => stop.id === currentStep)
    ?? (phase === "combat" ? undefined : COMBAT_PRIORITY_STOPS.find((stop) => stop.id === phase));
  if (option && (selected.includes(option.id) || allCombat)) return option.label;
  return allCombat ? "Combat" : undefined;
}

export function turnYieldPause(
  armed: TurnYieldBoundary,
  window: TurnYieldBoundary,
  decisionKind: string | undefined,
  canPass: boolean,
  holds = { stack: true, graveyard: false },
): string | undefined {
  if (window.turn !== armed.turn) return "Turn ended";
  if (decisionKind && decisionKind !== "priority") return "A decision is required";
  if (decisionKind !== "priority") return undefined;
  if (holds.stack && window.stack.some((ref) => !armed.stack.includes(ref))) return "New object on the stack";
  if (holds.graveyard && window.graveyard !== armed.graveyard) return "Graveyard activity";
  if (!canPass) return "Priority is held";
  return undefined;
}
