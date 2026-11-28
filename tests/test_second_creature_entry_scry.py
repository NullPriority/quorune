from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from optional_result_followup_fixture import OptionalResultFollowupFixture
from quorune.ability_resolution_ordinal import ABILITY_RESOLVED_HISTORY, validate_second_ability_scry_replacement
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.replacement.immutable import FrozenMap, thaw_value
from quorune.rules.ability_resolution_ordinal_shapes import (
    OTHER_CONTROLLED_CREATURE_ENTRY, SECOND_ABILITY_SCRY_CAPABILITY, second_ability_scry_replacement_runtime_dependencies,
)
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


CARD = "Rumor Gatherer"
PROFILE = "commander_review"


class SecondCreatureEntryScryTests(InheritedNativeFixture, unittest.TestCase):
    finish_turn = OptionalResultFollowupFixture.finish_turn

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        folder = Path(cls.temporary.name)
        fixtures = ROOT / "tests" / "fixtures"
        support = {
            "second-creature-entry-scry-cards.json": {CARD},
            "second-ability-resolution-draw-cards.json": {"Expedition Envoy", "Hop to It"},
            "second-landfall-reveal-until-subtype-match-cards.json": {"Stifle", "Strionic Resonator", "Isamaru, Hound of Konda", "Plains", "Island"},
            "public-history-query-cards.json": {"Flicker of Fate"},
        }
        pieces = []
        for filename, names in support.items():
            document = json.loads((fixtures / filename).read_bytes())
            document["cards"] = [card for card in document["cards"] if card["name"] in names]
            assert {card["name"] for card in document["cards"]} == names, filename
            oracle_ids = {card["oracle_id"] for card in document["cards"]}
            document["rulings"] = [r for r in document.get("rulings", ()) if r["oracle_id"] in oracle_ids]
            piece = folder / filename
            piece.write_text(json.dumps(document), encoding="utf-8")
            pieces.append(piece)
        path = folder / "second-creature-entry-scry.sqlite3"
        build_fixture_database(pieces, path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("Second creature entry", [DeckEntry(commander, 1, "commander"), DeckEntry("Plains", 30)], [commander])

    def test_complete_trigger_and_both_branch_shapes_fail_closed(self):
        card = self.db.lookup(CARD, fuzzy=False)
        report, semantics = register_card(self.db, self.registry, CARD)
        self.assertTrue(report["strict_capability_ready"], report["blockers"])
        self.assertEqual("capability_closed", report["trust_basis"])
        program, = semantics.programs_for_oracle(card.oracle_id)
        self.assertEqual(("creature.enter", "battlefield", None, OTHER_CONTROLLED_CREATURE_ENTRY),
                         (program.event, program.active_zone, program.target_schema, FrozenMap(program.event_condition)))
        wrapper, = thaw_value(program.effects)
        draw, = validate_second_ability_scry_replacement(wrapper)
        self.assertEqual(("draw", 1, True), (draw["op"], draw["count"], draw["private"]))
        self.assertEqual([{"op": "scry", "player": "$controller", "count": 1}], wrapper["otherwise_effects"])
        required = {SECOND_ABILITY_SCRY_CAPABILITY, "condition.history.fixed_public_query", "library.scry.fixed_controller", "zone.draw.library_to_hand"}
        self.assertLessEqual(required, set(program.capability_dependencies))
        self.assertLessEqual(required, set(second_ability_scry_replacement_runtime_dependencies(program)))
        for field, value in (("resolution_ordinal", 2), ("otherwise_effects", [{"op": "scry", "player": "$controller", "count": True}]),
                             ("effects", [{**draw, "private": 1}]), ("prefix_mechanic_ids", ["scry"])):
            with self.subTest(field=field), self.assertRaises(ValueError):
                validate_second_ability_scry_replacement({**wrapper, field: value})
        for changes in ({"event": "land.enter"}, {"event_condition": None}, {"effects": (*program.effects, deepcopy(wrapper))}):
            with self.subTest(changed=tuple(changes)), self.assertRaises(ValueError):
                second_ability_scry_replacement_runtime_dependencies(replace(program, **changes))
        deps = tuple(cap for cap in program.capability_dependencies if cap != SECOND_ABILITY_SCRY_CAPABILITY)
        altered = replace(program, capability_dependencies=deps, capability_closure=self.registry.closure(deps, profile=PROFILE).to_dict())
        report = bind_semantic_program_runtime(altered, capability_registry=self.registry, profile=PROFILE)
        self.assertFalse(report["strict"], report)
        self.assertIn(f"capability:undeclared_runtime_dependency:{SECOND_ABILITY_SCRY_CAPABILITY}", report["blockers"])
        for old, new in (("another creature", "a creature"), ("second time", "third time"), ("scry 1", "scry 2"),
                         ("a card instead", "a card"), ("you control", "an opponent controls")):
            with self.subTest(oracle=new):
                changed = replace(card, oracle_text=card.oracle_text.replace(old, new))
                compiled = compile_oracle_card(changed, capability_registry=self.registry, capability_profile=PROFILE)
                self.assertNotEqual("exact", compiled.status)
        with patch("quorune.compiler.resolution_condition_templates.second_ability_scry_replacement_trigger_result", return_value=None):
            compiled = compile_oracle_card(card, capability_registry=self.registry, capability_profile=PROFILE)
            self.assertNotEqual("exact", compiled.status)

    def start(self, game, *cards):
        for card in cards:
            self.assert_closed(game, card)
        game.state.config.semantic_policy = "trusted_only"
        self.prepare(game, mana={"C": 8, "W": 8})
        self.checkpoint(game)

    def act_ok(self, game, seat, command):
        result = game.act("pilot:" + seat, command)
        self.assertTrue(result.ok, result.summary)

    def order(self, game):
        self.assertEqual("trigger.order", game.state.pending_decision.kind)
        refs = [item.ref for batch in game.state.pending_trigger_batches for item in batch.items]
        self.act_ok(game, "A", {"action_id": "order", "triggers": refs})

    def top(self, game):
        return game.state.cards[game.state.players["A"].zones["library"][-1]]

    def open_scry(self, game):
        top = self.top(game)
        decision = self.resolve(game)
        self.assertIsNotNone(decision)
        self.assertEqual(("semantic.choice", ["A"]), (decision.kind, decision.actors))
        choose, = (a for a in decision.payload_by_actor["A"]["legal_actions"] if a["id"] == "choose")
        self.assertEqual([top.ref], choose["choice_schema"]["legal_refs"])
        for seat in "BCD":
            packet = game.packet("pilot:" + seat, full=True)
            self.assertIsNone(packet["decision"])
            self.assertNotIn(top.ref, json.dumps(packet))
        return top

    def choose_scry(self, game, top, *, bottom=False):
        cards = {"top": [], "bottom": [top.ref]} if bottom else {"top": [top.ref], "bottom": []}
        self.act_ok(game, "A", {"action_id": "choose", "cards": cards})

    def observed(self, game, *, incarnation=None):
        return [e for e in game.state.turn_history.events if e.kind == ABILITY_RESOLVED_HISTORY
                and (incarnation is None or e.object_incarnation == incarnation)]

    def scries(self, game):
        return sum(e.code == "library.scry" for e in game.state.events)

    def test_actual_countered_and_queued_entries_do_not_consume_the_second_resolution(self):
        game = self.session(1564001)
        source = self.add(game.engine, CARD, zone="hand")
        rabbits = self.add(game.engine, "Hop to It", zone="hand")
        next_entrant = self.add(game.engine, "Expedition Envoy", zone="hand")
        stifle = self.add(game.engine, "Stifle", seat="B", zone="hand")
        their_blink = self.add(game.engine, "Flicker of Fate", seat="B", zone="hand")
        their_creature = self.add(game.engine, "Expedition Envoy", seat="B", ref="opponent-envoy")
        self.add(game.engine, "Island", seat="B")
        self.add(game.engine, "Island", seat="B", ref="counter-seat-extra-island")
        self.add(game.engine, "Plains", seat="B")
        self.add(game.engine, "Plains", seat="B", ref="counter-seat-extra-plains")
        self.start(game, source, rabbits, next_entrant, stifle, their_blink)
        self.cast(game, "A", source)
        self.resolve_top(game)
        self.assertFalse(game.state.stack)
        self.cast(game, "B", their_blink, [their_creature.ref])
        self.resolve_top(game)
        self.assertFalse(game.state.stack)
        self.cast(game, "A", rabbits)
        self.resolve_top(game)
        self.order(game)
        self.assertEqual(3, len(game.state.stack))
        self.assertEqual([], self.observed(game))
        self.cast(game, "B", stifle, [game.state.stack[-1].ref])
        self.resolve_top(game)
        self.assertEqual(2, len(game.state.stack))
        self.assertEqual([], self.observed(game))

        top = self.open_scry(game)
        self.assertEqual(1, len(self.observed(game)))
        game = self.replay(game, load=True)
        before = authoritative_state_hash(game.state)
        for seat, top_refs in (("B", [top.ref]), ("A", [top.ref, top.ref])):
            rejected = game.act("pilot:" + seat, {"action_id": "choose", "cards": {"top": top_refs, "bottom": []}})
            self.assertFalse(rejected.ok, rejected.summary)
            self.assertEqual(before, authoritative_state_hash(game.state))
        self.choose_scry(game, top, bottom=True)
        self.assertEqual(top.object_id, game.state.players["A"].zones["library"][0])
        drawn = self.top(game)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("hand", game.state.cards[drawn.object_id].zone)
        self.assertEqual(1, self.scries(game))
        self.assertEqual(2, len(self.observed(game)))
        for seat in "BCD":
            self.assertNotIn(drawn.ref, json.dumps(game.packet("pilot:" + seat, full=True)))

        self.cast(game, "A", next_entrant)
        self.resolve_top(game)
        self.choose_scry(game, self.open_scry(game))
        self.assertEqual((3, 2), (len(self.observed(game)), self.scries(game)))
        self.finish_turn(game)
        self.assertEqual([], self.observed(game))
        self.replay(game, load=True)

    def test_actual_trigger_copy_shares_ordinal_with_its_source_only(self):
        game = self.session(1564002)
        first = self.add(game.engine, CARD)
        second = self.add(game.engine, CARD, ref="separate-gatherer")
        resonator = self.add(game.engine, "Strionic Resonator")
        entrant = self.add(game.engine, "Expedition Envoy", zone="hand")
        self.start(game, first, second, resonator, entrant)
        self.cast(game, "A", entrant)
        self.resolve_top(game)
        self.order(game)
        original, = [item for item in game.state.stack if item.source_object_id == first.object_id]
        self.priority(game, "A")
        actions = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        offer, = [a for a in actions if a["id"].startswith(f"activate:{resonator.ref}:")]
        self.act_ok(game, "A", {"action_id": offer["id"], "targets": [original.ref], "pay": "auto"})
        self.resolve_top(game)
        self.assertEqual(3, len(game.state.stack))
        self.assertEqual([], self.observed(game))
        self.assertEqual(original.semantic_key, game.state.stack[-1].semantic_key)
        self.assertEqual(original.context["source_logical_object_id"], game.state.stack[-1].context["source_logical_object_id"])
        self.choose_scry(game, self.open_scry(game))
        hand = len(game.state.players["A"].zones["hand"])
        while game.state.stack:
            item = game.state.stack[-1]
            if item.source_object_id == first.object_id:
                self.assertIsNone(self.resolve(game))
            else:
                self.choose_scry(game, self.open_scry(game), bottom=True)
        self.assertEqual(hand + 1, len(game.state.players["A"].zones["hand"]))
        self.assertEqual(2, self.scries(game))
        self.assertEqual(2, len(self.observed(game, incarnation=first.logical_object_id)))
        self.assertEqual(1, len(self.observed(game, incarnation=second.logical_object_id)))
        self.assertEqual({original.semantic_key}, {e.target for e in self.observed(game, incarnation=first.logical_object_id)})
        self.replay(game, load=True)

    def test_actual_new_incarnation_has_separate_ordinal_from_its_pending_old_trigger(self):
        game = self.session(1564003)
        source = self.add(game.engine, CARD)
        entrants = [self.add(game.engine, "Expedition Envoy", zone="hand", ref=f"gatherer-entrant-{i}") for i in range(3)]
        flicker = self.add(game.engine, "Flicker of Fate", zone="hand")
        self.start(game, source, *entrants, flicker)
        old = source.logical_object_id
        self.cast(game, "A", entrants[0])
        self.resolve_top(game)
        self.choose_scry(game, self.open_scry(game))
        self.cast(game, "A", entrants[1])
        self.resolve_top(game)
        queued, = game.state.stack
        self.cast(game, "A", flicker, [source.ref])
        self.resolve_top(game)
        self.assertEqual([queued.ref], [item.ref for item in game.state.stack])
        current = game.state.cards[source.object_id].logical_object_id
        self.assertNotEqual(old, current)
        self.assertEqual(old, game.state.stack[-1].context["source_logical_object_id"])
        drawn = self.top(game)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("hand", game.state.cards[drawn.object_id].zone)
        self.assertEqual(1, self.scries(game))
        self.cast(game, "A", entrants[2])
        self.resolve_top(game)
        self.choose_scry(game, self.open_scry(game))
        self.assertEqual(2, len(self.observed(game, incarnation=old)))
        self.assertEqual(1, len(self.observed(game, incarnation=current)))
        self.replay(game, load=True)
