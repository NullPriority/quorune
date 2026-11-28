"""Live ownership, paired exile, actual melded entry and physical departure."""

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture

from quorune.carddb import CardDatabase
from quorune.composition_characteristics import copiable_source_trigger_programs
from quorune.deck import DeckDefinition, DeckEntry
from quorune.meld_model import MeldPairSpec, meld_pair_effects
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.meld_pair_shapes import DEPENDENCIES
from scripts.build_test_database import build_fixture_database


SOURCE = "Hanweir Battlements"
PARTNER = "Hanweir Garrison"
RESULT = "Hanweir, the Writhing Township"


class MeldPairTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        database = Path(cls.temporary.name) / "hanweir-meld.sqlite3"
        fixtures = ROOT / "tests/fixtures"
        removal = json.loads((fixtures / "state-qualified-zone-cards.json").read_bytes())
        removal["cards"] = [row for row in removal["cards"] if row["name"] == "Murder"]
        removal["rulings"] = [row for row in removal.get("rulings", [])
                              if row["oracle_id"] == removal["cards"][0]["oracle_id"]]
        support = Path(cls.temporary.name) / "meld-removal-cards.json"
        support.write_text(json.dumps(removal), encoding="utf-8")
        build_fixture_database([fixtures / "hanweir-meld-cards.json",
                                fixtures / "next-personal-turn-protection-cards.json", support], database)
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Hanweir physical meld", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def start(self, game, *cards):
        game.state.config.semantic_policy = "trusted_only"
        for card in cards:
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 3, "R": 3})
        self.checkpoint(game)

    def offers(self, game, source):
        self.priority(game, "A")
        legal = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        return {row["id"]: row for row in legal if row["id"].startswith(f"activate:{source.ref}:")}

    def activate_meld(self, game, source):
        offers = self.offers(game, source)
        action, = (row for name, row in offers.items() if name.endswith(":ab3"))
        accepted = game.act("pilot:A", {"action_id": action["id"], "pay": "auto"})
        self.assertTrue(accepted.ok, accepted.summary)

    def test_exact_pair_retains_colorless_mana_haste_and_requires_original_complete_grammar(self):
        card = self.db.lookup(SOURCE, fuzzy=False)
        ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile="commander_review")
        self.assertEqual("exact", ir.status, ir.material_residuals)
        mana, haste, meld = ir.faces[0].nodes
        self.assertEqual("activated-mana-fixed-output-v1", mana.template_id)
        self.assertEqual("fixed-target-characteristics-until-end-of-turn-v1", haste.template_id)
        self.assertIsNotNone(haste.target_schema)
        self.assertIsNone(meld.target_schema)
        self.assertTrue(meld.cost["tap_source"])
        self.assertEqual(meld_pair_effects(MeldPairSpec(SOURCE, PARTNER, RESULT)), tuple(meld.effects))
        self.assertTrue(set(DEPENDENCIES) <= set(meld.capability_dependencies))
        for old, new in (("both own and control", "control"), (PARTNER, "Grizzly Bears"), (RESULT, "Brisela, Voice of Nightmares")):
            with self.subTest(new=new):
                changed = replace(card, oracle_text=card.oracle_text.replace(old, new))
                self.assertNotEqual("exact", compile_oracle_card(
                    changed, capability_registry=self.registry, capability_profile="commander_review",
                ).status)
        for name in (PARTNER, RESULT):
            linked = self.db.lookup(name, fuzzy=False)
            self.assertEqual("exact", compile_oracle_card(linked, capability_registry=self.registry,
                                                         capability_profile="commander_review").status)

    def test_real_pair_enters_as_one_combined_permanent_and_physical_cards_depart(self):
        game = self.session(71205002)
        source = self.add(game.engine, SOURCE)
        partner = self.add(game.engine, PARTNER)
        murder = self.add(game.engine, "Murder", seat="B", zone="hand")
        game.state.players["B"].mana_pool.update({"C": 1, "B": 2})
        self.start(game, source, partner, murder)
        self.assertEqual({"ab1", "ab2", "ab3"}, {key.rsplit(":", 1)[-1] for key in self.offers(game, source)})
        original = (source.logical_object_id, partner.logical_object_id)
        self.activate_meld(game, source)
        self.assertTrue(game.state.cards[source.object_id].tapped)
        self.assertEqual("battlefield", game.state.cards[partner.object_id].zone)
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))

        combined = game.state.cards[source.object_id]
        self.assertEqual("battlefield", combined.zone)
        self.assertNotIn(partner.object_id, game.state.cards)
        storage = combined.permanent_composition
        self.assertEqual((source.object_id, partner.object_id), storage.topology.physical_object_ids)
        self.assertEqual(0, storage.topology.mutation_count)
        self.assertEqual("exile", storage.parts[1].card_state["zone"])
        self.assertNotEqual(original[1], storage.parts[1].identity.logical_object_id)
        self.assertNotEqual(original[0], combined.logical_object_id)
        data = game.engine._effective_card_data(combined)
        self.assertEqual((RESULT, "7", "4", 3), (data["name"], data["power"], data["toughness"], data["mana_value"]))
        self.assertTrue({"trample", "haste"} <= {value.casefold() for value in data["keywords"]})
        self.assertEqual([], data["activated_abilities"])
        self.assertEqual(0, game.engine._copyable_characteristics(combined)["mana_value"])
        combined_record = self.db.lookup(RESULT, fuzzy=False)
        programs = copiable_source_trigger_programs(game.engine, data, "battlefield")
        self.assertIn((combined_record.oracle_id, "creature.attacks"),
                      {(program.oracle_id, program.event) for program in programs})
        game = self.replay(game, load=True)

        self.cast_for(game, "B", murder, [combined.ref])
        decision = self.resolve(game)
        self.assertEqual("semantic.choice", decision.kind)
        choice = game.packet("pilot:A", full=True)["decision"]["ctx"]
        self.assertEqual("composition_departure_order", choice["operation"])
        game = self.replay(game, load=True)
        command = {"action_id": "choose", "cards": [partner.ref, source.ref]}
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:B", command).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        result = game.act("pilot:A", command)
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("graveyard", game.state.cards[source.object_id].zone)
        self.assertEqual("graveyard", game.state.cards[partner.object_id].zone)
        self.assertIsNone(game.state.cards[source.object_id].permanent_composition)
        self.assertEqual((SOURCE, PARTNER), (game.state.cards[source.object_id].printed_name,
                                            game.state.cards[partner.object_id].printed_name))
        self.replay(game, load=True)

    def test_ordinary_paid_activation_rechecks_owner_and_control(self):
        for owner, controller in (("A", "B"), ("B", "A")):
            with self.subTest(owner=owner, controller=controller):
                game = self.session(71205004)
                source = self.add(game.engine, SOURCE)
                partner = self.add(game.engine, PARTNER, zone="hand", seat=owner)
                game.engine.move_card(partner.object_id, "battlefield", controller=controller, log=False)
                self.start(game, source, partner)
                self.activate_meld(game, source)
                self.assertIsNone(self.resolve(game))
                live = game.state.cards[source.object_id]
                self.assertEqual("battlefield", live.zone)
                self.assertTrue(live.tapped)
                self.assertIsNone(live.permanent_composition)
                self.assertEqual("battlefield", game.state.cards[partner.object_id].zone)
                self.replay(game, load=True)

    def test_chosen_copy_token_exiles_but_cannot_form_meld_pair(self):
        game = self.session(70142002)
        source = self.add(game.engine, SOURCE)
        partner = self.add(game.engine, PARTNER)
        self.assert_closed(game, partner)
        token_ref, = game.engine.create_token("A", name="", copy_of=partner.ref)
        self.start(game, source, partner)
        self.activate_meld(game, source)
        pending = self.resolve(game)
        self.assertEqual("semantic.choice", pending.kind)
        game = self.replay(game, load=True)
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:A", {"action_id": "choose", "objects": [source.ref]}).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        chosen = game.act("pilot:A", {"action_id": "choose", "objects": [token_ref]})
        self.assertTrue(chosen.ok, chosen.summary)
        self.assertEqual("exile", game.state.cards[source.object_id].zone)
        self.assertIsNone(game.state.cards[source.object_id].permanent_composition)
        self.assertEqual("battlefield", game.state.cards[partner.object_id].zone)
        self.assertFalse(any(card.ref == token_ref and card.zone == "battlefield" for card in game.state.cards.values()))
        self.replay(game, load=True)
