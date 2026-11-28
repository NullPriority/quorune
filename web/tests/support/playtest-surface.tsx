import { createRoot } from "react-dom/client";
import { useRef, useState } from "react";
import { PlayerBoard, attachedBattlefieldCards } from "../../src/PlayerBoard";
import { CardTile } from "../../src/CardSurface";
import { ContextualDecision } from "../../src/ContextualDecision";
import { TurnStatus } from "../../src/TurnStatus";
import { PriorityControls } from "../../src/PriorityControls";
import { DEFAULT_TABLE_PREFERENCES } from "../../src/tablePreferences";
import { useTablePriority } from "../../src/useTablePriority";
import { ZoneBrowser } from "../../src/TableZones";
import type { LegalAction } from "../../src/generated/protocol";
import type { ProjectedView } from "../../src/protocol";

const orderAction: LegalAction = {
  id: "order", action: "order",
  form: { v: 1, submit_label: "Confirm order", fields: [{
    name: "triggers", control: "refs", ordered: true, minimum: 2, maximum: 2,
    default: ["S1", "S2"], options: [
      { value: "S1", label: "Draw a card" },
      { value: "S2", label: "Create a token" },
    ],
  }] },
};
const decision = {
  kind: "trigger.order", actor: "C", id: "decision", legal_actions: [orderAction],
  ctx: { preview_cards: [
    { id: "S1", n: "First source", cid: "first" },
    { id: "S2", n: "Second source", cid: "second" },
  ] },
};
const view = {
  principal: "pilot:C", state: { players: {} },
  decision, definitions: {
    first: { cid: "first", n: "First source", o: "Draw a card.", t: "Creature" },
    second: { cid: "second", n: "Second source", o: "Create a token.", t: "Artifact" },
  },
} as unknown as ProjectedView;

const attackAction: LegalAction = {
  id: "attack", action: "attack", form: { v: 1, submit_label: "Confirm attackers", fields: [{
    name: "attackers", control: "assignment_map", rows: Array.from({ length: 30_004 }, (_, index) => ({
      value: `token-${index}`, options: [{ value: index < 30_000 ? "A" : "B" }],
    })),
  }] },
};
type Mode = "trigger" | "graveyard" | "combat" | "bulk" | "board" | "priority" | "costs";

function OptionalActivation() {
  const [turn, setTurn] = useState(1);
  const [passes, setPasses] = useState(0);
  const action: LegalAction = {
    id: "activate:A71:ab1", action: "activate", label: "Emry, Lurker of the Loch · Cast target artifact from your graveyard",
    form: { v: 1, submit_label: "Activate", fields: [{ name: "target", control: "ref",
      options: [{ value: turn > 2 ? "new-target" : "original-target" }] }] },
  };
  const pass = { id: "pass", action: "pass" };
  const priority = useTablePriority({
    gameId: "repeatable-activation", active: true, online: true, blocked: false, preferences: DEFAULT_TABLE_PREFERENCES,
    view: { ...view, principal: "pilot:A", viewRevision: turn,
      decision: { ...view.decision!, actor: "A", kind: "priority", id: String(turn), legal_actions: [pass, action] },
      state: { turn: { seq: turn, phase: "precombat_main", active: "A" } } },
    act: () => { setPasses((count) => count + 1); return true; },
  });
  return <>
    <output aria-label="Automatic passes">{passes}</output>
    <button type="button" onClick={() => setTurn((value) => value + 1)}>Next priority window</button>
    <PriorityControls priority={priority} preferences={DEFAULT_TABLE_PREFERENCES} activations={[action]}
      onToggleStop={() => undefined} onChange={() => undefined} />
  </>;
}

