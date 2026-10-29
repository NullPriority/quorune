"""CR113.2c and pinned Skybonder/Callaphe rulings preserve each granted tax."""

from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT
from test_qualified_zone_event_queries import query_record
from quorune.ability_fragments import StaticComponentSpec,canonical_ability_fragments,ability_fragment_to_dict
from quorune.static_component_scope import GRANTED_STATIC_COST_CAPABILITY,GRANTED_STATIC_COST_EVENT
from quorune.compiler.targeted_spell_costs import targeted_spell_cost_template
from quorune.compiler.generated_program_identity import generated_ability_id
from quorune.errors import GameRuleError
from quorune.rules.granted_static_costs import granted_static_cost_sources
from quorune.rules.casting.targeted_costs import target_cost_fact
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
import test_job_select as actions
import test_public_target_spell_costs as source_actions
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.record import authoritative_state_hash
from scripts.build_test_database import build_fixture_database


INNER='Spells your opponents cast that target this creature cost {2} more to cast.'
SKY='Creatures you control with flying have "'+INNER+'"'
CALL='Creatures and enchantments you control have "Spells your opponents cast that target this permanent cost {1} more to cast."'


class GrantedStaticCostModelTests(unittest.TestCase):
    def fragment(self,key='child'):
        return StaticComponentSpec(key,2,GRANTED_STATIC_COST_EVENT)

    def program(self,**changes):
        value=dict(key='child',provenance={'granted_only':True},active_zone='battlefield',
            event=GRANTED_STATIC_COST_EVENT,effects=(),
            handlers=(targeted_spell_cost_template(INNER,source_name='Granted creature')[1],))
        return SimpleNamespace(**{**value,**changes})

    def recipient(self,ref,controller='A',owner='C',**changes):
        return SimpleNamespace(**{**dict(ref=ref,logical_object_id=ref+':0',controller=controller,
            owner=owner,zone='battlefield',phased_out=False),**changes})

    def host(self,program,fragments,*,trusted=True):
        return SimpleNamespace(active_seats=tuple('ABCD'),semantics=SimpleNamespace(get=lambda key:program if key=='child' else None),
            semantic_program_is_current_trusted=lambda value:trusted,
            _effective_card_data=lambda recipient:{'ability_fragments':fragments.get(recipient.ref,())})

    def test_versioned_grant_reference_is_closed_and_legacy_payload_is_exact(self):
        old={'schema_version':1,'semantic_key':'printed'}
        self.assertEqual(json.dumps(old),json.dumps(StaticComponentSpec.from_dict(old).to_dict()))
        new=self.fragment();self.assertEqual(new,StaticComponentSpec.from_dict(new.to_dict()))
        self.assertEqual(GRANTED_STATIC_COST_CAPABILITY,new.capability_id)
        self.assertIsNone(StaticComponentSpec('printed').capability_id)
        for update in ({'schema_version':True},{'schema_version':1},{'event':'draw'},
                       {'event':None},{'unknown':True}):
            with self.assertRaises(ValueError):StaticComponentSpec.from_dict({**new.to_dict(),**update})
        self.assertEqual(2,len(canonical_ability_fragments((new,new))))

    def test_each_recipient_and_identical_grant_occurrence_is_an_independent_tax(self):
        first=self.recipient('one');second=self.recipient('two')
        marker=ability_fragment_to_dict(self.fragment())
        host=self.host(self.program(),{'one':(marker,marker),'two':(marker,marker)})
        sources=granted_static_cost_sources(host,'B',(first,second))
        self.assertEqual(4,len(sources));self.assertEqual(4,len({source.component_id for source in sources}))
        self.assertEqual(8,target_cost_fact(sources,('one','two'))['generic'])
        self.assertEqual(4,target_cost_fact(sources,('one','one'))['generic'])
        self.assertEqual((),granted_static_cost_sources(host,'A',(first,second)))
        # A is an opponent of a borrowed receiver controlled by B, regardless
        # of the grant provider controller and printed ownership.
        borrowed=self.recipient('one',controller='B',owner='C')
        self.assertEqual(4,target_cost_fact(granted_static_cost_sources(host,'A',(borrowed,)),('one',))['generic'])

    def test_only_current_fragments_and_present_recipients_supply_costs(self):
        marker=ability_fragment_to_dict(self.fragment());printed=ability_fragment_to_dict(StaticComponentSpec('printed'))
        host=self.host(self.program(),{'live':(marker,), 'printed':(printed,), 'phased':(marker,), 'gone':(marker,)})
        rows=(self.recipient('live'),self.recipient('printed'),self.recipient('phased',phased_out=True),self.recipient('gone',zone='hand'))
        self.assertEqual(('live',),tuple(source.source_ref for source in granted_static_cost_sources(host,'D',rows)))
        host._effective_card_data=lambda recipient:{'ability_fragments':()}
        self.assertEqual((),granted_static_cost_sources(host,'D',rows))

    def test_unknown_untrusted_wrong_kind_and_malformed_child_references_fail_closed(self):
        receiver=self.recipient('one');marker=ability_fragment_to_dict(self.fragment())
        wrong_header=deepcopy(self.program().handlers[0]);wrong_header['event']='draw'
        variants=(None,self.program(provenance={'granted_only':False}),self.program(provenance={'granted_only':'true'}),
            self.program(active_zone='hand'),self.program(event='draw'),self.program(effects=({'op':'draw_cards','amount':1},)),
            self.program(handlers=(wrong_header,)))
        for program in variants:
            host=self.host(program,{'one':(marker,)})
            with self.assertRaises(GameRuleError):granted_static_cost_sources(host,'B',(receiver,))
        host=self.host(self.program(),{'one':(marker,)},trusted=False)
        with self.assertRaises(GameRuleError):granted_static_cost_sources(host,'B',(receiver,))

    def test_granted_cost_identity_does_not_change_existing_role_static_ids(self):
        args=dict(kind='granted_static_ability',face_id='front',line=2,static_declaration=True)
        self.assertEqual('static:front:n2:role:0',generated_ability_id(**args,node_id='front:n2:role-static:0'))
        self.assertEqual('static:granted:front:n2',generated_ability_id(**args,node_id='front:n2:granted'))


