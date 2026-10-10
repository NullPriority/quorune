from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from common import ROOT
from quorune.ability_fragments import ability_fragment_from_dict,ability_fragment_to_dict
from quorune.compiler.toughness_assignment_templates import static_toughness_assignment_handler
from quorune.toughness_assignment_model import ToughnessAssignmentSpec
from quorune.object_predicate import ObjectQuerySpec
from quorune.combat_damage_snapshot import CombatDamageParticipant,CombatDamageSnapshot,CombatAttackRelationship,CombatBlockRelationship,CombatDamageRecipient
from quorune.combat_damage_assignment import build_combat_damage_assignment_proposal
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.rules.capabilities import load_default_capability_registry
from quorune.record import authoritative_state_hash
from scripts.build_test_database import build_fixture_database
import test_bound_effect_programs as witnesses
import test_as_unblocked_assignment as assignment_witnesses


class ToughnessAssignmentCompilerTests(unittest.TestCase):
    def test_temporary_rule_schema_and_blocked_assignment_owner_fail_closed(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.oracle_ir import compile_oracle_card
        from test_qualified_zone_event_queries import query_record
        from quorune.toughness_assignment_rule import ResolvedToughnessAssignmentRule
        from quorune.continuous_effect_model import ContinuousObjectIdentity
        from quorune.declaration_rule_effects import continuous_journal_effect_from_dict
        rule=ResolvedToughnessAssignmentRule('rule','source',1,(ContinuousObjectIdentity('card','card:0'),))
        self.assertEqual(rule,continuous_journal_effect_from_dict(rule.to_dict()))
        for changed in ({'timestamp':True},{'locked_objects':[]},{'duration':'while_source_present'},{'extra':0}):
            with self.assertRaises(ValueError):ResolvedToughnessAssignmentRule.from_dict({**rule.to_dict(),**changed})
        value=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        for row in value['capabilities']:
            if row['id']=='combat.damage.assignment.toughness':row.update(status='blocked',blockers=['independent assignment owner unavailable'])
        for text,types in (('Each creature assigns combat damage equal to its toughness rather than its power.','Creature'),
            ('Target creature you control assigns combat damage equal to its toughness rather than its power this turn.','Sorcery')):
            ir=compile_oracle_card(replace(query_record(text),type_line=types),capability_registry=CapabilityRegistry(value))
            self.assertNotEqual('exact',ir.status)
    def test_static_rule_shapes_preserve_scope_and_separate_characteristic_prefix(self):
        for text,scope,greater in (
            ('Each creature assigns combat damage equal to its toughness rather than its power.','all',False),
            ('Each creature you control assigns combat damage equal to its toughness rather than its power.','controller',False),
            ('Each creature you control with toughness greater than its power assigns combat damage equal to its toughness rather than its power.','controller',True),
            ('This creature assigns combat damage equal to its toughness rather than its power.','self',False),
            ('As long as equipped creature\'s toughness is greater than its power, it assigns combat damage equal to its toughness rather than its power.','attached',True),
            ('As long as enchanted creature has vigilance, it assigns combat damage equal to its toughness rather than its power.','attached',False),
            ('Enchanted creature gets +0/+2 and assigns combat damage equal to its toughness rather than its power.','attached',False),
        ):
            result=static_toughness_assignment_handler(text,source_name='Generic Source');self.assertIsNotNone(result,text)
            descriptors=result[1] if isinstance(result[1],tuple) else (result[1],)
            rule=ability_fragment_from_dict(descriptors[-1]['fragment'])
            self.assertEqual(scope,rule.scope);self.assertEqual(greater,rule.toughness_greater_than_power)
            if 'gets' in text:self.assertEqual(2,descriptors[0]['modifier']['toughness'])

    def test_toughness_rule_codec_and_unknown_subjects_fail_closed(self):
        rule=ToughnessAssignmentSpec('all',ObjectQuerySpec(zones=('battlefield',),types_all=('creature',)))
        self.assertEqual(rule,ability_fragment_from_dict(ability_fragment_to_dict(rule)))
        for changed in ({'schema_version':True},{'scope':'opponents'},{'toughness_greater_than_power':1},{'extra':0}):
            with self.assertRaises(ValueError):ToughnessAssignmentSpec.from_dict({**rule.to_dict(),**changed})
        for text in ('Each creature assigns all damage equal to its toughness rather than its power.',
            'Each creature with power less than 7 assigns combat damage equal to its toughness rather than its power.',
            'Each creature assigns combat damage equal to its toughness rather than its power. You win the game.'):
            self.assertIsNone(static_toughness_assignment_handler(text,source_name='Generic Source'),text)
        self.assertIsNone(static_toughness_assignment_handler("Each creature you control with toughness greater than its power assigns combat damage equal to its toughness rather than its power and can attack as though it didn't have defender.",source_name='Generic Source'))

    def test_snapshot_assignment_uses_current_toughness_for_attacker_and_blocker(self):
        attacker=CombatDamageParticipant('a','attacker','A',1,7,3,frozenset(),True,assigns_using_toughness=True)
        blocker=CombatDamageParticipant('b','blocker','B',2,4,1,frozenset(),True,assigns_using_toughness=True)
        snapshot=CombatDamageSnapshot('step',0,False,'A',(attacker,blocker),
            (CombatAttackRelationship('a',CombatDamageRecipient('B','player:B','B','player',True)),),
            (CombatBlockRelationship('a','b'),),frozenset({'a'}))
        self.assertEqual(7,build_combat_damage_assignment_proposal(seat='A',snapshot=snapshot).sources[0].power)
        self.assertEqual(4,build_combat_damage_assignment_proposal(seat='B',snapshot=snapshot).sources[0].power)
        self.assertEqual(1,attacker.power);self.assertEqual(7,attacker.toughness)
        negative=replace(attacker,toughness=-1)
        self.assertEqual(0,build_combat_damage_assignment_proposal(seat='A',snapshot=replace(snapshot,participants=(negative,blocker))).sources[0].power)


class ToughnessAssignmentRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'toughness.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',ROOT/'tests/fixtures/toughness-assignment-cards.json',ROOT/'tests/fixtures/defender-permission-cards.json',ROOT/'tests/fixtures/static-characteristic-setting-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Toughness witness',[DeckEntry('Generic Bound Commander',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])
    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temporary.cleanup()
    session=witnesses.BoundEffectProgramRuntimeTests.session
    add=witnesses.BoundEffectProgramRuntimeTests.add
    ready=witnesses.BoundEffectProgramRuntimeTests.ready
    resolve=witnesses.BoundEffectProgramRuntimeTests.resolve
    checkpoint=witnesses.BoundEffectProgramRuntimeTests.checkpoint
    replay=witnesses.BoundEffectProgramRuntimeTests.replay
    damage=assignment_witnesses.AsUnblockedAssignmentRuntimeTests.damage

    def test_actual_doran_changes_both_sides_combat_assignment_and_replays(self):
        session=self.session(292001);engine=session.engine
        self.add(engine,'Doran, the Siege Tower')
        attacker=self.add(engine,'Generic Bound Body');blocker=self.add(engine,'Generic Bound Body',seat='C',ref='doran-blocker')
        other=self.add(engine,'Generic Bound Body',seat='C',ref='other-doran-blocker')
        attacker.counters['+1/+1']=1
        from quorune.model import CombatState
        engine.permissions.invalidate_current();engine.state.pending_decision=None;engine.state.priority_player=None
        engine.state.started=True;engine.state.active_player='A';engine.state.phase_index=7;engine.state.phase='combat';engine.state.step='combat_damage'
        attacker.attacking='C';blocker.blocking=attacker.object_id;other.blocking=attacker.object_id
        engine.state.combat=CombatState(attackers_declared=True,blockers_declared=True,had_attacking_creature=True,
            attackers={attacker.object_id:'C'},defending_players=['C'],blockers={attacker.object_id:[blocker.object_id,other.object_id]})
        engine._begin_combat_damage();self.checkpoint(session)
        packet=session.packet('pilot:A',full=True)['decision'];self.assertEqual(7,packet['ctx']['combat']['damage_sources'][attacker.ref]['power'])
        self.assertEqual(3,engine._numeric_stat(attacker.object_id,'power'));self.assertEqual(7,engine._numeric_stat(attacker.object_id,'toughness'))
        before=authoritative_state_hash(session.state)
        wrong=session.act('pilot:B',{'a':'dmg','assignments':[{'source':attacker.ref,'target':blocker.ref,'amount':7}]})
        self.assertFalse(wrong.ok);self.assertEqual(before,authoritative_state_hash(session.state))
        accepted=session.act('pilot:A',{'a':'dmg','assignments':[{'source':attacker.ref,'target':blocker.ref,'amount':7}]})
        self.assertTrue(accepted.ok,accepted.summary)
        self.assertEqual('graveyard',session.state.cards[attacker.object_id].zone)
        self.assertEqual('graveyard',session.state.cards[blocker.object_id].zone)
        self.assertEqual('battlefield',session.state.cards[other.object_id].zone)
        self.replay(session,load=True)

    def test_toughness_assignment_mutant_is_killed(self):
        import quorune.combat_damage_engine_adapter as owner
        with patch.object(owner,'current_toughness_assignment',return_value=False):
            with self.assertRaises(AssertionError):self.test_actual_doran_changes_both_sides_combat_assignment_and_replays()

    def test_actual_high_alert_scope_does_not_change_enemy_assignment_or_power(self):
        session=self.session(292002);engine=session.engine
        source=self.add(engine,'High Alert')
        attacker=self.add(engine,'Generic Bound Body');blocker=self.add(engine,'Generic Bound Body',seat='B',ref='alert-blocker')
        self.assertEqual(2,engine._numeric_stat(attacker.object_id,'power'))
        self.damage(session,attacker,blocker)
        self.assertEqual('graveyard',session.state.cards[blocker.object_id].zone)
        self.assertEqual(2,session.state.cards[attacker.object_id].marked_damage)
        from quorune.toughness_assignment import current_toughness_assignment
        engine.move_card(source.object_id,'graveyard',log=False)
        self.assertFalse(current_toughness_assignment(engine,attacker,power=2,toughness=6))

    def test_actual_gauntlets_add_toughness_before_assignment_and_replay(self):
        session=self.session(292003);engine=session.engine
        attacker=self.add(engine,'Generic Bound Body');blocker=self.add(engine,'Generic Bound Body',seat='B',ref='gauntlets-blocker')
        aura=self.add(engine,'Gauntlets of Light',zone='hand')
        action=self.ready(session,aura,{'C':1,'W':2});self.checkpoint(session)
        accepted=session.act('pilot:A',{'action_id':action['id'],'targets':[attacker.ref],'pay':'auto'});self.assertTrue(accepted.ok,accepted.summary);self.resolve(session)
        self.assertEqual(2,engine._numeric_stat(attacker.object_id,'power'));self.assertEqual(8,engine._numeric_stat(attacker.object_id,'toughness'))
        self.replay(session,load=True)
        self.damage(session,attacker,blocker)
        self.assertEqual('graveyard',session.state.cards[blocker.object_id].zone)
        self.assertEqual(2,session.state.cards[attacker.object_id].marked_damage)
        engine.move_card(aura.object_id,'graveyard',log=False)
        from quorune.toughness_assignment import current_toughness_assignment
        self.assertFalse(current_toughness_assignment(engine,attacker,power=2,toughness=6))

    def test_actual_lumberknot_comparison_reads_full_current_stats(self):
        session=self.session(292004);engine=session.engine
        source=self.add(engine,'Ancient Lumberknot');body=self.add(engine,'Generic Bound Body')
        equalize=self.add(engine,'Generic Equalize Toughness Comparison',zone='hand')
        from quorune.toughness_assignment import current_toughness_assignment
        self.assertTrue(current_toughness_assignment(engine,body,power=2,toughness=6))
        spell=self.add(engine,'Generic Bound Growth',zone='hand')
        action=self.ready(session,spell,{'G':1});self.checkpoint(session)
        accepted=session.act('pilot:A',{'action_id':action['id'],'targets':[body.ref],'pay':'auto'});self.assertTrue(accepted.ok,accepted.summary);self.resolve(session)
        power=engine._numeric_stat(body.object_id,'power');toughness=engine._numeric_stat(body.object_id,'toughness')
        self.assertEqual((4,8),(power,toughness));self.assertTrue(current_toughness_assignment(engine,body,power=power,toughness=toughness))
        actions=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        offered=next(row for row in actions if row.get('card')==equalize.ref)
        accepted=session.act('pilot:A',{'action_id':offered['id'],'targets':[body.ref],'pay':'auto'});self.assertTrue(accepted.ok,accepted.summary);self.resolve(session)
        power=engine._numeric_stat(body.object_id,'power');toughness=engine._numeric_stat(body.object_id,'toughness')
        self.assertEqual((8,8),(power,toughness));self.assertFalse(current_toughness_assignment(engine,body,power=power,toughness=toughness))
        self.replay(session,load=True)

    def test_actual_bill_food_cost_and_pending_target_rule_replay(self):
        session=self.session(292005);engine=session.engine
        source=self.add(engine,'Bill the Pony',zone='hand')
        body=self.add(engine,'Generic Bound Body')
        action=self.ready(session,source,{'C':3,'W':1});self.checkpoint(session)
        accepted=session.act('pilot:A',{'action_id':action['id'],'pay':'auto'});self.assertTrue(accepted.ok,accepted.summary);self.resolve(session)
        foods=[card for card in session.state.cards.values() if card.object_kind=='token' and card.zone=='battlefield' and 'food' in engine._type_parts(engine._effective_card_data(card)['type_line'])[1]]
        self.assertEqual(2,len(foods))
        activation=next(row for row in session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions'] if row['id'].startswith('activate:'+source.ref+':'))
        response={'action_id':activation['id'],'targets':[body.ref],'cost_objects':[foods[0].ref],'pay':'auto'}
        accepted=session.act('pilot:A',response);self.assertTrue(accepted.ok,accepted.summary)
        self.assertNotEqual('battlefield',session.state.cards[foods[0].object_id].zone)
        self.assertEqual(1,len(session.state.stack))
        resumed=self.replay(session,load=True);self.resolve(resumed)
        from quorune.toughness_assignment_rule import active_resolved_toughness_rule
        restored=resumed.state.cards[body.object_id]
        self.assertTrue(active_resolved_toughness_rule(resumed.state,restored))
        self.replay(resumed,load=True)
        from quorune.continuous_effect_state import create_resolution_continuous_effect,ResolutionEffectSource,expire_end_of_turn_continuous_effects
        from quorune.continuous_effects import ContinuousOperation,Layer
        create_resolution_continuous_effect(resumed.engine,source=ResolutionEffectSource('direct:'+restored.ref,restored.object_id,restored.logical_object_id,restored.ref),
            targets=(restored,),layer=Layer.ABILITY,sublayer='6',operations=(ContinuousOperation('remove_all_abilities'),))
        self.assertTrue(active_resolved_toughness_rule(resumed.state,restored))
        resumed.engine.move_card(restored.object_id,'graveyard',log=False);resumed.engine.move_card(restored.object_id,'battlefield',log=False)
        self.assertFalse(active_resolved_toughness_rule(resumed.state,restored))
        expire_end_of_turn_continuous_effects(resumed.state);self.assertEqual([],resumed.state.continuous_effects)

    def test_actual_bulwark_couples_haste_defender_permission_and_toughness_rule(self):
        session=self.session(292006);engine=session.engine
        source=self.add(engine,'Walking Bulwark');target=self.add(engine,'Steel Wall')
        ordinary=self.add(engine,'Generic Bound Body')
        action=self.ready(session,source,{'C':2});self.checkpoint(session)
        self.assertIn(target.ref,action['target_schema']['legal_refs']);self.assertNotIn(ordinary.ref,action['target_schema']['legal_refs'])
        before=authoritative_state_hash(session.state)
        wrong=session.act('pilot:A',{'action_id':action['id'],'targets':[ordinary.ref],'pay':'auto'})
        self.assertFalse(wrong.ok);self.assertEqual(before,authoritative_state_hash(session.state))
        accepted=session.act('pilot:A',{'action_id':action['id'],'targets':[target.ref],'pay':'auto'});self.assertTrue(accepted.ok,accepted.summary)
        self.resolve(session)
        data=engine._effective_card_data(target)
        self.assertIn('Haste',data['keywords']);self.assertIn('Defender',data['keywords'])
        from quorune.defender import defender_prohibits_attack
        self.assertFalse(defender_prohibits_attack(data))
        self.assertIsNone(engine._attack_declaration_error(target,'A'))
        from quorune.toughness_assignment_rule import active_resolved_toughness_rule
        self.assertTrue(active_resolved_toughness_rule(session.state,target))
        self.replay(session,load=True)
        from quorune.continuous_effect_state import expire_end_of_turn_continuous_effects
        expire_end_of_turn_continuous_effects(session.state)
        self.assertTrue(defender_prohibits_attack(engine._effective_card_data(target)))
        self.assertFalse(active_resolved_toughness_rule(session.state,target))

    def test_external_rule_survives_recipient_ability_removal_but_source_rule_does_not(self):
        from quorune.toughness_assignment import current_toughness_assignment
        for index,name in enumerate(('High Alert','Doran, the Siege Tower')):
            session=self.session(292007+index);engine=session.engine
            source=self.add(engine,name);body=self.add(engine,'Generic Bound Body')
            humility=self.add(engine,'Humility',zone='hand')
            action=self.ready(session,humility,{'C':2,'W':2});self.checkpoint(session)
            accepted=session.act('pilot:A',{'action_id':action['id'],'pay':'auto'});self.assertTrue(accepted.ok,accepted.summary);self.resolve(session)
            self.assertEqual((1,1),(engine._numeric_stat(body.object_id,'power'),engine._numeric_stat(body.object_id,'toughness')))
            self.assertEqual(name=='High Alert',current_toughness_assignment(engine,body,power=1,toughness=1))
            self.replay(session,load=True)

    def test_attached_rule_survives_recipient_ability_removal_and_current_comparison_changes(self):
        session=self.session(292009);engine=session.engine
        body=self.add(engine,'Generic Bound Body')
        source=self.add(engine,'Bark of Doran');source.attached_to=body.object_id
        body.attachments.append(source.object_id)
        from quorune.toughness_assignment import current_toughness_assignment
        self.assertEqual((2,7),(engine._numeric_stat(body.object_id,'power'),engine._numeric_stat(body.object_id,'toughness')))
        self.assertTrue(current_toughness_assignment(engine,body,power=2,toughness=7))
        spell=self.add(engine,'Deep Freeze',zone='hand')
        action=self.ready(session,spell,{'C':2,'U':1});self.checkpoint(session)
        accepted=session.act('pilot:A',{'action_id':action['id'],'targets':[body.ref],'pay':'auto'});self.assertTrue(accepted.ok,accepted.summary);self.resolve(session)
        power=engine._numeric_stat(body.object_id,'power');toughness=engine._numeric_stat(body.object_id,'toughness')
        self.assertTrue(current_toughness_assignment(engine,body,power=power,toughness=toughness))
        self.replay(session,load=True)

    def test_actual_solid_footing_reads_current_vigilance_and_stale_assignment_rolls_back(self):
        session=self.session(292010);engine=session.engine
        body=self.add(engine,'Generic Bound Body');enemy=self.add(engine,'Generic Bound Body',seat='B',ref='footing-blocker')
        aura=self.add(engine,'Solid Footing',zone='hand')
        action=self.ready(session,aura,{'W':1});self.checkpoint(session)
        accepted=session.act('pilot:A',{'action_id':action['id'],'targets':[body.ref],'pay':'auto'});self.assertTrue(accepted.ok,accepted.summary);self.resolve(session)
        from quorune.toughness_assignment import current_toughness_assignment
        self.assertTrue(current_toughness_assignment(engine,body,power=3,toughness=7))
        self.replay(session,load=True)
        other=self.add(engine,'Generic Bound Body',seat='B',ref='footing-other-blocker')
        from quorune.model import CombatState
        engine.permissions.invalidate_current();engine.state.pending_decision=None;engine.state.priority_player=None
        engine.state.phase_index=7;engine.state.phase='combat';engine.state.step='combat_damage'
        body.attacking='B';enemy.blocking=body.object_id;other.blocking=body.object_id
        engine.state.combat=CombatState(attackers_declared=True,blockers_declared=True,had_attacking_creature=True,
            attackers={body.object_id:'B'},defending_players=['B'],blockers={body.object_id:[enemy.object_id,other.object_id]})
        engine._begin_combat_damage()
        from quorune.continuous_effect_state import create_resolution_continuous_effect,ResolutionEffectSource
        from quorune.continuous_effects import ContinuousOperation,Layer
        create_resolution_continuous_effect(engine,source=ResolutionEffectSource('direct:'+body.ref,body.object_id,body.logical_object_id,body.ref),
            targets=(body,),layer=Layer.ABILITY,sublayer='6',operations=(ContinuousOperation('remove_ability','Vigilance'),))
        self.assertFalse(current_toughness_assignment(engine,body,power=3,toughness=7))
        before=authoritative_state_hash(session.state)
        rejected=session.act('pilot:A',{'a':'dmg','assignments':[{'source':body.ref,'target':enemy.ref,'amount':7}]})
        self.assertFalse(rejected.ok);self.assertEqual(before,authoritative_state_hash(session.state))

    def test_actual_doran_does_not_change_noncombat_power_damage(self):
        session=self.session(292011);engine=session.engine
        self.add(engine,'Doran, the Siege Tower')
        source=self.add(engine,'Generic Power Damage Under Doran');target=self.add(engine,'Generic Bound Body',seat='B')
        action=self.ready(session,source,{});self.checkpoint(session)
        accepted=session.act('pilot:A',{'action_id':action['id'],'targets':[target.ref],'pay':'auto'});self.assertTrue(accepted.ok,accepted.summary);self.resolve(session)
        self.assertEqual(2,session.state.cards[target.object_id].marked_damage)
        self.assertEqual(2,engine._numeric_stat(source.object_id,'power'));self.assertEqual(6,engine._numeric_stat(source.object_id,'toughness'))
        self.replay(session,load=True)

    def test_toughness_trample_uses_current_lethal_threshold_and_spill(self):
        session=self.session(292012);engine=session.engine
        self.add(engine,'High Alert');attacker=self.add(engine,'Generic Toughness Trample')
        blocker=self.add(engine,'Generic Bound Body',seat='B')
        self.damage(session,attacker,blocker);self.checkpoint(session)
        before=authoritative_state_hash(session.state)
        invalid=session.act('pilot:A',{'a':'dmg','assignments':[{'source':attacker.ref,'target':blocker.ref,'amount':1},{'source':attacker.ref,'target':'B','amount':6}]})
        self.assertFalse(invalid.ok);self.assertEqual(before,authoritative_state_hash(session.state))
        accepted=session.act('pilot:A',{'a':'dmg','assignments':[{'source':attacker.ref,'target':blocker.ref,'amount':6},{'source':attacker.ref,'target':'B','amount':1}]})
        self.assertTrue(accepted.ok,accepted.summary);self.assertEqual(39,session.state.players['B'].life)
        self.assertEqual(2,session.state.cards[attacker.object_id].marked_damage)
        self.replay(session,load=True)

    def test_later_damage_step_recomputes_toughness_after_current_modifier(self):
        session=self.session(292013);engine=session.engine
        self.add(engine,'High Alert');attacker=self.add(engine,'Generic Toughness Double Strike')
        blocker=self.add(engine,'Generic Bound Body',seat='B');blocker.counters['+1/+1']=5
        self.damage(session,attacker,blocker)
        self.assertEqual(7,session.state.cards[blocker.object_id].marked_damage)
        self.assertEqual(0,session.state.cards[attacker.object_id].marked_damage)
        engine._grant_priority('A');engine.pump()
        from quorune.continuous_effect_state import create_resolution_continuous_effect,ResolutionEffectSource
        from quorune.continuous_effects import ContinuousOperation,Layer
        create_resolution_continuous_effect(engine,source=ResolutionEffectSource('direct:'+attacker.ref,attacker.object_id,attacker.logical_object_id,attacker.ref),
            targets=(attacker,),layer=Layer.POWER_TOUGHNESS,sublayer='7c',operations=(ContinuousOperation('modify_power_toughness',[0,2]),))
        for _ in range(16):
            if session.state.combat.damage_step_index==1:break
            result=session.act(session.pending_principals()[0],{'action_id':'pass'});self.assertTrue(result.ok,result.summary)
        self.assertEqual(1,session.state.combat.damage_step_index)
        damage=[event for event in session.state.events if event.code=='combat.damage'][-1]
        assignments=damage.details['assignments']
        self.assertTrue(any(row['source']==attacker.ref and row['amount']==9 for row in assignments))

    def test_intrinsic_and_source_controller_turn_rules_use_current_applicability(self):
        session=self.session(292014);engine=session.engine
        intrinsic=self.add(engine,'Generic Intrinsic Toughness Assignment')
        source=self.add(engine,'Generic Turn Toughness Assignment',seat='B')
        body=self.add(engine,'Generic Bound Body')
        from quorune.toughness_assignment import current_toughness_assignment
        engine.state.active_player='A'
        self.assertTrue(current_toughness_assignment(engine,intrinsic,power=2,toughness=6))
        self.assertFalse(current_toughness_assignment(engine,body,power=2,toughness=6))
        engine.state.active_player='B'
        self.assertTrue(current_toughness_assignment(engine,body,power=2,toughness=6))
        engine.change_control(source.object_id,'A',reason='Toughness turn-rule controller diagnostic')
        self.assertFalse(current_toughness_assignment(engine,body,power=2,toughness=6))
        engine.state.active_player='A'
        self.assertTrue(current_toughness_assignment(engine,body,power=2,toughness=6))
        engine.move_card(source.object_id,'graveyard',log=False)
        self.assertFalse(current_toughness_assignment(engine,body,power=2,toughness=6))

    def test_malformed_temporary_rule_rejects_before_journal_mutation(self):
        session=self.session(292015);engine=session.engine
        body=self.add(engine,'Generic Bound Body')
        from quorune.rules.toughness_assignment_effect import apply_temporary_toughness_assignment
        from quorune.errors import GameRuleError
        effect={'op':'apply_source_characteristics_until_end_of_turn','schema_version':5,'permission':'use_toughness','card':body.ref}
        before=authoritative_state_hash(session.state)
        for changed in ({'schema_version':True},{'permission':'use_mana_value'},{'card':False},{'extra':0}):
            with self.assertRaises(GameRuleError):apply_temporary_toughness_assignment(engine,{**effect,**changed},actor='A',reason='malformed witness')
            self.assertEqual(before,authoritative_state_hash(session.state))

    def test_combat_query_collects_current_source_rules_once_for_all_participants(self):
        session=self.session(292016);engine=session.engine
        self.add(engine,'Doran, the Siege Tower')
        attacker=self.add(engine,'Generic Bound Body');blocker=self.add(engine,'Generic Bound Body',seat='B')
        from quorune.model import CombatState
        attacker.attacking='B';blocker.blocking=attacker.object_id
        engine.state.active_player='A';engine.state.phase='combat';engine.state.step='combat_damage'
        engine.state.combat=CombatState(attackers_declared=True,blockers_declared=True,
            attackers={attacker.object_id:'B'},defending_players=['B'],blockers={attacker.object_id:[blocker.object_id]})
        from quorune.combat_damage_engine_adapter import EngineCombatDamageQuery
        import quorune.combat_damage_engine_adapter as owner
        with patch.object(owner,'current_toughness_rule_sources',wraps=owner.current_toughness_rule_sources) as collected:
            query=EngineCombatDamageQuery(engine)
            self.assertTrue(query.participant(attacker.object_id).assigns_using_toughness)
            self.assertTrue(query.participant(blocker.object_id).assigns_using_toughness)
            self.assertEqual(1,collected.call_count)


if __name__=='__main__':unittest.main()
