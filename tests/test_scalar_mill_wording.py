from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

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
import test_soulbond as pairing


class ScalarMillWordingCompilerTests(unittest.TestCase):
    def test_public_count_mill_retains_fixed_subjects_and_quantity_dependencies(self):
        from quorune.rules.capabilities import capability_dependencies_for_node

        registry = load_default_capability_registry()
        for subject, quantity in (
            ('Mill', 'the number of lands you control'),
            ('Target player mills', 'the number of creature cards in your graveyard'),
            ('Each opponent mills', 'the number of artifacts you control'),
        ):
            with self.subTest(subject=subject, quantity=quantity):
                direct = f'{subject} cards equal to {quantity}.'
                explicit = f'{subject} X cards, where X is {quantity}.'
                nodes = []
                for text in (direct, explicit):
                    ir = compile_oracle_card(record(text, 'Sorcery'), capability_registry=registry,
                        capability_profile='commander_review')
                    self.assertEqual('exact', ir.status, ir.material_residuals)
                    node, = ir.faces[0].nodes
                    self.assertEqual(text, node.text)
                    dependencies = capability_dependencies_for_node(effects=node.effects,
                        target_schema=node.target_schema, mechanic_ids=node.mechanics)
                    self.assertIn('quantity_expression.declared_effect_amount', dependencies)
                    self.assertIn('quantity_expression.public_query_effect_amount', dependencies)
                    for omitted in ('declared-effect-amount', 'public-query-effect-amount'):
                        self.assertEqual((), capability_dependencies_for_node(effects=node.effects,
                            target_schema=node.target_schema,
                            mechanic_ids=[m for m in node.mechanics if m != omitted]))
                    nodes.append(node)
                self.assertEqual(nodes[0].target_schema, nodes[1].target_schema)
                self.assertEqual(nodes[0].effects, nodes[1].effects)

    def test_unknown_quantity_antecedents_and_material_tails_remain_residual(self):
        registry = load_default_capability_registry()
        for text in (
            'Mill cards equal to the number of lands you control chosen secretly at random.',
            'Target player mills cards equal to that creature\'s power.',
            'Mill cards equal to the number of lands you control, unless that player pays {1}.',
            'Mill cards equal to the number of lands you control. Rewrite history.',
            'Each opponent mill cards equal to the number of lands you control.',
        ):
            with self.subTest(text=text):
                ir = compile_oracle_card(record(text, 'Sorcery'), capability_registry=registry,
                    capability_profile='commander_review')
                self.assertNotEqual('exact', ir.status)
                self.assertTrue(ir.material_residuals)

    def test_mandatory_public_land_count_uses_existing_quantity_and_preserves_surface(self):
        registry = load_default_capability_registry()
        for body in ('mill cards equal to the number of lands you control.',
                'mill X cards, where X is the number of lands you control.'):
            text = 'When this creature enters, ' + body
            ir = compile_oracle_card(record(text, 'Creature'), capability_registry=registry,
                capability_profile='commander_review')
            self.assertEqual('exact', ir.status, ir.material_residuals)
            node, = ir.faces[0].nodes
            self.assertEqual(text, node.text)
            self.assertEqual(text, text[node.span.start:node.span.end])

    def test_complete_equal_to_source_mill_matches_explicit_definition_and_spans(self):
        registry = load_default_capability_registry()
        for body in ('each opponent mills cards equal to its toughness.',
                'each opponent mills X cards, where X is its toughness.'):
            text = 'Whenever this creature attacks, ' + body
            ir = compile_oracle_card(record(text, 'Creature'), capability_registry=registry,
                capability_profile='commander_review')
            self.assertEqual('exact', ir.status, ir.material_residuals)
            for face in ir.faces:
                for node in face.nodes:
                    self.assertEqual(node.text, text[node.span.start:node.span.end])


