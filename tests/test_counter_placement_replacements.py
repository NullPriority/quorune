from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import random
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT, keep_all, make_session
from property_budget import property_transitions
from scripts.build_test_database import build_fixture_database
import quorune.effect_runtime.damage_life_and_turns as energy_effect_module
from quorune.carddb import CardDatabase
from quorune.counter_placement import (
    commit_prepared_counter_placements,
    CounterPlacementError,
    CounterPlacementResult,
    CounterPlacementRequest,
    place_counters,
    prepare_counter_placements,
)
from quorune.deck import DeckLoader
from quorune.errors import GameRuleError
from quorune.model import CardInstance, GameConfig, GameState, StackItem
from quorune.oracle_ir import register_generated_programs
from quorune.projection import StateProjector
from quorune.record import (
    authoritative_state_hash,
    checkpoint_envelope,
    replay_record,
)
from quorune.replacement_effects import (
    ReplaceableEvent, ReplacementChoiceRequired, ReplacementClass, ReplacementEffect, ReplacementEffectError,
)
from quorune.replacement.applicability import replacement_choice
from quorune.replacement.application import apply_replacement
from quorune.replacement.model import COUNTER_REPLACEMENT_GENERATED_FIELD
from quorune.replacement.operations import MultiplyAmount, SetField
from quorune.rules.capabilities import load_default_capability_registry
from quorune.semantic_runtime import (
    CounterPlacementEventSpec,
    CounterQuantityReplacementHandler,
    CounterReplacementSourceContext,
    SemanticNodeError,
    ZoneChangeReplacementContext,
    ZoneDestinationReplacementHandler,
    default_counter_placement_replacement_registry,
    resolve_counter_placement_replacements,
    resolve_zone_change_replacements,
)
from quorune.semantics import SemanticProgram


def counter_descriptor(
    *,
    multiplier: int = 2,
    additional: int = 0,
    placing_player_relation: str = "any",
    target_controller_relation: str = "source_controller",
    counter_names: list[str] | None = None,
    target_types_all: list[str] | None = None,
    effect_generated: bool = True,
) -> dict:
    return {
        "handler_id": "replacement.counter.quantity.v1",
        "schema_version": 1,
        "event": "counter.place",
        "condition": {
            "placing_player_relation": placing_player_relation,
            "target_controller_relation": target_controller_relation,
            "counter_names": list(counter_names or []),
            "target_types_all": list(target_types_all or []),
            "effect_generated": effect_generated,
        },
        "modification": {
            "multiplier": multiplier,
            "additional": additional,
        },
    }


