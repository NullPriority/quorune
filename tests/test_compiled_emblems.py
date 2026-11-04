from __future__ import annotations

import json
import unittest

from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from test_granted_skulk import record
from quorune.record import authoritative_state_hash
import test_source_self_optional_results as source_fixture
import test_emblem_creation as creation_fixture


class CompiledEmblemValueTests(unittest.TestCase):
    def specification(self):
        from quorune.emblem_program_model import CompiledEmblemSpec, EmblemAbilityProgram
        return CompiledEmblemSpec('fixture:emblem-parent', (EmblemAbilityProgram(
            semantic_key='fixture:emblem-parent:trigger:granted:emblem:1', kind='triggered', event='creature.enter',
            text='Whenever a creature you control enters, draw a card.',
            capabilities=('object.emblem.compiled_abilities', 'zone.draw.library_to_hand')),))

    def test_reference_schema_and_independent_runtime_minimum_are_closed(self):
        from copy import deepcopy
        from types import SimpleNamespace
        from quorune.emblem_program_model import CompiledEmblemSpec, compiled_emblem_runtime_dependencies
        spec = self.specification()
        effect = {'op': 'create_compiled_emblem', 'controller': '$controller', 'emblem': spec.to_dict()}
        required = compiled_emblem_runtime_dependencies(SimpleNamespace(effects=(effect,), coverage=(),
            active_zone='battlefield', provenance={}))
        self.assertTrue(set(spec.capabilities) <= set(required))
        for field, value in (('schema_version', True), ('schema_version', 2), ('oracle_id', ''),
                             ('abilities', []), ('unknown', True)):
            data = deepcopy(spec.to_dict());data[field] = value
            with self.subTest(field=field):
                with self.assertRaises((TypeError, ValueError)):
                    CompiledEmblemSpec.from_dict(data)
        for field, value in (('kind', 'activated'), ('event', ''), ('event', 'characteristics.evaluate'), ('capabilities', []), ('unknown', True)):
            data = deepcopy(spec.to_dict());data['abilities'][0][field] = value
            with self.subTest(child_field=field):
                with self.assertRaises((TypeError, ValueError)):
                    CompiledEmblemSpec.from_dict(data)

    def test_unavailable_wrong_source_and_underdeclared_children_fail_before_allocation(self):
        from copy import deepcopy
        from types import SimpleNamespace
        from quorune.emblem_program_model import EMBLEM_CHILD_COVERAGE
        from quorune.emblems import create_compiled_emblem
        from quorune.errors import GameRuleError
        spec = self.specification();reference = spec.abilities[0]
        original = SimpleNamespace(key=reference.semantic_key, oracle_id=spec.oracle_id, active_zone='command',
            event=reference.event, provenance={'granted_only': True}, coverage=(EMBLEM_CHILD_COVERAGE,),
            cost_schema=None, capability_dependencies=reference.capabilities,
            effects=({'op': 'draw', 'player': '$controller', 'count': 1},), handlers=())
        for field, value in (('missing', None), ('key', 'other-program'), ('oracle_id', 'other-source'), ('active_zone', 'battlefield'),
                             ('event', 'spell.cast'), ('coverage', ()), ('provenance', {}),
                             ('capability_dependencies', ('zone.draw.library_to_hand',)), ('trusted', False)):
            host = creation_fixture.EmblemCreationHostFixture();program = deepcopy(original)
            if field not in {'missing', 'trusted'}:
                setattr(program, field, value)
            host.semantics = SimpleNamespace(get=lambda key: None if field == 'missing' else program)
            host.semantic_program_is_current_trusted = lambda p: field != 'trusted'
            before = host.snapshot()
            with self.subTest(field=field):
                with self.assertRaises(GameRuleError):
                    create_compiled_emblem(host, 'A', spec, reason='invalid reference witness')
                self.assertEqual(before, host.snapshot())
        host = creation_fixture.EmblemCreationHostFixture()
        host.semantics = SimpleNamespace(get=lambda key: original)
        host.semantic_program_is_current_trusted = lambda p: True
        create_compiled_emblem(host, 'A', spec, reason='valid reference witness')
        from quorune.emblem_programs import command_emblem_programs
        emblem, = host.state.cards.values()
        self.assertEqual((original,), command_emblem_programs(host.semantics, emblem, program_is_trusted=lambda p: True))
        emblem.object_kind = 'card'
        with self.assertRaises(GameRuleError):
            command_emblem_programs(host.semantics, emblem, program_is_trusted=lambda p: True)

    def test_source_prevention_validates_the_complete_instruction_before_absent_source_noop(self):
        from quorune.source_prevention import source_prevention_dependencies, source_prevention_subject_unavailable
        from quorune.errors import GameRuleError
        descriptor = {'op': 'create_damage_prevention_shield', 'source': '$source', 'subject': '$source.zone_object',
                      'mode': 'all', 'duration': 'until_end_of_turn', 'source_subject_version': 1}
        self.assertEqual(('damage.prevention.persistent_amount',), source_prevention_dependencies(descriptor))
        resolved = {**descriptor, 'source': 'original-source', 'subject': None}
        self.assertTrue(source_prevention_subject_unavailable(None, resolved, actor='A'))
        for field, value in (('source_subject_version', True), ('mode', 'unknown'), ('duration', 'permanent'), ('unknown', True)):
            with self.subTest(field=field):
                with self.assertRaises((TypeError, ValueError)):
                    source_prevention_dependencies({**descriptor, field: value})
                with self.assertRaises(GameRuleError):
                    source_prevention_subject_unavailable(None, {**resolved, field: value}, actor='A')


