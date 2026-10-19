from __future__ import annotations

"""Pinned CR 702.146/712.8c: Disturb casts the back face for its own cost."""

import json
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from common import ROOT
from quorune.carddb import CardDatabase, CardRecord
from quorune.card_programs.admission import REQUIRES_COMPLETE_CARD_PROGRAM_FIELD
from quorune.card_programs import bind_card_program_runtime
from quorune.card_programs.adapters import compile_best_available_card_program
from quorune.deck import DeckDefinition, DeckEntry
from quorune.disturb_model import DISTURB_CAPABILITY, FixedManaDisturbSpec
from quorune.oracle_ir import compile_oracle_card, register_generated_programs
from quorune.replacement.model import ReplacementClass
from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry
from quorune.self_graveyard_exile_model import SELF_GRAVEYARD_EXILE_CAPABILITY, SELF_GRAVEYARD_EXILE_HANDLER, self_graveyard_exile_descriptor
from quorune.semantics import SemanticRegistry
from quorune.util import mana_cost_to_vector
from scripts.build_test_database import build_fixture_database
import test_spree_mode_costs as actions
import test_self_public_zone_casting as zone_actions


FIXTURE = ROOT / 'tests/fixtures/fixed-disturb-cards.json'


class FixedDisturbModelCompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registry = load_default_capability_registry()

    def record(self, front='Disturb {1}{W}', back=None, **changes):
        back = back if back is not None else 'Flying\nIf Disturb Back would be put into a graveyard from anywhere, exile it instead.'
        record = CardRecord(oracle_id='fixture:disturb-boundary', name='Disturb Front // Disturb Back',
            mana_cost='{W}', mana_value=1, type_line='Creature — Human', oracle_text='', power='1', toughness='1',
            loyalty=None, defense=None, colors=('W',), color_identity=('W',), keywords=('Disturb', 'Flying'),
            produced_mana=(), layout='transform', released_at='2026-01-01', legalities={'commander': 'legal'}, raw={},
            faces=({'name': 'Disturb Front', 'mana_cost': '{W}', 'oracle_text': front,
                    'type_line': 'Creature — Human', 'power': '1', 'toughness': '1', 'colors': ['W']},
                {'name': 'Disturb Back', 'mana_cost': '', 'oracle_text': back,
                    'type_line': 'Creature — Spirit', 'power': '1', 'toughness': '1', 'colors': ['W']}))
        return replace(record, **changes)

    def compile(self, record=None, registry=None):
        return compile_oracle_card(record or self.record(), capability_registry=registry or self.registry,
            capability_profile='commander_review')

    def test_disturb_declaration_is_immutable_and_has_closed_faces_prices_and_codec(self):
        vector = mana_cost_to_vector('{1}{W}')[0]
        spec = FixedManaDisturbSpec('front:disturb:line:1', 'Disturb Front', 'Disturb Back', vector)
        vector['GENERIC'] = 99
        self.assertEqual(1, spec.requirements['GENERIC'])
        self.assertEqual(spec, FixedManaDisturbSpec.from_dict(spec.to_dict()))
        for change in ({'extra': True}, {'schema_version': True}, {'front_face': ''}, {'back_face': 'Disturb Front'},
                {'requirements': {'GENERIC': 1}}, {'requirements': {**dict(spec.requirements), 'W': True}}):
            with self.assertRaises(ValueError):
                FixedManaDisturbSpec.from_dict({**spec.to_dict(), **change})

    def test_disturb_cost_and_back_replacement_keep_separate_source_spans_and_capabilities(self):
        ir = self.compile()
        self.assertEqual('exact', ir.status, ir.material_residuals)
        front = ir.faces[0].nodes[0]
        self.assertEqual('Disturb {1}{W}', front.text)
        self.assertIn(DISTURB_CAPABILITY, front.capability_dependencies)
        self.assertEqual((0, len(front.text)), (front.span.start, front.span.end))
        back = ir.faces[1].nodes[-1]
        self.assertIn(SELF_GRAVEYARD_EXILE_CAPABILITY, back.capability_dependencies)
        self.assertNotIn(DISTURB_CAPABILITY, back.capability_dependencies)
        from quorune.semantic_runtime.disturb import FixedManaDisturbHandler
        self.assertEqual('Disturb Back', FixedManaDisturbHandler().validate(front.handlers[0]).back_face)

    def test_unknown_disturb_variants_bodies_and_missing_owners_fail_closed(self):
        for record in (self.record(front='Disturb {X}{W}'), self.record(front='Disturb {W/U}'),
                self.record(front='Disturb {1}{W}\nDisturb {W}'), self.record(layout='modal_dfc'),
                self.record(back='Flying\nRewrite history.'), self.record(back='Disturb {W}')):
            with self.subTest(record=record):
                self.assertNotEqual('exact', self.compile(record).status)
        raw = json.loads((ROOT / 'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        for cap in (DISTURB_CAPABILITY, SELF_GRAVEYARD_EXILE_CAPABILITY, 'zone.change.destination_replacement'):
            changed = deepcopy(raw)
            next(row for row in changed['capabilities'] if row['id'] == cap).update(status='blocked', blockers=['owner omitted'])
            self.assertNotEqual('exact', self.compile(registry=CapabilityRegistry(changed)).status)

    def test_source_local_exile_is_not_a_self_replacement_priority_and_binds_one_incarnation(self):
        from quorune.semantic_runtime.self_graveyard_exile import SelfGraveyardExileHandler
        handler = SelfGraveyardExileHandler()
        descriptor = self_graveyard_exile_descriptor()
        for change in ({'extra': 1}, {'schema_version': True}, {REQUIRES_COMPLETE_CARD_PROGRAM_FIELD: False}):
            with self.assertRaises(ValueError):
                handler.validate({**descriptor, **change})
        for kind in ('card', 'token', 'spell_copy'):
            subject = SimpleNamespace(destination='graveyard', object_ref='R1', logical_object_id='object:1@2',
                is_card_object=kind == 'card')
            effect, = handler.subject_replacement_effects(descriptor, subject=subject, component_id='ability:1')
            self.assertEqual(ReplacementClass.OTHER, effect.replacement_class)
            self.assertEqual({'eq': 'object:1@2'}, effect.conditions['logical_object_id'])
        subject.destination = 'hand'
        self.assertEqual((), handler.subject_replacement_effects(descriptor, subject=subject, component_id='ability:1'))

    def test_disturb_producer_omission_is_detected_by_the_positive_contract(self):
        with mock.patch('quorune.compiler.disturb_nodes.printed_disturb_spec', return_value=None):
            with self.assertRaises(AssertionError):
                self.test_disturb_cost_and_back_replacement_keep_separate_source_spans_and_capabilities()

    def test_source_exile_producer_omission_is_detected_by_the_positive_contract(self):
        with mock.patch('quorune.compiler.self_graveyard_exile_nodes.self_graveyard_exile_node', return_value=None):
            with self.assertRaises(AssertionError):
                self.test_disturb_cost_and_back_replacement_keep_separate_source_spans_and_capabilities()


class FixedDisturbRuntimeTests(unittest.TestCase):
    register_fixture_cards_on_add = True
    session = actions.SpreeModeCostRuntimeTests.session
    add = actions.SpreeModeCostRuntimeTests.add
    checkpoint = actions.SpreeModeCostRuntimeTests.checkpoint
    resolve = actions.SpreeModeCostRuntimeTests.resolve
    replay = actions.SpreeModeCostRuntimeTests.replay
    prepare = actions.SpreeModeCostRuntimeTests.prepare
    assert_closed = actions.SpreeModeCostRuntimeTests.assert_closed
    reject = actions.SpreeModeCostRuntimeTests.reject
    cast = zone_actions.SelfPublicZoneRuntimeTests.cast

    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        path = Path(cls.temp.name) / 'disturb.sqlite3'
        build_fixture_database([ROOT / 'tests/fixtures/bound-effect-program-cards.json', FIXTURE,
            ROOT / 'tests/fixtures/target-restriction-cards.json'], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Disturb witness', [DeckEntry('Generic Bound Commander', 1, 'commander'),
            DeckEntry('Generic Bound Plains', 30)], ['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temp.cleanup()

    def disturb(self, session, card, targets=(), **payment):
        for _ in range(16):
            if session.state.priority_player == 'A':
                break
            result = session.act(session.pending_principals()[0], {'action_id': 'pass'})
            self.assertTrue(result.ok, result.summary)
        current = session.state.cards[card.object_id]
        back = str(self.db.lookup(current.printed_name).faces[1]['name'])
        action_id = f'cast:{current.ref}:face:{back}'
        self.assertIn(action_id, [row['id'] for row in session.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']])
        result = session.act('pilot:A', {'action_id': action_id, 'face': back, 'cost_option': 'disturb',
            'targets': list(targets), 'pay': 'auto', **payment})
        self.assertTrue(result.ok, result.summary)

    def test_actual_disturb_cast_uses_back_face_and_its_alternative_cost(self):
        session = self.session(70214601)
        engine = session.engine
        source = self.add(engine, 'Lunarch Veteran // Luminous Phantom', zone='graveyard', ref='disturb-source')
        private = self.add(engine, 'Generic Bound Growth', seat='C', zone='hand', ref='disturb-private')
        self.assert_closed(session, source)
        self.prepare(session, mana={'C': 1, 'W': 1})
        back = 'Luminous Phantom'
        action_id = f'cast:{source.ref}:face:{back}'
        offered = next(row for row in session.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']
            if row['id'] == action_id)
        self.assertEqual(['disturb'], [row['id'] for row in offered['cost_options']])
        self.checkpoint(session)
        self.assertNotIn(private.ref, json.dumps(session.packet('pilot:B', full=True)))
        self.reject(session, action_id, 'D', face=back, cost_option='disturb', pay='auto')
        self.reject(session, action_id, face=back, cost_option='normal', pay='auto')
        result = session.act('pilot:A', {'action_id': action_id, 'face': back,
            'cost_option': 'disturb', 'pay': 'auto'})
        self.assertTrue(result.ok, result.summary)
        current = session.state.cards[source.object_id]
        self.assertEqual(('stack', back), (current.zone, current.active_face))
        self.assertEqual(1, engine._effective_card_data(current)['mana_value'])
        self.assertEqual('', engine._effective_card_data(current)['mana_cost'])
        self.assertEqual(0, session.state.players['A'].mana_pool.get('C', 0))
        self.assertEqual(0, session.state.players['A'].mana_pool.get('W', 0))
        session = self.replay(session, load=True)
        self.resolve(session)
        current = session.state.cards[source.object_id]
        self.assertEqual(('battlefield', back), (current.zone, current.active_face))
        self.assertEqual((1, 1), (session.engine._numeric_stat(current.object_id, 'power'),
            session.engine._numeric_stat(current.object_id, 'toughness')))
        self.assertIn('flying', session.engine._combat_keywords(current))
        self.replay(session)

    def test_actual_countered_disturb_back_spell_goes_to_exile(self):
        session = self.session(70214602)
        engine = session.engine
        source = self.add(engine, 'Baithook Angler // Hook-Haunt Drifter', zone='graveyard', ref='disturb-countered')
        counter = self.add(engine, 'Counterspell', seat='B', zone='hand', ref='disturb-counter')
        self.assert_closed(session, source)
        engine.state.players['B'].mana_pool['U'] = 2
        self.prepare(session, mana={'C': 1, 'U': 1})
        self.checkpoint(session)
        self.disturb(session, source)
        spell_ref = session.state.stack[-1].ref
        self.cast(session, 'B', counter, [spell_ref])
        session = self.replay(session, load=True)
        self.resolve(session)
        current = session.state.cards[source.object_id]
        self.assertEqual('exile', current.zone)
        self.assertIsNone(current.active_face)
        self.replay(session)

    def test_actual_bounced_back_returns_front_and_does_not_cast_back_from_hand(self):
        session = self.session(70214603)
        engine = session.engine
        source = self.add(engine, 'Lunarch Veteran // Luminous Phantom', zone='graveyard', ref='disturb-bounced')
        bounce = self.add(engine, 'Unsummon', zone='hand', ref='disturb-bounce')
        self.assert_closed(session, source)
        self.prepare(session, mana={'C': 2, 'W': 2, 'U': 1})
        self.checkpoint(session)
        self.disturb(session, source, reserve={'U': 1})
        self.resolve(session)
        self.cast(session, 'A', bounce, [session.state.cards[source.object_id].ref])
        self.resolve(session)
        current = session.state.cards[source.object_id]
        self.assertEqual('hand', current.zone)
        self.assertIsNone(current.active_face)
        actions = session.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']
        self.assertNotIn(f'cast:{current.ref}:face:Luminous Phantom', [row['id'] for row in actions])
        self.reject(session, 'cast:' + current.ref, face='Luminous Phantom', cost_option='disturb', pay='auto')
        self.cast(session, 'A', current)
        self.resolve(session)
        current = session.state.cards[source.object_id]
        self.assertEqual('Lunarch Veteran', current.active_face)
        self.assertNotIn('flying', session.engine._combat_keywords(current))
        self.replay(session, load=True)

    def test_actual_disturb_aura_uses_back_targets_and_cost_modifier_type(self):
        session = self.session(70214604)
        engine = session.engine
        source = self.add(engine, "Lantern Bearer // Lanterns' Lift", zone='graveyard', ref='disturb-aura')
        self.add(engine, 'Transcendent Envoy', ref='disturb-aura-reducer')
        wrong = self.add(engine, 'Sphere of Resistance', ref='disturb-aura-increase')
        target = self.add(engine, 'Generic Bound Body', seat='B', ref='disturb-aura-target')
        self.assert_closed(session, source)
        self.prepare(session, mana={'C': 2, 'U': 1})
        self.checkpoint(session)
        back = "Lanterns' Lift"
        action_id = f'cast:{source.ref}:face:{back}'
        self.reject(session, action_id, face=back, cost_option='disturb', targets=[wrong.ref], pay='auto')
        self.disturb(session, source, [target.ref])
        session = self.replay(session, load=True)
        self.resolve(session)
        current = session.state.cards[source.object_id]
        self.assertEqual(('battlefield', back, target.object_id), (current.zone, current.active_face, current.attached_to))
        self.assertEqual(1, session.engine._effective_card_data(current)['mana_value'])
        self.assertEqual((3, 7), (session.engine._numeric_stat(target.object_id, 'power'),
            session.engine._numeric_stat(target.object_id, 'toughness')))
        self.assertIn('flying', session.engine._combat_keywords(session.state.cards[target.object_id]))
        self.assertEqual(0, sum(session.state.players['A'].mana_pool.values()))
        self.replay(session)

    def test_actual_disturb_aura_target_departure_exiles_on_stack_and_after_attachment(self):
        for attached in (False, True):
            with self.subTest(attached=attached):
                session = self.session(70214620 + attached)
                engine = session.engine
                source = self.add(engine, "Lantern Bearer // Lanterns' Lift", zone='graveyard', ref='disturb-departing-aura')
                target = self.add(engine, 'Generic Bound Body', seat='B', ref='disturb-departing-target')
                bounce = self.add(engine, 'Unsummon', seat='B', zone='hand', ref='disturb-target-bounce')
                private = self.add(engine, 'Generic Bound Growth', seat='C', zone='hand', ref='disturb-departure-private')
                self.assert_closed(session, source)
                engine.state.players['B'].mana_pool['U'] = 1
                self.prepare(session, mana={'C': 2, 'U': 1})
                self.checkpoint(session)
                self.assertNotIn(private.ref, json.dumps(session.packet('pilot:D', full=True)))
                self.disturb(session, source, [target.ref])
                if attached:
                    self.resolve(session)
                    current = session.state.cards[source.object_id]
                    self.assertEqual(target.object_id, current.attached_to)
                    self.assertEqual(7, session.engine._numeric_stat(target.object_id, 'toughness'))
                self.cast(session, 'B', bounce, [session.state.cards[target.object_id].ref])
                self.reject(session, 'pass', 'D')
                session = self.replay(session, load=True)
                self.resolve(session)
                current = session.state.cards[source.object_id]
                self.assertEqual(('exile', None, None), (current.zone, current.active_face, current.attached_to))
                self.assertEqual('hand', session.state.cards[target.object_id].zone)
                self.assertFalse(session.state.stack)
                self.replay(session)

    def test_actual_cutpurse_damage_history_target_then_disturb_and_back_exile(self):
        session = self.session(70214622)
        engine = session.engine
        source = self.add(engine, 'Covert Cutpurse // Covetous Geist', zone='hand', ref='disturb-cutpurse')
        target = self.add(engine, 'Generic Bound Body', seat='B', ref='disturb-damaged-target')
        untouched = self.add(engine, 'Generic Bound Body', seat='C', ref='disturb-undamaged-target')
        shock = self.add(engine, 'Shock', zone='hand', ref='disturb-history-shock')
        front_kill = self.add(engine, 'Murder', zone='hand', ref='disturb-front-kill')
        back_kill = self.add(engine, 'Murder', zone='hand', ref='disturb-back-kill')
        private = self.add(engine, 'Generic Bound Growth', seat='D', zone='hand', ref='disturb-history-private')
        self.assert_closed(session, source)
        self.prepare(session, mana={'C': 10, 'B': 6, 'R': 1})
        self.checkpoint(session)
        self.cast(session, 'A', shock, [target.ref])
        self.resolve(session)
        self.assertEqual('battlefield', session.state.cards[target.object_id].zone)
        self.cast(session, 'A', source)
        pending = self.resolve(session)
        self.assertEqual('semantic.target', pending.kind)
        offered = json.dumps(session.packet('pilot:A', full=True)['decision']['ctx']['target_schema'])
        self.assertIn(target.ref, offered)
        self.assertNotIn(untouched.ref, offered)
        self.assertNotIn(private.ref, json.dumps(session.packet('pilot:B', full=True)))
        self.reject(session, 'choose', targets=[untouched.ref])
        self.reject(session, 'choose', 'C', targets=[target.ref])
        accepted = session.act('pilot:A', {'action_id': 'choose', 'targets': [target.ref]})
        self.assertTrue(accepted.ok, accepted.summary)
        session = self.replay(session, load=True)
        self.resolve(session)
        self.assertEqual('graveyard', session.state.cards[target.object_id].zone)
        self.assertEqual('battlefield', session.state.cards[untouched.object_id].zone)
        self.cast(session, 'A', front_kill, [session.state.cards[source.object_id].ref])
        self.resolve(session)
        self.assertEqual('graveyard', session.state.cards[source.object_id].zone)
        self.disturb(session, source, reserve={'B': 2})
        self.resolve(session)
        self.assertEqual('Covetous Geist', session.state.cards[source.object_id].active_face)
        self.cast(session, 'A', back_kill, [session.state.cards[source.object_id].ref])
        session = self.replay(session, load=True)
        self.resolve(session)
        self.assertEqual('exile', session.state.cards[source.object_id].zone)
        self.replay(session)

    def test_generic_free_permission_does_not_waive_intrinsic_disturb_alternative(self):
        from quorune.impulse_access import grant_temporary_cast_permission
        from quorune.impulse_access_model import TemporaryCastPermissionGrant, ImpulseAccessDuration
        session = self.session(70214605)
        engine = session.engine
        source = self.add(engine, 'Lunarch Veteran // Luminous Phantom', zone='graveyard', ref='disturb-free-boundary')
        self.assert_closed(session, source)
        grant_temporary_cast_permission(engine, card=source,
            grant=TemporaryCastPermissionGrant(player='A', duration=ImpulseAccessDuration.END_OF_TURN,
                not_before_turn_sequence=None, without_mana_cost=True, source='disturb-typed-permission-boundary'),
            reason='Existing generic cast permission does not itself authorize a free transformed cast')
        self.prepare(session, mana={'C': 1, 'W': 1})
        self.checkpoint(session)
        back = 'Luminous Phantom'
        action_id = f'cast:{source.ref}:face:{back}'
        offered = next(row for row in session.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']
            if row['id'] == action_id)
        self.assertEqual(['disturb'], [row['id'] for row in offered['cost_options']])
        self.reject(session, action_id, face=back, cost_option='without_mana_cost', pay='auto')
        self.reject(session, action_id, face=back, cost_option='disturb', pay='manual', payment={})
        self.disturb(session, source)
        self.assertEqual(0, sum(session.state.players['A'].mana_pool.values()))
        self.resolve(session)
        self.replay(session, load=True)

    def test_actual_back_copy_inherits_exile_without_disturb_payment_and_humble_removes_it(self):
        for removed in (False, True):
            with self.subTest(removed=removed):
                session = self.session(70214606 + removed)
                engine = session.engine
                source = self.add(engine, 'Baithook Angler // Hook-Haunt Drifter', zone='graveyard', ref='disturb-copy-source')
                clone = self.add(engine, 'Clone', zone='hand', ref='disturb-physical-copy')
                kill = self.add(engine, 'Murder', zone='hand', ref='disturb-kill-copy')
                humble = self.add(engine, 'Humble', zone='hand', ref='disturb-remove-copy-ability') if removed else None
                self.assert_closed(session, source)
                self.prepare(session, mana={'C': 7, 'U': 2, 'B': 2, 'W': int(removed)})
                self.checkpoint(session)
                self.disturb(session, source, reserve={'U': 1, 'B': 2})
                self.resolve(session)
                original = session.state.cards[source.object_id]
                self.cast(session, 'A', clone)
                self.assertEqual('replacement.order', self.resolve(session).kind)
                session = self.replay(session, load=True)
                option = next(row for row in session.packet('pilot:A', full=True)['decision']['ctx']['options']
                    if original.ref in row['id'] and not row['id'].startswith('decline:'))
                result = session.act('pilot:A', {'action_id': 'choose', 'replacement': option['id']})
                self.assertTrue(result.ok, result.summary)
                self.resolve(session)
                copied = session.state.cards[clone.object_id]
                self.assertEqual(0, session.engine._effective_card_data(copied)['mana_value'])
                self.assertEqual(2, session.engine._effective_card_data(session.state.cards[source.object_id])['mana_value'])
                if removed:
                    self.cast(session, 'A', humble, [copied.ref])
                    self.resolve(session)
                    self.assertNotIn('flying', session.engine._combat_keywords(session.state.cards[clone.object_id]))
                self.cast(session, 'A', kill, [session.state.cards[clone.object_id].ref])
                self.resolve(session)
                self.assertEqual('graveyard' if removed else 'exile', session.state.cards[clone.object_id].zone)
                self.assertEqual('battlefield', session.state.cards[source.object_id].zone)
                self.replay(session, load=True)

    def test_actual_token_back_copy_exiles_instead_of_dying(self):
        from quorune.turn_history import current_turn_history_events
        session = self.session(70214608)
        engine = session.engine
        source = self.add(engine, 'Baithook Angler // Hook-Haunt Drifter', zone='graveyard', ref='disturb-token-source')
        copy = self.add(engine, 'Cackling Counterpart', zone='hand', ref='disturb-token-spell')
        control = self.add(engine, 'Generic Bound Body', ref='disturb-death-control')
        control_kill = self.add(engine, 'Murder', zone='hand', ref='disturb-control-kill')
        token_kill = self.add(engine, 'Murder', zone='hand', ref='disturb-token-kill')
        self.assert_closed(session, source)
        self.prepare(session, mana={'C': 8, 'U': 3, 'B': 4})
        self.checkpoint(session)
        self.disturb(session, source, reserve={'U': 2, 'B': 4})
        self.resolve(session)
        self.cast(session, 'A', copy, [session.state.cards[source.object_id].ref])
        self.resolve(session)
        tokens = [card for card in session.state.cards.values() if card.is_token and card.zone == 'battlefield']
        self.assertEqual(1, len(tokens))
        token = tokens[0]
        self.assertEqual(0, session.engine._effective_card_data(token)['mana_value'])
        control_identity = session.state.cards[control.object_id].logical_object_id
        self.cast(session, 'A', control_kill, [control.ref])
        self.resolve(session)
        events = lambda kind: current_turn_history_events(session.state.turn_history,
            turn_sequence=session.state.turn_sequence, kind=kind)
        self.assertIn(control_identity, [event.object_incarnation for event in events('creature_died')])
        token_identity = session.state.cards[token.object_id].logical_object_id
        self.cast(session, 'A', token_kill, [session.state.cards[token.object_id].ref])
        self.resolve(session)
        self.assertIn(token_identity, [event.object_incarnation for event in events('permanent_left')])
        self.assertNotIn(token_identity, [event.object_incarnation for event in events('creature_died')])
        self.replay(session, load=True)

    def test_disturb_price_omission_keeps_admission_but_fails_native_payment_consequences(self):
        from quorune.rules.casting import costs
        original = costs.disturb_base_options

        def free(*args, **kwargs):
            options = original(*args, **kwargs)
            for option in options:
                option['requirements'] = mana_cost_to_vector('{0}')[0]
            return options

        witness = type(self)('test_actual_disturb_cast_uses_back_face_and_its_alternative_cost')
        result = unittest.TestResult()
        with mock.patch.object(costs, 'disturb_base_options', free):
            witness.run(result)
        self.assertEqual(1, result.testsRun)
        self.assertFalse(result.errors)
        self.assertEqual(1, len(result.failures))
        self.assertIn("mana_pool.get('C'", result.failures[0][1])

    def test_actual_source_and_dauthi_replacements_remain_an_affected_player_choice(self):
        for intrinsic in (False, True):
            with self.subTest(intrinsic=intrinsic):
                session = self.session(70214609 + intrinsic)
                engine = session.engine
                source = self.add(engine, 'Baithook Angler // Hook-Haunt Drifter', zone='graveyard', ref='disturb-replacement-source')
                self.add(engine, 'Dauthi Voidwalker', seat='B', ref='disturb-other-replacement')
                kill = self.add(engine, 'Murder', zone='hand', ref='disturb-competing-kill')
                self.assert_closed(session, source)
                self.prepare(session, mana={'C': 2, 'U': 1, 'B': 2})
                self.checkpoint(session)
                self.disturb(session, source, reserve={'B': 2})
                self.resolve(session)
                self.cast(session, 'A', kill, [session.state.cards[source.object_id].ref])
                pending = self.resolve(session)
                self.assertEqual('replacement.order', pending.kind)
                self.assertEqual(['A'], pending.actors)
                session = self.replay(session, load=True)
                options = session.packet('pilot:A', full=True)['decision']['ctx']['options']
                self.assertEqual(2, len(options))
                option = next(row for row in options if (row['id'].startswith(SELF_GRAVEYARD_EXILE_HANDLER)
                    if intrinsic else 'disturb-other-replacement' in row['id']))
                self.reject(session, 'choose', 'B', replacement=option['id'])
                result = session.act('pilot:A', {'action_id': 'choose', 'replacement': option['id']})
                self.assertTrue(result.ok, result.summary)
                self.resolve(session)
                current = session.state.cards[source.object_id]
                self.assertEqual('exile', current.zone)
                self.assertEqual(0 if intrinsic else 1, current.counters.get('void', 0))
                self.replay(session)

    def test_original_disturb_cards_need_both_complete_faces_and_normal_registration(self):
        expected = {'Lunarch Veteran // Luminous Phantom', 'Devoted Grafkeeper // Departed Soulkeeper',
            'Overwhelmed Archivist // Archive Haunt', 'Drogskol Infantry // Drogskol Armaments',
            'Mischievous Catgeist // Catlike Curiosity', 'Twinblade Geist // Twinblade Invocation',
            'Malevolent Hermit // Benevolent Geist', 'Covert Cutpurse // Covetous Geist',
            'Binding Geist // Spectral Binding', 'Mourning Patrol // Morning Apparition',
            "Lantern Bearer // Lanterns' Lift", 'Beloved Beggar // Generous Soul',
            'Galedrifter // Waildrifter', 'Baithook Angler // Hook-Haunt Drifter',
            'Gutter Skulker // Gutter Shortcut', "Kindly Ancestor // Ancestor's Embrace",
            'Chaplain of Alms // Chapel Shieldgeist'}
        for raw in json.loads(FIXTURE.read_text(encoding='utf-8'))['cards']:
            if 'Disturb' not in raw['keywords']:
                continue
            with self.subTest(card=raw['name']):
                record = self.db.lookup(raw['name'])
                semantics = SemanticRegistry(include_builtin_packs=False)
                program = compile_best_available_card_program(self.db, record, semantic_registry=semantics,
                    capability_registry=self.registry, capability_profile='commander_review')
                bound = bind_card_program_runtime(program, capability_registry=self.registry, profile='commander_review')
                self.assertEqual(record.name in expected, bound['strict_capability_ready'], program.residuals)
                register_generated_programs(self.db, semantics, (record,), trust_level='provisional',
                    capability_registry=self.registry, capability_profile='commander_review',
                    promote_exact_runtime_handlers=True, promote_exact_trigger_programs=True,
                    promote_exact_effect_programs=True, promote_exact_capability_declarations=True)
                if record.name in expected:
                    programs = tuple(p for p in semantics.programs_for_oracle(record.oracle_id)
                        if 'generated_oracle_ir' in p.coverage)
                    self.assertTrue(programs)
                    self.assertTrue(all(p.trust_level == 'trusted' for p in programs))

    def test_omitted_current_source_replacement_keeps_admission_but_changes_counter_result(self):
        from quorune.semantic_runtime.zone_replacements import ZoneChangeReplacementRegistry
        original = ZoneChangeReplacementRegistry.subject_replacement_effects

        def omitted(registry, descriptor, **kwargs):
            if descriptor.get('handler_id') == SELF_GRAVEYARD_EXILE_HANDLER:
                return ()
            return original(registry, descriptor, **kwargs)

        result = unittest.TestResult()
        witness = type(self)('test_actual_countered_disturb_back_spell_goes_to_exile')
        with mock.patch.object(ZoneChangeReplacementRegistry, 'subject_replacement_effects', omitted):
            witness.run(result)
        self.assertFalse(result.errors)
        self.assertEqual(1, len(result.failures))
        self.assertIn("self.assertEqual('exile'", result.failures[0][1])

    def test_omitted_copied_face_provenance_is_detected_by_actual_token_departure(self):
        from quorune import engine as engine_module
        from quorune.entry_copy import COPIABLE_ORACLE_FIELD, COPIABLE_FACE_FIELD
        original = engine_module.copiable_card_characteristics

        def omitted(*args):
            values = original(*args)
            values.pop(COPIABLE_ORACLE_FIELD, None)
            values.pop(COPIABLE_FACE_FIELD, None)
            return values

        with mock.patch.object(engine_module, 'copiable_card_characteristics', omitted):
            # Provenance loss now fails the canonical prepared-move guard
            # before a wrong graveyard transition can commit.
            with self.assertRaisesRegex(AssertionError, 'Prepared zone replacement does not match the proposed move'):
                type(self)('test_actual_token_back_copy_exiles_instead_of_dying').test_actual_token_back_copy_exiles_instead_of_dying()

    def test_graveyard_target_prohibition_allows_disturb_and_preserves_back_exile(self):
        session = self.session(70214690)
        engine = session.engine
        seal = self.add(engine, 'Ground Seal', zone='hand', ref='disturb-ground-seal')
        source = self.add(engine, "Lantern Bearer // Lanterns' Lift", zone='graveyard', ref='sealed-disturb-source')
        corpse = self.add(engine, 'Generic Bound Body', zone='graveyard', ref='sealed-graveyard-target')
        recover = self.add(engine, 'Raise Dead', zone='hand', ref='sealed-targeted-return')
        target = self.add(engine, 'Generic Bound Body', seat='B', ref='sealed-disturb-aura-target')
        bounce = self.add(engine, 'Unsummon', seat='B', zone='hand', ref='sealed-disturb-bounce')
        private = self.add(engine, 'Generic Bound Growth', seat='C', zone='hand', ref='sealed-disturb-private')
        for card in (seal, source, recover, bounce):
            self.assert_closed(session, card)
        # The corpus carrier's separate back-face trigger is still unrepresented.
        dennick = self.db.lookup('Dennick, Pious Apprentice // Dennick, Pious Apparition')
        program = compile_best_available_card_program(self.db, dennick, semantic_registry=engine.semantics,
            capability_registry=self.registry, capability_profile='commander_review')
        self.assertFalse(bind_card_program_runtime(program, capability_registry=self.registry,
            profile='commander_review')['strict_capability_ready'])
        engine.state.players['B'].mana_pool['U'] = 1
        self.prepare(session, mana={'G':4, 'U':4, 'B':4, 'C':10})
        initial = session.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']
        return_offer = next(row for row in initial if row['id'] == 'cast:' + recover.ref)
        self.assertIn(corpse.ref, return_offer['target_schema']['legal_refs'])
        self.checkpoint(session)
        before_hand = set(session.state.players['A'].zones['hand'])
        self.cast(session, 'A', seal)
        self.assertIsNone(self.resolve(session))
        drawn_id, = set(session.state.players['A'].zones['hand']) - before_hand
        self.assertEqual('battlefield', session.state.cards[seal.object_id].zone)
        self.assertGreaterEqual(session.state.players['A'].mana_pool['B'], 1)
        actions = session.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']
        self.assertFalse(any(row['id'] == 'cast:' + recover.ref for row in actions))
        self.reject(session, return_offer['id'], targets=[corpse.ref], pay='auto')
        back = "Lanterns' Lift"
        action_id = f'cast:{source.ref}:face:{back}'
        offered = next(row for row in actions if row['id'] == action_id)
        self.assertIn(target.ref, offered['target_schema']['legal_refs'])
        self.assertNotIn(corpse.ref, offered['target_schema']['legal_refs'])
        self.reject(session, action_id, 'D', face=back, cost_option='disturb', targets=[target.ref], pay='auto')
        self.disturb(session, source, [target.ref])
        session = self.replay(session, load=True)
        self.cast(session, 'B', bounce, [session.state.cards[target.object_id].ref])
        session = self.replay(session, load=True)
        self.assertIsNone(self.resolve(session))
        current = session.state.cards[source.object_id]
        self.assertEqual(('exile', None, None), (current.zone, current.active_face, current.attached_to))
        self.assertEqual('hand', session.state.cards[target.object_id].zone)
        self.assertEqual('graveyard', session.state.cards[corpse.object_id].zone)
        self.assertEqual('hand', session.state.cards[recover.object_id].zone)
        for viewer in 'BCD':
            packet = json.dumps(session.packet('pilot:' + viewer, full=True))
            self.assertNotIn(session.state.cards[drawn_id].ref, packet)
            if viewer != 'C':
                self.assertNotIn(private.ref, packet)
        self.replay(session, load=True)
