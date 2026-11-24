"""Native retained-color entry, triggered targeting, and copy history evidence."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
import test_vanishing_rules as upkeep_witness
from quorune.ability_fragments import canonical_ability_fragments, granted_triggered_specs
from quorune.carddb import CardDatabase
from quorune.rules.copied_flip_faces import COPIED_FLIP_FACES, FLIPPED_STATUS
from quorune.deck import DeckDefinition, DeckEntry
from quorune.entry_copy import COPIABLE_FACE_FIELD, COPIABLE_ORACLE_FIELD
from quorune.morph import MORPH_FACE_DOWN_ANNOTATION, face_down_characteristics
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry
from quorune.rules.color_retaining_copy import RETAINED_COLOR_COPY_CAPABILITY
from quorune.rules.evidence import validate_capability_evidence_index
from quorune.rules.evidence_storage import read_evidence_document
from scripts.build_test_database import build_fixture_database
from scripts.update_capability_evidence import build_index, discover_tests


class RetainedColorCopyTests(InheritedNativeFixture, unittest.TestCase):
    source_name = "Trial Color Weaver"
    first = "Trial Ember Form"
    second = "Trial Moss Form"
    hidden_model = "Trial Hidden Model"
    flip_normal = "Trial Sprout Keeper"
    flip_alternative = "Trial Grove Elder"

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "retained-color-copy.sqlite3"
        fixtures = ROOT / "tests/fixtures"
        build_fixture_database([
            fixtures / "bound-effect-program-cards.json",
            fixtures / "retained-color-copy-cards.json",
            fixtures / "copiable-entry-ability-grant-cards.json",
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = "Generic Bound Commander"
        cls.deck = DeckDefinition("Retained color copy", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], [commander])

    def _entry(self, game, model=None, *, source_name=None):
        source = self.add(game.engine, source_name or self.source_name, zone="hand", ref="COLOR-COPY-SOURCE")
        self.assert_closed(game, source)
        self.prepare(game, mana={"C": 3, "U": 2})
        self.checkpoint(game)
        self.cast(game, "A", source)
        pending = self.resolve(game)
        self.assertEqual(("replacement.order", ["A"]), (pending.kind, pending.actors))
        context = game.packet("pilot:A", full=True)["decision"]["ctx"]
        rows = context["options"]
        option, = [row for row in rows if (bool(row.get("decline")) if model is None
                    else not row.get("decline") and row["id"].endswith(":" + model.ref))]
        before = authoritative_state_hash(game.state)
        command = {"action_id": "choose", "replacement": option["id"]}
        foreign = game.act("pilot:C", command)
        self.assertFalse(foreign.ok, foreign.summary)
        self.assertEqual(before, authoritative_state_hash(game.state))
        response = game.act("pilot:A", command)
        self.assertTrue(response.ok, response.summary)
        self.assertIsNone(self.resolve(game))
        return game.state.cards[source.object_id]

    def _upkeep(self, game, seat="A"):
        upkeep_witness.VanishingRuntimeTests.upkeep(self, game, seat=seat)
        self.checkpoint(game)
        return self.resolve(game)

    def _select_target(self, game, target):
        pending = game.state.pending_decision
        self.assertEqual(["A"], pending.actors)
        supplied = game.act("pilot:A", {"action_id": "choose", "targets": [target.ref]})
        self.assertTrue(supplied.ok, supplied.summary)
        pending = self.resolve(game)
        self.assertEqual(["A"], pending.actors)
        context = game.packet("pilot:A", full=True)["decision"]["ctx"]
        self.assertEqual("offer_optional_effect", context["operation"])

    def _copy_or_decline(self, game, choice):
        supplied = game.act("pilot:A", {"action_id": "choose", "choice": choice})
        self.assertTrue(supplied.ok, supplied.summary)
        self.assertIsNone(self.resolve(game))

    def _grants(self, game, card):
        raw = game.engine._effective_card_data(card)["ability_fragments"]
        return tuple(grant for grant in granted_triggered_specs(canonical_ability_fragments(raw))
                     if grant.event == "step.begin")

    def test_entry_retains_its_color_and_upkeep_targeting_precedes_the_copy_decision(self):
        game = self.session(707_910_11)
        model = self.add(game.engine, self.first, seat="B", ref="RED-ENTRY-MODEL")
        later = self.add(game.engine, self.second, seat="C", ref="GREEN-UPKEEP-MODEL")
        self.add(game.engine, self.hidden_model, seat="D", zone="hand", ref="HIDDEN-UPKEEP-REF")
        heading = self._entry(game, model, source_name="Vesuvan Doppelganger")
        original = heading.logical_object_id
        self.assertEqual(self.first, game.engine.display_name(heading.object_id))
        self.assertEqual(["U"], game.engine._effective_card_data(heading)["colors"])
        self.assertEqual(1, len(self._grants(game, heading)))
        game = self.replay(game, load=True)
        self.assertIsNone(self._upkeep(game, seat="B"))
        self.assertIsNotNone(self._upkeep(game))
        packet = json.dumps(game.packet("pilot:A", full=True)["decision"])
        self.assertIn(later.ref, packet)
        self.assertNotIn("HIDDEN-UPKEEP-REF", packet)
        self._select_target(game, later)
        game = self.replay(game, load=True)
        self._copy_or_decline(game, "decline")
        self.assertEqual(self.first, game.engine.display_name(heading.object_id))
        self.replay(game, load=True)

        self.assertIsNotNone(self._upkeep(game))
        self._select_target(game, later)
        copied = game.state.cards[heading.object_id]
        copied.counters["+1/+1"] = 1
        copied.tapped = True
        self.checkpoint(game)
        game = self.replay(game, load=True)
        self._copy_or_decline(game, "apply")
        current = game.state.cards[heading.object_id]
        self.assertEqual((original, self.second, ["U"], True, 1), (
            current.logical_object_id, game.engine.display_name(current.object_id),
            game.engine._effective_card_data(current)["colors"], current.tapped, current.counters["+1/+1"]))
        self.assertEqual(1, len(self._grants(game, current)))
        self.replay(game, load=True)

    def test_face_down_entry_model_supplies_anonymous_values_without_its_program(self):
        game = self.session(707_910_12)
        hidden = self.add(game.engine, self.hidden_model, seat="C", ref="ANONYMOUS-CONTROL")
        secret = hidden.oracle_id
        hidden.face_down = True
        hidden.known_to = ["C"]
        hidden.revealed_to = []
        hidden.annotations[MORPH_FACE_DOWN_ANNOTATION] = face_down_characteristics("morph")
        current = self._entry(game, hidden)
        values = game.engine._effective_card_data(current)
        self.assertFalse(current.face_down)
        self.assertEqual((["U"], "2", "2"), (values["colors"], str(values["power"]), str(values["toughness"])))
        self.assertEqual("custom-copy:face-down", current.annotations["copy_overrides"][COPIABLE_ORACLE_FIELD])
        self.assertEqual(1, len(self._grants(game, current)))
        self.assertNotIn(secret, json.dumps(game.packet("pilot:D", full=True)))
        self.assertNotIn(self.hidden_model, json.dumps(game.packet("pilot:D", full=True)))
        self.replay(game, load=True)

    def test_declining_entry_does_not_gain_the_upkeep_ability(self):
        game = self.session(707_910_13)
        self.add(game.engine, self.first, seat="C", ref="UNUSED-MODEL")
        source = self._entry(game)
        self.assertEqual("graveyard", source.zone)
        self.assertNotIn("copy_overrides", source.annotations)
        self.replay(game, load=True)

    def test_self_copy_adds_an_independently_targeted_upkeep_trigger(self):
        game = self.session(707_910_14)
        first = self.add(game.engine, self.first, seat="B", ref="FIRST-ORDERED-MODEL")
        second = self.add(game.engine, self.second, seat="C", ref="SECOND-ORDERED-MODEL")
        source = self._entry(game, first)
        self.assertIsNotNone(self._upkeep(game))
        self._select_target(game, source)
        self._copy_or_decline(game, "apply")
        source = game.state.cards[source.object_id]
        self.assertEqual(2, len(self._grants(game, source)))
        game = self.replay(game, load=True)

        pending = self._upkeep(game)
        self.assertEqual(("trigger.order", ["A"]), (pending.kind, pending.actors))
        low, high = reversed(pending.continuation["trigger_refs"])
        ordered = game.act("pilot:A", {"action_id": "order", "triggers": [low, high]})
        self.assertTrue(ordered.ok, ordered.summary)
        for target in (first, second):
            pending = game.state.pending_decision
            self.assertEqual(("semantic.target", ["A"]), (pending.kind, pending.actors))
            response = game.act("pilot:A", {"action_id": "choose", "targets": [target.ref]})
            self.assertTrue(response.ok, response.summary)
        self.assertEqual([low, high], [item.ref for item in game.state.stack])
        self.assertEqual({first.ref, second.ref}, {item.targets[0] for item in game.state.stack})
        top_target, bottom_target = [item.targets[0] for item in reversed(game.state.stack)]
        self.assertNotEqual(top_target, bottom_target)
        self.assertIsNotNone(self.resolve(game))
        game = self.replay(game, load=True)
        response = game.act("pilot:A", {"action_id": "choose", "choice": "apply"})
        self.assertTrue(response.ok, response.summary)
        self.assertEqual(self.first if top_target == first.ref else self.second,
                         game.engine.display_name(source.object_id))
        self.assertIsNotNone(self.resolve(game))
        self._copy_or_decline(game, "apply")
        self.assertEqual(self.first if bottom_target == first.ref else self.second,
                         game.engine.display_name(source.object_id))
        self.replay(game, load=True)

    def test_lost_upkeep_target_fails_before_the_copy_offer(self):
        game = self.session(707_910_15)
        first = self.add(game.engine, self.first, seat="B", ref="UNCHANGED-MODEL")
        second = self.add(game.engine, self.second, seat="C", ref="DEPARTING-MODEL")
        source = self._entry(game, first)
        self.assertIsNotNone(self._upkeep(game))
        target = game.act("pilot:A", {"action_id": "choose", "targets": [second.ref]})
        self.assertTrue(target.ok, target.summary)
        game.engine.move_card(second.object_id, "graveyard")
        self.checkpoint(game)
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(self.first, game.engine.display_name(source.object_id))
        self.replay(game, load=True)

    def test_flip_copy_uses_receiving_status_and_retains_both_printed_faces(self):
        game = self.session(707_910_16)
        model = self.add(game.engine, self.flip_normal, seat="B", ref="FLIPPED-MODEL")
        model.active_face = self.flip_alternative
        source = self._entry(game, model)
        original = source.logical_object_id
        copies = source.annotations["copy_overrides"][COPIED_FLIP_FACES]
        for face, name, stats in (("normal", self.flip_normal, ("2", "1")),
                                  ("alternative", self.flip_alternative, ("3", "3"))):
            values = copies[face]
            self.assertEqual((name, ["U"], "{1}{G}", *stats), (
                values["name"], values["colors"], values["mana_cost"],
                str(values["power"]), str(values["toughness"]),
            ))
        current = game.engine._effective_card_data(source)
        self.assertEqual((self.flip_normal, self.flip_normal),
                         (current["name"], current[COPIABLE_FACE_FIELD]))
        self.assertEqual(1, len(self._grants(game, source)))

        # Seed the receiver's own status to exercise layer-one projection.
        source.annotations[FLIPPED_STATUS] = True
        self.checkpoint(game)
        game = self.replay(game, load=True)
        source = game.state.cards[source.object_id]
        current = game.engine._effective_card_data(source)
        self.assertEqual((self.flip_alternative, self.flip_alternative, ["U"], "{1}{G}"),
                         (current["name"], current[COPIABLE_FACE_FIELD], current["colors"], current["mana_cost"]))
        self.assertIsNotNone(self._upkeep(game))
        self._select_target(game, source)
        self._copy_or_decline(game, "apply")
        current = game.state.cards[source.object_id]
        self.assertEqual((original, self.flip_alternative), (
            current.logical_object_id, game.engine.display_name(current.object_id)))
        self.assertEqual(2, len(self._grants(game, current)))
        self.assertEqual(self.flip_normal, current.annotations["copy_overrides"][COPIED_FLIP_FACES]["normal"]["name"])
        self.replay(game, load=True)

    def test_retained_color_copy_authority_withdrawal_reopens_the_full_printed_parent(self):
        card = self.db.lookup(self.source_name)
        def compile_card(record, registry):
            return compile_oracle_card(record, capability_registry=registry, capability_profile="commander_review")
        exact = compile_card(card, self.registry)
        self.assertEqual("exact", exact.status, exact.material_residuals)
        parent, quoted = exact.faces[0].nodes
        self.assertEqual("intrinsic-entry-copy-v14", parent.template_id)
        self.assertEqual("step.begin", quoted.event)
        self.assertEqual(1, quoted.target_schema["count"])
        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        cap = next(row for row in raw["capabilities"] if row["id"] == RETAINED_COLOR_COPY_CAPABILITY)
        cap.update(status="blocked", blockers=["Retained color copy authority withdrawn."])
        withdrawn = CapabilityRegistry(raw)
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        index = build_index(registry_value=raw, declaration_source=declarations,
                            discovered_test_ids=set(discover_tests(ROOT).values()))
        withdrawn.mark_evidence_verified(validate_capability_evidence_index(index, registry=withdrawn))
        self.assertNotEqual("exact", compile_card(card, withdrawn).status)
        with patch("quorune.compiler.color_retaining_copy.retained_color_copy_nodes", return_value=None):
            self.assertNotEqual("exact", compile_card(card, self.registry).status)
