import { FormEvent, useEffect, useRef, useState } from "react";
import {
  api,
  ApiError,
  type GameLifecycle,
  type Guest,
  type LegalityConfirmationRequired,
  type PublicGameEvent,
  type Room,
  type SavedDeck,
  type SystemStatus,
  streamUrl,
  streamProtocols,
} from "./api";
import { ChoiceFormView } from "./ChoiceForm";
import { DeckReadiness } from "./DeckReadiness";
import { ContextualDecision, contextualDecisionAction } from "./ContextualDecision";
import { projectedChoiceCards, projectedLabels } from "./CardChoiceGrid";
import {
  choiceCompatibilityErrors,
  executableChoices,
  initialChoices,
} from "./choices";
import type { DecisionPacket, JsonValue, LegalAction } from "./generated/protocol";
import { asList, asRecord, cardName, CardInspector, CardTile } from "./CardSurface";
import { ingestPacket, type ProjectedView } from "./protocol";
import { activationStopSignature, activeCombatPriorityStop, findSafeAutoPass, isIgnoredActivation, turnYieldPause, type CombatPriorityStop, type TurnYieldBoundary } from "./tableAutomation";
import { actionTitle, activationCostLabel, canMinimizeChoice, cardActionCost, choiceSubmitLabel, decisionDetail, decisionHeading, isRoutineCast, needsActivationConfirmation, phyrexianCastCostLabels, publicEventSummary, visibleActionLabel } from "./tablePresentation";
import { AutomationControls, ManaHelp } from "./TableStatus";
import { CardSizeSlider, TableSettings } from "./TableSettings";
import { CardPages } from "./CardPages";
import { CombatStopOptions, TurnStatus } from "./TurnStatus";
import { PlayerBoard } from "./PlayerBoard";
import { StackPanel } from "./StackPanel";
import { projectedCardSeat, SeatLabel, tableSeat } from "./SeatLabel";
import { GameMenu } from "./GameMenu";
import { SetupScreen } from "./SetupScreen";
import { RecentActivity } from "./RecentActivity";
import { Lobby, Welcome } from "./Lobby";
import type { SavedDeckSelection } from "./PlayLibrary";
import { handZoneOffers, type HandZoneOffer } from "./handZoneOffers";
import { HandRail } from "./HandRail";
import { ManaText } from "./ManaText";
import { useDecisionInteraction } from "./useDecisionInteraction";
import { PublicActionDraft, publicObjectSteps, stageTableObject, usesModeTargetDraft, type TableObjectSelection } from "./PublicActionDraft";
import { TargetArrowLayer, targetZones } from "./TargetArrowLayer";
import {
  DEFAULT_TABLE_PREFERENCES,
  tablePreferenceStyle,
  loadTablePreferences,
  saveTablePreferences,
  type OpponentView,
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

function RoomView({
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
  const [savedDecks, setSavedDecks] = useState<SavedDeck[]>([]);
  const [savedDeckId, setSavedDeckId] = useState(initialSavedDeck?.id ?? "");
  const [message, setMessage] = useState("");
  const [messageKind, setMessageKind] = useState<"success" | "warning" | "error">("success");
  const [busy, setBusy] = useState(false);
  const [legalityReview, setLegalityReview] = useState<LegalityConfirmationRequired | null>(null);
  const [nextPlayerCount, setNextPlayerCount] = useState<2 | 4>(initial.seat_count);
  const mine = room.seats.find((seat) => seat.mine);
  const owner = room.owner_guest_id === guest.guest_id;
  const readyCount = room.seats.filter((seat) => seat.ready).length;
  const reviewingDraft = !mine?.ready && Boolean(mine?.deck?.preflight?.data_integrity_issue);

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
      setMessage("Saved decks could not be loaded. You can still paste a list or use a Moxfield link.");
    });
    return () => { stopped = true; };
  }, [initialSavedDeck, room.spectator]);

  function chooseSavedDeck(id: string) {
    setSavedDeckId(id);
    const selected = savedDecks.find((deck) => deck.id === id);
    if (selected) setName(selected.name);
  }

  async function editSavedDeck(id: string) {
    setBusy(true);
    try {
      const { deck } = await api.savedDeck(id);
      const sections = { commander: "Commander", mainboard: "Mainboard", sideboard: "Sideboard", companion: "Companion", maybeboard: "Maybeboard" };
      const text = Object.entries(sections).flatMap(([board, title]) => {
        const entries = deck.entries.filter((entry) => entry.board === board);
        const lines = entries.map(({ quantity, name, printing }) => {
          const edition = printing?.set_code && printing.collector_number
            ? ` (${printing.set_code}) ${printing.collector_number}` : "";
          const finish = printing?.finish?.toLowerCase() === "foil" ? " *F*" : "";
          return `${quantity} ${name}${edition}${finish}`;
        });
        return lines.length ? [`${title}:\n${lines.join("\n")}`] : [];
      }).join("\n\n");
      setName(deck.name);
      setCommander(deck.commanders[0] ?? "");
      setDecklist(text);
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
        savedDeckId || sourceUrl.trim() ? "" : decklist,
        savedDeckId ? undefined : sourceUrl.trim() || undefined,
        legalityConfirmation,
        savedDeckId || undefined,
      );
      setLegalityReview(null);
      const preview = result.format_legality.status === "preview_override_confirmed";
      if (result.preflight.data_integrity_issue) {
        const { deck_id: id, name, commanders } = result.deck;
        setSavedDecks((decks) => [{ id, name, commanders }, ...decks.filter((deck) => deck.id !== id)]);
        setMessage("Draft saved. Review the card data before starting a game with this deck.");
        setMessageKind("warning");
      } else if (!result.preflight.trusted_only_ready) {
        setMessage("Deck accepted. Review the listed cards before you play.");
        setMessageKind("warning");
      } else if (preview) {
        setMessage("Deck validated with the preview cards you confirmed.");
        setMessageKind("warning");
      } else {
        setMessage("Deck validated and submitted.");
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
            <p>Choose a saved deck, import from Moxfield, or paste a list.</p>
          </div>
          <form onSubmit={upload} className="deck-form">
            {savedDecks.length > 0 && <label className="wide">Saved deck
              <select value={savedDeckId} onChange={(event) => chooseSavedDeck(event.target.value)}>
                <option value="">Use a pasted list or Moxfield link</option>
                {savedDecks.map((deck) => <option key={deck.id} value={deck.id}>{deck.name}</option>)}
              </select>
            </label>}
            <label>Deck name<input data-testid="deck-name" value={name} onChange={(event) => setName(event.target.value)} required /></label>
            {savedDeckId ? <button type="button" className="secondary-button" disabled={busy}
              onClick={() => void editSavedDeck(savedDeckId)}>Edit saved list</button> : <>
              <label>Commander (required for pasted text)<input data-testid="commander-name" value={commander} onChange={(event) => setCommander(event.target.value)} required={!sourceUrl.trim()} /></label>
              <label className="wide">Public Moxfield URL<input data-testid="deck-source-url" value={sourceUrl} onChange={(event) => setSourceUrl(event.target.value)} placeholder="https://moxfield.com/decks/…" /></label>
              <div className="import-divider wide"><span>or paste a list</span></div>
              <label className="wide">Deck list<textarea data-testid="deck-list" value={decklist} onChange={(event) => setDecklist(event.target.value)} rows={12} required={!sourceUrl.trim()} /></label>
            </>}
            <button data-testid="submit-deck" disabled={busy} type="submit">{busy ? "Checking deck…" : "Check and submit deck"}</button>
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
            <strong>{readyCount} of {room.seat_count} decks submitted</strong>
            <span>{readyCount === room.seat_count ? "You can start the game." : "Each player needs to submit a deck."}</span>
          </div>
          <div className="readiness-meter" aria-hidden="true"><span style={{ width: `${readyCount / room.seat_count * 100}%` }} /></div>
          <button data-testid="start-game" disabled={busy || !room.seats.every((seat) => seat.ready)} onClick={start}>{room.seat_count === 2 ? "Start duel" : "Start game"}</button>
        </div>
      )}
      {message && <div className={`${messageKind}-banner`} role={messageKind === "error" ? "alert" : "status"}>{message}</div>}
    </main>
  );
}

