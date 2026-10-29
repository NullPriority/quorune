"""CR613.8: static absent-keyword grants read independent layer-six changes."""

from copy import deepcopy
from dataclasses import replace
import json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch

from common import ROOT
from test_qualified_zone_event_queries import query_record
import test_enchanted_player_statics as native
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.object_predicate import ObjectQuerySpec
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.semantic_runtime.negative_keyword_grants import DisjointNegativeKeywordGrantHandler,NEGATIVE_KEYWORD_GRANT_CAPABILITY as CAPABILITY
from scripts.build_test_database import build_fixture_database

TEXT='Creatures without flying have reach.'


class DisjointNegativeKeywordCompilerTests(unittest.TestCase):
    def test_complete_disjoint_absence_grants_preserve_source_spans_and_reject_self_changes(self):
        from quorune.compiler.continuous_templates import fixed_query_keyword_grant_handler
        self.assertIsNone(fixed_query_keyword_grant_handler(TEXT))
        registry=load_default_capability_registry()
        for text in (TEXT,'Creatures you control without flying have reach.','Creatures without vigilance have haste.'):
            ir=compile_oracle_card(query_record(text),capability_registry=registry,capability_profile='commander_review')
            self.assertEqual('exact',ir.status,(text,ir.material_residuals));node,=ir.faces[0].nodes
            self.assertEqual((text,0,len(text)),(node.text,node.span.start,node.span.end));self.assertIn(CAPABILITY,node.capability_dependencies)
            DisjointNegativeKeywordGrantHandler().validate(node.handlers[0])
        for text in ('Creatures without flying have flying.','Creatures without flying have flying and reach.',
                'Creatures without flying have ward {2}.','Creatures without chosen abilities have reach.',TEXT+' Draw a card.'):
            self.assertNotEqual('exact',compile_oracle_card(query_record(text),capability_registry=registry).status,text)

    def test_absence_descriptor_rejects_wrong_identity_ordinary_presence_and_interfering_addition(self):
        ir=compile_oracle_card(query_record(TEXT),capability_registry=load_default_capability_registry())
        descriptor=ir.faces[0].nodes[0].handlers[0];handler=DisjointNegativeKeywordGrantHandler()
        for mutate in (lambda v:v.update(schema_version=True),lambda v:v.update(extra=None),
                lambda v:v['modifier'].update(add_abilities=['Flying']),
                lambda v:v['condition'].update(predicate=ObjectQuerySpec(zones=('battlefield',),types_all=('creature',),keywords_all=('flying',)).to_dict()),
                lambda v:v['condition']['predicate'].update(controller='A')):
            value=deepcopy(descriptor);mutate(value)
            with self.assertRaises(ValueError):handler.validate(value)

    def test_verified_absence_and_query_owner_withdrawals_keep_unrelated_positive_support(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index,discover_tests
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'));decl=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json');known=set(discover_tests(ROOT).values())
        for owner in (CAPABILITY,'continuous.ability.fixed_query_keyword_grant','combat.block.reach','variant.monarch.designate'):
            value=deepcopy(raw);next(r for r in value['capabilities'] if r['id']==owner).update(status='blocked',blockers=['Absence grant owner withdrawn'])
            registry=CapabilityRegistry(value);index=build_index(registry_value=value,declaration_source=decl,discovered_test_ids=known)
            registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry))
            ir=compile_oracle_card(query_record(TEXT),capability_registry=registry,capability_profile='commander_review')
            self.assertEqual(owner=='variant.monarch.designate',ir.status=='exact',owner)


