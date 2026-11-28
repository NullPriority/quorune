"""Actual targeted private searching, APNAP commits and Game Record v3."""

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.opponent_library_exile import current_opponent_library_exile_play_permission
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.targeted_search_round import CAPABILITY, target_pair_search_schema
from scripts.build_test_database import build_fixture_database


SOURCE = "Scheming Symmetry"


class TargetPairPrivateLibrarySearchTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        folder = Path(cls.temporary.name)
        fixtures = ROOT / "tests/fixtures"
        base = json.loads((fixtures / "bargain-cards.json").read_bytes())
        wanted = {"Opposition Agent", "Isamaru, Hound of Konda", "Plains", "Grizzly Bears"}
        base["cards"] = [card for card in base["cards"] if card["name"] in wanted]
        assert {card["name"] for card in base["cards"]} == wanted
        oracle_ids = {card["oracle_id"] for card in base["cards"]}
        base["rulings"] = [row for row in base.get("rulings", []) if row["oracle_id"] in oracle_ids]
        support = folder / "target-pair-search-support.json"
        support.write_text(json.dumps(base), encoding="utf-8")
        rules = json.loads((fixtures / "library-search-rules-cards.json").read_bytes())
        rules["cards"] = [card for card in rules["cards"] if card["name"] in {"Mindlock Orb", "Aven Mindcensor"}]
        rule_ids = {card["oracle_id"] for card in rules["cards"]}
        rules["rulings"] = [row for row in rules.get("rulings", []) if row["oracle_id"] in rule_ids]
        effects = folder / "target-pair-search-restrictions.json"
        effects.write_text(json.dumps(rules), encoding="utf-8")
        database = folder / "target-pair-search.sqlite3"
        build_fixture_database([support, effects, fixtures / "target-pair-private-search-cards.json"], database)
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Private library search targets", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def scene(self, seed, *, markers="ABCD"):
        game = self.session(seed)
        source = self.add(game.engine, SOURCE, zone="hand")
        found = {
            seat: self.add(game.engine, "Grizzly Bears", zone="library", seat=seat, ref=f"private-find-{seat}")
            for seat in markers
        }
        game.state.config.semantic_policy = "trusted_only"
        self.assert_closed(game, source)
        return game, source, found

    def prepare_cast(self, game):
        self.prepare(game, mana={"B": 1})
        self.checkpoint(game)

    def snapshot(self, game):
        return {
            seat: (tuple(game.state.players[seat].zones["library"]), game.state.players[seat].stats.get("shuffle_count", 0))
            for seat in "ABCD"
        }

    def looks(self, game, *, actor, principal=None):
        self.assertEqual(("semantic.search", [actor]), (game.state.pending_decision.kind, game.state.pending_decision.actors))
        shown = game.packet("pilot:" + (principal or actor), full=True)["decision"]
        self.assertEqual(actor, shown["actor"])
        return shown["ctx"]["legal_actions"][0]["choice_schema"]["legal_refs"]

    def chooses(self, game, seat, refs):
        result = game.act("pilot:" + seat, {"action_id": "choose", "search_cards": list(refs)})
        self.assertTrue(result.ok, result.summary)

    def rejects(self, game, seat, command):
        before = authoritative_state_hash(game.state), len(game.commands)
        self.assertFalse(game.act("pilot:" + seat, command).ok)
        self.assertEqual(before, (authoritative_state_hash(game.state), len(game.commands)))

    def check_top(self, game, found):
        for seat, original in found.items():
            current = game.state.cards[original.object_id]
            self.assertEqual((original.ref, original.logical_object_id, "library", [seat], []),
                             (current.ref, current.logical_object_id, current.zone, current.known_to, current.revealed_to))
            self.assertEqual(original.object_id, game.state.players[seat].zones["library"][-1])
            for viewer in set("ABCD") - {seat}:
                self.assertNotIn(current.ref, json.dumps(game.packet(f"pilot:{viewer}", full=True)))

    def test_exact_two_player_target_private_search_shape_and_required_owner(self):
        card = self.db.lookup(SOURCE, fuzzy=False)
        options = {"capability_registry": self.registry, "capability_profile": "commander_review"}
        ir = compile_oracle_card(card, **options)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        node, = ir.faces[0].nodes
        self.assertEqual(card.oracle_text, card.oracle_text[node.span.start:node.span.end])
        self.assertEqual(target_pair_search_schema(), node.target_schema)
        self.assertIn(CAPABILITY, node.capability_dependencies)
        for old, new in (("two target players", "two target opponents"), ("for a card", "for a land card"),
                         ("then shuffles", "then reveals that card and shuffles")):
            with self.subTest(new=new):
                text = card.oracle_text.replace(old, new)
                self.assertNotEqual("exact", compile_oracle_card(replace(card, oracle_text=text), **options).status)
        game, _, _ = self.scene(70123013)
        program, = game.engine.semantics.programs_for_oracle(card.oracle_id)
        deps = tuple(dep for dep in program.capability_dependencies if dep != CAPABILITY)
        changed = replace(program, capability_dependencies=deps,
                          capability_closure=self.registry.closure(deps, profile="commander_review").to_dict())
        report = bind_semantic_program_runtime(changed, capability_registry=self.registry, profile="commander_review")
        self.assertIn("capability:undeclared_runtime_dependency:" + CAPABILITY, report["blockers"])

    def test_actual_two_target_round_commits_only_after_apnap_private_choices_and_replays(self):
        game, source, found = self.scene(70123102)
        known = found["A"]
        known.known_to = ["A", "B", "C", "D"]
        known.revealed_to = ["A", "B", "C", "D"]
        self.prepare_cast(game)
        action = self.action(game, "A", source)
        group, = action["target_schema"]["groups"]
        self.assertEqual(set("ABCD"), set(group["legal_refs"]))
        for target in (["A"], ["A", "A"], ["A", "B", "C"]):
            self.rejects(game, "A", {"action_id": action["id"], "targets": target, "pay": "auto"})
        self.cast_for(game, "A", source, ["C", "A"])
        old = self.snapshot(game)
        self.resolve(game)
        self.assertIn(found["A"].ref, self.looks(game, actor="A"))
        cmd = {"action_id": "choose", "search_cards": [found["A"].ref]}
        self.rejects(game, "D", cmd)
        for refs in ([], [found["B"].ref], [found["A"].ref, found["A"].ref]):
            self.rejects(game, "A", {"action_id": "choose", "search_cards": refs})
        game = self.replay(game, load=True)
        self.chooses(game, "A", [found["A"].ref])
        self.assertEqual(old, self.snapshot(game))
        self.assertIn(found["C"].ref, self.looks(game, actor="C"))
        game = self.replay(game, load=True)
        self.chooses(game, "C", [found["C"].ref])
        self.check_top(game, {seat: found[seat] for seat in "AC"})
        new = self.snapshot(game)
        self.assertEqual([1, 0, 1, 0], [new[seat][1] - old[seat][1] for seat in "ABCD"])
        self.assertEqual([old[seat] for seat in "BD"], [new[seat] for seat in "BD"])
        self.assertEqual("graveyard", game.state.cards[source.object_id].zone)
        game = self.replay(game, load=True)
        self.check_top(game, {seat: found[seat] for seat in "AC"})

    def test_two_other_targets_keep_the_source_controller_out_of_both_private_looks(self):
        game, source, found = self.scene(70123104)
        self.prepare_cast(game)
        self.cast_for(game, "A", source, ["D", "B"])
        self.resolve(game)
        for seat in "BD":
            own = found[seat].ref
            other = found["D" if seat == "B" else "B"].ref
            refs = self.looks(game, actor=seat)
            self.assertIn(own, refs)
            self.assertNotIn(other, refs)
            for observer in set("ABCD") - {seat}:
                self.assertNotIn(own, json.dumps(game.packet("pilot:" + observer, full=True)))
            self.chooses(game, seat, [own])
        self.check_top(game, {seat: found[seat] for seat in "BD"})
        self.replay(game, load=True)

    def test_conceded_targets_never_search_or_shuffle_while_a_surviving_target_does(self):
        for departures in ("C", "BC"):
            with self.subTest(departures=departures):
                game, source, found = self.scene(60823001)
                self.prepare_cast(game)
                self.cast_for(game, "A", source, ["C", "B"])
                for seat in departures:
                    self.priority(game, seat)
                    quit_result = game.act("pilot:" + seat, {"action_id": "concede", "confirm_concede": True})
                    self.assertTrue(quit_result.ok, quit_result.summary)
                    self.assertFalse(game.state.players[seat].in_game)
                old = self.snapshot(game)
                game = self.replay(game, load=True)
                pending = self.resolve(game)
                if "B" not in departures:
                    self.assertIsNotNone(pending)
                    self.assertIn(found["B"].ref, self.looks(game, actor="B"))
                    self.chooses(game, "B", [found["B"].ref])
                    self.check_top(game, {"B": found["B"]})
                else:
                    self.assertIsNone(pending)
                self.assertEqual("graveyard", game.state.cards[source.object_id].zone)
                self.assertEqual([old[seat] for seat in departures], [self.snapshot(game)[seat] for seat in departures])
                self.replay(game, load=True)

    def test_prohibition_and_empty_library_still_allow_only_the_printed_shuffles(self):
        for prohibited in (False, True):
            with self.subTest(prohibited=prohibited):
                game, source, found = self.scene(70124010, markers="A")
                if prohibited:
                    orb = self.add(game.engine, "Mindlock Orb", seat="D")
                    self.assert_closed(game, orb)
                else:
                    for oid in tuple(game.state.players["B"].zones["library"]):
                        game.engine.move_card(oid, "graveyard", log=False)
                self.prepare_cast(game)
                self.cast_for(game, "A", source, ["B", "A"])
                old = self.snapshot(game)
                pending = self.resolve(game)
                if not prohibited:
                    self.assertIsNotNone(pending)
                    self.chooses(game, "A", [found["A"].ref])
                    self.assertEqual([], self.looks(game, actor="B"))
                    self.chooses(game, "B", [])
                    self.check_top(game, {"A": found["A"]})
                else:
                    self.assertIsNone(pending)
                new = self.snapshot(game)
                self.assertEqual([1, 1, 0, 0], [new[seat][1] - old[seat][1] for seat in "ABCD"])
                self.replay(game, load=True)

    def test_competing_opposition_agents_defer_the_actual_exiles_until_all_search_choices(self):
        game, source, found = self.scene(70123110)
        earlier = self.add(game.engine, "Opposition Agent", seat="B", ref="agent-older")
        later = self.add(game.engine, "Opposition Agent", seat="D", ref="agent-latest")
        self.assert_closed(game, earlier)
        self.assert_closed(game, later)
        self.prepare_cast(game)
        self.cast_for(game, "A", source, ["C", "A"])
        self.resolve(game)
        old = self.snapshot(game)
        controller, = game.pending_principals()
        controller = controller.removeprefix("pilot:")
        self.assertIn(controller, "BD")
        for actor in "AC":
            chosen = found[actor]
            self.assertIn(chosen.ref, self.looks(game, actor=actor, principal=controller))
            self.rejects(game, actor, {"action_id": "choose", "search_cards": [chosen.ref]})
            self.chooses(game, controller, [chosen.ref])
            self.assertEqual("search.exile_replacement", game.state.pending_decision.kind)
            self.assertEqual(old, self.snapshot(game))
            game = self.replay(game, load=True)
            picked = game.act(f"pilot:{controller}", {"action_id": "choose", "choice": earlier.ref})
            self.assertTrue(picked.ok, picked.summary)
            if actor == "A":
                self.assertEqual(old, self.snapshot(game))
        for actor in "AC":
            result = game.state.cards[found[actor].object_id]
            self.assertEqual(("exile", False), (result.zone, result.face_down))
            self.assertIsNotNone(current_opponent_library_exile_play_permission(game.state, "B", result))
        self.replay(game, load=True)
