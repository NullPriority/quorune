"""Actual creature-type choices with a locked stat set and a private draw."""

from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.card_programs.descriptor_requirements import typed_result_amount_runtime_dependencies
from quorune.carddb import CardDatabase
from quorune.compiler import fixed_resolution_characteristics
from quorune.creature_subtypes import CREATURE_SUBTYPES
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.resolution_characteristic_model import (
    CHOSEN_CREATURE_TYPE_RESULT_CAPABILITY, CHOSEN_CREATURE_TYPE_RESULT_FIELD,
    ChosenCreatureTypeResult,
)
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.fixed_resolution_characteristic_shapes import fixed_resolution_characteristic_set_node_capabilities
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


FEAR = "Crippling Fear"
MELODY = "Distant Melody"
PALISADE = "Raise the Palisade"
ORIGINALS = {
    FEAR: ChosenCreatureTypeResult.NONMATCHING_CREATURE_STATS,
    MELODY: ChosenCreatureTypeResult.CONTROLLED_PERMANENT_DRAW,
    PALISADE: ChosenCreatureTypeResult.NONMATCHING_CREATURE_RETURN,
}
PROFILE = "commander_review"
FIXTURES = ROOT / "tests" / "fixtures"


class ChosenCreatureTypeResultTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        folder = Path(cls.temporary.name)
        support = {
            "attached-destruction-set-cards.json": {
                "Extinction", "Doomed Traveler", "Grizzly Bears", "Unsummon",
            },
            "bound-effect-program-cards.json": {"Generic Bound Plains", "Generic Bound Growth", "Frost Lynx"},
            "characteristic-query-union-cards.json": {"Universal Automaton"},
            "attacking-creature-exile-optional-search-cards.json": {"Slippery Bogle"},
            "creature-subtype-addition-cards.json": {"Boggart Shenanigans", "Goblin Piker", "Raging Goblin"},
            "temporary-source-keyword-cards.json": {"Mistform Wall"},
            "source-attack-alone-cards.json": {"Isamaru, Hound of Konda"},
        }
        parts = [FIXTURES / "chosen-creature-type-results-cards.json",
                 FIXTURES / "chosen-type-owner-return-cards.json"]
        for name, names in support.items():
            document = json.loads((FIXTURES / name).read_bytes())
            document["cards"] = [card for card in document["cards"] if card["name"] in names]
            assert names == {card["name"] for card in document["cards"]}, name
            ids = {card["oracle_id"] for card in document["cards"]}
            document["rulings"] = [ruling for ruling in document.get("rulings", ()) if ruling["oracle_id"] in ids]
            part = folder / name
            part.write_text(json.dumps(document), encoding="utf-8")
            parts.append(part)
        path = folder / "chosen-creature-type-results.sqlite3"
        build_fixture_database(parts, path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Creature type outcomes", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"),
            DeckEntry("Generic Bound Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def _choose(self, game, creature_type):
        self.assertEqual("semantic.choice", game.state.pending_decision.kind)
        self.assertEqual(["pilot:A"], game.pending_principals())
        actions = game.packet("pilot:A", full=True)["decision"]["legal_actions"]
        offer, = actions
        self.assertEqual("choose", offer["id"])
        self.assertEqual(sorted(value.title() for value in CREATURE_SUBTYPES),
                         offer["choice_schema"]["legal_values"])
        for seat in "BCD":
            self.assertIsNone(game.packet(f"pilot:{seat}", full=True)["decision"])
        command = {"action_id": offer["id"], "creature_type": creature_type}
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:B", command).ok)
        self.assertFalse(game.act("pilot:A", {**command, "creature_type": "Artifact"}).ok)
        self.assertFalse(game.act("pilot:A", {**command, "creature_type": "Cat Warrior"}).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        result = game.act("pilot:A", command)
        self.assertTrue(result.ok, result.summary)

    def test_exact_wire_preserves_the_distinct_queries_and_ordinary_leaf_requirements(self):
        for mode in ChosenCreatureTypeResult:
            effect = mode.choice_effect()
            self.assertEqual(mode, ChosenCreatureTypeResult.from_effect(effect))
            self.assertEqual(mode.capability_dependencies, fixed_resolution_characteristic_set_node_capabilities(
                effects=(effect,), target_schema=None, mechanic_ids=(),
            ))
            self.assertEqual(set(mode.capability_dependencies), set(typed_result_amount_runtime_dependencies(effect)))
            self.assertEqual((), fixed_resolution_characteristic_set_node_capabilities(
                effects=(effect,), target_schema={"count": 1}, mechanic_ids=(),
            ))
            self.assertEqual((), fixed_resolution_characteristic_set_node_capabilities(
                effects=(effect, {"op": "draw"}), target_schema=None, mechanic_ids=(),
            ))
            for bad in (dict(effect, player="B"), dict(effect, excluded="cat"),
                        dict(effect, **{CHOSEN_CREATURE_TYPE_RESULT_FIELD: {"schema_version": True, "result": mode.value}})):
                with self.subTest(bad=bad), self.assertRaises(ValueError):
                    typed_result_amount_runtime_dependencies(bad)
        stats = ChosenCreatureTypeResult.NONMATCHING_CREATURE_STATS.battlefield_query("cat", actor="A")
        draw = ChosenCreatureTypeResult.CONTROLLED_PERMANENT_DRAW.battlefield_query("goblin", actor="A")
        self.assertEqual((("creature",), ("cat",), None), (stats.types_all, stats.excluded_subtypes, stats.controller))
        self.assertEqual(((), ("goblin",), "A"), (draw.types_all, draw.subtypes_all, draw.controller))

    def test_complete_originals_have_one_shared_resolving_choice_and_distinct_result_authority(self):
        for name, mode in ORIGINALS.items():
            with self.subTest(name=name):
                card = self.db.lookup(name, fuzzy=False)
                ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile=PROFILE)
                self.assertEqual("exact", ir.status, ir.material_residuals)
                bound, semantics = register_card(self.db, self.registry, name)
                self.assertTrue(bound["strict_capability_ready"], bound["blockers"])
                self.assertEqual("capability_closed", bound["trust_basis"])
                program, = semantics.programs_for_oracle(card.oracle_id)
                effect, = program.effects
                self.assertIs(mode, ChosenCreatureTypeResult.from_effect(effect))
                self.assertIsNone(program.target_schema)
                self.assertLessEqual(set(mode.capability_dependencies), set(program.capability_dependencies))

    def test_paid_fear_locks_creature_incarnations_through_later_entry_and_type_changes(self):
        game = self.session(6500120)
        spell = self.add(game.engine, FEAR, zone="hand")
        wall = self.add(game.engine, "Mistform Wall")
        traveler = self.add(game.engine, "Doomed Traveler")
        bear = self.add(game.engine, "Grizzly Bears", seat="B")
        changeling = self.add(game.engine, "Universal Automaton", seat="C")
        cat = self.add(game.engine, "Frost Lynx", seat="D")
        for card in (spell, wall, changeling, traveler):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 3, "B": 2})
        self.checkpoint(game)

        self.cast(game, "A", spell)
        self.assertEqual([], game.state.stack[-1].targets)
        self.assertEqual(1, sum(game.state.players["A"].mana_pool.values()))
        self.assertEqual("semantic.choice", self.resolve(game).kind)
        game = self.replay(game, load=True)
        self._choose(game, "Cat")
        self.assertIsNone(self.resolve(game))
        for creature in (bear, traveler):
            self.assertEqual("graveyard", game.state.cards[creature.object_id].zone)
        for creature in (wall, cat, changeling):
            self.assertEqual("battlefield", game.state.cards[creature.object_id].zone)
        self.assertEqual(1, game.engine._numeric_stat(wall.object_id, "toughness"))
        token, = [card for card in game.state.cards.values() if card.is_token and card.zone == "battlefield"]
        self.assertIn("spirit", token.printed_name.casefold())

        self.priority(game, "A")
        actions = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        offer, = [action for action in actions if action["id"].startswith(f"activate:{wall.ref}:")]
        activated = game.act("pilot:A", {"action_id": offer["id"], "pay": "auto"})
        self.assertTrue(activated.ok, activated.summary)
        self.assertEqual("semantic.choice", self.resolve(game).kind)
        self._choose(game, "Cat")
        self.assertIsNone(self.resolve(game))
        self.assertIn("cat", game.engine._effective_type_parts(game.state.cards[wall.object_id])[1])
        self.assertEqual(1, game.engine._numeric_stat(wall.object_id, "toughness"))
        self.until(game, lambda current: current.state.active_player == "B")
        self.assertEqual(4, game.engine._numeric_stat(wall.object_id, "toughness"))
        self.replay(game, load=True)

    def test_paid_melody_counts_kindred_changeling_and_own_current_permanents_for_private_draw(self):
        game = self.session(6500130)
        spell = self.add(game.engine, MELODY, zone="hand")
        kindred = self.add(game.engine, "Boggart Shenanigans")
        goblin = self.add(game.engine, "Goblin Piker")
        changeling = self.add(game.engine, "Universal Automaton")
        late_out = self.add(game.engine, "Raging Goblin")
        foreign = self.add(game.engine, "Goblin Piker", seat="B", ref="melody-foreign-goblin")
        bounce = self.add(game.engine, "Unsummon", seat="B", zone="hand")
        private = self.add(game.engine, "Generic Bound Growth", seat="D", zone="hand", ref="melody-private")
        for card in (spell, kindred, goblin, changeling):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 3, "U": 1})
        game.state.players["B"].mana_pool["U"] = 1
        self.checkpoint(game)

        self.cast(game, "A", spell)
        self.assertEqual([], game.state.stack[-1].targets)
        self.assertEqual(0, sum(game.state.players["A"].mana_pool.values()))
        self.cast(game, "B", bounce, [late_out.ref])
        self.resolve_top(game)
        self.assertEqual("hand", game.state.cards[late_out.object_id].zone)
        self.assertEqual("semantic.choice", self.resolve(game).kind)
        game = self.replay(game, load=True)
        before = set(game.state.players["A"].zones["hand"])
        other = len(game.state.players["B"].zones["hand"])
        self._choose(game, "Goblin")
        self.assertIsNone(self.resolve(game))
        drawn = set(game.state.players["A"].zones["hand"]) - before
        self.assertEqual(3, len(drawn))
        self.assertEqual(other, len(game.state.players["B"].zones["hand"]))
        self.assertEqual("battlefield", game.state.cards[foreign.object_id].zone)
        for seat in "BCD":
            packet = json.dumps(game.packet("pilot:" + seat, full=True))
            for object_id in drawn:
                self.assertNotIn(game.state.cards[object_id].ref, packet)
        for seat in "ABC":
            self.assertNotIn(private.ref, json.dumps(game.packet("pilot:" + seat, full=True)))
        self.replay(game, load=True)

    def test_actual_palisade_reads_effective_types_and_returns_one_owner_hand_batch(self):
        game = self.session(6500151)
        spell = self.add(game.engine, PALISADE, zone="hand")
        wall = self.add(game.engine, "Mistform Wall")
        cat = self.add(game.engine, "Frost Lynx", seat="D")
        changeling = self.add(game.engine, "Universal Automaton", seat="C")
        borrowed = self.add(game.engine, "Grizzly Bears", seat="B", ref="palisade-borrowed-bear")
        borrowed.owner = "C"
        hexproof = self.add(game.engine, "Slippery Bogle", seat="B")
        land = self.add(game.engine, "Generic Bound Plains", seat="B")
        private = self.add(game.engine, "Generic Bound Growth", seat="D", zone="hand", ref="palisade-private")
        for card in (spell, wall, changeling, hexproof):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 10, "U": 3})
        self.checkpoint(game)

        offer = next(row for row in game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
                     if row["id"] == "cast:" + spell.ref)
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:A", {
            "action_id": offer["id"], "targets": [hexproof.ref], "pay": "auto",
        }).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        self.cast(game, "A", spell)
        self.assertEqual([], game.state.stack[-1].targets)
        self.priority(game, "A")
        activation, = [row for row in game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
                       if row["id"].startswith(f"activate:{wall.ref}:")]
        result = game.act("pilot:A", {"action_id": activation["id"], "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("semantic.choice", self.resolve(game).kind)
        self._choose(game, "Cat")
        self.assertEqual("semantic.choice", self.resolve(game).kind)

        game = self.replay(game, load=True)
        self._choose(game, "Cat")
        self.assertIsNone(self.resolve(game))
        for card in (wall, cat, changeling, land):
            self.assertEqual("battlefield", game.state.cards[card.object_id].zone)
        for card, owner in ((borrowed, "C"), (hexproof, "B")):
            self.assertEqual("hand", game.state.cards[card.object_id].zone)
            self.assertIn(card.object_id, game.state.players[owner].zones["hand"])
        timestamps = {game.state.cards[card.object_id].zone_timestamp for card in (borrowed, hexproof)}
        self.assertEqual(1, len(timestamps))
        for viewer in "ABC":
            self.assertNotIn(private.ref, json.dumps(game.packet("pilot:" + viewer, full=True)))
        self.replay(game, load=True)

    def test_palisade_scope_is_untargeted_and_closed_to_creature_owner_hand_return(self):
        from test_bound_effect_programs import record

        material = "Choose a creature type. Return all creatures that aren't of the chosen type to their owners' hands."
        outcome = compile_oracle_card(record(material, "Sorcery"), capability_registry=self.registry)
        self.assertEqual("exact", outcome.status, outcome.material_residuals)
        node, = outcome.faces[0].nodes
        self.assertIsNone(node.target_schema)
        self.assertIn("zone.move.fixed_public_set", node.capability_dependencies)
        for changed in (material.replace("creatures", "permanents"),
                        material.replace("their owners' hands", "your hand")):
            self.assertNotEqual("exact", compile_oracle_card(record(changed, "Sorcery"),
                                                            capability_registry=self.registry).status)

    def test_omitting_the_typed_result_recognizer_restores_each_whole_spell_residual(self):
        with patch.object(fixed_resolution_characteristics, "_chosen_creature_type_result_template", return_value=None):
            for name in ORIGINALS:
                card = self.db.lookup(name, fuzzy=False)
                ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile=PROFILE)
                residual, = ir.material_residuals
                self.assertEqual("spell_effect", residual.kind)
                result, _ = register_card(self.db, self.registry, name)
                self.assertFalse(result["strict_capability_ready"], result)

    def test_genuine_type_result_withdrawal_retains_the_separate_extinction_choice(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests

        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        known = set(discover_tests(ROOT).values())
        document = deepcopy(raw)
        next(row for row in document["capabilities"] if row["id"] == CHOSEN_CREATURE_TYPE_RESULT_CAPABILITY).update(
            status="blocked", blockers=["Scoped creature-type result authority withdrawal"])
        registry = CapabilityRegistry(document)
        index = build_index(registry_value=document, declaration_source=declarations, discovered_test_ids=known)
        registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
        closure = registry.closure((CHOSEN_CREATURE_TYPE_RESULT_CAPABILITY,), profile=PROFILE)
        self.assertIn("status:" + CHOSEN_CREATURE_TYPE_RESULT_CAPABILITY + ":blocked", closure.blockers)
        self.assertFalse(any("unverified" in blocker for blocker in closure.blockers))
        for name in ORIGINALS:
            report, _ = register_card(self.db, registry, name)
            self.assertFalse(report["strict_capability_ready"], report)
        prior, _ = register_card(self.db, registry, "Extinction")
        self.assertTrue(prior["strict_capability_ready"], prior["blockers"])


if __name__ == "__main__":
    unittest.main()
