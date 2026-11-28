from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.attachments import attach_objects
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.random_activation_target import RANDOM_ACTIVATION_TARGET_CAPABILITY
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


class RandomActivationAnnouncementTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = TemporaryDirectory()
        path = Path(cls.directory.name) / "random-activation.sqlite3"
        fixture_dir = ROOT / "tests" / "fixtures"
        build_fixture_database([
            fixture_dir / "bound-effect-program-cards.json",
            fixture_dir / "flagbearer-target-requirements-cards.json",
            fixture_dir / "random-activation-announcement-cards.json",
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Random activation witness", [
            DeckEntry("Generic Bound Commander", 1, "commander"),
            DeckEntry("Generic Bound Plains", 30),
        ], ["Generic Bound Commander"])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.directory.cleanup()

    def offer(self, game, source):
        actions = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        return next((row for row in actions if row["id"].startswith("activate:"+source.ref+":")), None)

    def test_server_announces_the_random_target_and_ward_responds_before_damage(self):
        game = self.session(60220101)
        source = self.add(game.engine, "Kettle Sparkhand")
        flag = self.add(game.engine, "Standard Bearer", seat="B")
        armor = self.add(game.engine, "Leather Armor", seat="B")
        private = self.add(game.engine, "Generic Bound Growth", seat="C", zone="hand", ref="random-private-witness")
        for card in (source, flag, armor):
            self.assert_closed(game, card)
        attach_objects(game.state.cards, armor, flag, source_timestamp=armor.zone_timestamp,
                       players=game.state.players)
        game.state.config.semantic_policy = "trusted_only"
        self.prepare(game, mana={"C":1})
        self.checkpoint(game)

        action = self.offer(game, source)
        self.assertIsNotNone(action)
        self.assertNotIn("target_schema", action)
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:A", {"action_id":action["id"], "targets":[flag.ref], "pay":"auto"}).ok)
        self.assertFalse(game.act("pilot:D", {"action_id":action["id"], "pay":"auto"}).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        accepted = game.act("pilot:A", {"action_id":action["id"], "pay":"auto"})
        self.assertTrue(accepted.ok, accepted.summary)
        activated, = [item for item in game.state.stack if item.kind == "activated_ability"]
        self.assertEqual([flag.ref], activated.targets, "server announces the required target")
        ward, = [item for item in game.state.stack if item.semantic_key == "builtin:ward"]
        self.assertEqual(flag.object_id, ward.source_object_id)
        self.assertTrue(game.state.cards[source.object_id].tapped)
        self.assertEqual(0, game.state.cards[flag.object_id].marked_damage)
        self.assertNotIn(private.ref, json.dumps(game.packet("pilot:D", full=True)))
        self.assertEqual("semantic.choice", self.resolve(game).kind)
        game = self.replay(game, load=True)
        paid = game.act("pilot:A", {"action_id":"choose", "pay":True})
        self.assertTrue(paid.ok, paid.summary)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("graveyard", game.state.cards[flag.object_id].zone)
        self.assertEqual(0, game.state.players["A"].mana_pool["C"])
        self.replay(game, load=True)

    def test_mandatory_random_target_uses_ordinary_activation_price_and_rolls_back_rejections(self):
        for funding in (0,3):
            with self.subTest(funding=funding):
                game = self.session(60220110 + funding)
                source = self.add(game.engine, "Kettle Sparkhand")
                flag = self.add(game.engine, "Standard Bearer", seat="B")
                toll = self.add(game.engine, "Harbor Toll Lantern", seat="B")
                for card in (source, flag, toll):
                    self.assert_closed(game, card)
                game.state.config.semantic_policy = "trusted_only"
                self.prepare(game, mana={"C":funding})
                self.checkpoint(game)
                action=self.offer(game, source)
                if not funding:
                    self.assertIsNone(action)
                    before=authoritative_state_hash(game.state)
                    denied=game.act("pilot:A", {"action_id":"activate", "source":source.ref, "ability":"ab1", "pay":"auto"})
                    self.assertFalse(denied.ok)
                    self.assertEqual(before,authoritative_state_hash(game.state))
                    self.assertFalse(game.state.cards[source.object_id].tapped)
                else:
                    self.assertIsNotNone(action)
                    result=game.act("pilot:A", {"action_id":action["id"], "pay":"auto"})
                    self.assertTrue(result.ok,result.summary)
                    item,=game.state.stack
                    self.assertEqual([flag.ref],item.targets)
                    self.assertEqual(0,game.state.players["A"].mana_pool["C"])
                    self.assertIsNone(self.resolve(game))
                    self.assertEqual("graveyard",game.state.cards[flag.object_id].zone)
                    self.replay(game, load=True)

    def test_unpayable_legal_random_result_is_rejected_with_a_stable_draw(self):
        from quorune.rules.activation.proposal import build_activation_offer
        from quorune.rules.random_selection import sample_identity_rows

        game = self.session(60220125)
        source = self.add(game.engine, "Kettle Sparkhand")
        legal = self.add(game.engine, "Harbor Banner Keeper", seat="B")
        toll = self.add(game.engine, "Harbor Toll Lantern", seat="B")
        for card in (source, legal, toll):
            self.assert_closed(game, card)
        game.state.config.semantic_policy = "trusted_only"
        self.prepare(game)
        ability, = game.engine._activated_abilities(source)
        internal = build_activation_offer(game.engine, "A", source, ability)
        self.assertEqual("payable", internal.status)
        self.assertEqual({"A","B","C","D",source.ref,legal.ref}, set(internal.random_target_refs))
        self.assertNotIn(legal.ref, json.dumps(internal.offer.to_dict()["target_costs"]))
        rows = [{"object_id":"player:"+seat,"ref":seat} for seat in "ABCD"]
        rows.extend({"object_id":"permanent:"+card.object_id,"ref":card.ref} for card in (source,legal))
        revision=game.state.revision
        for seed in range(500):
            if all(
                sample_identity_rows(rows,1,seed=seed,seat="A",namespace="random-activation-target",
                    instruction_key=f"{revision+offset}|{source.logical_object_id}|{ability.ability_id}")[0]["ref"]==legal.ref
                for offset in (0,1)
            ):
                break
        else:
            self.fail("The fixture could not select the legal taxed target")
        game.state.config.seed=seed
        self.checkpoint(game)
        action=self.offer(game,source)
        self.assertIsNotNone(action)
        before=authoritative_state_hash(game.state)
        for _ in range(2):
            rejected=game.act("pilot:A",{"action_id":action["id"],"pay":"auto"})
            self.assertFalse(rejected.ok)
            self.assertEqual(before,authoritative_state_hash(game.state))
            self.assertFalse(game.state.cards[source.object_id].tapped)
            self.assertFalse(game.state.stack)
            self.assertEqual(0,game.state.players["A"].mana_pool["C"])

    def test_random_announcement_requires_its_typed_activation_and_target_capability(self):
        report, semantics = register_card(self.db, self.registry, "Kettle Sparkhand")
        self.assertTrue(report["strict_capability_ready"], report["blockers"])
        card=self.db.lookup("Kettle Sparkhand",fuzzy=False)
        program,=(program for program in semantics.programs_for_oracle(card.oracle_id)
                  if RANDOM_ACTIVATION_TARGET_CAPABILITY in program.capability_dependencies)
        deps=tuple(cap for cap in program.capability_dependencies if cap!=RANDOM_ACTIVATION_TARGET_CAPABILITY)
        removed=replace(program,capability_dependencies=deps,
                        capability_closure=self.registry.closure(deps,profile="commander_review").to_dict())
        result=bind_semantic_program_runtime(removed,capability_registry=self.registry,profile="commander_review")
        self.assertFalse(result["strict"])
        self.assertIn("capability:undeclared_runtime_dependency:"+RANDOM_ACTIVATION_TARGET_CAPABILITY,result["blockers"])
        wrong=replace(program,cost_schema={**program.cost_schema,"tap_source":False})
        result=bind_semantic_program_runtime(wrong,capability_registry=self.registry,profile="commander_review")
        self.assertFalse(result["strict"])
        self.assertTrue(any(x.startswith("runtime_random_activation_target:invalid:") for x in result["blockers"]))

    def test_muting_the_server_target_selection_breaks_the_native_announcement(self):
        test = type(self)("test_server_announces_the_random_target_and_ward_responds_before_damage")
        result = unittest.TestResult()
        with patch("quorune.rules.activation.proposal.choose_random_activated_target", return_value="A"):
            test.run(result)
        self.assertFalse(result.errors)
        self.assertEqual(1,len(result.failures))
