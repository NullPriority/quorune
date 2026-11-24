"""Dusk and Dawn through their normal split-face and public graveyard owners."""

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
from quorune.object_query import ObjectQueryResult
from quorune.oracle_ir import compile_oracle_card
from quorune.public_zone_moves import (
    PUBLIC_GRAVEYARD_POWER_RETURN_CAPABILITY, PUBLIC_GRAVEYARD_POWER_RETURN_VERSION,
    PublicZoneMoveSetSpec, public_graveyard_power_return_spec, select_public_zone_move_objects,
)
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.public_zone_move_capability_shapes import fixed_public_zone_move_set_node_capabilities
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


ORIGINAL = "Dusk // Dawn"
PROFILE = "commander_review"
FIXTURES = ROOT / "tests" / "fixtures"


class OwnGraveyardPowerReturnTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        folder = Path(cls.temporary.name)
        support = {
            "activation-public-state-cards.json": {"Grizzly Bears"},
            "attached-destruction-set-cards.json": {"Sol Ring"},
            "damage-death-exile-cards.json": {"Wall of Omens"},
            "aftermath-cards.json": {"Claim // Fame", "Counterspell"},
            "bound-effect-program-cards.json": {"Generic Bound Plains", "Generic Bound Growth"},
            "source-attack-alone-cards.json": {"Isamaru, Hound of Konda"},
        }
        parts = [FIXTURES / "own-graveyard-power-return-cards.json"]
        for name, names in support.items():
            document = json.loads((FIXTURES / name).read_bytes())
            document["cards"] = [card for card in document["cards"] if card["name"] in names]
            assert names == {card["name"] for card in document["cards"]}, name
            ids = {card["oracle_id"] for card in document["cards"]}
            document["rulings"] = [ruling for ruling in document.get("rulings", ()) if ruling["oracle_id"] in ids]
            part = folder / name
            part.write_text(json.dumps(document), encoding="utf-8")
            parts.append(part)
        path = folder / "own-graveyard-power-return.sqlite3"
        build_fixture_database(parts, path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Own graveyard power return", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"),
            DeckEntry("Generic Bound Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def _face_offer(self, game, card, face):
        actions = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        action, = [action for action in actions if action.get("card") == card.ref]
        self.assertEqual(face, action["face"])
        return action

    def _cast_face(self, game, card, face):
        self.priority(game, "A")
        action = self._face_offer(game, card, face)
        command = {"action_id": action["id"], "pay": "auto"}
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:B", command).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        result = game.act("pilot:A", command)
        self.assertTrue(result.ok, result.summary)
        item = game.state.stack[-1]
        self.assertEqual(face, item.label)
        self.assertEqual([], item.targets)
        return item

    def test_wire_owns_the_graveyard_ceiling_and_denies_generic_domain_changes(self):
        spec = public_graveyard_power_return_spec(2)
        payload = spec.to_dict()
        self.assertEqual(PUBLIC_GRAVEYARD_POWER_RETURN_VERSION, payload["schema_version"])
        self.assertEqual({"characteristic": "power", "comparison": "at_most", "value": 2},
                         payload["graveyard_power"])
        self.assertEqual(spec, PublicZoneMoveSetSpec.from_dict(payload))
        compiled = public_zone_move_templates.public_zone_move_effect_template(
            "Return all creature cards with power 2 or less from your graveyard to your hand."
        )
        _, effects, target, mechanics = compiled.compiled()
        self.assertIsNone(target)
        required = fixed_public_zone_move_set_node_capabilities(
            effects=effects, target_schema=target, mechanic_ids=mechanics,
        )
        self.assertIn(PUBLIC_GRAVEYARD_POWER_RETURN_CAPABILITY, required)
        self.assertEqual(required, typed_result_amount_runtime_dependencies(effects[0]))
        for altered in (
            dict(payload, schema_version=2),
            dict(payload, seat_relation="any"),
            dict(payload, destination="exile"),
            dict(payload, graveyard_power={**payload["graveyard_power"], "value": True}),
            dict(payload, graveyard_power={**payload["graveyard_power"], "characteristic": "toughness"}),
        ):
            with self.subTest(altered=altered), self.assertRaises(ValueError):
                PublicZoneMoveSetSpec.from_dict(altered)
        missing = dict(payload)
        missing.pop("graveyard_power")
        with self.assertRaises(ValueError):
            PublicZoneMoveSetSpec.from_dict(missing)

    def test_signed_effective_predicate_uses_only_actual_owned_creature_cards(self):
        def row(ref, owner, power, **flags):
            return ObjectQueryResult(
                ref, ref, ref, owner, owner, "graveyard", effective_power=power,
                logical_object_id=ref + "-incarnation", **flags,
            )

        rows = (
            row("negative", "A", -2, types=("creature",)),
            row("equal", "A", 2, types=("creature",)),
            row("high", "A", 3, types=("creature",)),
            row("foreign", "B", 1, types=("creature",)),
            row("token", "A", 1, types=("creature",), token=True),
            row("noncreature", "A", None, types=("artifact",)),
        )
        kwargs = {"actor": "A", "active_seats": ("A", "B"), "apnap_order": ("A", "B")}
        selected = select_public_zone_move_objects(rows, public_graveyard_power_return_spec(2), **kwargs)
        self.assertEqual({"negative", "equal"}, {r.ref for r in selected})
        with self.assertRaises(ValueError):
            select_public_zone_move_objects(
                (*rows, row("unresolved", "A", None, types=("creature",))),
                public_graveyard_power_return_spec(2), **kwargs,
            )

    def test_complete_split_program_keeps_dusk_and_aftermath_alongside_dawn(self):
        card = self.db.lookup(ORIGINAL, fuzzy=False)
        ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile=PROFILE)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        dusk, dawn = ir.faces
        self.assertEqual(("Dusk", "Dawn"), (dusk.face_name, dawn.face_name))
        self.assertTrue(any("permanent.destroy.fixed_set" in node.capability_dependencies for node in dusk.nodes))
        self.assertTrue(any("casting.aftermath.selected_half" in node.capability_dependencies for node in dawn.nodes))
        self.assertTrue(any(PUBLIC_GRAVEYARD_POWER_RETURN_CAPABILITY in node.capability_dependencies for node in dawn.nodes))
        bound, _ = register_card(self.db, self.registry, ORIGINAL)
        self.assertTrue(bound["strict_capability_ready"], bound["blockers"])
        self.assertEqual("capability_closed", bound["trust_basis"])

    def test_actual_dusk_then_paid_dawn_returns_both_owned_cards_and_exiles_the_split_source(self):
        game = self.session(6498210)
        spell = self.add(game.engine, ORIGINAL, zone="hand")
        high = self.add(game.engine, "Kalonian Tusker", seat="B")
        standing = self.add(game.engine, "Grizzly Bears", seat="C")
        bear = self.add(game.engine, "Grizzly Bears", zone="graveyard", ref="dawn-owned-bear")
        wall = self.add(game.engine, "Wall of Omens", zone="graveyard")
        above = self.add(game.engine, "Kalonian Tusker", zone="graveyard", ref="above-dawn-power")
        artifact = self.add(game.engine, "Sol Ring", zone="graveyard")
        foreign = self.add(game.engine, "Grizzly Bears", seat="B", zone="graveyard", ref="dawn-foreign-bear")
        private = self.add(game.engine, "Generic Bound Growth", seat="D", zone="hand", ref="dawn-private")
        self.assert_closed(game, spell)
        self.prepare(game, mana={"C": 5, "W": 4})
        self.checkpoint(game)

        item = self._cast_face(game, spell, "Dusk")
        self.assertNotIn("fixed_cast_lifecycle", item.context)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("graveyard", game.state.cards[high.object_id].zone)
        self.assertEqual("battlefield", game.state.cards[standing.object_id].zone)
        self.assertEqual("graveyard", game.state.cards[spell.object_id].zone)
        game = self.replay(game, load=True)

        item = self._cast_face(game, spell, "Dawn")
        self.assertEqual("aftermath", item.context["fixed_cast_lifecycle"]["kind"])
        self.assertEqual(0, sum(game.state.players["A"].mana_pool.values()))
        self.assertIsNone(self.resolve(game))
        for card in (bear, wall):
            self.assertEqual("hand", game.state.cards[card.object_id].zone)
        for card in (above, artifact, foreign, high):
            self.assertEqual("graveyard", game.state.cards[card.object_id].zone)
        self.assertEqual("exile", game.state.cards[spell.object_id].zone)
        for principal in ("pilot:A", "pilot:B", "pilot:C", "spectator"):
            self.assertNotIn(private.ref, json.dumps(game.packet(principal, full=True)))
        self.replay(game, load=True)

    def test_actual_dawn_freezes_graveyard_cda_power_before_returning_any_members(self):
        game = self.session(6498220)
        spell = self.add(game.engine, ORIGINAL, zone="graveyard")
        wurm = self.add(game.engine, "Boneyard Wurm", zone="graveyard")
        lord = self.add(game.engine, "Lord of Extinction", zone="graveyard")
        ring = self.add(game.engine, "Sol Ring", seat="B", zone="graveyard")
        for card in (spell, wurm, lord):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 3, "W": 2})
        self.checkpoint(game)

        self._cast_face(game, spell, "Dawn")
        self.assertEqual(2, game.engine._numeric_stat(wurm.object_id, "power"))
        self.assertEqual(3, game.engine._numeric_stat(lord.object_id, "power"))
        self.assertIsNone(self.resolve(game))
        self.assertEqual("hand", game.state.cards[wurm.object_id].zone)
        self.assertEqual("graveyard", game.state.cards[lord.object_id].zone)
        self.assertEqual(2, game.engine._numeric_stat(lord.object_id, "power"))
        self.assertEqual("graveyard", game.state.cards[ring.object_id].zone)
        self.assertEqual("exile", game.state.cards[spell.object_id].zone)
        self.replay(game, load=True)

    def test_countered_dawn_exiles_without_moving_the_selected_graveyard(self):
        game = self.session(6498230)
        spell = self.add(game.engine, ORIGINAL, zone="graveyard")
        bear = self.add(game.engine, "Grizzly Bears", zone="graveyard")
        counter = self.add(game.engine, "Counterspell", seat="B", zone="hand")
        self.prepare(game, mana={"C": 3, "W": 2})
        game.state.players["B"].mana_pool["U"] = 2
        self.checkpoint(game)

        item = self._cast_face(game, spell, "Dawn")
        self.cast(game, "B", counter, [item.ref])
        self.assertIsNone(self.resolve(game))
        self.assertEqual("graveyard", game.state.cards[bear.object_id].zone)
        self.assertEqual("exile", game.state.cards[spell.object_id].zone)
        self.replay(game, load=True)

    def test_omitting_the_power_return_restores_only_dawns_material_residual(self):
        with patch.object(public_zone_move_templates, "_graveyard_power_return_template", return_value=None):
            card = self.db.lookup(ORIGINAL, fuzzy=False)
            ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile=PROFILE)
            residual, = ir.material_residuals
            self.assertEqual("spell_effect", residual.kind)
            self.assertIn("power 2 or less", residual.text)
            dusk, dawn = ir.faces
            self.assertTrue(any("permanent.destroy.fixed_set" in node.capability_dependencies for node in dusk.nodes))
            self.assertTrue(any("casting.aftermath.selected_half" in node.capability_dependencies for node in dawn.nodes))
            report, _ = register_card(self.db, self.registry, ORIGINAL)
            self.assertFalse(report["strict_capability_ready"], report)

    def test_genuine_graveyard_power_withdrawal_preserves_an_independent_aftermath_card(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests

        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        known = set(discover_tests(ROOT).values())
        document = deepcopy(raw)
        next(row for row in document["capabilities"] if row["id"] == PUBLIC_GRAVEYARD_POWER_RETURN_CAPABILITY).update(
            status="blocked", blockers=["Scoped graveyard power return authority withdrawal"])
        registry = CapabilityRegistry(document)
        index = build_index(registry_value=document, declaration_source=declarations, discovered_test_ids=known)
        registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
        closure = registry.closure((PUBLIC_GRAVEYARD_POWER_RETURN_CAPABILITY,), profile=PROFILE)
        self.assertIn("status:" + PUBLIC_GRAVEYARD_POWER_RETURN_CAPABILITY + ":blocked", closure.blockers)
        self.assertFalse(any("unverified" in blocker for blocker in closure.blockers))
        report, _ = register_card(self.db, registry, ORIGINAL)
        self.assertFalse(report["strict_capability_ready"], report)
        control, _ = register_card(self.db, registry, "Claim // Fame")
        self.assertTrue(control["strict_capability_ready"], control["blockers"])


if __name__ == "__main__":
    unittest.main()
