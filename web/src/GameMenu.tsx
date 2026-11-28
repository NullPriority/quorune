import type { ReactNode } from "react";
import type { GameLifecycle, SystemStatus } from "./api";

export type MoveFeedback = { interactionId: string; action: string; receipt: string; revision: number; roundTripMs: number };

export function BuildIdentity({ system }: { system: SystemStatus | null }) {
  const database = system?.card_data.database;
  const dateLabel = (timestamp: string | null | undefined) => timestamp ? new Date(timestamp).toLocaleDateString() : "Unavailable";
  return <dl data-testid="build-identity">
    <div><dt>Browser build</dt><dd>{__QUORUNE_CLIENT_BUILD__}</dd></div>
    <div><dt>Server build</dt><dd>{system?.build ?? "Unavailable"}</dd></div>
    <div><dt>Oracle card data</dt><dd>{dateLabel(database?.oracle_updated_at)}</dd></div>
    <div><dt>Card rulings</dt><dd>{dateLabel(database?.rulings_updated_at)}</dd></div>
  </dl>;
}

export function GameMenu({ system, lifecycle, decision, move, disabled, onConcede, children }: {
  system: SystemStatus | null;
  lifecycle: GameLifecycle | null;
  decision?: string;
  move: MoveFeedback | null;
  disabled: boolean;
  onConcede?: () => void;
  children?: ReactNode;
}) {
  const client = __QUORUNE_CLIENT_BUILD__;
  const server = system?.build ?? "Unavailable";
  const database = system?.card_data.database;

  function exportFeedback() {
    const evidence = {
      client,
      server,
      protocol: system?.protocol ?? null,
      data: { oracle: database?.oracle_updated_at ?? null, rulings: database?.rulings_updated_at ?? null },
      viewport: { width: window.innerWidth, height: window.innerHeight, pixelRatio: window.devicePixelRatio },
      decision: decision ?? null,
      move,
      lastTableSaveMs: lifecycle?.persistence.last_authoritative_seconds == null
        ? null : Math.round(lifecycle.persistence.last_authoritative_seconds * 1000),
    };
    const url = URL.createObjectURL(new Blob([JSON.stringify(evidence, null, 2)], { type: "application/json" }));
    const download = document.createElement("a");
    download.href = url;
    download.download = "quorune-playtest-feedback.json";
    download.click();
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  return <details className="game-menu">
    <summary data-testid="open-game-menu">Game menu</summary>
    <div className="game-menu-panel">
      <strong>About Quorune</strong>
      <BuildIdentity system={system} />
      <button type="button" className="secondary-button" onClick={exportFeedback}>Export playtest feedback</button>
      <div className="match-menu-actions">{children}</div>
      {onConcede && <div className="game-menu-danger">
        <button type="button" className="action-button danger" data-testid="action-concede"
          disabled={disabled} onClick={(event) => {
            event.currentTarget.closest("details")?.removeAttribute("open");
            onConcede();
          }}>Concede game</button>
      </div>}
    </div>
  </details>;
}
