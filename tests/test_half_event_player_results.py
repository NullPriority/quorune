from __future__ import annotations

"""CR 608.2h: sealed event recipient and current life are separate facts."""

from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from common import ROOT
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import CapabilityRegistry, capability_dependencies_for_node, load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_granted_skulk import record
import test_bound_effect_programs as actions
import test_fixed_tap_payments as tap_actions
import test_self_public_zone_casting as zone_actions


DAMAGE = 'Whenever this creature deals combat damage to a player, that player loses half their life, rounded up.'


class HalfEventPlayerResultCompilerTests(unittest.TestCase):
    def test_half_event_results_preserve_occurrence_recipient_and_typed_amount(self):
        registry = load_default_capability_registry()
        for text, player in (
            (DAMAGE, '$context.target'),
            ('Whenever this creature attacks, defending player loses half their life, rounded down.', '$context.defending_player'),
        ):
            with self.subTest(text=text):
                ir = compile_oracle_card(record(text, 'Creature'), capability_registry=registry, capability_profile='commander_review')
                self.assertEqual('exact', ir.status, ir.material_residuals)
                node, = ir.faces[0].nodes
                self.assertEqual(player, node.effects[0]['player'])
                self.assertEqual('half_quantity', node.effects[0]['amount']['kind'])
                self.assertIn('resolution.event_player.fixed_result', node.capability_dependencies)
                self.assertIn('life.loss.half_current_player', node.capability_dependencies)

    def test_fractional_binding_keeps_unsupported_antecedents_and_quantity_fields_residual(self):
        registry = load_default_capability_registry()
        for text in (
            DAMAGE.replace('that player', "that creature's controller"),
            DAMAGE.replace('to a player', 'to a creature'),
            DAMAGE.replace('their life', 'your life'),
            DAMAGE.replace('rounded up', 'rounded sideways'),
            'Lose half their life, rounded up.',
        ):
            with self.subTest(text=text):
                self.assertNotEqual('exact', compile_oracle_card(record(text, 'Creature'), capability_registry=registry,
                    capability_profile='commander_review').status)
        ir = compile_oracle_card(record(DAMAGE, 'Creature'), capability_registry=registry, capability_profile='commander_review')
        self.assertEqual('exact', ir.status, ir.material_residuals)
        node = ir.faces[0].nodes[0]
        changed = deepcopy(node.effects[0])
        changed['amount']['divisor'] = 3
        self.assertFalse(capability_dependencies_for_node(effects=(changed,), target_schema=None, mechanic_ids=node.mechanics))

    def test_half_event_result_owner_withdrawal_prevents_whole_admission(self):
        raw = json.loads((ROOT / 'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        for owner in ('resolution.event_player.fixed_result', 'life.loss.half_current_player'):
            changed = deepcopy(raw)
            next(row for row in changed['capabilities'] if row['id'] == owner).update(status='blocked', blockers=['half event owner withdrawn'])
            self.assertNotEqual('exact', compile_oracle_card(record(DAMAGE, 'Creature'), capability_registry=CapabilityRegistry(changed),
                capability_profile='commander_review').status)


class HalfEventPlayerResultRuntimeTests(unittest.TestCase):
    register_fixture_cards_on_add = True
    session = actions.BoundEffectProgramRuntimeTests.session
    add = actions.BoundEffectProgramRuntimeTests.add
    checkpoint = actions.BoundEffectProgramRuntimeTests.checkpoint
    resolve = actions.BoundEffectProgramRuntimeTests.resolve
    replay = actions.BoundEffectProgramRuntimeTests.replay
    prepare = zone_actions.SelfPublicZoneRuntimeTests.prepare
    cast = zone_actions.SelfPublicZoneRuntimeTests.cast
    assert_closed = zone_actions.SelfPublicZoneRuntimeTests.assert_closed
    advance_to = tap_actions.FixedTapPaymentRuntimeTests.advance_to

    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        path = Path(cls.temp.name) / 'half-event-player.sqlite3'
        build_fixture_database([ROOT / 'tests/fixtures/bound-effect-program-cards.json',
            ROOT / 'tests/fixtures/event-player-subject-cards.json', ROOT / 'tests/fixtures/granted-skulk-cards.json',
            ROOT / 'tests/fixtures/half-event-player-result-cards.json'], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Half event witness', [DeckEntry('Generic Bound Commander', 1, 'commander'),
            DeckEntry('Generic Bound Plains', 30)], ['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temp.cleanup()

    def combat_trigger(self, session, attacker, actor='A'):
        self.advance_to(session, 'combat.attackers')
        before = authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:C', {'action_id': 'attack', 'attackers': {attacker.ref: 'B'}}).ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        result = session.act('pilot:' + actor, {'action_id': 'attack', 'attackers': {attacker.ref: 'B'}})
        self.assertTrue(result.ok, result.summary)
        for _ in range(64):
            session.engine.pump()
            if session.state.stack and session.state.players['B'].life == 39:
                break
            self.assertEqual('priority', session.state.pending_decision.kind)
            self.assertTrue(session.act(session.pending_principals()[0], {'action_id': 'pass'}).ok)
        else:
            self.fail('Actual combat damage did not produce the half-life trigger')
        self.assertEqual('A', session.state.stack[-1].controller)
        self.assertEqual('B', session.state.stack[-1].context['target'])

    def test_actual_quietus_cast_equip_damage_current_life_and_pending_departure(self):
        for change_and_depart in (False, True):
            with self.subTest(change_and_depart=change_and_depart):
                session = self.session(419000 + change_and_depart)
                source = self.add(session.engine, 'Quietus Spike', zone='hand', ref='half-event-original-spike')
                attacker = self.add(session.engine, 'Generic Half Event Body', ref='half-event-attacker')
                attacker.acquired_control_turn_count = -1
                gain = self.add(session.engine, 'Generic Half Event Gain', zone='hand', ref='half-event-pending-life')
                bounce = self.add(session.engine, 'Generic Skulk Bounce', zone='hand', ref='half-event-pending-departure')
                private = self.add(session.engine, 'Generic Bound Growth', seat='C', zone='hand', ref='half-event-private')
                self.assert_closed(session, source)
                self.prepare(session, mana={'C': 6})
                self.checkpoint(session)
                self.cast(session, 'A', source)
                self.resolve(session)
                equip = next(row for row in session.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']
                    if row.get('source') == source.ref and row['id'].startswith('activate:'))
                result = session.act('pilot:A', {'action_id': equip['id'], 'targets': [attacker.ref], 'pay': 'auto'})
                self.assertTrue(result.ok, result.summary)
                self.resolve(session)
                self.combat_trigger(session, attacker)
                session = self.replay(session, load=True)
                if change_and_depart:
                    self.cast(session, 'A', gain, ['B'])
                    self.cast(session, 'A', bounce, [source.ref])
                self.resolve(session)
                self.assertEqual(20 if change_and_depart else 19, session.state.players['B'].life)
                self.assertEqual([40, 40, 40], [session.state.players[seat].life for seat in 'ACD'])
                if change_and_depart:
                    self.assertEqual('hand', session.state.cards[source.object_id].zone)
                for viewer in 'ABD':
                    self.assertNotIn(private.ref, json.dumps(session.packet('pilot:' + viewer, full=True)))
                self.replay(session, load=True)

    def test_real_d_attacker_and_a_equipment_keep_b_as_recipient(self):
        from quorune.attachments import attach_objects
        session = self.session(419010)
        engine = session.engine
        source = self.add(engine, 'Quietus Spike', ref='half-event-A-equipment')
        attacker = self.add(engine, 'Generic Half Event Body', ref='half-event-owned-but-stolen')
        attach_objects(engine.state.cards, source, attacker, source_timestamp=engine._next_zone_timestamp(), players=engine.state.players)
        control = self.add(engine, 'Generic Event Subject Control', seat='D', zone='hand', ref='half-event-real-D-control')
        self.assert_closed(session, source)
        self.prepare(session, actor='D', active='D', mana={'W': 1})
        self.checkpoint(session)
        self.cast(session, 'D', control, [attacker.ref])
        self.resolve(session)
        self.assertEqual('D', session.state.cards[attacker.object_id].controller)
        self.combat_trigger(session, attacker, 'D')
        session = self.replay(session, load=True)
        self.resolve(session)
        self.assertEqual(19, session.state.players['B'].life)
        self.assertEqual([40, 40, 40], [session.state.players[seat].life for seat in 'ACD'])
        self.replay(session, load=True)
