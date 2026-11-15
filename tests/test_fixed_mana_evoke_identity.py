"""Pinned CR 400.7, 603.3b and 702.74a: Evoke retains its entered object."""
import tempfile
import unittest
from pathlib import Path
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
import test_recipient_control as seat_actions
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from quorune.evoke import (
    EvokeError, FixedManaEvokeSpec, compile_fixed_mana_evoke,
    evoke_payment_annotation, evoke_payment_marker, evoke_sacrifice_effect,
    is_evoked_annotation, validate_evoke_payment_marker,
)
from quorune.errors import GameRuleError


class FixedManaEvokeIdentityModelTests(unittest.TestCase):
    def test_new_compilation_versions_identity_without_changing_mana_choices(self):
        current = compile_fixed_mana_evoke(material_line='Evoke {R/G}{R/G}',
            oracle_line='Evoke {R/G}{R/G}', line_index=0)
        self.assertEqual(2, current.schema_version)
        legacy = replace(current, schema_version=1)
        for spec in (legacy, current):
            self.assertEqual(spec, FixedManaEvokeSpec.from_dict(spec.to_dict()))
            options = spec.cast_cost_options()
            self.assertEqual(3, len(options))
            self.assertEqual([row['requirements'] for row in legacy.cast_cost_options()],
                             [row['requirements'] for row in options])
            self.assertTrue(all(row['evoke_payment']['schema_version'] == spec.schema_version
                                for row in options))
        self.assertNotEqual(legacy.fingerprint, current.fingerprint)

    def test_marker_codecs_preserve_legacy_true_and_seal_new_identity(self):
        self.assertIs(True, evoke_payment_annotation(evoke_payment_marker()))
        marker = evoke_payment_marker(schema_version=2)
        self.assertEqual(marker, evoke_payment_annotation(marker))
        self.assertTrue(is_evoked_annotation(True))
        self.assertTrue(is_evoked_annotation(marker))
        for version in (True, False, 1.0, 2.0, None, 0, 3, '2'):
            with self.subTest(version=version):
                with self.assertRaises(EvokeError):evoke_payment_marker(schema_version=version)
                self.assertFalse(validate_evoke_payment_marker({**marker, 'schema_version': version}))
        self.assertFalse(validate_evoke_payment_marker({**marker, 'client_source': 'new-object'}))

    def test_current_source_identity_control_zone_and_phase_all_remain_required(self):
        context = {'evoke': True, 'evoke_source_identity_version': 2,
                   'source_logical_object_id': 'entered@1'}
        source = SimpleNamespace(zone='battlefield', phased_out=False,
                                 logical_object_id='entered@1', controller='A')
        self.assertEqual({'op': 'sacrifice_if_present', 'card': '$source.zone_object'},
            evoke_sacrifice_effect(context, source=source, controller='A'))
        for changes in ({'logical_object_id': 'returned@3'}, {'controller': 'B'},
                        {'zone': 'hand'}, {'phased_out': True}):
            with self.subTest(changes=changes):
                changed = SimpleNamespace(**{**vars(source), **changes})
                self.assertIsNone(evoke_sacrifice_effect(context, source=changed, controller='A'))
        self.assertIsNone(evoke_sacrifice_effect(context, source=None, controller='A'))

    def test_declared_identity_version_cannot_downgrade_or_omit_its_source(self):
        context = {'evoke': True, 'evoke_source_identity_version': 2,
                   'source_logical_object_id': 'entered@1'}
        for changes in ({'evoke_source_identity_version': True},
                        {'evoke_source_identity_version': 1},
                        {'evoke_source_identity_version': 2.0},
                        {'evoke': False}, {'source_logical_object_id': ''},
                        {'source_logical_object_id': None}):
            with self.subTest(changes=changes), self.assertRaises(GameRuleError):
                evoke_sacrifice_effect({**context, **changes}, source=None, controller='A')

    def test_legacy_and_other_builtin_source_sacrifices_keep_their_original_payload(self):
        for context in ({}, {'evoke': True, 'source_logical_object_id': 'old@1'}):
            self.assertEqual({'op': 'sacrifice_if_present', 'card': '$source'},
                evoke_sacrifice_effect(context, source=None, controller='A'))


