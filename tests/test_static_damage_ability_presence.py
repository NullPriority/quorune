from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import json
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT
from quorune.ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
from quorune.card_programs import bind_card_program_runtime
from quorune.card_programs.adapters import compile_best_available_card_program
from quorune.carddb import CardDatabase
from quorune.compiler import runtime_templates
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.semantic_runtime.damage_replacements import collect_damage_replacement_effects
from quorune.session import CommanderSession
from scripts.build_test_database import build_fixture_database
import test_bound_effect_programs as bound_controls
import test_self_public_zone_casting as zone_controls


CAPABILITY = 'damage.prevention.static_fixed'
TEXT = 'If a source an opponent controls would deal damage to you, prevent 1 of that damage.'


class StaticDamageAbilityPresenceTests(unittest.TestCase):
    register_fixture_cards_on_add = True
    session = bound_controls.BoundEffectProgramRuntimeTests.session
    add = bound_controls.BoundEffectProgramRuntimeTests.add
    checkpoint = bound_controls.BoundEffectProgramRuntimeTests.checkpoint
    resolve = bound_controls.BoundEffectProgramRuntimeTests.resolve
    replay = bound_controls.BoundEffectProgramRuntimeTests.replay
    prepare = zone_controls.SelfPublicZoneRuntimeTests.prepare

    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        path = Path(cls.temp.name)/'fixed-prevention-presence.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',
            ROOT/'tests/fixtures/static-damage-ability-presence-cards.json'], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Current fixed prevention',
            [DeckEntry('Generic Bound Commander', 1, 'commander'), DeckEntry('Generic Bound Plains', 30)],
            ['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temp.cleanup()

    def actions(self, s, seat='A'):
        return s.packet('pilot:'+seat, full=True)['decision']['ctx']['legal']['actions']

    def priority(self, s, seat):
        for _ in range(8):
            if s.state.priority_player == seat:
                return
            result = s.act(s.pending_principals()[0], {'action_id': 'pass'})
            self.assertTrue(result.ok, result.summary)
        self.fail('Expected ordinary priority for '+seat)

    def reject(self, s, command, *, seat='D'):
        before = authoritative_state_hash(s.state)
        result = s.act('pilot:'+seat, command)
        self.assertFalse(result.ok, result.summary)
        self.assertEqual(before, authoritative_state_hash(s.state))

    def cast(self, s, card, targets=(), *, seat='A', payment):
        self.priority(s, seat)
        command = {'action_id': 'cast:'+card.ref, 'targets': list(targets),
            'pay': 'manual', 'payment': payment}
        self.assertIn(command['action_id'], [row['id'] for row in self.actions(s, seat)])
        self.reject(s, command)
        result = s.act('pilot:'+seat, command)
        self.assertTrue(result.ok, result.summary)

    def binding(self, s, card):
        program = compile_best_available_card_program(self.db, self.db.lookup(card.printed_name),
            semantic_registry=s.engine.semantics, capability_registry=self.registry,
            capability_profile='commander_review')
        return bind_card_program_runtime(program, capability_registry=self.registry, profile='commander_review')

    def test_new_static_damage_versions_require_current_ability_presence(self):
        for text, handler in ((TEXT, 'prevention.damage.fixed.v1'),
                ('If a red source would deal damage to you, prevent 1 of that damage.', 'prevention.damage.fixed.v2'),
                (self.db.lookup('Furnace of Rath').oracle_text, 'replacement.damage.quantity.v1'),
                (self.db.lookup('Torbran, Thane of Red Fell').oracle_text, 'replacement.damage.quantity.v2'),
                (self.db.lookup('Palisade Giant').oracle_text, 'replacement.damage.redirect-to-source.v1'),
                ('Prevent all damage that would be dealt to you.', 'prevention.damage.all.v1'),
                ('If damage would be dealt to this creature, prevent that damage. Remove a +1/+1 counter from this creature.', 'prevention.damage.fixed-self-counter.v1')):
            template = runtime_templates.static_runtime_template(text, source_name='Generic Damage Source', source_damageable=True)
            self.assertIsNotNone(template)
            self.assertEqual(handler, template.compiled[1]['handler_id'])
            self.assertIn(CURRENT_ABILITY_FRAGMENT_COVERAGE, template.runtime_coverage)

    def test_originals_have_complete_normal_registration_and_current_component_identity(self):
        s = self.session(849100)
        for capability, name in ((CAPABILITY, 'Guardian Seraph'),
                ('damage.replacement.static_quantity', 'Torbran, Thane of Red Fell'),
                ('damage.redirection.static_to_source', 'Palisade Giant')):
            source = self.add(s.engine, name)
            binding = self.binding(s, source)
            self.assertTrue(binding['strict_capability_ready'], binding['blockers'])
            programs = s.engine.semantics.runtime_handler_programs_for_oracle(
                source.oracle_id, active_zone='battlefield', event='damage')
            relevant = [p for p in programs if capability in p.capability_dependencies]
            self.assertTrue(relevant)
            self.assertTrue(all(p.trust_level == 'trusted' and not p.requires_arbiter for p in relevant))
            fragments = s.engine._effective_card_data(source)['ability_fragments']
            keys = [f['value']['semantic_key'] for f in fragments if f['kind'] == 'static_component']
            for program in relevant:
                self.assertIn(CURRENT_ABILITY_FRAGMENT_COVERAGE, program.coverage)
                self.assertIn(program.key, keys)

    def test_collector_respects_current_marker_and_retains_unmarked_historical_behavior(self):
        s = self.session(849102)
        source = self.add(s.engine, 'Guardian Seraph')
        original, = (p for p in s.engine.semantics.programs_for_oracle(source.oracle_id)
            if CAPABILITY in p.capability_dependencies)
        legacy = replace(original, coverage=tuple(c for c in original.coverage
            if c != CURRENT_ABILITY_FRAGMENT_COVERAGE))
        selected = [legacy]
        data = {'ability_fragments': []}
        host = SimpleNamespace(state=SimpleNamespace(cards={source.object_id: source}, active_player='A'),
            active_seats=list('ABCD'), semantics=SimpleNamespace(runtime_handler_programs_for_oracle=lambda *a, **k: tuple(selected)),
            _semantic_event_sources=lambda **k: [source], semantic_program_is_current_trusted=lambda p: True,
            _effective_card_data=lambda card: data)
        self.assertEqual(1, len(collect_damage_replacement_effects(host)))
        current = original
        selected[:] = [current]
        self.assertEqual((), collect_damage_replacement_effects(host))
        data['ability_fragments'] = [{'kind': 'static_component',
            'value': {'schema_version': 1, 'semantic_key': current.key}}]
        self.assertEqual(1, len(collect_damage_replacement_effects(host)))
        data['ability_fragments'][0]['value']['semantic_key'] = 'fixture:other-component'
        self.assertEqual((), collect_damage_replacement_effects(host))

    def test_unknown_siblings_and_open_prevention_variants_remain_residual(self):
        record = replace(self.db.lookup('Guardian Seraph'), oracle_id='fixture:unknown-fixed-prevention',
            name='Generic Unclosed Guardian', oracle_text=TEXT+'\nRewrite the game history.')
        ir = compile_oracle_card(record, capability_registry=self.registry, capability_profile='commander_review')
        self.assertNotEqual('exact', ir.status)
        self.assertTrue(any('Rewrite the game history' in residual.text for residual in ir.material_residuals))
        for text in (TEXT.replace('prevent 1', 'prevent X'), TEXT.replace('prevent 1', 'you may prevent 1'),
                TEXT+' Draw a card.', TEXT.replace('prevent 1', 'prevent 0')):
            self.assertIsNone(runtime_templates.static_runtime_template(text))

    def test_verified_static_damage_owners_withdrawal_stays_fail_closed(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from scripts.update_capability_evidence import build_index, discover_tests
        raw = json.loads((ROOT/'quorune/rules/capability-registry.json').read_text())
        for capability, name in ((CAPABILITY, 'Guardian Seraph'),
                ('damage.replacement.static_quantity', 'Torbran, Thane of Red Fell'),
                ('damage.redirection.static_to_source', 'Palisade Giant')):
            changed = deepcopy(raw)
            next(c for c in changed['capabilities'] if c['id'] == capability).update(
                status='blocked', blockers=['withdrawn static damage owner'], dependency_fail_closed_status='not_run')
            registry = CapabilityRegistry(changed)
            index = build_index(registry_value=changed,
                declaration_source=json.loads((ROOT/'platform/capability-evidence-declarations.json').read_text()),
                discovered_test_ids=set(discover_tests(ROOT).values()))
            registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
            ir = compile_oracle_card(self.db.lookup(name), capability_registry=registry, capability_profile='commander_review')
            blockers = [b for residual in ir.material_residuals for b in residual.blockers]
            self.assertTrue(any('status:'+capability+':blocked' in b for b in blockers), blockers)
            self.assertFalse(any('evidence_index:unverified' in b for b in blockers), blockers)

    def test_fresh_current_presence_omission_is_detected(self):
        original = runtime_templates._static_damage_runtime_template
        method = 'test_new_static_damage_versions_require_current_ability_presence'
        for handler in ('prevention.damage.fixed.v1', 'prevention.damage.fixed.v2',
                'replacement.damage.quantity.v1', 'replacement.damage.quantity.v2',
                'replacement.damage.redirect-to-source.v1', 'prevention.damage.all.v1',
                'prevention.damage.fixed-self-counter.v1'):
            def missing_marker(*args, only=handler, **kwargs):
                template = original(*args, **kwargs)
                if template is not None and template.compiled[1]['handler_id'] == only:
                    return replace(template, runtime_coverage=())
                return template
            with patch.object(runtime_templates, '_static_damage_runtime_template', side_effect=missing_marker):
                with self.assertRaises(AssertionError):
                    getattr(type(self)(method), method)()

    def test_real_paid_torbran_stops_extra_damage_after_ability_loss_and_replays(self):
        s = self.session(849103)
        source = self.add(s.engine, 'Torbran, Thane of Red Fell', seat='B', zone='hand')
        humble = self.add(s.engine, 'Humble', zone='hand')
        shock = self.add(s.engine, 'Shock', seat='B', zone='hand')
        bolt = self.add(s.engine, 'Lightning Bolt', seat='B', zone='hand')
        private = self.add(s.engine, 'Generic Bound Growth', seat='C', zone='hand')
        self.assertTrue(self.binding(s, source)['strict_capability_ready'])
        s.state.players['A'].mana_pool.update({'C': 1, 'W': 1})
        self.prepare(s, actor='B', active='B', mana={'C': 1, 'R': 5})
        self.checkpoint(s)
        self.cast(s, source, seat='B', payment={'C': 1, 'R': 3})
        self.assertIsNone(self.resolve(s))
        self.cast(s, shock, ['A'], seat='B', payment={'R': 1})
        self.assertIsNone(self.resolve(s))
        self.assertEqual(36, s.state.players['A'].life)
        self.cast(s, humble, [source.ref], payment={'C': 1, 'W': 1})
        self.assertIsNone(self.resolve(s))
        self.assertEqual((0, 1), tuple(s.engine._numeric_stat(source.object_id, f) for f in ('power', 'toughness')))
        self.cast(s, bolt, ['A'], seat='B', payment={'R': 1})
        self.assertIsNone(self.resolve(s))
        self.assertEqual(33, s.state.players['A'].life)
        self.assert_private_and_replay(s, private)

    def test_real_paid_palisade_does_not_redirect_after_ability_loss_and_replays(self):
        s = self.session(849104)
        source = self.add(s.engine, 'Palisade Giant', zone='hand')
        humble = self.add(s.engine, 'Humble', zone='hand')
        shock = self.add(s.engine, 'Shock', seat='B', zone='hand')
        private = self.add(s.engine, 'Generic Bound Growth', seat='C', zone='hand')
        self.assertTrue(self.binding(s, source)['strict_capability_ready'])
        s.state.players['B'].mana_pool['R'] = 1
        self.prepare(s, mana={'C': 5, 'W': 3})
        self.checkpoint(s)
        self.cast(s, source, payment={'C': 4, 'W': 2})
        self.assertIsNone(self.resolve(s))
        self.cast(s, humble, [source.ref], payment={'C': 1, 'W': 1})
        self.assertIsNone(self.resolve(s))
        self.assertEqual((0, 1), tuple(s.engine._numeric_stat(source.object_id, f) for f in ('power', 'toughness')))
        self.cast(s, shock, ['A'], seat='B', payment={'R': 1})
        self.assertIsNone(self.resolve(s))
        self.assertEqual(38, s.state.players['A'].life)
        self.assertEqual('battlefield', s.state.cards[source.object_id].zone)
        self.assertEqual(0, s.state.cards[source.object_id].marked_damage)
        self.assert_private_and_replay(s, private)

    def assert_private_and_replay(self, s, private):
        for seat in 'ABD':
            self.assertNotIn(private.ref, json.dumps(s.packet('pilot:'+seat, full=True)))
        self.assertIn(private.ref, json.dumps(s.packet('pilot:C', full=True)))
        self.replay(s, load=True)

    def test_real_paid_prevention_stops_after_humble_and_resumes_after_cleanup_with_replay(self):
        s = self.session(849101)
        source = self.add(s.engine, 'Guardian Seraph', zone='hand')
        humble = self.add(s.engine, 'Humble', zone='hand')
        shock = self.add(s.engine, 'Shock', seat='B', zone='hand', ref='fixed-first-shock')
        bolt = self.add(s.engine, 'Lightning Bolt', seat='B', zone='hand')
        later = self.add(s.engine, 'Shock', seat='C', zone='hand', ref='fixed-restored-shock')
        mountain = self.add(s.engine, 'Mountain', seat='C')
        private = self.add(s.engine, 'Generic Bound Growth', seat='C', zone='hand')
        self.assertTrue(self.binding(s, source)['strict_capability_ready'])
        s.state.players['B'].mana_pool['R'] = 2
        self.prepare(s, mana={'C': 3, 'W': 3})
        self.checkpoint(s)
        self.cast(s, source, payment={'C': 2, 'W': 2})
        self.assertIsNone(self.resolve(s))
        self.cast(s, shock, ['A'], seat='B', payment={'R': 1})
        self.assertIsNone(self.resolve(s))
        self.assertEqual(39, s.state.players['A'].life)
        self.cast(s, humble, [source.ref], payment={'C': 1, 'W': 1})
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'pending-fixed-prevention'
            s.save(path)
            loaded = CommanderSession.load(self.db, path)
            self.assertEqual(authoritative_state_hash(s.state), authoritative_state_hash(loaded.state))
            s = loaded
        self.assertIsNone(self.resolve(s))
        self.assertEqual((0, 1), tuple(s.engine._numeric_stat(source.object_id, f) for f in ('power', 'toughness')))
        self.cast(s, bolt, ['A'], seat='B', payment={'R': 1})
        self.assertIsNone(self.resolve(s))
        self.assertEqual(36, s.state.players['A'].life)
        for _ in range(160):
            if 'flying' in s.engine._combat_keywords(s.state.cards[source.object_id]):
                break
            principal = s.pending_principals()[0]
            kind = s.state.pending_decision.kind
            if kind == 'priority':
                command = {'action_id': 'pass'}
            elif kind == 'combat.attackers':
                command = {'action_id': 'attack', 'atk': {}}
            elif kind == 'combat.blockers':
                command = {'action_id': 'block', 'blocks': {}}
            else:
                self.fail('Unexpected current-prevention restoration decision '+kind)
            result = s.act(principal, command)
            self.assertTrue(result.ok, result.summary)
        self.assertIn('flying', s.engine._combat_keywords(s.state.cards[source.object_id]))
        self.assertEqual((3, 4), tuple(s.engine._numeric_stat(source.object_id, f) for f in ('power', 'toughness')))
        self.priority(s, 'C')
        row = next(r for r in self.actions(s, 'C') if r['id'].startswith('activate:'+mountain.ref+':'))
        command = {'action_id': row['id'], 'pay': 'auto'}
        self.reject(s, command)
        result = s.act('pilot:C', command)
        self.assertTrue(result.ok, result.summary)
        self.cast(s, later, ['A'], seat='C', payment={'R': 1})
        self.assertIsNone(self.resolve(s))
        self.assertEqual(35, s.state.players['A'].life)
        self.assert_private_and_replay(s, private)
