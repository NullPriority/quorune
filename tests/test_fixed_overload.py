from __future__ import annotations

"""Pinned CR 702.96: the paid text variant is target-free and resolves live."""

import json
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from common import ROOT
from quorune.carddb import CardDatabase, CardRecord
from quorune.card_programs import bind_card_program_runtime
from quorune.card_programs.adapters import compile_best_available_card_program
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.overload_model import FixedManaOverloadSpec, OVERLOAD_CAPABILITY, overload_body_fingerprint
from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry
from quorune.semantics import SemanticRegistry
from quorune.util import mana_cost_to_vector
from scripts.build_test_database import build_fixture_database
import test_spree_mode_costs as actions
import test_self_public_zone_casting as zone_actions


FIXTURE = ROOT / 'tests/fixtures/fixed-overload-cards.json'


class FixedOverloadModelCompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registry = load_default_capability_registry()

    def record(self, cost='Overload {1}{R}', body=None, **changes):
        body = body if body is not None else "Overload Witness deals 1 damage to target creature you don't control."
        return replace(CardRecord(oracle_id='fixture:overload-boundary', name='Overload Witness',
            mana_cost='{R}', mana_value=1, type_line='Instant', oracle_text=body + '\n' + cost,
            power=None, toughness=None, loyalty=None, defense=None, colors=('R',), color_identity=('R',),
            keywords=('Overload',), produced_mana=(), layout='normal', released_at=None,
            legalities={'commander': 'legal'}, faces=(), raw={}), **changes)

    def compile(self, record=None, registry=None):
        return compile_oracle_card(record or self.record(), capability_registry=registry or self.registry,
            capability_profile='commander_review')

    def test_current_reviewed_legacy_alternative_keeps_its_declared_cost_owner(self):
        from types import SimpleNamespace
        from quorune.rules.casting.fixed_text_variant_costs import fixed_variant_cost_options
        from quorune.spell_text_variants import SpellTextVariantKind
        record = self.record()
        alternative = {'id':'overload','kind':'overload','requirements':{'GENERIC':1,'R':1},
            'target_schema':{'groups':[]},'effects':[{'op':'destroy_all'}]}
        program = SimpleNamespace(oracle_id=record.oracle_id,ability_id='spell:front',active_zone='stack',
            event='resolve',trust_level='trusted',requires_arbiter=False,coverage=['overload'],
            provenance={'review_status':'reviewed'},cost_schema={'alternate_costs':[alternative]})
        def options(candidate, *, current=True):
            host = SimpleNamespace(card_record=lambda card:record,
                semantic_program_is_current_trusted=lambda value:current,
                semantics=SimpleNamespace(get=lambda key:None))
            return fixed_variant_cost_options(host,'A',object(),candidate,kind=SpellTextVariantKind.OVERLOAD,
                commander_tax=0,force_without_mana_cost=False,alternative_base=None,suppress_source_costs=False)
        self.assertEqual([], options(program))
        self.assertIsNone(options(program,current=False))
        for field,value in (('trust_level','provisional'),('requires_arbiter',True),
                ('oracle_id','unrelated-source'),('provenance',{'review_status':'unreviewed'}),
                ('coverage',['overload','fixed_overload_cast_variant']),
                ('cost_schema',{'alternate_costs':[]})):
            changed = deepcopy(program);setattr(changed,field,value)
            with self.subTest(field=field):self.assertIsNone(options(changed))
        for mutation in ({'id':'unknown'}, {'kind':'alternate'}, {'effects':[]}, {'target_schema':{'groups':[{}]}}):
            changed = deepcopy(program)
            changed.cost_schema={'alternate_costs':[{**alternative,**mutation}]}
            with self.subTest(mutation=mutation):self.assertIsNone(options(changed))

    def test_overload_spec_is_immutable_and_has_two_distinct_closed_body_fingerprints(self):
        vector = mana_cost_to_vector('{1}{R}')[0]
        spec = FixedManaOverloadSpec('front:overload:line:2', vector, '0' * 64, '1' * 64)
        vector['R'] = 99
        self.assertEqual(1, spec.requirements['R'])
        self.assertEqual(spec, FixedManaOverloadSpec.from_dict(spec.to_dict()))
        for change in ({'schema_version': True}, {'extra': 1}, {'normal_fingerprint': '1' * 64},
                {'overloaded_fingerprint': 'bad'}, {'requirements': {'R': 1}},
                {'requirements': {**dict(spec.requirements), 'GENERIC': -1}}):
            with self.assertRaises(ValueError):
                FixedManaOverloadSpec.from_dict({**spec.to_dict(), **change})

    def test_overload_binds_distinct_nonmodal_programs_and_rejects_unclosed_variants(self):
        record = self.record()
        ir = self.compile(record)
        self.assertEqual('exact', ir.status, ir.material_residuals)
        normal, paid = ir.faces[0].nodes
        self.assertEqual(('spell_ability', 'spell_variant'), (normal.kind, paid.kind))
        self.assertIsNotNone(normal.target_schema)
        self.assertIsNone(paid.target_schema)
        self.assertNotEqual(normal.effects, paid.effects)
        self.assertEqual(normal.cost, paid.cost)
        self.assertEqual(record.oracle_text, normal.text)
        self.assertEqual(normal.span, paid.span)
        self.assertEqual((0, len(record.oracle_text)), (normal.span.start, normal.span.end))
        spec = FixedManaOverloadSpec.from_dict(normal.cost['overload'])
        self.assertEqual(spec.normal_fingerprint, overload_body_fingerprint(normal.effects, normal.target_schema))
        self.assertEqual(spec.overloaded_fingerprint, overload_body_fingerprint(paid.effects, None))
        self.assertTrue(all(OVERLOAD_CAPABILITY in node.capability_dependencies for node in (normal, paid)))
        self.assertNotIn('modes', normal.target_schema)
        for cost in ('Overload {X}{R}', 'Overload {U/R}', 'Overload {S}', 'Overload {R}\nOverload {1}{R}'):
            self.assertNotEqual('exact', self.compile(self.record(cost=cost)).status)
        for change in ({'oracle_text': record.oracle_text + '\nInvent an unrepresented sibling.'},
                {'mana_cost': '{X}{R}'}, {'layout': 'split'}):
            self.assertNotEqual('exact', self.compile(self.record(**change)).status)
        unclosed_each = self.record(body='Target creature you control gets +0/+1 and gains hexproof until end of turn.')
        normal_only = replace(unclosed_each, oracle_text=unclosed_each.oracle_text.splitlines()[0], keywords=())
        self.assertEqual('exact', self.compile(normal_only).status)
        self.assertNotEqual('exact', self.compile(unclosed_each).status)
        raw = json.loads((ROOT / 'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        for cap in (OVERLOAD_CAPABILITY, 'damage.amount.positive', 'target.revalidate_resolution'):
            changed = deepcopy(raw)
            next(row for row in changed['capabilities'] if row['id'] == cap).update(status='blocked', blockers=['Overload body owner withdrawn'])
            self.assertNotEqual('exact', self.compile(registry=CapabilityRegistry(changed)).status)

    def test_overload_whole_pair_omission_fails_the_independent_positive_contract(self):
        with mock.patch('quorune.compiler.overload_spell_faces.compile_fixed_overload_face', return_value=None):
            with self.assertRaises(AssertionError):
                self.test_overload_binds_distinct_nonmodal_programs_and_rejects_unclosed_variants()


class FixedOverloadRuntimeTests(unittest.TestCase):
    register_fixture_cards_on_add = True
    session = actions.SpreeModeCostRuntimeTests.session
    add = actions.SpreeModeCostRuntimeTests.add
    checkpoint = actions.SpreeModeCostRuntimeTests.checkpoint
    resolve = actions.SpreeModeCostRuntimeTests.resolve
    replay = actions.SpreeModeCostRuntimeTests.replay
    assert_closed = actions.SpreeModeCostRuntimeTests.assert_closed
    reject = actions.SpreeModeCostRuntimeTests.reject
    cast = zone_actions.SelfPublicZoneRuntimeTests.cast

    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        path = Path(cls.temp.name) / 'overload.sqlite3'
        build_fixture_database([ROOT / 'tests/fixtures/bound-effect-program-cards.json', FIXTURE], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Overload witness', [DeckEntry('Generic Bound Commander', 1, 'commander'),
            DeckEntry('Generic Bound Plains', 30)], ['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temp.cleanup()

    def prepare(self, session, **kwargs):
        session.state.config.semantic_policy = 'trusted_only'
        return actions.SpreeModeCostRuntimeTests.prepare(self, session, **kwargs)

    def cast_overloaded(self, session, source, **payment):
        for _ in range(16):
            if session.state.priority_player == 'A':
                break
            self.assertTrue(session.act(session.pending_principals()[0], {'action_id': 'pass'}).ok)
        current = session.state.cards[source.object_id]
        result = session.act('pilot:A', {'action_id': 'cast:' + current.ref,
            'cost_option': 'overload', 'targets': [], 'pay': 'auto', **payment})
        self.assertTrue(result.ok, result.summary)

    def test_actual_electrickery_overload_ignores_hexproof_and_affects_later_creature(self):
        session = self.session(70296001)
        engine = session.engine
        source = self.add(engine, 'Electrickery', zone='hand', ref='overload-electrickery')
        protected = self.add(engine, 'Gladecover Scout', seat='B', ref='overload-hexproof')
        prevention = self.add(engine, 'Silver Knight', seat='B', ref='overload-protection-red')
        friendly = self.add(engine, 'Llanowar Elves', ref='overload-friendly')
        ordinary = self.add(engine, 'Generic Bound Body', seat='C', ref='overload-current-opponent')
        later = self.add(engine, 'Ashcoat Bear', seat='C', zone='hand', ref='overload-later-creature')
        private = self.add(engine, 'Generic Bound Growth', seat='D', zone='hand', ref='overload-private')
        self.assert_closed(session, source)
        engine.state.players['C'].mana_pool.update({'C': 1, 'G': 1})
        self.prepare(session, mana={'C': 1, 'R': 1})
        self.checkpoint(session)
        action_id = 'cast:' + source.ref
        offered = next(row for row in session.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']
            if row['id'] == action_id)
        self.assertIn('overload', [row['id'] for row in offered['cost_options']])
        for viewer in 'ABC':
            self.assertNotIn(private.ref, json.dumps(session.packet('pilot:' + viewer, full=True)))
        self.reject(session, action_id, 'D', cost_option='overload', targets=[], pay='auto')
        self.reject(session, action_id, cost_option='normal', targets=[protected.ref], pay='auto')
        self.reject(session, action_id, cost_option='normal', targets=[], pay='auto')
        self.reject(session, action_id, cost_option='overload', targets=[ordinary.ref], pay='auto')
        self.reject(session, action_id, cost_option='overload', targets=[], pay='manual', payment={'R': 1})
        result = session.act('pilot:A', {'action_id': action_id, 'cost_option': 'overload', 'targets': [], 'pay': 'auto'})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual([], session.state.stack[-1].targets)
        self.assertEqual([], session.state.stack[-1].modes)
        self.assertEqual(1, session.engine._effective_card_data(session.state.cards[source.object_id])['mana_value'])
        self.assertEqual(0, sum(session.state.players['A'].mana_pool.values()))
        self.cast(session, 'C', later)
        session = self.replay(session, load=True)
        self.resolve(session)
        self.assertEqual('graveyard', session.state.cards[protected.object_id].zone)
        self.assertEqual(('battlefield', 0), (session.state.cards[prevention.object_id].zone,
            session.state.cards[prevention.object_id].marked_damage))
        self.assertEqual(('battlefield', 0), (session.state.cards[friendly.object_id].zone,
            session.state.cards[friendly.object_id].marked_damage))
        for card in (ordinary, later):
            current = session.state.cards[card.object_id]
            self.assertEqual(('battlefield', 1), (current.zone, current.marked_damage))
        self.assertEqual('graveyard', session.state.cards[source.object_id].zone)
        self.assertFalse(session.state.stack)
        self.replay(session)

    def test_only_overloaded_option_is_offered_when_all_opposing_creatures_are_untargetable(self):
        session = self.session(70296010)
        engine = session.engine
        source = self.add(engine, 'Electrickery', zone='hand', ref='overload-only-legal-variant')
        target = self.add(engine, 'Gladecover Scout', seat='B', ref='overload-only-hexproof')
        sphere = self.add(engine, 'Sphere of Resistance', ref='overload-only-cost-increase')
        self.assert_closed(session, source)
        self.prepare(session, mana={'C': 2, 'R': 1})
        self.checkpoint(session)
        action_id = 'cast:' + source.ref
        offered = next(row for row in session.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']
            if row['id'] == action_id)
        self.assertEqual(['overload'], [row['id'] for row in offered['cost_options']])
        self.assertEqual((2, 1), (offered['cost_options'][0]['requirements']['GENERIC'],
            offered['cost_options'][0]['requirements']['R']))
        self.reject(session, action_id, cost_option='overload', targets=[], pay='manual', payment={'C': 1, 'R': 1})
        self.cast_overloaded(session, source)
        session = self.replay(session, load=True)
        self.resolve(session)
        self.assertEqual('graveyard', session.state.cards[target.object_id].zone)
        self.assertEqual('battlefield', session.state.cards[sphere.object_id].zone)
        self.assertEqual(0, sum(session.state.players['A'].mana_pool.values()))
        self.replay(session)

    def test_actual_normal_electrickery_remains_targeted_and_pays_only_printed_price(self):
        session = self.session(70296002)
        engine = session.engine
        source = self.add(engine, 'Electrickery', zone='hand', ref='overload-normal')
        target = self.add(engine, 'Llanowar Elves', seat='B', ref='overload-normal-target')
        untouched = self.add(engine, 'Llanowar Elves', seat='C', ref='overload-normal-untouched')
        self.assert_closed(session, source)
        self.prepare(session, mana={'R': 1})
        self.checkpoint(session)
        self.reject(session, 'cast:' + source.ref, cost_option='overload', targets=[], pay='auto')
        self.cast(session, 'A', source, [target.ref])
        self.assertFalse(session.state.stack[-1].semantic_key.endswith(':overload'))
        self.assertEqual([target.ref], session.state.stack[-1].targets)
        self.assertEqual([], session.state.stack[-1].modes)
        self.assertEqual(0, sum(session.state.players['A'].mana_pool.values()))
        session = self.replay(session, load=True)
        self.resolve(session)
        self.assertEqual('graveyard', session.state.cards[target.object_id].zone)
        self.assertEqual(('battlefield', 0), (session.state.cards[untouched.object_id].zone,
            session.state.cards[untouched.object_id].marked_damage))
        self.replay(session)

    def test_actual_reverberate_keeps_overloaded_program_without_target_or_mode_choice(self):
        session = self.session(70296003)
        engine = session.engine
        source = self.add(engine, 'Dynacharge', zone='hand', ref='overload-copy-source')
        copy = self.add(engine, 'Reverberate', zone='hand', ref='overload-copy-spell')
        first = self.add(engine, 'Llanowar Elves', ref='overload-copy-first')
        second = self.add(engine, 'Generic Bound Body', ref='overload-copy-second')
        enemy = self.add(engine, 'Llanowar Elves', seat='B', ref='overload-copy-enemy')
        self.assert_closed(session, source)
        self.assert_closed(session, copy)
        self.prepare(session, mana={'C': 2, 'R': 3})
        self.checkpoint(session)
        self.cast_overloaded(session, source, reserve={'R': 2})
        original = session.state.stack[-1]
        self.assertTrue(original.semantic_key.endswith(':overload'))
        self.assertEqual(([], []), (original.targets, original.modes))
        self.cast(session, 'A', copy, [original.ref])
        session = self.replay(session, load=True)
        self.assertIsNone(self.resolve(session))
        self.assertEqual(5, session.engine._numeric_stat(first.object_id, 'power'))
        self.assertEqual(6, session.engine._numeric_stat(second.object_id, 'power'))
        self.assertEqual(1, session.engine._numeric_stat(enemy.object_id, 'power'))
        self.assertEqual(2, len(session.engine._current_turn_history('spell_cast')))
        self.assertEqual(0, sum(session.state.players['A'].mana_pool.values()))
        self.assertFalse(session.state.stack)
        self.replay(session)

    def test_counterspell_counters_target_free_overloaded_spell_without_applying_each_body(self):
        session = self.session(70296004)
        engine = session.engine
        source = self.add(engine, 'Electrickery', zone='hand', ref='overload-countered')
        target = self.add(engine, 'Llanowar Elves', seat='B', ref='overload-counter-untouched')
        counter = self.add(engine, 'Counterspell', seat='B', zone='hand', ref='overload-counter')
        self.assert_closed(session, source)
        engine.state.players['B'].mana_pool['U'] = 2
        self.prepare(session, mana={'C': 1, 'R': 1})
        self.checkpoint(session)
        self.cast_overloaded(session, source)
        original = session.state.stack[-1]
        self.cast(session, 'B', counter, [original.ref])
        session = self.replay(session, load=True)
        self.resolve(session)
        self.assertEqual('graveyard', session.state.cards[source.object_id].zone)
        self.assertEqual(('battlefield', 0), (session.state.cards[target.object_id].zone,
            session.state.cards[target.object_id].marked_damage))
        self.assertFalse(session.state.stack)
        self.replay(session)

    def test_cheaper_mind_rake_alternative_keeps_private_apnap_discard_for_each_player(self):
        session = self.session(70296005)
        engine = session.engine
        source = self.add(engine, 'Mind Rake', zone='hand', ref='overload-cheaper-price')
        chosen = {seat: [self.add(engine, 'Generic Bound Growth', seat=seat, zone='hand',
            ref='overload-private-' + seat + '-' + str(i)).ref for i in range(2)] for seat in 'ABCD'}
        self.assert_closed(session, source)
        self.prepare(session, mana={'C': 1, 'B': 1})
        hands = {seat: len(session.state.players[seat].zones['hand']) for seat in 'ABCD'}
        self.checkpoint(session)
        offered = next(row for row in session.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']
            if row['id'] == 'cast:' + source.ref)
        self.assertEqual(['overload'], [row['id'] for row in offered['cost_options']])
        self.cast_overloaded(session, source)
        self.assertEqual(3, session.engine._effective_card_data(session.state.cards[source.object_id])['mana_value'])
        self.assertIsNotNone(self.resolve(session))
        for index, seat in enumerate('ABCD'):
            self.assertIn('pilot:' + seat, session.pending_principals())
            other = 'ABCD'[(index + 1) % 4]
            packet = json.dumps(session.packet('pilot:' + other, full=True))
            for ref in chosen[seat]:
                self.assertNotIn(ref, packet)
            self.reject(session, 'choose', other, cards=chosen[seat])
            result = session.act('pilot:' + seat, {'action_id': 'choose', 'cards': chosen[seat]})
            self.assertTrue(result.ok, result.summary)
            if index == 0:
                session = self.replay(session, load=True)
        self.resolve(session)
        for seat in 'ABCD':
            self.assertEqual(hands[seat] - 2 - (1 if seat == 'A' else 0), len(session.state.players[seat].zones['hand']))
        self.assertEqual(0, sum(session.state.players['A'].mana_pool.values()))
        self.assertFalse(session.state.stack)
        self.replay(session)

    def test_free_base_cannot_make_overloaded_body_free_or_change_normal_targeted_body(self):
        from quorune.impulse_access import grant_temporary_cast_permission
        from quorune.impulse_access_model import TemporaryCastPermissionGrant, ImpulseAccessDuration
        for paid in (False, True):
            with self.subTest(paid=paid):
                session = self.session(70296006 + paid)
                engine = session.engine
                source = self.add(engine, 'Dynacharge', zone='hand', ref='overload-free-boundary')
                bodies = [self.add(engine, 'Llanowar Elves', ref='overload-free-' + str(i)) for i in range(2)]
                self.assert_closed(session, source)
                grant_temporary_cast_permission(engine, card=source,
                    grant=TemporaryCastPermissionGrant(player='A', duration=ImpulseAccessDuration.END_OF_TURN,
                        not_before_turn_sequence=None, without_mana_cost=True, source='overload-free-base-contract'),
                    reason='A free normal base cannot waive the distinct Overload alternative')
                self.prepare(session, mana={'C': 2, 'R': 1} if paid else {})
                self.checkpoint(session)
                self.reject(session, 'cast:' + source.ref, cost_option='without_mana_cost', targets=[], pay='auto', overload=True)
                if paid:
                    self.cast_overloaded(session, source)
                else:
                    self.reject(session, 'cast:' + source.ref, cost_option='overload', targets=[], pay='auto')
                    result = session.act('pilot:A', {'action_id': 'cast:' + source.ref, 'cost_option': 'without_mana_cost',
                        'targets': [bodies[0].ref], 'pay': 'auto'})
                    self.assertTrue(result.ok, result.summary)
                session = self.replay(session, load=True)
                self.resolve(session)
                self.assertEqual(3, session.engine._numeric_stat(bodies[0].object_id, 'power'))
                self.assertEqual(3 if paid else 1, session.engine._numeric_stat(bodies[1].object_id, 'power'))
                self.assertEqual(0, sum(session.state.players['A'].mana_pool.values()))
                self.replay(session)

    def test_original_overload_cards_require_both_complete_bodies_and_paired_normal_registration(self):
        expected = {'Mind Rake', 'Scale Up', 'Stirring Address', 'Mizzium Mortars', 'Dynacharge', 'Electrickery', 'Dragonshift'}
        rows = [r for r in json.loads(FIXTURE.read_text(encoding='utf-8'))['cards'] if 'Overload' in r.get('keywords', ())]
        self.assertEqual(27, len(rows))
        for row in rows:
            with self.subTest(name=row['name']):
                record = self.db.by_oracle_id(row['oracle_id'])
                program = compile_best_available_card_program(self.db, record, semantic_registry=SemanticRegistry(),
                    capability_registry=self.registry, capability_profile='commander_review')
                binding = bind_card_program_runtime(program, capability_registry=self.registry, profile='commander_review')
                self.assertEqual(row['name'] in expected, binding['strict_capability_ready'], binding)
                if row['name'] in expected:
                    session = self.session(70296008)
                    source = self.add(session.engine, row['name'], zone='hand', ref='overload-original-table')
                    self.assert_closed(session, source)
                    from quorune.rules.casting.overload_costs import normal_overload_program, select_overload_cast_program
                    normal = session.engine.semantics.get(record.oracle_id + ':spell:front')
                    normal = normal_overload_program(session.engine, source, normal)
                    self.assertIsNotNone(normal)
                    paid = select_overload_cast_program(session.engine, source, normal,
                        {'id': 'overload', 'overload': normal.cost_schema['overload']})
                    self.assertIsNone(paid.target_schema)
                    self.assertTrue(paid.key.endswith(':overload'))

    def test_missing_derived_program_keeps_card_residual_and_has_no_ordinary_cast_fallback(self):
        from quorune.compiler import program_generation as owner
        original = owner._generated_ability_id
        def omitted(**values):
            return None if values['kind'] == 'spell_variant' else original(**values)
        with mock.patch.object(owner, '_generated_ability_id', omitted):
            record = self.db.lookup('Electrickery')
            program = compile_best_available_card_program(self.db, record, semantic_registry=SemanticRegistry(),
                capability_registry=self.registry, capability_profile='commander_review')
            binding = bind_card_program_runtime(program, capability_registry=self.registry, profile='commander_review')
            self.assertFalse(binding['strict_capability_ready'])
            session = self.session(70296009)
            source = self.add(session.engine, 'Electrickery', zone='hand', ref='overload-missing-derived')
            self.prepare(session, mana={'C': 1, 'R': 1})
            self.checkpoint(session)
            actions = session.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']
            self.assertNotIn('cast:' + source.ref, [row['id'] for row in actions])
            self.reject(session, 'cast:' + source.ref, cost_option='normal', targets=[], pay='auto')

    def test_overload_price_omission_preserves_admission_but_fails_native_underpayment_contract(self):
        from quorune.rules.casting import overload_costs as owner
        original = owner.overload_cost_options
        changed = []
        def omitted(*args, **kwargs):
            result = original(*args, **kwargs)
            for option in result or ():
                changed.append(option['requirements']['GENERIC'])
                option['requirements']['GENERIC'] = 0
            return result
        result = unittest.TestResult()
        witness = type(self)('test_actual_electrickery_overload_ignores_hexproof_and_affects_later_creature')
        with mock.patch.object(owner, 'overload_cost_options', omitted):
            witness.run(result)
        self.assertTrue(changed)
        self.assertFalse(result.errors)
        self.assertEqual(1, len(result.failures))
        self.assertIn('self.assertFalse(session.act', result.failures[0][1])

    def test_overload_program_selection_omission_is_killed_before_wrong_body_can_execute(self):
        import inspect
        from quorune.rules.casting import proposal as owner
        source = inspect.getsource(owner._cast_program_and_cost)
        line = 'program = select_text_variant_proposal_program(host, card, program, selected.to_dict())'
        self.assertEqual(1, source.count(line))
        namespace = dict(vars(owner))
        exec(source.replace(line, 'program = program'), namespace)
        result = unittest.TestResult()
        witness = type(self)('test_actual_electrickery_overload_ignores_hexproof_and_affects_later_creature')
        with mock.patch.object(owner, '_cast_program_and_cost', namespace['_cast_program_and_cost']):
            witness.run(result)
        self.assertFalse(result.errors)
        self.assertEqual(1, len(result.failures))
        self.assertIn('self.assertTrue(result.ok', result.failures[0][1])
