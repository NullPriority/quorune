"""A real beginning-combat counter and a separately announced reflexive target."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.reflexive_trigger_model import SOURCE_COUNTER_REFLEXIVE_FIELD
from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry
from quorune.rules.evidence import validate_capability_evidence_index
from quorune.rules.evidence_storage import read_evidence_document
from quorune.rules.source_counter_reflexive import SOURCE_COUNTER_REFLEXIVE_CAPABILITY
from quorune.rules.temporary_declaration_restrictions import current_declaration_restrictions
from scripts.build_test_database import build_fixture_database
from scripts.update_capability_evidence import build_index, discover_tests


class SourceCounterReflexiveTests(InheritedNativeFixture, unittest.TestCase):
    scout = "Trial Reflexive Trail Watcher"
    body = "Trial Refuge Creature"
    instant = "Trial Reflexive Remedy"
    brake = "Trial Counter Impasse"
    extra = "Trial Counter Amplifier"
    plains = "Generic Bound Plains"

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "source-counter-reflexive.sqlite3"
        fixtures = ROOT / "tests/fixtures"
        build_fixture_database([
            fixtures / "bound-effect-program-cards.json",
            fixtures / "source-counter-reflexive-cards.json",
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        name = "Generic Bound Commander"
        cls.deck = DeckDefinition("Reflexive source counter", [
            DeckEntry(name, 1, "commander"), DeckEntry(cls.plains, 30),
        ], [name])

    def combat(self, seed, *, suppress=False, increases=0, cast_noncreature=True):
        game = self.session(seed)
        source = self.add(game.engine, self.scout, ref="REFLEXIVE-COUNTER-SOURCE")
        eligible = self.add(game.engine, self.body, ref="OWN-OPTIONAL-CREATURE")
        foreign = self.add(game.engine, self.body, seat="B", ref="FOREIGN-REFLEXIVE-CREATURE")
        spell = self.add(game.engine, self.instant, zone="hand")
        sources = []
        if suppress:
            sources.append(self.add(game.engine, self.brake))
        for i in range(increases):
            sources.append(self.add(game.engine, self.extra, ref=f"COMBAT-COUNTER-INCREASE-{i}"))
        source.acquired_control_turn_count = eligible.acquired_control_turn_count = -1
        for card in (source, spell, *sources):
            self.assert_closed(game, card)
        game.state.config.semantic_policy = "trusted_only"
        self.prepare(game, mana={"C": 1, "W": 1})
        self.checkpoint(game)
        if cast_noncreature:
            self.cast(game, "A", spell)
            self.assertIsNone(self.resolve(game))
        return game, source, eligible, foreign

    def parent(self, game, source):
        self.until(game, lambda session: any(item.source_object_id == source.object_id and item.kind == "triggered_ability"
                                            for item in session.state.stack))
        parent, = (item for item in game.state.stack if item.source_object_id == source.object_id
                   and item.kind == "triggered_ability")
        self.assertEqual([], parent.targets)
        self.assertEqual(0, game.state.cards[source.object_id].counters.get("+1/+1", 0))
        return parent.ref

    def choose(self, game, refs):
        result = game.act("pilot:A", {"action_id": "choose", "targets": list(refs)})
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(game))

    def restrictions(self, game):
        return {row.source.ref for row in current_declaration_restrictions(game.engine)
                if row.template.template_id == "intrinsic-unblockable-v1"}

    def test_live_counter_precedes_optional_target_and_replays(self):
        game, source, eligible, foreign = self.combat(60312021)
        original = self.parent(game, source)
        pending = self.resolve(game)
        self.assertEqual(("semantic.target", ["A"]), (pending.kind, pending.actors))
        self.assertEqual(1, game.state.cards[source.object_id].counters["+1/+1"])
        self.assertTrue(all(item.ref != original for item in game.state.stack))
        child, = game.state.stack
        self.assertEqual(original, child.context["reflexive_parent"])
        before = authoritative_state_hash(game.state)
        for invalid in (foreign.ref, source.ref):
            rejected = game.act("pilot:A", {"action_id": "choose", "targets": [invalid]})
            self.assertFalse(rejected.ok)
            self.assertEqual(before, authoritative_state_hash(game.state))
        game = self.replay(game, load=True)
        self.choose(game, (eligible.ref,))
        self.assertEqual({source.ref, eligible.ref}, self.restrictions(game))
        self.replay(game, load=True)

    def test_replacement_choice_resumes_one_reflexive_trigger_after_actual_increases(self):
        game, source, _, _ = self.combat(60312022, increases=2)
        original = self.parent(game, source)
        pending = self.resolve(game)
        self.assertEqual(("replacement.order", ["A"]), (pending.kind, pending.actors))
        self.assertEqual(0, game.state.cards[source.object_id].counters.get("+1/+1", 0))
        game = self.replay(game, load=True)
        view = game.packet("pilot:A", full=True)["decision"]["ctx"]
        action = game.act("pilot:A", {"action_id": "choose", "replacement": view["options"][0]["id"]})
        self.assertTrue(action.ok, action.summary)
        pending = self.resolve(game)
        self.assertEqual(("semantic.target", ["A"]), (pending.kind, pending.actors))
        self.assertEqual(3, game.state.cards[source.object_id].counters["+1/+1"])
        child, = game.state.stack
        self.assertEqual(original, child.context["reflexive_parent"])
        game = self.replay(game, load=True)
        self.choose(game, ())
        self.assertEqual({source.ref}, self.restrictions(game))
        self.replay(game, load=True)

    def test_prevented_counter_or_absent_spell_history_issues_no_reflexive_target(self):
        for blocked in (True, False):
            with self.subTest(blocked=blocked):
                game, source, _, _ = self.combat(60312023 + blocked, suppress=blocked, cast_noncreature=blocked)
                if blocked:
                    self.parent(game, source)
                    self.assertIsNone(self.resolve(game))
                self.until(game, lambda session: session.state.pending_decision.kind == "combat.attackers")
                self.assertEqual(0, game.state.cards[source.object_id].counters.get("+1/+1", 0))
                self.assertFalse(self.restrictions(game))
                self.replay(game, load=True)

    def test_counter_followup_depends_on_its_exact_parent_and_verified_authority(self):
        record = self.db.lookup(self.scout)
        def compile_card(registry):
            return compile_oracle_card(record, capability_registry=registry, capability_profile="commander_review")

        ir = compile_card(self.registry)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        node, = (n for n in ir.faces[0].nodes if n.event == "step.begin")
        self.assertIsNone(node.target_schema)
        self.assertIn(SOURCE_COUNTER_REFLEXIVE_FIELD, node.effects[0])
        self.assertIn(SOURCE_COUNTER_REFLEXIVE_CAPABILITY, node.capability_dependencies)
        data = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        target = next(row for row in data["capabilities"] if row["id"] == SOURCE_COUNTER_REFLEXIVE_CAPABILITY)
        target.update(status="blocked", blockers=["Committed source counter authority withdrawn."])
        registry = CapabilityRegistry(data)
        decl = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        index = build_index(registry_value=data, declaration_source=decl, discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
        self.assertNotEqual("exact", compile_card(registry).status)
        with patch("quorune.compiler.source_counter_reflexive.source_counter_reflexive_result", return_value=None):
            self.assertNotEqual("exact", compile_card(self.registry).status)