class CompiledEmblemCompilerTests(unittest.TestCase):
    def test_zero_loyalty_symbol_is_distinct_from_zero_mana(self):
        from quorune.abilities import parse_activated_abilities
        zero, = parse_activated_abilities(card_name='Generic Walker', oracle_text='0: Create a 2/2 white Knight Ally creature token.')
        free, = parse_activated_abilities(card_name='Generic Artifact', oracle_text='{0}: Create a 2/2 white Knight Ally creature token.')
        self.assertEqual(0, zero.loyalty_delta)
        self.assertFalse(zero.uncompiled_costs)
        self.assertIsNone(free.loyalty_delta)

    def test_source_walker_animation_retains_types_and_prevents_damage_to_itself(self):
        text = ('+1: Until end of turn, this planeswalker becomes a 5/5 Human Soldier Ally creature '
                "with indestructible that's still a planeswalker. Prevent all damage that would be dealt to him this turn.")
        ir = compile_oracle_card(record(text, 'Planeswalker — Generic'),
            capability_registry=load_default_capability_registry(), capability_profile='commander_review')
        self.assertEqual('exact', ir.status, ir.material_residuals)
        node, = ir.faces[0].nodes
        self.assertEqual(2, len(node.effects))
        self.assertEqual('$source.zone_object', node.effects[0]['card'])

    def test_static_and_triggered_emblem_children_are_bound_to_command_objects(self):
        registry = load_default_capability_registry()
        for body in ('Creatures you control get +1/+1.',
                     'At the beginning of your end step, create a 1/1 white Soldier creature token.'):
            for prefix, type_line in (('', 'Sorcery'), ('−2: ', 'Planeswalker — Generic')):
                with self.subTest(body=body, prefix=prefix):
                    ir = compile_oracle_card(record(prefix + 'You get an emblem with "' + body + '"', type_line),
                        capability_registry=registry, capability_profile='commander_review')
                    self.assertEqual('exact', ir.status, ir.material_residuals)
                    parent, child = ir.faces[0].nodes
                    self.assertEqual('create_compiled_emblem', parent.effects[0]['op'])
                    self.assertEqual('command', child.active_zone)
                    self.assertTrue(child.kind.startswith('granted_'))
                    self.assertIn('compiled-emblem-child', child.runtime_coverage)
                    self.assertEqual(body, child.text)
                    self.assertIn('object.emblem.compiled_abilities', parent.capability_dependencies)

    def test_unknown_emblem_child_keeps_the_whole_parent_residual(self):
        registry = load_default_capability_registry()
        for body in ('Perform an unsupported action.', "Your opponents can't cast noncreature spells."):
            with self.subTest(body=body):
                ir = compile_oracle_card(record('You get an emblem with "' + body + '"', 'Sorcery'),
                    capability_registry=registry, capability_profile='commander_review')
                self.assertNotEqual('exact', ir.status)

    def test_emblem_compiler_omission_is_killed(self):
        from unittest.mock import patch
        with patch('quorune.compiler.emblem_nodes.compiled_emblem_nodes', return_value=None):
            ir = compile_oracle_card(record('You get an emblem with "Creatures you control get +1/+1."', 'Sorcery'),
                capability_registry=load_default_capability_registry(), capability_profile='commander_review')
        self.assertNotEqual('exact', ir.status)


