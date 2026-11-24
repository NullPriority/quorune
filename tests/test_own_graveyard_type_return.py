"""Actual Creeping Renaissance permanent-type choices and Flashback."""

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
from quorune.compiler import public_zone_move_templates
from quorune.deck import DeckDefinition, DeckEntry
from quorune.external_entry_state_model import PERMANENT_CARD_TYPES
from quorune.oracle_ir import compile_oracle_card
from quorune.public_zone_moves import (
    OWN_GRAVEYARD_TYPE_CHOICE_FIELD, OWN_GRAVEYARD_TYPE_RETURN_CAPABILITY,
    PublicZoneMoveSetSpec, own_graveyard_type_return_choice_effect, own_graveyard_type_return_dependencies,
)
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.public_zone_move_capability_shapes import fixed_public_zone_move_set_node_capabilities
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


ORIGINAL = "Creeping Renaissance"
PROFILE = "commander_review"
FIXTURES = ROOT / "tests" / "fixtures"


class OwnGraveyardTypeReturnTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        folder = Path(cls.temporary.name)
        support = {
            "attached-destruction-set-cards.json": {"Darksteel Myr", "Forest", "Grizzly Bears", "Sol Ring"},
            "public-composition-cards.json": {"Crystal Chimes"},
            "bound-effect-program-cards.json": {"Generic Bound Plains", "Generic Bound Growth"},
            "source-attack-alone-cards.json": {"Isamaru, Hound of Konda"},
        }
        parts = [FIXTURES / "own-graveyard-type-return-cards.json"]
        for name, names in support.items():
            data = json.loads((FIXTURES / name).read_bytes())
            data["cards"] = [card for card in data["cards"] if card["name"] in names]
            assert names == {card["name"] for card in data["cards"]}, name
            ids = {card["oracle_id"] for card in data["cards"]}
            data["rulings"] = [ruling for ruling in data.get("rulings", ()) if ruling["oracle_id"] in ids]
            part = folder / name
            part.write_text(json.dumps(data), encoding="utf-8")
            parts.append(part)
        path = folder / "own-graveyard-type-return.sqlite3"
        build_fixture_database(parts, path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Own graveyard permanent types", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"),
            DeckEntry("Generic Bound Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def _cast_and_wait(self, game, spell, available_mana):
        self.priority(game, "A")
        actions = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        action, = [row for row in actions if row.get("card") == spell.ref]
        command = {"action_id": action["id"], "pay": "auto"}
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:C", command).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        receipt = game.act("pilot:A", command)
        self.assertTrue(receipt.ok, receipt.summary)
        self.assertEqual([], game.state.stack[-1].targets)
        self.assertEqual(available_mana, sum(game.state.players["A"].mana_pool.values()))
        pending = self.resolve(game)
        self.assertEqual(("semantic.choice", ["A"]), (pending.kind, pending.actors))

    def _choose(self, game, card_type):
        actions = game.packet("pilot:A", full=True)["decision"]["legal_actions"]
        offer, = actions
        self.assertEqual("choose", offer["id"])
        self.assertEqual(sorted(PERMANENT_CARD_TYPES), offer["choice_schema"]["legal_values"])
        for seat in "BCD":
            self.assertIsNone(game.packet("pilot:" + seat, full=True)["decision"])
        command = {"action_id": offer["id"], "choice": card_type}
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:B", command).ok)
        for other in ("kindred", "instant", "sorcery"):
            self.assertFalse(game.act("pilot:A", {**command, "choice": other}).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        receipt = game.act("pilot:A", command)
        self.assertTrue(receipt.ok, receipt.summary)
        self.assertIsNone(self.resolve(game))

    def test_versioned_option_has_all_six_typed_owner_hand_branches(self):
        effect = own_graveyard_type_return_choice_effect()
        self.assertEqual(
            ["artifact", "battle", "creature", "enchantment", "land", "planeswalker"],
            [value["id"] for value in effect["options"]],
        )
        for card_type, effects in effect["then_by_choice"].items():
            move, = effects
            self.assertEqual("move_public_zone_set", move["op"])
            spec = PublicZoneMoveSetSpec.from_dict(move["set"])
            self.assertEqual(("graveyard",), spec.query.zones)
            self.assertEqual((card_type,), spec.query.types_all)
            self.assertEqual(("owner", "actor", "hand"), (spec.relation_axis.value, spec.seat_relation.value, spec.destination.value))
            self.assertIs(spec.query.token, False)
        required = own_graveyard_type_return_dependencies(effect)
        self.assertIn(OWN_GRAVEYARD_TYPE_RETURN_CAPABILITY, required)
        self.assertTrue(set(required) <= set(typed_result_amount_runtime_dependencies(effect)))
        self.assertEqual(required, fixed_public_zone_move_set_node_capabilities(
            effects=(effect,), target_schema=None,
            mechanic_ids=("return-to-owner-hand", "fixed-public-zone-move", "fixed-public-zone-move-set"),
        ))
        for broken in (
            dict(effect, **{OWN_GRAVEYARD_TYPE_CHOICE_FIELD: True}),
            dict(effect, player="B"), dict(effect, options=effect["options"][:-1]),
            dict(effect, then_by_choice={**effect["then_by_choice"], "battle": effect["then_by_choice"]["land"]}),
        ):
            with self.subTest(broken=broken), self.assertRaises(ValueError):
                typed_result_amount_runtime_dependencies(broken)

    def test_original_compiles_with_ordinary_type_return_and_printed_flashback(self):
        record = self.db.lookup(ORIGINAL, fuzzy=False)
        ir = compile_oracle_card(record, capability_registry=self.registry, capability_profile=PROFILE)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        nodes = tuple(node for face in ir.faces for node in face.nodes)
        step, = [node for node in nodes if OWN_GRAVEYARD_TYPE_RETURN_CAPABILITY in node.capability_dependencies]
        self.assertIsNone(step.target_schema)
        self.assertTrue(any("casting.flashback.fixed_mana" in node.capability_dependencies for node in nodes))
        report, _ = register_card(self.db, self.registry, ORIGINAL)
        self.assertTrue(report["strict_capability_ready"], report["blockers"])
        self.assertEqual("capability_closed", report["trust_basis"])

    def test_actual_paid_hand_and_flashback_casts_choose_creature_and_land_with_exact_replay(self):
        game = self.session(6499220)
        spell = self.add(game.engine, ORIGINAL, zone="hand")
        bear = self.add(game.engine, "Grizzly Bears", zone="graveyard")
        myr = self.add(game.engine, "Darksteel Myr", zone="graveyard")
        land = self.add(game.engine, "Forest", zone="graveyard")
        foreign = self.add(game.engine, "Grizzly Bears", seat="B", zone="graveyard", ref="type-return-foreign")
        private = self.add(game.engine, "Generic Bound Growth", seat="D", zone="hand", ref="type-return-private")
        self.assert_closed(game, spell)
        self.prepare(game, mana={"C": 8, "G": 4})
        self.checkpoint(game)

        self._cast_and_wait(game, spell, 7)
        game = self.replay(game, load=True)
        self._choose(game, "creature")
        for card in (bear, myr):
            self.assertEqual("hand", game.state.cards[card.object_id].zone)
        for card in (land, foreign, spell):
            self.assertEqual("graveyard", game.state.cards[card.object_id].zone)

        self._cast_and_wait(game, spell, 0)
        game = self.replay(game, load=True)
        self._choose(game, "land")
        self.assertEqual("hand", game.state.cards[land.object_id].zone)
        self.assertEqual("exile", game.state.cards[spell.object_id].zone)
        self.assertEqual("graveyard", game.state.cards[foreign.object_id].zone)
        for principal in ("pilot:A", "pilot:B", "pilot:C", "spectator"):
            self.assertNotIn(private.ref, json.dumps(game.packet(principal, full=True)))
        self.replay(game, load=True)

    def test_actual_empty_battle_subset_keeps_the_type_available_and_preserves_foreign_cards(self):
        game = self.session(6499230)
        spell = self.add(game.engine, ORIGINAL, zone="graveyard")
        foreign = self.add(game.engine, "Sol Ring", seat="B", zone="graveyard")
        own = self.add(game.engine, "Forest", zone="graveyard")
        self.prepare(game, mana={"C": 5, "G": 2})
        self.checkpoint(game)

        self._cast_and_wait(game, spell, 0)
        self._choose(game, "battle")
        self.assertEqual("exile", game.state.cards[spell.object_id].zone)
        for card in (foreign, own):
            self.assertEqual("graveyard", game.state.cards[card.object_id].zone)
        self.replay(game, load=True)

    def test_omission_restores_only_the_material_type_choice_and_retains_flashback(self):
        with patch.object(public_zone_move_templates, "_own_graveyard_type_return_template", return_value=None):
            record = self.db.lookup(ORIGINAL, fuzzy=False)
            ir = compile_oracle_card(record, capability_registry=self.registry, capability_profile=PROFILE)
            residual, = ir.material_residuals
            self.assertEqual("spell_effect", residual.kind)
            self.assertTrue(any("casting.flashback.fixed_mana" in node.capability_dependencies
                                for face in ir.faces for node in face.nodes))
            report, _ = register_card(self.db, self.registry, ORIGINAL)
            self.assertFalse(report["strict_capability_ready"], report)

    def test_genuine_type_choice_authority_withdrawal_retains_crystal_chimes(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests

        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        known = set(discover_tests(ROOT).values())
        document = deepcopy(raw)
        next(row for row in document["capabilities"] if row["id"] == OWN_GRAVEYARD_TYPE_RETURN_CAPABILITY).update(
            status="blocked", blockers=["Scoped own-graveyard permanent-type authority withdrawal"])
        registry = CapabilityRegistry(document)
        index = build_index(registry_value=document, declaration_source=declarations, discovered_test_ids=known)
        registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
        closure = registry.closure((OWN_GRAVEYARD_TYPE_RETURN_CAPABILITY,), profile=PROFILE)
        self.assertIn("status:" + OWN_GRAVEYARD_TYPE_RETURN_CAPABILITY + ":blocked", closure.blockers)
        self.assertFalse(any("unverified" in blocker for blocker in closure.blockers))
        report, _ = register_card(self.db, registry, ORIGINAL)
        self.assertFalse(report["strict_capability_ready"], report)
        prior, _ = register_card(self.db, registry, "Crystal Chimes")
        self.assertTrue(prior["strict_capability_ready"], prior["blockers"])


if __name__ == "__main__":
    unittest.main()
