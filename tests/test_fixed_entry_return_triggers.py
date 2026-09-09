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


class FixedEntryReturnTriggerRuntimeTests(unittest.TestCase):
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
                        FixedEntryReturnTriggerRuntimeTests._event_condition_fields(
                            value if isinstance(value, Mapping) else None
                        )
                    )
        nested = condition.get("not")
        fields.update(
            FixedEntryReturnTriggerRuntimeTests._event_condition_fields(
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


    def test_entry_return_choice_uses_owner_hand_and_replays(self):
        session = self.session(121069, players=4)
        engine = session.engine
        engine.state.active_player = "A"
        source = self.add_card(
            engine,
            seat="A",
            name="Generic Entry Land Return Fixture",
            ref="entry-return-source",
            zone="hand",
        )
        candidate = self.add_card(
            engine,
            seat="B",
            name="Forest",
            ref="entry-return-candidate",
            zone="battlefield",
        )
        engine.change_control(
            candidate.object_id,
            "A",
            reason="entry-return owner-hand witness",
        )
        program = self.register_typed_event_trigger(engine, source)

        engine.move_card(
            source.object_id,
            "battlefield",
            reason="entry-return source entered",
            semantic_events=True,
        )
        engine._stabilize()
        self.assertEqual(program.key, engine.state.stack[-1].semantic_key)
        self.resolve_top(engine)
        self.assertEqual("choice.apnap", engine.state.pending_decision.kind)
        self.assertEqual(["A"], engine.state.pending_decision.actors)
        projected = StateProjector(self.db, engine.state)
        self.assertIsNotNone(projected._decision("pilot:A"))
        for seat in "BCD":
            self.assertIsNone(projected._decision(f"pilot:{seat}"))
        self.assertNotIn(
            candidate.object_id,
            json.dumps(projected._decision("pilot:A"), sort_keys=True),
        )
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()
        result = session.act(
            "pilot:A",
            {"action_id": "choose", "cards": [candidate.ref]},
        )
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("hand", candidate.zone)
        self.assertEqual("B", candidate.owner)
        self.assertIn(candidate.object_id, engine.state.players["B"].zones["hand"])
        self.assertEqual("battlefield", source.zone)
        expected_hash = authoritative_state_hash(engine.state)
        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / "entry-return-choice"
            session.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(expected_hash, replay["final_state_hash"])

    def test_entry_return_untapped_basic_land_subtypes_execute(self):
        for index, subtype in enumerate(
            ("Plains", "Island", "Swamp", "Mountain", "Forest")
        ):
            with self.subTest(subtype=subtype):
                session = self.session(121076 + index)
                engine = session.engine
                source = self.add_card(
                    engine,
                    seat="A",
                    name=f"Generic Untapped {subtype} Entry Return Fixture",
                    ref=f"entry-return-{subtype.casefold()}-source",
                    zone="hand",
                )
                candidate = self.add_card(
                    engine,
                    seat="A",
                    name=f"Generic {subtype} Land Witness",
                    ref=f"entry-return-{subtype.casefold()}-candidate",
                    zone="battlefield",
                )
                self.register_typed_event_trigger(engine, source)

                engine.move_card(
                    source.object_id,
                    "battlefield",
                    reason=f"untapped {subtype} entry-return witness",
                    semantic_events=True,
                )
                engine._stabilize()
                self.resolve_top(engine)
                self.assertEqual("choice.apnap", engine.state.pending_decision.kind)
                result = session.act(
                    "pilot:A",
                    {"action_id": "choose", "cards": [candidate.ref]},
                )

                self.assertTrue(result.ok, result.summary)
                self.assertEqual("hand", candidate.zone)
                self.assertEqual("battlefield", source.zone)

    def test_entry_return_counts_execute_as_many_and_exact_unless(self):
        count_cases = (
            ("One", 1),
            ("Two", 2),
            ("Three", 3),
        )

        def stage(
            *,
            seed: int,
            source_name: str,
            candidate_count: int,
        ):
            session = self.session(seed)
            engine = session.engine
            source = self.add_card(
                engine,
                seat="A",
                name=source_name,
                ref=f"entry-count-source-{seed}",
                zone="hand",
            )
            program = self.register_typed_event_trigger(engine, source)
            candidates = [
                self.add_card(
                    engine,
                    seat="A",
                    name="Generic Entry Dragon Witness",
                    ref=f"entry-count-candidate-{seed}-{index}",
                    zone="battlefield",
                )
                for index in range(candidate_count)
            ]
            engine.move_card(
                source.object_id,
                "battlefield",
                reason="entry return fixed-count witness",
                semantic_events=True,
            )
            engine._stabilize()
            self.assertEqual(program.key, engine.state.stack[-1].semantic_key)
            self.resolve_top(engine)
            return session, source, candidates

        seed = 121086
        for count_label, count in count_cases:
            for available in (max(0, count - 1), count + 1):
                with self.subTest(
                    count=count,
                    available=available,
                    payment="as-many-as-possible",
                ):
                    session, source, candidates = stage(
                        seed=seed,
                        source_name=(
                            f"Generic {count_label} Creature Entry Return Fixture"
                        ),
                        candidate_count=available,
                    )
                    seed += 1
                    selected = candidates[: min(count, available)]
                    if selected:
                        decision = StateProjector(
                            self.db, session.engine.state
                        )._decision("pilot:A")
                        self.assertEqual(len(selected), decision["ctx"]["count"])
                        returned = session.act(
                            "pilot:A",
                            {
                                "action_id": "choose",
                                "cards": [card.ref for card in selected],
                            },
                        )
                        self.assertTrue(returned.ok, returned.summary)
                    else:
                        self.assertIsNone(session.engine.state.pending_decision)
                    self.assertEqual("battlefield", source.zone)
                    self.assertTrue(all(card.zone == "hand" for card in selected))
                    self.assertTrue(
                        all(
                            card.zone == "battlefield"
                            for card in candidates[len(selected) :]
                        )
                    )

            for available in (max(0, count - 1), count):
                with self.subTest(
                    count=count,
                    available=available,
                    payment="exact-unless",
                ):
                    session, source, candidates = stage(
                        seed=seed,
                        source_name=(
                            f"Generic {count_label} Creature Entry Unless Fixture"
                        ),
                        candidate_count=available,
                    )
                    seed += 1
                    selected_return = session.act(
                        "pilot:A",
                        {"action_id": "choose", "choice": "return"},
                    )
                    self.assertTrue(selected_return.ok, selected_return.summary)
                    if available == count:
                        returned = session.act(
                            "pilot:A",
                            {
                                "action_id": "choose",
                                "cards": [card.ref for card in candidates],
                            },
                        )
                        self.assertTrue(returned.ok, returned.summary)
                        self.assertEqual("battlefield", source.zone)
                        self.assertTrue(
                            all(card.zone == "hand" for card in candidates)
                        )
                    else:
                        self.assertNotEqual(
                            "choice.apnap",
                            getattr(
                                session.engine.state.pending_decision,
                                "kind",
                                None,
                            ),
                        )
                        self.assertEqual("graveyard", source.zone)
                        self.assertTrue(
                            all(
                                card.zone == "battlefield"
                                for card in candidates
                            )
                        )

    def test_entry_return_choice_revalidates_and_rolls_back(self):
        session = self.session(121075)
        engine = session.engine
        source = self.add_card(
            engine,
            seat="A",
            name="Generic Entry Creature Return Fixture",
            ref="entry-return-stale-source",
            zone="hand",
        )
        candidate = self.add_card(
            engine,
            seat="A",
            name="Generic Entry Dragon Witness",
            ref="entry-return-stale-candidate",
            zone="battlefield",
        )
        self.register_typed_event_trigger(engine, source)
        engine.move_card(
            source.object_id,
            "battlefield",
            reason="entry return stale source entered",
            semantic_events=True,
        )
        engine._stabilize()
        self.resolve_top(engine)
        self.assertEqual("choice.apnap", engine.state.pending_decision.kind)
        engine.move_card(candidate.object_id, "graveyard", log=False)
        expected_hash = authoritative_state_hash(engine.state)
        rejected = session.act(
            "pilot:A",
            {"action_id": "choose", "cards": [candidate.ref]},
        )
        self.assertFalse(rejected.ok)
        self.assertEqual(expected_hash, authoritative_state_hash(engine.state))
        self.assertEqual("graveyard", candidate.zone)
        self.assertEqual("battlefield", source.zone)

    def test_entry_return_unless_branch_and_ability_removal(self):
        for seed, selection, expected_source_zone, expected_candidate_zone in (
            (121070, "sacrifice", "graveyard", "battlefield"),
            (121071, "return", "battlefield", "hand"),
        ):
            with self.subTest(selection=selection):
                session = self.session(seed)
                engine = session.engine
                source = self.add_card(
                    engine,
                    seat="A",
                    name="Generic Entry Unless Return Fixture",
                    ref=f"entry-unless-source-{selection}",
                    zone="hand",
                )
                candidate = self.add_card(
                    engine,
                    seat="A",
                    name="Generic Entry Dragon Witness",
                    ref=f"entry-unless-candidate-{selection}",
                    zone="battlefield",
                )
                self.register_typed_event_trigger(engine, source)
                engine.move_card(
                    source.object_id,
                    "battlefield",
                    reason="entry unless return source entered",
                    semantic_events=True,
                )
                engine._stabilize()
                self.resolve_top(engine)
                chosen = session.act(
                    "pilot:A",
                    {"action_id": "choose", "choice": selection},
                )
                self.assertTrue(chosen.ok, chosen.summary)
                if selection == "return":
                    returned = session.act(
                        "pilot:A",
                        {"action_id": "choose", "cards": [candidate.ref]},
                    )
                    self.assertTrue(returned.ok, returned.summary)
                self.assertEqual(expected_source_zone, source.zone)
                self.assertEqual(expected_candidate_zone, candidate.zone)

        fallback_session = self.session(121072)
        fallback_engine = fallback_session.engine
        fallback = self.add_card(
            fallback_engine,
            seat="A",
            name="Generic Entry Unless Return Fixture",
            ref="entry-unless-no-payment",
            zone="hand",
        )
        self.register_typed_event_trigger(fallback_engine, fallback)
        fallback_engine.move_card(
            fallback.object_id,
            "battlefield",
            reason="entry unless unavailable return",
            semantic_events=True,
        )
        fallback_engine._stabilize()
        self.resolve_top(fallback_engine)
        fallback_choice = fallback_session.act(
            "pilot:A",
            {"action_id": "choose", "choice": "return"},
        )
        self.assertTrue(fallback_choice.ok, fallback_choice.summary)
        self.assertEqual("graveyard", fallback.zone)

        removed_session = self.session(121073)
        removed_engine = removed_session.engine
        removed = self.add_card(
            removed_engine,
            seat="A",
            name="Generic Entry Creature Return Fixture",
            ref="entry-return-removed",
            zone="hand",
        )
        self.register_typed_event_trigger(removed_engine, removed)
        commit_continuous_effect(
            removed_engine.state,
            ContinuousEffect(
                effect_id="fixture:remove-entry-return",
                source_id="fixture:remove-entry-return-owner",
                layer=Layer.ABILITY,
                sublayer="6",
                timestamp=removed_engine._next_zone_timestamp(),
                operations=(ContinuousOperation("remove_all_abilities"),),
                origin=ContinuousEffectOrigin.RESOLUTION,
                duration=ContinuousEffectDuration.UNTIL_END_OF_TURN,
                applies=ObjectQuerySpec(
                    zones=("battlefield",),
                    types_all=("creature",),
                ),
                locked_objects=(
                    ContinuousObjectIdentity(
                        object_id=removed.object_id,
                        logical_object_id=(
                            f"{removed.object_id}@{removed.zone_change_counter + 1}"
                        ),
                    ),
                ),
            ),
        )
        removed_engine.move_card(
            removed.object_id,
            "battlefield",
            reason="entry return removed ability witness",
            semantic_events=True,
        )
        removed_engine._stabilize()
        self.assertFalse(removed_engine.state.stack)

    def test_external_entry_self_returns_batch_in_apnap_order(self):
        session = self.session(121074, players=4)
        engine = session.engine
        engine.state.active_player = "A"
        controlled_source = self.add_card(
            engine,
            seat="A",
            name="Generic Controlled Subtype Entry Return Fixture",
            ref="entry-return-controlled-source",
            zone="battlefield",
        )
        other_source = self.add_card(
            engine,
            seat="B",
            name="Generic Other Entry Return Fixture",
            ref="entry-return-other-source",
            zone="battlefield",
        )
        controlled_program = self.register_typed_event_trigger(
            engine, controlled_source
        )
        other_program = self.register_typed_event_trigger(engine, other_source)
        dragon = self.add_card(
            engine,
            seat="A",
            name="Generic Entry Dragon Witness",
            ref="entry-return-dragon",
            zone="hand",
        )
        engine.move_card(
            dragon.object_id,
            "battlefield",
            reason="external entry return Dragon witness",
            semantic_events=True,
        )
        engine._stabilize()
        self.assertEqual(
            {controlled_program.key, other_program.key},
            {item.semantic_key for item in engine.state.stack},
        )
        self.assertEqual("B", engine.state.stack[-1].controller)
        self.resolve_top(engine)
        self.assertEqual("hand", other_source.zone)
        self.resolve_top(engine)
        self.assertEqual("hand", controlled_source.zone)
        self.assertEqual("battlefield", dragon.zone)

    def test_external_entry_self_return_uses_source_incarnation(self):
        for reenter in (False, True):
            with self.subTest(reenter=reenter):
                session = self.session(121081 + int(reenter))
                engine = session.engine
                source = self.add_card(
                    engine,
                    seat="A",
                    name="Generic Controlled Subtype Entry Return Fixture",
                    ref=f"external-entry-incarnation-{int(reenter)}",
                    zone="battlefield",
                )
                program = self.register_typed_event_trigger(engine, source)
                dragon = self.add_card(
                    engine,
                    seat="A",
                    name="Generic Entry Dragon Witness",
                    ref=f"external-entry-dragon-{int(reenter)}",
                    zone="hand",
                )
                engine.move_card(
                    dragon.object_id,
                    "battlefield",
                    reason="external entry incarnation witness",
                    semantic_events=True,
                )
                engine._stabilize()
                item = next(
                    value
                    for value in engine.state.stack
                    if value.semantic_key == program.key
                )
                source_incarnation = item.context["source_logical_object_id"]

                engine.move_card(
                    source.object_id,
                    "graveyard",
                    reason="external entry source left before resolution",
                    semantic_events=False,
                )
                if reenter:
                    engine.move_card(
                        source.object_id,
                        "battlefield",
                        reason="external entry source reentered before resolution",
                        semantic_events=False,
                    )
                    self.assertNotEqual(
                        source_incarnation,
                        source.logical_object_id,
                    )

                self.resolve_top(engine)

                self.assertEqual(
                    "battlefield" if reenter else "graveyard",
                    source.zone,
                )
                self.assertEqual("battlefield", dragon.zone)

    def test_entry_return_unless_sacrifice_uses_source_incarnation(self):
        for reenter in (False, True):
            with self.subTest(reenter=reenter):
                session = self.session(121083 + int(reenter))
                engine = session.engine
                source = self.add_card(
                    engine,
                    seat="A",
                    name="Generic Entry Unless Return Fixture",
                    ref=f"entry-unless-incarnation-{int(reenter)}",
                    zone="hand",
                )
                program = self.register_typed_event_trigger(engine, source)
                engine.move_card(
                    source.object_id,
                    "battlefield",
                    reason="entry unless incarnation witness",
                    semantic_events=True,
                )
                engine._stabilize()
                item = next(
                    value
                    for value in engine.state.stack
                    if value.semantic_key == program.key
                )
                source_incarnation = item.context["source_logical_object_id"]
                engine.move_card(
                    source.object_id,
                    "graveyard",
                    reason="entry unless source left before resolution",
                    semantic_events=False,
                )
                if reenter:
                    engine.move_card(
                        source.object_id,
                        "battlefield",
                        reason="entry unless source reentered before resolution",
                        semantic_events=False,
                    )
                    self.assertNotEqual(
                        source_incarnation,
                        source.logical_object_id,
                    )

                self.resolve_top(engine)
                result = session.act(
                    "pilot:A",
                    {"action_id": "choose", "choice": "sacrifice"},
                )

                self.assertTrue(result.ok, result.summary)
                self.assertEqual(
                    "battlefield" if reenter else "graveyard",
                    source.zone,
                )

    def test_reentered_source_remains_excluded_from_another_return_payment(self):
        session = self.session(121085)
        engine = session.engine
        source = self.add_card(
            engine,
            seat="A",
            name="Generic Entry Unless Return Fixture",
            ref="entry-unless-another-incarnation",
            zone="hand",
        )
        program = self.register_typed_event_trigger(engine, source)
        engine.move_card(
            source.object_id,
            "battlefield",
            reason="entry unless another exclusion witness",
            semantic_events=True,
        )
        engine._stabilize()
        item = next(
            value for value in engine.state.stack if value.semantic_key == program.key
        )
        old_incarnation = item.context["source_logical_object_id"]
        engine.move_card(
            source.object_id,
            "graveyard",
            reason="another source left before resolution",
            semantic_events=False,
        )
        engine.move_card(
            source.object_id,
            "battlefield",
            reason="another source reentered before resolution",
            semantic_events=False,
        )
        self.assertNotEqual(old_incarnation, source.logical_object_id)

        self.resolve_top(engine)
        selected_return = session.act(
            "pilot:A",
            {"action_id": "choose", "choice": "return"},
        )

        self.assertTrue(selected_return.ok, selected_return.summary)
        self.assertNotEqual(
            "choice.apnap",
            getattr(engine.state.pending_decision, "kind", None),
        )
        self.assertEqual("battlefield", source.zone)


if __name__ == "__main__":
    unittest.main()
