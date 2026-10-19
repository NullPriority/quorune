from __future__ import annotations

"""CR 402.3/608.2h: hand size belongs to the selected player at resolution."""

from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from common import ROOT
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.query_effect_amount_model import PublicQueryAmountError
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import CapabilityRegistry,load_default_capability_registry
from quorune.scalar_effect_amount_model import ScalarEffectAmountSpec,ScalarAmountOrigin,TARGET_HAND_COUNT_CAPABILITY as CAP
from quorune.scalar_effect_amounts import resolve_scalar_effect_amount
from scripts.build_test_database import build_fixture_database
from test_bound_effect_programs import record
import test_compound_damage_recipients as controls


class TargetHandCountModelTests(unittest.TestCase):
    def test_versioned_scalar_reads_target_current_count_and_binds_declarations_to_that_seat(self):
        spec=ScalarEffectAmountSpec(ScalarAmountOrigin.TARGET_HAND_COUNT,schema_version=4)
        self.assertEqual(spec,ScalarEffectAmountSpec.from_dict(spec.to_dict()))
        players={s:SimpleNamespace(in_game=True,zones={'hand':['private-'+s+str(i) for i in range(n)]}) for s,n in (('A',1),('B',4),('C',2))}
        host=SimpleNamespace(state=SimpleNamespace(players=players));item=SimpleNamespace(controller='A',targets=['B'],context={})
        self.assertEqual(4,resolve_scalar_effect_amount(host,spec.to_dict(),item))
        players['B'].zones['hand'].pop();self.assertEqual(3,resolve_scalar_effect_amount(host,spec.to_dict(),item))
        bound=replace(spec,binding_id='declaration');self.assertEqual(3,resolve_scalar_effect_amount(host,bound.to_dict(),item))
        players['B'].zones['hand'].clear();self.assertEqual(3,resolve_scalar_effect_amount(host,bound.to_dict(),item))
        item.targets=['C']
        with self.assertRaises(PublicQueryAmountError):resolve_scalar_effect_amount(host,bound.to_dict(),item)
        for targets in ([],['B','C'],['unknown-seat']):
            item.targets=targets;item.context={}
            with self.assertRaises(PublicQueryAmountError):resolve_scalar_effect_amount(host,spec.to_dict(),item)
        for change in ({'schema_version':1},{'schema_version':4.0},{'counter_name':'charge'},{'characteristic':'power'},{'extra':'value'}):
            with self.assertRaises((ValueError,TypeError)):ScalarEffectAmountSpec.from_dict({**spec.to_dict(),**change})

    def test_source_grammar_keeps_player_identity_and_rejects_private_predicates_and_ambiguous_targets(self):
        registry=load_default_capability_registry()
        text="Generic Bound Compiler Fixture deals damage to target player equal to the number of cards in that player's hand."
        ir=compile_oracle_card(record(text),capability_registry=registry,capability_profile='commander_review')
        self.assertEqual('exact',ir.status,ir.material_residuals)
        node=ir.faces[0].nodes[0];self.assertIn(CAP,node.capability_dependencies)
        self.assertEqual((['player'],1),(node.target_schema['zones'],node.target_schema['count']))
        self.assertEqual('target_player_hand_count',node.effects[0]['amount']['origin'])
        self.assertEqual(text,node.text)
        for bad in (text.replace('target player','target creature'),
                text.replace("that player's hand","that creature's controller's hand"),
                text.replace('number of cards','number of creature cards'),
                text.replace('target player','two target players'),
                text.replace("that player's hand.","that player's hand minus 4.")):
            self.assertNotEqual('exact',compile_oracle_card(record(bad),capability_registry=registry,capability_profile='commander_review').status,bad)
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text())
        for cap in (CAP,'quantity_expression.scalar_effect_amount','damage.source.stack_current_or_lki'):
            changed=deepcopy(raw)
            next(r for r in changed['capabilities'] if r['id']==cap).update(status='blocked',blockers=['withdrawn'])
            self.assertNotEqual('exact',compile_oracle_card(record(text),capability_registry=CapabilityRegistry(changed),capability_profile='commander_review').status,cap)


