from __future__ import annotations

"""CR 702.175: paying Offspring is distinct from copying a permanent."""

from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest

from common import ROOT
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_granted_skulk import record
import test_bound_effect_programs as actions
import test_self_public_zone_casting as zone_actions


CAPABILITY = 'casting.additional.paid_entry_copy'


def offspring_record(text):
    return replace(record(text, 'Creature'), keywords=('Offspring',))


class PaidEntryCopyCompilerTests(unittest.TestCase):
    def test_paid_facts_reject_malformed_costs_counts_and_keep_copied_spell_choices(self):
        from types import SimpleNamespace
        from quorune.paid_entry_copy_model import PaidEntryCopySpec, validate_paid_entry_copy_fact
        from quorune.selection.copy_targets import stack_copy_context
        from quorune.zone_object_state import mark_resolving_paid_entry_copy, stack_entry_cast_facts
        from quorune.model import CardInstance
        vector = dict.fromkeys(('GENERIC', 'W', 'U', 'B', 'R', 'G', 'C'), 0)
        vector['GENERIC'] = 2
        spec = PaidEntryCopySpec('front:n1', vector)
        vector['GENERIC'] = 100
        self.assertEqual(2, spec.requirements['GENERIC'])
        for key, value in (('count', 2), ('count', True), ('schema_version', True), ('keyword', 'unknown'), ('instance_id', '')):
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                validate_paid_entry_copy_fact({**spec.payment_fact(), key: value})
        with self.assertRaises(ValueError):
            PaidEntryCopySpec('front:n1', {**dict(spec.requirements), 'GENERIC': True})
        target = SimpleNamespace(context={'paid_entry_copy': spec.payment_fact()}, ref='original-stack', label='Original')
        copied = stack_copy_context(target, {}, {}, permanent_spell=True, characteristics={'name': 'Original'})
        self.assertEqual(target.context['paid_entry_copy'], copied['paid_entry_copy'])
        card = CardInstance(object_id='copy-object', ref='copy-ref', oracle_id='fixture:paid-copy',
            printed_name='Copy', owner='B', controller='B', zone='stack', object_kind='spell_copy')
        mark_resolving_paid_entry_copy(card, copied)
        self.assertEqual(spec.payment_fact(), stack_entry_cast_facts(card)['paid_entry_copy'])
        copied['paid_entry_copy']['count'] = 2
        self.assertEqual(1, target.context['paid_entry_copy']['count'])

    def test_fixed_offspring_emits_separate_cost_and_paid_entry_copy_contracts(self):
        registry = load_default_capability_registry()
        ir = compile_oracle_card(offspring_record('Offspring {2}'),
            capability_registry=registry, capability_profile='commander_review')
        self.assertEqual('exact', ir.status, ir.material_residuals)
        cost, entry = ir.faces[0].nodes
        self.assertEqual('cast.cost', cost.event)
        self.assertEqual('permanent.enter.self', entry.event)
        self.assertEqual('paid_entry_copy_instance', entry.event_condition['field'])
        self.assertIn(CAPABILITY, cost.capability_dependencies)
        self.assertIn(CAPABILITY, entry.capability_dependencies)
        effect, = entry.effects
        self.assertEqual(('create_token', 1), (effect['op'], effect['quantity']))
        self.assertEqual('source', effect['copy_spec']['origin'])
        self.assertEqual((1, 1), (effect['copy_spec']['exception']['power'], effect['copy_spec']['exception']['toughness']))

    def test_open_costs_multiple_instances_and_missing_copy_owners_remain_residual(self):
        registry = load_default_capability_registry()
        self.assertEqual('exact', compile_oracle_card(offspring_record('Offspring {2}'),
            capability_registry=registry, capability_profile='commander_review').status)
        for text in ('Offspring {X}', 'Offspring {W/U}', 'Offspring—Sacrifice a creature.',
                     'Offspring {2}\nOffspring {3}'):
            with self.subTest(text=text):
                self.assertNotEqual('exact', compile_oracle_card(offspring_record(text),
                    capability_registry=registry, capability_profile='commander_review').status)
        raw = json.loads((ROOT / 'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        for owner in (CAPABILITY, 'token.creation.fixed_copy'):
            changed = deepcopy(raw)
            next(row for row in changed['capabilities'] if row['id'] == owner).update(status='blocked', blockers=['paid-copy owner withdrawn'])
            self.assertNotEqual('exact', compile_oracle_card(offspring_record('Offspring {2}'),
                capability_registry=CapabilityRegistry(changed), capability_profile='commander_review').status)


class PaidEntryCopyRuntimeTests(unittest.TestCase):
    register_fixture_cards_on_add = True
    session = actions.BoundEffectProgramRuntimeTests.session
    add = actions.BoundEffectProgramRuntimeTests.add
    checkpoint = actions.BoundEffectProgramRuntimeTests.checkpoint
    resolve = actions.BoundEffectProgramRuntimeTests.resolve
    replay = actions.BoundEffectProgramRuntimeTests.replay
    prepare = zone_actions.SelfPublicZoneRuntimeTests.prepare
    cast = zone_actions.SelfPublicZoneRuntimeTests.cast
    assert_closed = zone_actions.SelfPublicZoneRuntimeTests.assert_closed

    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        path = Path(cls.temp.name) / 'paid-entry-copy.sqlite3'
        build_fixture_database([ROOT / 'tests/fixtures/bound-effect-program-cards.json',
            ROOT / 'tests/fixtures/granted-skulk-cards.json', ROOT / 'tests/fixtures/event-player-subject-cards.json',
            ROOT / 'tests/fixtures/paid-entry-copy-cards.json'], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Paid entry copy witness', [DeckEntry('Generic Bound Commander', 1, 'commander'),
            DeckEntry('Generic Bound Plains', 30)], ['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temp.cleanup()

    def cast_offspring(self, session, source, *, paid):
        self.assert_closed(session, source)
        rows = session.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']
        action = next(row for row in rows if row.get('card') == source.ref and row['id'].startswith('cast:'))
        options = [row for row in action['cost_options'] if ('+offspring:' in row['id']) == paid]
        self.assertEqual(1, len(options), action)
        option, = options
        before = authoritative_state_hash(session.state)
        command = {'action_id': action['id'], 'cost_option': option['id'], 'pay': 'auto'}
        self.assertFalse(session.act('pilot:C', command).ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        result = session.act('pilot:A', command)
        self.assertTrue(result.ok, result.summary)
        return option

    def test_offspring_fee_is_added_before_reduction_and_unaffordable_branch_rejects(self):
        for reduction in (False, True):
            with self.subTest(reduction=reduction):
                session = self.session(421030 + reduction)
                source = self.add(session.engine, 'Rust-Shield Rampager', zone='hand', ref='paid-copy-cost-boundary')
                if reduction:
                    self.add(session.engine, 'Generic Paid Entry Reduction')
                self.assert_closed(session, source)
                self.prepare(session, mana={'C': 1 if reduction else 3, 'G': 1})
                self.checkpoint(session)
                if reduction:
                    option = self.cast_offspring(session, source, paid=True)
                    self.assertEqual(1, option['requirements']['GENERIC'])
                    self.resolve(session)
                    self.assertEqual(1, len(self.tokens(session, source)))
                else:
                    row = next(a for a in session.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']
                        if a.get('card') == source.ref and a['id'].startswith('cast:'))
                    self.assertFalse(any('+offspring:' in a['id'] for a in row['cost_options']))
                    ir = compile_oracle_card(self.db.lookup('Rust-Shield Rampager'), capability_registry=self.registry,
                        capability_profile='commander_review')
                    instance = ir.faces[0].nodes[1].event_condition['value']
                    before = authoritative_state_hash(session.state)
                    result = session.act('pilot:A', {'action_id': row['id'],
                        'cost_option': 'normal+offspring:' + instance, 'pay': 'auto'})
                    self.assertFalse(result.ok)
                    self.assertEqual(before, authoritative_state_hash(session.state))
                self.replay(session, load=True)

    def pending_entry(self, session, source):
        for _ in range(64):
            if session.state.cards[source.object_id].zone == 'battlefield' and session.state.stack:
                return
            self.assertEqual('priority', session.state.pending_decision.kind)
            self.assertTrue(session.act(session.pending_principals()[0], {'action_id': 'pass'}).ok)
        self.fail('Paid creature did not produce its entry trigger')

    def tokens(self, session, source):
        return [card for card in session.state.cards.values()
            if card.zone == 'battlefield' and card.is_token and card.oracle_id == source.oracle_id]

    def test_actual_rampager_paid_and_unpaid_total_price_copy_values_and_replay(self):
        for paid in (False, True):
            with self.subTest(paid=paid):
                session = self.session(421000 + paid)
                source = self.add(session.engine, 'Rust-Shield Rampager', zone='hand', ref='paid-copy-rampager')
                hidden = self.add(session.engine, 'Generic Bound Growth', seat='C', zone='hand', ref='paid-copy-private')
                self.prepare(session, mana={'C': 5, 'G': 1})
                self.checkpoint(session)
                option = self.cast_offspring(session, source, paid=paid)
                self.assertEqual(5 if paid else 3, option['requirements']['GENERIC'])
                self.assertEqual(0 if paid else 2, session.state.players['A'].mana_pool['C'])
                self.resolve(session)
                self.assertEqual('battlefield', session.state.cards[source.object_id].zone)
                copies = self.tokens(session, source)
                self.assertEqual(int(paid), len(copies))
                if paid:
                    data = session.engine._effective_card_data(copies[0])
                    self.assertEqual(('1', '1', '{3}{G}'), (str(data['power']), str(data['toughness']), data['mana_cost']))
                    self.assertEqual('A', copies[0].controller)
                for viewer in 'ABD':
                    self.assertNotIn(hidden.ref, json.dumps(session.packet('pilot:' + viewer, full=True)))
                self.replay(session, load=True)

    def test_pending_offspring_departure_blink_and_control_keep_original_paid_trigger(self):
        for action in ('bounce', 'blink', 'control'):
            with self.subTest(action=action):
                session = self.session(421010 + ('bounce', 'blink', 'control').index(action))
                source = self.add(session.engine, 'Rust-Shield Rampager', zone='hand', ref='paid-copy-departure')
                bounce = self.add(session.engine, 'Generic Skulk Bounce', zone='hand', ref='paid-copy-bounce')
                blink = self.add(session.engine, 'Generic Paid Entry Blink', zone='hand', ref='paid-copy-blink')
                control = self.add(session.engine, 'Generic Event Subject Control', seat='D', zone='hand', ref='paid-copy-control')
                self.prepare(session, mana={'C': 5, 'G': 1})
                session.state.players['D'].mana_pool['W'] = 1
                self.checkpoint(session)
                self.cast_offspring(session, source, paid=True)
                self.pending_entry(session, source)
                original_identity = session.state.cards[source.object_id].logical_object_id
                session = self.replay(session, load=True)
                self.cast(session, 'D' if action == 'control' else 'A',
                    control if action == 'control' else blink if action == 'blink' else bounce, [source.ref])
                self.resolve(session)
                copies = self.tokens(session, source)
                self.assertEqual(1, len(copies))
                self.assertEqual('A', copies[0].controller)
                original = session.state.cards[source.object_id]
                self.assertEqual('hand' if action == 'bounce' else 'battlefield', original.zone)
                if action == 'blink':
                    self.assertNotEqual(original_identity, original.logical_object_id)
                if action == 'control':
                    self.assertEqual('D', original.controller)
                self.replay(session, load=True)

    def test_actual_copied_mage_keeps_its_spell_trigger_without_recursive_offspring(self):
        session = self.session(421020)
        source = self.add(session.engine, 'Coruscation Mage', zone='hand', ref='paid-copy-mage')
        spell = self.add(session.engine, 'Generic Paid Entry Life', zone='hand', ref='paid-copy-life')
        self.prepare(session, mana={'C': 3, 'R': 1})
        self.checkpoint(session)
        self.cast_offspring(session, source, paid=True)
        self.resolve(session)
        self.assertEqual(1, len(self.tokens(session, source)))
        self.cast(session, 'A', spell)
        self.resolve(session)
        self.assertEqual('trigger.order', session.state.pending_decision.kind)
        packet = session.packet('pilot:A', full=True)['decision']
        order = [row['id'] for row in packet['ctx']['triggers']]
        self.assertEqual(2, len(order))
        before = authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:C', {'action_id': 'order', 'triggers': order}).ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        session = self.replay(session, load=True)
        ordered = session.act('pilot:A', {'action_id': 'order', 'triggers': order})
        self.assertTrue(ordered.ok, ordered.summary)
        self.resolve(session)
        self.assertEqual([38, 38, 38], [session.state.players[seat].life for seat in 'BCD'])
        self.assertEqual(41, session.state.players['A'].life)
        self.assertEqual(1, len(self.tokens(session, source)))
        self.replay(session, load=True)

    def test_actual_agate_copy_entry_and_later_creature_entry_use_distinct_observers(self):
        session = self.session(421040)
        source = self.add(session.engine, 'Agate Instigator', zone='hand', ref='paid-copy-agate')
        creature = self.add(session.engine, 'Generic Bound Body', zone='hand', ref='paid-copy-later-creature')
        self.prepare(session, mana={'C': 4, 'R': 2, 'W': 2, 'G': 2})
        self.checkpoint(session)
        self.cast_offspring(session, source, paid=True)
        self.assertEqual(0, session.state.players['A'].mana_pool['R'])
        self.resolve(session)
        self.assertEqual(1, len(self.tokens(session, source)))
        # The original observes the new copy; the copy does not observe itself
        # as "another" creature. Both observe a later creature normally.
        self.assertEqual([39, 39, 39], [session.state.players[s].life for s in 'BCD'])
        self.cast(session, 'A', creature)
        self.resolve(session)
        self.assertEqual('trigger.order', session.state.pending_decision.kind)
        order = [row['id'] for row in session.packet('pilot:A', full=True)['decision']['ctx']['triggers']]
        self.assertEqual(2, len(order))
        session = self.replay(session, load=True)
        result = session.act('pilot:A', {'action_id': 'order', 'triggers': order})
        self.assertTrue(result.ok, result.summary)
        self.resolve(session)
        self.assertEqual([37, 37, 37], [session.state.players[s].life for s in 'BCD'])
        self.assertEqual(1, len(self.tokens(session, source)))
        self.replay(session, load=True)
