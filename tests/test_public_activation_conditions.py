from __future__ import annotations

import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from common import ROOT, keep_all, make_session, pass_current
from quorune.abilities import (
    ActivationCondition,
    ActivationConditionKind,
    parse_activated_abilities,
)
from quorune.activation_condition_model import (
    ACTIVATION_PHASE_CONDITION_CAPABILITY,
    ACTIVATION_PUBLIC_QUERY_CAPABILITY,
)
from quorune.card_programs import compile_card_program
from quorune.carddb import CardDatabase, CardRecord
from quorune.continuous_effect_state import commit_continuous_effect
from quorune.continuous_effects import (
    ContinuousEffect,
    ContinuousEffectDuration,
    ContinuousEffectOrigin,
    ContinuousObjectIdentity,
    ContinuousOperation,
    Layer,
)
from quorune.deck import DeckLoader
from quorune.compiler.target_effect_corpus_assurance import (
    TargetEffectCorpusCollector,
)
from quorune.object_predicate import ObjectQuerySpec, PermanentStatePredicateSpec
from quorune.oracle_ir import compile_oracle_card
from quorune.projection import StateProjector
from quorune.record import authoritative_state_hash, checkpoint_envelope, replay_record
from quorune.rules.activation import activation_condition_status
from quorune.rules.activation.commit import commit_activation
from quorune.rules.activation.model import (
    ActivationProposalError,
    ActivationProposalRequest,
)
from quorune.rules.activation.proposal import (
    build_activation_offer,
    build_activation_proposal,
)
from quorune.rules.capabilities import CapabilityRegistry
from scripts.build_test_database import build_fixture_database


REGISTRY_PATH = ROOT / "quorune" / "rules" / "capability-registry.json"
FIXTURE_PATH = (
    ROOT / "tests" / "fixtures" / "public-activation-condition-cards.json"
)


class _NoRulingsDatabase:
    @staticmethod
    def rulings(record):
        del record
        return ()


def trusted_registry() -> CapabilityRegistry:
    registry = CapabilityRegistry.from_path(REGISTRY_PATH)
    registry.mark_evidence_verified("0" * 64)
    return registry


def condition_record(
    oracle_text: str,
    *,
    suffix: int,
    type_line: str = "Artifact",
) -> CardRecord:
    return CardRecord(
        oracle_id=f"17300000-0000-4000-8000-{suffix:012d}",
        name="Generic Activation Condition",
        mana_cost="{2}",
        mana_value=2.0,
        type_line=type_line,
        oracle_text=oracle_text,
        power="2" if "Creature" in type_line else None,
        toughness="2" if "Creature" in type_line else None,
        loyalty=None,
        defense=None,
        colors=(),
        color_identity=(),
        keywords=(),
        produced_mana=(),
        layout="normal",
        released_at="2026-09-05",
        legalities={"commander": "legal"},
        faces=(),
        raw={},
    )


def focused_database(directory: str) -> CardDatabase:
    database = Path(directory) / "public-activation-conditions.sqlite3"
    build_fixture_database(
        [
            ROOT / "tests" / "fixtures" / "scryfall-exact-lists.json",
            ROOT / "tests" / "fixtures" / "counter-replacement-cards.json",
            FIXTURE_PATH,
        ],
        database,
    )
    return CardDatabase(database)


class PublicActivationConditionCompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.registry = trusted_registry()
        cls.registry_value = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))

    def compile(self, text: str, *, suffix: int):
        record = condition_record(text, suffix=suffix)
        oracle = compile_oracle_card(
            record,
            capability_registry=self.registry,
            capability_profile="commander_review",
        )
        program = compile_card_program(
            _NoRulingsDatabase(),
            record,
            capability_registry=self.registry,
            capability_profile="commander_review",
            trust_level="trusted",
        )
        return oracle, program

    def test_public_activation_conditions_compile_across_effect_families(self):
        cases = (
            (
                "{1}, {T}: Draw a card. Activate only during your upkeep.",
                ACTIVATION_PHASE_CONDITION_CAPABILITY,
                ActivationConditionKind.CONTROLLERS_UPKEEP,
            ),
            (
                "{1}: Put a +1/+1 counter on this artifact. Activate only "
                "during your turn, before attackers are declared.",
                ACTIVATION_PHASE_CONDITION_CAPABILITY,
                ActivationConditionKind.CONTROLLERS_TURN_BEFORE_ATTACKERS,
            ),
            (
                "{1}: You gain 2 life. Activate only during your upkeep and "
                "only once each turn.",
                ACTIVATION_PHASE_CONDITION_CAPABILITY,
                ActivationConditionKind.CONTROLLERS_UPKEEP,
            ),
            (
                "{1}: Draw a card. Activate only if you have no cards in hand.",
                ACTIVATION_PUBLIC_QUERY_CAPABILITY,
                ActivationConditionKind.PUBLIC_QUERY_COUNT,
            ),
            (
                "{1}: Scry 2. Activate only if you have exactly seven cards "
                "in your hand.",
                ACTIVATION_PUBLIC_QUERY_CAPABILITY,
                ActivationConditionKind.PUBLIC_QUERY_COUNT,
            ),
            (
                "{1}: You gain 4 life. Activate only if there are seven or "
                "more cards in your graveyard.",
                ACTIVATION_PUBLIC_QUERY_CAPABILITY,
                ActivationConditionKind.PUBLIC_QUERY_COUNT,
            ),
            (
                "{1}: Draw a card. Activate only if there are two or more "
                "creature cards in your graveyard.",
                ACTIVATION_PUBLIC_QUERY_CAPABILITY,
                ActivationConditionKind.PUBLIC_QUERY_COUNT,
            ),
            (
                "{1}: Scry 1. Activate only if you control a legendary creature.",
                ACTIVATION_PUBLIC_QUERY_CAPABILITY,
                ActivationConditionKind.PUBLIC_QUERY_COUNT,
            ),
            (
                "{1}: Draw a card. Activate only if you control a creature "
                "with flying.",
                ACTIVATION_PUBLIC_QUERY_CAPABILITY,
                ActivationConditionKind.PUBLIC_QUERY_COUNT,
            ),
            (
                "{1}: You gain 2 life. Activate only if you control four or "
                "more snow permanents.",
                ACTIVATION_PUBLIC_QUERY_CAPABILITY,
                ActivationConditionKind.PUBLIC_QUERY_COUNT,
            ),
            (
                "{1}: Draw a card. Activate only if you control two or more "
                "black permanents.",
                ACTIVATION_PUBLIC_QUERY_CAPABILITY,
                ActivationConditionKind.PUBLIC_QUERY_COUNT,
            ),
            (
                "{1}: Scry 1. Activate only if you control a Gideon planeswalker.",
                ACTIVATION_PUBLIC_QUERY_CAPABILITY,
                ActivationConditionKind.PUBLIC_QUERY_COUNT,
            ),
        )
        for index, (text, capability, kind) in enumerate(cases):
            with self.subTest(text=text):
                ability = parse_activated_abilities(
                    card_name="Generic Activation Condition",
                    oracle_text=text,
                )[0]
                self.assertIs(kind, ability.activation_conditions[0].kind)
                oracle, program = self.compile(text, suffix=173_001_000 + index)
                self.assertEqual("exact", oracle.status, oracle.material_residuals)
                node = oracle.faces[0].nodes[0]
                self.assertIn(capability, node.capability_dependencies)
                self.assertEqual((), program.residuals)
                self.assertTrue(program.trust_closure["strict_capability_ready"])

    def test_public_activation_condition_grammar_and_schema_fail_closed(self):
        excluded = (
            "Activate only during any upkeep step.",
            "Activate only during the declare blockers step.",
            "Activate only during an opponent's upkeep.",
            "Activate only if this artifact entered this turn.",
            "Activate only if an opponent lost life this turn.",
            "Activate only if you control an attacking modified creature.",
            "Activate only if you control three creatures with different powers.",
            "Activate only if you control your commander.",
            "Activate only if an opponent has seven cards in their graveyard.",
            "Activate only if you control a creature named Grizzly Bears.",
            "Activate only if you control an artifact or enchantment.",
            "Activate only if you control a creature and have no cards in hand.",
        )
        for index, restriction in enumerate(excluded):
            with self.subTest(restriction=restriction):
                text = f"{{1}}: Draw a card. {restriction}"
                oracle = compile_oracle_card(
                    condition_record(
                        text,
                        suffix=173_002_000 + index,
                    ),
                    capability_registry=self.registry,
                    capability_profile="commander_review",
                )
                self.assertNotEqual("exact", oracle.status)
                self.assertTrue(oracle.material_residuals)

        valid = ActivationCondition(
            ActivationConditionKind.PUBLIC_QUERY_COUNT,
            minimum=1,
            query=ObjectQuerySpec(
                zones=("battlefield",),
                types_all=("creature",),
            ),
        ).to_dict()
        self.assertEqual(valid, ActivationCondition.from_dict(valid).to_dict())
        malformed = (
            {**valid, "unknown": True},
            {**valid, "minimum": -1},
            {**valid, "maximum": 0},
            {
                **valid,
                "query": {
                    **valid["query"],
                    "controller": "$opponent",
                },
            },
            {
                **valid,
                "query": ObjectQuerySpec(
                    zones=("hand",),
                    types_all=("creature",),
                ).to_dict(),
            },
            {
                **valid,
                "query": {
                    **valid["query"],
                    "state_predicate": PermanentStatePredicateSpec(
                        tapped=True
                    ).to_dict(),
                },
            },
        )
        for value in malformed:
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    ActivationCondition.from_dict(value)

    def test_public_activation_condition_dependencies_fail_closed(self):
        for index, (capability_id, restriction) in enumerate(
            (
                (
                    ACTIVATION_PHASE_CONDITION_CAPABILITY,
                    "Activate only during your upkeep.",
                ),
                (
                    ACTIVATION_PUBLIC_QUERY_CAPABILITY,
                    "Activate only if you have no cards in hand.",
                ),
            )
        ):
            value = copy.deepcopy(self.registry_value)
            capability = next(
                row
                for row in value["capabilities"]
                if row["id"] == capability_id
            )
            capability["status"] = "blocked"
            capability["blockers"] = ["focused dependency mutation"]
            registry = CapabilityRegistry(value)
            registry.mark_evidence_verified("0" * 64)
            record = condition_record(
                f"{{1}}: Draw a card. {restriction}",
                suffix=173_003_000 + index,
            )
            oracle = compile_oracle_card(
                record,
                capability_registry=registry,
                capability_profile="commander_review",
            )
            self.assertNotEqual("exact", oracle.status)
            self.assertTrue(oracle.material_residuals)

    def test_target_effect_assurance_accepts_public_activation_restriction(self):
        record = condition_record(
            "{1}: Target creature gets +2/+2 until end of turn. Activate only "
            "during your turn, before attackers are declared.",
            suffix=173_004_001,
        )
        oracle = compile_oracle_card(
            record,
            capability_registry=self.registry,
            capability_profile="commander_review",
        )
        self.assertEqual("exact", oracle.status, oracle.material_residuals)
        TargetEffectCorpusCollector().observe(record, oracle)


class PublicActivationConditionRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temporary = tempfile.TemporaryDirectory()
        cls.db = focused_database(cls.temporary.name)
        loader = DeckLoader(cls.db)
        cls.mishra = loader.load(
            ROOT / "examples" / "mishra-eminent-one.txt",
            commander="Mishra, Eminent One",
            deck_name="Mishra",
        )
        cls.zimone = loader.load(
            ROOT / "examples" / "zimone-and-dina.txt",
            commander="Zimone and Dina",
            deck_name="Zimone",
        )

    @classmethod
    def tearDownClass(cls) -> None:
        cls.db.close()
        cls.temporary.cleanup()

    def session(self, *card_names: str, seed: int, players: int = 4):
        deck = copy.deepcopy(self.mishra)
        entries = [entry for entry in deck.entries if entry.board == "mainboard"]
        for entry, card_name in zip(
            entries[: len(card_names)], card_names, strict=True
        ):
            entry.name = card_name
        session = make_session(
            self.db,
            deck,
            self.zimone,
            players=players,
            seed=seed,
            auto_pass_empty=False,
        )
        keep_all(session)
        engine = session.engine
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine.state.priority_player = None
        engine.state.priority_passes = []
        session.commands.clear()
        session.decisions.clear()
        return session

    @staticmethod
    def source(engine, name: str):
        source = next(
            card
            for card in engine.state.cards.values()
            if card.owner == "A" and card.printed_name == name
        )
        engine.move_card(
            source.object_id,
            "battlefield",
            controller="A",
            tapped=False,
            log=False,
        )
        return source

    @staticmethod
    def token(engine, *, name: str, type_line: str, colors=()):
        ref = engine.create_token(
            "A",
            name=name,
            characteristics={
                "type_line": type_line,
                "power": "1",
                "toughness": "1",
                "colors": list(colors),
            },
            reason="public activation condition fixture",
        )[0]
        return engine._resolve_object("A", ref, zones={"battlefield"})

    @staticmethod
    def condition(text: str):
        return parse_activated_abilities(
            card_name="Generic Activation Condition",
            oracle_text=f"{{T}}: Draw a card. {text}",
        )[0]

    @staticmethod
    def set_priority(engine, *, phase: str, step: str) -> None:
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine.state.active_player = "A"
        engine.state.started = True
        engine.state.phase = phase
        engine.state.step = step
        engine._grant_priority("A")
        engine._issue_priority("A")

    @staticmethod
    def activation_offer(session, source, ability):
        engine = session.engine
        engine.pump()
        packet = session.packet("pilot:A", full=True)
        return next(
            action
            for action in packet["decision"]["ctx"]["legal"]["actions"]
            if action["id"] == f"activate:{source.ref}:{ability.ability_id}"
        )

    def test_phase_windows_share_offer_and_commit_revalidation(self):
        session = self.session("Dawn Channeler", seed=173_010_001, players=2)
        engine = session.engine
        source = self.source(engine, "Dawn Channeler")
        ability = engine._activated_abilities(source)[0]
        for phase, step, expected in (
            ("beginning", "upkeep", "payable"),
            ("precombat_main", "main", "payable"),
            ("combat", "beginning_combat", "payable"),
            ("combat", "declare_attackers", "unavailable"),
            ("postcombat_main", "main", "unavailable"),
        ):
            with self.subTest(phase=phase, step=step):
                engine.state.phase = phase
                engine.state.step = step
                self.assertEqual(
                    expected,
                    activation_condition_status(engine, "A", ability, source)[0],
                )

        upkeep = self.condition("Activate only during your upkeep.")
        engine.state.phase = "beginning"
        engine.state.step = "upkeep"
        self.assertEqual(
            ("payable", None),
            activation_condition_status(engine, "A", upkeep, source),
        )
        engine.state.step = "draw"
        self.assertEqual(
            "unavailable",
            activation_condition_status(engine, "A", upkeep, source)[0],
        )

        engine.state.players["A"].mana_pool["C"] = 1
        self.set_priority(engine, phase="precombat_main", step="main")
        hand_before = len(engine.state.players["A"].zones["hand"])
        offer = self.activation_offer(session, source, ability)
        result = session.act("pilot:A", {"action_id": offer["id"]})
        self.assertTrue(result.ok, result.summary)
        for _ in range(12):
            if not engine.state.stack:
                break
            pass_current(session)
        self.assertEqual(
            hand_before + 1,
            len(engine.state.players["A"].zones["hand"]),
        )

    def test_public_queries_use_current_type_color_and_ability_characteristics(self):
        session = self.session("Flight Surveyor", seed=173_010_002, players=2)
        engine = session.engine
        source = self.source(engine, "Flight Surveyor")
        flying = engine._activated_abilities(source)[0]
        creature = self.token(
            engine,
            name="Grounded Citizen",
            type_line="Token Creature — Citizen",
        )
        self.assertEqual(
            "unavailable",
            activation_condition_status(engine, "A", flying, source)[0],
        )
        engine.apply_effect(
            {
                "op": "grant_keyword_until_end_of_turn",
                "card": creature.ref,
                "keyword": "Flying",
            },
            actor="A",
        )
        self.assertEqual(
            ("payable", None),
            activation_condition_status(engine, "A", flying, source),
        )
        commit_continuous_effect(
            engine.state,
            ContinuousEffect(
                effect_id="fixture:remove-flying",
                source_id="fixture:remove-flying-source",
                layer=Layer.ABILITY,
                sublayer="6",
                timestamp=engine._next_zone_timestamp(),
                operations=(ContinuousOperation("remove_all_abilities"),),
                origin=ContinuousEffectOrigin.RESOLUTION,
                duration=ContinuousEffectDuration.UNTIL_END_OF_TURN,
                locked_objects=(
                    ContinuousObjectIdentity(
                        object_id=creature.object_id,
                        logical_object_id=creature.logical_object_id,
                    ),
                ),
            ),
        )
        self.assertEqual(
            "unavailable",
            activation_condition_status(engine, "A", flying, source)[0],
        )

        device = self.token(
            engine,
            name="Layer Four Device",
            type_line="Token Artifact — Device",
        )
        no_creatures = self.condition(
            "Activate only if you control no creatures."
        )
        engine.move_card(creature.object_id, "graveyard", log=False)
        self.assertEqual(
            ("payable", None),
            activation_condition_status(engine, "A", no_creatures, source),
        )
        engine.apply_effect(
            {"op": "add_type", "card": device.ref, "type": "Creature"},
            actor="A",
        )
        self.assertEqual(
            "unavailable",
            activation_condition_status(engine, "A", no_creatures, source)[0],
        )

        black_condition = self.condition(
            "Activate only if you control two or more black permanents."
        )
        self.token(
            engine,
            name="Black Device One",
            type_line="Token Artifact — Device",
            colors=("B",),
        )
        self.assertEqual(
            "unavailable",
            activation_condition_status(engine, "A", black_condition, source)[0],
        )
        self.token(
            engine,
            name="Black Device Two",
            type_line="Token Artifact — Device",
            colors=("B",),
        )
        self.assertEqual(
            ("payable", None),
            activation_condition_status(engine, "A", black_condition, source),
        )
        engine.change_control(
            source.object_id,
            "B",
            reason="public activation condition controller witness",
        )
        engine.create_token(
            "B",
            name="Opponent Flying Witness",
            characteristics={
                "type_line": "Token Creature — Bird",
                "power": "1",
                "toughness": "1",
                "keywords": ["Flying"],
            },
            reason="public activation condition controller fixture",
        )
        self.assertEqual(
            ("payable", None),
            activation_condition_status(engine, "B", flying, source),
        )

    def _stale_query_rejected(self) -> None:
        session = self.session("Public Query Lens", seed=173_010_003, players=2)
        engine = session.engine
        source = self.source(engine, "Public Query Lens")
        for object_id in tuple(engine.state.players["A"].zones["hand"]):
            engine.move_card(object_id, "graveyard", log=False)
        engine.state.players["A"].mana_pool["C"] = 1
        self.set_priority(engine, phase="precombat_main", step="main")
        ability = engine._activated_abilities(source)[0]
        proposal = build_activation_proposal(
            engine,
            ActivationProposalRequest.from_submission(
                "A",
                {
                    "source": source.ref,
                    "from": "battlefield",
                    "ability": ability.ability_id,
                },
            ),
        )
        engine.move_card(
            engine.state.players["A"].zones["library"][-1],
            "hand",
            log=False,
        )
        stale_offer = build_activation_offer(engine, "A", source, ability)
        self.assertNotEqual("payable", stale_offer.status)
        self.assertEqual("requires_public_activation_query", stale_offer.reason)
        before = authoritative_state_hash(engine.state)
        with self.assertRaises(ActivationProposalError):
            commit_activation(
                engine,
                proposal,
                {"payment": {"C": 1}},
            )
        self.assertEqual(before, authoritative_state_hash(engine.state))
        self.assertFalse(source.tapped)
        self.assertEqual(1, engine.state.players["A"].mana_pool["C"])
        self.assertFalse(engine.state.stack)

    def test_stale_public_query_offer_rolls_back_before_cost_mutation(self):
        self._stale_query_rejected()

    def test_conditions_gate_existing_effect_and_replacement_owners(self):
        counter_session = self.session(
            "Upkeep Counter",
            "Doubling Season",
            seed=173_010_006,
            players=2,
        )
        engine = counter_session.engine
        source = self.source(engine, "Upkeep Counter")
        replacement = self.source(engine, "Doubling Season")
        self.assertEqual("battlefield", replacement.zone)
        engine.state.players["A"].mana_pool["C"] = 1
        self.set_priority(engine, phase="beginning", step="upkeep")
        ability = engine._activated_abilities(source)[0]
        offer = self.activation_offer(counter_session, source, ability)
        result = counter_session.act("pilot:A", {"action_id": offer["id"]})
        self.assertTrue(result.ok, result.summary)
        for _ in range(12):
            if not engine.state.stack:
                break
            pass_current(counter_session)
        self.assertEqual(2, source.counters["+1/+1"])

        damage_session = self.session(
            "Threshold Spark",
            seed=173_010_007,
            players=2,
        )
        engine = damage_session.engine
        source = self.source(engine, "Threshold Spark")
        for object_id in tuple(engine.state.players["A"].zones["hand"]):
            engine.move_card(object_id, "graveyard", log=False)
        target_ref = engine.create_token(
            "B",
            name="Damage Target",
            characteristics={
                "type_line": "Token Creature — Citizen",
                "power": "5",
                "toughness": "5",
            },
            reason="public activation condition damage fixture",
        )[0]
        target = engine._resolve_object("B", target_ref, zones={"battlefield"})
        engine.state.players["A"].mana_pool["C"] = 1
        self.set_priority(engine, phase="precombat_main", step="main")
        ability = engine._activated_abilities(source)[0]
        offer = self.activation_offer(damage_session, source, ability)
        result = damage_session.act(
            "pilot:A",
            {"action_id": offer["id"], "targets": [target.ref]},
        )
        self.assertTrue(result.ok, result.summary)
        for _ in range(12):
            if not engine.state.stack:
                break
            pass_current(damage_session)
        self.assertEqual(2, target.marked_damage)
        source.tapped = False
        engine.state.players["A"].mana_pool["C"] = 1
        self.set_priority(engine, phase="precombat_main", step="main")
        offer = self.activation_offer(damage_session, source, ability)
        life_before = engine.state.players["B"].life
        result = damage_session.act(
            "pilot:A",
            {"action_id": offer["id"], "targets": ["B"]},
        )
        self.assertTrue(result.ok, result.summary)
        for _ in range(12):
            if not engine.state.stack:
                break
            pass_current(damage_session)
        self.assertEqual(life_before - 2, engine.state.players["B"].life)

        life_session = self.session(
            "Threshold Vessel",
            seed=173_010_008,
            players=2,
        )
        engine = life_session.engine
        source = self.source(engine, "Threshold Vessel")
        for object_id in tuple(engine.state.players["A"].zones["hand"]):
            engine.move_card(object_id, "graveyard", log=False)
        engine.state.players["A"].mana_pool["C"] = 1
        self.set_priority(engine, phase="precombat_main", step="main")
        ability = engine._activated_abilities(source)[0]
        offer = self.activation_offer(life_session, source, ability)
        life_before = engine.state.players["A"].life
        result = life_session.act("pilot:A", {"action_id": offer["id"]})
        self.assertTrue(result.ok, result.summary)
        for _ in range(12):
            if not engine.state.stack:
                break
            pass_current(life_session)
        self.assertEqual(life_before + 4, engine.state.players["A"].life)

    def test_tapped_entry_gates_public_any_target_activation(self):
        session = self.session(
            "Tapped Threshold Spark",
            seed=173_010_009,
            players=2,
        )
        engine = session.engine
        source = next(
            card
            for card in engine.state.cards.values()
            if card.owner == "A"
            and card.printed_name == "Tapped Threshold Spark"
        )
        engine.move_card(
            source.object_id,
            "battlefield",
            controller="A",
            log=False,
        )
        self.assertTrue(source.tapped)

        for object_id in tuple(engine.state.players["A"].zones["hand"]):
            engine.move_card(object_id, "graveyard", log=False)
        target_ref = engine.create_token(
            "B",
            name="Tapped Entry Damage Target",
            characteristics={
                "type_line": "Token Creature — Citizen",
                "power": "3",
                "toughness": "3",
            },
            reason="tapped entry and public target interaction fixture",
        )[0]
        target = engine._resolve_object(
            "B",
            target_ref,
            zones={"battlefield"},
        )
        engine.state.players["A"].mana_pool["C"] = 1
        self.set_priority(engine, phase="precombat_main", step="main")
        ability = engine._activated_abilities(source)[0]
        action_id = f"activate:{source.ref}:{ability.ability_id}"
        engine.pump()
        actions = session.packet("pilot:A", full=True)["decision"]["ctx"][
            "legal"
        ]["actions"]
        self.assertNotIn(action_id, {action["id"] for action in actions})

        source.tapped = False
        self.set_priority(engine, phase="precombat_main", step="main")
        offer = self.activation_offer(session, source, ability)
        self.assertIn(target.ref, offer["target_schema"]["legal_refs"])
        self.assertIn("B", offer["target_schema"]["legal_refs"])
        result = session.act(
            "pilot:A",
            {"action_id": offer["id"], "targets": [target.ref]},
        )
        self.assertTrue(result.ok, result.summary)
        self.assertTrue(source.tapped)
        for _ in range(12):
            if not engine.state.stack:
                break
            pass_current(session)
        self.assertEqual(1, target.marked_damage)

    def test_public_hand_and_graveyard_queries_preserve_privacy_and_replay(self):
        privacy = self.session("Public Query Lens", seed=173_010_004)
        engine = privacy.engine
        source = self.source(engine, "Public Query Lens")
        exact_hand = self.condition(
            "Activate only if you have exactly seven cards in your hand."
        )
        hand_refs = {
            engine.state.cards[object_id].ref
            for object_id in engine.state.players["A"].zones["hand"]
        }
        self.assertEqual(
            ("payable", None),
            activation_condition_status(engine, "A", exact_hand, source),
        )
        projected = json.dumps(StateProjector(self.db, engine.state)._snapshot("pilot:B"))
        self.assertTrue(all(ref not in projected for ref in hand_refs))

        for object_id in tuple(engine.state.players["A"].zones["hand"]):
            engine.move_card(object_id, "graveyard", log=False)
        graveyard = self.condition(
            "Activate only if there are seven or more cards in your graveyard."
        )
        self.assertEqual(
            ("payable", None),
            activation_condition_status(engine, "A", graveyard, source),
        )
        engine.state.players["A"].mana_pool["C"] = 1
        self.set_priority(engine, phase="precombat_main", step="main")
        ability = engine._activated_abilities(source)[0]
        offer = self.activation_offer(privacy, source, ability)
        privacy.initial_checkpoint = checkpoint_envelope(engine.state)
        privacy.commands.clear()
        privacy.decisions.clear()
        hand_before = len(engine.state.players["A"].zones["hand"])
        result = privacy.act("pilot:A", {"action_id": offer["id"]})
        self.assertTrue(result.ok, result.summary)
        for _ in range(12):
            if not engine.state.stack:
                break
            pass_current(privacy)
        self.assertEqual(
            hand_before + 1,
            len(engine.state.players["A"].zones["hand"]),
        )
        expected_query_hash = authoritative_state_hash(engine.state)
        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / "public-query-condition-record"
            privacy.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(expected_query_hash, replay["final_state_hash"])

        session = self.session("Upkeep Reliquary", seed=173_010_005)
        engine = session.engine
        source = self.source(engine, "Upkeep Reliquary")
        engine.state.players["A"].mana_pool["C"] = 1
        self.set_priority(engine, phase="beginning", step="upkeep")
        ability = engine._activated_abilities(source)[0]
        offer = self.activation_offer(session, source, ability)
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()
        life_before = engine.state.players["A"].life
        activated = session.act("pilot:A", {"action_id": offer["id"]})
        self.assertTrue(activated.ok, activated.summary)
        for _ in range(12):
            if not engine.state.stack:
                break
            pass_current(session)
        self.assertFalse(engine.state.stack)
        self.assertEqual(life_before + 2, engine.state.players["A"].life)
        self.assertEqual(
            "unavailable",
            activation_condition_status(engine, "A", ability, source)[0],
        )
        expected_hash = authoritative_state_hash(engine.state)
        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / "public-activation-condition-record"
            session.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(expected_hash, replay["final_state_hash"])

    def test_public_activation_condition_mutants_are_killed(self):
        registry = trusted_registry()
        record = condition_record(
            "{1}: Draw a card. Activate only if you have no cards in hand.",
            suffix=173_020_001,
        )

        def assert_compiles() -> None:
            oracle = compile_oracle_card(
                record,
                capability_registry=registry,
                capability_profile="commander_review",
            )
            self.assertEqual("exact", oracle.status)

        assert_compiles()
        with mock.patch(
            "quorune.abilities.activation_restriction_spec",
            return_value=None,
        ):
            with self.assertRaises(AssertionError):
                assert_compiles()

        session = self.session("Flight Surveyor", seed=173_020_002, players=2)
        engine = session.engine
        source = self.source(engine, "Flight Surveyor")
        ability = engine._activated_abilities(source)[0]
        self.token(
            engine,
            name="Flying Witness",
            type_line="Token Creature — Bird",
        ).annotations["object_characteristics"] = {
            "type_line": "Token Creature — Bird",
            "power": "1",
            "toughness": "1",
            "keywords": ["Flying"],
        }

        def assert_payable() -> None:
            self.assertEqual(
                ("payable", None),
                activation_condition_status(engine, "A", ability, source),
            )

        assert_payable()
        with mock.patch(
            "quorune.rules.activation.conditions._public_query_count",
            return_value=0,
        ):
            with self.assertRaises(AssertionError):
                assert_payable()

        self._stale_query_rejected()
        with mock.patch(
            "quorune.rules.activation.commit.activation_condition_status",
            return_value=("payable", None),
        ):
            with self.assertRaises(AssertionError):
                self._stale_query_rejected()


if __name__ == "__main__":
    unittest.main()
