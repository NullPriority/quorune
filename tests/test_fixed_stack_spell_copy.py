from __future__ import annotations

"""Pinned CR 707.10/115.7d: one stack spell copy with optional new targets."""

import json
import re
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
from quorune.card_programs.validation import program_source_is_current
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry
from quorune.stack_spell_copy_model import FixedStackSpellCopySpec, STACK_SPELL_COPY_CAPABILITY
from quorune.semantics import SemanticRegistry
from scripts.build_test_database import build_fixture_database
import test_spree_mode_costs as actions
import test_self_public_zone_casting as zone_actions


FIXTURE = ROOT / 'tests/fixtures/fixed-stack-spell-copy-cards.json'


class FixedStackSpellCopyModelCompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registry = load_default_capability_registry()

    def record(self, text='Copy target instant or sorcery spell. You may choose new targets for the copy.'):
        return CardRecord(oracle_id='fixture:stack-copy-boundary', name='Stack Copy Witness',
            mana_cost='{R}{R}', mana_value=2, type_line='Instant', oracle_text=text,
            power=None, toughness=None, loyalty=None, defense=None, colors=('R',), color_identity=('R',),
            keywords=(), produced_mana=(), layout='normal', released_at=None,
            legalities={'commander': 'legal'}, faces=(), raw={})

    def compile(self, record=None, registry=None):
        return compile_oracle_card(record or self.record(), capability_registry=registry or self.registry,
            capability_profile='commander_review')

    def test_fixed_stack_copy_spec_is_closed_and_current_controller_scoped(self):
        for controlled in (False, True):
            spec = FixedStackSpellCopySpec(controlled)
            self.assertEqual(spec, FixedStackSpellCopySpec.from_dict(spec.to_dict()))
            self.assertEqual(['stack'], spec.target_schema['zones'])
            self.assertEqual(['instant', 'sorcery'], spec.target_schema['types_any'])
            self.assertEqual('you' if controlled else None, spec.target_schema.get('controller_relation'))
        for value in ({'schema_version': True, 'controlled_only': False},
                {'schema_version': 1, 'controlled_only': 1},
                {'schema_version': 1, 'controlled_only': False, 'count': 2}):
            with self.assertRaises(ValueError):
                FixedStackSpellCopySpec.from_dict(value)

    def test_fixed_stack_copy_source_span_and_exact_shape_reject_unknown_variants(self):
        from quorune.rules.stack_spell_copy_shapes import fixed_stack_spell_copy_node_capabilities
        for controlled in (False, True):
            text='Copy target instant or sorcery spell' + (' you control' if controlled else '') + '. You may choose new targets for the copy.'
            ir = self.compile(self.record(text))
            self.assertEqual('exact', ir.status, ir.material_residuals)
            node = ir.faces[0].nodes[0]
            self.assertEqual((0, len(text)), (node.span.start, node.span.end))
            self.assertIn(STACK_SPELL_COPY_CAPABILITY, node.capability_dependencies)
            self.assertEqual(FixedStackSpellCopySpec(controlled).effects, node.effects)
            self.assertTrue(fixed_stack_spell_copy_node_capabilities(effects=node.effects,
                target_schema=node.target_schema, mechanic_ids=node.mechanics))
            self.assertFalse(fixed_stack_spell_copy_node_capabilities(effects=({**node.effects[0], 'count': 2},),
                target_schema=node.target_schema, mechanic_ids=node.mechanics))
        for text in ('Copy target instant or sorcery spell twice. You may choose new targets for the copies.',
                'Copy target creature spell. You may choose new targets for the copy.',
                'Copy target instant or sorcery spell, except that the copy is red. You may choose new targets for the copy.',
                'Copy target instant or sorcery spell.'):
            self.assertNotEqual('exact', self.compile(self.record(text)).status)
        self.assertNotEqual('exact', self.compile(self.record(self.record().oracle_text + '\nInvent an unrepresented sibling.')).status)
        registry = json.loads((ROOT / 'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        for capability in (STACK_SPELL_COPY_CAPABILITY, 'target.revalidate_resolution'):
            changed = deepcopy(registry)
            next(row for row in changed['capabilities'] if row['id'] == capability).update(status='blocked', blockers=['Copy owner withdrawn'])
            self.assertNotEqual('exact', self.compile(registry=CapabilityRegistry(changed)).status)

    def test_fixed_stack_copy_producer_omission_fails_independent_positive_contract(self):
        with mock.patch('quorune.compiler.fixed_stack_spell_copy.fixed_stack_spell_copy_template', return_value=None):
            with self.assertRaises(AssertionError):
                self.test_fixed_stack_copy_source_span_and_exact_shape_reject_unknown_variants()


class FixedStackSpellCopyRuntimeTests(unittest.TestCase):
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
        path = Path(cls.temp.name) / 'stack-copy.sqlite3'
        build_fixture_database([ROOT / 'tests/fixtures/bound-effect-program-cards.json', FIXTURE], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Stack copy witness', [DeckEntry('Generic Bound Commander', 1, 'commander'),
            DeckEntry('Generic Bound Plains', 30)], ['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temp.cleanup()

    def prepare(self, session, **kwargs):
        session.state.config.semantic_policy = 'trusted_only'
        return actions.SpreeModeCostRuntimeTests.prepare(self, session, **kwargs)

    def resolve_top(self, session):
        ref = session.state.stack[-1].ref
        for _ in range(16):
            if not any(item.ref == ref for item in session.state.stack):
                return
            self.assertEqual('priority', session.state.pending_decision.kind)
            result = session.act(session.pending_principals()[0], {'action_id': 'pass'})
            self.assertTrue(result.ok, result.summary)
        self.fail('Top stack item did not resolve through ordinary priority')

    def copy_activation(self, session, seat, source):
        for _ in range(16):
            if session.state.priority_player == seat:
                break
            self.assertTrue(session.act(session.pending_principals()[0], {'action_id': 'pass'}).ok)
        return next(row for row in session.packet('pilot:' + seat, full=True)['decision']['ctx']['legal']['actions']
            if row.get('source') == source.ref and row['id'].startswith('activate:')
            and 'stack' in json.dumps(row.get('target_schema', {})))

    def test_actual_reverberate_copies_a_spell_with_independent_targets_and_full_replay(self):
        session = self.session(70710001)
        engine = session.engine
        source = self.add(engine, 'Reverberate', zone='hand', ref='stack-copy-reverberate')
        growth = self.add(engine, 'Giant Growth', zone='hand', ref='stack-copy-original')
        first = self.add(engine, 'Llanowar Elves', ref='stack-copy-first')
        second = self.add(engine, 'Llanowar Elves', ref='stack-copy-second')
        enemy = self.add(engine, 'Llanowar Elves', seat='B', ref='stack-copy-untouched')
        private = self.add(engine, 'Generic Bound Growth', seat='D', zone='hand', ref='stack-copy-private')
        self.assert_closed(session, source)
        self.assert_closed(session, growth)
        self.prepare(session, mana={'G': 1, 'R': 2})
        self.checkpoint(session)
        self.cast(session, 'A', growth, [first.ref])
        original = session.state.stack[-1]
        self.cast(session, 'A', source, [original.ref])
        pending = self.resolve(session)
        self.assertEqual('semantic.choice', pending.kind)
        self.assertNotIn(private.ref, json.dumps(session.packet('pilot:C', full=True)))
        self.reject(session, 'choose', 'B', targets=[second.ref])
        self.reject(session, 'choose', targets=[first.ref, second.ref])
        session = self.replay(session, load=True)
        result = session.act('pilot:A', {'action_id': 'choose', 'targets': [second.ref]})
        self.assertTrue(result.ok, result.summary)
        copies = [item for item in session.state.stack if item.kind == 'spell_copy']
        self.assertEqual(1, len(copies))
        self.assertEqual((original.semantic_key, [second.ref], []),
            (copies[0].semantic_key, copies[0].targets, copies[0].modes))
        self.assertEqual([first.ref], next(item for item in session.state.stack if item.ref == original.ref).targets)
        self.resolve(session)
        self.assertEqual(4, session.engine._numeric_stat(first.object_id, 'power'))
        self.assertEqual(4, session.engine._numeric_stat(second.object_id, 'power'))
        self.assertEqual(1, session.engine._numeric_stat(enemy.object_id, 'power'))
        self.assertEqual(2, len(session.engine._current_turn_history('spell_cast')))
        self.assertEqual(0, sum(session.state.players['A'].mana_pool.values()))
        self.assertFalse(session.state.stack)
        self.replay(session)

    def test_original_countered_before_copy_producer_resolves_creates_no_copy(self):
        session = self.session(70710002)
        engine = session.engine
        source = self.add(engine, 'Reverberate', zone='hand', ref='stack-copy-stale-producer')
        growth = self.add(engine, 'Giant Growth', zone='hand', ref='stack-copy-stale-original')
        target = self.add(engine, 'Llanowar Elves', ref='stack-copy-stale-body')
        counter = self.add(engine, 'Counterspell', seat='B', zone='hand', ref='stack-copy-stale-counter')
        self.assert_closed(session, source)
        engine.state.players['B'].mana_pool['U'] = 2
        self.prepare(session, mana={'G': 1, 'R': 2})
        self.checkpoint(session)
        self.cast(session, 'A', growth, [target.ref])
        original_ref = session.state.stack[-1].ref
        self.cast(session, 'A', source, [original_ref])
        self.cast(session, 'B', counter, [original_ref])
        session = self.replay(session, load=True)
        self.assertIsNone(self.resolve(session))
        self.assertEqual(1, session.engine._numeric_stat(target.object_id, 'power'))
        self.assertEqual('graveyard', session.state.cards[source.object_id].zone)
        self.assertEqual('graveyard', session.state.cards[growth.object_id].zone)
        self.assertFalse(any(card.is_spell_copy for card in session.state.cards.values()))
        self.assertEqual(3, len(session.engine._current_turn_history('spell_cast')))
        self.replay(session)

    def test_retain_old_target_incarnation_or_choose_returned_creature_as_new_target(self):
        for change in (False, True):
            with self.subTest(change=change):
                session = self.session(70710003 + change)
                engine = session.engine
                source = self.add(engine, 'Reverberate', zone='hand', ref='stack-copy-incarnation-producer')
                growth = self.add(engine, 'Giant Growth', zone='hand', ref='stack-copy-incarnation-original')
                target = self.add(engine, 'Ashcoat Bear', ref='stack-copy-incarnation-target')
                bounce = self.add(engine, 'Unsummon', seat='B', zone='hand', ref='stack-copy-incarnation-bounce')
                self.assert_closed(session, source)
                engine.state.players['B'].mana_pool['U'] = 1
                self.prepare(session, mana={'C': 1, 'G': 2, 'R': 2})
                self.checkpoint(session)
                self.cast(session, 'A', growth, [target.ref])
                original = session.state.stack[-1]
                snapshot = deepcopy(original.context['target_snapshots'][target.ref])
                old_identity = target.logical_object_id
                self.cast(session, 'A', source, [original.ref])
                self.cast(session, 'B', bounce, [target.ref])
                self.resolve_top(session)
                self.assertEqual('hand', session.state.cards[target.object_id].zone)
                self.cast(session, 'A', target)
                self.resolve_top(session)
                self.assertNotEqual(old_identity, session.state.cards[target.object_id].logical_object_id)
                self.assertEqual('semantic.choice', self.resolve(session).kind)
                session = self.replay(session, load=True)
                response = {'action_id': 'choose', **({'targets': [{'ref': target.ref}]} if change else {})}
                result = session.act('pilot:A', response)
                self.assertTrue(result.ok, result.summary)
                copied = next(item for item in session.state.stack if item.kind == 'spell_copy')
                current_snapshot = copied.context['target_snapshots'][target.ref]
                self.assertEqual(not change, current_snapshot == snapshot)
                self.resolve(session)
                self.assertEqual(5 if change else 2, session.engine._numeric_stat(target.object_id, 'power'))
                self.assertEqual(0, sum(session.state.players['A'].mana_pool.values()))
                self.replay(session)

    def test_native_naru_entry_copy_target_is_restricted_to_current_controller(self):
        session = self.session(70710005)
        engine = session.engine
        mage = self.add(engine, 'Naru Meha, Master Wizard', zone='hand', ref='stack-copy-naru')
        growth = self.add(engine, 'Giant Growth', zone='hand', ref='stack-copy-naru-own-spell')
        shock = self.add(engine, 'Shock', seat='B', zone='hand', ref='stack-copy-naru-other-spell')
        bodies = [self.add(engine, 'Llanowar Elves', ref='stack-copy-naru-body-' + str(i)) for i in range(2)]
        self.assert_closed(session, mage)
        engine.state.players['B'].mana_pool['R'] = 1
        self.prepare(session, mana={'C': 2, 'U': 2, 'G': 1})
        self.checkpoint(session)
        self.cast(session, 'A', growth, [bodies[0].ref])
        own_ref = session.state.stack[-1].ref
        self.cast(session, 'B', shock, ['D'])
        other_ref = session.state.stack[-1].ref
        self.cast(session, 'A', mage)
        self.assertEqual('semantic.target', self.resolve(session).kind)
        schema = json.dumps(session.packet('pilot:A', full=True)['decision']['ctx']['target_schema'])
        self.assertIn(own_ref, schema)
        self.assertNotIn(other_ref, schema)
        self.reject(session, 'choose', targets=[other_ref])
        result = session.act('pilot:A', {'action_id': 'choose', 'targets': [own_ref]})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual('semantic.choice', self.resolve(session).kind)
        result = session.act('pilot:A', {'action_id': 'choose', 'targets': [bodies[1].ref]})
        self.assertTrue(result.ok, result.summary)
        session = self.replay(session, load=True)
        self.resolve(session)
        self.assertEqual([4, 4], [session.engine._numeric_stat(card.object_id, 'power') for card in bodies])
        self.assertEqual(38, session.state.players['D'].life)
        self.assertEqual(3, len(session.engine._current_turn_history('spell_cast')))
        self.replay(session)

    def test_native_dualcaster_copies_opponents_spell_under_trigger_controllers_authority(self):
        session = self.session(70710006)
        engine = session.engine
        mage = self.add(engine, 'Dualcaster Mage', zone='hand', ref='stack-copy-dualcaster')
        shock = self.add(engine, 'Shock', seat='B', zone='hand', ref='stack-copy-dualcaster-other-spell')
        self.assert_closed(session, mage)
        engine.state.players['B'].mana_pool['R'] = 1
        self.prepare(session, mana={'C': 1, 'R': 2})
        self.checkpoint(session)
        self.cast(session, 'B', shock, ['D'])
        original_ref = session.state.stack[-1].ref
        self.cast(session, 'A', mage)
        self.assertEqual('semantic.target', self.resolve(session).kind)
        self.reject(session, 'choose', 'B', targets=[original_ref])
        result = session.act('pilot:A', {'action_id': 'choose', 'targets': [original_ref]})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual('semantic.choice', self.resolve(session).kind)
        result = session.act('pilot:A', {'action_id': 'choose', 'targets': ['B']})
        self.assertTrue(result.ok, result.summary)
        copied = next(item for item in session.state.stack if item.kind == 'spell_copy')
        self.assertEqual(('A', ['B']), (copied.controller, copied.targets))
        session = self.replay(session, load=True)
        self.resolve(session)
        self.assertEqual((38, 38), (session.state.players['B'].life, session.state.players['D'].life))
        self.assertEqual(2, len(session.engine._current_turn_history('spell_cast')))
        self.replay(session)

    def test_unsupported_source_siblings_have_no_independent_executable_copy_fallback(self):
        session = self.session(70710007)
        engine = session.engine
        source = self.add(engine, 'Fury Storm', zone='hand', ref='stack-copy-incomplete-source')
        growth = self.add(engine, 'Giant Growth', zone='hand', ref='stack-copy-incomplete-target')
        body = self.add(engine, 'Llanowar Elves', ref='stack-copy-incomplete-body')
        self.prepare(session, mana={'C': 2, 'R': 2, 'G': 1})
        self.checkpoint(session)
        self.cast(session, 'A', growth, [body.ref])
        target_ref = session.state.stack[-1].ref
        actions = session.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']
        self.assertNotIn('cast:' + source.ref, [row['id'] for row in actions])
        self.reject(session, 'cast:' + source.ref, targets=[target_ref], pay='auto')
        self.resolve(session)
        self.assertEqual(4, session.engine._numeric_stat(body.object_id, 'power'))
        self.replay(session)

    def test_copy_source_buyback_pays_extra_and_returns_only_its_own_physical_card(self):
        session = self.session(70710010)
        engine = session.engine
        source = self.add(engine, 'Reiterate', zone='hand', ref='stack-copy-buyback')
        growth = self.add(engine, 'Giant Growth', zone='hand', ref='stack-copy-buyback-original')
        bodies = [self.add(engine, 'Llanowar Elves', ref='stack-copy-buyback-body-' + str(i)) for i in range(2)]
        self.assert_closed(session, source)
        self.prepare(session, mana={'C': 4, 'R': 2, 'G': 1})
        self.checkpoint(session)
        self.cast(session, 'A', growth, [bodies[0].ref])
        original_ref = session.state.stack[-1].ref
        result = session.act('pilot:A', {'action_id': 'cast:' + source.ref, 'cost_option': 'buyback',
            'targets': [original_ref], 'pay': 'auto'})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual('semantic.choice', self.resolve(session).kind)
        result = session.act('pilot:A', {'action_id': 'choose', 'targets': [bodies[1].ref]})
        self.assertTrue(result.ok, result.summary)
        session = self.replay(session, load=True)
        self.resolve(session)
        self.assertEqual('hand', session.state.cards[source.object_id].zone)
        self.assertEqual('graveyard', session.state.cards[growth.object_id].zone)
        self.assertEqual([4, 4], [session.engine._numeric_stat(card.object_id, 'power') for card in bodies])
        self.assertEqual(0, sum(session.state.players['A'].mana_pool.values()))
        copies = [card for card in session.state.cards.values() if card.is_spell_copy]
        self.assertEqual(1, len(copies))
        self.assertTrue(all(card.zone == 'outside' for card in copies))
        self.replay(session)

    def test_flare_copy_alternative_sacrifices_current_red_creature_instead_of_mana(self):
        session = self.session(70710011)
        engine = session.engine
        source = self.add(engine, 'Flare of Duplication', zone='hand', ref='stack-copy-flare')
        growth = self.add(engine, 'Giant Growth', zone='hand', ref='stack-copy-flare-original')
        creature = self.add(engine, 'Raging Goblin', ref='stack-copy-flare-red-creature')
        bodies = [self.add(engine, 'Llanowar Elves', ref='stack-copy-flare-body-' + str(i)) for i in range(2)]
        self.assert_closed(session, source)
        self.prepare(session, mana={'G': 1})
        self.checkpoint(session)
        self.cast(session, 'A', growth, [bodies[0].ref])
        original_ref = session.state.stack[-1].ref
        offered = next(row for row in session.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']
            if row['id'] == 'cast:' + source.ref)
        option = next(row for row in offered['cost_options'] if sum(row['requirements'].values()) == 0)
        self.reject(session, offered['id'], cost_option=option['id'], targets=[original_ref],
            sacrifice_cards=[bodies[0].ref], pay='auto')
        result = session.act('pilot:A', {'action_id': offered['id'], 'cost_option': option['id'],
            'targets': [original_ref], 'sacrifice_cards': [creature.ref], 'pay': 'auto'})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual('graveyard', session.state.cards[creature.object_id].zone)
        self.assertEqual('semantic.choice', self.resolve(session).kind)
        result = session.act('pilot:A', {'action_id': 'choose', 'targets': [bodies[1].ref]})
        self.assertTrue(result.ok, result.summary)
        session = self.replay(session, load=True)
        self.resolve(session)
        self.assertEqual([4, 4], [session.engine._numeric_stat(card.object_id, 'power') for card in bodies])
        self.assertEqual(0, sum(session.state.players['A'].mana_pool.values()))
        self.replay(session)

    def test_public_alternative_price_declares_reused_nonmana_payment_owner(self):
        from dataclasses import replace
        from quorune.public_alternative_costs import FIXED_PUBLIC_ALTERNATIVE_COST_CAPABILITY
        from quorune.card_programs.binding import bind_semantic_program_runtime
        program=compile_best_available_card_program(self.db,self.db.lookup('Flare of Duplication'),
            semantic_registry=SemanticRegistry(),capability_registry=self.registry,capability_profile='commander_review')
        price=next(ability for ability in program.abilities
            if FIXED_PUBLIC_ALTERNATIVE_COST_CAPABILITY in ability.capability_dependencies)
        payment='casting.additional_cost.fixed_sacrifice'
        self.assertIn(payment,price.capability_dependencies)
        remaining=tuple(value for value in price.capability_dependencies if value!=payment)
        stripped=replace(price,capability_dependencies=remaining,
            capability_closure=self.registry.closure(remaining,profile='commander_review').to_dict())
        binding=bind_semantic_program_runtime(stripped,capability_registry=self.registry,profile='commander_review')
        self.assertFalse(binding['strict'])
        self.assertIn('capability:undeclared_runtime_dependency:'+payment,binding['blockers'])

    def test_permanent_spell_and_activated_ability_are_not_eligible_spell_copy_targets(self):
        for ability in (False, True):
            with self.subTest(ability=ability):
                session = self.session(70710012 + ability)
                engine = session.engine
                source = self.add(engine, 'Reverberate', zone='hand', ref='stack-copy-wrong-kind')
                other = self.add(engine, 'Prodigal Pyromancer' if ability else 'Ashcoat Bear', seat='B',
                    zone='battlefield' if ability else 'hand', ref='stack-copy-wrong-kind-source')
                other.acquired_control_turn_count = -1
                self.assert_closed(session, source)
                self.prepare(session, mana={'R': 2})
                engine.state.players['B'].mana_pool.update({'C': 1, 'G': 1})
                self.checkpoint(session)
                if ability:
                    while session.state.priority_player != 'B':
                        self.assertTrue(session.act(session.pending_principals()[0], {'action_id': 'pass'}).ok)
                    offered = next(row for row in session.packet('pilot:B', full=True)['decision']['ctx']['legal']['actions']
                        if row.get('source') == other.ref and row['id'].startswith('activate:'))
                    result = session.act('pilot:B', {'action_id': offered['id'], 'targets': ['D'], 'pay': 'auto'})
                    self.assertTrue(result.ok, result.summary)
                else:
                    self.cast(session, 'B', other)
                target_ref = session.state.stack[-1].ref
                while session.state.priority_player != 'A':
                    self.assertTrue(session.act(session.pending_principals()[0], {'action_id': 'pass'}).ok)
                actions = session.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']
                self.assertNotIn('cast:' + source.ref, [row['id'] for row in actions])
                self.reject(session, 'cast:' + source.ref, targets=[target_ref], pay='auto')
                self.resolve(session)
                self.assertEqual('hand', session.state.cards[source.object_id].zone)
                self.assertEqual(2, sum(session.state.players['A'].mana_pool.values()))
                self.replay(session)

    def test_original_copy_candidates_need_complete_siblings_and_current_generated_registration(self):
        expected = {'Geistblast', 'Reiterate', 'Dual Casting', 'Sigil Tracer', 'Naru Meha, Master Wizard',
            'Mirrorpool', 'Dualcaster Mage', 'Twincast', 'Uyo, Silent Prophet', 'Reverberate',
            'Nivix Guildmage', 'Flare of Duplication', 'Kitsa, Otterball Elite', 'Stella Lee, Wild Card'}
        pattern = re.compile(r'Copy target instant or sorcery spell(?: you control)?\. You may choose new targets for the copy\.', re.I)
        rows = [r for r in json.loads(FIXTURE.read_text(encoding='utf-8'))['cards'] if pattern.search(r.get('oracle_text') or '')]
        self.assertEqual(24, len(rows))
        for row in rows:
            with self.subTest(name=row['name']):
                record = self.db.by_oracle_id(row['oracle_id'])
                program = compile_best_available_card_program(self.db, record, semantic_registry=SemanticRegistry(),
                    capability_registry=self.registry, capability_profile='commander_review')
                binding = bind_card_program_runtime(program, capability_registry=self.registry, profile='commander_review')
                self.assertEqual(row['name'] in expected, binding['strict_capability_ready'], binding)
                if row['name'] in expected:
                    session = self.session(70710008)
                    source = self.add(session.engine, row['name'], zone='hand', ref='stack-copy-original-table')
                    self.assert_closed(session, source)
                    generated = [p for p in session.engine.semantics.programs_for_oracle(record.oracle_id)
                        if 'fixed-stack-spell-copy' in p.coverage]
                    self.assertTrue(generated)
                    self.assertTrue(all(session.engine.semantic_program_is_current_trusted(p) for p in generated))

    def test_native_geistblast_exiles_itself_from_graveyard_and_retains_copy_choice(self):
        session = self.session(70710014)
        engine = session.engine
        source = self.add(engine, 'Geistblast', zone='graveyard', ref='stack-copy-geistblast')
        growth = self.add(engine, 'Giant Growth', zone='hand', ref='stack-copy-geistblast-original')
        bodies = [self.add(engine, 'Llanowar Elves', ref='stack-copy-geistblast-body-' + str(i)) for i in range(2)]
        self.assert_closed(session, source)
        self.prepare(session, mana={'C': 2, 'U': 1, 'G': 1})
        self.checkpoint(session)
        self.cast(session, 'A', growth, [bodies[0].ref])
        original_ref = session.state.stack[-1].ref
        action = self.copy_activation(session, 'A', source)
        self.reject(session, action['id'], 'B', targets=[original_ref], pay='auto')
        result = session.act('pilot:A', {'action_id': action['id'], 'targets': [original_ref], 'pay': 'auto'})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual('exile', session.state.cards[source.object_id].zone)
        self.assertEqual('semantic.choice', self.resolve(session).kind)
        session = self.replay(session, load=True)
        result = session.act('pilot:A', {'action_id': 'choose', 'targets': [bodies[1].ref]})
        self.assertTrue(result.ok, result.summary)
        self.resolve(session)
        self.assertEqual([4, 4], [session.engine._numeric_stat(card.object_id, 'power') for card in bodies])
        self.assertEqual('exile', session.state.cards[source.object_id].zone)
        self.assertEqual(1, len(session.engine._current_turn_history('spell_cast')))
        self.assertEqual(0, sum(session.state.players['A'].mana_pool.values()))
        self.replay(session)

    def test_native_sigil_tracer_taps_two_current_wizards_including_itself(self):
        session = self.session(70710015)
        engine = session.engine
        source = self.add(engine, 'Sigil Tracer', ref='stack-copy-sigil')
        wizard = self.add(engine, 'Dualcaster Mage', ref='stack-copy-sigil-wizard')
        wrong = self.add(engine, 'Llanowar Elves', ref='stack-copy-sigil-nonwizard')
        growth = self.add(engine, 'Giant Growth', zone='hand', ref='stack-copy-sigil-original')
        self.assert_closed(session, source)
        self.prepare(session, mana={'C': 1, 'U': 1, 'G': 1})
        self.checkpoint(session)
        self.cast(session, 'A', growth, [wrong.ref])
        original_ref = session.state.stack[-1].ref
        action = self.copy_activation(session, 'A', source)
        for refs in ([source.ref], [source.ref, source.ref], [source.ref, wrong.ref]):
            self.reject(session, action['id'], targets=[original_ref], cost_cards=refs, pay='auto')
        result = session.act('pilot:A', {'action_id': action['id'], 'targets': [original_ref],
            'cost_cards': [source.ref, wizard.ref], 'pay': 'auto'})
        self.assertTrue(result.ok, result.summary)
        self.assertTrue(all(session.state.cards[card.object_id].tapped for card in (source, wizard)))
        self.assertEqual('semantic.choice', self.resolve(session).kind)
        session = self.replay(session, load=True)
        result = session.act('pilot:A', {'action_id': 'choose'})
        self.assertTrue(result.ok, result.summary)
        self.resolve(session)
        self.assertEqual(7, session.engine._numeric_stat(wrong.object_id, 'power'))
        self.assertEqual(1, len(session.engine._current_turn_history('spell_cast')))
        self.assertEqual(0, sum(session.state.players['A'].mana_pool.values()))
        self.replay(session)

    def test_native_dual_casting_uses_recipient_controller_instead_of_aura_controller(self):
        session = self.session(70710016)
        engine = session.engine
        aura = self.add(engine, 'Dual Casting', zone='hand', ref='stack-copy-dual-casting')
        recipient = self.add(engine, 'Llanowar Elves', seat='B', ref='stack-copy-granted-recipient')
        recipient.acquired_control_turn_count = -1
        own_body = self.add(engine, 'Llanowar Elves', ref='stack-copy-aura-controller-body')
        growth = self.add(engine, 'Giant Growth', zone='hand', ref='stack-copy-aura-controller-spell')
        shock = self.add(engine, 'Shock', seat='B', zone='hand', ref='stack-copy-recipient-controller-spell')
        self.assert_closed(session, aura)
        engine.state.players['B'].mana_pool['R'] = 2
        self.prepare(session, mana={'C': 1, 'R': 1, 'G': 1})
        self.checkpoint(session)
        self.cast(session, 'A', aura, [recipient.ref])
        self.resolve(session)
        self.cast(session, 'A', growth, [own_body.ref])
        foreign_ref = session.state.stack[-1].ref
        self.cast(session, 'B', shock, ['C'])
        own_ref = session.state.stack[-1].ref
        action = self.copy_activation(session, 'B', recipient)
        self.reject(session, action['id'], 'A', targets=[own_ref], pay='auto')
        self.reject(session, action['id'], 'B', targets=[foreign_ref], pay='auto')
        result = session.act('pilot:B', {'action_id': action['id'], 'targets': [own_ref], 'pay': 'auto'})
        self.assertTrue(result.ok, result.summary)
        self.assertTrue(session.state.cards[recipient.object_id].tapped)
        self.assertEqual('semantic.choice', self.resolve(session).kind)
        self.reject(session, 'choose', 'A', targets=['D'])
        session = self.replay(session, load=True)
        result = session.act('pilot:B', {'action_id': 'choose', 'targets': ['D']})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual('B', next(item for item in session.state.stack if item.kind == 'spell_copy').controller)
        self.resolve(session)
        self.assertEqual((38, 38), (session.state.players['C'].life, session.state.players['D'].life))
        self.assertEqual(4, session.engine._numeric_stat(own_body.object_id, 'power'))
        self.assertEqual(3, len(session.engine._current_turn_history('spell_cast')))
        self.replay(session)

    def test_native_uyo_can_use_land_mana_before_returning_two_lands_to_owners_hands(self):
        session = self.session(70710017)
        engine = session.engine
        source = self.add(engine, 'Uyo, Silent Prophet', ref='stack-copy-uyo')
        lands = [self.add(engine, 'Generic Bound Plains', ref='stack-copy-uyo-land-' + str(i)) for i in range(2)]
        lands[1].owner = 'B'
        growth = self.add(engine, 'Giant Growth', zone='hand', ref='stack-copy-uyo-original')
        bodies = [self.add(engine, 'Llanowar Elves', ref='stack-copy-uyo-body-' + str(i)) for i in range(2)]
        self.assert_closed(session, source)
        self.prepare(session, mana={'G': 1})
        self.checkpoint(session)
        self.cast(session, 'A', growth, [bodies[0].ref])
        original_ref = session.state.stack[-1].ref
        action = self.copy_activation(session, 'A', source)
        self.reject(session, action['id'], targets=[original_ref], cost_cards=[lands[0].ref] * 2, pay='auto')
        result = session.act('pilot:A', {'action_id': action['id'], 'targets': [original_ref],
            'cost_cards': [card.ref for card in lands], 'pay': 'auto'})
        self.assertTrue(result.ok, result.summary)
        for card, owner in zip(lands, 'AB', strict=True):
            current = session.state.cards[card.object_id]
            self.assertEqual((owner, 'hand'), (current.owner, current.zone))
            self.assertIn(current.object_id, session.state.players[owner].zones['hand'])
        self.assertEqual(0, sum(session.state.players['A'].mana_pool.values()))
        self.assertEqual('semantic.choice', self.resolve(session).kind)
        session = self.replay(session, load=True)
        result = session.act('pilot:A', {'action_id': 'choose', 'targets': [bodies[1].ref]})
        self.assertTrue(result.ok, result.summary)
        self.resolve(session)
        self.assertEqual([4, 4], [session.engine._numeric_stat(card.object_id, 'power') for card in bodies])
        self.replay(session)

    def test_whole_source_certificate_is_enforced_without_forcing_trust(self):
        from quorune.rules.stack_spell_copy_shapes import fixed_stack_spell_copy_program_is_closed
        session = self.session(70710009)
        source = self.add(session.engine, 'Reverberate', zone='hand', ref='stack-copy-certificate')
        program = next(p for p in session.engine.semantics.programs_for_oracle(source.oracle_id)
            if 'fixed-stack-spell-copy' in p.coverage)
        self.assertTrue(program_source_is_current(self.db, program))
        self.assertTrue(fixed_stack_spell_copy_program_is_closed(program))
        self.assertIs(program.provenance['requires_complete_card_program'], True)
        for certificate in ({'schema_version': 1, 'oracle_ir_status': 'partial', 'material_residual_count': 1},
                {'schema_version': True, 'oracle_ir_status': 'exact', 'material_residual_count': 0}, None):
            changed = replace(program, provenance={**deepcopy(program.provenance), 'card_program_admission': certificate})
            self.assertFalse(program_source_is_current(self.db, changed))
            self.assertFalse(fixed_stack_spell_copy_program_is_closed(changed))
        missing = replace(program, provenance={key: value for key, value in program.provenance.items()
            if key != 'requires_complete_card_program'})
        self.assertFalse(fixed_stack_spell_copy_program_is_closed(missing))

    def test_stack_copy_commit_omission_preserves_admission_but_fails_native_copy_consequence(self):
        from quorune.semantic_choices.intent_host import SemanticChoiceIntentHostMixin
        result = unittest.TestResult()
        witness = type(self)('test_actual_reverberate_copies_a_spell_with_independent_targets_and_full_replay')
        with mock.patch.object(SemanticChoiceIntentHostMixin, 'copy_stack_item_intent', return_value='omitted-copy'):
            witness.run(result)
        self.assertFalse(result.errors)
        self.assertEqual(1, len(result.failures))
        self.assertIn('self.assertEqual(1, len(copies))', result.failures[0][1])
