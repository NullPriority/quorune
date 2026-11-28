"""Authenticated combat damage and announced opponent library play witnesses."""
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
from quorune.fixed_top_exile import ANNOUNCED_OPPONENT_FREE_PLAY_CAPABILITY
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.replacement.immutable import thaw_value
from quorune.rules.announced_hand_discard import ANNOUNCED_HAND_DISCARD_CAPABILITY
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.event_optional_draw_shapes import DAMAGE_DEALER_LIFE_DRAW_CAPABILITY
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


GIX = "Gix, Yawgmoth Praetor"
PROFILE = "commander_review"
FIXTURES = ROOT / "tests/fixtures"


class AnnouncedOpponentResolvingPlayTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "announced-opponent-play.sqlite3"
        build_fixture_database([
            FIXTURES / "announced-opponent-resolving-play-card.json",
            FIXTURES / "committed-damage-dealer-optional-draw-cards.json",
            FIXTURES / "targeted-opponent-library-top-exile-cards.json",
            FIXTURES / "bounded-hand-cast-cards.json",
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("Announced opponent library play", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Plains", 30),
        ], [commander])

    def effect_programs(self):
        rec = self.db.lookup(GIX, fuzzy=False)
        ir = compile_oracle_card(rec, capability_registry=self.registry, capability_profile=PROFILE)
        self.assertEqual(("exact", ()), (ir.status, ir.material_residuals))
        binding, registry = register_card(self.db, self.registry, GIX)
        self.assertTrue(binding["strict_capability_ready"], binding)
        programs = registry.programs_for_oracle(rec.oracle_id)
        trigger, = (row for row in programs if row.event == "damage.dealt")
        activation, = (row for row in programs if row.event == "activate")
        return rec, trigger, activation

    def test_source_bound_payment_and_both_result_descriptors_fail_closed(self):
        rec, trigger, activation = self.effect_programs()
        self.assertIn(DAMAGE_DEALER_LIFE_DRAW_CAPABILITY, trigger.capability_dependencies)
        self.assertIn(ANNOUNCED_HAND_DISCARD_CAPABILITY, activation.capability_dependencies)
        self.assertIn(ANNOUNCED_OPPONENT_FREE_PLAY_CAPABILITY, activation.capability_dependencies)
        trigger_effect, = thaw_value(trigger.effects)
        activation_effect, = thaw_value(activation.effects)
        for mutated in (
            replace(trigger, effects=({**trigger_effect, "player": "$controller", "drawer": "$controller"},)),
            replace(activation, effects=({**activation_effect, "count": 2},)),
            replace(activation, effects=({**activation_effect, "supplier": "$controller"},)),
            replace(activation, cost_schema={**thaw_value(activation.cost_schema), "choices": []}),
        ):
            report = bind_semantic_program_runtime(mutated, capability_registry=self.registry, profile=PROFILE)
            self.assertFalse(report["strict"], report)
        for cap, program in ((DAMAGE_DEALER_LIFE_DRAW_CAPABILITY, trigger),
                             (ANNOUNCED_HAND_DISCARD_CAPABILITY, activation),
                             (ANNOUNCED_OPPONENT_FREE_PLAY_CAPABILITY, activation)):
            dependencies = tuple(key for key in program.capability_dependencies if key != cap)
            missing = replace(program, capability_dependencies=dependencies,
                              capability_closure=self.registry.closure(dependencies, profile=PROFILE).to_dict())
            report = bind_semantic_program_runtime(missing, capability_registry=self.registry, profile=PROFILE)
            self.assertIn("capability:undeclared_runtime_dependency:" + cap, report["blockers"])
        with patch("quorune.compiler.activation_x_costs._announced_hand_discard_cost", return_value=None):
            result = compile_oracle_card(rec, capability_registry=self.registry, capability_profile=PROFILE)
            self.assertNotEqual("exact", result.status)

    def command(self, game, seat, request, *, accepted=True):
        before = authoritative_state_hash(game.state)
        result = game.act("pilot:" + seat, request)
        self.assertEqual(accepted, result.ok, result.summary)
        if not accepted:
            self.assertEqual(before, authoritative_state_hash(game.state))

    def combat_scene(self, seed, victim, *, life=40):
        game = self.session(seed)
        source = self.add(game.engine, GIX)
        self.assert_closed(game, source)
        attacker = self.add(game.engine, "Grizzly Bears", seat="B")
        attacker.acquired_control_turn_count = -1
        self.add(game.engine, "Grizzly Bears", seat=victim, ref="gix-available-blocker")
        control = self.add(game.engine, "Word of Seizing", seat="C", zone="hand")
        bounce = self.add(game.engine, "Boomerang", seat="D", zone="hand")
        for index in range(5):
            self.add(game.engine, "Mountain", seat="C", ref=f"gix-control-mana-{index}")
        for index in range(2):
            self.add(game.engine, "Island", seat="D", ref=f"gix-bounce-mana-{index}")
        game.state.players["C"].life = life
        self.prepare(game, actor="B", active="B")
        self.checkpoint(game)
        self.until(game, lambda current: current.state.pending_decision.kind == "combat.attackers")
        self.command(game, "B", {"action_id": "attack", "attackers": {attacker.ref: victim}})
        self.until(game, lambda current: current.state.pending_decision.kind == "combat.blockers")
        self.command(game, victim, {"action_id": "block", "blocks": {}})
        self.until(game, lambda current: current.state.step == "combat_damage"
                   and current.state.pending_decision.kind == "priority")
        return game, attacker, control, bounce

    def test_actual_paid_damage_draw_is_private_and_uses_current_or_last_known_controller(self):
        for departed in (False, True):
            with self.subTest(departed=departed):
                life = 1 if departed else 40
                game, attacker, control, bounce = self.combat_scene(272610101 + departed, "D", life=life)
                self.assertEqual(38, game.state.players["D"].life)
                trigger, = game.state.stack
                self.assertEqual("A", trigger.controller)
                self.cast_for(game, "C", control, [attacker.ref])
                self.resolve_top(game)
                self.assertEqual("C", game.state.cards[attacker.object_id].controller)
                if departed:
                    self.cast_for(game, "D", bounce, [attacker.ref])
                    self.resolve_top(game)
                    self.assertEqual("hand", game.state.cards[attacker.object_id].zone)
                pending = self.resolve(game)
                self.assertEqual(("semantic.choice", ("C",)), (pending.kind, tuple(pending.actors)))
                command = {"action_id": "choose", "choice": "pay"}
                for foreign in "ABD":
                    self.command(game, foreign, command, accepted=False)
                self.command(game, "C", {**command, "drawer": "A"}, accepted=False)
                game = self.replay(game, load=True)
                event_id = game.state.event_sequence
                self.command(game, "C", command)
                self.assertEqual(life - 1, game.state.players["C"].life)
                self.assertTrue(any(event.code == "card.draw" and event.actor == "C" and event.event_id > event_id
                                    for event in game.state.events))
                private = [event.details["objects"][0] for event in game.state.events
                           if event.code == "card.draw.private" and event.actor == "C" and event.event_id > event_id]
                self.assertEqual(1, len(private))
                for foreign in ("pilot:A", "pilot:B", "pilot:D", "spectator"):
                    self.assertNotIn(json.dumps(private[0]), json.dumps(game.packet(foreign, full=True)))
                if departed:
                    self.assertNotIn("C", game.engine.active_seats)
                self.replay(game, load=True)

    def test_actual_damage_draw_decline_and_damage_to_gix_controller(self):
        game, *_ = self.combat_scene(272610103, "A")
        self.assertEqual(38, game.state.players["A"].life)
        self.assertFalse(game.state.stack)
        self.replay(game, load=True)
        game, *_ = self.combat_scene(272610104, "D")
        pending = self.resolve(game)
        self.assertEqual(("B",), tuple(pending.actors))
        before = (game.state.players["B"].life, tuple(game.state.players["B"].zones["hand"]))
        self.command(game, "B", {"action_id": "choose", "choice": "decline"})
        self.assertEqual(before, (game.state.players["B"].life, tuple(game.state.players["B"].zones["hand"])))
        self.replay(game, load=True)

    def scene(self, seed, names, *, off_turn=False, short_library=False):
        game = self.session(seed)
        host = game.engine
        source = self.add(host, GIX, zone="battlefield" if off_turn else "hand")
        self.assert_closed(game, source)
        tops = tuple(self.add(host, name, seat="B", zone="library", ref=f"gix-top-{index}")
                     for index, name in enumerate(names))
        for card in tops:
            card.known_to = []
            card.revealed_to = []
            self.assert_closed(game, card)
        if short_library:
            for oid in list(game.state.players["B"].zones["library"]):
                if oid not in {card.object_id for card in tops}:
                    host.move_card(oid, "graveyard", reason="fixture short library")
        foreign = self.add(host, "Plains", seat="C", zone="hand", ref="gix-foreign-discard")
        unrelated = self.add(host, "Unsummon", seat="B", zone="exile", ref="gix-unrelated-exile")
        victim = self.add(host, "Grizzly Bears", seat="C", ref="gix-legal-free-target")
        hand = tuple(self.add(host, "Plains", zone="hand", ref=f"gix-payment-{index}") for index in range(7))
        self.prepare(game, active="B" if off_turn else "A",
                     mana={"C": 4, "B": 3} if off_turn else {"C": 5, "B": 5})
        self.checkpoint(game)
        if not off_turn:
            self.cast_for(game, "A", source)
            self.resolve_top(game)
        self.priority(game, "A")
        return game, source, tops, hand, foreign, unrelated, victim

    def activation(self, game, source, hand, foreign, *, count):
        legal = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        offered, = [action for action in legal if action["id"].startswith(f"activate:{source.ref}:")]
        schema = offered["choice_schema"]
        self.assertEqual("x", schema["cost_objects"]["count_from"])
        self.assertEqual(schema["x"]["maximum"], len(schema["cost_objects"]["legal_refs"]))
        self.assertNotIn(foreign.ref, schema["cost_objects"]["legal_refs"])
        response = {"action_id": offered["id"], "pay": "auto", "targets": ["B"],
                    "x": count, "cost_objects": [card.ref for card in hand[:count]]}
        self.command(game, "A", {**response, "targets": ["A"]}, accepted=False)
        self.command(game, "A", {**response, "x": -1}, accepted=False)
        if count:
            self.command(game, "A", {**response, "cost_objects": response["cost_objects"][:-1]}, accepted=False)
            self.command(game, "A", {**response, "cost_objects": [foreign.ref, *response["cost_objects"][1:]]},
                         accepted=False)
        self.command(game, "A", response)
        self.assertEqual((count, ["B"]), (game.state.stack[-1].x_value, game.state.stack[-1].targets))
        for card in hand[:count]:
            self.assertEqual("graveyard", game.state.cards[card.object_id].zone)
        self.assertEqual(0, sum(game.state.players["A"].mana_pool.values()))

    def play_options(self, game):
        return game.packet("pilot:A", full=True)["decision"]["ctx"]["cast_options"]

    def play(self, game, option, *, accepted=True, **fields):
        response = {"action_id": "choose", "choice": "play", "cost_option": option["id"], **fields}
        self.command(game, "A", response, accepted=accepted)

    def test_actual_discard_exiles_only_opponent_tops_and_plays_several_immediately(self):
        game, source, tops, hand, foreign, unrelated, victim = self.scene(
            272610201, ("Plains", "Mountain", "Unsummon", "Stonecoil Serpent", "Llanowar Elves"),
        )
        land, other_land, target_spell, x_spell, unused = tops
        self.activation(game, source, hand, foreign, count=len(tops))
        self.assertTrue(all(game.state.cards[card.object_id].zone == "library" for card in tops))
        pending = self.resolve(game)
        self.assertEqual(("A",), tuple(pending.actors))
        options = {row["card"]: row for row in self.play_options(game)}
        self.assertEqual({card.ref for card in tops}, set(options))
        self.assertNotIn(unrelated.ref, options)
        self.play(game, options[target_spell.ref], targets=["D"], accepted=False)
        self.play(game, options[x_spell.ref], x=1, accepted=False)
        game = self.replay(game, load=True)
        self.play(game, options[land.ref])
        self.assertEqual("A", game.state.cards[land.object_id].controller)
        self.assertEqual(0, game.state.players["A"].land_plays_remaining)
        self.assertNotIn(other_land.ref, {row["card"] for row in self.play_options(game)})
        self.play(game, options[target_spell.ref], targets=[victim.ref])
        self.assertEqual("battlefield", game.state.cards[victim.object_id].zone)
        self.assertEqual("semantic.choice", game.state.pending_decision.kind)
        self.play(game, options[x_spell.ref], x=0)
        self.assertEqual(0, game.state.stack[-1].x_value)
        self.assertEqual("semantic.choice", game.state.pending_decision.kind)
        self.command(game, "A", {"action_id": "choose", "choice": "decline"})
        self.assertEqual([target_spell.object_id, x_spell.object_id], [item.card_object_id for item in game.state.stack])
        for card in (other_land, unused, unrelated):
            self.assertEqual("exile", game.state.cards[card.object_id].zone)
            self.assertNotIn("temporary_play_permission", game.state.cards[card.object_id].annotations)
        game = self.replay(game, load=True)
        self.resolve_top(game)
        self.resolve_top(game)
        self.assertEqual("hand", game.state.cards[victim.object_id].zone)
        self.replay(game, load=True)

    def test_actual_off_turn_excludes_lands_and_keeps_additional_cast_costs(self):
        game, source, tops, hand, foreign, _, _ = self.scene(
            272610202, ("Plains", "Cathartic Reunion", "Llanowar Elves"), off_turn=True,
        )
        land, discard_spell, other_spell = tops
        self.assertEqual("B", game.state.active_player)
        self.activation(game, source, hand, foreign, count=len(tops))
        self.resolve(game)
        options = {row["card"]: row for row in self.play_options(game)}
        self.assertEqual({discard_spell.ref, other_spell.ref}, set(options))
        self.play(game, options[discard_spell.ref], discard_cards=[hand[3].ref, foreign.ref], accepted=False)
        game = self.replay(game, load=True)
        self.play(game, options[discard_spell.ref], discard_cards=[hand[3].ref, hand[4].ref])
        self.play(game, options[other_spell.ref])
        self.assertEqual([discard_spell.object_id, other_spell.object_id],
                         [item.card_object_id for item in game.state.stack])
        self.assertEqual("exile", game.state.cards[land.object_id].zone)
        self.assertTrue(all(game.state.cards[card.object_id].zone == "graveyard" for card in hand[:5]))
        self.replay(game, load=True)

    def test_x_zero_and_short_library_finish_without_granting_future_play(self):
        for count, short in ((0, False), (3, True)):
            with self.subTest(count=count):
                game, source, (top,), hand, foreign, unrelated, _ = self.scene(
                    272610203 + count, ("Llanowar Elves",), short_library=short,
                )
                self.activation(game, source, hand, foreign, count=count)
                pending = self.resolve(game)
                if count:
                    self.assertEqual({top.ref}, {option["card"] for option in self.play_options(game)})
                    self.command(game, "A", {"action_id": "choose", "choice": "decline"})
                    self.assertEqual("exile", game.state.cards[top.object_id].zone)
                    self.assertNotIn("temporary_play_permission", game.state.cards[top.object_id].annotations)
                else:
                    self.assertIsNone(pending)
                    self.assertEqual("library", game.state.cards[top.object_id].zone)
                self.assertEqual("exile", game.state.cards[unrelated.object_id].zone)
                self.replay(game, load=True)
