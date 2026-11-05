"""CR118.12/701.21a: another controlled permanent pays the real attack cost."""
import json,tempfile,unittest
from pathlib import Path
from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database


class AnotherSacrificeNativeTests(InheritedNativeFixture,unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'another-sacrifice.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',
            ROOT/'tests/fixtures/another-sacrifice-payment-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Another sacrifice',[DeckEntry('Isamaru, Hound of Konda',1,'commander'),
            DeckEntry('Generic Bound Plains',14)],['Isamaru, Hound of Konda'])

    def test_actual_ogre_excludes_itself_and_foreign_control_then_pays_to_owner_graveyard_and_replays(self):
        s=self.session(118701210);source=self.add(s.engine,'Brawl-Bash Ogre',ref='actual-another-sacrifice-ogre')
        borrowed=self.add(s.engine,'Grizzly Bears',ref='borrowed-sacrifice');borrowed.owner='B'
        foreign=self.add(s.engine,'Grizzly Bears',seat='B',ref='foreign-sacrifice');foreign.owner='A'
        self.add(s.engine,'Counterspell',seat='C',zone='hand',ref='another-sacrifice-private-hand')
        source.acquired_control_turn_count=-1
        self.assert_closed(s,source);self.prepare(s);self.checkpoint(s)
        self.attack(s,[(source,'D')]);self.assertEqual('semantic.choice',self.resolve(s).kind)
        offered=s.packet('pilot:A',full=True)['decision']['ctx']['legal_actions'][0]['choice_schema']
        self.assertEqual([borrowed.ref],offered['legal_refs'])
        before=authoritative_state_hash(s.state)
        for actor,refs in (('B',[borrowed.ref]),('A',[source.ref]),('A',[foreign.ref]),('A',[borrowed.ref,borrowed.ref])):
            rejected=s.act('pilot:'+actor,{'action_id':'choose','cards':refs})
            self.assertFalse(rejected.ok);self.assertEqual(before,authoritative_state_hash(s.state))
        self.assertEqual(3,s.engine._numeric_stat(source.object_id,'power'))
        self.assertEqual('battlefield',s.state.cards[borrowed.object_id].zone)
        s=self.replay(s,load=True)
        paid=s.act('pilot:A',{'action_id':'choose','cards':[borrowed.ref]});self.assertTrue(paid.ok,paid.summary)
        self.assertEqual('graveyard',s.state.cards[borrowed.object_id].zone)
        self.assertIn(borrowed.object_id,s.state.players['B'].zones['graveyard'])
        self.assertNotIn(borrowed.object_id,s.state.players['A'].zones['graveyard'])
        self.assertEqual('battlefield',s.state.cards[source.object_id].zone)
        self.assertEqual('battlefield',s.state.cards[foreign.object_id].zone)
        self.assertEqual((5,5),(s.engine._numeric_stat(source.object_id,'power'),s.engine._numeric_stat(source.object_id,'toughness')))
        self.assertFalse(s.state.stack);self.assertEqual('priority',s.state.pending_decision.kind)
        for seat in 'ABD':self.assertNotIn('another-sacrifice-private-hand',json.dumps(s.packet('pilot:'+seat,full=True)))
        self.replay(s,load=True)
