"""Existing keyword programs granted through current attachment/query scopes."""
from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT
import test_job_select as actions
import test_ring_native_actions as traversal
from test_qualified_zone_event_queries import query_record
from quorune.ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
from quorune.carddb import CardDatabase
from quorune.card_programs import bind_card_program_runtime
from quorune.card_programs.adapters import compile_best_available_card_program
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.semantics import SemanticRegistry
from scripts.build_test_database import build_fixture_database


class GrantedKeywordEventCompilerTests(unittest.TestCase):
    def record(self,text):
        return replace(query_record(text),type_line='Artifact — Equipment',keywords=())

    def test_one_keyword_reuses_the_existing_complete_inner_event_program(self):
        registry=load_default_capability_registry()
        for text,event,op in (
            ('Equipped creature gets +2/+0 and has afflict 1.','creature.becomes_blocked','lose_life'),
            ('Enchanted creature gets +10/+10 and has trample and annihilator 2.','creature.attacks','choose_cards_apnap'),
            ('Artifact creatures you control have afflict 3.','creature.becomes_blocked','lose_life'),
            ('As long as you control an artifact, creatures you control have afflict 2.','creature.becomes_blocked','lose_life'),
            ('Creatures you control have firebending 2.','creature.attacks','mana'),
            ('Creatures you control have ingest.','damage.dealt.self','exile_top_library_card'),
            ('Creatures you control have mobilize 1.','creature.attacks','choose_attacking_token_destinations'),
            ('Creatures you control have soulshift 2.','permanent.graveyard.self','offer_optional_effect'),
            ('Creatures you control have exploit.','permanent.enter.self','exploit'),
            ('Creatures you control have provoke.','creature.attacks','offer_optional_effect'),
        ):
            ir=compile_oracle_card(self.record(text),capability_registry=registry)
            self.assertEqual('exact',ir.status,text)
            nodes=[node for face in ir.faces for node in face.nodes]
            inner=next(node for node in nodes if node.kind=='granted_triggered_ability')
            outer=next(node for node in nodes if node.kind=='static_ability')
            self.assertEqual(event,inner.event);self.assertEqual(op,inner.effects[0]['op'])
            self.assertEqual(outer.span,inner.span)
            self.assertEqual(text,outer.text)
            self.assertIn('continuous.ability.fixed_keyword_event_grant',outer.capability_dependencies)
            self.assertNotIn(CURRENT_ABILITY_FRAGMENT_COVERAGE,inner.runtime_coverage)
        for text in ('Equipped creature has afflict X.','Equipped creature has afflict 0.',
                     'Equipped creature has afflict 21.','Equipped creature has afflict 2 and draw a card.',
                     'Equipped creature has afflict 1 and annihilator 2.'):
            self.assertNotEqual('exact',compile_oracle_card(self.record(text),capability_registry=registry).status)

    def test_grant_lowering_omission_is_killed(self):
        from quorune.compiler import granted_keyword_event_nodes
        card=self.record('Equipped creature has afflict 1.')
        registry=load_default_capability_registry()
        self.assertEqual('exact',compile_oracle_card(card,capability_registry=registry).status)
        with patch.object(granted_keyword_event_nodes,'compile_granted_keyword_event_nodes',return_value=None):
            self.assertNotEqual('exact',compile_oracle_card(card,capability_registry=registry).status)

    def test_dependency_fail_closed_uses_verified_evidence_and_an_unrelated_control(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index,discover_tests
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        declarations=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json')
        discovered=set(discover_tests(ROOT).values())
        card=self.record('Equipped creature has afflict 1.')
        for omitted in ('continuous.ability.fixed_keyword_event_grant','trigger.keyword.afflict.fixed',
                        'life.change.effect','trigger.placement.apnap','variant.monarch.designate'):
            value=deepcopy(raw)
            next(row for row in value['capabilities'] if row['id']==omitted).update(status='blocked',blockers=['tested omitted owner'])
            registry=CapabilityRegistry(value)
            index=build_index(registry_value=value,declaration_source=declarations,discovered_test_ids=discovered)
            registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry))
            with self.subTest(omitted=omitted):
                self.assertEqual(omitted=='variant.monarch.designate',
                    compile_oracle_card(card,capability_registry=registry).status=='exact')


