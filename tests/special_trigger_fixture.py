"""Shared actions and private fixture; no unittest TestCase inheritance."""
import json,tempfile
from pathlib import Path
from common import ROOT
import inherited_composition_fixture as actions
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database

class SpecialTriggerFixture(actions.InheritedNativeFixture):
    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'special-triggers.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',ROOT/'tests/fixtures/special-trigger-envelope-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry();cls.deck=DeckDefinition('Special triggers',[DeckEntry('Isamaru, Hound of Konda',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Isamaru, Hound of Konda'])

    def top(self,session,cards):
        library=session.state.players['A'].zones['library']
        for card in cards:library.remove(card.object_id)
        library.extend(c.object_id for c in reversed(cards))

    def choose_dread(self,session,selected,other):
        self.assertEqual('semantic.choice',session.state.pending_decision.kind)
        for seat in 'BCD':self.assertIsNone(session.packet('pilot:'+seat,full=True)['decision'])
        before=authoritative_state_hash(session.state)
        for principal,card in (('pilot:B',selected.ref),('pilot:A','outside-private-look')):
            self.assertFalse(session.act(principal,{'action_id':'choose','card':card}).ok);self.assertEqual(before,authoritative_state_hash(session.state))
        session=self.replay(session,load=True);result=session.act('pilot:A',{'action_id':'choose','card':selected.ref});self.assertTrue(result.ok,result.summary);self.settle(session)
        self.assertEqual('battlefield',session.state.cards[selected.object_id].zone);self.assertTrue(session.state.cards[selected.object_id].face_down);self.assertEqual('graveyard',session.state.cards[other.object_id].zone);self.assertEqual(2,session.engine._numeric_stat(selected.object_id,'power'))
        return session

    def begin_prepared_turn(self,session,seat):
        from quorune.model import TurnEntry
        engine=session.engine;engine.permissions.invalidate_current();session.state.pending_decision=None;session.state.priority_player=None;session.state.priority_passes=[]
        engine._begin_turn(TurnEntry(turn_id='prepared-special-trigger-'+seat+'-'+str(session.state.turn_sequence),player=seat,created_sequence=session.state.turn_sequence));engine.pump();self.checkpoint(session)
