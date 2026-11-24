"""Native evidence for the one-shot all-permanents result at the next end step."""

from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.compiler import source_self_effect_templates
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.destruction_result_model import (
    NEXT_END_STEP_DESTRUCTION_CAPABILITY, NEXT_END_STEP_DESTRUCTION_FIELD,
    next_end_step_destruction_effect, validate_next_end_step_destruction,
)
from quorune.target_source import TARGETING_SOURCE_CONTEXT, TargetingSourceBinding
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


BEARER = "Bearer of the Heavens"
PROFILE = "commander_review"
FIXTURES = ROOT / "tests" / "fixtures"


class NextEndStepDestructionTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        folder = Path(cls.temporary.name)
        support = {
            "activation-public-state-cards.json": {"Raise the Alarm"},
            "activation-restriction-material-cards.json": {"Murder", "Sol Ring", "Forest", "Plains", "Swamp"},
            "announced-target-cardinality-cards.json": {"Darksteel Citadel"},
            "bound-effect-program-cards.json": {"Generic Bound Plains", "Generic Bound Growth"},
            "source-attack-alone-cards.json": {"Isamaru, Hound of Konda"},
        }
        parts = [FIXTURES / "next-end-step-destruction-cards.json"]
        for name, names in support.items():
            doc = json.loads((FIXTURES / name).read_bytes())
            doc["cards"] = [row for row in doc["cards"] if row["name"] in names]
            assert names == {row["name"] for row in doc["cards"]}, name
            ids = {row["oracle_id"] for row in doc["cards"]}
            doc["rulings"] = [row for row in doc.get("rulings", ()) if row["oracle_id"] in ids]
            part = folder / name
            part.write_text(json.dumps(doc), encoding="utf-8")
            parts.append(part)
        path = folder / "next-end-step-destruction.sqlite3"
        build_fixture_database(parts, path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Next end step destruction", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"),
            DeckEntry("Generic Bound Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def _original(self):
        card = self.db.lookup(BEARER, fuzzy=False)
        report, semantics = register_card(self.db, self.registry, BEARER)
        self.assertTrue(report["strict_capability_ready"], report["blockers"])
        return card, tuple(semantics.programs_for_oracle(card.oracle_id))

    def _death(self, game, source, removal):
        identity = game.state.cards[source.object_id].logical_object_id
        self.cast(game, "B", removal, [source.ref])
        self.resolve_top(game)
        self.assertEqual("graveyard", game.state.cards[source.object_id].zone)
        pending, = [item for item in game.state.stack if item.source_object_id == source.object_id]
        binding = TargetingSourceBinding.from_dict(pending.context[TARGETING_SOURCE_CONTEXT])
        self.assertEqual((source.object_id, identity, "battlefield"), (binding.object_id, binding.logical_object_id, binding.zone))
        self.assertEqual("A", pending.controller)
        return identity

    def _wait_for_delayed_stack(self, game, ref):
        self.until(game, lambda current: any(item.context.get("delayed_trigger_ref") == ref for item in current.state.stack))
        item, = [item for item in game.state.stack if item.context.get("delayed_trigger_ref") == ref]
        self.assertEqual(("ending", "end_step", "A"), (game.state.phase, game.state.step, item.controller))
        return item

    def _scheduled(self, game, source, identity):
        trigger, = [row for row in game.state.delayed_triggers if row.active]
        self.assertEqual(("A", source.object_id, identity), (trigger.controller, trigger.source_object_id, trigger.source_logical_object_id))
        self.assertEqual("step.begin", trigger.event_kind)
        self.assertEqual({"phase": "ending", "step": "end_step"}, trigger.condition)
        self.assertTrue(trigger.once)
        self.assertIsNone(trigger.expires_turn_sequence)
        return trigger

    def test_wire_has_one_actual_end_step_and_universal_untargeted_result(self):
        effect = next_end_step_destruction_effect()
        validate_next_end_step_destruction(effect)
        for changed in (
            dict(effect, once=False), dict(effect, source_binding="$source"),
            dict(effect, condition={"phase": "ending", "step": "cleanup"}),
            dict(effect, **{NEXT_END_STEP_DESTRUCTION_FIELD: True}),
        ):
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                validate_next_end_step_destruction(changed)
        malformed = deepcopy(effect)
        malformed["stack"]["context"]["dynamic_effects"][0]["set"]["exclude_source"] = True
        with self.assertRaises(ValueError):
            validate_next_end_step_destruction(malformed)

    def test_complete_original_owns_its_sealed_death_and_independent_delayed_authority(self):
        record, programs = self._original()
        ir = compile_oracle_card(record, capability_registry=self.registry, capability_profile=PROFILE)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        trigger, = programs
        self.assertEqual(("creature.dies.self", "battlefield"), (trigger.event, trigger.active_zone))
        self.assertIsNone(trigger.target_schema)
        self.assertLessEqual({NEXT_END_STEP_DESTRUCTION_CAPABILITY, "permanent.destroy.fixed_set",
                              "trigger.event.normalized_zone_change"}, set(trigger.capability_dependencies))
        effect, = trigger.effects
        validate_next_end_step_destruction(effect)

    def test_actual_death_schedules_once_and_destroys_permanents_that_enter_in_response(self):
        game = self.session(6493120)
        bearer = self.add(game.engine, BEARER)
        murder = self.add(game.engine, "Murder", seat="B", zone="hand")
        late = self.add(game.engine, "Sol Ring", zone="hand")
        alarm = self.add(game.engine, "Raise the Alarm", seat="B", zone="hand")
        before = self.add(game.engine, "Forest", seat="C")
        indestructible = self.add(game.engine, "Darksteel Citadel", seat="C")
        private = self.add(game.engine, "Generic Bound Growth", seat="D", zone="hand", ref="end-step-private")
        for index in range(2):
            self.add(game.engine, "Plains", seat="B", ref=f"end-step-response-mana-{index}")
        self.prepare(game, mana={"C": 1})
        game.state.players["B"].mana_pool.update({"C": 1, "B": 2})
        self.checkpoint(game)

        identity = self._death(game, bearer, murder)
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        delayed = self._scheduled(game, bearer, identity)
        self.assertEqual("battlefield", game.state.cards[before.object_id].zone)
        self.cast(game, "A", late)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("battlefield", game.state.cards[late.object_id].zone)

        pending = self._wait_for_delayed_stack(game, delayed.ref)
        self.assertEqual(bearer.object_id, pending.source_object_id)
        self.assertEqual(identity, pending.context["source_logical_object_id"])
        self.cast(game, "B", alarm)
        self.resolve_top(game)
        new_tokens = [card.object_id for card in game.state.cards.values()
                      if card.zone == "battlefield" and card.is_token and card.controller == "B"]
        self.assertEqual(2, len(new_tokens))
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        for card in (before, late):
            self.assertEqual("graveyard", game.state.cards[card.object_id].zone)
        self.assertTrue(all(object_id not in game.state.players["B"].zones["battlefield"]
                            for object_id in new_tokens))
        self.assertEqual("battlefield", game.state.cards[indestructible.object_id].zone)
        self.assertFalse(any(t.active and t.ref == delayed.ref for t in game.state.delayed_triggers))
        self.assertNotIn(private.ref, json.dumps(game.packet("pilot:A", full=True)))
        self.replay(game, load=True)

    def test_actual_death_during_an_end_step_waits_for_the_next_turns_end_step(self):
        game = self.session(6493130)
        bearer = self.add(game.engine, BEARER)
        murder = self.add(game.engine, "Murder", seat="B", zone="hand")
        forest = self.add(game.engine, "Forest", seat="C")
        for index in range(3):
            self.add(game.engine, "Swamp", seat="B", ref=f"late-death-response-mana-{index}")
        self.prepare(game)
        self.checkpoint(game)
        turn = game.state.turn_sequence
        self.until(game, lambda current: current.state.step == "end_step")
        identity = self._death(game, bearer, murder)
        self.assertIsNone(self.resolve(game))
        delayed = self._scheduled(game, bearer, identity)
        self.assertEqual((turn, "end_step"), (game.state.turn_sequence, game.state.step))
        self.assertFalse(game.state.stack)
        self.assertEqual("battlefield", game.state.cards[forest.object_id].zone)
        game = self.replay(game, load=True)
        pending = self._wait_for_delayed_stack(game, delayed.ref)
        self.assertEqual((turn + 1, "B"), (game.state.turn_sequence, game.state.active_player))
        self.assertEqual(identity, pending.context["source_logical_object_id"])
        self.assertIsNone(self.resolve(game))
        self.assertEqual("graveyard", game.state.cards[forest.object_id].zone)
        self.assertFalse(any(row.active and row.ref == delayed.ref for row in game.state.delayed_triggers))
        self.replay(game, load=True)

    def test_omitting_the_exact_delayed_result_restores_the_original_residual(self):
        original = source_self_effect_templates.compile_source_self_zone_effect

        def without_scope(body, **kwargs):
            if body.strip().casefold() == "destroy all permanents at the beginning of the next end step.":
                return None, (), None, (), None
            return original(body, **kwargs)

        with patch.object(source_self_effect_templates, "compile_source_self_zone_effect", side_effect=without_scope):
            card = self.db.lookup(BEARER, fuzzy=False)
            ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile=PROFILE)
            residual, = ir.material_residuals
            self.assertEqual("trigger_effect", residual.kind)
            report, _ = register_card(self.db, self.registry, BEARER)
            self.assertFalse(report["strict_capability_ready"], report)
            control, _ = register_card(self.db, self.registry, "Sol Ring")
            self.assertTrue(control["strict_capability_ready"], control["blockers"])

    def test_genuine_delayed_set_authority_withdrawal_denies_the_complete_original(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests

        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        known = set(discover_tests(ROOT).values())
        doc = deepcopy(raw)
        next(row for row in doc["capabilities"] if row["id"] == NEXT_END_STEP_DESTRUCTION_CAPABILITY).update(
            status="blocked", blockers=["Scoped next-end-step universal destruction withdrawal"])
        registry = CapabilityRegistry(doc)
        index = build_index(registry_value=doc, declaration_source=declarations, discovered_test_ids=known)
        registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
        closure = registry.closure((NEXT_END_STEP_DESTRUCTION_CAPABILITY,), profile=PROFILE)
        self.assertIn("status:" + NEXT_END_STEP_DESTRUCTION_CAPABILITY + ":blocked", closure.blockers)
        self.assertFalse(any("unverified" in blocker for blocker in closure.blockers))
        report, _ = register_card(self.db, registry, BEARER)
        self.assertFalse(report["strict_capability_ready"], report)
        control, _ = register_card(self.db, registry, "Sol Ring")
        self.assertTrue(control["strict_capability_ready"], control["blockers"])


if __name__ == "__main__":
    unittest.main()
