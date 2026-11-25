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
from quorune.carddb import CardDatabase
from quorune.compiler import graveyard_return_choice_templates
from quorune.deck import DeckDefinition, DeckEntry
from quorune.object_query import ObjectQueryResult
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.replacement.immutable import thaw_value
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.graveyard_return_choices import (
    CAPABILITY, GREATEST_POWER_CAPABILITY, OPERATION, GraveyardReturnSelection,
)
from quorune.semantic_choices.context import SemanticChoiceContext
from quorune.semantic_choices.graveyard_return import GraveyardReturnChoiceHandler
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


PROFILE = "commander_review"
FIXTURES = ROOT / "tests" / "fixtures"
ORIGINAL = "Desecrator Hag"
CDA_WITNESS = "Boneyard Wurm"
OPTIONAL_RETURN = "Grapple with the Past"


class GreatestPowerGraveyardReturnTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        folder = Path(cls.temporary.name)
        support = {
            "ability-source-counter-cards.json": {"Shock", "Sol Ring"},
            "additional-continuous-carriers-cards.json": {"Walking Corpse"},
            "bound-effect-program-cards.json": {"Generic Bound Plains"},
            "counter-replacement-cards.json": {"Ancient Brontodon"},
            "graveyard-salvage-cards.json": {OPTIONAL_RETURN},
            "greatest-power-graveyard-return-cards.json": {ORIGINAL, CDA_WITNESS},
            "public-zone-quantity-cards.json": {"Mountain"},
            "regeneration-cards.json": {"Drudge Skeletons"},
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
        path = folder / "greatest-power-graveyard-return.sqlite3"
        build_fixture_database(subsets, path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Greatest-power graveyard return", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"),
            DeckEntry("Generic Bound Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def _original(self):
        card = self.db.lookup(ORIGINAL, fuzzy=False)
        result, semantics = register_card(self.db, self.registry, ORIGINAL)
        self.assertTrue(result["strict_capability_ready"], result["blockers"])
        trigger, = [program for program in semantics.programs_for_oracle(card.oracle_id)
                    if program.event == "permanent.enter.self"]
        return card, trigger

    def test_complete_hag_registers_its_exact_mandatory_nontargeted_return(self):
        card, trigger = self._original()
        ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile=PROFILE)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        self.assertIsNone(trigger.target_schema)
        effect, = thaw_value(trigger.effects)
        self.assertEqual({"op": OPERATION, "player": "$controller",
                          "selection": {"schema_version": 2, "types_any": ["creature"]}}, effect)
        self.assertLessEqual({CAPABILITY, GREATEST_POWER_CAPABILITY, "trigger.event.normalized_zone_change"},
                             set(trigger.capability_dependencies))
        self.assertIn("card.return.own_graveyard_to_owner_hand", trigger.capability_closure["reachable"])

    def test_graveyard_maximum_codec_preserves_v1_and_rejects_unrelated_shapes(self):
        old = {"schema_version": 1, "types_any": ["creature", "land"]}
        selection = GraveyardReturnSelection.from_dict(old)
        self.assertEqual(old, selection.to_dict())
        self.assertFalse(selection.greatest_power)
        maximum = {"schema_version": 2, "types_any": ["creature"]}
        self.assertEqual(maximum, GraveyardReturnSelection.from_dict(maximum).to_dict())
        for changes in ({"schema_version": True}, {"schema_version": 2.0},
                        {"types_any": ["land"]}, {"types_any": ["creature", "land"]}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                GraveyardReturnSelection.from_dict({**maximum, **changes})
        card, _ = self._original()
        for before, after in (("the creature card", "the land card"),
                              ("the greatest power", "the least power"),
                              ("choose one of them", "may choose one of them")):
            changed = replace(card, oracle_text=card.oracle_text.replace(before, after))
            ir = compile_oracle_card(changed, capability_registry=self.registry, capability_profile=PROFILE)
            self.assertTrue(ir.material_residuals, ir)

    def test_greatest_power_comparison_keeps_signed_values_and_applies_the_undefined_zero_rule(self):
        negative = ObjectQueryResult("minus", "minus-ref", "Negative", "A", "A", "graveyard",
                                     types=("creature",), effective_power=-4, logical_object_id="minus@2")
        undefined = replace(negative, object_id="undefined", ref="undefined-ref",
                            effective_power=None, logical_object_id="undefined@3")
        lower = replace(negative, object_id="lower", ref="lower-ref", effective_power=-6, logical_object_id="lower@1")
        handler = GraveyardReturnChoiceHandler()
        effect = {"op": OPERATION, "player": "A", "selection": {"schema_version": 2, "types_any": ["creature"]}}
        for rows, expected in (((negative, lower), negative), ((negative, undefined), undefined)):
            with self.subTest(expected=expected.ref):
                indexed = {row.ref: row for row in rows}
                query = SimpleNamespace(choice_candidate_refs=lambda: tuple(indexed),
                                        object=lambda ref, zones: indexed.get(ref))
                context = SemanticChoiceContext("A", "stack:graveyard", "A", "Graveyard return",
                                                None, None, "greatest-graveyard", 1, query)
                result = handler.prepare(effect, context)
                self.assertIsNone(result.request)
                intent, = result.preparation_intents
                self.assertEqual(expected.ref, intent.object_ref)
                self.assertEqual(expected.object_id, intent.target_snapshot["object_id"])

    def _queue_entry(self, game, source):
        self.assert_closed(game, source)
        self.prepare(game, mana={"C": 2, "G": 2})
        self.checkpoint(game)
        self.cast(game, "A", source)
        self.resolve_top(game)
        self.assertEqual(1, self.trigger_count(game, source))
        self.assertEqual([], game.state.stack[-1].targets)

    def _zone(self, game, card):
        return game.state.cards[card.object_id].zone

    def _reject(self, game, seat, command):
        before = authoritative_state_hash(game.state)
        result = game.act("pilot:" + seat, command)
        self.assertFalse(result.ok, result.summary)
        self.assertEqual(before, authoritative_state_hash(game.state))

    def test_actual_hag_returns_the_live_cda_maximum_automatically_after_a_response(self):
        game = self.session(77141001)
        source = self.add(game.engine, ORIGINAL, zone="hand")
        wurm = self.add(game.engine, CDA_WITNESS, zone="graveyard")
        tie = self.add(game.engine, "Walking Corpse", zone="graveyard")
        body = self.add(game.engine, "Walking Corpse", ref="graveyard-max-pending-death")
        foreign = self.add(game.engine, "Ancient Brontodon", seat="B", zone="graveyard")
        shock = self.add(game.engine, "Shock", seat="B", zone="hand")
        self.add(game.engine, "Mountain", seat="B")
        for card in (wurm, tie, body, foreign, shock):
            self.assert_closed(game, card)
        self._queue_entry(game, source)
        self.assertEqual(2, game.engine._numeric_stat(wurm.object_id, "power"))
        self.cast(game, "B", shock, [body.ref])
        self.resolve_top(game)
        self.assertEqual("graveyard", self._zone(game, body))
        self.assertEqual(3, game.engine._numeric_stat(wurm.object_id, "power"))
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("hand", self._zone(game, wurm))
        self.assertEqual({"graveyard"}, {self._zone(game, card) for card in (tie, body, foreign)})
        self.replay(game, load=True)

    def test_actual_hag_requires_one_tied_current_creature_from_the_owners_graveyard(self):
        game = self.session(77141010)
        source = self.add(game.engine, ORIGINAL, zone="hand")
        first = self.add(game.engine, "Walking Corpse", zone="graveyard")
        tied = self.add(game.engine, "Walking Corpse", zone="graveyard", ref="graveyard-max-tied")
        lower = self.add(game.engine, "Drudge Skeletons", zone="graveyard")
        artifact = self.add(game.engine, "Sol Ring", zone="graveyard")
        other = self.add(game.engine, "Ancient Brontodon", seat="B", zone="graveyard")
        hidden = self.add(game.engine, "Sol Ring", seat="C", zone="hand", ref="graveyard-max-private-c")
        for card in (first, tied, lower, artifact, other):
            self.assert_closed(game, card)
        self._queue_entry(game, source)
        result = self.resolve(game)
        self.assertEqual(("semantic.choice", ["A"]), (result.kind, result.actors))
        ctx = game.packet("pilot:A", full=True)["decision"]["ctx"]
        offer, = ctx["legal_actions"]
        self.assertEqual({first.ref, tied.ref}, set(offer["choice_schema"]["legal_refs"]))
        self.assertEqual({first.ref, tied.ref}, {row["id"] for row in ctx["objects"]})
        self.assertNotIn(hidden.ref, json.dumps(game.packet("pilot:A", full=True)))
        command = {"action_id": "choose", "card": tied.ref}
        self._reject(game, "B", command)
        for invalid in (None, lower.ref, artifact.ref, other.ref):
            self._reject(game, "A", {"action_id": "choose", "card": invalid})
        game = self.replay(game, load=True)
        accepted = game.act("pilot:A", command)
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("hand", self._zone(game, tied))
        self.assertEqual({"graveyard"}, {self._zone(game, card) for card in (first, lower, artifact, other)})
        self.replay(game, load=True)

    def test_actual_hag_empty_own_creature_card_query_completes_with_no_choice(self):
        game = self.session(77141020)
        source = self.add(game.engine, ORIGINAL, zone="hand")
        artifact = self.add(game.engine, "Sol Ring", zone="graveyard")
        foreign = self.add(game.engine, "Ancient Brontodon", seat="B", zone="graveyard")
        self._queue_entry(game, source)
        self.assertIsNone(self.resolve(game))
        self.assertEqual({"graveyard"}, {self._zone(game, card) for card in (artifact, foreign)})
        self.replay(game, load=True)

    def test_omitting_the_maximum_return_lowering_keeps_the_original_partial(self):
        with patch.object(graveyard_return_choice_templates, "_greatest_power_return_choice", return_value=None):
            result, _ = register_card(self.db, self.registry, ORIGINAL)
            self.assertFalse(result["strict_capability_ready"], result)
            retained, _ = register_card(self.db, self.registry, OPTIONAL_RETURN)
            self.assertTrue(retained["strict_capability_ready"], retained["blockers"])

    def test_genuine_graveyard_maximum_and_return_withdrawals_block_their_exact_programs(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests

        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        declared = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        known = set(discover_tests(ROOT).values())
        for capability in (CAPABILITY, GREATEST_POWER_CAPABILITY):
            with self.subTest(capability=capability):
                document = deepcopy(raw)
                next(row for row in document["capabilities"] if row["id"] == capability).update(
                    status="blocked", blockers=["Scoped mandatory graveyard maximum return authority withdrawal"])
                registry = CapabilityRegistry(document)
                index = build_index(registry_value=document, declaration_source=declared, discovered_test_ids=known)
                registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
                closure = registry.closure((capability,), profile=PROFILE)
                self.assertIn("status:" + capability + ":blocked", closure.blockers)
                self.assertFalse(any("unverified" in blocker for blocker in closure.blockers))
                original, _ = register_card(self.db, registry, ORIGINAL)
                self.assertFalse(original["strict_capability_ready"], original)
                retained, _ = register_card(self.db, registry, OPTIONAL_RETURN)
                self.assertEqual(capability != CAPABILITY, retained["strict_capability_ready"], retained)
                control, _ = register_card(self.db, registry, "Sol Ring")
                self.assertTrue(control["strict_capability_ready"], control["blockers"])


if __name__ == "__main__":
    unittest.main()
