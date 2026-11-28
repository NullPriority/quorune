import { useEffect, useMemo, useRef, useState, type RefObject } from "react";
import { useTableLoop, type ObservedTableAction } from "./useTableLoop";
import { api } from "./api";
import { asList, asRecord } from "./CardSurface";
import type { ChoiceValues } from "./choices";
import type { LegalAction } from "./generated/protocol";
import type { ProjectedView } from "./protocol";
import {
  activationStopSignature, findSafeAutoPass, hasPriorityResponse, isIgnoredActivation, ownSpellResponseHold,
  priorityActivityHold, priorityHoldForScope, readSourcePriorityYieldOffer, remainsOfCapturedStack,
  sourcePriorityYieldStatus,
  selectedPriorityStop, skipsOptionalActivation, type EmptyPriorityHolds, type PriorityActivity,
} from "./tableAutomation";
import { priorityStopsForTurn, type TablePreferences } from "./tablePreferences";
import { tableSeat } from "./SeatLabel";

export function useTablePriorityLoop({ observer, busy, ...table }: {
  gameId: string;
  view: ProjectedView | null;
  active: boolean;
  online: boolean;
  busy: boolean;
  preferences: TablePreferences;
  act: (action: LegalAction, choices?: ChoiceValues) => boolean;
  observer: RefObject<((result: ObservedTableAction) => void) | null>;
}) {
  const priorityHeld = useRef(false);
  const holdsSynced = useRef(false);
  const loop = useTableLoop({ view: table.view, enabled: table.active && table.online, busy,
    observer, priorityHeld, holdsSynced, act: table.act });
  const priorityAutomation = useTablePriority({
    ...table,
    blocked: busy || (loop.loop.kind !== "off" && loop.loop.kind !== "held"),
    externalHold: loop.loop.kind === "held",
    preserveEmpty: loop.loop.kind !== "off",
  });
  priorityHeld.current = Boolean(priorityAutomation.held) && loop.loop.kind !== "held";
  holdsSynced.current = priorityAutomation.sync.synced;
  return { loop, priorityAutomation };
}

function publicPriorityActivity(view: ProjectedView | null): PriorityActivity {
  const stack = asList(view?.state.stack).map(asRecord);
  const zones = Object.entries(asRecord(view?.state.players)).sort(([a], [b]) => a.localeCompare(b)).map(([seat, player]) => [
    seat, asList(asRecord(player).gy).map((card) => String(asRecord(card).id)).sort(),
  ] as const);
  const graveyardRefs = new Set(zones.flatMap(([, refs]) => refs));
  return {
    stack: stack.map((item) => String(item.id)),
    graveyard: JSON.stringify([asRecord(view?.state.turn).graveyard_activity ?? 0, zones]),
    graveyardStack: stack.filter((item) => item.from === "graveyard" || item.graveyard_interaction === true
      || asList(item.targets).some((target) => graveyardRefs.has(String(target)))).map((item) => String(item.id)),
  };
}

function useEmptyPriorityHolds(gameId: string, required: boolean, online: boolean, holds: EmptyPriorityHolds) {
  const request = JSON.stringify(holds);
  const pending = useRef(Promise.resolve());
  const [error, setError] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const desired = useMemo(() => ({ gameId, online, required, request, attempt }), [gameId, online, required, request, attempt]);
  const [synced, setSynced] = useState<typeof desired | null>(null);
  useEffect(() => {
    if (!desired.online || !desired.required) return;
    let current = true;
    setError(false);
    pending.current = pending.current.then(async () => {
      if (!current) return;
      try {
        await api.priorityHolds(desired.gameId, JSON.parse(desired.request) as EmptyPriorityHolds);
        if (current) setSynced(desired);
      } catch {
        if (current) setError(true);
      }
    });
    return () => { current = false; };
  }, [desired]);
  return { synced: !required || synced === desired, error, retry: () => setAttempt((value) => value + 1) };
}

