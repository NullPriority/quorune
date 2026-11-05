"""Actual commands for restored scalar, reflexive and regeneration compositions."""
import json,tempfile,unittest
from pathlib import Path
from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database


class BatchAdmissionRetentionNativeTests(InheritedNativeFixture,unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'batch-admission-retention.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',
            ROOT/'tests/fixtures/batch-admission-retention-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Batch admission retention',[DeckEntry('Isamaru, Hound of Konda',1,'commander'),
            DeckEntry('Generic Bound Plains',20)],['Isamaru, Hound of Konda'])

    def load_pending(self,s):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'retention-pending';s.save(path);loaded=type(s).load(self.db,path)
            self.assertEqual(authoritative_state_hash(s.state),authoritative_state_hash(loaded.state));return loaded

    def assert_private(self,s,card,owner):
        for seat in 'ABCD':
            if seat!=owner:self.assertNotIn(card.ref,json.dumps(s.packet('pilot:'+seat,full=True)))

    def test_actual_monumental_quantity_resolves_after_response_and_binds_private_draw_and_loss(self):
        s=self.session(111400501)
        spell=self.add(s.engine,'Monumental Corruption',zone='hand')
        ring=self.add(s.engine,'Sol Ring');self.add(s.engine,'Memnite')
        response=self.add(s.engine,'Disenchant',seat='B',zone='hand')
        private=self.add(s.engine,'Counterspell',seat='C',zone='hand')
        self.add(s.engine,'Plains',seat='B');self.add(s.engine,'Wastes',seat='B')
        for card in (spell,response,ring):self.assert_closed(s,card)
        self.prepare(s,mana={'C':3,'B':2});self.checkpoint(s)
        before_hand=set(s.state.players['B'].zones['hand'])
        self.cast(s,'A',spell,['B']);self.cast(s,'B',response,[ring.ref]);self.resolve_top(s)
        self.assertEqual('graveyard',s.state.cards[ring.object_id].zone);s=self.load_pending(s)
        self.assertIsNone(self.resolve(s))
        drawn=set(s.state.players['B'].zones['hand'])-before_hand
        self.assertEqual(1,len(drawn));self.assertEqual([40,39,40,40],[s.state.players[seat].life for seat in 'ABCD'])
        self.assert_private(s,private,'C')
        for oid in drawn:self.assert_private(s,s.state.cards[oid],'B')
        self.replay(s)

    def test_actual_contractor_can_pay_itself_then_choose_and_resolve_the_separate_player_result(self):
        s=self.session(111400502)
        source=self.add(s.engine,'Unscrupulous Contractor',zone='hand')
        private=self.add(s.engine,'Counterspell',seat='C',zone='hand');self.assert_closed(s,source)
        self.prepare(s,mana={'C':3,'B':1});self.checkpoint(s);self.cast(s,'A',source)
        decision=self.resolve(s);self.assertEqual('semantic.choice',decision.kind)
        identity=s.state.cards[source.object_id].logical_object_id
        hands={seat:len(s.state.players[seat].zones['hand']) for seat in 'ABCD'}
        command={'action_id':'choose','cards':[source.ref]};before=authoritative_state_hash(s.state)
        self.assertFalse(s.act('pilot:D',command).ok);self.assertEqual(before,authoritative_state_hash(s.state))
        accepted=s.act('pilot:A',command);self.assertTrue(accepted.ok,accepted.summary)
        self.assertEqual('graveyard',s.state.cards[source.object_id].zone)
        self.assertEqual('semantic.target',s.state.pending_decision.kind)
        self.assertEqual('A',s.state.stack[-1].controller)
        self.assertEqual(identity,s.state.stack[-1].context['source_logical_object_id'])
        s=self.load_pending(s);self.target(s,'A',['B']);self.assertIsNone(self.resolve(s))
        self.assertEqual(hands['B']+2,len(s.state.players['B'].zones['hand']))
        self.assertEqual([40,38,40,40],[s.state.players[seat].life for seat in 'ABCD'])
        self.assert_private(s,private,'C');self.replay(s)

    def test_actual_necrobite_foreign_attacker_kills_larger_blocker_and_regenerates_through_combat(self):
        s=self.session(111400503)
        attacker=self.add(s.engine,'Grizzly Bears',seat='B');attacker.acquired_control_turn_count=-1
        blocker=self.add(s.engine,'Hill Giant')
        spell=self.add(s.engine,'Necrobite',zone='hand');private=self.add(s.engine,'Counterspell',seat='C',zone='hand')
        self.add(s.engine,'Swamp');self.add(s.engine,'Plains',ref='necrobite-plains-one');self.add(s.engine,'Plains',ref='necrobite-plains-two')
        for card in (attacker,blocker,spell):self.assert_closed(s,card)
        self.prepare(s,actor='B',active='B');self.checkpoint(s)
        self.until(s,lambda game:game.state.pending_decision is not None and game.state.pending_decision.kind=='combat.attackers')
        accepted=s.act('pilot:B',{'action_id':'attack','attackers':{attacker.ref:'A'}});self.assertTrue(accepted.ok,accepted.summary)
        self.until(s,lambda game:game.state.pending_decision is not None and game.state.pending_decision.kind=='combat.blockers')
        accepted=s.act('pilot:A',{'action_id':'block','blocks':{blocker.ref:attacker.ref}});self.assertTrue(accepted.ok,accepted.summary)
        self.cast(s,'A',spell,[attacker.ref]);self.assertIsNone(self.resolve(s))
        self.assertIn('deathtouch',{word.casefold() for word in s.engine._combat_keywords(s.state.cards[attacker.object_id])})
        self.until(s,lambda game:game.state.phase=='postcombat_main')
        body=s.state.cards[attacker.object_id]
        self.assertEqual('battlefield',body.zone);self.assertEqual('B',body.controller)
        self.assertTrue(body.tapped);self.assertEqual(0,body.marked_damage);self.assertIsNone(body.attacking)
        self.assertEqual('graveyard',s.state.cards[blocker.object_id].zone)
        self.assertEqual([40,40,40,40],[s.state.players[seat].life for seat in 'ABCD'])
        self.assert_private(s,private,'C');self.replay(s)
