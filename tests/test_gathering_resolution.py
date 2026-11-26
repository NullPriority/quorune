"""The original Season resolves two non-targeted choices before its draw."""

from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.pawprint_modes import PAWPRINT_CAPABILITY
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.permanent_resolution_choices import (
    COUNTER_KEYWORDS, COUNTER_KEYWORDS_CAPABILITY, TYPE_DESTRUCTION_CAPABILITY,
    permanent_type_destruction_result,
)
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


GATHERING = "Season of Gathering"
PROFILE = "commander_review"


class GatheringResolutionTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "gathering.sqlite3"
        fixtures = ROOT / "tests/fixtures"
        build_fixture_database([
            fixtures / "nonartifact-nonblack-mass-destruction-cards.json",
            fixtures / "grouped-choose-one-cards.json",
            fixtures / "counter-prohibition-cards.json",
            fixtures / "gathering-resolution-cards.json",
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("Gathering native proof", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Plains", 30),
        ], [commander])

    def test_original_and_separate_choices_have_exact_owned_shapes(self):
        card = self.db.lookup(GATHERING, fuzzy=False)
        arguments = dict(capability_registry=self.registry, capability_profile=PROFILE)
        whole = compile_oracle_card(card, **arguments)
        self.assertEqual("exact", whole.status, whole.material_residuals)
        for line in card.oracle_text.splitlines()[1:]:
            body = line.partition(" " + chr(8212) + " ")[2]
            with self.subTest(body=body):
                ir = compile_oracle_card(replace(card, oracle_text=body, keywords=()), **arguments)
                self.assertEqual("exact", ir.status, ir.material_residuals)
        ready, semantics = register_card(self.db, self.registry, GATHERING)
        self.assertTrue(ready["strict_capability_ready"], ready["blockers"])
        program, = semantics.programs_for_oracle(card.oracle_id)
        self.assertTrue({PAWPRINT_CAPABILITY, COUNTER_KEYWORDS_CAPABILITY, TYPE_DESTRUCTION_CAPABILITY}
                        <= set(program.capability_dependencies))
        for mode in ("mode_1", "mode_2"):
            with self.subTest(mode=mode):
                schema = deepcopy(program.target_schema)
                branches = schema["modes"]
                self.assertEqual([], branches[mode]["groups"])
                instruction, = branches[mode]["effects"]
                if mode == "mode_1":
                    instruction["then"].pop()
                else:
                    instruction["then_by_choice"]["enchantment"] = instruction["then_by_choice"]["artifact"]
                changed = replace(program, target_schema=schema)
                self.assertFalse(bind_semantic_program_runtime(changed, capability_registry=self.registry,
                                                              profile=PROFILE)["strict"])

        schema = deepcopy(program.target_schema)
        schema["modes"]["mode_3"]["effects"][0]["count"]["binding_id"] = "forged-shared-result"
        self.assertFalse(bind_semantic_program_runtime(replace(program, target_schema=schema),
                                                      capability_registry=self.registry, profile=PROFILE)["strict"])

    def prepare_spell(self, seed):
        game = self.session(seed)
        source = self.add(game.engine, GATHERING, zone="hand")
        self.assert_closed(game, source)
        return game, source

    def submit(self, game, actor, command):
        result = game.act("pilot:" + actor, command)
        self.assertTrue(result.ok, result.summary)

    def reject(self, game, actor, command):
        previous = authoritative_state_hash(game.state)
        result = game.act("pilot:" + actor, command)
        self.assertFalse(result.ok, result.summary)
        self.assertEqual(previous, authoritative_state_hash(game.state))

    def cast_modes(self, game, source, aliases, submitted):
        self.prepare(game, mana={"C": 4, "G": 2})
        self.checkpoint(game)
        offer = self.action(game, "A", source)
        option, = [row for row in offer["cost_options"] if row["id"] == "normal+pawprint:" + ",".join(aliases)]
        mode_schemas = option["target_schema"]["mode_schemas"]
        self.assertEqual(aliases, option["target_schema"]["legal_modes"])
        self.assertTrue(all(value["groups"] == [] for value in mode_schemas.values()))
        self.submit(game, "A", {"action_id": offer["id"], "cost_option": option["id"], "pay": "auto",
                               "modes": submitted})
        self.assertEqual(aliases, game.state.stack[-1].modes)
        self.assertEqual([], game.state.stack[-1].targets)
        return len(game.state.players["A"].zones["hand"])

    def choice(self, game):
        pending = game.state.pending_decision
        self.assertIsNotNone(pending)
        self.assertEqual(("semantic.choice", ["A"]), (pending.kind, pending.actors))
        self.assertIsNone(game.packet("pilot:B", full=True)["decision"])

    def keyword_set(self, game, card):
        data = game.engine._effective_card_data(game.state.cards[card.object_id])
        return {str(keyword).casefold() for keyword in data["keywords"]}

    def prove_double_counter(self, *, replay):
        game, source = self.prepare_spell(77201901)
        first = self.add(game.engine, "Grizzly Bears", ref="gathering-first-creature")
        second = self.add(game.engine, "Ornithopter", ref="gathering-second-creature")
        other = self.add(game.engine, "Grizzly Bears", seat="C", ref="gathering-opponent-creature")
        self.add(game.engine, "Hardened Scales")
        in_hand = self.cast_modes(game, source, ["mode_1", "mode_1_repeat_2", "mode_3"],
                                  ["mode_3", "mode_1", "mode_1"])
        self.resolve(game)
        self.choice(game)
        if replay:
            game = self.replay(game, load=True)
        self.reject(game, "B", {"action_id": "choose", "objects": [first.ref]})
        self.reject(game, "A", {"action_id": "choose", "objects": [other.ref]})
        self.reject(game, "A", {"action_id": "choose", "objects": [first.ref, second.ref]})
        self.reject(game, "A", {"action_id": "choose", "objects": []})
        self.submit(game, "A", {"action_id": "choose", "objects": [first.ref]})
        self.choice(game)
        self.assertEqual({"+1/+1": 2}, game.state.cards[first.object_id].counters)
        self.assertEqual({}, game.state.cards[second.object_id].counters)
        self.assertTrue({value.casefold() for value in COUNTER_KEYWORDS} <= self.keyword_set(game, first))
        if replay:
            game = self.replay(game, load=True)
        self.submit(game, "A", {"action_id": "choose", "objects": [second.ref]})
        self.assertEqual({"+1/+1": 2}, game.state.cards[second.object_id].counters)
        self.assertEqual({}, game.state.cards[other.object_id].counters)
        self.assertTrue({"vigilance", "trample"} <= self.keyword_set(game, second))
        self.assertEqual("graveyard", game.state.cards[source.object_id].zone)
        self.assertEqual(in_hand + 4, len(game.state.players["A"].zones["hand"]))
        if replay:
            self.replay(game, load=True)

    def test_repeated_creature_choices_keep_replacements_and_draw_after_actual_growth(self):
        self.prove_double_counter(replay=True)

    def prove_type_choices(self, *, replay):
        game, source = self.prepare_spell(77201902)
        artifact = self.add(game.engine, "Ornithopter", seat="B", ref="gathering-artifact")
        enchantment = self.add(game.engine, "Hardened Scales", seat="C", ref="gathering-enchantment")
        protected = self.add(game.engine, "Darksteel Relic", seat="D", ref="gathering-indestructible")
        unaffected = self.add(game.engine, "Grizzly Bears", seat="D", ref="gathering-nontype")
        self.assert_closed(game, protected)
        self.cast_modes(game, source, ["mode_2", "mode_2_repeat_2"], ["mode_2", "mode_2"])
        self.resolve(game)
        self.choice(game)
        if replay:
            game = self.replay(game, load=True)
        self.reject(game, "A", {"action_id": "choose", "choice": "creature"})
        self.reject(game, "B", {"action_id": "choose", "choice": "artifact"})
        self.submit(game, "A", {"action_id": "choose", "choice": "artifact"})
        self.choice(game)
        self.assertEqual("graveyard", game.state.cards[artifact.object_id].zone)
        self.assertEqual("battlefield", game.state.cards[enchantment.object_id].zone)
        self.assertEqual("battlefield", game.state.cards[protected.object_id].zone)
        if replay:
            game = self.replay(game, load=True)
        self.submit(game, "A", {"action_id": "choose", "choice": "enchantment"})
        self.assertEqual("graveyard", game.state.cards[enchantment.object_id].zone)
        self.assertEqual("battlefield", game.state.cards[unaffected.object_id].zone)
        self.assertEqual("graveyard", game.state.cards[source.object_id].zone)
        if replay:
            self.replay(game, load=True)

    def test_repeated_type_choices_use_separate_destructions_and_keep_indestructible(self):
        self.prove_type_choices(replay=True)

    def test_prevented_counter_does_not_prevent_keywords_or_extend_their_duration(self):
        game, source = self.prepare_spell(77201903)
        recipient = self.add(game.engine, "Grizzly Bears", ref="gathering-uncountered-recipient")
        prevention = self.add(game.engine, "Solemnity", seat="B")
        self.assert_closed(game, prevention)
        self.cast_modes(game, source, ["mode_1"], ["mode_1"])
        self.resolve(game)
        self.choice(game)
        self.submit(game, "A", {"action_id": "choose", "objects": [recipient.ref]})
        self.assertEqual({}, game.state.cards[recipient.object_id].counters)
        self.assertTrue({"vigilance", "trample"} <= self.keyword_set(game, recipient))
        game = self.replay(game, load=True)
        self.until(game, lambda current: current.state.active_player == "B")
        self.assertFalse({"vigilance", "trample"} & self.keyword_set(game, recipient))
        self.replay(game, load=True)

    def test_no_controlled_creature_skips_its_choice_and_draws_zero(self):
        game, source = self.prepare_spell(77201904)
        self.add(game.engine, "Grizzly Bears", seat="B", ref="gathering-only-opponent")
        in_hand = self.cast_modes(game, source, ["mode_1", "mode_3"], ["mode_1", "mode_3"])
        self.assertIsNone(self.resolve(game))
        self.assertEqual(in_hand, len(game.state.players["A"].zones["hand"]))
        self.assertEqual("graveyard", game.state.cards[source.object_id].zone)
        self.replay(game, load=True)

    def test_omitting_the_keyword_grants_breaks_the_actual_creature_choice(self):
        from quorune.rules.permanent_resolution_choices import controlled_counter_keywords_results

        with patch("quorune.semantic_choices.public_selection.controlled_counter_keywords_results",
                   side_effect=lambda ref: controlled_counter_keywords_results(ref)[:1]):
            with self.assertRaises(AssertionError):
                self.prove_double_counter(replay=False)

    def test_using_the_artifact_scope_for_both_options_breaks_the_actual_second_choice(self):
        with patch("quorune.semantic_choices.scalar.permanent_type_destruction_result",
                   side_effect=lambda _: permanent_type_destruction_result("artifact")):
            with self.assertRaises(AssertionError):
                self.prove_type_choices(replay=False)
