"""CR702.72 originals through normal registration, commands and records."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from common import ROOT
import test_job_select as actions
import test_ring_native_actions as traversal
from test_qualified_zone_event_queries import query_record
from quorune.carddb import CardDatabase
from quorune.card_programs import bind_card_program_runtime
from quorune.card_programs.adapters import compile_best_available_card_program
from quorune.compiler.generated_program_identity import generated_ability_id
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.semantics import SemanticRegistry

from scripts.build_test_database import build_fixture_database


class ChampionCompilerTests(unittest.TestCase):
    def test_missing_link_choice_or_zone_dependency_rejects_with_unrelated_control(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests
        record=replace(query_record('Champion a creature'),type_line='Creature — Human',keywords=('Champion',))
        self.assertEqual('exact',compile_oracle_card(record,
            capability_registry=load_default_capability_registry()).status)
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        declarations=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json')
        discovered=set(discover_tests(ROOT).values())
        for capability in ('keyword.champion.linked_exile','zone.exile.linked_ability_pair',
                           'zone.linked_exile_return.fixed','zone.change.destination_replacement',
                           'trigger.placement.apnap','variant.monarch.designate'):
            value=deepcopy(raw)
            next(row for row in value['capabilities'] if row['id']==capability).update(
                status='blocked',blockers=['omitted tested owner'])
            registry=CapabilityRegistry(value)
            index=build_index(registry_value=value,declaration_source=declarations,discovered_test_ids=discovered)
            registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry))
            with self.subTest(capability=capability):
                self.assertEqual(capability=='variant.monarch.designate',
                    compile_oracle_card(record,capability_registry=registry).status=='exact')

    def test_paired_shapes_reject_targets_extra_fields_and_wrong_trigger(self):
        from quorune.compiler.champion_templates import champion_spec
        from quorune.rules.champion_capability_shapes import champion_node_capabilities, is_closed_champion_program
        effect={'op':'choose_champion_exile','ability_pair':'champion:source:front:n1',
                'spec':champion_spec('Champion a creature').to_dict()}
        def capabilities(value, target=None):
            return champion_node_capabilities(effects=(value,),target_schema=target,mechanic_ids=('champion',))
        self.assertEqual(('keyword.champion.linked_exile',),capabilities(effect))
        for change in ({'ability_pair':'champion:'},{'ability_pair':'champion:source '},
                       {'unexpected':True},{'spec':{'schema_version':2}},{'op':'unknown'}):
            self.assertEqual((),capabilities({**effect,**change}))
        self.assertEqual((),capabilities(effect,target={'kind':'object'}))
        program=SimpleNamespace(effects=(effect,),target_schema=None,coverage=('champion',),
            capability_dependencies=('keyword.champion.linked_exile',),event='permanent.enter.self')
        self.assertTrue(is_closed_champion_program(program))
        program.event='permanent.leave.self'
        self.assertFalse(is_closed_champion_program(program))

    def test_keyword_has_two_distinct_linked_source_spanned_abilities(self):
        registry=load_default_capability_registry()
        for text in ('Champion a creature','Champion a Treefolk or Warrior','Champion an Elemental'):
            card=replace(query_record(text),type_line='Creature — Shapeshifter',keywords=('Champion',))
            ir=compile_oracle_card(card,capability_registry=registry)
            self.assertEqual('exact',ir.status)
            nodes=[node for face in ir.faces for node in face.nodes]
            self.assertEqual(2,len(nodes))
            self.assertEqual({'permanent.enter.self','permanent.leave.self'},{node.event for node in nodes})
            self.assertEqual(1,len({node.effects[0]['ability_pair'] for node in nodes}))
            ids=[generated_ability_id(kind=node.kind,face_id='front',line=node.span.line,
                static_declaration=False,node_id=node.node_id) for node in nodes]
            self.assertEqual(2,len(set(ids)))
        for text in ('Champion another creature','Champion a creature you own',
                     'Champion an unrepresentedkind','Champion a creature and draw a card.'):
            self.assertNotEqual('exact',compile_oracle_card(replace(query_record(text),
                type_line='Creature — Human',keywords=('Champion',)),capability_registry=registry).status)


class ChampionNativeTests(unittest.TestCase):
    register_fixture_cards_on_add=True
    session=actions.JobSelectRuntimeTests.session
    add=actions.JobSelectRuntimeTests.add
    prepare=actions.JobSelectRuntimeTests.prepare
    cast=actions.JobSelectRuntimeTests.cast
    checkpoint=actions.JobSelectRuntimeTests.checkpoint
    resolve=actions.JobSelectRuntimeTests.resolve
    replay=actions.JobSelectRuntimeTests.replay
    until=traversal.RingNativeActionTests.until

    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory()
        path=Path(cls.temporary.name)/'champion.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',
                               ROOT/'tests/fixtures/champion-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Champion originals',[DeckEntry('Generic Bound Commander',1,'commander'),
            DeckEntry('Generic Bound Plains',24)],['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):
        cls.db.close();cls.temporary.cleanup()

    def assert_closed(self,name):
        record=self.db.lookup(name)
        program=compile_best_available_card_program(self.db,record,semantic_registry=SemanticRegistry(),
            capability_registry=self.registry,capability_profile='commander_review')
        self.assertTrue(bind_card_program_runtime(program,capability_registry=self.registry,
            profile='commander_review')['strict_capability_ready'],name)

    def fixture(self,seed,*,name='Changeling Titan',victim=True):
        self.assert_closed(name);session=self.session(seed)
        source=self.add(session.engine,name,zone='hand',ref='champion-source')
        selected=self.add(session.engine,'Generic Bound Body',seat='B',ref='champion-borrowed') if victim else None
        if selected is not None:session.engine.change_control(selected.object_id,'A',reason='pre-action borrowed permanent')
        response=self.add(session.engine,'Unsummon',zone='hand',ref='champion-return-source')
        self.prepare(session,mana={'G':4,'U':4,'W':4,'R':4,'C':20})
        self.checkpoint(session)
        return session,source,selected,response

    def test_native_entry_exiles_borrowed_creature_and_return_uses_its_owner(self):
        session,source,selected,response=self.fixture(702_072_101)
        self.cast(session,'A',source)
        decision=self.resolve(session)
        self.assertEqual('semantic.choice',decision.kind)
        packet=json.dumps(session.packet('pilot:A',full=True)['decision'])
        self.assertIn(selected.ref,packet)
        self.assertIn(response.ref,json.dumps(session.packet('pilot:A',full=True)))
        for seat in ('B','C','D'):
            self.assertNotIn(response.ref,json.dumps(session.packet('pilot:'+seat,full=True)))
        session=self.replay(session,load=True)
        unchanged=authoritative_state_hash(session.state)
        command={'action_id':'choose','cards':[selected.ref]}
        for seat in ('B','C','D'):
            self.assertFalse(session.act('pilot:'+seat,command).ok)
            self.assertEqual(unchanged,authoritative_state_hash(session.state))
        result=session.act('pilot:A',command);self.assertTrue(result.ok,result.summary)
        self.assertIsNone(self.resolve(session))
        self.assertEqual('exile',session.state.cards[selected.object_id].zone)
        self.assertEqual('battlefield',session.state.cards[source.object_id].zone)
        for seat in ('A','B','C','D'):
            projected=json.dumps(session.packet('pilot:'+seat,full=True))
            self.assertIn(selected.ref,projected)
            if seat!='A':self.assertNotIn(response.ref,projected)
        self.cast(session,'A',response,[source.ref])
        self.assertIsNone(self.resolve(session))
        self.assertEqual('hand',session.state.cards[source.object_id].zone)
        returned=session.state.cards[selected.object_id]
        self.assertEqual('battlefield',returned.zone);self.assertEqual('B',returned.controller)
        self.assertIn(returned.object_id,session.state.players['B'].zones['battlefield'])
        self.replay(session,load=True)

    def test_native_decline_and_impossible_choice_sacrifice_only_original_source(self):
        for index,victim in enumerate((True,False)):
            session,source,selected,_response=self.fixture(702_072_102+index,victim=victim)
            self.cast(session,'A',source);decision=self.resolve(session)
            if victim:
                self.assertEqual('semantic.choice',decision.kind)
                result=session.act('pilot:A',{'action_id':'choose','cards':[]})
                self.assertTrue(result.ok,result.summary);self.assertIsNone(self.resolve(session))
                self.assertEqual('battlefield',session.state.cards[selected.object_id].zone)
            else:self.assertIsNone(decision)
            self.assertEqual('graveyard',session.state.cards[source.object_id].zone)
            self.replay(session,load=True)

    def test_early_leaving_trigger_does_not_return_a_later_exiled_card(self):
        session,source,selected,response=self.fixture(702_072_104)
        self.cast(session,'A',source)
        self.until(session,lambda current:current.state.cards[source.object_id].zone=='battlefield')
        self.cast(session,'A',response,[source.ref])
        decision=self.resolve(session)
        self.assertEqual('semantic.choice',decision.kind)
        self.assertEqual('hand',session.state.cards[source.object_id].zone)
        result=session.act('pilot:A',{'action_id':'choose','cards':[selected.ref]})
        self.assertTrue(result.ok,result.summary);self.assertIsNone(self.resolve(session))
        self.assertEqual('exile',session.state.cards[selected.object_id].zone)
        self.replay(session,load=True)

    def test_kindred_permanent_matches_subtype_only_champion(self):
        self.assert_closed('Thoughtweft Trio');session=self.session(702_072_105)
        source=self.add(session.engine,'Thoughtweft Trio',zone='hand',ref='champion-kithkin-source')
        kindred=self.add(session.engine,'Generic Champion Kithkin Kindred',ref='champion-kindred')
        self.prepare(session,mana={'W':6,'C':20});self.checkpoint(session)
        self.cast(session,'A',source);self.assertEqual('semantic.choice',self.resolve(session).kind)
        result=session.act('pilot:A',{'action_id':'choose','cards':[kindred.ref]})
        self.assertTrue(result.ok,result.summary);self.assertIsNone(self.resolve(session))
        self.assertEqual('exile',session.state.cards[kindred.object_id].zone)
        self.replay(session,load=True)

    def test_exile_link_omission_mutant_is_killed(self):
        self.test_native_entry_exiles_borrowed_creature_and_return_uses_its_owner()
        with patch('quorune.champion_state.record_linked_ability_exile',return_value=()),self.assertRaises(AssertionError):
            self.test_native_entry_exiles_borrowed_creature_and_return_uses_its_owner()

    def test_owner_entry_replacement_choice_resumes_only_the_linked_return(self):
        for name in ('Arcbound Worker','Corpsejack Menace','Hardened Scales'):
            self.assert_closed(name)
        session=self.session(702_072_106)
        source=self.add(session.engine,'Changeling Titan',zone='hand',ref='champion-replacement-source')
        selected=self.add(session.engine,'Arcbound Worker',seat='B',ref='champion-worker')
        selected.counters['+1/+1']=1
        session.engine.change_control(selected.object_id,'A',reason='pre-action borrowed Worker')
        self.add(session.engine,'Corpsejack Menace',seat='B',ref='champion-double')
        self.add(session.engine,'Hardened Scales',seat='B',ref='champion-plus')
        response=self.add(session.engine,'Unsummon',zone='hand',ref='champion-replacement-response')
        self.prepare(session,mana={'G':4,'U':4,'C':20});self.checkpoint(session)
        self.cast(session,'A',source);self.assertEqual('semantic.choice',self.resolve(session).kind)
        result=session.act('pilot:A',{'action_id':'choose','cards':[selected.ref]})
        self.assertTrue(result.ok,result.summary);self.assertIsNone(self.resolve(session))
        exile_id=session.state.cards[selected.object_id].logical_object_id
        self.cast(session,'A',response,[source.ref])
        self.assertEqual('replacement.order',self.resolve(session).kind)
        self.assertEqual('exile',session.state.cards[selected.object_id].zone)
        self.assertEqual(['pilot:B'],session.pending_principals())
        session=self.replay(session,load=True)
        options=session.packet('pilot:B',full=True)['decision']['ctx']['options']
        selection=next(row['id'] for row in options if 'champion-plus' in row['id'])
        command={'action_id':'choose','replacement':selection}
        unchanged=authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:A',command).ok)
        self.assertEqual(unchanged,authoritative_state_hash(session.state))
        result=session.act('pilot:B',command);self.assertTrue(result.ok,result.summary)
        self.assertIsNone(self.resolve(session))
        returned=session.state.cards[selected.object_id]
        self.assertEqual('battlefield',returned.zone);self.assertEqual('B',returned.controller)
        self.assertEqual(4,returned.counters['+1/+1'])
        self.assertNotEqual(exile_id,returned.logical_object_id)
        self.assertEqual(2,returned.zone_change_counter)
        self.replay(session,load=True)

    def test_decline_completion_retains_sacrifice_across_destination_replacement_choice(self):
        self.assert_closed('Generic Champion Destination Replacement')
        session=self.session(702_072_107)
        source=self.add(session.engine,'Changeling Titan',zone='hand',ref='champion-decline-source')
        selected=self.add(session.engine,'Generic Bound Body',ref='champion-decline-eligible')
        for seat in ('B','C'):
            self.add(session.engine,'Generic Champion Destination Replacement',seat=seat,ref='champion-destination-'+seat)
        self.prepare(session,mana={'G':4,'C':20});self.checkpoint(session)
        self.cast(session,'A',source);self.assertEqual('semantic.choice',self.resolve(session).kind)
        result=session.act('pilot:A',{'action_id':'choose','cards':[]})
        self.assertTrue(result.ok,result.summary)
        self.assertEqual('replacement.order',session.state.pending_decision.kind)
        self.assertEqual('battlefield',session.state.cards[source.object_id].zone)
        session=self.replay(session,load=True)
        options=session.packet('pilot:A',full=True)['decision']['ctx']['options']
        result=session.act('pilot:A',{'action_id':'choose','replacement':options[0]['id']})
        self.assertTrue(result.ok,result.summary);self.assertIsNone(self.resolve(session))
        self.assertEqual('exile',session.state.cards[source.object_id].zone)
        self.assertEqual(1,session.state.cards[source.object_id].counters['void'])
        self.assertEqual('battlefield',session.state.cards[selected.object_id].zone)
        self.replay(session,load=True)
