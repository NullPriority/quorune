"""Resolve both original split halves and retain private opponent search authority."""

from dataclasses import replace
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.compiler.targeted_library_exile_templates import targeted_library_exile_spec
from quorune.deck import DeckDefinition, DeckEntry
from quorune.errors import GameRuleError
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash, replay_record
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.targeted_library_exile import (
    TARGET_OPPONENT_EXILE_LIFE_CAPABILITY as CAPABILITY,
    targeted_library_exile_node_capabilities,
)


class TargetLibraryExileLifeTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path = os.environ.get("MTG_CARD_DB", ROOT / "data" / "scryfall-current.sqlite3")
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition(
            "Targeted library exile",
            [DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30)],
            ["Isamaru, Hound of Konda"],
        )

    @classmethod
    def tearDownClass(cls):
        cls.db.close()

    def reject(self, game, principal, command):
        before = authoritative_state_hash(game.state)
        command_count = len(game.commands)
        result = game.act(principal, command)
        self.assertFalse(result.ok, result.summary)
        self.assertEqual(before, authoritative_state_hash(game.state))
        self.assertEqual(command_count, len(game.commands))

    def row_for(self, game, source, face):
        rows = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        choices = [row for row in rows if row.get("card") == source.ref]
        self.assertFalse(any(row.get("cast_method") == "fuse" for row in choices))
        row, = [row for row in choices if row.get("face") == face]
        return row

    def submit(self, game, command):
        result = game.act("pilot:A", command)
        self.assertTrue(result.ok, result.summary)

    def cast_half(self, game, source, face, target):
        row = self.row_for(game, source, face)
        self.submit(game, {"action_id": row["id"], "targets": [target], "pay": "auto"})
        self.assertEqual(face, game.state.stack[-1].label)
        self.assertEqual([target], game.state.stack[-1].targets)
        self.assertFalse(any(game.state.players["A"].mana_pool.values()))

    def export_replay(self, game, name):
        folder = os.environ.get("QUORUNE_HIDE_SEEK_RECORDS")
        if folder:
            path = Path(folder) / name
            game.save(path, include_review=False)
            result = replay_record(path, self.db, verify=True)
            self.assertTrue(result["ok"], result)
            self.assertEqual(authoritative_state_hash(game.state), result["final_state_hash"])

    def seek_scenario(self, seed, result="Hide // Seek"):
        game = self.session(seed)
        source = self.add(game.engine, "Hide // Seek", zone="hand", ref="actual-split-spell")
        selected = self.add(game.engine, result, seat="B", zone="library", ref="seek-selected")
        hidden = self.add(game.engine, "Grizzly Bears", seat="B", zone="library", ref="seek-hidden")
        other = self.add(game.engine, "Island", seat="C", zone="library", ref="other-hidden")
        for card in (selected, hidden, other):
            card.known_to.clear()
            card.revealed_to.clear()
        self.assert_closed(game, source)
        self.prepare(game, mana={"W": 1, "B": 1})
        self.checkpoint(game)
        return game, source, selected, hidden, other

    def test_search_shape_and_authority_fail_closed(self):
        record = self.db.lookup("Hide // Seek", fuzzy=False)
        body = record.faces[1]["oracle_text"]
        spec = targeted_library_exile_spec(body)
        self.assertEqual("opponent", spec.target_schema()["player_relation"])
        for before, after in (
            ("target opponent", "target player"),
            ("a card", "a creature card"),
            ("exile it", "exile it face down"),
            ("mana value", "power"),
            ("that player shuffles", "you shuffle"),
        ):
            with self.subTest(before=before):
                self.assertIsNone(targeted_library_exile_spec(body.replace(before, after)))
        self.assertFalse(targeted_library_exile_node_capabilities(
            effects=(spec.effect(),), target_schema={**spec.target_schema(), "player_relation": "any"},
        ))
        material = body[0].lower() + body[1:]
        for ability in (f"When this creature enters, {material}", f"{{T}}: {material}"):
            with self.subTest(ability=ability):
                nonspell = replace(record, name="Search scope fixture", oracle_id="fixture:search-scope",
                                   layout="normal", faces=(), type_line="Creature — Human", oracle_text=ability)
                ir = compile_oracle_card(nonspell, capability_registry=self.registry,
                                         capability_profile="commander_review")
                self.assertNotEqual("exact", ir.status)
                self.assertTrue(ir.material_residuals)
        game, source, *_ = self.seek_scenario(742190001)
        program, = [program for program in game.engine.semantics.programs_for_oracle(source.oracle_id)
                    if CAPABILITY in program.capability_dependencies]
        deps = tuple(dep for dep in program.capability_dependencies if dep != CAPABILITY)
        changed = replace(program, capability_dependencies=deps, coverage=(),
                          capability_closure=self.registry.closure(deps, profile="commander_review").to_dict())
        self.assertFalse(bind_semantic_program_runtime(
            changed, capability_registry=self.registry, profile="commander_review",
        )["strict"])

    def test_original_seek_privately_exiles_the_selected_split_card_then_gains_its_combined_value_and_shuffles(self):
        game, source, selected, hidden, other = self.seek_scenario(742190002)
        life = {seat: player.life for seat, player in game.state.players.items()}
        row = self.row_for(game, source, "Seek")
        self.assertEqual({"B", "C", "D"}, set(row["target_schema"]["legal_refs"]))
        self.reject(game, "pilot:A", {"action_id": row["id"], "targets": ["A"], "pay": "auto"})
        self.cast_half(game, source, "Seek", "B")
        self.assertEqual("semantic.search", self.resolve(game).kind)
        ctx = game.packet("pilot:A", full=True)["decision"]["ctx"]
        refs = {row["id"] for row in ctx["search_cards"]}
        self.assertTrue({selected.ref, hidden.ref} <= refs)
        self.assertNotIn(other.ref, refs)
        for principal in ("pilot:B", "pilot:C", "pilot:D", "spectator"):
            projected = json.dumps(game.packet(principal, full=True))
            self.assertNotIn(selected.ref, projected)
            self.assertNotIn(hidden.ref, projected)
        choose = {"action_id": "choose", "search_cards": [selected.ref]}
        self.reject(game, "pilot:B", choose)
        self.reject(game, "pilot:A", {**choose, "search_cards": []})
        self.reject(game, "pilot:A", {**choose, "search_cards": [other.ref]})
        self.reject(game, "pilot:A", {"action_id": "choose", "cards": [selected.ref]})
        self.export_replay(game, "seek-pending")
        game = self.replay(game, load=True)
        shuffle = game.engine.shuffle_library

        def check_order(seat, **kwargs):
            self.assertEqual("B", seat)
            self.assertEqual("exile", game.state.cards[selected.object_id].zone)
            self.assertEqual(life["A"] + 4, game.state.players["A"].life)
            return shuffle(seat, **kwargs)

        with patch.object(game.engine, "shuffle_library", side_effect=check_order) as recorded_shuffle:
            self.submit(game, choose)
            self.assertIsNone(self.resolve(game))
            recorded_shuffle.assert_called_once()
        moved = game.state.cards[selected.object_id]
        self.assertEqual(("B", "exile", False), (moved.owner, moved.zone, moved.face_down))
        self.assertNotEqual(selected.logical_object_id, moved.logical_object_id)
        self.assertEqual("library", game.state.cards[hidden.object_id].zone)
        for seat in "BCD":
            self.assertEqual(life[seat], game.state.players[seat].life)
        for principal in ("pilot:C", "pilot:D", "spectator"):
            projected = json.dumps(game.packet(principal, full=True))
            self.assertIn(moved.ref, projected)
            self.assertNotIn(hidden.ref, projected)
        self.export_replay(game, "seek-resolved")
        self.replay(game, load=True)

    def test_original_hide_bottoms_an_artifact_without_search_or_life_gain(self):
        game = self.session(742190003)
        source = self.add(game.engine, "Hide // Seek", zone="hand")
        artifact = self.add(game.engine, "Sol Ring", seat="B")
        self.add(game.engine, "Grizzly Bears", seat="B")
        game.engine.change_control(artifact.object_id, "C", reason="Fixture controller")
        self.prepare(game, mana={"R": 1, "W": 1})
        self.checkpoint(game)
        self.assert_closed(game, source)
        life = game.state.players["A"].life
        self.cast_half(game, source, "Hide", artifact.ref)
        with patch.object(game.engine, "shuffle_library", wraps=game.engine.shuffle_library) as shuffle:
            self.assertIsNone(self.resolve(game))
            shuffle.assert_not_called()
        self.assertEqual(artifact.object_id, game.state.players["B"].zones["library"][0])
        self.assertEqual("library", game.state.cards[artifact.object_id].zone)
        self.assertEqual(life, game.state.players["A"].life)
        self.export_replay(game, "hide-resolved")
        self.replay(game, load=True)

    def test_zero_value_and_prohibited_search_still_shuffle_the_target(self):
        for index, variant in enumerate(("zero", "empty", "prohibited")):
            with self.subTest(variant=variant):
                game, source, selected, *_ = self.seek_scenario(742190005 + index, "Island")
                if variant == "empty":
                    for oid in tuple(game.state.players["B"].zones["library"]):
                        game.engine.move_card(oid, "exile", reason="Empty-library fixture", log=False)
                elif variant == "prohibited":
                    orb = self.add(game.engine, "Mindlock Orb", seat="C")
                    self.assert_closed(game, orb)
                self.checkpoint(game)
                life = game.state.players["A"].life
                self.cast_half(game, source, "Seek", "B")
                with patch.object(game.engine, "shuffle_library", wraps=game.engine.shuffle_library) as shuffle:
                    decision = self.resolve(game)
                    if variant == "zero":
                        self.assertEqual("semantic.search", decision.kind)
                        self.submit(game, {"action_id": "choose", "search_cards": [selected.ref]})
                    elif variant == "empty" and decision is not None:
                        self.assertEqual("semantic.search", decision.kind)
                        self.submit(game, {"action_id": "choose", "search_cards": []})
                    elif variant == "prohibited":
                        self.assertIsNone(decision)
                    self.assertIsNone(self.resolve(game))
                    self.assertEqual(["B"], [call.args[0] for call in shuffle.call_args_list])
                self.assertEqual(life, game.state.players["A"].life)
                self.replay(game, load=True)

    def test_shuffle_fault_rolls_back_private_search_life_and_exile(self):
        game, source, selected, *_ = self.seek_scenario(742190009)
        self.cast_half(game, source, "Seek", "B")
        self.assertEqual("semantic.search", self.resolve(game).kind)
        choose = {"action_id": "choose", "search_cards": [selected.ref]}
        life = game.state.players["A"].life
        shuffle = game.engine.shuffle_library

        def reject_after_commit(seat, **kwargs):
            shuffle(seat, **kwargs)
            self.assertEqual(life + 4, game.state.players["A"].life)
            raise GameRuleError("Injected late shuffle failure")

        with patch.object(game.engine, "shuffle_library", side_effect=reject_after_commit):
            self.reject(game, "pilot:A", choose)
        self.assertEqual("library", game.state.cards[selected.object_id].zone)
        self.assertEqual(life, game.state.players["A"].life)
        game = self.replay(game, load=True)
        self.submit(game, choose)
        self.assertEqual(life + 4, game.state.players["A"].life)
        self.replay(game, load=True)

    def pending_life_replacement(self, seed):
        game, source, selected, *_ = self.seek_scenario(seed)
        doubling = self.add(game.engine, "Boon Reflection")
        conversion = self.add(game.engine, "Tainted Remedy", seat="C")
        self.assert_closed(game, doubling)
        self.assert_closed(game, conversion)
        self.checkpoint(game)
        life = game.state.players["A"].life
        self.cast_half(game, source, "Seek", "B")
        self.assertEqual("semantic.search", self.resolve(game).kind)
        choose = {"action_id": "choose", "search_cards": [selected.ref]}
        with patch.object(game.engine, "shuffle_library", wraps=game.engine.shuffle_library) as shuffle:
            self.submit(game, choose)
            pending = game.state.pending_decision
            self.assertEqual(("replacement.order", ["A"]), (pending.kind, pending.actors))
            shuffle.assert_not_called()
        self.assertEqual("exile", game.state.cards[selected.object_id].zone)
        self.assertEqual(life, game.state.players["A"].life)
        options = game.packet("pilot:A", full=True)["decision"]["ctx"]["options"]
        first, = [row for row in options if "multiply life gained" in row["label"]]
        replace_life = {"action_id": "choose", "replacement": first["id"]}
        return game, life, replace_life

    def test_life_replacement_resumes_once_before_the_target_shuffles(self):
        game, life, replace_life = self.pending_life_replacement(742190012)
        self.reject(game, "pilot:C", replace_life)
        game = self.replay(game, load=True)
        shuffle = game.engine.shuffle_library

        def check_order(seat, **kwargs):
            self.assertEqual("B", seat)
            self.assertEqual(life - 8, game.state.players["A"].life)
            return shuffle(seat, **kwargs)

        with patch.object(game.engine, "shuffle_library", side_effect=check_order) as observed:
            self.submit(game, replace_life)
            self.assertIsNone(self.resolve(game))
            observed.assert_called_once()
        self.export_replay(game, "seek-life-replacement")
        self.replay(game, load=True)

    def test_target_departure_after_the_search_preserves_the_pending_life_event(self):
        game, life, replace_life = self.pending_life_replacement(742190018)
        game.engine._eliminate_players(["B"], reason="conceded")
        self.checkpoint(game)
        self.assertNotIn("B", game.state.active_seats())
        game = self.replay(game, load=True)
        with patch.object(game.engine, "shuffle_library", wraps=game.engine.shuffle_library) as shuffle:
            self.submit(game, replace_life)
            self.assertIsNone(self.resolve(game))
            shuffle.assert_not_called()
        self.assertEqual(life - 8, game.state.players["A"].life)
        self.replay(game, load=True)

    def test_omitted_life_followup_mutant_is_killed(self):
        from quorune.selection import searching

        original = searching.targeted_library_exile_life_followups

        def mutant(*args):
            return [effect for effect in original(*args) if effect["op"] != "life"]

        def witness():
            game, source, selected, *_ = self.seek_scenario(742190010)
            self.cast_half(game, source, "Seek", "B")
            self.assertEqual("semantic.search", self.resolve(game).kind)
            life = game.state.players["A"].life
            self.submit(game, {"action_id": "choose", "search_cards": [selected.ref]})
            self.assertEqual(life + 4, game.state.players["A"].life)

        witness()
        with patch.object(searching, "targeted_library_exile_life_followups", side_effect=mutant):
            with self.assertRaises(AssertionError):
                witness()
