"""Shared real-card library-order actions; deliberately not a TestCase."""
import json,tempfile
from pathlib import Path
from common import ROOT
import inherited_composition_fixture as actions
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database

class TopOrderFixture(actions.InheritedNativeFixture):
    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'top-order.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',ROOT/'tests/fixtures/fixed-top-order-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry();cls.deck=DeckDefinition('Top order',[DeckEntry('Isamaru, Hound of Konda',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Isamaru, Hound of Konda'])

    def top_cards(self,session):
        cards=[self.add(session.engine,name,zone='library',ref='private-order-top-'+str(i)) for i,name in enumerate(('Memnite','Shock','Grizzly Bears','Island','Forest'))]
        library=session.state.players['A'].zones['library']
        for card in cards:library.remove(card.object_id)
        library.extend(c.object_id for c in reversed(cards))
        return cards

    def top_refs(self,session,count):
        return [session.state.cards[oid].ref for oid in reversed(session.state.players['A'].zones['library'][-count:])]

    def choose_order(self,session,expected,order=None):
        self.assertEqual('semantic.choice',session.state.pending_decision.kind);self.assertEqual(['A'],session.state.pending_decision.actors)
        for seat in 'BCD':
            packet=session.packet('pilot:'+seat,full=True);self.assertIsNone(packet['decision'])
            for ref in expected:self.assertNotIn(ref,json.dumps(packet))
        before=authoritative_state_hash(session.state)
        bad=[[],expected[:-1],expected[:-1]+[expected[0]],expected[:-1]+['foreign-library-ref']]
        for principal,refs in [('pilot:B',expected),*[('pilot:A',refs) for refs in bad if refs!=expected]]:
            self.assertFalse(session.act(principal,{'action_id':'choose','cards':refs}).ok);self.assertEqual(before,authoritative_state_hash(session.state))
        session=self.replay(session,load=True);chosen=list(reversed(expected)) if order is None else list(order);result=session.act('pilot:A',{'action_id':'choose','cards':chosen});self.assertTrue(result.ok,result.summary)
        return session,chosen
