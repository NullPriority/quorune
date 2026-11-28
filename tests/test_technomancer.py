"""Native optional paid power and positive artifact-count activation for Technomancer."""

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.power_sacrifice_model import ENTRY_SACRIFICE_POWER_TREASURE_CAPABILITY, ENTRY_SACRIFICE_POWER_TREASURE_FIELD
from quorune.record import authoritative_state_hash
from quorune.rules.announced_artifact_sacrifice_return import ARTIFACT_SACRIFICE_X_CAPABILITY
from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry
from scripts.build_test_database import build_fixture_database


class TechnomancerTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        database = Path(cls.temporary.name) / "technomancer.sqlite3"
        fixtures = ROOT / "tests/fixtures"
        build_fixture_database([fixtures / "bound-effect-program-cards.json", fixtures / "technomancer-cards.json"], database)
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        commander = "Generic Bound Commander"
        cls.deck = DeckDefinition("Paid power and artifact costs", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], [commander])

    def test_technomancer_requires_both_closed_original_abilities(self):
        card = self.db.lookup("Ruthless Technomancer", fuzzy=False)
        ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile="commander_review")
        self.assertEqual("exact", ir.status, ir.material_residuals)
        trigger, activation = ir.faces[0].nodes
        self.assertIn(ENTRY_SACRIFICE_POWER_TREASURE_FIELD, trigger.effects[0])
        self.assertIn(ENTRY_SACRIFICE_POWER_TREASURE_CAPABILITY, trigger.capability_dependencies)
        self.assertIn(ARTIFACT_SACRIFICE_X_CAPABILITY, activation.capability_dependencies)
        self.assertEqual("x", activation.cost["choices"][0]["count_parameter"])
        for changed in (
            card.oracle_text.replace("another creature", "a creature"),
            card.oracle_text.replace("that creature's power", "that creature's toughness"),
            card.oracle_text.replace("power X or less", "mana value X or less"),
            card.oracle_text.replace("X can't be 0.", ""),
        ):
            self.assertNotEqual("exact", compile_oracle_card(replace(card, oracle_text=changed), capability_registry=self.registry,
                                                                 capability_profile="commander_review").status)

        registry_path = ROOT / "quorune/rules/capability-registry.json"
        for dependency in (ENTRY_SACRIFICE_POWER_TREASURE_CAPABILITY, ARTIFACT_SACRIFICE_X_CAPABILITY,
                           "zone.reanimate.fixed_target", "token.creation.fixed_definition"):
            data = json.loads(registry_path.read_text(encoding="utf-8"))
            entry = next(entry for entry in data["capabilities"] if entry["id"] == dependency)
            entry.update(status="blocked", blockers=["Focused Technomancer dependency mutation"])
            registry = CapabilityRegistry(data)
            registry.mark_evidence_verified(self.registry.evidence_fingerprint)
            with self.subTest(dependency=dependency):
                self.assertNotEqual("exact", compile_oracle_card(card, capability_registry=registry, capability_profile="commander_review").status)

    def _action(self, game, seat, card, kind):
        packet = game.packet("pilot:" + seat, full=True)
        return next(action for action in packet["decision"]["ctx"]["legal"]["actions"]
                    if action.get("action") == kind and (action.get("source") == card.ref
                                                         or action.get("card") == card.ref))

    def _reject(self, game, seat, command):
        before = authoritative_state_hash(game.state)
        receipt = game.act("pilot:" + seat, command)
        self.assertFalse(receipt.ok, receipt.summary)
        self.assertEqual(before, authoritative_state_hash(game.state))

    def _treasures(self, game):
        return [game.state.cards[oid] for oid in game.state.players["A"].zones["battlefield"]
                if "Treasure" in game.state.cards[oid].printed_name]

    def test_technomancer_entry_creates_exact_treasure_from_replaced_paid_power(self):
        game = self.session(111_210_01)
        engine = game.engine
        source = self.add(engine, "Ruthless Technomancer", zone="hand", ref="TECHNOMANCER")
        spider = self.add(engine, "Giant Spider", ref="PAID-SPIDER")
        foreign = self.add(engine, "Grizzly Bears", seat="D", ref="FOREIGN-BEARS")
        growth = self.add(engine, "Giant Growth", zone="hand", ref="CHANGE-POWER")
        self.add(engine, "Dauthi Voidwalker", seat="B", ref="REPLACEMENT")
        self.assert_closed(game, source)
        self.prepare(game, mana={"B": 4, "G": 1})
        self.checkpoint(game)
        self.cast_for(game, "A", source, reserve={"G": 1})
        self.resolve_top(game)
        self.cast_for(game, "A", growth, [spider.ref])
        self.resolve_top(game)
        pending = self.resolve(game)
        self.assertEqual(("choice.apnap", ["A"]), (pending.kind, pending.actors))
        for ref in (source.ref, foreign.ref):
            self._reject(game, "A", {"action_id": "choose", "cards": [ref]})
        self._reject(game, "B", {"action_id": "choose", "cards": [spider.ref]})
        declined = self.replay(game, load=True)
        passed = declined.act("pilot:A", {"action_id": "choose", "cards": []})
        self.assertTrue(passed.ok, passed.summary)
        self.assertIsNone(self.resolve(declined))
        self.assertFalse(self._treasures(declined))
        self.assertEqual("battlefield", declined.state.cards[spider.object_id].zone)
        self.replay(declined, load=True)

        paid = game.act("pilot:A", {"action_id": "choose", "cards": [spider.ref]})
        self.assertTrue(paid.ok, paid.summary)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("exile", game.state.cards[spider.object_id].zone)
        treasure = self._treasures(game)
        self.assertEqual(5, len(treasure))
        self.assertTrue(all(game.engine._effective_type_parts(card)[1] == {"treasure"} for card in treasure))
        self.replay(game, load=True)

    def test_technomancer_uses_one_positive_x_for_cost_and_power_target_through_replay(self):
        game = self.session(111_210_02)
        engine = game.engine
        source = self.add(engine, "Ruthless Technomancer", ref="ARTIFACT-SOURCE-TO-BE")
        coating = self.add(engine, "Liquimetal Coating", ref="TYPE-CHANGER")
        own_artifact = self.add(engine, "Memnite", ref="OWN-ARTIFACT")
        foreign = self.add(engine, "Memnite", seat="C", ref="FOREIGN-ARTIFACT")
        spider = self.add(engine, "Giant Spider", zone="graveyard", ref="POWER-TWO-MANA-FOUR")
        ornithopter = self.add(engine, "Ornithopter", zone="graveyard", ref="POWER-ZERO")
        enemy_grave = self.add(engine, "Giant Spider", seat="C", zone="graveyard", ref="FOREIGN-GRAVE")
        removal = self.add(engine, "Purify the Grave", seat="B", zone="hand", ref="TARGET-REMOVAL")
        game.state.players["B"].mana_pool["W"] = 1
        self.assert_closed(game, source)
        self.assert_closed(game, coating)
        self.prepare(game, mana={"B": 3})
        self.checkpoint(game)
        change = self._action(game, "A", coating, "activate")
        cast = game.act("pilot:A", {"action_id": change["id"], "targets": [source.ref], "pay": "auto"})
        self.assertTrue(cast.ok, cast.summary)
        self.resolve_top(game)
        self.assertIn("artifact", engine._effective_type_parts(source)[0])

        action = self._action(game, "A", source, "activate")
        self.assertEqual({"minimum": 2, "maximum": 3}, action["target_schema"]["x_bounds_by_ref"][spider.ref])
        self.assertEqual(1, action["target_schema"]["x_bounds_by_ref"][ornithopter.ref]["minimum"])
        self.assertNotIn(enemy_grave.ref, action["target_schema"]["x_bounds_by_ref"])
        self.assertIn(source.ref, action["choice_schema"]["cost_cards"]["legal_refs"])
        command = {"action_id": action["id"], "targets": [spider.ref], "cost_cards": [source.ref, own_artifact.ref],
                   "x": 2, "pay": "auto"}
        for overrides in (
            {"x": 0, "targets": [ornithopter.ref], "cost_cards": []},
            {"x": 1, "cost_cards": [own_artifact.ref]},
            {"cost_cards": [source.ref, source.ref]},
            {"cost_cards": [own_artifact.ref, foreign.ref]},
            {"targets": [enemy_grave.ref]},
            {"cost_cards": [source.ref]},
            {"targets": [own_artifact.ref]},
        ):
            self._reject(game, "A", {**command, **overrides})
        accepted = game.act("pilot:A", command)
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertEqual(2, game.state.stack[-1].x_value)
        for card in (source, own_artifact):
            self.assertEqual("graveyard", game.state.cards[card.object_id].zone)
        restored = self.replay(game, load=True)
        self.resolve_top(restored)
        revived = restored.state.cards[spider.object_id]
        self.assertEqual(("battlefield", "A"), (revived.zone, revived.controller))
        self.replay(restored, load=True)

        self.assertTrue(game.act("pilot:A", {"action_id": "pass"}).ok)
        remove = self._action(game, "B", removal, "cast")
        outcome = game.act("pilot:B", {"action_id": remove["id"], "targets": [spider.ref], "pay": "auto"})
        self.assertTrue(outcome.ok, outcome.summary)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("exile", game.state.cards[spider.object_id].zone)
        self.assertEqual("graveyard", game.state.cards[own_artifact.object_id].zone)
        self.replay(game, load=True)

    def test_technomancer_keeps_x_in_a_suspended_artifact_sacrifice(self):
        game = self.session(111_210_03)
        engine = game.engine
        source = self.add(engine, "Ruthless Technomancer", ref="COUNT-SOURCE")
        spider = self.add(engine, "Giant Spider", zone="graveyard", ref="COUNT-TARGET")
        payment = [self.add(engine, "Memnite", ref=f"COUNT-ARTIFACT-{index}") for index in range(2)]
        self.add(engine, "Dauthi Voidwalker", seat="B", ref="WALKER-B")
        self.add(engine, "Dauthi Voidwalker", seat="C", ref="WALKER-C")
        self.prepare(game, mana={"B": 3})
        self.checkpoint(game)
        action = self._action(game, "A", source, "activate")
        played = game.act("pilot:A", {"action_id": action["id"], "x": 2,
                                    "targets": [spider.ref], "cost_cards": [card.ref for card in payment],
                                    "pay": "auto"})
        self.assertTrue(played.ok, played.summary)
        self.assertEqual("replacement.order", game.state.pending_decision.kind)
        game = self.replay(game, load=True)
        for _ in range(4):
            pending = game.state.pending_decision
            if pending is None or pending.kind != "replacement.order":
                break
            self.assertEqual(["A"], pending.actors)
            options = game.packet("pilot:A", full=True)["decision"]["ctx"]["options"]
            selected = game.act("pilot:A", {"action_id": "choose", "replacement": options[0]["id"]})
            self.assertTrue(selected.ok, selected.summary)
        self.assertEqual(2, game.state.stack[-1].x_value)
        self.assertTrue(all(game.state.cards[card.object_id].zone == "exile" for card in payment))
        self.assertIsNone(self.resolve(game))
        self.assertEqual("battlefield", game.state.cards[spider.object_id].zone)
        self.replay(game, load=True)
