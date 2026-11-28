import { useEffect, useMemo, useRef, useState, type RefObject } from "react";
import { useTableLoop, type ObservedTableAction } from "./useTableLoop";
import { api } from "./api";
import { asList, asRecord } from "./CardSurface";
import type { ChoiceValues } from "./choices";
import type { LegalAction } from "./generated/protocol";
import type { ProjectedView } from "./protocol";
import {
  activationStopSignature, findSafeAutoPass, isIgnoredActivation, priorityActivityHold,
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
  const [ignored, setIgnored] = useState<{ turn: number; offers: ReadonlyMap<string, string> }>();
  const [skippedForGame, setSkippedForGame] = useState<ReadonlyMap<string, string>>(() => new Map());
  const acknowledged = useRef<PriorityActivity | null>(null);
  const lastAutomaticPass = useRef("");
  const turn = asRecord(view?.state.turn);
  const number = Number(turn.seq ?? 0);
  const phase = String(turn.phase ?? "");
  const decision = view?.decision;
  const activity = useMemo(() => publicPriorityActivity(view), [view]);
  const armed = yieldedTurn !== null && yieldedTurn === number;
  const firstMainRequested = firstMainTurn === number;
  const beforeFirstMain = number > 0 && phase === "beginning";
  const advancingToFirstMain = firstMainRequested && beforeFirstMain;
  const holdingFirstMain = firstMainRequested && phase === "precombat_main";
  const activelyPassing = armed || advancingToFirstMain;
  const offers = decision?.legal_actions ?? [];
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
    preserve_empty: preserveEmpty || externalHold || holdAfterCancel || (!preferences.autoPass && !activelyPassing)
      || preferences.holdNewStack || preferences.holdGraveyard,
  });

  const priority = decision?.kind === "priority";
  const combat = asRecord(view?.state.combat);
  const held = priority ? (holdingFirstMain ? "First main phase" : undefined) ?? selectedPriorityStop(phase, String(turn.step ?? ""), Boolean(combat.first_strike_step),
    Number(combat.damage_step ?? 0), relevantStops,
    turn.next_player === view?.principal.split(":").at(-1), Object.keys(asRecord(combat.atk)).length > 0)
    ?? (externalHold ? "Loop shortcut" : holdAfterCancel ? "Pass turn canceled" : priorityActivityHold(acknowledged.current ?? activity, activity, {
      stack: preferences.holdNewStack, graveyard: preferences.holdGraveyard,
    })) : undefined;
  const window = {
    activePlayer: String(turn.active ?? ""), phase, principalSeat: decision?.actor ?? view?.principal.split(":").at(-1),
    stackDepth: activity.stack.length, ignoredActivations: ignoredOffers,
    passWithOptionalActivations: preferences.passWithOptionalActivations,
  };
  const pass = activelyPassing ? offers.find((action) => action.action === "pass") : findSafeAutoPass(offers, window);

  useEffect(() => {
    if (view && !acknowledged.current) acknowledged.current = activity;
    if (yieldedTurn !== null && !armed) setYieldedTurn(null);
    if (firstMainTurn !== null && (!firstMainRequested || !beforeFirstMain && !holdingFirstMain)) setFirstMainTurn(null);
    if (!active || !online || blocked || !sync.synced || (!preferences.autoPass && !activelyPassing)
      || !priority || !pass || held) return;
    const key = `${decision.id}/${view?.viewRevision}`;
    if (lastAutomaticPass.current === key) return;
    const ignoredIds = offers.filter((action) => skipsOptionalActivation(action, {
      ...window, passWithOptionalActivations: activelyPassing || window.passWithOptionalActivations,
    })).map((action) => action.id);
    if (act(pass, activelyPassing || ignoredIds.length ? {
      pass_turn_sequence: number,
      ...(ignoredIds.length ? { ignored_activation_ids: ignoredIds } : {}),
    } : {})) {
      lastAutomaticPass.current = key;
      acknowledged.current = activity;
    }
  });

  function toggleTurnYield() {
    setFirstMainTurn(null);
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
    acknowledged.current = activity;
    setHoldAfterCancel(false);
    setYieldedTurn(null);
    setFirstMainTurn(number);
  }

  function acknowledgeManualPass() {
    if (holdingFirstMain) setFirstMainTurn(null);
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
    setFirstMainTurn(null);
    setYieldedTurn(null);
    setHoldAfterCancel(false);
    lastAutomaticPass.current = "";
  }

  return {
    armed, advancingToFirstMain, canAdvanceToFirstMain: beforeFirstMain && !advancingToFirstMain,
    advanceToFirstMain, held, turnNumber: number, toggleTurnYield, acknowledgeManualPass, resetYield,
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
