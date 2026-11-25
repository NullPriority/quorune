"""The original opposing player chooses one current, untargeted counter recipient."""

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.counter_placement_targets import OPPONENT_CREATURE_COUNTER_CHOICE_CAPABILITY
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry
from quorune.rules.evidence import validate_capability_evidence_index
from quorune.rules.evidence_storage import read_evidence_document
from scripts.build_test_database import build_fixture_database
from scripts.update_capability_evidence import build_index, discover_tests


class TargetOpponentCounterChoiceTests(InheritedNativeFixture, unittest.TestCase):
    envoy = "Trial Bitter Envoy"
    body = "Trial Refuge Creature"
    veiled = "Trial Veiled Creature"
    brake = "Trial Counter Impasse"
    basic = "Generic Bound Plains"

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        d = Path(cls.temporary.name) / "opponent-counter.sqlite3"
        fixtures = ROOT / "tests/fixtures"
        build_fixture_database([
            fixtures / "bound-effect-program-cards.json",
            fixtures / "source-counter-reflexive-cards.json",
            fixtures / "target-opponent-counter-choice-cards.json",
        ], d)
        cls.db = CardDatabase(d)
        cls.registry = load_default_capability_registry()
        ruler = "Generic Bound Commander"
        cls.deck = DeckDefinition("Opponent counter",
            [DeckEntry(ruler, 1, "commander"), DeckEntry(cls.basic, 30)], [ruler])

    def scene(self, seed, *, empty=False, prevented=False):
        game = self.session(seed)
        source = self.add(game.engine, self.envoy, zone="hand", ref="SOURCE-CAST")
        foreign = self.add(game.engine, self.body, seat="C", ref="OTHER-PLAYER-CREATURE")
        hidden = self.add(game.engine, self.body, seat="D", zone="hand", ref="HIDDEN-FOREIGN-CARD")
        if not empty:
            ordinary = self.add(game.engine, self.body, seat="B", ref="CHOOSERS-ORDINARY-CREATURE")
            veiled = self.add(game.engine, self.veiled, seat="B", ref="UNTARGETABLE-CREATURE")
            self.assert_closed(game, veiled)
        else:
            ordinary = veiled = None
        self.assert_closed(game, source)
        if prevented:
            prohibition = self.add(game.engine, self.brake, seat="C", ref="PLACEMENT-PROHIBITION")
            self.assert_closed(game, prohibition)
        game.state.config.semantic_policy = "trusted_only"
        self.prepare(game, mana={"C": 1, "B": 2})
        self.checkpoint(game)
        self.cast(game, "A", source)
        pending = self.resolve(game)
        self.assertEqual(("semantic.target", ["A"]), (pending.kind, pending.actors))
        selected = game.act("pilot:A", {"action_id": "choose", "targets": ["B"]})
        self.assertTrue(selected.ok, selected.summary)
        trigger, = game.state.stack
        self.assertEqual(["B"], trigger.targets)
        return game, source, ordinary, veiled, foreign, hidden

    def test_announced_opponent_must_choose_their_own_even_untargetable_creature(self):
        game, _, ordinary, veiled, foreign, hidden = self.scene(11510301)
        pending = self.resolve(game)
        self.assertEqual(("semantic.choice", ["B"]), (pending.kind, pending.actors))
        request = game.packet("pilot:B", full=True)["decision"]
        self.assertEqual({ordinary.ref, veiled.ref}, {value["id"] for value in request["ctx"]["objects"]})
        self.assertNotIn(hidden.ref, json.dumps(request))
        self.assertIsNone(game.packet("pilot:A", full=True)["decision"])
        before = authoritative_state_hash(game.state)
        valid = {"action_id": "choose", "objects": [veiled.ref]}
        for foreign_pilot in ("pilot:A", "pilot:C", "pilot:D"):
            self.assertFalse(game.act(foreign_pilot, valid).ok)
        for malformed in ([], [foreign.ref], [veiled.ref, ordinary.ref]):
            self.assertFalse(game.act("pilot:B", {**valid, "objects": malformed}).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        game = self.replay(game, load=True)
        counters = game.act("pilot:B", valid)
        self.assertTrue(counters.ok, counters.summary)
        self.assertIsNone(self.resolve(game))
        selected = game.state.cards[veiled.object_id]
        self.assertEqual(1, selected.counters["deathtouch"])
        self.assertIn("deathtouch", game.engine._combat_keywords(selected))
        self.assertEqual(0, game.state.cards[ordinary.object_id].counters.get("deathtouch", 0))
        self.replay(game, load=True)

    def test_no_eligible_recipient_finishes_the_original_trigger(self):
        for blocked in (False, True):
            with self.subTest(counter_prohibition=blocked):
                game, source, ordinary, veiled, foreign, _ = self.scene(11510302+blocked, empty=not blocked, prevented=blocked)
                self.assertIsNone(self.resolve(game))
                self.assertEqual("battlefield", game.state.cards[source.object_id].zone)
                for card in (ordinary, veiled, foreign):
                    if card is not None:
                        self.assertEqual(0, game.state.cards[card.object_id].counters.get("deathtouch", 0))
                self.replay(game, load=True)

    def test_admission_requires_the_opponent_target_and_exact_untargeted_counter(self):
        row = self.db.lookup(self.envoy)

        def compile_card(card, registry):
            return compile_oracle_card(card, capability_registry=registry, capability_profile="commander_review")

        ir = compile_card(row, self.registry)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        node, = (n for n in ir.faces[0].nodes if n.event == "permanent.enter.self")
        self.assertEqual(["player"], node.target_schema["zones"])
        self.assertEqual("opponent", node.target_schema["player_relation"])
        self.assertEqual("$target.0", node.effects[0]["player"])
        self.assertIn(OPPONENT_CREATURE_COUNTER_CHOICE_CAPABILITY, node.capability_dependencies)
        for text in (row.oracle_text.replace("target opponent", "each opponent"),
                     row.oracle_text.replace("deathtouch counter", "flying counter"),
                     row.oracle_text.replace("on a creature they control", "on a creature you control")):
            self.assertNotEqual("exact", compile_card(replace(row, oracle_text=text), self.registry).status)
        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        owned = next(x for x in raw["capabilities"] if x["id"] == OPPONENT_CREATURE_COUNTER_CHOICE_CAPABILITY)
        owned.update(status="blocked", blockers=["Withdraw the exact opponent counter-choice authority."])
        registry = CapabilityRegistry(raw)
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        index = build_index(registry_value=raw, declaration_source=declarations,
                            discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
        self.assertNotEqual("exact", compile_card(row, registry).status)
        with patch("quorune.compiler.target_opponent_counter_choice.target_opponent_counter_choice_result",
                   return_value=None):
            self.assertNotEqual("exact", compile_card(row, self.registry).status)
