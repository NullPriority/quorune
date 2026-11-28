"""Native source-death timing and exact owner-hand return."""

from pathlib import Path
from tempfile import TemporaryDirectory
import json
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import CapabilityRegistry, DEFAULT_CAPABILITY_REGISTRY, load_default_capability_registry
from quorune.delayed_object_model import (
    SOURCE_DEATH_HAND_CAPABILITY, SOURCE_DEATH_HAND_EVENT, source_death_hand_return_effect,
)
from scripts.build_test_database import build_fixture_database


CARD = "The Scorpion God"


class SourceDeathDelayedHandReturnTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        folder = Path(cls.temporary.name)
        support = {
            "activated-self-transform-cards.json": {"Act of Treason"},
            "activation-restriction-material-cards.json": {"Swamp"},
            "activation-result-views-cards.json": {"Llanowar Elves"},
            "bound-effect-program-cards.json": {"Generic Bound Commander", "Generic Bound Plains", "Murder"},
            "counter-qualified-zone-cards.json": {CARD},
            "fixed-public-zone-move-cards.json": {"Tormod's Crypt"},
        }
        fixtures = []
        for filename, names in support.items():
            document = json.loads((ROOT / "tests/fixtures" / filename).read_bytes())
            cards = [row for row in document["cards"] if row["name"] in names]
            assert {row["name"] for row in cards} == names, filename
            identities = {row["oracle_id"] for row in cards}
            document["cards"] = cards
            document["rulings"] = [row for row in document.get("rulings", ()) if row["oracle_id"] in identities]
            selected = folder / filename
            selected.write_text(json.dumps(document), encoding="utf-8", newline="\n")
            fixtures.append(selected)
        database = folder / "source-death-delayed-hand-return.sqlite3"
        build_fixture_database(fixtures, database)
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Source death delayed hand return", [
            DeckEntry("Generic Bound Commander", 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], ["Generic Bound Commander"])

    def _advance(self, game, condition):
        for _ in range(150):
            if condition(game.state):
                return
            game.engine.pump()
            pending = game.state.pending_decision
            self.assertIsNotNone(pending)
            if pending.kind == "priority":
                command = {"action_id": "pass"}
            elif pending.kind == "combat.attackers":
                command = {"action_id": "attack", "attackers": {}}
            elif pending.kind == "combat.blockers":
                command = {"action_id": "block", "blocks": {}}
            else:
                self.fail(f"Unexpected step decision {pending.kind}")
            result = game.act(game.pending_principals()[0], command)
            self.assertTrue(result.ok, result.summary)
        self.fail("The required step did not begin")

    def _activate(self, game, source, target):
        self.priority(game, "A")
        offers = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        action, = (row for row in offers if row["id"].startswith(f"activate:{source.ref}:"))
        result = game.act("pilot:A", {"action_id": action["id"], "targets": [target], "pay": "auto"})
        self.assertTrue(result.ok, result.summary)

    def test_original_whole_binding_and_removed_authority_fail_closed(self):
        def compile_with(registry):
            return compile_oracle_card(self.db.lookup(CARD), capability_registry=registry,
                                       capability_profile="commander_review")

        ir = compile_with(self.registry)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        self.assertEqual(3, len(ir.faces[0].nodes))
        node, = (row for row in ir.faces[0].nodes if SOURCE_DEATH_HAND_CAPABILITY in row.capability_dependencies)
        self.assertEqual(SOURCE_DEATH_HAND_EVENT, node.event)
        self.assertEqual((source_death_hand_return_effect(),), node.effects)
        with patch("quorune.compiler.source_self_effect_templates.source_death_hand_return_template", return_value=None):
            self.assertNotEqual("exact", compile_with(self.registry).status)
        raw = json.loads(DEFAULT_CAPABILITY_REGISTRY.read_bytes())
        declaration = next(row for row in raw["capabilities"] if row["id"] == SOURCE_DEATH_HAND_CAPABILITY)
        declaration.update(status="blocked", blockers=["Withdraw the exact delayed owner-hand result"])
        denied = compile_with(CapabilityRegistry(raw))
        self.assertNotEqual("exact", denied.status)
        self.assertTrue(any(SOURCE_DEATH_HAND_CAPABILITY in value
                            for residual in denied.material_residuals for value in residual.blockers))

    def test_original_counter_draw_and_borrowed_source_return_to_owner_replay(self):
        game = self.session(60307054)
        game.state.config.semantic_policy = "trusted_only"
        source = self.add(game.engine, CARD, seat="B")
        creature = self.add(game.engine, "Llanowar Elves", seat="B")
        gain = self.add(game.engine, "Act of Treason", zone="hand")
        murder = self.add(game.engine, "Murder", zone="hand")
        self.assert_closed(game, source)
        self.prepare(game, mana={"C": 7, "R": 3, "B": 4})
        private = game.state.cards[game.state.players["B"].zones["hand"][0]].ref
        self.checkpoint(game)
        self.cast(game, "A", gain, [source.ref])
        self.assertIsNone(self.resolve(game))
        self.assertEqual("A", game.state.cards[source.object_id].controller)
        hand = len(game.state.players["A"].zones["hand"])
        paid = sum(game.state.players["A"].mana_pool.values())
        self._activate(game, source, creature.ref)
        self.assertEqual(paid - 3, sum(game.state.players["A"].mana_pool.values()))
        self.assertIsNone(self.resolve(game))
        self.assertEqual("graveyard", game.state.cards[creature.object_id].zone)
        self.assertEqual(hand + 1, len(game.state.players["A"].zones["hand"]))
        self.cast(game, "A", murder, [source.ref])
        self.assertIsNone(self.resolve(game))
        self.assertEqual("graveyard", game.state.cards[source.object_id].zone)
        delayed, = game.state.delayed_triggers
        self.assertEqual("A", delayed.controller)
        game = self.replay(game, load=True)
        self._advance(game, lambda state: state.phase == "ending" and state.step == "end_step")
        self.assertIsNone(self.resolve(game))
        self.assertIn(source.object_id, game.state.players["B"].zones["hand"])
        self.assertNotIn(source.object_id, game.state.players["A"].zones["hand"])
        self.assertNotIn(private, json.dumps(game.packet("pilot:A", full=True)))
        self.replay(game, load=True)

    def test_death_inside_end_step_defers_once_and_stale_arrival_stays_exiled(self):
        for exile in (False, True):
            with self.subTest(exile=exile):
                game = self.session(60307062 + exile)
                game.state.config.semantic_policy = "trusted_only"
                source = self.add(game.engine, CARD)
                murder = self.add(game.engine, "Murder", seat="B", zone="hand")
                for index in range(3):
                    self.add(game.engine, "Swamp", seat="B", ref=f"late-swamp-{index}")
                crypt = self.add(game.engine, "Tormod's Crypt") if exile else None
                self.prepare(game)
                self.checkpoint(game)
                self._advance(game, lambda state: state.phase == "ending" and state.step == "end_step")
                turn = game.state.turn_sequence
                self.cast(game, "B", murder, [source.ref])
                self.assertIsNone(self.resolve(game))
                self.assertEqual(turn, game.state.turn_sequence)
                self.assertEqual("graveyard", game.state.cards[source.object_id].zone)
                self.assertEqual(1, len(game.state.delayed_triggers))
                if crypt is not None:
                    self._activate(game, crypt, "A")
                    self.assertIsNone(self.resolve(game))
                    self.assertEqual("exile", game.state.cards[source.object_id].zone)
                game = self.replay(game, load=True)
                self._advance(game, lambda state: state.turn_sequence > turn
                              and state.phase == "ending" and state.step == "end_step")
                self.assertIsNone(self.resolve(game))
                self.assertEqual("exile" if exile else "hand", game.state.cards[source.object_id].zone)
                settled, = game.state.delayed_triggers
                self.assertTrue(settled.once)
                self.assertFalse(settled.active)
                self.replay(game, load=True)
