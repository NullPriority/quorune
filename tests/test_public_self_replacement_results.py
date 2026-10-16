from __future__ import annotations

"""CR 608.2c/614.15: fixed self-replacements precede ordinary replacements."""

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
import test_self_public_zone_casting as zone_actions


class PublicSelfReplacementCompilerTests(unittest.TestCase):
    def test_replacement_schema_requires_owned_otherwise_shape_and_preserves_old_variants(self):
        from copy import deepcopy
        from quorune.resolution_conditions import validate_resolution_condition_instruction
        from quorune.rules.resolution_condition_shapes import resolution_condition_node_capabilities
        text="Draw two cards. If you have the city's blessing, draw three cards instead."
        ir=compile_oracle_card(record(text,'Sorcery'),capability_registry=load_default_capability_registry(),capability_profile='commander_review')
        node=ir.faces[0].nodes[0];effect=json.loads(json.dumps(node.effects[0]))
        self.assertEqual(3,effect['schema_version']);validate_resolution_condition_instruction(effect)
        for change in ({'schema_version':True},{'otherwise_effects':[]},
                {'otherwise_mechanic_ids':[]},{'unrepresented':'extra'}):
            with self.subTest(change=tuple(change)),self.assertRaises(ValueError):
                validate_resolution_condition_instruction({**effect,**change})
        changed=deepcopy(effect);changed['otherwise_effects'][0]['op']='unowned_effect'
        self.assertFalse(resolution_condition_node_capabilities(effects=(changed,),target_schema=None,mechanic_ids=node.mechanics))
        old={k:v for k,v in effect.items() if k not in {'schema_version','otherwise_effects','otherwise_mechanic_ids'}}
        before=deepcopy(old);validate_resolution_condition_instruction(old);self.assertEqual(before,old)
        kicked={**old,'schema_version':2,'condition':{'kind':'kicked_cast','schema_version':1},'cast_fact':False}
        before=deepcopy(kicked);validate_resolution_condition_instruction(kicked);self.assertEqual(before,kicked)

    def test_withdrawn_self_replacement_capability_prevents_admission(self):
        from quorune.rules.capabilities import CapabilityRegistry
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        next(cap for cap in raw['capabilities'] if cap['id']=='resolution.effect.fixed_public_self_replacement').update(
            status='blocked',blockers=['withdrawn'])
        ir=compile_oracle_card(record("Draw two cards. If you have the city's blessing, draw three cards instead.",'Sorcery'),
            capability_registry=CapabilityRegistry(raw),capability_profile='commander_review')
        self.assertNotEqual('exact',ir.status)

    def test_fixed_default_and_alternative_are_typed_independent_complete_branches(self):
        registry=load_default_capability_registry()
        for text in (
                "Draw two cards. If you have the city's blessing, draw three cards instead.",
                "Each player draws a card. If you have the city's blessing, instead only you draw a card.",
                "All creatures get -2/-2 until end of turn. If you have the city's blessing, instead only creatures your opponents control get -2/-2 until end of turn.",
                "Creatures you control get +1/+1 until end of turn. If you have the city's blessing, those creatures get +2/+2 until end of turn instead.",
                "Put a +1/+1 counter on each creature you control. If you have the city's blessing, put two +1/+1 counters on each creature you control instead."):
            with self.subTest(text=text):
                ir=compile_oracle_card(record(text,'Sorcery'),capability_registry=registry,capability_profile='commander_review')
                self.assertEqual('exact',ir.status,ir.material_residuals)
                node=ir.faces[0].nodes[0];wrapper=node.effects[-1]
                self.assertEqual(('apply_if_public_condition',3),(wrapper['op'],wrapper['schema_version']))
                self.assertEqual('$controller',wrapper['player']);self.assertTrue(wrapper['effects']);self.assertTrue(wrapper['otherwise_effects'])
                self.assertIn('resolution.effect.fixed_public_self_replacement',node.capability_dependencies)
                self.assertIsNone(node.target_schema)

    def test_optional_ambiguous_target_and_variable_self_replacements_remain_residual(self):
        registry=load_default_capability_registry()
        for text in (
                "Draw two cards. If you have the city's blessing, you may draw three cards instead.",
                "You may draw two cards. If you have the city's blessing, draw three cards instead.",
                "Target player draws two cards. If you have the city's blessing, that player draws three cards instead.",
                "Draw two cards. If you have the city's blessing, draw X cards instead."):
            with self.subTest(text=text):
                ir=compile_oracle_card(record(text,'Sorcery'),capability_registry=registry,capability_profile='commander_review')
                self.assertNotEqual('exact',ir.status)


