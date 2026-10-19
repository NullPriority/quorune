from __future__ import annotations

import json,unittest
from dataclasses import replace
from common import ROOT
from quorune.token_copy_recipes import TokenCopyExceptionSpec,apply_copy_exception
from quorune.entry_copy import EntryCopySpec
from quorune.compiler.entry_copy_templates import entry_copy_spec


class EntryCopyValueModelTests(unittest.TestCase):
    def test_legacy_copy_exception_bytes_and_extended_value_shapes_are_closed(self):
        old=TokenCopyExceptionSpec().to_dict()
        self.assertNotIn('name',old);self.assertNotIn('card_types',old);self.assertNotIn('retain_source_stats',old)
        self.assertEqual(old,TokenCopyExceptionSpec.from_dict(old).to_dict())
        new=TokenCopyExceptionSpec(schema_version=3,name='Original copier',card_types=('artifact',))
        self.assertEqual(new,TokenCopyExceptionSpec.from_dict(json.loads(json.dumps(new.to_dict()))))
        for changes in ({'name':[]},{'card_types':[]},{'retain_source_stats':1},{'unknown':True}):
            with self.assertRaises(ValueError):TokenCopyExceptionSpec.from_dict({**new.to_dict(),**changes})
        with self.assertRaises(ValueError):TokenCopyExceptionSpec(name='Versionless')

    def test_type_replacement_retains_only_artifact_subtypes_and_supertypes(self):
        source={'name':'Public source','type_line':'Legendary Artifact Creature Land — Equipment Elf Forest',
            'power':'4','toughness':'5','mana_cost':'{3}{G}','mana_value':4,'colors':['G'],'keywords':[], 'ability_fragments':[]}
        artifact=apply_copy_exception(source,TokenCopyExceptionSpec(schema_version=3,card_types=('artifact',)))
        self.assertEqual('Legendary Artifact — Equipment',artifact['type_line'])
        self.assertEqual(('4','5'),(artifact['power'],artifact['toughness']))
        renamed=apply_copy_exception(source,TokenCopyExceptionSpec(schema_version=3,name='Original copier'))
        self.assertEqual('Original copier',renamed['name']);self.assertEqual(source['name'],'Public source')
        additive=apply_copy_exception(source,TokenCopyExceptionSpec(add_card_types=('enchantment',)))
        self.assertIn('Elf',additive['type_line']);self.assertIn('Forest',additive['type_line'])

    def test_copy_name_stats_and_graveyard_selection_have_explicit_version_three(self):
        clauses=[('You may have Chameleon enter as a copy of a creature you control, except his name is Chameleon, Master of Disguise.','Chameleon, Master of Disguise'),
            ('You may have this creature enter as a copy of any creature card in a graveyard.','Body Double'),
            ("You may have this creature enter as a copy of an artifact or creature you control, except it's an artifact creature in addition to its other types, and its power and toughness are equal to this creature's power and toughness.",'Hulking Metamorph')]
        for text,name in clauses:
            spec=entry_copy_spec(text,source_name=name);self.assertIsNotNone(spec,text)
            self.assertEqual(3,spec.schema_version);self.assertEqual(spec,EntryCopySpec.from_dict(spec.to_dict()))
        for text in ['You may have this creature enter as a copy of a creature card in your hand.',
                'You may have this creature enter as a copy of a creature card in a graveyard that died this turn.',
                'You may have this creature enter as a copy of any creature on the battlefield, except its name is Some Other Name.']:
            self.assertIsNone(entry_copy_spec(text,source_name='Original copier'))


    def test_omitting_copy_value_application_fails_independent_type_contract(self):
        from unittest.mock import patch
        import sys
        module=sys.modules[__name__]
        method='test_type_replacement_retains_only_artifact_subtypes_and_supertypes'
        with patch.object(module,'apply_copy_exception',side_effect=lambda value,spec:dict(value)),self.assertRaises(AssertionError):
            getattr(type(self)(method),method)()


    def test_extended_copy_values_json_roundtrip_preserves_replay_payload(self):
        value=TokenCopyExceptionSpec(schema_version=3,name='Source name',card_types=('artifact',),add_subtypes=('vehicle',))
        replayed=TokenCopyExceptionSpec.from_dict(json.loads(json.dumps(value.to_dict())))
        source={'name':'Original','type_line':'Creature — Elf','colors':['G'],'power':'2','toughness':'3','keywords':[],'ability_fragments':[]}
        self.assertEqual(apply_copy_exception(source,value),apply_copy_exception(source,replayed))


