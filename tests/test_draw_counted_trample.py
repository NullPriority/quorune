"""A spell's committed draw is counted before its targeted temporary bonus."""

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
from quorune.drawing.restrictions import drawn_this_turn
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.scalar_effect_amount_model import SCALAR_AMOUNT_CAPABILITY, ScalarAmountOrigin, ScalarEffectAmountSpec
from scripts.build_test_database import build_fixture_database


SOURCE = "Fists of Flame"
SUPPORT = {
    "next-personal-turn-protection-cards.json": {"Isamaru, Hound of Konda", "Plains", "Grizzly Bears", "Revitalize"},
    "library-search-rules-cards.json": {"Demonic Tutor"},
    "discard-result-negative-offset-cards.json": {"Dakmor Salvage", "Narset, Parter of Veils"},
    "activated-self-transform-cards.json": {"Ephemerate"},
}


class DrawCountedTrampleTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        folder = Path(cls.temporary.name)
        fixtures = ROOT / "tests/fixtures"
        sources = [fixtures / "draw-counted-trample-cards.json"]
        for name, selected in SUPPORT.items():
            fixture = json.loads((fixtures / name).read_bytes())
            fixture["cards"] = [card for card in fixture["cards"] if card["name"] in selected]
            assert {card["name"] for card in fixture["cards"]} == selected
            oracle_ids = {card["oracle_id"] for card in fixture["cards"]}
            fixture["rulings"] = [row for row in fixture.get("rulings", []) if row["oracle_id"] in oracle_ids]
            support = folder / name
            support.write_text(json.dumps(fixture), encoding="utf-8")
            sources.append(support)
        database = folder / "draw-counted-trample.sqlite3"
        build_fixture_database(sources, database)
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Draw count and targeted trample", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def scene(self, seed):
        game = self.session(seed)
        game.state.config.semantic_policy = "trusted_only"
        source = self.add(game.engine, SOURCE, zone="hand")
        target = self.add(game.engine, "Grizzly Bears", seat="B", ref="foreign-creature")
        self.assert_closed(game, source)
        return game, source, target

    def start(self, game, mana, *support, opponent_mana=None):
        for card in support:
            self.assert_closed(game, card)
        self.prepare(game, mana=mana)
        if opponent_mana:
            game.state.players["B"].mana_pool.update(opponent_mana)
        self.checkpoint(game)

    def current(self, game, target):
        return game.state.cards[target.object_id]

    def check_creature(self, game, target, *, power, trample):
        card = self.current(game, target)
        self.assertEqual((power, 2), (
            game.engine._numeric_stat(card.object_id, "power"),
            game.engine._numeric_stat(card.object_id, "toughness"),
        ))
        keywords = {keyword.casefold() for keyword in game.engine._effective_card_data(card).get("keywords", ())}
        self.assertEqual(trample, "trample" in keywords)

    def test_exact_printed_source_preserves_draw_then_scalar_and_keyword_owners(self):
        card = self.db.lookup(SOURCE, fuzzy=False)
        options = {"capability_registry": self.registry, "capability_profile": "commander_review"}
        ir = compile_oracle_card(card, **options)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        node, = ir.faces[0].nodes
        self.assertEqual(card.oracle_text, card.oracle_text[node.span.start:node.span.end])
        self.assertEqual({"zones": ["battlefield"], "categories": ["permanent"], "count": 1, "types_any": ["creature"]},
                         node.target_schema)
        draw, stats, grant = node.effects
        self.assertEqual({"op": "draw", "player": "$controller", "count": 1, "private": True}, draw)
        self.assertEqual(("modify_stats_until_end_of_turn", "$target.0", 0),
                         (stats["op"], stats["card"], stats["toughness"]))
        amount = ScalarEffectAmountSpec.from_dict(stats["power"])
        self.assertEqual((ScalarAmountOrigin.HISTORY, "cards_drawn", 1),
                         (amount.origin, amount.history_fact, amount.coefficient))
        self.assertEqual({"op": "grant_keyword_until_end_of_turn", "card": "$target.0", "keyword": "Trample"}, grant)
        stripped = card.oracle_text.split(" (", maxsplit=1)[0]
        for old, new in (("drawn this turn", "drawn last turn"), ("each card you've drawn", "each card that creature drew")):
            with self.subTest(new=new):
                changed = replace(card, oracle_text=stripped.replace(old, new))
                self.assertNotEqual("exact", compile_oracle_card(changed, **options).status)
        game, _, _ = self.scene(12115001)
        program, = game.engine.semantics.programs_for_oracle(card.oracle_id)
        deps = tuple(dep for dep in program.capability_dependencies if dep != SCALAR_AMOUNT_CAPABILITY)
        unbound = replace(program, capability_dependencies=deps,
                          capability_closure=self.registry.closure(deps, profile="commander_review").to_dict())
        report = bind_semantic_program_runtime(unbound, capability_registry=self.registry, profile="commander_review")
        self.assertIn("capability:undeclared_runtime_dependency:" + SCALAR_AMOUNT_CAPABILITY, report["blockers"])

    def test_actual_postdraw_amount_ignores_tutor_locks_and_expires(self):
        game, source, target = self.scene(60826111)
        first = self.add(game.engine, "Revitalize", zone="hand", ref="early-draw")
        later = self.add(game.engine, "Revitalize", zone="hand", ref="later-draw")
        tutor = self.add(game.engine, "Demonic Tutor", zone="hand")
        searched = self.add(game.engine, "Grizzly Bears", zone="library", ref="private-tutor-find")
        self.start(game, {"C": 12, "B": 1, "W": 2, "R": 1}, first, later, tutor)
        turn = game.state.turn_sequence
        prior = drawn_this_turn(game.engine, "A")
        self.cast_for(game, "A", tutor, reserve={"W": 2, "R": 1})
        self.assertEqual("semantic.search", self.resolve(game).kind)
        accepted = game.act("pilot:A", {"action_id": "choose", "search_cards": [searched.ref]})
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertEqual("hand", self.current(game, searched).zone)
        self.assertEqual(prior, drawn_this_turn(game.engine, "A"))
        self.cast_for(game, "A", first, reserve={"W": 1, "R": 1})
        self.assertIsNone(self.resolve(game))
        self.assertEqual(prior + 1, drawn_this_turn(game.engine, "A"))
        self.cast_for(game, "A", source, [target.ref], reserve={"W": 1})
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(prior + 2, drawn_this_turn(game.engine, "A"))
        self.assertEqual("B", self.current(game, target).controller)
        self.check_creature(game, target, power=2 + prior + 2, trample=True)
        self.cast_for(game, "A", later)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(prior + 3, drawn_this_turn(game.engine, "A"))
        self.check_creature(game, target, power=2 + prior + 2, trample=True)
        game = self.replay(game, load=True)
        self.until(game, lambda s: (s.state.turn_sequence > turn
                                   or getattr(s.state.pending_decision, "kind", None) == "cleanup.discard"))
        if game.state.turn_sequence == turn:
            ctx = game.packet("pilot:A", full=True)["decision"]["ctx"]
            result = game.act("pilot:A", {
                "action_id": "discard", "cards": [card["id"] for card in ctx["hand"][:ctx["count"]]],
            })
            self.assertTrue(result.ok, result.summary)
            self.until(game, lambda s: s.state.turn_sequence > turn)
        self.assertEqual(0, drawn_this_turn(game.engine, "A"))
        self.check_creature(game, target, power=2, trample=False)
        self.replay(game, load=True)

    def test_actual_dredge_replaces_the_draw_leaving_trample_at_zero_bonus(self):
        game, source, target = self.scene(12126011)
        dredger = self.add(game.engine, "Dakmor Salvage", zone="graveyard")
        self.start(game, {"C": 1, "R": 1}, dredger)
        prior = drawn_this_turn(game.engine, "A")
        self.assertEqual(0, prior)
        top = list(game.state.players["A"].zones["library"][-2:])
        self.cast_for(game, "A", source, [target.ref])
        self.assertEqual("draw.replacement", self.resolve(game).kind)
        game = self.replay(game, load=True)
        options = game.packet("pilot:A", full=True)["decision"]["ctx"]["options"]
        replacement, = [row for row in options if "Dredge" in row["label"]]
        command = {"action_id": "choose", "choice": replacement["id"]}
        before = authoritative_state_hash(game.state), len(game.commands)
        self.assertFalse(game.act("pilot:B", command).ok)
        self.assertEqual(before, (authoritative_state_hash(game.state), len(game.commands)))
        self.check_creature(game, target, power=2, trample=False)
        result = game.act("pilot:A", command)
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("hand", self.current(game, dredger).zone)
        self.assertTrue(all(game.state.cards[oid].zone == "graveyard" for oid in top))
        self.assertEqual(prior, drawn_this_turn(game.engine, "A"))
        self.check_creature(game, target, power=2, trample=True)
        self.replay(game, load=True)

    def test_actual_narset_prohibition_does_not_increase_prior_committed_count(self):
        game, source, target = self.scene(12122011)
        limiter = self.add(game.engine, "Narset, Parter of Veils", seat="B")
        earlier = self.add(game.engine, "Revitalize", zone="hand")
        self.start(game, {"C": 4, "W": 1, "R": 1}, limiter, earlier)
        self.cast_for(game, "A", earlier, reserve={"R": 1})
        self.assertIsNone(self.resolve(game))
        prior = drawn_this_turn(game.engine, "A")
        self.assertEqual(1, prior)
        self.cast_for(game, "A", source, [target.ref])
        start = game.state.event_sequence
        self.assertIsNone(self.resolve(game))
        self.assertEqual(prior, drawn_this_turn(game.engine, "A"))
        events = [event for event in game.state.events if event.event_id > start]
        self.assertEqual(1, sum(event.code == "card.draw.prohibited" for event in events))
        self.check_creature(game, target, power=3, trample=True)
        self.replay(game, load=True)

    def test_actual_target_reentry_invalidates_the_whole_spell_including_the_draw(self):
        game, source, target = self.scene(60826117)
        blink = self.add(game.engine, "Ephemerate", zone="hand", seat="B")
        self.start(game, {"C": 1, "R": 1}, blink, opponent_mana={"W": 1})
        prior = drawn_this_turn(game.engine, "A")
        original = target.logical_object_id
        self.cast_for(game, "A", source, [target.ref])
        self.cast_for(game, "B", blink, [target.ref])
        self.resolve_top(game)
        self.assertNotEqual(original, self.current(game, target).logical_object_id)
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(prior, drawn_this_turn(game.engine, "A"))
        self.assertEqual("graveyard", self.current(game, source).zone)
        self.check_creature(game, target, power=2, trample=False)
        self.replay(game, load=True)