class CounterQuantityProvenanceTests(unittest.TestCase):
    def setUp(self):
        self.original = CounterPlacementEventSpec(
            event_id="cost", subject_kind="permanent", subject_id="creature",
            placing_player="A", counter_name="-1/-1", amount=1,
            source_ref="spell", effect_generated=False, owner="A",
            controller="A", target_zone="battlefield",
        ).event()
        self.current = replace(self.original, payload={
            **self.original.payload, COUNTER_REPLACEMENT_GENERATED_FIELD: False,
        })
        self.any_placement = ReplacementEffect(
            "any", "all-placements", "counter.place", ReplacementClass.OTHER,
            operations=(MultiplyAmount("amount", 2),),
        )
        self.effect_only = ReplacementEffect(
            "effect", "effect-only", "counter.place", ReplacementClass.OTHER,
            conditions={"effect_generated": {"eq": True}, "amount": {"gt": 0}},
            operations=(MultiplyAmount("amount", 2),),
        )

    def test_effect_only_doubler_reconsiders_the_transformed_cost(self):
        effects = (self.any_placement, self.effect_only)
        for event, expected in ((self.current, 4), (self.original, 2)):
            with self.subTest(expected=expected):
                first = replacement_choice(event, effects)
                self.assertEqual(("any",), first.options)
                changed = apply_replacement(first, effects, "any")
                self.assertIs(changed.payload["effect_generated"], False)
                second = replacement_choice(changed, effects)
                if second is not None:
                    self.assertEqual(("effect",), second.options)
                    changed = apply_replacement(second, effects, "effect")
                self.assertEqual(expected, changed.payload["amount"])
                self.assertIsNone(replacement_choice(changed, effects))

    def test_omitted_replacement_provenance_mutant_is_killed(self):
        effects = (self.any_placement, self.effect_only)
        first = replacement_choice(self.current, effects)
        with patch("quorune.replacement.application._record_replacement_generated_counters", return_value=None):
            changed = apply_replacement(first, effects, "any")
            with self.assertRaises(AssertionError):
                self.assertIsNotNone(replacement_choice(changed, effects))

    def test_decline_and_zero_placement_preserve_effect_only_ineligibility(self):
        declined = replace(self.any_placement, optional=True)
        zero = replace(self.any_placement, operations=(SetField("amount", 0),))
        for effect, selection in ((declined, "decline:any"), (zero, "any")):
            with self.subTest(selection=selection):
                effects = (effect, self.effect_only)
                first = replacement_choice(self.current, effects)
                result = apply_replacement(first, effects, selection)
                self.assertIs(result.payload[COUNTER_REPLACEMENT_GENERATED_FIELD], False)
                self.assertIsNone(replacement_choice(result, effects))

    def test_forged_counter_replacement_provenance_rejects(self):
        for changed in (
            {COUNTER_REPLACEMENT_GENERATED_FIELD: True},
            {COUNTER_REPLACEMENT_GENERATED_FIELD: 1},
            {"effect_generated": True},
        ):
            with self.subTest(changed=changed), self.assertRaisesRegex(ReplacementEffectError, "provenance"):
                data = self.current.to_dict()
                data["payload"].update(changed)
                ReplaceableEvent.from_dict(data)

    def test_counter_quantity_provenance_version_preserves_absent_record_field(self):
        state = GameState("counter-version", GameConfig(), {}, {}, {}, {}, [], None, None)
        current = state.to_dict()
        field = "counter_quantity_provenance_version"
        self.assertEqual(1, current[field])
        self.assertEqual(current, GameState.from_dict(current).to_dict())
        historical = {key: value for key, value in current.items() if key != field}
        self.assertEqual(historical, GameState.from_dict(historical).to_dict())
        for invalid in (True, 0, 2, "1"):
            with self.subTest(invalid=invalid), self.assertRaisesRegex(ValueError, "provenance version"):
                GameState.from_dict({**current, field: invalid})


