"""A live basic land threshold uses the same tally as the source's power bonus."""

from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune import characteristic_evaluation
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.characteristic_fragments import CharacteristicFragmentError, QueryCharacteristicModifierSpec
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


NAME = "Sardian Cliffstomper"
CAPABILITY = "continuous.characteristics.query_count_modifier"
PROFILE = "commander_review"


class ControllerTurnBasicLandThresholdPowerTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "controller-turn-land-count.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/controller-turn-basic-land-threshold-power-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Controller turn land count", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def program(self):
        record = self.db.lookup(NAME, fuzzy=False)
        binding, semantics = register_card(self.db, self.registry, NAME)
        self.assertTrue(binding["strict_capability_ready"], binding["blockers"])
        program, = semantics.programs_for_oracle(record.oracle_id, event="continuous")
        fragment = program.handlers[0]["fragment"]["value"]
        return record, program, QueryCharacteristicModifierSpec.from_dict(fragment)

    def test_complete_original_uses_one_current_basic_land_subtype_for_both_clauses(self):
        record, program, fragment = self.program()
        self.assertIn(CAPABILITY, program.capability_dependencies)
        self.assertEqual((2, True, 4, 1, 0),
                         (fragment.schema_version, fragment.controller_turn_only, fragment.minimum_count,
                          fragment.power, fragment.toughness))
        query = fragment.quantity.query
        self.assertEqual(("battlefield",), query.zones)
        self.assertEqual(("land",), query.types_all)
        self.assertEqual(("mountain",), query.subtypes_all)
        for bad in (
            record.oracle_text.replace("number of Mountains", "number of Islands"),
            record.oracle_text.replace("more Mountains", "more basic Mountains"),
            record.oracle_text.replace("your turn", "an opponent's turn"),
            record.oracle_text.replace("+X/+0", "+X/+X"),
        ):
            ir = compile_oracle_card(replace(record, oracle_text=bad), capability_registry=self.registry)
            self.assertNotEqual("exact", ir.status, bad)

    def test_raw_scope_and_owner_withdrawal_preserve_legacy_fragment_version(self):
        _, program, fragment = self.program()
        modern = fragment.to_dict()
        self.assertEqual(fragment, QueryCharacteristicModifierSpec.from_dict(modern))
        legacy = replace(fragment, schema_version=1, controller_turn_only=False, minimum_count=0).to_dict()
        self.assertNotIn("controller_turn_only", legacy)
        self.assertEqual(legacy, QueryCharacteristicModifierSpec.from_dict(legacy).to_dict())
        wrong_domain = deepcopy(modern)
        wrong_domain["quantity"]["query"]["types_all"] = ["creature"]
        for raw in (wrong_domain, {**modern, "controller_turn_only": False},
                    {**modern, "minimum_count": 0}, {**modern, "power": 2}):
            with self.assertRaises(CharacteristicFragmentError):
                QueryCharacteristicModifierSpec.from_dict(raw)
        dependencies = [cap for cap in program.capability_dependencies if cap != CAPABILITY]
        closure = self.registry.closure(dependencies, profile=PROFILE).to_dict() if dependencies else None
        binding = bind_semantic_program_runtime(
            replace(program, capability_dependencies=dependencies, capability_closure=closure),
            capability_registry=self.registry, profile=PROFILE,
        )
        self.assertFalse(binding["strict"], binding)
        self.assertIn("capability:undeclared_runtime_dependency:" + CAPABILITY, binding["blockers"])

    def scene(self, seed, *, extra_mountain=False):
        game = self.session(seed)
        engine = game.engine
        cliff = self.add(engine, NAME, zone="hand")
        moon = self.add(engine, "Blood Moon", zone="hand")
        removal = self.add(engine, "Disenchant", seat="B", zone="hand")
        new_land = self.add(engine, "Mountain", zone="hand", ref="cliff-land-in-hand")
        self.add(engine, "Taiga")
        tundra = self.add(engine, "Tundra")
        self.add(engine, "Savannah")
        self.add(engine, "Mountain", seat="C", ref="cliff-foreign-mountain")
        for index in range(2 + extra_mountain):
            self.add(engine, "Mountain", ref=f"cliff-own-mountain-{index}")
        for index in range(3):
            self.add(engine, "Plains", ref=f"cliff-own-plains-{index}")
        for index in range(2):
            self.add(engine, "Plains", seat="B", ref=f"cliff-disenchant-plains-{index}")
        hidden = self.add(engine, "Counterspell", seat="D", zone="hand", ref="cliff-hidden")
        for card in (cliff, moon, removal, tundra):
            self.assert_closed(game, card)
        self.prepare(game)
        self.checkpoint(game)
        self.cast_for(game, "A", cliff)
        self.resolve_top(game)
        return game, cliff, moon, removal, new_land, hidden

    def stats(self, game, card):
        data = game.engine._effective_card_data(game.state.cards[card.object_id])
        return int(data["power"]), int(data["toughness"])

    def next_turn(self, game):
        self.until(game, lambda current: current.state.active_player == "B"
                   and current.state.pending_decision.kind == "priority")

    def test_actual_power_tracks_blood_moon_land_entry_and_the_next_active_player(self):
        game, cliff, moon, removal, new_land, hidden = self.scene(61340344)
        self.assertEqual((0, 4), self.stats(game, cliff))
        self.cast_for(game, "A", moon)
        self.resolve_top(game)
        self.assertEqual((5, 4), self.stats(game, cliff))
        game = self.replay(game, load=True)

        self.cast_for(game, "B", removal, [moon.ref])
        self.resolve_top(game)
        self.assertEqual((0, 4), self.stats(game, cliff))
        self.priority(game, "A")
        command = {"action_id": f"play-land:{new_land.ref}"}
        actions = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        self.assertIn(command["action_id"], {row["id"] for row in actions})
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:B", command).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        accepted = game.act("pilot:A", command)
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertEqual((4, 4), self.stats(game, cliff))

        self.next_turn(game)
        self.assertEqual((0, 4), self.stats(game, cliff))
        for principal in ("pilot:A", "pilot:B", "pilot:C", "spectator"):
            self.assertNotIn(hidden.ref, json.dumps(game.packet(principal, full=True)))
        self.assertIn(hidden.ref, json.dumps(game.packet("pilot:D", full=True)))
        self.replay(game, load=True)

    def test_actual_turn_boundary_kills_always_using_the_source_controller(self):
        game, cliff, moon, _removal, new_land, _hidden = self.scene(61340345, extra_mountain=True)
        self.assertEqual((4, 4), self.stats(game, cliff))
        self.cast_for(game, "A", moon)
        self.resolve_top(game)
        self.priority(game, "A")
        played = game.act("pilot:A", {"action_id": f"play-land:{new_land.ref}"})
        self.assertTrue(played.ok, played.summary)
        self.assertEqual((7, 4), self.stats(game, cliff))
        self.next_turn(game)
        self.assertEqual((0, 4), self.stats(game, cliff))
        original = characteristic_evaluation._query_characteristic_effects

        def assume_controller_active(card, *args, **kwargs):
            return original(card, *args, **{**kwargs, "active_player": card.controller})

        with patch.object(characteristic_evaluation, "_query_characteristic_effects", assume_controller_active):
            def turn_boundary():
                self.assertEqual((0, 4), self.stats(game, cliff), "Current controller turn is required")
            with self.assertRaisesRegex(AssertionError, "Current controller turn is required"):
                turn_boundary()
