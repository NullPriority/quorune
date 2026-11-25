"""Two optional target phrases feed one simultaneous destruction."""

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
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.compound_target_sets import OPTIONAL_DESTROY_ROLES_VERSION_FIELD, optional_destroy_roles_schema
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


ORIGINAL = "Sword of Sinew and Steel"


class TwoOptionalDestructiveTargetRolesTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        database = Path(cls.temporary.name) / "two-optional-destruction.sqlite3"
        build_fixture_database([
            ROOT / "tests/fixtures/two-optional-destructive-target-roles-cards.json",
        ], database)
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Separate optional destruction roles", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def test_complete_original_preserves_one_trigger_and_two_exact_optional_roles(self):
        binding, semantics = register_card(self.db, self.registry, ORIGINAL)
        self.assertTrue(binding["strict_capability_ready"], binding["blockers"])
        original = self.db.lookup(ORIGINAL, fuzzy=False)
        programs = semantics.programs_for_oracle(original.oracle_id)
        trigger, = (program for program in programs if program.event == "damage.dealt")
        self.assertGreater(len(programs), 1)
        self.assertEqual(optional_destroy_roles_schema(), trigger.target_schema)
        effect, = trigger.effects
        self.assertEqual({"op": "destroy_targets", "cards": "$targets", "maximum_targets": 2,
                          "target_roles": True, OPTIONAL_DESTROY_ROLES_VERSION_FIELD: 1}, effect)
        self.assertIn("resolution.effect.fixed_compound_target_set", trigger.capability_dependencies)
        groups = [dict(entry) for entry in trigger.target_schema["groups"]]
        compulsory = [{**{key: value for key, value in entry.items() if key != "up_to"}, "count": 1}
                      for entry in groups]
        for changes in (
            {"effects": [{**effect, OPTIONAL_DESTROY_ROLES_VERSION_FIELD: True}]},
            {"effects": [{**effect, "maximum_targets": 3}]},
            {"effects": [{key: value for key, value in effect.items() if key != OPTIONAL_DESTROY_ROLES_VERSION_FIELD}]},
            {"target_schema": {"groups": compulsory}},
        ):
            bound = bind_semantic_program_runtime(
                replace(trigger, **changes), capability_registry=self.registry, profile="commander_review",
            )
            self.assertFalse(bound["strict"], bound)
        for text in (
            original.oracle_text.replace("target artifact", "target creature"),
            original.oracle_text.replace("up to one target artifact", "up to two target artifacts"),
            original.oracle_text.replace("Destroy", "Exile").replace("destroy", "exile"),
        ):
            self.assertNotEqual("exact", compile_oracle_card(
                replace(original, oracle_text=text), capability_registry=self.registry,
            ).status)

    def scene(self, seed, *, same_target=False, response=False):
        game = self.session(seed)
        sword = self.add(game.engine, ORIGINAL, zone="hand")
        carrier = self.add(game.engine, "Runeclaw Bear", ref="optional-role-equipment-carrier")
        carrier.acquired_control_turn_count = -1
        walker = self.add(game.engine, "Jace Beleren", seat="B")
        walker.counters["loyalty"] = 3
        artifact = self.add(game.engine, "Sol Ring", seat="C")
        coating = None
        if same_target:
            coating = self.add(game.engine, "Liquimetal Coating")
            self.assert_closed(game, coating)
        bounce = None
        if response:
            bounce = self.add(game.engine, "Boomerang", seat="B", zone="hand")
            for index in range(2):
                self.add(game.engine, "Island", seat="B", ref=f"optional-response-island-{index}")
            self.assert_closed(game, bounce)
        self.assert_closed(game, sword)
        self.prepare(game, mana={"C": 5})
        self.checkpoint(game)
        self.cast(game, "A", sword)
        self.resolve_top(game)
        self.activate(game, sword, carrier.ref)
        self.resolve_top(game)
        self.assertEqual(carrier.object_id, game.state.cards[sword.object_id].attached_to)
        self.assertEqual(4, game.engine._numeric_stat(carrier.object_id, "power"))
        if same_target:
            self.activate(game, coating, walker.ref)
            self.resolve_top(game)
            data = game.engine._effective_card_data(game.state.cards[walker.object_id])
            self.assertTrue({"artifact", "planeswalker"} <= game.engine._type_parts(data["type_line"])[0])
        self.attack(game, [(carrier, "B")])
        self.until(game, lambda current: current.state.pending_decision.kind == "semantic.target")
        self.assertEqual(36, game.state.players["B"].life)
        self.assertEqual(["A"], game.state.pending_decision.actors)
        return game, sword, walker, artifact, bounce

    def activate(self, game, card, target):
        self.priority(game, "A")
        prefix = f"activate:{card.ref}:"
        legal = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        action = next(row for row in legal if row["id"].startswith(prefix))
        applied = game.act("pilot:A", {"action_id": action["id"], "targets": [target], "pay": "auto"})
        self.assertTrue(applied.ok, applied.summary)

    def choose(self, game, targets, *, invalid=()):
        before = authoritative_state_hash(game.state)
        command = {"action_id": "choose", "targets": list(targets)}
        self.assertFalse(game.act("pilot:B", command).ok)
        for choices in invalid:
            self.assertFalse(game.act("pilot:A", {"action_id": "choose", "targets": list(choices)}).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        result = game.act("pilot:A", command)
        self.assertTrue(result.ok, result.summary)

    def groups(self, game):
        data = game.packet("pilot:A", full=True)["decision"]["ctx"]["target_schema"]
        return {row["id"]: row for row in data["groups"]}

    def test_actual_artifact_planeswalker_can_fill_both_roles_and_moves_only_once(self):
        game, _, walker, alternative, _ = self.scene(11530121, same_target=True)
        groups = self.groups(game)
        self.assertIn(walker.ref, groups["role_0"]["legal_refs"])
        self.assertIn(walker.ref, groups["role_1"]["legal_refs"])
        self.assertNotIn(alternative.ref, groups["role_0"]["legal_refs"])
        self.assertIn(alternative.ref, groups["role_1"]["legal_refs"])
        first = {"group": "role_0", "ref": walker.ref}
        second = {"group": "role_1", "ref": walker.ref}
        before = game.state.cards[walker.object_id].zone_change_counter
        self.choose(game, [first, second], invalid=(
            [first, first], [{"group": "role_0", "ref": alternative.ref}],
        ))
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        moved = game.state.cards[walker.object_id]
        self.assertEqual(("graveyard", before + 1), (moved.zone, moved.zone_change_counter))
        self.assertEqual(1, game.state.players["B"].zones["graveyard"].count(walker.object_id))
        self.assertEqual("battlefield", game.state.cards[alternative.object_id].zone)
        self.replay(game, load=True)

    def test_actual_trigger_accepts_no_targets_even_when_both_roles_have_choices(self):
        game, sword, walker, artifact, _ = self.scene(11530122)
        groups = self.groups(game)
        self.assertIn(walker.ref, groups["role_0"]["legal_refs"])
        self.assertIn(artifact.ref, groups["role_1"]["legal_refs"])
        self.choose(game, [])
        self.assertIsNone(self.resolve(game))
        self.assertTrue(all(game.state.cards[card.object_id].zone == "battlefield"
                            for card in (sword, walker, artifact)))
        self.replay(game, load=True)

    def test_actual_bounced_planeswalker_leaves_the_separate_artifact_role_legal(self):
        game, _, walker, artifact, bounce = self.scene(11530123, response=True)
        self.choose(game, [{"group": "role_0", "ref": walker.ref}, {"group": "role_1", "ref": artifact.ref}])
        self.cast_for(game, "B", bounce, [walker.ref])
        self.resolve_top(game)
        self.assertEqual("hand", game.state.cards[walker.object_id].zone)
        self.assertEqual("battlefield", game.state.cards[artifact.object_id].zone)
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("hand", game.state.cards[walker.object_id].zone)
        self.assertEqual("graveyard", game.state.cards[artifact.object_id].zone)
        self.replay(game, load=True)
