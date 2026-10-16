from __future__ import annotations

"""CR 603.5: source-self optional results choose targets before resolution."""

import unittest
import json
from pathlib import Path
import tempfile
from common import ROOT
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.record import authoritative_state_hash
from scripts.build_test_database import build_fixture_database
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from test_granted_skulk import record
import test_bound_effect_programs as actions
import test_self_public_zone_casting as zone_actions


class SourceSelfOptionalCompilerTests(unittest.TestCase):
    def test_optional_source_self_result_keeps_target_timing_and_complete_span(self):
        registry=load_default_capability_registry()
        for event in ('enters','dies'):
            text=f'When this creature {event}, you may have it deal 2 damage to any target.'
            with self.subTest(event=event):
                ir=compile_oracle_card(record(text,'Creature — Human'),capability_registry=registry,capability_profile='commander_review')
                self.assertEqual('exact',ir.status,ir.material_residuals)
                node=ir.faces[0].nodes[0]
                self.assertEqual(text,node.text)
                self.assertIsNotNone(node.target_schema)
                self.assertEqual('offer_optional_effect',node.effects[0]['op'])
                self.assertEqual('$source',node.effects[0]['effects'][0]['source'])
                self.assertIn('effect.choice.optional_fixed',node.capability_dependencies)
        for result in ('get +1/+1','gain flying'):
            text=f'When this creature enters, you may have it {result} until end of turn.'
            with self.subTest(result=result):
                ir=compile_oracle_card(record(text,'Creature — Human'),capability_registry=registry,capability_profile='commander_review')
                self.assertEqual('exact',ir.status,ir.material_residuals)
                self.assertEqual('$source.zone_object',ir.faces[0].nodes[0].effects[0]['effects'][0]['card'])

    def test_unestablished_or_new_object_pronouns_are_not_rebound_to_source(self):
        registry=load_default_capability_registry()
        for text in ('Whenever another creature enters, you may have it deal 2 damage to any target.',
                     'When this creature enters, create a 1/1 green Elf creature token. You may have it deal 2 damage to any target.',
                     'You may have it deal 2 damage to any target.'):
            with self.subTest(text=text):
                ir=compile_oracle_card(record(text,'Creature — Human'),capability_registry=registry,capability_profile='commander_review')
                self.assertNotEqual('exact',ir.status)


