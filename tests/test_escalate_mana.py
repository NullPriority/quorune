"""Exact Escalate quotes bind modes, targets, printed order and replay."""

from copy import deepcopy
from dataclasses import replace
from itertools import combinations
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from test_controller_creature_keyword_absence import register_card
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.escalate_mana_model import ESCALATE_MANA_CAPABILITY, ESCALATE_MANA_FIELD
from quorune.escalate_tap_model import ESCALATE_MODE_IDS
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.casting.escalate_tap_costs import with_escalate_mana_mode_cost_options
from quorune.util import mana_cost_to_vector
from scripts.build_test_database import build_fixture_database


CARD = "Collective Resistance"
PROFILE = "commander_review"
PROTECTION = {"hexproof", "indestructible"}


class EscalateManaTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "escalate-mana.sqlite3"
        fixtures = ROOT / "tests" / "fixtures"
        build_fixture_database([
            fixtures / "bound-effect-program-cards.json",
            fixtures / "ability-source-counter-cards.json",
            fixtures / "all-kind-counter-cards.json",
            fixtures / "activation-result-views-cards.json",
            fixtures / "escalate-mana-cards.json",
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Escalate mana", [
            DeckEntry("Generic Bound Commander", 1, "commander"),
            DeckEntry("Generic Bound Plains", 30),
        ], ["Generic Bound Commander"])

    def scene(self, seed, *, mana, b_mana=None, shared_creature=False):
        game = self.session(seed)
        game.state.config.semantic_policy = "trusted_only"
        spell = self.add(game.engine, CARD, zone="hand")
        artifact = self.add(game.engine, "Sol Ring", seat="B")
        enchantment = self.add(game.engine, "Hardened Scales", seat="B")
        creature = self.add(game.engine, "Ornithopter" if shared_creature else "Grizzly Bears", seat="C")
        self.assert_closed(game, spell)
        self.prepare(game, mana=mana)
        game.state.players["B"].mana_pool.update(b_mana or {})
        return game, spell, artifact, enchantment, creature

    @staticmethod
    def quote_id(modes):
        return "normal+escalate:" + ",".join(modes)

    def reject(self, game, seat, command):
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act(f"pilot:{seat}", command).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))

    def accept(self, game, command):
        result = game.act("pilot:A", command)
        self.assertTrue(result.ok, result.summary)

    def keywords(self, game, card):
        return set(game.engine._combat_keywords(game.state.cards[card.object_id]))

    def test_affordable_options_retain_green_price_and_selected_target_groups(self):
        game, spell, artifact, enchantment, creature = self.scene(70212031, mana={"C": 2, "G": 2})
        game.state.players["A"].mana_pool["W"] = 0
        self.checkpoint(game)
        offered = self.action(game, "A", spell)
        expected = tuple(group for count in (1, 2) for group in combinations(ESCALATE_MODE_IDS, count))
        options = {option["id"]: option for option in offered["cost_options"]}
        self.assertEqual({self.quote_id(group) for group in expected}, set(options))
        for selected in expected:
            quoted = options[self.quote_id(selected)]
            self.assertEqual(len(selected), quoted["requirements"]["G"])
            self.assertEqual(1, quoted["requirements"]["GENERIC"])
            target_schema = quoted["target_schema"]
            self.assertEqual(selected, tuple(target_schema["legal_modes"]))
            self.assertEqual(selected, tuple(target_schema["mode_schemas"]))
        selected = ("mode_1", "mode_3")
        command = {"action_id": offered["id"], "cost_option": self.quote_id(selected), "pay": "auto",
                   "modes": list(reversed(selected)), "targets": [artifact.ref, creature.ref]}
        self.reject(game, "D", command)
        self.reject(game, "A", {**command, "cost_option": self.quote_id(("mode_1",))})
        self.reject(game, "A", {**command, "modes": ["mode_1", "mode_1"]})
        self.reject(game, "A", {**command, "targets": [enchantment.ref, creature.ref]})
        self.reject(game, "A", {**command, "cost_option": self.quote_id(ESCALATE_MODE_IDS),
                                "modes": list(ESCALATE_MODE_IDS),
                                "targets": [artifact.ref, enchantment.ref, creature.ref]})
        self.accept(game, command)
        self.assertEqual(list(selected), game.state.stack[-1].modes)
        self.assertEqual((1, 0), tuple(game.state.players["A"].mana_pool[symbol] for symbol in ("C", "G")))
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(("graveyard", "battlefield"), tuple(
            game.state.cards[card.object_id].zone for card in (artifact, enchantment)
        ))
        self.assertLessEqual(PROTECTION, self.keywords(game, creature))
        self.replay(game, load=True)

    def test_paid_three_modes_resolve_legal_targets_and_grant_keywords_through_turn_end(self):
        game, spell, artifact, enchantment, creature = self.scene(
            70212032, mana={"C": 1, "G": 3}, b_mana={"U": 2},
        )
        answer = self.add(game.engine, "Boomerang", seat="B", zone="hand")
        self.checkpoint(game)
        offered = self.action(game, "A", spell)
        command = {"action_id": offered["id"], "cost_option": self.quote_id(ESCALATE_MODE_IDS), "pay": "auto",
                   "modes": list(ESCALATE_MODE_IDS), "targets": [artifact.ref, enchantment.ref, creature.ref]}
        self.accept(game, command)
        self.assertEqual(0, sum(game.state.players["A"].mana_pool.values()))
        self.cast(game, "B", answer, [enchantment.ref])
        self.resolve_top(game)
        self.assertEqual("hand", game.state.cards[enchantment.object_id].zone)
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("graveyard", game.state.cards[artifact.object_id].zone)
        self.assertLessEqual(PROTECTION, self.keywords(game, creature))
        self.until(game, lambda g: g.state.active_player == "B")
        self.assertTrue(PROTECTION.isdisjoint(self.keywords(game, creature)))
        self.replay(game, load=True)

    def test_shared_target_is_offered_and_the_later_grant_cannot_follow_it_across_zones(self):
        game, spell, _, _, both = self.scene(70212033, mana={"C": 1, "G": 2}, shared_creature=True)
        selected = ("mode_1", "mode_3")
        self.checkpoint(game)
        offered = self.action(game, "A", spell)
        self.accept(game, {"action_id": offered["id"], "cost_option": self.quote_id(selected), "pay": "auto",
                           "modes": list(selected), "targets": [both.ref, both.ref]})
        sequence = game.state.event_sequence
        self.assertIsNone(self.resolve(game))
        self.assertEqual("graveyard", game.state.cards[both.object_id].zone)
        skipped = [event for event in game.state.events if event.event_id > sequence
                   and event.code == "effect.target.skipped"]
        self.assertEqual(["grant_keyword_until_end_of_turn"] * 2,
                         [event.details["operation"] for event in skipped])
        self.replay(game, load=True)

    def test_copy_preserves_locked_modes_and_uses_no_new_payment(self):
        game, spell, _, _, creature = self.scene(70212034, mana={"C": 1, "G": 1})
        selected = ("mode_3",)
        self.checkpoint(game)
        offered = self.action(game, "A", spell)
        self.accept(game, {"action_id": offered["id"], "cost_option": self.quote_id(selected), "pay": "auto",
                           "modes": list(selected), "targets": [creature.ref]})
        original, = game.state.stack
        before = dict(game.state.players["B"].mana_pool)
        # Exercise the canonical copy owner without admitting an unrelated copier.
        copy = game.engine._copy_stack_item(
            controller="B", target=original, targets=original.targets,
            target_groups=original.context["target_groups"], reason="Escalate copied mode fixture",
        )
        self.assertEqual(list(selected), copy.modes)
        self.checkpoint(game)
        self.resolve_top(game)
        game = self.replay(game, load=True)
        self.assertEqual(before, game.state.players["B"].mana_pool)
        self.assertLessEqual(PROTECTION, self.keywords(game, creature))
        self.assertIsNone(self.resolve(game))
        self.replay(game, load=True)

    def test_exact_source_requires_escale_authority_and_free_base_retains_extra_green(self):
        card = self.db.lookup(CARD, fuzzy=False)
        options = {"capability_registry": self.registry, "capability_profile": PROFILE}
        ir = compile_oracle_card(card, **options)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        node, = ir.faces[0].nodes
        self.assertEqual((0, len(card.oracle_text)), (node.span.start, node.span.end))
        self.assertIn(ESCALATE_MANA_CAPABILITY, node.capability_dependencies)
        report, semantics = register_card(self.db, self.registry, CARD)
        self.assertTrue(report["strict_capability_ready"], report)
        program, = semantics.programs_for_oracle(card.oracle_id)
        price = program.cost_schema[ESCALATE_MANA_FIELD]
        self.assertEqual(mana_cost_to_vector("{G}")[0], price["mana"])
        deps = tuple(cap for cap in program.capability_dependencies if cap != ESCALATE_MANA_CAPABILITY)
        damaged = deepcopy(program.cost_schema)
        damaged[ESCALATE_MANA_FIELD]["mana"]["G"] = True
        for candidate in (
            replace(program, cost_schema=damaged),
            replace(program, capability_dependencies=deps,
                    capability_closure=self.registry.closure(deps, profile=PROFILE).to_dict()),
        ):
            result = bind_semantic_program_runtime(candidate, capability_registry=self.registry, profile=PROFILE)
            self.assertFalse(result["strict"])
        for old, new in (("Escalate {G}", "Escalate {G/U}"), ("Choose one or more", "Choose two or more")):
            self.assertNotEqual("exact", compile_oracle_card(
                replace(card, oracle_text=card.oracle_text.replace(old, new)), **options,
            ).status)
        free = {"id": "free", "kind": "alternative", "requirements": mana_cost_to_vector("")[0]}
        response = {"modes": list(ESCALATE_MODE_IDS)}
        quote, = with_escalate_mana_mode_cost_options(program, program.cost_schema, [free], response, hint=False)
        self.assertEqual(mana_cost_to_vector("{G}{G}")[0], quote["requirements"])
        self.assertEqual(mana_cost_to_vector("")[0], free["requirements"])
        with patch("quorune.escalate_mana_model.EscalateManaModeSpec.additional_mana", return_value={}):
            mutant, = with_escalate_mana_mode_cost_options(
                program, program.cost_schema, [free], response, hint=False,
            )
        self.assertNotEqual(quote["requirements"], mutant["requirements"])
