from __future__ import annotations

import json
import unittest
from dataclasses import replace
from pathlib import Path
import tempfile
from unittest.mock import patch

from common import ROOT
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
import test_paid_graveyard_casts as controls
import test_provoke_rules as combat_controls

from quorune.object_predicate import ObjectQueryError, ObjectQuerySpec, object_query_capabilities
from quorune.object_query import ObjectQueryResult, query_objects
from quorune.object_query_union import RECEIVER_UNION_CAPABILITY, bind_receiver_union_source
from quorune.compiler.public_state_queries import fixed_characteristic_battlefield_query_subject
from quorune.rules.evidence_storage import read_evidence_document


class ReceiverQueryUnionModelTests(unittest.TestCase):
    def query(self):
        return ObjectQuerySpec(zones=('battlefield',), any_of=(
            ObjectQuerySpec(zones=('battlefield',), subtypes_all=('skeleton',)),
            ObjectQuerySpec(zones=('battlefield',), subtypes_all=('zombie',), exclude_ref='$source'),
        ))

    def row(self, ref, *, subtypes=(), controller='A'):
        return ObjectQueryResult(object_id=ref, ref=ref, printed_name='Generic public object',
            owner='A', controller=controller, zone='battlefield', types=('creature',),
            subtypes=subtypes, supertypes=(), colors=(), keywords=())

    def test_overlap_is_one_member_and_other_is_branch_local(self):
        query = replace(bind_receiver_union_source(self.query(), 'source'), controller='A')
        rows = (self.row('source', subtypes=('zombie',)),
            self.row('overlap', subtypes=('skeleton', 'zombie')),
            self.row('skeleton', subtypes=('skeleton',)), self.row('zombie', subtypes=('zombie',)),
            self.row('opponent', subtypes=('skeleton',), controller='B'))
        self.assertEqual(('overlap', 'skeleton', 'zombie'), tuple(r.ref for r in query_objects(rows, query)))
        changed_source = replace(rows[0], subtypes=('skeleton', 'zombie'))
        self.assertEqual((changed_source,), query_objects((changed_source,), query))
        self.assertEqual((), query_objects((changed_source,), replace(query, exclude_ref='source')))
        kindred = replace(rows[1], types=('kindred', 'enchantment'))
        self.assertEqual((kindred,), query_objects((kindred,), query))
        three = replace(query, any_of=(*query.any_of,
            ObjectQuerySpec(zones=('battlefield',), types_all=('creature',), colors_all=('G',))))
        self.assertEqual((rows[1],), query_objects((rows[1],), three))

    def test_wire_round_trip_and_old_versions_keep_exact_fields(self):
        query = self.query()
        wire = json.loads(json.dumps(query.to_dict()))
        self.assertEqual(query, ObjectQuerySpec.from_dict(wire))
        self.assertEqual(wire, ObjectQuerySpec.from_dict(wire).to_dict())
        self.assertEqual((RECEIVER_UNION_CAPABILITY,), object_query_capabilities(wire))
        self.assertEqual((RECEIVER_UNION_CAPABILITY,), object_query_capabilities(query))
        for version in range(1, 10):
            old = ObjectQuerySpec(zones=('battlefield',), types_all=('creature',), _serialization_version=version).to_dict()
            self.assertNotIn('any_of', old)
            self.assertEqual(old, ObjectQuerySpec.from_dict(old).to_dict())

    def test_unions_reject_recursive_hidden_late_or_unbounded_queries(self):
        query = self.query()
        for branches in ((), [query], [query, query.any_of[0]], [query.any_of[0]] * 5):
            wire = {**query.to_dict(), 'any_of': [part.to_dict() for part in branches]}
            with self.assertRaises(ObjectQueryError):
                ObjectQuerySpec.from_dict(wire)
        for field, value in (('zones', ('hand',)), ('known_to_actor', True),
                ('keywords_all', ('flying',)), ('tapped', True), ('any_of', query.any_of)):
            bad = replace(query.any_of[0], **{field: value}) if field != 'any_of' else query
            with self.assertRaises(ObjectQueryError):
                replace(query, any_of=(bad, query.any_of[1]))
        for value in ({}, True, None, [], 'not a list'):
            with self.assertRaises(ObjectQueryError):
                ObjectQuerySpec.from_dict({**query.to_dict(), 'any_of': value})
        with self.assertRaises(ObjectQueryError):
            replace(query, types_all=('creature',))
        from quorune.compiler.fixed_resolution_characteristic_queries import fixed_resolution_characteristic_query_is_closed
        from quorune.characteristic_fragments import CharacteristicQuantitySpec, CharacteristicQuantityScope, CharacteristicFragmentError
        self.assertFalse(fixed_resolution_characteristic_query_is_closed(query, target_schema=None))
        with self.assertRaises(CharacteristicFragmentError):
            CharacteristicQuantitySpec(scope=CharacteristicQuantityScope.CONTROLLER_ZONE, query=query)
        from quorune.semantic_runtime.conditional_continuous import _validate_target
        with self.assertRaises(ValueError):
            _validate_target({'kind': 'fixed_query', 'target_controller': 'source_controller',
                'predicate': query.to_dict(), 'exclude_source': False, 'types_all': []})

    def test_union_surface_preserves_scope_and_unsupported_antecedents(self):
        cases = (
            ('Other Treefolk and Forests you control', 'source_controller', True),
            ('Goblins you control and Elementals you control', 'source_controller', False),
            ('Skeletons you control and other Zombies you control', 'source_controller', False),
            ('Other snow and Zombie creatures you control', 'source_controller', True),
            ('Green creatures and white creatures', 'any', False),
        )
        for text, relation, other in cases:
            parsed = fixed_characteristic_battlefield_query_subject(text)
            self.assertIsNotNone(parsed, text)
            self.assertEqual((relation, other), (parsed[0], parsed[2]))
            self.assertEqual(2, len(parsed[1].any_of))
        death = fixed_characteristic_battlefield_query_subject(cases[2][0])[1]
        self.assertEqual((None, '$source'), tuple(part.exclude_ref for part in death.any_of))
        for text in ('Creatures with flying and creatures with vigilance',
                'Creatures in your hand and Zombies you control',
                'Creatures with power 3 or greater and Zombies you control',
                'Goblins you control and Elementals your opponents control',
                'Snow and Zombies you control', 'Goblins and Goblins you control'):
            self.assertIsNone(fixed_characteristic_battlefield_query_subject(text), text)


