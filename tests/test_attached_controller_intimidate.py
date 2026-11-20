from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.card_programs.runtime import _component_public_state_snapshot
from quorune.carddb import CardDatabase
from quorune.compiler.continuous_templates import fixed_public_state_characteristics_handler
from quorune.conditional_recipient_model import (
    ATTACHED_CREATURE_CONDITION_SUBJECT, attached_creature_no_other_creatures_condition,
)
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from quorune.semantic_runtime.conditional_continuous import FixedPublicStateCharacteristicsHandler
from quorune.semantic_runtime.context import SemanticNodeError
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


GAMBIT = "Predator's Gambit"
CAPABILITY = "continuous.characteristics.fixed_public_state"
INTIMIDATE = "combat.block.intimidate"
PROFILE = "commander_review"
FIXTURES = ROOT / "tests" / "fixtures"


class AttachedControllerIntimidateTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        folder = Path(cls.temporary.name)
        selections = {
            "bound-effect-program-cards.json": {"Generic Bound Plains"},
            "source-attack-alone-cards.json": {"Isamaru, Hound of Konda"},
            "attached-destruction-set-cards.json": {"Grizzly Bears", "Swamp", "Murder"},
        }
        fixtures = [FIXTURES / "attached-controller-intimidate-cards.json"]
        for name, names in selections.items():
            data = json.loads((FIXTURES / name).read_bytes())
            data["cards"] = [row for row in data["cards"] if row["name"] in names]
            assert {row["name"] for row in data["cards"]} == names
            ids = {row["oracle_id"] for row in data["cards"]}
            data["rulings"] = [row for row in data.get("rulings", ()) if row["oracle_id"] in ids]
            chosen = folder / name
            chosen.write_text(json.dumps(data), encoding="utf-8")
            fixtures.append(chosen)
        path = folder / "attached-controller-intimidate.sqlite3"
        build_fixture_database(fixtures, path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Attached controller Intimidate", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def test_complete_original_binds_one_exact_attached_condition_and_keeps_fixed_stats(self):
        card = self.db.lookup(GAMBIT, fuzzy=False)
        ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile=PROFILE)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        nodes = ir.faces[0].nodes
        self.assertEqual(3, len(nodes))
        stats, = [n for n in nodes if n.template_id == "continuous-attached-fixed-characteristics-v1"]
        fixed, = stats.handlers
        self.assertEqual((2, 1), (fixed["modifier"]["power"], fixed["modifier"]["toughness"]))
        guarded, = [n for n in nodes if n.template_id == "continuous-fixed-public-state-characteristics-v1"]
        self.assertTrue({CAPABILITY, INTIMIDATE} <= set(guarded.capability_dependencies))
        descriptor, = guarded.handlers
        self.assertEqual(ATTACHED_CREATURE_CONDITION_SUBJECT, descriptor["condition_subject"])
        self.assertEqual(attached_creature_no_other_creatures_condition().to_dict(), descriptor["source_condition"])
        ready, _ = register_card(self.db, self.registry, GAMBIT)
        self.assertTrue(ready["strict_capability_ready"], ready["blockers"])

        for original, changed in (
            ("its controller", "its owner"), ("no other creatures", "only one other creature"),
            ("other creatures", "other permanents"), ("has intimidate", "has flying"),
        ):
            with self.subTest(changed=changed):
                replacement = replace(card, oracle_text=card.oracle_text.replace(original, changed))
                ir = compile_oracle_card(replacement, capability_registry=self.registry, capability_profile=PROFILE)
                self.assertNotEqual("exact", ir.status)

        validator = FixedPublicStateCharacteristicsHandler()
        validated = validator.validate(descriptor)
        self.assertEqual(("attached", ("creature",), ("Intimidate",)),
            (validated.target_kind, validated.subject_types_all, validated.abilities))
        for changes in (
            {"condition_subject": "source"},
            {"source_condition": {**descriptor["source_condition"], "amount": 1}},
            {"target": {**descriptor["target"], "types_all": ["land"]}},
            {"modifier": {**descriptor["modifier"], "add_abilities": ["Flying"]}},
            {"modifier": {**descriptor["modifier"], "power": 1}},
        ):
            with self.subTest(changes=changes), self.assertRaises(SemanticNodeError):
                validator.validate({**descriptor, **changes})
        old = fixed_public_state_characteristics_handler(
            "This creature gets +1/+1 as long as you control a Swamp.", source_name="Witness",
        )
        self.assertNotIn("condition_subject", old[1])

    def assert_has_intimidate(self, game, card):
        self.assertIn("intimidate", game.engine._combat_keywords(game.state.cards[card.object_id]),
            "Intimidate uses the attached creature controller")

    def test_actual_foreign_attachment_uses_recipient_count_and_changes_legal_blocks(self):
        game = self.session(80951121)
        aura = self.add(game.engine, GAMBIT, zone="hand")
        timing = self.add(game.engine, "Vedalken Orrery")
        attacker = self.add(game.engine, "Grizzly Bears", seat="B", ref="gambit-foreign-attacker")
        attacker.acquired_control_turn_count = -1
        same_color = self.add(game.engine, "Grizzly Bears", ref="gambit-shared-color")
        other_color = self.add(game.engine, "Merfolk of the Pearl Trident")
        artifact = self.add(game.engine, "Ornithopter")
        raise_alarm = self.add(game.engine, "Raise the Alarm", seat="B", zone="hand")
        private = self.add(game.engine, "Murder", seat="D", zone="hand", ref="gambit-private")
        self.add(game.engine, "Swamp")
        for n in range(2):
            self.add(game.engine, "Generic Bound Plains", seat="B", ref=f"gambit-white-{n}")
        for card in (aura, timing, attacker, other_color, artifact, raise_alarm):
            self.assert_closed(game, card)
        self.prepare(game, active="B", actor="B")
        self.checkpoint(game)
        self.until(game, lambda s: s.state.pending_decision.kind == "combat.attackers")
        declared = game.act("pilot:B", {"action_id": "attack", "attackers": {attacker.ref: "A"}})
        self.assertTrue(declared.ok, declared.summary)
        self.cast(game, "A", aura, [attacker.ref])
        self.assertIsNone(self.resolve(game))
        current = game.state.cards[aura.object_id]
        self.assertEqual(("A", attacker.object_id), (current.controller, current.attached_to))
        self.assert_has_intimidate(game, attacker)
        self.assertEqual((4, 3), tuple(game.engine._numeric_stat(attacker.object_id, field)
            for field in ("power", "toughness")))
        self.assertFalse(game.engine._can_block(attacker, other_color)[0])
        self.assertTrue(game.engine._can_block(attacker, same_color)[0])
        self.assertTrue(game.engine._can_block(attacker, artifact)[0])

        self.cast(game, "B", raise_alarm)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(2, sum(card.zone == "battlefield" and card.is_token and card.controller == "B"
            for card in game.state.cards.values()))
        self.assertNotIn("intimidate", game.engine._combat_keywords(game.state.cards[attacker.object_id]))
        self.assertEqual((4, 3), tuple(game.engine._numeric_stat(attacker.object_id, field)
            for field in ("power", "toughness")))
        self.until(game, lambda s: s.state.pending_decision.kind == "combat.blockers")
        block = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal_blocks"]
        self.assertIn(attacker.ref, block[other_color.ref])
        accepted = game.act("pilot:A", {"action_id": "block", "blocks": {other_color.ref: attacker.ref}})
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertIn(other_color.object_id, game.state.combat.blockers[attacker.object_id])
        for seat in "ABC":
            packet = json.dumps(game.packet("pilot:" + seat, full=True))
            self.assertNotIn(private.ref, packet)
            self.assertNotIn(private.printed_name, packet)
        self.replay(game, load=True)

    def test_using_the_aura_controller_breaks_the_actual_condition(self):
        def aura_controller(source, descriptor, **kwargs):
            return _component_public_state_snapshot(source,
                {key: value for key, value in descriptor.items() if key != "condition_subject"}, **kwargs)

        game = self.session(80951122)
        aura = self.add(game.engine, GAMBIT, zone="hand")
        self.add(game.engine, "Grizzly Bears", ref="aura-controller-other-creature")
        target = self.add(game.engine, "Grizzly Bears", seat="B", ref="gambit-sole-creature")
        self.add(game.engine, "Swamp")
        self.assert_closed(game, aura)
        self.prepare(game)
        self.checkpoint(game)
        with patch("quorune.card_programs.runtime._component_public_state_snapshot", aura_controller):
            self.cast(game, "A", aura, [target.ref])
            self.assertIsNone(self.resolve(game))
            with self.assertRaisesRegex(AssertionError, "Intimidate uses the attached creature controller"):
                self.assert_has_intimidate(game, target)

    def test_verified_withdrawal_preserves_independent_aura_and_stats(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests

        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        known = set(discover_tests(ROOT).values())
        for cap in (CAPABILITY, INTIMIDATE):
            with self.subTest(cap=cap):
                rows = deepcopy(raw)
                next(row for row in rows["capabilities"] if row["id"] == cap).update(
                    status="blocked", blockers=["Attached-controller condition withdrawn"])
                registry = CapabilityRegistry(rows)
                index = build_index(registry_value=rows, declaration_source=declarations, discovered_test_ids=known)
                registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
                closure = registry.closure((cap,), profile=PROFILE)
                self.assertIn("status:" + cap + ":blocked", closure.blockers)
                self.assertFalse(any("unverified" in item for item in closure.blockers))
                ready, _ = register_card(self.db, registry, GAMBIT)
                self.assertFalse(ready["strict_capability_ready"], ready)
                control, _ = register_card(self.db, registry, "Grizzly Bears")
                self.assertTrue(control["strict_capability_ready"], control["blockers"])
                ir = compile_oracle_card(self.db.lookup(GAMBIT, fuzzy=False), capability_registry=registry,
                    capability_profile=PROFILE)
                self.assertTrue(any(n.exact and n.template_id == "continuous-attached-fixed-characteristics-v1"
                    for n in ir.faces[0].nodes))
