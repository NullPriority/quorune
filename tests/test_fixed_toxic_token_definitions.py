from __future__ import annotations

"""Fixed token definitions retain Toxic and intrinsic declaration fragments."""

import json
import unittest

from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from test_granted_skulk import record
import test_source_self_optional_results as source_fixture


class FixedToxicTokenCompilerTests(unittest.TestCase):
    def test_fixed_toxic_token_definitions_retain_typed_poison_and_declaration_owners(self):
        from quorune.ability_fragments import ToxicSpec, canonical_ability_fragments
        for text, type_line in (
            ('Create a 3/3 green Phyrexian Beast creature token with toxic 1.', 'Sorcery'),
            ('Create two 1/1 colorless Phyrexian Mite artifact creature tokens with toxic 1 and "This token can\'t block."', 'Sorcery'),
            ('When this creature dies, create a 1/1 colorless Phyrexian Mite artifact creature token with toxic 1 and "This token can\'t block."', 'Creature — Phyrexian'),
            ('{3}, {T}: Create a 1/1 colorless Phyrexian Mite artifact creature token with toxic 1 and "This token can\'t block."', 'Land'),
        ):
            with self.subTest(text=text):
                ir = compile_oracle_card(record(text, type_line),
                    capability_registry=load_default_capability_registry(), capability_profile='commander_review')
                self.assertEqual('exact', ir.status, ir.material_residuals)
                tokens = [effect for face in ir.faces for node in face.nodes for effect in node.effects
                          if effect.get('op') == 'create_token']
                self.assertEqual(1, len(tokens))
                fragments = canonical_ability_fragments(tokens[0]['characteristics']['ability_fragments'])
                self.assertIn(ToxicSpec(value=1), fragments)
                dependencies = {cap for face in ir.faces for node in face.nodes for cap in node.capability_dependencies}
                self.assertIn('damage.result.toxic', dependencies)
                if 'can\'t block' in text:
                    self.assertIn('combat.declaration.typed_components', dependencies)


    def test_fixed_toxic_token_definition_rejects_mismatched_fragments_and_infers_runtime_owner(self):
        from copy import deepcopy
        from quorune.card_programs.descriptor_requirements import typed_result_amount_runtime_dependencies
        from quorune.compiler.token_templates import fixed_token_creation_effect_template
        from quorune.rules.token_creation_capability_shapes import fixed_token_creation_node_capabilities
        template = fixed_token_creation_effect_template(
            'Create a 3/3 green Phyrexian Beast creature token with toxic 2.')
        self.assertIsNotNone(template)
        self.assertIn('damage.result.toxic', typed_result_amount_runtime_dependencies(template.effect))
        for field, value in (
            ('ability_fragments', []),
            ('ability_fragments', [{'kind': 'toxic', 'value': {'schema_version': 1, 'value': 1}}]),
            ('ability_fragments', [{'kind': 'toxic', 'value': {'schema_version': 1, 'value': True}}]),
            ('keywords', ['Toxic 0']),
            ('keywords', ['Toxic X']),
        ):
            with self.subTest(field=field, value=value):
                effect = deepcopy(template.effect)
                effect['characteristics'][field] = value
                self.assertEqual((), fixed_token_creation_node_capabilities(
                    effects=(effect,), target_schema=None, mechanic_ids=template.mechanics))
                with self.assertRaises((TypeError, ValueError)):
                    typed_result_amount_runtime_dependencies(effect)
        for toxic in ('0', '-1', 'X', '1 and an unsupported ability'):
            self.assertIsNone(fixed_token_creation_effect_template(
                'Create a 3/3 green Phyrexian Beast creature token with toxic ' + toxic + '.'))



