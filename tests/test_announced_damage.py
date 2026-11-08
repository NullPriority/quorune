from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT
import test_any_number_targets as controls
import test_soulbond as combat_controls
from quorune.announced_damage import ANNOUNCED_DAMAGE_CAPABILITY, ANNOUNCED_DAMAGE_CONTEXT
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.choice_forms import delegated_choice_fields
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database


class AnnouncedDamageTests(unittest.TestCase):
    register_fixture_cards_on_add = True
    session = controls.AnyNumberTargetTests.session
    add = controls.AnyNumberTargetTests.add
    prepare = controls.AnyNumberTargetTests.prepare
    checkpoint = controls.AnyNumberTargetTests.checkpoint
    action = controls.AnyNumberTargetTests.action
    actions = controls.AnyNumberTargetTests.actions
    cast = controls.AnyNumberTargetTests.cast
    resolve = controls.AnyNumberTargetTests.resolve
    until = combat_controls.SoulbondRuntimeTests.until
    replay = controls.AnyNumberTargetTests.replay
    binding = controls.AnyNumberTargetTests.binding
    reject = controls.AnyNumberTargetTests.reject
    private_replay = controls.AnyNumberTargetTests.private_replay

    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        path = Path(cls.temp.name) / 'announced-damage.sqlite3'
        fixtures = ROOT / 'tests/fixtures'
        build_fixture_database([
            fixtures / 'bound-effect-program-cards.json',
            fixtures / 'any-number-target-cards.json',
            fixtures / 'damage-replacement-cards.json',
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Announced damage witnesses', [
            DeckEntry('Generic Bound Commander', 1, 'commander'),
            DeckEntry('Generic Bound Plains', 30),
        ], ['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temp.cleanup()

    def assert_allocation_offer(self, packet, action, *, total, minimum, maximum):
        ctx = packet['decision']['ctx']
        schemas = [action.get('target_schema')]
        schemas.extend(option.get('target_schema') for option in action.get('cost_options', ()))
        schemas.extend(option.get('target_schema') for option in action.get('choice_schema', {}).get('cast_options', ()))
        schema = next(schema for schema in schemas if schema and 'damage_allocation' in schema)
        self.assertEqual({'schema_version': 1, 'total': total}, schema['damage_allocation'])
        self.assertEqual([(minimum, maximum)], [(group['min'], group['max']) for group in schema['groups']])
        self.assertIn('damage_allocation', delegated_choice_fields(
            action, decision_kind=packet['decision']['kind'], context=ctx,
        ))

    def test_actual_arc_lightning_pins_shares_before_payment_and_loses_the_illegal_share(self):
        s = self.session(769001)
        source = self.add(s.engine, 'Arc Lightning', zone='hand')
        bounce = self.add(s.engine, 'Unsummon', seat='B', zone='hand')
        target = self.add(s.engine, 'Generic Bound Body', seat='B')
        private = self.add(s.engine, 'Generic Bound Growth', seat='C', zone='hand')
        s.state.players['B'].mana_pool['U'] = 1
        self.prepare(s, mana={'R': 1, 'C': 2})
        self.checkpoint(s)
        binding = self.binding(s, source)
        self.assertTrue(binding['strict_capability_ready'], binding['blockers'])
        action = self.action(s, source)
        self.assert_allocation_offer(s.packet('pilot:A', full=True), action, total=3, minimum=1, maximum=3)
        command = {'action_id': action['id'], 'targets': [target.ref, 'D'], 'pay': 'auto'}
        legal = {target.ref: 1, 'D': 2}
        self.reject(s, {**command, 'damage_allocation': legal}, 'pilot:C')
        self.reject(s, command)
        for malformed in ({target.ref: 0, 'D': 3}, {target.ref: True, 'D': 2},
                          {'D': 3}, {**legal, 'C': 1}, {target.ref: 1, 'D': 1}):
            self.reject(s, {**command, 'damage_allocation': malformed})
        accepted = s.act('pilot:A', {**command, 'choices': {'damage_allocation': legal}})
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertEqual(0, sum(s.state.players['A'].mana_pool.values()))
        self.assertEqual({'schema_version': 1, 'shares': [1, 2]}, s.state.stack[-1].context[ANNOUNCED_DAMAGE_CONTEXT])
        for seat in 'ABCD':
            public = s.packet('pilot:' + seat, full=True)['state']['stack'][0]
            self.assertEqual(([target.ref, 'D'], [1, 2]), (public['targets'], public['damage_shares']))
        self.cast(s, 'B', bounce, [target.ref])
        s = self.replay(s, load=True)
        self.assertIsNone(self.resolve(s))
        self.assertEqual('hand', s.state.cards[target.object_id].zone)
        self.assertEqual(38, s.state.players['D'].life)
        self.assertEqual(40, s.state.players['B'].life)
        self.private_replay(s, private)

    def test_actual_reverberate_copies_shares_to_new_targets_in_slot_order(self):
        s = self.session(769002)
        source = self.add(s.engine, 'Arc Lightning', zone='hand')
        copier = self.add(s.engine, 'Reverberate', seat='B', zone='hand')
        s.state.players['B'].mana_pool['R'] = 2
        self.prepare(s, mana={'R': 1, 'C': 2})
        self.checkpoint(s)
        cast = s.act('pilot:A', {'action_id': self.action(s, source)['id'], 'targets': ['B', 'D'],
                                'damage_allocation': {'D': 2, 'B': 1}, 'pay': 'auto'})
        self.assertTrue(cast.ok, cast.summary)
        self.cast(s, 'B', copier, [s.state.stack[-1].ref])
        self.assertEqual('semantic.choice', self.resolve(s).kind)
        self.reject(s, {'action_id': 'choose', 'targets': ['A']}, 'pilot:B')
        self.reject(s, {'action_id': 'choose', 'targets': [{'retain': 1}, 'C']}, 'pilot:B')
        s = self.replay(s, load=True)
        copy = s.act('pilot:B', {'action_id': 'choose', 'targets': ['A', 'C']})
        self.assertTrue(copy.ok, copy.summary)
        self.assertIsNone(self.resolve(s))
        self.assertEqual([39, 39, 38, 38], [s.state.players[seat].life for seat in 'ABCD'])
        self.replay(s, load=True)

    def test_actual_arc_mage_announces_before_tap_discard_and_mana(self):
        s = self.session(769010)
        source = self.add(s.engine, 'Arc Mage')
        source.acquired_control_turn_count = -1
        cost = self.add(s.engine, 'Generic Bound Body', zone='hand', ref='mage-discard')
        target = self.add(s.engine, 'Generic Bound Body', seat='B', ref='mage-target')
        bounce = self.add(s.engine, 'Unsummon', seat='B', zone='hand')
        private = self.add(s.engine, 'Generic Bound Growth', seat='C', zone='hand')
        s.state.players['B'].mana_pool['U'] = 1
        self.prepare(s, mana={'R': 1, 'C': 2})
        self.checkpoint(s)
        self.assertTrue(self.binding(s, source)['strict_capability_ready'])
        action = next(a for a in self.actions(s) if a['id'].startswith(f'activate:{source.ref}:'))
        self.assert_allocation_offer(s.packet('pilot:A', full=True), action, total=2, minimum=1, maximum=2)
        command = {'action_id': action['id'], 'targets': [target.ref, 'D'], 'cost_cards': [cost.ref], 'pay': 'auto'}
        allocation = {target.ref: 1, 'D': 1}
        self.reject(s, {**command, 'damage_allocation': allocation}, 'pilot:C')
        self.reject(s, command)
        self.reject(s, {**command, 'damage_allocation': {target.ref: 2, 'D': 1}})
        accepted = s.act('pilot:A', {**command, 'choices': {'damage_allocation': allocation}})
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertTrue(s.state.cards[source.object_id].tapped)
        self.assertEqual('graveyard', s.state.cards[cost.object_id].zone)
        self.assertEqual(0, sum(s.state.players['A'].mana_pool.values()))
        self.assertEqual({'schema_version': 1, 'shares': [1, 1]}, s.state.stack[-1].context[ANNOUNCED_DAMAGE_CONTEXT])
        self.cast(s, 'B', bounce, [target.ref])
        s = self.replay(s, load=True)
        self.assertIsNone(self.resolve(s))
        self.assertEqual('hand', s.state.cards[target.object_id].zone)
        self.assertEqual(39, s.state.players['D'].life)
        self.private_replay(s, private)

    def test_actual_inferno_titan_announces_entry_and_attack_trigger_shares(self):
        for attacks in (False, True):
            with self.subTest(attacks=attacks):
                s = self.session(769011 + attacks)
                source = self.add(s.engine, 'Inferno Titan', zone='battlefield' if attacks else 'hand')
                source.acquired_control_turn_count = -1
                private = self.add(s.engine, 'Generic Bound Growth', seat='C', zone='hand')
                self.prepare(s, mana={'R': 2, 'C': 4})
                self.checkpoint(s)
                self.assertTrue(self.binding(s, source)['strict_capability_ready'])
                if attacks:
                    self.until(s, lambda session: getattr(session.state.pending_decision, 'kind', None) == 'combat.attackers')
                    result = s.act('pilot:A', {'action_id': 'attack', 'attackers': {source.ref: 'B'}})
                    self.assertTrue(result.ok, result.summary)
                else:
                    self.cast(s, 'A', source)
                self.assertEqual('semantic.target', self.resolve(s).kind)
                packet = s.packet('pilot:A', full=True)
                action = next(a for a in packet['decision']['legal_actions'] if a['id'] == 'choose')
                self.assert_allocation_offer(packet, action, total=3, minimum=1, maximum=3)
                command = {'action_id': 'choose', 'targets': ['B', 'D']}
                self.reject(s, {**command, 'damage_allocation': {'B': 1, 'D': 2}}, 'pilot:B')
                self.reject(s, command)
                self.reject(s, {**command, 'damage_allocation': {'B': 0, 'D': 3}})
                self.reject(s, {**command, 'targets': ['B', 'B'], 'damage_allocation': {'B': 3}})
                s = self.replay(s, load=True)
                result = s.act('pilot:A', {**command, 'choices': {'damage_allocation': {'D': 2, 'B': 1}}})
                self.assertTrue(result.ok, result.summary)
                self.assertEqual({'schema_version': 1, 'shares': [1, 2]}, s.state.stack[-1].context[ANNOUNCED_DAMAGE_CONTEXT])
                self.assertEqual(40, s.state.players['D'].life)
                self.assertIsNone(self.resolve(s))
                self.assertEqual([39, 38], [s.state.players[seat].life for seat in 'BD'])
                self.private_replay(s, private)

    def test_actual_gang_of_devils_offers_the_split_to_its_controller(self):
        s = self.session(769013)
        source = self.add(s.engine, 'Gang of Devils', seat='B')
        kill = self.add(s.engine, 'Murder', zone='hand')
        self.prepare(s, mana={'B': 2, 'C': 1})
        self.checkpoint(s)
        self.assertTrue(self.binding(s, source)['strict_capability_ready'])
        self.cast(s, 'A', kill, [source.ref])
        pending = self.resolve(s)
        self.assertEqual('semantic.target', pending.kind)
        self.assertEqual(['B'], list(pending.actors))
        self.assertEqual('graveyard', s.state.cards[source.object_id].zone)
        command = {'action_id': 'choose', 'targets': ['A', 'C'], 'damage_allocation': {'A': 2, 'C': 1}}
        self.reject(s, command)
        result = s.act('pilot:B', command)
        self.assertTrue(result.ok, result.summary)
        s = self.replay(s, load=True)
        self.assertIsNone(self.resolve(s))
        self.assertEqual([38, 39], [s.state.players[seat].life for seat in 'AC'])
        self.replay(s, load=True)

    def test_actual_mogg_mob_uses_sacrificed_source_lifelink_for_the_batch(self):
        s = self.session(769014)
        source = self.add(s.engine, 'Mogg Mob')
        grant = self.add(s.engine, 'Cruel Feeding', zone='hand')
        self.prepare(s, mana={'B': 1})
        self.checkpoint(s)
        self.assertTrue(self.binding(s, source)['strict_capability_ready'])
        self.cast(s, 'A', grant, [source.ref])
        self.assertIsNone(self.resolve(s))
        action = next(a for a in self.actions(s) if a['id'].startswith(f'activate:{source.ref}:'))
        result = s.act('pilot:A', {'action_id': action['id'], 'targets': ['B', 'D'],
                                   'damage_allocation': {'B': 2, 'D': 1}, 'pay': 'auto'})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual('graveyard', s.state.cards[source.object_id].zone)
        s = self.replay(s, load=True)
        self.assertIsNone(self.resolve(s))
        self.assertEqual([43, 38, 39], [s.state.players[seat].life for seat in 'ABD'])
        self.replay(s, load=True)

    def test_actual_atarka_allows_zero_and_filters_opponent_damageable_permanents(self):
        for choose_targets in (False, True):
            with self.subTest(choose_targets=choose_targets):
                s = self.session(769015 + choose_targets)
                source = self.add(s.engine, 'Dragonlord Atarka', zone='hand')
                own = self.add(s.engine, 'Generic Bound Body', ref='atarka-own-creature')
                creature = self.add(s.engine, 'Generic Bound Body', seat='B', ref='atarka-opponent-creature')
                walker = self.add(s.engine, 'Ral, Caller of Storms', seat='D')
                walker.counters['loyalty'] = 5
                self.prepare(s, mana={'R': 1, 'G': 1, 'C': 5})
                self.checkpoint(s)
                self.assertTrue(self.binding(s, source)['strict_capability_ready'])
                self.cast(s, 'A', source)
                self.assertEqual('semantic.target', self.resolve(s).kind)
                packet = s.packet('pilot:A', full=True)
                action = next(a for a in packet['decision']['legal_actions'] if a['id'] == 'choose')
                self.assert_allocation_offer(packet, action, total=5, minimum=0, maximum=5)
                self.assertEqual({creature.ref, walker.ref}, set(action['target_schema']['legal_refs']))
                self.reject(s, {'action_id': 'choose', 'targets': [own.ref], 'damage_allocation': {own.ref: 5}})
                command = {'action_id': 'choose'}
                if choose_targets:
                    command.update(targets=[walker.ref, creature.ref], damage_allocation={walker.ref: 2, creature.ref: 3})
                result = s.act('pilot:A', command)
                self.assertTrue(result.ok, result.summary)
                s = self.replay(s, load=True)
                self.assertIsNone(self.resolve(s))
                self.assertEqual(3 if choose_targets else 5, s.state.cards[walker.object_id].counters['loyalty'])
                self.assertEqual(3 if choose_targets else 0, s.state.cards[creature.object_id].marked_damage)
                self.replay(s, load=True)

    def test_actual_electrolyze_keeps_its_draw_when_only_one_target_remains(self):
        s = self.session(769007)
        source = self.add(s.engine, 'Electrolyze', zone='hand')
        bounce = self.add(s.engine, 'Unsummon', seat='B', zone='hand')
        target = self.add(s.engine, 'Generic Bound Body', seat='B')
        s.state.players['B'].mana_pool['U'] = 1
        self.prepare(s, mana={'U': 1, 'R': 1, 'C': 1})
        self.checkpoint(s)
        initial_hand = len(s.state.players['A'].zones['hand'])
        accepted = s.act('pilot:A', {
            'action_id': self.action(s, source)['id'], 'targets': [target.ref, 'D'],
            'damage_allocation': {target.ref: 1, 'D': 1}, 'pay': 'auto',
        })
        self.assertTrue(accepted.ok, accepted.summary)
        self.cast(s, 'B', bounce, [target.ref])
        s = self.replay(s, load=True)
        self.assertIsNone(self.resolve(s))
        self.assertEqual('hand', s.state.cards[target.object_id].zone)
        self.assertEqual(39, s.state.players['D'].life)
        self.assertEqual(initial_hand, len(s.state.players['A'].zones['hand']))
        self.replay(s, load=True)

    def test_actual_cascade_offers_and_requires_the_same_split_before_free_cast(self):
        s = self.session(769003)
        party = self.add(s.engine, 'Boarding Party', zone='hand')
        source = self.add(s.engine, 'Arc Lightning', zone='library')
        library = s.state.players['A'].zones['library']
        library.remove(source.object_id)
        library.insert(0, source.object_id)
        self.prepare(s, mana={'R': 1, 'C': 5})
        self.checkpoint(s)
        self.cast(s, 'A', party)
        self.assertEqual('selection.exile_cast', self.resolve(s).kind)
        packet = s.packet('pilot:A', full=True)
        action = next(action for action in packet['decision']['legal_actions'] if action['id'] == 'cast')
        self.assert_allocation_offer(packet, action, total=3, minimum=1, maximum=3)
        command = {'action_id': 'cast', 'targets': ['B', 'D'], 'pay': 'auto'}
        self.reject(s, command)
        accepted = s.act('pilot:A', {**command, 'choices': {'damage_allocation': {'B': 2, 'D': 1}}})
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertEqual(0, sum(s.state.players['A'].mana_pool.values()))
        s = self.replay(s, load=True)
        self.assertIsNone(self.resolve(s))
        self.assertEqual([38, 39], [s.state.players[seat].life for seat in 'BD'])
        self.replay(s, load=True)

    def test_actual_pyrotechnics_zero_targets_resolves_with_no_damage(self):
        s = self.session(769004)
        source = self.add(s.engine, 'Pyrotechnics', zone='hand')
        self.prepare(s, mana={'R': 1, 'C': 4})
        self.checkpoint(s)
        action = self.action(s, source)
        self.assert_allocation_offer(s.packet('pilot:A', full=True), action, total=4, minimum=0, maximum=4)
        command = {'action_id': action['id'], 'pay': 'auto'}
        self.reject(s, {**command, 'damage_allocation': {'B': 4}})
        self.reject(s, {**command, 'damage_allocation': None})
        accepted = s.act('pilot:A', command)
        self.assertTrue(accepted.ok, accepted.summary)
        s = self.replay(s, load=True)
        self.assertIsNone(self.resolve(s))
        self.assertEqual('graveyard', s.state.cards[source.object_id].zone)
        self.assertEqual([40] * 4, [s.state.players[seat].life for seat in 'ABCD'])
        self.replay(s, load=True)

    def test_actual_pyrotechnics_resumes_one_replacement_batch_after_reload(self):
        s = self.session(769005)
        source = self.add(s.engine, 'Pyrotechnics', zone='hand')
        furnace = self.add(s.engine, 'Furnace of Rath')
        shield = self.add(s.engine, 'Mending Hands', seat='B', zone='hand')
        s.state.players['B'].mana_pool['W'] = 1
        self.prepare(s, mana={'R': 1, 'C': 4})
        self.checkpoint(s)
        accepted = s.act('pilot:A', {'action_id': self.action(s, source)['id'], 'targets': ['B', 'D'],
                                    'damage_allocation': {'B': 3, 'D': 1}, 'pay': 'auto'})
        self.assertTrue(accepted.ok, accepted.summary)
        self.cast(s, 'B', shield, ['B'])
        self.assertEqual('replacement.order', self.resolve(s).kind)
        self.assertEqual([40, 40], [s.state.players[seat].life for seat in 'BD'])
        s = self.replay(s, load=True)
        packet = s.packet('pilot:B', full=True)
        options = packet['decision']['ctx']['options']
        option = next((option['id'] for option in options if f':{furnace.ref}:' in option['id']), None)
        self.assertIsNotNone(option, options)
        command = {'action_id': 'choose', 'choices': {'replacement': option}}
        self.reject(s, command, 'pilot:C')
        chosen = s.act('pilot:B', command)
        self.assertTrue(chosen.ok, chosen.summary)
        self.assertEqual([38, 38], [s.state.players[seat].life for seat in 'BD'])
        self.replay(s, load=True)

    def test_announced_damage_rejects_unowned_totals_riders_and_runtime_dependency(self):
        record = self.db.lookup('Arc Lightning')
        for text in (record.oracle_text.replace('3 damage', 'X damage'),
                     record.oracle_text + ' Draw a card.'):
            ir = compile_oracle_card(replace(record, oracle_text=text), capability_registry=self.registry,
                                     capability_profile='commander_review')
            self.assertNotEqual('exact', ir.status, text)
        titan = self.db.lookup('Inferno Titan')
        unbound = replace(titan, oracle_text=titan.oracle_text.replace(
            'this creature enters or attacks', 'another creature you control enters',
        ))
        ir = compile_oracle_card(unbound, capability_registry=self.registry, capability_profile='commander_review')
        self.assertNotEqual('exact', ir.status, unbound.oracle_text)
        s = self.session(769006)
        source = self.add(s.engine, 'Arc Lightning', zone='hand')
        program = next(p for p in s.engine.semantics.programs_for_oracle(source.oracle_id)
                       if ANNOUNCED_DAMAGE_CAPABILITY in p.capability_dependencies)
        dependencies = tuple(cap for cap in program.capability_dependencies if cap != ANNOUNCED_DAMAGE_CAPABILITY)
        edited = replace(program, capability_dependencies=dependencies,
                         capability_closure=self.registry.closure(dependencies, profile='commander_review').to_dict())
        bound = bind_semantic_program_runtime(edited, capability_registry=self.registry, profile='commander_review')
        self.assertFalse(bound['strict'])
        self.assertIn('capability:undeclared_runtime_dependency:' + ANNOUNCED_DAMAGE_CAPABILITY, bound['blockers'])

    def test_announced_damage_omitting_cast_validation_is_caught_by_native_witness(self):
        method = 'test_actual_arc_lightning_pins_shares_before_payment_and_loses_the_illegal_share'
        with patch('quorune.announced_damage.cast_allocation_details', return_value={}):
            with self.assertRaises(AssertionError):
                getattr(type(self)(method), method)()

    def test_announced_damage_omitting_ability_validation_is_caught_by_native_witnesses(self):
        cases = (
            ('quorune.rules.activation.proposal', 'test_actual_arc_mage_announces_before_tap_discard_and_mana'),
            ('quorune.selection.targeting', 'test_actual_inferno_titan_announces_entry_and_attack_trigger_shares'),
        )
        for owner, witness in cases:
            with self.subTest(owner=owner), patch(f'{owner}.announced_allocation_details', return_value={}):
                with self.assertRaises(AssertionError):
                    getattr(type(self)(witness), witness)()
