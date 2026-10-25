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
from quorune.compiler.activated_ability_catalog import with_activated_ability_catalog
from quorune.deck import DeckDefinition, DeckEntry
from quorune.rules.capabilities import load_default_capability_registry
from quorune.semantics import SemanticRegistry
from scripts.build_test_database import build_fixture_database
import test_land_search_view_equivalence as native


NAMES = ('Master Transmuter', 'Goblin Engineer', 'Cryogen Relic', 'Soul-Guide Lantern')


class ActivationResultViewTests(unittest.TestCase):
    register_fixture_cards_on_add = True
    session = native.LandSearchViewEquivalenceTests.session
    add = native.LandSearchViewEquivalenceTests.add
    prepare = native.LandSearchViewEquivalenceTests.prepare
    checkpoint = native.LandSearchViewEquivalenceTests.checkpoint
    resolve = native.LandSearchViewEquivalenceTests.resolve
    replay = native.LandSearchViewEquivalenceTests.replay
    private_replay = native.LandSearchViewEquivalenceTests.private_replay
    priority = native.LandSearchViewEquivalenceTests.priority
    actions = native.LandSearchViewEquivalenceTests.actions
    cast = native.LandSearchViewEquivalenceTests.cast
    reject = native.LandSearchViewEquivalenceTests.reject
    binding = native.LandSearchViewEquivalenceTests.binding
    activation_command = native.LandSearchViewEquivalenceTests.activation_command
    activate = native.LandSearchViewEquivalenceTests.activate
    resume = native.LandSearchViewEquivalenceTests.resume
    views = native.LandSearchViewEquivalenceTests.views

    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        path = Path(cls.temp.name)/'activation-result-views.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',
            ROOT/'tests/fixtures/activation-result-views-cards.json'], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Complete fixed activation results',
            [DeckEntry('Generic Bound Commander', 1, 'commander'), DeckEntry('Generic Bound Plains', 30)],
            ['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temp.cleanup()

    def test_four_originals_bind_complete_results_and_preserve_all_prices(self):
        from quorune.card_programs.activation_view_equivalence import shadowed_reviewed_activation_view_keys as shadow
        counts = dict(zip(NAMES, (1, 1, 1, 2)))
        for i, name in enumerate(NAMES):
            s, source, record, current, old = self.views(name, 859000+i)
            bound = self.binding(s, source)
            self.assertTrue(bound['strict_capability_ready'], (name, bound['blockers']))
            keys = shadow(record, current, old, capability_registry=self.registry, capability_profile='commander_review')
            self.assertEqual(counts[name], len(keys), name)
            self.assertTrue(all(p.trust_level == 'trusted' and not p.requires_arbiter for p in current))
            for program in current:
                if program.event == 'activate':
                    self.assertTrue(program.cost_schema)
                    self.assertTrue(str(program.provenance.get('authored_by', '')).startswith('oracle-ir-v'))

    def test_source_price_role_and_duplicate_guards_reject_changed_views(self):
        from quorune.card_programs.activation_view_equivalence import shadowed_reviewed_activation_view_keys as shadow
        for i, name in enumerate(NAMES):
            _s, _source, record, current, old = self.views(name, 859010+i)
            check = lambda g=current, r=old: shadow(record, g, r,
                capability_registry=self.registry, capability_profile='commander_review')
            keys = check();self.assertTrue(keys)
            before = json.dumps([p.to_dict() for p in (*current, *old)], sort_keys=True)
            for key in keys:
                previous = next(p for p in old if p.key == key)
                changed = deepcopy(previous.handlers)
                changed[0]['ability']['life_payment'] = 1
                self.assertNotIn(key, check(r=[replace(previous, handlers=changed)]))
                self.assertNotIn(key, check(r=[replace(previous,
                    provenance={**previous.provenance, 'source_oracle_hash': '0'*64})]))
                self.assertNotIn(key, check(r=[replace(previous, active_zone='graveyard')]))
            self.assertFalse(check(g=[replace(p, trust_level='provisional') for p in current]))
            self.assertFalse(check(g=[*current, *current]))
            self.assertEqual(before, json.dumps([p.to_dict() for p in (*current, *old)], sort_keys=True))

    def test_result_comparison_rejects_changed_domains_flags_and_extra_operations(self):
        from quorune.card_programs.activation_result_equivalence import fixed_activation_results_equal
        for i, name in enumerate(NAMES):
            _s, _source, _record, current, old = self.views(name, 859020+i)
            pairs = [(p, q) for p in current for q in old if p.key == q.key
                and fixed_activation_results_equal(p, q)]
            self.assertTrue(pairs, name)
            for p, q in pairs:
                self.assertFalse(fixed_activation_results_equal(replace(p,
                    effects=[*p.effects, {'op': 'draw', 'player': '$controller', 'count': 1, 'private': True}]), q))
                with self.assertRaises(ValueError):
                    replace(p, capability_dependencies=())
                effect = deepcopy(p.effects[0])
                if effect['op'] == 'put_card_from_hand':
                    effect['query']['types_all'] = ['creature']
                elif effect['op'] == 'reanimate':
                    effect['tapped'] = True
                elif effect['op'] == 'place_counters_on_targets':
                    effect['amount'] = True
                else:
                    effect['set']['seat_relation'] = 'actor'
                self.assertFalse(fixed_activation_results_equal(replace(p, effects=[effect]), q))

    def test_target_aliases_keep_optional_tap_and_owned_graveyard_boundaries(self):
        from quorune.card_programs.activation_target_equivalence import reviewed_activation_with_equivalent_target as compare
        for i, name in enumerate(('Goblin Engineer', 'Cryogen Relic')):
            _s, _source, _record, current, old = self.views(name, 859030+i)
            q = next(p for p in old if p.event == 'activate')
            p = next(p for p in current if p.key == q.key)
            self.assertIsNotNone(compare(p, q))
            before = json.dumps(q.to_dict(), sort_keys=True)
            for field, value in (('future', True), ('count', True), ('controller_relation', 'opponent')):
                target = {**deepcopy(q.target_schema), field: value}
                handlers = deepcopy(q.handlers);handlers[0]['ability']['target_schema'] = deepcopy(target)
                self.assertIsNone(compare(p, replace(q, target_schema=target, handlers=handlers)))
            self.assertIsNone(compare(p, replace(q, target_schema={**q.target_schema, 'future': True})))
            self.assertEqual(before, json.dumps(q.to_dict(), sort_keys=True))

    def test_fresh_result_and_target_omissions_break_original_admission(self):
        method = 'test_four_originals_bind_complete_results_and_preserve_all_prices'
        with patch('quorune.card_programs.activation_view_equivalence.fixed_activation_results_equal', return_value=False):
            with self.assertRaises(AssertionError):
                getattr(type(self)(method), method)()
        with patch('quorune.card_programs.activation_target_equivalence._optional_tapped_creature_target', return_value=None):
            with self.assertRaises(AssertionError):
                getattr(type(self)(method), method)()

    def test_real_paid_transmuter_can_return_and_put_back_its_own_new_incarnation(self):
        s = self.session(859100)
        source = self.add(s.engine, 'Master Transmuter', zone='hand')
        haste = self.add(s.engine, 'Expedite', zone='hand')
        alternative = self.add(s.engine, 'Memnite', zone='hand', ref='transmuter-private-artifact')
        nonartifact = self.add(s.engine, 'Generic Bound Growth', zone='hand', ref='transmuter-private-nonartifact')
        foreign = self.add(s.engine, 'Sol Ring', seat='B', zone='hand', ref='transmuter-foreign-hand')
        private = self.add(s.engine, 'Generic Bound Growth', seat='C', zone='hand', ref='transmuter-private-control')
        self.prepare(s, mana={'C': 3, 'U': 2, 'R': 1})
        self.checkpoint(s)
        self.cast(s, source, payment={'C': 3, 'U': 1})
        self.assertIsNone(self.resolve(s))
        self.cast(s, haste, [source.ref], payment={'R': 1})
        self.assertIsNone(self.resolve(s))
        original = s.state.cards[source.object_id].logical_object_id
        self.activate(s, source, costs=[source])
        self.assertEqual('hand', s.state.cards[source.object_id].zone)
        decision = self.resolve(s)
        self.assertIsNotNone(decision)
        self.assertEqual('semantic.choice', decision.kind)
        s = self.resume(s)
        packet = json.dumps(s.packet('pilot:A', full=True)['decision'])
        self.assertIn(source.ref, packet)
        self.assertIn(alternative.ref, packet)
        self.assertNotIn(nonartifact.ref, packet)
        self.assertNotIn(foreign.ref, packet)
        for seat in 'BCD':
            self.assertNotIn(alternative.ref, json.dumps(s.packet('pilot:'+seat, full=True)))
        command = {'action_id': 'choose', 'card': source.ref}
        self.reject(s, command, seat='D')
        result = s.act('pilot:A', command)
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(s))
        current = s.state.cards[source.object_id]
        self.assertEqual('battlefield', current.zone)
        self.assertNotEqual(original, current.logical_object_id)
        self.assertFalse(current.tapped)
        self.assertNotIn('haste', s.engine._combat_keywords(current))
        self.private_replay(s, private)

    def test_trigger_aliases_reject_changed_search_exile_event_and_source_domains(self):
        from quorune.card_programs.trigger_result_equivalence import fixed_trigger_results_equal
        from quorune.card_programs.trigger_view_equivalence import shadowed_reviewed_trigger_view_keys as shadow
        for i, name in enumerate(('Goblin Engineer', 'Soul-Guide Lantern')):
            _s, _source, record, current, old = self.views(name, 859150+i)
            pairs = [(p, q) for p in current for q in old if p.event != 'activate'
                and fixed_trigger_results_equal(p, q)]
            self.assertEqual(1, len(pairs), name)
            p, q = pairs[0]
            check = lambda a=p, b=q: shadow(record, [a], [b],
                capability_registry=self.registry, capability_profile='commander_review')
            self.assertEqual({q.key}, check())
            before = json.dumps([p.to_dict(), q.to_dict()], sort_keys=True)
            self.assertFalse(check(a=replace(p, event='creature.dies')))
            self.assertFalse(check(a=replace(p, trust_level='provisional')))
            self.assertFalse(check(b=replace(q, provenance={**q.provenance,
                'source_rulings_hash': '0'*64})))
            self.assertFalse(shadow(record, [p, p], [q], capability_registry=self.registry,
                capability_profile='commander_review'))
            effect = deepcopy(q.effects[0])
            if name == 'Goblin Engineer':
                for field, value in (('optional', False), ('shuffle_after', False),
                        ('searching_player', '$opponent'), ('reveal', True),
                        ('count', {'minimum': 0, 'maximum': True}),
                        ('selector', {'types': ['creature']})):
                    self.assertFalse(fixed_trigger_results_equal(p,
                        replace(q, effects=[{**effect, field: value}])), field)
            else:
                for field, value in (('owner_relation', 'you'), ('count', True),
                        ('zones', ['battlefield']), ('future', True)):
                    self.assertFalse(check(b=replace(q,
                        target_schema={**deepcopy(q.target_schema), field: value})), field)
            self.assertEqual(before, json.dumps([p.to_dict(), q.to_dict()], sort_keys=True))

    def test_fresh_trigger_result_omission_prevents_both_whole_card_bindings(self):
        with patch('quorune.card_programs.trigger_view_equivalence.fixed_trigger_results_equal', return_value=False):
            for i, name in enumerate(('Goblin Engineer', 'Soul-Guide Lantern')):
                s, source, _record, _current, _old = self.views(name, 859160+i)
                self.assertFalse(self.binding(s, source)['strict_capability_ready'], name)

    def test_real_cryogen_zero_targets_still_pays_and_draws_on_departure(self):
        s = self.session(859170)
        source = self.add(s.engine, 'Cryogen Relic', zone='hand')
        elf = self.add(s.engine, 'Llanowar Elves', seat='B')
        private = self.add(s.engine, 'Generic Bound Growth', seat='C', zone='hand')
        self.prepare(s, mana={'C': 2, 'U': 2})
        self.checkpoint(s)
        hand = len(s.state.players['A'].zones['hand'])
        self.cast(s, source, payment={'C': 1, 'U': 1})
        self.assertIsNone(self.resolve(s))
        command = self.activation_command(s, source, targets=[elf.ref])
        self.reject(s, command, seat='A')
        self.activate(s, source, targets=[])
        self.assertEqual('graveyard', s.state.cards[source.object_id].zone)
        s = self.resume(s)
        self.assertIsNone(self.resolve(s))
        self.assertEqual(hand+1, len(s.state.players['A'].zones['hand']))
        self.assertFalse(s.state.cards[elf.object_id].counters)
        self.assertEqual(0, sum(s.state.players['A'].mana_pool.values()))
        self.private_replay(s, private)

    def test_real_cryogen_target_untapping_in_response_fizzles_without_refunding_price(self):
        s = self.session(859171)
        source = self.add(s.engine, 'Cryogen Relic', zone='hand')
        elf = self.add(s.engine, 'Llanowar Elves', seat='B')
        follower = self.add(s.engine, "Kiora's Follower", seat='B')
        elf.acquired_control_turn_count = follower.acquired_control_turn_count = -1
        private = self.add(s.engine, 'Generic Bound Growth', seat='C', zone='hand')
        self.prepare(s, mana={'C': 2, 'U': 2})
        self.checkpoint(s)
        hand = len(s.state.players['A'].zones['hand'])
        self.cast(s, source, payment={'C': 1, 'U': 1})
        self.assertIsNone(self.resolve(s))
        self.activate(s, elf, seat='B')
        self.activate(s, source, targets=[elf.ref])
        self.activate(s, follower, seat='B', targets=[elf.ref])
        s = self.resume(s)
        self.assertIsNone(self.resolve(s))
        self.assertFalse(s.state.cards[elf.object_id].tapped)
        self.assertFalse(s.state.cards[elf.object_id].counters)
        self.assertEqual('graveyard', s.state.cards[source.object_id].zone)
        self.assertEqual(hand+1, len(s.state.players['A'].zones['hand']))
        self.assertEqual(0, sum(s.state.players['A'].mana_pool.values()))
        self.private_replay(s, private)

    def choose(self, s, command, *, seat='A'):
        self.reject(s, command, seat='D')
        result = s.act('pilot:'+seat, command)
        self.assertTrue(result.ok, result.summary)

    def optional_search(self, s, *, accept):
        decision = self.resolve(s)
        self.assertIsNotNone(decision)
        self.assertEqual('semantic.choice', decision.kind)
        packet = s.packet('pilot:A', full=True)['decision']
        values = packet['legal_actions'][0]['choice_schema']['legal_values']
        value = 'apply' if accept else 'decline'
        self.assertIn(value, values)
        self.choose(s, {'action_id': 'choose', 'choice': value})

    def test_real_engineer_decline_keeps_library_then_paid_artifact_return_is_untapped(self):
        s = self.session(859110)
        source = self.add(s.engine, 'Goblin Engineer', zone='hand')
        haste = self.add(s.engine, 'Expedite', zone='hand')
        price = self.add(s.engine, 'Memnite', ref='engineer-artifact-price')
        wrong = self.add(s.engine, 'Generic Bound Body', ref='engineer-nonartifact-price')
        target = self.add(s.engine, 'Sol Ring', zone='graveyard', ref='engineer-own-target')
        foreign = self.add(s.engine, 'Sol Ring', seat='B', zone='graveyard', ref='engineer-foreign-target')
        expensive = self.add(s.engine, 'Vedalken Orrery', zone='graveyard', ref='engineer-too-expensive')
        private = self.add(s.engine, 'Generic Bound Growth', seat='C', zone='hand')
        self.prepare(s, mana={'C': 1, 'R': 3})
        self.checkpoint(s)
        before = tuple(s.state.players['A'].zones['library'])
        self.cast(s, source, payment={'C': 1, 'R': 1})
        self.optional_search(s, accept=False)
        self.assertIsNone(self.resolve(s))
        self.assertEqual(before, tuple(s.state.players['A'].zones['library']))
        self.cast(s, haste, [source.ref], payment={'R': 1})
        self.assertIsNone(self.resolve(s))
        for bad in (price, foreign, expensive):
            self.reject(s, self.activation_command(s, source, costs=[price], targets=[bad.ref]))
        self.reject(s, self.activation_command(s, source, costs=[wrong], targets=[target.ref]))
        self.activate(s, source, costs=[price], targets=[target.ref])
        self.assertTrue(s.state.cards[source.object_id].tapped)
        self.assertEqual('graveyard', s.state.cards[price.object_id].zone)
        s = self.resume(s)
        self.assertIsNone(self.resolve(s))
        current = s.state.cards[target.object_id]
        self.assertEqual(('battlefield', 'A'), (current.zone, current.controller))
        self.assertFalse(current.tapped)
        self.private_replay(s, private)

    def test_real_engineer_accepted_empty_search_shuffles_and_exiled_target_fizzles(self):
        s = self.session(859111)
        source = self.add(s.engine, 'Goblin Engineer', zone='hand')
        haste = self.add(s.engine, 'Expedite', zone='hand')
        price = self.add(s.engine, 'Memnite', ref='engineer-fizzle-price')
        target = self.add(s.engine, 'Sol Ring', zone='graveyard', ref='engineer-fizzle-target')
        crypt = self.add(s.engine, "Tormod's Crypt", seat='B')
        private = self.add(s.engine, 'Generic Bound Growth', seat='C', zone='hand')
        self.prepare(s, mana={'C': 1, 'R': 3})
        self.checkpoint(s)
        before = tuple(s.state.players['A'].zones['library'])
        self.cast(s, source, payment={'C': 1, 'R': 1})
        self.optional_search(s, accept=True)
        decision = self.resolve(s)
        self.assertEqual('semantic.search', decision.kind)
        s = self.resume(s)
        self.choose(s, {'action_id': 'choose', 'search_cards': []})
        self.assertIsNone(self.resolve(s))
        after = tuple(s.state.players['A'].zones['library'])
        self.assertEqual(set(before), set(after))
        self.assertNotEqual(before, after)
        self.cast(s, haste, [source.ref], payment={'R': 1})
        self.assertIsNone(self.resolve(s))
        self.activate(s, source, costs=[price], targets=[target.ref])
        self.activate(s, crypt, seat='B', targets=['A'])
        self.assertIsNone(self.resolve(s))
        self.assertEqual('exile', s.state.cards[target.object_id].zone)
        self.assertEqual('exile', s.state.cards[price.object_id].zone)
        self.assertTrue(s.state.cards[source.object_id].tapped)
        self.assertEqual(0, s.state.players['A'].mana_pool.get('R', 0))
        self.private_replay(s, private)

    def test_real_cryogen_entry_and_paid_departure_draw_then_stun_prevents_untap_once(self):
        s = self.session(859120)
        source = self.add(s.engine, 'Cryogen Relic', zone='hand')
        elf = self.add(s.engine, 'Llanowar Elves', seat='B')
        elf.acquired_control_turn_count = -1
        follower = self.add(s.engine, "Kiora's Follower")
        follower.acquired_control_turn_count = -1
        private = self.add(s.engine, 'Generic Bound Growth', seat='C', zone='hand')
        self.prepare(s, mana={'C': 2, 'U': 2})
        self.checkpoint(s)
        hand = len(s.state.players['A'].zones['hand'])
        self.cast(s, source, payment={'C': 1, 'U': 1})
        self.assertIsNone(self.resolve(s))
        self.assertEqual(hand, len(s.state.players['A'].zones['hand']))
        self.activate(s, elf, seat='B')
        self.assertTrue(s.state.cards[elf.object_id].tapped)
        self.activate(s, source, targets=[elf.ref])
        self.assertEqual('graveyard', s.state.cards[source.object_id].zone)
        s = self.resume(s)
        self.assertIsNone(self.resolve(s))
        self.assertEqual(hand+1, len(s.state.players['A'].zones['hand']))
        self.assertEqual(1, s.state.cards[elf.object_id].counters.get('stun'))
        self.activate(s, follower, targets=[elf.ref])
        self.assertIsNone(self.resolve(s))
        self.assertTrue(s.state.cards[elf.object_id].tapped)
        self.assertEqual(0, s.state.cards[elf.object_id].counters.get('stun', 0))
        self.private_replay(s, private)

    def test_real_lantern_exiles_entry_card_and_only_opponent_graveyards_after_price(self):
        s = self.session(859130)
        source = self.add(s.engine, 'Soul-Guide Lantern', zone='hand')
        selected = self.add(s.engine, 'Sol Ring', zone='graveyard', ref='lantern-entry-target')
        own = self.add(s.engine, 'Expedite', zone='graveyard', ref='lantern-own-retained')
        others = [self.add(s.engine, name, seat=seat, zone='graveyard', ref='lantern-opponent-'+seat)
            for seat, name in zip('BCD', ('Memnite', 'Sol Ring', 'Ornithopter'))]
        private = self.add(s.engine, 'Generic Bound Growth', seat='C', zone='hand')
        self.prepare(s, mana={'C': 1})
        self.checkpoint(s)
        self.cast(s, source, payment={'C': 1})
        decision = self.resolve(s)
        self.assertEqual('semantic.target', decision.kind)
        self.reject(s, {'action_id': 'choose', 'targets': []})
        self.choose(s, {'action_id': 'choose', 'targets': [selected.ref]})
        self.assertIsNone(self.resolve(s))
        self.assertEqual('exile', s.state.cards[selected.object_id].zone)
        self.activate(s, source)
        s = self.resume(s)
        self.assertIsNone(self.resolve(s))
        for card in others:
            self.assertEqual('exile', s.state.cards[card.object_id].zone)
        self.assertEqual('graveyard', s.state.cards[own.object_id].zone)
        self.assertEqual('graveyard', s.state.cards[source.object_id].zone)
        self.private_replay(s, private)

    def test_real_lantern_private_draw_price_is_not_graveyard_exile(self):
        s = self.session(859131)
        source = self.add(s.engine, 'Soul-Guide Lantern', zone='hand')
        private = self.add(s.engine, 'Generic Bound Growth', seat='C', zone='hand')
        self.prepare(s, mana={'C': 2})
        self.checkpoint(s)
        self.cast(s, source, payment={'C': 1})
        self.assertIsNone(self.resolve(s))
        self.priority(s, 'A')
        abilities = [a for a in s.engine._activated_abilities(s.state.cards[source.object_id])
            if a.mana.get('GENERIC', 0) == 1]
        self.assertEqual(1, len(abilities))
        row = next(r for r in self.actions(s) if r['id'].startswith('activate:'+source.ref+':')
            and abilities[0].ability_id in r['id'])
        before = set(s.state.players['A'].zones['hand'])
        command = {'action_id': row['id'], 'pay': 'auto'}
        self.reject(s, command, seat='D')
        result = s.act('pilot:A', command)
        self.assertTrue(result.ok, result.summary)
        s = self.resume(s)
        self.assertIsNone(self.resolve(s))
        self.assertEqual('graveyard', s.state.cards[source.object_id].zone)
        drawn = set(s.state.players['A'].zones['hand'])-before
        self.assertEqual(1, len(drawn))
        ref = s.state.cards[next(iter(drawn))].ref
        for seat in 'BCD':
            self.assertNotIn(ref, json.dumps(s.packet('pilot:'+seat, full=True)))
        self.private_replay(s, private)
