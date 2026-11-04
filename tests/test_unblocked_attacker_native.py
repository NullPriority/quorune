"""CR509.3g: actual blocker completion precedes unblocked triggers and damage."""
import json,tempfile,unittest
from pathlib import Path
from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database


class UnblockedAttackerNativeTests(InheritedNativeFixture,unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'unblocked-attacker.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',ROOT/'tests/fixtures/unblocked-attacker-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Unblocked timing',[DeckEntry('Isamaru, Hound of Konda',1,'commander'),DeckEntry('Generic Bound Plains',14)],['Isamaru, Hound of Konda'])

    def test_actual_zero_block_assignments_still_create_one_trigger_and_source_blink_keeps_it(self):
        s=self.session(509300702);source=self.add(s.engine,'Eternal of Harsh Truths',ref='actual-zero-block-eternal')
        blink=self.add(s.engine,'Cloudshift',zone='hand',ref='actual-unblocked-source-blink')
        self.add(s.engine,'Plains',ref='zero-block-blink-mana')
        self.add(s.engine,'Counterspell',seat='D',zone='hand',ref='zero-block-private-hand')
        source.acquired_control_turn_count=-1;self.assert_closed(s,source)
        self.prepare(s);self.checkpoint(s);hands=len(s.state.players['A'].zones['hand'])
        self.attack(s,[(source,'B')]);self.assertFalse(s.state.stack)
        self.until(s,lambda game:bool(game.state.stack) and not game.state.pending_trigger_batches)
        self.assertEqual(1,len(s.state.stack));self.assertFalse(s.state.combat.blockers)
        original=s.state.cards[source.object_id].logical_object_id
        self.cast(s,'A',blink,[source.ref]);self.resolve_top(s)
        self.assertNotEqual(original,s.state.cards[source.object_id].logical_object_id)
        s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
        self.assertEqual(hands,len(s.state.players['A'].zones['hand']),'One blink spent and the already-created draw retained')
        self.assertEqual(40,s.state.players['B'].life)
        self.until(s,lambda game:game.state.phase=='combat' and game.state.step=='end_combat' and not game.state.stack)
        self.assertEqual(40,s.state.players['B'].life)
        for seat in 'ABC':self.assertNotIn('zero-block-private-hand',json.dumps(s.packet('pilot:'+seat,full=True)))
        self.replay(s,load=True)

    def test_actual_unblocked_trigger_waits_for_all_blockers_and_blocker_departure_does_not_retrigger(self):
        s=self.session(509300701)
        open_attacker=self.add(s.engine,'Eternal of Harsh Truths',ref='actual-unblocked-eternal')
        blocked=self.add(s.engine,'Eternal of Harsh Truths',ref='actual-blocked-eternal')
        blocker=self.add(s.engine,'Phyrexian Walker',seat='B',ref='actual-declared-walker')
        self.add(s.engine,'Phyrexian Walker',seat='C',ref='actual-declined-walker')
        bounce=self.add(s.engine,'Unsummon',seat='B',zone='hand',ref='actual-later-blocker-removal')
        self.add(s.engine,'Island',seat='B',ref='unblocked-bounce-mana')
        self.add(s.engine,'Counterspell',seat='D',zone='hand',ref='unblocked-private-hand')
        for card in (open_attacker,blocked):card.acquired_control_turn_count=-1
        self.assert_closed(s,open_attacker);self.prepare(s);self.checkpoint(s)
        hand=len(s.state.players['A'].zones['hand'])
        self.attack(s,[(open_attacker,'C'),(blocked,'B')])
        self.assertFalse(s.state.stack,'Unblocked event must not fire at attacker declaration')
        self.until(s,lambda game:game.state.pending_decision is not None and game.state.pending_decision.kind=='combat.blockers')
        command={'action_id':'block','blocks':{blocker.ref:blocked.ref}}
        before=authoritative_state_hash(s.state);self.assertFalse(s.act('pilot:D',command).ok);self.assertEqual(before,authoritative_state_hash(s.state))
        result=s.act('pilot:B',command);self.assertTrue(result.ok,result.summary)
        self.assertEqual(hand,len(s.state.players['A'].zones['hand']))
        result=s.act('pilot:C',{'action_id':'block','blocks':{}});self.assertTrue(result.ok,result.summary)
        self.until(s,lambda game:bool(game.state.stack) and not game.state.pending_trigger_batches)
        self.assertEqual(2,len(s.state.stack),'One unblocked draw and the blocked attacker\'s Afflict')
        s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
        self.assertEqual(hand+1,len(s.state.players['A'].zones['hand']))
        self.assertEqual((38,40),(s.state.players['B'].life,s.state.players['C'].life))
        self.cast(s,'B',bounce,[blocker.ref]);self.assertIsNone(self.resolve(s))
        self.assertEqual('hand',s.state.cards[blocker.object_id].zone)
        self.until(s,lambda game:game.state.phase=='combat' and game.state.step=='end_combat' and not game.state.stack)
        self.assertEqual(hand+1,len(s.state.players['A'].zones['hand']))
        self.assertEqual((38,39),(s.state.players['B'].life,s.state.players['C'].life))
        for seat in 'ABC':self.assertNotIn('unblocked-private-hand',json.dumps(s.packet('pilot:'+seat,full=True)))
        self.replay(s,load=True)
