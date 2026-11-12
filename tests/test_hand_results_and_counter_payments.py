"""Original Oracle/rulings: ordered whole-hand results and optional exile costs."""
import json
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database


class AdditionalCompositionNativeTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        base = json.loads((ROOT/'tests/fixtures/bound-effect-program-cards.json').read_text(encoding='utf-8'))
        base['cards'] = [row for row in base['cards'] if row['name']=='Generic Bound Plains']
        base['rulings'] = []
        basic = Path(cls.temporary.name)/'basic.json'
        basic.write_text(json.dumps(base),encoding='utf-8')
        path = Path(cls.temporary.name)/'additional-compositions.sqlite3'
        build_fixture_database([basic,ROOT/'tests/fixtures/hand-result-counter-payment-cards.json'],path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Additional compositions',[
            DeckEntry('Sliver Hivelord',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Sliver Hivelord'])

    def assert_private(self, session, private):
        for seat in 'ACD':
            self.assertNotIn(private.ref,json.dumps(session.packet('pilot:'+seat,full=True)))

    def test_actual_redemption_uses_resulting_hand_for_life_including_empty_hand(self):
        for empty in (False,True):
            with self.subTest(empty=empty):
                session = self.session(992100+empty)
                if empty:
                    for oid in tuple(session.state.players['A'].zones['hand']):
                        session.engine.move_card(oid,'library',log=False,semantic_events=False)
                source = self.add(session.engine,'Heartwarming Redemption',zone='hand')
                private = self.add(session.engine,'Counterspell',seat='B',zone='hand',ref='redemption-private')
                self.assert_closed(session,source)
                self.prepare(session,mana={'C':2,'R':1,'W':1});self.checkpoint(session)
                self.cast(session,'A',source)
                discarded = tuple(session.state.players['A'].zones['hand'])
                library = len(session.state.players['A'].zones['library'])
                life = session.state.players['A'].life
                others = {seat:len(session.state.players[seat].zones['hand']) for seat in 'BCD'}
                session = self.replay(session,load=True)
                self.assertIsNone(self.resolve(session))
                self.assertEqual(len(discarded)+1,len(session.state.players['A'].zones['hand']))
                self.assertEqual(library-len(discarded)-1,len(session.state.players['A'].zones['library']))
                self.assertEqual(life+len(discarded)+1,session.state.players['A'].life)
                self.assertTrue(set(discarded)<=set(session.state.players['A'].zones['graveyard']))
                self.assertEqual(others,{seat:len(session.state.players[seat].zones['hand']) for seat in 'BCD'})
                self.assert_private(session,private);self.replay(session,load=True)

    def test_actual_showdown_counts_resolving_hand_and_illegal_target_prevents_all_results(self):
        for invalid in (False,True):
            with self.subTest(invalid=invalid):
                session = self.session(992110+invalid)
                source = self.add(session.engine,'Fateful Showdown',zone='hand')
                target = self.add(session.engine,'Grizzly Bears')
                bounce = self.add(session.engine,'Unsummon',seat='B',zone='hand')
                self.add(session.engine,'Island',seat='B')
                private = self.add(session.engine,'Counterspell',seat='B',zone='hand',ref='showdown-private')
                self.assert_closed(session,source)
                self.prepare(session,mana={'C':2,'R':2});self.checkpoint(session)
                self.cast(session,'A',source,targets=[target.ref if invalid else 'B'])
                hand = tuple(session.state.players['A'].zones['hand'])
                library = len(session.state.players['A'].zones['library']);life = session.state.players['B'].life
                if invalid:
                    self.cast(session,'B',bounce,targets=[target.ref]);self.resolve_top(session)
                    self.assertEqual('hand',session.state.cards[target.object_id].zone)
                session = self.replay(session,load=True)
                self.assertIsNone(self.resolve(session))
                self.assertEqual(life if invalid else life-len(hand),session.state.players['B'].life)
                self.assertEqual(library if invalid else library-len(hand),len(session.state.players['A'].zones['library']))
                if invalid:
                    self.assertEqual(set(hand)|{target.object_id},set(session.state.players['A'].zones['hand']))
                else:
                    self.assertEqual(len(hand),len(session.state.players['A'].zones['hand']))
                    self.assertTrue(set(hand)<=set(session.state.players['A'].zones['graveyard']))
                self.assert_private(session,private);self.replay(session,load=True)

    def upkeep_payment(self, seed, *, harvest, pay):
        session = self.session(seed)
        source = self.add(session.engine,'Forgotten Harvest' if harvest else 'Aegis Sculptor')
        source.owner = 'B'
        target = self.add(session.engine,'Grizzly Bears') if harvest else source
        first = self.add(session.engine,'Forest',zone='graveyard',ref='owned-land-cost');first.controller = 'D'
        second = self.add(session.engine,'Grizzly Bears',zone='graveyard',ref='owned-creature-cost')
        foreign = self.add(session.engine,'Island',seat='B',zone='graveyard')
        private = self.add(session.engine,'Counterspell',seat='B',zone='hand',ref='upkeep-private')
        self.assert_closed(session,source)
        for oid in tuple(session.state.players['D'].zones['hand'])[6:]:
            session.engine.move_card(oid,'library',log=False,semantic_events=False)
        self.prepare(session,actor='D',active='D')
        session.state.current_turn = replace(session.state.current_turn,player='D')
        session.state.last_normal_turn_player = 'D'
        self.checkpoint(session)
        self.until(session,lambda s:s.state.active_player=='A' and (
            bool(s.state.stack) or s.state.pending_decision.kind=='semantic.target'))
        decision = self.resolve(session)
        if harvest:
            self.assertEqual('semantic.target',decision.kind)
            self.target(session,'A',[target.ref]);decision = self.resolve(session)
        self.assertEqual('semantic.choice',decision.kind)
        self.assertEqual(['A'],decision.actors)
        schema = session.packet('pilot:A',full=True)['decision']['ctx']['legal_actions'][0]['choice_schema']
        self.assertIn(first.ref,schema['legal_refs']);self.assertNotIn(foreign.ref,schema['legal_refs'])
        if harvest:self.assertNotIn(second.ref,schema['legal_refs'])
        self.assertFalse(session.state.cards[target.object_id].counters)
        selected = [first.ref] if harvest else [first.ref,second.ref]
        before = authoritative_state_hash(session.state)
        for actor,refs in (('B',selected),('A',[foreign.ref]),('A',[second.ref] if harvest else [first.ref,first.ref])):
            self.assertFalse(session.act('pilot:'+actor,{'action_id':'choose','cards':refs}).ok)
            self.assertEqual(before,authoritative_state_hash(session.state))
        self.assert_private(session,private)
        session = self.replay(session,load=True)
        result = session.act('pilot:A',{'action_id':'choose','cards':selected if pay else []})
        self.assertTrue(result.ok,result.summary);self.assertIsNone(self.resolve(session))
        self.assertEqual(int(pay),session.state.cards[target.object_id].counters.get('+1/+1',0))
        self.assertEqual('exile' if pay else 'graveyard',session.state.cards[first.object_id].zone)
        self.assertEqual('exile' if pay and not harvest else 'graveyard',session.state.cards[second.object_id].zone)
        self.assertEqual('graveyard',session.state.cards[foreign.object_id].zone)
        self.assert_private(session,private);self.replay(session,load=True)

    def test_actual_sculptor_requires_two_owned_distinct_graveyard_cards_or_declines(self):
        for pay in (False,True):
            with self.subTest(pay=pay):self.upkeep_payment(992120+pay,harvest=False,pay=pay)

    def test_actual_harvest_selects_target_before_optional_owned_land_payment(self):
        for pay in (False,True):
            with self.subTest(pay=pay):self.upkeep_payment(992130+pay,harvest=True,pay=pay)
