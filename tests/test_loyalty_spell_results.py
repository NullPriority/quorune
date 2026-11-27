"""Native loyalty activation, save and replay witnesses for the original Ral."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.announced_damage import ANNOUNCED_DAMAGE_CONTEXT
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.impulse_access import current_temporary_play_permission
from quorune.next_spell_cost_model import ResolvedNextSpellCostReduction
from quorune.next_spell_costs import matching_next_spell_cost_reductions
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.loyalty_spell_results import CAPABILITY
from scripts.build_test_database import build_fixture_database


NAME = "Ral, Monsoon Mage // Ral, Leyline Prodigy"


class FixedLoyaltySpellResultTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        scratch = Path(cls.temporary.name)
        directory = ROOT / "tests" / "fixtures"
        sources = [
            directory / "cast-optional-coin-transform-cards.json",
            directory / "bound-effect-program-cards.json",
        ]
        additional = {
            "bounded-hand-cast-cards.json": {
                "Wind Drake", "Cyclonic Rift", "Brazen Borrower // Petty Theft", "Plains",
            },
            "next-personal-turn-protection-cards.json": {"Revitalize", "Lightning Bolt", "Isamaru, Hound of Konda"},
            "cast-spell-mana-value-cards.json": {"Blaze"},
            "void-counter-play-cards.json": {"Bone Splinters"},
        }
        for fixture, names in additional.items():
            data = json.loads((directory / fixture).read_bytes())
            data["cards"] = [row for row in data["cards"] if row["name"] in names]
            assert {row["name"] for row in data["cards"]} == names
            ids = {row["oracle_id"] for row in data["cards"]}
            data["rulings"] = [row for row in data.get("rulings", []) if row["oracle_id"] in ids]
            path = scratch / fixture
            path.write_text(json.dumps(data), encoding="utf-8")
            sources.append(path)
        database = scratch / "loyalty.sqlite3"
        build_fixture_database(sources, database)
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("Fixed loyalty", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], [commander])

    def source(self, game, loyalty):
        game.state.config.semantic_policy = "trusted_only"
        source = self.add(game.engine, NAME)
        source.active_face = "Ral, Leyline Prodigy"
        source.counters["loyalty"] = loyalty
        self.assert_closed(game, source)
        return source

    def actions(self, game, seat="A"):
        self.priority(game, seat)
        return game.packet("pilot:" + seat, full=True)["decision"]["ctx"]["legal"]["actions"]

    def reject(self, game, command, seat="A"):
        before = authoritative_state_hash(game.state)
        result = game.act("pilot:" + seat, command)
        self.assertFalse(result.ok, result.summary)
        self.assertEqual(before, authoritative_state_hash(game.state))

    def activate_index(self, game, source, index, **choices):
        from quorune.semantic_runtime.activated_abilities import activated_abilities_from_descriptors

        programs = game.engine.semantics.runtime_handler_programs_for_oracle(source.oracle_id,
            active_zone="battlefield", event="activate")
        abilities = [ability for program in programs for ability in activated_abilities_from_descriptors(program.handlers)]
        ability, = [ability for ability in abilities if ability.loyalty_delta == index]
        prefix = f"activate:{source.ref}:{ability.ability_id}"
        offered, = [row for row in self.actions(game) if row["id"] == prefix]
        result = game.act("pilot:A", {"action_id": offered["id"], "pay": "auto", **choices})
        self.assertTrue(result.ok, result.summary)
        return offered

    def next_turn(self, game, seat):
        start = game.state.turn_sequence
        for _ in range(len(game.state.players) + 1):
            self.until(game, lambda current: (current.state.turn_sequence > start and current.state.active_player == seat)
                       or current.state.pending_decision.kind == "cleanup.discard")
            if game.state.turn_sequence > start and game.state.active_player == seat:
                return
            principal = game.pending_principals()[0]
            context = game.packet(principal, full=True)["decision"]["ctx"]
            cards = [row["id"] for row in context["hand"][:context["count"]]]
            result = game.act(principal, {"action_id": "discard", "cards": cards})
            self.assertTrue(result.ok, result.summary)
        self.fail(f"The next turn for {seat} did not begin")

    def test_positive_cost_persists_after_source_departure_through_an_opponent_turn(self):
        game = self.session(576728011)
        source = self.source(game, 2)
        reply = self.add(game.engine, "Lightning Bolt", seat="B", zone="hand")
        casts = [self.add(game.engine, "Revitalize", zone="hand", ref=f"loyalty-instant-{i}") for i in range(2)]
        creature = self.add(game.engine, "Grizzly Bears", zone="hand")
        for i in range(2):
            self.add(game.engine, "Plains", ref=f"loyalty-plains-{i}")
        self.prepare(game)
        game.state.players["B"].mana_pool["R"] = 1
        self.checkpoint(game)
        self.activate_index(game, source, 1)
        self.cast_for(game, "B", reply, [source.ref])
        self.assertIsNone(self.resolve(game))
        self.assertEqual("graveyard", game.state.cards[source.object_id].zone)
        first, = [value for value in game.state.continuous_effects if isinstance(value, ResolvedNextSpellCostReduction)]
        self.assertEqual((3, "A"), (first.schema_version, first.expires_on_next_turn_of))
        self.assertEqual((), matching_next_spell_cost_reductions(game.engine, "A", creature))
        self.assertEqual((), matching_next_spell_cost_reductions(game.engine, "B", casts[0]))
        self.cast_for(game, "A", casts[0])
        self.assertIsNone(self.resolve(game))
        self.next_turn(game, "B")
        game = self.replay(game, load=True)
        self.assertEqual((first,), matching_next_spell_cost_reductions(game.engine, "A", casts[1]))
        self.cast_for(game, "A", casts[1])
        self.assertIsNone(self.resolve(game))
        self.assertEqual(46, game.state.players["A"].life)
        self.next_turn(game, "A")
        self.assertNotIn(first, game.state.continuous_effects)
        self.replay(game, load=True)

    def test_damage_retains_announced_shares_and_checks_the_other_blue_permanent_afterward(self):
        for i, mode in enumerate(("split", "no targets left", "source only")):
            with self.subTest(mode=mode):
                game = self.session(576728021 + i)
                source = self.source(game, 5)
                self.add(game.engine, "Wind Drake", seat="C", ref="other-player-blue")
                if mode != "source only":
                    self.add(game.engine, "Wind Drake")
                victim = self.add(game.engine, "Grizzly Bears", seat="B")
                bounce = self.add(game.engine, "Unsummon", seat="B", zone="hand")
                self.prepare(game)
                game.state.players["B"].mana_pool["U"] = 1
                self.checkpoint(game)
                before = len(game.state.players["A"].zones["hand"])
                next_draw = game.state.players["A"].zones["library"][-1]
                targets = {"split": [victim.ref, "D"], "no targets left": [victim.ref], "source only": ["D"]}[mode]
                shares = {"split": {victim.ref: 1, "D": 1},
                          "no targets left": {victim.ref: 2}, "source only": {"D": 2}}[mode]
                self.activate_index(game, source, -2, targets=targets, damage_allocation=shares)
                self.assertEqual(list(shares.values()), game.state.stack[-1].context[ANNOUNCED_DAMAGE_CONTEXT]["shares"])
                if mode != "source only":
                    self.cast_for(game, "B", bounce, [victim.ref])
                game = self.replay(game, load=True)
                self.assertIsNone(self.resolve(game))
                self.assertEqual({"split": 39, "no targets left": 40, "source only": 38}[mode], game.state.players["D"].life)
                self.assertEqual(before + (mode == "split"), len(game.state.players["A"].zones["hand"]))
                if mode == "split":
                    self.assertIn(next_draw, game.state.players["A"].zones["hand"])
                    drawn = game.state.cards[next_draw]
                    self.assertNotIn(drawn.ref, json.dumps(game.packet("pilot:C", full=True)))
                self.replay(game, load=True)

    def test_ultimate_uses_exact_top_set_selected_face_normal_timing_and_costs(self):
        game = self.session(576728031)
        source = self.source(game, 9)
        payment = self.add(game.engine, "Grizzly Bears", ref="loyalty-splinters-payment")
        targets = [self.add(game.engine, "Grizzly Bears", seat="B", ref=f"loyalty-target-{i}") for i in range(3)]
        names = ("Plains", "Revitalize", "Wind Drake", "Grizzly Bears", "Bone Splinters",
                 "Cyclonic Rift", "Brazen Borrower // Petty Theft", "Blaze")
        below = self.add(game.engine, "Unsummon", zone="library")
        top = [self.add(game.engine, name, zone="library", ref=f"loyalty-top-{i}") for i, name in enumerate(names)]
        self.prepare(game, mana={"C": 8, "R": 1, "U": 2})
        self.checkpoint(game)
        self.activate_index(game, source, -8)
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("library", game.state.cards[below.object_id].zone)
        current = {name: game.state.cards[card.object_id] for name, card in zip(names, top, strict=True)}
        self.assertTrue(all(card.zone == "exile" and not card.face_down for card in current.values()))
        rows = self.actions(game)
        self.assertFalse(any(row.get("card") in {current[name].ref for name in names[0:1] + names[2:4]}
                             for row in rows))
        adventure, = [row for row in rows if row.get("card") == current[names[6]].ref]
        self.assertEqual("Petty Theft", adventure["face"])
        def spell(name):
            ref = game.state.cards[current[name].object_id].ref
            row, = [entry for entry in self.actions(game) if entry.get("card") == ref]
            self.assertEqual(["without_mana_cost"], [value["id"] for value in row["cost_options"]])
            return {"action_id": row["id"], "cost_option": "without_mana_cost", "pay": "auto"}
        self.reject(game, {"action_id": f"cast:{current[names[6]].ref}", "face": "Brazen Borrower", "pay": "auto"})
        blaze = {**spell("Blaze"), "targets": ["D"]}
        self.reject(game, {**blaze, "x": 1})
        result = game.act("pilot:A", {**blaze, "x": 0})
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(40, game.state.players["D"].life)
        rift = {**spell("Cyclonic Rift"), "targets": [targets[0].ref]}
        self.reject(game, {**rift, "cost_option": "overload", "targets": []})
        result = game.act("pilot:A", rift)
        self.assertTrue(result.ok, result.summary)
        pending = self.actions(game)
        self.assertFalse(any(row.get("card") == current["Bone Splinters"].ref for row in pending))
        self.assertIsNone(self.resolve(game))
        splinters = {**spell("Bone Splinters"), "targets": [targets[1].ref]}
        self.reject(game, splinters)
        result = game.act("pilot:A", {**splinters, "sacrifice_cards": [payment.ref]})
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("graveyard", game.state.cards[payment.object_id].zone)
        self.assertEqual("graveyard", game.state.cards[targets[1].object_id].zone)
        grant_incarnation = current[names[6]].logical_object_id
        result = game.act("pilot:A", {"action_id": adventure["id"], "face": "Petty Theft",
                                      "cost_option": "without_mana_cost", "targets": [targets[2].ref], "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(game))
        card = game.state.cards[current[names[6]].object_id]
        self.assertNotEqual(grant_incarnation, card.logical_object_id)
        self.assertEqual("hand", game.state.cards[targets[2].object_id].zone)
        self.assertEqual("exile", card.zone)
        self.assertIsNone(current_temporary_play_permission(game.state, "A", card))
        borrowed, = [row for row in self.actions(game) if row.get("card") == card.ref]
        self.assertEqual("Brazen Borrower", borrowed["face"])
        cost, = borrowed["cost_options"]
        self.assertEqual((1, 2), (cost["requirements"]["GENERIC"], cost["requirements"]["U"]))
        game = self.replay(game, load=True)
        spell("Revitalize")
        self.next_turn(game, "B")
        expiring = game.state.cards[current["Revitalize"].object_id]
        self.assertIsNone(current_temporary_play_permission(game.state, "A", expiring))
        self.assertFalse(any(row.get("card") == expiring.ref for row in self.actions(game)))
        self.replay(game, load=True)

    def test_loyalty_result_rejects_changed_price_scope_and_an_omitted_compiler_route(self):
        card = self.db.lookup(NAME)
        with patch("quorune.compiler.activation_paid_sacrifice_results.fixed_loyalty_spell_result", return_value=None):
            ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile="commander_review")
            self.assertEqual(3, len(ir.material_residuals))
        source = card.faces[1]
        from quorune.rules.loyalty_spell_results import loyalty_result_cost_is_closed
        from quorune.next_spell_cost_model import next_turn_spell_cost_effect, validate_next_spell_cost_instruction
        with self.assertRaises(ValueError):
            validate_next_spell_cost_instruction({**next_turn_spell_cost_effect(), "amount": 2})
        self.assertFalse(loyalty_result_cost_is_closed({"text": "+2", "loyalty_delta": 1}, 1))
        self.assertIn("until your next turn", source["oracle_text"].casefold())
        self.assertEqual("trusted", next(row["status"] for row in self.registry.capabilities() if row["id"] == CAPABILITY))
