from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import random
import tempfile
import unittest
from unittest.mock import patch

from common import change_permanent_counter, keep_all, load_assets, make_session
from quorune.aura import (
    SimpleEnchantSpec,
    simple_enchant_spec_from_oracle,
)
from quorune.ability_fragments import (
    CounterMaximumSpec,
    ProtectionQualityKind,
    ProtectionSpec,
    ability_fragment_to_dict,
)
from quorune.carddb import CardRecord
from quorune.damage import (
    DamageError,
    apply_damage_results_to_permanent,
)
from quorune.engine import GameRuleError
from quorune.model import DecisionGroup, StackItem
from quorune.projection import StateProjector
from quorune.record import (
    authoritative_state_hash,
    checkpoint_envelope,
    replay_record,
)
from quorune.replacement_effects import ReplacementChoiceRequired
from quorune.semantics import SemanticProgram
from quorune.state_based_actions import (
    ObjectSnapshot,
    PermanentSnapshot,
    evaluate_state_based_actions,
    evaluate_permanent_state_based_actions,
)
from quorune.compiler.counter_maximum_templates import (
    parse_fixed_self_counter_maximum,
)
from quorune.saga_lifecycle import (
    SagaFinalChapterSnapshot,
    SagaLifecycleError,
)


class StateBasedActionPrimitiveTests(unittest.TestCase):
    def test_contract_is_pinned_to_the_current_rules_snapshot(self):
        root = Path(__file__).resolve().parents[1]
        manifest = json.loads(
            (root / "rules" / "manifest.json").read_text(
                encoding="utf-8"
            )
        )
        contract = json.loads(
            (
                root
                / "mechanics"
                / "contracts"
                / "state-based-actions.json"
            ).read_text(encoding="utf-8")
        )
        registry = json.loads(
            (root / "mechanics" / "registry.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(
            manifest["source_sha256"], contract["source_sha256"]
        )
        self.assertEqual(
            manifest["effective_date"], contract["effective_date"]
        )
        self.assertIn("704.5q", contract["rule_references"])
        self.assertIn("704.5r", contract["rule_references"])
        self.assertIn("704.5v", contract["rule_references"])
        self.assertIn("704.5x", contract["rule_references"])
        self.assertIn("704.5y", contract["rule_references"])
        row = next(
            item
            for item in registry["mechanics"]
            if item["mechanic_id"]
            == "cr-704-state-based-actions"
        )
        self.assertEqual("partial", row["coverage_status"])
        self.assertEqual(
            "mechanics/contracts/state-based-actions.json",
            row["contract_path"],
        )
        for mechanic_id, filename, rule_id in (
            ("cr-120-damage", "damage.json", "120.3h"),
            ("cr-210-defense", "defense.json", "210.1"),
            ("cr-310-battles", "battles.json", "310.12b"),
        ):
            related = json.loads(
                (
                    root / "mechanics" / "contracts" / filename
                ).read_text(encoding="utf-8")
            )
            self.assertEqual(
                manifest["source_sha256"],
                related["source_sha256"],
            )
            self.assertEqual(
                manifest["effective_date"],
                related["effective_date"],
            )
            self.assertIn(rule_id, related["rule_references"])
            registry_row = next(
                item
                for item in registry["mechanics"]
                if item["mechanic_id"] == mechanic_id
            )
            self.assertEqual("partial", registry_row["coverage_status"])
            self.assertEqual(
                f"mechanics/contracts/{filename}",
                registry_row["contract_path"],
            )

    def test_battle_contract_traces_every_cr_310_rule(self):
        root = Path(__file__).resolve().parents[1]
        contract = json.loads(
            (
                root / "mechanics" / "contracts" / "battles.json"
            ).read_text(encoding="utf-8")
        )
        expected = {
            "310",
            "310.1",
            "310.2",
            "310.3",
            "310.4",
            "310.4a",
            "310.4b",
            "310.4c",
            "310.5",
            "310.6",
            "310.7",
            "310.8",
            "310.9",
            "310.9a",
            "310.9b",
            "310.9c",
            "310.9d",
            "310.9e",
            "310.9f",
            "310.9g",
            "310.10",
            "310.11",
            "310.12",
            "310.12a",
            "310.12b",
        }

        self.assertEqual(
            expected,
            {
                rule_id
                for rule_id in contract["rule_references"]
                if str(rule_id).startswith("310")
            },
        )

    def test_defense_contract_traces_every_cr_210_rule(self):
        root = Path(__file__).resolve().parents[1]
        contract = json.loads(
            (
                root / "mechanics" / "contracts" / "defense.json"
            ).read_text(encoding="utf-8")
        )

        self.assertEqual(
            {"210", "210.1"},
            {
                rule_id
                for rule_id in contract["rule_references"]
                if str(rule_id).startswith("210")
            },
        )

    def test_damage_contract_traces_every_cr_120_rule(self):
        root = Path(__file__).resolve().parents[1]
        contract = json.loads(
            (
                root / "mechanics" / "contracts" / "damage.json"
            ).read_text(encoding="utf-8")
        )
        expected = {
            "120",
            "120.1",
            "120.1a",
            "120.2",
            "120.2a",
            "120.2b",
            "120.3",
            "120.3a",
            "120.3b",
            "120.3c",
            "120.3d",
            "120.3e",
            "120.3f",
            "120.3g",
            "120.3h",
            "120.4",
            "120.4a",
            "120.4b",
            "120.4c",
            "120.4d",
            "120.5",
            "120.6",
            "120.7",
            "120.8",
            "120.9",
            "120.10",
        }

        self.assertEqual(
            expected,
            {
                rule_id
                for rule_id in contract["rule_references"]
                if str(rule_id).startswith("120")
            },
        )

    def test_snapshot_distinguishes_put_into_graveyard_from_destroy(self):
        batch = evaluate_permanent_state_based_actions(
            [
                PermanentSnapshot(
                    "zero",
                    card_types=frozenset({"creature"}),
                    toughness=0,
                    indestructible=True,
                ),
                PermanentSnapshot(
                    "lethal",
                    card_types=frozenset({"creature"}),
                    toughness=3,
                    marked_damage=3,
                ),
                PermanentSnapshot(
                    "deathtouch",
                    card_types=frozenset({"creature"}),
                    toughness=10,
                    deathtouch_damage=True,
                ),
                PermanentSnapshot(
                    "indestructible",
                    card_types=frozenset({"creature"}),
                    toughness=2,
                    marked_damage=99,
                    indestructible=True,
                ),
                PermanentSnapshot(
                    "walker",
                    card_types=frozenset({"planeswalker"}),
                    loyalty=0,
                ),
            ]
        )
        self.assertEqual(("walker", "zero"), batch.put_in_graveyard)
        self.assertEqual(
            ("deathtouch", "indestructible", "lethal"),
            batch.destroy,
        )

    def test_zero_defense_battle_waits_for_its_pending_trigger(self):
        batch = evaluate_permanent_state_based_actions(
            [
                PermanentSnapshot(
                    "defeated",
                    card_types=frozenset({"battle"}),
                    defense=0,
                ),
                PermanentSnapshot(
                    "trigger-pending",
                    card_types=frozenset({"battle"}),
                    defense=0,
                    battle_trigger_pending=True,
                ),
                PermanentSnapshot(
                    "defended",
                    card_types=frozenset({"battle"}),
                    defense=1,
                ),
            ]
        )

        self.assertEqual(("defeated",), batch.put_in_graveyard)

    def test_completed_saga_waits_only_for_its_pending_chapter(self):
        completed = SagaFinalChapterSnapshot(
            object_id="completed",
            logical_object_id="completed:1",
            controller="A",
            lore_counters=4,
            chapter_numbers=(1, 2, 3),
            chapter_trigger_pending=False,
        )
        pending = SagaFinalChapterSnapshot(
            object_id="pending",
            logical_object_id="pending:1",
            controller="B",
            lore_counters=3,
            chapter_numbers=(1, 3),
            chapter_trigger_pending=True,
        )
        below_final = SagaFinalChapterSnapshot(
            object_id="below",
            logical_object_id="below:1",
            controller="C",
            lore_counters=2,
            chapter_numbers=(1, 2, 3),
            chapter_trigger_pending=False,
        )

        batch = evaluate_permanent_state_based_actions(
            [
                PermanentSnapshot(
                    value.object_id,
                    card_types=frozenset({"enchantment"}),
                    subtypes=frozenset({"saga"}),
                    saga=value,
                    indestructible=value is completed,
                )
                for value in (completed, pending, below_final)
            ]
        )

        self.assertEqual((completed,), batch.saga_sacrifices)
        self.assertTrue(batch.changed)

    def test_completed_saga_with_another_zone_action_fails_closed(self):
        saga = SagaFinalChapterSnapshot(
            object_id="creature-saga",
            logical_object_id="creature-saga:1",
            controller="A",
            lore_counters=3,
            chapter_numbers=(1, 2, 3),
            chapter_trigger_pending=False,
        )
        with self.assertRaisesRegex(
            ValueError, "combined-cause handling"
        ):
            evaluate_permanent_state_based_actions(
                [
                    PermanentSnapshot(
                        "creature-saga",
                        card_types=frozenset(
                            {"creature", "enchantment"}
                        ),
                        subtypes=frozenset({"saga"}),
                        toughness=0,
                        saga=saga,
                    )
                ]
            )

    def test_phased_completed_saga_is_ignored_by_the_current_check(self):
        saga = SagaFinalChapterSnapshot(
            object_id="phased",
            logical_object_id="phased:1",
            controller="A",
            lore_counters=3,
            chapter_numbers=(1, 2, 3),
            chapter_trigger_pending=False,
        )
        batch = evaluate_permanent_state_based_actions(
            [
                PermanentSnapshot(
                    "phased",
                    card_types=frozenset({"enchantment"}),
                    subtypes=frozenset({"saga"}),
                    saga=saga,
                    phased_out=True,
                )
            ]
        )
        self.assertEqual((), batch.saga_sacrifices)

    def test_malformed_saga_lifecycle_fails_closed(self):
        for chapters in ((), (0,), (2, 1), (1, 1)):
            with self.subTest(chapters=chapters):
                with self.assertRaises(SagaLifecycleError):
                    SagaFinalChapterSnapshot(
                        object_id="saga",
                        logical_object_id="saga:1",
                        controller="A",
                        lore_counters=3,
                        chapter_numbers=chapters,
                        chapter_trigger_pending=False,
                    )
        saga = SagaFinalChapterSnapshot(
            object_id="saga",
            logical_object_id="saga:1",
            controller="A",
            lore_counters=3,
            chapter_numbers=(1, 2, 3),
            chapter_trigger_pending=False,
        )
        with self.assertRaisesRegex(ValueError, "match a Saga"):
            evaluate_permanent_state_based_actions(
                [
                    PermanentSnapshot(
                        "other",
                        card_types=frozenset({"enchantment"}),
                        subtypes=frozenset({"saga"}),
                        saga=saga,
                    )
                ]
            )

    def test_attachment_and_counter_actions_are_snapshot_based(self):
        batch = evaluate_permanent_state_based_actions(
            [
                PermanentSnapshot(
                    "aura",
                    card_types=frozenset({"enchantment"}),
                    subtypes=frozenset({"aura"}),
                    attached_to="land",
                    attachment_legal=False,
                ),
                PermanentSnapshot(
                    "equipment",
                    card_types=frozenset({"artifact"}),
                    subtypes=frozenset({"equipment"}),
                    attached_to="land",
                    attachment_legal=False,
                ),
                PermanentSnapshot(
                    "creature-equipment",
                    card_types=frozenset({"artifact", "creature"}),
                    subtypes=frozenset({"equipment"}),
                    toughness=2,
                    attached_to="creature",
                    attachment_legal=True,
                ),
                PermanentSnapshot(
                    "creature-aura",
                    card_types=frozenset({"creature", "enchantment"}),
                    subtypes=frozenset({"aura"}),
                    toughness=2,
                    attached_to="creature",
                    attachment_legal=True,
                ),
                PermanentSnapshot(
                    "counters",
                    card_types=frozenset({"creature"}),
                    toughness=4,
                    counters={"+1/+1": 3, "-1/-1": 2},
                ),
            ]
        )
        self.assertEqual(
            ("aura", "creature-aura"), batch.put_in_graveyard
        )
        self.assertEqual(
            ("creature-equipment", "equipment"), batch.detach
        )
        self.assertEqual(
            (("counters", 2),), batch.counter_pairs_to_remove
        )

    def test_counter_maximum_sentence_and_snapshot_action(self):
        self.assertEqual(
            CounterMaximumSpec("dream", 7),
            parse_fixed_self_counter_maximum(
                "Rasputin can't have more than seven dream counters on it.",
                source_name="Rasputin Dreamweaver",
            ),
        )
        self.assertEqual(
            CounterMaximumSpec("+1/+1", 2),
            parse_fixed_self_counter_maximum(
                "This creature can’t have more than 2 +1/+1 counters on it.",
                source_name="Counter Fixture",
            ),
        )
        self.assertIsNone(
            parse_fixed_self_counter_maximum(
                "Remove up to seven dream counters from it.",
                source_name="Counter Fixture",
            )
        )

        batch = evaluate_permanent_state_based_actions(
            [
                PermanentSnapshot(
                    "rasputin",
                    counters={"dream": 9},
                    counter_maximums={"dream": 7},
                ),
                PermanentSnapshot(
                    "at-limit",
                    counters={"dream": 7},
                    counter_maximums={"dream": 7},
                ),
            ]
        )
        self.assertEqual(
            (("rasputin", "dream", 2),),
            batch.counter_maximums_to_remove,
        )

    def test_input_mutation_cannot_change_batch(self):
        values = [
            PermanentSnapshot(
                f"counter-{index}",
                card_types=frozenset({"creature"}),
                toughness=2,
                counters={
                    "+1/+1": index + 1,
                    "-1/-1": 1,
                    "charge": index,
                },
                counter_maximums={"charge": 3},
            )
            for index in range(20)
        ]
        expected = evaluate_permanent_state_based_actions(values)
        randomizer = random.Random(704)
        for _ in range(50):
            randomizer.shuffle(values)
            self.assertEqual(
                expected,
                evaluate_permanent_state_based_actions(values),
            )

    def test_nonbattlefield_tokens_cease_from_the_shared_snapshot(self):
        batch = evaluate_state_based_actions(
            permanents=[],
            objects=[
                ObjectSnapshot(
                    "grave-token",
                    zone="graveyard",
                    is_token=True,
                ),
                ObjectSnapshot(
                    "battlefield-token",
                    zone="battlefield",
                    is_token=True,
                ),
                ObjectSnapshot(
                    "ordinary-card",
                    zone="graveyard",
                ),
            ],
        )
        self.assertEqual(("grave-token",), batch.cease)

    def test_noncard_copies_cease_only_outside_their_valid_zones(self):
        batch = evaluate_state_based_actions(
            permanents=[],
            objects=[
                ObjectSnapshot(
                    "resolved-spell-copy",
                    zone="graveyard",
                    is_spell_copy=True,
                ),
                ObjectSnapshot(
                    "stack-spell-copy",
                    zone="stack",
                    is_spell_copy=True,
                ),
                ObjectSnapshot(
                    "exiled-card-copy",
                    zone="exile",
                    is_card_copy=True,
                ),
                ObjectSnapshot(
                    "stack-card-copy",
                    zone="stack",
                    is_card_copy=True,
                ),
                ObjectSnapshot(
                    "permanent-card-copy",
                    zone="battlefield",
                    is_card_copy=True,
                ),
            ],
        )
        self.assertEqual(
            ("exiled-card-copy", "resolved-spell-copy"),
            batch.cease,
        )

    def test_world_rule_keeps_only_the_unique_newest_world(self):
        batch = evaluate_permanent_state_based_actions(
            [
                PermanentSnapshot(
                    "old-world",
                    world=True,
                    world_timestamp=10,
                ),
                PermanentSnapshot(
                    "new-world",
                    world=True,
                    world_timestamp=20,
                ),
                PermanentSnapshot("ordinary"),
            ]
        )

        self.assertEqual(("old-world",), batch.world_rule)

    def test_tied_newest_world_permanents_all_leave_order_independently(
        self,
    ):
        values = [
            PermanentSnapshot(
                "world-b",
                world=True,
                world_timestamp=20,
            ),
            PermanentSnapshot(
                "world-a",
                world=True,
                world_timestamp=20,
            ),
            PermanentSnapshot(
                "older-world",
                world=True,
                world_timestamp=10,
            ),
        ]
        expected = ("older-world", "world-a", "world-b")

        self.assertEqual(
            expected,
            evaluate_permanent_state_based_actions(values).world_rule,
        )
        values.reverse()
        self.assertEqual(
            expected,
            evaluate_permanent_state_based_actions(values).world_rule,
        )

    def test_world_rule_requires_a_since_timestamp(self):
        with self.assertRaisesRegex(
            ValueError,
            "World permanent requires",
        ):
            evaluate_permanent_state_based_actions(
                [
                    PermanentSnapshot(
                        "missing-world-time",
                        world=True,
                    ),
                    PermanentSnapshot(
                        "other-world",
                        world=True,
                        world_timestamp=1,
                    ),
                ]
            )


class StateBasedActionEngineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db, cls.mishra, cls.zimone = load_assets()

    @classmethod
    def tearDownClass(cls):
        cls.db.close()

    def make_engine(self, seed: int):
        session = make_session(
            self.db,
            self.mishra,
            self.zimone,
            seed=seed,
        )
        keep_all(session)
        session.engine.permissions.invalidate_current()
        session.engine.state.priority_player = None
        return session.engine

    @staticmethod
    def card(engine, ref):
        return next(
            card for card in engine.state.cards.values() if card.ref == ref
        )

    @staticmethod
    def attach(attachment, target):
        attachment.attached_to = target.object_id
        target.attachments.append(attachment.object_id)

    def transforming_siege(self, engine):
        card = next(
            candidate
            for candidate in engine.state.cards.values()
            if candidate.oracle_id
            == "883a4180-9ede-4249-a4b8-3a29c998fb63"
            and candidate.owner == "A"
        )
        engine.move_card(
            card.object_id,
            "battlefield",
            controller="A",
            reason="CR 310.12b test setup",
            semantic_events=False,
        )
        card.annotations["copy_overrides"] = {
            "name": "Test Invasion",
            "type_line": "Battle — Siege",
            "defense": "1",
            "oracle_text": "",
        }
        card.battle_protector = "B"
        card.counters["defense"] = 1
        return card

    def transforming_siege_record(
        self,
        engine,
        siege,
        *,
        back_name,
        back_type_line,
        back_oracle_text,
    ):
        record = engine.card_record(siege)
        self.assertIsNotNone(record)
        front = dict(record.faces[0])
        back = {
            "name": back_name,
            "mana_cost": "",
            "type_line": back_type_line,
            "oracle_text": back_oracle_text,
            "power": None,
            "toughness": None,
            "loyalty": None,
            "defense": None,
            "colors": [],
        }
        return replace(
            record,
            name=f"{front['name']} // {back_name}",
            type_line=f"{front['type_line']} // {back_type_line}",
            oracle_text=f"{front.get('oracle_text', '')} // {back_oracle_text}",
            layout="transform",
            faces=(front, back),
        )

    def test_opposing_power_toughness_counters_cancel_in_pairs(self):
        engine = self.make_engine(7041)
        ref = engine.create_token(
            "A",
            name="Counter Bear",
            characteristics={
                "type_line": "Token Creature — Bear",
                "power": "2",
                "toughness": "2",
            },
        )[0]
        creature = self.card(engine, ref)
        creature.counters.update({"+1/+1": 3, "-1/-1": 1})

        self.assertFalse(engine._stabilize())

        self.assertEqual(2, creature.counters["+1/+1"])
        self.assertNotIn("-1/-1", creature.counters)
        event = next(
            event
            for event in reversed(engine.state.events)
            if event.code == "state.counters_annihilated"
        )
        self.assertEqual(
            [{"object": ref, "pairs_removed": 1}],
            event.details["changes"],
        )

    def test_battle_enters_with_printed_defense_and_copies_reset_it(self):
        engine = self.make_engine(7052)
        original_ref = engine.create_token(
            "A",
            name="Test Battle",
            characteristics={
                "type_line": "Token Battle",
                "defense": "4",
            },
        )[0]
        original = self.card(engine, original_ref)
        self.assertEqual(4, original.counters["defense"])
        change_permanent_counter(engine, original, "defense", -3)
        self.assertEqual(
            "1",
            engine._effective_card_data(original)["defense"],
        )

        copied_ref = engine.create_token(
            "A",
            name="Test Battle Copy",
            copy_of=original.ref,
        )[0]
        copied = self.card(engine, copied_ref)

        self.assertEqual(1, original.counters["defense"])
        self.assertEqual(4, copied.counters["defense"])
        self.assertEqual(
            "4",
            engine._effective_card_data(copied)["defense"],
        )
        engine.move_card(
            original.object_id,
            "graveyard",
            reason="off-battlefield defense test",
            semantic_events=False,
        )
        self.assertEqual(
            "4",
            engine._effective_card_data(original)["defense"],
        )

    def test_battle_without_valid_printed_defense_fails_closed(self):
        for index, defense in enumerate((None, "not-a-number", "-1")):
            with self.subTest(defense=defense):
                engine = self.make_engine(7080 + index)
                with self.assertRaisesRegex(
                    GameRuleError,
                    (
                        "Battle defense must be a represented "
                        "nonnegative integer"
                        if defense != "-1"
                        else "Battle defense cannot be negative"
                    ),
                ):
                    engine.create_token(
                        "A",
                        name="Malformed Battle",
                        characteristics={
                            "type_line": "Token Battle",
                            "defense": defense,
                        },
                    )

    def test_typeless_battle_controller_is_its_protector(self):
        engine = self.make_engine(7071)
        battle_ref = engine.create_token(
            "A",
            name="Typeless Battle",
            characteristics={
                "type_line": "Token Battle",
                "defense": "3",
            },
        )[0]
        battle = self.card(engine, battle_ref)

        self.assertEqual("A", battle.battle_protector)
        self.assertNotIn(
            battle.ref,
            {
                candidate["id"]
                for candidate in engine._attackable_battles("A")
            },
        )
        self.assertIn(
            battle.ref,
            {
                candidate["id"]
                for candidate in engine._attackable_battles("B")
            },
        )

    def test_battle_protector_persists_through_type_and_copy_changes(self):
        engine = self.make_engine(7072)
        battle_ref = engine.create_token(
            "A",
            name="Persistent Protector Siege",
            battle_protector="B",
            characteristics={
                "type_line": "Token Battle — Siege",
                "defense": "3",
            },
        )[0]
        battle = self.card(engine, battle_ref)

        battle.annotations["copy_overrides"] = {
            "name": "Temporary Creature",
            "type_line": "Creature — Shapeshifter",
            "power": "2",
            "toughness": "2",
        }
        self.assertIsNone(engine._repair_battle_protectors())
        self.assertEqual("B", battle.battle_protector)

        battle.annotations["copy_overrides"] = {
            "name": "Different Siege",
            "type_line": "Battle — Siege",
            "defense": "5",
        }
        self.assertIsNone(engine._repair_battle_protectors())
        self.assertEqual("B", battle.battle_protector)

    def test_attached_battle_becomes_unattached(self):
        engine = self.make_engine(7073)
        target_ref = engine.create_token(
            "A",
            name="Attachment Target",
            characteristics={
                "type_line": "Token Creature — Soldier",
                "power": "2",
                "toughness": "2",
            },
        )[0]
        battle_ref = engine.create_token(
            "A",
            name="Attached Battle",
            characteristics={
                "type_line": "Token Battle",
                "defense": "3",
            },
        )[0]
        target = self.card(engine, target_ref)
        battle = self.card(engine, battle_ref)
        battle.attached_to = target.object_id
        target.attachments.append(battle.object_id)

        self.assertFalse(engine._stabilize())

        self.assertIsNone(battle.attached_to)
        self.assertNotIn(battle.object_id, target.attachments)
        self.assertEqual("battlefield", battle.zone)

    def test_invalid_battle_protector_waits_while_it_is_attacked(self):
        engine = self.make_engine(7074)
        attacker_ref = engine.create_token(
            "C",
            name="Protector Repair Attacker",
            characteristics={
                "type_line": "Token Creature — Soldier",
                "power": "2",
                "toughness": "2",
                "keywords": ["Haste"],
            },
        )[0]
        battle_ref = engine.create_token(
            "A",
            name="Attacked Siege",
            battle_protector="B",
            characteristics={
                "type_line": "Token Battle — Siege",
                "defense": "3",
            },
        )[0]
        attacker = self.card(engine, attacker_ref)
        battle = self.card(engine, battle_ref)
        engine.state.players["B"].in_game = False
        engine.state.combat.attackers[attacker.object_id] = battle.ref

        self.assertIsNone(engine._repair_battle_protectors())
        self.assertEqual("B", battle.battle_protector)
        self.assertIsNone(engine.state.pending_decision)

        engine.state.combat.attackers.clear()
        self.assertEqual(
            "waiting",
            engine._repair_battle_protectors(),
        )
        self.assertEqual(
            ["C", "D"],
            engine.state.pending_decision.payload_by_actor["A"][
                "protectors"
            ],
        )

    def test_controller_protector_is_repaired_even_while_attacked(self):
        engine = self.make_engine(7075)
        attacker_ref = engine.create_token(
            "B",
            name="Controller Protector Attacker",
            characteristics={
                "type_line": "Token Creature — Soldier",
                "power": "2",
                "toughness": "2",
                "keywords": ["Haste"],
            },
        )[0]
        battle_ref = engine.create_token(
            "A",
            name="Controller-Protected Siege",
            battle_protector="B",
            characteristics={
                "type_line": "Token Battle — Siege",
                "defense": "3",
            },
        )[0]
        attacker = self.card(engine, attacker_ref)
        battle = self.card(engine, battle_ref)
        battle.battle_protector = "A"
        engine.state.combat.attackers[attacker.object_id] = battle.ref

        self.assertEqual(
            "waiting",
            engine._repair_battle_protectors(),
        )
        self.assertEqual(
            ["B", "C", "D"],
            engine.state.pending_decision.payload_by_actor["A"][
                "protectors"
            ],
        )

    def test_siege_without_legal_protector_goes_to_owner_graveyard(self):
        engine = self.make_engine(7076)
        battle_ref = engine.create_token(
            "A",
            name="Opponentless Siege",
            battle_protector="B",
            characteristics={
                "type_line": "Token Battle — Siege",
                "defense": "3",
            },
        )[0]
        battle = self.card(engine, battle_ref)
        for seat in ("B", "C", "D"):
            engine.state.players[seat].in_game = False

        self.assertEqual(
            "changed",
            engine._repair_battle_protectors(),
        )
        self.assertEqual("graveyard", battle.zone)
        self.assertIsNone(engine.state.pending_decision)

    def test_battle_damage_removes_defense_instead_of_marking_damage(self):
        engine = self.make_engine(7053)
        source_ref = engine.create_token(
            "A",
            name="Battle Tester",
            characteristics={
                "type_line": "Token Creature — Wizard",
                "power": "2",
                "toughness": "2",
            },
        )[0]
        battle_ref = engine.create_token(
            "B",
            name="Test Battle",
            characteristics={
                "type_line": "Token Battle",
                "defense": "5",
            },
        )[0]
        battle = self.card(engine, battle_ref)

        engine._apply_combat_assignments(
            [
                {
                    "source": source_ref,
                    "target": battle_ref,
                    "amount": 2,
                }
            ]
        )

        self.assertEqual(3, battle.counters["defense"])
        self.assertEqual(0, battle.marked_damage)

    def test_damage_to_battle_creature_applies_both_results(self):
        engine = self.make_engine(7077)
        battle_ref = engine.create_token(
            "A",
            name="Animated Siege",
            battle_protector="B",
            characteristics={
                "type_line": "Token Battle Creature — Siege",
                "power": "3",
                "toughness": "4",
                "defense": "5",
            },
        )[0]
        battle = self.card(engine, battle_ref)

        result = apply_damage_results_to_permanent(
            engine,
            battle,
            2,
        )

        self.assertEqual(2, result["marked_damage"])
        self.assertEqual(2, result["defense_removed"])
        self.assertEqual(2, battle.marked_damage)
        self.assertEqual(3, battle.counters["defense"])

    def test_damage_rejects_nondamageable_permanent(self):
        engine = self.make_engine(7083)
        artifact_ref = engine.create_token(
            "A",
            name="Nondamageable Relic",
            characteristics={
                "type_line": "Token Artifact",
            },
        )[0]
        artifact = self.card(engine, artifact_ref)

        with self.assertRaisesRegex(
            DamageError,
            "is not a Battle, creature, or planeswalker",
        ):
            apply_damage_results_to_permanent(
                engine,
                artifact,
                1,
            )

    def test_zero_damage_creates_no_results_or_damage_event(self):
        engine = self.make_engine(7084)
        creature_ref = engine.create_token(
            "A",
            name="Zero Damage Creature",
            characteristics={
                "type_line": "Token Creature — Test",
                "power": "0",
                "toughness": "2",
            },
        )[0]
        creature = self.card(engine, creature_ref)
        creature.is_commander = True
        before_life = engine.state.players["B"].life
        before_events = len(engine.state.events)

        result = apply_damage_results_to_permanent(
            engine,
            creature,
            0,
            source_keywords=("Deathtouch",),
        )
        engine.apply_effect(
            {
                "op": "damage",
                "target": "B",
                "amount": 0,
            },
            actor="A",
        )
        engine.apply_effect(
            {
                "op": "damage_each_opponent",
                "amount": 0,
            },
            actor="A",
        )
        engine._apply_combat_assignments(
            [
                {
                    "source": creature.ref,
                    "target": "B",
                    "amount": 0,
                }
            ]
        )

        self.assertEqual(
            {"amount": 0, "types": ["creature"]},
            result,
        )
        self.assertEqual(0, creature.marked_damage)
        self.assertFalse(creature.deathtouch_damage)
        self.assertEqual(before_life, engine.state.players["B"].life)
        self.assertEqual(
            {},
            engine.state.players["B"].commander_damage_received,
        )
        self.assertFalse(
            any(
                event.code == "effect.damage"
                for event in engine.state.events[before_events:]
            )
        )
        combat_event = next(
            event
            for event in reversed(engine.state.events)
            if event.code == "combat.damage"
        )
        self.assertEqual([], combat_event.details["assignments"])

    def test_negative_damage_fails_closed(self):
        engine = self.make_engine(7085)
        creature_ref = engine.create_token(
            "A",
            name="Negative Damage Creature",
            characteristics={
                "type_line": "Token Creature — Test",
                "power": "1",
                "toughness": "2",
            },
        )[0]
        creature = self.card(engine, creature_ref)

        with self.assertRaisesRegex(
            DamageError,
            "Damage cannot be negative",
        ):
            apply_damage_results_to_permanent(
                engine,
                creature,
                -1,
            )
        with self.assertRaisesRegex(
            GameRuleError,
            "Damage cannot be negative",
        ):
            engine.apply_effect(
                {
                    "op": "damage",
                    "target": "B",
                    "amount": -1,
                },
                actor="A",
            )
        with self.assertRaisesRegex(
            GameRuleError,
            "Damage cannot be negative",
        ):
            engine.apply_effect(
                {
                    "op": "damage_each_opponent",
                    "amount": -1,
                },
                actor="A",
            )
        with self.assertRaisesRegex(
            GameRuleError,
            "Damage cannot be negative",
        ):
            engine._apply_combat_assignments(
                [
                    {
                        "source": creature.ref,
                        "target": "B",
                        "amount": -1,
                    }
                ]
            )

    def test_negative_power_combat_source_assigns_no_damage(self):
        engine = self.make_engine(7088)
        creature_ref = engine.create_token(
            "A",
            name="Negative Power Attacker",
            characteristics={
                "type_line": "Token Creature — Test",
                "power": "-1",
                "toughness": "2",
            },
        )[0]
        creature = self.card(engine, creature_ref)
        engine.state.active_player = "A"
        engine.state.combat.attackers[creature.object_id] = "B"
        before_life = engine.state.players["B"].life

        engine._begin_combat_damage()

        self.assertEqual(before_life, engine.state.players["B"].life)
        self.assertEqual([], engine.state.combat.damage_assignments)
        combat_event = next(
            event
            for event in reversed(engine.state.events)
            if event.code == "combat.damage"
        )
        self.assertEqual([], combat_event.details["assignments"])

    def test_damage_results_do_not_destroy_before_state_actions(self):
        engine = self.make_engine(7086)
        creature_ref = engine.create_token(
            "A",
            name="Lethally Damaged Creature",
            characteristics={
                "type_line": "Token Creature — Test",
                "power": "2",
                "toughness": "2",
            },
        )[0]
        creature = self.card(engine, creature_ref)

        apply_damage_results_to_permanent(
            engine,
            creature,
            2,
        )

        self.assertEqual("battlefield", creature.zone)
        self.assertEqual(2, creature.marked_damage)
        self.assertFalse(engine._stabilize())
        self.assertEqual("outside", creature.zone)

    def test_damage_to_multityped_permanent_applies_every_result(self):
        engine = self.make_engine(7089)
        characteristics = {
            "type_line": "Token Creature Planeswalker Battle — Siege",
            "power": "3",
            "toughness": "4",
            "loyalty": "5",
            "defense": "6",
        }
        creation = {
            "name": "Every Damageable Type",
            "battle_protector": "B",
            "characteristics": characteristics,
        }
        before = authoritative_state_hash(engine.state)
        with self.assertRaises(ReplacementChoiceRequired) as raised:
            engine.create_token("A", **creation)
        self.assertEqual(before, authoritative_state_hash(engine.state))
        options = raised.exception.pending.choice.options
        self.assertEqual(2, len(options))
        self.assertTrue(all(
            option.startswith("replacement.intrinsic-entry-counter:")
            for option in options
        ))
        permanent_ref = engine.create_token(
            "A", **creation, replacement_selections=(options[0],)
        )[0]
        permanent = self.card(engine, permanent_ref)

        result = apply_damage_results_to_permanent(
            engine,
            permanent,
            2,
        )

        self.assertEqual(2, result["marked_damage"])
        self.assertEqual(2, result["loyalty_removed"])
        self.assertEqual(2, result["defense_removed"])
        self.assertEqual(2, permanent.marked_damage)
        self.assertEqual(3, permanent.counters["loyalty"])
        self.assertEqual(4, permanent.counters["defense"])

    def test_marked_damage_survives_type_loss_until_cleanup(self):
        engine = self.make_engine(7087)
        creature_ref = engine.create_token(
            "A",
            name="Temporarily Animated Relic",
            characteristics={
                "type_line": "Token Creature — Construct",
                "power": "3",
                "toughness": "4",
            },
        )[0]
        creature = self.card(engine, creature_ref)
        apply_damage_results_to_permanent(
            engine,
            creature,
            2,
        )
        creature.annotations["copy_overrides"] = {
            "name": "Dormant Relic",
            "type_line": "Artifact",
        }

        self.assertFalse(engine._stabilize())
        self.assertEqual(2, creature.marked_damage)

        engine._finish_cleanup()

        self.assertEqual(0, creature.marked_damage)

    def test_planeswalker_damage_removes_loyalty_counters(self):
        engine = self.make_engine(7061)
        walker_ref = engine.create_token(
            "A",
            name="Test Planeswalker",
            characteristics={
                "type_line": "Token Planeswalker — Test",
                "loyalty": "4",
            },
        )[0]
        walker = self.card(engine, walker_ref)
        self.assertEqual(4, walker.counters["loyalty"])

        result = apply_damage_results_to_permanent(
            engine,
            walker,
            2,
        )

        self.assertEqual(2, result["loyalty_removed"])
        self.assertEqual(2, walker.counters["loyalty"])
        self.assertEqual(0, walker.marked_damage)

    def test_battle_trigger_from_same_incarnation_defers_state_action(self):
        engine = self.make_engine(7054)
        battle_ref = engine.create_token(
            "A",
            name="Triggered Battle",
            characteristics={
                "type_line": "Token Battle",
                "defense": "1",
            },
        )[0]
        battle = self.card(engine, battle_ref)
        trigger = StackItem(
            stack_id="battle-trigger",
            ref="S-battle-trigger",
            kind="triggered_ability",
            controller="A",
            label="Battle trigger",
            source_object_id=battle.object_id,
            visibility=["A", "B"],
            context={
                "source_logical_object_id": battle.logical_object_id,
            },
        )
        engine.state.stack.append(trigger)
        change_permanent_counter(engine, battle, "defense", -1)

        self.assertFalse(engine._stabilize())
        self.assertEqual("battlefield", battle.zone)

        engine.state.stack.remove(trigger)
        self.assertFalse(engine._stabilize())
        self.assertEqual("outside", battle.zone)

    def test_old_incarnation_trigger_does_not_defer_battle_state_action(self):
        engine = self.make_engine(7061)
        battle_ref = engine.create_token(
            "A",
            name="Reentered Battle",
            characteristics={
                "type_line": "Token Battle",
                "defense": "1",
            },
        )[0]
        battle = self.card(engine, battle_ref)
        engine.state.stack.append(
            StackItem(
                stack_id="old-battle-trigger",
                ref="S-old-battle-trigger",
                kind="triggered_ability",
                controller="A",
                label="Trigger from an old object incarnation",
                source_object_id=battle.object_id,
                visibility=["A", "B", "C", "D"],
                context={
                    "source_logical_object_id": "old-incarnation",
                },
            )
        )
        change_permanent_counter(engine, battle, "defense", -1)

        self.assertFalse(engine._battle_trigger_pending(battle))
        self.assertFalse(engine._stabilize())
        self.assertEqual("outside", battle.zone)

    def test_last_siege_defense_counter_queues_intrinsic_trigger(self):
        engine = self.make_engine(7055)
        siege_ref = engine.create_token(
            "A",
            name="Test Siege",
            battle_protector="B",
            characteristics={
                "type_line": "Token Battle — Siege",
                "defense": "2",
            },
        )[0]
        siege = self.card(engine, siege_ref)
        engine.state.stack.append(
            StackItem(
                stack_id="other-siege-trigger",
                ref="S-other-siege-trigger",
                kind="triggered_ability",
                controller="A",
                label="Unrelated Siege trigger",
                source_object_id=siege.object_id,
                semantic_key="test:other-siege-trigger",
                visibility=["A", "B", "C", "D"],
                context={
                    "source_logical_object_id": (
                        siege.logical_object_id
                    )
                },
            )
        )

        result = apply_damage_results_to_permanent(engine, siege, 2)
        self.assertEqual(2, result["defense_removed"])
        self.assertFalse(engine._stabilize())

        trigger = next(
            item
            for item in engine.state.stack
            if item.semantic_key == "builtin:siege-defeated"
        )
        self.assertEqual(siege.object_id, trigger.source_object_id)
        self.assertEqual(
            siege.logical_object_id,
            trigger.context["source_logical_object_id"],
        )
        self.assertEqual("battlefield", siege.zone)
        engine._prepare_stack_resolution()
        self.assertEqual("outside", siege.zone)
        self.assertIsNone(engine.state.pending_decision)
        self.assertEqual(
            "exiled_not_castable_transformed",
            next(
                event
                for event in reversed(engine.state.events)
                if event.code
                == "battle.siege_defeated.resolve"
            ).details["outcome"],
        )

    def test_siege_defeated_casts_back_face_for_free_during_resolution(self):
        engine = self.make_engine(7064)
        siege = self.transforming_siege(engine)
        engine.state.active_player = "B"
        engine.state.phase = "combat"
        engine.state.step = "combat_damage"
        change_permanent_counter(engine, siege, "defense", -1)
        self.assertFalse(engine._stabilize())

        trigger = next(
            item
            for item in engine.state.stack
            if item.semantic_key == "builtin:siege-defeated"
        )
        engine._prepare_stack_resolution()

        self.assertEqual("exile", siege.zone)
        self.assertEqual(
            "battle.siege_defeated",
            engine.state.pending_decision.kind,
        )
        payload = engine.state.pending_decision.payload_by_actor["A"]
        self.assertEqual("Consuming Sepulcher", payload["transformed_face"])
        mana_before = dict(engine.state.players["A"].mana_pool)
        capability = next(
            value
            for value in engine.state.capabilities.values()
            if (
                value.decision_id
                == engine.state.pending_decision.decision_id
                and value.principal == "pilot:A"
                and not value.consumed
            )
        )

        result = engine.try_submit(
            token=capability.token,
            principal="pilot:A",
            action="choose",
            payload={"choice": "cast"},
        )

        self.assertTrue(result.ok, result.summary)
        self.assertEqual("stack", siege.zone)
        self.assertEqual("Consuming Sepulcher", siege.active_face)
        self.assertFalse(
            any(item.ref == trigger.ref for item in engine.state.stack)
        )
        cast_item = next(
            item
            for item in engine.state.stack
            if item.card_object_id == siege.object_id
        )
        self.assertEqual("spell", cast_item.kind)
        self.assertEqual(
            "without_mana_cost",
            cast_item.context["cost_option"],
        )
        self.assertEqual(
            mana_before,
            dict(engine.state.players["A"].mana_pool),
        )
        cast_event = next(
            event
            for event in reversed(engine.state.events)
            if event.code == "stack.cast"
            and event.details.get("object") == siege.ref
        )
        self.assertEqual("exile", cast_event.details["from"])
        self.assertEqual({}, cast_event.details["payment"])
        self.assertEqual(
            "cast_transformed",
            next(
                event
                for event in reversed(engine.state.events)
                if event.code
                == "battle.siege_defeated.resolve"
            ).details["outcome"],
        )

    def test_siege_defeated_may_be_declined_after_exile(self):
        engine = self.make_engine(7065)
        siege = self.transforming_siege(engine)
        change_permanent_counter(engine, siege, "defense", -1)
        self.assertFalse(engine._stabilize())
        engine._prepare_stack_resolution()
        capability = next(
            value
            for value in engine.state.capabilities.values()
            if (
                value.decision_id
                == engine.state.pending_decision.decision_id
                and value.principal == "pilot:A"
                and not value.consumed
            )
        )

        result = engine.try_submit(
            token=capability.token,
            principal="pilot:A",
            action="choose",
            payload={"choice": "decline"},
        )

        self.assertTrue(result.ok, result.summary)
        self.assertEqual("exile", siege.zone)
        self.assertIsNone(siege.active_face)
        self.assertFalse(
            any(
                item.semantic_key == "builtin:siege-defeated"
                for item in engine.state.stack
            )
        )
        self.assertEqual(
            "declined",
            next(
                event
                for event in reversed(engine.state.events)
                if event.code
                == "battle.siege_defeated.resolve"
            ).details["outcome"],
        )

    def test_siege_uncompiled_nonpermanent_back_fails_closed_without_prose(self):
        engine = self.make_engine(7069)
        siege = self.transforming_siege(engine)
        original_card_record = engine.card_record
        targeted_record = self.transforming_siege_record(
            engine,
            siege,
            back_name="Uncompiled Victory",
            back_type_line="Sorcery",
            back_oracle_text="Draw two cards.",
        )

        def staged_record(value):
            candidate = (
                value
                if hasattr(value, "object_id")
                else engine.state.cards.get(str(value))
            )
            if (
                candidate is not None
                and candidate.object_id == siege.object_id
            ):
                return targeted_record
            return original_card_record(value)

        change_permanent_counter(engine, siege, "defense", -1)
        self.assertFalse(engine._stabilize())
        with patch.object(
            engine,
            "card_record",
            side_effect=staged_record,
        ):
            engine._prepare_stack_resolution()

        self.assertEqual("exile", siege.zone)
        self.assertEqual(
            "arbiter.resolve",
            engine.state.pending_decision.kind,
        )
        payload = engine.state.pending_decision.payload_by_actor[
            "arbiter"
        ]
        self.assertEqual("Uncompiled Victory", payload["transformed_face"])
        self.assertEqual(
            (
                "transformed Siege spell lacks trusted typed cast semantics"
            ),
            payload["reason"],
        )
        self.assertTrue(
            any(
                item.semantic_key == "builtin:siege-defeated"
                for item in engine.state.stack
            )
        )

    def test_siege_targeted_back_uses_compiled_target_schema(self):
        engine = self.make_engine(7070)
        siege = self.transforming_siege(engine)
        original_card_record = engine.card_record
        targeted_record = self.transforming_siege_record(
            engine,
            siege,
            back_name="Compiled Victory",
            back_type_line="Sorcery",
            back_oracle_text="Target player draws two cards.",
        )
        program = SemanticProgram(
            key=(
                f"{targeted_record.oracle_id}:spell:"
                "Compiled Victory"
            ),
            label="Compiled Victory",
            effects=[
                {
                    "op": "draw",
                    "player": "$target.0",
                    "count": 2,
                    "private": True,
                }
            ],
            destination="graveyard",
            target_schema={
                "zones": ["player"],
                "categories": ["player"],
                "player_relation": "any",
                "count": 1,
            },
            trust_level="provisional",
        )
        engine.semantics.put(program)

        def staged_record(value):
            candidate = (
                value
                if hasattr(value, "object_id")
                else engine.state.cards.get(str(value))
            )
            if (
                candidate is not None
                and candidate.object_id == siege.object_id
            ):
                return targeted_record
            return original_card_record(value)

        change_permanent_counter(engine, siege, "defense", -1)
        self.assertFalse(engine._stabilize())
        with patch.object(
            engine,
            "card_record",
            side_effect=staged_record,
        ):
            engine._prepare_stack_resolution()
            self.assertEqual(
                "battle.siege_defeated",
                engine.state.pending_decision.kind,
            )
            public_option = engine.state.pending_decision.payload_by_actor[
                "A"
            ]["cast_options"][0]
            self.assertIn("B", public_option["target_schema"]["legal_refs"])
            capability = next(
                value
                for value in engine.state.capabilities.values()
                if (
                    value.decision_id
                    == engine.state.pending_decision.decision_id
                    and value.principal == "pilot:A"
                    and not value.consumed
                )
            )
            result = engine.try_submit(
                token=capability.token,
                principal="pilot:A",
                action="choose",
                payload={
                    "choice": "cast",
                    "targets": ["B"],
                },
            )

        self.assertTrue(result.ok, result.summary)
        self.assertEqual("stack", siege.zone)
        cast_item = next(
            item
            for item in engine.state.stack
            if item.card_object_id == siege.object_id
        )
        self.assertEqual(["B"], cast_item.targets)
        self.assertEqual(program.key, cast_item.semantic_key)

    def test_siege_trigger_does_not_follow_a_changed_object(self):
        engine = self.make_engine(7066)
        siege = self.transforming_siege(engine)
        change_permanent_counter(engine, siege, "defense", -1)
        self.assertFalse(engine._stabilize())
        engine.move_card(
            siege.object_id,
            "graveyard",
            reason="source removed before defeated trigger",
        )
        departed_logical_object_id = siege.logical_object_id
        engine.move_card(
            siege.object_id,
            "battlefield",
            controller="A",
            reason="source returned before defeated trigger",
            semantic_events=False,
        )
        siege.annotations["copy_overrides"] = {
            "name": "Returned Test Invasion",
            "type_line": "Battle — Siege",
            "defense": "1",
            "oracle_text": "",
        }
        siege.battle_protector = "B"
        siege.counters["defense"] = 1
        self.assertNotEqual(
            departed_logical_object_id,
            siege.logical_object_id,
        )

        engine._prepare_stack_resolution()

        self.assertEqual("battlefield", siege.zone)
        self.assertFalse(
            any(
                item.semantic_key == "builtin:siege-defeated"
                for item in engine.state.stack
            )
        )
        self.assertEqual(
            "source_unavailable",
            next(
                event
                for event in reversed(engine.state.events)
                if event.code
                == "battle.siege_defeated.resolve"
            ).details["outcome"],
        )

    def test_transform_back_face_cannot_be_cast_without_permission(self):
        engine = self.make_engine(7067)
        card = next(
            candidate
            for candidate in engine.state.cards.values()
            if candidate.oracle_id
            == "883a4180-9ede-4249-a4b8-3a29c998fb63"
            and candidate.owner == "A"
        )
        engine.move_card(card.object_id, "hand", reason="test setup")
        engine.state.active_player = "A"
        engine.state.phase = "precombat_main"
        engine.state.step = "main"
        engine.state.priority_player = "A"

        with self.assertRaisesRegex(
            GameRuleError,
            "back face cannot be cast",
        ):
            engine._cast(
                "A",
                {
                    "card": card.ref,
                    "from": "hand",
                    "face": "Consuming Sepulcher",
                },
            )

    def test_siege_transformed_cast_choice_replays_exactly(self):
        session = make_session(
            self.db,
            self.mishra,
            self.zimone,
            seed=7068,
        )
        keep_all(session)
        engine = session.engine
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        siege = self.transforming_siege(engine)
        change_permanent_counter(engine, siege, "defense", -1)
        self.assertFalse(engine._stabilize())
        engine._prepare_stack_resolution()
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()

        result = session.act(
            "pilot:A",
            {
                "action_id": "cast",
                "reason": "Cast the defeated Siege transformed.",
                "plan": "RULES_CHOICE",
            },
        )

        self.assertTrue(result.ok, result.summary)
        self.assertEqual("stack", siege.zone)
        self.assertEqual("Consuming Sepulcher", siege.active_face)
        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / "siege-cast-record"
            session.save(record_dir)
            replay = replay_record(
                record_dir,
                self.db,
                verify=True,
            )
        self.assertTrue(replay["ok"], replay)

    def test_departed_combat_source_deals_no_damage(self):
        engine = self.make_engine(7062)
        source_ref = engine.create_token(
            "A",
            name="Departed Attacker",
            characteristics={
                "type_line": "Token Creature — Soldier",
                "power": "2",
                "toughness": "2",
            },
        )[0]
        source = self.card(engine, source_ref)
        engine.move_card(source.object_id, "graveyard")
        life_before = engine.state.players["B"].life

        engine._apply_combat_assignments(
            [
                {
                    "source": source_ref,
                    "target": "B",
                    "amount": 2,
                }
            ]
        )

        self.assertEqual(life_before, engine.state.players["B"].life)
        self.assertEqual(
            "combat.damage.no_source",
            next(
                event
                for event in reversed(engine.state.events)
                if event.code == "combat.damage.no_source"
            ).code,
        )


    def test_invalid_siege_protector_is_repaired_by_its_controller(self):
        engine = self.make_engine(7057)
        siege_ref = engine.create_token(
            "A",
            name="Protector Test Siege",
            battle_protector="B",
            characteristics={
                "type_line": "Token Battle — Siege",
                "defense": "3",
            },
        )[0]
        siege = self.card(engine, siege_ref)
        engine.state.players["B"].in_game = False

        self.assertTrue(engine._stabilize())
        self.assertEqual(
            "state.battle_protector",
            engine.state.pending_decision.kind,
        )
        payload = engine.state.pending_decision.payload_by_actor["A"]
        self.assertEqual(["C", "D"], payload["protectors"])
        capability = next(
            value
            for value in engine.state.capabilities.values()
            if (
                value.decision_id
                == engine.state.pending_decision.decision_id
                and value.principal == "pilot:A"
                and not value.consumed
            )
        )

        result = engine.try_submit(
            token=capability.token,
            principal="pilot:A",
            action="choose",
            payload={"protector": "C"},
        )

        self.assertTrue(result.ok, result.summary)
        self.assertEqual("C", siege.battle_protector)
        projected = StateProjector(self.db, engine.state)._obj(
            siege,
            "pilot:A",
        )
        self.assertEqual("C", projected["protect"])
        self.assertNotIn("object_id", projected)
        self.assertNotIn("logical_object_id", projected)

    def test_battle_protector_choice_replays_exactly(self):
        session = make_session(
            self.db,
            self.mishra,
            self.zimone,
            seed=7060,
        )
        keep_all(session)
        engine = session.engine
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        siege_ref = engine.create_token(
            "A",
            name="Replay Protector Siege",
            battle_protector="B",
            characteristics={
                "type_line": "Token Battle — Siege",
                "defense": "3",
            },
        )[0]
        siege = self.card(engine, siege_ref)
        engine.state.players["B"].in_game = False
        self.assertTrue(engine._stabilize())
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()

        result = session.act(
            "pilot:A",
            {
                "action_id": "choose",
                "choices": {"protector": "C"},
                "reason": "Choose a legal replacement protector.",
                "plan": "RULES_CHOICE",
            },
        )

        self.assertTrue(result.ok, result.summary)
        self.assertEqual("C", siege.battle_protector)
        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / "battle-protector-record"
            session.save(record_dir)
            replay = replay_record(
                record_dir,
                self.db,
                verify=True,
            )
        self.assertTrue(replay["ok"], replay)

    def test_siege_is_attackable_and_its_protector_blocks(self):
        engine = self.make_engine(7058)
        attacker_ref = engine.create_token(
            "A",
            name="Siege Attacker",
            characteristics={
                "type_line": "Token Creature — Soldier",
                "power": "1",
                "toughness": "1",
                "keywords": ["Haste"],
            },
        )[0]
        siege_ref = engine.create_token(
            "A",
            name="Attackable Siege",
            battle_protector="B",
            characteristics={
                "type_line": "Token Battle — Siege",
                "defense": "3",
            },
        )[0]
        blocker_ref = engine.create_token(
            "B",
            name="Siege Blocker",
            characteristics={
                "type_line": "Token Creature — Soldier",
                "power": "1",
                "toughness": "1",
            },
        )[0]
        engine.state.active_player = "A"
        engine.state.phase = "combat"
        engine.state.step = "declare_attackers"
        engine._complete_attackers(
            DecisionGroup(
                decision_id="attack-siege",
                kind="combat.attackers",
                role="pilot",
                actors=["A"],
                allowed_actions=["attack"],
                responses={
                    "A": {
                        "attackers": {
                            attacker_ref: siege_ref,
                        }
                    }
                },
            )
        )

        attacker = self.card(engine, attacker_ref)
        siege = self.card(engine, siege_ref)
        self.assertEqual(siege.ref, attacker.attacking)
        self.assertEqual(
            ["B", "C", "D"],
            engine.state.combat.defending_players,
        )
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine._issue_next_blocker()
        payload = engine.state.pending_decision.payload_by_actor["B"]
        self.assertIn(attacker_ref, payload["attackers"])
        self.assertEqual(
            [attacker_ref],
            payload["legal_blocks"][blocker_ref],
        )
        engine._complete_blockers(
            DecisionGroup(
                decision_id="block-siege",
                kind="combat.blockers",
                role="pilot",
                actors=["B"],
                allowed_actions=["block"],
                responses={
                    "B": {
                        "blocks": {
                            blocker_ref: attacker_ref,
                        }
                    }
                },
            )
        )
        self.assertEqual(
            [self.card(engine, blocker_ref).object_id],
            engine.state.combat.blockers[
                self.card(engine, attacker_ref).object_id
            ],
        )
        self.assertNotIn(
            siege.ref,
            {
                candidate["id"]
                for candidate in engine._attackable_battles("B")
            },
        )

    def test_battle_creature_cannot_attack_or_block(self):
        engine = self.make_engine(7063)
        battle_creature_ref = engine.create_token(
            "A",
            name="Animated Battle",
            characteristics={
                "type_line": "Token Battle Creature",
                "power": "3",
                "toughness": "3",
                "defense": "3",
                "keywords": ["Haste"],
            },
        )[0]
        attacker_ref = engine.create_token(
            "A",
            name="Ordinary Attacker",
            characteristics={
                "type_line": "Token Creature — Soldier",
                "power": "2",
                "toughness": "2",
                "keywords": ["Haste"],
            },
        )[0]
        battle_blocker_ref = engine.create_token(
            "B",
            name="Animated Blocking Battle",
            characteristics={
                "type_line": "Token Battle Creature",
                "power": "3",
                "toughness": "3",
                "defense": "3",
            },
        )[0]
        engine.state.active_player = "A"
        engine.state.phase = "combat"
        engine.state.step = "declare_attackers"

        engine._issue_attackers()
        candidates = engine.state.pending_decision.payload_by_actor["A"][
            "candidates"
        ]
        self.assertNotIn(
            battle_creature_ref,
            [candidate["id"] for candidate in candidates],
        )
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        with self.assertRaisesRegex(
            GameRuleError,
            "cannot attack because it is a Battle",
        ):
            engine._complete_attackers(
                DecisionGroup(
                    decision_id="illegal-battle-attack",
                    kind="combat.attackers",
                    role="pilot",
                    actors=["A"],
                    allowed_actions=["attack"],
                    responses={
                        "A": {
                            "attackers": {
                                battle_creature_ref: "B",
                            }
                        }
                    },
                )
            )

        engine._complete_attackers(
            DecisionGroup(
                decision_id="ordinary-attack",
                kind="combat.attackers",
                role="pilot",
                actors=["A"],
                allowed_actions=["attack"],
                responses={
                    "A": {
                        "attackers": {
                            attacker_ref: "B",
                        }
                    }
                },
            )
        )
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine._issue_next_blocker()
        self.assertIsNone(engine.state.pending_decision)
        self.assertEqual(
            (False, "blocker_is_battle"),
            engine._can_block(
                self.card(engine, attacker_ref),
                self.card(engine, battle_blocker_ref),
            ),
        )

    def test_siege_protector_is_chosen_as_the_spell_resolves(self):
        engine = self.make_engine(7059)
        object_id = engine.state.players["A"].zones["hand"][0]
        card = engine.state.cards[object_id]
        original_card_record = engine.card_record
        siege_record = CardRecord(
            oracle_id=card.oracle_id,
            name="Invasion of Test // Test Victor",
            mana_cost="{1}",
            mana_value=1,
            type_line="Battle — Siege // Creature — Soldier",
            oracle_text="",
            power=None,
            toughness=None,
            loyalty=None,
            defense="3",
            colors=(),
            color_identity=(),
            keywords=(),
            produced_mana=(),
            layout="transform",
            released_at="2023-04-21",
            legalities={"commander": "legal"},
            faces=(
                {
                    "name": "Invasion of Test",
                    "mana_cost": "{1}",
                    "type_line": "Battle — Siege",
                    "oracle_text": "",
                    "power": None,
                    "toughness": None,
                    "loyalty": None,
                    "defense": "3",
                    "colors": [],
                },
                {
                    "name": "Test Victor",
                    "mana_cost": "",
                    "type_line": "Creature — Soldier",
                    "oracle_text": "",
                    "power": "3",
                    "toughness": "3",
                    "loyalty": None,
                    "defense": None,
                    "colors": [],
                },
            ),
            raw={},
        )

        def staged_record(value):
            candidate = (
                value
                if hasattr(value, "object_id")
                else engine.state.cards.get(str(value))
            )
            if (
                candidate is not None
                and candidate.object_id == card.object_id
            ):
                return siege_record
            return original_card_record(value)

        engine.state.active_player = "A"
        engine.state.phase = "beginning"
        engine.state.step = "upkeep"
        engine.state.priority_player = "A"
        engine.state.players["A"].mana_pool["C"] = 1
        with patch.object(
            engine,
            "card_record",
            side_effect=staged_record,
        ):
            self.assertEqual(
                "3",
                engine._effective_card_data(card)["defense"],
            )
            with self.assertRaisesRegex(
                GameRuleError,
                "requires a main phase",
            ):
                engine._cast(
                    "A",
                    {
                        "card": card.ref,
                        "from": "hand",
                        "auto_pay": True,
                    },
                )
            engine.state.phase = "precombat_main"
            engine.state.step = "main"
            hints = engine._priority_action_hints("A")
            action = next(
                value
                for value in hints["actions"]
                if value["id"] == f"cast:{card.ref}"
            )
            self.assertNotIn("choice_schema", action)
            engine._cast(
                "A",
                {
                    "card": card.ref,
                    "from": "hand",
                    "auto_pay": True,
                },
            )
            self.assertEqual("stack", card.zone)
            self.assertEqual("Invasion of Test", card.active_face)
            self.assertIsNone(card.battle_protector)
            self.assertTrue(
                engine._begin_battle_entry_protector_choice(
                    engine.state.stack[-1]
                )
            )
            self.assertEqual(
                "battle.enter_protector",
                engine.state.pending_decision.kind,
            )
            self.assertEqual(
                ["B", "C", "D"],
                engine.state.pending_decision.payload_by_actor["A"][
                    "protectors"
                ],
            )
            capability = next(
                value
                for value in engine.state.capabilities.values()
                if (
                    value.decision_id
                    == engine.state.pending_decision.decision_id
                    and value.principal == "pilot:A"
                    and not value.consumed
                )
            )
            result = engine.try_submit(
                token=capability.token,
                principal="pilot:A",
                action="choose",
                payload={"protector": "B"},
            )

        self.assertTrue(result.ok, result.summary)
        self.assertEqual("battlefield", card.zone)
        self.assertEqual("B", card.battle_protector)
        self.assertEqual(3, card.counters["defense"])

    def test_battle_damage_and_state_action_replay_exactly(self):
        session = make_session(
            self.db,
            self.mishra,
            self.zimone,
            seed=7056,
        )
        keep_all(session)
        engine = session.engine
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        battle_ref = engine.create_token(
            "B",
            name="Replay Battle",
            characteristics={
                "type_line": "Token Battle",
                "defense": "2",
            },
        )[0]
        battle = self.card(engine, battle_ref)
        program = SemanticProgram(
            key="test:defeat-battle",
            label="Deal two damage to a Battle",
            effects=[
                {
                    "op": "damage",
                    "target": battle.ref,
                    "amount": 2,
                }
            ],
            trust_level="provisional",
        )
        engine.semantics.put(program)
        engine.state.stack.append(
            StackItem(
                stack_id="defeat-battle",
                ref="S-defeat-battle",
                kind="triggered_ability",
                controller="A",
                label=program.label,
                semantic_key=program.key,
                visibility=["A", "B"],
            )
        )
        engine.state.active_player = "A"
        engine.state.phase = "precombat_main"
        engine.state.step = "main"
        engine._grant_priority("A")
        engine._issue_priority("A")
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()

        for principal in (
            "pilot:A",
            "pilot:B",
            "pilot:C",
            "pilot:D",
        ):
            result = session.act(
                principal,
                {
                    "action_id": "pass",
                    "reason": "Allow Battle damage to resolve.",
                    "plan": "HOLD",
                },
            )
            self.assertTrue(result.ok, result.summary)
        self.assertEqual("outside", battle.zone)

        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / "battle-sba-record"
            session.save(record_dir)
            replay = replay_record(
                record_dir,
                self.db,
                verify=True,
            )
        self.assertTrue(replay["ok"], replay)

    def test_counter_maximum_overlaps_opposing_pair_removal(self):
        engine = self.make_engine(7050)
        ref = engine.create_token(
            "A",
            name="Counter-Limited Bear",
            characteristics={
                "type_line": "Token Creature — Bear",
                "oracle_text": "Display text is not runtime authority.",
                "ability_fragments": [
                    ability_fragment_to_dict(
                        CounterMaximumSpec("+1/+1", 2)
                    )
                ],
                "power": "2",
                "toughness": "2",
            },
        )[0]
        creature = self.card(engine, ref)
        creature.counters.update({"+1/+1": 10, "-1/-1": 4})

        self.assertFalse(engine._stabilize())

        self.assertEqual(2, creature.counters["+1/+1"])
        self.assertNotIn("-1/-1", creature.counters)
        event = next(
            event
            for event in reversed(engine.state.events)
            if event.code == "state.counter_maximums"
        )
        self.assertEqual(
            [
                {
                    "object": ref,
                    "counter": "+1/+1",
                    "before": 10,
                    "maximum": 2,
                    "required_removal": 8,
                    "after": 2,
                }
            ],
            event.details["changes"],
        )

    def test_rasputin_counter_maximum_replays_exactly(self):
        session = make_session(
            self.db,
            self.mishra,
            self.zimone,
            seed=7051,
        )
        keep_all(session)
        engine = session.engine
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        rasputin_ref = engine.create_token(
            "A",
            name="Rasputin Dreamweaver Copy",
            characteristics={
                "type_line": (
                    "Token Legendary Creature — Human Wizard"
                ),
                "oracle_text": "Display text is not runtime authority.",
                "ability_fragments": [
                    ability_fragment_to_dict(
                        CounterMaximumSpec("dream", 7)
                    )
                ],
                "power": "4",
                "toughness": "1",
            },
        )[0]
        rasputin = self.card(engine, rasputin_ref)
        rasputin.counters["dream"] = 7
        program = SemanticProgram(
            key="test:rasputin-counter-overflow",
            label="Put two dream counters on Rasputin",
            effects=[
                {
                    "op": "counter",
                    "card": rasputin_ref,
                    "counter": "dream",
                    "delta": 2,
                }
            ],
            trust_level="provisional",
        )
        engine.semantics.put(program)
        engine.state.stack.append(
            StackItem(
                stack_id="rasputin-counter-overflow",
                ref="S-rasputin-counter-overflow",
                kind="triggered",
                controller="A",
                label=program.label,
                source_object_id=rasputin.object_id,
                semantic_key=program.key,
                visibility=["A", "B"],
            )
        )
        engine.state.active_player = "A"
        engine.state.phase = "precombat_main"
        engine.state.step = "main"
        engine._grant_priority("A")
        engine._issue_priority("A")
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()

        for principal in (
            "pilot:A",
            "pilot:B",
            "pilot:C",
            "pilot:D",
        ):
            result = session.act(
                principal,
                {
                    "action_id": "pass",
                    "reason": "Allow the test trigger to resolve.",
                    "plan": "HOLD",
                },
            )
            self.assertTrue(result.ok, result.summary)
        self.assertEqual(7, rasputin.counters["dream"])

        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / "counter-maximum-record"
            session.save(record_dir)
            replay = replay_record(
                record_dir,
                self.db,
                verify=True,
            )
        self.assertTrue(replay["ok"], replay)

    def test_unattached_and_illegally_attached_auras_leave(self):
        engine = self.make_engine(7042)
        land_ref = engine.create_token(
            "A",
            name="Test Land",
            characteristics={"type_line": "Token Land"},
        )[0]
        land = self.card(engine, land_ref)
        creature_ref = engine.create_token(
            "A",
            name="Legal Aura Target",
            characteristics={
                "type_line": "Token Creature",
                "power": "1",
                "toughness": "1",
            },
        )[0]
        creature = self.card(engine, creature_ref)
        aura_ref = engine.create_token(
            "A",
            name="Creature Aura",
            characteristics={
                "type_line": "Token Enchantment — Aura",
                "oracle_text": "Enchant creature",
                "ability_fragments": [
                    ability_fragment_to_dict(SimpleEnchantSpec("creature"))
                ],
            },
            aura_target_ref=creature_ref,
        )[0]
        aura = self.card(engine, aura_ref)
        creature.attachments.remove(aura.object_id)
        aura.attached_to = None
        self.attach(aura, land)
        unattached_ref = engine.create_token(
            "A",
            name="Unattached Aura",
            characteristics={
                "type_line": "Token Enchantment — Aura",
                "oracle_text": "Enchant creature",
                "ability_fragments": [
                    ability_fragment_to_dict(SimpleEnchantSpec("creature"))
                ],
            },
            aura_target_ref=creature_ref,
        )[0]
        unattached = self.card(engine, unattached_ref)
        creature.attachments.remove(unattached.object_id)
        unattached.attached_to = None

        self.assertFalse(engine._stabilize())

        self.assertEqual("outside", aura.zone)
        self.assertEqual("outside", unattached.zone)
        self.assertNotIn(aura.object_id, land.attachments)

    def test_equipment_detaches_from_illegal_or_protected_object(self):
        engine = self.make_engine(7043)
        land_ref = engine.create_token(
            "A",
            name="Test Land",
            characteristics={"type_line": "Token Land"},
        )[0]
        red_equipment_ref = engine.create_token(
            "A",
            name="Red Equipment",
            characteristics={
                "type_line": "Token Artifact — Equipment",
                "colors": ["R"],
            },
        )[0]
        protected_ref = engine.create_token(
            "B",
            name="Protected Bear",
            characteristics={
                "type_line": "Token Creature — Bear",
                "oracle_text": "Protection from red",
                "keywords": ["Protection"],
                "ability_fragments": [
                    ability_fragment_to_dict(
                        ProtectionSpec(
                            ProtectionQualityKind.COLOR,
                            "R",
                        )
                    )
                ],
                "power": "2",
                "toughness": "2",
            },
        )[0]
        colorless_equipment_ref = engine.create_token(
            "A",
            name="Colorless Equipment",
            characteristics={
                "type_line": "Token Artifact — Equipment",
            },
        )[0]
        land = self.card(engine, land_ref)
        red_equipment = self.card(engine, red_equipment_ref)
        protected = self.card(engine, protected_ref)
        colorless_equipment = self.card(
            engine, colorless_equipment_ref
        )
        self.attach(red_equipment, protected)
        self.attach(colorless_equipment, land)

        self.assertFalse(engine._stabilize())

        self.assertEqual("battlefield", red_equipment.zone)
        self.assertIsNone(red_equipment.attached_to)
        self.assertIsNone(colorless_equipment.attached_to)
        self.assertNotIn(
            red_equipment.object_id, protected.attachments
        )
        self.assertNotIn(
            colorless_equipment.object_id, land.attachments
        )

    def test_fixed_point_moves_aura_after_enchanted_creature_dies(self):
        engine = self.make_engine(7044)
        creature_ref = engine.create_token(
            "A",
            name="Doomed Bear",
            characteristics={
                "type_line": "Token Creature — Bear",
                "power": "2",
                "toughness": "2",
            },
        )[0]
        aura_ref = engine.create_token(
            "A",
            name="Creature Aura",
            characteristics={
                "type_line": "Token Enchantment — Aura",
                "oracle_text": "Enchant creature",
                "ability_fragments": [
                    ability_fragment_to_dict(SimpleEnchantSpec("creature"))
                ],
            },
            aura_target_ref=creature_ref,
        )[0]
        creature = self.card(engine, creature_ref)
        aura = self.card(engine, aura_ref)
        creature.marked_damage = 2

        self.assertFalse(engine._stabilize())

        self.assertEqual("outside", creature.zone)
        self.assertEqual("outside", aura.zone)
        state_events = [
            event
            for event in engine.state.events
            if event.code == "state.creatures_died"
        ]
        self.assertGreaterEqual(len(state_events), 2)
        self.assertEqual(
            [creature_ref], state_events[-2].details["destroyed"]
        )
        self.assertEqual(
            [aura_ref],
            state_events[-1].details["put_in_graveyard"],
        )

    def test_simultaneous_move_captures_all_lki_before_mutation(self):
        engine = self.make_engine(7045)
        source_ref = engine.create_token(
            "A",
            name="Static Source",
            characteristics={
                "type_line": "Token Creature — Wizard",
                "power": "2",
                "toughness": "2",
            },
        )[0]
        recipient_ref = engine.create_token(
            "A",
            name="Static Recipient",
            characteristics={
                "type_line": "Token Creature — Bear",
                "power": "2",
                "toughness": "2",
            },
        )[0]
        source = self.card(engine, source_ref)
        recipient = self.card(engine, recipient_ref)
        effective_card_data = engine._effective_card_data
        captured: dict[str, dict] = {}

        def derived_data(card, **kwargs):
            data = effective_card_data(card, **kwargs)
            if (
                card.object_id == recipient.object_id
                and source.zone == "battlefield"
            ):
                data["power"] = "9"
            return data

        def capture(card, **kwargs):
            captured[card.ref] = kwargs["origin_data"]

        with (
            patch.object(
                engine,
                "_effective_card_data",
                side_effect=derived_data,
            ),
            patch.object(
                engine,
                "_dispatch_zone_change_events",
                side_effect=capture,
            ),
        ):
            engine._move_cards_simultaneously(
                [
                    (source.object_id, "graveyard"),
                    (recipient.object_id, "graveyard"),
                ],
                reason="state-based action",
            )

        self.assertEqual("9", captured[recipient_ref]["power"])

    def test_unrecognized_enchant_suffix_fails_before_token_mutation(self):
        engine = self.make_engine(7046)
        before = set(engine.state.cards)
        with self.assertRaisesRegex(
            GameRuleError,
            "trusted compiled Enchant descriptor",
        ):
            engine.create_token(
                "A",
                name="Unsupported Aura",
                characteristics={
                    "type_line": "Token Enchantment — Aura",
                    "oracle_text": (
                        "Enchant creature with flying or a Vehicle you control"
                    ),
                },
            )
        self.assertEqual(before, set(engine.state.cards))
        self.assertIsNone(
            simple_enchant_spec_from_oracle(
                "Enchant creature with flying or a Vehicle you control"
            )
        )

    @staticmethod
    def stage_as_world(engine, card):
        engine._remove_from_zone(card)
        engine._reset_zone_change(card, "stack")
        card.zone = "stack"
        card.controller = card.owner
        card.annotations["copy_overrides"] = {
            "type_line": "World Enchantment",
        }

    def test_new_world_moves_the_older_world_to_graveyard(self):
        engine = self.make_engine(7047)
        object_ids = list(
            engine.state.players["A"].zones["library"][:2]
        )
        old_world, new_world = [
            engine.state.cards[object_id] for object_id in object_ids
        ]
        self.stage_as_world(engine, old_world)
        engine.move_card(
            old_world.object_id,
            "battlefield",
            controller="A",
            log=False,
        )
        self.assertFalse(engine._stabilize())

        self.stage_as_world(engine, new_world)
        engine.move_card(
            new_world.object_id,
            "battlefield",
            controller="A",
            log=False,
        )
        self.assertLess(
            old_world.world_supertype_timestamp,
            new_world.world_supertype_timestamp,
        )
        self.assertFalse(engine._stabilize())

        self.assertEqual("graveyard", old_world.zone)
        self.assertEqual("battlefield", new_world.zone)
        event = next(
            event
            for event in reversed(engine.state.events)
            if event.code == "state.world_rule"
        )
        self.assertEqual(
            [old_world.ref],
            [item["object"] for item in event.details["moved"]],
        )
        self.assertEqual([new_world.ref], event.details["survivors"])

    def test_worlds_entering_simultaneously_are_tied_and_all_leave(self):
        engine = self.make_engine(7048)
        object_ids = list(
            engine.state.players["A"].zones["library"][:2]
        )
        worlds = [
            engine.state.cards[object_id] for object_id in object_ids
        ]
        for card in worlds:
            self.stage_as_world(engine, card)

        engine._move_cards_simultaneously(
            [
                (card.object_id, "battlefield")
                for card in worlds
            ],
            reason="simultaneous World entry",
            log=False,
        )

        self.assertEqual(
            1, len({card.zone_timestamp for card in worlds})
        )
        self.assertEqual(
            1,
            len(
                {
                    card.world_supertype_timestamp
                    for card in worlds
                }
            ),
        )
        self.assertFalse(engine._stabilize())
        self.assertEqual(
            {"graveyard"},
            {card.zone for card in worlds},
        )
        event = next(
            event
            for event in reversed(engine.state.events)
            if event.code == "state.world_rule"
        )
        self.assertEqual([], event.details["survivors"])

    def test_losing_and_regaining_world_gets_a_new_since_time(self):
        engine = self.make_engine(7049)
        card = next(
            engine.state.cards[object_id]
            for object_id in engine.state.players["A"].zones["library"]
            if engine._type_parts(
                str(
                    engine._effective_card_data(
                        engine.state.cards[object_id]
                    ).get("type_line")
                    or ""
                )
            )[0].isdisjoint({"instant", "sorcery"})
        )
        engine.move_card(
            card.object_id,
            "battlefield",
            controller="A",
            log=False,
        )
        card.annotations["copy_overrides"] = {
            "type_line": "World Enchantment",
        }
        self.assertFalse(engine._stabilize())
        first = card.world_supertype_timestamp
        self.assertIsNotNone(first)

        card.annotations.pop("copy_overrides")
        self.assertFalse(engine._stabilize())
        self.assertIsNone(card.world_supertype_timestamp)

        card.annotations["copy_overrides"] = {
            "type_line": "World Enchantment",
        }
        self.assertFalse(engine._stabilize())
        self.assertGreater(card.world_supertype_timestamp, first)


if __name__ == "__main__":
    unittest.main()
