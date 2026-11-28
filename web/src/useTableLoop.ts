import { useEffect, useRef, useState, type RefObject } from "react";
import type { LegalAction } from "./generated/protocol";
import type { ProjectedView } from "./protocol";
import type { ChoiceValues } from "./choices";
import { loopFrame, opponentResponded, repeatableOffer } from "./loopShortcuts";

export type ObservedTableAction = {
  before: ProjectedView;
  action: LegalAction;
  choices: ChoiceValues;
  accepted: boolean;
  revision: number;
};

type Step = { frame: string; offer: string; id: string; action: string; choices: ChoiceValues };
type Loop =
  | { kind: "off"; message?: string }
  | { kind: "recording"; frame: string; turn: number | null; steps: Step[]; revision: number }
  | { kind: "ready"; steps: Step[]; turn: number | null }
  | { kind: "running"; steps: Step[]; turn: number | null; index: number; remaining: number; revision: number;
      pending: boolean; hold: boolean; afterEvent: number }
  | { kind: "held"; message?: string };
function turnNumber(view: ProjectedView): number | null {
  const turn = view.state.turn;
  return turn && !Array.isArray(turn) && typeof turn === "object" && typeof turn.seq === "number" ? turn.seq : null;
}

export function useTableLoop({ view, enabled, busy, observer, priorityHeld, holdsSynced, act }: {
  view: ProjectedView | null;
  enabled: boolean;
  busy: boolean;
  observer: RefObject<((result: ObservedTableAction) => void) | null>;
  priorityHeld: RefObject<boolean>;
  holdsSynced: RefObject<boolean>;
  act: (action: LegalAction, choices?: ChoiceValues) => boolean;
}) {
  const [loop, display] = useState<Loop>({ kind: "off" });
  const current = useRef(loop);

  function update(next: Loop) {
    current.current = next;
    display(next);
  }

  useEffect(() => {
    if (loop.kind !== "recording" && loop.kind !== "running") return;
    observer.current = (result) => {
      const phase = current.current;
      if (!result.accepted) {
        update({ kind: "off", message: "Loop stopped at the last accepted action." });
        return;
      }
      if (phase.kind === "recording") {
        const frame = loopFrame(result.before);
        if (!frame || turnNumber(result.before) !== phase.turn) {
          update({ kind: "off", message: "Loop recording ended at a different decision or turn." });
          return;
        }
        update({
          ...phase, revision: result.revision,
          steps: [...phase.steps, {
            frame, id: result.action.id, action: result.action.action,
            offer: repeatableOffer(result.action), choices: result.choices,
          }],
        });
      } else if (phase.kind === "running" && phase.pending) {
        if (phase.steps[phase.index].id !== result.action.id) {
          update({ kind: "off", message: "Loop stopped when a different action was accepted." });
          return;
        }
        const ended = phase.index === phase.steps.length - 1;
        update({
          ...phase, pending: false, revision: result.revision,
          index: ended ? 0 : phase.index + 1, remaining: phase.remaining - Number(ended),
        });
      }
    };
    return () => { observer.current = null; };
  }, [loop.kind, observer]);

  useEffect(() => {
    const phase = current.current;
    if (phase.kind === "recording" && view && view.viewRevision >= phase.revision) {
      if (turnNumber(view) !== phase.turn || (view.decision && view.decision.kind !== "priority")) {
        update({ kind: "off", message: "Loop recording ended at a different decision or turn." });
      } else if (phase.steps.some((step) => step.action !== "pass") && loopFrame(view) === phase.frame) {
        update({ kind: "ready", steps: phase.steps, turn: phase.turn });
      }
      return;
    }
    if (phase.kind !== "running" || phase.pending || !enabled || busy || !view
      || view.viewRevision < phase.revision || !holdsSynced.current) return;
    const frame = loopFrame(view);
    const turnChanged = turnNumber(view) !== phase.turn;
    if (turnChanged || (view.decision && !frame) || opponentResponded(view, phase.afterEvent)) {
      update({ kind: "held", message: "Loop stopped because another action or decision changed the sequence." });
      return;
    }
    if (!view.decision) return;
    if (priorityHeld.current) {
      update({ kind: "held", message: "Loop stopped at your priority stop." });
      return;
    }
    const step = phase.steps[phase.index];
    if (phase.remaining === 0 && frame === step.frame) {
      update(phase.hold ? { kind: "held", message: "Loop finished." } : { kind: "off", message: "Loop finished." });
      return;
    }
    const action = view.decision.legal_actions.find((candidate) => candidate.id === step.id);
    if (frame !== step.frame || !action || repeatableOffer(action) !== step.offer) {
      update({ kind: "held", message: "Loop stopped because the table or available choices changed." });
      return;
    }
    const pending = { ...phase, pending: true, afterEvent: Number(view.state.event ?? 0) };
    current.current = pending;
    if (act(action, step.choices)) display(pending);
    else update({ kind: "off", message: "Loop stopped because its next action could not be sent." });
  }, [view, enabled, busy, loop, priorityHeld, holdsSynced, act]);

  function startRecording() {
    const frame = loopFrame(view);
    if (frame && view) update({ kind: "recording", frame, turn: turnNumber(view), steps: [], revision: view.viewRevision });
  }

  function repeat(count: number, hold: boolean) {
    const phase = current.current;
    if (phase.kind === "ready" && Number.isSafeInteger(count) && count > 0 && view) {
      update({ kind: "running", steps: phase.steps, turn: phase.turn, index: 0, remaining: count,
        revision: view.viewRevision, pending: false, hold, afterEvent: Number(view.state.event ?? 0) });
    }
  }

  return {
    loop, canRecord: enabled && !busy && view?.decision?.kind === "priority",
    waitingForPriority: loop.kind === "running" && (!view?.decision || loop.pending),
    holdingPriority: loop.kind === "held" && loopFrame(view) !== null,
    startRecording, repeat,
    holdNow: () => update({ kind: "held" }),
    stop: () => update({ kind: "off" }),
  };
}
