"""Real spell and attack observations with an original linked delayed return."""

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture

from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.cast_or_attack_source_exile import DEPENDENCIES, cast_or_attack_source_exile_condition
from scripts.build_test_database import build_fixture_database


CARD = "Norin the Wary"
RETURN_LABEL = "Return linked exiled objects"


class CastOrAttackSourceExileTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        database = Path(cls.temporary.name) / "cast-or-attack-source-exile.sqlite3"
        fixtures = ROOT / "tests/fixtures"
        build_fixture_database([fixtures / name for name in (
            "cast-or-attack-source-exile-cards.json", "next-personal-turn-protection-cards.json",
            "ability-source-counter-cards.json", "static-step-skip-cards.json",
        )], database)
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Cast or attack self-exile", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def start(self, game, *cards):
        game.state.config.semantic_policy = "trusted_only"
        for card in cards:
            self.assert_closed(game, card)
        self.prepare(game)
        self.checkpoint(game)

    def return_triggers(self, game):
        return [row for row in game.state.delayed_triggers if row.active and row.label == RETURN_LABEL]

    def test_exact_source_body_and_both_observations_are_required(self):
        card = self.db.lookup(CARD, fuzzy=False)
        ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile="commander_review")
        self.assertEqual("exact", ir.status, ir.material_residuals)
        node, = ir.faces[0].nodes
        self.assertEqual(card.oracle_text, card.oracle_text[node.span.start:node.span.end])
        self.assertEqual(cast_or_attack_source_exile_condition(), node.event_condition)
        self.assertIsNone(node.target_schema)
        self.assertTrue(set(DEPENDENCIES) <= set(node.capability_dependencies))
        exile, return_from_exile = node.effects
        self.assertEqual("$source.zone_object", exile["cards"])
        self.assertEqual(exile["binding_id"], return_from_exile["binding_id"])
        self.assertEqual(("owner", "next_end_step"), (exile["spec"]["controller"], exile["spec"]["timing"]))
        for old, new in (
            ("a player casts", "an opponent casts"), ("a creature attacks", "a creature blocks"),
            ("its owner's control", "your control"), ("next end step", "next upkeep"), ("exile Norin", "exile another creature"),
        ):
            with self.subTest(new=new):
                changed = replace(card, oracle_text=card.oracle_text.replace(old, new))
                self.assertNotEqual("exact", compile_oracle_card(
                    changed, capability_registry=self.registry, capability_profile="commander_review",
                ).status)
        game = self.session(60307001)
        card_obj = self.add(game.engine, CARD)
        self.assert_closed(game, card_obj)
        program, = game.engine.semantics.programs_for_oracle(card.oracle_id)
        altered = cast_or_attack_source_exile_condition()
        altered["event_subscriptions"][1]["event"] = "creature.blocks"
        result = bind_semantic_program_runtime(replace(program, event_condition=altered),
                                               capability_registry=self.registry, profile="commander_review")
        self.assertFalse(result["strict"])

    def test_own_attack_other_attack_and_cast_share_one_actual_owner_return(self):
        game = self.session(50804007)
        source = self.add(game.engine, CARD, seat="B", zone="hand")
        game.engine.move_card(source.object_id, "battlefield", controller="A", log=False)
        source = game.state.cards[source.object_id]
        source.acquired_control_turn_count = -1
        companion = self.add(game.engine, "Grizzly Bears")
        companion.acquired_control_turn_count = -1
        bolt = self.add(game.engine, "Lightning Bolt", zone="hand", seat="B")
        self.add(game.engine, "Mountain", seat="B")
        self.start(game, source, bolt)

        battlefield_identity = source.logical_object_id
        self.assertEqual(("B", "A"), (source.owner, source.controller))
        self.attack(game, [(source, "C"), (companion, "C")])
        self.assertIn(source.object_id, game.state.combat.attackers)
        self.assertEqual(2, self.trigger_count(game, source))
        self.cast_for(game, "B", bolt, ["D"])
        self.assertEqual(3, self.trigger_count(game, source))
        game = self.replay(game, load=True)
        self.resolve_top(game)
        exiled = game.state.cards[source.object_id]
        self.assertEqual("exile", exiled.zone)
        self.assertNotEqual(battlefield_identity, exiled.logical_object_id)
        self.assertNotIn(source.object_id, game.state.combat.attackers)
        self.assertIn(companion.object_id, game.state.combat.attackers)
        self.assertIsNone(self.resolve(game))
        pending, = self.return_triggers(game)
        self.assertEqual("A", pending.controller)
        self.assertEqual({"step": "end_step"}, pending.condition)
        delayed_effect, = pending.stack_template["context"]["dynamic_effects"]
        exile_identity, = delayed_effect["objects"]
        self.assertEqual(exiled.logical_object_id, exile_identity["logical_object_id"])
        self.assertEqual(source.object_id, exile_identity["object_id"])
        game = self.replay(game, load=True)

        turn = game.state.turn_sequence
        self.until(game, lambda s: any(item.label == RETURN_LABEL for item in s.state.stack))
        self.assertEqual((turn, "A", "end_step"), (game.state.turn_sequence, game.state.active_player, game.state.step))
        self.resolve_top(game)
        returned = game.state.cards[source.object_id]
        self.assertEqual(("battlefield", "B", "B"), (returned.zone, returned.owner, returned.controller))
        self.assertNotIn(returned.logical_object_id, {battlefield_identity, exile_identity["logical_object_id"]})
        self.assertFalse(self.return_triggers(game))
        self.replay(game, load=True)

    def test_source_priority_yield_projection_is_opaque_scoped_and_target_aware(self):
        for targeted in (False, True):
            with self.subTest(targeted=targeted):
                game = self.session(60307017 + targeted)
                source = self.add(game.engine, CARD)
                bolt = self.add(game.engine, "Lightning Bolt", seat="B", zone="hand")
                self.add(game.engine, "Mountain", seat="B")
                self.start(game, source, bolt)

                self.cast_for(game, "B", bolt, [source.ref if targeted else "D"])
                first = game.packet("pilot:A", full=True)["state"]["stack"][0]
                self.assertEqual("triggered_ability", first["kind"])
                offer = first["source_priority_yield"]
                self.assertEqual({"id", "targeted"}, set(offer))
                self.assertEqual(targeted, offer["targeted"])
                self.assertRegex(offer["id"], r"^[0-9a-f]{64}$")
                self.assertNotEqual(source.object_id, offer["id"])
                for principal in ("pilot:B", "pilot:C", "spectator"):
                    visible = game.packet(principal, full=True)["state"]["stack"]
                    self.assertFalse(any("source_priority_yield" in row for row in visible), principal)
                resumed = self.replay(game, load=True)
                later = resumed.packet("pilot:A", full=True)["state"]["stack"][0]
                self.assertEqual(offer, later["source_priority_yield"])

                self.resolve_top(resumed)
                next_top = resumed.packet("pilot:A", full=True)["state"]["stack"][0]
                self.assertEqual("spell", next_top["kind"])
                self.assertNotIn("source_priority_yield", next_top)

    def test_earlier_activation_removal_cannot_schedule_a_false_return(self):
        game = self.session(40007009)
        source = self.add(game.engine, CARD)
        revitalize = self.add(game.engine, "Revitalize", zone="hand")
        pyromancer = self.add(game.engine, "Prodigal Pyromancer", seat="B")
        pyromancer.acquired_control_turn_count = -1
        for index in range(3):
            self.add(game.engine, "Plains", ref=f"cast-observer-mana-{index}")
        self.start(game, source, revitalize, pyromancer)

        self.cast_for(game, "A", revitalize)
        self.assertEqual(1, self.trigger_count(game, source))
        self.priority(game, "B")
        legal = game.packet("pilot:B", full=True)["decision"]["ctx"]["legal"]["actions"]
        action, = (row["id"] for row in legal if row["id"].startswith(f"activate:{pyromancer.ref}:"))
        dealt = game.act("pilot:B", {"action_id": action, "targets": [source.ref], "pay": "auto"})
        self.assertTrue(dealt.ok, dealt.summary)
        self.assertEqual(1, self.trigger_count(game, source))
        self.resolve_top(game)
        self.assertEqual("graveyard", game.state.cards[source.object_id].zone)
        self.assertFalse(self.return_triggers(game))
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("graveyard", game.state.cards[source.object_id].zone)
        self.assertFalse(self.return_triggers(game))
        self.replay(game, load=True)
