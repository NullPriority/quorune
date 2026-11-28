import { type CSSProperties, FormEvent, useEffect, useMemo, useRef, useState } from "react";
import {
  api,
  ApiError,
  type GameLifecycle,
  type Guest,
  type PublicGameEvent,
  type Room,
  type SystemStatus,
  streamUrl,
  streamProtocols,
} from "./api";
import { ChoiceFormView } from "./ChoiceForm";
import { ExileCastChoice } from "./ExileCastChoice";
import { createPortal } from "react-dom";
import { useCompanionWindow } from "./useCompanionWindow";
import { inviteStorageKey, RoomView } from "./RoomView";
import { clearTriggerResponses, ContextualDecision, contextualDecisionAction } from "./ContextualDecision";
import { projectedChoiceCards, projectedLabels } from "./CardChoiceGrid";
import {
  castsWithNoXTargets,
  choiceCompatibilityErrors,
  executableChoices,
  initialChoices,
} from "./choices";
import type { DecisionPacket, JsonValue, LegalAction } from "./generated/protocol";
import { asList, asRecord, cardName, CardInspector, CardTile } from "./CardSurface";
import { canFollowProjection, ingestPacket, type ProjectedView } from "./protocol";
import { PRIORITY_STOPS, type PriorityStop } from "./tableAutomation";
import { actionTitle, activationCostLabel, canMinimizeChoice, cardActionCost, choiceSubmitLabel, decisionDetail, decisionHeading, isRoutineCast, needsActivationConfirmation, orderedTableSeats, phyrexianCastCostLabels, publicEventSummary, visibleActionLabel } from "./tablePresentation";
import { AutomationControls, ManaHelp, ManaPoolStatus } from "./TableStatus";
import { CardSizeSlider, TableSettings } from "./TableSettings";
import { type OpenTableZone, ZoneBrowser, ZonePiles } from "./TableZones";
import { TurnStatus } from "./TurnStatus";
import { PriorityControls } from "./PriorityControls";
import { useTablePriorityLoop } from "./useTablePriority";
import { attachedBattlefieldCards, PlayerBoard } from "./PlayerBoard";
import { StackPanel } from "./StackPanel";
import { projectedCardSeat, SeatLabel, tableSeat } from "./SeatLabel";
import { GameMenu } from "./GameMenu";
import { SetupScreen } from "./SetupScreen";
import { RecentActivity } from "./RecentActivity";
import { PinnedTableViewer, TableCardViewer, usePinnedTableViewer } from "./TableCardViewer";
import { projectedPublicEvents, TableMotion } from "./TableMotion";
import { Lobby, Welcome } from "./Lobby";
import type { SavedDeckSelection } from "./PlayLibrary";
import { handZoneOffers, type HandZoneOffer } from "./handZoneOffers";
import { HandRail } from "./HandRail";
import { ManaText } from "./ManaText";
import { useDecisionInteraction } from "./useDecisionInteraction";
import type { ObservedTableAction } from "./useTableLoop";
import { LoopControls } from "./LoopControls";
import { PublicActionDraft, publicObjectSteps, stageTableObject, usesModeTargetDraft, type TableObjectSelection } from "./PublicActionDraft";
import { TargetArrowLayer, targetZones } from "./TargetArrowLayer";
import {
  DEFAULT_TABLE_PREFERENCES,
  MAX_HAND_PANEL_HEIGHT,
  MIN_HAND_PANEL_HEIGHT,
  loadTableColors,
  saveTableColors,
  tableColorStyle,
  tablePreferenceStyle,
  loadTablePreferences,
  saveTablePreferences,
  type OpponentView,
  type RightRailPanel,
  type TablePreferences,
} from "./tablePreferences";
import { useTableDockClearance } from "./useTableDockClearance";

type Screen = "loading" | "setup" | "welcome" | "lobby" | "room" | "game";
const noStackTargets: string[] = [];

function actionTone(action: LegalAction): string {
  const value = `${action.id} ${action.action} ${action.kind ?? ""}`.toLowerCase();
  if (value.includes("pass") || value.includes("decline") || value.includes("cancel")) return "quiet";
  if (value.includes("concede")) return "danger";
  return "primary";
}

