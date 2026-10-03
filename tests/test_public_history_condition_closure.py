from __future__ import annotations

"""CR 603.4/608.2i: sealed look-back facts, not current object counts."""

import unittest
import json
from types import SimpleNamespace
from copy import deepcopy
from pathlib import Path
import tempfile

from quorune.carddb import CardRecord
from quorune.compiler.public_state_queries import fixed_public_state_condition
from quorune.oracle_ir import compile_oracle_card, register_generated_programs
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.model import CardInstance, CombatState, GameConfig
from quorune.session import CommanderSession
from quorune.record import authoritative_state_hash, checkpoint_envelope, replay_record
from common import ROOT, keep_all, set_fixture_turn
from scripts.build_test_database import build_fixture_database
from quorune.rules.capabilities import load_default_capability_registry
from quorune.card_programs.runtime import _FixedPublicStateSnapshotResolver
from quorune.model import TurnHistory, TurnHistoryEvent
from quorune.zone_trigger_processing import record_zone_change_history
from quorune.zone_trigger_events import ZoneChangeOccurrence
from quorune.record_state_provenance import format_state_versions, validate_state_versions
from quorune.turn_history import roll_turn_history


def record(text: str) -> CardRecord:
    return CardRecord(
        oracle_id="fixture:public-history-compiler",
        name="Generic Public History Fixture", mana_cost="{1}{G}",
        mana_value=2, type_line="Creature — Test", oracle_text=text,
        power="2", toughness="3", loyalty=None, defense=None,
        colors=("G",), color_identity=("G",), keywords=(), produced_mana=(),
        layout="normal", released_at="2026-01-01",
        legalities={"commander": "legal"}, faces=(), raw={},
    )


class PublicHistoryConditionCompilerTests(unittest.TestCase):
    def test_shared_history_conditions_compile_with_exact_thresholds(self):
        expected = (
            ("a creature died this turn", "any_creature_died_this_turn", 1),
            ("you attacked this turn", "controller_attacked_this_turn", 1),
            ("a permanent left the battlefield under your control this turn", "controller_permanent_left_this_turn", 1),
            ("a permanent you controlled left the battlefield this turn", "controller_permanent_left_this_turn", 1),
            ("an opponent lost life this turn", "opponent_lost_life_this_turn", 1),
            ("you gained 3 or more life this turn", "controller_life_gained_this_turn", 3),
            ("you lost four or more life this turn", "controller_life_lost_this_turn", 4),
        )
        registry = load_default_capability_registry()
        for condition, fact, amount in expected:
            with self.subTest(condition=condition):
                parsed = fixed_public_state_condition(condition, source_name="Generic Public History Fixture")
                self.assertIsNotNone(parsed)
                self.assertEqual(fact, parsed.fact.value)
                self.assertEqual(amount, parsed.amount)
                ir = compile_oracle_card(
                    record(f"When this creature enters, if {condition}, draw a card."),
                    capability_registry=registry, capability_profile="commander_review",
                )
                self.assertEqual("exact", ir.status)
                node = ir.faces[0].nodes[0]
                self.assertIn("trigger.condition.fixed_public_state", node.capability_dependencies)
                self.assertEqual("fixed_public_state_condition", node.event_condition["field"])
                self.assertEqual(parsed.to_dict(), node.event_condition["condition"])

    def test_unrepresented_history_conditions_remain_residual(self):
        for condition in (
            "you descended this turn", "it was kicked", "you attacked twice this turn",
            "no creature died this turn", "an opponent lost X life this turn",
            "a permanent left the battlefield this turn or a spell was warped this turn",
        ):
            with self.subTest(condition=condition):
                self.assertIsNone(fixed_public_state_condition(condition, source_name="Generic Public History Fixture"))

    def test_new_history_model_rejects_legacy_schema_and_malformed_amounts(self):
        from quorune.continuous_conditions import FixedPublicStateConditionSpec
        value=fixed_public_state_condition("you attacked this turn",source_name="Generic").to_dict()
        for changed in ({**value,"schema_version":3},{**value,"amount":True},
                        {**value,"amount":0},{**value,"unknown":True}):
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                FixedPublicStateConditionSpec.from_dict(changed)

    def test_history_condition_parser_mutant_is_killed(self):
        from unittest.mock import patch
        with patch("quorune.compiler.public_state_fact_queries._fixed_history_fact",return_value=None):
            with self.assertRaises(AssertionError):
                self.assertIsNotNone(fixed_public_state_condition("you attacked this turn",source_name="Generic"))


