from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.attachment_references import (
    ATTACHMENT_INFORMATION_CAPABILITY, ATTACHMENT_INFORMATION_VERSION, SourceAttachmentSnapshot,
)
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.compiler import public_trigger_conditions
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.replacement.immutable import thaw_value
from quorune.rules.attached_greatest_power_condition import (
    ATTACHED_GREATEST_POWER_CAPABILITIES, AttachedGreatestPowerCondition,
    attached_greatest_power_event_condition,
)
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


PROFILE = "commander_review"
FIXTURES = ROOT / "tests" / "fixtures"
ORIGINAL = "Historian's Wisdom"


class AttachedGreatestPowerConditionTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        folder = Path(cls.temporary.name)
        support = {
            "ability-source-counter-cards.json": {"Sol Ring"},
            "additional-continuous-carriers-cards.json": {"Walking Corpse"},
            "attached-exile-activation-cards.json": {"Cloudshift", "Disenchant"},
            "attached-greatest-power-condition-cards.json": {ORIGINAL},
            "bound-effect-program-cards.json": {"Generic Bound Plains"},
            "counter-replacement-cards.json": {"Ancient Brontodon"},
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
        path = folder / "attached-greatest-power.sqlite3"
        build_fixture_database(subsets, path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Attached greatest power", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"),
            DeckEntry("Generic Bound Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def _original(self):
        card = self.db.lookup(ORIGINAL, fuzzy=False)
        result, semantics = register_card(self.db, self.registry, ORIGINAL)
        self.assertTrue(result["strict_capability_ready"], result["blockers"])
        programs = semantics.programs_for_oracle(card.oracle_id)
        trigger, = [program for program in programs if program.event == "permanent.enter.self"]
        return card, trigger

    def test_complete_historian_binds_the_attachment_condition_and_its_private_entry_draw(self):
        card, trigger = self._original()
        ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile=PROFILE)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        predicate = attached_greatest_power_event_condition(trigger.event_condition)
        self.assertEqual(AttachedGreatestPowerCondition(), predicate)
        self.assertEqual([{"op": "draw", "player": "$controller", "count": 1, "private": True}],
                         thaw_value(trigger.effects))
        self.assertIsNone(trigger.target_schema)
        self.assertLessEqual(set(ATTACHED_GREATEST_POWER_CAPABILITIES), set(trigger.capability_dependencies))
        self.assertIn("intervening_condition", trigger.coverage)

    def test_attached_maximum_codec_rejects_unrelated_attachment_and_trigger_shapes(self):
        value = AttachedGreatestPowerCondition().to_dict()
        self.assertEqual(value, AttachedGreatestPowerCondition.from_dict(value).to_dict())
        for changes in ({"schema_version": True}, {"attachment": {**value["attachment"], "schema_version": 1}},
                        {"attachment": None}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                AttachedGreatestPowerCondition.from_dict({**value, **changes})
        card, trigger = self._original()
        for changes in ({"event": "creature.attacks"}, {"effects": [{**thaw_value(trigger.effects)[0], "count": 2}]}):
            with self.subTest(changes=changes):
                result = bind_semantic_program_runtime(replace(trigger, **changes),
                                                       capability_registry=self.registry, profile=PROFILE)
                self.assertFalse(result["strict"], result)
        unsupported = replace(card, oracle_text=card.oracle_text.replace("greatest power", "greatest toughness"))
        ir = compile_oracle_card(unsupported, capability_registry=self.registry, capability_profile=PROFILE)
        self.assertTrue(ir.material_residuals, ir)

    def _setup(self, seed, *, medium_competitor=False, huge_competitor=False, response=None, artifact=False):
        game = self.session(seed)
        source = self.add(game.engine, ORIGINAL, zone="hand")
        attached = self.add(game.engine, "Sol Ring" if artifact else "Walking Corpse", seat="B")
        competitor = self.add(game.engine, "Ancient Brontodon" if huge_competitor else "Walking Corpse",
                              seat="C", ref="historian-other-creature")
        if medium_competitor:
            competitor.counters["+1/+1"] = 1
        reply = self.add(game.engine, response, seat="B", zone="hand") if response else None
        for card in (source, attached, competitor, reply):
            if card is not None:
                self.assert_closed(game, card)
        self.prepare(game, mana={"C": 2, "G": 1})
        game.state.players["B"].mana_pool.update({"C": 1, "W": 1})
        self.checkpoint(game)
        self.cast(game, "A", source, [attached.ref])
        self.resolve_top(game)
        self.assertEqual(attached.object_id, game.state.cards[source.object_id].attached_to)
        return game, source, attached, competitor, reply

    def _trigger(self, game, source):
        trigger, = [item for item in game.state.stack
                    if item.kind == "triggered_ability" and item.source_object_id == source.object_id]
        self.assertEqual("A", trigger.controller)
        self.assertEqual([], trigger.targets)
        self.assertEqual(1, trigger.context[ATTACHMENT_INFORMATION_VERSION])
        return trigger

    def _hands(self, game):
        return {seat: set(game.state.players[seat].zones["hand"]) for seat in "ABCD"}

    def _assert_draw(self, game, before, count):
        actual = self._hands(game)
        for seat in "BCD":
            self.assertEqual(before[seat], actual[seat])
        self.assertLessEqual(before["A"], actual["A"])
        gained = actual["A"] - before["A"]
        self.assertEqual(count, len(gained))
        for object_id in gained:
            ref = game.state.cards[object_id].ref
            self.assertIn(ref, json.dumps(game.packet("pilot:A", full=True)))
            for seat in "BCD":
                self.assertNotIn(ref, json.dumps(game.packet("pilot:" + seat, full=True)))

    def test_actual_historian_checks_current_rank_and_its_independent_aura_bonus_at_entry(self):
        for mode in ("maximum", "below", "artifact"):
            with self.subTest(mode=mode):
                game, source, attached, _, _ = self._setup(
                    77147000 + len(mode), medium_competitor=mode == "maximum",
                    huge_competitor=mode == "below", artifact=mode == "artifact",
                )
                before = self._hands(game)
                if mode == "maximum":
                    trigger = self._trigger(game, source)
                    sealed = SourceAttachmentSnapshot.from_dict(trigger.context["source_attachment_snapshot"])
                    self.assertEqual(attached.logical_object_id, sealed.attached_object.logical_object_id)
                else:
                    self.assertEqual(0, self.trigger_count(game, source))
                if mode != "artifact":
                    self.assertEqual(4, game.engine._numeric_stat(attached.object_id, "power"))
                game = self.replay(game, load=True)
                self.assertIsNone(self.resolve(game))
                self._assert_draw(game, before, int(mode == "maximum"))
                self.replay(game, load=True)

    def test_actual_historian_uses_its_departure_attachment_and_the_creatures_current_power(self):
        for still_maximum in (False, True):
            with self.subTest(still_maximum=still_maximum):
                game, source, attached, competitor, reply = self._setup(
                    77147010 + still_maximum, medium_competitor=not still_maximum, response="Disenchant",
                )
                self._trigger(game, source)
                self.cast(game, "B", reply, [source.ref])
                self.resolve_top(game)
                self.assertEqual("graveyard", game.state.cards[source.object_id].zone)
                self.assertEqual(2, game.engine._numeric_stat(attached.object_id, "power"))
                self.assertEqual(2 if still_maximum else 3, game.engine._numeric_stat(competitor.object_id, "power"))
                retained = SourceAttachmentSnapshot.from_dict(self._trigger(game, source).context["source_attachment_snapshot"])
                self.assertEqual(attached.logical_object_id, retained.attached_object.logical_object_id)
                before = self._hands(game)
                game = self.replay(game, load=True)
                self.assertIsNone(self.resolve(game))
                self._assert_draw(game, before, int(still_maximum))
                self.replay(game, load=True)

    def test_actual_historian_does_not_rebind_a_blinked_creature_after_its_detached_aura_departs(self):
        game, source, attached, _, reply = self._setup(77147020, response="Cloudshift")
        original = attached.logical_object_id
        self._trigger(game, source)
        self.cast(game, "B", reply, [attached.ref])
        self.resolve_top(game)
        live = game.state.cards[attached.object_id]
        self.assertEqual("battlefield", live.zone)
        self.assertNotEqual(original, live.logical_object_id)
        self.assertEqual("graveyard", game.state.cards[source.object_id].zone)
        departed = SourceAttachmentSnapshot.from_dict(self._trigger(game, source).context["source_attachment_snapshot"])
        self.assertIsNone(departed.attached_object)
        before = self._hands(game)
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self._assert_draw(game, before, 0)
        self.replay(game, load=True)

    def test_omitting_the_attached_maximum_lowering_restores_the_original_trigger_residual(self):
        with patch.object(public_trigger_conditions, "_attached_greatest_power_trigger_condition", return_value=None):
            card = self.db.lookup(ORIGINAL, fuzzy=False)
            ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile=PROFILE)
            self.assertEqual([2], [residual.span.line for residual in ir.material_residuals])
            result, _ = register_card(self.db, self.registry, ORIGINAL)
            self.assertFalse(result["strict_capability_ready"], result)
            control, _ = register_card(self.db, self.registry, "Sol Ring")
            self.assertTrue(control["strict_capability_ready"], control["blockers"])

    def test_genuine_attachment_and_maximum_authority_withdrawals_block_the_original(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests

        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        known = set(discover_tests(ROOT).values())
        for capability in (ATTACHMENT_INFORMATION_CAPABILITY, "quantity_expression.public_characteristic_aggregate"):
            with self.subTest(capability=capability):
                document = deepcopy(raw)
                next(row for row in document["capabilities"] if row["id"] == capability).update(
                    status="blocked", blockers=["Scoped attached maximum trigger authority withdrawal"])
                registry = CapabilityRegistry(document)
                index = build_index(registry_value=document, declaration_source=declarations, discovered_test_ids=known)
                registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
                closure = registry.closure((capability,), profile=PROFILE)
                self.assertIn("status:" + capability + ":blocked", closure.blockers)
                self.assertFalse(any("unverified" in blocker for blocker in closure.blockers))
                original, _ = register_card(self.db, registry, ORIGINAL)
                self.assertFalse(original["strict_capability_ready"], original)
                control, _ = register_card(self.db, registry, "Sol Ring")
                self.assertTrue(control["strict_capability_ready"], control["blockers"])


if __name__ == "__main__":
    unittest.main()