class FixedToxicTokenRuntimeTests(unittest.TestCase):
    register_fixture_cards_on_add = True
    setUpClass = classmethod(source_fixture.SourceSelfOptionalRuntimeTests.setUpClass.__func__)
    tearDownClass = classmethod(source_fixture.SourceSelfOptionalRuntimeTests.tearDownClass.__func__)
    session = source_fixture.SourceSelfOptionalRuntimeTests.session
    add = source_fixture.SourceSelfOptionalRuntimeTests.add
    checkpoint = source_fixture.SourceSelfOptionalRuntimeTests.checkpoint
    resolve = source_fixture.SourceSelfOptionalRuntimeTests.resolve
    replay = source_fixture.SourceSelfOptionalRuntimeTests.replay
    prepare = source_fixture.SourceSelfOptionalRuntimeTests.prepare
    cast = source_fixture.SourceSelfOptionalRuntimeTests.cast
    assert_closed = source_fixture.SourceSelfOptionalRuntimeTests.assert_closed

    def test_original_fixed_toxic_token_carriers_have_complete_bindings(self):
        from quorune.card_programs import bind_card_program_runtime
        from quorune.card_programs.adapters import compile_best_available_card_program
        from quorune.oracle_ir import register_generated_programs
        from quorune.semantics import SemanticRegistry
        semantics = SemanticRegistry()
        for name in ('Mirrex', 'Stinging Hivemaster', 'Vishgraz, the Doomhive',
                     'Basilica Shepherd', 'Crawling Chorus', 'Mite Overseer', 'Infested Fleshcutter'):
            with self.subTest(name=name):
                source = self.db.lookup(name)
                register_generated_programs(self.db, semantics, (source,), trust_level='provisional',
                    capability_registry=self.registry, capability_profile='commander_review',
                    promote_exact_runtime_handlers=True, promote_exact_trigger_programs=True,
                    promote_exact_effect_programs=True, promote_exact_capability_declarations=True)
                program = compile_best_available_card_program(self.db, source, semantic_registry=semantics,
                    capability_registry=self.registry, capability_profile='commander_review')
                binding = bind_card_program_runtime(program, capability_registry=self.registry, profile='commander_review')
                self.assertTrue(binding['strict_capability_ready'], binding['blockers'])


    def run_original_mite_creation_and_combat_scenario(self):
        from quorune.ability_fragments import ToxicSpec
        session = self.session(402121)
        engine = session.engine
        source = self.add(engine, 'Basilica Shepherd', zone='hand', ref='actual-toxic-token-entry')
        mirrex = self.add(engine, 'Mirrex', seat='B', ref='actual-toxic-token-activation')
        self.add(engine, 'Generic Optional Ground', seat='B', ref='legal-toxic-combat-blocker')
        self.add(engine, 'Fervor', ref='toxic-token-haste')
        growth = self.add(engine, 'Generic Bound Growth', zone='hand', ref='toxic-token-growth')
        private = self.add(engine, 'Generic Bound Growth', seat='D', zone='hand', ref='toxic-token-private')
        self.assert_closed(session, source)
        self.assert_closed(session, mirrex)
        self.prepare(session, mana={'W': 5, 'G': 1})
        session.state.players['B'].mana_pool['C'] = 3
        self.checkpoint(session)
        self.cast(session, 'A', source)
        self.resolve(session)
        mites = [card for card in session.state.cards.values()
                 if card.is_token and card.controller == 'A' and card.zone == 'battlefield']
        self.assertEqual(2, len(mites))
        for mite in mites:
            self.assertIn(ToxicSpec(value=1), session.engine._effective_ability_fragments(mite))
            types, subtypes, _ = session.engine._type_parts(session.engine._effective_card_data(mite)['type_line'])
            self.assertEqual({'artifact', 'creature'}, set(types))
            self.assertEqual({'phyrexian', 'mite'}, set(subtypes))
        session = self.replay(session, load=True)
        self.cast(session, 'A', growth, [mites[0].ref])
        self.resolve(session)
        for _ in range(12):
            if session.state.priority_player == 'B':
                break
            self.assertTrue(session.act(session.pending_principals()[0], {'action_id': 'pass'}).ok)
        actions = session.packet('pilot:B', full=True)['decision']['ctx']['legal']['actions']
        offered, = [row for row in actions if row['id'] == 'activate:' + mirrex.ref + ':ab3']
        before = authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:C', {'action_id': offered['id'], 'pay': 'auto'}).ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        accepted = session.act('pilot:B', {'action_id': offered['id'], 'pay': 'auto'})
        self.assertTrue(accepted.ok, accepted.summary)
        session = self.replay(session, load=True)
        self.resolve(session)
        enemy_mites = [card for card in session.state.cards.values()
                       if card.is_token and card.controller == 'B' and card.zone == 'battlefield']
        self.assertEqual(1, len(enemy_mites))
        self.assertTrue(session.state.cards[mirrex.object_id].tapped)
        self.assertEqual(0, sum(session.state.players['B'].mana_pool.values()))
        for _ in range(24):
            if session.state.pending_decision.kind == 'combat.attackers':
                break
            self.assertTrue(session.act(session.pending_principals()[0], {'action_id': 'pass'}).ok)
        else:
            self.fail('The real main phase did not reach attacker declaration')
        accepted = session.act('pilot:A', {'action_id': 'attack', 'attackers': {mite.ref: 'B' for mite in mites}})
        self.assertTrue(accepted.ok, accepted.summary)
        for _ in range(16):
            if session.state.pending_decision.kind == 'combat.blockers':
                break
            self.assertTrue(session.act(session.pending_principals()[0], {'action_id': 'pass'}).ok)
        else:
            self.fail('The defending player did not receive its legal blocker choice')
        before = authoritative_state_hash(session.state)
        rejected = session.act('pilot:B', {'action_id': 'block', 'blocks': {enemy_mites[0].ref: mites[0].ref}})
        self.assertFalse(rejected.ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        accepted = session.act('pilot:B', {'action_id': 'block', 'blocks': {}})
        self.assertTrue(accepted.ok, accepted.summary)
        session = self.replay(session, load=True)
        for _ in range(24):
            if session.state.players['B'].life == 36:
                break
            if session.state.pending_decision.kind == 'combat.damage':
                result = session.act(session.pending_principals()[0], {'action_id': 'assign_damage',
                    'assignments': [{'source': mite.ref, 'target': 'B', 'amount': 3 if i == 0 else 1}
                                    for i, mite in enumerate(mites)]})
            else:
                result = session.act(session.pending_principals()[0], {'action_id': 'pass'})
            self.assertTrue(result.ok, result.summary)
        self.assertEqual(36, session.state.players['B'].life)
        self.assertEqual({'A': 0, 'B': 2, 'C': 0, 'D': 0},
                         {seat: player.poison for seat, player in session.state.players.items()})
        for viewer in 'ABC':
            self.assertNotIn(private.ref, json.dumps(session.packet('pilot:' + viewer, full=True)))
        self.replay(session, load=True)


    def test_original_mite_entry_and_activation_keep_cannot_block_and_actual_combat_poison(self):
        self.run_original_mite_creation_and_combat_scenario()


    def test_created_mite_toxic_fragment_mutation_is_killed_by_normal_admission(self):
        from unittest.mock import patch
        from quorune.ability_fragments import ToxicSpec
        with patch('quorune.compiler.token_templates.ToxicSpec', side_effect=lambda *, value: ToxicSpec(value=value + 1)):
            with self.assertRaises(AssertionError):
                self.run_original_mite_creation_and_combat_scenario()

