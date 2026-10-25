from __future__ import annotations

import json
from pathlib import Path
import tempfile
from dataclasses import replace
import unittest
from unittest.mock import patch

from common import ROOT
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.conditional_recipient_model import ConditionalRecipientSpec, CONDITIONAL_RECIPIENT_CAPABILITY as CAPABILITY, conditional_recipient_capabilities
from quorune.compiler.conditional_recipients import conditional_recipient_template
from quorune.compiler.conditional_recipient_conditions import conditional_recipient_condition
from quorune.semantic_runtime.conditional_continuous import FixedPublicStateCharacteristicsHandler
from scripts.build_test_database import build_fixture_database
import test_receiver_query_unions as controls
from quorune.rules.evidence_storage import read_evidence_document


NAMES = ('Sylvan Advocate', 'Angelic Field Marshal', 'Thunderfoot Baloth', 'Homicidal Seclusion', 'Deadly Wanderings')


class ConditionalRecipientModelTests(unittest.TestCase):
    def test_roles_and_exactly_one_condition_have_closed_legacy_compatible_wire(self):
        for role in ('source', 'other_creatures', 'other_land_creatures', 'controlled_creatures', 'unique_creature'):
            spec = ConditionalRecipientSpec(role)
            self.assertEqual(spec, ConditionalRecipientSpec.from_dict(json.loads(json.dumps(spec.to_dict()))))
            for change in ({'role': 'future'}, {'schema_version': True}, {'future': 1}):
                with self.assertRaises(ValueError):
                    ConditionalRecipientSpec.from_dict({**spec.to_dict(), **change})
        from public_state_condition_support import source_context
        from quorune.continuous_conditions import FixedPublicStateConditionSpec
        condition = conditional_recipient_condition('you control exactly one creature')
        self.assertEqual(condition, FixedPublicStateConditionSpec.from_dict(condition.to_dict()))
        snapshot = source_context().public_state
        for count in (0, 1, 2, 7):
            leaf = replace(snapshot, condition_quantity=count)
            self.assertEqual(count == 1, condition.matches(replace(leaf, term_snapshots=(leaf, leaf))))

    def test_pinned_bodies_keep_separate_recipients_and_reject_scope_drift(self):
        data = json.loads((ROOT/'tests/fixtures/conditional-recipient-cards.json').read_text(encoding='utf-8'))
        for card in data['cards'][:5]:
            text = next(line for line in card['oracle_text'].splitlines() if 'as long as' in line.casefold())
            compiled = conditional_recipient_template(text, source_name=card['name'])
            self.assertIsNotNone(compiled, card['name'])
            self.assertEqual(2 if card['name'] in NAMES[:3] else 1, len(compiled[1]))
            for descriptor in compiled[1]:
                FixedPublicStateCharacteristicsHandler().validate(descriptor)
                broken = {**descriptor, 'recipient': {'schema_version': 1, 'role': 'unique_creature'}}
                if descriptor['recipient']['role'] != 'unique_creature':
                    with self.assertRaises(ValueError):
                        FixedPublicStateCharacteristicsHandler().validate(broken)
            self.assertIn(CAPABILITY, conditional_recipient_capabilities(compiled[1]))
        for text in ('As long as you control exactly two creatures, that creature gets +2/+2.',
                'As long as you control your commander, this creature and opponents\u2019 creatures get +2/+2.',
                'As long as you control six or more lands, this creature and land creatures you control get +X/+X.'):
            self.assertIsNone(conditional_recipient_template(text, source_name='Generic Observer'))

    def test_commander_presence_fact_requires_boolean_designation_and_independent_minimum(self):
        from quorune.card_programs.descriptor_requirements import typed_result_amount_runtime_dependencies
        from quorune.continuous_conditions import FixedPublicStateConditionSpec
        condition = conditional_recipient_condition('you control your commander')
        self.assertIn('state_query.commander.designation', typed_result_amount_runtime_dependencies(condition.to_dict()))
        self.assertIn(CAPABILITY, typed_result_amount_runtime_dependencies(condition.to_dict()))
        for change in ({'amount': 2}, {'fact_parameter': 'printed name'}, {'schema_version': 3}):
            with self.assertRaises(ValueError):
                FixedPublicStateConditionSpec.from_dict({**condition.to_dict(), **change})
        self.assertEqual((), conditional_recipient_capabilities(({
            'handler_id': 'continuous.characteristics.fixed-public-state.v1',
            'source_condition': {'schema_version': 1, 'kind': 'controller_turn', 'amount': None, 'counter_name': None}},)))


