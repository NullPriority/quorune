"""Native optional temporary copy entry, scoped haste and exact replay."""

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from optional_result_followup_fixture import OptionalResultFollowupFixture
from quorune.carddb import CardDatabase
from quorune.compiler.entry_copy_templates import entry_copy_spec
from quorune.deck import DeckDefinition, DeckEntry
from quorune.entry_copy import (
    EntryCopySpec, TEMPORARY_HASTE_COPY_CAPABILITY, TEMPORARY_HASTE_COPY_HANDLER,
)
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.replacement.face_status_copy import COPY_HISTORY_FIELD, CopyExpiry, current_copy_history
from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry
from scripts.build_test_database import build_fixture_database


class TemporaryHasteEntryCopyTests(InheritedNativeFixture, unittest.TestCase):
    finish_turn = OptionalResultFollowupFixture.finish_turn

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "temporary-copy-entry.sqlite3"
        build_fixture_database((
            ROOT / "tests/fixtures/temporary-haste-entry-copy-cards.json",
            ROOT / "tests/fixtures/entry-copy-values-cards.json",
            ROOT / "tests/fixtures/inherited-rule-composition-cards.json",
        ), path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition(
            "Timed entry copy",
            [DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30)],
            ["Isamaru, Hound of Konda"],
        )

    def scene(self, seed, *, descendant=False, bounce=False):
        game = self.session(seed)
        mirror = self.add(game.engine, "Cursed Mirror", zone="hand", ref="timed-copy-mirror")
        model = self.add(game.engine, "Elvish Visionary", seat="B", ref="timed-copy-creature")
        noncreature = self.add(game.engine, "Plains", seat="B", ref="timed-copy-land")
        private = self.add(game.engine, "Elvish Visionary", seat="D", zone="hand", ref="timed-copy-private")
        model.tapped = True
        model.counters["+1/+1"] = 2
        other = (self.add(game.engine, "Clone", zone="hand", ref="timed-copy-descendant") if descendant
                 else self.add(game.engine, "Unsummon", zone="hand", ref="timed-copy-bounce") if bounce
                 else None)
        for card in (mirror, model, *((other,) if other is not None else ())):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 9, "R": 3, "U": 3})
        self.checkpoint(game)
        return game, mirror, model, noncreature, private, other

    def start_copy(self, game, source):
        self.cast_for(game, "A", game.state.cards[source.object_id], [])
        pending = self.resolve(game)
        self.assertEqual(("replacement.order", ["A"]), (pending.kind, list(pending.actors)))
        return game.packet("pilot:A", full=True)["decision"]["ctx"]["options"]

    def choose(self, game, options, model=None):
        if model is None:
            chosen = next(row for row in options if row["id"].startswith("decline:"))
        else:
            chosen = next(row for row in options if not row["id"].startswith("decline:")
                          and row["id"].endswith(":" + model.ref))
        result = game.act("pilot:A", {"action_id": "choose", "replacement": chosen["id"]})
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(game))

    def available_mana(self, game, source):
        self.priority(game, "A")
        return next((row for row in game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
                     if row["id"] == f"activate:{source.ref}:ab1"), None)

    def assert_original_and_tap_red(self, game, source):
        current = game.state.cards[source.object_id]
        effective = game.engine._effective_card_data(current)
        self.assertEqual(("battlefield", "Cursed Mirror", {"artifact"}),
                         (current.zone, effective["name"], game.engine._type_parts(effective["type_line"])[0]))
        self.assertNotIn("Haste", effective["keywords"])
        self.assertNotIn(COPY_HISTORY_FIELD, current.annotations)
        mana = self.available_mana(game, current)
        self.assertIsNotNone(mana)
        before = game.state.players["A"].mana_pool["R"]
        activated = game.act("pilot:A", {"action_id": mana["id"]})
        self.assertTrue(activated.ok, activated.summary)
        self.assertTrue(game.state.cards[source.object_id].tapped)
        self.assertEqual(before + 1, game.state.players["A"].mana_pool["R"])

    def test_original_closes_with_exact_timed_haste_and_preserved_mana(self):
        card = self.db.lookup("Cursed Mirror", fuzzy=False)
        ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile="commander_review")
        self.assertEqual("exact", ir.status)
        nodes = {node.kind: node for node in ir.faces[0].nodes}
        self.assertIn("mana.activated.fixed_output", nodes["mana_ability"].capability_dependencies)
        node = nodes["replacement_effect"]
        self.assertIn(TEMPORARY_HASTE_COPY_CAPABILITY, node.capability_dependencies)
        descriptor, = node.handlers
        self.assertEqual(TEMPORARY_HASTE_COPY_HANDLER, descriptor["handler_id"])
        spec = EntryCopySpec.from_dict(descriptor["copy"])
        self.assertTrue(spec.temporary_haste)
        self.assertEqual(("Haste",), spec.exception.add_keywords)
        for before, after in (
            ("until end of turn", "until end of combat"),
            ("has haste", "has flying"),
            ("any creature", "any permanent"),
            ("on the battlefield", "in a graveyard"),
        ):
            with self.subTest(after=after):
                wrong = replace(card, oracle_text=card.oracle_text.replace(before, after))
                self.assertNotEqual("exact", compile_oracle_card(
                    wrong, capability_registry=self.registry, capability_profile="commander_review",
                ).status)
        malformed = {**spec.to_dict(), "exception": {**spec.exception.to_dict(), "add_keywords": []}}
        with self.assertRaises(ValueError):
            EntryCopySpec.from_dict(malformed)
        self.assertEqual(spec, entry_copy_spec(card.oracle_text.splitlines()[1], source_name=card.name))

    def test_required_timed_copy_authority_and_source_production_fail_closed(self):
        card = self.db.lookup("Cursed Mirror", fuzzy=False)
        document = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        entry = next(row for row in document["capabilities"] if row["id"] == TEMPORARY_HASTE_COPY_CAPABILITY)
        entry.update(status="blocked", blockers=["Timed copy authority withdrawn in this witness."])
        blocked = compile_oracle_card(card, capability_registry=CapabilityRegistry(document),
                                      capability_profile="commander_review")
        self.assertNotEqual("exact", blocked.status)
        with patch("quorune.compiler.entry_copy_templates.entry_copy_spec", return_value=None):
            omitted = compile_oracle_card(card, capability_registry=self.registry, capability_profile="commander_review")
        self.assertNotEqual("exact", omitted.status)
        self.assertTrue(any("except it has haste" in residual.text for residual in omitted.material_residuals))

    def test_decline_keeps_printed_mana_and_private_or_forged_options_reject(self):
        game, mirror, model, noncreature, private, _ = self.scene(49388000)
        options = self.start_copy(game, mirror)
        offered = [row["id"] for row in options if not row["id"].startswith("decline:")]
        self.assertEqual(1, len(offered))
        self.assertTrue(offered[0].endswith(":" + model.ref))
        self.assertNotIn(noncreature.ref, json.dumps(options))
        for principal in ("pilot:A", "pilot:B", "pilot:C", "spectator"):
            self.assertNotIn(private.ref, json.dumps(game.packet(principal, full=True)))
        before = authoritative_state_hash(game.state)
        for principal, replacement_id in (("pilot:B", offered[0]), ("pilot:A", "forged-copy")):
            result = game.act(principal, {"action_id": "choose", "replacement": replacement_id})
            self.assertFalse(result.ok)
            self.assertEqual(before, authoritative_state_hash(game.state))
        self.choose(game, options)
        self.assert_original_and_tap_red(game, mirror)
        self.replay(game, load=True)

    def test_apply_is_immediate_and_another_copy_keeps_haste_after_normal_cleanup(self):
        game, mirror, model, _, _, descendant = self.scene(49388001, descendant=True)
        draws = len(game.state.players["A"].draw_history)
        options = self.start_copy(game, mirror)
        game = self.replay(game, load=True)
        self.choose(game, options, model)
        copied = game.state.cards[mirror.object_id]
        self.assertFalse(copied.tapped)
        self.assertEqual({}, copied.counters)
        self.assertEqual(draws + 1, len(game.state.players["A"].draw_history))
        self.assertEqual("Elvish Visionary", game.engine.display_name(copied.object_id))
        self.assertIn("Haste", game.engine._effective_card_data(copied)["keywords"])
        self.assertIsNone(self.available_mana(game, copied))
        history = current_copy_history(copied)
        self.assertEqual((CopyExpiry.END_OF_TURN,), tuple(scope for scope, _ in history.ordered))

        options = self.start_copy(game, descendant)
        self.choose(game, options, copied)
        clone = game.state.cards[descendant.object_id]
        self.assertEqual(draws + 2, len(game.state.players["A"].draw_history))
        self.assertIn("Haste", game.engine._effective_card_data(clone)["keywords"])
        self.finish_turn(game)
        self.assertEqual("Elvish Visionary", game.engine.display_name(clone.object_id))
        self.assertIn("Haste", game.engine._effective_card_data(clone)["keywords"])
        self.assert_original_and_tap_red(game, mirror)
        self.replay(game, load=True)

    def test_mirror_that_leaves_and_reenters_has_a_fresh_optional_choice(self):
        game, mirror, model, _, _, bounce = self.scene(49388002, bounce=True)
        self.choose(game, self.start_copy(game, mirror), model)
        copied = game.state.cards[mirror.object_id]
        previous = copied.logical_object_id
        self.cast_for(game, "A", game.state.cards[bounce.object_id], [copied.ref])
        self.assertIsNone(self.resolve(game))
        current = game.state.cards[mirror.object_id]
        self.assertEqual("hand", current.zone)
        self.assertNotEqual(previous, current.logical_object_id)
        self.assertNotIn(COPY_HISTORY_FIELD, current.annotations)
        self.choose(game, self.start_copy(game, mirror))
        self.assert_original_and_tap_red(game, mirror)
        self.replay(game, load=True)


if __name__ == "__main__":
    unittest.main()
