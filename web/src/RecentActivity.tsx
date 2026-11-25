import type { PublicGameEvent } from "./api";
import { SeatLabel, tableSeat } from "./SeatLabel";

export function RecentActivity({ events, describeEvent, onOpenLog }: {
  events: PublicGameEvent[];
  describeEvent: (event: PublicGameEvent) => string;
  onOpenLog: () => void;
}) {
  return (
    <section className="activity-panel" data-testid="activity-panel">
      <header><div className="zone-label">RECENT ACTIVITY</div><button type="button" className="link-button" onClick={onOpenLog}>Full log</button></header>
      <ol>
        {events.slice(-5).reverse().map((event) => <li key={event.id}>
          {tableSeat(event.actor) ? <SeatLabel seat={event.actor} /> : <span>Game</span>}{describeEvent(event)}
        </li>)}
        {!events.length && <li className="empty-activity">Game events will appear here.</li>}
      </ol>
    </section>
  );
}
