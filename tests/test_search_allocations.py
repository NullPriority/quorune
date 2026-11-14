"""One private search chooses an explicit ordered, replacement-safe allocation."""
from copy import deepcopy
from dataclasses import replace
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import test_recipient_control as recipients
import test_opponent_greater_metrics as search_controls
from inherited_composition_fixture import InheritedNativeFixture
from quorune.compiler.search_allocation_templates import search_allocation_template
from quorune.library_search_model import FixedCountedLibrarySearchTemplate, counted_search_bound_dependencies
from quorune.search_allocation_model import SearchAllocationSpec, SEARCH_ALLOCATION_CAPABILITY as CAP
from quorune.replacement.immutable import freeze_value

CULTIVATE = 'Search your library for up to two basic land cards, reveal those cards, put one onto the battlefield tapped and the other into your hand, then shuffle.'


class SearchAllocationModelTests(unittest.TestCase):
    def test_versioned_allocation_round_trip_frozen_values_and_closed_legacy_forms(self):
        compiled = search_allocation_template(CULTIVATE);effect, = compiled[1]
        spec = SearchAllocationSpec.from_effect(effect)
        self.assertEqual(spec, SearchAllocationSpec.from_effect(freeze_value(effect)))
        self.assertEqual(effect, spec.effect());self.assertEqual(spec.search, FixedCountedLibrarySearchTemplate.from_effect(effect))
        self.assertEqual(('battlefield', 'hand'), spec.destinations(('a', 'b')))
        self.assertEqual(('battlefield',), spec.destinations(('a',)))
        self.assertIn(CAP, counted_search_bound_dependencies(effect))
        for change in ({'schema_version': 2}, {'schema_version': 4}, {'schema_version': 5.0}, {'unknown': True}):
            with self.subTest(change=change), self.assertRaises(ValueError):FixedCountedLibrarySearchTemplate.from_effect({**effect, **change})
        for change in ({'first_count': True}, {'first_count': 0}, {'first_tapped': 1}, {'first_destination': 'exile'}, {'extra': True}):
            changed = deepcopy(effect);changed['allocation'].update(change)
            with self.subTest(change=change), self.assertRaises(ValueError):SearchAllocationSpec.from_effect(changed)

    def test_grammar_keeps_counts_reveal_scope_and_unknown_neighbors(self):
        for text, reveal, first in ((CULTIVATE, True, 'battlefield'),
                ('Search your library for two cards. Put one into your hand and the other into your graveyard. Then shuffle.', False, 'hand'),
                ('Search your library for up to two creature cards and reveal them. Put one into your hand and the other into your graveyard. Then shuffle.', True, 'hand')):
            result = search_allocation_template(text);spec = SearchAllocationSpec.from_effect(result[1][0])
            self.assertEqual((reveal, first), (spec.search.reveal, spec.first_destination))
        for text in (CULTIVATE + ' Draw a card.', CULTIVATE.replace('the other into your hand', 'the other into exile'),
                CULTIVATE.replace('basic land', 'artifact'), CULTIVATE.replace('two basic land', 'X basic land')):
            self.assertIsNone(search_allocation_template(text), text)


