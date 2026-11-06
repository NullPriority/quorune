from __future__ import annotations

"""CR 107.1/608/701: rounded player quantities use current affected domains."""

from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

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



class HalfSacrificeSequenceCompilerTests(unittest.TestCase):
    def test_shared_rounding_and_excluded_permanent_predicates_compile_through_existing_owners(self):
        registry = load_default_capability_registry()
        for domain, rounding, excluded in (
            ("creatures", "up", {}),
            ("permanents", "down", {}),
            ("nonland permanents", "down", {"excluded_types": ["land"]}),
            ("non-God creatures", "up", {"excluded_subtypes": ["god"]}),
        ):
            text = ("Each player loses half their life, then discards half the cards in their hand, "
                    f"then sacrifices half the {domain} they control of their choice. Round {rounding} each time.")
            with self.subTest(domain=domain):
                ir = compile_oracle_card(record(text, "Sorcery"), capability_registry=registry,
                                         capability_profile="commander_review")
                self.assertEqual("exact", ir.status, ir.material_residuals)
                node = ir.faces[0].nodes[0]
                self.assertIn("resolution.effect_program.closed_components", node.capability_dependencies)
                self.assertIn(LIFE_OWNER, node.capability_dependencies)
                self.assertIn(CHOICE_OWNER, node.capability_dependencies)
                life, hand, sacrifice = node.effects
                self.assertEqual(("lose_life", "discard", "sacrifice"),
                                 (life["op"], hand["then"], sacrifice["then"]))
                self.assertEqual([rounding] * 3,
                                 [life["amount"]["rounding"], hand["count"]["rounding"], sacrifice["count"]["rounding"]])
                for key, value in excluded.items():
                    self.assertEqual(value, sacrifice["predicate"][key])

    def test_shared_rounding_producer_omission_leaves_material_text(self):
        registry = load_default_capability_registry()
        text = ("Each player loses half their life, then discards half the cards in their hand, "
                "then sacrifices half the permanents they control of their choice. Round down each time.")
        card = record(text, "Sorcery")
        self.assertEqual("exact", compile_oracle_card(card, capability_registry=registry,
            capability_profile="commander_review").status)
        with patch("quorune.compiler.fractional_player_templates._SHARED_ROUNDING_SEQUENCE") as pattern:
            pattern.fullmatch.return_value = None
            self.assertNotEqual("exact", compile_oracle_card(card, capability_registry=registry,
                capability_profile="commander_review").status)

    def test_unowned_domains_and_missing_common_rounding_remain_residual(self):
        registry = load_default_capability_registry()
        for text in (
            "Target opponent sacrifices half the nonland permanents they own of their choice, rounded down.",
            "Each player sacrifices half the non-Unrecognized creatures they control of their choice, rounded down.",
            "Each player loses half their life, then discards half the cards in their hand, "
            "then sacrifices half the creatures they control of their choice.",
        ):
            with self.subTest(text=text):
                ir = compile_oracle_card(record(text, "Sorcery"), capability_registry=registry,
                                         capability_profile="commander_review")
                self.assertNotEqual("exact", ir.status)


