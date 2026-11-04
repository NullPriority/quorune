"""CR115/601.2c/608.2b: control and keyword requirements apply together."""
import json,tempfile,unittest
from pathlib import Path
from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database


class ControlledKeywordTargetNativeTests(InheritedNativeFixture,unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'controlled-keywords.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',
            ROOT/'tests/fixtures/controlled-keyword-target-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Controlled keywords',[DeckEntry('Isamaru, Hound of Konda',1,'commander'),
            DeckEntry('Generic Bound Plains',14)],['Isamaru, Hound of Konda'])

    def test_actual_counter_trigger_requires_current_control_and_absent_flying_after_pending_load(self):
        s=self.session(115608201);root=self.add(s.engine,'Grizzly Bears',ref='controlled-nonflyer')
        flying=self.add(s.engine,'Concordia Pegasus',ref='controlled-flyer')
        foreign=self.add(s.engine,'Grizzly Bears',seat='B',ref='foreign-nonflyer')
        first=self.add(s.engine,'Pileated Provisioner',zone='hand',ref='actual-first-provisioner')
        second=self.add(s.engine,'Pileated Provisioner',zone='hand',ref='actual-second-provisioner')
        jump=self.add(s.engine,'Jump',seat='B',zone='hand',ref='actual-flying-response')
        self.add(s.engine,'Counterspell',seat='C',zone='hand',ref='private-controlled-keyword-hand')
        for card in (first,jump):self.assert_closed(s,card)
        self.prepare(s,mana={'C':20,'W':5});s.state.players['B'].mana_pool.update({'U':3});self.checkpoint(s)
        self.cast(s,'A',first);self.assertEqual('semantic.target',self.resolve(s).kind)
        before=authoritative_state_hash(s.state)
        for invalid in (flying,foreign):
            self.assertFalse(s.act('pilot:A',{'action_id':'choose','targets':[invalid.ref]}).ok)
            self.assertEqual(before,authoritative_state_hash(s.state))
        for seat in 'ABD':self.assertNotIn('private-controlled-keyword-hand',json.dumps(s.packet('pilot:'+seat,full=True)))
        s=self.replay(s,load=True);self.target(s,'A',[root.ref]);self.assertIsNone(self.resolve(s))
        self.assertEqual(1,s.state.cards[root.object_id].counters.get('+1/+1',0))
        self.cast(s,'A',second);self.assertEqual('semantic.target',self.resolve(s).kind)
        self.target(s,'A',[root.ref]);self.cast(s,'B',jump,[root.ref]);self.assertIsNone(self.resolve(s))
        self.assertIn('flying',s.engine._combat_keywords(s.state.cards[root.object_id]))
        self.assertEqual(1,s.state.cards[root.object_id].counters.get('+1/+1',0),
            'The now-flying recipient cannot receive the second counter')
        self.assertFalse(s.state.stack);self.replay(s,load=True)
