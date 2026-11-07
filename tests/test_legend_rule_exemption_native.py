"""CR 101.1/611.3/704.5j: current exemption lifetime, normal original admission."""
import json
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
import test_fixed_copy_type_exceptions


class LegendRuleExemptionNativeTests(InheritedNativeFixture, unittest.TestCase):
    choose_copy = test_fixed_copy_type_exceptions.FixedCopyTypeExceptionRuntimeTests.choose_copy

    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / 'legend-exemptions.sqlite3'
        build_fixture_database([
            ROOT / 'tests/fixtures/bound-effect-program-cards.json',
            ROOT / 'tests/fixtures/legend-rule-exemption-cards.json',
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Legend exemption originals', [
            DeckEntry('Generic Bound Commander', 1, 'commander'),
            DeckEntry('Generic Bound Plains', 20),
        ], ['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    def test_original_gallery_cast_copy_and_removal_reopens_current_legend_choice(self):
        s = self.session(704500701)
        gallery = self.add(s.engine, 'Mirror Gallery', zone='hand', ref='original-gallery')
        first = self.add(s.engine, 'Isamaru, Hound of Konda', ref='original-legend')
        clone = self.add(s.engine, 'Clone', zone='hand', ref='original-clone')
        remove = self.add(s.engine, 'Disenchant', zone='hand', ref='remove-gallery')
        private = self.add(s.engine, 'Generic Bound Body', seat='C', zone='hand', ref='legend-exemption-private')
        for card in (gallery, first, clone, remove):
            self.assert_closed(s, card)
        self.prepare(s, mana={'C': 9, 'U': 1, 'W': 1})
        self.checkpoint(s)
        self.cast(s, 'A', gallery)
        self.assertIsNone(self.resolve(s))
        self.assertEqual('battlefield', s.state.cards[gallery.object_id].zone)
        self.cast(s, 'A', clone)
        s = self.choose_copy(s, first)
        self.assertEqual('battlefield', s.state.cards[clone.object_id].zone)
        self.assertEqual('Isamaru, Hound of Konda', s.engine.display_name(clone.object_id))
        self.assertEqual('priority', s.state.pending_decision.kind)
        self.assertTrue(all(s.state.cards[c.object_id].zone == 'battlefield' for c in (first, clone)))
        self.cast(s, 'A', remove, [gallery.ref])
        self.assertEqual('state.legend', self.resolve(s).kind)
        self.assertEqual('graveyard', s.state.cards[gallery.object_id].zone)
        self.assertTrue(all(s.state.cards[c.object_id].zone == 'battlefield' for c in (first, clone)))
        offered = s.state.pending_decision.payload_by_actor['A']['keep_one']
        self.assertEqual({first.ref, clone.ref}, set(offered))
        before = authoritative_state_hash(s.state)
        self.assertFalse(s.act('pilot:B', {'action_id': 'choose', 'card': first.ref}).ok)
        self.assertEqual(before, authoritative_state_hash(s.state))
        s = self.replay(s, load=True)
        result = s.act('pilot:A', {'action_id': 'choose', 'card': first.ref})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual('battlefield', s.state.cards[first.object_id].zone)
        self.assertEqual('graveyard', s.state.cards[clone.object_id].zone)
        self.assertEqual('priority', s.state.pending_decision.kind)
        for viewer in 'ABD':
            self.assertNotIn(private.ref, json.dumps(s.packet('pilot:' + viewer, full=True)))
        self.assertIn(private.ref, json.dumps(s.packet('pilot:C', full=True)))
        self.replay(s, load=True)

    def test_token_exemption_excludes_token_from_nonexempt_keep_choice(self):
        s = self.session(704500702)
        source = self.add(s.engine, 'Generic Token Legend Exemption', ref='token-exemption-source')
        remove = self.add(s.engine, 'Disenchant', zone='hand', ref='remove-token-exemption')
        first = self.add(s.engine, 'Isamaru, Hound of Konda', ref='nonexempt-first')
        second = self.add(s.engine, 'Isamaru, Hound of Konda', ref='nonexempt-second')
        for card in (source, first, second, remove):
            self.assert_closed(s, card)
        token_ref, = s.engine.create_token('A', name='Isamaru, Hound of Konda', copy_of=first.ref,
            reason='Explicit mixed-group owner snapshot; no producing spell claimed')
        token = next(card for card in s.state.cards.values() if card.ref == token_ref)
        self.prepare(s, mana={'C': 1, 'W': 1})
        self.assertEqual('state.legend', s.state.pending_decision.kind)
        self.checkpoint(s)
        self.assertEqual({first.ref, second.ref},
            set(s.state.pending_decision.payload_by_actor['A']['keep_one']))
        before = authoritative_state_hash(s.state)
        self.assertFalse(s.act('pilot:A', {'action_id': 'choose', 'card': token.ref}).ok)
        self.assertEqual(before, authoritative_state_hash(s.state))
        self.assertFalse(s.act('pilot:B', {'action_id': 'choose', 'card': first.ref}).ok)
        self.assertEqual(before, authoritative_state_hash(s.state))
        s = self.replay(s, load=True)
        result = s.act('pilot:A', {'action_id': 'choose', 'card': first.ref})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual('battlefield', s.state.cards[first.object_id].zone)
        self.assertEqual('graveyard', s.state.cards[second.object_id].zone)
        self.assertEqual('battlefield', s.state.cards[token.object_id].zone)
        self.assertEqual('priority', s.state.pending_decision.kind)
        s = self.replay(s, load=True)
        self.cast(s, 'A', remove, [source.ref])
        self.assertEqual('state.legend', self.resolve(s).kind)
        self.assertEqual('graveyard', s.state.cards[source.object_id].zone)
        self.assertEqual({first.ref, token.ref},
            set(s.state.pending_decision.payload_by_actor['A']['keep_one']))
        s = self.replay(s, load=True)
        result = s.act('pilot:A', {'action_id': 'choose', 'card': token.ref})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual('graveyard', s.state.cards[first.object_id].zone)
        self.assertEqual('battlefield', s.state.cards[token.object_id].zone)
        self.replay(s, load=True)

    def test_subtype_exemption_ends_when_source_loses_its_current_ability(self):
        s = self.session(704500703)
        source = self.add(s.engine, 'Generic Spider Legend Exemption', ref='spider-rule-source')
        first = self.add(s.engine, 'Isamaru, Hound of Konda', ref='current-spider-first')
        second = self.add(s.engine, 'Isamaru, Hound of Konda', ref='current-spider-second')
        remove = self.add(s.engine, 'Dress Down', zone='hand', ref='remove-source-abilities')
        for card in (source, first, second, remove):
            self.assert_closed(s, card)
        # Public current-characteristic owner snapshot. No producing type-change
        # spell is claimed; the later Dress Down cast and keep are actual commands.
        for card in (first, second):
            card.annotations.setdefault('copy_overrides', {}).update(
                {'type_line': 'Legendary Creature — Spider', 'power': '2', 'toughness': '2'})
        self.prepare(s, mana={'C': 1, 'U': 1})
        self.assertEqual('priority', s.state.pending_decision.kind)
        self.assertTrue(all(s.state.cards[c.object_id].zone == 'battlefield' for c in (first, second)))
        self.checkpoint(s)
        self.cast(s, 'A', remove)
        self.assertEqual('state.legend', self.resolve(s).kind)
        self.assertEqual('battlefield', s.state.cards[source.object_id].zone)
        self.assertEqual({first.ref, second.ref},
            set(s.state.pending_decision.payload_by_actor['A']['keep_one']))
        s = self.replay(s, load=True)
        result = s.act('pilot:A', {'action_id': 'choose', 'card': first.ref})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual('battlefield', s.state.cards[first.object_id].zone)
        self.assertEqual('graveyard', s.state.cards[second.object_id].zone)
        self.replay(s, load=True)

    def test_qualified_source_uses_current_controller_after_actual_control_magic(self):
        s = self.session(704500704)
        source = self.add(s.engine, 'Generic Spider Legend Exemption', ref='changing-rule-source')
        first = self.add(s.engine, 'Isamaru, Hound of Konda', ref='controlled-spider-first')
        second = self.add(s.engine, 'Isamaru, Hound of Konda', ref='controlled-spider-second')
        theft = self.add(s.engine, 'Control Magic', seat='B', zone='hand', ref='actual-rule-source-theft')
        for card in (source, first, second, theft):
            self.assert_closed(s, card)
        for card in (first, second):
            card.annotations.setdefault('copy_overrides', {}).update(
                {'type_line': 'Legendary Creature — Spider', 'power': '2', 'toughness': '2'})
        self.prepare(s, actor='B', active='B', mana={'C': 2, 'U': 2})
        self.assertEqual('priority', s.state.pending_decision.kind)
        self.checkpoint(s)
        self.cast(s, 'B', theft, [source.ref])
        self.assertEqual('state.legend', self.resolve(s).kind)
        self.assertEqual('A', s.state.cards[source.object_id].owner)
        self.assertEqual('B', s.state.cards[source.object_id].controller)
        self.assertEqual(['A'], s.state.pending_decision.actors)
        self.assertEqual({first.ref, second.ref},
            set(s.state.pending_decision.payload_by_actor['A']['keep_one']))
        before = authoritative_state_hash(s.state)
        self.assertFalse(s.act('pilot:B', {'action_id': 'choose', 'card': first.ref}).ok)
        self.assertEqual(before, authoritative_state_hash(s.state))
        s = self.replay(s, load=True)
        result = s.act('pilot:A', {'action_id': 'choose', 'card': first.ref})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual('battlefield', s.state.cards[first.object_id].zone)
        self.assertEqual('graveyard', s.state.cards[second.object_id].zone)
        self.assertEqual('priority', s.state.pending_decision.kind)
        self.assertEqual('B', s.state.priority_player)
        self.replay(s, load=True)
