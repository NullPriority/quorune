"""A real card play draws once, and finite cleanup rules use timestamps."""

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune import maximum_hand_size
from quorune.cast_event_type_model import CARD_PLAY_EVENT_FACT_VERSION_FIELD, physical_card_event_context
from quorune.maximum_hand_size import FixedHandSizeSettingSpec, effective_maximum_hand_size
from quorune.model import GameState
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.graveyard_copy_cast_model import GRAVEYARD_COPY_OFFER
from quorune.rules.spell_cast_events import SpellCastEvent, SpellCastEventError
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


RECYCLE = "Recycle"
NULL_PROFUSION = "Null Profusion"
PROFILE = "commander_review"
PLAY_TEMPLATE = "fixed-physical-card-play-trigger-v1"
LIMIT_TEMPLATE = "fixed-maximum-hand-size-setting-v1"


class ActualCardPlayAndFixedCleanupLimitTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "actual-card-play-and-cleanup.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/actual-card-play-and-fixed-cleanup-limit-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Physical cards and current cleanup", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def programs(self, name):
        card = self.db.lookup(name, fuzzy=False)
        binding, semantics = register_card(self.db, self.registry, name)
        self.assertTrue(binding["strict_capability_ready"], binding["blockers"])
        by_template = {item.provenance["template_id"]: item for item in semantics.programs_for_oracle(card.oracle_id)}
        return card, by_template

    def test_both_complete_originals_keep_draw_skip_and_the_closed_physical_play_trigger(self):
        for name in (RECYCLE, NULL_PROFUSION):
            with self.subTest(card=name):
                card, programs = self.programs(name)
                self.assertEqual({PLAY_TEMPLATE, LIMIT_TEMPLATE, "static-turn-step-skip-v1"}, set(programs))
                trigger = programs[PLAY_TEMPLATE]
                land, cast = trigger.event_condition["event_subscriptions"]
                self.assertEqual(("land.played", "spell.cast"), (land["event"], cast["event"]))
                physical = {"field": "physical_card", "op": "eq", "value": True}
                self.assertIn(physical, land["condition"]["all"])
                self.assertIn(physical, cast["condition"]["all"])
                self.assertIsNone(trigger.target_schema)
                self.assertEqual(("draw",), tuple(item["op"] for item in trigger.effects))
                original = compile_oracle_card(card, capability_registry=self.registry)
                self.assertEqual("exact", original.status)
                for text in (
                    card.oracle_text.replace("Whenever you play a card", "Whenever an opponent plays a card"),
                    card.oracle_text.replace("Whenever you play a card", "Whenever you play or copy a card"),
                    card.oracle_text.replace("hand size is two", "hand size is X"),
                ):
                    self.assertNotEqual("exact", compile_oracle_card(
                        replace(card, oracle_text=text), capability_registry=self.registry).status)

    def test_raw_play_subscriptions_require_the_physical_card_guard_on_both_events(self):
        _, programs = self.programs(RECYCLE)
        trigger = programs[PLAY_TEMPLATE]
        guard = {"field": "physical_card", "op": "eq", "value": True}
        for index in range(2):
            subscriptions = [dict(value) for value in trigger.event_condition["event_subscriptions"]]
            subscription = subscriptions[index]
            actor, = [clause for clause in subscription["condition"]["all"] if clause != guard]
            subscription["condition"] = actor
            bound = bind_semantic_program_runtime(
                replace(trigger, event_condition={"event_subscriptions": subscriptions}),
                capability_registry=self.registry, profile=PROFILE,
            )
            self.assertFalse(bound["strict"], bound)


    def test_card_play_fact_and_cleanup_setting_are_strict_and_preserve_historical_envelopes(self):
        game = self.session(70118023)
        card = self.add(game.engine, "Forest", zone="hand")
        raw = game.state.to_dict()
        self.assertEqual(1, raw.pop(CARD_PLAY_EVENT_FACT_VERSION_FIELD))
        historical = GameState.from_dict(raw)
        self.assertEqual(raw, historical.to_dict())
        self.assertEqual({}, physical_card_event_context(historical, card))
        self.assertEqual({"physical_card": True}, physical_card_event_context(game.state, card))
        for malformed in (True, 0, 2, None):
            with self.assertRaises(ValueError):
                GameState.from_dict({**raw, CARD_PLAY_EVENT_FACT_VERSION_FIELD: malformed})

        v7 = SpellCastEvent(
            card_ref="C1", object_id="obj1", logical_object_id="inc1", controller="A",
            origin="hand", stack_ref="S1", types=("instant",), mana_value=1,
            owner="A", active_player="A", caster_spell_number=1, kicked=False,
            has_x_cost=False, has_adventure=False, keywords=(), phase="precombat_main",
            targets=(), creature_target_controllers=(), mana_spent_total=1, schema_version=7,
        )
        previous = v7.to_context()
        self.assertNotIn("physical_card", previous)
        self.assertEqual(previous, SpellCastEvent.from_context(previous).to_context())
        for kind in (True, False):
            modern = replace(v7, schema_version=8, physical_card=kind).to_context()
            self.assertEqual(kind, modern["physical_card"])
            self.assertEqual(modern, SpellCastEvent.from_context(modern).to_context())
        with self.assertRaises(SpellCastEventError):
            SpellCastEvent.from_context({**previous, "schema_version": 8, "physical_card": 1})
        with self.assertRaises(SpellCastEventError):
            SpellCastEvent.from_context({**previous, "physical_card": True})
        for invalid in ({"amount": True, "schema_version": 1}, {"amount": -1, "schema_version": 1},
                        {"amount": 2, "schema_version": 1, "player": "B"}):
            with self.assertRaises(ValueError):
                FixedHandSizeSettingSpec.from_dict(invalid)

    @staticmethod
    def source_triggers(game, source):
        return [item for item in game.state.stack if item.kind == "triggered_ability"
                and item.source_object_id == source.object_id]

    @staticmethod
    def hand_size(game, seat="A"):
        return len(game.state.players[seat].zones["hand"])

    def test_actual_land_play_and_countered_original_draw_but_land_entry_and_cast_copy_add_no_trigger(self):
        game = self.session(70118021)
        engine = game.engine
        source = self.add(engine, RECYCLE, zone="hand")
        forest = self.add(engine, "Forest", zone="hand", ref="physically-played-forest")
        put_land = self.add(engine, "Forest", zone="hand", ref="land-put-by-growth-spiral")
        bear = self.add(engine, "Grizzly Bears", zone="hand")
        spiral = self.add(engine, "Growth Spiral", zone="hand")
        proxy = self.add(engine, "Arcane Proxy", zone="hand")
        selected = self.add(engine, "Shock", zone="graveyard", ref="physical-original-of-copy")
        counterspell = self.add(engine, "Counterspell", seat="B", zone="hand")
        private = self.add(engine, "Shock", seat="D", zone="hand", ref="unrelated-private-hand")
        for index in range(5):
            self.add(engine, "Forest", ref=f"physical-play-green-{index}")
        for index in range(4):
            self.add(engine, "Island", ref=f"physical-play-blue-{index}")
        for index in range(2):
            self.add(engine, "Island", seat="B", ref=f"physical-play-counterspell-{index}")
        for card in (source, bear, spiral, proxy, counterspell):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 4, "G": 2})
        self.checkpoint(game)
        self.cast_for(game, "A", source)
        self.resolve_top(game)
        self.assertFalse(self.source_triggers(game, source))

        self.priority(game, "A")
        result = game.act("pilot:A", {"action_id": "play-land:" + forest.ref})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("battlefield", game.state.cards[forest.object_id].zone)
        self.assertEqual(1, len(self.source_triggers(game, source)))
        before = self.hand_size(game)
        self.resolve_top(game)
        self.assertEqual(before + 1, self.hand_size(game))

        self.cast_for(game, "A", bear)
        original, = [item for item in game.state.stack if item.card_object_id == bear.object_id]
        self.assertEqual(1, len(self.source_triggers(game, source)))
        self.cast_for(game, "B", counterspell, [original.ref])
        self.assertEqual(1, len(self.source_triggers(game, source)))
        self.resolve_top(game)
        self.assertEqual("graveyard", game.state.cards[bear.object_id].zone)
        before = self.hand_size(game)
        self.resolve_top(game)
        self.assertEqual(before + 1, self.hand_size(game))

        self.cast_for(game, "A", spiral)
        self.assertEqual(1, len(self.source_triggers(game, source)))
        self.resolve_top(game)
        self.assertEqual("semantic.choice", self.resolve(game).kind)
        placed = game.act("pilot:A", {"action_id": "choose", "card": put_land.ref})
        self.assertTrue(placed.ok, placed.summary)
        self.settle(game)
        self.assertEqual("battlefield", game.state.cards[put_land.object_id].zone)
        self.assertFalse(self.source_triggers(game, source))

        self.priority(game, "A")
        command = {"action_id": "cast-prototype:" + proxy.ref, "pay": "auto"}
        rows = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        self.assertIn(command["action_id"], {row["id"] for row in rows})
        self.assertTrue(game.act("pilot:A", command).ok)
        self.assertEqual(1, len(self.source_triggers(game, source)))
        self.resolve_top(game)
        self.target(game, "A", [selected.ref])
        self.assertEqual("semantic.choice", self.resolve(game).kind)
        choice = game.packet("pilot:A", full=True)["decision"]
        self.assertEqual(GRAVEYARD_COPY_OFFER, choice["ctx"]["operation"])
        option, = choice["ctx"]["cast_options"]
        before = self.hand_size(game)
        accepted = game.act("pilot:A", {"action_id": "choose", "choice": "cast",
                                       "cast": {"cost_option": option["id"], "targets": ["C"]}})
        self.assertTrue(accepted.ok, accepted.summary)
        copied, = [item for item in game.state.cards.values() if item.is_card_copy and item.zone == "stack"]
        self.assertEqual("Shock", copied.printed_name)
        self.assertFalse(self.source_triggers(game, source), "Casting a card copy must not create a physical-card trigger")
        self.settle(game)
        self.assertEqual(before, self.hand_size(game))
        self.assertEqual(38, game.state.players["C"].life)
        for principal in ("pilot:A", "pilot:B", "pilot:C", "spectator"):
            self.assertNotIn(private.ref, json.dumps(game.packet(principal, full=True)))
        self.replay(game, load=True)

    def test_actual_arcane_proxy_witness_kills_counting_a_cast_copy_as_a_card(self):
        def also_count_copies(state, card):
            context = physical_card_event_context(state, card)
            return {**context, "physical_card": True} if context else context

        with patch("quorune.rules.casting.commit.physical_card_event_context", also_count_copies):
            with self.assertRaisesRegex(AssertionError, "Casting a card copy"):
                self.test_actual_land_play_and_countered_original_draw_but_land_entry_and_cast_copy_add_no_trigger()

    def cleanup_scene(self, *, unlimited_last):
        game = self.session(61311033 + unlimited_last)
        engine = game.engine
        source = self.add(engine, NULL_PROFUSION, zone="hand")
        library = self.add(engine, "Spellbook", zone="hand")
        for card in (source, library):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 4, "B": 2})
        self.checkpoint(game)
        for card in ((source, library) if unlimited_last else (library, source)):
            self.cast_for(game, "A", card)
            self.settle(game)
        self.assertEqual(None if unlimited_last else 2, effective_maximum_hand_size(engine, "A"),
                         "The later maximum must take effect at cleanup")
        self.assertEqual(7, effective_maximum_hand_size(engine, "B"))
        return game

    def test_actual_spellbook_witness_kills_reversing_the_maximum_timestamps(self):
        original = maximum_hand_size._current_hand_size_rules

        def reverse_timestamps(host, seat):
            return tuple((-timestamp, spec) for timestamp, spec in original(host, seat))

        with patch.object(maximum_hand_size, "_current_hand_size_rules", reverse_timestamps):
            with self.assertRaisesRegex(AssertionError, "later maximum must take effect"):
                self.cleanup_scene(unlimited_last=False)

    def test_actual_null_profusion_and_spellbook_follow_both_current_cleanup_timestamp_orders(self):
        for unlimited_last in (False, True):
            with self.subTest(unlimited_last=unlimited_last):
                game = self.cleanup_scene(unlimited_last=unlimited_last)
                initial = self.hand_size(game)
                self.until(game, lambda current: current.state.active_player != "A"
                           or current.state.pending_decision.kind == "cleanup.discard")
                if unlimited_last:
                    self.assertEqual("B", game.state.active_player)
                    self.assertEqual(initial, self.hand_size(game))
                else:
                    choice = game.packet("pilot:A", full=True)["decision"]
                    self.assertEqual("cleanup.discard", choice["kind"])
                    count = choice["ctx"]["count"]
                    self.assertEqual(initial - 2, count)
                    self.assertIsNone(game.packet("pilot:B", full=True)["decision"])
                    command = {"a": "discard", "cards": [row["id"] for row in choice["ctx"]["hand"][:count]]}
                    before = authoritative_state_hash(game.state)
                    self.assertFalse(game.act("pilot:B", command).ok)
                    self.assertEqual(before, authoritative_state_hash(game.state))
                    result = game.act("pilot:A", command)
                    self.assertTrue(result.ok, result.summary)
                    self.assertEqual(2, self.hand_size(game))
                self.replay(game, load=True)
