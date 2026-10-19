from __future__ import annotations

"""CR 614.15/120.2b: the selected fixed damage amount retains its source."""

from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import CapabilityRegistry,load_default_capability_registry
from quorune.resolution_conditions import cast_fact_result_is_fixed
from scripts.build_test_database import build_fixture_database
from test_bound_effect_programs import record
import test_compound_damage_recipients as controls


class KickedDamageReplacementCompilerTests(unittest.TestCase):
    def test_only_adopted_fixed_damage_source_is_attribution_and_legacy_conditions_stay_closed(self):
        from quorune.oracle_ir import _reviewed_atomic_effect_template
        effect=_reviewed_atomic_effect_template('Generic Witness deals 2 damage to each creature.',card_name='Generic Witness')[1][0]
        self.assertFalse(cast_fact_result_is_fixed((effect,),('cr-120-damage',)))
        self.assertTrue(cast_fact_result_is_fixed((effect,),('cr-120-damage',),allow_fixed_damage_source=True))
        for changed in ({**effect,'source':'$source.controller'},
                {**effect,'amount':{'kind':'declared_effect_amount','value':'$source.power'}},
                {**effect,'op':'unowned_damage'}):
            self.assertFalse(cast_fact_result_is_fixed((changed,),('cr-120-damage',),allow_fixed_damage_source=True))
        registry=load_default_capability_registry()
        for text in ('Generic Bound Compiler Fixture deals 1 damage to each creature. If this spell was kicked, it deals X damage to each creature instead.',
                'Generic Bound Compiler Fixture deals 1 damage to each creature. If this spell was kicked, another source deals 2 damage to each creature instead.',
                'Generic Bound Compiler Fixture deals 1 damage to each creature. If this spell was kicked, it deals 2 damage to each creature instead and that damage cannot be prevented.'):
            ir=compile_oracle_card(replace(record('Kicker {1}\n'+text,'Sorcery'),keywords=('Kicker',)),
                capability_registry=registry,capability_profile='commander_review')
            self.assertNotEqual('exact',ir.status,text)

    def test_fixed_damage_branches_require_each_existing_damage_and_predicate_owner(self):
        import json
        text='Kicker {1}\nGeneric Bound Compiler Fixture deals 1 damage to each creature without flying and each player. If this spell was kicked, it deals 4 damage to each creature without flying and each player instead.'
        source=replace(record(text,'Sorcery'),keywords=('Kicker',))
        registry=load_default_capability_registry()
        ir=compile_oracle_card(source,capability_registry=registry,capability_profile='commander_review')
        self.assertEqual('exact',ir.status,ir.material_residuals)
        node=next(n for n in ir.faces[0].nodes if n.effects)
        required=('damage.batch.fixed_set','damage.result.player_life','damage.source.stack_current_or_lki',
            'target.permanent.characteristic_predicate','resolution.effect.fixed_public_self_replacement')
        for cap in required:self.assertIn(cap,node.capability_dependencies)
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text())
        for cap in required:
            changed=deepcopy(raw)
            next(row for row in changed['capabilities'] if row['id']==cap).update(status='blocked',blockers=['withdrawn'])
            self.assertNotEqual('exact',compile_oracle_card(source,capability_registry=CapabilityRegistry(changed),capability_profile='commander_review').status,cap)


