"""Real upkeep, live per-opponent thresholds and simultaneous battlefield search."""

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.deck import DeckDefinition, DeckEntry
from quorune.library_search_model import FIXED_CREATURE_GROUP_BATTLEFIELD_SEARCH_CAPABILITY, validate_fixed_creature_group_search
from quorune.oracle_ir import compile_oracle_card
from quorune.player_maximum_model import OPPONENT_CREATURE_THRESHOLD_CAPABILITY
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.source_sacrifice_shapes import fixed_source_sacrifice_runtime_dependencies
from quorune.source_sacrifice_model import fixed_source_sacrifice_effect
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


DEFENSE = "Defense of the Heart"
PROFILE = "commander_review"


class OpponentCreatureThresholdUpkeepSearchTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "opponent-creature-threshold-search.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/opponent-creature-threshold-upkeep-search-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("Opponent threshold", [DeckEntry(commander, 1, "commander"), DeckEntry("Plains", 30)],
                                  [commander])

    def world(self, seed, counts):
        game = self.session(seed)
        source = self.add(game.engine, DEFENSE)
        field = {seat: [self.add(game.engine, "Grizzly Bears", seat=seat, ref=f"threshold-{seat}-{i}")
                        for i in range(amount)] for seat, amount in counts.items()}
        bear = self.add(game.engine, "Grizzly Bears", zone="library", ref="threshold-secret-bear")
        warden = self.add(game.engine, "Soul Warden", zone="library", ref="threshold-secret-warden")
        noncreature = self.add(game.engine, "Forest", zone="library", ref="threshold-secret-forest")
        self.assert_closed(game, source)
        return game, source, field, (bear, warden, noncreature)

    def own_upkeep(self, game):
        order = game.state.turn_order
        previous = order[order.index("A") - 1]
        for object_id in tuple(game.state.players[previous].zones["hand"][7:]):
            game.engine.move_card(object_id, "library", reason="Prepare the prior player's starting hand")
        game.state.config.semantic_policy = "trusted_only"
        self.prepare(game, actor=previous, active=previous)
        game.state.last_normal_turn_player = previous
        self.checkpoint(game)
        self.until(game, lambda session: session.state.active_player == "A" and session.state.step == "upkeep")

    def act_ok(self, game, seat, command):
        result = game.act("pilot:" + seat, command)
        self.assertTrue(result.ok, result.summary)

    def expect_private_search(self, game, candidates):
        pending = self.resolve(game)
        self.assertIsNotNone(pending)
        self.assertEqual(("semantic.search", ["A"]), (pending.kind, pending.actors))
        decision = game.packet("pilot:A", full=True)["decision"]
        schema = decision["ctx"]["legal_actions"][0]["choice_schema"]
        self.assertEqual(2, schema["maximum"])
        self.assertEqual({card.ref for card in candidates}, set(schema["legal_refs"]))
        for seat in "BCD":
            packet = game.packet("pilot:" + seat, full=True)
            self.assertIsNone(packet["decision"])
            for card in candidates:
                self.assertNotIn(card.ref, json.dumps(packet))

    def test_actual_different_opponent_can_qualify_after_source_leaves_and_entries_are_simultaneous(self):
        game, source, field, (bear, warden, noncreature) = self.world(61260701, {"B": 3, "C": 1})
        bounce = self.add(game.engine, "Unsummon", zone="hand")
        alarm = self.add(game.engine, "Raise the Alarm", seat="C", zone="hand")
        destroy = self.add(game.engine, "Disenchant", seat="B", zone="hand")
        self.add(game.engine, "Island")
        for seat in "BC":
            for i in range(2):
                self.add(game.engine, "Plains", seat=seat, ref=f"threshold-mana-{seat}-{i}")
        for card in (bounce, alarm, destroy, warden):
            self.assert_closed(game, card)
        self.own_upkeep(game)
        self.assertEqual(1, self.trigger_count(game, source))
        self.cast(game, "A", bounce, [field["B"][0].ref])
        self.resolve_top(game)
        self.assertEqual("hand", game.state.cards[field["B"][0].object_id].zone)
        self.cast(game, "C", alarm)
        self.resolve_top(game)
        self.cast(game, "B", destroy, [source.ref])
        self.resolve_top(game)
        self.assertEqual("graveyard", game.state.cards[source.object_id].zone)
        shuffles = game.state.players["A"].stats.get("shuffle_count", 0)
        life = game.state.players["A"].life
        self.expect_private_search(game, (bear, warden))
        game = self.replay(game, load=True)
        before = authoritative_state_hash(game.state)
        for seat, refs in (("B", [bear.ref]), ("A", [bear.ref, bear.ref]), ("A", [bear.ref, noncreature.ref])):
            rejected = game.act("pilot:" + seat, {"action_id": "choose", "search_cards": refs})
            self.assertFalse(rejected.ok, rejected.summary)
            self.assertEqual(before, authoritative_state_hash(game.state))
        self.act_ok(game, "A", {"action_id": "choose", "search_cards": [bear.ref, warden.ref]})
        self.assertEqual(1, self.trigger_count(game, warden))
        self.assertIsNone(self.resolve(game))
        self.assertEqual(life + 1, game.state.players["A"].life)
        self.assertEqual(shuffles + 1, game.state.players["A"].stats.get("shuffle_count", 0))
        for chosen in (bear, warden):
            current = game.state.cards[chosen.object_id]
            self.assertEqual(("battlefield", "A", False), (current.zone, current.controller, current.tapped))
        self.replay(game, load=True)

    def test_actual_threshold_does_not_add_two_opponents_or_include_its_controller(self):
        game, source, _, _ = self.world(61260702, {"A": 4, "B": 2, "C": 1})
        self.own_upkeep(game)
        self.assertEqual(0, self.trigger_count(game, source))
        self.assertEqual("battlefield", game.state.cards[source.object_id].zone)
        self.assertEqual("priority", game.state.pending_decision.kind)
        self.replay(game, load=True)

    def test_actual_resolution_rechecks_before_sacrificing_or_searching(self):
        game, source, field, _ = self.world(61260703, {"B": 3, "C": 1})
        bounce = self.add(game.engine, "Unsummon", zone="hand")
        self.add(game.engine, "Island")
        self.assert_closed(game, bounce)
        self.own_upkeep(game)
        self.assertEqual(1, self.trigger_count(game, source))
        self.cast(game, "A", bounce, [field["B"][2].ref])
        self.resolve_top(game)
        shuffles = game.state.players["A"].stats.get("shuffle_count", 0)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("battlefield", game.state.cards[source.object_id].zone)
        self.assertEqual(shuffles, game.state.players["A"].stats.get("shuffle_count", 0))
        self.replay(game, load=True)

    def test_actual_source_sacrifice_precedes_a_private_search_that_can_find_zero(self):
        game, source, _, (bear, warden, _) = self.world(61260704, {"B": 3})
        self.own_upkeep(game)
        shuffles = game.state.players["A"].stats.get("shuffle_count", 0)
        self.expect_private_search(game, (bear, warden))
        self.assertEqual("graveyard", game.state.cards[source.object_id].zone)
        self.act_ok(game, "A", {"action_id": "choose", "search_cards": []})
        self.assertFalse(game.state.stack)
        self.assertEqual(shuffles + 1, game.state.players["A"].stats.get("shuffle_count", 0))
        self.assertEqual("library", game.state.cards[bear.object_id].zone)
        self.assertEqual("library", game.state.cards[warden.object_id].zone)
        self.replay(game, load=True)

    def test_actual_two_entry_replacements_resume_the_private_search_before_both_arrive(self):
        game, _, _, (bear, warden, _) = self.world(61260705, {"B": 3})
        second = self.add(game.engine, "Grizzly Bears", zone="library", ref="threshold-secret-second-bear")
        for name in ("Grumgully, the Generous", "Hardened Scales", "Doubling Season"):
            self.assert_closed(game, self.add(game.engine, name))
        self.own_upkeep(game)
        before = game.state.players["A"].stats.get("shuffle_count", 0)
        self.expect_private_search(game, (bear, second, warden))
        self.act_ok(game, "A", {"action_id": "choose", "search_cards": [bear.ref, second.ref]})
        self.assertEqual("replacement.order", game.state.pending_decision.kind)
        self.assertEqual(before, game.state.players["A"].stats.get("shuffle_count", 0))
        self.assertTrue(all(game.state.cards[card.object_id].zone == "library" for card in (bear, second)))
        for seat in "BCD":
            packet = json.dumps(game.packet("pilot:" + seat, full=True))
            self.assertNotIn(bear.ref, packet)
            self.assertNotIn(second.ref, packet)
        game = self.replay(game, load=True)
        for _ in range(4):
            pending = game.state.pending_decision
            if pending is None or pending.kind != "replacement.order":
                break
            options = game.packet("pilot:A", full=True)["decision"]["ctx"]["options"]
            scales = next(row for row in options if "hardened" in (row["id"] + row["label"]).casefold())
            self.act_ok(game, "A", {"action_id": "choose", "replacement": scales["id"]})
        self.assertIsNone(self.resolve(game))
        self.assertEqual(before + 1, game.state.players["A"].stats.get("shuffle_count", 0))
        for card in (bear, second):
            current = game.state.cards[card.object_id]
            self.assertEqual(("battlefield", 4), (current.zone, current.counters.get("+1/+1")))
        self.assertEqual("library", game.state.cards[warden.object_id].zone)
        self.replay(game, load=True)

    def test_whole_card_requires_both_authorities_and_the_exact_ordered_upkeep(self):
        record = self.db.lookup(DEFENSE, fuzzy=False)
        report, semantics = register_card(self.db, self.registry, "Defense of the Heart")
        self.assertTrue(report["strict_capability_ready"], report["blockers"])
        self.assertEqual("capability_closed", report["trust_basis"])
        program, = semantics.programs_for_oracle(record.oracle_id)
        sacrifice, search = program.effects
        self.assertEqual(fixed_source_sacrifice_effect(), sacrifice)
        validate_fixed_creature_group_search(search)
        caps = {OPPONENT_CREATURE_THRESHOLD_CAPABILITY, FIXED_CREATURE_GROUP_BATTLEFIELD_SEARCH_CAPABILITY}
        self.assertLessEqual(caps, set(fixed_source_sacrifice_runtime_dependencies(program)))
        for withheld in caps:
            with self.subTest(withheld=withheld):
                deps = tuple(cap for cap in program.capability_dependencies if cap != withheld)
                without_cap = replace(program, capability_dependencies=deps,
                    capability_closure=self.registry.closure(deps, profile=PROFILE).to_dict())
                runtime = bind_semantic_program_runtime(without_cap, capability_registry=self.registry, profile=PROFILE)
                self.assertFalse(runtime["strict"])
                self.assertIn(f"capability:undeclared_runtime_dependency:{withheld}", runtime["blockers"])
        for before, after in (
            ("your upkeep", "that player's upkeep"),
            ("an opponent controls", "all opponents control"),
            ("up to two", "up to three"),
            ("then shuffle.", "then shuffle. Change the name of each card in your hand."),
        ):
            with self.subTest(after=after):
                changed = replace(record, oracle_text=record.oracle_text.replace(before, after))
                self.assertNotEqual("exact", compile_oracle_card(
                    changed, capability_registry=self.registry, capability_profile=PROFILE,
                ).status)
        with patch("quorune.compiler.trigger_result_templates._upkeep_opponent_threshold_search_result", return_value=None):
            self.assertNotEqual("exact", compile_oracle_card(
                record, capability_registry=self.registry, capability_profile=PROFILE,
            ).status)
