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
from quorune.compiler import resolution_effect_templates
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.relative_power_target import GREATEST_CREATURE_POWER_PREDICATE
from quorune.replacement.immutable import thaw_value
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.greatest_power_target_shapes import GREATEST_POWER_TARGET_CAPABILITY
from quorune.zone_object_state import mark_card_manifested
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


PROFILE = "commander_review"
FIXTURES = ROOT / "tests" / "fixtures"
TOPPLE = "Topple"
TRIUMPH = "Triumph of Gerrard"
ORIGINALS = (TOPPLE, TRIUMPH)
BASTION = "Karn's Bastion"


class GreatestPowerTargetTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        folder = Path(cls.temporary.name)
        support = {
            "ability-label-composition-cards.json": {"Forest"},
            "ability-source-counter-cards.json": {"Sol Ring"},
            "activation-public-state-cards.json": {"Giant Growth"},
            "additional-continuous-carriers-cards.json": {"Walking Corpse"},
            "bound-effect-program-cards.json": {"Generic Bound Plains"},
            "counter-replacement-cards.json": {"Ancient Brontodon"},
            "event-spell-copy-cards.json": {BASTION},
            "greatest-power-target-cards.json": set(ORIGINALS),
            "power-toughness-switch-cards.json": {"Wall of Denial"},
            "qualified-attack-cards.json": {"Daru Lancer"},
            "regeneration-cards.json": {"Drudge Skeletons"},
            "source-attack-alone-cards.json": {"Isamaru, Hound of Konda"},
        }
        subsets = []
        for filename, names in support.items():
            document = json.loads((FIXTURES / filename).read_bytes())
            document["cards"] = [row for row in document["cards"] if row["name"] in names]
            assert {row["name"] for row in document["cards"]} == names, (filename, names)
            ids = {row["oracle_id"] for row in document["cards"]}
            document["rulings"] = [row for row in document.get("rulings", ()) if row["oracle_id"] in ids]
            piece = folder / filename
            piece.write_text(json.dumps(document), encoding="utf-8")
            subsets.append(piece)
        path = folder / "greatest-power-targets.sqlite3"
        build_fixture_database(subsets, path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Greatest-power targets", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"),
            DeckEntry("Generic Bound Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def _original(self, name):
        card = self.db.lookup(name, fuzzy=False)
        result, semantics = register_card(self.db, self.registry, name)
        self.assertTrue(result["strict_capability_ready"], result["blockers"])
        return card, semantics.programs_for_oracle(card.oracle_id)

    def test_complete_originals_keep_global_spell_and_controlled_chapter_target_shapes(self):
        for name in ORIGINALS:
            with self.subTest(name=name):
                card, programs = self._original(name)
                ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile=PROFILE)
                self.assertEqual("exact", ir.status, ir.material_residuals)
                expected = {"resolve"} if name == TOPPLE else {f"saga.chapter.{n}" for n in (1, 2, 3)}
                self.assertEqual(expected, {program.event for program in programs})
                for program in programs:
                    schema = thaw_value(program.target_schema)
                    self.assertEqual(GREATEST_CREATURE_POWER_PREDICATE, schema["predicate"])
                    self.assertEqual((1, ["creature"]), (schema["count"], schema["types_any"]))
                    self.assertEqual("any" if name == TOPPLE else "you", schema.get("controller_relation", "any"))
                    self.assertLessEqual({GREATEST_POWER_TARGET_CAPABILITY, "target.revalidate_resolution"},
                                         set(program.capability_dependencies))

    def test_ranked_target_shape_limits_literal_domains_counts_and_owned_results(self):
        card, (program,) = self._original(TOPPLE)
        schema = thaw_value(program.target_schema)
        for change in ({"count": True}, {"count": 2}, {"controller_relation": "you"},
                       {"types_any": ["artifact"]}, {"another": True}):
            with self.subTest(change=change):
                altered = replace(program, target_schema={**schema, **change})
                result = bind_semantic_program_runtime(altered, capability_registry=self.registry, profile=PROFILE)
                self.assertFalse(result["strict"], result)
        for phrase in ("the least power", "the greatest toughness", "the greatest power among artifacts"):
            text = card.oracle_text.replace("the greatest power among creatures on the battlefield", phrase, 1)
            ir = compile_oracle_card(replace(card, oracle_text=text),
                                     capability_registry=self.registry, capability_profile=PROFILE)
            self.assertTrue(ir.material_residuals, ir)

    def _reject(self, game, seat, command):
        before = authoritative_state_hash(game.state)
        result = game.act("pilot:" + seat, command)
        self.assertFalse(result.ok, result.summary)
        self.assertEqual(before, authoritative_state_hash(game.state))

    def _zone(self, game, card):
        return game.state.cards[card.object_id].zone

    def _cast_offer(self, game, card):
        self.priority(game, "A")
        actions = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        return next((action for action in actions if action.get("card") == card.ref), None)

    def test_actual_topple_cannot_choose_a_lower_creature_when_its_maximum_has_shroud(self):
        game = self.session(77145001)
        source = self.add(game.engine, TOPPLE, zone="hand")
        excluded = self.add(game.engine, "Wall of Denial", seat="B")
        excluded.counters["+1/+1"] = 3
        too_small = self.add(game.engine, "Walking Corpse", seat="C")
        for card in (source, excluded, too_small):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 2, "W": 1})
        self.checkpoint(game)
        self.assertIsNone(self._cast_offer(game, source))
        self._reject(game, "A", {"action_id": "cast:" + source.ref, "targets": [too_small.ref], "pay": "auto"})
        self.assertEqual("hand", self._zone(game, source))
        self.replay(game, load=True)

    def test_actual_topple_keeps_legal_ties_and_rechecks_the_maximum_after_a_response(self):
        for drop in (False, True):
            with self.subTest(drop=drop):
                game = self.session(77145010 + drop)
                source = self.add(game.engine, TOPPLE, zone="hand")
                untargetable = self.add(game.engine, "Wall of Denial", seat="B")
                untargetable.counters["+1/+1"] = 2
                selected = self.add(game.engine, "Walking Corpse", seat="C")
                lower = self.add(game.engine, "Drudge Skeletons", seat="B")
                growth = self.add(game.engine, "Giant Growth", seat="B", zone="hand")
                self.add(game.engine, "Forest", seat="B")
                for card in (source, untargetable, selected, lower, growth):
                    self.assert_closed(game, card)
                self.prepare(game, mana={"C": 2, "W": 1})
                self.checkpoint(game)
                offer = self._cast_offer(game, source)
                self.assertEqual([selected.ref], offer["target_schema"]["legal_refs"])
                self._reject(game, "A", {"action_id": offer["id"], "targets": [lower.ref], "pay": "auto"})
                self.cast(game, "A", source, [selected.ref])
                if drop:
                    self.cast(game, "B", growth, [lower.ref])
                    self.resolve_top(game)
                    self.assertGreater(game.engine._numeric_stat(lower.object_id, "power"),
                                       game.engine._numeric_stat(selected.object_id, "power"))
                game = self.replay(game, load=True)
                self.assertIsNone(self.resolve(game))
                self.assertEqual("battlefield" if drop else "exile", self._zone(game, selected))
                self.assertEqual("battlefield", self._zone(game, untargetable))
                self.assertEqual("graveyard", self._zone(game, source))
                self.replay(game, load=True)

    def test_actual_topple_uses_a_face_down_permanents_public_power_without_revealing_its_card(self):
        game = self.session(77145020)
        source = self.add(game.engine, TOPPLE, zone="hand")
        masked = self.add(game.engine, "Daru Lancer", seat="B", ref="topple-public-facedown-ref")
        candidate = self.add(game.engine, "Walking Corpse", seat="C")
        for card in (source, masked, candidate):
            self.assert_closed(game, card)
        mark_card_manifested(masked, controller="B")
        self.prepare(game, mana={"C": 2, "W": 1})
        self.checkpoint(game)
        self.assertEqual(2, game.engine._numeric_stat(masked.object_id, "power"))
        offer = self._cast_offer(game, source)
        self.assertEqual({masked.ref, candidate.ref}, set(offer["target_schema"]["legal_refs"]))
        self.assertNotIn("Daru Lancer", json.dumps(game.packet("pilot:A", full=True)))
        self.cast(game, "A", source, [candidate.ref])
        self.assertIsNone(self.resolve(game))
        self.assertEqual(("battlefield", "exile"), (self._zone(game, masked), self._zone(game, candidate)))
        self.replay(game, load=True)

    def _proliferate(self, game, land, saga):
        self.priority(game, "A")
        current_land = game.state.cards[land.object_id]
        ability = next(ability for ability in game.engine._activated_abilities(current_land) if not ability.mana_ability)
        action_id = f"activate:{current_land.ref}:{ability.ability_id}"
        legal = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        self.assertIn(action_id, {entry["id"] for entry in legal})
        result = game.act("pilot:A", {"action_id": action_id, "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("semantic.choice", self.resolve(game).kind)
        result = game.act("pilot:A", {"action_id": "choose", "objects": [saga.ref]})
        self.assertTrue(result.ok, result.summary)

    def _chapter_offer(self, game, expected):
        pending = game.state.pending_decision
        self.assertEqual(("semantic.target", ["A"]), (pending.kind, pending.actors))
        schema = pending.payload_by_actor["A"]["target_schema"]
        self.assertEqual({card.ref for card in expected}, set(schema["legal_refs"]))

    def test_actual_triumph_uses_only_its_controller_maxima_and_preserves_all_three_chapters(self):
        game = self.session(77145030)
        source = self.add(game.engine, TRIUMPH, zone="hand")
        first_land = self.add(game.engine, BASTION)
        second_land = self.add(game.engine, BASTION, ref="triumph-second-lore-source")
        first = self.add(game.engine, "Walking Corpse")
        changing = self.add(game.engine, "Walking Corpse", ref="triumph-rising-ally")
        foreign = self.add(game.engine, "Ancient Brontodon", seat="B")
        growth = self.add(game.engine, "Giant Growth", seat="B", zone="hand")
        self.add(game.engine, "Forest", seat="B")
        for card in (source, first_land, second_land, first, changing, foreign, growth):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 9, "W": 1})
        self.checkpoint(game)
        self.cast(game, "A", source)
        self.resolve_top(game)
        self._chapter_offer(game, (first, changing))
        self._reject(game, "A", {"action_id": "choose", "targets": [foreign.ref]})
        self.target(game, "A", [first.ref])
        self.assertIsNone(self.resolve(game))
        self.assertEqual(1, game.state.cards[first.object_id].counters["+1/+1"])
        self._proliferate(game, first_land, source)
        self._chapter_offer(game, (first,))
        self.target(game, "A", [first.ref])
        self.cast(game, "B", growth, [changing.ref])
        self.resolve_top(game)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(1, game.state.cards[first.object_id].counters["+1/+1"])
        self.assertFalse(game.state.cards[changing.object_id].counters)
        self._proliferate(game, second_land, source)
        self._chapter_offer(game, (changing,))
        self.target(game, "A", [changing.ref])
        self.assertEqual("battlefield", self._zone(game, source))
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertLessEqual({"flying", "first strike", "lifelink"},
                             game.engine._combat_keywords(game.state.cards[changing.object_id]))
        self.assertEqual("graveyard", self._zone(game, source))
        self.replay(game, load=True)

    def test_omitting_the_ranked_target_lowering_keeps_both_originals_partial(self):
        with patch.object(resolution_effect_templates, "_greatest_power_target_template", return_value=None):
            for name in ORIGINALS:
                result, _ = register_card(self.db, self.registry, name)
                self.assertFalse(result["strict_capability_ready"], result)
            control, _ = register_card(self.db, self.registry, "Sol Ring")
            self.assertTrue(control["strict_capability_ready"], control["blockers"])

    def test_verified_maximum_and_revalidation_withdrawals_block_the_originals(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests

        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        known = set(discover_tests(ROOT).values())
        for capability in (GREATEST_POWER_TARGET_CAPABILITY, "target.revalidate_resolution"):
            with self.subTest(capability=capability):
                document = deepcopy(raw)
                next(row for row in document["capabilities"] if row["id"] == capability).update(
                    status="blocked", blockers=["Scoped maximum or ordinary target revalidation withdrawn"])
                registry = CapabilityRegistry(document)
                index = build_index(registry_value=document, declaration_source=declarations, discovered_test_ids=known)
                registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
                closure = registry.closure((capability,), profile=PROFILE)
                self.assertIn("status:" + capability + ":blocked", closure.blockers)
                self.assertFalse(any("unverified" in blocker for blocker in closure.blockers))
                for name in ORIGINALS:
                    result, _ = register_card(self.db, registry, name)
                    self.assertFalse(result["strict_capability_ready"], result)
                control, _ = register_card(self.db, registry, "Sol Ring")
                self.assertTrue(control["strict_capability_ready"], control["blockers"])


if __name__ == "__main__":
    unittest.main()
