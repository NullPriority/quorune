import { type FormEvent, useEffect, useState } from "react";
import { api, ApiError, type EditableDeckEntry, type Guest, type LegalityConfirmationRequired, type Room, type SavedDeck, type StartingPlayer } from "./api";
import { DeckEditor, deckEditorText } from "./DeckEditor";
import { DeckReadiness } from "./DeckReadiness";
import { deckSubmissionNotice } from "./deckSubmissionNotice";
import type { JsonValue } from "./generated/protocol";
import type { SavedDeckSelection } from "./PlayLibrary";
import { tableSeat } from "./SeatLabel";

function readable(value: JsonValue | undefined, fallback = "Unknown"): string {
  const text = String(value ?? "").replaceAll("_", " ").trim();
  return text ? text.replace(/\b\w/g, (letter) => letter.toUpperCase()) : fallback;
}

export function inviteStorageKey(roomId: string): string {
  return `commander-invite:${roomId}`;
}

export function RoomView({
  guest,
  initial,
  invite,
  onGame,
  onRoom,
  onLeave,
  initialSavedDeck,
}: {
  guest: Guest;
  initial: Room;
  invite: string;
  onGame: (gameId: string) => void;
  onRoom: (room: Room, invite?: string) => void;
  onLeave: () => void;
  initialSavedDeck?: SavedDeckSelection;
}) {
  const [room, setRoom] = useState(initial);
  const [currentInvite, setCurrentInvite] = useState(invite);
  const [name, setName] = useState("My Commander deck");
  const [commander, setCommander] = useState("");
  const [decklist, setDecklist] = useState("");
  const [sourceUrl, setSourceUrl] = useState("");
  const [editor, setEditor] = useState<{ id?: string; entries: EditableDeckEntry[]; refs: Record<string, string> }>();
  const [visualEditor, setVisualEditor] = useState(false);
  const [savedDecks, setSavedDecks] = useState<SavedDeck[]>([]);
  const [savedDeckId, setSavedDeckId] = useState(initialSavedDeck?.id ?? "");
  const [message, setMessage] = useState("");
  const [messageKind, setMessageKind] = useState<"success" | "warning" | "error">("success");
  const [busy, setBusy] = useState(false);
  const [legalityReview, setLegalityReview] = useState<LegalityConfirmationRequired | null>(null);
  const [nextPlayerCount, setNextPlayerCount] = useState<2 | 4>(initial.seat_count);
  const [firstPlayer, setFirstPlayer] = useState<StartingPlayer>("random");
  const [startingLifeInput, setStartingLifeInput] = useState("");
  const mine = room.seats.find((seat) => seat.mine);
  const owner = room.owner_guest_id === guest.guest_id;
  const occupiedSeats = room.seats.filter((seat) => seat.guest_id !== null);
  const minimumPlayers = room.seat_count === 2 ? 2 : 3;
  const enoughPlayers = occupiedSeats.length >= minimumPlayers;
  const requiredDeckCount = Math.max(minimumPlayers, occupiedSeats.length);
  const readyCount = occupiedSeats.filter((seat) => seat.ready).length;
  const allReady = readyCount === requiredDeckCount;
  const allDecksSubmitted = enoughPlayers && occupiedSeats.every((seat) => seat.deck !== null);
  const reviewingDraft = !mine?.ready && Boolean(mine?.deck?.preflight?.data_integrity_issue);
  const startingSeat = occupiedSeats.some((seat) => seat.seat === firstPlayer) ? firstPlayer : "random";
  const startingLife = startingLifeInput === "" ? undefined : Number(startingLifeInput);
  const invalidStartingLife = startingLife !== undefined && (!Number.isSafeInteger(startingLife) || startingLife < 1);

  useEffect(() => {
    if (room.spectator) return;
    let stopped = false;
    void api.savedDecks().then(({ decks }) => {
      if (stopped) return;
      setSavedDecks(decks);
      const preferred = decks.find((deck) => deck.id === initialSavedDeck?.id);
      if (!preferred) return;
      if (initialSavedDeck?.edit) void editSavedDeck(preferred.id);
      else setName(preferred.name);
    }).catch(() => {
      if (stopped) return;
      setMessageKind("warning");
      setMessage("Saved decks could not be loaded. Paste a list or import a public deck link.");
    });
    return () => { stopped = true; };
  }, [initialSavedDeck, room.spectator]);

  function chooseSavedDeck(id: string) {
    setEditor(undefined);
    setVisualEditor(false);
    setSavedDeckId(id);
    const selected = savedDecks.find((deck) => deck.id === id);
    if (selected) setName(selected.name);
  }

  async function editSavedDeck(id: string) {
    setBusy(true);
    try {
      const { deck, card_refs } = await api.savedDeck(id);
      setEditor({ id, entries: deck.entries, refs: card_refs });
      setVisualEditor(false);
      setName(deck.name);
      setCommander(deck.commanders[0] ?? "");
      setDecklist(deckEditorText(deck.entries));
      setSourceUrl("");
      setSavedDeckId("");
    } catch (caught) {
      setMessageKind("error");
      setMessage(caught instanceof Error ? caught.message : "Couldn't open the saved list.");
    } finally {
      setBusy(false);
    }
  }

  async function copyInvite() {
    try {
      await navigator.clipboard.writeText(currentInvite);
      setMessageKind("success");
      setMessage("Invite code copied to clipboard.");
    } catch {
      setMessageKind("error");
      setMessage("Could not copy automatically. Select the invite code and copy it manually.");
    }
  }

  async function replaceInvite() {
    setBusy(true);
    try {
      const result = await api.rotateInvite(room.room_id);
      setCurrentInvite(result.invite_code);
      sessionStorage.setItem(inviteStorageKey(room.room_id), result.invite_code);
      setMessageKind("success");
      setMessage("A new invite code was created. The previous code no longer works.");
    } catch (caught) {
      setMessageKind("error");
      setMessage(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setBusy(false);
    }
  }

  useEffect(() => {
    let stopped = false;
    let timer = 0;
    let delay = 750;
    let startupWaiting = false;
    async function pollRoom() {
      if (stopped) return;
      let continuePolling = true;
      try {
        const result = await api.room(room.room_id);
        delay = 750;
        if (startupWaiting) {
          startupWaiting = false;
          setMessage("");
        }
        if (result.room.status === "closed") {
          continuePolling = false;
          if (!stopped) onLeave();
        } else if (!stopped) {
          setRoom(result.room);
          if (result.room.game_id) onGame(result.room.game_id);
        }
      } catch (caught) {
        if (caught instanceof ApiError && (caught.status === 403 || caught.status === 404)) {
          continuePolling = false;
          if (!stopped) onLeave();
        } else if (!stopped && caught instanceof ApiError && caught.status === 503) {
          startupWaiting = true;
          delay = Math.min(delay * 2, 5000);
          setMessageKind("warning");
          setMessage("The server is finishing its card-data startup check. Room updates will resume automatically.");
        } else if (!stopped) {
          delay = Math.min(delay * 2, 5000);
          setMessageKind("error");
          setMessage(caught instanceof Error ? caught.message : String(caught));
        }
      }
      if (!stopped && continuePolling) timer = window.setTimeout(pollRoom, delay);
    }
    timer = window.setTimeout(pollRoom, delay);
    return () => {
      stopped = true;
      window.clearTimeout(timer);
    };
  }, [room.room_id, onGame, onLeave]);

  async function makeNewRoom() {
    setBusy(true);
    try {
      const oldRoomId = room.room_id;
      const result = await api.replaceRoom(oldRoomId, nextPlayerCount);
      sessionStorage.removeItem(inviteStorageKey(oldRoomId));
      onRoom(result.room, result.invite_code);
    } catch (caught) {
      setMessageKind("error");
      setMessage(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setBusy(false);
    }
  }

  async function leaveRoom() {
    setBusy(true);
    try {
      await api.leaveRoom(room.room_id);
      onLeave();
    } catch (caught) {
      setMessageKind("error");
      setMessage(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setBusy(false);
    }
  }

  async function removeSeat(seat: string) {
    setBusy(true);
    try {
      const result = await api.removeSeat(room.room_id, seat);
      setRoom(result.room);
      setMessageKind("success");
      setMessage(`Seat ${seat} was removed and is open again.`);
    } catch (caught) {
      setMessageKind("error");
      setMessage(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setBusy(false);
    }
  }

  async function submitDeck(legalityConfirmation?: string) {
    setBusy(true);
    if (!legalityConfirmation) setLegalityReview(null);
    try {
      const result = await api.deck(
        room.room_id,
        name,
        commander,
        visualEditor || savedDeckId || sourceUrl.trim() ? "" : decklist,
        visualEditor || savedDeckId ? undefined : sourceUrl.trim() || undefined,
        legalityConfirmation,
        savedDeckId || undefined,
        visualEditor && editor ? { entries: editor.entries, edited_deck_id: editor.id } : undefined,
      );
      setLegalityReview(null);
      if (result.preflight.data_integrity_issue) {
        const { deck_id: id, name, commanders } = result.deck;
        setSavedDecks((decks) => [{ id, name, commanders }, ...decks.filter((deck) => deck.id !== id)]);
        setMessage("Draft saved. Review the card data before starting a game with this deck.");
        setMessageKind("warning");
      } else {
        const notice = deckSubmissionNotice(result);
        setMessage(notice.message);
        setMessageKind(notice.kind);
      }
      setRoom((await api.room(room.room_id)).room);
    } catch (caught) {
      const detail = caught instanceof ApiError ? caught.detail : null;
      if (
        detail
        && typeof detail === "object"
        && "code" in detail
        && detail.code === "legality_confirmation_required"
      ) {
        setLegalityReview(detail as LegalityConfirmationRequired);
        setMessageKind("warning");
        setMessage("Review and confirm the preview-card legality warning to continue.");
        return;
      }
      setMessageKind("error");
      setMessage(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setBusy(false);
    }
  }

  async function upload(event: FormEvent) {
    event.preventDefault();
    await submitDeck();
  }

  async function unready() {
    setBusy(true);
    try {
      const result = await api.clearDeck(room.room_id);
      setRoom(result.room);
      const previous = mine?.deck;
      if (previous) {
        setSavedDecks((decks) => decks.some((deck) => deck.id === previous.deck_id)
          ? decks : [{ id: previous.deck_id, name: previous.name, commanders: [] }, ...decks]);
        await editSavedDeck(previous.deck_id);
      }
      setLegalityReview(null);
      setMessageKind("success");
      setMessage("Edit your current list or choose another deck, then check and submit it.");
    } catch (caught) {
      setMessageKind("error");
      setMessage(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setBusy(false);
    }
  }

  async function start(useReviewedCardData = false) {
    setBusy(true);
    try {
      const result = await api.start(room.room_id, {
        first_player: startingSeat,
        starting_life: startingLife,
        use_reviewed_card_data: useReviewedCardData,
      });
      onGame(result.game_id);
    } catch (caught) {
      setMessageKind("error");
      setMessage(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="page-shell">
      <header className="room-header">
        <div>
          <div className="eyebrow">{room.seat_count === 2 ? "COMMANDER DUEL" : "COMMANDER MULTIPLAYER"}</div>
          <h1>{room.seat_count === 2 ? "Commander duel" : "Commander table"}</h1>
        </div>
        <div className="room-header-actions">
          {owner && <button type="button" className="quiet-button" data-testid="room-library" disabled={busy}
            onClick={onLeave}>Decks and games</button>}
          {owner && currentInvite && (
            <div className="invite-chip">
              <div><span>Invite code</span><strong data-testid="room-invite">{currentInvite}</strong></div>
              <div className="invite-actions">
                <button type="button" className="icon-button" aria-label="Copy invite code" onClick={copyInvite}>Copy</button>
                <button type="button" className="quiet-button" data-testid="replace-invite" disabled={busy} onClick={replaceInvite}>Replace</button>
              </div>
            </div>
          )}
          {owner && !currentInvite && (
            <button type="button" data-testid="replace-invite" disabled={busy} onClick={replaceInvite}>Generate new invite</button>
          )}
          {owner ? (
            <div className="new-room-control">
              <select
                aria-label="New room size"
                data-testid="new-room-size"
                value={nextPlayerCount}
                onChange={(event) => setNextPlayerCount(Number(event.target.value) as 2 | 4)}
              >
                <option value={4}>3–4 players</option>
                <option value={2}>1v1 duel</option>
              </select>
              <button type="button" className="quiet-button" data-testid="new-room" disabled={busy} onClick={makeNewRoom}>New room</button>
            </div>
          ) : (
            <button type="button" className="quiet-button" data-testid="leave-room" disabled={busy} onClick={leaveRoom}>Leave room</button>
          )}
        </div>
      </header>
      <section className={`seat-row seats-${room.seat_count}`}>
        {room.seats.map((seat) => (
          <article key={seat.seat} className={`seat-card ${seat.mine ? "mine" : ""}`} data-testid={`seat-${seat.seat}`}>
            <div className="seat-letter">{seat.seat}</div>
            <div>
              <strong>{seat.display_name ?? "Open seat"}</strong>
              <small>{seat.deck?.name ?? "No deck submitted"}</small>
            </div>
            <span className={`seat-state ${seat.ready ? "ready" : "waiting"}`}><i aria-hidden="true" />
              {seat.ready ? "Deck submitted" : seat.deck ? "Deck needs review" : seat.guest_id ? "Choosing a deck" : "Open"}
            </span>
            {owner && seat.guest_id && !seat.mine && (
              <button type="button" className="seat-remove" data-testid={`remove-seat-${seat.seat}`} disabled={busy} onClick={() => void removeSeat(seat.seat)}>Remove</button>
            )}
          </article>
        ))}
      </section>
      {room.spectator && (
        <section className="watch-room-card" data-testid="watch-mode">
          <div>
            <div className="eyebrow">READ-ONLY TABLE ACCESS</div>
            <h2>You are watching this table</h2>
            <p>Public zones and the public game log are available. Hands, private choices, and player capabilities stay hidden.</p>
          </div>
          <button type="button" className="quiet-button" data-testid="leave-watch-room" disabled={busy} onClick={leaveRoom}>Leave table</button>
        </section>
      )}
      {!room.spectator && mine?.deck && (mine.ready || reviewingDraft) && (
        <section className="deck-ready-card" data-testid={reviewingDraft ? "deck-review-summary" : "deck-ready-summary"}>
          <div>
            <h2>{mine.deck.name}</h2>
            <DeckReadiness deck={mine.deck} />
          </div>
          <div className="deck-ready-actions">
            {mine.ready ? <button type="button" className="quiet-button" data-testid="unready-deck" disabled={busy} onClick={unready}>Change deck</button>
              : <button type="button" className="quiet-button" disabled={busy} onClick={() => void editSavedDeck(mine.deck!.deck_id)}>Edit saved draft</button>}
          </div>
        </section>
      )}
      {!room.spectator && !mine?.ready && (
        <section className="panel deck-panel">
          <div>
            <div className="eyebrow">SEAT {mine?.seat}</div>
            <h2>Validate your deck</h2>
            <p>Choose a saved deck, import a public deck link, or paste a list.</p>
          </div>
          <form onSubmit={upload} className="deck-form">
            {savedDecks.length > 0 && <label className="wide">Saved deck
              <select value={savedDeckId} onChange={(event) => chooseSavedDeck(event.target.value)}>
                <option value="">Use a pasted list or public link</option>
                {savedDecks.map((deck) => <option key={deck.id} value={deck.id}>{deck.name}</option>)}
              </select>
            </label>}
            <label>Deck name<input data-testid="deck-name" value={name} onChange={(event) => setName(event.target.value)} required /></label>
            {savedDeckId ? <button type="button" className="secondary-button" disabled={busy}
              onClick={() => void editSavedDeck(savedDeckId)}>Edit saved list</button> : visualEditor && editor ? <>
              <DeckEditor key={editor.id ?? "new"} entries={editor.entries} cardRefs={editor.refs}
                onChange={(entries) => setEditor({ ...editor, entries })} />
              <button type="button" className="secondary-button" onClick={() => {
                setDecklist(deckEditorText(editor.entries));
                setCommander(editor.entries.find((entry) => entry.board === "commander")?.name ?? "");
                setVisualEditor(false);
              }}>Edit as text</button>
            </> : <>
              <label>Commander (required for pasted text)<input data-testid="commander-name" value={commander} onChange={(event) => {
                setEditor(undefined);
                setCommander(event.target.value);
              }} required={!sourceUrl.trim()} /></label>
              <label className="wide">Public deck URL<input data-testid="deck-source-url" value={sourceUrl}
                onChange={(event) => { setEditor(undefined); setSourceUrl(event.target.value); }}
                placeholder="Moxfield, Archidekt, or TappedOut" /></label>
              <div className="import-divider wide"><span>or paste a list</span></div>
              <label className="wide">Deck list<textarea data-testid="deck-list" value={decklist} onChange={(event) => {
                setEditor(undefined);
                setDecklist(event.target.value);
              }} rows={12} required={!sourceUrl.trim()} /></label>
              {(editor || (!sourceUrl.trim() && !decklist.trim())) && <button type="button" className="secondary-button"
                onClick={() => {
                  if (!editor) setEditor({ entries: [], refs: {} });
                  setVisualEditor(true);
                }}>{editor ? "Edit visually" : "Build visually"}</button>}
            </>}
            <button data-testid="submit-deck" disabled={busy || (visualEditor && !editor?.entries.length)} type="submit">
              {busy ? "Checking deck…" : "Check and submit deck"}
            </button>
          </form>
        </section>
      )}
      {!room.spectator && !mine?.ready && legalityReview && (
        <section className="preview-review" role="alert" data-testid="legality-confirmation">
          <div>
            <div className="eyebrow">PREVIEW CARD WARNING</div>
            <h2>These cards are not Commander-legal yet.</h2>
            <p>These cards have future release dates. Confirm to submit this list and check its card behavior.</p>
          </div>
          <ul>
            {legalityReview.issues.map((issue) => (
              <li key={`${issue.card}:${issue.board}`}>
                <strong>{issue.card}</strong>
                <span>{readable(issue.legality)} · releases {issue.released_at ?? "unknown"}</span>
              </li>
            ))}
          </ul>
          <button
            type="button"
            data-testid="confirm-preview-legality"
            disabled={busy}
            onClick={() => void submitDeck(legalityReview.confirmation)}
          >
            {busy ? "Confirming…" : "I understand — use this preview list"}
          </button>
        </section>
      )}
      {owner && (
        <div className="start-bar">
          <div className="readiness-copy">
            <strong>{readyCount} of {requiredDeckCount} decks ready</strong>
            <span>{allReady ? "You can start the game."
              : !enoughPlayers ? `At least ${minimumPlayers} players must join.`
              : allDecksSubmitted ? "A submitted deck needs a card data review."
              : "Each seated player needs to submit a deck."}</span>
          </div>
          <div className="readiness-meter" aria-hidden="true"><span style={{ width: `${readyCount / requiredDeckCount * 100}%` }} /></div>
          <div className="room-start-actions">
            <label>Starting player
              <select value={startingSeat} disabled={busy} onChange={(event) => setFirstPlayer(tableSeat(event.target.value) ?? "random")}>
                <option value="random">Random</option>
                {occupiedSeats.map((seat) => <option key={seat.seat} value={seat.seat}>Seat {seat.seat} · {seat.display_name}</option>)}
              </select>
            </label>
            <button data-testid="start-game" disabled={busy || invalidStartingLife || !allReady} onClick={() => void start()}>{room.seat_count === 2 ? "Start duel" : "Start game"}</button>
          </div>
          <details className="advanced-start-settings">
            <summary>Advanced settings</summary>
            <label>Starting life per player
              <input type="number" min={1} max={Number.MAX_SAFE_INTEGER} step={1} placeholder="40"
                value={startingLifeInput} disabled={busy} aria-invalid={invalidStartingLife || undefined}
                aria-describedby={invalidStartingLife ? "starting-life-error" : undefined}
                onChange={(event) => setStartingLifeInput(event.target.value)} />
            </label>
            {invalidStartingLife && <small id="starting-life-error" role="alert">Enter a valid whole number greater than zero.</small>}
          </details>
          {allDecksSubmitted && !allReady && <details className="reviewed-rules-action">
            <summary>Use earlier card data</summary>
            <div>
              <p>Start this match using one retained version of the card data for the whole table. Every submitted deck must pass its checks with that version.</p>
              <button type="button" className="quiet-button" disabled={busy || invalidStartingLife} onClick={() => void start(true)}>
                {busy ? "Checking decks…" : "Start with earlier card data"}
              </button>
            </div>
          </details>}
        </div>
      )}
      {message && <div className={`${messageKind}-banner`} role={messageKind === "error" ? "alert" : "status"}>{message}</div>}
    </main>
  );
}
