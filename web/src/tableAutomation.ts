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

function isRoutineManaOffer(action: AutoPassAction): boolean {
  return action.action === "activate" && action.mana_ability === true && !action.form
    && (!action.cost_summary || Object.keys(action.cost_summary).every((key) => key === "tap"));
}

export function hasPriorityResponse(actions: readonly AutoPassAction[]): boolean {
  return actions.some((action) => !["pass", "concede"].includes(action.action)
    && !isRoutineManaOffer(action));
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
  if (activeEmptyMain && !window.allowActiveMain
      && !actions.some((action) => isRoutineManaOffer(action) || skipsOptionalActivation(action, window))) {
    return null;
  }
  return hasPriorityResponse(actions.filter((action) => !skipsOptionalActivation(action, window))) ? null : pass;
}

// The projected stack is top-first. Only removing a prefix can resolve the
// captured stack without introducing, reordering, or changing its objects.
export function remainsOfCapturedStack(captured: readonly string[], current: readonly string[]): boolean {
  const offset = captured.length - current.length;
  return offset >= 0 && current.every((item, index) => captured[offset + index] === item);
}

export function ownSpellResponseHold(top: { kind?: unknown; ctl?: unknown } | undefined,
    seat: string | undefined, offers: readonly AutoPassAction[]): boolean {
  return Boolean(seat && top?.kind === "spell" && top.ctl === seat && hasPriorityResponse(offers));
}

export type SourcePriorityYieldOffer = { id: string; targeted: boolean };

export function readSourcePriorityYieldOffer(value: unknown): SourcePriorityYieldOffer | undefined {
  if (!value || typeof value !== "object") return undefined;
  const offer = value as Record<string, unknown>;
  if (typeof offer.id !== "string" || !/^[0-9a-f]{64}$/.test(offer.id)
      || typeof offer.targeted !== "boolean") return undefined;
  return { id: offer.id, targeted: offer.targeted };
}

export function sourcePriorityYieldStatus(scope: string | undefined, offer: SourcePriorityYieldOffer | undefined,
    decisionKind: string | undefined, actions: readonly AutoPassAction[], waitingSourceItem = false) {
  if (!scope) return "inactive";
  if (decisionKind && decisionKind !== "priority") return "choice";
  if (scope !== offer?.id) return waitingSourceItem ? "changed" : "inactive";
  if (offer.targeted) return "targeted";
  if (decisionKind !== "priority") return "inactive";
  if (hasPriorityResponse(actions)) return "response";
  return actions.some((action) => action.action === "pass") ? "ready" : "inactive";
}

export function priorityHoldForScope(passTurn: boolean, externalHold: boolean, ordinaryHold?: string): string | undefined {
  if (externalHold) return "Loop shortcut";
  return passTurn ? undefined : ordinaryHold;
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