import test_fixed_copy_type_exceptions as copies


class EntryCopyValueCompilerBoundaryTests(unittest.TestCase):
    def test_new_value_and_added_ability_dependencies_fail_closed(self):
        from copy import deepcopy
        from quorune.oracle_ir import compile_oracle_card
        from quorune.rules.capabilities import load_default_capability_registry,CapabilityRegistry
        from test_qualified_zone_event_queries import query_record
        record=query_record('You may have this creature enter as a copy of any creature on the battlefield, except it has haste and dethrone.')
        caps=load_default_capability_registry();self.assertEqual('exact',compile_oracle_card(record,capability_registry=caps).status)
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        for cap in ('zone.entry.fixed_copy','combat.attack.haste','activation.tap_untap_cost.haste','counter.producer.dethrone'):
            changed=deepcopy(raw);next(r for r in changed['capabilities'] if r['id']==cap).update(status='blocked',blockers=['omitted copy child authority'])
            self.assertNotEqual('exact',compile_oracle_card(record,capability_registry=CapabilityRegistry(changed)).status)
        named=query_record('You may have this creature enter as a copy of any creature card in a graveyard.')
        changed=deepcopy(raw);next(r for r in changed['capabilities'] if r['id']=='zone.entry.copy_value_exceptions').update(status='blocked',blockers=['omitted copy value owner'])
        self.assertNotEqual('exact',compile_oracle_card(named,capability_registry=CapabilityRegistry(changed)).status)

    def test_graveyard_card_domain_and_granted_crew_codec_reject_ambiguous_forms(self):
        from quorune.object_predicate import ObjectQuerySpec
        from quorune.ability_fragments import GrantedActivatedAbilitySpec
        for query in (ObjectQuerySpec(zones=('graveyard',),types_all=('artifact',)),ObjectQuerySpec(zones=('hand',),types_all=('creature',))):
            with self.assertRaises(ValueError):EntryCopySpec(query,schema_version=3)
        base=GrantedActivatedAbilitySpec('ability','program','Crew 3','Crew result',schema_version=3,crew_threshold=3)
        raw=base.to_dict();self.assertEqual(base,GrantedActivatedAbilitySpec.from_dict(json.loads(json.dumps(raw))))
        for patch in ({'crew_threshold':True},{'crew_threshold':None},{'schema_version':2},{'extra':1}):
            with self.assertRaises(ValueError):GrantedActivatedAbilitySpec.from_dict({**raw,**patch})


