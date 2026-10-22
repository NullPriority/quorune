from __future__ import annotations

"""CR402.2/514.1/613.11: current static adjustments govern private cleanup."""

from pathlib import Path
from copy import deepcopy
from dataclasses import replace
import json
import tempfile
import unittest
from unittest.mock import patch
from common import ROOT
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
import test_temporary_cast_restrictions as controls


class HandSizeAdjustmentTests(unittest.TestCase):
    register_fixture_cards_on_add=True
    session=controls.TemporaryCastRestrictionTests.session
    add=controls.TemporaryCastRestrictionTests.add
    prepare=controls.TemporaryCastRestrictionTests.prepare
    checkpoint=controls.TemporaryCastRestrictionTests.checkpoint
    resolve=controls.TemporaryCastRestrictionTests.resolve
    replay=controls.TemporaryCastRestrictionTests.replay
    binding=controls.TemporaryCastRestrictionTests.binding
    priority=controls.TemporaryCastRestrictionTests.priority
    cast=controls.TemporaryCastRestrictionTests.cast
    top=controls.TemporaryCastRestrictionTests.top
    private_replay=controls.TemporaryCastRestrictionTests.private_replay
    advance=controls.TemporaryCastRestrictionTests.advance

    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();path=Path(cls.temp.name)/'hand-size.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',ROOT/'tests/fixtures/attached-protection-cards.json',
            ROOT/'tests/fixtures/hand-size-adjustment-cards.json',ROOT/'tests/fixtures/source-keyword-choice-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Hand-size adjustments',[DeckEntry('Generic Bound Commander',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temp.cleanup()

    def cleanup(self,session,seat):
        self.advance(session,lambda s:s.state.active_player==seat and s.state.pending_decision is not None and s.state.pending_decision.kind=='cleanup.discard')

    def finish_cleanup(self,session,seat,expected):
        self.assertEqual('cleanup.discard',session.state.pending_decision.kind)
        packet=session.packet('pilot:'+seat,full=True);ctx=packet['decision']['ctx']
        self.assertEqual(expected,ctx['count']);refs=[r['id'] for r in ctx['hand'][:expected]]
        before=authoritative_state_hash(session.state)
        foreign=next(s for s in 'ABCD' if s!=seat)
        self.assertFalse(session.act('pilot:'+foreign,{'a':'discard','cards':refs}).ok)
        self.assertEqual(before,authoritative_state_hash(session.state))
        self.assertFalse(session.act('pilot:'+seat,{'a':'discard','cards':refs[:-1]}).ok)
        self.assertEqual(before,authoritative_state_hash(session.state))
        session=self.replay(session,load=True)
        result=session.act('pilot:'+seat,{'a':'discard','cards':refs});self.assertTrue(result.ok,result.summary)
        self.assertTrue(all(session.state.cards[c.object_id].zone=='graveyard' for c in session.state.cards.values() if c.ref in refs))
        return session

    def test_actual_gnat_miser_changes_only_opponents_and_forces_exact_private_cleanup(self):
        session=self.session(751400);source=self.add(session.engine,'Gnat Miser',zone='hand')
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.assertTrue(self.binding(session,source)['strict_capability_ready'],'Complete original Gnat Miser must admit')
        self.prepare(session,mana={'B':1});self.checkpoint(session);self.cast(session,'A',source,payment={'B':1})
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        from quorune.maximum_hand_size import effective_maximum_hand_size
        self.assertEqual([7,6,6,6],[effective_maximum_hand_size(session.engine,seat) for seat in 'ABCD'])
        self.cleanup(session,'B');expected=len(session.state.players['B'].zones['hand'])-6
        self.assertGreater(expected,0);session=self.finish_cleanup(session,'B',expected)
        self.assertEqual(6,len(session.state.players['B'].zones['hand']));self.private_replay(session,private)

    def test_actual_minamo_scrollkeeper_increases_own_limit_and_preserves_defender(self):
        session=self.session(751401);source=self.add(session.engine,'Minamo Scrollkeeper',zone='hand')
        for i in range(3):self.add(session.engine,'Generic Bound Growth',zone='hand',ref='excess-hand-'+str(i))
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand',ref='unrevealed-hand-control')
        self.assertTrue(self.binding(session,source)['strict_capability_ready'],'Complete original Minamo Scrollkeeper must admit')
        self.prepare(session,mana={'U':1,'C':1});self.checkpoint(session);self.cast(session,'A',source,payment={'U':1,'C':1})
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        from quorune.maximum_hand_size import effective_maximum_hand_size
        self.assertEqual([8,7,7,7],[effective_maximum_hand_size(session.engine,seat) for seat in 'ABCD'])
        self.assertIn('defender',session.engine._combat_keywords(session.state.cards[source.object_id]))
        self.cleanup(session,'A');expected=len(session.state.players['A'].zones['hand'])-8
        self.assertGreater(expected,0);session=self.finish_cleanup(session,'A',expected)
        self.assertEqual(8,len(session.state.players['A'].zones['hand']));self.private_replay(session,private)

    def test_fixed_adjustment_model_and_handler_reject_unowned_scope_value_and_version(self):
        from quorune.maximum_hand_size import FixedHandSizeAdjustmentSpec,fixed_hand_size_adjustment_descriptor
        from quorune.semantic_runtime.maximum_hand_size import FixedHandSizeAdjustmentHandler
        spec=FixedHandSizeAdjustmentSpec('opponents',-2)
        self.assertEqual(spec,FixedHandSizeAdjustmentSpec.from_dict(spec.to_dict()))
        handler=FixedHandSizeAdjustmentHandler();descriptor=fixed_hand_size_adjustment_descriptor(spec)
        self.assertEqual(spec,handler.validate(descriptor))
        for data in ({**spec.to_dict(),'schema_version':True},{**spec.to_dict(),'schema_version':2},
                {**spec.to_dict(),'amount':True},{**spec.to_dict(),'amount':0},{**spec.to_dict(),'amount':'X'},
                {**spec.to_dict(),'player_scope':'all'},{**spec.to_dict(),'extra':1}):
            with self.assertRaises((ValueError,TypeError)):FixedHandSizeAdjustmentSpec.from_dict(data)
        for changed in ({**descriptor,'schema_version':True},{**descriptor,'event':'resolve'},{**descriptor,'unexpected':1}):
            with self.assertRaises(ValueError):handler.validate(changed)

    def test_emitted_adjustment_requires_its_component_capability_and_closed_descriptor(self):
        from quorune.card_programs.binding import bind_semantic_program_runtime
        from quorune.maximum_hand_size import HAND_SIZE_ADJUSTMENT_CAPABILITY_ID as cap
        session=self.session(751402);source=self.add(session.engine,'Gnat Miser',zone='hand')
        programs=[p for p in session.engine.semantics.programs_for_oracle(source.oracle_id) if cap in p.capability_dependencies]
        self.assertEqual(1,len(programs));program=programs[0]
        self.assertTrue(bind_semantic_program_runtime(program,capability_registry=self.registry,profile='commander_review')['strict'])
        deps=tuple(c for c in program.capability_dependencies if c!=cap)
        stripped=replace(program,capability_dependencies=deps,capability_closure=self.registry.closure(deps,profile='commander_review').to_dict() if deps else None)
        bound=bind_semantic_program_runtime(stripped,capability_registry=self.registry,profile='commander_review')
        self.assertFalse(bound['strict']);self.assertIn('capability:undeclared_runtime_dependency:'+cap,bound['blockers'])
        descriptor=program.handlers[0]
        for changed in ({**descriptor,'spec':{**descriptor['spec'],'amount':True}},
                {**descriptor,'spec':{**descriptor['spec'],'player_scope':'all'}},{**descriptor,'extra':True}):
            with self.assertRaises(ValueError):replace(program,handlers=(changed,))

    def test_actual_three_thought_creatures_reduce_own_limit_and_retain_flying(self):
        from quorune.maximum_hand_size import effective_maximum_hand_size
        for index,(name,payment,limit) in enumerate((('Thought Nibbler',{'U':1},5),('Thought Eater',{'U':1,'C':1},4),
                ('Thought Devourer',{'U':2,'C':2},3))):
            with self.subTest(name=name):
                session=self.session(751410+index);source=self.add(session.engine,name,zone='hand')
                private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
                self.assertTrue(self.binding(session,source)['strict_capability_ready'],'Complete original '+name+' must admit')
                self.prepare(session,mana=payment);self.checkpoint(session);self.cast(session,'A',source,payment=payment)
                session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
                self.assertEqual([limit,7,7,7],[effective_maximum_hand_size(session.engine,seat) for seat in 'ABCD'])
                self.assertIn('flying',session.engine._combat_keywords(session.state.cards[source.object_id]))
                count=len(session.state.players['A'].zones['hand']);self.assertGreater(count,limit)
                self.assertEqual('priority',session.state.pending_decision.kind,'Static adjustment does not discard immediately')
                self.cleanup(session,'A');session=self.finish_cleanup(session,'A',count-limit)
                self.assertEqual(limit,len(session.state.players['A'].zones['hand']));self.private_replay(session,private)

    def test_actual_locust_miser_reduces_each_opponent_by_two_at_cleanup(self):
        from quorune.maximum_hand_size import effective_maximum_hand_size
        session=self.session(751413);source=self.add(session.engine,'Locust Miser',zone='hand')
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.assertTrue(self.binding(session,source)['strict_capability_ready'],'Complete original Locust Miser must admit')
        self.prepare(session,mana={'B':2,'C':2});self.checkpoint(session);self.cast(session,'A',source,payment={'B':2,'C':2})
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertEqual([7,5,5,5],[effective_maximum_hand_size(session.engine,seat) for seat in 'ABCD'])
        self.cleanup(session,'B');session=self.finish_cleanup(session,'B',len(session.state.players['B'].zones['hand'])-5)
        self.assertEqual(5,len(session.state.players['B'].zones['hand']));self.private_replay(session,private)

    def test_current_adjustments_add_clamp_at_zero_and_preserve_no_maximum(self):
        from quorune.maximum_hand_size import effective_maximum_hand_size
        session=self.session(751414)
        self.add(session.engine,'Gnat Miser');self.add(session.engine,'Locust Miser');self.add(session.engine,'Minamo Scrollkeeper')
        self.add(session.engine,'Minamo Scrollkeeper',seat='B',ref='other-scrollkeeper')
        self.add(session.engine,'Thought Devourer',seat='B')
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(session);self.checkpoint(session)
        self.assertEqual([8,1,4,4],[effective_maximum_hand_size(session.engine,seat) for seat in 'ABCD'])
        for index in range(5):self.add(session.engine,'Gnat Miser',ref='additional-miser-'+str(index))
        self.prepare(session);self.checkpoint(session)
        self.assertEqual([8,0,0,0],[effective_maximum_hand_size(session.engine,seat) for seat in 'ABCD'])
        self.cleanup(session,'B');count=len(session.state.players['B'].zones['hand']);session=self.finish_cleanup(session,'B',count)
        self.assertEqual([],session.state.players['B'].zones['hand']);self.private_replay(session,private)
        unbounded=self.session(751415)
        for index in range(5):self.add(unbounded.engine,'Locust Miser',seat='B',ref='unbounded-miser-'+str(index))
        book=self.add(unbounded.engine,'Spellbook');private=self.add(unbounded.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.assertTrue(self.binding(unbounded,book)['strict_capability_ready'])
        self.prepare(unbounded);self.checkpoint(unbounded)
        self.assertIsNone(effective_maximum_hand_size(unbounded.engine,'A'))
        turn=unbounded.state.turn_sequence;count=len(unbounded.state.players['A'].zones['hand'])
        self.advance(unbounded,lambda s:s.state.turn_sequence>turn and s.state.phase=='precombat_main')
        self.assertEqual(count,len(unbounded.state.players['A'].zones['hand']));self.private_replay(unbounded,private)

    def test_departing_miser_restores_current_limits_before_cleanup(self):
        from quorune.maximum_hand_size import effective_maximum_hand_size
        session=self.session(751416);source=self.add(session.engine,'Gnat Miser')
        self.add(session.engine,'Thought Nibbler',seat='B')
        bounce=self.add(session.engine,'Boomerang',seat='B',zone='hand')
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(session,mana={});session.state.players['B'].mana_pool.update({'U':2});self.checkpoint(session)
        self.assertEqual([7,4,6,6],[effective_maximum_hand_size(session.engine,seat) for seat in 'ABCD'])
        self.cast(session,'B',bounce,[source.ref],{'U':2});session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertEqual('hand',session.state.cards[source.object_id].zone)
        self.assertEqual([7,5,7,7],[effective_maximum_hand_size(session.engine,seat) for seat in 'ABCD']);self.private_replay(session,private)

    def test_lost_ability_restores_cleanup_limit_before_end_of_turn_expiry(self):
        from quorune.maximum_hand_size import effective_maximum_hand_size
        session=self.session(751417);source=self.add(session.engine,'Gnat Miser',seat='B')
        humble=self.add(session.engine,'Humble',seat='C',zone='hand')
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        for index in range(2):self.add(session.engine,'Generic Bound Growth',zone='hand',ref='humble-excess-'+str(index))
        self.prepare(session);session.state.players['C'].mana_pool.update({'W':1,'C':1});self.checkpoint(session)
        self.assertEqual(6,effective_maximum_hand_size(session.engine,'A'))
        self.cast(session,'C',humble,[source.ref],{'W':1,'C':1});session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertEqual(7,effective_maximum_hand_size(session.engine,'A'))
        self.cleanup(session,'A');session=self.finish_cleanup(session,'A',len(session.state.players['A'].zones['hand'])-7)
        self.assertEqual(7,len(session.state.players['A'].zones['hand']))
        self.assertEqual(6,effective_maximum_hand_size(session.engine,'A'),'Ability returns after cleanup discard without another discard solely for the restored limit')
        self.private_replay(session,private)

    def test_changed_source_controller_rebinds_opponents_until_control_expires_after_discard(self):
        from quorune.maximum_hand_size import effective_maximum_hand_size
        session=self.session(751418);source=self.add(session.engine,'Gnat Miser')
        steal=self.add(session.engine,'Word of Seizing',seat='B',zone='hand')
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.assertTrue(self.binding(session,steal)['strict_capability_ready'])
        self.prepare(session);session.state.players['B'].mana_pool.update({'R':2,'C':3});self.checkpoint(session)
        self.cast(session,'B',steal,[source.ref],{'R':2,'C':3});session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertEqual('B',session.state.cards[source.object_id].controller)
        self.assertEqual([6,7,6,6],[effective_maximum_hand_size(session.engine,seat) for seat in 'ABCD'])
        self.cleanup(session,'A');session=self.finish_cleanup(session,'A',len(session.state.players['A'].zones['hand'])-6)
        self.assertEqual('A',session.state.cards[source.object_id].controller)
        self.assertEqual([7,6,6,6],[effective_maximum_hand_size(session.engine,seat) for seat in 'ABCD']);self.private_replay(session,private)

    def test_canonically_verified_adjustment_owner_withdrawal_keeps_all_originals_residual(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from scripts.update_capability_evidence import build_index,discover_tests
        from quorune.oracle_ir import compile_oracle_card
        cap='hand.cleanup.fixed_adjustment';raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'));changed=deepcopy(raw)
        next(r for r in changed['capabilities'] if r['id']==cap).update(status='blocked',blockers=['withdrawn current hand-size adjustment owner'])
        registry=CapabilityRegistry(changed);declarations=json.loads((ROOT/'platform/capability-evidence-declarations.json').read_text(encoding='utf-8'))
        index=build_index(registry_value=changed,declaration_source=declarations,discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry))
        for name in ('Thought Nibbler','Thought Eater','Thought Devourer','Gnat Miser','Locust Miser','Minamo Scrollkeeper'):
            ir=compile_oracle_card(self.db.lookup(name),capability_registry=registry,capability_profile='commander_review')
            self.assertNotEqual('exact',ir.status,name);blockers=[b for r in ir.material_residuals for b in r.blockers]
            self.assertTrue(any('status:'+cap+':blocked' in b for b in blockers),(name,blockers))
            self.assertFalse(any('evidence_index:unverified' in b for b in blockers))

    def test_full_original_compiler_and_current_adjustment_query_omissions_are_killed(self):
        method='test_actual_gnat_miser_changes_only_opponents_and_forces_exact_private_cleanup'
        for owner,omission in (('quorune.compiler.closed_static_nodes.no_maximum_hand_size_node',lambda **kwargs:None),
                ('quorune.maximum_hand_size.current_hand_size_adjustments',lambda *a:())):
            with patch(owner,omission):
                with self.assertRaises(AssertionError):getattr(type(self)(method),method)()
