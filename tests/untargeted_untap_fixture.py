"""Shared native land-untap actions; deliberately not a TestCase."""
import json
from pathlib import Path
import tempfile

from common import ROOT
import inherited_composition_fixture as actions
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database


class UntargetedUntapFixture(actions.InheritedNativeFixture):
    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'untargeted-untap.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',ROOT/'tests/fixtures/untargeted-untap-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry();cls.deck=DeckDefinition('Untargeted untap',[DeckEntry('Isamaru, Hound of Konda',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Isamaru, Hound of Konda'])

    def land(self,session,ref,*,seat='A',name='Island',tapped=True):
        card=self.add(session.engine,name,seat=seat,ref=ref);card.tapped=tapped;return card

    def choose_lands(self,session,refs):
        self.assertEqual('semantic.choice',session.state.pending_decision.kind);self.assertEqual(['A'],session.state.pending_decision.actors)
        for seat in 'BCD':
            packet=session.packet('pilot:'+seat,full=True);self.assertIsNone(packet['decision']);self.assertNotIn('private-untap-hand',json.dumps(packet))
        before=authoritative_state_hash(session.state)
        for principal,submitted in [('pilot:B',refs),('pilot:A',['forged-land']),*([('pilot:A',[refs[0],refs[0]])] if refs else [])]:
            self.assertFalse(session.act(principal,{'action_id':'choose','objects':submitted}).ok);self.assertEqual(before,authoritative_state_hash(session.state))
        session=self.replay(session,load=True);result=session.act('pilot:A',{'action_id':'choose','objects':list(refs)});self.assertTrue(result.ok,result.summary);return session

    def discard(self,session,count):
        self.assertEqual('choice.apnap',session.state.pending_decision.kind)
        refs=[session.state.cards[oid].ref for oid in session.state.players['A'].zones['hand'][:count]]
        self.assertEqual(count,len(refs));result=session.act('pilot:A',{'action_id':'choose','cards':refs});self.assertTrue(result.ok,result.summary)
