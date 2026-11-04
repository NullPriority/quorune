from __future__ import annotations

"""Public graveyard exile preserves the selecting controller's owner relation."""

import json
import unittest

from quorune.compiler.public_zone_move_templates import public_graveyard_card_exile_template
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from test_granted_skulk import record
import test_source_self_optional_results as source_fixture


class OwnerRelativeGraveyardExileCompilerTests(unittest.TestCase):
    def test_closed_owner_relations_lower_the_same_physical_card_exile(self):
        registry = load_default_capability_registry()
        for zone, owner in (("a graveyard", "any"), ("your graveyard", "you"),
                            ("an opponent's graveyard", "opponent")):
            for subject in ("card", "creature card", "instant or sorcery card"):
                with self.subTest(zone=zone, subject=subject):
                    text = "Exile target " + subject + " from " + zone + "."
                    leaf = public_graveyard_card_exile_template(text)
                    self.assertIsNotNone(leaf)
                    _, effects, schema, _ = leaf.compiled()
                    self.assertEqual(({'op': 'exile_public_graveyard_card', 'card': '$target.0'},), effects)
                    self.assertEqual(owner, schema['owner_relation'])
                    ir = compile_oracle_card(record(text, 'Instant'),
                        capability_registry=registry, capability_profile='commander_review')
                    self.assertEqual('exact', ir.status, ir.material_residuals)
                    self.assertIn('card.exile.public_graveyard', ir.faces[0].nodes[0].capability_dependencies)

    def test_owner_schema_and_unrepresented_grammar_fail_closed(self):
        from quorune.rules.graveyard_card_targets import PublicGraveyardCardTargetSpec
        from quorune.rules.public_zone_move_capability_shapes import public_graveyard_card_exile_node_capabilities
        leaf = public_graveyard_card_exile_template("Exile target creature card from an opponent's graveyard.")
        _, effects, schema, mechanics = leaf.compiled()
        for field, value in (('owner_relation', 'teammate'), ('owner_relation', True),
                             ('count', True), ('count', 2), ('zones', ['hand']),
                             ('controller_relation', 'you'), ('unknown', True)):
            changed = {**schema, field: value}
            with self.subTest(field=field, value=value):
                with self.assertRaises((TypeError, ValueError)):
                    PublicGraveyardCardTargetSpec.from_target_schema(changed)
                self.assertEqual((), public_graveyard_card_exile_node_capabilities(
                    effects=effects, target_schema=changed, mechanic_ids=mechanics))
        for text in ("Exile target card from defending player's graveyard.",
                     "Exile target card from an opponent's graveyard. Draw a card.",
                     "Exile target creature card with flying from your graveyard."):
            self.assertIsNone(public_graveyard_card_exile_template(text))

    def test_existing_target_set_lifting_preserves_the_closed_owner_relation(self):
        registry = load_default_capability_registry()
        for zone, owner in (("your graveyard", "you"), ("an opponent's graveyard", "opponent")):
            with self.subTest(zone=zone):
                ir = compile_oracle_card(record('Exile up to two target creature cards from ' + zone + '.', 'Instant'),
                    capability_registry=registry, capability_profile='commander_review')
                self.assertEqual('exact', ir.status, ir.material_residuals)
                node, = ir.faces[0].nodes
                self.assertEqual(owner, node.target_schema['owner_relation'])
                self.assertEqual('exile_public_graveyard_targets', node.effects[0]['op'])
                self.assertIn('card.exile.public_graveyard', node.capability_dependencies)


