"""The original own-combat Entwine uses normal offers, payment and replay."""
from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.extra_combat_model import EXTRA_COMBAT_CAPABILITY
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry
from scripts.build_test_database import build_fixture_database
import test_self_public_zone_casting as public_zone

CARD = "Savage Beating"
CAP = "casting.restriction.self_fixed_public_condition"
FIXTURES = ROOT / "tests" / "fixtures"


class OwnCombatEntwineTests(InheritedNativeFixture, unittest.TestCase):
    prepare = public_zone.SelfPublicZoneRuntimeTests.prepare

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        folder = Path(cls.temporary.name)
        support = {
            "activation-restriction-material-cards.json": {"Grizzly Bears", "Plains", "Sol Ring"},
            "attached-cast-restriction-cards.json": {"Mountain"},
            "bound-effect-program-cards.json": {"Generic Bound Plains", "Generic Bound Growth"},
            "source-attack-alone-cards.json": {"Isamaru, Hound of Konda"},
        }
        pieces = [FIXTURES / "own-combat-entwine-cards.json"]
        for filename, names in support.items():
            contents = json.loads((FIXTURES / filename).read_bytes())
            contents["cards"] = [card for card in contents["cards"] if card["name"] in names]
            assert names == {card["name"] for card in contents["cards"]}, filename
            piece = folder / filename
            piece.write_text(json.dumps(contents), encoding="utf-8")
            pieces.append(piece)
        path = folder / "own-combat-entwine.sqlite3"
        build_fixture_database(pieces, path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Own combat", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"),
            DeckEntry("Generic Bound Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def compile_original(self, *, text=None, registry=None):
        record = self.db.lookup(CARD, fuzzy=False)
        if text is not None:
            record = replace(record, oracle_text=text)
        return compile_oracle_card(record, capability_registry=registry or self.registry,
                                   capability_profile="commander_review")

    def test_exact_modal_compilation_and_authority_withdrawals(self):
        ir = self.compile_original()
        self.assertEqual("exact", ir.status, ir.material_residuals)
        timing, resolution = ir.faces[0].nodes
        self.assertIn(CAP, timing.capability_dependencies)
        self.assertIn(EXTRA_COMBAT_CAPABILITY, resolution.capability_dependencies)
        self.assertEqual(["mode_1", "mode_2"], list(resolution.target_schema["modes"]))
        for cap in (CAP, EXTRA_COMBAT_CAPABILITY, "casting.additional_cost.entwine_fixed_modes"):
            with self.subTest(cap=cap):
                raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
                row = next(row for row in raw["capabilities"] if row["id"] == cap)
                row.update(status="blocked", blockers=["Scoped original-card control"])
                self.assertNotEqual("exact", self.compile_original(registry=CapabilityRegistry(raw)).status)
        text = self.db.lookup(CARD, fuzzy=False).oracle_text
        for changed in (
            text.replace("on your turn", "on an opponent’s turn"),
            text.replace("Untap all creatures you control", "Untap all creatures"),
            text.replace("Entwine {1}{R}", "Entwine {2}{R}"),
        ):
            with self.subTest(text=changed):
                self.assertNotEqual("exact", self.compile_original(text=changed).status)

    def scene(self, seed, *, active="A"):
        game = self.session(seed)
        source = self.add(game.engine, CARD, zone="hand")
        attacker = self.add(game.engine, "Grizzly Bears", ref="own-combat-attacker")
        attacker.acquired_control_turn_count = -1
        ally = self.add(game.engine, "Grizzly Bears", ref="own-combat-ally")
        opponent = self.add(game.engine, "Grizzly Bears", seat="B", ref="own-combat-opponent")
        rock = self.add(game.engine, "Sol Ring", ref="own-combat-tapped-artifact")
        private = self.add(game.engine, "Generic Bound Growth", seat="C", zone="hand", ref="own-combat-private")
        for card in (ally, opponent, rock):
            card.tapped = True
        for index in range(3):
            self.add(game.engine, "Mountain", ref=f"own-combat-mountain-{index}")
        for index in range(4):
            self.add(game.engine, "Plains", ref=f"own-combat-plains-{index}")
        self.assert_closed(game, source)
        self.prepare(game, active=active)
        self.checkpoint(game)
        return game, source, attacker, ally, opponent, rock, private

    def offer(self, game, source):
        decision = game.packet("pilot:A", full=True).get("decision")
        self.assertIsNotNone(decision)
        return next((row for row in decision["ctx"]["legal"]["actions"]
                     if row["id"] == "cast:" + source.ref), None)

    def reject(self, game, seat, command):
        before = authoritative_state_hash(game.state)
        result = game.act("pilot:" + seat, command)
        self.assertFalse(result.ok, result.summary)
        self.assertEqual(before, authoritative_state_hash(game.state))

    def has_double_strike(self, game, card):
        data = game.engine._effective_card_data(game.state.cards[card.object_id])
        return "double strike" in {name.casefold() for name in data.get("keywords", ())}

    def test_actual_entwine_requires_current_own_combat_and_full_price_then_replays(self):
        game, source, attacker, ally, opponent, rock, private = self.scene(4892071)
        cast = {"action_id": "cast:" + source.ref, "modes": ["mode_2", "mode_1"], "pay": "auto"}
        self.assertIsNone(self.offer(game, source))
        self.reject(game, "A", cast)
        self.attack(game, [(attacker, "B")])
        self.priority(game, "A")
        self.assertTrue(game.state.cards[attacker.object_id].tapped)
        combat = game.state.combat.damage_sequence_id
        offer = self.offer(game, source)
        self.assertIsNotNone(offer)
        cheap = next(option["id"] for option in offer["cost_options"]
                     if option["target_schema"]["legal_modes"] == ["mode_1"])
        self.reject(game, "A", {**cast, "cost_option": cheap})
        self.reject(game, "A", {**cast, "pay": "manual", "payment": {"W": 3, "R": 2}})
        self.reject(game, "C", cast)
        result = game.act("pilot:A", cast)
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(["mode_1", "mode_2"], game.state.stack[-1].modes)
        self.assertTrue(all(game.state.cards[card.object_id].tapped for card in (attacker, ally, opponent, rock)))
        game = self.replay(game, load=True)
        resolution_start = len(game.state.events)
        self.resolve(game)
        self.assertEqual(["effect.permanent.tap_state_set", "combat.extra.scheduled"],
                         [event.code for event in game.state.events[resolution_start:]
                          if event.code in {"effect.permanent.tap_state_set", "combat.extra.scheduled"}])
        self.assertEqual([False, False, True, True],
                         [game.state.cards[card.object_id].tapped for card in (attacker, ally, opponent, rock)])
        self.assertEqual([True, True, False], [self.has_double_strike(game, card) for card in (attacker, ally, opponent)])
        self.assertEqual(1, game.state.extra_combat_phases)
        for seat in "ABD":
            self.assertNotIn(private.ref, json.dumps(game.packet("pilot:" + seat, full=True)))
        game = self.replay(game, load=True)
        self.until(game, lambda g: g.state.pending_decision.kind == "combat.attackers")
        self.assertEqual(("A", "combat", 0), (game.state.active_player, game.state.phase, game.state.extra_combat_phases))
        self.assertNotEqual(combat, game.state.combat.damage_sequence_id)
        self.assertIn(attacker.ref, game.engine._attack_declaration_problem("A").domains)
        self.attack(game, [(attacker, "B")])
        self.until(game, lambda g: g.state.phase == "postcombat_main")
        damage = [row for event in game.state.events if event.code == "combat.damage"
                  for row in event.details["damage_events"] if row["source"] == attacker.ref and row["target"] == "B"]
        self.assertEqual([2, 2, 2, 2], [row["amount"] for row in damage])
        self.replay(game, load=True)

    def test_actual_other_combat_is_closed_and_each_single_mode_uses_its_own_effect(self):
        other, card, *_ = self.scene(4892072, active="B")
        self.until(other, lambda g: g.state.phase == "combat" and g.state.pending_decision.kind == "priority")
        self.priority(other, "A")
        self.assertEqual(("B", "combat"), (other.state.active_player, other.state.phase))
        self.assertIsNone(self.offer(other, card))
        self.reject(other, "A", {"action_id": "cast:" + card.ref, "modes": ["mode_1"], "pay": "auto"})
        self.replay(other, load=True)
        for index, modes in enumerate((["mode_1"], ["mode_2"])):
            with self.subTest(modes=modes):
                game, source, attacker, *_ = self.scene(4892073 + index)
                self.attack(game, [(attacker, "B")])
                self.priority(game, "A")
                result = game.act("pilot:A", {"action_id": "cast:" + source.ref, "modes": modes, "pay": "auto"})
                self.assertTrue(result.ok, result.summary)
                self.resolve(game)
                self.assertEqual(index, game.state.extra_combat_phases)
                self.assertEqual(index == 0, game.state.cards[attacker.object_id].tapped)
                self.assertEqual(index == 0, self.has_double_strike(game, attacker))
                self.replay(game, load=True)

    def test_dropping_own_combat_split_is_killed(self):
        def require_exact():
            actual = self.compile_original()
            self.assertEqual("exact", actual.status, actual.material_residuals)
        require_exact()
        with patch("quorune.compiler.own_combat_modal_templates.split_own_combat_modal", return_value=None):
            with self.assertRaises(AssertionError):
                require_exact()
