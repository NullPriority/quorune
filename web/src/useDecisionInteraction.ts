import { useEffect, useRef, useState } from "react";
import { api } from "./api";
import { executableChoices, initialChoices, record, restoreCastChoices, validateChoices, type ChoiceValues } from "./choices";
import type { CommandEnvelope, LegalAction } from "./generated/protocol";
import type { MoveFeedback } from "./GameMenu";
import type { ProjectedView } from "./protocol";
import { actionTitle } from "./tablePresentation";

type Draft = {
  decisionId: string;
  viewRevision: number;
  action: LegalAction;
  values: ChoiceValues;
  errors: string[];
};

type Submission =
  | { kind: "submitting" }
  | { kind: "awaiting"; revision: number }
  | { kind: "uncertain"; envelope: CommandEnvelope; label: string };

export function useDecisionInteraction({
  view, gameId, enabled, onNotice,
}: {
  view: ProjectedView | null;
  gameId: string;
  enabled: boolean;
  onNotice: (message: string) => void;
}) {
  const [draft, setDraft] = useState<Draft | null>(null);
  const [parked, setParked] = useState<{ draft: Draft; boundary: string } | null>(null);
  const [submission, setSubmission] = useState<Submission | null>(null);
  const [lastMove, setLastMove] = useState<MoveFeedback | null>(null);
  const commitment = useRef<Submission | null>(null);
  const current = useRef(view);
  current.current = view;
  const decision = view?.decision;
  const turn = record(view?.state.turn);
  const boundary = `${turn.seq}/${turn.phase}/${turn.step}`;
  const stale = draft !== null && (draft.decisionId !== decision?.id || draft.viewRevision !== view?.viewRevision);
  const active = stale ? null : draft;

  useEffect(() => {
    if (parked && parked.boundary !== boundary) {
      setParked(null);
      onNotice("The phase changed while paying mana. Start the cast again when it is offered.");
    }
  }, [parked, boundary, onNotice]);

  function updateSubmission(next: Submission | null) {
    commitment.current = next;
    setSubmission(next);
  }

  useEffect(() => {
    if (stale) {
      setDraft(null);
      if (!commitment.current) onNotice("The table changed. Start the action again with the current choices.");
    }
  }, [stale, onNotice]);

  useEffect(() => {
    if (submission?.kind === "awaiting" && view && view.viewRevision >= submission.revision) {
      updateSubmission(null);
    }
  }, [submission, view]);

  function openDraft(action: LegalAction) {
    if (!enabled || !view?.decision || commitment.current) return;
    setDraft({
      action,
      decisionId: view.decision.id,
      viewRevision: view.viewRevision,
      values: action.form ? initialChoices(action.form) : {},
      errors: [],
    });
  }

  function closeDraft() {
    setDraft(null);
  }

  function stageChoices(values: ChoiceValues) {
    setDraft((existing) => existing ? { ...existing, values, errors: [] } : null);
  }

  function parkDraft() {
    if (!active || parked || active.action.action !== "cast" || commitment.current) return false;
    setParked({ draft: active, boundary });
    closeDraft();
    return true;
  }

  const parkedOffer = parked && parked.boundary === boundary
    ? decision?.legal_actions.find((action) => action.id === parked.draft.action.id) : undefined;

  function resumeDraft() {
    if (!parked || !parkedOffer || !decision || !view || !enabled || active || commitment.current) return;
    const form = parkedOffer.form;
    const values = form ? restoreCastChoices(form, parked.draft.values) : {};
    setDraft({
      action: parkedOffer, decisionId: decision.id, viewRevision: view.viewRevision,
      values, errors: form ? validateChoices(form, values) : [],
    });
    if (JSON.stringify(parked.draft.values) !== JSON.stringify(values)) {
      onNotice("Review the choices available after adding mana.");
    }
    setParked(null);
  }

  async function send(envelope: CommandEnvelope, label: string) {
    const started = performance.now();
    updateSubmission({ kind: "submitting" });
    try {
      const { receipt } = await api.command(gameId, envelope);
      setLastMove({
        interactionId: envelope.command_id,
        revision: receipt.state_revision,
        action: envelope.action_id.split(":", 1)[0],
        receipt: receipt.code,
        roundTripMs: Math.round(performance.now() - started),
      });
      if (!receipt.ok) {
        updateSubmission(null);
        const expired = ["stale_decision", "stale_view", "stale_action", "unauthorized_capability"].includes(receipt.code);
        if (expired) closeDraft();
        onNotice(expired
          ? "That action expired. Review the current table and try again."
          : receipt.summary);
        return;
      }
      closeDraft();
      onNotice("");
      updateSubmission(receipt.state_changed && (current.current?.viewRevision ?? -1) < receipt.state_revision
        ? { kind: "awaiting", revision: receipt.state_revision } : null);
    } catch {
      updateSubmission({ kind: "uncertain", envelope, label });
      onNotice("Delivery is uncertain. Retry the same move to recover its result.");
    }
  }

  function act(action: LegalAction, choices: ChoiceValues = {}) {
    if (!enabled || !view?.decision || commitment.current) return;
    const currentAction = view.decision.legal_actions.find((row) => row.id === action.id);
    if (!currentAction) return;
    const errors = currentAction.form ? validateChoices(currentAction.form, choices) : [];
    if (errors.length > 0) {
      onNotice(errors.join(" "));
      return;
    }
    void send({
      protocol_version: "3.0",
      game_id: gameId,
      command_id: `web-${crypto.randomUUID()}`,
      decision_id: view.decision.id,
      action_id: action.id,
      capability: view.decision.cap,
      expected_view_revision: view.viewRevision,
      choices,
    }, actionTitle(action));
  }

  function submitDraft() {
    if (!active) return;
    const { action, values } = active;
    const errors = action.form ? validateChoices(action.form, values) : [];
    if (errors.length > 0) {
      setDraft({ ...active, errors });
      return;
    }
    act(action, action.form ? executableChoices(action.form, values) : {});
  }

  function retry() {
    const uncertain = commitment.current;
    if (enabled && uncertain?.kind === "uncertain") void send(uncertain.envelope, uncertain.label);
  }

  return {
    draft: active, openDraft, closeDraft, stageChoices, submitDraft, act, retry, lastMove,
    pendingRetry: submission?.kind === "uncertain" ? submission : null,
    submitting: submission?.kind === "submitting" || submission?.kind === "awaiting",
    awaitingProjection: submission?.kind === "awaiting",
    parkedAction: parked?.draft.action ?? null,
    parkDraft, resumeDraft, canResumeDraft: Boolean(parkedOffer) && enabled && !active && !submission,
    dismissParkedDraft: () => setParked(null),
  };
}
