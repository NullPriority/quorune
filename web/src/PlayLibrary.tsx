import { useEffect, useRef, useState } from "react";
import { api, type RecentGame, type Room, type SavedDeck } from "./api";

export type SavedDeckSelection = { id: string; edit?: boolean };

const statuses: Record<RecentGame["status"], string> = {
  active: "In progress",
  paused: "Paused",
  complete: "Complete",
  aborted: "Ended",
};

export function PlayLibrary({
  tableSize, onRoom, onGame,
}: {
  tableSize: 2 | 4;
  onRoom: (room: Room, inviteCode?: string, selection?: SavedDeckSelection) => void;
  onGame: (gameId: string) => void;
}) {
  const [decks, setDecks] = useState<SavedDeck[]>([]);
  const [games, setGames] = useState<RecentGame[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const pending = useRef(false);
  useEffect(() => {
    let stopped = false;
    void Promise.allSettled([api.savedDecks(), api.recentGames()]).then(([lists, records]) => {
      if (stopped) return;
      if (lists.status === "fulfilled") setDecks(lists.value.decks);
      if (records.status === "fulfilled") setGames(records.value.games);
      if (lists.status === "rejected" || records.status === "rejected") {
        setError(lists.status === "fulfilled" ? "Recent games couldn’t be loaded."
          : records.status === "fulfilled" ? "Saved decks couldn’t be loaded."
            : "Saved decks and recent games couldn’t be loaded.");
      }
      setLoading(false);
    });
    return () => { stopped = true; };
  }, []);

  async function openDeck(deck: SavedDeck, edit = false) {
    if (pending.current) return;
    pending.current = true;
    setBusy(true);
    setError("");
    try {
      const result = await api.createRoom(tableSize);
      onRoom(result.room, result.invite_code, { id: deck.id, edit });
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Couldn't create the table.");
    } finally {
      pending.current = false;
      setBusy(false);
    }
  }

  async function openGame(game: RecentGame) {
    if (pending.current) return;
    pending.current = true;
    setBusy(true);
    setError("");
    try {
      const result = await api.room(game.room_id);
      onRoom(result.room);
      onGame(game.game_id);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Couldn't open the game.");
    } finally {
      pending.current = false;
      setBusy(false);
    }
  }

  if (!loading && !error && decks.length === 0 && games.length === 0) return null;
  return <div className="play-library" aria-busy={loading}>
    <section className="panel" aria-label="Saved decks">
      <h2>Saved decks</h2>
      {!loading && decks.length === 0 && <p>Your validated lists will appear here.</p>}
      <ul>{decks.map((deck) => <li key={deck.id}>
        <div><strong>{deck.name}</strong><small>{deck.commanders.join(" and ")}</small></div>
        <div className="saved-deck-actions">
          <button type="button" className="secondary-button" disabled={busy}
            onClick={() => void openDeck(deck, true)} aria-label={`Edit ${deck.name}`}>Edit deck</button>
          <button type="button" className="secondary-button" disabled={busy}
            onClick={() => void openDeck(deck)} aria-label={`Use ${deck.name} in a new table`}>Use deck</button>
        </div>
      </li>)}</ul>
    </section>
    <section className="panel" aria-label="Recent games">
      <h2>Recent games</h2>
      {!loading && games.length === 0 && <p>Games you join will appear here.</p>}
      <ul>{games.map((game) => <li key={game.game_id}>
        <div><strong>{game.format_profile === "commander_duel" ? "Commander duel" : "Commander pod"}</strong>
          <small>{statuses[game.status]} · {new Date(game.created_at).toLocaleString()}</small></div>
        <button type="button" className="secondary-button" disabled={busy}
          onClick={() => void openGame(game)}>{game.status === "complete" || game.status === "aborted" ? "View game" : "Open game"}</button>
      </li>)}</ul>
    </section>
    {error && <p role="alert">{error}</p>}
  </div>;
}
