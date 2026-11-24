"""The original target recipients inherit one public creature chosen during resolution."""

from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.temporary_copy_subject import (
    TEMPORARY_COPY_SUBJECT_CAPABILITY as CAPABILITY, TemporaryCopySubjectScope,
    decode_temporary_copy_subject_effect, temporary_copy_subject_node_capabilities,
)
from quorune.target_count_cost_model import TARGET_COUNT_COST_CAPABILITY
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


CYTO = "Cytoshape"
RUSH = "Polymorphous Rush"
PROFILE = "commander_review"
FIXTURES = ROOT / "tests" / "fixtures"


class TemporaryCopySubjectTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        folder = Path(cls.temporary.name)
        support = {
            "any-number-target-cards.json": {RUSH},
            "temporary-creature-copy-set-cards.json": {"Mirrorweave"},
            "bound-effect-program-cards.json": {"Generic Bound Plains", "Unsummon"},
            "activation-public-state-cards.json": {"Giant Growth"},
            "attached-destruction-set-cards.json": {"Grizzly Bears", "Island"},
            "creature-subtype-addition-cards.json": {"Goblin Piker", "Isamaru, Hound of Konda"},
            "enchant-public-state-cards.json": {"Slippery Bogle"},
            "face-down-method-cards.json": {"Soul Summons"},
        }
        fixtures = [FIXTURES / "temporary-copy-subject-cards.json"]
        for name, names in support.items():
            doc = json.loads((FIXTURES / name).read_bytes())
            doc["cards"] = [row for row in doc["cards"] if row["name"] in names]
            assert {row["name"] for row in doc["cards"]} == names, name
            ids = {row["oracle_id"] for row in doc["cards"]}
            doc["rulings"] = [row for row in doc.get("rulings", ()) if row["oracle_id"] in ids]
            partial = folder / name
            partial.write_text(json.dumps(doc), encoding="utf-8")
            fixtures.append(partial)
        database = folder / "temporary-copy-subject.sqlite3"
        build_fixture_database(fixtures, database)
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Resolving public copy subjects", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def test_full_originals_bind_the_separate_target_and_choice_and_retain_strive_authority(self):
        for name, scope in ((CYTO, TemporaryCopySubjectScope.NONLEGENDARY_FOR_ONE_TARGET),
                            (RUSH, TemporaryCopySubjectScope.CREATURE_FOR_CONTROLLED_TARGETS)):
            with self.subTest(name=name):
                card = self.db.lookup(name, fuzzy=False)
                ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile=PROFILE)
                self.assertEqual("exact", ir.status, ir.material_residuals)
                report, registry = register_card(self.db, self.registry, name)
                self.assertTrue(report["strict_capability_ready"], report["blockers"])
                self.assertEqual("capability_closed", report["trust_basis"])
                node, = (n for n in ir.faces[0].nodes if CAPABILITY in n.capability_dependencies)
                effect, = node.effects
                self.assertIs(scope, decode_temporary_copy_subject_effect(effect))
                self.assertEqual(scope.target_schema(), node.target_schema)
                self.assertEqual(name == RUSH, any(TARGET_COUNT_COST_CAPABILITY in n.capability_dependencies
                                                    for n in ir.faces[0].nodes))
                for change in ({"schema_version": True}, {"player": "$target.0"}, {"receivers": "$source.zone_object"},
                               {"scope": "choose_opposing_creature"}):
                    self.assertFalse(temporary_copy_subject_node_capabilities(
                        effects=({**effect, **change},), target_schema=node.target_schema, mechanic_ids=node.mechanics,
                    ))
                schema = deepcopy(node.target_schema)
                schema["supertypes_none"] = ["legendary"]
                self.assertFalse(temporary_copy_subject_node_capabilities(
                    effects=(effect,), target_schema=schema, mechanic_ids=node.mechanics,
                ))
                program, = (p for p in registry.programs_for_oracle(card.oracle_id) if CAPABILITY in p.capability_dependencies)
                deps = tuple(c for c in program.capability_dependencies if c != CAPABILITY)
                undeclared = replace(program, coverage=(), capability_dependencies=deps,
                                     capability_closure=self.registry.closure(deps, profile=PROFILE).to_dict())
                binding = bind_semantic_program_runtime(undeclared, capability_registry=self.registry, profile=PROFILE)
                self.assertIn("capability:undeclared_runtime_dependency:" + CAPABILITY, binding["blockers"])

    def _start(self, game, originals, mana):
        for card in originals:
            self.assert_closed(game, card)
        game.state.config.semantic_policy = "trusted_only"
        self.prepare(game, mana=mana)
        self.checkpoint(game)

    def _action(self, game, command, *, actor="A"):
        result = game.act("pilot:" + actor, command)
        self.assertTrue(result.ok, result.summary)

    def _reject(self, game, command, *, actor="A"):
        before = authoritative_state_hash(game.state)
        rejected = game.act("pilot:" + actor, command)
        self.assertFalse(rejected.ok, rejected.summary)
        self.assertEqual(before, authoritative_state_hash(game.state))

    def _subject_offer(self, game):
        pending = self.resolve(game)
        self.assertEqual(("semantic.choice", ["A"]), (pending.kind, pending.actors))
        for seat in "BCD":
            self.assertIsNone(game.packet("pilot:" + seat, full=True)["decision"])
        row, = [action for action in pending.payload_by_actor["A"]["legal_actions"] if action["id"] == "choose"]
        refs = row["choice_schema"]["legal_refs"]
        self.assertEqual(sorted(refs), list(refs))
        return set(refs)

    def _data(self, game, card):
        return game.engine._effective_card_data(game.state.cards[card.object_id])

    def test_actual_cytoshape_targets_the_receiver_and_chooses_a_hexproof_model_later(self):
        game = self.session(70721101)
        spell = self.add(game.engine, CYTO, zone="hand")
        receiver = self.add(game.engine, "Goblin Piker")
        model = self.add(game.engine, "Slippery Bogle", seat="B")
        legend = self.add(game.engine, "Isamaru, Hound of Konda", seat="C", ref="cyto-off-scope")
        hidden = self.add(game.engine, "Grizzly Bears", seat="D", zone="hand", ref="cyto-secret")
        self._start(game, (spell, model), {"C": 1, "G": 1, "U": 1})
        offered = self.action(game, "A", spell)
        command = {"action_id": offered["id"], "pay": "auto"}
        self._reject(game, {**command, "targets": [model.ref]})
        self._action(game, {**command, "targets": [receiver.ref]})
        self.assertEqual({receiver.ref, model.ref}, self._subject_offer(game))
        self.assertNotIn(hidden.ref, json.dumps(game.packet("pilot:A", full=True)))
        self._reject(game, {"action_id": "choose", "card": legend.ref})
        self._reject(game, {"action_id": "choose", "card": model.ref}, actor="B")
        game = self.replay(game, load=True)
        self._action(game, {"action_id": "choose", "card": model.ref})
        self.assertEqual("Slippery Bogle", self._data(game, receiver)["name"])
        self.assertEqual("Slippery Bogle", self._data(game, model)["name"])
        self.assertEqual(receiver.logical_object_id, game.state.cards[receiver.object_id].logical_object_id)
        self.assertIn("hexproof", game.engine._combat_keywords(game.state.cards[receiver.object_id]))
        self.replay(game, load=True)

    def test_actual_face_down_subject_copies_only_the_anonymous_public_values(self):
        game = self.session(70721102)
        spell = self.add(game.engine, CYTO, zone="hand")
        manifest = self.add(game.engine, "Soul Summons", zone="hand")
        receiver = self.add(game.engine, "Goblin Piker")
        hidden = self.add(game.engine, "Giant Growth", zone="library", ref="cyto-manifest-hidden")
        hidden.known_to.clear()
        hidden.revealed_to.clear()
        self._start(game, (spell, manifest), {"C": 2, "W": 1, "G": 1, "U": 1})
        action = self.action(game, "A", manifest)
        self._action(game, {"action_id": action["id"], "pay": "manual", "payment": {"C": 1, "W": 1}})
        self.assertIsNone(self.resolve(game))
        self.assertTrue(game.state.cards[hidden.object_id].face_down)
        action = self.action(game, "A", spell)
        self._action(game, {"action_id": action["id"], "pay": "manual", "payment": {"C": 1, "G": 1, "U": 1},
                            "targets": [receiver.ref]})
        self.assertIn(hidden.ref, self._subject_offer(game))
        game = self.replay(game, load=True)
        self._action(game, {"action_id": "choose", "card": hidden.ref})
        values = self._data(game, receiver)
        self.assertEqual(("", "Creature", [], []), (values["name"], values["type_line"], values["colors"], values["keywords"]))
        self.assertFalse(game.state.cards[receiver.object_id].face_down)
        self.assertEqual((2, 2), tuple(game.engine._numeric_stat(receiver.object_id, field) for field in ("power", "toughness")))
        for seat in "BCD":
            visible = json.dumps(game.packet("pilot:" + seat, full=True)["state"])
            self.assertNotIn("Giant Growth", visible)
            self.assertNotIn(hidden.oracle_id[:8], visible)
        self.replay(game, load=True)

    def test_actual_strive_prices_the_announced_pair_and_copies_only_the_surviving_target(self):
        game = self.session(70721104)
        spell = self.add(game.engine, RUSH, zone="hand")
        first = self.add(game.engine, "Goblin Piker")
        second = self.add(game.engine, "Grizzly Bears")
        model = self.add(game.engine, "Isamaru, Hound of Konda", seat="C", ref="rush-legend-model")
        bounce = self.add(game.engine, "Unsummon", seat="B", zone="hand")
        self.add(game.engine, "Island", seat="B")
        self._start(game, (spell,), {"C": 3, "U": 2})
        row = self.action(game, "A", spell)
        command = {"action_id": row["id"], "targets": [first.ref, second.ref], "pay": "manual"}
        self._reject(game, {**command, "payment": {"C": 2, "U": 1}})
        self._reject(game, {**command, "targets": [first.ref, model.ref], "payment": {"C": 3, "U": 2}})
        self._action(game, {**command, "payment": {"C": 3, "U": 2}})
        self.assertFalse(any(game.state.players["A"].mana_pool.values()))
        self.cast(game, "B", bounce, [first.ref])
        self.resolve_top(game)
        self.assertEqual("hand", game.state.cards[first.object_id].zone)
        self.assertIn(model.ref, self._subject_offer(game))
        game = self.replay(game, load=True)
        self._action(game, {"action_id": "choose", "card": model.ref})
        self.assertEqual("Isamaru, Hound of Konda", self._data(game, second)["name"])
        self.assertIn("legendary", game.engine._effective_type_parts(game.state.cards[second.object_id])[2])
        self.assertEqual("Goblin Piker", self._data(game, first)["name"])
        self.replay(game, load=True)

    def test_zero_target_strive_and_unavailable_cytoshape_model_resolve_without_a_copy(self):
        game = self.session(70721106)
        rush = self.add(game.engine, RUSH, zone="hand")
        model = self.add(game.engine, "Grizzly Bears", seat="B")
        self._start(game, (rush,), {"C": 2, "U": 1})
        self.cast(game, "A", rush, [])
        self.assertEqual({model.ref}, self._subject_offer(game))
        self._action(game, {"action_id": "choose", "card": model.ref})
        self.assertFalse(any(event.code == "permanent.copy" for event in game.state.events))
        self.replay(game, load=True)

        game = self.session(70721107)
        spell = self.add(game.engine, CYTO, zone="hand")
        legend = self.add(game.engine, "Isamaru, Hound of Konda", seat="B", ref="cyto-only-legend")
        self._start(game, (spell,), {"C": 1, "G": 1, "U": 1})
        self.cast(game, "A", spell, [legend.ref])
        self.assertIsNone(self.resolve(game))
        self.assertFalse(any(event.code == "permanent.copy" for event in game.state.events))
        self.assertEqual("graveyard", game.state.cards[spell.object_id].zone)
        self.replay(game, load=True)

    def test_omitting_the_exact_subject_choice_keeps_the_actual_material_results(self):
        with patch("quorune.compiler.temporary_self_copy_templates.temporary_copy_subject_template", return_value=None):
            for name in (CYTO, RUSH):
                report, _ = register_card(self.db, self.registry, name)
                self.assertFalse(report["strict_capability_ready"], report)

    def test_genuine_subject_choice_withdrawal_preserves_the_direct_copy_set(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests

        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        next(row for row in raw["capabilities"] if row["id"] == CAPABILITY).update(
            status="blocked", blockers=["Scoped resolving temporary copy subject withdrawal"],
        )
        registry = CapabilityRegistry(raw)
        index = build_index(registry_value=raw, declaration_source=declarations,
                            discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
        closure = registry.closure((CAPABILITY,), profile=PROFILE)
        self.assertIn("status:" + CAPABILITY + ":blocked", closure.blockers)
        self.assertFalse(any("unverified" in blocker for blocker in closure.blockers))
        for name in (CYTO, RUSH):
            report, _ = register_card(self.db, registry, name)
            self.assertFalse(report["strict_capability_ready"], report)
        control, _ = register_card(self.db, registry, "Mirrorweave")
        self.assertTrue(control["strict_capability_ready"], control["blockers"])


if __name__ == "__main__":
    unittest.main()
