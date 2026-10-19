"""CR602.2b/605.3: a filtering mana ability must pay its own mana cost."""
from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT
from quorune.carddb import CardDatabase
from quorune.card_programs import bind_card_program_runtime
from quorune.card_programs.adapters import compile_best_available_card_program
from quorune.deck import DeckDefinition,DeckEntry
from quorune.errors import GameRuleError
from quorune.mana_activation import complete_mana_plan_activations
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
import test_bound_effect_programs as actions
import test_self_public_zone_casting as zones


class PricedManaSourceRuntimeTests(unittest.TestCase):
    register_fixture_cards_on_add=True
    session=actions.BoundEffectProgramRuntimeTests.session
    add=actions.BoundEffectProgramRuntimeTests.add
    checkpoint=actions.BoundEffectProgramRuntimeTests.checkpoint
    resolve=actions.BoundEffectProgramRuntimeTests.resolve
    replay=actions.BoundEffectProgramRuntimeTests.replay
    prepare=zones.SelfPublicZoneRuntimeTests.prepare
    cast=zones.SelfPublicZoneRuntimeTests.cast

    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();path=Path(cls.temp.name)/'priced-mana.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',
            ROOT/'tests/fixtures/priced-mana-source-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Priced mana',[DeckEntry('Generic Bound Commander',1,'commander'),
            DeckEntry('Generic Bound Plains',20)],['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):
        cls.db.close();cls.temp.cleanup()

    def assert_closed(self,session,card):
        program=compile_best_available_card_program(self.db,self.db.lookup(card.printed_name),
            semantic_registry=session.engine.semantics,capability_registry=self.registry,
            capability_profile='commander_review')
        binding=bind_card_program_runtime(program,capability_registry=self.registry,profile='commander_review')
        self.assertTrue(binding['strict_capability_ready'],binding)

    def private_replay(self,session,private):
        for viewer in 'ABD':self.assertNotIn(private.ref,json.dumps(session.packet('pilot:'+viewer,full=True)))
        self.assertIn(private.ref,json.dumps(session.packet('pilot:C',full=True)))
        return self.replay(session,load=True)

    def filtering_fixture(self,name,seed,*,mana=None):
        session=self.session(seed);source=self.add(session.engine,name)
        spell=self.add(session.engine,'Generic Bound Growth',zone='hand')
        target=self.add(session.engine,'Generic Bound Body')
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand',ref='foreign-private')
        self.prepare(session,mana=mana);self.checkpoint(session)
        self.assert_closed(session,source);self.assert_closed(session,spell)
        return session,source,spell,target,private

    def assert_unfunded_filter_rejected(self,name,seed):
        session,source,spell,target,private=self.filtering_fixture(name,seed)
        actions=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        self.assertFalse(any(row['id']=='cast:'+spell.ref for row in actions))
        before=authoritative_state_hash(session.state)
        command={'action_id':'cast:'+spell.ref,'targets':[target.ref],'pay':'auto'}
        for principal in ('pilot:D','pilot:A'):
            result=session.act(principal,command)
            self.assertFalse(result.ok,result.summary)
            self.assertEqual(before,authoritative_state_hash(session.state))
        self.assertFalse(session.state.cards[source.object_id].tapped)
        self.assertEqual('hand',session.state.cards[spell.object_id].zone)
        self.assertEqual(2,session.engine._numeric_stat(target.object_id,'power'))
        self.private_replay(session,private)

    def test_original_prism_cannot_filter_green_from_zero_mana_for_an_automatic_cast(self):
        self.assert_unfunded_filter_rejected('Mana Prism',71402001)

    def test_original_filter_lands_do_not_skip_their_mana_price(self):
        for i,name in enumerate(('Shimmering Grotto','Unknown Shores')):
            self.assert_unfunded_filter_rejected(name,71402010+i)

    def test_free_colorless_mode_of_each_original_remains_available_for_automatic_payment(self):
        for i,name in enumerate(('Mana Prism','Shimmering Grotto','Unknown Shores')):
            session=self.session(71402020+i);source=self.add(session.engine,name)
            spell=self.add(session.engine,'Sol Ring',zone='hand')
            private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
            self.prepare(session);self.checkpoint(session)
            self.assert_closed(session,source);self.assert_closed(session,spell)
            actions=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
            offered=next(row for row in actions if row['id']=='cast:'+spell.ref)
            result=session.act('pilot:A',{'action_id':offered['id'],'pay':'auto'})
            self.assertTrue(result.ok,result.summary)
            self.assertTrue(session.state.cards[source.object_id].tapped)
            session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
            self.assertEqual('battlefield',session.state.cards[spell.object_id].zone)
            self.assertEqual(0,sum(session.state.players['A'].mana_pool.values()))
            self.private_replay(session,private)

    def test_original_prism_can_pay_its_filter_cost_explicitly_then_cast_and_replay(self):
        session,source,spell,target,private=self.filtering_fixture('Mana Prism',71402030,mana={'C':1})
        actions=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        offered=next(row for row in actions if row['id'].startswith('activate:'+source.ref+':')
            and row.get('cost_summary',{}).get('m',{}).get('GENERIC')==1)
        before=authoritative_state_hash(session.state)
        command={'action_id':offered['id'],'mana_choice':'G','pay':'auto'}
        self.assertFalse(session.act('pilot:D',command).ok)
        self.assertEqual(before,authoritative_state_hash(session.state))
        result=session.act('pilot:A',command);self.assertTrue(result.ok,result.summary)
        self.assertTrue(session.state.cards[source.object_id].tapped)
        self.assertEqual((0,1),tuple(session.state.players['A'].mana_pool[c] for c in ('C','G')))
        session=self.replay(session,load=True)
        self.cast(session,'A',spell,[target.ref])
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertEqual(4,session.engine._numeric_stat(target.object_id,'power'))
        self.assertEqual(0,sum(session.state.players['A'].mana_pool.values()))
        self.private_replay(session,private)

    def test_original_prism_cannot_tap_for_its_own_filter_payment(self):
        session,source,spell,target,private=self.filtering_fixture('Mana Prism',71402031)
        abilities=session.engine._activated_abilities(source)
        priced=next(a for a in abilities if a.mana.get('GENERIC')==1)
        before=authoritative_state_hash(session.state)
        result=session.act('pilot:A',{'action_id':'activate:'+source.ref+':'+priced.ability_id,
            'mana_choice':'G','pay':'auto'})
        self.assertFalse(result.ok,result.summary)
        self.assertEqual(before,authoritative_state_hash(session.state))
        self.assertFalse(session.state.cards[source.object_id].tapped)
        self.private_replay(session,private)

    def test_prepared_filter_plan_rejects_a_paid_mode_before_any_write(self):
        session,source,spell,target,private=self.filtering_fixture('Mana Prism',71402032)
        before=authoritative_state_hash(session.state)
        with self.assertRaises(GameRuleError):
            complete_mana_plan_activations(session.engine,'A',[{'source':source.ref,'bundle':{'G':1}}])
        self.assertEqual(before,authoritative_state_hash(session.state))

    def test_omitting_the_price_guard_is_killed_by_a_fresh_native_cast_witness(self):
        method='test_original_prism_cannot_filter_green_from_zero_mana_for_an_automatic_cast'
        with patch('quorune.mana_source_discovery._mana_plan_cost_is_supported',return_value=True):
            with self.assertRaises(AssertionError):getattr(type(self)(method),method)()
