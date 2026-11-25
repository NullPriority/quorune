"""Native evidence for paid exile followed by the retained self-copy."""

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.entry_copy import COPIABLE_FACE_FIELD
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry
from quorune.rules.copied_flip_faces import COPIED_FLIP_FACES, FLIPPED_STATUS
from quorune.rules.evidence import validate_capability_evidence_index
from quorune.rules.evidence_storage import read_evidence_document
from quorune.rules.graveyard_exile_copy import GRAVEYARD_EXILE_COPY_CAPABILITY
from quorune.zone_transitions import ZoneTransitionOwner
from scripts.build_test_database import build_fixture_database
from scripts.update_capability_evidence import build_index, discover_tests


class GraveyardExileCopyTests(InheritedNativeFixture, unittest.TestCase):
    source_name = "Trial Exile Weaver"
    first = "Trial River Form"
    second = "Trial Ridge Form"
    basic = "Generic Bound Plains"
    hidden = "Trial Shrouded Form"
    flip_normal = "Trial Tidal Scout"
    flip_alternative = "Trial Harbor Keeper"

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        database = Path(cls.temporary.name) / "graveyard-exile-copy.sqlite3"
        fixtures = ROOT / "tests/fixtures"
        build_fixture_database([
            fixtures / "bound-effect-program-cards.json",
            fixtures / "graveyard-exile-copy-cards.json",
        ], database)
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        commander = "Generic Bound Commander"
        cls.deck = DeckDefinition("Graveyard exile copy", [
            DeckEntry(commander, 1, "commander"), DeckEntry(cls.basic, 30),
        ], [commander])

    def _source(self, game):
        card = self.add(game.engine, self.source_name, ref="EXILE-COPY-SOURCE")
        self.assert_closed(game, card)
        return card

    def _prepare_with_targets(self, game, repeats=1):
        self.prepare(game, mana={key: repeats for key in ("C", "U", "B")})
        self.checkpoint(game)

    def _offer(self, game, source):
        self.priority(game, "A")
        actions = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        offer, = (row for row in actions if row["id"].startswith("activate:" + source.ref + ":"))
        return offer

    def _activate(self, game, source, target):
        offer = self._offer(game, source)
        response = game.act("pilot:A", {"action_id": offer["id"], "targets": [target.ref], "pay": "auto"})
        self.assertTrue(response.ok, response.summary)
        self.assertEqual((target.ref,), tuple(game.state.stack[-1].targets))

    def test_exile_copy_pays_colored_cost_and_repeats_with_another_owners_card(self):
        game = self.session(707_900_01)
        source = self._source(game)
        first = self.add(game.engine, self.first, seat="B", zone="graveyard", ref="OTHER-GRAVEYARD")
        second = self.add(game.engine, self.second, seat="C", zone="graveyard", ref="ANOTHER-GRAVEYARD")
        hidden = self.add(game.engine, self.hidden, seat="D", zone="hand", ref="PRIVATE-MODEL")
        land = self.add(game.engine, self.basic, zone="graveyard", ref="PUBLIC-LAND")
        source.counters["+1/+1"] = 1
        original = source.logical_object_id
        self._prepare_with_targets(game, repeats=2)
        offer = self._offer(game, source)
        self.assertEqual({first.ref, second.ref}, set(offer["target_schema"]["legal_refs"]))
        self.assertNotIn(hidden.ref, json.dumps(offer))
        before = authoritative_state_hash(game.state)
        for actor, invalid in (("B", first), ("A", hidden), ("A", land)):
            result = game.act("pilot:" + actor, {"action_id": offer["id"], "targets": [invalid.ref], "pay":"auto"})
            self.assertFalse(result.ok, result.summary)
            self.assertEqual(before, authoritative_state_hash(game.state))
        self._activate(game, source, first)
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        source = game.state.cards[source.object_id]
        self.assertEqual((original, self.first, 1), (
            source.logical_object_id, game.engine.display_name(source.object_id), source.counters["+1/+1"],
        ))
        self.assertEqual("exile", game.state.cards[first.object_id].zone)
        self.assertEqual((1, 1, 1), tuple(game.state.players["A"].mana_pool[key] for key in ("C", "U", "B")))
        self.assertGreater(next(event.event_id for event in reversed(game.state.events)
                                if event.code == "permanent.copy"),
                           next(event.event_id for event in reversed(game.state.events)
                                if event.code == "zone.move" and event.details.get("object") == first.ref))
        self._activate(game, source, second)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("exile", game.state.cards[second.object_id].zone)
        self.assertEqual(self.second, game.engine.display_name(source.object_id))
        self.assertTrue(any(a.ability_id.endswith(":graveyard-exile-copy")
                            for a in game.engine._activated_abilities(game.state.cards[source.object_id])))
        payment = game.state.players["A"].mana_pool
        self.assertEqual((0, 0, 0), tuple(payment.get(key, 0) for key in ("C", "U", "B")))
        turn = game.state.turn_sequence
        self.until(game, lambda session: session.state.turn_sequence > turn)
        self.assertEqual(self.second, game.engine.display_name(source.object_id))
        self.assertNotIn(hidden.ref, json.dumps(game.packet("pilot:A", full=True)))
        self.replay(game, load=True)

    def test_departed_source_still_exiles_its_valid_target_without_copying_fresh_source(self):
        game = self.session(707_900_02)
        source = self._source(game)
        target = self.add(game.engine, self.first, seat="B", zone="graveyard", ref="DEPARTED-SOURCE-TARGET")
        self._prepare_with_targets(game)
        self._activate(game, source, target)
        owner = ZoneTransitionOwner(game.engine)
        prior = source.logical_object_id
        owner.move_card(source.object_id, "hand")
        owner.move_card(source.object_id, "battlefield")
        self.assertNotEqual(prior, source.logical_object_id)
        self.checkpoint(game)
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("exile", game.state.cards[target.object_id].zone)
        self.assertEqual(self.source_name, game.engine.display_name(source.object_id))
        self.assertNotIn("copy_overrides", game.state.cards[source.object_id].annotations)
        self.replay(game, load=True)

    def test_same_reference_of_reentered_graveyard_card_cancels_the_whole_copy(self):
        game = self.session(707_900_03)
        source = self._source(game)
        target = self.add(game.engine, self.first, seat="B", zone="graveyard", ref="REENTERED-TARGET")
        self._prepare_with_targets(game)
        self._activate(game, source, target)
        prior = target.logical_object_id
        owner = ZoneTransitionOwner(game.engine)
        owner.move_card(target.object_id, "exile")
        owner.move_card(target.object_id, "graveyard")
        self.assertNotEqual(prior, target.logical_object_id)
        self.checkpoint(game)
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("graveyard", game.state.cards[target.object_id].zone)
        self.assertEqual(self.source_name, game.engine.display_name(source.object_id))
        self.replay(game, load=True)

    def test_graveyard_exile_copy_trust_requires_the_complete_typed_result_and_price(self):
        card = self.db.lookup(self.source_name)
        def compile_card(record, registry):
            return compile_oracle_card(record, capability_registry=registry, capability_profile="commander_review")

        exact = compile_card(card, self.registry)
        self.assertEqual("exact", exact.status, exact.material_residuals)
        node, = exact.faces[0].nodes
        self.assertEqual("graveyard-exile-self-copy-v1", node.template_id)
        for oracle in (card.oracle_text.replace("{1}{U}{B}", "{2}{U}{B}"),
                       card.oracle_text.replace("a graveyard", "your graveyard"),
                       card.oracle_text + " Draw a card."):
            self.assertNotEqual("exact", compile_card(replace(card, oracle_text=oracle), self.registry).status)
        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        row = next(row for row in raw["capabilities"] if row["id"] == GRAVEYARD_EXILE_COPY_CAPABILITY)
        row.update(status="blocked", blockers=["Graveyard exile copy authority withdrawn."])
        removed = CapabilityRegistry(raw)
        source = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        discovered = set(discover_tests(ROOT).values())
        index = build_index(registry_value=raw, declaration_source=source, discovered_test_ids=discovered)
        removed.mark_evidence_verified(validate_capability_evidence_index(index, registry=removed))
        self.assertNotEqual("exact", compile_card(card, removed).status)
        with patch("quorune.compiler.graveyard_exile_copy.matches_graveyard_exile_self_copy", return_value=False):
            self.assertNotEqual("exact", compile_card(card, self.registry).status)

    def test_graveyard_exile_copy_retains_both_flip_faces_and_the_receivers_status(self):
        game = self.session(707_900_04)
        source = self._source(game)
        target = self.add(game.engine, self.flip_normal, seat="B", zone="graveyard", ref="PUBLIC-FLIP-MODEL")
        self._prepare_with_targets(game)
        self._activate(game, source, target)
        self.assertIsNone(self.resolve(game))
        current = game.state.cards[source.object_id]
        self.assertEqual("exile", game.state.cards[target.object_id].zone)
        self.assertEqual(self.flip_normal, game.engine.display_name(current.object_id))
        copied = current.annotations["copy_overrides"][COPIED_FLIP_FACES]
        for face, name in (("normal", self.flip_normal), ("alternative", self.flip_alternative)):
            self.assertEqual((name, "{1}{G}", ["G"]), (
                copied[face]["name"], copied[face]["mana_cost"], copied[face]["colors"],
            ))
        current.annotations[FLIPPED_STATUS] = True
        self.checkpoint(game)
        game = self.replay(game, load=True)
        values = game.engine._effective_card_data(game.state.cards[source.object_id])
        self.assertEqual((self.flip_alternative, self.flip_alternative, "{1}{G}", ["G"]), (
            values["name"], values[COPIABLE_FACE_FIELD], values["mana_cost"], values["colors"],
        ))
        self.assertTrue(any(a.ability_id.endswith(":graveyard-exile-copy")
                            for a in game.engine._activated_abilities(game.state.cards[source.object_id])))
