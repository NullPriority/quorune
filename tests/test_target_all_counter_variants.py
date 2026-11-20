"""CR122/608 and original Hapatra/Omen rulings, with complete printed sources."""
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import json
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT
from quorune.carddb import CardDatabase
from quorune.card_programs.adapters import compile_best_available_card_program
from quorune.card_programs.binding import bind_card_program_runtime, bind_semantic_program_runtime
from quorune.compiler.counter_removal_templates import (
    all_counter_removal_effect_template, all_named_source_counter_removal_effect_template,
)
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card, register_generated_programs
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry
from quorune.rules.target_all_counter_removal import (
    NAMED_TARGET_ALL_COUNTER_CAPABILITY as NAMED, OPTIONAL_TARGET_ALL_COUNTER_CAPABILITY as OPTIONAL,
    target_all_counter_variant_capabilities, named_target_all_counter_payload_dependencies,
    target_all_counter_variant_runtime_dependencies,
)
from quorune.semantic_runtime.context import ReadOnlyHandlerContext, SemanticNodeError
from quorune.semantic_runtime.counter_removal_handlers import AllCounterRemovalHandler
from quorune.semantics import SemanticRegistry
from scripts.build_test_database import build_fixture_database
import test_attachment_followup_bindings as controls
import test_bound_effect_programs as compiler_controls

FIXTURE = ROOT / 'tests/fixtures/target-all-counter-variants-cards.json'
CANDIDATES = ("Hapatra's Mark", 'Purging Stormbrood // Absorb Essence')


class TargetAllCounterModelTests(unittest.TestCase):
    def test_named_target_three_keeps_legacy_source_two_and_bare_all_encodings(self):
        named = all_counter_removal_effect_template('Remove all -1/-1 counters from target creature.')
        _, effects, schema, mechanics = named.compiled()
        self.assertEqual(3, effects[0]['schema_version'])
        self.assertIn(NAMED, target_all_counter_variant_capabilities(effects=effects,
            target_schema=schema, mechanic_ids=mechanics))
        legacy = all_named_source_counter_removal_effect_template(
            'Remove all -1/-1 counters from this creature.', card_name='Source')
        self.assertEqual({'op': 'remove_all_counters', 'schema_version': 2, 'counter': '-1/-1',
            'card': '$source.zone_object', 'source': '$source'}, legacy[1][0])
        ordinary = all_counter_removal_effect_template('Remove all counters from target permanent.')
        self.assertEqual({'op': 'remove_all_counters', 'card': '$target.0', 'source': '$source'}, ordinary.effects[0])
        context = ReadOnlyHandlerContext.from_sequences(actor='A', default_reason='Counter selection',
            seats=tuple('ABCD'), active_seats=tuple('ABCD'), apnap_order=tuple('ABCD'))
        for version in (2, 3):
            plan = AllCounterRemovalHandler().lower({**effects[0], 'schema_version': version,
                'card': 'recipient', 'source': 'source'}, context)
            intent, = plan.intents
            self.assertEqual(('-1/-1', 'recipient'), (intent.counter_name, intent.object_ref))
        for changed in ({'schema_version': True}, {'schema_version': 4}, {'counter': 'Charge'},
                        {'counter': ''}, {'extra': True}):
            with self.subTest(changed=changed), self.assertRaises(SemanticNodeError):
                AllCounterRemovalHandler().lower({**effects[0], 'card': 'recipient', 'source': 'source', **changed}, context)

    def test_optional_target_and_named_payload_cannot_borrow_unsupported_scope(self):
        for text in ('Remove all counters from up to one target creature.',
                     'Remove all charge counters from target artifact you control.'):
            _, effects, schema, mechanics = all_counter_removal_effect_template(text).compiled()
            self.assertTrue(target_all_counter_variant_capabilities(effects=effects,
                target_schema=schema, mechanic_ids=mechanics))
            for invalid in ({**schema, 'count': True}, {**schema, 'count': 2},
                            {**schema, 'up_to': True}, {**schema, 'up_to': 2}, {**schema, 'unknown': True}):
                self.assertFalse(target_all_counter_variant_capabilities(effects=effects,
                    target_schema=invalid, mechanic_ids=mechanics))
        named = all_counter_removal_effect_template('Remove all charge counters from target artifact.').effects[0]
        for changed in ({'schema_version': 2}, {'card': '$source.zone_object'}, {'counter': 'Charge'}, {'amount': 1000}):
            with self.assertRaises(ValueError):named_target_all_counter_payload_dependencies({**named, **changed})
        for text in ('Remove all counters from target player.', 'Remove all counters from each creature.',
                     'Remove all counters from up to two target creatures.',
                     'Remove all charge counters from up to one target artifact.'):
            self.assertIsNone(all_counter_removal_effect_template(text))

    def test_payload_and_optional_target_drive_runtime_minima_without_coverage(self):
        for text, capability in (('Remove all -1/-1 counters from target creature.', NAMED),
                                 ('Remove all counters from up to one target creature.', OPTIONAL)):
            _, effects, schema, _ = all_counter_removal_effect_template(text).compiled()
            program = SimpleNamespace(effects=effects, target_schema=schema, coverage=())
            self.assertIn(capability, target_all_counter_variant_runtime_dependencies(program))
            with self.assertRaises(ValueError):
                target_all_counter_variant_runtime_dependencies(SimpleNamespace(effects=effects,
                    target_schema={**schema, 'count': 2}, coverage=()))
            if capability == NAMED:
                for version in (True, 1, 2, '3'):
                    with self.assertRaises(ValueError):
                        target_all_counter_variant_runtime_dependencies(SimpleNamespace(
                            effects=({**effects[0], 'schema_version': version},), target_schema=schema, coverage=()))


class TargetAllCounterCompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.registry = load_default_capability_registry()

    def test_exact_linked_and_optional_source_spans_require_both_new_authorities(self):
        texts = ("Target creature you control gains hexproof until end of turn. Remove all -1/-1 counters from it.",
                 'Remove all counters from up to one target creature.')
        for text, required in zip(texts, (NAMED, OPTIONAL)):
            card = compiler_controls.record(text)
            ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile='commander_review')
            self.assertEqual('exact', ir.status, ir.material_residuals)
            node, = ir.faces[0].nodes
            self.assertEqual(text[node.span.start:node.span.end], node.text)
            self.assertIn(required, node.capability_dependencies)
            for missing in (required, 'counter.removal.all_effect', 'target.revalidate_resolution'):
                raw = json.loads((ROOT / 'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
                next(r for r in raw['capabilities'] if r['id'] == missing).update(status='blocked', blockers=['all-counter authority withdrawn'])
                registry = CapabilityRegistry(raw)
                registry.mark_evidence_verified(self.registry.evidence_fingerprint)
                self.assertNotEqual('exact', compile_oracle_card(card,
                    capability_registry=registry, capability_profile='commander_review').status)

    def test_complete_originals_admit_both_faces_and_keep_material_exclusions(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'target-all-originals.sqlite3'
            build_fixture_database([FIXTURE], path)
            with CardDatabase(path) as db:
                semantics = SemanticRegistry()
                for name in (*CANDIDATES, 'Blitz Leech', 'Vampire Hexmage', 'Give // Take', 'Mutated Cultist'):
                    with self.subTest(name=name):
                        card = db.lookup(name, fuzzy=False)
                        register_generated_programs(db, semantics, (card,), trust_level='provisional',
                            capability_registry=self.registry, capability_profile='commander_review',
                            promote_exact_runtime_handlers=True, promote_exact_trigger_programs=True,
                            promote_exact_effect_programs=True, promote_exact_capability_declarations=True)
                        program = compile_best_available_card_program(db, card, semantic_registry=semantics,
                            capability_registry=self.registry, capability_profile='commander_review')
                        binding = bind_card_program_runtime(program, capability_registry=self.registry, profile='commander_review')
                        self.assertEqual(name in (*CANDIDATES, 'Blitz Leech', 'Vampire Hexmage'),
                            bool(binding['strict_capability_ready'] and binding['trust_basis'] == 'capability_closed'), binding['blockers'])
                        if name in CANDIDATES:
                            required = NAMED if name == CANDIDATES[0] else OPTIONAL
                            ability = next(p for p in semantics.programs_for_oracle(card.oracle_id)
                                if 'remove_all_counters' in str(p.effects))
                            deps = tuple(c for c in ability.capability_dependencies if c != required)
                            changed = replace(ability, coverage=(), capability_dependencies=deps,
                                capability_closure=self.registry.closure(deps, profile='commander_review').to_dict())
                            bound = bind_semantic_program_runtime(changed, capability_registry=self.registry,
                                profile='commander_review')
                            self.assertFalse(bound['strict'])
                            self.assertTrue(any(required in blocker for blocker in bound['blockers']), bound['blockers'])
                            with patch('quorune.compiler.counter_removal_templates.target_all_counter_variant_template', return_value=None):
                                self.assertNotEqual('exact', compile_oracle_card(card,
                                    capability_registry=self.registry, capability_profile='commander_review').status)


class TargetAllCounterRuntimeTests(unittest.TestCase):
    register_fixture_cards_on_add = True
    session = controls.AttachmentFollowupRuntimeTests.session
    add = controls.AttachmentFollowupRuntimeTests.add
    prepare = controls.AttachmentFollowupRuntimeTests.prepare
    closed = controls.AttachmentFollowupRuntimeTests.closed
    actions = controls.AttachmentFollowupRuntimeTests.actions
    checkpoint = controls.AttachmentFollowupRuntimeTests.checkpoint
    resolve = controls.AttachmentFollowupRuntimeTests.resolve
    replay = controls.AttachmentFollowupRuntimeTests.replay

    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / 'target-all-native.sqlite3'
        build_fixture_database([ROOT / 'tests/fixtures/bound-effect-program-cards.json', FIXTURE], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('All-counter target variants', [DeckEntry('Generic Bound Commander', 1, 'commander'),
            DeckEntry('Generic Bound Plains', 30)], ['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temporary.cleanup()

    def world(self, seed, name=CANDIDATES[0], *, counters=None, extra=(), target=True):
        session = self.session(seed)
        source = self.add(session.engine, name, zone='hand')
        recipient = self.add(session.engine, 'Generic Bound Body', seat='B', ref='all-counter-recipient') if target else None
        if recipient is not None:
            session.engine.change_control(recipient.object_id, 'A')
            recipient.counters.update({'-1/-1': 3, 'charge': 2} if counters is None else counters)
        opponent = self.add(session.engine, 'Generic Bound Body', seat='B', ref='all-counter-opponent') if target else None
        private = self.add(session.engine, 'Unsummon', seat='D', zone='hand', ref='all-counter-private')
        responses = [self.add(session.engine, n, zone='hand', ref='all-counter-response-' + str(i)) for i, n in enumerate(extra)]
        self.prepare(session, source, {color: 20 for color in 'CWUBRG'})
        self.checkpoint(session)
        return session, source, recipient, opponent, private, responses

    def cast(self, session, card, *, targets=(), face=None, seat='A'):
        self.closed(session, card)
        offered = next(a for a in self.actions(session, seat) if a.get('card') == card.ref
            and (face is None or a.get('face') == face))
        command = {'action_id': offered['id'], 'pay': 'auto'}
        if targets:command['targets'] = list(targets)
        if face is not None:command['face'] = face
        if offered.get('cost_options'):command['cost_option'] = offered['cost_options'][0]['id']
        result = session.act('pilot:' + seat, command)
        self.assertTrue(result.ok, result.summary)

    def reject(self, session, command, *, seat='A'):
        before = authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:' + seat, command).ok)
        self.assertEqual(before, authoritative_state_hash(session.state))

    def select(self, session, targets=()):
        result = session.act('pilot:A', {'action_id': 'choose', 'targets': list(targets)})
        self.assertTrue(result.ok, result.summary)

    def private_replay(self, session, private):
        for seat in 'ABC':self.assertNotIn(private.ref, str(session.packet('pilot:' + seat, full=True)))
        return self.replay(session, load=True)

    def test_actual_hapatra_grants_hexproof_and_removes_only_negative_counters_or_none(self):
        for index, negative in enumerate((3, 0)):
            session, source, target, opponent, private, _ = self.world(302100 + index,
                counters={'-1/-1': negative, 'charge': 2} if negative else {'charge': 2})
            offered = next(a for a in self.actions(session) if a.get('card') == source.ref)
            self.assertIn(target.ref, offered['target_schema']['legal_refs'])
            self.assertNotIn(opponent.ref, offered['target_schema']['legal_refs'])
            self.reject(session, {'action_id': offered['id'], 'targets': [opponent.ref], 'pay': 'auto'})
            self.cast(session, source, targets=(target.ref,))
            session = self.private_replay(session, private)
            self.assertIsNone(self.resolve(session))
            current = session.state.cards[target.object_id]
            self.assertEqual(('B', 'A'), (current.owner, current.controller))
            self.assertEqual({'charge': 2}, dict(current.counters))
            self.assertIn('hexproof', session.engine._combat_keywords(current))
            self.assertEqual(2, session.engine._numeric_stat(current.object_id, 'power'))
            self.private_replay(session, private)

    def test_named_kind_filter_mutation_is_killed_by_a_fresh_original_cast(self):
        original = AllCounterRemovalHandler.lower
        def omit_kind(handler, effect, context):
            plan = original(handler, effect, context)
            return replace(plan, intents=tuple(replace(intent, counter_name=None) for intent in plan.intents))
        with patch.object(AllCounterRemovalHandler, 'lower', omit_kind):
            with self.assertRaises(AssertionError):
                self.test_actual_hapatra_grants_hexproof_and_removes_only_negative_counters_or_none()

    def test_optional_zero_target_mutation_is_killed_by_a_fresh_original_cast(self):
        import quorune.targets as owner
        original = owner._target_count_bounds
        def require_one(raw):
            bounds = original(raw)
            return (1, bounds[1]) if raw.get('up_to') == 1 else bounds
        with patch.object(owner, '_target_count_bounds', require_one):
            with self.assertRaises(AssertionError):
                self.test_actual_purging_uses_zero_targets_or_removes_every_current_counter_kind()

    def test_actual_purging_uses_zero_targets_or_removes_every_current_counter_kind(self):
        for index, empty in enumerate((True, False)):
            session, source, target, _, private, _ = self.world(302110 + index, CANDIDATES[1], target=not empty)
            self.cast(session, source)
            self.assertEqual('semantic.target', self.resolve(session).kind)
            self.assertEqual('battlefield', session.state.cards[source.object_id].zone)
            self.assertIn('flying', session.engine._combat_keywords(session.state.cards[source.object_id]))
            if empty:
                self.reject(session, {'action_id': 'choose', 'targets': [source.ref, source.ref]})
            session = self.private_replay(session, private)
            self.select(session, () if empty else (target.ref,))
            self.assertIsNone(self.resolve(session))
            if target is not None:self.assertFalse(session.state.cards[target.object_id].counters)
            self.private_replay(session, private)

    def test_hapatra_blinked_target_is_not_redirected_to_a_new_incarnation(self):
        session, source, target, _, private, responses = self.world(302120, extra=('Ephemerate',))
        old = session.state.cards[target.object_id].logical_object_id
        self.cast(session, source, targets=(target.ref,))
        self.cast(session, responses[0], targets=(target.ref,))
        self.assertIsNone(self.resolve(session))
        current = session.state.cards[target.object_id]
        self.assertNotEqual(old, current.logical_object_id)
        self.assertNotIn('hexproof', session.engine._combat_keywords(current))
        self.private_replay(session, private)

    def test_purging_source_departure_preserves_its_counter_removal_trigger(self):
        session, source, target, _, private, responses = self.world(302130, CANDIDATES[1], extra=('Unsummon',))
        self.cast(session, source)
        self.assertEqual('semantic.target', self.resolve(session).kind)
        self.select(session, (target.ref,))
        self.cast(session, responses[0], targets=(source.ref,))
        self.assertIsNone(self.resolve(session))
        self.assertEqual('hand', session.state.cards[source.object_id].zone)
        self.assertFalse(session.state.cards[target.object_id].counters)
        self.private_replay(session, private)

    def test_actual_absorb_essence_omen_grants_all_results_and_shuffles_only_on_resolution(self):
        for index, response in enumerate((None, 'Unsummon', 'Counterspell')):
            session, source, target, _, private, responses = self.world(302140 + index, CANDIDATES[1],
                counters={}, extra=() if response is None else (response,))
            before = session.state.players['A'].stats.get('shuffle_count', 0)
            self.cast(session, source, targets=(target.ref,), face='Absorb Essence')
            self.assertEqual('Absorb Essence', session.state.stack[-1].chosen_face)
            if response is not None:
                ref = target.ref if response == 'Unsummon' else session.state.stack[-1].ref
                self.cast(session, responses[0], targets=(ref,))
            self.assertIsNone(self.resolve(session))
            current = session.state.cards[source.object_id]
            self.assertEqual('library' if response is None else 'graveyard', current.zone)
            self.assertEqual(before + int(response is None), session.state.players['A'].stats.get('shuffle_count', 0))
            if response is None:
                self.assertEqual((4, 8), tuple(session.engine._numeric_stat(target.object_id, stat) for stat in ('power', 'toughness')))
                self.assertTrue({'hexproof', 'lifelink'} <= session.engine._combat_keywords(session.state.cards[target.object_id]))
                self.assertEqual(([], []), (current.known_to, current.revealed_to))
            self.private_replay(session, private)

    def test_actual_purging_ward_charges_the_opponent_two_life_or_counters_the_spell(self):
        for index, pay in enumerate((True, False)):
            session = self.session(302150 + index)
            source = self.add(session.engine, CANDIDATES[1])
            response = self.add(session.engine, 'Shock', seat='B', zone='hand')
            private = self.add(session.engine, 'Unsummon', seat='D', zone='hand', ref='ward-private')
            session.state.players['B'].mana_pool['R'] = 1
            self.prepare(session, source, {})
            self.checkpoint(session)
            while session.pending_principals()[0] != 'pilot:B':
                result = session.act(session.pending_principals()[0], {'action_id': 'pass'})
                self.assertTrue(result.ok, result.summary)
            self.cast(session, response, targets=(source.ref,), seat='B')
            self.assertEqual('semantic.choice', self.resolve(session).kind)
            self.assertEqual(['pilot:B'], list(session.pending_principals()))
            self.reject(session, {'action_id': 'choose', 'pay': pay}, seat='A')
            session = self.private_replay(session, private)
            result = session.act('pilot:B', {'action_id': 'choose', 'pay': pay})
            self.assertTrue(result.ok, result.summary)
            self.assertIsNone(self.resolve(session))
            self.assertEqual(38 if pay else 40, session.state.players['B'].life)
            self.assertEqual(2 if pay else 0, session.state.cards[source.object_id].marked_damage)
            self.assertEqual('graveyard', session.state.cards[response.object_id].zone)
            self.private_replay(session, private)
