"""Native evidence for a source definition using the color chosen on entry."""

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
from quorune.characteristic_fragments import CHOSEN_COLOR_DEFINITION_CAPABILITY, CHOSEN_COLOR_DEFINITION_TEMPLATE
from quorune.continuous_effects import Layer
from quorune.deck import DeckDefinition, DeckEntry
from quorune.entry_designations import ENTRY_DESIGNATION_CAPABILITY
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry
from quorune.rules.evidence import validate_capability_evidence_index
from quorune.rules.evidence_storage import read_evidence_document
from scripts.build_test_database import build_fixture_database
from scripts.update_capability_evidence import build_index, discover_tests
from test_controller_creature_keyword_absence import register_card


ORIGINAL = "Chameleon Spirit"


class ChosenColorCharacteristicDefinitionTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def use_database(cls, path):
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Chosen color definition", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"),
            DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "chosen-color-characteristic-definition.sqlite3"
        build_fixture_database(ROOT / "tests/fixtures/chosen-color-characteristic-definition-cards.json", path)
        cls.use_database(path)

    def stats(self, game, original):
        data = game.engine._effective_card_data(game.state.cards[original.object_id])
        return int(data["power"]), int(data["toughness"])

    def color_command(self, game, seat, color):
        decision = game.packet("pilot:" + seat, full=True)["decision"]
        self.assertEqual("replacement.order", decision["kind"])
        options = decision["ctx"]["options"]
        self.assertEqual(set("WUBRG"), {option["id"].rsplit(":", 1)[-1] for option in options})
        choice = next(option["id"] for option in options if option["id"].endswith(":" + color))
        return {"action_id": "choose", "choices": {"replacement": choice}}

    def test_two_actual_sources_keep_independent_choices_and_count_live_opposing_permanents(self):
        game = self.session(31415021)
        game.state.config.semantic_policy = "trusted_only"
        spirit = self.add(game.engine, ORIGINAL, zone="hand")
        second = self.add(game.engine, ORIGINAL, seat="B", zone="hand", ref="second-color-definition")
        secret = self.add(game.engine, "Phyrexian Arena", seat="D", zone="hand", ref="private-arena")
        arena = self.add(game.engine, "Phyrexian Arena", seat="B", ref="phasing-arena")
        own = self.add(game.engine, "Phyrexian Arena", ref="own-arena")
        self.add(game.engine, "Baleful Strix", seat="C")
        self.add(game.engine, "Fervor", seat="D")
        cape = self.add(game.engine, "Clever Concealment", seat="B", zone="hand")
        timing = self.add(game.engine, "Vedalken Orrery", seat="B")
        for card in (spirit, second, cape, timing):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 3, "U": 1})
        game.state.players["B"].mana_pool.update({"C": 5, "U": 1, "W": 2})
        self.checkpoint(game)

        self.assertEqual((0, 0), self.stats(game, spirit))
        self.cast_for(game, "A", spirit)
        self.assertEqual("replacement.order", self.resolve(game).kind)
        self.assertEqual("stack", game.state.cards[spirit.object_id].zone)
        command = self.color_command(game, "A", "B")
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:B", command).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        self.assertNotIn(secret.ref, json.dumps(game.packet("pilot:A", full=True)))
        game = self.replay(game, load=True)
        result = game.act("pilot:A", command)
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("battlefield", game.state.cards[spirit.object_id].zone,
                         "The selected count must keep the original on the battlefield")
        self.assertEqual("B", game.state.cards[spirit.object_id].annotations["chosen_color"])
        self.assertEqual((2, 2), self.stats(game, spirit))

        self.cast_for(game, "B", second, reserve={"C": 2, "W": 2})
        self.assertEqual("replacement.order", self.resolve(game).kind)
        result = game.act("pilot:B", self.color_command(game, "B", "U"))
        self.assertTrue(result.ok, result.summary)
        self.assertEqual((2, 2), self.stats(game, second))
        self.assertEqual((2, 2), self.stats(game, spirit))
        data = game.engine._effective_card_data(game.state.cards[second.object_id], maximum_layer=Layer.COLOR)
        self.assertEqual(["U"], data["colors"])
        self.cast(game, "B", cape, [arena.ref])
        self.assertIsNone(self.resolve(game))
        self.assertTrue(game.state.cards[arena.object_id].phased_out)
        self.assertEqual((1, 1), self.stats(game, spirit))
        self.assertEqual((2, 2), self.stats(game, second))
        self.assertEqual("battlefield", game.state.cards[own.object_id].zone)
        self.replay(game, load=True)

    def test_chosen_color_definition_requires_same_face_choice_and_both_trusted_dependencies(self):
        card = self.db.lookup(ORIGINAL)
        ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile="commander_review")
        self.assertEqual("exact", ir.status)
        definition, = [node for face in ir.faces for node in face.nodes if node.template_id == CHOSEN_COLOR_DEFINITION_TEMPLATE]
        self.assertIn(CHOSEN_COLOR_DEFINITION_CAPABILITY, definition.capability_dependencies)
        self.assertIn(ENTRY_DESIGNATION_CAPABILITY, definition.capability_dependencies)
        for text in (
            card.oracle_text.rsplit("\n", 1)[-1],
            card.oracle_text.replace("your opponents control", "you control"),
            card.oracle_text.replace("power and toughness are each", "power is"),
        ):
            self.assertNotEqual("exact", compile_oracle_card(
                replace(card, oracle_text=text), capability_registry=self.registry, capability_profile="commander_review",
            ).status)
        for dependency in (CHOSEN_COLOR_DEFINITION_CAPABILITY, ENTRY_DESIGNATION_CAPABILITY):
            raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
            next(row for row in raw["capabilities"] if row["id"] == dependency).update(
                status="blocked", blockers=["Withhold chosen-color definition authority"],
            )
            registry = CapabilityRegistry(raw)
            index = build_index(
                registry_value=raw,
                declaration_source=read_evidence_document(ROOT / "platform/capability-evidence-declarations.json"),
                discovered_test_ids=set(discover_tests(ROOT).values()),
            )
            registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
            self.assertNotEqual("exact", compile_oracle_card(
                card, capability_registry=registry, capability_profile="commander_review",
            ).status)

    def test_native_witness_detects_omitted_live_count(self):
        with patch("quorune.rules.live_modifier_quantities.opponent_chosen_color_permanent_count", return_value=0):
            with self.assertRaisesRegex(AssertionError, "The selected count must keep the original on the battlefield"):
                self.test_two_actual_sources_keep_independent_choices_and_count_live_opposing_permanents()

    def test_live_count_is_bound_to_its_all_zone_definition_program(self):
        binding, semantics = register_card(self.db, self.registry, ORIGINAL)
        self.assertTrue(binding["strict_capability_ready"], binding["blockers"])
        card = self.db.lookup(ORIGINAL)
        program, = (item for item in semantics.programs_for_oracle(card.oracle_id)
                    if item.provenance.get("template_id") == CHOSEN_COLOR_DEFINITION_TEMPLATE)
        for changed in (
            replace(program, handlers=()),
            replace(program, active_zone="battlefield"),
            replace(program, provenance={**program.provenance, "template_id": "continuous-query-power-toughness-definition-v1"}),
        ):
            self.assertFalse(bind_semantic_program_runtime(
                changed, capability_registry=self.registry, profile="commander_review",
            )["strict"])
