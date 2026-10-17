from __future__ import annotations

from pathlib import Path
from dataclasses import replace
import json
import tempfile
import unittest
from unittest import mock

from common import ROOT
from quorune.carddb import CardDatabase,CardRecord
from quorune.deck import DeckDefinition,DeckEntry
from quorune.record import authoritative_state_hash
from quorune.model import CombatState
from quorune.rules.capabilities import load_default_capability_registry,CapabilityRegistry
from quorune.card_programs import bind_card_program_runtime
from quorune.card_programs.adapters import compile_best_available_card_program
from quorune.oracle_ir import compile_oracle_card,register_generated_programs
from quorune.semantics import SemanticProgram,SemanticRegistry
from scripts.build_test_database import build_fixture_database
import test_fixed_copy_type_exceptions as copies
import test_attack_counter_triggers as attacks

FIXTURE=ROOT/'tests/fixtures/copiable-entry-ability-grant-cards.json'


class CopiableEntryAbilityCompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.registry=load_default_capability_registry()

    def record(self,text):
        return CardRecord(oracle_id='fixture:copy-grant-boundary',name='Copy Boundary',
            mana_cost='{4}{U}',mana_value=5.0,type_line='Creature',oracle_text=text,
            power='0',toughness='0',loyalty=None,defense=None,colors=('U',),color_identity=('U',),
            keywords=(),produced_mana=(),layout='normal',released_at='2026-01-01',
            legalities={'commander':'legal'},faces=(),raw={})

    def test_copiable_quoted_grants_consume_one_closed_child_and_keep_near_misses_residual(self):
        stem='You may have this creature enter as a copy of any creature on the battlefield, except it has '
        good=compile_oracle_card(self.record(stem+'"{2}{U}{U}: Return this creature to its owner\'s hand."'),
            capability_registry=self.registry,capability_profile='commander_review')
        self.assertEqual('exact',good.status)
        for suffix in ('"{X}: This creature has base power and toughness X/X."',
            '"This creature has flying."',
            '"{U}: Return Copy Boundary to its owner\'s hand."',
            '"{U}: Return this creature to its owner\'s hand." and "Flying"',
            '"{U}: Return this creature to its owner\'s hand." Draw a card.'):
            with self.subTest(suffix=suffix):
                ir=compile_oracle_card(self.record(stem+suffix),capability_registry=self.registry,capability_profile='commander_review')
                self.assertNotEqual('exact',ir.status)

    def test_source_return_promotion_preserves_exact_shape_and_dependencies(self):
        from quorune.compiler.nonleaf_effect_closure import nonleaf_effect_program_is_closed
        program=SemanticProgram(key='fixture:closed-self-return',label='Closed self return',
            ability_id='ability:closed-self-return',effects=[{'op':'bounce','card':'$source'}],
            coverage=['fixed-self-return-to-owner-hand'],capability_dependencies=['permanent.return.owner_hand'],
            capability_closure=self.registry.closure(('permanent.return.owner_hand',),profile='commander_review').to_dict())
        self.assertTrue(nonleaf_effect_program_is_closed(program))
        variants=(replace(program,effects=[{'op':'bounce','card':'$target.0'}]),
            replace(program,effects=[{'op':'bounce','card':'$source','destination':'exile'}]),
            replace(program,effects=[{'op':'bounce','card':'$source'},{'op':'draw','count':1}]),
            replace(program,target_schema={'kind':'object'}),replace(program,coverage=[]),
            replace(program,capability_dependencies=[],capability_closure=None))
        for variant in variants:
            with self.subTest(effects=variant.effects,coverage=variant.coverage):
                self.assertFalse(nonleaf_effect_program_is_closed(variant))