class ScalarMillWordingRuntimeTests(unittest.TestCase):
    register_fixture_cards_on_add = True
    session = pairing.SoulbondRuntimeTests.session
    add = pairing.SoulbondRuntimeTests.add
    prepare = pairing.SoulbondRuntimeTests.prepare
    cast = pairing.SoulbondRuntimeTests.cast
    checkpoint = pairing.SoulbondRuntimeTests.checkpoint
    resolve = pairing.SoulbondRuntimeTests.resolve
    replay = pairing.SoulbondRuntimeTests.replay
    until = pairing.SoulbondRuntimeTests.until
    resolve_spell = pairing.SoulbondRuntimeTests.resolve_spell

    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        path = Path(cls.temp.name) / 'scalar-mill.sqlite3'
        build_fixture_database([ROOT / 'tests/fixtures/bound-effect-program-cards.json',
            ROOT / 'tests/fixtures/scalar-mill-wording-cards.json',
            ROOT / 'tests/fixtures/event-player-subject-cards.json'], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Scalar mill', [DeckEntry('Generic Bound Commander', 1, 'commander'),
            DeckEntry('Generic Bound Plains', 20)], ['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temp.cleanup()

    def binding(self, session, card):
        program = compile_best_available_card_program(self.db, self.db.lookup(card.printed_name),
            semantic_registry=session.engine.semantics, capability_registry=self.registry,
            capability_profile='commander_review')
        return bind_card_program_runtime(program, capability_registry=self.registry, profile='commander_review')

    def test_scalar_and_public_quantity_producer_omissions_are_killed_by_native_witnesses(self):
        with patch('quorune.compiler.scalar_effect_amounts._characteristic', return_value=None):
            with self.assertRaises(AssertionError):
                type(self)('test_actual_paired_attack_locks_one_mill_count_before_any_opponents_graveyard_changes').test_actual_paired_attack_locks_one_mill_count_before_any_opponents_graveyard_changes()
        with patch('quorune.compiler.declared_effect_amounts.resolution_quantity', return_value=None):
            with self.assertRaises(AssertionError):
                type(self)('test_public_land_mill_reads_own_current_count_after_real_sacrifice_response').test_public_land_mill_reads_own_current_count_after_real_sacrifice_response()

    def attack_fixture(self, seed):
        session = self.session(seed)
        source = self.add(session.engine, 'Imperious Mindbreaker', zone='hand', ref='printed-paired-mill')
        attacker = self.add(session.engine, 'Wight of Precinct Six', ref='mill-current-toughness')
        attacker.acquired_control_turn_count = -1
        milled_creature = self.add(session.engine, 'Generic Bound Body', seat='B', zone='library', ref='opponent-mill-top-creature')
        private = self.add(session.engine, 'Generic Bound Growth', seat='C', zone='hand', ref='scalar-mill-private')
        self.prepare(session, mana={'U':4, 'C':8})
        self.checkpoint(session)
        return session, source, attacker, milled_creature, private

    def test_actual_paired_attack_locks_one_mill_count_before_any_opponents_graveyard_changes(self):
        session, source, attacker, milled_creature, private = self.attack_fixture(70117001)
        for card in (source, attacker):
            self.assertTrue(self.binding(session, card)['strict_capability_ready'])
        self.assertEqual(1, session.engine._numeric_stat(attacker.object_id, 'toughness'))
        self.cast(session, 'A', source)
        self.assertEqual('semantic.choice', self.resolve(session).kind)
        result = session.act('pilot:A', {'action_id':'choose', 'cards':[attacker.ref]})
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(session))
        session = self.replay(session, load=True)
        self.until(session, lambda s:s.state.pending_decision is not None and s.state.pending_decision.kind == 'combat.attackers')
        libraries = {seat:len(player.zones['library']) for seat, player in session.state.players.items()}
        before = authoritative_state_hash(session.state)
        response = {'action_id':'attack', 'attackers':{attacker.ref:'B'}}
        self.assertFalse(session.act('pilot:D', response).ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        accepted = session.act('pilot:A', response)
        self.assertTrue(accepted.ok, accepted.summary)
        self.until(session, lambda s:bool(s.state.stack))
        self.assertEqual('A', session.state.stack[-1].controller)
        self.assertEqual(attacker.object_id, session.state.stack[-1].source_object_id)
        session = self.replay(session, load=True)
        self.assertIsNone(self.resolve(session))
        for seat in 'ABCD':
            expected = 0 if seat == 'A' else 1
            self.assertEqual(expected, libraries[seat] - len(session.state.players[seat].zones['library']), seat)
        self.assertEqual('graveyard', session.state.cards[milled_creature.object_id].zone)
        self.assertEqual(2, session.engine._numeric_stat(attacker.object_id, 'toughness'))
        for viewer in 'ABD':
            self.assertNotIn(private.ref, json.dumps(session.packet('pilot:' + viewer, full=True)))
        self.replay(session, load=True)

    def pending_paired_attack(self, session, source, attacker):
        self.assertTrue(self.binding(session, source)['strict_capability_ready'])
        self.cast(session, 'A', source)
        self.assertEqual('semantic.choice', self.resolve(session).kind)
        self.assertTrue(session.act('pilot:A', {'action_id':'choose', 'cards':[attacker.ref]}).ok)
        self.assertIsNone(self.resolve(session))
        self.until(session, lambda s:s.state.pending_decision is not None and s.state.pending_decision.kind == 'combat.attackers')
        libraries = {seat:len(player.zones['library']) for seat, player in session.state.players.items()}
        accepted = session.act('pilot:A', {'action_id':'attack', 'attackers':{attacker.ref:'B'}})
        self.assertTrue(accepted.ok, accepted.summary)
        self.until(session, lambda s:bool(s.state.stack))
        self.assertEqual('A', session.state.stack[-1].controller)
        return libraries

    def test_public_land_mill_reads_own_current_count_after_real_sacrifice_response(self):
        session = self.session(70117002)
        source = self.add(session.engine, 'Generic Public Land Mill', zone='hand')
        island = self.add(session.engine, 'Island', ref='mill-count-own-island')
        self.add(session.engine, 'Forest', ref='mill-count-own-forest')
        orb = self.add(session.engine, 'Zuran Orb', ref='mill-count-orb')
        for index in range(3):
            self.add(session.engine, 'Generic Bound Plains', seat='B', ref='mill-count-foreign-land-' + str(index))
        private = self.add(session.engine, 'Generic Bound Growth', seat='C', zone='hand', ref='public-mill-private')
        self.prepare(session, mana={'U':2, 'C':4})
        self.checkpoint(session)
        for card in (source, orb):
            self.assertTrue(self.binding(session, card)['strict_capability_ready'])
        libraries = {seat:len(player.zones['library']) for seat, player in session.state.players.items()}
        self.cast(session, 'A', source)
        self.resolve_spell(session, source)
        self.assertEqual(1, len(session.state.stack))
        actions = session.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']
        offered = next(row for row in actions if row['id'].startswith('activate:' + orb.ref + ':'))
        command = {'action_id':offered['id'], 'cost_cards':[island.ref]}
        before = authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:B', command).ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        accepted = session.act('pilot:A', command)
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertEqual('graveyard', session.state.cards[island.object_id].zone)
        session = self.replay(session, load=True)
        self.assertIsNone(self.resolve(session))
        self.assertEqual(42, session.state.players['A'].life)
        for seat in 'ABCD':
            self.assertEqual(1 if seat == 'A' else 0,
                libraries[seat] - len(session.state.players[seat].zones['library']), seat)
        for viewer in 'ABD':
            self.assertNotIn(private.ref, json.dumps(session.packet('pilot:' + viewer, full=True)))
        self.replay(session, load=True)

    def test_paired_mill_keeps_trigger_controller_but_reads_current_recipient_after_control_change(self):
        session, source, attacker, _, private = self.attack_fixture(70117003)
        for index in range(2):
            self.add(session.engine, 'Generic Bound Body', seat='C', zone='graveyard', ref='mill-C-grave-' + str(index))
        control = self.add(session.engine, 'Generic Event Subject Control', seat='C', zone='hand')
        self.add(session.engine, 'Generic Bound Plains', seat='C', ref='mill-control-response-mana')
        self.prepare(session, mana={'U':4, 'C':8})
        self.checkpoint(session)
        self.assertEqual(3, session.engine._numeric_stat(attacker.object_id, 'toughness'))
        libraries = self.pending_paired_attack(session, source, attacker)
        self.cast(session, 'C', control, [attacker.ref])
        self.until(session, lambda s:s.state.cards[attacker.object_id].controller == 'C')
        self.assertEqual((), session.state.soulbond_pairs)
        self.assertEqual(1, session.engine._numeric_stat(attacker.object_id, 'toughness'))
        self.assertEqual('A', session.state.stack[-1].controller)
        session = self.replay(session, load=True)
        self.assertIsNone(self.resolve(session))
        for seat in 'ABCD':
            self.assertEqual(0 if seat == 'A' else 1,
                libraries[seat] - len(session.state.players[seat].zones['library']), seat)
        for viewer in 'ABD':
            self.assertNotIn(private.ref, json.dumps(session.packet('pilot:' + viewer, full=True)))
        self.replay(session, load=True)

    def test_paired_mill_uses_boosted_departure_lki_instead_of_trigger_or_new_hand_incarnation(self):
        session, source, attacker, _, private = self.attack_fixture(70117004)
        for index in range(2):
            self.add(session.engine, 'Generic Bound Body', seat='B', zone='graveyard', ref='mill-B-grave-' + str(index))
        growth = self.add(session.engine, 'Giant Growth', zone='hand')
        bounce = self.add(session.engine, 'Unsummon', zone='hand')
        self.add(session.engine, 'Forest', ref='mill-growth-response-mana')
        self.add(session.engine, 'Island', ref='mill-bounce-response-mana')
        self.prepare(session, mana={'U':4, 'C':8})
        self.checkpoint(session)
        self.assertEqual(3, session.engine._numeric_stat(attacker.object_id, 'toughness'))
        libraries = self.pending_paired_attack(session, source, attacker)
        self.cast(session, 'A', growth, [attacker.ref])
        self.until(session, lambda s:s.engine._numeric_stat(attacker.object_id, 'toughness') == 6)
        old_identity = session.state.cards[attacker.object_id].logical_object_id
        self.cast(session, 'A', bounce, [attacker.ref])
        self.until(session, lambda s:s.state.cards[attacker.object_id].zone == 'hand')
        self.assertNotEqual(old_identity, session.state.cards[attacker.object_id].logical_object_id)
        self.assertEqual(1, session.engine._numeric_stat(attacker.object_id, 'toughness'))
        self.assertEqual((), session.state.soulbond_pairs)
        self.assertEqual('A', session.state.stack[-1].controller)
        session = self.replay(session, load=True)
        self.assertIsNone(self.resolve(session))
        for seat in 'ABCD':
            self.assertEqual(0 if seat == 'A' else 6,
                libraries[seat] - len(session.state.players[seat].zones['library']), seat)
        for viewer in 'ABD':
            self.assertNotIn(private.ref, json.dumps(session.packet('pilot:' + viewer, full=True)))
        self.replay(session, load=True)
