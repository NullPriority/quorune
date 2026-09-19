import { type CSSProperties, DragEvent, FormEvent, useEffect, useRef, useState } from "react";
import {
  api,
  ApiError,
  beginGuestSession,
  type GameLifecycle,
  type Guest,
  type LegalityConfirmationRequired,
  type PublicGameEvent,
  type Room,
  type SystemStatus,
  streamUrl,
  streamProtocols,
} from "./api";
import { ChoiceFormView } from "./ChoiceForm";
import {
  executableChoices,
  initialChoices,
  validateChoices,
  type ChoiceValues,
} from "./choices";
import type { CommandEnvelope, DecisionPacket, JsonValue, LegalAction } from "./generated/protocol";
import { asList, asRecord, cardName, CardInspector, CardTile } from "./CardSurface";
import { ingestPacket, type ProjectedView } from "./protocol";
import { findSafeAutoPass } from "./tableAutomation";
import { visibleActionLabel } from "./tablePresentation";
import { AutomationControls, CommanderDamage, ManaHelp } from "./TableStatus";
import { TableSettings } from "./TableSettings";
import { TurnStatus } from "./TurnStatus";
import {
  DEFAULT_TABLE_PREFERENCES,
  loadTablePreferences,
  saveTablePreferences,
  type RightRailPanel,
  type TablePreferences,
} from "./tablePreferences";
import { useTableDockClearance } from "./useTableDockClearance";

type Screen = "loading" | "setup" | "welcome" | "lobby" | "room" | "game";

function readable(value: JsonValue | undefined, fallback = "Unknown"): string {
  const text = String(value ?? "").replaceAll("_", " ").trim();
  return text ? text.replace(/\b\w/g, (letter) => letter.toUpperCase()) : fallback;
}

function inviteStorageKey(roomId: string): string {
  return `commander-invite:${roomId}`;
}

function actionTone(action: LegalAction): string {
  const value = `${action.id} ${action.action} ${action.kind ?? ""}`.toLowerCase();
  if (value.includes("pass") || value.includes("decline") || value.includes("cancel")) return "quiet";
  if (value.includes("concede")) return "danger";
  return "primary";
}

function projectedLabels(view: ProjectedView): Map<string, string> {
  const labels = new Map<string, string>();
  function walk(value: JsonValue | undefined) {
    if (Array.isArray(value)) {
      value.forEach(walk);
      return;
    }
    if (!value || typeof value !== "object") return;
    const row = value as Record<string, JsonValue>;
    const id = row.id;
    if (typeof id === "string") {
      const label = row.label ?? row.n ?? row.name;
      if (typeof label === "string") labels.set(id, label);
    }
    Object.values(row).forEach(walk);
  }
  walk(view.state);
  walk(view.decision?.ctx);
  for (const seat of ["A", "B", "C", "D"]) labels.set(seat, `Seat ${seat}`);
  return labels;
}

function Welcome({ onReady }: { onReady: (guest: Guest) => void }) {
  const [name, setName] = useState("");
  const [error, setError] = useState("");
  async function submit(event: FormEvent) {
    event.preventDefault();
    try {
      setError("");
      beginGuestSession();
      const result = await api.guest(name.trim());
      onReady(result.guest);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    }
  }
  return (
    <main className="welcome-shell">
      <section className="hero-card">
        <div className="hero-copy">
          <div className="brand-mark" aria-hidden="true">Q</div>
          <div className="eyebrow">QUORUNE · SERVER-AUTHORITATIVE PLAY</div>
          <h1>Authoritative rules.<br />Private state. Exact replay.</h1>
          <p>
            Play multiplayer card games through a deterministic rules engine.
            Your hand stays private, every accepted move is replayable, and each
            player sees only their own projected table.
          </p>
          <ul className="trust-list" aria-label="Platform guarantees">
            <li>Seat-private game views</li>
            <li>Server-validated actions</li>
            <li>Durable match recovery</li>
          </ul>
        </div>
        <form onSubmit={submit} className="stack-form entry-form">
          <div>
            <span className="step-kicker">01 · PLAYER IDENTITY</span>
            <h2>Enter Quorune</h2>
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
          <button data-testid="create-guest" type="submit">Continue to tables <span aria-hidden="true">→</span></button>
          {error && <div className="error-banner" role="alert">{error}</div>}
        </form>
      </section>
    </main>
  );
}

function Lobby({ guest, system, onRoom }: { guest: Guest; system: SystemStatus | null; onRoom: (room: Room, invite?: string) => void }) {
  const [invite, setInvite] = useState("");
  const [seat, setSeat] = useState("B");
  const [playerCount, setPlayerCount] = useState<2 | 4>(4);
  const [error, setError] = useState("");
  async function create() {
    try {
      setError("");
      const result = await api.createRoom(playerCount);
      onRoom(result.room, result.invite_code);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    }
  }
  async function join(event: FormEvent) {
    event.preventDefault();
    try {
      setError("");
      const result = await api.joinRoom(invite.trim(), seat);
      onRoom(result.room);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    }
  }
  async function watch() {
    try {
      setError("");
      const result = await api.watchRoom(invite.trim());
      onRoom(result.room);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    }
  }
  return (
    <main className="page-shell narrow">
      <header className="page-header">
        <div className="session-line">
          <span className="status-dot" /> Session ready · {guest.display_name}
          {system?.card_data.database && <> · {system.card_data.database.cards.toLocaleString()} local cards</>}
        </div>
        <div className="eyebrow">QUORUNE</div>
        <h1>Find your table</h1>
        <p className="page-lede">Host a private duel or four-player table using the current Commander profile, join a seat, or watch with an invite code.</p>
      </header>
      <div className="lobby-grid">
        <section className="panel">
          <div className="panel-number">01</div>
          <h2>Host a pod</h2>
          <p>Create an invite-only 1v1 duel or four-player Commander room.</p>
          <label>
            Table size
            <select
              data-testid="room-size"
              value={playerCount}
              onChange={(event) => setPlayerCount(Number(event.target.value) as 2 | 4)}
            >
              <option value={4}>Four-player Commander</option>
              <option value={2}>1v1 Commander duel</option>
            </select>
          </label>
          <button data-testid="create-room" onClick={create}>Create room</button>
        </section>
        <section className="panel">
          <div className="panel-number">02</div>
          <h2>Join a pod</h2>
          <form onSubmit={join} className="stack-form">
            <label>
              Invite code
              <input data-testid="invite-code" value={invite} onChange={(event) => setInvite(event.target.value)} required />
            </label>
            <label>
              Seat
              <select data-testid="seat-select" value={seat} onChange={(event) => setSeat(event.target.value)}>
                {"ABCD".split("").map((value) => <option key={value}>{value}</option>)}
              </select>
            </label>
            <button data-testid="join-room" type="submit">Take seat</button>
            <button data-testid="watch-room" className="quiet-button" type="button" disabled={!invite.trim()} onClick={watch}>Watch only</button>
          </form>
        </section>
      </div>
      {error && <div className="error-banner" role="alert">{error}</div>}
    </main>
  );
}

