from __future__ import annotations

import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT
from damage_replacement_support import DamageReplacementPipelineBase
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.damage import resolve_damage_batch, source_snapshot
from quorune.damage_modifier_state import (
    DamageModifierDuration,
    DamagePreventionShield,
    DamageSubject,
    PreventionMode,
)
from quorune.model import GameState
from quorune.engine import GameRuleError
from quorune.prevention_triggers import (
    DealDamagePreventionTrigger,
    DrawCardsPreventionTrigger,
    PlaceCountersPreventionTrigger,
    PreventionTriggeredAbility,
    PreventionTriggerError,
    prevention_trigger_result_from_dict,
)
from quorune.trigger_processing import begin_pending_trigger_batch
from quorune.record import (
    authoritative_state_hash,
    checkpoint_envelope,
    replay_record,
)
from quorune.semantics import SemanticProgram
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database


class PreventionTriggerStackTests(DamageReplacementPipelineBase):
    def _ability(self, engine, source, *, player: str = "B"):
        pinned = source_snapshot(
            engine,
            source.ref,
            controller=source.controller,
        )
        return PreventionTriggeredAbility(
            controller=source.controller,
            source=pinned,
            label="Damage prevented this way",
            results=(
                DealDamagePreventionTrigger(
                    source=pinned,
                    recipient_kind="prevented_source_controller",
                    per_prevented=1,
                ),
                DrawCardsPreventionTrigger(
                    player=player,
                    per_prevented=1,
                ),
            ),
        )

    def _shield(
        self,
        engine,
        prevention_source,
        *,
        shield_id: str = "prevention-trigger",
        subject: DamageSubject | None = None,
        mode: PreventionMode = PreventionMode.NEXT_INSTANCE,
    ) -> DamagePreventionShield:
        shield = DamagePreventionShield(
            shield_id=shield_id,
            source_id=prevention_source.ref,
            controller=prevention_source.controller,
            subject=subject
            or DamageSubject(ref="B", kind="player", controller="B"),
            mode=mode,
            remaining=None,
            duration=DamageModifierDuration.UNTIL_END_OF_TURN,
            created_turn_sequence=engine.state.turn_sequence,
            label="Prevent that damage",
            triggered_ability=self._ability(engine, prevention_source),
        )
        engine.state.damage_prevention_shields.append(shield)
        return shield

    def test_trigger_model_is_immutable_strict_and_checkpoint_safe(self):
        engine = self.session(615601).engine
        prevention_source = self.add_permanent(
            engine,
            seat="B",
            name="Goblin Engineer",
            ref="new-way-forward",
        )
        target_schema = {
            "zones": ["player", "battlefield"],
            "categories": ["player", "permanent"],
            "predicate": "damageable",
            "count": 1,
        }
        pinned = source_snapshot(engine, prevention_source.ref, controller="B")
        ability = PreventionTriggeredAbility(
            controller="B",
            source=pinned,
            label="Prevented-damage trigger",
            results=(
                DealDamagePreventionTrigger(
                    source=pinned,
                    recipient_kind="selected_target",
                    per_prevented=1,
                ),
            ),
            target_schema=target_schema,
        )
        target_schema["zones"].append("graveyard")
        self.assertEqual(
            ["player", "battlefield"],
            ability.to_dict()["target_schema"]["zones"],
        )

        shield = DamagePreventionShield(
            shield_id="serialized-trigger",
            source_id=prevention_source.ref,
            controller="B",
            subject=DamageSubject(ref="B", kind="player", controller="B"),
            mode=PreventionMode.ALL,
            remaining=None,
            duration=DamageModifierDuration.UNTIL_END_OF_TURN,
            created_turn_sequence=engine.state.turn_sequence,
            triggered_ability=ability,
        )
        engine.state.damage_prevention_shields.append(shield)
        restored = GameState.from_dict(copy.deepcopy(engine.state.to_dict()))
        self.assertEqual(
            shield.to_dict(), restored.damage_prevention_shields[0].to_dict()
        )

        malformed = ability.to_dict()
        malformed["unexpected"] = True
        with self.assertRaises(PreventionTriggerError):
            PreventionTriggeredAbility.from_dict(malformed)

        malformed = ability.to_dict()
        malformed["controller"] = 7
        with self.assertRaises(PreventionTriggerError):
            PreventionTriggeredAbility.from_dict(malformed)

        malformed_result = DrawCardsPreventionTrigger(
            player="B", fixed_amount=1
        ).to_dict()
        malformed_result["player"] = True
        with self.assertRaises(PreventionTriggerError):
            prevention_trigger_result_from_dict(malformed_result)

        with self.assertRaises(PreventionTriggerError):
            PlaceCountersPreventionTrigger(
                subject_ref="B",
                counter_name=1,  # type: ignore[arg-type]
                placing_player="B",
                fixed_amount=1,
            )

    def test_one_trigger_is_created_per_effect_for_simultaneous_damage(self):
        engine = self.session(615602, players=4).engine
        damage_source = self.add_permanent(
            engine, seat="A", name="Mishra, Eminent One", ref="damage-source"
        )
        prevention_source = self.add_permanent(
            engine,
            seat="B",
            name="Goblin Engineer",
            ref="prevention-source",
        )
        self._shield(
            engine,
            prevention_source,
            subject=DamageSubject(ref="*", kind="any", controller="B"),
            mode=PreventionMode.ALL,
        )

        result = resolve_damage_batch(
            engine,
            (
                self.proposal(
                    engine,
                    source=damage_source,
                    target="B",
                    amount=2,
                    event_id="damage:simultaneous:b",
                ),
                self.proposal(
                    engine,
                    source=damage_source,
                    target="D",
                    amount=3,
                    event_id="damage:simultaneous:d",
                ),
            ),
        )

        self.assertEqual(1, len(result.prevention_events))
        self.assertEqual(5, result.prevention_events[0].prevented_amount)
        waiting = [
            item
            for batch in engine.state.pending_trigger_batches
            for group in batch["groups"]
            for item in group["items"]
            if item["label"] == "Damage prevented this way"
        ]
        self.assertEqual(1, len(waiting))
        self.assertEqual(5, waiting[0]["context"]["prevented_amount"])
        self.assertEqual(
            ["damage:simultaneous:b", "damage:simultaneous:d"],
            waiting[0]["context"]["damage_event_ids"],
        )

    def test_unpreventable_damage_creates_no_prevention_trigger(self):
        engine = self.session(615603).engine
        damage_source = self.add_permanent(
            engine, seat="A", name="Mishra, Eminent One", ref="damage-source"
        )
        prevention_source = self.add_permanent(
            engine,
            seat="B",
            name="Goblin Engineer",
            ref="prevention-source",
        )
        self._shield(engine, prevention_source)

        result = resolve_damage_batch(
            engine,
            (
                self.proposal(
                    engine,
                    source=damage_source,
                    target="B",
                    amount=4,
                    event_id="damage:unpreventable",
                    unpreventable=True,
                ),
            ),
        )

        self.assertEqual((), result.prevention_events)
        self.assertEqual([], engine.state.pending_trigger_batches)
        self.assertEqual(36, engine.state.players["B"].life)

    def test_four_player_apnap_preserves_created_controller_after_control_change(self):
        engine = self.session(615609, players=4).engine
        engine.state.active_player = "A"
        damage_source = self.add_permanent(
            engine, seat="A", name="Mishra, Eminent One", ref="damage-source"
        )
        source_b = self.add_permanent(
            engine, seat="B", name="Goblin Engineer", ref="prevention-b"
        )
        source_d = self.add_permanent(
            engine, seat="D", name="Goblin Engineer", ref="prevention-d"
        )
        self._shield(
            engine,
            source_b,
            shield_id="prevention-trigger-b",
            subject=DamageSubject(ref="B", kind="player", controller="B"),
            mode=PreventionMode.ALL,
        )
        self._shield(
            engine,
            source_d,
            shield_id="prevention-trigger-d",
            subject=DamageSubject(ref="D", kind="player", controller="D"),
            mode=PreventionMode.ALL,
        )
        engine.change_control(
            source_b.object_id,
            "C",
            reason="prevention trigger controller characterization",
        )

        result = resolve_damage_batch(
            engine,
            (
                self.proposal(
                    engine,
                    source=damage_source,
                    target="B",
                    amount=2,
                    event_id="damage:apnap:b",
                ),
                self.proposal(
                    engine,
                    source=damage_source,
                    target="D",
                    amount=3,
                    event_id="damage:apnap:d",
                ),
            ),
        )

        self.assertEqual(2, len(result.prevention_events))
        groups = engine.state.pending_trigger_batches[0]["groups"]
        self.assertEqual(["B", "D"], [group["controller"] for group in groups])
        self.assertEqual("C", source_b.controller)
        self.assertEqual(
            "B",
            next(
                item["controller"]
                for group in groups
                for item in group["items"]
                if item["source_object_id"] == source_b.object_id
            ),
        )

    def test_general_prevention_trigger_uses_affected_player_condition(self):
        engine = self.session(615606, players=4).engine
        source_a = self.add_permanent(
            engine, seat="A", name="Mishra, Eminent One", ref="damage-source"
        )
        monitor_b = self.add_permanent(
            engine, seat="B", name="Goblin Engineer", ref="monitor-b"
        )
        monitor_c = self.add_permanent(
            engine, seat="C", name="Goblin Engineer", ref="monitor-c"
        )
        engine.semantics.put(
            SemanticProgram(
                key=(
                    f"{monitor_b.oracle_id}:"
                    "test:affected-player-prevention"
                ),
                label="Damage to this controller was prevented",
                oracle_id=monitor_b.oracle_id,
                ability_id="test:affected-player-prevention",
                active_zone="battlefield",
                event="damage.prevented",
                event_condition={
                    "field": "affected_players",
                    "op": "contains_any",
                    "value": ["$source.controller"],
                },
                effects=[
                    {
                        "op": "counter",
                        "card": "$source",
                        "counter": "+1/+1",
                        "delta": "$context.prevented_amount",
                        "source": "$source",
                    }
                ],
            )
        )
        engine.state.damage_prevention_shields.append(
            DamagePreventionShield(
                shield_id="general-prevention-trigger",
                source_id=monitor_b.ref,
                controller="B",
                subject=DamageSubject(ref="B", kind="player", controller="B"),
                mode=PreventionMode.ALL,
                remaining=None,
                duration=DamageModifierDuration.UNTIL_END_OF_TURN,
                created_turn_sequence=engine.state.turn_sequence,
            )
        )

        resolve_damage_batch(
            engine,
            (
                self.proposal(
                    engine,
                    source=source_a,
                    target="B",
                    amount=3,
                    event_id="damage:general-prevention-trigger",
                ),
            ),
        )

        waiting = [
            item
            for batch in engine.state.pending_trigger_batches
            for group in batch["groups"]
            for item in group["items"]
            if item["label"] == "Damage to this controller was prevented"
        ]
        self.assertEqual(1, len(waiting))
        self.assertEqual("B", waiting[0]["controller"])
        self.assertEqual(monitor_b.object_id, waiting[0]["source_object_id"])
        self.assertNotEqual(monitor_c.object_id, waiting[0]["source_object_id"])
        self.assertFalse(begin_pending_trigger_batch(engine))
        self.assertEqual(1, len(engine.state.stack))
        engine._prepare_stack_resolution()
        self.assertEqual(3, monitor_b.counters["+1/+1"])
        self.assertEqual(0, monitor_c.counters.get("+1/+1", 0))

    def test_targeted_prevention_trigger_chooses_target_when_put_on_stack(self):
        engine = self.session(615607).engine
        damage_source = self.add_permanent(
            engine, seat="A", name="Mishra, Eminent One", ref="damage-source"
        )
        prevention_source = self.add_permanent(
            engine,
            seat="B",
            name="Goblin Engineer",
            ref="targeted-prevention-source",
        )
        pinned = source_snapshot(
            engine,
            prevention_source.ref,
            controller=prevention_source.controller,
        )
        ability = PreventionTriggeredAbility(
            controller="B",
            source=pinned,
            label="Deal prevented damage to any target",
            results=(
                DealDamagePreventionTrigger(
                    source=pinned,
                    recipient_kind="selected_target",
                    per_prevented=1,
                ),
            ),
            target_schema={
                "zones": ["player", "battlefield"],
                "categories": ["player", "permanent"],
                "predicate": "damageable",
                "count": 1,
            },
        )
        engine.state.damage_prevention_shields.append(
            DamagePreventionShield(
                shield_id="targeted-prevention-trigger",
                source_id=prevention_source.ref,
                controller="B",
                subject=DamageSubject(ref="B", kind="player", controller="B"),
                mode=PreventionMode.ALL,
                remaining=None,
                duration=DamageModifierDuration.UNTIL_END_OF_TURN,
                created_turn_sequence=engine.state.turn_sequence,
                triggered_ability=ability,
            )
        )

        resolve_damage_batch(
            engine,
            (
                self.proposal(
                    engine,
                    source=damage_source,
                    target="B",
                    amount=3,
                    event_id="damage:targeted-prevention-trigger",
                ),
            ),
        )
        self.assertFalse(begin_pending_trigger_batch(engine))
        self.assertTrue(engine._begin_pending_trigger_target_selection())
        self.assertEqual("semantic.target", engine.state.pending_decision.kind)
        hidden_ref = engine.state.cards[
            engine.state.players["A"].zones["hand"][0]
        ].ref
        self.assertIn(
            "A",
            engine.state.pending_decision.payload_by_actor["B"][
                "target_schema"
            ]["legal_refs"],
        )
        self.assertNotIn(
            hidden_ref,
            engine.state.pending_decision.payload_by_actor["B"][
                "target_schema"
            ]["legal_refs"],
        )

        capability = engine.permissions.capability_for("pilot:B")
        self.assertIsNotNone(capability)
        response = engine.submit(
            token=capability.token,
            principal="pilot:B",
            action="choose",
            payload={"targets": ["A"]},
        )
        self.assertTrue(response.ok, response.summary)
        self.assertEqual(["A"], engine.state.stack[-1].targets)

        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine.state.priority_player = None
        engine.state.priority_passes = []
        engine._prepare_stack_resolution()
        self.assertEqual(37, engine.state.players["A"].life)
        self.assertEqual(40, engine.state.players["B"].life)
        self.assertEqual([], engine.state.stack)

    def test_runtime_lowers_trigger_descriptor_and_rejects_bad_shape_atomically(self):
        engine = self.session(615605).engine
        prevention_source = self.add_permanent(
            engine,
            seat="B",
            name="Goblin Engineer",
            ref="prevention-source",
        )
        effect = {
            "op": "create_damage_prevention_shield",
            "source": prevention_source.ref,
            "subject": "B",
            "mode": "next_instance",
            "duration": "until_end_of_turn",
            "triggered_ability": {
                "source": prevention_source.ref,
                "label": "When damage is prevented this way",
                "target_schema": {},
                "results": [
                    {
                        "kind": "draw_cards",
                        "player": "B",
                        "per_prevented": 1,
                        "fixed_amount": 0,
                        "private": True,
                    }
                ],
            },
        }
        malformed = copy.deepcopy(effect)
        malformed["triggered_ability"]["unexpected"] = True
        with self.assertRaises(GameRuleError):
            engine.apply_effect(malformed, actor="B")
        self.assertEqual([], engine.state.damage_prevention_shields)

        engine.apply_effect(effect, actor="B")
        self.assertEqual(1, len(engine.state.damage_prevention_shields))
        trigger = engine.state.damage_prevention_shields[0].triggered_ability
        self.assertIsNotNone(trigger)
        self.assertEqual(
            "draw_cards", trigger.results[0].to_dict()["kind"]
        )

    def test_damage_result_rejects_mismatched_source_lki_before_mutation(self):
        engine = self.session(615608).engine
        prevention_source = self.add_permanent(
            engine,
            seat="B",
            name="Goblin Engineer",
            ref="prevention-source",
        )
        pinned = source_snapshot(engine, prevention_source.ref, controller="B")

        with self.assertRaises(GameRuleError):
            engine.apply_effect(
                {
                    "op": "damage",
                    "source": "different-source",
                    "source_snapshot": pinned.to_dict(),
                    "target": "A",
                    "amount": 3,
                },
                actor="B",
            )

        self.assertEqual(40, engine.state.players["A"].life)

    def test_trigger_uses_source_lki_resolves_on_stack_and_replays(self):
        session = self.session(615604)
        engine = session.engine
        damage_source = self.add_permanent(
            engine, seat="A", name="Mishra, Eminent One", ref="damage-source"
        )
        prevention_source = self.add_permanent(
            engine,
            seat="B",
            name="Goblin Engineer",
            ref="new-way-forward",
        )
        self._shield(engine, prevention_source)
        engine.move_card(prevention_source.object_id, "graveyard", log=False)
        hand_before = len(engine.state.players["B"].zones["hand"])

        resolve_damage_batch(
            engine,
            (
                self.proposal(
                    engine,
                    source=damage_source,
                    target="B",
                    amount=4,
                    event_id="damage:new-way-forward",
                ),
            ),
        )
        self.assertEqual(40, engine.state.players["B"].life)
        self.assertEqual(40, engine.state.players["A"].life)
        engine.state.damage_prevention_shields.append(
            DamagePreventionShield(
                shield_id="nested-trigger-damage-prevention",
                source_id=damage_source.ref,
                controller="A",
                subject=DamageSubject(ref="A", kind="player", controller="A"),
                mode=PreventionMode.NEXT_INSTANCE,
                remaining=None,
                duration=DamageModifierDuration.UNTIL_END_OF_TURN,
                created_turn_sequence=engine.state.turn_sequence,
            )
        )
        self.assertFalse(begin_pending_trigger_batch(engine))
        self.assertEqual(1, len(engine.state.stack))
        item = engine.state.stack[-1]
        self.assertEqual(
            "battlefield",
            item.context["dynamic_effects"][0]["source_snapshot"]["zone"],
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
            response = session.act(principal, {"action_id": "pass"})
            self.assertTrue(response.ok, response.summary)

        self.assertEqual(40, engine.state.players["A"].life)
        self.assertEqual(
            hand_before + 4,
            len(engine.state.players["B"].zones["hand"]),
        )
        self.assertEqual([], engine.state.stack)
        expected_hash = authoritative_state_hash(engine.state)

        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / "prevention-trigger-record"
            session.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(expected_hash, replay["final_state_hash"])


class StaticSelfPreventionTriggerTests(InheritedNativeFixture, unittest.TestCase):
    source_name = "Static Prevention Test Creature"

    @classmethod
    def source_fixture(cls):
        return {"cards": [{
            "name": cls.source_name, "oracle_id": "1f443bf3-8128-495f-9a22-07a3631e5e41",
            "type_line": "Creature — Horror", "mana_cost": "{W}{W}{W}{W}", "cmc": 4,
            "color_identity": ["W"], "colors": ["W"], "power": "5", "toughness": "5", "keywords": ["Flying"],
            "oracle_text": "Flying\nIf damage would be dealt to this creature, prevent that damage. "
                           "When damage is prevented this way, this creature deals that much damage to any other target.",
        }]}

    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / "static-prevention-trigger.sqlite3"
        fixture = path.with_suffix(".json")
        payload = {**cls.source_fixture(), "schema_version": 1}
        payload["cards"].append({
            "name": "Static Prevention Test Flier", "oracle_id": "5eaeed04-b360-4c8f-bcd7-d55da0842ba6",
            "type_line": "Creature — Bird", "power": "2", "toughness": "6",
            "oracle_text": "Flying", "keywords": ["Flying"],
        })
        fixture.write_text(json.dumps(payload), encoding="utf-8")
        fixtures = ROOT / "tests" / "fixtures"
        build_fixture_database([
            fixtures / "bound-effect-program-cards.json",
            fixtures / "intrinsic-noncombat-prevention-life-gain-cards.json",
            fixture,
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Static prevention", [
            DeckEntry("Generic Bound Commander", 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], ["Generic Bound Commander"])

    def submit(self, game, seat, action, **params):
        result = game.act(f"pilot:{seat}", {"action_id": action, **params})
        self.assertTrue(result.ok, result.summary)

    def reject(self, game, seat, action, **params):
        before = authoritative_state_hash(game.state)
        result = game.act(f"pilot:{seat}", {"action_id": action, **params})
        self.assertFalse(result.ok)
        self.assertEqual(before, authoritative_state_hash(game.state))

    def target_offer(self, game, seat):
        self.assertEqual(("semantic.target", [seat]),
                         (game.state.pending_decision.kind, game.state.pending_decision.actors))
        return game.packet(f"pilot:{seat}", full=True)["decision"]["ctx"]["target_schema"]["legal_refs"]

    def test_static_trigger_scope_closes_and_rejects_malformed_foreign_roles(self):
        from dataclasses import replace
        from quorune.card_programs.binding import bind_semantic_program_runtime
        from quorune.rules.static_prevention_trigger import CAPABILITY
        from quorune.semantic_runtime.context import SemanticNodeError
        from quorune.targets import TargetGroup

        game = self.session(6151301)
        source = self.add(game.engine, self.source_name)
        self.assert_closed(game, source)
        program, = [value for value in game.engine.semantics.programs_for_oracle(source.oracle_id)
                    if CAPABILITY in value.capability_dependencies]
        field, = program.handlers
        for changed in ({**field, "modification": {"amount": 2}}, {**field, "schema_version": True}):
            with self.assertRaisesRegex(SemanticNodeError, "closed self scope"):
                replace(program, handlers=(changed,))
        remaining = tuple(capability for capability in program.capability_dependencies if capability != CAPABILITY)
        bad = replace(program, capability_dependencies=remaining,
                      capability_closure=self.registry.closure(remaining, profile="commander_review").to_dict())
        binding = bind_semantic_program_runtime(bad, capability_registry=self.registry, profile="commander_review")
        self.assertFalse(binding["strict"], binding)
        self.assertIn(f"capability:undeclared_runtime_dependency:{CAPABILITY}", binding["blockers"])
        for flags in ({"exclude_source_incarnation": 1}, {"exclude_source_incarnation": True, "another": True}):
            with self.assertRaises(ValueError):
                TargetGroup.from_mapping(flags)

    def test_static_trigger_other_target_excludes_only_the_original_incarnation(self):
        from quorune.rules.static_prevention_trigger import other_damage_target_schema
        from quorune.target_source import ability_targeting_source
        from quorune.targets import TargetGroup

        game = self.session(6151305)
        source = self.add(game.engine, self.source_name)
        ref = source.ref
        original = ability_targeting_source(game.engine, source)
        group = TargetGroup.from_mapping(other_damage_target_schema())
        options = lambda: game.engine._target_candidates("A", group, source_ref=ref, acting_source=original)
        self.assertNotIn(ref, options())
        game.engine.move_card(source.object_id, "exile", log=False, reason="Source incarnation fixture")
        game.engine.move_card(source.object_id, "battlefield", log=False, reason="Source incarnation fixture")
        returned = game.state.cards[source.object_id]
        self.assertEqual(ref, returned.ref)
        self.assertNotEqual(original.logical_object_id, returned.logical_object_id)
        self.assertIn(returned.ref, options())

    def test_static_trigger_aggregates_combat_then_places_ordinary_apnap_targets_and_replays(self):
        game = self.session(6151302)
        source = self.add(game.engine, self.source_name, ref="prevention-attacker")
        defender = self.add(game.engine, self.source_name, seat="B", ref="prevention-blocker")
        flyers = [self.add(game.engine, "Static Prevention Test Flier", seat="B", ref=f"flier-{i}") for i in range(2)]
        second = self.add(game.engine, "Generic Bound Body", ref="second-attacker")
        other = self.add(game.engine, "Generic Bound Body", ref="unprevented-attacker")
        private = self.add(game.engine, "Generic Bound Growth", seat="D", zone="hand", ref="private-hand-card")
        for card in (source, second, other):
            card.acquired_control_turn_count = -1
        self.assert_closed(game, source)
        self.prepare(game)
        self.checkpoint(game)
        self.until(game, lambda current: current.state.pending_decision.kind == "combat.attackers")
        self.submit(game, "A", "attack", attackers={source.ref: "B", second.ref: "B", other.ref: "D"})
        self.until(game, lambda current: current.state.pending_decision.kind == "combat.blockers")
        self.submit(game, "B", "block", blocks={
            defender.ref: second.ref, **{flier.ref: source.ref for flier in flyers},
        })
        self.until(game, lambda current: current.state.pending_decision.kind == "combat.damage")
        self.submit(game, "A", "assign_damage", assignments=[
            {"source": source.ref, "target": flyers[0].ref, "amount": 5},
            {"source": second.ref, "target": defender.ref, "amount": 2},
            {"source": other.ref, "target": "D", "amount": 2},
        ])
        offer = self.target_offer(game, "A")
        self.assertNotIn(source.ref, offer)
        self.assertNotIn(private.ref, offer)
        self.assertIn("D", offer)
        self.assertIn(defender.ref, offer)
        self.assertEqual(0, game.state.cards[source.object_id].marked_damage)
        self.assertEqual(0, game.state.cards[defender.object_id].marked_damage)
        self.assertEqual(38, game.state.players["D"].life)
        self.reject(game, "A", "choose", targets=[source.ref])
        self.reject(game, "B", "choose", targets=["C"])
        game = self.replay(game, load=True)
        self.submit(game, "A", "choose", targets=["D"])
        offer = self.target_offer(game, "B")
        self.assertNotIn(defender.ref, offer)
        self.assertIn(source.ref, offer)
        self.submit(game, "B", "choose", targets=["C"])
        triggers = [value for value in game.state.stack if value.context.get("event") == "damage.prevented"]
        self.assertEqual([("A", 4), ("B", 2)],
                         [(trigger.controller, trigger.context["prevented_amount"]) for trigger in triggers])
        self.assertEqual(2, len(triggers[0].context["damage_event_ids"]))
        self.assertIsNone(self.resolve(game))
        self.assertEqual(38, game.state.players["C"].life)
        self.assertEqual(34, game.state.players["D"].life)
        self.replay(game)

    def test_static_trigger_uses_only_own_prevented_amount_and_survives_source_departure(self):
        game = self.session(6151303)
        source = self.add(game.engine, self.source_name)
        healer = self.add(game.engine, "Samite Healer")
        shot = self.add(game.engine, "Shock", seat="B", zone="hand")
        bounce = self.add(game.engine, "Unsummon", seat="B", zone="hand")
        healer.acquired_control_turn_count = -1
        self.prepare(game)
        game.state.players["B"].mana_pool.update({"R": 1, "U": 1})
        self.checkpoint(game)
        actions = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        ability = next(row["id"] for row in actions if row["id"].startswith(f"activate:{healer.ref}:"))
        self.submit(game, "A", ability, targets=[source.ref], pay="auto")
        self.resolve_top(game)
        shield, = game.state.damage_prevention_shields
        self.cast_for(game, "B", shot, [source.ref], reserve={"U": 1})
        choice = self.resolve(game)
        self.assertEqual(("replacement.order", ["A"]), (choice.kind, choice.actors))
        self.reject(game, "B", "choose", replacement=shield.effect_id)
        game = self.replay(game, load=True)
        self.submit(game, "A", "choose", replacement=shield.effect_id)
        self.target_offer(game, "A")
        self.submit(game, "A", "choose", targets=["C"])
        trigger = game.state.stack[-1]
        self.assertEqual(1, trigger.context["prevented_amount"])
        self.cast_for(game, "B", bounce, [source.ref])
        self.resolve_top(game)
        self.assertEqual("hand", game.state.cards[source.object_id].zone)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(39, game.state.players["C"].life)
        self.replay(game)

    def test_static_trigger_creates_nothing_when_damage_cannot_be_prevented(self):
        game = self.session(6151304)
        source = self.add(game.engine, self.source_name)
        shot = self.add(game.engine, "Shock", seat="B", zone="hand")
        pain = self.add(game.engine, "Flaring Pain", seat="B", zone="hand")
        self.prepare(game)
        game.state.players["B"].mana_pool.update({"C": 1, "R": 2})
        self.checkpoint(game)
        self.cast_for(game, "B", pain, reserve={"R": 1})
        self.resolve_top(game)
        self.cast_for(game, "B", shot, [source.ref])
        self.assertIsNone(self.resolve(game))
        self.assertEqual(2, game.state.cards[source.object_id].marked_damage)
        self.assertFalse(game.state.stack)
        self.replay(game)

    def test_static_trigger_revalidates_its_only_target(self):
        game = self.session(6151306)
        source = self.add(game.engine, self.source_name)
        other = self.add(game.engine, "Generic Bound Body", ref="departing-damage-target")
        shot = self.add(game.engine, "Shock", seat="B", zone="hand")
        bounce = self.add(game.engine, "Unsummon", seat="B", zone="hand")
        self.prepare(game)
        game.state.players["B"].mana_pool.update({"R": 1, "U": 1})
        self.checkpoint(game)
        self.cast_for(game, "B", shot, [source.ref], reserve={"U": 1})
        self.assertEqual("semantic.target", self.resolve(game).kind)
        self.submit(game, "A", "choose", targets=[other.ref])
        self.cast_for(game, "B", bounce, [other.ref])
        self.resolve_top(game)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("hand", game.state.cards[other.object_id].zone)
        self.assertEqual(0, game.state.cards[other.object_id].marked_damage)
        self.assertTrue(any(event.code == "target.illegal" for event in game.state.events))
        self.replay(game)

    def test_static_trigger_producer_mutation_is_killed(self):
        with patch("quorune.rules.static_prevention_trigger.applied_static_prevention_trigger", return_value=None):
            with self.assertRaisesRegex(AssertionError, "semantic.target.*priority"):
                self.test_static_trigger_aggregates_combat_then_places_ordinary_apnap_targets_and_replays()


if __name__ == "__main__":

    unittest.main()
