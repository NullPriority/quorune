"""The legally resolving Equipment trigger gains life before its optional return."""

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.fixed_effect_clause_shapes import fixed_optional_effect_node_capabilities
from quorune.rules.fixed_homogeneous_target_set_capability_shapes import OPTIONAL_GRAVEYARD_TARGET_RETURN_VERSION
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


ORIGINAL = "Sword of Light and Shadow"


class EquippedLifeAndOptionalGraveyardReturnTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        database = Path(cls.temporary.name) / "equipped-life-and-optional-return.sqlite3"
        build_fixture_database([
            ROOT / "tests/fixtures/equipped-life-and-optional-graveyard-return-cards.json",
        ], database)
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Equipped life and optional return", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def test_complete_original_keeps_the_fixed_life_before_the_optional_own_graveyard_target(self):
        outcome, semantics = register_card(self.db, self.registry, ORIGINAL)
        self.assertTrue(outcome["strict_capability_ready"], outcome["blockers"])
        record = self.db.lookup(ORIGINAL, fuzzy=False)
        programs = semantics.programs_for_oracle(record.oracle_id)
        trigger, = (program for program in programs if program.event == "damage.dealt")
        self.assertGreater(len(programs), 1)
        self.assertEqual({
            "zones": ["graveyard"], "categories": ["card"], "owner_relation": "you",
            "types_any": ["creature"], "up_to": 1,
        }, trigger.target_schema)
        life, returning = trigger.effects
        self.assertEqual({"op": "life", "player": "$controller", "delta": 3}, life)
        self.assertEqual(("offer_optional_effect", "$controller"), (returning["op"], returning["player"]))
        self.assertEqual(OPTIONAL_GRAVEYARD_TARGET_RETURN_VERSION, returning["schema_version"])
        for malformed, schema in (
            (dict(returning, schema_version=True), trigger.target_schema),
            (returning, {**trigger.target_schema, "up_to": 2}),
            (dict(returning, effects=[{"op": "life", "player": "$controller", "delta": 3}]), trigger.target_schema),
        ):
            self.assertFalse(fixed_optional_effect_node_capabilities(
                effects=(malformed,), target_schema=schema, mechanic_ids=trigger.coverage,
            ))
        self.assertEqual([{"op": "return_graveyard_targets_to_owner_hand", "cards": "$targets",
                           "maximum_targets": 1}], returning["effects"])
        self.assertLessEqual({"life.change.effect", "effect.choice.optional_fixed",
                              "card.return.own_graveyard_to_owner_hand", "target.revalidate_resolution"},
                             set(trigger.capability_dependencies))

    def activate(self, game, seat, source, *, targets):
        self.priority(game, seat)
        prefix = f"activate:{source.ref}:"
        legal = game.packet(f"pilot:{seat}", full=True)["decision"]["ctx"]["legal"]["actions"]
        action = next(row for row in legal if row["id"].startswith(prefix))
        result = game.act(f"pilot:{seat}", {"action_id": action["id"], "targets": list(targets), "pay": "auto"})
        self.assertTrue(result.ok, result.summary)

    def scene(self, seed, *, crypt=False):
        game = self.session(seed)
        sword = self.add(game.engine, ORIGINAL, zone="hand")
        carrier = self.add(game.engine, "Runeclaw Bear", ref="light-shadow-carrier")
        carrier.acquired_control_turn_count = -1
        target = self.add(game.engine, "Runeclaw Bear", zone="graveyard", ref="light-shadow-own-creature")
        foreign = self.add(game.engine, "Runeclaw Bear", seat="B", zone="graveyard", ref="light-shadow-foreign")
        noncreature = self.add(game.engine, "Island", zone="graveyard", ref="light-shadow-own-land")
        removal = self.add(game.engine, "Tormod's Crypt", seat="B") if crypt else None
        self.assert_closed(game, sword)
        if removal is not None:
            self.assert_closed(game, removal)
        self.prepare(game, mana={"C": 5})
        self.checkpoint(game)
        self.cast(game, "A", sword)
        self.resolve_top(game)
        self.activate(game, "A", sword, targets=[carrier.ref])
        self.resolve_top(game)
        self.assertEqual(carrier.object_id, game.state.cards[sword.object_id].attached_to)
        self.assertEqual(4, game.engine._numeric_stat(carrier.object_id, "power"))
        self.attack(game, [(carrier, "B")])
        self.until(game, lambda current: current.state.pending_decision.kind == "semantic.target")
        self.assertEqual(["A"], game.state.pending_decision.actors)
        context = game.packet("pilot:A", full=True)["decision"]["ctx"]
        legal = context["target_schema"]["legal_refs"]
        self.assertIn(target.ref, legal)
        self.assertNotIn(foreign.ref, legal)
        self.assertNotIn(noncreature.ref, legal)
        before = authoritative_state_hash(game.state)
        for actor, refs in (("B", [target.ref]), ("A", [foreign.ref]), ("A", [noncreature.ref])):
            self.assertFalse(game.act(f"pilot:{actor}", {"action_id": "choose", "targets": refs}).ok)
            self.assertEqual(before, authoritative_state_hash(game.state))
        self.assertEqual(36, game.state.players["B"].life)
        return game, target, removal

    def test_actual_zero_announced_targets_gains_life_without_a_return_choice(self):
        game, target, _ = self.scene(60820221)
        self.target(game, "A", [])
        self.assertIsNone(self.resolve(game))
        self.assertEqual(43, game.state.players["A"].life)
        self.assertEqual("graveyard", game.state.cards[target.object_id].zone)
        self.replay(game, load=True)

    def test_actual_acceptance_or_decline_happens_after_the_same_life_gain(self):
        for index, choice in enumerate(("apply", "decline")):
            with self.subTest(choice=choice):
                game, target, _ = self.scene(60820222 + index)
                self.target(game, "A", [target.ref])
                pending = self.resolve(game)
                self.assertEqual(("semantic.choice", ["A"]), (pending.kind, pending.actors))
                self.assertEqual(43, game.state.players["A"].life)
                self.assertEqual("graveyard", game.state.cards[target.object_id].zone)
                command = {"action_id": "choose", "choice": choice}
                before = authoritative_state_hash(game.state)
                self.assertFalse(game.act("pilot:B", command).ok)
                self.assertEqual(before, authoritative_state_hash(game.state))
                game = self.replay(game, load=True)
                result = game.act("pilot:A", command)
                self.assertTrue(result.ok, result.summary)
                self.assertIsNone(self.resolve(game))
                self.assertEqual(43, game.state.players["A"].life)
                self.assertEqual("hand" if choice == "apply" else "graveyard", game.state.cards[target.object_id].zone)
                self.replay(game, load=True)

    def test_actual_sole_target_exiled_before_resolution_stops_even_the_life_gain(self):
        game, target, crypt = self.scene(60820224, crypt=True)
        self.target(game, "A", [target.ref])
        self.activate(game, "B", crypt, targets=["A"])
        self.resolve_top(game)
        self.assertEqual("exile", game.state.cards[target.object_id].zone)
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(40, game.state.players["A"].life)
        self.assertEqual("exile", game.state.cards[target.object_id].zone)
        self.replay(game, load=True)
