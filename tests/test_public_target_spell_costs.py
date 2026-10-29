"""Pinned Kopala/Strong Back rulings distinguish public subjects and payers."""

from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT
import test_job_select as actions
from test_qualified_zone_event_queries import query_record
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.object_predicate import ObjectQuerySpec
from quorune.object_query import ObjectQueryResult
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.casting.targeted_costs import target_cost_fact
from quorune.semantic_runtime.targeted_spell_costs import _public_target_cost_source
from quorune.targeted_spell_costs import (TargetedSpellCostSpec, TargetedCostSubject,
    PUBLIC_TARGET_COST_CAPABILITY, TARGETED_SPELL_COST_HANDLER)
from quorune.compiler.targeted_spell_costs import targeted_spell_cost_template
from scripts.build_test_database import build_fixture_database


KOPALA = 'Spells your opponents cast that target a Merfolk you control cost {2} more to cast.'
STRONG = 'Aura spells you cast that target enchanted creature cost {3} less to cast.'


class PublicTargetSpellCostModelTests(unittest.TestCase):
    def spec(self, text=KOPALA):
        return TargetedSpellCostSpec.from_dict(targeted_spell_cost_template(text, source_name='Generic Source')[1]['modifier'])

    def test_public_subject_codec_preserves_legacy_versions_and_rejects_widened_headers(self):
        from quorune.replacement.immutable import FrozenMap
        old = (TargetedSpellCostSpec(TargetedCostSubject.SOURCE, 2),
            TargetedSpellCostSpec(TargetedCostSubject.SPELL_TARGET, -1, 2,
                FrozenMap({'zones':['battlefield'],'categories':['permanent'],'types_any':['creature'],'count':1})),
            TargetedSpellCostSpec(TargetedCostSubject.SOURCE, -1, 3, caster_relation='any'))
        for spec in old:
            value = spec.to_dict()
            self.assertEqual(json.dumps(value), json.dumps(TargetedSpellCostSpec.from_dict(value).to_dict()))
            self.assertNotIn('target_subject', value)
        current = self.spec()
        self.assertEqual(current, TargetedSpellCostSpec.from_dict(current.to_dict()))
        for update in ({'schema_version':1},{'generic':0},{'caster_relation':'owner'},
                       {'target_subject':None},{'spell_predicate':ObjectQuerySpec(zones=('hand',)).to_dict()}, {'unknown':1}):
            with self.assertRaises((ValueError,TypeError)):
                TargetedSpellCostSpec.from_dict({**current.to_dict(), **update})
        with self.assertRaises(ValueError): TargetedSpellCostSpec(TargetedCostSubject.PUBLIC_TARGET, 2)

    def test_printed_query_tax_counts_once_across_targets_and_uses_current_control(self):
        def row(ref, controller, owner, types=('creature',)):
            return ObjectQueryResult(ref, ref, 'Merfolk', owner, controller, 'battlefield',
                types=types, subtypes=('merfolk',))
        members = (row('borrowed:1','A','C'),row('kindred:1','A','D',('kindred','enchantment')),
                   row('enemy:1','B','A'))
        provider = SimpleNamespace(ref='provider:1',logical_object_id='provider-object:1',controller='A',attached_to=None)
        host = SimpleNamespace(state=SimpleNamespace(cards={}),_public_object_query_result=lambda member:member)
        program = SimpleNamespace(key='printed-query')
        source = _public_target_cost_source(host,'B',provider,program,0,self.spec(),members,None,None)
        self.assertEqual(('borrowed:1','kindred:1'),source.eligible_target_refs)
        self.assertEqual(2,target_cost_fact((source,),('borrowed:1','kindred:1'))['generic'])
        self.assertEqual(2,target_cost_fact((source,),('borrowed:1','borrowed:1'))['generic'])
        self.assertIsNone(_public_target_cost_source(host,'A',provider,program,0,self.spec(),members,None,None))
        provider.controller='B'
        source = _public_target_cost_source(host,'A',provider,program,0,self.spec(),members,None,None)
        self.assertEqual(('enemy:1',),source.eligible_target_refs)

    def test_aura_reduction_uses_provider_caster_attachment_and_spell_type(self):
        receiver = SimpleNamespace(ref='receiver:1',zone='battlefield',phased_out=False)
        provider = SimpleNamespace(ref='aura:1',logical_object_id='aura-object:1',controller='A',attached_to='receiver')
        host = SimpleNamespace(state=SimpleNamespace(cards={'receiver':receiver}))
        program = SimpleNamespace(key='aura-discount')
        aura = ObjectQueryResult('spell','spell:1','Aura','C','A','hand',types=('enchantment',),subtypes=('aura',))
        with patch('quorune.semantic_runtime.targeted_spell_costs._prospective_spell_subject',return_value=aura):
            source = _public_target_cost_source(host,'A',provider,program,0,self.spec(STRONG),(),object(),None)
            self.assertEqual(('receiver:1',),source.eligible_target_refs)
            self.assertEqual(3,target_cost_fact((source,),('receiver:1',))['generic_reduction'])
            self.assertIsNone(_public_target_cost_source(host,'B',provider,program,0,self.spec(STRONG),(),object(),None))
            receiver.ref='receiver:2'
            later = _public_target_cost_source(host,'A',provider,program,0,self.spec(STRONG),(),object(),None)
            self.assertFalse(later.applies(('receiver:1',))); self.assertTrue(later.applies(('receiver:2',)))
        creature = replace(aura,types=('creature',),subtypes=('spirit',))
        with patch('quorune.semantic_runtime.targeted_spell_costs._prospective_spell_subject',return_value=creature):
            self.assertIsNone(_public_target_cost_source(host,'A',provider,program,0,self.spec(STRONG),(),object(),None))


