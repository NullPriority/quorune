"""Versioned counter permissions for the current creature an Aura enchants."""

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
import test_targeted_graveyard_exile as previous
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database

from quorune.counter_permission import (
    CounterPermissionError,
    CounterPermissionSnapshot,
    CounterPlacementFact,
    CounterProhibitionRule,
    FixedCounterProhibitionSpec,
)


class AttachedCounterPermissionTests(unittest.TestCase):
    def test_attached_rule_checks_the_bound_creature_incarnation_and_all_counters(self):
        spec = FixedCounterProhibitionSpec('permanent', 'attached', types_any=('creature',))
        snapshot = CounterPermissionSnapshot((CounterProhibitionRule(spec, 'aura', 'A', 'creature', 'creature@2'),))
        subject = CounterPlacementFact('permanent', 'creature', '+1/+1', 1,
            controller='B', zone='battlefield', types=('creature',), logical_object_id='creature@2')
        self.assertFalse(snapshot.allows(subject))
        self.assertFalse(snapshot.allows(replace(subject, counter_name='stun', controller='C')))
        self.assertTrue(snapshot.allows(replace(subject, logical_object_id='creature@3')))
        self.assertTrue(snapshot.allows(replace(subject, subject_id='different', logical_object_id='different@1')))
        self.assertTrue(snapshot.allows(replace(subject, types=('artifact',))))
        self.assertTrue(snapshot.allows(replace(subject, zone='graveyard', controller=None)))
        self.assertTrue(snapshot.allows(replace(subject, amount=0)))
        with self.assertRaises(CounterPermissionError):
            snapshot.allows(replace(subject, logical_object_id=None))

    def test_attached_snapshot_has_one_version_and_retains_the_version_one_wire(self):
        old = {'schema_version': 1, 'rules': [{
            'spec': {'schema_version': 1, 'target_kind': 'permanent', 'relation': 'self',
                'counter_name': None, 'types_any': []},
            'source_object_id': 'self', 'source_controller': 'A',
        }]}
        existing = CounterPermissionSnapshot.from_dict(old)
        self.assertEqual(old, existing.to_dict())
        stale_fields = {**old['rules'][0], 'attached_object_id': None, 'attached_logical_object_id': None}
        with self.assertRaises(CounterPermissionError):
            CounterPermissionSnapshot.from_dict({'schema_version': 1, 'rules': [stale_fields]})
        spec = FixedCounterProhibitionSpec('permanent', 'attached', types_any=('creature',))
        combined = CounterPermissionSnapshot((*existing.rules,
            CounterProhibitionRule(spec, 'aura', 'B', 'creature', 'creature@1')))
        wire = combined.to_dict()
        self.assertEqual(2, wire['schema_version'])
        self.assertEqual(old['rules'][0], wire['rules'][0])
        self.assertEqual(combined, CounterPermissionSnapshot.from_dict(wire))
        with self.assertRaises(CounterPermissionError):
            CounterPermissionSnapshot.from_dict({**wire, 'schema_version': 1})

    def test_attachment_scope_needs_the_exact_type_and_paired_identity(self):
        for variants in (
            ('player', None, ()),
            ('permanent', '+1/+1', ('creature',)),
            ('permanent', None, ('artifact',)),
        ):
            with self.subTest(variants=variants), self.assertRaises(CounterPermissionError):
                FixedCounterProhibitionSpec(variants[0], 'attached', variants[1], variants[2])
        spec = FixedCounterProhibitionSpec('permanent', 'attached', types_any=('creature',))
        for object_id, incarnation in ((None, None), ('creature', None), (None, 'creature@1')):
            with self.subTest(object_id=object_id, incarnation=incarnation), self.assertRaises(CounterPermissionError):
                CounterProhibitionRule(spec, 'aura', 'A', object_id, incarnation)


