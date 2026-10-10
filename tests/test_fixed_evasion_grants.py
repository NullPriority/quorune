from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT
from quorune.carddb import CardDatabase
from quorune.compiler.continuous_templates import attached_fixed_characteristics_handler, fixed_query_keyword_grant_handler
from quorune.deck import DeckDefinition, DeckEntry
from quorune.model import CombatState
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_qualified_zone_event_queries import query_record
import test_bound_effect_programs as witnesses


class FixedEvasionGrantCompilerTests(unittest.TestCase):
    def test_fixed_basic_landwalk_and_horsemanship_grants_declare_exact_consumers(self):
        registry=load_default_capability_registry()
        for keyword,capability in (
            ('plainswalk','combat.block.landwalk.basic_type'),('islandwalk','combat.block.landwalk.basic_type'),
            ('swampwalk','combat.block.landwalk.basic_type'),('mountainwalk','combat.block.landwalk.basic_type'),
            ('forestwalk','combat.block.landwalk.basic_type'),('horsemanship','combat.block.horsemanship'),
        ):
            with self.subTest(keyword=keyword):
                for text in ('Creatures you control have '+keyword+'.','Enchanted creature has '+keyword+'.'):
                    parsed=(attached_fixed_characteristics_handler(text) if text.startswith('Enchanted')
                            else fixed_query_keyword_grant_handler(text))
                    self.assertIsNotNone(parsed)
                    self.assertIn(capability,parsed[2])
                ir=compile_oracle_card(query_record('Creatures you control have '+keyword+'.'),capability_registry=registry)
                self.assertEqual('exact',ir.status,ir.material_residuals)

    def test_grant_boundaries_and_missing_evasion_consumer_fail_closed(self):
        for text in ('Creatures you control have nonbasic landwalk.','Creatures you control have desertwalk.',
                     'Creatures you control have landwalk of the chosen type.','Enchanted creature has snow forestwalk.'):
            self.assertIsNone(fixed_query_keyword_grant_handler(text) if text.startswith('Creatures')
                              else attached_fixed_characteristics_handler(text))
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text())
        for keyword,cap in (('forestwalk','combat.block.landwalk.basic_type'),('horsemanship','combat.block.horsemanship')):
            value=json.loads(json.dumps(raw))
            next(c for c in value['capabilities'] if c['id']==cap).update(status='blocked',blockers=['evasion owner unavailable'])
            ir=compile_oracle_card(query_record('Creatures you control have '+keyword+'.'),capability_registry=CapabilityRegistry(value))
            self.assertNotEqual('exact',ir.status)

    def test_missing_granted_landwalk_consumer_mapping_mutant_is_killed(self):
        from quorune import keyword_abilities
        mapping={k:v for k,v in keyword_abilities.FIXED_CHARACTERISTIC_KEYWORD_CAPABILITIES.items() if k!='Forestwalk'}
        with patch.dict(keyword_abilities.FIXED_CHARACTERISTIC_KEYWORD_CAPABILITIES,mapping,clear=True):
            with self.assertRaises(AssertionError):
                parsed=fixed_query_keyword_grant_handler('Creatures you control have forestwalk.')
                self.assertIn('combat.block.landwalk.basic_type',parsed[2])


class FixedEvasionGrantRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'evasion.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',ROOT/'tests/fixtures/fixed-evasion-grants.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Evasion grants',[DeckEntry('Generic Bound Commander',1,'commander'),
            DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])
    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temporary.cleanup()
    session=witnesses.BoundEffectProgramRuntimeTests.session
    add=witnesses.BoundEffectProgramRuntimeTests.add
    ready=witnesses.BoundEffectProgramRuntimeTests.ready
    checkpoint=witnesses.BoundEffectProgramRuntimeTests.checkpoint
    resolve=witnesses.BoundEffectProgramRuntimeTests.resolve
    replay=witnesses.BoundEffectProgramRuntimeTests.replay

    def blocks(self,session,attacker,blocker,ordinary=None):
        engine=session.engine
        engine.permissions.invalidate_current();engine.state.pending_decision=None
        engine.state.priority_player=None;engine.state.priority_passes=[]
        engine.state.active_player=attacker.controller
        engine.state.phase='combat';engine.state.step='declare_blockers'
        attacker.attacking=blocker.controller
        if ordinary is None:
            ordinary=next((c for c in engine.state.cards.values() if c.ref=='ordinary-evasion-control'),None)
            if ordinary is None:
                ordinary=self.add(engine,'Generic Bound Body',seat=attacker.controller,ref='ordinary-evasion-control')
        ordinary.attacking=blocker.controller
        engine.state.combat=CombatState(attackers_declared=True,had_attacking_creature=True,
            attackers={attacker.object_id:blocker.controller,ordinary.object_id:blocker.controller},
            defending_players=[blocker.controller])
        engine._begin_blocker_decisions()
        decision=session.packet('pilot:'+blocker.controller,full=True)['decision']
        return [] if decision is None else decision['ctx']['legal_blocks'][blocker.ref]

    def test_actual_elvish_champion_cast_affects_other_controllers_and_current_landwalk_blocks(self):
        session=self.session(297101);engine=session.engine
        own=self.add(engine,'Llanowar Elves',ref='own-elf')
        foreign=self.add(engine,'Llanowar Elves',seat='B',ref='foreign-elf')
        blocker=self.add(engine,'Generic Bound Body',seat='C',ref='forest-blocker')
        land_ref=engine.create_token('C',name='Nonbasic Forest',characteristics={'type_line':'Token Land — Forest'})[0]
        lord=self.add(engine,'Elvish Champion',zone='hand')
        action=self.ready(session,lord,{'G':2,'C':1});self.checkpoint(session)
        before=authoritative_state_hash(session.state)
        rejected=session.act('pilot:B',{'action_id':action['id'],'pay':'auto'})
        self.assertFalse(rejected.ok);self.assertEqual(before,authoritative_state_hash(session.state))
        accepted=session.act('pilot:A',{'action_id':action['id'],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary);self.resolve(session)
        for elf in (own,foreign):
            self.assertEqual((2,2),(engine._numeric_stat(elf.object_id,'power'),engine._numeric_stat(elf.object_id,'toughness')))
            self.assertIn('forestwalk',engine._combat_keywords(elf))
        self.assertEqual(2,engine._numeric_stat(lord.object_id,'power'))
        self.assertNotIn('forestwalk',engine._combat_keywords(lord))
        self.replay(session,load=True)
        legal=self.blocks(session,foreign,blocker);self.assertNotIn(foreign.ref,legal)
        for seat in 'ABD':
            self.assertIsNone(session.packet('pilot:'+seat,full=True)['decision'])
        self.checkpoint(session);before=authoritative_state_hash(session.state)
        rejected=session.act('pilot:C',{'a':'block','blk':{blocker.ref:foreign.ref}})
        self.assertFalse(rejected.ok);self.assertEqual(before,authoritative_state_hash(session.state))
        land=engine._resolve_object('C',land_ref,zones={'battlefield'})
        engine.move_card(land.object_id,'graveyard',log=False)
        legal=self.blocks(session,foreign,blocker);self.assertIn(foreign.ref,legal)
        self.checkpoint(session)
        accepted=session.act('pilot:C',{'a':'block','blk':{blocker.ref:foreign.ref}})
        self.assertTrue(accepted.ok,accepted.summary);self.replay(session,load=True)

    def test_actual_sun_quan_cast_grants_self_and_rejects_flying_reach_as_horsemanship(self):
        session=self.session(297102);engine=session.engine
        attacker=self.add(engine,'Generic Bound Body')
        foreign=self.add(engine,'Generic Bound Body',seat='B',ref='foreign-body')
        blocker=self.add(engine,'Giant Spider',seat='C')
        source=self.add(engine,'Sun Quan, Lord of Wu',zone='hand')
        action=self.ready(session,source,{'U':2,'C':4});self.checkpoint(session)
        accepted=session.act('pilot:A',{'action_id':action['id'],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary);self.resolve(session)
        self.assertIn('horsemanship',engine._combat_keywords(source))
        self.assertIn('horsemanship',engine._combat_keywords(attacker))
        self.assertNotIn('horsemanship',engine._combat_keywords(foreign))
        self.replay(session,load=True)
        legal=self.blocks(session,attacker,blocker);self.assertNotIn(attacker.ref,legal)
        before=authoritative_state_hash(session.state)
        rejected=session.act('pilot:C',{'a':'block','blk':{blocker.ref:attacker.ref}})
        self.assertFalse(rejected.ok);self.assertEqual(before,authoritative_state_hash(session.state))
        engine.move_card(source.object_id,'hand',log=False)
        self.assertNotIn('horsemanship',engine._combat_keywords(attacker))
        legal=self.blocks(session,attacker,blocker);self.assertIn(attacker.ref,legal)
        self.checkpoint(session)
        accepted=session.act('pilot:C',{'a':'block','blk':{blocker.ref:attacker.ref}})
        self.assertTrue(accepted.ok,accepted.summary);self.replay(session,load=True)

    def test_actual_islandwalk_aura_cast_tracks_recipient_and_ends_on_source_departure(self):
        session=self.session(297103);engine=session.engine
        recipient=self.add(engine,'Generic Bound Body',seat='B')
        own=self.add(engine,'Generic Bound Body',ref='unenchanted-body')
        aura=self.add(engine,'Fishliver Oil',zone='hand')
        action=self.ready(session,aura,{'U':1,'C':1});self.checkpoint(session)
        self.assertIn(recipient.ref,action['target_schema']['legal_refs'])
        accepted=session.act('pilot:A',{'action_id':action['id'],'targets':[recipient.ref],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary);self.resolve(session)
        self.assertEqual(recipient.object_id,aura.attached_to)
        self.assertIn('islandwalk',engine._combat_keywords(recipient))
        self.assertNotIn('islandwalk',engine._combat_keywords(own))
        self.replay(session,load=True)
        bounce=self.add(engine,'Unsummon',zone='hand')
        action=self.ready(session,bounce,{'U':1});self.checkpoint(session)
        accepted=session.act('pilot:A',{'action_id':action['id'],'targets':[recipient.ref],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary);self.resolve(session)
        self.assertEqual('hand',recipient.zone)
        self.assertEqual('graveyard',aura.zone)
        self.assertNotIn('islandwalk',engine._combat_keywords(recipient))
        self.replay(session,load=True)

    def test_actual_temporary_swampwalk_activation_preserves_fixed_penalty_and_expires(self):
        from quorune.continuous_effect_state import expire_end_of_turn_continuous_effects
        session=self.session(297104);engine=session.engine
        source=self.add(engine,'Viscid Lemures')
        action=self.ready(session,source,{})
        self.checkpoint(session)
        before=engine._numeric_stat(source.object_id,'power')
        accepted=session.act('pilot:A',{'action_id':action['id']})
        self.assertTrue(accepted.ok,accepted.summary);self.resolve(session)
        self.assertIn('swampwalk',engine._combat_keywords(source))
        self.assertEqual(before-1,engine._numeric_stat(source.object_id,'power'))
        self.replay(session,load=True)
        self.assertGreater(expire_end_of_turn_continuous_effects(engine.state),0)
        self.assertNotIn('swampwalk',engine._combat_keywords(source))
        self.assertEqual(before,engine._numeric_stat(source.object_id,'power'))

    def test_actual_turn_to_frog_removes_source_grant_until_expiration_and_replays(self):
        from quorune.continuous_effect_state import expire_end_of_turn_continuous_effects
        session=self.session(297105);engine=session.engine
        source=self.add(engine,'Sun Quan, Lord of Wu')
        body=self.add(engine,'Generic Bound Body')
        spell=self.add(engine,'Turn to Frog',zone='hand')
        action=self.ready(session,spell,{'U':1,'C':1})
        self.assertIn('horsemanship',engine._combat_keywords(body))
        self.checkpoint(session)
        accepted=session.act('pilot:A',{'action_id':action['id'],'targets':[source.ref],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary);self.resolve(session)
        self.assertNotIn('horsemanship',engine._combat_keywords(source))
        self.assertNotIn('horsemanship',engine._combat_keywords(body))
        self.replay(session,load=True)
        expire_end_of_turn_continuous_effects(engine.state)
        self.assertIn('horsemanship',engine._combat_keywords(body))


if __name__=='__main__':unittest.main()
