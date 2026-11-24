"""The opposing upkeep participant's current hand size is a public damage amount."""

from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.maximum_hand_size import NO_MAXIMUM_HAND_SIZE_CAPABILITY_ID
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from quorune.upkeep_hand_difference_model import (
    HandDifferenceDirection, UPKEEP_HAND_DIFFERENCE_CAPABILITY, UpkeepHandDifferenceSpec,
)
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card
import test_upkeep_hand_differences as upkeep_controls


NAME = "Price of Knowledge"
PROFILE = "commander_review"


class OpponentUpkeepCurrentHandDamageTests(InheritedNativeFixture, unittest.TestCase):
    start = upkeep_controls.UpkeepHandDifferenceGameplayTests.start
    hand = upkeep_controls.UpkeepHandDifferenceGameplayTests.hand
    fill = upkeep_controls.UpkeepHandDifferenceGameplayTests.fill
    lands = upkeep_controls.UpkeepHandDifferenceGameplayTests.lands
    reject = upkeep_controls.UpkeepHandDifferenceGameplayTests.reject
    cast_closed = upkeep_controls.UpkeepHandDifferenceGameplayTests.cast_closed
    upkeep = upkeep_controls.UpkeepHandDifferenceGameplayTests.upkeep
    trigger = upkeep_controls.UpkeepHandDifferenceGameplayTests.trigger
    privacy = upkeep_controls.UpkeepHandDifferenceGameplayTests.privacy

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "opposing-upkeep-current-hand-damage.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/opponent-upkeep-current-hand-damage-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Opponent upkeep hand count", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def programs(self):
        record = self.db.lookup(NAME, fuzzy=False)
        binding, semantics = register_card(self.db, self.registry, NAME)
        self.assertTrue(binding["strict_capability_ready"], binding["blockers"])
        programs = semantics.programs_for_oracle(record.oracle_id)
        trigger, = [entry for entry in programs if entry.event == "step.begin"]
        self.assertTrue(any(NO_MAXIMUM_HAND_SIZE_CAPABILITY_ID in p.capability_dependencies for p in programs))
        return record, trigger

    def test_complete_original_keeps_global_cleanup_and_non_targeted_current_upkeep_count(self):
        record, program = self.programs()
        self.assertEqual("fixed-typed-effect-step-trigger-v1", program.provenance["template_id"])
        self.assertIsNone(program.target_schema)
        self.assertIn(UPKEEP_HAND_DIFFERENCE_CAPABILITY, program.capability_dependencies)
        damage, = program.effects
        self.assertEqual(("damage", "$source", "$context.player"), (damage["op"], damage["source"], damage["target"]))
        self.assertEqual(UpkeepHandDifferenceSpec(HandDifferenceDirection.HAND_MINUS_CONSTANT, 0, 2).scalar_amount(),
                         damage["amount"])
        for altered in (
            record.oracle_text.replace("each opponent's upkeep", "your upkeep"),
            record.oracle_text.replace("that player's hand", "your hand"),
            record.oracle_text.replace("to that player", "to target player"),
        ):
            ir = compile_oracle_card(replace(record, oracle_text=altered), capability_registry=self.registry)
            self.assertFalse(any(effect.get("amount") == damage["amount"]
                                 for face in ir.faces for node in face.nodes for effect in node.effects))

    def test_raw_direct_count_keeps_its_inner_version_and_sealed_opponent(self):
        _, program = self.programs()
        size = UpkeepHandDifferenceSpec(HandDifferenceDirection.HAND_MINUS_CONSTANT, 0, 2)
        self.assertEqual((0, 6), (size.evaluate(0), size.evaluate(6)))
        self.assertEqual(size, UpkeepHandDifferenceSpec.from_dict(size.to_dict()))
        for invalid in ({"schema_version": 1, "constant": 0}, {"schema_version": 2, "constant": 4}):
            with self.assertRaises(ValueError):
                UpkeepHandDifferenceSpec.from_dict({**size.to_dict(), **invalid})
        effect, = program.effects
        changed = deepcopy(effect)
        changed["target"] = "$source.controller"
        bad = bind_semantic_program_runtime(replace(program, effects=(changed,)),
            capability_registry=self.registry, profile=PROFILE)
        self.assertFalse(bad["strict"], bad)
        caps = [cap for cap in program.capability_dependencies if cap != UPKEEP_HAND_DIFFERENCE_CAPABILITY]
        bad = bind_semantic_program_runtime(replace(program, capability_dependencies=caps,
                    capability_closure=self.registry.closure(caps, profile=PROFILE).to_dict()),
                    capability_registry=self.registry, profile=PROFILE)
        self.assertFalse(bad["strict"], bad)
        self.assertIn("capability:undeclared_runtime_dependency:" + UPKEEP_HAND_DIFFERENCE_CAPABILITY, bad["blockers"])

    def scene(self, seed, *, response=None):
        game = self.start(seed)
        source = self.add(game.engine, NAME, zone="hand")
        self.fill(game, "B", 2 if response else 0)
        secret = self.add(game.engine, "Shock", seat="C", zone="hand", ref="price-hidden-c")
        card = None
        if response:
            card = self.add(game.engine, response, seat="B", zone="hand")
            self.lands(game, "B", "Island" if response == "Opportunity" else "Plains",
                       6 if response == "Opportunity" else 2)
        self.prepare(game, mana={"C": 6, "B": 1})
        self.checkpoint(game)
        self.cast_closed(game, "A", source)
        self.upkeep(game, "B")
        trigger = self.trigger(game, source)
        self.assertEqual(("A", "B", []), (trigger.controller, trigger.context["player"], trigger.targets))
        self.privacy(game, secret)
        return game, source, card, secret

    def test_actual_instant_response_changes_the_opponents_amount_before_normal_draw(self):
        game, _source, response, secret = self.scene(6082101, response="Opportunity")
        self.assertEqual(3, len(self.hand(game, "B")))
        self.cast_closed(game, "B", response, ["B"], top_only=True)
        self.assertEqual(6, len(self.hand(game, "B")))
        self.assertEqual(40, game.state.players["B"].life)
        draws = len(game.state.players["B"].draw_history)
        self.privacy(game, secret)
        self.assertIsNone(self.resolve(game))
        self.assertEqual((40, 34, 40, 40), tuple(game.state.players[seat].life for seat in "ABCD"))
        self.assertEqual(draws, len(game.state.players["B"].draw_history))
        self.replay(game, load=True)

    def test_actual_departed_source_deals_the_unchanged_players_current_count(self):
        game, source, response, secret = self.scene(6082102, response="Disenchant")
        old = game.state.cards[source.object_id].logical_object_id
        self.cast_closed(game, "B", response, [source.ref], top_only=True)
        self.assertEqual(("graveyard", 2), (game.state.cards[source.object_id].zone, len(self.hand(game, "B"))))
        with patch.object(game.engine, "_dispatch_semantic_event", wraps=game.engine._dispatch_semantic_event) as dispatch:
            self.assertIsNone(self.resolve(game))
        damage = [entry.args[1] for entry in dispatch.call_args_list if entry.args[0] == "damage.dealt"]
        self.assertEqual([(2, old)], [(event["amount"], event["source_logical_object_id"]) for event in damage])
        self.assertEqual(38, game.state.players["B"].life)
        self.privacy(game, secret)
        self.replay(game, load=True)

    def test_actual_empty_hand_creates_no_damage_event(self):
        game, _source, _response, _secret = self.scene(6082103)
        with patch.object(game.engine, "_dispatch_semantic_event", wraps=game.engine._dispatch_semantic_event) as dispatch:
            self.assertIsNone(self.resolve(game))
        self.assertFalse(any(entry.args[0] == "damage.dealt" for entry in dispatch.call_args_list))
        self.assertEqual(40, game.state.players["B"].life)
        self.replay(game, load=True)

    def test_actual_response_witness_kills_reusing_a_historical_subtraction(self):
        calculate = UpkeepHandDifferenceSpec.evaluate

        def subtract_four(spec, count):
            return max(0, count - 4) if spec.schema_version == 2 else calculate(spec, count)

        with patch.object(UpkeepHandDifferenceSpec, "evaluate", subtract_four):
            with self.assertRaisesRegex(AssertionError, r"\(40, 34, 40, 40\) != \(40, 38, 40, 40\)"):
                self.test_actual_instant_response_changes_the_opponents_amount_before_normal_draw()
