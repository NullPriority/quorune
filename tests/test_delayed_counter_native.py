"""CR122/603.7: actual combat delays counter changes and excludes new objects."""
import json,tempfile,unittest
from pathlib import Path
from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database


class DelayedCounterNativeTests(InheritedNativeFixture,unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'delayed-counters.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',
            ROOT/'tests/fixtures/delayed-counter-action-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Delayed counters',[DeckEntry('Isamaru, Hound of Konda',1,'commander'),
            DeckEntry('Generic Bound Plains',14)],['Isamaru, Hound of Konda'])

    def test_actual_clockwork_and_warcrawler_delay_counter_changes_and_skip_the_returned_beetle(self):
        s=self.session(603122001)
        retained=self.add(s.engine,'Clockwork Beetle',ref='actual-retained-clockwork')
        returned=self.add(s.engine,'Clockwork Beetle',ref='actual-blinked-clockwork')
        warcrawler=self.add(s.engine,'Wicker Warcrawler',ref='actual-delayed-warcrawler')
        for card in (retained,returned):card.counters['+1/+1']=2
        for card in (retained,returned,warcrawler):card.acquired_control_turn_count=-1
        blink=self.add(s.engine,'Cloudshift',zone='hand',ref='actual-counter-blink')
        self.add(s.engine,'Plains',ref='counter-blink-mana')
        self.add(s.engine,'Counterspell',seat='C',zone='hand',ref='delayed-counter-private-hand')
        for card in (retained,warcrawler,blink):self.assert_closed(s,card)
        self.prepare(s);self.checkpoint(s)
        self.attack(s,[(retained,'D'),(returned,'D'),(warcrawler,'B')]);self.assertIsNone(self.resolve(s))
        self.assertEqual(3,len(s.state.delayed_triggers))
        self.assertEqual((2,2,0),(s.state.cards[retained.object_id].counters.get('+1/+1',0),
            s.state.cards[returned.object_id].counters.get('+1/+1',0),s.state.cards[warcrawler.object_id].counters.get('-1/-1',0)))
        original=s.state.cards[returned.object_id].logical_object_id
        self.cast(s,'A',blink,[returned.ref]);self.assertIsNone(self.resolve(s))
        self.assertNotEqual(original,s.state.cards[returned.object_id].logical_object_id)
        self.assertEqual(2,s.state.cards[returned.object_id].counters.get('+1/+1',0))
        self.until(s,lambda game:game.state.phase=='combat' and game.state.step=='end_combat'
            and bool(game.state.stack) and not game.state.pending_trigger_batches)
        self.assertEqual(3,len(s.state.stack))
        self.assertEqual((38,34),(s.state.players['D'].life,s.state.players['B'].life))
        self.assertEqual((2,2,0),(s.state.cards[retained.object_id].counters.get('+1/+1',0),
            s.state.cards[returned.object_id].counters.get('+1/+1',0),s.state.cards[warcrawler.object_id].counters.get('-1/-1',0)))
        before=authoritative_state_hash(s.state)
        self.assertFalse(s.act('pilot:D',{'action_id':'choose','pay':True}).ok)
        self.assertEqual(before,authoritative_state_hash(s.state))
        s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
        self.assertEqual((1,2,1),(s.state.cards[retained.object_id].counters.get('+1/+1',0),
            s.state.cards[returned.object_id].counters.get('+1/+1',0),s.state.cards[warcrawler.object_id].counters.get('-1/-1',0)))
        self.assertEqual((1,5),(s.engine._numeric_stat(retained.object_id,'power'),s.engine._numeric_stat(warcrawler.object_id,'power')))
        self.assertFalse(any(trigger.active for trigger in s.state.delayed_triggers))
        for seat in 'ABD':self.assertNotIn('delayed-counter-private-hand',json.dumps(s.packet('pilot:'+seat,full=True)))
        self.replay(s,load=True)
