"""Exercise the original Pawprint spell through authenticated cast and replay."""

from copy import deepcopy
from dataclasses import replace
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
from quorune.pawprint_modes import PAWPRINT_CAPABILITY, PAWPRINT_FIELD, PawprintModeBudget
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


BURROW = "Season of the Burrow"
PROFILE = "commander_review"


class PawprintModeTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        database = Path(cls.temporary.name) / "pawprint-mode.sqlite3"
        fixtures = ROOT / "tests/fixtures"
        build_fixture_database([
            fixtures / "nonartifact-nonblack-mass-destruction-cards.json",
            fixtures / "grouped-choose-one-cards.json",
            fixtures / "pawprint-mode-cards.json",
        ], database)
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("Pawprint native proof", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Plains", 30),
        ], [commander])

    def test_complete_original_uses_three_closed_bodies_and_the_repeat_budget(self):
        card = self.db.lookup(BURROW, fuzzy=False)
        args = dict(capability_registry=self.registry, capability_profile=PROFILE)
        ir = compile_oracle_card(card, **args)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        ready, semantics = register_card(self.db, self.registry, BURROW)
        self.assertTrue(ready["strict_capability_ready"], ready["blockers"])
        program, = semantics.programs_for_oracle(card.oracle_id)
        self.assertIn(PAWPRINT_CAPABILITY, program.capability_dependencies)
        model = PawprintModeBudget.from_dict(program.cost_schema[PAWPRINT_FIELD])
        model.validate_schema(program.target_schema)
        self.assertEqual(16, len(model.available_choices()))
        other = deepcopy(program.cost_schema)
        other[PAWPRINT_FIELD]["mode_ids"].reverse()
        deps = tuple(dep for dep in program.capability_dependencies if dep != PAWPRINT_CAPABILITY)
        for forged in (
            replace(program, cost_schema=other),
            replace(program, capability_dependencies=deps,
                    capability_closure=self.registry.closure(deps, profile=PROFILE).to_dict()),
        ):
            self.assertFalse(bind_semantic_program_runtime(
                forged, capability_registry=self.registry, profile=PROFILE,
            )["strict"])
        for before, after in (("up to five", "up to six"), ("{P}{P}{P}", "{P}{P}"),
                              ("Create a 1/1 white Rabbit creature token.", "Rewrite history.")):
            with self.subTest(after=after):
                changed = replace(card, oracle_text=card.oracle_text.replace(before, after))
                self.assertNotEqual("exact", compile_oracle_card(changed, **args).status)

    def state_strings(self, value):
        if isinstance(value, dict):
            return {part for child in value.values() for part in self.state_strings(child)}
        if isinstance(value, list):
            return {part for child in value for part in self.state_strings(child)}
        return {value} if isinstance(value, str) else set()

    def accept(self, game, seat, command):
        result = game.act("pilot:" + seat, command)
        self.assertTrue(result.ok, result.summary)

    def reject(self, game, seat, command):
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:" + seat, command).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))

    def scene(self, seed):
        game = self.session(seed)
        source = self.add(game.engine, BURROW, zone="hand")
        left = self.add(game.engine, "Grizzly Bears", seat="B", ref="pawprint-target-b")
        right = self.add(game.engine, "Ornithopter", seat="C", ref="pawprint-target-c")
        land = self.add(game.engine, "Plains", seat="B", ref="pawprint-nontarget-land")
        grave = self.add(game.engine, "Ornithopter", zone="graveyard", ref="pawprint-own-grave")
        other = self.add(game.engine, "Ornithopter", seat="B", zone="graveyard", ref="pawprint-foreign-grave")
        self.assert_closed(game, source)
        self.prepare(game, mana={"C": 3, "W": 2})
        self.checkpoint(game)
        return game, source, left, right, land, grave, other

    def option(self, offered, *modes):
        suffix = "+pawprint:" + (",".join(modes) or "empty")
        row, = [row for row in offered["cost_options"] if row["id"] == "normal" + suffix]
        return row

    def prove_repeat_targets(self):
        game, source, left, right, land, _, _ = self.scene(75180011)
        offered = self.action(game, "A", source)
        modes = ["mode_2", "mode_2_repeat_2"]
        price = self.option(offered, *modes)
        self.assertIn("Mode 2 ×2", price["label"])
        groups = [group for mode in modes
                  for group in price["target_schema"]["mode_schemas"][mode]["groups"]]
        self.assertEqual(2, len(groups))
        self.assertEqual(2, len({group["id"] for group in groups}))
        for group in groups:
            self.assertIn(left.ref, group["legal_refs"])
            self.assertIn(right.ref, group["legal_refs"])
            self.assertNotIn(land.ref, group["legal_refs"])
        submissions = [{"group": group["id"], "ref": card.ref}
                       for group, card in zip(groups, (left, right), strict=True)]
        command = {"action_id": offered["id"], "cost_option": price["id"], "pay": "auto",
                   "modes": ["mode_2", "mode_2"], "targets": submissions}
        self.reject(game, "D", command)
        for invalid in (
            {"modes": ["mode_1", "mode_2", "mode_3"]},
            {"modes": ["mode_3", "mode_3"]},
            {"modes": ["mode_2", "mode_2_repeat_3"]},
            {"targets": submissions[:1]},
            {"targets": [submissions[0], {**submissions[1], "ref": land.ref}]},
            {"targets": [submissions[0], {**submissions[1], "group": groups[0]["id"]}]},
            {"cost_option": self.option(offered, "mode_2")["id"]},
        ):
            self.reject(game, "A", {**command, **invalid})
        self.accept(game, "A", command)
        self.assertEqual(modes, game.state.stack[-1].modes)
        self.assertEqual([left.ref, right.ref], game.state.stack[-1].targets)
        self.assertEqual(0, sum(game.state.players["A"].mana_pool.values()))
        top = {seat: set(game.state.players[seat].zones["hand"]) for seat in "ABCD"}
        game = self.replay(game, load=True)
        self.resolve_top(game)
        self.assertEqual("exile", game.state.cards[left.object_id].zone)
        self.assertEqual("exile", game.state.cards[right.object_id].zone)
        self.assertEqual("battlefield", game.state.cards[land.object_id].zone)
        for seat in "ABCD":
            added = set(game.state.players[seat].zones["hand"]) - top[seat]
            self.assertEqual(int(seat in "BC"), len(added))
            if added:
                object_id, = added
                ref = game.state.cards[object_id].ref
                owner = game.packet("pilot:" + seat, full=True)["state"]
                self.assertIn(ref, self.state_strings(owner))
                for other in set("ABCD") - {seat}:
                    same = game.packet("pilot:" + other, full=True)["state"]
                    self.assertNotIn(ref, self.state_strings(same))
        self.replay(game, load=True)

    def test_repeated_targeted_modes_keep_two_independent_groups_and_private_draws(self):
        self.prove_repeat_targets()

    def test_two_and_three_pawprints_use_printed_order_and_keep_the_own_graveyard(self):
        game, source, left, _, _, grave, other = self.scene(75180012)
        offered = self.action(game, "A", source)
        price = self.option(offered, "mode_2", "mode_3")
        command = {"action_id": offered["id"], "cost_option": price["id"], "pay": "auto",
                   "modes": ["mode_3", "mode_2"], "targets": [left.ref, grave.ref]}
        self.reject(game, "A", {**command, "targets": [left.ref, other.ref]})
        self.accept(game, "A", command)
        self.assertEqual(["mode_2", "mode_3"], game.state.stack[-1].modes)
        self.resolve_top(game)
        self.assertEqual("exile", game.state.cards[left.object_id].zone)
        updated = game.state.cards[grave.object_id]
        self.assertEqual("battlefield", updated.zone)
        self.assertEqual({"indestructible": 1}, updated.counters)
        self.assertEqual("graveyard", game.state.cards[other.object_id].zone)
        self.replay(game, load=True)

    def test_zero_or_five_single_pawprint_modes_preserve_the_ordinary_cast_price(self):
        for count in (0, 5):
            with self.subTest(count=count):
                game, source, *_ = self.scene(75180020 + count)
                offered = self.action(game, "A", source)
                selected = ["mode_1" if i == 1 else f"mode_1_repeat_{i}" for i in range(1, count + 1)]
                price = self.option(offered, *selected)
                command = {"action_id": offered["id"], "cost_option": price["id"], "pay": "auto",
                           "modes": ["mode_1"] * count}
                self.reject(game, "A", {**command, "modes": ["mode_1"] * 6})
                self.accept(game, "A", command)
                self.assertEqual(selected, game.state.stack[-1].modes)
                self.assertEqual(0, sum(game.state.players["A"].mana_pool.values()))
                game = self.replay(game, load=True)
                self.resolve_top(game)
                rabbits = [card for card in game.state.cards.values()
                           if card.is_token and card.zone == "battlefield" and card.controller == "A"]
                self.assertEqual(count, len(rabbits))
                for card in rabbits:
                    self.assertEqual("Rabbit Token", card.printed_name)
                    data = game.engine._effective_card_data(card)
                    self.assertEqual(("1", "1", ["W"]), (data["power"], data["toughness"], data["colors"]))
                    self.assertIn("Creature", data["type_line"])
                self.replay(game, load=True)

    def test_repeated_same_target_uses_last_known_controller_after_one_move(self):
        game, source, target, *_ = self.scene(75180032)
        offered = self.action(game, "A", source)
        price = self.option(offered, "mode_2", "mode_2_repeat_2")
        count = len(game.state.players["B"].zones["hand"])
        incarnation = target.zone_change_counter
        self.accept(game, "A", {"action_id": offered["id"], "cost_option": price["id"], "pay": "auto",
                               "modes": ["mode_2", "mode_2_repeat_2"], "targets": [target.ref, target.ref]})
        game = self.replay(game, load=True)
        self.resolve_top(game)
        target_after = game.state.cards[target.object_id]
        self.assertEqual(("exile", incarnation + 1), (target_after.zone, target_after.zone_change_counter))
        self.assertEqual(count + 2, len(game.state.players["B"].zones["hand"]))
        self.replay(game, load=True)

    def test_stale_targets_stop_their_occurrence_and_all_stale_stop_the_spell(self):
        for two in (False, True):
            with self.subTest(two=two):
                game, source, left, right, *_ = self.scene(75180040 + two)
                bounce = self.add(game.engine, "Unsummon", seat="B", zone="hand")
                game.state.players["B"].mana_pool["U"] = 1
                self.assert_closed(game, bounce)
                self.checkpoint(game)
                offered = self.action(game, "A", source)
                modes = ["mode_1", "mode_2", "mode_2_repeat_2"] if two else ["mode_1", "mode_2"]
                targets = [left.ref, right.ref] if two else [left.ref]
                price = self.option(offered, *modes)
                self.accept(game, "A", {"action_id": offered["id"], "cost_option": price["id"], "pay": "auto",
                                       "modes": modes, "targets": targets})
                self.cast_for(game, "B", bounce, [left.ref])
                self.resolve_top(game)
                self.assertEqual("hand", game.state.cards[left.object_id].zone)
                counts = {seat: len(game.state.players[seat].zones["hand"]) for seat in "BC"}
                game = self.replay(game, load=True)
                self.resolve_top(game)
                self.assertEqual("exile" if two else "battlefield", game.state.cards[right.object_id].zone)
                self.assertEqual(counts["B"], len(game.state.players["B"].zones["hand"]))
                self.assertEqual(counts["C"] + int(two), len(game.state.players["C"].zones["hand"]))
                self.assertEqual(int(two), sum(c.is_token and c.controller == "A" and c.zone == "battlefield"
                                               for c in game.state.cards.values()))
                self.replay(game, load=True)

    def test_copy_preserves_repeat_instances_and_can_change_each_target(self):
        game, source, left, right, *_ = self.scene(75180053)
        copier = self.add(game.engine, "Reverberate", seat="B", zone="hand")
        new_left = self.add(game.engine, "Grizzly Bears", seat="D", ref="pawprint-copy-target-d")
        new_right = self.add(game.engine, "Ornithopter", ref="pawprint-copy-target-a")
        self.assert_closed(game, copier)
        game.state.players["B"].mana_pool["R"] = 2
        self.prepare(game, mana={"C": 3, "W": 2})
        self.checkpoint(game)
        offered = self.action(game, "A", source)
        modes = ["mode_2", "mode_2_repeat_2"]
        price = self.option(offered, *modes)
        self.accept(game, "A", {"action_id": offered["id"], "cost_option": price["id"], "pay": "auto",
                               "modes": modes, "targets": [left.ref, right.ref]})
        original = game.state.stack[-1]
        self.cast_for(game, "B", copier, [original.ref])
        pending = self.resolve(game)
        self.assertEqual("semantic.choice", pending.kind)
        game = self.replay(game, load=True)
        groups = [group for mode in modes
                  for group in price["target_schema"]["mode_schemas"][mode]["groups"]]
        targets = [{"group": group["id"], "ref": ref} for group, ref in
                   zip(groups, (new_left.ref, new_right.ref), strict=True)]
        self.reject(game, "C", {"action_id": "choose", "targets": targets})
        self.accept(game, "B", {"action_id": "choose", "targets": targets})
        clone = game.state.stack[-1]
        self.assertEqual(("spell_copy", "B", modes, [new_left.ref, new_right.ref]),
                         (clone.kind, clone.controller, clone.modes, clone.targets))
        self.assertEqual([left.ref, right.ref],
                         next(i for i in game.state.stack if i.ref == original.ref).targets)
        self.resolve_top(game)
        self.resolve_top(game)
        self.assertTrue(all(game.state.cards[card.object_id].zone == "exile"
                            for card in (left, right, new_left, new_right)))
        self.replay(game, load=True)

    def test_coalescing_repeat_occurrences_breaks_the_real_quote(self):
        original = PawprintModeBudget.selection

        def coalesced(budget, submitted):
            return original(budget, tuple(dict.fromkeys(submitted)))

        with patch.object(PawprintModeBudget, "selection", coalesced):
            with self.assertRaises((AssertionError, ValueError)):
                self.prove_repeat_targets()
