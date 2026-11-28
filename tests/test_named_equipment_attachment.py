from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.characteristic_fragments import NAMED_ATTACHED_EQUIPMENT_CAPABILITY
from quorune.compiler.otherwise_characteristic_templates import composed_public_state_characteristics_handler
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_qualified_zone_event_queries import query_record


def synthetic_static_clause():
    return (
        "Equipped creature gets +1/+0. It gets an additional +0/+1 and has vigilance "
        "as long as an Equipment named Relay Bracket is attached to a creature you control."
    )


class NamedEquipmentAttachmentCompilerTests(unittest.TestCase):
    def test_companion_relation_has_a_separate_typed_capability(self):
        text = synthetic_static_clause()
        parsed = composed_public_state_characteristics_handler(text, source_name="Signal Harness")
        self.assertIsNotNone(parsed)
        base, conditional = parsed[1]
        self.assertEqual((1, 0), (base["modifier"]["power"], base["modifier"]["toughness"]))
        self.assertEqual((0, 1), (conditional["modifier"]["power"], conditional["modifier"]["toughness"]))
        quantity = conditional["source_condition"]["quantity"]
        self.assertEqual("equipment_on_controlled_creatures", quantity["scope"])
        self.assertEqual("Relay Bracket", quantity["literal_name"])
        self.assertIn(NAMED_ATTACHED_EQUIPMENT_CAPABILITY, parsed[2])
        registry = load_default_capability_registry()
        for changed in (
            text.replace("Relay Bracket", "Relay Bracket or Other Equipment"),
            text.replace("a creature you control", "the equipped creature"),
            text.replace("Equipment named Relay Bracket", "artifact named Relay Bracket"),
        ):
            with self.subTest(text=changed):
                self.assertIsNone(composed_public_state_characteristics_handler(changed, source_name="Signal Harness"))
                record = replace(query_record(changed), type_line="Artifact — Equipment")
                self.assertNotEqual("exact", compile_oracle_card(record, capability_registry=registry).status)

    def test_quantity_uses_current_name_and_reciprocal_public_equipment(self):
        from quorune.attachments import attach_objects
        from quorune.characteristic_evaluation import type_parts
        from quorune.continuous_conditions import FixedPublicStateConditionSpec
        from quorune.dynamic_characteristics import query_characteristic_count
        from quorune.model import CardInstance

        parsed = composed_public_state_characteristics_handler(synthetic_static_clause(), source_name="Signal Harness")
        quantity = FixedPublicStateConditionSpec.from_dict(parsed[1][1]["source_condition"]).quantity
        source = CardInstance("source", "source", "test:source", "Signal Harness", "A", "A", "battlefield")
        wearer = CardInstance("wearer", "wearer", "test:wearer", "Test Creature", "A", "A", "battlefield")
        companion = CardInstance("partner", "partner", "test:partner", "Unrelated Form", "B", "B", "battlefield")
        cards = {card.object_id: card for card in (source, wearer, companion)}
        attach_objects(cards, companion, wearer, source_timestamp=1)
        data = {
            wearer.object_id: {"name": "Test Creature", "type_line": "Creature — Human"},
            companion.object_id: {"name": "Relay Bracket", "type_line": "Artifact — Equipment"},
        }
        state = SimpleNamespace(cards=cards, players={"A": SimpleNamespace(
            in_game=True, zones={"battlefield": [source.object_id, wearer.object_id]},
        )})
        host = SimpleNamespace(state=state, _type_parts=type_parts,
            _effective_card_data=lambda card, **_options: data[card.object_id])

        self.assertEqual(1, query_characteristic_count(host, source, quantity))
        companion.phased_out = True
        self.assertEqual(0, query_characteristic_count(host, source, quantity))
        companion.phased_out = False
        companion.face_down = True
        self.assertEqual(0, query_characteristic_count(host, source, quantity))
        companion.face_down = False
        data[companion.object_id]["type_line"] = "Artifact — Clue"
        self.assertEqual(0, query_characteristic_count(host, source, quantity))
        data[companion.object_id]["type_line"] = "Artifact — Equipment"
        wearer.attachments.clear()
        self.assertEqual(0, query_characteristic_count(host, source, quantity))


