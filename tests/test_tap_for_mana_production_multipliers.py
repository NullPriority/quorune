"""Exercise controller tap-for-mana multipliers and actual colored provenance."""

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.semantic_runtime.context import SemanticNodeError
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


REFLECTION = "Mana Reflection"
ANCIENT = "Nyxbloom Ancient"
VIRTUE = "Virtue of Strength // Garenbrig Growth"


class TapForManaProductionMultiplierTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "tap-for-mana-multipliers.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/tap-for-mana-production-multipliers-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Tap-for-mana production", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def original_component(self, name):
        card = self.db.lookup(name, fuzzy=False)
        binding, semantics = register_card(self.db, self.registry, name)
        self.assertTrue(binding["strict_capability_ready"], binding["blockers"])
        component, = [item for item in semantics.programs_for_oracle(card.oracle_id)
                      if item.event == "action.permission"]
        return card, component

    def test_all_three_whole_originals_register_the_exact_factor_and_source_scope(self):
        for name, permission in ((REFLECTION, "double_tap_mana"), (ANCIENT, "triple_tap_mana"),
                                 (VIRTUE, "triple_basic_land_tap_mana")):
            with self.subTest(name=name):
                card, program = self.original_component(name)
                self.assertEqual("battlefield", program.active_zone)
                descriptor, = program.handlers
                self.assertEqual(permission, descriptor["permission"])
                for mutate in (
                    lambda text: text.replace("twice as much", "four times as much")
                                     .replace("three times as much", "four times as much"),
                    lambda text: text.replace("you tap", "an opponent taps"),
                ):
                    edited = replace(card, oracle_text=mutate(card.oracle_text),
                                     faces=tuple({**face, "oracle_text": mutate(face["oracle_text"])}
                                                 for face in card.faces))
                    self.assertNotEqual("exact", compile_oracle_card(edited,
                                        capability_registry=self.registry).status, edited.oracle_text)

    def test_raw_static_codec_rejects_an_extra_factor_or_nonbasic_scope(self):
        _, program = self.original_component(REFLECTION)
        descriptor, = program.handlers
        changed = replace(program, active_zone="hand")
        bound = bind_semantic_program_runtime(changed, capability_registry=self.registry,
                                              profile="commander_review")
        self.assertFalse(bound["strict"], bound)
        for fields in ({"factor": 4}, {"schema_version": True},
                       {"permission": "triple_basic_land_tap_mana"}, {"player": "B"}):
            with self.subTest(fields=fields), self.assertRaises(SemanticNodeError):
                replace(program, handlers=({**descriptor, **fields},))
        dependencies = tuple(capability for capability in program.capability_dependencies
                             if capability != "mana.production.tap_multiplier")
        without_multiplier = replace(
            program, capability_dependencies=dependencies,
            capability_closure=(self.registry.closure(dependencies, profile="commander_review").to_dict()
                                if dependencies else None),
        )
        bound = bind_semantic_program_runtime(without_multiplier, capability_registry=self.registry,
                                              profile="commander_review")
        self.assertFalse(bound["strict"], bound)
        self.assertIn("capability:undeclared_runtime_dependency:mana.production.tap_multiplier", bound["blockers"])

    def use_mana(self, game, seat, card, **fields):
        self.priority(game, seat)
        rows = game.packet("pilot:" + seat, full=True)["decision"]["ctx"]["legal"]["actions"]
        action, = [row["id"] for row in rows if row["id"].startswith("activate:" + card.ref + ":")]
        outcome = game.act("pilot:" + seat, {"action_id": action, **fields})
        self.assertTrue(outcome.ok, outcome.summary)

    def test_real_doubler_and_tripler_stack_and_do_not_scale_mana_without_a_tap_cost(self):
        game = self.session(61342901)
        engine = game.engine
        reflection = self.add(engine, REFLECTION, zone="hand")
        ancient = self.add(engine, ANCIENT, zone="hand")
        ring = self.add(engine, "Sol Ring")
        thrull = self.add(engine, "Basal Thrull")
        pet = self.add(engine, "Blood Pet")
        island = self.add(engine, "Island", seat="B")
        for card in (reflection, ancient, ring, thrull, pet):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 8, "G": 5})
        self.checkpoint(game)
        self.cast_for(game, "A", reflection, [])
        self.resolve_top(game)
        self.cast_for(game, "A", ancient, [])
        self.resolve_top(game)
        self.assertFalse(any(game.state.players["A"].mana_pool.values()))
        self.use_mana(game, "A", ring)
        self.assertEqual(12, game.state.players["A"].mana_pool["C"],
                         "The same two mana must be doubled and then tripled")
        offers = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        undo, = [row["id"] for row in offers if row["action"] == "undo_mana" and row["source"] == ring.ref]
        result = game.act("pilot:A", {"action_id": undo})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(0, game.state.players["A"].mana_pool["C"])
        self.assertFalse(game.state.cards[ring.object_id].tapped)
        self.use_mana(game, "A", ring)
        self.assertEqual(12, game.state.players["A"].mana_pool["C"])
        self.use_mana(game, "A", pet)
        self.assertEqual(1, game.state.players["A"].mana_pool["B"])
        self.use_mana(game, "A", thrull)
        self.assertEqual("graveyard", game.state.cards[thrull.object_id].zone)
        self.assertEqual(13, game.state.players["A"].mana_pool["B"])
        self.use_mana(game, "B", island)
        self.assertEqual(1, game.state.players["B"].mana_pool["U"])
        self.replay(game, load=True)

    def test_same_restricted_bundle_and_triggered_extra_mana_keep_their_owners(self):
        game = self.session(61342902)
        engine = game.engine
        reflection = self.add(engine, REFLECTION)
        forest = self.add(engine, "Forest")
        ziggurat = self.add(engine, "Ancient Ziggurat")
        growth = self.add(engine, "Wild Growth", zone="hand")
        opt = self.add(engine, "Opt", zone="hand", ref="mana-multiplier-private-opt")
        creature = self.add(engine, "Merfolk of the Pearl Trident", zone="hand", ref="mana-multiplier-private-merfolk")
        for card in (reflection, growth, ziggurat):
            self.assert_closed(game, card)
        self.prepare(game, mana={"G": 1})
        self.checkpoint(game)
        self.cast_for(game, "A", growth, [forest.ref])
        self.resolve_top(game)
        self.use_mana(game, "A", forest)
        self.assertEqual(3, game.state.players["A"].mana_pool["G"])
        self.use_mana(game, "A", ziggurat, mana_choice="U")
        self.assertEqual(2, game.state.players["A"].mana_pool["U"])
        self.assertEqual(2, game.engine._restricted_mana("A")["creature_spell_only"]["U"])
        for viewer in ("pilot:B", "pilot:C", "pilot:D", "spectator"):
            packet = json.dumps(game.packet(viewer, full=True))
            self.assertNotIn(opt.ref, packet)
            self.assertNotIn(creature.ref, packet)
        offers = {row["id"] for row in game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]}
        self.assertNotIn("cast:" + opt.ref, offers)
        self.assertIn("cast:" + creature.ref, offers)
        self.cast_for(game, "A", creature, [])
        self.resolve_top(game)
        self.assertEqual(1, game.engine._restricted_mana("A")["creature_spell_only"]["U"])
        self.replay(game, load=True)

    def test_automatic_payment_can_use_the_same_tripled_green_production(self):
        game = self.session(61342903)
        engine = game.engine
        ancient = self.add(engine, ANCIENT)
        forest = self.add(engine, "Forest")
        spell = self.add(engine, "Centaur Courser", zone="hand")
        self.assert_closed(game, ancient)
        self.prepare(game)
        self.checkpoint(game)
        self.cast_for(game, "A", spell, [])
        self.resolve_top(game)
        self.assertEqual("battlefield", game.state.cards[spell.object_id].zone)
        self.assertTrue(game.state.cards[forest.object_id].tapped)
        self.assertFalse(any(game.state.players["A"].mana_pool.values()))
        self.replay(game, load=True)

    def test_actual_adventure_and_virtue_distinguish_basic_supertype_from_forest_subtype(self):
        game = self.session(61342904)
        engine = game.engine
        virtue = self.add(engine, VIRTUE, zone="hand")
        forest = self.add(engine, "Forest", ref="virtue-basic-forest")
        arbor = self.add(engine, "Dryad Arbor", ref="virtue-nonbasic-forest")
        returned = self.add(engine, "Grizzly Bears", zone="graveyard")
        self.assert_closed(game, virtue)
        self.prepare(game, mana={"C": 5, "G": 3})
        self.checkpoint(game)
        offered, = [row["id"] for row in game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
                    if row["id"].endswith(":face:Garenbrig Growth")]
        outcome = game.act("pilot:A", {"action_id": offered, "face": "Garenbrig Growth",
                                       "targets": [returned.ref], "pay": "auto"})
        self.assertTrue(outcome.ok, outcome.summary)
        self.resolve_top(game)
        self.assertEqual("hand", game.state.cards[returned.object_id].zone)
        self.assertEqual("exile", game.state.cards[virtue.object_id].zone)
        game = self.replay(game, load=True)
        self.cast_for(game, "A", game.state.cards[virtue.object_id], [])
        self.resolve_top(game)
        self.use_mana(game, "A", forest)
        self.assertEqual(3, game.state.players["A"].mana_pool["G"])
        self.use_mana(game, "A", arbor)
        self.assertEqual(4, game.state.players["A"].mana_pool["G"])
        self.replay(game, load=True)

    def test_omitting_the_shared_multiplier_fails_the_actual_compounded_result(self):
        with patch("quorune.mana_ability_runtime.tap_mana_production_factor", return_value=1):
            with self.assertRaisesRegex(AssertionError, "The same two mana must be doubled and then tripled"):
                self.test_real_doubler_and_tripler_stack_and_do_not_scale_mana_without_a_tap_cost()
