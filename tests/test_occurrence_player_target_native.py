"""Actual damaged-player targets retain their principal and current control."""

import json,tempfile,unittest
from pathlib import Path
from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database


class OccurrencePlayerTargetNativeTests(InheritedNativeFixture,unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'occurrence-player-target.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',
            ROOT/'tests/fixtures/defending-player-target-cards.json',ROOT/'tests/fixtures/occurrence-player-target-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Damaged player target relation',[DeckEntry('Isamaru, Hound of Konda',1,'commander'),DeckEntry('Generic Bound Plains',14)],['Isamaru, Hound of Konda'])

    def choose_target(self,s,target,foreign,*,load=False):
        self.assertEqual('semantic.target',s.state.pending_decision.kind)
        offered=s.packet('pilot:A',full=True)['decision']['ctx']['target_schema']['legal_refs']
        self.assertEqual([target.ref],offered)
        before=authoritative_state_hash(s.state)
        for seat,ref in (('A',foreign.ref),('D',target.ref)):
            self.assertFalse(s.act('pilot:'+seat,{'action_id':'choose','targets':[ref]}).ok)
            self.assertEqual(before,authoritative_state_hash(s.state))
        if load:s=self.replay(s,load=True)
        result=s.act('pilot:A',{'action_id':'choose','targets':[target.ref]});self.assertTrue(result.ok,result.summary)
        return s

    def test_actual_split_damaged_players_keep_targets_after_source_blink_and_target_control_change(self):
        s=self.session(1206031001)
        ogre=self.add(s.engine,'Rustmouth Ogre',ref='ogre-hits-b')
        predator=self.add(s.engine,'Trygon Predator',ref='predator-hits-c')
        b=self.add(s.engine,'Phyrexian Walker',seat='B',ref='b-artifact-target')
        c=self.add(s.engine,'Sol Ring',seat='C',ref='c-artifact-target')
        d=self.add(s.engine,'Sol Ring',seat='D',ref='unrelated-player-artifact')
        private=self.add(s.engine,'Counterspell',seat='C',zone='hand',ref='private-occurrence-card')
        theft=self.add(s.engine,'Act of Treason',zone='hand',ref='actual-artifact-control-change')
        blink=self.add(s.engine,'Cloudshift',zone='hand',ref='actual-damage-source-reentry')
        self.add(s.engine,'Vedalken Orrery')
        for index in range(3):self.add(s.engine,'Mountain',ref='occurrence-target-red-'+str(index))
        self.add(s.engine,'Plains')
        for card in (ogre,predator):card.acquired_control_turn_count=-1;self.assert_closed(s,card)
        self.prepare(s);self.checkpoint(s);self.attack(s,[(ogre,'B'),(predator,'C')])
        self.until(s,lambda game:game.state.pending_decision and game.state.pending_decision.kind=='combat.blockers')
        self.assertEqual(['B'],s.state.pending_decision.actors)
        result=s.act('pilot:B',{'action_id':'block','blocks':{}});self.assertTrue(result.ok,result.summary)
        self.until(s,lambda game:game.state.pending_decision and game.state.pending_decision.kind=='semantic.target')
        seen=[]
        while s.state.pending_decision and s.state.pending_decision.kind=='semantic.target':
            context=s.packet('pilot:A',full=True)['decision']['ctx']
            item=next(item for item in s.state.stack if item.ref==context['stack'])
            victim=item.context['target'];target={'B':b,'C':c}[victim]
            for viewer in 'ABD':self.assertNotIn(private.ref,json.dumps(s.packet('pilot:'+viewer,full=True)))
            s=self.choose_target(s,target,d,load=not seen);seen.append(victim)
        self.assertEqual({'B','C'},set(seen));self.assertEqual(2,len(seen))
        self.assertEqual((40,35,38,40),tuple(s.state.players[seat].life for seat in 'ABCD'))
        old=s.state.cards[predator.object_id].logical_object_id
        self.cast(s,'A',blink,[predator.ref]);self.resolve_top(s)
        self.assertNotEqual(old,s.state.cards[predator.object_id].logical_object_id)
        self.cast(s,'A',theft,[b.ref]);self.resolve_top(s)
        self.assertEqual('A',s.state.cards[b.object_id].controller)
        choices=0
        while s.state.stack:
            decision=self.resolve(s)
            if decision is None:break
            self.assertEqual('semantic.choice',decision.kind)
            result=s.act('pilot:A',{'action_id':'choose','choice':'apply'});self.assertTrue(result.ok,result.summary);choices+=1
        self.assertEqual(1,choices)
        self.assertEqual('battlefield',s.state.cards[b.object_id].zone)
        self.assertEqual('graveyard',s.state.cards[c.object_id].zone)
        self.assertIn(c.object_id,s.state.players['C'].zones['graveyard'])
        self.assertEqual('battlefield',s.state.cards[d.object_id].zone)
        self.replay(s,load=True)

    def test_actual_granted_noncombat_damage_uses_the_damaged_player_without_a_combat_defender(self):
        s=self.session(1206031002)
        visionary=self.add(s.engine,'Soltari Visionary',ref='noncombat-original-visionary')
        visionary.acquired_control_turn_count=-1
        gift=self.add(s.engine,'Psionic Gift',zone='hand',ref='actual-granted-ping')
        b=self.add(s.engine,'Fervor',seat='B',ref='damaged-player-enchantment')
        c=self.add(s.engine,'Fervor',seat='C',ref='unrelated-enchantment')
        for index in range(2):self.add(s.engine,'Island',ref='actual-gift-mana-'+str(index))
        self.assert_closed(s,visionary);self.assert_closed(s,gift)
        self.prepare(s);self.checkpoint(s);self.cast(s,'A',gift,[visionary.ref]);self.assertIsNone(self.resolve(s))
        self.priority(s,'A')
        action=next((row for row in s.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
            if row['id'].startswith('activate:'+visionary.ref+':') and not row.get('mana_ability')),None)
        self.assertIsNotNone(action)
        result=s.act('pilot:A',{'action_id':action['id'],'targets':['B'],'pay':'auto'});self.assertTrue(result.ok,result.summary)
        self.resolve_top(s)
        self.assertEqual((40,39,40,40),tuple(s.state.players[seat].life for seat in 'ABCD'))
        self.assertFalse(s.state.combat.attackers)
        s=self.choose_target(s,b,c,load=True);self.assertIsNone(self.resolve(s))
        self.assertEqual('graveyard',s.state.cards[b.object_id].zone)
        self.assertEqual('battlefield',s.state.cards[c.object_id].zone)
        self.replay(s,load=True)
