from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest import mock
from types import SimpleNamespace
from quorune.object_predicate import ObjectQuerySpec
from quorune.object_query import ObjectQueryResult,object_matches_query
from quorune.continuous_effects import CharacteristicState,ContinuousEffect,ContinuousOperation,Layer,evaluate_continuous_effects
from quorune.characteristic_evaluation import type_parts
from common import ROOT
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.rules.capabilities import load_default_capability_registry
from quorune.card_programs import bind_card_program_runtime
from quorune.card_programs.adapters import compile_best_available_card_program
from quorune.semantics import SemanticRegistry
from quorune.record import authoritative_state_hash
import json
from scripts.build_test_database import build_fixture_database
import test_bound_effect_programs as actions
import test_self_public_zone_casting as zone_actions

FIXTURE=ROOT/'tests/fixtures/fixed-basic-type-setting-cards.json'
CLOSED_SETTING={'Evil Presence','Contaminated Ground','Magus of the Moon','Harbinger of the Seas',
    'Blood Moon','Spreading Seas','Tainted Well','Lush Growth','Lingering Mirage',"Sea's Claim"}


class FixedBasicTypeSettingKernelTests(unittest.TestCase):
    def test_nonbasic_query_is_supertype_based_and_preserves_older_serialization(self):
        older=ObjectQuerySpec(types_all=('land',)).to_dict()
        self.assertNotIn('excluded_supertypes',older)
        self.assertEqual(older,ObjectQuerySpec.from_dict(older).to_dict())
        legacy={k:v for k,v in older.items() if k!='types_any'}
        self.assertEqual(legacy,ObjectQuerySpec.from_dict(legacy).to_dict())
        query=ObjectQuerySpec(types_all=('land',),excluded_supertypes=('basic',))
        self.assertEqual(query,ObjectQuerySpec.from_dict(query.to_dict()))
        for supertypes,expected in (((),True),(('legendary','snow'),True),(('basic','snow'),False)):
            row=ObjectQueryResult(object_id='land',ref='ref',printed_name='Named land',owner='A',controller='B',
                zone='battlefield',types=('land',),subtypes=('mountain',),supertypes=supertypes)
            self.assertEqual(expected,object_matches_query(row,query))

    def test_setting_removes_only_land_subtypes_and_printed_abilities_before_intrinsic_mana(self):
        kinds,subtypes,supers=type_parts('Legendary Snow Artifact Land Creature — Urza’s Tower Elf')
        self.assertIn("urza's",subtypes)
        state=CharacteristicState('Unchanged name',card_types=kinds,subtypes=subtypes,supertypes=supers,
            colors={'G'},abilities=['Indestructible'],power=3,toughness=4)
        effect=ContinuousEffect('setting','source',Layer.TYPE,'4',1,
            (ContinuousOperation('set_basic_land_types',['island']),))
        values=evaluate_continuous_effects(state,[effect]).characteristics
        self.assertEqual({'island','elf'},set(values['subtypes']))
        self.assertEqual(kinds,set(values['card_types']));self.assertEqual(supers,set(values['supertypes']))
        self.assertEqual({'G'},set(values['colors']));self.assertEqual((3,4),(values['power'],values['toughness']))
        self.assertEqual([],values['abilities'])
        self.assertEqual(['intrinsic_island'],[row['ability_id'] for row in values['activated_abilities']])
        for value in ([],['desert'],['island','island']):
            with self.assertRaises(ValueError):ContinuousOperation('set_basic_land_types',value)


