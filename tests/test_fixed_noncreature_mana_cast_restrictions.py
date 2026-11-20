from copy import deepcopy
from dataclasses import replace
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
from quorune.replacement.immutable import thaw_value
from quorune.rules.capabilities import load_default_capability_registry
from quorune.semantic_runtime.context import SemanticNodeError
from quorune.semantic_runtime.static_cast_rules import StaticCastCondition
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


PROFILE = "commander_review"
NAME = "Gaddock Teeg"
CAPABILITY = "casting.prohibition.fixed_public"
FIXTURES = ROOT / "tests" / "fixtures"
CONDITIONS = {
    StaticCastCondition.NONCREATURE_MANA_VALUE_AT_LEAST_FOUR,
    StaticCastCondition.NONCREATURE_MANA_COST_HAS_X,
}


class FixedNoncreatureManaCastRestrictionTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        folder = Path(cls.temporary.name)
        support = {
            "ability-label-composition-cards.json": {"Island"},
            "ability-source-counter-cards.json": {"Boomerang", "Grizzly Bears"},
            "additional-conditional-modifier-cards.json": {"Divination"},
            "bound-effect-program-cards.json": {"Generic Bound Plains"},
            "cast-spell-mana-value-cards.json": {"Blaze"},
            "expend-cards.json": {"Frogmite", "Inspiration"},
            "lands-cast-mana-value-limit-cards.json": {"Lavinia, Azorius Renegade"},
            "multikicker-entry-counter-cards.json": {"Everflowing Chalice"},
            "source-attack-alone-cards.json": {"Isamaru, Hound of Konda"},
        }
        parts = [FIXTURES / "fixed-noncreature-mana-cast-restriction-cards.json"]
        for filename, names in support.items():
            data = json.loads((FIXTURES / filename).read_bytes())
            data["cards"] = [row for row in data["cards"] if row["name"] in names]
            assert {row["name"] for row in data["cards"]} == names
            ids = {row["oracle_id"] for row in data["cards"]}
            data["rulings"] = [row for row in data.get("rulings", ()) if row["oracle_id"] in ids]
            piece = folder / filename
            piece.write_text(json.dumps(data), encoding="utf-8")
            parts.append(piece)
        database = folder / "fixed-noncreature-mana-cast-restrictions.sqlite3"
        build_fixture_database(parts, database)
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Fixed noncreature mana prohibitions", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"),
            DeckEntry("Generic Bound Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def original_programs(self):
        card = self.db.lookup(NAME, fuzzy=False)
        bound, semantics = register_card(self.db, self.registry, NAME)
        self.assertTrue(bound["strict_capability_ready"], bound["blockers"])
        return card, semantics.programs_for_oracle(card.oracle_id)

    def test_complete_original_has_both_independent_all_player_prohibitions(self):
        card, programs = self.original_programs()
        ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile=PROFILE)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        self.assertEqual(2, len(programs))
        seen = set()
        for program in programs:
            self.assertEqual(("cast.static.rule", "battlefield"), (program.event, program.active_zone))
            self.assertIn(CAPABILITY, program.capability_dependencies)
            handler, = thaw_value(program.handlers)
            self.assertEqual(("all_players", "cast_prohibition"), (handler["player_scope"], handler["kind"]))
            query, = handler["spell_queries"]
            self.assertEqual(["creature", "land"], query["excluded_types"])
            seen.add(handler["condition"])
        self.assertEqual(CONDITIONS, seen)

    def test_fixed_mana_rule_scope_and_malformed_descriptors_fail_closed(self):
        card, programs = self.original_programs()
        for program in programs:
            handler, = thaw_value(program.handlers)
            query, = handler["spell_queries"]
            for changes in (
                {"player_scope": "source_opponents"},
                {"player_scope": "source_controller"},
                {"spell_queries": [{**query, "excluded_types": ["land"]}]},
                {"spell_queries": [{**query, "types_all": ["artifact"]}]},
                {"origin_zones": ["graveyard"]},
                {"chosen_name": True},
                {"schema_version": True},
            ):
                with self.subTest(condition=handler["condition"], changes=changes):
                    with self.assertRaises(SemanticNodeError):
                        replace(program, handlers=[{**handler, **changes}])
        for old, new in (("4 or greater", "5 or greater"), ("{X}", "{Y}"),
                         ("Noncreature spells", "Creature spells")):
            ir = compile_oracle_card(replace(card, oracle_text=card.oracle_text.replace(old, new)),
                capability_registry=self.registry, capability_profile=PROFILE)
            self.assertNotEqual("exact", ir.status, new)

    def _actions(self, game, seat):
        self.priority(game, seat)
        return {row["id"]: row for row in game.packet("pilot:" + seat, full=True)["decision"]["ctx"]["legal"]["actions"]}

    def _accept(self, game, seat, command):
        result = game.act("pilot:" + seat, command)
        self.assertTrue(result.ok, result.summary)

    def _reject_unchanged(self, game, seat, command):
        before = authoritative_state_hash(game.state)
        result = game.act("pilot:" + seat, command)
        self.assertFalse(result.ok, result.summary)
        self.assertEqual(before, authoritative_state_hash(game.state))

    def test_actual_four_mana_and_printed_x_are_prohibited_for_the_controller_and_opponents(self):
        game = self.session(76156821)
        teeg = self.add(game.engine, NAME)
        higher = self.add(game.engine, "Inspiration", zone="hand")
        other_higher = self.add(game.engine, "Inspiration", seat="B", zone="hand", ref="teeg-opponent-high-cost")
        variable = self.add(game.engine, "Blaze", zone="hand")
        boundary = self.add(game.engine, "Divination", zone="hand")
        creature = self.add(game.engine, "Frogmite", zone="hand")
        for card in (teeg, higher, other_higher, variable, boundary, creature):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 9, "U": 2, "R": 1})
        game.state.players["B"].mana_pool.update({"C": 3, "U": 1})
        self.checkpoint(game)
        offers = self._actions(game, "A")
        for cast, targets, fields in ((higher, ["C"], {}), (variable, ["D"], {"x": 0})):
            action_id = "cast:" + cast.ref
            self.assertNotIn(action_id, offers)
            self._reject_unchanged(game, "A", {"action_id": action_id, "targets": targets, "pay": "auto", **fields})
        self.assertIn("cast:" + boundary.ref, offers)
        self.assertIn("cast:" + creature.ref, offers)

        hand_size = len(game.state.players["A"].zones["hand"])
        self.cast(game, "A", boundary)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(hand_size + 1, len(game.state.players["A"].zones["hand"]))
        self.cast(game, "A", creature)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("battlefield", game.state.cards[creature.object_id].zone)

        other_id = "cast:" + other_higher.ref
        self.assertNotIn(other_id, self._actions(game, "B"))
        self._reject_unchanged(game, "B", {"action_id": other_id, "targets": ["C"], "pay": "auto"})
        self.replay(game, load=True)

    def test_actual_selected_split_face_and_paid_additional_cost_use_printed_mana_value(self):
        game = self.session(76156822)
        teeg = self.add(game.engine, NAME, seat="B")
        split = self.add(game.engine, "Assault // Battery", zone="hand")
        chalice = self.add(game.engine, "Everflowing Chalice", zone="hand")
        private = self.add(game.engine, "Inspiration", seat="C", zone="hand", ref="teeg-private-spell")
        for card in (teeg, split, chalice):
            self.assert_closed(game, card)
        self.prepare(game, mana={"R": 1, "G": 1, "C": 7})
        self.checkpoint(game)
        offers = self._actions(game, "A")
        assault = "cast:" + split.ref
        battery = assault + ":face:Battery"
        self.assertIn(assault, offers)
        self.assertEqual("Assault", offers[assault]["face"])
        self.assertNotIn(battery, offers)
        self._reject_unchanged(game, "A", {"action_id": battery, "face": "Battery", "pay": "auto"})
        self._accept(game, "A", {"action_id": assault, "face": "Assault", "targets": ["D"], "pay": "auto"})
        self.assertIsNone(self.resolve(game))
        self.assertEqual(38, game.state.players["D"].life)

        offer = self._actions(game, "A")["cast:" + chalice.ref]
        option, = (row for row in offer["cost_options"] if "+multikicker:" in row["id"])
        self._accept(game, "A", {"action_id": offer["id"], "cost_option": option["id"],
                                 "multikicker_count": 2, "pay": "auto"})
        spell = next(item for item in game.state.stack if item.card_object_id == chalice.object_id)
        self.assertEqual(4, spell.context["mana_spent_total"])
        self.assertIsNone(self.resolve(game))
        current = game.state.cards[chalice.object_id]
        self.assertEqual(("battlefield", 2), (current.zone, current.counters["charge"]))
        for seat in "ABD":
            self.assertNotIn(private.ref, json.dumps(game.packet("pilot:" + seat, full=True)))
        self.replay(game, load=True)

    def test_actual_lavinia_and_teeg_use_the_lower_current_cast_limit(self):
        game = self.session(76156823)
        lavinia = self.add(game.engine, "Lavinia, Azorius Renegade", seat="B")
        teeg = self.add(game.engine, NAME, seat="C")
        self.add(game.engine, "Island")
        extra = self.add(game.engine, "Island", zone="hand", ref="teeg-lavinia-extra-land")
        spell = self.add(game.engine, "Boomerang", zone="hand")
        target = self.add(game.engine, "Grizzly Bears", seat="D")
        for card in (lavinia, teeg, spell):
            self.assert_closed(game, card)
        self.prepare(game, mana={"U": 2})
        self.checkpoint(game)
        action_id = "cast:" + spell.ref
        self.assertNotIn(action_id, self._actions(game, "A"))
        self._accept(game, "A", {"action_id": "play-land:" + extra.ref})
        self.assertIn(action_id, self._actions(game, "A"))
        self.cast(game, "A", spell, [target.ref])
        self.assertIsNone(self.resolve(game))
        self.assertEqual("hand", game.state.cards[target.object_id].zone)
        self.replay(game, load=True)

    def test_omitting_either_exact_rule_restores_the_original_residual(self):
        from quorune.compiler import static_cast_rule_templates as owner

        original = owner._noncreature_mana_prohibition
        for omitted in CONDITIONS:
            def omit_exact(text):
                result = original(text)
                if result is not None and result[1]["condition"] == omitted:
                    return None
                return result

            with self.subTest(omitted=omitted), patch.object(owner, "_noncreature_mana_prohibition", side_effect=omit_exact):
                bound, _ = register_card(self.db, self.registry, NAME)
                other, _ = register_card(self.db, self.registry, "Lavinia, Azorius Renegade")
            self.assertFalse(bound["strict_capability_ready"], bound)
            self.assertTrue(other["strict_capability_ready"], other["blockers"])

    def test_verified_withdrawal_requires_static_prohibition_authority(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests

        raw = deepcopy(json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes()))
        next(row for row in raw["capabilities"] if row["id"] == CAPABILITY).update(
            status="blocked", blockers=["Fixed noncreature cast authority withdrawn"])
        registry = CapabilityRegistry(raw)
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        index = build_index(registry_value=raw, declaration_source=declarations,
                            discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
        closure = registry.closure((CAPABILITY,), profile=PROFILE)
        self.assertIn("status:" + CAPABILITY + ":blocked", closure.blockers)
        self.assertFalse(any("unverified" in reason for reason in closure.blockers))
        bound, _ = register_card(self.db, registry, NAME)
        other, _ = register_card(self.db, registry, "Boomerang")
        self.assertFalse(bound["strict_capability_ready"], bound)
        self.assertTrue(other["strict_capability_ready"], other["blockers"])
