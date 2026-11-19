from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import test_temporary_keyword_removal as previous_removal
from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.compiler.direct_target import direct_permanent_target_spec
from quorune.continuous_effect_model import ContinuousEffect, ContinuousEffectError, ContinuousOperation, Layer
from quorune.continuous_effects import CharacteristicState, _ability_presence_dependency_ids, _apply_ability_operation
from quorune.deck import DeckDefinition, DeckEntry
from quorune.landwalk_vocabulary import LANDWALK_KEYWORDS
from quorune.next_turn_effect import NEXT_TURN_EFFECT_FIELD
from quorune.object_predicate import ObjectQuerySpec
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.temporary_keyword_removal import ALL_LANDWALK_ABILITIES, CAPABILITY
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


HAMMERHEIM = "Hammerheim"
ATTACKER = "Livonya Silone"
AURA = "Burrowing"
PROFILE = "commander_review"
LANDWALK_CAPABILITIES = ("combat.block.landwalk.basic_type", "combat.block.landwalk.qualified_type")
FIXTURES = ROOT / "tests" / "fixtures"


class TemporaryLandwalkRemovalTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        folder = Path(cls.temporary.name)
        fixtures = [FIXTURES / "temporary-landwalk-removal-cards.json"]
        selected = {
            "bound-effect-program-cards.json": {"Generic Bound Plains"},
            "source-attack-alone-cards.json": {"Isamaru, Hound of Konda"},
            "attached-destruction-set-cards.json": {"Grizzly Bears", "Mountain", "Unsummon"},
        }
        for filename, names in selected.items():
            data = json.loads((FIXTURES / filename).read_bytes())
            data["cards"] = [card for card in data["cards"] if card["name"] in names]
            assert {card["name"] for card in data["cards"]} == names
            ids = {card["oracle_id"] for card in data["cards"]}
            data["rulings"] = [row for row in data.get("rulings", ()) if row["oracle_id"] in ids]
            chosen = folder / filename
            chosen.write_text(json.dumps(data), encoding="utf-8")
            fixtures.append(chosen)
        path = folder / "temporary-landwalk-removal.sqlite3"
        build_fixture_database(fixtures, path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Temporary all-landwalk removal", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def test_complete_original_keeps_independent_red_mana_and_closed_target_removal(self):
        card = self.db.lookup(HAMMERHEIM, fuzzy=False)
        ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile=PROFILE)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        mana, removal = ir.faces[0].nodes
        self.assertIn("mana.activated.fixed_output", mana.capability_dependencies)
        self.assertTrue({CAPABILITY, "target.revalidate_resolution", *LANDWALK_CAPABILITIES}
            <= set(removal.capability_dependencies))
        self.assertEqual(direct_permanent_target_spec("target creature").to_target_schema(), removal.target_schema)
        leaf, = removal.effects
        self.assertEqual((11, "$target.0", [ALL_LANDWALK_ABILITIES]),
            (leaf["schema_version"], leaf["card"], leaf["remove_keywords"]))
        admitted, _ = register_card(self.db, self.registry, HAMMERHEIM)
        self.assertTrue(admitted["strict_capability_ready"], admitted["blockers"])

    def test_removed_family_excludes_other_targets_durations_subsets_and_companion_changes(self):
        card = self.db.lookup(HAMMERHEIM, fuzzy=False)
        for original, changed in (
            ("Target creature", "Target attacking creature"),
            ("Target creature", "Creatures you control"),
            ("all landwalk abilities", "all snow landwalk abilities"),
            ("until end of turn", "until your next turn"),
            ("loses all", "gets +1/+1 and loses all"),
        ):
            with self.subTest(changed=changed):
                alternative = replace(card, oracle_text=card.oracle_text.replace(original, changed))
                ir = compile_oracle_card(alternative, capability_registry=self.registry, capability_profile=PROFILE)
                self.assertNotEqual("exact", ir.status)

        _, semantics = register_card(self.db, self.registry, HAMMERHEIM)
        ability, = [program for program in semantics.programs_for_oracle(card.oracle_id)
            if program.event == "activate" and program.target_schema]
        effect, = ability.effects
        for changes in (
            {"card": "$source.zone_object"},
            {"remove_keywords": [ALL_LANDWALK_ABILITIES, "Flying"]},
            {"keywords": ["Mountainwalk"]},
            {NEXT_TURN_EFFECT_FIELD: True},
        ):
            with self.subTest(changes=changes):
                bound = bind_semantic_program_runtime(replace(ability, effects=({**effect, **changes},)),
                    capability_registry=self.registry, profile=PROFILE)
                self.assertFalse(bound["strict"], bound)
                self.assertTrue(any("removal" in blocker for blocker in bound["blockers"]), bound["blockers"])

    def tap_offer(self, game, land, *, targeted):
        self.priority(game, "A")
        actions = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        action, = [row for row in actions if row["id"].startswith("activate:" + land.ref + ":")
            and bool(row.get("target_schema")) == targeted]
        return action

    def activate_removal(self, game, land, target):
        offered = self.tap_offer(game, land, targeted=True)
        self.assertIn(target.ref, offered["target_schema"]["legal_refs"])
        accepted = game.act("pilot:A", {"action_id": offered["id"], "targets": [target.ref], "pay": "auto"})
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertTrue(game.state.cards[land.object_id].tapped)
        self.assertEqual(1, len(game.state.stack))

    def combat_fixture(self, seed, *, grant_mountainwalk, extra_attacker=False):
        game = self.session(seed)
        land = self.add(game.engine, HAMMERHEIM)
        attacker = self.add(game.engine, ATTACKER, seat="B")
        attacker.acquired_control_turn_count = -1
        blocker = self.add(game.engine, "Grizzly Bears")
        private = self.add(game.engine, "Unsummon", seat="D", zone="hand", ref="temporary-landwalk-private")
        self.assert_closed(game, land)
        self.assert_closed(game, attacker)
        attackers = {attacker.ref: "A"}
        if extra_attacker:
            ordinary = self.add(game.engine, "Grizzly Bears", seat="B", ref="ordinary-second-attacker")
            ordinary.acquired_control_turn_count = -1
            attackers[ordinary.ref] = "A"
        if grant_mountainwalk:
            aura = self.add(game.engine, AURA, seat="B", zone="hand")
            self.assert_closed(game, aura)
            self.add(game.engine, "Mountain", ref="defending-mountain")
            self.add(game.engine, "Mountain", seat="B", ref="aura-payment-mountain")
        self.prepare(game, active="B", actor="B")
        self.checkpoint(game)
        if grant_mountainwalk:
            self.cast(game, "B", aura, [attacker.ref])
            self.assertIsNone(self.resolve(game))
            self.assertIn("mountainwalk", game.engine._combat_keywords(game.state.cards[attacker.object_id]))
        self.until(game, lambda s: s.state.pending_decision.kind == "combat.attackers")
        accepted = game.act("pilot:B", {"action_id": "attack", "attackers": attackers})
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertFalse(game.engine._can_block(game.state.cards[attacker.object_id], blocker)[0])
        return game, land, attacker, blocker, private

    def test_actual_paid_removal_allows_a_block_and_preserves_first_strike_until_cleanup(self):
        game, land, attacker, blocker, private = self.combat_fixture(60518211, grant_mountainwalk=True)
        before = game.engine._combat_keywords(game.state.cards[attacker.object_id])
        self.assertTrue({"mountainwalk", "legendary landwalk", "first strike"} <= before)
        self.activate_removal(game, land, attacker)
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        effective = game.engine._combat_keywords(game.state.cards[attacker.object_id])
        self.assertEqual({"first strike"}, effective, "All represented landwalk variants must leave")

        self.until(game, lambda s: s.state.pending_decision.kind == "combat.blockers")
        block_options = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal_blocks"]
        self.assertIn(attacker.ref, block_options[blocker.ref])
        blocked = game.act("pilot:A", {"action_id": "block", "blocks": {blocker.ref: attacker.ref}})
        self.assertTrue(blocked.ok, blocked.summary)
        self.assertEqual([blocker.object_id], game.state.combat.blockers[attacker.object_id])
        previous_removal.TemporaryKeywordRemovalRuntimeTests.advance(self, game, lambda s: s.state.active_player == "C")
        restored = game.engine._combat_keywords(game.state.cards[attacker.object_id])
        self.assertEqual(before, restored)
        for seat in "ABC":
            self.assertNotIn(private.ref, json.dumps(game.packet("pilot:" + seat, full=True)))
        self.replay(game, load=True)

    def test_actual_late_removal_leaves_existing_block_declaration_unchanged(self):
        game, land, attacker, blocker, _ = self.combat_fixture(60518212,
            grant_mountainwalk=False, extra_attacker=True)
        self.until(game, lambda s: s.state.pending_decision.kind == "combat.blockers")
        block = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal_blocks"]
        self.assertNotIn(attacker.ref, block[blocker.ref])
        ordinary, = block[blocker.ref]
        accepted = game.act("pilot:A", {"action_id": "block", "blocks": {blocker.ref: ordinary}})
        self.assertTrue(accepted.ok, accepted.summary)
        prior = deepcopy(game.state.combat.blockers)
        self.assertEqual("declare_blockers", game.state.step)
        self.activate_removal(game, land, attacker)
        self.assertIsNone(self.resolve(game))
        self.assertNotIn("legendary landwalk", game.engine._combat_keywords(game.state.cards[attacker.object_id]))
        self.assertEqual(prior, game.state.combat.blockers)
        self.assertFalse(game.state.combat.blockers.get(attacker.object_id))
        self.replay(game, load=True)

    def test_actual_nonwalking_target_is_legal_with_no_keyword_change(self):
        game = self.session(60518213)
        land = self.add(game.engine, HAMMERHEIM)
        ordinary = self.add(game.engine, "Grizzly Bears", seat="C")
        self.prepare(game)
        self.checkpoint(game)
        offered = self.tap_offer(game, land, targeted=True)
        before = authoritative_state_hash(game.state)
        foreign = game.act("pilot:C", {"action_id": offered["id"], "targets": [ordinary.ref], "pay": "auto"})
        self.assertFalse(foreign.ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        keywords = game.engine._combat_keywords(ordinary)
        self.activate_removal(game, land, ordinary)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(keywords, game.engine._combat_keywords(game.state.cards[ordinary.object_id]))
        self.replay(game, load=True)

    def test_actual_red_mana_is_its_own_printed_tap_action(self):
        game = self.session(60518214)
        land = self.add(game.engine, HAMMERHEIM)
        creature = self.add(game.engine, "Grizzly Bears")
        self.assert_closed(game, land)
        self.prepare(game)
        self.checkpoint(game)
        removal = self.tap_offer(game, land, targeted=True)
        self.assertIn(creature.ref, removal["target_schema"]["legal_refs"])
        mana = self.tap_offer(game, land, targeted=False)
        paid = game.act("pilot:A", {"action_id": mana["id"], "pay": "auto"})
        self.assertTrue(paid.ok, paid.summary)
        self.assertEqual(1, game.state.players["A"].mana_pool.get("R"))
        self.assertTrue(game.state.cards[land.object_id].tapped)
        self.assertFalse(game.state.stack)
        self.replay(game, load=True)

    def test_typed_layer_removal_preserves_unrelated_and_unrepresented_abilities(self):
        known = [keyword.title() for keyword in LANDWALK_KEYWORDS]
        operation = ContinuousOperation("remove_landwalk_abilities")
        self.assertEqual(operation, ContinuousOperation.from_dict(operation.to_dict()))
        with self.assertRaises(ContinuousEffectError):
            ContinuousOperation("remove_landwalk_abilities", "Mountainwalk")
        for keywords, remaining in (
            (["Landwalk", *known, "Flying", "Horsemanship"], ["Flying", "Horsemanship"]),
            (["Landwalk", "Islandwalk", "Unknownwalk"], ["Landwalk", "Unknownwalk"]),
            (["Landwalk"], ["Landwalk"]),
        ):
            with self.subTest(keywords=keywords):
                state = CharacteristicState("Landwalk witness", abilities=keywords)
                _apply_ability_operation(state, operation)
                self.assertEqual(remaining, state.abilities)
        previous = CharacteristicState("Legacy operation", abilities=["Landwalk", "Islandwalk"])
        _apply_ability_operation(previous, ContinuousOperation("remove_ability", ALL_LANDWALK_ABILITIES))
        self.assertEqual(["Landwalk", "Islandwalk"], previous.abilities)

    def test_same_layer_keyword_qualification_depends_on_landwalk_removal(self):
        grant = ContinuousEffect("grant", "grant-source", Layer.ABILITY, "6", 1,
            (ContinuousOperation("add_ability", "Flying"),), applies=ObjectQuerySpec(keywords_all=("islandwalk",)))
        removal = ContinuousEffect("removal", "removal-source", Layer.ABILITY, "6", 2,
            (ContinuousOperation("remove_landwalk_abilities"),))
        self.assertEqual(frozenset({"removal"}), _ability_presence_dependency_ids(grant, (grant, removal)))
        not_landwalk = replace(grant, applies=ObjectQuerySpec(keywords_all=("vigilance",)))
        self.assertEqual(frozenset(), _ability_presence_dependency_ids(not_landwalk, (not_landwalk, removal)))

    def test_omitting_a_qualified_variant_breaks_the_actual_attack_witness(self):
        with patch("quorune.continuous_effects.LANDWALK_KEYWORDS", ("mountainwalk",)):
            with self.assertRaisesRegex(AssertionError, "All represented landwalk variants must leave"):
                self.test_actual_paid_removal_allows_a_block_and_preserves_first_strike_until_cleanup()

    def test_verified_withdrawals_require_both_landwalk_owners_and_keep_mana_mode_compilable(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests

        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        known = set(discover_tests(ROOT).values())

        def verified(rows):
            registry = CapabilityRegistry(rows)
            index = build_index(registry_value=rows, declaration_source=declarations, discovered_test_ids=known)
            registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
            return registry

        for capability in (CAPABILITY, *LANDWALK_CAPABILITIES):
            with self.subTest(capability=capability):
                rows = deepcopy(raw)
                next(row for row in rows["capabilities"] if row["id"] == capability).update(
                    status="blocked", blockers=["Landwalk removal requirement withdrawn"])
                registry = verified(rows)
                closure = registry.closure((capability,), profile=PROFILE)
                self.assertIn("status:" + capability + ":blocked", closure.blockers)
                self.assertFalse(any("unverified" in blocker for blocker in closure.blockers))
                result, _ = register_card(self.db, registry, HAMMERHEIM)
                self.assertFalse(result["strict_capability_ready"], result)
                unrelated, _ = register_card(self.db, registry, "Grizzly Bears")
                self.assertTrue(unrelated["strict_capability_ready"], unrelated["blockers"])
                card = self.db.lookup(HAMMERHEIM, fuzzy=False)
                ir = compile_oracle_card(card, capability_registry=registry, capability_profile=PROFILE)
                self.assertIn("mana.activated.fixed_output", ir.faces[0].nodes[0].capability_dependencies)