function RoomView({
  guest,
  initial,
  invite,
  onGame,
  onRoom,
  onLeave,
}: {
  guest: Guest;
  initial: Room;
  invite: string;
  onGame: (gameId: string) => void;
  onRoom: (room: Room, invite?: string) => void;
  onLeave: () => void;
}) {
  const [room, setRoom] = useState(initial);
  const [currentInvite, setCurrentInvite] = useState(invite);
  const [name, setName] = useState("My Commander deck");
  const [commander, setCommander] = useState("");
  const [decklist, setDecklist] = useState("");
  const [sourceUrl, setSourceUrl] = useState("");
  const [message, setMessage] = useState("");
  const [messageKind, setMessageKind] = useState<"success" | "warning" | "error">("success");
  const [busy, setBusy] = useState(false);
  const [legalityReview, setLegalityReview] = useState<LegalityConfirmationRequired | null>(null);
  const [nextPlayerCount, setNextPlayerCount] = useState<2 | 4>(initial.seat_count);
  const mine = room.seats.find((seat) => seat.mine);
  const owner = room.owner_guest_id === guest.guest_id;
  const readyCount = room.seats.filter((seat) => seat.ready).length;

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
        sourceUrl.trim() ? "" : decklist,
        sourceUrl.trim() || undefined,
        legalityConfirmation,
      );
      setLegalityReview(null);
      const preview = result.format_legality.status === "preview_override_confirmed";
      if (!result.preflight.trusted_only_ready) {
        setMessage("Deck accepted with semantic fidelity warnings. Unsupported card behavior will still fail closed during play.");
        setMessageKind("warning");
      } else if (preview) {
        setMessage("Preview-card legality warning confirmed. The exact override is recorded with this list.");
        setMessageKind("warning");
      } else {
        setMessage("Deck validated: trusted-only semantic gate passes.");
        setMessageKind("success");
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
      setLegalityReview(null);
      setMessageKind("success");
      setMessage("You are unready. Update or replace your deck, then validate it again.");
    } catch (caught) {
      setMessageKind("error");
      setMessage(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setBusy(false);
    }
  }

  async function start() {
    setBusy(true);
    try {
      const result = await api.start(room.room_id);
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
          <div className="eyebrow">{room.seat_count === 2 ? "COMMANDER DUEL" : "COMMANDER MULTIPLAYER"} · 40 LIFE</div>
          <h1>Room {room.room_id.slice(0, 8)}</h1>
        </div>
        <div className="room-header-actions">
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
                <option value={4}>4 players</option>
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
            <span className={`seat-state ${seat.ready ? "ready" : "waiting"}`}><i aria-hidden="true" />{seat.ready ? "READY" : "WAITING"}</span>
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
      {!room.spectator && mine?.ready && mine.deck && (
        <section className="deck-ready-card" data-testid="deck-ready-summary">
          <div className="deck-ready-icon" aria-hidden="true">✓</div>
          <div>
            <div className="eyebrow">YOUR DECK · SEAT {mine.seat}</div>
            <h2>{mine.deck.name}</h2>
            {mine.deck.format_legality.status === "preview_override_confirmed" ? (
              <p><span className="preview-chip">PREVIEW OVERRIDE</span> {mine.deck.format_legality.issue_count} not-yet-legal card{mine.deck.format_legality.issue_count === 1 ? "" : "s"} explicitly confirmed. Images are cached locally.</p>
            ) : (
              <p>Validated, fingerprinted, and ready. Card images are cached locally in the background.</p>
            )}
          </div>
          <div className="deck-ready-actions">
            <code title="Exact deck-list fingerprint">{mine.deck.deck_list_fingerprint.slice(0, 12)}</code>
            <button type="button" className="quiet-button" data-testid="unready-deck" disabled={busy} onClick={unready}>Change deck / Unready</button>
          </div>
        </section>
      )}
      {!room.spectator && !mine?.ready && (
        <section className="panel deck-panel">
          <div>
            <div className="eyebrow">SEAT {mine?.seat}</div>
            <h2>Validate your deck</h2>
            <p>The server resolves names against the pinned card database, checks Commander legality, and runs semantic preflight before marking you ready.</p>
          </div>
          <form onSubmit={upload} className="deck-form">
            <label>Deck name<input data-testid="deck-name" value={name} onChange={(event) => setName(event.target.value)} required /></label>
            <label>Commander (required for pasted text)<input data-testid="commander-name" value={commander} onChange={(event) => setCommander(event.target.value)} required={!sourceUrl.trim()} /></label>
            <label className="wide">Public Moxfield URL<input data-testid="deck-source-url" value={sourceUrl} onChange={(event) => setSourceUrl(event.target.value)} placeholder="https://moxfield.com/decks/…" /></label>
            <div className="import-divider wide"><span>or paste a list</span></div>
            <label className="wide">Deck list<textarea data-testid="deck-list" value={decklist} onChange={(event) => setDecklist(event.target.value)} rows={12} required={!sourceUrl.trim()} /></label>
            <button data-testid="submit-deck" disabled={busy} type="submit">{busy ? "Validating…" : "Validate and ready"}</button>
          </form>
        </section>
      )}
      {!room.spectator && !mine?.ready && legalityReview && (
        <section className="preview-review" role="alert" data-testid="legality-confirmation">
          <div>
            <div className="eyebrow">PREVIEW CARD WARNING</div>
            <h2>These cards are not Commander-legal yet.</h2>
            <p>They exist in the current Scryfall snapshot but have future release dates. Confirming records an override for this exact deck fingerprint; it does not waive rules-semantic failures.</p>
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
            <strong>{readyCount}/{room.seat_count} decks ready</strong>
            <span>{readyCount === room.seat_count ? "Your table is ready to begin." : "Waiting for every seat to validate a deck."}</span>
          </div>
          <div className="readiness-meter" aria-label={`${readyCount} of ${room.seat_count} decks ready`}><span style={{ width: `${readyCount / room.seat_count * 100}%` }} /></div>
          <button data-testid="start-game" disabled={busy || !room.seats.every((seat) => seat.ready)} onClick={start}>{room.seat_count === 2 ? "Start duel" : "Start game"}</button>
        </div>
      )}
      {message && <div className={`${messageKind}-banner`} role={messageKind === "error" ? "alert" : "status"}>{message}</div>}
    </main>
  );
}

function PlayerBoard({
  seat,
  player,
  active,
  priority,
  mine,
  view,
  manualMana,
  cardActions,
  onCardIntent,
  onManaIntent,
  onInspectCard,
  selectedCardRef,
  onOpenZone,
  dropEnabled,
  onCardDrop,
  onCardDrag,
  getDraggedCard,
  dragRelease,
}: {
  seat: string;
  player: Record<string, JsonValue>;
  active: boolean;
  priority: boolean;
  mine: boolean;
  view: ProjectedView;
  manualMana: boolean;
  cardActions: LegalAction[];
  onCardIntent: (actions: LegalAction[], card: JsonValue) => void;
  onManaIntent: (actions: LegalAction[], card: JsonValue) => void;
  onInspectCard: (card: JsonValue) => void;
  selectedCardRef: string;
  onOpenZone: (seat: string, zone: "gy" | "ex") => void;
  dropEnabled: boolean;
  onCardDrop: (cardRef: string) => void;
  onCardDrag: (cardRef: string | null) => void;
  getDraggedCard: () => string;
  dragRelease: number;
}) {
  const [dragActive, setDragActive] = useState(false);
  useEffect(() => setDragActive(false), [dragRelease]);
  const battlefield = asList(player.bf);
  const command = asList(player.cmd);
  const graveyard = asList(player.gy);
  const exile = asList(player.ex);
  const mana = asRecord(player.mana);
  return (
    <article className={`player-board${active ? " active-player" : ""}${priority ? " has-priority" : ""}${mine ? " own-board" : ""}`} data-testid={`player-${seat}`}>
      <header>
        <span className="seat-letter small">{seat}</span>
        <div>
          <div className="player-name"><strong>Seat {seat}</strong>{mine && <span className="you-chip">You</span>}</div>
          <small>{String(player.hand_n ?? 0)} hand · {String(player.lib_n ?? 0)} library</small>
        </div>
        <div className="life" aria-label={`${String(player.life ?? 0)} life`}><span>{String(player.life ?? 0)}</span><small>LIFE</small></div>
      </header>
      {(active || priority) && <div className="turn-flags" aria-label={`Seat ${seat} turn status`}>{active && <span className="active-flag"><span aria-hidden="true">▶</span> Active turn</span>}{priority && <span className="priority-flag"><span aria-hidden="true">◎</span> Priority</span>}</div>}
      <CommanderDamage seat={seat} value={player.cmd_dmg ?? []} />
      <div className="zone-label">COMMAND</div>
      <div className="card-strip command-zone">{command.length ? command.map((card, index) => {
        const ref = String(asRecord(card).id ?? "");
        const actions = mine
          ? cardActions.filter((action) => String(action.card ?? action.source ?? "") === ref)
          : [];
        return <CardTile key={ref || index} value={card} view={view} compact actions={actions} onIntent={onCardIntent} onInspect={onInspectCard} onDragCard={onCardDrag} onDropCard={onCardDrop} selected={selectedCardRef === ref} explainUnavailable={mine} />;
      }) : <em>Empty</em>}</div>
      <div className="zone-label">BATTLEFIELD</div>
      <div
        className={`card-strip battlefield${mine && dropEnabled ? " drop-target" : ""}${dragActive ? " drag-active" : ""}`}
        data-testid={mine ? "own-battlefield" : undefined}
        data-own-battlefield={mine ? "true" : undefined}
        onDragOver={(event) => {
          if (!mine || !dropEnabled) return;
          event.preventDefault();
          event.dataTransfer.dropEffect = "move";
        }}
        onDragEnter={(event) => {
          if (!mine || !dropEnabled) return;
          event.preventDefault();
          setDragActive(true);
        }}
        onDragLeave={(event) => {
          if (event.currentTarget.contains(event.relatedTarget as Node | null)) return;
          setDragActive(false);
        }}
        onDrop={(event: DragEvent<HTMLDivElement>) => {
          if (!mine || !dropEnabled) return;
          event.preventDefault();
          setDragActive(false);
          const ref = event.dataTransfer.getData("application/x-commander-card") || event.dataTransfer.getData("text/plain") || getDraggedCard();
          if (ref) onCardDrop(ref);
        }}
        onPointerUp={(event) => {
          if (!mine || !dropEnabled || event.button !== 0) return;
          const ref = getDraggedCard();
          if (ref) onCardDrop(ref);
        }}
      >
        {battlefield.length ? battlefield.map((card, index) => {
          const ref = String(asRecord(card).id ?? "");
          const actions = mine
            ? cardActions.filter((action) =>
                String(action.card ?? action.source ?? "") === ref
                && (action.mana_ability !== true || manualMana),
              )
            : [];
          const manaOnly = actions.length > 0 && actions.every((action) =>
            action.mana_ability === true || action.action === "undo_mana",
          );
          return (
            <div className="battlefield-card-slot" key={ref || index}>
              <CardTile value={card} view={view} table actions={actions} onIntent={manaOnly ? onManaIntent : onCardIntent} onInspect={onInspectCard} manualMana={manaOnly} selected={selectedCardRef === ref} />
            </div>
          );
        }) : <em>{mine && dropEnabled ? "Drop a playable card here" : "Empty battlefield"}</em>}
      </div>
      <footer className="zone-summary">
        <button type="button" data-testid={`zone-${seat}-graveyard`} disabled={!graveyard.length} onClick={() => onOpenZone(seat, "gy")}>GY <strong>{graveyard.length}</strong></button>
        <button type="button" data-testid={`zone-${seat}-exile`} disabled={!exile.length} onClick={() => onOpenZone(seat, "ex")}>EXILE <strong>{exile.length}</strong></button>
        <span>LAND <strong>{String(player.lands ?? 0)}</strong></span>
        <span>MANA <strong>{Object.entries(mana).map(([color, amount]) => `${color}${amount}`).join(" ") || "—"}</strong></span>
      </footer>
    </article>
  );
}

