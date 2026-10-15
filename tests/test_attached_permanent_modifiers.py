from __future__ import annotations

"""CR 208.3a/613.4b-c: noncreature modifiers survive until creature status."""

import json
from pathlib import Path
import tempfile
import unittest

from common import ROOT
from quorune.carddb import CardDatabase
from quorune.card_programs import bind_card_program_runtime
from quorune.card_programs.adapters import compile_best_available_card_program
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_granted_skulk import record
import test_bound_effect_programs as actions


class AttachedPermanentModifierCompilerTests(unittest.TestCase):
    def test_pure_fixed_modifiers_declare_shape_and_consumer_without_type_setting(self):
        registry = load_default_capability_registry()
        for text in ('Enchanted permanent gets +1/+1 and has vigilance.',
                     'Enchanted artifact gets +2/+0 and has skulk.', 'Enchanted land gets +0/+1.'):
            with self.subTest(text=text):
                ir = compile_oracle_card(record(text, 'Enchantment — Aura'), capability_registry=registry,
                    capability_profile='commander_review')
                self.assertEqual('exact', ir.status, ir.material_residuals)
                node = ir.faces[0].nodes[0]
                self.assertEqual(text, node.text)
                descriptor = node.handlers[0]
                self.assertEqual('attached', descriptor['target']['kind'])
                self.assertEqual([], descriptor['modifier']['type_operations'])
                self.assertIsNone(descriptor['modifier']['base_power'])
                self.assertIn('continuous.characteristics.fixed_public_setting', node.capability_dependencies)
                if 'skulk' in text:
                    self.assertIn('combat.block.skulk', node.capability_dependencies)

    def test_empty_or_unknown_modifier_clauses_remain_residual(self):
        registry = load_default_capability_registry()
        for text in ('Enchanted permanent gets +0/+0.',
                     'Enchanted permanent gets +1/+1 and has an unrepresented ability.',
                     'Enchanted permanent gets +1/+1 and draws a card.'):
            ir = compile_oracle_card(record(text, 'Enchantment — Aura'), capability_registry=registry,
                capability_profile='commander_review')
            self.assertNotEqual('exact', ir.status)
            self.assertTrue(ir.material_residuals)


class AttachedPermanentModifierRuntimeTests(unittest.TestCase):
    register_fixture_cards_on_add = True
    session = actions.BoundEffectProgramRuntimeTests.session
    add = actions.BoundEffectProgramRuntimeTests.add
    checkpoint = actions.BoundEffectProgramRuntimeTests.checkpoint
    resolve = actions.BoundEffectProgramRuntimeTests.resolve
    replay = actions.BoundEffectProgramRuntimeTests.replay

    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name).resolve() / 'attached-permanent-modifiers.sqlite3'
        build_fixture_database([ROOT / 'tests/fixtures/bound-effect-program-cards.json',
            ROOT / 'tests/fixtures/static-characteristic-setting-cards.json',
            ROOT / 'tests/fixtures/attached-permanent-modifier-cards.json'], path)
        cls.db = CardDatabase(path); cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Attached permanent witness', [DeckEntry('Generic Bound Commander', 1, 'commander'),
            DeckEntry('Generic Bound Plains', 30)], ['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):
        cls.db.close(); cls.temporary.cleanup()

    def cast(self, session, card, targets):
        session.engine.pump()
        current = session.state.cards[card.object_id]
        offered = next(row for row in session.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']
            if row['id'] == 'cast:' + current.ref)
        accepted = session.act('pilot:A', {'action_id': offered['id'], 'targets': list(targets), 'pay': 'auto'})
        self.assertTrue(accepted.ok, accepted.summary)

    def counter_targets(self, session, counter):
        action = next(row for row in session.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']
            if row['id'] == 'cast:' + counter.ref)
        return action['target_schema']['legal_refs']

    def test_actual_lightwheel_vehicle_animation_layers_source_departure_and_replay(self):
        session = self.session(397001); engine = session.engine
        aura = self.add(engine, 'Lightwheel Enhancements', zone='hand', ref='original-permanent-modifier')
        vehicle = self.add(engine, 'Generic Modifier Vehicle', seat='B', ref='foreign-modifier-vehicle')
        self.add(engine, 'Generic Bound Body', ref='other-legal-creature')
        animation = self.add(engine, 'Generic Setting Animation', zone='hand', ref='modifier-animation')
        bounce = self.add(engine, 'Generic Setting Bounce', zone='hand', ref='modifier-source-return')
        counter = self.add(engine, 'Generic Setting Counter Spell', zone='hand', ref='modifier-creature-check')
        private = self.add(engine, 'Generic Bound Growth', seat='C', zone='hand', ref='modifier-private')
        program = compile_best_available_card_program(self.db, self.db.lookup(aura.printed_name),
            semantic_registry=engine.semantics, capability_registry=self.registry, capability_profile='commander_review')
        binding = bind_card_program_runtime(program, capability_registry=self.registry, profile='commander_review')
        self.assertTrue(binding['strict_capability_ready'], binding)
        self.assertEqual('capability_closed', binding['trust_basis'])
        engine.permissions.invalidate_current(); engine.state.pending_decision = None
        engine.state.started = True; engine.state.active_player = 'A'; engine.state.phase = 'precombat_main'
        engine.state.step = 'main'; engine.state.phase_index = 3
        engine.state.players['A'].mana_pool.update({'W': 1, 'G': 1})
        engine._grant_priority('A'); engine.pump(); self.checkpoint(session)
        offered = next(row for row in session.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']
            if row['id'] == 'cast:' + aura.ref)
        before = authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:D', {'action_id': offered['id'], 'targets': [vehicle.ref], 'pay': 'auto'}).ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        self.cast(session, aura, [vehicle.ref])
        session = self.replay(session, load=True); self.resolve(session)
        self.assertNotIn('creature', session.engine._type_parts(session.engine._effective_card_data(
            session.state.cards[vehicle.object_id])['type_line'])[0])
        self.assertNotIn(vehicle.ref, self.counter_targets(session, counter))
        self.cast(session, animation, [vehicle.ref]); self.resolve(session)
        self.assertEqual((6, 6), tuple(session.engine._numeric_stat(vehicle.object_id, field) for field in ('power', 'toughness')))
        self.assertIn('vigilance', session.engine._combat_keywords(session.state.cards[vehicle.object_id]))
        self.assertIn(vehicle.ref, self.counter_targets(session, counter))
        self.cast(session, bounce, [aura.ref])
        session = self.replay(session, load=True); self.resolve(session)
        self.assertEqual('hand', session.state.cards[aura.object_id].zone)
        self.assertEqual((5, 5), tuple(session.engine._numeric_stat(vehicle.object_id, field) for field in ('power', 'toughness')))
        self.assertNotIn('vigilance', session.engine._combat_keywords(session.state.cards[vehicle.object_id]))
        for viewer in 'ABD':
            self.assertNotIn(private.ref, json.dumps(session.packet('pilot:' + viewer, full=True)))
        self.replay(session, load=True)
