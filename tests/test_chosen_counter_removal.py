"""CR122.1/608.2d and original rulings: choose a present kind at resolution."""
from dataclasses import replace
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import json
import tempfile
from pathlib import Path

from common import ROOT
from quorune.carddb import CardDatabase
from quorune.card_programs.adapters import compile_best_available_card_program
from quorune.card_programs.binding import bind_card_program_runtime, bind_semantic_program_runtime
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card, register_generated_programs
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry
from quorune.semantics import SemanticRegistry
from scripts.build_test_database import build_fixture_database
import test_attachment_followup_bindings as controls
import test_bound_effect_programs as compiler_controls
import inherited_composition_fixture as combat

from quorune.chosen_counter_removal_model import ChosenCounterRemovalSpec
from quorune.compiler.counter_removal_templates import fixed_counter_removal_effect_template
from quorune.object_query import ObjectQueryResult
from quorune.replacement.immutable import FrozenMap
from quorune.rules.chosen_counter_removal import chosen_counter_removal_node_capabilities
from quorune.semantic_choices.chosen_counter_removal import ChosenCounterRemovalHandler
from quorune.semantic_choices.model import SemanticChoiceError

FIXTURE = ROOT / 'tests/fixtures/chosen-counter-removal-cards.json'
CAPABILITY = 'counter.removal.chosen_kind'
ORIGINALS = ('Medicine Runner', 'Ferropede', 'Spinal Parasite', 'Thrull Parasite')


