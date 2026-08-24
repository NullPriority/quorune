from __future__ import annotations

import contextlib
from dataclasses import replace
import io
import json
import random
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

from common import DB_PATH, keep_all
from quorune import (
    CardDatabase,
    CommanderSession,
    GameConfig,
)
from quorune.cli import main
from quorune.carddb import CardRecord
from quorune.deck import DeckDefinition, DeckEntry
from quorune.damage import (
    commit_prepared_damage_batch,
    damage_proposal,
    prepare_damage_batch,
)
from quorune.mechanic_contracts import (
    MechanicContractError,
    apply_contracts_to_registry,
    load_mechanic_contracts,
    validate_mechanic_contract,
)
from quorune.model import StackItem
from quorune.oracle_ir import (
    ORACLE_COMPILER_VERSION,
    compile_oracle_card,
    generated_programs,
    oracle_corpus_coverage,
    register_generated_programs,
)
from quorune.rules_corpus import verify_rules_corpus
from quorune.rules.capabilities import (
    load_default_capability_registry,
)
from quorune.record import checkpoint_envelope, replay_record
from quorune.semantics import (
    SemanticProgram,
    SemanticRegistry,
)
from quorune.util import normalize_card_name


ROOT = Path(__file__).resolve().parents[1]


class MechanicContractTests(unittest.TestCase):
    def test_partial_contracts_overlay_without_claiming_trust(self):
        registry = json.loads(
            (ROOT / "mechanics" / "registry.json").read_text(
                encoding="utf-8"
            )
        )
        contracts = load_mechanic_contracts(
            ROOT,
            expected_effective_date=registry["effective_date"],
            expected_source_sha256=registry["source_sha256"],
            known_rule_ids={
                row["rule_id"]
                for row in json.loads(
                    (ROOT / "rules" / "rule-index.json").read_text(
                        encoding="utf-8"
                    )
                )["rules"]
            },
        )
        self.assertEqual(
            {
                str(contract["mechanic_id"])
                for contract in registry.get("contracts", [])
            },
            {contract["mechanic_id"] for contract in contracts},
        )
        overlaid = apply_contracts_to_registry(registry, contracts)
        flying = next(
            row
            for row in overlaid["mechanics"]
            if row["mechanic_id"] == "flying"
        )
        reach = next(
            row
            for row in overlaid["mechanics"]
            if row["mechanic_id"] == "reach"
        )
        self.assertEqual("trusted", flying["coverage_status"])
        self.assertEqual("trusted", flying["trust_level"])
        self.assertEqual([], flying["known_blockers"])
        self.assertEqual("trusted", reach["coverage_status"])
        self.assertEqual("trusted", reach["trust_level"])
        self.assertEqual([], reach["known_blockers"])
        self.assertTrue(verify_rules_corpus(ROOT)["ok"])

    def test_trusted_contract_cannot_retain_known_blockers(self):
        contract = json.loads(
            (
                ROOT
                / "mechanics"
                / "contracts"
                / "flying.json"
            ).read_text(encoding="utf-8")
        )
        contract["coverage_status"] = "trusted"
        contract["trust_level"] = "trusted"
        contract["review_status"] = "reviewed"
        contract["known_blockers"] = ["fixture blocker"]
        with self.assertRaises(MechanicContractError):
            validate_mechanic_contract(contract)


class OracleIRTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._temporary_directory = tempfile.TemporaryDirectory()
        cls.db_path = (
            Path(cls._temporary_directory.name)
            / "oracle-ir-test.sqlite3"
        )
        shutil.copy2(DB_PATH, cls.db_path)
        cls._insert_synthetic_cards(cls.db_path)
        cls.db = CardDatabase(cls.db_path)

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls._temporary_directory.cleanup()

    @staticmethod
    def _insert_synthetic_cards(path: Path) -> None:
        cards = [
            {
                "oracle_id": "00000000-0000-4000-8000-000000000001",
                "name": "Lightning Bolt",
                "mana_cost": "{R}",
                "mana_value": 1.0,
                "type_line": "Instant",
                "oracle_text": (
                    "Lightning Bolt deals 3 damage to any target."
                ),
                "colors": ["R"],
                "color_identity": ["R"],
            },
            {
                "oracle_id": "00000000-0000-4000-8000-000000000002",
                "name": "Rest in Peace",
                "mana_cost": "{1}{W}",
                "mana_value": 2.0,
                "type_line": "Enchantment",
                "oracle_text": (
                    "When Rest in Peace enters, exile all graveyards.\n"
                    "If a card or token would be put into a graveyard "
                    "from anywhere, exile it instead."
                ),
                "colors": ["W"],
                "color_identity": ["W"],
            },
            {
                "oracle_id": "00000000-0000-4000-8000-000000000003",
                "name": "Divination",
                "mana_cost": "{2}{U}",
                "mana_value": 3.0,
                "type_line": "Sorcery",
                "oracle_text": "Draw two cards.",
                "colors": ["U"],
                "color_identity": ["U"],
            },
            {
                "oracle_id": "00000000-0000-4000-8000-000000000004",
                "name": "Grizzly Bears",
                "mana_cost": "{1}{G}",
                "mana_value": 2.0,
                "type_line": "Creature — Bear",
                "oracle_text": "",
                "power": "2",
                "toughness": "2",
                "colors": ["G"],
                "color_identity": ["G"],
            },
            {
                "oracle_id": "00000000-0000-4000-8000-000000000005",
                "name": "Flying Men",
                "mana_cost": "{U}",
                "mana_value": 1.0,
                "type_line": "Creature — Human",
                "oracle_text": "Flying",
                "power": "1",
                "toughness": "1",
                "colors": ["U"],
                "color_identity": ["U"],
                "keywords": ["Flying"],
            },
            {
                "oracle_id": "00000000-0000-4000-8000-000000000006",
                "name": "Llanowar Elves",
                "mana_cost": "{G}",
                "mana_value": 1.0,
                "type_line": "Creature — Elf Druid",
                "oracle_text": "{T}: Add {G}.",
                "power": "1",
                "toughness": "1",
                "colors": ["G"],
                "color_identity": ["G"],
                "keywords": ["Mana Ability"],
                "produced_mana": ["G"],
            },
            {
                "oracle_id": "00000000-0000-4000-8000-000000000007",
                "name": "Elvish Visionary",
                "mana_cost": "{1}{G}",
                "mana_value": 2.0,
                "type_line": "Creature — Elf Shaman",
                "oracle_text": (
                    "When this creature enters, draw a card."
                ),
                "power": "1",
                "toughness": "1",
                "colors": ["G"],
                "color_identity": ["G"],
            },
            {
                "oracle_id": "00000000-0000-4000-8000-000000000008",
                "name": "Kingfisher",
                "mana_cost": "{3}{U}",
                "mana_value": 4.0,
                "type_line": "Creature — Bird",
                "oracle_text": (
                    "Flying\nWhen this creature dies, draw a card."
                ),
                "power": "2",
                "toughness": "2",
                "colors": ["U"],
                "color_identity": ["U"],
                "keywords": ["Flying"],
            },
            {
                "oracle_id": "00000000-0000-4000-8000-000000000009",
                "name": "Moss Diamond",
                "mana_cost": "{2}",
                "mana_value": 2.0,
                "type_line": "Artifact",
                "oracle_text": (
                    "This artifact enters tapped.\n{T}: Add {G}."
                ),
                "produced_mana": ["G"],
                "color_identity": ["G"],
                "keywords": ["Mana Ability"],
            },
            {
                "oracle_id": "00000000-0000-4000-8000-000000000010",
                "name": "Sprout",
                "mana_cost": "{G}",
                "mana_value": 1.0,
                "type_line": "Instant",
                "oracle_text": (
                    "Create a 1/1 green Saproling creature token."
                ),
                "colors": ["G"],
                "color_identity": ["G"],
            },
            {
                "oracle_id": "00000000-0000-4000-8000-000000000011",
                "name": "Whispering Shade",
                "mana_cost": "{3}{B}",
                "mana_value": 4.0,
                "type_line": "Creature — Shade",
                "oracle_text": (
                    "{B}: This creature gets +1/+1 until end of turn."
                ),
                "power": "1",
                "toughness": "1",
                "colors": ["B"],
                "color_identity": ["B"],
            },
        ]
        with contextlib.closing(sqlite3.connect(path)) as connection, connection:
            for card in cards:
                raw = {
                    "object": "card",
                    "id": card["oracle_id"],
                    **card,
                    "layout": "normal",
                    "released_at": "2026-01-01",
                    "legalities": {"commander": "legal"},
                }
                connection.execute(
                    """
                    INSERT INTO cards (
                        oracle_id, name, normalized_name, mana_cost,
                        mana_value, type_line, oracle_text, power,
                        toughness, loyalty, defense, colors_json,
                        color_identity_json, keywords_json,
                        produced_mana_json, layout, released_at,
                        legalities_json, faces_json, raw_json
                    ) VALUES (
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, ?, ?,
                        ?, ?, 'normal', '2026-01-01', ?, '[]', ?
                    )
                    """,
                    (
                        card["oracle_id"],
                        card["name"],
                        normalize_card_name(card["name"]),
                        card["mana_cost"],
                        card["mana_value"],
                        card["type_line"],
                        card["oracle_text"],
                        card.get("power"),
                        card.get("toughness"),
                        json.dumps(card.get("colors", [])),
                        json.dumps(card.get("color_identity", [])),
                        json.dumps(card.get("keywords", [])),
                        json.dumps(card.get("produced_mana", [])),
                        json.dumps({"commander": "legal"}),
                        json.dumps(raw),
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO aliases (
                        normalized_alias, alias, oracle_id, priority
                    ) VALUES (?, ?, ?, 100)
                    """,
                    (
                        normalize_card_name(card["name"]),
                        card["name"],
                        card["oracle_id"],
                    ),
                )

    def test_simple_spell_compiles_with_exact_source_span(self):
        record = self.db.lookup("Lightning Bolt")
        ir = compile_oracle_card(record)
        self.assertEqual("partial", ir.status)
        self.assertEqual(1, len(ir.material_residuals))
        node = ir.faces[0].nodes[0]
        self.assertEqual("damage-any-target-v1", node.template_id)
        self.assertTrue(node.lowerable)
        self.assertFalse(node.exact)
        self.assertEqual(record.oracle_text, record.oracle_text[
            node.span.start : node.span.end
        ])
        self.assertEqual(
            {
                "zones": ["player", "battlefield"],
                "categories": ["player", "permanent"],
                "predicate": "damageable",
                "count": 1,
            },
            node.target_schema,
        )

    def test_fixed_prevention_shields_compile_without_card_names(self):
        base = self.db.lookup("Lightning Bolt")
        for label, oracle_text, subject, amount in (
            (
                "any target",
                "Prevent the next 3 damage that would be dealt to any target this turn.",
                "$target.0",
                3,
            ),
            (
                "controller",
                "Prevent the next 2 damage that would be dealt to you this turn.",
                "$controller",
                2,
            ),
            (
                "self",
                (
                    "{T}: Prevent the next 1 damage that would be dealt to "
                    "this creature this turn."
                ),
                "$source",
                1,
            ),
        ):
            with self.subTest(label=label):
                record = replace(
                    base,
                    oracle_id=f"fixture-prevention-{label}",
                    name="Fixture Prevention",
                    oracle_text=oracle_text,
                    type_line=(
                        "Creature — Test" if label == "self" else "Instant"
                    ),
                )
                node = compile_oracle_card(record).faces[0].nodes[0]
                self.assertEqual(
                    "damage-prevention-fixed-shield-v1"
                    if label != "self"
                    else "damage-prevention-fixed-shield-self-v1",
                    node.template_id,
                )
                self.assertTrue(node.lowerable)
                self.assertEqual(
                    {
                        "op": "create_damage_prevention_shield",
                        "source": "$source",
                        "subject": subject,
                        "mode": "amount",
                        "amount": amount,
                        "duration": "until_end_of_turn",
                    },
                    node.effects[0],
                )

    def test_combat_scoped_prevention_compiles_in_spell_and_activated_contexts(self):
        base = self.db.lookup("Lightning Bolt")
        registry = load_default_capability_registry()
        cases = (
            (
                "damage-prevention-all-combat-v1",
                "Instant",
                "Prevent all combat damage that would be dealt this turn.",
                {"damage_kind": "combat", "subject": "*"},
            ),
            (
                "damage-prevention-all-combat-to-players-v1",
                "Instant",
                (
                    "Prevent all combat damage that would be dealt to players "
                    "this turn."
                ),
                {"damage_kind": "combat", "recipient_kind": "player"},
            ),
            (
                "damage-prevention-all-combat-by-target-v1",
                "Artifact",
                (
                    "{2}, {T}: Prevent all combat damage that would be dealt by "
                    "target creature this turn."
                ),
                {"damage_kind": "combat", "chosen_source": "$target.0"},
            ),
            (
                "damage-prevention-all-combat-to-from-self-v1",
                "Creature — Spirit",
                (
                    "{3}{W}: Prevent all combat damage that would be dealt to and "
                    "dealt by this creature this turn."
                ),
                {"damage_kind": "combat"},
            ),
            (
                "damage-prevention-fixed-combat-shield-v1",
                "Creature — Griffin",
                (
                    "{1}{W}: Prevent the next 1 combat damage that would be dealt "
                    "to target player or planeswalker this turn."
                ),
                {"damage_kind": "combat", "amount": 1},
            ),
            (
                "damage-prevention-all-shield-self-v1",
                "Creature — Antelope",
                (
                    "Discard a card: Prevent all damage that would be dealt to "
                    "this creature this turn."
                ),
                {"subject": "$source"},
            ),
            (
                "damage-prevention-chosen-source-next-instance-v1",
                "Enchantment",
                (
                    "{1}: The next time a blue source of your choice would deal "
                    "damage to you this turn, prevent that damage."
                ),
                {"op": "choose_damage_source"},
            ),
        )
        for index, (template, type_line, text, expected) in enumerate(cases):
            with self.subTest(template=template):
                ir = compile_oracle_card(
                    replace(
                        base,
                        oracle_id=f"fixture-combat-prevention-{index}",
                        name="Fixture Combat Prevention",
                        type_line=type_line,
                        oracle_text=text,
                    ),
                    capability_registry=registry,
                    capability_profile="commander_review",
                )
                node = ir.faces[0].nodes[0]
                self.assertEqual(template, node.template_id)
                self.assertEqual("exact", ir.status)
                self.assertIn(
                    "damage.prevention.persistent_amount",
                    node.capability_dependencies,
                )
                self.assertTrue(
                    any(
                        all(
                            effect.get(field) == value
                            for field, value in expected.items()
                        )
                        for effect in node.effects
                    )
                )

        two_target_clauses = compile_oracle_card(
            replace(
                base,
                oracle_id="fixture-combat-prevention-two-targets",
                name="Fixture Two-Target Combat Prevention",
                type_line="Instant",
                oracle_text=(
                    "Prevent all combat damage target creature would deal this "
                    "turn.\nPrevent all combat damage that would be dealt to "
                    "target creature this turn."
                ),
            ),
            capability_registry=registry,
            capability_profile="commander_review",
        )
        self.assertEqual("exact", two_target_clauses.status)
        self.assertEqual(
            [
                "damage-prevention-all-combat-by-target-v1",
                "damage-prevention-all-combat-to-target-v1",
            ],
            [node.template_id for node in two_target_clauses.faces[0].nodes],
        )

    def test_fixed_continuous_modifiers_compile_exactly_without_card_names(self):
        base = self.db.lookup("Lightning Bolt")
        registry = load_default_capability_registry()
        fixtures = (
            (
                "Fixture Anthem",
                "Enchantment",
                "Creatures you control get +1/+1.",
                "continuous-fixed-query-anthem-v2",
                "exact",
            ),
            (
                "Fixture Dragon Anthem",
                "Enchantment",
                "Dragon creatures you control get +3/+3.",
                "continuous-fixed-query-anthem-v2",
                "exact",
            ),
            (
                "Fixture Charge",
                "Instant",
                "Creatures you control get +1/+1 until end of turn.",
                "modify-controlled-creatures-fixed-stats-eot-v1",
                "exact",
            ),
            (
                "Fixture Target Pump",
                "Instant",
                "Target creature gets +2/+0 until end of turn.",
                "fixed-target-characteristics-until-end-of-turn-v1",
                "exact",
            ),
        )
        for name, type_line, text, template, status in fixtures:
            with self.subTest(name=name):
                record = replace(
                    base,
                    oracle_id=f"fixture-{normalize_card_name(name)}",
                    name=name,
                    type_line=type_line,
                    oracle_text=text,
                )
                ir = compile_oracle_card(
                    record, capability_registry=registry
                )
                self.assertEqual(template, ir.faces[0].nodes[0].template_id)
                self.assertEqual(status, ir.status)

        stateful = replace(
            base,
            oracle_id="fixture-attacking-anthem",
            name="Fixture Attacking Anthem",
            type_line="Enchantment",
            oracle_text="Attacking creatures you control get +1/+0.",
        )
        stateful_ir = compile_oracle_card(
            stateful, capability_registry=registry
        )
        self.assertNotEqual("exact", stateful_ir.status)
        self.assertNotEqual(
            "continuous-fixed-query-anthem-v2",
            stateful_ir.faces[0].nodes[0].template_id,
        )

    def test_newly_exact_nodes_deduplicate_against_reviewed_programs(self):
        record = self.db.lookup("Stridehangar Automaton")
        capabilities = load_default_capability_registry()
        ir = compile_oracle_card(
            record,
            capability_registry=capabilities,
            capability_profile="commander_review",
        )
        self.assertEqual("exact", ir.status)
        generated_node = ir.faces[0].nodes[0]
        self.assertTrue(generated_node.exact)
        self.assertEqual(
            "continuous-fixed-query-anthem-v2",
            generated_node.template_id,
        )

        programs = generated_programs(
            self.db,
            record,
            trust_level="trusted",
            capability_registry=capabilities,
            capability_profile="commander_review",
        )
        self.assertEqual(2, len(programs))
        self.assertTrue(
            all(program.trust_level == "trusted" for program in programs)
        )
        self.assertTrue(
            all(not program.requires_arbiter for program in programs)
        )

        reviewed_registry = SemanticRegistry()
        generation = register_generated_programs(
            self.db,
            reviewed_registry,
            (record,),
            capability_registry=capabilities,
            capability_profile="commander_review",
            promote_exact_runtime_handlers=True,
        )
        self.assertEqual(0, generation["programs_generated"])
        self.assertEqual(2, generation["programs_skipped_existing"])
        self.assertIsNone(
            reviewed_registry.get(
                f"{record.oracle_id}:static:front:n1"
            )
        )
        self.assertIsNone(
            reviewed_registry.get(
                f"{record.oracle_id}:static:front:n2"
            )
        )

    def test_exact_keyword_capability_promotes_beside_residual_ability(self):
        record = self.db.lookup("Faerie Mastermind")
        capabilities = load_default_capability_registry()

        programs = generated_programs(
            self.db,
            record,
            trust_level="trusted",
            capability_registry=capabilities,
            capability_profile="commander_review",
        )

        self.assertEqual(
            [
                f"{record.oracle_id}:static:front:n1:flash",
                f"{record.oracle_id}:static:front:n2",
                f"{record.oracle_id}:ability:ab4",
            ],
            [program.key for program in programs],
        )
        self.assertTrue(
            all(program.trust_level == "trusted" for program in programs)
        )
        self.assertEqual(
            ["timing.cast.printed_flash"],
            programs[0].capability_dependencies,
        )
        self.assertEqual(1, programs[0].provenance["source_span"]["line"])
        self.assertEqual(
            ["combat.block.flying"],
            programs[1].capability_dependencies,
        )
        self.assertEqual(2, programs[1].provenance["source_span"]["line"])
        self.assertEqual(
            ["zone.draw.library_to_hand"],
            programs[2].capability_dependencies,
        )
        self.assertEqual(4, programs[2].provenance["source_span"]["line"])

    def test_dynamic_and_aftermath_prevention_compile_generically(self):
        base = self.db.lookup("Lightning Bolt")
        for template_id, oracle_text, expected in (
            (
                "damage-prevention-fixed-shield-v1",
                "Prevent the next X damage that would be dealt to any target this turn.",
                {"amount": "$x"},
            ),
            (
                "damage-prevention-life-aftermath-v1",
                (
                    "Prevent the next 3 damage that would be dealt to any target "
                    "this turn. You gain life equal to the damage prevented this way."
                ),
                {"aftermath_kind": "gain_life", "per_prevented": 1},
            ),
            (
                "damage-prevention-counter-aftermath-v1",
                (
                    "Prevent the next X damage that would be dealt to target creature "
                    "this turn. For each 1 damage prevented this way, put a +1/+1 "
                    "counter on that creature."
                ),
                {"amount": "$x", "aftermath_kind": "place_counters"},
            ),
            (
                "damage-prevention-chosen-source-fixed-life-v2",
                (
                    "Prevent the next 3 damage that would be dealt to any target this "
                    "turn by a source of your choice. You gain 3 life."
                ),
                {"choice_op": "choose_damage_source"},
            ),
            (
                "damage-prevention-shared-color-creatures-v1",
                (
                    "Prevent the next 1 damage that would be dealt to target creature "
                    "and each other creature that shares a color with it this turn."
                ),
                {"selector": "shares_color_with"},
            ),
        ):
            with self.subTest(oracle_text=oracle_text):
                record = replace(
                    base,
                    oracle_id=f"fixture-{template_id}",
                    name="Fixture Prevention",
                    oracle_text=oracle_text,
                )
                ir = compile_oracle_card(record)
                node = ir.faces[0].nodes[0]
                self.assertEqual(template_id, node.template_id)
                self.assertTrue(node.lowerable)
                effect = node.effects[0]
                if "amount" in expected:
                    self.assertEqual(expected["amount"], effect["amount"])
                if "choice_op" in expected:
                    self.assertEqual(expected["choice_op"], effect["op"])
                    effect = effect["shield"]
                if "aftermath_kind" in expected:
                    self.assertEqual(
                        expected["aftermath_kind"],
                        effect["aftermath"][0]["kind"],
                    )
                if "per_prevented" in expected:
                    self.assertEqual(
                        expected["per_prevented"],
                        effect["aftermath"][0]["per_prevented"],
                    )
                if "selector" in expected:
                    self.assertEqual(
                        expected["selector"], effect["selector"]["kind"]
                    )

    def test_source_controller_damage_aftermath_compiles_generically(self):
        record = self.db.lookup("Deflecting Palm")

        ir = compile_oracle_card(
            record,
            capability_registry=load_default_capability_registry(),
            capability_profile="commander_review",
        )

        self.assertEqual("exact", ir.status)
        node = ir.faces[0].nodes[0]
        self.assertEqual(
            "damage-prevention-source-controller-aftermath-v1",
            node.template_id,
        )
        self.assertTrue(node.lowerable)
        choice = node.effects[0]
        self.assertEqual("choose_damage_source", choice["op"])
        self.assertEqual(
            {
                "kind": "deal_damage",
                "source": "$source",
                "recipient": None,
                "recipient_kind": "prevented_source_controller",
                "per_prevented": 1,
                "fixed_amount": 0,
            },
            choice["shield"]["aftermath"][0],
        )

    def test_prevention_triggered_damage_and_draw_compile_as_a_stack_trigger(self):
        base = self.db.lookup("Lightning Bolt")
        record = replace(
            base,
            oracle_id="fixture:new-way-forward",
            name="New Way Forward",
            oracle_text=(
                "The next time a source of your choice would deal damage to "
                "you this turn, prevent that damage. When damage is prevented "
                "this way, New Way Forward deals that much damage to that "
                "source's controller and you draw that many cards."
            ),
        )

        node = compile_oracle_card(record).faces[0].nodes[0]

        self.assertEqual(
            "damage-prevention-triggered-damage-draw-v1",
            node.template_id,
        )
        self.assertTrue(node.lowerable)
        trigger = node.effects[0]["shield"]["triggered_ability"]
        self.assertEqual({}, trigger["target_schema"])
        self.assertEqual(
            ["deal_damage", "draw_cards"],
            [result["kind"] for result in trigger["results"]],
        )
        self.assertNotIn("aftermath", node.effects[0]["shield"])

        wrong_source = compile_oracle_card(
            replace(
                record,
                oracle_id="fixture:new-way-forward-wrong-source",
                oracle_text=record.oracle_text.replace(
                    "New Way Forward deals",
                    "Another Card deals",
                ),
            )
        )
        self.assertNotEqual(
            "damage-prevention-triggered-damage-draw-v1",
            wrong_source.faces[0].nodes[0].template_id,
        )
        self.assertTrue(wrong_source.faces[0].residuals)

    def test_general_prevention_trigger_binds_affected_player_and_amount(self):
        base = self.db.lookup("Grizzly Bears")
        record = replace(
            base,
            oracle_id="fixture:selfless-squire-prevention",
            name="Fixture Selfless Squire",
            type_line="Creature — Human Soldier",
            oracle_text=(
                "When Fixture Selfless Squire enters, prevent all damage that would be "
                "dealt to you this turn.\n"
                "Whenever damage that would be dealt to you is prevented, put "
                "that many +1/+1 counters on Fixture Selfless Squire."
            ),
        )

        ir = compile_oracle_card(record)
        entry, prevention = ir.faces[0].nodes

        self.assertEqual("damage-prevention-all-shield-v1", entry.template_id)
        self.assertEqual("all", entry.effects[0]["mode"])
        self.assertEqual(
            "damage-prevented-self-counter-trigger-v1",
            prevention.template_id,
        )
        self.assertEqual("damage.prevented", prevention.event)
        self.assertEqual(
            {
                "field": "affected_players",
                "op": "contains_any",
                "value": ["$source.controller"],
            },
            prevention.event_condition,
        )
        self.assertEqual(
            "$context.prevented_amount",
            prevention.effects[0]["delta"],
        )
        programs = generated_programs(
            self.db,
            record,
            trusted_mechanics={
                "cr-603-handling-triggered-abilities",
                "cr-615-prevention-effects",
                "cr-122-counters",
            },
        )
        prevention_program = next(
            value for value in programs if value.event == "damage.prevented"
        )
        self.assertEqual(
            prevention.event_condition,
            prevention_program.event_condition,
        )

    def test_fixed_post_prevention_life_is_a_sequential_instruction(self):
        base = self.db.lookup("Lightning Bolt")
        oracle_text = (
            "Prevent the next 3 damage that would be dealt to any target "
            "this turn by a source of your choice. You gain 3 life."
        )

        ir = compile_oracle_card(
            replace(
                base,
                oracle_id="fixture:fixed-independent-prevention-life",
                name="Fixture Prevention Sequence",
                oracle_text=oracle_text,
            ),
            capability_registry=load_default_capability_registry(),
            capability_profile="commander_review",
        )
        node = ir.faces[0].nodes[0]

        self.assertEqual("exact", ir.status)
        self.assertEqual(
            "damage-prevention-chosen-source-fixed-life-v2",
            node.template_id,
        )
        self.assertEqual(2, len(node.effects))
        source_choice, life_gain = node.effects
        self.assertEqual("choose_damage_source", source_choice["op"])
        self.assertNotIn("aftermath", source_choice["shield"])
        self.assertEqual(
            {
                "op": "life",
                "player": "$controller",
                "delta": 3,
                "source": "$source",
                "cause": "spell_resolution",
            },
            life_gain,
        )

    def test_only_explicit_prevention_dependency_lowers_as_aftermath(self):
        base = self.db.lookup("Lightning Bolt")
        scaled = compile_oracle_card(
            replace(
                base,
                oracle_id="fixture:scaled-prevention-life",
                name="Fixture Scaled Prevention",
                oracle_text=(
                    "Prevent the next 3 damage that would be dealt to any target "
                    "this turn. You gain life equal to the damage prevented this way."
                ),
            )
        ).faces[0].nodes[0]
        independent_draw = compile_oracle_card(
            replace(
                base,
                oracle_id="fixture:independent-post-prevention-draw",
                name="Fixture Prevention Draw",
                oracle_text=(
                    "Prevent the next 3 damage that would be dealt to any target "
                    "this turn. Draw a card."
                ),
            )
        )
        conditional_draw = compile_oracle_card(
            replace(
                base,
                oracle_id="fixture:conditional-prevention-draw",
                name="Fixture Conditional Prevention Draw",
                oracle_text=(
                    "Prevent the next 3 damage that would be dealt to any target "
                    "this turn. If damage is prevented this way, draw a card."
                ),
            )
        )

        self.assertEqual(
            "gain_life", scaled.effects[0]["aftermath"][0]["kind"]
        )
        self.assertNotEqual("exact", independent_draw.status)
        self.assertNotEqual("exact", conditional_draw.status)
        for ir in (independent_draw, conditional_draw):
            for face in ir.faces:
                for node in face.nodes:
                    for effect in node.effects:
                        self.assertNotIn("aftermath", effect)

    def test_chosen_source_prevention_filters_compile_generically(self):
        base = self.db.lookup("Lightning Bolt")
        cases = (
            (
                "damage-prevention-chosen-source-next-instance-v1",
                (
                    "The next time a black or red source of your choice would "
                    "deal damage to you this turn, prevent that damage."
                ),
                "next_instance",
                "$controller",
                {"colors_any": ["B", "R"]},
            ),
            (
                "damage-prevention-chosen-source-next-instance-v1",
                (
                    "The next time an artifact source of your choice would "
                    "deal damage to you this turn, prevent that damage."
                ),
                "next_instance",
                "$controller",
                {"types_all": ["artifact"]},
            ),
            (
                "damage-prevention-chosen-source-all-v1",
                (
                    "Prevent all damage a red source of your choice would deal "
                    "this turn."
                ),
                "all",
                "*",
                {"colors_any": ["R"]},
            ),
            (
                "damage-prevention-chosen-source-all-v1",
                (
                    "Prevent all damage that would be dealt to you this turn by "
                    "a source of your choice."
                ),
                "all",
                "$controller",
                {},
            ),
            (
                "damage-prevention-chosen-source-fixed-v1",
                (
                    "Prevent the next 3 damage that would be dealt to any target "
                    "this turn by a legendary source of your choice."
                ),
                "amount",
                "$target.0",
                {"supertypes_all": ["legendary"]},
            ),
        )
        for template_id, text, mode, subject, filters in cases:
            with self.subTest(text=text):
                node = compile_oracle_card(
                    replace(
                        base,
                        oracle_id=f"fixture:{template_id}:{mode}:{subject}",
                        name="Fixture chosen-source prevention",
                        oracle_text=text,
                    )
                ).faces[0].nodes[0]
                self.assertEqual(template_id, node.template_id)
                choice = node.effects[0]
                self.assertEqual("choose_damage_source", choice["op"])
                for field, value in filters.items():
                    self.assertEqual(
                        value, choice["source_predicate"][field]
                    )
                self.assertEqual(mode, choice["shield"]["mode"])
                self.assertEqual(subject, choice["shield"]["subject"])

    def test_divided_and_open_combat_prevention_remain_unresolved(self):
        base = self.db.lookup("Lightning Bolt")
        for oracle_text in (
            (
                "Prevent the next 4 damage that would be dealt this turn to any "
                "number of targets, divided as you choose."
            ),
            (
                "Prevent all combat damage that would be dealt by creatures other "
                "than target creature this turn."
            ),
            "Prevent all combat damage that would be dealt next turn.",
        ):
            record = replace(
                base,
                oracle_id="fixture-unresolved-prevention",
                name="Fixture Prevention",
                oracle_text=oracle_text,
            )
            ir = compile_oracle_card(record)
            self.assertIsNone(ir.faces[0].nodes[0].template_id)
            self.assertFalse(ir.faces[0].nodes[0].lowerable)

    def test_static_redirection_requires_a_damageable_source_type(self):
        base = self.db.lookup("Grizzly Bears")
        text = (
            "All damage that would be dealt to you and other permanents "
            "you control is dealt to this permanent instead."
        )
        creature = compile_oracle_card(
            replace(
                base,
                oracle_id="fixture-damageable-redirection",
                name="Fixture Redirection Creature",
                oracle_text=text,
                type_line="Creature — Test",
            ),
            capability_registry=load_default_capability_registry(),
            capability_profile="commander_review",
        )
        self.assertEqual(
            "damage-redirection-static-to-source-v1",
            creature.faces[0].nodes[0].template_id,
        )
        self.assertEqual("exact", creature.status)

        artifact = compile_oracle_card(
            replace(
                base,
                oracle_id="fixture-nondamageable-redirection",
                name="Fixture Redirection Artifact",
                oracle_text=text,
                type_line="Artifact",
            ),
            capability_registry=load_default_capability_registry(),
            capability_profile="commander_review",
        )
        self.assertIsNone(artifact.faces[0].nodes[0].template_id)
        self.assertFalse(artifact.faces[0].nodes[0].lowerable)

    def test_vanilla_keyword_and_mana_cards_compile_without_name_branches(self):
        self.assertEqual("exact", compile_oracle_card(
            self.db.lookup("Grizzly Bears")
        ).status)
        flying = compile_oracle_card(self.db.lookup("Flying Men"))
        self.assertEqual("partial", flying.status)
        self.assertEqual(
            ("flying",), flying.faces[0].nodes[0].mechanics
        )
        elf = compile_oracle_card(self.db.lookup("Llanowar Elves"))
        self.assertEqual("partial", elf.status)
        self.assertEqual("mana_ability", elf.faces[0].nodes[0].kind)

    def test_damage_result_keywords_compile_as_generic_mechanics(self):
        base = self.db.lookup("Flying Men")
        for keyword, oracle_text in (
            ("infect", "Infect"),
            ("wither", "Wither"),
            ("lifelink", "Lifelink"),
            ("toxic", "Toxic 2"),
        ):
            with self.subTest(keyword=keyword):
                ir = compile_oracle_card(
                    replace(
                        base,
                        oracle_text=oracle_text,
                        keywords=(keyword.title(),),
                    )
                )
                self.assertEqual(
                    (keyword,), ir.faces[0].nodes[0].mechanics
                )
                self.assertEqual(
                    "dependency_contract", ir.material_residuals[0].kind
                )
                trusted = compile_oracle_card(
                    replace(
                        base,
                        oracle_text=oracle_text,
                        keywords=(keyword.title(),),
                    ),
                    trusted_mechanics={keyword},
                )
                self.assertFalse(trusted.material_residuals)

                capability_bound = compile_oracle_card(
                    replace(
                        base,
                        oracle_text=oracle_text,
                        keywords=(keyword.title(),),
                    ),
                    capability_registry=load_default_capability_registry(),
                    capability_profile="commander_review",
                )
                node = capability_bound.faces[0].nodes[0]
                self.assertEqual("exact", capability_bound.status)
                self.assertEqual(
                    (f"damage.result.{keyword}",),
                    node.capability_dependencies,
                )
                self.assertIn(
                    f"damage.result.{keyword}",
                    node.capability_closure,
                )
                self.assertEqual(
                    "commander_review", node.capability_profile
                )
                self.assertIsNotNone(node.capability_fingerprint)

    def test_dredge_keyword_lowers_to_a_graveyard_draw_replacement(self):
        record = self.db.lookup("Life from the Loam")
        ir = compile_oracle_card(
            record,
            capability_registry=load_default_capability_registry(),
            capability_profile="commander_review",
        )
        node = next(
            node
            for node in ir.faces[0].nodes
            if node.template_id == "dredge-keyword-replacement-v1"
        )

        self.assertEqual(("dredge",), node.mechanics)
        self.assertEqual("graveyard", node.active_zone)
        self.assertEqual("draw", node.event)
        self.assertEqual(
            ("zone.draw.library_to_hand",), node.capability_dependencies
        )
        self.assertEqual(
            {
                "handler_id": "replacement.draw.dredge.v1",
                "schema_version": 1,
                "event": "draw",
                "modification": {"mill_count": 3},
            },
            dict(node.handlers[0]),
        )
        programs = generated_programs(
            self.db,
            record,
            capability_registry=load_default_capability_registry(),
            capability_profile="commander_review",
        )
        program = next(
            value
            for value in programs
            if value.handlers
            and value.handlers[0]["handler_id"]
            == "replacement.draw.dredge.v1"
        )
        self.assertEqual("graveyard", program.active_zone)
        self.assertEqual("draw", program.event)

    def test_draw_restrictions_doubling_and_optional_draw_compile_generically(self):
        capabilities = load_default_capability_registry()
        expectations = {
            "Spirit of the Labyrinth": (
                "restriction.draw.maximum-per-turn.v1",
                "draw.permission",
                {"maximum_per_turn": 1},
            ),
            "Thought Reflection": (
                "replacement.draw.result.multiply.v1",
                "draw",
                {"factor": 2},
            ),
        }
        for name, expected in expectations.items():
            with self.subTest(name=name):
                ir = compile_oracle_card(
                    self.db.lookup(name),
                    capability_registry=capabilities,
                    capability_profile="commander_review",
                )
                self.assertEqual("exact", ir.status)
                node = next(value for value in ir.faces[0].nodes if value.handlers)
                handler_id, event, payload = expected
                self.assertEqual(event, node.event)
                self.assertEqual(handler_id, node.handlers[0]["handler_id"])
                field = (
                    "restriction"
                    if handler_id.startswith("restriction.")
                    else "modification"
                )
                self.assertEqual(payload, node.handlers[0][field])
                expected_capability = (
                    "zone.draw.result_generated_ordering"
                    if name == "Thought Reflection"
                    else "zone.draw.library_to_hand"
                )
                self.assertEqual(
                    (expected_capability,), node.capability_dependencies
                )

        for name in ("Oculus", "Surveilling Sprite", "Aven Fisher"):
            with self.subTest(optional_draw=name):
                ir = compile_oracle_card(
                    self.db.lookup(name),
                    capability_registry=capabilities,
                    capability_profile="commander_review",
                )
                self.assertEqual("exact", ir.status)
                trigger = next(
                    value
                    for value in ir.faces[0].nodes
                    if value.kind == "triggered_ability"
                )
                self.assertEqual("offer_draw", trigger.effects[0]["op"])

    def test_generic_draw_prohibition_replays_exactly(self):
        deck_a = DeckDefinition(
            name="A",
            commanders=["Zimone and Dina"],
            entries=[
                DeckEntry("Zimone and Dina", board="commander"),
                DeckEntry("Spirit of the Labyrinth"),
                DeckEntry("Island", quantity=10),
            ],
        )
        deck_b = DeckDefinition(
            name="B",
            commanders=["Mishra, Eminent One"],
            entries=[
                DeckEntry("Mishra, Eminent One", board="commander"),
                DeckEntry("Island", quantity=10),
            ],
        )
        session = CommanderSession.create(
            self.db,
            {"A": deck_a, "B": deck_b},
            first_player="A",
            seed=121_302,
            config=GameConfig(
                seed=121_302,
                auto_pass_empty_priority=False,
            ),
        )
        keep_all(session)
        engine = session.engine
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine.state.priority_player = None
        spirit = next(
            card
            for card in engine.state.cards.values()
            if card.printed_name == "Spirit of the Labyrinth"
        )
        engine.move_card(spirit.object_id, "battlefield", controller="A")
        generic = next(
            program
            for program in engine.semantics.programs_for_oracle(spirit.oracle_id)
            if program.handlers
        )
        self.assertEqual("trusted", generic.trust_level)
        self.assertEqual(
            "restriction.draw.maximum-per-turn.v1",
            generic.handlers[0]["handler_id"],
        )
        effect = SemanticProgram(
            key="test:draw-two-under-spirit",
            label="Draw two under Spirit",
            effects=[
                {
                    "op": "draw",
                    "player": "A",
                    "count": 2,
                    "private": True,
                    "reason": "generic prohibition replay",
                }
            ],
            trust_level="provisional",
        )
        engine.semantics.put(effect)
        engine.state.stack.append(
            StackItem(
                stack_id="draw-two-under-spirit",
                ref="S-draw-two-under-spirit",
                kind="triggered_ability",
                controller="A",
                label=effect.label,
                semantic_key=effect.key,
                visibility=["A", "B"],
            )
        )
        engine.state.active_player = "A"
        engine.state.started = True
        engine.state.phase = "precombat_main"
        engine.state.step = "main"
        engine._grant_priority("A")
        engine._issue_priority("A")
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()
        hand_before = len(engine.state.players["A"].zones["hand"])

        for principal in ("pilot:A", "pilot:B"):
            result = session.act(principal, {"action_id": "pass"})
            self.assertTrue(result.ok, result.summary)

        self.assertEqual(
            hand_before + 1,
            len(engine.state.players["A"].zones["hand"]),
        )
        self.assertEqual(
            1,
            sum(
                event.code == "card.draw.prohibited"
                for event in engine.state.events
            ),
        )
        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / "generic-draw-prohibition"
            session.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)

    def test_static_damage_replacement_wording_lowers_to_generic_handlers(self):
        capabilities = load_default_capability_registry()
        expectations = {
            "Furnace of Rath": (
                "replacement.damage.quantity.v1",
                "damage.replacement.static_quantity",
                {"multiplier": 2, "additional": 0},
            ),
            "Daunting Defender": (
                "prevention.damage.fixed.v1",
                "damage.prevention.static_fixed",
                {"amount": 1},
            ),
            "Angrath's Marauders": (
                "replacement.damage.quantity.v1",
                "damage.replacement.static_quantity",
                {"multiplier": 2, "additional": 0},
            ),
            "Urza's Armor": (
                "prevention.damage.fixed.v1",
                "damage.prevention.static_fixed",
                {"amount": 1},
            ),
        }
        for card_name, expected in expectations.items():
            with self.subTest(card_name=card_name):
                raw = compile_oracle_card(self.db.lookup(card_name))
                self.assertTrue(raw.material_residuals)
                ir = compile_oracle_card(
                    self.db.lookup(card_name),
                    capability_registry=capabilities,
                    capability_profile="commander_review",
                )
                self.assertEqual("exact", ir.status)
                node = ir.faces[0].nodes[0]
                handler_id, capability_id, modification = expected
                self.assertEqual((capability_id,), node.capability_dependencies)
                self.assertEqual(handler_id, node.handlers[0]["handler_id"])
                self.assertEqual(modification, node.handlers[0]["modification"])
                programs = generated_programs(
                    self.db,
                    self.db.lookup(card_name),
                    trust_level="trusted",
                    capability_registry=capabilities,
                    capability_profile="commander_review",
                )
                self.assertEqual(1, len(programs))
                self.assertEqual(
                    handler_id, programs[0].handlers[0]["handler_id"]
                )

        unsupported = compile_oracle_card(
            self.db.lookup("Gisela, Blade of Goldnight"),
            capability_registry=capabilities,
            capability_profile="commander_review",
        )
        self.assertNotEqual("exact", unsupported.status)
        self.assertTrue(unsupported.material_residuals)

    def test_fixed_additive_damage_replacements_lower_with_typed_scope(self):
        records = (
            CardRecord(
                oracle_id="00000000-0000-4000-8000-000000009901",
                name="Jaya, Venerated Firemage",
                mana_cost="{4}{R}",
                mana_value=5.0,
                type_line="Legendary Planeswalker — Jaya",
                oracle_text=(
                    "If another red source you control would deal damage to a "
                    "permanent or player, it deals that much damage plus 1 to "
                    "that permanent or player instead.\n"
                    "−2: Jaya deals 2 damage to any target."
                ),
                power=None,
                toughness=None,
                loyalty="5",
                defense=None,
                colors=("R",),
                color_identity=("R",),
                keywords=(),
                produced_mana=(),
                layout="normal",
                released_at="2019-05-03",
                legalities={"commander": "legal"},
                faces=(),
                raw={},
            ),
            CardRecord(
                oracle_id="00000000-0000-4000-8000-000000009902",
                name="Torbran, Thane of Red Fell",
                mana_cost="{1}{R}{R}{R}",
                mana_value=4.0,
                type_line="Legendary Creature — Dwarf Noble",
                oracle_text=(
                    "If a red source you control would deal damage to an "
                    "opponent or a permanent an opponent controls, it deals "
                    "that much damage plus 2 instead."
                ),
                power="2",
                toughness="4",
                loyalty=None,
                defense=None,
                colors=("R",),
                color_identity=("R",),
                keywords=(),
                produced_mana=(),
                layout="normal",
                released_at="2019-10-04",
                legalities={"commander": "legal"},
                faces=(),
                raw={},
            ),
        )
        registry = load_default_capability_registry()
        expected = ((1, True), (2, False))
        for record, (additional, exclude_source) in zip(records, expected):
            with self.subTest(card_name=record.name):
                ir = compile_oracle_card(
                    record,
                    capability_registry=registry,
                    capability_profile="commander_review",
                )
                self.assertEqual("exact", ir.status)
                node = ir.faces[0].nodes[0]
                self.assertEqual(
                    "replacement.damage.quantity.v2",
                    node.handlers[0]["handler_id"],
                )
                self.assertEqual(
                    {"multiplier": 1, "additional": additional},
                    node.handlers[0]["modification"],
                )
                self.assertEqual(
                    ["R"], node.handlers[0]["condition"]["source_colors_all"]
                )
                self.assertEqual(
                    exclude_source,
                    node.handlers[0]["condition"]["exclude_source_ref"],
                )
        level_gated = replace(
            records[1],
            oracle_id="00000000-0000-4000-8000-000000009903",
            name="Additive Damage Class Fixture",
            type_line="Enchantment — Class",
            oracle_text=(
                "If a red source you control would deal damage to an opponent, "
                "it deals that much damage plus 2 instead."
            ),
        )
        spell_scoped = replace(
            level_gated,
            oracle_id="00000000-0000-4000-8000-000000009904",
            name="Additive Damage Spell Fixture",
            type_line="Instant",
        )
        for unsupported in (level_gated, spell_scoped):
            with self.subTest(unsupported=unsupported.name):
                gated_ir = compile_oracle_card(
                    unsupported,
                    capability_registry=registry,
                    capability_profile="commander_review",
                )
                self.assertNotEqual("exact", gated_ir.status)
                self.assertFalse(
                    any(
                        handler.get("handler_id")
                        == "replacement.damage.quantity.v2"
                        for face in gated_ir.faces
                        for node in face.nodes
                        for handler in node.handlers
                    )
                )

    def test_static_life_gain_multiplier_lowers_generically(self):
        capabilities = load_default_capability_registry()
        ir = compile_oracle_card(
            self.db.lookup("Boon Reflection"),
            capability_registry=capabilities,
            capability_profile="commander_review",
        )
        node = ir.faces[0].nodes[0]

        self.assertEqual("exact", ir.status)
        self.assertEqual(
            "life-gain-double-controller-static-v1", node.template_id
        )
        self.assertEqual("life.change", node.event)
        self.assertEqual(
            "replacement.life.gain.multiplier.v1",
            node.handlers[0]["handler_id"],
        )
        self.assertEqual(
            ("life.gain.replacement.static_multiplier",),
            node.capability_dependencies,
        )

    def test_exact_generic_damage_handler_is_trusted_and_active_in_session(self):
        deck_a = DeckDefinition(
            name="A",
            commanders=["Zimone and Dina"],
            entries=[
                DeckEntry("Zimone and Dina", board="commander"),
                DeckEntry("Angrath's Marauders"),
            ],
        )
        deck_b = DeckDefinition(
            name="B",
            commanders=["Mishra, Eminent One"],
            entries=[
                DeckEntry("Mishra, Eminent One", board="commander"),
                DeckEntry("Island"),
            ],
        )
        session = CommanderSession.create(
            self.db,
            {"A": deck_a, "B": deck_b},
            first_player="A",
            seed=120_461_614,
        )
        engine = session.engine
        marauders = next(
            card
            for card in engine.state.cards.values()
            if card.printed_name == "Angrath's Marauders"
        )
        engine.move_card(marauders.object_id, "battlefield", controller="A")
        programs = engine.semantics.programs_for_oracle(marauders.oracle_id)
        generic = next(program for program in programs if program.handlers)
        self.assertEqual("trusted", generic.trust_level)
        self.assertEqual(
            "replacement.damage.quantity.v1",
            generic.handlers[0]["handler_id"],
        )

        life_before = engine.state.players["B"].life
        proposal = damage_proposal(
            engine,
            proposal_id="damage:generic-marauders",
            actor="A",
            source_ref=marauders.ref,
            target="B",
            amount=2,
            combat=False,
            reason="generic static replacement compiler witness",
        )
        result = commit_prepared_damage_batch(
            engine, prepare_damage_batch(engine, (proposal,))
        )
        self.assertEqual(4, result.events[0].dealt_amount)
        self.assertEqual(life_before - 4, engine.state.players["B"].life)

        registry = SemanticRegistry()
        furnace = self.db.lookup("Furnace of Rath")
        existing_handlers = [
            program
            for program in registry.programs_for_oracle(furnace.oracle_id)
            if program.handlers
        ]
        self.assertEqual(1, len(existing_handlers))
        generation = register_generated_programs(
            self.db,
            registry,
            (furnace,),
            trust_level="provisional",
            capability_registry=load_default_capability_registry(),
            capability_profile="commander_review",
            promote_exact_runtime_handlers=True,
        )
        self.assertEqual(0, generation["programs_generated"])
        self.assertEqual(1, generation["programs_skipped_existing"])
        self.assertEqual(0, generation["runtime_handlers_promoted"])
        self.assertEqual(
            1,
            len(
                [
                    program
                    for program in registry.programs_for_oracle(
                        furnace.oracle_id
                    )
                    if program.handlers
                ]
            ),
        )

    def test_trusted_dependency_set_promotes_only_the_matching_template(self):
        bolt = compile_oracle_card(
            self.db.lookup("Lightning Bolt"),
            trusted_mechanics={
                "cr-120-damage",
                "cr-115-targets",
            },
        )
        self.assertEqual("exact", bolt.status)
        self.assertEqual(0, len(bolt.material_residuals))

    def test_mixed_keyword_line_preserves_runtime_and_capability_coverage(self):
        record = self.db.lookup("Scryb Ranger")
        capabilities = load_default_capability_registry()
        ir = compile_oracle_card(
            record,
            capability_registry=capabilities,
            capability_profile="commander_review",
        )
        compound = next(
            node
            for node in ir.faces[0].nodes
            if "protection" in node.mechanics
        )

        self.assertEqual("exact", ir.status)
        self.assertEqual(0, len(ir.material_residuals))
        self.assertTrue(compound.exact)
        self.assertEqual(
            ("combat.block.flying", "protection.typed.debt"),
            compound.capability_dependencies,
        )
        self.assertEqual(
            ["ability.static.protection.v1"],
            [handler["handler_id"] for handler in compound.handlers],
        )

        registry = SemanticRegistry(include_builtin_packs=False)
        generation = register_generated_programs(
            self.db,
            registry,
            (record,),
            capability_registry=capabilities,
            capability_profile="commander_review",
            promote_exact_runtime_handlers=True,
        )
        programs = registry.runtime_handler_programs_for_oracle(
            record.oracle_id,
            active_zone="battlefield",
            event="continuous",
        )
        self.assertEqual(2, generation["runtime_handlers_promoted"])
        self.assertEqual(1, len(programs))
        self.assertEqual("trusted", programs[0].trust_level)
        self.assertIn("protection", programs[0].coverage)
        self.assertIn("flying", programs[0].coverage)
        flash_programs = registry.runtime_handler_programs_for_oracle(
            record.oracle_id,
            active_zone="playable",
            event="cast.permission",
        )
        self.assertEqual(1, len(flash_programs))
        self.assertEqual(
            ["ability.static.flash.v1"],
            [
                handler["handler_id"]
                for handler in flash_programs[0].handlers
            ],
        )

    def test_vigilance_keyword_uses_the_bounded_combat_capability(self):
        # Keep this grammar unit independent of the full Scryfall database.
        # Wight is present in the compact CI fixture; narrowing its text leaves
        # a single keyword declaration while retaining a normal CardRecord.
        record = replace(
            self.db.lookup("Wight of the Reliquary"),
            oracle_text="Vigilance",
            keywords=("Vigilance",),
        )
        capabilities = load_default_capability_registry()

        ir = compile_oracle_card(
            record,
            capability_registry=capabilities,
            capability_profile="commander_review",
        )

        self.assertEqual("exact", ir.status)
        self.assertEqual(0, len(ir.material_residuals))
        node = ir.faces[0].nodes[0]
        self.assertEqual(("vigilance",), node.mechanics)
        self.assertEqual(
            ("combat.attack.vigilance",),
            node.capability_dependencies,
        )
        programs = generated_programs(
            self.db,
            record,
            trust_level="trusted",
            capability_registry=capabilities,
            capability_profile="commander_review",
        )
        self.assertEqual(1, len(programs))
        self.assertEqual("trusted", programs[0].trust_level)
        self.assertEqual(
            ["combat.attack.vigilance"],
            programs[0].capability_dependencies,
        )

    def test_haste_keyword_uses_attack_and_activation_capabilities(self):
        record = replace(
            self.db.lookup("Wight of the Reliquary"),
            oracle_text="Haste",
            keywords=("Haste",),
        )
        capabilities = load_default_capability_registry()

        ir = compile_oracle_card(
            record,
            capability_registry=capabilities,
            capability_profile="commander_review",
        )

        self.assertEqual("exact", ir.status)
        self.assertEqual(0, len(ir.material_residuals))
        node = ir.faces[0].nodes[0]
        self.assertEqual(("haste",), node.mechanics)
        self.assertEqual(
            "Haste",
            record.oracle_text[node.span.start : node.span.end],
        )
        self.assertEqual(
            (
                "activation.tap_untap_cost.haste",
                "combat.attack.haste",
            ),
            node.capability_dependencies,
        )
        programs = generated_programs(
            self.db,
            record,
            trust_level="trusted",
            capability_registry=capabilities,
            capability_profile="commander_review",
        )
        self.assertEqual(1, len(programs))
        self.assertEqual("trusted", programs[0].trust_level)
        self.assertEqual(
            [
                "activation.tap_untap_cost.haste",
                "combat.attack.haste",
            ],
            programs[0].capability_dependencies,
        )

    def test_flying_reach_and_hexproof_use_source_spanned_capabilities(self):
        base = self.db.lookup("Wight of the Reliquary")
        capabilities = load_default_capability_registry()

        for keyword, capability_id in (
            ("Flying", "combat.block.flying"),
            ("Reach", "combat.block.reach"),
            ("Hexproof", "target.protection.hexproof_permanent"),
        ):
            with self.subTest(keyword=keyword):
                record = replace(
                    base,
                    oracle_id=f"fixture-{keyword.casefold()}",
                    name=f"Fixture {keyword}",
                    oracle_text=keyword,
                    keywords=(keyword,),
                )
                ir = compile_oracle_card(
                    record,
                    capability_registry=capabilities,
                    capability_profile="commander_review",
                )

                self.assertEqual("exact", ir.status)
                self.assertEqual(0, len(ir.material_residuals))
                node = ir.faces[0].nodes[0]
                self.assertEqual((keyword.casefold(),), node.mechanics)
                self.assertEqual(
                    (capability_id,), node.capability_dependencies
                )
                self.assertEqual(
                    keyword,
                    record.oracle_text[node.span.start : node.span.end],
                )
                programs = generated_programs(
                    self.db,
                    record,
                    trust_level="trusted",
                    capability_registry=capabilities,
                    capability_profile="commander_review",
                )
                self.assertEqual(1, len(programs))
                self.assertEqual("trusted", programs[0].trust_level)
                self.assertEqual(
                    [capability_id],
                    programs[0].capability_dependencies,
                )
                if keyword == "Flying":
                    self.assertIn(
                        "combat.block.reach",
                        programs[0].capability_closure["reachable"],
                    )

    def test_hexproof_compounds_and_unrepresented_variants_fail_closed(self):
        base = self.db.lookup("Wight of the Reliquary")
        capabilities = load_default_capability_registry()
        combined = replace(
            base,
            oracle_id="fixture-flying-hexproof",
            name="Fixture Flying Hexproof",
            oracle_text="Flying, hexproof",
            keywords=("Flying", "Hexproof"),
        )
        exact = compile_oracle_card(
            combined,
            capability_registry=capabilities,
            capability_profile="commander_review",
        )
        self.assertEqual("exact", exact.status)
        self.assertEqual(
            (
                "combat.block.flying",
                "target.protection.hexproof_permanent",
            ),
            exact.faces[0].nodes[0].capability_dependencies,
        )

        for oracle_id, oracle_text in (
            ("fixture-hexproof-from-black", "Hexproof from black"),
            ("fixture-hexproof-from-each-color", "Hexproof from each color"),
            ("fixture-player-hexproof", "You have hexproof."),
        ):
            with self.subTest(oracle_text=oracle_text):
                unsupported = compile_oracle_card(
                    replace(
                        base,
                        oracle_id=oracle_id,
                        name="Fixture Unsupported Hexproof",
                        oracle_text=oracle_text,
                        keywords=("Hexproof",),
                    ),
                    capability_registry=capabilities,
                    capability_profile="commander_review",
                )
                self.assertNotEqual("exact", unsupported.status)
                self.assertTrue(unsupported.material_residuals)

    def test_trample_uses_source_spanned_assignment_capability(self):
        base = self.db.lookup("Wight of the Reliquary")
        record = replace(
            base,
            oracle_id="fixture-trample",
            name="Fixture Trample",
            oracle_text="Trample",
            keywords=("Trample",),
        )
        capabilities = load_default_capability_registry()

        ir = compile_oracle_card(
            record,
            capability_registry=capabilities,
            capability_profile="commander_review",
        )

        self.assertEqual("exact", ir.status)
        self.assertEqual(0, len(ir.material_residuals))
        node = ir.faces[0].nodes[0]
        self.assertEqual(("trample",), node.mechanics)
        self.assertEqual(
            "Trample",
            record.oracle_text[node.span.start : node.span.end],
        )
        self.assertEqual(
            ("combat.damage.assignment.trample",),
            node.capability_dependencies,
        )
        programs = generated_programs(
            self.db,
            record,
            trust_level="trusted",
            capability_registry=capabilities,
            capability_profile="commander_review",
        )
        self.assertEqual(1, len(programs))
        self.assertEqual("trusted", programs[0].trust_level)
        self.assertEqual(
            ["combat.damage.assignment.trample"],
            programs[0].capability_dependencies,
        )

    def test_material_unknowns_fail_closed_with_specific_residuals(self):
        rest = compile_oracle_card(self.db.lookup("Rest in Peace"))
        self.assertEqual("partial", rest.status)
        kinds = {residual.kind for residual in rest.material_residuals}
        self.assertIn("replacement_effect", kinds)
        self.assertNotIn("trigger", kinds)
        self.assertTrue(
            all(residual.reason for residual in rest.material_residuals)
        )

    def test_generated_program_is_provisional_and_requires_arbiter(self):
        record = self.db.lookup("Lightning Bolt")
        programs = generated_programs(self.db, record)
        self.assertEqual(1, len(programs))
        program = programs[0]
        self.assertEqual(
            f"{record.oracle_id}:spell:front", program.key
        )
        self.assertEqual("provisional", program.trust_level)
        self.assertTrue(program.requires_arbiter)
        self.assertEqual(
            ORACLE_COMPILER_VERSION,
            program.provenance["authored_by"],
        )
        self.assertEqual(
            "pending_mechanic_contracts",
            program.provenance["dependency_trust"],
        )

    def test_simple_self_trigger_compiles_to_normalized_engine_event(self):
        record = self.db.lookup("Elvish Visionary")
        ir = compile_oracle_card(record)
        node = ir.faces[0].nodes[0]
        self.assertEqual("triggered_ability", node.kind)
        self.assertEqual("permanent.enter.self", node.event)
        self.assertEqual("draw-controller-v1", node.template_id)
        self.assertTrue(node.lowerable)
        self.assertFalse(node.exact)
        self.assertEqual(
            (
                "cr-603-handling-triggered-abilities",
                "trigger-event-normalized-zone-change",
                "cr-121-drawing-a-card",
            ),
            node.mechanics,
        )
        programs = generated_programs(self.db, record)
        self.assertEqual(1, len(programs))
        self.assertEqual("permanent.enter.self", programs[0].event)
        self.assertEqual(
            [{"op": "draw", "player": "$controller", "count": 1,
              "private": True}],
            programs[0].effects,
        )
        capability_ir = compile_oracle_card(
            record,
            capability_registry=load_default_capability_registry(),
            capability_profile="commander_review",
        )
        capability_node = capability_ir.faces[0].nodes[0]
        self.assertEqual("exact", capability_ir.status)
        self.assertEqual(
            {
                "trigger.event.normalized_zone_change",
                "trigger.placement.apnap",
                "zone.draw.library_to_hand",
            },
            set(capability_node.capability_dependencies),
        )
        self.assertEqual((), capability_ir.material_residuals)
        registry = SemanticRegistry(include_builtin_packs=False)
        generation = register_generated_programs(
            self.db,
            registry,
            (record,),
            capability_registry=load_default_capability_registry(),
            capability_profile="commander_review",
            promote_exact_trigger_programs=True,
        )
        self.assertEqual(1, generation["exact_programs_promoted"])
        self.assertTrue(
            all(program.trust_level == "trusted" for program in registry.programs())
        )

    def test_fixed_life_self_trigger_is_capability_closed_and_exact(self):
        record = self.db.lookup("Lone Missionary")
        capabilities = load_default_capability_registry()
        ir = compile_oracle_card(
            record,
            capability_registry=capabilities,
            capability_profile="commander_review",
        )
        node = ir.faces[0].nodes[0]

        self.assertEqual("exact", ir.status)
        self.assertEqual("permanent.enter.self", node.event)
        self.assertEqual("gain-life-controller-v1", node.template_id)
        self.assertEqual(
            {
                "life.change.effect",
                "trigger.event.normalized_zone_change",
                "trigger.placement.apnap",
            },
            set(node.capability_dependencies),
        )
        programs = generated_programs(
            self.db,
            record,
            trust_level="trusted",
            capability_registry=capabilities,
            capability_profile="commander_review",
        )
        self.assertEqual("trusted", programs[0].trust_level)
        self.assertFalse(programs[0].requires_arbiter)
        registry = SemanticRegistry(include_builtin_packs=False)
        generation = register_generated_programs(
            self.db,
            registry,
            (record,),
            capability_registry=capabilities,
            capability_profile="commander_review",
            promote_exact_trigger_programs=True,
        )
        self.assertEqual(1, generation["exact_programs_promoted"])
        self.assertEqual(0, generation["runtime_handlers_promoted"])
        self.assertEqual(
            ["trusted"],
            [program.trust_level for program in registry.programs()],
        )

    def test_trigger_with_uncompiled_condition_remains_residual(self):
        record = replace(
            self.db.lookup("Elvish Visionary"),
            oracle_text=(
                "When this creature enters, if you control an Elf, "
                "draw a card."
            ),
        )
        ir = compile_oracle_card(record)
        self.assertEqual("unresolved", ir.status)
        self.assertEqual("trigger", ir.material_residuals[0].kind)
        self.assertFalse(generated_programs(self.db, record))

    def test_self_pump_and_basic_token_creation_lower_generically(self):
        shade_program = generated_programs(
            self.db,
            self.db.lookup("Whispering Shade"),
        )[0]
        self.assertEqual(
            [
                {
                    "op": "modify_stats_until_end_of_turn",
                    "card": "$source",
                    "power": 1,
                    "toughness": 1,
                }
            ],
            shade_program.effects,
        )
        sprout_program = generated_programs(
            self.db,
            self.db.lookup("Sprout"),
        )[0]
        token = sprout_program.effects[0]
        self.assertEqual("create_token", token["op"])
        self.assertEqual("Saproling", token["name"])
        self.assertEqual(
            "Token Creature — Saproling",
            token["characteristics"]["type_line"],
        )
        self.assertEqual(["G"], token["characteristics"]["colors"])
        counter_record = replace(
            self.db.lookup("Whispering Shade"),
            oracle_text=(
                "{T}: Put a +1/+1 counter on target creature."
            ),
        )
        counter_program = generated_programs(
            self.db,
            counter_record,
        )[0]
        self.assertEqual(
            {
                "op": "place_counters",
                "card": "$target.0",
                "counter": "+1/+1",
                "amount": 1,
                "source": "$source",
            },
            counter_program.effects[0],
        )
        self.assertEqual(
            ["creature"],
            counter_program.target_schema["types_any"],
        )

    def test_new_templates_remain_whole_text_anchored(self):
        cases = [
            (
                self.db.lookup("Sprout"),
                (
                    "Create a 1/1 green Saproling creature token "
                    "and you gain 1 life."
                ),
            ),
            (
                self.db.lookup("Whispering Shade"),
                (
                    "{B}: This creature gets +1/+1 until end of turn. "
                    "Activate only once each turn."
                ),
            ),
            (
                self.db.lookup("Moss Diamond"),
                (
                    "This artifact enters tapped unless you control "
                    "a Forest.\n{T}: Add {G}."
                ),
            ),
        ]
        for base, oracle_text in cases:
            with self.subTest(base.name):
                ir = compile_oracle_card(
                    replace(base, oracle_text=oracle_text)
                )
                self.assertTrue(ir.material_residuals)
                self.assertNotEqual("exact", ir.status)

    def test_generated_trust_cannot_bypass_material_residuals(self):
        with self.assertRaisesRegex(
            ValueError,
            "material Oracle residuals remain",
        ):
            generated_programs(
                self.db,
                self.db.lookup("Lightning Bolt"),
                trust_level="trusted",
            )

    def test_reviewed_trigger_shadows_equivalent_generated_event(self):
        record = self.db.lookup("Elvish Visionary")
        registry = SemanticRegistry(include_builtin_packs=False)
        reviewed = SemanticProgram(
            key=f"{record.oracle_id}:reviewed:enter",
            label="Reviewed Elvish Visionary trigger",
            effects=[
                {
                    "op": "draw",
                    "player": "$controller",
                    "count": 1,
                }
            ],
            oracle_id=record.oracle_id,
            ability_id="reviewed:enter",
            active_zone="battlefield",
            event="permanent.enter.self",
            trust_level="trusted",
            provenance={
                "source_oracle_hash": "reviewed-oracle-hash",
                "source_rulings_hash": "reviewed-rulings-hash",
                "authored_by": "test",
                "review_status": "reviewed",
            },
            tests=["test_reviewed_trigger"],
        )
        registry.put(reviewed)
        result = register_generated_programs(
            self.db,
            registry,
            [record],
        )
        self.assertEqual(0, result["programs_generated"])
        self.assertEqual(1, result["programs_skipped_existing"])
        self.assertEqual(
            [reviewed.key],
            [program.key for program in registry.programs()],
        )

    def test_template_mutation_cannot_be_silently_discarded(self):
        record = self.db.lookup("Lightning Bolt")
        mutated = replace(
            record,
            oracle_text=(
                record.oracle_text
                + " Then that player discards a card."
            ),
        )
        ir = compile_oracle_card(
            mutated,
            trusted_mechanics={
                "cr-120-damage",
                "cr-115-targets",
            },
        )
        self.assertNotEqual("exact", ir.status)
        self.assertTrue(ir.material_residuals)
        self.assertFalse(generated_programs(self.db, mutated))

    def test_unrecognized_oracle_fuzz_always_leaves_material_residual(self):
        base = self.db.lookup("Lightning Bolt")
        randomizer = random.Random(701)
        words = [
            "choose",
            "exchange",
            "outside",
            "perpetually",
            "unless",
            "instead",
            "owner",
            "copy",
        ]
        for index in range(250):
            text = " ".join(
                randomizer.choice(words)
                for _ in range(randomizer.randint(3, 12))
            ) + f" {index}."
            ir = compile_oracle_card(
                replace(base, oracle_text=text)
            )
            self.assertTrue(ir.material_residuals, text)
            self.assertNotEqual("exact", ir.status)

    def test_semantic_hash_is_stable_and_source_sensitive(self):
        record = self.db.lookup("Divination")
        first = compile_oracle_card(record)
        second = compile_oracle_card(record)
        changed = compile_oracle_card(
            replace(record, oracle_text=record.oracle_text + " ")
        )
        self.assertEqual(first.semantic_hash, second.semantic_hash)
        self.assertNotEqual(first.semantic_hash, changed.semantic_hash)

    def test_session_registers_generated_program_for_new_deck_card(self):
        deck_a = DeckDefinition(
            name="A",
            commanders=["Zimone and Dina"],
            entries=[
                DeckEntry(
                    "Zimone and Dina",
                    board="commander",
                ),
                DeckEntry("Lightning Bolt"),
            ],
        )
        deck_b = DeckDefinition(
            name="B",
            commanders=["Mishra, Eminent One"],
            entries=[
                DeckEntry(
                    "Mishra, Eminent One",
                    board="commander",
                ),
                DeckEntry("Island"),
            ],
        )
        session = CommanderSession.create(
            self.db,
            {"A": deck_a, "B": deck_b},
            first_player="A",
            seed=9191,
        )
        bolt = self.db.lookup("Lightning Bolt")
        program = session.engine.semantics.get(
            f"{bolt.oracle_id}:spell:front"
        )
        self.assertIsNotNone(program)
        self.assertEqual("trusted", program.trust_level)
        self.assertFalse(program.requires_arbiter)

    def _trigger_session(self):
        deck_a = DeckDefinition(
            name="A",
            commanders=["Zimone and Dina"],
            entries=[
                DeckEntry("Zimone and Dina", board="commander"),
                DeckEntry("Elvish Visionary"),
                DeckEntry("Lone Missionary"),
                DeckEntry("Kingfisher"),
                DeckEntry("Moss Diamond"),
            ],
        )
        deck_b = DeckDefinition(
            name="B",
            commanders=["Mishra, Eminent One"],
            entries=[
                DeckEntry("Mishra, Eminent One", board="commander"),
                DeckEntry("Island"),
            ],
        )
        session = CommanderSession.create(
            self.db,
            {"A": deck_a, "B": deck_b},
            first_player="A",
            seed=9393,
        )
        engine = session.engine
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine.state.priority_player = None
        for player in engine.state.players.values():
            player.attempted_empty_draw = False
        return session

    def test_generated_enters_draw_trigger_resolves_without_arbiter(self):
        session = self._trigger_session()
        engine = session.engine
        visionary = next(
            card
            for card in engine.state.cards.values()
            if card.printed_name == "Elvish Visionary"
        )
        draw_card = next(
            card
            for card in engine.state.cards.values()
            if card.printed_name == "Moss Diamond"
        )
        engine.move_card(draw_card.object_id, "library", log=False)
        engine.move_card(
            visionary.object_id,
            "battlefield",
            controller="A",
            semantic_events=True,
        )
        self.assertTrue(engine.state.pending_trigger_batches)
        self.assertFalse(engine._stabilize())
        trigger = engine.state.stack[-1]
        self.assertEqual("A", trigger.controller)
        self.assertEqual(
            "permanent.enter",
            trigger.context["event"],
        )
        hand_before = len(engine.state.players["A"].zones["hand"])
        library_before = len(engine.state.players["A"].zones["library"])
        engine._prepare_stack_resolution()
        self.assertIsNone(engine.state.pending_decision)
        self.assertEqual(
            hand_before + 1,
            len(engine.state.players["A"].zones["hand"]),
        )
        self.assertEqual(
            library_before - 1,
            len(engine.state.players["A"].zones["library"]),
        )

    def test_fixed_life_self_enter_trigger_replays_exactly(self):
        session = self._trigger_session()
        engine = session.engine
        missionary = next(
            card
            for card in engine.state.cards.values()
            if card.printed_name == "Lone Missionary"
        )
        engine.move_card(missionary.object_id, "hand")
        engine.state.active_player = "A"
        engine.state.started = True
        engine.state.phase = "precombat_main"
        engine.state.step = "main"
        engine.state.priority_player = None
        engine.state.priority_passes = []
        engine.state.players["A"].mana_pool["W"] = 1
        engine.state.players["A"].mana_pool["C"] = 1
        engine._grant_priority("A")
        engine._issue_priority("A")
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()

        result = session.act(
            "pilot:A",
            {
                "action": "cast",
                "card": missionary.ref,
                "from": "hand",
                "auto_pay": True,
            },
        )
        self.assertTrue(result.ok, result.summary)
        for _ in range(12):
            if engine.state.players["A"].life == 44:
                break
            session.next_task()
            principals = session.pending_principals()
            if not principals:
                continue
            for principal in principals:
                passed = session.act(principal, {"a": "pass"})
                self.assertTrue(passed.ok, passed.summary)
        self.assertEqual(44, engine.state.players["A"].life)
        self.assertFalse(
            engine.state.pending_decision
            and engine.state.pending_decision.kind == "arbiter.resolve"
        )

        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / "fixed-life-trigger-replay"
            session.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)

    def test_self_dies_trigger_uses_last_known_controller(self):
        session = self._trigger_session()
        engine = session.engine
        kingfisher = next(
            card
            for card in engine.state.cards.values()
            if card.printed_name == "Kingfisher"
        )
        engine.move_card(
            kingfisher.object_id,
            "battlefield",
            controller="B",
        )
        engine.move_card(
            kingfisher.object_id,
            "graveyard",
            semantic_events=True,
        )
        self.assertTrue(engine.state.pending_trigger_batches)
        self.assertFalse(engine._stabilize())
        trigger = engine.state.stack[-1]
        self.assertEqual("B", trigger.controller)
        self.assertEqual("creature.dies", trigger.context["event"])
        self.assertEqual("A", kingfisher.controller)

    def test_unconditional_entry_tapped_is_engine_derived(self):
        session = self._trigger_session()
        engine = session.engine
        diamond = next(
            card
            for card in engine.state.cards.values()
            if card.printed_name == "Moss Diamond"
        )
        ir = compile_oracle_card(self.db.lookup("Moss Diamond"))
        entry = next(
            node
            for node in ir.faces[0].nodes
            if node.template_id == "zone-entry-state-self-tapped-v1"
        )
        self.assertTrue(entry.lowerable)
        engine.move_card(
            diamond.object_id,
            "battlefield",
            controller="A",
        )
        self.assertTrue(diamond.tapped)
        engine.move_card(diamond.object_id, "hand")
        engine.move_card(
            diamond.object_id,
            "battlefield",
            controller="A",
            tapped=False,
        )
        self.assertTrue(diamond.tapped)

    def _generated_spell_session(self, *, trusted_only=False):
        deck_a = DeckDefinition(
            name="A",
            commanders=["Zimone and Dina"],
            entries=[
                DeckEntry("Zimone and Dina", board="commander"),
                DeckEntry("Lightning Bolt"),
            ],
        )
        deck_b = DeckDefinition(
            name="B",
            commanders=["Mishra, Eminent One"],
            entries=[
                DeckEntry("Mishra, Eminent One", board="commander"),
                DeckEntry("Island"),
            ],
        )
        session = CommanderSession.create(
            self.db,
            {"A": deck_a, "B": deck_b},
            first_player="A",
            seed=9292,
            config=GameConfig(
                seed=9292,
                profile="commander_duel",
                semantic_policy=(
                    "trusted_only"
                    if trusted_only
                    else "arbitrate_or_pause"
                ),
            ),
        )
        engine = session.engine
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        # This deliberately tiny compiler fixture exhausts both libraries
        # during setup. Clear that unrelated loss marker before exercising
        # post-cast semantic arbitration.
        for player in engine.state.players.values():
            player.attempted_empty_draw = False
        bolt = next(
            card
            for card in engine.state.cards.values()
            if card.printed_name == "Lightning Bolt"
        )
        engine.move_card(bolt.object_id, "hand")
        engine.state.active_player = "A"
        engine.state.phase = "precombat_main"
        engine.state.step = "main"
        engine.state.priority_player = "A"
        engine.state.players["A"].mana_pool["R"] = 1
        return session, bolt

    def test_exact_generated_spell_resolves_without_arbiter(self):
        session, bolt = self._generated_spell_session()
        engine = session.engine
        engine._cast(
            "A",
            {
                "card": bolt.ref,
                "targets": ["B"],
                "auto_pay": True,
            },
        )
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine.state.priority_player = None
        engine._prepare_stack_resolution()
        self.assertEqual(37, engine.state.players["B"].life)
        self.assertFalse(engine.state.stack)
        self.assertFalse(
            engine.state.pending_decision
            and engine.state.pending_decision.kind == "arbiter.resolve"
        )

    def test_exact_generated_spell_is_available_under_trusted_only(self):
        session, bolt = self._generated_spell_session(
            trusted_only=True
        )
        hints = session.engine._priority_action_hints("A")
        self.assertTrue(
            any(
                action.get("card") == bolt.ref
                for action in hints["actions"]
            )
        )
        self.assertFalse(
            any(
                row.get("card") == bolt.ref
                and row.get("reason")
                == "semantic_policy_requires_trusted"
                for row in hints["diagnostic"][
                    "unresolved_cost_semantics"
                ]
            )
        )

    def test_limited_corpus_coverage_is_measured_not_claimed(self):
        coverage = oracle_corpus_coverage(self.db, limit=25)
        self.assertEqual(25, coverage["total_oracle_ids"])
        self.assertTrue(coverage["limited"])
        self.assertFalse(coverage["current_snapshot_complete"])
        self.assertEqual(
            25, sum(coverage["status_counts"].values())
        )

        capabilities = load_default_capability_registry()
        capability_bound = oracle_corpus_coverage(
            self.db,
            limit=25,
            capability_registry=capabilities,
            capability_profile="commander_review",
        )
        self.assertEqual(
            "commander_review", capability_bound["capability_profile"]
        )
        self.assertEqual(
            capabilities.fingerprint,
            capability_bound["capability_registry_fingerprint"],
        )
        self.assertEqual(
            capabilities.evidence_fingerprint,
            capability_bound["capability_evidence_fingerprint"],
        )

    def test_oracle_cli_parse_explain_and_coverage(self):
        for args in (
            [
                "oracle",
                "parse",
                "Lightning Bolt",
                "--db",
                str(self.db_path),
            ],
            [
                "oracle",
                "explain",
                "Rest in Peace",
                "--db",
                str(self.db_path),
            ],
            [
                "oracle",
                "coverage",
                "--db",
                str(self.db_path),
                "--limit",
                "5",
                "--profile",
                "commander_review",
            ],
        ):
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(0, main(args))
            self.assertIsInstance(json.loads(output.getvalue()), dict)


if __name__ == "__main__":
    unittest.main()
