from __future__ import annotations

import unittest

from common import keep_all, load_assets
from quorune import CommanderSession, GameConfig
from quorune.engine import GameRuleError
from quorune.model import CardInstance, StackItem
from quorune.oracle_ir import register_generated_programs
from quorune.preflight import (
    _card_source_hashes,
    card_semantic_status,
    semantic_preflight,
)
from quorune.record import pause_reason_for_state
from quorune.report import derive_review
from quorune.rules.capabilities import load_default_capability_registry
from quorune.semantics import SemanticProgram, SemanticRegistry


class SemanticPreflightV2Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db, cls.mishra, cls.zimone = load_assets()

    @classmethod
    def tearDownClass(cls):
        cls.db.close()

    def test_preflight_v2_records_exact_sources_and_material_fields(self):
        report = semantic_preflight(self.db, self.zimone)
        self.assertEqual(3, report["schema_version"])
        self.assertEqual("commander_review", report["capability_profile"])
        self.assertEqual(
            64, len(report["capability_evidence_fingerprint"])
        )
        self.assertIn("match_trust_closure", report)
        self.assertFalse(report["trusted_only_ready"])
        self.assertTrue(report["compatibility_ready"])
        self.assertEqual(
            report["deck_fingerprint"],
            report["deck_list_fingerprint"],
        )
        self.assertIsNotNone(report["deck_source_fingerprint"])
        by_name = {row["name"]: row for row in report["cards"]}
        offer = by_name["An Offer You Can't Refuse"]
        self.assertEqual("front", offer["active_face"])
        self.assertIn("stack", offer["zones"])
        self.assertEqual("trusted_card", offer["support_kind"])
        self.assertTrue(offer["source_hash_match"])
        self.assertEqual(64, len(offer["oracle_hash"]))
        self.assertEqual(64, len(offer["rulings_hash"]))
        self.assertTrue(offer["scenario_tests"])
        self.assertIn(
            "spell_effect", offer["material_effect_categories"]
        )

    def test_trusted_program_is_invalidated_by_source_hash_drift(self):
        registry = SemanticRegistry()
        record = self.db.lookup("An Offer You Can't Refuse")
        program = registry.get(f"{record.oracle_id}:spell:front")
        self.assertIsNotNone(program)
        program.provenance["source_oracle_hash"] = "0" * 64

        row = card_semantic_status(
            record,
            registry,
            db=self.db,
        )

        self.assertFalse(row["source_hash_match"])
        self.assertEqual("unresolved", row["status"])
        self.assertIn("semantic_source_hash_drift", row["unresolved"])

    def test_rulings_hash_is_independent_of_database_import_order(self):
        record = self.db.lookup("An Offer You Can't Refuse")
        rulings = self.db.rulings(record)

        class OrderedRulings:
            def __init__(self, values):
                self.values = values

            def rulings(self, _record):
                return list(self.values)

        forward = _card_source_hashes(OrderedRulings(rulings), record)
        reverse = _card_source_hashes(
            OrderedRulings(reversed(rulings)),
            record,
        )
        self.assertEqual(forward, reverse)

    def test_reminder_text_does_not_invent_source_mana_ability(self):
        row = card_semantic_status(
            self.db.lookup("An Offer You Can't Refuse"),
            SemanticRegistry(),
            db=self.db,
        )

        self.assertNotIn(
            "mana_ability", row["material_effect_categories"]
        )
        self.assertNotIn("mana_ability", row["unresolved"])

    def test_mixed_keyword_and_static_card_is_not_silently_complete(self):
        row = card_semantic_status(
            self.db.lookup("Roaming Throne"),
            SemanticRegistry(include_builtin_packs=False),
            db=self.db,
        )

        self.assertIn(
            "combat_or_protection_keyword",
            row["material_effect_categories"],
        )
        self.assertIn("static_ability", row["material_effect_categories"])
        self.assertIn("keyword:ward", row["unresolved"])
        self.assertEqual("unresolved", row["status"])

    def test_each_nonmana_activated_ability_needs_exact_coverage(self):
        row = card_semantic_status(
            self.db.lookup("Deathrite Shaman"),
            SemanticRegistry(include_builtin_packs=False),
            db=self.db,
        )

        self.assertEqual(
            {
                "activated_ability:ab1",
                "activated_ability:ab2",
                "activated_ability:ab3",
            },
            {
                value
                for value in row["unresolved"]
                if value.startswith("activated_ability:")
            },
        )

    def test_unrestricted_generic_mana_ability_remains_trusted_builtin(self):
        row = card_semantic_status(
            self.db.lookup("Sol Ring"),
            SemanticRegistry(),
            db=self.db,
        )

        self.assertEqual("fully_playable", row["status"])
        self.assertNotIn("mana_ability", row["unresolved"])

    def test_exact_equip_keyword_is_recognized_as_supported(self):
        row = card_semantic_status(
            self.db.lookup("Lightning Greaves"),
            SemanticRegistry(),
            db=self.db,
        )

        self.assertIn("keyword_ability", row["material_effect_categories"])
        self.assertNotIn("keyword:equip", row["unresolved"])
        self.assertEqual("fully_playable", row["status"])

    def test_damage_result_keywords_are_recognized_by_preflight(self):
        for name, keyword in (
            ("Phyrexian Crusader", "infect"),
            ("Boggart Ram-Gang", "wither"),
            ("Healer's Hawk", "lifelink"),
            ("Crawling Chorus", "toxic"),
        ):
            with self.subTest(name=name):
                row = card_semantic_status(
                    self.db.lookup(name),
                    SemanticRegistry(),
                    db=self.db,
                )
                self.assertNotIn(f"keyword:{keyword}", row["unresolved"])


class TrustedOnlyPolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db, cls.mishra, cls.zimone = load_assets()

    @classmethod
    def tearDownClass(cls):
        cls.db.close()

    def make_session(self, seed: int) -> CommanderSession:
        session = CommanderSession.create(
            self.db,
            {"A": self.mishra, "B": self.zimone},
            first_player="A",
            seed=seed,
            config=GameConfig(
                seed=seed,
                profile="commander_duel",
                semantic_policy="trusted_only",
                auto_pass_empty_priority=False,
            ),
        )
        keep_all(session)
        session.engine.permissions.invalidate_current()
        session.engine.state.pending_decision = None
        return session

    @staticmethod
    def card(engine, name: str, owner: str):
        return next(
            card
            for card in engine.state.cards.values()
            if card.owner == owner and card.printed_name == name
        )

    def test_provisional_cast_is_not_advertised(self):
        session = self.make_session(801)
        engine = session.engine
        intent = self.card(engine, "Diabolic Intent", "B")
        engine.semantics.get(
            f"{intent.oracle_id}:spell:front"
        ).trust_level = "provisional"
        fodder = self.card(engine, "Birds of Paradise", "B")
        engine.move_card(intent.object_id, "hand")
        engine.move_card(
            fodder.object_id,
            "battlefield",
            controller="B",
        )
        engine.state.players["B"].mana_pool.update({"C": 1, "B": 1})
        engine.state.active_player = "B"
        engine.state.phase = "precombat_main"
        engine.state.step = "main"
        engine.state.priority_player = "B"

        hints = engine._priority_action_hints("B")

        self.assertFalse(
            any(
                action.get("card") == intent.ref
                for action in hints["actions"]
            )
        )
        diagnostic = hints["diagnostic"]["unresolved_cost_semantics"]
        self.assertTrue(
            any(
                row.get("card") == intent.ref
                and row.get("reason")
                == "semantic_policy_requires_trusted"
                for row in diagnostic
            )
        )

    def test_untrusted_resolution_pauses_without_arbiter(self):
        session = self.make_session(802)
        engine = session.engine
        intent = self.card(engine, "Diabolic Intent", "B")
        program_key = f"{intent.oracle_id}:spell:front"
        engine.semantics.get(program_key).trust_level = "provisional"
        engine._remove_from_zone(intent)
        intent.zone = "stack"
        item = StackItem(
            stack_id="trusted-only-test",
            ref="S-untrusted",
            kind="spell",
            controller="B",
            label=intent.printed_name,
            card_object_id=intent.object_id,
            semantic_key=program_key,
            default_destination="graveyard",
            visibility=list(engine.seats),
        )
        engine.state.stack.append(item)
        engine.state.priority_player = None

        engine._prepare_stack_resolution()

        self.assertIsNone(engine.state.pending_decision)
        pause = pause_reason_for_state(engine.state)
        self.assertEqual("semantic_unsupported", pause["kind"])
        self.assertEqual("provisional", pause["trust_level"])
        self.assertTrue(
            any(
                event.code == "fidelity.semantic_unsupported"
                for event in engine.state.events
            )
        )

    def test_trusted_generic_mana_permanent_resolves(self):
        session = self.make_session(803)
        engine = session.engine
        ring = self.card(engine, "Sol Ring", "A")
        engine._remove_from_zone(ring)
        ring.zone = "stack"
        item = StackItem(
            stack_id="trusted-generic-test",
            ref="S-generic",
            kind="spell",
            controller="A",
            label=ring.printed_name,
            card_object_id=ring.object_id,
            semantic_key=f"{ring.oracle_id}:spell:front",
            default_destination="battlefield",
            visibility=list(engine.seats),
        )
        engine.state.stack.append(item)
        engine.state.priority_player = None

        engine._prepare_stack_resolution()

        self.assertEqual("battlefield", ring.zone)
        self.assertIsNone(pause_reason_for_state(engine.state))

    def test_untrusted_permanent_program_does_not_auto_resolve_from_prose(self):
        session = self.make_session(805)
        engine = session.engine
        engine.state.config.semantic_policy = "arbitrate_or_pause"
        ring = self.card(engine, "Sol Ring", "A")
        semantic_key = f"{ring.oracle_id}:spell:front"
        engine.semantics.put(
            SemanticProgram(
                key=semantic_key,
                label=ring.printed_name,
                destination="battlefield",
                requires_arbiter=True,
                oracle_id=ring.oracle_id,
                ability_id="test:preflight-semantic-key",
            )
        )
        engine._remove_from_zone(ring)
        ring.zone = "stack"
        item = StackItem(
            stack_id="untrusted-permanent-test",
            ref="S-untrusted-permanent",
            kind="spell",
            controller="A",
            label=ring.printed_name,
            card_object_id=ring.object_id,
            semantic_key=semantic_key,
            default_destination="battlefield",
            visibility=list(engine.seats),
        )
        engine.state.stack.append(item)
        engine.state.priority_player = None

        engine._prepare_stack_resolution()

        self.assertEqual("stack", ring.zone)
        self.assertEqual("arbiter.resolve", engine.state.pending_decision.kind)

    def test_pilot_cannot_supply_semantic_key(self):
        session = self.make_session(804)
        engine = session.engine
        ring = self.card(engine, "Sol Ring", "A")
        engine.move_card(ring.object_id, "hand")
        engine.state.active_player = "A"
        engine.state.phase = "precombat_main"
        engine.state.step = "main"
        engine.state.priority_player = "A"
        with self.assertRaises(GameRuleError):
            engine._cast(
                "A",
                {
                    "card": ring.ref,
                    "semantic_key": "attacker:chosen",
                },
            )

    def test_duplicated_pod_can_only_be_deck_operation_evidence(self):
        session = CommanderSession.create(
            self.db,
            {
                "A": self.mishra,
                "B": self.zimone,
                "C": self.mishra,
                "D": self.zimone,
            },
            first_player="A",
            seed=805,
            config=GameConfig(
                seed=805,
                profile="commander_multiplayer",
                semantic_policy="trusted_only",
            ),
        )
        session.engine.permissions.invalidate_current()
        session.state.pending_decision = None
        session.state.game_over = True
        session.state.winner = "A"
        session.state.started = True
        session.state.action_opportunities = []
        decisions = []
        for index, seat in enumerate("ABCD", start=1):
            decision_id = f"D{index}"
            session.state.action_opportunities.append(
                {
                    "decision_id": decision_id,
                    "turn_sequence": 1,
                    "seat": seat,
                    "active_player": "A",
                    "phase": "precombat_main",
                    "step": "main",
                    "meaningful_actions_exist": True,
                    "meaningful_action_ids": ["pass"],
                    "outcome": "pilot_task_issued",
                    "incorrectly_suppressed": False,
                }
            )
            decisions.append(
                {
                    "decision_id": decision_id,
                    "accepted": True,
                    "role": "pilot",
                    "principal": f"pilot:{seat}",
                    "seat": seat,
                    "action": "pass",
                    "action_id": "pass",
                    "reason": "No stronger action is available.",
                    "legal_alternatives": [{"id": "pass"}],
                }
            )
        manifest = {
            "replay": {"verification": "pass"},
            "profile_fingerprint_match": True,
            "players": [
                {"deck_list_fingerprint": "mishra"},
                {"deck_list_fingerprint": "zimone"},
                {"deck_list_fingerprint": "mishra"},
                {"deck_list_fingerprint": "zimone"},
            ],
            "codex_arena": {
                "pilot_thread_count": 4,
                "persistent_thread_reuse": True,
                "primary_made_strategic_decision": False,
                "seat_projection_verified": True,
                "provider_identity_verified": True,
                "model_identity_verified": True,
                "codex_subagent_run": True,
                "stop_reason": {"kind": "winner"},
            },
        }

        review = derive_review(
            session.engine,
            decisions=decisions,
            manifest=manifest,
        )

        self.assertTrue(
            review["fidelity"]["deck_operation_evidence"]
        )
        self.assertEqual(
            "deck_operation_evidence",
            review["fidelity"]["classification"],
        )
        self.assertFalse(review["fidelity"]["matchup_evidence"])


class NormalizedZoneEventTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db, cls.mishra, cls.zimone = load_assets()

    @classmethod
    def tearDownClass(cls):
        cls.db.close()

    def make_session(self, seed: int) -> CommanderSession:
        session = CommanderSession.create(
            self.db,
            {"A": self.zimone, "B": self.mishra},
            first_player="A",
            seed=seed,
            config=GameConfig(
                seed=seed,
                profile="commander_duel",
                semantic_policy="arbitrate_or_pause",
            ),
        )
        session.engine.permissions.invalidate_current()
        session.state.pending_decision = None
        return session

    @staticmethod
    def card(engine, name: str, owner: str):
        return next(
            card
            for card in engine.state.cards.values()
            if card.owner == owner and card.printed_name == name
        )

    def test_graveyard_source_observes_land_enter(self):
        session = self.make_session(806)
        engine = session.engine
        bloodghast = self.card(engine, "Elves of Deep Shadow", "A")
        land = self.card(engine, "Island", "A")
        engine.move_card(bloodghast.object_id, "graveyard")
        engine.semantics.put(
            SemanticProgram(
                key=f"{bloodghast.oracle_id}:test:landfall",
                label="Bloodghast test landfall",
                oracle_id=bloodghast.oracle_id,
                ability_id="test:landfall",
                active_zone="graveyard",
                event="land.enter",
                effects=[],
            )
        )

        engine.move_card(
            land.object_id,
            "battlefield",
            controller="A",
            semantic_events=True,
            reason="zone-event test",
        )
        self.assertFalse(engine._stabilize())

        self.assertTrue(
            any(
                item.label == "Bloodghast test landfall"
                for item in engine.state.stack
            )
        )

    def test_self_dies_trigger_uses_last_known_battlefield_zone(self):
        session = self.make_session(807)
        engine = session.engine
        hulk = self.card(engine, "Elves of Deep Shadow", "A")
        engine.move_card(
            hulk.object_id,
            "battlefield",
            controller="A",
        )
        engine.semantics.put(
            SemanticProgram(
                key=f"{hulk.oracle_id}:test:dies",
                label="Self dies test",
                oracle_id=hulk.oracle_id,
                ability_id="test:dies",
                active_zone="battlefield",
                event="creature.dies.self",
                effects=[],
            )
        )

        engine.apply_effect(
            {"op": "sacrifice", "card": hulk.ref},
            actor="A",
        )
        self.assertFalse(engine._stabilize())

        self.assertEqual("graveyard", hulk.zone)
        self.assertTrue(
            any(
                item.label == "Self dies test"
                for item in engine.state.stack
            )
        )

    def test_declarative_event_condition_matches_normalized_context(self):
        session = self.make_session(8071)
        engine = session.engine
        source = self.card(engine, "Elves of Deep Shadow", "A")
        engine.move_card(
            source.object_id,
            "battlefield",
            controller="A",
        )
        engine.semantics.put(
            SemanticProgram(
                key=f"{source.oracle_id}:test:upkeep",
                label="Conditional upkeep test",
                oracle_id=source.oracle_id,
                ability_id="test:upkeep",
                active_zone="battlefield",
                event="step.begin",
                event_condition={
                    "all": [
                        {
                            "field": "step",
                            "op": "eq",
                            "value": "upkeep",
                        },
                        {
                            "field": "player",
                            "op": "eq",
                            "value": "$source.controller",
                        },
                    ]
                },
                effects=[],
            )
        )

        engine._dispatch_semantic_event(
            "step.begin",
            {"phase": "combat", "step": "beginning_combat", "player": "A"},
        )
        engine._dispatch_semantic_event(
            "step.begin",
            {"phase": "beginning", "step": "upkeep", "player": "B"},
        )
        self.assertFalse(engine._stabilize())
        self.assertFalse(engine.state.stack)

        engine._dispatch_semantic_event(
            "step.begin",
            {"phase": "beginning", "step": "upkeep", "player": "A"},
        )
        self.assertFalse(engine._stabilize())
        self.assertEqual(
            ["Conditional upkeep test"],
            [item.label for item in engine.state.stack],
        )

    def test_simultaneous_semantic_triggers_use_apnap_and_owner_order(self):
        session = self.make_session(8072)
        engine = session.engine
        source_a = self.card(engine, "Elves of Deep Shadow", "A")
        source_b = self.card(engine, "Sensei's Divining Top", "B")
        for source in (source_a, source_b):
            engine.move_card(
                source.object_id,
                "battlefield",
                controller=source.owner,
            )
        for suffix, label in (("one", "A trigger one"), ("two", "A trigger two")):
            engine.semantics.put(
                SemanticProgram(
                    key=f"{source_a.oracle_id}:test:{suffix}",
                    label=label,
                    oracle_id=source_a.oracle_id,
                    ability_id=f"test:{suffix}",
                    active_zone="battlefield",
                    event="test.simultaneous",
                    effects=[],
                )
            )
        engine.semantics.put(
            SemanticProgram(
                key=f"{source_b.oracle_id}:test:three",
                label="B trigger",
                oracle_id=source_b.oracle_id,
                ability_id="test:three",
                active_zone="battlefield",
                event="test.simultaneous",
                effects=[],
            )
        )
        engine.state.active_player = "A"
        engine.state.config.auto_pass_empty_priority = False

        engine._dispatch_semantic_event(
            "test.simultaneous",
            {},
            sources=[source_a, source_b],
        )
        self.assertTrue(engine._stabilize())
        packet = session.packet("pilot:A", full=True)
        self.assertEqual("trigger.order", packet["decision"]["kind"])
        by_label = {
            item["label"]: item["id"]
            for item in packet["decision"]["ctx"]["triggers"]
        }
        result = session.act(
            "pilot:A",
            {
                "action_id": "order",
                "triggers": [
                    by_label["A trigger two"],
                    by_label["A trigger one"],
                ],
                "plan": "DEVELOP_ENGINE",
                "reason": (
                    "Choose A's bottom-to-top order before the nonactive "
                    "player's trigger is placed."
                ),
            },
        )
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(
            ["A trigger two", "A trigger one", "B trigger"],
            [item.label for item in engine.state.stack],
        )

    def test_hybrid_cast_cost_exposes_only_payable_variants(self):
        session = self.make_session(808)
        engine = session.engine
        shaman = self.card(engine, "Deathrite Shaman", "A")
        engine.move_card(shaman.object_id, "hand")
        engine.state.active_player = "A"
        engine.state.phase = "precombat_main"
        engine.state.step = "main"
        engine.state.priority_player = "A"
        engine.state.players["A"].mana_pool["G"] = 1

        action = next(
            action
            for action in engine._priority_action_hints("A")["actions"]
            if action.get("card") == shaman.ref
        )

        self.assertEqual(1, len(action["cost_options"]))
        self.assertEqual(
            1,
            action["cost_options"][0]["requirements"]["G"],
        )
        engine._cast("A", {"card": shaman.ref})
        self.assertEqual("stack", shaman.zone)
        self.assertEqual(0, engine.state.players["A"].mana_pool["G"])

    def test_sacrifice_additional_cost_is_required_and_paid(self):
        session = self.make_session(809)
        engine = session.engine
        intent = self.card(engine, "Diabolic Intent", "A")
        engine.move_card(intent.object_id, "hand")
        program = engine.semantics.get(
            f"{intent.oracle_id}:spell:front"
        )
        self.assertIsNotNone(program)
        program.cost_schema = {
            "additional_costs": [
                {
                    "kind": "sacrifice",
                    "count": 1,
                    "types_any": ["creature"],
                }
            ]
        }
        engine.state.active_player = "A"
        engine.state.phase = "precombat_main"
        engine.state.step = "main"
        engine.state.priority_player = "A"
        engine.state.players["A"].mana_pool.update({"B": 1, "C": 1})

        self.assertFalse(
            any(
                action.get("card") == intent.ref
                for action in engine._priority_action_hints("A")["actions"]
            )
        )

        fodder_ref = engine.create_token(
            "A",
            name="Fodder",
            characteristics={
                "type_line": "Token Creature",
                "power": "1",
                "toughness": "1",
            },
        )[0]
        action = next(
            action
            for action in engine._priority_action_hints("A")["actions"]
            if action.get("card") == intent.ref
        )
        self.assertIn(
            fodder_ref,
            action["cost_options"][0]["choice_schema"][
                "sacrifice_cards"
            ]["legal_refs"],
        )

        engine._cast(
            "A",
            {
                "card": intent.ref,
                "sacrifice_cards": [fodder_ref],
            },
        )

        fodder = next(
            card for card in engine.state.cards.values()
            if card.ref == fodder_ref
        )
        self.assertEqual("outside", fodder.zone)
        self.assertEqual("stack", intent.zone)
        cast_event = next(
            event
            for event in reversed(engine.state.events)
            if event.code == "stack.cast"
        )
        self.assertIn(
            fodder_ref,
            cast_event.details.get(
                "additional_cost_objects", []
            ),
        )

    def test_convoke_pays_colored_x_cost_without_using_mana_sources(self):
        session = self.make_session(810)
        engine = session.engine
        chord = self.card(engine, "Chord of Calling", "A")
        engine.move_card(chord.object_id, "hand")
        creatures = [
            engine.create_token(
                "A",
                name=f"Green Fodder {index}",
                characteristics={
                    "type_line": "Token Creature",
                    "colors": ["G"],
                    "power": "1",
                    "toughness": "1",
                },
            )[0]
            for index in range(3)
        ]
        engine.state.active_player = "A"
        engine.state.phase = "precombat_main"
        engine.state.step = "main"
        engine.state.priority_player = "A"

        action = next(
            action
            for action in engine._priority_action_hints("A")["actions"]
            if action.get("card") == chord.ref
        )
        option = action["cost_options"][0]
        self.assertEqual(0, sum(option["requirements"].values()))
        self.assertEqual(
            set(creatures),
            set(
                option["choice_schema"]["convoke_cards"][
                    "legal_refs"
                ]
            ),
        )

        engine._cast(
            "A",
            {
                "card": chord.ref,
                "x": 0,
                "convoke_cards": creatures,
            },
        )

        self.assertEqual("stack", chord.zone)
        self.assertTrue(
            all(
                next(
                    card
                    for card in engine.state.cards.values()
                    if card.ref == ref
                ).tapped
                for ref in creatures
            )
        )

    def test_improvise_only_reduces_generic_mana(self):
        session = self.make_session(811)
        engine = session.engine
        whir = self.card(engine, "Whir of Invention", "B")
        engine.move_card(whir.object_id, "hand")
        artifacts = [
            engine.create_token(
                "B",
                name=f"Improvise Artifact {index}",
                characteristics={"type_line": "Token Artifact"},
            )[0]
            for index in range(2)
        ]
        engine.state.active_player = "B"
        engine.state.phase = "precombat_main"
        engine.state.step = "main"
        engine.state.priority_player = "B"
        engine.state.players["B"].mana_pool["U"] = 3

        engine._cast(
            "B",
            {
                "card": whir.ref,
                "x": 2,
                "improvise_cards": artifacts,
            },
        )

        self.assertEqual("stack", whir.zone)
        self.assertEqual(0, engine.state.players["B"].mana_pool["U"])
        self.assertTrue(
            all(
                next(
                    card
                    for card in engine.state.cards.values()
                    if card.ref == ref
                ).tapped
                for ref in artifacts
            )
        )

    def test_affinity_reduction_is_derived_from_public_artifacts(self):
        session = self.make_session(812)
        engine = session.engine
        record = self.db.lookup("Myr Enforcer")
        register_generated_programs(
            self.db,
            engine.semantics,
            (record,),
            trust_level="provisional",
            capability_registry=load_default_capability_registry(),
            capability_profile=engine.state.config.review_profile,
            promote_exact_runtime_handlers=True,
            promote_exact_capability_declarations=True,
        )
        enforcer = CardInstance(
            object_id="fixture:preflight-myr-enforcer",
            ref="B-preflight-affinity",
            oracle_id=record.oracle_id,
            printed_name=record.name,
            owner="B",
            controller="B",
            zone="hand",
            zone_timestamp=engine.state.event_sequence + 1,
            known_to=["B"],
        )
        engine.state.cards[enforcer.object_id] = enforcer
        engine.state.players["B"].zones["hand"].append(enforcer.object_id)
        for index in range(7):
            engine.create_token(
                "B",
                name=f"Affinity Artifact {index}",
                characteristics={"type_line": "Token Artifact"},
            )
        engine.state.active_player = "B"
        engine.state.phase = "precombat_main"
        engine.state.step = "main"
        engine.state.priority_player = "B"

        action = next(
            action
            for action in engine._priority_action_hints("B")["actions"]
            if action.get("card") == enforcer.ref
        )
        self.assertEqual(
            0,
            action["cost_options"][0]["requirements"]["GENERIC"],
        )
        engine._cast("B", {"card": enforcer.ref})
        self.assertEqual("stack", enforcer.zone)


if __name__ == "__main__":
    unittest.main()
