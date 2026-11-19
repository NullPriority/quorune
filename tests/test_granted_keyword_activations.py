"""CR 113.7a, 702.6 and 702.122: additional fixed Equip/Crew activations."""
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT,packet_without_decision_capability
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_fixed_token_creation_effects import token_record
import test_target_recipient_results as recipients


class FixedKeywordActivationCompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.registry=load_default_capability_registry()

    def compile(self,text):
        return compile_oracle_card(token_record('Generic Activation Grant',text,111401,
            type_line='Creature — Human'),capability_registry=self.registry,capability_profile='commander_review')

    def test_query_and_conditional_keyword_grants_keep_complete_typed_costs(self):
        for text,kind,amount in (('Vehicles you control have crew 2.','crew',2),
                ('Equipment you control have equip {1}.','equip',1),
                ('Metalcraft — Equipment you control have equip {0} as long as you control three or more artifacts.','equip',0)):
            with self.subTest(text=text):
                ir=self.compile(text);self.assertEqual('exact',ir.status,ir.material_residuals)
                child,= [n for n in ir.faces[0].nodes if n.kind=='granted_activated_ability']
                if kind=='crew':
                    self.assertEqual(amount,child.cost['crew'])
                    self.assertIn('activation.crew.fixed_power',child.capability_dependencies)
                else:
                    self.assertEqual(amount,child.cost['mana']['GENERIC'])
                    self.assertEqual('you',child.target_schema['controller'])
                    self.assertEqual('attach',child.effects[0]['op'])
                self.assertEqual(1,child.span.line)

    def test_variable_ambiguous_and_unrepresented_keyword_suffixes_remain_residual(self):
        for text in ('Vehicles you control have crew X.','Vehicles you control have crew -1.',
                'Vehicles you control have crew 2 and crew 3.',
                'Vehicles you control have crew 2. Rewrite history.',
                'Equipment you control have equip {X}.','Equipment you control have equip {01}.',
                'Equipment you control have equip {1} and draw a card.',
                'Equipment you control have equip {1} only if you imagine it.'):
            with self.subTest(text=text):self.assertNotEqual('exact',self.compile(text).status)

    def test_keyword_lowering_omission_mutant_loses_the_positive_contract(self):
        text='Vehicles you control have crew 2.'
        self.assertEqual('exact',self.compile(text).status)
        with patch('quorune.compiler.granted_keyword_activation_nodes.compile_granted_keyword_activation_nodes',return_value=None):
            self.assertNotEqual('exact',self.compile(text).status)

    def equip_identity(self):
        from types import SimpleNamespace
        from quorune.rules.activation.query import _typed_granted_abilities
        ir=self.compile('Equipment you control have equip {1}.')
        self.assertEqual('exact',ir.status,ir.material_residuals)
        outer,child=ir.faces[0].nodes
        ability,=_typed_granted_abilities({'ability_fragments':outer.handlers[0]['modifier']['add_ability_fragments']})
        program=SimpleNamespace(provenance={'granted_only':True},event=child.event,
            active_zone=child.active_zone,handlers=child.handlers,cost_schema=child.cost,
            target_schema=child.target_schema,effects=child.effects)
        return ability,program

    def test_equip_identity_requires_a_trusted_catalog_with_matching_cost_target_and_result(self):
        from copy import deepcopy
        from types import SimpleNamespace
        from quorune.rules.activation.ability_kinds import activation_matches_cost_scope
        ability,original=self.equip_identity()
        from quorune.card_programs.runtime_requirements import nonmana_equip_runtime_dependencies
        self.assertTrue({'attachment.equip.fixed_mana','attachment.action.fixed_source',
            'target.revalidate_resolution'}<=set(nonmana_equip_runtime_dependencies(original)))
        holder={'program':original,'trusted':True}
        host=SimpleNamespace(semantics=SimpleNamespace(get=lambda key:holder['program']),
            _semantic_key_for_ability=lambda source,value:value.builtin_semantic_key,
            semantic_program_is_current_trusted=lambda program:holder['trusted'])
        self.assertTrue(activation_matches_cost_scope(host,None,ability,'equip'))
        for mutation in ('untrusted','missing_catalog','cost','target','result','ordinary_program'):
            program=deepcopy(original);holder.update(program=program,trusted=True)
            if mutation=='untrusted':holder['trusted']=False
            elif mutation=='missing_catalog':program.handlers=()
            elif mutation=='cost':program.cost_schema['mana']['GENERIC']=0
            elif mutation=='target':program.target_schema['controller']='opponent'
            elif mutation=='result':program.effects=({'op':'draw','player':'$controller','count':1},)
            else:program.provenance={'granted_only':False}
            with self.subTest(mutation=mutation):self.assertFalse(activation_matches_cost_scope(host,None,ability,'equip'))

    def test_equip_catalog_omission_mutant_cannot_satisfy_the_keyword_identity_contract(self):
        from types import SimpleNamespace
        from quorune.rules.activation.ability_kinds import activation_matches_cost_scope
        def witness():
            ability,program=self.equip_identity()
            host=SimpleNamespace(semantics=SimpleNamespace(get=lambda key:program),
                _semantic_key_for_ability=lambda source,value:value.builtin_semantic_key,
                semantic_program_is_current_trusted=lambda value:True)
            self.assertTrue(activation_matches_cost_scope(host,None,ability,'equip'))
        witness()
        with patch('quorune.compiler.granted_keyword_activation_nodes.equip_keyword_catalog_node',
                side_effect=lambda node,**kwargs:node),self.assertRaises(AssertionError):witness()

    def test_identical_quoted_token_attachment_does_not_become_the_literal_equip_keyword(self):
        text=('When this creature enters, create a colorless Equipment artifact token named Rig with '
            '"{1}: Attach this Equipment to target creature you control. Activate only as a sorcery." and equip {1}.')
        ir=self.compile(text);self.assertEqual('exact',ir.status,ir.material_residuals)
        children=[node for node in ir.faces[0].nodes if node.kind=='granted_activated_ability']
        self.assertEqual(2,len(children))
        self.assertEqual([0,1],[len(node.handlers) for node in children])


