"""Original-card optional result composition witnesses."""
import json
from pathlib import Path
import tempfile
import unittest
from common import ROOT
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
import optional_result_followup_fixture as followups

class OptionalResultFollowupTests(followups.OptionalResultFollowupFixture, unittest.TestCase):

    def test_optional_control_validation_requires_and_preserves_immutable_resolving_provenance(self):
        from dataclasses import replace
        from quorune.semantic_choices.context import SnapshotSemanticChoiceQuery
        from quorune.semantic_choices.model import SemanticChoiceError
        from quorune.semantic_choices.optional_effect import _represented_effect
        from quorune.semantic_runtime.context import SemanticSourceContext
        source = SemanticSourceContext(stack_ref='trigger', object_id='source-id', logical_object_id='source-id@1', card_ref='source')
        query = SnapshotSemanticChoiceQuery(seat_order=('A', 'B'), active_order=('A', 'B'), resolution_source=source)
        effect = {'op': 'gain_control', 'card': 'victim', 'controller': 'A', 'duration': 'until_end_of_turn', 'source': 'source'}
        self.assertIsNone(_represented_effect(effect, actor='A', query=query))
        with self.assertRaises(SemanticChoiceError):
            _represented_effect(effect, actor='A', query=replace(query, resolution_source=None))
        for changed in ({'controller': 'B'}, {'duration': 'unrepresented'}, {'source': None}, {'extra': True}):
            with self.assertRaises(SemanticChoiceError):
                _represented_effect({**effect, **changed}, actor='A', query=query)
        with self.assertRaises(ValueError):
            replace(query, resolution_source={'stack_ref': 'forged'})
        from unittest.mock import patch
        from quorune.semantic_runtime.interpreter import SemanticInterpreter
        original = SemanticInterpreter.lower_for_seats

        def omit_source(interpreter, *args, **kwargs):
            kwargs.pop('source', None)
            return original(interpreter, *args, **kwargs)
        with patch.object(SemanticInterpreter, 'lower_for_seats', omit_source), self.assertRaises(SemanticChoiceError):
            _represented_effect(effect, actor='A', query=query)

    def test_actual_surrakar_optional_counter_then_combat_draw_reads_departed_source_counter_lki(self):
        s = self.session(613655330)
        source = self.add(s.engine, 'Surrakar Spellblade', zone='hand')
        buff = self.add(s.engine, 'Giant Growth', zone='hand')
        shock = self.add(s.engine, 'Shock', zone='hand')
        bounce = self.add(s.engine, 'Unsummon', zone='hand')
        self.add(s.engine, 'Island')
        self.add(s.engine, 'Fervor')
        self.assert_closed(s, source)
        self.ready(s, {'C': 1, 'U': 2, 'G': 1, 'R': 1})
        self.cast_for(s, 'A', source, reserve={'G': 1, 'R': 1})
        self.settle(s)
        self.cast_for(s, 'A', buff, [source.ref], reserve={'R': 1})
        self.assertEqual('semantic.choice', self.resolve(s).kind)
        s = self.optional_counter(s, True)
        self.settle(s)
        self.assertEqual(1, s.state.cards[source.object_id].counters.get('charge', 0))
        self.cast(s, 'A', shock, ['B'])
        self.assertEqual('semantic.choice', self.resolve(s).kind)
        s = self.optional_counter(s, False)
        self.settle(s)
        self.assertEqual(1, s.state.cards[source.object_id].counters.get('charge', 0))
        hand = len(s.state.players['A'].zones['hand'])
        self.attack(s, [(source, 'B')])
        self.until(s, lambda s: self.trigger_count(s, source) == 1)
        self.cast(s, 'A', bounce, [source.ref])
        self.assertEqual('semantic.choice', self.resolve(s).kind)
        s = self.optional_counter(s, False)
        self.resolve_top(s)
        self.assertEqual('hand', s.state.cards[source.object_id].zone)
        self.assertEqual({}, s.state.cards[source.object_id].counters)
        self.assertEqual('semantic.choice', self.resolve(s).kind)
        s = self.optional(s, True)
        self.settle(s)
        self.assertEqual(hand + 1, len(s.state.players['A'].zones['hand']))
        self.replay(s, load=True)

    def test_actual_vicious_shadows_uses_target_current_hand_after_response_without_disclosing_cards(self):
        s = self.session(613655340)
        source = self.add(s.engine, 'Vicious Shadows', zone='hand')
        victim = self.add(s.engine, 'Memnite', seat='D')
        shock = self.add(s.engine, 'Shock', zone='hand')
        draw = self.add(s.engine, 'Divination', seat='B', zone='hand')
        self.add(s.engine, 'Giant Growth', seat='B', zone='hand', ref='shadows-private-1')
        self.add(s.engine, 'Unsummon', seat='B', zone='hand', ref='shadows-private-2')
        self.add(s.engine, 'Vedalken Orrery', seat='B')
        self.assert_closed(s, source)
        self.ready(s, {'C': 6, 'R': 2})
        s.state.players['B'].mana_pool.update({'C': 2, 'U': 1})
        self.checkpoint(s)
        self.cast_for(s, 'A', source, reserve={'R': 1})
        self.settle(s)
        self.cast(s, 'A', shock, [victim.ref])
        self.target(s, 'A', ['B'])
        initial = len(s.state.players['B'].zones['hand'])
        self.cast(s, 'B', draw)
        self.resolve_top(s)
        amount = len(s.state.players['B'].zones['hand'])
        self.assertEqual(initial + 1, amount)
        for seat in 'ACD':
            packet = json.dumps(s.packet('pilot:' + seat, full=True))
            self.assertNotIn('shadows-private-1', packet)
            self.assertNotIn('shadows-private-2', packet)
        life = {seat: s.state.players[seat].life for seat in 'ABCD'}
        self.assertEqual('semantic.choice', self.resolve(s).kind)
        s = self.optional(s, True)
        self.settle(s)
        for seat in 'ABCD':
            self.assertEqual(life[seat] - (amount if seat == 'B' else 0), s.state.players[seat].life)
        self.replay(s, load=True)

    def test_actual_obligator_requires_colorless_and_optional_control_untap_haste_expires(self):
        for scene in ('pay', 'decline', 'no_colorless'):
            with self.subTest(scene=scene):
                s = self.session(613655380 + len(scene))
                source = self.add(s.engine, 'Eldrazi Obligator', zone='hand')
                target = self.add(s.engine, 'Grizzly Bears', seat='B')
                self.assert_closed(s, source)
                mana = {'C': 4, 'R': 1} if scene != 'no_colorless' else {'C': 2, 'R': 1, 'G': 2}
                self.ready(s, mana)
                target.tapped = True
                self.checkpoint(s)
                reserve = {'C': 2} if scene != 'no_colorless' else {'G': 2}
                self.cast_for(s, 'A', source, reserve=reserve)
                self.target(s, 'A', [target.ref])
                self.assertEqual('semantic.choice', self.resolve(s).kind)
                s = self.replay(s, load=True)
                before = authoritative_state_hash(s.state)
                self.assertFalse(s.act('pilot:C', {'action_id': 'choose', 'pay': True}).ok)
                self.assertEqual(before, authoritative_state_hash(s.state))
                if scene == 'no_colorless':
                    self.assertFalse(s.act('pilot:A', {'action_id': 'choose', 'pay': True}).ok)
                    self.assertEqual(before, authoritative_state_hash(s.state))
                r = s.act('pilot:A', {'action_id': 'choose', 'pay': scene == 'pay'})
                self.assertTrue(r.ok, r.summary)
                self.settle(s)
                current = s.state.cards[target.object_id]
                self.assertEqual('A' if scene == 'pay' else 'B', current.controller)
                self.assertEqual(scene != 'pay', current.tapped)
                self.assertEqual(scene == 'pay', 'haste' in s.engine._combat_keywords(current))
                self.assertEqual('battlefield', s.state.cards[source.object_id].zone)
                self.finish_turn(s)
                current = s.state.cards[target.object_id]
                self.assertEqual('B', current.controller)
                self.assertNotIn('haste', s.engine._combat_keywords(current))
                self.replay(s, load=True)