function SetupScreen({ initial, onReady }: { initial: SystemStatus; onReady: () => void }) {
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
  const progress = data.phase === "building" ? 72 : data.phase === "downloading" ? 38 : data.phase === "checking" ? 18 : 8;
  return (
    <main className="setup-shell" data-testid="setup-screen">
      <section className="setup-card" aria-live="polite">
        <div className="brand-mark" aria-hidden="true">Q</div>
        <div>
          <div className="eyebrow">QUORUNE · FIRST-RUN SETUP</div>
          <h1>Preparing your card library.</h1>
          <p>{data.detail}</p>
        </div>
        <div className="setup-progress" role="progressbar" aria-label="Card library setup" aria-valuemin={0} aria-valuemax={100} aria-valuenow={progress}>
          <span style={{ width: `${progress}%` }} />
        </div>
        <dl className="setup-facts">
          <div><dt>Server</dt><dd><span className="status-dot" /> Running</dd></div>
          <div><dt>Card data</dt><dd>{readable(data.phase)}</dd></div>
          <div><dt>Images</dt><dd>Local cache · {status.images.downloaded}</dd></div>
        </dl>
        {data.last_error && <div className="error-banner" role="alert">{data.last_error}</div>}
        {data.phase === "error" && data.automatic_updates && (
          <button type="button" onClick={retry} disabled={retrying}>{retrying ? "Retrying…" : "Retry setup"}</button>
        )}
        <small>Initial setup downloads compressed Scryfall bulk data once. Future checks run every 24 hours.</small>
      </section>
    </main>
  );
}