NAMES = ('Timber Protector', 'Caterwauling Boggart', 'Death Baron', 'Narfi, Betrayer King', 'Masked Gorgon')


class ReceiverQueryUnionRuntimeTests(unittest.TestCase):
    register_fixture_cards_on_add = True
    session = controls.PaidGraveyardRuntimeTests.session
    add = controls.PaidGraveyardRuntimeTests.add
    prepare = controls.PaidGraveyardRuntimeTests.prepare
    checkpoint = controls.PaidGraveyardRuntimeTests.checkpoint
    resolve = controls.PaidGraveyardRuntimeTests.resolve
    replay = controls.PaidGraveyardRuntimeTests.replay
    binding = controls.PaidGraveyardRuntimeTests.binding
    priority = controls.PaidGraveyardRuntimeTests.priority
    private_replay = controls.PaidGraveyardRuntimeTests.private_replay
    advance = combat_controls.ProvokeTests.advance
    attack = combat_controls.ProvokeTests.attack
    blockers = combat_controls.ProvokeTests.blockers
    block = combat_controls.ProvokeTests.block

    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        path = Path(cls.temp.name) / 'receiver-unions.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',
            ROOT/'tests/fixtures/union-receiver-cards.json'], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Receiver union witnesses', [DeckEntry('Generic Bound Commander', 1, 'commander'),
            DeckEntry('Generic Bound Plains', 30)], ['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temp.cleanup()

    def cast(self, s, card, targets=(), *, seat='A', payment=None):
        self.priority(s, seat)
        actions = s.packet('pilot:'+seat, full=True)['decision']['ctx']['legal']['actions']
        row = next((row for row in actions if row['id'] == 'cast:'+card.ref), None)
        self.assertIsNotNone(row, card.printed_name)
        command = {'action_id': row['id'], 'targets': list(targets), 'pay': 'auto'}
        if payment is not None:
            command.update(pay='manual', payment=payment)
        before = authoritative_state_hash(s.state)
        denied = s.act('pilot:D', command)
        self.assertFalse(denied.ok)
        self.assertEqual(before, authoritative_state_hash(s.state))
        accepted = s.act('pilot:'+seat, command)
        self.assertTrue(accepted.ok, accepted.summary)

    def stats(self, s, card):
        return tuple(s.engine._numeric_stat(card.object_id, stat) for stat in ('power', 'toughness'))

    def test_five_originals_have_complete_admission_and_union_runtime_minimum(self):
        from quorune.card_programs.binding import bind_semantic_program_runtime
        from quorune.oracle_ir import compile_oracle_card
        for index, name in enumerate(NAMES):
            s = self.session(751100+index)
            source = self.add(s.engine, name, zone='hand')
            self.assertTrue(self.binding(s, source)['strict_capability_ready'], name)
            ir = compile_oracle_card(self.db.lookup(name), capability_registry=self.registry,
                capability_profile='commander_review')
            self.assertEqual('exact', ir.status, (name, ir.material_residuals))
            program = next(p for p in s.engine.semantics.programs_for_oracle(source.oracle_id)
                if RECEIVER_UNION_CAPABILITY in p.capability_dependencies)
            deps = tuple(cap for cap in program.capability_dependencies if cap != RECEIVER_UNION_CAPABILITY)
            missing = replace(program, capability_dependencies=deps,
                capability_closure=self.registry.closure(deps, profile='commander_review').to_dict())
            bound = bind_semantic_program_runtime(missing, capability_registry=self.registry, profile='commander_review')
            self.assertFalse(bound['strict'])
            self.assertIn('capability:undeclared_runtime_dependency:'+RECEIVER_UNION_CAPABILITY, bound['blockers'])

    def test_original_death_baron_cast_grants_overlap_once_and_deathtouch_kills(self):
        s = self.session(751110)
        source = self.add(s.engine, 'Death Baron', zone='hand')
        overlap = self.add(s.engine, 'Generic Union Skeleton Zombie')
        foreign = self.add(s.engine, 'Generic Union Skeleton Zombie', seat='B', ref='foreign-overlap')
        victim = self.add(s.engine, 'Generic Bound Body', seat='B', ref='deathtouch-victim')
        fight = self.add(s.engine, 'Generic Union Fight', zone='hand')
        private = self.add(s.engine, 'Generic Bound Growth', seat='C', zone='hand')
        self.prepare(s, mana={'C': 1, 'B': 2, 'G': 1})
        self.checkpoint(s)
        self.cast(s, source, payment={'C': 1, 'B': 2})
        s = self.replay(s, load=True)
        self.assertIsNone(self.resolve(s))
        self.assertEqual((3, 3), self.stats(s, overlap))
        self.assertEqual((2, 2), self.stats(s, foreign))
        self.assertEqual((2, 2), self.stats(s, source))
        self.assertIn('deathtouch', s.engine._combat_keywords(s.state.cards[overlap.object_id]))
        self.cast(s, fight, [overlap.ref, victim.ref])
        self.assertIsNone(self.resolve(s))
        self.assertEqual('graveyard', s.state.cards[victim.object_id].zone)
        self.assertEqual('battlefield', s.state.cards[overlap.object_id].zone)
        self.private_replay(s, private)

    def test_original_timber_cast_protects_forests_and_retains_other_treefolk_stats(self):
        s = self.session(751111)
        source = self.add(s.engine, 'Timber Protector', zone='hand')
        tree = self.add(s.engine, 'Generic Union Forest Treefolk')
        forest = self.add(s.engine, 'Forest')
        foreign = self.add(s.engine, 'Forest', seat='B', ref='foreign-forest')
        murder = self.add(s.engine, 'Murder', zone='hand')
        private = self.add(s.engine, 'Generic Bound Growth', seat='C', zone='hand')
        self.prepare(s, mana={'C': 7, 'G': 1, 'B': 4})
        self.checkpoint(s)
        self.cast(s, source)
        self.assertIsNone(self.resolve(s))
        self.assertEqual((3, 5), self.stats(s, tree))
        self.assertIn('indestructible', s.engine._combat_keywords(s.state.cards[forest.object_id]))
        self.assertNotIn('indestructible', s.engine._combat_keywords(s.state.cards[foreign.object_id]))
        self.assertNotIn('indestructible', s.engine._combat_keywords(s.state.cards[source.object_id]))
        self.cast(s, murder, [tree.ref])
        self.assertIsNone(self.resolve(s))
        self.assertEqual('battlefield', s.state.cards[tree.object_id].zone)
        bounce = self.add(s.engine, 'Unsummon', zone='hand')
        # A new fixture boundary starts before the source-departure action.
        self.prepare(s, mana={'U': 1})
        self.checkpoint(s)
        self.cast(s, bounce, [source.ref])
        self.assertIsNone(self.resolve(s))
        self.assertEqual((2, 4), self.stats(s, tree))
        self.assertNotIn('indestructible', s.engine._combat_keywords(s.state.cards[forest.object_id]))
        self.private_replay(s, private)

    def test_original_boggart_cast_includes_self_and_overlap_has_two_blocker_menace(self):
        from quorune.menace import current_menace_restriction
        s = self.session(751112)
        source = self.add(s.engine, 'Caterwauling Boggart', zone='hand')
        overlap = self.add(s.engine, 'Generic Union Goblin Elemental')
        overlap.acquired_control_turn_count = -1
        foreign = self.add(s.engine, 'Generic Union Goblin Elemental', seat='B', ref='foreign-goblin-elemental')
        second = self.add(s.engine, 'Generic Bound Body', seat='B', ref='second-boggart-blocker')
        private = self.add(s.engine, 'Generic Bound Growth', seat='C', zone='hand')
        self.prepare(s, mana={'C': 3, 'R': 1})
        self.checkpoint(s)
        self.cast(s, source)
        self.assertIsNone(self.resolve(s))
        for card in (source, overlap):
            current = s.state.cards[card.object_id]
            self.assertIn('menace', s.engine._combat_keywords(current))
            restriction = current_menace_restriction(s.engine._effective_card_data(current), current.ref, is_attacking=True)
            self.assertIsNotNone(restriction)
            self.assertEqual(2, restriction.minimum_blockers)
        self.assertNotIn('menace', s.engine._combat_keywords(s.state.cards[foreign.object_id]))
        self.attack(s, {overlap.ref: 'B'})
        self.blockers(s)
        self.block(s, {foreign.ref: [overlap.ref]}, valid=False)
        s = self.replay(s, load=True)
        self.block(s, {foreign.ref: [overlap.ref], second.ref: [overlap.ref]})
        self.advance(s, lambda current: current.state.pending_decision is not None
            and current.state.pending_decision.kind == 'combat.damage')
        accepted = s.act('pilot:A', {'action_id': 'assign_damage', 'assignments': [
            {'source': overlap.ref, 'target': foreign.ref, 'amount': 2}]})
        self.assertTrue(accepted.ok, accepted.summary)
        self.advance(s, lambda current: current.state.phase == 'postcombat_main')
        self.assertEqual(40, s.state.players['B'].life)
        self.assertEqual('graveyard', s.state.cards[overlap.object_id].zone)
        self.private_replay(s, private)

    def test_original_narfi_cast_buffs_snow_or_zombie_once_and_excludes_self(self):
        s = self.session(751113)
        source = self.add(s.engine, 'Narfi, Betrayer King', zone='hand')
        overlap = self.add(s.engine, 'Generic Union Snow Zombie')
        snow = self.add(s.engine, 'Generic Union Snow Bear')
        zombie = self.add(s.engine, 'Generic Union Skeleton Zombie')
        land = self.add(s.engine, 'Snow-Covered Forest')
        foreign = self.add(s.engine, 'Generic Union Snow Zombie', seat='B', ref='foreign-snow-zombie')
        private = self.add(s.engine, 'Generic Bound Growth', seat='C', zone='hand')
        self.prepare(s, mana={'C': 3, 'U': 1, 'B': 1})
        self.checkpoint(s)
        self.cast(s, source)
        self.assertIsNone(self.resolve(s))
        for card in (overlap, snow, zombie):
            self.assertEqual((3, 3), self.stats(s, card))
        self.assertEqual((4, 3), self.stats(s, source))
        self.assertEqual((2, 2), self.stats(s, foreign))
        self.assertNotIn('creature', s.engine._type_parts(s.engine._effective_card_data(s.state.cards[land.object_id])['type_line'])[0])
        self.assertEqual(0, s.engine._numeric_stat(land.object_id, 'power'))
        self.private_replay(s, private)

    def test_original_masked_gorgon_cast_grants_global_protection_and_keeps_threshold(self):
        from quorune.ability_fragments import ProtectionSpec
        s = self.session(751114)
        source = self.add(s.engine, 'Masked Gorgon', zone='hand')
        green = self.add(s.engine, 'Grizzly Bears', seat='B')
        green.acquired_control_turn_count = -1
        white = self.add(s.engine, 'Generic Bound Body', seat='C')
        overlap = self.add(s.engine, 'Generic Union Green White', seat='D')
        black = self.add(s.engine, 'Generic Union Skeleton Zombie')
        private = self.add(s.engine, 'Generic Bound Growth', seat='C', zone='hand')
        for i in range(7):
            self.add(s.engine, 'Generic Bound Plains', zone='graveyard', ref='threshold-'+str(i))
        self.prepare(s, mana={'C': 4, 'B': 1})
        self.checkpoint(s)
        self.cast(s, source)
        self.assertIsNone(self.resolve(s))
        for card in (green, white, overlap):
            fragments = [f for f in s.engine._effective_ability_fragments(s.state.cards[card.object_id]) if isinstance(f, ProtectionSpec)]
            self.assertEqual(1, len(fragments))
            self.assertEqual(('gorgon',), fragments[0].source_predicate.subtypes_all)
        self.assertNotIn('protection', s.engine._combat_keywords(s.state.cards[black.object_id]))
        self.assertIn('protection', s.engine._combat_keywords(s.state.cards[source.object_id]))
        self.assertFalse(s.engine._can_block(s.state.cards[green.object_id], s.state.cards[source.object_id])[0])
        self.assertTrue(s.engine._can_block(s.state.cards[black.object_id], s.state.cards[source.object_id])[0])
        from quorune.continuous_effect_model import ContinuousOperation, Layer
        from quorune.continuous_effect_state import ResolutionEffectSource, ResolutionContinuousComponent, create_resolution_continuous_effect_components
        create_resolution_continuous_effect_components(s.engine, source=ResolutionEffectSource(stack_ref='union-color-boundary'),
            targets=(overlap,), components=(ResolutionContinuousComponent(layer=Layer.COLOR, sublayer='5',
                operations=(ContinuousOperation('set_colors', ['B']),)),))
        self.assertNotIn('protection', s.engine._combat_keywords(s.state.cards[overlap.object_id]))
        create_resolution_continuous_effect_components(s.engine, source=ResolutionEffectSource(stack_ref='union-color-return'),
            targets=(overlap,), components=(ResolutionContinuousComponent(layer=Layer.COLOR, sublayer='5',
                operations=(ContinuousOperation('set_colors', ['G', 'W']),)),))
        self.assertEqual(1, len([f for f in s.engine._effective_ability_fragments(s.state.cards[overlap.object_id]) if isinstance(f, ProtectionSpec)]))
        # Start an explicit B-turn combat boundary after the earlier-layer controls.
        self.prepare(s, actor='B', active='B')
        self.checkpoint(s)
        self.advance(s, lambda current: current.state.pending_decision is not None
            and current.state.pending_decision.kind == 'combat.attackers')
        accepted = s.act('pilot:B', {'action_id': 'attack', 'attacks': {green.ref: 'A'}})
        self.assertTrue(accepted.ok, accepted.summary)
        self.blockers(s)
        before = authoritative_state_hash(s.state)
        denied = s.act('pilot:A', {'action_id': 'block', 'blocks': {source.ref: [green.ref]}})
        self.assertFalse(denied.ok)
        self.assertEqual(before, authoritative_state_hash(s.state))
        accepted = s.act('pilot:A', {'action_id': 'block', 'blocks': {}})
        self.assertTrue(accepted.ok, accepted.summary)
        self.advance(s, lambda current: current.state.phase == 'postcombat_main')
        self.assertEqual(38, s.state.players['A'].life)
        self.private_replay(s, private)

    def test_death_baron_other_is_local_after_source_becomes_a_skeleton(self):
        from quorune.continuous_effect_model import ContinuousOperation, Layer
        from quorune.continuous_effect_state import ResolutionEffectSource, ResolutionContinuousComponent, create_resolution_continuous_effect_components
        s = self.session(751115)
        source = self.add(s.engine, 'Death Baron')
        recipient = self.add(s.engine, 'Generic Union Skeleton Zombie')
        create_resolution_continuous_effect_components(s.engine, source=ResolutionEffectSource(stack_ref='union-type-boundary'),
            targets=(source,), components=(ResolutionContinuousComponent(layer=Layer.TYPE, sublayer='4',
                operations=(ContinuousOperation('add_types', ['Skeleton'], 'subtypes'),)),))
        self.assertEqual((3, 3), self.stats(s, source))
        self.assertIn('deathtouch', s.engine._combat_keywords(source))
        second = self.add(s.engine, 'Death Baron', ref='second-independent-baron')
        self.assertEqual((4, 4), self.stats(s, recipient))
        second.phased_out = True
        self.assertEqual((3, 3), self.stats(s, recipient))
        source.phased_out = True
        self.assertEqual((2, 2), self.stats(s, recipient))
        source.phased_out = False
        self.assertEqual((3, 3), self.stats(s, recipient))
        recipient.phased_out = True
        self.assertEqual((2, 2), self.stats(s, recipient))
        recipient.phased_out = False
        create_resolution_continuous_effect_components(s.engine, source=ResolutionEffectSource(stack_ref='union-type-removal'),
            targets=(recipient,), components=(ResolutionContinuousComponent(layer=Layer.TYPE, sublayer='4',
                operations=(ContinuousOperation('set_types', ['Bear'], 'subtypes'),)),))
        self.assertEqual((2, 2), self.stats(s, recipient))

    def test_actual_humble_removes_union_grants_and_control_rebinds_recipients(self):
        s = self.session(751116)
        source = self.add(s.engine, 'Death Baron')
        own = self.add(s.engine, 'Generic Union Skeleton Zombie')
        foreign = self.add(s.engine, 'Generic Union Skeleton Zombie', seat='B', ref='control-zombie')
        control = self.add(s.engine, 'Control Magic', seat='B', zone='hand')
        humble = self.add(s.engine, 'Humble', seat='B', zone='hand')
        private = self.add(s.engine, 'Generic Bound Growth', seat='C', zone='hand')
        self.prepare(s, actor='B', active='B', mana={'C': 3, 'U': 2, 'W': 1})
        self.checkpoint(s)
        self.assertEqual((3, 3), self.stats(s, own))
        self.cast(s, control, [source.ref], seat='B')
        self.assertIsNone(self.resolve(s))
        self.assertEqual('B', s.state.cards[source.object_id].controller)
        self.assertEqual((2, 2), self.stats(s, own))
        self.assertEqual((3, 3), self.stats(s, foreign))
        self.cast(s, humble, [source.ref], seat='B')
        s = self.replay(s, load=True)
        self.assertIsNone(self.resolve(s))
        self.assertEqual((2, 2), self.stats(s, foreign))
        self.assertNotIn('deathtouch', s.engine._combat_keywords(s.state.cards[foreign.object_id]))
        self.private_replay(s, private)

    def test_original_narfi_snow_return_pays_three_real_snow_sources(self):
        s = self.session(751117)
        source = self.add(s.engine, 'Narfi, Betrayer King', zone='graveyard')
        overlap = self.add(s.engine, 'Generic Union Snow Zombie')
        lands = [self.add(s.engine, 'Snow-Covered Forest', ref='narfi-snow-'+str(i)) for i in range(3)]
        private = self.add(s.engine, 'Generic Bound Growth', seat='C', zone='hand')
        self.prepare(s)
        self.checkpoint(s)
        for land in lands:
            actions = s.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']
            row = next(row for row in actions if row['id'].startswith('activate:'+land.ref+':'))
            result = s.act('pilot:A', {'action_id': row['id'], 'pay': 'auto'})
            self.assertTrue(result.ok, result.summary)
        actions = s.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']
        row = next((row for row in actions if row['id'].startswith('activate:'+source.ref+':')), None)
        self.assertIsNotNone(row)
        before = authoritative_state_hash(s.state)
        self.assertFalse(s.act('pilot:D', {'action_id': row['id'], 'pay': 'auto'}).ok)
        self.assertEqual(before, authoritative_state_hash(s.state))
        accepted = s.act('pilot:A', {'action_id': row['id'], 'pay': 'auto'})
        self.assertTrue(accepted.ok, accepted.summary)
        s = self.replay(s, load=True)
        self.assertIsNone(self.resolve(s))
        self.assertEqual('battlefield', s.state.cards[source.object_id].zone)
        self.assertTrue(s.state.cards[source.object_id].tapped)
        self.assertTrue(all(s.state.cards[land.object_id].tapped for land in lands))
        self.assertEqual(0, sum(s.state.players['A'].mana_pool.values()))
        self.assertEqual((3, 3), self.stats(s, overlap))
        self.private_replay(s, private)

    def test_canonically_verified_union_withdrawal_blocks_all_originals(self):
        from copy import deepcopy
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from scripts.update_capability_evidence import build_index, discover_tests
        from quorune.oracle_ir import compile_oracle_card
        raw = json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        declarations = read_evidence_document(ROOT/'platform/capability-evidence-declarations.json')
        value = deepcopy(raw)
        next(c for c in value['capabilities'] if c['id'] == RECEIVER_UNION_CAPABILITY).update(status='blocked', blockers=['withdrawn receiver union'])
        registry = CapabilityRegistry(value)
        index = build_index(registry_value=value, declaration_source=declarations,
            discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
        for name in NAMES:
            ir = compile_oracle_card(self.db.lookup(name), capability_registry=registry, capability_profile='commander_review')
            self.assertNotEqual('exact', ir.status, name)
            blockers = [b for residual in ir.material_residuals for b in residual.blockers]
            self.assertTrue(any('status:'+RECEIVER_UNION_CAPABILITY+':blocked' in b for b in blockers), blockers)
            self.assertFalse(any('evidence_index:unverified' in b for b in blockers), blockers)

    def test_handler_rejects_hidden_recursive_and_foreign_source_union_branches(self):
        from quorune.compiler.continuous_templates import fixed_query_characteristic_grant_handler
        from quorune.semantic_runtime.continuous_components import default_continuous_effect_component_registry
        descriptor = fixed_query_characteristic_grant_handler(self.db.lookup('Death Baron').oracle_text)[1]
        registry = default_continuous_effect_component_registry()
        registry.validate(descriptor)
        for changes in ({'exclude_ref': 'arbitrary-card'}, {'known_to_actor': True}, {'keywords_all': ['flying']},
                {'zones': ['hand']}, {'any_of': descriptor['condition']['predicate']['any_of']}):
            leaf = {**descriptor['condition']['predicate']['any_of'][0], **changes}
            predicate = {**descriptor['condition']['predicate'], 'any_of': [leaf, descriptor['condition']['predicate']['any_of'][1]]}
            with self.assertRaises(ValueError):
                registry.validate({**descriptor, 'condition': {**descriptor['condition'], 'predicate': predicate}})

    def test_fresh_original_witnesses_kill_union_producer_and_matcher_omissions(self):
        method = 'test_five_originals_have_complete_admission_and_union_runtime_minimum'
        with patch('quorune.compiler.receiver_union_queries.fixed_receiver_union_subject', return_value=None):
            with self.assertRaises(AssertionError):
                getattr(type(self)(method), method)()
        from quorune import object_query
        original = object_query.object_matches_query
        def omit(row, spec):
            return False if spec.any_of else original(row, spec)
        method = 'test_original_death_baron_cast_grants_overlap_once_and_deathtouch_kills'
        with patch('quorune.continuous_effects.object_matches_query', side_effect=omit):
            with self.assertRaises(AssertionError):
                getattr(type(self)(method), method)()
