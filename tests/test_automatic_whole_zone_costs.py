"""Native witnesses for whole-zone additional costs through the cast owner."""

from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
import test_mandatory_discard_sacrifice as native
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.replacement.model import ReplacementEffectError
from quorune.replacement.replay import ReplacementContinuation
from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry
from quorune.rules.casting.whole_zone_cost_frame import WHOLE_ZONE_COST_FRAME_FIELD
from quorune.rules.evidence import validate_capability_evidence_index
from quorune.rules.evidence_storage import read_evidence_document
from quorune.rules.fixed_payment_groups import WHOLE_DISCARD_SACRIFICE_CAPABILITY
from scripts.build_test_database import build_fixture_database
from scripts.update_capability_evidence import build_index, discover_tests


class WholeZoneCastingTests(unittest.TestCase):
    Witness = native.MandatoryDiscardSacrificeTests
    register_fixture_cards_on_add = True
    session = Witness.session
    add = Witness.add
    prepare = Witness.prepare
    checkpoint = Witness.checkpoint
    resolve = Witness.resolve
    replay = Witness.replay
    options = Witness.options
    spell = "Trial Furnace Covenant"
    body = "Generic Bound Body"
    basic = "Generic Bound Plains"
    bridge = "Stillglass Keeper"
    disposable = "Trial Fuel Pebble"
    payment = {"B": 1, "C": 1}
    loss = 4

    @classmethod
    def setUpClass(cls):
        cls.temp = TemporaryDirectory()
        path = Path(cls.temp.name) / "whole-zone-cast.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures" / name for name in (
            "bound-effect-program-cards.json",
            "mandatory-discard-sacrifice-cards.json",
            "automatic-whole-zone-cost-cards.json",
        )], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Whole-zone casting costs", [
            DeckEntry("Generic Bound Commander", 1, "commander"), DeckEntry(cls.basic, 30),
        ], ["Generic Bound Commander"])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temp.cleanup()

    def clear_opening_hand(self, game):
        for oid in tuple(game.state.players["A"].zones["hand"]):
            game.engine.move_card(oid, "library", reason="whole-zone fixture setup", semantic_events=False)

    def command(self, spell, option, *, pay="manual", payment=None):
        values = {"action_id": "cast:" + spell.ref, "targets": ["C"], "cost_option": option["id"], "pay": pay}
        if pay == "manual":
            values["payment"] = self.payment if payment is None else payment
        return values

    def selected(self, game, spell, *, discard_first):
        options = self.options(game, spell)
        self.assertEqual(len(options), 2, options)
        phrase = "discard, then sacrifice" if discard_first else "sacrifice, then discard"
        return next(value for value in options if phrase in value["label"])

    def test_automatic_whole_zone_order_uses_current_sources_and_simultaneous_sets(self):
        for discard_first in (False, True):
            with self.subTest(discard_first=discard_first):
                game = self.session(601_803+int(discard_first))
                spell = self.add(game.engine, self.spell, zone="hand")
                keeper = self.add(game.engine, self.bridge)
                stolen = self.add(game.engine, self.body, ref="cost-controlled-by-caster")
                stolen.owner = "B"
                donated = self.add(game.engine, self.body, seat="B", ref="cost-controlled-by-opponent")
                donated.owner = "A"
                hand = self.add(game.engine, self.basic, zone="hand", ref="cost-private-hand")
                other = self.add(game.engine, self.body, seat="D", zone="hand", ref="cost-other-private")
                self.prepare(game, mana=self.payment)
                self.checkpoint(game)
                quote = self.selected(game, spell, discard_first=discard_first)
                encoded = json.dumps(quote, ensure_ascii=True)
                for key in ("discard_cards", "sacrifice_cards", hand.ref, other.ref):
                    self.assertNotIn(key, encoded)
                sent = game.act("pilot:A", self.command(spell, quote))
                self.assertTrue(sent.ok, sent.summary)
                cards = game.state.cards
                self.assertEqual("stack", cards[spell.object_id].zone)
                self.assertEqual("exile", cards[keeper.object_id].zone)
                self.assertEqual("exile", cards[stolen.object_id].zone)
                self.assertEqual(cards[keeper.object_id].zone_timestamp, cards[stolen.object_id].zone_timestamp)
                self.assertEqual("B", cards[stolen.object_id].owner)
                self.assertEqual("battlefield", cards[donated.object_id].zone)
                self.assertEqual("exile" if discard_first else "graveyard", cards[hand.object_id].zone)
                self.assertEqual("hand", cards[other.object_id].zone)
                game = self.replay(game, load=True)
                self.assertIsNone(self.resolve(game))
                self.assertEqual(40-self.loss, game.state.players["C"].life)
                self.replay(game, load=True)

    def test_native_whole_zone_replacement_pause_is_private_and_rolls_back_every_payment(self):
        game = self.session(601_805)
        self.clear_opening_hand(game)
        spell = self.add(game.engine, self.spell, zone="hand")
        keeper = self.add(game.engine, self.bridge, ref="whole-caster-replacement")
        body = self.add(game.engine, self.body)
        self.add(game.engine, self.bridge, seat="B", ref="whole-peer-replacement")
        hand_cards = [self.add(game.engine, self.basic, zone="hand", ref=f"private-whole-discard-{i}") for i in (1, 2)]
        self.prepare(game, mana=self.payment)
        self.checkpoint(game)
        quote = self.selected(game, spell, discard_first=True)
        cast = game.act("pilot:A", self.command(spell, quote))
        self.assertTrue(cast.ok, cast.summary)
        phases = set()
        for index in range(8):
            pending = game.state.pending_decision
            if pending.kind != "replacement.order":
                break
            frame = pending.continuation["priority_response"][WHOLE_ZONE_COST_FRAME_FIELD]
            phases.add(frame["groups"][-1]["origin"])
            if index == 0:
                altered = deepcopy(pending.continuation)
                changed = altered["priority_response"][WHOLE_ZONE_COST_FRAME_FIELD]["groups"][-1]
                changed["members"].clear()
                with self.assertRaises(ReplacementEffectError):
                    ReplacementContinuation.from_dict(altered)
            self.assertEqual("hand", game.state.cards[spell.object_id].zone)
            self.assertEqual("battlefield", game.state.cards[body.object_id].zone)
            self.assertEqual("battlefield", game.state.cards[keeper.object_id].zone)
            self.assertFalse(game.state.stack)
            for key, amount in self.payment.items():
                self.assertEqual(amount, game.state.players["A"].mana_pool[key])
            for seat in "BCD":
                packet = json.dumps(game.packet("pilot:" + seat, full=True))
                self.assertNotIn("replacement.order", packet)
                for hidden in hand_cards:
                    self.assertNotIn(hidden.ref, packet)
            game = self.replay(game, load=True)
            payload = game.packet("pilot:A", full=True)["decision"]["ctx"]
            command = {"action_id": "choose", "replacement": payload["options"][0]["id"]}
            if payload.get("event_order_options"):
                command["replacement_event"] = payload["event_order_options"][0]
            before = authoritative_state_hash(game.state)
            self.assertFalse(game.act("pilot:D", command).ok)
            self.assertEqual(before, authoritative_state_hash(game.state))
            chosen = game.act("pilot:A", command)
            self.assertTrue(chosen.ok, chosen.summary)
        self.assertEqual(phases, {"battlefield", "hand"})
        self.assertEqual("stack", game.state.cards[spell.object_id].zone)
        self.assertEqual({"exile"}, {game.state.cards[c.object_id].zone for c in (keeper, body, *hand_cards)})
        self.replay(game, load=True)

    def test_whole_zone_ignores_no_resources_only_after_mana_and_rejects_client_members(self):
        game = self.session(601_806)
        self.clear_opening_hand(game)
        spell = self.add(game.engine, self.spell, zone="hand")
        fuel = self.add(game.engine, self.disposable)
        available = self.payment.copy()
        available.pop("B")
        self.prepare(game, mana=available)
        self.checkpoint(game)
        option = self.selected(game, spell, discard_first=False)
        command = self.command(spell, option, pay="auto")
        before = authoritative_state_hash(game.state)
        for delta in ({"discard_cards":[]}, {"sacrifice_cards":[fuel.ref]}, {"cost_cards":[fuel.ref]},
                      {WHOLE_ZONE_COST_FRAME_FIELD:{"schema_version":1}}, {"cost_option":"other-payment"}):
            self.assertFalse(game.act("pilot:A", {**command, **delta}).ok)
            self.assertEqual(before, authoritative_state_hash(game.state))
        owned = game.act("pilot:A", command)
        self.assertTrue(owned.ok, owned.summary)
        self.assertEqual("stack", game.state.cards[spell.object_id].zone)
        self.assertEqual("graveyard", game.state.cards[fuel.object_id].zone)
        self.assertFalse(game.state.players["A"].zones["battlefield"])
        self.assertFalse(game.state.players["A"].zones["hand"])
        costs = [event for event in game.state.events if event.code in {"cost.sacrifice", "cost.discard"}]
        self.assertFalse(costs, costs)
        self.replay(game, load=True)

    def test_whole_zone_card_admission_withdraws_its_own_authority(self):
        source = self.db.lookup(self.spell)
        def compiled(record, registry):
            return compile_oracle_card(record, capability_registry=registry, capability_profile="commander_review")
        exact = compiled(source, self.registry)
        self.assertEqual("exact", exact.status, exact.material_residuals)
        nodes = [node for face in exact.faces for node in face.nodes if node.event == "resolve"]
        self.assertTrue(any(WHOLE_DISCARD_SACRIFICE_CAPABILITY in node.capability_dependencies for node in nodes))
        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        row = next(row for row in raw["capabilities"] if row["id"] == WHOLE_DISCARD_SACRIFICE_CAPABILITY)
        row.update(status="blocked", blockers=["Automatic zone payment owner withdrawn."])
        withdrawn = CapabilityRegistry(raw)
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        index = build_index(registry_value=raw, declaration_source=declarations,
                            discovered_test_ids=set(discover_tests(ROOT).values()))
        withdrawn.mark_evidence_verified(validate_capability_evidence_index(index, registry=withdrawn))
        self.assertNotEqual("exact", compiled(source, withdrawn).status)
        self.assertEqual("exact", compiled(self.db.lookup("Cinder's Verdict"), withdrawn).status)
        malformed = replace(source, oracle_text=source.oracle_text.replace("your hand", "an opponent's hand"))
        self.assertNotEqual("exact", compiled(malformed, self.registry).status)
        with patch("quorune.compiler.spell_additional_cost_nodes.fixed_discard_sacrifice_template", return_value=None):
            self.assertNotEqual("exact", compiled(source, self.registry).status)
