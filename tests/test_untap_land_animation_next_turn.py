"""Original loyalty activation, retained land identity and controller-relative expiry."""

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
import test_next_personal_turn_protection as prior_protection
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.resolution_characteristic_model import (
    NEXT_TURN_TARGET_LAND_ANIMATION_CAPABILITY, next_turn_target_land_animation_effects,
)
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.fixed_resolution_characteristic_shapes import fixed_resolution_characteristics_runtime_dependencies
from scripts.build_test_database import build_fixture_database


CARD = "Nissa, Vital Force"
PROFILE = "commander_review"


class UntapLandAnimationNextTurnTests(InheritedNativeFixture, unittest.TestCase):
    until_following_turn = prior_protection.NextPersonalTurnProtectionTests.until_following_turn
    reject = prior_protection.NextPersonalTurnProtectionTests.reject

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        root = Path(cls.temporary.name)
        fixtures = ROOT / "tests" / "fixtures"
        support = {
            "bound-effect-program-cards.json": {"Generic Bound Plains"},
            "targeted-library-exile-cards.json": {"Isamaru, Hound of Konda", "Island", "Grizzly Bears"},
            "ability-source-counter-cards.json": {"Boomerang"},
        }
        paths = []
        for filename, names in support.items():
            payload = json.loads((fixtures / filename).read_text(encoding="utf-8"))
            payload["cards"] = [card for card in payload["cards"] if card["name"] in names]
            payload["rulings"] = []
            path = root / filename
            path.write_text(json.dumps(payload), encoding="utf-8")
            paths.append(path)
        db = root / "untap-next-turn-animation.sqlite3"
        build_fixture_database([*paths, fixtures / "untap-land-animation-next-turn-cards.json"], db)
        cls.db = CardDatabase(db)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Until next turn", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def test_exact_original_requires_ordered_untap_and_next_turn_animation_authority(self):
        card = self.db.lookup(CARD, fuzzy=False)
        opts = {"capability_registry": self.registry, "capability_profile": PROFILE}
        ir = compile_oracle_card(card, **opts)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        ability, = [node for node in ir.faces[0].nodes if (node.cost or {}).get("loyalty_delta") == 1]
        self.assertEqual(next_turn_target_land_animation_effects(), tuple(ability.effects))
        self.assertIn(NEXT_TURN_TARGET_LAND_ANIMATION_CAPABILITY, ability.capability_dependencies)
        for old, new in (("your next turn", "end of turn"), ("5/5", "7/7"), ("haste", "vigilance"),
                         ("target land you control", "target land an opponent controls")):
            with self.subTest(operand=old):
                changed = replace(card, oracle_text=card.oracle_text.replace(old, new, 1))
                compiled = compile_oracle_card(changed, **opts)
                self.assertNotIn(NEXT_TURN_TARGET_LAND_ANIMATION_CAPABILITY, {
                    cap for face in compiled.faces for node in face.nodes for cap in node.capability_dependencies
                })
        game = self.session(917233100)
        walker = self.add(game.engine, CARD, zone="hand")
        self.assert_closed(game, walker)
        program, = [row for row in game.engine.semantics.programs_for_oracle(walker.oracle_id)
                    if NEXT_TURN_TARGET_LAND_ANIMATION_CAPABILITY in row.capability_dependencies]
        for effects in (program.effects[1:], tuple(reversed(program.effects))):
            with self.subTest(effects=effects), self.assertRaises(ValueError):
                fixed_resolution_characteristics_runtime_dependencies(replace(program, effects=effects))
        deps = tuple(dep for dep in program.capability_dependencies if dep != NEXT_TURN_TARGET_LAND_ANIMATION_CAPABILITY)
        mutated = replace(program, capability_dependencies=deps,
                          capability_closure=self.registry.closure(deps, profile=PROFILE).to_dict())
        self.assertFalse(bind_semantic_program_runtime(
            mutated, capability_registry=self.registry, profile=PROFILE,
        )["strict"])

    def prepare_nissa(self, seed, *, tapped=True, bounce=False):
        game = self.session(seed)
        walker = self.add(game.engine, CARD, zone="hand")
        land = self.add(game.engine, "Island", ref="nissa-selected-island")
        land.tapped = tapped
        other = self.add(game.engine, "Island", seat="B", ref="nissa-opponent-island")
        creature = self.add(game.engine, "Grizzly Bears")
        response = self.add(game.engine, "Boomerang", seat="B", zone="hand") if bounce else None
        self.prepare(game, mana={"C": 3, "G": 2})
        if bounce:
            game.state.players["B"].mana_pool["U"] = 2
        self.checkpoint(game)
        self.cast_for(game, "A", walker)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(5, game.state.cards[walker.object_id].counters["loyalty"])
        self.priority(game, "A")
        action_id = f"activate:{walker.ref}:ab1"
        offers = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        offer, = [row for row in offers if row["id"] == action_id]
        self.assertEqual({land.ref}, set(offer["target_schema"]["legal_refs"]))
        for illegal in (other, creature):
            self.reject(game, "A", {"action_id": action_id, "targets": [illegal.ref], "pay": "auto"})
        result = game.act("pilot:A", {"action_id": action_id, "targets": [land.ref], "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(6, game.state.cards[walker.object_id].counters["loyalty"])
        self.assertEqual(tapped, game.state.cards[land.object_id].tapped)
        return game, land, response

    def assert_animated(self, game, land):
        card = game.state.cards[land.object_id]
        current = game.engine._effective_card_data(card)
        self.assertTrue({"Creature", "Land", "Elemental", "Island"} <= set(current["type_line"].split()))
        self.assertEqual((5, 5), tuple(game.engine._numeric_stat(card.object_id, stat) for stat in ("power", "toughness")))
        self.assertIn("haste", game.engine._combat_keywords(card))

    def test_paid_plus_one_untaps_animates_and_expires_on_the_activators_next_turn(self):
        game, land, _response = self.prepare_nissa(917233101)
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertFalse(game.state.cards[land.object_id].tapped)
        self.assert_animated(game, land)
        turn = game.state.turn_sequence
        for seat in "BCD":
            self.until_following_turn(game, turn, seat)
            self.assert_animated(game, land)
        self.until_following_turn(game, turn, "A")
        card = game.state.cards[land.object_id]
        self.assertIn("Land", game.engine._effective_card_data(card)["type_line"])
        self.assertNotIn("Creature", game.engine._effective_card_data(card)["type_line"])
        self.assertNotIn("haste", game.engine._combat_keywords(card))
        self.replay(game, load=True)

    def test_already_untapped_land_still_animates_and_departed_target_never_does(self):
        game, land, _response = self.prepare_nissa(917233102, tapped=False)
        self.assertIsNone(self.resolve(game))
        self.assert_animated(game, land)
        self.replay(game, load=True)

        game, land, response = self.prepare_nissa(917233103, bounce=True)
        self.cast_for(game, "B", response, [land.ref])
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        card = game.state.cards[land.object_id]
        self.assertEqual("hand", card.zone)
        self.assertNotIn("Creature", game.engine._effective_card_data(card)["type_line"])
        self.assertFalse(any(getattr(effect, "expires_on_next_turn_of", None) == "A"
                             for effect in game.state.continuous_effects))
        self.replay(game, load=True)