class EntryCopyValueRuntimeTests(unittest.TestCase):
    register_fixture_cards_on_add=True
    session=copies.FixedCopyTypeExceptionRuntimeTests.session
    add=copies.FixedCopyTypeExceptionRuntimeTests.add
    prepare=copies.FixedCopyTypeExceptionRuntimeTests.prepare
    checkpoint=copies.FixedCopyTypeExceptionRuntimeTests.checkpoint
    cast=copies.FixedCopyTypeExceptionRuntimeTests.cast
    resolve=copies.FixedCopyTypeExceptionRuntimeTests.resolve
    replay=copies.FixedCopyTypeExceptionRuntimeTests.replay
    choose_copy=copies.FixedCopyTypeExceptionRuntimeTests.choose_copy
    assert_closed=copies.FixedCopyTypeExceptionRuntimeTests.assert_closed

    @classmethod
    def setUpClass(cls):
        import tempfile
        from pathlib import Path
        from quorune.carddb import CardDatabase
        from quorune.deck import DeckDefinition,DeckEntry
        from quorune.rules.capabilities import load_default_capability_registry
        from scripts.build_test_database import build_fixture_database
        cls.temp=tempfile.TemporaryDirectory();path=Path(cls.temp.name)/'entry-copy-values.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',ROOT/'tests/fixtures/entry-copy-values-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Copy values',[DeckEntry('Generic Bound Commander',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temp.cleanup()

    def start(self,seed,name,*,target_zone='battlefield',target_seat='A',target_name='Elvish Visionary'):
        s=self.session(seed);e=s.engine
        target=self.add(e,target_name,seat=target_seat,zone=target_zone,ref='values-copy-target')
        source=self.add(e,name,zone='hand',ref='values-copier');self.assert_closed(s,source)
        self.prepare(s,mana={'U':10,'G':6,'B':6,'R':6,'C':20});self.checkpoint(s)
        return s,source,target

    def test_body_double_copies_public_graveyard_card_and_its_entry_draw_with_replay(self):
        s,source,target=self.start(707201,'Body Double',target_zone='graveyard',target_seat='B')
        before=len(s.state.players['A'].zones['hand'])
        self.cast(s,'A',source);s=self.choose_copy(s,target)
        self.assertEqual('battlefield',s.state.cards[source.object_id].zone)
        self.assertEqual('graveyard',s.state.cards[target.object_id].zone)
        self.assertEqual('Elvish Visionary',s.engine.display_name(source.object_id))
        self.assertEqual(before,len(s.state.players['A'].zones['hand']))
        self.replay(s,load=True)

    def test_chameleon_retained_full_name_is_copiable_through_second_clone(self):
        s,source,target=self.start(707202,'Chameleon, Master of Disguise')
        clone=self.add(s.engine,'Clone',zone='hand',ref='second-values-clone')
        private=self.add(s.engine,'Generic Bound Body',seat='C',zone='hand',ref='private-copy-value-hand');self.checkpoint(s)
        self.cast(s,'A',source);s=self.choose_copy(s,target)
        self.assertEqual('Chameleon, Master of Disguise',s.engine.display_name(source.object_id))
        self.assertEqual('Creature — Elf Shaman',s.engine._effective_card_data(s.state.cards[source.object_id])['type_line'])
        self.cast(s,'A',clone);s=self.choose_copy(s,s.state.cards[source.object_id])
        self.assertEqual('Chameleon, Master of Disguise',s.engine.display_name(clone.object_id))
        for viewer in 'ABD':self.assertNotIn(private.ref,json.dumps(s.packet('pilot:'+viewer,full=True)))
        self.replay(s,load=True)

    def test_effigy_enters_as_noncreature_artifact_draws_and_retains_copiable_blue_mana(self):
        s,source,target=self.start(707203,"Machine God's Effigy")
        self.cast(s,'A',source);s=self.choose_copy(s,target)
        data=s.engine._effective_card_data(s.state.cards[source.object_id])
        self.assertEqual('Artifact',data['type_line']);self.assertNotIn('creature',s.engine._type_parts(data['type_line'])[0])
        actions=s.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        mana=[a for a in actions if a['id'].startswith('activate:'+source.ref+':')]
        self.assertTrue(mana)
        before=s.state.players['A'].mana_pool['U']
        result=s.act('pilot:A',{'action_id':mana[0]['id']});self.assertTrue(result.ok,result.summary)
        self.assertEqual(before+1,s.state.players['A'].mana_pool['U']);self.assertFalse(s.state.stack)
        self.replay(s,load=True)

    def test_hulking_metamorph_uses_original_seven_stats_and_copied_entry_draw(self):
        s,source,target=self.start(707204,'Hulking Metamorph')
        self.cast(s,'A',source);s=self.choose_copy(s,target)
        self.assertEqual((7,7),tuple(s.engine._numeric_stat(source.object_id,k) for k in ('power','toughness')))
        data=s.engine._effective_card_data(s.state.cards[source.object_id])
        self.assertEqual({'artifact','creature'},s.engine._type_parts(data['type_line'])[0])
        self.assertEqual(2,data['mana_value']);self.replay(s,load=True)


    def test_prototyped_hulking_retains_three_stats_but_copies_target_mana_and_color(self):
        s,source,target=self.start(707205,'Hulking Metamorph')
        actions=s.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        prototype=next(a for a in actions if a.get('card')==source.ref and a.get('cast_method')=='prototype')
        result=s.act('pilot:A',{'action_id':prototype['id'],'pay':'auto'});self.assertTrue(result.ok,result.summary)
        s=self.choose_copy(s,target)
        data=s.engine._effective_card_data(s.state.cards[source.object_id])
        self.assertEqual((3,3),tuple(s.engine._numeric_stat(source.object_id,k) for k in ('power','toughness')))
        self.assertEqual('{1}{G}',data['mana_cost']);self.assertEqual(2,data['mana_value']);self.assertEqual(['G'],data['colors'])
        self.replay(s,load=True)


    def test_imposter_opponent_copy_loses_creature_subtypes_and_crew_uses_copied_stats(self):
        s,source,target=self.start(707206,'Imposter Mech',target_seat='B',target_name='Generic Bound Body')
        helpers=[self.add(s.engine,'Generic Bound Body',ref='crew-copy-helper-'+str(i)) for i in range(2)]
        self.checkpoint(s);self.cast(s,'A',source);s=self.choose_copy(s,target)
        data=s.engine._effective_card_data(s.state.cards[source.object_id])
        self.assertEqual({'artifact'},s.engine._type_parts(data['type_line'])[0])
        self.assertEqual({'vehicle'},s.engine._type_parts(data['type_line'])[1])
        actions=s.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        crew=[a for a in actions if a['id'].startswith('activate:'+source.ref+':')]
        self.assertTrue(crew,actions)
        result=s.act('pilot:A',{'action_id':crew[0]['id'],'cost_cards':[c.ref for c in helpers]})
        self.assertTrue(result.ok,result.summary);self.assertIsNone(self.resolve(s))
        data=s.engine._effective_card_data(s.state.cards[source.object_id])
        self.assertEqual({'artifact','creature'},s.engine._type_parts(data['type_line'])[0])
        self.assertEqual({'vehicle'},s.engine._type_parts(data['type_line'])[1])
        self.assertEqual((2,6),tuple(s.engine._numeric_stat(source.object_id,k) for k in ('power','toughness')))
        self.replay(s,load=True)

    def attack_after_copy(self,session,source,defender='B'):
        e=session.engine
        e.permissions.invalidate_current();e.state.pending_decision=None;e.state.priority_player=None;e.state.priority_passes=[]
        e.state.started=True;e.state.active_player='A';e.state.phase='combat';e.state.step='declare_attackers'
        e.state.phase_index=5;source.acquired_control_turn_count=-1
        e.state.players['A'].life=50;e.state.players['B'].life=50;e.state.players['C'].life=40;e.state.players['D'].life=30
        # This setup boundary starts a separate replay from the retained copied
        # object. Cast/entry already have their own full pre-action replay.
        e._issue_attackers();self.checkpoint(session)
        result=session.act('pilot:A',{'action_id':'attack','attacks':{source.ref:defender}})
        self.assertTrue(result.ok,result.summary)

    def test_dack_added_haste_and_dethrone_are_copiable_and_trigger_on_actual_attack(self):
        s,source,target=self.start(707207,"Dack's Duplicate",target_seat='B',target_name='Generic Bound Body')
        self.cast(s,'A',source);s=self.choose_copy(s,target)
        self.assertIn('haste',s.engine._combat_keywords(s.state.cards[source.object_id]))
        from quorune.ability_fragments import CombatKeywordTriggerSpec,CombatKeywordTriggerKind
        fragments=s.engine._effective_ability_fragments(s.state.cards[source.object_id])
        self.assertTrue(any(isinstance(f,CombatKeywordTriggerSpec) and f.kind is CombatKeywordTriggerKind.DETHRONE for f in fragments))
        self.replay(s,load=True)
        self.attack_after_copy(s,s.state.cards[source.object_id]);self.assertIsNone(self.resolve(s))
        self.assertEqual(1,s.state.cards[source.object_id].counters.get('+1/+1',0))
        self.replay(s,load=True)

    def test_auton_added_myriad_creates_optional_copies_with_copiable_exceptions(self):
        s,source,target=self.start(707208,'Auton Soldier',target_seat='B',target_name='Generic Bound Body')
        self.cast(s,'A',source);s=self.choose_copy(s,target)
        self.replay(s,load=True)
        self.attack_after_copy(s,s.state.cards[source.object_id]);decision=self.resolve(s)
        self.assertIsNotNone(decision);self.assertEqual('semantic.choice',decision.kind)
        s=self.replay(s,load=True)
        result=s.act('pilot:A',{'action_id':'choose','attacking':{'myriad:C':'create','myriad:D':'decline'}})
        self.assertTrue(result.ok,result.summary)
        result=s.act('pilot:A',{'action_id':'choose','attacking':{'myriad:C:0':'C'}})
        self.assertTrue(result.ok,result.summary);self.assertIsNone(self.resolve(s))
        tokens=[c for c in s.state.cards.values() if c.is_token and c.zone=='battlefield']
        self.assertEqual(1,len(tokens));token=tokens[0]
        self.assertTrue(token.tapped);self.assertEqual('C',token.attacking)
        types,_,supers=s.engine._type_parts(s.engine._effective_card_data(token)['type_line'])
        self.assertEqual({'artifact','creature'},types);self.assertNotIn('legendary',supers)
        self.assertTrue(s.state.delayed_triggers)
        self.replay(s,load=True)
        e=s.engine;e.permissions.invalidate_current();e.state.pending_decision=None;e.state.priority_player=None;e.state.priority_passes=[]
        e.state.phase='combat';e.state.step='end_combat';e.state.phase_index=8;e._enter_step();e.pump();self.checkpoint(s)
        if s.state.pending_decision is not None and s.state.pending_decision.kind=='trigger.order':
            refs=[r['id'] for r in s.state.pending_decision.payload_by_actor['A']['triggers']]
            result=s.act('pilot:A',{'action_id':'order','triggers':refs});self.assertTrue(result.ok,result.summary)
        self.assertIsNone(self.resolve(s));self.assertEqual('outside',s.state.cards[token.object_id].zone)
        self.replay(s,load=True)


    def test_dack_second_copy_retains_added_dethrone_and_haste_after_original_leaves(self):
        from quorune.ability_fragments import CombatKeywordTriggerSpec,CombatKeywordTriggerKind
        s,source,target=self.start(707210,"Dack's Duplicate",target_seat='B',target_name='Generic Bound Body')
        clone=self.add(s.engine,'Clone',zone='hand',ref='dack-second-copy');bounce=self.add(s.engine,'Unsummon',zone='hand',ref='dack-bounce')
        self.checkpoint(s);self.cast(s,'A',source);s=self.choose_copy(s,target)
        self.cast(s,'A',clone);s=self.choose_copy(s,s.state.cards[source.object_id])
        self.cast(s,'A',bounce,[source.ref]);self.assertIsNone(self.resolve(s))
        current=s.state.cards[clone.object_id]
        self.assertIn('haste',s.engine._combat_keywords(current))
        self.assertTrue(any(isinstance(f,CombatKeywordTriggerSpec) and f.kind is CombatKeywordTriggerKind.DETHRONE for f in s.engine._effective_ability_fragments(current)))
        self.replay(s,load=True)

    def test_name_and_fixed_stat_values_survive_ability_removal_and_zone_return_resets(self):
        s,source,target=self.start(707211,'Chameleon, Master of Disguise',target_name='Generic Bound Body')
        humble=self.add(s.engine,'Humble',zone='hand');bounce=self.add(s.engine,'Unsummon',zone='hand')
        self.prepare(s,mana={'W':2});self.checkpoint(s)
        self.cast(s,'A',source);s=self.choose_copy(s,target)
        self.cast(s,'A',humble,[source.ref]);self.assertIsNone(self.resolve(s))
        self.assertEqual('Chameleon, Master of Disguise',s.engine.display_name(source.object_id))
        self.cast(s,'A',bounce,[source.ref]);self.assertIsNone(self.resolve(s))
        self.assertNotIn('copy_overrides',s.state.cards[source.object_id].annotations)
        self.assertEqual('Chameleon, Master of Disguise',s.engine.display_name(source.object_id));self.replay(s,load=True)

    def test_real_copy_value_and_grant_omissions_fail_native_witnesses(self):
        from unittest.mock import patch
        from dataclasses import replace
        import quorune.semantic_runtime.entry_copy as owner
        original=owner.apply_copy_exception
        def no_name(values,spec):return original(values,replace(spec,name=None))
        method='test_chameleon_retained_full_name_is_copiable_through_second_clone'
        with patch.object(owner,'apply_copy_exception',side_effect=no_name),self.assertRaises(AssertionError):
            getattr(type(self)(method),method)()
        def no_types(values,spec):return original(values,replace(spec,card_types=None))
        method='test_effigy_enters_as_noncreature_artifact_draws_and_retains_copiable_blue_mana'
        with patch.object(owner,'apply_copy_exception',side_effect=no_types),self.assertRaises(AssertionError):
            getattr(type(self)(method),method)()


    def test_all_seven_originals_and_added_children_have_complete_admission(self):
        from quorune.card_programs import bind_card_program_runtime
        from quorune.card_programs.adapters import compile_best_available_card_program
        from quorune.semantics import SemanticRegistry
        for name in ('Body Double','Chameleon, Master of Disguise',"Machine God's Effigy","Dack's Duplicate",'Hulking Metamorph','Imposter Mech','Auton Soldier'):
            card=self.db.lookup(name)
            program=compile_best_available_card_program(self.db,card,semantic_registry=SemanticRegistry(),capability_registry=self.registry,capability_profile='commander_review')
            with self.subTest(card=name):self.assertTrue(bind_card_program_runtime(program,capability_registry=self.registry,profile='commander_review')['strict_capability_ready'])


    def test_graveyard_copy_ignores_targeting_prohibition_and_excludes_noncards(self):
        s,source,target=self.start(707212,'Body Double',target_zone='graveyard',target_seat='B')
        seal=self.add(s.engine,'Ground Seal');self.checkpoint(s)
        self.cast(s,'A',source);s=self.choose_copy(s,target)
        self.assertEqual('Elvish Visionary',s.engine.display_name(source.object_id))
        self.assertEqual('graveyard',s.state.cards[target.object_id].zone)
        self.replay(s,load=True)


    def test_retained_stats_suppress_copied_power_toughness_cda(self):
        s,source,target=self.start(707213,'Hulking Metamorph',target_name='Tarmogoyf')
        self.cast(s,'A',source);s=self.choose_copy(s,target)
        self.assertEqual((7,7),tuple(s.engine._numeric_stat(source.object_id,k) for k in ('power','toughness')))
        clone=self.add(s.engine,'Clone',zone='hand',ref='cda-second-copy')
        self.prepare(s,mana={'U':2,'C':4});self.checkpoint(s)
        self.cast(s,'A',clone);s=self.choose_copy(s,s.state.cards[source.object_id])
        self.assertEqual((7,7),tuple(s.engine._numeric_stat(clone.object_id,k) for k in ('power','toughness')))
        self.replay(s,load=True)
