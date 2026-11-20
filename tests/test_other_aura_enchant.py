from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.aura.grammar import parse_enchant_line
from quorune.carddb import CardDatabase
from quorune.characteristic_evaluation import type_parts
from quorune.enchant_public_state_model import (
    ENCHANT_PUBLIC_STATE_CAPABILITY, EnchantPublicStateKind, EnchantPublicStateSpec, OTHER_AURA_ATTACHED_PREDICATE,
)
from quorune.enchant_spec import TypedEnchantSpec
from quorune.deck import DeckDefinition, DeckEntry
from quorune.model import CardInstance
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.target_predicates import _other_aura_attached
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


CORONET = "Daybreak Coronet"
PROFILE = "commander_review"
FIXTURES = ROOT / "tests" / "fixtures"


class OtherAuraEnchantTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        folder = Path(cls.temporary.name)
        selections = {
            "bound-effect-program-cards.json": {"Generic Bound Plains"},
            "source-attack-alone-cards.json": {"Isamaru, Hound of Konda"},
            "enchant-public-state-cards.json": {CORONET, "Disenchant", "Plains", "Replenish"},
            "public-graveyard-entry-cards.json": {"Grizzly Bears", "Rancor", "Vedalken Orrery"},
            "attached-destruction-set-cards.json": {"Forest", "Unsummon"},
        }
        fixtures = []
        for name, names in selections.items():
            data = json.loads((FIXTURES / name).read_bytes())
            data["cards"] = [card for card in data["cards"] if card["name"] in names]
            assert {card["name"] for card in data["cards"]} == names
            ids = {card["oracle_id"] for card in data["cards"]}
            data["rulings"] = [row for row in data.get("rulings", ()) if row["oracle_id"] in ids]
            destination = folder / name
            destination.write_text(json.dumps(data), encoding="utf-8")
            fixtures.append(destination)
        path = folder / "other-aura-enchant.sqlite3"
        build_fixture_database(fixtures, path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Creature with another Aura", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def test_complete_original_keeps_the_exact_other_aura_domain_and_characteristic_grant(self):
        card = self.db.lookup(CORONET, fuzzy=False)
        restricted, _ = card.oracle_text.splitlines()
        spec = parse_enchant_line(restricted)
        self.assertIsInstance(spec, TypedEnchantSpec)
        self.assertEqual(4, spec.schema_version)
        self.assertEqual(EnchantPublicStateKind.CREATURE_WITH_OTHER_AURA, spec.public_state.kind)
        self.assertEqual(spec, TypedEnchantSpec.from_dict(spec.to_dict()))
        self.assertEqual(
            {"zones": ["battlefield"], "categories": ["permanent"], "controller": "any", "count": 1,
                "source_exclusion": True, "types_all": ["creature"], "predicate": OTHER_AURA_ATTACHED_PREDICATE},
            spec.target_schema(),
        )
        ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile=PROFILE)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        enchant, continuous = ir.faces[0].nodes
        self.assertTrue({ENCHANT_PUBLIC_STATE_CAPABILITY, "attachment.aura.typed_restriction",
            "target.revalidate_resolution"} <= set(enchant.capability_dependencies))
        self.assertIn("continuous.attached.fixed_characteristics", continuous.capability_dependencies)
        admitted, _ = register_card(self.db, self.registry, CORONET)
        self.assertTrue(admitted["strict_capability_ready"], admitted["blockers"])
        for other in (
            "Enchant creature with two other Auras attached to it",
            "Enchant creature with an Equipment attached to it",
            "Enchant creature with another Aura you control attached to it",
            "Enchant permanent with another Aura attached to it",
        ):
            with self.subTest(other=other):
                changed = replace(card, oracle_text=card.oracle_text.replace(restricted, other))
                result = compile_oracle_card(changed, capability_registry=self.registry, capability_profile=PROFILE)
                self.assertNotEqual("exact", result.status)
        for changed in ({"kind": "creature_with_two_auras"}, {"schema_version": True}, {"number": 1}):
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                EnchantPublicStateSpec.from_dict({**spec.public_state.to_dict(), **changed})

    def prepare_live_cast(self, seed):
        game = self.session(seed)
        coronet = self.add(game.engine, CORONET, zone="hand")
        support = self.add(game.engine, "Rancor", seat="B", zone="hand")
        removal = self.add(game.engine, "Disenchant", seat="B", zone="hand")
        target = self.add(game.engine, "Grizzly Bears", seat="B")
        unenchanted = self.add(game.engine, "Grizzly Bears", ref="coronet-unenchanted")
        private = self.add(game.engine, "Unsummon", seat="D", zone="hand", ref="coronet-private-hand")
        timing = self.add(game.engine, "Vedalken Orrery")
        self.add(game.engine, "Forest", seat="B")
        for seat in "AB":
            for n in range(2):
                self.add(game.engine, "Plains", seat=seat, ref=f"coronet-plains-{seat}-{n}")
        for card in (coronet, support, removal, timing):
            self.assert_closed(game, card)
        self.prepare(game, active="B", actor="A")
        self.checkpoint(game)
        actions = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        self.assertFalse(any(row["id"] == "cast:" + coronet.ref for row in actions))
        before = authoritative_state_hash(game.state)
        reject = game.act("pilot:A", {"action_id": "cast:" + coronet.ref, "targets": [target.ref], "pay": "auto"})
        self.assertFalse(reject.ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        self.cast(game, "B", support, [target.ref])
        self.assertIsNone(self.resolve(game))
        self.assertEqual(("B", target.object_id), (game.state.cards[support.object_id].controller,
            game.state.cards[support.object_id].attached_to))

        self.priority(game, "A")
        offered = self.action(game, "A", coronet)
        self.assertIn(target.ref, offered["target_schema"]["legal_refs"])
        self.assertNotIn(unenchanted.ref, offered["target_schema"]["legal_refs"])
        self.cast(game, "A", coronet, [target.ref])
        return game, coronet, support, removal, target, private

    def test_actual_foreign_aura_enables_cast_and_sba_removes_coronet_after_it_leaves(self):
        game, coronet, support, removal, target, private = self.prepare_live_cast(70682111)
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        current = game.state.cards[coronet.object_id]
        self.assertEqual(("A", target.object_id, "battlefield"), (current.controller, current.attached_to, current.zone))
        self.assertEqual((7, 5), (game.engine._numeric_stat(target.object_id, "power"),
            game.engine._numeric_stat(target.object_id, "toughness")))
        self.assertTrue({"first strike", "vigilance", "lifelink", "trample"} <=
            game.engine._combat_keywords(game.state.cards[target.object_id]))
        self.cast(game, "B", removal, [game.state.cards[support.object_id].ref])
        self.resolve_top(game)
        self.assertEqual("graveyard", game.state.cards[coronet.object_id].zone,
            "Only another Aura can sustain the original attachment")
        self.assertEqual("A", game.state.cards[coronet.object_id].owner)
        self.assertEqual((2, 2), (game.engine._numeric_stat(target.object_id, "power"),
            game.engine._numeric_stat(target.object_id, "toughness")))
        self.resolve(game)
        for seat in "ABC":
            packet = json.dumps(game.packet("pilot:" + seat, full=True))
            self.assertNotIn(private.ref, packet)
            self.assertNotIn(private.printed_name, packet)
        self.replay(game, load=True)

    def test_actual_spell_revalidates_after_the_last_other_aura_leaves_in_response(self):
        game, coronet, support, removal, target, _ = self.prepare_live_cast(70682112)
        self.cast(game, "B", removal, [game.state.cards[support.object_id].ref])
        self.resolve_top(game)
        self.assertEqual("stack", game.state.cards[coronet.object_id].zone)
        self.assertFalse(game.state.cards[target.object_id].attachments)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("graveyard", game.state.cards[coronet.object_id].zone)
        self.assertFalse(game.state.cards[target.object_id].attachments)
        self.replay(game, load=True)

    def test_actual_replenish_cannot_use_an_aura_entering_in_the_same_batch(self):
        game = self.session(70682113)
        coronet = self.add(game.engine, CORONET, zone="graveyard")
        support = self.add(game.engine, "Rancor", zone="graveyard")
        replenish = self.add(game.engine, "Replenish", zone="hand")
        targets = [self.add(game.engine, "Grizzly Bears", ref=f"simultaneous-coronet-{n}") for n in range(2)]
        for card in (coronet, support, replenish):
            self.assert_closed(game, card)
        self.prepare(game, mana={"W": 1, "C": 3})
        self.checkpoint(game)
        self.cast(game, "A", replenish)
        pending = self.resolve(game)
        self.assertIsNotNone(pending)
        self.assertEqual(("aura.entry", ["A"]), (pending.kind, pending.actors))
        self.assertEqual("graveyard", game.state.cards[support.object_id].zone)
        self.assertEqual("graveyard", game.state.cards[coronet.object_id].zone)
        context = game.packet("pilot:A", full=True)["decision"]["ctx"]
        self.assertEqual(support.ref, context["aura"])
        self.assertTrue({card.ref for card in targets} <= set(context["target_schema"]["legal_refs"]))
        game = self.replay(game, load=True)
        accepted = game.act("pilot:A", {"action_id": "choose", "aura_target": targets[0].ref})
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("graveyard", game.state.cards[coronet.object_id].zone)
        entered = game.state.cards[support.object_id]
        self.assertEqual(("battlefield", targets[0].object_id), (entered.zone, entered.attached_to))
        self.replay(game, load=True)

    def test_live_attachment_identity_ignores_other_types_phasing_and_stale_links(self):
        target = CardInstance("target", "target-ref", "fixture-target", "Creature", "A", "B", "battlefield")
        own = CardInstance("own", "own-ref", "fixture-aura", CORONET, "A", "A", "battlefield", attached_to="target")
        another = CardInstance("other", "other-ref", "fixture-aura", CORONET, "B", "C", "battlefield", attached_to="target")
        equipment = CardInstance("equipment", "equipment-ref", "fixture-equip", "Equipment", "C", "C",
            "battlefield", attached_to="target")
        data = {"own": "Enchantment - Aura", "other": "Enchantment - Aura", "equipment": "Artifact - Equipment"}
        host = SimpleNamespace(
            state=SimpleNamespace(cards={card.object_id: card for card in (target, own, another, equipment)}),
            _type_parts=type_parts,
            _effective_card_data=lambda card, **kwargs: {"type_line": data[card.object_id]},
        )
        target.attachments = [own.object_id, equipment.object_id]
        target.counters["stun"] = 1
        self.assertFalse(_other_aura_attached(host, target, own.ref))
        target.attachments.append(another.object_id)
        self.assertTrue(_other_aura_attached(host, target, own.ref))
        another.phased_out = True
        self.assertFalse(_other_aura_attached(host, target, own.ref))
        another.phased_out = False
        another.attached_to = None
        self.assertFalse(_other_aura_attached(host, target, own.ref))

    def test_counting_the_source_aura_breaks_the_actual_state_based_witness(self):
        def includes_source(host, target, source_ref):
            return _other_aura_attached(host, target, "another-reference")

        with patch("quorune.target_predicates._other_aura_attached", includes_source):
            with self.assertRaisesRegex(AssertionError, "Only another Aura can sustain the original attachment"):
                self.test_actual_foreign_aura_enables_cast_and_sba_removes_coronet_after_it_leaves()

    def test_verified_capability_withdrawal_blocks_the_complete_original(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests

        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        known = set(discover_tests(ROOT).values())

        def registry_for(rows):
            registry = CapabilityRegistry(rows)
            index = build_index(registry_value=rows, declaration_source=declarations, discovered_test_ids=known)
            registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
            return registry

        baseline = registry_for(deepcopy(raw))
        result, _ = register_card(self.db, baseline, CORONET)
        self.assertTrue(result["strict_capability_ready"], result["blockers"])
        for capability in (ENCHANT_PUBLIC_STATE_CAPABILITY, "attachment.aura.typed_restriction",
                "target.revalidate_resolution"):
            with self.subTest(capability=capability):
                rows = deepcopy(raw)
                next(row for row in rows["capabilities"] if row["id"] == capability).update(
                    status="blocked", blockers=["Other-Aura Enchant requirement withdrawn"])
                registry = registry_for(rows)
                closure = registry.closure((capability,), profile=PROFILE)
                self.assertIn("status:" + capability + ":blocked", closure.blockers)
                self.assertFalse(any("unverified" in blocker for blocker in closure.blockers))
                result, _ = register_card(self.db, registry, CORONET)
                self.assertFalse(result["strict_capability_ready"], result)
                control, _ = register_card(self.db, registry, "Grizzly Bears")
                self.assertTrue(control["strict_capability_ready"], control["blockers"])
