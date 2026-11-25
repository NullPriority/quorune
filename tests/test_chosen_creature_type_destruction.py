"""Complete original gameplay for resolving creature-type destruction choices."""

from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.affected_permanents import AffectedPermanentSetSpec
from quorune.card_programs.descriptor_requirements import typed_result_amount_runtime_dependencies
from quorune.carddb import CardDatabase
from quorune.compiler import destruction_templates
from quorune.creature_subtypes import CREATURE_SUBTYPES
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.destruction_regeneration_capability_shapes import mass_destruction_node_capabilities
from quorune.rules.destruction_result_model import (
    CREATURE_TYPE_DESTRUCTION_CAPABILITY, CREATURE_TYPE_DESTRUCTION_DEPENDENCIES,
    CREATURE_TYPE_DESTRUCTION_FIELD, ChosenCreatureTypeDestruction,
)
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


EXTINCTION = "Extinction"
DOMINANCE = "Kindred Dominance"
PROFILE = "commander_review"
FIXTURES = ROOT / "tests" / "fixtures"
ORIGINALS = {
    EXTINCTION: ChosenCreatureTypeDestruction.MATCHING,
    DOMINANCE: ChosenCreatureTypeDestruction.NONMATCHING,
}


class ChosenCreatureTypeDestructionTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        folder = Path(cls.temporary.name)
        support = {
            "attached-destruction-set-cards.json": {
                "Extinction", "Troll Ascetic", "Darksteel Myr", "Grizzly Bears",
                "Forest", "Gladecover Scout", "Doomed Traveler",
            },
            "characteristic-query-union-cards.json": {"Universal Automaton"},
            "creature-subtype-addition-cards.json": {"Boggart Shenanigans"},
            "bound-effect-program-cards.json": {"Generic Bound Plains", "Generic Bound Growth", "Frost Lynx"},
            "source-attack-alone-cards.json": {"Isamaru, Hound of Konda"},
        }
        parts = [FIXTURES / "chosen-creature-type-destruction-cards.json"]
        for name, names in support.items():
            document = json.loads((FIXTURES / name).read_bytes())
            document["cards"] = [card for card in document["cards"] if card["name"] in names]
            assert names == {card["name"] for card in document["cards"]}, name
            ids = {card["oracle_id"] for card in document["cards"]}
            document["rulings"] = [ruling for ruling in document.get("rulings", ()) if ruling["oracle_id"] in ids]
            part = folder / name
            part.write_text(json.dumps(document), encoding="utf-8")
            parts.append(part)
        path = folder / "chosen-creature-type-destruction.sqlite3"
        build_fixture_database(parts, path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Creature-type destruction", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"),
            DeckEntry("Generic Bound Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def _choose_action(self, game):
        pending = game.state.pending_decision
        self.assertEqual("semantic.choice", pending.kind)
        self.assertEqual(["pilot:A"], game.pending_principals())
        for seat in "BCD":
            self.assertIsNone(game.packet(f"pilot:{seat}", full=True)["decision"])
        actions = game.packet("pilot:A", full=True)["decision"]["legal_actions"]
        action, = actions
        self.assertEqual("choose", action["id"])
        offered = action["choice_schema"]["legal_values"]
        self.assertEqual(sorted(value.title() for value in CREATURE_SUBTYPES), offered)
        self.assertIn("Time Lord", offered)
        self.assertNotIn("Artifact", offered)
        return {"action_id": action["id"]}

    def _commit_choice(self, game, value, *, rejected=()):
        action = self._choose_action(game)
        valid = {**action, "creature_type": value}
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:B", valid).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        for fields in rejected:
            self.assertFalse(game.act("pilot:A", {**action, **fields}).ok)
            self.assertEqual(before, authoritative_state_hash(game.state))
        start = len(game.state.events)
        result = game.act("pilot:A", valid)
        self.assertTrue(result.ok, result.summary)
        immutable = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:A", valid).ok)
        self.assertEqual(immutable, authoritative_state_hash(game.state))
        effects = [event for event in game.state.events[start:]
                   if event.code in {"semantic.option.chosen", "effect.permanent.destroy_set"}]
        self.assertEqual(["semantic.option.chosen", "effect.permanent.destroy_set"], [event.code for event in effects])
        self.assertEqual(value.casefold(), effects[0].details["creature_type"])
        return effects[1].details

    def test_wire_is_one_closed_creature_choice_and_ordinary_simultaneous_set(self):
        for mode in ChosenCreatureTypeDestruction:
            choice = mode.choice_effect()
            self.assertIs(mode, ChosenCreatureTypeDestruction.from_effect(choice))
            dependencies = mass_destruction_node_capabilities(
                effects=(choice,), target_schema=None, mechanic_ids=("destroy", "destroy-fixed-set"))
            self.assertEqual(CREATURE_TYPE_DESTRUCTION_DEPENDENCIES, dependencies)
            self.assertEqual(dependencies, typed_result_amount_runtime_dependencies(choice))
            result = mode.destruction_effect("cat")
            self.assertEqual({"op", "source", "set"}, set(result))
            self.assertEqual("destroy_all", result["op"])
            query = AffectedPermanentSetSpec.from_dict(result["set"]).query
            self.assertEqual(("battlefield",), query.zones)
            self.assertEqual(("creature",), query.types_all)
            relation = (query.subtypes_all, query.excluded_subtypes)
            self.assertEqual((("cat",), ()) if mode is mode.MATCHING else ((), ("cat",)), relation)
            self.assertFalse(mass_destruction_node_capabilities(
                effects=(choice,), target_schema={"count": 1}, mechanic_ids=("destroy", "destroy-fixed-set")))
            self.assertFalse(mass_destruction_node_capabilities(
                effects=(choice, result), target_schema=None, mechanic_ids=("destroy", "destroy-fixed-set")))
            for bad in (
                dict(choice, player="B"), dict(choice, op="destroy_all"),
                dict(choice, **{CREATURE_TYPE_DESTRUCTION_FIELD: {"schema_version": True, "scope": mode.value}}),
                dict(choice, **{CREATURE_TYPE_DESTRUCTION_FIELD: {"schema_version": 1, "scope": "any"}}),
            ):
                with self.subTest(bad=bad), self.assertRaises(ValueError):
                    typed_result_amount_runtime_dependencies(bad)

    def test_complete_originals_bind_their_own_match_and_complement_with_no_targets(self):
        for name, mode in ORIGINALS.items():
            with self.subTest(name=name):
                card = self.db.lookup(name, fuzzy=False)
                ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile=PROFILE)
                self.assertEqual("exact", ir.status, ir.material_residuals)
                report, semantics = register_card(self.db, self.registry, name)
                self.assertTrue(report["strict_capability_ready"], report["blockers"])
                self.assertEqual("capability_closed", report["trust_basis"])
                program, = semantics.programs_for_oracle(card.oracle_id)
                self.assertIsNone(program.target_schema)
                self.assertLessEqual(set(CREATURE_TYPE_DESTRUCTION_DEPENDENCIES), set(program.capability_dependencies))
                effect, = program.effects
                self.assertIs(mode, ChosenCreatureTypeDestruction.from_effect(effect))
        unrepresented, _ = register_card(self.db, self.registry, "Harsh Mercy")
        self.assertFalse(unrepresented["strict_capability_ready"], unrepresented)

    def test_actual_extinction_destroys_matching_players_creatures_and_regeneration_applies(self):
        game = self.session(6494140)
        spell = self.add(game.engine, EXTINCTION, zone="hand")
        own = self.add(game.engine, "Troll Ascetic")
        shielded = self.add(game.engine, "Troll Ascetic", seat="B", ref="type-destruction-shielded")
        changeling = self.add(game.engine, "Universal Automaton", seat="C")
        bear = self.add(game.engine, "Grizzly Bears", seat="D")
        forest = self.add(game.engine, "Forest", seat="B")
        self.assert_closed(game, shielded)
        self.prepare(game, mana={"C": 4, "B": 1})
        game.state.players["B"].mana_pool.update({"C": 1, "G": 1})
        self.checkpoint(game)

        self.cast(game, "A", spell)
        self.assertEqual([], game.state.stack[-1].targets)
        self.assertEqual(0, sum(game.state.players["A"].mana_pool.values()))
        self.priority(game, "B")
        actions = game.packet("pilot:B", full=True)["decision"]["ctx"]["legal"]["actions"]
        offer, = [action for action in actions if action["id"].startswith(f"activate:{shielded.ref}:")]
        response = game.act("pilot:B", {"action_id": offer["id"], "pay": "auto"})
        self.assertTrue(response.ok, response.summary)
        self.resolve_top(game)
        self.assertEqual(1, game.state.cards[shielded.object_id].regeneration_shields)

        self.assertEqual("semantic.choice", self.resolve(game).kind)
        self.assertEqual("battlefield", game.state.cards[own.object_id].zone)
        game = self.replay(game, load=True)
        destruction = self._commit_choice(game, "Troll")
        self.assertEqual((3, 2), (destruction["affected_count"], destruction["destroyed_count"]))
        for card in (own, changeling):
            self.assertEqual("graveyard", game.state.cards[card.object_id].zone)
        for card in (shielded, bear, forest):
            self.assertEqual("battlefield", game.state.cards[card.object_id].zone)
        survivor = game.state.cards[shielded.object_id]
        self.assertTrue(survivor.tapped)
        self.assertEqual(0, survivor.regeneration_shields)
        self.assertFalse(game.state.stack)
        self.replay(game, load=True)

    def test_actual_kindred_dominance_uses_live_creature_types_and_one_choice_before_death_triggers(self):
        game = self.session(6494150)
        spell = self.add(game.engine, DOMINANCE, zone="hand")
        cat = self.add(game.engine, "Frost Lynx", seat="A")
        changeling = self.add(game.engine, "Universal Automaton", seat="B")
        kindred = self.add(game.engine, "Boggart Shenanigans", seat="B")
        bear = self.add(game.engine, "Grizzly Bears", seat="A")
        traveler = self.add(game.engine, "Doomed Traveler", seat="C")
        hexproof = self.add(game.engine, "Gladecover Scout", seat="D")
        indestructible = self.add(game.engine, "Darksteel Myr", seat="D")
        private = self.add(game.engine, "Generic Bound Growth", seat="D", zone="hand", ref="type-destruction-private")
        self.prepare(game, mana={"C": 5, "B": 2})
        self.checkpoint(game)

        self.cast(game, "A", spell)
        self.assertEqual(0, sum(game.state.players["A"].mana_pool.values()))
        self.assertEqual([], game.state.stack[-1].targets)
        self.assertEqual("semantic.choice", self.resolve(game).kind)
        for principal in ("pilot:A", "pilot:B", "pilot:C", "spectator"):
            self.assertNotIn(private.ref, json.dumps(game.packet(principal, full=True)))
        game = self.replay(game, load=True)
        destruction = self._commit_choice(game, "Cat", rejected=(
            {"creature_type": "Artifact"},
            {"creature_type": "Cat Warrior"},
            {"creature_type": "Cat", "choice": "Elf"},
            {"creature_type": "Cat", "scope": "matching"},
        ))
        self.assertEqual((4, 3), (destruction["affected_count"], destruction["destroyed_count"]))
        for card in (bear, traveler, hexproof):
            self.assertEqual("graveyard", game.state.cards[card.object_id].zone)
        for card in (cat, changeling, kindred, indestructible):
            self.assertEqual("battlefield", game.state.cards[card.object_id].zone)
        self.assertEqual(1, self.trigger_count(game, traveler))
        self.assertIsNone(self.resolve(game))
        tokens = [card for card in game.state.cards.values() if card.zone == "battlefield" and card.is_token]
        token, = tokens
        self.assertEqual("C", token.controller)
        self.assertIn("spirit", token.printed_name.casefold())
        self.replay(game, load=True)

    def test_omitting_the_exact_chosen_result_restores_both_material_residuals(self):
        with patch.object(destruction_templates, "_chosen_creature_type_destruction_template", return_value=None):
            for name in ORIGINALS:
                card = self.db.lookup(name, fuzzy=False)
                ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile=PROFILE)
                residual, = ir.material_residuals
                self.assertEqual("spell_effect", residual.kind)
                report, _ = register_card(self.db, self.registry, name)
                self.assertFalse(report["strict_capability_ready"], report)

    def test_genuine_chosen_destruction_authority_withdrawal_denies_both_originals(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests

        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        known = set(discover_tests(ROOT).values())
        document = deepcopy(raw)
        next(row for row in document["capabilities"] if row["id"] == CREATURE_TYPE_DESTRUCTION_CAPABILITY).update(
            status="blocked", blockers=["Scoped chosen creature-type destruction withdrawal"])
        registry = CapabilityRegistry(document)
        index = build_index(registry_value=document, declaration_source=declarations, discovered_test_ids=known)
        registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
        closure = registry.closure((CREATURE_TYPE_DESTRUCTION_CAPABILITY,), profile=PROFILE)
        self.assertIn("status:" + CREATURE_TYPE_DESTRUCTION_CAPABILITY + ":blocked", closure.blockers)
        self.assertFalse(any("unverified" in blocker for blocker in closure.blockers))
        for name in ORIGINALS:
            report, _ = register_card(self.db, registry, name)
            self.assertFalse(report["strict_capability_ready"], report)
        ordinary, _ = register_card(self.db, registry, "Grizzly Bears")
        self.assertTrue(ordinary["strict_capability_ready"], ordinary["blockers"])


if __name__ == "__main__":
    unittest.main()
