import type { JsonValue, LegalAction } from "./generated/protocol";
import type { ProjectedView } from "./protocol";

function fields(value: JsonValue | undefined): Record<string, JsonValue> {
  return value && typeof value === "object" && !Array.isArray(value) ? value : {};
}

function canonical(value: unknown): string {
  return JSON.stringify(value, (_key, item: unknown) =>
    item && typeof item === "object" && !Array.isArray(item)
      ? Object.fromEntries(Object.entries(item).sort(([left], [right]) => left < right ? -1 : left > right ? 1 : 0))
      : item);
}

export function repeatableOffer(action: LegalAction): string {
  const { expiry_revision: _revision, proposal_fingerprint: _proposal, ...summary } = action;
  return canonical(summary);
}

const responseEvents = new Set([
  "stack.cast", "stack.activate", "mana.ability", "mana.produce", "mana.undo", "land.play",
  "card.suspend", "player.eliminated",
]);

export function opponentResponded(view: ProjectedView, afterEvent: number): boolean {
  const seat = view.principal.split(":")[1];
  return view.events.some((row) => row.pub === true && typeof row.id === "number" && row.id > afterEvent
    && typeof row.a === "string" && row.a !== seat && typeof row.c === "string" && responseEvents.has(row.c));
}

export function loopFrame(view: ProjectedView | null): string | null {
  if (!view || view.decision?.kind !== "priority" || !view.principal.startsWith("pilot:")) return null;
  const state = view.state;
  const turn = fields(state.turn);
  const seat = view.principal.slice("pilot:".length);
  if (view.decision.actor !== seat) return null;
  const players = Object.fromEntries(Object.entries(fields(state.players)).map(([name, value]) => {
    const player = { ...fields(value) };
    // Available mana may accumulate in a repeating sequence. Each fresh
    // action still has to be offered and paid by the engine.
    if (name === seat) delete player.mana;
    return [name, player];
  }));
  const stack = Array.isArray(state.stack) ? state.stack.map(fields) : [];
  const ownAbilityRefs = new Map<string, string>();
  stack.forEach((item, index) => {
    if (typeof item.id === "string" && item.ctl === seat
      && (item.kind === "activated_ability" || item.kind === "triggered_ability")) {
      ownAbilityRefs.set(item.id, `own-ability-${index}`);
    }
  });
  const stackFrame = stack.map((item) => ({
    ...item,
    id: ownAbilityRefs.get(String(item.id)) ?? item.id,
    ...(Array.isArray(item.targets) ? {
      targets: item.targets.map((target) => ownAbilityRefs.get(String(target)) ?? target),
    } : {}),
  }));
  return canonical({
    game: state.game, principal: view.principal,
    turn: [turn.seq, turn.phase, turn.step, turn.active, turn.extra_q],
    players, stack: stackFrame, combat: state.combat,
  });
}
