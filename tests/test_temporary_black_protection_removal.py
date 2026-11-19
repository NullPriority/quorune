from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.ability_fragments import ProtectionQualityKind, ProtectionSpec, protection_specs
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.compiler.direct_target import direct_permanent_target_spec
from quorune.continuous_effect_model import ContinuousEffectError, ContinuousOperation
from quorune.continuous_effects import CharacteristicState, _apply_ability_operation
from quorune.deck import DeckDefinition, DeckEntry
from quorune.next_turn_effect import NEXT_TURN_EFFECT_FIELD
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.temporary_keyword_removal import CAPABILITY, PROTECTION_FROM_BLACK, temporary_keyword_removal_instruction
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


SNITCH = "Cephalid Snitch"
PALADIN = "Paladin en-Vec"
PROFILE = "commander_review"
FIXTURES = ROOT / "tests" / "fixtures"
ORIGINALS = FIXTURES / "temporary-black-protection-removal-cards.json"
PROTECTION_CAPABILITY = "protection.typed.debt"
TARGET_CAPABILITY = "target.revalidate_resolution"
SACRIFICE_CAPABILITY = "activation.source_zone_change.fixed"


class TemporaryBlackProtectionRemovalTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        folder = Path(cls.temporary.name)
        selected = {
            "bound-effect-program-cards.json": {"Generic Bound Plains"},
            "source-attack-alone-cards.json": {"Isamaru, Hound of Konda", "Grizzly Bears", "Island", "Unsummon"},
            "attached-destruction-set-cards.json": {"Murder", "Shock", "Swamp", "Mountain"},
        }
        fixtures = [ORIGINALS]
        for name, names in selected.items():
            data = json.loads((FIXTURES / name).read_bytes())
            data["cards"] = [card for card in data["cards"] if card["name"] in names]
            assert {card["name"] for card in data["cards"]} == names
            ids = {card["oracle_id"] for card in data["cards"]}
            data["rulings"] = [row for row in data.get("rulings", ()) if row["oracle_id"] in ids]
            snapshot = folder / name
            snapshot.write_text(json.dumps(data), encoding="utf-8")
            fixtures.append(snapshot)
        path = folder / "temporary-black-protection-removal.sqlite3"
        build_fixture_database(fixtures, path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Temporary color protection removal", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def test_complete_original_uses_source_sacrifice_and_one_typed_color_removal(self):
        card = self.db.lookup(SNITCH, fuzzy=False)
        ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile=PROFILE)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        node, = ir.faces[0].nodes
        self.assertEqual(direct_permanent_target_spec("target creature").to_target_schema(), node.target_schema)
        self.assertTrue({CAPABILITY, PROTECTION_CAPABILITY, TARGET_CAPABILITY, SACRIFICE_CAPABILITY}
            <= set(node.capability_dependencies))
        leaf, = node.effects
        self.assertEqual([PROTECTION_FROM_BLACK], leaf["remove_keywords"])
        self.assertEqual("$target.0", temporary_keyword_removal_instruction(leaf))
        admitted, _ = register_card(self.db, self.registry, SNITCH)
        self.assertTrue(admitted["strict_capability_ready"], admitted["blockers"])

    def test_compiler_and_runtime_exclude_unrepresented_qualities_subjects_and_durations(self):
        card = self.db.lookup(SNITCH, fuzzy=False)
        for old, new in (
            ("from black", "from red"), ("from black", "from everything"),
            ("Target creature", "Target creature you control"), ("Target creature", "Creatures you control"),
            ("until end of turn", "until your next turn"),
            ("loses protection", "gets +1/+1 and loses protection"),
        ):
            with self.subTest(new=new):
                raw = replace(card, oracle_text=card.oracle_text.replace(old, new))
                ir = compile_oracle_card(raw, capability_registry=self.registry, capability_profile=PROFILE)
                self.assertNotEqual("exact", ir.status)

        _, semantics = register_card(self.db, self.registry, SNITCH)
        program, = [p for p in semantics.programs_for_oracle(card.oracle_id) if p.event == "activate"]
        effect, = program.effects
        for changes in (
            {"remove_keywords": ["Protection from Red"]},
            {"remove_keywords": [PROTECTION_FROM_BLACK, "Flying"]},
            {"keywords": ["Flying"]},
            {NEXT_TURN_EFFECT_FIELD: True},
            {"card": "$source.zone_object"},
        ):
            with self.subTest(changes=changes):
                bound = bind_semantic_program_runtime(replace(program, effects=({**effect, **changes},)),
                    capability_registry=self.registry, profile=PROFILE)
                self.assertFalse(bound["strict"], bound)
                self.assertTrue(any("removal" in blocker for blocker in bound["blockers"]), bound["blockers"])

    def offer_snitch(self, game, source):
        self.priority(game, "A")
        actions = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        action, = [row for row in actions if row["id"].startswith("activate:" + source.ref + ":")]
        return action

    def remove_protection(self, game, source, target):
        action = self.offer_snitch(game, source)
        self.assertIn(target.ref, action["target_schema"]["legal_refs"])
        accepted = game.act("pilot:A", {"action_id": action["id"], "targets": [target.ref], "pay": "auto"})
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertEqual("graveyard", game.state.cards[source.object_id].zone)
        self.assertEqual("battlefield", game.state.cards[target.object_id].zone)
        self.assertEqual(1, len(game.state.stack))

    @staticmethod
    def protection_colors(game, card):
        fragments = game.engine._effective_ability_fragments(game.state.cards[card.object_id])
        return {spec.quality for spec in protection_specs(fragments) if spec.quality_kind is ProtectionQualityKind.COLOR}

    def test_actual_paid_removal_enables_black_target_while_red_still_cannot_target(self):
        game = self.session(74098211)
        snitch = self.add(game.engine, SNITCH, zone="hand")
        target = self.add(game.engine, PALADIN, seat="B")
        alternative = self.add(game.engine, "Grizzly Bears", seat="B")
        murder = self.add(game.engine, "Murder", zone="hand")
        shock = self.add(game.engine, "Shock", zone="hand")
        private = self.add(game.engine, "Unsummon", seat="D", zone="hand", ref="black-removal-private-hand")
        self.add(game.engine, "Island")
        self.add(game.engine, "Mountain")
        for n in range(3):
            self.add(game.engine, "Swamp", ref=f"black-removal-swamp-{n}")
        for card in (snitch, target, murder, shock):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 1})
        self.checkpoint(game)
        self.cast(game, "A", snitch)
        self.resolve(game)
        self.priority(game, "A")
        snitch = game.state.cards[snitch.object_id]
        self.assertEqual("battlefield", snitch.zone)
        self.assertEqual({"B", "R"}, self.protection_colors(game, target))

        black_offer = self.action(game, "A", murder)
        self.assertIn(alternative.ref, black_offer["target_schema"]["legal_refs"])
        self.assertNotIn(target.ref, black_offer["target_schema"]["legal_refs"])
        self.assertNotIn(target.ref, self.action(game, "A", shock)["target_schema"]["legal_refs"])
        before = authoritative_state_hash(game.state)
        rejected = game.act("pilot:A", {"action_id": black_offer["id"], "targets": [target.ref], "pay": "auto"})
        self.assertFalse(rejected.ok)
        self.assertEqual(before, authoritative_state_hash(game.state))

        self.remove_protection(game, snitch, target)
        self.assertEqual({"B", "R"}, self.protection_colors(game, target))
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual({"R"}, self.protection_colors(game, target), "Layer six must remove only black protection")
        self.priority(game, "A")
        self.assertIn(target.ref, self.action(game, "A", murder)["target_schema"]["legal_refs"])
        self.assertNotIn(target.ref, self.action(game, "A", shock)["target_schema"]["legal_refs"])
        self.cast(game, "A", murder, [target.ref])
        self.assertIsNone(self.resolve(game))
        self.assertEqual("graveyard", game.state.cards[target.object_id].zone)
        for seat in "ABC":
            packet = json.dumps(game.packet("pilot:" + seat, full=True))
            self.assertNotIn(private.ref, packet)
            self.assertNotIn(private.printed_name, packet)
        self.replay(game, load=True)

    def test_actual_paid_removal_expires_at_cleanup_and_restores_both_protections(self):
        game = self.session(74098212)
        snitch = self.add(game.engine, SNITCH)
        target = self.add(game.engine, PALADIN, seat="B")
        self.prepare(game)
        self.checkpoint(game)
        self.remove_protection(game, snitch, target)
        self.assertIsNone(self.resolve(game))
        self.assertEqual({"R"}, self.protection_colors(game, target))
        self.until(game, lambda session: session.state.active_player == "B")
        self.assertEqual({"B", "R"}, self.protection_colors(game, target))
        self.assertEqual("graveyard", game.state.cards[snitch.object_id].zone)
        self.replay(game, load=True)

    def test_actual_missing_target_does_not_refund_the_paid_source_sacrifice(self):
        game = self.session(74098213)
        snitch = self.add(game.engine, SNITCH)
        target = self.add(game.engine, PALADIN, seat="B")
        bounce = self.add(game.engine, "Unsummon", seat="B", zone="hand")
        self.add(game.engine, "Island", seat="B")
        self.prepare(game)
        self.checkpoint(game)
        self.remove_protection(game, snitch, target)
        self.cast(game, "B", bounce, [target.ref])
        self.resolve_top(game)
        self.assertEqual("hand", game.state.cards[target.object_id].zone)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("graveyard", game.state.cards[snitch.object_id].zone)
        self.assertEqual("hand", game.state.cards[target.object_id].zone)
        self.assertFalse(game.state.stack)
        self.replay(game, load=True)

    def test_layer_removes_matching_color_fragments_and_only_a_redundant_marker(self):
        black = ProtectionSpec(ProtectionQualityKind.COLOR, "B")
        retained_aura = ProtectionSpec(ProtectionQualityKind.COLOR, "B", schema_version=3,
            exempt_aura_incarnation="aura-incarnation")
        red = ProtectionSpec(ProtectionQualityKind.COLOR, "R")
        everything = ProtectionSpec(ProtectionQualityKind.EVERYTHING)
        operation = ContinuousOperation("remove_color_protection", "B")
        self.assertEqual(operation, ContinuousOperation.from_dict(operation.to_dict()))
        for value in ("b", "black", "C", None):
            with self.subTest(value=value), self.assertRaises(ContinuousEffectError):
                ContinuousOperation("remove_color_protection", value)
        legacy = ContinuousOperation("remove_ability", PROTECTION_FROM_BLACK)
        old = CharacteristicState("Historical instruction", abilities=["Protection"], ability_fragments=[black])
        _apply_ability_operation(old, ContinuousOperation.from_dict(legacy.to_dict()))
        self.assertEqual(([black], ["Protection"]), (old.ability_fragments, old.abilities))
        for original, remaining, labels in (
            ([black, retained_aura, red, everything], [red, everything], ["Protection", "First Strike"]),
            ([black], [], ["First Strike"]),
            ([], [], ["Protection", "First Strike"]),
        ):
            with self.subTest(original=original):
                state = CharacteristicState("Protection witness", abilities=["Protection", "First Strike"],
                    ability_fragments=original)
                _apply_ability_operation(state, operation)
                self.assertEqual(remaining, state.ability_fragments)
                self.assertEqual(labels, state.abilities)

    def test_omitting_the_typed_layer_result_breaks_actual_target_availability(self):
        with patch("quorune.continuous_effects.ProtectionSpec", type(None)):
            with self.assertRaisesRegex(AssertionError, "Layer six must remove only black protection"):
                self.test_actual_paid_removal_enables_black_target_while_red_still_cannot_target()

    def test_verified_withdrawals_preserve_unrelated_complete_creature(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests

        source = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        known = set(discover_tests(ROOT).values())

        def verified(rows):
            registry = CapabilityRegistry(rows)
            index = build_index(registry_value=rows, declaration_source=declarations, discovered_test_ids=known)
            registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
            return registry

        names = (SNITCH, PALADIN, "Grizzly Bears")
        baseline = verified(deepcopy(source))
        for name in names:
            admitted, _ = register_card(self.db, baseline, name)
            self.assertTrue(admitted["strict_capability_ready"], admitted["blockers"])
        for capability, blocked in (
            (CAPABILITY, {SNITCH}), (PROTECTION_CAPABILITY, {SNITCH, PALADIN}),
            (SACRIFICE_CAPABILITY, {SNITCH}), (TARGET_CAPABILITY, {SNITCH, PALADIN}),
        ):
            with self.subTest(capability=capability):
                rows = deepcopy(source)
                next(row for row in rows["capabilities"] if row["id"] == capability).update(
                    status="blocked", blockers=["Temporary protection removal requirement withdrawn"])
                registry = verified(rows)
                closure = registry.closure((capability,), profile=PROFILE)
                self.assertIn("status:" + capability + ":blocked", closure.blockers)
                self.assertFalse(any("unverified" in blocker for blocker in closure.blockers))
                for name in names:
                    admitted, _ = register_card(self.db, registry, name)
                    self.assertEqual(name not in blocked, admitted["strict_capability_ready"],
                        (name, admitted["blockers"]))
