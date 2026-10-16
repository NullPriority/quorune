from __future__ import annotations

"""CR 702.157: repeated fees, sealed count, simultaneous token entries."""

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
import test_paid_entry_copy as paid_actions


CAPABILITY = 'casting.additional.squad_fixed_mana'


def squad_record(text):
    return replace(record(text, 'Creature'), keywords=('Squad', 'Offspring'))


class SquadCompilerTests(unittest.TestCase):
    def test_fixed_squad_links_one_repeatable_fee_to_a_sealed_copy_count(self):
        ir = compile_oracle_card(squad_record('Squad {2}'), capability_registry=load_default_capability_registry(),
            capability_profile='commander_review')
        self.assertEqual('exact', ir.status, ir.material_residuals)
        cost, entry = ir.faces[0].nodes
        self.assertIn(CAPABILITY, entry.capability_dependencies)
        self.assertEqual('squad', cost.handlers[0]['paid_copy']['keyword'])
        self.assertEqual('$context.paid_entry_copy_count', entry.effects[0]['quantity'])
        self.assertIsNone(entry.effects[0]['copy_spec']['exception']['power'])
        self.assertEqual('paid_entry_copy_instance', entry.event_condition['field'])

    def test_nonmana_open_and_multiple_instances_or_missing_owners_fail_closed(self):
        registry = load_default_capability_registry()
        self.assertEqual('exact', compile_oracle_card(squad_record('Squad {2}'),
            capability_registry=registry, capability_profile='commander_review').status)
        for text in ('Squad {X}', 'Squad {W/U}', 'Squad—Exile four cards from your graveyard.',
                     'Squad {1}, Discard a card.', 'Squad {2}\nSquad {3}', 'Squad {2}\nOffspring {2}'):
            with self.subTest(text=text):
                self.assertNotEqual('exact', compile_oracle_card(squad_record(text),
                    capability_registry=registry, capability_profile='commander_review').status)
        raw = json.loads((ROOT / 'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        for owner in (CAPABILITY, 'casting.additional.paid_entry_copy', 'token.creation.fixed_copy'):
            changed = deepcopy(raw)
            next(row for row in changed['capabilities'] if row['id'] == owner).update(status='blocked', blockers=['Squad owner withdrawn'])
            self.assertNotEqual('exact', compile_oracle_card(squad_record('Squad {2}'),
                capability_registry=CapabilityRegistry(changed), capability_profile='commander_review').status)

    def test_repeat_prices_and_integer_form_have_no_two_payment_limit(self):
        from quorune.paid_entry_copy_model import PaidEntryCopySpec, validate_paid_entry_copy_fact
        from quorune.rules.casting.paid_entry_copy_costs import expand_paid_entry_copy_costs
        from quorune.choice_forms import build_action_form
        from quorune.selection.copy_targets import stack_copy_context
        from types import SimpleNamespace
        vector = dict.fromkeys(('GENERIC', 'W', 'U', 'B', 'R', 'G', 'C'), 0)
        fee = {**vector, 'GENERIC': 2}
        spec = PaidEntryCopySpec('front:n1', fee, keyword='squad')
        options = [{'id': 'normal', 'kind': 'normal', 'requirements': {**vector, 'GENERIC': 2, 'U': 1}}]
        for count in (1, 2, 4, 97):
            rows = expand_paid_entry_copy_costs(options, {'paid_entry_copy': spec.to_dict()},
                response={'cost_option': 'normal+squad:front:n1', 'squad_count': count}, hint=False)
            paid = next(row for row in rows if '+squad:' in row['id'])
            self.assertEqual(2 + 2 * count, paid['requirements']['GENERIC'])
            self.assertEqual(count, paid['paid_entry_copy']['count'])
            self.assertEqual(count, validate_paid_entry_copy_fact(spec.payment_fact(count))['count'])
        hinted = expand_paid_entry_copy_costs(options, {'paid_entry_copy': spec.to_dict()}, response={}, hint=True)
        form = build_action_form({'id': 'cast:source', 'cost_options': hinted}, decision_kind='priority', context={})
        variant = next(row for row in form['variants']['options'] if '+squad:' in row['value'])
        field, = variant['fields']
        self.assertEqual(('squad_count', 'integer', 1), (field['name'], field['control'], field['minimum']))
        self.assertNotIn('maximum', field)
        for value in (True, -1, 0, 1.5, '2'):
            with self.subTest(value=value):
                self.assertFalse(expand_paid_entry_copy_costs(options, {'paid_entry_copy': spec.to_dict()},
                    response={'cost_option': 'normal+squad:front:n1', 'squad_count': value}, hint=False))
        independent_x = [{**options[0], 'x_value': 11}]
        priced = expand_paid_entry_copy_costs(independent_x, {'paid_entry_copy': spec.to_dict()},
            response={'cost_option': 'normal+squad:front:n1', 'squad_count': 3, 'x': 11}, hint=False)
        self.assertEqual(11, next(row for row in priced if '+squad:' in row['id'])['x_value'])
        source = SimpleNamespace(context={'paid_entry_copy': spec.payment_fact(6)}, ref='squad-spell', label='Squad spell')
        copied = stack_copy_context(source, {}, {}, permanent_spell=True, characteristics={'name': 'Squad spell'})
        self.assertEqual(6, copied['paid_entry_copy']['count'])
        copied['paid_entry_copy']['count'] = 1
        self.assertEqual(6, source.context['paid_entry_copy']['count'])


class SquadRuntimeTests(unittest.TestCase):
    register_fixture_cards_on_add = True
    session = paid_actions.PaidEntryCopyRuntimeTests.session
    add = paid_actions.PaidEntryCopyRuntimeTests.add
    checkpoint = paid_actions.PaidEntryCopyRuntimeTests.checkpoint
    resolve = paid_actions.PaidEntryCopyRuntimeTests.resolve
    replay = paid_actions.PaidEntryCopyRuntimeTests.replay
    prepare = paid_actions.PaidEntryCopyRuntimeTests.prepare
    cast = paid_actions.PaidEntryCopyRuntimeTests.cast
    assert_closed = paid_actions.PaidEntryCopyRuntimeTests.assert_closed
    tokens = paid_actions.PaidEntryCopyRuntimeTests.tokens

    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        path = Path(cls.temp.name) / 'squad.sqlite3'
        build_fixture_database([ROOT / 'tests/fixtures/bound-effect-program-cards.json',
            ROOT / 'tests/fixtures/paid-entry-copy-cards.json', ROOT / 'tests/fixtures/squad-paid-entry-copy-cards.json'], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Squad witness', [DeckEntry('Generic Bound Commander', 1, 'commander'),
            DeckEntry('Generic Bound Plains', 30)], ['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temp.cleanup()

    def drain(self, session):
        for _ in range(128):
            decision = self.resolve(session)
            if decision is None:
                return
            self.assertEqual('trigger.order', decision.kind)
            packet = session.packet(session.pending_principals()[0], full=True)['decision']
            result = session.act(session.pending_principals()[0], {'action_id': 'order',
                'triggers': [row['id'] for row in packet['ctx']['triggers']]})
            self.assertTrue(result.ok, result.summary)
        self.fail('Squad triggers did not finish')

    def squad_cast(self, session, source, count):
        self.assert_closed(session, source)
        action = next(row for row in session.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']
            if row.get('card') == source.ref and row['id'].startswith('cast:'))
        options = [row for row in action['cost_options'] if ('+squad:' in row['id']) == (count > 0)]
        self.assertEqual(1, len(options), action)
        option, = options
        command = {'action_id': action['id'], 'cost_option': option['id'], 'pay': 'auto'}
        if count:
            command['squad_count'] = count
        before = authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:C', command).ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        result = session.act('pilot:A', command)
        self.assertTrue(result.ok, result.summary)
        return command

    def test_actual_sicarian_zero_one_two_and_six_payments_draw_for_each_entry(self):
        for count in (0, 1, 2, 6):
            with self.subTest(count=count):
                session = self.session(422000 + count)
                source = self.add(session.engine, 'Sicarian Infiltrator', zone='hand', ref='squad-original')
                hidden = self.add(session.engine, 'Generic Bound Growth', seat='C', zone='hand', ref='squad-private')
                self.prepare(session, mana={'C': 2 + count * 2, 'U': 1})
                self.checkpoint(session)
                library = len(session.state.players['A'].zones['library'])
                hand = len(session.state.players['A'].zones['hand'])
                self.squad_cast(session, source, count)
                self.assertEqual(0, session.state.players['A'].mana_pool['C'])
                if count:
                    self.assertEqual(count, session.state.stack[-1].context['paid_entry_copy']['count'])
                session = self.replay(session, load=True)
                self.drain(session)
                self.assertEqual(count, len(self.tokens(session, source)))
                self.assertEqual(library - count - 1, len(session.state.players['A'].zones['library']))
                self.assertEqual(hand + count, len(session.state.players['A'].zones['hand']))
                for viewer in 'ABD':
                    self.assertNotIn(hidden.ref, json.dumps(session.packet('pilot:' + viewer, full=True)))
                self.replay(session, load=True)

    def test_securitron_two_copies_enter_together_and_each_gets_three_counters(self):
        session = self.session(422010)
        source = self.add(session.engine, 'Securitron Squadron', zone='hand', ref='squad-securitron')
        self.prepare(session, mana={'C': 7, 'W': 1})
        self.checkpoint(session)
        self.squad_cast(session, source, 2)
        session = self.replay(session, load=True)
        self.drain(session)
        copies = self.tokens(session, source)
        self.assertEqual(2, len(copies))
        self.assertEqual([3, 3], sorted(card.counters.get('+1/+1', 0) for card in copies))
        self.assertEqual(0, session.state.cards[source.object_id].counters.get('+1/+1', 0))
        self.replay(session, load=True)

    def test_squad_invalid_unaffordable_and_unpaid_count_submissions_roll_back(self):
        session = self.session(422020)
        source = self.add(session.engine, 'Sicarian Infiltrator', zone='hand', ref='squad-invalid')
        self.assert_closed(session, source)
        self.prepare(session, mana={'C': 6, 'U': 1})
        self.checkpoint(session)
        action = next(row for row in session.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']
            if row.get('card') == source.ref and row['id'].startswith('cast:'))
        paid = next(row for row in action['cost_options'] if '+squad:' in row['id'])
        before = authoritative_state_hash(session.state)
        for count in (True, -1, 0, 1.5, '2', 3, 999999):
            with self.subTest(count=count):
                result = session.act('pilot:A', {'action_id': action['id'], 'cost_option': paid['id'],
                    'squad_count': count, 'pay': 'auto'})
                self.assertFalse(result.ok)
                self.assertEqual(before, authoritative_state_hash(session.state))
        self.assertFalse(session.act('pilot:A', {'action_id': action['id'], 'cost_option': 'normal',
            'squad_count': 2, 'pay': 'auto'}).ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        self.squad_cast(session, source, 2)
        self.drain(session)
        self.assertEqual(2, len(self.tokens(session, source)))
        self.replay(session, load=True)

    def test_reduced_squad_pending_blink_keeps_paid_count_and_private_draws(self):
        session = self.session(422030)
        source = self.add(session.engine, 'Sicarian Infiltrator', zone='hand', ref='squad-pending-source')
        blink = self.add(session.engine, 'Generic Paid Entry Blink', zone='hand', ref='squad-pending-blink')
        self.add(session.engine, 'Generic Paid Entry Reduction')
        self.prepare(session, mana={'C': 2, 'U': 1})
        self.checkpoint(session)
        library = len(session.state.players['A'].zones['library'])
        hand = set(session.state.players['A'].zones['hand'])
        self.squad_cast(session, source, 2)
        self.assertEqual(0, session.state.players['A'].mana_pool['C'])
        self.resolve(session)
        self.assertEqual('trigger.order', session.state.pending_decision.kind)
        identity = session.state.cards[source.object_id].logical_object_id
        packet = session.packet('pilot:A', full=True)['decision']
        ordered = session.act('pilot:A', {'action_id': 'order',
            'triggers': [row['id'] for row in packet['ctx']['triggers']]})
        self.assertTrue(ordered.ok, ordered.summary)
        session = self.replay(session, load=True)
        self.cast(session, 'A', blink, [source.ref])
        self.drain(session)
        self.assertNotEqual(identity, session.state.cards[source.object_id].logical_object_id)
        self.assertEqual(2, len(self.tokens(session, source)))
        self.assertEqual(library - 4, len(session.state.players['A'].zones['library']))
        drawn = set(session.state.players['A'].zones['hand']) - hand
        self.assertEqual(4, len(drawn))
        for viewer in 'BCD':
            projected = deepcopy(session.packet('pilot:' + viewer, full=True))
            if isinstance(projected.get('decision'), dict):
                projected['decision'].pop('id', None)
            def strings(value):
                if isinstance(value, dict):
                    return {text for child in value.values() for text in strings(child)}
                if isinstance(value, list):
                    return {text for child in value for text in strings(child)}
                return {value} if isinstance(value, str) else set()
            public_strings = strings(projected)
            for object_id in drawn:
                self.assertNotIn(session.state.cards[object_id].ref, public_strings)
        self.replay(session, load=True)
