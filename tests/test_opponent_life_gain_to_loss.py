"""Native opponent life-gain replacement, including combat and loaded replay."""

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.compiler.life_templates import static_opponent_life_gain_to_loss_handler
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.replacement import ReplaceableEvent, ReplacementEffectError, resolve_replacements
from quorune.replacement.operations import MultiplyAmount, SetField
from quorune.rules.capabilities import load_default_capability_registry
from quorune.semantic_runtime.context import SemanticNodeError
from quorune.semantic_runtime.life_replacements import (
    OPPONENT_LIFE_GAIN_TO_LOSS_CAPABILITY,
    OPPONENT_LIFE_GAIN_TO_LOSS_HANDLER_ID,
    LifeReplacementSourceContext,
    OpponentLifeGainToLossHandler,
)
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


ORIGINALS = ("Tainted Remedy", "Plague Drone")
PRINTED_CLAUSE = "If an opponent would gain life, that player loses that much life instead."
PROFILE = "commander_review"


class OpponentLifeGainToLossTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        database = Path(cls.temporary.name) / "opponent-life-gain-to-loss.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/opponent-life-gain-to-loss-cards.json"], database)
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Life conversion", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def programs(self, name):
        bound, semantics = register_card(self.db, self.registry, name)
        self.assertTrue(bound["strict_capability_ready"], bound["blockers"])
        return semantics.programs_for_oracle(self.db.lookup(name, fuzzy=False).oracle_id)

    def test_exact_originals_require_the_current_typed_owner(self):
        capability = OPPONENT_LIFE_GAIN_TO_LOSS_CAPABILITY
        for name in ORIGINALS:
            with self.subTest(name=name):
                programs = self.programs(name)
                program, = (program for program in programs if program.event == "life.change")
                self.assertIn(CURRENT_ABILITY_FRAGMENT_COVERAGE, program.coverage)
                self.assertEqual(OPPONENT_LIFE_GAIN_TO_LOSS_HANDLER_ID, program.handlers[0]["handler_id"])
                self.assertIn(capability, program.capability_dependencies)
                self.assertTrue(self.db.rulings(self.db.lookup(name, fuzzy=False)))
                dependencies = tuple(dep for dep in program.capability_dependencies if dep != capability)
                without = replace(program, capability_dependencies=dependencies,
                                  capability_closure=(self.registry.closure(dependencies, profile=PROFILE).to_dict()
                                                      if dependencies else None))
                result = bind_semantic_program_runtime(without, capability_registry=self.registry, profile=PROFILE)
                self.assertFalse(result["strict"])
                self.assertIn(f"capability:undeclared_runtime_dependency:{capability}", result["blockers"])
        rain = self.db.lookup("Rain of Gore", fuzzy=False)
        rain_ir = compile_oracle_card(rain, capability_registry=self.registry, capability_profile=PROFILE)
        self.assertEqual([rain.oracle_text], [residual.text for residual in rain_ir.material_residuals])
        self.assertIsNone(static_opponent_life_gain_to_loss_handler(PRINTED_CLAUSE + " Draw a card."))

    def test_typed_conversion_requires_positive_opponent_gain_and_keeps_source_and_amount(self):
        _, descriptor, _ = static_opponent_life_gain_to_loss_handler(PRINTED_CLAUSE)
        handler = OpponentLifeGainToLossHandler()
        effect = handler.replacement_effect(descriptor, LifeReplacementSourceContext("source", "A"))
        for seat, direction, amount, expected in (
            ("B", "gain", 3, "loss"), ("A", "gain", 3, "gain"),
            ("B", "gain", 0, "gain"), ("B", "loss", 3, "loss"),
        ):
            with self.subTest(seat=seat, direction=direction, amount=amount):
                event = ReplaceableEvent(f"life:{seat}:{direction}:{amount}", "life.change", seat, {
                    "direction": direction, "amount": amount, "source": "generator",
                    "source_controller": "C", "cause": "effect",
                })
                selections = (effect.effect_id,) if expected != direction else ()
                actual = resolve_replacements(event, (effect,), selections=selections)
                expected_payload = {**event.payload, "direction": expected}
                if selections:
                    expected_payload["replacement_choosers"] = {effect.effect_id: seat}
                self.assertEqual(expected_payload, dict(actual.payload))
                self.assertEqual((event.event_id, seat), (actual.event_id, actual.affected_player))
        gain = ReplaceableEvent("invalid-gain", "life.change", "B", {"direction": "gain", "amount": 3})
        for invalid_direction in ("gain", "draw"):
            forged = replace(effect, operations=(SetField(field="direction", value=invalid_direction),))
            with self.assertRaises(ReplacementEffectError):
                resolve_replacements(gain, (forged,), selections=(forged.effect_id,))
        for invalid in ({"schema_version": True}, {"schema_version": 2}, {"scope": "any"}, {"event": "damage"}):
            with self.assertRaises(SemanticNodeError):
                handler.validate({**descriptor, **invalid})

    def test_conversion_direction_mutation_is_killed(self):
        lower = OpponentLifeGainToLossHandler.replacement_effect

        def keep_the_gain(handler, descriptor, context):
            result = lower(handler, descriptor, context)
            return replace(result, operations=(MultiplyAmount(field="amount", factor=1),))

        with patch.object(OpponentLifeGainToLossHandler, "replacement_effect", keep_the_gain):
            case = type(self)("test_typed_conversion_requires_positive_opponent_gain_and_keeps_source_and_amount")
            result = unittest.TestResult()
            case.run(result)
            self.assertEqual(1, len(result.failures))
            self.assertEqual([], result.errors)

    def test_actual_gain_becomes_loss_and_only_the_matching_loss_observer_triggers(self):
        game = self.session(119614610)
        source = self.add(game.engine, "Tainted Remedy", zone="hand")
        gain = self.add(game.engine, "Heroes' Reunion", zone="hand")
        loss_observer = self.add(game.engine, "Exquisite Blood")
        gain_observer = self.add(game.engine, "Sanguine Bond", seat="B")
        for card in (source, gain, loss_observer, gain_observer):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 2, "B": 1, "G": 1, "W": 1})
        self.checkpoint(game)
        self.cast_for(game, "A", source, reserve={"G": 1, "W": 1})
        self.resolve_top(game)
        self.cast_for(game, "A", gain, ["B"])
        self.resolve_top(game)
        self.assertEqual((40, 33), tuple(game.state.players[seat].life for seat in "AB"))
        self.assertEqual(0, self.trigger_count(game, gain_observer))
        self.assertEqual(1, self.trigger_count(game, loss_observer))
        self.resolve_top(game)
        self.assertEqual((47, 33), tuple(game.state.players[seat].life for seat in "AB"))
        self.replay(game, load=True)

    def test_actual_affected_player_orders_conversion_against_doubling_and_replays_privately(self):
        cases = (("Tainted Remedy", 2, "lose that much", 7),
                 ("Plague Drone", 3, "multiply life gained", 14))
        for name, generic, first, loss in cases:
            with self.subTest(name=name, first=first):
                game = self.session(119616100 + generic)
                source = self.add(game.engine, name, zone="hand")
                double = self.add(game.engine, "Boon Reflection", seat="B")
                gain = self.add(game.engine, "Heroes' Reunion", zone="hand")
                private = self.add(game.engine, "Humble", seat="B", zone="hand", ref="life-conversion-private")
                for card in (source, double, gain):
                    self.assert_closed(game, card)
                self.prepare(game, mana={"C": generic, "B": 1, "W": 1, "G": 1})
                self.checkpoint(game)
                self.cast_for(game, "A", source, reserve={"G": 1, "W": 1})
                self.resolve_top(game)
                self.cast_for(game, "A", gain, ["B"])
                pending = self.resolve(game)
                self.assertEqual(("replacement.order", ["B"]), (pending.kind, pending.actors))
                self.assertEqual(40, game.state.players["B"].life)
                options = game.packet("pilot:B", full=True)["decision"]["ctx"]["options"]
                self.assertEqual(2, len(options))
                choice = next(option for option in options if first in option["label"])
                command = {"action_id": "choose", "replacement": choice["id"]}
                before = authoritative_state_hash(game.state)
                self.assertFalse(game.act("pilot:A", command).ok)
                self.assertFalse(game.act("pilot:B", {**command, "replacement": "unoffered-replacement"}).ok)
                self.assertEqual(before, authoritative_state_hash(game.state))
                for seat in "ACD":
                    packet = json.dumps(game.packet(f"pilot:{seat}", full=True))
                    self.assertNotIn(choice["id"], packet)
                    self.assertNotIn(private.ref, packet)
                game = self.replay(game, load=True)
                applied = game.act("pilot:B", command)
                self.assertTrue(applied.ok, applied.summary)
                self.assertEqual([40, 40 - loss, 40, 40], [game.state.players[seat].life for seat in "ABCD"])
                self.replay(game, load=True)

    def test_actual_humble_ends_the_original_drones_flying_and_gain_conversion(self):
        game = self.session(119614405)
        source = self.add(game.engine, "Plague Drone", zone="hand")
        humble = self.add(game.engine, "Humble", zone="hand")
        first = self.add(game.engine, "Heroes' Reunion", zone="hand", ref="life-conversion-first-gain")
        second = self.add(game.engine, "Heroes' Reunion", zone="hand", ref="life-conversion-second-gain")
        for card in (source, humble, first):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 4, "B": 1, "G": 2, "W": 3})
        self.checkpoint(game)
        self.cast_for(game, "A", source, reserve={"G": 2, "W": 3})
        self.resolve_top(game)
        self.assertIn("flying", game.engine._combat_keywords(game.state.cards[source.object_id]))
        self.cast_for(game, "A", first, ["B"])
        self.resolve_top(game)
        self.assertEqual(33, game.state.players["B"].life)
        self.cast_for(game, "A", humble, [source.ref], reserve={"G": 1, "W": 1})
        self.resolve_top(game)
        self.assertNotIn("flying", game.engine._combat_keywords(game.state.cards[source.object_id]))
        self.cast_for(game, "A", second, ["B"])
        self.resolve_top(game)
        self.assertEqual(40, game.state.players["B"].life)
        self.replay(game, load=True)

    def test_actual_lifelink_converts_but_a_prohibited_gain_never_occurs(self):
        for prohibit in (False, True):
            with self.subTest(prohibit=prohibit):
                game = self.session(119710030 + prohibit)
                source = self.add(game.engine, "Plague Drone", seat="B")
                attacker = self.add(game.engine, "Child of Night")
                attacker.acquired_control_turn_count = -1
                for card in (source, attacker):
                    self.assert_closed(game, card)
                if prohibit:
                    denial = self.add(game.engine, "Skullcrack", zone="hand")
                    self.assert_closed(game, denial)
                self.prepare(game, mana={"C": 1, "R": 1})
                self.checkpoint(game)
                if prohibit:
                    self.cast_for(game, "A", denial, ["D"])
                    self.resolve_top(game)
                self.attack(game, [(attacker, "C")])
                self.until(game, lambda session: session.state.phase == "postcombat_main")
                self.assertEqual((40 if prohibit else 38, 40, 38, 37 if prohibit else 40),
                                 tuple(game.state.players[seat].life for seat in "ABCD"))
                self.replay(game, load=True)
