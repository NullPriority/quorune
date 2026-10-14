from __future__ import annotations

"""Pinned CR608.2c/h and 401.4: a sole remainder keeps its printed destination."""

from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from common import ROOT
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
import test_bound_effect_programs as bound_witnesses
from test_bound_effect_programs import record


class LibraryRemainderCompilerTests(unittest.TestCase):
    def test_single_other_remainder_uses_fixed_selection_only_when_cardinality_agrees(self):
        cap=load_default_capability_registry()
        for count,selected in (('two','one'),('three','two')):
            for destination in ('on the bottom of your library','into your graveyard'):
                text=f'Look at the top {count} cards of your library. Put {selected} of them into your hand and the other {destination}.'
                ir=compile_oracle_card(record(text,'Sorcery'),capability_registry=cap)
                self.assertEqual('exact',ir.status,ir.material_residuals)
                self.assertEqual('fixed_library_selection',ir.faces[0].nodes[0].effects[0]['op'])
        for text in (
            'Look at the top three cards of your library. Put one of them into your hand and the other into your graveyard.',
            'Look at the top two cards of your library. You may put one of them into your hand and the other into your graveyard.',
            'Look at the top two cards of your library. Put one of them into your hand and the other onto the battlefield.',
        ):
            self.assertNotEqual('exact',compile_oracle_card(record(text,'Sorcery'),capability_registry=cap).status,text)


class LibraryRemainderActionTests(unittest.TestCase):
    add=bound_witnesses.BoundEffectProgramRuntimeTests.add
    ready=bound_witnesses.BoundEffectProgramRuntimeTests.ready
    checkpoint=bound_witnesses.BoundEffectProgramRuntimeTests.checkpoint
    resolve=bound_witnesses.BoundEffectProgramRuntimeTests.resolve
    replay=bound_witnesses.BoundEffectProgramRuntimeTests.replay
    session=bound_witnesses.BoundEffectProgramRuntimeTests.session

    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'other-library-remainder.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',
            ROOT/'tests/fixtures/library-other-remainder-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Library remainder witness',[DeckEntry('Generic Bound Commander',1,'commander'),
            DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temporary.cleanup()

    def test_original_sleight_and_tower_geist_private_partition_reload_and_replay(self):
        for index,(name,mana,remainder) in enumerate((('Sleight of Hand',{'U':1},'bottom'),
                                                   ('Tower Geist',{'U':1,'C':3},'graveyard'))):
            session=self.session(608240+index);engine=session.engine
            spell=self.add(engine,name,zone='hand',ref='original-other-remainder')
            offered=self.ready(session,spell,mana);self.checkpoint(session)
            self.assertTrue(session.act('pilot:A',{'action_id':offered['id'],'pay':'auto'}).ok)
            self.assertEqual('semantic.choice',self.resolve(session).kind)
            refs=[row['id'] for row in session.packet('pilot:A',full=True)['decision']['ctx']['objects']]
            self.assertEqual(2,len(refs))
            for seat in 'BCD':
                packet=json.dumps(session.packet('pilot:'+seat,full=True))
                for ref in refs:self.assertNotIn(ref,packet)
            before=authoritative_state_hash(session.state)
            self.assertFalse(session.act('pilot:D',{'action_id':'choose','cards':{'hand':[refs[0]],remainder:[refs[1]]}}).ok)
            self.assertEqual(before,authoritative_state_hash(session.state))
            resumed=self.replay(session,load=True)
            accepted=resumed.act('pilot:A',{'action_id':'choose','cards':{'hand':[refs[0]],remainder:[refs[1]]}})
            self.assertTrue(accepted.ok,accepted.summary);self.resolve(resumed)
            selected=next(c for c in resumed.state.cards.values() if c.ref==refs[0])
            other=next(c for c in resumed.state.cards.values() if c.ref==refs[1])
            self.assertEqual('hand',selected.zone)
            if remainder=='graveyard':self.assertEqual('graveyard',other.zone)
            else:self.assertEqual(other.object_id,resumed.state.players['A'].zones['library'][0])
            self.replay(resumed,load=True)

    def test_original_sleight_on_short_library_commits_only_available_cards(self):
        session=self.session(608244);engine=session.engine
        spell=self.add(engine,'Sleight of Hand',zone='hand',ref='short-other-remainder')
        for key in tuple(engine.state.players['A'].zones['library'])[:-1]:engine.move_card(key,'outside',log=False)
        offered=self.ready(session,spell,{'U':1});self.checkpoint(session)
        self.assertTrue(session.act('pilot:A',{'action_id':offered['id'],'pay':'auto'}).ok)
        decision=self.resolve(session)
        if decision is not None:
            refs=[row['id'] for row in session.packet('pilot:A',full=True)['decision']['ctx']['objects']]
            self.assertEqual(1,len(refs))
            accepted=session.act('pilot:A',{'action_id':'choose','cards':{'hand':refs,'bottom':[]}})
            self.assertTrue(accepted.ok,accepted.summary);self.resolve(session)
        self.assertEqual([],session.state.players['A'].zones['library'])
        self.replay(session,load=True)
