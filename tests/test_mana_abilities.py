from __future__ import annotations

import json
import tempfile
import unittest
from types import SimpleNamespace
from pathlib import Path

from common import keep_all, load_assets, make_session
from quorune.abilities import parse_activated_abilities
from quorune.card_programs import compile_card_program
from quorune.mana_undo import (
    available_mana_undo,
    undo_mana_activation,
)
from quorune.model import CardInstance, StackItem
from quorune.oracle_ir import compile_oracle_card, register_generated_programs
from quorune.record import checkpoint_envelope, replay_record
from quorune.rules.capabilities import load_default_capability_registry


class ManaAbilityRuleTests(unittest.TestCase):
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
            players=2,
            seed=seed,
        )
        keep_all(session)
        session.engine.permissions.invalidate_current()
        session.engine.state.pending_decision = None
        session.engine.state.priority_player = "A"
        return session.engine

    @staticmethod
    def card(engine, owner: str, name: str):
        return next(
            card
            for card in engine.state.cards.values()
            if card.owner == owner
            and card.is_card_object
            and card.printed_name == name
        )

    @staticmethod
    def prepare_main(engine, seat: str = "A") -> None:
        engine.state.active_player = seat
        engine.state.phase = "precombat_main"
        engine.state.step = "main"
        engine.state.stack.clear()
        engine.state.priority_player = seat
        engine.state.priority_passes = []
        engine.state.pending_decision = None

    def test_contract_traces_every_cr_605_rule(self):
        root = Path(__file__).resolve().parents[1]
        contract = json.loads(
            (
                root
                / "mechanics"
                / "contracts"
                / "mana-abilities.json"
            ).read_text(encoding="utf-8")
        )

        self.assertEqual(
            {
                "605",
                "605.1",
                "605.1a",
                "605.1b",
                "605.2",
                "605.3",
                "605.3a",
                "605.3b",
                "605.3c",
                "605.4",
                "605.4a",
                "605.5",
                "605.5a",
                "605.5b",
            },
            {
                rule_id
                for rule_id in contract["rule_references"]
                if str(rule_id).startswith("605")
            },
        )

    def test_activated_mana_ability_excludes_targets_and_loyalty(self):
        ordinary = parse_activated_abilities(
            card_name="Mana Relic",
            oracle_text="{T}: Add {G}.",
        )[0]
        targeted = parse_activated_abilities(
            card_name="Targeted Mana Relic",
            oracle_text="{T}: Target player adds {G}.",
        )[0]
        loyalty = parse_activated_abilities(
            card_name="Loyalty Mana Relic",
            oracle_text="+1: Add {G}.",
        )[0]
        nonmana = parse_activated_abilities(
            card_name="Drawing Relic",
            oracle_text="{T}: Draw a card.",
        )[0]

        self.assertTrue(ordinary.mana_ability)
        self.assertFalse(targeted.mana_ability)
        self.assertEqual(1, loyalty.loyalty_delta)
        self.assertFalse(loyalty.mana_ability)
        self.assertFalse(nonmana.mana_ability)

    def test_library_moving_output_is_not_a_mana_ability(self):
        for oracle_text in (
            "Mill a card, {T}: Add {C}.",
            "{T}: Add {C}, then draw a card.",
            "{T}: Add {C}, then put a card from your graveyard on the "
            "bottom of your library.",
            "{T}: Add {C}, then explore.",
        ):
            with self.subTest(oracle_text=oracle_text):
                ability = parse_activated_abilities(
                    card_name="Library Mana Relic",
                    oracle_text=oracle_text,
                )[0]
                self.assertFalse(ability.mana_ability)

    def test_mana_classification_survives_zero_output_or_unavailability(self):
        conditional = parse_activated_abilities(
            card_name="Conditional Mana Relic",
            oracle_text="{T}: Add {G} for each creature you control.",
        )[0]
        self.assertTrue(conditional.mana_ability)

        engine = self.make_engine(60501)
        island = self.card(engine, "A", "Island")
        engine.move_card(
            island.object_id,
            "battlefield",
            controller="A",
            log=False,
        )
        island.tapped = True
        ability = engine._activated_abilities(island)[0]
        self.assertTrue(ability.mana_ability)
        self.assertEqual(
            ("unavailable", "source_tapped"),
            engine._ability_availability("A", island, ability),
        )

    def test_activated_mana_ability_resolves_without_using_stack(self):
        engine = self.make_engine(60502)
        island = self.card(engine, "A", "Island")
        engine.move_card(
            island.object_id,
            "battlefield",
            controller="A",
            log=False,
        )
        self.prepare_main(engine)
        existing = StackItem(
            stack_id="S-existing",
            ref="S-existing",
            kind="spell",
            controller="B",
            label="Existing spell",
        )
        engine.state.stack.append(existing)
        ability = engine._activated_abilities(island)[0]
        before = engine.state.players["A"].mana_pool["U"]

        engine._activate(
            "A",
            {"source": island.ref, "ability": ability.ability_id},
        )

        self.assertEqual([existing], engine.state.stack)
        self.assertEqual(
            before + 1,
            engine.state.players["A"].mana_pool["U"],
        )
        self.assertTrue(island.tapped)

    def test_multityped_land_exposes_distinct_intrinsic_mana_abilities(self):
        engine = self.make_engine(60505)
        pool = self.card(engine, "B", "Breeding Pool")
        engine.move_card(
            pool.object_id,
            "battlefield",
            controller="B",
            tapped=False,
            log=False,
        )
        pool.tapped = False
        self.prepare_main(engine, "B")
        abilities = {
            ability.ability_id: ability
            for ability in engine._activated_abilities(pool)
        }
        self.assertEqual(
            {"intrinsic_island", "intrinsic_forest"},
            set(abilities),
        )
        self.assertEqual(
            {"intrinsic_island", "intrinsic_forest"},
            {
                action["ability"]
                for action in engine._priority_action_hints("B")["actions"]
                if action.get("source") == pool.ref
            },
        )
        forest_ability = abilities["intrinsic_forest"]
        self.assertEqual(
            [{"G": 1}],
            [
                {key: amount for key, amount in mode.bundle.items() if amount}
                for mode in engine._mana_modes_for_ability(
                    "B", pool, forest_ability
                )
            ],
        )

        engine._activate(
            "B",
            {
                "source": pool.ref,
                "ability": forest_ability.ability_id,
            },
        )

        self.assertTrue(pool.tapped)
        self.assertEqual(1, engine.state.players["B"].mana_pool["G"])
        self.assertEqual(0, engine.state.players["B"].mana_pool["U"])

        painland = self.card(engine, "B", "Elves of Deep Shadow")
        engine.move_card(
            painland.object_id,
            "battlefield",
            controller="B",
            tapped=False,
            log=False,
        )
        engine.state.players["B"].turns_begun = (
            painland.acquired_control_turn_count + 1
        )
        pain_ability = next(
            candidate
            for candidate in engine._activated_abilities(painland)
            if "Add {B}" in candidate.effect_text
        )
        before_life = engine.state.players["B"].life
        engine._activate(
            "B",
            {
                "source": painland.ref,
                "ability": pain_ability.ability_id,
                "mana_output": {"B": 1},
            },
        )
        self.assertEqual(before_life - 1, engine.state.players["B"].life)
        self.assertEqual(1, engine.state.players["B"].mana_pool["B"])

    def test_intrinsic_basic_land_mana_offer_submission_and_replay_are_controller_scoped(
        self,
    ):
        session = make_session(
            self.db,
            self.mishra,
            self.zimone,
            players=4,
            seed=60506,
        )
        keep_all(session)
        engine = session.engine
        pool = self.card(engine, "B", "Breeding Pool")
        record = engine.card_record(pool)
        card_program = compile_card_program(
            self.db,
            record,
            capability_registry=load_default_capability_registry(),
            capability_profile="commander_review",
            trust_level="trusted",
        )
        declarations = [
            ability
            for ability in card_program.to_dict()["abilities"]
            if ability["runtime"]["provenance"].get("template_id")
            == "intrinsic_basic_land_mana"
        ]
        self.assertEqual(
            {"forest", "island"},
            {
                ability["runtime"]["provenance"]["card_form_descriptor"]
                ["basic_land_type"]
                for ability in declarations
            },
        )
        pool = engine.move_card(
            pool.object_id,
            "battlefield",
            controller="B",
            tapped=False,
            log=False,
        )
        pool.tapped = False
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine.state.active_player = "B"
        engine.state.phase = "precombat_main"
        engine.state.step = "main"
        engine.state.stack.clear()
        engine.state.priority_player = None
        engine.state.priority_passes = []
        engine._grant_priority("B")
        engine._issue_priority("B")
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()

        packet = session.packet("pilot:B", full=True)
        action = next(
            value
            for value in packet["decision"]["legal_actions"]
            if value.get("action") == "activate"
            and value.get("source") == pool.ref
            and value.get("ability") == "intrinsic_forest"
        )
        self.assertEqual(
            {"intrinsic_island", "intrinsic_forest"},
            {
                value["ability"]
                for value in packet["decision"]["legal_actions"]
                if value.get("action") == "activate"
                and value.get("source") == pool.ref
            },
        )
        before = {
            seat: engine.state.players[seat].mana_pool["G"]
            for seat in "ABCD"
        }
        result = session.act("pilot:B", {"action_id": action["id"]})
        self.assertTrue(result.ok, result.summary)
        self.assertTrue(pool.tapped)
        self.assertEqual(before["B"] + 1, engine.state.players["B"].mana_pool["G"])
        for seat in "ACD":
            self.assertEqual(before[seat], engine.state.players[seat].mana_pool["G"])
        self.assertEqual([], engine.state.stack)

        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / "intrinsic-basic-land-mana"
            session.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)

    def test_murmuring_bosk_intrinsic_mana_executes_with_closed_reveal_entry(
        self,
    ):
        capabilities = load_default_capability_registry()
        record = self.db.lookup("Murmuring Bosk", fuzzy=False)
        compiled = compile_oracle_card(
            record,
            capability_registry=capabilities,
            capability_profile="commander_review",
        )
        self.assertEqual("exact", compiled.status, compiled.material_residuals)
        self.assertFalse(compiled.material_residuals)
        self.assertIn(
            "zone.entry.reveal_hand_or_tapped",
            {
                dependency
                for face in compiled.faces
                for node in face.nodes
                for dependency in node.capability_dependencies
            },
        )
        self.assertTrue(
            any(
                node.template_id
                == "intrinsic-basic-land-mana-reminder-v1"
                and node.capability_dependencies
                == ("mana.intrinsic.basic_land_type",)
                for node in compiled.faces[0].nodes
            )
        )

        session = make_session(
            self.db,
            self.mishra,
            self.zimone,
            players=4,
            seed=60507,
        )
        keep_all(session)
        engine = session.engine
        bosk = CardInstance(
            object_id="fixture:murmuring-bosk",
            ref="B-murmuring-bosk",
            oracle_id=record.oracle_id,
            printed_name=record.name,
            owner="B",
            controller="B",
            zone="hand",
            zone_timestamp=engine._next_zone_timestamp(),
            known_to=list(engine.seats),
            revealed_to=list(engine.seats),
        )
        engine.state.cards[bosk.object_id] = bosk
        engine.state.players["B"].zones["hand"].append(bosk.object_id)
        register_generated_programs(
            self.db,
            engine.semantics,
            (record,),
            capability_registry=capabilities,
            capability_profile="commander_review",
            promote_exact_runtime_handlers=True,
            promote_exact_trigger_programs=True,
            promote_exact_effect_programs=True,
            promote_exact_capability_declarations=True,
        )
        engine.move_card(bosk.object_id, "battlefield", controller="B", log=False)
        self.assertTrue(bosk.tapped)
        engine.apply_effect({"op": "untap", "card": bosk.ref}, actor="B")
        self.prepare_main(engine, "B")
        engine.permissions.invalidate_current()
        engine._grant_priority("B")
        engine._issue_priority("B")
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()

        packet = session.packet("pilot:B", full=True)
        action = next(
            value
            for value in packet["decision"]["legal_actions"]
            if value.get("action") == "activate"
            and value.get("source") == bosk.ref
            and value.get("ability") == "intrinsic_forest"
        )
        before = engine.state.players["B"].mana_pool["G"]
        result = session.act("pilot:B", {"action_id": action["id"]})
        self.assertTrue(result.ok, result.summary)
        self.assertTrue(bosk.tapped)
        self.assertEqual(before + 1, engine.state.players["B"].mana_pool["G"])
        self.assertEqual([], engine.state.stack)

        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / "murmuring-bosk-intrinsic-mana"
            session.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)

    def test_mana_ability_can_activate_during_spell_payment(self):
        engine = self.make_engine(60503)
        island = self.card(engine, "A", "Island")
        ring = self.card(engine, "A", "Sol Ring")
        engine.move_card(
            island.object_id,
            "battlefield",
            controller="A",
            log=False,
        )
        engine.move_card(ring.object_id, "hand", log=False)
        self.prepare_main(engine)

        engine._cast("A", {"card": ring.ref, "pay": "auto"})

        self.assertTrue(island.tapped)
        self.assertEqual(1, len(engine.state.stack))
        self.assertEqual(ring.object_id, engine.state.stack[0].card_object_id)
        self.assertEqual(
            0,
            sum(engine.state.players["A"].mana_pool.values()),
        )

    def test_treasure_exposes_color_choice_and_auto_payment_sacrifices_it(self):
        engine = self.make_engine(60506)
        treasure_ref = engine.create_token(
            "A",
            name="Treasure",
            characteristics={
                "type_line": "Token Artifact — Treasure",
                "oracle_text": (
                    "{T}, Sacrifice this token: Add one mana of any color."
                ),
                "activated_ability_profile": "tap_sac_any_color_mana_v1",
            },
        )[0]
        treasure = next(
            card
            for card in engine.state.cards.values()
            if card.ref == treasure_ref
        )
        ring = self.card(engine, "A", "Sol Ring")
        engine.move_card(ring.object_id, "hand", log=False)
        self.prepare_main(engine)
        engine.pump()

        action = next(
            action
            for action in engine.state.pending_decision.payload_by_actor[
                "A"
            ]["legal"]["actions"]
            if action.get("source") == treasure.ref
        )

        self.assertTrue(action["mana_ability"])
        self.assertEqual(
            [{color: 1} for color in "WUBRG"],
            [
                option["value"]
                for option in action["choice_schema"]["mana_output"][
                    "options"
                ]
            ],
        )
        opportunity = engine.state.action_opportunities[-1]
        self.assertTrue(opportunity["pilot_task_issued"])
        self.assertIn(
            f"cast:{ring.ref}", opportunity["meaningful_action_ids"]
        )
        self.assertEqual(
            0,
            engine.state.players["A"].stats[
                "decision_optimization"
            ]["suppressed_meaningful_windows"],
        )

        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine.state.priority_player = "A"

        engine._cast("A", {"card": ring.ref, "pay": "auto"})

        self.assertEqual("outside", treasure.zone)
        self.assertEqual("stack", ring.zone)
        self.assertEqual(
            0,
            sum(engine.state.players["A"].mana_pool.values()),
        )

    def test_recordless_mana_plan_ignores_submitted_side_effects(self):
        engine = self.make_engine(60507)
        treasure_ref = engine.create_token(
            "A",
            name="Treasure",
            characteristics={
                "type_line": "Token Artifact — Treasure",
                "oracle_text": (
                    "{T}, Sacrifice this token: Add one mana of any color."
                ),
                "activated_ability_profile": "tap_sac_any_color_mana_v1",
            },
        )[0]
        treasure = next(
            card
            for card in engine.state.cards.values()
            if card.ref == treasure_ref
        )
        ring = self.card(engine, "A", "Sol Ring")
        engine.move_card(ring.object_id, "hand", log=False)
        self.prepare_main(engine)
        starting_life = engine.state.players["A"].life

        engine._cast(
            "A",
            {
                "card": ring.ref,
                "pay": "manual",
                "mana": [
                    {
                        "source": treasure.ref,
                        "bundle": {"U": 1},
                        "side_effects": [
                            {"op": "pay_life", "amount": starting_life}
                        ],
                    }
                ],
                "payment": {"U": 1},
            },
        )

        self.assertEqual(starting_life, engine.state.players["A"].life)
        self.assertEqual("outside", treasure.zone)
        self.assertEqual("stack", ring.zone)

    def test_mana_producing_spell_is_not_a_mana_ability(self):
        engine = self.make_engine(60504)
        drain = self.card(engine, "A", "Mana Drain")
        target_card = self.card(engine, "B", "Sol Ring")
        engine._remove_from_zone(target_card)
        engine._reset_zone_change(target_card, "stack")
        target_card.zone = "stack"
        target_card.controller = "B"
        target = StackItem(
            stack_id="S-mana-drain-target",
            ref="S-mana-drain-target",
            kind="spell",
            controller="B",
            label="Sol Ring",
            card_object_id=target_card.object_id,
            default_destination="battlefield",
            visibility=list(engine.seats),
        )
        engine.state.stack.append(target)
        engine.move_card(drain.object_id, "hand", log=False)
        engine.state.players["A"].mana_pool["U"] = 2
        engine.state.active_player = "B"
        engine.state.phase = "precombat_main"
        engine.state.step = "main"
        engine.state.priority_player = "A"
        engine.state.priority_passes = []

        engine._cast(
            "A",
            {"card": drain.ref, "targets": [target.ref]},
        )

        self.assertEqual(2, len(engine.state.stack))
        self.assertEqual("spell", engine.state.stack[-1].kind)
        self.assertEqual(
            drain.object_id,
            engine.state.stack[-1].card_object_id,
        )
        self.assertEqual(
            [],
            [
                ability
                for ability in engine._activated_abilities(drain)
                if ability.mana_ability
            ],
        )

    def test_pure_manual_mana_activation_can_be_undone_in_same_window(self):
        engine = self.make_engine(60508)
        island = self.card(engine, "A", "Island")
        engine.move_card(
            island.object_id,
            "battlefield",
            controller="A",
            log=False,
        )
        self.prepare_main(engine)
        ability = engine._activated_abilities(island)[0]

        engine._activate(
            "A", {"source": island.ref, "ability": ability.ability_id}
        )

        self.assertTrue(island.tapped)
        self.assertEqual(1, engine.state.players["A"].mana_pool["U"])
        undo = next(
            action
            for action in engine._priority_action_hints("A")["actions"]
            if action["action"] == "undo_mana"
        )
        self.assertEqual(island.ref, undo["source"])

        undo_mana_activation(engine, "A", {"source": island.ref})

        self.assertFalse(island.tapped)
        self.assertEqual(0, engine.state.players["A"].mana_pool["U"])
        self.assertIsNone(available_mana_undo(engine.state, "A"))

    def test_passing_priority_closes_manual_mana_rollback_window(self):
        engine = self.make_engine(60509)
        island = self.card(engine, "A", "Island")
        engine.move_card(
            island.object_id,
            "battlefield",
            controller="A",
            log=False,
        )
        self.prepare_main(engine)
        ability = engine._activated_abilities(island)[0]
        engine._activate(
            "A", {"source": island.ref, "ability": ability.ability_id}
        )
        self.assertIsNotNone(available_mana_undo(engine.state, "A"))

        engine._complete_priority(
            SimpleNamespace(
                actors=["A"],
                responses={"A": {"action": "pass"}},
            )
        )

        self.assertIsNone(available_mana_undo(engine.state, "A"))
        self.assertTrue(island.tapped)
        self.assertEqual(1, engine.state.players["A"].mana_pool["U"])

    def test_mana_activation_with_side_effect_is_not_reversible(self):
        engine = self.make_engine(60510)
        elves = self.card(engine, "B", "Elves of Deep Shadow")
        engine.move_card(
            elves.object_id,
            "battlefield",
            controller="B",
            log=False,
        )
        engine.state.players["B"].turns_begun = (
            elves.acquired_control_turn_count + 1
        )
        self.prepare_main(engine, "B")
        ability = next(
            ability
            for ability in engine._activated_abilities(elves)
            if "Add {B}" in ability.effect_text
        )
        engine._activate(
            "B", {"source": elves.ref, "ability": ability.ability_id}
        )
        self.assertIsNone(available_mana_undo(engine.state, "B"))


if __name__ == "__main__":
    unittest.main()
