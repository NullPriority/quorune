"""CR109.5/903.3: designated objects, current control, and noncopiable status."""

from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT, keep_all
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.model import GameConfig
from quorune.object_predicate import ObjectQuerySpec
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.commander_keyword_grants import (
    COMMANDER_KEYWORD_GRANT_HANDLER as HANDLER, COMMANDER_PERMANENT_QUERY_CAPABILITY as CAPABILITY,
)
from quorune.semantic_runtime.commander_keyword_grants import CommanderKeywordGrantHandler
from quorune.semantic_runtime.continuous_components import FixedQueryKeywordGrantHandler
from quorune.session import CommanderSession
from scripts.build_test_database import build_fixture_database
from test_qualified_zone_event_queries import query_record
import test_ability_source_counter_targets as native


class CommanderPermanentGrantCompilerTests(unittest.TestCase):
    def test_bare_commanders_preserve_each_keyword_and_owner_or_controller_relation(self):
        registry = load_default_capability_registry()
        for subject, relation in (('Commanders you control', 'source_controller'),
                                  ('Commanders you own', 'source_owner'), ('Other commanders you control', 'source_controller')):
            with self.subTest(subject=subject):
                ir = compile_oracle_card(query_record(subject + ' have menace and deathtouch.'),
                    capability_registry=registry, capability_profile='commander_review')
                self.assertEqual('exact', ir.status, ir.material_residuals)
                descriptor = ir.faces[0].nodes[0].handlers[0]
                self.assertEqual(HANDLER, descriptor['handler_id'])
                self.assertEqual(relation, descriptor['condition']['target_controller'])
                self.assertEqual(ObjectQuerySpec(zones=('battlefield',), commander=True),
                                 ObjectQuerySpec.from_dict(descriptor['condition']['predicate']))
                self.assertEqual(['Menace', 'Deathtouch'], descriptor['modifier']['add_abilities'])
                self.assertIn(CAPABILITY, ir.faces[0].nodes[0].capability_dependencies)

    def test_creature_only_contract_and_unknown_siblings_keep_their_boundaries(self):
        registry = load_default_capability_registry()
        ir = compile_oracle_card(query_record('Commander creatures you control have hexproof.'),
            capability_registry=registry, capability_profile='commander_review')
        self.assertEqual('exact', ir.status)
        descriptor = ir.faces[0].nodes[0].handlers[0]
        self.assertEqual('continuous.ability.fixed-query-keyword-grant.v1', descriptor['handler_id'])
        self.assertEqual(('creature',), ObjectQuerySpec.from_dict(descriptor['condition']['predicate']).types_all)
        self.assertNotIn(CAPABILITY, ir.faces[0].nodes[0].capability_dependencies)
        ir = compile_oracle_card(query_record('Commanders you control have hexproof.\nThe remembered prophecy becomes true.'),
            capability_registry=registry, capability_profile='commander_review')
        self.assertNotEqual('exact', ir.status)
        self.assertTrue(ir.material_residuals)

    def test_verified_commander_authority_withdrawal_retains_ordinary_creature_grants(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests
        raw = json.loads((ROOT / 'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        next(c for c in raw['capabilities'] if c['id'] == CAPABILITY).update(status='blocked', blockers=['Commander-permanent query withdrawn'])
        registry = CapabilityRegistry(raw)
        index = build_index(registry_value=raw,
            declaration_source=read_evidence_document(ROOT / 'platform/capability-evidence-declarations.json'),
            discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
        for text, expected in (('Commanders you control have hexproof.', False),
                               ('Commander creatures you control have hexproof.', True), ('Creatures you control have hexproof.', True)):
            ir = compile_oracle_card(query_record(text), capability_registry=registry, capability_profile='commander_review')
            self.assertEqual(expected, ir.status == 'exact', ir.material_residuals)


class CommanderPermanentGrantModelTests(unittest.TestCase):
    def descriptor(self):
        return {'handler_id': HANDLER, 'schema_version': 1, 'event': 'characteristics.evaluate',
            'condition': {'target_controller': 'source_controller', 'exclude_source': False,
                          'predicate': ObjectQuerySpec(zones=('battlefield',), commander=True).to_dict()},
            'modifier': {'add_abilities': ['Hexproof']}}

    def test_new_receiver_restores_the_exact_noncreature_predicate_after_modifier_validation(self):
        from quorune.card_programs.descriptor_requirements import typed_result_amount_runtime_dependencies
        descriptor = self.descriptor();before = deepcopy(descriptor)
        node = CommanderKeywordGrantHandler().validate(descriptor)
        self.assertEqual(ObjectQuerySpec(zones=('battlefield',), commander=True), node.predicate)
        self.assertEqual(('Hexproof',), node.abilities)
        self.assertIn(CAPABILITY, typed_result_amount_runtime_dependencies(descriptor))
        self.assertNotIn(CAPABILITY, typed_result_amount_runtime_dependencies(
            ObjectQuerySpec(zones=('command',), commander=True).to_dict()))
        self.assertEqual(before, descriptor)
        with self.assertRaises(ValueError):
            FixedQueryKeywordGrantHandler().validate({**descriptor, 'handler_id': FixedQueryKeywordGrantHandler().handler_id})

    def test_closed_receiver_envelope_rejects_unknown_and_changed_scope(self):
        descriptor = self.descriptor();handler = CommanderKeywordGrantHandler()
        for change in ({'schema_version': True}, {'schema_version': 2}, {'event': 'activate'}, {'future': 1}):
            with self.subTest(change=change), self.assertRaises(ValueError):handler.validate({**descriptor, **change})
        for query in (ObjectQuerySpec(zones=('hand',), commander=True), ObjectQuerySpec(zones=('battlefield',), commander=False),
                      ObjectQuerySpec(zones=('battlefield',), types_all=('creature',), commander=True),
                      ObjectQuerySpec(zones=('battlefield',), commander=True, include_phased_out=True)):
            with self.subTest(query=query), self.assertRaises(ValueError):
                handler.validate({**descriptor, 'condition': {**descriptor['condition'], 'predicate': query.to_dict()}})
        for changes in ({'target_controller': 'any'}, {'exclude_source': 1}, {'owner': 'A'}):
            with self.assertRaises(ValueError):handler.validate({**descriptor, 'condition': {**descriptor['condition'], **changes}})
        for modifier in ({'add_abilities': None}, {'add_abilities': ['Unknown']}, {'future': True}):
            with self.assertRaises(ValueError):handler.validate({**descriptor, 'modifier': modifier})

    def test_typed_protection_and_ward_keep_required_fragments(self):
        from quorune.compiler.continuous_templates import fixed_query_keyword_grant_handler
        for wording, label in (('protection from everything', 'Protection'), ('ward {2}', 'Ward {2}')):
            with self.subTest(wording=wording):
                compiled = fixed_query_keyword_grant_handler('Commanders you control have ' + wording + '.')
                self.assertIsNotNone(compiled)
                descriptor = compiled[1]
                node = CommanderKeywordGrantHandler().validate(descriptor)
                self.assertEqual((label,), node.abilities)
                self.assertEqual(1, len(node.fragments))
                with self.assertRaises(ValueError):
                    CommanderKeywordGrantHandler().validate({**descriptor, 'modifier': {'add_abilities': [label]}})

    def test_omitting_receiver_restoration_is_killed(self):
        import quorune.semantic_runtime.commander_keyword_grants as owner
        from quorune.semantic_runtime.continuous_components import FixedQueryKeywordGrantNode
        original = owner.replace
        def omit(value, **changes):
            return value if isinstance(value, FixedQueryKeywordGrantNode) else original(value, **changes)
        method = 'test_new_receiver_restores_the_exact_noncreature_predicate_after_modifier_validation'
        baseline = unittest.TestResult();type(self)(method).run(baseline)
        self.assertTrue(baseline.wasSuccessful(), baseline.errors + baseline.failures)
        with patch.object(owner, 'replace', side_effect=omit):
            changed = unittest.TestResult();type(self)(method).run(changed)
        self.assertFalse(changed.errors, changed.errors)
        self.assertEqual(1, len(changed.failures))


class CommanderPermanentGrantRuntimeTests(unittest.TestCase):
    register_fixture_cards_on_add = True
    add = native.AbilitySourceCounterRuntimeTests.add
    prepare = native.AbilitySourceCounterRuntimeTests.prepare
    checkpoint = native.AbilitySourceCounterRuntimeTests.checkpoint
    priority = native.AbilitySourceCounterRuntimeTests.priority
    actions = native.AbilitySourceCounterRuntimeTests.actions
    cast = native.AbilitySourceCounterRuntimeTests.cast
    binding = native.AbilitySourceCounterRuntimeTests.binding
    reject = native.AbilitySourceCounterRuntimeTests.reject
    settle = native.AbilitySourceCounterRuntimeTests.settle
    replay = native.AbilitySourceCounterRuntimeTests.replay
    private_replay = native.AbilitySourceCounterRuntimeTests.private_replay

    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory();path = Path(cls.temporary.name) / 'commander-permanent-keywords.sqlite3'
        build_fixture_database([ROOT / 'tests/fixtures/bound-effect-program-cards.json',
            ROOT / 'tests/fixtures/commander-permanent-keyword-cards.json'], path)
        cls.db = CardDatabase(path);cls.registry = load_default_capability_registry()

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temporary.cleanup()

    def session(self, seed):
        names = {'A': 'Shorikai, Genesis Engine', 'B': 'Isamaru, Hound of Konda',
                 'C': 'Sliver Hivelord', 'D': 'Sliver Hivelord'}
        decks = {seat: DeckDefinition('Commander receivers ' + seat,
            [DeckEntry(name, 1, 'commander'), DeckEntry('Island' if seat == 'A' else 'Plains', 30)], [name])
            for seat, name in names.items()}
        session = CommanderSession.create(self.db, decks, first_player='A', seed=seed,
            config=GameConfig(seed=seed, auto_pass_empty_priority=False))
        keep_all(session)
        session.engine.permissions.invalidate_current();session.state.pending_decision = None
        session.state.priority_player = None;session.state.priority_passes = []
        return session

    def commander(self, session, seat):
        card = session.state.cards[session.state.players[seat].zones['command'][0]]
        self.assertTrue(self.binding(session, card)['strict_capability_ready'])
        session.engine.move_card(card.object_id, 'battlefield', controller=seat, log=False)
        return card

    def test_originals_normally_bind_every_printed_sibling(self):
        session = self.session(982_000)
        for name in ('Falthis, Shadowcat Familiar', 'Guardian Augmenter',
                     'Anara, Wolvid Familiar', 'Vexilus Praetor'):
            with self.subTest(name=name):
                source = self.add(session.engine, name, seat='C', zone='hand')
                result = self.binding(session, source)
                self.assertTrue(result['strict_capability_ready'], result['blockers'])

    def test_actual_guardian_covers_noncreature_and_foreign_commanders_but_not_a_copy(self):
        session = self.session(982_001);engine = session.engine
        vehicle, foreign = self.commander(session, 'A'), self.commander(session, 'B')
        guardian = self.add(engine, 'Guardian Augmenter', seat='C')
        first = self.add(engine, 'Control Magic', zone='hand', ref='guardian-control')
        second = self.add(engine, 'Control Magic', zone='hand', ref='foreign-commander-control')
        removal = self.add(engine, 'Naturalize', seat='D', zone='hand')
        copy_removal = self.add(engine, 'Naturalize', seat='D', zone='hand', ref='copy-removal')
        private = self.add(engine, 'Generic Bound Growth', seat='C', zone='hand')
        copy_ref, = engine.create_token('C', name='Shorikai copy', copy_of=vehicle.ref)
        copied = next(c for c in session.state.cards.values() if c.ref == copy_ref)
        for card in (guardian, first, second, removal, copy_removal):
            self.assertTrue(self.binding(session, card)['strict_capability_ready'], card.printed_name)
        self.prepare(session, mana={'C': 4, 'U': 4});session.state.players['D'].mana_pool.update(C=2, G=2)
        self.checkpoint(session)
        self.cast(session, first, (guardian.ref,));self.assertIsNone(self.settle(session))
        self.cast(session, second, (foreign.ref,));self.assertIsNone(self.settle(session))
        self.assertEqual(('A', 'C'), (session.state.cards[guardian.object_id].controller, guardian.owner))
        self.assertNotIn('Creature', engine._effective_card_data(session.state.cards[vehicle.object_id])['type_line'])
        self.priority(session, 'D')
        offered = next(r for r in self.actions(session, 'D') if r.get('card') == removal.ref)
        self.assertNotIn(vehicle.ref, offered['target_schema']['legal_refs'])
        self.assertEqual((4, 4), (engine._numeric_stat(foreign.object_id, 'power'), engine._numeric_stat(foreign.object_id, 'toughness')))
        session = self.private_replay(session, private)
        self.cast(session, removal, (first.ref,), seat='D');self.assertIsNone(self.settle(session))
        self.assertEqual('C', session.state.cards[guardian.object_id].controller)
        self.assertEqual((2, 2), (session.engine._numeric_stat(foreign.object_id, 'power'), session.engine._numeric_stat(foreign.object_id, 'toughness')))
        self.priority(session, 'D')
        offered = next(r for r in self.actions(session, 'D') if r.get('card') == copy_removal.ref)
        self.assertIn(copied.ref, offered['target_schema']['legal_refs'])
        self.cast(session, copy_removal, (copied.ref,), seat='D');self.assertIsNone(self.settle(session))
        self.assertNotEqual('battlefield', session.state.cards[copied.object_id].zone)
        self.private_replay(session, private)

    def test_actual_anara_turn_condition_follows_current_provider_control(self):
        session = self.session(982_002);engine = session.engine
        vehicle = self.commander(session, 'A')
        source = self.add(engine, 'Anara, Wolvid Familiar', seat='C')
        control = self.add(engine, 'Control Magic', zone='hand')
        first = self.add(engine, 'Naturalize', seat='D', zone='hand', ref='anara-first-destruction')
        second = self.add(engine, 'Naturalize', seat='D', zone='hand', ref='anara-control-removal')
        third = self.add(engine, 'Naturalize', seat='D', zone='hand', ref='anara-final-destruction')
        private = self.add(engine, 'Generic Bound Growth', seat='C', zone='hand')
        self.assertTrue(self.binding(session, source)['strict_capability_ready'])
        self.prepare(session, mana={'C': 2, 'U': 2});session.state.players['D'].mana_pool.update(C=3, G=3)
        self.checkpoint(session)
        self.cast(session, control, (source.ref,));self.assertIsNone(self.settle(session))
        self.cast(session, first, (vehicle.ref,), seat='D');self.assertIsNone(self.settle(session))
        self.assertEqual('battlefield', session.state.cards[vehicle.object_id].zone)
        session = self.private_replay(session, private)
        self.cast(session, second, (control.ref,), seat='D');self.assertIsNone(self.settle(session))
        self.assertEqual(('C', 'A'), (session.state.cards[source.object_id].controller, session.state.active_player))
        self.cast(session, third, (vehicle.ref,), seat='D')
        decision = self.settle(session)
        self.assertEqual('state.commander_zone', decision.kind)
        self.assertEqual(['A'], decision.actors)
        self.reject(session, {'action_id': 'command'}, seat='D')
        result = session.act('pilot:A', {'action_id': 'command'});self.assertTrue(result.ok, result.summary)
        self.assertEqual('command', session.state.cards[vehicle.object_id].zone)
        self.private_replay(session, private)
