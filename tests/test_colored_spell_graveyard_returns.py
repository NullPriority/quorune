"""Original-card color/type targets and independent optional graveyard returns."""

from pathlib import Path
import tempfile
import unittest

from common import ROOT
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
import test_ability_source_counter_targets as native


class ColoredSpellGraveyardReturnRuntimeTests(unittest.TestCase):
    register_fixture_cards_on_add = True
    session = native.AbilitySourceCounterRuntimeTests.session
    add = native.AbilitySourceCounterRuntimeTests.add
    prepare = native.AbilitySourceCounterRuntimeTests.prepare
    checkpoint = native.AbilitySourceCounterRuntimeTests.checkpoint
    priority = native.AbilitySourceCounterRuntimeTests.priority
    actions = native.AbilitySourceCounterRuntimeTests.actions
    binding = native.AbilitySourceCounterRuntimeTests.binding
    reject = native.AbilitySourceCounterRuntimeTests.reject
    settle = native.AbilitySourceCounterRuntimeTests.settle
    replay = native.AbilitySourceCounterRuntimeTests.replay
    private_replay = native.AbilitySourceCounterRuntimeTests.private_replay

    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / "colored-spell-returns.sqlite3"
        build_fixture_database([
            ROOT / "tests/fixtures/bound-effect-program-cards.json",
            ROOT / "tests/fixtures/colored-spell-graveyard-return-cards.json",
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Colored spell returns", [
            DeckEntry("Generic Bound Commander", 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], ["Generic Bound Commander"])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    def cast(self, session, card, targets=(), *, cost_option=None):
        self.priority(session, "A")
        row = next(row for row in self.actions(session) if row.get("card") == card.ref)
        binding = self.binding(session, card)
        self.assertTrue(binding["strict_capability_ready"], binding["blockers"])
        command = {"action_id": row["id"], "targets": list(targets), "pay": "auto"}
        if cost_option is not None:
            self.assertIn(cost_option, [option["id"] for option in row["cost_options"]])
            command["cost_option"] = cost_option
        self.reject(session, command, seat="D")
        result = session.act("pilot:A", command)
        self.assertTrue(result.ok, result.summary)

    def choose(self, session, **fields):
        command = {"action_id": "choose", **fields}
        self.reject(session, command, seat="D")
        result = session.act("pilot:A", command)
        self.assertTrue(result.ok, result.summary)

    def test_original_two_returns_preserve_color_type_owner_and_source_departure(self):
        for index, leave_and_decline in enumerate((False, True)):
            with self.subTest(source_leaves_and_red_declined=leave_and_decline):
                session = self.session(977_000 + index)
                source = self.add(session.engine, "Nucklavee", zone="hand")
                bounce = self.add(session.engine, "Unsummon", zone="hand")
                red = self.add(session.engine, "Expressive Iteration", zone="graveyard", ref="colored-red")
                blue = self.add(session.engine, "Opt", zone="graveyard", ref="colored-blue")
                wrong = [
                    self.add(session.engine, name, zone="graveyard", ref="colored-wrong-" + str(i))
                    for i, name in enumerate(("Shock", "Ponder", "Grizzly Bears"))
                ]
                foreign = self.add(session.engine, "Opt", seat="B", zone="graveyard", ref="colored-foreign")
                self.prepare(session, mana={"C": 10, "U": 10})
                self.checkpoint(session)
                self.cast(session, source, cost_option="hybrid-1")
                selected = set()
                for _ in range(2):
                    self.assertEqual("semantic.target", self.settle(session).kind)
                    schema = session.packet("pilot:A", full=True)["decision"]["ctx"]["target_schema"]
                    legal = set(schema["legal_refs"])
                    candidates = [card for card in (red, blue) if card.ref in legal]
                    self.assertEqual(1, len(candidates))
                    good = candidates[0]
                    self.assertNotIn(good.ref, selected)
                    for bad in (*wrong, foreign, blue if good is red else red):
                        self.assertNotIn(bad.ref, legal)
                        self.reject(session, {"action_id": "choose", "targets": [bad.ref]}, seat="A")
                    session = self.replay(session, load=True)
                    self.choose(session, targets=[good.ref])
                    selected.add(good.ref)
                self.assertEqual({red.ref, blue.ref}, selected)
                if leave_and_decline:
                    self.cast(session, bounce, [source.ref])
                choices = 0
                for _ in range(4):
                    decision = self.settle(session)
                    if decision is None:
                        break
                    self.assertEqual("semantic.choice", decision.kind)
                    for seat in "BCD":
                        self.assertIsNone(session.packet("pilot:" + seat, full=True)["decision"])
                    decline = leave_and_decline and session.state.stack[-1].targets == [red.ref]
                    session = self.replay(session, load=True)
                    self.choose(session, choice="decline" if decline else "apply")
                    choices += 1
                self.assertEqual(2, choices)
                self.assertEqual("graveyard" if leave_and_decline else "hand", session.state.cards[red.object_id].zone)
                self.assertEqual("hand", session.state.cards[blue.object_id].zone)
                self.assertEqual("hand" if leave_and_decline else "battlefield", session.state.cards[source.object_id].zone)
                self.assertTrue(all(session.state.cards[card.object_id].zone == "graveyard" for card in (*wrong, foreign)))
                self.replay(session, load=True)
