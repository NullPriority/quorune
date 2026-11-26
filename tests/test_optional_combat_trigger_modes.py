"""Optional nonrepeating modes chosen during the placement of a combat trigger."""

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
from quorune.replacement.immutable import thaw_value
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.modal_selection import OPTIONAL_COMBAT_TRIGGER_MODAL_CAPABILITY
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


ORIGINAL = "Rankle, Master of Pranks"
PROFILE = "commander_review"


class OptionalCombatTriggerModeTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "optional-combat-modes.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/optional-combat-trigger-modes-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("Optional combat modes", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Forest", 30),
        ], [commander])

    def ready(self, game, rankle, *, generic=2, companions=()):
        game.state.config.semantic_policy = "trusted_only"
        for card in (rankle, *companions):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": generic, "B": 2})
        self.checkpoint(game)
        self.cast(game, "A", rankle)
        self.resolve_top(game)

    def await_combat_modes(self, game, rankle):
        self.attack(game, [(rankle, "B")])
        self.until(game, lambda current: current.state.pending_decision.kind == "semantic.target")
        pending = game.state.pending_decision
        self.assertEqual(["A"], pending.actors)
        self.assertEqual(1, self.trigger_count(game, rankle))
        schema = game.packet("pilot:A", full=True)["decision"]["ctx"]["target_schema"]
        self.assertEqual((0, 0, 3), (schema["mode_count"], schema["min_modes"], schema["max_modes"]))
        self.assertEqual(["mode_1", "mode_2", "mode_3"], schema["legal_modes"])
        self.assertEqual([], schema["legal_refs"])
        return game.state.stack[-1].ref

    def act(self, game, seat, command):
        result = game.act("pilot:" + seat, command)
        self.assertTrue(result.ok, result.summary)

    def reject(self, game, seat, command):
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:" + seat, command).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))

    def expect_apnap(self, game, seat, cards):
        pending = self.resolve(game)
        self.assertIsNotNone(pending)
        self.assertEqual(("choice.apnap", [seat]), (pending.kind, pending.actors))
        ctx = game.packet("pilot:" + seat, full=True)["decision"]["ctx"]
        self.assertEqual(1, ctx["count"])
        self.assertEqual({card.ref for card in cards}, set(ctx["options"]))
        return ctx["prior_public_choices"]

    def choose_card(self, game, seat, card):
        self.act(game, seat, {"action_id": "choose", "cards": [card.ref]})

    def activate(self, game, permanent, target):
        self.priority(game, "A")
        actions = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        action, = (item for item in actions if item["id"].startswith("activate:" + permanent.ref + ":"))
        self.act(game, "A", {"action_id": action["id"], "targets": [target], "pay": "auto"})
        self.resolve_top(game)

    def test_actual_all_modes_are_ordered_with_private_discard_and_simultaneous_sacrifice(self):
        game = self.session(700260301)
        for player in game.state.players.values():
            for object_id in tuple(player.zones["hand"]):
                game.engine.move_card(object_id, "library", log=False)
        rankle = self.add(game.engine, ORIGINAL, zone="hand")
        held = {seat: self.add(game.engine, "Forest", seat=seat, zone="hand", ref=f"rankle-private-held-{seat}")
                for seat in "ABC"}
        spare = self.add(game.engine, "Forest", zone="hand", ref="rankle-private-spare")
        creatures = {seat: self.add(game.engine, "Grizzly Bears", seat=seat, ref=f"rankle-creature-{seat}")
                     for seat in "ABC"}
        game.state.players["B"].life = 4
        self.ready(game, rankle)
        stack_ref = self.await_combat_modes(game, rankle)
        self.assertEqual(1, game.state.players["B"].life)
        self.assertEqual("hand", game.state.cards[held["A"].object_id].zone)
        self.reject(game, "B", {"action_id": "choose", "modes": ["mode_3"]})
        self.reject(game, "A", {"action_id": "choose", "modes": ["mode_3", "mode_3"]})
        self.reject(game, "A", {"action_id": "choose", "modes": ["mode_2"], "targets": ["B"]})
        game = self.replay(game, load=True)
        self.act(game, "A", {"action_id": "choose", "modes": ["mode_3", "mode_1", "mode_2"]})
        stack = game.state.stack[-1]
        self.assertEqual((stack_ref, ["mode_1", "mode_2", "mode_3"]), (stack.ref, list(stack.modes)))
        self.assertEqual("priority", game.state.pending_decision.kind)
        self.expect_apnap(game, "A", [held["A"], spare])
        self.choose_card(game, "A", held["A"])
        game = self.replay(game, load=True)
        self.assertEqual({}, self.expect_apnap(game, "B", [held["B"]]))
        for seat in "BCD":
            foreign = json.dumps(game.packet("pilot:" + seat, full=True))
            self.assertNotIn(held["A"].ref, foreign)
            self.assertNotIn(spare.ref, foreign)
        self.choose_card(game, "B", held["B"])
        self.assertEqual({}, self.expect_apnap(game, "C", [held["C"]]))
        self.assertEqual(["hand"] * 3, [game.state.cards[card.object_id].zone for card in held.values()])
        self.assertEqual(1, game.state.players["B"].life)
        before = len(game.state.events)
        self.choose_card(game, "C", held["C"])

        self.expect_apnap(game, "A", [rankle, creatures["A"]])
        self.assertEqual(["graveyard"] * 3, [game.state.cards[card.object_id].zone for card in held.values()])
        self.assertEqual([39, 0, 39, 39], [game.state.players[seat].life for seat in "ABCD"])
        self.assertTrue(game.state.players["B"].in_game)
        self.assertEqual(list("ABCD"), [event.actor for event in game.state.events[before:] if event.code == "card.draw"])
        private_draw, = (event.details["objects"][0] for event in game.state.events[before:]
                         if event.code == "card.draw.private" and event.actor == "D")
        for seat in "ABC":
            self.assertNotIn(private_draw, json.dumps(game.packet("pilot:" + seat, full=True)))
        self.choose_card(game, "A", creatures["A"])
        self.assertEqual([creatures["A"].ref], self.expect_apnap(game, "B", [creatures["B"]])["A"])
        self.choose_card(game, "B", creatures["B"])
        self.expect_apnap(game, "C", [creatures["C"]])
        self.assertEqual(["battlefield"] * 3, [game.state.cards[card.object_id].zone for card in creatures.values()])
        sacrificed = {game.state.cards[card.object_id].logical_object_id for card in creatures.values()}
        self.choose_card(game, "C", creatures["C"])
        self.assertIsNone(self.resolve(game))
        self.assertEqual(sacrificed, {fact.object_incarnation for fact in game.engine._current_turn_history("permanent_sacrificed")})
        self.assertEqual(["graveyard", "outside", "graveyard"],
                         [game.state.cards[card.object_id].zone for card in creatures.values()])
        self.assertFalse(game.state.players["B"].in_game)
        self.assertEqual("battlefield", game.state.cards[rankle.object_id].zone)
        self.replay(game, load=True)

    def test_actual_zero_modes_remove_the_pending_trigger_before_priority(self):
        game = self.session(700260302)
        rankle = self.add(game.engine, ORIGINAL, zone="hand")
        self.ready(game, rankle)
        ref = self.await_combat_modes(game, rankle)
        hand = [len(game.state.players[seat].zones["hand"]) for seat in "ABCD"]
        game = self.replay(game, load=True)
        self.act(game, "A", {"action_id": "choose", "modes": []})
        self.assertEqual("priority", game.state.pending_decision.kind)
        self.assertFalse(any(item.ref == ref for item in game.state.stack))
        self.assertEqual(hand, [len(game.state.players[seat].zones["hand"]) for seat in "ABCD"])
        removed = [event.details for event in game.state.events if event.code == "stack.trigger.removed"
                   and event.details.get("stack") == ref]
        self.assertEqual([{"stack": ref, "reason": "no_modes_chosen"}], removed)
        self.assertEqual(37, game.state.players["B"].life)
        self.replay(game, load=True)

    def test_actual_single_mode_stays_selected_after_source_leaves(self):
        game = self.session(700260305)
        rankle = self.add(game.engine, ORIGINAL, zone="hand")
        response = self.add(game.engine, "Unsummon", seat="B", zone="hand")
        bear = self.add(game.engine, "Grizzly Bears", seat="C")
        self.add(game.engine, "Island", seat="B")
        self.ready(game, rankle, companions=(response,))
        ref = self.await_combat_modes(game, rankle)
        self.act(game, "A", {"action_id": "choose", "modes": ["mode_2"]})
        self.cast(game, "B", response, [rankle.ref])
        self.resolve_top(game)
        self.assertEqual("hand", game.state.cards[rankle.object_id].zone)
        self.assertEqual(ref, game.state.stack[-1].ref)
        self.assertEqual(["mode_2"], list(game.state.stack[-1].modes))
        game = self.replay(game, load=True)
        before = len(game.state.events)
        self.assertIsNone(self.resolve(game))
        self.assertEqual([39, 36, 39, 39], [game.state.players[seat].life for seat in "ABCD"])
        self.assertEqual("battlefield", game.state.cards[bear.object_id].zone)
        self.assertEqual(list("ABCD"), [event.actor for event in game.state.events[before:] if event.code == "card.draw"])
        self.replay(game, load=True)

    def test_actual_noncombat_player_and_combat_planeswalker_damage_offer_no_modes(self):
        for recipient_kind in ("player", "planeswalker"):
            with self.subTest(recipient_kind=recipient_kind):
                game = self.session(700260303 + (recipient_kind == "planeswalker"))
                rankle = self.add(game.engine, ORIGINAL, zone="hand")
                if recipient_kind == "player":
                    bow = self.add(game.engine, "Viridian Longbow")
                    self.ready(game, rankle, generic=5, companions=(bow,))
                    self.activate(game, bow, rankle.ref)
                    self.activate(game, rankle, "B")
                    self.assertEqual(39, game.state.players["B"].life)
                else:
                    walker = self.add(game.engine, "Jace Beleren", seat="B")
                    walker.counters["loyalty"] = 5
                    self.ready(game, rankle, companions=(walker,))
                    self.attack(game, [(rankle, walker.ref)])
                    self.until(game, lambda current: current.state.phase == "postcombat_main")
                    self.assertEqual(2, game.state.cards[walker.object_id].counters["loyalty"])
                self.assertEqual("priority", game.state.pending_decision.kind)
                self.assertEqual(0, self.trigger_count(game, rankle))
                self.replay(game, load=True)

    def test_complete_original_requires_untargeted_modes_and_exact_combat_authority(self):
        card = self.db.lookup(ORIGINAL, fuzzy=False)
        optional = OPTIONAL_COMBAT_TRIGGER_MODAL_CAPABILITY
        damage = "trigger.event.normalized_damage"
        for profile in self.registry.profiles:
            ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile=profile)
            self.assertEqual("exact", ir.status, ir.material_residuals)
            static, trigger = ir.faces[0].nodes
            self.assertEqual(("flying", "haste"), static.mechanics)
            self.assertEqual(card.oracle_text.split("\n", 1)[1], card.oracle_text[trigger.span.start:trigger.span.end])
            self.assertTrue({optional, damage}.issubset(trigger.capability_dependencies))
        report, semantics = register_card(self.db, self.registry, ORIGINAL)
        self.assertTrue(report["strict_capability_ready"], report["blockers"])
        program, = (row for row in semantics.programs_for_oracle(card.oracle_id) if optional in row.capability_dependencies)
        for missing in (optional, damage):
            dependencies = tuple(key for key in program.capability_dependencies if key != missing)
            changed = replace(program, coverage=[], capability_dependencies=dependencies,
                              capability_closure=self.registry.closure(dependencies, profile=PROFILE).to_dict())
            bound = bind_semantic_program_runtime(changed, capability_registry=self.registry, profile=PROFILE)
            self.assertFalse(bound["strict"])
            self.assertIn(f"capability:undeclared_runtime_dependency:{missing}", bound["blockers"])
        replaced = thaw_value(program.event_condition)
        replaced["all"].pop(1)
        for changed in (replace(program, event_condition=replaced), replace(program, event="permanent.enter.self")):
            self.assertFalse(bind_semantic_program_runtime(changed, capability_registry=self.registry, profile=PROFILE)["strict"])
        for text in (
            card.oracle_text.replace("combat damage", "damage"),
            card.oracle_text.replace("to a player", "to an opponent"),
            card.oracle_text.replace("Each player discards a card.", "Target player discards a card."),
            card.oracle_text.replace("choose any number", "choose any number. You may choose the same mode more than once."),
            card.oracle_text.split("\n", 2)[-1],
        ):
            changed = replace(card, oracle_text=text)
            self.assertNotEqual("exact", compile_oracle_card(changed, capability_registry=self.registry).status)
