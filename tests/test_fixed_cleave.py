from __future__ import annotations

"""Pinned CR 702.148: removing bracketed restrictions requires Cleave payment."""

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
from quorune.cleave_model import FixedManaCleaveSpec, CLEAVE_CAPABILITY
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry
from quorune.semantics import SemanticRegistry
from quorune.spell_text_variants import spell_body_fingerprint
from quorune.util import mana_cost_to_vector
from scripts.build_test_database import build_fixture_database
import test_spree_mode_costs as actions
import test_self_public_zone_casting as zone_actions


FIXTURE = ROOT / 'tests/fixtures/fixed-cleave-cards.json'


class FixedCleaveModelCompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registry = load_default_capability_registry()

    def record(self, cost='Cleave {5}{W}', body='Destroy target [attacking] creature.', **changes):
        return replace(CardRecord(oracle_id='fixture:cleave-boundary', name='Cleave Witness',
            mana_cost='{1}{W}', mana_value=2, type_line='Instant', oracle_text=cost + '\n' + body,
            power=None, toughness=None, loyalty=None, defense=None, colors=('W',), color_identity=('W',),
            keywords=('Cleave',), produced_mana=(), layout='normal', released_at=None,
            legalities={'commander': 'legal'}, faces=(), raw={}), **changes)

    def compile(self, record=None, registry=None):
        return compile_oracle_card(record or self.record(), capability_registry=registry or self.registry,
            capability_profile='commander_review')

    def test_cleave_spec_has_closed_price_and_distinct_body_fingerprints(self):
        vector = mana_cost_to_vector('{5}{W}')[0]
        spec = FixedManaCleaveSpec('front:cleave:line:1', vector, '0' * 64, '1' * 64)
        vector['W'] = 99
        self.assertEqual(1, spec.requirements['W'])
        self.assertEqual(spec, FixedManaCleaveSpec.from_dict(spec.to_dict()))
        for changes in ({'schema_version': True}, {'extra': 1}, {'cleaved_fingerprint': '0' * 64},
                {'normal_fingerprint': 'bad'}, {'requirements': {'W': 1}}):
            with self.assertRaises(ValueError):
                FixedManaCleaveSpec.from_dict({**spec.to_dict(), **changes})

    def test_cleave_keeps_two_independent_target_schemas_and_complete_source_spans(self):
        from quorune.compiler.cleave_spell_faces import cleave_bodies
        record = self.record()
        ir = self.compile(record)
        self.assertEqual('exact', ir.status, ir.material_residuals)
        normal, paid = ir.faces[0].nodes
        self.assertEqual(('spell_ability', 'spell_variant'), (normal.kind, paid.kind))
        self.assertIsNotNone(normal.target_schema)
        self.assertIsNotNone(paid.target_schema)
        self.assertNotEqual(normal.target_schema, paid.target_schema)
        self.assertEqual(normal.cost, paid.cost)
        self.assertEqual(record.oracle_text, normal.text)
        self.assertEqual(normal.span, paid.span)
        self.assertEqual((0, len(record.oracle_text)), (normal.span.start, normal.span.end))
        spec = FixedManaCleaveSpec.from_dict(normal.cost['cleave'])
        self.assertEqual(spec.normal_fingerprint, spell_body_fingerprint(normal.effects, normal.target_schema))
        self.assertEqual(spec.cleaved_fingerprint, spell_body_fingerprint(paid.effects, paid.target_schema))
        self.assertTrue(all(CLEAVE_CAPABILITY in node.capability_dependencies for node in (normal, paid)))
        self.assertNotIn('modes', normal.target_schema)
        for body in ('Destroy target creature.', 'Destroy target [[attacking]] creature.',
                'Destroy target [attacking creature.', 'Destroy target attacking] creature.', 'Destroy target [] creature.'):
            self.assertIsNone(cleave_bodies(body))
            self.assertNotEqual('exact', self.compile(self.record(body=body)).status)
        for cost in ('Cleave {X}{W}', 'Cleave {U/W}', 'Cleave {S}', 'Cleave {W}\nCleave {2}{W}'):
            self.assertNotEqual('exact', self.compile(self.record(cost=cost)).status)
        self.assertNotEqual('exact', self.compile(self.record(oracle_text=record.oracle_text + '\nInvent an unknown sibling.')).status)
        raw = json.loads((ROOT / 'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        for capability in (CLEAVE_CAPABILITY, 'target.revalidate_resolution', 'permanent.destroy.effect'):
            changed = deepcopy(raw)
            next(row for row in changed['capabilities'] if row['id'] == capability).update(status='blocked', blockers=['Cleave owner withdrawn'])
            self.assertNotEqual('exact', self.compile(registry=CapabilityRegistry(changed)).status)

    def test_cleave_pair_producer_omission_fails_independent_positive_contract(self):
        with mock.patch('quorune.compiler.cleave_spell_faces.compile_fixed_cleave_face', return_value=None):
            with self.assertRaises(AssertionError):
                self.test_cleave_keeps_two_independent_target_schemas_and_complete_source_spans()


class FixedCleaveRuntimeTests(unittest.TestCase):
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
        path = Path(cls.temp.name) / 'cleave.sqlite3'
        build_fixture_database([ROOT / 'tests/fixtures/bound-effect-program-cards.json', FIXTURE], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Cleave witness', [DeckEntry('Generic Bound Commander', 1, 'commander'),
            DeckEntry('Generic Bound Plains', 30)], ['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temp.cleanup()

    def prepare(self, session, **kwargs):
        session.state.config.semantic_policy = 'trusted_only'
        return actions.SpreeModeCostRuntimeTests.prepare(self, session, **kwargs)

    def cast_cleaved(self, session, source, targets=(), **payment):
        for _ in range(16):
            if session.state.priority_player == 'A':
                break
            self.assertTrue(session.act(session.pending_principals()[0], {'action_id': 'pass'}).ok)
        current = session.state.cards[source.object_id]
        result = session.act('pilot:A', {'action_id': 'cast:' + current.ref, 'cost_option': 'cleave',
            'targets': list(targets), 'pay': 'auto', **payment})
        self.assertTrue(result.ok, result.summary)

    def test_actual_fierce_retribution_cleave_targets_nonattacker_with_empty_mode_list(self):
        session = self.session(70214801)
        engine = session.engine
        source = self.add(engine, 'Fierce Retribution', zone='hand', ref='cleave-fierce-retribution')
        target = self.add(engine, 'Llanowar Elves', seat='B', ref='cleave-nonattacking-target')
        private = self.add(engine, 'Generic Bound Growth', seat='D', zone='hand', ref='cleave-private')
        self.assert_closed(session, source)
        self.prepare(session, mana={'C': 5, 'W': 1})
        self.checkpoint(session)
        action_id = 'cast:' + source.ref
        offered = next(row for row in session.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']
            if row['id'] == action_id)
        self.assertEqual(['cleave'], [row['id'] for row in offered['cost_options']])
        for viewer in 'ABC':
            self.assertNotIn(private.ref, json.dumps(session.packet('pilot:' + viewer, full=True)))
        self.reject(session, action_id, 'C', cost_option='cleave', targets=[target.ref], pay='auto')
        self.reject(session, action_id, cost_option='normal', targets=[target.ref], pay='auto')
        self.reject(session, action_id, cost_option='cleave', targets=[target.ref], pay='manual', payment={'C': 1, 'W': 1})
        result = session.act('pilot:A', {'action_id': action_id, 'cost_option': 'cleave',
            'targets': [target.ref], 'pay': 'auto'})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual([target.ref], session.state.stack[-1].targets)
        self.assertEqual([], session.state.stack[-1].modes)
        self.assertTrue(session.state.stack[-1].semantic_key.endswith(':cleave'))
        self.assertEqual(2, session.engine._effective_card_data(session.state.cards[source.object_id])['mana_value'])
        self.assertEqual(0, sum(session.state.players['A'].mana_pool.values()))
        session = self.replay(session, load=True)
        self.resolve(session)
        self.assertEqual('graveyard', session.state.cards[target.object_id].zone)
        self.assertEqual('graveyard', session.state.cards[source.object_id].zone)
        self.replay(session)

    def test_normal_retrieval_keeps_controller_restriction_and_printed_price(self):
        session = self.session(70214802)
        engine = session.engine
        source = self.add(engine, "Alchemist's Retrieval", zone='hand', ref='cleave-normal-retrieval')
        own = self.add(engine, 'Generic Bound Body', ref='cleave-normal-own')
        enemy = self.add(engine, 'Llanowar Elves', seat='B', ref='cleave-normal-enemy')
        self.assert_closed(session, source)
        self.prepare(session, mana={'U': 1})
        self.checkpoint(session)
        self.reject(session, 'cast:' + source.ref, cost_option='normal', targets=[enemy.ref], pay='auto', cleave=True)
        self.reject(session, 'cast:' + source.ref, cost_option='cleave', targets=[enemy.ref], pay='auto')
        self.cast(session, 'A', source, [own.ref])
        self.assertFalse(session.state.stack[-1].semantic_key.endswith(':cleave'))
        self.assertEqual([], session.state.stack[-1].modes)
        session = self.replay(session, load=True)
        self.resolve(session)
        self.assertEqual('hand', session.state.cards[own.object_id].zone)
        self.assertEqual('battlefield', session.state.cards[enemy.object_id].zone)
        self.assertEqual(0, sum(session.state.players['A'].mana_pool.values()))
        self.replay(session)

    def test_native_reverberate_keeps_cleaved_target_scope_and_other_remaining_restrictions(self):
        session = self.session(70214803)
        engine = session.engine
        source = self.add(engine, "Alchemist's Retrieval", zone='hand', ref='cleave-copy-retrieval')
        copy = self.add(engine, 'Reverberate', zone='hand', ref='cleave-copy-producer')
        first = self.add(engine, 'Llanowar Elves', seat='B', ref='cleave-copy-first')
        second = self.add(engine, 'Llanowar Elves', seat='C', ref='cleave-copy-second')
        land = self.add(engine, 'Generic Bound Plains', seat='D', ref='cleave-copy-still-nonland')
        self.assert_closed(session, source)
        self.assert_closed(session, copy)
        self.prepare(session, mana={'C': 1, 'U': 1, 'R': 2})
        self.checkpoint(session)
        self.cast_cleaved(session, source, [first.ref], reserve={'R': 2})
        original = session.state.stack[-1]
        self.assertTrue(original.semantic_key.endswith(':cleave'))
        self.cast(session, 'A', copy, [original.ref])
        self.assertEqual('semantic.choice', self.resolve(session).kind)
        self.reject(session, 'choose', 'B', targets=[second.ref])
        self.reject(session, 'choose', targets=[land.ref])
        self.reject(session, 'choose', targets=[first.ref, second.ref])
        session = self.replay(session, load=True)
        result = session.act('pilot:A', {'action_id': 'choose', 'targets': [second.ref]})
        self.assertTrue(result.ok, result.summary)
        copied = next(item for item in session.state.stack if item.kind == 'spell_copy')
        self.assertEqual((original.semantic_key, [second.ref], []), (copied.semantic_key, copied.targets, copied.modes))
        self.resolve(session)
        for card, seat in ((first, 'B'), (second, 'C')):
            current = session.state.cards[card.object_id]
            self.assertEqual('hand', current.zone)
            self.assertIn(current.object_id, session.state.players[seat].zones['hand'])
        self.assertEqual('battlefield', session.state.cards[land.object_id].zone)
        self.assertEqual(2, len(session.engine._current_turn_history('spell_cast')))
        self.assertEqual(0, sum(session.state.players['A'].mana_pool.values()))
        self.replay(session)

    def test_target_free_winged_portent_variants_count_live_creatures_and_keep_draws_private(self):
        for paid in (False, True):
            with self.subTest(paid=paid):
                session = self.session(70214804 + paid)
                engine = session.engine
                source = self.add(engine, 'Winged Portent', zone='hand', ref='cleave-quantity-draw')
                self.add(engine, 'Wind Drake', ref='cleave-quantity-flyer')
                self.add(engine, 'Llanowar Elves', ref='cleave-quantity-ground')
                later = self.add(engine, 'Ashcoat Bear', zone='hand', ref='cleave-quantity-later')
                self.assert_closed(session, source)
                self.prepare(session, mana={'C': 5, 'G': 2, 'U': 1} if paid else {'C': 2, 'U': 2, 'G': 1})
                hand_before = len(session.state.players['A'].zones['hand'])
                library = set(session.state.players['A'].zones['library'])
                self.checkpoint(session)
                if paid:
                    self.cast_cleaved(session, source, reserve={'G': 1})
                else:
                    self.cast(session, 'A', source)
                self.assertEqual(([], []), (session.state.stack[-1].targets, session.state.stack[-1].modes))
                self.cast(session, 'A', later)
                session = self.replay(session, load=True)
                self.resolve(session)
                expected = 3 if paid else 1
                self.assertEqual(hand_before - 2 + expected, len(session.state.players['A'].zones['hand']))
                drawn = library.intersection(session.state.players['A'].zones['hand'])
                self.assertEqual(expected, len(drawn))
                for seat in 'BCD':
                    packet = json.dumps(session.packet('pilot:' + seat, full=True))
                    for oid in drawn:
                        self.assertNotIn(session.state.cards[oid].ref, packet)
                self.assertEqual(0, sum(session.state.players['A'].mana_pool.values()))
                self.replay(session)

    def test_cleaved_lunar_rejection_revalidates_departed_target_and_does_not_draw(self):
        session = self.session(70214806)
        engine = session.engine
        source = self.add(engine, 'Lunar Rejection', zone='hand', ref='cleave-lunar-stale')
        target = self.add(engine, 'Llanowar Elves', seat='B', ref='cleave-lunar-stale-target')
        bounce = self.add(engine, 'Unsummon', seat='B', zone='hand', ref='cleave-lunar-response')
        self.assert_closed(session, source)
        engine.state.players['B'].mana_pool['U'] = 1
        self.prepare(session, mana={'C': 3, 'U': 1})
        hand_before = len(session.state.players['A'].zones['hand'])
        self.checkpoint(session)
        self.cast_cleaved(session, source, [target.ref])
        self.cast(session, 'B', bounce, [target.ref])
        session = self.replay(session, load=True)
        self.resolve(session)
        self.assertEqual('hand', session.state.cards[target.object_id].zone)
        self.assertEqual(hand_before - 1, len(session.state.players['A'].zones['hand']))
        self.assertEqual('graveyard', session.state.cards[source.object_id].zone)
        self.replay(session)

    def test_free_base_cannot_select_cleaved_body_and_cost_increase_applies_to_cleave(self):
        from quorune.impulse_access import grant_temporary_cast_permission
        from quorune.impulse_access_model import TemporaryCastPermissionGrant, ImpulseAccessDuration
        for paid in (False, True):
            with self.subTest(paid=paid):
                session = self.session(70214807 + paid)
                engine = session.engine
                source = self.add(engine, "Alchemist's Retrieval", zone='hand', ref='cleave-free-boundary')
                own = self.add(engine, 'Llanowar Elves', ref='cleave-free-own')
                enemy = self.add(engine, 'Llanowar Elves', seat='B', ref='cleave-free-other')
                self.add(engine, 'Sphere of Resistance', ref='cleave-cost-increase')
                self.assert_closed(session, source)
                grant_temporary_cast_permission(engine, card=source,
                    grant=TemporaryCastPermissionGrant(player='A', duration=ImpulseAccessDuration.END_OF_TURN,
                        not_before_turn_sequence=None, without_mana_cost=True, source='cleave-free-base-contract'),
                    reason='Free normal execution cannot select the distinct bracket-removed alternative')
                self.prepare(session, mana={'C': 2, 'U': 1} if paid else {'C': 1})
                self.checkpoint(session)
                self.reject(session, 'cast:' + source.ref, cost_option='without_mana_cost', targets=[enemy.ref], pay='auto')
                if paid:
                    self.reject(session, 'cast:' + source.ref, cost_option='cleave', targets=[enemy.ref],
                        pay='manual', payment={'C': 1, 'U': 1})
                    self.cast_cleaved(session, source, [enemy.ref])
                else:
                    result = session.act('pilot:A', {'action_id': 'cast:' + source.ref, 'cost_option': 'without_mana_cost',
                        'targets': [own.ref], 'pay': 'auto'})
                    self.assertTrue(result.ok, result.summary)
                session = self.replay(session, load=True)
                self.resolve(session)
                self.assertEqual('hand' if paid else 'battlefield', session.state.cards[enemy.object_id].zone)
                self.assertEqual('battlefield' if paid else 'hand', session.state.cards[own.object_id].zone)
                self.assertEqual(0, sum(session.state.players['A'].mana_pool.values()))
                self.replay(session)

    def test_all_original_cleave_sources_require_both_programs_and_normal_registration(self):
        expected = {'Winged Portent', 'Fierce Retribution', "Alchemist's Retrieval", 'Dig Up', 'Lunar Rejection',
            'Parasitic Grasp', 'Dread Fugue'}
        rows = [r for r in json.loads(FIXTURE.read_text(encoding='utf-8'))['cards'] if 'Cleave' in r.get('keywords', ())]
        self.assertEqual(12, len(rows))
        for row in rows:
            with self.subTest(name=row['name']):
                record = self.db.by_oracle_id(row['oracle_id'])
                program = compile_best_available_card_program(self.db, record, semantic_registry=SemanticRegistry(),
                    capability_registry=self.registry, capability_profile='commander_review')
                binding = bind_card_program_runtime(program, capability_registry=self.registry, profile='commander_review')
                self.assertEqual(row['name'] in expected, binding['strict_capability_ready'], binding)
                if row['name'] in expected:
                    session = self.session(70214809)
                    source = self.add(session.engine, row['name'], zone='hand', ref='cleave-original-table')
                    self.assert_closed(session, source)
                    pairs = [p for p in session.engine.semantics.programs_for_oracle(record.oracle_id)
                        if 'fixed_cleave_cast_variant' in p.coverage]
                    self.assertEqual(2, len(pairs))
                    self.assertTrue(all(session.engine.semantic_program_is_current_trusted(p) for p in pairs))

    def test_missing_cleaved_lowering_prevents_false_whole_source_admission(self):
        from quorune.compiler import program_generation as owner
        original = owner._generated_ability_id
        def omitted(**values):
            return None if values['kind'] == 'spell_variant' and str(values.get('node_id')).endswith(':cleave') else original(**values)
        with mock.patch.object(owner, '_generated_ability_id', omitted):
            record = self.db.lookup('Fierce Retribution')
            program = compile_best_available_card_program(self.db, record, semantic_registry=SemanticRegistry(),
                capability_registry=self.registry, capability_profile='commander_review')
            self.assertFalse(bind_card_program_runtime(program, capability_registry=self.registry,
                profile='commander_review')['strict_capability_ready'])

    def test_cleave_price_omission_preserves_admission_but_changes_native_payment(self):
        from quorune.rules.casting import cleave_costs as owner
        original = owner.cleave_cost_options
        changed = []
        def omitted(*args, **kwargs):
            result = original(*args, **kwargs)
            for option in result or ():
                changed.append(option['requirements']['GENERIC'])
                option['requirements']['GENERIC'] = 0
            return result
        result = unittest.TestResult()
        witness = type(self)('test_actual_fierce_retribution_cleave_targets_nonattacker_with_empty_mode_list')
        with mock.patch.object(owner, 'cleave_cost_options', omitted):
            witness.run(result)
        self.assertTrue(changed)
        self.assertFalse(result.errors)
        self.assertEqual(1, len(result.failures))
        self.assertIn("sum(session.state.players['A'].mana_pool.values())", result.failures[0][1])
