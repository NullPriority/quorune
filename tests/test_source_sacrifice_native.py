"""Actual original-source costs before and after combat damage in four seats."""

import json,tempfile,unittest
from pathlib import Path
from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database


class SourceSacrificeNativeTests(InheritedNativeFixture,unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'source-sacrifice.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',ROOT/'tests/fixtures/source-sacrifice-payment-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Original source costs',[DeckEntry('Isamaru, Hound of Konda',1,'commander'),DeckEntry('Generic Bound Plains',14)],['Isamaru, Hound of Konda'])

    def choice_refs(self,s,seat):
        return s.packet('pilot:'+seat,full=True)['decision']['ctx']['legal_actions'][0]['choice_schema']['legal_refs']

    def test_actual_furnace_source_sacrifice_preserves_lki_for_its_following_damage(self):
        from unittest.mock import patch
        s=self.session(1181270122)
        source=self.add(s.engine,'Furnace Scamp',ref='paid-source-following-damage')
        source.acquired_control_turn_count=-1;self.assert_closed(s,source)
        self.prepare(s);self.checkpoint(s);identity=source.logical_object_id
        with patch.object(s.engine,'_dispatch_semantic_event',wraps=s.engine._dispatch_semantic_event) as dispatched:
            self.attack(s,[(source,'B')])
            self.until(s,lambda game:game.state.stack and self.trigger_count(game,source)==1)
            self.assertEqual('semantic.choice',self.resolve(s).kind)
            self.assertEqual([source.ref],self.choice_refs(s,'A'))
            self.assertEqual(39,s.state.players['B'].life)
            result=s.act('pilot:A',{'action_id':'choose','cards':[source.ref]});self.assertTrue(result.ok,result.summary)
            self.assertEqual('graveyard',s.state.cards[source.object_id].zone)
            self.assertEqual((40,36,40,40),tuple(s.state.players[seat].life for seat in 'ABCD'))
            events=[call.args[1] for call in dispatched.call_args_list if call.args[0]=='damage.dealt']
            self.assertEqual([1,3],[event['amount'] for event in events])
            self.assertEqual([identity,identity],[event['source_logical_object_id'] for event in events])
        self.replay(s,load=True)

    def test_actual_source_payments_keep_private_victim_original_incarnation_and_owner_destinations(self):
        s=self.session(1181270121)
        blinked=self.add(s.engine,'Blind Zealot',ref='source-cost-blinked')
        borrowed=self.add(s.engine,'Blind Zealot',ref='source-cost-borrowed');borrowed.owner='B'
        thrull=self.add(s.engine,'Mindstab Thrull',ref='source-cost-unblocked')
        b=self.add(s.engine,'Phyrexian Walker',seat='B',ref='source-cost-b-target')
        d=self.add(s.engine,'Phyrexian Walker',seat='D',ref='source-cost-d-target')
        private=[self.add(s.engine,name,seat='C',zone='hand',ref='source-cost-private-'+str(i))
            for i,name in enumerate(('Counterspell','Island','Forest'))]
        blink=self.add(s.engine,'Cloudshift',zone='hand',ref='source-cost-real-blink');self.add(s.engine,'Plains')
        for card in (blinked,borrowed,thrull):card.acquired_control_turn_count=-1;self.assert_closed(s,card)
        self.prepare(s);self.checkpoint(s);hand=len(s.state.players['C'].zones['hand'])
        self.attack(s,[(blinked,'B'),(thrull,'C'),(borrowed,'D')])
        self.until(s,lambda game:game.state.pending_decision and game.state.pending_decision.kind=='combat.blockers')
        for seat in ('B','D'):
            self.assertEqual([seat],s.state.pending_decision.actors)
            result=s.act('pilot:'+seat,{'action_id':'block','blocks':{}});self.assertTrue(result.ok,result.summary)
        self.assertEqual('semantic.choice',self.resolve(s).kind)
        self.assertEqual([thrull.ref],self.choice_refs(s,'A'))
        before=authoritative_state_hash(s.state)
        for seat,refs in (('D',[thrull.ref]),('A',[blinked.ref])):
            self.assertFalse(s.act('pilot:'+seat,{'action_id':'choose','cards':refs}).ok)
            self.assertEqual(before,authoritative_state_hash(s.state))
        for viewer in 'ABD':
            packet=json.dumps(s.packet('pilot:'+viewer,full=True))
            for card in private:self.assertNotIn(card.ref,packet)
        s=self.replay(s,load=True)
        result=s.act('pilot:A',{'action_id':'choose','cards':[thrull.ref]});self.assertTrue(result.ok,result.summary)
        self.assertEqual('graveyard',s.state.cards[thrull.object_id].zone)
        self.assertEqual('choice.apnap',s.state.pending_decision.kind);self.assertEqual(['C'],s.state.pending_decision.actors)
        before=authoritative_state_hash(s.state)
        command={'action_id':'choose','cards':[card.ref for card in private]}
        self.assertFalse(s.act('pilot:A',command).ok);self.assertEqual(before,authoritative_state_hash(s.state))
        for viewer in 'ABD':
            packet=json.dumps(s.packet('pilot:'+viewer,full=True))
            for card in private:self.assertNotIn(card.ref,packet)
        result=s.act('pilot:C',command);self.assertTrue(result.ok,result.summary)
        self.assertEqual(hand-3,len(s.state.players['C'].zones['hand']))
        self.assertTrue(all(s.state.cards[card.object_id].zone=='graveyard' for card in private))
        self.until(s,lambda game:game.state.pending_decision and game.state.pending_decision.kind=='semantic.target')
        victims=[]
        while s.state.pending_decision and s.state.pending_decision.kind=='semantic.target':
            ctx=s.packet('pilot:A',full=True)['decision']['ctx'];item=next(item for item in s.state.stack if item.ref==ctx['stack'])
            victim=item.context['target'];target={'B':b,'D':d}[victim]
            self.assertEqual([target.ref],ctx['target_schema']['legal_refs'])
            result=s.act('pilot:A',{'action_id':'choose','targets':[target.ref]});self.assertTrue(result.ok,result.summary);victims.append(victim)
        self.assertEqual({'B','D'},set(victims))
        self.assertEqual((40,38,40,38),tuple(s.state.players[seat].life for seat in 'ABCD'))
        old=s.state.cards[blinked.object_id].logical_object_id
        self.cast(s,'A',blink,[blinked.ref]);self.resolve_top(s)
        self.assertNotEqual(old,s.state.cards[blinked.object_id].logical_object_id)
        paid=unavailable=0
        while s.state.stack:
            decision=self.resolve(s)
            if decision is None:break
            self.assertEqual('semantic.choice',decision.kind)
            refs=self.choice_refs(s,'A');before=authoritative_state_hash(s.state)
            self.assertFalse(s.act('pilot:D',{'action_id':'choose','cards':refs}).ok)
            self.assertEqual(before,authoritative_state_hash(s.state))
            if refs:
                self.assertEqual([borrowed.ref],refs)
                self.assertFalse(s.act('pilot:A',{'action_id':'choose','cards':[blinked.ref]}).ok)
                self.assertEqual(before,authoritative_state_hash(s.state));paid+=1
            else:
                self.assertFalse(s.act('pilot:A',{'action_id':'choose','cards':[blinked.ref]}).ok)
                self.assertEqual(before,authoritative_state_hash(s.state));unavailable+=1
            result=s.act('pilot:A',{'action_id':'choose','cards':refs});self.assertTrue(result.ok,result.summary)
        self.assertEqual((1,1),(paid,unavailable))
        self.assertEqual('battlefield',s.state.cards[blinked.object_id].zone)
        self.assertEqual('battlefield',s.state.cards[b.object_id].zone)
        self.assertEqual('graveyard',s.state.cards[d.object_id].zone)
        self.assertIn(borrowed.object_id,s.state.players['B'].zones['graveyard'])
        self.assertNotIn(borrowed.object_id,s.state.players['A'].zones['graveyard'])
        self.assertIn(thrull.object_id,s.state.players['A'].zones['graveyard'])
        self.replay(s,load=True)
