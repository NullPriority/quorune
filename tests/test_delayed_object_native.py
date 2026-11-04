"""CR603.7: real combat creates delayed actions without immediate movement."""
import json,tempfile,unittest
from pathlib import Path
from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database


class DelayedObjectNativeTests(InheritedNativeFixture,unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'delayed-object.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',
            ROOT/'tests/fixtures/delayed-object-action-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Delayed object actions',[DeckEntry('Isamaru, Hound of Konda',1,'commander'),
            DeckEntry('Generic Bound Plains',14)],['Isamaru, Hound of Konda'])

    def test_actual_combat_delays_destruction_and_sacrifice_and_skips_the_blinked_wall_after_load(self):
        s=self.session(603700001)
        fog=self.add(s.engine,'Fog Elemental',ref='actual-delayed-fog')
        first=self.add(s.engine,'Grizzly Bears',ref='first-delayed-attacker')
        second=self.add(s.engine,'Grizzly Bears',ref='second-delayed-attacker')
        asp=self.add(s.engine,'Tangle Asp',ref='actual-delayed-counterpart-source')
        counterpart=self.add(s.engine,'Grizzly Bears',seat='B',ref='actual-delayed-counterpart')
        blinked=self.add(s.engine,'Cinder Wall',seat='B',ref='actual-blinked-wall')
        doomed=self.add(s.engine,'Cinder Wall',seat='B',ref='actual-doomed-wall')
        blink=self.add(s.engine,'Cloudshift',seat='B',zone='hand',ref='actual-delayed-blink')
        self.add(s.engine,'Plains',seat='B',ref='delayed-blink-mana')
        self.add(s.engine,'Counterspell',seat='C',zone='hand',ref='delayed-private-hand')
        for card in (fog,first,second,asp):card.acquired_control_turn_count=-1
        for card in (fog,blinked,doomed,blink,asp):self.assert_closed(s,card)
        self.prepare(s);self.checkpoint(s)
        self.attack(s,[(fog,'D'),(first,'B'),(second,'B'),(asp,'B')]);self.assertIsNone(self.resolve(s))
        self.assertEqual(1,len(s.state.delayed_triggers))
        self.until(s,lambda game:game.state.pending_decision is not None and game.state.pending_decision.kind=='combat.blockers')
        command={'action_id':'block','blocks':{blinked.ref:first.ref,doomed.ref:second.ref,counterpart.ref:asp.ref}}
        before=authoritative_state_hash(s.state)
        self.assertFalse(s.act('pilot:C',command).ok);self.assertEqual(before,authoritative_state_hash(s.state))
        result=s.act('pilot:B',command);self.assertTrue(result.ok,result.summary)
        self.until(s,lambda game:len(game.state.delayed_triggers)==4 and not game.state.stack and game.state.pending_decision.kind=='priority')
        for card in (fog,blinked,doomed,asp,counterpart):self.assertEqual('battlefield',s.state.cards[card.object_id].zone)
        original=s.state.cards[blinked.object_id].logical_object_id
        self.cast(s,'B',blink,[blinked.ref]);self.assertIsNone(self.resolve(s))
        self.assertNotEqual(original,s.state.cards[blinked.object_id].logical_object_id)
        self.until(s,lambda game:game.state.phase=='combat' and game.state.step=='end_combat' and bool(game.state.stack) and not game.state.pending_trigger_batches)
        self.assertEqual(4,len(s.state.stack))
        self.assertEqual(36,s.state.players['D'].life)
        self.assertEqual(40,s.state.players['B'].life)
        self.assertEqual('graveyard',s.state.cards[asp.object_id].zone)
        self.assertEqual(1,s.state.cards[counterpart.object_id].marked_damage)
        for card in (fog,blinked,doomed,counterpart):self.assertEqual('battlefield',s.state.cards[card.object_id].zone)
        s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
        self.assertEqual('battlefield',s.state.cards[blinked.object_id].zone)
        for card in (fog,doomed,counterpart):self.assertEqual('graveyard',s.state.cards[card.object_id].zone)
        self.assertEqual(4,len(s.state.delayed_triggers));self.assertFalse(any(t.active for t in s.state.delayed_triggers))
        for seat in 'ABD':self.assertNotIn('delayed-private-hand',json.dumps(s.packet('pilot:'+seat,full=True)))
        self.replay(s,load=True)
