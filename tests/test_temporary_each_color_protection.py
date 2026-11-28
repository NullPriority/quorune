"""Actual normal and paid Overload protection through public targeting and replay."""

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.ability_fragments import canonical_ability_fragments, protection_specs
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.temporary_ability_grant import TEMPORARY_EACH_COLOR_PROTECTION_CAPABILITY as CAPABILITY
from scripts.build_test_database import build_fixture_database


class TemporaryEachColorProtectionTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "temporary-each-color-protection.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/temporary-each-color-protection-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("Temporary all-color protection", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Plains", 30),
        ], [commander])

    def compile(self, card):
        return compile_oracle_card(card, capability_registry=self.registry, capability_profile="commander_review")

    def test_exact_correlated_bodies_and_closed_five_color_shape(self):
        record = self.db.lookup("Eldritch Immunity", fuzzy=False)
        ir = self.compile(record)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        normal, = (node for node in ir.faces[0].nodes if node.target_schema)
        paid, = (node for node in ir.faces[0].nodes if node.effects and node.target_schema is None)
        self.assertEqual("you", normal.target_schema["controller_relation"])
        self.assertNotIn("colors_any", normal.target_schema)
        self.assertNotIn("colors_none", normal.target_schema)
        for node in (normal, paid):
            self.assertIn(CAPABILITY, node.capability_dependencies)
            grants = canonical_ability_fragments(node.effects[0]["ability_fragments"])
            self.assertEqual(set("WUBRG"), {fragment.quality for fragment in protection_specs(grants)})
        altered = self.compile(replace(record, oracle_text=record.oracle_text.replace("creature you control", "colorless creature you control")))
        self.assertNotEqual("exact", altered.status)
        game = self.session(747041000)
        source = self.add(game.engine, record.name, zone="hand")
        self.assert_closed(game, source)
        program, = (row for row in game.engine.semantics.programs_for_oracle(source.oracle_id) if row.target_schema)
        effect, = program.effects
        mutant = replace(program, effects=({**effect, "ability_fragments": list(effect["ability_fragments"][:-1])},))
        self.assertFalse(bind_semantic_program_runtime(mutant, capability_registry=self.registry,
                                                      profile="commander_review")["strict"])

    def protections(self, game, permanent):
        data = game.engine._effective_card_data(game.state.cards[permanent.object_id])
        return {item.quality for item in protection_specs(canonical_ability_fragments(data.get("ability_fragments", ())))}

    def cast_choice(self, game, source, choice, targets=(), accepted=True):
        self.priority(game, "A")
        before = authoritative_state_hash(game.state)
        result = game.act("pilot:A", {"action_id": f"cast:{source.ref}", "cost_option": choice,
                                      "targets": list(targets), "pay": "auto"})
        self.assertEqual(accepted, result.ok, result.summary)
        if not accepted:
            self.assertEqual(before, authoritative_state_hash(game.state))

    def test_actual_normal_protects_a_colored_target_from_color_but_allows_colorless(self):
        game = self.session(747041001)
        source = self.add(game.engine, "Eldritch Immunity", zone="hand")
        owned = self.add(game.engine, "Grizzly Bears", ref="protected-green-creature")
        other = self.add(game.engine, "Ornithopter")
        rival = self.add(game.engine, "Grizzly Bears", seat="C", ref="opposing-creature")
        bolt = self.add(game.engine, "Lightning Bolt", seat="B", zone="hand")
        spatial = self.add(game.engine, "Spatial Contortion", seat="B", zone="hand")
        self.add(game.engine, "Mountain", seat="B")
        for i in range(2):
            self.add(game.engine, "Wastes", seat="B", ref=f"spatial-source-{i}")
        for card in (source, bolt, spatial):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 1})
        self.checkpoint(game)
        self.cast_choice(game, source, "normal", [rival.ref], accepted=False)
        self.cast_choice(game, source, "normal", [owned.ref])
        game = self.replay(game, load=True)
        self.resolve_top(game)
        self.assertEqual(set("WUBRG"), self.protections(game, owned))
        self.assertFalse(self.protections(game, other))
        self.assertFalse(self.protections(game, rival))
        self.priority(game, "B")
        before = authoritative_state_hash(game.state)
        denied = game.act("pilot:B", {"action_id": f"cast:{bolt.ref}", "targets": [owned.ref], "pay": "auto"})
        self.assertFalse(denied.ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        self.cast_for(game, "B", spatial, [owned.ref])
        self.resolve_top(game)
        self.assertEqual("graveyard", game.state.cards[owned.object_id].zone)
        self.replay(game, load=True)

    def test_actual_paid_overload_grants_to_controlled_creatures_at_resolution(self):
        game = self.session(747041002)
        source = self.add(game.engine, "Eldritch Immunity", zone="hand")
        colored = self.add(game.engine, "Grizzly Bears")
        colorless = self.add(game.engine, "Ornithopter", ref="original-colorless")
        rival = self.add(game.engine, "Grizzly Bears", seat="B", ref="opposing-colored")
        late = self.add(game.engine, "Ornithopter", zone="hand", ref="late-colorless")
        self.assert_closed(game, source)
        self.prepare(game, mana={"C": 5})
        self.checkpoint(game)
        self.cast_choice(game, source, "overload", [colored.ref], accepted=False)
        self.cast_choice(game, source, "overload")
        self.assertFalse(game.state.stack[-1].targets)
        self.assertEqual(0, sum(game.state.players["A"].mana_pool.values()))
        game = self.replay(game, load=True)
        self.resolve_top(game)
        self.assertEqual(set("WUBRG"), self.protections(game, colored))
        self.assertEqual(set("WUBRG"), self.protections(game, colorless))
        self.assertFalse(self.protections(game, rival))
        self.cast_for(game, "A", late)
        self.resolve_top(game)
        self.assertFalse(self.protections(game, late))
        self.replay(game, load=True)
