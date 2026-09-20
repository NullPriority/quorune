from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT, keep_all, load_assets, make_session
from quorune.ability_fragments import (
    ability_fragment_from_dict,
    ability_fragment_to_dict,
)
from quorune.compiler.ability_keyword_fragments import (
    lower_ability_keyword_fragments,
)
from quorune.compiler.trigger_participation_templates import (
    static_trigger_multiplier_handler,
)
from quorune.continuous_effect_state import commit_continuous_effect
from quorune.continuous_effects import (
    ContinuousEffect,
    ContinuousEffectDuration,
    ContinuousEffectOrigin,
    ContinuousObjectIdentity,
    ContinuousOperation,
    Layer,
)
from quorune.object_predicate import ObjectQuerySpec
from quorune.model import StackItem
from quorune.oracle_ir import compile_oracle_card
from quorune.projection import StateProjector
from quorune.record import (
    authoritative_state_hash,
    checkpoint_envelope,
    replay_record,
)
from quorune.rules.capabilities import CapabilityRegistry
from quorune.trigger_batches import (
    PendingTriggerItem,
    TriggerBatchError,
    TriggerOccurrence,
)
from quorune.trigger_participation import (
    StaticTriggerParticipation,
    TriggerMultiplierPredicate,
    TriggerMultiplierSpec,
    TriggerParticipationError,
    WardSpec,
)
from quorune.trigger_discovery import applicable_trigger_multipliers
from quorune.trigger_processing import collect_ward_occurrences


REGISTRY_PATH = ROOT / "quorune" / "rules" / "capability-registry.json"


class StaticTriggerParticipationTests(unittest.TestCase):
    def test_multiplier_snapshot_is_typed_canonical_and_source_pinned(self):
        spec = TriggerMultiplierSpec(
            predicate=(
                TriggerMultiplierPredicate.ANOTHER_CREATURE_OF_CHOSEN_TYPE
            ),
            exclude_self=True,
        )
        participation = StaticTriggerParticipation(
            source_object_id="card-physical",
            source_logical_object_id="card-logical-7",
            source_controller="A",
            active_zone="battlefield",
            spec=spec,
            chosen_creature_type="  Goblin  ",
        )

        self.assertEqual("goblin", participation.chosen_creature_type)
        self.assertEqual(
            "trigger.multiplier.another_creature_of_chosen_type",
            participation.capability_id,
        )
        self.assertEqual(participation.fingerprint, participation.fingerprint)
        self.assertEqual(
            participation.fingerprint,
            StaticTriggerParticipation(
                source_object_id="card-physical",
                source_logical_object_id="card-logical-7",
                source_controller="A",
                active_zone="battlefield",
                spec=TriggerMultiplierSpec.from_dict(spec.to_dict()),
                chosen_creature_type="goblin",
            ).fingerprint,
        )

    def test_multiplier_snapshot_rejects_missing_choice_and_wrong_self_rule(self):
        with self.assertRaises(TriggerParticipationError):
            TriggerMultiplierSpec(
                predicate=(
                    TriggerMultiplierPredicate.ANOTHER_CREATURE_OF_CHOSEN_TYPE
                ),
                exclude_self=False,
            )
        with self.assertRaises(TriggerParticipationError):
            StaticTriggerParticipation(
                source_object_id="card-physical",
                source_logical_object_id="card-logical-7",
                source_controller="A",
                active_zone="battlefield",
                spec=TriggerMultiplierSpec(
                    predicate=(
                        TriggerMultiplierPredicate.ANOTHER_CREATURE_OF_CHOSEN_TYPE
                    ),
                    exclude_self=True,
                ),
            )

    def test_multiplier_and_ward_fragments_have_closed_schemas(self):
        multiplier = TriggerMultiplierSpec(
            predicate=(
                TriggerMultiplierPredicate.ARTIFACT_OR_CREATURE_ENTERS
            ),
        )
        ward = WardSpec(generic_cost=2)

        self.assertEqual(
            multiplier,
            ability_fragment_from_dict(
                {
                    "kind": "trigger_multiplier",
                    "value": multiplier.to_dict(),
                }
            ),
        )
        self.assertEqual(
            ward,
            ability_fragment_from_dict(
                {"kind": "ward", "value": ward.to_dict()}
            ),
        )
        with self.assertRaises(TriggerParticipationError):
            TriggerMultiplierSpec.from_dict(
                {**multiplier.to_dict(), "oracle_text": "live authority"}
            )
        with self.assertRaises(TriggerParticipationError):
            WardSpec.from_dict({**ward.to_dict(), "arbitrary": True})


class TriggerParticipationCompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db, cls.mishra, cls.zimone = load_assets()
        cls.capabilities = CapabilityRegistry(
            json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
        )

    @classmethod
    def tearDownClass(cls):
        cls.db.close()

    def test_artifact_creature_enter_multiplier_compiles_with_precise_span(self):
        result = static_trigger_multiplier_handler(
            "If an artifact or creature entering causes a triggered ability "
            "of a permanent you control to trigger, that ability triggers "
            "an additional time."
        )

        self.assertIsNotNone(result)
        assert result is not None
        template_id, descriptor, capability_id = result
        self.assertEqual(
            "static-trigger-multiplier-artifact-creature-enters-v1",
            template_id,
        )
        self.assertEqual(
            "trigger.multiplier.artifact_or_creature_enters",
            capability_id,
        )
        fragment = ability_fragment_from_dict(descriptor["fragment"])
        self.assertIsInstance(fragment, TriggerMultiplierSpec)
        record = self.db.lookup("Panharmonicon")
        oracle_ir = compile_oracle_card(
            record,
            capability_registry=self.capabilities,
            capability_profile="commander_review",
        )
        handler_nodes = [
            node
            for face in oracle_ir.to_dict()["faces"]
            for node in face["nodes"]
            if any(
                handler.get("handler_id")
                == "ability.static.trigger-multiplier.v1"
                for handler in node.get("handlers", [])
            )
        ]
        self.assertEqual(1, len(handler_nodes))
        span = handler_nodes[0]["span"]
        self.assertEqual(1, span["line"])
        self.assertEqual(0, span["start"])
        self.assertGreater(span["end"], span["start"])

    def test_chosen_type_multiplier_compiles_and_uses_effective_fragment(self):
        result = static_trigger_multiplier_handler(
            "If a triggered ability of another creature you control of the "
            "chosen type triggers, it triggers an additional time."
        )
        self.assertIsNotNone(result)
        assert result is not None
        fragment = ability_fragment_from_dict(result[1]["fragment"])
        self.assertEqual(
            TriggerMultiplierPredicate.ANOTHER_CREATURE_OF_CHOSEN_TYPE,
            fragment.predicate,
        )
        self.assertTrue(fragment.exclude_self)
        self.assertIsNone(
            static_trigger_multiplier_handler(
                "If an ability of a creature you control triggers, it "
                "triggers two additional times."
            )
        )

    def test_fixed_generic_ward_compiles_and_unsupported_costs_remain_residual(self):
        fixed = lower_ability_keyword_fragments("Ward {2}", ("ward",))
        life = lower_ability_keyword_fragments("Ward—Pay 3 life.", ("ward",))

        self.assertIsNone(fixed.residual_kind)
        self.assertEqual(1, len(fixed.handlers))
        ward = ability_fragment_from_dict(fixed.handlers[0]["fragment"])
        self.assertEqual(WardSpec(generic_cost=2), ward)
        self.assertEqual("unsupported_ward_cost", life.residual_kind)
        self.assertFalse(life.handlers)


class TriggerProcessingOwnerIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db, cls.mishra, cls.zimone = load_assets()

    @classmethod
    def tearDownClass(cls):
        cls.db.close()

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
        engine.state.stack.clear()
        engine.state.pending_trigger_batches.clear()
        session.commands.clear()
        session.decisions.clear()
        return session

    @staticmethod
    def card(engine, seat: str, name: str):
        return next(
            card
            for card in engine.state.cards.values()
            if card.owner == seat and card.printed_name == name
        )

    def prepare_panharmonicon(self, engine):
        panharmonicon = self.card(engine, "A", "Panharmonicon")
        source = self.card(engine, "A", "Ichor Wellspring")
        engine.move_card(
            panharmonicon.object_id,
            "battlefield",
            controller="A",
            log=False,
        )
        engine.move_card(
            source.object_id,
            "battlefield",
            controller="A",
            log=False,
        )
        return panharmonicon, source

    @staticmethod
    def targeted_item(engine, target, *, controller: str = "B") -> StackItem:
        item = StackItem(
            stack_id=engine._stable_runtime_id("stack", "S-target"),
            ref="S-target",
            kind="activated_ability",
            controller=controller,
            label="Target the Ward permanent",
            semantic_key="fixture:target",
            targets=[target.ref],
            visibility=list(engine.seats),
        )
        engine.state.stack.append(item)
        return item

    def test_enter_multiplier_ignores_unrepresented_events_and_types(self):
        engine = self.session(603201).engine
        _panharmonicon, source = self.prepare_panharmonicon(engine)

        for event, types in (
            ("spell.cast", ["artifact"]),
            ("permanent.enter", ["land"]),
            ("permanent.leave", ["creature"]),
        ):
            with self.subTest(event=event, types=types):
                self.assertEqual(
                    (),
                    applicable_trigger_multipliers(
                        engine,
                        source=source,
                        controller="A",
                        event=event,
                        context={"types": types},
                    ),
                )

    def test_chosen_type_multiplier_excludes_self_and_wrong_type(self):
        engine = self.session(603202).engine
        throne = self.card(engine, "A", "Roaming Throne")
        engineer = self.card(engine, "A", "Goblin Engineer")
        wellspring = self.card(engine, "A", "Ichor Wellspring")
        for card in (throne, engineer, wellspring):
            engine.move_card(
                card.object_id,
                "battlefield",
                controller="A",
                log=False,
            )
        throne.annotations["chosen_creature_type"] = "Goblin"
        throne.annotations["chosen_creature_type_adds_subtype"] = True

        matching = applicable_trigger_multipliers(
            engine,
            source=engineer,
            controller="A",
            event="permanent.enter",
            context={"types": ["creature"]},
        )
        self.assertEqual(1, len(matching))
        self.assertEqual(
            (),
            applicable_trigger_multipliers(
                engine,
                source=throne,
                controller="A",
                event="permanent.enter",
                context={"types": ["creature"]},
            ),
        )
        self.assertEqual(
            (),
            applicable_trigger_multipliers(
                engine,
                source=wellspring,
                controller="A",
                event="permanent.enter",
                context={"types": ["artifact"]},
            ),
        )

    def test_multiplier_source_change_and_ability_removal_are_effective(self):
        engine = self.session(603203).engine
        panharmonicon, source = self.prepare_panharmonicon(engine)

        def count() -> int:
            return len(
                applicable_trigger_multipliers(
                    engine,
                    source=source,
                    controller="A",
                    event="permanent.enter",
                    context={"types": ["artifact"]},
                )
            )

        self.assertEqual(1, count())
        panharmonicon.phased_out = True
        self.assertEqual(0, count())
        panharmonicon.phased_out = False
        engine.change_control(
            panharmonicon.object_id,
            "B",
            reason="focused trigger-owner test",
        )
        self.assertEqual(0, count())
        engine.change_control(
            panharmonicon.object_id,
            "A",
            reason="focused trigger-owner test",
        )

        identity = ContinuousObjectIdentity(
            object_id=panharmonicon.object_id,
            logical_object_id=panharmonicon.logical_object_id,
        )
        commit_continuous_effect(
            engine.state,
            ContinuousEffect(
                effect_id="fixture:remove-trigger-multiplier",
                source_id="fixture:remove-abilities-source",
                layer=Layer.ABILITY,
                sublayer="6",
                timestamp=engine._next_zone_timestamp(),
                operations=(ContinuousOperation("remove_all_abilities"),),
                origin=ContinuousEffectOrigin.RESOLUTION,
                duration=ContinuousEffectDuration.UNTIL_END_OF_TURN,
                applies=ObjectQuerySpec(zones=("battlefield",)),
                locked_objects=(identity,),
            ),
        )
        self.assertEqual(0, count())
        with patch(
            "quorune.trigger_discovery.canonical_ability_fragments",
            return_value=(
                TriggerMultiplierSpec(
                    predicate=(
                        TriggerMultiplierPredicate.ARTIFACT_OR_CREATURE_ENTERS
                    )
                ),
            ),
        ):
            with self.assertRaises(AssertionError):
                self.assertEqual(0, count())

    def test_multiplier_occurrence_projection_contains_no_hidden_cards(self):
        session = self.session(603204, players=4)
        engine = session.engine
        panharmonicon = self.card(engine, "A", "Panharmonicon")
        source = self.card(engine, "A", "Ichor Wellspring")
        private_a = self.card(engine, "A", "Deflecting Swat")
        private_b = self.card(engine, "B", "Mystic Remora")
        engine.move_card(
            panharmonicon.object_id,
            "battlefield",
            controller="A",
            log=False,
        )
        engine.move_card(source.object_id, "hand", log=False)
        engine.move_card(private_a.object_id, "hand", log=False)
        engine.move_card(private_b.object_id, "hand", log=False)
        engine.move_card(
            source.object_id,
            "battlefield",
            controller="A",
            semantic_events=True,
        )

        projection = json.dumps(
            StateProjector(self.db, engine.state)._snapshot("pilot:B"),
            sort_keys=True,
        )
        self.assertIn(panharmonicon.ref, projection)
        self.assertIn(source.ref, projection)
        self.assertNotIn(private_a.ref, projection)
        own_projection = json.dumps(
            StateProjector(self.db, engine.state)._snapshot("pilot:A"),
            sort_keys=True,
        )
        self.assertNotIn(private_b.ref, own_projection)

    def test_multiple_multiplier_sources_share_apnap_occurrence_batch(self):
        session = self.session(6032041, players=4)
        engine = session.engine
        first = self.card(engine, "A", "Panharmonicon")
        second = self.card(engine, "C", "Panharmonicon")
        source = self.card(engine, "A", "Ichor Wellspring")
        for multiplier in (first, second):
            engine.move_card(
                multiplier.object_id,
                "battlefield",
                controller="A",
                log=False,
            )
        engine.move_card(
            source.object_id,
            "battlefield",
            controller="A",
            semantic_events=True,
        )

        self.assertEqual(1, len(engine.state.pending_trigger_batches))
        batch = engine.state.pending_trigger_batches[0]
        self.assertEqual(("A", "B", "C", "D"), batch.apnap_order)
        occurrences = [
            item
            for item in batch.items
            if item.source_object_id == source.object_id
            and item.source_ability_id is not None
        ]
        self.assertEqual(3, len(occurrences))
        self.assertEqual(
            1,
            len({item.source_ability_id for item in occurrences}),
        )
        program = engine.semantics.get(occurrences[0].source_ability_id)
        self.assertIsNotNone(program)
        assert program is not None
        self.assertIn("fixed_multi_event_subscription", program.coverage)
        copy_sources = {
            item.copy_provenance["source_object_id"]
            for item in occurrences
            if item.copy_provenance
        }
        self.assertEqual({first.object_id, second.object_id}, copy_sources)

    def test_chosen_type_multiplier_runs_in_four_player_apnap_batch(self):
        session = self.session(6032042, players=4)
        engine = session.engine
        throne = self.card(engine, "A", "Roaming Throne")
        engineer = self.card(engine, "A", "Goblin Engineer")
        for card in (throne, engineer):
            engine.move_card(
                card.object_id,
                "battlefield",
                controller="A",
                log=False,
            )
        throne.annotations["chosen_creature_type"] = "Goblin"
        throne.annotations["chosen_creature_type_adds_subtype"] = True

        refs = engine._dispatch_semantic_event(
            "permanent.enter",
            {
                "card": engineer.ref,
                "controller": "A",
                "types": ["creature"],
            },
            sources=[engineer],
        )

        self.assertEqual(2, len(refs))
        batch = engine.state.pending_trigger_batches[0]
        self.assertEqual(("A", "B", "C", "D"), batch.apnap_order)
        self.assertEqual(
            throne.object_id,
            next(
                item
                for item in batch.items
                if item.copy_provenance
            ).copy_provenance["source_object_id"],
        )

    def test_multiplier_occurrence_replays_exactly(self):
        session = self.session(603205, players=4)
        engine = session.engine
        panharmonicon = self.card(engine, "A", "Panharmonicon")
        source = self.card(engine, "A", "Ichor Wellspring")
        engine.move_card(
            panharmonicon.object_id,
            "battlefield",
            controller="A",
            log=False,
        )
        engine.move_card(
            source.object_id,
            "battlefield",
            controller="A",
            semantic_events=True,
        )
        self.assertTrue(engine._stabilize())
        decision = engine.state.pending_decision
        self.assertIsNotNone(decision)
        assert decision is not None
        self.assertEqual("trigger.order", decision.kind)
        trigger_refs = [
            item.ref for item in engine.state.pending_trigger_batches[0].items
        ]
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()

        result = session.act(
            "pilot:A",
            {"action_id": "order", "triggers": trigger_refs},
        )
        self.assertTrue(result.ok, result.summary)
        expected_hash = authoritative_state_hash(engine.state)
        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / "trigger-multiplier-replay"
            session.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(expected_hash, replay["final_state_hash"])

    def test_ward_unsupported_costs_remain_residual_and_removed_ability_does_not_trigger(self):
        unsupported = lower_ability_keyword_fragments(
            "Ward—Pay 3 life.", ("ward",)
        )
        self.assertEqual("unsupported_ward_cost", unsupported.residual_kind)

        engine = self.session(7022101).engine
        throne = self.card(engine, "A", "Roaming Throne")
        engine.move_card(
            throne.object_id,
            "battlefield",
            controller="A",
            log=False,
        )
        targeted = self.targeted_item(engine, throne)
        self.assertEqual(1, len(collect_ward_occurrences(engine, targeted)))
        engine.state.pending_trigger_batches.clear()

        identity = ContinuousObjectIdentity(
            object_id=throne.object_id,
            logical_object_id=throne.logical_object_id,
        )
        commit_continuous_effect(
            engine.state,
            ContinuousEffect(
                effect_id="fixture:remove-ward",
                source_id="fixture:remove-abilities-source",
                layer=Layer.ABILITY,
                sublayer="6",
                timestamp=engine._next_zone_timestamp(),
                operations=(ContinuousOperation("remove_all_abilities"),),
                origin=ContinuousEffectOrigin.RESOLUTION,
                duration=ContinuousEffectDuration.UNTIL_END_OF_TURN,
                applies=ObjectQuerySpec(zones=("battlefield",)),
                locked_objects=(identity,),
            ),
        )
        self.assertEqual([], collect_ward_occurrences(engine, targeted))
        with patch(
            "quorune.trigger_processing.canonical_ability_fragments",
            return_value=(WardSpec(generic_cost=2),),
        ):
            with self.assertRaises(AssertionError):
                self.assertEqual([], collect_ward_occurrences(engine, targeted))

    def test_multiple_ward_instances_and_control_change_use_current_state(self):
        session = self.session(7022102, players=4)
        engine = session.engine
        throne = self.card(engine, "A", "Roaming Throne")
        private_a = self.card(engine, "A", "Deflecting Swat")
        private_b = self.card(engine, "B", "Mystic Remora")
        engine.move_card(
            throne.object_id,
            "battlefield",
            controller="A",
            log=False,
        )
        engine.move_card(private_a.object_id, "hand", log=False)
        engine.move_card(private_b.object_id, "hand", log=False)
        engine.change_control(
            throne.object_id,
            "C",
            reason="focused Ward controller test",
        )
        identity = ContinuousObjectIdentity(
            object_id=throne.object_id,
            logical_object_id=throne.logical_object_id,
        )
        commit_continuous_effect(
            engine.state,
            ContinuousEffect(
                effect_id="fixture:grant-second-ward",
                source_id="fixture:ward-grant-source",
                layer=Layer.ABILITY,
                sublayer="6",
                timestamp=engine._next_zone_timestamp(),
                operations=(
                    ContinuousOperation(
                        "add_ability_fragment",
                        ability_fragment_to_dict(WardSpec(generic_cost=3)),
                    ),
                ),
                origin=ContinuousEffectOrigin.RESOLUTION,
                duration=ContinuousEffectDuration.UNTIL_END_OF_TURN,
                applies=ObjectQuerySpec(zones=("battlefield",)),
                locked_objects=(identity,),
            ),
        )
        targeted = self.targeted_item(engine, throne, controller="B")

        refs = collect_ward_occurrences(engine, targeted)

        self.assertEqual(2, len(refs))
        occurrences = engine.state.pending_trigger_batches[0].items
        self.assertEqual({"C"}, {item.controller for item in occurrences})
        self.assertEqual(
            [2, 3],
            sorted(item.event_facts["cost"]["GENERIC"] for item in occurrences),
        )
        projection_a = json.dumps(
            StateProjector(self.db, engine.state)._snapshot("pilot:A"),
            sort_keys=True,
        )
        projection_b = json.dumps(
            StateProjector(self.db, engine.state)._snapshot("pilot:B"),
            sort_keys=True,
        )
        self.assertNotIn(private_b.ref, projection_a)
        self.assertNotIn(private_a.ref, projection_b)

        engine.state.pending_trigger_batches.clear()
        engine.change_control(
            throne.object_id,
            "B",
            reason="Ward does not trigger for its controller",
        )
        self.assertEqual([], collect_ward_occurrences(engine, targeted))

    def test_ward_occurrence_replays_exactly(self):
        session = self.session(7022103, players=4)
        engine = session.engine
        throne = self.card(engine, "A", "Roaming Throne")
        engine.move_card(
            throne.object_id,
            "battlefield",
            controller="A",
            log=False,
        )
        targeted = self.targeted_item(engine, throne, controller="B")
        self.assertEqual(1, len(collect_ward_occurrences(engine, targeted)))
        self.assertFalse(engine._stabilize())
        engine.state.active_player = "A"
        engine.state.phase = "precombat_main"
        engine.state.step = "main"
        engine._grant_priority("A")
        engine._issue_priority("A")
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()

        for seat in engine.seats:
            result = session.act(f"pilot:{seat}", {"action_id": "pass"})
            self.assertTrue(result.ok, result.summary)
        self.assertEqual("semantic.choice", engine.state.pending_decision.kind)
        result = session.act(
            "pilot:B",
            {
                "action_id": "choose",
                "pay": False,
                "plan": "DECLINE_WARD",
                "reason": "Preserve mana.",
            },
        )
        self.assertTrue(result.ok, result.summary)
        self.assertNotIn(targeted, engine.state.stack)

        expected_hash = authoritative_state_hash(engine.state)
        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / "ward-replay"
            session.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(expected_hash, replay["final_state_hash"])