class PublicSelfReplacementRuntimeTests(unittest.TestCase):
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

    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory()
        path=Path(cls.temp.name)/'public-self-replacement.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',
            ROOT/'tests/fixtures/event-player-subject-cards.json',
            ROOT/'tests/fixtures/ascend-city-blessing-cards.json',
            ROOT/'tests/fixtures/public-self-replacement-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Public replacement witness',[DeckEntry('Generic Bound Commander',1,'commander'),
            DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temp.cleanup()

    def test_actual_secrets_selects_self_replacement_before_external_draw_doubling(self):
        for blessed in (False,True):
            with self.subTest(blessed=blessed):
                session=self.session(410000+blessed);engine=session.engine
                self.lands(engine,9 if blessed else 8)
                reflection=self.add(engine,'Thought Reflection',ref='self-replacement-external-draw')
                source=self.add(engine,'Secrets of the Golden City',zone='hand',ref='actual-city-secrets')
                self.assert_closed(session,source);self.assert_closed(session,reflection)
                self.prepare(session,mana={'C':1,'U':2});self.checkpoint(session)
                hand=len(session.state.players['A'].zones['hand'])
                self.cast(session,'A',source);session=self.replay(session,load=True);self.resolve(session)
                self.assertEqual(blessed,self.blessed(session))
                self.assertEqual(hand-1+(6 if blessed else 4),len(session.state.players['A'].zones['hand']))
                drawn=[session.state.cards[oid].ref for oid in session.state.players['A'].zones['hand'][-(6 if blessed else 4):]]
                for viewer in 'BCD':
                    packet=json.dumps(session.packet('pilot:'+viewer,full=True))
                    for ref in drawn:self.assertNotIn(ref,packet)
                self.replay(session,load=True)

    def test_actual_demise_and_pride_replace_the_whole_fixed_modifier_once(self):
        for name in ('Golden Demise','Pride of Conquerors'):
            for blessed in (False,True):
                with self.subTest(name=name,blessed=blessed):
                    session=self.session(410010+blessed+len(name));engine=session.engine
                    self.lands(engine,9 if blessed else 8)
                    own=self.add(engine,'Memnite',ref='self-replacement-own-creature')
                    other=self.add(engine,'Memnite',seat='B',ref='self-replacement-opponent-creature')
                    source=self.add(engine,name,zone='hand',ref='actual-public-replacement-modifier')
                    mana={'C':1,'B':2} if name=='Golden Demise' else {'C':1,'W':1}
                    self.assert_closed(session,source);self.prepare(session,mana=mana);self.checkpoint(session)
                    self.cast(session,'A',source);session=self.replay(session,load=True);self.resolve(session)
                    if name=='Golden Demise':
                        self.assertEqual('battlefield' if blessed else 'graveyard',session.state.cards[own.object_id].zone)
                        self.assertEqual('graveyard',session.state.cards[other.object_id].zone)
                    else:
                        self.assertEqual(3 if blessed else 2,session.engine._numeric_stat(own.object_id,'power'))
                        self.assertEqual(1,session.engine._numeric_stat(other.object_id,'power'))
                    self.replay(session,load=True)

    def test_pending_self_replacement_uses_locked_controller_after_real_control_change(self):
        session=self.session(410030);engine=session.engine
        self.lands(engine,9)
        source=self.add(engine,'Generic City Replacement Enterer',zone='hand',ref='public-replacement-enterer')
        control=self.add(engine,'Generic Event Subject Control',seat='D',zone='hand',ref='public-replacement-control')
        self.assert_closed(session,source);self.prepare(session);engine.state.players['D'].mana_pool['W']=1
        self.checkpoint(session);self.cast(session,'A',source)
        for _ in range(16):
            if session.state.stack and session.state.stack[-1].kind=='triggered_ability':break
            self.assertTrue(session.act(session.pending_principals()[0],{'action_id':'pass'}).ok)
        else:self.fail('Actual entering source did not create its trigger')
        self.assertTrue(self.blessed(session));self.assertFalse(self.blessed(session,'D'))
        hand=len(session.state.players['A'].zones['hand'])
        self.cast(session,'D',control,[source.ref]);session=self.replay(session,load=True);self.resolve(session)
        self.assertEqual('D',session.state.cards[source.object_id].controller)
        self.assertEqual(hand+3,len(session.state.players['A'].zones['hand']))
        self.assertFalse(self.blessed(session,'D'));self.replay(session,load=True)

    def test_mandatory_earlier_life_prefix_survives_and_condition_precedes_default_draw(self):
        session=self.session(410040);engine=session.engine
        source=self.add(engine,'Generic Public Replacement Prefix',zone='hand',ref='public-replacement-prefix')
        player=session.state.players['A']
        for oid in list(player.zones['hand']):
            if oid!=source.object_id and len(player.zones['hand'])>3:
                player.zones['hand'].remove(oid);player.zones['library'].append(oid);session.state.cards[oid].zone='library'
        self.assert_closed(session,source);self.prepare(session);self.checkpoint(session)
        self.cast(session,'A',source);session=self.replay(session,load=True);self.resolve(session)
        self.assertEqual((41,3),(session.state.players['A'].life,len(session.state.players['A'].zones['hand'])))
        self.replay(session,load=True)


if __name__=='__main__':unittest.main()
