from pathlib import Path
import tempfile
import unittest

from common import ROOT
from quorune.carddb import CardDatabase
from quorune.compiler.scalar_effect_amounts import scalar_effect_amount_template
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import _effect_template
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
import test_compiled_reviewed_trigger_consumers as native


class ScaledPublicCountCompilerTests(unittest.TestCase):
    def compile(self, text, *, source_event=True):
        return scalar_effect_amount_template(text, source_name="Fixture", source_event=source_event,
            compile_fixed=lambda body: _effect_template(body, card_name="Fixture"))

    def test_public_count_multiplier_stays_in_its_existing_quantity_and_scalar_producers_remain_distinct(self):
        for text in (
            "It deals damage equal to twice the number of Vehicles you control to any target.",
            "It deals damage to any target equal to twice the number of Vehicles you control.",
        ):
            with self.subTest(text=text):
                compiled = self.compile(text)
                self.assertIsNotNone(compiled)
                amount = compiled[1][0]["amount"]
                self.assertEqual("public_query_effect_amount", amount["kind"])
                self.assertEqual(1, amount["coefficient"])
                self.assertEqual(2, amount["quantity"]["multiplier"])
                self.assertEqual(1, compiled[2]["count"])
        scalar = self.compile("It deals damage equal to twice its power to any target.")
        self.assertEqual("scalar_effect_amount", scalar[1][0]["amount"]["kind"])
        self.assertEqual(2, scalar[1][0]["amount"]["coefficient"])

    def test_scaling_does_not_authorize_unknown_queries_or_an_unbound_source(self):
        text = "It deals damage equal to twice the number of Vehicles you control to any target."
        self.assertIsNone(self.compile(text, source_event=False))
        self.assertIsNone(self.compile("It deals damage equal to twice the number of secret cards named Fixture to any target."))


class ScaledPublicCountRuntimeTests(unittest.TestCase):
    register_fixture_cards_on_add = True
    session = native.CompiledReviewedTriggerConsumerTests.session
    add = native.CompiledReviewedTriggerConsumerTests.add
    ready = native.CompiledReviewedTriggerConsumerTests.ready
    prepare = native.CompiledReviewedTriggerConsumerTests.prepare
    checkpoint = native.CompiledReviewedTriggerConsumerTests.checkpoint
    priority = native.CompiledReviewedTriggerConsumerTests.priority
    actions = native.CompiledReviewedTriggerConsumerTests.actions
    binding = native.CompiledReviewedTriggerConsumerTests.binding
    reject = native.CompiledReviewedTriggerConsumerTests.reject
    settle = native.CompiledReviewedTriggerConsumerTests.settle
    replay = native.CompiledReviewedTriggerConsumerTests.replay
    cast = native.CompiledReviewedTriggerConsumerTests.cast
    choose = native.CompiledReviewedTriggerConsumerTests.choose

    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / "scaled-public-count.sqlite3"
        build_fixture_database([
            ROOT / "tests/fixtures/bound-effect-program-cards.json",
            ROOT / "tests/fixtures/scaled-public-count-cards.json",
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Scaled public counts", [
            DeckEntry("Generic Bound Commander", 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], ["Generic Bound Commander"])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    def test_twice_the_live_vehicle_count_keeps_its_target_and_source_after_departure(self):
        for index, (other_vehicle, leave, damage) in enumerate(((True, False, 4), (True, True, 2), (False, True, 0))):
            with self.subTest(other_vehicle=other_vehicle, source_leaves=leave):
                session = self.session(971_000 + index)
                source = self.add(session.engine, "Surgehacker Mech", zone="hand")
                if other_vehicle:
                    self.add(session.engine, "Consulate Dreadnought", ref="own-vehicle")
                self.add(session.engine, "Consulate Dreadnought", seat="B", ref="foreign-vehicle")
                victim = self.add(session.engine, "Generic Bound Body", seat="B", ref="scaled-damage-body")
                bounce = self.add(session.engine, "Disperse", seat="B", zone="hand")
                session.state.players["B"].mana_pool.update(C=1, U=1)
                self.ready(session, {"C": 4})
                self.cast(session, source)
                self.assertEqual("semantic.target", self.settle(session).kind)
                session = self.replay(session, load=True)
                self.reject(session, {"action_id": "choose", "targets": ["B"]})
                self.choose(session, targets=[victim.ref])
                if leave:
                    self.cast(session, bounce, [source.ref], seat="B")
                self.assertIsNone(self.settle(session))
                self.assertEqual(damage, session.state.cards[victim.object_id].marked_damage)
                self.assertEqual("hand" if leave else "battlefield", session.state.cards[source.object_id].zone)
                self.replay(session, load=True)
