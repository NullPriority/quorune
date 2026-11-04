"""CR120/603.7: committed creature damage retains its actual recipient."""
import json,tempfile,unittest
from pathlib import Path
from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database


class DealtDamageRecipientNativeTests(InheritedNativeFixture,unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'dealt-damage-recipient.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',
            ROOT/'tests/fixtures/dealt-damage-recipient-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Damaged creatures',[DeckEntry('Isamaru, Hound of Konda',1,'commander'),
            DeckEntry('Generic Bound Plains',14)],['Isamaru, Hound of Konda'])

    def test_actual_combat_retains_damaged_objects_after_source_departure_and_recipient_blink(self):
        s=self.session(120603701)
        viper=self.add(s.engine,'Ohran Viper',ref='actual-combat-only-viper')
        basilisk=self.add(s.engine,'Lowland Basilisk',ref='actual-any-damage-basilisk')
        first=self.add(s.engine,'Wall of Mist',seat='B',ref='actual-original-damaged-wall')
        second=self.add(s.engine,'Wall of Mist',seat='C',ref='actual-returned-damaged-wall')
        spell=self.add(s.engine,'Fall of the Hammer',zone='hand',ref='actual-noncombat-negative')
        source_blink=self.add(s.engine,'Cloudshift',zone='hand',ref='actual-damage-source-blink')
        recipient_blink=self.add(s.engine,'Cloudshift',seat='C',zone='hand',ref='actual-damage-recipient-blink')
        self.add(s.engine,'Plains',ref='source-blink-mana');self.add(s.engine,'Plains',seat='C',ref='recipient-blink-mana')
        self.add(s.engine,'Counterspell',seat='D',zone='hand',ref='dealt-recipient-private-hand')
        for card in (viper,basilisk):card.acquired_control_turn_count=-1
        for card in (viper,basilisk,spell,source_blink):self.assert_closed(s,card)
        self.prepare(s,mana={'C':1,'R':1});self.checkpoint(s)
        self.cast(s,'A',spell,[viper.ref,first.ref]);self.assertIsNone(self.resolve(s))
        self.assertEqual(1,s.state.cards[first.object_id].marked_damage)
        self.assertFalse(s.state.delayed_triggers,'Noncombat damage cannot create the combat-only Viper delay')
        self.attack(s,[(viper,'B'),(basilisk,'C')])
        self.until(s,lambda game:game.state.pending_decision is not None and game.state.pending_decision.kind=='combat.blockers')
        command={'action_id':'block','blocks':{first.ref:viper.ref}};before=authoritative_state_hash(s.state)
        self.assertFalse(s.act('pilot:D',command).ok);self.assertEqual(before,authoritative_state_hash(s.state))
        result=s.act('pilot:B',command);self.assertTrue(result.ok,result.summary)
        result=s.act('pilot:C',{'action_id':'block','blocks':{second.ref:basilisk.ref}});self.assertTrue(result.ok,result.summary)
        self.until(s,lambda game:len(game.state.delayed_triggers)==2 and not game.state.stack and game.state.pending_decision.kind=='priority')
        self.assertEqual((2,1),(s.state.cards[first.object_id].marked_damage,s.state.cards[second.object_id].marked_damage))
        original_source=s.state.cards[viper.object_id].logical_object_id
        original_recipient=s.state.cards[second.object_id].logical_object_id
        self.cast(s,'A',source_blink,[viper.ref]);self.assertIsNone(self.resolve(s))
        self.cast(s,'C',recipient_blink,[second.ref]);self.assertIsNone(self.resolve(s))
        self.assertNotEqual(original_source,s.state.cards[viper.object_id].logical_object_id)
        self.assertNotEqual(original_recipient,s.state.cards[second.object_id].logical_object_id)
        self.until(s,lambda game:game.state.phase=='combat' and game.state.step=='end_combat'
            and bool(game.state.stack) and not game.state.pending_trigger_batches)
        self.assertEqual(2,len(s.state.stack));self.assertEqual((40,40),(s.state.players['B'].life,s.state.players['C'].life))
        self.assertEqual('battlefield',s.state.cards[first.object_id].zone)
        s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
        self.assertEqual('graveyard',s.state.cards[first.object_id].zone)
        self.assertIn(first.object_id,s.state.players['B'].zones['graveyard'])
        for card in (viper,basilisk,second):self.assertEqual('battlefield',s.state.cards[card.object_id].zone)
        self.assertFalse(any(trigger.active for trigger in s.state.delayed_triggers))
        for seat in 'ABC':self.assertNotIn('dealt-recipient-private-hand',json.dumps(s.packet('pilot:'+seat,full=True)))
        self.replay(s,load=True)
