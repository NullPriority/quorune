"""Native evidence for persistent copying between two announced permanents."""

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.attachments import attach_objects
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.entry_copy import COPIABLE_ORACLE_FIELD
from quorune.face_down_copy import ANONYMOUS_FACE_DOWN_SOURCE
from quorune.morph import MORPH_FACE_DOWN_ANNOTATION, face_down_characteristics
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry
from quorune.rules.evidence import validate_capability_evidence_index
from quorune.rules.evidence_storage import read_evidence_document
from quorune.rules.persistent_public_copy import PERSISTENT_PUBLIC_COPY_CAPABILITY
from quorune.zone_transitions import ZoneTransitionOwner
from scripts.build_test_database import build_fixture_database
from scripts.update_capability_evidence import build_index, discover_tests


class PersistentPublicCopyTests(InheritedNativeFixture, unittest.TestCase):
    copy_spell = "Trial Binding Reflection"
    growth_spell = "Trial Growth Spark"
    copy_producer = "Trial Echoing Ink"
    first_model = "Trial River Guardian"
    second_model = "Trial Oak Guard"
    gear = "Trial Surveyor Harness"
    other_gear = "Trial Outrider Mantle"
    secret = "Trial Sealed Watcher"
    basic = "Generic Bound Plains"

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        database = Path(cls.temporary.name) / "persistent-public-copy.sqlite3"
        fixtures = ROOT / "tests/fixtures"
        build_fixture_database([
            fixtures / "bound-effect-program-cards.json",
            fixtures / "persistent-public-copy-cards.json",
        ], database)
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        commander = "Generic Bound Commander"
        cls.deck = DeckDefinition("Persistent public copy", [
            DeckEntry(commander, 1, "commander"), DeckEntry(cls.basic, 30),
        ], [commander])

    def _begin(self, game, spells, mana):
        for spell in spells:
            self.assert_closed(game, spell)
        self.prepare(game, mana=mana)
        self.checkpoint(game)

    def _command(self, game, spell, targets):
        action = self.action(game, "A", spell)
        return {"action_id": action["id"], "pay": "auto", "targets": [target.ref for target in targets]}

    def _cast(self, game, spell, *targets):
        result = game.act("pilot:A", self._command(game, spell, targets))
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(tuple(target.ref for target in targets), tuple(game.state.stack[-1].targets))

    def _current(self, game, card):
        return game.state.cards[card.object_id]

    def _values(self, game, card):
        return game.engine._effective_card_data(self._current(game, card))

    def test_persistent_copy_retains_recipient_identity_effects_and_the_models_current_copy(self):
        game = self.session(707_810_01)
        engine = game.engine
        first = self.add(engine, self.copy_spell, zone="hand", ref="FIRST-COPY")
        second = self.add(engine, self.copy_spell, zone="hand", ref="FOLLOWUP-COPY")
        growth = self.add(engine, self.growth_spell, zone="hand", ref="RECIPIENT-GROWTH")
        recipient = self.add(engine, self.first_model, ref="KEEP-RECIPIENT")
        model = self.add(engine, self.first_model, seat="B", ref="COPIED-MODEL")
        original_form = self.add(engine, self.second_model, seat="C", ref="PRINTED-FORM")
        land = self.add(engine, self.basic, seat="C", ref="NOT-ARTIFACT-OR-CREATURE")
        hidden = self.add(engine, self.secret, seat="D", zone="hand", ref="PRIVATE-COPY-DECOY")
        recipient.tapped = True
        recipient.counters["+1/+1"] = 1
        model.counters["+1/+1"] = 3
        original_identity = recipient.logical_object_id
        control_since = recipient.acquired_control_turn_count
        printed_power = engine._numeric_stat(original_form.object_id, "power")
        self._begin(game, (first, second, growth), {"C": 8, "U": 4, "G": 1})
        before = authoritative_state_hash(game.state)
        for actor, choices in (("A", (model, model)), ("A", (land, model)),
                               ("A", (recipient, hidden)), ("B", (model, original_form))):
            result = game.act("pilot:" + actor, self._command(game, first, choices))
            self.assertFalse(result.ok, result.summary)
            self.assertEqual(before, authoritative_state_hash(game.state))
        self.assertNotIn(hidden.ref, json.dumps(game.packet("pilot:A", full=True)))

        self._cast(game, first, model, original_form)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(self.second_model, self._values(game, model)["name"])
        self._cast(game, growth, recipient)
        self.assertIsNone(self.resolve(game))
        self._cast(game, second, recipient, model)
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        current = self._current(game, recipient)
        self.assertEqual((original_identity, control_since, "A", True, 1), (
            current.logical_object_id, current.acquired_control_turn_count,
            current.controller, current.tapped, current.counters["+1/+1"],
        ))
        self.assertEqual(self.second_model, self._values(game, recipient)["name"])
        self.assertEqual(printed_power + 4, game.engine._numeric_stat(current.object_id, "power"))
        first_turn = game.state.turn_sequence
        self.until(game, lambda run: run.state.turn_sequence > first_turn)
        self.assertEqual(printed_power + 1, game.engine._numeric_stat(current.object_id, "power"))
        self.assertEqual(self.second_model, self._values(game, recipient)["name"])
        self.replay(game, load=True)

    def test_each_lost_copy_target_cancels_the_result_even_if_its_reference_returns(self):
        for role in ("recipient", "model"):
            with self.subTest(role=role):
                game = self.session(707_810_02)
                spell = self.add(game.engine, self.copy_spell, zone="hand", ref="TWO-ROLES")
                recipient = self.add(game.engine, self.first_model, ref="FIRST-ROLE")
                model = self.add(game.engine, self.second_model, seat="B", ref="SECOND-ROLE")
                self._begin(game, (spell,), {"C": 4, "U": 2})
                self._cast(game, spell, recipient, model)
                lost = recipient if role == "recipient" else model
                old_identity = lost.logical_object_id
                owner = ZoneTransitionOwner(game.engine)
                owner.move_card(lost.object_id, "exile")
                owner.move_card(lost.object_id, "battlefield")
                self.assertNotEqual(old_identity, lost.logical_object_id)
                self.checkpoint(game)
                game = self.replay(game, load=True)
                self.assertIsNone(self.resolve(game))
                self.assertEqual(self.first_model, self._values(game, recipient)["name"])
                self.assertNotIn("copy_overrides", self._current(game, recipient).annotations)
                self.assertEqual("graveyard", self._current(game, spell).zone)
                self.replay(game, load=True)

    def test_persistent_copy_keeps_an_equipment_attachment_until_the_recipient_is_ineligible(self):
        game = self.session(707_810_03)
        engine = game.engine
        first = self.add(engine, self.copy_spell, zone="hand", ref="EQUIPMENT-COPY")
        second = self.add(engine, self.copy_spell, zone="hand", ref="CREATURE-COPY")
        recipient = self.add(engine, self.gear, ref="FITTED-EQUIPMENT")
        equipped = self.add(engine, self.first_model, ref="EQUIPPED")
        equipment_model = self.add(engine, self.other_gear, seat="B", ref="EQUIPMENT-MODEL")
        creature_model = self.add(engine, self.second_model, seat="C", ref="CREATURE-MODEL")
        attach_objects(engine.state.cards, recipient, equipped,
                       source_timestamp=ZoneTransitionOwner(engine).next_timestamp(), players=engine.state.players)
        self._begin(game, (first, second), {"C": 8, "U": 4})
        self._cast(game, first, recipient, equipment_model)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(self.other_gear, self._values(game, recipient)["name"])
        self.assertEqual(equipped.object_id, self._current(game, recipient).attached_to)
        self._cast(game, second, recipient, creature_model)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(self.second_model, self._values(game, recipient)["name"])
        self.assertIsNone(self._current(game, recipient).attached_to)
        self.assertNotIn(recipient.object_id, self._current(game, equipped).attachments)
        self.replay(game, load=True)

    def test_persistent_copy_of_hidden_model_uses_only_anonymous_layer_one_values(self):
        game = self.session(707_810_04)
        engine = game.engine
        spell = self.add(engine, self.copy_spell, zone="hand", ref="SEALED-COPY")
        recipient = self.add(engine, self.first_model, ref="FACE-UP-RECIPIENT")
        model = self.add(engine, self.secret, seat="B", ref="FACE-DOWN-MODEL")
        model.face_down = True
        model.known_to = ["B"]
        model.revealed_to = []
        model.annotations[MORPH_FACE_DOWN_ANNOTATION] = face_down_characteristics("morph")
        self._begin(game, (spell,), {"C": 4, "U": 2})
        self._cast(game, spell, recipient, model)
        self.assertIsNone(self.resolve(game))
        current = self._current(game, recipient)
        values = self._values(game, recipient)
        self.assertFalse(current.face_down)
        self.assertEqual(ANONYMOUS_FACE_DOWN_SOURCE, current.annotations["copy_overrides"][COPIABLE_ORACLE_FIELD])
        self.assertEqual(("", "2", "2", []), (
            current.annotations["copy_overrides"]["name"], str(values["power"]), str(values["toughness"]), values["colors"],
        ))
        packet = json.dumps(game.packet("pilot:D", full=True))
        self.assertNotIn(model.oracle_id, packet)
        self.assertNotIn(self.secret, packet)
        self.replay(game, load=True)

    def test_spell_copy_can_choose_a_distinct_recipient_and_model_without_retargeting_original(self):
        game = self.session(707_810_05)
        engine = game.engine
        spell = self.add(engine, self.copy_spell, zone="hand", ref="ORIGINAL-COPY-SPELL")
        copier = self.add(engine, self.copy_producer, seat="B", zone="hand", ref="COPY-PRODUCER")
        first = self.add(engine, self.first_model, ref="ORIGINAL-RECIPIENT")
        first_model = self.add(engine, self.second_model, seat="B", ref="ORIGINAL-MODEL")
        second = self.add(engine, self.second_model, seat="C", ref="COPY-RECIPIENT")
        second_model = self.add(engine, self.first_model, seat="D", ref="COPY-MODEL")
        game.state.players["B"].mana_pool["R"] = 2
        self._begin(game, (spell, copier), {"C": 4, "U": 2})
        self._cast(game, spell, first, first_model)
        original = game.state.stack[-1]
        self.cast(game, "B", copier, [original.ref])
        pending = self.resolve(game)
        self.assertEqual(("semantic.choice", ["B"]), (pending.kind, pending.actors))
        game = self.replay(game, load=True)
        choose = {"action_id": "choose", "targets": [second.ref, second_model.ref]}
        result = game.act("pilot:B", choose)
        self.assertTrue(result.ok, result.summary)
        copied, = (item for item in game.state.stack if item.kind == "spell_copy")
        self.assertEqual([second.ref, second_model.ref], copied.targets)
        original, = (item for item in game.state.stack if item.ref == original.ref)
        self.assertEqual([first.ref, first_model.ref], original.targets)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(self.second_model, self._values(game, first)["name"])
        self.assertEqual(self.first_model, self._values(game, second)["name"])
        self.replay(game, load=True)

    def test_persistent_public_copy_requires_the_complete_compiler_clause_and_authority(self):
        card = self.db.lookup(self.copy_spell)
        def compiled(record, registry):
            return compile_oracle_card(record, capability_registry=registry, capability_profile="commander_review")

        exact = compiled(card, self.registry)
        self.assertEqual("exact", exact.status, exact.material_residuals)
        node, = exact.faces[0].nodes
        self.assertEqual("persistent-public-copy-v1", node.template_id)
        self.assertTrue(node.target_schema["globally_distinct"])
        for changed in (card.oracle_text.replace("another target","target"), card.oracle_text+" Draw a card."):
            self.assertNotEqual("exact", compiled(replace(card, oracle_text=changed), self.registry).status)
        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        row = next(row for row in raw["capabilities"] if row["id"] == PERSISTENT_PUBLIC_COPY_CAPABILITY)
        row.update(status="blocked", blockers=["Persistent public copy authority withdrawn."])
        registry = CapabilityRegistry(raw)
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        index = build_index(registry_value=raw, declaration_source=declarations,
                            discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
        self.assertNotEqual("exact", compiled(card, registry).status)
        with patch("quorune.compiler.persistent_public_copy.persistent_public_copy_template", return_value=None):
            self.assertNotEqual("exact", compiled(card, self.registry).status)