class PublicHistoryConditionRuntimeContractTests(unittest.TestCase):
    def test_history_facts_use_sealed_actor_and_target_not_current_objects(self):
        history = TurnHistory(turn_sequence=4, departure_history_version=1, events=[
            TurnHistoryEvent("creature_died", actor="B", object_incarnation="gone@1"),
            TurnHistoryEvent("creature_attacked", actor="A", object_incarnation="stolen@1"),
            TurnHistoryEvent("permanent_left", actor="A", object_incarnation="returned@2", types=("land",)),
            TurnHistoryEvent("player_gained_life", target="A", amount=2),
            TurnHistoryEvent("player_gained_life", target="A", amount=1),
            TurnHistoryEvent("player_lost_life", target="B", amount=1),
        ])
        state = SimpleNamespace(turn_history=history, turn_sequence=4,
                                players={seat:SimpleNamespace(in_game=True) for seat in "ABCD"})
        resolver = _FixedPublicStateSnapshotResolver(state, None, None)
        source = SimpleNamespace(controller="A")
        for text, expected in (("a creature died this turn", 1), ("you attacked this turn", 1),
                               ("a permanent you controlled left the battlefield this turn", 1),
                               ("an opponent lost life this turn", 1), ("you gained 3 or more life this turn", 3)):
            with self.subTest(text=text):
                condition = fixed_public_state_condition(text, source_name="Generic")
                self.assertEqual(expected, resolver._history_fact_quantity(source, condition))
        source.controller="C"
        self.assertEqual(0, resolver._history_fact_quantity(source, fixed_public_state_condition("you attacked this turn", source_name="Generic")))
        history.turn_sequence=3
        self.assertIsNone(resolver._history_fact_quantity(source, fixed_public_state_condition("a creature died this turn", source_name="Generic")))

    def test_departure_marker_roundtrip_rollover_and_manifest_binding(self):
        history = TurnHistory(turn_sequence=4, departure_history_version=1)
        self.assertEqual(history.to_dict(), TurnHistory.from_dict(history.to_dict()).to_dict())
        advanced = roll_turn_history(history, next_turn_sequence=5, previous_active_player="A")
        self.assertEqual(1, advanced.departure_history_version)
        old = TurnHistory(turn_sequence=4)
        self.assertNotIn("departure_history_version", old.to_dict())
        for invalid in (True, 0, 2, "1"):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                TurnHistory(departure_history_version=invalid)
        state = SimpleNamespace(turn_history=history, commander_damage_identity_version=2, control_history_version=1)
        versions = format_state_versions(state)
        self.assertEqual(1, versions["departure_history_version"])
        validate_state_versions({"format":versions},state)
        bad=dict(versions);bad["departure_history_version"]=0
        with self.assertRaisesRegex(ValueError,"Departure-history provenance"):
            validate_state_versions({"format":bad},state)
        state.turn_history=old
        legacy=format_state_versions(state)
        self.assertEqual(0,legacy["departure_history_version"])
        legacy.pop("departure_history_version")
        validate_state_versions({"format":legacy},state)

    def test_fixture_turn_reset_preserves_explicit_history_mode(self):
        engine=SimpleNamespace(state=SimpleNamespace(
            turn_sequence=4,turn_history=TurnHistory(turn_sequence=4,departure_history_version=1)))
        set_fixture_turn(engine,5)
        self.assertEqual(1,engine.state.turn_history.departure_history_version)
    def test_absent_history_is_unavailable_not_known_empty(self):
        state = SimpleNamespace(turn_history=None, turn_sequence=4)
        query = _FixedPublicStateSnapshotResolver(state, None, None)
        source = SimpleNamespace(controller="A")
        condition = fixed_public_state_condition("you gained life this turn", source_name="Generic")
        self.assertIsNone(query._history_fact_quantity(source, condition))
        legacy=TurnHistory(turn_sequence=4)
        state.turn_history=legacy
        departure=fixed_public_state_condition("a permanent you controlled left the battlefield this turn",source_name="Generic")
        self.assertIsNone(query._history_fact_quantity(source,departure))
        legacy.departure_history_version=1
        self.assertEqual(0,query._history_fact_quantity(source,departure))

    def test_versioned_departures_use_previous_controller_and_preserve_legacy(self):
        changes = []
        state = SimpleNamespace(turn_history=TurnHistory(turn_sequence=4))
        self.assertTrue(hasattr(state.turn_history, "departure_history_version"))
        state.turn_history.departure_history_version = 1
        host = SimpleNamespace(state=state, _record_turn_history=lambda kind, **kw: changes.append((kind, kw)))
        event = ZoneChangeOccurrence(
            object_id="generic", card_ref="old-ref", owner="A", origin="battlefield",
            destination="hand", previous_controller="B", current_controller="A",
            previous_logical_object_id="generic@2", current_logical_object_id="generic@3",
            zone_change_counter=3, token=False, card_object=True,
            previous_characteristics={"type_line":"Artifact Creature — Test"},
            current_characteristics={"type_line":"Artifact Creature — Test"},
            previous_attachments=(), cause="generic return",
        )
        record_zone_change_history(host, event)
        self.assertEqual(1, len(changes))
        self.assertEqual("permanent_left", changes[0][0])
        self.assertEqual("B", changes[0][1]["actor"])
        self.assertEqual("generic@2", changes[0][1]["object_incarnation"])
        changes.clear()
        state.turn_history.departure_history_version = None
        record_zone_change_history(host, event)
        self.assertEqual([], changes)


class PublicHistoryTrustedActionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory()
        path=Path(cls.temporary.name)/"history.sqlite3"
        build_fixture_database([ROOT/"tests/fixtures/public-history-condition-cards.json"],path)
        cls.db=CardDatabase(path)
        cls.deck=DeckDefinition("Generic history review",[
            DeckEntry("Generic History Commander",1,"commander"),
            DeckEntry("Generic History Forest",15)], ["Generic History Commander"])

    @classmethod
    def tearDownClass(cls):
        cls.db.close();cls.temporary.cleanup()

    def session(self,seed):
        session=CommanderSession.create(self.db,{seat:deepcopy(self.deck) for seat in "ABCD"},
            first_player="A",seed=seed,config=GameConfig(seed=seed,auto_pass_empty_priority=False))
        keep_all(session)
        engine=session.engine
        engine.permissions.invalidate_current();session.state.pending_decision=None
        session.state.priority_player=None;session.state.priority_passes=[]
        register_generated_programs(self.db,engine.semantics,tuple(self.db.iter_cards()),
            trust_level="trusted",capability_registry=load_default_capability_registry(),
            capability_profile="commander_review",promote_exact_runtime_handlers=True,
            promote_exact_trigger_programs=True,promote_exact_effect_programs=True,
            promote_exact_capability_declarations=True)
        session.state.active_player="A";session.state.started=True
        session.state.phase="precombat_main";session.state.step="main"
        session.state.players["A"].mana_pool["G"]=5
        return session

    def add(self,session,name,ref,*,owner="A",controller=None,zone="hand",trusted=True):
        row=self.db.lookup(name);controller=controller or owner
        card=CardInstance(object_id="history:"+ref,ref=ref,oracle_id=row.oracle_id,
            printed_name=row.name,owner=owner,controller=controller,zone=zone,
            zone_timestamp=session.engine._next_zone_timestamp(),
            known_to=list("ABCD") if zone=="battlefield" else [owner])
        session.state.cards[card.object_id]=card
        container=controller if zone=="battlefield" else owner
        session.state.players[container].zones[zone].append(card.object_id)
        programs=session.engine.semantics.programs_for_oracle(card.oracle_id)
        self.assertTrue(programs)
        if trusted:
            self.assertTrue(all(session.engine.semantic_program_is_current_trusted(p) for p in programs))
        return card

    def seal(self,session):
        session.engine._grant_priority("A");session.engine.pump()
        session.initial_checkpoint=checkpoint_envelope(session.state)
        session.commands.clear();session.decisions.clear()

    def cast(self,session,card,targets=()):
        session.engine.pump();decision=session.packet("pilot:A",full=True)["decision"]
        action=next(a for a in decision["ctx"]["legal"]["actions"] if a["id"]=="cast:"+card.ref)
        if targets:
            self.assertTrue(set(targets)<=set(action["target_schema"]["legal_refs"]))
        result=session.act("pilot:A",{"action_id":action["id"],"targets":list(targets),"pay":"auto"})
        self.assertTrue(result.ok,result.summary)

    def finish(self,session):
        for _ in range(48):
            if not session.state.stack and not session.state.pending_trigger_batches:
                return
            session.engine.pump();principal=session.pending_principals()[0]
            decision=session.packet(principal,full=True)["decision"]
            self.assertEqual("priority",decision["kind"])
            result=session.act(principal,{"action_id":"pass"});self.assertTrue(result.ok,result.summary)
        self.fail("Generic history resolution did not finish")

    def replay(self,session):
        expected=authoritative_state_hash(session.state)
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/"record";session.save(path)
            loaded=CommanderSession.load(self.db,path)
            self.assertEqual(expected,authoritative_state_hash(loaded.state))
            restored=replay_record(path,self.db,verify=True)
            self.assertTrue(restored["ok"],restored)
            self.assertEqual(expected,restored["final_state_hash"])

    def test_trusted_death_and_life_history_use_actual_spells(self):
        session=self.session(24300002)
        victim=self.add(session,"Generic History Victim","VICTIM",owner="B",zone="battlefield")
        death=self.add(session,"Generic History Destroy","DESTROY")
        death_witness=self.add(session,"Generic Death Entry Witness","DEATH")
        gain=self.add(session,"Generic History Life Gain","GAIN")
        life_witness=self.add(session,"Generic Life Entry Witness","LIFE")
        self.seal(session)
        for card,targets in ((death,(victim.ref,)),(death_witness,()),(gain,()),(life_witness,())):
            self.cast(session,card,targets);self.finish(session)
        events=session.state.turn_history.events
        self.assertEqual(1,len([e for e in events if e.kind=="creature_died" and e.actor=="B"]))
        self.assertEqual(3,sum(e.amount for e in events if e.kind=="player_gained_life" and e.target=="A"))
        self.assertEqual(2,len([e for e in session.state.events if e.code=="card.draw" and e.actor=="A" and "Witness" in str(e.details.get("reason",""))]))
        self.replay(session)

    def test_no_qualifying_entry_history_draws_nothing(self):
        session=self.session(24300003)
        witness=self.add(session,"Generic Departure Entry Witness","NO-DEPARTURE")
        self.seal(session)
        prior=len([e for e in session.state.events if e.code=="card.draw" and e.actor=="A"])
        self.cast(session,witness);self.finish(session)
        self.assertEqual(prior,len([e for e in session.state.events if e.code=="card.draw" and e.actor=="A"]))
        self.assertFalse([e for e in session.state.turn_history.events if e.kind=="permanent_left"])
        self.replay(session)

    def test_actual_attack_history_survives_combat_then_entry(self):
        session=self.session(24300004)
        attacker=self.add(session,"Generic History Victim","ATTACKER",zone="battlefield")
        self.add(session,"Generic History Forest","ATTACK-MANA",zone="battlefield")
        witness=self.add(session,"Generic Attack Entry Witness","RAID")
        session.state.phase_index=5;session.state.phase="combat";session.state.step="declare_attackers"
        session.state.combat=CombatState()
        session.engine._issue_attackers()
        session.initial_checkpoint=checkpoint_envelope(session.state)
        session.commands.clear();session.decisions.clear()
        result=session.act("pilot:A",{"a":"attack","atk":{attacker.ref:"B"}})
        self.assertTrue(result.ok,result.summary)
        for _ in range(80):
            session.engine.pump()
            if session.state.phase=="postcombat_main" and session.state.priority_player=="A":break
            principal=session.pending_principals()[0]
            decision=session.packet(principal,full=True)["decision"]
            response={"action_id":"pass"} if decision["kind"]=="priority" else {"a":"block","blk":{}}
            result=session.act(principal,response);self.assertTrue(result.ok,result.summary)
        else:self.fail("Combat did not reach postcombat main")
        self.assertEqual(1,len([e for e in session.state.turn_history.events if e.kind=="creature_attacked" and e.actor=="A"]))
        prior=len([e for e in session.state.events if e.code=="card.draw" and e.actor=="A"])
        self.cast(session,witness);self.finish(session)
        self.assertEqual(prior+1,len([e for e in session.state.events if e.code=="card.draw" and e.actor=="A"]))
        self.replay(session)

    def test_simultaneous_return_records_full_group_and_real_entry_trigger(self):
        session=self.session(24300005)
        first=self.add(session,"Generic History Victim","FIRST",zone="battlefield")
        second=self.add(session,"Generic History Victim","SECOND",owner="B",controller="A",zone="battlefield")
        removal=self.add(session,"Generic History Group Return","GROUP")
        witness=self.add(session,"Generic Departure Entry Witness","ENTRY")
        before_ids={first.logical_object_id,second.logical_object_id}
        self.seal(session)
        self.cast(session,removal);self.finish(session)
        rows=[e for e in session.state.turn_history.events if e.kind=="permanent_left"]
        self.assertEqual(before_ids,{e.object_incarnation for e in rows})
        self.assertTrue(all(e.actor=="A" for e in rows))
        prior=len([e for e in session.state.events if e.code=="card.draw" and e.actor=="A"])
        self.cast(session,witness);self.finish(session)
        self.assertEqual(prior+1,len([e for e in session.state.events if e.code=="card.draw" and e.actor=="A"]))
        self.replay(session)

    def test_trusted_return_then_entry_draw_uses_previous_controller_and_exact_replay(self):
        session=self.session(24300001)
        victim=self.add(session,"Generic History Victim","VICTIM",owner="B",controller="A",zone="battlefield")
        removal=self.add(session,"Generic History Return","RETURN")
        witness=self.add(session,"Generic Departure Entry Witness","WITNESS")
        self.seal(session)
        before_invalid=authoritative_state_hash(session.state)
        rejected=session.act("pilot:A",{"action_id":"cast:"+removal.ref,"targets":["UNKNOWN"],"pay":"auto"})
        self.assertFalse(rejected.ok)
        self.assertEqual(before_invalid,authoritative_state_hash(session.state))
        self.cast(session,removal,(victim.ref,));self.finish(session)
        self.assertEqual("hand",session.state.cards[victim.object_id].zone)
        departures=[e for e in session.state.turn_history.events if e.kind=="permanent_left"]
        self.assertEqual(1,len(departures));self.assertEqual("A",departures[0].actor)
        before=len(session.state.players["A"].zones["hand"])
        prior_draws=len([e for e in session.state.events if e.code=="card.draw" and e.actor=="A"])
        self.cast(session,witness);self.finish(session)
        self.assertEqual(before,len(session.state.players["A"].zones["hand"]))
        self.assertEqual(prior_draws+1,len([e for e in session.state.events if e.code=="card.draw" and e.actor=="A"]))
        self.replay(session)


    def test_actual_opponent_loss_draw_is_private_and_own_loss_does_not_qualify(self):
        for payer,qualifies in (("B",True),("A",False)):
            with self.subTest(payer=payer):
                session=self.session(24300006+(payer=="A"))
                loss=self.add(session,"Generic History Life Loss","LOSS")
                witness=self.add(session,"Generic Opponent Loss Witness","OPPONENT")
                self.seal(session)
                self.cast(session,loss,(payer,));self.finish(session)
                prior=len([e for e in session.state.events if e.code=="card.draw" and e.actor=="A"])
                self.cast(session,witness);self.finish(session)
                self.assertEqual(prior+int(qualifies),len([e for e in session.state.events if e.code=="card.draw" and e.actor=="A"]))
                for seat in "BCD":
                    packet=session.packet("pilot:"+seat,full=True)
                    public=json.dumps(packet)
                    for object_id in session.state.players["A"].zones["hand"]:
                        self.assertNotIn(session.state.cards[object_id].ref,public)
                self.replay(session)

    def test_real_entry_trigger_keeps_locked_controller_after_control_change(self):
        session=self.session(24300009)
        loss=self.add(session,"Generic History Life Loss","LOCK-LOSS")
        witness=self.add(session,"Generic Opponent Loss Witness","LOCK-WITNESS")
        self.seal(session)
        self.cast(session,loss,("B",));self.finish(session)
        self.cast(session,witness)
        for _ in range(16):
            session.engine.pump()
            triggers=[item for item in session.state.stack if item.kind=="triggered_ability" and item.source_object_id==witness.object_id]
            if triggers:break
            principal=session.pending_principals()[0]
            result=session.act(principal,{"action_id":"pass"});self.assertTrue(result.ok,result.summary)
        else:self.fail("Actual entry did not create its history trigger")
        self.assertEqual("A",triggers[0].controller)
        session.engine.change_control(witness.object_id,"B",reason="generic controller-lock owner diagnostic")
        # This is a current-controller owner diagnostic over a genuinely cast
        # and triggered card, not a new control-changing Oracle production.
        session.initial_checkpoint=checkpoint_envelope(session.state)
        session.commands.clear();session.decisions.clear()
        before_a=len([e for e in session.state.events if e.code=="card.draw" and e.actor=="A"])
        before_b=len([e for e in session.state.events if e.code=="card.draw" and e.actor=="B"])
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/"pending";session.save(path)
            loaded=CommanderSession.load(self.db,path)
            self.assertEqual(authoritative_state_hash(session.state),authoritative_state_hash(loaded.state))
        self.finish(session)
        self.assertEqual(before_a+1,len([e for e in session.state.events if e.code=="card.draw" and e.actor=="A"]))
        self.assertEqual(before_b,len([e for e in session.state.events if e.code=="card.draw" and e.actor=="B"]))
        self.replay(session)

    def test_exact_history_counter_trigger_cannot_admit_unrepresented_entry_replacement(self):
        from quorune.card_programs import bind_card_program_runtime
        from quorune.card_programs.adapters import compile_best_available_card_program
        session=self.session(24300010)
        session.state.config.semantic_policy="trusted_only"
        card=self.add(session,"Generic History Unrepresented Entry Replacement","UNREPRESENTED",trusted=False)
        row=self.db.by_oracle_id(card.oracle_id)
        ir=compile_oracle_card(row,capability_registry=load_default_capability_registry(),capability_profile="commander_review")
        self.assertTrue(any(n.exact and "counter.producer.fixed_permanent_set_effect" in n.capability_dependencies
                            for f in ir.faces for n in f.nodes))
        self.assertTrue(ir.material_residuals)
        program=compile_best_available_card_program(self.db,row,semantic_registry=session.engine.semantics,
            capability_registry=load_default_capability_registry(),capability_profile="commander_review")
        binding=bind_card_program_runtime(program,capability_registry=load_default_capability_registry(),profile="commander_review")
        self.assertFalse(binding["strict_capability_ready"])
        self.assertFalse(binding["compatible_ready"])
        before=authoritative_state_hash(session.state)
        # Whole-card admission is not ordinary permanent cast legality. The
        # latter remains core rules even when an independent ability is
        # unsupported; never assert a nonexistent legality restriction.
        self.assertEqual("unresolved",binding["trust_basis"])
        self.assertTrue(binding["blockers"])
        self.assertEqual(before,authoritative_state_hash(session.state))
        self.assertEqual("hand",session.state.cards[card.object_id].zone)


if __name__ == "__main__":
    unittest.main()