function GameView({ gameId, onExit }: { gameId: string; onExit: () => void }) {
  const [view, setView] = useState<ProjectedView | null>(null);
  const viewRef = useRef<ProjectedView | null>(null);
  const ingestChain = useRef(Promise.resolve());
  const [connection, setConnection] = useState("CONNECTING");
  const [reconnectNonce, setReconnectNonce] = useState(0);
  const [reconnectAttempts, setReconnectAttempts] = useState(0);
  const [notice, setNotice] = useState("");
  const [selectedAction, setSelectedAction] = useState<LegalAction | null>(null);
  const [actionChoices, setActionChoices] = useState<LegalAction[]>([]);
  const [tablePreferences, setTablePreferences] = useState<TablePreferences>(loadTablePreferences);
  const [handManuallyCollapsed, setHandManuallyCollapsed] = useState(false);
  const [inspectedCard, setInspectedCard] = useState<JsonValue | null>(null);
  const [cardContext, setCardContext] = useState<JsonValue | null>(null);
  const [zoneBrowser, setZoneBrowser] = useState<{ seat: string; zone: "gy" | "ex" } | null>(null);
  const [expandedInspector, setExpandedInspector] = useState(false);
  const [choiceValues, setChoiceValues] = useState<ChoiceValues>({});
  const [choiceErrors, setChoiceErrors] = useState<string[]>([]);
  const [submitting, setSubmitting] = useState(false);
  const [lifecycle, setLifecycle] = useState<GameLifecycle | null>(null);
  const [stopReason, setStopReason] = useState("Pause for a table break");
  const [showInspection, setShowInspection] = useState(false);
  const [showPublicLog, setShowPublicLog] = useState(false);
  const [publicEvents, setPublicEvents] = useState<PublicGameEvent[]>([]);
  const [publicLogLoading, setPublicLogLoading] = useState(false);
  const [controlling, setControlling] = useState(false);
  const [pendingRetry, setPendingRetry] = useState<{ envelope: CommandEnvelope; label: string } | null>(null);
  const [dragRelease, setDragRelease] = useState(0);
  const [bottomDockRef, shellStyle] = useTableDockClearance();
  const choiceDialogRef = useRef<HTMLFormElement | null>(null);
  const actionPickerRef = useRef<HTMLElement | null>(null);
  const zoneDialogRef = useRef<HTMLElement | null>(null);
  const previewDialogRef = useRef<HTMLElement | null>(null);
  const publicLogDialogRef = useRef<HTMLElement | null>(null);
  const handPanelRef = useRef<HTMLElement | null>(null);
  const publicEventCursor = useRef(0);
  const publicLogChain = useRef(Promise.resolve());
  const draggedCardRef = useRef("");
  const dragOverOwnBattlefieldRef = useRef(false);
  const lastDroppedCardRef = useRef("");
  const cardDropHandlerRef = useRef<(cardRef: string) => void>(() => undefined);
  const autoPassDecisionRef = useRef("");
  const manualMana = !tablePreferences.autoMana;

  function refreshPublicLog(reset = false): Promise<void> {
    publicLogChain.current = publicLogChain.current
      .then(async () => {
        setPublicLogLoading(true);
        let after = reset ? 0 : publicEventCursor.current;
        const collected: PublicGameEvent[] = [];
        while (true) {
          const page = await api.events(gameId, after, 200);
          collected.push(...page.events);
          if (page.next_after <= after && page.has_more) {
            throw new Error("Public event log pagination did not advance");
          }
          after = page.next_after;
          if (!page.has_more) break;
        }
        publicEventCursor.current = after;
        setPublicEvents((current) => {
          const rows = reset ? collected : [...current, ...collected];
          return [...new Map(rows.map((event) => [event.id, event])).values()]
            .sort((left, right) => left.id - right.id);
        });
      })
      .catch((caught) => {
        setNotice(caught instanceof Error ? caught.message : String(caught));
      })
      .finally(() => setPublicLogLoading(false));
    return publicLogChain.current;
  }

  useEffect(() => saveTablePreferences(tablePreferences), [tablePreferences]);

  useEffect(() => {
    const overOwnBattlefield = (target: EventTarget | null) =>
      target instanceof Element && Boolean(target.closest("[data-own-battlefield='true']"));
    const beginCardDrag = (target: EventTarget | null) => {
      if (!(target instanceof Element)) return;
      const card = target.closest<HTMLElement>("[data-card-ref][draggable='true']");
      if (card?.dataset.cardRef) {
        draggedCardRef.current = card.dataset.cardRef;
      }
    };
    const finishCardDrag = (overBattlefield: boolean) => {
      const ref = draggedCardRef.current;
      if (!ref) {
        dragOverOwnBattlefieldRef.current = false;
        return;
      }
      if (ref && overBattlefield) cardDropHandlerRef.current(ref);
      dragOverOwnBattlefieldRef.current = false;
      setDragRelease((value) => value + 1);
      window.setTimeout(() => { draggedCardRef.current = ""; }, 0);
    };
    const trackNativeDrag = (event: globalThis.DragEvent) => {
      beginCardDrag(event.target);
      dragOverOwnBattlefieldRef.current = overOwnBattlefield(event.target);
    };
    const finishNativeDrop = (event: globalThis.DragEvent) => {
      finishCardDrag(overOwnBattlefield(event.target));
    };
    const finishNativeDrag = () => {
      finishCardDrag(dragOverOwnBattlefieldRef.current);
    };
    const finishPointerDrag = (event: PointerEvent) => {
      const target = document.elementFromPoint(event.clientX, event.clientY);
      finishCardDrag(overOwnBattlefield(target));
    };
    const beginPointerDrag = (event: PointerEvent) => {
      if (event.button === 0) beginCardDrag(event.target);
    };
    window.addEventListener("pointerdown", beginPointerDrag);
    window.addEventListener("dragover", trackNativeDrag);
    window.addEventListener("drop", finishNativeDrop);
    window.addEventListener("dragend", finishNativeDrag);
    window.addEventListener("pointerup", finishPointerDrag);
    return () => {
      window.removeEventListener("pointerdown", beginPointerDrag);
      window.removeEventListener("dragover", trackNativeDrag);
      window.removeEventListener("drop", finishNativeDrop);
      window.removeEventListener("dragend", finishNativeDrag);
      window.removeEventListener("pointerup", finishPointerDrag);
    };
  }, []);

  useEffect(() => {
    let stopped = false;
    let socket: WebSocket | null = null;
    let terminal = false;
    let retry = 250;
    let timer = 0;
    publicEventCursor.current = 0;
    setPublicEvents([]);
    function connect() {
      if (stopped) return;
      setConnection("CONNECTING");
      socket = new WebSocket(streamUrl(gameId), streamProtocols());
      socket.onopen = () => {
        retry = 250;
        setReconnectAttempts(0);
        setConnection("LIVE");
      };
      socket.onmessage = (event) => {
        const message = JSON.parse(String(event.data)) as {
          type: string;
          packet?: DecisionPacket;
          game?: GameLifecycle;
          message?: string;
        };
        if (message.type === "terminal") {
          terminal = true;
          setConnection("STOPPED");
          setNotice(message.message ?? "This game connection is no longer available.");
          socket?.close();
          return;
        }
        if (message.game) setLifecycle(message.game);
        if (message.type !== "projection" || !message.packet) return;
        ingestChain.current = ingestChain.current
          .then(async () => {
            const next = await ingestPacket(viewRef.current, message.packet!);
            viewRef.current = next;
            setView(next);
            void refreshPublicLog(false);
          })
          .catch((error) => {
            setNotice(error instanceof Error ? error.message : String(error));
            socket?.close();
          });
      };
      socket.onclose = () => {
        if (stopped || terminal) return;
        setConnection("RECONNECTING");
        setReconnectAttempts((value) => value + 1);
        timer = window.setTimeout(connect, retry);
        retry = Math.min(retry * 2, 5000);
      };
    }
    api.game(gameId)
      .then((result) => {
        setLifecycle(result.game);
        void refreshPublicLog(true);
      })
      .catch((caught) => setNotice(caught instanceof Error ? caught.message : String(caught)));
    connect();
    return () => {
      stopped = true;
      window.clearTimeout(timer);
      socket?.close();
      viewRef.current = null;
      publicEventCursor.current = 0;
    };
  }, [gameId, reconnectNonce]);

  useEffect(() => {
    setSelectedAction(null);
    setActionChoices([]);
    setCardContext(null);
    setChoiceValues({});
    setChoiceErrors([]);
  }, [view?.decision?.id]);

  useEffect(() => {
    if (pendingRetry && view?.decision?.id !== pendingRetry.envelope.decision_id) {
      setPendingRetry(null);
    }
  }, [pendingRetry, view?.decision?.id]);

  useEffect(() => {
    const decision = view?.decision;
    if (
      !tablePreferences.autoPass
      || !decision
      || decision.kind !== "priority"
      || lifecycle?.status !== "active"
      || connection !== "LIVE"
      || submitting
      || pendingRetry
      || selectedAction
      || actionChoices.length > 0
      || autoPassDecisionRef.current === decision.id
    ) return;
    const projectedState = asRecord(view?.state);
    const projectedTurn = asRecord(projectedState.turn);
    const principalSeat = view?.principal === "spectator"
      ? ""
      : view?.principal.split(":").at(-1) ?? "";
    const pass = findSafeAutoPass(decision.legal_actions, {
      activePlayer: String(projectedTurn.active ?? ""),
      phase: String(projectedTurn.phase ?? ""),
      principalSeat,
      stackDepth: asList(projectedState.stack).length,
    });
    if (!pass) return;
    autoPassDecisionRef.current = decision.id;
    void act(pass);
  }, [
    connection,
    lifecycle?.status,
    pendingRetry,
    selectedAction,
    actionChoices.length,
    submitting,
    tablePreferences.autoPass,
    view?.decision?.id,
    view?.viewRevision,
  ]);

  useEffect(() => {
    if (!selectedAction && actionChoices.length === 0 && !zoneBrowser && !expandedInspector && !showPublicLog) return;
    const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const oldOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    window.requestAnimationFrame(() => {
      const dialog = choiceDialogRef.current ?? actionPickerRef.current ?? zoneDialogRef.current ?? previewDialogRef.current ?? publicLogDialogRef.current;
      dialog?.querySelector<HTMLElement>("input:not(:disabled), select:not(:disabled), button:not(:disabled)")?.focus();
    });
    function keydown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        event.preventDefault();
        setSelectedAction(null);
        setActionChoices([]);
        setZoneBrowser(null);
        setExpandedInspector(false);
        setShowPublicLog(false);
        return;
      }
      const dialog = choiceDialogRef.current ?? actionPickerRef.current ?? zoneDialogRef.current ?? previewDialogRef.current ?? publicLogDialogRef.current;
      if (event.key !== "Tab" || !dialog) return;
      const controls = [...dialog.querySelectorAll<HTMLElement>("input:not(:disabled), select:not(:disabled), textarea:not(:disabled), button:not(:disabled), [tabindex]:not([tabindex='-1'])")];
      if (!controls.length) return;
      const first = controls[0];
      const last = controls.at(-1)!;
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    }
    document.addEventListener("keydown", keydown);
    return () => {
      document.removeEventListener("keydown", keydown);
      document.body.style.overflow = oldOverflow;
      previous?.focus();
    };
  }, [selectedAction, actionChoices, zoneBrowser, expandedInspector, showPublicLog]);

  async function submitEnvelope(envelope: CommandEnvelope, label: string) {
    setSubmitting(true);
    try {
      const result = await api.command(gameId, envelope);
      setPendingRetry(null);
      setNotice(result.receipt.summary);
      if (!result.receipt.ok) {
        setNotice(
          ["stale_decision", "stale_view", "stale_action"].includes(result.receipt.code)
            ? "That action expired because the authoritative decision window changed. Review the refreshed legal actions and try again."
            : `${result.receipt.code}: ${result.receipt.summary}`,
        );
      } else {
        setSelectedAction(null);
        setChoiceValues({});
        setChoiceErrors([]);
      }
    } catch (caught) {
      setPendingRetry({ envelope, label });
      setNotice(`Delivery uncertain: ${caught instanceof Error ? caught.message : String(caught)}`);
    } finally {
      setSubmitting(false);
    }
  }

  async function act(action: LegalAction, choices: ChoiceValues = {}) {
    if (!view?.decision || lifecycle?.status !== "active" || pendingRetry) return;
    await submitEnvelope(
      {
        protocol_version: "3.0",
        game_id: gameId,
        command_id: `web-${crypto.randomUUID()}`,
        decision_id: view.decision.id,
        action_id: action.id,
        capability: view.decision.cap,
        expected_view_revision: view.viewRevision,
        choices,
      },
      action.label ?? action.action,
    );
  }

  async function stopMatch() {
    setControlling(true);
    try {
      const result = await api.stop(gameId, stopReason.trim());
      setLifecycle(result.game);
      setNotice("Match stopped. The authoritative record is saved and resumable.");
    } catch (caught) {
      setNotice(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setControlling(false);
    }
  }

  async function resumeMatch() {
    setControlling(true);
    try {
      const result = await api.resume(gameId);
      setLifecycle(result.game);
      setNotice("Match resumed from the preserved decision boundary.");
    } catch (caught) {
      setNotice(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setControlling(false);
    }
  }

  function chooseAction(action: LegalAction) {
    setActionChoices([]);
    setCardContext(null);
    if (!action.form && action.action !== "cast") {
      void act(action);
      return;
    }
    setSelectedAction(action);
    setChoiceValues(action.form ? initialChoices(action.form) : {});
    setChoiceErrors([]);
  }

  function chooseCardAction(actions: LegalAction[], card?: JsonValue) {
    if (card) setInspectedCard(card);
    if (actions.length === 1) {
      chooseAction(actions[0]);
    } else if (actions.length > 1) {
      setActionChoices(actions);
    }
  }

  function selectCardActions(_actions: LegalAction[], card: JsonValue) {
    setInspectedCard(card);
    setCardContext(card);
    setActionChoices([]);
  }

  function projectedUnavailableMessage(ref: string): string {
    const projectedState = asRecord(viewRef.current?.state);
    const explanations = asRecord(projectedState.action_explanations);
    const explanation = asRecord(explanations[ref]);
    return typeof explanation.message === "string"
      ? explanation.message
      : "No server-authorized action is available for this card in the current decision window.";
  }

  function handleCardDrop(ref: string) {
    if (lastDroppedCardRef.current === ref) return;
    lastDroppedCardRef.current = ref;
    window.setTimeout(() => {
      if (lastDroppedCardRef.current === ref) lastDroppedCardRef.current = "";
    }, 250);
    draggedCardRef.current = "";
    const actions = actionsForCard(ref).filter((action) =>
      ["play_land", "cast"].includes(action.action),
    );
    if (!actions.length) {
      setNotice(projectedUnavailableMessage(ref));
      return;
    }
    chooseCardAction(actions);
  }
  cardDropHandlerRef.current = handleCardDrop;
  function setTablePreference<K extends keyof TablePreferences>(
    key: K,
    value: TablePreferences[K],
  ) {
    if (key === "autoPass" && value) autoPassDecisionRef.current = "";
    setTablePreferences((current) => ({ ...current, [key]: value }));
  }

  function resetTablePreferences() {
    autoPassDecisionRef.current = "";
    setHandManuallyCollapsed(false);
    setTablePreferences({
      ...DEFAULT_TABLE_PREFERENCES,
      rightRailOrder: [...DEFAULT_TABLE_PREFERENCES.rightRailOrder],
    });
  }

  function submitChoice(event: FormEvent) {
    event.preventDefault();
    if (!selectedAction) return;
    const errors = selectedAction.form
      ? validateChoices(selectedAction.form, choiceValues)
      : [];
    setChoiceErrors(errors);
    if (errors.length) return;
    void act(
      selectedAction,
      selectedAction.form
        ? executableChoices(selectedAction.form, choiceValues)
        : {},
    );
  }

  if (!view) return <main className="loading-screen"><div className="spinner" /><p>Opening your projected table…</p>{notice && <div className="error-banner">{notice}</div>}</main>;
  const currentView = view;
  const state = view.state;
  const game = asRecord(state.game);
  const turn = asRecord(state.turn);
  const combat = asRecord(state.combat);
  const players = asRecord(state.players);
  const isSpectator = view.principal === "spectator" || lifecycle?.spectator === true;
  const ownSeat = isSpectator ? "" : view.principal.split(":").at(-1) ?? "?";
  const ownPlayer = asRecord(players[ownSeat]);
  const hand = asList(ownPlayer.hand);
  const ownCommand = asList(ownPlayer.cmd);
  const inspectionTarget = inspectedCard ?? hand[0] ?? null;
  const stack = asList(state.stack);
  const labels = projectedLabels(view);
  const labelFor = (value: string) => labels.get(value) ?? value;
  const legalActions = view.decision?.legal_actions ?? [];
  const cardActions = legalActions.filter((action) =>
    typeof (action.card ?? action.source) === "string",
  );
  const manaActions = legalActions.filter((action) =>
    action.action === "activate" && action.mana_ability === true,
  );
  const manaUndoActions = legalActions.filter((action) =>
    action.action === "undo_mana",
  );
  const displayActions = legalActions.filter((action) =>
    action.mana_ability !== true || manualMana,
  );
  const actionsForCard = (ref: string) => cardActions.filter((action) =>
    String(action.card ?? action.source ?? "") === ref
    && (action.mana_ability !== true || manualMana),
  );
  const dropEnabled = !isSpectator && [...hand, ...ownCommand].some((card) =>
    actionsForCard(String(asRecord(card).id ?? "")).some((action) => ["play_land", "cast"].includes(action.action)),
  );
  const activeSeat = String(turn.active ?? "");
  const prioritySeat = String(turn.priority ?? "");
  const actionWindow = { activeSeat, ownSeat, phase: String(turn.phase ?? ""), stackDepth: stack.length };
  const selectedCardRef = String(asRecord(cardContext ?? undefined).id ?? "");
  const contextualActions = selectedCardRef ? actionsForCard(selectedCardRef) : [];
  const actionExplanations = asRecord(state.action_explanations);
  const selectedExplanation = asRecord(actionExplanations[selectedCardRef]);
  const zonePlayer = zoneBrowser ? asRecord(players[zoneBrowser.seat]) : {};
  const zoneCards = zoneBrowser ? asList(zonePlayer[zoneBrowser.zone]) : [];
  const zoneName = zoneBrowser?.zone === "gy" ? "Graveyard" : "Exile";
  const handCollapsed = handManuallyCollapsed || (
    tablePreferences.handAutoCollapse && hand.length === 0
  );
  const tableStyle = {
    ...(shellStyle ?? {}),
    "--hand-panel-height": `${tablePreferences.handPanelHeight}px`,
    "--card-scale": String(tablePreferences.cardScale),
    "--right-rail-width": `${tablePreferences.rightRailWidth}px`,
  } as CSSProperties;
  const turnPresentation = {
    turnSequence: Number(turn.seq ?? 0),
    activeSeat,
    prioritySeat,
    phase: String(turn.phase ?? ""),
    step: String(turn.step ?? ""),
    combatDamageStep: Number(combat.damage_step ?? 0),
    firstStrikeStep: Boolean(combat.first_strike_step),
    lifecycleStatus: lifecycle?.status ?? "loading",
  };

  function railPanel(panel: RightRailPanel) {
    if (panel === "stack") {
      return (
        <section className="stack-panel" key={panel} data-testid="stack-panel">
          <header><div className="zone-label">STACK</div><strong>{stack.length}</strong></header>
          <div className="stack-items">
            {stack.length ? stack.map((item, index) => <CardTile key={String(asRecord(item).id ?? index)} value={item} view={currentView} compact onInspect={setInspectedCard} />) : <em>The stack is empty</em>}
          </div>
        </section>
      );
    }
    if (panel === "activity") {
      if (!tablePreferences.activityVisible) return null;
      return (
        <section className="activity-panel" key={panel} data-testid="activity-panel">
          <header><div className="zone-label">RECENT ACTIVITY</div><button type="button" className="link-button" onClick={() => setShowPublicLog(true)}>Full log</button></header>
          <ol>
            {publicEvents.slice(-5).reverse().map((event) => <li key={event.id}><span>{event.actor ? `Seat ${event.actor}` : "Game"}</span>{event.summary || readable(event.code)}</li>)}
            {!publicEvents.length && <li className="empty-activity">Game events will appear here.</li>}
          </ol>
        </section>
      );
    }
    if (tablePreferences.inspectorCollapsed) {
      return (
        <section className="inspector-collapsed" key={panel} data-testid="card-inspector-collapsed">
          <span>Card viewer collapsed</span>
          <button type="button" className="link-button" onClick={() => setTablePreference("inspectorCollapsed", false)}>Open</button>
        </section>
      );
    }
    return <CardInspector key={panel} value={inspectionTarget} view={currentView} onExpand={() => setExpandedInspector(true)} />;
  }
  return (
    <main
      className={`game-shell${isSpectator ? " spectator-table" : ""} density-${tablePreferences.boardDensity}`}
      data-game-id={String(game.id ?? gameId)}
      data-view-revision={view.viewRevision}
      data-state-revision={lifecycle?.state_revision ?? -1}
      data-command-count={lifecycle?.commands ?? 0}
      data-event-count={lifecycle?.events ?? 0}
      data-lifecycle-status={lifecycle?.status ?? "loading"}
      data-phase={String(turn.phase ?? lifecycle?.phase ?? "")}
      data-step={String(turn.step ?? lifecycle?.step ?? "")}
      data-active-player={activeSeat}
      data-priority-player={prioritySeat}
      data-latest-event-id={publicEvents.at(-1)?.id ?? 0}
      data-latest-event-code={publicEvents.at(-1)?.code ?? ""}
      data-last-persistence-ms={Math.round((lifecycle?.persistence.last_total_seconds ?? 0) * 1000)}
      data-last-derived-review-ms={Math.round((lifecycle?.persistence.last_derived_review_seconds ?? 0) * 1000)}
      style={tableStyle}
    >
      {isSpectator && <div className="watch-mode-banner" data-testid="watch-mode"><strong>WATCH MODE</strong><span>Public table and game log · no player controls</span></div>}
      {lifecycle?.status !== "complete" && <a className="skip-link" href="#decision-tray">Skip to current actions</a>}
      <header className="game-topbar">
        <div className="connection-group">
          <span className={`connection ${connection.toLowerCase()}`} />
          <span>{connection}</span>
          {connection !== "LIVE" && connection !== "STOPPED" && <button type="button" className="link-button" onClick={() => setReconnectNonce((value) => value + 1)}>Retry now</button>}
          {connection === "STOPPED" && <button type="button" className="link-button" onClick={onExit}>Return to lobby</button>}
        </div>
        <div className="game-identity"><small>{lifecycle?.format_profile === "commander_duel" ? "COMMANDER DUEL" : "COMMANDER POD"}</small><strong>{String(game.id ?? gameId).slice(0, 8)}</strong></div>
        <div className="game-status-line">
          <span data-testid="game-status" className={`game-status ${lifecycle?.status ?? "loading"}`}>
            {(lifecycle?.status ?? "loading").toUpperCase()}
          </span>
        </div>
      </header>
      <TurnStatus value={turnPresentation} compact={tablePreferences.compactPhaseRail} />
      {connection !== "LIVE" && (
        <div className="connection-banner" role="status">
          <div><strong>{connection === "STOPPED" ? "This game tab is stale" : "Restoring your seat projection"}</strong><span>{connection === "STOPPED" ? "Return to the lobby instead of repeatedly reconnecting to an inaccessible game." : "No actions are sent while the live table connection is unavailable."}</span></div>
          <span>Attempt {Math.max(1, reconnectAttempts)}</span>
        </div>
      )}
      <section className="operations-panel" aria-label="Match operations">
        <div className="operations-heading"><span className="eyebrow">{isSpectator ? "TABLE VIEW" : "TABLE CONTROLS"}</span><strong>{isSpectator ? "Spectator" : `Seat ${ownSeat}`}</strong></div>
        {!isSpectator && lifecycle?.status !== "complete" && (
          <AutomationControls
            autoPass={tablePreferences.autoPass}
            autoMana={tablePreferences.autoMana}
            onAutoPass={() => setTablePreference("autoPass", !tablePreferences.autoPass)}
            onAutoMana={() => setTablePreference("autoMana", !tablePreferences.autoMana)}
          />
        )}
        <button
          type="button"
          className="secondary-button"
          data-testid="inspect-game"
          aria-expanded={showInspection}
          onClick={() => setShowInspection((value) => !value)}
        >Inspect match</button>
        <button type="button" className="secondary-button" data-testid="open-public-log" onClick={() => setShowPublicLog(true)}>Public log</button>
        <TableSettings value={tablePreferences} onChange={setTablePreference} onReset={resetTablePreferences} />
        {lifecycle?.owner && lifecycle.can_stop && (
          <label className="stop-control">
            Stop reason
            <input
              data-testid="stop-reason"
              value={stopReason}
              maxLength={500}
              onChange={(event) => setStopReason(event.target.value)}
            />
            <button
              type="button"
              data-testid="stop-game"
              disabled={controlling || !stopReason.trim()}
              onClick={stopMatch}
            >Stop match</button>
          </label>
        )}
        {lifecycle?.owner && lifecycle.can_resume && (
          <button
            type="button"
            data-testid="resume-game"
            disabled={controlling}
            onClick={resumeMatch}
          >Resume match</button>
        )}
      </section>
      {showInspection && lifecycle && (
        <section className="inspection-panel" data-testid="game-inspection">
          <h2>Match record</h2>
          <dl>
            <div><dt>Status</dt><dd>{lifecycle.status}</dd></div>
            <div><dt>Revision</dt><dd>{lifecycle.state_revision}</dd></div>
            <div><dt>Commands</dt><dd>{lifecycle.commands}</dd></div>
            <div><dt>Decisions</dt><dd>{lifecycle.decisions}</dd></div>
            <div><dt>Events</dt><dd>{lifecycle.events}</dd></div>
            <div><dt>Pending seats</dt><dd>{lifecycle.pending_principals.map((value) => value.split(":").at(-1)).join(", ") || "None"}</dd></div>
          </dl>
        </section>
      )}
      {lifecycle?.status === "paused" && (
        <div className="paused-banner" role="status" data-testid="paused-banner">
          <strong>{lifecycle.pause_reason?.kind === "administrative_stop" ? "Match stopped" : "Rules boundary reached"}</strong>
          <span>{lifecycle.pause_reason?.label ?? "Waiting for the room owner to resume."}</span>
        </div>
      )}
      {lifecycle?.status === "complete" && (
        <div className="game-over-banner" role="status" data-testid="game-over-banner">
          <strong>Game complete</strong>
          <span>
            {lifecycle.draw
              ? "The game ended in a draw."
              : lifecycle.winner
                ? `Seat ${lifecycle.winner} wins.`
                : "The authoritative game record is complete."}
          </span>
        </div>
      )}
      <div className="table-workspace">
        <section className="boards-grid" aria-label="Commander battlefield">
          {Object.entries(players).map(([seat, player]) => (
            <PlayerBoard
              key={seat}
              seat={seat}
              player={asRecord(player)}
              active={activeSeat === seat}
              priority={prioritySeat === seat}
              mine={ownSeat === seat}
              view={view}
              manualMana={manualMana}
              cardActions={cardActions}
              onCardIntent={selectCardActions}
              onManaIntent={chooseCardAction}
              onInspectCard={setInspectedCard}
              selectedCardRef={selectedCardRef}
              onOpenZone={(nextSeat, zone) => {
                const nextCards = asList(asRecord(players[nextSeat])[zone]);
                setInspectedCard(nextCards[0] ?? null);
                setZoneBrowser({ seat: nextSeat, zone });
              }}
              dropEnabled={dropEnabled}
              onCardDrop={handleCardDrop}
              onCardDrag={(ref) => { draggedCardRef.current = ref ?? ""; }}
              getDraggedCard={() => draggedCardRef.current}
              dragRelease={dragRelease}
            />
          ))}
        </section>
        <aside className="table-sidebar" aria-label="Stack and recent game activity">
          {tablePreferences.rightRailOrder.map(railPanel)}
        </aside>
      </div>
      <div ref={bottomDockRef} className={`table-bottom-dock${isSpectator ? " spectator" : ""}`} data-testid="table-bottom-dock">
      {!isSpectator && handCollapsed && (
        <section className="hand-panel-collapsed" data-testid="hand-panel-collapsed">
          <span>Your hand · {hand.length} cards</span>
          <button type="button" className="link-button" onClick={() => setHandManuallyCollapsed(false)}>Show hand</button>
        </section>
      )}
      {!isSpectator && !handCollapsed && <section ref={handPanelRef} className="hand-panel" data-testid="hand-panel" data-resizable="true" onPointerUp={() => {
        const height = handPanelRef.current?.getBoundingClientRect().height;
        if (height) setTablePreference("handPanelHeight", Math.round(height));
      }}>
        <header><div><span className="eyebrow">YOUR PRIVATE ZONE · SEAT {ownSeat}</span><h2>Your hand</h2></div><div className="hand-panel-controls"><span className="zone-count">{hand.length} cards</span><button type="button" className="link-button" onClick={() => setHandManuallyCollapsed(true)}>Collapse</button></div></header>
        {cardContext && (
          <div className="selected-card-actions" data-testid="selected-card-actions">
            <div>
              <span className="eyebrow">SELECTED CARD</span>
              <strong>{cardName(cardContext)}</strong>
              <small>{contextualActions.length ? "Choose a legal action, or drag the card to your battlefield for the fast path." : typeof selectedExplanation.message === "string" ? selectedExplanation.message : "No server-authorized action is available for this card in the current decision window."}</small>
            </div>
            <div className="selected-card-action-buttons">
              {contextualActions.map((action) => (
                <button type="button" key={action.id} disabled={submitting || Boolean(pendingRetry) || connection !== "LIVE" || lifecycle?.status !== "active"} onClick={() => chooseAction(action)}>
                  {action.action === "cast" && !manualMana ? `Auto-mana · ${action.label ?? "Cast"}` : action.label ?? action.action}
                </button>
              ))}
              <button type="button" className="secondary-button" onClick={() => setCardContext(null)}>Cancel</button>
            </div>
          </div>
        )}
        <div className="hand-cards" data-testid="own-hand">
          {hand.map((card, index) => {
            const ref = String(asRecord(card).id ?? "");
            return <CardTile key={ref || index} value={card} view={view} actions={actionsForCard(ref)} onIntent={selectCardActions} onInspect={setInspectedCard} onDragCard={(nextRef) => { draggedCardRef.current = nextRef ?? ""; }} onDropCard={handleCardDrop} selected={selectedCardRef === ref} explainUnavailable />;
          })}
        </div>
      </section>}
      {pendingRetry && (
        <section className="retry-panel" role="alert" data-testid="command-retry">
          <div><strong>Command delivery is uncertain</strong><span>Retrying “{pendingRetry.label}” will reuse command {pendingRetry.envelope.command_id} exactly.</span></div>
          <button type="button" disabled={submitting} onClick={() => void submitEnvelope(pendingRetry.envelope, pendingRetry.label)}>{submitting ? "Checking…" : "Retry exact command"}</button>
        </section>
      )}
      {manualMana && (manaActions.length > 0 || manaUndoActions.length > 0) && (
        <ManaHelp
          actions={manaUndoActions}
          disabled={submitting || Boolean(pendingRetry) || connection !== "LIVE" || lifecycle?.status !== "active"}
          onSelect={chooseAction}
        />
      )}
      {lifecycle?.status !== "complete" && <section
        id="decision-tray"
        className="decision-panel"
        data-testid="decision-panel"
        data-decision-id={view.decision?.id ?? ""}
        aria-live="polite"
      >
        {lifecycle?.status === "paused" ? (
          <div className="waiting-decision" data-testid="paused-decision"><span className="status-dot muted" /><div><strong>Match paused</strong><p>No player action or priority pass is pending while this boundary is reviewed.</p></div></div>
        ) : isSpectator ? (
          <div className="waiting-decision"><span className="status-dot muted" /><div><strong>Watching the table</strong><p>Player decisions and private information remain seat-scoped.</p></div></div>
        ) : view.decision ? (
          <>
            <div className="decision-copy"><span className="eyebrow">YOUR DECISION · {view.decision.id}</span><h2>{readable(view.decision.kind)}</h2><small>{displayActions.length} shown · {view.decision.legal_actions.length} legal</small></div>
            <div className="action-tools">
              {manaActions.length > 0 && (
                <button
                  type="button"
                  className={`mana-toggle${manualMana ? " active" : ""}`}
                  aria-pressed={manualMana}
                  data-testid="manual-mana-toggle"
                  onClick={() => setTablePreference("autoMana", manualMana)}
                >
                  {manualMana ? "Manual mana on" : "Manual mana"}
                </button>
              )}
              <div className="action-row">
                {displayActions.map((action) => (
                  <button className={`action-button ${actionTone(action)}`} key={action.id} data-testid={`action-${action.id}`} disabled={submitting || Boolean(pendingRetry) || connection !== "LIVE" || lifecycle?.status !== "active"} onClick={() => chooseAction(action)}>{action.action === "cast" && !manualMana ? `Auto-mana · ${action.label ?? "Cast"}` : visibleActionLabel(action, actionWindow)}</button>
                ))}
              </div>
            </div>
          </>
        ) : <div className="waiting-decision"><span className="status-dot muted" /><div><strong>Waiting for the table</strong><p>Waiting for another player’s decision.</p></div></div>}
      </section>}
      </div>
      {inspectionTarget && (
        <button type="button" className="mobile-inspector-trigger" onClick={() => setExpandedInspector(true)}>
          View {cardName(inspectionTarget)}
        </button>
      )}
      {showPublicLog && (
        <div className="choice-overlay" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) setShowPublicLog(false); }}>
          <section ref={publicLogDialogRef} className="choice-dialog public-log-dialog" role="dialog" aria-modal="true" aria-labelledby="public-log-title" data-testid="public-game-log">
            <header>
              <div><span className="eyebrow">PUBLIC MATCH RECORD</span><h2 id="public-log-title">Complete game log</h2><p>Every public event retained by the authoritative Game Record, in sequence.</p></div>
              <div className="dialog-actions">
                <button type="button" className="quiet-button" data-testid="refresh-public-log" disabled={publicLogLoading} onClick={() => void refreshPublicLog(false)}>{publicLogLoading ? "Refreshing…" : "Refresh"}</button>
                <button type="button" className="secondary-button" onClick={() => setShowPublicLog(false)}>Close</button>
              </div>
            </header>
            <ol className="public-log-list">
              {publicEvents.map((event) => (
                <li key={event.id} data-testid="public-log-entry">
                  <span>#{event.id}</span>
                  <div><strong>{event.actor ? `Seat ${event.actor}` : "Game"}</strong><p>{event.summary || readable(event.code)}</p><small>{readable(event.code)}</small></div>
                </li>
              ))}
              {!publicEvents.length && <li className="empty-activity">{publicLogLoading ? "Loading public events…" : "No public events have been recorded."}</li>}
            </ol>
          </section>
        </div>
      )}
      {zoneBrowser && (
        <div className="choice-overlay" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) setZoneBrowser(null); }}>
          <section ref={zoneDialogRef} className="choice-dialog zone-browser" role="dialog" aria-modal="true" aria-labelledby="zone-browser-title" data-testid="zone-browser">
            <header>
              <div><span className="eyebrow">PUBLIC ZONE · SEAT {zoneBrowser.seat}</span><h2 id="zone-browser-title">{zoneName} · {zoneCards.length}</h2></div>
              <button type="button" className="secondary-button" onClick={() => setZoneBrowser(null)}>Close</button>
            </header>
            <div className="zone-browser-layout">
              <div className="zone-card-grid">
                {zoneCards.map((card, index) => {
                  const ref = String(asRecord(card).id ?? "");
                  const actions = zoneBrowser.seat === ownSeat ? actionsForCard(ref) : [];
                  return (
                    <CardTile
                      key={ref || index}
                      value={card}
                      view={view}
                      actions={actions}
                      onInspect={setInspectedCard}
                      onIntent={(nextActions, selectedCard) => {
                        setZoneBrowser(null);
                        selectCardActions(nextActions, selectedCard);
                      }}
                      selected={selectedCardRef === ref}
                    />
                  );
                })}
              </div>
              <CardInspector value={inspectedCard ?? zoneCards[0] ?? null} view={view} expanded />
            </div>
          </section>
        </div>
      )}
      {expandedInspector && (
        <div className="choice-overlay" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) setExpandedInspector(false); }}>
          <section ref={previewDialogRef} className="choice-dialog card-preview-dialog" role="dialog" aria-modal="true" aria-label={`Card viewer: ${inspectionTarget ? cardName(inspectionTarget) : "No card selected"}`}>
            <header><div><span className="eyebrow">CARD VIEWER</span><h2>{inspectionTarget ? cardName(inspectionTarget) : "No card selected"}</h2></div><button type="button" className="secondary-button" onClick={() => setExpandedInspector(false)}>Close</button></header>
            <CardInspector value={inspectionTarget} view={view} expanded />
          </section>
        </div>
      )}
      {actionChoices.length > 0 && (
        <div className="choice-overlay" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) setActionChoices([]); }}>
          <section ref={actionPickerRef} className="choice-dialog action-picker" role="dialog" aria-modal="true" aria-labelledby="action-picker-title">
            <header>
              <div><span className="eyebrow">CHOOSE CARD ACTION</span><h2 id="action-picker-title">How should this card be used?</h2></div>
              <button type="button" className="secondary-button" onClick={() => setActionChoices([])}>Cancel</button>
            </header>
            <div className="action-picker-options">
              {actionChoices.map((action) => (
                <button type="button" key={action.id} onClick={() => chooseAction(action)}>
                  {action.action === "cast" && !manualMana ? `Auto-mana · ${action.label ?? "Cast"}` : action.label ?? action.action}
                </button>
              ))}
            </div>
          </section>
        </div>
      )}
      {selectedAction && (selectedAction.form || selectedAction.action === "cast") && (
        <div className="choice-overlay" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) setSelectedAction(null); }}>
          <form ref={choiceDialogRef} className="choice-dialog" role="dialog" aria-modal="true" aria-labelledby="choice-dialog-title" aria-describedby="choice-dialog-description" data-testid="choice-dialog" onSubmit={submitChoice}>
            <header>
              <div>
                <span className="eyebrow">SERVER-ISSUED CHOICES</span>
                <h2 id="choice-dialog-title">{selectedAction.label ?? selectedAction.action}</h2>
                <p id="choice-dialog-description">{selectedAction.action === "cast" ? manualMana ? "Spend the mana you floated in the order you chose. The server validates the pool and can complete any routine remainder." : "Use Auto-mana for a routine payment, or cancel and enable Manual mana to tap sources in your preferred order. The server validates every payment." : "Choose from the legal values supplied for your seat. The server validates the final action."}</p>
              </div>
              <button
                type="button"
                className="secondary-button"
                data-testid="cancel-choice"
                onClick={() => setSelectedAction(null)}
              >Cancel</button>
            </header>
            {selectedAction.form && (
              <ChoiceFormView
                form={selectedAction.form}
                values={choiceValues}
                onChange={(values) => {
                  setChoiceValues(values);
                  setChoiceErrors([]);
                }}
                labelFor={labelFor}
              />
            )}
            {choiceErrors.length > 0 && (
              <ul className="choice-errors" data-testid="choice-errors">
                {choiceErrors.map((error) => <li key={error}>{error}</li>)}
              </ul>
            )}
            <div className="choice-submit-row">
              {selectedAction.action === "cast" && !manualMana && (
                <button type="button" className="secondary-button" onClick={() => { setTablePreference("autoMana", false); setSelectedAction(null); setNotice("Manual mana enabled. Click highlighted mana sources, then choose the spell again."); }}>Use manual mana</button>
              )}
              <button type="submit" data-testid="submit-choice" disabled={submitting || connection !== "LIVE"}>
                {submitting ? "Submitting…" : selectedAction.action === "cast" ? manualMana ? selectedAction.label ?? "Cast" : `Auto-mana & ${selectedAction.label ?? "Cast"}` : selectedAction.form?.submit_label ?? "Submit"}
              </button>
            </div>
          </form>
        </div>
      )}
      {notice && <div className="toast" role="status" aria-live="polite">{notice}</div>}
    </main>
  );
}

