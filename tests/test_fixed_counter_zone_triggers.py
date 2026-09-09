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


class FixedCounterZoneTriggerRuntimeTests(unittest.TestCase):
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
                        FixedCounterZoneTriggerRuntimeTests._event_condition_fields(
                            value if isinstance(value, Mapping) else None
                        )
                    )
        nested = condition.get("not")
        fields.update(
            FixedCounterZoneTriggerRuntimeTests._event_condition_fields(
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


    def test_land_entry_counter_trigger_uses_normalized_event(self):
        session = self.session(120002)
        engine = session.engine
        source = self.add_card(
            engine,
            seat="A",
            name="Landfall Counter Trigger Fixture",
            ref="landfall-counter-source",
            zone="battlefield",
        )
        program = self.register_trigger(engine, source)
        land = self.add_card(
            engine,
            seat="A",
            name="Forest",
            ref="landfall-entering-land",
            zone="hand",
        )

        engine.move_card(
            land.object_id,
            "battlefield",
            reason="Fixed counter Landfall fixture",
            semantic_events=True,
        )
        engine._stabilize()

        self.assertEqual(program.key, engine.state.stack[-1].semantic_key)
        self.assertEqual("land.enter", engine.state.stack[-1].context["event"])
        self.assertEqual(land.ref, engine.state.stack[-1].context["card"])
        self.resolve_top(engine)
        self.assertEqual(1, source.counters.get("+1/+1"))

    def test_zone_entry_counter_triggers_apply_typed_subject_relations(self):
        session = self.session(120014)
        engine = session.engine
        source = self.add_card(
            engine,
            seat="A",
            name="Artifact Entry Counter Trigger Fixture",
            ref="controlled-artifact-entry-source",
            zone="battlefield",
        )
        program = self.register_trigger(engine, source)
        opponent_artifact = self.add_card(
            engine,
            seat="B",
            name="Sol Ring",
            ref="opponent-entering-artifact",
            zone="hand",
        )
        engine.move_card(
            opponent_artifact.object_id,
            "battlefield",
            reason="opponent artifact entry",
            semantic_events=True,
        )
        engine._stabilize()
        self.assertFalse(engine.state.stack)

        controlled_artifact = self.add_card(
            engine,
            seat="A",
            name="Sol Ring",
            ref="controlled-entering-artifact",
            zone="hand",
        )
        engine.move_card(
            controlled_artifact.object_id,
            "battlefield",
            reason="controlled artifact entry",
            semantic_events=True,
        )
        engine._stabilize()
        self.assertEqual(program.key, engine.state.stack[-1].semantic_key)
        self.assertEqual(
            controlled_artifact.ref,
            engine.state.stack[-1].context["card"],
        )
        self.resolve_top(engine)
        self.assertEqual(1, source.counters.get("charge"))

        other_session = self.session(120015)
        other_engine = other_session.engine
        other_source = self.add_card(
            other_engine,
            seat="A",
            name="Other Artifact Entry Counter Trigger Fixture",
            ref="other-artifact-entry-source",
            zone="hand",
        )
        other_program = self.register_trigger(other_engine, other_source)
        other_engine.move_card(
            other_source.object_id,
            "battlefield",
            reason="source artifact entry",
            semantic_events=True,
        )
        other_engine._stabilize()
        self.assertFalse(other_engine.state.stack)

        unrelated = self.add_card(
            other_engine,
            seat="B",
            name="Sol Ring",
            ref="unrelated-entering-artifact",
            zone="hand",
        )
        other_engine.move_card(
            unrelated.object_id,
            "battlefield",
            reason="another artifact entry",
            semantic_events=True,
        )
        other_engine._stabilize()
        self.assertEqual(
            other_program.key,
            other_engine.state.stack[-1].semantic_key,
        )
        self.resolve_top(other_engine)
        self.assertEqual(1, other_source.counters.get("charge"))

    def test_subtype_entry_trigger_filters_and_uses_replacement_owner(self):
        session = self.session(120028)
        engine = session.engine
        record = self.db.lookup("Champion of the Parish")
        program = next(
            value
            for value in generated_programs(
                self.db,
                record,
                trust_level="trusted",
                capability_registry=self.capabilities,
                capability_profile="commander_review",
            )
            if value.provenance.get("template_id")
            == "fixed-counter-subtype-entry-trigger-v1"
        )
        engine.semantics.put(program)
        source = self.add_card(
            engine,
            seat="A",
            name="Champion of the Parish",
            ref="subtype-entry-source",
            zone="battlefield",
        )
        vorinclex = self.add_card(
            engine,
            seat="A",
            name="Vorinclex, Monstrous Raider",
            ref="subtype-entry-vorinclex",
            zone="battlefield",
        )
        register_generated_programs(
            self.db,
            engine.semantics,
            (self.db.lookup("Vorinclex, Monstrous Raider"),),
            trust_level="provisional",
            capability_registry=self.capabilities,
            capability_profile="commander_review",
            promote_exact_runtime_handlers=True,
        )

        opponent_human = self.add_card(
            engine,
            seat="B",
            name="Mishra, Eminent One",
            ref="opponent-entering-human",
            zone="hand",
        )
        engine.move_card(
            opponent_human.object_id,
            "battlefield",
            reason="opponent Human subtype near miss",
            semantic_events=True,
        )
        engine._stabilize()
        self.assertFalse(engine.state.stack)

        controlled_nonhuman = self.add_card(
            engine,
            seat="A",
            name="Sol Ring",
            ref="controlled-entering-nonhuman",
            zone="hand",
        )
        engine.move_card(
            controlled_nonhuman.object_id,
            "battlefield",
            reason="controlled subtype near miss",
            semantic_events=True,
        )
        engine._stabilize()
        self.assertFalse(engine.state.stack)

        controlled_human = self.add_card(
            engine,
            seat="A",
            name="Mishra, Eminent One",
            ref="controlled-entering-human",
            zone="hand",
        )
        engine.move_card(
            controlled_human.object_id,
            "battlefield",
            reason="controlled Human subtype match",
            semantic_events=True,
        )
        engine._stabilize()
        self.assertEqual(program.key, engine.state.stack[-1].semantic_key)
        self.assertIn("human", engine.state.stack[-1].context["subtypes"])
        self.resolve_top(engine)

        self.assertEqual(2, source.counters.get("+1/+1"))
        replacement_event = next(
            event
            for event in reversed(engine.state.events)
            if event.code == "replacement.apply"
        )
        self.assertEqual(vorinclex.ref, replacement_event.details["source"])

    def test_creature_death_counter_trigger_uses_lki_subject_filters(self):
        session = self.session(120016)
        engine = session.engine
        source = self.add_card(
            engine,
            seat="A",
            name="Creature Death Counter Trigger Fixture",
            ref="death-counter-source",
            zone="battlefield",
        )
        program = self.register_trigger(engine, source)

        controlled_token = self.add_card(
            engine,
            seat="A",
            name="Scute Swarm",
            ref="controlled-death-token",
            zone="battlefield",
            is_token=True,
        )
        engine.move_card(
            controlled_token.object_id,
            "graveyard",
            reason="controlled token death",
            semantic_events=True,
        )
        engine._stabilize()
        self.assertFalse(engine.state.stack)

        opponent_creature = self.add_card(
            engine,
            seat="B",
            name="Scute Swarm",
            ref="opponent-death-creature",
            zone="battlefield",
        )
        engine.move_card(
            opponent_creature.object_id,
            "graveyard",
            reason="opponent creature death",
            semantic_events=True,
        )
        engine._stabilize()
        self.assertFalse(engine.state.stack)

        controlled_creature = self.add_card(
            engine,
            seat="A",
            name="Scute Swarm",
            ref="controlled-death-creature",
            zone="battlefield",
        )
        previous_identity = controlled_creature.logical_object_id
        engine.move_card(
            controlled_creature.object_id,
            "graveyard",
            reason="controlled creature death",
            semantic_events=True,
        )
        engine._stabilize()
        item = engine.state.stack[-1]
        self.assertEqual(program.key, item.semantic_key)
        self.assertEqual("creature.dies", item.context["event"])
        self.assertEqual("A", item.context["previous_controller"])
        self.assertEqual(previous_identity, item.context["card_object_identity"])
        self.resolve_top(engine)
        self.assertEqual(1, source.counters.get("+1/+1"))

        engine.move_card(
            source.object_id,
            "graveyard",
            reason="counter source death",
            semantic_events=True,
        )
        engine._stabilize()
        self.assertFalse(engine.state.stack)

    def test_opponent_death_counter_trigger_uses_previous_controller(self):
        session = self.session(120017, players=4)
        engine = session.engine
        source = self.add_card(
            engine,
            seat="C",
            name="Opponent Death Counter Trigger Fixture",
            ref="opponent-death-counter-source",
            zone="battlefield",
        )
        program = self.register_trigger(engine, source)
        controlled = self.add_card(
            engine,
            seat="C",
            name="Scute Swarm",
            ref="same-controller-death-creature",
            zone="battlefield",
        )
        engine.move_card(
            controlled.object_id,
            "graveyard",
            reason="same-controller creature death",
            semantic_events=True,
        )
        engine._stabilize()
        self.assertFalse(engine.state.stack)

        opponent = self.add_card(
            engine,
            seat="D",
            name="Scute Swarm",
            ref="different-controller-death-creature",
            zone="battlefield",
        )
        engine.move_card(
            opponent.object_id,
            "graveyard",
            reason="opponent creature death",
            semantic_events=True,
        )
        engine._stabilize()
        self.assertEqual(program.key, engine.state.stack[-1].semantic_key)
        self.assertEqual(
            "D",
            engine.state.stack[-1].context["previous_controller"],
        )
        self.resolve_top(engine)
        self.assertEqual(1, source.counters.get("+1/+1"))

    def test_any_death_trigger_observes_opponents_and_its_own_lki(self):
        session = self.session(120019, players=4)
        engine = session.engine
        source = self.add_card(
            engine,
            seat="A",
            name="Any Creature Death Counter Trigger Fixture",
            ref="any-death-counter-source",
            zone="battlefield",
        )
        program = self.register_trigger(engine, source)
        opponent = self.add_card(
            engine,
            seat="D",
            name="Scute Swarm",
            ref="any-death-opponent-creature",
            zone="battlefield",
        )
        engine.move_card(
            opponent.object_id,
            "graveyard",
            reason="any-controller creature death",
            semantic_events=True,
        )
        engine._stabilize()
        self.assertEqual(program.key, engine.state.stack[-1].semantic_key)
        self.assertEqual(
            "D",
            engine.state.stack[-1].context["previous_controller"],
        )
        self.resolve_top(engine)
        self.assertEqual(1, source.counters.get("+1/+1"))

        previous_identity = source.logical_object_id
        engine.move_card(
            source.object_id,
            "graveyard",
            reason="source creature death",
            semantic_events=True,
        )
        engine._stabilize()
        self.assertEqual(program.key, engine.state.stack[-1].semantic_key)
        self.assertEqual(
            previous_identity,
            engine.state.stack[-1].context["card_object_identity"],
        )
        self.assertEqual(
            "battlefield",
            engine.state.stack[-1].context["source_zone"],
        )

    def test_death_counter_replacement_is_private_and_replays_exactly(self):
        session = self.session(120018, players=4)
        engine = session.engine
        source = self.add_card(
            engine,
            seat="C",
            name="Creature Death Counter Trigger Fixture",
            ref="private-death-counter-source",
            zone="battlefield",
        )
        self.add_card(
            engine,
            seat="C",
            name="Doubling Season",
            ref="private-death-doubling",
            zone="battlefield",
        )
        self.add_card(
            engine,
            seat="C",
            name="Doc Samson, Super Psychiatrist",
            ref="private-death-addition",
            zone="battlefield",
        )
        self.register_trigger(engine, source)
        departed = self.add_card(
            engine,
            seat="C",
            name="Scute Swarm",
            ref="private-death-creature",
            zone="battlefield",
        )

        engine.move_card(
            departed.object_id,
            "graveyard",
            reason="private replacement death",
            semantic_events=True,
        )
        engine._stabilize()
        self.resolve_top(engine)

        self.assertEqual("replacement.order", engine.state.pending_decision.kind)
        projector = StateProjector(self.db, engine.state)
        for seat in ("A", "B", "D"):
            self.assertIsNone(projector._decision(f"pilot:{seat}"))
        projected = projector._decision("pilot:C")
        self.assertIsNotNone(projected)
        self.assertNotIn(source.object_id, json.dumps(projected, sort_keys=True))

        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()
        self.finish_replacements(session, "C")

        self.assertIn(source.counters.get("+1/+1"), {3, 4})
        expected_hash = authoritative_state_hash(engine.state)
        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / "death-counter-trigger-replay"
            session.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(expected_hash, replay["final_state_hash"])

    def test_scheduled_counter_trigger_suspends_for_quantity_replacement(self):
        session = self.session(120003)
        engine = session.engine
        source = self.add_card(
            engine,
            seat="A",
            name="Scheduled Counter Trigger Fixture",
            ref="scheduled-replacement-source",
            zone="battlefield",
        )
        self.add_card(
            engine,
            seat="A",
            name="Doubling Season",
            ref="scheduled-doubling-season",
            zone="battlefield",
        )
        self.add_card(
            engine,
            seat="A",
            name="Doc Samson, Super Psychiatrist",
            ref="scheduled-doc-samson",
            zone="battlefield",
        )
        self.register_trigger(engine, source)

        engine._dispatch_semantic_event(
            "step.begin",
            self.step_context(player="A"),
        )
        engine._stabilize()
        self.resolve_top(engine)

        self.assertEqual("replacement.order", engine.state.pending_decision.kind)
        self.assertFalse(source.counters)
        self.finish_replacements(session, "A")
        self.assertIn(source.counters.get("charge"), {5, 6})

    def test_optional_counter_trigger_choice_composes_with_replacement_and_replay(
        self,
    ):
        session = self.session(120007, players=4)
        engine = session.engine
        source = self.add_card(
            engine,
            seat="C",
            name="Optional Scheduled Counter Trigger Fixture",
            ref="optional-counter-trigger-source",
            zone="battlefield",
        )
        self.add_card(
            engine,
            seat="C",
            name="Doubling Season",
            ref="optional-trigger-doubling",
            zone="battlefield",
        )
        self.add_card(
            engine,
            seat="C",
            name="Doc Samson, Super Psychiatrist",
            ref="optional-trigger-addition",
            zone="battlefield",
        )
        self.register_trigger(engine, source)

        def begin_choice() -> None:
            engine.permissions.invalidate_current()
            engine.state.pending_decision = None
            engine.state.priority_player = None
            engine.state.priority_passes = []
            engine._dispatch_semantic_event(
                "step.begin",
                self.step_context(player="C"),
            )
            engine._stabilize()
            self.resolve_top(engine)
            self.assertEqual(
                "semantic.choice",
                engine.state.pending_decision.kind,
            )

        begin_choice()
        declined = session.act(
            "pilot:C",
            {"action_id": "choose", "choice": "decline"},
        )
        self.assertTrue(declined.ok, declined.summary)
        self.assertFalse(source.counters)

        begin_choice()
        projector = StateProjector(self.db, engine.state)
        for seat in ("A", "B", "D"):
            self.assertIsNone(projector._decision(f"pilot:{seat}"))
        projected = projector._decision("pilot:C")
        self.assertIsNotNone(projected)
        self.assertNotIn(source.object_id, json.dumps(projected, sort_keys=True))

        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()
        accepted = session.act(
            "pilot:C",
            {"action_id": "choose", "choice": "put"},
        )
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertEqual(
            "replacement.order",
            engine.state.pending_decision.kind,
        )
        self.assertFalse(source.counters)
        self.finish_replacements(session, "C")
        self.assertIn(source.counters.get("charge"), {5, 6})

        expected_hash = authoritative_state_hash(engine.state)
        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / "optional-counter-trigger-replay"
            session.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(expected_hash, replay["final_state_hash"])

    def test_multiple_scheduled_counter_triggers_use_one_apnap_batch(self):
        session = self.session(120004, players=4)
        engine = session.engine
        engine.state.active_player = "A"
        source_a = self.add_card(
            engine,
            seat="A",
            name="Each Upkeep Counter Trigger Fixture",
            ref="apnap-counter-source-a",
            zone="battlefield",
        )
        source_c = self.add_card(
            engine,
            seat="C",
            name="Each Upkeep Counter Trigger Fixture",
            ref="apnap-counter-source-c",
            zone="battlefield",
        )
        self.register_trigger(engine, source_a)
        self.register_trigger(engine, source_c)

        items = collect_trigger_items(
            engine,
            "step.begin",
            self.step_context(player="A"),
        )
        self.assertEqual({"A", "C"}, {item.controller for item in items})
        enqueue_trigger_batch(engine, items)
        self.assertEqual(1, len(engine.state.pending_trigger_batches))
        self.assertEqual(
            ["A", "B", "C", "D"],
            list(engine.state.pending_trigger_batches[0].apnap_order),
        )

        engine._stabilize()

        self.assertEqual(
            ["A", "C"],
            [item.controller for item in engine.state.stack[-2:]],
        )
        self.assertEqual(
            {source_a.object_id, source_c.object_id},
            {item.source_object_id for item in engine.state.stack[-2:]},
        )

    def test_four_player_counter_trigger_choice_is_private_and_replays_exactly(
        self,
    ):
        session = self.session(120005, players=4)
        engine = session.engine
        source = self.add_card(
            engine,
            seat="C",
            name="Scheduled Counter Trigger Fixture",
            ref="private-counter-trigger-source",
            zone="battlefield",
        )
        self.add_card(
            engine,
            seat="C",
            name="Doubling Season",
            ref="private-trigger-doubling",
            zone="battlefield",
        )
        self.add_card(
            engine,
            seat="C",
            name="Doc Samson, Super Psychiatrist",
            ref="private-trigger-addition",
            zone="battlefield",
        )
        self.register_trigger(engine, source)

        engine._dispatch_semantic_event(
            "step.begin",
            self.step_context(player="C"),
        )
        engine._stabilize()
        self.resolve_top(engine)
        projector = StateProjector(self.db, engine.state)
        for seat in ("A", "B", "D"):
            self.assertIsNone(projector._decision(f"pilot:{seat}"))
        projected = projector._decision("pilot:C")
        self.assertIsNotNone(projected)
        self.assertNotIn(source.object_id, json.dumps(projected, sort_keys=True))

        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()
        self.finish_replacements(session, "C")

        self.assertIn(source.counters.get("charge"), {5, 6})
        expected_hash = authoritative_state_hash(engine.state)
        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / "fixed-counter-trigger-replay"
            session.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(expected_hash, replay["final_state_hash"])

    def test_stale_counter_target_rolls_back_trigger_resolution(self):
        session = self.session(120006)
        engine = session.engine
        source = self.add_card(
            engine,
            seat="A",
            name="Targeted Scheduled Counter Trigger Fixture",
            ref="targeted-counter-trigger-source",
            zone="battlefield",
        )
        target = self.add_card(
            engine,
            seat="A",
            name="Scute Swarm",
            ref="stale-counter-trigger-target",
            zone="battlefield",
        )
        self.register_trigger(engine, source)

        engine._dispatch_semantic_event(
            "step.begin",
            self.step_context(player="A", step="beginning_combat"),
        )
        engine._stabilize()
        self.assertEqual("semantic.target", engine.state.pending_decision.kind)
        selected = session.act(
            "pilot:A",
            {"action_id": "choose", "targets": [target.ref]},
        )
        self.assertTrue(selected.ok, selected.summary)
        target = engine.state.cards[target.object_id]
        engine.move_card(target.object_id, "graveyard", log=False)
        counter_snapshot = {
            object_id: dict(card.counters)
            for object_id, card in engine.state.cards.items()
        }

        self.resolve_top(engine)

        self.assertEqual("graveyard", target.zone)
        self.assertEqual(
            counter_snapshot,
            {
                object_id: dict(card.counters)
                for object_id, card in engine.state.cards.items()
            },
        )

    def test_fixed_typed_event_effect_trigger_resolves_targeted_body_and_replays(
        self,
    ):
        session = self.session(121001, players=4)
        engine = session.engine
        engine.state.active_player = "C"
        source = self.add_card(
            engine,
            seat="C",
            name="Typed Scheduled Target Trigger Fixture",
            ref="typed-target-trigger-source",
            zone="battlefield",
        )
        target = self.add_card(
            engine,
            seat="C",
            name="Scute Swarm",
            ref="typed-target-trigger-target",
            zone="battlefield",
        )
        program = self.register_typed_event_trigger(engine, source)

        engine._dispatch_semantic_event(
            "step.begin",
            self.step_context(player="C", step="beginning_combat"),
        )
        engine._stabilize()

        self.assertEqual(program.key, engine.state.stack[-1].semantic_key)
        self.assertEqual("semantic.target", engine.state.pending_decision.kind)
        projector = StateProjector(self.db, engine.state)
        for seat in ("A", "B", "D"):
            self.assertIsNone(projector._decision(f"pilot:{seat}"))
        projected = projector._decision("pilot:C")
        self.assertIsNotNone(projected)
        self.assertNotIn(source.object_id, json.dumps(projected, sort_keys=True))

        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()
        selected = session.act(
            "pilot:C",
            {"action_id": "choose", "targets": [target.ref]},
        )
        self.assertTrue(selected.ok, selected.summary)
        for seat in ("C", "D", "A", "B"):
            passed = session.act(
                f"pilot:{seat}",
                {"action_id": "pass"},
            )
            self.assertTrue(passed.ok, passed.summary)

        self.assertEqual(3, engine._numeric_stat(target.object_id, "power"))
        expected_hash = authoritative_state_hash(engine.state)
        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / "fixed-typed-event-trigger-replay"
            session.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(expected_hash, replay["final_state_hash"])

    def test_creature_subtype_dispatches_death_and_graveyard_wordings(self):
        session = self.session(121013)
        engine = session.engine
        source = self.add_card(
            engine,
            seat="A",
            name="Typed Subtype Graveyard Trigger Fixture",
            ref="subtype-wording-source",
            zone="battlefield",
        )
        dies = self.register_subtype_graveyard_trigger(
            engine,
            source,
            event="creature.dies",
            qualifier_field="card",
        )
        graveyard = self.register_subtype_graveyard_trigger(
            engine,
            source,
            event="permanent.graveyard",
            qualifier_field="card",
        )
        departed = self.add_card(
            engine,
            seat="B",
            name="Generic Creature Goblin Fixture",
            ref="creature-goblin-departure",
            zone="battlefield",
        )

        engine.move_card(
            departed.object_id,
            "graveyard",
            reason="creature Goblin departure",
            semantic_events=True,
        )

        self.assertEqual(1, len(engine.state.pending_trigger_batches))
        pending = engine.state.pending_trigger_batches[0].items
        self.assertEqual(2, len(pending))
        self.assertEqual(
            {dies.key, graveyard.key},
            {item.source_ability_id for item in pending},
        )
        self.assertEqual(
            {"creature.dies", "permanent.graveyard"},
            {item.normalized_event_id for item in pending},
        )
        engine._stabilize()
        self.assertEqual("trigger.order", engine.state.pending_decision.kind)
        ordered = session.act(
            "pilot:A",
            {
                "action_id": "order",
                "triggers": [item.ref for item in pending],
            },
        )
        self.assertTrue(ordered.ok, ordered.summary)

        items = [
            item
            for item in engine.state.stack
            if item.semantic_key in {dies.key, graveyard.key}
        ]
        self.assertEqual(2, len(items))
        self.assertEqual(
            {"creature.dies", "permanent.graveyard"},
            {item.context["event"] for item in items},
        )
        self.assertTrue(
            all("goblin" in item.context["subtypes"] for item in items)
        )

    def test_noncreature_kindred_subtype_only_dispatches_graveyard_wording(self):
        session = self.session(121014)
        engine = session.engine
        source = self.add_card(
            engine,
            seat="A",
            name="Typed Subtype Graveyard Trigger Fixture",
            ref="kindred-wording-source",
            zone="battlefield",
        )
        dies = self.register_subtype_graveyard_trigger(
            engine,
            source,
            event="creature.dies",
            qualifier_field="card",
        )
        graveyard = self.register_subtype_graveyard_trigger(
            engine,
            source,
            event="permanent.graveyard",
            qualifier_field="card",
        )
        departed = self.add_card(
            engine,
            seat="B",
            name="Generic Kindred Goblin Fixture",
            ref="kindred-goblin-departure",
            zone="battlefield",
        )

        engine.move_card(
            departed.object_id,
            "graveyard",
            reason="noncreature Kindred Goblin departure",
            semantic_events=True,
        )
        engine._stabilize()

        items = [
            item
            for item in engine.state.stack
            if item.semantic_key in {dies.key, graveyard.key}
        ]
        self.assertEqual(1, len(items))
        self.assertEqual(graveyard.key, items[0].semantic_key)
        self.assertEqual("permanent.graveyard", items[0].context["event"])
        self.assertIn("kindred", items[0].context["types"])
        self.assertNotIn("creature", items[0].context["types"])

    def test_subtype_graveyard_another_and_nontoken_filters_use_lki(self):
        for qualifier, is_token, depart_source, expected in (
            ("card", False, True, False),
            ("token", True, False, False),
            ("token", False, False, True),
        ):
            with self.subTest(
                qualifier=qualifier,
                is_token=is_token,
                depart_source=depart_source,
            ):
                session = self.session(
                    121015 + int(is_token) + int(depart_source)
                )
                engine = session.engine
                source = self.add_card(
                    engine,
                    seat="A",
                    name="Typed Subtype Graveyard Trigger Fixture",
                    ref="subtype-filter-source",
                    zone="battlefield",
                )
                program = self.register_subtype_graveyard_trigger(
                    engine,
                    source,
                    event="permanent.graveyard",
                    qualifier_field=qualifier,
                )
                departed = source
                if not depart_source:
                    departed = self.add_card(
                        engine,
                        seat="B",
                        name="Generic Creature Goblin Fixture",
                        ref="subtype-filter-departure",
                        zone="battlefield",
                        is_token=is_token,
                    )

                engine.move_card(
                    departed.object_id,
                    "graveyard",
                    reason="subtype predicate departure",
                    semantic_events=True,
                )
                engine._stabilize()

                matched = any(
                    item.semantic_key == program.key
                    for item in engine.state.stack
                )
                self.assertEqual(expected, matched)

    def test_subtype_graveyard_control_and_owner_filters_use_lki(self):
        session = self.session(121018)
        engine = session.engine
        source = self.add_card(
            engine,
            seat="A",
            name="Typed Subtype Graveyard Trigger Fixture",
            ref="subtype-control-source",
            zone="battlefield",
        )
        controlled = self.register_subtype_graveyard_trigger(
            engine,
            source,
            event="permanent.graveyard",
            qualifier_field="previous_controller",
        )
        departed = self.add_card(
            engine,
            seat="B",
            name="Generic Creature Goblin Fixture",
            ref="control-changed-goblin",
            zone="battlefield",
        )
        engine.change_control(
            departed.object_id,
            "A",
            reason="focused control-change witness",
        )

        engine.move_card(
            departed.object_id,
            "graveyard",
            reason="controlled Goblin departure",
            semantic_events=True,
        )
        engine._stabilize()

        item = next(
            value
            for value in engine.state.stack
            if value.semantic_key == controlled.key
        )
        self.assertEqual("A", item.context["previous_controller"])
        self.assertEqual("B", item.context["owner"])
        self.assertEqual("B", departed.controller)

        for owner, controller, expected in (
            ("A", "B", True),
            ("B", "A", False),
        ):
            with self.subTest(owner=owner, controller=controller):
                session = self.session(121019 + (owner == "B"))
                engine = session.engine
                source = self.add_card(
                    engine,
                    seat="A",
                    name="Typed Subtype Graveyard Trigger Fixture",
                    ref="subtype-owner-source",
                    zone="battlefield",
                )
                owned = self.register_subtype_graveyard_trigger(
                    engine,
                    source,
                    event="permanent.graveyard",
                    qualifier_field="owner",
                )
                departed = self.add_card(
                    engine,
                    seat=owner,
                    name="Generic Creature Goblin Fixture",
                    ref="owner-filter-goblin",
                    zone="battlefield",
                )
                if controller != owner:
                    engine.change_control(
                        departed.object_id,
                        controller,
                        reason="focused ownership witness",
                    )

                engine.move_card(
                    departed.object_id,
                    "graveyard",
                    reason="owner-filtered Goblin departure",
                    semantic_events=True,
                )
                engine._stabilize()

                matched = any(
                    value.semantic_key == owned.key
                    for value in engine.state.stack
                )
                self.assertIn(
                    departed.object_id,
                    engine.state.players[owner].zones["graveyard"],
                )
                self.assertEqual(expected, matched)

    def test_subtype_graveyard_triggers_preserve_apnap_and_exact_replay(self):
        session = self.session(121021, players=4)
        engine = session.engine
        engine.state.active_player = "A"
        source_a = self.add_card(
            engine,
            seat="A",
            name="Typed Subtype Graveyard Trigger Fixture",
            ref="subtype-apnap-source-a",
            zone="battlefield",
        )
        self.add_card(
            engine,
            seat="C",
            name="Typed Subtype Graveyard Trigger Fixture",
            ref="subtype-apnap-source-c",
            zone="battlefield",
        )
        program = self.register_subtype_graveyard_trigger(
            engine,
            source_a,
            event="permanent.graveyard",
            qualifier_field="card",
        )
        departed = self.add_card(
            engine,
            seat="B",
            name="Generic Creature Goblin Fixture",
            ref="subtype-apnap-departure",
            zone="battlefield",
        )

        engine.move_card(
            departed.object_id,
            "graveyard",
            reason="APNAP subtype graveyard occurrence",
            semantic_events=True,
        )

        self.assertEqual(1, len(engine.state.pending_trigger_batches))
        batch = engine.state.pending_trigger_batches[0]
        self.assertEqual(("A", "B", "C", "D"), batch.apnap_order)
        self.assertEqual(
            ["A", "C"],
            [item.controller for item in batch.items],
        )
        self.assertTrue(
            all(
                item.source_ability_id == program.key
                and item.normalized_event_id == "permanent.graveyard"
                for item in batch.items
            )
        )

        engine._stabilize()
        self.assertEqual(
            ["A", "C"],
            [item.controller for item in engine.state.stack[-2:]],
        )
        self.assertTrue(
            all(
                item.context["event"] == "permanent.graveyard"
                for item in engine.state.stack[-2:]
            )
        )

        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()
        expected_hash = authoritative_state_hash(engine.state)
        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / "subtype-graveyard-apnap-replay"
            session.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(expected_hash, replay["final_state_hash"])

    def test_fixed_typed_event_effect_triggers_share_four_player_apnap_batch(
        self,
    ):
        session = self.session(121002, players=4)
        engine = session.engine
        engine.state.active_player = "A"
        source_a = self.add_card(
            engine,
            seat="A",
            name="Typed Each Upkeep Life Trigger Fixture",
            ref="typed-apnap-source-a",
            zone="battlefield",
        )
        source_c = self.add_card(
            engine,
            seat="C",
            name="Typed Each Upkeep Life Trigger Fixture",
            ref="typed-apnap-source-c",
            zone="battlefield",
        )
        self.register_typed_event_trigger(engine, source_a)
        self.register_typed_event_trigger(engine, source_c)

        items = collect_trigger_items(
            engine,
            "step.begin",
            self.step_context(player="A"),
        )
        self.assertEqual({"A", "C"}, {item.controller for item in items})
        self.assertTrue(
            all(
                item.semantic_key in {
                    program.key
                    for program in engine.semantics.programs()
                    if program.provenance.get("template_id")
                    in FIXED_TYPED_EVENT_TEMPLATE_IDS
                }
                for item in items
            )
        )
        enqueue_trigger_batch(engine, items)
        self.assertEqual(1, len(engine.state.pending_trigger_batches))
        self.assertEqual(
            ["A", "B", "C", "D"],
            list(engine.state.pending_trigger_batches[0].apnap_order),
        )
        engine._stabilize()
        self.assertEqual(
            ["A", "C"],
            [item.controller for item in engine.state.stack[-2:]],
        )

    def test_source_zone_lifecycle_uses_last_known_controller_and_replays(
        self,
    ):
        session = self.session(121008, players=4)
        engine = session.engine
        source = self.add_card(
            engine,
            seat="C",
            controller="B",
            name="Typed Artifact Graveyard Draw Trigger Fixture",
            ref="typed-source-graveyard",
            zone="battlefield",
        )
        program = self.register_typed_event_trigger(engine, source)
        previous_identity = source.logical_object_id
        hand_before = len(engine.state.players["B"].zones["hand"])

        engine.move_card(
            source.object_id,
            "graveyard",
            reason="typed source graveyard occurrence",
            semantic_events=True,
        )
        engine._stabilize()

        item = next(
            value
            for value in engine.state.stack
            if value.semantic_key == program.key
        )
        self.assertEqual("B", item.controller)
        self.assertEqual("permanent.graveyard", item.context["event"])
        self.assertEqual(source.ref, item.context["card"])
        self.assertEqual(previous_identity, item.context["card_object_identity"])
        self.assertEqual("battlefield", item.context["source_zone"])
        self.assertEqual("C", source.controller)

        engine.state.priority_player = engine.state.active_player
        engine._issue_priority(engine.state.active_player)
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()
        for _ in range(12):
            if not engine.state.stack:
                break
            pass_current(session)
        self.assertFalse(engine.state.stack)
        self.assertEqual(
            hand_before + 1,
            len(engine.state.players["B"].zones["hand"]),
        )

        expected_hash = authoritative_state_hash(engine.state)
        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / "typed-source-graveyard-trigger"
            session.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(expected_hash, replay["final_state_hash"])

    def test_source_damage_bindings_use_committed_damage_events(self):
        session = self.session(121009, players=4)
        engine = session.engine
        source = self.add_card(
            engine,
            seat="A",
            name="Typed Combat Damage Draw Trigger Fixture",
            ref="typed-combat-damage-source",
            zone="battlefield",
        )
        program = self.register_typed_event_trigger(engine, source)
        hand_before = len(engine.state.players["A"].zones["hand"])

        for target, combat, suffix in (("B", False, "noncombat"),):
            resolve_damage_batch(
                engine,
                (
                    damage_proposal(
                        engine,
                        proposal_id=f"typed-source-damage:{suffix}",
                        actor="A",
                        source_ref=source.ref,
                        target=target,
                        amount=1,
                        combat=combat,
                        reason="typed source damage negative witness",
                    ),
                ),
            )
            engine._stabilize()
            self.assertFalse(
                any(
                    item.semantic_key == program.key
                    for item in engine.state.stack
                )
            )

        resolve_damage_batch(
            engine,
            (
                damage_proposal(
                    engine,
                    proposal_id="typed-source-damage:combat-player",
                    actor="A",
                    source_ref=source.ref,
                    target="B",
                    amount=1,
                    combat=True,
                    reason="typed source combat damage witness",
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
        self.assertEqual(source.ref, item.context["card"])
        self.assertEqual("B", item.context["target"])
        self.assertEqual("player", item.context["target_kind"])
        self.assertTrue(item.context["combat"])
        for seat in engine.active_seats:
            packet_text = json.dumps(
                session.packet(f"pilot:{seat}", full=True),
                sort_keys=True,
            )
            self.assertNotIn(source.object_id, packet_text)
            self.assertNotIn(source.logical_object_id, packet_text)

        engine.state.priority_player = engine.state.active_player
        engine._issue_priority(engine.state.active_player)
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()
        for _ in range(12):
            if not engine.state.stack:
                break
            pass_current(session)
        self.assertEqual(
            hand_before + 1,
            len(engine.state.players["A"].zones["hand"]),
        )
        expected_hash = authoritative_state_hash(engine.state)
        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / "typed-source-damage-trigger"
            session.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(expected_hash, replay["final_state_hash"])

        opponent_session = self.session(121010, players=4)
        opponent_engine = opponent_session.engine
        opponent_source = self.add_card(
            opponent_engine,
            seat="C",
            name="Typed Opponent Damage Draw Trigger Fixture",
            ref="typed-opponent-damage-source",
            zone="battlefield",
        )
        opponent_program = self.register_typed_event_trigger(
            opponent_engine,
            opponent_source,
        )
        resolve_damage_batch(
            opponent_engine,
            (
                damage_proposal(
                    opponent_engine,
                    proposal_id="typed-opponent-damage:controller",
                    actor="C",
                    source_ref=opponent_source.ref,
                    target="C",
                    amount=1,
                    combat=False,
                    reason="typed opponent relation negative witness",
                ),
            ),
        )
        opponent_engine._stabilize()
        self.assertFalse(opponent_engine.state.stack)
        resolve_damage_batch(
            opponent_engine,
            (
                damage_proposal(
                    opponent_engine,
                    proposal_id="typed-opponent-damage:opponent",
                    actor="C",
                    source_ref=opponent_source.ref,
                    target="D",
                    amount=1,
                    combat=False,
                    reason="typed opponent relation witness",
                ),
            ),
        )
        opponent_engine._stabilize()
        self.assertEqual(
            opponent_program.key,
            opponent_engine.state.stack[-1].semantic_key,
        )

        recipient_session = self.session(121011, players=4)
        recipient_engine = recipient_session.engine
        recipient = self.add_card(
            recipient_engine,
            seat="B",
            name="Typed Dealt Damage Life Trigger Fixture",
            ref="typed-damage-recipient",
            zone="battlefield",
        )
        recipient_program = self.register_typed_event_trigger(
            recipient_engine,
            recipient,
        )
        damage_source = self.add_card(
            recipient_engine,
            seat="A",
            name="Typed Combat Damage Draw Trigger Fixture",
            ref="typed-recipient-damage-source",
            zone="battlefield",
        )
        life_before = recipient_engine.state.players["B"].life
        resolve_damage_batch(
            recipient_engine,
            (
                damage_proposal(
                    recipient_engine,
                    proposal_id="typed-recipient-damage:permanent",
                    actor="A",
                    source_ref=damage_source.ref,
                    target=recipient.ref,
                    amount=1,
                    combat=False,
                    reason="typed dealt-damage recipient witness",
                ),
            ),
        )
        recipient_engine._stabilize()
        self.assertEqual(
            recipient_program.key,
            recipient_engine.state.stack[-1].semantic_key,
        )
        self.resolve_top(recipient_engine)
        self.assertEqual(
            life_before + 1,
            recipient_engine.state.players["B"].life,
        )

    def test_source_damage_event_dispatch_mutant_is_killed(self):
        session = self.session(121012)
        engine = session.engine
        source = self.add_card(
            engine,
            seat="A",
            name="Typed Combat Damage Draw Trigger Fixture",
            ref="typed-damage-dispatch-mutant",
            zone="battlefield",
        )
        program = self.register_typed_event_trigger(engine, source)

        with patch.object(
            engine,
            "_dispatch_semantic_event",
            return_value=[],
        ):
            resolve_damage_batch(
                engine,
                (
                    damage_proposal(
                        engine,
                        proposal_id="typed-source-damage:dispatch-mutant",
                        actor="A",
                        source_ref=source.ref,
                        target="B",
                        amount=1,
                        combat=True,
                        reason="typed source damage dispatch mutation",
                    ),
                ),
            )
        engine._stabilize()
        self.assertFalse(
            any(
                item.semantic_key == program.key
                for item in engine.state.stack
            )
        )


if __name__ == "__main__":
    unittest.main()
