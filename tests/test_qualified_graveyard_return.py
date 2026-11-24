"""Subtype-qualified graveyard selection after the original Balemurk trigger."""
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
from quorune.compiler.graveyard_return_choice_templates import graveyard_return_choice_template
from quorune.deck import DeckDefinition, DeckEntry
from quorune.object_query import object_matches_query
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.graveyard_return_choices import (
    CAPABILITY, MECHANIC, OPERATION, QUALIFIED_CHOICE_CAPABILITY, GraveyardReturnSelection,
    graveyard_return_candidates, graveyard_return_choice_node_capabilities,
)
from scripts.build_test_database import build_fixture_database
from test_graveyard_return_choices import row


RETURN = "You may return a non-Avatar creature card or a planeswalker card from your graveyard to your hand."


class QualifiedGraveyardReturnTests(unittest.TestCase):
    def test_subtype_qualification_is_versioned_and_local_to_the_creature_branch(self):
        template = graveyard_return_choice_template("Mill four cards, then " + RETURN)
        self.assertIsNotNone(template)
        _, effects, schema, coverage = template
        self.assertIsNone(schema)
        self.assertEqual(("mill", OPERATION), tuple(effect["op"] for effect in effects))
        self.assertEqual(4, effects[0]["count"])
        selected = effects[1]["selection"]
        self.assertEqual({"schema_version": 3, "types_any": ["creature", "planeswalker"],
                          "excluded_creature_subtype": "avatar"}, selected)
        selection = GraveyardReturnSelection.from_dict(selected)
        self.assertEqual(selected, selection.to_dict())
        self.assertEqual((CAPABILITY, QUALIFIED_CHOICE_CAPABILITY),
            graveyard_return_choice_node_capabilities(effects=effects[1:], target_schema=None,
                                                      mechanic_ids=coverage))
        for changed in (
            {**selected, "excluded_creature_subtype": None},
            {**selected, "excluded_creature_subtype": "Avatar"},
            {**selected, "types_any": ["creature"]},
            {**selected, "schema_version": 1},
            {**selected, "schema_version": True},
        ):
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                GraveyardReturnSelection.from_dict(changed)
        for printed in (
            RETURN.replace("non-Avatar", "non-Plains"),
            RETURN.replace("a planeswalker", "an artifact"),
            RETURN.replace("You may return", "Return"),
            RETURN.replace("from your graveyard", "from any graveyard"),
        ):
            with self.subTest(printed=printed):
                self.assertIsNone(graveyard_return_choice_template(printed))

    def test_subtype_exclusion_keeps_a_planeswalker_that_is_also_an_avatar(self):
        rows = (
            row("bear"),
            replace(row("avatar"), subtypes=("avatar",)),
            replace(row("dual", types=("creature", "planeswalker")), subtypes=("avatar",)),
            row("planeswalker", types=("planeswalker",)),
            row("foreign", owner="B", controller="A"),
            row("hand", zone="hand"),
            row("token", token=True),
        )
        host = SimpleNamespace(
            state=SimpleNamespace(cards={item.object_id: SimpleNamespace(is_card_object=not item.token)
                                         for item in rows}),
            public_zone_move_object_rows=lambda actor: rows,
            public_zone_move_active_seats=lambda: tuple("ABCD"),
            public_zone_move_apnap_order=lambda: tuple("BCDA"),
        )
        selection = GraveyardReturnSelection(("creature", "planeswalker"), schema_version=3,
                                             excluded_creature_subtype="avatar")
        effect = {"op": OPERATION, "player": "A", "selection": selection.to_dict()}
        self.assertEqual({"bear", "dual", "planeswalker"},
                         set(graveyard_return_candidates(host, "A", effect)))
        with patch.object(GraveyardReturnSelection, "matches",
                          lambda selection, item: object_matches_query(item, selection.query)):
            self.assertIn("avatar", graveyard_return_candidates(host, "A", effect))
            self.assertNotEqual({"bear", "dual", "planeswalker"},
                                set(graveyard_return_candidates(host, "A", effect)))


