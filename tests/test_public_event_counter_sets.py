from __future__ import annotations

"""CR 603.3a/109.5: public-event fixed sets retain the trigger controller."""

from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest

from common import ROOT
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_granted_skulk import record
import test_bound_effect_programs as actions
import test_ascend_city_blessing as ascend_actions
import test_fixed_tap_payments as tap_actions
import test_self_public_zone_casting as zone_actions


class PublicEventCounterSetCompilerTests(unittest.TestCase):
    def test_fixed_set_producer_rejects_unowned_or_mixed_conditional_leaves(self):
        from copy import deepcopy
        from quorune.oracle_ir import _reviewed_effect_template
        from quorune.rules.public_event_counter_set_shapes import fixed_public_event_set_result_capabilities
        value=_reviewed_effect_template('Put a +1/+1 counter on each creature you control.',card_name='Set producer',source_is_permanent=True)
        self.assertTrue(fixed_public_event_set_result_capabilities(effects=value[1],target_schema=None,mechanic_ids=value[3]))
        for change in ({'amount':True},{'amount':{'kind':'unknown_quantity'}},{'source':'$context.target'},
                {'set':{**value[1][0]['set'],'unknown':True}}):
            effect={**deepcopy(value[1][0]),**change}
            self.assertFalse(fixed_public_event_set_result_capabilities(effects=(effect,),target_schema=None,mechanic_ids=value[3]))
        registry=load_default_capability_registry()
        text="Whenever equipped creature attacks, put a +1/+1 counter on each creature you control. If you have the city's blessing, draw a card instead."
        self.assertNotEqual('exact',compile_oracle_card(record(text,'Artifact — Equipment'),capability_registry=registry,capability_profile='commander_review').status)

    def test_withdrawn_public_set_event_owner_prevents_promotion(self):
        from quorune.rules.capabilities import CapabilityRegistry
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        next(cap for cap in raw['capabilities'] if cap['id']=='counter.producer.fixed_public_set_event').update(
            status='blocked',blockers=['withdrawn'])
        ir=compile_oracle_card(record('Whenever equipped creature attacks, put a +1/+1 counter on each creature you control.','Artifact — Equipment'),
            capability_registry=CapabilityRegistry(raw),capability_profile='commander_review')
        self.assertNotEqual('exact',ir.status)

    def test_public_event_set_results_and_complete_conditional_sets_close_independently(self):
        registry=load_default_capability_registry()
        for text,kind,new_owner in (
                ('Whenever equipped creature attacks, put a +1/+1 counter on each creature you control.','Artifact — Equipment',True),
                ('Whenever enchanted creature attacks or blocks, put a +1/+1 counter on each creature you control.','Enchantment — Aura',True),
                ('Whenever you cast a blue spell, put a charge counter on each artifact you control.','Enchantment',False),
                ('Whenever another creature you control enters, put a +1/+1 counter on each creature you control.','Enchantment',False),
                ("Whenever equipped creature attacks, put a +1/+1 counter on each creature you control. If you have the city's blessing, put two +1/+1 counters on each creature you control instead.",'Artifact — Equipment',True)):
            with self.subTest(text=text):
                ir=compile_oracle_card(record(text,kind),capability_registry=registry,capability_profile='commander_review')
                self.assertEqual('exact',ir.status,ir.material_residuals)
                node=ir.faces[0].nodes[0]
                self.assertIsNone(node.target_schema)
                self.assertEqual(new_owner,'counter.producer.fixed_public_set_event' in node.capability_dependencies)
                self.assertIn('counter.producer.fixed_permanent_set_effect',node.capability_dependencies)

    def test_unestablished_sets_targets_and_variable_results_remain_residual(self):
        registry=load_default_capability_registry()
        for text in (
                'Whenever equipped creature attacks, put a +1/+1 counter on each of them.',
                'Whenever equipped creature attacks, put X +1/+1 counters on each creature you control.',
                'Whenever a player rewrites history, put a +1/+1 counter on each creature you control.'):
            with self.subTest(text=text):
                ir=compile_oracle_card(record(text,'Artifact — Equipment'),capability_registry=registry,capability_profile='commander_review')
                self.assertNotEqual('exact',ir.status)