class TargetHandCountRuntimeTests(unittest.TestCase):
    register_fixture_cards_on_add=True
    session=controls.CompoundDamageRuntimeTests.session
    add=controls.CompoundDamageRuntimeTests.add
    prepare=controls.CompoundDamageRuntimeTests.prepare
    checkpoint=controls.CompoundDamageRuntimeTests.checkpoint
    cast=controls.CompoundDamageRuntimeTests.cast
    resolve=controls.CompoundDamageRuntimeTests.resolve
    replay=controls.CompoundDamageRuntimeTests.replay
    binding=controls.CompoundDamageRuntimeTests.binding
    actions=controls.CompoundDamageRuntimeTests.actions
    action=controls.CompoundDamageRuntimeTests.action
    private_replay=controls.CompoundDamageRuntimeTests.private_replay

    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'target-hand.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',
            ROOT/'tests/fixtures/target-player-hand-count-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Target hand count',[DeckEntry('Generic Bound Commander',1,'commander'),
            DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temporary.cleanup()

    def private_hands(self,session):
        cards=[self.add(session.engine,'Generic Bound Growth',seat='B',zone='hand',ref='hand-count-b-'+str(i)) for i in range(3)]
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand',ref='hand-count-c')
        return cards,private

    def assert_b_private(self,session,cards):
        for seat in 'ACD':
            packet=json.dumps(session.packet('pilot:'+seat,full=True))
            for card in cards:self.assertNotIn(card.ref,packet)

    def test_three_originals_damage_only_selected_player_by_current_public_hand_size(self):
        for name,mana in (('Sudden Impact',{'R':1,'C':3}),('Storm Seeker',{'G':1,'C':3}),
                ('Gaze of Adamaro',{'R':2,'C':2})):
            session=self.session(729901+len(name));source=self.add(session.engine,name,zone='hand')
            cards,private=self.private_hands(session)
            self.prepare(session,mana=mana);self.checkpoint(session)
            self.assertTrue(self.binding(session,source)['strict_capability_ready'])
            from quorune.preflight import card_semantic_status
            status=card_semantic_status(self.db.lookup(name),session.engine.semantics,db=self.db,
                capability_registry=self.registry,capability_profile='commander_review')
            self.assertEqual('fully_playable',status['status'],(name,status['unresolved']))
            before=authoritative_state_hash(session.state);action=self.action(session,source)
            command={'action_id':action['id'],'targets':['B'],'pay':'auto'}
            denied=session.act('pilot:B',command);self.assertFalse(denied.ok)
            self.assertEqual(before,authoritative_state_hash(session.state))
            accepted=session.act('pilot:A',command);self.assertTrue(accepted.ok,accepted.summary)
            amount=len(session.state.players['B'].zones['hand'])
            self.assertNotEqual(amount,len(session.state.players['A'].zones['hand']))
            session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
            self.assertEqual([40,40-amount,40,40],[session.state.players[s].life for s in 'ABCD'])
            self.assertEqual(0,sum(session.state.players['A'].mana_pool.values()))
            self.assert_b_private(session,cards);self.private_replay(session,private)

    def test_actual_draw_response_changes_count_after_cast_and_remains_private(self):
        session=self.session(729920);source=self.add(session.engine,'Sudden Impact',zone='hand')
        draw=self.add(session.engine,'Inspiration',seat='B',zone='hand')
        cards,private=self.private_hands(session)
        session.state.players['B'].mana_pool.update({'U':1,'C':3})
        self.prepare(session,mana={'R':1,'C':3});self.checkpoint(session)
        amount=len(session.state.players['B'].zones['hand'])
        accepted=session.act('pilot:A',{'action_id':self.action(session,source)['id'],'targets':['B'],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary)
        self.cast(session,'B',draw,['B']);session=self.replay(session,load=True)
        self.assertIsNone(self.resolve(session))
        self.assertEqual(amount+1,len(session.state.players['B'].zones['hand']))
        self.assertEqual(40-(amount+1),session.state.players['B'].life)
        drawn=[session.state.cards[oid] for oid in session.state.players['B'].zones['hand'][-2:]]
        self.assert_b_private(session,[*cards,*drawn]);self.private_replay(session,private)

    def test_fresh_originals_kill_caster_hand_and_zero_count_mutations(self):
        def caster(host,seat):return len(host.state.players['A'].zones['hand'])
        for mutant in (caster,lambda host,seat:0):
            method='test_three_originals_damage_only_selected_player_by_current_public_hand_size'
            with patch('quorune.scalar_effect_amounts._current_target_hand_count',side_effect=mutant):
                with self.assertRaises(AssertionError):getattr(type(self)(method),method)()

    def test_actual_discard_response_makes_zero_damage_without_refunding_cost(self):
        session=self.session(729921);source=self.add(session.engine,'Sudden Impact',zone='hand')
        discard=self.add(session.engine,'One with Nothing',seat='B',zone='hand')
        _cards,private=self.private_hands(session)
        session.state.players['B'].mana_pool.update({'B':1})
        self.prepare(session,mana={'R':1,'C':3});self.checkpoint(session)
        accepted=session.act('pilot:A',{'action_id':self.action(session,source)['id'],'targets':['B'],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary);self.cast(session,'B',discard)
        session=self.replay(session,load=True);pending=self.resolve(session)
        if pending is not None:
            self.assertEqual(['pilot:B'],session.pending_principals())
            session=self.replay(session,load=True)
            refs=[session.state.cards[oid].ref for oid in session.state.players['B'].zones['hand']]
            accepted=session.act('pilot:B',{'action_id':'choose','cards':refs})
            self.assertTrue(accepted.ok,accepted.summary);self.assertIsNone(self.resolve(session))
        self.assertEqual([],session.state.players['B'].zones['hand'])
        self.assertEqual([40]*4,[session.state.players[s].life for s in 'ABCD'])
        self.assertEqual(0,sum(session.state.players['A'].mana_pool.values()))
        self.assertEqual('graveyard',session.state.cards[source.object_id].zone)
        self.private_replay(session,private)

    def test_original_storm_seeker_can_target_its_caster_after_the_card_leaves_hand(self):
        session=self.session(729922);source=self.add(session.engine,'Storm Seeker',zone='hand')
        cards,private=self.private_hands(session)
        self.prepare(session,mana={'G':1,'C':3});self.checkpoint(session)
        before=len(session.state.players['A'].zones['hand'])
        accepted=session.act('pilot:A',{'action_id':self.action(session,source)['id'],'targets':['A'],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary)
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertEqual([40-(before-1),40,40,40],[session.state.players[s].life for s in 'ABCD'])
        self.assert_b_private(session,cards);self.private_replay(session,private)

    def test_countered_original_keeps_paid_cost_and_does_not_damage_the_selected_player(self):
        session=self.session(729923);source=self.add(session.engine,'Sudden Impact',zone='hand')
        counter=self.add(session.engine,'Counterspell',seat='B',zone='hand')
        cards,private=self.private_hands(session)
        session.state.players['B'].mana_pool.update({'U':2})
        self.prepare(session,mana={'R':1,'C':3});self.checkpoint(session)
        accepted=session.act('pilot:A',{'action_id':self.action(session,source)['id'],'targets':['B'],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary)
        self.cast(session,'B',counter,[session.state.stack[-1].ref]);session=self.replay(session,load=True)
        self.assertIsNone(self.resolve(session))
        self.assertEqual([40]*4,[session.state.players[s].life for s in 'ABCD'])
        self.assertEqual(0,sum(session.state.players['A'].mana_pool.values()))
        self.assertEqual('graveyard',session.state.cards[source.object_id].zone)
        self.assert_b_private(session,cards);self.private_replay(session,private)

    def test_actual_copy_queries_its_new_target_and_preserves_original_target(self):
        session=self.session(729924);source=self.add(session.engine,'Sudden Impact',zone='hand')
        copy=self.add(session.engine,'Reverberate',seat='B',zone='hand')
        cards,private=self.private_hands(session)
        session.state.players['B'].mana_pool.update({'R':2})
        self.prepare(session,mana={'R':1,'C':3});self.checkpoint(session)
        accepted=session.act('pilot:A',{'action_id':self.action(session,source)['id'],'targets':['B'],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary);original=session.state.stack[-1]
        self.cast(session,'B',copy,[original.ref]);pending=self.resolve(session)
        self.assertEqual('semantic.choice',pending.kind)
        before=authoritative_state_hash(session.state)
        denied=session.act('pilot:A',{'action_id':'choose','targets':['D']})
        self.assertFalse(denied.ok);self.assertEqual(before,authoritative_state_hash(session.state))
        session=self.replay(session,load=True)
        accepted=session.act('pilot:B',{'action_id':'choose','targets':['D']})
        self.assertTrue(accepted.ok,accepted.summary)
        copied=next(item for item in session.state.stack if item.kind=='spell_copy')
        self.assertEqual((['D'],'B'),(copied.targets,copied.controller))
        self.assertEqual(['B'],next(item for item in session.state.stack if item.ref==original.ref).targets)
        b_count=len(session.state.players['B'].zones['hand']);d_count=len(session.state.players['D'].zones['hand'])
        self.assertNotEqual(b_count,d_count)
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertEqual([40,40-b_count,40,40-d_count],[session.state.players[s].life for s in 'ABCD'])
        self.assertEqual(2,len(session.engine._current_turn_history('spell_cast')))
        self.assert_b_private(session,cards);self.private_replay(session,private)

    def test_departed_sole_target_prevents_query_and_resolution(self):
        session=self.session(729925);source=self.add(session.engine,'Sudden Impact',zone='hand')
        _cards,private=self.private_hands(session)
        self.prepare(session,mana={'R':1,'C':3});self.checkpoint(session)
        accepted=session.act('pilot:A',{'action_id':self.action(session,source)['id'],'targets':['B'],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary)
        for _ in range(8):
            if session.state.priority_player=='B':break
            self.assertTrue(session.act(session.pending_principals()[0],{'action_id':'pass'}).ok)
        else:self.fail('Selected player did not receive priority before departure')
        accepted=session.act('pilot:B',{'action_id':'concede','choices':{'confirm_concede':True}})
        self.assertTrue(accepted.ok,accepted.summary);self.assertFalse(session.state.players['B'].in_game)
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertEqual([40]*3,[session.state.players[s].life for s in 'ACD'])
        self.assertEqual('graveyard',session.state.cards[source.object_id].zone)
        self.assertEqual(0,sum(session.state.players['A'].mana_pool.values()))
        self.private_replay(session,private)

    def test_runtime_requires_hand_count_scalar_and_target_revalidation_authority(self):
        from quorune.card_programs.binding import bind_semantic_program_runtime
        session=self.session(729926);source=self.add(session.engine,'Sudden Impact',zone='hand')
        program=next(p for p in session.engine.semantics.programs_for_oracle(source.oracle_id) if CAP in p.capability_dependencies)
        self.assertTrue(bind_semantic_program_runtime(program,capability_registry=self.registry,profile='commander_review')['strict'])
        for cap in (CAP,'quantity_expression.scalar_effect_amount','target.revalidate_resolution'):
            dependencies=tuple(c for c in program.capability_dependencies if c!=cap)
            changed=replace(program,capability_dependencies=dependencies,
                capability_closure=self.registry.closure(dependencies,profile='commander_review').to_dict())
            bound=bind_semantic_program_runtime(changed,capability_registry=self.registry,profile='commander_review')
            self.assertFalse(bound['strict']);self.assertIn('capability:undeclared_runtime_dependency:'+cap,bound['blockers'])