export function useTablePriority({ gameId, view, active, online, blocked, externalHold = false, preserveEmpty = false, preferences, act }: {
  gameId: string;
  view: ProjectedView | null;
  active: boolean;
  online: boolean;
  blocked: boolean;
  externalHold?: boolean;
  preserveEmpty?: boolean;
  preferences: TablePreferences;
  act: (action: LegalAction, choices?: ChoiceValues) => boolean;
}) {
  const [yieldedTurn, setYieldedTurn] = useState<number | null>(null);
  const [firstMainTurn, setFirstMainTurn] = useState<number | null>(null);
  const [holdAfterCancel, setHoldAfterCancel] = useState(false);
  const [stackYield, setStackYield] = useState<{ turn: number; stack: readonly string[]; firstWindow: string } | null>(null);
  const [priorityPause, setPriorityPause] = useState<string | null>(null);
  const [sourceYield, setSourceYield] = useState<{ gameId: string; scope: string } | null>(null);
  const [ignored, setIgnored] = useState<{ turn: number; offers: ReadonlyMap<string, string> }>();
  const [skippedForGame, setSkippedForGame] = useState<ReadonlyMap<string, string>>(() => new Map());
  const acknowledged = useRef<PriorityActivity | null>(null);
  const lastAutomaticPass = useRef("");
  const yieldedSourceItems = useRef(new Set<string>());
  const turn = asRecord(view?.state.turn);
  const number = Number(turn.seq ?? 0);
  const phase = String(turn.phase ?? "");
  const decision = view?.decision;
  const priority = decision?.kind === "priority";
  const offers = decision?.legal_actions ?? [];
  const explicitPass = offers.find((action) => action.action === "pass");
  const activity = useMemo(() => publicPriorityActivity(view), [view]);
  const stack = useMemo(() => asList(view?.state.stack).map(asRecord), [view]);
  const stackFacts = useMemo(() => stack.map((item) => JSON.stringify(item)), [stack]);
  const decisionKey = priority ? [decision.id, view?.viewRevision].join("/") : "";
  const ownSeat = decision?.actor ?? view?.principal.split(":").at(-1);
  const stackInScope = stackYield !== null && stackYield.turn === number && stackFacts.length > 0
    && remainsOfCapturedStack(stackYield.stack, stackFacts);
  const firstStackWindow = stackInScope && decisionKey === stackYield?.firstWindow;
  const stackInterruption = stackYield && (decision && !priority ? "Required choice"
    : stackFacts.length > 0 && !stackInScope ? "Stack changed"
    : stackInScope && priority && !firstStackWindow && hasPriorityResponse(offers) ? "Response available"
    : undefined);
  const resolvingStack = stackInScope && !stackInterruption;
  const canResolveStack = priority && Boolean(explicitPass) && !blocked && !externalHold && stackFacts.length > 0;
  const armed = yieldedTurn !== null && yieldedTurn === number;
  const firstMainRequested = firstMainTurn === number;
  const beforeFirstMain = number > 0 && phase === "beginning";
  const advancingToFirstMain = firstMainRequested && beforeFirstMain;
  const holdingFirstMain = firstMainRequested && phase === "precombat_main";
  const activelyPassing = armed || advancingToFirstMain;
  const selectedSourceScope = sourceYield?.gameId === gameId ? sourceYield.scope : undefined;
  const sourceYieldOffer = readSourcePriorityYieldOffer(stack[0]?.source_priority_yield);
  const sourceStatus = sourcePriorityYieldStatus(selectedSourceScope, sourceYieldOffer, decision?.kind, offers,
    activity.stack.some((ref) => yieldedSourceItems.current.has(ref)));
  const sourcePassing = sourceStatus === "ready";
  const sourcePause = sourceStatus === "targeted" ? "Source became a target"
    : sourceStatus === "response" ? "Response available"
    : sourceStatus === "choice" ? "Required choice"
    : sourceStatus === "changed" ? "Stack changed" : undefined;
  const canYieldSource = !blocked && !externalHold && !activelyPassing && !resolvingStack
    && sourcePriorityYieldStatus(sourceYieldOffer?.id, sourceYieldOffer, decision?.kind, offers) === "ready";
  const ignoredOffers = new Map(ignored?.turn === number ? ignored.offers : undefined);
  for (const offer of offers) {
    if (isIgnoredActivation(offer, skippedForGame)) ignoredOffers.set(offer.id, activationStopSignature(offer));
  }
  const relevantStops = priorityStopsForTurn(preferences, tableSeat(turn.active));
  // The current server can preserve steps globally for this principal. Preserve
  // the union and apply the saved active-seat filter before passing locally.
  const serverStops = new Set([...preferences.priorityStops, ...Object.values(preferences.seatPriorityStops).flat()]);
  if (firstMainRequested) serverStops.add("precombat_main");
  const sync = useEmptyPriorityHolds(gameId, turn.fast_empty_priority === true && view?.principal !== "spectator", active && online, {
    steps: [...serverStops],
    preserve_empty: preserveEmpty || externalHold || holdAfterCancel || Boolean(priorityPause)
      || !preferences.autoPass || activelyPassing || stackYield !== null || Boolean(selectedSourceScope)
      || preferences.holdNewStack || preferences.holdGraveyard,
  });

  const combat = asRecord(view?.state.combat);
  const ordinaryHold = (holdingFirstMain ? "First main phase" : undefined)
    ?? selectedPriorityStop(phase, String(turn.step ?? ""), Boolean(combat.first_strike_step),
      Number(combat.damage_step ?? 0), relevantStops,
      turn.next_player === view?.principal.split(":").at(-1), Object.keys(asRecord(combat.atk)).length > 0)
    ?? (holdAfterCancel ? "Pass turn canceled" : stackInterruption || sourcePause || priorityPause
      || (!firstStackWindow && ownSpellResponseHold(stack[0], ownSeat, offers) ? "Response to your spell" : undefined)
      || priorityActivityHold(acknowledged.current ?? activity, activity, {
        stack: preferences.holdNewStack && !sourcePassing, graveyard: preferences.holdGraveyard,
      }));
  const held = priority ? priorityHoldForScope(armed, externalHold, ordinaryHold) : undefined;
  const window = {
    activePlayer: String(turn.active ?? ""), phase, principalSeat: ownSeat,
    stackDepth: activity.stack.length, ignoredActivations: ignoredOffers,
    passWithOptionalActivations: preferences.passWithOptionalActivations,
  };
  const pass = activelyPassing || resolvingStack || sourcePassing ? explicitPass : findSafeAutoPass(offers, window);

  useEffect(() => {
    if (view && !acknowledged.current) acknowledged.current = activity;
    if (yieldedTurn !== null && !armed) setYieldedTurn(null);
    if (armed && decision && !priority) {
      setYieldedTurn(null);
      setPriorityPause("Required choice");
      return;
    }
    if (firstMainTurn !== null && (!firstMainRequested || !beforeFirstMain && !holdingFirstMain)) setFirstMainTurn(null);
    if (sourceYield && sourceYield.gameId !== gameId) setSourceYield(null);
    if (sourcePause) {
      setSourceYield(null);
      setPriorityPause(sourcePause);
      return;
    }
    if (stackYield && (!stackInScope || stackInterruption)) {
      setStackYield(null);
      if (stackInterruption) setPriorityPause(stackInterruption);
      return;
    }
    if (!active || !online || blocked || !sync.synced
      || (!preferences.autoPass && !activelyPassing && !resolvingStack && !sourcePassing)
      || !priority || !pass || held) return;
    if (lastAutomaticPass.current === decisionKey) return;
    const ignoredIds = resolvingStack || sourcePassing ? [] : offers.filter((action) => skipsOptionalActivation(action, {
      ...window, passWithOptionalActivations: activelyPassing || window.passWithOptionalActivations,
    })).map((action) => action.id);
    if (act(pass, activelyPassing || resolvingStack || sourcePassing || ignoredIds.length ? {
      pass_turn_sequence: number,
      ...(ignoredIds.length ? { ignored_activation_ids: ignoredIds } : {}),
    } : {})) {
      lastAutomaticPass.current = decisionKey;
      acknowledged.current = activity;
      if (sourcePassing) yieldedSourceItems.current = new Set(activity.stack.filter(
        (ref, index) => index === 0 || yieldedSourceItems.current.has(ref),
      ));
    }
  });

  function toggleSourceYield() {
    if (selectedSourceScope) {
      setSourceYield(null);
      setPriorityPause("Source yield canceled");
    } else if (canYieldSource && sourceYieldOffer) {
      acknowledged.current = activity;
      setPriorityPause(null);
      setHoldAfterCancel(false);
      setSourceYield({ gameId, scope: sourceYieldOffer.id });
      yieldedSourceItems.current = new Set(activity.stack.slice(0, 1));
    }
  }

  function toggleStackResolve() {
    if (resolvingStack) {
      setStackYield(null);
      setPriorityPause("Resolve stack canceled");
      return;
    }
    if (!canResolveStack) return;
    setSourceYield(null);
    acknowledged.current = activity;
    setHoldAfterCancel(false);
    setPriorityPause(null);
    setYieldedTurn(null);
    setFirstMainTurn(null);
    setStackYield({ turn: number, stack: stackFacts, firstWindow: decisionKey });
  }

  function toggleTurnYield() {
    if (!activelyPassing && decision && !priority) return;
    setSourceYield(null);
    setFirstMainTurn(null);
    setStackYield(null);
    setPriorityPause(null);
    if (activelyPassing) {
      setYieldedTurn(null);
      setHoldAfterCancel(true);
    } else {
      acknowledged.current = activity;
      setHoldAfterCancel(false);
      setYieldedTurn(number);
    }
  }

  function advanceToFirstMain() {
    if (!beforeFirstMain) return;
    setSourceYield(null);
    acknowledged.current = activity;
    setHoldAfterCancel(false);
    setYieldedTurn(null);
    setStackYield(null);
    setPriorityPause(null);
    setFirstMainTurn(number);
  }

  function acknowledgeManualPass() {
    if (holdingFirstMain) setFirstMainTurn(null);
    setPriorityPause(null);
    acknowledged.current = activity;
    setHoldAfterCancel(false);
    if (!preferences.autoPass || held) return;
    const passive = findSafeAutoPass(offers, { ...window, passWithOptionalActivations: true, allowActiveMain: true });
    if (!passive) return;
    setIgnored((before) => {
      const remembered = new Map(before?.turn === number ? before.offers : undefined);
      for (const offer of offers) {
        if (offer.action === "activate" && offer.mana_ability !== true) {
          remembered.set(offer.id, activationStopSignature(offer));
        }
      }
      return { turn: number, offers: remembered };
    });
  }

  function resetYield() {
    setSourceYield(null);
    setFirstMainTurn(null);
    setYieldedTurn(null);
    setHoldAfterCancel(false);
    setStackYield(null);
    setPriorityPause(null);
    lastAutomaticPass.current = "";
    yieldedSourceItems.current.clear();
  }

  return {
    armed, advancingToFirstMain, canAdvanceToFirstMain: beforeFirstMain && !advancingToFirstMain,
    advanceToFirstMain, held, turnNumber: number, toggleTurnYield, acknowledgeManualPass, resetYield,
    canResolveStack, resolvingStack, toggleStackResolve,
    canArmPassTurn: !decision || priority,
    canYieldSource, sourceYieldEnabled: Boolean(selectedSourceScope), toggleSourceYield,
    openingHoldsPending: number === 0 && !sync.synced && !sync.error,
    waitingOnRequiredChoice: activelyPassing && Boolean(decision && !priority),
    isSkippingActivation: (action: LegalAction) => isIgnoredActivation(action, skippedForGame),
    skipActivation: (action: LegalAction, skip: boolean) => setSkippedForGame((before) => {
      const offers = new Map(before);
      if (skip) offers.set(action.id, activationStopSignature(action));
      else offers.delete(action.id);
      return offers;
    }),
    sync,
  };
}
