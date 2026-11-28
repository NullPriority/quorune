"""The exact Mardu/Jeskai entry choice and independently active branches."""

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from test_controller_creature_keyword_absence import register_card
from test_qualified_zone_event_queries import query_record
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.continuous_effect_model import Layer
from quorune.deck import DeckDefinition, DeckEntry
from quorune.entry_designations import ENTRY_MARDU_JESKAI_CAPABILITY
from quorune.fixed_token_production import CREATED_TOKEN_HASTE_LIFELINK_CAPABILITY
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.faction_trigger_model import FACTION_ATTACK_MULTIPLIER_CAPABILITY, FACTION_UPKEEP_TOKEN_CAPABILITY
from scripts.build_test_database import build_fixture_database


CARD = "Windcrag Siege"
PROFILE = "commander_review"
FIXTURES = ROOT / "tests" / "fixtures"
LINDEN = "Linden, the Steadfast Queen"


class MarduJeskaiEntryTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "mardu-jeskai-entry.sqlite3"
        build_fixture_database([FIXTURES / name for name in (
            "mardu-jeskai-entry-cards.json", "qualified-attack-cards.json", "cast-target-copy-cards.json",
            "fixed-extra-turn-cards.json", "activation-result-views-cards.json", "bound-effect-program-cards.json",
        )], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Faction entry branches", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"),
            DeckEntry("Generic Bound Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def test_exact_choice_branch_symbols_and_independent_runtime_authorities(self):
        printed = self.db.lookup(CARD, fuzzy=False)
        record = replace(query_record(printed.oracle_text), type_line="Enchantment", mana_cost="{1}{R}{W}")
        ir = compile_oracle_card(record, capability_registry=self.registry, capability_profile=PROFILE)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        entry, mardu, jeskai = ir.faces[0].nodes
        self.assertEqual(tuple(printed.oracle_text.splitlines()), tuple(
            printed.oracle_text[node.span.start:node.span.end] for node in (entry, mardu, jeskai)))
        self.assertIn(ENTRY_MARDU_JESKAI_CAPABILITY, entry.capability_dependencies)
        self.assertIn(FACTION_ATTACK_MULTIPLIER_CAPABILITY, mardu.capability_dependencies)
        self.assertEqual("Mardu", mardu.handlers[0]["fragment"]["value"]["required_faction"])
        self.assertIn(FACTION_UPKEEP_TOKEN_CAPABILITY, jeskai.capability_dependencies)
        self.assertIn(CREATED_TOKEN_HASTE_LIFELINK_CAPABILITY, jeskai.capability_dependencies)
        for old, new in (
            ("Mardu or Jeskai", "Mardu or Abzan"), ("a creature attacking", "a spell resolving"),
            ("lifelink and haste until end of turn", "lifelink and haste"),
        ):
            with self.subTest(new=new):
                mutant = replace(record, oracle_text=printed.oracle_text.replace(old, new))
                result = compile_oracle_card(mutant, capability_registry=self.registry, capability_profile=PROFILE)
                self.assertNotEqual("exact", result.status)
        orphan = replace(record, oracle_text=printed.oracle_text.splitlines()[1].removeprefix("• "))
        self.assertNotEqual("exact", compile_oracle_card(orphan, capability_registry=self.registry,
                                                       capability_profile=PROFILE).status)

        admitted, semantics = register_card(self.db, self.registry, CARD)
        self.assertTrue(admitted["strict_capability_ready"], admitted["blockers"])
        programs = semantics.programs_for_oracle(printed.oracle_id)
        for event, capability in (
            ("zone.change", ENTRY_MARDU_JESKAI_CAPABILITY), ("continuous", FACTION_ATTACK_MULTIPLIER_CAPABILITY),
            ("step.begin", FACTION_UPKEEP_TOKEN_CAPABILITY), ("step.begin", CREATED_TOKEN_HASTE_LIFELINK_CAPABILITY),
        ):
            with self.subTest(capability=capability):
                program, = [value for value in programs if value.event == event]
                deps = [key for key in program.capability_dependencies if key != capability]
                changed = replace(program, capability_dependencies=deps,
                                  capability_closure=self.registry.closure(deps, profile=PROFILE).to_dict())
                self.assertFalse(bind_semantic_program_runtime(changed, capability_registry=self.registry,
                                                               profile=PROFILE)["strict"])

    def choose_faction(self, game, source, choice, *, zone):
        pending = self.resolve(game)
        self.assertEqual(("replacement.order", ["A"]), (pending.kind, pending.actors))
        game = self.replay(game, load=True)
        self.assertEqual(zone, game.state.cards[source.object_id].zone)
        decision = game.packet("pilot:A", full=True)["decision"]
        options = {row["id"].rsplit(":", 1)[-1]: row["id"] for row in decision["ctx"]["options"]}
        self.assertEqual({"Mardu", "Jeskai"}, set(options))
        command = {"action_id": "choose", "choices": {"replacement": options[choice]}}
        before = authoritative_state_hash(game.state)
        self.assertIsNone(game.packet("pilot:B", full=True)["decision"])
        self.assertFalse(game.act("pilot:B", command).ok)
        self.assertFalse(game.act("pilot:A", {"action_id": "choose", "choices": {"replacement": "Brotherhood"}}).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        accepted = game.act("pilot:A", command)
        self.assertTrue(accepted.ok, accepted.summary)
        card = game.state.cards[source.object_id]
        self.assertEqual(("battlefield", choice), (card.zone, card.annotations.get("chosen_faction")))
        return game

    def test_native_mardu_doubles_original_attacks_and_reentry_requires_a_fresh_choice(self):
        game = self.session(60320401)
        siege = self.add(game.engine, CARD, zone="hand")
        linden = self.add(game.engine, LINDEN)
        linden.acquired_control_turn_count = -1
        flicker = self.add(game.engine, "Flicker of Fate", zone="hand")
        self.add(game.engine, "Plains", ref="faction-flicker-first")
        self.add(game.engine, "Plains", ref="faction-flicker-second")
        for card in (siege, linden, flicker):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 1, "R": 1, "W": 1})
        self.checkpoint(game)
        self.cast(game, "A", siege)
        game = self.choose_faction(game, siege, "Mardu", zone="stack")
        source = game.state.cards[siege.object_id]
        original = source.logical_object_id
        life = game.state.players["A"].life
        self.attack(game, [(linden, "B")])
        self.assertEqual(2, self.trigger_count(game, linden))
        self.cast(game, "A", flicker, [source.ref])
        game = self.choose_faction(game, siege, "Jeskai", zone="exile")
        self.assertNotEqual(original, game.state.cards[siege.object_id].logical_object_id)
        self.assertEqual(2, self.trigger_count(game, linden))
        self.assertIsNone(self.resolve(game))
        self.assertEqual(life + 2, game.state.players["A"].life)
        self.replay(game, load=True)

    def test_native_jeskai_upkeep_goblin_attacks_and_both_noncopiable_keywords_expire(self):
        game = self.session(60320402)
        spare = game.state.players["A"].zones["hand"][0]
        game.engine.move_card(spare, "library", position="bottom", log=False)
        siege = self.add(game.engine, CARD, zone="hand")
        extra = self.add(game.engine, "Time Warp", zone="hand")
        linden = self.add(game.engine, LINDEN)
        linden.acquired_control_turn_count = -1
        private = self.add(game.engine, "Flicker of Fate", seat="D", zone="hand", ref="faction-private")
        for card in (siege, extra, linden):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 4, "R": 1, "W": 1, "U": 2})
        self.checkpoint(game)
        self.cast(game, "A", siege)
        game = self.choose_faction(game, siege, "Jeskai", zone="stack")
        self.cast(game, "A", extra, ["A"])
        self.assertIsNone(self.resolve(game))
        turn, = game.state.extra_turns
        self.until(game, lambda g: g.state.current_turn is not None and g.state.current_turn.turn_id == turn.turn_id
                   and g.state.step == "upkeep" and self.trigger_count(g, siege) == 1)
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        token, = [card for card in game.state.cards.values() if card.is_token and card.printed_name == "Goblin Token"]
        keywords = {"Haste", "Lifelink"}
        self.assertLessEqual(keywords, set(game.engine._effective_card_data(token)["keywords"]))
        self.assertFalse(keywords.intersection(game.engine._effective_card_data(token, maximum_layer=Layer.COPY)["keywords"]))
        self.assertEqual(("A", False), (token.controller, token.tapped))
        self.assertNotIn(private.ref, json.dumps(game.packet("pilot:A", full=True)))
        before = {seat: game.state.players[seat].life for seat in "ABCD"}
        self.attack(game, [(linden, "C"), (token, "B")])
        self.assertEqual(1, self.trigger_count(game, linden))
        self.until(game, lambda g: g.state.phase == "postcombat_main")
        self.assertEqual({**before, "A": before["A"] + 2, "B": before["B"] - 1, "C": before["C"] - 3},
                         {seat: game.state.players[seat].life for seat in "ABCD"})
        self.until(game, lambda g: g.state.active_player == "B" and g.state.step == "upkeep")
        self.assertFalse(keywords.intersection(game.engine._effective_card_data(token.object_id)["keywords"]))
        self.replay(game, load=True)