class CompiledEmblemRuntimeTests(unittest.TestCase):
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
    choose = source_fixture.SourceSelfOptionalRuntimeTests.choose
    def assert_closed(self, session, card):
        from quorune.card_programs import bind_card_program_runtime
        from quorune.card_programs.adapters import compile_best_available_card_program
        program = compile_best_available_card_program(self.db, self.db.lookup(card.printed_name),
            semantic_registry=session.engine.semantics, capability_registry=self.registry, capability_profile='commander_review')
        binding = bind_card_program_runtime(program, capability_registry=self.registry, profile='commander_review')
        self.assertTrue(binding['strict_capability_ready'], {
            'card': card.printed_name, 'trust_basis': binding['trust_basis'], 'blockers': binding['blockers'],
            'programs': [(p.ability_id, p.trust_level) for p in session.engine.semantics.programs_for_oracle(card.oracle_id)]})

    def activate(self, session, source, ability):
        for _ in range(12):
            if session.state.priority_player == 'A':
                break
            self.assertTrue(session.act(session.pending_principals()[0], {'action_id': 'pass'}).ok)
        rows = session.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']
        offered, = [row for row in rows if row['id'] == 'activate:' + source.ref + ':' + ability]
        before = authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:B', {'action_id': offered['id'], 'pay': 'auto'}).ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        result = session.act('pilot:A', {'action_id': offered['id'], 'pay': 'auto'})
        self.assertTrue(result.ok, result.summary)

    def test_original_emblem_parents_and_children_have_complete_bindings(self):
        from quorune.card_programs import bind_card_program_runtime
        from quorune.card_programs.adapters import compile_best_available_card_program
        from quorune.oracle_ir import register_generated_programs
        from quorune.semantics import SemanticRegistry
        semantics = SemanticRegistry()
        for name in ('Huatli, Radiant Champion', 'Gideon, Ally of Zendikar', 'Ajani, Adversary of Tyrants'):
            with self.subTest(name=name):
                card = self.db.lookup(name)
                register_generated_programs(self.db, semantics, (card,), trust_level='provisional',
                    capability_registry=self.registry, capability_profile='commander_review',
                    promote_exact_runtime_handlers=True, promote_exact_trigger_programs=True,
                    promote_exact_effect_programs=True, promote_exact_capability_declarations=True)
                program = compile_best_available_card_program(self.db, card, semantic_registry=semantics,
                    capability_registry=self.registry, capability_profile='commander_review')
                binding = bind_card_program_runtime(program, capability_registry=self.registry, profile='commander_review')
                self.assertTrue(binding['strict_capability_ready'], {'card': name, 'blockers': binding['blockers']})

    def test_original_huatli_emblem_triggers_after_creator_dies_and_replays_private_draw(self):
        session = self.session(402140)
        creator = self.add(session.engine, 'Huatli, Radiant Champion', ref='actual-huatli-emblem-creator')
        creator.counters['loyalty'] = 8
        entrant = self.add(session.engine, 'Generic Optional Ground', zone='hand', ref='actual-emblem-entrant')
        private = self.add(session.engine, 'Generic Bound Growth', seat='D', zone='hand', ref='emblem-private-card')
        self.assert_closed(session, creator)
        self.prepare(session)
        self.checkpoint(session)
        self.activate(session, creator, 'ab3')
        self.assertEqual('graveyard', session.state.cards[creator.object_id].zone)
        self.resolve(session)
        emblem, = [card for card in session.state.cards.values() if card.object_kind == 'emblem']
        self.assertEqual(('A', 'A', 'command'), (emblem.owner, emblem.controller, emblem.zone))
        session = self.replay(session, load=True)
        before = len(session.state.players['A'].draw_history)
        self.cast(session, 'A', entrant)
        decision = self.resolve(session)
        self.assertIsNotNone(decision, 'The command-zone emblem did not offer its draw')
        self.assertEqual('semantic.choice', decision.kind)
        self.assertEqual(emblem.object_id, session.state.stack[-1].source_object_id)
        session = self.replay(session, load=True)
        self.choose(session, choice='apply')
        self.resolve(session)
        self.assertEqual(before + 1, len(session.state.players['A'].draw_history))
        self.assertEqual('graveyard', session.state.cards[creator.object_id].zone)
        for viewer in 'ABC':
            self.assertNotIn(private.ref, json.dumps(session.packet('pilot:' + viewer, full=True)))
        self.replay(session, load=True)

    def test_command_program_provider_omission_is_killed_by_original_huatli(self):
        from unittest.mock import patch
        with patch('quorune.emblem_programs.command_emblem_programs', return_value=()):
            with self.assertRaises(AssertionError):
                self.test_original_huatli_emblem_triggers_after_creator_dies_and_replays_private_draw()

    def test_original_koth_emblem_is_a_colorless_command_damage_source_after_creator_dies(self):
        session = self.session(402145)
        creator = self.add(session.engine, 'Koth, Fire of Resistance')
        creator.counters['loyalty'] = 7
        mountain = self.add(session.engine, 'Mountain', zone='hand', ref='emblem-triggering-mountain')
        protected = self.add(session.engine, 'Silver Knight', seat='B', ref='red-protected-emblem-target')
        self.assert_closed(session, creator)
        self.assert_closed(session, protected)
        self.prepare(session)
        self.checkpoint(session)
        self.activate(session, creator, 'ab3')
        self.resolve(session)
        self.assertEqual('graveyard', session.state.cards[creator.object_id].zone)
        emblem, = [c for c in session.state.cards.values() if c.object_kind == 'emblem']
        self.assertEqual([], session.engine._effective_card_data(emblem)['colors'])
        rows = session.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']
        offered, = [row for row in rows if mountain.ref in row['id']]
        accepted = session.act('pilot:A', {'action_id': offered['id']})
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertEqual('semantic.target', session.state.pending_decision.kind)
        self.assertEqual(emblem.object_id, session.state.stack[-1].source_object_id)
        schema = session.packet('pilot:A', full=True)['decision']['ctx']['target_schema']
        self.assertIn(protected.ref, schema['legal_refs'])
        before = authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:B', {'action_id': 'choose', 'targets': [protected.ref]}).ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        session = self.replay(session, load=True)
        self.choose(session, targets=[protected.ref])
        self.resolve(session)
        self.assertEqual('graveyard', session.state.cards[protected.object_id].zone)
        self.replay(session, load=True)

    def test_original_gideon_emblems_stack_after_bounce_and_keep_owner_after_creator_control_change(self):
        session = self.session(402141)
        creator = self.add(session.engine, 'Gideon, Ally of Zendikar', ref='actual-gideon-emblem-creator')
        creator.counters['loyalty'] = 5
        creature = self.add(session.engine, 'Generic Optional Ground', ref='friendly-emblem-recipient')
        foreign = self.add(session.engine, 'Generic Optional Ground', seat='B', ref='foreign-emblem-recipient')
        bounce = self.add(session.engine, 'Generic Optional Bounce', zone='hand')
        control = self.add(session.engine, 'Confiscate', seat='B', zone='hand')
        flash = self.add(session.engine, 'Vedalken Orrery', seat='B')
        self.assert_closed(session, creator)
        self.assert_closed(session, control)
        self.assert_closed(session, flash)
        self.prepare(session, mana={'W': 4, 'U': 1})
        session.state.players['B'].mana_pool.update(U=6)
        self.checkpoint(session)
        self.activate(session, creator, 'ab3')
        self.cast(session, 'B', control, [creator.ref])
        self.resolve(session)
        self.assertEqual('B', session.state.cards[creator.object_id].controller)
        self.assertEqual(3, session.engine._numeric_stat(creature.object_id, 'power'))
        self.assertEqual(2, session.engine._numeric_stat(foreign.object_id, 'power'))
        self.cast(session, 'A', bounce, [creator.ref])
        self.resolve(session)
        self.assertEqual('hand', session.state.cards[creator.object_id].zone)
        self.assertEqual(3, session.engine._numeric_stat(creature.object_id, 'power'))
        self.cast(session, 'A', creator)
        self.resolve(session)
        self.activate(session, creator, 'ab3')
        self.resolve(session)
        emblems = [card for card in session.state.cards.values() if card.object_kind == 'emblem']
        self.assertEqual(2, len(emblems))
        self.assertEqual(2, len({card.logical_object_id for card in emblems}))
        self.assertEqual((4, 8), tuple(session.engine._numeric_stat(creature.object_id, stat) for stat in ('power', 'toughness')))
        self.assertEqual((2, 6), tuple(session.engine._numeric_stat(foreign.object_id, stat) for stat in ('power', 'toughness')))
        self.assertEqual('graveyard', session.state.cards[creator.object_id].zone)
        session = self.replay(session, load=True)
        self.assertEqual(4, session.engine._numeric_stat(creature.object_id, 'power'))
        self.assertEqual(2, session.engine._numeric_stat(foreign.object_id, 'power'))
        self.replay(session, load=True)

    def test_original_gideon_zero_loyalty_uses_the_same_permanent_limit_without_changing_counters(self):
        session = self.session(402142)
        creator = self.add(session.engine, 'Gideon, Ally of Zendikar')
        creator.counters['loyalty'] = 4
        self.assert_closed(session, creator)
        self.prepare(session)
        self.checkpoint(session)
        self.activate(session, creator, 'ab2')
        self.resolve(session)
        self.assertEqual(4, session.state.cards[creator.object_id].counters['loyalty'])
        tokens = [c for c in session.state.cards.values() if c.is_token and c.zone == 'battlefield']
        self.assertEqual(1, len(tokens))
        self.assertEqual((2, 2), tuple(session.engine._numeric_stat(tokens[0].object_id, stat) for stat in ('power', 'toughness')))
        rows = session.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']
        self.assertFalse(any(row['id'].startswith('activate:' + creator.ref + ':') for row in rows))
        before = authoritative_state_hash(session.state)
        rejected = session.act('pilot:A', {'action_id': 'activate:' + creator.ref + ':ab2', 'pay': 'auto'})
        self.assertFalse(rejected.ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        self.replay(session, load=True)

    def test_source_animation_and_shield_do_not_follow_departed_creator(self):
        session = self.session(402144)
        creator = self.add(session.engine, 'Gideon, Ally of Zendikar')
        creator.counters['loyalty'] = 4
        bounce = self.add(session.engine, 'Generic Optional Bounce', zone='hand')
        self.assert_closed(session, creator)
        self.prepare(session, mana={'U': 1})
        self.checkpoint(session)
        self.activate(session, creator, 'ab1')
        self.cast(session, 'A', bounce, [creator.ref])
        self.resolve(session)
        self.assertEqual('hand', session.state.cards[creator.object_id].zone)
        self.assertFalse(session.state.damage_prevention_shields)
        self.replay(session, load=True)

    def test_original_gideon_animation_prevents_incoming_damage_but_deals_its_own_combat_damage(self):
        session = self.session(402143)
        creator = self.add(session.engine, 'Gideon, Ally of Zendikar')
        creator.counters['loyalty'] = 4
        creator.acquired_control_turn_count = -1
        blocker = self.add(session.engine, 'Generic Optional Ground', seat='B', ref='gideon-animation-blocker')
        bolt = self.add(session.engine, 'Lightning Bolt', seat='B', zone='hand')
        self.assert_closed(session, creator)
        self.prepare(session)
        session.state.players['B'].mana_pool['R'] = 1
        self.checkpoint(session)
        self.activate(session, creator, 'ab1')
        self.resolve(session)
        self.assertEqual((5, 5), tuple(session.engine._numeric_stat(creator.object_id, stat) for stat in ('power', 'toughness')))
        types, subtypes, _ = session.engine._type_parts(session.engine._effective_card_data(session.state.cards[creator.object_id])['type_line'])
        self.assertTrue({'creature', 'planeswalker'} <= set(types))
        self.assertTrue({'gideon', 'human', 'soldier', 'ally'} <= set(subtypes))
        self.cast(session, 'B', bolt, [creator.ref])
        self.resolve(session)
        self.assertEqual(0, session.state.cards[creator.object_id].marked_damage)
        self.assertEqual(5, session.state.cards[creator.object_id].counters['loyalty'])
        for _ in range(24):
            if session.state.pending_decision.kind == 'combat.attackers':
                break
            self.assertTrue(session.act(session.pending_principals()[0], {'action_id': 'pass'}).ok)
        else:
            self.fail('The animated planeswalker did not reach its real combat')
        accepted = session.act('pilot:A', {'action_id': 'attack', 'attackers': {creator.ref: 'B'}})
        self.assertTrue(accepted.ok, accepted.summary)
        for _ in range(16):
            if session.state.pending_decision.kind == 'combat.blockers':
                break
            self.assertTrue(session.act(session.pending_principals()[0], {'action_id': 'pass'}).ok)
        accepted = session.act('pilot:B', {'action_id': 'block', 'blocks': {blocker.ref: creator.ref}})
        self.assertTrue(accepted.ok, accepted.summary)
        for _ in range(24):
            if session.state.cards[blocker.object_id].marked_damage == 5:
                break
            if session.state.pending_decision.kind == 'combat.damage':
                result = session.act(session.pending_principals()[0], {'action_id': 'assign_damage',
                    'assignments': [{'source': creator.ref, 'target': blocker.ref, 'amount': 5}]})
            else:
                result = session.act(session.pending_principals()[0], {'action_id': 'pass'})
            self.assertTrue(result.ok, result.summary)
        self.assertEqual(5, session.state.cards[blocker.object_id].marked_damage)
        self.assertEqual(0, session.state.cards[creator.object_id].marked_damage)
        self.assertEqual(5, session.state.cards[creator.object_id].counters['loyalty'])
        self.replay(session, load=True)