class TriggerOccurrenceTests(unittest.TestCase):
    @staticmethod
    def payload() -> dict:
        return {
            "stack_id": "stack-1",
            "ref": "S1",
            "kind": "triggered_ability",
            "controller": "A",
            "label": "Typed trigger",
            "source_object_id": "physical-1",
            "semantic_key": "oracle:ability:ab1",
            "visibility": ["A", "B"],
            "context": {
                "event": "permanent.enter",
                "source_logical_object_id": "logical-2",
                "source_zone": "battlefield",
                "intervening_condition": {"field": "enabled"},
                "trigger_target_selection_pending": True,
                "additional_trigger_source": {
                    "source_object_id": "multiplier-1"
                },
                "one_or_more_aggregation_id": "aggregation-1",
            },
        }

    def test_occurrence_is_immutable_canonical_and_game_record_compatible(self):
        payload = self.payload()
        occurrence = TriggerOccurrence.from_dict(payload)
        payload["context"]["event"] = "tampered"

        self.assertIs(PendingTriggerItem, TriggerOccurrence)
        self.assertEqual("permanent.enter", occurrence.normalized_event_id)
        self.assertEqual("logical-2", occurrence.source_logical_object_id)
        self.assertTrue(occurrence.target_selection_required)
        self.assertEqual(
            occurrence.fingerprint,
            TriggerOccurrence.from_dict(occurrence.to_dict()).fingerprint,
        )
        self.assertEqual(occurrence.to_dict(), PendingTriggerItem.from_dict(
            occurrence.to_dict()
        ).to_dict())

    def test_occurrence_rejects_malformed_typed_context(self):
        malformed_values = (
            {"event": ""},
            {"source_logical_object_id": 4},
            {"trigger_target_selection_pending": 1},
            {"intervening_condition": []},
            {"additional_trigger_source": "oracle text"},
        )
        for update in malformed_values:
            with self.subTest(update=update):
                payload = self.payload()
                payload["context"].update(update)
                with self.assertRaises(TriggerBatchError):
                    TriggerOccurrence.from_dict(payload)


if __name__ == "__main__":
    unittest.main()
