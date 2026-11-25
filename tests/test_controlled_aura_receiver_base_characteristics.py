"""A qualifying Aura and its creature use the source's current controller."""

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
from quorune.object_query import object_matches_query
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.semantic_runtime.context import SemanticNodeError
from quorune.semantic_runtime.fixed_characteristic_settings import (
    CONTROLLED_AURA_RECIPIENT_SETTING_HANDLER_ID, FIXED_SETTING_CAPABILITY,
)
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


NAME = "Archon of the Wild Rose"
PROFILE = "commander_review"


class ControlledAuraReceiverBaseCharacteristicsTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "controlled-aura-recipient.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/controlled-aura-receiver-base-characteristics-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Controlled Aura and recipient", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def program(self):
        card = self.db.lookup(NAME, fuzzy=False)
        binding, semantics = register_card(self.db, self.registry, NAME)
        self.assertTrue(binding["strict_capability_ready"], binding["blockers"])
        program, = semantics.programs_for_oracle(card.oracle_id, event="characteristics.evaluate")
        return card, program

    def test_complete_original_keeps_intrinsic_flying_and_one_exact_recipient_query(self):
        card, program = self.program()
        self.assertIn(FIXED_SETTING_CAPABILITY, program.capability_dependencies)
        descriptor, = program.handlers
        self.assertEqual(CONTROLLED_AURA_RECIPIENT_SETTING_HANDLER_ID, descriptor["handler_id"])
        target = descriptor["target"]
        self.assertEqual("source_controller", target["controller"])
        self.assertTrue(target["exclude_source"])
        self.assertEqual(["creature"], target["predicate"]["types_all"])
        self.assertEqual((4, 4), tuple(descriptor["modifier"][key] for key in ("base_power", "base_toughness")))
        ir = compile_oracle_card(card, capability_registry=self.registry)
        self.assertTrue(any(node.template_id == "printed-keyword-list-v1" for face in ir.faces for node in face.nodes))
        for changed in (
            card.oracle_text.replace("Other creatures", "Creatures"),
            card.oracle_text.replace("by Auras you control", "by Auras"),
            card.oracle_text.replace("by Auras you control", "by Auras you own"),
            card.oracle_text.replace("4/4 and have flying", "4/4 and are enchantments"),
        ):
            result = compile_oracle_card(replace(card, oracle_text=changed), capability_registry=self.registry)
            self.assertNotEqual("exact", result.status)

    def test_raw_recipient_scope_and_characters_are_closed_under_owner_withdrawal(self):
        _, program = self.program()
        descriptor, = program.handlers
        target = descriptor["target"]
        for invalid in (
            {**descriptor, "target": {**target, "exclude_source": False}},
            {**descriptor, "target": {**target, "controller": "any"}},
            {**descriptor, "modifier": {**descriptor["modifier"], "power": 1}},
        ):
            with self.assertRaises(SemanticNodeError):
                replace(program, handlers=(invalid,))
        caps = [value for value in program.capability_dependencies if value != FIXED_SETTING_CAPABILITY]
        closure = self.registry.closure(caps, profile=PROFILE).to_dict() if caps else None
        binding = bind_semantic_program_runtime(
            replace(program, capability_dependencies=caps, capability_closure=closure),
            capability_registry=self.registry, profile=PROFILE,
        )
        self.assertFalse(binding["strict"], binding)
        self.assertIn("capability:undeclared_runtime_dependency:" + FIXED_SETTING_CAPABILITY, binding["blockers"])

    def scene(self, seed, *, full):
        game = self.session(seed)
        engine = game.engine
        archon = self.add(engine, NAME, zone="hand")
        own = self.add(engine, "Grizzly Bears", ref="aura-controller-own-bear")
        other = self.add(engine, "Grizzly Bears", ref="aura-controller-foreign-bear")
        self.add(engine, "Vedalken Orrery", seat="B")
        spells = {"Pacifism": self.add(engine, "Pacifism", seat="B", zone="hand")}
        land_groups = [("A", "Plains", 5), ("B", "Plains", 2)]
        if full:
            self.add(engine, "Kudo, King Among Bears", seat="C")
            land_groups.extend((("A", "Island", 3), ("A", "Plains", 2), ("B", "Island", 3)))
            for name, seat in (("Holy Strength", "A"), ("Flight", "A"), ("Steal Enchantment", "B"),
                               ("Disenchant", "A"), ("Counterspell", "D")):
                spells[name] = self.add(engine, name, seat=seat, zone="hand",
                                        ref=f"aura-controller-{name.lower().replace(' ', '-')}")
        for group, (seat, land, count) in enumerate(land_groups):
            for index in range(count):
                self.add(engine, land, seat=seat, ref=f"aura-controller-mana-{group}-{index}")
        for card in (archon, *spells.values()):
            self.assert_closed(game, card)
        self.prepare(game)
        self.checkpoint(game)
        self.cast_for(game, "A", archon)
        self.resolve_top(game)
        return game, archon, own, other, spells

    def enchant_with(self, game, seat, aura, target):
        self.cast_for(game, seat, aura, [target.ref])
        self.resolve_top(game)

    def characteristics(self, game, creature):
        record = game.state.cards[creature.object_id]
        data = game.engine._effective_card_data(record)
        return int(data["power"]), int(data["toughness"]), "flying" in game.engine._combat_keywords(record)

    def test_actual_aura_control_removes_and_restores_only_the_qualifying_recipient(self):
        game, archon, own, other, spells = self.scene(109303405, full=True)
        self.assertEqual((2, 2, True), self.characteristics(game, archon))
        self.assertEqual((2, 2, False), self.characteristics(game, own))

        self.enchant_with(game, "A", spells["Holy Strength"], own)
        self.assertEqual((5, 6, True), self.characteristics(game, own))
        self.enchant_with(game, "A", spells["Flight"], archon)
        self.assertEqual((2, 2, True), self.characteristics(game, archon))

        self.enchant_with(game, "B", spells["Pacifism"], other)
        self.assertEqual((2, 2, False), self.characteristics(game, other))
        self.priority(game, "B")
        stealing = spells["Steal Enchantment"]
        strength = spells["Holy Strength"]
        command = {"action_id": f"cast:{stealing.ref}", "targets": [strength.ref], "pay": "auto"}
        offered = game.packet("pilot:B", full=True)["decision"]["ctx"]["legal"]["actions"]
        self.assertIn(command["action_id"], {row["id"] for row in offered})
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:C", command).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        accepted = game.act("pilot:B", command)
        self.assertTrue(accepted.ok, accepted.summary)
        self.resolve_top(game)
        self.assertEqual(("A", "B"), (game.state.cards[strength.object_id].owner, game.state.cards[strength.object_id].controller))
        self.assertEqual("A", game.state.cards[own.object_id].controller)
        self.assertEqual((3, 4, False), self.characteristics(game, own))

        self.enchant_with(game, "A", spells["Disenchant"], stealing)
        self.assertEqual("A", game.state.cards[strength.object_id].controller)
        self.assertEqual((5, 6, True), self.characteristics(game, own))
        self.assertEqual((2, 2, False), self.characteristics(game, other))
        for principal in ("pilot:A", "pilot:B", "pilot:C", "spectator"):
            self.assertNotIn(spells["Counterspell"].ref, json.dumps(game.packet(principal, full=True)))
        self.assertIn(spells["Counterspell"].ref, json.dumps(game.packet("pilot:D", full=True)))
        self.replay(game, load=True)

    def test_actual_foreign_aura_kills_matching_only_the_creature_controller(self):
        game, _archon, _own, other, spells = self.scene(109303406, full=False)
        self.enchant_with(game, "B", spells["Pacifism"], other)

        def any_aura_controller(row, query):
            if query.types_all == ("enchantment",) and query.subtypes_all == ("aura",):
                query = replace(query, controller=None)
            return object_matches_query(row, query)

        def witness():
            self.assertEqual((2, 2, False), self.characteristics(game, other), "The Aura needs the source controller")

        witness()
        with patch("quorune.semantic_runtime.fixed_characteristic_settings.object_matches_query", any_aura_controller):
            with self.assertRaisesRegex(AssertionError, "The Aura needs the source controller"):
                witness()
