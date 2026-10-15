from __future__ import annotations

import unittest
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from test_public_query_effect_amounts import query_amount_record


class FixedEnergyPaymentCompilerTests(unittest.TestCase):
    def test_energy_payment_missing_producer_and_open_cost_remain_untrusted(self):
        import json
        from common import ROOT
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.compiler.fixed_effect_payment_templates import fixed_effect_payment_spec
        raw = json.loads((ROOT / 'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        next(r for r in raw['capabilities'] if r['id'] == 'cost.energy.fixed_resolution_payment').update(
            status='blocked', blockers=['Energy payment producer withdrawn'])
        text = 'Whenever this creature attacks, you may pay {E}{E}. If you do, put a +1/+1 counter on it.'
        self.assertNotEqual('exact', compile_oracle_card(query_amount_record(text, suffix=365203011,
            type_line='Creature — Rhino'), capability_registry=CapabilityRegistry(raw)).status)
        for cost in ('pay {E}{X}', 'pay {E} and 1 life', 'pay {E} any number of times'):
            self.assertIsNone(fixed_effect_payment_spec(cost))

    def test_fixed_energy_payment_binds_cost_before_source_result(self):
        text = 'Whenever this creature attacks, you may pay {E}{E}. If you do, put a +1/+1 counter on it.'
        ir = compile_oracle_card(query_amount_record(text, suffix=365203001, type_line='Creature — Rhino'),
            capability_registry=load_default_capability_registry())
        self.assertEqual('exact', ir.status, ir.material_residuals)
        wrapper = ir.faces[0].nodes[0].effects[0]
        self.assertEqual('energy', wrapper['payment']['kind'])
        self.assertEqual(2, wrapper['payment']['amount'])
        self.assertEqual('$source.zone_object', wrapper['effects'][0]['card'])


class FixedEnergyPaymentModelTests(unittest.TestCase):
    def test_unavailable_counter_subject_is_empty_after_complete_shape_validation(self):
        from types import SimpleNamespace
        from quorune.semantic_runtime.counter_placement_handlers import FixedCounterPlacementHandler
        from quorune.semantic_runtime.context import SemanticNodeError
        context = SimpleNamespace(actor='A', default_reason='Unavailable original source')
        effect = {'op': 'place_counters', 'card': None, 'counter': '+1/+1', 'amount': 1, 'source': 'old-source'}
        plan = FixedCounterPlacementHandler().lower(effect, context)
        self.assertEqual((), plan.intents[0].object_refs)
        for malformed in ({**effect, 'unknown': True}, {**effect, 'amount': True},
                {**effect, 'counter': ''}, {**effect, 'source': None},
                {**effect, 'cards': ['other', 'subject']}):
            with self.assertRaises(SemanticNodeError):
                FixedCounterPlacementHandler().lower(malformed, context)

    def test_energy_payment_counter_transaction_mutant_is_killed(self):
        from types import SimpleNamespace
        from unittest.mock import patch
        from quorune.semantic_runtime.energy_payment import PayEnergyIntent
        from quorune.energy_payment import execute_energy_payment
        player = SimpleNamespace(energy=2, poison=0, counters={})
        host = SimpleNamespace(state=SimpleNamespace(players={'A': player}), _log=lambda *a, **k: None)
        with patch('quorune.counter_state.commit_counter_changes', return_value=()):
            execute_energy_payment(host, PayEnergyIntent('A', 'A', 2, 'Mutation'))
            with self.assertRaises(AssertionError):
                self.assertEqual(0, player.energy)

    def test_energy_cost_requires_full_resource_and_preserves_unrelated_counters(self):
        from types import SimpleNamespace
        from quorune.fixed_effect_payment import FixedEffectPaymentSpec
        from quorune.semantic_runtime.energy_payment import PayEnergyIntent
        from quorune.energy_payment import execute_energy_payment
        from quorune.errors import GameRuleError
        player = SimpleNamespace(energy=2, poison=4, counters={})
        host = SimpleNamespace(state=SimpleNamespace(players={'A': player}), _log=lambda *a, **k: None)
        spec = FixedEffectPaymentSpec('energy', amount=2)
        self.assertEqual(spec, FixedEffectPaymentSpec.from_dict(spec.to_dict()))
        execute_energy_payment(host, PayEnergyIntent('A', 'A', 2, 'Fixed test payment'))
        self.assertEqual((0, 4), (player.energy, player.poison))
        with self.assertRaises(GameRuleError):
            execute_energy_payment(host, PayEnergyIntent('A', 'A', 1, 'Unpayable'))
        self.assertEqual(0, player.energy)
        for amount in (-1, 0, True, 1.5):
            with self.assertRaises(ValueError):
                PayEnergyIntent('A', 'A', amount, 'Invalid')
        with self.assertRaises(ValueError):
            PayEnergyIntent('B', 'A', 1, 'Wrong payer')


class FixedEnergyPaymentActionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import tempfile
        from pathlib import Path
        from common import ROOT
        from scripts.build_test_database import build_fixture_database
        from quorune.carddb import CardDatabase
        from quorune.deck import DeckDefinition, DeckEntry
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / 'energy-payments.sqlite3'
        build_fixture_database([ROOT / 'tests/fixtures/bound-effect-program-cards.json',
            ROOT / 'tests/fixtures/fixed-energy-payment-cards.json'], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Energy', [DeckEntry('Generic Bound Commander', 1, 'commander'),
            DeckEntry('Generic Bound Plains', 30)], ['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    import test_bound_effect_programs as _bound
    import test_live_battlefield_quantities as _live
    session = _bound.BoundEffectProgramRuntimeTests.session
    add = _bound.BoundEffectProgramRuntimeTests.add
    ready = _bound.BoundEffectProgramRuntimeTests.ready
    checkpoint = _bound.BoundEffectProgramRuntimeTests.checkpoint
    replay = _bound.BoundEffectProgramRuntimeTests.replay
    resolve = _bound.BoundEffectProgramRuntimeTests.resolve
    attack = _live.LiveBattlefieldQuantityActionTests.attack
    action = _live.LiveBattlefieldQuantityActionTests.action
    register_fixture_cards_on_add = True

    def test_original_thriving_rhino_cast_attack_payment_decline_and_pending_replay(self):
        from quorune.record import authoritative_state_hash
        for pay in (True, False):
            with self.subTest(pay=pay):
                session = self.session(365203002 + pay)
                source = self.add(session.engine, 'Thriving Rhino', zone='hand')
                offer = self.ready(session, source, {'G': 1, 'C': 2})
                self.checkpoint(session)
                accepted = session.act('pilot:A', {'action_id': offer['id'], 'pay': 'auto'})
                self.assertTrue(accepted.ok, accepted.summary)
                self.resolve(session)
                self.assertEqual(2, session.state.players['A'].energy)
                self.attack(session, [session.state.cards[source.object_id]])
                self.assertEqual('semantic.choice', self.resolve(session).kind)
                for seat in 'BCD':
                    self.assertIsNone(session.packet('pilot:' + seat, full=True)['decision'])
                session = self.replay(session, load=True)
                for principal, amount in (('pilot:B', True), ('pilot:A', 'true')):
                    before = authoritative_state_hash(session.state)
                    denied = session.act(principal, {'action_id': 'choose', 'pay': amount})
                    self.assertFalse(denied.ok)
                    self.assertEqual(before, authoritative_state_hash(session.state))
                accepted = session.act('pilot:A', {'action_id': 'choose', 'pay': pay})
                self.assertTrue(accepted.ok, accepted.summary)
                self.resolve(session)
                self.assertEqual(0 if pay else 2, session.state.players['A'].energy)
                self.assertEqual(int(pay), session.state.cards[source.object_id].counters.get('+1/+1', 0))
                self.replay(session)

    def test_original_aether_chaser_pays_energy_then_creates_exact_servo(self):
        session = self.session(365203004)
        source = self.add(session.engine, 'Aether Chaser', zone='hand')
        offer = self.ready(session, source, {'R': 1, 'C': 1})
        self.checkpoint(session)
        self.assertTrue(session.act('pilot:A', {'action_id': offer['id'], 'pay': 'auto'}).ok)
        self.resolve(session)
        self.assertEqual(2, session.state.players['A'].energy)
        self.attack(session, [session.state.cards[source.object_id]])
        self.assertEqual('semantic.choice', self.resolve(session).kind)
        session = self.replay(session, load=True)
        accepted = session.act('pilot:A', {'action_id': 'choose', 'pay': True})
        self.assertTrue(accepted.ok, accepted.summary)
        self.resolve(session)
        tokens = [c for c in session.state.cards.values() if c.zone == 'battlefield' and c.is_token]
        self.assertEqual(1, len(tokens))
        data = session.engine._effective_card_data(tokens[0])
        self.assertIn('Artifact Creature', data['type_line'])
        self.assertIn('Servo', data['type_line'])
        self.assertEqual([], data['colors'])
        self.assertEqual(('1', '1'), (data['power'], data['toughness']))
        self.assertEqual(0, session.state.players['A'].energy)
        self.replay(session)

    def test_original_rhino_blink_cannot_receive_old_counter_and_insufficient_payment_rejects(self):
        from quorune.record import authoritative_state_hash
        session = self.session(365203005)
        engine = session.engine
        source = self.add(engine, 'Thriving Rhino')
        blink = self.add(engine, 'Cloudshift', zone='hand')
        engine.state.players['A'].energy = 2
        engine.state.players['A'].mana_pool['W'] = 1
        self.attack(session, [source])
        self.action(session, blink, targets=[source.ref])
        for _ in range(4):
            self.assertTrue(session.act(session.pending_principals()[0], {'action_id': 'pass'}).ok)
        self.assertEqual('semantic.choice', self.resolve(session).kind)
        session = self.replay(session, load=True)
        self.assertEqual(4, session.state.players['A'].energy)
        accepted = session.act('pilot:A', {'action_id': 'choose', 'pay': True})
        self.assertTrue(accepted.ok, accepted.summary)
        self.resolve(session)
        self.assertEqual(2, session.state.players['A'].energy)
        self.assertEqual(0, session.state.cards[source.object_id].counters.get('+1/+1', 0))
        self.replay(session)
        # A separate exact-action initial state has fewer than the printed cost.
        session = self.session(365203006)
        source = self.add(session.engine, 'Thriving Rhino')
        session.state.players['A'].energy = 1
        self.attack(session, [source])
        self.assertEqual('semantic.choice', self.resolve(session).kind)
        before = authoritative_state_hash(session.state)
        denied = session.act('pilot:A', {'action_id': 'choose', 'pay': True})
        self.assertFalse(denied.ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        self.assertTrue(session.act('pilot:A', {'action_id': 'choose', 'pay': False}).ok)
        self.resolve(session)
        self.assertEqual(1, session.state.players['A'].energy)
        self.replay(session)

    def test_original_eddytrail_hawk_selects_target_before_energy_choice(self):
        from quorune.record import authoritative_state_hash
        session = self.session(365203007)
        engine = session.engine
        source = self.add(engine, 'Eddytrail Hawk', zone='hand')
        body = self.add(engine, 'Generic Bound Body')
        enemy = self.add(engine, 'Generic Bound Body', seat='B', ref='energy-wrong-target')
        offer = self.ready(session, source, {'W': 1, 'C': 1})
        self.checkpoint(session)
        self.assertTrue(session.act('pilot:A', {'action_id': offer['id'], 'pay': 'auto'}).ok)
        self.resolve(session)
        self.assertEqual(2, session.state.players['A'].energy)
        self.attack(session, [session.state.cards[source.object_id], body])
        self.assertEqual('semantic.target', session.state.pending_decision.kind)
        for target in (source.ref, enemy.ref):
            before = authoritative_state_hash(session.state)
            denied = session.act('pilot:A', {'action_id': 'choose', 'targets': [target]})
            self.assertFalse(denied.ok)
            self.assertEqual(before, authoritative_state_hash(session.state))
        chosen = session.act('pilot:A', {'action_id': 'choose', 'targets': [body.ref]})
        self.assertTrue(chosen.ok, chosen.summary)
        self.assertEqual('semantic.choice', self.resolve(session).kind)
        session = self.replay(session, load=True)
        paid = session.act('pilot:A', {'action_id': 'choose', 'pay': True})
        self.assertTrue(paid.ok, paid.summary)
        self.resolve(session)
        self.assertEqual(1, session.state.players['A'].energy)
        self.assertIn('flying', session.engine._combat_keywords(session.state.cards[body.object_id]))
        self.assertNotIn('flying', session.engine._combat_keywords(session.state.cards[enemy.object_id]))
        self.replay(session)
