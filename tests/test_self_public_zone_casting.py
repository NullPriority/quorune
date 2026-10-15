from __future__ import annotations

"""CR 113.6f/117.1a/601.3: extra source zones retain ordinary casting rules."""

import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from common import ROOT
from quorune.carddb import CardDatabase
from quorune.card_programs import bind_card_program_runtime
from quorune.card_programs.adapters import compile_best_available_card_program
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry
from quorune.semantic_runtime.casting_activation_metadata import (
    SELF_PUBLIC_ZONE_CAST_PERMISSION_HANDLER_ID, SELF_PUBLIC_ZONE_CAST_PERMISSION_CAPABILITY,
    SelfPublicZoneCastPermissionHandler, compiled_self_zone_cast_permission,
)
from quorune.compiler.casting_activation_metadata_templates import static_self_zone_cast_permission_handler
from scripts.build_test_database import build_fixture_database
from test_granted_skulk import record
import test_bound_effect_programs as actions


class SelfPublicZoneCompilerTests(unittest.TestCase):
    def test_max_speed_prefix_keeps_its_condition_before_unconditional_permission(self):
        registry = load_default_capability_registry()
        text = 'Max speed — You may cast this card from your graveyard.'
        ir = compile_oracle_card(record(text, 'Creature — Human'), capability_registry=registry,
            capability_profile='commander_review')
        self.assertEqual('exact', ir.status, ir.material_residuals)
        descriptors = [handler for face in ir.faces for node in face.nodes for handler in node.handlers]
        scope = next(handler for handler in descriptors if handler['handler_id'] == 'ability.static.component-scope.v1')
        self.assertEqual('controller_speed_equals', scope['fragment']['value']['applicability']['kind'])
        self.assertEqual(4, scope['fragment']['value']['applicability']['minimum'])
        self.assertTrue(any(handler['handler_id'] == SELF_PUBLIC_ZONE_CAST_PERMISSION_HANDLER_ID for handler in descriptors))

    def test_fixed_zone_declarations_are_complete_and_source_face_bound(self):
        registry = load_default_capability_registry()
        for text, zones in (
            ('You may cast this card from your graveyard.', ['graveyard']),
            ('You may cast this card from exile.', ['exile']),
            ('You may cast this card from your graveyard or from exile.', ['exile', 'graveyard']),
        ):
            with self.subTest(text=text):
                ir = compile_oracle_card(record(text, 'Creature — Human'), capability_registry=registry,
                    capability_profile='commander_review')
                self.assertEqual('exact', ir.status, ir.material_residuals)
                node = ir.faces[0].nodes[0]
                self.assertEqual(text, node.text)
                self.assertEqual('all', node.active_zone)
                self.assertEqual(zones, node.handlers[0]['source_zones'])
                self.assertIn(SELF_PUBLIC_ZONE_CAST_PERMISSION_CAPABILITY, node.capability_dependencies)
                self.assertIsNone(node.target_schema)

    def test_permission_codec_rejects_open_zone_or_admission_authority(self):
        descriptor = static_self_zone_cast_permission_handler('You may cast this card from your graveyard or from exile.')[1]
        handler = SelfPublicZoneCastPermissionHandler()
        self.assertEqual(('exile', 'graveyard'), tuple(p.source_zone for p in handler.lower(descriptor, None)))
        for zones in ([], ['hand'], ['library'], ['exile', 'exile'], ['graveyard', 'exile'], [True]):
            with self.subTest(zones=zones), self.assertRaises(ValueError):
                handler.validate({**descriptor, 'source_zones': zones})
        for field, value in (('schema_version', True), ('requires_complete_card_program', False), ('payer', 'opponent')):
            with self.assertRaises(ValueError):
                handler.validate({**descriptor, field: value})

    def test_restriction_cost_or_condition_tail_and_withdrawn_authority_fail_closed(self):
        registry = load_default_capability_registry()
        for text in ('You may cast this card from your graveyard, but not from anywhere else.',
                     'You may cast this card from exile without paying its mana cost.',
                     'You may cast creature cards from your graveyard.',
                     'You may cast this card from exile if the top card of your library is blue.'):
            ir = compile_oracle_card(record(text, 'Creature — Human'), capability_registry=registry,
                capability_profile='commander_review')
            self.assertNotEqual('exact', ir.status)
            self.assertTrue(ir.material_residuals)
        raw = json.loads((ROOT / 'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        next(row for row in raw['capabilities'] if row['id'] == SELF_PUBLIC_ZONE_CAST_PERMISSION_CAPABILITY).update(
            status='blocked', blockers=['withdrawn'])
        ir = compile_oracle_card(record('You may cast this card from exile.', 'Creature — Human'),
            capability_registry=CapabilityRegistry(raw), capability_profile='commander_review')
        self.assertNotEqual('exact', ir.status)


class SelfPublicZoneRuntimeTests(unittest.TestCase):
    register_fixture_cards_on_add = True
    session = actions.BoundEffectProgramRuntimeTests.session
    add = actions.BoundEffectProgramRuntimeTests.add
    checkpoint = actions.BoundEffectProgramRuntimeTests.checkpoint
    resolve = actions.BoundEffectProgramRuntimeTests.resolve
    replay = actions.BoundEffectProgramRuntimeTests.replay

    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name).resolve() / 'public-zone-casting.sqlite3'
        build_fixture_database([ROOT / 'tests/fixtures/bound-effect-program-cards.json',
            ROOT / 'tests/fixtures/self-public-zone-cards.json', ROOT / 'tests/fixtures/event-player-subject-cards.json'], path)
        cls.db = CardDatabase(path); cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Public-zone permission witness', [DeckEntry('Generic Bound Commander', 1, 'commander'),
            DeckEntry('Generic Bound Plains', 30)], ['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):
        cls.db.close(); cls.temporary.cleanup()

    def prepare(self, session, *, actor='A', active='A', mana=None):
        engine = session.engine
        engine.permissions.invalidate_current(); engine.state.pending_decision = None
        engine.state.started = True; engine.state.active_player = active
        engine.state.phase = 'precombat_main'; engine.state.step = 'main'; engine.state.phase_index = 3
        if mana:
            engine.state.players[actor].mana_pool.update(mana)
        engine._grant_priority(actor); engine.pump()

    def assert_closed(self, session, card):
        program = compile_best_available_card_program(self.db, self.db.lookup(card.printed_name),
            semantic_registry=session.engine.semantics, capability_registry=self.registry, capability_profile='commander_review')
        binding = bind_card_program_runtime(program, capability_registry=self.registry, profile='commander_review')
        self.assertTrue(binding['strict_capability_ready'], binding)
        self.assertEqual('capability_closed', binding['trust_basis'])

    def cast(self, session, seat, card, targets=()):
        for _ in range(16):
            session.engine.pump()
            self.assertEqual('priority', session.state.pending_decision.kind)
            if session.state.priority_player == seat:
                break
            self.assertTrue(session.act(session.pending_principals()[0], {'action_id': 'pass'}).ok)
        else:
            self.fail('Casting principal did not receive priority')
        current = session.state.cards[card.object_id]
        offered = next(row for row in session.packet('pilot:' + seat, full=True)['decision']['ctx']['legal']['actions']
            if row['id'] == 'cast:' + current.ref)
        accepted = session.act('pilot:' + seat, {'action_id': offered['id'], 'targets': list(targets), 'pay': 'auto'})
        self.assertTrue(accepted.ok, accepted.summary)

    def test_actual_squee_hand_graveyard_exile_and_owner_after_foreign_control_replay(self):
        session = self.session(396001); engine = session.engine
        source = self.add(engine, 'Squee, the Immortal', zone='hand', ref='ordinary-squee')
        control = self.add(engine, 'Generic Event Subject Control', seat='D', zone='hand', ref='squee-control')
        self.add(engine, 'Generic Bound Plains', seat='D', ref='squee-control-payment')
        kill = self.add(engine, 'Generic Public Zone Destroy', zone='hand', ref='squee-departure')
        exile = self.add(engine, 'Generic Public Zone Exile', zone='hand', ref='squee-exile')
        private = self.add(engine, 'Generic Bound Growth', seat='C', zone='hand', ref='squee-private')
        lands = [self.add(engine, 'Mountain', ref='squee-red-source-' + str(i)) for i in range(6)]
        self.assert_closed(session, source)
        self.prepare(session, mana={'R': 3}); self.checkpoint(session)
        self.cast(session, 'A', source); self.resolve(session)
        self.assertEqual('battlefield', session.state.cards[source.object_id].zone)
        self.cast(session, 'D', control, [source.ref]); self.resolve(session)
        self.assertEqual('D', session.state.cards[source.object_id].controller)
        self.cast(session, 'A', kill, [source.ref]); self.resolve(session)
        current = session.state.cards[source.object_id]
        self.assertEqual('graveyard', current.zone)
        self.assertTrue(compiled_self_zone_cast_permission(session.engine, 'A', current))
        self.assertFalse(compiled_self_zone_cast_permission(session.engine, 'D', current))
        before = authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:D', {'action_id': 'cast:' + current.ref, 'pay': 'auto'}).ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        old_identity = current.logical_object_id
        self.cast(session, 'A', source)
        session = self.replay(session, load=True); self.resolve(session)
        self.assertEqual('battlefield', session.state.cards[source.object_id].zone)
        self.assertNotEqual(old_identity, session.state.cards[source.object_id].logical_object_id)
        self.assertEqual('A', session.state.cards[source.object_id].controller)
        self.cast(session, 'A', exile, [source.ref]); self.resolve(session)
        self.assertEqual('exile', session.state.cards[source.object_id].zone)
        self.cast(session, 'A', source)
        session = self.replay(session, load=True); self.resolve(session)
        self.assertEqual('battlefield', session.state.cards[source.object_id].zone)
        self.assertFalse(session.state.cards[source.object_id].annotations.get('cast_via_flashback', False))
        self.assertEqual(0, sum(not session.state.cards[land.object_id].tapped for land in lands))
        self.assertEqual(0, sum(session.state.players['A'].mana_pool.values()))
        for viewer in 'ABD':
            self.assertNotIn(private.ref, json.dumps(session.packet('pilot:' + viewer, full=True)))
        self.replay(session, load=True)

    def test_actual_exile_permission_retains_timing_owner_visibility_and_complete_admission(self):
        for boundary in ('normal', 'wrong_turn', 'face_down', 'foreign_owner', 'incomplete'):
            with self.subTest(boundary=boundary):
                session = self.session(396010 + len(boundary)); engine = session.engine
                name = 'Generic Incomplete Public Zone Caster' if boundary == 'incomplete' else 'Misthollow Griffin'
                card = self.add(engine, name, seat='B' if boundary == 'foreign_owner' else 'A', zone='exile', ref='exiled-zone-source')
                card.face_down = boundary == 'face_down'
                if not card.face_down:
                    card.known_to = list(engine.seats); card.revealed_to = list(engine.seats)
                self.prepare(session, active='B' if boundary == 'wrong_turn' else 'A', mana={'U': 4})
                self.checkpoint(session)
                allowed = compiled_self_zone_cast_permission(engine, 'A', card)
                self.assertEqual(boundary in {'normal', 'wrong_turn'}, allowed)
                actions_now = session.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']
                offered = [row for row in actions_now if row['id'] == 'cast:' + card.ref]
                self.assertEqual(boundary == 'normal', bool(offered))
                if boundary == 'face_down':
                    for viewer in 'BCD':
                        self.assertNotIn('Misthollow Griffin', json.dumps(session.packet('pilot:' + viewer, full=True)))
                if boundary == 'normal':
                    self.assert_closed(session, card)
                    self.cast(session, 'A', card)
                    session = self.replay(session, load=True); self.resolve(session)
                    self.assertEqual('battlefield', session.state.cards[card.object_id].zone)
                    self.assertEqual(0, sum(session.state.players['A'].mana_pool.values()))
                else:
                    before = authoritative_state_hash(session.state)
                    self.assertFalse(session.act('pilot:A', {'action_id': 'cast:' + card.ref, 'pay': 'auto'}).ok)
                    self.assertEqual(before, authoritative_state_hash(session.state))
                self.replay(session, load=True)

    def test_wrong_source_face_or_missing_whole_program_certificate_supplies_no_permission(self):
        session = self.session(396099)
        source = self.add(session.engine, 'Squee, the Immortal', zone='graveyard', ref='pinned-source-face')
        self.assertTrue(compiled_self_zone_cast_permission(session.engine, 'A', source))
        with mock.patch('quorune.semantic_runtime.casting_activation_metadata.program_matches_face', return_value=False):
            self.assertFalse(compiled_self_zone_cast_permission(session.engine, 'A', source))
        with mock.patch('quorune.semantic_runtime.casting_activation_metadata.program_has_complete_card_program_admission', return_value=False):
            self.assertFalse(compiled_self_zone_cast_permission(session.engine, 'A', source))

    def test_max_speed_component_scope_gates_actual_graveyard_casting(self):
        for speed in (3, 4):
            with self.subTest(speed=speed):
                session = self.session(396110 + speed)
                card = self.add(session.engine, 'Generic Speed Public Zone Caster', zone='graveyard', ref='speed-scoped-card')
                session.state.players['A'].stats['speed'] = speed
                self.prepare(session); self.checkpoint(session)
                self.assertEqual(speed == 4, compiled_self_zone_cast_permission(session.engine, 'A', card))
                offers = session.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']
                self.assertEqual(speed == 4, any(row['id'] == 'cast:' + card.ref for row in offers))
                if speed == 4:
                    self.cast(session, 'A', card); self.resolve(session)
                    self.assertEqual('battlefield', session.state.cards[card.object_id].zone)
                else:
                    before = authoritative_state_hash(session.state)
                    self.assertFalse(session.act('pilot:A', {'action_id': 'cast:' + card.ref, 'pay': 'auto'}).ok)
                    self.assertEqual(before, authoritative_state_hash(session.state))
                self.replay(session, load=True)
