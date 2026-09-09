from __future__ import annotations

from copy import deepcopy
from dataclasses import FrozenInstanceError, replace
import json
from pathlib import Path
import tempfile
from typing import Mapping
import unittest
from unittest.mock import patch

from common import ROOT, keep_all, make_session, pass_current
from quorune.ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
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
from quorune.counter_placement import place_counters_on_refs
from quorune.damage import damage_proposal, resolve_damage_batch
from quorune.damage_modifier_state import (
    DamageModifierDuration,
    DamagePreventionShield,
    DamageSubject,
    GainLifePreventionAftermath,
    PreventionMode,
)
from quorune.effect_runtime import dispatch_effect
from quorune.compiler.fixed_counter_trigger_nodes import (
    FIXED_COUNTER_EVENT_TRIGGER_MECHANIC,
    FIXED_COUNTER_EVENT_TRIGGER_TEMPLATE_IDS,
    FIXED_SPELL_CAST_CHARACTERISTIC_MECHANIC,
    FIXED_TYPED_EVENT_EFFECT_TRIGGER_MECHANIC,
    FIXED_TYPED_EVENT_EFFECT_TRIGGER_TEMPLATE_IDS,
    OPTIONAL_COUNTER_PLACEMENT_OPERATION,
    OPTIONAL_FIXED_COUNTER_EVENT_TRIGGER_MECHANIC,
    FixedCounterTriggerBinding,
    FixedCounterTriggerEvent,
    FixedCounterZoneController,
    FixedCounterZoneSubject,
    FixedSpellCastController,
    FixedSpellCastCharacteristicKind,
    FixedSpellCastCharacteristicQuery,
    FixedSpellCastCharacteristicTerm,
    FixedSpellCastQuality,
    FixedSpellCastSubject,
    fixed_counter_trigger_binding,
)
from quorune.compiler.target_effect_corpus_assurance import (
    TargetEffectCorpusCollector,
)
from quorune.deck import DeckLoader
from quorune.kicker import KICKER_CAST_OPTION_ID
from quorune.model import CardInstance, CombatState
from quorune.object_predicate import ObjectQuerySpec
from quorune.oracle_ir import (
    compile_oracle_card,
    generated_programs,
    register_generated_programs,
)
from quorune.player_result_events import (
    CardDrawEvent,
    LifeGainEvent,
    PlayerResultEventError,
)
from quorune.projection import StateProjector
from quorune.record import (
    authoritative_state_hash,
    checkpoint_envelope,
    replay_record,
)
from quorune.rules.capabilities import (
    CapabilityRegistry,
    load_default_capability_registry,
)
from quorune.rules.entry_return_capability_shapes import (
    fixed_entry_return_node_capabilities,
)
from quorune.semantic_choices.context import (
    SemanticChoiceContext,
    SnapshotSemanticChoiceQuery,
)
from quorune.semantic_choices.model import SemanticChoiceError
from quorune.semantic_choices.optional_counter_placement import (
    OptionalCounterPlacementHandler,
)
from quorune.semantic_runtime import LifeChangeIntent
from quorune.trigger_processing import collect_trigger_items, enqueue_trigger_batch
from scripts.build_test_database import build_fixture_database


REGISTRY_PATH = ROOT / "quorune" / "rules" / "capability-registry.json"
TEMPLATE_IDS = set(FIXED_COUNTER_EVENT_TRIGGER_TEMPLATE_IDS)
OPTIONAL_TEMPLATE_IDS = {
    template_id.removesuffix("-v1") + "-optional-v1"
    for template_id in TEMPLATE_IDS
}
ALL_TEMPLATE_IDS = TEMPLATE_IDS | OPTIONAL_TEMPLATE_IDS
FIXED_TYPED_EVENT_TEMPLATE_IDS = set(
    FIXED_TYPED_EVENT_EFFECT_TRIGGER_TEMPLATE_IDS
)


def focused_database(directory: str) -> CardDatabase:
    database = Path(directory) / "fixed-counter-event-triggers.sqlite3"
    build_fixture_database(
        [
            ROOT / "tests" / "fixtures" / "scryfall-exact-lists.json",
            ROOT / "tests" / "fixtures" / "counter-replacement-cards.json",
            ROOT / "tests" / "fixtures" / "damage-result-cards.json",
            ROOT
            / "tests"
            / "fixtures"
            / "fixed-counter-event-trigger-cards.json",
            ROOT
            / "tests"
            / "fixtures"
            / "fixed-typed-event-trigger-cards.json",
            ROOT / "tests" / "fixtures" / "typecycling-cards.json",
            ROOT / "tests" / "fixtures" / "kicker-rules-cards.json",
        ],
        database,
    )
    return CardDatabase(database)


class FixedSourceCombatGrowthTriggerRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
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
        cls.capabilities = load_default_capability_registry()

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    def session(self, seed: int, *, players: int = 2):
        session = make_session(
            self.db,
            self.mishra,
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
        engine.state.pending_trigger_batches.clear()
        engine.state.stack.clear()
        session.commands.clear()
        session.decisions.clear()
        return session

    def add_card(
        self,
        engine,
        *,
        seat: str,
        name: str,
        ref: str,
        zone: str,
        controller: str | None = None,
        is_token: bool = False,
    ) -> CardInstance:
        record = self.db.lookup(name)
        current_controller = controller or seat
        public = zone == "battlefield"
        card = CardInstance(
            object_id=f"fixture:{ref}",
            ref=ref,
            oracle_id=record.oracle_id,
            printed_name=record.name,
            owner=seat,
            controller=current_controller,
            zone=zone,
            is_token=is_token,
            zone_timestamp=engine.state.event_sequence + 1,
            known_to=list(engine.seats) if public else [seat],
            revealed_to=list(engine.seats) if public else [],
        )
        engine.state.cards[card.object_id] = card
        engine.state.players[seat].zones[zone].append(card.object_id)
        return card

    @staticmethod
    def deck_card(engine, seat: str, name: str) -> CardInstance:
        return next(
            card
            for card in engine.state.cards.values()
            if card.owner == seat and card.printed_name == name
        )

    def register_trigger(self, engine, source: CardInstance):
        programs = [
            program
            for program in generated_programs(
                self.db,
                self.db.by_oracle_id(source.oracle_id),
                trust_level="trusted",
                capability_registry=self.capabilities,
                capability_profile="commander_review",
            )
            if program.provenance.get("template_id") in ALL_TEMPLATE_IDS
        ]
        self.assertEqual(1, len(programs))
        engine.semantics.put(programs[0])
        return programs[0]

    def register_typed_event_trigger(
        self,
        engine,
        source: CardInstance,
    ):
        programs = [
            program
            for program in generated_programs(
                self.db,
                self.db.by_oracle_id(source.oracle_id),
                trust_level="trusted",
                capability_registry=self.capabilities,
                capability_profile="commander_review",
            )
            if program.provenance.get("template_id")
            in FIXED_TYPED_EVENT_TEMPLATE_IDS
        ]
        self.assertEqual(1, len(programs))
        engine.semantics.put(programs[0])
        return programs[0]

    @staticmethod
    def _event_condition_fields(condition: Mapping | None) -> set[str]:
        if not isinstance(condition, Mapping):
            return set()
        field = condition.get("field")
        fields = {field} if isinstance(field, str) else set()
        for key in ("all", "any"):
            values = condition.get(key)
            if isinstance(values, list):
                for value in values:
                    fields.update(
                        FixedSourceCombatGrowthTriggerRuntimeTests._event_condition_fields(
                            value if isinstance(value, Mapping) else None
                        )
                    )
        nested = condition.get("not")
        fields.update(
            FixedSourceCombatGrowthTriggerRuntimeTests._event_condition_fields(
                nested if isinstance(nested, Mapping) else None
            )
        )
        return fields

    def register_subtype_graveyard_trigger(
        self,
        engine,
        source: CardInstance,
        *,
        event: str,
        qualifier_field: str,
    ):
        programs = [
            program
            for program in generated_programs(
                self.db,
                self.db.by_oracle_id(source.oracle_id),
                trust_level="trusted",
                capability_registry=self.capabilities,
                capability_profile="commander_review",
            )
            if program.provenance.get("template_id")
            in FIXED_TYPED_EVENT_TEMPLATE_IDS
            and program.event == event
            and qualifier_field
            in self._event_condition_fields(program.event_condition)
        ]
        self.assertEqual(1, len(programs))
        engine.semantics.put(programs[0])
        return programs[0]

    @staticmethod
    def resolve_top(engine) -> None:
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine.state.priority_player = None
        engine._prepare_stack_resolution()

    def prepare_noncreature_cast(self, engine) -> CardInstance:
        engine.state.active_player = "A"
        engine.state.phase = "precombat_main"
        engine.state.step = "main"
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine.state.priority_player = "A"
        engine.state.priority_passes = []
        card = self.deck_card(engine, "A", "Sol Ring")
        if card.zone != "hand":
            engine.move_card(card.object_id, "hand", log=False)
        engine.state.players["A"].mana_pool["C"] += 1
        engine._cast("A", {"card": card.ref, "pay": "auto"})
        return card

    @staticmethod
    def step_context(*, player: str, step: str = "upkeep") -> dict[str, str]:
        return {"phase": "beginning", "step": step, "player": player}

    @staticmethod
    def replacement_options(session, seat: str) -> list[str]:
        decision = StateProjector(
            session.engine.card_db,
            session.state,
        )._decision(f"pilot:{seat}")
        assert decision is not None
        return [option["id"] for option in decision["ctx"]["options"]]

    def finish_replacements(self, session, seat: str) -> None:
        for _ in range(8):
            decision = session.state.pending_decision
            if decision is None or decision.kind != "replacement.order":
                return
            result = session.act(
                f"pilot:{seat}",
                {
                    "action_id": "choose",
                    "choices": {
                        "replacement": self.replacement_options(
                            session,
                            seat,
                        )[0]
                    },
                },
            )
            self.assertTrue(result.ok, result.summary)
        self.fail("Fixed counter event-trigger replacement did not converge")

    def assert_player_result_trigger(
        self,
        engine,
        source: CardInstance,
        *,
        event: str,
        amount: int | None = None,
    ) -> None:
        engine._stabilize()
        self.assertTrue(engine.state.stack)
        item = engine.state.stack[-1]
        self.assertEqual(event, item.context["event"])
        self.assertEqual(source.object_id, item.source_object_id)
        if amount is not None:
            self.assertEqual(amount, item.context["amount"])
        self.resolve_top(engine)
        self.assertEqual(1, source.counters.get("+1/+1"))


    def test_source_combat_growth_attack_uses_current_ability_and_replays(self):
        session = self.session(121060, players=4)
        engine = session.engine
        engine.state.active_player = "A"
        engine.state.phase_index = 5
        engine.state.phase = "combat"
        engine.state.step = "declare_attackers"
        engine.state.combat = CombatState()
        source = self.add_card(
            engine,
            seat="B",
            name="Generic Attack Growth Trigger Fixture",
            ref="source-combat-growth-attacker",
            zone="battlefield",
        )
        engine.change_control(
            source.object_id,
            "A",
            reason="source combat growth control witness",
        )
        source.temporary_keywords.append("Haste")
        program = self.register_typed_event_trigger(engine, source)

        engine._issue_attackers()
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()
        declared = session.act(
            "pilot:A",
            {"a": "attack", "atk": {source.ref: "B"}},
        )
        self.assertTrue(declared.ok, declared.summary)
        item = next(
            value
            for value in engine.state.stack
            if value.semantic_key == program.key
        )
        self.assertEqual("A", item.controller)
        self.assertEqual("creature.attacks", item.context["event"])
        self.assertEqual(
            source.logical_object_id,
            item.context["source_logical_object_id"],
        )
        self.assertEqual(2, engine._numeric_stat(source.object_id, "power"))
        for _ in range(12):
            if not engine.state.stack:
                break
            pass_current(session)
        self.assertFalse(engine.state.stack)
        self.assertEqual(4, engine._numeric_stat(source.object_id, "power"))
        expected_hash = authoritative_state_hash(engine.state)
        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / "source-combat-growth-attack"
            session.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(expected_hash, replay["final_state_hash"])

        removed_session = self.session(121061)
        removed_engine = removed_session.engine
        removed_engine.state.active_player = "A"
        removed_engine.state.phase_index = 5
        removed_engine.state.phase = "combat"
        removed_engine.state.step = "declare_attackers"
        removed_engine.state.combat = CombatState()
        removed = self.add_card(
            removed_engine,
            seat="A",
            name="Generic Attack Growth Trigger Fixture",
            ref="source-combat-growth-removed",
            zone="battlefield",
        )
        removed_program = self.register_typed_event_trigger(
            removed_engine,
            removed,
        )
        commit_continuous_effect(
            removed_engine.state,
            ContinuousEffect(
                effect_id="fixture:remove-source-combat-growth",
                source_id="fixture:remove-source-combat-growth-owner",
                layer=Layer.ABILITY,
                sublayer="6",
                timestamp=removed_engine._next_zone_timestamp(),
                operations=(ContinuousOperation("remove_all_abilities"),),
                origin=ContinuousEffectOrigin.RESOLUTION,
                duration=ContinuousEffectDuration.UNTIL_END_OF_TURN,
                applies=ObjectQuerySpec(zones=("battlefield",)),
                locked_objects=(
                    ContinuousObjectIdentity(
                        object_id=removed.object_id,
                        logical_object_id=removed.logical_object_id,
                    ),
                ),
            ),
        )
        self.assertEqual(
            [],
            removed_engine._effective_card_data(removed)["ability_fragments"],
        )
        removed_engine._issue_attackers()
        removed_result = removed_session.act(
            "pilot:A",
            {"a": "attack", "atk": {removed.ref: "B"}},
        )
        self.assertTrue(removed_result.ok, removed_result.summary)
        self.assertFalse(
            any(
                item.semantic_key == removed_program.key
                for item in removed_engine.state.stack
            )
        )

    def test_source_combat_growth_trigger_controller_is_stable_after_stack_placement(
        self,
    ):
        session = self.session(121069, players=4)
        engine = session.engine
        engine.state.active_player = "A"
        engine.state.phase_index = 5
        engine.state.phase = "combat"
        engine.state.step = "declare_attackers"
        engine.state.combat = CombatState()
        source = self.add_card(
            engine,
            seat="B",
            name="Generic Attack Growth Trigger Fixture",
            ref="source-combat-growth-controller-lock",
            zone="battlefield",
        )
        engine.change_control(
            source.object_id,
            "A",
            reason="source combat growth pre-event controller witness",
        )
        source.temporary_keywords.append("Haste")
        program = self.register_typed_event_trigger(engine, source)

        engine._issue_attackers()
        declared = session.act(
            "pilot:A",
            {"a": "attack", "atk": {source.ref: "B"}},
        )
        self.assertTrue(declared.ok, declared.summary)
        item = next(
            value
            for value in engine.state.stack
            if value.semantic_key == program.key
        )
        self.assertEqual("A", item.controller)

        engine.change_control(
            source.object_id,
            "C",
            reason="source combat growth post-placement controller witness",
        )

        self.assertEqual("C", source.controller)
        self.assertEqual("A", item.controller)
        self.resolve_top(engine)
        self.assertEqual(4, engine._numeric_stat(source.object_id, "power"))

    def test_source_combat_growth_block_bindings_use_sealed_participants(self):
        cases = (
            (
                "Generic Block Growth Trigger Fixture",
                (),
                True,
                "creature.blocks",
                "toughness",
                4,
            ),
            (
                "Generic Flying Block Growth Trigger Fixture",
                ("Flying",),
                True,
                "creature.blocks",
                "power",
                4,
            ),
            (
                "Generic Flying Block Growth Trigger Fixture",
                (),
                False,
                "creature.blocks",
                "power",
                1,
            ),
        )
        for index, (
            fixture,
            attacker_keywords,
            should_trigger,
            event,
            stat,
            expected,
        ) in enumerate(cases):
            with self.subTest(fixture=fixture, keywords=attacker_keywords):
                session = self.session(121062 + index)
                engine = session.engine
                engine.state.active_player = "A"
                engine.state.phase_index = 6
                engine.state.phase = "combat"
                engine.state.step = "declare_blockers"
                source = self.add_card(
                    engine,
                    seat="B",
                    name=fixture,
                    ref=f"source-combat-growth-blocker-{index}",
                    zone="battlefield",
                )
                program = self.register_typed_event_trigger(engine, source)
                attacker_ref = engine.create_token(
                    "A",
                    name=f"Source combat growth attacker {index}",
                    characteristics={
                        "type_line": "Token Creature — Test",
                        "power": "2",
                        "toughness": "2",
                        "keywords": list(attacker_keywords),
                    },
                )[0]
                attacker = engine._resolve_object("A", attacker_ref)
                attacker.attacking = "B"
                engine.state.combat = CombatState(
                    attackers_declared=True,
                    had_attacking_creature=True,
                    attackers={attacker.object_id: "B"},
                    defending_players=["B"],
                )
                engine._begin_blocker_decisions()
                result = session.act(
                    "pilot:B",
                    {"a": "block", "blk": {source.ref: attacker.ref}},
                )
                self.assertTrue(result.ok, result.summary)
                matching = [
                    item
                    for item in engine.state.stack
                    if item.semantic_key == program.key
                ]
                self.assertEqual(should_trigger, bool(matching))
                if not should_trigger:
                    self.assertEqual(
                        expected,
                        engine._numeric_stat(source.object_id, stat),
                    )
                    continue
                item = matching[0]
                self.assertEqual(event, item.context["event"])
                self.assertEqual(
                    [value.casefold() for value in attacker_keywords],
                    item.context["blocked_attacker_keywords"],
                )
                self.resolve_top(engine)
                self.assertEqual(
                    expected,
                    engine._numeric_stat(source.object_id, stat),
                )

        session = self.session(121065)
        engine = session.engine
        engine.state.active_player = "A"
        engine.state.phase_index = 6
        engine.state.phase = "combat"
        engine.state.step = "declare_blockers"
        source = self.add_card(
            engine,
            seat="A",
            name="Generic Becomes Blocked Growth Trigger Fixture",
            ref="source-combat-growth-becomes-blocked",
            zone="battlefield",
        )
        program = self.register_typed_event_trigger(engine, source)
        blocker_ref = engine.create_token(
            "B",
            name="Source combat growth blocking witness",
            characteristics={
                "type_line": "Token Creature — Test",
                "power": "1",
                "toughness": "3",
            },
        )[0]
        blocker = engine._resolve_object("B", blocker_ref)
        source.attacking = "B"
        engine.state.combat = CombatState(
            attackers_declared=True,
            had_attacking_creature=True,
            attackers={source.object_id: "B"},
            defending_players=["B"],
        )
        engine._begin_blocker_decisions()
        result = session.act(
            "pilot:B",
            {"a": "block", "blk": {blocker.ref: source.ref}},
        )
        self.assertTrue(result.ok, result.summary)
        item = next(
            value
            for value in engine.state.stack
            if value.semantic_key == program.key
        )
        self.assertEqual("creature.becomes_blocked", item.context["event"])
        self.resolve_top(engine)
        self.assertEqual(3, engine._numeric_stat(source.object_id, "power"))
        self.assertEqual(3, engine._numeric_stat(source.object_id, "toughness"))

    def test_gaining_flying_after_block_declaration_does_not_create_trigger(self):
        session = self.session(121070)
        engine = session.engine
        engine.state.active_player = "A"
        engine.state.phase_index = 6
        engine.state.phase = "combat"
        engine.state.step = "declare_blockers"
        source = self.add_card(
            engine,
            seat="B",
            name="Generic Flying Block Growth Trigger Fixture",
            ref="source-combat-growth-gain-flying",
            zone="battlefield",
        )
        program = self.register_typed_event_trigger(engine, source)
        attacker_ref = engine.create_token(
            "A",
            name="Post-declaration flying attacker",
            characteristics={
                "type_line": "Token Creature — Test",
                "power": "2",
                "toughness": "2",
            },
        )[0]
        attacker = engine._resolve_object("A", attacker_ref)
        attacker.attacking = "B"
        engine.state.combat = CombatState(
            attackers_declared=True,
            had_attacking_creature=True,
            attackers={attacker.object_id: "B"},
            defending_players=["B"],
        )
        engine._begin_blocker_decisions()
        result = session.act(
            "pilot:B",
            {"a": "block", "blk": {source.ref: attacker.ref}},
        )
        self.assertTrue(result.ok, result.summary)
        self.assertFalse(
            any(item.semantic_key == program.key for item in engine.state.stack)
        )

        attacker.temporary_keywords.append("Flying")
        engine._stabilize()

        self.assertIn("flying", engine._combat_keywords(attacker))
        self.assertFalse(
            any(item.semantic_key == program.key for item in engine.state.stack)
        )

    def test_losing_flying_after_block_declaration_does_not_remove_existing_trigger(
        self,
    ):
        session = self.session(121071)
        engine = session.engine
        engine.state.active_player = "A"
        engine.state.phase_index = 6
        engine.state.phase = "combat"
        engine.state.step = "declare_blockers"
        source = self.add_card(
            engine,
            seat="B",
            name="Generic Flying Block Growth Trigger Fixture",
            ref="source-combat-growth-lose-flying",
            zone="battlefield",
        )
        program = self.register_typed_event_trigger(engine, source)
        attacker_ref = engine.create_token(
            "A",
            name="Declaration-time flying attacker",
            characteristics={
                "type_line": "Token Creature — Test",
                "power": "2",
                "toughness": "2",
            },
            temporary_keywords=("Flying",),
        )[0]
        attacker = engine._resolve_object("A", attacker_ref)
        attacker.attacking = "B"
        engine.state.combat = CombatState(
            attackers_declared=True,
            had_attacking_creature=True,
            attackers={attacker.object_id: "B"},
            defending_players=["B"],
        )
        engine._begin_blocker_decisions()
        result = session.act(
            "pilot:B",
            {"a": "block", "blk": {source.ref: attacker.ref}},
        )
        self.assertTrue(result.ok, result.summary)
        item = next(
            value
            for value in engine.state.stack
            if value.semantic_key == program.key
        )
        self.assertEqual(["flying"], item.context["blocked_attacker_keywords"])

        attacker.temporary_keywords.clear()

        self.assertNotIn("flying", engine._combat_keywords(attacker))
        self.assertEqual("B", item.controller)
        self.resolve_top(engine)
        self.assertEqual(4, engine._numeric_stat(source.object_id, "power"))

    def test_source_combat_growth_damage_requires_committed_player_damage(self):
        session = self.session(121066, players=4)
        engine = session.engine
        source = self.add_card(
            engine,
            seat="A",
            name="Generic Combat Damage Growth Trigger Fixture",
            ref="source-combat-growth-damage",
            zone="battlefield",
        )
        program = self.register_trigger(engine, source)
        recipient_ref = engine.create_token(
            "B",
            name="Source combat growth permanent recipient",
            characteristics={
                "type_line": "Token Creature — Test",
                "power": "2",
                "toughness": "4",
            },
        )[0]
        for index, (target, combat) in enumerate(
            (("B", False), (recipient_ref, True))
        ):
            resolve_damage_batch(
                engine,
                (
                    damage_proposal(
                        engine,
                        proposal_id=f"source-combat-growth-negative:{index}",
                        actor="A",
                        source_ref=source.ref,
                        target=target,
                        amount=1,
                        combat=combat,
                        reason="source combat growth negative witness",
                    ),
                ),
            )
            engine._stabilize()
            self.assertFalse(
                any(item.semantic_key == program.key for item in engine.state.stack)
            )

        engine.state.damage_prevention_shields.append(
            DamagePreventionShield(
                shield_id="source-combat-growth-prevention",
                source_id="fixture:source-combat-growth-prevention",
                controller="B",
                subject=DamageSubject(ref="B", kind="player", controller="B"),
                mode=PreventionMode.AMOUNT,
                remaining=1,
                duration=DamageModifierDuration.UNTIL_END_OF_TURN,
                created_turn_sequence=engine.state.turn_sequence,
            )
        )
        resolve_damage_batch(
            engine,
            (
                damage_proposal(
                    engine,
                    proposal_id="source-combat-growth-prevented",
                    actor="A",
                    source_ref=source.ref,
                    target="B",
                    amount=1,
                    combat=True,
                    reason="source combat growth prevented witness",
                ),
            ),
        )
        engine._stabilize()
        self.assertFalse(
            any(item.semantic_key == program.key for item in engine.state.stack)
        )

        resolve_damage_batch(
            engine,
            (
                damage_proposal(
                    engine,
                    proposal_id="source-combat-growth-positive",
                    actor="A",
                    source_ref=source.ref,
                    target="C",
                    amount=1,
                    combat=True,
                    reason="source combat growth committed witness",
                ),
            ),
        )
        engine._stabilize()
        item = next(
            value
            for value in engine.state.stack
            if value.semantic_key == program.key
        )
        self.assertEqual("damage.dealt", item.context["event"])
        self.assertEqual("player", item.context["target_kind"])
        self.assertTrue(item.context["combat"])
        self.resolve_top(engine)
        self.assertEqual(1, source.counters.get("+1/+1"))

    def test_combat_damage_growth_triggers_in_each_positive_double_strike_damage_step(
        self,
    ):
        session = self.session(121072)
        engine = session.engine
        engine.state.active_player = "A"
        engine.state.phase_index = 7
        engine.state.phase = "combat"
        engine.state.step = "combat_damage"
        source = self.add_card(
            engine,
            seat="A",
            name="Generic Combat Damage Growth Trigger Fixture",
            ref="source-combat-growth-double-strike",
            zone="battlefield",
        )
        source.temporary_keywords.append("Double strike")
        source.attacking = "B"
        engine.state.combat = CombatState(
            attackers_declared=True,
            blockers_declared=True,
            had_attacking_creature=True,
            attackers={source.object_id: "B"},
            defending_players=["B"],
        )
        program = self.register_trigger(engine, source)

        engine._begin_combat_damage()
        first = next(
            value
            for value in engine.state.stack
            if value.semantic_key == program.key
        )
        self.assertEqual(1, first.context["damage_step"])
        self.assertTrue(first.context["first_strike_step"])
        self.assertEqual(1, first.context["amount"])
        self.resolve_top(engine)
        self.assertEqual(1, source.counters.get("+1/+1"))

        engine._advance_step()

        second = next(
            value
            for value in engine.state.stack
            if value.semantic_key == program.key
        )
        self.assertEqual(2, second.context["damage_step"])
        self.assertTrue(second.context["first_strike_step"])
        self.assertEqual(2, second.context["amount"])
        self.assertNotEqual(first.ref, second.ref)
        self.resolve_top(engine)
        self.assertEqual(2, source.counters.get("+1/+1"))
        self.assertEqual(37, engine.state.players["B"].life)

    def test_combat_damage_growth_triggers_once_for_positive_trample_player_damage(
        self,
    ):
        session = self.session(121073)
        engine = session.engine
        engine.state.active_player = "A"
        engine.state.phase_index = 7
        engine.state.phase = "combat"
        engine.state.step = "combat_damage"
        source = self.add_card(
            engine,
            seat="A",
            name="Generic Combat Damage Growth Trigger Fixture",
            ref="source-combat-growth-trample",
            zone="battlefield",
        )
        source.temporary_keywords.append("Trample")
        place_counters_on_refs(
            engine,
            actor="A",
            object_refs=(source.ref,),
            counter_name="+1/+1",
            amount=3,
            reason="source combat growth trample fixture",
        )
        blocker_ref = engine.create_token(
            "B",
            name="Source combat growth trample blocker",
            characteristics={
                "type_line": "Token Creature — Test",
                "power": "1",
                "toughness": "2",
            },
        )[0]
        blocker = engine._resolve_object("B", blocker_ref)
        source.attacking = "B"
        blocker.blocking = source.object_id
        engine.state.combat = CombatState(
            attackers_declared=True,
            blockers_declared=True,
            had_attacking_creature=True,
            attackers={source.object_id: "B"},
            defending_players=["B"],
            blockers={source.object_id: [blocker.object_id]},
        )
        program = self.register_trigger(engine, source)
        engine._begin_combat_damage()

        result = session.act(
            "pilot:A",
            {
                "a": "dmg",
                "assignments": [
                    {"source": source.ref, "target": blocker.ref, "amount": 2},
                    {"source": source.ref, "target": "B", "amount": 2},
                ],
            },
        )

        self.assertTrue(result.ok, result.summary)
        matching = [
            value
            for value in engine.state.stack
            if value.semantic_key == program.key
        ]
        self.assertEqual(1, len(matching))
        self.assertEqual("B", matching[0].context["player"])
        self.assertEqual(2, matching[0].context["amount"])
        self.assertEqual(2, matching[0].context["assigned_amount"])
        self.resolve_top(engine)
        self.assertEqual(4, source.counters.get("+1/+1"))
        self.assertEqual(38, engine.state.players["B"].life)

    def test_combat_damage_to_nonplayer_permanents_does_not_satisfy_player_binding(
        self,
    ):
        for index, kind in enumerate(("planeswalker", "battle")):
            with self.subTest(kind=kind):
                session = self.session(121074 + index, players=3)
                engine = session.engine
                engine.state.active_player = "A"
                engine.state.phase_index = 7
                engine.state.phase = "combat"
                engine.state.step = "combat_damage"
                source = self.add_card(
                    engine,
                    seat="A",
                    name="Generic Combat Damage Growth Trigger Fixture",
                    ref=f"source-combat-growth-{kind}",
                    zone="battlefield",
                )
                if kind == "planeswalker":
                    target_ref = engine.create_token(
                        "B",
                        name="Source combat growth planeswalker target",
                        characteristics={
                            "type_line": "Token Planeswalker — Test",
                            "loyalty": "5",
                        },
                    )[0]
                else:
                    target_ref = engine.create_token(
                        "C",
                        name="Source combat growth battle target",
                        battle_protector="B",
                        characteristics={
                            "type_line": "Token Battle — Siege",
                            "defense": "5",
                        },
                    )[0]
                target = engine._resolve_object(
                    "A", target_ref, zones={"battlefield"}
                )
                source.attacking = target.ref
                engine.state.combat = CombatState(
                    attackers_declared=True,
                    blockers_declared=True,
                    had_attacking_creature=True,
                    attackers={source.object_id: target.ref},
                    attack_target_context={
                        source.object_id: {
                            "target": target.ref,
                            "kind": kind,
                            "defending_player": "B",
                            "logical_object_id": target.logical_object_id,
                        }
                    },
                    defending_players=["B"],
                )
                program = self.register_trigger(engine, source)

                engine._begin_combat_damage()

                self.assertFalse(
                    any(
                        item.semantic_key == program.key
                        for item in engine.state.stack
                    )
                )
                damage_event = next(
                    event
                    for event in reversed(engine.state.events)
                    if event.code == "combat.damage"
                )
                self.assertEqual(
                    target.ref,
                    damage_event.details["damage_events"][0]["target"],
                )
                self.assertEqual(
                    "permanent",
                    damage_event.details["damage_events"][0]["target_kind"],
                )

    def test_combat_damage_growth_trigger_is_created_when_source_dies_in_same_damage_batch(
        self,
    ):
        session = self.session(121076)
        engine = session.engine
        engine.state.active_player = "A"
        engine.state.phase_index = 7
        engine.state.phase = "combat"
        engine.state.step = "combat_damage"
        source = self.add_card(
            engine,
            seat="A",
            name="Generic Combat Damage Growth Trigger Fixture",
            ref="source-combat-growth-lethal",
            zone="battlefield",
        )
        source.temporary_keywords.append("Trample")
        place_counters_on_refs(
            engine,
            actor="A",
            object_refs=(source.ref,),
            counter_name="+1/+1",
            amount=1,
            reason="source combat growth simultaneous lethal fixture",
        )
        blocker_ref = engine.create_token(
            "B",
            name="Source combat growth lethal blocker",
            characteristics={
                "type_line": "Token Creature — Test",
                "power": "2",
                "toughness": "1",
            },
        )[0]
        blocker = engine._resolve_object("B", blocker_ref)
        source.attacking = "B"
        blocker.blocking = source.object_id
        engine.state.combat = CombatState(
            attackers_declared=True,
            blockers_declared=True,
            had_attacking_creature=True,
            attackers={source.object_id: "B"},
            defending_players=["B"],
            blockers={source.object_id: [blocker.object_id]},
        )
        program = self.register_trigger(engine, source)
        engine._begin_combat_damage()
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()
        source_logical_at_damage = source.logical_object_id

        result = session.act(
            "pilot:A",
            {
                "a": "dmg",
                "assignments": [
                    {"source": source.ref, "target": blocker.ref, "amount": 1},
                    {"source": source.ref, "target": "B", "amount": 1},
                ],
            },
        )

        self.assertTrue(result.ok, result.summary)
        self.assertEqual("graveyard", source.zone)
        item = next(
            value
            for value in engine.state.stack
            if value.semantic_key == program.key
        )
        self.assertEqual("A", item.controller)
        self.assertEqual("A", item.context["source_controller"])
        self.assertEqual("battlefield", item.context["source_zone"])
        self.assertEqual(
            source_logical_at_damage,
            item.context["source_logical_object_id"],
        )
        self.assertNotEqual(source_logical_at_damage, source.logical_object_id)
        self.assertEqual(1, item.context["amount"])

        for _ in range(8):
            if not any(
                value.semantic_key == program.key
                for value in engine.state.stack
            ):
                break
            pass_current(session)

        self.assertEqual("graveyard", source.zone)
        self.assertNotIn("+1/+1", source.counters)
        expected_hash = authoritative_state_hash(engine.state)
        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / "source-combat-growth-lethal"
            session.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(expected_hash, replay["final_state_hash"])

    def test_source_combat_growth_shares_apnap_and_pins_source_incarnation(self):
        session = self.session(121067, players=4)
        engine = session.engine
        engine.state.active_player = "A"
        engine.state.phase_index = 5
        engine.state.phase = "combat"
        engine.state.step = "declare_attackers"
        engine.state.combat = CombatState()
        source = self.add_card(
            engine,
            seat="A",
            name="Generic Attack Growth Trigger Fixture",
            ref="source-combat-growth-apnap",
            zone="battlefield",
        )
        observer = self.add_card(
            engine,
            seat="C",
            name="Typed Public Attack Trigger Fixture",
            ref="source-combat-growth-observer",
            zone="battlefield",
        )
        source_program = self.register_typed_event_trigger(engine, source)
        observer_program = self.register_typed_event_trigger(engine, observer)
        engine._issue_attackers()
        result = session.act(
            "pilot:A",
            {"a": "attack", "atk": {source.ref: "B"}},
        )
        self.assertTrue(result.ok, result.summary)
        applicable = [
            item
            for item in engine.state.stack
            if item.semantic_key in {source_program.key, observer_program.key}
        ]
        self.assertEqual(["A", "C"], [item.controller for item in applicable])
        self.assertEqual(
            [source_program.key, observer_program.key],
            [item.semantic_key for item in applicable],
        )

        departure_session = self.session(121068)
        departure_engine = departure_session.engine
        departure_engine.state.active_player = "A"
        departure_engine.state.phase_index = 5
        departure_engine.state.phase = "combat"
        departure_engine.state.step = "declare_attackers"
        departure_engine.state.combat = CombatState()
        departed = self.add_card(
            departure_engine,
            seat="A",
            name="Generic Attack Growth Trigger Fixture",
            ref="source-combat-growth-departure",
            zone="battlefield",
        )
        departed_program = self.register_typed_event_trigger(
            departure_engine,
            departed,
        )
        departure_engine._issue_attackers()
        departure_result = departure_session.act(
            "pilot:A",
            {"a": "attack", "atk": {departed.ref: "B"}},
        )
        self.assertTrue(departure_result.ok, departure_result.summary)
        self.assertTrue(
            any(
                item.semantic_key == departed_program.key
                for item in departure_engine.state.stack
            )
        )
        departure_engine.move_card(
            departed.object_id,
            "graveyard",
            reason="source combat growth incarnation witness",
        )
        self.resolve_top(departure_engine)
        self.assertEqual("graveyard", departed.zone)
        self.assertEqual([], departure_engine.state.continuous_effects)


if __name__ == "__main__":
    unittest.main()
