"""Seasinger shares an Island-qualified target predicate at selection and resolution."""

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
from quorune.targets import CONTROLLER_ISLAND_TARGET_PREDICATE
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


ORIGINAL = "Seasinger"


class CreatureTargetControllerControlsIslandTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        database = Path(cls.temporary.name) / "creature-target-controller-controls-island.sqlite3"
        fixtures = ROOT / "tests/fixtures"
        build_fixture_database([
            fixtures / "creature-target-controller-controls-island-cards.json",
            fixtures / "combat-prevention-source-power-ceiling-cards.json",
            fixtures / "opponent-life-gain-to-loss-cards.json",
        ], database)
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Island-qualified creature targeting", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def test_complete_original_retains_maintenance_untap_activation_and_exact_live_target(self):
        binding, semantics = register_card(self.db, self.registry, ORIGINAL)
        self.assertTrue(binding["strict_capability_ready"], binding["blockers"])
        record = self.db.lookup(ORIGINAL, fuzzy=False)
        programs = {program.event: program for program in semantics.programs_for_oracle(record.oracle_id)}
        self.assertEqual({"state.condition", "untap.step", "activate"}, set(programs))
        ability = programs["activate"]
        self.assertEqual(CONTROLLER_ISLAND_TARGET_PREDICATE, ability.target_schema["predicate"])
        self.assertTrue(DirectPermanentTargetSpec.from_target_schema(ability.target_schema).controller_controls_island)
        self.assertEqual("until_source_control_changes_or_untaps", ability.effects[0]["duration"])
        self.assertIn("target.revalidate_resolution", ability.capability_dependencies)
        omitted = {key: value for key, value in ability.target_schema.items() if key != "predicate"}
        with self.assertRaises(ValueError):
            controller_island_target_runtime_dependencies(replace(ability, target_schema=omitted))
        for bad in ("target creature whose controller controls a land",
                    "target creature whose owner controls an Island",
                    "another target creature whose controller controls an Island"):
            self.assertIsNone(direct_permanent_target_spec(bad))
        for schema in ({**ability.target_schema, "count": 2},
                       {**ability.target_schema, "controller_relation": "opponent"},
                       {**ability.target_schema, "predicate": "controls_an_unknown_land"}):
            with self.assertRaises(ValueError):
                DirectPermanentTargetSpec.from_target_schema(schema)

    def prepare_source(self, game):
        self.add(game.engine, "Island", ref="seasinger-own-island")
        source = self.add(game.engine, ORIGINAL)
        source.acquired_control_turn_count = -1
        self.assert_closed(game, source)
        return source

    def offer_activation(self, game, source):
        self.priority(game, "A")
        actions = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        offer, = [row for row in actions if row["id"].startswith(f"activate:{source.ref}:")]
        return offer

    def activate(self, game, offer, target):
        action = game.act("pilot:A", {"action_id": offer["id"], "targets": [target.ref], "pay": "auto"})
        self.assertTrue(action.ok, action.summary)

    def test_actual_offer_and_command_use_the_same_current_controller_and_accept_a_nonbasic_island(self):
        game = self.session(11510201)
        source = self.prepare_source(game)
        island = self.add(game.engine, "Tropical Island", seat="B")
        legal = self.add(game.engine, "Centaur Courser", seat="B")
        illegal = self.add(game.engine, "Elvish Mystic", seat="C")
        self.add(game.engine, "Forest", seat="C")
        bounce = self.add(game.engine, "Boomerang", zone="hand")
        untap = self.add(game.engine, "Vitalize", zone="hand")
        for card in (legal, illegal, bounce, untap):
            self.assert_closed(game, card)
        self.prepare(game, mana={"U": 2, "G": 1})
        self.checkpoint(game)
        offer = self.offer_activation(game, source)
        offered = offer["target_schema"]["legal_refs"]
        self.assertIn(legal.ref, offered)
        self.assertIn(source.ref, offered)
        self.assertNotIn(illegal.ref, offered)
        before = authoritative_state_hash(game.state)
        rejected = game.act("pilot:A", {"action_id": offer["id"], "targets": [illegal.ref], "pay": "auto"})
        self.assertFalse(rejected.ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        self.assertFalse(game.state.cards[source.object_id].tapped)
        self.activate(game, offer, legal)
        self.assertTrue(game.state.cards[source.object_id].tapped)
        game = self.replay(game, load=True)
        self.resolve_top(game)
        self.assertEqual(("B", "A"), (game.state.cards[legal.object_id].owner, game.state.cards[legal.object_id].controller))
        self.cast_for(game, "A", bounce, [island.ref])
        self.resolve_top(game)
        self.assertEqual("hand", game.state.cards[island.object_id].zone)
        self.assertEqual("A", game.state.cards[legal.object_id].controller)
        self.cast_for(game, "A", untap)
        self.resolve_top(game)
        self.assertFalse(game.state.cards[source.object_id].tapped)
        self.assertEqual("B", game.state.cards[legal.object_id].controller)
        self.replay(game, load=True)

    def test_actual_loss_of_the_targets_island_before_resolution_prevents_control_but_keeps_the_tap_cost(self):
        game = self.session(60820021)
        source = self.prepare_source(game)
        island = self.add(game.engine, "Tropical Island", seat="B")
        target = self.add(game.engine, "Centaur Courser", seat="B")
        bounce = self.add(game.engine, "Boomerang", zone="hand")
        for card in (target, bounce):
            self.assert_closed(game, card)
        self.prepare(game, mana={"U": 2})
        self.checkpoint(game)
        offer = self.offer_activation(game, source)
        self.assertIn(target.ref, offer["target_schema"]["legal_refs"])
        self.activate(game, offer, target)
        self.cast_for(game, "A", bounce, [island.ref])
        self.resolve_top(game)
        self.assertEqual("hand", game.state.cards[island.object_id].zone)
        game = self.replay(game, load=True)
        self.resolve_top(game)
        self.assertEqual("B", game.state.cards[target.object_id].controller)
        self.assertEqual(("battlefield", True), (game.state.cards[source.object_id].zone,
                                                 game.state.cards[source.object_id].tapped))
        self.replay(game, load=True)

    def test_actual_loss_of_the_sources_last_island_keeps_the_printed_maintenance(self):
        game = self.session(60380001)
        source = self.prepare_source(game)
        own_island = next(card for card in game.state.cards.values() if card.ref == "seasinger-own-island")
        bounce = self.add(game.engine, "Boomerang", zone="hand")
        self.assert_closed(game, bounce)
        self.prepare(game, mana={"U": 2})
        self.checkpoint(game)
        self.cast_for(game, "A", bounce, [own_island.ref])
        self.resolve_top(game)
        self.assertEqual(1, self.trigger_count(game, source))
        self.resolve_top(game)
        self.assertEqual("graveyard", game.state.cards[source.object_id].zone)
        self.replay(game, load=True)