class QualifiedGraveyardReturnNativeTests(InheritedNativeFixture, unittest.TestCase):
    from test_graveyard_return_choices import GraveyardReturnChoiceNativeTests as _graveyard
    library = _graveyard.library
    options = _graveyard.options
    choose = _graveyard.choose

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "qualified-graveyard-return.sqlite3"
        build_fixture_database([
            ROOT / "tests/fixtures/bound-effect-program-cards.json",
            ROOT / "tests/fixtures/graveyard-salvage-cards.json",
            ROOT / "tests/fixtures/source-event-player-damage-cards.json",
            ROOT / "tests/fixtures/upkeep-creature-token-absence-cards.json",
            ROOT / "tests/fixtures/fixed-mana-result-cards.json",
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = "Generic Bound Commander"
        cls.deck = DeckDefinition("Qualified graveyard return", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], [commander])

    def test_actual_balemurk_entry_mills_then_offers_an_owned_creature_or_planeswalker(self):
        game = self.session(702176250)
        overlord = self.add(game.engine, "Overlord of the Balemurk", zone="hand")
        avatar = self.add(game.engine, "Overlord of the Balemurk", zone="graveyard", ref="BAL_AVATAR")
        planeswalker = self.add(game.engine, "Jace Beleren", zone="graveyard", ref="BAL_PLANESWALKER")
        foreign = self.add(game.engine, "Grizzly Bears", seat="B", zone="graveyard", ref="BAL_FOREIGN")
        private = self.add(game.engine, "Generic Bound Plains", seat="D", zone="hand", ref="BAL_PRIVATE")
        # The library helper appends fixture cards from the bottom up.
        fifth, creature, changeling, land, artifact = self.library(
            game, ["Forest", "Grizzly Bears", "Universal Automaton", "Forest", "Sol Ring"],
        )
        self.assert_closed(game, overlord)
        self.prepare(game, mana={"C": 1, "B": 1})
        self.checkpoint(game)
        self.priority(game, "A")
        offer = self.action(game, "A", overlord)
        cast = game.act("pilot:A", {"action_id": offer["id"], "cost_option": "impending", "pay": "auto"})
        self.assertTrue(cast.ok, cast.summary)
        self.assertEqual("semantic.choice", self.resolve(game).kind)
        self.assertEqual(5, game.state.cards[overlord.object_id].counters.get("time"))
        self.assertEqual({creature.ref, planeswalker.ref}, self.options(game))
        self.assertEqual(["graveyard"] * 4, [game.state.cards[card.object_id].zone
                                            for card in (creature, changeling, land, artifact)])
        self.assertEqual("library", game.state.cards[fifth.object_id].zone)
        self.assertNotIn(private.ref, json.dumps(game.packet("pilot:A", full=True)))
        game = self.choose(game, planeswalker, (avatar.ref, changeling.ref, land.ref, artifact.ref, foreign.ref))
        self.resolve(game)
        self.assertEqual("hand", game.state.cards[planeswalker.object_id].zone)
        self.assertEqual("graveyard", game.state.cards[creature.object_id].zone)
        self.replay(game, load=True)

    def test_withdrawn_subtype_choice_keeps_the_original_positive_graveyard_choice(self):
        from quorune.oracle_ir import compile_oracle_card
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests

        unrelated = self.db.lookup("Grapple with the Past")
        original = self.db.lookup("Overlord of the Balemurk")
        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        entry = next(row for row in raw["capabilities"] if row["id"] == QUALIFIED_CHOICE_CAPABILITY)
        entry.update(status="blocked", blockers=["withheld subtype qualification"])
        registry = CapabilityRegistry(raw)
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        index = build_index(registry_value=raw, declaration_source=declarations,
                            discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
        kwargs = {"capability_registry": registry, "capability_profile": "commander_review"}
        self.assertTrue(compile_oracle_card(original, **kwargs).material_residuals)
        self.assertEqual("exact", compile_oracle_card(unrelated, **kwargs).status)

    def test_actual_balemurk_attack_can_decline_after_a_short_mill(self):
        game = self.session(702176251)
        overlord = self.add(game.engine, "Overlord of the Balemurk")
        creature = self.add(game.engine, "Grizzly Bears", zone="graveyard", ref="BAL_OLD_CREATURE")
        land, = self.library(game, ["Forest"])
        self.assert_closed(game, overlord)
        self.prepare(game)
        self.checkpoint(game)
        self.attack(game, [(overlord, "B")])
        self.assertEqual("semantic.choice", self.resolve(game).kind)
        self.assertEqual({creature.ref}, self.options(game))
        game = self.choose(game, invalid=(land.ref,))
        self.resolve(game)
        self.assertEqual("graveyard", game.state.cards[land.object_id].zone)
        self.assertEqual("graveyard", game.state.cards[creature.object_id].zone)
        self.replay(game, load=True)
