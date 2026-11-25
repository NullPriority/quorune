"""Current Auras select the source's fixed base stats and printed keyword band."""

from copy import deepcopy
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
from quorune.characteristic_fragments import CharacteristicQuantityScope
from quorune.deck import DeckDefinition, DeckEntry
from quorune.dynamic_characteristics import query_characteristic_count
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.semantic_runtime.context import SemanticNodeError
from quorune.semantic_runtime.fixed_characteristic_settings import (
    AURA_COUNT_SETTING_HANDLER_ID, FIXED_SETTING_CAPABILITY, attached_aura_count_setting_condition,
)
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


NAME = "Timber Paladin"
PROFILE = "commander_review"


class SourceAuraCountBaseCharacteristicsTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "source-aura-count.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/source-aura-count-base-characteristics-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Current attached Aura count", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def programs(self):
        original = self.db.lookup(NAME, fuzzy=False)
        binding, semantics = register_card(self.db, self.registry, NAME)
        self.assertTrue(binding["strict_capability_ready"], binding["blockers"])
        programs = semantics.programs_for_oracle(original.oracle_id, event="characteristics.evaluate")
        self.assertEqual(3, len(programs))
        return original, programs

    def test_complete_original_retains_three_distinct_current_aura_bands(self):
        record, programs = self.programs()
        by_power = {program.handlers[0]["modifier"]["base_power"]: program for program in programs}
        self.assertEqual({3, 5, 10}, set(by_power))
        for count, power, exact, keywords in (
                (1, 3, True, set()), (2, 5, True, {"vigilance"}),
                (3, 10, False, {"vigilance", "trample"})):
            program = by_power[power]
            self.assertIn(FIXED_SETTING_CAPABILITY, program.capability_dependencies)
            descriptor, = program.handlers
            self.assertEqual(AURA_COUNT_SETTING_HANDLER_ID, descriptor["handler_id"])
            self.assertEqual(attached_aura_count_setting_condition(count, exact=exact).to_dict(),
                             descriptor["source_condition"])
            self.assertEqual(power, descriptor["modifier"]["base_toughness"])
            self.assertEqual(keywords, {item.casefold() for item in descriptor["modifier"]["add_abilities"]})
        for altered in (
            record.oracle_text.replace("three or more Auras", "three or more Auras you control"),
            record.oracle_text.replace("exactly one Aura", "exactly one Equipment"),
            record.oracle_text.replace("it has base power", "target creature has base power"),
            record.oracle_text.replace("and trample", "and is an artifact"),
        ):
            ir = compile_oracle_card(replace(record, oracle_text=altered), capability_registry=self.registry)
            self.assertNotEqual("exact", ir.status)

    def test_raw_aura_count_gate_rejects_wrong_scope_and_loses_closure_without_settings_owner(self):
        _, programs = self.programs()
        chosen = next(program for program in programs if program.handlers[0]["modifier"]["base_power"] == 3)
        original, = chosen.handlers
        for update in (
            {"source_condition": None},
            {"source_condition": {**original["source_condition"], "kind": "any"}},
            {"target": {**original["target"], "kind": "fixed_query", "controller": "any"}},
            {"modifier": {**original["modifier"], "power": 1}},
        ):
            with self.assertRaises(SemanticNodeError):
                replace(chosen, handlers=({**deepcopy(original), **update},))
        dependencies = [cap for cap in chosen.capability_dependencies if cap != FIXED_SETTING_CAPABILITY]
        binding = bind_semantic_program_runtime(
            replace(chosen, capability_dependencies=dependencies,
                    capability_closure=self.registry.closure(dependencies, profile=PROFILE).to_dict()),
            capability_registry=self.registry, profile=PROFILE,
        )
        self.assertFalse(binding["strict"], binding)
        self.assertIn("capability:undeclared_runtime_dependency:" + FIXED_SETTING_CAPABILITY, binding["blockers"])

    def scene(self, seed, *, full):
        game = self.session(seed)
        engine = game.engine
        knight = self.add(engine, NAME, zone="hand")
        sword = self.add(engine, "Short Sword")
        cards = {}
        if full:
            self.add(engine, "Vedalken Orrery", seat="B")
            for name, seat in (("Flight", "A"), ("Holy Strength", "A"), ("Pacifism", "B"),
                               ("Diminish", "B"), ("Disenchant", "B"), ("Counterspell", "D")):
                cards[name] = self.add(engine, name, seat=seat, zone="hand", ref="aura-band-" + name.lower().replace(" ", "-"))
            for land, seat, count in (("Island", "A", 3), ("Plains", "A", 2), ("Island", "B", 2), ("Plains", "B", 2)):
                for index in range(count):
                    self.add(engine, land, seat=seat, ref=f"aura-band-{seat}-{land}-{index}")
        for index in range(3):
            self.add(engine, "Forest", ref=f"aura-band-payment-{index}")
        for card in (knight, sword, *cards.values()):
            self.assert_closed(game, card)
        self.prepare(game)
        self.checkpoint(game)
        self.cast_for(game, "A", knight)
        self.resolve_top(game)
        return game, knight, sword, cards

    def equip(self, game, sword, knight):
        self.priority(game, "A")
        actions = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        offer, = [row for row in actions if row["id"].startswith(f"activate:{sword.ref}:")]
        command = {"action_id": offer["id"], "targets": [knight.ref], "pay": "auto"}
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:B", command).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        accepted = game.act("pilot:A", command)
        self.assertTrue(accepted.ok, accepted.summary)
        self.resolve_top(game)

    def characteristics(self, game, knight):
        card = game.state.cards[knight.object_id]
        data = game.engine._effective_card_data(card)
        relevant = set(game.engine._combat_keywords(card)) & {"flying", "vigilance", "trample"}
        return (int(data["power"]), int(data["toughness"])), relevant

    def test_actual_foreign_aura_changes_the_band_without_retimestamping_base_stats(self):
        game, knight, sword, cards = self.scene(61373401, full=True)
        self.assertEqual(((1, 1), set()), self.characteristics(game, knight))
        self.equip(game, sword, knight)
        self.assertEqual(((2, 2), set()), self.characteristics(game, knight))

        self.cast_for(game, "A", cards["Flight"], [knight.ref])
        self.resolve_top(game)
        self.assertEqual(((4, 4), {"flying"}), self.characteristics(game, knight))
        self.cast_for(game, "B", cards["Pacifism"], [knight.ref])
        self.resolve_top(game)
        self.assertEqual("B", game.state.cards[cards["Pacifism"].object_id].controller)
        self.assertEqual(((6, 6), {"flying", "vigilance"}), self.characteristics(game, knight))

        self.cast_for(game, "B", cards["Diminish"], [knight.ref])
        self.resolve_top(game)
        self.assertEqual(((2, 2), {"flying", "vigilance"}), self.characteristics(game, knight))
        self.cast_for(game, "A", cards["Holy Strength"], [knight.ref])
        self.resolve_top(game)
        self.assertEqual(((3, 4), {"flying", "vigilance", "trample"}), self.characteristics(game, knight))
        self.until(game, lambda current: current.state.active_player == "B"
                   and current.state.pending_decision.kind == "priority")
        self.assertEqual(((12, 13), {"flying", "vigilance", "trample"}), self.characteristics(game, knight))
        self.cast_for(game, "B", cards["Disenchant"], [cards["Pacifism"].ref])
        self.resolve_top(game)
        self.assertEqual(((7, 8), {"flying", "vigilance"}), self.characteristics(game, knight))
        for principal in ("pilot:A", "pilot:B", "pilot:C", "spectator"):
            self.assertNotIn(cards["Counterspell"].ref, json.dumps(game.packet(principal, full=True)))
        self.assertIn(cards["Counterspell"].ref, json.dumps(game.packet("pilot:D", full=True)))
        self.replay(game, load=True)

    def test_paid_equip_witness_kills_counting_non_aura_attachments(self):
        game, knight, sword, _cards = self.scene(61373402, full=False)
        self.equip(game, sword, knight)

        def no_aura_filter(host, source, quantity, **kwargs):
            if quantity.scope is CharacteristicQuantityScope.ATTACHED_TO_SOURCE:
                return len(source.attachments)
            return query_characteristic_count(host, source, quantity, **kwargs)

        def witness():
            self.assertEqual(((2, 2), set()), self.characteristics(game, knight), "Only Aura attachments count")

        witness()
        with patch("quorune.characteristic_evaluation_host.query_characteristic_count", no_aura_filter):
            with self.assertRaisesRegex(AssertionError, "Only Aura attachments count"):
                witness()
