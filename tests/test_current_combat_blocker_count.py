"""Native blockers, live combat changes, source incarnation, and replay."""

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules import current_combat_blocker_count as blocker_rule
from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry
from quorune.rules.evidence import validate_capability_evidence_index
from quorune.rules.evidence_storage import read_evidence_document
from scripts.build_test_database import build_fixture_database
from scripts.update_capability_evidence import build_index, discover_tests


ORIGINALS = {
    "Rabid Elephant": 2, "Gang of Elk": 2, "Sparring Golem": 1, "Elvish Berserker": 1,
}


class CurrentCombatBlockerCountTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "current-combat-blocker-count.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/current-combat-blocker-count-cards.json"], path)
        cls.use_database(path)

    @classmethod
    def use_database(cls, path):
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("Blocking creatures",
                                  [DeckEntry(commander, 1, "commander"), DeckEntry("Plains", 30)], [commander])

    def stage(self, name, *, responses=()):
        game = self.session(5095061)
        source = self.add(game.engine, name)
        source.acquired_control_turn_count = -1
        blockers = tuple(self.add(game.engine, "Grizzly Bears", seat="B", ref=f"current-blocker-{index}")
                         for index in range(2))
        spells = tuple(self.add(game.engine, name, seat="B", zone="hand", ref=f"combat-response-{index}")
                       for index, name in enumerate(responses))
        hidden = self.add(game.engine, "Plains", seat="D", zone="hand", ref="private-blocker-count-sentinel")
        for index in range(len(responses)):
            self.add(game.engine, "Plains", seat="B", ref=f"response-mana-{index}")
        self.add(game.engine, "Wastes", seat="B")
        self.assert_closed(game, source)
        self.prepare(game)
        self.checkpoint(game)
        return game, source, blockers, spells, hidden

    def block(self, game, source, blockers):
        self.attack(game, [(source, "B")])
        self.until(game, lambda current: current.state.pending_decision.kind == "combat.blockers")
        command = {"action_id": "block", "blocks": {card.ref: source.ref for card in blockers}}
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:C", command).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        result = game.act("pilot:B", command)
        self.assertTrue(result.ok, result.summary)
        trigger, = (item for item in game.state.stack if item.source_object_id == source.object_id)
        self.assertEqual("triggered_ability", trigger.kind)
        self.assertFalse(trigger.targets)

    def stats(self, game, source):
        return tuple(game.engine._numeric_stat(source.object_id, key) for key in ("power", "toughness"))

    def test_complete_originals_get_a_single_bonus_for_all_current_blockers(self):
        for name, bonus in ORIGINALS.items():
            with self.subTest(name=name):
                game, source, blockers, _, hidden = self.stage(name)
                baseline = self.stats(game, source)
                selected = blockers if bonus == 2 else blockers[:1]
                self.block(game, source, selected)
                self.assertNotIn(hidden.ref, str(game.packet("pilot:B", full=True)))
                self.assertIsNone(self.resolve(game))
                self.assertEqual(tuple(value + bonus * len(selected) for value in baseline), self.stats(game, source))
                self.replay(game, load=True)

    def test_live_blocker_count_changes_until_resolution_and_stays_fixed_afterward(self):
        game, source, blockers, responses, _ = self.stage("Gang of Elk", responses=("Cloudshift", "Cloudshift"))
        baseline = self.stats(game, source)
        self.block(game, source, blockers)
        self.cast_for(game, "B", responses[0], [blockers[0].ref])
        self.resolve_top(game)
        game = self.replay(game, load=True)
        self.assertEqual([blockers[1].object_id], game.state.combat.blockers[source.object_id])
        self.assertIsNone(self.resolve(game))
        grown = tuple(value + 2 for value in baseline)
        self.assertEqual(grown, self.stats(game, source))
        self.cast_for(game, "B", responses[1], [blockers[1].ref])
        self.assertIsNone(self.resolve(game))
        self.assertFalse(game.state.combat.blockers.get(source.object_id))
        self.assertEqual(grown, self.stats(game, source))
        self.replay(game, load=True)

    def test_the_new_incarnation_of_a_blinked_attacker_gets_no_bonus(self):
        game, source, blockers, responses, _ = self.stage("Rabid Elephant", responses=("Flicker of Fate",))
        baseline = self.stats(game, source)
        incarnation = source.logical_object_id
        self.block(game, source, blockers)
        self.cast_for(game, "B", responses[0], [source.ref])
        self.resolve_top(game)
        self.assertNotEqual(incarnation, game.state.cards[source.object_id].logical_object_id)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(baseline, self.stats(game, source))
        self.replay(game, load=True)

    def test_live_count_witness_detects_an_omitted_blocker(self):
        self.test_live_blocker_count_changes_until_resolution_and_stays_fixed_afterward()
        with patch.object(blocker_rule, "current_attacker_blocker_count", return_value=0):
            with self.assertRaises(AssertionError):
                self.test_live_blocker_count_changes_until_resolution_and_stays_fixed_afterward()

    def test_the_result_requires_the_source_trigger_and_the_named_capability(self):
        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        capability = blocker_rule.CURRENT_BLOCKER_COUNT_CAPABILITY
        row = next(row for row in raw["capabilities"] if row["id"] == capability)
        row.update(status="blocked", blockers=["Current blocker count evidence withdrawn"])
        untrusted = CapabilityRegistry(raw)
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        index = build_index(registry_value=raw, declaration_source=declarations,
                            discovered_test_ids=set(discover_tests(ROOT).values()))
        untrusted.mark_evidence_verified(validate_capability_evidence_index(index, registry=untrusted))
        card = self.db.lookup("Rabid Elephant")
        current = compile_oracle_card(card, capability_registry=self.registry)
        self.assertEqual("exact", current.status, current.material_residuals)
        blocked = compile_oracle_card(card, capability_registry=untrusted)
        self.assertNotEqual("exact", blocked.status)
        self.assertTrue(any(capability in blocker for residual in blocked.material_residuals for blocker in residual.blockers))
        for modified in (
            card.oracle_text.replace("becomes blocked", "blocks"),
            card.oracle_text.replace("+2/+2", "+2/+1"),
            card.oracle_text.replace("blocking it", "that blocked it last turn"),
        ):
            with self.subTest(text=modified):
                self.assertNotEqual("exact", compile_oracle_card(replace(card, oracle_text=modified),
                                                                capability_registry=self.registry).status)
        distinct = replace(card, oracle_text=card.oracle_text.replace("blocking it", "attacking you"))
        separate = compile_oracle_card(distinct, capability_registry=self.registry)
        self.assertTrue(all(capability not in node.capability_dependencies
                            for face in separate.faces for node in face.nodes))
