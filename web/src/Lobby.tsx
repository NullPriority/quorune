import { type FormEvent, useRef, useState } from "react";
import { ApiError, api, beginGuestSession, type Guest, type Room, type SystemStatus } from "./api";
import { BuildIdentity } from "./GameMenu";
import { PlayLibrary, type SavedDeckSelection } from "./PlayLibrary";

export function Lobby({ guest, system, onRoom, onGame }: {
  guest: Guest; system: SystemStatus | null;
  onRoom: (room: Room, invite?: string, selection?: SavedDeckSelection) => void;
  onGame: (gameId: string) => void;
}) {
  const [invite, setInvite] = useState("");
  const [seat, setSeat] = useState("B");
  const [playerCount, setPlayerCount] = useState<2 | 4>(4);
  const [error, setError] = useState("");
  const [opening, setOpening] = useState(false);
  const pending = useRef(false);
  async function openRoom(request: () => Promise<{ room: Room; invite_code?: string }>) {
    if (pending.current) return;
    pending.current = true;
    setOpening(true);
    try {
      setError("");
      const result = await request();
      onRoom(result.room, result.invite_code);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Couldn’t open the table. Try again.");
    } finally {
      pending.current = false;
      setOpening(false);
    }
  }
  async function join(event: FormEvent) {
    event.preventDefault();
    if (invite.trim()) await openRoom(() => api.joinRoom(invite.trim(), seat));
  }
  return (
    <main className="page-shell narrow lobby-shell">
      <header className="page-header">
        <div className="session-line">
          <span className="status-dot" /> Playing as {guest.display_name}
          {system?.card_data.database && <> · {system.card_data.database.cards.toLocaleString()} local cards</>}
        </div>
        <div className="eyebrow">QUORUNE</div>
        <h1>Find your table</h1>
      </header>
      <PlayLibrary tableSize={playerCount} onRoom={onRoom} onGame={onGame} />
      <div className="lobby-grid">
        <section className="panel">
          <h2>Host a pod</h2>
          <p>Create an invite-only 1v1 duel or four-player Commander room.</p>
          <label>
            Table size
            <select
              data-testid="room-size"
              value={playerCount}
              onChange={(event) => setPlayerCount(Number(event.target.value) as 2 | 4)}
            >
              <option value={4}>Commander table (3–4 players)</option>
              <option value={2}>1v1 Commander duel</option>
            </select>
          </label>
          <button data-testid="create-room" disabled={opening} onClick={() => void openRoom(() => api.createRoom(playerCount))}>Create room</button>
        </section>
        <section className="panel">
          <h2>Join a pod</h2>
          <form onSubmit={join} className="stack-form">
            <label>
              Invite code
              <input data-testid="invite-code" value={invite} onChange={(event) => setInvite(event.target.value)} required />
            </label>
            <label>
              Seat
              <select data-testid="seat-select" value={seat} onChange={(event) => setSeat(event.target.value)}>
                {"ABCD".split("").map((value) => <option key={value} value={value}>Seat {value}</option>)}
              </select>
            </label>
            <button data-testid="join-room" type="submit" disabled={opening}>Take seat</button>
            <button data-testid="watch-room" className="quiet-button" type="button" disabled={opening || !invite.trim()}
              onClick={() => void openRoom(() => api.watchRoom(invite.trim()))}>Watch only</button>
          </form>
        </section>
      </div>
      {opening && <p role="status">Opening table…</p>}
      {error && <div className="error-banner" role="alert">{error}</div>}
      <details><summary>Build details</summary><BuildIdentity system={system} /></details>
    </main>
  );
}

export function Welcome({ onReady }: { onReady: (guest: Guest) => void }) {
  const [name, setName] = useState("");
  const [error, setError] = useState("");
  const [joining, setJoining] = useState(false);
  const pending = useRef(false);
  async function submit(event: FormEvent) {
    event.preventDefault();
    const displayName = name.trim();
    if (pending.current || !displayName) return;
    pending.current = true;
    setJoining(true);
    try {
      setError("");
      beginGuestSession();
      const result = await api.guest(displayName);
      onReady(result.guest);
    } catch (caught) {
      setError(caught instanceof ApiError && caught.status < 500
        ? caught.message : "Couldn’t join Quorune. Try again when the server is ready.");
    } finally {
      pending.current = false;
      setJoining(false);
    }
  }
  return (
    <main className="welcome-shell">
      <section className="hero-card">
        <div className="hero-copy">
          <div className="brand-mark" aria-hidden="true">Q</div>
          <h1>Quorune</h1>
          <p>Play a Commander duel or invite three friends to a table.</p>
        </div>
        <form onSubmit={submit} className="stack-form entry-form">
          <div>
            <h2>Join as a guest</h2>
            <p>Choose the name your pod will see. No account is required.</p>
          </div>
          <label>
            Display name
            <input
              data-testid="display-name"
              value={name}
              onChange={(event) => setName(event.target.value)}
              maxLength={40}
              autoComplete="nickname"
              autoFocus
              required
            />
          </label>
          <button data-testid="create-guest" type="submit" disabled={joining || !name.trim()}>{joining ? "Joining…" : "Continue to tables"}</button>
          {error && <div className="error-banner" role="alert">{error}</div>}
        </form>
      </section>
    </main>
  );
}
