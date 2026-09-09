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


class FixedCounterPlayerCastTriggerRuntimeTests(unittest.TestCase):
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
                        FixedCounterPlayerCastTriggerRuntimeTests._event_condition_fields(
                            value if isinstance(value, Mapping) else None
                        )
                    )
        nested = condition.get("not")
        fields.update(
            FixedCounterPlayerCastTriggerRuntimeTests._event_condition_fields(
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


    def test_draw_counter_triggers_use_public_normalized_events(self):
        session = self.session(120007)
        engine = session.engine
        source = self.add_card(
            engine,
            seat="A",
            name="Lorescale Coatl",
            ref="draw-counter-source",
            zone="battlefield",
        )
        self.register_trigger(engine, source)
        drawn = engine.state.cards[
            engine.state.players["A"].zones["library"][-1]
        ]

        engine._begin_draw_sequence("A", 1, reason="public draw occurrence")
        engine._stabilize()

        item = engine.state.stack[-1]
        self.assertEqual("card.drawn", item.context["event"])
        self.assertEqual("A", item.context["player"])
        self.assertEqual(1, item.context["draw_ordinal"])
        serialized = json.dumps(item.context, sort_keys=True)
        self.assertNotIn(drawn.ref, serialized)
        self.assertNotIn(drawn.printed_name, serialized)
        self.assertNotIn("object", item.context)
        self.resolve_top(engine)
        self.assertEqual(1, source.counters.get("+1/+1"))

    def test_draw_and_second_draw_counter_triggers_share_one_batch(self):
        session = self.session(120008, players=4)
        engine = session.engine
        draw_source = self.add_card(
            engine,
            seat="A",
            name="Lorescale Coatl",
            ref="each-draw-counter-source",
            zone="battlefield",
        )
        second_source = self.add_card(
            engine,
            seat="A",
            name="Faerie Vandal",
            ref="second-draw-counter-source",
            zone="battlefield",
        )
        self.register_trigger(engine, draw_source)
        self.register_trigger(engine, second_source)
        turn_key = str(engine.state.turn_sequence)
        engine.state.players["A"].stats.setdefault(
            "cards_drawn_by_turn", {}
        )[turn_key] = 1

        engine._begin_draw_sequence("A", 1, reason="second public draw")
        engine._stabilize()

        self.assertEqual("trigger.order", engine.state.pending_decision.kind)
        self.assertEqual(1, len(engine.state.pending_trigger_batches))
        batch = engine.state.pending_trigger_batches[0]
        self.assertEqual(2, len(batch.items))
        self.assertEqual(
            {"card.drawn", "card.second_draw"},
            {item.normalized_event_id for item in batch.items},
        )
        refs = [
            item["id"]
            for item in engine.state.pending_decision.payload_by_actor["A"][
                "triggers"
            ]
        ]
        ordered = session.act(
            "pilot:A",
            {"action_id": "order", "triggers": refs},
        )
        self.assertTrue(ordered.ok, ordered.summary)
        self.assertEqual(
            {draw_source.object_id, second_source.object_id},
            {item.source_object_id for item in engine.state.stack[-2:]},
        )
        self.resolve_top(engine)
        self.resolve_top(engine)
        self.assertEqual(1, draw_source.counters.get("+1/+1"))
        self.assertEqual(1, second_source.counters.get("+1/+1"))

    def test_life_gain_counter_trigger_uses_replacement_resolved_amount(self):
        session = self.session(120009)
        engine = session.engine
        source = self.add_card(
            engine,
            seat="A",
            name="Ajani's Pridemate",
            ref="life-counter-source",
            zone="battlefield",
        )
        register_generated_programs(
            self.db,
            engine.semantics,
            (self.db.lookup("Boon Reflection"),),
            capability_registry=self.capabilities,
            capability_profile="commander_review",
            promote_exact_runtime_handlers=True,
        )
        self.add_card(
            engine,
            seat="A",
            name="Boon Reflection",
            ref="life-gain-doubler",
            zone="battlefield",
        )
        self.register_trigger(engine, source)
        before = engine.state.players["A"].life

        dispatch_effect(
            engine,
            {"op": "life", "player": "A", "delta": 1},
            actor="A",
            operation="life",
            reason="replacement-resolved gain",
        )

        self.assertEqual(before + 2, engine.state.players["A"].life)
        self.assert_player_result_trigger(
            engine,
            source,
            event="life.gained",
            amount=2,
        )

    def test_life_gain_counter_trigger_covers_effect_intent_lifelink_and_aftermath(
        self,
    ):
        producers = ("effect", "intent", "lifelink", "aftermath")
        for index, producer in enumerate(producers):
            with self.subTest(producer=producer):
                session = self.session(120010 + index)
                engine = session.engine
                controller = "B" if producer == "aftermath" else "A"
                source = self.add_card(
                    engine,
                    seat=controller,
                    name="Ajani's Pridemate",
                    ref=f"{producer}-life-counter-source",
                    zone="battlefield",
                )
                self.register_trigger(engine, source)
                if producer == "effect":
                    dispatch_effect(
                        engine,
                        {"op": "life", "player": "A", "delta": 1},
                        actor="A",
                        operation="life",
                        reason="immediate represented gain",
                    )
                    amount = 1
                elif producer == "intent":
                    engine.apply_life_change_intent(
                        LifeChangeIntent(
                            actor="A",
                            player="A",
                            amount=2,
                            reason="semantic choice gain",
                        )
                    )
                    amount = 2
                elif producer == "lifelink":
                    lifelink = self.add_card(
                        engine,
                        seat="A",
                        name="Healer's Hawk",
                        ref="lifelink-gain-source",
                        zone="battlefield",
                    )
                    resolve_damage_batch(
                        engine,
                        (
                            damage_proposal(
                                engine,
                                proposal_id="damage:player-result:lifelink",
                                actor="A",
                                source_ref=lifelink.ref,
                                target="B",
                                amount=1,
                                combat=True,
                                reason="represented Lifelink gain",
                            ),
                        ),
                    )
                    amount = 1
                else:
                    damage_source = self.add_card(
                        engine,
                        seat="A",
                        name="Mishra, Eminent One",
                        ref="aftermath-damage-source",
                        zone="battlefield",
                    )
                    engine.state.players["B"].life = 30
                    engine.state.damage_prevention_shields.append(
                        DamagePreventionShield(
                            shield_id="player-result-life-aftermath",
                            source_id="fixture:player-result-life-aftermath",
                            controller="B",
                            subject=DamageSubject(
                                ref="B", kind="player", controller="B"
                            ),
                            mode=PreventionMode.AMOUNT,
                            remaining=2,
                            duration=(
                                DamageModifierDuration.UNTIL_END_OF_TURN
                            ),
                            created_turn_sequence=engine.state.turn_sequence,
                            aftermath=(
                                GainLifePreventionAftermath(
                                    player="B", per_prevented=1
                                ),
                            ),
                        )
                    )
                    resolve_damage_batch(
                        engine,
                        (
                            damage_proposal(
                                engine,
                                proposal_id="damage:player-result:aftermath",
                                actor="A",
                                source_ref=damage_source.ref,
                                target="B",
                                amount=2,
                                combat=False,
                                reason="represented prevention aftermath",
                            ),
                        ),
                    )
                    amount = 2
                self.assert_player_result_trigger(
                    engine,
                    source,
                    event="life.gained",
                    amount=amount,
                )

    def test_player_result_counter_replacement_is_private_and_replays_exactly(
        self,
    ):
        session = self.session(120014, players=4)
        engine = session.engine
        source = self.add_card(
            engine,
            seat="C",
            name="Ajani's Pridemate",
            ref="private-life-counter-source",
            zone="battlefield",
        )
        self.add_card(
            engine,
            seat="C",
            name="Doubling Season",
            ref="private-life-doubling",
            zone="battlefield",
        )
        self.add_card(
            engine,
            seat="C",
            name="Doc Samson, Super Psychiatrist",
            ref="private-life-addition",
            zone="battlefield",
        )
        self.register_trigger(engine, source)
        dispatch_effect(
            engine,
            {"op": "life", "player": "C", "delta": 1},
            actor="C",
            operation="life",
            reason="private player-result counter replacement",
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
            record_dir = Path(temporary) / "player-result-counter-replay"
            session.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(expected_hash, replay["final_state_hash"])

    def test_player_result_event_dispatch_mutants_are_killed(self):
        draw_session = self.session(120015)
        draw_engine = draw_session.engine
        draw_source = self.add_card(
            draw_engine,
            seat="A",
            name="Lorescale Coatl",
            ref="draw-dispatch-mutant-source",
            zone="battlefield",
        )
        draw_program = self.register_trigger(draw_engine, draw_source)
        with patch(
            "quorune.drawing.transaction.dispatch_card_draw_event",
            return_value=(),
        ):
            draw_engine._begin_draw_sequence(
                "A", 1, reason="draw dispatch mutation"
            )
        draw_engine._stabilize()
        self.assertFalse(
            any(
                item.semantic_key == draw_program.key
                for item in draw_engine.state.stack
            )
        )

        life_session = self.session(120016)
        life_engine = life_session.engine
        life_source = self.add_card(
            life_engine,
            seat="A",
            name="Ajani's Pridemate",
            ref="life-dispatch-mutant-source",
            zone="battlefield",
        )
        life_program = self.register_trigger(life_engine, life_source)
        before = life_engine.state.players["A"].life
        with patch(
            "quorune.effect_runtime.life_effects.dispatch_life_gain_records",
            return_value=(),
        ):
            dispatch_effect(
                life_engine,
                {"op": "life", "player": "A", "delta": 1},
                actor="A",
                operation="life",
                reason="life dispatch mutation",
            )
        life_engine._stabilize()
        self.assertEqual(before + 1, life_engine.state.players["A"].life)
        self.assertFalse(
            any(
                item.semantic_key == life_program.key
                for item in life_engine.state.stack
            )
        )

    def test_cast_counter_trigger_uses_normalized_event(self):
        session = self.session(120001)
        engine = session.engine
        source = self.add_card(
            engine,
            seat="A",
            name="Noncreature Cast Counter Trigger Fixture",
            ref="cast-counter-source",
            zone="battlefield",
        )
        program = self.register_trigger(engine, source)

        spell = self.prepare_noncreature_cast(engine)
        engine._stabilize()

        self.assertEqual("stack", spell.zone)
        self.assertEqual(program.key, engine.state.stack[-1].semantic_key)
        self.assertEqual("spell.cast", engine.state.stack[-1].context["event"])
        self.assertEqual(
            source.logical_object_id,
            engine.state.stack[-1].context["source_logical_object_id"],
        )
        self.resolve_top(engine)
        self.assertEqual(1, source.counters.get("+1/+1"))

    def test_cast_relations_and_type_predicates_share_committed_event(self):
        session = self.session(121003, players=4)
        engine = session.engine
        controller_source = self.add_card(
            engine,
            seat="A",
            name="Typed Artifact Cast Draw Trigger Fixture",
            ref="controller-artifact-cast-source",
            zone="battlefield",
        )
        opponent_source = self.add_card(
            engine,
            seat="B",
            name="Typed Opponent Cast Life Trigger Fixture",
            ref="opponent-cast-source",
            zone="battlefield",
        )
        any_source = self.add_card(
            engine,
            seat="C",
            name="Typed Any Cast Life Trigger Fixture",
            ref="any-cast-source",
            zone="battlefield",
        )
        programs = {
            source.ref: self.register_typed_event_trigger(engine, source)
            for source in (controller_source, opponent_source, any_source)
        }
        before_hand = len(engine.state.players["A"].zones["hand"])
        before_life = {
            seat: engine.state.players[seat].life for seat in ("B", "C")
        }

        spell = self.prepare_noncreature_cast(engine)
        engine._stabilize()

        self.assertEqual("stack", spell.zone)
        trigger_items = [
            item
            for item in engine.state.stack
            if item.semantic_key in {program.key for program in programs.values()}
        ]
        self.assertEqual(3, len(trigger_items))
        self.assertEqual(
            {program.key for program in programs.values()},
            {item.semantic_key for item in trigger_items},
        )
        self.assertTrue(
            all(
                item.context["event"] == "spell.cast"
                and item.context["controller"] == "A"
                and item.context["types"] == ["artifact"]
                for item in trigger_items
            )
        )
        for _ in trigger_items:
            self.resolve_top(engine)
        self.assertEqual(
            before_hand + 1,
            len(engine.state.players["A"].zones["hand"]),
        )
        self.assertEqual(before_life["B"] + 1, engine.state.players["B"].life)
        self.assertEqual(before_life["C"] + 1, engine.state.players["C"].life)

    def test_cast_characteristics_use_one_sealed_stack_snapshot(self):
        source_names = {
            "multicolored": "Typed Multicolored Cast Life Trigger Fixture",
            "colorless": "Typed Colorless Cast Life Trigger Fixture",
            "legendary_or_spirit": (
                "Typed Legendary or Spirit Cast Life Trigger Fixture"
            ),
            "red": "Typed Red Cast Life Trigger Fixture",
        }

        def setup(seed: int):
            session = self.session(seed, players=4)
            engine = session.engine
            programs = {}
            for index, (quality, name) in enumerate(source_names.items()):
                source = self.add_card(
                    engine,
                    seat="B" if quality == "legendary_or_spirit" else "A",
                    name=name,
                    ref=f"{quality}-cast-source-{seed}-{index}",
                    zone="battlefield",
                )
                programs[quality] = self.register_typed_event_trigger(
                    engine,
                    source,
                )
            return session, programs

        def cast_fixture(engine, name: str, *, mana: Mapping[str, int]):
            record = self.db.lookup(name)
            for program in generated_programs(
                self.db,
                record,
                trust_level="trusted",
                capability_registry=self.capabilities,
                capability_profile="commander_review",
            ):
                engine.semantics.put(program)
            spell = self.add_card(
                engine,
                seat="A",
                name=name,
                ref=f"cast-{name.casefold().replace(' ', '-')}",
                zone="hand",
            )
            engine.state.active_player = "A"
            engine.state.phase = "precombat_main"
            engine.state.step = "main"
            engine.state.priority_player = "A"
            engine.state.priority_passes = []
            engine.permissions.invalidate_current()
            engine.state.pending_decision = None
            for symbol, amount in mana.items():
                engine.state.players["A"].mana_pool[symbol] += amount
            engine._cast("A", {"card": spell.ref, "pay": "auto"})
            engine._stabilize()
            return spell

        session, programs = setup(121008)
        spell = cast_fixture(
            session.engine,
            "Legendary Spirit Cast Fixture",
            mana={"W": 1, "U": 1},
        )
        items = [
            item
            for item in session.engine.state.stack
            if item.semantic_key in {program.key for program in programs.values()}
        ]
        self.assertEqual("stack", spell.zone)
        self.assertEqual(
            {
                programs["multicolored"].key,
                programs["legendary_or_spirit"].key,
            },
            {item.semantic_key for item in items},
        )
        self.assertEqual(2, len(items))
        for item in items:
            self.assertEqual(["creature"], item.context["types"])
            self.assertEqual(["spirit"], item.context["subtypes"])
            self.assertEqual(["legendary"], item.context["supertypes"])
            self.assertEqual(["W", "U"], item.context["colors"])

        devoid_session, devoid_programs = setup(121009)
        devoid = cast_fixture(
            devoid_session.engine,
            "Devoid Spirit Cast Fixture",
            mana={"C": 2, "R": 1},
        )
        devoid_items = [
            item
            for item in devoid_session.engine.state.stack
            if item.semantic_key
            in {program.key for program in devoid_programs.values()}
        ]
        self.assertEqual("stack", devoid.zone)
        self.assertEqual(
            {
                devoid_programs["colorless"].key,
                devoid_programs["legendary_or_spirit"].key,
            },
            {item.semantic_key for item in devoid_items},
        )
        self.assertEqual(2, len(devoid_items))
        for item in devoid_items:
            self.assertEqual(["creature"], item.context["types"])
            self.assertEqual(["spirit"], item.context["subtypes"])
            self.assertEqual([], item.context["supertypes"])
            self.assertEqual([], item.context["colors"])

    def test_spell_cast_predicates_share_v4_event_and_stack_source(self):
        def cast_fixture(
            current_session,
            name: str,
            ref: str,
            mana: Mapping[str, int],
            response: Mapping[str, object] | None = None,
        ):
            engine = current_session.engine
            record = self.db.lookup(name)
            for program in generated_programs(
                self.db,
                record,
                trust_level="trusted",
                capability_registry=self.capabilities,
                capability_profile="commander_review",
            ):
                engine.semantics.put(program)
            spell = self.add_card(
                engine,
                seat="A",
                name=name,
                ref=ref,
                zone="hand",
            )
            engine.state.active_player = "A"
            engine.state.phase = "precombat_main"
            engine.state.step = "main"
            engine.state.priority_player = "A"
            engine.state.priority_passes = []
            engine.permissions.invalidate_current()
            engine.state.pending_decision = None
            for symbol, amount in mana.items():
                engine.state.players["A"].mana_pool[symbol] += amount
            engine._cast(
                "A",
                {
                    "card": spell.ref,
                    "pay": "auto",
                    **dict(response or {}),
                },
            )
            if (
                engine.state.pending_decision is not None
                and engine.state.pending_decision.kind == "trigger.order"
            ):
                refs = [
                    item["id"]
                    for item in engine.state.pending_decision.payload_by_actor[
                        "A"
                    ]["triggers"]
                ]
                ordered = current_session.act(
                    "pilot:A",
                    {"action_id": "order", "triggers": refs},
                )
                self.assertTrue(ordered.ok, ordered.summary)
            return spell

        session = self.session(121010, players=4)
        engine = session.engine
        sources = tuple(
            self.add_card(
                engine,
                seat="A",
                name=name,
                ref=f"cast-predicate-{index}",
                zone="battlefield",
            )
            for index, name in enumerate(
                (
                    "Typed Second Cast Life Trigger Fixture",
                    "Typed Mana Value Cast Life Trigger Fixture",
                    "Typed Historic Cast Life Trigger Fixture",
                ),
                start=1,
            )
        )
        programs = {
            self.register_typed_event_trigger(engine, source).key
            for source in sources
        }
        engine._record_turn_history(
            "spell_cast",
            actor="A",
            object_incarnation="fixture:prior-cast",
            types=("instant",),
        )

        spell = cast_fixture(
            session,
            "Legendary Spirit Cast Fixture",
            "typed-v3-cast",
            {"W": 1, "U": 1},
        )
        items = [
            item for item in engine.state.stack if item.semantic_key in programs
        ]

        self.assertEqual("stack", spell.zone)
        self.assertEqual(programs, {item.semantic_key for item in items})
        self.assertEqual(3, len(items))
        for item in items:
            self.assertEqual(5, item.context["schema_version"])
            self.assertEqual([], item.context["targets"])
            self.assertEqual("precombat_main", item.context["phase"])
            self.assertEqual(2.0, item.context["mana_value"])
            self.assertEqual(2, item.context["caster_spell_number"])
            self.assertEqual("A", item.context["owner"])
            self.assertEqual("A", item.context["active_player"])
            self.assertFalse(item.context["kicked"])
            self.assertFalse(item.context["has_x_cost"])
            self.assertFalse(item.context["has_adventure"])

        self_session = self.session(121011, players=4)
        self_engine = self_session.engine
        self_spell = self.add_card(
            self_engine,
            seat="A",
            name="Typed Self Cast Life Trigger Fixture",
            ref="typed-self-cast",
            zone="hand",
        )
        self_program = self.register_typed_event_trigger(
            self_engine,
            self_spell,
        )
        self_engine.state.active_player = "A"
        self_engine.state.phase = "precombat_main"
        self_engine.state.step = "main"
        self_engine.state.priority_player = "A"
        self_engine.state.players["A"].mana_pool["C"] += 1
        self_engine._cast("A", {"card": self_spell.ref, "pay": "auto"})
        self_item = next(
            item
            for item in self_engine.state.stack
            if item.semantic_key == self_program.key
        )
        self.assertEqual("stack", self_spell.zone)
        self.assertEqual(self_spell.ref, self_item.context["card"])
        self.assertEqual(self_spell.object_id, self_item.source_object_id)
        self.assertEqual("stack", self_item.context["source_zone"])

        fact_session = self.session(121012, players=4)
        fact_engine = fact_session.engine
        fact_programs = {}
        for index, (fact, name) in enumerate(
            (
                ("kicked", "Typed Kicked Cast Life Trigger Fixture"),
                ("has_x_cost", "Typed X Cost Cast Life Trigger Fixture"),
                (
                    "has_adventure",
                    "Typed Adventure Cast Life Trigger Fixture",
                ),
            ),
            start=1,
        ):
            source = self.add_card(
                fact_engine,
                seat="A",
                name=name,
                ref=f"typed-positive-fact-{index}",
                zone="battlefield",
            )
            fact_programs[fact] = self.register_typed_event_trigger(
                fact_engine,
                source,
            ).key

        positive_contexts = {}
        for field, name, ref, mana, response in (
            (
                "kicked",
                "Kavu Titan",
                "typed-kicked-cast",
                {"C": 3, "G": 2},
                {"cost_option": KICKER_CAST_OPTION_ID},
            ),
            (
                "has_x_cost",
                "Typed X Cast Fixture",
                "typed-x-cast",
                {"C": 2, "G": 1},
                {"x": 2},
            ),
            (
                "has_adventure",
                "Typed Adventure Cast Fixture // Typed Adventure Effect Fixture",
                "typed-adventure-cast",
                {"C": 2, "B": 1},
                {},
            ),
        ):
            cast_fixture(fact_session, name, ref, mana, response)
            item = next(
                value
                for value in fact_engine.state.stack
                if value.semantic_key == fact_programs[field]
            )
            positive_contexts[field] = dict(item.context)
            fact_engine.state.stack.clear()
            fact_engine.state.pending_trigger_batches.clear()
        for field, context in positive_contexts.items():
            self.assertTrue(context[field])
        self.assertEqual(3.0, positive_contexts["has_x_cost"]["mana_value"])

    def test_main_phase_cast_trigger_optionally_returns_source(self):
        def setup(
            seed: int,
            *,
            phase: str,
            step: str,
            active_player: str = "A",
        ):
            session = self.session(seed, players=4)
            engine = session.engine
            source = self.add_card(
                engine,
                seat="A",
                name="Typed Main Phase Self Return Trigger Fixture",
                ref=f"main-phase-return-{seed}",
                zone="battlefield",
            )
            program = self.register_typed_event_trigger(engine, source)
            spell = self.add_card(
                engine,
                seat="A",
                name="Typed Main Phase Instant Fixture",
                ref=f"main-phase-instant-{seed}",
                zone="hand",
            )
            engine.state.active_player = active_player
            engine.state.phase = phase
            engine.state.step = step
            engine.state.priority_player = "A"
            engine.state.priority_passes = []
            engine.permissions.invalidate_current()
            engine.state.pending_decision = None
            engine.state.players["A"].mana_pool["U"] += 1
            engine._cast("A", {"card": spell.ref, "pay": "auto"})
            return session, source, program

        off_phase, off_source, off_program = setup(
            121044,
            phase="ending",
            step="end",
        )
        self.assertFalse(
            any(
                item.semantic_key == off_program.key
                for item in off_phase.engine.state.stack
            )
        )
        self.assertEqual("battlefield", off_source.zone)

        opponent_main, opponent_source, opponent_program = setup(
            121046,
            phase="precombat_main",
            step="main",
            active_player="B",
        )
        self.assertFalse(
            any(
                item.semantic_key == opponent_program.key
                for item in opponent_main.engine.state.stack
            )
        )
        self.assertEqual("battlefield", opponent_source.zone)

        session, source, program = setup(
            121045,
            phase="precombat_main",
            step="main",
        )
        engine = session.engine
        trigger = next(
            item
            for item in engine.state.stack
            if item.semantic_key == program.key
        )
        self.assertEqual("precombat_main", trigger.context["phase"])
        previous_identity = source.logical_object_id

        self.resolve_top(engine)
        self.assertEqual("semantic.choice", engine.state.pending_decision.kind)
        self.assertEqual("battlefield", source.zone)
        applied = session.act(
            "pilot:A",
            {
                "action_id": "choose",
                "choice": "apply",
                "reason": "Apply the represented source-return choice.",
            },
        )

        self.assertTrue(applied.ok, applied.summary)
        self.assertEqual("hand", source.zone)
        self.assertNotEqual(previous_identity, source.logical_object_id)
        self.assertIn(
            source.object_id,
            engine.state.players["A"].zones["hand"],
        )

    def test_shroud_and_enchantment_cast_draw_compose(self):
        session = self.session(121007, players=4)
        engine = session.engine
        source = self.add_card(
            engine,
            seat="B",
            name="Typed Shroud Enchantment Cast Draw Trigger Fixture",
            ref="shroud-enchantment-cast-source",
            zone="battlefield",
        )
        program = self.register_typed_event_trigger(engine, source)

        legal_target = self.deck_card(engine, "A", "Emry, Lurker of the Loch")
        engine.move_card(
            legal_target.object_id,
            "battlefield",
            controller="A",
            log=False,
        )
        reb = self.deck_card(engine, "A", "Red Elemental Blast")
        engine.move_card(reb.object_id, "hand", log=False)
        engine.state.players["A"].mana_pool["R"] = 1
        engine.state.priority_player = "A"
        engine._issue_priority("A")
        hints = engine._priority_action_hints("A")
        action = next(
            row for row in hints["actions"] if row.get("card") == reb.ref
        )
        legal_refs = action["target_schema"]["legal_refs"]
        self.assertNotIn(source.ref, legal_refs)
        self.assertIn(legal_target.ref, legal_refs)
        before_rejection = authoritative_state_hash(engine.state)
        rejected = session.act(
            "pilot:A",
            {
                "action_id": action["id"],
                "modes": ["destroy"],
                "targets": [source.ref],
                "pay": "manual",
                "payment": {"R": 1},
            },
        )
        self.assertFalse(rejected.ok)
        self.assertEqual(
            before_rejection,
            authoritative_state_hash(engine.state),
        )

        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine.state.active_player = "B"
        engine.state.phase = "precombat_main"
        engine.state.step = "main"
        engine.state.priority_player = "B"
        engine.state.priority_passes = []
        enchantment = self.deck_card(engine, "B", "Mystic Remora")
        engine.move_card(enchantment.object_id, "hand", log=False)
        engine.state.players["B"].mana_pool["U"] += 1
        engine._cast("B", {"card": enchantment.ref, "pay": "auto"})
        engine._stabilize()

        trigger = next(
            item for item in engine.state.stack if item.semantic_key == program.key
        )
        self.assertEqual("spell.cast", trigger.context["event"])
        self.assertEqual(["enchantment"], trigger.context["types"])
        hand_before_draw = len(engine.state.players["B"].zones["hand"])
        library_top = engine.state.players["B"].zones["library"][-1]
        self.resolve_top(engine)
        self.assertEqual(
            hand_before_draw + 1,
            len(engine.state.players["B"].zones["hand"]),
        )
        self.assertIn(library_top, engine.state.players["B"].zones["hand"])
        self.assertEqual("battlefield", source.zone)

    def test_source_attack_trigger_uses_sealed_transition_and_replays(self):
        session = self.session(121004, players=4)
        engine = session.engine
        engine.state.active_player = "A"
        engine.state.phase_index = 5
        engine.state.phase = "combat"
        engine.state.step = "declare_attackers"
        engine.state.combat = CombatState()
        source = self.add_card(
            engine,
            seat="A",
            name="Typed Self Attack Life Trigger Fixture",
            ref="typed-self-attack-source",
            zone="battlefield",
        )
        program = self.register_typed_event_trigger(engine, source)
        before_life = engine.state.players["A"].life

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
        self.assertEqual("creature.attacks", item.context["event"])
        self.assertEqual(source.ref, item.context["card"])
        self.assertEqual(
            item.context["event_id"],
            item.context["attack_transition"]["transition_id"],
        )
        for seat in engine.active_seats:
            packet = session.packet(f"pilot:{seat}", full=True)
            packet_text = json.dumps(packet, sort_keys=True)
            self.assertNotIn(source.object_id, packet_text)
            self.assertNotIn(source.logical_object_id, packet_text)

        for _ in range(12):
            if not engine.state.stack:
                break
            pass_current(session)
        self.assertFalse(engine.state.stack)
        self.assertEqual(before_life + 1, engine.state.players["A"].life)
        expected_hash = authoritative_state_hash(engine.state)
        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / "typed-self-attack-trigger"
            session.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(expected_hash, replay["final_state_hash"])

    def test_public_action_occurrences_share_typed_batch_owners(self):
        attack_session = self.session(121040, players=4)
        attack_engine = attack_session.engine
        attack_engine.state.active_player = "A"
        attack_engine.state.phase_index = 5
        attack_engine.state.phase = "combat"
        attack_engine.state.step = "declare_attackers"
        attack_engine.state.combat = CombatState()
        observer_a = self.add_card(
            attack_engine,
            seat="A",
            name="Typed Public Attack Trigger Fixture",
            ref="public-attack-observer-a",
            zone="battlefield",
        )
        observer_c = self.add_card(
            attack_engine,
            seat="C",
            name="Typed Public Attack Trigger Fixture",
            ref="public-attack-observer-c",
            zone="battlefield",
        )
        program = self.register_typed_event_trigger(attack_engine, observer_a)
        self.register_typed_event_trigger(attack_engine, observer_c)
        attacker = self.add_card(
            attack_engine,
            seat="A",
            name="Typed Self Attack Life Trigger Fixture",
            ref="public-attacker",
            zone="battlefield",
        )
        attack_engine._issue_attackers()
        declared = attack_session.act(
            "pilot:A",
            {"a": "attack", "atk": {attacker.ref: "B"}},
        )
        self.assertTrue(declared.ok, declared.summary)
        attack_items = [
            item
            for item in attack_engine.state.stack
            if item.semantic_key == program.key
        ]
        self.assertEqual(
            ["A", "C"],
            [item.controller for item in attack_items],
        )
        self.assertEqual(
            {observer_a.object_id, observer_c.object_id},
            {item.source_object_id for item in attack_items},
        )
        attack_item = attack_items[0]
        self.assertEqual("creature.attacks", attack_item.context["event"])
        self.assertEqual(attacker.ref, attack_item.context["card"])
        self.assertEqual("A", attack_item.context["controller"])
        self.assertTrue(attack_item.context["attacking_alone"])

        block_session = self.session(121041, players=2)
        block_engine = block_session.engine
        block_engine.state.active_player = "A"
        block_engine.state.phase_index = 6
        block_engine.state.phase = "combat"
        block_engine.state.step = "declare_blockers"
        block_observer = self.add_card(
            block_engine,
            seat="B",
            name="Typed Public Block Trigger Fixture",
            ref="public-block-observer",
            zone="battlefield",
        )
        block_program = self.register_typed_event_trigger(
            block_engine, block_observer
        )
        attacker_ref = block_engine.create_token(
            "A",
            name="Public block attacker",
            characteristics={
                "type_line": "Token Creature — Test",
                "power": "2",
                "toughness": "2",
            },
        )[0]
        blocker_ref = block_engine.create_token(
            "B",
            name="Public defender blocker",
            characteristics={
                "type_line": "Token Creature — Wall",
                "power": "0",
                "toughness": "4",
                "keywords": ["Defender"],
            },
        )[0]
        block_attacker = block_engine._resolve_object("A", attacker_ref)
        blocker = block_engine._resolve_object("B", blocker_ref)
        block_attacker.attacking = "B"
        block_engine.state.combat = CombatState(
            attackers_declared=True,
            had_attacking_creature=True,
            attackers={block_attacker.object_id: "B"},
            defending_players=["B"],
        )
        block_engine._begin_blocker_decisions()
        blocked = block_session.act(
            "pilot:B",
            {"a": "block", "blk": {blocker.ref: block_attacker.ref}},
        )
        self.assertTrue(blocked.ok, blocked.summary)
        block_item = next(
            item
            for item in block_engine.state.stack
            if item.semantic_key == block_program.key
        )
        self.assertEqual("creature.blocks", block_item.context["event"])
        self.assertEqual(blocker.ref, block_item.context["card"])
        self.assertIn("defender", block_item.context["keywords"])
        transition_log = next(
            event
            for event in reversed(block_engine.state.events)
            if event.code == "combat.block_transition"
        )
        self.assertIn(
            block_item.ref,
            transition_log.details["semantic_trigger_refs"],
        )

        becomes_session = self.session(121044, players=2)
        becomes_engine = becomes_session.engine
        becomes_engine.state.active_player = "A"
        becomes_engine.state.phase_index = 6
        becomes_engine.state.phase = "combat"
        becomes_engine.state.step = "declare_blockers"
        becomes_attacker = self.add_card(
            becomes_engine,
            seat="A",
            name="Typed Becomes Blocked Trigger Fixture",
            ref="becomes-blocked-attacker",
            zone="battlefield",
        )
        becomes_program = self.register_typed_event_trigger(
            becomes_engine, becomes_attacker
        )
        blocker_refs = [
            becomes_engine.create_token(
                "B",
                name=f"Becomes blocked witness {index}",
                characteristics={
                    "type_line": "Token Creature — Soldier",
                    "power": "1",
                    "toughness": "3",
                },
            )[0]
            for index in (1, 2)
        ]
        becomes_attacker.attacking = "B"
        becomes_engine.state.combat = CombatState(
            attackers_declared=True,
            had_attacking_creature=True,
            attackers={becomes_attacker.object_id: "B"},
            defending_players=["B"],
        )
        becomes_engine._begin_blocker_decisions()
        blockers = [
            becomes_engine._resolve_object("B", ref)
            for ref in blocker_refs
        ]
        becomes_result = becomes_session.act(
            "pilot:B",
            {
                "a": "block",
                "blk": {
                    blocker.ref: becomes_attacker.ref
                    for blocker in blockers
                },
            },
        )
        self.assertTrue(becomes_result.ok, becomes_result.summary)
        becomes_items = [
            item
            for item in becomes_engine.state.stack
            if item.semantic_key == becomes_program.key
        ]
        self.assertEqual(1, len(becomes_items))
        self.assertEqual(
            "creature.becomes_blocked",
            becomes_items[0].context["event"],
        )
        self.assertEqual(
            becomes_attacker.ref,
            becomes_items[0].context["card"],
        )

    def test_public_action_event_dispatch_mutant_is_killed(self):
        session = self.session(121048, players=4)
        engine = session.engine
        engine.state.active_player = "A"
        engine.state.phase_index = 5
        engine.state.phase = "combat"
        engine.state.step = "declare_attackers"
        engine.state.combat = CombatState()
        observer = self.add_card(
            engine,
            seat="C",
            name="Typed Public Attack Trigger Fixture",
            ref="public-action-mutation-observer",
            zone="battlefield",
        )
        program = self.register_typed_event_trigger(engine, observer)
        attacker = self.add_card(
            engine,
            seat="A",
            name="Typed Self Attack Life Trigger Fixture",
            ref="public-action-mutation-attacker",
            zone="battlefield",
        )
        engine._issue_attackers()

        with patch.object(
            engine,
            "_dispatch_semantic_event",
            return_value=[],
        ):
            result = session.act(
                "pilot:A",
                {"a": "attack", "atk": {attacker.ref: "B"}},
            )

        self.assertTrue(result.ok, result.summary)
        self.assertFalse(
            any(item.semantic_key == program.key for item in engine.state.stack)
        )

    def test_cycling_trigger_uses_public_hand_snapshot(self):
        session = self.session(121042, players=4)
        engine = session.engine
        observer = self.add_card(
            engine,
            seat="B",
            name="Typed Public Cycling Trigger Fixture",
            ref="public-cycle-observer",
            zone="battlefield",
        )
        observer_program = self.register_typed_event_trigger(engine, observer)
        source = self.deck_card(engine, "A", "Xander's Lounge")
        engine.move_card(source.object_id, "hand", log=False)
        register_generated_programs(
            self.db,
            engine.semantics,
            (self.db.lookup(source.printed_name),),
            capability_registry=self.capabilities,
            capability_profile="commander_review",
            promote_exact_runtime_handlers=True,
        )
        engine.state.players["A"].mana_pool["C"] = 3
        engine.state.active_player = "A"
        engine.state.started = True
        engine.state.phase = "precombat_main"
        engine.state.step = "main"
        engine.state.priority_player = None
        engine.state.priority_passes = []
        engine.state.pending_decision = None
        engine.permissions.invalidate_current()
        engine._grant_priority("A")
        engine.pump()
        action_id = f"activate:{source.ref}:ab3"
        result = session.act("pilot:A", {"action_id": action_id})
        self.assertTrue(result.ok, result.summary)
        engine._stabilize()
        self.assertEqual("graveyard", source.zone)
        cycle_item = next(
            item
            for item in engine.state.stack
            if item.semantic_key == observer_program.key
        )
        self.assertEqual("card.cycled", cycle_item.context["event"])
        self.assertEqual(source.ref, cycle_item.context["card"])
        self.assertEqual("A", cycle_item.context["player"])
        self.assertEqual("cycling", cycle_item.context["cycling_kind"])
        serialized = json.dumps(cycle_item.context, sort_keys=True)
        self.assertNotIn(source.object_id, serialized)
        self.assertNotIn(source.logical_object_id, serialized)
        self.assertLess(
            next(
                index
                for index, item in enumerate(engine.state.stack)
                if item.kind == "activated_ability"
                and item.source_object_id == source.object_id
            ),
            engine.state.stack.index(cycle_item),
        )

    def test_typecycling_trigger_uses_public_hand_snapshot(self):
        session = self.session(121043, players=4)
        engine = session.engine
        observer = self.add_card(
            engine,
            seat="B",
            name="Typed Public Cycling Trigger Fixture",
            ref="public-typecycle-observer",
            zone="battlefield",
        )
        observer_program = self.register_typed_event_trigger(engine, observer)
        source = self.add_card(
            engine,
            seat="A",
            name="Ash Barrens",
            ref="public-typecycle-source",
            zone="hand",
        )
        register_generated_programs(
            self.db,
            engine.semantics,
            (self.db.by_oracle_id(source.oracle_id),),
            capability_registry=self.capabilities,
            capability_profile="commander_review",
            promote_exact_runtime_handlers=True,
        )
        engine.state.players["A"].mana_pool["C"] = 1
        engine.state.active_player = "A"
        engine.state.started = True
        engine.state.phase = "precombat_main"
        engine.state.step = "main"
        engine.state.priority_player = None
        engine.state.priority_passes = []
        engine.state.pending_decision = None
        engine.permissions.invalidate_current()
        engine._grant_priority("A")
        engine.pump()

        result = session.act(
            "pilot:A",
            {"action_id": f"activate:{source.ref}:ab2"},
        )

        self.assertTrue(result.ok, result.summary)
        self.assertEqual("graveyard", source.zone)
        cycle_item = next(
            item
            for item in engine.state.stack
            if item.semantic_key == observer_program.key
        )
        self.assertEqual("card.cycled", cycle_item.context["event"])
        self.assertEqual(source.ref, cycle_item.context["card"])
        self.assertEqual("typecycling", cycle_item.context["cycling_kind"])

    def test_face_up_trigger_notifies_public_battlefield_sources(self):
        session = self.session(121045, players=4)
        engine = session.engine
        source = self.add_card(
            engine,
            seat="B",
            name="Typed Public Face Up Trigger Fixture",
            ref="public-face-up-source",
            zone="hand",
        )
        program = self.register_typed_event_trigger(engine, source)
        register_generated_programs(
            self.db,
            engine.semantics,
            (self.db.by_oracle_id(source.oracle_id),),
            capability_registry=self.capabilities,
            capability_profile="commander_review",
            promote_exact_runtime_handlers=True,
            promote_exact_effect_programs=True,
        )
        engine.state.players["B"].mana_pool["C"] = 4
        engine.state.active_player = "B"
        engine.state.started = True
        engine.state.phase = "precombat_main"
        engine.state.step = "main"
        engine.state.priority_player = None
        engine.state.priority_passes = []
        engine.state.pending_decision = None
        engine.permissions.invalidate_current()
        engine._grant_priority("B")
        engine.pump()
        cast_action = next(
            value
            for value in engine._priority_action_hints("B")["actions"]
            if value["id"] == f"cast-morph:{source.ref}"
        )
        result = session.act("pilot:B", {"action_id": cast_action["id"]})
        self.assertTrue(result.ok, result.summary)
        for _ in range(12):
            if source.zone != "stack":
                break
            principal = session.pending_principals()[0]
            result = session.act(principal, {"action_id": "pass"})
            self.assertTrue(result.ok, result.summary)
        self.assertEqual("battlefield", source.zone)
        self.assertTrue(source.face_down)
        action = next(
            value
            for value in engine._priority_action_hints("B")["actions"]
            if value["id"] == f"turn-face-up:{source.ref}"
        )

        result = session.act("pilot:B", {"action_id": action["id"]})

        self.assertTrue(result.ok, result.summary)
        self.assertFalse(source.face_down)
        item = next(
            value
            for value in engine.state.stack
            if value.semantic_key == program.key
        )
        self.assertEqual("permanent.turned_face_up", item.context["event"])
        self.assertEqual(source.ref, item.context["card"])
        self.assertEqual("B", item.context["controller"])

    def test_public_zone_trigger_uses_sealed_entry_power(self):
        session = self.session(121046, players=4)
        engine = session.engine
        observer = self.add_card(
            engine,
            seat="A",
            name="Typed Public Power Entry Trigger Fixture",
            ref="public-power-observer",
            zone="battlefield",
        )
        program = self.register_typed_event_trigger(engine, observer)

        engine.create_token(
            "A",
            name="Low-power entry witness",
            characteristics={
                "type_line": "Token Creature — Beast",
                "power": "2",
                "toughness": "2",
            },
        )
        engine._stabilize()
        self.assertFalse(
            any(item.semantic_key == program.key for item in engine.state.stack)
        )

        high_ref = engine.create_token(
            "A",
            name="High-power entry witness",
            characteristics={
                "type_line": "Token Creature — Beast",
                "power": "3",
                "toughness": "3",
            },
        )[0]
        engine._stabilize()
        item = next(
            value
            for value in engine.state.stack
            if value.semantic_key == program.key
        )
        self.assertEqual("creature.enter", item.context["event"])
        self.assertEqual(high_ref, item.context["card"])
        self.assertEqual(3, item.context["power"])

    def test_public_damage_trigger_uses_committed_damage_occurrence(self):
        session = self.session(121047, players=4)
        engine = session.engine
        observer = self.add_card(
            engine,
            seat="A",
            name="Typed Public Damage Trigger Fixture",
            ref="public-damage-observer",
            zone="battlefield",
        )
        program = self.register_typed_event_trigger(engine, observer)
        source = self.add_card(
            engine,
            seat="A",
            name="Typed Self Attack Life Trigger Fixture",
            ref="public-damage-source",
            zone="battlefield",
        )

        resolve_damage_batch(
            engine,
            (
                damage_proposal(
                    engine,
                    proposal_id="public-damage:noncombat",
                    actor="A",
                    source_ref=source.ref,
                    target="B",
                    amount=1,
                    combat=False,
                    reason="public damage negative witness",
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
                    proposal_id="public-damage:combat",
                    actor="A",
                    source_ref=source.ref,
                    target="B",
                    amount=1,
                    combat=True,
                    reason="public damage positive witness",
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
        self.assertEqual("A", item.context["source_controller"])
        self.assertEqual("B", item.context["target"])
        self.assertTrue(item.context["combat"])

    def test_public_action_occurrences_replay_exactly(self):
        session = self.session(121043, players=4)
        engine = session.engine
        engine.state.active_player = "A"
        engine.state.phase_index = 5
        engine.state.phase = "combat"
        engine.state.step = "declare_attackers"
        engine.state.combat = CombatState()
        observer = self.add_card(
            engine,
            seat="A",
            name="Typed Public Attack Trigger Fixture",
            ref="public-replay-observer",
            zone="battlefield",
        )
        program = self.register_typed_event_trigger(engine, observer)
        attacker = self.add_card(
            engine,
            seat="A",
            name="Typed Self Attack Life Trigger Fixture",
            ref="public-replay-attacker",
            zone="battlefield",
        )
        engine._issue_attackers()
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()
        result = session.act(
            "pilot:A",
            {"a": "attack", "atk": {attacker.ref: "B"}},
        )
        self.assertTrue(result.ok, result.summary)
        self.assertTrue(
            any(item.semantic_key == program.key for item in engine.state.stack)
        )
        expected_hash = authoritative_state_hash(engine.state)
        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / "typed-public-action-trigger"
            session.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(expected_hash, replay["final_state_hash"])

    def test_source_attack_counter_trigger_uses_replacement_owner(self):
        session = self.session(121006)
        engine = session.engine
        engine.state.active_player = "A"
        engine.state.phase_index = 5
        engine.state.phase = "combat"
        engine.state.step = "declare_attackers"
        engine.state.combat = CombatState()
        source = self.add_card(
            engine,
            seat="A",
            name="Source Attack Counter Trigger Fixture",
            ref="counter-self-attack-source",
            zone="battlefield",
        )
        program = self.register_trigger(engine, source)

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
        self.assertEqual("creature.attacks", item.context["event"])
        self.resolve_top(engine)
        self.assertEqual(1, source.counters.get("+1/+1"))

    def test_source_attack_event_dispatch_mutant_is_killed(self):
        session = self.session(121005)
        engine = session.engine
        engine.state.active_player = "A"
        engine.state.phase_index = 5
        engine.state.phase = "combat"
        engine.state.step = "declare_attackers"
        engine.state.combat = CombatState()
        source = self.add_card(
            engine,
            seat="A",
            name="Typed Self Attack Life Trigger Fixture",
            ref="mutated-self-attack-source",
            zone="battlefield",
        )
        program = self.register_typed_event_trigger(engine, source)

        engine._issue_attackers()
        with patch.object(
            engine,
            "_dispatch_semantic_event",
            return_value=[],
        ):
            declared = session.act(
                "pilot:A",
                {"a": "attack", "atk": {source.ref: "B"}},
            )
        self.assertTrue(declared.ok, declared.summary)
        self.assertFalse(
            any(item.semantic_key == program.key for item in engine.state.stack)
        )


if __name__ == "__main__":
    unittest.main()