class CounterPlacementReplacementTests(unittest.TestCase):
    """CR 122/614/616 witnesses for the typed counter producer boundary."""

    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        database = Path(cls.temporary.name) / "counter-replacements.sqlite3"
        build_fixture_database(
            [
                ROOT / "tests" / "fixtures" / "scryfall-exact-lists.json",
                ROOT
                / "tests"
                / "fixtures"
                / "counter-replacement-cards.json",
            ],
            database,
        )
        cls.db = CardDatabase(database)
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
        session.commands.clear()
        session.decisions.clear()
        return session

    def add_permanent(
        self,
        engine,
        *,
        seat: str,
        name: str,
        ref: str,
        owner: str | None = None,
    ) -> CardInstance:
        record = self.db.lookup(name)
        from common import register_current_fixture_programs
        register_current_fixture_programs(self.db, engine.semantics, (record,),
                                          profile=engine.state.config.review_profile)
        owner = owner or seat
        card = CardInstance(
            object_id=f"fixture:{ref}",
            ref=ref,
            oracle_id=record.oracle_id,
            printed_name=record.name,
            owner=owner,
            controller=seat,
            zone="battlefield",
            zone_timestamp=engine.state.event_sequence + 1,
            known_to=list(engine.seats),
            revealed_to=list(engine.seats),
        )
        engine.state.cards[card.object_id] = card
        engine.state.players[seat].zones["battlefield"].append(card.object_id)
        return card

    def stage_competing_sources(self, engine, *, seat: str = "A"):
        prefix = seat.casefold()
        doubling = self.add_permanent(
            engine,
            seat=seat,
            name="Doubling Season",
            ref=f"{prefix}-doubling",
        )
        doc = self.add_permanent(
            engine,
            seat=seat,
            name="Doc Samson, Super Psychiatrist",
            ref=f"{prefix}-doc",
        )
        return doubling, doc

    def register_vorinclex_counter_replacement(self, engine) -> None:
        register_generated_programs(
            self.db,
            engine.semantics,
            (self.db.lookup("Vorinclex, Monstrous Raider"),),
            trust_level="provisional",
            capability_registry=load_default_capability_registry(),
            capability_profile=engine.state.config.review_profile,
            promote_exact_runtime_handlers=True,
        )

    def test_counter_quantity_component_rejects_malformed_and_nonmatching_events(
        self,
    ):
        handler = CounterQuantityReplacementHandler()
        malformed = counter_descriptor(multiplier=0)
        with self.assertRaisesRegex(SemanticNodeError, "positive integer"):
            handler.validate(malformed)

        malformed = counter_descriptor(effect_generated=False)
        with self.assertRaisesRegex(SemanticNodeError, "effect_generated"):
            handler.validate(malformed)

        malformed = counter_descriptor(counter_names=["Charge", "charge"])
        with self.assertRaisesRegex(SemanticNodeError, "normalization"):
            handler.validate(malformed)

        with self.assertRaisesRegex(CounterPlacementError, "amount"):
            CounterPlacementRequest(
                subject_kind="permanent",
                subject_id="fixture",
                counter_name="charge",
                amount=True,
                placing_player="A",
            )

        effect = handler.replacement_effect(
            counter_descriptor(),
            CounterReplacementSourceContext(
                source_ref="doubling",
                source_controller="A",
            ),
        )
        for event in (
            CounterPlacementEventSpec(
                event_id="opponent-permanent",
                subject_kind="permanent",
                subject_id="target-b",
                owner="B",
                controller="B",
                target_zone="battlefield",
                target_types=("creature",),
                placing_player="B",
                counter_name="+1/+1",
                amount=1,
                source_ref=None,
                effect_generated=True,
            ).event(),
            CounterPlacementEventSpec(
                event_id="cost-placement",
                subject_kind="permanent",
                subject_id="target-a",
                owner="A",
                controller="A",
                target_zone="battlefield",
                target_types=("planeswalker",),
                placing_player="A",
                counter_name="loyalty",
                amount=1,
                source_ref=None,
                effect_generated=False,
            ).event(),
            CounterPlacementEventSpec(
                event_id="nonpermanent-card",
                subject_kind="permanent",
                subject_id="target-a-exile",
                owner="A",
                controller=None,
                target_zone="exile",
                target_types=("creature",),
                placing_player="A",
                counter_name="void",
                amount=1,
                source_ref=None,
                effect_generated=True,
            ).event(),
        ):
            resolution = resolve_counter_placement_replacements(
                batch_id=f"batch:{event.event_id}",
                events=(event,),
                effects=(effect,),
                apnap_order=("A", "B"),
            )
            self.assertIsNone(resolution.pending)
            self.assertEqual(1, resolution.batch.events[0].payload["amount"])
            self.assertEqual((), resolution.journal)

    def test_counter_quantity_component_changes_effect_generated_placement(
        self,
    ):
        session = self.session(12261401)
        engine = session.engine
        doubling = self.add_permanent(
            engine,
            seat="A",
            name="Doubling Season",
            ref="a-doubling",
        )
        target = self.add_permanent(
            engine,
            seat="A",
            name="Island",
            ref="a-target",
        )

        result = engine.apply_effect(
            {
                "op": "counter",
                "card": target.ref,
                "counter": "+1/+1",
                "delta": 2,
            },
            actor="A",
        )

        self.assertEqual(4, result)
        self.assertEqual(4, target.counters["+1/+1"])
        counter_event = next(
            event
            for event in reversed(engine.state.events)
            if event.code == "counter.add"
        )
        self.assertEqual(2, counter_event.details["requested"])
        self.assertEqual(4, counter_event.details["placed"])
        replacement_event = next(
            event
            for event in reversed(engine.state.events)
            if event.code == "replacement.apply"
        )
        self.assertEqual(doubling.ref, replacement_event.details["source"])
        self.assertEqual(
            "counter.placement.quantity_replacement",
            default_counter_placement_replacement_registry().inventory()[0][
                "capability_dependencies"
            ][0],
        )

    def test_legacy_energy_effect_uses_player_counter_placement_owner(self):
        session = self.session(12261421)
        engine = session.engine
        vorinclex = self.add_permanent(
            engine,
            seat="A",
            name="Vorinclex, Monstrous Raider",
            ref="a-vorinclex-energy",
        )
        self.register_vorinclex_counter_replacement(engine)

        result = engine.apply_effect(
            {
                "op": "energy",
                "player": "A",
                "delta": 2,
                "source": "legacy-energy-source",
            },
            actor="A",
        )

        self.assertEqual(4, result)
        self.assertEqual(4, engine.state.players["A"].energy)
        counter_event = next(
            event
            for event in reversed(engine.state.events)
            if event.code == "counter.add"
        )
        self.assertEqual("A", counter_event.details["player"])
        self.assertEqual("energy", counter_event.details["counter"])
        self.assertEqual(2, counter_event.details["requested"])
        self.assertEqual(4, counter_event.details["placed"])
        replacement_event = next(
            event
            for event in reversed(engine.state.events)
            if event.code == "replacement.apply"
        )
        self.assertEqual(vorinclex.ref, replacement_event.details["source"])
        self.assertFalse(
            any(event.code == "effect.energy" for event in engine.state.events)
        )

        malformed = (
            {"op": "energy", "player": "A", "delta": -1},
            {"op": "energy", "player": "A", "delta": 0},
            {"op": "energy", "player": "A", "delta": True},
            {
                "op": "energy",
                "player": "A",
                "delta": 1,
                "_replacement_selections": {},
            },
        )
        for effect in malformed:
            with self.subTest(effect=effect):
                before = authoritative_state_hash(engine.state)
                with self.assertRaises(GameRuleError):
                    engine.apply_effect(effect, actor="A")
                self.assertEqual(before, authoritative_state_hash(engine.state))

    def test_gonti_energy_trigger_uses_typed_player_counter_program(self):
        session = self.session(12261422)
        engine = session.engine
        heart = self.add_permanent(
            engine,
            seat="A",
            name="Gonti's Aether Heart",
            ref="a-gonti-heart",
        )
        vorinclex = self.add_permanent(
            engine,
            seat="A",
            name="Vorinclex, Monstrous Raider",
            ref="a-vorinclex-gonti",
        )
        self.register_vorinclex_counter_replacement(engine)
        program = next(
            value
            for value in engine.semantics.programs_for_oracle(
                "69428825-3c40-486d-b051-14e97a598ce6"
            )
            if value.event == "artifact.enter"
            and any(
                effect.get("op") == "place_player_counters"
                for effect in value.effects
            )
        )
        self.assertEqual(
            {
                "op": "place_player_counters",
                "subjects": "controller",
                "counter": "energy",
                "amount": 2,
                "source": "$source",
            },
            program.effects[0],
        )
        self.assertEqual("trusted", program.trust_level)
        self.assertTrue(program.capability_closure["trusted"])
        self.assertTrue(
            {
                "counter.producer.fixed_event_trigger",
                "counter.producer.fixed_player_effect",
                "trigger.event.normalized_zone_change",
                "trigger.placement.apnap",
            }.issubset(program.capability_dependencies)
        )
        self.assertIn(
            "counter.placement.quantity_replacement",
            program.capability_closure["reachable"],
        )

        engine.create_token(
            "A",
            name="Gonti Trigger Artifact",
            characteristics={"type_line": "Token Artifact"},
        )
        engine.state.priority_player = None
        engine._prepare_stack_resolution()

        self.assertEqual(4, engine.state.players["A"].energy)
        counter_event = next(
            event
            for event in reversed(engine.state.events)
            if event.code == "counter.add"
        )
        self.assertEqual(heart.ref, counter_event.details["source"])
        replacement_event = next(
            event
            for event in reversed(engine.state.events)
            if event.code == "replacement.apply"
        )
        self.assertEqual(vorinclex.ref, replacement_event.details["source"])

    def test_legacy_energy_counter_owner_mutant_is_killed(self):
        def assert_energy_commits() -> None:
            session = self.session(12261423)
            result = session.engine.apply_effect(
                {"op": "energy", "player": "A", "delta": 2},
                actor="A",
            )
            self.assertEqual(2, result)
            self.assertEqual(2, session.engine.state.players["A"].energy)

        assert_energy_commits()

        def bypass_counter_owner(*_args, **_kwargs):
            return (
                CounterPlacementResult(
                    subject_kind="player",
                    subject_id="A",
                    counter_name="energy",
                    requested=2,
                    placed=2,
                    before=0,
                    after=2,
                ),
            )

        with patch.object(
            energy_effect_module,
            "place_counters",
            bypass_counter_owner,
        ):
            with self.assertRaises(AssertionError):
                assert_energy_commits()

    def test_counter_replacements_apply_in_affected_players_chosen_order(self):
        session = self.session(12261402)
        engine = session.engine
        doubling, doc = self.stage_competing_sources(engine)
        first = self.add_permanent(
            engine,
            seat="A",
            name="Island",
            ref="a-first-target",
        )
        before = authoritative_state_hash(engine.state)
        effect = {
            "op": "counter",
            "card": first.ref,
            "counter": "+1/+1",
            "delta": 1,
        }

        with self.assertRaises(ReplacementChoiceRequired) as required:
            engine.apply_effect(effect, actor="A")
        self.assertEqual(before, authoritative_state_hash(engine.state))
        self.assertNotIn("+1/+1", first.counters)
        pending = required.exception.pending
        self.assertEqual("A", pending.choice.chooser)
        self.assertEqual(2, len(pending.choice.options))
        doubling_first = next(
            option
            for option in pending.choice.options
            if doubling.ref in option
        )
        resolved = engine.apply_effect(
            {**effect, "_replacement_selections": [doubling_first]},
            actor="A",
        )
        self.assertEqual(3, resolved)

        second = self.add_permanent(
            engine,
            seat="A",
            name="Island",
            ref="a-second-target",
        )
        second_effect = {**effect, "card": second.ref}
        with self.assertRaises(ReplacementChoiceRequired) as required:
            engine.apply_effect(second_effect, actor="A")
        doc_first = next(
            option
            for option in required.exception.pending.choice.options
            if doc.ref in option
        )
        resolved = engine.apply_effect(
            {
                **second_effect,
                "_replacement_selections": [doc_first],
            },
            actor="A",
        )
        self.assertEqual(4, resolved)

    def test_simultaneous_counter_choices_follow_apnap(self):
        session = self.session(12261403, players=4)
        engine = session.engine
        self.stage_competing_sources(engine, seat="A")
        self.stage_competing_sources(engine, seat="B")
        target_a = self.add_permanent(
            engine,
            seat="A",
            name="Island",
            ref="a-apnap-target",
        )
        target_b = self.add_permanent(
            engine,
            seat="B",
            name="Island",
            ref="b-apnap-target",
        )
        requests = (
            CounterPlacementRequest(
                subject_kind="permanent",
                subject_id=target_b.object_id,
                counter_name="charge",
                amount=1,
                placing_player="B",
            ),
            CounterPlacementRequest(
                subject_kind="permanent",
                subject_id=target_a.object_id,
                counter_name="charge",
                amount=1,
                placing_player="A",
            ),
        )

        with self.assertRaises(ReplacementChoiceRequired) as first:
            prepare_counter_placements(engine, requests)
        self.assertEqual("A", first.exception.pending.choice.chooser)
        selected_a = first.exception.pending.choice.options[0]
        with self.assertRaises(ReplacementChoiceRequired) as second:
            prepare_counter_placements(
                engine,
                requests,
                selections=(selected_a,),
            )
        self.assertEqual("B", second.exception.pending.choice.chooser)
        self.assertNotIn("charge", target_a.counters)
        self.assertNotIn("charge", target_b.counters)

        selected_b = second.exception.pending.choice.options[0]
        prepared = prepare_counter_placements(
            engine,
            requests,
            selections=(selected_a, selected_b),
        )
        self.assertEqual(
            ["A", "A", "B", "B"],
            [entry.chooser for entry in prepared.journal],
        )
        results = commit_prepared_counter_placements(
            engine,
            prepared,
            reason="APNAP counter characterization",
        )
        self.assertEqual(2, len(results))
        self.assertGreater(target_a.counters["charge"], 1)
        self.assertGreater(target_b.counters["charge"], 1)

    def test_non_effect_and_inactive_source_do_not_replace_placement(self):
        session = self.session(12261404)
        engine = session.engine
        doubling = self.add_permanent(
            engine,
            seat="A",
            name="Doubling Season",
            ref="a-doubling",
        )
        cost_target = self.add_permanent(
            engine,
            seat="A",
            name="Island",
            ref="a-cost-target",
        )
        cost_result = place_counters(
            engine,
            (
                CounterPlacementRequest(
                    subject_kind="permanent",
                    subject_id=cost_target.object_id,
                    counter_name="loyalty",
                    amount=1,
                    placing_player="A",
                    effect_generated=False,
                ),
            ),
            reason="cost placement characterization",
        )
        self.assertEqual(1, cost_result[0].placed)

        engine.state.players["A"].zones["battlefield"].remove(
            doubling.object_id
        )
        doubling.zone = "graveyard"
        engine.state.players["A"].zones["graveyard"].append(
            doubling.object_id
        )
        inactive_target = self.add_permanent(
            engine,
            seat="A",
            name="Island",
            ref="a-inactive-target",
        )
        result = place_counters(
            engine,
            (
                CounterPlacementRequest(
                    subject_kind="permanent",
                    subject_id=inactive_target.object_id,
                    counter_name="charge",
                    amount=2,
                    placing_player="A",
                ),
            ),
            reason="inactive source characterization",
        )
        self.assertEqual(2, result[0].placed)

    def test_zone_replacement_counter_is_a_nested_precommit_event(self):
        zone_handler = ZoneDestinationReplacementHandler()
        zone_effect = zone_handler.replacement_effect(
            {
                "handler_id": "replacement.zone.destination.v1",
                "schema_version": 1,
                "event": "zone.change",
                "condition": {
                    "destination": "graveyard",
                    "object_kind": "card",
                    "owner_relation": "opponent",
                },
                "destination": "battlefield",
                "counters": {"charge": 1},
            },
            ZoneChangeReplacementContext(
                source_ref="zone-source",
                source_controller="B",
                object_id="entering-card",
                object_ref="A-card",
                object_owner="A",
                object_controller="A",
                object_types=("artifact",),
                origin="stack",
                destination="graveyard",
                is_card_object=True,
            ),
        )
        counter_effect = CounterQuantityReplacementHandler().replacement_effect(
            counter_descriptor(),
            CounterReplacementSourceContext(
                source_ref="doubling",
                source_controller="A",
            ),
        )

        resolution = resolve_zone_change_replacements(
            event_id="zone:entering-card",
            object_id="entering-card",
            owner="A",
            controller="A",
            origin="stack",
            destination="graveyard",
            is_card_object=True,
            effects=(counter_effect, zone_effect),
            apnap_order=("A", "B"),
        )

        self.assertIsNone(resolution.pending)
        self.assertEqual("battlefield", resolution.destination)
        self.assertEqual(1, len(resolution.counter_events))
        counter_event = resolution.counter_events[0]
        self.assertEqual("counter.place", counter_event.kind)
        self.assertEqual(2, counter_event.payload["amount"])
        self.assertEqual(
            [(), (0,)],
            [selection.path for selection in resolution.journal],
        )
        self.assertEqual(
            [zone_effect.effect_id, counter_effect.effect_id],
            [selection.effect_id for selection in resolution.journal],
        )

    def test_counter_replacement_choice_is_seat_scoped(self):
        session = self.session(12261405)
        engine = session.engine
        self.stage_competing_sources(engine)
        target = self.add_permanent(
            engine,
            seat="A",
            name="Island",
            ref="a-private-target",
        )
        program = SemanticProgram(
            key="test:counter-replacement-choice",
            label="Put a counter with competing replacements",
            effects=[
                {
                    "op": "counter",
                    "card": target.ref,
                    "counter": "+1/+1",
                    "delta": 1,
                }
            ],
            trust_level="provisional",
        )
        engine.semantics.put(program)
        item = StackItem(
            stack_id="counter-replacement-choice",
            ref="S-counter-replacement-choice",
            kind="triggered_ability",
            controller="A",
            label=program.label,
            semantic_key=program.key,
            visibility=["A", "B"],
        )
        engine.state.stack.append(item)

        engine._continue_resolution(
            stack_ref=item.ref,
            effects=[dict(value) for value in program.effects],
            destination=None,
            note="",
        )

        decision = engine.state.pending_decision
        self.assertIsNotNone(decision)
        self.assertEqual("replacement.order", decision.kind)
        self.assertEqual(["A"], decision.actors)
        projector = StateProjector(self.db, engine.state)
        projected_a = projector._decision("pilot:A")
        projected_b = projector._decision("pilot:B")
        self.assertIsNone(projected_b)
        self.assertEqual(2, len(projected_a["ctx"]["options"]))
        serialized = json.dumps(projected_a, sort_keys=True)
        self.assertNotIn("replacement_batch", serialized)
        self.assertNotIn("replacement_effects", serialized)
        self.assertNotIn(target.object_id, serialized)

        capability = engine.permissions.capability_for("pilot:A")
        before_rejection = authoritative_state_hash(engine.state)
        rejected = engine.try_submit(
            token=capability.token,
            principal="pilot:A",
            action="choose",
            payload={"replacement": "unknown"},
        )
        self.assertFalse(rejected.ok)
        self.assertEqual(before_rejection, authoritative_state_hash(engine.state))

        capability = engine.permissions.capability_for("pilot:A")
        selected = projected_a["ctx"]["options"][0]["id"]
        accepted = engine.submit(
            token=capability.token,
            principal="pilot:A",
            action="choose",
            payload={"replacement": selected},
        )
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertGreater(
            engine.state.cards[target.object_id].counters["+1/+1"],
            1,
        )
        self.assertFalse(
            any(candidate.ref == item.ref for candidate in engine.state.stack)
        )

    def test_counter_replacement_choice_replays_exactly(self):
        session = self.session(12261406)
        engine = session.engine
        self.stage_competing_sources(engine)
        target = self.add_permanent(
            engine,
            seat="A",
            name="Island",
            ref="a-replay-target",
        )
        program = SemanticProgram(
            key="test:counter-replacement-replay",
            label="Replay a counter replacement choice",
            effects=[
                {
                    "op": "counter",
                    "card": target.ref,
                    "counter": "charge",
                    "delta": 1,
                }
            ],
            trust_level="provisional",
        )
        engine.semantics.put(program)
        engine.state.stack.append(
            StackItem(
                stack_id="counter-replacement-replay",
                ref="S-counter-replacement-replay",
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

        for principal in ("pilot:A", "pilot:B"):
            result = session.act(principal, {"action_id": "pass"})
            self.assertTrue(result.ok, result.summary)
        self.assertEqual("replacement.order", engine.state.pending_decision.kind)
        projected = StateProjector(self.db, engine.state)._decision("pilot:A")
        selected = projected["ctx"]["options"][0]["id"]
        result = session.act(
            "pilot:A",
            {
                "action_id": "choose",
                "choices": {"replacement": selected},
            },
        )
        self.assertTrue(result.ok, result.summary)
        expected_hash = authoritative_state_hash(engine.state)

        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / "counter-replacement-record"
            session.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(3, replay["commands"])
        self.assertEqual(expected_hash, replay["final_state_hash"])

    def test_counter_replacement_property_1000_deterministic_transitions(self):
        handler = CounterQuantityReplacementHandler()
        randomizer = random.Random(12261416)
        for index in range(property_transitions()):
            amount = randomizer.randint(1, 20)
            multiplier = randomizer.randint(2, 4)
            additional = randomizer.randint(1, 4)
            multiply = handler.replacement_effect(
                counter_descriptor(multiplier=multiplier),
                CounterReplacementSourceContext(
                    source_ref=f"multiply-{index}",
                    source_controller="A",
                ),
            )
            add = handler.replacement_effect(
                counter_descriptor(multiplier=1, additional=additional),
                CounterReplacementSourceContext(
                    source_ref=f"add-{index}",
                    source_controller="A",
                ),
            )
            event = CounterPlacementEventSpec(
                event_id=f"counter-property-{index}",
                subject_kind="permanent",
                subject_id=f"target-{index}",
                owner="A",
                controller="A",
                target_zone="battlefield",
                target_types=("creature",),
                placing_player="A",
                counter_name="+1/+1",
                amount=amount,
                source_ref=None,
                effect_generated=True,
            ).event()
            multiply_first = index % 2 == 0
            selected = multiply.effect_id if multiply_first else add.effect_id
            effects = (add, multiply) if index % 3 else (multiply, add)
            resolution = resolve_counter_placement_replacements(
                batch_id=f"counter-property-batch-{index}",
                events=(event,),
                effects=effects,
                apnap_order=("A",),
                selections=(selected,),
            )
            expected = (
                amount * multiplier + additional
                if multiply_first
                else (amount + additional) * multiplier
            )
            self.assertIsNone(resolution.pending)
            self.assertEqual(
                expected,
                resolution.batch.events[0].payload["amount"],
            )
            self.assertEqual(2, len(resolution.journal))


if __name__ == "__main__":
    unittest.main()