class PublicEventCounterSetRuntimeTests(unittest.TestCase):
    register_fixture_cards_on_add=True
    session=actions.BoundEffectProgramRuntimeTests.session
    add=actions.BoundEffectProgramRuntimeTests.add
    checkpoint=actions.BoundEffectProgramRuntimeTests.checkpoint
    resolve=actions.BoundEffectProgramRuntimeTests.resolve
    replay=actions.BoundEffectProgramRuntimeTests.replay
    prepare=zone_actions.SelfPublicZoneRuntimeTests.prepare
    cast=zone_actions.SelfPublicZoneRuntimeTests.cast
    assert_closed=zone_actions.SelfPublicZoneRuntimeTests.assert_closed
    lands=ascend_actions.AscendRuntimeTests.lands
    blessed=ascend_actions.AscendRuntimeTests.blessed
    advance_to=tap_actions.FixedTapPaymentRuntimeTests.advance_to

    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory()
        path=Path(cls.temp.name)/'public-event-counter-sets.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',
            ROOT/'tests/fixtures/event-player-subject-cards.json',
            ROOT/'tests/fixtures/ascend-city-blessing-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Public counter set witness',[DeckEntry('Generic Bound Commander',1,'commander'),
            DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temp.cleanup()

    def test_actual_anduril_cast_equip_attack_and_pending_departure_place_one_or_two_once(self):
        for city,depart in ((False,False),(True,False),(True,True)):
            with self.subTest(city=city,depart=depart):
                session=self.session(411000+city+depart);engine=session.engine
                self.lands(engine,7 if city else 2)
                source=self.add(engine,'Andúril, Narsil Reforged',zone='hand',ref='actual-public-set-anduril')
                attacker=self.add(engine,'Generic Bound Body',ref='public-set-attacker');attacker.acquired_control_turn_count=-1
                ally=self.add(engine,'Generic Bound Body',ref='public-set-ally')
                opponent=self.add(engine,'Generic Bound Body',seat='B',ref='public-set-enemy')
                removal=self.add(engine,'Disenchant',zone='hand',ref='public-set-source-removal')
                private=self.add(engine,'Generic Bound Growth',seat='C',zone='hand',ref='public-set-private')
                self.assert_closed(session,source);self.prepare(session,mana={'C':5});self.checkpoint(session)
                self.cast(session,'A',source);self.resolve(session)
                equip=next(r for r in session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
                    if r.get('source')==source.ref and r['id'].startswith('activate:'))
                accepted=session.act('pilot:A',{'action_id':equip['id'],'targets':[attacker.ref],'pay':'auto'})
                self.assertTrue(accepted.ok,accepted.summary);self.resolve(session)
                self.assertEqual(city,self.blessed(session))
                self.advance_to(session,'combat.attackers')
                accepted=session.act('pilot:A',{'action_id':'attack','attackers':{attacker.ref:'B'}})
                self.assertTrue(accepted.ok,accepted.summary)
                self.assertEqual('A',session.state.stack[-1].controller)
                session=self.replay(session,load=True)
                if depart:self.cast(session,'A',removal,[source.ref])
                self.resolve(session)
                for card in (attacker,ally):self.assertEqual(2 if city else 1,session.state.cards[card.object_id].counters.get('+1/+1',0))
                self.assertEqual(0,session.state.cards[opponent.object_id].counters.get('+1/+1',0))
                if depart:self.assertEqual('graveyard',session.state.cards[source.object_id].zone)
                for viewer in 'ABD':self.assertNotIn(private.ref,json.dumps(session.packet('pilot:'+viewer,full=True)))
                self.replay(session,load=True)

    def test_real_attacker_control_change_does_not_change_equipment_trigger_actor_or_set(self):
        from quorune.attachments import attach_objects
        session=self.session(411010);engine=session.engine
        self.lands(engine,8)
        source=self.add(engine,'Andúril, Narsil Reforged',ref='public-set-cross-controller-equipment')
        attacker=self.add(engine,'Generic Bound Body',ref='public-set-owned-but-stolen-attacker')
        ally=self.add(engine,'Generic Bound Body',ref='public-set-remaining-beneficiary')
        attach_objects(engine.state.cards,source,attacker,source_timestamp=engine._next_zone_timestamp(),players=engine.state.players)
        control=self.add(engine,'Generic Event Subject Control',seat='D',zone='hand',ref='public-set-real-control')
        self.assert_closed(session,source);self.prepare(session,actor='D',active='D',mana={'W':1});self.checkpoint(session)
        self.assertTrue(self.blessed(session));self.assertFalse(self.blessed(session,'D'))
        self.cast(session,'D',control,[attacker.ref]);self.resolve(session)
        self.advance_to(session,'combat.attackers')
        accepted=session.act('pilot:D',{'action_id':'attack','attackers':{attacker.ref:'B'}})
        self.assertTrue(accepted.ok,accepted.summary)
        self.assertEqual('A',session.state.stack[-1].controller)
        session=self.replay(session,load=True);self.resolve(session)
        self.assertEqual(('D',0),(session.state.cards[attacker.object_id].controller,session.state.cards[attacker.object_id].counters.get('+1/+1',0)))
        self.assertEqual(2,session.state.cards[ally.object_id].counters.get('+1/+1',0))
        self.assertTrue(self.blessed(session));self.assertFalse(self.blessed(session,'D'))
        self.replay(session,load=True)


if __name__=='__main__':unittest.main()