class PublicTargetSpellCostCompilerTests(unittest.TestCase):
    def test_complete_public_spell_clauses_have_typed_source_spans(self):
        registry = load_default_capability_registry()
        for text in (KOPALA,STRONG,'Spells you cast that target a colorless creature cost {1} less to cast.'):
            ir=compile_oracle_card(query_record(text),capability_registry=registry)
            self.assertEqual('exact',ir.status,ir.material_residuals)
            node,=ir.faces[0].nodes
            self.assertEqual(text,query_record(text).oracle_text[node.span.start:node.span.end])
            self.assertEqual(4,node.handlers[0]['modifier']['schema_version'])
            self.assertIn(PUBLIC_TARGET_COST_CAPABILITY,node.capability_dependencies)
        for text in (KOPALA.replace('{2}','{X}'),KOPALA.replace('{2}','{U}'),
                     KOPALA.replace('a Merfolk you control','a tapped creature'),STRONG+' Draw a card.'):
            self.assertNotEqual('exact',compile_oracle_card(query_record(text),capability_registry=registry).status)

    def test_missing_public_subject_or_base_owner_fails_closed_with_unrelated_control(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index,discover_tests
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        declarations=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json')
        discovered=set(discover_tests(ROOT).values())
        for omitted in (PUBLIC_TARGET_COST_CAPABILITY,'casting.cost.targeted_fixed_generic','variant.monarch.designate'):
            value=deepcopy(raw)
            next(row for row in value['capabilities'] if row['id']==omitted).update(status='blocked',blockers=['public target owner omitted'])
            registry=CapabilityRegistry(value)
            index=build_index(registry_value=value,declaration_source=declarations,discovered_test_ids=discovered)
            registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry))
            self.assertEqual(omitted=='variant.monarch.designate',
                compile_oracle_card(query_record(KOPALA),capability_registry=registry).status=='exact',omitted)


