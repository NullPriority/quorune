from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import json,tempfile,unittest
from unittest.mock import patch

from common import ROOT
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.target_cardinality import (AnnouncedTargetCardinality,ANY_NUMBER_TARGET_CAPABILITY as CAP,
    bind_any_number_target_schema,selected_target_count_descriptor,resolve_selected_target_count)
from quorune.target_count_cost_model import TARGET_COUNT_COST_CAPABILITY as COST_CAP,FixedManaPerTargetCostSpec
from quorune.repeated_mana_costs import FixedRepeatedManaCostSpec
from quorune.util import mana_cost_to_vector
from scripts.build_test_database import build_fixture_database
import test_announced_target_cardinality as controls
from quorune.rules.evidence_storage import read_evidence_document

NAMES=('Aerial Formation',"Ajani's Presence",'Cruel Feeding','Desperate Stand','Phalanx Formation','Rouse the Mob',
    'Consign to Dust',"Kiora's Dismissal")


class AnyNumberTargetTests(unittest.TestCase):
    register_fixture_cards_on_add=True
    session=controls.AnnouncedTargetCardinalityRuntimeTests.session
    add=controls.AnnouncedTargetCardinalityRuntimeTests.add
    prepare=controls.AnnouncedTargetCardinalityRuntimeTests.prepare
    checkpoint=controls.AnnouncedTargetCardinalityRuntimeTests.checkpoint
    resolve=controls.AnnouncedTargetCardinalityRuntimeTests.resolve
    replay=controls.AnnouncedTargetCardinalityRuntimeTests.replay
    binding=controls.AnnouncedTargetCardinalityRuntimeTests.binding
    actions=controls.AnnouncedTargetCardinalityRuntimeTests.actions
    action=controls.AnnouncedTargetCardinalityRuntimeTests.action
    cast=controls.AnnouncedTargetCardinalityRuntimeTests.cast

    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();path=Path(cls.temp.name)/'any-number.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',ROOT/'tests/fixtures/any-number-target-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Any number and mandatory extra-target prices',[DeckEntry('Generic Bound Commander',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temp.cleanup()

    def reject(self,s,command,principal='pilot:A'):
        before=authoritative_state_hash(s.state);result=s.act(principal,command)
        self.assertFalse(result.ok,result.summary);self.assertEqual(before,authoritative_state_hash(s.state))

    def private_replay(self,s,private):
        for seat in 'ABCD':
            packet=json.dumps(s.packet('pilot:'+seat,full=True))
            if seat==private.owner:self.assertIn(private.ref,packet)
            else:self.assertNotIn(private.ref,packet)
        return self.replay(s,load=True)

    def test_cardinality_and_price_codecs_preserve_legacy_x_and_reject_malformed_authority(self):
        old=AnnouncedTargetCardinality().to_dict()
        self.assertEqual({'schema_version':1,'parameter':'x','selection':'exact'},old)
        spec=AnnouncedTargetCardinality(selection='up_to',parameter='any_number',schema_version=2)
        schema={'zones':['battlefield'],'types_any':['creature'],'count_parameter':spec.to_dict()}
        self.assertEqual(20,bind_any_number_target_schema(schema,20)['up_to'])
        with self.assertRaises(ValueError):bind_any_number_target_schema(schema,None)
        for change in ({'schema_version':True},{'schema_version':1},{'parameter':'x'},{'selection':'exact'},{'unknown':1}):
            with self.assertRaises((ValueError,TypeError)):AnnouncedTargetCardinality.from_dict({**spec.to_dict(),**change})
        descriptor=selected_target_count_descriptor()
        item=SimpleNamespace(context={'target_groups':{'target_0':['a','b']}},targets=['a',None])
        self.assertEqual(2,resolve_selected_target_count(descriptor,item))
        for groups in (None,{}, {'target_0':['a','a']},{'target_0':[None]}):
            with self.assertRaises(ValueError):resolve_selected_target_count(descriptor,SimpleNamespace(context={'target_groups':groups}))
        requirements,_=mana_cost_to_vector('{2}{U}')
        cost=FixedManaPerTargetCostSpec(FixedRepeatedManaCostSpec('front:test',requirements))
        self.assertEqual(cost,FixedManaPerTargetCostSpec.from_dict(cost.to_dict()))
        for count,generic,blue in ((0,0,0),(1,0,0),(2,2,1),(9,16,8)):
            result=cost.requirements_for_count(count);self.assertEqual((generic,blue),(result['GENERIC'],result['U']))
        for count in (True,-1,None,'2'):
            with self.assertRaises(ValueError):cost.requirements_for_count(count)

    def test_originals_have_complete_registration_and_both_independent_runtime_minima(self):
        from quorune.card_programs.binding import bind_semantic_program_runtime
        s=self.session(794000)
        for name in NAMES:
            card=self.add(s.engine,name,zone='hand');binding=self.binding(s,card)
            self.assertTrue(binding['strict_capability_ready'],(name,binding['blockers']))
            ir=compile_oracle_card(self.db.lookup(name),capability_registry=self.registry,capability_profile='commander_review')
            self.assertEqual('exact',ir.status,(name,ir.material_residuals))
            for cap in (CAP,COST_CAP):
                program=next(p for p in s.engine.semantics.programs_for_oracle(card.oracle_id) if cap in p.capability_dependencies)
                deps=tuple(d for d in program.capability_dependencies if d!=cap)
                changed=replace(program,capability_dependencies=deps,capability_closure=self.registry.closure(deps,profile='commander_review').to_dict() if deps else None)
                result=bind_semantic_program_runtime(changed,capability_registry=self.registry,profile='commander_review')
                self.assertFalse(result['strict']);self.assertIn('capability:undeclared_runtime_dependency:'+cap,result['blockers'])

    def test_each_original_pays_two_target_price_and_applies_its_complete_result(self):
        modifiers={'Aerial Formation':(1,1,'flying'),"Ajani's Presence":(1,1,'indestructible'),
            'Cruel Feeding':(1,0,'lifelink'),'Desperate Stand':(2,0,'first strike'),
            'Phalanx Formation':(0,0,'double strike'),'Rouse the Mob':(2,0,'trample')}
        for index,name in enumerate(NAMES):
            s=self.session(794010+index);source=self.add(s.engine,name,zone='hand')
            target_name='Sol Ring' if name=='Consign to Dust' else 'Glorious Anthem' if name=="Kiora's Dismissal" else 'Generic Bound Body'
            targets=[self.add(s.engine,target_name,seat=seat,ref='any-original-'+seat) for seat in 'BD']
            private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
            base,_=mana_cost_to_vector(self.db.lookup(name).mana_cost)
            import re
            extra,_=mana_cost_to_vector(re.search(r'costs (.*?) more',self.db.lookup(name).oracle_text)[1])
            mana={key:base[key]+extra[key] for key in base};mana['C']=mana.pop('GENERIC')
            self.prepare(s,mana=mana);self.checkpoint(s)
            action=self.action(s,source);self.assertIsNotNone(action,name)
            command={'action_id':action['id'],'targets':[c.ref for c in targets],'pay':'auto'}
            self.reject(s,command,'pilot:C');self.reject(s,{**command,'targets':[targets[0].ref]*2})
            result=s.act('pilot:A',command);self.assertTrue(result.ok,(name,result.summary))
            self.assertEqual(0,sum(s.state.players['A'].mana_pool.values()),name)
            self.assertNotIn('kicker_paid',s.state.stack[-1].context)
            s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
            for target in targets:
                current=s.state.cards[target.object_id]
                if name in modifiers:
                    power,toughness,keyword=modifiers[name]
                    self.assertEqual((2+power,6+toughness),tuple(s.engine._numeric_stat(current.object_id,k) for k in ('power','toughness')),name)
                    self.assertIn(keyword,s.engine._combat_keywords(current),name)
                    if name=='Desperate Stand':self.assertIn('vigilance',s.engine._combat_keywords(current))
                else:self.assertEqual('graveyard' if name=='Consign to Dust' else 'hand',current.zone,name)
            self.private_replay(s,private)

    def test_unrestricted_targets_accept_zero_and_nine_without_x_or_a_fixed_six_limit(self):
        for count in (0,9):
            s=self.session(794030+count);source=self.add(s.engine,'Generic Any Tap',zone='hand')
            targets=[self.add(s.engine,'Generic Bound Body',seat='B',ref='any-nine-'+str(i)) for i in range(9)]
            private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
            self.prepare(s,mana={'U':1});self.checkpoint(s)
            self.assertTrue(self.binding(s,source)['strict_capability_ready'])
            action=self.action(s,source);self.assertGreaterEqual(action['target_schema']['groups'][0]['max'],9)
            self.assertNotIn('x',action['cost_options'][0].get('choice_schema',{}))
            result=s.act('pilot:A',{'action_id':action['id'],'targets':[c.ref for c in targets[:count]],'pay':'auto'})
            self.assertTrue(result.ok,result.summary)
            s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
            self.assertEqual(count,sum(s.state.cards[c.object_id].tapped for c in targets))
            self.private_replay(s,private)

    def test_strive_zero_one_and_nine_targets_use_the_same_base_and_per_extra_price(self):
        for count in (0,1,9):
            s=self.session(794100+count);source=self.add(s.engine,'Aerial Formation',zone='hand')
            targets=[self.add(s.engine,'Generic Bound Body',seat='B',ref='strive-nine-'+str(i)) for i in range(9)]
            private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
            self.prepare(s,mana={'U':max(count,1),'C':2*max(0,count-1)});self.checkpoint(s)
            action=self.action(s,source);option=action['cost_options'][0]
            quotes=option['target_costs']['prices'];self.assertTrue(any(row['target_count']==count for row in quotes))
            result=s.act('pilot:A',{'action_id':action['id'],'targets':[c.ref for c in targets[:count]],'pay':'auto'})
            self.assertTrue(result.ok,result.summary);self.assertEqual(0,sum(s.state.players['A'].mana_pool.values()))
            s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
            for index,target in enumerate(targets):
                current=s.state.cards[target.object_id]
                self.assertEqual(3 if index<count else 2,s.engine._numeric_stat(current.object_id,'power'))
                self.assertEqual(index<count,'flying' in s.engine._combat_keywords(current))
            self.private_replay(s,private)

    def test_colored_surcharge_target_taxes_and_static_reductions_share_offer_and_acceptance(self):
        for reduced in (False,True):
            s=self.session(794120+reduced);source=self.add(s.engine,'Aerial Formation',zone='hand')
            taxed=self.add(s.engine,'Boreal Elemental',seat='B')
            ordinary=self.add(s.engine,'Generic Bound Body',seat='D')
            if reduced:self.add(s.engine,'Goblin Electromancer')
            private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
            self.prepare(s,mana={'U':2,'C':3 if reduced else 4});self.checkpoint(s)
            action=self.action(s,source);self.assertIn(taxed.ref,action['target_schema']['legal_refs'])
            command={'action_id':action['id'],'targets':[taxed.ref,ordinary.ref],'pay':'auto'}
            self.reject(s,command,'pilot:D')
            result=s.act('pilot:A',command);self.assertTrue(result.ok,result.summary)
            self.assertEqual(0,sum(s.state.players['A'].mana_pool.values()))
            s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
            self.assertIn('flying',s.engine._combat_keywords(s.state.cards[ordinary.object_id]));self.private_replay(s,private)
        s=self.session(794122);source=self.add(s.engine,'Aerial Formation',zone='hand')
        targets=[self.add(s.engine,'Generic Bound Body',seat='B',ref='strive-no-blue-'+str(i)) for i in range(2)]
        self.prepare(s,mana={'U':1,'C':50});self.checkpoint(s)
        action=self.action(s,source);self.assertFalse(any(row['target_count']==2 for row in action['cost_options'][0]['target_costs']['prices']))
        self.reject(s,{'action_id':action['id'],'targets':[c.ref for c in targets],'pay':'auto'})
        accepted=s.act('pilot:A',{'action_id':action['id'],'targets':[targets[0].ref],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary)

    def test_partial_and_all_illegal_targets_keep_the_original_paid_count(self):
        for all_illegal in (False,True):
            s=self.session(794130+all_illegal);source=self.add(s.engine,'Aerial Formation',zone='hand')
            targets=[self.add(s.engine,'Generic Bound Body',seat='B',ref='strive-loss-'+str(i)) for i in range(2)]
            murders=[self.add(s.engine,'Murder',seat='C',zone='hand',ref='strive-murder-'+str(i)) for i in range(2 if all_illegal else 1)]
            private=self.add(s.engine,'Generic Bound Growth',seat='D',zone='hand')
            s.state.players['C'].mana_pool.update({'B':2*len(murders),'C':len(murders)})
            self.prepare(s,mana={'U':2,'C':2});self.checkpoint(s)
            self.cast(s,'A',source,[c.ref for c in targets])
            for murder,target in zip(murders,targets):self.cast(s,'C',murder,[target.ref])
            s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
            self.assertEqual(0,sum(s.state.players['A'].mana_pool.values()))
            self.assertEqual('graveyard',s.state.cards[targets[0].object_id].zone)
            last=s.state.cards[targets[1].object_id]
            self.assertEqual('graveyard' if all_illegal else 'battlefield',last.zone)
            if not all_illegal:self.assertEqual(3,s.engine._numeric_stat(last.object_id,'power'))
            self.assertEqual('graveyard',s.state.cards[source.object_id].zone);self.private_replay(s,private)

    def test_copy_retains_two_target_roles_and_changes_targets_without_repaying_strive(self):
        s=self.session(794140);source=self.add(s.engine,'Aerial Formation',zone='hand')
        copy=self.add(s.engine,'Reverberate',seat='B',zone='hand')
        targets=[self.add(s.engine,'Generic Bound Body',seat='D',ref='strive-copy-'+str(i)) for i in range(4)]
        private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
        s.state.players['B'].mana_pool.update({'R':2})
        self.prepare(s,mana={'U':2,'C':2});self.checkpoint(s)
        self.cast(s,'A',source,[c.ref for c in targets[:2]])
        self.cast(s,'B',copy,[s.state.stack[-1].ref]);decision=self.resolve(s)
        self.assertIsNotNone(decision);self.assertEqual('semantic.choice',decision.kind)
        self.reject(s,{'action_id':'choose','targets':[targets[2].ref]},'pilot:B')
        self.reject(s,{'action_id':'choose','targets':[targets[2].ref,targets[3].ref]},'pilot:A')
        s=self.replay(s,load=True)
        accepted=s.act('pilot:B',{'action_id':'choose','targets':[targets[2].ref,targets[3].ref]})
        self.assertTrue(accepted.ok,accepted.summary);self.assertIsNone(self.resolve(s))
        for target in targets:
            self.assertEqual(3,s.engine._numeric_stat(target.object_id,'power'))
            self.assertIn('flying',s.engine._combat_keywords(s.state.cards[target.object_id]))
        for seat in 'AB':self.assertEqual(0,sum(s.state.players[seat].mana_pool.values()))
        self.private_replay(s,private)

    def test_copy_can_retain_an_unchanged_illegal_target_while_retargeting_the_other_role(self):
        s=self.session(794145);source=self.add(s.engine,'Aerial Formation',zone='hand')
        copy=self.add(s.engine,'Reverberate',seat='B',zone='hand')
        murder=self.add(s.engine,'Murder',seat='C',zone='hand')
        targets=[self.add(s.engine,'Generic Bound Body',seat='D',ref='strive-retained-'+str(i)) for i in range(3)]
        private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
        s.state.players['B'].mana_pool.update({'R':2});s.state.players['C'].mana_pool.update({'B':2,'C':1})
        self.prepare(s,mana={'U':2,'C':2});self.checkpoint(s)
        self.cast(s,'A',source,[c.ref for c in targets[:2]])
        self.cast(s,'B',copy,[s.state.stack[-1].ref]);self.cast(s,'C',murder,[targets[0].ref])
        decision=self.resolve(s);self.assertIsNotNone(decision);self.assertEqual('semantic.choice',decision.kind)
        self.assertEqual('graveyard',s.state.cards[targets[0].object_id].zone)
        s=self.replay(s,load=True)
        accepted=s.act('pilot:B',{'action_id':'choose','targets':[{'retain':0},targets[2].ref]})
        self.assertTrue(accepted.ok,accepted.summary);self.assertIsNone(self.resolve(s))
        for target in targets[1:]:self.assertEqual(3,s.engine._numeric_stat(target.object_id,'power'))
        for seat in 'AB':self.assertEqual(0,sum(s.state.players[seat].mana_pool.values()))
        self.private_replay(s,private)

    def test_cascade_waives_the_base_mana_cost_but_preserves_the_target_surcharge(self):
        s=self.session(794150);party=self.add(s.engine,'Boarding Party',zone='hand')
        source=self.add(s.engine,'Aerial Formation',zone='library')
        library=s.state.players['A'].zones['library'];library.remove(source.object_id);library.insert(0,source.object_id)
        targets=[self.add(s.engine,'Generic Bound Body',seat='B',ref='strive-free-'+str(i)) for i in range(2)]
        private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(s,mana={'R':1,'C':7,'U':1});self.checkpoint(s)
        self.cast(s,'A',party);pending=self.resolve(s)
        self.assertIsNotNone(pending);self.assertEqual('selection.exile_cast',pending.kind)
        self.reject(s,{'action_id':'cast','targets':[c.ref for c in targets]},'pilot:D')
        result=s.act('pilot:A',{'action_id':'cast','targets':[c.ref for c in targets],'pay':'auto'})
        self.assertTrue(result.ok,result.summary);self.assertEqual(0,sum(s.state.players['A'].mana_pool.values()))
        s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
        for target in targets:self.assertEqual(3,s.engine._numeric_stat(target.object_id,'power'))
        self.private_replay(s,private)

    def test_activation_and_trigger_target_choices_share_unrestricted_cardinality(self):
        s=self.session(794160);source=self.add(s.engine,'Generic Any Untapper')
        targets=[self.add(s.engine,'Generic Bound Plains',seat='B',ref='any-activation-'+str(i)) for i in range(9)]
        for target in targets:target.tapped=True
        private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(s,mana={'C':1});self.checkpoint(s)
        self.assertTrue(self.binding(s,source)['strict_capability_ready'])
        action=next(r for r in self.actions(s) if r['id'].startswith('activate:'+source.ref+':'))
        command={'action_id':action['id'],'targets':[c.ref for c in targets],'pay':'auto'}
        self.reject(s,command,'pilot:C');accepted=s.act('pilot:A',command)
        self.assertTrue(accepted.ok,accepted.summary);self.assertTrue(s.state.cards[source.object_id].tapped)
        s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
        self.assertTrue(all(not s.state.cards[c.object_id].tapped for c in targets));self.private_replay(s,private)
        s=self.session(794161);source=self.add(s.engine,'Generic Any Arrival',zone='hand')
        targets=[self.add(s.engine,'Generic Bound Body',seat='B',ref='any-trigger-'+str(i)) for i in range(9)]
        private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(s,mana={'C':2});self.checkpoint(s)
        self.assertTrue(self.binding(s,source)['strict_capability_ready']);self.cast(s,'A',source)
        pending=self.resolve(s);self.assertIsNotNone(pending);self.assertEqual('semantic.target',pending.kind)
        self.reject(s,{'action_id':'choose','targets':[c.ref for c in targets]},'pilot:D')
        s=self.replay(s,load=True);accepted=s.act('pilot:A',{'action_id':'choose','targets':[c.ref for c in targets]})
        self.assertTrue(accepted.ok,accepted.summary);self.assertIsNone(self.resolve(s))
        self.assertTrue(all(s.state.cards[c.object_id].tapped for c in targets));self.private_replay(s,private)

    def test_incomplete_source_results_and_variable_prices_remain_residual(self):
        for name in ('Colossal Heroics','Hour of Need','Silence the Believers','Twinflame','Call the Coppercoats','Polymorphous Rush'):
            s=self.session(794050);source=self.add(s.engine,name,zone='hand')
            self.assertFalse(self.binding(s,source)['strict_capability_ready'],name)
        from quorune.compiler.any_number_target_sets import any_number_target_set_template
        for text in ('Destroy any number of target creatures and their dreams.',
                'Any number of target creatures each get +X/+0 until end of turn.',
                'Tap any number of target creatures. Draw a card.'):
            self.assertIsNone(any_number_target_set_template(text),text)
        record=self.db.lookup('Aerial Formation')
        for text in (record.oracle_text.replace('{2}{U}','{X}{U}'),
                record.oracle_text.replace('beyond the first','beyond a hungry moon'),
                record.oracle_text.splitlines()[0]+"\nReturn target creature to its owner's hand. This spell deals 2 damage to another target creature."):
            ir=compile_oracle_card(replace(record,oracle_text=text),capability_registry=self.registry,capability_profile='commander_review')
            self.assertNotEqual('exact',ir.status,text)

    def test_price_commitment_rejects_count_source_and_malformed_fact_drift(self):
        from quorune.rules.casting.costs import build_cast_cost_options
        from quorune.rules.casting.target_count_costs import validate_target_count_cost_commit
        s=self.session(794170);source=self.add(s.engine,'Aerial Formation',zone='hand')
        target=self.add(s.engine,'Generic Bound Body',seat='B')
        self.prepare(s,mana={'U':1});self.checkpoint(s)
        program=next(p for p in s.engine.semantics.programs_for_oracle(source.oracle_id) if p.event=='resolve')
        options=build_cast_cost_options(s.engine,'A',source,program,response={'targets':[target.ref]},hint=False)
        self.assertTrue(options);option=options[0].to_dict()
        validate_target_count_cost_commit(s.engine,source,option,[target.ref])
        for update in ({'count':True},{'count':0},{'count':2},{'unknown':1}):
            drift=deepcopy(option);drift['target_count_cost'].update(update)
            with self.assertRaises(ValueError):validate_target_count_cost_commit(s.engine,source,drift,[target.ref])
        for update in ({'schema_version':True},{'free_targets':2},{'unknown':1}):
            drift=deepcopy(option);drift['target_count_cost']['spec'].update(update)
            with self.assertRaises(ValueError):validate_target_count_cost_commit(s.engine,source,drift,[target.ref])
        with patch('quorune.rules.casting.target_count_costs.compiled_target_count_cost',return_value=None):
            with self.assertRaises(ValueError):validate_target_count_cost_commit(s.engine,source,option,[target.ref])

    def test_verified_dependency_withdrawal_and_fresh_count_or_price_omissions_reject(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from scripts.update_capability_evidence import build_index,discover_tests
        decl=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json')
        discovered=set(discover_tests(ROOT).values())
        for capability in (CAP,COST_CAP):
            raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
            next(c for c in raw['capabilities'] if c['id']==capability).update(status='blocked',blockers=['withdrawn target count authority'],dependency_fail_closed_status='not_run')
            registry=CapabilityRegistry(raw)
            index=build_index(registry_value=raw,declaration_source=decl,discovered_test_ids=discovered)
            registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry))
            ir=compile_oracle_card(self.db.lookup('Aerial Formation'),capability_registry=registry,capability_profile='commander_review')
            blocks=[b for r in ir.material_residuals for b in r.blockers]
            self.assertTrue(any('status:'+capability+':blocked' in b for b in blocks),blocks)
            self.assertFalse(any('evidence_index:unverified' in b for b in blocks))
        from quorune.rules.casting import target_cardinality
        original=target_cardinality.bind_announced_target_schema
        method='test_unrestricted_targets_accept_zero_and_nine_without_x_or_a_fixed_six_limit'
        with patch.object(target_cardinality,'bind_announced_target_schema',side_effect=lambda schema,value:original(schema,min(value,6))):
            with self.assertRaises(AssertionError):getattr(type(self)(method),method)()
        method='test_each_original_pays_two_target_price_and_applies_its_complete_result'
        with patch.object(FixedManaPerTargetCostSpec,'requirements_for_count',side_effect=lambda count:dict.fromkeys(('GENERIC','W','U','B','R','G','C'),0)):
            with self.assertRaises(AssertionError):getattr(type(self)(method),method)()
