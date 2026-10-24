from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.compiler.activated_ability_catalog import with_activated_ability_catalog
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.semantics import SemanticRegistry
from quorune.session import CommanderSession
from scripts.build_test_database import build_fixture_database
import test_static_player_restrictions as controls


NAMES = ('Wight of the Reliquary', 'Elvish Reclaimer')


class LandSearchViewEquivalenceTests(unittest.TestCase):
    register_fixture_cards_on_add = True
    session = controls.StaticPlayerRestrictionRuntimeTests.session
    add = controls.StaticPlayerRestrictionRuntimeTests.add
    prepare = controls.StaticPlayerRestrictionRuntimeTests.prepare
    checkpoint = controls.StaticPlayerRestrictionRuntimeTests.checkpoint
    resolve = controls.StaticPlayerRestrictionRuntimeTests.resolve
    replay = controls.StaticPlayerRestrictionRuntimeTests.replay
    private_replay = controls.StaticPlayerRestrictionRuntimeTests.private_replay
    priority = controls.StaticPlayerRestrictionRuntimeTests.priority
    actions = controls.StaticPlayerRestrictionRuntimeTests.actions
    cast = controls.StaticPlayerRestrictionRuntimeTests.cast
    reject = controls.StaticPlayerRestrictionRuntimeTests.reject
    binding = controls.StaticPlayerRestrictionRuntimeTests.binding

    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        path = Path(cls.temp.name) / 'land-search-views.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',
            ROOT/'tests/fixtures/land-search-view-cards.json'], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Owned land search views', [DeckEntry('Generic Bound Commander', 1, 'commander'),
            DeckEntry('Generic Bound Plains', 30)], ['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temp.cleanup()

    def hidden(self, s, name, *, seat='A', ref=None):
        card = self.add(s.engine, name, seat=seat, zone='library', ref=ref)
        zone = s.state.players[seat].zones['library']
        zone.remove(card.object_id)
        zone.insert(0, card.object_id)
        return card

    def activation_command(self, s, source, *, costs=(), seat='A', targets=()):
        self.priority(s, seat)
        row = next((r for r in self.actions(s, seat) if r['id'].startswith('activate:'+source.ref+':')), None)
        self.assertIsNotNone(row, source.printed_name)
        return {'action_id': row['id'], 'pay': 'auto', 'cost_cards': [c.ref for c in costs], 'targets': list(targets)}

    def activate(self, s, source, *, costs=(), seat='A', targets=()):
        command = self.activation_command(s, source, costs=costs, seat=seat, targets=targets)
        self.reject(s, command, seat='D')
        result = s.act('pilot:'+seat, command)
        self.assertTrue(result.ok, result.summary)

    def resume(self, s):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'pending-search'
            s.save(path)
            resumed = CommanderSession.load(self.db, path)
            self.assertEqual(authoritative_state_hash(s.state), authoritative_state_hash(resumed.state))
            return resumed

    def finish_search(self, s, land, *, seat='A', fail=False, excluded=()):
        decision = self.resolve(s)
        self.assertIsNotNone(decision)
        self.assertEqual('semantic.search', decision.kind)
        s = self.resume(s)
        packet = s.packet('pilot:'+seat, full=True)
        options = {c['id'] for c in packet['decision']['ctx']['search_cards']}
        self.assertIn(land.ref, options)
        for card in excluded:
            self.assertNotIn(card.ref, options)
            self.reject(s, {'action_id': 'choose', 'search_cards': [card.ref]}, seat=seat)
        schema = packet['decision']['legal_actions'][0]['choice_schema']
        self.assertEqual(0, schema['minimum'])
        self.assertTrue(schema['rules_may_fail_to_find'])
        for other in 'ABCD':
            if other != seat:
                rendered = json.dumps(s.packet('pilot:'+other, full=True))
                self.assertNotIn(land.ref, rendered)
        before = tuple(s.state.players[seat].zones['library'])
        command = {'action_id': 'choose', 'search_cards': [] if fail else [land.ref]}
        self.reject(s, command, seat='D')
        result = s.act('pilot:'+seat, command)
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(s))
        after = tuple(s.state.players[seat].zones['library'])
        self.assertEqual(set(before) if fail else set(before)-{land.object_id}, set(after))
        self.assertNotEqual(tuple(c for c in before if fail or c != land.object_id), after)
        found = s.state.cards[land.object_id]
        self.assertEqual('library' if fail else 'battlefield', found.zone)
        if not fail:
            self.assertTrue(found.tapped)
            self.assertEqual((seat, seat), (found.owner, found.controller))
            for other in 'ABCD':
                self.assertIn(land.ref, json.dumps(s.packet('pilot:'+other, full=True)))
        return s

    def views(self, name, seed):
        s = self.session(seed)
        source = self.add(s.engine, name, zone='hand')
        record = self.db.lookup(name)
        current = s.engine.semantics.programs_for_oracle(source.oracle_id)
        old = tuple(with_activated_ability_catalog(record, SemanticRegistry().programs_for_oracle(source.oracle_id), reference_programs=current))
        return s, source, record, current, old

    def test_originals_normally_bind_complete_current_search_and_exact_price(self):
        from quorune.card_programs.activation_view_equivalence import shadowed_reviewed_activation_view_keys as shadow
        from quorune.card_programs.battlefield_search_equivalence import fixed_battlefield_search_results_equal
        for i, name in enumerate(NAMES):
            s, source, record, current, old = self.views(name, 825000+i)
            bound = self.binding(s, source)
            self.assertTrue(bound['strict_capability_ready'], bound['blockers'])
            keys = shadow(record, current, old, capability_registry=self.registry, capability_profile='commander_review')
            self.assertEqual(1, len(keys))
            key = next(iter(keys))
            now, previous = next(p for p in current if p.key == key), next(p for p in old if p.key == key)
            self.assertTrue(fixed_battlefield_search_results_equal(now, previous))
            self.assertTrue(str(now.provenance['authored_by']).startswith('oracle-ir-v'))
            self.assertIsNotNone(now.cost_schema)
            self.assertTrue(all(p.trust_level == 'trusted' and not p.requires_arbiter for p in current))

    def test_typed_result_defaults_preserve_all_other_fields_and_inputs(self):
        from quorune.card_programs.activation_view_equivalence import shadowed_reviewed_activation_view_keys as shadow
        for i, name in enumerate(NAMES):
            _, _, record, current, old = self.views(name, 825010+i)
            def check(g=current, r=old):
                return shadow(record, g, r, capability_registry=self.registry, capability_profile='commander_review')
            keys = check()
            self.assertEqual(1, len(keys))
            key = next(iter(keys))
            previous = next(p for p in old if p.key == key)
            before = json.dumps([p.to_dict() for p in (*current, *old)], sort_keys=True)
            base = previous.effects[0]
            changes = [{'searching_player': 'B'}, {'reveal': True}, {'shuffle_after': False},
                {'enters_tapped_override': False}, {'destination': 'hand'}, {'future': True},
                {'count': {'minimum': 0, 'maximum': 1}}, {'count': {'minimum': 1, 'maximum': 2}},
                {'count': {'minimum': True, 'maximum': 1}}, {'selector': {'types': ['Creature']}},
                {'selector': {'types': ['Land'], 'supertypes': ['Basic']}}, {'selector': {'colors_any': ['G']}},
                {'selector': {'types': ['Land'], 'future': []}}]
            for change in changes:
                self.assertNotIn(key, check(r=[replace(previous, effects=[{**deepcopy(base), **change}])]), change)
            for required in ('shuffle_after', 'enters_tapped_override', 'count', 'selector'):
                effect = deepcopy(base)
                effect.pop(required)
                self.assertNotIn(key, check(r=[replace(previous, effects=[effect])]))
            wrong = deepcopy(next(p for p in current if p.key == key).cost_schema)
            wrong['tap'] = False
            self.assertNotIn(key, check(r=[replace(previous, cost_schema=wrong)]))
            self.assertNotIn(key, check(r=[replace(previous, target_schema={'count': 1})]))
            self.assertFalse(check(r=[replace(previous, provenance={**previous.provenance, 'source_oracle_hash': '0'*64})]))
            self.assertFalse(check(g=[replace(p, trust_level='provisional') for p in current]))
            self.assertFalse(check(g=[*current, *current]))
            self.assertEqual(before, json.dumps([p.to_dict() for p in (*current, *old)], sort_keys=True))

    def test_real_wight_paid_other_creature_cost_and_private_tapped_land_search(self):
        s = self.session(825020)
        source = self.add(s.engine, NAMES[0], zone='hand')
        haste = self.add(s.engine, 'Expedite', zone='hand')
        bounce = self.add(s.engine, 'Unsummon', seat='B', zone='hand')
        price = self.add(s.engine, 'Fugitive Wizard', ref='wight-own-price')
        foreign = self.add(s.engine, 'Fugitive Wizard', seat='B', ref='wight-foreign-price')
        self.add(s.engine, 'Walking Corpse', seat='B', zone='graveyard', ref='wight-foreign-grave')
        land = self.hidden(s, 'Forest', ref='wight-own-private-land')
        nonland = self.hidden(s, 'Sol Ring', ref='wight-own-private-artifact')
        foreign_land = self.hidden(s, 'Island', seat='B', ref='wight-foreign-private-land')
        private = self.add(s.engine, 'Generic Bound Growth', seat='C', zone='hand')
        s.state.players['B'].mana_pool['U'] = 1
        self.prepare(s, mana={'B': 1, 'G': 1, 'R': 1})
        self.checkpoint(s)
        self.cast(s, source, payment={'B': 1, 'G': 1})
        self.assertIsNone(self.resolve(s))
        self.assertEqual(2, s.engine._numeric_stat(source.object_id, 'power'))
        self.assertFalse(any(r['id'].startswith('activate:'+source.ref+':') for r in self.actions(s)))
        self.cast(s, haste, [source.ref], payment={'R': 1})
        self.assertIsNone(self.resolve(s))
        for wrong in (source, foreign):
            self.reject(s, self.activation_command(s, source, costs=[wrong]))
        self.activate(s, source, costs=[price])
        self.assertEqual('graveyard', s.state.cards[price.object_id].zone)
        self.assertTrue(s.state.cards[source.object_id].tapped)
        self.assertEqual(3, s.engine._numeric_stat(source.object_id, 'power'))
        self.cast(s, bounce, [source.ref], seat='B', payment={'U': 1})
        s = self.finish_search(s, land, excluded=[nonland, foreign_land])
        self.assertEqual('hand', s.state.cards[source.object_id].zone)
        self.assertEqual('graveyard', s.state.cards[price.object_id].zone)
        self.private_replay(s, private)

    def test_real_wight_token_price_is_not_a_card_and_counter_refunds_nothing(self):
        s = self.session(825030)
        source = self.add(s.engine, NAMES[0], zone='hand')
        haste = self.add(s.engine, 'Expedite', zone='hand')
        alarm = self.add(s.engine, 'Raise the Alarm', zone='hand')
        stifle = self.add(s.engine, 'Stifle', seat='B', zone='hand')
        land = self.hidden(s, 'Forest', ref='countered-wight-private-land')
        private = self.add(s.engine, 'Generic Bound Growth', seat='C', zone='hand')
        s.state.players['B'].mana_pool['U'] = 1
        self.prepare(s, mana={'B': 1, 'G': 1, 'R': 1, 'C': 1, 'W': 1})
        self.checkpoint(s)
        self.cast(s, source, payment={'B': 1, 'G': 1}); self.assertIsNone(self.resolve(s))
        self.cast(s, haste, [source.ref], payment={'R': 1}); self.assertIsNone(self.resolve(s))
        self.cast(s, alarm, payment={'C': 1, 'W': 1}); self.assertIsNone(self.resolve(s))
        tokens = [c for c in s.state.cards.values() if c.zone == 'battlefield' and c.is_token and c.controller == 'A']
        self.assertEqual(2, len(tokens))
        self.activate(s, source, costs=[tokens[0]])
        self.assertTrue(s.state.cards[source.object_id].tapped)
        self.assertEqual(2, s.engine._numeric_stat(source.object_id, 'power'))
        self.cast(s, stifle, [s.state.stack[-1].ref], seat='B', payment={'U': 1})
        s = self.resume(s)
        self.assertIsNone(self.resolve(s))
        self.assertTrue(s.state.cards[source.object_id].tapped)
        self.assertNotEqual('battlefield', s.state.cards[tokens[0].object_id].zone)
        self.assertEqual('library', s.state.cards[land.object_id].zone)
        self.assertEqual(2, s.engine._numeric_stat(source.object_id, 'power'))
        self.private_replay(s, private)

    def test_real_reclaimer_land_price_establishes_threshold_then_exile_revokes_it(self):
        s = self.session(825040)
        source = self.add(s.engine, NAMES[1], zone='hand')
        haste = self.add(s.engine, 'Expedite', zone='hand')
        lands = [self.add(s.engine, name, zone='graveyard', ref='reclaimer-old-'+str(i)) for i, name in enumerate(('Island', 'Plains'))]
        price = self.add(s.engine, 'Forest', ref='reclaimer-paid-land')
        price.tapped = True
        wrong = self.add(s.engine, 'Fugitive Wizard')
        crypt = self.add(s.engine, "Tormod's Crypt", seat='B')
        land = self.hidden(s, 'Island', ref='reclaimer-private-result')
        private = self.add(s.engine, 'Generic Bound Growth', seat='C', zone='hand')
        self.prepare(s, mana={'G': 1, 'R': 1, 'C': 2})
        self.checkpoint(s)
        self.cast(s, source, payment={'G': 1}); self.assertIsNone(self.resolve(s))
        self.cast(s, haste, [source.ref], payment={'R': 1}); self.assertIsNone(self.resolve(s))
        self.assertEqual(1, s.engine._numeric_stat(source.object_id, 'power'))
        self.reject(s, self.activation_command(s, source, costs=[wrong]))
        self.activate(s, source, costs=[price])
        self.assertEqual('graveyard', s.state.cards[price.object_id].zone)
        self.assertEqual(3, s.engine._numeric_stat(source.object_id, 'power'))
        self.assertEqual(0, s.state.players['A'].mana_pool.get('C', 0))
        self.activate(s, crypt, seat='B', targets=['A'])
        s = self.resume(s)
        s = self.finish_search(s, land)
        self.assertTrue(all(s.state.cards[c.object_id].zone == 'exile' for c in [*lands, price]))
        self.assertEqual('graveyard', s.state.cards[crypt.object_id].zone)
        self.assertEqual(1, s.engine._numeric_stat(source.object_id, 'power'))
        self.private_replay(s, private)

    def test_real_reclaimer_may_fail_to_find_but_still_pays_and_shuffles(self):
        s = self.session(825050)
        source = self.add(s.engine, NAMES[1], zone='hand')
        haste = self.add(s.engine, 'Expedite', zone='hand')
        price = self.add(s.engine, 'Forest', ref='failed-reclaimer-price')
        land = self.hidden(s, 'Island', ref='failed-reclaimer-eligible-land')
        private = self.add(s.engine, 'Generic Bound Growth', seat='C', zone='hand')
        self.prepare(s, mana={'G': 1, 'R': 1, 'C': 2})
        self.checkpoint(s)
        self.cast(s, source, payment={'G': 1}); self.assertIsNone(self.resolve(s))
        self.cast(s, haste, [source.ref], payment={'R': 1}); self.assertIsNone(self.resolve(s))
        self.activate(s, source, costs=[price])
        s = self.finish_search(s, land, fail=True)
        self.assertTrue(s.state.cards[source.object_id].tapped)
        self.assertEqual('graveyard', s.state.cards[price.object_id].zone)
        self.assertEqual(0, s.state.players['A'].mana_pool.get('C', 0))
        self.private_replay(s, private)

    def test_real_wight_owner_controller_and_search_principal_follow_control(self):
        s = self.session(825060)
        source = self.add(s.engine, NAMES[0])
        for i in range(2):
            self.add(s.engine, 'Walking Corpse', zone='graveyard', ref='wight-owner-grave-'+str(i))
        self.add(s.engine, 'Walking Corpse', seat='B', zone='graveyard', ref='wight-controller-grave')
        price = self.add(s.engine, 'Fugitive Wizard', seat='B', ref='wight-controller-price')
        steal = self.add(s.engine, 'Control Magic', seat='B', zone='hand')
        haste = self.add(s.engine, 'Expedite', seat='B', zone='hand')
        land = self.hidden(s, 'Island', seat='B', ref='wight-controller-private-land')
        foreign = self.hidden(s, 'Forest', ref='wight-owner-private-land')
        private = self.add(s.engine, 'Generic Bound Growth', seat='C', zone='hand')
        self.prepare(s, actor='B', active='B', mana={'C': 2, 'U': 2, 'R': 1})
        self.checkpoint(s)
        self.assertEqual(4, s.engine._numeric_stat(source.object_id, 'power'))
        self.cast(s, steal, [source.ref], seat='B', payment={'C': 2, 'U': 2}); self.assertIsNone(self.resolve(s))
        self.assertEqual(('A', 'B'), (source.owner, source.controller))
        self.assertEqual(3, s.engine._numeric_stat(source.object_id, 'power'))
        self.cast(s, haste, [source.ref], seat='B', payment={'R': 1}); self.assertIsNone(self.resolve(s))
        self.activate(s, source, costs=[price], seat='B')
        self.assertEqual(4, s.engine._numeric_stat(source.object_id, 'power'))
        s = self.finish_search(s, land, seat='B', excluded=[foreign])
        self.assertEqual(('A', 'B'), (s.state.cards[source.object_id].owner, s.state.cards[source.object_id].controller))
        self.private_replay(s, private)

    def test_fresh_omission_or_widened_result_equality_is_detected(self):
        method = 'test_originals_normally_bind_complete_current_search_and_exact_price'
        with patch('quorune.card_programs.activation_view_equivalence.fixed_battlefield_search_results_equal', return_value=False):
            with self.assertRaises(AssertionError):
                getattr(type(self)(method), method)()
        method = 'test_typed_result_defaults_preserve_all_other_fields_and_inputs'
        with patch('quorune.card_programs.activation_view_equivalence.fixed_battlefield_search_results_equal', return_value=True):
            with self.assertRaises(AssertionError):
                getattr(type(self)(method), method)()


if __name__ == '__main__':
    unittest.main()
