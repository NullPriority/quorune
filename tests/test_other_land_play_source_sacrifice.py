"""A controller's other land play sacrifices only the observing incarnation."""

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.event_subscriptions import (
    LAND_PLAY_EVENT,
    OTHER_LAND_PLAY_SACRIFICE_CAPABILITY,
    OTHER_LAND_PLAY_SACRIFICE_TEMPLATE,
    PLAYED_LAND_REFERENCE_FIELD,
)
from quorune.source_sacrifice_model import fixed_source_sacrifice_effect
from scripts.build_test_database import build_fixture_database


class OtherLandPlaySourceSacrificeTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "other-land-play.sqlite3"
        build_fixture_database([
            ROOT / "tests/fixtures/actual-card-play-and-fixed-cleanup-limit-cards.json",
            ROOT / "tests/fixtures/bound-effect-program-cards.json",
            ROOT / "tests/fixtures/city-of-traitors-cards.json",
            ROOT / "tests/fixtures/entry-copy-cards.json",
            ROOT / "tests/fixtures/mana-tapped-land-next-untap-cards.json",
            ROOT / "tests/fixtures/resolving-top-play-cards.json",
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = "Generic Bound Commander"
        cls.deck = DeckDefinition("Other land play", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Generic Bound Plains", 24),
        ], [commander])

    def compile(self, record):
        return compile_oracle_card(record, capability_registry=self.registry, capability_profile="commander_review")

    def play(self, game, seat, land):
        self.priority(game, seat)
        action_id = f"play-land:{land.ref}"
        actions = game.packet(f"pilot:{seat}", full=True)["decision"]["ctx"]["legal"]["actions"]
        self.assertIn(action_id, {option["id"] for option in actions})
        result = game.act(f"pilot:{seat}", {"action_id": action_id})
        self.assertTrue(result.ok, result.summary)

    def source_triggers(self, game, source):
        return [item for item in game.state.stack if item.kind == "triggered_ability"
                and item.source_object_id == source.object_id]

    def assert_no_self_play_trigger(self, game, city):
        self.assertFalse(self.source_triggers(game, city), "The original land play cannot trigger its own ability")

    def test_compiler_closes_the_other_land_shape_and_retains_fixed_colorless_mana(self):
        record = self.db.lookup("City of Traitors", fuzzy=False)
        compiled = self.compile(record)
        self.assertEqual("exact", compiled.status, compiled.material_residuals)
        trigger, mana = compiled.faces[0].nodes
        self.assertEqual((LAND_PLAY_EVENT, OTHER_LAND_PLAY_SACRIFICE_TEMPLATE), (trigger.event, trigger.template_id))
        self.assertIn(OTHER_LAND_PLAY_SACRIFICE_CAPABILITY, trigger.capability_dependencies)
        self.assertEqual((fixed_source_sacrifice_effect(),), trigger.effects)
        self.assertEqual({"all": [
            {"field": "player", "op": "eq", "value": "$source.controller"},
            {"field": "physical_card", "op": "eq", "value": True},
            {"field": PLAYED_LAND_REFERENCE_FIELD, "op": "truthy", "value": True},
            {"field": PLAYED_LAND_REFERENCE_FIELD, "op": "ne", "value": "$source.ref"},
        ]}, trigger.event_condition)
        self.assertEqual(0, trigger.span.start)
        self.assertEqual(trigger.text, record.oracle_text[trigger.span.start:trigger.span.end])
        self.assertEqual("mana_ability", mana.kind)
        self.assertTrue(mana.exact)
        ability = mana.handlers[0]["ability"]
        self.assertTrue(ability["tap_source"])
        bundle = ability["modes"][0]["bundle"]
        self.assertEqual({"C": 2}, {color: count for color, count in bundle.items() if count})

        for text in (
            "When an opponent plays another land, sacrifice this land.",
            "When you play another land, sacrifice a land.",
            "When you play another land, sacrifice this land. Draw a card.",
            "When another land enters under your control, sacrifice this land.",
        ):
            with self.subTest(text=text):
                probe = self.compile(replace(record, oracle_text=text))
                self.assertFalse(any(OTHER_LAND_PLAY_SACRIFICE_CAPABILITY in node.capability_dependencies
                                     for face in probe.faces for node in face.nodes))

    def test_runtime_binding_rejects_changed_actor_identity_event_or_result(self):
        game = self.session(305_701_180)
        city = self.add(game.engine, "City of Traitors")
        self.assert_closed(game, city)
        program, = (item for item in game.engine.semantics.programs_for_oracle(city.oracle_id)
                    if item.event == LAND_PLAY_EVENT)
        condition = program.event_condition["all"]
        remaining = [dependency for dependency in program.capability_dependencies
                     if dependency != OTHER_LAND_PLAY_SACRIFICE_CAPABILITY]
        for changed in (
            {"event": "land.enter"},
            {"event_condition": {"all": condition[1:]}},
            {"event_condition": {"all": condition[:-1]}},
            {"effects": [{"op": "draw", "player": "$controller", "count": 1, "private": True}]},
            {"capability_dependencies": remaining,
             "capability_closure": self.registry.closure(remaining, profile="commander_review").to_dict()},
        ):
            with self.subTest(changed=changed):
                binding = bind_semantic_program_runtime(replace(program, **changed),
                                                         capability_registry=self.registry, profile="commander_review")
                self.assertFalse(binding["strict"], binding)

    def test_city_ignores_itself_and_retains_two_colorless_mana_before_sacrifice(self):
        game = self.session(305_701_181)
        city = self.add(game.engine, "City of Traitors", zone="hand")
        other = self.add(game.engine, "Generic Bound Plains", zone="hand", ref="city-other-played-land")
        exploration = self.add(game.engine, "Exploration")
        for card in (city, exploration):
            self.assert_closed(game, card)
        self.prepare(game)
        self.checkpoint(game)
        self.play(game, "A", city)
        self.assert_no_self_play_trigger(game, city)
        self.assertFalse(game.state.stack)
        self.play(game, "A", other)
        trigger, = self.source_triggers(game, city)
        self.assertEqual([trigger.ref], [item.ref for item in game.state.stack])
        self.assertEqual("battlefield", game.state.cards[city.object_id].zone)
        self.assertEqual((LAND_PLAY_EVENT, "A", "hand", True),
                         tuple(trigger.context[key] for key in ("event", "player", "from", "physical_card")))
        self.assertNotIn(PLAYED_LAND_REFERENCE_FIELD, trigger.context)
        game = self.replay(game, load=True)

        self.priority(game, "A")
        actions = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        mana, = (row for row in actions if row["id"].startswith(f"activate:{city.ref}:"))
        result = game.act("pilot:A", {"action_id": mana["id"]})
        self.assertTrue(result.ok, result.summary)
        self.assertTrue(game.state.cards[city.object_id].tapped)
        self.assertEqual(2, game.state.players["A"].mana_pool["C"])
        self.assertEqual(2, sum(game.state.players["A"].mana_pool.values()))
        self.assertEqual([trigger.ref], [item.ref for item in game.state.stack])
        self.assertIsNone(self.resolve(game))
        self.assertEqual(("graveyard", "battlefield"), tuple(game.state.cards[card.object_id].zone for card in (city, other)))
        self.assertEqual(2, game.state.players["A"].mana_pool["C"])
        self.replay(game, load=True)

    def test_city_ignores_other_seat_plays_and_land_put_onto_battlefield(self):
        game = self.session(305_701_182)
        city = self.add(game.engine, "City of Traitors")
        rival = self.add(game.engine, "Generic Bound Plains", seat="B", zone="hand", ref="city-rival-play")
        placed = self.add(game.engine, "Generic Bound Plains", zone="hand", ref="city-placed-land")
        spiral = self.add(game.engine, "Growth Spiral", zone="hand")
        for card in (city, spiral):
            self.assert_closed(game, card)
        game.state.players["A"].mana_pool.update({"G": 1, "U": 1})
        self.prepare(game, actor="B", active="B")
        self.checkpoint(game)
        self.play(game, "B", rival)
        self.assertFalse(self.source_triggers(game, city))
        self.cast_for(game, "A", spiral)
        self.assertEqual("semantic.choice", self.resolve(game).kind)
        result = game.act("pilot:A", {"action_id": "choose", "card": placed.ref})
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("battlefield", game.state.cards[placed.object_id].zone)
        self.assertEqual("battlefield", game.state.cards[city.object_id].zone)
        self.assertFalse(self.source_triggers(game, city))
        plays = [event.details["object"] for event in game.state.events if event.code == "land.play"]
        self.assertIn(rival.ref, plays)
        self.assertNotIn(placed.ref, plays)
        self.replay(game, load=True)

    def test_copied_city_ability_uses_its_own_physical_source(self):
        game = self.session(305_701_183)
        original = self.add(game.engine, "City of Traitors", seat="B")
        copy = self.add(game.engine, "Vesuva", zone="hand")
        copy_oracle = copy.oracle_id
        other = self.add(game.engine, "Generic Bound Plains", zone="hand", ref="city-copy-next-land")
        exploration = self.add(game.engine, "Exploration")
        for card in (original, copy, exploration):
            self.assert_closed(game, card)
        self.prepare(game)
        self.checkpoint(game)
        self.play(game, "A", copy)
        self.assertEqual("replacement.order", game.state.pending_decision.kind)
        options = game.packet("pilot:A", full=True)["decision"]["ctx"]["options"]
        candidates = [row for row in options if not row.get("decline") and original.ref in row["id"]]
        self.assertEqual(1, len(candidates), options)
        option, = candidates
        result = game.act("pilot:A", {"action_id": "choose", "replacement": option["id"]})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("City of Traitors", game.engine.display_name(copy.object_id))
        self.assertEqual(copy_oracle, game.state.cards[copy.object_id].oracle_id)
        self.assert_no_self_play_trigger(game, copy)
        self.assertFalse(self.source_triggers(game, original))

        self.play(game, "A", other)
        trigger, = self.source_triggers(game, copy)
        self.assertEqual("A", trigger.controller)
        self.assertFalse(self.source_triggers(game, original))
        self.assertIsNone(self.resolve(game))
        self.assertEqual("graveyard", game.state.cards[copy.object_id].zone)
        self.assertEqual("battlefield", game.state.cards[original.object_id].zone)
        self.replay(game, load=True)

    def test_departure_and_return_cannot_sacrifice_the_new_incarnation(self):
        game = self.session(305_701_184)
        city = self.add(game.engine, "City of Traitors")
        other = self.add(game.engine, "Generic Bound Plains", zone="hand", ref="city-flicker-other")
        flicker = self.add(game.engine, "Ghostly Flicker", zone="hand")
        for card in (city, flicker):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 2, "U": 1})
        self.checkpoint(game)
        self.play(game, "A", other)
        original, = self.source_triggers(game, city)
        old_logical = game.state.cards[city.object_id].logical_object_id
        self.cast_for(game, "A", flicker, [city.ref, other.ref])
        self.resolve_top(game)
        resumed = game.state.cards[city.object_id]
        self.assertEqual("battlefield", resumed.zone)
        self.assertNotEqual(old_logical, resumed.logical_object_id)
        self.assertEqual([original.ref], [item.ref for item in self.source_triggers(game, city)])
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual((resumed.logical_object_id, "battlefield"),
                         (game.state.cards[city.object_id].logical_object_id, game.state.cards[city.object_id].zone))
        self.replay(game, load=True)

    def test_native_self_play_witness_detects_missing_other_land_comparison(self):
        from quorune import trigger_discovery

        game = self.session(305_701_185)
        city = self.add(game.engine, "City of Traitors", zone="hand")
        self.assert_closed(game, city)
        self.prepare(game)
        self.checkpoint(game)
        real_matcher = trigger_discovery.semantic_event_matches

        def omit_reference_comparison(host, program, source, event, context, *, source_zone=None):
            if program.provenance.get("template_id") == OTHER_LAND_PLAY_SACRIFICE_TEMPLATE:
                condition = program.event_condition["all"]
                program = replace(program, event_condition={"all": condition[:-1]})
            return real_matcher(host, program, source, event, context, source_zone=source_zone)

        with patch.object(trigger_discovery, "semantic_event_matches", omit_reference_comparison):
            self.play(game, "A", city)
            with self.assertRaisesRegex(AssertionError, "The original land play cannot trigger its own ability"):
                self.assert_no_self_play_trigger(game, city)
