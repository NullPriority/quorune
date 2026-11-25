import { asList, asRecord } from "./CardSurface";
import type { JsonValue } from "./generated/protocol";
import type { ProjectedView } from "./protocol";

type Seat = "A" | "B" | "C" | "D";

export function tableSeat(value: unknown): Seat | undefined {
  return value === "A" || value === "B" || value === "C" || value === "D" ? value : undefined;
}

export function SeatLabel({ seat }: { seat: string | null | undefined }) {
  const shown = tableSeat(seat);
  return shown ? <span className="seat-label" data-seat={shown}>Seat {shown}</span> : null;
}

export function projectedCardSeat(view: ProjectedView, value: JsonValue | null): Seat | undefined {
  const card = asRecord(value);
  const controller = tableSeat(card.ctl);
  if (controller) return controller;
  if (typeof card.id !== "string") return undefined;
  for (const [seat, player] of Object.entries(asRecord(view.state.players))) {
    const zones = asRecord(player);
    const visible = ["bf", "cmd", "gy", "ex", "hand", "known_hand", "known_top"];
    if (visible.some((zone) => asList(zones[zone]).some((entry) => asRecord(entry).id === card.id))) {
      return tableSeat(seat);
    }
  }
  return undefined;
}