class GrantedStaticCostCompilerTests(unittest.TestCase):
    def test_query_grants_bind_one_hidden_cost_child_and_its_versioned_fragment(self):
        registry=load_default_capability_registry()
        for text in (SKY,CALL):
            ir=compile_oracle_card(query_record(text),capability_registry=registry)
            self.assertEqual('exact',ir.status,ir.material_residuals)
            outer,child=ir.faces[0].nodes
            self.assertEqual('granted_static_ability',child.kind)
            self.assertEqual(GRANTED_STATIC_COST_EVENT,child.event)
            self.assertIn(GRANTED_STATIC_COST_CAPABILITY,outer.capability_dependencies)
            self.assertIn(GRANTED_STATIC_COST_CAPABILITY,child.capability_dependencies)
            fragments=outer.handlers[0]['modifier']['add_ability_fragments']
            self.assertEqual(1,len(fragments));self.assertEqual(2,fragments[0]['value']['schema_version'])
            self.assertEqual(text,query_record(text).oracle_text[outer.span.start:outer.span.end])
        unsupported='Creatures you control have "Spells you cast that target this creature cost {2} less to cast."'
        self.assertNotEqual('exact',compile_oracle_card(query_record(unsupported),capability_registry=registry).status)
        for record in (replace(query_record('Enchant creature\nEnchanted creature has "'+INNER+'"'),
                               type_line='Enchantment — Aura',keywords=('Enchant',)),
                       query_record('As long as you control an artifact, this creature has "'+INNER+'"')):
            ir=compile_oracle_card(record,capability_registry=registry)
            self.assertEqual('exact',ir.status,ir.material_residuals)
            self.assertEqual(1,sum(node.kind=='granted_static_ability' for node in ir.faces[0].nodes))

    def test_missing_grant_cost_or_query_capability_fails_closed_with_unrelated_control(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index,discover_tests
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text())
        declarations=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json')
        discovered=set(discover_tests(ROOT).values())
        for omitted in (GRANTED_STATIC_COST_CAPABILITY,'casting.cost.targeted_fixed_generic',
                        'continuous.ability.fixed_query_grant','variant.monarch.designate'):
            value=deepcopy(raw)
            next(row for row in value['capabilities'] if row['id']==omitted).update(status='blocked',blockers=['static grant owner omitted'])
            registry=CapabilityRegistry(value)
            index=build_index(registry_value=value,declaration_source=declarations,discovered_test_ids=discovered)
            registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry))
            self.assertEqual(omitted=='variant.monarch.designate',compile_oracle_card(query_record(SKY),capability_registry=registry).status=='exact',omitted)