class HalfSacrificeSequenceRuntimeTests(unittest.TestCase):
    register_fixture_cards_on_add = True
    session = FractionalPlayerResultRuntimeTests.session
    add = FractionalPlayerResultRuntimeTests.add
    checkpoint = FractionalPlayerResultRuntimeTests.checkpoint
    resolve = FractionalPlayerResultRuntimeTests.resolve
    replay = FractionalPlayerResultRuntimeTests.replay
    prepare = FractionalPlayerResultRuntimeTests.prepare
    cast = FractionalPlayerResultRuntimeTests.cast
    assert_closed = FractionalPlayerResultRuntimeTests.assert_closed
    choose = FractionalPlayerResultRuntimeTests.choose
    reject = FractionalPlayerResultRuntimeTests.reject

    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        path = Path(cls.temp.name) / "half-sacrifice-sequences.sqlite3"
        build_fixture_database([
            ROOT / "tests/fixtures/bound-effect-program-cards.json",
            ROOT / "tests/fixtures/half-sacrifice-sequence-cards.json",
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Half-sacrifice sequence", [
            DeckEntry("Generic Bound Commander", 1, "commander"),
            DeckEntry("Generic Bound Plains", 30),
        ], ["Generic Bound Commander"])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temp.cleanup()

    def _bodies(self, session, seat, count, prefix):
        return [self.add(session.engine, "Generic Bound Body", seat=seat, ref=f"{prefix}-{seat}-{i}")
                for i in range(count)]

    def test_xander_death_counts_current_nonland_control_and_sacrifices_to_owner(self):
        session = self.session(728100)
        engine = session.engine
        xander = self.add(engine, "Lord Xander, the Collector")
        kill = self.add(engine, "Murder", zone="hand")
        land = self.add(engine, "Great Furnace", seat="B")
        self._bodies(session, "B", 2, "xander-local")
        borrowed = self._bodies(session, "D", 1, "xander-borrowed")[0]
        engine.change_control(borrowed.object_id, "B")
        self.assert_closed(session, xander)
        self.prepare(session, mana={"C": 1, "B": 2})
        self.checkpoint(session)
        self.cast(session, "A", kill, targets=[xander.ref])
        pending = self.resolve(session)
        self.assertEqual("semantic.target", pending.kind)
        chosen = session.act("pilot:A", {"action_id": "choose", "targets": ["B"]})
        self.assertTrue(chosen.ok, chosen.summary)
        self.resolve(session)
        self.assertEqual(["pilot:B"], session.pending_principals())
        self.assertEqual(1, session.state.pending_decision.payload_by_actor["B"]["count"])
        self.reject(session, "B", [land.ref])
        self.reject(session, "A", [borrowed.ref])
        session = self.replay(session, load=True)
        self.choose(session, "B", [borrowed.ref])
        self.assertIsNone(self.resolve(session))
        self.assertIn(borrowed.object_id, session.state.players["D"].zones["graveyard"])
        self.assertEqual("battlefield", session.state.cards[land.object_id].zone)
        self.replay(session)

    def test_zodiark_excludes_gods_from_each_players_half_and_counts_the_sacrifice(self):
        session = self.session(728110)
        zodiark = self.add(session.engine, "Zodiark, Umbral God", zone="hand")
        a_body = self._bodies(session, "A", 1, "zodiark-friendly")[0]
        victims = self._bodies(session, "B", 3, "zodiark-victim")
        self._bodies(session, "C", 1, "zodiark-short")
        self.assert_closed(session, zodiark)
        self.prepare(session, mana={"B": 5})
        self.checkpoint(session)
        self.cast(session, "A", zodiark)
        self.resolve(session)
        self.assertEqual(["pilot:B"], session.pending_principals())
        self.assertEqual(1, session.state.pending_decision.payload_by_actor["B"]["count"])
        self.choose(session, "B", [victims[0].ref])
        self.assertIsNone(self.resolve(session))
        self.assertEqual("battlefield", session.state.cards[a_body.object_id].zone)
        self.assertEqual("battlefield", session.state.cards[zodiark.object_id].zone)
        self.assertEqual(1, session.state.cards[zodiark.object_id].counters.get("+1/+1"))
        self.assertEqual("graveyard", session.state.cards[victims[0].object_id].zone)
        self.replay(session, load=True)

    def test_fraying_resolves_life_private_discard_and_public_sacrifice_before_state_actions(self):
        session = self.session(728120)
        spell = self.add(session.engine, "Fraying Omnipotence", zone="hand")
        private = self.add(session.engine, "Generic Bound Growth", seat="B", zone="hand",
                           ref="fraying-private-hand")
        bodies = {"B": self._bodies(session, "B", 3, "fraying"),
                  "C": self._bodies(session, "C", 2, "fraying")}
        for seat, life in zip("ABCD", (39, 1, 38, 37), strict=True):
            session.state.players[seat].life = life
        self.assert_closed(session, spell)
        self.prepare(session, mana={"C": 3, "B": 2})
        self.checkpoint(session)
        self.cast(session, "A", spell)
        hand_ids = {seat: list(session.state.players[seat].zones["hand"]) for seat in "ABCD"}
        self.resolve(session)
        self.assertEqual([19, 0, 19, 18], [session.state.players[seat].life for seat in "ABCD"])
        for seat in "ABCD":
            self.assertTrue(session.state.players["B"].in_game)
            self.assertEqual([f"pilot:{seat}"], session.pending_principals())
            count = (len(hand_ids[seat]) + 1) // 2
            self.assertEqual(count, session.state.pending_decision.payload_by_actor[seat]["count"])
            if seat == "B":
                self.assertNotIn(private.ref, json.dumps(session.packet("pilot:C", full=True)))
                session = self.replay(session, load=True)
            refs = [session.state.cards[oid].ref for oid in hand_ids[seat][:count]]
            self.choose(session, seat, refs)
            if seat != "D":
                self.assertEqual(hand_ids, {p: list(session.state.players[p].zones["hand"]) for p in "ABCD"})
        for seat, count in (("B", 2), ("C", 1)):
            self.assertTrue(session.state.players["B"].in_game)
            self.assertEqual([f"pilot:{seat}"], session.pending_principals())
            self.assertEqual(count, session.state.pending_decision.payload_by_actor[seat]["count"])
            refs = [card.ref for card in bodies[seat][:count]]
            self.choose(session, seat, refs)
            if seat == "B":
                self.assertTrue(all(session.state.cards[card.object_id].zone == "battlefield"
                                    for group in bodies.values() for card in group))
        self.assertIsNone(self.resolve(session))
        self.assertFalse(session.state.players["B"].in_game)
        self.assertEqual("graveyard", session.state.cards[bodies["C"][0].object_id].zone)
        self.assertEqual("battlefield", session.state.cards[bodies["C"][1].object_id].zone)
        self.replay(session)
