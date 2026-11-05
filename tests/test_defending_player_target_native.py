"""CR508.5/608.2b: actual target choices retain the particular defender."""

import json,tempfile,unittest
from pathlib import Path
from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database


class DefendingPlayerTargetNativeTests(InheritedNativeFixture,unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'defending-player-target.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',ROOT/'tests/fixtures/defending-player-target-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Defender target relation',[DeckEntry('Isamaru, Hound of Konda',1,'commander'),DeckEntry('Generic Bound Plains',14)],['Isamaru, Hound of Konda'])

    def test_actual_split_defenders_revalidate_control_and_survive_attacker_reentry(self):
        s=self.session(508601608)
        binder=self.add(s.engine,'Fiend Binder',ref='binder-attacks-b')
        master=self.add(s.engine,'Master of Diversion',ref='master-attacks-c')
        b=self.add(s.engine,'Phyrexian Walker',seat='B',ref='b-defending-target')
        c=self.add(s.engine,'Phyrexian Walker',seat='C',ref='c-defending-target')
        d=self.add(s.engine,'Phyrexian Walker',seat='D',ref='unrelated-defender-target')
        private=self.add(s.engine,'Counterspell',seat='C',zone='hand',ref='private-c-card')
        theft=self.add(s.engine,'Act of Treason',zone='hand',ref='actual-target-control-change')
        blink=self.add(s.engine,'Cloudshift',zone='hand',ref='actual-attacker-reentry')
        self.add(s.engine,'Vedalken Orrery')
        for index in range(3):self.add(s.engine,'Mountain',ref='target-relation-red-'+str(index))
        self.add(s.engine,'Plains')
        for card in (binder,master):card.acquired_control_turn_count=-1;self.assert_closed(s,card)
        self.prepare(s);self.checkpoint(s)
        self.attack(s,[(binder,'B'),(master,'C')])
        seen=[]
        while s.state.pending_decision and s.state.pending_decision.kind=='semantic.target':
            context=s.packet('pilot:A',full=True)['decision']['ctx']
            item=next(item for item in s.state.stack if item.ref==context['stack'])
            defender=item.context['defending_player'];target={'B':b,'C':c}[defender]
            offered=context['target_schema']['legal_refs']
            self.assertEqual([target.ref],offered)
            before=authoritative_state_hash(s.state)
            self.assertFalse(s.act('pilot:A',{'action_id':'choose','targets':[d.ref]}).ok)
            self.assertEqual(before,authoritative_state_hash(s.state))
            self.assertFalse(s.act('pilot:D',{'action_id':'choose','targets':[target.ref]}).ok)
            self.assertEqual(before,authoritative_state_hash(s.state))
            for viewer in 'ABD':self.assertNotIn(private.ref,json.dumps(s.packet('pilot:'+viewer,full=True)))
            if not seen:s=self.replay(s,load=True)
            result=s.act('pilot:A',{'action_id':'choose','targets':[target.ref]});self.assertTrue(result.ok,result.summary)
            seen.append(defender)
        self.assertEqual({'B','C'},set(seen));self.assertEqual(2,len(seen))
        self.assertFalse(s.state.cards[b.object_id].tapped);self.assertFalse(s.state.cards[c.object_id].tapped)
        old=s.state.cards[master.object_id].logical_object_id
        self.cast(s,'A',blink,[master.ref]);self.resolve_top(s)
        self.assertNotEqual(old,s.state.cards[master.object_id].logical_object_id)
        self.assertNotIn(master.object_id,s.state.combat.attackers)
        self.cast(s,'A',theft,[b.ref]);self.resolve_top(s)
        self.assertEqual('A',s.state.cards[b.object_id].controller)
        self.assertIsNone(self.resolve(s))
        self.assertFalse(s.state.cards[b.object_id].tapped)
        self.assertTrue(s.state.cards[c.object_id].tapped)
        self.assertFalse(s.state.cards[d.object_id].tapped)
        self.until(s,lambda game:game.state.phase_index>=9)
        self.assertEqual((40,37,40,40),tuple(s.state.players[seat].life for seat in 'ABCD'))
        self.replay(s,load=True)
