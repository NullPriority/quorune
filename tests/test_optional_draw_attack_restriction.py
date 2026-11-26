"""Native proofs for optional draws and player-bound next-turn attack restrictions."""

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
from quorune.maximum_hand_size import effective_maximum_hand_size
from quorune.next_turn_attack_restriction_model import (
    ATTACK_RESTRICTION_CAPABILITY, ATTACK_RESTRICTION_OPERATION, ResolvedNextTurnAttackRestriction,
)
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database


class OptionalDrawAttackRestrictionTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "optional-draw-attack-restriction.sqlite3"
        fixtures = ROOT / "tests" / "fixtures"
        build_fixture_database([
            fixtures / "bound-effect-program-cards.json",
            fixtures / "cast-participant-optional-opponent-draw-cards.json",
            fixtures / "redirected-draw-cards.json",
            fixtures / "optional-draw-attack-restriction-cards.json",
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Optional draw attack restriction", [
            DeckEntry("Generic Bound Commander", 1, "commander"),
            DeckEntry("Generic Bound Plains", 30),
        ], ["Generic Bound Commander"])

    def begin_end_step_draws(self, game, source):
        self.until(game, lambda g: g.state.step == "end_step" and any(
            item.source_object_id == source.object_id for item in g.state.stack
        ))
        self.assertEqual("semantic.choice", self.resolve(game).kind)

    def choose_draw(self, game, seat, answer, previous):
        decision = game.state.pending_decision
        self.assertEqual(("semantic.choice", [seat]), (decision.kind, decision.actors))
        packet = game.packet(f"pilot:{seat}", full=True)["decision"]
        self.assertEqual(seat, packet["ctx"]["drawer"])
        self.assertEqual(
            [{"player": player, "choice": choice} for player, choice in previous],
            packet["ctx"]["previous_choices"],
        )
        self.assertIsNone(game.packet("spectator", full=True)["decision"])
        result = game.act(f"pilot:{seat}", {"action_id": "choose", "choice": answer})
        self.assertTrue(result.ok, result.summary)

    @staticmethod
    def libraries(game):
        return {seat: tuple(player.zones["library"]) for seat, player in game.state.players.items()}

    @staticmethod
    def restrictions(game):
        return {
            effect.restricted_player: (effect.protected_player, effect.own_turn)
            for effect in game.state.continuous_effects
            if isinstance(effect, ResolvedNextTurnAttackRestriction)
        }

    def prepare_defenders(self, game):
        engine = game.engine
        walker = self.add(engine, "Generic Protected Walker", ref="doctor-protected-walker")
        walker.counters["loyalty"] = 4
        protected = self.add(engine, "Generic Protected Battle", ref="doctor-protected-battle")
        protected.counters["defense"] = 4
        protected.battle_protector = "C"
        other = self.add(engine, "Generic Protected Battle", seat="C", ref="doctor-unprotected-battle")
        other.counters["defense"] = 4
        other.battle_protector = "A"
        return walker, protected, other

    def expect_attack_targets(self, game, card, *, legal, barred):
        actor = game.state.active_player
        self.assertEqual("combat.attackers", game.state.pending_decision.kind)
        packet = game.packet(f"pilot:{actor}", full=True)["decision"]
        offers = packet["ctx"]["declaration_constraints"]["domains"][card.ref]
        self.assertTrue(set(legal) <= set(offers), offers)
        self.assertTrue(set(barred).isdisjoint(offers), offers)
        attack, = [action for action in packet["legal_actions"] if action["action"] == "attack"]
        assignment, = [field for field in attack["form"]["fields"] if field["name"] == "attackers"]
        row, = [row for row in assignment["rows"] if row["value"] == card.ref]
        choices = {option["value"] for option in row["options"]}
        self.assertTrue(set(legal) <= choices, choices)
        self.assertTrue(set(barred).isdisjoint(choices), choices)

    def test_native_apnap_draws_and_each_opponents_next_attack_turn(self):
        game = self.session(61012101)
        doctor = self.add(game.engine, "The Second Doctor", seat="C", ref="doctor-original-source")
        game.engine.change_control(doctor.object_id, "A", reason="Pre-action borrowed source fixture")
        self.assert_closed(game, doctor)
        attacker_b = self.add(game.engine, "Generic Bound Body", seat="B", ref="doctor-b-attacker")
        attacker_d = self.add(game.engine, "Generic Bound Body", seat="D", ref="doctor-d-attacker")
        walker, protected, other = self.prepare_defenders(game)
        private = self.add(game.engine, "Generic Bound Growth", seat="D", zone="library", ref="doctor-private-draw")
        self.prepare(game)
        self.checkpoint(game)
        self.assertEqual([None] * 4, [effective_maximum_hand_size(game.engine, seat) for seat in "ABCD"])
        library_before = self.libraries(game)
        next_turns = {seat: game.state.players[seat].turns_begun + 1 for seat in "BD"}
        self.begin_end_step_draws(game, doctor)
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:B", {"action_id": "choose", "choice": "draw"}).ok)
        self.assertFalse(game.act("pilot:A", {
            "action_id": "choose", "choice": "draw", "protected_player": "C",
        }).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))

        answered = []
        for seat, answer in (("A", "draw"), ("B", "draw"), ("C", "decline")):
            self.choose_draw(game, seat, answer, answered)
            answered.append((seat, answer))
            self.assertEqual(library_before, self.libraries(game))
            self.assertEqual({}, self.restrictions(game))
        game = self.replay(game, load=True)
        offset = len(game.state.events)
        self.choose_draw(game, "D", "draw", answered)
        self.assertEqual(["A", "B", "D"], [event.actor for event in game.state.events[offset:]
                                           if event.code == "card.draw"][:3])
        self.assertEqual({seat: ("A", own_turn) for seat, own_turn in next_turns.items()}, self.restrictions(game))
        self.assertIn(private.object_id, game.state.players["D"].zones["hand"])
        self.assertIn(private.ref, json.dumps(game.packet("pilot:D", full=True)))
        for viewer in ("pilot:A", "pilot:B", "pilot:C", "spectator"):
            self.assertTrue(private.ref not in json.dumps(game.packet(viewer, full=True)), viewer)

        self.until(game, lambda g: g.state.active_player == "B" and g.state.pending_decision.kind == "combat.attackers")
        barred = ("A", walker.ref, protected.ref)
        allowed = ("C", "D", other.ref)
        self.expect_attack_targets(game, attacker_b, legal=allowed, barred=barred)
        before = authoritative_state_hash(game.state)
        for target in barred:
            rejected = game.act("pilot:B", {"action_id": "attack", "attackers": {attacker_b.ref: target}})
            self.assertFalse(rejected.ok, target)
            self.assertEqual(before, authoritative_state_hash(game.state))
        game = self.replay(game, load=True)
        self.expect_attack_targets(game, attacker_b, legal=allowed, barred=barred)
        with patch.object(ResolvedNextTurnAttackRestriction, "applies", return_value=False):
            problem, _, _ = game.engine._attack_declaration_components("B")
            self.assertIn("A", problem.domains[attacker_b.ref], "Removing the duration predicate defeats the offer")
        accepted = game.act("pilot:B", {"action_id": "attack", "attackers": {attacker_b.ref: other.ref}})
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertEqual(other.ref, game.state.cards[attacker_b.object_id].attacking)
        self.until(game, lambda g: g.state.active_player == "D" and g.state.pending_decision.kind == "combat.attackers")
        self.assertEqual({"D": ("A", next_turns["D"])}, self.restrictions(game))
        self.expect_attack_targets(game, attacker_d, legal=allowed[:1], barred=barred)
        self.until(game, lambda g: g.state.active_player == "A")
        self.assertEqual({}, self.restrictions(game))
        self.replay(game, load=True)

    def test_redirected_empty_library_draw_restricts_the_chooser_not_the_recipient(self):
        game = self.session(61012102)
        doctor = self.add(game.engine, "The Second Doctor")
        thief = self.add(game.engine, "Notion Thief", seat="C")
        self.assert_closed(game, doctor)
        self.assert_closed(game, thief)
        for object_id in tuple(game.state.players["B"].zones["library"]):
            game.engine.move_card(object_id, "outside", log=False, reason="Initial empty-library fixture")
        private = self.add(game.engine, "Generic Bound Growth", seat="C", zone="library", ref="doctor-replacement-card")
        self.prepare(game)
        self.checkpoint(game)
        self.begin_end_step_draws(game, doctor)
        answered = []
        for seat, answer in (("A", "decline"), ("B", "draw"), ("C", "decline")):
            self.choose_draw(game, seat, answer, answered)
            answered.append((seat, answer))
        game = self.replay(game, load=True)
        self.choose_draw(game, "D", "decline", answered)
        self.assertIn("B", game.engine.active_seats)
        self.assertFalse(game.state.players["B"].attempted_empty_draw)
        self.assertIn(private.object_id, game.state.players["C"].zones["hand"])
        self.assertEqual({"B": ("A", game.state.players["B"].turns_begun + 1)}, self.restrictions(game))
        self.replay(game, load=True)

    def test_prohibited_draw_is_skipped_and_raw_roles_cannot_be_forged(self):
        game = self.session(61012103)
        doctor = self.add(game.engine, "The Second Doctor")
        limit = self.add(game.engine, "Spirit of the Labyrinth", seat="D")
        spell = self.add(game.engine, "Reach Through Mists", zone="hand")
        for source in (doctor, limit, spell):
            self.assert_closed(game, source)
        self.prepare(game, mana={"U": 1})
        self.checkpoint(game)
        self.cast_for(game, "A", spell)
        self.resolve_top(game)
        self.begin_end_step_draws(game, doctor)
        answered = []
        for seat in "BCD":
            self.choose_draw(game, seat, "decline", answered)
            answered.append((seat, "decline"))
        self.assertEqual({}, self.restrictions(game))
        self.replay(game, load=True)

        card = self.db.lookup("The Second Doctor", fuzzy=False)
        trigger, = [program for program in game.engine.semantics.programs_for_oracle(card.oracle_id)
                    if program.event == "step.begin"]
        self.assertIn(ATTACK_RESTRICTION_CAPABILITY, trigger.capability_dependencies)
        effect, = trigger.effects
        for changed in (
            replace(trigger, event_condition=None),
            replace(trigger, effects=({**effect, "protected_player": "$context.player"},)),
            replace(trigger, effects=({**effect, "table_draw_choices": ["draw"]},)),
            replace(trigger, effects=({
                "op": ATTACK_RESTRICTION_OPERATION, "schema_version": 1,
                "protected_player": "$controller", "opponents": ["B"],
            },)),
        ):
            result = bind_semantic_program_runtime(changed, capability_registry=self.registry, profile="commander_review")
            self.assertFalse(result["strict"], result)
        for wrong in (
            card.oracle_text.replace("your end step", "each end step"),
            card.oracle_text.replace("draw a card", "draw two cards"),
            card.oracle_text.replace("their next turn", "this turn"),
        ):
            self.assertNotEqual("exact", compile_oracle_card(
                replace(card, oracle_text=wrong), capability_registry=self.registry,
            ).status)

    def test_arbiter_cannot_supply_the_reserved_offer_or_followup(self):
        game = self.session(61012104)
        unresolved = self.add(game.engine, "Generic Raw Resolution Witness", zone="hand")
        self.prepare(game, mana={"U": 1})
        self.checkpoint(game)
        self.cast_for(game, "A", unresolved)
        self.assertEqual("arbiter.resolve", self.resolve(game).kind)

        restriction = {"op": ATTACK_RESTRICTION_OPERATION, "schema_version": 1,
                       "protected_player": "A", "opponents": ["B"]}
        offer = {"op": "offer_draw", "event_optional_draw_version": 6,
                 "protected_player": "A", "count": 1, "private": True}
        staged = {**offer, "player": "D", "table_draw_order": list("ABCD"),
                  "table_draw_choices": ["decline", "draw", "decline"]}

        def wrapped(effect):
            return {"op": "offer_optional_effect", "player": "A", "effects": [effect]}

        before = authoritative_state_hash(game.state)
        for effects in (restriction, offer, staged, wrapped(restriction), wrapped(staged)):
            with self.subTest(op=effects["op"]):
                result = game.act("arbiter", {"action_id": "resolve", "effects": [effects]})
                self.assertFalse(result.ok)
                self.assertIn("server-issued optional draw choices", result.summary)
                self.assertEqual(before, authoritative_state_hash(game.state))
        for symbolic in (
            {**offer, "event_optional_draw_version": "$context.version"},
            wrapped({**restriction, "op": "$context.operation"}),
        ):
            result = game.act("arbiter", {"action_id": "resolve", "effects": [symbolic]})
            self.assertFalse(result.ok)
            self.assertIn("must be literal", result.summary)
            self.assertEqual(before, authoritative_state_hash(game.state))
        rejected = game.act("arbiter", {"action_id": "register_and_resolve", "effects": [offer]})
        self.assertFalse(rejected.ok)
        self.assertIn("server-issued optional draw choices", rejected.summary)
        self.assertEqual(before, authoritative_state_hash(game.state))
        self.assertEqual({}, self.restrictions(game))

        library = self.libraries(game)["A"]
        result = game.act("arbiter", {"action_id": "resolve", "effects": [
            {"op": "offer_draw", "player": "$controller", "count": 1, "private": True},
        ]})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("semantic.choice", game.state.pending_decision.kind)
        result = game.act("pilot:A", {"action_id": "choose", "choice": "draw"})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(library[:-1], self.libraries(game)["A"])
        self.assertEqual({}, self.restrictions(game))
        self.replay(game, load=True)
