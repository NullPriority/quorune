from __future__ import annotations

"""Pinned CR 702.78: two distinct eligible tap costs and one copy trigger."""

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
from quorune.conspire_model import FixedConspireSpec, CONSPIRE_CAPABILITY, validate_conspire_fact
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry
from quorune.semantic_runtime.context import SemanticNodeError
from quorune.semantics import SemanticRegistry
from scripts.build_test_database import build_fixture_database
import test_spree_mode_costs as actions
import test_self_public_zone_casting as zone_actions


FIXTURE = ROOT / 'tests/fixtures/fixed-conspire-cards.json'


class FixedConspireModelCompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registry = load_default_capability_registry()

    def record(self, keyword='Conspire', body='Destroy target artifact or enchantment.', **changes):
        return replace(CardRecord(oracle_id='fixture:conspire-boundary', name='Conspire Witness',
            mana_cost='{1}{G}', mana_value=2, type_line='Sorcery', oracle_text=body + '\n' + keyword,
            power=None, toughness=None, loyalty=None, defense=None, colors=('G',), color_identity=('G',),
            keywords=('Conspire',), produced_mana=(), layout='normal', released_at=None,
            legalities={'commander': 'legal'}, faces=(), raw={}), **changes)

    def compile(self, record=None, registry=None):
        return compile_oracle_card(record or self.record(), capability_registry=registry or self.registry,
            capability_profile='commander_review')

    def test_conspire_spec_and_paid_pair_have_closed_distinct_incarnations(self):
        spec = FixedConspireSpec('front:conspire:line:2')
        self.assertEqual(spec, FixedConspireSpec.from_dict(spec.to_dict()))
        rows = [{'ref': 'R1', 'logical_object_id': 'object:1@0'}, {'ref': 'R2', 'logical_object_id': 'object:2@0'}]
        fact = spec.payment_fact(rows)
        rows[0]['ref'] = 'changed'
        self.assertEqual('R1', fact['objects'][0]['ref'])
        for change in ({'schema_version': True}, {'instance_id': ''}, {'extra': 1}):
            with self.assertRaises(ValueError):
                FixedConspireSpec.from_dict({**spec.to_dict(), **change})
        for change in ({'schema_version': True}, {'objects': []}, {'objects': [fact['objects'][0]] * 2},
                {'objects': [{'ref': 'R1', 'logical_object_id': True}, fact['objects'][1]]}, {'extra': 1}):
            with self.assertRaises(ValueError):
                validate_conspire_fact({**fact, **change})

    def test_conspire_source_span_handler_and_unsupported_payment_combinations(self):
        from quorune.semantic_runtime.conspire import FixedConspireHandler
        record = self.record()
        ir = self.compile(record)
        self.assertEqual('exact', ir.status, ir.material_residuals)
        node = ir.faces[0].nodes[-1]
        self.assertEqual('Conspire', node.text)
        self.assertEqual(record.oracle_text.index('Conspire'), node.span.start)
        self.assertIn(CONSPIRE_CAPABILITY, node.capability_dependencies)
        handler = FixedConspireHandler()
        self.assertEqual('front:conspire:line:2', handler.validate(node.handlers[0]).instance_id)
        for field, value in [('schema_version', True), ('requires_complete_card_program', False), ('extra', 1)]:
            with self.assertRaises(SemanticNodeError):
                handler.validate({**node.handlers[0], field: value})
        for keyword in ('Conspire {2}', 'Conspire\nConspire', 'Convoke\nConspire', 'Replicate {G}\nConspire'):
            self.assertNotEqual('exact', self.compile(self.record(keyword=keyword)).status)
        for change in ({'type_line': 'Creature — Wizard', 'power': '2', 'toughness': '2'},
                {'mana_cost': '{X}{G}'}, {'oracle_text': 'Invent an unrepresented sibling.\nConspire'}):
            self.assertNotEqual('exact', self.compile(self.record(**change)).status)
        registry = json.loads((ROOT / 'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        for capability in (CONSPIRE_CAPABILITY, 'target.revalidate_resolution', 'trigger.placement.apnap'):
            changed = deepcopy(registry)
            next(row for row in changed['capabilities'] if row['id'] == capability).update(status='blocked', blockers=['Conspire owner withdrawn'])
            self.assertNotEqual('exact', self.compile(registry=CapabilityRegistry(changed)).status)

    def test_conspire_producer_omission_fails_the_independent_positive_contract(self):
        with mock.patch('quorune.compiler.conspire_nodes.printed_conspire_spec', return_value=None):
            with self.assertRaises(AssertionError):
                self.test_conspire_source_span_handler_and_unsupported_payment_combinations()


class FixedConspireRuntimeTests(unittest.TestCase):
    register_fixture_cards_on_add = True
    session = actions.SpreeModeCostRuntimeTests.session
    add = actions.SpreeModeCostRuntimeTests.add
    checkpoint = actions.SpreeModeCostRuntimeTests.checkpoint
    resolve = actions.SpreeModeCostRuntimeTests.resolve
    replay = actions.SpreeModeCostRuntimeTests.replay
    prepare = actions.SpreeModeCostRuntimeTests.prepare
    assert_closed = actions.SpreeModeCostRuntimeTests.assert_closed
    reject = actions.SpreeModeCostRuntimeTests.reject
    cast = zone_actions.SelfPublicZoneRuntimeTests.cast

    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        path = Path(cls.temp.name) / 'conspire.sqlite3'
        build_fixture_database([ROOT / 'tests/fixtures/bound-effect-program-cards.json', FIXTURE], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Conspire witness', [DeckEntry('Generic Bound Commander', 1, 'commander'),
            DeckEntry('Generic Bound Plains', 30)], ['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temp.cleanup()

    def conspire_option(self, session, source, *, cost_base=None):
        for _ in range(16):
            if session.state.priority_player == 'A':
                break
            self.assertTrue(session.act(session.pending_principals()[0], {'action_id': 'pass'}).ok)
        source = session.state.cards[source.object_id]
        offered = next(row for row in session.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']
            if row['id'] == 'cast:' + source.ref)
        options = [row for row in offered['cost_options'] if '+conspire:' in row['id']
            and (cost_base is None or row['id'].startswith(cost_base + '+conspire:'))]
        self.assertEqual(1, len(options), options)
        return offered['id'], options[0]['id']

    def cast_conspired(self, session, source, targets=(), *, cost_base=None, **payment):
        action_id, option = self.conspire_option(session, source, cost_base=cost_base)
        result = session.act('pilot:A', {'action_id': action_id, 'cost_option': option,
            'targets': list(targets), 'pay': 'auto', **payment})
        self.assertTrue(result.ok, result.summary)

    def choose_copy(self, session, targets):
        result = session.act('pilot:A', {'action_id': 'choose', 'copy_targets': [list(targets)]})
        self.assertTrue(result.ok, result.summary)

    def test_actual_sabotage_taps_newly_cast_creatures_and_retargets_one_copy(self):
        session = self.session(70278001)
        engine = session.engine
        source = self.add(engine, 'Gleeful Sabotage', zone='hand', ref='conspire-sabotage')
        first = self.add(engine, 'Llanowar Elves', zone='hand', ref='conspire-first-elf')
        second = self.add(engine, 'Llanowar Elves', zone='hand', ref='conspire-second-elf')
        original_target = self.add(engine, 'Sphere of Resistance', seat='B', ref='conspire-original-target')
        copy_target = self.add(engine, 'Sphere of Resistance', seat='C', ref='conspire-copy-target')
        private = self.add(engine, 'Generic Bound Growth', seat='D', zone='hand', ref='conspire-private')
        self.assert_closed(session, source)
        self.prepare(session, mana={'C': 7, 'G': 3})
        self.checkpoint(session)
        self.cast(session, 'A', first)
        self.resolve(session)
        self.cast(session, 'A', second)
        self.resolve(session)
        for card in (first, second):
            current = session.state.cards[card.object_id]
            self.assertFalse(current.tapped)
            offered = session.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']
            self.assertFalse(any(row['id'].startswith('activate:') and row.get('source') == current.ref for row in offered))
        self.assertNotIn(private.ref, json.dumps(session.packet('pilot:C', full=True)))
        offered = next(row for row in session.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']
            if row['id'] == 'cast:' + source.ref)
        option = next(row['id'] for row in offered['cost_options'] if '+conspire:' in row['id'])
        self.reject(session, offered['id'], 'B', cost_option=option, targets=[original_target.ref], pay='auto')
        result = session.act('pilot:A', {'action_id': offered['id'], 'cost_option': option,
            'targets': [original_target.ref], 'pay': 'auto'})
        self.assertTrue(result.ok, result.summary)
        self.assertTrue(all(session.state.cards[c.object_id].tapped for c in (first, second)))
        self.assertEqual(0, sum(session.state.players['A'].mana_pool.values()))
        self.assertEqual(2, len(session.state.stack))
        pending = self.resolve(session)
        self.assertEqual('semantic.storm', pending.kind)
        self.reject(session, 'choose', 'C', copy_targets=[[copy_target.ref]])
        session = self.replay(session, load=True)
        result = session.act('pilot:A', {'action_id': 'choose', 'copy_targets': [[copy_target.ref]]})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(1, sum(item.kind == 'spell_copy' for item in session.state.stack))
        self.resolve(session)
        self.assertEqual('graveyard', session.state.cards[original_target.object_id].zone)
        self.assertEqual('graveyard', session.state.cards[copy_target.object_id].zone)
        self.assertEqual(3, len(session.engine._current_turn_history('spell_cast')))
        self.assertFalse(session.state.stack)
        self.replay(session)

    def test_multicolored_source_accepts_different_shared_colors_and_rejects_ineligible_pairs(self):
        session = self.session(70278002)
        engine = session.engine
        source = self.add(engine, 'Memory Sluice', zone='hand', ref='conspire-multicolored')
        blue = self.add(engine, 'Merfolk of the Pearl Trident', ref='conspire-blue')
        black = self.add(engine, 'Walking Corpse', ref='conspire-black-foreign-owner')
        black.owner = 'D'
        wrong_color = self.add(engine, 'Llanowar Elves', ref='conspire-wrong-color')
        tapped = self.add(engine, 'Merfolk of the Pearl Trident', ref='conspire-tapped')
        phased = self.add(engine, 'Merfolk of the Pearl Trident', ref='conspire-phased')
        foreign = self.add(engine, 'Merfolk of the Pearl Trident', seat='B', ref='conspire-foreign-controller')
        curiosity = self.add(engine, 'Curiosity', zone='hand', ref='conspire-blue-noncreature')
        tapped.tapped = True
        phased.phased_out = True
        self.assert_closed(session, source)
        self.prepare(session, mana={'U': 2})
        library = len(session.state.players['B'].zones['library'])
        self.checkpoint(session)
        self.cast(session, 'A', curiosity, [wrong_color.ref])
        self.resolve(session)
        self.assertEqual(wrong_color.object_id, session.state.cards[curiosity.object_id].attached_to)
        self.assertEqual(['B', 'U'], sorted(session.engine._effective_card_data(source)['colors']))
        self.cast_conspired(session, source, ['B'])
        self.assertTrue(session.state.cards[blue.object_id].tapped)
        self.assertTrue(session.state.cards[black.object_id].tapped)
        for card in (wrong_color, phased, foreign, curiosity):
            self.assertFalse(session.state.cards[card.object_id].tapped)
        self.assertEqual('semantic.storm', self.resolve(session).kind)
        session = self.replay(session, load=True)
        result = session.act('pilot:A', {'action_id': 'choose'})
        self.assertTrue(result.ok, result.summary)
        self.resolve(session)
        self.assertEqual(library - 8, len(session.state.players['B'].zones['library']))
        self.assertEqual(0, sum(session.state.players['A'].mana_pool.values()))
        self.replay(session)

    def test_conspirators_cannot_also_produce_cast_mana_and_manual_double_use_rolls_back(self):
        for pooled in (False, True):
            with self.subTest(pooled=pooled):
                session = self.session(70278003 + pooled)
                engine = session.engine
                source = self.add(engine, 'Barkshell Blessing', zone='hand', ref='conspire-mana-boundary')
                first = self.add(engine, 'Llanowar Elves', ref='conspire-mana-first')
                second = self.add(engine, 'Llanowar Elves', ref='conspire-mana-second')
                for card in (first, second):
                    card.acquired_control_turn_count = -1
                self.assert_closed(session, source)
                self.prepare(session, mana={'G': 1} if pooled else {})
                self.checkpoint(session)
                if not pooled:
                    offered = next(row for row in session.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']
                        if row['id'] == 'cast:' + source.ref)
                    self.assertFalse(any('+conspire:' in row['id'] for row in offered['cost_options']))
                    self.cast(session, 'A', source, [first.ref])
                    self.assertEqual(1, len(session.state.stack))
                    self.assertEqual(1, sum(session.state.cards[c.object_id].tapped for c in (first, second)))
                else:
                    action_id, option = self.conspire_option(session, source)
                    self.reject(session, action_id, cost_option=option, targets=[first.ref], pay='manual',
                        mana=[{'source': first.ref, 'bundle': {'G': 1}}], payment={'G': 1})
                    self.cast_conspired(session, source, [first.ref])
                    self.assertEqual('semantic.storm', self.resolve(session).kind)
                    self.choose_copy(session, [second.ref])
                    self.assertTrue(all(session.state.cards[c.object_id].tapped for c in (first, second)))
                session = self.replay(session, load=True)
                self.resolve(session)
                self.assertEqual(3, session.engine._numeric_stat(first.object_id, 'power'))
                self.assertEqual(3 if pooled else 1, session.engine._numeric_stat(second.object_id, 'power'))
                self.assertEqual(0, sum(session.state.players['A'].mana_pool.values()))
                self.replay(session)

    def test_departed_and_returned_conspirator_invalidates_old_pair_option(self):
        session = self.session(70278005)
        engine = session.engine
        source = self.add(engine, 'Memory Sluice', zone='hand', ref='conspire-incarnation-spell')
        blue = self.add(engine, 'Merfolk of the Pearl Trident', ref='conspire-incarnation-blue')
        self.add(engine, 'Walking Corpse', ref='conspire-incarnation-black')
        bounce = self.add(engine, 'Unsummon', zone='hand', ref='conspire-incarnation-bounce')
        self.assert_closed(session, source)
        self.prepare(session, mana={'U': 3})
        self.checkpoint(session)
        _, old_option = self.conspire_option(session, source)
        old_identity = blue.logical_object_id
        self.cast(session, 'A', bounce, [blue.ref])
        self.resolve(session)
        self.cast(session, 'A', blue)
        self.resolve(session)
        self.assertNotEqual(old_identity, session.state.cards[blue.object_id].logical_object_id)
        action_id, current_option = self.conspire_option(session, source)
        self.assertNotEqual(old_option, current_option)
        self.reject(session, action_id, cost_option=old_option, targets=['B'], pay='auto')
        self.cast_conspired(session, source, ['B'])
        self.assertEqual('semantic.storm', self.resolve(session).kind)
        self.choose_copy(session, ['C'])
        session = self.replay(session, load=True)
        self.resolve(session)
        self.replay(session)

    def test_countered_original_keeps_one_paid_conspire_copy_and_no_extra_cast(self):
        session = self.session(70278006)
        engine = session.engine
        source = self.add(engine, 'Burn Trail', zone='hand', ref='conspire-countered')
        goblins = [self.add(engine, 'Raging Goblin', ref='conspire-red-' + str(i)) for i in range(2)]
        counter = self.add(engine, 'Counterspell', seat='B', zone='hand', ref='conspire-original-counter')
        self.assert_closed(session, source)
        engine.state.players['B'].mana_pool['U'] = 2
        self.prepare(session, mana={'C': 3, 'R': 1})
        lives = {seat: session.state.players[seat].life for seat in 'CD'}
        self.checkpoint(session)
        self.cast_conspired(session, source, ['D'])
        original = next(item for item in session.state.stack if item.kind == 'spell')
        self.cast(session, 'B', counter, [original.ref])
        self.assertEqual('semantic.storm', self.resolve(session).kind)
        self.assertEqual('graveyard', session.state.cards[source.object_id].zone)
        self.assertEqual(1, len(session.state.stack))
        session = self.replay(session, load=True)
        self.choose_copy(session, ['C'])
        self.resolve(session)
        self.assertEqual(lives['C'] - 3, session.state.players['C'].life)
        self.assertEqual(lives['D'], session.state.players['D'].life)
        self.assertEqual(2, len(session.engine._current_turn_history('spell_cast')))
        self.assertTrue(all(session.state.cards[card.object_id].tapped for card in goblins))
        self.assertFalse(session.state.stack)
        self.replay(session)

    def test_free_base_keeps_conspire_tap_cost_and_independent_mill_targets(self):
        from quorune.impulse_access import grant_temporary_cast_permission
        from quorune.impulse_access_model import TemporaryCastPermissionGrant, ImpulseAccessDuration
        session = self.session(70278007)
        engine = session.engine
        source = self.add(engine, 'Memory Sluice', zone='hand', ref='conspire-free-base')
        bodies = [self.add(engine, 'Merfolk of the Pearl Trident', ref='conspire-free-' + str(i)) for i in range(2)]
        self.assert_closed(session, source)
        grant_temporary_cast_permission(engine, card=source,
            grant=TemporaryCastPermissionGrant(player='A', duration=ImpulseAccessDuration.END_OF_TURN,
                not_before_turn_sequence=None, without_mana_cost=True, source='conspire-free-base-contract'),
            reason='Free base does not waive an optional Conspire tap payment')
        self.prepare(session, mana={})
        libraries = {seat: len(session.state.players[seat].zones['library']) for seat in 'BC'}
        self.checkpoint(session)
        self.cast_conspired(session, source, ['B'], cost_base='without_mana_cost')
        self.assertTrue(all(session.state.cards[card.object_id].tapped for card in bodies))
        self.assertEqual('semantic.storm', self.resolve(session).kind)
        self.choose_copy(session, ['C'])
        session = self.replay(session, load=True)
        self.resolve(session)
        for seat in 'BC':
            self.assertEqual(libraries[seat] - 4, len(session.state.players[seat].zones['library']))
        self.assertEqual(0, sum(session.state.players['A'].mana_pool.values()))
        self.replay(session)

    def test_actual_rally_spell_and_copy_create_two_untapped_independent_creature_tokens(self):
        session = self.session(70278009)
        engine = session.engine
        source = self.add(engine, 'Rally the Galadhrim', zone='hand', ref='conspire-rally')
        blue = self.add(engine, 'Merfolk of the Pearl Trident', ref='conspire-rally-blue')
        green = self.add(engine, 'Llanowar Elves', ref='conspire-rally-green')
        self.assert_closed(session, source)
        self.prepare(session, mana={'C': 2, 'G': 1, 'U': 1})
        self.checkpoint(session)
        self.cast_conspired(session, source, [blue.ref])
        self.assertTrue(all(session.state.cards[c.object_id].tapped for c in (blue, green)))
        self.assertEqual('semantic.storm', self.resolve(session).kind)
        self.choose_copy(session, [green.ref])
        session = self.replay(session, load=True)
        self.resolve(session)
        tokens = [card for card in session.state.cards.values() if card.is_token and card.zone == 'battlefield']
        self.assertEqual(2, len(tokens))
        self.assertEqual({'Merfolk of the Pearl Trident', 'Llanowar Elves'},
            {session.engine._effective_card_data(card)['name'] for card in tokens})
        self.assertTrue(all(not card.tapped for card in tokens))
        self.assertEqual(1, len(session.engine._current_turn_history('spell_cast')))
        self.assertEqual(0, sum(session.state.players['A'].mana_pool.values()))
        self.replay(session)

    def test_original_conspire_cards_require_complete_normal_registration(self):
        expected = {'Ghastly Discovery', 'Gleeful Sabotage', 'Burn Trail', 'Rally the Galadhrim',
            'Memory Sluice', 'Barkshell Blessing', 'Aethertow'}
        rows = [r for r in json.loads(FIXTURE.read_text(encoding='utf-8'))['cards']
            if 'Conspire' in r.get('keywords', ())]
        self.assertEqual(11, len(rows))
        for row in rows:
            with self.subTest(name=row['name']):
                record = self.db.by_oracle_id(row['oracle_id'])
                program = compile_best_available_card_program(self.db, record, semantic_registry=SemanticRegistry(),
                    capability_registry=self.registry, capability_profile='commander_review')
                binding = bind_card_program_runtime(program, capability_registry=self.registry, profile='commander_review')
                self.assertEqual(row['name'] in expected, binding['strict_capability_ready'], binding)
                if row['name'] in expected:
                    session = self.session(70278008)
                    source = self.add(session.engine, row['name'], zone='hand', ref='conspire-original-table')
                    self.assert_closed(session, source)

    def test_omitted_tap_commit_keeps_admission_but_fails_actual_cost_consequence(self):
        from quorune.rules.casting import commit as owner
        calls = []
        def omitted(*args, **kwargs):
            calls.append((args, kwargs))
        result = unittest.TestResult()
        witness = type(self)('test_actual_sabotage_taps_newly_cast_creatures_and_retargets_one_copy')
        with mock.patch.object(owner, 'set_permanent_tapped', omitted):
            witness.run(result)
        self.assertTrue(calls)
        self.assertFalse(result.errors)
        self.assertEqual(1, len(result.failures))
        self.assertIn('self.assertTrue(all(session.state.cards[c.object_id].tapped', result.failures[0][1])