class NegativeKeywordDependencyTests(unittest.TestCase):
    def test_absence_dependency_orders_later_addition_and_removal_before_query(self):
        from quorune.continuous_effects import CharacteristicState,evaluate_continuous_effects
        from quorune.continuous_effect_model import ContinuousEffect,ContinuousOperation,Layer
        absence=ContinuousEffect('absence','giver',Layer.ABILITY,'6',1,(ContinuousOperation('add_ability','Reach'),),
            applies=ObjectQuerySpec(zones=('battlefield',),types_all=('creature',),keywords_none=('flying',)))
        flight=ContinuousEffect('flight','aura',Layer.ABILITY,'6',2,(ContinuousOperation('add_ability','Flying'),))
        result=evaluate_continuous_effects(CharacteristicState('receiver',card_types={'Creature'}),(absence,flight))
        self.assertEqual(['Flying'],result.characteristics['abilities']);self.assertEqual(('flight',),result.applied_effects);self.assertEqual(('absence',),result.inapplicable_effects)
        removal=ContinuousEffect('remove','blanker',Layer.ABILITY,'6',3,(ContinuousOperation('remove_all_abilities'),))
        result=evaluate_continuous_effects(CharacteristicState('receiver',card_types={'Creature'},abilities=['Flying']),(absence,removal))
        self.assertEqual(['Reach'],result.characteristics['abilities']);self.assertEqual(('remove','absence'),result.applied_effects)

    def test_omitted_absence_dependency_mutant_is_killed(self):
        import quorune.continuous_effects as effects
        self.test_absence_dependency_orders_later_addition_and_removal_before_query()
        with patch.object(effects,'_ability_presence_dependency_ids',return_value=frozenset()),self.assertRaises(AssertionError):
            self.test_absence_dependency_orders_later_addition_and_removal_before_query()