class SourceSelfOptionalRuntimeTests(unittest.TestCase):
    register_fixture_cards_on_add=True
    session=actions.BoundEffectProgramRuntimeTests.session
    add=actions.BoundEffectProgramRuntimeTests.add
    checkpoint=actions.BoundEffectProgramRuntimeTests.checkpoint
    resolve=actions.BoundEffectProgramRuntimeTests.resolve
    replay=actions.BoundEffectProgramRuntimeTests.replay
    prepare=zone_actions.SelfPublicZoneRuntimeTests.prepare
    cast=zone_actions.SelfPublicZoneRuntimeTests.cast
    assert_closed=zone_actions.SelfPublicZoneRuntimeTests.assert_closed

    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory()
        path=Path(cls.temporary.name).resolve()/'source-self-optional.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',
            ROOT/'tests/fixtures/event-player-subject-cards.json',
            ROOT/'tests/fixtures/fixed-damage-source-lki-cards.json',
            ROOT/'tests/fixtures/source-self-optional-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Source optional witness',[DeckEntry('Generic Bound Commander',1,'commander'),
            DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temporary.cleanup()

    def choose(self,session,actor='A',**response):
        result=session.act('pilot:'+actor,{'action_id':'choose',**response})
        self.assertTrue(result.ok,result.summary)

    def test_original_rig_selects_a_flying_target_before_apply_or_decline(self):
        for apply in (False,True):
            with self.subTest(apply=apply):
                session=self.session(402001+apply);engine=session.engine
                source=self.add(engine,"Geistcatcher's Rig",zone='hand',ref='actual-optional-rig')
                flyer=self.add(engine,'Generic Optional Flyer',seat='B',ref='rig-flying-target')
                ground=self.add(engine,'Generic Optional Ground',seat='B',ref='rig-illegal-ground-target')
                private=self.add(engine,'Generic Bound Growth',seat='D',zone='hand',ref='rig-private')
                self.assert_closed(session,source);self.prepare(session,mana={'C':6});self.checkpoint(session)
                self.cast(session,'A',source)
                self.assertEqual('semantic.target',self.resolve(session).kind)
                before=authoritative_state_hash(session.state)
                self.assertFalse(session.act('pilot:A',{'action_id':'choose','targets':[ground.ref]}).ok)
                self.assertEqual(before,authoritative_state_hash(session.state))
                session=self.replay(session,load=True);self.choose(session,targets=[flyer.ref])
                self.assertEqual('semantic.choice',self.resolve(session).kind)
                self.assertEqual(0,session.state.cards[flyer.object_id].marked_damage)
                session=self.replay(session,load=True)
                self.choose(session,choice='apply' if apply else 'decline');self.resolve(session)
                self.assertEqual(4 if apply else 0,session.state.cards[flyer.object_id].marked_damage)
                self.assertEqual(0,session.state.cards[ground.object_id].marked_damage)
                for viewer in 'ABC':self.assertNotIn(private.ref,json.dumps(session.packet('pilot:'+viewer,full=True)))
                self.replay(session,load=True)

    def test_original_arsonist_optional_death_damage_keeps_lki_and_trigger_controller(self):
        for apply in (False,True):
            with self.subTest(apply=apply):
                session=self.session(402010+apply);engine=session.engine
                source=self.add(engine,'Goblin Arsonist',zone='hand',ref='actual-optional-arsonist')
                grant=self.add(engine,'Generic Damage Source Lifelink',zone='hand',ref='optional-death-lifelink')
                control=self.add(engine,'Generic Event Subject Control',seat='D',zone='hand',ref='optional-death-control')
                kill=self.add(engine,'Generic Damage Source Kill',zone='hand',ref='optional-death-kill')
                self.assert_closed(session,source);self.prepare(session,mana={'R':1});engine.state.players['D'].mana_pool['W']=1
                self.checkpoint(session);self.cast(session,'A',source);self.resolve(session)
                self.cast(session,'A',grant,[source.ref]);self.cast(session,'D',control,[source.ref]);self.resolve(session)
                self.cast(session,'A',kill,[source.ref]);self.assertEqual('semantic.target',self.resolve(session).kind)
                self.assertEqual('D',session.state.stack[-1].controller)
                session=self.replay(session,load=True);self.choose(session,'D',targets=['B'])
                self.assertEqual('semantic.choice',self.resolve(session).kind)
                self.assertEqual(40,session.state.players['B'].life)
                session=self.replay(session,load=True)
                self.choose(session,'D',choice='apply' if apply else 'decline');self.resolve(session)
                self.assertEqual({'A':40,'B':39 if apply else 40,'C':40,'D':41 if apply else 40},
                    {seat:player.life for seat,player in session.state.players.items()})
                self.replay(session,load=True)

    def test_original_clash_grants_to_entering_recipients_and_pending_trigger_survives_grant_departure(self):
        session=self.session(402020);engine=session.engine
        clash=self.add(engine,'Clash of Realities',zone='hand',ref='actual-source-optional-clash')
        spirit_target=self.add(engine,'Generic Optional Spirit',seat='C',ref='clash-spirit-target')
        other_target=self.add(engine,'Generic Optional Ground',seat='C',ref='clash-nonspirit-target')
        spirit=self.add(engine,'Generic Optional Spirit',seat='B',zone='hand',ref='clash-entering-spirit')
        other=self.add(engine,'Generic Optional Nonspirit',seat='D',zone='hand',ref='clash-entering-nonspirit')
        bounce=self.add(engine,'Generic Optional Bounce',zone='hand',ref='clash-real-source-departure')
        self.add(engine,'Island',ref='clash-bounce-mana');self.add(engine,'Mountain',ref='clash-recast-mana')
        self.assert_closed(session,clash);self.prepare(session,mana={'C':8,'R':3});self.checkpoint(session)
        self.cast(session,'A',clash);self.resolve(session);self.assertFalse(session.state.stack)
        self.cast(session,'B',spirit);self.assertEqual('semantic.target',self.resolve(session).kind)
        self.assertEqual('B',session.state.stack[-1].controller)
        before=authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:B',{'action_id':'choose','targets':[spirit_target.ref]}).ok)
        self.assertEqual(before,authoritative_state_hash(session.state))
        self.choose(session,'B',targets=[other_target.ref])
        self.cast(session,'A',bounce,[clash.ref]);self.assertEqual('semantic.choice',self.resolve(session).kind)
        self.assertEqual('hand',session.state.cards[clash.object_id].zone)
        session=self.replay(session,load=True);self.choose(session,'B',choice='apply');self.resolve(session)
        self.assertEqual(3,session.state.cards[other_target.object_id].marked_damage)
        self.cast(session,'A',clash);self.resolve(session);self.assertFalse(session.state.stack)
        self.cast(session,'D',other);self.assertEqual('semantic.target',self.resolve(session).kind)
        self.assertEqual('D',session.state.stack[-1].controller)
        self.choose(session,'D',targets=[spirit_target.ref]);self.assertEqual('semantic.choice',self.resolve(session).kind)
        session=self.replay(session,load=True);self.choose(session,'D',choice='apply');self.resolve(session)
        self.assertEqual(3,session.state.cards[spirit_target.object_id].marked_damage)
        self.replay(session,load=True)