class AttachedCounterPermissionCompilerTests(unittest.TestCase):
    def test_actual_compound_preserves_both_static_events_and_complete_card_admission(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory

        from quorune.card_programs import bind_card_program_runtime
        from quorune.card_programs.adapters import compile_best_available_card_program
        from quorune.carddb import CardDatabase
        from quorune.oracle_ir import compile_oracle_card, register_generated_programs
        from quorune.rules.capabilities import load_default_capability_registry
        from quorune.semantics import SemanticRegistry
        from scripts.build_test_database import build_fixture_database

        registry = load_default_capability_registry()
        profile = 'commander_review'
        fixture = Path(__file__).parent / 'fixtures' / 'attached-counter-prohibition-cards.json'
        with TemporaryDirectory() as folder:
            path = Path(folder) / 'attached-counters.sqlite3'
            build_fixture_database(fixture, path)
            with CardDatabase(path) as db:
                originals = ('Blossombind', 'Frozen in Ice', 'Solemnity', 'Spider-Woman, Secret Agent')
                semantics = SemanticRegistry()
                for name in originals:
                    with self.subTest(name=name):
                        record = db.lookup(name, fuzzy=False)
                        register_generated_programs(
                            db, semantics, (record,), capability_registry=registry, capability_profile=profile,
                            promote_exact_runtime_handlers=True, promote_exact_trigger_programs=True,
                            promote_exact_effect_programs=True, promote_exact_capability_declarations=True,
                        )
                        card = compile_best_available_card_program(
                            db, record, semantic_registry=semantics, capability_registry=registry, capability_profile=profile,
                        )
                        readiness = bind_card_program_runtime(card, capability_registry=registry, profile=profile)
                        self.assertEqual(name != originals[-1], readiness['strict_capability_ready'], readiness['blockers'])

                record = db.lookup('Blossombind', fuzzy=False)
                source_clause = record.oracle_text.splitlines()[-1]
                ir = compile_oracle_card(record, capability_registry=registry, capability_profile=profile)
                untap, counters = [n for n in ir.faces[0].nodes if n.text == source_clause]
                self.assertEqual(('characteristics.evaluate', 'continuous'), (untap.event, counters.event))
                self.assertEqual(untap.span, counters.span)
                self.assertEqual(source_clause, record.oracle_text[untap.span.start:untap.span.end])
                programs = {p.event: p for p in semantics.programs_for_oracle(record.oracle_id)
                    if p.event in {'characteristics.evaluate', 'continuous'}}
                self.assertNotEqual(programs[untap.event].ability_id, programs[counters.event].ability_id)
                self.assertIn('untap.permission.attached_static_restraint', programs[untap.event].capability_dependencies)
                self.assertIn('object.reference.source_attachment_information', programs[counters.event].capability_dependencies)

                for altered in (source_clause + ' Draw a card.', source_clause.replace('creature', 'permanent'),
                        source_clause.replace('counters put on it', '+1/+1 counters put on it')):
                    mutated = replace(record, oracle_text=record.oracle_text.replace(source_clause, altered))
                    rejected = compile_oracle_card(mutated, capability_registry=registry, capability_profile=profile)
                    self.assertNotEqual('exact', rejected.status, altered)

    def test_raw_attached_permission_and_snapshot_require_the_attachment_owner(self):
        from quorune.ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
        from quorune.compiler.counter_permission_templates import counter_prohibition_descriptor
        from quorune.counter_permission import counter_prohibition_payload_dependencies

        spec = FixedCounterProhibitionSpec('permanent', 'attached', types_any=('creature',))
        descriptor = counter_prohibition_descriptor(spec)
        expected = {'counter.placement.prohibition.static', 'object.reference.source_attachment_information'}
        self.assertEqual(expected, set(counter_prohibition_payload_dependencies(descriptor,
            coverage=(CURRENT_ABILITY_FRAGMENT_COVERAGE,))))
        with self.assertRaises(CounterPermissionError):
            counter_prohibition_payload_dependencies(descriptor)
        snapshot = CounterPermissionSnapshot((CounterProhibitionRule(spec, 'aura', 'A', 'target', 'target@1'),))
        self.assertEqual(expected, set(counter_prohibition_payload_dependencies(
            {'op': 'set', 'field': '_counter_permissions', 'value': snapshot.to_dict()})))


class AttachedCounterPermissionNativeTests(InheritedNativeFixture, unittest.TestCase):
    start = previous.TargetedGraveyardExileNativeTests.start
    reject = previous.TargetedGraveyardExileNativeTests.reject
    finish = previous.TargetedGraveyardExileNativeTests.finish

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        folder = Path(cls.temporary.name)
        base = json.loads((ROOT / 'tests/fixtures/bound-effect-program-cards.json').read_text(encoding='utf-8'))
        base['cards'] = [card for card in base['cards'] if card['name'] in {'Generic Bound Plains', 'Grizzly Bears'}]
        base['rulings'] = []
        basic = folder / 'basic.json'
        basic.write_text(json.dumps(base), encoding='utf-8')
        path = folder / 'attached-counter-native.sqlite3'
        build_fixture_database([
            basic,
            ROOT / 'tests/fixtures/targeted-graveyard-exile-cards.json',
            ROOT / 'tests/fixtures/counter-prohibition-cards.json',
            ROOT / 'tests/fixtures/attached-untap-restraint-cards.json',
            ROOT / 'tests/fixtures/attached-counter-prohibition-cards.json',
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Attached counter prohibitions', [
            DeckEntry('Sliver Hivelord', 1, 'commander'), DeckEntry('Generic Bound Plains', 30),
        ], ['Sliver Hivelord'])

    def test_paid_aura_blocks_both_effects_and_counter_cost_until_removed(self):
        session, private = self.start(744110)
        aura = self.add(session.engine, 'Blossombind', zone='hand')
        subject = self.add(session.engine, 'Grizzly Bears', ref='enchanted-counter-subject')
        subject.counters.update({'+1/+1': 1, 'stun': 1})
        other = self.add(session.engine, 'Fugitive Wizard', seat='B')
        boosts = [self.add(session.engine, 'Burst of Strength', zone='hand', ref=f'attachment-burst-{n}')
            for n in range(3)]
        sting = self.add(session.engine, 'Lethal Sting', zone='hand')
        removal = self.add(session.engine, 'Disenchant', zone='hand')
        for card in (aura, subject, other, *boosts, sting, removal):
            self.assert_closed(session, card)

        self.prepare(session, mana={'C': 4, 'U': 1, 'G': 3, 'B': 1, 'W': 1})
        self.checkpoint(session)
        self.cast(session, 'A', aura, [subject.ref])
        self.assertIsNone(self.resolve(session))
        self.assertEqual(subject.object_id, session.state.cards[aura.object_id].attached_to)
        self.assertEqual(3, session.state.players['A'].mana_pool['C'])
        self.assertEqual(0, session.state.players['A'].mana_pool['U'])
        self.assertTrue(session.state.cards[subject.object_id].tapped)

        self.cast(session, 'A', boosts[0], [subject.ref])
        self.assertIsNone(self.resolve(session))
        current = session.state.cards[subject.object_id]
        self.assertEqual({'+1/+1': 1, 'stun': 1}, dict(current.counters))
        self.assertTrue(current.tapped)
        self.cast(session, 'A', boosts[1], [other.ref])
        self.assertIsNone(self.resolve(session))
        self.assertEqual(1, session.state.cards[other.object_id].counters.get('+1/+1'))

        self.priority(session, 'A')
        command = {'action_id':f'cast:{sting.ref}', 'targets':[other.ref],
            'counter_cost_card':subject.ref, 'pay':'auto'}
        actions = session.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']
        self.assertNotIn(command['action_id'], {a['id'] for a in actions})
        self.reject(session, 'A', command)
        self.assertEqual(3, session.state.players['A'].mana_pool['C'])
        self.assertEqual(1, session.state.players['A'].mana_pool['B'])
        session = self.replay(session, load=True)

        self.cast(session, 'A', removal, [aura.ref])
        self.assertIsNone(self.resolve(session))
        self.cast(session, 'A', boosts[2], [subject.ref])
        self.assertIsNone(self.resolve(session))
        current = session.state.cards[subject.object_id]
        self.assertEqual({'+1/+1': 2}, dict(current.counters))
        self.assertTrue(current.tapped)

        self.priority(session, 'A')
        actions = session.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']
        self.assertIn(command['action_id'], {a['id'] for a in actions})
        result = session.act('pilot:A', command)
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(session))
        self.assertEqual('graveyard', session.state.cards[other.object_id].zone)
        self.assertEqual({'+1/+1': 1}, dict(session.state.cards[subject.object_id].counters))
        self.finish(session, private)

    def test_paid_aura_during_combat_preserves_dealt_infect_and_lifelink_without_counters(self):
        session, private = self.start(744120)
        attacker = self.add(session.engine, 'Flensermite')
        attacker.acquired_control_turn_count = -1
        blocker = self.add(session.engine, 'Grizzly Bears', seat='B', ref='infect-blocker')
        aura = self.add(session.engine, 'Blossombind', seat='C', zone='hand')
        orrery = self.add(session.engine, 'Vedalken Orrery', seat='C')
        lands = [self.add(session.engine, name, seat='C') for name in ('Island', 'Forest')]
        for card in (attacker, blocker, aura, orrery, *lands):
            self.assert_closed(session, card)

        self.prepare(session)
        self.checkpoint(session)
        self.attack(session, [(attacker, 'B')])
        self.until(session, lambda game: game.state.pending_decision is not None
            and game.state.pending_decision.kind == 'combat.blockers')
        session.packet('pilot:B', full=True)
        block = {'action_id':'block', 'blocks':{blocker.ref:attacker.ref}}
        self.reject(session, 'A', block)
        result = session.act('pilot:B', block)
        self.assertTrue(result.ok, result.summary)
        self.cast(session, 'C', aura, [blocker.ref])
        self.assertIsNone(self.resolve(session))
        self.assertTrue(session.state.cards[blocker.object_id].tapped)
        self.assertTrue(all(session.state.cards[land.object_id].tapped for land in lands))
        session = self.replay(session, load=True)

        self.until(session, lambda game: game.state.phase == 'postcombat_main')
        self.assertEqual('graveyard', session.state.cards[attacker.object_id].zone)
        defended = session.state.cards[blocker.object_id]
        self.assertEqual('battlefield', defended.zone)
        self.assertEqual({}, dict(defended.counters))
        self.assertEqual(0, defended.marked_damage)
        self.assertEqual(41, session.state.players['A'].life)
        self.assertEqual(40, session.state.players['B'].life)
        self.finish(session, private)

    def test_omitting_the_attached_counter_rule_breaks_the_actual_paid_aura_witness(self):
        from unittest.mock import patch

        original = FixedCounterProhibitionSpec.blocks

        def omitted(spec, fact, **bound):
            if spec.relation == 'attached':
                return False
            return original(spec, fact, **bound)

        with patch.object(FixedCounterProhibitionSpec, 'blocks', omitted):
            with self.assertRaisesRegex(AssertionError, r'\+1/\+1'):
                self.test_paid_aura_blocks_both_effects_and_counter_cost_until_removed()

    def test_verified_withdrawals_require_both_rule_owners_for_the_complete_aura(self):
        from copy import deepcopy

        from quorune.card_programs import bind_card_program_runtime
        from quorune.card_programs.adapters import compile_best_available_card_program
        from quorune.oracle_ir import register_generated_programs
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from quorune.semantics import SemanticRegistry
        from scripts.update_capability_evidence import build_index, discover_tests

        value = json.loads((ROOT / 'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        declarations = read_evidence_document(ROOT / 'platform/capability-evidence-declarations.json')
        discovered = set(discover_tests(ROOT).values())
        profile = 'commander_review'

        def verified(rows):
            registry = CapabilityRegistry(rows)
            index = build_index(registry_value=rows, declaration_source=declarations, discovered_test_ids=discovered)
            registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
            return registry

        def ready(name, registry):
            card = self.db.lookup(name, fuzzy=False)
            semantics = SemanticRegistry()
            register_generated_programs(
                self.db, semantics, (card,), capability_registry=registry, capability_profile=profile,
                promote_exact_runtime_handlers=True, promote_exact_trigger_programs=True,
                promote_exact_effect_programs=True, promote_exact_capability_declarations=True,
            )
            program = compile_best_available_card_program(
                self.db, card, semantic_registry=semantics, capability_registry=registry, capability_profile=profile,
            )
            return bind_card_program_runtime(program, capability_registry=registry, profile=profile)['strict_capability_ready']

        baseline = verified(deepcopy(value))
        for name in ('Blossombind', 'Frozen in Ice', 'Solemnity', 'Grizzly Bears'):
            self.assertTrue(ready(name, baseline), name)
        for missing in ('counter.placement.prohibition.static', 'object.reference.source_attachment_information',
                'untap.permission.attached_static_restraint'):
            with self.subTest(withdrawn=missing):
                rows = deepcopy(value)
                next(entry for entry in rows['capabilities'] if entry['id'] == missing).update(
                    status='blocked', blockers=['Exact attached Aura owner withdrawn'])
                registry = verified(rows)
                self.assertIn('status:' + missing + ':blocked', registry.closure((missing,), profile=profile).blockers)
                self.assertFalse(ready('Blossombind', registry))
                self.assertTrue(ready('Grizzly Bears', registry))
                if missing == 'counter.placement.prohibition.static':
                    self.assertTrue(ready('Frozen in Ice', registry))
                else:
                    self.assertTrue(ready('Solemnity', registry))