function Surface({ mode }: { mode: Mode }) {
  const dialog = useRef<HTMLElement>(null);
  if (mode === "priority") return <OptionalActivation />;
  if (mode === "bulk") return <ContextualDecision action={attackAction} view={{
    ...view, state: { players: { A: { life: 40 }, B: { life: 40 } } },
    decision: { ...view.decision!, kind: "combat.attackers", legal_actions: [attackAction] },
  }} disabled={false} submitting={false} labelFor={(ref) => ref} onSubmit={(_action, choices) => {
    document.body.dataset.assigned = String(Object.keys(choices.attackers ?? {}).length);
  }} />;
  if (mode === "board" || mode === "costs") {
    const commander = { id: "C06", n: "Progenitus", m: "{W}{W}{U}{U}{B}{B}{R}{R}{G}{G}",
      cmd: 1, cmd_tax: 4, p: 10, q: 10 };
    const cast: LegalAction = { id: "cast:C07", action: "cast", card: "C07",
      cost_options: [{ id: "normal", requirements: { W: 2, U: 2, B: 2, R: 2, G: 2 } }] };
    const player = mode === "costs" ? { cmd: [commander], bf: [] } : { bf: [
      { id: "C01", n: "Forest", cid: "forest" },
      { id: "C02", n: "Snow-Covered Forest", cid: "snow" },
      { id: "C03", n: "Island", cid: "island" },
      { id: "C04", n: "Forest", cid: "forest" },
      { id: "C05", n: "Creature with counters", cid: "growth", t: "Creature", p: 5, q: 6, ctr: { "+1/+1": 3 } },
    ] };
    const definitions = Object.fromEntries([["forest", "G"], ["snow", "G"], ["island", "U"]]
      .map(([cid, mana]) => [cid, { cid, t: "Land", produced_mana: [mana] }]));
    return <div className="game-shell"><section className="boards-grid one-opponent" data-no-opponents>
      <PlayerBoard seat="C" player={player} active priority mine hidden={false}
      view={{ ...view, definitions }} labelFor={(ref) => ref} cardActions={[]}
      attachments={attachedBattlefieldCards({ C: player })}
      onCardIntent={() => undefined} onManaIntent={() => undefined} onInspectCard={() => undefined}
      onOpenInspector={() => undefined} highlightedTargets={[]} selectedCardRef="" onOpenZone={() => undefined}
      dropEnabled={false} onCardDrop={() => undefined} onCardDrag={() => undefined}
      getDraggedCard={() => ""} dragRelease={0} /></section>
      {mode === "costs" && <section className="hand-panel" style={{ width: 106, height: 148 }}>
        <div className="hand-scroll"><div className="hand-cards">
          <CardTile value={{ ...commander, id: "C07" }} view={view} actions={[cast]} onIntent={() => undefined} />
        </div></div>
      </section>}
    </div>;
  }
  if (mode === "trigger") return <ContextualDecision action={orderAction} view={view}
    disabled={false} submitting={false} labelFor={(ref) => ref}
    onSubmit={(_action, choices) => { document.body.dataset.submittedOrder = JSON.stringify(choices.triggers); }} />;
  if (mode === "combat") return <TurnStatus value={{
    turnSequence: 17, personalTurn: 4, phase: "combat", step: "combat_damage", activeSeat: "C",
    prioritySeat: "C", firstStrikeStep: true, combatDamageStep: 1, lifecycleStatus: "active",
  }} compact={false} priorityStops={[]} />;
  const cards = Array.from({ length: 24 }, (_, index) => ({ id: `card-${index}`, n: `Graveyard card ${index + 1}` }));
  return <ZoneBrowser opened={{ seat: "C", zone: "gy" }} player={{ gy: cards }}
    dialogRef={dialog} view={view} selection={null} selectedCardRef="" actionDisabled
    actionsForCard={() => []} onIntent={() => undefined} onClose={() => undefined} />;
}

export function mountPlaytestSurface(mode: Mode) {
  const node = document.createElement("div");
  document.body.appendChild(node);
  document.getElementById("root")?.setAttribute("hidden", "");
  createRoot(node).render(<Surface mode={mode} />);
}
