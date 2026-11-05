"""CR508.5: actual combat results reach the sealed defending player."""
import json,tempfile,unittest
from pathlib import Path
from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database


class CombatDefendingPlayerNativeTests(InheritedNativeFixture,unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'combat-defending-player.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',ROOT/'tests/fixtures/combat-defending-player-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Defending player results',[DeckEntry('Isamaru, Hound of Konda',1,'commander'),DeckEntry('Generic Bound Plains',14)],['Isamaru, Hound of Konda'])

    def test_actual_blocked_results_keep_each_defender_private_choices_and_once_per_attacker(self):
        s=self.session(508509701)
        golem=self.add(s.engine,'Flint Golem',ref='actual-defender-mill')
        ghoul=self.add(s.engine,'Vedalken Ghoul',ref='actual-defender-life')
        grifters=self.add(s.engine,'Alley Grifters',ref='actual-defender-discard')
        beast=self.add(s.engine,'Thresher Beast',ref='actual-defender-sacrifice')
        b1=self.add(s.engine,'Phyrexian Walker',seat='B',ref='defender-b-first-blocker')
        b2=self.add(s.engine,'Phyrexian Walker',seat='B',ref='defender-b-second-blocker')
        c=self.add(s.engine,'Phyrexian Walker',seat='C',ref='defender-c-blocker')
        d1=self.add(s.engine,'Phyrexian Walker',seat='D',ref='defender-d-first-blocker')
        d2=self.add(s.engine,'Phyrexian Walker',seat='D',ref='defender-d-second-blocker')
        discard=self.add(s.engine,'Counterspell',seat='D',zone='hand',ref='actual-private-defender-discard')
        land=self.add(s.engine,'Plains',seat='D',ref='actual-defender-land-payment')
        for card in (golem,ghoul,grifters,beast):card.acquired_control_turn_count=-1;self.assert_closed(s,card)
        self.prepare(s);self.checkpoint(s)
        libraries={seat:len(s.state.players[seat].zones['library']) for seat in 'ABCD'}
        graves={seat:len(s.state.players[seat].zones['graveyard']) for seat in 'ABCD'}
        hands={seat:len(s.state.players[seat].zones['hand']) for seat in 'ABCD'}
        self.attack(s,[(golem,'B'),(ghoul,'C'),(grifters,'D'),(beast,'D')])
        self.until(s,lambda game:game.state.pending_decision is not None and game.state.pending_decision.kind=='combat.blockers')
        for seat,blocks in (('B',{b1.ref:golem.ref,b2.ref:golem.ref}),('C',{c.ref:ghoul.ref}),('D',{d1.ref:grifters.ref,d2.ref:beast.ref})):
            command={'action_id':'block','blocks':blocks};before=authoritative_state_hash(s.state)
            self.assertFalse(s.act('pilot:A',command).ok);self.assertEqual(before,authoritative_state_hash(s.state))
            result=s.act('pilot:'+seat,command);self.assertTrue(result.ok,result.summary)
        self.until(s,lambda game:len(game.state.stack)==4 and not game.state.pending_trigger_batches)
        seen=0
        while s.state.stack:
            decision=self.resolve(s)
            if decision is None:break
            self.assertEqual('choice.apnap',decision.kind);self.assertEqual(['D'],decision.actors)
            options=s.packet('pilot:D',full=True)['decision']['ctx']['options']
            before=authoritative_state_hash(s.state)
            command={'action_id':'choose','cards':[land.ref if land.ref in options else discard.ref]}
            self.assertFalse(s.act('pilot:A',command).ok);self.assertEqual(before,authoritative_state_hash(s.state))
            if s.state.cards[discard.object_id].zone=='hand':
                for viewer in 'ABC':self.assertNotIn(discard.ref,json.dumps(s.packet('pilot:'+viewer,full=True)))
            s=self.replay(s,load=True);result=s.act('pilot:D',command);self.assertTrue(result.ok,result.summary);seen+=1
        self.assertEqual(2,seen)
        self.assertEqual(libraries['B']-3,len(s.state.players['B'].zones['library']))
        for seat in 'ACD':self.assertEqual(libraries[seat],len(s.state.players[seat].zones['library']))
        self.assertEqual((40,40,36,40),tuple(s.state.players[seat].life for seat in 'ABCD'))
        self.assertEqual('graveyard',s.state.cards[discard.object_id].zone);self.assertEqual('graveyard',s.state.cards[land.object_id].zone)
        self.assertEqual(hands['D']-1,len(s.state.players['D'].zones['hand']))
        self.assertEqual(graves['B']+3,len(s.state.players['B'].zones['graveyard']))
        self.replay(s,load=True)
