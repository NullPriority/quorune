from __future__ import annotations

import unittest
import json
from dataclasses import replace
from pathlib import Path
import tempfile
from common import ROOT
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.rules.capabilities import load_default_capability_registry
from quorune.record import authoritative_state_hash
from scripts.build_test_database import build_fixture_database
import test_bound_effect_programs as witnesses
from unittest.mock import patch
from quorune.ability_fragments import ability_fragment_from_dict, ability_fragment_to_dict
from quorune.defender import defender_prohibits_attack
from quorune.defender_permission import DefenderAttackPermission
from quorune.compiler.defender_permission_templates import static_defender_permission_handler
from quorune.compiler.defender_permission_templates import temporary_defender_permission_template
from quorune.semantic_runtime.conditional_continuous import ConditionalDefenderPermissionHandler
from quorune.semantic_runtime.context import SemanticNodeError


class DefenderPermissionCompilerTests(unittest.TestCase):
    def test_static_permission_queries_and_public_conditions_preserve_only_defender_exception(self):
        for line in (
            "This creature can attack as though it didn't have defender.",
            "Wall creatures can attack as though they didn't have defender.",
            "Creatures you control can attack as though they didn't have defender.",
            "Modified creatures you control can attack as though they didn't have defender.",
            "Enchanted creature can attack as though it didn't have defender.",
            "As long as this creature has a +1/+1 counter on it, it can attack as though it didn't have defender.",
            "Metalcraft — As long as you control three or more artifacts, this creature gets +2/+2 and can attack as though it didn't have defender.",
            "As long as you've cast an instant or sorcery spell this turn, this creature can attack as though it didn't have defender.",
            "As long as an artifact entered the battlefield under your control this turn, this creature can attack as though it didn't have defender.",
        ):
            with self.subTest(line=line):
                result=static_defender_permission_handler(line,source_name='Generic Defender')
                self.assertIsNotNone(result)
                self.assertIn('combat.attack.defender_permission',result[2])
                descriptor=result[1]
                raw=descriptor.get('fragment') or descriptor['modifier']['add_ability_fragments'][0]
                self.assertEqual(DefenderAttackPermission(),ability_fragment_from_dict(raw))
                self.assertNotIn('combat.attack.vigilance',result[2])
                if 'source_condition' in descriptor:ConditionalDefenderPermissionHandler().validate(descriptor)
        query=static_defender_permission_handler("Wall creatures can attack as though they didn't have defender.",source_name='Generic Source')[1]
        self.assertEqual(['wall'],query['condition']['predicate']['subtypes_all'])
        self.assertEqual(['creature'],query['condition']['predicate']['types_all'])

    def test_permission_model_schema_and_unknown_grammar_fail_closed(self):
        raw=ability_fragment_to_dict(DefenderAttackPermission())
        self.assertEqual(DefenderAttackPermission(),ability_fragment_from_dict(raw))
        for value in ({'schema_version':True},{'schema_version':2},{'schema_version':1,'extra':0}):
            with self.assertRaises(ValueError):DefenderAttackPermission.from_dict(value)
        for line in (
            "This creature can attack players who attacked you as though it didn't have defender.",
            "This creature can attack this turn as though it didn't have defender.",
            "Creatures with a sticker can attack as though they didn't have defender.",
            "Creatures can attack as though they didn't have defender. You win the game.",
            "This creature may assign its combat damage as though it weren't blocked.",
        ):
            self.assertIsNone(static_defender_permission_handler(line,source_name='Generic Source'),line)
        result=static_defender_permission_handler("As long as this creature has a +1/+1 counter on it, it can attack as though it didn't have defender.",source_name='Generic Source')
        descriptor=result[1];modifier=dict(descriptor['modifier'])
        modifier['add_ability_fragments']=[{'kind':'activation_prohibition','value':{'schema_version':1,'kind':'all'}}]
        with self.assertRaises((SemanticNodeError,ValueError)):
            ConditionalDefenderPermissionHandler().validate({**descriptor,'modifier':modifier})

    def test_permission_keeps_keyword_and_shared_verdict_mutant_is_killed(self):
        data={'type_line':'Creature — Wall','keywords':['Defender'],
            'ability_fragments':[ability_fragment_to_dict(DefenderAttackPermission())]}
        self.assertFalse(defender_prohibits_attack(data))
        self.assertEqual(['Defender'],data['keywords'])
        self.assertTrue(defender_prohibits_attack({**data,'ability_fragments':[]}))
        import quorune.defender as owner
        with patch.object(owner,'canonical_ability_fragments',return_value=()):
            with self.assertRaises(AssertionError):self.assertFalse(defender_prohibits_attack(data))

    def test_temporary_permission_locks_selection_and_rejects_linked_riders(self):
        from quorune.rules.defender_permission_effect import temporary_defender_permission_capabilities
        for line in (
            "This creature can attack this turn as though it didn't have defender.",
            "Target creature can attack this turn as though it didn't have defender.",
            "This creature gets +4/-4 until end of turn and can attack this turn as though it didn't have defender.",
            "Creatures you control can attack this turn as though they didn't have defender.",
        ):
            compiled=temporary_defender_permission_template(line,source_name='Generic Source',source_is_permanent=True,source_card_types=('creature',))
            self.assertIsNotNone(compiled,line)
            required=temporary_defender_permission_capabilities(effects=compiled[1],target_schema=compiled[2],mechanic_ids=compiled[3])
            self.assertIn('combat.attack.defender_permission.temporary',required,line)
            for changed in ({'schema_version':True},{'permission':'ignore_all_restrictions'},{'keywords':[[]]},{'extra':0}):
                self.assertEqual((),temporary_defender_permission_capabilities(effects=({**compiled[1][0],**changed},),target_schema=compiled[2],mechanic_ids=compiled[3]))
        for line in (
            "This creature can attack this turn as though it didn't have defender. Exile it at the beginning of the next end step.",
            "Target creature can attack this turn as though it didn't have defender and assigns combat damage equal to its toughness.",
        ):
            self.assertIsNone(temporary_defender_permission_template(line,source_name='Generic Source',source_is_permanent=True,source_card_types=('creature',)))

    def test_permission_dependencies_and_forged_effect_shape_fail_closed(self):
        from quorune.rules.capabilities import CapabilityRegistry, capability_dependencies_for_node
        from quorune.oracle_ir import compile_oracle_card
        from test_qualified_zone_event_queries import query_record
        value=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        for row in value['capabilities']:
            if row['id']=='combat.attack.defender':row.update(status='blocked',blockers=['independent owner unavailable'])
        blocked=CapabilityRegistry(value)
        for text,types in (
            ("Wall creatures can attack as though they didn't have defender.",'Enchantment'),
            ("{2}{G}: This creature can attack this turn as though it didn't have defender.",'Creature'),
        ):
            ir=compile_oracle_card(replace(query_record(text),type_line=types),capability_registry=blocked,capability_profile='commander_review')
            self.assertNotEqual('exact',ir.status)
        compiled=temporary_defender_permission_template("This creature can attack this turn as though it didn't have defender.",
            source_name='Generic Source',source_is_permanent=True,source_card_types=('creature',))
        forged={**compiled[1][0],'permission':'ignore_all_restrictions'}
        supplied=capability_dependencies_for_node(effects=(forged,),target_schema=None,mechanic_ids=compiled[3])
        self.assertNotIn('combat.attack.defender_permission.temporary',supplied)


class DefenderPermissionRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'defender.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',ROOT/'tests/fixtures/defender-permission-cards.json',ROOT/'tests/fixtures/static-characteristic-setting-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Defender permission witness',[DeckEntry('Generic Bound Commander',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temporary.cleanup()
    session=witnesses.BoundEffectProgramRuntimeTests.session
    add=witnesses.BoundEffectProgramRuntimeTests.add
    ready=witnesses.BoundEffectProgramRuntimeTests.ready
    checkpoint=witnesses.BoundEffectProgramRuntimeTests.checkpoint
    resolve=witnesses.BoundEffectProgramRuntimeTests.resolve
    replay=witnesses.BoundEffectProgramRuntimeTests.replay

    def attackers(self,session):
        engine=session.engine;engine.permissions.invalidate_current()
        engine.state.pending_decision=None;engine.state.priority_player=None
        engine.state.started=True;engine.state.active_player='A'
        engine.state.phase_index=5;engine.state.phase='combat';engine.state.step='declare_attackers'
        engine._issue_attackers()
        return {row['id'] for row in engine.state.pending_decision.payload_by_actor['A']['candidates']}

    def test_actual_rolling_stones_uses_shared_offer_and_accepted_declaration(self):
        session=self.session(288001);engine=session.engine
        source=self.add(engine,'Rolling Stones')
        wall=self.add(engine,'Steel Wall');enemy=self.add(engine,'Steel Wall',seat='B',ref='enemy-wall')
        wall.acquired_control_turn_count=-1;enemy.acquired_control_turn_count=-1
        offered=self.attackers(session);self.checkpoint(session)
        self.assertIn(wall.ref,offered);self.assertNotIn(enemy.ref,offered)
        self.assertIn('Defender',engine._effective_card_data(wall)['keywords'])
        before=authoritative_state_hash(session.state)
        wrong=session.act('pilot:B',{'a':'attack','atk':{wall.ref:'C'}})
        self.assertFalse(wrong.ok);self.assertEqual(before,authoritative_state_hash(session.state))
        self.assertIsNone(session.packet('pilot:B',full=True)['decision'])
        accepted=session.act('pilot:A',{'a':'attack','atk':{wall.ref:'C'}})
        self.assertTrue(accepted.ok,accepted.summary);self.assertEqual('C',session.state.cards[wall.object_id].attacking)
        self.replay(session,load=True)

    def test_actual_counter_condition_loss_removes_permission_and_rejects_attack(self):
        session=self.session(288002);engine=session.engine
        ordinary=self.add(engine,'Generic Bound Body');ordinary.acquired_control_turn_count=-1
        source=self.add(engine,'Skyclave Sentinel');source.acquired_control_turn_count=-1
        self.assertNotIn(source.ref,self.attackers(session))
        source.counters['+1/+1']=1
        self.assertIn(source.ref,self.attackers(session))
        source.counters.clear()
        before=authoritative_state_hash(session.state)
        result=session.act('pilot:A',{'a':'attack','atk':{source.ref:'B'}})
        self.assertFalse(result.ok);self.assertEqual(before,authoritative_state_hash(session.state))

    def test_permission_does_not_override_tap_sickness_or_other_attack_restrictions(self):
        session=self.session(288003);engine=session.engine
        ordinary=self.add(engine,'Generic Bound Body');ordinary.acquired_control_turn_count=-1
        self.add(engine,'Rolling Stones');wall=self.add(engine,'Steel Wall');wall.acquired_control_turn_count=-1
        wall.tapped=True;self.assertNotIn(wall.ref,self.attackers(session))
        wall.tapped=False;wall.acquired_control_turn_count=engine.state.players['A'].turns_begun
        self.assertNotIn(wall.ref,self.attackers(session))
        wall.acquired_control_turn_count=-1
        self.assertIn(wall.ref,self.attackers(session))
        forbidden=self.add(engine,'Generic Forbidden Wall');forbidden.acquired_control_turn_count=-1
        offered=self.attackers(session)
        self.assertIn(wall.ref,offered);self.assertNotIn(forbidden.ref,offered)
        before=authoritative_state_hash(session.state)
        rejected=session.act('pilot:A',{'a':'attack','atk':{forbidden.ref:'B'}})
        self.assertFalse(rejected.ok);self.assertEqual(before,authoritative_state_hash(session.state))

    def test_actual_temporary_activation_expires_and_does_not_follow_new_incarnation(self):
        session=self.session(288004);engine=session.engine
        source=self.add(engine,'Krotiq Nestguard');source.acquired_control_turn_count=-1
        action=self.ready(session,source,{'C':2,'G':1});self.checkpoint(session)
        accepted=session.act('pilot:A',{'action_id':action['id'],'pay':'auto'});self.assertTrue(accepted.ok,accepted.summary)
        self.resolve(session)
        self.assertFalse(defender_prohibits_attack(engine._effective_card_data(source)))
        self.assertIn('Defender',engine._effective_card_data(source)['keywords'])
        resumed=self.replay(session,load=True)
        restored=resumed.state.cards[source.object_id]
        self.assertIn(restored.ref,self.attackers(resumed))
        from quorune.continuous_effect_state import expire_end_of_turn_continuous_effects
        expire_end_of_turn_continuous_effects(resumed.state)
        ordinary=self.add(resumed.engine,'Generic Bound Body');ordinary.acquired_control_turn_count=-1
        self.assertNotIn(restored.ref,self.attackers(resumed))
        engine.move_card(source.object_id,'graveyard',log=False);engine.move_card(source.object_id,'battlefield',log=False)
        source.acquired_control_turn_count=-1
        self.assertTrue(defender_prohibits_attack(engine._effective_card_data(source)))

    def test_temporary_permission_journal_mutant_is_killed(self):
        from quorune.rules import defender_permission_effect as owner
        with patch.object(owner,'create_resolution_continuous_effect_components',return_value=()):
            with self.assertRaises(AssertionError):self.test_actual_temporary_activation_expires_and_does_not_follow_new_incarnation()

    def test_pending_temporary_activation_save_load_resolves_same_source_once(self):
        session=self.session(288015);engine=session.engine
        source=self.add(engine,'Krotiq Nestguard');source.acquired_control_turn_count=-1
        action=self.ready(session,source,{'C':2,'G':1});self.checkpoint(session)
        accepted=session.act('pilot:A',{'action_id':action['id'],'pay':'auto'});self.assertTrue(accepted.ok,accepted.summary)
        self.assertEqual(1,len(session.state.stack))
        self.assertTrue(defender_prohibits_attack(engine._effective_card_data(source)))
        resumed=self.replay(session,load=True);self.resolve(resumed)
        self.assertFalse(defender_prohibits_attack(resumed.engine._effective_card_data(resumed.state.cards[source.object_id])))
        self.assertEqual(1,len(resumed.state.continuous_effects))
        self.replay(resumed,load=True)

    def test_pending_temporary_activation_does_not_grant_returned_source(self):
        session=self.session(288016);engine=session.engine
        source=self.add(engine,'Krotiq Nestguard');source.acquired_control_turn_count=-1
        action=self.ready(session,source,{'C':2,'G':1})
        accepted=session.act('pilot:A',{'action_id':action['id'],'pay':'auto'});self.assertTrue(accepted.ok,accepted.summary)
        old=source.logical_object_id
        engine.move_card(source.object_id,'graveyard',log=False);engine.move_card(source.object_id,'battlefield',log=False)
        self.assertNotEqual(old,source.logical_object_id)
        self.resolve(session)
        self.assertTrue(defender_prohibits_attack(engine._effective_card_data(source)))
        self.assertEqual([],session.state.continuous_effects)

    def test_actual_coupled_power_toughness_and_permission_share_timestamp_and_expiry(self):
        session=self.session(288005);engine=session.engine
        source=self.add(engine,'Wall of Wonder');source.acquired_control_turn_count=-1
        action=self.ready(session,source,{'C':2,'U':2});self.checkpoint(session)
        accepted=session.act('pilot:A',{'action_id':action['id'],'pay':'auto'});self.assertTrue(accepted.ok,accepted.summary)
        self.resolve(session)
        self.assertEqual(5,engine._numeric_stat(source.object_id,'power'))
        self.assertEqual(1,engine._numeric_stat(source.object_id,'toughness'))
        self.assertFalse(defender_prohibits_attack(engine._effective_card_data(source)))
        effects=[row for row in session.state.continuous_effects if any(identity.object_id==source.object_id for identity in row.locked_objects)]
        self.assertEqual(2,len(effects));self.assertEqual(1,len({row.timestamp for row in effects}))
        self.replay(session,load=True)
        from quorune.continuous_effect_state import expire_end_of_turn_continuous_effects
        expire_end_of_turn_continuous_effects(session.state)
        self.assertEqual(1,engine._numeric_stat(source.object_id,'power'))
        self.assertEqual(5,engine._numeric_stat(source.object_id,'toughness'))
        self.assertTrue(defender_prohibits_attack(engine._effective_card_data(source)))

    def test_actual_static_source_and_artifact_condition_loss_remove_only_permission(self):
        session=self.session(288006);engine=session.engine
        source=self.add(engine,'Rolling Stones');wall=self.add(engine,'Steel Wall');wall.acquired_control_turn_count=-1
        ordinary=self.add(engine,'Generic Bound Body');ordinary.acquired_control_turn_count=-1
        self.assertIn(wall.ref,self.attackers(session))
        engine.move_card(source.object_id,'graveyard',log=False)
        self.assertNotIn(wall.ref,self.attackers(session))
        serpent=self.add(engine,'Spire Serpent');serpent.acquired_control_turn_count=-1
        for index in range(2):self.add(engine,'Steel Wall',ref='metal-wall-'+str(index))
        self.assertEqual(5,engine._numeric_stat(serpent.object_id,'power'))
        self.assertIn(serpent.ref,self.attackers(session))
        engine.move_card(wall.object_id,'graveyard',log=False)
        self.assertEqual(3,engine._numeric_stat(serpent.object_id,'power'))
        self.assertNotIn(serpent.ref,self.attackers(session))

    def test_temporary_set_locks_current_objects_and_survives_source_departure(self):
        session=self.session(288007);engine=session.engine
        source=self.add(engine,'Generic Mass Defender Permission')
        wall=self.add(engine,'Steel Wall');wall.acquired_control_turn_count=-1
        enemy=self.add(engine,'Steel Wall',seat='B',ref='opponent-wall')
        action=self.ready(session,source,{'C':1});self.checkpoint(session)
        accepted=session.act('pilot:A',{'action_id':action['id'],'pay':'auto'});self.assertTrue(accepted.ok,accepted.summary)
        self.resolve(session)
        self.assertFalse(defender_prohibits_attack(engine._effective_card_data(wall)))
        self.assertTrue(defender_prohibits_attack(engine._effective_card_data(enemy)))
        self.replay(session,load=True)
        late=self.add(engine,'Steel Wall',ref='later-wall');late.acquired_control_turn_count=-1
        engine.move_card(source.object_id,'graveyard',log=False)
        offered=self.attackers(session)
        self.assertIn(wall.ref,offered);self.assertNotIn(late.ref,offered)

    def test_actual_aura_ability_removal_disables_static_permission_source(self):
        session=self.session(288008);engine=session.engine
        sentinel=self.add(engine,'Skyclave Sentinel');sentinel.acquired_control_turn_count=-1
        sentinel.counters['+1/+1']=1
        ordinary=self.add(engine,'Generic Bound Body');ordinary.acquired_control_turn_count=-1
        aura=self.add(engine,'Deep Freeze',zone='hand')
        action=self.ready(session,aura,{'C':2,'U':1});self.checkpoint(session)
        self.assertFalse(defender_prohibits_attack(engine._effective_card_data(sentinel)))
        accepted=session.act('pilot:A',{'action_id':action['id'],'pay':'auto','targets':[sentinel.ref]})
        self.assertTrue(accepted.ok,accepted.summary);self.resolve(session)
        data=engine._effective_card_data(sentinel)
        self.assertFalse(any(isinstance(ability_fragment_from_dict(raw),DefenderAttackPermission) for raw in data['ability_fragments']))
        self.replay(session,load=True)

    def test_actual_instant_cast_trigger_grants_fixed_stats_and_defender_permission(self):
        session=self.session(288009);engine=session.engine
        source=self.add(engine,'Nivix Cyclops');source.acquired_control_turn_count=-1
        spell=self.add(engine,'Generic Bound Growth',zone='hand')
        target=self.add(engine,'Generic Bound Body')
        action=self.ready(session,spell,{'G':1});self.checkpoint(session)
        accepted=session.act('pilot:A',{'action_id':action['id'],'pay':'auto','targets':[target.ref]})
        self.assertTrue(accepted.ok,accepted.summary);self.resolve(session)
        self.assertEqual(4,engine._numeric_stat(source.object_id,'power'))
        self.assertFalse(defender_prohibits_attack(engine._effective_card_data(source)))
        self.replay(session,load=True)

    def test_actual_cast_and_artifact_entry_facts_change_static_permission(self):
        session=self.session(288012);engine=session.engine
        cast_source=self.add(engine,'Piston-Fist Cyclops');cast_source.acquired_control_turn_count=-1
        entry_source=self.add(engine,'Mechan Shieldmate');entry_source.acquired_control_turn_count=-1
        artifact=self.add(engine,'Steel Wall',zone='hand')
        spell=self.add(engine,'Generic Bound Growth',zone='hand')
        target=self.add(engine,'Generic Bound Body')
        self.assertTrue(defender_prohibits_attack(engine._effective_card_data(cast_source)))
        self.assertTrue(defender_prohibits_attack(engine._effective_card_data(entry_source)))
        action=self.ready(session,spell,{'G':1,'C':1});self.checkpoint(session)
        accepted=session.act('pilot:A',{'action_id':action['id'],'pay':'auto','targets':[target.ref]})
        self.assertTrue(accepted.ok,accepted.summary);self.resolve(session)
        self.assertFalse(defender_prohibits_attack(engine._effective_card_data(cast_source)))
        self.assertTrue(defender_prohibits_attack(engine._effective_card_data(entry_source)))
        # Use a real cast and canonical entry to establish the turn fact.
        actions=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        action=next(row for row in actions if row.get('card')==artifact.ref)
        accepted=session.act('pilot:A',{'action_id':action['id'],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary);self.resolve(session)
        self.assertFalse(defender_prohibits_attack(engine._effective_card_data(entry_source)))
        self.replay(session,load=True)

    def test_actual_once_per_turn_activation_does_not_erase_usage_limit(self):
        session=self.session(288010);engine=session.engine
        source=self.add(engine,'Mobile Fort');source.acquired_control_turn_count=-1
        action=self.ready(session,source,{'C':6});self.checkpoint(session)
        accepted=session.act('pilot:A',{'action_id':action['id'],'pay':'auto'});self.assertTrue(accepted.ok,accepted.summary)
        self.resolve(session)
        self.assertFalse(defender_prohibits_attack(engine._effective_card_data(source)))
        actions=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        self.assertFalse(any(row['id']==action['id'] for row in actions))
        before=authoritative_state_hash(session.state)
        rejected=session.act('pilot:A',{'action_id':action['id'],'pay':'auto'})
        self.assertFalse(rejected.ok);self.assertEqual(before,authoritative_state_hash(session.state))
        self.replay(session,load=True)

    def test_malformed_temporary_permission_rolls_back_before_journal_mutation(self):
        session=self.session(288011);engine=session.engine
        source=self.add(engine,'Krotiq Nestguard')
        from quorune.rules.defender_permission_effect import apply_temporary_defender_permission
        from quorune.errors import GameRuleError
        base={'op':'apply_source_characteristics_until_end_of_turn','schema_version':3,'permission':'ignore_defender',
            'card':source.ref,'power':0,'toughness':0,'keywords':[]}
        before=authoritative_state_hash(session.state)
        for changed in ({'schema_version':True},{'permission':'ignore_all_restrictions'},{'keywords':[[]]},{'extra':0}):
            with self.assertRaises(GameRuleError):apply_temporary_defender_permission(engine,{**base,**changed},actor='A',reason='malformed witness')
            self.assertEqual(before,authoritative_state_hash(session.state))

    def test_targeted_temporary_permission_offers_legal_creatures_and_replays(self):
        session=self.session(288013);engine=session.engine
        spell=self.add(engine,'Generic Target Defender Permission',zone='hand')
        wall=self.add(engine,'Steel Wall');wall.acquired_control_turn_count=-1
        other=self.add(engine,'Steel Wall',ref='other-wall');other.acquired_control_turn_count=-1
        land=self.add(engine,'Generic Bound Plains')
        action=self.ready(session,spell,{'C':1});self.checkpoint(session)
        self.assertIn(wall.ref,action['target_schema']['legal_refs'])
        self.assertNotIn(land.ref,action['target_schema']['legal_refs'])
        before=authoritative_state_hash(session.state)
        rejected=session.act('pilot:A',{'action_id':action['id'],'pay':'auto','targets':[land.ref]})
        self.assertFalse(rejected.ok);self.assertEqual(before,authoritative_state_hash(session.state))
        accepted=session.act('pilot:A',{'action_id':action['id'],'pay':'auto','targets':[wall.ref]})
        self.assertTrue(accepted.ok,accepted.summary);self.resolve(session)
        self.assertFalse(defender_prohibits_attack(engine._effective_card_data(wall)))
        self.assertTrue(defender_prohibits_attack(engine._effective_card_data(other)))
        self.replay(session,load=True)

    def test_attached_permission_follows_current_attachment_and_disappears_with_aura(self):
        session=self.session(288014);engine=session.engine
        aura=self.add(engine,'Generic Attached Defender Permission',zone='hand')
        wall=self.add(engine,'Steel Wall');wall.acquired_control_turn_count=-1
        other=self.add(engine,'Steel Wall',ref='unenchanted-wall');other.acquired_control_turn_count=-1
        action=self.ready(session,aura,{'W':1});self.checkpoint(session)
        accepted=session.act('pilot:A',{'action_id':action['id'],'pay':'auto','targets':[wall.ref]})
        self.assertTrue(accepted.ok,accepted.summary);self.resolve(session)
        self.assertFalse(defender_prohibits_attack(engine._effective_card_data(wall)))
        self.assertTrue(defender_prohibits_attack(engine._effective_card_data(other)))
        self.replay(session,load=True)
        engine.move_card(aura.object_id,'graveyard',log=False)
        self.assertTrue(defender_prohibits_attack(engine._effective_card_data(wall)))


if __name__=='__main__':unittest.main()
