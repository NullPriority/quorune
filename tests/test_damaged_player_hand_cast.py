"""Native private hand inspection and one resolution-authorized free cast."""

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.bounded_hand_cast_model import BOUNDED_HAND_CAST_FIELD, DAMAGED_PLAYER_HAND_CAST_CAPABILITY
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.model import CombatState
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.session import CommanderSession
from quorune.turn_step_owner import TURN_STEPS
from scripts.build_test_database import build_fixture_database


class DamagedPlayerHandCastTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "damaged-player-hand-cast.sqlite3"
        fixtures = ROOT / "tests" / "fixtures"
        build_fixture_database([
            fixtures / "bound-effect-program-cards.json",
            fixtures / "bounded-hand-cast-cards.json",
            fixtures / "damaged-player-hand-cast-cards.json",
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Damaged player hand cast", [
            DeckEntry("Generic Bound Commander", 1, "commander"),
            DeckEntry("Generic Bound Plains", 30),
        ], ["Generic Bound Commander"])

    def prepare_combat(self, game, attacker, *, mana=None, recipient="C"):
        self.prepare(game, mana=mana)
        engine = game.engine
        engine.state.config.semantic_policy = "trusted_only"
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine.state.priority_player = None
        engine.state.priority_passes = []
        engine.state.phase = "combat"
        engine.state.step = "declare_blockers"
        engine.state.phase_index = TURN_STEPS.index(("combat", "declare_blockers"))
        engine.state.combat = CombatState(
            attackers_declared=True, blockers_declared=True, had_attacking_creature=True,
            attackers={attacker.object_id: recipient},
            attack_target_context={attacker.object_id: {
                "target": recipient, "kind": "player", "defending_player": recipient,
            }},
            defending_players=[recipient],
        )
        attacker.attacking = recipient
        attacker.tapped = True
        engine._grant_priority("A")
        engine.pump()
        self.checkpoint(game)

    def reach_trigger(self, game, source):
        self.until(game, lambda current: any(item.kind == "triggered_ability"
                   and item.source_object_id == source.object_id for item in current.state.stack))
        return self.resolve(game)

    def private_choice(self, game, source, owner="C"):
        decision = self.reach_trigger(game, source)
        self.assertEqual(("semantic.choice", ["A"]), (decision.kind, decision.actors))
        context = game.packet("pilot:A", full=True)["decision"]["ctx"]
        self.assertEqual(owner, context["target"])
        return context

    @staticmethod
    def command(card, option, **details):
        return {"action_id": "choose", "choice": "cast",
                "cast": {"card": card.ref, "cost_option": option, **details}}

    def reject_unchanged(self, game, command, *, principal="pilot:A"):
        before = authoritative_state_hash(game.state)
        result = game.act(principal, command)
        self.assertFalse(result.ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        return result

    def test_paid_ninjutsu_looks_at_only_damaged_hand_and_casts_one_legal_target(self):
        game = self.session(60802401)
        oni = self.add(game.engine, "Silent-Blade Oni", zone="hand", ref="oni-return-entry")
        attacker = self.add(game.engine, "Generic Bound Body", ref="oni-unblocked-tribute")
        target = self.add(game.engine, "Grizzly Bears", seat="B", ref="oni-bounce-target")
        other = self.add(game.engine, "Wind Drake", seat="B", ref="oni-other-permanent")
        rift = self.add(game.engine, "Cyclonic Rift", seat="C", zone="hand", ref="oni-private-rift")
        land = self.add(game.engine, "Plains", seat="C", zone="hand", ref="oni-inspected-land")
        foreign = self.add(game.engine, "Lightning Bolt", seat="D", zone="hand", ref="oni-uninspected")
        for card in (oni, rift):
            self.assert_closed(game, card)
        self.prepare_combat(game, attacker, mana={"C": 4, "U": 1, "B": 1})
        offers = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        activate, = [offer for offer in offers if offer["id"].startswith(f"activate:{oni.ref}:")]
        result = game.act("pilot:A", {
            "action_id": activate["id"], "cost_objects": [attacker.ref], "pay": "auto",
        })
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("hand", game.state.cards[attacker.object_id].zone)
        self.resolve_top(game)
        self.assertEqual("C", game.state.cards[oni.object_id].attacking)
        before_life = game.state.players["C"].life

        context = self.private_choice(game, oni)
        self.assertEqual(before_life - 6, game.state.players["C"].life)
        self.assertEqual({rift.ref, land.ref} | {
            game.state.cards[object_id].ref for object_id in game.state.players["C"].zones["hand"]
        }, {row["id"] for row in context["objects"]})
        options = context["cast_options"]
        option, = [row for row in options if row["card"] == rift.ref]
        self.assertFalse(any(row["card"] in {land.ref, foreign.ref} or row["kind"] == "overload" for row in options))
        self.assertEqual(0, sum(option["requirements"].values()))
        for viewer in ("pilot:B", "pilot:C", "pilot:D", "spectator"):
            packet = game.packet(viewer, full=True)
            self.assertIsNone(packet["decision"])
            self.assertTrue(option["id"] not in json.dumps(packet), viewer)
            if viewer != "pilot:C":
                self.assertTrue(rift.ref not in json.dumps(packet), viewer)
                self.assertTrue(land.ref not in json.dumps(packet), viewer)

        command = self.command(rift, option["id"], targets=[target.ref])
        self.reject_unchanged(game, command, principal="pilot:C")
        self.reject_unchanged(game, self.command(foreign, option["id"], targets=["B"]))
        self.reject_unchanged(game, self.command(rift, "overload", targets=[target.ref]))
        self.reject_unchanged(game, self.command(rift, option["id"], targets=[oni.ref]))
        with TemporaryDirectory() as directory:
            pending = Path(directory) / "pending-inspected-hand"
            game.save(pending)
            stale = CommanderSession.load(self.db, pending)
            old_incarnation = stale.state.cards[rift.object_id].logical_object_id
            stale.engine.move_card(rift.object_id, "exile", log=False)
            stale.engine.move_card(rift.object_id, "hand", log=False)
            self.assertNotEqual(old_incarnation, stale.state.cards[rift.object_id].logical_object_id)
            rejected = self.reject_unchanged(stale, command)
            self.assertIn("inspected hand changed", rejected.summary)

        game = self.replay(game, load=True)
        result = game.act("pilot:A", command)
        self.assertTrue(result.ok, result.summary)
        spell, = [item for item in game.state.stack if item.card_object_id == rift.object_id]
        self.assertEqual("A", spell.controller)
        self.assertEqual("C", game.state.cards[rift.object_id].owner)
        spectator = game.packet("spectator", full=True)
        public_spell, = [row for row in spectator["state"]["stack"] if row["id"] == spell.ref]
        self.assertEqual(("spell", "A", "Cyclonic Rift"),
                         (public_spell["kind"], public_spell["ctl"], public_spell["n"]))
        self.assertTrue(land.ref not in json.dumps(spectator))
        self.assertTrue(foreign.ref not in json.dumps(spectator))
        self.resolve_top(game)
        self.assertEqual("graveyard", game.state.cards[rift.object_id].zone)
        self.assertIn(rift.object_id, game.state.players["C"].zones["graveyard"])
        self.assertEqual("hand", game.state.cards[target.object_id].zone)
        self.assertEqual("battlefield", game.state.cards[other.object_id].zone)
        self.replay(game, load=True)

    def test_lands_only_hand_is_still_looked_at_and_grants_no_future_cast(self):
        game = self.session(60802402)
        oni = self.add(game.engine, "Silent-Blade Oni", ref="oni-inspection-only")
        land = self.add(game.engine, "Plains", seat="C", zone="hand", ref="oni-only-inspected-land")
        other = self.add(game.engine, "Wind Drake", seat="D", zone="hand", ref="oni-unrelated-spell")
        self.prepare_combat(game, oni)
        self.assertNotIn("A", game.state.cards[land.object_id].known_to)
        self.assertIsNone(self.reach_trigger(game, oni))
        self.assertIn("A", game.state.cards[land.object_id].known_to)
        self.assertNotIn("A", game.state.cards[other.object_id].known_to)
        look, = [event for event in game.state.events if event.code == "hand.look"]
        self.assertEqual("A", look.actor)
        self.assertEqual("C", look.details["player"])
        self.assertNotIn(land.ref, json.dumps(game.packet("spectator", full=True)))
        self.assertNotIn(land.ref, json.dumps(game.packet("pilot:D", full=True)))
        self.assertFalse(any(action.get("card") == other.ref
                             for action in game.engine._priority_action_hints("A")["actions"]))
        self.replay(game, load=True)

    def test_decline_or_cast_a_foreign_permanent_under_the_choosers_control(self):
        game = self.session(60802403)
        oni = self.add(game.engine, "Silent-Blade Oni", ref="oni-permanent-parent")
        drake = self.add(game.engine, "Wind Drake", seat="C", zone="hand", ref="oni-private-permanent")
        self.assert_closed(game, drake)
        self.prepare_combat(game, oni)
        options = self.private_choice(game, oni)["cast_options"]
        option, = [row for row in options if row["card"] == drake.ref]
        with TemporaryDirectory() as directory:
            pending = Path(directory) / "decline-inspected-hand"
            game.save(pending)
            declining = CommanderSession.load(self.db, pending)
            result = declining.act("pilot:A", {"action_id": "choose", "choice": "decline"})
            self.assertTrue(result.ok, result.summary)
            self.assertEqual("hand", declining.state.cards[drake.object_id].zone)
            self.assertFalse(declining.state.stack)
        result = game.act("pilot:A", self.command(drake, option["id"]))
        self.assertTrue(result.ok, result.summary)
        self.resolve_top(game)
        entered = game.state.cards[drake.object_id]
        self.assertEqual(("battlefield", "A", "C"), (entered.zone, entered.controller, entered.owner))
        self.replay(game, load=True)

    def test_foreign_free_cast_pays_mandatory_additional_cost_as_chooser(self):
        game = self.session(60802404)
        oni = self.add(game.engine, "Silent-Blade Oni", ref="oni-additional-cost-parent")
        rites = self.add(game.engine, "Village Rites", seat="C", zone="hand", ref="oni-private-rites")
        serpent = self.add(game.engine, "Stonecoil Serpent", seat="C", zone="hand", ref="oni-private-x-spell")
        tribute = self.add(game.engine, "Generic Bound Body", ref="oni-choosers-tribute")
        forbidden = self.add(game.engine, "Grizzly Bears", seat="C", ref="oni-owners-tribute")
        self.assert_closed(game, rites)
        self.prepare_combat(game, oni)
        options = self.private_choice(game, oni)["cast_options"]
        option, = [row for row in options if row["card"] == rites.ref]
        zero_x, = [row for row in options if row["card"] == serpent.ref]
        self.reject_unchanged(game, self.command(serpent, zero_x["id"], x=1))
        self.reject_unchanged(game, self.command(rites, option["id"]))
        self.reject_unchanged(game, self.command(rites, option["id"], sacrifice_cards=[forbidden.ref]))
        result = game.act("pilot:A", self.command(rites, option["id"], sacrifice_cards=[tribute.ref]))
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("graveyard", game.state.cards[tribute.object_id].zone)
        self.assertEqual("battlefield", game.state.cards[forbidden.object_id].zone)
        before = len(game.state.players["A"].draw_history)
        self.resolve_top(game)
        self.assertEqual(before + 2, len(game.state.players["A"].draw_history))
        self.assertIn(rites.object_id, game.state.players["C"].zones["graveyard"])
        self.replay(game, load=True)

    def test_compiler_keeps_the_private_permission_on_its_exact_damage_occurrence(self):
        game = self.session(60802405)
        oni = self.add(game.engine, "Silent-Blade Oni")
        self.assert_closed(game, oni)
        card = self.db.lookup("Silent-Blade Oni", fuzzy=False)
        trigger, = [program for program in game.engine.semantics.programs_for_oracle(card.oracle_id)
                    if program.event == "damage.dealt.self"]
        self.assertIn(DAMAGED_PLAYER_HAND_CAST_CAPABILITY, trigger.capability_dependencies)
        effect, = trigger.effects
        for changed in (
            replace(trigger, event_condition=None),
            replace(trigger, effects=({**effect, BOUNDED_HAND_CAST_FIELD: {
                "schema_version": 4, "hand_owner": "D",
            }},)),
        ):
            binding = bind_semantic_program_runtime(
                changed, capability_registry=self.registry, profile="commander_review",
            )
            self.assertFalse(binding["strict"], binding)
        for wrong in (
            card.oracle_text.replace("combat damage", "damage"),
            card.oracle_text.replace("that player's hand", "your hand"),
            card.oracle_text.replace("a spell from among those cards", "two spells from among those cards"),
        ):
            modified = replace(card, oracle_text=wrong)
            self.assertNotEqual("exact", compile_oracle_card(modified, capability_registry=self.registry).status)
        with patch("quorune.compiler.bounded_hand_cast_templates.damaged_player_hand_cast_trigger_result",
                   return_value=None):
            self.assertNotEqual("exact", compile_oracle_card(card, capability_registry=self.registry).status)
