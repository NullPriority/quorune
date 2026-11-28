"""Native evidence for a controlled successful spell counter and optional loot."""

from dataclasses import replace
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules import controlled_spell_counter as rules
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database

from test_qualified_zone_event_queries import query_record


BARAL = "Baral, Chief of Compliance"
TRIGGER = "Whenever a spell or ability you control counters a spell, you may draw a card. If you do, discard a card."
COST = "Instant and sorcery spells you cast cost {1} less to cast."
COUNTER_ACTIVATION = "Generic Counter Observer Activation"


class ControlledSpellCounterCompilerTests(unittest.TestCase):
    def test_complete_source_preserves_cost_and_requires_exact_standalone_optional_loot(self):
        registry = load_default_capability_registry()
        ir = compile_oracle_card(query_record(COST + "\n" + TRIGGER), capability_registry=registry)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        cost, loot = ir.faces[0].nodes
        self.assertEqual("cast.cost.modify", cost.event)
        self.assertEqual((len(COST) + 1, len(COST) + 1 + len(TRIGGER)), (loot.span.start, loot.span.end))
        self.assertEqual("spell.countered_by_effect", loot.event)
        self.assertEqual({"field": "counter_source_controller", "op": "eq", "value": "$source.controller"}, loot.event_condition)
        self.assertIn(rules.CONTROLLED_SPELL_COUNTER_CAPABILITY, loot.capability_dependencies)
        self.assertEqual(["draw", "choose_cards_apnap"], [effect["op"] for effect in loot.effects[0]["effects"]])
        base = dict(event=loot.event, active_zone=loot.active_zone, effects=loot.effects, handlers=loot.handlers,
                    event_condition=loot.event_condition, target_schema=None, cost_schema=None,
                    trigger_limit=None, coverage=loot.runtime_coverage)
        self.assertEqual((rules.CONTROLLED_SPELL_COUNTER_CAPABILITY,),
                         rules.controlled_spell_counter_runtime_dependencies(SimpleNamespace(**base), None))
        for forged in ({"active_zone": "graveyard"}, {"event_condition": None}, {"effects": ()},
                       {"effects": ({"op": "offer_optional_effect", "player": "$controller", "effects": ()},)},
                       {"coverage": (*loot.runtime_coverage, "intervening_condition")}, {"trigger_limit": {"schema_version": 1}}):
            with self.subTest(forged=forged), self.assertRaises(ValueError):
                rules.controlled_spell_counter_runtime_dependencies(SimpleNamespace(**(base | forged)), None)
        for text in (TRIGGER.replace("counters a spell", "counters a spell or ability"),
                     TRIGGER.replace("you control", "an opponent controls"),
                     TRIGGER.replace("discard a card.", "discard a card at random."),
                     TRIGGER.replace("draw a card.", "draw two cards."),
                     TRIGGER + " This ability triggers only once each turn."):
            with self.subTest(text=text):
                self.assertNotEqual("exact", compile_oracle_card(query_record(text), capability_registry=registry).status)


class ControlledSpellCounterNativeTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / "controlled-spell-counter.sqlite3"
        fixtures = ROOT / "tests/fixtures"
        build_fixture_database([fixtures / name for name in (
            "bound-effect-program-cards.json", "optional-draw-discard-sequences.json",
            "targeted-counter-cards.json", "controlled-spell-counter-cards.json",
        )], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = "Generic Bound Commander"
        cls.deck = DeckDefinition("Controlled spell counter", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], [commander])

    def assert_act(self, game, seat, action_id, **fields):
        result = game.act("pilot:" + seat, {"action_id": action_id, **fields})
        self.assertTrue(result.ok, result.summary)

    def spell(self, game, card):
        item, = (item for item in game.state.stack if item.card_object_id == card.object_id)
        return item

    def assert_baral_trigger(self, game, baral, countered, counter_source):
        trigger = game.state.stack[-1]
        self.assertEqual("triggered_ability", trigger.kind)
        self.assertEqual("A", trigger.controller)
        self.assertEqual(baral.object_id, trigger.source_object_id)
        self.assertEqual(countered.ref, trigger.context["stack"])
        self.assertEqual(counter_source.ref, trigger.context["counter_source"])
        self.assertEqual("A", trigger.context["counter_source_controller"])
        self.assertNotIn(countered.ref, [item.ref for item in game.state.stack])

    def assert_no_baral_trigger(self, game, baral):
        self.assertFalse(any(item.source_object_id == baral.object_id for item in game.state.stack))
        self.assertFalse(any(event.code == "stack.trigger" and event.details.get("source") == baral.ref
                             for event in game.state.events))

    def prepare_non_top_counter(self, seed):
        game = self.session(seed)
        baral = self.add(game.engine, BARAL, seat="A")
        baral.owner = "D"
        body = self.add(game.engine, "Generic Bound Body", seat="C")
        bottom = self.add(game.engine, "Unsummon", zone="hand", seat="B", ref="bottom-bounce")
        top = self.add(game.engine, "Unanswerable Test Spell", zone="hand", seat="B")
        cancel = self.add(game.engine, "Cancel", zone="hand", seat="A")
        departure = self.add(game.engine, "Unsummon", zone="hand", seat="A", ref="source-departure")
        self.assert_closed(game, baral)
        self.prepare(game, active="B", actor="B", mana={"U": 2})
        game.state.players["A"].mana_pool.update({"U": 3})
        self.checkpoint(game)
        self.cast(game, "B", bottom, [body.ref])
        bottom_item = self.spell(game, bottom)
        self.cast(game, "B", top)
        top_item = self.spell(game, top)
        self.assertEqual([bottom_item.ref, top_item.ref], [item.ref for item in game.state.stack])
        self.priority(game, "A")
        action = self.action(game, "A", cancel)
        self.assertIn(bottom_item.ref, action["target_schema"]["legal_refs"])
        self.assert_act(game, "A", action["id"], targets=[bottom_item.ref], pay="auto")
        self.assertEqual(1, game.state.players["A"].mana_pool["U"])
        self.assert_no_baral_trigger(game, baral)
        return game, baral, bottom_item, top_item, self.spell(game, cancel), departure

    def test_actual_non_top_counter_and_source_departure_preserve_private_draw_discard_and_replay(self):
        game, baral, bottom, top, cancel, departure = self.prepare_non_top_counter(77094001)
        self.resolve_top(game)
        self.assert_baral_trigger(game, baral, bottom, cancel)
        self.assertEqual(top.ref, game.state.stack[-2].ref)
        counter, = (event for event in game.state.events
                    if event.code == "stack.counter" and event.details["stack"] == bottom.ref)
        self.assertEqual({"stack", "destination", "reason", "counter_kind"}, set(counter.details))
        self.assertEqual("effect", counter.details["counter_kind"])
        self.cast(game, "A", departure, [baral.ref])
        self.resolve_top(game)
        self.assertEqual("hand", game.state.cards[baral.object_id].zone)
        self.assertIn(baral.object_id, game.state.players["D"].zones["hand"])
        self.assert_baral_trigger(game, baral, bottom, cancel)
        self.assertEqual("semantic.choice", self.resolve(game).kind)
        prior_hand = set(game.state.players["A"].zones["hand"])
        self.assert_act(game, "A", "choose", choice="apply")
        self.assertEqual("choice.apnap", game.state.pending_decision.kind)
        drawn, = set(game.state.players["A"].zones["hand"]) - prior_hand
        drawn_ref = game.state.cards[drawn].ref
        self.assertIn(drawn_ref, json.dumps(game.packet("pilot:A", full=True)))
        for seat in "BCD":
            self.assertNotIn(drawn_ref, json.dumps(game.packet("pilot:" + seat, full=True)))
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:B", {"action_id": "choose", "cards": [drawn_ref]}).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        game = self.replay(game, load=True)
        self.assert_act(game, "A", "choose", cards=[drawn_ref])
        self.assertIsNone(self.resolve(game))
        self.assertEqual(prior_hand, set(game.state.players["A"].zones["hand"]))
        self.assertEqual("graveyard", game.state.cards[drawn].zone)
        self.replay(game)

    def activate_counter(self, game, seat, source, target):
        self.priority(game, seat)
        actions = game.packet("pilot:" + seat, full=True)["decision"]["ctx"]["legal"]["actions"]
        action, = (action for action in actions if action["id"].startswith("activate:" + source.ref + ":"))
        self.assertIn(target.ref, action["target_schema"]["legal_refs"])
        self.assert_act(game, seat, action["id"], targets=[target.ref], pay="auto")
        return game.state.stack[-1]

    def test_actual_activated_counter_stops_spell_copy_and_decline_changes_no_hidden_zone(self):
        game = self.session(77094002)
        baral = self.add(game.engine, BARAL)
        activated_source = self.add(game.engine, COUNTER_ACTIVATION, seat="A")
        body = self.add(game.engine, "Generic Bound Body", zone="hand", seat="B")
        duplicate = self.add(game.engine, "Double Major", zone="hand", seat="B")
        self.prepare(game, active="B", actor="B", mana={"W": 1, "G": 1, "U": 1})
        game.state.players["A"].mana_pool.update({"U": 1})
        self.checkpoint(game)
        self.cast(game, "B", body)
        original = self.spell(game, body)
        self.cast(game, "B", duplicate, [original.ref])
        self.resolve_top(game)
        copied = game.state.stack[-1]
        self.assertEqual("spell_copy", copied.kind)
        ability = self.activate_counter(game, "A", activated_source, copied)
        self.assertEqual("activated_ability", ability.kind)
        self.resolve_top(game)
        self.assert_baral_trigger(game, baral, copied, ability)
        self.assertEqual(original.ref, game.state.stack[-2].ref)
        self.assertEqual("semantic.choice", self.resolve(game).kind)
        prior = {zone: list(game.state.players["A"].zones[zone]) for zone in ("hand", "library")}
        self.assert_act(game, "A", "choose", choice="decline")
        self.assertIsNone(self.resolve(game))
        self.assertEqual(prior, {zone: game.state.players["A"].zones[zone] for zone in prior})
        self.assertEqual("battlefield", game.state.cards[body.object_id].zone)
        self.replay(game)

    def test_actual_opponent_counter_and_countered_ability_do_not_trigger(self):
        game = self.session(77094003)
        baral = self.add(game.engine, BARAL)
        body = self.add(game.engine, "Generic Bound Body", seat="C")
        bounced = self.add(game.engine, "Unsummon", zone="hand", seat="A")
        activated_source = self.add(game.engine, COUNTER_ACTIVATION, seat="B")
        stifle = self.add(game.engine, "Stifle", zone="hand", seat="A")
        opposing_counter = self.add(game.engine, "Counterspell", zone="hand", seat="B")
        self.prepare(game, mana={"U": 2})
        game.state.players["B"].mana_pool.update({"U": 3})
        self.checkpoint(game)
        self.cast(game, "A", bounced, [body.ref])
        original = self.spell(game, bounced)
        ability = self.activate_counter(game, "B", activated_source, original)
        self.cast(game, "A", stifle, [ability.ref])
        self.resolve_top(game)
        self.assertEqual([original.ref], [item.ref for item in game.state.stack])
        self.assert_no_baral_trigger(game, baral)
        self.cast(game, "B", opposing_counter, [original.ref])
        self.assertIsNone(self.resolve(game))
        self.assertEqual("graveyard", game.state.cards[bounced.object_id].zone)
        self.assertEqual("battlefield", game.state.cards[body.object_id].zone)
        self.assert_no_baral_trigger(game, baral)
        self.replay(game)

    def test_actual_uncounterable_attempt_and_target_fizzle_do_not_trigger(self):
        game = self.session(77094004)
        baral = self.add(game.engine, BARAL)
        body = self.add(game.engine, "Generic Bound Body", seat="C")
        safe = self.add(game.engine, "Unanswerable Test Spell", zone="hand", seat="B")
        counter = self.add(game.engine, "Counterspell", zone="hand", seat="A")
        hostile = self.add(game.engine, "Unsummon", zone="hand", seat="B", ref="illegal-target-spell")
        remove = self.add(game.engine, "Unsummon", zone="hand", seat="A", ref="leave-before-resolution")
        self.prepare(game, active="B", actor="B", mana={"U": 2})
        game.state.players["A"].mana_pool.update({"U": 3})
        self.checkpoint(game)
        self.cast(game, "B", safe)
        safe_item = self.spell(game, safe)
        self.cast(game, "A", counter, [safe_item.ref])
        self.resolve_top(game)
        self.assertEqual([safe_item.ref], [item.ref for item in game.state.stack])
        self.assertTrue(any(event.code == "stack.counter.failed" and event.details["stack"] == safe_item.ref
                            for event in game.state.events))
        self.assert_no_baral_trigger(game, baral)
        self.assertIsNone(self.resolve(game))
        self.cast(game, "B", hostile, [body.ref])
        hostile_item = self.spell(game, hostile)
        self.cast(game, "A", remove, [body.ref])
        self.assertIsNone(self.resolve(game))
        self.assertEqual("hand", game.state.cards[body.object_id].zone)
        self.assertTrue(any(event.code == "stack.counter" and event.details["stack"] == hostile_item.ref
                            and event.details["counter_kind"] == "rules" for event in game.state.events))
        self.assert_no_baral_trigger(game, baral)
        self.replay(game)

    def test_actual_counter_source_controller_mutation_loses_the_trigger(self):
        game, baral, bottom, top, cancel, _ = self.prepare_non_top_counter(77094005)
        original = rules.prepare_successful_spell_counter

        def use_countered_spell_controller(stack, subject, *, countered_by):
            occurrence = original(stack, subject, countered_by=countered_by)
            return replace(occurrence, counter_source_controller=subject.controller) if occurrence else None

        with patch.object(rules, "prepare_successful_spell_counter", side_effect=use_countered_spell_controller):
            self.resolve_top(game)
        self.assertEqual([top.ref], [item.ref for item in game.state.stack])
        with self.assertRaises(AssertionError):
            self.assert_baral_trigger(game, baral, bottom, cancel)
