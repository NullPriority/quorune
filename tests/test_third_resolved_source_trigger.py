"""An original death trigger's third completed resolution and separate optional fight."""

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.ability_resolution_ordinal import ABILITY_RESOLVED_HISTORY
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.reflexive_trigger_model import THIRD_SOURCE_TRIGGER_FIELD
from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry
from quorune.rules.evidence import validate_capability_evidence_index
from quorune.rules.evidence_storage import read_evidence_document
from quorune.rules.third_resolved_source_trigger import THIRD_SOURCE_TRIGGER_CAPABILITY
from scripts.build_test_database import build_fixture_database
from scripts.update_capability_evidence import build_index, discover_tests


class ThirdResolvedSourceTriggerTests(InheritedNativeFixture, unittest.TestCase):
    scout = "Trial Memorial Warden"
    body = "Trial Refuge Creature"
    altar = "Ashnod's Altar"
    bounce = "Trial Trail Retreat"
    brake = "Trial Counter Impasse"
    amplifier = "Trial Counter Amplifier"
    basic = "Generic Bound Plains"

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "third-resolved-trigger.sqlite3"
        fixtures = ROOT / "tests/fixtures"
        build_fixture_database([
            fixtures / "bound-effect-program-cards.json",
            fixtures / "activated-self-transform-cards.json",
            fixtures / "source-counter-reflexive-cards.json",
            fixtures / "source-optional-creature-unblockable-cards.json",
            fixtures / "third-resolved-source-trigger-cards.json",
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = "Generic Bound Commander"
        cls.deck = DeckDefinition("Resolving death trigger", [
            DeckEntry(commander, 1, "commander"), DeckEntry(cls.basic, 30),
        ], [commander])

    def scene(self, seed, *, suppress=False, increases=0):
        game = self.session(seed)
        source = self.add(game.engine, self.scout, ref="ORIGINAL-RESOLVING-SOURCE")
        altar = self.add(game.engine, self.altar, ref="REPEATABLE-SACRIFICE-ACTION")
        friends = [self.add(game.engine, self.body, ref=f"OWN-CREATURE-{i}") for i in range(5)]
        opponent = self.add(game.engine, self.body, seat="B", ref="FIGHT-RECIPIENT")
        bounce = self.add(game.engine, self.bounce, seat="B", zone="hand", ref="PRIVATE-BOUNCE")
        supports = [self.add(game.engine, self.brake)] if suppress else []
        for i in range(increases):
            supports.append(self.add(game.engine, self.amplifier, ref=f"COUNTER-AMPLIFIER-{i}"))
        for card in (source, bounce, *supports):
            self.assert_closed(game, card)
        game.state.config.semantic_policy = "trusted_only"
        self.prepare(game)
        game.state.players["B"].mana_pool["U"] = 1
        self.checkpoint(game)
        return game, source, altar, friends, opponent, bounce

    def sacrifice(self, game, altar, creature):
        self.priority(game, "A")
        action, = (row for row in game.packet("pilot:A", full=True)["decision"]["legal_actions"]
                   if row["id"].startswith(f"activate:{altar.ref}:"))
        submitted = game.act("pilot:A", {"action_id": action["id"], "cost_cards": [creature.ref]})
        self.assertTrue(submitted.ok, submitted.summary)
        self.assertEqual("graveyard", game.state.cards[creature.object_id].zone)

    def history_count(self, game, source):
        return sum(event.object_incarnation == source.logical_object_id
                   for event in game.engine._current_turn_history(ABILITY_RESOLVED_HISTORY))

    def choose(self, game, refs):
        answer = game.act("pilot:A", {"action_id": "choose", "targets": list(refs)})
        self.assertTrue(answer.ok, answer.summary)

    def test_third_completed_parent_announces_a_private_optional_fight_once(self):
        game, source, altar, friends, opponent, bounce = self.scene(60312031)
        for index, creature in enumerate(friends[:2], start=1):
            self.sacrifice(game, altar, creature)
            self.assertIsNone(self.resolve(game))
            self.assertEqual(index, self.history_count(game, source))
        self.sacrifice(game, altar, friends[2])
        parent, = game.state.stack
        self.assertEqual([], parent.targets)
        self.assertEqual(2, self.history_count(game, source))
        pending = self.resolve(game)
        self.assertEqual(("semantic.target", ["A"]), (pending.kind, pending.actors))
        child, = game.state.stack
        self.assertEqual("trigger.reflexive", child.context["event"])
        self.assertEqual(parent.ref, child.context["reflexive_parent"])
        self.assertEqual(3, self.history_count(game, source))
        self.assertNotIn(bounce.ref, json.dumps(game.packet("pilot:A", full=True)))
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:C", {"action_id": "choose", "targets": [opponent.ref]}).ok)
        self.assertFalse(game.act("pilot:A", {"action_id": "choose", "targets": [friends[3].ref]}).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        game = self.replay(game, load=True)
        self.choose(game, (opponent.ref,))
        self.assertIsNone(self.resolve(game))
        self.assertEqual("graveyard", game.state.cards[opponent.object_id].zone)
        self.assertEqual(2, game.state.cards[source.object_id].marked_damage)
        self.sacrifice(game, altar, friends[3])
        self.assertIsNone(self.resolve(game))
        self.assertEqual(4, self.history_count(game, source))
        self.assertEqual(4, game.state.cards[source.object_id].counters["+1/+1"])
        self.replay(game, load=True)

    def test_third_resolution_survives_counter_prevention_and_may_choose_zero(self):
        game, source, altar, friends, opponent, _ = self.scene(60312032, suppress=True)
        for creature in friends[:2]:
            self.sacrifice(game, altar, creature)
            self.assertIsNone(self.resolve(game))
        self.sacrifice(game, altar, friends[2])
        pending = self.resolve(game)
        self.assertEqual("semantic.target", pending.kind)
        self.assertEqual(3, self.history_count(game, source))
        self.assertEqual(0, game.state.cards[source.object_id].counters.get("+1/+1", 0))
        self.choose(game, ())
        self.assertIsNone(self.resolve(game))
        self.assertEqual("battlefield", game.state.cards[opponent.object_id].zone)
        self.assertEqual(0, game.state.cards[opponent.object_id].marked_damage)
        self.replay(game, load=True)

    def test_original_source_departure_prevents_both_fight_packets(self):
        game, source, altar, friends, opponent, bounce = self.scene(60312033)
        for creature in friends[:2]:
            self.sacrifice(game, altar, creature)
            self.assertIsNone(self.resolve(game))
        self.sacrifice(game, altar, friends[2])
        self.assertEqual("semantic.target", self.resolve(game).kind)
        self.choose(game, (opponent.ref,))
        self.cast(game, "B", bounce, [source.ref])
        self.resolve_top(game)
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("hand", game.state.cards[source.object_id].zone)
        self.assertEqual("battlefield", game.state.cards[opponent.object_id].zone)
        self.assertEqual(0, game.state.cards[opponent.object_id].marked_damage)
        self.replay(game, load=True)

    def test_pending_counter_order_keeps_the_third_resolution_pending(self):
        game, source, altar, friends, _, _ = self.scene(60312034, increases=2)
        for index, creature in enumerate(friends[:3]):
            self.sacrifice(game, altar, creature)
            pending = self.resolve(game)
            self.assertEqual("replacement.order", pending.kind)
            self.assertEqual(index, self.history_count(game, source))
            if index == 2:
                game = self.replay(game, load=True)
            option = game.packet("pilot:A", full=True)["decision"]["ctx"]["options"][0]["id"]
            chosen = game.act("pilot:A", {"action_id": "choose", "replacement": option})
            self.assertTrue(chosen.ok, chosen.summary)
            pending = self.resolve(game)
            self.assertEqual(index + 1, self.history_count(game, source))
            if index < 2:
                self.assertIsNone(pending)
            else:
                self.assertEqual("semantic.target", pending.kind)
        self.assertEqual(9, game.state.cards[source.object_id].counters["+1/+1"])
        self.choose(game, ())
        self.assertIsNone(self.resolve(game))
        self.replay(game, load=True)

    def test_admission_requires_original_death_trigger_third_ordinal_and_live_fight(self):
        row = self.db.lookup(self.scout)

        def compile_card(card, registry):
            return compile_oracle_card(card, capability_registry=registry, capability_profile="commander_review")

        ir = compile_card(row, self.registry)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        node, = (n for n in ir.faces[0].nodes if n.event == "creature.dies")
        self.assertIsNone(node.target_schema)
        self.assertIn(THIRD_SOURCE_TRIGGER_FIELD, node.effects[0])
        self.assertIn(THIRD_SOURCE_TRIGGER_CAPABILITY, node.capability_dependencies)
        for modified in (row.oracle_text.replace("third time", "fourth time"),
                         row.oracle_text.replace("another creature you control", "a creature you control"),
                         row.oracle_text + " Draw a card."):
            self.assertNotEqual("exact", compile_card(replace(row, oracle_text=modified), self.registry).status)
        data = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        cap = next(x for x in data["capabilities"] if x["id"] == THIRD_SOURCE_TRIGGER_CAPABILITY)
        cap.update(status="blocked", blockers=["Withdraw the exact third-resolution fight authority."])
        registry = CapabilityRegistry(data)
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        index = build_index(registry_value=data, declaration_source=declarations,
                            discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
        self.assertNotEqual("exact", compile_card(row, registry).status)
        with patch("quorune.compiler.third_resolved_source_trigger.third_resolved_source_trigger_result", return_value=None):
            self.assertNotEqual("exact", compile_card(row, self.registry).status)
