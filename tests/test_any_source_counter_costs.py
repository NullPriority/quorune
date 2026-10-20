from __future__ import annotations

"""CR118.3/122.1/602.2b: fixed one/two counters, chosen kinds, one source."""

from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT
from quorune.carddb import CardDatabase
from quorune.choice_forms import build_action_form
from quorune.deck import DeckDefinition, DeckEntry
from quorune.model import CardInstance
from quorune.oracle_ir import compile_oracle_card
from quorune.preflight import card_semantic_status
from quorune.record import authoritative_state_hash
from quorune.rules.activation_counter_cost import SourceCounterRemovalCost
from quorune.rules.activation.source_counter_choices import (
    ANY_SOURCE_COUNTER_COST_CAPABILITY, source_counter_payment_choices,
)
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
import test_compound_damage_recipients as controls
import test_temporary_keyword_removal as journeys

NAMES = ('Loch Mare', 'Burdened Stoneback', 'Gnarlbark Elm', 'Moonlit Lamenter',
         'Reaping Willow', 'Brambleback Brute', 'Hovel Hurler')


class AnySourceCounterValueTests(unittest.TestCase):
    def test_version_two_distributions_are_complete_exact_and_keep_legacy_named_wire(self):
        legacy = {'schema_version': 1, 'counter_name': 'charge', 'amount': 2}
        self.assertEqual(legacy, SourceCounterRemovalCost.from_dict(legacy).to_dict())
        source = CardInstance(object_id='source', ref='source-ref', oracle_id='fixture:source',
            printed_name='Source', owner='A', controller='A', zone='battlefield')
        source.counters.update({'-1/-1': 2, 'stun': 1, 'flying': 1})
        one = source_counter_payment_choices(source, SourceCounterRemovalCost(None, 1, 2))
        self.assertEqual({(('-1/-1', 1),), (('stun', 1),), (('flying', 1),)}, {choice.removals for choice in one})
        two = source_counter_payment_choices(source, SourceCounterRemovalCost(None, 2, 2))
        self.assertEqual({(('-1/-1', 2),), (('-1/-1', 1), ('flying', 1)),
            (('-1/-1', 1), ('stun', 1)), (('flying', 1), ('stun', 1))}, {choice.removals for choice in two})
        spec = SourceCounterRemovalCost(None, 2, 2)
        self.assertEqual(spec, SourceCounterRemovalCost.from_dict(spec.to_dict()))
        for bad in ({'counter_name': 'charge'}, {'amount': 0}, {'amount': 3},
                    {'amount': True}, {'schema_version': True}, {'unknown': 1}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                SourceCounterRemovalCost.from_dict({**spec.to_dict(), **bad})
        previous = {choice.choice_id for choice in one}
        source.zone_change_counter += 1
        self.assertFalse(previous.intersection(choice.choice_id for choice in source_counter_payment_choices(source, SourceCounterRemovalCost(None, 1, 2))))


class AnySourceCounterRuntimeTests(unittest.TestCase):
    register_fixture_cards_on_add = True
    session = controls.CompoundDamageRuntimeTests.session
    add = controls.CompoundDamageRuntimeTests.add
    prepare = controls.CompoundDamageRuntimeTests.prepare
    checkpoint = controls.CompoundDamageRuntimeTests.checkpoint
    cast = controls.CompoundDamageRuntimeTests.cast
    resolve = controls.CompoundDamageRuntimeTests.resolve
    replay = controls.CompoundDamageRuntimeTests.replay
    binding = controls.CompoundDamageRuntimeTests.binding
    actions = controls.CompoundDamageRuntimeTests.actions
    private_replay = controls.CompoundDamageRuntimeTests.private_replay
    advance = journeys.TemporaryKeywordRemovalRuntimeTests.advance

    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / 'any-source-counters.sqlite3'
        build_fixture_database([ROOT / 'tests/fixtures/bound-effect-program-cards.json',
            ROOT / 'tests/fixtures/any-source-counter-cost-cards.json'], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Any source counters',
            [DeckEntry('Generic Bound Commander', 1, 'commander'), DeckEntry('Generic Bound Plains', 30)],
            ['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    def counter_action(self, session, source, amount=1):
        current = session.state.cards[source.object_id]
        return next(row for row in self.actions(session) if row['id'].startswith('activate:' + current.ref + ':')
            and row['cost_summary'].get('remove_source_counter', {}).get('schema_version') == 2
            and row['cost_summary']['remove_source_counter']['amount'] == amount)

    def test_seven_originals_close_with_real_distribution_forms_and_preserved_mana_constraints(self):
        for index, name in enumerate(NAMES):
            session = self.session(735000 + index)
            source = self.add(session.engine, name)
            source.counters['-1/-1'] = 2
            source.counters['stun'] = 1
            self.add(session.engine, 'Generic Bound Body', ref='any-counter-other-target')
            self.add(session.engine, 'Generic Bound Body', zone='graveyard', ref='any-counter-grave-target')
            self.prepare(session, mana={'C': 10, 'W': 10, 'U': 10, 'B': 10, 'R': 10})
            card = self.db.lookup(name)
            ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile='commander_review')
            self.assertEqual('exact', ir.status, (name, ir.material_residuals))
            self.assertTrue(self.binding(session, source)['strict_capability_ready'], name)
            status = card_semantic_status(card, session.engine.semantics, db=self.db,
                capability_registry=self.registry, capability_profile='commander_review')
            self.assertEqual('fully_playable', status['status'], (name, status['unresolved']))
            actions = [row for row in self.actions(session) if row['id'].startswith('activate:' + source.ref + ':')
                and row['cost_summary'].get('remove_source_counter', {}).get('schema_version') == 2]
            self.assertTrue(actions, name)
            for action in actions:
                field = action['choice_schema']['source_counter_payment']
                self.assertTrue(field['legal_values'])
                self.assertTrue(all(label.startswith('Remove ') for label in field['option_labels'].values()))
                form = build_action_form(action, decision_kind='priority', context=session.packet('pilot:A', full=True)['decision']['ctx'])
                select = next(field for field in form['fields'] if field['name'] == 'source_counter_payment')
                self.assertEqual('select', select['control'])
                self.assertTrue(all(option['label'].startswith('Remove ') for option in select['options']))

    def test_actual_loch_cast_chooses_minus_counter_before_private_draw_and_rejects_bad_selection(self):
        session = self.session(735020)
        source = self.add(session.engine, 'Loch Mare', zone='hand')
        private = self.add(session.engine, 'Generic Bound Growth', seat='C', zone='hand')
        self.prepare(session, mana={'C': 10, 'U': 10})
        self.checkpoint(session)
        self.cast(session, 'A', source)
        self.assertIsNone(self.resolve(session))
        current = session.state.cards[source.object_id]
        self.assertEqual(3, current.counters.get('-1/-1', 0))
        before_stats = tuple(session.engine._numeric_stat(current.object_id, field) for field in ('power', 'toughness'))
        action = self.counter_action(session, source)
        choice = next(key for key, label in action['choice_schema']['source_counter_payment']['option_labels'].items()
            if label == 'Remove 1 -1/-1 counter')
        before = authoritative_state_hash(session.state)
        good = {'action_id': action['id'], 'source_counter_payment': choice, 'pay': 'auto'}
        for actor, command in (('pilot:B', good), ('pilot:A', {**good, 'source_counter_payment': 'forged'}),
                               ('pilot:A', {'action_id': action['id'], 'pay': 'auto'}),
                               ('pilot:A', {**good, 'source_counter_payment': True})):
            rejected = session.act(actor, command)
            self.assertFalse(rejected.ok)
            self.assertEqual(before, authoritative_state_hash(session.state))
        hand = len(session.state.players['A'].zones['hand'])
        result = session.act('pilot:A', good)
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(2, session.state.cards[source.object_id].counters.get('-1/-1', 0))
        self.assertEqual(tuple(value + 1 for value in before_stats),
            tuple(session.engine._numeric_stat(source.object_id, field) for field in ('power', 'toughness')))
        self.assertEqual(hand, len(session.state.players['A'].zones['hand']))
        session = self.replay(session, load=True)
        self.assertIsNone(self.resolve(session))
        self.assertEqual(hand + 1, len(session.state.players['A'].zones['hand']))
        self.private_replay(session, private)

    def test_fresh_loch_witness_kills_omitted_counter_payment(self):
        method = 'test_actual_loch_cast_chooses_minus_counter_before_private_draw_and_rejects_bad_selection'
        with patch('quorune.rules.activation.counter_costs.commit_counter_removals', return_value=None):
            with self.assertRaises(AssertionError):
                getattr(type(self)(method), method)()

    def pay_counter_ability(self, session, source, removals, *, targets=(), payment=None):
        amount = sum(removals.values())
        action = self.counter_action(session, source, amount)
        current = session.state.cards[source.object_id]
        cost = SourceCounterRemovalCost.from_dict(action['cost_summary']['remove_source_counter'])
        choice = next(choice for choice in source_counter_payment_choices(current, cost)
            if choice.removals == tuple(sorted(removals.items())))
        self.assertIn(choice.choice_id, action['choice_schema']['source_counter_payment']['legal_values'])
        command = {'action_id': action['id'], 'source_counter_payment': choice.choice_id,
            'targets': list(targets), 'pay': 'auto' if payment is None else 'manual'}
        if payment is not None:
            command['payment'] = payment
        if action.get('cost_options'):
            command['cost_option'] = action['cost_options'][0]['id']
        accepted = session.act('pilot:A', command)
        self.assertTrue(accepted.ok, accepted.summary)

    def test_actual_loch_two_distinct_kinds_then_keyword_removal_keep_exact_resource_and_tap_consequences(self):
        session = self.session(735030)
        source = self.add(session.engine, 'Loch Mare')
        source.counters.update({'-1/-1': 1, 'stun': 1, 'flying': 1})
        source.tapped = True
        target = self.add(session.engine, 'Generic Bound Body', seat='D')
        private = self.add(session.engine, 'Generic Bound Growth', seat='C', zone='hand')
        self.prepare(session, mana={'C': 3, 'U': 2})
        self.checkpoint(session)
        before_stats = tuple(session.engine._numeric_stat(source.object_id, field) for field in ('power', 'toughness'))
        self.assertIn('flying', session.engine._combat_keywords(source))
        self.pay_counter_ability(session, source, {'-1/-1': 1, 'stun': 1}, targets=[target.ref], payment={'C': 2, 'U': 1})
        self.assertTrue(session.state.cards[source.object_id].tapped)
        self.assertEqual(0, session.state.cards[source.object_id].counters.get('stun', 0))
        self.assertEqual(tuple(value + 1 for value in before_stats), tuple(session.engine._numeric_stat(source.object_id, field) for field in ('power', 'toughness')))
        self.assertIsNone(self.resolve(session))
        self.assertEqual((True, 1), (session.state.cards[target.object_id].tapped,
            session.state.cards[target.object_id].counters.get('stun', 0)))
        hand = len(session.state.players['A'].zones['hand'])
        self.pay_counter_ability(session, source, {'flying': 1}, payment={'C': 1, 'U': 1})
        self.assertNotIn('flying', session.engine._combat_keywords(session.state.cards[source.object_id]))
        self.assertTrue(session.state.cards[source.object_id].tapped)
        session = self.replay(session, load=True)
        self.assertIsNone(self.resolve(session))
        self.assertEqual(hand + 1, len(session.state.players['A'].zones['hand']))
        self.private_replay(session, private)

    def test_actual_burdened_and_gnarlbark_pay_counter_kinds_before_indestructible_or_weakening(self):
        for index, name in enumerate(('Burdened Stoneback', 'Gnarlbark Elm')):
            session = self.session(735040 + index)
            source = self.add(session.engine, name)
            amount = 1 if index == 0 else 2
            source.counters['-1/-1'] = amount
            target = self.add(session.engine, 'Generic Bound Body', ref='source-counter-target')
            response = self.add(session.engine, 'Murder', seat='B', zone='hand')
            private = self.add(session.engine, 'Generic Bound Growth', seat='C', zone='hand')
            session.state.players['B'].mana_pool.update({'C': 1, 'B': 2})
            self.prepare(session, mana={'C': 2, 'W': 1, 'B': 1})
            self.checkpoint(session)
            self.pay_counter_ability(session, source, {'-1/-1': amount}, targets=[target.ref])
            self.assertEqual(0, session.state.cards[source.object_id].counters.get('-1/-1', 0))
            self.assertIsNone(self.resolve(session))
            if index == 0:
                self.assertIn('indestructible', session.engine._combat_keywords(session.state.cards[target.object_id]))
                self.cast(session, 'B', response, [target.ref])
                self.assertIsNone(self.resolve(session))
                self.assertEqual('battlefield', session.state.cards[target.object_id].zone)
            else:
                self.assertEqual((0, 4), tuple(session.engine._numeric_stat(target.object_id, field) for field in ('power', 'toughness')))
            self.private_replay(session, private)

    def test_actual_reaping_hybrid_payment_returns_only_the_selected_small_graveyard_creature(self):
        session = self.session(735050)
        source = self.add(session.engine, 'Reaping Willow')
        source.counters['-1/-1'] = 2
        target = self.add(session.engine, 'Generic Bound Body', zone='graveyard', ref='reaping-small')
        other = self.add(session.engine, 'Generic Bound Body', zone='graveyard', ref='reaping-unselected')
        private = self.add(session.engine, 'Generic Bound Growth', seat='C', zone='hand')
        self.prepare(session, mana={'C': 1, 'W': 1})
        self.checkpoint(session)
        self.pay_counter_ability(session, source, {'-1/-1': 2}, targets=[target.ref])
        self.assertEqual(0, sum(session.state.players['A'].mana_pool.values()))
        session = self.replay(session, load=True)
        self.assertIsNone(self.resolve(session))
        self.assertEqual(('battlefield', 'A'), (session.state.cards[target.object_id].zone, session.state.cards[target.object_id].controller))
        self.assertEqual('graveyard', session.state.cards[other.object_id].zone)
        self.private_replay(session, private)

    def test_actual_hovel_hybrid_counter_payment_grants_its_other_target_power_and_flying(self):
        session = self.session(735060)
        source = self.add(session.engine, 'Hovel Hurler')
        source.counters['-1/-1'] = 1
        target = self.add(session.engine, 'Generic Bound Body', ref='hovel-other')
        private = self.add(session.engine, 'Generic Bound Growth', seat='C', zone='hand')
        self.prepare(session, mana={'R': 2})
        self.checkpoint(session)
        self.pay_counter_ability(session, source, {'-1/-1': 1}, targets=[target.ref])
        session = self.replay(session, load=True)
        self.assertIsNone(self.resolve(session))
        self.assertEqual((3, 6), tuple(session.engine._numeric_stat(target.object_id, field) for field in ('power', 'toughness')))
        self.assertIn('flying', session.engine._combat_keywords(session.state.cards[target.object_id]))
        self.private_replay(session, private)

    def test_actual_bramble_counter_payment_prevents_the_selected_creature_from_blocking(self):
        session = self.session(735070)
        source = self.add(session.engine, 'Brambleback Brute')
        source.counters['-1/-1'] = 1
        attacker = self.add(session.engine, 'Generic Bound Body', ref='bramble-attacker')
        blocker = self.add(session.engine, 'Generic Bound Body', seat='B', ref='bramble-blocker')
        self.add(session.engine, 'Generic Bound Body', seat='B', ref='bramble-legal-blocker')
        private = self.add(session.engine, 'Generic Bound Growth', seat='C', zone='hand')
        self.prepare(session, mana={'C': 1, 'R': 1})
        self.checkpoint(session)
        self.pay_counter_ability(session, source, {'-1/-1': 1}, targets=[blocker.ref])
        self.assertIsNone(self.resolve(session))
        self.advance(session, lambda s: s.state.pending_decision is not None and s.state.pending_decision.kind == 'combat.attackers')
        self.assertTrue(session.act('pilot:A', {'action_id': 'attack', 'attacks': {attacker.ref: 'B'}}).ok)
        self.advance(session, lambda s: s.state.pending_decision is not None and s.state.pending_decision.kind == 'combat.blockers')
        before = authoritative_state_hash(session.state)
        rejected = session.act('pilot:B', {'action_id': 'block', 'blocks': {blocker.ref: [attacker.ref]}})
        self.assertFalse(rejected.ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        self.assertTrue(session.act('pilot:B', {'action_id': 'block', 'blocks': {}}).ok)
        self.advance(session, lambda s: s.state.players['B'].life == 38)
        self.assertEqual('battlefield', session.state.cards[blocker.object_id].zone)
        self.private_replay(session, private)

    def test_actual_moonlit_keeps_sorcery_timing_and_its_paid_minus_counter_draw(self):
        session = self.session(735080)
        source = self.add(session.engine, 'Moonlit Lamenter')
        source.counters['-1/-1'] = 1
        self.prepare(session, active='B', mana={'C': 1, 'W': 1})
        self.assertFalse(any(row['id'].startswith('activate:' + source.ref + ':') for row in self.actions(session)))
        session = self.session(735081)
        source = self.add(session.engine, 'Moonlit Lamenter')
        source.counters['-1/-1'] = 1
        private = self.add(session.engine, 'Generic Bound Growth', seat='C', zone='hand')
        self.prepare(session, mana={'C': 1, 'W': 1})
        self.checkpoint(session)
        hand = len(session.state.players['A'].zones['hand'])
        self.pay_counter_ability(session, source, {'-1/-1': 1})
        session = self.replay(session, load=True)
        self.assertIsNone(self.resolve(session))
        self.assertEqual(hand + 1, len(session.state.players['A'].zones['hand']))
        self.assertEqual(0, session.state.cards[source.object_id].counters.get('-1/-1', 0))
        self.private_replay(session, private)

    def test_departed_burdened_source_does_not_refund_payment_or_remove_its_stack_effect(self):
        session = self.session(735090)
        source = self.add(session.engine, 'Burdened Stoneback')
        source.counters.update({'stun': 1})
        target = self.add(session.engine, 'Generic Bound Body', seat='D')
        response = self.add(session.engine, 'Unsummon', seat='B', zone='hand')
        private = self.add(session.engine, 'Generic Bound Growth', seat='C', zone='hand')
        session.state.players['B'].mana_pool['U'] = 1
        self.prepare(session, mana={'C': 1, 'W': 1})
        self.checkpoint(session)
        self.pay_counter_ability(session, source, {'stun': 1}, targets=[target.ref])
        self.cast(session, 'B', response, [source.ref])
        session = self.replay(session, load=True)
        self.assertIsNone(self.resolve(session))
        self.assertEqual('hand', session.state.cards[source.object_id].zone)
        self.assertEqual(0, sum(session.state.players['A'].mana_pool.values()))
        self.assertIn('indestructible', session.engine._combat_keywords(session.state.cards[target.object_id]))
        self.private_replay(session, private)

    def test_runtime_cannot_omit_any_kind_source_counter_authority(self):
        from quorune.card_programs.binding import bind_semantic_program_runtime
        session = self.session(735100)
        source = self.add(session.engine, 'Loch Mare')
        program = next(program for program in session.engine.semantics.programs_for_oracle(source.oracle_id)
            if program.cost_schema and program.cost_schema.get('source_counter_removal_cost', {}).get('schema_version') == 2)
        deps = tuple(cap for cap in program.capability_dependencies if cap != ANY_SOURCE_COUNTER_COST_CAPABILITY)
        missing = replace(program, capability_dependencies=deps,
            capability_closure=self.registry.closure(deps, profile='commander_review').to_dict())
        bound = bind_semantic_program_runtime(missing, capability_registry=self.registry, profile='commander_review')
        self.assertFalse(bound['strict'])
        self.assertIn('capability:undeclared_runtime_dependency:' + ANY_SOURCE_COUNTER_COST_CAPABILITY, bound['blockers'])

    def test_unknown_amounts_and_mixed_source_costs_remain_residual(self):
        card = self.db.lookup('Moonlit Lamenter')
        for text in (card.oracle_text.replace('Remove a counter', 'Remove three counters'),
                     card.oracle_text.replace('Remove a counter', 'Remove X counters'),
                     card.oracle_text.replace('Remove a counter', 'Remove all counters'),
                     card.oracle_text.replace('from this creature', 'from another creature'),
                     card.oracle_text + '\nWhenever a player sneezes, draw a card.'):
            self.assertNotEqual('exact', compile_oracle_card(replace(card, oracle_text=text),
                capability_registry=self.registry, capability_profile='commander_review').status, text)

    def test_fresh_original_admission_kills_omitted_any_kind_source_compiler(self):
        method = 'test_seven_originals_close_with_real_distribution_forms_and_preserved_mana_constraints'
        with patch('quorune.compiler.activated_counter_costs.fixed_any_source_counter_activation', side_effect=lambda ability: ability):
            with self.assertRaises(AssertionError):
                getattr(type(self)(method), method)()
