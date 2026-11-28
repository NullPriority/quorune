from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import json
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.unpaid_creature_entry import CAPABILITY, GROUP_FIELD, condition, condition_actual
from scripts.build_test_database import build_fixture_database


SOURCE = "Satoru, the Infiltrator"


class UnpaidCreatureEntryTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        db = Path(cls.temporary.name) / "unpaid-creature-entries.sqlite3"
        fixtures = (
            "bound-effect-program-cards", "unpaid-creature-entry-group-cards",
            "plural-reanimation-cards", "activation-public-state-cards",
        )
        build_fixture_database([ROOT / f"tests/fixtures/{name}.json" for name in fixtures], db)
        cls.db = CardDatabase(db)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Unpaid creature entries", [
            DeckEntry("Generic Bound Commander", 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], ["Generic Bound Commander"])

    def test_actual_paid_and_zero_cast_tokens_and_simultaneous_returns_use_one_draw_and_replay(self):
        game = self.session(603427)
        source = self.add(game.engine, SOURCE, zone="hand")
        free = self.add(game.engine, "Ornithopter", zone="hand", ref="zero-mana-thopter")
        paid = self.add(game.engine, "Grizzly Bears", zone="hand")
        token_spell = self.add(game.engine, "Raise the Alarm", zone="hand")
        return_spell = self.add(game.engine, "Proclamation of Rebirth", zone="hand")
        returned_thopter = self.add(game.engine, "Ornithopter", zone="graveyard", ref="returned-thopter")
        returned_myr = self.add(game.engine, "Memnite", zone="graveyard")
        for card in (source, free, paid, token_spell, return_spell):
            self.assert_closed(game, card)
        self.prepare(game, mana={"U":1, "B":1, "C":4, "W":2, "G":1})
        self.checkpoint(game)
        for card, targets in ((source, ()), (paid, ()), (token_spell, ())):
            self.cast(game, "A", card, targets)
            self.resolve_top(game)
            self.assertFalse(game.state.stack)
        self.assertEqual(2, sum(card.is_token and card.zone == "battlefield" for card in game.state.cards.values()))

        self.cast(game, "A", free)
        self.resolve_top(game)
        (pending,) = game.state.stack
        self.assertEqual(source.object_id, pending.source_object_id)
        self.assertEqual(0, pending.context[GROUP_FIELD][0]["mana"])
        self.assertTrue(pending.context[GROUP_FIELD][0]["cast"])
        hand = len(game.state.players["A"].zones["hand"])
        game = self.replay(game, load=True)
        self.resolve_top(game)
        self.assertEqual(hand+1, len(game.state.players["A"].zones["hand"]))

        self.cast(game, "A", return_spell, [returned_thopter.ref, returned_myr.ref])
        self.resolve_top(game)
        self.assertEqual({"battlefield"}, {game.state.cards[card.object_id].zone
                                         for card in (returned_thopter, returned_myr)})
        (pending,) = game.state.stack
        self.assertEqual(source.object_id, pending.source_object_id)
        self.assertEqual({game.state.cards[card.object_id].logical_object_id for card in (returned_thopter, returned_myr)},
                         {member["identity"] for member in pending.context[GROUP_FIELD]})
        self.assertTrue(all(not member["cast"] for member in pending.context[GROUP_FIELD]))
        hand = len(game.state.players["A"].zones["hand"])
        game = self.replay(game, load=True)
        self.resolve_top(game)
        self.assertEqual(hand + 1, len(game.state.players["A"].zones["hand"]))
        drawn = game.state.cards[game.state.players["A"].zones["hand"][-1]]
        self.assertNotIn(drawn.ref, json.dumps(game.packet("pilot:B", full=True)["state"]))
        self.replay(game, load=True)

    def test_group_rejects_one_paid_arrival_and_ignores_other_controllers_and_tokens(self):
        def member(identity, *, controller="A", token=False, cast=False, mana=None):
            return {"identity":identity, "controller":controller, "creature":True,
                    "token":token, "cast":cast, "mana":mana}
        source = SimpleNamespace(controller="A", logical_object_id="source@2")
        free = member("free@1", cast=True, mana=0)
        paid = member("paid@1", cast=True, mana=1)
        context = {"card_object_identity":"free@1", GROUP_FIELD:[free, paid]}
        self.assertFalse(condition_actual(condition(), context, source))
        context[GROUP_FIELD] = [free, member("foreign@1", controller="B", cast=True, mana=2), member("token@1", token=True)]
        self.assertTrue(condition_actual(condition(), context, source))
        self.assertFalse(condition_actual(condition(), {**context,"card_object_identity":"token@1"}, source))
        context[GROUP_FIELD] = [free, member("unknown@1", cast=True)]
        self.assertFalse(condition_actual(condition(), context, source))

    def test_complete_original_binds_the_exact_group_and_rejects_scope_changes(self):
        record = self.db.lookup(SOURCE, fuzzy=False)
        ir = compile_oracle_card(record, capability_registry=self.registry, capability_profile="commander_review")
        self.assertEqual("exact", ir.status, ir.material_residuals)
        node, = (node for face in ir.faces for node in face.nodes if CAPABILITY in node.capability_dependencies)
        self.assertEqual(condition(), node.event_condition)
        for before,after in (("other nontoken creatures", "other creatures"), ("none of them were cast", "some of them were cast")):
            changed = replace(record, oracle_text=record.oracle_text.replace(before,after))
            self.assertNotEqual("exact", compile_oracle_card(changed, capability_registry=self.registry,
                                                            capability_profile="commander_review").status)

    def test_omitting_the_exact_entry_group_is_killed(self):
        with patch("quorune.compiler.public_trigger_conditions._unpaid_creature_entry_binding", return_value=None):
            with self.assertRaises(AssertionError):
                self.test_complete_original_binds_the_exact_group_and_rejects_scope_changes()
