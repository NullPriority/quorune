"""Actual one-per-opponent selection, partial resolution and copied choices."""
import json,tempfile,unittest
from pathlib import Path
from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database


class PerPlayerTargetNativeTests(InheritedNativeFixture,unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'per-player-target.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',ROOT/'tests/fixtures/per-player-target-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Per player targets',[DeckEntry('Isamaru, Hound of Konda',1,'commander'),DeckEntry('Generic Bound Plains',14)],['Isamaru, Hound of Konda'])

    def reject(self,s,seat,command):
        before=authoritative_state_hash(s.state);result=s.act('pilot:'+seat,command)
        self.assertFalse(result.ok,result.summary);self.assertEqual(before,authoritative_state_hash(s.state))

    def board(self,seed):
        s=self.session(seed);wave=self.add(s.engine,'Dismantling Wave',zone='hand',ref='actual-per-player-wave')
        own=self.add(s.engine,'Phyrexian Walker',ref='per-player-own')
        b1=self.add(s.engine,'Phyrexian Walker',seat='B',ref='per-player-b-first')
        b2=self.add(s.engine,'Phyrexian Walker',seat='B',ref='per-player-b-second')
        c=self.add(s.engine,'Phyrexian Walker',seat='C',ref='per-player-c')
        d=self.add(s.engine,'Phyrexian Walker',seat='D',ref='per-player-d')
        private=self.add(s.engine,'Counterspell',seat='C',zone='hand',ref='per-player-private')
        self.assert_closed(s,wave)
        return s,wave,own,b1,b2,c,d,private

    def test_actual_wave_rejects_same_player_then_keeps_partial_targets_after_control_and_departure(self):
        s,wave,own,b1,b2,c,d,private=self.board(115760820)
        theft=self.add(s.engine,'Act of Treason',seat='D',zone='hand',ref='per-player-actual-control-change')
        self.add(s.engine,'Vedalken Orrery',seat='D')
        for i in range(3):self.add(s.engine,'Mountain',seat='D',ref='per-player-red-'+str(i))
        self.prepare(s,mana={'W':1,'C':2});self.checkpoint(s)
        action=next(row for row in s.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions'] if row['id']=='cast:'+wave.ref)
        group=action['target_schema']['groups'][0]
        self.assertTrue(group['distinct_controllers']);self.assertTrue(group['retain_controller'])
        self.assertNotIn(own.ref,group['legal_refs'])
        self.reject(s,'A',{'action_id':action['id'],'targets':[b1.ref,b2.ref],'pay':'auto'})
        self.reject(s,'B',{'action_id':action['id'],'targets':[b1.ref,c.ref,d.ref],'pay':'auto'})
        self.cast(s,'A',wave,[b1.ref,c.ref,d.ref])
        self.cast(s,'D',theft,[b1.ref]);self.resolve_top(s)
        self.assertEqual('D',s.state.cards[b1.object_id].controller)
        for viewer in 'ABD':self.assertNotIn(private.ref,json.dumps(s.packet('pilot:'+viewer,full=True)))
        self.assertIn(private.ref,json.dumps(s.packet('pilot:C',full=True)))
        self.priority(s,'C')
        result=s.act('pilot:C',{'action_id':'concede','choices':{'confirm_concede':True}});self.assertTrue(result.ok,result.summary)
        self.assertFalse(s.state.players['C'].in_game)
        s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
        self.assertEqual('battlefield',s.state.cards[b1.object_id].zone)
        self.assertEqual('battlefield',s.state.cards[b2.object_id].zone)
        self.assertEqual('battlefield',s.state.cards[own.object_id].zone)
        self.assertEqual('graveyard',s.state.cards[d.object_id].zone)
        self.assertIn(d.object_id,s.state.players['D'].zones['graveyard'])
        self.replay(s,load=True)

    def test_actual_copy_keeps_illegal_original_and_count_while_retargeting_under_new_controller(self):
        s,wave,own,b1,b2,c,d,private=self.board(115770710)
        copy=self.add(s.engine,'Reverberate',seat='B',zone='hand',ref='per-player-real-copy')
        s.state.players['B'].mana_pool.update({'R':2})
        self.prepare(s,mana={'W':1,'C':2});self.checkpoint(s)
        self.cast(s,'A',wave,[b1.ref,c.ref,d.ref]);self.cast(s,'B',copy,[s.state.stack[-1].ref])
        decision=self.resolve(s);self.assertEqual('semantic.choice',decision.kind)
        self.reject(s,'B',{'action_id':'choose','targets':[own.ref,d.ref]})
        self.reject(s,'B',{'action_id':'choose','targets':[b2.ref,c.ref,d.ref]})
        self.reject(s,'A',{'action_id':'choose','targets':[{'retain':0},own.ref,{'retain':2}]})
        for viewer in 'ABD':self.assertNotIn(private.ref,json.dumps(s.packet('pilot:'+viewer,full=True)))
        s=self.replay(s,load=True)
        result=s.act('pilot:B',{'action_id':'choose','targets':[{'retain':0},own.ref,{'retain':2}]});self.assertTrue(result.ok,result.summary)
        self.assertEqual('spell_copy',s.state.stack[-1].kind)
        self.assertEqual(3,len(s.state.stack[-1].targets));self.resolve_top(s)
        self.assertEqual('graveyard',s.state.cards[own.object_id].zone)
        self.assertEqual('graveyard',s.state.cards[d.object_id].zone)
        self.assertEqual('battlefield',s.state.cards[b1.object_id].zone)
        self.assertEqual('battlefield',s.state.cards[c.object_id].zone)
        self.assertIsNone(self.resolve(s))
        for target in (b1,c):self.assertEqual('graveyard',s.state.cards[target.object_id].zone)
        self.assertEqual('battlefield',s.state.cards[b2.object_id].zone)
        self.replay(s,load=True)

    def test_actual_optional_zero_targets_resolves_without_target_or_effect_mutation(self):
        s,wave,own,b1,b2,c,d,_private=self.board(115760800)
        self.prepare(s,mana={'W':1,'C':2});self.checkpoint(s);self.cast(s,'A',wave,[])
        self.assertIsNone(self.resolve(s))
        for card in (own,b1,b2,c,d):self.assertEqual('battlefield',s.state.cards[card.object_id].zone)
        self.replay(s,load=True)
