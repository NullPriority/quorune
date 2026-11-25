type AutoPassAction = {
  action: string;
  id?: string;
  mana_ability?: boolean;
  form?: unknown;
  cost_summary?: unknown;
};

export type AutoPassWindow = {
  activePlayer?: string;
  phase?: string;
  principalSeat?: string;
  stackDepth?: number;
  allowActiveMain?: boolean;
  ignoredActivations?: ReadonlyMap<string, string>;
};

export function isIgnoredActivation(action: AutoPassAction, offers: ReadonlyMap<string, string> | undefined): boolean {
  return action.action === "activate" && action.mana_ability !== true && action.id !== undefined
    && offers?.get(action.id) === JSON.stringify(action);
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
};

export const COMBAT_PRIORITY_STOPS = [
  { id: "combat", label: "Every combat step" },
  { id: "beginning_combat", label: "Beginning of combat" },
  { id: "declare_attackers", label: "After attackers are declared" },
  { id: "declare_blockers", label: "After blockers are declared" },
  { id: "first_strike_damage", label: "After first-strike damage" },
  { id: "combat_damage", label: "After combat damage" },
  { id: "end_combat", label: "End of combat" },
] as const;

export type CombatPriorityStop = typeof COMBAT_PRIORITY_STOPS[number]["id"];

export function activeCombatPriorityStop(
  phase: string, step: string, firstStrike: boolean, selected: readonly CombatPriorityStop[],
): string | undefined {
  if (phase !== "combat" || selected.length === 0) return undefined;
  const currentStep = step === "combat_damage" && firstStrike ? "first_strike_damage" : step;
  const option = COMBAT_PRIORITY_STOPS.find((stop) => stop.id === currentStep);
  if (option && (selected.includes(option.id) || selected.includes("combat"))) return option.label;
  return selected.includes("combat") ? "Combat" : undefined;
}

export function turnYieldPause(
  armed: TurnYieldBoundary,
  window: TurnYieldBoundary,
  decisionKind: string | undefined,
  canPass: boolean,
): string | undefined {
  if (window.turn !== armed.turn) return "Turn ended";
  if (decisionKind && decisionKind !== "priority") return "A decision is required";
  if (window.stack.some((ref) => !armed.stack.includes(ref))) return "New object on the stack";
  if (decisionKind === "priority" && !canPass) return "A response is available";
  return undefined;
}