class CopiableEntryAbilityRuntimeTests(unittest.TestCase):
    register_fixture_cards_on_add=True
    session=copies.FixedCopyTypeExceptionRuntimeTests.session
    add=copies.FixedCopyTypeExceptionRuntimeTests.add
    checkpoint=copies.FixedCopyTypeExceptionRuntimeTests.checkpoint
    resolve=copies.FixedCopyTypeExceptionRuntimeTests.resolve
    replay=copies.FixedCopyTypeExceptionRuntimeTests.replay
    prepare=copies.FixedCopyTypeExceptionRuntimeTests.prepare
    cast=copies.FixedCopyTypeExceptionRuntimeTests.cast
    assert_closed=copies.FixedCopyTypeExceptionRuntimeTests.assert_closed
    choose_copy=copies.FixedCopyTypeExceptionRuntimeTests.choose_copy

    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();path=Path(cls.temp.name)/'copy-abilities.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',FIXTURE],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Copiable abilities',[DeckEntry('Generic Bound Commander',1,'commander'),
            DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temp.cleanup()

    def return_actions(self,session,card):
        return [row for row in session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
            if row['id'].startswith('activate:'+card.ref+':')]

    def test_actual_copy_added_activation_is_copiable_and_uses_its_own_source(self):
        session=self.session(70709201);engine=session.engine
        target=self.add(engine,'Generic Bound Body')
        source=self.add(engine,'Mercurial Pretender',zone='hand')
        clone=self.add(engine,'Clone',zone='hand')
        humble=self.add(engine,'Humble',zone='hand')
        private=self.add(engine,'Generic Bound Growth',seat='C',zone='hand',ref='copiable-ability-private')
        for card in (source,clone,humble):self.assert_closed(session,card)
        self.prepare(session,mana={'C':4,'U':1});self.checkpoint(session)
        self.cast(session,'A',source);session=self.choose_copy(session,target)
        current=session.state.cards[source.object_id]
        self.prepare(session,mana={'C':2,'U':2});self.checkpoint(session)
        actions=self.return_actions(session,current)
        self.assertEqual(1,len(actions),actions);original_action=actions[0]['id']
        self.prepare(session,mana={'C':1,'W':1});self.checkpoint(session)
        self.cast(session,'A',humble,[current.ref]);self.resolve(session)
        self.prepare(session,mana={'C':2,'U':2});self.checkpoint(session)
        self.assertEqual([],self.return_actions(session,current))
        before=authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:A',{'action_id':original_action,'pay':'auto'}).ok)
        self.assertEqual(before,authoritative_state_hash(session.state))
        self.prepare(session,mana={'C':3,'U':1});self.checkpoint(session)
        self.cast(session,'A',clone);session=self.choose_copy(session,current)
        copied=session.state.cards[clone.object_id]
        self.prepare(session,mana={'C':2,'U':2});self.checkpoint(session)
        actions=self.return_actions(session,copied)
        self.assertEqual(1,len(actions),actions)
        before=authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:C',{'action_id':actions[0]['id'],'pay':'auto'}).ok)
        self.assertEqual(before,authoritative_state_hash(session.state))
        for viewer in 'ABD':self.assertNotIn(private.ref,json.dumps(session.packet('pilot:'+viewer,full=True)))
        result=session.act('pilot:A',{'action_id':actions[0]['id'],'pay':'auto'})
        self.assertTrue(result.ok,result.summary)
        session=self.replay(session,load=True);self.resolve(session)
        self.assertEqual('hand',session.state.cards[clone.object_id].zone)
        self.assertNotIn('copy_overrides',session.state.cards[clone.object_id].annotations)
        self.assertEqual('battlefield',session.state.cards[source.object_id].zone)
        self.assertEqual('battlefield',session.state.cards[target.object_id].zone)
        self.replay(session,load=True)

    def test_copy_added_fragment_omission_is_killed_by_actual_activated_copy(self):
        import quorune.semantic_runtime.entry_copy as owner
        original=owner.apply_copy_exception
        def omitted(values,spec):return original(values,replace(spec,add_ability_fragments=()))
        with mock.patch.object(owner,'apply_copy_exception',side_effect=omitted):
            with self.assertRaises(AssertionError):
                self.test_actual_copy_added_activation_is_copiable_and_uses_its_own_source()

    def test_controller_qualified_copy_cannot_choose_an_opponent_creature(self):
        session=self.session(70709202);engine=session.engine
        foreign=self.add(engine,'Generic Bound Body',seat='B')
        source=self.add(engine,'Mercurial Pretender',zone='hand')
        self.assert_closed(session,source)
        self.prepare(session,mana={'C':4,'U':1});self.checkpoint(session)
        self.cast(session,'A',source);self.resolve(session)
        self.assertEqual('graveyard',session.state.cards[source.object_id].zone)
        self.assertEqual('battlefield',session.state.cards[foreign.object_id].zone)
        self.replay(session,load=True)

    def test_actual_clone_inherits_phantasmal_target_trigger_on_its_own_source(self):
        session=self.session(70709203);engine=session.engine
        target=self.add(engine,'Generic Bound Body',seat='B')
        image=self.add(engine,'Phantasmal Image',zone='hand')
        clone=self.add(engine,'Clone',zone='hand')
        humble=self.add(engine,'Humble',zone='hand')
        for card in (image,clone,humble):self.assert_closed(session,card)
        self.prepare(session,mana={'C':1,'U':1});self.checkpoint(session)
        self.cast(session,'A',image);session=self.choose_copy(session,target)
        self.assertIn('illusion',session.engine._type_parts(session.engine._effective_card_data(session.state.cards[image.object_id])['type_line'])[1])
        self.prepare(session,mana={'C':3,'U':1});self.checkpoint(session)
        self.cast(session,'A',clone);session=self.choose_copy(session,session.state.cards[image.object_id])
        copied=session.state.cards[clone.object_id]
        self.assertIn('illusion',session.engine._type_parts(session.engine._effective_card_data(copied)['type_line'])[1])
        self.prepare(session,mana={'C':1,'W':1});self.checkpoint(session)
        self.cast(session,'A',humble,[copied.ref])
        self.assertEqual(2,len(session.state.stack))
        session=self.replay(session,load=True);self.resolve(session)
        self.assertEqual('graveyard',session.state.cards[clone.object_id].zone)
        self.assertEqual('battlefield',session.state.cards[image.object_id].zone)
        self.assertEqual('battlefield',session.state.cards[target.object_id].zone)
        self.replay(session,load=True)

    def test_actual_copy_of_copy_keeps_both_connive_triggers_and_private_choices(self):
        session=self.session(70709204);engine=session.engine
        target=self.add(engine,'Generic Bound Body',seat='B')
        haste=self.add(engine,'Fervor')
        first=self.add(engine,'Copycrook',zone='hand',ref='first-copycrook')
        second=self.add(engine,'Copycrook',zone='hand',ref='second-copycrook')
        selected=[self.add(engine,'Generic Bound Growth',zone='hand',ref='connive-private-'+str(i)) for i in range(2)]
        for card in (haste,first):self.assert_closed(session,card)
        self.prepare(session,mana={'C':2,'U':2});self.checkpoint(session)
        self.cast(session,'A',first);session=self.choose_copy(session,target)
        self.prepare(session,mana={'C':2,'U':2});self.checkpoint(session)
        self.cast(session,'A',second);session=self.choose_copy(session,session.state.cards[first.object_id])
        engine=session.engine
        engine.permissions.invalidate_current();engine.state.pending_decision=None
        engine.state.priority_player=None;engine.state.priority_passes=[]
        engine.state.phase='combat';engine.state.step='declare_attackers';engine.state.phase_index=5
        engine.state.combat=CombatState();engine._issue_attackers();self.checkpoint(session)
        before_b=len(session.state.players['B'].zones['hand'])
        result=session.act('pilot:A',{'action_id':'attack','attackers':{session.state.cards[second.object_id].ref:'B'}})
        self.assertTrue(result.ok,result.summary)
        attacks.AttackCounterTriggerRuntimeTests.order_current_triggers(session)
        for discard in selected:
            decision=self.resolve(session);self.assertEqual('semantic.choice',decision.kind)
            self.assertEqual(['A'],list(decision.actors))
            for seat in 'BCD':self.assertNotIn(discard.ref,json.dumps(session.packet('pilot:'+seat,full=True)))
            before=authoritative_state_hash(session.state)
            self.assertFalse(session.act('pilot:C',{'action_id':'choose','cards':[discard.ref]}).ok)
            self.assertEqual(before,authoritative_state_hash(session.state))
            session=self.replay(session,load=True)
            result=session.act('pilot:A',{'action_id':'choose','cards':[discard.ref]})
            self.assertTrue(result.ok,result.summary)
        self.assertEqual(0,session.state.cards[first.object_id].counters.get('+1/+1',0))
        self.assertEqual(2,session.state.cards[second.object_id].counters.get('+1/+1',0))
        completed=[event for event in session.state.events if event.code=='connive.complete']
        self.assertEqual(2,len(completed))
        self.assertTrue(all(event.details['conniver_logical_object_id']==session.state.cards[second.object_id].logical_object_id for event in completed))
        self.assertEqual(before_b,len(session.state.players['B'].zones['hand']))
        self.replay(session,load=True)

    def test_original_copiable_grant_carriers_require_normal_registration_and_child_closure(self):
        expected={'Mercurial Pretender','Phantasmal Image','Copycrook'}
        controls={'Clone','Humble','Unsummon','Fervor'}
        for raw in json.loads(FIXTURE.read_text())['cards']:
            if raw['name'] in controls:continue
            with self.subTest(name=raw['name']):
                card=self.db.lookup(raw['name']);semantics=SemanticRegistry()
                register_generated_programs(self.db,semantics,(card,),trust_level='provisional',
                    capability_registry=self.registry,capability_profile='commander_review',
                    promote_exact_runtime_handlers=True,promote_exact_trigger_programs=True,
                    promote_exact_effect_programs=True,promote_exact_capability_declarations=True)
                program=compile_best_available_card_program(self.db,card,semantic_registry=semantics,
                    capability_registry=self.registry,capability_profile='commander_review')
                binding=bind_card_program_runtime(program,capability_registry=self.registry,profile='commander_review')
                self.assertEqual(raw['name'] in expected,binding['strict_capability_ready'],binding['blockers'])
        for capability in ('zone.entry.fixed_copy','permanent.return.owner_hand'):
            raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text())
            next(row for row in raw['capabilities'] if row['id']==capability).update(status='blocked',blockers=['required copied ability owner omitted'])
            registry=CapabilityRegistry(raw)
            program=compile_best_available_card_program(self.db,self.db.lookup('Mercurial Pretender'),
                semantic_registry=SemanticRegistry(),capability_registry=registry,capability_profile='commander_review')
            self.assertFalse(bind_card_program_runtime(program,capability_registry=registry,profile='commander_review')['strict_capability_ready'])

    def test_source_return_promotion_omission_is_killed_without_forced_trust(self):
        with mock.patch('quorune.compiler.nonleaf_effect_closure.fixed_self_return_node_capabilities',return_value=()) as omitted:
            with self.assertRaises(AssertionError):
                self.test_actual_copy_added_activation_is_copiable_and_uses_its_own_source()
        self.assertGreater(omitted.call_count,0)
