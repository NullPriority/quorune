"""Native proof for the mandatory own-graveyard return and mana-value life loss."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_granted_skulk import record


_BODY = "Return target creature card from your graveyard to the battlefield. You lose life equal to that card's mana value."
_HEAD = "When this creature enters, "


class OwnedGraveyardReanimationCompilerTests(unittest.TestCase):
    def test_delver_body_keeps_the_owned_creature_target_and_the_complete_life_result(self):
        registry = load_default_capability_registry()

        def compile_body(text):
            return compile_oracle_card(record(text, "Creature — Zombie"), capability_registry=registry,
                                       capability_profile="commander_review")

        ir = compile_body(_HEAD + _BODY)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        trigger, = ir.faces[0].nodes
        self.assertEqual("permanent.enter.self", trigger.event)
        self.assertEqual("you", trigger.target_schema["owner_relation"])
        self.assertEqual(["creature"], trigger.target_schema["types_any"])
        self.assertEqual(["reanimate", "lose_life"], [effect["op"] for effect in trigger.effects])
        self.assertIn("quantity_expression.graveyard_target_mana_value", trigger.capability_dependencies)
        for changed in (
            _BODY.replace("your graveyard", "their graveyard"),
            _BODY.replace("target creature card", "up to two target creature cards"),
            _BODY.replace("that card's mana value", "its controller's life total"),
        ):
            with self.subTest(changed=changed):
                self.assertNotEqual("exact", compile_body(_HEAD + changed).status)

        existing = compile_body(
            _HEAD + "Put target creature card from a graveyard onto the battlefield under your control. "
                    "You lose life equal to that card's mana value."
        )
        self.assertEqual("exact", existing.status, existing.material_residuals)
        self.assertEqual("any", existing.faces[0].nodes[0].target_schema["owner_relation"])


class OwnedGraveyardReanimationLifeTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "owned-graveyard-reanimation.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/owned-graveyard-reanimation-life-cards.json"], path)
        cls.use_database(path)

    @classmethod
    def use_database(cls, path):
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("Owned graveyard witness",
            [DeckEntry(commander, 1, "commander"), DeckEntry("Plains", 30)], [commander])

    def cast_delver(self, game, delver):
        self.assert_closed(game, delver)
        self.prepare(game, mana={"B": 2, "C": 3})
        self.checkpoint(game)
        self.cast(game, "A", delver)
        self.until(game, lambda current: current.state.pending_decision.kind == "semantic.target")
        self.assertEqual("battlefield", game.state.cards[delver.object_id].zone)

    def test_native_delver_uses_the_original_graveyard_mana_value_through_entry_copy_and_reload(self):
        game = self.session(608020801)
        delver = self.add(game.engine, "Phyrexian Delver", zone="hand")
        clone = self.add(game.engine, "Clone", zone="graveyard")
        zero = self.add(game.engine, "Ornithopter", ref="zero-mana-copy-choice")
        foreign = self.add(game.engine, "Grizzly Bears", seat="B", zone="graveyard", ref="other-graveyard")
        private = self.add(game.engine, "Grizzly Bears", seat="D", zone="hand", ref="private-delver-choice-witness")
        self.assert_closed(game, clone)
        self.cast_delver(game, delver)
        before = authoritative_state_hash(game.state)
        rejected = game.act("pilot:A", {"action_id": "choose", "targets": [foreign.ref]})
        self.assertFalse(rejected.ok, rejected.summary)
        self.assertEqual(before, authoritative_state_hash(game.state))
        self.assertNotIn(private.ref, json.dumps(game.packet("pilot:A", full=True)))
        self.target(game, "A", [clone.ref])
        self.until(game, lambda current: current.state.pending_decision.kind == "replacement.order")
        self.assertEqual(40, game.state.players["A"].life)
        game = self.replay(game, load=True)
        option = next(row for row in game.packet("pilot:A", full=True)["decision"]["ctx"]["options"]
                      if zero.ref in row["id"])
        accepted = game.act("pilot:A", {"action_id": "choose", "choices": {"replacement": option["id"]}})
        self.assertTrue(accepted.ok, accepted.summary)
        self.settle(game)
        copied = game.state.cards[clone.object_id]
        self.assertEqual("battlefield", copied.zone)
        self.assertEqual("A", copied.controller)
        self.assertEqual("Ornithopter", game.engine.display_name(copied.object_id))
        self.assertEqual(36, game.state.players["A"].life)
        self.replay(game, load=True)

    def test_native_delver_returns_the_owned_creature_and_loses_its_mana_value(self):
        game = self.session(608020802)
        delver = self.add(game.engine, "Phyrexian Delver", zone="hand")
        target = self.add(game.engine, "Grizzly Bears", zone="graveyard")
        self.cast_delver(game, delver)
        self.target(game, "A", [target.ref])
        self.settle(game)
        self.assertEqual("battlefield", game.state.cards[target.object_id].zone)
        self.assertEqual("A", game.state.cards[target.object_id].controller)
        self.assertEqual(38, game.state.players["A"].life)
        self.replay(game, load=True)

    def test_native_delver_with_an_illegal_original_target_does_not_lose_life(self):
        game = self.session(608020803)
        delver = self.add(game.engine, "Phyrexian Delver", zone="hand")
        target = self.add(game.engine, "Grizzly Bears", zone="graveyard")
        purge = self.add(game.engine, "Coffin Purge", seat="B", zone="hand")
        game.state.players["B"].mana_pool["B"] = 1
        self.assert_closed(game, purge)
        self.cast_delver(game, delver)
        self.target(game, "A", [target.ref])
        self.cast(game, "B", purge, (target.ref,))
        self.settle(game)
        self.assertEqual("exile", game.state.cards[target.object_id].zone)
        self.assertEqual(40, game.state.players["A"].life)
        self.replay(game, load=True)
