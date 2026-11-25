"""Check current-source graveyard grants through native paid actions."""

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.ability_fragments import AbilityFragmentError, ability_fragment_from_dict
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.granted_activation_model import GRAVEYARD_UNEARTH_GRANT_CAPABILITY, GRANTED_UNEARTH_VERSION
from quorune.haste import has_effective_haste
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database


class GraveyardUnearthGrantTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        database = Path(cls.temporary.name) / "unearth-grants.sqlite3"
        build_fixture_database([
            ROOT / "tests/fixtures/bound-effect-program-cards.json",
            ROOT / "tests/fixtures/opponent-discard-results-cards.json",
            ROOT / "tests/fixtures/graveyard-unearth-grant-cards.json",
        ], database)
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Unearth grants", [
            DeckEntry("Generic Bound Commander", 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], ["Generic Bound Commander"])

    @staticmethod
    def graveyard_offers(game, player, card):
        return [a for a in game.engine._priority_action_hints(player)["actions"]
                if a.get("source") == card.ref and a["id"].startswith("activate:")]

    @staticmethod
    def graveyard_abilities(game, card):
        return tuple(ability for ability in game.engine._activated_abilities(card)
                     if "graveyard" in ability.zones)

    def test_paid_grant_survives_source_departure_with_distinct_printed_and_granted_choices(self):
        game = self.session(70284281)
        grantor = self.add(game.engine, "Generic Unearth Keeper", seat="A", zone="battlefield")
        self.add(game.engine, "Generic Unearth Sliver", seat="A", zone="battlefield", ref="SLIVER-GRANT-1")
        self.add(game.engine, "Generic Unearth Sliver", seat="A", zone="battlefield", ref="SLIVER-GRANT-2")
        self.add(game.engine, "Generic Unearth Sliver", seat="B", zone="battlefield", ref="OPPONENT-GRANT")
        subject = self.add(game.engine, "Generic Unearth Sliver", seat="A", zone="graveyard", ref="SLIVER-RECIPIENT")
        bounce = self.add(game.engine, "Generic Creature Return", seat="C", zone="hand")
        secret = self.add(game.engine, "Generic Bound Growth", seat="D", zone="hand", ref="UNE-GRANT-PRIVATE")
        for card in (grantor, subject, bounce):
            self.assert_closed(game, card)
        game.state.config.semantic_policy = "trusted_only"
        self.prepare(game, mana={"B": 3, "C": 9})
        game.state.players["C"].mana_pool["U"] = 1
        self.checkpoint(game)
        abilities = self.graveyard_abilities(game, subject)
        self.assertEqual(["{1}", "{2}{B}", "{3}", "{3}"], sorted(a.cost_text for a in abilities))
        self.assertEqual(4, len({ability.ability_id for ability in abilities}))
        offers = self.graveyard_offers(game, "A", subject)
        self.assertEqual(4, len(offers), offers)
        self.assertNotIn(secret.ref, str(game.packet("pilot:A", full=True)))
        selected, = [ability for ability in abilities if ability.cost_text == "{2}{B}"]
        offer, = [row for row in offers if row["ability"] == selected.ability_id]
        result = game.act("pilot:A", {"action_id": offer["id"]})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("graveyard", game.state.cards[subject.object_id].zone)
        self.cast_for(game, "C", bounce, [grantor.ref])
        self.resolve_top(game)
        self.assertEqual("hand", game.state.cards[grantor.object_id].zone)
        self.assertEqual(["{1}", "{3}", "{3}"],
                         sorted(a.cost_text for a in self.graveyard_abilities(game, subject)))
        game = self.replay(game, load=True)
        self.resolve_top(game)
        moved = game.state.cards[subject.object_id]
        self.assertTrue(moved.unearthed)
        self.assertEqual("battlefield", moved.zone)
        self.assertTrue(has_effective_haste(game.engine, moved))
        self.until(game, lambda s: any(item.source_object_id == moved.object_id
                                       and item.context.get("dynamic_effects") == [{"op": "unearth", "action": "exile"}]
                                       for item in s.state.stack))
        self.resolve_top(game)
        self.assertEqual("exile", game.state.cards[subject.object_id].zone)
        self.replay(game, load=True)

    def test_party_and_artifact_queries_exclude_other_owners_and_wrong_types(self):
        game = self.session(70284282)
        party = self.add(game.engine, "Generic Party Unearth Keeper", seat="A", zone="battlefield")
        artifacts = self.add(game.engine, "Generic Artifact Unearth Keeper", seat="A", zone="battlefield")
        warrior = self.add(game.engine, "Generic Graveyard Warrior", seat="A", zone="graveyard")
        relic = self.add(game.engine, "Generic Graveyard Relic", seat="A", zone="graveyard")
        other = self.add(game.engine, "Generic Graveyard Warrior", seat="B", zone="graveyard", ref="OTHER-WARRIOR")
        beast = self.add(game.engine, "Generic Graveyard Beast", seat="A", zone="graveyard")
        for card in (party, artifacts, warrior, relic, other, beast):
            self.assert_closed(game, card)
        game.state.config.semantic_policy = "trusted_only"
        self.prepare(game, mana={"W": 2, "C": 8})
        self.checkpoint(game)
        self.assertEqual(["{2}{W}"], [a.cost_text for a in self.graveyard_abilities(game, warrior)])
        self.assertEqual(["{4}"], [a.cost_text for a in self.graveyard_abilities(game, relic)])
        self.assertEqual([], self.graveyard_offers(game, "A", other))
        self.assertEqual((), self.graveyard_abilities(game, other))
        self.assertEqual([], self.graveyard_offers(game, "A", beast))
        offer, = self.graveyard_offers(game, "A", relic)
        result = game.act("pilot:A", {"action_id": offer["id"]})
        self.assertTrue(result.ok, result.summary)
        self.resolve_top(game)
        self.assertEqual("battlefield", game.state.cards[relic.object_id].zone)
        self.assertTrue(game.state.cards[relic.object_id].unearthed)
        self.replay(game, load=True)

    def test_graveyard_fragment_rejects_forged_timing_cost_and_child(self):
        record = self.db.lookup("Generic Unearth Keeper", fuzzy=False)
        compiled = compile_oracle_card(record, capability_registry=self.registry, capability_profile="commander_review")
        self.assertEqual("exact", compiled.status, compiled.material_residuals)
        outer, = [node for face in compiled.faces for node in face.nodes
                  if GRAVEYARD_UNEARTH_GRANT_CAPABILITY in node.capability_dependencies]
        encoded, = outer.handlers[0]["modifier"]["add_ability_fragments"]
        fragment = ability_fragment_from_dict(encoded)
        self.assertEqual(GRANTED_UNEARTH_VERSION, fragment.schema_version)
        self.assertEqual(("graveyard",), fragment.activation_zones)
        for extra in ({"sorcery_speed": False}, {"cost_text": "{X}"}, {"grant_incarnation": "invalid"}):
            with self.assertRaises(AbilityFragmentError):
                ability_fragment_from_dict({**encoded, "value": {**encoded["value"], **extra}})
        unsupported = replace(record, oracle_text=record.oracle_text.replace("your graveyard", "all graveyards"))
        self.assertNotEqual("exact", compile_oracle_card(unsupported, capability_registry=self.registry).status)

        game = self.session(70284283)
        grantor = self.add(game.engine, "Generic Unearth Keeper", seat="A", zone="battlefield")
        target = self.add(game.engine, "Generic Graveyard Warrior", seat="A", zone="graveyard")
        for card in (grantor, target):
            self.assert_closed(game, card)
        self.prepare(game, mana={"B": 2, "C": 5})
        real = game.engine.semantics.get
        with patch.object(game.engine.semantics, "get",
                          side_effect=lambda key: None if key == fragment.semantic_key else real(key)):
            self.assertEqual([], self.graveyard_offers(game, "A", target))
