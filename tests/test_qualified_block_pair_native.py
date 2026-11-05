"""CR509.3f/603.7: actual block qualities are captured before responses."""
import json,tempfile,unittest
from pathlib import Path
from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database


class QualifiedBlockPairNativeTests(InheritedNativeFixture,unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'qualified-block-pairs.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',ROOT/'tests/fixtures/qualified-block-pair-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Qualified block pairs',[DeckEntry('Isamaru, Hound of Konda',1,'commander'),DeckEntry('Generic Bound Plains',14)],['Isamaru, Hound of Konda'])

    def test_actual_single_blocked_by_triggers_once_per_blocker_and_skips_a_returned_blocker(self):
        s=self.session(509603702);source=self.add(s.engine,'Sylvan Basilisk',ref='actual-single-pair-basilisk')
        first=self.add(s.engine,'Phyrexian Walker',seat='B',ref='single-pair-first-blocker')
        returned=self.add(s.engine,'Phyrexian Walker',seat='B',ref='single-pair-returned-blocker')
        blink=self.add(s.engine,'Cloudshift',seat='B',zone='hand',ref='single-pair-blocker-blink')
        self.add(s.engine,'Plains',seat='B',ref='single-pair-blink-mana')
        self.add(s.engine,'Counterspell',seat='D',zone='hand',ref='single-pair-private-hand')
        source.acquired_control_turn_count=-1;self.assert_closed(s,source)
        self.prepare(s);self.checkpoint(s);self.attack(s,[(source,'B')])
        self.until(s,lambda game:game.state.pending_decision is not None and game.state.pending_decision.kind=='combat.blockers')
        command={'action_id':'block','blocks':{first.ref:source.ref,returned.ref:source.ref}}
        before=authoritative_state_hash(s.state);self.assertFalse(s.act('pilot:C',command).ok);self.assertEqual(before,authoritative_state_hash(s.state))
        result=s.act('pilot:B',command);self.assertTrue(result.ok,result.summary)
        self.until(s,lambda game:len(game.state.stack)==2 and not game.state.pending_trigger_batches)
        self.assertEqual('battlefield',s.state.cards[first.object_id].zone)
        original=s.state.cards[returned.object_id].logical_object_id
        self.cast(s,'B',blink,[returned.ref]);self.resolve_top(s)
        self.assertNotEqual(original,s.state.cards[returned.object_id].logical_object_id)
        s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
        self.assertEqual('graveyard',s.state.cards[first.object_id].zone)
        self.assertEqual('battlefield',s.state.cards[returned.object_id].zone)
        self.assertEqual(0,s.state.cards[source.object_id].marked_damage)
        self.assertFalse(s.state.delayed_triggers)
        for seat in 'ABC':self.assertNotIn('single-pair-private-hand',json.dumps(s.packet('pilot:'+seat,full=True)))
        self.replay(s,load=True)

    def test_actual_deathgazer_uses_block_time_color_and_old_recipient_after_pending_load(self):
        s=self.session(509603701)
        first=self.add(s.engine,'Phyrexian Walker',ref='qualified-first-attacker')
        returned=self.add(s.engine,'Phyrexian Walker',ref='qualified-returned-attacker')
        black=self.add(s.engine,'Walking Corpse',ref='qualified-black-attacker')
        gazers=[self.add(s.engine,'Deathgazer',seat='B',ref='actual-qualified-gazer-'+str(i)) for i in range(3)]
        self.add(s.engine,'Vedalken Orrery',seat='B',ref='qualified-black-response-timing')
        darkness=self.add(s.engine,'Darkest Hour',seat='B',zone='hand',ref='actual-after-block-black')
        blink=self.add(s.engine,'Cloudshift',zone='hand',ref='actual-qualified-recipient-blink')
        self.add(s.engine,'Plains',ref='qualified-blink-mana');self.add(s.engine,'Swamp',seat='B',ref='qualified-color-mana')
        self.add(s.engine,'Counterspell',seat='D',zone='hand',ref='qualified-block-private-hand')
        for card in (first,returned,black):card.acquired_control_turn_count=-1
        for card in (gazers[0],darkness,blink):self.assert_closed(s,card)
        self.prepare(s);self.checkpoint(s)
        self.attack(s,[(first,'B'),(returned,'B'),(black,'B')])
        self.until(s,lambda game:game.state.pending_decision is not None and game.state.pending_decision.kind=='combat.blockers')
        command={'action_id':'block','blocks':{g.ref:body.ref for g,body in zip(gazers,(first,returned,black))}}
        before=authoritative_state_hash(s.state);self.assertFalse(s.act('pilot:C',command).ok);self.assertEqual(before,authoritative_state_hash(s.state))
        result=s.act('pilot:B',command);self.assertTrue(result.ok,result.summary)
        self.until(s,lambda game:len(game.state.delayed_triggers)==2 and not game.state.stack and game.state.pending_decision.kind=='priority')
        for body in (first,returned,black):self.assertEqual('battlefield',s.state.cards[body.object_id].zone)
        self.cast(s,'B',darkness);self.assertIsNone(self.resolve(s))
        self.assertEqual(['B'],s.engine._effective_card_data(s.state.cards[first.object_id])['colors'])
        original=s.state.cards[returned.object_id].logical_object_id
        self.cast(s,'A',blink,[returned.ref]);self.assertIsNone(self.resolve(s))
        self.assertNotEqual(original,s.state.cards[returned.object_id].logical_object_id)
        self.until(s,lambda game:game.state.phase=='combat' and game.state.step=='end_combat' and bool(game.state.stack) and not game.state.pending_trigger_batches)
        self.assertEqual(2,len(s.state.stack));s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
        self.assertEqual('graveyard',s.state.cards[first.object_id].zone)
        self.assertEqual('battlefield',s.state.cards[returned.object_id].zone)
        # Walking Corpse and its blocker trade in combat; no qualified delay was made for it.
        self.assertEqual('graveyard',s.state.cards[black.object_id].zone)
        for seat in 'ABC':self.assertNotIn('qualified-block-private-hand',json.dumps(s.packet('pilot:'+seat,full=True)))
        self.replay(s,load=True)