class GrantedKeywordEventNativeTests(unittest.TestCase):
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
        path=Path(cls.temporary.name)/'granted-keywords.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',
                                ROOT/'tests/fixtures/granted-keyword-event-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Granted keyword events',[DeckEntry('Generic Bound Commander',1,'commander'),
            DeckEntry('Generic Bound Plains',24)],['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):
        cls.db.close();cls.temporary.cleanup()

    def assert_closed(self,name):
        program=compile_best_available_card_program(self.db,self.db.lookup(name),semantic_registry=SemanticRegistry(),
            capability_registry=self.registry,capability_profile='commander_review')
        self.assertTrue(bind_card_program_runtime(program,capability_registry=self.registry,
            profile='commander_review')['strict_capability_ready'],name)

    def afflict_fixture(self,seed,*,name='Khenra Eternal'):
        self.assert_closed(name);session=self.session(seed)
        source=self.add(session.engine,name,ref='granted-keyword-attacker')
        blocker=self.add(session.engine,'Generic Bound Body',seat='C',ref='granted-keyword-borrowed-blocker')
        session.engine.change_control(blocker.object_id,'B',reason='pre-action borrowed blocker')
        response=self.add(session.engine,'Unsummon',zone='hand',ref='granted-keyword-source-response')
        self.add(session.engine,'Island',ref='granted-keyword-response-land')
        private={seat:self.add(session.engine,'Generic Bound Growth',seat=seat,zone='hand',ref='granted-keyword-private-'+seat)
                 for seat in 'ABCD'}
        return session,source,blocker,response,private

    def attack(self,session,source,defender='B',*,actor='A'):
        self.until(session,lambda current:current.state.pending_decision is not None
                   and current.state.pending_decision.kind=='combat.attackers' and current.state.active_player==actor)
        result=session.act('pilot:'+actor,{'action_id':'attack','attackers':{source.ref:defender}})
        self.assertTrue(result.ok,result.summary)

    def block(self,session,source,blocker):
        self.until(session,lambda current:current.state.pending_decision is not None
                   and current.state.pending_decision.kind=='combat.blockers' and current.state.pending_decision.actors==['B'])
        command={'action_id':'block','blocks':{blocker.ref:source.ref}}
        unchanged=authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:C',command).ok)
        self.assertEqual(unchanged,authoritative_state_hash(session.state))
        result=session.act('pilot:B',command);self.assertTrue(result.ok,result.summary)

    def test_two_equipment_grants_add_instances_and_survive_source_departure(self):
        self.assert_closed('Dagger of the Worthy')
        session,source,blocker,response,private=self.afflict_fixture(702_130_201)
        equipment=[self.add(session.engine,'Dagger of the Worthy',ref='granted-keyword-dagger-'+str(index)) for index in range(2)]
        self.prepare(session,mana={'U':4,'C':20});self.checkpoint(session)
        for dagger in equipment:
            offered=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
            action=next(row for row in offered if row.get('source')==dagger.ref and row['id'].startswith('activate:'))
            result=session.act('pilot:A',{'action_id':action['id'],'targets':[source.ref],'pay':'auto'})
            self.assertTrue(result.ok,result.summary);self.assertIsNone(self.resolve(session))
        self.attack(session,source);self.block(session,source,blocker)
        self.until(session,lambda current:len([item for item in current.state.stack if item.source_object_id==source.object_id])==3)
        session=self.replay(session,load=True)
        for viewer in 'ABCD':
            projected=json.dumps(session.packet('pilot:'+viewer,full=True))
            for owner,card in private.items():
                if owner==viewer:self.assertIn(card.ref,projected)
                else:self.assertNotIn(card.ref,projected)
        self.cast(session,'A',response,[source.ref]);self.assertIsNone(self.resolve(session))
        self.assertEqual('hand',session.state.cards[source.object_id].zone)
        self.assertEqual({'A':40,'B':37,'C':40,'D':40},{seat:session.state.players[seat].life for seat in 'ABCD'})
        self.replay(session,load=True)

    def test_query_self_grant_creates_exactly_one_instance(self):
        session,source,blocker,_response,_private=self.afflict_fixture(702_130_202,name='Cyberman Patrol')
        self.prepare(session);self.checkpoint(session);self.attack(session,source);self.block(session,source,blocker)
        self.assertIsNone(self.resolve(session));self.assertEqual(37,session.state.players['B'].life)
        self.replay(session,load=True)

    def test_annihilator_grant_keeps_defender_after_provider_removal(self):
        self.assert_closed('Eldrazi Conscription');session=self.session(702_086_201)
        source=self.add(session.engine,'Generic Bound Body',seat='B',ref='granted-annihilator-attacker')
        aura=self.add(session.engine,'Eldrazi Conscription',zone='hand',ref='granted-annihilator-provider')
        response=self.add(session.engine,'Disenchant',zone='hand',ref='granted-annihilator-response')
        chosen=self.add(session.engine,'Generic Bound Body',seat='C',ref='granted-annihilator-owned-C')
        session.engine.change_control(chosen.object_id,'D',reason='pre-action borrowed sacrifice permanent')
        other=self.add(session.engine,'Generic Bound Body',seat='D',ref='granted-annihilator-other')
        for index in range(2):self.add(session.engine,'Generic Bound Plains',seat='D',ref='granted-annihilator-land-'+str(index))
        private=self.add(session.engine,'Generic Bound Growth',seat='D',zone='hand',ref='granted-annihilator-private-D')
        for index in range(2):self.add(session.engine,'Generic Bound Plains',ref='granted-annihilator-response-land-'+str(index))
        starting_card=session.state.players['A'].zones['hand'][0]
        session.engine.move_card(starting_card,'graveyard',reason='pre-action response hand-size fixture')
        self.prepare(session,mana={'W':4,'C':20});self.checkpoint(session)
        self.cast(session,'A',aura,[source.ref]);self.assertIsNone(self.resolve(session))
        self.attack(session,source,'D',actor='B')
        self.until(session,lambda current:current.state.pending_decision is not None
                   and current.state.pending_decision.kind=='priority' and current.state.pending_decision.actors==['A'])
        self.assertEqual('B',next(item.controller for item in session.state.stack if item.source_object_id==source.object_id))
        self.cast(session,'A',response,[aura.ref])
        self.assertEqual('choice.apnap',self.resolve(session).kind)
        self.assertEqual('graveyard',session.state.cards[aura.object_id].zone)
        self.assertEqual(['D'],session.state.pending_decision.actors)
        session=self.replay(session,load=True);unchanged=authoritative_state_hash(session.state)
        command={'action_id':'choose','cards':[chosen.ref,other.ref]}
        for seat in 'ABC':
            packet=session.packet('pilot:'+seat,full=True)
            self.assertIsNone(packet['decision']);self.assertNotIn(private.ref,json.dumps(packet))
            self.assertFalse(session.act('pilot:'+seat,command).ok)
            self.assertEqual(unchanged,authoritative_state_hash(session.state))
        self.assertFalse(session.act('pilot:D',{'action_id':'choose','cards':[chosen.ref,source.ref]}).ok)
        self.assertEqual(unchanged,authoritative_state_hash(session.state))
        result=session.act('pilot:D',command);self.assertTrue(result.ok,result.summary)
        self.assertIsNone(self.resolve(session))
        self.assertIn(chosen.object_id,session.state.players['C'].zones['graveyard'])
        self.assertEqual('graveyard',session.state.cards[other.object_id].zone)
        self.assertEqual(40,session.state.players['D'].life)
        self.replay(session,load=True)

    def test_conditional_grant_reads_current_state_before_the_event(self):
        self.assert_closed('Generic Conditional Afflict Provider')
        for index,remove in enumerate((False,True)):
            session,source,blocker,response,_private=self.afflict_fixture(702_130_203+index)
            self.add(session.engine,'Generic Conditional Afflict Provider',ref='conditional-keyword-provider')
            artifact=self.add(session.engine,'Ornithopter',ref='conditional-keyword-artifact')
            self.prepare(session,mana={'U':4,'C':20});self.checkpoint(session);self.attack(session,source)
            if remove:
                self.cast(session,'A',response,[artifact.ref]);self.assertIsNone(self.resolve(session))
            self.block(session,source,blocker)
            expected=1 if remove else 2
            self.until(session,lambda current:len([item for item in current.state.stack
                       if item.source_object_id==source.object_id])==expected)
            self.assertIsNone(self.resolve(session))
            self.assertEqual(39 if remove else 37,session.state.players['B'].life)
            self.replay(session,load=True)
