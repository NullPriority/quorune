import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "./api";
import { asList, asRecord } from "./CardSurface";
import type { ChoiceValues } from "./choices";
import type { LegalAction } from "./generated/protocol";
import type { ProjectedView } from "./protocol";
import {
  activationStopSignature, findSafeAutoPass, isIgnoredActivation, priorityActivityHold,
  selectedPriorityStop, skipsOptionalActivation, type EmptyPriorityHolds, type PriorityActivity,
} from "./tableAutomation";
import type { TablePreferences } from "./tablePreferences";

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

export function useTablePriority({ gameId, view, active, online, blocked, preferences, act }: {
  gameId: string;
  view: ProjectedView | null;
  active: boolean;
  online: boolean;
  blocked: boolean;
  preferences: TablePreferences;
  act: (action: LegalAction, choices?: ChoiceValues) => boolean;
}) {
  const [yieldedTurn, setYieldedTurn] = useState<number | null>(null);
  const [holdAfterCancel, setHoldAfterCancel] = useState(false);
  const [ignored, setIgnored] = useState<{ turn: number; offers: ReadonlyMap<string, string> }>();
  const acknowledged = useRef<PriorityActivity | null>(null);
  const lastAutomaticPass = useRef("");
  const turn = asRecord(view?.state.turn);
  const number = Number(turn.seq ?? 0);
  const phase = String(turn.phase ?? "");
  const decision = view?.decision;
  const activity = useMemo(() => publicPriorityActivity(view), [view]);
  const armed = yieldedTurn !== null && yieldedTurn === number;
  const ignoredOffers = ignored?.turn === number ? ignored.offers : undefined;
  const sync = useEmptyPriorityHolds(gameId, turn.fast_empty_priority === true && view?.principal !== "spectator", active && online, {
    steps: preferences.priorityStops,
    preserve_empty: holdAfterCancel || (!preferences.autoPass && !armed) || preferences.holdNewStack || preferences.holdGraveyard,
  });

  const priority = decision?.kind === "priority";
  const combat = asRecord(view?.state.combat);
  const held = priority ? selectedPriorityStop(phase, String(turn.step ?? ""), Boolean(combat.first_strike_step),
    Number(combat.damage_step ?? 0), preferences.priorityStops)
    ?? (holdAfterCancel ? "Pass turn canceled" : priorityActivityHold(acknowledged.current ?? activity, activity, {
      stack: preferences.holdNewStack, graveyard: preferences.holdGraveyard,
    })) : undefined;
  const offers = decision?.legal_actions ?? [];
  const window = {
    activePlayer: String(turn.active ?? ""), phase, principalSeat: decision?.actor ?? view?.principal.split(":").at(-1),
    stackDepth: activity.stack.length, ignoredActivations: ignoredOffers,
    passWithOptionalActivations: preferences.passWithOptionalActivations,
  };
  const pass = armed ? offers.find((action) => action.action === "pass") : findSafeAutoPass(offers, window);

  useEffect(() => {
    if (view && !acknowledged.current) acknowledged.current = activity;
    if (yieldedTurn !== null && !armed) setYieldedTurn(null);
    if (!active || !online || blocked || !sync.synced || (!preferences.autoPass && !armed)
      || !priority || !pass || held) return;
    const key = `${decision.id}/${view?.viewRevision}`;
    if (lastAutomaticPass.current === key) return;
    const ignoredIds = offers.filter((action) => skipsOptionalActivation(action, {
      ...window, passWithOptionalActivations: armed || window.passWithOptionalActivations,
    })).map((action) => action.id);
    if (act(pass, armed || ignoredIds.length ? {
      pass_turn_sequence: number,
      ...(ignoredIds.length ? { ignored_activation_ids: ignoredIds } : {}),
    } : {})) {
      lastAutomaticPass.current = key;
      acknowledged.current = activity;
    }
  });

  function toggleTurnYield() {
    if (armed) {
      setYieldedTurn(null);
      setHoldAfterCancel(true);
    } else {
      acknowledged.current = activity;
      setHoldAfterCancel(false);
      setYieldedTurn(number);
    }
  }

  function acknowledgeManualPass() {
    acknowledged.current = activity;
    setHoldAfterCancel(false);
  }

  function resetYield() {
    setYieldedTurn(null);
    setHoldAfterCancel(false);
    lastAutomaticPass.current = "";
  }

  return {
    armed, held, turnNumber: number, toggleTurnYield, acknowledgeManualPass, resetYield,
    openingHoldsPending: number === 0 && !sync.synced && !sync.error,
    waitingOnRequiredChoice: armed && Boolean(decision && !priority),
    isIgnored: (action: LegalAction) => isIgnoredActivation(action, ignoredOffers),
    setIgnored: (action: LegalAction, skip: boolean) => setIgnored((before) => {
      const offers = new Map(before?.turn === number ? before.offers : undefined);
      if (skip) offers.set(action.id, activationStopSignature(action));
      else offers.delete(action.id);
      return { turn: number, offers };
    }),
    sync,
  };
}
