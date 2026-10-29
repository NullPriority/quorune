"""Real complete-card compositions sharing existing ordered and leaf owners."""
import json
from pathlib import Path
import tempfile
import unittest
from common import ROOT
import inherited_composition_fixture as actions
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database

class OrderedCompositionFollowupTests(actions.InheritedNativeFixture, unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / 'composition-followup.sqlite3'
        build_fixture_database([ROOT / 'tests/fixtures/bound-effect-program-cards.json', ROOT / 'tests/fixtures/ordered-composition-followup-cards.json'], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Ordered composition followup', [DeckEntry('Isamaru, Hound of Konda', 1, 'commander'), DeckEntry('Generic Bound Plains', 30)], ['Isamaru, Hound of Konda'])

    def tokens(self, s, subtype):
        return [c for c in s.state.cards.values() if c.is_token and c.zone == 'battlefield' and (c.controller == 'A') and (subtype.casefold() in str(s.engine._effective_card_data(c).get('type_line', '')).casefold().split())]

    def private(self, s, hidden_refs=()):
        for seat in 'BCD':
            packet = s.packet('pilot:' + seat, full=True)
            self.assertIsNone(packet['decision'])
            serialized = json.dumps(packet)
            for ref in ('private-composition-hand', *hidden_refs):
                self.assertNotIn(ref, serialized)

    def discard(self, s, refs):
        self.assertEqual('choice.apnap', s.state.pending_decision.kind)
        self.private(s, refs)
        before = authoritative_state_hash(s.state)
        for principal, selected in (('pilot:B', refs), ('pilot:A', []), ('pilot:A', ['forged-discard'] * len(refs))):
            self.assertFalse(s.act(principal, {'action_id': 'choose', 'cards': list(selected)}).ok)
            self.assertEqual(before, authoritative_state_hash(s.state))
        s = self.replay(s, load=True)
        r = s.act('pilot:A', {'action_id': 'choose', 'cards': list(refs)})
        self.assertTrue(r.ok, r.summary)
        return s

    def activate(self, s, source):
        self.priority(s, 'A')
        offered = next((a for a in s.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions'] if a['id'].startswith('activate:' + source.ref + ':')))
        r = s.act('pilot:A', {'action_id': offered['id'], 'pay': 'auto'})
        self.assertTrue(r.ok, r.summary)

    def offered_ids(self, s, source):
        self.priority(s, 'A')
        return {a['id'] for a in s.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions'] if a['id'].startswith('activate:' + source.ref + ':')}

    def test_actual_rapturous_draw_discard_precedes_one_fixed_resolution_mana_bundle(self):
        s = self.session(613653101)
        source = self.add(s.engine, 'Rapturous Moment', zone='hand')
        self.add(s.engine, 'Divination', zone='hand', ref='private-composition-hand')
        self.assert_closed(s, source)
        self.prepare(s, mana={'C': 4, 'U': 1, 'R': 1})
        self.checkpoint(s)
        self.cast(s, 'A', source)
        before = set(s.state.players['A'].zones['hand'])
        self.assertEqual('choice.apnap', self.resolve(s).kind)
        drawn = set(s.state.players['A'].zones['hand']) - before
        self.assertEqual(3, len(drawn))
        self.assertEqual(0, sum(s.state.players['A'].mana_pool.values()))
        refs = [s.state.cards[oid].ref for oid in sorted(drawn)[:2]]
        s = self.discard(s, refs)
        self.settle(s)
        self.assertEqual(len(before) + 1, len(s.state.players['A'].zones['hand']))
        self.assertEqual({'U': 2, 'R': 3}, {c: n for c, n in s.state.players['A'].mana_pool.items() if n})
        self.private(s, [s.state.cards[oid].ref for oid in drawn if s.state.cards[oid].zone == 'hand'])
        self.replay(s, load=True)

    def test_actual_stern_lesson_tapped_powerstone_follows_discard_and_keeps_mana_restriction(self):
        s = self.session(613653110)
        source = self.add(s.engine, 'Stern Lesson', zone='hand')
        untap = self.add(s.engine, 'Twiddle', zone='hand')
        artifact = self.add(s.engine, 'Sol Ring', zone='hand')
        creature = self.add(s.engine, 'Grizzly Bears', zone='hand')
        self.add(s.engine, 'Divination', zone='hand', ref='private-composition-hand')
        self.add(s.engine, 'Island')
        self.assert_closed(s, source)
        self.prepare(s, mana={'C': 2, 'U': 1, 'G': 1})
        self.checkpoint(s)
        self.cast_for(s, 'A', source, reserve={'G': 1})
        before = set(s.state.players['A'].zones['hand'])
        self.assertEqual('choice.apnap', self.resolve(s).kind)
        drawn = set(s.state.players['A'].zones['hand']) - before
        self.assertEqual(2, len(drawn))
        self.assertFalse(self.tokens(s, 'Powerstone'))
        s = self.discard(s, [s.state.cards[sorted(drawn)[0]].ref])
        self.settle(s)
        tokens = self.tokens(s, 'Powerstone')
        self.assertEqual(1, len(tokens))
        powerstone = tokens[0]
        self.assertTrue(powerstone.tapped)
        self.assertFalse(self.offered_ids(s, powerstone))
        self.cast_for(s, 'A', untap, [powerstone.ref], reserve={'G': 1})
        self.assertEqual('semantic.choice', self.resolve(s).kind)
        r = s.act('pilot:A', {'action_id': 'choose', 'choice': 'untap'})
        self.assertTrue(r.ok, r.summary)
        self.settle(s)
        powerstone = s.state.cards[powerstone.object_id]
        self.activate(s, powerstone)
        self.assertEqual(1, s.state.players['A'].mana_pool['C'])
        self.assertEqual(1, s.state.players['A'].stats['restricted_mana']['nonartifact_spell_prohibited']['C'])
        self.priority(s, 'A')
        self.assertFalse(any((a.get('card') == creature.ref for a in s.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions'])))
        self.cast_for(s, 'A', artifact, reserve={'G': 1})
        spell = next((i for i in s.state.stack if i.card_object_id == artifact.object_id))
        self.assertEqual(1, spell.mana_spent_counts.count_for(s.state.cards[artifact.object_id], 'C'))
        self.settle(s)
        self.assertEqual(0, s.state.players['A'].mana_pool['C'])
        self.assertEqual(1, s.state.players['A'].mana_pool['G'])
        self.private(s)
        self.replay(s, load=True)

    def test_actual_madblind_colorless_land_needs_two_red_permanents_and_each_shuffle_is_counterable(self):
        s = self.session(613653120)
        source = self.add(s.engine, 'Madblind Mountain', zone='hand')
        red = self.add(s.engine, 'Raging Goblin', zone='hand')
        self.add(s.engine, 'Fervor')
        untaps = [self.add(s.engine, 'Twiddle', zone='hand', ref='madblind-untap-' + str(i)) for i in range(2)]
        counter = self.add(s.engine, 'Stifle', seat='B', zone='hand')
        self.add(s.engine, 'Island', seat='B')
        self.assert_closed(s, source)
        self.prepare(s, mana={'R': 3, 'U': 2})
        self.checkpoint(s)
        r = s.act('pilot:A', {'action_id': 'play-land:' + source.ref})
        self.assertTrue(r.ok, r.summary)
        self.assertTrue(s.state.cards[source.object_id].tapped)
        self.assertFalse(s.engine._effective_card_data(s.state.cards[source.object_id]).get('colors'))
        self.cast(s, 'A', untaps[0], [source.ref])
        self.assertEqual('semantic.choice', self.resolve(s).kind)
        r = s.act('pilot:A', {'action_id': 'choose', 'choice': 'untap'})
        self.assertTrue(r.ok, r.summary)
        self.settle(s)
        before = self.offered_ids(s, source)
        self.assertEqual(1, len(before))
        self.cast(s, 'A', red)
        self.settle(s)
        after = self.offered_ids(s, source)
        self.assertEqual(1, len(after - before))
        shuffle = next(iter(after - before))
        original = tuple(s.state.players['A'].zones['library'])
        events = sum((e.code == 'library.shuffle' for e in s.state.events))
        r = s.act('pilot:A', {'action_id': shuffle, 'pay': 'auto'})
        self.assertTrue(r.ok, r.summary)
        self.assertTrue(s.state.cards[source.object_id].tapped)
        target = s.state.stack[-1].ref
        self.cast(s, 'B', counter, [target])
        self.resolve_top(s)
        self.settle(s)
        self.assertEqual(original, tuple(s.state.players['A'].zones['library']))
        self.assertEqual(events, sum((e.code == 'library.shuffle' for e in s.state.events)))
        s = self.replay(s, load=True)
        self.cast(s, 'A', untaps[1], [source.ref])
        self.assertEqual('semantic.choice', self.resolve(s).kind)
        r = s.act('pilot:A', {'action_id': 'choose', 'choice': 'untap'})
        self.assertTrue(r.ok, r.summary)
        self.settle(s)
        self.priority(s, 'A')
        self.assertIn(shuffle, self.offered_ids(s, source))
        r = s.act('pilot:A', {'action_id': shuffle, 'pay': 'auto'})
        self.assertTrue(r.ok, r.summary)
        self.assertEqual(0, sum(s.state.players['A'].mana_pool.values()))
        self.settle(s)
        self.assertEqual(events + 1, sum((e.code == 'library.shuffle' for e in s.state.events)))
        self.assertEqual(set(original), set(s.state.players['A'].zones['library']))
        self.replay(s, load=True)

    def test_actual_drag_canal_checks_real_death_then_surveys_privately_before_one_usable_clue(self):
        for died in (False, True):
            with self.subTest(died=died):
                s = self.session(613653130 + died)
                source = self.add(s.engine, 'Drag the Canal', zone='hand')
                self.add(s.engine, 'Divination', zone='hand', ref='private-composition-hand')
                victim = self.add(s.engine, 'Memnite', zone='hand')
                kill = self.add(s.engine, 'Shock', zone='hand')
                self.assert_closed(s, source)
                self.prepare(s, mana={'C': 2, 'U': 1, 'B': 1, **({'R': 1} if died else {})})
                self.checkpoint(s)
                if died:
                    self.cast(s, 'A', victim)
                    self.settle(s)
                    self.cast_for(s, 'A', kill, [victim.ref], reserve={'C': 2, 'U': 1, 'B': 1})
                    self.settle(s)
                    self.assertEqual('graveyard', s.state.cards[victim.object_id].zone)
                self.cast_for(s, 'A', source, reserve={'C': 2})
                if not died:
                    self.settle(s)
                    self.assertEqual(40, s.state.players['A'].life)
                    self.assertEqual(1, len(self.tokens(s, 'Detective')))
                    self.assertFalse(self.tokens(s, 'Clue'))
                    self.replay(s, load=True)
                    continue
                self.assertEqual('semantic.choice', self.resolve(s).kind)
                self.assertEqual(42, s.state.players['A'].life)
                detective = self.tokens(s, 'Detective')
                self.assertEqual(1, len(detective))
                self.assertEqual(2, s.engine._numeric_stat(detective[0].object_id, 'power'))
                self.assertEqual(2, s.engine._numeric_stat(detective[0].object_id, 'toughness'))
                self.assertEqual({'W', 'U'}, set(s.engine._effective_card_data(detective[0]).get('colors', ())))
                self.assertFalse(self.tokens(s, 'Clue'))
                looked = [s.state.cards[oid].ref for oid in reversed(s.state.players['A'].zones['library'][-2:])]
                self.private(s, looked)
                before = authoritative_state_hash(s.state)
                for principal, top, graveyard in (('pilot:B', looked, []), ('pilot:A', [looked[0], looked[0]], [])):
                    self.assertFalse(s.act(principal, {'action_id': 'choose', 'cards': {'top': top, 'graveyard': graveyard}}).ok)
                    self.assertEqual(before, authoritative_state_hash(s.state))
                s = self.replay(s, load=True)
                r = s.act('pilot:A', {'action_id': 'choose', 'cards': {'top': [looked[0]], 'graveyard': [looked[1]]}})
                self.assertTrue(r.ok, r.summary)
                self.settle(s)
                clues = self.tokens(s, 'Clue')
                self.assertEqual(1, len(clues))
                clue = clues[0]
                hand = len(s.state.players['A'].zones['hand'])
                self.activate(s, clue)
                self.assertNotIn(clue.object_id, s.state.players['A'].zones['battlefield'])
                self.assertEqual(hand, len(s.state.players['A'].zones['hand']))
                self.assertFalse(self.offered_ids(s, clue))
                self.settle(s)
                self.assertEqual(hand + 1, len(s.state.players['A'].zones['hand']))
                self.assertFalse(self.tokens(s, 'Clue'))
                self.private(s)
                self.replay(s, load=True)
