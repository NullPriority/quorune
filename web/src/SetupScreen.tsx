import { useEffect, useState } from "react";
import { api, type SystemStatus } from "./api";
import { BuildIdentity } from "./GameMenu";

export function SetupScreen({ initial, onReady }: { initial: SystemStatus; onReady: () => void }) {
  const [status, setStatus] = useState(initial);
  const [retrying, setRetrying] = useState(false);

  useEffect(() => {
    let stopped = false;
    const poll = async () => {
      try {
        const next = await api.system();
        if (stopped) return;
        setStatus(next);
        if (next.card_data.ready) onReady();
      } catch {
        // Keep the last actionable setup status visible while the server restarts.
      }
    };
    const timer = window.setInterval(poll, 1000);
    return () => {
      stopped = true;
      window.clearInterval(timer);
    };
  }, [onReady]);

  async function retry() {
    setRetrying(true);
    try {
      await api.refreshSystem();
      setStatus(await api.system());
    } catch {
      // Polling keeps the last server-provided failure visible.
    } finally {
      setRetrying(false);
    }
  }

  const data = status.card_data;
  const stage: Record<string, string> = {
    starting: "Starting the card library",
    checking: "Checking for new card data",
    downloading: "Downloading card data",
    building: "Preparing the local card database",
    error: "Card library needs attention",
  };
  return (
    <main className="setup-shell" data-testid="setup-screen">
      <section className="setup-card" aria-live="polite">
        <div className="brand-mark" aria-hidden="true">Q</div>
        <div>
          <h1>{stage[data.phase] ?? "Preparing the card library"}</h1>
          {data.phase === "error" && <p>{data.automatic_updates
            ? "Check the server’s network connection and retry setup."
            : "Ask the host to restore the local card database and restart Quorune."}</p>}
        </div>
        <dl className="setup-facts">
          <div><dt>Server</dt><dd><span className="status-dot" /> Running</dd></div>
          <div><dt>Card data</dt><dd>{data.phase === "error" ? "Needs attention" : "Setting up"}</dd></div>
        </dl>
        <details><summary>Setup details</summary>
          <p>{data.detail}</p>{data.last_error && data.last_error !== data.detail && <p>{data.last_error}</p>}
          <BuildIdentity system={status} />
        </details>
        {data.phase === "error" && data.automatic_updates && (
          <button type="button" onClick={retry} disabled={retrying}>{retrying ? "Retrying…" : "Retry setup"}</button>
        )}
      </section>
    </main>
  );
}