export default function App() {
  const [screen, setScreen] = useState<Screen>("loading");
  const [system, setSystem] = useState<SystemStatus | null>(null);
  const [bootNonce, setBootNonce] = useState(0);
  const [guest, setGuest] = useState<Guest | null>(null);
  const [room, setRoom] = useState<Room | null>(null);
  const [invite, setInvite] = useState("");
  const [gameId, setGameId] = useState("");

  useEffect(() => {
    let stopped = false;
    async function boot() {
      try {
        const nextSystem = await api.system();
        if (stopped) return;
        setSystem(nextSystem);
        if (!nextSystem.card_data.ready) {
          setScreen("setup");
          return;
        }
        try {
          const result = await api.me();
          if (stopped) return;
          setGuest(result.guest);
          // Room restoration is tab-scoped for the same reason authentication
          // is: localStorage is shared by incognito windows.
          localStorage.removeItem("commander-room");
          const roomId = sessionStorage.getItem("commander-room");
          if (!roomId) {
            setScreen("lobby");
            return;
          }
          try {
            const saved = (await api.room(roomId)).room;
            if (stopped) return;
            setRoom(saved);
            setInvite(sessionStorage.getItem(inviteStorageKey(saved.room_id)) ?? "");
            if (saved.game_id) {
              setGameId(saved.game_id);
              setScreen("game");
            } else {
              setScreen("room");
            }
          } catch {
            sessionStorage.removeItem("commander-room");
            sessionStorage.removeItem(inviteStorageKey(roomId));
            setScreen("lobby");
          }
        } catch {
          setScreen("welcome");
        }
      } catch {
        setScreen("loading");
      }
    }
    void boot();
    return () => { stopped = true; };
  }, [bootNonce]);

  function enterRoom(next: Room, code = "") {
    setRoom(next);
    setInvite(code);
    sessionStorage.setItem("commander-room", next.room_id);
    if (code) sessionStorage.setItem(inviteStorageKey(next.room_id), code);
    setScreen("room");
  }

  function enterGame(id: string) {
    setGameId(id);
    setScreen("game");
  }

  function leaveRoomScreen() {
    if (room) sessionStorage.removeItem(inviteStorageKey(room.room_id));
    sessionStorage.removeItem("commander-room");
    setRoom(null);
    setInvite("");
    setScreen("lobby");
  }

  if (screen === "loading") return <main className="loading-screen"><div className="spinner" /></main>;
  if (screen === "setup" && system) return <SetupScreen initial={system} onReady={() => { setScreen("loading"); setBootNonce((value) => value + 1); }} />;
  if (screen === "welcome") return <Welcome onReady={(value) => { setGuest(value); setScreen("lobby"); }} />;
  if (!guest) return null;
  if (screen === "lobby") return <Lobby guest={guest} system={system} onRoom={enterRoom} />;
  if (screen === "room" && room) return <RoomView key={room.room_id} guest={guest} initial={room} invite={invite} onGame={enterGame} onRoom={enterRoom} onLeave={leaveRoomScreen} />;
  if (screen === "game" && gameId) return <GameView key={gameId} gameId={gameId} onExit={() => {
    sessionStorage.removeItem("commander-room");
    setRoom(null);
    setGameId("");
    setScreen("loading");
    setBootNonce((value) => value + 1);
  }} />;
  return null;
}
