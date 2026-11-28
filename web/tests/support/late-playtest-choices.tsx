import { useState } from "react";
import { createRoot } from "react-dom/client";
import { ExileCastChoice } from "../../src/ExileCastChoice";
import { ContextualDecision } from "../../src/ContextualDecision";
import { ChoiceFormView } from "../../src/ChoiceForm";
import { initialChoices } from "../../src/choices";
import type { ChoiceForm, Decision, LegalAction } from "../../src/generated/protocol";
import type { ProjectedView } from "../../src/protocol";

type Surface = "cascade" | "targeted-cascade" | "target" | "printing" | "commander";

const targetField = {
  name: "targets", label: "Target artifact or enchantment", control: "targets",
  schema: { groups: [{
    id: "permanent", min: 1, max: 1, legal_refs: ["C1", "B1", "C2"],
  }] },
};

function ChoiceSurface({ mode }: { mode: Surface }) {
  const cascade = mode === "cascade" || mode === "targeted-cascade";
  const printing = { set_code: "j22", collector_number: "835", language: "zht", finish: "etched",
    scryfall_id: "8b71345a-c3e8-4b35-beb7-6347e41d7626" };
  const land = { id: "forest", n: "Temple Garden", cid: "12345678", printing };
  const spell = { id: "spell", n: "Harmonic Sliver", cid: "23456789", t: "Creature — Sliver" };
  const form: ChoiceForm = mode === "commander" ? { v: 1, submit_label: "Choose replacement", fields: [
    { name: "replacement", label: "Replacement", control: "select", required: true, options: [
      { value: "command", label: "Move to command zone" }, { value: "decline", label: "Return to hand" },
    ] },
  ] } : mode === "printing" ? { v: 1, submit_label: "Find land", fields: [
    { name: "search_cards", label: "Search cards", control: "refs", minimum: 0, maximum: 1,
      options: [{ value: "forest", label: land.n }] },
  ] } : { v: 1, submit_label: "Choose targets", fields: [targetField] };
  const cast: LegalAction = { id: "cast", action: "choose", form: {
    v: 1, fields: [], variants: {
      field: "cost_option", default: "without_mana_cost",
      options: [{ value: "without_mana_cost", fields: mode === "targeted-cascade" ? [targetField] : [] }],
    },
  } };
  const decision: Decision = {
    id: "decision", kind: cascade ? "selection.exile_cast" : mode === "target" ? "semantic.target"
      : mode === "commander" ? "replacement.order" : "semantic.search",
    actor: "A", cap: "synthetic", allow: ["choose"],
    legal_actions: cascade ? [cast, { id: "decline", action: "choose" }] : [{ id: "choose", action: "choose", form }],
    ctx: cascade ? { card: spell.id } : mode === "printing" ? { preview_cards: [land] } : {},
  };
  const view: ProjectedView = {
    principal: "pilot:A", decision, viewHash: "", viewRevision: 1, packetNumber: 1, events: [],
    definitions: {}, state: { players: {
      A: { ex: [spell] },
      B: { bf: [{ id: "B1", n: "Sol Ring" }] },
      C: { bf: [{ id: "C1", n: "Arcane Signet" }, { id: "C2", n: "Mishra’s Bauble" }] },
    } },
  };
  const labels: Record<string, string> = { B1: "Sol Ring", C1: "Arcane Signet", C2: "Mishra’s Bauble" };
  const [review, setReview] = useState<LegalAction | null>(null);
  const [values, setValues] = useState(() => initialChoices(cast.form!));
  return <main className="game-shell">
    {cascade && !review ? <ExileCastChoice view={view} disabled={false} onInspect={() => undefined}
      onAnswer={(action, choices) => {
        if (choices === null) setReview(action);
        else document.body.dataset.answer = JSON.stringify({ action: action.id, choices });
      }} />
      : review?.form ? <ChoiceFormView form={review.form} values={values} view={view} onChange={setValues}
        labelFor={(ref) => labels[ref] ?? ref} />
      : <ContextualDecision action={decision.legal_actions[0]} view={view} disabled={false} submitting={false}
        labelFor={(ref) => labels[ref] ?? ref} onSubmit={(action, choices) => {
          document.body.dataset.answer = JSON.stringify({ action: action.id, choices });
        }} />}
  </main>;
}

export function mountLatePlaytestChoices(mode: Surface) {
  const node = document.createElement("div");
  document.body.appendChild(node);
  document.getElementById("root")?.setAttribute("hidden", "");
  createRoot(node).render(<ChoiceSurface mode={mode} />);
}
