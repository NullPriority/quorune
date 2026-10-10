from __future__ import annotations

from dataclasses import replace
import json
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from common import ROOT
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.model import CombatState
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
import test_bound_effect_programs as witnesses
from quorune.as_unblocked import AsUnblockedAssignmentPermission
from quorune.ability_fragments import ability_fragment_from_dict,ability_fragment_to_dict
from quorune.combat_damage_assignment import CombatDamageAssignmentProposal,CombatDamageSourceSpec
from quorune.combat_damage_values import AsUnblockedDamageSpec,TrampleDamageSpec,CreatureDamageState,CombatDamageAssignmentError
from quorune.compiler.as_unblocked_templates import static_as_unblocked_handler
from quorune.semantic_runtime.conditional_continuous import ConditionalAsUnblockedAssignmentHandler


class AsUnblockedAssignmentModelTests(unittest.TestCase):
    def proposal(self,*,trample=False,blockers=True):
        return CombatDamageAssignmentProposal('step','A',
            (CombatDamageSourceSpec('attacker','A','attacker:0',5,('blocker','B') if blockers else ('B',)),),
            frozenset({'attacker'}),frozenset(),
            (TrampleDamageSpec('attacker','B',(('blocker',CreatureDamageState(3,0)),) if blockers else ()),) if trample else (),
            (AsUnblockedDamageSpec('attacker','B'),))

    def test_all_or_nothing_choice_preserves_ordinary_and_trample_routes(self):
        proposal=self.proposal()
        self.assertIsNone(proposal.automatic_assignments())
        self.assertEqual('B',proposal.projected_options()['attacker']['as_unblocked_recipient'])
        for target in ('blocker','B'):
            self.assertEqual(target,proposal.validate([{'source':'attacker','target':target,'amount':5}])[0].target)
        with self.assertRaises(CombatDamageAssignmentError):proposal.validate([
            {'source':'attacker','target':'blocker','amount':2},{'source':'attacker','target':'B','amount':3}])
        trample=self.proposal(trample=True)
        self.assertEqual(2,len(trample.validate([{'source':'attacker','target':'blocker','amount':3},{'source':'attacker','target':'B','amount':2}])))
        self.assertEqual('B',trample.validate([{'source':'attacker','target':'B','amount':5}])[0].target)
        with self.assertRaises(CombatDamageAssignmentError):trample.validate([
            {'source':'attacker','target':'blocker','amount':1},{'source':'attacker','target':'B','amount':4}])

    def test_departed_blockers_leave_optional_zero_or_all_recipient_assignment(self):
        proposal=self.proposal(blockers=False)
        self.assertIsNone(proposal.automatic_assignments())
        self.assertEqual([0,5],proposal.projected_options()['attacker']['allowed_totals'])
        self.assertEqual((),proposal.validate([]))
        self.assertEqual(5,proposal.validate([{'source':'attacker','target':'B','amount':5}])[0].amount)
        with self.assertRaises(CombatDamageAssignmentError):proposal.validate([{'source':'attacker','target':'B','amount':3}])
        with self.assertRaises(CombatDamageAssignmentError):self.proposal(trample=True,blockers=False).validate([])

    def test_permission_is_typed_and_proposal_identity_binds_legal_branch(self):
        fragment=ability_fragment_to_dict(AsUnblockedAssignmentPermission())
        self.assertEqual(AsUnblockedAssignmentPermission(),ability_fragment_from_dict(fragment))
        for raw in ({'schema_version':True},{'schema_version':2},{'schema_version':1,'extra':0}):
            with self.assertRaises(ValueError):AsUnblockedAssignmentPermission.from_dict(raw)
        proposal=self.proposal(trample=True)
        self.assertNotEqual(proposal.proposal_id,replace(proposal,as_unblocked_sources=()).proposal_id)
        for choices in ((AsUnblockedDamageSpec('other','B'),),(AsUnblockedDamageSpec('attacker','C'),),
            (AsUnblockedDamageSpec('attacker','B'),AsUnblockedDamageSpec('attacker','B'))):
            with self.assertRaises(CombatDamageAssignmentError):replace(proposal,as_unblocked_sources=choices)
        legacy=replace(proposal,as_unblocked_sources=())
        payload={'actor':legacy.actor,'damage_step_id':legacy.damage_step_id,'sources':[
            {'source':source.source,'logical_object_id':source.logical_object_id,'power':source.power,'targets':list(source.targets)} for source in legacy.sources]}
        expected='combat-assignment:'+hashlib.sha256(json.dumps(payload,sort_keys=True,separators=(',',':')).encode('utf-8')).hexdigest()
        self.assertEqual(expected,legacy.proposal_id)
        mandatory=replace(legacy,mandatory_as_unblocked_sources=frozenset({'attacker'}))
        self.assertNotEqual(legacy.proposal_id,mandatory.proposal_id)

    def test_compiler_static_attached_query_and_conditional_grants_are_closed(self):
        for text in (
            "You may have this creature assign its combat damage as though it weren't blocked.",
            "Enchanted creature's controller may have it assign its combat damage as though it weren't blocked.",
            "For each non-Human creature you control, you may have that creature assign its combat damage as though it weren't blocked.",
            "Creatures you control with trample have \"You may have this creature assign its combat damage as though it weren't blocked.\"",
            "As long as this creature is attacking, for each creature you control, you may have that creature assign its combat damage as though it weren't blocked.",
        ):
            result=static_as_unblocked_handler(text,source_name='Generic Assignment')
            self.assertIsNotNone(result,text);self.assertIn('combat.damage.assignment.as_unblocked',result[2])
            if 'source_condition' in result[1]:ConditionalAsUnblockedAssignmentHandler().validate(result[1])
        for text in ("You may have this creature assign some of its combat damage as though it weren't blocked.",
            "You may have this creature assign its combat damage as though it weren't blocked. Draw a card."):
            self.assertIsNone(static_as_unblocked_handler(text,source_name='Generic Assignment'))

    def test_temporary_permission_schema_rejects_unknown_subjects_and_extra_fields(self):
        from quorune.compiler.as_unblocked_templates import temporary_as_unblocked_template
        from quorune.rules.as_unblocked_effect import temporary_as_unblocked_capabilities
        text="You may have creatures you control assign their combat damage this turn as though they weren't blocked."
        compiled=temporary_as_unblocked_template(text,source_name='Generic Sorcery',source_is_permanent=False,source_card_types=('sorcery',))
        self.assertIsNotNone(compiled)
        inner=compiled[1][0]['effects'][0]
        self.assertIn('combat.damage.assignment.as_unblocked',temporary_as_unblocked_capabilities(effects=(inner,),target_schema=None,mechanic_ids=compiled[3]))
        for changed in ({'schema_version':True},{'permission':'assign_anywhere'},{'extra':0}):
            self.assertEqual((),temporary_as_unblocked_capabilities(effects=({**inner,**changed},),target_schema=None,mechanic_ids=compiled[3]))
        for text in ("X target blocked creatures assign their combat damage this turn as though they weren't blocked.",
            "You may have this creature assign its combat damage this turn as though it weren't blocked."):
            self.assertIsNone(temporary_as_unblocked_template(text,source_name='Generic Sorcery',source_is_permanent=False,source_card_types=('sorcery',)))

    def test_blocked_assignment_owner_prevents_exact_printed_and_temporary_closure(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.oracle_ir import compile_oracle_card
        from test_qualified_zone_event_queries import query_record
        value=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        for row in value['capabilities']:
            if row['id']=='combat.damage.assignment.as_unblocked':row.update(status='blocked',blockers=['independent assignment owner unavailable'])
        registry=CapabilityRegistry(value)
        for text,types in (("You may have this creature assign its combat damage as though it weren't blocked.",'Creature'),
            ("You may have creatures you control assign their combat damage this turn as though they weren't blocked.",'Sorcery')):
            ir=compile_oracle_card(replace(query_record(text),type_line=types),capability_registry=registry)
            self.assertNotEqual('exact',ir.status)

    def test_dynamic_assignment_rule_round_trip_is_distinct_from_ability_fragments(self):
        from quorune.as_unblocked_rule import AsUnblockedAssignmentRule
        from quorune.declaration_rule_effects import continuous_journal_effect_from_dict
        rule=AsUnblockedAssignmentRule('rule','source',1,controller='A')
        self.assertEqual(rule,continuous_journal_effect_from_dict(rule.to_dict()))
        for changed in ({'timestamp':True},{'controller':None},{'extra':0},{'duration':'while_source_present'}):
            with self.assertRaises(ValueError):AsUnblockedAssignmentRule.from_dict({**rule.to_dict(),**changed})

    def test_mandatory_rule_keeps_only_attacked_recipient_and_never_adds_optional_split(self):
        from quorune.combat_damage_snapshot import CombatDamageSnapshot,CombatDamageParticipant,CombatAttackRelationship,CombatDamageRecipient,CombatBlockRelationship
        from quorune.combat_damage_assignment import build_combat_damage_assignment_proposal
        attacker=CombatDamageParticipant('a','attacker','A',5,7,0,frozenset({'trample'}),True,must_assign_as_unblocked=True)
        blocker=CombatDamageParticipant('b','blocker','B',2,3,0,frozenset(),True)
        snapshot=CombatDamageSnapshot('step',0,False,'A',(attacker,blocker),
            (CombatAttackRelationship('a',CombatDamageRecipient('B','player:B','B','player',True)),),
            (CombatBlockRelationship('a','b'),),frozenset({'a'}))
        proposal=build_combat_damage_assignment_proposal(seat='A',snapshot=snapshot)
        self.assertEqual(['B'],proposal.projected_options()['attacker']['targets'])
        self.assertEqual(frozenset({'attacker'}),proposal.mandatory_as_unblocked_sources)
        self.assertEqual((),proposal.as_unblocked_sources);self.assertEqual((),proposal.trample_sources)
        self.assertEqual('B',proposal.automatic_assignments()[0].target)
        with self.assertRaises(CombatDamageAssignmentError):proposal.validate([{'source':'attacker','target':'blocker','amount':5}])


class AsUnblockedAssignmentRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'assignment.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',ROOT/'tests/fixtures/as-unblocked-assignment-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('As-unblocked witness',[DeckEntry('Generic Bound Commander',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])
    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temporary.cleanup()
    session=witnesses.BoundEffectProgramRuntimeTests.session
    add=witnesses.BoundEffectProgramRuntimeTests.add
    ready=witnesses.BoundEffectProgramRuntimeTests.ready
    resolve=witnesses.BoundEffectProgramRuntimeTests.resolve
    checkpoint=witnesses.BoundEffectProgramRuntimeTests.checkpoint
    replay=witnesses.BoundEffectProgramRuntimeTests.replay

    def damage(self,session,attacker,blocker,*,recipient='B'):
        engine=session.engine;engine.permissions.invalidate_current();engine.state.pending_decision=None
        engine.state.priority_player=None;engine.state.priority_passes=[]
        engine.state.started=True;engine.state.active_player='A';engine.state.phase_index=7;engine.state.phase='combat';engine.state.step='combat_damage'
        attacker.attacking=recipient;blocker.blocking=attacker.object_id
        engine.state.combat=CombatState(attackers_declared=True,blockers_declared=True,had_attacking_creature=True,
            attackers={attacker.object_id:recipient},defending_players=[blocker.controller],blockers={attacker.object_id:[blocker.object_id]})
        engine.state.combat.attack_target_context[attacker.object_id]=engine._attack_target_details('A',recipient)
        engine._initialize_combat_damage_steps();engine._begin_combat_damage()

    def test_actual_thorn_optional_assignment_is_principal_scoped_and_replays(self):
        session=self.session(291001);engine=session.engine
        attacker=self.add(engine,'Thorn Elemental');blocker=self.add(engine,'Generic Bound Body',seat='C')
        self.damage(session,attacker,blocker,recipient='C');self.checkpoint(session)
        self.assertIsNotNone(session.state.pending_decision)
        self.assertEqual('combat.damage',session.state.pending_decision.kind)
        packet=session.packet('pilot:A',full=True)['decision']
        self.assertIn('C',str(packet));self.assertIsNone(session.packet('pilot:B',full=True)['decision'])
        before=authoritative_state_hash(session.state)
        wrong=session.act('pilot:B',{'a':'dmg','assignments':[{'source':attacker.ref,'target':'C','amount':7}]})
        self.assertFalse(wrong.ok);self.assertEqual(before,authoritative_state_hash(session.state))
        mixed=session.act('pilot:A',{'a':'dmg','assignments':[{'source':attacker.ref,'target':'C','amount':4},{'source':attacker.ref,'target':blocker.ref,'amount':3}]})
        self.assertFalse(mixed.ok);self.assertEqual(before,authoritative_state_hash(session.state))
        resumed=self.replay(session,load=True)
        life=resumed.state.players['C'].life
        accepted=resumed.act('pilot:A',{'a':'dmg','assignments':[{'source':attacker.ref,'target':'C','amount':7}]})
        self.assertTrue(accepted.ok,accepted.summary)
        self.assertEqual(life-7,resumed.state.players['C'].life)
        self.assertEqual(0,resumed.state.cards[blocker.object_id].marked_damage)
        self.assertEqual(2,resumed.state.cards[attacker.object_id].marked_damage)
        self.assertIn(attacker.object_id,resumed.state.combat.blockers)
        self.replay(resumed,load=True)

    def test_assignment_permission_mutant_is_killed(self):
        import quorune.combat_damage_engine_adapter as owner
        with patch.object(owner,'can_assign_as_unblocked',return_value=False):
            with self.assertRaises(AssertionError):self.test_actual_thorn_optional_assignment_is_principal_scoped_and_replays()

    def test_actual_thorn_can_assign_ordinary_damage_to_blocker(self):
        session=self.session(291002);engine=session.engine
        attacker=self.add(engine,'Thorn Elemental');blocker=self.add(engine,'Generic Bound Body',seat='B')
        self.damage(session,attacker,blocker);self.checkpoint(session)
        before=session.state.players['B'].life
        accepted=session.act('pilot:A',{'a':'dmg','assignments':[{'source':attacker.ref,'target':blocker.ref,'amount':7}]})
        self.assertTrue(accepted.ok,accepted.summary)
        self.assertEqual(before,session.state.players['B'].life)
        self.assertEqual('graveyard',session.state.cards[blocker.object_id].zone)
        self.assertEqual(2,session.state.cards[attacker.object_id].marked_damage)
        self.replay(session,load=True)

    def test_actual_spinebiter_as_unblocked_uses_infect_and_blocker_still_deals_damage(self):
        session=self.session(291003);engine=session.engine
        attacker=self.add(engine,'Spinebiter');blocker=self.add(engine,'Generic Bound Body',seat='B')
        self.damage(session,attacker,blocker);self.checkpoint(session)
        life=session.state.players['B'].life
        accepted=session.act('pilot:A',{'a':'dmg','assignments':[{'source':attacker.ref,'target':'B','amount':3}]})
        self.assertTrue(accepted.ok,accepted.summary)
        self.assertEqual(life,session.state.players['B'].life);self.assertEqual(3,session.state.players['B'].poison)
        self.assertEqual(2,session.state.cards[attacker.object_id].marked_damage)
        self.assertEqual({},session.state.cards[blocker.object_id].counters)
        self.replay(session,load=True)

    def test_actual_predatory_focus_chooses_at_resolution_and_applies_to_later_creatures(self):
        session=self.session(291004);engine=session.engine
        body=self.add(engine,'Generic Bound Body')
        enemy=self.add(engine,'Generic Bound Body',seat='B',ref='enemy-body')
        spell=self.add(engine,'Predatory Focus',zone='hand')
        action=self.ready(session,spell,{'C':3,'G':2});self.checkpoint(session)
        accepted=session.act('pilot:A',{'action_id':action['id'],'pay':'auto'});self.assertTrue(accepted.ok,accepted.summary)
        self.resolve(session);self.assertEqual('semantic.choice',session.state.pending_decision.kind)
        resumed=self.replay(session,load=True)
        accepted=resumed.act('pilot:A',{'action_id':'choose','choice':'apply'});self.assertTrue(accepted.ok,accepted.summary)
        self.resolve(resumed);session=resumed;engine=resumed.engine;body=session.state.cards[body.object_id];enemy=session.state.cards[enemy.object_id]
        from quorune.as_unblocked_rule import active_as_unblocked_rule
        self.assertTrue(active_as_unblocked_rule(session.state,body))
        self.assertFalse(active_as_unblocked_rule(session.state,enemy))
        self.replay(session,load=True)
        late=self.add(engine,'Generic Bound Body',ref='later-body')
        self.assertTrue(active_as_unblocked_rule(session.state,late))
        engine.move_card(body.object_id,'graveyard',log=False);engine.move_card(body.object_id,'battlefield',log=False)
        self.assertTrue(active_as_unblocked_rule(session.state,body))
        self.damage(session,body,enemy);self.checkpoint(session)
        # Accepted Predatory Focus makes assignment mandatory for every
        # current controlled attacker: this step has no optional branch.
        self.assertEqual(38,session.state.players['B'].life)
        self.assertEqual(0,session.state.cards[enemy.object_id].marked_damage)
        self.replay(session,load=True)
        from quorune.continuous_effect_state import expire_end_of_turn_continuous_effects
        expire_end_of_turn_continuous_effects(session.state)
        self.assertEqual([],session.state.continuous_effects)

    def test_actual_predatory_focus_decline_creates_no_assignment_rule(self):
        session=self.session(291026);engine=session.engine
        spell=self.add(engine,'Predatory Focus',zone='hand')
        action=self.ready(session,spell,{'C':3,'G':2});self.checkpoint(session)
        accepted=session.act('pilot:A',{'action_id':action['id'],'pay':'auto'});self.assertTrue(accepted.ok,accepted.summary)
        self.resolve(session);before=authoritative_state_hash(session.state)
        wrong=session.act('pilot:B',{'action_id':'choose','choice':'apply'})
        self.assertFalse(wrong.ok);self.assertEqual(before,authoritative_state_hash(session.state))
        result=session.act('pilot:A',{'action_id':'choose','choice':'decline'});self.assertTrue(result.ok,result.summary)
        self.resolve(session);self.assertEqual([],session.state.continuous_effects)
        self.replay(session,load=True)

    def test_accepted_predatory_focus_rule_applies_to_real_later_cast_and_forces_recipient(self):
        session=self.session(291027);engine=session.engine
        spell=self.add(engine,'Predatory Focus',zone='hand')
        later=self.add(engine,'Generic Bound Body',zone='hand')
        blocker=self.add(engine,'Generic Bound Body',seat='B',ref='later-cast-blocker')
        action=self.ready(session,spell,{'C':3,'G':2,'W':1});self.checkpoint(session)
        accepted=session.act('pilot:A',{'action_id':action['id'],'pay':'manual','payment':{'C':3,'G':2}});self.assertTrue(accepted.ok,accepted.summary)
        self.resolve(session)
        selected=session.act('pilot:A',{'action_id':'choose','choice':'apply'});self.assertTrue(selected.ok,selected.summary);self.resolve(session)
        cast=next(row for row in session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions'] if row.get('card')==later.ref)
        accepted=session.act('pilot:A',{'action_id':cast['id'],'pay':'auto'});self.assertTrue(accepted.ok,accepted.summary);self.resolve(session)
        from quorune.as_unblocked_rule import active_as_unblocked_rule
        self.assertTrue(active_as_unblocked_rule(session.state,session.state.cards[later.object_id]))
        self.replay(session,load=True)
        self.damage(session,later,blocker)
        # This rule supplies a forced recipient even though the creature was
        # blocked; no legal split or later per-creature decline exists.
        self.assertEqual(38,session.state.players['B'].life)
        self.assertEqual(0,session.state.cards[blocker.object_id].marked_damage)
        self.assertEqual(2,session.state.cards[later.object_id].marked_damage)

    def test_quoted_temporary_ability_grant_locks_recipients_and_leaves_assignment_optional(self):
        session=self.session(291028);engine=session.engine
        attacker=self.add(engine,'Generic Bound Body');blocker=self.add(engine,'Generic Bound Body',seat='B',ref='quoted-blocker')
        spell=self.add(engine,'Generic Quoted Assignment Grant',zone='hand')
        action=self.ready(session,spell,{});self.checkpoint(session)
        accepted=session.act('pilot:A',{'action_id':action['id'],'pay':'auto'});self.assertTrue(accepted.ok,accepted.summary);self.resolve(session)
        from quorune.as_unblocked import can_assign_as_unblocked
        self.assertTrue(can_assign_as_unblocked(engine._effective_ability_fragments(attacker)))
        self.replay(session,load=True)
        late=self.add(engine,'Generic Bound Body',ref='later-quoted-body')
        self.assertFalse(can_assign_as_unblocked(engine._effective_ability_fragments(late)))
        self.damage(session,attacker,blocker);self.checkpoint(session)
        self.assertEqual('combat.damage',session.state.pending_decision.kind)
        accepted=session.act('pilot:A',{'a':'dmg','assignments':[{'source':attacker.ref,'target':blocker.ref,'amount':2}]})
        self.assertTrue(accepted.ok,accepted.summary);self.assertEqual(40,session.state.players['B'].life)
        self.replay(session,load=True)
        engine.move_card(attacker.object_id,'graveyard',log=False);engine.move_card(attacker.object_id,'battlefield',log=False)
        self.assertFalse(can_assign_as_unblocked(engine._effective_ability_fragments(attacker)))
        from quorune.continuous_effect_state import expire_end_of_turn_continuous_effects
        expire_end_of_turn_continuous_effects(session.state)
        self.assertEqual([],session.state.continuous_effects)

    def test_resolved_assignment_rule_survives_creature_ability_removal(self):
        session=self.session(291029);engine=session.engine
        attacker=self.add(engine,'Generic Bound Body');blocker=self.add(engine,'Generic Bound Body',seat='B',ref='rule-blocker')
        spell=self.add(engine,'Predatory Focus',zone='hand')
        action=self.ready(session,spell,{'C':3,'G':2});self.checkpoint(session)
        accepted=session.act('pilot:A',{'action_id':action['id'],'pay':'auto'});self.assertTrue(accepted.ok,accepted.summary);self.resolve(session)
        selected=session.act('pilot:A',{'action_id':'choose','choice':'apply'});self.assertTrue(selected.ok,selected.summary);self.resolve(session)
        self.replay(session,load=True)
        from quorune.continuous_effect_state import create_resolution_continuous_effect,ResolutionEffectSource
        from quorune.continuous_effects import ContinuousOperation,Layer
        create_resolution_continuous_effect(engine,source=ResolutionEffectSource('direct:'+attacker.ref,attacker.object_id,attacker.logical_object_id,attacker.ref),
            targets=(attacker,),layer=Layer.ABILITY,sublayer='6',operations=(ContinuousOperation('remove_all_abilities'),))
        from quorune.as_unblocked import can_assign_as_unblocked
        from quorune.as_unblocked_rule import active_as_unblocked_rule
        self.assertFalse(can_assign_as_unblocked(engine._effective_ability_fragments(attacker)))
        self.assertTrue(active_as_unblocked_rule(session.state,attacker))
        self.damage(session,attacker,blocker)
        self.assertEqual(38,session.state.players['B'].life)
        self.assertEqual(0,session.state.cards[blocker.object_id].marked_damage)

    def test_actual_indomitable_might_attachment_grants_current_recipient_and_replays(self):
        session=self.session(291005);engine=session.engine
        attacker=self.add(engine,'Generic Bound Body')
        blocker=self.add(engine,'Generic Bound Body',seat='B',ref='aura-blocker')
        aura=self.add(engine,'Indomitable Might',zone='hand')
        action=self.ready(session,aura,{'C':3,'G':1});self.checkpoint(session)
        accepted=session.act('pilot:A',{'action_id':action['id'],'targets':[attacker.ref],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary);self.resolve(session)
        from quorune.as_unblocked import can_assign_as_unblocked
        self.assertTrue(can_assign_as_unblocked(engine._effective_ability_fragments(attacker)))
        self.replay(session,load=True)
        self.damage(session,attacker,blocker);self.checkpoint(session)
        life=session.state.players['B'].life
        accepted=session.act('pilot:A',{'a':'dmg','assignments':[{'source':attacker.ref,'target':'B','amount':5}]})
        self.assertTrue(accepted.ok,accepted.summary);self.assertEqual(life-5,session.state.players['B'].life)
        self.replay(session,load=True)
        engine.move_card(aura.object_id,'graveyard',log=False)
        self.assertFalse(can_assign_as_unblocked(engine._effective_ability_fragments(attacker)))

    def test_actual_proud_wildbonder_preserves_trample_split_and_all_recipient_choice(self):
        session=self.session(291006);engine=session.engine
        attacker=self.add(engine,'Proud Wildbonder');blocker=self.add(engine,'Generic Bound Body',seat='B')
        self.damage(session,attacker,blocker);self.checkpoint(session)
        before=authoritative_state_hash(session.state)
        split=session.act('pilot:A',{'a':'dmg','assignments':[{'source':attacker.ref,'target':blocker.ref,'amount':1},{'source':attacker.ref,'target':'B','amount':3}]})
        self.assertFalse(split.ok);self.assertEqual(before,authoritative_state_hash(session.state))
        accepted=session.act('pilot:A',{'a':'dmg','assignments':[{'source':attacker.ref,'target':'B','amount':4}]})
        self.assertTrue(accepted.ok,accepted.summary)
        self.assertEqual(2,session.state.cards[attacker.object_id].marked_damage)
        self.replay(session,load=True)

    def test_actual_trample_permission_accepts_lethal_then_spill_split(self):
        session=self.session(291023);engine=session.engine
        attacker=self.add(engine,'Proud Wildbonder');blocker=self.add(engine,'Generic Small Assignment Blocker',seat='B')
        self.damage(session,attacker,blocker)
        from quorune.combat_damage_projection import project_combat_damage_assignment
        from quorune.combat_damage_engine_adapter import EngineCombatDamageQuery
        proposal=project_combat_damage_assignment(EngineCombatDamageQuery(engine),'A')
        lethal=proposal.trample_sources[0].blockers[0][1].toughness
        self.assertLess(lethal,4)
        self.checkpoint(session)
        life=session.state.players['B'].life
        accepted=session.act('pilot:A',{'a':'dmg','assignments':[{'source':attacker.ref,'target':blocker.ref,'amount':lethal},{'source':attacker.ref,'target':'B','amount':4-lethal}]})
        self.assertTrue(accepted.ok,accepted.summary)
        self.assertEqual(life-(4-lethal),session.state.players['B'].life)
        self.replay(session,load=True)

    def test_as_unblocked_damage_uses_attacked_planeswalker_or_battle_not_its_controller(self):
        for index,kind in enumerate(('planeswalker','battle')):
            session=self.session(291007+index);engine=session.engine
            attacker=self.add(engine,'Thorn Elemental');blocker=self.add(engine,'Generic Bound Body',seat='B')
            reference=engine.create_token('C' if kind=='battle' else 'B',name='Generic Attacked '+kind,
                battle_protector='B' if kind=='battle' else None,
                characteristics={'type_line':'Token Battle — Siege' if kind=='battle' else 'Token Planeswalker — Test',
                    'defense':'10','loyalty':'10'})[0]
            recipient=engine._resolve_object('A',reference,zones={'battlefield'})
            self.damage(session,attacker,blocker,recipient=recipient.ref);self.checkpoint(session)
            lives={seat:player.life for seat,player in session.state.players.items()}
            accepted=session.act('pilot:A',{'a':'dmg','assignments':[{'source':attacker.ref,'target':recipient.ref,'amount':7}]})
            self.assertTrue(accepted.ok,accepted.summary)
            self.assertEqual(3,session.state.cards[recipient.object_id].counters['defense' if kind=='battle' else 'loyalty'])
            self.assertEqual(lives,{seat:player.life for seat,player in session.state.players.items()})
            self.replay(session,load=True)

    def test_double_strike_makes_independent_assignment_choices_in_both_damage_steps(self):
        session=self.session(291009);engine=session.engine
        attacker=self.add(engine,'Generic Double Strike Assignment');blocker=self.add(engine,'Generic Bound Body',seat='B')
        self.damage(session,attacker,blocker);self.checkpoint(session)
        life=session.state.players['B'].life
        first=session.act('pilot:A',{'a':'dmg','assignments':[{'source':attacker.ref,'target':'B','amount':4}]})
        self.assertTrue(first.ok,first.summary);self.assertEqual(life-4,session.state.players['B'].life)
        self.assertEqual(0,session.state.cards[attacker.object_id].marked_damage)
        for _ in range(16):
            if session.state.pending_decision is not None and session.state.pending_decision.kind=='combat.damage':break
            result=session.act(session.pending_principals()[0],{'action_id':'pass'});self.assertTrue(result.ok,result.summary)
        self.assertEqual(1,session.state.combat.damage_step_index)
        second=session.act('pilot:A',{'a':'dmg','assignments':[{'source':attacker.ref,'target':blocker.ref,'amount':4}]})
        self.assertTrue(second.ok,second.summary)
        self.assertEqual(life-4,session.state.players['B'].life)
        self.assertEqual(4,session.state.cards[blocker.object_id].marked_damage)
        self.assertEqual(2,session.state.cards[attacker.object_id].marked_damage)
        self.replay(session,load=True)

    def test_current_permission_loss_invalidates_pending_assignment_before_mutation(self):
        session=self.session(291010);engine=session.engine
        attacker=self.add(engine,'Generic Bound Body');blocker=self.add(engine,'Generic Bound Body',seat='B',ref='stale-blocker')
        aura=self.add(engine,'Indomitable Might',zone='hand')
        action=self.ready(session,aura,{'C':3,'G':1})
        accepted=session.act('pilot:A',{'action_id':action['id'],'targets':[attacker.ref],'pay':'auto'});self.assertTrue(accepted.ok,accepted.summary)
        self.resolve(session);self.damage(session,attacker,blocker)
        engine.move_card(aura.object_id,'graveyard',log=False)
        before=authoritative_state_hash(session.state)
        rejected=session.act('pilot:A',{'a':'dmg','assignments':[{'source':attacker.ref,'target':'B','amount':5}]})
        self.assertFalse(rejected.ok);self.assertEqual(before,authoritative_state_hash(session.state))

    def test_actual_siege_condition_grants_only_while_source_is_attacking(self):
        session=self.session(291011);engine=session.engine
        source=self.add(engine,'Siege Behemoth')
        attacker=self.add(engine,'Generic Bound Body');blocker=self.add(engine,'Generic Bound Body',seat='B',ref='siege-blocker')
        from quorune.as_unblocked import can_assign_as_unblocked
        self.assertFalse(can_assign_as_unblocked(engine._effective_ability_fragments(attacker)))
        source.attacking='B';engine.state.combat.attackers[source.object_id]='B'
        self.assertTrue(can_assign_as_unblocked(engine._effective_ability_fragments(attacker)))
        self.damage(session,attacker,blocker)
        # The helper rebuilds combat relationships, so add the source's actual
        # represented attack before a fresh shared snapshot is issued.
        engine.state.combat.attackers[source.object_id]='B';source.attacking='B'
        engine.permissions.invalidate_current();engine.state.pending_decision=None;engine._begin_combat_damage();self.checkpoint(session)
        life=session.state.players['B'].life
        accepted=session.act('pilot:A',{'a':'dmg','assignments':[
            {'source':attacker.ref,'target':'B','amount':2},{'source':source.ref,'target':'B','amount':7}]})
        self.assertTrue(accepted.ok,accepted.summary);self.assertEqual(life-9,session.state.players['B'].life)
        self.replay(session,load=True)

    def test_departed_attacked_permanent_is_not_replaced_with_its_controller(self):
        session=self.session(291012);engine=session.engine
        attacker=self.add(engine,'Thorn Elemental');blocker=self.add(engine,'Generic Bound Body',seat='B')
        reference=engine.create_token('B',name='Generic Departed Walker',characteristics={'type_line':'Token Planeswalker — Test','loyalty':'10'})[0]
        walker=engine._resolve_object('A',reference,zones={'battlefield'})
        self.damage(session,attacker,blocker,recipient=walker.ref)
        engine.move_card(walker.object_id,'graveyard',log=False)
        from quorune.combat_damage_projection import project_combat_damage_assignment
        from quorune.combat_damage_engine_adapter import EngineCombatDamageQuery
        proposal=project_combat_damage_assignment(EngineCombatDamageQuery(engine),'A')
        self.assertEqual([blocker.ref],proposal.projected_options()[attacker.ref]['targets'])
        before=authoritative_state_hash(session.state)
        rejected=session.act('pilot:A',{'a':'dmg','assignments':[{'source':attacker.ref,'target':'B','amount':7}]})
        self.assertFalse(rejected.ok);self.assertEqual(before,authoritative_state_hash(session.state))

    def test_source_ability_removal_changes_permission_before_second_damage_step(self):
        session=self.session(291013);engine=session.engine
        attacker=self.add(engine,'Generic Double Strike Assignment');blocker=self.add(engine,'Generic Bound Body',seat='B')
        self.damage(session,attacker,blocker)
        accepted=session.act('pilot:A',{'a':'dmg','assignments':[{'source':attacker.ref,'target':'B','amount':4}]})
        self.assertTrue(accepted.ok,accepted.summary)
        # Owner temporal diagnostic: removing its permission during the
        # intervening priority window changes the next step's current fact.
        from quorune.continuous_effect_state import create_resolution_continuous_effect,ResolutionEffectSource
        from quorune.continuous_effects import ContinuousOperation,Layer
        create_resolution_continuous_effect(engine,source=ResolutionEffectSource('direct:'+attacker.ref,attacker.object_id,attacker.logical_object_id,attacker.ref),
            targets=(attacker,),layer=Layer.ABILITY,sublayer='6',operations=(ContinuousOperation('remove_all_abilities'),ContinuousOperation('add_ability','Double Strike')))
        for _ in range(16):
            if session.state.combat.damage_step_index==1:break
            result=session.act(session.pending_principals()[0],{'action_id':'pass'});self.assertTrue(result.ok,result.summary)
        self.assertEqual(1,session.state.combat.damage_step_index)
        self.assertEqual(4,session.state.cards[blocker.object_id].marked_damage)
        self.assertEqual(36,session.state.players['B'].life)

    def test_departed_blockers_offer_zero_or_all_damage_to_attacked_seat(self):
        for amount in (0,7):
            session=self.session(291014+amount);engine=session.engine
            attacker=self.add(engine,'Thorn Elemental');blocker=self.add(engine,'Generic Bound Body',seat='B')
            self.damage(session,attacker,blocker)
            engine.move_card(blocker.object_id,'graveyard',log=False)
            engine.permissions.invalidate_current();engine.state.pending_decision=None
            engine._begin_combat_damage();self.checkpoint(session)
            packet=session.packet('pilot:A',full=True)['decision'];self.assertEqual('combat.damage',session.state.pending_decision.kind)
            source=packet['ctx']['combat']['damage_sources'][attacker.ref]
            self.assertEqual([0,7],source['allowed_totals'])
            rows=[] if amount==0 else [{'source':attacker.ref,'target':'B','amount':7}]
            before=session.state.players['B'].life
            accepted=session.act('pilot:A',{'a':'dmg','assignments':rows});self.assertTrue(accepted.ok,accepted.summary)
            self.assertEqual(before-amount,session.state.players['B'].life)
            self.replay(session,load=True)

    def test_malformed_temporary_permission_rejects_before_journal_mutation(self):
        session=self.session(291025);engine=session.engine
        from quorune.rules.as_unblocked_effect import apply_temporary_as_unblocked
        from quorune.object_predicate import ObjectQuerySpec
        from quorune.errors import GameRuleError
        effect={'op':'apply_source_characteristics_until_end_of_turn','schema_version':4,'permission':'assign_as_unblocked','mode':'assignment_rule',
            'predicate':ObjectQuerySpec(zones=('battlefield',),controller='A',types_all=('creature',)).to_dict()}
        before=authoritative_state_hash(session.state)
        for changed in ({'schema_version':True},{'permission':'assign_anywhere'},{'extra':0}):
            with self.assertRaises(GameRuleError):apply_temporary_as_unblocked(engine,{**effect,**changed},actor='A',reason='malformed witness')
            self.assertEqual(before,authoritative_state_hash(session.state))


if __name__=='__main__':unittest.main()
