"""Actual from-hand cast events, public reveal, free spell and random bottom."""

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
from quorune.replacement.immutable import FrozenMap
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.revealed_library_cast import (
    CAPABILITY, HAND_CAST_CONDITION, MANA_VALUE_CAST_CONTEXT, mana_value_cast_instruction,
)
from scripts.build_test_database import build_fixture_database


SOURCE = "Sunbird's Invocation"
SUPPORT = {
    "next-personal-turn-protection-cards.json": {"Grizzly Bears", "Isamaru, Hound of Konda", "Plains", "Revitalize", "Sol Ring"},
    "bounded-hand-cast-cards.json": {"Wear // Tear", "Village Rites"},
    "bargain-cards.json": {"Counterspell", "Divination", "Ornithopter"},
    "external-entry-tapped-cards.json": {"Blind Obedience"},
    "own-combat-entwine-cards.json": {"Savage Beating"},
    "library-search-rules-cards.json": {"Opt"},
}


class ManaValueRevealedLibraryCastTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        folder = Path(cls.temporary.name)
        fixtures = ROOT / "tests/fixtures"
        sources = [fixtures / "mana-value-revealed-library-cast-cards.json"]
        for name, selected in SUPPORT.items():
            data = json.loads((fixtures / name).read_bytes())
            data["cards"] = [card for card in data["cards"] if card["name"] in selected]
            assert {card["name"] for card in data["cards"]} == selected, name
            oracle_ids = {card["oracle_id"] for card in data["cards"]}
            data["rulings"] = [row for row in data.get("rulings", []) if row["oracle_id"] in oracle_ids]
            support = folder / name
            support.write_text(json.dumps(data), encoding="utf-8")
            sources.append(support)
        database = folder / "revealed-mana-value-cast.sqlite3"
        build_fixture_database(sources, database)
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Reveal by committed cast mana value", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def scene(self, seed, *, invocation_zone="battlefield"):
        game = self.session(seed)
        game.state.config.semantic_policy = "trusted_only"
        invocation = self.add(game.engine, SOURCE, zone=invocation_zone)
        self.assert_closed(game, invocation)
        return game, invocation

    def begin(self, game, mana, *support, opponent_mana=None):
        for card in support:
            self.assert_closed(game, card)
        self.prepare(game, mana=mana)
        if opponent_mana:
            game.state.players["B"].mana_pool.update(opponent_mana)
        self.checkpoint(game)

    def cast_x(self, game, spell, x, target):
        self.priority(game, "A")
        offer = self.action(game, "A", spell)
        response = game.act("pilot:A", {"action_id": offer["id"], "x": x, "targets": [target.ref], "pay": "auto"})
        self.assertTrue(response.ok, response.summary)

    def reveal_options(self, game, shown):
        shown = tuple(shown)
        pending = self.resolve(game)
        self.assertEqual(("semantic.choice", ["A"]), (pending.kind, pending.actors))
        event = [event for event in game.state.events if event.code == "library.reveal"][-1]
        self.assertTrue({"A", "B", "C", "D", "spectator"} <= set(event.visibility))
        self.assertEqual("A", event.details["player"])
        self.assertEqual([card.ref for card in shown], [card["ref"] for card in event.details["cards"]])
        self.assertEqual({card.object_id for card in shown}, set(event.changed_objects))
        for card in shown:
            self.assertEqual(set("ABCD"), set(game.state.cards[card.object_id].revealed_to))
        for other in "BCD":
            packet = game.packet("pilot:" + other, full=True)
            self.assertIsNone(packet["decision"])
            self.assertTrue(all(card.ref in json.dumps(packet) for card in shown))
        context = game.packet("pilot:A", full=True)["decision"]["ctx"]
        return context.get("cast_options") or context["context"]["cast_options"]

    def cast_choice(self, option, card, **extra):
        return {"action_id": "choose", "choice": "cast", "cast": {
            "card": card.ref, "cost_option": option["id"], "pay": "auto", **extra,
        }}

    def reject(self, game, command, seat="A"):
        before = authoritative_state_hash(game.state), len(game.commands)
        result = game.act("pilot:" + seat, command)
        self.assertFalse(result.ok, result.summary)
        self.assertEqual(before, (authoritative_state_hash(game.state), len(game.commands)))

    def accept(self, game, command):
        result = game.act("pilot:A", command)
        self.assertTrue(result.ok, result.summary)

    def spell_on_stack(self, game, card):
        return next(item for item in game.state.stack if item.kind == "spell" and item.card_object_id == card.object_id)

    def test_exact_hand_cast_and_complete_mandatory_reveal_cast_remainder_shape(self):
        card = self.db.lookup(SOURCE)
        options = {"capability_registry": self.registry, "capability_profile": "commander_review"}
        ir = compile_oracle_card(card, **options)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        node, = ir.faces[0].nodes
        self.assertEqual(card.oracle_text, card.oracle_text[node.span.start:node.span.end])
        self.assertEqual(("spell.cast", "battlefield", HAND_CAST_CONDITION),
                         (node.event, node.active_zone, FrozenMap(node.event_condition)))
        self.assertEqual((mana_value_cast_instruction(),), node.effects)
        self.assertIn(CAPABILITY, node.capability_dependencies)
        for old, new in (("from your hand", "from exile"), ("X or less", "exactly X"),
                         ("in a random order", "in any order")):
            with self.subTest(new=new):
                changed = replace(card, oracle_text=card.oracle_text.replace(old, new))
                self.assertNotEqual("exact", compile_oracle_card(changed, **options).status)
        game, _ = self.scene(60320071)
        program, = game.engine.semantics.programs_for_oracle(card.oracle_id)
        deps = tuple(dep for dep in program.capability_dependencies if dep != CAPABILITY)
        changed = replace(program, capability_dependencies=deps,
                          capability_closure=self.registry.closure(deps, profile="commander_review").to_dict())
        report = bind_semantic_program_runtime(changed, capability_registry=self.registry, profile="commander_review")
        self.assertIn("capability:undeclared_runtime_dependency:" + CAPABILITY, report["blockers"])

    def test_actual_announced_x_reveals_public_prefix_and_casts_a_split_face_from_library(self):
        game, invocation = self.scene(10730041)
        origin = self.add(game.engine, "Heat Ray", zone="hand")
        victim = self.add(game.engine, "Grizzly Bears", seat="B")
        enchantment = self.add(game.engine, "Blind Obedience", seat="B")
        hidden = self.add(game.engine, "Opt", zone="library", ref="unrevealed-opt")
        land = self.add(game.engine, "Plains", zone="library", ref="revealed-land")
        split = self.add(game.engine, "Wear // Tear", zone="library", ref="revealed-split")
        self.begin(game, {"C": 1, "R": 1}, origin, enchantment, split)
        self.cast_x(game, origin, 1, victim)
        self.assertEqual(1, self.trigger_count(game, invocation))
        trigger = game.state.stack[-1]
        self.assertEqual(2, trigger.context[MANA_VALUE_CAST_CONTEXT]["mana_value"])
        game = self.replay(game, load=True)
        options = self.reveal_options(game, [split, land])
        self.assertEqual({split.ref}, {option["card"] for option in options})
        tear, = [option for option in options if option["face"] == "Tear"]
        for seat in "BCD":
            self.assertNotIn(hidden.ref, json.dumps(game.packet("pilot:" + seat, full=True)))
        good = self.cast_choice(tear, split, face="Tear", targets=[enchantment.ref])
        self.reject(game, good, "B")
        self.reject(game, self.cast_choice(tear, hidden, targets=[enchantment.ref]))
        game = self.replay(game, load=True)
        self.accept(game, good)
        cast = self.spell_on_stack(game, split)
        self.assertEqual(("Tear", "library"), (cast.chosen_face, cast.context["cast_origin"]))
        self.assertEqual(cast.ref, game.state.stack[-1].ref)
        self.assertEqual(0, self.trigger_count(game, invocation))
        self.assertEqual(land.object_id, game.state.players["A"].zones["library"][0])
        self.resolve_top(game)
        self.assertEqual("graveyard", game.state.cards[enchantment.object_id].zone)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(1, game.state.cards[victim.object_id].marked_damage)
        self.replay(game, load=True)

    def test_actual_original_counter_leaves_mandatory_reveal_and_random_decline(self):
        game, invocation = self.scene(11370041)
        origin = self.add(game.engine, "Divination", zone="hand")
        counter = self.add(game.engine, "Counterspell", seat="B", zone="hand")
        hidden = self.add(game.engine, "Sol Ring", zone="library", ref="not-revealed-ring")
        shown = [self.add(game.engine, name, zone="library", ref=f"public-top-{index}")
                 for index, name in enumerate(("Plains", "Opt", "Revitalize"))]
        self.begin(game, {"C": 2, "U": 1}, origin, counter, *shown[1:], opponent_mana={"U": 2})
        original_library = tuple(game.state.players["A"].zones["library"])
        self.cast_for(game, "A", origin)
        original = self.spell_on_stack(game, origin)
        self.cast_for(game, "B", counter, [original.ref])
        self.resolve_top(game)
        self.assertEqual("graveyard", game.state.cards[origin.object_id].zone)
        self.assertEqual(1, self.trigger_count(game, invocation))
        self.reveal_options(game, reversed(shown))
        game = self.replay(game, load=True)
        self.reject(game, {"action_id": "choose", "choice": "decline", "order": [card.ref for card in shown]})
        self.accept(game, {"action_id": "choose", "choice": "decline"})
        library = tuple(game.state.players["A"].zones["library"])
        self.assertEqual({card.object_id for card in shown}, set(library[:3]))
        self.assertEqual(original_library[:-3], library[3:])
        for card in shown:
            self.assertEqual(([], []), (game.state.cards[card.object_id].known_to, game.state.cards[card.object_id].revealed_to))
        for seat in "BCD":
            self.assertNotIn(hidden.ref, json.dumps(game.packet("pilot:" + seat, full=True)))
        self.replay(game, load=True)

    def test_free_cast_x_must_be_zero_and_other_costs_and_printed_timing_still_apply(self):
        game, invocation = self.scene(60826071)
        origin = self.add(game.engine, SOURCE, zone="hand", ref="other-invocation")
        victim = self.add(game.engine, "Grizzly Bears", ref="sacrifice-creature")
        foreign = self.add(game.engine, "Grizzly Bears", seat="B", ref="foreign-creature")
        shown = [self.add(game.engine, name, zone="library", ref=f"price-top-{index}")
                 for index, name in enumerate(("Plains", "Plains", "Plains", "Village Rites", "Heat Ray", "Savage Beating"))]
        self.begin(game, {"C": 5, "R": 1}, origin, *shown[3:])
        self.cast_for(game, "A", origin)
        options = self.reveal_options(game, reversed(shown))
        rites, heat, restricted = shown[3:]
        self.assertEqual({rites.ref, heat.ref}, {option["card"] for option in options})
        self.assertNotIn(restricted.ref, {option["card"] for option in options})
        self.assertEqual(1, self.trigger_count(game, invocation))
        heat_offer, = [option for option in options if option["card"] == heat.ref]
        rites_offer, = [option for option in options if option["card"] == rites.ref]
        self.reject(game, self.cast_choice(heat_offer, heat, x=1, targets=[foreign.ref]))
        self.assertIn(victim.ref, rites_offer["choice_schema"]["sacrifice_cards"]["legal_refs"])
        self.reject(game, self.cast_choice(rites_offer, rites, sacrifice_cards=[foreign.ref]))
        game = self.replay(game, load=True)
        self.accept(game, self.cast_choice(rites_offer, rites, sacrifice_cards=[victim.ref]))
        self.assertEqual("graveyard", game.state.cards[victim.object_id].zone)
        self.assertEqual("library", self.spell_on_stack(game, rites).context["cast_origin"])
        self.assertFalse(any(game.state.players["A"].mana_pool.values()))
        self.assertEqual({card.object_id for card in shown if card is not rites},
                         set(game.state.players["A"].zones["library"][:5]))
        self.assertIsNone(self.resolve(game))
        self.replay(game, load=True)

    def test_zero_value_does_nothing_and_casting_the_source_itself_does_not_trigger(self):
        for initial_source in (False, True):
            with self.subTest(initial_source=initial_source):
                if initial_source:
                    game, invocation = self.scene(10730000)
                    spell = self.add(game.engine, "Ornithopter", zone="hand")
                else:
                    game, invocation = self.scene(10730006, invocation_zone="hand")
                    spell = invocation
                self.begin(game, {"C": 5, "R": 1}, spell)
                original = tuple(game.state.players["A"].zones["library"])
                self.cast_for(game, "A", spell)
                self.assertEqual(int(initial_source), self.trigger_count(game, invocation))
                self.assertIsNone(self.resolve(game))
                self.assertEqual(original, tuple(game.state.players["A"].zones["library"]))
                self.assertFalse(any(event.code in {"library.look", "library.reveal"} for event in game.state.events))
                self.replay(game, load=True)
