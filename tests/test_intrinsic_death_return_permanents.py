"""CR 702.79a/702.93a do not require a creature at graveyard departure."""

from pathlib import Path
import json
import tempfile
import unittest

from common import ROOT
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
import test_ability_source_counter_targets as native


class IntrinsicPermanentDeathReturnTests(unittest.TestCase):
    register_fixture_cards_on_add = True
    session = native.AbilitySourceCounterRuntimeTests.session
    add = native.AbilitySourceCounterRuntimeTests.add
    prepare = native.AbilitySourceCounterRuntimeTests.prepare
    checkpoint = native.AbilitySourceCounterRuntimeTests.checkpoint
    priority = native.AbilitySourceCounterRuntimeTests.priority
    actions = native.AbilitySourceCounterRuntimeTests.actions
    cast = native.AbilitySourceCounterRuntimeTests.cast
    binding = native.AbilitySourceCounterRuntimeTests.binding
    reject = native.AbilitySourceCounterRuntimeTests.reject
    settle = native.AbilitySourceCounterRuntimeTests.settle
    resume = native.AbilitySourceCounterRuntimeTests.resume
    replay = native.AbilitySourceCounterRuntimeTests.replay
    private_replay = native.AbilitySourceCounterRuntimeTests.private_replay

    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name)/'intrinsic-permanent-return.sqlite3'
        base = json.loads((ROOT/'tests/fixtures/bound-effect-program-cards.json').read_text(encoding='utf-8'))
        base['cards'] = [card for card in base['cards'] if card['name'] in {
            'Generic Bound Commander', 'Generic Bound Plains', 'Generic Bound Growth'}]
        base['rulings'] = []
        base_path = Path(cls.temporary.name)/'synthetic-deck-and-private-control.json'
        base_path.write_text(json.dumps(base, ensure_ascii=False), encoding='utf-8', newline='\n')
        build_fixture_database([base_path,
            ROOT/'tests/fixtures/intrinsic-permanent-death-return-cards.json'], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Intrinsic permanent return', [
            DeckEntry('Generic Bound Commander', 1, 'commander'), DeckEntry('Generic Bound Plains', 30)],
            ['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    def return_trigger(self, session, source):
        for _ in range(64):
            matches = [item for item in session.state.stack if item.kind == 'triggered_ability'
                and item.source_object_id == source.object_id]
            if matches:
                self.assertEqual(1, len(matches))
                return matches[0]
            decision = session.state.pending_decision
            if decision is None:
                session.engine.pump()
                continue
            self.assertEqual('priority', decision.kind)
            result = session.act(session.pending_principals()[0], {'action_id': 'pass'})
            self.assertTrue(result.ok, result.summary)
        self.fail('The intrinsic permanent-return ability did not trigger')

    def test_original_keywords_keep_departure_counter_condition_owner_return_and_replay(self):
        cases = (('Young Wolf', 'undying', '+1/+1', 2), ('Putrid Goblin', 'persist', '-1/-1', 1))
        for offset, (name, keyword, counter, expected_stat) in enumerate(cases):
            for prohibited in (False, True):
                with self.subTest(original=name, prohibited_counter=prohibited):
                    session = self.session(977_500 + offset * 2 + prohibited)
                    bearer = self.add(session.engine, name, seat='B')
                    session.engine.change_control(bearer.object_id, 'C', reason='Independent owner/controller distinction')
                    if prohibited:
                        bearer.counters[counter] = 1
                    change = self.add(session.engine, 'One with the Stars', zone='hand')
                    removal = self.add(session.engine, 'Naturalize', seat='D', zone='hand')
                    private = self.add(session.engine, 'Generic Bound Growth', seat='C', zone='hand')
                    for card in (bearer, change, removal):
                        bound = self.binding(session, card)
                        self.assertTrue(bound['strict_capability_ready'], bound['blockers'])
                    self.prepare(session, mana={'U': 1, 'C': 3})
                    session.state.players['D'].mana_pool.update(G=1, C=1)
                    self.checkpoint(session)
                    self.cast(session, change, (bearer.ref,))
                    self.assertIsNone(self.settle(session))
                    effective = session.engine._effective_card_data(session.state.cards[bearer.object_id])
                    self.assertNotIn('Creature', effective['type_line'])
                    self.assertIn(keyword, {word.casefold() for word in effective['keywords']})
                    self.cast(session, removal, (bearer.ref,), seat='D')
                    if prohibited:
                        self.assertIsNone(self.settle(session))
                        self.assertEqual('graveyard', session.state.cards[bearer.object_id].zone)
                        self.private_replay(session, private)
                        continue
                    trigger = self.return_trigger(session, bearer)
                    self.assertEqual('C', trigger.controller)
                    self.assertEqual('graveyard', session.state.cards[bearer.object_id].zone)
                    session = self.resume(session)
                    self.assertIsNone(self.settle(session))
                    returned = session.state.cards[bearer.object_id]
                    self.assertEqual(('battlefield', 'B', 'B'), (returned.zone, returned.owner, returned.controller))
                    self.assertEqual({counter: 1}, returned.counters)
                    self.assertEqual((expected_stat, expected_stat), (
                        session.engine._numeric_stat(returned.object_id, 'power'),
                        session.engine._numeric_stat(returned.object_id, 'toughness')))
                    placement = next(event for event in reversed(session.state.events)
                        if event.code == 'counter.add' and event.details.get('object') == returned.ref)
                    self.assertEqual('B', placement.actor)
                    self.assertEqual('graveyard', session.state.cards[change.object_id].zone)
                    self.private_replay(session, private)