function GameView({ gameId, userId, system, onExit, onRoom }: {
  gameId: string; userId: string; system: SystemStatus | null; onExit: () => void;
  onRoom: (room: Room, invite?: string) => void;
}) {
  const [view, setView] = useState<ProjectedView | null>(null);
  const visibleCards = useMemo(() => view ? projectedChoiceCards(view) : new Map<string, JsonValue>(), [view]);
  const labels = useMemo(() => view ? projectedLabels(view) : new Map<string, string>(), [view]);
  const boardAttachments = useMemo(() => attachedBattlefieldCards(asRecord(view?.state.players)), [view?.state.players]);
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
  const [tableColors, setTableColors] = useState(() => loadTableColors(userId));
  const [compactBoards, setCompactBoards] = useState(() =>
    window.matchMedia("(max-height: 520px), (max-width: 650px)").matches);
  const [compactOwnBoard, setCompactOwnBoard] = useState(true);
  const [handManuallyCollapsed, setHandManuallyCollapsed] = useState(false);
  const [activityCollapsed, setActivityCollapsed] = useState(false);
  const [detachedSeats, setDetachedSeats] = useState<string[]>([]);
  const [detachedStackFocus, setDetachedStackFocus] = useState("");
  const [detachedInspectionRef, setDetachedInspectionRef] = useState("");
  const boardsWindow = useCompanionWindow("Quorune opponent boards", "width=1200,height=850");
  const [inspectedCard, setInspectedCard] = useState<JsonValue | null>(null);
  const { pinned: keyboardPinnedCard, close: closeKeyboardPinnedCard } = usePinnedTableViewer(view, visibleCards, inspectedCard);
  const [focusedStackId, setFocusedStackId] = useState("");
  const [stackMinimized, setStackMinimized] = useState(false);
  const [railOpen, setRailOpen] = useState(false);
  const [cardContext, setCardContext] = useState<JsonValue | null>(null);
  const [publicStage, setPublicStage] = useState("");
  const [zoneBrowser, setZoneBrowser] = useState<OpenTableZone | null>(null);
  const [pinnedCardRef, setPinnedCardRef] = useState<string | null>(null);
  const [lifecycle, setLifecycle] = useState<GameLifecycle | null>(null);
  const [stopReason, setStopReason] = useState("Pause for a table break");
  const [showInspection, setShowInspection] = useState(false);
  const [showPublicLog, setShowPublicLog] = useState(false);
  const publicLogOpen = useRef(false);
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
  const publicEventCursor = useRef(0);
  const publicLogCall = useRef<Promise<void> | null>(null);
  const queuedLogRefresh = useRef<boolean | null>(null);
  const draggedCardRef = useRef("");
  const dragOverOwnBattlefieldRef = useRef(false);
  const lastDroppedCardRef = useRef("");
  const cardDropHandlerRef = useRef<(cardRef: string) => void>(() => undefined);
  const loopObserver = useRef<((result: ObservedTableAction) => void) | null>(null);
  const manualMana = !tablePreferences.autoMana;
  const liveDecision = connection === "LIVE" && lifecycle?.status === "active";
  const {
    draft, openDraft, closeDraft, stageChoices, submitDraft, act, retry, lastMove,
    pendingRetry, submitting, awaitingProjection, parkedAction, parkDraft, resumeDraft,
    canResumeDraft, dismissParkedDraft,
  } = useDecisionInteraction({ view, gameId, enabled: liveDecision, onNotice: setNotice, actionObserver: loopObserver });
  const selectedAction = draft?.action ?? null;
  const busyDeciding = submitting || Boolean(pendingRetry || selectedAction || parkedAction || actionChoices.length);
  const { loop, priorityAutomation } = useTablePriorityLoop({
    gameId, view, active: lifecycle?.status === "active", online: connection === "LIVE",
    preferences: tablePreferences, busy: busyDeciding, observer: loopObserver, act,
  });
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
  useEffect(() => saveTableColors(userId, tableColors), [userId, tableColors]);
  useEffect(() => {
    publicLogOpen.current = showPublicLog;
    if (showPublicLog) void refreshPublicLog(false);
  }, [gameId, showPublicLog]);
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
        let message: {
          type: string;
          packet?: DecisionPacket;
          game?: GameLifecycle;
          message?: string;
        };
        try {
          message = JSON.parse(String(event.data));
          if (!message || typeof message.type !== "string") throw new Error("Invalid stream message");
        } catch {
          setNotice("The table stream was interrupted. Reconnecting…");
          stream.close();
          return;
        }
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
            if (!canFollowProjection(viewRef.current, next)) {
              throw new Error("The reconnected projection belongs to another player.");
            }
            if (viewRef.current && viewRef.current.principal !== next.principal) {
              setInspectedCard(null);
              setPinnedCardRef(null);
              setZoneBrowser(null);
              setCardContext(null);
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
            if (refreshEvents && publicLogOpen.current) void refreshPublicLog(false);
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
        if (publicLogOpen.current) void refreshPublicLog(true);
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
      const choices = action.action === "cast" && action.form
        ? executableChoices(action.form, initialChoices(action.form)) : {};
      if (act(action, choices) && action.action === "pass") priorityAutomation.acknowledgeManualPass();
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
    if (key === "autoPass") priorityAutomation.resetYield();
    setTablePreferences((current) => ({ ...current, [key]: value }));
  }

  function togglePriorityStop(stop: PriorityStop) {
    const selected = new Set(tablePreferences.priorityStops);
    if (selected.has(stop)) selected.delete(stop);
    else selected.add(stop);
    setTablePreference("priorityStops", PRIORITY_STOPS.filter(({ id }) => selected.has(id)).map(({ id }) => id));
  }

  function resetTablePreferences() {
    priorityAutomation.resetYield();
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

  if (!view && connection === "STOPPED") return <main className="loading-screen">
    <div className="error-banner" role="alert">{notice || "This match is unavailable."}</div>
    <button type="button" onClick={onExit}>Return to lobby</button>
  </main>;
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
  const inspectionRef = asRecord(inspectedCard ?? undefined).id;
  const activityInspection = asRecord(inspectedCard ?? undefined).activity === true ? inspectedCard : null;
  const inspectionTarget = activityInspection ?? (typeof inspectionRef === "string"
    ? stack.find((item) => asRecord(item).id === inspectionRef) ?? visibleCards.get(inspectionRef)
    : undefined) ?? hand[0] ?? null;
  const pinnedCard = pinnedCardRef
    ? stack.find((item) => asRecord(item).id === pinnedCardRef) ?? visibleCards.get(pinnedCardRef)
      ?? (pinnedCardRef === inspectionRef ? activityInspection : null)
    : null;
  const inspectionSeat = pinnedCard ? projectedCardSeat(view, pinnedCard) : undefined;
  const pinInspector = (value: JsonValue | null) => {
    const ref = asRecord(value).id;
    if (typeof ref === "string" && ref) setPinnedCardRef(ref);
  };
  const decisionSeat = tableSeat(view.decision?.actor) ?? tableSeat(ownSeat);
  const compactInspector = inspectionTarget && <button type="button" className="mobile-inspector-trigger link-button"
    aria-label={`View ${cardName(inspectionTarget)}`} onClick={() => pinInspector(inspectionTarget)}>Inspect</button>;
  const showRail = Boolean(stackTargetStep) || (hasStack ? !stackMinimized : railOpen);
  const focusedStackItem = showRail ? stack.find((item) => asRecord(item).id === focusedStackId) : undefined;
  const defaultStackSource = stack.find((item) => asList(asRecord(item).targets).length > 0);
  const tracedStackItem = asRecord(showRail ? focusedStackItem ?? defaultStackSource : undefined);
  const stackSource = String(tracedStackItem.id ?? "");
  const stackTargets = tracedStackItem.targets
    ? asList(tracedStackItem.targets).map(String) : noStackTargets;
  const stackCountLabel = `${stack.length} ${stack.length === 1 ? "item" : "items"}`;
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
  const actionDisabled = submitting || Boolean(pendingRetry) || priorityAutomation.openingHoldsPending || loop.loop.kind === "running"
    || connection !== "LIVE" || lifecycle?.status !== "active";
  const showPublicDraft = Boolean(currentStep) || usesModeTargetDraft(selectedAction, choiceValues);
  const draftSource = showPublicDraft && selectedAction ? String(selectedAction.card ?? selectedAction.source ?? "") : "";
  const draftTargets = publicSteps.flatMap((step) => step.group ? step.selected : []);
  const targetLimitReached = currentStep?.maximum === 0 || (currentStep?.maximumFromX
    && currentStep.maximum > 1 && currentStep.selected.length >= currentStep.maximum);
  const publicSelection: TableObjectSelection | null = currentStep ? {
    refs: targetLimitReached ? currentStep.selected : currentStep.refs,
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
  const handCollapsed = handManuallyCollapsed || (
    tablePreferences.handAutoCollapse && hand.length === 0 && zoneOffers.size === 0
  );
  const orderedPlayers = orderedTableSeats(Object.keys(players), ownSeat)
    .map((seat) => [seat, players[seat]] as const);
  const opponents = orderedPlayers.filter(([seat]) => seat !== ownSeat);
  const opponentView = opponents.some(([seat]) => seat === tablePreferences.opponentView)
    ? tablePreferences.opponentView : "all";
  const showOwnOnly = !isSpectator && compactBoards && compactOwnBoard;
  const showOpponentsOnly = !isSpectator && compactBoards && !compactOwnBoard;
  const detached = new Set(boardsWindow.host ? detachedSeats : []);
  const mainOpponents = opponents.filter(([seat]) => !detached.has(seat));
  const showOpponentControls = !isSpectator && (opponents.length > 1 || compactBoards || mainOpponents.length > 0);
  const visibleOpponents = mainOpponents.filter(([seat]) => opponentView === "all" || opponentView === seat);
  const mainOpponentWidths = (visibleOpponents.length ? visibleOpponents : mainOpponents).map(([seat]) =>
    `minmax(0, ${tablePreferences.opponentWidths[tableSeat(seat)!] ?? 100}fr)`);
  const opponentStyle = mainOpponentWidths.length ? { "--opponent-columns": mainOpponentWidths.join(" ") } as CSSProperties : undefined;
  const tableSizeControl = <CardSizeSlider label="Table card size" value={tablePreferences.cardScale}
    onChange={(next) => setTablePreference("cardScale", next)} />;
  const singleOpponent = !isSpectator && (mainOpponents.length <= 1 || visibleOpponents.length === 1);
  const isBoardHidden = (seat: string) => !isSpectator && (seat === ownSeat
    ? showOpponentsOnly && mainOpponents.length > 0
    : detached.has(seat) || showOwnOnly || (visibleOpponents.length > 0 && !visibleOpponents.some(([id]) => id === seat)));
  function selectOpponent(next: OpponentView) {
    setTablePreference("opponentView", next);
    setCompactOwnBoard(false);
  }
  function opponentTargets(seat: string, player: JsonValue) {
    if (!publicSelection) return 0;
    const board = asRecord(player);
    const refs = [seat, ...["bf", "cmd", "gy", "ex", "known_top"].flatMap((zone) =>
      asList(board[zone]).map((card) => String(asRecord(card).id ?? "")))];
    return refs.filter((ref) => publicSelection.refs.includes(ref)).length;
  }
  const arrowSeats = new Map<string, string>();
  for (const [seat, player] of orderedPlayers) {
    arrowSeats.set(seat, seat);
    for (const card of asList(asRecord(player).bf)) arrowSeats.set(String(asRecord(card).id), seat);
  }
  const attackPairs = Object.entries(asRecord(combat.atk)).filter((pair): pair is [string, string] => typeof pair[1] === "string");
  const zones = targetZones(view);
  const detachedStack = asRecord(stack.find((item) => asRecord(item).id === detachedStackFocus) ?? defaultStackSource);
  const detachedTargets = asList(detachedStack.targets).map(String);
  const detachedCards = opponents.filter(([seat]) => detached.has(seat)).flatMap(([, player]) => {
    const projected = asRecord(player);
    return [...asList(projected.bf), ...asList(projected.cmd)];
  });
  const detachedInspection = [...detachedCards, ...stack].find((card) => asRecord(card).id === detachedInspectionRef) ?? null;
  const inspectDetached = (card: JsonValue) => setDetachedInspectionRef(String(asRecord(card).id ?? ""));
  const colorStyle = tableColorStyle(tableColors);
  const tableStyle = { ...shellStyle, ...tablePreferenceStyle(tablePreferences), ...colorStyle };
  const turnPresentation = {
    turnSequence: Number(turn.seq ?? 0),
    personalTurn: Number(turn.number ?? 0),
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
      return tablePreferences.activityVisible ? <RecentActivity key={panel} events={projectedPublicEvents(currentView)}
        cards={visibleCards} describeEvent={describeEvent}
        onInspect={(card, wasPublic) => setInspectedCard(wasPublic ? { ...asRecord(card), activity: true } : card)}
        collapsed={activityCollapsed}
        onToggle={() => setActivityCollapsed((value) => !value)}
        onOpenLog={() => setShowPublicLog(true)} /> : null;
    }
    return <TableCardViewer key={panel} value={inspectionTarget} view={currentView} collapsed={tablePreferences.inspectorCollapsed}
      onToggle={() => setTablePreference("inspectorCollapsed", !tablePreferences.inspectorCollapsed)}
      onPopOut={() => setActivityCollapsed(true)} onExpand={() => pinInspector(inspectionTarget)} />;
  }
  const toolbar = (
    <div className="table-header-tools">
      <div className="connection-group">
        <span className={`connection ${connection.toLowerCase()}`} />
        <span className="connection-label" data-testid="connection-label">{connection === "LIVE" ? "Connected"
          : connection === "SYNCING" ? "Syncing table" : connection === "RECONNECTING" ? "Reconnecting"
            : connection === "STOPPED" ? "Connection ended" : "Connecting"}</span>
        {connection !== "LIVE" && connection !== "STOPPED" && <button type="button" className="link-button" onClick={() => setReconnectNonce((value) => value + 1)}>Retry now</button>}
        {connection === "STOPPED" && <button type="button" className="link-button" onClick={onExit}>Return to lobby</button>}
      </div>
      <div className="game-status-line">
        {!isSpectator && lifecycle?.status !== "complete" && <div className="routine-table-actions" aria-label="Priority and mana">
          <AutomationControls autoPass={tablePreferences.autoPass} autoMana={tablePreferences.autoMana}
            onAutoPass={() => setTablePreference("autoPass", !tablePreferences.autoPass)}
            onAutoMana={() => setTablePreference("autoMana", !tablePreferences.autoMana)} />
        </div>}
        <span data-testid="game-status" className={`game-status ${lifecycle?.status ?? "loading"}`}>
          {statusLabel}
        </span>
        <GameMenu system={system} lifecycle={lifecycle} decision={view.decision?.kind} move={lastMove}
          disabled={actionDisabled} onConcede={concedeAction ? () => chooseAction(concedeAction) : undefined}>
          <button type="button" className="secondary-button" onClick={onExit}>Decks and games</button>
          <button
            type="button"
            className="secondary-button"
            data-testid="inspect-game"
            aria-expanded={showInspection}
            onClick={() => setShowInspection((value) => !value)}
          >Inspect match</button>
          <button type="button" className="secondary-button" data-testid="open-public-log" onClick={() => setShowPublicLog(true)}>Public log</button>
          <button type="button" className="secondary-button" onClick={() => {
            clearTriggerResponses(gameId);
            setNotice("Remembered trigger responses cleared.");
          }}>Clear trigger responses</button>
          <TableSettings value={tablePreferences} colors={tableColors} onChange={setTablePreference}
            onColors={setTableColors} onReset={resetTablePreferences}
            opponentSeats={opponents.flatMap(([seat]) => tableSeat(seat) ?? [])} />
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
    </div>
  );
  return (
    <main
      className={`game-shell${isSpectator ? " spectator-table" : ""} density-${tablePreferences.boardDensity}`}
      data-game-id={String(game.id ?? gameId)}
      data-custom-colors={colorStyle ? "true" : undefined}
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
        {lifecycle?.status === "complete" ? toolbar : <TurnStatus value={turnPresentation} compact={tablePreferences.compactPhaseRail}
          priorityStops={tablePreferences.priorityStops} onPriorityStop={isSpectator ? undefined : togglePriorityStop} holding={priorityAutomation.held}
          onAdvanceToFirstMain={!isSpectator && lifecycle?.status === "active" && priorityAutomation.canAdvanceToFirstMain
            ? priorityAutomation.advanceToFirstMain : undefined} toolbar={toolbar} />}
      </header>
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
            {lifecycle.seat !== null && <button type="button" disabled={preparingRematch} onClick={() => void playAgain()}>
              {preparingRematch ? "Opening the next table…" : lifecycle.owner ? "Play again with these decks" : "Join the next table"}
            </button>}
            <button type="button" className="secondary-button" onClick={onExit}>Decks and games</button>
          </div>
        </div>
      )}
      <TableMotion key={gameId} view={view} ownSeat={ownSeat} />
      <div className={`table-workspace main-table-workspace has-stack-rail${hasStack && showRail ? " has-active-stack" : ""}`}>
        <div className="board-view-bar">
          {showOpponentControls && <>
            <div className="opponent-views" role="group" aria-label="Battlefield views">
              {compactBoards && <button type="button" className="link-button" data-seat={ownSeat} data-seat-anchor={ownSeat}
                aria-pressed={showOwnOnly} onClick={() => setCompactOwnBoard(true)}>Your board</button>}
              {opponents.length > 1 && <button type="button" className="link-button"
                aria-pressed={!showOwnOnly && opponentView === "all"} onClick={() => selectOpponent("all")}>All opponents</button>}
              {opponents.map(([seat, player]) => {
                const targetCount = opponentTargets(seat, player);
                return <button key={seat} type="button" className="link-button" data-seat={seat} data-seat-anchor={seat}
                  aria-pressed={!detached.has(seat) && !showOwnOnly && (opponentView === seat || opponents.length === 1)}
                  onClick={() => {
                    if (detached.has(seat)) { boardsWindow.open(); return; }
                    const next = tableSeat(seat);
                    if (next) selectOpponent(next);
                  }}>
                  Seat {seat}{detached.has(seat) && " ↗"}{targetCount > 0 && <span className="opponent-target-count"> · {targetCount} {targetCount === 1 ? "target" : "targets"}</span>}
                </button>;
              })}
            </div>
            <details className="board-detach">
              <summary>Detach boards</summary>
              <div className="board-detach-options">
                {opponents.map(([seat]) => <label key={seat} data-seat={seat}>
                  <input type="checkbox" checked={detachedSeats.includes(seat)} onChange={(event) =>
                    setDetachedSeats((seats) => event.target.checked ? [...seats, seat] : seats.filter((id) => id !== seat))} />
                  Seat {seat}
                </label>)}
                <button type="button" disabled={!detachedSeats.length} onClick={() => {
                  if (boardsWindow.open()) {
                    (document.activeElement as HTMLElement | null)?.closest("details")?.removeAttribute("open");
                  } else setNotice("Allow pop-ups to open the opponent boards.");
                }}>Open detached boards</button>
              </div>
            </details>
          </>}
          {tableSizeControl}
          <button ref={stackToggleRef} type="button" className="table-control-toggle stack-toggle" aria-expanded={showRail} data-testid="stack-toggle"
            aria-controls={showRail ? "match-stack" : undefined}
            aria-label={hasStack ? `${showRail ? "Minimize" : "Expand"} stack (${stackCountLabel})` : undefined}
            onClick={toggleStackRail}>{showRail ? "Hide stack" : hasStack ? `Show stack (${stack.length})` : "Show stack"}</button>
        </div>
        <section className={`boards-grid${isSpectator ? " watching" : ""}${singleOpponent || showOwnOnly ? " one-opponent" : ""}${showOwnOnly || showOpponentsOnly ? " compact-board-row" : ""}`}
          aria-label="Commander battlefield" data-opponent-view={opponentView} style={opponentStyle}
          data-no-opponents={!mainOpponents.length || undefined}>
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
              attachments={boardAttachments}
              onCardIntent={selectPermanentActions}
              onManaIntent={chooseCardAction}
              onInspectCard={setInspectedCard}
              onOpenInspector={pinInspector}
              highlightedTargets={stackTargets}
              selection={publicSelection}
              selectedCardRef={selectedCardRef}
              onOpenZone={(seat, zone) => setZoneBrowser({ seat, zone })}
              dropEnabled={dropEnabled}
              onCardDrop={handleCardDrop}
              onCardDrag={(ref) => { draggedCardRef.current = ref ?? ""; }}
              getDraggedCard={() => draggedCardRef.current}
              dragRelease={dragRelease}
            />
          ))}
        </section>
        <aside className="table-sidebar" aria-label="Stack, card viewer and game activity">
          {tablePreferences.rightRailOrder.filter((panel) => panel !== "stack" || showRail).map(railPanel)}
        </aside>
      </div>
      <div ref={bottomDockRef} className={`table-bottom-dock${isSpectator ? " spectator" : ""}`} data-testid="table-bottom-dock">
      {!isSpectator && <div className="hand-and-zones">
      {!isSpectator && handCollapsed && (
        <section className="hand-panel-collapsed" data-testid="hand-panel-collapsed">
          <span>Your hand · {hand.length} cards</span>
          {handManuallyCollapsed && <button type="button" className="link-button" onClick={() => setHandManuallyCollapsed(false)}>Show hand</button>}
          {compactInspector}
        </section>
      )}
      {!isSpectator && !handCollapsed && <section className="hand-panel" data-testid="hand-panel" data-resizable="true">
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
        <HandRail cards={hand} artFocused={tablePreferences.handArtFocused} zoneOffers={zoneOffers} view={view} selection={publicSelection}
          selectedRef={selectedCardRef} actionsForCard={actionsForCard} onIntent={selectCardActions}
          onResize={(height) => setTablePreference("handPanelHeight", Math.max(MIN_HAND_PANEL_HEIGHT, Math.min(MAX_HAND_PANEL_HEIGHT, height)))}
          onInspect={setInspectedCard} onDrag={(ref) => { draggedCardRef.current = ref ?? ""; }} onDrop={handleCardDrop} />
      </section>}
      </div>}
      {!isSpectator && <ZonePiles localDock seat={ownSeat} player={ownPlayer} hidden={false} selection={publicSelection}
        highlightedTargets={stackTargets} onOpen={(seat, zone) => setZoneBrowser({ seat, zone })} />}
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
      {manualMana && !isSpectator && <ManaPoolStatus pool={ownPlayer.mana} />}
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
            disabled={actionDisabled} submitting={submitting} labelFor={labelFor} onInspect={setInspectedCard} onSubmit={(action, choices) => { void act(action, choices); }} />
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
              {view.decision.kind === "selection.exile_cast" && displayActions.length === 2
                && displayActions.some(({ id }) => id === "cast") && displayActions.some(({ id }) => id === "decline")
                ? <ExileCastChoice view={view} disabled={actionDisabled} onInspect={setInspectedCard}
                  onAnswer={(action, choices) => { if (choices) act(action, choices); else chooseAction(action); }} />
                : <div className="action-row">
                {displayActions.map((action) => (
                  <button className={`action-button ${action.action === "pass" ? "primary" : actionTone(action)}`} key={action.id}
                    data-testid={`action-${action.id}`} disabled={actionDisabled}
                    title={visibleActionLabel(action, actionWindow)}
                    onClick={() => chooseAction(action)}>{visibleActionLabel(action, actionWindow)}</button>
                ))}
              </div>}
            </div>}
          </>
        ) : <div className="waiting-decision"><span className="status-dot muted" /><div><strong>Waiting for the table</strong><p>Waiting for another player’s decision.</p></div></div>}
      </section>}
      {!isSpectator && lifecycle?.status === "active" && <PriorityControls priority={priorityAutomation}
        preferences={tablePreferences} activations={optionalActivations} onToggleStop={togglePriorityStop}
        onChange={setTablePreference} loopControls={<LoopControls controller={loop} />} opponentSeats={opponents.map(([seat]) => seat)} />}
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
      {zoneBrowser && <ZoneBrowser key={`${zoneBrowser.seat}-${zoneBrowser.zone}`} opened={zoneBrowser}
        player={asRecord(players[zoneBrowser.seat])} dialogRef={zoneDialogRef} view={view}
        selection={publicSelection} selectedCardRef={selectedCardRef} actionsForCard={actionsForCard}
        actionDisabled={actionDisabled} onClose={() => setZoneBrowser(null)}
        onIntent={(actions, card) => { setZoneBrowser(null); selectCardActions(actions, card); }} />}
      {keyboardPinnedCard && <PinnedTableViewer value={keyboardPinnedCard} view={view} onClose={closeKeyboardPinnedCard} />}
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
                  : selectedAction.action === "concede" ? "Leave as a player and stay to watch the table."
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
                onInspect={setInspectedCard}
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
                {!submitting && selectedAction.action === "cast" && selectedAction.form
                  && castsWithNoXTargets(selectedAction.form, choiceValues)
                  ? "Cast with no targets"
                  : choiceSubmitLabel(selectedAction, manualMana, submitting, choiceValues.mana_pool_payment)}
              </button>
            </div>
          </form>
        </div>
      )}
      <TargetArrowLayer sourceRef={draftSource || stackSource}
        targets={draftSource ? draftTargets : stackTargets} candidates={currentStep?.group ? publicSelection?.refs ?? [] : []}
        zones={zones} stackPositions={stackPositions} attackPairs={attackPairs} seats={arrowSeats} />
      {boardsWindow.host && createPortal(
        <main className="game-shell detached-table" data-custom-colors={colorStyle ? "true" : undefined} style={tableStyle}>
          <header className="detached-header">
            <strong>Opponent boards</strong>
            <span>Inspect cards and select targets here. Confirm actions on the main table.</span>
            <div className="detached-seat-links" aria-label="Seats on the main table">
              {orderedPlayers.filter(([seat]) => !detached.has(seat)).map(([seat]) =>
                <span key={seat} data-seat-anchor={seat}><SeatLabel seat={seat} /></span>)}
            </div>
          </header>
          <div className="table-workspace has-stack-rail has-active-stack">
            <div className="detached-boards" aria-label="Detached opponent battlefields">
              {opponents.filter(([seat]) => detached.has(seat)).map(([seat, player]) =>
                <PlayerBoard key={seat} seat={seat} player={asRecord(player)} active={activeSeat === seat} priority={prioritySeat === seat}
                  mine={false} hidden={false} view={view} labelFor={labelFor} cardActions={[]} attachments={boardAttachments}
                  onInspectCard={inspectDetached} highlightedTargets={detachedTargets}
                  selection={publicSelection} selectedCardRef={selectedCardRef} dropEnabled={false} getDraggedCard={() => ""} dragRelease={0} />)}
            </div>
            <aside className="table-sidebar" aria-label="Stack and card viewer">
              <StackPanel stack={stack} view={view} labelFor={labelFor} positions={stackPositions} targets={detachedTargets}
                onFocusItem={setDetachedStackFocus} onInspect={inspectDetached} selection={publicSelection} />
              <CardInspector value={detachedInspection} view={view} />
            </aside>
          </div>
          <TargetArrowLayer sourceRef={String(detachedStack.id ?? "")} targets={detachedTargets} candidates={[]}
            zones={zones} stackPositions={stackPositions} attackPairs={attackPairs} seats={arrowSeats} host={boardsWindow.host} />
        </main>, boardsWindow.host
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
  if (screen === "game" && gameId) return <GameView key={`${guest?.guest_id ?? ""}:${gameId}`} gameId={gameId}
    userId={guest?.guest_id ?? ""} system={system} onExit={leaveRoomScreen} onRoom={enterRoom} />;
  return null;
}