class KickedDamageReplacementRuntimeTests(unittest.TestCase):
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
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'kicked-damage.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',
            ROOT/'tests/fixtures/kicked-damage-replacement-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Kicked damage witnesses',[DeckEntry('Generic Bound Commander',1,'commander'),
            DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temporary.cleanup()

    def bodies(self,session):
        ground=self.add(session.engine,'Generic Bound Body',seat='B',ref='kicked-ground')
        flying=self.add(session.engine,'Generic Bound Body',seat='D',ref='kicked-flying')
        for card in (ground,flying):card.counters['+1/+1']=10
        ground.counters['hexproof']=1;flying.counters['flying']=1
        return ground,flying

    def test_original_cinderclasm_selects_one_or_two_damage_to_all_creatures(self):
        for kicked in (False,True):
            session=self.session(729701+int(kicked))
            source=self.add(session.engine,'Cinderclasm',zone='hand')
            ground,flying=self.bodies(session)
            private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
            self.prepare(session,mana={'R':2 if kicked else 1,'C':1});self.checkpoint(session)
            self.assertTrue(self.binding(session,source)['strict_capability_ready'])
            from quorune.preflight import card_semantic_status
            status=card_semantic_status(self.db.lookup('Cinderclasm'),session.engine.semantics,
                db=self.db,capability_registry=self.registry,capability_profile='commander_review')
            self.assertEqual('fully_playable',status['status'],status['unresolved'])
            action=self.action(session,source);before=authoritative_state_hash(session.state)
            denied=session.act('pilot:B',{'action_id':action['id'],'cost_option':'kicked' if kicked else 'normal','pay':'auto'})
            self.assertFalse(denied.ok);self.assertEqual(before,authoritative_state_hash(session.state))
            accepted=session.act('pilot:A',{'action_id':action['id'],'cost_option':'kicked' if kicked else 'normal','pay':'auto'})
            self.assertTrue(accepted.ok,accepted.summary)
            session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
            for card in (ground,flying):self.assertEqual(2 if kicked else 1,session.state.cards[card.object_id].marked_damage)
            self.assertEqual([40]*4,[session.state.players[s].life for s in 'ABCD'])
            self.assertEqual(0,sum(session.state.players['A'].mana_pool.values()))
            self.private_replay(session,private)

    def test_original_breath_and_canopy_select_current_flying_group_and_all_players(self):
        for name,color,select_flying in (('Breath of Darigaaz','R',False),('Canopy Surge','G',True)):
            for kicked in (False,True):
                session=self.session(729703+int(kicked)+len(name))
                source=self.add(session.engine,name,zone='hand');ground,flying=self.bodies(session)
                private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
                self.prepare(session,mana={color:1,'C':3 if kicked else 1});self.checkpoint(session)
                self.assertTrue(self.binding(session,source)['strict_capability_ready'])
                accepted=session.act('pilot:A',{'action_id':self.action(session,source)['id'],
                    'cost_option':'kicked' if kicked else 'normal','pay':'auto'})
                self.assertTrue(accepted.ok,accepted.summary)
                session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
                amount=4 if kicked else 1
                self.assertEqual(amount if select_flying else 0,session.state.cards[flying.object_id].marked_damage)
                self.assertEqual(0 if select_flying else amount,session.state.cards[ground.object_id].marked_damage)
                self.assertEqual([40-amount]*4,[session.state.players[s].life for s in 'ABCD'])
                self.private_replay(session,private)

    def test_self_replacement_amount_precedes_admitted_generic_damage_doubler(self):
        session=self.session(729708)
        source=self.add(session.engine,'Cinderclasm',zone='hand')
        external=self.add(session.engine,'Generic Kicked Damage Doubler');ground,flying=self.bodies(session)
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(session,mana={'R':2,'C':1});self.checkpoint(session)
        for card in (source,external):self.assertTrue(self.binding(session,card)['strict_capability_ready'],card.printed_name)
        accepted=session.act('pilot:A',{'action_id':self.action(session,source)['id'],'cost_option':'kicked','pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary)
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        for card in (ground,flying):self.assertEqual(4,session.state.cards[card.object_id].marked_damage)
        self.private_replay(session,private)

    def test_copied_kicked_cinderclasm_keeps_fact_and_independent_spell_source(self):
        session=self.session(729710)
        source=self.add(session.engine,'Cinderclasm',zone='hand')
        copy=self.add(session.engine,'Reverberate',seat='B',zone='hand');ground,flying=self.bodies(session)
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        session.state.players['B'].mana_pool.update({'R':2})
        self.prepare(session,mana={'R':2,'C':1});self.checkpoint(session)
        accepted=session.act('pilot:A',{'action_id':self.action(session,source)['id'],'cost_option':'kicked','pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary);original=session.state.stack[-1]
        self.cast(session,'B',copy,[original.ref]);copy_producer=session.state.stack[-1].ref
        session=self.replay(session,load=True)
        for _ in range(16):
            if not any(item.ref==copy_producer for item in session.state.stack):break
            self.assertEqual('priority',session.state.pending_decision.kind)
            result=session.act(session.pending_principals()[0],{'action_id':'pass'})
            self.assertTrue(result.ok,result.summary)
        else:self.fail('Actual copy producer did not resolve')
        copies=[item for item in session.state.stack if item.kind=='spell_copy']
        self.assertEqual(1,len(copies));copied=copies[0]
        self.assertIs(True,copied.context['kicked']);self.assertEqual('B',copied.controller)
        self.assertNotEqual(original.card_object_id,copied.card_object_id)
        self.assertNotEqual(session.engine._stack_source_ref(original),session.engine._stack_source_ref(copied))
        self.assertEqual(0,sum(session.state.players['A'].mana_pool.values()))
        self.assertEqual(0,sum(session.state.players['B'].mana_pool.values()))
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        for card in (ground,flying):self.assertEqual(4,session.state.cards[card.object_id].marked_damage)
        self.assertEqual(2,len(session.engine._current_turn_history('spell_cast')))
        self.private_replay(session,private)

    def test_countered_kicked_damage_has_no_damage_or_refund(self):
        session=self.session(729709)
        source=self.add(session.engine,'Breath of Darigaaz',zone='hand')
        counter=self.add(session.engine,'Counterspell',seat='B',zone='hand');ground,flying=self.bodies(session)
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        session.state.players['B'].mana_pool.update({'U':2})
        self.prepare(session,mana={'R':1,'C':3});self.checkpoint(session)
        accepted=session.act('pilot:A',{'action_id':self.action(session,source)['id'],'cost_option':'kicked','pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary)
        self.cast(session,'B',counter,[session.state.stack[-1].ref])
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        for card in (ground,flying):self.assertEqual(0,session.state.cards[card.object_id].marked_damage)
        self.assertEqual([40]*4,[session.state.players[s].life for s in 'ABCD'])
        self.assertEqual(0,sum(session.state.players['A'].mana_pool.values()))
        self.private_replay(session,private)

    def test_losing_paid_damage_branch_is_killed_by_fresh_original_cinderclasm(self):
        from quorune.semantic_choices.resolution_condition import PublicResolutionConditionHandler
        original=PublicResolutionConditionHandler.prepare
        def unpaid(handler,effect,context):
            if effect.get('schema_version')==4:effect={**effect,'cast_fact':False}
            return original(handler,effect,context)
        method='test_original_cinderclasm_selects_one_or_two_damage_to_all_creatures'
        with patch.object(PublicResolutionConditionHandler,'prepare',unpaid):
            with self.assertRaises(AssertionError):getattr(type(self)(method),method)()