class ChosenCounterRemovalPureTests(unittest.TestCase):
    def world(self, counters=None):
        target = ObjectQueryResult(object_id='target:1', ref='target', printed_name='Public counter recipient',
            owner='B', controller='C', zone='battlefield', types=('artifact',),
            counters=FrozenMap({'charge': 2, 'shield': 1} if counters is None else counters),
            logical_object_id='target:1@battlefield:1')
        box = [target]
        query = SimpleNamespace(object=lambda ref, zones: box[0] if ref == 'target' else None,
            stack_object=lambda ref: SimpleNamespace(controller='A') if ref == 'stack:1' else None,
            active_seats=tuple('ABCD'))
        context = SimpleNamespace(actor='A', stack_controller='A', stack_ref='stack:1', query=query)
        effect = {**ChosenCounterRemovalSpec().instruction(), 'card': 'target', 'source': 'source'}
        return ChosenCounterRemovalHandler(), effect, context, box

    def continuation(self, preparation):
        return SimpleNamespace(effect=preparation.continuation_effect, stack_ref='stack:1',
            semantic_frame=SimpleNamespace(controller='A'))

    def test_unnamed_counter_codec_and_target_domains_are_closed(self):
        spec = ChosenCounterRemovalSpec()
        self.assertEqual(('$target.0', '$source'), spec.decode(spec.instruction()))
        for changed in ({'schema_version': True}, {'schema_version': 2}, {'card': '$target.1'},
                        {'amount': 2}, {'player': '$controller'}, {'source': '$event.card'}):
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                spec.decode({**spec.instruction(), **changed})
        for text in ('Remove a counter from target permanent.', 'Remove a counter from target nonland permanent.'):
            template = fixed_counter_removal_effect_template(text)
            _, effects, schema, mechanics = template.compiled()
            self.assertIn('counter.removal.chosen_kind', chosen_counter_removal_node_capabilities(
                effects=effects, target_schema=schema, mechanic_ids=mechanics))
            for invalid in ({**schema, 'count': True}, {**schema, 'count': 2}, {**schema, 'unknown': True}):
                self.assertFalse(chosen_counter_removal_node_capabilities(
                    effects=effects, target_schema=invalid, mechanic_ids=mechanics))
        for text in ('Remove two counters from target permanent.', 'Remove a counter from target creature.',
                     'Remove up to one counter from target permanent.', 'Remove a counter from target player.'):
            self.assertIsNone(fixed_counter_removal_effect_template(text))

    def test_resolution_controller_chooses_a_kind_without_mutating_public_counters(self):
        handler, effect, context, box = self.world()
        preparation = handler.prepare(effect, context)
        self.assertEqual(('charge', 'shield'), preparation.request.choice.legal_values)
        self.assertEqual({'charge': 2, 'shield': 1}, dict(box[0].counters))
        completion = handler.complete(self.continuation(preparation), {'counter_kind': 'charge'}, context.query)
        intent, = completion.intents
        self.assertEqual(('A', 'target', 'charge', 1, 'source'),
            (intent.actor, intent.object_ref, intent.counter_name, intent.amount, intent.source_ref))
        self.assertEqual({'charge': 2, 'shield': 1}, dict(box[0].counters))
        with self.assertRaises(SemanticChoiceError):
            handler.prepare(effect, SimpleNamespace(**{**vars(context), 'actor': 'B'}))

    def test_no_counter_and_unavailable_recipient_skip_the_impossible_choice(self):
        for counters in ({}, {'charge': 0}):
            handler, effect, context, _ = self.world(counters)
            preparation = handler.prepare(effect, context)
            self.assertIsNone(preparation.request)
            self.assertIsNotNone(preparation.auto_continue)
        handler, effect, context, box = self.world()
        box[0] = replace(box[0], phased_out=True)
        self.assertIsNone(handler.prepare(effect, context).request)
        with self.assertRaises(SemanticChoiceError):
            box[0] = replace(box[0], phased_out=False, logical_object_id='')
            handler.prepare(effect, context)

    def test_choices_use_resolution_time_kinds_and_reject_stale_or_forged_responses(self):
        handler, effect, context, box = self.world()
        box[0] = replace(box[0], counters=FrozenMap({'stun': 3}))
        preparation = handler.prepare(effect, context)
        self.assertEqual(('stun',), preparation.request.choice.legal_values)
        continuation = self.continuation(preparation)
        for response in ({'counter_kind': 'charge'}, {'counter_kind': True}, {'counter_kind': ['stun']},
                         {'counter_kind': 'stun', 'amount': 2}):
            with self.subTest(response=response), self.assertRaises(SemanticChoiceError):
                handler.complete(continuation, response, context.query)
        box[0] = replace(box[0], counters=FrozenMap())
        with self.assertRaises(SemanticChoiceError):
            handler.complete(continuation, {'counter_kind': 'stun'}, context.query)
        box[0] = replace(box[0], counters=FrozenMap({'stun': 3}), logical_object_id='target:1@battlefield:2')
        with self.assertRaises(SemanticChoiceError):
            handler.complete(continuation, {'counter_kind': 'stun'}, context.query)

    def test_counter_kind_filter_mutation_is_detected_before_any_intent(self):
        handler, effect, context, _ = self.world({'charge': 0, 'shield': 1})
        self.assertEqual(('shield',), handler.prepare(effect, context).request.choice.legal_values)
        with patch('quorune.semantic_choices.chosen_counter_removal._counter_kinds',
                   return_value=('charge', 'shield')):
            with self.assertRaises(AssertionError):
                self.assertEqual(('shield',), handler.prepare(effect, context).request.choice.legal_values)


class ChosenCounterRemovalCompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registry = load_default_capability_registry()

    def test_full_originals_close_and_unsupported_counts_or_siblings_remain_material(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'chosen-originals.sqlite3'
            build_fixture_database([FIXTURE], path)
            with CardDatabase(path) as db:
                semantics = SemanticRegistry()
                for name in (*ORIGINALS, 'Vampire Hexmage', 'Price of Betrayal', 'Purging Stormbrood // Absorb Essence'):
                    with self.subTest(name=name):
                        card = db.lookup(name, fuzzy=False)
                        register_generated_programs(db, semantics, (card,), trust_level='provisional',
                            capability_registry=self.registry, capability_profile='commander_review',
                            promote_exact_runtime_handlers=True, promote_exact_trigger_programs=True,
                            promote_exact_effect_programs=True, promote_exact_capability_declarations=True)
                        program = compile_best_available_card_program(db, card, semantic_registry=semantics,
                            capability_registry=self.registry, capability_profile='commander_review')
                        binding = bind_card_program_runtime(program, capability_registry=self.registry, profile='commander_review')
                        self.assertEqual(name in (*ORIGINALS, 'Vampire Hexmage', 'Purging Stormbrood // Absorb Essence'),
                            bool(binding['strict_capability_ready'] and binding['trust_basis'] == 'capability_closed'), binding['blockers'])
                        if name in ORIGINALS:
                            ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile='commander_review')
                            self.assertEqual('exact', ir.status, ir.material_residuals)
                            ability = next(p for p in semantics.programs_for_oracle(card.oracle_id)
                                if 'remove_chosen_counter' in str(p.effects))
                            deps = tuple(c for c in ability.capability_dependencies if c != CAPABILITY)
                            changed = replace(ability, coverage=(), capability_dependencies=deps,
                                capability_closure=self.registry.closure(deps, profile='commander_review').to_dict())
                            bound = bind_semantic_program_runtime(changed, capability_registry=self.registry,
                                profile='commander_review')
                            self.assertFalse(bound['strict'])
                            self.assertTrue(any(CAPABILITY in x for x in bound['blockers']), bound['blockers'])
                            changed = replace(ability, target_schema={**ability.target_schema, 'count': 2})
                            bound = bind_semantic_program_runtime(changed, capability_registry=self.registry,
                                profile='commander_review')
                            self.assertFalse(bound['strict'])
                            with patch('quorune.compiler.counter_removal_templates.chosen_counter_removal_effect_template', return_value=None):
                                self.assertNotEqual('exact', compile_oracle_card(card,
                                    capability_registry=self.registry, capability_profile='commander_review').status)

    def test_source_spans_primitive_authorities_and_payload_minima_are_independent(self):
        record = compiler_controls.record('Remove a counter from target nonland permanent.')
        ir = compile_oracle_card(record, capability_registry=self.registry, capability_profile='commander_review')
        self.assertEqual('exact', ir.status, ir.material_residuals)
        node, = ir.faces[0].nodes
        self.assertEqual(record.oracle_text[node.span.start:node.span.end], node.text)
        for missing in (CAPABILITY, 'counter.removal.fixed_effect', 'target.revalidate_resolution'):
            raw = json.loads((ROOT / 'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
            next(r for r in raw['capabilities'] if r['id'] == missing).update(status='blocked', blockers=['counter choice authority withdrawn'])
            registry = CapabilityRegistry(raw)
            registry.mark_evidence_verified(self.registry.evidence_fingerprint)
            self.assertNotEqual('exact', compile_oracle_card(record,
                capability_registry=registry, capability_profile='commander_review').status)


class ChosenCounterRemovalRuntimeTests(combat.InheritedNativeFixture, unittest.TestCase):
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
        path = Path(cls.temporary.name) / 'chosen-counter.sqlite3'
        build_fixture_database([ROOT / 'tests/fixtures/bound-effect-program-cards.json', FIXTURE], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Chosen counter removal', [DeckEntry('Generic Bound Commander', 1, 'commander'),
            DeckEntry('Generic Bound Plains', 30)], ['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    def world(self, seed, name='Medicine Runner', *, zone='hand', counters=None, responses=()):
        session = self.session(seed)
        source = self.add(session.engine, name, zone=zone)
        source.acquired_control_turn_count = -1
        target = self.add(session.engine, 'Generic Bound Body', seat='B', ref='chosen-counter-target')
        target.counters.update({'charge': 2, 'shield': 1} if counters is None else counters)
        private = self.add(session.engine, 'Unsummon', seat='D', zone='hand', ref='counter-private')
        extra = [self.add(session.engine, n, zone='hand', ref='counter-response-' + str(i)) for i, n in enumerate(responses)]
        self.prepare(session, source, {'C': 20, 'W': 10, 'U': 10, 'B': 10, 'R': 10, 'G': 10})
        self.checkpoint(session)
        return session, source, target, private, extra

    def reject(self, session, command, seat='A'):
        before = authoritative_state_hash(session.state)
        result = session.act('pilot:' + seat, command)
        self.assertFalse(result.ok, result.summary)
        self.assertEqual(before, authoritative_state_hash(session.state))

    def cast_offered(self, session, card, *, targets=(), seat='A'):
        self.closed(session, card)
        offered = next(a for a in self.actions(session, seat) if a['id'] == 'cast:' + card.ref)
        command = {'action_id': offered['id'], 'pay': 'auto'}
        if targets:command['targets'] = list(targets)
        if offered.get('cost_options'):command['cost_option'] = offered['cost_options'][0]['id']
        result = session.act('pilot:' + seat, command)
        self.assertTrue(result.ok, result.summary)

    def choose(self, session, **fields):
        result = session.act('pilot:A', {'action_id': 'choose', **fields})
        self.assertTrue(result.ok, result.summary)

    def target_optional(self, session, target, *, apply=True):
        self.assertEqual('semantic.target', self.resolve(session).kind)
        self.choose(session, targets=[target.ref])
        self.assertEqual('semantic.choice', self.resolve(session).kind)
        self.choose(session, choice='apply' if apply else 'decline')

    def kind(self, session, name='charge'):
        self.assertEqual('semantic.choice', self.resolve(session).kind)
        ctx = session.packet('pilot:A', full=True)['decision']['ctx']
        self.assertEqual('remove_chosen_counter', ctx['operation'])
        self.choose(session, counter_kind=name)
        self.assertIsNone(self.resolve(session))

    def private_replay(self, session, private):
        for seat in 'ABC':self.assertNotIn(private.ref, str(session.packet('pilot:' + seat, full=True)))
        return self.replay(session, load=True)

    def activate(self, session, source, target):
        offered = next(a for a in self.actions(session) if a['id'].startswith('activate:' + source.ref + ':'))
        result = session.act('pilot:A', {'action_id': offered['id'], 'targets': [target.ref], 'pay': 'auto'})
        self.assertTrue(result.ok, result.summary)

    def test_original_medicine_runner_chooses_the_resolution_kind_and_replays(self):
        session, source, target, private, _ = self.world(301100)
        self.cast_offered(session, source)
        self.target_optional(session, target)
        for fields in ({'counter_kind': 'missing'}, {'counter_kind': ['charge']}, {'counter_kind': 'charge', 'amount': 2}):
            self.reject(session, {'action_id': 'choose', **fields})
        self.reject(session, {'action_id': 'choose', 'counter_kind': 'charge'}, seat='B')
        session = self.private_replay(session, private)
        self.kind(session)
        self.assertEqual({'charge': 1, 'shield': 1}, dict(session.state.cards[target.object_id].counters))
        self.assertEqual('battlefield', session.state.cards[source.object_id].zone)
        self.private_replay(session, private)

    def test_counterless_targets_and_optional_decline_remain_available(self):
        for index, (counters, apply) in enumerate((({}, True), ({'charge': 2}, False))):
            session, source, target, private, _ = self.world(301110 + index, counters=counters)
            self.cast_offered(session, source)
            self.target_optional(session, target, apply=apply)
            self.assertIsNone(self.resolve(session))
            self.assertEqual(counters, dict(session.state.cards[target.object_id].counters))
            self.private_replay(session, private)

    def test_counterless_runtime_mutation_is_killed_by_a_fresh_original_cast(self):
        with patch('quorune.semantic_choices.chosen_counter_removal._counter_kinds', return_value=('charge',)):
            with self.assertRaises(AssertionError):
                self.test_counterless_targets_and_optional_decline_remain_available()

    def test_actual_counter_placement_response_changes_the_available_kind_at_resolution(self):
        session, source, target, private, responses = self.world(301115, responses=('Battlegrowth',))
        self.cast_offered(session, source)
        self.assertEqual('semantic.target', self.resolve(session).kind)
        self.choose(session, targets=[target.ref])
        self.assertNotIn('+1/+1', session.state.cards[target.object_id].counters)
        self.cast_offered(session, responses[0], targets=(target.ref,))
        self.assertEqual('semantic.choice', self.resolve(session).kind)
        self.assertEqual(1, session.state.cards[target.object_id].counters['+1/+1'])
        self.choose(session, choice='apply')
        session = self.private_replay(session, private)
        self.kind(session, '+1/+1')
        self.assertEqual({'charge': 2, 'shield': 1}, dict(session.state.cards[target.object_id].counters))
        self.private_replay(session, private)

    def test_original_spinal_parasite_pays_source_counters_before_removing_one(self):
        session, source, target, private, _ = self.world(301120, 'Spinal Parasite')
        offered = next(a for a in self.actions(session) if a['id'] == 'cast:' + source.ref)
        result = session.act('pilot:A', {'action_id': offered['id'], 'pay': 'manual',
            'payment': {color: 1 for color in 'WUBRG'}})
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(session))
        self.assertEqual(5, session.state.cards[source.object_id].counters['+1/+1'])
        self.activate(session, source, target)
        self.assertEqual(3, session.state.cards[source.object_id].counters['+1/+1'])
        session = self.private_replay(session, private)
        self.kind(session, 'shield')
        self.assertEqual({'charge': 2}, dict(session.state.cards[target.object_id].counters))
        self.assertEqual(3, session.state.cards[source.object_id].counters['+1/+1'])
        self.private_replay(session, private)

    def test_original_thrull_parasite_pays_life_and_tap_for_nonland_target(self):
        session, source, target, private, _ = self.world(301130, 'Thrull Parasite', zone='battlefield', counters={})
        land = self.add(session.engine, 'Generic Bound Plains', seat='B', ref='counter-land-boundary')
        self.prepare(session, source, {})
        self.checkpoint(session)
        offered = next(a for a in self.actions(session) if a['id'].startswith('activate:' + source.ref + ':'))
        self.assertIn(target.ref, offered['target_schema']['legal_refs'])
        self.assertNotIn(land.ref, offered['target_schema']['legal_refs'])
        self.reject(session, {'action_id': offered['id'], 'targets': [land.ref], 'pay': 'auto'})
        self.activate(session, source, target)
        self.assertTrue(session.state.cards[source.object_id].tapped)
        self.assertEqual(38, session.state.players['A'].life)
        self.assertIsNone(self.resolve(session))
        self.assertFalse(session.state.cards[target.object_id].counters)
        self.private_replay(session, private)

    def test_original_ferropede_combat_trigger_uses_its_controller(self):
        session, source, target, private, _ = self.world(301140, 'Ferropede', zone='battlefield')
        self.attack(session, [(source, 'B')])
        for _ in range(80):
            decision = session.state.pending_decision
            if decision.kind == 'semantic.target':break
            if decision.kind == 'combat.blockers':command = {'action_id': 'block', 'blocks': {}}
            else:
                self.assertEqual('priority', decision.kind)
                command = {'action_id': 'pass'}
            result = session.act(session.pending_principals()[0], command)
            self.assertTrue(result.ok, result.summary)
        self.assertEqual(39, session.state.players['B'].life)
        self.target_optional(session, target)
        self.kind(session)
        self.assertEqual({'charge': 1, 'shield': 1}, dict(session.state.cards[target.object_id].counters))
        self.private_replay(session, private)

    def test_target_blink_fizzles_and_source_departure_keeps_the_queued_choice(self):
        for index, blink in enumerate((False, True)):
            session, source, target, private, responses = self.world(301150 + index,
                responses=('Unsummon',) if not blink else ('Ephemerate',))
            if blink:
                session.engine.change_control(target.object_id, 'A')
                self.prepare(session, source, {'W': 1})
                self.checkpoint(session)
            self.cast_offered(session, source)
            self.assertEqual('semantic.target', self.resolve(session).kind)
            self.choose(session, targets=[target.ref])
            old = session.state.cards[target.object_id].logical_object_id
            self.cast_offered(session, responses[0], targets=((target if blink else source).ref,))
            if blink:
                self.assertIsNone(self.resolve(session))
                self.assertNotEqual(old, session.state.cards[target.object_id].logical_object_id)
                self.assertFalse(session.state.cards[target.object_id].counters)
            else:
                self.assertEqual('semantic.choice', self.resolve(session).kind)
                self.choose(session, choice='apply')
                self.kind(session)
                self.assertEqual('hand', session.state.cards[source.object_id].zone)
                self.assertEqual({'charge': 1, 'shield': 1}, dict(session.state.cards[target.object_id].counters))
            self.private_replay(session, private)