class OwnerRelativeGraveyardExileRuntimeTests(unittest.TestCase):
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
    choose = source_fixture.SourceSelfOptionalRuntimeTests.choose

    def test_original_simple_owner_relative_graveyard_exile_carriers_bind_normally(self):
        from quorune.card_programs import bind_card_program_runtime
        from quorune.card_programs.adapters import compile_best_available_card_program
        from quorune.oracle_ir import register_generated_programs
        from quorune.semantics import SemanticRegistry
        semantics = SemanticRegistry()
        for name in ('Disposal Mummy', 'Leonin of the Lost Pride', 'Disruptor Wanderglyph', 'Scavenging Harpy', 'Ruin Rat'):
            with self.subTest(name=name):
                card = self.db.lookup(name)
                register_generated_programs(self.db, semantics, (card,), trust_level='provisional',
                    capability_registry=self.registry, capability_profile='commander_review',
                    promote_exact_runtime_handlers=True, promote_exact_trigger_programs=True,
                    promote_exact_effect_programs=True, promote_exact_capability_declarations=True)
                program = compile_best_available_card_program(self.db, card, semantic_registry=semantics,
                    capability_registry=self.registry, capability_profile='commander_review')
                binding = bind_card_program_runtime(program, capability_registry=self.registry, profile='commander_review')
                self.assertTrue(binding['strict_capability_ready'], binding['blockers'])

    def run_original_mummy_owner_relative_target_scenario(self, *, rescue):
        session = self.session(402130 + rescue)
        engine = session.engine
        source = self.add(engine, 'Disposal Mummy', zone='hand', ref='original-owner-relative-mummy')
        own = self.add(engine, 'Generic Optional Ground', zone='graveyard', ref='own-graveyard-card')
        opponents = [self.add(engine, 'Generic Optional Ground', seat=seat, zone='graveyard',
                              ref='opponent-graveyard-' + seat) for seat in 'BCD']
        selected = opponents[1]
        response = self.add(engine, 'Generic Graveyard Exile Rescue' if rescue else 'Generic Event Subject Control',
                            seat='C', zone='hand', ref='graveyard-exile-response')
        own_exile = self.add(engine, 'Generic Own Graveyard Exile', zone='hand')
        private = self.add(engine, 'Generic Bound Growth', seat='D', zone='hand', ref='graveyard-exile-private')
        self.assert_closed(session, source)
        self.assert_closed(session, response)
        self.prepare(session, mana={'W': 4})
        session.state.players['C'].mana_pool['W'] = 1
        self.checkpoint(session)
        self.cast(session, 'A', source)
        self.assertEqual('semantic.target', self.resolve(session).kind)
        public_schema = session.packet('pilot:A', full=True)['decision']['ctx']['target_schema']
        self.assertEqual({card.ref for card in opponents}, set(public_schema['legal_refs']))
        for principal, target in (('pilot:A', own.ref), ('pilot:A', private.ref), ('pilot:B', selected.ref)):
            before = authoritative_state_hash(session.state)
            rejected = session.act(principal, {'action_id': 'choose', 'targets': [target]})
            self.assertFalse(rejected.ok)
            self.assertEqual(before, authoritative_state_hash(session.state))
        session = self.replay(session, load=True)
        self.choose(session, targets=[selected.ref])
        self.assertEqual('A', session.state.stack[-1].controller)
        self.cast(session, 'C', response, [selected.ref if rescue else source.ref])
        self.resolve(session)
        self.assertEqual('C', session.state.cards[selected.object_id].owner)
        self.assertEqual('hand' if rescue else 'exile', session.state.cards[selected.object_id].zone)
        if not rescue:
            self.assertEqual('C', session.state.cards[source.object_id].controller)
            self.assertIn(selected.object_id, session.state.players['C'].zones['exile'])
        self.assertEqual('graveyard', session.state.cards[own.object_id].zone)
        self.assertEqual('graveyard', session.state.cards[opponents[0].object_id].zone)
        session = self.replay(session, load=True)
        for _ in range(12):
            if session.state.priority_player == 'A':
                break
            self.assertTrue(session.act(session.pending_principals()[0], {'action_id': 'pass'}).ok)
        actions = session.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']
        offered, = [row for row in actions if row['id'] == 'cast:' + own_exile.ref]
        self.assertEqual([own.ref], offered['target_schema']['legal_refs'])
        before = authoritative_state_hash(session.state)
        rejected = session.act('pilot:A', {'action_id': offered['id'], 'targets': [opponents[2].ref], 'pay': 'auto'})
        self.assertFalse(rejected.ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        self.cast(session, 'A', own_exile, [own.ref])
        self.resolve(session)
        self.assertEqual('exile', session.state.cards[own.object_id].zone)
        self.assertIn(own.object_id, session.state.players['A'].zones['exile'])
        for viewer in 'ABC':
            self.assertNotIn(private.ref, json.dumps(session.packet('pilot:' + viewer, full=True)))
        self.replay(session, load=True)

    def test_original_mummy_exile_keeps_stacked_controller_after_source_control_change(self):
        self.run_original_mummy_owner_relative_target_scenario(rescue=False)

    def test_original_mummy_exile_rejects_departed_target_then_own_exile_uses_its_owner(self):
        self.run_original_mummy_owner_relative_target_scenario(rescue=True)

    def test_owner_predicate_widening_is_killed_by_native_target_offers(self):
        from unittest.mock import patch
        from quorune.rules.graveyard_card_targets import PublicGraveyardCardTargetSpec
        original = PublicGraveyardCardTargetSpec.to_target_schema
        def widen_owner(spec):
            return {**original(spec), 'owner_relation': 'any'}
        with patch.object(PublicGraveyardCardTargetSpec, 'to_target_schema', widen_owner):
            with self.assertRaises(AssertionError):
                self.run_original_mummy_owner_relative_target_scenario(rescue=False)

    def test_own_graveyard_target_set_keeps_only_the_still_legal_incarnation(self):
        session = self.session(402132)
        source = self.add(session.engine, 'Generic Own Graveyard Set Exile', zone='hand')
        rescue = self.add(session.engine, 'Generic Graveyard Exile Rescue', zone='hand')
        first = self.add(session.engine, 'Generic Optional Ground', zone='graveyard', ref='own-first-set-target')
        second = self.add(session.engine, 'Generic Optional Ground', zone='graveyard', ref='own-second-set-target')
        foreign = self.add(session.engine, 'Generic Optional Ground', seat='B', zone='graveyard', ref='foreign-set-target')
        self.assert_closed(session, source)
        self.prepare(session, mana={'W': 2})
        self.checkpoint(session)
        actions = session.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']
        offered, = [row for row in actions if row['id'] == 'cast:' + source.ref]
        self.assertEqual({first.ref, second.ref}, set(offered['target_schema']['legal_refs']))
        before = authoritative_state_hash(session.state)
        rejected = session.act('pilot:A', {'action_id': offered['id'], 'targets': [first.ref, foreign.ref], 'pay': 'auto'})
        self.assertFalse(rejected.ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        self.cast(session, 'A', source, [first.ref, second.ref])
        session = self.replay(session, load=True)
        self.cast(session, 'A', rescue, [first.ref])
        self.resolve(session)
        self.assertEqual('hand', session.state.cards[first.object_id].zone)
        self.assertEqual('exile', session.state.cards[second.object_id].zone)
        self.assertEqual('graveyard', session.state.cards[foreign.object_id].zone)
        self.replay(session, load=True)