class PublicTargetSpellCostNativeTests(unittest.TestCase):
    register_fixture_cards_on_add=True
    session=actions.JobSelectRuntimeTests.session
    add=actions.JobSelectRuntimeTests.add
    prepare=actions.JobSelectRuntimeTests.prepare
    cast=actions.JobSelectRuntimeTests.cast
    checkpoint=actions.JobSelectRuntimeTests.checkpoint
    resolve=actions.JobSelectRuntimeTests.resolve
    replay=actions.JobSelectRuntimeTests.replay

    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'public-target-cost.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',
                                ROOT/'tests/fixtures/public-target-spell-cost-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Public target cost',[DeckEntry('Generic Bound Commander',1,'commander'),
            DeckEntry('Generic Bound Plains',24)],['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temporary.cleanup()

    def priority(self,session,seat):
        for _ in range(12):
            if session.state.pending_decision.kind=='priority' and session.state.pending_decision.actors==[seat]:return
            result=session.act(session.pending_principals()[0],{'action_id':'pass'});self.assertTrue(result.ok,result.summary)
        self.fail('Expected priority for '+seat)

    def action(self,session,seat,card):
        return next(row for row in session.packet('pilot:'+seat,full=True)['decision']['ctx']['legal']['actions']
            if row['id']=='cast:'+card.ref)

    def cast_reserved(self,session,card,targets=(),*,reserve):
        offered=self.action(session,'A',card)
        result=session.act('pilot:A',{'action_id':offered['id'],'targets':list(targets),
            'pay':'auto','reserve':reserve})
        self.assertTrue(result.ok,result.summary)

    def test_actual_kopala_charges_one_tax_for_two_targets_and_none_for_its_controller(self):
        session=self.session(601_204_301)
        source=self.add(session.engine,'Kopala, Warden of Waves',zone='hand')
        first=self.add(session.engine,'Merfolk of the Pearl Trident');first.owner='C'
        second=self.add(session.engine,'Coral Merfolk')
        self.add(session.engine,'Memnite',seat='B',ref='spell-cost-untaxed-one')
        self.add(session.engine,'Goblin Piker',seat='D',ref='spell-cost-untaxed-two')
        own=self.add(session.engine,'Martial Glory',zone='hand',ref='spell-cost-own-glory')
        spell=self.add(session.engine,'Martial Glory',seat='B',zone='hand',ref='spell-cost-opponent-glory')
        ritual=self.add(session.engine,'Dark Ritual',seat='B',zone='hand')
        self.add(session.engine,'Swamp',seat='B')
        self.add(session.engine,'Mountain',seat='B');self.add(session.engine,'Plains',seat='B')
        private=self.add(session.engine,'Twiddle',seat='D',zone='hand')
        self.prepare(session,mana={'C':1,'U':2,'R':1,'W':1});self.checkpoint(session)
        self.cast_reserved(session,source,reserve={'R':1,'W':1});self.assertIsNone(self.resolve(session))
        self.cast(session,'A',own,[first.ref,second.ref]);self.assertIsNone(self.resolve(session))
        self.priority(session,'B');offered=self.action(session,'B',spell)
        self.assertNotIn(first.ref,json.dumps(offered['target_schema']))
        unchanged=authoritative_state_hash(session.state)
        command={'action_id':offered['id'],'targets':[first.ref,second.ref],'pay':'auto'}
        self.assertFalse(session.act('pilot:C',command).ok);self.assertFalse(session.act('pilot:B',command).ok)
        self.assertEqual(unchanged,authoritative_state_hash(session.state))
        for seat in 'ABC':self.assertNotIn(private.ref,json.dumps(session.packet('pilot:'+seat,full=True)))
        self.cast(session,'B',ritual);self.assertIsNone(self.resolve(session))
        self.priority(session,'B')
        offered=self.action(session,'B',session.state.cards[spell.object_id])
        self.assertIn(first.ref,json.dumps(offered['target_schema']))
        accepted=session.act('pilot:B',{**command,'action_id':offered['id']});self.assertTrue(accepted.ok,accepted.summary)
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertEqual('graveyard',session.state.cards[ritual.object_id].zone)
        self.assertEqual(1,sum(session.state.players['B'].mana_pool.values()));self.replay(session,load=True)

    def test_actual_strong_back_discounts_its_controller_aura_on_an_opponent_receiver(self):
        session=self.session(601_204_302)
        receiver=self.add(session.engine,'Goblin Piker',seat='B')
        other=self.add(session.engine,'Memnite')
        source=self.add(session.engine,'Strong Back',zone='hand')
        aura=self.add(session.engine,'Oakenform',zone='hand')
        enemy_aura=self.add(session.engine,'Eel Umbra',seat='B',zone='hand')
        collar=self.add(session.engine,'Basilisk Collar',seat='B')
        self.add(session.engine,'Island',seat='B')
        self.prepare(session,mana={'C':2,'G':2});self.checkpoint(session)
        self.cast_reserved(session,source,[receiver.ref],reserve={'G':1});self.assertIsNone(self.resolve(session))
        offered=self.action(session,'A',aura)
        self.assertIn(receiver.ref,json.dumps(offered['target_schema']));self.assertNotIn(other.ref,json.dumps(offered['target_schema']))
        unchanged=authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:A',{'action_id':offered['id'],'targets':[other.ref],'pay':'auto'}).ok)
        self.assertEqual(unchanged,authoritative_state_hash(session.state))
        self.cast(session,'A',aura,[session.state.cards[receiver.object_id].ref])
        self.priority(session,'B')
        actions=session.packet('pilot:B',full=True)['decision']['ctx']['legal']['actions']
        self.assertNotIn('cast:'+enemy_aura.ref,[row['id'] for row in actions])
        for seat in 'ACD':self.assertNotIn(enemy_aura.ref,json.dumps(session.packet('pilot:'+seat,full=True)))
        self.assertIsNone(self.resolve(session));self.assertEqual(receiver.object_id,session.state.cards[aura.object_id].attached_to)
        data=session.engine._effective_card_data(session.state.cards[receiver.object_id])
        self.assertEqual((9,8),(int(data['power']),int(data['toughness'])))
        self.replay(session,load=True)

    def test_actual_disturb_back_aura_gets_the_discount_for_its_selected_face(self):
        session=self.session(601_204_303)
        receiver=self.add(session.engine,'Goblin Piker',seat='B')
        source=self.add(session.engine,'Strong Back',zone='hand')
        disturb=self.add(session.engine,"Kindly Ancestor // Ancestor's Embrace",zone='graveyard')
        self.prepare(session,mana={'C':2,'G':1,'W':1});self.checkpoint(session)
        self.cast_reserved(session,source,[receiver.ref],reserve={'W':1});self.assertIsNone(self.resolve(session))
        back="Ancestor's Embrace";action_id=f'cast:{disturb.ref}:face:{back}'
        actions=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        offered=next(row for row in actions if row['id']==action_id)
        accepted=session.act('pilot:A',{'action_id':offered['id'],'face':back,'cost_option':'disturb',
            'targets':[receiver.ref],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary)
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        current=session.state.cards[disturb.object_id]
        self.assertEqual(('battlefield',back,receiver.object_id),(current.zone,current.active_face,current.attached_to))
        self.assertEqual(0,sum(session.state.players['A'].mana_pool.values()));self.replay(session,load=True)

    def test_public_tax_omission_mutant_is_killed_by_actual_kopala(self):
        with patch('quorune.semantic_runtime.targeted_spell_costs._public_target_cost_source',return_value=None),self.assertRaises(AssertionError):
            self.test_actual_kopala_charges_one_tax_for_two_targets_and_none_for_its_controller()


if __name__=='__main__':unittest.main()
