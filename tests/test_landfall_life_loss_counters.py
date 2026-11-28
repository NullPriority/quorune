"""Announced landfall target, optional life loss and original source counters."""

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
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.landfall_life_loss_counters import (
    CAPABILITY, LAND_CONDITION, optional_loss_source_counters_effect, target_player_schema,
)
from scripts.build_test_database import build_fixture_database


SOURCE = "Ob Nixilis, the Fallen"
SUPPORT = {
    "next-personal-turn-protection-cards.json": {"Isamaru, Hound of Konda", "Plains"},
    "library-search-rules-cards.json": {"Rampant Growth"},
    "activated-self-transform-cards.json": {"Ephemerate"},
    "veil-color-spell-protection-cards.json": {"Veil of Summer"},
    "life-total-prohibition-cards.json": {"Platinum Emperion"},
    "counter-prohibition-cards.json": {"Solemnity"},
}


class LandfallLifeLossCountersTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        folder = Path(cls.temporary.name)
        fixtures = ROOT / "tests/fixtures"
        sources = [fixtures / "landfall-life-loss-counters-cards.json"]
        for name, selected in SUPPORT.items():
            fixture = json.loads((fixtures / name).read_bytes())
            fixture["cards"] = [card for card in fixture["cards"] if card["name"] in selected]
            assert {card["name"] for card in fixture["cards"]} == selected
            oracle_ids = {card["oracle_id"] for card in fixture["cards"]}
            fixture["rulings"] = [row for row in fixture.get("rulings", []) if row["oracle_id"] in oracle_ids]
            support = folder / name
            support.write_text(json.dumps(fixture), encoding="utf-8")
            sources.append(support)
        database = folder / "landfall-life-counter.sqlite3"
        build_fixture_database(sources, database)
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Landfall life loss and source counters", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def scene(self, seed, *, seat="A"):
        game = self.session(seed)
        game.state.config.semantic_policy = "trusted_only"
        source = self.add(game.engine, SOURCE)
        land = self.add(game.engine, "Plains", zone="hand", seat=seat, ref="native-land-entry")
        self.assert_closed(game, source)
        return game, source, land

    def start(self, game, *support, mana=None, opponent_mana=None, active="A"):
        for card in support:
            self.assert_closed(game, card)
        self.prepare(game, actor=active, active=active, mana=mana or {})
        if active != "A":
            game.state.current_turn = replace(game.state.current_turn, player=active)
            game.state.last_normal_turn_player = active
        if opponent_mana:
            game.state.players["B"].mana_pool.update(opponent_mana)
        self.checkpoint(game)

    def play_land(self, game, land, seat="A"):
        self.priority(game, seat)
        legal = game.packet("pilot:" + seat, full=True)["decision"]["ctx"]["legal"]["actions"]
        offer = next(row for row in legal if row.get("card") == land.ref and row["action"] == "play_land")
        result = game.act("pilot:" + seat, {"action_id": offer["id"]})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("battlefield", game.state.cards[land.object_id].zone)

    def rejects(self, game, principal, command):
        before = authoritative_state_hash(game.state), len(game.commands)
        self.assertFalse(game.act(principal, command).ok)
        self.assertEqual(before, (authoritative_state_hash(game.state), len(game.commands)))

    def announce_target(self, game, target):
        pending = game.state.pending_decision
        self.assertEqual(("semantic.target", ["A"]), (pending.kind, pending.actors))
        visible = game.packet("pilot:A", full=True)["decision"]["ctx"]["target_schema"]
        group, = visible["groups"]
        self.assertIn(target, group["legal_refs"])
        command = {"action_id": "choose", "targets": [target]}
        result = game.act("pilot:A", command)
        self.assertTrue(result.ok, result.summary)
        self.assertEqual([target], game.state.stack[-1].targets)

    def decide(self, game, answer):
        pending = game.state.pending_decision
        self.assertEqual(("semantic.choice", ["A"]), (pending.kind, pending.actors))
        command = {"action_id": "choose", "choice": answer}
        self.rejects(game, "pilot:B", command)
        result = game.act("pilot:A", command)
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(game))

    def counters(self, game, source):
        return game.state.cards[source.object_id].counters.get("+1/+1", 0)

    def test_exact_controlled_land_entry_target_and_fixed_source_are_required(self):
        card = self.db.lookup(SOURCE, fuzzy=False)
        options = {"capability_registry": self.registry, "capability_profile": "commander_review"}
        ir = compile_oracle_card(card, **options)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        node, = ir.faces[0].nodes
        self.assertEqual(card.oracle_text, card.oracle_text[node.span.start:node.span.end])
        self.assertEqual(("land.enter", "battlefield", LAND_CONDITION), (node.event, node.active_zone, node.event_condition))
        self.assertEqual(target_player_schema(), node.target_schema)
        self.assertEqual((optional_loss_source_counters_effect(),), node.effects)
        self.assertIn(CAPABILITY, node.capability_dependencies)
        for old, new in (("a land you control", "a land an opponent controls"), ("lose 3 life", "lose 2 life"),
                         ("on Ob Nixilis.", "on target creature.")):
            with self.subTest(new=new):
                changed = replace(card, oracle_text=card.oracle_text.replace(old, new))
                self.assertNotEqual("exact", compile_oracle_card(changed, **options).status)
        game, _, _ = self.scene(60310014)
        program, = game.engine.semantics.programs_for_oracle(card.oracle_id)
        deps = tuple(dep for dep in program.capability_dependencies if dep != CAPABILITY)
        missing = replace(program, capability_dependencies=deps,
                          capability_closure=self.registry.closure(deps, profile="commander_review").to_dict())
        report = bind_semantic_program_runtime(missing, capability_registry=self.registry, profile="commander_review")
        self.assertIn("capability:undeclared_runtime_dependency:" + CAPABILITY, report["blockers"])

    def test_played_and_spell_arriving_land_allow_apply_and_decline_with_replay(self):
        game, source, land = self.scene(60353001)
        growth = self.add(game.engine, "Rampant Growth", zone="hand")
        found = self.add(game.engine, "Plains", zone="library", ref="search-land-entry")
        self.start(game, growth, mana={"C": 1, "G": 1})
        self.play_land(game, land)
        self.rejects(game, "pilot:A", {"action_id": "choose", "targets": []})
        self.rejects(game, "pilot:B", {"action_id": "choose", "targets": ["C"]})
        game = self.replay(game, load=True)
        self.announce_target(game, "B")
        pending = self.resolve(game)
        self.assertEqual("semantic.choice", pending.kind)
        self.assertEqual(40, game.state.players["B"].life)
        self.assertEqual(0, self.counters(game, source))
        game = self.replay(game, load=True)
        self.decide(game, "apply")
        self.assertEqual(37, game.state.players["B"].life)
        self.assertEqual(3, self.counters(game, source))

        self.cast_for(game, "A", growth)
        self.assertEqual("semantic.search", self.resolve(game).kind)
        accepted = game.act("pilot:A", {"action_id": "choose", "search_cards": [found.ref]})
        self.assertTrue(accepted.ok, accepted.summary)
        entrant = game.state.cards[found.object_id]
        self.assertEqual(("battlefield", "A", True), (entrant.zone, entrant.controller, entrant.tapped))
        self.announce_target(game, "A")
        self.assertEqual("semantic.choice", self.resolve(game).kind)
        self.decide(game, "decline")
        self.assertEqual((40, 37, 40, 40), tuple(game.state.players[p].life for p in "ABCD"))
        self.assertEqual(3, self.counters(game, source))
        self.replay(game, load=True)

    def test_another_players_actual_land_play_creates_no_trigger(self):
        game, source, land = self.scene(60326011, seat="B")
        self.start(game, active="B")
        self.play_land(game, land, "B")
        self.assertEqual(0, self.trigger_count(game, source))
        self.assertIsNone(self.resolve(game))
        self.assertEqual(0, self.counters(game, source))
        self.replay(game, load=True)

    def test_original_source_reentry_still_allows_loss_without_counters(self):
        game, source, land = self.scene(40076031)
        blink = self.add(game.engine, "Ephemerate", zone="hand")
        self.start(game, blink, mana={"W": 1})
        original = source.logical_object_id
        self.play_land(game, land)
        self.announce_target(game, "B")
        self.cast_for(game, "A", blink, [source.ref])
        self.resolve_top(game)
        current = game.state.cards[source.object_id]
        self.assertEqual("battlefield", current.zone)
        self.assertNotEqual(original, current.logical_object_id)
        self.assertEqual("semantic.choice", self.resolve(game).kind)
        game = self.replay(game, load=True)
        self.decide(game, "apply")
        self.assertEqual(37, game.state.players["B"].life)
        self.assertEqual(0, self.counters(game, source))
        self.replay(game, load=True)

    def test_actual_veil_invalidates_the_only_target_before_choice(self):
        game, source, land = self.scene(60826031)
        veil = self.add(game.engine, "Veil of Summer", zone="hand", seat="B")
        self.start(game, veil, opponent_mana={"G": 1})
        self.play_land(game, land)
        self.announce_target(game, "B")
        self.cast_for(game, "B", veil)
        self.resolve_top(game)
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual((40, 0), (game.state.players["B"].life, self.counters(game, source)))
        self.assertTrue(any(event.code == "target.illegal" for event in game.state.events))
        self.replay(game, load=True)

    def test_locked_life_makes_apply_unavailable_while_counter_prevention_is_independent(self):
        for card, life in (("Platinum Emperion", 40), ("Solemnity", 37)):
            with self.subTest(card=card):
                game, source, land = self.scene(60826041)
                rule = self.add(game.engine, card, seat="B")
                self.start(game, rule)
                self.play_land(game, land)
                self.announce_target(game, "B")
                pending = self.resolve(game)
                if card == "Solemnity":
                    self.assertIsNotNone(pending)
                    self.decide(game, "apply")
                else:
                    self.assertIsNone(pending)
                self.assertEqual((life, 0), (game.state.players["B"].life, self.counters(game, source)))
                self.replay(game, load=True)
