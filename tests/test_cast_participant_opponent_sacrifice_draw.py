"""Resolve an actual cast-bound source sacrifice before the private opponent draws."""

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
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


STANDSTILL = "Standstill"


class CastParticipantOpponentSacrificeDrawTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "cast-sacrifice-draw.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/cast-participant-opponent-sacrifice-draw-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Cast sacrifice draw", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def test_complete_standstill_uses_one_untargeted_cast_program(self):
        card = self.db.lookup(STANDSTILL, fuzzy=False)
        binding, semantics = register_card(self.db, self.registry, STANDSTILL)
        self.assertTrue(binding["strict_capability_ready"], binding["blockers"])
        trigger, = [item for item in semantics.programs_for_oracle(card.oracle_id) if item.event == "spell.cast"]
        self.assertEqual(("battlefield", None, None), (trigger.active_zone, trigger.target_schema, trigger.event_condition))
        effect, = trigger.effects
        self.assertEqual(("self_zone_move", 3, "$context.player"), (
            effect["op"], effect["fixed_source_sacrifice_version"], effect["cast_participant"],
        ))
        required = {
            "permanent.sacrifice.fixed_source", "trigger.event.normalized_spell_cast",
            "zone.self_move.activated", "zone.draw.library_to_hand",
        }
        self.assertLessEqual(required, set(trigger.capability_dependencies))
        variations = (
            card.oracle_text.replace("a player casts", "you cast"),
            card.oracle_text.replace("that player's opponents", "your opponents"),
            card.oracle_text.replace("three cards", "four cards"),
            card.oracle_text + " Create an unrepresented token.",
        )
        for text in variations:
            self.assertNotEqual("exact", compile_oracle_card(
                replace(card, oracle_text=text), capability_registry=self.registry,
            ).status, text)

    def test_runtime_rejects_reinterpreted_participant_and_unauthorized_frames(self):
        card = self.db.lookup(STANDSTILL, fuzzy=False)
        _, semantics = register_card(self.db, self.registry, STANDSTILL)
        trigger, = [item for item in semantics.programs_for_oracle(card.oracle_id) if item.event == "spell.cast"]
        effect, = trigger.effects
        retained = tuple(key for key in trigger.capability_dependencies if key != "zone.draw.library_to_hand")
        invalid = (
            replace(trigger, event="step.begin"),
            replace(trigger, event_condition={"field": "controller", "op": "eq", "value": "$source.controller"}),
            replace(trigger, effects=({**effect, "cast_participant": "$controller"},)),
            replace(trigger, effects=({**effect, "players": ["A"]},)),
            replace(trigger, effects=({**effect, "_replacement_selections": ["preselected"]},)),
            replace(trigger, effects=(effect, {"op": "draw", "player": "$controller", "count": 1})),
            replace(trigger, coverage=(), capability_dependencies=retained,
                    capability_closure=self.registry.closure(retained, profile="commander_review").to_dict()),
        )
        for program in invalid:
            with self.subTest(event=program.event, effects=program.effects):
                outcome = bind_semantic_program_runtime(program, capability_registry=self.registry,
                                                        profile="commander_review")
                self.assertFalse(outcome["strict"], outcome)

    def enter(self, game, source, *others):
        for card in (source, *others):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 1, "U": 1})
        self.checkpoint(game)
        self.cast_for(game, "A", source)
        self.resolve_top(game)
        self.assertEqual("battlefield", game.state.cards[source.object_id].zone)

    def hands(self, game):
        return {seat: len(player.zones["hand"]) for seat, player in game.state.players.items()}

    def drawn_by(self, game, offset):
        return [event.actor for event in game.state.events[offset:] if event.code == "card.draw"]

    def test_actual_later_cast_uses_its_original_caster_and_replaced_sacrifice_only_once(self):
        game = self.session(61340801)
        engine = game.engine
        source = self.add(engine, STANDSTILL, zone="hand")
        older = self.add(engine, "Opt", seat="B", zone="hand")
        later = self.add(engine, "Unsummon", seat="C", zone="hand")
        creature = self.add(engine, "Grizzly Bears")
        leyline = self.add(engine, "Leyline of the Void", seat="B", ref="standstill-leyline-b")
        other_leyline = self.add(engine, "Leyline of the Void", seat="C", ref="standstill-leyline-c")
        self.add(engine, "Island", seat="B", ref="standstill-island-b")
        self.add(engine, "Island", seat="C", ref="standstill-island-c")
        private = self.add(engine, "Grizzly Bears", seat="D", zone="library", ref="standstill-private-draw")
        library = game.state.players["D"].zones["library"]
        library.remove(private.object_id)
        library.append(private.object_id)
        self.enter(game, source, older, later, leyline, other_leyline)
        self.cast_for(game, "B", older)
        old_trigger = game.state.stack[-1].ref
        self.cast_for(game, "C", later, [creature.ref])
        self.assertNotEqual(old_trigger, game.state.stack[-1].ref)
        before = self.hands(game)
        decision = self.resolve(game)
        self.assertEqual(("replacement.order", ["A"]), (decision.kind, decision.actors))
        self.assertEqual(before, self.hands(game))
        self.assertEqual("battlefield", game.state.cards[source.object_id].zone)
        options = game.packet("pilot:A", full=True)["decision"]["ctx"]["options"]
        self.assertEqual(2, len(options))
        choice = {"action_id": "choose", "replacement": options[0]["id"]}
        state = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:C", choice).ok)
        self.assertEqual(state, authoritative_state_hash(game.state))
        game = self.replay(game, load=True)
        offset = len(game.state.events)
        result = game.act("pilot:A", choice)
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("exile", game.state.cards[source.object_id].zone)
        self.assertEqual(["A"] * 3 + ["B"] * 3 + ["D"] * 3, self.drawn_by(game, offset))
        self.assertEqual({seat: count + (0 if seat == "C" else 3) for seat, count in before.items()}, self.hands(game))
        self.assertIn(private.object_id, game.state.players["D"].zones["hand"])
        self.assertIn(private.ref, json.dumps(game.packet("pilot:D", full=True)))
        for viewer in ("pilot:A", "pilot:B", "pilot:C", "spectator"):
            self.assertNotIn(private.ref, json.dumps(game.packet(viewer, full=True)))
        self.resolve_top(game)
        self.assertEqual("hand", game.state.cards[creature.object_id].zone)
        self.assertEqual(old_trigger, game.state.stack[-1].ref)
        self.resolve_top(game)
        self.assertEqual(["A"] * 3 + ["B"] * 3 + ["D"] * 3, self.drawn_by(game, offset))
        self.replay(game, load=True)

    def test_actual_seal_activation_prevents_the_already_queued_sacrifice_and_draw(self):
        game = self.session(61340802)
        engine = game.engine
        source = self.add(engine, STANDSTILL, zone="hand")
        spell = self.add(engine, "Opt", seat="B", zone="hand")
        seal = self.add(engine, "Seal of Cleansing", seat="C")
        self.add(engine, "Island", seat="B")
        self.enter(game, source, spell, seal)
        self.cast_for(game, "B", spell)
        trigger = game.state.stack[-1].ref
        self.priority(game, "C")
        actions = game.packet("pilot:C", full=True)["decision"]["ctx"]["legal"]["actions"]
        action, = [row["id"] for row in actions if row["id"].startswith("activate:" + seal.ref + ":")]
        result = game.act("pilot:C", {"action_id": action, "targets": [source.ref], "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("graveyard", game.state.cards[seal.object_id].zone)
        self.assertEqual(trigger, game.state.stack[-2].ref)
        before = self.hands(game)
        offset = len(game.state.events)
        self.resolve_top(game)
        self.assertEqual("graveyard", game.state.cards[source.object_id].zone)
        self.assertEqual(trigger, game.state.stack[-1].ref)
        self.resolve_top(game)
        self.assertEqual(before, self.hands(game))
        self.assertEqual([], self.drawn_by(game, offset))
        self.replay(game, load=True)

    def test_actual_departed_caster_keeps_its_opponents_and_mutation_breaks_draws(self):
        from quorune.rules.committed_quantity_followups import CommittedQuantityFollowup
        from quorune.semantic_runtime.source_sacrifice import apply_fixed_source_sacrifice

        def resolve_witness():
            game = self.session(61340803)
            source = self.add(game.engine, STANDSTILL, zone="hand")
            spell = self.add(game.engine, "Opt", seat="B", zone="hand")
            self.add(game.engine, "Island", seat="B")
            self.enter(game, source, spell)
            self.cast_for(game, "B", spell)
            trigger = game.state.stack[-1].ref
            result = game.act("pilot:B", {"action_id": "concede", "choices": {"confirm_concede": True}})
            self.assertTrue(result.ok, result.summary)
            self.assertNotIn("B", game.engine.active_seats)
            self.assertEqual(trigger, game.state.stack[-1].ref)
            offset = len(game.state.events)
            self.resolve_top(game)
            self.assertEqual("graveyard", game.state.cards[source.object_id].zone)
            self.assertEqual(["A"] * 3 + ["C"] * 3 + ["D"] * 3, self.drawn_by(game, offset),
                             "The original cast participant's opponents draw")
            self.replay(game, load=True)

        def omit_draw(host, intent):
            apply_fixed_source_sacrifice(host, intent)
            return CommittedQuantityFollowup(())

        resolve_witness()
        with patch("quorune.semantic_runtime.source_sacrifice.apply_fixed_source_sacrifice", side_effect=omit_draw):
            with self.assertRaisesRegex(AssertionError, "original cast participant"):
                resolve_witness()
