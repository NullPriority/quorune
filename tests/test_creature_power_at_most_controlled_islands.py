"""Vedalken Shackles shares the live activating-controller Island count."""

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.compiler.direct_target import DirectPermanentTargetSpec, direct_permanent_target_spec
from quorune.deck import DeckDefinition, DeckEntry
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.permanent_predicate_capability_shapes import controller_island_target_runtime_dependencies
from quorune.targets import CONTROLLER_ISLAND_TARGET_PREDICATE, ISLAND_COUNT_POWER_TARGET_PREDICATE
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


ORIGINAL = "Vedalken Shackles"


class CreaturePowerAtMostControlledIslandsTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        database = Path(cls.temporary.name) / "creature-power-at-most-controlled-islands.sqlite3"
        fixtures = ROOT / "tests/fixtures"
        build_fixture_database([
            fixtures / "creature-power-at-most-controlled-islands-cards.json",
            fixtures / "creature-target-controller-controls-island-cards.json",
            fixtures / "combat-prevention-source-power-ceiling-cards.json",
            fixtures / "opponent-life-gain-to-loss-cards.json",
        ], database)
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Island count and tapped control", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def test_complete_original_requires_the_activators_island_count_and_the_existing_tapped_duration(self):
        binding, semantics = register_card(self.db, self.registry, ORIGINAL)
        self.assertTrue(binding["strict_capability_ready"], binding["blockers"])
        record = self.db.lookup(ORIGINAL, fuzzy=False)
        programs = {program.event: program for program in semantics.programs_for_oracle(record.oracle_id)}
        self.assertEqual({"activate", "untap.step"}, set(programs))
        ability = programs["activate"]
        self.assertEqual("until_source_leaves_or_untaps", ability.effects[0]["duration"])
        self.assertEqual(ISLAND_COUNT_POWER_TARGET_PREDICATE, ability.target_schema["predicate"])
        self.assertTrue(DirectPermanentTargetSpec.from_target_schema(ability.target_schema).power_at_most_controlled_islands)
        self.assertIn("target.revalidate_resolution", ability.capability_dependencies)
        for invalid in (
            {**ability.target_schema, "predicate": CONTROLLER_ISLAND_TARGET_PREDICATE},
            {key: value for key, value in ability.target_schema.items() if key != "predicate"},
        ):
            with self.assertRaises(ValueError):
                controller_island_target_runtime_dependencies(replace(ability, target_schema=invalid))
        with self.assertRaises(ValueError):
            DirectPermanentTargetSpec(types_any=("creature",), controller_controls_island=True,
                                      power_at_most_controlled_islands=True)
        for unsupported in (
            "target creature with power less than the number of Islands you control",
            "target creature with power less than or equal to the number of Islands its controller controls",
            "target creature with power less than or equal to the number of lands you control",
        ):
            self.assertIsNone(direct_permanent_target_spec(unsupported))

    def activation_offer(self, game, source):
        self.priority(game, "A")
        actions = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        offer, = [row for row in actions if row["id"].startswith(f"activate:{source.ref}:")]
        return offer

    def activate_on(self, game, offer, target, *, reserve=None):
        action = game.act("pilot:A", {
            "action_id": offer["id"], "targets": [target.ref], "pay": "auto", "reserve": reserve or {},
        })
        self.assertTrue(action.ok, action.summary)

    def test_actual_count_and_power_select_equal_target_then_remain_fixed_until_the_artifact_untaps(self):
        game = self.session(11512001)
        source = self.add(game.engine, ORIGINAL)
        self.add(game.engine, "Island")
        dual = self.add(game.engine, "Tropical Island")
        equal = self.add(game.engine, "Runeclaw Bear", seat="B")
        above = self.add(game.engine, "Centaur Courser", seat="C")
        self.add(game.engine, "Island", seat="C", ref="shackles-foreign-island")
        growth = self.add(game.engine, "Giant Growth", zone="hand")
        bounce = self.add(game.engine, "Boomerang", zone="hand")
        key = self.add(game.engine, "Voltaic Key")
        for card in (source, equal, above, growth, bounce, key):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 3, "G": 1, "U": 2})
        self.checkpoint(game)
        offer = self.activation_offer(game, source)
        self.assertIn(equal.ref, offer["target_schema"]["legal_refs"])
        self.assertNotIn(above.ref, offer["target_schema"]["legal_refs"])
        before = authoritative_state_hash(game.state)
        rejected = game.act("pilot:A", {"action_id": offer["id"], "targets": [above.ref], "pay": "auto"})
        self.assertFalse(rejected.ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        self.assertFalse(game.state.cards[source.object_id].tapped)

        self.activate_on(game, offer, equal, reserve={"C": 1, "G": 1, "U": 2})
        self.resolve_top(game)
        self.assertEqual(("B", "A"), (game.state.cards[equal.object_id].owner,
                                     game.state.cards[equal.object_id].controller))
        self.cast_for(game, "A", growth, [equal.ref])
        self.resolve_top(game)
        self.assertEqual(5, game.engine._numeric_stat(equal.object_id, "power"))
        self.cast_for(game, "A", bounce, [dual.ref])
        self.resolve_top(game)
        self.assertEqual("hand", game.state.cards[dual.object_id].zone)
        self.assertEqual("A", game.state.cards[equal.object_id].controller)

        game = self.replay(game, load=True)
        self.activate_on(game, self.activation_offer(game, key), game.state.cards[source.object_id])
        self.resolve_top(game)
        self.assertFalse(game.state.cards[source.object_id].tapped)
        self.assertEqual("B", game.state.cards[equal.object_id].controller)
        self.replay(game, load=True)

    def test_actual_lost_island_before_resolution_invalidates_the_chosen_target_after_both_costs(self):
        game = self.session(60820031)
        source = self.add(game.engine, ORIGINAL)
        self.add(game.engine, "Island")
        island = self.add(game.engine, "Tropical Island")
        target = self.add(game.engine, "Runeclaw Bear", seat="B")
        bounce = self.add(game.engine, "Boomerang", zone="hand")
        for card in (source, target, bounce):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 2, "U": 2})
        self.checkpoint(game)
        self.activate_on(game, self.activation_offer(game, source), target, reserve={"U": 2})
        self.assertEqual((0, True), (game.state.players["A"].mana_pool["C"],
                                     game.state.cards[source.object_id].tapped))
        self.cast_for(game, "A", bounce, [island.ref])
        self.resolve_top(game)
        game = self.replay(game, load=True)
        self.resolve_top(game)
        self.assertEqual("B", game.state.cards[target.object_id].controller)
        self.assertTrue(game.state.cards[source.object_id].tapped)
        self.replay(game, load=True)

    def test_actual_zero_islands_can_take_zero_power_and_artifact_transfer_keeps_the_tapped_duration(self):
        game = self.session(11512002)
        source = self.add(game.engine, ORIGINAL)
        target = self.add(game.engine, "Ornithopter", seat="B")
        donate = self.add(game.engine, "Donate", zone="hand")
        for card in (source, target, donate):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 4, "U": 1})
        self.checkpoint(game)
        offer = self.activation_offer(game, source)
        self.assertIn(target.ref, offer["target_schema"]["legal_refs"])
        self.activate_on(game, offer, target, reserve={"C": 2, "U": 1})
        self.resolve_top(game)
        self.assertEqual("A", game.state.cards[target.object_id].controller)
        self.cast_for(game, "A", donate, ["C", source.ref])
        self.resolve_top(game)
        self.assertEqual(("C", True), (game.state.cards[source.object_id].controller,
                                       game.state.cards[source.object_id].tapped))
        self.assertEqual("A", game.state.cards[target.object_id].controller)
        self.replay(game, load=True)
