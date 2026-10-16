from __future__ import annotations

"""CR 601.2/702.33c-e/400.7d: repeated fees and linked prospective counters."""

from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from common import ROOT
from quorune.carddb import CardDatabase
from quorune.card_programs import bind_card_program_runtime
from quorune.card_programs.adapters import compile_best_available_card_program
from quorune.deck import DeckDefinition, DeckEntry
from quorune.model import CardInstance
from quorune.multikicker_model import (
    FixedManaMultikickerSpec, MultikickerEntrySpec, MULTIKICKER_FIELD,
    MULTIKICKER_CAPABILITY, MULTIKICKER_ENTRY_CAPABILITY, validate_multikicker_fact,
)
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry
from quorune.rules.casting.multikicker_costs import expand_multikicker_costs
from quorune.selection.copy_targets import stack_copy_context
from quorune.semantic_runtime.multikicker import MultikickerEntryHandler, prospective_multikicker_fields
from quorune.semantic_runtime.zone_replacement_model import ZoneChangeSubjectSnapshot
from quorune.semantics import SemanticRegistry
from quorune.zone_object_state import mark_multikicker_paid, reset_card_after_zone_change
from scripts.build_test_database import build_fixture_database
from test_granted_skulk import record
import test_paid_entry_copy as paid_actions


FIXTURE = ROOT / 'tests/fixtures/multikicker-entry-counter-cards.json'
NAMES = ('Everflowing Chalice', 'Skitter of Lizards', 'Quag Vampires', 'Enclave Elite',
    'Flametongue Yearling', 'Joraga Warcaller', 'Gnarlid Pack', 'Apex Hawks')