class SearchAllocationNativeTests(InheritedNativeFixture, unittest.TestCase):
    setUpClass = classmethod(recipients.RecipientControlNativeTests.setUpClass.__func__)
    scene = recipients.RecipientControlNativeTests.scene
    reject = recipients.RecipientControlNativeTests.reject
    finish = recipients.RecipientControlNativeTests.finish
    activate = recipients.RecipientControlNativeTests.activate
    search = search_controls.OpponentGreaterMetricNativeTests.search

    def board(self, name, *, restricted=False, short=False, commander=False, copy_spell=False):
        extra = {}
        def before(s, source, gift):
            for key, record in (('first', 'Island'), ('second', 'Generic Bound Plains'), ('third', 'Forest'), ('fourth', 'Mountain'),
                    ('gate', 'Azorius Guildgate'), ('nonbasic', 'Tundra'), ('creature', 'Grizzly Bears'), ('creature2', 'Fugitive Wizard'), ('secret', 'Counterspell')):
                extra[key] = self.add(s.engine, record, zone='library', ref='allocated-' + key)
            extra['land_price'] = self.add(s.engine, 'Island', ref='allocation-price-land')
            extra['discard_price'] = self.add(s.engine, 'Grizzly Bears', zone='hand', ref='allocation-price-discard')
            if copy_spell:extra['copy'] = self.add(s.engine, 'Reverberate', zone='hand', ref='allocation-copy')
            if name == 'Navigation Orb':source.owner = 'D'
            if restricted:self.add(s.engine, 'Aven Mindcensor', seat='B')
            if commander:
                card = next(c for c in s.state.cards.values() if c.owner == 'A' and c.is_commander)
                s.state.players['A'].zones[card.zone].remove(card.object_id)
                card.zone = 'library';s.state.players['A'].zones['library'].append(card.object_id);extra['commander'] = card
            if short:
                for oid in tuple(s.state.players['A'].zones['library']):
                    if oid == extra['secret'].object_id:continue
                    s.state.players['A'].zones['library'].remove(oid)
                    c = s.state.cards[oid];c.zone = 'exile';c.known_to = list(s.engine.seats);c.revealed_to = list(s.engine.seats)
                    s.state.players['A'].zones['exile'].append(oid)
        s, source, gift, private = self.scene(name, zone='battlefield' if name in {'Navigation Orb', 'Dreamscape Artist'} else 'hand', before=before)
        extra['creature_price'] = gift
        return s, source, private, extra

    def cast_search(self, s, source, extra, *, kicked=False, alternate=False):
        if source.printed_name in {'Navigation Orb', 'Dreamscape Artist'}:
            fields = {'cost_cards': [extra['discard_price'].ref, extra['land_price'].ref]} if source.printed_name == 'Dreamscape Artist' else {}
            self.activate(s, source, [], **fields)
        else:
            self.priority(s, 'A');row = self.action(s, 'A', source)
            options = row.get('cost_options', ())
            choice = next((o for o in options if (o['id'] == 'kicked') == kicked and o['id'].startswith('plain-alternative-') == alternate), None)
            if alternate:self.assertIsNotNone(choice, options)
            fields = {'cost_option': choice['id']} if choice else ({'cost_option': 'kicked'} if kicked else {})
            if source.printed_name == 'Harrow':fields['sacrifice_cards'] = [extra['land_price'].ref]
            if kicked and source.printed_name == 'Primal Growth':fields['sacrifice_cards'] = [extra['creature_price'].ref]
            if alternate:fields['sacrifice_cards'] = [extra['creature_price'].ref]
            command = {'action_id': row['id'], 'pay': 'auto', **fields};self.reject(s, 'B', command)
            result = s.act('pilot:A', command);self.assertTrue(result.ok, result.summary)
        return self.search(s)

    def submit(self, s, refs):
        command = {'action_id': 'choose', 'search_cards': list(refs)};self.reject(s, 'B', command)
        s = self.replay(s, load=True);before = s.state.players['A'].stats.get('shuffle_count', 0)
        result = s.act('pilot:A', command);self.assertTrue(result.ok, result.summary)
        self.resolve(s)
        self.assertEqual(before + 1, s.state.players['A'].stats.get('shuffle_count', 0))
        return s

    def cultivate(self, name='Cultivate'):
        s, source, private, extra = self.board(name);decision = self.cast_search(s, source, extra)
        action = next(a for a in decision['legal_actions'] if a['id'] == 'choose')
        schema = action['choice_schema'];self.assertEqual('destination_groups', schema['order'])
        form = action['form']
        self.assertTrue(next(f for f in form['fields'] if f['name'] == 'search_cards')['ordered'])
        self.reject(s, 'A', {'action_id': 'choose', 'search_cards': [extra['nonbasic'].ref]})
        s = self.submit(s, [extra['first'].ref, extra['second'].ref])
        a, b = (s.state.cards[extra[k].object_id] for k in ('first', 'second'))
        self.assertEqual(('battlefield', True, 'hand', False), (a.zone, a.tapped, b.zone, b.tapped), 'ordered-search-destinations')
        self.assertIn(b.ref, json.dumps(s.packet('pilot:B', full=True)));self.finish(s, private)

    def test_original_cultivate_reach_and_flare_keep_ordered_revealed_destinations(self):
        for name in ('Cultivate', "Kodama's Reach", 'Flare of Cultivation'):
            with self.subTest(card=name):self.cultivate(name)

    def test_original_hand_graveyard_searches_preserve_qualified_failure_and_private_hand(self):
        for name in ('Fork in the Road', "Jarad's Orders", 'Final Parting'):
            with self.subTest(card=name):
                s, source, private, extra = self.board(name);self.cast_search(s, source, extra)
                keys = ('creature', 'secret') if name == 'Final Parting' else ('creature', 'creature2') if name == "Jarad's Orders" else ('first', 'second')
                refs = [extra[k].ref for k in keys]
                if name == 'Final Parting':self.reject(s, 'A', {'action_id': 'choose', 'search_cards': refs[:1]})
                s = self.submit(s, refs)
                cards = [next(c for c in s.state.cards.values() if c.ref == ref) for ref in refs]
                self.assertEqual(['hand', 'graveyard'], [c.zone for c in cards])
                if name == 'Final Parting':
                    for seat in 'BCD':self.assertNotIn(cards[0].ref, json.dumps(s.packet('pilot:' + seat, full=True)))
                else:self.assertIn(cards[0].ref, json.dumps(s.packet('pilot:B', full=True)))
                self.finish(s, private)

    def test_short_searches_force_first_group_and_empty_qualified_search_still_shuffles(self):
        for found in (0, 1):
            s, source, private, extra = self.board('Cultivate');self.cast_search(s, source, extra)
            s = self.submit(s, [extra['first'].ref] if found else [])
            self.assertEqual('battlefield' if found else 'library', s.state.cards[extra['first'].object_id].zone);self.finish(s, private)
        s, source, private, extra = self.board('Final Parting', short=True);self.cast_search(s, source, extra)
        s = self.submit(s, [extra['secret'].ref]);self.assertEqual('hand', s.state.cards[extra['secret'].object_id].zone);self.finish(s, private)

    def test_original_viewpoint_two_first_slots_and_navigation_gate_source_controller(self):
        for found in (2, 3):
            s, source, private, extra = self.board('Viewpoint Synchronization');self.cast_search(s, source, extra)
            s = self.submit(s, [extra[k].ref for k in ('first', 'second', 'third')[:found]])
            self.assertEqual(['battlefield', 'battlefield'] + (['hand'] if found == 3 else []), [s.state.cards[extra[k].object_id].zone for k in ('first', 'second', 'third')[:found]])
            self.assertTrue(s.state.cards[extra['first'].object_id].tapped);self.assertTrue(s.state.cards[extra['second'].object_id].tapped);self.finish(s, private)
        s, source, private, extra = self.board('Navigation Orb');self.cast_search(s, source, extra)
        s = self.submit(s, [extra['gate'].ref, extra['first'].ref])
        self.assertEqual(('graveyard', 'D'), (s.state.cards[source.object_id].zone, s.state.cards[source.object_id].owner))
        self.assertEqual(('battlefield', 'A'), (s.state.cards[extra['gate'].object_id].zone, s.state.cards[extra['gate'].object_id].controller));self.finish(s, private)

    def test_original_untapped_basic_searches_and_both_kicker_cost_paths(self):
        for name, kicked in (('Harrow', False), ('Grow from the Ashes', False), ('Grow from the Ashes', True), ('Primal Growth', False), ('Primal Growth', True)):
            with self.subTest(card=name, kicked=kicked):
                s, source, private, extra = self.board(name);self.cast_search(s, source, extra, kicked=kicked)
                refs = [extra['first'].ref, extra['second'].ref] if kicked or name in {'Harrow', 'Dreamscape Artist'} else [extra['first'].ref]
                s = self.submit(s, refs)
                for ref in refs:
                    card = next(c for c in s.state.cards.values() if c.ref == ref)
                    self.assertEqual(('battlefield', False), (card.zone, card.tapped))
                self.finish(s, private)

    def test_original_dreamscape_uses_ordered_discard_and_land_price_slots(self):
        s, source, private, extra = self.board('Dreamscape Artist');self.cast_search(s, source, extra)
        self.assertEqual('graveyard', s.state.cards[extra['discard_price'].object_id].zone)
        self.assertEqual('graveyard', s.state.cards[extra['land_price'].object_id].zone)
        s = self.submit(s, [extra['first'].ref, extra['second'].ref])
        for key in ('first', 'second'):
            self.assertEqual(('battlefield', False), (s.state.cards[extra[key].object_id].zone, s.state.cards[extra[key].object_id].tapped))
        self.finish(s, private)

    def test_original_peregrination_shuffles_before_independent_scry(self):
        s, source, private, extra = self.board('Peregrination');self.cast_search(s, source, extra)
        s = self.submit(s, [extra['first'].ref, extra['second'].ref])
        decision = s.packet('pilot:A', full=True)['decision'];schema = next(a for a in decision['legal_actions'] if a['id'] == 'choose')['choice_schema']
        refs = schema['legal_refs'];self.assertEqual(1, len(refs))
        command = {'action_id': 'choose', 'cards': {'top': refs, 'bottom': []}};self.reject(s, 'B', command)
        s = self.replay(s, load=True);result = s.act('pilot:A', command);self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(s));self.finish(s, private)

    def test_original_flare_alternative_price_and_kicked_copy_preserve_search_results(self):
        s, source, private, extra = self.board('Flare of Cultivation')
        self.cast_search(s, source, extra, alternate=True)
        self.assertEqual('graveyard', s.state.cards[extra['creature_price'].object_id].zone)
        s = self.submit(s, [extra['first'].ref, extra['second'].ref]);self.finish(s, private)
        s, source, private, extra = self.board('Grow from the Ashes', copy_spell=True)
        self.priority(s, 'A');row = self.action(s, 'A', source)
        result = s.act('pilot:A', {'action_id': row['id'], 'cost_option': 'kicked', 'pay': 'auto'});self.assertTrue(result.ok, result.summary)
        item = next(item for item in s.state.stack if item.card_object_id == source.object_id)
        self.cast(s, 'A', extra['copy'], [item.ref]);self.search(s)
        s = self.submit(s, [extra['first'].ref, extra['second'].ref])
        self.assertEqual('semantic.search', s.state.pending_decision.kind)
        s = self.submit(s, [extra['third'].ref, extra['fourth'].ref])
        for key in ('first', 'second', 'third', 'fourth'):
            self.assertEqual(('battlefield', False), (s.state.cards[extra[key].object_id].zone, s.state.cards[extra[key].object_id].tapped))
        self.finish(s, private)

    def test_bounded_search_window_and_payload_minimum_remain_authoritative(self):
        from quorune.card_programs.binding import bind_semantic_program_runtime
        s, source, private, extra = self.board('Cultivate', restricted=True);decision = self.cast_search(s, source, extra)
        refs = [r['id'] for r in decision['ctx']['search_cards']];self.assertLessEqual(len(refs), 4)
        outside = next(c.ref for c in s.state.cards.values() if c.zone == 'library' and c.owner == 'A' and c.ref not in refs and c.printed_name == 'Generic Bound Plains')
        self.reject(s, 'A', {'action_id': 'choose', 'search_cards': [outside]})
        self.assertNotIn(outside, json.dumps(s.packet('pilot:A', full=True)))
        p = next(p for p in s.engine.semantics.programs_for_oracle(source.oracle_id) if CAP in p.capability_dependencies)
        deps = tuple(c for c in p.capability_dependencies if c != CAP)
        changed = replace(p, coverage=(), capability_dependencies=deps, capability_closure=self.registry.closure(deps, profile='commander_review').to_dict())
        bound = bind_semantic_program_runtime(changed, capability_registry=self.registry, profile='commander_review')
        self.assertFalse(bound['strict']);self.assertIn('capability:undeclared_runtime_dependency:' + CAP, bound['blockers'])
        s = self.submit(s, refs[:2]);self.finish(s, private)

    def test_commander_destination_replacement_preserves_entire_allocation_after_reload(self):
        s, source, private, extra = self.board('Final Parting', commander=True);self.cast_search(s, source, extra)
        selected = [extra['commander'], extra['creature']]
        result = s.act('pilot:A', {'action_id': 'choose', 'search_cards': [c.ref for c in selected]});self.assertTrue(result.ok, result.summary)
        self.assertEqual('replacement.order', s.state.pending_decision.kind)
        self.assertTrue(all(s.state.cards[c.object_id].zone == 'library' for c in selected))
        s = self.replay(s, load=True);options = s.packet('pilot:A', full=True)['decision']['ctx']['options']
        apply = next(o['id'] for o in options if not o['id'].startswith('decline:'))
        command = {'action_id': 'choose', 'choices': {'replacement': apply}};self.reject(s, 'B', command)
        result = s.act('pilot:A', command);self.assertTrue(result.ok, result.summary);self.assertIsNone(self.resolve(s))
        self.assertEqual(['command', 'graveyard'], [s.state.cards[c.object_id].zone for c in selected]);self.finish(s, private)

    def test_fresh_witness_kills_ignoring_the_submitted_destination_order(self):
        self.cultivate();original = SearchAllocationSpec.destinations
        def backwards(spec, selected):return tuple(reversed(original(spec, selected)))
        with patch.object(SearchAllocationSpec, 'destinations', backwards):
            with self.assertRaisesRegex(AssertionError, 'ordered-search-destinations'):self.cultivate()
