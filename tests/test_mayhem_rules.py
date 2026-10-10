from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT
from quorune.carddb import CardDatabase
from quorune.cast_lifecycles import compile_fixed_cast_lifecycle,FixedCastLifecycleSpec,FixedCastLifecycleError
from quorune.compiled_cast_lifecycles import compiled_mayhem_permission
from quorune.deck import DeckDefinition,DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.errors import GameRuleError
from quorune.rules.capabilities import load_default_capability_registry
from quorune.zone_trigger_events import ZoneTransitionKind
from scripts.build_test_database import build_fixture_database
from test_qualified_zone_event_queries import query_record
import test_bound_effect_programs as witnesses


class MayhemCompilerTests(unittest.TestCase):
    def test_mayhem_fixed_and_bare_descriptors_round_trip_and_reject_open_costs(self):
        for text in ('Mayhem {1}{B}','Mayhem {0}','Mayhem'):
            spec=compile_fixed_cast_lifecycle(material_line=text,oracle_line=text,line_index=0)
            self.assertIsNotNone(spec)
            self.assertEqual(4,spec.schema_version)
            self.assertEqual(spec,FixedCastLifecycleSpec.from_dict(spec.to_dict()))
            if text=='Mayhem':
                option=spec.printed_zone_cost_option({'id':'normal','requirements':{'GENERIC':2}})
                self.assertEqual([],option['_additional_option_costs'])
                self.assertEqual({'GENERIC':2},option['requirements'])
        for text in ('Mayhem {X}','Mayhem {B/R}','Mayhem {S}','Mayhem Pay 2 life','Mayhem {B}. Draw a card.'):
            self.assertIsNone(compile_fixed_cast_lifecycle(material_line=text,oracle_line=text,line_index=0))
        spec=compile_fixed_cast_lifecycle(material_line='Mayhem {1}{B}',oracle_line='Mayhem {1}{B}',line_index=0)
        for update in ({'schema_version':3},{'counter_count':1},{'mana_cost':None}):
            with self.assertRaises(FixedCastLifecycleError):replace(spec,**update)


class MayhemRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'mayhem.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',ROOT/'tests/fixtures/mayhem-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Mayhem witness',[DeckEntry('Generic Bound Commander',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])
    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temporary.cleanup()
    session=witnesses.BoundEffectProgramRuntimeTests.session
    add=witnesses.BoundEffectProgramRuntimeTests.add
    ready=witnesses.BoundEffectProgramRuntimeTests.ready
    checkpoint=witnesses.BoundEffectProgramRuntimeTests.checkpoint
    resolve=witnesses.BoundEffectProgramRuntimeTests.resolve
    replay=witnesses.BoundEffectProgramRuntimeTests.replay

    def discard_with_real_looting(self,session,card,mana):
        engine=session.engine
        other=self.add(engine,'Generic Bound Growth',zone='hand',ref='looting-other')
        looting=self.add(engine,'Faithless Looting',zone='hand')
        action=self.ready(session,looting,mana);self.checkpoint(session)
        accepted=session.act('pilot:A',{'action_id':action['id'],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary)
        decision=self.resolve(session);self.assertIsNotNone(decision)
        accepted=session.act('pilot:A',{'action_id':'choose','cards':[card.ref,other.ref]})
        self.assertTrue(accepted.ok,accepted.summary);self.resolve(session)
        return card

    def test_actual_discarded_spider_cast_uses_alternative_cost_and_ordinary_destination(self):
        session=self.session(300001);engine=session.engine
        source=self.add(engine,'Spider-Islanders',zone='hand')
        self.discard_with_real_looting(session,source,{'R':2,'C':1})
        self.assertEqual('graveyard',source.zone)
        self.assertTrue(compiled_mayhem_permission(engine,'A',source))
        actions=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        self.assertTrue(any(a.get('card')==source.ref for a in actions))
        cast=next(a for a in actions if a.get('card')==source.ref)
        self.assertEqual({'mayhem'},{o['id'] for o in cast['cost_options']})
        before=authoritative_state_hash(session.state)
        rejected=session.act('pilot:B',{'action_id':cast['id'],'cost_option':'mayhem','pay':'auto'})
        self.assertFalse(rejected.ok);self.assertEqual(before,authoritative_state_hash(session.state))
        accepted=session.act('pilot:A',{'action_id':cast['id'],'cost_option':'mayhem','pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary);self.resolve(session)
        self.assertEqual('battlefield',source.zone)
        self.replay(session,load=True)

    def test_mayhem_permission_requires_same_discard_turn_actor_and_adjacent_incarnation(self):
        session=self.session(300002);engine=session.engine
        source=self.add(engine,'Spider-Islanders',zone='hand')
        self.ready(session,source,{'R':4,'C':4})
        engine.move_card(source.object_id,'graveyard',log=False,semantic_events=True)
        self.assertFalse(compiled_mayhem_permission(engine,'A',source))
        engine.move_card(source.object_id,'hand',log=False)
        engine.move_card(source.object_id,'graveyard',log=False,semantic_events=True,transition_kind=ZoneTransitionKind.DISCARD)
        self.assertTrue(compiled_mayhem_permission(engine,'A',source))
        self.assertFalse(compiled_mayhem_permission(engine,'B',source))
        history=engine.state.turn_history
        engine.state.turn_history=replace(history,turn_sequence=history.turn_sequence+1)
        self.assertFalse(compiled_mayhem_permission(engine,'A',source))
        engine.state.turn_history=history
        engine.move_card(source.object_id,'exile',log=False,semantic_events=True)
        engine.move_card(source.object_id,'graveyard',log=False,semantic_events=True)
        self.assertFalse(compiled_mayhem_permission(engine,'A',source))

    def test_mayhem_stale_discard_turn_rejects_prepared_cast_atomically(self):
        from quorune.rules.casting.proposal import build_cast_proposal
        from quorune.rules.casting.model import CastProposalRequest,CastProposalError
        from quorune.rules.casting.commit import commit_cast
        session=self.session(300003);engine=session.engine
        source=self.add(engine,'Spider-Islanders',zone='hand')
        self.discard_with_real_looting(session,source,{'R':2,'C':1})
        request=CastProposalRequest.from_submission('A',{'card':source.ref,'from':'graveyard','cost_option':'mayhem','pay':'auto'})
        proposal=build_cast_proposal(engine,request)
        engine.state.turn_history=replace(engine.state.turn_history,turn_sequence=engine.state.turn_sequence+1)
        before=authoritative_state_hash(session.state)
        with self.assertRaises(CastProposalError):commit_cast(engine,proposal,request.response())
        self.assertEqual(before,authoritative_state_hash(session.state))

    def test_mayhem_additional_cost_is_required_and_committed_before_entry(self):
        session=self.session(300004);engine=session.engine
        source=self.add(engine,'Generic Mayhem Additional Cost',zone='hand')
        victim=self.add(engine,'Generic Bound Body')
        self.discard_with_real_looting(session,source,{'R':2})
        cast=next(a for a in session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions'] if a.get('card')==source.ref)
        before=authoritative_state_hash(session.state)
        rejected=session.act('pilot:A',{'action_id':cast['id'],'cost_option':'mayhem','pay':'auto'})
        self.assertFalse(rejected.ok);self.assertEqual(before,authoritative_state_hash(session.state))
        accepted=session.act('pilot:A',{'action_id':cast['id'],'cost_option':'mayhem','sacrifice_cards':[victim.ref],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary);self.resolve(session)
        self.assertEqual('graveyard',engine.state.cards[victim.object_id].zone)
        self.assertEqual('battlefield',engine.state.cards[source.object_id].zone)
        self.replay(session,load=True)

    def test_bare_mayhem_land_uses_existing_land_play_timing_and_limit(self):
        session=self.session(300005);engine=session.engine
        land=self.add(engine,'Generic Bare Mayhem Land',zone='hand')
        self.discard_with_real_looting(session,land,{'R':1})
        action=next(a for a in session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions'] if a['id']=='play-land:'+land.ref)
        engine.state.players['A'].land_plays_remaining=0
        before=authoritative_state_hash(session.state)
        rejected=session.act('pilot:A',{'action_id':action['id']})
        self.assertFalse(rejected.ok);self.assertEqual(before,authoritative_state_hash(session.state))
        engine.state.players['A'].land_plays_remaining=1
        engine.permissions.invalidate_current();engine.state.pending_decision=None;engine._grant_priority('A');engine.pump()
        action=next(a for a in session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions'] if a['id']=='play-land:'+land.ref)
        self.checkpoint(session)
        accepted=session.act('pilot:A',{'action_id':action['id']})
        self.assertTrue(accepted.ok,accepted.summary)
        current=engine.state.cards[land.object_id]
        self.assertEqual('battlefield',current.zone);self.assertEqual(0,engine.state.players['A'].land_plays_remaining)
        self.replay(session,load=True)

    def test_mayhem_creature_normal_timing_and_flash_share_existing_offer_legality(self):
        from quorune.rules.casting.proposal import build_cast_proposal
        from quorune.rules.casting.model import CastProposalRequest,CastProposalError
        session=self.session(300006);engine=session.engine
        normal=self.add(engine,'Spider-Islanders',zone='hand')
        flash=self.add(engine,'Swarm, Being of Bees',zone='hand')
        self.ready(session,normal,{'R':3,'B':1,'C':2})
        for card in (normal,flash):
            engine.move_card(card.object_id,'graveyard',log=False,semantic_events=True,transition_kind=ZoneTransitionKind.DISCARD)
        engine.state.active_player='B';engine.permissions.invalidate_current();engine.state.pending_decision=None
        engine._grant_priority('A');engine.pump()
        actions=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        self.assertFalse(any(a.get('card')==normal.ref for a in actions))
        self.assertTrue(any(a.get('card')==flash.ref for a in actions))
        before=authoritative_state_hash(session.state)
        with self.assertRaisesRegex(GameRuleError,'active player'):
            build_cast_proposal(engine,CastProposalRequest.from_submission('A',{'card':normal.ref,'from':'graveyard','cost_option':'mayhem','pay':'auto'}))
        self.assertEqual(before,authoritative_state_hash(session.state))

    def test_replaced_discard_to_exile_and_later_graveyard_move_does_not_grant_mayhem(self):
        session=self.session(300007);engine=session.engine
        self.add(engine,'Dauthi Voidwalker',seat='B')
        source=self.add(engine,'Spider-Islanders',zone='hand')
        self.discard_with_real_looting(session,source,{'R':2,'C':1})
        self.assertEqual('exile',source.zone)
        self.assertFalse(compiled_mayhem_permission(engine,'A',source))
        engine.move_card(source.object_id,'graveyard',log=False,semantic_events=True)
        self.assertFalse(compiled_mayhem_permission(engine,'A',source))

    def test_mayhem_permission_omission_mutant_is_killed_by_real_discard_cast(self):
        with patch('quorune.compiled_cast_lifecycles.compiled_mayhem_permission',return_value=False):
            with self.assertRaises(AssertionError):
                self.test_actual_discarded_spider_cast_uses_alternative_cost_and_ordinary_destination()


if __name__=='__main__':unittest.main()