class FixedManaEvokeIdentityNativeTests(InheritedNativeFixture, unittest.TestCase):
    reject = seat_actions.RecipientControlNativeTests.reject
    finish = seat_actions.RecipientControlNativeTests.finish

    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        path = Path(cls.temp.name) / 'evoke-identity.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',
            ROOT/'tests/fixtures/fixed-mana-evoke-identity-cards.json'], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Evoke identity', [
            DeckEntry('Generic Bound Commander', 1, 'commander'),
            DeckEntry('Generic Bound Plains', 30)], ['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temp.cleanup()

    def order(self, session, *, body_first):
        rows = session.packet(session.pending_principals()[0], full=True)['decision']['ctx']['triggers']
        rows = sorted(rows, key=lambda row:
            ('evoke' not in row['label'].casefold()) if body_first else
            ('evoke' in row['label'].casefold()))
        command = {'action_id': 'order', 'triggers': [row['id'] for row in rows]}
        result = session.act(session.pending_principals()[0], command)
        self.assertTrue(result.ok, result.summary)

    def leave_only_sacrifice(self, session):
        for _ in range(40):
            decision = session.state.pending_decision
            if (decision.kind == 'priority' and len(session.state.stack) == 1
                    and session.state.stack[0].context.get('evoke') is True):
                return
            if decision.kind == 'trigger.order':
                self.order(session, body_first=True)
                continue
            self.assertEqual('priority', decision.kind)
            self.assertTrue(session.act(session.pending_principals()[0], {'action_id': 'pass'}).ok)
        self.fail('Original draw did not leave the Evoke sacrifice pending')

    def blink_witness(self, *, expected_zone):
        session = self.session(70274090)
        source = self.add(session.engine, 'Mulldrifter', zone='hand')
        blink = self.add(session.engine, 'Ephemerate', zone='hand')
        self.add(session.engine, 'Generic Bound Plains', ref='blink-white-source')
        private = self.add(session.engine, 'Generic Bound Growth', seat='C', zone='hand')
        for card in (source, blink):
            self.assert_closed(session, card)
        self.prepare(session, mana={'C': 2, 'U': 1, 'W': 1})
        self.checkpoint(session)
        offered = self.action(session, 'A', source)
        command = {'action_id': offered['id'], 'cost_option': 'evoke', 'targets': [], 'pay': 'auto'}
        self.reject(session, 'B', command)
        result = session.act('pilot:A', command)
        self.assertTrue(result.ok, result.summary)
        session = self.replay(session, load=True)
        self.leave_only_sacrifice(session)
        before = session.state.cards[source.object_id].logical_object_id
        self.cast(session, 'A', blink, [source.ref])
        for _ in range(48):
            decision = session.state.pending_decision
            if not session.state.stack:
                break
            if decision.kind == 'trigger.order':
                self.order(session, body_first=True)
                continue
            self.assertEqual('priority', decision.kind)
            self.assertTrue(session.act(session.pending_principals()[0], {'action_id': 'pass'}).ok)
        else:
            self.fail('Blink and original sacrifice did not finish')
        current = session.state.cards[source.object_id]
        self.assertNotEqual(before, current.logical_object_id)
        self.assertEqual(expected_zone, current.zone)
        return session, private

    def test_original_evoke_sacrifice_does_not_consume_a_blinked_incarnation(self):
        session, private = self.blink_witness(expected_zone='battlefield')
        self.finish(session, private)

    def test_omitted_incarnation_binding_is_killed_by_the_original_action_witness(self):
        def omit(context, *, source, controller):
            return {'op': 'sacrifice_if_present', 'card': '$source'}
        with patch('quorune.evoke.evoke_sacrifice_effect', side_effect=omit):
            with self.assertRaises(AssertionError):
                self.blink_witness(expected_zone='battlefield')

    def test_historical_descriptor_one_keeps_its_recorded_blink_result_on_current_replay(self):
        import quorune.compiler.casting_payment_keyword_nodes as compiler
        original = compiler.compile_fixed_mana_evoke
        def historical(**kwargs):
            spec = original(**kwargs)
            return replace(spec, schema_version=1) if spec is not None else None
        with patch.object(compiler, 'compile_fixed_mana_evoke', side_effect=historical):
            session, private = self.blink_witness(expected_zone='graveyard')
        # Replay the stored descriptor-one recipe with the current compiler restored.
        self.finish(session, private)

    def test_both_original_trigger_orders_draw_and_sacrifice_the_same_entered_object(self):
        for index, body_first in enumerate((True, False)):
            with self.subTest(body_first=body_first):
                session = self.session(70274100 + index)
                source = self.add(session.engine, 'Mulldrifter', zone='hand')
                private = self.add(session.engine, 'Generic Bound Growth', seat='C', zone='hand')
                self.assert_closed(session, source)
                self.prepare(session, mana={'C': 2, 'U': 1})
                self.checkpoint(session)
                hand = len(session.state.players['A'].zones['hand'])
                offered = self.action(session, 'A', source)
                result = session.act('pilot:A', {'action_id': offered['id'],
                    'cost_option': 'evoke', 'targets': [], 'pay': 'auto'})
                self.assertTrue(result.ok, result.summary)
                while True:
                    decision = self.resolve(session)
                    if decision is None:break
                    self.assertEqual('trigger.order', decision.kind)
                    self.order(session, body_first=body_first)
                self.assertEqual('graveyard', session.state.cards[source.object_id].zone)
                self.assertIn(source.object_id, session.state.players[source.owner].zones['graveyard'])
                self.assertEqual(hand + 1, len(session.state.players['A'].zones['hand']))
                self.finish(session, private)

    def test_normal_original_cast_draws_without_an_evoke_sacrifice(self):
        session = self.session(70274110)
        source = self.add(session.engine, 'Mulldrifter', zone='hand')
        private = self.add(session.engine, 'Generic Bound Growth', seat='C', zone='hand')
        self.assert_closed(session, source)
        self.prepare(session, mana={'C': 4, 'U': 1})
        self.checkpoint(session)
        hand = len(session.state.players['A'].zones['hand'])
        self.cast(session, 'A', source)
        self.assertIsNone(self.resolve(session))
        self.assertEqual('battlefield', session.state.cards[source.object_id].zone)
        self.assertEqual(hand + 1, len(session.state.players['A'].zones['hand']))
        self.finish(session, private)

    def test_former_controller_cannot_sacrifice_the_actual_donated_incarnation(self):
        session = self.session(70274120)
        source = self.add(session.engine, 'Mulldrifter', zone='hand')
        donation = self.add(session.engine, 'Donate', zone='hand')
        flash = self.add(session.engine, 'Vedalken Orrery')
        private = self.add(session.engine, 'Generic Bound Growth', seat='C', zone='hand')
        for card in (source, donation, flash):self.assert_closed(session, card)
        self.prepare(session, mana={'C': 6, 'U': 2})
        self.checkpoint(session)
        offered = self.action(session, 'A', source)
        result = session.act('pilot:A', {'action_id': offered['id'],
            'cost_option': 'evoke', 'targets': [], 'pay': 'auto'})
        self.assertTrue(result.ok, result.summary)
        self.leave_only_sacrifice(session)
        self.cast(session, 'A', donation, ['B', source.ref])
        self.resolve_top(session)
        self.assertEqual('B', session.state.cards[source.object_id].controller)
        session = self.replay(session, load=True)
        self.assertIsNone(self.resolve(session))
        self.assertEqual('battlefield', session.state.cards[source.object_id].zone)
        self.assertEqual('B', session.state.cards[source.object_id].controller)
        self.finish(session, private)
