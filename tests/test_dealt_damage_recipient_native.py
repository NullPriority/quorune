"""CR120/603.7: committed creature damage retains its actual recipient."""
import json,tempfile,unittest
from pathlib import Path
from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.tap_state import NEXT_UNTAP_PROHIBITION_ANNOTATION
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

    def test_combat_next_untap_uses_the_original_damaged_creature_after_responses(self):
        session = self.session(120603702)
        warrior = self.add(session.engine, 'Kashi-Tribe Warriors', ref='untap-damage-warrior')
        wall = self.add(session.engine, 'Wall of Mist', seat='B', ref='untap-damage-wall')
        noncombat = self.add(session.engine, 'Fall of the Hammer', zone='hand', ref='untap-damage-noncombat')
        source_blink = self.add(session.engine, 'Cloudshift', zone='hand', ref='untap-damage-source-blink')
        recipient_blink = self.add(session.engine, 'Cloudshift', seat='B', zone='hand', ref='untap-damage-recipient-blink')
        self.add(session.engine, 'Plains', ref='untap-damage-source-mana')
        self.add(session.engine, 'Plains', seat='B', ref='untap-damage-recipient-mana')
        warrior.acquired_control_turn_count = -1
        self.assert_closed(session, warrior)
        self.prepare(session, mana={'C': 1, 'R': 1})
        self.checkpoint(session)

        self.cast(session, 'A', noncombat, [warrior.ref, wall.ref])
        self.resolve_top(session)
        self.assertGreater(session.state.cards[wall.object_id].marked_damage, 0)
        self.assertFalse(session.state.stack)
        self.assertNotIn(NEXT_UNTAP_PROHIBITION_ANNOTATION, session.state.cards[wall.object_id].annotations)

        self.attack(session, [(warrior, 'B')])
        self.until(session, lambda game: game.state.pending_decision.kind == 'combat.blockers')
        result = session.act('pilot:B', {'action_id': 'block', 'blocks': {wall.ref: warrior.ref}})
        self.assertTrue(result.ok, result.summary)
        self.until(session, lambda game: bool(game.state.stack) and game.state.pending_decision.kind == 'priority')
        item, = session.state.stack
        original_wall = session.state.cards[wall.object_id].logical_object_id
        original_warrior = session.state.cards[warrior.object_id].logical_object_id
        self.assertFalse(item.targets)
        self.assertEqual({'reference': wall.ref, 'logical_object_id': original_wall},
                         item.context['damage_permanent_recipient'])

        returned = self.replay(session, load=True)
        self.cast(session, 'A', source_blink, [warrior.ref])
        self.resolve_top(session)
        self.resolve_top(session)
        self.assertNotEqual(original_warrior, session.state.cards[warrior.object_id].logical_object_id)
        self.assertTrue(session.state.cards[wall.object_id].tapped)
        self.assertIs(session.state.cards[wall.object_id].annotations[NEXT_UNTAP_PROHIBITION_ANNOTATION], True)
        self.assertNotIn(NEXT_UNTAP_PROHIBITION_ANNOTATION, session.state.cards[warrior.object_id].annotations)
        self.until(session, lambda game: game.state.active_player == 'B' and game.state.step == 'upkeep')
        self.assertTrue(session.state.cards[wall.object_id].tapped)
        self.assertNotIn(NEXT_UNTAP_PROHIBITION_ANNOTATION, session.state.cards[wall.object_id].annotations)
        self.replay(session, load=True)

        self.cast(returned, 'B', recipient_blink, [wall.ref])
        self.resolve_top(returned)
        self.resolve_top(returned)
        new_wall = returned.state.cards[wall.object_id]
        self.assertNotEqual(original_wall, new_wall.logical_object_id)
        self.assertFalse(new_wall.tapped)
        self.assertNotIn(NEXT_UNTAP_PROHIBITION_ANNOTATION, new_wall.annotations)
        self.replay(returned, load=True)

    def test_noncombat_next_untap_marks_an_already_tapped_survivor_and_skips_lethal_damage(self):
        for index, (name, survives) in enumerate((('Wind Drake', True), ('Suntail Hawk', False))):
            with self.subTest(recipient=name):
                session = self.session(120603703 + index)
                sniper = self.add(session.engine, 'Matsu-Tribe Sniper', ref='untap-damage-sniper')
                flyer = self.add(session.engine, name, seat='B', ref='untap-damage-flyer')
                sniper.acquired_control_turn_count = -1
                flyer.tapped = True
                original_flyer = flyer.logical_object_id
                self.assert_closed(session, sniper)
                self.prepare(session)
                self.checkpoint(session)
                offered = next(row for row in session.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']
                               if row['id'].startswith('activate:' + sniper.ref + ':'))
                result = session.act('pilot:A', {'action_id': offered['id'], 'targets': [flyer.ref], 'pay': 'auto'})
                self.assertTrue(result.ok, result.summary)
                self.resolve_top(session)
                item, = session.state.stack
                self.assertFalse(item.targets)
                self.assertEqual({'reference': flyer.ref, 'logical_object_id': original_flyer},
                                 item.context['damage_permanent_recipient'])
                session = self.replay(session, load=True)
                self.resolve_top(session)
                recipient = session.state.cards[flyer.object_id]
                self.assertEqual('battlefield' if survives else 'graveyard', recipient.zone)
                if survives:
                    self.assertTrue(recipient.tapped)
                    self.assertIs(recipient.annotations[NEXT_UNTAP_PROHIBITION_ANNOTATION], True)
                else:
                    self.assertNotIn(NEXT_UNTAP_PROHIBITION_ANNOTATION, recipient.annotations)
                self.replay(session, load=True)