class GrantedStaticCostNativeTests(unittest.TestCase):
    register_fixture_cards_on_add=True
    session=actions.JobSelectRuntimeTests.session
    add=actions.JobSelectRuntimeTests.add
    prepare=actions.JobSelectRuntimeTests.prepare
    cast=actions.JobSelectRuntimeTests.cast
    checkpoint=actions.JobSelectRuntimeTests.checkpoint
    resolve=actions.JobSelectRuntimeTests.resolve
    replay=actions.JobSelectRuntimeTests.replay
    priority=source_actions.PublicTargetSpellCostNativeTests.priority
    action=source_actions.PublicTargetSpellCostNativeTests.action
    cast_reserved=source_actions.PublicTargetSpellCostNativeTests.cast_reserved

    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'granted-costs.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',
            ROOT/'tests/fixtures/granted-static-cost-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Granted costs',[DeckEntry('Generic Bound Commander',1,'commander'),
            DeckEntry('Generic Bound Plains',24)],['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temporary.cleanup()

    def lands(self,session,seat):
        self.add(session.engine,'Mountain',seat=seat,ref='grant-mountain-'+seat)
        self.add(session.engine,'Plains',seat=seat,ref='grant-plains-'+seat)

    def test_two_actual_identical_providers_supply_four_instances_and_source_removal_updates_them(self):
        session=self.session(113_206_401)
        first=self.add(session.engine,'Jubilant Skybonder',zone='hand',ref='grant-sky-one')
        second=self.add(session.engine,'Jubilant Skybonder',zone='hand',ref='grant-sky-two')
        receiver=self.add(session.engine,'Ornithopter');receiver.owner='C'
        bounce=self.add(session.engine,'Unsummon',zone='hand')
        spell_b=self.add(session.engine,'Martial Glory',seat='B',zone='hand',ref='grant-glory-B')
        spell_c=self.add(session.engine,'Martial Glory',seat='C',zone='hand',ref='grant-glory-C')
        private=self.add(session.engine,'Twiddle',seat='D',zone='hand')
        self.lands(session,'B');self.lands(session,'C')
        session.state.players['B'].mana_pool['C']=8;session.state.players['C'].mana_pool['C']=4
        self.prepare(session,mana={'C':2,'U':5});self.checkpoint(session)
        self.cast_reserved(session,first,reserve={'C':1,'U':3});self.assertIsNone(self.resolve(session))
        self.cast_reserved(session,second,reserve={'U':1});self.assertIsNone(self.resolve(session))
        self.priority(session,'B');offered=self.action(session,'B',spell_b)
        command={'action_id':offered['id'],'targets':[first.ref,receiver.ref],'pay':'auto'}
        unchanged=authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:C',command).ok);self.assertEqual(unchanged,authoritative_state_hash(session.state))
        for seat in 'ABC':self.assertNotIn(private.ref,json.dumps(session.packet('pilot:'+seat,full=True)))
        accepted=session.act('pilot:B',command);self.assertTrue(accepted.ok,accepted.summary)
        self.assertEqual(0,session.state.players['B'].mana_pool['C'])
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.cast(session,'A',bounce,[session.state.cards[second.object_id].ref]);self.assertIsNone(self.resolve(session))
        self.assertEqual('hand',session.state.cards[second.object_id].zone)
        self.cast(session,'C',spell_c,[session.state.cards[first.object_id].ref,session.state.cards[receiver.object_id].ref])
        self.assertEqual(0,session.state.players['C'].mana_pool['C']);self.assertIsNone(self.resolve(session))
        self.replay(session,load=True)

    def test_repeated_target_is_one_tax_and_current_flying_membership_survives_later_grant_source(self):
        session=self.session(113_206_402)
        first=self.add(session.engine,'Jubilant Skybonder',zone='hand',ref='membership-sky-one')
        second=self.add(session.engine,'Jubilant Skybonder',zone='hand',ref='membership-sky-two')
        receiver=self.add(session.engine,'Ornithopter')
        spell=self.add(session.engine,'Martial Glory',seat='B',zone='hand')
        humble=self.add(session.engine,'Humble',seat='D',zone='hand')
        blue=self.add(session.engine,'Generic Blue Pulse',seat='B',zone='hand')
        bounce=self.add(session.engine,'Unsummon',seat='B',zone='hand')
        self.lands(session,'B');session.state.players['B'].mana_pool['C']=2
        session.state.players['D'].mana_pool.update({'C':3,'W':1})
        self.prepare(session,mana={'C':2,'U':4});self.checkpoint(session)
        self.cast_reserved(session,first,reserve={'C':1,'U':2});self.assertIsNone(self.resolve(session))
        self.priority(session,'B');offered=self.action(session,'B',spell)
        self.assertIn(receiver.ref,json.dumps(offered['target_schema']))
        unchanged=authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:B',{'action_id':offered['id'],'targets':[first.ref,receiver.ref],'pay':'auto'}).ok)
        self.assertEqual(unchanged,authoritative_state_hash(session.state))
        accepted=session.act('pilot:B',{'action_id':offered['id'],'targets':[receiver.ref,receiver.ref],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary);self.assertIsNone(self.resolve(session))
        self.assertEqual(0,session.state.players['B'].mana_pool['C'])
        self.cast(session,'D',humble,[session.state.cards[receiver.object_id].ref]);self.assertIsNone(self.resolve(session))
        self.cast(session,'A',second);self.assertIsNone(self.resolve(session))
        self.cast(session,'B',blue);self.assertIsNone(self.resolve(session))
        self.cast(session,'B',bounce,[session.state.cards[receiver.object_id].ref]);self.assertIsNone(self.resolve(session))
        self.assertEqual('hand',session.state.cards[receiver.object_id].zone);self.replay(session,load=True)

    def test_actual_callaphe_grants_once_to_a_creature_enchantment_and_once_per_recipient(self):
        session=self.session(113_206_403)
        source=self.add(session.engine,'Callaphe, Beloved of the Sea',zone='hand')
        other=self.add(session.engine,'Ornithopter')
        spell=self.add(session.engine,'Martial Glory',seat='B',zone='hand')
        self.lands(session,'B');session.state.players['B'].mana_pool['C']=1
        self.prepare(session,mana={'C':1,'U':2});self.checkpoint(session)
        self.cast(session,'A',source);self.assertIsNone(self.resolve(session))
        self.priority(session,'B');offered=self.action(session,'B',spell)
        unchanged=authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:B',{'action_id':offered['id'],'targets':[source.ref,other.ref],'pay':'auto'}).ok)
        self.assertEqual(unchanged,authoritative_state_hash(session.state))
        accepted=session.act('pilot:B',{'action_id':offered['id'],'targets':[source.ref,source.ref],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary);self.assertEqual(0,session.state.players['B'].mana_pool['C'])
        self.assertIsNone(self.resolve(session));self.replay(session,load=True)

    def test_attached_grant_uses_borrowed_receiver_controller_instead_of_aura_provider(self):
        session=self.session(113_206_404)
        receiver=self.add(session.engine,'Memnite',seat='B');receiver.owner='C'
        giver=self.add(session.engine,'Generic Borrowed Tax Aura',zone='hand')
        jump=self.add(session.engine,'Jump',seat='B',zone='hand')
        bounce=self.add(session.engine,'Unsummon',zone='hand')
        private=self.add(session.engine,'Twiddle',seat='D',zone='hand')
        self.add(session.engine,'Island',seat='B')
        self.prepare(session,mana={'W':1,'C':2,'U':1});self.checkpoint(session)
        self.cast_reserved(session,giver,[receiver.ref],reserve={'C':2,'U':1});self.assertIsNone(self.resolve(session))
        self.cast(session,'B',jump,[receiver.ref]);self.assertIsNone(self.resolve(session))
        self.priority(session,'A');offered=self.action(session,'A',bounce)
        unchanged=authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:C',{'action_id':offered['id'],'targets':[receiver.ref],'pay':'auto'}).ok)
        self.assertEqual(unchanged,authoritative_state_hash(session.state))
        for seat in 'ABC':self.assertNotIn(private.ref,json.dumps(session.packet('pilot:'+seat,full=True)))
        self.cast(session,'A',bounce,[session.state.cards[receiver.object_id].ref])
        self.assertEqual(0,sum(session.state.players['A'].mana_pool.values()));self.assertIsNone(self.resolve(session))
        self.assertIn(receiver.object_id,session.state.players['C'].zones['hand'])
        self.assertEqual('graveyard',session.state.cards[giver.object_id].zone);self.replay(session,load=True)

    def test_conditional_child_is_not_printed_and_only_current_condition_supplies_the_tax(self):
        session=self.session(113_206_405)
        source=self.add(session.engine,'Generic Conditional Tax Keeper')
        artifact=self.add(session.engine,'Memnite',zone='hand')
        first=self.add(session.engine,'Jump',seat='B',zone='hand',ref='conditional-jump-one')
        second=self.add(session.engine,'Jump',seat='B',zone='hand',ref='conditional-jump-two')
        for index in range(2):self.add(session.engine,'Island',seat='B',ref='conditional-island-'+str(index))
        self.prepare(session);self.checkpoint(session)
        self.cast(session,'B',first,[source.ref]);self.assertIsNone(self.resolve(session))
        self.cast(session,'A',artifact);self.assertIsNone(self.resolve(session))
        self.priority(session,'B');offered=self.action(session,'B',second)
        self.assertNotIn(source.ref,json.dumps(offered['target_schema']));self.assertIn(artifact.ref,json.dumps(offered['target_schema']))
        self.cast(session,'B',second,[session.state.cards[artifact.object_id].ref]);self.assertIsNone(self.resolve(session))
        self.replay(session,load=True)

    def test_lost_grant_multiplicity_mutant_is_killed_by_actual_duplicate_skybonders(self):
        import quorune.rules.granted_static_costs as owner
        original=owner.canonical_ability_fragments
        def collapsed(values):
            result=[];seen=set()
            for value in original(values):
                if isinstance(value,StaticComponentSpec) and value.schema_version==2:
                    if value.semantic_key in seen:continue
                    seen.add(value.semantic_key)
                result.append(value)
            return tuple(result)
        with patch.object(owner,'canonical_ability_fragments',side_effect=collapsed),self.assertRaises(AssertionError):
            self.test_two_actual_identical_providers_supply_four_instances_and_source_removal_updates_them()


if __name__=='__main__':unittest.main()