class ConditionalRecipientRuntimeTests(unittest.TestCase):
    register_fixture_cards_on_add = True
    session = controls.ReceiverQueryUnionRuntimeTests.session
    add = controls.ReceiverQueryUnionRuntimeTests.add
    prepare = controls.ReceiverQueryUnionRuntimeTests.prepare
    checkpoint = controls.ReceiverQueryUnionRuntimeTests.checkpoint
    resolve = controls.ReceiverQueryUnionRuntimeTests.resolve
    replay = controls.ReceiverQueryUnionRuntimeTests.replay
    binding = controls.ReceiverQueryUnionRuntimeTests.binding
    priority = controls.ReceiverQueryUnionRuntimeTests.priority
    private_replay = controls.ReceiverQueryUnionRuntimeTests.private_replay
    cast = controls.ReceiverQueryUnionRuntimeTests.cast
    stats = controls.ReceiverQueryUnionRuntimeTests.stats
    advance = controls.ReceiverQueryUnionRuntimeTests.advance
    attack = controls.ReceiverQueryUnionRuntimeTests.attack
    blockers = controls.ReceiverQueryUnionRuntimeTests.blockers
    block = controls.ReceiverQueryUnionRuntimeTests.block

    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        path = Path(cls.temp.name)/'conditional-recipients.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',
            ROOT/'tests/fixtures/conditional-recipient-cards.json'], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Conditional recipients', [DeckEntry('Generic Bound Commander', 1, 'commander'),
            DeckEntry('Generic Bound Plains', 30)], ['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temp.cleanup()

    def commander(self, s, seat='A', *, battlefield=True):
        self.add(s.engine, 'Generic Bound Commander', seat='D', zone='hand', ref='commander-registration-control-'+seat)
        card = next(s.state.cards[oid] for oid in s.state.players[seat].zones['command'])
        if battlefield:
            s.engine.move_card(card.object_id, 'battlefield', controller=seat, reason='native commander condition boundary')
        return s.state.cards[card.object_id]

    def test_five_originals_have_complete_programs_and_independent_recipient_minimum(self):
        from quorune.oracle_ir import compile_oracle_card
        from quorune.card_programs.binding import bind_semantic_program_runtime
        for i, name in enumerate(NAMES):
            s = self.session(753100+i)
            source = self.add(s.engine, name, zone='hand')
            self.assertTrue(self.binding(s, source)['strict_capability_ready'], name)
            ir = compile_oracle_card(self.db.lookup(name), capability_registry=self.registry, capability_profile='commander_review')
            self.assertEqual('exact', ir.status, (name, ir.material_residuals))
            program = next(p for p in s.engine.semantics.programs_for_oracle(source.oracle_id) if CAPABILITY in p.capability_dependencies)
            self.assertEqual(2 if name in NAMES[:3] else 1, len(program.handlers))
            deps = tuple(c for c in program.capability_dependencies if c != CAPABILITY)
            missing = replace(program, capability_dependencies=deps,
                capability_closure=self.registry.closure(deps, profile='commander_review').to_dict())
            bound = bind_semantic_program_runtime(missing, capability_registry=self.registry, profile='commander_review')
            self.assertFalse(bound['strict'])
            self.assertIn('capability:undeclared_runtime_dependency:'+CAPABILITY, bound['blockers'])

    def test_actual_advocate_cast_and_land_animation_apply_overlap_once(self):
        s = self.session(753110)
        source = self.add(s.engine, 'Sylvan Advocate', zone='hand')
        lands = [self.add(s.engine, 'Forest', ref='advocate-land-'+str(i)) for i in range(5)]
        animated = self.add(s.engine, 'Island')
        animate = self.add(s.engine, 'Living Plane', zone='hand')
        ordinary = self.add(s.engine, 'Generic Bound Body')
        private = self.add(s.engine, 'Generic Bound Growth', seat='C', zone='hand')
        self.prepare(s, mana={'C': 3, 'G': 3})
        self.checkpoint(s)
        self.cast(s, source, payment={'C': 1, 'G': 1})
        s = self.replay(s, load=True)
        self.assertIsNone(self.resolve(s))
        self.assertEqual((4, 5), self.stats(s, source))
        self.assertEqual((2, 6), self.stats(s, ordinary))
        self.assertIn('vigilance', s.engine._combat_keywords(s.state.cards[source.object_id]))
        self.cast(s, animate, payment={'C': 2, 'G': 2})
        self.assertIsNone(self.resolve(s))
        self.assertEqual((3, 3), self.stats(s, animated))
        from quorune.continuous_effect_model import ContinuousOperation, Layer
        from quorune.continuous_effect_state import ResolutionEffectSource, ResolutionContinuousComponent, create_resolution_continuous_effect_components
        create_resolution_continuous_effect_components(s.engine, source=ResolutionEffectSource(stack_ref='advocate-land-type-boundary'),
            targets=(s.state.cards[source.object_id],), components=(ResolutionContinuousComponent(Layer.TYPE, '4',
                (ContinuousOperation('add_types', ['Land'], 'card_types'),)),))
        self.assertEqual((3, 3), self.stats(s, source))
        # The source now also counts as a land; removing two lands crosses six.
        s.engine.move_card(lands[0].object_id, 'hand', reason='conditional count boundary')
        s.engine.move_card(lands[1].object_id, 'hand', reason='conditional count boundary')
        self.assertEqual((1, 1), self.stats(s, source))
        self.assertEqual((1, 1), self.stats(s, animated))
        self.prepare(s)
        self.checkpoint(s)
        self.private_replay(s, private)

    def test_actual_field_marshal_cast_grants_current_creatures_vigilance_and_self_stats(self):
        s = self.session(753111)
        commander = self.commander(s)
        source = self.add(s.engine, 'Angelic Field Marshal', zone='hand')
        body = self.add(s.engine, 'Grizzly Bears')
        body.acquired_control_turn_count = -1
        foreign = self.add(s.engine, 'Grizzly Bears', seat='B', ref='marshal-opponent')
        private = self.add(s.engine, 'Generic Bound Growth', seat='C', zone='hand')
        self.prepare(s, mana={'C': 2, 'W': 2})
        self.checkpoint(s)
        self.cast(s, source)
        self.assertIsNone(self.resolve(s))
        self.assertEqual((5, 5), self.stats(s, source))
        self.assertIn('flying', s.engine._combat_keywords(s.state.cards[source.object_id]))
        self.assertIn('vigilance', s.engine._combat_keywords(s.state.cards[body.object_id]))
        self.assertNotIn('vigilance', s.engine._combat_keywords(s.state.cards[foreign.object_id]))
        self.attack(s, {body.ref: 'B'})
        self.assertFalse(s.state.cards[body.object_id].tapped)
        self.blockers(s)
        self.block(s, {})
        self.advance(s, lambda current: current.state.phase == 'postcombat_main')
        self.assertEqual(38, s.state.players['B'].life)
        self.private_replay(s, private)

    def test_actual_baloth_cast_buffs_source_once_and_grants_other_creatures_trample(self):
        s = self.session(753112)
        commander = self.commander(s)
        source = self.add(s.engine, 'Thunderfoot Baloth', zone='hand')
        body = self.add(s.engine, 'Generic Bound Body')
        foreign = self.add(s.engine, 'Generic Bound Body', seat='B', ref='baloth-opponent')
        private = self.add(s.engine, 'Generic Bound Growth', seat='C', zone='hand')
        self.prepare(s, mana={'C': 4, 'G': 2})
        self.checkpoint(s)
        self.cast(s, source)
        s = self.replay(s, load=True)
        self.assertIsNone(self.resolve(s))
        self.assertEqual((7, 7), self.stats(s, source))
        self.assertEqual((4, 8), self.stats(s, body))
        self.assertEqual((2, 6), self.stats(s, foreign))
        self.assertIn('trample', s.engine._combat_keywords(s.state.cards[body.object_id]))
        s.engine.change_control(commander.object_id, 'B', reason='Lieutenant commander control boundary')
        self.assertEqual((5, 5), self.stats(s, source))
        self.assertEqual((2, 6), self.stats(s, body))
        self.assertIn('trample', s.engine._combat_keywords(s.state.cards[source.object_id]))
        self.assertNotIn('trample', s.engine._combat_keywords(s.state.cards[body.object_id]))
        self.prepare(s)
        self.checkpoint(s)
        self.private_replay(s, private)

    def sole_creature(self, name, seed, expected, keywords):
        s = self.session(seed)
        source = self.add(s.engine, name, zone='hand')
        body = self.add(s.engine, 'Generic Bound Body')
        body.acquired_control_turn_count = -1
        self.add(s.engine, 'Grizzly Bears', seat='B', ref='sole-legal-blocker')
        private = self.add(s.engine, 'Generic Bound Growth', seat='C', zone='hand')
        self.prepare(s, mana={'C': 4, 'B': 2})
        self.checkpoint(s)
        self.cast(s, source)
        s = self.replay(s, load=True)
        self.assertIsNone(self.resolve(s))
        self.assertEqual(expected, self.stats(s, body))
        self.assertTrue(set(keywords).issubset(s.engine._combat_keywords(s.state.cards[body.object_id])))
        self.attack(s, {body.ref: 'B'})
        self.blockers(s)
        self.block(s, {})
        self.advance(s, lambda current: current.state.phase == 'postcombat_main')
        self.assertEqual(40+expected[0], s.state.players['A'].life)
        self.assertEqual(40-expected[0], s.state.players['B'].life)
        self.private_replay(s, private)

    def test_actual_seclusion_cast_grants_only_current_sole_creature_and_lifelink_damage(self):
        self.sole_creature('Homicidal Seclusion', 753113, (5, 7), ('lifelink',))

    def test_actual_wanderings_cast_grants_only_current_sole_creature_and_lifelink_damage(self):
        self.sole_creature('Deadly Wanderings', 753114, (4, 6), ('lifelink', 'deathtouch'))

    def test_actual_second_creature_and_removal_move_the_unique_bonus_live(self):
        s = self.session(753115)
        source = self.add(s.engine, 'Deadly Wanderings')
        first = self.add(s.engine, 'Generic Bound Body')
        second = self.add(s.engine, 'Grizzly Bears', zone='hand')
        remove = self.add(s.engine, 'Unsummon', zone='hand')
        humble = self.add(s.engine, 'Humble', zone='hand')
        private = self.add(s.engine, 'Generic Bound Growth', seat='C', zone='hand')
        self.prepare(s, mana={'C': 2, 'G': 1, 'U': 1, 'W': 1})
        self.checkpoint(s)
        self.assertEqual((4, 6), self.stats(s, first))
        self.cast(s, second, payment={'C': 1, 'G': 1})
        self.assertIsNone(self.resolve(s))
        self.assertEqual((2, 6), self.stats(s, first))
        self.assertEqual((2, 2), self.stats(s, second))
        self.assertNotIn('lifelink', s.engine._combat_keywords(s.state.cards[first.object_id]))
        self.cast(s, remove, [first.ref])
        self.assertIsNone(self.resolve(s))
        self.assertEqual((4, 2), self.stats(s, second))
        self.cast(s, humble, [second.ref])
        s = self.replay(s, load=True)
        self.assertIsNone(self.resolve(s))
        self.assertEqual((2, 1), self.stats(s, second))
        self.assertNotIn('lifelink', s.engine._combat_keywords(s.state.cards[second.object_id]))
        self.private_replay(s, private)

    def test_lieutenant_checks_current_controller_own_commander_and_phasing(self):
        s = self.session(753116)
        source = self.add(s.engine, 'Angelic Field Marshal')
        own = self.commander(s, battlefield=False)
        enemy = self.commander(s, seat='B')
        s.engine.change_control(enemy.object_id, 'A', reason='foreign commander control boundary')
        self.assertEqual((3, 3), self.stats(s, source))
        s.engine.move_card(own.object_id, 'battlefield', controller='A', reason='own commander arrives')
        self.assertEqual((5, 5), self.stats(s, source))
        own = s.state.cards[own.object_id]
        own.phased_out = True
        self.assertEqual((3, 3), self.stats(s, source))
        own.phased_out = False
        self.assertEqual((5, 5), self.stats(s, source))
        s.engine.change_control(source.object_id, 'B', reason='Lieutenant source control boundary')
        self.assertEqual((3, 3), self.stats(s, source))
        s.engine.change_control(enemy.object_id, 'B', reason='current controller commander returns')
        self.assertEqual((5, 5), self.stats(s, source))
        source.phased_out = True
        self.assertEqual((3, 3), self.stats(s, source))

    def test_actual_source_ability_loss_and_departure_remove_both_recipient_grants(self):
        s = self.session(753117)
        self.commander(s)
        source = self.add(s.engine, 'Thunderfoot Baloth')
        body = self.add(s.engine, 'Generic Bound Body')
        humble = self.add(s.engine, 'Humble', zone='hand')
        bounce = self.add(s.engine, 'Unsummon', zone='hand')
        private = self.add(s.engine, 'Generic Bound Growth', seat='C', zone='hand')
        self.prepare(s, mana={'C': 1, 'W': 1, 'U': 1})
        self.checkpoint(s)
        self.assertEqual((4, 8), self.stats(s, body))
        self.cast(s, humble, [source.ref], payment={'C': 1, 'W': 1})
        self.assertIsNone(self.resolve(s))
        self.assertEqual((0, 1), self.stats(s, source))
        self.assertEqual((2, 6), self.stats(s, body))
        self.assertNotIn('trample', s.engine._combat_keywords(s.state.cards[body.object_id]))
        self.cast(s, bounce, [source.ref])
        self.assertIsNone(self.resolve(s))
        self.assertEqual('hand', s.state.cards[source.object_id].zone)
        self.assertEqual((2, 6), self.stats(s, body))
        self.private_replay(s, private)

    def test_canonically_verified_conditional_recipient_withdrawal_blocks_all_originals(self):
        from copy import deepcopy
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from scripts.update_capability_evidence import build_index, discover_tests
        from quorune.oracle_ir import compile_oracle_card
        raw = json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        declarations = read_evidence_document(ROOT/'platform/capability-evidence-declarations.json')
        value = deepcopy(raw)
        next(c for c in value['capabilities'] if c['id'] == CAPABILITY).update(status='blocked', blockers=['withdrawn conditional recipient'])
        registry = CapabilityRegistry(value)
        index = build_index(registry_value=value, declaration_source=declarations,
            discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
        for name in NAMES:
            ir = compile_oracle_card(self.db.lookup(name), capability_registry=registry, capability_profile='commander_review')
            self.assertNotEqual('exact', ir.status, name)
            blockers = [b for residual in ir.material_residuals for b in residual.blockers]
            self.assertTrue(any('status:'+CAPABILITY+':blocked' in b for b in blockers), blockers)
            self.assertFalse(any('evidence_index:unverified' in b for b in blockers), blockers)

    def test_fresh_originals_kill_conditional_recipient_producer_and_commander_fact_omissions(self):
        method = 'test_five_originals_have_complete_programs_and_independent_recipient_minimum'
        with patch('quorune.compiler.conditional_recipients.conditional_recipient_template', return_value=None):
            with self.assertRaises(AssertionError):
                getattr(type(self)(method), method)()
        from quorune.card_programs import runtime
        original = runtime._FixedPublicStateSnapshotResolver._public_fact_quantity
        def omit(self, source, condition):
            from quorune.continuous_conditions import FixedPublicStateFact
            return 0 if condition.fact is FixedPublicStateFact.CONTROLLER_CONTROLS_OWN_COMMANDER else original(self, source, condition)
        method = 'test_actual_field_marshal_cast_grants_current_creatures_vigilance_and_self_stats'
        with patch.object(runtime._FixedPublicStateSnapshotResolver, '_public_fact_quantity', omit):
            with self.assertRaises(AssertionError):
                getattr(type(self)(method), method)()
