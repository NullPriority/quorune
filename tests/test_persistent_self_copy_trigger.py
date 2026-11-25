from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch
import json
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
import test_vanishing_rules as upkeep_witness
from quorune.ability_fragments import canonical_ability_fragments, granted_triggered_specs
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.replacement.face_status_copy import upkeep_refacing_condition
from quorune.rules import persistent_self_copy_trigger as rules
from quorune.rules.capabilities import load_default_capability_registry
from quorune.effect_runtime import persistent_self_copy_trigger as runtime
from scripts.build_test_database import build_fixture_database


class PersistentSelfCopyTriggerTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "persistent-self-copy.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/persistent-self-copy-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("Retained creature trigger", [DeckEntry(commander, 1, "commander"),
                                                             DeckEntry("Plains", 30)], [commander])

    def board(self, seed):
        game = self.session(seed)
        source = self.add(game.engine, "Cryptoplasm")
        self.assert_closed(game, source)
        return game, source

    def upkeep(self, game, seat="A"):
        upkeep_witness.VanishingRuntimeTests.upkeep(self, game, seat=seat)
        self.checkpoint(game)
        return self.resolve(game)

    def choose(self, game, **fields):
        result = game.act("pilot:A", {"action_id": "choose", **fields})
        self.assertTrue(result.ok, result.summary)

    def grants(self, game, ref):
        current = game.state.cards[ref.object_id]
        values = game.engine._effective_card_data(current)
        return granted_triggered_specs(canonical_ability_fragments(values["ability_fragments"]))

    def choose_model(self, game, model):
        self.assertEqual("semantic.target", game.state.pending_decision.kind)
        self.choose(game, targets=[model.ref])
        pending = self.resolve(game)
        self.assertIsNotNone(pending)
        self.assertEqual(("semantic.choice", ["A"]), (pending.kind, pending.actors))

    def test_actual_cryptoplasm_keeps_its_trigger_and_copies_again_next_upkeep(self):
        game, source = self.board(707090101)
        first = self.add(game.engine, "Grizzly Bears", seat="B")
        second = self.add(game.engine, "Hill Giant", seat="C")
        self.add(game.engine, "Hill Giant", seat="D", zone="hand", ref="hidden-copy-model")
        first.counters["+1/+1"] = 2
        source.counters["+1/+1"] = 1
        identity = source.logical_object_id

        pending = self.upkeep(game)
        self.assertEqual(("semantic.target", ["A"]), (pending.kind, pending.actors))
        packet = json.dumps(game.packet("pilot:A", full=True)["decision"])
        self.assertNotIn("hidden-copy-model", packet)
        self.assertFalse(game.act("pilot:A", {"action_id": "choose", "targets": [source.ref]}).ok)
        self.assertFalse(game.act("pilot:B", {"action_id": "choose", "targets": [first.ref]}).ok)
        self.choose_model(game, first)
        game = self.replay(game, load=True)
        self.choose(game, choice="apply")
        self.assertIsNone(self.resolve(game))
        current = game.state.cards[source.object_id]
        self.assertEqual(("Grizzly Bears", identity, 1),
                         (game.engine.display_name(current.object_id), current.logical_object_id, current.counters["+1/+1"]))
        self.assertEqual(1, len(self.grants(game, source)), "retained original trigger")

        self.assertIsNone(self.upkeep(game, "B"))
        self.assertEqual("Grizzly Bears", game.engine.display_name(source.object_id))
        self.assertIsNotNone(self.upkeep(game))
        self.choose_model(game, second)
        self.choose(game, choice="apply")
        self.assertIsNone(self.resolve(game))
        self.assertEqual("Hill Giant", game.engine.display_name(source.object_id))
        self.assertEqual(1, len(self.grants(game, source)))
        self.replay(game, load=True)

    def test_actual_cryptoplasm_copy_of_cryptoplasm_produces_two_next_upkeep_triggers(self):
        game, source = self.board(707090102)
        other = self.add(game.engine, "Cryptoplasm", seat="B", ref="model-cryptoplasm")
        self.add(game.engine, "Grizzly Bears", seat="B")
        self.assertIsNotNone(self.upkeep(game))
        self.choose_model(game, other)
        self.choose(game, choice="apply")
        self.assertIsNone(self.resolve(game))
        first, = self.grants(game, source)
        self.assertEqual("step.begin", first.event)
        pending = self.upkeep(game)
        self.assertEqual(("trigger.order", ["A"]), (pending.kind, pending.actors))
        self.assertEqual(2, len(pending.continuation["trigger_refs"]))
        self.replay(game, load=True)

    def test_actual_cryptoplasm_declines_and_skips_an_invalidated_model(self):
        for leave, seed in ((False, 707090103), (True, 707090104)):
            with self.subTest(leave=leave):
                game, source = self.board(seed)
                model = self.add(game.engine, "Grizzly Bears", seat="B")
                self.assertIsNotNone(self.upkeep(game))
                if leave:
                    self.choose(game, targets=[model.ref])
                    game.engine.move_card(model.object_id, "graveyard")
                    self.checkpoint(game)
                else:
                    self.choose_model(game, model)
                    self.choose(game, choice="decline")
                self.assertIsNone(self.resolve(game))
                self.assertEqual("Cryptoplasm", game.engine.display_name(source.object_id))
                self.assertFalse(self.grants(game, source))
                self.replay(game, load=True)

    def test_bound_copy_rejects_other_events_and_extra_effects(self):
        effect = rules.self_copy_upkeep_effect()
        base = dict(
            active_zone="battlefield", event="step.begin", target_schema=rules.self_copy_upkeep_target(),
            event_condition=upkeep_refacing_condition(), effects=(effect,), coverage=(), cost_schema=None,
        )
        self.assertIn(rules.PERSISTENT_SELF_COPY_TRIGGER_CAPABILITY,
                      rules.persistent_self_copy_trigger_dependencies(SimpleNamespace(**base)))
        for changes in (
            {"event": "creature.enter"},
            {"target_schema": {**base["target_schema"], "another": False}},
            {"effects": (effect, {"op": "draw", "count": 1})},
            {"effects": ({**effect, "player": "$context.player"},)},
        ):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                rules.persistent_self_copy_trigger_dependencies(SimpleNamespace(**{**base, **changes}))

    def test_actual_cryptoplasm_kills_removing_the_retained_trigger(self):
        original = runtime.persistent_public_copy_values

        def without_grant(host, model, *, recipient, exception):
            return original(host, model, recipient=recipient)

        with patch.object(runtime, "persistent_public_copy_values", side_effect=without_grant):
            with self.assertRaisesRegex(AssertionError, "retained original trigger"):
                self.test_actual_cryptoplasm_keeps_its_trigger_and_copies_again_next_upkeep()
