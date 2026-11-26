"""The printed Null Elemental Blast modes use two distinct current target domains."""

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase, CardRecord
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.node_capability_shapes import fixed_stack_counter_target_schema_is_closed
from scripts.build_test_database import build_fixture_database


CARD_NAME = "Null Elemental Blast"
COUNTER_MODE = "mode_1"
DESTROY_MODE = "mode_2"


class NullElementalBlastTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        fixtures = ROOT / "tests/fixtures"
        database = Path(cls.temporary.name) / "null-elemental-blast.sqlite3"
        build_fixture_database([
            fixtures / "battlefield-spell-payment-grant-cards.json",
            fixtures / "bound-effect-program-cards.json",
            fixtures / "conditional-modal-target-color-cards.json",
            fixtures / "fixed-resolution-mana-cards.json",
            fixtures / "graveyard-self-return-cards.json",
            fixtures / "live-modifier-quantity-cards.json",
            fixtures / "null-elemental-blast-cards.json",
        ], database)
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        commander = "Generic Bound Commander"
        cls.deck = DeckDefinition("Null Elemental Blast", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], [commander])

    def test_exact_two_modes_and_closed_counter_schema(self):
        card = CardRecord(
            oracle_id="fixture:multicolored-choose-one", name="Generic Multicolored Answer",
            mana_cost="{C}", mana_value=1, type_line="Instant",
            oracle_text=("Choose one \u2014\n"
                         "\u2022 Counter target multicolored spell.\n"
                         "\u2022 Destroy target multicolored permanent."),
            power=None, toughness=None, loyalty=None, defense=None,
            colors=(), color_identity=(), keywords=(), produced_mana=(),
            layout="normal", released_at="2024-06-14", legalities={"commander": "legal"},
            faces=(), raw={},
        )

        def compile_card():
            return compile_oracle_card(card, capability_registry=self.registry,
                                       capability_profile="commander_review")

        ir = compile_card()
        self.assertEqual("exact", ir.status)
        self.assertFalse(ir.material_residuals)
        node, = ir.faces[0].nodes
        self.assertEqual((), node.effects)
        self.assertEqual(1, node.target_schema["mode_count"])
        self.assertEqual([COUNTER_MODE, DESTROY_MODE], list(node.target_schema["modes"]))
        self.assertIn("choice.modal.fixed_one", node.capability_dependencies)
        self.assertIn("stack.counter.effect", node.capability_dependencies)
        self.assertIn("permanent.destroy.effect", node.capability_dependencies)
        modes = node.target_schema["modes"]
        for mode, zone, category, effect in (
            (COUNTER_MODE, "stack", "spell", {"op": "counter_stack_target", "stack": "$target.0"}),
            (DESTROY_MODE, "battlefield", "permanent", {"op": "destroy", "card": "$target.0"}),
        ):
            self.assertEqual([zone], modes[mode]["zones"])
            self.assertEqual([category], modes[mode]["categories"])
            self.assertEqual(2, modes[mode]["color_count_min"])
            self.assertEqual([effect], modes[mode]["effects"])
        schema = {k: v for k, v in modes[COUNTER_MODE].items() if k not in {"effects", "mechanics"}}
        self.assertTrue(fixed_stack_counter_target_schema_is_closed(schema))
        for mutation in (
            {"color_count_min": 2.0}, {"color_count_min": 1}, {"color_count_min": 3},
            {"categories": ["ability"]}, {"colors_any": ["U"]},
            {"color_count_equal": 2}, {"source_exclusion": False},
        ):
            with self.subTest(mutation=mutation):
                self.assertFalse(fixed_stack_counter_target_schema_is_closed({**schema, **mutation}))
        with patch("quorune.rules.node_capability_shapes._TARGETED_COUNTER_SCHEMAS",
                   (dict(schema, color_count_min=1),)):
            self.assertTrue(compile_card().material_residuals)

    def _prepare(self, game, source):
        game.state.config.semantic_policy = "trusted_only"
        self.assert_closed(game, source)
        game.state.players["B"].mana_pool.update({"C": 8, "B": 3, "R": 3, "W": 2})
        game.state.players["C"].mana_pool.update({"C": 3, "U": 3, "R": 1, "W": 2})
        self.prepare(game, mana={"C": 3, "W": 1, "U": 1, "B": 1, "R": 1, "G": 1})
        self.checkpoint(game)

    def _offer(self, game, source):
        self.priority(game, "A")
        return self.action(game, "A", game.state.cards[source.object_id])["target_schema"]

    def _targets(self, schema, mode):
        group, = schema["mode_schemas"][mode]["groups"]
        return set(group["legal_refs"])

    def _command(self, source, mode, target):
        return {"action_id": "cast:" + source.ref, "modes": [mode], "targets": [target], "pay": "auto"}

    def _cast_mode(self, game, source, mode, target):
        self.assertIn(target, self._targets(self._offer(game, source), mode))
        result = game.act("pilot:A", self._command(source, mode, target))
        self.assertTrue(result.ok, result.summary)
        item = game.state.stack[-1]
        self.assertEqual([mode], item.modes)
        self.assertEqual([target], item.targets)
        return item.ref

    def _cast_spell(self, game, seat, card, targets=()):
        self.cast(game, seat, card, targets)
        return next(item.ref for item in game.state.stack if item.card_object_id == card.object_id)

    def _counter_event(self, game, ref, kind):
        event, = [event for event in game.state.events
                  if event.code == "stack.counter" and event.details.get("stack") == ref]
        self.assertEqual(kind, event.details["counter_kind"])

    def test_modes_offer_only_live_multicolored_objects_and_execute_separately(self):
        game = self.session(10502011)
        blast = self.add(game.engine, CARD_NAME, zone="hand", ref="counter-blast")
        other = self.add(game.engine, CARD_NAME, zone="hand", ref="destroy-blast")
        five_spell = self.add(game.engine, "Fusion Elemental", zone="hand", ref="five-color-spell")
        two_spell = self.add(game.engine, "Terminate", seat="B", zone="hand")
        mono_spell = self.add(game.engine, "Lightning Bolt", seat="B", zone="hand")
        devoid_spell = self.add(game.engine, "Reality Hemorrhage", seat="B", zone="hand")
        neutral_spell = self.add(game.engine, "Spatial Contortion", seat="C", zone="hand")
        five = self.add(game.engine, "Fusion Elemental", seat="D", ref="five-color-permanent")
        two = self.add(game.engine, "Baleful Strix", seat="B")
        mono = self.add(game.engine, "Grizzly Bears", seat="C")
        neutral = self.add(game.engine, "Sol Ring", seat="C")
        devoid = self.add(game.engine, "Flayer Drone", seat="D")
        self._prepare(game, blast)

        self.assertEqual([DESTROY_MODE], self._offer(game, blast)["legal_modes"])
        five_ref = self._cast_spell(game, "A", five_spell)
        two_ref = self._cast_spell(game, "B", two_spell, [mono.ref])
        mono_ref = self._cast_spell(game, "B", mono_spell, ["D"])
        devoid_ref = self._cast_spell(game, "B", devoid_spell, ["D"])
        neutral_ref = self._cast_spell(game, "C", neutral_spell, [five.ref])
        schema = self._offer(game, blast)
        self.assertEqual([COUNTER_MODE, DESTROY_MODE], schema["legal_modes"])
        self.assertEqual({five_ref, two_ref}, self._targets(schema, COUNTER_MODE))
        self.assertEqual({five.ref, two.ref}, self._targets(schema, DESTROY_MODE))
        before_hash = authoritative_state_hash(game.state)
        before_commands = len(game.commands)
        invalid = [(COUNTER_MODE, ref) for ref in (mono_ref, devoid_ref, neutral_ref, five.ref)]
        invalid += [(DESTROY_MODE, ref) for ref in (mono.ref, neutral.ref, devoid.ref, two_ref)]
        for mode, ref in invalid:
            with self.subTest(mode=mode, ref=ref):
                self.assertFalse(game.act("pilot:A", self._command(blast, mode, ref)).ok)
                self.assertEqual(before_hash, authoritative_state_hash(game.state))
                self.assertEqual(before_commands, len(game.commands))
        command = self._command(blast, COUNTER_MODE, two_ref)
        self.assertFalse(game.act("pilot:D", command).ok)
        self.assertFalse(game.act("pilot:A", {**command, "modes": [COUNTER_MODE, DESTROY_MODE]}).ok)
        self.assertEqual(before_hash, authoritative_state_hash(game.state))

        self._cast_mode(game, blast, COUNTER_MODE, two_ref)
        self.resolve_top(game)
        self._counter_event(game, two_ref, "effect")
        self.assertEqual("graveyard", game.state.cards[two_spell.object_id].zone)
        self.assertEqual("battlefield", game.state.cards[mono.object_id].zone)
        self.assertEqual("battlefield", game.state.cards[five.object_id].zone)
        self.assertEqual({five_ref}, self._targets(self._offer(game, other), COUNTER_MODE))
        before_hash = authoritative_state_hash(game.state)
        before_commands = len(game.commands)
        self.assertFalse(game.act("pilot:A", self._command(other, COUNTER_MODE, two_ref)).ok)
        self.assertEqual(before_hash, authoritative_state_hash(game.state))
        self.assertEqual(before_commands, len(game.commands))
        self._cast_mode(game, other, DESTROY_MODE, two.ref)
        game = self.replay(game, load=True)
        self.resolve_top(game)
        self.assertEqual("graveyard", game.state.cards[two.object_id].zone)
        self.assertIn(five_ref, [item.ref for item in game.state.stack])
        self.assertIsNone(self.resolve(game))
        self.assertEqual("battlefield", game.state.cards[five_spell.object_id].zone)
        self.assertEqual("battlefield", game.state.cards[five.object_id].zone)
        self.assertEqual(35, game.state.players["D"].life)
        self.replay(game, load=True)

    def test_both_modes_recheck_color_and_destroy_rejects_a_returned_incarnation(self):
        game = self.session(10502012)
        blasts = [self.add(game.engine, CARD_NAME, zone="hand", ref=f"stale-blast-{i}") for i in range(3)]
        laces = [self.add(game.engine, "Moonlace", seat="C", zone="hand", ref=f"moonlace-{i}") for i in range(2)]
        blink = self.add(game.engine, "Flicker of Fate", seat="B", zone="hand")
        spell = self.add(game.engine, "Terminate", seat="B", zone="hand")
        victim = self.add(game.engine, "Grizzly Bears", seat="D")
        recolored = self.add(game.engine, "Fusion Elemental", seat="D", ref="recolored-permanent")
        returning = self.add(game.engine, "Fusion Elemental", seat="B", ref="returning-permanent")
        self._prepare(game, blasts[0])

        spell_ref = self._cast_spell(game, "B", spell, [victim.ref])
        counter_ref = self._cast_mode(game, blasts[0], COUNTER_MODE, spell_ref)
        self._cast_spell(game, "C", laces[0], [spell_ref])
        self.resolve_top(game)
        self.assertFalse(game.engine._target_snapshot(spell_ref)["colors"])
        self.assertEqual([DESTROY_MODE], self._offer(game, blasts[1])["legal_modes"])
        self.assertIsNone(self.resolve(game))
        self._counter_event(game, counter_ref, "rules")
        self.assertEqual("graveyard", game.state.cards[victim.object_id].zone)

        destroy_ref = self._cast_mode(game, blasts[1], DESTROY_MODE, recolored.ref)
        self._cast_spell(game, "C", laces[1], [recolored.ref])
        self.resolve_top(game)
        self.assertEqual({returning.ref}, self._targets(self._offer(game, blasts[2]), DESTROY_MODE))
        self.assertIsNone(self.resolve(game))
        self._counter_event(game, destroy_ref, "rules")
        self.assertEqual("battlefield", game.state.cards[recolored.object_id].zone)

        stale_ref = self._cast_mode(game, blasts[2], DESTROY_MODE, returning.ref)
        old_identity = game.state.cards[returning.object_id].logical_object_id
        self._cast_spell(game, "B", blink, [returning.ref])
        self.resolve_top(game)
        self.assertNotEqual(old_identity, game.state.cards[returning.object_id].logical_object_id)
        self.assertEqual(set("WUBRG"), set(game.engine._target_snapshot(returning.ref)["colors"]))
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self._counter_event(game, stale_ref, "rules")
        self.assertEqual("battlefield", game.state.cards[returning.object_id].zone)
        self.replay(game, load=True)
