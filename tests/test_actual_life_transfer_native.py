"""Printed actual-total gain retains loss, replacement and stabilization order."""
import json,tempfile,unittest
from pathlib import Path
from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database


class ActualLifeTransferNativeTests(InheritedNativeFixture,unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'actual-life-transfer.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',ROOT/'tests/fixtures/actual-life-transfer-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Actual loss transfer',[DeckEntry('Isamaru, Hound of Konda',1,'commander'),DeckEntry('Generic Bound Plains',14)],['Isamaru, Hound of Konda'])

    def test_actual_scholar_activation_pays_its_cost_and_uses_the_same_loss_owner(self):
        s=self.session(119608250);source=self.add(s.engine,'Scholar of Athreos')
        private=self.add(s.engine,'Counterspell',seat='C',zone='hand',ref='actual-loss-activation-private')
        self.assert_closed(s,source);self.prepare(s,mana={'B':1,'C':2});self.checkpoint(s)
        actions=s.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        offered=next(row for row in actions if row['id'].startswith('activate:'+source.ref+':'))
        command={'action_id':offered['id'],'pay':'auto'};before=authoritative_state_hash(s.state)
        self.assertFalse(s.act('pilot:D',command).ok);self.assertEqual(before,authoritative_state_hash(s.state))
        result=s.act('pilot:A',command);self.assertTrue(result.ok,result.summary)
        self.assertEqual(0,sum(s.state.players['A'].mana_pool.values()));s=self.replay(s,load=True)
        self.assertIsNone(self.resolve(s));self.assertEqual(43,s.state.players['A'].life)
        self.assertEqual((39,39,39),tuple(s.state.players[p].life for p in 'BCD'))
        self.assertEqual('battlefield',s.state.cards[source.object_id].zone)
        for viewer in 'ABD':self.assertNotIn(private.ref,json.dumps(s.packet('pilot:'+viewer,full=True)))
        self.replay(s,load=True)

    def test_actual_blood_tithe_counts_full_loss_before_departure_and_gains_once_with_replacement(self):
        for doubled in (False,True):
            with self.subTest(doubled=doubled):
                s=self.session(119608240+int(doubled))
                source=self.add(s.engine,'Blood Tithe',zone='hand')
                pride=self.add(s.engine,"Ajani's Pridemate")
                if doubled:
                    reflection=self.add(s.engine,'Boon Reflection');self.assert_closed(s,reflection)
                private=self.add(s.engine,'Counterspell',seat='C',zone='hand',ref='actual-loss-private')
                self.assert_closed(s,source);self.assert_closed(s,pride)
                s.state.players['B'].life=2
                self.prepare(s,mana={'B':1,'C':3});self.checkpoint(s)
                before=authoritative_state_hash(s.state)
                rejected=s.act('pilot:B',{'action_id':'cast:'+source.ref,'targets':[],'pay':'auto'})
                self.assertFalse(rejected.ok);self.assertEqual(before,authoritative_state_hash(s.state))
                self.cast(s,'A',source,[]);s=self.replay(s,load=True)
                self.assertIsNone(self.resolve(s))
                self.assertFalse(s.state.players['B'].in_game)
                self.assertEqual(49 if not doubled else 58,s.state.players['A'].life)
                self.assertEqual((37,37),tuple(s.state.players[p].life for p in 'CD'))
                self.assertEqual(1,s.state.cards[pride.object_id].counters.get('+1/+1',0))
                self.assertEqual('graveyard',s.state.cards[source.object_id].zone)
                self.assertTrue(set(s.pending_principals())<=set(('pilot:A','pilot:C','pilot:D')))
                for viewer in 'ABD':self.assertNotIn(private.ref,json.dumps(s.packet('pilot:'+viewer,full=True)))
                self.assertIn(private.ref,json.dumps(s.packet('pilot:C',full=True)))
                self.replay(s,load=True)
