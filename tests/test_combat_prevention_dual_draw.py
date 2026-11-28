"""The granted combat damage ability keeps both draws when prevention is prohibited."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT, packet_without_decision_capability
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.compiler.activated_costs import parse_compiled_activation_line
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database


class CombatPreventionDualDrawTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        target = Path(cls.temporary.name) / "combat-prevention-dual-draw.sqlite3"
        fixtures = ROOT / "tests/fixtures"
        build_fixture_database([
            fixtures / "bound-effect-program-cards.json",
            fixtures / "combat-prevention-dual-draw-cards.json",
            fixtures / "captured-draw-keyword-grant-cards.json",
        ], target)
        cls.db = CardDatabase(target)
        cls.registry = load_default_capability_registry()
        commander = "Generic Bound Commander"
        cls.deck = DeckDefinition("Combat damage and draw", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], [commander])

    def test_activation_reader_retains_the_whole_printed_granted_replacement(self):
        record = self.db.lookup("Sokrates, Athenian Teacher", fuzzy=False)
        raw_line = record.oracle_text.splitlines()[-1]
        abilities, _ = parse_compiled_activation_line(raw_line, card_name=record.name, keywords=record.keywords)
        ability, = abilities
        self.assertTrue(ability.tap_source)
        self.assertEqual(raw_line.split(": ", 1)[1], ability.effect_text)
        self.assertFalse(ability.mana_ability)
        self.assertIsNone(ability.builtin_semantic_key)

    def test_original_compiles_the_complete_granted_combat_damage_ability(self):
        record = self.db.lookup("Sokrates, Athenian Teacher", fuzzy=False)
        ir = compile_oracle_card(record, capability_registry=self.registry, capability_profile="commander_review")
        self.assertEqual("exact", ir.status, [residual.text for residual in ir.material_residuals])
        node, = (item for item in ir.faces[0].nodes if item.kind == "activated_ability")
        self.assertTrue(node.cost["tap_source"])
        self.assertEqual(["creature"], node.target_schema["types_any"])

    def test_actual_grant_draws_for_the_attacker_controller_and_damaged_player(self):
        for unpreventable in (False, True):
            with self.subTest(unpreventable=unpreventable):
                self.verify_combat_draws(unpreventable)

    def verify_combat_draws(self, unpreventable):
        from quorune.damage_prevention_permission import temporary_damage_prevention_prohibition
        from quorune.semantic_runtime.damage_permissions import apply_temporary_damage_prevention_prohibition

        game = self.session(61550311 + int(unpreventable))
        source = self.add(game.engine, "Sokrates, Athenian Teacher")
        creature = self.add(game.engine, "Generic Bound Body")
        self.assert_closed(game, source)
        game.state.config.semantic_policy = "trusted_only"
        self.prepare(game)
        if unpreventable:
            effect = {**temporary_damage_prevention_prohibition(), "source": source.ref}
            apply_temporary_damage_prevention_prohibition(game.engine, "A", effect)
        self.checkpoint(game)

        options = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        activation, = (row for row in options if row.get("source") == source.ref and row.get("ability"))
        result = game.act("pilot:A", {"action_id": activation["id"], "targets": [creature.ref]})
        self.assertTrue(result.ok, result.summary)
        self.resolve_top(game)
        life_before = game.state.players["B"].life
        hands_before = {seat: len(player.zones["hand"]) for seat, player in game.state.players.items()}
        tops = [game.state.cards[game.state.players[seat].zones["library"][-1]].ref for seat in "AB"]
        power = game.engine._numeric_stat(creature.object_id, "power")
        self.assertGreaterEqual(power, 2)

        self.attack(game, [(creature, "B")])
        self.until(game, lambda current: current.state.phase == "postcombat_main")

        self.assertEqual(life_before - (power if unpreventable else 0), game.state.players["B"].life)
        received = {seat: len(player.zones["hand"]) - hands_before[seat] for seat, player in game.state.players.items()}
        self.assertEqual({"A": power // 2, "B": power // 2, "C": 0, "D": 0}, received)
        for observer in "CD":
            projected = json.dumps(packet_without_decision_capability(game.packet(f"pilot:{observer}", full=True)))
            self.assertTrue(all(ref not in projected for ref in tops))
        self.replay(game, load=True)

    def test_omitting_the_draw_result_changes_the_actual_combat(self):
        with patch("quorune.rules.combat_prevention_draw.record_prevention_draw") as skipped:
            with self.assertRaises(AssertionError):
                self.verify_combat_draws(unpreventable=True)
        skipped.assert_called_once()

    def test_pending_combat_draw_replays_without_repeating_damage_or_the_other_draw(self):
        game = self.session(61550313)
        source = self.add(game.engine, "Sokrates, Athenian Teacher")
        creature = self.add(game.engine, "Generic Bound Body")
        words = self.add(game.engine, "Words of Wilding", seat="B")
        for card in (source, words):
            self.assert_closed(game, card)
        game.state.config.semantic_policy = "trusted_only"
        self.prepare(game)
        game.state.players["B"].mana_pool["C"] = 2
        self.checkpoint(game)
        for _ in range(2):
            self.activate_and_resolve(game, "B", words)
        self.activate_and_resolve(game, "A", source, targets=(creature.ref,))
        sizes = {seat: len(game.state.players[seat].zones["hand"]) for seat in "AB"}

        self.attack(game, [(creature, "B")])
        self.until(game, lambda current: current.state.pending_decision is not None
                   and current.state.pending_decision.kind == "draw.replacement")
        self.assertEqual(["B"], game.state.pending_decision.actors)
        self.assertIsNone(game.packet("pilot:A", full=True)["decision"])
        self.assertEqual(sizes["A"] + 1, len(game.state.players["A"].zones["hand"]))

        game = self.replay(game, load=True)
        options = game.packet("pilot:B", full=True)["decision"]["ctx"]["options"]
        self.assertEqual(2, len(options))
        result = game.act("pilot:B", {"action_id": "choose", "choice": options[0]["id"]})
        self.assertTrue(result.ok, result.summary)
        self.until(game, lambda current: current.state.phase == "postcombat_main")
        self.assertEqual(sizes["A"] + 1, len(game.state.players["A"].zones["hand"]))
        self.assertEqual(sizes["B"], len(game.state.players["B"].zones["hand"]))
        self.assertEqual(1, sum(event.code == "combat.damage" for event in game.state.events))
        tokens = [card for card in game.state.cards.values() if card.is_token and card.zone == "battlefield"]
        self.assertEqual(["B"], [card.controller for card in tokens])
        self.replay(game, load=True)

    def activate_and_resolve(self, game, seat, source, *, targets=()):
        self.priority(game, seat)
        options = game.packet(f"pilot:{seat}", full=True)["decision"]["ctx"]["legal"]["actions"]
        action, = (row for row in options if row.get("source") == source.ref and row.get("ability"))
        result = game.act(f"pilot:{seat}", {"action_id": action["id"], "targets": list(targets)})
        self.assertTrue(result.ok, result.summary)
        self.resolve_top(game)

    def test_withdrawing_the_granted_combat_owner_leaves_an_activation_residual(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.combat_prevention_draw import CAPABILITY

        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        item = next(row for row in raw["capabilities"] if row["id"] == CAPABILITY)
        item.update(status="blocked", blockers=["The granted combat draw owner is unavailable"])
        withheld = CapabilityRegistry(raw)
        withheld.mark_evidence_verified("0" * 64)
        record = self.db.lookup("Sokrates, Athenian Teacher", fuzzy=False)
        ir = compile_oracle_card(record, capability_registry=withheld, capability_profile="commander_review")
        self.assertNotEqual("exact", ir.status)
        self.assertTrue(any(CAPABILITY in blocker for residual in ir.material_residuals for blocker in residual.blockers))
