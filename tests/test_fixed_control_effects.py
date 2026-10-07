from __future__ import annotations

import copy
import json
from pathlib import Path
import tempfile
from unittest.mock import patch
import unittest

from common import keep_all, load_assets, make_session, pass_current
from quorune.compiler.fixed_control_templates import fixed_control_effect_template
from quorune.compiler.fixed_control_templates import fixed_control_set_effect_template
from quorune.compiler.untap_step_templates import static_untap_step_handler
from quorune.continuous_effect_model import ContinuousEffectDuration as Duration
from quorune.continuous_effect_state import ResolutionEffectSource, expire_end_of_turn_continuous_effects
from quorune.control_effects import (
    ControlEffectError, end_player_control_effects, gain_control_of_refs,
    has_control_origin, synchronize_control_effects,
)
from quorune.model import GameState, CardInstance
from quorune.oracle_ir import _reviewed_effect_template, register_generated_programs
from quorune.record import authoritative_state_hash, checkpoint_envelope, replay_record
from quorune.rules.capabilities import load_default_capability_registry, CapabilityRegistry, DEFAULT_CAPABILITY_REGISTRY
from quorune.rules.control_capability_shapes import fixed_control_node_capabilities
from quorune.semantic_runtime.control_handlers import FixedControlHandler, FixedControlSetHandler
from quorune.semantic_runtime.context import ReadOnlyHandlerContext, ReadOnlyRulesQuery, SemanticSourceContext, SemanticNodeError
from quorune.semantic_runtime.untap_steps import OptionalSourceUntapStepHandler, StaticUntapStepParticipationHandler, UntapStepSourceContext
from quorune.untap_step import UntapInstruction, plan_untap_step
from quorune.object_query import ObjectQueryResult
from quorune.session import CommanderSession
from quorune.optional_untap import OptionalUntapContinuation, OptionalUntapSubject
from quorune.semantic_runtime.control_intents import GainControlSetIntent
from quorune.object_predicate import ObjectQuerySpec
from quorune.untap_step import UntapStepPlan
from quorune.errors import GameRuleError


class FixedControlCompilerTests(unittest.TestCase):
    def test_control_and_optional_untap_dependency_closures_fail_closed(self):
        registry = load_default_capability_registry()
        raw = json.loads(Path(DEFAULT_CAPABILITY_REGISTRY).read_text(encoding="utf-8"))
        for row in raw["capabilities"]:
            if row["id"] == "permanent.untap.effect":
                row["status"] = "blocked"
                row["blockers"] = ["dependency failure diagnostic"]
        unavailable = CapabilityRegistry(raw)
        for capability in ("continuous.control.fixed_resolution", "untap.step.optional_source"):
            with self.subTest(capability=capability):
                self.assertTrue(registry.closure((capability,), profile="commander_review").trusted)
                closure = unavailable.closure((capability,), profile="commander_review")
                self.assertFalse(closure.trusted)
                self.assertIn("status:permanent.untap.effect:blocked", closure.blockers)

    def test_retained_optional_untap_rejects_coerced_clocks_and_subjects(self):
        frame = OptionalUntapContinuation(
            active_player="A", turn_sequence=1, phase_index=0,
            plan=UntapStepPlan("A", (), (), optional_object_ids=("object",)),
            rows=(OptionalUntapSubject("object", "object:original", "P1", "A", True),),
            available_object_ids=("object",), waiting_triggers=(),
        )
        mutations = (
            ("boolean turn", lambda value: value.update(turn_sequence=True)),
            ("wrong step index", lambda value: value.update(phase_index=1)),
            ("string subject array", lambda value: value["plan"].update(optional_object_ids="object")),
            ("boolean tapped coercion", lambda value: value["rows"][0].update(tapped="true")),
            ("duplicate incarnation", lambda value: value["rows"].append(copy.deepcopy(value["rows"][0]))),
            ("unavailable selection", lambda value: value.update(available_object_ids=["another-object"])),
            ("malformed held trigger", lambda value: value.update(waiting_triggers=[{}])),
        )
        self.assertEqual(frame, OptionalUntapContinuation.from_dict(frame.to_dict()))
        for label, mutate in mutations:
            with self.subTest(label=label):
                raw = frame.to_dict()
                mutate(raw)
                with self.assertRaises(GameRuleError):
                    OptionalUntapContinuation.from_dict(raw)

    def test_control_shapes_reject_other_principals_and_missing_duration_identity(self):
        template = fixed_control_effect_template(
            "Gain control of target creature for as long as you control this creature.",
            card_name="Control Witness", source_is_permanent=True, source_card_types=("creature",),
        )
        _, effects, schema, mechanics = template.compiled()
        self.assertIn("continuous.control.fixed_resolution", fixed_control_node_capabilities(
            effects=effects, target_schema=schema, mechanic_ids=mechanics))
        for replacement in ({"controller": "$target.current_controller.0"}, {"duration_source": "$target.0"}, {"extra": True}):
            mutated = ({**effects[0], **replacement},)
            self.assertEqual((), fixed_control_node_capabilities(
                effects=mutated, target_schema=schema, mechanic_ids=mechanics))

    def test_registered_control_lowering_retains_original_source_and_controller(self):
        context = ReadOnlyHandlerContext(
            actor="A", default_reason="control witness",
            query=ReadOnlyRulesQuery(("A", "B", "C"), ("A", "B", "C"), ("A", "B", "C")),
            source=SemanticSourceContext(stack_ref="S-control", object_id="source", logical_object_id="source@0", card_ref="P-source"),
        )
        effect = {"op": "gain_control", "card": "P-target", "controller": "A",
                  "source": "P-source", "duration": "zone_object"}
        intent = FixedControlHandler().lower(effect, context).intents[0]
        self.assertEqual("A", intent.controller)
        self.assertEqual("source@0", intent.source.logical_object_id)
        for extra in ({"controller": "B"}, {"duration": True}, {"amount": 2}):
            with self.assertRaises(SemanticNodeError):
                FixedControlHandler().lower({**effect, **extra}, context)

    def test_optional_untap_is_not_admitted_through_the_original_static_contract(self):
        _, descriptor, _ = static_untap_step_handler(
            "You may choose not to untap this artifact during your untap step.", source_name="Control Witness",
        )
        self.assertEqual(UntapInstruction.OPTIONAL, OptionalSourceUntapStepHandler().validate(descriptor).instruction)
        old = copy.deepcopy(descriptor)
        old["handler_id"] = StaticUntapStepParticipationHandler().handler_id
        with self.assertRaises(SemanticNodeError):
            StaticUntapStepParticipationHandler().validate(old)

    def test_control_and_optional_compiler_mutations_are_killed(self):
        text = "Gain control of target creature."
        self.assertIsNotNone(_reviewed_effect_template(text, card_name="Control Witness")[0])
        with patch("quorune.compiler.resolution_effect_templates.fixed_control_effect_template", return_value=None):
            self.assertIsNone(_reviewed_effect_template(text, card_name="Control Witness")[0])
        descriptor = static_untap_step_handler(
            "You may choose not to untap this artifact during your untap step.", source_name="Control Witness",
        )[1]
        participations = OptionalSourceUntapStepHandler().lower(
            descriptor, UntapStepSourceContext("source", "P-source", "A", True, "optional-source"),
        )
        rows = (ObjectQueryResult(object_id="source", logical_object_id="source@0", ref="P-source",
            printed_name="Control Witness", owner="B", controller="A", zone="battlefield", tapped=True),)
        plan = plan_untap_step(active_player="A", participations=participations, rows=rows)
        self.assertEqual(("source",), plan.optional_object_ids)
        with patch("quorune.untap_step._turn_matches", return_value=False):
            mutant = plan_untap_step(active_player="A", participations=participations, rows=rows)
        self.assertNotEqual(plan.optional_object_ids, mutant.optional_object_ids)

    def test_direct_control_and_untap_first_preserve_printed_order(self):
        for text, operations in (
            ("Gain control of target creature.", ["gain_control"]),
            ("Gain control of target artifact or creature until end of turn.", ["gain_control"]),
            ("Untap target creature and gain control of it until end of turn.", ["untap", "gain_control"]),
        ):
            with self.subTest(text=text):
                template = fixed_control_effect_template(text)
                self.assertIsNotNone(template)
                self.assertEqual(operations, [effect["op"] for effect in template.compiled()[1]])
        text = ("Untap target creature and gain control of it until end of turn. "
                "That creature gains haste until end of turn.")
        compiled = _reviewed_effect_template(text, card_name="Threaten")
        self.assertEqual(["untap", "gain_control", "grant_keyword_until_end_of_turn"],
                         [effect["op"] for effect in compiled[1]])

    def test_unsupported_control_subjects_and_durations_are_not_consumed(self):
        for text in (
            "Gain control of target spell.",
            "Gain control of target opponent during that player's next turn.",
            "Gain control of target creature for as long as you control this creature.",
            "Gain control of target creature until the end of your next turn.",
            "Gain control of target creature. You may sacrifice it.",
            "Untap target creature and gain control of another creature until end of turn.",
        ):
            with self.subTest(text=text):
                self.assertIsNone(fixed_control_effect_template(text))

    def test_source_duration_requires_the_original_permanent_context(self):
        text = "Gain control of target creature for as long as you control this creature."
        self.assertIsNone(fixed_control_effect_template(text))
        template = fixed_control_effect_template(
            text, card_name="Control Witness", source_is_permanent=True,
            source_card_types=("creature",),
        )
        self.assertEqual(Duration.UNTIL_SOURCE_CONTROL_CHANGES, template.duration)
        self.assertEqual("$source.zone_object", template.compiled()[1][0]["duration_source"])
        self.assertIsNone(fixed_control_effect_template(
            "Gain control of target creature for as long as you control another creature.",
            card_name="Control Witness", source_is_permanent=True, source_card_types=("creature",),
        ))

    def test_set_sequence_retains_explicit_order_and_ownership_selection(self):
        template = fixed_control_set_effect_template(
            "Untap all creatures and gain control of them until end of turn. They gain haste until end of turn."
        )
        self.assertEqual(("untap", "gain_control", "haste"), template.steps)
        own = fixed_control_set_effect_template("Gain control of all permanents you own.")
        self.assertEqual("$controller", own.predicate.owner)
        self.assertIsNone(fixed_control_set_effect_template(
            "Gain control of all creatures until end of turn. Untap another creature."
        ))

    def test_optional_untap_grammar_keeps_the_controller_as_chooser(self):
        template, handler, capability = static_untap_step_handler(
            "You may choose not to untap this artifact during your untap step.",
            source_name="Control Witness",
        )
        self.assertEqual("untap-step-optional-source-v1", template)
        self.assertEqual("source_controller", handler["subject"]["controller_relation"])
        self.assertEqual("untap.step.optional_source", capability)
        for text in (
            "Your opponents may choose not to untap this artifact during your untap step.",
            "You may choose not to untap target artifact during your untap step.",
            "You may choose not to untap this artifact during each player's untap step.",
        ):
            self.assertIsNone(static_untap_step_handler(text, source_name="Control Witness"))


class FixedControlJournalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db, cls.mishra, cls.zimone = load_assets()

    @classmethod
    def tearDownClass(cls):
        cls.db.close()

    def setUp(self):
        self.session = make_session(self.db, self.mishra, self.zimone, players=4, seed=61120001)
        keep_all(self.session)
        self.engine = self.session.engine
        ref = self.engine.create_token("B", name="Control Bear", characteristics={
            "type_line": "Creature — Bear", "power": "2", "toughness": "2", "colors": ["G"],
        })[0]
        self.card = self.engine._resolve_object("B", ref, zones={"battlefield"})
        self.source = ResolutionEffectSource(stack_ref="control-test-stack")

    def gain(self, seat, duration=Duration.UNTIL_END_OF_TURN):
        return gain_control_of_refs(
            self.engine, actor=seat, object_refs=(self.card.ref,), controller=seat,
            duration=duration, source=self.source, reason="control journal diagnostic",
        )

    def expire(self):
        expire_end_of_turn_continuous_effects(self.engine.state)
        synchronize_control_effects(self.engine, reason="temporary control expired")

    def test_expiration_restores_original_controller_rather_than_owner(self):
        self.engine.change_control(self.card.object_id, "C", reason="initial custody")
        self.gain("A")
        self.assertEqual("A", self.card.controller)
        self.assertEqual("B", self.card.owner)
        self.expire()
        self.assertEqual("C", self.card.controller)
        self.assertIn(self.card.object_id, self.engine.state.players["C"].zones["battlefield"])
        self.assertNotIn(self.card.object_id, self.engine.state.players["A"].zones["battlefield"])

    def test_overlapping_temporary_and_indefinite_effects_follow_timestamps(self):
        self.gain("A")
        self.gain("C", Duration.ZONE_OBJECT)
        self.gain("D")
        self.assertEqual("D", self.card.controller)
        self.expire()
        self.assertEqual("C", self.card.controller)

    def test_same_controller_indefinite_effect_supersedes_legacy_restoration(self):
        self.engine.apply_effect({"op": "change_control_until_end_of_turn", "card": self.card.ref}, actor="A")
        acquired = self.card.acquired_control_timestamp
        self.gain("A", Duration.ZONE_OBJECT)
        self.assertEqual(acquired, self.card.acquired_control_timestamp)
        self.assertNotIn("control_previous", self.card.annotations.get("until_end_of_turn", {}))
        self.expire()
        self.assertEqual("A", self.card.controller)

    def test_legacy_temporary_effect_migrates_before_later_temporary_effect(self):
        self.engine.apply_effect({"op": "change_control_until_end_of_turn", "card": self.card.ref}, actor="C")
        self.gain("A")
        self.expire()
        self.assertEqual("B", self.card.controller)

    def test_control_journal_round_trip_preserves_hash_and_future_expiration(self):
        self.gain("A", Duration.ZONE_OBJECT)
        self.gain("C")
        expected = authoritative_state_hash(self.engine.state)
        self.engine.state = GameState.from_dict(copy.deepcopy(self.engine.state.to_dict()))
        self.card = self.engine.state.cards[self.card.object_id]
        self.assertEqual(expected, authoritative_state_hash(self.engine.state))
        self.expire()
        self.assertEqual("A", self.card.controller)

    def test_departing_controller_ends_its_effect_and_preserves_earlier_custody(self):
        self.gain("A", Duration.ZONE_OBJECT)
        self.gain("C")
        self.engine.state.players["C"].in_game = False
        self.assertEqual(1, end_player_control_effects(self.engine.state, "C"))
        synchronize_control_effects(self.engine, reason="controller left")
        self.assertEqual("A", self.card.controller)

    def test_duplicate_or_unavailable_journal_rejects_before_mutation(self):
        before = authoritative_state_hash(self.engine.state)
        with self.assertRaises(ControlEffectError):
            gain_control_of_refs(self.engine, actor="A", object_refs=(self.card.ref, self.card.ref),
                controller="A", duration=Duration.UNTIL_END_OF_TURN, source=self.source, reason="invalid")
        self.assertEqual(before, authoritative_state_hash(self.engine.state))
        self.assertFalse(has_control_origin(self.engine.state, self.card))
        self.engine.state.continuous_effects = None
        before = authoritative_state_hash(self.engine.state)
        with self.assertRaises(ControlEffectError):
            self.gain("A")
        self.assertEqual(before, authoritative_state_hash(self.engine.state))


class FixedControlGameplayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db, cls.mishra, cls.zimone = load_assets()

    @classmethod
    def tearDownClass(cls):
        cls.db.close()

    def session(self, seed):
        session = make_session(self.db, self.mishra, self.zimone, players=4, seed=seed)
        keep_all(session)
        engine = session.engine
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine.state.priority_player = None
        engine.state.priority_passes = []
        return session

    def card(self, session, name, seat, zone="battlefield", *, tapped=False):
        engine = session.engine
        record = self.db.lookup(name)
        ref = engine._next_ref("P")
        card = CardInstance(
            object_id=f"control-fixture:{ref}", ref=ref, oracle_id=record.oracle_id,
            printed_name=record.name, owner=seat, controller=seat, zone=zone,
            tapped=tapped, zone_timestamp=engine._next_zone_timestamp(),
            known_to=list(engine.seats) if zone == "battlefield" else [seat],
            revealed_to=list(engine.seats) if zone == "battlefield" else [],
        )
        engine.state.cards[card.object_id] = card
        engine.state.players[seat].zones[zone].append(card.object_id)
        register_generated_programs(
            self.db, engine.semantics, (record,), trust_level="trusted",
            capability_registry=load_default_capability_registry(), capability_profile="commander_review",
            promote_exact_runtime_handlers=True, promote_exact_trigger_programs=True,
            promote_exact_effect_programs=True, promote_exact_capability_declarations=True,
        )
        return card

    def priority(self, session):
        engine = session.engine
        engine.state.started = True
        engine.state.active_player = "A"
        engine.state.phase = "precombat_main"
        engine.state.step = "main"
        engine._grant_priority("A")
        engine.pump()

    def checkpoint(self, session):
        session.initial_checkpoint = checkpoint_envelope(session.engine.state)
        session.commands.clear()
        session.decisions.clear()

    def replay(self, session):
        expected = authoritative_state_hash(session.engine.state)
        with tempfile.TemporaryDirectory() as directory:
            session.save(directory)
            replay = replay_record(directory, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(expected, replay["final_state_hash"])

    def test_printed_control_spell_offers_accepts_principal_correct_targets_and_replays(self):
        session = self.session(61120101)
        engine = session.engine
        spell = self.card(session, "Act of Treason", "A", "hand")
        target = self.card(session, "Llanowar Elves", "B", tapped=True)
        engine.state.players["A"].mana_pool.update(C=2, R=1)
        self.priority(session)
        action_id = f"cast:{spell.ref}"
        offered = session.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        action = next(row for row in offered if row["id"] == action_id)
        self.assertIn(target.ref, action["target_schema"]["legal_refs"])
        before = authoritative_state_hash(engine.state)
        rejected = session.act("pilot:B", {"action_id": action_id, "targets": [target.ref], "pay": "auto"})
        self.assertFalse(rejected.ok)
        self.assertEqual(before, authoritative_state_hash(engine.state))
        self.checkpoint(session)
        accepted = session.act("pilot:A", {"action_id": action_id, "targets": [target.ref], "pay": "auto"})
        self.assertTrue(accepted.ok, accepted.summary)
        for _ in range(12):
            if not engine.state.stack:
                break
            pass_current(session)
        self.assertFalse(engine.state.stack)
        self.assertEqual("A", target.controller)
        self.assertEqual("B", target.owner)
        self.assertFalse(target.tapped)
        self.assertIn("haste", engine._combat_keywords(target))
        self.replay(session)

    def test_printed_insurrection_controls_one_original_public_set_and_replays(self):
        session = self.session(61120106)
        engine = session.engine
        spell = self.card(session, "Insurrection", "A", "hand")
        creatures = [self.card(session, "Llanowar Elves", seat, tapped=True) for seat in engine.seats]
        artifact = self.card(session, "Helm of Possession", "B", tapped=True)
        own_acquired = creatures[0].acquired_control_timestamp
        engine.state.players["A"].mana_pool.update(C=5, R=3)
        self.priority(session)
        actions = session.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        self.assertIn(f"cast:{spell.ref}", {row["id"] for row in actions})
        self.checkpoint(session)
        cast = session.act("pilot:A", {"action_id": f"cast:{spell.ref}", "pay": "auto"})
        self.assertTrue(cast.ok, cast.summary)
        for _ in range(12):
            if not engine.state.stack:
                break
            pass_current(session)
        self.assertFalse(engine.state.stack)
        for card, seat in zip(creatures, engine.seats):
            self.assertEqual("A", card.controller)
            self.assertEqual(seat, card.owner)
            self.assertFalse(card.tapped)
            self.assertIn("haste", engine._combat_keywords(card))
        self.assertEqual(own_acquired, creatures[0].acquired_control_timestamp)
        self.assertEqual("B", artifact.controller)
        self.assertTrue(artifact.tapped)
        grants = [effect for effect in engine.state.continuous_effects
                  if effect.effect_id.startswith("control-effect:")]
        self.assertEqual(1, len(grants))
        self.assertEqual({card.logical_object_id for card in creatures},
                         {identity.logical_object_id for identity in grants[0].locked_objects})
        self.replay(session)

    def test_retained_optional_choice_rejects_changed_controller_without_mutation(self):
        session = self.session(61120107)
        engine = session.engine
        source = self.card(session, "Rubinia Soulsinger", "A", tapped=True)
        engine.state.started = True
        engine.state.active_player = "A"
        engine.state.phase = "beginning"
        engine.state.step = "untap"
        engine.state.phase_index = 0
        from quorune.untap_step_coordination import coordinate_untap_step
        coordinate_untap_step(engine, phase="beginning", step="untap", active_player="A")
        self.assertEqual("untap.optional_source", engine.state.pending_decision.kind)
        engine.change_control(source.object_id, "C", reason="stale retained subject diagnostic")
        before = authoritative_state_hash(engine.state)
        selected = session.act("pilot:A", {"action_id": "choose", "refs": [source.ref]})
        self.assertFalse(selected.ok)
        self.assertIn("subjects changed", selected.summary)
        self.assertEqual(before, authoritative_state_hash(engine.state))
        self.assertTrue(engine.state.cards[source.object_id].tapped)
        self.assertEqual("untap", engine.state.step)

    def test_optional_choice_preserves_original_printed_held_and_new_untap_triggers_once(self):
        session = self.session(61120110)
        engine = session.engine
        source = self.card(session, "Rubinia Soulsinger", "A", tapped=True)
        watcher = self.card(session, "Wake Thrasher", "A")
        previously_untapped = self.card(session, "Llanowar Elves", "A", tapped=True)
        from quorune.tap_state import untap_permanent, tap_state_occurrence_context
        from quorune.trigger_processing import collect_trigger_items
        from quorune.untap_step_coordination import coordinate_untap_step
        # Seed a previously collected trigger through the real printed-card
        # discovery owner; the continuation is the action/replay boundary.
        self.assertTrue(untap_permanent(engine, previously_untapped, actor="A", reason="held occurrence fixture"))
        held = collect_trigger_items(engine, "permanent.untap",
            tap_state_occurrence_context(engine, previously_untapped, reason="held occurrence fixture"))
        self.assertEqual(1, len(held))
        engine.state.started = True
        engine.state.active_player = "A"
        engine.state.phase = "beginning"
        engine.state.step = "untap"
        engine.state.phase_index = 0
        engine.state.players["A"].turns_begun = 1
        coordinate_untap_step(engine, phase="beginning", step="untap", active_player="A", held_triggers=held)
        frame = OptionalUntapContinuation.from_dict(engine.state.pending_decision.continuation["optional_untap"])
        self.assertEqual(1, len(frame.waiting_triggers))
        self.assertFalse(engine.state.stack)
        self.assertIsNone(engine.state.priority_player)
        self.checkpoint(session)
        chosen = session.act("pilot:A", {"action_id": "choose", "refs": [source.ref]})
        self.assertTrue(chosen.ok, chosen.summary)
        self.assertEqual("upkeep", engine.state.step)
        self.assertEqual("trigger.order", engine.state.pending_decision.kind)
        options = engine.state.pending_decision.payload_by_actor["A"]["triggers"]
        self.assertEqual(2, len(options), "One held occurrence plus one newly untapped source")
        self.assertEqual(2, len({row["id"] for row in options}))
        self.assertIn(held[0].ref, {row["id"] for row in options})
        ordered = session.act("pilot:A", {"action_id": "order", "triggers": [row["id"] for row in options]})
        self.assertTrue(ordered.ok, ordered.summary)
        self.assertEqual(2, len(engine.state.stack))
        for _ in range(16):
            if not engine.state.stack:
                break
            pass_current(session)
        self.assertFalse(engine.state.stack)
        current = engine._effective_card_data(watcher)
        self.assertEqual("3", str(current["power"]))
        self.assertEqual("3", str(current["toughness"]))
        self.replay(session)

    def test_control_set_retains_membership_after_acquisition_and_excludes_reentry(self):
        session = self.session(61120111)
        engine = session.engine
        first = self.card(session, "Llanowar Elves", "B", tapped=True)
        sibling = self.card(session, "Llanowar Elves", "C", tapped=True)
        own = self.card(session, "Llanowar Elves", "A", tapped=True)
        from quorune.control_effects import execute_control_set
        intent = GainControlSetIntent(
            actor="A", controller="A", duration=Duration.UNTIL_END_OF_TURN,
            predicate=ObjectQuerySpec(zones=("battlefield",), types_all=("creature",), excluded_controllers=("A",)),
            source=ResolutionEffectSource(stack_ref="resolved-set-diagnostic"),
            steps=("gain_control", "untap", "haste"), reason="original-set diagnostic",
        )
        original_identity = first.logical_object_id
        refs = execute_control_set(engine, intent)
        self.assertEqual({first.ref, sibling.ref}, set(refs))
        for card in (first, sibling):
            self.assertEqual("A", card.controller)
            self.assertFalse(card.tapped, "Changing the query predicate cannot erase retained membership")
            self.assertIn("haste", engine._combat_keywords(card))
        self.assertTrue(own.tapped)
        self.assertNotIn("haste", engine._combat_keywords(own))
        engine.move_card(first.object_id, "graveyard", reason="original member leaves", log=False)
        engine.move_card(first.object_id, "battlefield", controller="B", reason="new incarnation", log=False)
        self.assertNotEqual(original_identity, first.logical_object_id)
        self.assertEqual("B", first.controller)
        self.assertFalse(has_control_origin(engine.state, first))
        self.assertNotIn("haste", engine._combat_keywords(first))
        self.assertEqual("A", sibling.controller)
        self.assertIn("haste", engine._combat_keywords(sibling))

    def test_cyclic_source_guards_end_and_never_restart_when_cleanup_restores_control(self):
        session = self.session(61120116)
        engine = session.engine
        first = self.card(session, "Rubinia Soulsinger", "A", tapped=True)
        second = self.card(session, "Dragonlord Silumgar", "A", tapped=True)
        target = self.card(session, "Llanowar Elves", "D")
        from quorune.control_effects import source_continuity_snapshot

        def guarded(source, subject):
            return gain_control_of_refs(engine, actor="A", object_refs=(subject.ref,), controller="A",
                duration=(Duration.UNTIL_SOURCE_CONTROL_CHANGES_OR_UNTAPS if source is first
                          else Duration.UNTIL_SOURCE_CONTROL_CHANGES),
                source=ResolutionEffectSource(stack_ref=f"resolved:{source.ref}", object_id=source.object_id,
                    logical_object_id=source.logical_object_id, card_ref=source.ref),
                reason="cyclic source guard diagnostic", history_snapshot=source_continuity_snapshot(source))

        guarded(first, target)
        guarded(first, second)
        guarded(second, first)
        self.assertEqual(3, sum(effect.duration.source_bound for effect in engine.state.continuous_effects))
        gain_control_of_refs(engine, actor="B", object_refs=(first.ref, second.ref), controller="B",
            duration=Duration.UNTIL_END_OF_TURN, source=ResolutionEffectSource(stack_ref="resolved-later-grant"),
            reason="later timestamp supersedes cyclic grants")
        self.assertEqual(["B", "B", "D"], [card.controller for card in (first, second, target)])
        self.assertFalse(any(effect.duration.source_bound for effect in engine.state.continuous_effects))
        expire_end_of_turn_continuous_effects(engine.state)
        synchronize_control_effects(engine, reason="cleanup source guard diagnostic")
        self.assertEqual(["A", "A", "D"], [card.controller for card in (first, second, target)])
        self.assertFalse(any(effect.duration.source_bound for effect in engine.state.continuous_effects))

    def test_manual_untap_mode_preserves_tapped_sources_without_optional_choice(self):
        session = self.session(61120117)
        engine = session.engine
        source = self.card(session, "Rubinia Soulsinger", "A", tapped=True)
        target = self.card(session, "Llanowar Elves", "B", tapped=True)
        from quorune.control_effects import source_continuity_snapshot
        gain_control_of_refs(engine, actor="A", object_refs=(target.ref,), controller="A",
            duration=Duration.UNTIL_SOURCE_CONTROL_CHANGES_OR_UNTAPS,
            source=ResolutionEffectSource(stack_ref="resolved-manual-mode-fixture", object_id=source.object_id,
                logical_object_id=source.logical_object_id, card_ref=source.ref),
            reason="manual untap preservation", history_snapshot=source_continuity_snapshot(source))
        history = source.source_continuity
        engine.state.config.auto_untap = False
        engine.state.started = True
        engine.state.active_player = "A"
        engine.state.phase = "beginning"
        engine.state.step = "untap"
        engine.state.phase_index = 0
        from quorune.untap_step_coordination import coordinate_untap_step
        coordinate_untap_step(engine, phase="beginning", step="untap", active_player="A")
        self.assertTrue(source.tapped)
        self.assertTrue(target.tapped)
        self.assertEqual(history, source.source_continuity)
        self.assertEqual("A", target.controller)
        self.assertTrue(engine.state.pending_decision is None
                        or engine.state.pending_decision.kind != "untap.optional_source")
        self.assertEqual("upkeep", engine.state.step)

    def test_copied_control_ability_uses_fresh_continuity_and_copying_principal(self):
        session = self.session(61120112)
        engine = session.engine
        source = self.card(session, "Rubinia Soulsinger", "A")
        original_target = self.card(session, "Llanowar Elves", "B")
        copy_target = self.card(session, "Llanowar Elves", "C")
        opponent_target = self.card(session, "Llanowar Elves", "D")
        engine.state.players["A"].turns_begun = 1
        self.priority(session)
        actions = session.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        activation = next(row["id"] for row in actions if row["id"].startswith(f"activate:{source.ref}:"))
        accepted = session.act("pilot:A", {"action_id": activation, "targets": [original_target.ref], "pay": "auto"})
        self.assertTrue(accepted.ok, accepted.summary)
        original = engine.state.stack[-1]
        from quorune.tap_state import set_permanent_tapped
        # This diagnostic seeds copies through the canonical copy owner.
        # It claims the retained copied-resolution path, not a copy producer.
        set_permanent_tapped(engine, source.ref, actor="A", tapped=False, reason="copy window fixture")
        set_permanent_tapped(engine, source.ref, actor="A", tapped=True, reason="new copy window fixture")
        own_copy = engine._copy_stack_item(controller="A", target=original,
            targets=[copy_target.ref], target_groups={}, reason="copy continuity diagnostic")
        opponent_copy = engine._copy_stack_item(controller="B", target=original,
            targets=[opponent_target.ref], target_groups={}, reason="copy principal diagnostic")
        self.assertNotEqual(original.context["control_duration_snapshot"], own_copy.context["control_duration_snapshot"])
        self.assertEqual(own_copy.context["control_duration_snapshot"], opponent_copy.context["control_duration_snapshot"])
        self.checkpoint(session)
        for _ in range(24):
            if not engine.state.stack:
                break
            pass_current(session)
        self.assertFalse(engine.state.stack)
        self.assertEqual("B", original_target.controller)
        self.assertEqual("A", copy_target.controller)
        self.assertEqual("D", opponent_target.controller, "The copying principal does not control the original source")
        self.replay(session)

    def test_historical_v1_keeps_ordinary_printed_activation_and_replay(self):
        session = self.session(61120113)
        engine = session.engine
        engine.state.control_history_version = 1
        source = self.card(session, "Royal Assassin", "A")
        target = self.card(session, "Llanowar Elves", "B", tapped=True)
        engine.state.players["A"].turns_begun = 1
        self.priority(session)
        actions = session.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        activation = next(row for row in actions if row["id"].startswith(f"activate:{source.ref}:"))
        self.assertIn(target.ref, activation["target_schema"]["legal_refs"])
        self.checkpoint(session)
        accepted = session.act("pilot:A", {"action_id": activation["id"], "targets": [target.ref], "pay": "auto"})
        self.assertTrue(accepted.ok, accepted.summary)
        for _ in range(12):
            if not engine.state.stack:
                break
            pass_current(session)
        self.assertEqual("graveyard", target.zone)
        self.assertEqual(1, engine.state.control_history_version)
        self.assertIsNone(source.source_continuity)
        self.replay(session)

    def test_printed_sower_source_dies_before_trigger_resolution_and_replays(self):
        session = self.session(61120114)
        engine = session.engine
        source = self.card(session, "Sower of Temptation", "A", "hand")
        target = self.card(session, "Llanowar Elves", "B")
        bolt = self.card(session, "Lightning Bolt", "B", "hand")
        engine.state.players["A"].mana_pool.update(C=2, U=2)
        engine.state.players["B"].mana_pool.update(R=1)
        self.priority(session)
        self.checkpoint(session)
        cast = session.act("pilot:A", {"action_id": f"cast:{source.ref}", "pay": "auto"})
        self.assertTrue(cast.ok, cast.summary)
        for _ in range(12):
            if engine.state.pending_decision.kind == "semantic.target":
                break
            pass_current(session)
        self.assertEqual("semantic.target", engine.state.pending_decision.kind)
        selected = session.act("pilot:A", {"action_id": "choose", "targets": [target.ref]})
        self.assertTrue(selected.ok, selected.summary)
        self.assertEqual("battlefield", source.zone)
        trigger = engine.state.stack[-1]
        self.assertIn("control_duration_snapshot", trigger.context)
        for _ in range(12):
            if engine.state.priority_player == "B":
                break
            pass_current(session)
        actions = session.packet("pilot:B", full=True)["decision"]["ctx"]["legal"]["actions"]
        action = next(row for row in actions if row["id"] == f"cast:{bolt.ref}")
        self.assertIn(source.ref, action["target_schema"]["legal_refs"])
        response = session.act("pilot:B", {"action_id": action["id"], "targets": [source.ref], "pay": "auto"})
        self.assertTrue(response.ok, response.summary)
        for _ in range(12):
            if len(engine.state.stack) == 1:
                break
            pass_current(session)
        self.assertEqual("graveyard", source.zone)
        self.assertEqual(trigger.ref, engine.state.stack[-1].ref)
        for _ in range(12):
            if not engine.state.stack:
                break
            pass_current(session)
        self.assertFalse(engine.state.stack)
        self.assertEqual("B", target.controller)
        self.assertFalse(any(effect.duration.source_bound for effect in engine.state.continuous_effects))
        self.replay(session)

    def test_actual_departures_preserve_initial_custody_and_exile_inactive_origin_with_replay(self):
        session = self.session(61120115)
        engine = session.engine
        inactive_origin = self.card(session, "Llanowar Elves", "B", "hand")
        engine.move_card(inactive_origin.object_id, "battlefield", controller="C", reason="initial custody fixture", log=False)
        ordinary_origin = self.card(session, "Llanowar Elves", "B")
        gain_control_of_refs(engine, actor="A", object_refs=(inactive_origin.ref, ordinary_origin.ref),
            controller="A", duration=Duration.UNTIL_END_OF_TURN,
            source=ResolutionEffectSource(stack_ref="resolved-prior-grant"), reason="prior resolved control fixture")
        engine.state.config.auto_pass_empty_priority = False
        self.priority(session)
        self.checkpoint(session)
        for _ in range(8):
            if engine.state.priority_player == "C":
                break
            pass_current(session)
        departed = session.act("pilot:C", {"action_id": "concede", "choices": {"confirm_concede": True}})
        self.assertTrue(departed.ok, departed.summary)
        self.assertFalse(engine.state.players["C"].in_game)
        self.assertEqual("A", inactive_origin.controller, "Initial custody is not an expiring grant to C")
        for _ in range(8):
            if engine.state.priority_player == "A":
                break
            pass_current(session)
        departed = session.act("pilot:A", {"action_id": "concede", "choices": {"confirm_concede": True}})
        self.assertTrue(departed.ok, departed.summary)
        self.assertFalse(engine.state.players["A"].in_game)
        self.assertEqual("exile", inactive_origin.zone)
        self.assertEqual("B", inactive_origin.owner)
        self.assertEqual("battlefield", ordinary_origin.zone)
        self.assertEqual("B", ordinary_origin.controller)
        self.replay(session)

    def test_printed_temporary_control_expires_at_actual_cleanup_to_initial_controller_and_replays(self):
        session = self.session(61120118)
        engine = session.engine
        spell = self.card(session, "Act of Treason", "A", "hand")
        target = self.card(session, "Llanowar Elves", "B", "hand")
        engine.move_card(target.object_id, "battlefield", controller="D", reason="initial custody fixture", log=False)
        engine.state.players["A"].mana_pool.update(C=2, R=1)
        engine.state.config.auto_pass_empty_priority = False
        self.priority(session)
        from quorune.turn_step_owner import TURN_STEPS
        engine.state.phase_index = TURN_STEPS.index(("precombat_main", "main"))
        self.checkpoint(session)
        cast = session.act("pilot:A", {"action_id": f"cast:{spell.ref}", "targets": [target.ref], "pay": "auto"})
        self.assertTrue(cast.ok, cast.summary)
        for _ in range(12):
            if not engine.state.stack:
                break
            pass_current(session)
        self.assertEqual("A", target.controller)
        self.assertIn("haste", engine._combat_keywords(target))
        before_turn = engine.state.turn_sequence
        for _ in range(64):
            if engine.state.turn_sequence != before_turn:
                break
            if engine.state.pending_decision.kind == "combat.attackers":
                accepted = session.act("pilot:A", {"a": "attack", "atk": {}})
                self.assertTrue(accepted.ok, accepted.summary)
            else:
                pass_current(session)
        self.assertNotEqual(before_turn, engine.state.turn_sequence)
        self.assertEqual("D", target.controller)
        self.assertEqual("B", target.owner)
        self.assertNotIn("haste", engine._combat_keywords(target))
        self.assertFalse(any(effect.duration is Duration.UNTIL_END_OF_TURN for effect in engine.state.continuous_effects))
        self.replay(session)

    def test_printed_control_of_face_down_creature_preserves_principal_private_projection_and_replays(self):
        session = self.session(61120121)
        engine = session.engine
        spell = self.card(session, "Act of Treason", "A", "hand")
        target = self.card(session, "Llanowar Elves", "B")
        # A previously created face-down permanent is a scene fixture. The
        # offered and accepted control spell is the gameplay boundary.
        target.face_down = True
        from quorune.morph import face_down_characteristics, MORPH_FACE_DOWN_ANNOTATION
        target.annotations[MORPH_FACE_DOWN_ANNOTATION] = face_down_characteristics("morph")
        target.known_to = ["B"]
        target.revealed_to = ["B"]
        engine.state.players["A"].mana_pool.update(C=2, R=1)
        self.priority(session)

        def public_object(seat):
            state = session.packet(f"pilot:{seat}", full=True)["state"]
            return next(row for player in state["players"].values() for row in player["bf"]
                        if row["id"] == target.ref)

        for seat in ("A", "C", "D"):
            row = public_object(seat)
            self.assertEqual("?", row["n"])
            self.assertNotIn("cid", row)
            self.assertEqual(1, row["fd"])
        actions = session.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        action = next(row for row in actions if row["id"] == f"cast:{spell.ref}")
        self.assertIn(target.ref, action["target_schema"]["legal_refs"])
        self.checkpoint(session)
        accepted = session.act("pilot:A", {"action_id": action["id"], "targets": [target.ref], "pay": "auto"})
        self.assertTrue(accepted.ok, accepted.summary)
        for _ in range(12):
            if not engine.state.stack:
                break
            pass_current(session)
        self.assertEqual("A", target.controller)
        self.assertEqual(target.printed_name, public_object("A")["n"])
        for seat in ("C", "D"):
            self.assertEqual("?", public_object(seat)["n"])
            self.assertNotIn("cid", public_object(seat))
        data = engine._effective_card_data(target)
        self.assertEqual("2", str(data["power"]))
        self.assertEqual("2", str(data["toughness"]))
        actions = session.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        self.assertFalse(any(row["id"].startswith(f"activate:{target.ref}:") for row in actions),
                         "Private knowledge does not expose the face-down object's printed mana ability")
        self.replay(session)

    def test_printed_brand_uses_owner_selection_across_other_controllers_and_replays(self):
        session = self.session(61120122)
        engine = session.engine
        spell = self.card(session, "Brand", "A", "hand")
        owned_creature = self.card(session, "Llanowar Elves", "A", "hand")
        owned_artifact = self.card(session, "Helm of Possession", "A", "hand")
        foreign = self.card(session, "Llanowar Elves", "B")
        engine.move_card(owned_creature.object_id, "battlefield", controller="B", reason="initial custody fixture", log=False)
        engine.move_card(owned_artifact.object_id, "battlefield", controller="D", reason="initial custody fixture", log=False)
        engine.state.players["A"].mana_pool.update(R=1)
        self.priority(session)
        actions = session.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        self.assertIn(f"cast:{spell.ref}", {row["id"] for row in actions})
        self.checkpoint(session)
        cast = session.act("pilot:A", {"action_id": f"cast:{spell.ref}", "pay": "auto"})
        self.assertTrue(cast.ok, cast.summary)
        for _ in range(12):
            if not engine.state.stack:
                break
            pass_current(session)
        self.assertEqual(["A", "A", "B"], [card.controller for card in (owned_creature, owned_artifact, foreign)])
        grants = [effect for effect in engine.state.continuous_effects if effect.effect_id.startswith("control-effect:")]
        self.assertEqual(1, len(grants))
        self.assertEqual({owned_creature.logical_object_id, owned_artifact.logical_object_id},
                         {identity.logical_object_id for identity in grants[0].locked_objects})
        self.assertEqual(Duration.ZONE_OBJECT, grants[0].duration)
        self.replay(session)

    def test_printed_helm_pins_duration_before_tap_and_sacrifice_costs_and_replays(self):
        session = self.session(61120123)
        engine = session.engine
        source = self.card(session, "Helm of Possession", "A")
        cost = self.card(session, "Llanowar Elves", "A")
        target = self.card(session, "Llanowar Elves", "B")
        engine.state.players["A"].mana_pool.update(C=2)
        self.priority(session)
        actions = session.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        action = next(row for row in actions if row["id"].startswith(f"activate:{source.ref}:"))
        self.assertIn(target.ref, action["target_schema"]["legal_refs"])
        self.checkpoint(session)
        accepted = session.act("pilot:A", {"action_id": action["id"], "targets": [target.ref],
            "cost_objects": [cost.ref], "pay": "auto"})
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertTrue(source.tapped)
        self.assertEqual("graveyard", cost.zone)
        self.assertIn("control_duration_snapshot", engine.state.stack[-1].context)
        for _ in range(12):
            if not engine.state.stack:
                break
            pass_current(session)
        self.assertFalse(engine.state.stack)
        self.assertEqual("A", target.controller)
        guards = [effect for effect in engine.state.continuous_effects if effect.duration.source_bound]
        self.assertEqual(1, len(guards))
        self.assertEqual(source.logical_object_id, guards[0].duration_source.logical_object_id)
        self.replay(session)

    def test_historical_v1_control_keeps_annotation_cleanup_and_rejects_new_journal(self):
        session = self.session(61120119)
        engine = session.engine
        engine.state.control_history_version = 1
        target = self.card(session, "Llanowar Elves", "B")
        engine.apply_effect({"op": "change_control_until_end_of_turn", "card": target.ref}, actor="A")
        self.assertEqual("A", target.controller)
        self.assertEqual("B", target.annotations["until_end_of_turn"]["control_previous"])
        self.assertFalse(has_control_origin(engine.state, target))
        before = authoritative_state_hash(engine.state)
        with self.assertRaisesRegex(ControlEffectError, "current retained custody history"):
            gain_control_of_refs(engine, actor="A", object_refs=(target.ref,), controller="A",
                duration=Duration.ZONE_OBJECT, source=ResolutionEffectSource(stack_ref="unsupported-v1-grant"),
                reason="historical control boundary diagnostic")
        self.assertEqual(before, authoritative_state_hash(engine.state))
        engine._finish_cleanup()
        self.assertEqual("B", target.controller)
        self.assertNotIn("until_end_of_turn", target.annotations)
        self.assertEqual(1, engine.state.control_history_version)
        self.assertFalse(has_control_origin(engine.state, target))

    def test_source_reentry_cannot_restore_a_grant_or_satisfy_the_old_pending_identity(self):
        session = self.session(61120120)
        engine = session.engine
        source = self.card(session, "Rubinia Soulsinger", "A", tapped=True)
        target = self.card(session, "Llanowar Elves", "B")
        from quorune.control_effects import source_continuity_snapshot
        original = ResolutionEffectSource(stack_ref="original-source-diagnostic", object_id=source.object_id,
            logical_object_id=source.logical_object_id, card_ref=source.ref)
        history = source_continuity_snapshot(source)
        gain_control_of_refs(engine, actor="A", object_refs=(target.ref,), controller="A",
            duration=Duration.UNTIL_SOURCE_CONTROL_CHANGES_OR_UNTAPS, source=original,
            reason="prior resolved source grant", history_snapshot=history)
        self.assertEqual("A", target.controller)
        engine.move_card(source.object_id, "graveyard", reason="source departure diagnostic", log=False)
        self.assertEqual("B", target.controller, "Expiration settles before the zone instruction returns")
        engine.move_card(source.object_id, "battlefield", controller="A", tapped=True,
            reason="source reentry diagnostic", log=False)
        self.assertNotEqual(original.logical_object_id, source.logical_object_id)
        self.assertIsNone(source.source_continuity)
        before = authoritative_state_hash(engine.state)
        result = gain_control_of_refs(engine, actor="A", object_refs=(target.ref,), controller="A",
            duration=Duration.UNTIL_SOURCE_CONTROL_CHANGES_OR_UNTAPS, source=original,
            reason="old pending identity diagnostic", history_snapshot=history,
            resolution_timestamp=engine.state.timestamp_sequence)
        self.assertEqual((), result)
        self.assertEqual(before, authoritative_state_hash(engine.state))
        self.assertEqual("B", target.controller)
        self.assertFalse(any(effect.duration.source_bound for effect in engine.state.continuous_effects))

    def test_printed_optional_untap_retains_controller_authority_empty_choice_and_replays(self):
        session = self.session(61120102)
        engine = session.engine
        source = self.card(session, "Rubinia Soulsinger", "A", tapped=True)
        self.card(session, "Royal Assassin", "A")
        self.card(session, "Royal Assassin", "B", tapped=True)
        engine.state.started = True
        engine.state.active_player = "A"
        engine.state.phase = "beginning"
        engine.state.step = "untap"
        engine.state.phase_index = 0
        engine.state.players["A"].turns_begun = 1
        from quorune.untap_step_coordination import coordinate_untap_step
        coordinate_untap_step(engine, phase="beginning", step="untap", active_player="A")
        self.assertEqual("untap.optional_source", engine.state.pending_decision.kind)
        self.assertEqual(["A"], engine.state.pending_decision.actors)
        self.assertIsNone(engine.state.priority_player)
        self.checkpoint(session)
        before = authoritative_state_hash(engine.state)
        rejected = session.act("pilot:B", {"action_id": "choose", "refs": [source.ref]})
        self.assertFalse(rejected.ok)
        self.assertEqual(before, authoritative_state_hash(engine.state))
        accepted = session.act("pilot:A", {"action_id": "choose", "refs": []})
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertTrue(source.tapped)
        self.assertEqual("upkeep", engine.state.step)
        self.replay(session)

    def test_printed_rubinia_activation_pins_source_duration_and_replays(self):
        session = self.session(61120103)
        engine = session.engine
        source = self.card(session, "Rubinia Soulsinger", "A")
        target = self.card(session, "Llanowar Elves", "B")
        engine.state.players["A"].turns_begun = 1
        self.priority(session)
        actions = session.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        candidates = [row for row in actions if row["id"].startswith(f"activate:{source.ref}:")]
        self.assertEqual(1, len(candidates), [row["id"] for row in actions])
        action = candidates[0]
        action_id = action["id"]
        self.assertIn(target.ref, action["target_schema"]["legal_refs"])
        self.checkpoint(session)
        accepted = session.act("pilot:A", {"action_id": action_id, "targets": [target.ref], "pay": "auto"})
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertTrue(source.tapped)
        self.assertIsNotNone(source.source_continuity)
        self.assertIn("control_duration_snapshot", engine.state.stack[-1].context)
        for _ in range(12):
            if not engine.state.stack:
                break
            pass_current(session)
        self.assertFalse(engine.state.stack)
        self.assertEqual("A", target.controller)
        self.assertEqual("B", target.owner)
        guards = [effect for effect in engine.state.continuous_effects if effect.duration.source_bound]
        self.assertEqual(1, len(guards))
        self.assertEqual(source.logical_object_id, guards[0].duration_source.logical_object_id)
        self.replay(session)

    def test_selected_optional_untap_ends_control_after_reload_and_replays(self):
        session = self.session(61120104)
        engine = session.engine
        source = self.card(session, "Rubinia Soulsinger", "A", tapped=True)
        target = self.card(session, "Llanowar Elves", "B", tapped=True)
        self.card(session, "Royal Assassin", "A")
        self.card(session, "Royal Assassin", "B", tapped=True)
        from quorune.control_effects import source_continuity_snapshot
        history = source_continuity_snapshot(source)
        gain_control_of_refs(
            engine, actor="A", object_refs=(target.ref,), controller="A",
            duration=Duration.UNTIL_SOURCE_CONTROL_CHANGES_OR_UNTAPS,
            source=ResolutionEffectSource(stack_ref="resolved-rubinia", object_id=source.object_id,
                logical_object_id=source.logical_object_id, card_ref=source.ref),
            reason="retained resolved duration fixture", history_snapshot=history,
        )
        engine.state.started = True
        engine.state.active_player = "A"
        engine.state.phase = "beginning"
        engine.state.step = "untap"
        engine.state.phase_index = 0
        engine.state.players["A"].turns_begun = 1
        from quorune.untap_step_coordination import coordinate_untap_step
        coordinate_untap_step(engine, phase="beginning", step="untap", active_player="A")
        self.assertEqual("untap.optional_source", engine.state.pending_decision.kind)
        for seat in ("B", "C", "D"):
            self.assertIsNone(session.packet(f"pilot:{seat}", full=True)["decision"])
        before = authoritative_state_hash(engine.state)
        duplicate = session.act("pilot:A", {"action_id": "choose", "refs": [source.ref, source.ref]})
        self.assertFalse(duplicate.ok)
        self.assertEqual(before, authoritative_state_hash(engine.state))
        with tempfile.TemporaryDirectory() as directory:
            session.save(directory)
            session = CommanderSession.load(self.db, directory)
        engine = session.engine
        source = engine.state.cards[source.object_id]
        target = engine.state.cards[target.object_id]
        self.assertEqual(before, authoritative_state_hash(engine.state))
        self.checkpoint(session)
        chosen = session.act("pilot:A", {"action_id": "choose", "refs": [source.ref]})
        self.assertTrue(chosen.ok, chosen.summary)
        self.assertFalse(source.tapped)
        self.assertFalse(target.tapped, "CR 502.3 untaps the original set simultaneously")
        self.assertEqual("B", target.controller)
        self.assertFalse(any(effect.duration.source_bound for effect in engine.state.continuous_effects))
        self.assertEqual("upkeep", engine.state.step)
        self.replay(session)

    def test_source_loss_and_regain_before_resolution_never_restarts_retained_duration(self):
        session = self.session(61120105)
        engine = session.engine
        source = self.card(session, "Rubinia Soulsinger", "A", tapped=True)
        target = self.card(session, "Llanowar Elves", "B")
        from quorune.control_effects import source_continuity_snapshot
        history = source_continuity_snapshot(source)
        engine.change_control(source.object_id, "C", reason="source loss before resolution")
        engine.change_control(source.object_id, "A", reason="source regained before resolution")
        resolution_timestamp = engine._next_zone_timestamp()
        before = authoritative_state_hash(engine.state)
        result = gain_control_of_refs(
            engine, actor="A", object_refs=(target.ref,), controller="A",
            duration=Duration.UNTIL_SOURCE_CONTROL_CHANGES_OR_UNTAPS,
            source=ResolutionEffectSource(stack_ref="pending-rubinia", object_id=source.object_id,
                logical_object_id=source.logical_object_id, card_ref=source.ref),
            reason="expired original duration", history_snapshot=history,
            resolution_timestamp=resolution_timestamp,
        )
        self.assertEqual((), result)
        self.assertEqual("B", target.controller)
        self.assertEqual(before, authoritative_state_hash(engine.state))

    def test_simultaneous_source_departures_settle_prerequisite_control_before_dependent_duration(self):
        session = self.session(61120109)
        engine = session.engine
        first = self.card(session, "Rubinia Soulsinger", "A", tapped=True)
        first_helm = self.card(session, "Helm of Possession", "A", tapped=True)
        second = self.card(session, "Rubinia Soulsinger", "B", tapped=True)
        second_helm = self.card(session, "Helm of Possession", "C", tapped=True)
        target = self.card(session, "Llanowar Elves", "D")
        from quorune.control_effects import source_continuity_snapshot

        def gained_by(source, card):
            gain_control_of_refs(
                engine, actor=source.controller, object_refs=(card.ref,), controller=source.controller,
                duration=Duration.UNTIL_SOURCE_CONTROL_CHANGES_OR_UNTAPS,
                source=ResolutionEffectSource(stack_ref=f"resolved:{source.ref}", object_id=source.object_id,
                    logical_object_id=source.logical_object_id, card_ref=source.ref),
                reason="source dependency diagnostic", history_snapshot=source_continuity_snapshot(source),
            )

        gained_by(second_helm, second)
        gained_by(second, first)
        gained_by(first_helm, first)
        gained_by(first, target)
        self.assertEqual("A", first.controller)
        self.assertEqual("C", second.controller)
        self.assertEqual("A", target.controller)
        continuity = first.source_continuity
        engine._move_cards_simultaneously(
            [(first_helm.object_id, "graveyard"), (second_helm.object_id, "graveyard")],
            reason="simultaneous source departure diagnostic",
        )
        self.assertEqual("A", first.controller)
        self.assertEqual("B", second.controller)
        self.assertEqual(continuity, first.source_continuity,
                         "Settling prerequisites must not invent an intermediate acquisition")
        self.assertEqual("A", target.controller,
                         "The original source stayed continuously controlled and tapped")
        guards = [effect for effect in engine.state.continuous_effects if effect.duration.source_bound]
        self.assertEqual(1, len(guards))
        self.assertEqual(first.logical_object_id, guards[0].duration_source.logical_object_id)

    def test_printed_rubinia_untapped_and_retapped_before_resolution_only_new_activation_applies(self):
        session = self.session(61120108)
        engine = session.engine
        source = self.card(session, "Rubinia Soulsinger", "A")
        original_target = self.card(session, "Llanowar Elves", "B")
        later_target = self.card(session, "Llanowar Elves", "C")
        refocus = self.card(session, "Refocus", "B", "hand")
        engine.state.players["A"].turns_begun = 1
        engine.state.players["B"].mana_pool.update(C=1, U=1)
        self.priority(session)
        actions = session.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        activation = next(row["id"] for row in actions if row["id"].startswith(f"activate:{source.ref}:"))
        self.checkpoint(session)
        accepted = session.act("pilot:A", {"action_id": activation, "targets": [original_target.ref], "pay": "auto"})
        self.assertTrue(accepted.ok, accepted.summary)
        original_stack_ref = engine.state.stack[-1].ref
        self.assertTrue(source.tapped)
        for _ in range(12):
            if engine.state.priority_player == "B":
                break
            pass_current(session)
        actions = session.packet("pilot:B", full=True)["decision"]["ctx"]["legal"]["actions"]
        self.assertIn(f"cast:{refocus.ref}", {row["id"] for row in actions})
        cast = session.act("pilot:B", {"action_id": f"cast:{refocus.ref}", "targets": [source.ref], "pay": "auto"})
        self.assertTrue(cast.ok, cast.summary)
        for _ in range(12):
            if len(engine.state.stack) == 1:
                break
            pass_current(session)
        self.assertEqual(original_stack_ref, engine.state.stack[-1].ref)
        self.assertFalse(source.tapped)
        for _ in range(12):
            if engine.state.priority_player == "A":
                break
            pass_current(session)
        actions = session.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        self.assertIn(activation, {row["id"] for row in actions})
        retap = session.act("pilot:A", {"action_id": activation, "targets": [later_target.ref], "pay": "auto"})
        self.assertTrue(retap.ok, retap.summary)
        self.assertTrue(source.tapped)
        for _ in range(16):
            if not engine.state.stack:
                break
            pass_current(session)
        self.assertFalse(engine.state.stack)
        self.assertEqual("B", original_target.controller, "An ended original duration cannot restart")
        self.assertEqual("A", later_target.controller, "A newly activated ability retains its own continuity")
        guards = [effect for effect in engine.state.continuous_effects if effect.duration.source_bound]
        self.assertEqual(1, len(guards))
        self.assertEqual(later_target.logical_object_id, guards[0].locked_objects[0].logical_object_id)
        self.replay(session)
