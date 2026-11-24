"""Ordinary X announcement, private reveal payment, and all five paid results."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.colored_hand_reveal_model import VARIABLE_COLORED_REVEAL_CAPABILITY
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry
from quorune.rules.evidence import validate_capability_evidence_index
from quorune.rules.evidence_storage import read_evidence_document
from scripts.build_test_database import build_fixture_database
from scripts.update_capability_evidence import build_index, discover_tests


class VariableColoredRevealActivationTests(InheritedNativeFixture, unittest.TestCase):
    cards = {
        "red": "Trial Red Reveal",
        "black": "Trial Black Reveal",
        "blue": "Trial Blue Reveal",
        "white": "Trial White Reveal",
        "green": "Trial Green Reveal",
    }
    ground = "Trial Ground Witness"
    sky = "Trial Flying Witness"
    growth = "Trial Stack Growth"
    balm = "Trial White Balm"
    basic = "Generic Bound Plains"

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        out = Path(cls.temporary.name) / "variable-reveal.sqlite3"
        fixtures = ROOT / "tests/fixtures"
        build_fixture_database([
            fixtures / "bound-effect-program-cards.json",
            fixtures / "variable-colored-reveal-activation-cards.json",
        ], out)
        cls.db = CardDatabase(out)
        cls.registry = load_default_capability_registry()
        commander = "Generic Bound Commander"
        cls.deck = DeckDefinition("Variable colored reveal", [
            DeckEntry(commander, 1, "commander"), DeckEntry(cls.basic, 30),
        ], [commander])

    def add_cost_scene(self, game, color, count):
        source = self.add(game.engine, self.cards[color], ref="EXACT-REVEAL-SOURCE")
        chosen = [self.add(game.engine, self.cards[color], zone="hand", ref=f"SELECTED-COLOR-{i}")
                  for i in range(count)]
        self.assert_closed(game, source)
        return source, chosen

    def begin(self, game, *, mana=None):
        game.state.config.semantic_policy = "trusted_only"
        self.prepare(game, mana=mana or {"C": 2})
        self.checkpoint(game)

    def offer(self, game, source):
        self.priority(game, "A")
        actions = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        return next(row for row in actions if row["id"].startswith(f"activate:{source.ref}:"))

    def activation(self, game, source, cards, *, targets=None):
        offer = self.offer(game, source)
        response = {"action_id": offer["id"], "x": len(cards),
                    "reveal_cards": [c.ref for c in cards], "pay": "auto"}
        if targets is not None:
            response["targets"] = [c.ref for c in targets]
        accepted = game.act("pilot:A", response)
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertEqual("graveyard", game.state.cards[source.object_id].zone)
        self.assertEqual(len(cards), game.state.stack[-1].x_value)
        return game.state.stack[-1]

    def test_white_live_selection_locks_x_and_limits_reveal_to_the_paid_scope(self):
        game = self.session(1073201)
        source, cards = self.add_cost_scene(game, "white", 1)
        balm = self.add(game.engine, self.balm, zone="hand", ref="REVEALED-WHITE-INSTANT")
        other = self.add(game.engine, self.cards["blue"], zone="hand", ref="WRONG-COLOR-HAND")
        foreign = self.add(game.engine, self.cards["white"], seat="B", zone="hand", ref="FOREIGN-MATCH")
        cards.append(balm)
        self.assert_closed(game, balm)
        self.begin(game, mana={"C": 2, "W": 1})
        offer = self.offer(game, source)
        schema = offer["choice_schema"]
        self.assertEqual({c.ref for c in cards}, set(schema["reveal_cards"]["legal_refs"]))
        self.assertEqual(2, schema["x"]["maximum"])
        before = authoritative_state_hash(game.state)
        for x, refs, principal in (
            (2, [cards[0].ref] * 2, "pilot:A"), (1, [other.ref], "pilot:A"),
            (1, [foreign.ref], "pilot:A"), (1, [cards[0].ref], "pilot:B"),
            (True, [cards[0].ref], "pilot:A"),
        ):
            rejected = game.act(principal, {"action_id": offer["id"], "x": x, "reveal_cards": refs, "pay": "auto"})
            self.assertFalse(rejected.ok)
            self.assertEqual(before, authoritative_state_hash(game.state))
        self.activation(game, source, cards)
        for card in cards:
            self.assertEqual(list("ABCD"), game.state.cards[card.object_id].revealed_to)
        self.assertNotIn(other.ref, json.dumps(game.packet("pilot:C", full=True)))
        game = self.replay(game, load=True)
        self.cast(game, "A", balm)
        self.resolve_top(game)
        self.assertEqual("graveyard", game.state.cards[balm.object_id].zone)
        self.assertEqual(2, game.state.stack[-1].x_value)
        before_expiry = game.state.players["A"].life
        self.assertIsNone(self.resolve(game))
        self.assertEqual(before_expiry + 6, game.state.players["A"].life)
        self.assertNotIn("C", game.state.cards[cards[0].object_id].revealed_to)
        self.replay(game, load=True)

    def test_white_zero_is_a_paid_source_sacrifice_with_no_life_change(self):
        game = self.session(1073202)
        source, cards = self.add_cost_scene(game, "white", 0)
        self.begin(game, mana={"C": 1})
        before = game.state.players["A"].life
        self.activation(game, source, cards)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(before, game.state.players["A"].life)
        self.replay(game, load=True)

    def test_green_live_result_applies_the_paid_count_to_one_creature(self):
        game = self.session(1073203)
        source, cards = self.add_cost_scene(game, "green", 1)
        target = self.add(game.engine, self.ground, seat="B")
        self.begin(game)
        power = game.engine._numeric_stat(target.object_id, "power")
        self.activation(game, source, cards, targets=[target])
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(power + 1, game.engine._numeric_stat(target.object_id, "power"))
        self.replay(game, load=True)

    def test_black_announces_a_graveyard_bound_and_exiles_its_owned_selection(self):
        game = self.session(1073204)
        source, cards = self.add_cost_scene(game, "black", 2)
        targets = [self.add(game.engine, self.ground, seat="B", zone="graveyard", ref=f"SHARED-GRAVEYARD-{i}")
                   for i in range(2)]
        unrelated = self.add(game.engine, self.ground, seat="C", zone="graveyard", ref="DIFFERENT-GRAVEYARD")
        self.begin(game)
        offer = self.offer(game, source)
        before = authoritative_state_hash(game.state)
        for x, refs in ((2, [targets[0].ref, unrelated.ref]), (1, [c.ref for c in targets])):
            failed = game.act("pilot:A", {
                "action_id": offer["id"], "x": x, "reveal_cards": [c.ref for c in cards[:x]], "targets": refs, "pay": "auto",
            })
            self.assertFalse(failed.ok)
            self.assertEqual(before, authoritative_state_hash(game.state))
        self.activation(game, source, cards, targets=targets)
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertTrue(all(game.state.cards[c.object_id].zone == "exile" for c in targets))
        self.assertEqual("graveyard", game.state.cards[unrelated.object_id].zone)
        self.replay(game, load=True)

    def test_red_uses_its_departed_source_and_only_current_ground_creatures(self):
        game = self.session(1073205)
        source, cards = self.add_cost_scene(game, "red", 2)
        ground = self.add(game.engine, self.ground, seat="B")
        flyer = self.add(game.engine, self.sky, seat="C")
        self.begin(game)
        self.activation(game, source, cards)
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("graveyard", game.state.cards[ground.object_id].zone)
        self.assertEqual("battlefield", game.state.cards[flyer.object_id].zone)
        self.assertEqual(0, game.state.cards[flyer.object_id].marked_damage)
        self.assertTrue(any(e.code == "effect.damage.fixed_set" for e in game.state.events))
        self.replay(game, load=True)

    def test_blue_asks_the_original_spell_controller_to_pay_announced_x(self):
        game = self.session(1073206)
        source, cards = self.add_cost_scene(game, "blue", 2)
        growth = self.add(game.engine, self.growth, seat="B", zone="hand")
        target = self.add(game.engine, self.ground, seat="B")
        self.assert_closed(game, growth)
        game.state.players["B"].mana_pool.update({"G": 1, "C": 2})
        self.begin(game)
        self.cast(game, "B", growth, [target.ref])
        old_power = game.engine._numeric_stat(target.object_id, "power")
        self.activation(game, source, cards, targets=[game.state.stack[-1]])
        pending = self.resolve(game)
        self.assertEqual(("semantic.choice", ["B"]), (pending.kind, pending.actors))
        self.assertEqual("counter_unless_pay", pending.payload_by_actor["B"]["operation"])
        paid = self.replay(game, load=True)
        declined = self.replay(game, load=True)
        result = paid.act("pilot:B", {"action_id": "choose", "pay": True, "plan": "auto"})
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(paid))
        self.assertEqual(0, paid.state.players["B"].mana_pool.get("C", 0))
        self.assertEqual(old_power + 3, paid.engine._numeric_stat(target.object_id, "power"))
        self.replay(paid, load=True)
        result = declined.act("pilot:B", {"action_id": "choose", "pay": False})
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(declined))
        self.assertEqual(old_power, declined.engine._numeric_stat(target.object_id, "power"))
        self.replay(declined, load=True)

    def test_price_lowering_and_verified_capability_withdrawal_fail_closed(self):
        def compile_card(name, registry):
            return compile_oracle_card(self.db.lookup(name), capability_registry=registry,
                                       capability_profile="commander_review")

        for name in self.cards.values():
            ir = compile_card(name, self.registry)
            self.assertEqual("exact", ir.status, ir.material_residuals)
            self.assertIn(VARIABLE_COLORED_REVEAL_CAPABILITY, ir.faces[0].nodes[0].capability_dependencies)
        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        cap = next(row for row in raw["capabilities"] if row["id"] == VARIABLE_COLORED_REVEAL_CAPABILITY)
        cap.update(status="blocked", blockers=["Variable color reveal price authority withdrawn."])
        registry = CapabilityRegistry(raw)
        doc = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        index = build_index(registry_value=raw, declaration_source=doc,
                            discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
        for name in self.cards.values():
            self.assertNotEqual("exact", compile_card(name, registry).status)
        with patch("quorune.compiler.variable_colored_reveal_price.variable_colored_reveal_price", return_value=None):
            self.assertNotEqual("exact", compile_card(self.cards["white"], self.registry).status)
