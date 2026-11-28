"""Native own-combat modal legality and temporary mana for Glorious Sunrise."""

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
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.temporary_ability_grant import TEMPORARY_LAND_MANA_GRANT_CAPABILITY
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


CARD = "Glorious Sunrise"
PROFILE = "commander_review"


class GloriousSunriseNativeTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "glorious-sunrise.sqlite3"
        fixtures = ROOT / "tests/fixtures"
        build_fixture_database([
            fixtures / "glorious-sunrise-cards.json",
            fixtures / "public-state-trigger-cards.json",
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition(
            "Glorious Sunrise fixture",
            [DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30)],
            ["Isamaru, Hound of Konda"],
        )

    @staticmethod
    def current(game, card):
        return game.state.cards[card.object_id]

    def begin_choice(self, game, source, *spells):
        for card in (source, *spells):
            self.assert_closed(game, card)
        game.state.config.semantic_policy = "trusted_only"
        self.prepare(game)
        self.checkpoint(game)
        self.until(game, lambda current: (
            current.state.step == "beginning_combat"
            and current.state.pending_decision is not None
            and current.state.pending_decision.kind == "semantic.target"
        ))
        decision = game.packet("pilot:A", full=True)["decision"]
        for seat in "BCD":
            self.assertIsNone(game.packet("pilot:" + seat, full=True)["decision"])
        return decision["ctx"]["target_schema"]

    def accept(self, game, seat, command):
        result = game.act("pilot:" + seat, command)
        self.assertTrue(result.ok, result.summary)

    def reject(self, game, seat, command):
        before = authoritative_state_hash(game.state)
        result = game.act("pilot:" + seat, command)
        self.assertFalse(result.ok, result.summary)
        self.assertEqual(before, authoritative_state_hash(game.state))

    @staticmethod
    def mode(mode_id, targets=()):
        return {"action_id": "choose", "modes": [mode_id], "targets": list(targets)}

    def test_complete_original_binds_four_modes_and_the_source_spanned_mana_child(self):
        ready, semantics = register_card(self.db, self.registry, CARD)
        self.assertTrue(ready["strict_capability_ready"], ready["blockers"])
        self.assertEqual("capability_closed", ready["trust_basis"])
        record = self.db.lookup(CARD, fuzzy=False)
        ir = compile_oracle_card(record, capability_registry=self.registry, capability_profile=PROFILE)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        outer, quoted = ir.faces[0].nodes
        self.assertEqual(("triggered_ability", "granted_mana_ability"), (outer.kind, quoted.kind))
        self.assertEqual("{T}: Add {G}{G}{G}", record.oracle_text[quoted.span.start:quoted.span.end])
        programs = semantics.programs_for_oracle(record.oracle_id)
        trigger, = [program for program in programs if program.event == "step.begin"]
        modes = trigger.target_schema["modes"]
        self.assertEqual(["mode_1", "mode_2", "mode_3", "mode_4"], list(modes))
        for name in ("mode_1", "mode_3", "mode_4"):
            self.assertEqual([], modes[name]["groups"])
        land, = modes["mode_2"]["groups"]
        self.assertEqual(["land"], land["types_any"])
        fragment, = modes["mode_2"]["effects"][0]["ability_fragments"]
        child = semantics.get(fragment["value"]["semantic_key"])
        self.assertIsNotNone(child)
        self.assertTrue(child.trust_level == "trusted" and not child.requires_arbiter)
        deps = tuple(cap for cap in trigger.capability_dependencies if cap != TEMPORARY_LAND_MANA_GRANT_CAPABILITY)
        missing = replace(trigger, capability_dependencies=deps,
                          capability_closure=self.registry.closure(deps, profile=PROFILE).to_dict())
        bound = bind_semantic_program_runtime(missing, capability_registry=self.registry, profile=PROFILE)
        self.assertFalse(bound["strict"])
        self.assertIn(f"capability:undeclared_runtime_dependency:{TEMPORARY_LAND_MANA_GRANT_CAPABILITY}",
                      bound["blockers"])

    def test_omitting_the_land_grant_compiler_leaves_the_original_unresolved(self):
        record = self.db.lookup(CARD, fuzzy=False)
        with patch("quorune.compiler.temporary_quoted_ability_nodes.temporary_land_mana_mode_nodes",
                   return_value=None):
            ir = compile_oracle_card(record, capability_registry=self.registry, capability_profile=PROFILE)
        self.assertNotEqual("exact", ir.status)
        self.assertTrue(ir.material_residuals)

    def test_actual_creature_mode_affects_the_controllers_creatures(self):
        game = self.session(60330041)
        source = self.add(game.engine, CARD)
        own = self.add(game.engine, "Grizzly Bears", ref="sunrise-own-bear")
        other = self.add(game.engine, "Grizzly Bears", seat="B", ref="sunrise-foreign-bear")
        schema = self.begin_choice(game, source)
        self.assertIn("mode_1", schema["legal_modes"])
        self.reject(game, "A", self.mode("mode_1", [own.ref]))
        self.reject(game, "A", {"action_id": "choose", "modes": ["mode_1", "mode_4"]})
        self.accept(game, "A", self.mode("mode_1"))
        self.assertIsNone(self.resolve(game))
        for card, power, trample in ((own, 3, True), (other, 2, False)):
            current = self.current(game, card)
            self.assertEqual(power, game.engine._numeric_stat(current.object_id, "power"))
            self.assertEqual(power, game.engine._numeric_stat(current.object_id, "toughness"))
            keywords = game.engine._effective_card_data(current).get("keywords", ())
            self.assertEqual(trample, "trample" in {keyword.casefold() for keyword in keywords})
        self.replay(game, load=True)

    def test_actual_land_mode_uses_recipient_controller_and_expires_at_cleanup(self):
        game = self.session(60330042)
        source = self.add(game.engine, CARD)
        land = self.add(game.engine, "Island", seat="B", ref="sunrise-target-island")
        decoy = self.add(game.engine, "Grizzly Bears")
        schema = self.begin_choice(game, source)
        self.assertEqual(["mode_1", "mode_2", "mode_3", "mode_4"], schema["legal_modes"])
        group, = schema["mode_schemas"]["mode_2"]["groups"]
        self.assertEqual([land.ref], group["legal_refs"])
        self.reject(game, "B", self.mode("mode_2", [land.ref]))
        self.reject(game, "A", self.mode("mode_2", []))
        self.reject(game, "A", self.mode("mode_2", [decoy.ref]))
        self.reject(game, "A", self.mode("mode_2", [land.ref, land.ref]))
        game = self.replay(game, load=True)
        self.accept(game, "A", self.mode("mode_2", [land.ref]))
        self.assertIsNone(self.resolve(game))
        self.assertFalse(self.green_mana_abilities(game, self.current(game, source)))
        self.priority(game, "B")

        current = self.current(game, land)
        ability, = self.green_mana_abilities(game, current)
        action_id = f"activate:{current.ref}:{ability.ability_id}"
        legal = game.packet("pilot:B", full=True)["decision"]["ctx"]["legal"]["actions"]
        self.assertTrue(any(action["id"] == action_id for action in legal))
        self.accept(game, "B", {"action_id": action_id, "pay": "auto"})
        self.assertTrue(self.current(game, land).tapped)
        self.assertEqual(3, game.state.players["B"].mana_pool["G"])
        self.until(game, lambda current: current.state.active_player == "B"
                   and current.state.phase == "precombat_main")
        self.assertFalse(self.green_mana_abilities(game, self.current(game, land)))
        self.replay(game, load=True)

    @staticmethod
    def green_mana_abilities(game, land):
        return tuple(ability for ability in game.engine._activated_abilities(land)
                     if ability.mana_ability
                     and any(output.bundle["G"] == 3 for output in ability.fixed_mana_outputs))

    def test_actual_land_mode_is_unavailable_without_a_land_and_life_mode_still_resolves(self):
        game = self.session(60330043)
        source = self.add(game.engine, CARD)
        schema = self.begin_choice(game, source)
        self.assertEqual(["mode_1", "mode_3", "mode_4"], schema["legal_modes"])
        self.reject(game, "A", self.mode("mode_2"))
        self.accept(game, "A", self.mode("mode_4"))
        self.assertIsNone(self.resolve(game))
        self.assertEqual([43, 40, 40, 40], [game.state.players[seat].life for seat in "ABCD"])
        self.replay(game, load=True)

    def test_actual_target_leaving_prevents_the_land_grant(self):
        game = self.session(60330044)
        source = self.add(game.engine, CARD)
        land = self.add(game.engine, "Island", ref="sunrise-land-in-response")
        boom = self.add(game.engine, "Boomerang", seat="B", zone="hand")
        self.add(game.engine, "Island", seat="B", ref="sunrise-first-response-island")
        self.add(game.engine, "Island", seat="B", ref="sunrise-second-response-island")
        self.begin_choice(game, source, boom)
        self.accept(game, "A", self.mode("mode_2", [land.ref]))
        self.cast(game, "B", boom, [land.ref])
        self.assertIsNone(self.resolve(game))
        self.assertEqual("hand", self.current(game, land).zone)
        self.assertFalse(self.green_mana_abilities(game, self.current(game, land)))
        self.replay(game, load=True)

    def test_actual_power_mode_uses_live_power_and_draws_privately(self):
        for threshold_at_choice in (False, True):
            with self.subTest(threshold_at_choice=threshold_at_choice):
                game = self.session(60330045 + threshold_at_choice)
                source = self.add(game.engine, CARD)
                target = self.add(game.engine, "Grizzly Bears")
                if threshold_at_choice:
                    target.counters["+1/+1"] = 1
                    response = self.add(game.engine, "Unsummon", seat="B", zone="hand")
                    responder = "B"
                    self.add(game.engine, "Island", seat="B")
                else:
                    response = self.add(game.engine, "Giant Growth", zone="hand")
                    responder = "A"
                    self.add(game.engine, "Forest")
                amounts = {seat: len(game.state.players[seat].zones["library"]) for seat in "ABCD"}
                next_card = game.state.players["A"].zones["library"][-1]
                self.assertIn("mode_3", self.begin_choice(game, source, response)["legal_modes"])
                self.accept(game, "A", self.mode("mode_3"))
                self.cast(game, responder, response, [target.ref])
                self.assertIsNone(self.resolve(game))
                drew = not threshold_at_choice
                self.assertEqual(amounts["A"] - drew, len(game.state.players["A"].zones["library"]))
                self.assertEqual(drew, next_card in game.state.players["A"].zones["hand"])
                drawn = game.state.cards[next_card]
                for seat in "BCD":
                    self.assertEqual(amounts[seat], len(game.state.players[seat].zones["library"]))
                    self.assertNotIn(drawn.ref, json.dumps(game.packet("pilot:" + seat, full=True)))
                self.replay(game, load=True)


if __name__ == "__main__":
    unittest.main()
