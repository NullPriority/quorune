"""One declared target is returned, then copied through its retained public values."""

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
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.token_copy_recipes import TokenCopyRecipeSpec
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


SUPPLANT = "Supplant Form"
CAPABILITY = "token.creation.fixed_copy"
PROFILE = "commander_review"
FIXTURES = ROOT / "tests" / "fixtures"


class ReturnedTargetCopyTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        folder = Path(cls.temporary.name)
        support = {
            "temporary-creature-copy-set-cards.json": {"Mirage Mirror", "Mirrorweave"},
            "bound-effect-program-cards.json": {"Generic Bound Plains", "Unsummon"},
            "activation-public-state-cards.json": {"Giant Growth"},
            "face-down-method-cards.json": {"Soul Summons"},
            "attached-destruction-set-cards.json": {"Grizzly Bears", "Island"},
            "creature-subtype-addition-cards.json": {"Isamaru, Hound of Konda"},
        }
        parts = [FIXTURES / "returned-target-copy-cards.json"]
        for name, names in support.items():
            data = json.loads((FIXTURES / name).read_bytes())
            data["cards"] = [card for card in data["cards"] if card["name"] in names]
            assert {card["name"] for card in data["cards"]} == names, name
            ids = {card["oracle_id"] for card in data["cards"]}
            data["rulings"] = [row for row in data.get("rulings", ()) if row["oracle_id"] in ids]
            part = folder / name
            part.write_text(json.dumps(data), encoding="utf-8")
            parts.append(part)
        database = folder / "returned-target-copy.sqlite3"
        build_fixture_database(parts, database)
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Returned target copy", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def test_original_uses_one_target_and_independent_typed_return_and_copy_owners(self):
        original = self.db.lookup(SUPPLANT, fuzzy=False)
        ir = compile_oracle_card(original, capability_registry=self.registry, capability_profile=PROFILE)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        report, semantics = register_card(self.db, self.registry, SUPPLANT)
        self.assertTrue(report["strict_capability_ready"], report["blockers"])
        self.assertEqual("capability_closed", report["trust_basis"])
        program, = semantics.programs_for_oracle(original.oracle_id)
        self.assertEqual({"zones": ["battlefield"], "categories": ["permanent"], "count": 1, "types_any": ["creature"]},
                         program.target_schema)
        bounce, create = program.effects
        self.assertEqual({"op": "bounce", "card": "$target.0"}, bounce)
        self.assertEqual(TokenCopyRecipeSpec("target"), TokenCopyRecipeSpec.from_dict(create["copy_spec"]))
        self.assertIn("permanent.return.owner_hand", program.capability_dependencies)
        self.assertIn("resolution.effect_program.bound_references", program.capability_dependencies)
        self.assertIn(CAPABILITY, program.capability_dependencies)
        legacy = {key: value for key, value in create.items() if key != "copy_spec"}
        legacy["copy_of"] = "$target.0"
        changed = replace(program, effects=(bounce, legacy))
        self.assertFalse(bind_semantic_program_runtime(changed, capability_registry=self.registry, profile=PROFILE)["strict"])

    def _start(self, game, originals, mana, *, active="A", other_mana=None):
        for card in originals:
            self.assert_closed(game, card)
        game.state.config.semantic_policy = "trusted_only"
        self.prepare(game, active=active, actor=active, mana=mana)
        if other_mana is not None:
            seat, reserve = other_mana
            game.state.players[seat].mana_pool.update(reserve)
        self.checkpoint(game)

    def _only_token(self, game):
        token, = [card for card in game.state.cards.values() if card.is_token and card.zone == "battlefield"]
        self.assertEqual("A", token.controller)
        return token

    def test_actual_return_copies_the_targets_latest_copiable_face_in_order(self):
        game = self.session(70724000)
        spell = self.add(game.engine, SUPPLANT, zone="hand")
        target = self.add(game.engine, "Mirage Mirror", seat="B")
        model = self.add(game.engine, "Grizzly Bears", seat="C")
        private = self.add(game.engine, "Giant Growth", seat="D", zone="hand", ref="supplant-private-card")
        target.counters["+1/+1"] = 1
        self._start(game, (spell, target), {"C": 4, "U": 2}, other_mana=("B", {"C": 2}))
        self.priority(game, "B")
        actions = game.packet("pilot:B", full=True)["decision"]["ctx"]["legal"]["actions"]
        row, = (a for a in actions if a["id"].startswith("activate:" + target.ref + ":"))
        result = game.act("pilot:B", {"action_id": row["id"], "targets": [model.ref], "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("Grizzly Bears", game.engine._effective_card_data(game.state.cards[target.object_id])["name"])
        self.cast(game, "A", spell, [target.ref])
        game = self.replay(game, load=True)
        cursor = len(game.state.events)
        self.assertIsNone(self.resolve(game))
        restored = game.state.cards[target.object_id]
        self.assertEqual(("B", "hand"), (restored.owner, restored.zone))
        self.assertEqual("Mirage Mirror", game.engine._effective_card_data(restored)["name"])
        token = self._only_token(game)
        self.assertEqual("Grizzly Bears", game.engine._effective_card_data(token)["name"])
        self.assertEqual({}, token.counters)
        events = game.state.events[cursor:]
        bounce, = [event for event in events if event.code == "permanent.return_to_owner_hand"
                   and event.details.get("destination") == "hand" and event.details.get("object") == target.ref]
        copy, = [event for event in events if event.code == "token.create"]
        self.assertLess(events.index(bounce), events.index(copy))
        for viewer in "ABC":
            self.assertNotIn(private.ref, json.dumps(game.packet("pilot:" + viewer, full=True)))
        self.replay(game, load=True)

    def test_actual_returned_face_down_card_creates_an_anonymous_face_up_token(self):
        game = self.session(70724001)
        spell = self.add(game.engine, SUPPLANT, zone="hand")
        manifest = self.add(game.engine, "Soul Summons", seat="B", zone="hand")
        target = self.add(game.engine, "Giant Growth", seat="B", zone="library", ref="supplant-face-down")
        target.known_to.clear()
        target.revealed_to.clear()
        self._start(game, (spell, manifest), {"C": 1, "W": 1}, active="B", other_mana=("A", {"C": 4, "U": 2}))
        self.cast(game, "B", manifest)
        self.assertIsNone(self.resolve(game))
        self.assertTrue(game.state.cards[target.object_id].face_down)
        self.assertNotIn("Giant Growth", json.dumps(game.packet("pilot:A", full=True)["state"]))
        self.cast(game, "A", spell, [target.ref])
        self.assertIsNone(self.resolve(game))
        self.assertEqual("hand", game.state.cards[target.object_id].zone)
        token = self._only_token(game)
        self.assertFalse(token.face_down)
        self.assertNotEqual(target.oracle_id, token.oracle_id)
        values = game.engine._effective_card_data(token)
        self.assertEqual(("", "Creature", [], []), (values["name"], values["type_line"], values["colors"], values["keywords"]))
        self.assertEqual((2, 2), tuple(game.engine._numeric_stat(token.object_id, stat) for stat in ("power", "toughness")))
        self.replay(game, load=True)

    def test_departure_before_resolution_cannot_supply_a_copy_to_the_illegal_target_spell(self):
        game = self.session(70724002)
        spell = self.add(game.engine, SUPPLANT, zone="hand")
        target = self.add(game.engine, "Grizzly Bears", seat="B")
        bounce = self.add(game.engine, "Unsummon", seat="B", zone="hand")
        self.add(game.engine, "Island", seat="B")
        self._start(game, (spell,), {"C": 4, "U": 2})
        offered = self.action(game, "A", spell)
        command = {"action_id": offered["id"], "targets": [target.ref], "pay": "auto"}
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:D", command).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        self.cast(game, "A", spell, [target.ref])
        self.cast(game, "B", bounce, [target.ref])
        self.resolve_top(game)
        self.assertEqual("hand", game.state.cards[target.object_id].zone)
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertFalse(any(card.is_token for card in game.state.cards.values()))
        self.assertTrue(any(event.code == "target.illegal" for event in game.state.events))
        self.replay(game, load=True)

    def test_omission_keeps_the_legacy_copy_operand_out_of_complete_admission(self):
        with patch("quorune.compiler.bound_effect_programs._returned_creature_copy_tail", return_value=None):
            original = self.db.lookup(SUPPLANT, fuzzy=False)
            ir = compile_oracle_card(original, capability_registry=self.registry, capability_profile=PROFILE)
            self.assertNotEqual("exact", ir.status)
            report, _ = register_card(self.db, self.registry, SUPPLANT)
            self.assertFalse(report["strict_capability_ready"])

    def test_verified_target_copy_withdrawal_keeps_the_independent_temporary_copy_set(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests

        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        next(row for row in raw["capabilities"] if row["id"] == CAPABILITY).update(
            status="blocked", blockers=["Scoped fixed token copy withdrawal"],
        )
        registry = CapabilityRegistry(raw)
        index = build_index(registry_value=raw, declaration_source=declarations,
                            discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
        closure = registry.closure((CAPABILITY,), profile=PROFILE)
        self.assertIn("status:" + CAPABILITY + ":blocked", closure.blockers)
        self.assertFalse(any("unverified" in blocker for blocker in closure.blockers))
        report, _ = register_card(self.db, registry, SUPPLANT)
        self.assertFalse(report["strict_capability_ready"], report)
        control, _ = register_card(self.db, registry, "Mirrorweave")
        self.assertTrue(control["strict_capability_ready"], control["blockers"])


if __name__ == "__main__":
    unittest.main()
