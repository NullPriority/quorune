"""CR 509.3a/c: unqualified source block events, independent of blocker count."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from common import ROOT
import test_bound_effect_programs as controls
from quorune.carddb import CardDatabase
from quorune.card_programs import bind_card_program_runtime
from quorune.card_programs.adapters import compile_best_available_card_program
from quorune.deck import DeckDefinition, DeckEntry
from quorune.model import CombatState
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.semantics import SemanticRegistry
from scripts.build_test_database import build_fixture_database


class SourceBlockPairRuntimeTests(unittest.TestCase):
    register_fixture_cards_on_add = True
    session = controls.BoundEffectProgramRuntimeTests.session
    add = controls.BoundEffectProgramRuntimeTests.add
    checkpoint = controls.BoundEffectProgramRuntimeTests.checkpoint
    resolve = controls.BoundEffectProgramRuntimeTests.resolve
    replay = controls.BoundEffectProgramRuntimeTests.replay

    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / 'block-pair.sqlite3'
        build_fixture_database([
            ROOT / 'tests/fixtures/bound-effect-program-cards.json',
            ROOT / 'tests/fixtures/source-block-pair-cards.json',
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Block pair', [
            DeckEntry('Generic Bound Commander', 1, 'commander'),
            DeckEntry('Generic Bound Plains', 30),
        ], ['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    def test_original_block_pair_cards_have_complete_normal_admission(self):
        fixture = json.loads((ROOT / 'tests/fixtures/source-block-pair-cards.json').read_text(encoding='utf-8'))
        semantics = SemanticRegistry()
        for row in fixture['cards']:
            card = self.db.lookup(row['name'])
            program = compile_best_available_card_program(self.db, card,
                semantic_registry=semantics, capability_registry=self.registry,
                capability_profile='commander_review')
            with self.subTest(card=card.name):
                self.assertTrue(bind_card_program_runtime(program,
                    capability_registry=self.registry, profile='commander_review')['strict_capability_ready'])

    def fixture(self, seed):
        session = self.session(seed)
        engine = session.engine
        attacker = self.add(engine, 'Chub Toad', ref='pair-attacker')
        blocker = self.add(engine, 'Brushwagg', seat='B', ref='pair-blocker')
        extra = self.add(engine, 'Generic Bound Body', seat='B', ref='second-blocker')
        other = self.add(engine, 'Generic Bound Body', ref='unblocked-attacker')
        observer = self.add(engine, 'Slinking Giant', ref='nonparticipating-source')
        self.add(engine, 'Generic Bound Body', seat='C', ref='unused-C-blocker')
        private = {seat: self.add(engine, 'Generic Bound Body', seat=seat,
            zone='hand', ref='pair-private-' + seat) for seat in 'ABCD'}
        engine.state.phase_index = 6
        engine.state.phase = 'combat'
        engine.state.step = 'declare_blockers'
        attacker.attacking = 'B'
        other.attacking = 'C'
        engine.state.combat = CombatState(attackers_declared=True,
            had_attacking_creature=True,
            attackers={attacker.object_id: 'B', other.object_id: 'C'},
            defending_players=['B', 'C'])
        engine._begin_blocker_decisions()
        self.checkpoint(session)
        return session, attacker, blocker, extra, other, observer, private

    def test_real_multiplayer_blocks_bind_both_sources_once_and_resume_privately(self):
        session, attacker, blocker, extra, other, observer, private = self.fixture(509_003_001)
        packet = session.packet('pilot:B', full=True)
        self.assertEqual('combat.blockers', packet['decision']['kind'])
        command = {'a': 'block', 'blk': {blocker.ref: attacker.ref, extra.ref: attacker.ref}}
        before = authoritative_state_hash(session.state)
        rejected = session.act('pilot:C', command)
        self.assertFalse(rejected.ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        accepted = session.act('pilot:B', command)
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertEqual('combat.blockers', session.state.pending_decision.kind)
        self.assertEqual(['C'], session.state.pending_decision.actors)
        self.assertFalse(session.state.stack)
        session = self.replay(session, load=True)
        for viewer in 'ABCD':
            projected = json.dumps(session.packet('pilot:' + viewer, full=True))
            self.assertIn(private[viewer].ref, projected)
            for owner in 'ABCD':
                if owner != viewer:
                    self.assertNotIn(private[owner].ref, projected)
        accepted = session.act('pilot:C', {'a': 'block', 'blk': {}})
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertEqual(2, len(session.state.stack))
        self.assertIsNone(self.resolve(session))
        engine = session.engine
        self.assertEqual((3, 3), tuple(engine._numeric_stat(attacker.object_id, stat) for stat in ('power', 'toughness')))
        self.assertEqual((1, 4), tuple(engine._numeric_stat(blocker.object_id, stat) for stat in ('power', 'toughness')))
        self.assertEqual((4, 4), tuple(engine._numeric_stat(observer.object_id, stat) for stat in ('power', 'toughness')))
        self.assertFalse(session.state.combat.blockers.get(other.object_id))
        self.replay(session, load=True)

    def test_pair_binding_omission_is_killed_by_native_combat_consequences(self):
        from dataclasses import replace
        import quorune.compiler.fixed_public_multi_event_bindings as owner
        original = owner._event_pair
        def omit(pair):
            subscriptions, mechanic, capabilities = original(pair)
            if pair == 'blocks or becomes blocked':
                subscriptions = (subscriptions[0], replace(subscriptions[1], event='creature.attacks'))
            return subscriptions, mechanic, capabilities
        method = 'test_real_multiplayer_blocks_bind_both_sources_once_and_resume_privately'
        with patch.object(owner, '_event_pair', side_effect=omit), self.assertRaises(AssertionError):
            getattr(type(self)(method), method)()


class SourceBlockPairGrammarTests(unittest.TestCase):
    def test_multi_event_and_source_result_dependencies_fail_closed(self):
        from copy import deepcopy
        from quorune.oracle_ir import compile_oracle_card
        from quorune.rules.capabilities import CapabilityRegistry
        from test_qualified_zone_event_queries import query_record
        record = query_record('Whenever this creature blocks or becomes blocked, it gets +2/+2 until end of turn.')
        raw = json.loads((ROOT / 'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        registry = load_default_capability_registry()
        self.assertEqual('exact', compile_oracle_card(record, capability_registry=registry).status)
        changed = deepcopy(raw)
        next(row for row in changed['capabilities'] if row['id'] == 'trigger.subscription.fixed_multi_event').update(status='blocked', blockers=['omitted multi-event dependency'])
        self.assertNotEqual('exact', compile_oracle_card(record, capability_registry=CapabilityRegistry(changed)).status)
        with patch('quorune.compiler.fixed_source_combat_growth._ADMITTED_BINDINGS', frozenset()):
            self.assertNotEqual('exact', compile_oracle_card(record, capability_registry=registry).status)

    def test_pair_binds_each_source_event_and_keeps_qualified_occurrences_residual(self):
        from quorune.compiler.fixed_public_multi_event_bindings import fixed_public_multi_event_binding_spec
        body = 'this creature gets +2/+2 until end of turn.'
        binding = fixed_public_multi_event_binding_spec('Whenever this creature blocks or becomes blocked, ' + body, card_name='Generic Source')
        self.assertIsNotNone(binding)
        self.assertEqual('source_blocks_or_becomes_blocked', binding.variant)
        for prefix in ('Whenever this creature blocks or becomes blocked by a creature, ',
                       'Whenever this creature blocks or becomes blocked by one or more Orcs, ',
                       'Whenever another creature blocks or becomes blocked, '):
            self.assertIsNone(fixed_public_multi_event_binding_spec(prefix + body, card_name='Generic Source'))
