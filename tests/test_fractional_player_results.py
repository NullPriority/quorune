from __future__ import annotations

"""CR 107.1/608/701: rounded player quantities use current affected domains."""

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
import test_ascend_city_blessing as ascend_actions
import test_bound_effect_programs as actions
import test_self_public_zone_casting as zone_actions


LIFE_OWNER = 'life.loss.half_current_player'
CHOICE_OWNER = 'choice.affected_player.half_domain'


class FractionalPlayerResultCompilerTests(unittest.TestCase):
    def test_half_player_life_hand_and_creature_results_have_closed_rounding_contracts(self):
        registry = load_default_capability_registry()
        for text, owner in (
            ('Target opponent loses half their life, rounded up.', LIFE_OWNER),
            ('Each player loses half their life, rounded down.', LIFE_OWNER),
            ('You lose half your life, rounded up.', LIFE_OWNER),
            ('Target opponent discards half the cards in their hand, rounded up.', CHOICE_OWNER),
            ('Each opponent sacrifices half the creatures they control of their choice, rounded up.', CHOICE_OWNER),
        ):
            with self.subTest(text=text):
                ir = compile_oracle_card(record(text, 'Sorcery'), capability_registry=registry, capability_profile='commander_review')
                self.assertEqual('exact', ir.status, ir.material_residuals)
                self.assertIn(owner, ir.faces[0].nodes[0].capability_dependencies)

    def test_half_quantity_arithmetic_and_malformed_fields_are_closed(self):
        from quorune.fractional_quantity_model import HalfQuantitySpec
        for rounding in ('up', 'down'):
            spec = HalfQuantitySpec(rounding)
            self.assertEqual(spec, HalfQuantitySpec.from_dict(spec.to_dict()))
            for amount, up, down in ((-3, 0, 0), (0, 0, 0), (1, 1, 0), (3, 2, 1), (4, 2, 2), (41, 21, 20)):
                self.assertEqual(up if rounding == 'up' else down, spec.amount(amount))
        for change in ({'rounding': 'nearest'}, {'divisor': 0}, {'divisor': True}, {'unknown': True}):
            with self.assertRaises(ValueError):
                HalfQuantitySpec.from_dict({**HalfQuantitySpec('up').to_dict(), **change})

    def test_fraction_result_unknown_domains_and_shape_mutations_remain_residual(self):
        registry = load_default_capability_registry()
        for text in ('Target opponent loses half their life, rounded sideways.',
                     'Target opponent discards half the cards in another player\'s hand, rounded up.',
                     'Each opponent sacrifices half the creatures they own of their choice, rounded up.',
                     'Target opponent discards a secretly chosen fraction of their hand.'):
            self.assertNotEqual('exact', compile_oracle_card(record(text, 'Sorcery'), capability_registry=registry,
                capability_profile='commander_review').status)
        ir = compile_oracle_card(record('Target opponent discards half the cards in their hand, rounded up.', 'Sorcery'),
            capability_registry=registry, capability_profile='commander_review')
        self.assertEqual('exact', ir.status, ir.material_residuals)
        node = ir.faces[0].nodes[0]
        effect = deepcopy(node.effects[0])
        effect['count']['divisor'] = 3
        self.assertFalse(capability_dependencies_for_node(effects=(effect,), target_schema=node.target_schema, mechanic_ids=node.mechanics))
        raw = json.loads((ROOT / 'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        for owner, text in ((LIFE_OWNER, 'Target opponent loses half their life, rounded up.'),
                            (CHOICE_OWNER, 'Target opponent discards half the cards in their hand, rounded up.')):
            changed = deepcopy(raw)
            next(row for row in changed['capabilities'] if row['id'] == owner).update(status='blocked', blockers=['half result withdrawn'])
            self.assertNotEqual('exact', compile_oracle_card(record(text, 'Sorcery'), capability_registry=CapabilityRegistry(changed),
                capability_profile='commander_review').status)

    def test_fractional_selection_state_rejects_changed_sealed_counts_and_domains(self):
        from quorune.errors import GameRuleError
        from quorune.fractional_quantity_model import HalfQuantitySpec
        from quorune.selection.fractional_counts import apnap_required_count
        state = {'effect': {'count': HalfQuantitySpec('up').to_dict()}, 'player_order': ['C'],
            'fractional_domains': {'C': ['a', 'b', 'c']}, 'fractional_counts': {'C': 2}}
        self.assertEqual(2, apnap_required_count(state, 'C', ['a', 'b', 'c']))
        changed = deepcopy(state)
        changed['fractional_counts']['C'] = 1
        with self.assertRaises(GameRuleError):
            apnap_required_count(changed, 'C', ['a', 'b', 'c'])
        with self.assertRaises(GameRuleError):
            apnap_required_count(state, 'C', ['a', 'b'])


class FractionalPlayerResultRuntimeTests(unittest.TestCase):
    register_fixture_cards_on_add = True
    session = actions.BoundEffectProgramRuntimeTests.session
    add = actions.BoundEffectProgramRuntimeTests.add
    checkpoint = actions.BoundEffectProgramRuntimeTests.checkpoint
    resolve = actions.BoundEffectProgramRuntimeTests.resolve
    replay = actions.BoundEffectProgramRuntimeTests.replay
    prepare = zone_actions.SelfPublicZoneRuntimeTests.prepare
    cast = zone_actions.SelfPublicZoneRuntimeTests.cast
    assert_closed = zone_actions.SelfPublicZoneRuntimeTests.assert_closed
    lands = ascend_actions.AscendRuntimeTests.lands

    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        path = Path(cls.temp.name) / 'fractional-player-results.sqlite3'
        build_fixture_database([ROOT / 'tests/fixtures/bound-effect-program-cards.json',
            ROOT / 'tests/fixtures/conditional-query-declaration-cards.json',
            ROOT / 'tests/fixtures/fractional-player-result-cards.json'], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Fractional result witness', [DeckEntry('Generic Bound Commander', 1, 'commander'),
            DeckEntry('Generic Bound Plains', 30)], ['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temp.cleanup()

    def choose(self, session, actor, refs):
        result = session.act('pilot:' + actor, {'action_id': 'choose', 'cards': refs})
        self.assertTrue(result.ok, result.summary)

    def reject(self, session, actor, refs):
        before = authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:' + actor, {'action_id': 'choose', 'cards': refs}).ok)
        self.assertEqual(before, authoritative_state_hash(session.state))

    def test_actual_rush_modes_keep_independent_opponents_counts_and_current_life(self):
        session = self.session(418000)
        source = self.add(session.engine, 'Rush of Dread', zone='hand', ref='fraction-original-rush')
        gain = self.add(session.engine, 'Generic Fraction Life Gain', zone='hand', ref='fraction-pending-life-change')
        bodies = [self.add(session.engine, 'Generic Conditional Detective', seat='D', ref='fraction-D-body-' + str(i)) for i in range(3)]
        for index in range(2):
            self.add(session.engine, 'Generic Bound Growth', seat='C', zone='hand', ref='fraction-C-private-' + str(index))
        hand = [session.state.cards[oid].ref for oid in session.state.players['C'].zones['hand']]
        self.assertEqual(9, len(hand))
        self.assert_closed(session, source)
        self.prepare(session, mana={'C': 6, 'B': 2})
        self.checkpoint(session)
        result = session.act('pilot:A', {'action_id': 'cast:' + source.ref,
            'modes': ['mode_3', 'mode_1', 'mode_2'], 'targets': ['D', 'C', 'B'], 'pay': 'auto'})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(0, sum(session.state.players['A'].mana_pool.values()))
        self.cast(session, 'A', gain, ['B'])
        self.resolve(session)
        self.assertEqual(41, session.state.players['B'].life)
        self.assertEqual('choice.apnap', session.state.pending_decision.kind)
        payload = session.state.pending_decision.payload_by_actor['D']
        self.assertEqual(2, payload['count'])
        self.assertEqual({card.ref for card in bodies}, set(payload['options']))
        self.reject(session, 'A', [card.ref for card in bodies[:2]])
        self.reject(session, 'D', [bodies[0].ref])
        session = self.replay(session, load=True)
        self.choose(session, 'D', [card.ref for card in bodies[:2]])
        self.assertEqual('choice.apnap', session.state.pending_decision.kind)
        self.assertEqual(5, session.state.pending_decision.payload_by_actor['C']['count'])
        for actor in 'ABD':
            self.assertFalse(any(ref in json.dumps(session.packet('pilot:' + actor, full=True)) for ref in hand))
        self.reject(session, 'B', hand[:5])
        self.reject(session, 'C', hand[:4])
        session = self.replay(session, load=True)
        self.choose(session, 'C', hand[:5])
        self.resolve(session)
        self.assertEqual(4, len(session.state.players['C'].zones['hand']))
        self.assertEqual(1, sum(session.state.cards[card.object_id].zone == 'battlefield' for card in bodies))
        self.assertEqual(20, session.state.players['B'].life)
        self.assertEqual(40, session.state.players['A'].life)
        self.replay(session, load=True)

    def test_actual_vona_city_branch_uses_current_each_opponent_creature_count(self):
        for city in (False, True):
            with self.subTest(city=city):
                session = self.session(418010 + city)
                self.lands(session.engine, 10 if city else 1)
                source = self.add(session.engine, "Vona's Hunger", zone='hand', ref='fraction-original-vona')
                bodies = {seat: [self.add(session.engine, 'Generic Conditional Detective', seat=seat,
                    ref='fraction-vona-' + seat + '-' + str(index)) for index in range(count)] for seat, count in (('B', 3), ('C', 2))}
                self.assert_closed(session, source)
                self.prepare(session, mana={'C': 2, 'B': 1})
                self.checkpoint(session)
                self.cast(session, 'A', source)
                self.resolve(session)
                self.assertEqual(city, session.state.players['A'].stats.get('citys_blessing', False))
                for seat, expected in (('B', 2 if city else 1), ('C', 1)):
                    self.assertEqual('pilot:' + seat, session.pending_principals()[0])
                    self.assertEqual(expected, session.state.pending_decision.payload_by_actor[seat]['count'])
                    refs = [card.ref for card in bodies[seat][:expected]]
                    self.reject(session, 'A', refs)
                    session = self.replay(session, load=True)
                    self.choose(session, seat, refs)
                self.resolve(session)
                self.assertEqual(1 if city else 2, sum(session.state.cards[c.object_id].zone == 'battlefield' for c in bodies['B']))
                self.assertEqual(1, sum(session.state.cards[c.object_id].zone == 'battlefield' for c in bodies['C']))
                self.assertFalse(any(card.controller == 'D' and card.zone == 'battlefield' for card in session.state.cards.values()))
                self.replay(session, load=True)

    def test_each_opponent_fractional_discard_keeps_all_sealed_hands_private_until_commit(self):
        session = self.session(418020)
        source = self.add(session.engine, 'Generic Fraction Each Discard', zone='hand', ref='fraction-each-private-discard')
        for seat, extras in (('C', 2), ('D', 1)):
            for index in range(extras):
                self.add(session.engine, 'Generic Bound Growth', seat=seat, zone='hand', ref='fraction-private-' + seat + '-' + str(index))
        hands = {seat: [session.state.cards[oid].ref for oid in session.state.players[seat].zones['hand']] for seat in 'BCD'}
        self.assertEqual([7, 9, 8], [len(hands[seat]) for seat in 'BCD'])
        self.assert_closed(session, source)
        self.prepare(session)
        self.checkpoint(session)
        self.cast(session, 'A', source)
        self.resolve(session)
        for seat, count in (('B', 4), ('C', 5), ('D', 4)):
            self.assertEqual('pilot:' + seat, session.pending_principals()[0])
            self.assertEqual(count, session.state.pending_decision.payload_by_actor[seat]['count'])
            for viewer in 'ABCD':
                packet = session.packet('pilot:' + viewer, full=True)
                # Decision IDs and card refs use separate namespaces and may coincide.
                decision = {key: value for key, value in (packet.get('decision') or {}).items() if key != 'id'}
                projected = json.dumps({**packet, 'decision': decision})
                for owner in 'BCD':
                    if owner != viewer:
                        self.assertFalse(any(ref in projected for ref in hands[owner]))
            self.reject(session, 'A', hands[seat][:count])
            session = self.replay(session, load=True)
            self.choose(session, seat, hands[seat][:count])
            if seat != 'D':
                self.assertTrue(all(session.state.cards[next(oid for oid, card in session.state.cards.items() if card.ref == ref)].zone == 'hand'
                    for ref in hands[seat][:count]))
        self.resolve(session)
        self.assertEqual([3, 4, 4], [len(session.state.players[seat].zones['hand']) for seat in 'BCD'])
        self.replay(session, load=True)