class NamedEquipmentAttachmentNativeTests(InheritedNativeFixture, unittest.TestCase):
    equipment = ("Signal Harness", "Relay Bracket")
    keywords = ("vigilance", "lifelink")
    bases = ((3, 2), (2, 3))
    boosted = ((3, 3), (3, 3))
    private_card = "Generic Bound Growth"

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        database = Path(cls.temporary.name) / "named-equipment.sqlite3"
        fixtures = ROOT / "tests/fixtures"
        build_fixture_database(
            [fixtures / "bound-effect-program-cards.json",
             fixtures / "recipient-control-cards.json",
             fixtures / "named-equipment-attachment-cards.json"],
            database,
        )
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Attachment scope", [
            DeckEntry("Generic Bound Commander", 1, "commander"),
            DeckEntry("Generic Bound Plains", 30),
        ], ["Generic Bound Commander"])

    def equip(self, game, equipment, target):
        self.priority(game, "A")
        source = game.state.cards[equipment.object_id]
        actions = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        offer, = (row for row in actions if row["id"].startswith("activate:" + source.ref + ":"))
        actual = game.act("pilot:A", {"action_id": offer["id"], "targets": [target.ref], "pay": "auto"})
        self.assertTrue(actual.ok, actual.summary)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(target.object_id, game.state.cards[equipment.object_id].attached_to)

    def donate(self, game, spell, target):
        self.priority(game, "A")
        offer = self.action(game, "A", game.state.cards[spell.object_id])
        result = game.act("pilot:A", {"action_id": offer["id"], "targets": ["B", target.ref], "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("B", game.state.cards[target.object_id].controller)

    def assert_modifier(self, game, target, index, enabled):
        current = game.state.cards[target.object_id]
        stats = tuple(game.engine._numeric_stat(current.object_id, stat) for stat in ("power", "toughness"))
        self.assertEqual(self.boosted[index] if enabled else self.bases[index], stats,
                         "modifier for the distinct attachment roles")
        abilities = {name.casefold() for name in game.engine._effective_card_data(current)["keywords"]}
        self.assertEqual(enabled, self.keywords[index] in abilities)

    def test_live_distinct_recipients_and_foreign_equipment_keep_the_current_relation(self):
        game = self.session(61133017)
        primary, companion = [self.add(game.engine, name) for name in self.equipment]
        worn = self.add(game.engine, "Grizzly Bears", ref="equipment-beneficiary")
        qualified = self.add(game.engine, "Grizzly Bears", ref="equipment-qualifying-creature")
        donations = [self.add(game.engine, "Donate", zone="hand", ref=f"equipment-donation-{i}")
                     for i in range(3)]
        secret = self.add(game.engine, self.private_card, seat="D", zone="hand", ref="equipment-private-card")
        for card in (primary, companion, *donations):
            self.assert_closed(game, card)
        game.state.config.semantic_policy = "trusted_only"
        self.prepare(game, mana={"C": 16, "U": 3})
        self.checkpoint(game)
        self.equip(game, primary, worn)
        self.assert_modifier(game, worn, 0, False)
        self.equip(game, companion, qualified)
        self.assert_modifier(game, worn, 0, True)
        self.assert_modifier(game, qualified, 1, True)
        self.donate(game, donations[0], companion)
        self.assert_modifier(game, worn, 0, True)
        self.assert_modifier(game, qualified, 1, False)
        game = self.replay(game, load=True)
        self.donate(game, donations[1], qualified)
        self.assert_modifier(game, worn, 0, False)
        self.donate(game, donations[2], primary)
        self.assertEqual("A", game.state.cards[worn.object_id].controller)
        self.assert_modifier(game, worn, 0, True)
        self.assert_modifier(game, qualified, 1, False)
        self.assertNotIn(secret.ref, json.dumps(game.packet("pilot:A", full=True)))
        self.replay(game, load=True)

    def test_missing_attachment_scan_changes_the_live_native_result(self):
        witness = type(self)("test_live_distinct_recipients_and_foreign_equipment_keep_the_current_relation")
        result = unittest.TestResult()
        with patch("quorune.dynamic_characteristics._controlled_creature_attachment_ids", return_value=()):
            witness.run(result)
        self.assertFalse(result.errors)
        self.assertEqual(1, len(result.failures))
        self.assertIn("modifier for the distinct attachment roles", result.failures[0][1])

    def test_omitting_the_companion_capability_does_not_admit_the_program(self):
        from quorune.card_programs.binding import bind_semantic_program_runtime
        from test_controller_creature_keyword_absence import register_card

        profile = "commander_review"
        record = self.db.lookup(self.equipment[0], fuzzy=False)
        result, semantics = register_card(self.db, self.registry, self.equipment[0])
        self.assertTrue(result["strict_capability_ready"], result["blockers"])
        program, = (item for item in semantics.programs_for_oracle(record.oracle_id)
                    if NAMED_ATTACHED_EQUIPMENT_CAPABILITY in item.capability_dependencies)
        dependencies = tuple(item for item in program.capability_dependencies
                             if item != NAMED_ATTACHED_EQUIPMENT_CAPABILITY)
        stripped = replace(program, coverage=[], capability_dependencies=dependencies,
                           capability_closure=self.registry.closure(dependencies, profile=profile).to_dict())
        binding = bind_semantic_program_runtime(stripped, capability_registry=self.registry, profile=profile)
        self.assertFalse(binding["strict"])
        self.assertIn("capability:undeclared_runtime_dependency:" + NAMED_ATTACHED_EQUIPMENT_CAPABILITY,
                      binding["blockers"])


if __name__ == "__main__":
    unittest.main()
