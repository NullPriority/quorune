"""Pinned discard-after-occurrence, destination replacement and committed counts."""

from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.discard_result_model import (
    DISCARD_RESULT_CAPABILITY,
    EVENT_PLAYER_DISCARD_RESULT_CAPABILITY as CAPABILITY,
    PlayerDiscardCounts,
    event_player_discard_result_effect,
)
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.graveyard_arrival_order import GRAVEYARD_ARRIVAL_ORDER_CAPABILITY
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


NAMES = ("Robber Fly", "Shocker", "Barbed Shocker")
PROFILE = "commander_review"
FIXTURES = ROOT / "tests/fixtures"


class EventPlayerDiscardResultTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        folder = Path(cls.temporary.name)
        basic = json.loads((FIXTURES / "bound-effect-program-cards.json").read_bytes())
        basic["cards"] = [row for row in basic["cards"] if row["name"] == "Generic Bound Plains"]
        basic["rulings"] = []
        base = folder / "base.json"
        base.write_text(json.dumps(basic), encoding="utf-8")
        path = folder / "event-player-discard-result.sqlite3"
        build_fixture_database([base, FIXTURES / "event-player-discard-result-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Event-player discard", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def registered(self, name):
        card = self.db.lookup(name, fuzzy=False)
        outcome, semantics = register_card(self.db, self.registry, name)
        self.assertTrue(outcome["strict_capability_ready"], outcome["blockers"])
        program, = (p for p in semantics.programs_for_oracle(card.oracle_id)
                    if p.event in {"creature.becomes_blocked", "damage.dealt.self"})
        return card, program

    def test_complete_originals_bind_distinct_source_occurrences(self):
        for name in NAMES:
            with self.subTest(name=name):
                card, program = self.registered(name)
                event, player = (("creature.becomes_blocked", "$context.defending_player")
                                 if name == NAMES[0] else ("damage.dealt.self", "$context.target"))
                self.assertEqual(event, program.event)
                self.assertIsNone(program.target_schema)
                self.assertIn(CAPABILITY, program.capability_dependencies)
                self.assertIn(DISCARD_RESULT_CAPABILITY, program.capability_dependencies)
                self.assertIn(GRAVEYARD_ARRIVAL_ORDER_CAPABILITY, program.capability_dependencies)
                self.assertEqual((event_player_discard_result_effect(player),), tuple(program.effects))
                self.assertEqual("exact", compile_oracle_card(
                    card, capability_registry=self.registry, capability_profile=PROFILE,
                ).status)
        robber, _ = self.registered(NAMES[0])
        shocker, _ = self.registered(NAMES[1])
        for original, text in (
            (robber, robber.oracle_text.replace("becomes blocked,", "becomes blocked by a creature,")),
            (robber, robber.oracle_text.replace("that many cards.", "that many cards plus one.")),
            (shocker, shocker.oracle_text.replace("damage to a player", "damage to an opponent")),
            (shocker, shocker.oracle_text + " Then they gain 1 life."),
        ):
            with self.subTest(text=text):
                ir = compile_oracle_card(replace(original, oracle_text=text),
                                         capability_registry=self.registry, capability_profile=PROFILE)
                self.assertNotEqual("exact", ir.status)

        group = replace(shocker, oracle_text=shocker.oracle_text.replace("that player discards", "each player discards"))
        ir = compile_oracle_card(group, capability_registry=self.registry, capability_profile=PROFILE)
        self.assertEqual("exact", ir.status)
        node, = (n for face in ir.faces for n in face.nodes if n.event == "damage.dealt.self")
        self.assertNotIn(CAPABILITY, node.capability_dependencies)
        self.assertEqual((1, "all"), (node.effects[0]["schema_version"], node.effects[0]["players"]))

    def test_raw_payload_cannot_change_origin_reduction_or_omit_owner(self):
        _, program = self.registered(NAMES[1])
        effect, = program.effects
        for changed in (
            replace(program, event="creature.becomes_blocked"),
            replace(program, active_zone="graveyard"),
            replace(program, event_condition={"field": "target_kind", "op": "eq", "value": "player"}),
            replace(program, effects=({**effect, "players": ["$context.defending_player"]},)),
            replace(program, effects=({**effect, "players": ["$controller"]},)),
            replace(program, effects=({**effect, "schema_version": 1},)),
            replace(program, effects=({**effect, "reduction": "greatest"},)),
            replace(program, effects=({**effect, "offset": True},)),
        ):
            with self.subTest(changed=changed.to_dict()):
                result = bind_semantic_program_runtime(changed, capability_registry=self.registry, profile=PROFILE)
                self.assertFalse(result["strict"], result)
        deps = [d for d in program.capability_dependencies if d != CAPABILITY]
        changed = replace(program, capability_dependencies=deps,
                          capability_closure=self.registry.closure(deps, profile=PROFILE).to_dict())
        result = bind_semantic_program_runtime(changed, capability_registry=self.registry, profile=PROFILE)
        self.assertFalse(result["strict"], result)
        self.assertIn("capability:undeclared_runtime_dependency:" + CAPABILITY, result["blockers"])

    def hand_members(self, game, seat):
        return tuple(game.state.players[seat].zones["hand"])

    def assert_player_redrew(self, game, before, player, destination="graveyard"):
        for seat, originals in before.items():
            hand = self.hand_members(game, seat)
            if seat != player:
                self.assertEqual(originals, hand)
                continue
            self.assertEqual(len(originals), len(hand))
            self.assertFalse(set(originals).intersection(hand))
            for obj in originals:
                self.assertEqual(destination, game.state.cards[obj].zone)
            for viewer in ("pilot:A", "pilot:C", "pilot:D", "spectator"):
                if viewer == f"pilot:{player}":
                    continue
                packet = json.dumps(game.packet(viewer, full=True))
                for obj in hand:
                    self.assertNotIn(game.state.cards[obj].ref, packet)

    def pending_robber_fly(self, seed, *, replace_destination):
        game = self.session(seed)
        source = self.add(game.engine, NAMES[0])
        other = self.add(game.engine, "Grizzly Bears", ref="other-attacker")
        walker = self.add(game.engine, "Ral, Caller of Storms", seat="B")
        walker.counters["loyalty"] = 5
        blockers = [self.add(game.engine, "Storm Crow", seat="B", ref=f"bird-{i}") for i in range(2)]
        self.add(game.engine, "Grizzly Bears", seat="C", ref="other-defending-blocker")
        if replace_destination:
            self.assert_closed(game, self.add(game.engine, "Rest in Peace"))
        self.add(game.engine, "Counterspell", seat="B", zone="hand", ref="sealed-defender-hand")
        self.add(game.engine, "Shock", seat="C", zone="hand", ref="different-combat-lane")
        for creature in (source, other):
            creature.acquired_control_turn_count = -1
        self.assert_closed(game, source)
        self.prepare(game)
        self.checkpoint(game)
        self.attack(game, [(source, walker.ref), (other, "C")])
        self.until(game, lambda s: s.state.pending_decision.kind == "combat.blockers")
        for seat, blocks in (("B", {b.ref: source.ref for b in blockers}), ("C", {})):
            result = game.act(f"pilot:{seat}", {"action_id": "block", "blocks": blocks})
            self.assertTrue(result.ok, result.summary)
        self.until(game, lambda s: self.trigger_count(s, source) == 1)
        self.assertEqual(1, len(game.state.stack))
        return game

    def choose_graveyard_order(self, game, before, owner, *, excluded=()):
        decision = self.resolve(game)
        self.assertIsNotNone(decision)
        self.assertEqual(("semantic.choice", [owner]), (decision.kind, decision.actors))
        pilot = f"pilot:{owner}"
        order = [oid for oid in reversed(before[owner]) if oid not in excluded]
        refs = [game.state.cards[oid].ref for oid in order]
        choose, = game.packet(pilot, full=True)["decision"]["legal_actions"]
        self.assertEqual(set(refs), set(choose["choice_schema"]["legal_refs"]))
        self.assertEqual("bottom_to_top", choose["choice_schema"]["order"])
        self.assertEqual(before[owner], self.hand_members(game, owner))
        for viewer in ("pilot:A", "pilot:C", "pilot:D", "spectator"):
            if viewer == pilot:
                continue
            packet = json.dumps(game.packet(viewer, full=True))
            self.assertTrue(all(ref not in packet for ref in refs))
        game = self.replay(game, load=True)
        before_attempt = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:A", {"action_id": "choose", "cards": refs}).ok)
        self.assertFalse(game.act(pilot, {"action_id": "choose", "cards": [refs[0]] * len(refs)}).ok)
        self.assertEqual(before_attempt, authoritative_state_hash(game.state))
        response = game.act(pilot, {"action_id": "choose", "cards": refs})
        self.assertTrue(response.ok, response.summary)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(order, game.state.players[owner].zones["graveyard"][-len(order):])
        return game

    def test_actual_robber_fly_orders_only_the_retained_defenders_hand(self):
        game = self.pending_robber_fly(509370191, replace_destination=False)
        before = {seat: self.hand_members(game, seat) for seat in "ABCD"}
        game = self.choose_graveyard_order(game, before, "B")
        self.assert_player_redrew(game, before, "B")
        self.replay(game, load=True)

    def test_actual_robber_fly_discards_one_lane_through_destination_replacement(self):
        game = self.pending_robber_fly(509370181, replace_destination=True)
        before = {seat: self.hand_members(game, seat) for seat in "ABCD"}
        for viewer in ("pilot:A", "pilot:C", "pilot:D", "spectator"):
            self.assertNotIn("sealed-defender-hand", json.dumps(game.packet(viewer, full=True)))
        departed = self.replay(game, load=True)
        self.priority(departed, "B")
        result = departed.act("pilot:B", {"action_id": "concede", "choices": {"confirm_concede": True}})
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(departed))
        for seat in "ACD":
            self.assertEqual(before[seat], self.hand_members(departed, seat))
        self.replay(departed)
        self.assertIsNone(self.resolve(game))
        self.assert_player_redrew(game, before, "B", destination="exile")
        self.replay(game, load=True)

    def pending_shocker(self, seed):
        game = self.session(seed)
        source = self.add(game.engine, NAMES[1])
        source.acquired_control_turn_count = -1
        aura = self.add(game.engine, "Hermetic Study", zone="hand")
        self.add(game.engine, "Counterspell", seat="B", zone="hand", ref="damaged-seat-hand")
        for i in range(2):
            self.add(game.engine, "Island", ref=f"island-{i}")
        for card in (source, aura):
            self.assert_closed(game, card)
        self.prepare(game)
        self.checkpoint(game)
        self.cast_for(game, "A", aura, [source.ref])
        self.assertIsNone(self.resolve(game))
        self.priority(game, "A")
        actions = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        action, = [row for row in actions if row["id"].startswith(f"activate:{source.ref}:")]
        command = {"action_id": action["id"], "targets": ["B"], "pay": "auto"}
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:C", command).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        accepted = game.act("pilot:A", command)
        self.assertTrue(accepted.ok, accepted.summary)
        self.resolve_top(game)
        self.assertEqual(39, game.state.players["B"].life)
        self.assertFalse(game.state.combat.attackers)
        self.assertEqual(1, self.trigger_count(game, source))
        return game

    def test_actual_shocker_uses_nondeclared_noncombat_damage_recipient(self):
        game = self.pending_shocker(120406182)
        before = {seat: self.hand_members(game, seat) for seat in "ABCD"}
        self.assertGreater(len(before["B"]), 1)
        game = self.replay(game, load=True)
        game = self.choose_graveyard_order(game, before, "B")
        self.assert_player_redrew(game, before, "B")
        self.replay(game, load=True)

    def test_actual_barbed_shocker_paid_cast_haste_and_discard_quantity(self):
        game = self.session(120406183)
        source = self.add(game.engine, NAMES[2], zone="hand")
        self.add(game.engine, "Grizzly Bears", seat="B", ref="available-blocker")
        self.add(game.engine, "Counterspell", seat="B", zone="hand", ref="barbed-defender-hand")
        self.assert_closed(game, source)
        self.prepare(game, mana={"C": 3, "R": 1})
        self.checkpoint(game)
        self.cast_for(game, "A", source)
        self.assertIsNone(self.resolve(game))
        before = {seat: self.hand_members(game, seat) for seat in "ABCD"}
        self.assertGreater(len(before["B"]), 2)
        self.attack(game, [(source, "B")])
        self.until(game, lambda s: s.state.pending_decision.kind == "combat.blockers")
        result = game.act("pilot:B", {"action_id": "block", "blocks": {}})
        self.assertTrue(result.ok, result.summary)
        self.until(game, lambda s: self.trigger_count(s, source) == 1)
        self.assertEqual(38, game.state.players["B"].life)
        game = self.choose_graveyard_order(game, before, "B")
        self.assert_player_redrew(game, before, "B")
        self.replay(game)

    def test_actual_shocker_orders_only_arrivals_after_a_replacement_choice(self):
        from quorune.replacement_effects import ReplacementClass, ReplacementEffect
        from quorune.semantic_runtime import zone_replacements

        game = self.pending_shocker(120406194)
        before = {seat: self.hand_members(game, seat) for seat in "ABCD"}
        redirect = next(oid for oid in before["B"] if game.state.cards[oid].ref == "damaged-seat-hand")
        # Prepare two represented destination replacements for the same event.
        effects = tuple(ReplacementEffect(
            f"fixture:discard:{destination}", f"fixture:{destination}", "zone.change", ReplacementClass.OTHER,
            conditions={
                "object_ref": {"eq": "damaged-seat-hand"},
                "origin": {"eq": "hand"}, "destination": {"eq": "graveyard"},
            },
            operations=({"op": "set", "field": "destination", "value": destination},),
        ) for destination in ("exile", "library"))
        collect = zone_replacements.collect_zone_change_replacement_effects

        def selective(host, **kwargs):
            return (*collect(host, **kwargs), *effects)

        with patch.object(zone_replacements, "collect_zone_change_replacement_effects", side_effect=selective):
            pending = self.resolve(game)
            self.assertEqual(("replacement.order", ["B"]), (pending.kind, pending.actors))
            self.assertEqual(before["B"], self.hand_members(game, "B"))
            game = self.replay(game, load=True)
            options = game.packet("pilot:B", full=True)["decision"]["ctx"]["options"]
            option, = [row["id"] for row in options if row["id"] == "fixture:discard:exile"]
            result = game.act("pilot:B", {"action_id": "choose", "replacement": option})
            self.assertTrue(result.ok, result.summary)
            game = self.choose_graveyard_order(game, before, "B", excluded=(redirect,))
            self.assertEqual("exile", game.state.cards[redirect].zone)
            self.assertEqual(len(before["B"]), len(self.hand_members(game, "B")))
            self.assertFalse(set(before["B"]).intersection(self.hand_members(game, "B")))
            for viewer in "ACD":
                packet = json.dumps(game.packet(f"pilot:{viewer}", full=True))
                self.assertTrue(all(game.state.cards[oid].ref not in packet for oid in self.hand_members(game, "B")))
            self.replay(game, load=True)

    def test_live_order_witness_kills_omitted_order_requirement(self):
        from quorune.zone_transitions import ZoneTransitionOwner

        game = self.pending_shocker(120406195)
        before = {seat: self.hand_members(game, seat) for seat in "ABCD"}
        original = ZoneTransitionOwner.move_cards_simultaneously

        def without_order(owner, changes, **options):
            options["require_graveyard_order"] = False
            return original(owner, changes, **options)

        with patch.object(ZoneTransitionOwner, "move_cards_simultaneously", without_order):
            with self.assertRaises(AssertionError):
                self.choose_graveyard_order(game, before, "B")

    def test_actual_discard_count_witness_kills_zero_quantity_mutant(self):
        from quorune.semantic_runtime import executor

        game = self.pending_shocker(120406184)
        before = {seat: self.hand_members(game, seat) for seat in "ABCD"}
        execute = executor.execute_discard_result

        def zero(sink, intent):
            original = execute(sink, intent)
            return PlayerDiscardCounts(tuple((player, 0) for player, _ in original.counts))

        with patch.object(executor, "execute_discard_result", side_effect=zero):
            game = self.choose_graveyard_order(game, before, "B")
        with self.assertRaises(AssertionError):
            self.assert_player_redrew(game, before, "B")

    def test_verified_withdrawals_leave_other_event_and_legacy_draws_available(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests

        source = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        discovered = set(discover_tests(ROOT).values())
        for missing, affected in (
            (CAPABILITY, set(NAMES)),
            (GRAVEYARD_ARRIVAL_ORDER_CAPABILITY, set(NAMES)),
            ("trigger.event.normalized_damage", set(NAMES[1:])),
            ("trigger.event.normalized_public_action", {NAMES[0]}),
        ):
            with self.subTest(missing=missing):
                raw = deepcopy(source)
                next(row for row in raw["capabilities"] if row["id"] == missing).update(
                    status="blocked", blockers=["Event-player discard dependency withdrawn"])
                registry = CapabilityRegistry(raw)
                evidence = build_index(registry_value=raw, declaration_source=declarations,
                                       discovered_test_ids=discovered)
                registry.mark_evidence_verified(validate_capability_evidence_index(evidence, registry=registry))
                for name in (*NAMES, "Tolarian Winds", "Windfall"):
                    outcome, _ = register_card(self.db, registry, name)
                    self.assertEqual(name not in affected, outcome["strict_capability_ready"],
                                     (name, outcome["blockers"]))
