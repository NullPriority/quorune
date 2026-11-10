import type { JsonValue, LegalAction } from "./generated/protocol";

export function choiceSubmitLabel(
  action: LegalAction, manualMana: boolean, submitting: boolean, allocation: JsonValue | undefined,
): string {
  if (submitting) return "Submitting…";
  if (action.action !== "cast") return action.form?.submit_label ?? "Submit";
  const selectedPool = allocation && typeof allocation === "object" && !Array.isArray(allocation)
    && Object.keys(allocation).length > 0;
  const label = action.label ?? "Cast";
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
  const terminal = input.lifecycleStatus === "complete";
  const current = macroPhase(input.phase, input.step);
  const currentIndex = MACRO_PHASES.findIndex((entry) => entry.id === current);
  return {
    terminal,
    headline: terminal
      ? "Game complete"
      : `Seat ${input.activeSeat || "?"}'s Turn · Turn ${input.turnSequence}`,
    priority: terminal
      ? "No live priority"
      : input.prioritySeat
        ? `Priority: Seat ${input.prioritySeat}`
        : "Priority: resolving game actions",
    exactStep: terminal ? "Final result" : exactStepLabel(input),
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
  if (
    action.action === "pass"
    && window.activeSeat === window.ownSeat
    && window.stackDepth === 0
  ) {
    if (window.phase === "precombat_main") return "Continue to combat";
    if (window.phase === "postcombat_main") return "End turn";
  }
  return action.label ?? action.kind ?? action.action;
}
