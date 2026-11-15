"""CR701.9/121 and original rulings: any hand subset, then its draw plus zero/one."""
from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from test_qualified_zone_event_queries import query_record
from quorune.carddb import CardDatabase
from quorune.compiler.discard_result_templates import discard_result_draw_template
from quorune.deck import DeckDefinition,DeckEntry
from quorune.discard_result_model import VARIABLE_SUBSET_DISCARD_DRAW_CAPABILITY as CAPABILITY,validate_variable_subset_discard_draw
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.discard_result_shapes import discard_result_node_capabilities
from quorune.semantic_choices.library_and_hand import DiscardDrawChoiceHandler
from quorune.semantic_choices.model import SemanticChoiceError
from quorune.day_night import set_day_night_designation
from quorune.day_night_model import validate_day_night_designation
from quorune.compiler.day_night_nodes import fixed_day_night_designation_template
from quorune.rules.fixed_rule_action_coverage import fixed_day_night_designation_capabilities
from scripts.build_test_database import build_fixture_database


class VariableSubsetDiscardDrawCompilerTests(unittest.TestCase):
    def test_explicit_day_night_designations_are_closed_and_use_the_existing_world_owner(self):
        registry=load_default_capability_registry()
        for text in ('It becomes day.','It becomes night.'):
            compiled=fixed_day_night_designation_template(text)
            self.assertTrue(fixed_day_night_designation_capabilities(effects=compiled[1],target_schema=None,mechanic_ids=compiled[3]))
            self.assertEqual('exact',compile_oracle_card(replace(query_record(text),type_line='Sorcery'),capability_registry=registry).status)
        for changed in ({'designation':'dusk'},{'designation':True},{'actor':'A'}):
            with self.assertRaises(ValueError):validate_day_night_designation({'op':'set_day_night','designation':'night',**changed})
        self.assertIsNone(fixed_day_night_designation_template('It becomes night. Rewrite history.'))
        for previous in (None,'day','night'):
            observed=[];state=SimpleNamespace(day_night=previous)
            host=SimpleNamespace(state=state,active_seats=('A','B'),_log=lambda *args,**kw:observed.append(args))
            with patch('quorune.day_night.synchronize_day_night',side_effect=lambda owner,**kw:(owner.state.day_night,())):
                self.assertEqual(('night',()),set_day_night_designation(host,'night',reason='explicit effect'))
            self.assertEqual('night',state.day_night);self.assertEqual(int(previous!='night'),len(observed))

    def test_variable_hand_bound_and_offset_are_closed_across_source_contexts(self):
        registry=load_default_capability_registry()
        for prefix,kind in (('', 'Sorcery'),('When this creature dies, ', 'Creature'),('{2}: ', 'Artifact')):
            for extra in ('',' plus one'):
                ir=compile_oracle_card(replace(query_record(prefix+'Discard any number of cards, then draw that many cards'+extra+'.'),type_line=kind),capability_registry=registry)
                self.assertEqual('exact',ir.status,ir.material_residuals)
                effect=ir.faces[0].nodes[0].effects[0]
                self.assertEqual('hand_size',effect['maximum']);self.assertEqual(int(bool(extra)),effect['draw_offset'])
        for text in ('Each player discards any number of cards, then draws that many cards.',
                'Discard any number of creature cards, then draw that many cards.',
                'Discard any number of cards at random, then draw that many cards.',
                'Discard any number of cards, then draw that many cards. Rewrite history.'):
            self.assertIsNone(discard_result_draw_template(text))

    def test_modern_hand_selection_has_no_fixed_ceiling_and_empty_offset_survives(self):
        handler=DiscardDrawChoiceHandler()
        effect={'op':'discard_draw_up_to','schema_version':3,'maximum':'hand_size','draw_offset':1}
        rows=tuple(SimpleNamespace(ref=f'hand-{index:03}',printed_name='private') for index in reversed(range(117)))
        ctx=SimpleNamespace(actor='A',stack_ref='stack',stack_label='variable',query=SimpleNamespace(objects=lambda **kw:rows))
        prepared=handler.prepare(effect,ctx)
        self.assertEqual(117,prepared.request.choice.maximum)
        self.assertEqual(tuple(sorted(row.ref for row in rows)),tuple(prepared.continuation_effect['_legal_refs']))
        empty=SimpleNamespace(actor='A',stack_ref='stack',stack_label='empty',query=SimpleNamespace(objects=lambda **kw:()))
        for offset in (0,1):
            result=handler.prepare({**effect,'draw_offset':offset},empty)
            self.assertIsNone(result.request)
            self.assertEqual(offset,sum(row['count'] for row in result.auto_continue.prepend_effects))
        completion=handler.complete(SimpleNamespace(effect=prepared.continuation_effect),{'cards':[]},ctx.query)
        self.assertEqual(1,completion.prepend_effects[0]['count'])
        for changed in ({'draw_offset':True},{'draw_offset':-1},{'draw_offset':2},{'maximum':2},{'schema_version':True},{'extra':'open'}):
            with self.subTest(changed=changed),self.assertRaises(ValueError):validate_variable_subset_discard_draw({**effect,**changed})
        self.assertEqual((),discard_result_node_capabilities(effects=({**effect,'draw_offset':True},),target_schema=None,mechanic_ids=('variable-subset-discard-draw',)))

    def test_verified_variable_owner_withdrawal_retains_fixed_subset_and_plain_draw(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index,discover_tests
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        next(row for row in raw['capabilities'] if row['id']==CAPABILITY).update(status='blocked',blockers=['variable owner withdrawn'])
        registry=CapabilityRegistry(raw);index=build_index(registry_value=raw,declaration_source=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json'),discovered_test_ids=set(discover_tests(ROOT).values()));registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry))
        for text,expected in (('Discard any number of cards, then draw that many cards plus one.',False),('Discard up to two cards, then draw that many cards.',True),('Draw a card.',True)):
            self.assertEqual(expected,compile_oracle_card(replace(query_record(text),type_line='Sorcery'),capability_registry=registry).status=='exact')


class VariableSubsetDiscardDrawNativeTests(InheritedNativeFixture,unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory()
        base=json.loads((ROOT/'tests/fixtures/bound-effect-program-cards.json').read_text(encoding='utf-8'));base['cards']=[row for row in base['cards'] if row['name']=='Generic Bound Plains'];base['rulings']=[]
        basic=Path(cls.temporary.name)/'basic.json';basic.write_text(json.dumps(base),encoding='utf-8',newline='\n')
        path=Path(cls.temporary.name)/'variable-subset.sqlite3';build_fixture_database([basic,ROOT/'tests/fixtures/variable-subset-discard-draw-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry();cls.deck=DeckDefinition('Variable subset',[DeckEntry('Sliver Hivelord',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Sliver Hivelord'])

    def choose(self,session,refs,private):
        self.assertEqual('semantic.choice',session.state.pending_decision.kind);self.assertEqual(['A'],session.state.pending_decision.actors)
        legal=session.packet('pilot:A',full=True)['decision']['ctx']['legal_actions'][0]['choice_schema']['legal_refs']
        self.assertTrue(set(refs)<=set(legal));before=authoritative_state_hash(session.state)
        for actor,cards in (('B',refs),('A',[private.ref]),('A',[refs[0],refs[0]]) if refs else ('B',[])):
            self.assertFalse(session.act('pilot:'+actor,{'action_id':'choose','cards':cards}).ok);self.assertEqual(before,authoritative_state_hash(session.state))
        for seat in 'ABD':self.assertNotIn(private.ref,json.dumps(session.packet('pilot:'+seat,full=True)))
        session=self.replay(session,load=True);result=session.act('pilot:A',{'action_id':'choose','cards':refs});self.assertTrue(result.ok,result.summary);self.assertIsNone(self.resolve(session));return session

    def colossus_witness(self,seed):
        session=self.session(seed);source=self.add(session.engine,'Colossus of the Blood Age');source.owner='B'
        kill=self.add(session.engine,'Infernal Grasp',zone='hand');private=self.add(session.engine,'Counterspell',seat='C',zone='hand',ref='variable-private-hand')
        self.assert_closed(session,source);self.assert_closed(session,kill)
        selected=[session.state.cards[oid].ref for oid in session.state.players['A'].zones['hand'] if oid!=kill.object_id][:3]
        self.assertEqual(3,len(selected));self.prepare(session,mana={'C':1,'B':1});self.checkpoint(session);self.cast(session,'A',kill,targets=[source.ref])
        self.assertEqual('semantic.choice',self.resolve(session).kind);self.assertEqual('graveyard',session.state.cards[source.object_id].zone)
        hand=len(session.state.players['A'].zones['hand']);library=len(session.state.players['A'].zones['library']);other={seat:len(session.state.players[seat].zones['hand']) for seat in 'BCD'}
        session=self.choose(session,selected,private)
        self.assertEqual(hand+1,len(session.state.players['A'].zones['hand']),'printed extra card must survive the discard count')
        self.assertEqual(library-4,len(session.state.players['A'].zones['library']))
        self.assertEqual(other,{seat:len(session.state.players[seat].zones['hand']) for seat in 'BCD'})
        for ref in selected:self.assertEqual('graveyard',next(card.zone for card in session.state.cards.values() if card.ref==ref))
        self.replay(session,load=True)

    def test_actual_borrowed_colossus_death_discards_more_than_two_then_draws_extra(self):
        self.colossus_witness(988100)

    def night_witness(self,seed,*,empty):
        session=self.session(seed)
        if empty:
            for oid in tuple(session.state.players['A'].zones['hand']):session.engine.move_card(oid,'library',log=False,semantic_events=False)
        source=self.add(session.engine,'Into the Night',zone='hand');private=self.add(session.engine,'Counterspell',seat='C',zone='hand',ref='night-private')
        bound=self.add(session.engine,'Tireless Hauler // Dire-Strain Brawler',zone='hand')
        self.assert_closed(session,bound)
        self.assert_closed(session,source);self.prepare(session,mana={'C':10,'G':2,'R':2,'B':2,'U':2});self.checkpoint(session)
        self.cast(session,'A',bound);self.assertIsNone(self.resolve(session))
        self.assertEqual('day',session.state.day_night)
        self.cast(session,'A',source)
        identity=session.state.cards[bound.object_id].logical_object_id
        hand=len(session.state.players['A'].zones['hand']);library=len(session.state.players['A'].zones['library'])
        decision=self.resolve(session)
        self.assertEqual('Dire-Strain Brawler',session.state.cards[bound.object_id].active_face,
            'night face must follow the explicit designation')
        self.assertEqual(identity,session.state.cards[bound.object_id].logical_object_id)
        if empty:self.assertIsNone(decision)
        else:session=self.choose(session,[],private)
        self.assertEqual(hand+1,len(session.state.players['A'].zones['hand']))
        self.assertEqual(library-1,len(session.state.players['A'].zones['library']))
        self.assertEqual('night',session.state.day_night)
        self.replay(session,load=True)

    def test_actual_into_night_zero_and_empty_hand_both_draw_the_extra_card(self):
        for empty in (False,True):
            with self.subTest(empty=empty):self.night_witness(988200+int(empty),empty=empty)

    def test_fresh_omitted_extra_draw_mutant_is_killed(self):
        original=DiscardDrawChoiceHandler.complete;observed=[]
        def no_extra(handler,continuation,response,query):
            result=original(handler,continuation,response,query)
            if continuation.effect.get('schema_version')==3 and continuation.effect.get('draw_offset')==1:
                observed.append(True);result=replace(result,prepend_effects=tuple({**dict(effect),'count':effect['count']-1} for effect in result.prepend_effects))
            return result
        with patch.object(DiscardDrawChoiceHandler,'complete',no_extra):
            with self.assertRaisesRegex(AssertionError,'printed extra card must survive'):self.colossus_witness(988300)
        self.assertTrue(observed)

    def test_fresh_omitted_designation_mutant_is_killed_before_the_private_draw(self):
        from quorune.effect_runtime import state_and_permissions
        observed=[]
        def skipped(host,effect,**kwargs):
            observed.append(effect['designation'])
            return host.state.day_night,()
        with patch.dict(state_and_permissions.HANDLERS,{'set_day_night':skipped}):
            with self.assertRaisesRegex(AssertionError,'night face must follow'):
                self.night_witness(988400,empty=False)
        self.assertTrue(observed)