class FixedBasicTypeSettingRuntimeTests(unittest.TestCase):
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
        cls.temp=tempfile.TemporaryDirectory();path=Path(cls.temp.name)/'setting.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',FIXTURE],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Basic setting',[DeckEntry('Generic Bound Commander',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temp.cleanup()

    def test_actual_blood_moon_removes_nonbasic_entry_replacements_before_land_play(self):
        session=self.session(30507201);engine=session.engine
        moon=self.add(engine,'Blood Moon',zone='hand')
        vivid=self.add(engine,'Vivid Creek',zone='hand')
        basic=self.add(engine,'Snow-Covered Island',seat='B')
        private=self.add(engine,'Generic Bound Growth',seat='C',zone='hand',ref='setting-private')
        self.assert_closed(session,moon);self.assert_closed(session,vivid)
        self.prepare(session,mana={'C':2,'R':1});self.checkpoint(session)
        self.cast(session,'A',moon);self.resolve(session)
        actions=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        action=next(a for a in actions if a['id']=='play-land:'+vivid.ref)
        before=authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:C',{'action_id':action['id']}).ok)
        self.assertEqual(before,authoritative_state_hash(session.state))
        result=session.act('pilot:A',{'action_id':action['id']});self.assertTrue(result.ok,result.summary)
        current=session.state.cards[vivid.object_id]
        self.assertEqual('battlefield',current.zone);self.assertFalse(current.tapped)
        self.assertEqual({},current.counters)
        data=session.engine._effective_card_data(current)
        self.assertEqual({'mountain'},session.engine._type_parts(data['type_line'])[1])
        self.assertNotIn('basic',session.engine._type_parts(data['type_line'])[2])
        self.assertEqual({'basic','snow'},session.engine._type_parts(session.engine._effective_card_data(basic)['type_line'])[2])
        self.assertEqual({'intrinsic_mountain'},{a.ability_id for a in session.engine._activated_abilities(current)})
        for viewer in 'ABD':self.assertNotIn(private.ref,json.dumps(session.packet('pilot:'+viewer,full=True)))
        self.replay(session,load=True)

    def test_all_setting_originals_require_complete_sibling_admission(self):
        expected=CLOSED_SETTING|{'Stormtide Leviathan','Swampbenders','Dryad of the Ilysian Grove',"Nylea's Presence",'Prismatic Omen'}
        names=[r['name'] for r in json.loads(FIXTURE.read_text(encoding='utf-8'))['cards']][:25]
        for name in names:
            with self.subTest(name=name):
                p=compile_best_available_card_program(self.db,self.db.lookup(name),semantic_registry=SemanticRegistry(),
                    capability_registry=self.registry,capability_profile='commander_review')
                binding=bind_card_program_runtime(p,capability_registry=self.registry,profile='commander_review')
                self.assertEqual(name in expected,binding['strict_capability_ready'],binding['blockers'])

    def test_setting_omission_is_killed_by_actual_blood_moon_entry(self):
        from quorune.semantic_runtime.basic_type_setting import FixedBasicTypeSettingHandler
        with mock.patch.object(FixedBasicTypeSettingHandler,'lower',return_value=()):
            with self.assertRaises(AssertionError):
                self.test_actual_blood_moon_removes_nonbasic_entry_replacements_before_land_play()

    def test_actual_seas_removes_printed_mana_and_indestructible_and_source_loss_restores(self):
        for destroy_land in (False,True):
            with self.subTest(destroy_land=destroy_land):
                session=self.session(30507202+destroy_land);engine=session.engine
                land=self.add(engine,'Darksteel Citadel')
                aura=self.add(engine,'Spreading Seas',zone='hand')
                destroy=self.add(engine,'Disenchant',zone='hand')
                self.assert_closed(session,aura)
                self.prepare(session,mana={'C':1,'U':1});self.checkpoint(session)
                self.cast(session,'A',aura,[land.ref]);session=self.replay(session,load=True);self.resolve(session)
                land=session.state.cards[land.object_id]
                data=session.engine._effective_card_data(land)
                self.assertEqual({'artifact','land'},session.engine._type_parts(data['type_line'])[0])
                self.assertEqual({'island'},session.engine._type_parts(data['type_line'])[1])
                self.assertNotIn('Indestructible',data['keywords'])
                self.assertEqual({'intrinsic_island'},{a.ability_id for a in session.engine._activated_abilities(land)})
                self.replay(session,load=True)
                self.prepare(session,mana={'C':1,'W':1});self.checkpoint(session)
                self.cast(session,'A',destroy,[land.ref if destroy_land else aura.ref]);self.resolve(session)
                self.assertEqual('graveyard',session.state.cards[aura.object_id].zone)
                current=session.state.cards[land.object_id]
                if destroy_land:self.assertEqual('graveyard',current.zone)
                else:
                    self.assertEqual('battlefield',current.zone)
                    self.assertIn('Indestructible',session.engine._effective_card_data(current)['keywords'])
                    self.assertFalse(any(a.ability_id.startswith('intrinsic_') for a in session.engine._activated_abilities(current)))
                    self.assertTrue(any(a.mana_ability for a in session.engine._activated_abilities(current)))
                self.replay(session,load=True)

    def test_actual_moon_urborg_dependency_is_independent_of_timestamp(self):
        for moon_first in (False,True):
            with self.subTest(moon_first=moon_first):
                session=self.session(30507204+moon_first);engine=session.engine
                if moon_first:moon=self.add(engine,'Blood Moon')
                urborg=self.add(engine,'Urborg, Tomb of Yawgmoth')
                if not moon_first:moon=self.add(engine,'Blood Moon')
                basic=self.add(engine,'Snow-Covered Island',seat='B')
                self.assert_closed(session,moon);self.assert_closed(session,urborg)
                self.prepare(session);self.checkpoint(session)
                self.assertEqual({'mountain'},engine._type_parts(engine._effective_card_data(urborg)['type_line'])[1])
                self.assertEqual({'island'},engine._type_parts(engine._effective_card_data(basic)['type_line'])[1])
                self.assertEqual({'intrinsic_mountain'},{a.ability_id for a in engine._activated_abilities(urborg)})
                self.replay(session,load=True)

    def test_actual_magus_type_setting_survives_later_humble_and_ends_on_departure(self):
        session=self.session(30507206);engine=session.engine
        source=self.add(engine,'Magus of the Moon')
        land=self.add(engine,'Darksteel Citadel',seat='B')
        humble=self.add(engine,'Humble',zone='hand')
        bounce=self.add(engine,'Unsummon',zone='hand')
        self.assert_closed(session,source)
        self.prepare(session,mana={'C':1,'W':1});self.checkpoint(session)
        self.cast(session,'A',humble,[source.ref]);self.resolve(session)
        self.assertEqual((0,1),tuple(engine._numeric_stat(source.object_id,s) for s in ['power','toughness']))
        self.assertEqual({'mountain'},engine._type_parts(engine._effective_card_data(land)['type_line'])[1])
        self.replay(session,load=True)
        self.prepare(session,mana={'U':1});self.checkpoint(session)
        self.cast(session,'A',bounce,[source.ref]);self.resolve(session)
        self.assertEqual('hand',source.zone)
        self.assertEqual(set(),engine._type_parts(engine._effective_card_data(land)['type_line'])[1])
        self.assertIn('Indestructible',engine._effective_card_data(land)['keywords'])
        self.replay(session,load=True)