function GameView({ gameId, system, onExit, onRoom }: {
  gameId: string; system: SystemStatus | null; onExit: () => void;
  onRoom: (room: Room, invite?: string) => void;
}) {
  const [view, setView] = useState<ProjectedView | null>(null);
  const viewRef = useRef<ProjectedView | null>(null);
  const ingestChain = useRef(Promise.resolve());
  const [connection, setConnection] = useState("CONNECTING");
  const [reconnectNonce, setReconnectNonce] = useState(0);
  const [reconnectAttempts, setReconnectAttempts] = useState(0);
  const [notice, setNotice] = useState("");
  const [actionChoices, setActionChoices] = useState<LegalAction[]>([]);
  const choosingLoyaltyAbility = actionChoices.every((action) =>
    action.action === "activate" && typeof asRecord(action.cost_summary as JsonValue | undefined).loyalty === "number",
  );
  const [minimizedChoice, setMinimizedChoice] = useState<string | null>(null);
  const [tablePreferences, setTablePreferences] = useState<TablePreferences>(loadTablePreferences);
  const [compactBoards, setCompactBoards] = useState(() =>
    window.matchMedia("(max-height: 520px), (max-width: 650px)").matches);
  const [compactOwnBoard, setCompactOwnBoard] = useState(true);
  const [turnYield, setTurnYield] = useState<TurnYieldBoundary | null>(null);
  const [combatStops, setCombatStops] = useState<CombatPriorityStop[]>([]);
  const [ignoredStops, setIgnoredStops] = useState<{ turn: number; offers: ReadonlyMap<string, string> }>();
  const [handManuallyCollapsed, setHandManuallyCollapsed] = useState(false);
  const [inspectedCard, setInspectedCard] = useState<JsonValue | null>(null);
  const [focusedStackId, setFocusedStackId] = useState("");
  const [stackMinimized, setStackMinimized] = useState(false);
  const [railOpen, setRailOpen] = useState(false);
  const [cardContext, setCardContext] = useState<JsonValue | null>(null);
  const [publicStage, setPublicStage] = useState("");
  const [zoneBrowser, setZoneBrowser] = useState<{ seat: string; zone: "gy" | "ex" } | null>(null);
  const [pinnedCardRef, setPinnedCardRef] = useState<string | null>(null);
  const [lifecycle, setLifecycle] = useState<GameLifecycle | null>(null);
  const [stopReason, setStopReason] = useState("Pause for a table break");
  const [showInspection, setShowInspection] = useState(false);
  const [showPublicLog, setShowPublicLog] = useState(false);
  const [publicEvents, setPublicEvents] = useState<PublicGameEvent[]>([]);
  const [publicLogLoading, setPublicLogLoading] = useState(false);
  const [controlling, setControlling] = useState(false);
  const [preparingRematch, setPreparingRematch] = useState(false);
  const [dragRelease, setDragRelease] = useState(0);
  const [bottomDockRef, shellStyle] = useTableDockClearance();
  const choiceDialogRef = useRef<HTMLFormElement | null>(null);
  const actionPickerRef = useRef<HTMLElement | null>(null);
  const zoneDialogRef = useRef<HTMLElement | null>(null);
  const previewDialogRef = useRef<HTMLElement | null>(null);
  const publicLogDialogRef = useRef<HTMLElement | null>(null);
  const stackToggleRef = useRef<HTMLButtonElement | null>(null);
  const handPanelRef = useRef<HTMLElement | null>(null);
  const publicEventCursor = useRef(0);
  const publicLogCall = useRef<Promise<void> | null>(null);
  const queuedLogRefresh = useRef<boolean | null>(null);
  const draggedCardRef = useRef("");
  const dragOverOwnBattlefieldRef = useRef(false);
  const lastDroppedCardRef = useRef("");
  const cardDropHandlerRef = useRef<(cardRef: string) => void>(() => undefined);
  const autoPassDecisionRef = useRef("");
  const manualMana = !tablePreferences.autoMana;
  const {
    draft, openDraft, closeDraft, stageChoices, submitDraft, act, retry, lastMove,
    pendingRetry, submitting, awaitingProjection, parkedAction, parkDraft, resumeDraft,
    canResumeDraft, dismissParkedDraft,
  } = useDecisionInteraction({ view, gameId, enabled: connection === "LIVE" && lifecycle?.status === "active", onNotice: setNotice });
  const selectedAction = draft?.action ?? null;
  const choiceMinimized = Boolean(selectedAction && minimizedChoice === view?.decision?.id);
  const minimizeInsteadOfCancel = canMinimizeChoice(view?.decision);
  const currentChoiceDetail = view?.decision ? decisionDetail(view.decision) : "";
  const choiceValues = draft?.values ?? {};
  const stack = asList(view?.state.stack);
  const hasStack = stack.length > 0;
  const stackPositions = new Map(stack.map((item, index) => [String(asRecord(item).id), index + 1]));
  const publicSteps = view ? publicObjectSteps(selectedAction, view, choiceValues) : [];
  const currentStep = publicSteps.find((step) => step.id === publicStage) ?? publicSteps[0];
  const stackTargetStep = currentStep?.refs.some((ref) => stackPositions.has(ref)) ? currentStep.id : null;
  const compatibilityErrors = choiceCompatibilityErrors(selectedAction?.form, choiceValues);
  const commitBlocked = compatibilityErrors.length > 0;
  const choiceErrors = commitBlocked ? compatibilityErrors : draft?.errors ?? [];
  const projectedTurn = asRecord(view?.state.turn);
  const turnBoundary = {
    turn: Number(projectedTurn.seq ?? 0),
    phase: String(projectedTurn.phase ?? ""),
    stack: asList(view?.state.stack).map((item) => String(asRecord(item).id)),
  };
  const combatStop = view?.decision?.kind === "priority" ? activeCombatPriorityStop(
    turnBoundary.phase, String(projectedTurn.step ?? ""), Boolean(asRecord(view.state.combat).first_strike_step), combatStops,
  ) : undefined;
  function toggleCombatStop(stop: CombatPriorityStop) {
    setCombatStops((selected) => selected.includes(stop) ? selected.filter((value) => value !== stop) : [...selected, stop]);
  }
  const ignoredOffers = ignoredStops?.turn === turnBoundary.turn ? ignoredStops.offers : undefined;
  const passWindow = {
    activePlayer: String(projectedTurn.active ?? ""),
    phase: turnBoundary.phase,
    principalSeat: view?.principal.split(":").at(-1) ?? "",
    stackDepth: turnBoundary.stack.length,
    allowActiveMain: Boolean(turnYield),
    ignoredActivations: turnYield ? ignoredOffers : undefined,
  };
  const autoPass = findSafeAutoPass(view?.decision?.legal_actions ?? [], passWindow);
  const yieldPause = turnYield && (combatStop ? `Priority held · ${combatStop}`
    : turnYieldPause(turnYield, turnBoundary, view?.decision?.kind, Boolean(autoPass)));

  useEffect(() => {
    if (turnYield && turnYield.turn !== turnBoundary.turn) setTurnYield(null);
  }, [turnYield, turnBoundary.turn]);

  function refreshPublicLog(reset = false): Promise<void> {
    queuedLogRefresh.current = reset || queuedLogRefresh.current === true;
    if (publicLogCall.current) return publicLogCall.current;
    publicLogCall.current = (async () => {
      setPublicLogLoading(true);
      try {
        while (queuedLogRefresh.current !== null) {
          const reload = queuedLogRefresh.current;
          queuedLogRefresh.current = null;
          let after = reload ? 0 : publicEventCursor.current;
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
          setPublicEvents((current) => [...new Map(
            (reload ? collected : [...current, ...collected]).map((event) => [event.id, event]),
          ).values()].sort((left, right) => left.id - right.id));
        }
      } catch (caught) {
        setNotice(caught instanceof Error ? caught.message : String(caught));
      } finally {
        setPublicLogLoading(false);
        publicLogCall.current = null;
      }
    })();
    return publicLogCall.current;
  }

  useEffect(() => saveTablePreferences(tablePreferences), [tablePreferences]);
  useEffect(() => {
    const query = window.matchMedia("(max-height: 520px), (max-width: 650px)");
    const update = () => setCompactBoards(query.matches);
    query.addEventListener("change", update);
    return () => query.removeEventListener("change", update);
  }, []);

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
      const stream = new WebSocket(streamUrl(gameId), streamProtocols());
      let streamView: ProjectedView | null = null;
      socket = stream;
      stream.onopen = () => { if (!stopped && socket === stream) setConnection("SYNCING"); };
      stream.onmessage = (event) => {
        if (stopped || socket !== stream) return;
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
          stream.close();
          return;
        }
        if (message.game) setLifecycle(message.game);
        if (message.type !== "projection" || !message.packet) return;
        ingestChain.current = ingestChain.current
          .then(async () => {
            if (stopped || socket !== stream || stream.readyState !== WebSocket.OPEN) return;
            const next = await ingestPacket(streamView, message.packet!);
            if (stopped || socket !== stream || stream.readyState !== WebSocket.OPEN) return;
            if (viewRef.current && next.principal !== viewRef.current.principal) {
              throw new Error("The reconnected projection belongs to another player.");
            }
            const refreshEvents = !streamView || next.state.event !== streamView.state.event;
            if (!streamView) {
              retry = 250;
              setReconnectAttempts(0);
            }
            streamView = next;
            viewRef.current = next;
            setView(next);
            setConnection("LIVE");
            if (refreshEvents) void refreshPublicLog(false);
          })
          .catch((error) => {
            if (stopped || socket !== stream) return;
            setNotice(error instanceof Error ? error.message : String(error));
            stream.close();
          });
      };
      stream.onclose = () => {
        if (stopped || terminal || socket !== stream) return;
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
    setActionChoices([]);
    setCardContext(null);
  }, [view?.decision?.id]);
  useEffect(() => setFocusedStackId(""), [view?.viewRevision]);
  useEffect(() => {
    if (!hasStack || stackTargetStep) setStackMinimized(false);
  }, [hasStack, selectedAction?.id, stackTargetStep]);

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
      || parkedAction
      || actionChoices.length > 0
      || autoPassDecisionRef.current === decision.id
    ) return;
    if (!autoPass || yieldPause || combatStop) return;
    autoPassDecisionRef.current = decision.id;
    const ignored = decision.legal_actions.filter((row) => isIgnoredActivation(row, ignoredOffers)).map((row) => row.id);
    void act(autoPass, turnYield ? {
      pass_turn_sequence: turnBoundary.turn,
      ...(ignored.length > 0 ? { ignored_activation_ids: ignored } : {}),
    } : {});
  }, [
    connection,
    lifecycle?.status,
    pendingRetry,
    selectedAction,
    parkedAction,
    actionChoices.length,
    submitting,
    tablePreferences.autoPass,
    autoPass,
    combatStop,
    yieldPause,
    turnYield,
    turnBoundary.turn,
    view?.decision?.id,
    view?.viewRevision,
  ]);

  useEffect(() => {
    if ((!selectedAction || choiceMinimized) && actionChoices.length === 0 && !zoneBrowser && !pinnedCardRef && !showPublicLog) return;
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
        if (pinnedCardRef) {
          setPinnedCardRef(null);
          return;
        }
        if (zoneBrowser) {
          setZoneBrowser(null);
          return;
        }
        dismissChoice();
        setActionChoices([]);
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
  }, [selectedAction, actionChoices, choiceMinimized, minimizeInsteadOfCancel, view?.decision?.id, zoneBrowser, pinnedCardRef, showPublicLog]);

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

  async function playAgain() {
    setPreparingRematch(true);
    try {
      const result = await api.rematch(gameId);
      onRoom(result.room, result.invite_code);
    } catch (caught) {
      setNotice(caught instanceof Error ? caught.message : "Couldn't open the next table.");
    } finally {
      setPreparingRematch(false);
    }
  }

  function chooseAction(action: LegalAction) {
    setActionChoices([]);
    setMinimizedChoice(null);
    setCardContext(null);
    setPublicStage("");
    if (action.action === "pass" || isRoutineCast(action) || (!action.form && !needsActivationConfirmation(action))) {
      if (action.action === "pass" && turnYield) setTurnYield(turnBoundary);
      const choices = action.action === "cast" && action.form
        ? executableChoices(action.form, initialChoices(action.form)) : {};
      void act(action, choices);
      return;
    }
    openDraft(action);
  }

  function dismissChoice() {
    if (selectedAction && minimizeInsteadOfCancel && view?.decision) setMinimizedChoice(view.decision.id);
    else closeDraft();
  }

  function chooseCardAction(actions: LegalAction[], card?: JsonValue) {
    if (card) setInspectedCard(card);
    if (actions.length === 1) {
      chooseAction(actions[0]);
    } else if (actions.length > 1) {
      setActionChoices(actions);
    }
  }

  function selectCardActions(actions: LegalAction[], card: JsonValue) {
    const [action] = actions;
    if (actions.length === 1 && action?.action === "play_land" && !action.form) {
      chooseAction(action);
      return;
    }
    setInspectedCard(card);
    setCardContext(card);
    setActionChoices([]);
  }

  function selectPermanentActions(actions: LegalAction[], card: JsonValue) {
    if (actions.length > 0 && actions.every((action) => action.action === "activate")) {
      chooseCardAction(actions, card);
    } else {
      selectCardActions(actions, card);
    }
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
    if (key === "autoPass") {
      if (value) autoPassDecisionRef.current = "";
      else setTurnYield(null);
    }
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
    submitDraft();
  }

  function startManualPayment() {
    if (!parkDraft()) return;
    setTablePreference("autoMana", false);
    setNotice("Add mana from your sources, then resume your cast.");
  }

  if (!view) return <main className="loading-screen"><div className="spinner" /><p>Opening your projected table…</p>{notice && <div className="error-banner">{notice}</div>}</main>;
  const currentView = view;
  const state = view.state;
  const game = asRecord(state.game);
  const statusLabel = lifecycle?.status === "complete" ? "Complete" : lifecycle?.status === "paused" ? "Paused"
    : lifecycle?.status === "aborted" ? "Ended" : lifecycle?.status === "active" ? "In progress" : "Loading";
  const turn = asRecord(state.turn);
  const combat = asRecord(state.combat);
  const players = asRecord(state.players);
  const isSpectator = view.principal === "spectator" || lifecycle?.spectator === true;
  const ownSeat = isSpectator ? "" : view.principal.split(":").at(-1) ?? "?";
  const ownPlayer = asRecord(players[ownSeat]);
  const hand = asList(ownPlayer.hand);
  const ownCommand = asList(ownPlayer.cmd);
  const zoneOffers = !isSpectator && connection === "LIVE" && lifecycle?.status === "active"
    ? handZoneOffers(view) : new Map<string, HandZoneOffer>();
  const inspectionTarget = inspectedCard ?? hand[0] ?? null;
  const pinnedCard = pinnedCardRef
    ? stack.find((item) => asRecord(item).id === pinnedCardRef) ?? projectedChoiceCards(view).get(pinnedCardRef) ?? null
    : null;
  const inspectionSeat = pinnedCard ? projectedCardSeat(view, pinnedCard) : undefined;
  const pinInspector = (value: JsonValue | null) => {
    const ref = asRecord(value).id;
    if (typeof ref === "string" && ref) setPinnedCardRef(ref);
  };
  const decisionSeat = tableSeat(view.decision?.actor) ?? tableSeat(ownSeat);
  const compactInspector = inspectionTarget && <button type="button" className="mobile-inspector-trigger link-button"
    aria-label={`View ${cardName(inspectionTarget)}`} onClick={() => pinInspector(inspectionTarget)}>Inspect</button>;
  const showRail = hasStack ? !stackMinimized : railOpen;
  const focusedStackItem = showRail ? stack.find((item) => asRecord(item).id === focusedStackId) : undefined;
  const response = showRail && !focusedStackItem
    ? stack.find((item) => asList(asRecord(item).targets).some((ref) => stackPositions.has(String(ref))))
    : undefined;
  const tracedStackItem = asRecord(focusedStackItem ?? response);
  const stackSource = String(tracedStackItem.id ?? "");
  const stackTargets = asList(tracedStackItem.targets).map(String).filter((ref) => focusedStackItem || stackPositions.has(ref));
  const stackCountLabel = `${stack.length} ${stack.length === 1 ? "item" : "items"}`;
  const labels = projectedLabels(view);
  const labelFor = (ref: string) => {
    const name = labels.get(ref) ?? "Unavailable object";
    const position = stackPositions.get(ref);
    return position && stack.length > 1 ? `${name} · Stack #${position}` : name;
  };
  const describeEvent = (event: PublicGameEvent) => publicEventSummary(event, (ref) => labels.get(ref));
  const legalActions = (view.decision?.legal_actions ?? []).map((action) => {
    if (action.action !== "undo_mana" || typeof action.source !== "string") return action;
    const source = labels.get(action.source);
    return { ...action, label: source ? `Undo mana from ${source}` : "Undo mana" };
  });
  const cardActions = legalActions.filter((action) =>
    typeof (action.card ?? action.source) === "string",
  );
  const manaActions = legalActions.filter((action) =>
    action.action === "activate" && action.mana_ability === true,
  );
  const manaUndoActions = legalActions.filter((action) =>
    action.action === "undo_mana",
  );
  const handActionIds = new Set(handManuallyCollapsed ? [] : Array.from(zoneOffers.values())
    .filter((offer) => offer.zone !== "cmd").flatMap((offer) => offer.actions.map((action) => action.id)));
  const displayActions = legalActions.filter((action) =>
    action.action !== "concede" && !handActionIds.has(action.id) && (action.mana_ability !== true || manualMana),
  );
  const concedeAction = legalActions.find((action) => action.action === "concede");
  const optionalActivations = legalActions.filter((action) => action.action === "activate" && action.mana_ability !== true);
  const contextualAction = contextualDecisionAction(view);
  const actionDisabled = submitting || Boolean(pendingRetry) || connection !== "LIVE" || lifecycle?.status !== "active";
  const showPublicDraft = Boolean(currentStep) || usesModeTargetDraft(selectedAction, choiceValues);
  const draftSource = showPublicDraft && selectedAction ? String(selectedAction.card ?? selectedAction.source ?? "") : "";
  const draftTargets = publicSteps.flatMap((step) => step.group ? step.selected : []);
  const publicSelection: TableObjectSelection | null = currentStep ? {
    refs: currentStep.refs,
    selected: currentStep.selected,
    select(ref) {
      if (actionDisabled) return;
      stageChoices(stageTableObject(currentStep, ref, choiceValues));
      if (currentStep.maximum === 1 && !currentStep.selected.includes(ref)) {
        const next = publicSteps[publicSteps.indexOf(currentStep) + 1];
        if (next && next.selected.length < next.minimum) setPublicStage(next.id);
      }
    },
  } : null;
  const actionsForCard = (ref: string) => zoneOffers.get(ref)?.actions ?? cardActions.filter((action) =>
    String(action.card ?? action.source ?? "") === ref
    && (action.mana_ability !== true || manualMana),
  );
  const dropEnabled = !showPublicDraft && !isSpectator && [...hand, ...ownCommand, ...Array.from(zoneOffers.values(), (offer) => offer.card)].some((card) =>
    actionsForCard(String(asRecord(card).id ?? "")).some((action) => ["play_land", "cast"].includes(action.action)),
  );
  const activeSeat = String(turn.active ?? "");
  const prioritySeat = String(turn.priority ?? "");
  const actionWindow = { activeSeat, ownSeat, phase: String(turn.phase ?? ""), stackDepth: stack.length };
  const selectedCardRef = draftSource || String(asRecord(cardContext ?? undefined).id ?? "");
  const contextualActions = selectedCardRef ? actionsForCard(selectedCardRef) : [];
  const actionExplanations = asRecord(state.action_explanations);
  const selectedExplanation = asRecord(actionExplanations[selectedCardRef]);
  const zonePlayer = zoneBrowser ? asRecord(players[zoneBrowser.seat]) : {};
  const zoneCards = zoneBrowser ? asList(zonePlayer[zoneBrowser.zone]) : [];
  const zoneName = zoneBrowser?.zone === "gy" ? "Graveyard" : "Exile";
  const canChooseFromZone = zoneCards.some((card) => publicSelection?.refs.includes(String(asRecord(card).id ?? "")));
  const spaceForChoice = contextualAction?.action === "block" || contextualAction?.action === "attack"
    || view.decision?.kind === "semantic.search";
  const handCollapsed = handManuallyCollapsed || spaceForChoice || (
    tablePreferences.handAutoCollapse && hand.length === 0 && zoneOffers.size === 0
  );
  const orderedPlayers = Object.entries(players).sort(([left], [right]) =>
    Number(left === ownSeat) - Number(right === ownSeat));
  const opponents = orderedPlayers.filter(([seat]) => seat !== ownSeat);
  const opponentView = opponents.some(([seat]) => seat === tablePreferences.opponentView)
    ? tablePreferences.opponentView : "all";
  const showOwnOnly = !isSpectator && compactBoards && compactOwnBoard;
  const showOpponentsOnly = !isSpectator && compactBoards && !compactOwnBoard;
  const singleOpponent = !isSpectator && (opponents.length <= 1 || opponentView !== "all");
  const isBoardHidden = (seat: string) => !isSpectator && (seat === ownSeat
    ? showOpponentsOnly : showOwnOnly || (opponents.length > 1 && opponentView !== "all" && opponentView !== seat));
  function selectOpponent(next: OpponentView) {
    setTablePreference("opponentView", next);
    setCompactOwnBoard(false);
  }
  function opponentTargets(seat: string, player: JsonValue) {
    if (!publicSelection) return 0;
    const board = asRecord(player);
    const refs = [seat, ...["bf", "cmd", "gy", "ex"].flatMap((zone) =>
      asList(board[zone]).map((card) => String(asRecord(card).id ?? "")))];
    return refs.filter((ref) => publicSelection.refs.includes(ref)).length;
  }
  const tableStyle = { ...shellStyle, ...tablePreferenceStyle(tablePreferences) };
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

  function toggleStackRail() {
    if (hasStack) setStackMinimized((minimized) => !minimized);
    else setRailOpen((open) => !open);
    setFocusedStackId("");
  }

  function railPanel(panel: RightRailPanel) {
    if (panel === "stack") {
      return <StackPanel key={panel} stack={stack} view={currentView} labelFor={labelFor}
        positions={stackPositions} targets={draftSource ? [] : stackTargets}
        onFocusItem={setFocusedStackId} onMinimize={() => {
          toggleStackRail();
          stackToggleRef.current?.focus();
        }}
        onInspect={setInspectedCard} selection={publicSelection}
        onOpenInspector={pinInspector} />;
    }
    if (panel === "activity") {
      return tablePreferences.activityVisible ? <RecentActivity key={panel} events={publicEvents}
        describeEvent={describeEvent} onOpenLog={() => setShowPublicLog(true)} /> : null;
    }
    if (tablePreferences.inspectorCollapsed) {
      return (
        <section className="inspector-collapsed" key={panel} data-testid="card-inspector-collapsed">
          <span>Card viewer collapsed</span>
          <button type="button" className="link-button" onClick={() => setTablePreference("inspectorCollapsed", false)}>Open</button>
        </section>
      );
    }
    return <CardInspector key={panel} value={inspectionTarget} view={currentView} onExpand={() => pinInspector(inspectionTarget)} />;
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
          <span data-testid="connection-label">{connection === "LIVE" ? "Connected"
            : connection === "SYNCING" ? "Syncing table" : connection === "RECONNECTING" ? "Reconnecting"
              : connection === "STOPPED" ? "Connection ended" : "Connecting"}</span>
          {connection !== "LIVE" && connection !== "STOPPED" && <button type="button" className="link-button" onClick={() => setReconnectNonce((value) => value + 1)}>Retry now</button>}
          {connection === "STOPPED" && <button type="button" className="link-button" onClick={onExit}>Return to lobby</button>}
        </div>
        <div className="game-identity"><small>{lifecycle?.format_profile === "commander_duel" ? "Commander duel" : "Commander pod"}</small></div>
        <div className="game-status-line">
          <div className="routine-table-actions" aria-label="Priority and mana">
        {!isSpectator && lifecycle?.status !== "complete" && (
          <AutomationControls
            autoPass={tablePreferences.autoPass}
            autoMana={tablePreferences.autoMana}
            onAutoPass={() => setTablePreference("autoPass", !tablePreferences.autoPass)}
            onAutoMana={() => setTablePreference("autoMana", !tablePreferences.autoMana)}
          />
        )}
        {!isSpectator && lifecycle?.status === "active" && turnBoundary.turn > 0 && <div className="turn-yield">
          <button type="button" className="table-control-toggle" aria-pressed={Boolean(turnYield)}
            disabled={!tablePreferences.autoPass && !turnYield}
            onClick={() => setTurnYield(turnYield ? null : turnBoundary)}>
            {turnYield ? "Cancel pass turn" : "Pass turn"}
          </button>
          {turnYield && <small role="status">{yieldPause ?? `Armed for turn ${turnYield.turn}`}</small>}
        </div>}
          </div>
          <button ref={stackToggleRef} type="button" className="table-control-toggle stack-toggle" aria-expanded={showRail} data-testid="stack-toggle"
            aria-controls={showRail ? "match-stack" : undefined}
            aria-label={hasStack ? `${showRail ? "Minimize" : "Expand"} stack (${stackCountLabel})` : undefined}
            onClick={toggleStackRail}>{hasStack ? `Stack (${stack.length})` : showRail ? "Stack open" : "Stack"}</button>
          <span data-testid="game-status" className={`game-status ${lifecycle?.status ?? "loading"}`}>
            {statusLabel}
          </span>
          <GameMenu system={system} lifecycle={lifecycle} decision={view.decision?.kind} move={lastMove}
            disabled={actionDisabled} onConcede={concedeAction ? () => chooseAction(concedeAction) : undefined}>
            <button type="button" className="secondary-button" onClick={onExit}>Decks and games</button>
            {!isSpectator && <details>
              <summary>Combat priority stops</summary>
              <CombatStopOptions selected={combatStops} onToggle={toggleCombatStop} />
            </details>}
            {optionalActivations.length > 0 && <details>
              <summary>Priority stops for this turn</summary>
              {optionalActivations.map((action) => <label key={action.id} className="choice-option">
                <input type="checkbox" checked={isIgnoredActivation(action, ignoredOffers)}
                  onChange={(event) => {
                    const offers = new Map(ignoredOffers);
                    if (event.target.checked) offers.set(action.id, activationStopSignature(action));
                    else offers.delete(action.id);
                    setIgnoredStops({ turn: turnBoundary.turn, offers });
                  }} />
                Ignore stop for {action.label ?? "this ability"}
              </label>)}
            </details>}
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
          </GameMenu>
        </div>
      </header>
      <TurnStatus value={turnPresentation} compact={tablePreferences.compactPhaseRail}
        combatStops={combatStops} onCombatStop={isSpectator ? undefined : toggleCombatStop} holding={combatStop} />
      {connection !== "LIVE" && (
        <div className="connection-banner" role="status">
          <div><strong>{connection === "STOPPED" ? "Connection closed" : "Restoring your table"}</strong><span>{connection === "STOPPED" ? "Open the game again from Decks and games." : "Wait for the table to sync before sending a move."}</span></div>
          <span>Attempt {Math.max(1, reconnectAttempts)}</span>
        </div>
      )}

      {showInspection && lifecycle && (
        <section className="inspection-panel" data-testid="game-inspection">
          <h2>Match record</h2>
          <dl>
            <div><dt>Status</dt><dd>{statusLabel}</dd></div>
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
                : "This match has ended."}
          </span>
          <div className="game-over-actions">
            {!isSpectator && <button type="button" disabled={preparingRematch} onClick={() => void playAgain()}>
              {preparingRematch ? "Opening the next table…" : lifecycle.owner ? "Play again with these decks" : "Join the next table"}
            </button>}
            <button type="button" className="secondary-button" onClick={onExit}>Decks and games</button>
          </div>
        </div>
      )}
      <div className={`table-workspace${showRail ? " has-stack-rail" : ""}${hasStack ? " has-active-stack" : ""}`}>
        <div className="boards-panel">
          <div className="board-view-bar">
            {!isSpectator && (opponents.length > 1 || compactBoards) && <div className="opponent-views" role="group" aria-label="Battlefield views">
              {compactBoards && <button type="button" className="link-button" data-seat={ownSeat}
                aria-pressed={showOwnOnly} onClick={() => setCompactOwnBoard(true)}>Your board</button>}
              {opponents.length > 1 && <button type="button" className="link-button"
                aria-pressed={!showOwnOnly && opponentView === "all"} onClick={() => selectOpponent("all")}>All opponents</button>}
              {opponents.map(([seat, player]) => {
                const targetCount = opponentTargets(seat, player);
                return <button key={seat} type="button" className="link-button" data-seat={seat}
                  aria-pressed={!showOwnOnly && (opponentView === seat || opponents.length === 1)}
                  onClick={() => { const next = tableSeat(seat); if (next) selectOpponent(next); }}>
                  Seat {seat}{targetCount > 0 && <span className="opponent-target-count"> · {targetCount} {targetCount === 1 ? "target" : "targets"}</span>}
                </button>;
              })}
            </div>}
            <CardSizeSlider label="Table card size" value={tablePreferences.cardScale}
              onChange={(next) => setTablePreference("cardScale", next)} />
          </div>
        <section className={`boards-grid${isSpectator ? " watching" : ""}${singleOpponent || showOwnOnly ? " one-opponent" : ""}${showOwnOnly || showOpponentsOnly ? " compact-board-row" : ""}`}
          aria-label="Commander battlefield" data-opponent-view={opponentView}>
          {orderedPlayers.map(([seat, player]) => (
            <PlayerBoard
              key={seat}
              seat={seat}
              player={asRecord(player)}
              active={activeSeat === seat}
              priority={prioritySeat === seat}
              mine={ownSeat === seat}
              hidden={isBoardHidden(seat)}
              view={view}
              labelFor={labelFor}
              cardActions={cardActions}
              onCardIntent={selectPermanentActions}
              onManaIntent={chooseCardAction}
              onInspectCard={setInspectedCard}
              onOpenInspector={pinInspector}
              highlightedTargets={stackTargets}
              selection={publicSelection}
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
        </div>
        {showRail && <aside className="table-sidebar" aria-label="Stack and recent game activity">
          {(hasStack ? ["stack", "activity"] as const : tablePreferences.rightRailOrder).map(railPanel)}
        </aside>}
      </div>
      <div ref={bottomDockRef} className={`table-bottom-dock${isSpectator ? " spectator" : ""}`} data-testid="table-bottom-dock">
      {!isSpectator && handCollapsed && (
        <section className="hand-panel-collapsed" data-testid="hand-panel-collapsed">
          <span>Your hand · {hand.length} cards</span>
          {!spaceForChoice && <button type="button" className="link-button" onClick={() => setHandManuallyCollapsed(false)}>Show hand</button>}
          {compactInspector}
        </section>
      )}
      {!isSpectator && !handCollapsed && <section ref={handPanelRef} className="hand-panel" data-testid="hand-panel" data-resizable="true" onPointerUp={() => {
        const height = handPanelRef.current?.getBoundingClientRect().height;
        if (height) setTablePreference("handPanelHeight", Math.round(height));
      }}>
        <header><h2>Your hand</h2><div className="hand-panel-controls">
          <span className="zone-count">{hand.length} cards</span>{!cardContext && compactInspector}
          <button type="button" className="link-button" onClick={() => setHandManuallyCollapsed(true)}>Collapse</button>
        </div></header>
        {cardContext && (
          <div className="selected-card-actions" data-testid="selected-card-actions">
            <div>
              <strong>{cardName(cardContext)}</strong>
              {contextualActions.length === 0 && <small>{typeof selectedExplanation.message === "string" ? selectedExplanation.message : "No action is available for this card right now."}</small>}
            </div>
            <div className="selected-card-action-buttons">
              {contextualActions.map((action) => (
                <button type="button" key={action.id} disabled={submitting || Boolean(pendingRetry) || connection !== "LIVE" || lifecycle?.status !== "active"} onClick={() => chooseAction(action)}>
                  <ManaText text={actionTitle(action)} />
                </button>
              ))}
              <button type="button" className="secondary-button" onClick={() => pinInspector(cardContext)}>Inspect</button>
              <button type="button" className="secondary-button" onClick={() => setCardContext(null)}>Close</button>
            </div>
          </div>
        )}
        <HandRail cards={hand} zoneOffers={zoneOffers} view={view} selection={publicSelection}
          selectedRef={selectedCardRef} actionsForCard={actionsForCard} onIntent={selectCardActions}
          onInspect={setInspectedCard} onDrag={(ref) => { draggedCardRef.current = ref ?? ""; }} onDrop={handleCardDrop} />
      </section>}
      {pendingRetry && (
        <section className="retry-panel" role="alert" data-testid="command-retry">
          <div><strong>Command delivery is uncertain</strong><span>Retry “{pendingRetry.label}” to recover its result.</span></div>
          <button type="button" disabled={submitting || connection !== "LIVE"} onClick={retry}>{submitting ? "Checking…" : "Retry exact command"}</button>
        </section>
      )}
      {parkedAction && <aside className="manual-cast-draft">
        <strong>{parkedAction.label ?? "Spell to cast"}</strong>
        <button type="button" disabled={!canResumeDraft} onClick={resumeDraft}>Resume cast</button>
        <button type="button" className="secondary-button" onClick={dismissParkedDraft}>Discard draft</button>
      </aside>}
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
        data-seat={view.decision ? decisionSeat : undefined}
        data-testid="decision-panel"
        data-decision-id={view.decision?.id ?? ""}
        data-decision-kind={view.decision?.kind ?? ""}
        aria-live="polite"
      >
        {lifecycle?.status === "paused" ? (
          <div className="waiting-decision" data-testid="paused-decision"><span className="status-dot muted" /><div><strong>Match paused</strong><p>No player action or priority pass is pending while this boundary is reviewed.</p></div></div>
        ) : isSpectator ? (
          <div className="waiting-decision"><span className="status-dot muted" /><div><strong>Watching the table</strong><p>Player decisions and private information remain seat-scoped.</p></div></div>
        ) : selectedAction && showPublicDraft ? (
          <PublicActionDraft action={selectedAction} values={choiceValues} steps={publicSteps} activeStep={currentStep?.id ?? ""} view={view}
            onStage={setPublicStage} onValues={stageChoices} onCancel={closeDraft} onSubmit={submitDraft}
            onPayManually={selectedAction.action === "cast" && !manualMana ? startManualPayment : undefined}
            labelFor={labelFor} errors={choiceErrors} disabled={actionDisabled} commitBlocked={commitBlocked} />
        ) : contextualAction ? (
          <ContextualDecision key={view.decision?.id} action={contextualAction} view={view}
            disabled={actionDisabled} submitting={submitting} labelFor={labelFor} onSubmit={(action, choices) => { void act(action, choices); }} />
        ) : view.decision ? (
          <>
            <div className="decision-copy">
              <h2>{awaitingProjection ? "Updating the table…" : submitting ? "Sending move…" : decisionHeading(view.decision)}</h2>
              {currentChoiceDetail && <p>{currentChoiceDetail}</p>}
            </div>
            {choiceMinimized ? <button type="button" onClick={() => setMinimizedChoice(null)}>Show choice</button> :
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
                  <button className={`action-button ${action.action === "pass" ? "primary" : actionTone(action)}`} key={action.id} data-testid={`action-${action.id}`} disabled={actionDisabled} onClick={() => chooseAction(action)}>{visibleActionLabel(action, actionWindow)}</button>
                ))}
              </div>
            </div>}
          </>
        ) : <div className="waiting-decision"><span className="status-dot muted" /><div><strong>Waiting for the table</strong><p>Waiting for another player’s decision.</p></div></div>}
      </section>}
      </div>
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
                  <div>{tableSeat(event.actor) ? <SeatLabel seat={event.actor} /> : <strong>Game</strong>}<p>{describeEvent(event)}</p></div>
                </li>
              ))}
              {!publicEvents.length && <li className="empty-activity">{publicLogLoading ? "Loading public events…" : "No public events have been recorded."}</li>}
            </ol>
          </section>
        </div>
      )}
      {zoneBrowser && (
        <div className="choice-overlay" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) setZoneBrowser(null); }}>
          <section ref={zoneDialogRef} className="choice-dialog zone-browser" data-seat={zoneBrowser.seat} role="dialog" aria-modal="true" aria-labelledby="zone-browser-title" data-testid="zone-browser">
            <header>
              <div><SeatLabel seat={zoneBrowser.seat} /><h2 id="zone-browser-title">{zoneName} · {zoneCards.length}</h2></div>
              <button type="button" className="secondary-button" onClick={() => setZoneBrowser(null)}>{canChooseFromZone ? "Back to table" : "Close"}</button>
            </header>
            <div className="zone-browser-layout">
              <CardPages items={zoneCards} className={`zone-card-pages ${zoneBrowser.zone === "gy" ? "graveyard-pages" : "exile-pages"}`}
                label={zoneName} trackClassName="zone-card-grid" layout="grid" render={(card, index) => {
                  const ref = String(asRecord(card).id ?? "");
                  const actions = !publicSelection && zoneBrowser.seat === ownSeat ? actionsForCard(ref) : [];
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
                      selected={selectedCardRef === ref || publicSelection?.selected.includes(ref)}
                      choice={!actionDisabled && publicSelection?.refs.includes(ref) ? {
                        selected: publicSelection.selected.includes(ref),
                        select: () => publicSelection.select(ref),
                      } : undefined}
                    />
                  );
                }} />
              <CardInspector value={inspectedCard ?? zoneCards[0] ?? null} view={view} expanded />
            </div>
          </section>
        </div>
      )}
      {pinnedCardRef && (
        <div className="choice-overlay" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) setPinnedCardRef(null); }}>
          <section ref={previewDialogRef} className="choice-dialog card-preview-dialog" data-seat={inspectionSeat} role="dialog" aria-modal="true" aria-label={`Card viewer: ${pinnedCard ? cardName(pinnedCard) : "Card no longer visible"}`}>
            <header><div><SeatLabel seat={inspectionSeat} /><h2>{pinnedCard ? cardName(pinnedCard) : "Card no longer visible"}</h2></div><button type="button" className="secondary-button" onClick={() => setPinnedCardRef(null)}>Close</button></header>
            {pinnedCard && <CardInspector value={pinnedCard} view={view} expanded />}
          </section>
        </div>
      )}
      {actionChoices.length > 0 && (
        <div className="choice-overlay" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) setActionChoices([]); }}>
          <section ref={actionPickerRef} className="choice-dialog action-picker" data-seat={decisionSeat} role="dialog" aria-modal="true" aria-labelledby="action-picker-title">
            <header>
              <div>
                <SeatLabel seat={decisionSeat} />
                <h2 id="action-picker-title">{choosingLoyaltyAbility ? "Choose a loyalty ability" : "How should this card be used?"}</h2>
              </div>
              <button type="button" className="secondary-button" onClick={() => setActionChoices([])}>Cancel</button>
            </header>
            <div className="action-picker-options">
              {actionChoices.map((action) => (
                <button type="button" key={action.id} onClick={() => chooseAction(action)}>
                  {action.action === "cast" && !manualMana ? `Auto-mana · ${actionTitle(action)}` : actionTitle(action)}
                  {action.action === "activate" && <small>{activationCostLabel(action)}</small>}
                </button>
              ))}
            </div>
          </section>
        </div>
      )}
      {selectedAction && !choiceMinimized && !showPublicDraft && (
        selectedAction.form || selectedAction.action === "cast" || needsActivationConfirmation(selectedAction)
      ) && (
        <div className="choice-overlay" role="presentation" onMouseDown={(event) => {
          if (event.target === event.currentTarget) dismissChoice();
        }}>
          <form ref={choiceDialogRef} className="choice-dialog" data-seat={decisionSeat} role="dialog" aria-modal="true" aria-labelledby="choice-dialog-title" aria-describedby="choice-dialog-description" data-testid="choice-dialog" onSubmit={submitChoice}>
            <header>
              <div>
                <SeatLabel seat={decisionSeat} />
                <h2 id="choice-dialog-title">{minimizeInsteadOfCancel && view.decision ? decisionHeading(view.decision) : actionTitle(selectedAction)}</h2>
                <p id="choice-dialog-description">{selectedAction.action === "activate" ? <ManaText text={activationCostLabel(selectedAction)} />
                  : selectedAction.action === "concede" ? "Leave this game. Play continues for any remaining players."
                    : selectedAction.action === "cast" ? <ManaText text={cardActionCost([selectedAction]) || "Review the spell’s choices."} />
                      : currentChoiceDetail || String(view.decision?.ctx.prompt ?? "")}</p>
              </div>
              <button
                type="button"
                className="secondary-button"
                data-testid="cancel-choice"
                onClick={dismissChoice}
              >{minimizeInsteadOfCancel ? "Minimize choice" : "Cancel"}</button>
            </header>
            {selectedAction.form && selectedAction.action !== "concede" && (
              <ChoiceFormView
                form={selectedAction.form}
                values={choiceValues}
                onChange={stageChoices}
                labelFor={labelFor}
                view={view}
                costLabels={phyrexianCastCostLabels(selectedAction)}
              />
            )}
            {choiceErrors.length > 0 && (
              <ul className="choice-errors" data-testid="choice-errors" role="alert">
                {choiceErrors.map((error) => <li key={error}>{error}</li>)}
              </ul>
            )}
            <div className="choice-submit-row">
              {selectedAction.action === "cast" && !manualMana && (
                <button type="button" className="secondary-button" onClick={startManualPayment}>Use manual mana</button>
              )}
              <button type="submit" data-testid="submit-choice" disabled={submitting || connection !== "LIVE" || commitBlocked}>
                {choiceSubmitLabel(selectedAction, manualMana, submitting, choiceValues.mana_pool_payment)}
              </button>
            </div>
          </form>
        </div>
      )}
      <TargetArrowLayer sourceRef={draftSource || stackSource}
        targets={draftSource ? draftTargets : stackTargets} candidates={currentStep?.group ? currentStep.refs : []}
        zones={targetZones(view)} stackPositions={stackPositions} />
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
  const [preferredDeck, setPreferredDeck] = useState<SavedDeckSelection>();
  const [bootProblem, setBootProblem] = useState("");

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
          } catch (caught) {
            if (!(caught instanceof ApiError) || ![403, 404].includes(caught.status)) throw caught;
            if (stopped) return;
            sessionStorage.removeItem("commander-room");
            sessionStorage.removeItem(inviteStorageKey(roomId));
            setScreen("lobby");
          }
        } catch (caught) {
          if (!(caught instanceof ApiError) || ![401, 403].includes(caught.status)) throw caught;
          if (stopped) return;
          setScreen("welcome");
        }
      } catch (caught) {
        if (stopped) return;
        setBootProblem(caught instanceof Error ? caught.message : "The server did not respond.");
        setScreen("loading");
      }
    }
    void boot();
    return () => { stopped = true; };
  }, [bootNonce]);

  function enterRoom(next: Room, code = "", selection?: SavedDeckSelection) {
    setRoom(next);
    setInvite(code);
    setPreferredDeck(selection);
    setGameId("");
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
    setPreferredDeck(undefined);
    setGameId("");
    setScreen("lobby");
  }

  if (screen === "loading") return <main className="loading-screen">
    {bootProblem ? <section className="setup-card">
      <h1>Couldn’t open Quorune</h1>
      <p>Check that the server is running, then try again.</p>
      <button type="button" onClick={() => { setBootProblem(""); setBootNonce((value) => value + 1); }}>Retry connection</button>
      <details><summary>Connection details</summary><p>{bootProblem}</p></details>
    </section> : <div role="status"><div className="spinner" /><p>Opening Quorune…</p></div>}
  </main>;
  if (screen === "setup" && system) return <SetupScreen initial={system} onReady={() => { setScreen("loading"); setBootNonce((value) => value + 1); }} />;
  if (screen === "welcome") return <Welcome onReady={(value) => { setGuest(value); setScreen("lobby"); }} />;
  if (!guest) return null;
  if (screen === "lobby") return <Lobby guest={guest} system={system} onRoom={enterRoom} onGame={enterGame} />;
  if (screen === "room" && room) return <RoomView key={room.room_id} guest={guest} initial={room} invite={invite}
    onGame={enterGame} onRoom={enterRoom} onLeave={leaveRoomScreen} initialSavedDeck={preferredDeck} />;
  if (screen === "game" && gameId) return <GameView key={gameId} gameId={gameId} system={system} onExit={leaveRoomScreen} onRoom={enterRoom} />;
  return null;
}
