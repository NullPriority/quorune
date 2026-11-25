import type { JsonValue, LegalAction } from "./generated/protocol";
import type { ProjectedView } from "./protocol";
import { list, record } from "./choices";

const zones = [
  ["gy", "Graveyard"], ["ex", "Exile"], ["cmd", "Command zone"], ["known_top", "Library"],
] as const;

export type HandZoneOffer = { card: JsonValue; actions: LegalAction[]; origin: string; zone: typeof zones[number][0] };

export function handZoneOffers(view: ProjectedView): Map<string, HandZoneOffer> {
  const decision = view.decision;
  const actor = decision?.actor;
  if (!actor || view.principal !== `pilot:${actor}`) return new Map();

  const byRef = new Map<string, LegalAction[]>();
  for (const action of decision.legal_actions) {
    if (!["cast", "activate", "play_land"].includes(action.action)) continue;
    const ref = action.card ?? action.source;
    if (typeof ref !== "string") continue;
    const actions = byRef.get(ref);
    if (actions) actions.push(action);
    else byRef.set(ref, [action]);
  }

  const offers = new Map<string, HandZoneOffer>();
  if (!byRef.size) return offers;
  const players = record(view.state.players);
  const hand = new Set(list(record(players[actor]).hand).map((card) => record(card).id));
  for (const [seat, player] of Object.entries(players)) {
    for (const [zone, label] of zones) {
      for (const card of list(record(player)[zone])) {
        const ref = record(card).id;
        if (typeof ref !== "string" || hand.has(ref) || offers.has(ref)) continue;
        const actions = byRef.get(ref);
        if (actions) offers.set(ref, {
          card, actions, zone, origin: seat === actor ? label : `Seat ${seat}’s ${label.toLowerCase()}`,
        });
      }
    }
  }
  return offers;
}
