"""Native and authoring evidence for exact third-resolution activated abilities."""

from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.ability_resolution_ordinal import (
    ABILITY_RESOLVED_HISTORY, is_third_ability_result, validate_third_ability_result,
)
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.compiler import resolution_condition_templates
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.replacement.immutable import thaw_value
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


PROFILE = "commander_review"
FIXTURES = ROOT / "tests" / "fixtures"
FLAMEKIN = "Soulbright Flamekin"
SEEKER = "Soulbright Seeker"
IGNITER = "Inner-Flame Igniter"
LITHOFORM = "Lithoform Engine"
ORIGINALS = {
    FLAMEKIN: ("grant_keyword_until_end_of_turn", "offer_optional_effect", "{2}"),
    SEEKER: ("grant_keyword_until_end_of_turn", "mana", "{R}"),
    IGNITER: ("modify_all_matching_permanents_until_end_of_turn", "modify_all_matching_permanents_until_end_of_turn", "{2}{R}"),
}
HISTORY = "condition.history.fixed_public_query"
CONDITION = "resolution.effect.public_condition"
MANA = "mana.production.fixed_resolution"
OPTIONAL = "effect.choice.optional_fixed"


class ThirdAbilityResolutionTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        folder = Path(cls.temporary.name)
        support = {
            "ability-label-composition-cards.json": {"Island", "Unsummon"},
            "ability-source-counter-cards.json": {"Sol Ring"},
            "activated-self-transform-cards.json": {"Act of Treason"},
            "activation-view-equivalence-cards.json": {"Stifle"},
            "additional-continuous-carriers-cards.json": {"Walking Corpse"},
            "bound-effect-program-cards.json": {"Generic Bound Plains"},
            "fixed-qualified-stack-copy-cards.json": {LITHOFORM},
            "source-attack-alone-cards.json": {"Isamaru, Hound of Konda"},
            "third-ability-resolution-cards.json": set(ORIGINALS),
        }
        subsets = []
        for filename, names in support.items():
            document = json.loads((FIXTURES / filename).read_bytes())
            document["cards"] = [row for row in document["cards"] if row["name"] in names]
            assert names == {row["name"] for row in document["cards"]}, filename
            ids = {row["oracle_id"] for row in document["cards"]}
            document["rulings"] = [row for row in document.get("rulings", ()) if row["oracle_id"] in ids]
            piece = folder / filename
            piece.write_text(json.dumps(document), encoding="utf-8")
            subsets.append(piece)
        path = folder / "third-ability-resolution.sqlite3"
        build_fixture_database(subsets, path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Third ability resolution", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"),
            DeckEntry("Generic Bound Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def _original(self, name):
        card = self.db.lookup(name, fuzzy=False)
        result, semantics = register_card(self.db, self.registry, name)
        self.assertTrue(result["strict_capability_ready"], result["blockers"])
        program, = [
            value for value in semantics.programs_for_oracle(card.oracle_id)
            if any(is_third_ability_result(effect) for effect in value.effects)
        ]
        return card, program

    def test_complete_third_resolution_originals_keep_their_fixed_prices_prefixes_and_results(self):
        for name, (prefix_op, result_op, cost) in ORIGINALS.items():
            with self.subTest(name=name):
                card, program = self._original(name)
                ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile=PROFILE)
                self.assertEqual("exact", ir.status, ir.material_residuals)
                self.assertEqual(("battlefield", "activate"), (program.active_zone, program.event))
                self.assertEqual(cost, program.cost_schema["text"])
                prefix, wrapper = thaw_value(program.effects)
                self.assertEqual(prefix_op, prefix["op"])
                self.assertIsNone(wrapper["resolution_ordinal"])
                result, = validate_third_ability_result(wrapper)
                self.assertEqual(result_op, result["op"])
                self.assertLessEqual({HISTORY, CONDITION}, set(program.capability_dependencies))
                self.assertEqual(name != IGNITER, MANA in program.capability_dependencies)
                self.assertEqual(name == FLAMEKIN, OPTIONAL in program.capability_dependencies)
                if name == SEEKER:
                    self.assertTrue(any(node.kind != "activated_ability" and "behold" in node.text.casefold()
                                        for face in ir.faces for node in face.nodes))

    def test_third_resolution_shapes_reject_forged_counts_altered_prices_and_wrong_stack_kinds(self):
        card, program = self._original(FLAMEKIN)
        _, wrapper = thaw_value(program.effects)
        bad_effects = deepcopy(wrapper["effects"])
        bad_effects[0]["effects"][0]["amount"] = 7
        for changes in (
            {"resolution_ordinal": 3}, {"schema_version": 21.0},
            {"condition": {**wrapper["condition"], "ordinal": 4}}, {"effects": bad_effects},
        ):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                validate_third_ability_result({**wrapper, **changes})
        cost = thaw_value(program.cost_schema)
        cost["mana"]["GENERIC"] = 1
        for forged in (replace(program, cost_schema=cost), replace(program, event="resolve", handlers=())):
            report = bind_semantic_program_runtime(forged, capability_registry=self.registry, profile=PROFILE)
            self.assertFalse(report["strict"], report)
        for altered in (
            card.oracle_text.replace("third time", "fourth time"),
            card.oracle_text.replace("Target creature", "Target creature you control"),
            card.oracle_text.replace("{2}:", "{3}:"),
        ):
            ir = compile_oracle_card(replace(card, oracle_text=altered), capability_registry=self.registry,
                                     capability_profile=PROFILE)
            self.assertNotEqual("exact", ir.status, altered)

    def _events(self, game, source):
        history = game.state.turn_history
        self.assertEqual(game.state.turn_sequence, history.turn_sequence)
        identity = game.state.cards[source.object_id].logical_object_id
        return [row for row in history.events if row.kind == ABILITY_RESOLVED_HISTORY and row.object_incarnation == identity]

    def _keywords(self, game, source):
        return game.engine._combat_keywords(game.state.cards[source.object_id])

    def _activation_offer(self, game, actor, source, target=None):
        self.priority(game, actor)
        actions = game.packet(f"pilot:{actor}", full=True)["decision"]["ctx"]["legal"]["actions"]
        candidates = [row for row in actions if row["id"].startswith(f"activate:{source.ref}:")]
        if target is not None:
            candidates = [row for row in candidates if f'"{target}"' in json.dumps(row.get("target_schema"))]
        self.assertEqual(1, len(candidates), candidates)
        return candidates[0]

    def _activate(self, game, actor, source, payment, target=None):
        offer = self._activation_offer(game, actor, source, target)
        command = {"action_id": offer["id"], "pay": "manual", "payment": payment}
        if target is not None:
            command["targets"] = [target]
        outcome = game.act(f"pilot:{actor}", command)
        self.assertTrue(outcome.ok, outcome.summary)
        item = game.state.stack[-1]
        self.assertEqual(("activated_ability", actor, source.object_id), (item.kind, item.controller, item.source_object_id))
        return item

    def _reject(self, game, actor, command):
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act(f"pilot:{actor}", command).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))

    def test_actual_flamekin_copy_counts_only_for_its_source_and_offers_the_third_bonus_once(self):
        for index, choice in enumerate(("apply", "decline")):
            with self.subTest(choice=choice):
                game = self.session(77153001 + index)
                source = self.add(game.engine, FLAMEKIN)
                other = self.add(game.engine, FLAMEKIN, ref="other-flamekin")
                copier = self.add(game.engine, LITHOFORM)
                opposing = self.add(game.engine, "Walking Corpse", seat="B")
                self.prepare(game, mana={"C": 12})
                self.checkpoint(game)

                self._activate(game, "A", source, {"C": 2}, opposing.ref)
                self.assertEqual([], self._events(game, source))
                self.assertIsNone(self.resolve(game))
                self.assertIn("trample", self._keywords(game, opposing))
                self._activate(game, "A", other, {"C": 2}, opposing.ref)
                self.assertIsNone(self.resolve(game))
                self.assertEqual((1, 1), (len(self._events(game, source)), len(self._events(game, other))))

                original = self._activate(game, "A", source, {"C": 2}, opposing.ref)
                self._activate(game, "A", copier, {"C": 2}, original.ref)
                copied_targets = self.resolve(game)
                self.assertEqual("semantic.choice", copied_targets.kind)
                result = game.act("pilot:A", {"action_id": "choose", "targets": [source.ref]})
                self.assertTrue(result.ok, result.summary)
                copied = game.state.stack[-1]
                self.assertNotEqual(original.ref, copied.ref)
                self.assertEqual(original.semantic_key, copied.semantic_key)
                self.assertEqual(1, len(self._events(game, source)))
                self.resolve_top(game)
                self.assertEqual(2, len(self._events(game, source)))
                self.assertEqual(0, game.state.players["A"].mana_pool["R"])

                optional = self.resolve(game)
                self.assertEqual("semantic.choice", optional.kind)
                self.assertEqual(["A"], list(optional.actors))
                self.assertIsNone(game.packet("pilot:B", full=True).get("decision"))
                self.assertEqual(3, len(self._events(game, source)))
                self.assertEqual(0, game.state.players["A"].mana_pool["R"])
                command = {"action_id": "choose", "choice": choice}
                self._reject(game, "B", command)
                self._reject(game, "A", {"action_id": "choose", "choice": "forged"})
                game = self.replay(game, load=True)
                result = game.act("pilot:A", command)
                self.assertTrue(result.ok, result.summary)
                expected_red = 8 if choice == "apply" else 0
                self.assertEqual(expected_red, game.state.players["A"].mana_pool["R"])
                self.assertEqual(0, game.state.players["B"].mana_pool["R"])

                self._activate(game, "A", source, {"C": 2}, other.ref)
                self.assertIsNone(self.resolve(game))
                self.assertIn("trample", self._keywords(game, other))
                self.assertEqual(expected_red, game.state.players["A"].mana_pool["R"])
                self.assertEqual((4, 1), (len(self._events(game, source)), len(self._events(game, other))))
                self.replay(game, load=True)

    def test_actual_seeker_beholds_excludes_an_illegal_target_and_adds_mandatory_red_on_the_third(self):
        game = self.session(77153003)
        source = self.add(game.engine, SEEKER, zone="hand")
        elemental = self.add(game.engine, FLAMEKIN)
        opposing = self.add(game.engine, "Walking Corpse", seat="B")
        bounce = self.add(game.engine, "Unsummon", seat="B", zone="hand")
        self.prepare(game, mana={"C": 8, "R": 10})
        game.state.players["B"].mana_pool["U"] = 1
        self.checkpoint(game)

        self.priority(game, "A")
        offer = self.action(game, "A", source)
        behold = next(row for row in offer["cost_options"] if "behold_cards" in row.get("choice_schema", {}))
        self.assertIn(elemental.ref, behold["choice_schema"]["behold_cards"]["legal_refs"])
        self.assertTrue(any("behold_cards" not in row.get("choice_schema", {}) for row in offer["cost_options"]))
        command = {"action_id": offer["id"], "cost_option": behold["id"], "behold_cards": [elemental.ref], "pay": "auto"}
        self._reject(game, "B", command)
        outcome = game.act("pilot:A", command)
        self.assertTrue(outcome.ok, outcome.summary)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("battlefield", game.state.cards[source.object_id].zone)
        red = game.state.players["A"].mana_pool["R"]

        live_source = game.state.cards[source.object_id]
        offer = self._activation_offer(game, "A", live_source, elemental.ref)
        self.assertNotIn(f'"{opposing.ref}"', json.dumps(offer["target_schema"]))
        self._reject(game, "A", {"action_id": offer["id"], "targets": [opposing.ref], "pay": "manual", "payment": {"R": 1}})
        self._activate(game, "A", live_source, {"R": 1}, elemental.ref)
        self.cast(game, "B", bounce, [elemental.ref])
        self.resolve_top(game)
        self.assertIsNone(self.resolve(game))
        self.assertEqual([], self._events(game, source))
        self.assertEqual(red - 1, game.state.players["A"].mana_pool["R"])

        for ordinal in range(1, 5):
            self._activate(game, "A", live_source, {"R": 1}, live_source.ref)
            self.assertIsNone(self.resolve(game))
            self.assertIn("trample", self._keywords(game, live_source))
            self.assertEqual(ordinal, len(self._events(game, source)))
            self.assertEqual(red - 1 - ordinal + (4 if ordinal >= 3 else 0), game.state.players["A"].mana_pool["R"])
        self.replay(game, load=True)

    def test_actual_igniter_excludes_countered_ability_and_preserves_source_through_control_change(self):
        game = self.session(77153004)
        source = self.add(game.engine, IGNITER, seat="B")
        identity = source.logical_object_id
        old_controlled = self.add(game.engine, "Walking Corpse", seat="B", ref="old-control-creature")
        new_controlled = self.add(game.engine, "Walking Corpse", ref="new-control-creature")
        later = self.add(game.engine, "Isamaru, Hound of Konda", zone="hand")
        theft = self.add(game.engine, "Act of Treason", zone="hand")
        stifle = self.add(game.engine, "Stifle", seat="C", zone="hand")
        self.prepare(game, mana={"C": 10, "R": 5, "W": 1})
        game.state.players["B"].mana_pool.update({"C": 4, "R": 2})
        game.state.players["C"].mana_pool["U"] = 1
        self.checkpoint(game)

        self._activate(game, "B", source, {"C": 2, "R": 1})
        self.assertIsNone(self.resolve(game))
        self.assertEqual(3, game.engine._numeric_stat(old_controlled.object_id, "power"))
        countered = self._activate(game, "B", source, {"C": 2, "R": 1})
        self.cast(game, "C", stifle, [countered.ref])
        self.assertIsNone(self.resolve(game))
        self.assertEqual(["B"], [row.actor for row in self._events(game, source)])
        self.assertEqual(3, game.engine._numeric_stat(old_controlled.object_id, "power"))

        self.priority(game, "A")
        theft_action = self.action(game, "A", theft)
        outcome = game.act("pilot:A", {"action_id": theft_action["id"], "targets": [source.ref],
                                       "pay": "manual", "payment": {"C": 2, "R": 1}})
        self.assertTrue(outcome.ok, outcome.summary)
        self.assertIsNone(self.resolve(game))
        live_source = game.state.cards[source.object_id]
        self.assertEqual("A", live_source.controller)
        self.assertEqual(identity, live_source.logical_object_id)

        self._activate(game, "A", live_source, {"C": 2, "R": 1})
        self.assertIsNone(self.resolve(game))
        self.assertEqual(3, game.engine._numeric_stat(new_controlled.object_id, "power"))
        self.assertNotIn("first strike", self._keywords(game, new_controlled))
        self._activate(game, "A", live_source, {"C": 2, "R": 1})
        self.assertIsNone(self.resolve(game))
        self.assertEqual(4, game.engine._numeric_stat(new_controlled.object_id, "power"))
        self.assertIn("first strike", self._keywords(game, new_controlled))
        self.assertIn("first strike", self._keywords(game, live_source))
        self.assertNotIn("first strike", self._keywords(game, old_controlled))
        self.assertEqual(["B", "A", "A"], [row.actor for row in self._events(game, source)])

        self.cast(game, "A", later)
        self.assertIsNone(self.resolve(game))
        self._activate(game, "A", live_source, {"C": 2, "R": 1})
        self.assertIsNone(self.resolve(game))
        self.assertEqual(3, game.engine._numeric_stat(later.object_id, "power"))
        self.assertNotIn("first strike", self._keywords(game, later))
        self.assertEqual(4, len(self._events(game, source)))
        self.replay(game, load=True)

    def test_omitting_third_ability_lowering_restores_only_the_printed_ordinal_residual(self):
        with patch.object(resolution_condition_templates, "third_ability_result_template", return_value=None):
            for name in ORIGINALS:
                card = self.db.lookup(name, fuzzy=False)
                result, _ = register_card(self.db, self.registry, name)
                self.assertFalse(result["strict_capability_ready"], result)
                ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile=PROFILE)
                residual, = ir.material_residuals
                self.assertIn("third time this ability has resolved", residual.text)
            control, _ = register_card(self.db, self.registry, "Sol Ring")
            self.assertTrue(control["strict_capability_ready"], control["blockers"])

    def test_genuine_withdrawals_enforce_history_fixed_mana_and_optional_choice_separately(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests

        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        known = set(discover_tests(ROOT).values())
        for capability, denied in ((HISTORY, set(ORIGINALS)), (MANA, {FLAMEKIN, SEEKER}), (OPTIONAL, {FLAMEKIN})):
            with self.subTest(capability=capability):
                document = deepcopy(raw)
                next(row for row in document["capabilities"] if row["id"] == capability).update(
                    status="blocked", blockers=["Scoped third-resolution authority withdrawal"])
                registry = CapabilityRegistry(document)
                index = build_index(registry_value=document, declaration_source=declarations, discovered_test_ids=known)
                registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
                closure = registry.closure((capability,), profile=PROFILE)
                self.assertIn(f"status:{capability}:blocked", closure.blockers)
                self.assertFalse(any("unverified" in blocker for blocker in closure.blockers))
                for name in ORIGINALS:
                    result, _ = register_card(self.db, registry, name)
                    self.assertEqual(name not in denied, result["strict_capability_ready"], result)
                control, _ = register_card(self.db, registry, "Sol Ring")
                self.assertTrue(control["strict_capability_ready"], control["blockers"])


if __name__ == "__main__":
    unittest.main()