class FixedKeywordActivationNativeTests(InheritedNativeFixture,unittest.TestCase):
    reject=recipients.TargetRecipientNativeTests.reject
    finish=recipients.TargetRecipientNativeTests.finish

    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'keyword-activation-grants.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/granted-keyword-activation-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Keyword activation grants',[DeckEntry('Sliver Hivelord',1,'commander'),
            DeckEntry('Generic Bound Plains',30)],['Sliver Hivelord'])

    def crew(self,s,vehicle,creature,threshold):
        self.priority(s,vehicle.controller)
        abilities=s.engine._activated_abilities(s.state.cards[vehicle.object_id])
        self.assertIn(6,[ability.crew_threshold for ability in abilities])
        ability,=[ability for ability in abilities if ability.crew_threshold==threshold]
        command={'action_id':'activate:'+vehicle.ref+':'+ability.ability_id,'cost_cards':[creature.ref],
            'targets':[],'pay':'auto'}
        offered=next(row for row in s.packet('pilot:'+vehicle.controller,full=True)['decision']['ctx']['legal']['actions']
            if row['id']==command['action_id'])
        self.assertTrue(offered['cost_summary'])
        crew_cost,=[c for c in offered['cost_summary']['choose_cost'] if c['k']=='crew']
        self.assertEqual(threshold,crew_cost['minimum_total_power'])
        self.reject(s,'D',command)
        self.reject(s,vehicle.controller,{**command,'cost_cards':[]})
        printed,=[a for a in abilities if a.crew_threshold==6]
        self.reject(s,vehicle.controller,{**command,'action_id':'activate:'+vehicle.ref+':'+printed.ability_id})
        before=sum(s.state.players[vehicle.controller].mana_pool.values())
        result=s.act('pilot:'+vehicle.controller,command);self.assertTrue(result.ok,result.summary)
        self.assertEqual(before,sum(s.state.players[vehicle.controller].mana_pool.values()))
        self.assertTrue(s.state.cards[creature.object_id].tapped)
        self.assertNotIn('Creature',s.engine._effective_card_data(s.state.cards[vehicle.object_id])['type_line'])

    def test_actual_kotori_grants_additional_crew_two_and_its_combat_keywords(self):
        s=self.session(1114010)
        source=self.add(s.engine,'Kotori, Pilot Prodigy',zone='hand')
        vehicle=self.add(s.engine,'Consulate Dreadnought')
        body=self.add(s.engine,'Grizzly Bears')
        foreign=self.add(s.engine,'Consulate Dreadnought',seat='B',ref='foreign-granted-crew-vehicle')
        self.add(s.engine,'Fervor')
        private=self.add(s.engine,'Counterspell',seat='C',zone='hand',ref='crew-grant-private')
        self.assert_closed(s,source);self.prepare(s,mana={'C':3,'U':1,'W':1});self.checkpoint(s)
        self.cast(s,'A',source);self.assertIsNone(self.resolve(s))
        self.assertEqual([6],[ability.crew_threshold for ability in s.engine._activated_abilities(foreign)])
        self.crew(s,vehicle,body,2)
        self.replay(s,load=True);self.assertIsNone(self.resolve(s))
        self.assertIn('Creature',s.engine._effective_card_data(s.state.cards[vehicle.object_id])['type_line'])
        self.until(s,lambda current:current.state.pending_decision is not None
            and current.state.pending_decision.kind=='semantic.target')
        self.assertEqual(('combat','beginning_combat'),(s.state.phase,s.state.step))
        decision=s.state.pending_decision
        if decision is not None and decision.kind!='priority':
            self.reject(s,'D',{'action_id':'choose','targets':[vehicle.ref]})
            result=s.act('pilot:A',{'action_id':'choose','targets':[vehicle.ref]});self.assertTrue(result.ok,result.summary)
        self.assertIsNone(self.resolve(s))
        self.assertTrue({'lifelink','vigilance'}<=s.engine._combat_keywords(s.state.cards[vehicle.object_id]))
        self.finish(s,private)

    def equip(self,s,equipment,recipient,amount,*,resolve=True,expected_payment=None):
        self.priority(s,equipment.controller)
        abilities=s.engine._activated_abilities(s.state.cards[equipment.object_id])
        ability,=[a for a in abilities if a.sorcery_speed and a.mana['GENERIC']==amount]
        command={'action_id':'activate:'+equipment.ref+':'+ability.ability_id,'targets':[recipient.ref],'pay':'auto'}
        offered=next(row for row in s.packet('pilot:'+equipment.controller,full=True)['decision']['ctx']['legal']['actions']
            if row['id']==command['action_id'])
        self.assertIn(recipient.ref,offered['target_schema']['legal_refs'])
        self.reject(s,'D',command)
        before=sum(s.state.players[equipment.controller].mana_pool.values())
        result=s.act('pilot:'+equipment.controller,command);self.assertTrue(result.ok,result.summary)
        self.assertEqual(amount if expected_payment is None else expected_payment,
            before-sum(s.state.players[equipment.controller].mana_pool.values()))
        if resolve:
            self.assertIsNone(self.resolve(s))
            self.assertEqual(recipient.object_id,s.state.cards[equipment.object_id].attached_to)

    def test_actual_astor_privately_selects_vehicle_then_preserves_both_printed_and_granted_prices(self):
        s=self.session(1114020)
        source=self.add(s.engine,'Astor, Bearer of Blades',zone='hand')
        equipment=self.add(s.engine,'Heartseeker')
        body=self.add(s.engine,'Grizzly Bears',ref='astor-equip-first')
        second=self.add(s.engine,'Grizzly Bears',ref='astor-equip-second')
        pilot=self.add(s.engine,'Llanowar Elves')
        vehicle=self.add(s.engine,'Consulate Dreadnought',zone='library',ref='astor-looked-vehicle')
        other_equipment=self.add(s.engine,'Lightning Greaves',zone='library',ref='astor-looked-equipment')
        invalid=self.add(s.engine,'Grizzly Bears',zone='library',ref='astor-looked-nonmatching')
        foreign=self.add(s.engine,'Heartseeker',seat='B',ref='astor-foreign-equipment')
        private=self.add(s.engine,'Counterspell',seat='C',zone='hand',ref='astor-private')
        library=s.state.players['A'].zones['library']
        lands=[oid for oid in library if oid not in {vehicle.object_id,other_equipment.object_id,invalid.object_id}][:4]
        looked=[vehicle.object_id,other_equipment.object_id,invalid.object_id,*lands]
        library[:]=[oid for oid in library if oid not in looked]+list(reversed(looked))
        self.assert_closed(s,source);self.prepare(s,mana={'C':14,'R':1,'W':1});self.checkpoint(s)
        self.cast(s,'A',source);self.assertEqual('semantic.choice',self.resolve(s).kind)
        for seat in 'BCD':
            packet=json.dumps(packet_without_decision_capability(s.packet('pilot:'+seat,full=True)))
            for oid in looked:self.assertNotIn(s.state.cards[oid].ref,packet)
        self.reject(s,'A',{'action_id':'choose','cards':[invalid.ref]})
        self.reject(s,'D',{'action_id':'choose','cards':[vehicle.ref]})
        self.replay(s,load=True)
        result=s.act('pilot:A',{'action_id':'choose','cards':[vehicle.ref]});self.assertTrue(result.ok,result.summary)
        self.assertIsNone(self.resolve(s))
        self.assertEqual('hand',s.state.cards[vehicle.object_id].zone)
        self.assertEqual(set(looked)-{vehicle.object_id},set(s.state.players['A'].zones['library'][:6]))
        self.assertEqual([5],[a.mana['GENERIC'] for a in s.engine._activated_abilities(foreign)])
        self.cast(s,'A',s.state.cards[vehicle.object_id]);self.assertIsNone(self.resolve(s))
        self.equip(s,equipment,body,1)
        self.equip(s,equipment,second,5)
        self.crew(s,s.state.cards[vehicle.object_id],pilot,1)
        self.assertIsNone(self.resolve(s))
        self.assertIn('Creature',s.engine._effective_card_data(s.state.cards[vehicle.object_id])['type_line'])
        self.finish(s,private)

    def test_actual_puresteel_draws_then_grants_zero_equip_only_with_three_controlled_artifacts(self):
        s=self.session(1114030)
        source=self.add(s.engine,'Puresteel Paladin',zone='hand')
        equipment=self.add(s.engine,'Heartseeker',zone='hand')
        self.add(s.engine,'Consulate Dreadnought')
        third=self.add(s.engine,'Ornithopter',zone='hand')
        body=self.add(s.engine,'Grizzly Bears')
        self.add(s.engine,'Sol Ring',seat='B')
        destroy=self.add(s.engine,'Disenchant',seat='B',zone='hand')
        for index in range(2):self.add(s.engine,'Generic Bound Plains',seat='B',ref='metalcraft-response-land-'+str(index))
        private=self.add(s.engine,'Counterspell',seat='C',zone='hand',ref='private-C-paladin-card')
        top=self.add(s.engine,'Counterspell',zone='library',ref='paladin-drawn-counterspell')
        library=s.state.players['A'].zones['library'];library[:]=[oid for oid in library if oid!=top.object_id]+[top.object_id]
        self.assert_closed(s,source);self.prepare(s,mana={'C':12,'W':2});self.checkpoint(s)
        self.cast(s,'A',source);self.assertIsNone(self.resolve(s))
        self.cast(s,'A',equipment);self.assertEqual('semantic.choice',self.resolve(s).kind)
        self.reject(s,'D',{'action_id':'choose','choice':'apply'})
        result=s.act('pilot:A',{'action_id':'choose','choice':'apply'});self.assertTrue(result.ok,result.summary)
        self.assertIsNone(self.resolve(s));self.assertEqual('hand',s.state.cards[top.object_id].zone)
        self.assertEqual([5],[a.mana['GENERIC'] for a in s.engine._activated_abilities(s.state.cards[equipment.object_id])])
        self.cast(s,'A',third);self.assertIsNone(self.resolve(s))
        self.assertEqual({0,5},{a.mana['GENERIC'] for a in s.engine._activated_abilities(s.state.cards[equipment.object_id])})
        self.equip(s,equipment,body,0,resolve=False)
        self.cast(s,'B',destroy,[third.ref]);self.resolve_top(s)
        self.assertEqual('graveyard',s.state.cards[third.object_id].zone)
        self.assertEqual([5],[a.mana['GENERIC'] for a in s.engine._activated_abilities(s.state.cards[equipment.object_id])])
        self.replay(s,load=True);self.assertIsNone(self.resolve(s))
        self.assertEqual(body.object_id,s.state.cards[equipment.object_id].attached_to)
        self.finish(s,private)

    def test_actual_announced_crew_keeps_its_cost_and_resolves_after_kotori_leaves(self):
        s=self.session(1114040)
        source=self.add(s.engine,'Kotori, Pilot Prodigy',zone='hand')
        vehicle=self.add(s.engine,'Consulate Dreadnought')
        body=self.add(s.engine,'Grizzly Bears')
        bounce=self.add(s.engine,'Unsummon',seat='B',zone='hand')
        self.add(s.engine,'Island',seat='B')
        private=self.add(s.engine,'Counterspell',seat='C',zone='hand',ref='private-C-removed-kotori')
        self.assert_closed(s,source);self.prepare(s,mana={'C':3,'U':1,'W':1});self.checkpoint(s)
        self.cast(s,'A',source);self.assertIsNone(self.resolve(s))
        self.crew(s,vehicle,body,2)
        self.cast(s,'B',bounce,[source.ref]);self.resolve_top(s)
        self.assertEqual('hand',s.state.cards[source.object_id].zone)
        self.assertEqual([6],[a.crew_threshold for a in s.engine._activated_abilities(s.state.cards[vehicle.object_id])])
        self.replay(s,load=True);self.assertIsNone(self.resolve(s))
        self.assertIn('Creature',s.engine._effective_card_data(s.state.cards[vehicle.object_id])['type_line'])
        self.assertTrue(s.state.cards[body.object_id].tapped)
        self.finish(s,private)

    def test_actual_granted_equip_keeps_its_keyword_discount_while_generic_attachment_does_not(self):
        for index,kind in enumerate(('query','token','ordinary')):
            with self.subTest(kind=kind):
                s=self.session(1114050+index)
                champion=self.add(s.engine,'Fervent Champion')
                private=self.add(s.engine,'Counterspell',seat='C',zone='hand',ref='private-C-equip-discount')
                if kind=='query':
                    source=self.add(s.engine,'Astor, Bearer of Blades')
                    equipment=self.add(s.engine,'Heartseeker')
                    amount,payment=1,0
                elif kind=='token':
                    source=self.add(s.engine,'Mabel, Heir to Cragflame',zone='hand')
                    equipment=None;amount,payment=2,0
                else:
                    source=self.add(s.engine,'Generic Equip Lookalike')
                    equipment=source;amount,payment=1,1
                self.assert_closed(s,source);self.assert_closed(s,champion)
                self.prepare(s,mana={'C':5,'R':1,'W':1});self.checkpoint(s)
                if kind=='token':
                    self.cast(s,'A',source);self.assertIsNone(self.resolve(s))
                    equipment,=[c for c in s.state.cards.values() if c.zone=='battlefield' and c.is_token]
                self.equip(s,equipment,champion,amount,expected_payment=payment)
                self.finish(s,private)
