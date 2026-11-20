"""CR121.3/508.5: the sealed defender chooses its own private optional draw."""
from dataclasses import replace
from types import SimpleNamespace
import unittest
import tempfile
import json
from pathlib import Path
from unittest.mock import patch

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
import inherited_composition_fixture as combat
import test_attachment_followup_bindings as controls
import test_bound_effect_programs as compiler_controls

from quorune.compiler.event_optional_draw_results import (
    fixed_event_optional_draw_result, EVENT_OPTIONAL_DRAW_FIELD as FIELD,
    EVENT_OPTIONAL_DRAW_CAPABILITY as CAPABILITY,
)
from quorune.compiler.fixed_counter_trigger_nodes import fixed_counter_trigger_binding
from quorune.drawing import DrawPermission
from quorune.replacement.immutable import thaw_value
from quorune.rules.event_optional_draw_shapes import (
    validate_event_optional_draw_payload, event_optional_draw_node_capabilities,
    event_optional_draw_runtime_dependencies,
)
from quorune.semantic_choices.model import SemanticChoiceError
from quorune.semantic_choices.optional_draw import OptionalDrawHandler
import test_optional_draw_choices as old

FIXTURE = ROOT / 'tests/fixtures/event-player-optional-draw-cards.json'
CANDIDATES = ('Sibilant Spirit', 'Harbor Guardian')


def compiled(body='defending player may draw a card.'):
    binding = fixed_counter_trigger_binding('Whenever this creature attacks, ' + body, card_name='Source')
    return fixed_event_optional_draw_result(binding, body, effect_template=None, card_name='Source')


class EventOptionalDrawModelTests(unittest.TestCase):
    def snapshot(self, permission=None):
        return old.query(permission_a=DrawPermission('A', 0), permission_b=permission or DrawPermission('B', 0))

    def effect(self):
        return {'op': 'offer_draw', 'player': 'B', 'drawer': 'B', 'count': 1, 'private': True, FIELD: 1}

    def test_fixed_draw_lowering_reuses_one_sealed_defender_and_rejects_unknown_siblings(self):
        result, flag = compiled()
        self.assertFalse(flag)
        self.assertEqual('$context.defending_player', result[1][0]['player'])
        self.assertEqual(result[1][0]['player'], result[1][0]['drawer'])
        self.assertIn(CAPABILITY, event_optional_draw_node_capabilities(effects=result[1],
            target_schema=result[2], mechanic_ids=result[3]))
        for body in ('defending player may draw X cards.', 'defending player may draw a card. You gain 2 life.',
                     'defending player draws a card.', 'each opponent may draw a card.'):
            self.assertIsNone(compiled(body))

    def test_drawer_chooses_despite_different_stack_controller_and_marker_roundtrips(self):
        snapshot = self.snapshot()
        handler = OptionalDrawHandler()
        effect = self.effect()
        preparation = handler.prepare(effect, old.context(snapshot, actor='B'))
        self.assertEqual(('draw', 'decline'), preparation.request.choice.legal_values)
        self.assertEqual(effect, thaw_value(preparation.continuation_effect))
        continuation = old.continuation(preparation.continuation_effect)
        self.assertEqual('A', continuation.semantic_frame.controller)
        completion = handler.complete(continuation, {'choice': 'draw'}, snapshot)
        self.assertEqual(({'op': 'draw', 'player': 'B', 'count': 1, 'private': True},),
            tuple(thaw_value(value) for value in completion.prepend_effects))
        self.assertFalse(handler.complete(continuation, {'choice': 'decline'}, snapshot).prepend_effects)
        with self.assertRaises(SemanticChoiceError):handler.prepare(effect, old.context(snapshot, actor='A'))

    def test_empty_library_preserves_choice_but_prohibition_removes_it(self):
        handler = OptionalDrawHandler()
        effect = self.effect()
        snapshot = self.snapshot()
        self.assertIsNotNone(handler.prepare(effect, old.context(snapshot, actor='B')).request)
        prohibited = self.snapshot(DrawPermission('B', 0, 0, ('fixture:cannot-draw',)))
        prepared = handler.prepare(effect, old.context(prohibited, actor='B'))
        self.assertIsNone(prepared.request)
        self.assertIsNotNone(prepared.auto_continue)

    def test_version_roles_and_response_fields_fail_closed_without_changing_legacy(self):
        handler = OptionalDrawHandler()
        snapshot = self.snapshot()
        effect = self.effect()
        for changed in ({FIELD: True}, {FIELD: 3}, {'drawer': 'A'}, {'private': False}, {'count': True}, {'extra': 1}):
            with self.subTest(changed=changed), self.assertRaises(SemanticChoiceError):
                handler.prepare({**effect, **changed}, old.context(snapshot, actor='B'))
        continuation = old.continuation(handler.prepare(effect, old.context(snapshot, actor='B')).continuation_effect)
        for response in ({'choice': 'draw', 'drawer': 'A'}, {'choice': True}, {'choice': 'draw', FIELD: 2}):
            with self.assertRaises(SemanticChoiceError):handler.complete(continuation, response, snapshot)
        legacy = {'op': 'offer_draw', 'player': 'A', 'drawer': 'B', 'count': 1, 'private': True}
        prepared = handler.prepare(legacy, old.context(snapshot))
        self.assertEqual(legacy, thaw_value(prepared.continuation_effect))
        self.assertNotIn(FIELD, prepared.continuation_effect)

    def test_runtime_owner_requires_complete_original_attack_binding_without_coverage(self):
        result, _ = compiled()
        effect = result[1][0]
        program = SimpleNamespace(effects=(effect,), event='creature.attacks', active_zone='battlefield',
            target_schema=None, event_condition={'field': 'card', 'op': 'eq', 'value': '$source.ref'}, coverage=())
        self.assertIn(CAPABILITY, event_optional_draw_runtime_dependencies(program))
        for changed in ({'event': 'spell.cast'}, {'event_condition': None}, {'active_zone': 'graveyard'},
                        {'target_schema': {'count': 1}}):
            with self.assertRaises(ValueError):
                event_optional_draw_runtime_dependencies(SimpleNamespace(**{**vars(program), **changed}))
        for changed in ({'player': '$controller'}, {'drawer': '$context.target'}, {FIELD: True}, {'count': 0}):
            with self.assertRaises(ValueError):validate_event_optional_draw_payload({**effect, **changed})


class EventOptionalDrawCompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.registry = load_default_capability_registry()

    def test_fixed_defender_grammar_keeps_exact_span_and_requires_each_authority(self):
        text = 'Whenever this creature attacks, defending player may draw a card.'
        card = compiler_controls.record(text, 'Creature — Spirit')
        ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile='commander_review')
        self.assertEqual('exact', ir.status, ir.material_residuals)
        node, = ir.faces[0].nodes
        self.assertEqual(text[node.span.start:node.span.end], node.text)
        for missing in (CAPABILITY, 'resolution.event_player.fixed_result', 'zone.draw.library_to_hand',
                        'trigger.event.normalized_self_attack'):
            raw = json.loads((ROOT / 'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
            next(r for r in raw['capabilities'] if r['id'] == missing).update(status='blocked', blockers=['event draw authority withdrawn'])
            registry = CapabilityRegistry(raw)
            registry.mark_evidence_verified(self.registry.evidence_fingerprint)
            self.assertNotEqual('exact', compile_oracle_card(card,
                capability_registry=registry, capability_profile='commander_review').status)

    def test_complete_originals_admit_without_conditional_or_compound_sibling_loss(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'event-draw-originals.sqlite3'
            build_fixture_database([FIXTURE], path)
            with CardDatabase(path) as db:
                semantics = SemanticRegistry()
                for name in (*CANDIDATES, 'Ludevic, Necro-Alchemist', 'Academy Loremaster', 'Fungal Shambler', 'Notion Thief'):
                    card = db.lookup(name, fuzzy=False)
                    register_generated_programs(db, semantics, (card,), trust_level='provisional',
                        capability_registry=self.registry, capability_profile='commander_review',
                        promote_exact_runtime_handlers=True, promote_exact_trigger_programs=True,
                        promote_exact_effect_programs=True, promote_exact_capability_declarations=True)
                    program = compile_best_available_card_program(db, card, semantic_registry=semantics,
                        capability_registry=self.registry, capability_profile='commander_review')
                    binding = bind_card_program_runtime(program, capability_registry=self.registry, profile='commander_review')
                    self.assertEqual(name in CANDIDATES,
                        bool(binding['strict_capability_ready'] and binding['trust_basis'] == 'capability_closed'), binding['blockers'])
                    if name in CANDIDATES:
                        ability = next(p for p in semantics.programs_for_oracle(card.oracle_id)
                            if any(FIELD in effect for effect in p.effects))
                        deps = tuple(c for c in ability.capability_dependencies if c != CAPABILITY)
                        changed = replace(ability, coverage=(), capability_dependencies=deps,
                            capability_closure=self.registry.closure(deps, profile='commander_review').to_dict())
                        bound = bind_semantic_program_runtime(changed, capability_registry=self.registry, profile='commander_review')
                        self.assertFalse(bound['strict'])
                        self.assertTrue(any(CAPABILITY in x for x in bound['blockers']), bound['blockers'])
                        with patch('quorune.compiler.event_optional_draw_results.fixed_event_optional_draw_result', return_value=None):
                            self.assertNotEqual('exact', compile_oracle_card(card,
                                capability_registry=self.registry, capability_profile='commander_review').status)


class EventOptionalDrawRuntimeTests(combat.InheritedNativeFixture, unittest.TestCase):
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
        path = Path(cls.temporary.name) / 'event-draw-native.sqlite3'
        build_fixture_database([ROOT / 'tests/fixtures/bound-effect-program-cards.json', FIXTURE], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Defender optional draws', [DeckEntry('Generic Bound Commander', 1, 'commander'),
            DeckEntry('Generic Bound Plains', 30)], ['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temporary.cleanup()

    def world(self, seed, name=CANDIDATES[0], *, extras=(), empty=False):
        session = self.session(seed)
        source = self.add(session.engine, name, zone='hand')
        haste = self.add(session.engine, 'Fervor')
        self.closed(session, haste)
        self.add(session.engine, 'Island', ref='draw-response-island-A')
        for index in range(3):self.add(session.engine, 'Island', seat='C', ref='draw-response-island-C-' + str(index))
        self.add(session.engine, 'Mountain', seat='C', ref='draw-response-mountain-C')
        private = self.add(session.engine, 'Unsummon', seat='D', zone='hand', ref='draw-private-control')
        rows = [self.add(session.engine, card, seat=seat, zone=zone, ref='draw-extra-' + str(i))
                for i, (card, seat, zone) in enumerate(extras)]
        for row in rows:
            record = self.db.by_oracle_id(row.oracle_id)
            if record.loyalty is not None:row.counters['loyalty'] = int(record.loyalty)
            if record.defense is not None:
                row.counters['defense'] = int(record.defense)
                row.battle_protector = 'D'
            self.closed(session, row)
        if empty:
            for object_id in tuple(session.state.players['B'].zones['library']):
                card = session.state.cards[object_id]
                session.state.players['B'].zones['library'].remove(object_id)
                session.state.players['B'].zones['graveyard'].append(object_id)
                card.zone = 'graveyard'
                card.zone_timestamp = session.engine._next_zone_timestamp()
                card.known_to = list('ABCD')
                card.revealed_to = list('ABCD')
        for seat in 'BCD':session.state.players[seat].mana_pool.update({color: 20 for color in 'CWUBRG'})
        self.prepare(session, source, {color: 20 for color in 'CWUBRG'})
        from quorune.engine import TURN_STEPS
        session.state.phase_index = TURN_STEPS.index(('precombat_main', 'main'))
        self.checkpoint(session)
        self.cast_card(session, source)
        self.assertIsNone(self.resolve(session))
        return session, source, private, rows

    def cast_card(self, session, card, *, seat='A', targets=()):
        self.closed(session, card)
        offered = next(a for a in self.actions(session, seat) if a.get('card') == card.ref)
        command = {'action_id': offered['id'], 'pay': 'auto'}
        if targets:command['targets'] = list(targets)
        if offered.get('cost_options'):command['cost_option'] = offered['cost_options'][0]['id']
        result = session.act('pilot:' + seat, command)
        self.assertTrue(result.ok, result.summary)

    def priority_for(self, session, seat):
        for _ in range(12):
            if session.pending_principals()[0] == 'pilot:' + seat:return
            self.assertEqual('priority', session.state.pending_decision.kind)
            result = session.act(session.pending_principals()[0], {'action_id': 'pass'})
            self.assertTrue(result.ok, result.summary)
        self.fail('Priority did not reach selected responder')

    def reject(self, session, command, seat='A'):
        before = authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:' + seat, command).ok)
        self.assertEqual(before, authoritative_state_hash(session.state))

    def choose(self, session, seat, choice):
        result = session.act('pilot:' + seat, {'action_id': 'choose', 'choice': choice})
        self.assertTrue(result.ok, result.summary)

    def private_replay(self, session, private):
        for seat in 'ABC':self.assertNotIn(private.ref, str(session.packet('pilot:' + seat, full=True)))
        return self.replay(session, load=True)

    def test_both_originals_cast_attack_and_offer_only_the_defender_accept_or_decline(self):
        for index, (name, choice) in enumerate(((CANDIDATES[0], 'draw'), (CANDIDATES[1], 'decline'))):
            session, source, private, _ = self.world(303100 + index, name)
            self.assertIn('flying' if index == 0 else 'reach', session.engine._combat_keywords(session.state.cards[source.object_id]))
            before = len(session.state.players['B'].zones['hand'])
            self.attack(session, [(source, 'B')])
            self.assertEqual('semantic.choice', self.resolve(session).kind)
            self.assertEqual(['pilot:B'], list(session.pending_principals()))
            for seat in 'ACD':self.reject(session, {'action_id': 'choose', 'choice': 'draw'}, seat)
            self.reject(session, {'action_id': 'choose', 'choice': 'draw', 'drawer': 'A'}, 'B')
            self.reject(session, {'action_id': 'choose', 'choice': True}, 'B')
            session = self.private_replay(session, private)
            self.choose(session, 'B', choice)
            self.assertIsNone(self.resolve(session))
            self.assertEqual(before + int(choice == 'draw'), len(session.state.players['B'].zones['hand']))
            if choice == 'draw':
                ref = session.state.cards[session.state.players['B'].zones['hand'][-1]].ref
                self.assertIn(ref, str(session.packet('pilot:B', full=True)))
                for seat in 'ACD':self.assertNotIn(ref, str(session.packet('pilot:' + seat, full=True)))
            self.private_replay(session, private)

    def test_planeswalker_controller_and_battle_protector_are_the_specific_defenders(self):
        for index, battle in enumerate((False, True)):
            name = 'Generic Defender Battle' if battle else 'Elspeth, Knight-Errant'
            session, source, private, extras = self.world(303110 + index, extras=((name, 'B', 'battlefield'),))
            target = extras[0]
            session.engine.change_control(target.object_id, 'C')
            if battle:
                target.battle_protector = 'D'
                target.counters['defense'] = 20
            else:target.counters['loyalty'] = 10
            self.prepare(session, session.state.cards[source.object_id], {})
            self.checkpoint(session)
            defender = 'D' if battle else 'C'
            before = len(session.state.players[defender].zones['hand'])
            self.attack(session, [(session.state.cards[source.object_id], target.ref)])
            self.assertEqual('semantic.choice', self.resolve(session).kind)
            self.assertEqual(['pilot:' + defender], list(session.pending_principals()))
            self.reject(session, {'action_id': 'choose', 'choice': 'draw'}, 'B')
            self.choose(session, defender, 'draw')
            self.assertIsNone(self.resolve(session))
            self.assertEqual(before + 1, len(session.state.players[defender].zones['hand']))
            self.private_replay(session, private)

    def test_source_departure_and_new_control_do_not_change_the_queued_defender(self):
        for index, control in enumerate((False, True)):
            extra = ('Blind with Anger', 'C', 'hand') if control else ('Unsummon', 'A', 'hand')
            session, source, private, extras = self.world(303120 + index, extras=(extra,))
            self.attack(session, [(source, 'B')])
            seat = 'C' if control else 'A'
            self.priority_for(session, seat)
            self.cast_card(session, extras[0], seat=seat, targets=(source.ref,))
            self.assertEqual('semantic.choice', self.resolve(session).kind)
            self.assertEqual(['pilot:B'], list(session.pending_principals()))
            current = session.state.cards[source.object_id]
            self.assertEqual('C' if control else 'A', current.controller)
            self.assertEqual('battlefield' if control else 'hand', current.zone)
            self.choose(session, 'B', 'decline')
            self.assertIsNone(self.resolve(session))
            self.private_replay(session, private)

    def test_two_attacks_bind_different_defenders_and_preserve_independent_choices(self):
        session, source, private, extras = self.world(303130, extras=((CANDIDATES[1], 'A', 'battlefield'),))
        other = extras[0]
        self.attack(session, [(source, 'B'), (other, 'C')])
        seen = set()
        for _ in range(2):
            self.assertEqual('semantic.choice', self.resolve(session).kind)
            seat = session.pending_principals()[0].removeprefix('pilot:')
            self.assertIn(seat, 'BC')
            seen.add(seat)
            self.choose(session, seat, 'draw' if seat == 'B' else 'decline')
        self.assertEqual({'B', 'C'}, seen)
        self.assertIsNone(self.resolve(session))
        self.private_replay(session, private)

    def test_empty_library_can_be_chosen_and_draw_restriction_suppresses_the_option(self):
        session, source, private, _ = self.world(303140, empty=True)
        self.attack(session, [(source, 'B')])
        self.assertEqual('semantic.choice', self.resolve(session).kind)
        self.choose(session, 'B', 'draw')
        self.assertIsNone(self.resolve(session))
        self.assertFalse(session.state.players['B'].in_game)
        self.private_replay(session, private)
        session, source, private, extras = self.world(303141,
            extras=(('Spirit of the Labyrinth', 'A', 'battlefield'), ('Reach Through Mists', 'B', 'hand')))
        self.priority_for(session, 'B')
        self.cast_card(session, extras[1], seat='B')
        self.assertIsNone(self.resolve(session))
        before = len(session.state.players['B'].zones['hand'])
        self.attack(session, [(source, 'B')])
        self.assertIsNone(self.resolve(session))
        self.assertEqual(before, len(session.state.players['B'].zones['hand']))
        self.private_replay(session, private)

    def test_draw_doubling_preserves_defender_choice_and_private_actual_drawer(self):
        session, source, private, _ = self.world(303150, extras=(('Thought Reflection', 'B', 'battlefield'),))
        hands = {seat: len(session.state.players[seat].zones['hand']) for seat in 'ABC'}
        self.attack(session, [(source, 'B')])
        self.assertEqual('semantic.choice', self.resolve(session).kind)
        self.assertEqual(['pilot:B'], list(session.pending_principals()))
        self.choose(session, 'B', 'draw')
        self.assertIsNone(self.resolve(session))
        self.assertEqual(hands['B'] + 2, len(session.state.players['B'].zones['hand']))
        self.assertEqual(hands['A'], len(session.state.players['A'].zones['hand']))
        self.assertEqual(hands['C'], len(session.state.players['C'].zones['hand']))
        for object_id in session.state.players['B'].zones['hand'][-2:]:
            ref = session.state.cards[object_id].ref
            self.assertIn(ref, str(session.packet('pilot:B', full=True)))
            for seat in 'ACD':self.assertNotIn(ref, str(session.packet('pilot:' + seat, full=True)))
        self.private_replay(session, private)

    def test_missing_defender_rebind_is_killed_by_a_fresh_original_attack(self):
        original = OptionalDrawHandler.prepare
        def change_to_controller(handler, effect, context):
            return original(handler, {**effect, 'player': context.stack_controller,
                'drawer': context.stack_controller}, context)
        with patch.object(OptionalDrawHandler, 'prepare', change_to_controller):
            with self.assertRaises(AssertionError):
                self.test_both_originals_cast_attack_and_offer_only_the_defender_accept_or_decline()
