"""A source attack counts committed distinct discarding players."""

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
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.experience_results import EXPERIENCE_CAPABILITY
from scripts.build_test_database import build_fixture_database


CARD = "Azula, Ruthless Firebender"
FIXTURE = ROOT / "tests/fixtures/discarding-player-experience-cards.json"


class DiscardingPlayerExperienceTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = TemporaryDirectory()
        work = Path(cls.temp.name)
        root = ROOT / "tests/fixtures"
        base = root / "bound-effect-program-cards.json"
        main = {row["name"] for row in json.loads(base.read_bytes())["cards"]}
        neighbors = []
        for name, file in (("Mind Rot", "additional-conditional-modifier-cards.json"),
                           ("Swamp", "activation-restriction-material-cards.json"),
                           ("Mountain", "activation-result-views-cards.json")):
            if name not in main:
                card, = (row for row in json.loads((root / file).read_bytes())["cards"] if row["name"] == name)
                neighbors.append(card)
        subset = work / "neighbors.json"
        subset.write_text(json.dumps({"schema_version": 1, "cards": neighbors, "rulings": []}), encoding="utf8")
        db = work / "experience.sqlite3"
        build_fixture_database([base, FIXTURE, subset], db)
        cls.db = CardDatabase(db)
        cls.registry = load_default_capability_registry()
        cmd = "Generic Bound Commander"
        cls.deck = DeckDefinition("Discard and experience", [DeckEntry(cmd, 1, "commander"),
            DeckEntry("Generic Bound Plains", 30)], [cmd])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temp.cleanup()

    def test_exact_experience_results_require_distinct_discarders_and_original_power_shape(self):
        record = self.db.lookup(CARD, fuzzy=False)

        def compile(card):
            return compile_oracle_card(card, capability_registry=self.registry, capability_profile="commander_review")

        result = compile(record)
        self.assertEqual("exact", result.status, result.material_residuals)
        self.assertEqual(2, sum(EXPERIENCE_CAPABILITY in node.capability_dependencies for node in result.faces[0].nodes))
        for before, after in (("each player who discarded", "each card you discarded"),
                              ("experience counter you have", "experience counter an opponent of your choice has"),
                              ("gains menace", "gains trample")):
            changed = replace(record, oracle_text=record.oracle_text.replace(before, after))
            with self.subTest(after=after):
                self.assertNotEqual("exact", compile(changed).status)

    def choose(self, game, seat, **values):
        response = {"action_id": "choose", **values}
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:D", response).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        result = game.act("pilot:" + seat, response)
        self.assertTrue(result.ok, result.summary)

    def test_native_attack_counts_each_discarder_once_and_activation_uses_experience(self):
        for accepted, expected in ((True, 2), (False, 1)):
            with self.subTest(accepted=accepted):
                game = self.session(701900100 + int(accepted))
                game.state.config.semantic_policy = "trusted_only"
                source = self.add(game.engine, CARD)
                rot = self.add(game.engine, "Mind Rot", zone="hand")
                self.add(game.engine, "Swamp")
                self.add(game.engine, "Mountain")
                for card in (source, rot):
                    self.assert_closed(game, card)
                self.prepare(game, mana={"B": 1, "C": 2})
                self.checkpoint(game)
                self.cast_for(game, "A", rot, ["B"])
                pending = self.resolve(game)
                self.assertEqual(("choice.apnap", ["B"]), (pending.kind, pending.actors))
                viewed = game.packet("pilot:B", full=True)["decision"]["ctx"]
                self.choose(game, "B", cards=viewed["options"][:2])
                self.assertIsNone(self.resolve(game))
                self.until(game, lambda s: s.state.pending_decision.kind == "combat.attackers")
                attack = game.act("pilot:A", {"action_id": "attack", "attacks": {source.ref: "B"}})
                self.assertTrue(attack.ok, attack.summary)
                if game.state.pending_decision.kind == "trigger.order":
                    rows = game.packet("pilot:A", full=True)["decision"]["ctx"]["triggers"]
                    ordered = game.act("pilot:A", {"action_id": "order", "triggers": [row["id"] for row in rows]})
                    self.assertTrue(ordered.ok, ordered.summary)
                pending = self.resolve(game)
                self.assertEqual(("semantic.choice", ["A"]), (pending.kind, pending.actors))
                game = self.replay(game, load=True)
                self.choose(game, "A", choice="apply" if accepted else "decline")
                if accepted:
                    pending = self.resolve(game)
                    self.assertEqual(("choice.apnap", ["A"]), (pending.kind, pending.actors))
                    viewed = game.packet("pilot:A", full=True)["decision"]["ctx"]
                    for viewer in ("B", "C", "D"):
                        audience = json.dumps(game.packet("pilot:" + viewer, full=True))
                        self.assertNotIn(viewed["options"][0], audience)
                    self.choose(game, "A", cards=viewed["options"][:1])
                self.assertIsNone(self.resolve(game))
                self.assertEqual(expected, game.state.players["A"].counters.get("experience", 0))
                self.priority(game, "A")
                action = next(row for row in game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
                              if row["id"].startswith("activate:" + source.ref + ":"))
                activated = game.act("pilot:A", {"action_id": action["id"], "pay": "auto"})
                self.assertTrue(activated.ok, activated.summary)
                game = self.replay(game, load=True)
                self.assertIsNone(self.resolve(game))
                self.assertIn("menace", game.engine._combat_keywords(game.state.cards[source.object_id]))
                self.assertEqual((3 + expected, 3 + expected), tuple(
                    game.engine._numeric_stat(source.object_id, key) for key in ("power", "toughness")
                ))
                self.replay(game, load=True)
