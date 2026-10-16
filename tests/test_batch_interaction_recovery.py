from __future__ import annotations

"""Independent runtime composition and whole-card rejection witnesses for #519."""

import json
from pathlib import Path
import tempfile
import unittest

from common import ROOT
from quorune.carddb import CardDatabase
from quorune.card_programs import bind_card_program_runtime
from quorune.card_programs.adapters import compile_best_available_card_program
from quorune.compiler.unlock_frontier import analyze_card_unlocks
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.semantics import SemanticRegistry
from scripts.build_test_database import build_fixture_database
from high_risk_interaction_support import _observed_piece_ids
import test_paid_entry_copy as paid_actions


class BatchInteractionRecoveryTests(unittest.TestCase):
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
        path = Path(cls.temp.name) / 'batch-interactions.sqlite3'
        build_fixture_database([ROOT / 'tests/fixtures/bound-effect-program-cards.json',
            ROOT / 'tests/fixtures/static-characteristic-setting-cards.json',
            ROOT / 'tests/fixtures/attached-permanent-modifier-cards.json',
            ROOT / 'tests/fixtures/entry-color-composition-cards.json',
            ROOT / 'tests/fixtures/batch-interaction-recovery-cards.json'], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Batch interaction witness', [DeckEntry('Generic Bound Commander', 1, 'commander'),
            DeckEntry('Generic Bound Plains', 30)], ['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temp.cleanup()

    def priority(self, session, actor):
        for _ in range(128):
            session.engine.pump()
            decision = session.state.pending_decision
            self.assertEqual('priority', decision.kind)
            if session.pending_principals() == ['pilot:' + actor]:
                return
            self.assertTrue(session.act(session.pending_principals()[0], {'action_id': 'pass'}).ok)
        self.fail('Requested principal never received priority')

    def next_player_turn(self, session):
        for _ in range(160):
            if session.state.active_player == 'B':
                return
            decision = session.state.pending_decision
            if decision.kind == 'priority':
                command = {'action_id': 'pass'}
            elif decision.kind == 'combat.attackers':
                command = {'action_id': 'attack', 'attackers': {}}
            elif decision.kind == 'combat.blockers':
                command = {'action_id': 'block', 'blocks': {}}
            else:
                self.fail('Unexpected turn-advance decision: ' + decision.kind)
            result = session.act(session.pending_principals()[0], command)
            self.assertTrue(result.ok, result.summary)
        self.fail('A did not finish its actual turn')

    def test_actual_javelin_entry_equip_and_next_turn_recompute_attached_modifier(self):
        session = self.session(423001)
        source = self.add(session.engine, 'Javelin of Lightning', zone='hand', ref='assurance-javelin')
        first = self.add(session.engine, 'Generic Bound Body', ref='assurance-first-holder')
        second = self.add(session.engine, 'Generic Bound Body', ref='assurance-second-holder')
        enemy = self.add(session.engine, 'Generic Bound Body', seat='B', ref='assurance-enemy-holder')
        private = self.add(session.engine, 'Generic Bound Growth', seat='C', zone='hand', ref='assurance-javelin-private')
        self.assert_closed(session, source)
        self.prepare(session, mana={'C': 5, 'R': 1})
        self.checkpoint(session)
        self.cast(session, 'A', source)
        self.resolve(session)
        self.assertEqual('semantic.target', session.state.pending_decision.kind)
        before = authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:A', {'action_id': 'choose', 'targets': [enemy.ref]}).ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        session = self.replay(session, load=True)
        result = session.act('pilot:A', {'action_id': 'choose', 'targets': [first.ref]})
        self.assertTrue(result.ok, result.summary)
        self.resolve(session)
        self.assertEqual(first.object_id, session.state.cards[source.object_id].attached_to)
        self.assertEqual(4, session.engine._numeric_stat(first.object_id, 'power'))
        self.assertIn('first strike', session.engine._combat_keywords(session.state.cards[first.object_id]))
        row = next(row for row in session.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']
            if row.get('source') == source.ref and row['id'].startswith('activate:'))
        result = session.act('pilot:A', {'action_id': row['id'], 'targets': [second.ref], 'pay': 'auto'})
        self.assertTrue(result.ok, result.summary)
        self.resolve(session)
        self.assertEqual((2, 4), tuple(session.engine._numeric_stat(card.object_id, 'power') for card in (first, second)))
        self.assertNotIn('first strike', session.engine._combat_keywords(session.state.cards[first.object_id]))
        self.next_player_turn(session)
        self.assertEqual(second.object_id, session.state.cards[source.object_id].attached_to)
        self.assertEqual(2, session.engine._numeric_stat(second.object_id, 'power'))
        self.assertNotIn('first strike', session.engine._combat_keywords(session.state.cards[second.object_id]))
        for viewer in 'ABD':
            self.assertNotIn(private.ref, json.dumps(session.packet('pilot:' + viewer, full=True)))
        self.replay(session, load=True)

    def test_actual_silken_vehicle_modifiers_and_departed_aura_untap_reference(self):
        for leave in (False, True):
            with self.subTest(leave=leave):
                session = self.session(423010 + leave)
                aura = self.add(session.engine, 'Silken Strength', zone='hand', ref='assurance-silken')
                vehicle = self.add(session.engine, 'Generic Modifier Vehicle', ref='assurance-silken-vehicle')
                vehicle.tapped = True
                land = self.add(session.engine, 'Generic Bound Plains', seat='B', ref='assurance-illegal-aura-land')
                bounce = self.add(session.engine, 'Generic Setting Bounce', zone='hand', ref='assurance-silken-bounce')
                animation = self.add(session.engine, 'Generic Setting Animation', zone='hand', ref='assurance-silken-animation')
                self.assert_closed(session, aura)
                self.prepare(session, mana={'C': 1, 'G': 1})
                self.checkpoint(session)
                row = next(row for row in session.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']
                    if row.get('card') == aura.ref and row['id'].startswith('cast:'))
                self.assertIn(vehicle.ref, row['target_schema']['legal_refs'])
                self.assertNotIn(land.ref, row['target_schema']['legal_refs'])
                before = authoritative_state_hash(session.state)
                self.assertFalse(session.act('pilot:A', {'action_id': row['id'], 'targets': [land.ref], 'pay': 'auto'}).ok)
                self.assertEqual(before, authoritative_state_hash(session.state))
                self.cast(session, 'A', aura, [vehicle.ref])
                for _ in range(64):
                    if session.state.cards[aura.object_id].zone == 'battlefield' and session.state.stack:
                        break
                    self.assertEqual('priority', session.state.pending_decision.kind)
                    self.assertTrue(session.act(session.pending_principals()[0], {'action_id': 'pass'}).ok)
                else:
                    self.fail('Silken entry trigger did not wait on the stack')
                self.assertTrue(session.state.cards[vehicle.object_id].tapped)
                session = self.replay(session, load=True)
                if leave:
                    self.cast(session, 'A', bounce, [aura.ref])
                self.resolve(session)
                self.assertFalse(session.state.cards[vehicle.object_id].tapped)
                self.assertNotIn('creature', session.engine._type_parts(session.engine._effective_card_data(
                    session.state.cards[vehicle.object_id])['type_line'])[0])
                self.cast(session, 'A', animation, [vehicle.ref])
                self.resolve(session)
                self.assertEqual((5, 5) if leave else (6, 7), tuple(session.engine._numeric_stat(vehicle.object_id, name)
                    for name in ('power', 'toughness')))
                self.assertEqual(not leave, 'reach' in session.engine._combat_keywords(session.state.cards[vehicle.object_id]))
                self.replay(session, load=True)

    def test_actual_hexproof_lair_chooses_entry_color_and_allows_own_target_only(self):
        session = self.session(423020)
        land = self.add(session.engine, "Valgavoth's Lair", zone='hand', ref='assurance-lair')
        own = self.add(session.engine, 'Generic Assurance Untap Land', zone='hand', ref='assurance-own-untap')
        enemy = self.add(session.engine, 'Generic Assurance Untap Land', seat='B', zone='hand', ref='assurance-enemy-untap')
        self.add(session.engine, 'Generic Bound Plains', seat='B', ref='assurance-other-land')
        self.assert_closed(session, land)
        self.prepare(session, mana={})
        self.checkpoint(session)
        result = session.act('pilot:A', {'action_id': 'play-land:' + land.ref})
        self.assertTrue(result.ok, result.summary)
        session = self.replay(session, load=True)
        for _ in range(4):
            if session.state.pending_decision.kind != 'replacement.order':
                break
            options = session.packet('pilot:A', full=True)['decision']['ctx']['options']
            selected = next((row['id'] for row in options if row['id'].endswith(':G')), options[0]['id'])
            command = {'action_id': 'choose', 'choices': {'replacement': selected}}
            before = authoritative_state_hash(session.state)
            self.assertFalse(session.act('pilot:B', command).ok)
            self.assertEqual(before, authoritative_state_hash(session.state))
            self.assertTrue(session.act('pilot:A', command).ok)
        current = session.state.cards[land.object_id]
        self.assertEqual(('battlefield', True, 'G'), (current.zone, current.tapped, current.annotations['chosen_color']))
        self.priority(session, 'B')
        row = next(row for row in session.packet('pilot:B', full=True)['decision']['ctx']['legal']['actions']
            if row.get('card') == enemy.ref and row['id'].startswith('cast:'))
        self.assertNotIn(land.ref, row['target_schema']['legal_refs'])
        before = authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:B', {'action_id': row['id'], 'targets': [land.ref], 'pay': 'auto'}).ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        self.cast(session, 'A', own, [land.ref])
        self.resolve(session)
        self.assertFalse(session.state.cards[land.object_id].tapped)
        action = next(row for row in session.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']
            if row.get('source') == land.ref and row['id'].startswith('activate:'))
        result = session.act('pilot:A', {'action_id': action['id'], 'pay': 'auto'})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(1, session.state.players['A'].mana_pool['G'])
        self.replay(session, load=True)

    def test_original_promoted_leaves_keep_unrepresented_siblings_closed_at_admission(self):
        data = json.loads((ROOT / 'tests/fixtures/batch-interaction-recovery-cards.json').read_text(encoding='utf-8'))
        for witness in data['admission_boundaries']:
            with self.subTest(card=witness['name']):
                card = self.db.by_oracle_id(witness['oracle_id'])
                ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile='commander_review')
                program = compile_best_available_card_program(self.db, card, semantic_registry=SemanticRegistry(),
                    capability_registry=self.registry, capability_profile='commander_review')
                unlock = analyze_card_unlocks(ir, program=program, program_error=None,
                    capabilities=self.registry, profile='commander_review')
                self.assertLessEqual(set(witness['piece_ids']), _observed_piece_ids(unlock))
                self.assertTrue(ir.material_residuals)
                binding = bind_card_program_runtime(program, capability_registry=self.registry, profile='commander_review')
                self.assertFalse(binding['strict_capability_ready'])
                self.assertFalse(binding['compatible_ready'])
                self.assertTrue(binding['blockers'])