class MultikickerValueTests(unittest.TestCase):
    def spec(self):
        return FixedManaMultikickerSpec('front:multikicker:line:1',
            {'GENERIC': 2, 'W': 0, 'U': 0, 'B': 0, 'R': 0, 'G': 0, 'C': 0})

    def test_repeat_costs_and_integer_form_are_instance_bound_and_strict(self):
        from quorune.choice_forms import build_action_form
        spec = self.spec()
        self.assertEqual(spec, FixedManaMultikickerSpec.from_dict(spec.to_dict()))
        base = [{'id': 'normal', 'kind': 'normal', 'requirements': {**dict(spec.requirements), 'GENERIC': 3}}]
        schema = {MULTIKICKER_FIELD: spec.to_dict()}
        option_id = 'normal+multikicker:' + spec.instance_id
        for count in (1, 2, 6, 97):
            rows = expand_multikicker_costs(base, schema,
                response={'cost_option': option_id, 'multikicker_count': count})
            paid = next(row for row in rows if row['id'] == option_id)
            self.assertEqual(3 + 2 * count, paid['requirements']['GENERIC'])
            self.assertEqual(spec.payment_fact(count), paid[MULTIKICKER_FIELD])
        hinted = expand_multikicker_costs(base, schema, hint=True)
        form = build_action_form({'id': 'cast:source', 'cost_options': hinted}, decision_kind='priority', context={})
        variant = next(row for row in form['variants']['options'] if row['value'] == option_id)
        field, = variant['fields']
        self.assertEqual(('multikicker_count', 'integer', 1), (field['name'], field['control'], field['minimum']))
        for value in (True, -1, 0, 1.5, '2'):
            with self.subTest(value=value):
                self.assertEqual([], expand_multikicker_costs(base, schema,
                    response={'cost_option': option_id, 'multikicker_count': value}))
        self.assertEqual(base, expand_multikicker_costs(base, schema, response={'multikicker_count': 0}))
        self.assertEqual([], expand_multikicker_costs(base, {}, response={'multikicker_count': 2}))
        self.assertEqual([], expand_multikicker_costs(base, schema,
            response={'cost_option': 'normal', 'multikicker_count': 2}))
        for change in ({'count': True}, {'count': 0}, {'cost_fingerprint': 'unbound'}, {'unknown': 1}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate_multikicker_fact({**spec.payment_fact(2), **change})
        self.assertEqual(3, base[0]['requirements']['GENERIC'])

    def test_spell_copy_retains_payment_but_new_permanent_incarnation_does_not(self):
        spec = self.spec()
        source = SimpleNamespace(context={MULTIKICKER_FIELD: spec.payment_fact(3)}, ref='source-spell', label='Source')
        copied = stack_copy_context(source, {}, {}, permanent_spell=True, characteristics={'name': 'Source'})
        card = CardInstance(object_id='copy', ref='copy-ref', oracle_id='fixture:copy',
            printed_name='Copy', owner='B', controller='B', zone='stack', object_kind='spell_copy')
        host = SimpleNamespace(state=SimpleNamespace(stack=[SimpleNamespace(card_object_id=card.object_id, context=copied)]))
        fields = prospective_multikicker_fields(host, card, 'battlefield')
        self.assertEqual(3, fields['multikicker_paid']['count'])
        self.assertEqual('kicked', fields['cast_option'])
        mark_multikicker_paid(card, copied)
        copied[MULTIKICKER_FIELD]['count'] = 1
        self.assertEqual(3, source.context[MULTIKICKER_FIELD]['count'])
        with self.assertRaises(ValueError):
            prospective_multikicker_fields(host, card, 'battlefield')
        reset_card_after_zone_change(card, destination='battlefield', stack_to_battlefield=True)
        card.zone = 'battlefield'
        self.assertNotIn(MULTIKICKER_FIELD, card.annotations)
        self.assertEqual({'multikicker_paid': None, 'cast_option': None},
            prospective_multikicker_fields(host, card, 'battlefield'))
        reset_card_after_zone_change(card, destination='exile', stack_to_battlefield=False)
        card.zone = 'exile'
        self.assertIsNone(prospective_multikicker_fields(host, card, 'battlefield')['multikicker_paid'])

    def test_entry_seals_named_counter_count_and_rejects_a_different_instance(self):
        spec = self.spec()
        entry = MultikickerEntrySpec(spec, 'charge')
        descriptor = {'handler_id': 'replacement.zone.multikicker-counters.v1', 'schema_version': 1,
            'event': 'zone.change', 'entry': entry.to_dict()}
        subject = ZoneChangeSubjectSnapshot(object_id='object', object_ref='ref', logical_object_id='object@1',
            owner='A', controller='B', origin='stack', destination='battlefield', destination_controller='B',
            entry_face_id='front', object_types=('artifact',), is_card_object=False,
            cast_option='kicked', multikicker_paid=spec.payment_fact(3))
        effect = MultikickerEntryHandler().subject_replacement_effect(descriptor, subject=subject, component_id='entry')
        operation, = effect.operations
        self.assertEqual(('charge', 3, 'B'), (operation.counter_name, operation.amount, operation.placing_player))
        self.assertIsNone(MultikickerEntryHandler().subject_replacement_effect(descriptor,
            subject=replace(subject, multikicker_paid=None, cast_option=None), component_id='entry'))
        changed = replace(subject, multikicker_paid={**spec.payment_fact(3), 'instance_id': 'other'})
        with self.assertRaises(ValueError):
            MultikickerEntryHandler().subject_replacement_effect(descriptor, subject=changed, component_id='entry')


class MultikickerCompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        path = Path(cls.temp.name) / 'multikicker-compiler.sqlite3'
        build_fixture_database([FIXTURE], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()

    @classmethod
    def tearDownClass(cls):
        cls.db.close(); cls.temp.cleanup()

    def test_actual_originals_close_with_source_spans_and_linked_counter_instances(self):
        for name in NAMES:
            with self.subTest(name=name):
                card = self.db.lookup(name)
                ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile='commander_review')
                self.assertEqual('exact', ir.status, ir.material_residuals)
                cost, entry = ir.faces[0].nodes[:2]
                # Keywords such as Haste may occur between the two linked nodes.
                entry = next(n for n in ir.faces[0].nodes if n.template_id == 'multikicker-named-entry-counters-v1')
                self.assertEqual(cost.handlers[0]['multikicker'], entry.handlers[0]['entry']['cost'])
                for node in (cost, entry):
                    self.assertEqual(node.text, card.oracle_text[node.span.start:node.span.end])
                program = compile_best_available_card_program(self.db, card, semantic_registry=SemanticRegistry(),
                    capability_registry=self.registry, capability_profile='commander_review')
                binding = bind_card_program_runtime(program, capability_registry=self.registry, profile='commander_review')
                self.assertTrue(binding['strict_capability_ready'], binding)

    def test_variable_multiple_zero_and_unrepresented_siblings_stay_residual(self):
        for text in ('Multikicker {X}', 'Multikicker {W/U}', 'Multikicker {0}',
                     'Multikicker {2}\nMultikicker {3}', 'Multikicker {2}\nKicker {1}',
                     'Multikicker {2}\nSquad {2}', 'Multikicker—Pay 2 life.'):
            with self.subTest(text=text):
                card = replace(record(text, 'Creature'), keywords=('Multikicker',))
                self.assertNotEqual('exact', compile_oracle_card(card, capability_registry=self.registry,
                    capability_profile='commander_review').status)
        original = self.db.lookup('Gnarlid Pack')
        changed = replace(original, oracle_text=original.oracle_text + '\nWhenever a player sneezes, draw a card.')
        program = compile_best_available_card_program(self.db, changed, semantic_registry=SemanticRegistry(),
            capability_registry=self.registry, capability_profile='commander_review')
        self.assertFalse(bind_card_program_runtime(program, capability_registry=self.registry,
            profile='commander_review')['compatible_ready'])

    def test_dependency_and_compiler_omissions_fail_closed(self):
        from quorune.compiler import multikicker_nodes
        card = self.db.lookup('Gnarlid Pack')
        raw = json.loads((ROOT / 'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        for capability in (MULTIKICKER_CAPABILITY, MULTIKICKER_ENTRY_CAPABILITY, 'counter.placement.quantity_replacement'):
            changed = deepcopy(raw)
            next(row for row in changed['capabilities'] if row['id'] == capability).update(status='blocked', blockers=['omitted'])
            self.assertNotEqual('exact', compile_oracle_card(card, capability_registry=CapabilityRegistry(changed),
                capability_profile='commander_review').status)
        with mock.patch.object(multikicker_nodes, 'multikicker_static_node', return_value=None):
            self.assertNotEqual('exact', compile_oracle_card(card, capability_registry=self.registry,
                capability_profile='commander_review').status)


class MultikickerRuntimeTests(unittest.TestCase):
    register_fixture_cards_on_add = True
    session = paid_actions.PaidEntryCopyRuntimeTests.session
    add = paid_actions.PaidEntryCopyRuntimeTests.add
    checkpoint = paid_actions.PaidEntryCopyRuntimeTests.checkpoint
    resolve = paid_actions.PaidEntryCopyRuntimeTests.resolve
    replay = paid_actions.PaidEntryCopyRuntimeTests.replay
    prepare = paid_actions.PaidEntryCopyRuntimeTests.prepare
    cast = paid_actions.PaidEntryCopyRuntimeTests.cast
    assert_closed = paid_actions.PaidEntryCopyRuntimeTests.assert_closed

    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        path = Path(cls.temp.name) / 'multikicker-runtime.sqlite3'
        build_fixture_database([ROOT / 'tests/fixtures/bound-effect-program-cards.json',
            ROOT / 'tests/fixtures/paid-entry-copy-cards.json', FIXTURE], path)
        cls.db = CardDatabase(path); cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Multikicker witness', [DeckEntry('Generic Bound Commander', 1, 'commander'),
            DeckEntry('Generic Bound Plains', 30)], ['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):
        cls.db.close(); cls.temp.cleanup()

    def cast_repeated(self, session, card, count):
        self.assert_closed(session, card)
        row = next(a for a in session.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']
            if a.get('card') == card.ref and a['id'].startswith('cast:'))
        option = next(o for o in row['cost_options'] if ('+multikicker:' in o['id']) == (count > 0))
        command = {'action_id': row['id'], 'cost_option': option['id'], 'pay': 'auto', 'multikicker_count': count}
        before = authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:B', command).ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        result = session.act('pilot:A', command)
        self.assertTrue(result.ok, result.summary)
        return command, option

    def test_actual_chalice_zero_one_two_and_six_payments_mana_privacy_and_replay(self):
        for count in (0, 1, 2, 6):
            with self.subTest(count=count):
                session = self.session(424000 + count)
                card = self.add(session.engine, 'Everflowing Chalice', zone='hand', ref='multi-chalice')
                hidden = self.add(session.engine, 'Generic Bound Growth', seat='C', zone='hand', ref='multi-private')
                self.prepare(session, mana={'C': 2 * count})
                self.checkpoint(session); self.cast_repeated(session, card, count)
                self.assertEqual(0, session.state.players['A'].mana_pool['C'])
                session = self.replay(session, load=True)
                self.resolve(session)
                current = session.state.cards[card.object_id]
                self.assertEqual(('battlefield', count), (current.zone, current.counters.get('charge', 0)))
                self.assertNotIn(MULTIKICKER_FIELD, current.annotations)
                action = next(a for a in session.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']
                    if a.get('source') == card.ref and a['id'].startswith('activate:'))
                result = session.act('pilot:A', {'action_id': action['id'], 'pay': 'auto'})
                self.assertTrue(result.ok, result.summary)
                self.assertEqual(count, session.state.players['A'].mana_pool['C'])
                for viewer in 'ABD':
                    self.assertNotIn(hidden.ref, json.dumps(session.packet('pilot:'+viewer, full=True)))
                self.replay(session, load=True)

    def test_actual_creature_total_reduction_counter_replacement_and_invalid_counts(self):
        session = self.session(424010)
        card = self.add(session.engine, 'Gnarlid Pack', zone='hand', ref='multi-gnarlid')
        reduction = self.add(session.engine, 'Generic Paid Entry Reduction')
        doubler = self.add(session.engine, 'Branching Evolution')
        self.assert_closed(session, reduction); self.assert_closed(session, doubler)
        self.prepare(session, mana={'C': 3, 'G': 7})
        self.checkpoint(session)
        row = next(a for a in session.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']
            if a.get('card') == card.ref and a['id'].startswith('cast:'))
        paid = next(o for o in row['cost_options'] if '+multikicker:' in o['id'])
        for value in (True, -1, 1.5, '2', 20, 0):
            with self.subTest(value=value):
                before = authoritative_state_hash(session.state)
                result = session.act('pilot:A', {'action_id': row['id'], 'cost_option': paid['id'],
                    'pay': 'auto', 'multikicker_count': value})
                self.assertFalse(result.ok)
                self.assertEqual(before, authoritative_state_hash(session.state))
        self.cast_repeated(session, card, 6)
        self.assertEqual({'C': 0, 'G': 0}, {k:session.state.players['A'].mana_pool[k] for k in ('C', 'G')})
        self.resolve(session)
        current = session.state.cards[card.object_id]
        self.assertEqual(12, current.counters['+1/+1'])
        self.assertEqual((14, 14), tuple(session.engine._numeric_stat(card.object_id, k) for k in ('power','toughness')))
        self.replay(session, load=True)

    def test_pending_entry_order_load_and_later_unpaid_blink_keep_incarnations_distinct(self):
        session = self.session(424020)
        card = self.add(session.engine, 'Gnarlid Pack', zone='hand', ref='multi-pending')
        self.add(session.engine, 'Branching Evolution', ref='multi-double')
        scales = self.add(session.engine, 'Hardened Scales', ref='multi-scales')
        blink = self.add(session.engine, 'Generic Paid Entry Blink', zone='hand', ref='multi-blink')
        self.prepare(session, mana={'C': 3, 'G': 3, 'W': 1})
        self.checkpoint(session); self.cast_repeated(session, card, 2)
        decision = self.resolve(session)
        self.assertEqual('replacement.order', decision.kind)
        self.assertEqual('stack', session.state.cards[card.object_id].zone)
        session = self.replay(session, load=True)
        options = session.packet('pilot:A', full=True)['decision']['ctx']['options']
        selected = next(row['id'] for row in options if scales.ref in row['id'])
        command = {'action_id': 'choose', 'choices': {'replacement': selected}}
        before = authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:B', command).ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        self.assertTrue(session.act('pilot:A', command).ok)
        self.resolve(session)
        self.assertEqual(6, session.state.cards[card.object_id].counters['+1/+1'])
        self.assertNotIn(MULTIKICKER_FIELD, session.state.cards[card.object_id].annotations)
        self.cast(session, 'A', blink, [card.ref]); self.resolve(session)
        self.assertEqual(('battlefield', 0), (session.state.cards[card.object_id].zone,
            session.state.cards[card.object_id].counters.get('+1/+1', 0)))
        self.replay(session, load=True)

    def test_actual_warcaller_and_yearling_use_current_counters_and_departed_power(self):
        for leave in (False, True):
            with self.subTest(leave=leave):
                session = self.session(424030 + leave)
                elf = self.add(session.engine, 'Llanowar Elves', ref='multi-elf')
                warcaller = self.add(session.engine, 'Joraga Warcaller', zone='hand', ref='multi-warcaller')
                yearling = self.add(session.engine, 'Flametongue Yearling', zone='hand', ref='multi-yearling')
                target = self.add(session.engine, 'Colossal Dreadmaw', seat='B', ref='multi-damage-target')
                grow_elf = self.add(session.engine, 'Battlegrowth', zone='hand', ref='multi-grow-elf')
                grow_yearling = self.add(session.engine, 'Battlegrowth', zone='hand', ref='multi-grow-yearling')
                bounce = self.add(session.engine, 'Unsummon', zone='hand', ref='multi-bounce')
                # Auto-payment may legally spend blue on the six generic mana.
                # Reserve enough blue for the later response under any such plan.
                self.prepare(session, mana={'C': 6, 'G': 5, 'R': 2, 'U': 8})
                self.checkpoint(session)
                self.cast_repeated(session, warcaller, 2); self.resolve(session)
                self.assertEqual(3, session.engine._numeric_stat(elf.object_id, 'power'))
                self.cast(session, 'A', grow_elf, [warcaller.ref]); self.resolve(session)
                self.assertEqual(4, session.engine._numeric_stat(elf.object_id, 'power'))
                self.cast_repeated(session, yearling, 2)
                self.assertEqual('semantic.target', self.resolve(session).kind)
                self.assertTrue(session.act('pilot:A', {'action_id': 'choose', 'targets': [target.ref]}).ok)
                session = self.replay(session, load=True)
                self.cast(session, 'A', grow_yearling, [yearling.ref])
                # Preserve the ETB trigger while resolving the growth response.
                for _ in range(32):
                    if session.engine._numeric_stat(yearling.object_id, 'power') == 5:
                        break
                    self.assertEqual('priority', session.state.pending_decision.kind)
                    self.assertTrue(session.act(session.pending_principals()[0], {'action_id': 'pass'}).ok)
                else:
                    self.fail('Yearling growth did not resolve before its trigger')
                if leave:
                    self.assertGreaterEqual(session.state.players['A'].mana_pool['U'], 1)
                    self.cast(session, 'A', bounce, [yearling.ref])
                self.resolve(session)
                self.assertEqual(5, session.state.cards[target.object_id].marked_damage)
                self.assertEqual('hand' if leave else 'battlefield', session.state.cards[yearling.object_id].zone)
                self.replay(session, load=True)

    def assert_chalice_payment_and_entry(self, seed):
        session = self.session(seed)
        card = self.add(session.engine, 'Everflowing Chalice', zone='hand', ref='multi-mutant-chalice')
        self.prepare(session, mana={'C': 4}); self.checkpoint(session)
        self.cast_repeated(session, card, 2)
        self.assertEqual(0, session.state.players['A'].mana_pool['C'])
        self.resolve(session)
        self.assertEqual(2, session.state.cards[card.object_id].counters.get('charge', 0))

    def test_native_fee_count_and_paid_fact_mutants_are_killed(self):
        from quorune.rules.casting import multikicker_costs as owner
        original_expand = owner.expand_multikicker_costs
        def omit_fee(options, schema, **kwargs):
            rows = original_expand(options, schema, **kwargs)
            if MULTIKICKER_FIELD in schema:
                spec = FixedManaMultikickerSpec.from_dict(schema[MULTIKICKER_FIELD])
                for row in rows:
                    if MULTIKICKER_FIELD in row:
                        for symbol, amount in spec.requirements.items():
                            row['requirements'][symbol] -= amount * row[MULTIKICKER_FIELD]['count']
            return rows
        with mock.patch.object(owner, 'expand_multikicker_costs', side_effect=omit_fee):
            with self.assertRaises(AssertionError):
                self.assert_chalice_payment_and_entry(424040)
        original_count = MultikickerEntrySpec.paid_count
        def one_count(spec, fact):
            return min(1, original_count(spec, fact))
        with mock.patch.object(MultikickerEntrySpec, 'paid_count', new=one_count):
            with self.assertRaises(AssertionError):
                self.assert_chalice_payment_and_entry(424041)
        with mock.patch('quorune.rules.casting.commit.multikicker_cast_context', return_value={}):
            with self.assertRaises(AssertionError):
                self.assert_chalice_payment_and_entry(424042)

    def assert_yearling_departure_power(self, seed):
        session = self.session(seed)
        source = self.add(session.engine, 'Flametongue Yearling', zone='hand', ref='multi-power-mutant')
        target = self.add(session.engine, 'Colossal Dreadmaw', seat='B', ref='multi-power-target')
        growth = self.add(session.engine, 'Battlegrowth', zone='hand', ref='multi-power-growth')
        bounce = self.add(session.engine, 'Unsummon', zone='hand', ref='multi-power-bounce')
        self.prepare(session, mana={'R':2,'G':1,'U':1})
        self.checkpoint(session); self.cast_repeated(session, source, 0)
        self.assertEqual('semantic.target', self.resolve(session).kind)
        self.assertTrue(session.act('pilot:A', {'action_id':'choose','targets':[target.ref]}).ok)
        self.cast(session, 'A', growth, [source.ref])
        for _ in range(32):
            if session.engine._numeric_stat(source.object_id, 'power') == 3:
                break
            self.assertTrue(session.act(session.pending_principals()[0], {'action_id':'pass'}).ok)
        else:
            self.fail('Power response did not resolve')
        self.cast(session, 'A', bounce, [source.ref]); self.resolve(session)
        self.assertEqual(3, session.state.cards[target.object_id].marked_damage)
        self.replay(session, load=True)

    def test_native_power_departure_pin_omission_is_killed(self):
        self.assert_yearling_departure_power(424050)
        with mock.patch('quorune.rules.stack_damage_sources.pin_creature_power_damage_departures'):
            with self.assertRaises(AssertionError):
                self.assert_yearling_departure_power(424051)
