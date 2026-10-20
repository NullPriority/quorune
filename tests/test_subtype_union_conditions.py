from __future__ import annotations

"""CR109.2/601.2e: current controlled tapped subtype unions count objects once."""

import json
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT
from quorune.carddb import CardDatabase
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.compiler.public_state_queries import fixed_public_state_condition
from quorune.continuous_conditions import FixedPublicStateConditionSpec
from quorune.deck import DeckDefinition,DeckEntry
from quorune.object_predicate import ObjectQuerySpec
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry
from scripts.build_test_database import build_fixture_database
import test_turn_count_self_costs as controls


class SubtypeUnionConditionTests(unittest.TestCase):
    register_fixture_cards_on_add=True
    session=controls.TurnCountSelfCostTests.session
    add=controls.TurnCountSelfCostTests.add
    prepare=controls.TurnCountSelfCostTests.prepare
    checkpoint=controls.TurnCountSelfCostTests.checkpoint
    cast=controls.TurnCountSelfCostTests.cast
    resolve=controls.TurnCountSelfCostTests.resolve
    replay=controls.TurnCountSelfCostTests.replay
    binding=controls.TurnCountSelfCostTests.binding
    actions=controls.TurnCountSelfCostTests.actions
    priority=controls.TurnCountSelfCostTests.priority
    private_replay=controls.TurnCountSelfCostTests.private_replay
    advance=controls.TurnCountSelfCostTests.advance

    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();path=Path(cls.temp.name)/'subtype-union-conditions.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',ROOT/'tests/fixtures/subtype-union-condition-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Controlled subtype union conditions',[DeckEntry('Generic Bound Commander',1,'commander'),
            DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):
        cls.db.close();cls.temp.cleanup()

    def offered(self,session,source):
        return any(row['id']=='cast:'+source.ref for row in self.actions(session))

    def activate_target(self,session,source,target):
        self.priority(session,'A')
        action=next(row for row in self.actions(session) if row['id'].startswith('activate:'+source.ref+':'))
        result=session.act('pilot:A',{'action_id':action['id'],'targets':[target.ref],'pay':'auto'})
        self.assertTrue(result.ok,result.summary)

    def test_actual_borrowed_vehicle_and_icy_tap_enable_full_graveyard_cast(self):
        session=self.session(744100)
        source=self.add(session.engine,'The Indomitable',zone='graveyard')
        pirate=self.add(session.engine,'Sailor of Means')
        own=self.add(session.engine,'Sky Skiff',ref='own-union-skiff')
        foreign=self.add(session.engine,'Sky Skiff',seat='B',ref='borrowed-union-skiff')
        pirate.tapped=True;own.tapped=True
        icy=self.add(session.engine,'Icy Manipulator')
        key=self.add(session.engine,'Voltaic Key')
        steal=self.add(session.engine,'Steal Artifact',zone='hand')
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(session,mana={'U':6,'C':10});self.checkpoint(session)
        self.assertFalse(self.offered(session,source))
        self.cast(session,'A',steal,[foreign.ref]);self.assertIsNone(self.resolve(session))
        self.assertEqual(('B','A'),(session.state.cards[foreign.object_id].owner,session.state.cards[foreign.object_id].controller))
        self.assertFalse(self.offered(session,source))
        self.activate_target(session,icy,foreign);self.assertIsNone(self.resolve(session))
        self.assertTrue(session.state.cards[foreign.object_id].tapped)
        self.assertTrue(self.offered(session,source))
        before=authoritative_state_hash(session.state)
        command={'action_id':'cast:'+source.ref,'pay':'auto'}
        self.assertFalse(session.act('pilot:D',command).ok)
        self.assertEqual(before,authoritative_state_hash(session.state))
        self.assertFalse(session.act('pilot:A',{'action_id':'cast:'+source.ref,'pay':{}}).ok)
        self.assertEqual(before,authoritative_state_hash(session.state))
        result=session.act('pilot:A',command);self.assertTrue(result.ok,result.summary)
        self.assertEqual(5,session.state.players['A'].mana_pool['C'])
        self.assertEqual(2,session.state.players['A'].mana_pool['U'])
        self.assertTrue(self.binding(session,source)['strict_capability_ready'])
        session=self.replay(session,load=True)
        self.activate_target(session,key,foreign)
        self.assertIsNone(self.resolve(session))
        self.assertFalse(session.state.cards[foreign.object_id].tapped)
        self.assertEqual('battlefield',session.state.cards[source.object_id].zone)
        self.assertFalse(session.state.cards[source.object_id].tapped)
        self.assertEqual(6,session.engine._numeric_stat(source.object_id,'power'))
        self.private_replay(session,private)

    def test_original_has_complete_nonempty_union_permission_and_retains_crew(self):
        session=self.session(744101);source=self.add(session.engine,'The Indomitable',zone='graveyard')
        ir=compile_oracle_card(self.db.lookup('The Indomitable'),capability_registry=self.registry,capability_profile='commander_review')
        self.assertEqual('exact',ir.status,ir.material_residuals)
        self.assertTrue(self.binding(session,source)['strict_capability_ready'])
        program,=[p for p in session.engine.semantics.programs_for_oracle(source.oracle_id) if p.event=='cast.zone.permission']
        self.assertTrue(program.handlers)
        condition=FixedPublicStateConditionSpec.from_dict(program.handlers[0]['source_condition'])
        self.assertEqual(3,condition.amount)
        self.assertEqual(('pirate','vehicle'),condition.quantity.query.subtypes_any)
        self.assertEqual((),condition.quantity.query.types_all)
        self.assertTrue(condition.quantity.query.state_predicate.tapped)
        self.assertTrue(any(p.handlers and any('crew' in str(h.get('handler_id')) for h in p.handlers)
            for p in session.engine.semantics.programs_for_oracle(source.oracle_id)))

    def test_union_surface_reuses_closed_typed_count_and_preserves_unknown_rejection(self):
        for phrase,tapped in (('tapped Pirates and/or Vehicles',True),('untapped Elves or Equipment',False),
                ('Pirates or Vehicles',None)):
            condition=fixed_public_state_condition('you control three or more '+phrase,source_name='Generic Union Observer')
            self.assertIsNotNone(condition)
            self.assertEqual(condition,FixedPublicStateConditionSpec.from_dict(condition.to_dict()))
            query=condition.quantity.query
            self.assertEqual((),query.types_all)
            self.assertEqual(tapped,None if query.state_predicate is None else query.state_predicate.tapped)
        for phrase in ('tapped Pirates and/or NotARealSubtype','tapped Pirates and/or Vehicles that attacked last turn',
                'tapped Pirates with Vehicles','tapped Pirates and/or Vehicles and/or Elves',
                'tapped Pirates and/or creature cards in your hand'):
            self.assertIsNone(fixed_public_state_condition('you control three or more '+phrase,source_name='Generic Union Observer'),phrase)
        valid=fixed_public_state_condition('you control three or more tapped Pirates and/or Vehicles',source_name='Generic Union Observer').to_dict()
        malformed=json.loads(json.dumps(valid));malformed['quantity']['query']['state_predicate']['tapped']='true'
        with self.assertRaises(ValueError):FixedPublicStateConditionSpec.from_dict(malformed)

    def test_dual_subtype_counts_once_and_kindred_pirate_needs_no_creature_type(self):
        session=self.session(744102);source=self.add(session.engine,'The Indomitable',zone='graveyard')
        dual=self.add(session.engine,'Generic Union Pirate Vehicle');dual.tapped=True
        skiff=self.add(session.engine,'Sky Skiff');skiff.tapped=True
        pirate=self.add(session.engine,'Sailor of Means')
        icy=self.add(session.engine,'Icy Manipulator')
        self.prepare(session,mana={'U':2,'C':3});self.checkpoint(session)
        self.assertFalse(self.offered(session,source))
        before=authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:A',{'action_id':'cast:'+source.ref,'pay':'auto'}).ok)
        self.assertEqual(before,authoritative_state_hash(session.state))
        self.activate_target(session,icy,pirate);self.assertIsNone(self.resolve(session))
        self.assertTrue(self.offered(session,source))
        self.cast(session,'A',source);session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertEqual('battlefield',session.state.cards[source.object_id].zone)
        session=self.session(744103);source=self.add(session.engine,'The Indomitable',zone='graveyard')
        for name,ref in (('Generic Union Kindred Pirate','noncreature-pirate'),('Sky Skiff','kindred-skiff1'),('Sky Skiff','kindred-skiff2')):
            card=self.add(session.engine,name,ref=ref);card.tapped=True
        self.prepare(session,mana={'U':2,'C':2});self.checkpoint(session)
        self.assertTrue(self.offered(session,source))
        self.cast(session,'A',source);session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertEqual('battlefield',session.state.cards[source.object_id].zone)

    def test_untapped_foreign_phased_and_other_zone_objects_do_not_meet_threshold(self):
        session=self.session(744104);source=self.add(session.engine,'The Indomitable',zone='graveyard')
        pirate=self.add(session.engine,'Sailor of Means');pirate.tapped=True
        skiff=self.add(session.engine,'Sky Skiff',ref='scope-skiff');skiff.tapped=True
        self.add(session.engine,'Sky Skiff',ref='untapped-skiff')
        phased=self.add(session.engine,'Sky Skiff',ref='phased-skiff');phased.tapped=True;phased.phased_out=True
        for zone in ('hand','graveyard','exile'):
            row=self.add(session.engine,'Sky Skiff',zone=zone,ref='excluded-skiff-'+zone);row.tapped=True
        for seat in 'BCD':
            row=self.add(session.engine,'Sky Skiff',seat=seat,ref='foreign-skiff-'+seat);row.tapped=True
        self.prepare(session,mana={'U':2,'C':2});self.checkpoint(session)
        self.assertFalse(self.offered(session,source))
        before=authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:A',{'action_id':'cast:'+source.ref,'pay':'auto'}).ok)
        self.assertEqual(before,authoritative_state_hash(session.state))
        self.replay(session,load=True)

    def test_actual_key_untap_revokes_stale_offer_before_acceptance(self):
        session=self.session(744105);source=self.add(session.engine,'The Indomitable',zone='graveyard')
        pirate=self.add(session.engine,'Sailor of Means');pirate.tapped=True
        skiffs=[self.add(session.engine,'Sky Skiff',ref='stale-skiff-'+str(i)) for i in range(2)]
        for card in skiffs:card.tapped=True
        key=self.add(session.engine,'Voltaic Key')
        self.prepare(session,mana={'U':2,'C':3});self.checkpoint(session)
        self.assertTrue(self.offered(session,source))
        self.activate_target(session,key,skiffs[0]);session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertFalse(self.offered(session,source))
        before=authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:A',{'action_id':'cast:'+source.ref,'pay':'auto'}).ok)
        self.assertEqual(before,authoritative_state_hash(session.state))
        self.assertEqual('graveyard',session.state.cards[source.object_id].zone)
        self.replay(session,load=True)

    def test_original_crew_and_real_combat_keep_trample_damage_and_private_draw(self):
        session=self.session(744106);source=self.add(session.engine,'The Indomitable')
        sailor=self.add(session.engine,'Sailor of Means')
        bear=self.add(session.engine,'Grizzly Bears')
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(session);self.checkpoint(session)
        self.assertTrue(self.binding(session,source)['strict_capability_ready'])
        action=next(row for row in self.actions(session) if row['id'].startswith('activate:'+source.ref+':'))
        before=authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:D',{'action_id':action['id'],'cost_cards':[sailor.ref,bear.ref]}).ok)
        self.assertEqual(before,authoritative_state_hash(session.state))
        self.assertFalse(session.act('pilot:A',{'action_id':action['id'],'cost_cards':[sailor.ref]}).ok)
        self.assertEqual(before,authoritative_state_hash(session.state))
        result=session.act('pilot:A',{'action_id':action['id'],'cost_cards':[sailor.ref,bear.ref]})
        self.assertTrue(result.ok,result.summary)
        self.assertTrue(session.state.cards[sailor.object_id].tapped and session.state.cards[bear.object_id].tapped)
        self.assertEqual(3,session.state.stack[-1].context['crew']['threshold'])
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertIn('creature',session.engine._type_parts(session.engine._effective_card_data(session.state.cards[source.object_id])['type_line'])[0])
        self.assertIn('trample',session.engine._combat_keywords(session.state.cards[source.object_id]))
        hand_before=set(session.state.players['A'].zones['hand'])
        self.advance(session,lambda current:current.state.pending_decision is not None and current.state.pending_decision.kind=='combat.attackers')
        result=session.act('pilot:A',{'action_id':'attack','attacks':{source.ref:'B'}});self.assertTrue(result.ok,result.summary)
        self.advance(session,lambda current:current.state.players['B'].life==34 and len(current.state.players['A'].zones['hand'])==len(hand_before)+1)
        drawn=set(session.state.players['A'].zones['hand'])-hand_before
        self.assertEqual(1,len(drawn))
        for viewer in 'BCD':self.assertNotIn(session.state.cards[next(iter(drawn))].ref,json.dumps(session.packet('pilot:'+viewer,full=True)))
        self.private_replay(session,private)

    def test_normal_hand_cast_is_unrestricted_and_mana_sacrifice_cannot_revoke_legal_graveyard_cast(self):
        session=self.session(744108);source=self.add(session.engine,'The Indomitable',zone='hand')
        self.prepare(session,mana={'U':2,'C':2});self.checkpoint(session)
        self.assertTrue(self.offered(session,source))
        self.cast(session,'A',source);session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertEqual('battlefield',session.state.cards[source.object_id].zone)
        session=self.session(744109);source=self.add(session.engine,'The Indomitable',zone='graveyard')
        for name in ('Sailor of Means','Sky Skiff'):
            row=self.add(session.engine,name);row.tapped=True
        donor=self.add(session.engine,'Generic Union Mana Pirate');donor.tapped=True
        self.prepare(session,mana={'U':1,'C':2});self.checkpoint(session)
        self.assertTrue(self.binding(session,donor)['strict_capability_ready'])
        self.assertTrue(self.offered(session,source))
        self.cast(session,'A',source)
        self.assertEqual('graveyard',session.state.cards[donor.object_id].zone)
        self.assertEqual('stack',session.state.cards[source.object_id].zone)
        self.assertEqual(0,session.state.players['A'].mana_pool['U'])
        self.assertEqual(0,session.state.players['A'].mana_pool['C'])
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertEqual('battlefield',session.state.cards[source.object_id].zone)
        self.replay(session,load=True)

    def test_union_and_zone_permission_authorities_cannot_be_withdrawn(self):
        session=self.session(744107);source=self.add(session.engine,'The Indomitable',zone='graveyard')
        program,=[p for p in session.engine.semantics.programs_for_oracle(source.oracle_id) if p.event=='cast.zone.permission']
        for cap in ('continuous.characteristics.fixed_public_state','casting.zone.self_graveyard.controlled_subtype'):
            deps=tuple(c for c in program.capability_dependencies if c!=cap)
            missing=replace(program,capability_dependencies=deps,capability_closure=self.registry.closure(deps,profile='commander_review').to_dict())
            self.assertFalse(bind_semantic_program_runtime(missing,capability_registry=self.registry,profile='commander_review')['strict'])
            raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
            next(row for row in raw['capabilities'] if row['id']==cap).update(status='blocked',blockers=['withdrawn subtype union condition authority'])
            self.assertNotEqual('exact',compile_oracle_card(self.db.lookup('The Indomitable'),capability_registry=CapabilityRegistry(raw),capability_profile='commander_review').status)

    def test_fresh_original_kills_lost_pirate_and_vehicle_union_branches(self):
        original=ObjectQuerySpec.to_dict
        for branch in ('pirate','vehicle'):
            def omit(query):
                result=original(query)
                if set(result.get('subtypes_any',()))=={'pirate','vehicle'}:
                    result['subtypes_any']=[value for value in result['subtypes_any'] if value!=branch]
                return result
            method='test_actual_borrowed_vehicle_and_icy_tap_enable_full_graveyard_cast'
            with patch.object(ObjectQuerySpec,'to_dict',omit):
                with self.assertRaises(AssertionError):getattr(type(self)(method),method)()

    def test_fresh_original_kills_omitted_union_production(self):
        method='test_original_has_complete_nonempty_union_permission_and_retains_crew'
        with patch('quorune.compiler.public_condition_surfaces.fixed_condition_subtype_union',return_value=None):
            with self.assertRaises(AssertionError):getattr(type(self)(method),method)()