class DisjointNegativeKeywordNativeTests(unittest.TestCase):
    register_fixture_cards_on_add=True
    session=native.EnchantedPlayerStaticNativeTests.session
    add=native.EnchantedPlayerStaticNativeTests.add
    prepare=native.EnchantedPlayerStaticNativeTests.prepare
    cast=native.EnchantedPlayerStaticNativeTests.cast
    cast_for=native.EnchantedPlayerStaticNativeTests.cast_for
    action=native.EnchantedPlayerStaticNativeTests.action
    priority=native.EnchantedPlayerStaticNativeTests.priority
    checkpoint=native.EnchantedPlayerStaticNativeTests.checkpoint
    resolve=native.EnchantedPlayerStaticNativeTests.resolve
    replay=native.EnchantedPlayerStaticNativeTests.replay
    assert_closed=native.EnchantedPlayerStaticNativeTests.assert_closed
    until=native.EnchantedPlayerStaticNativeTests.until

    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'negative-keywords.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',ROOT/'tests/fixtures/disjoint-negative-keyword-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Absent keyword grants',[DeckEntry('Isamaru, Hound of Konda',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Isamaru, Hound of Konda'])

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temporary.cleanup()

    def keywords(self,session,card):return session.engine._combat_keywords(session.state.cards[card.object_id])

    def test_actual_chaosphere_grants_ground_reach_and_shared_block_actions_keep_flyers_off_ground(self):
        session=self.session(613626001);source=self.add(session.engine,'Chaosphere',zone='hand')
        ground=self.add(session.engine,'Grizzly Bears',ref='ground-attacker');ground.acquired_control_turn_count=-1
        flyer=self.add(session.engine,'Wind Drake',ref='flying-attacker');flyer.acquired_control_turn_count=-1
        reach=self.add(session.engine,'Grizzly Bears',seat='B',ref='ground-reach-blocker')
        normal=self.add(session.engine,'Grizzly Bears',seat='B',ref='ground-normal-blocker')
        flying=self.add(session.engine,'Wind Drake',seat='B',ref='flying-blocker')
        private=self.add(session.engine,'Twiddle',seat='D',zone='hand')
        self.assert_closed(session,source);self.prepare(session,mana={'C':2,'R':1});self.checkpoint(session)
        self.cast(session,'A',source);self.assertIsNone(self.resolve(session))
        self.assertIn('reach',self.keywords(session,ground));self.assertNotIn('reach',self.keywords(session,flyer))
        self.assertIn('reach',self.keywords(session,reach));self.assertNotIn('reach',self.keywords(session,flying))
        self.until(session,lambda s:s.state.pending_decision is not None and s.state.pending_decision.kind=='combat.attackers')
        result=session.act('pilot:A',{'action_id':'attack','attackers':{ground.ref:'B',flyer.ref:'B'}});self.assertTrue(result.ok,result.summary)
        self.until(session,lambda s:s.state.pending_decision is not None and s.state.pending_decision.kind=='combat.blockers')
        before=authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:B',{'action_id':'block','blocks':{flying.ref:ground.ref}}).ok);self.assertEqual(before,authoritative_state_hash(session.state))
        result=session.act('pilot:B',{'action_id':'block','blocks':{reach.ref:flyer.ref,flying.ref:flyer.ref,normal.ref:ground.ref}});self.assertTrue(result.ok,result.summary)
        for seat in 'ABC':self.assertNotIn(private.ref,json.dumps(session.packet('pilot:'+seat,full=True)))
        self.replay(session,load=True)

    def test_actual_later_flight_precedes_absence_query_then_removal_restores_reach(self):
        session=self.session(613626010);source=self.add(session.engine,'Chaosphere',zone='hand')
        body=self.add(session.engine,'Grizzly Bears',seat='B');flight=self.add(session.engine,'Flight',zone='hand');removal=self.add(session.engine,'Naturalize',zone='hand')
        self.assert_closed(session,source);self.prepare(session,mana={'C':3,'R':1,'U':1,'G':1});self.checkpoint(session)
        self.cast_for(session,'A',source,reserve={'C':1,'U':1,'G':1});self.assertIsNone(self.resolve(session));self.assertIn('reach',self.keywords(session,body))
        self.cast_for(session,'A',flight,[body.ref],reserve={'C':1,'G':1});self.assertIsNone(self.resolve(session))
        self.assertIn('flying',self.keywords(session,body));self.assertNotIn('reach',self.keywords(session,body))
        self.cast(session,'A',removal,[flight.ref]);self.assertIsNone(self.resolve(session));self.assertNotIn('flying',self.keywords(session,body));self.assertIn('reach',self.keywords(session,body));self.replay(session,load=True)

    def test_actual_earlier_and_later_humility_changes_tested_keyword_before_static_reach_grant(self):
        session=self.session(613626020);earlier=self.add(session.engine,'Humility',seat='B',ref='earlier-blanker')
        body=self.add(session.engine,'Wind Drake',ref='printed-flyer');ground=self.add(session.engine,'Grizzly Bears',ref='printed-ground')
        source=self.add(session.engine,'Chaosphere',zone='hand');removal=self.add(session.engine,'Disenchant',zone='hand')
        later=self.add(session.engine,'Humility',seat='B',zone='hand',ref='later-blanker');self.add(session.engine,'Vedalken Orrery',seat='B')
        self.assert_closed(session,source);self.prepare(session,mana={'C':3,'R':1,'W':1});session.state.players['B'].mana_pool.update({'C':2,'W':2});self.checkpoint(session)
        self.cast_for(session,'A',source,reserve={'C':1,'W':1});self.assertIsNone(self.resolve(session))
        for card in (body,ground):self.assertIn('reach',self.keywords(session,card));self.assertNotIn('flying',self.keywords(session,card))
        self.cast(session,'A',removal,[earlier.ref]);self.assertIsNone(self.resolve(session));self.assertIn('flying',self.keywords(session,body));self.assertNotIn('reach',self.keywords(session,body))
        self.cast(session,'B',later);self.assertIsNone(self.resolve(session))
        for card in (body,ground):self.assertIn('reach',self.keywords(session,card));self.assertNotIn('flying',self.keywords(session,card))
        self.replay(session,load=True)

    def test_runtime_absence_owner_cannot_be_omitted(self):
        from quorune.card_programs.binding import bind_semantic_program_runtime
        session=self.session(613626030);source=self.add(session.engine,'Chaosphere');checked=0
        for program in session.engine.semantics.programs_for_oracle(source.oracle_id):
            if CAPABILITY not in program.capability_dependencies:continue
            checked+=1;deps=tuple(c for c in program.capability_dependencies if c!=CAPABILITY)
            value=replace(program,capability_dependencies=deps,capability_closure=self.registry.closure(deps,profile='commander_review').to_dict())
            bound=bind_semantic_program_runtime(value,capability_registry=self.registry,profile='commander_review')
            self.assertFalse(bound['strict']);self.assertIn('capability:undeclared_runtime_dependency:'+CAPABILITY,bound['blockers'])
        self.assertGreater(checked,0)
