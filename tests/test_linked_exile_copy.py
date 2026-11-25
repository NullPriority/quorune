"""Focused native evidence for a persistent copy of a linked exile target."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.continuous_effect_model import ContinuousObjectIdentity
from quorune.deck import DeckDefinition, DeckEntry
from quorune.linked_ability_exile_state import record_linked_ability_exile, retain_linked_graveyard_entry_source
from quorune.linked_exile_return_model import ExiledObjectIdentity
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry
from quorune.rules.evidence import validate_capability_evidence_index
from quorune.rules.evidence_storage import read_evidence_document
from quorune.rules.linked_exile_copy import LINKED_EXILE_COPY_CAPABILITY, LINKED_EXILE_COPY_PAIR
from quorune.rules.linked_graveyard_entry import LINKED_GRAVEYARD_ENTRY_CAPABILITY
from quorune.zone_transitions import ZoneTransitionOwner
from scripts.build_test_database import build_fixture_database
from scripts.update_capability_evidence import build_index, discover_tests
import test_temporary_self_copy as temporary_copy_witness


class LinkedExileCopyTests(InheritedNativeFixture, unittest.TestCase):
    source_name = "Trial Exile Mimic"
    first_model = "Generic Bound Body"
    second_model = "Trial Exile Golem"
    basic = "Generic Bound Plains"
    copy_spell = "Trial Twin Sign"
    quote = temporary_copy_witness.TemporarySelfCopyTests.quote
    activate = temporary_copy_witness.TemporarySelfCopyTests.activate

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "linked-exile-copy.sqlite3"
        fixtures = ROOT / "tests/fixtures"
        build_fixture_database([
            fixtures / "bound-effect-program-cards.json", fixtures / "linked-exile-copy-cards.json",
            fixtures / "announced-x-copy-entry-cards.json",
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = "Generic Bound Commander"
        cls.deck = DeckDefinition("Linked exile copy", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], [commander])

    def _linked_setup(self, seed, *, mana):
        game = self.session(seed)
        engine = game.engine
        source = self.add(engine, self.source_name, ref="COPY-SOURCE")
        source.counters["+1/+1"] = 3
        known_first = self.add(engine, self.first_model, seat="B", zone="graveyard", ref="FIRST-EXILE")
        known_second = self.add(engine, self.second_model, seat="C", zone="graveyard", ref="SECOND-EXILE")
        decoy = self.add(engine, self.first_model, seat="D", zone="graveyard", ref="UNLINKED-EXILE")
        hidden = self.add(engine, self.first_model, seat="D", zone="hand", ref="HIDDEN-HAND-CARD")
        owner = ZoneTransitionOwner(engine)
        first, second, decoy = owner.move_cards_simultaneously(
            [(card.object_id, "exile") for card in (known_first, known_second, decoy)],
            reason="Fixture creates actual exile incarnations",
        )
        record_linked_ability_exile(engine,
            source=ContinuousObjectIdentity(source.object_id, source.logical_object_id),
            ability_pair=LINKED_EXILE_COPY_PAIR,
            objects=tuple(ExiledObjectIdentity(card.object_id, card.ref, card.logical_object_id)
                          for card in (first, second)),
        )
        retain_linked_graveyard_entry_source(source)
        self.assert_closed(game, source)
        self.prepare(game, mana={"C": mana})
        self.checkpoint(game)
        return game, source, first, second, decoy, hidden

    def test_paid_linked_copy_retains_its_activation_and_survives_cleanup(self):
        game, source, first, second, decoy, hidden = self._linked_setup(607_250_01, mana=4)
        offer = self.quote(game, source)
        self.assertNotIn(hidden.ref, json.dumps(offer))
        self.assertNotIn(decoy.ref, json.dumps(offer))
        before = authoritative_state_hash(game.state)
        rejected = game.act("pilot:B", {"action_id": offer["id"], "targets": [first.ref], "pay": "auto"})
        self.assertFalse(rejected.ok, rejected.summary)
        self.assertEqual(before, authoritative_state_hash(game.state))
        self.activate(game, source, first, reject=(decoy, hidden))
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        current = game.state.cards[source.object_id]
        self.assertEqual(self.first_model, game.engine.display_name(current.object_id))
        self.assertEqual(3, game.engine._numeric_stat(current.object_id, "power"))
        paid_pool = game.state.players["A"].mana_pool
        self.assertEqual(2, paid_pool.get("C", 0))
        self.activate(game, current, second, reject=(decoy,))
        self.assertIsNone(self.resolve(game))
        self.assertEqual(self.second_model, game.engine.display_name(current.object_id))
        self.assertEqual(3, game.engine._numeric_stat(current.object_id, "toughness"))
        turn = game.state.turn_sequence
        self.until(game, lambda session: session.state.turn_sequence > turn)
        current = game.state.cards[source.object_id]
        self.assertEqual((source.logical_object_id, 3),
                         (current.logical_object_id, current.counters["+1/+1"]))
        self.assertEqual(self.second_model, game.engine.display_name(current.object_id))
        self.assertTrue(any(a.ability_id.endswith(":linked-exile-copy")
                            for a in game.engine._activated_abilities(current)))
        self.replay(game, load=True)

    def test_announced_linked_target_does_not_follow_a_new_exile_incarnation(self):
        game, source, first, _second, _decoy, _hidden = self._linked_setup(607_250_02, mana=2)
        self.activate(game, source, first)
        owner = ZoneTransitionOwner(game.engine)
        identity = first.logical_object_id
        owner.move_card(first.object_id, "graveyard", reason="Other effect removes the selected card")
        current = owner.move_card(first.object_id, "exile", reason="The same card enters exile again")
        record_linked_ability_exile(game.engine,
            source=ContinuousObjectIdentity(source.object_id, source.logical_object_id),
            ability_pair=LINKED_EXILE_COPY_PAIR,
            objects=(ExiledObjectIdentity(current.object_id, current.ref, current.logical_object_id),),
        )
        self.assertNotEqual(identity, current.logical_object_id)
        self.checkpoint(game)
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(self.source_name, game.engine.display_name(source.object_id))
        self.assertEqual(0, game.state.players["A"].mana_pool.get("C", 0))
        self.replay(game, load=True)

    def test_linked_copy_withdrawal_and_unrepresented_price_are_rejected(self):
        card = self.db.lookup(self.source_name)
        def compile_record(record, registry):
            return compile_oracle_card(record, capability_registry=registry, capability_profile="commander_review")
        result = compile_record(card, self.registry)
        self.assertEqual("exact", result.status, result.material_residuals)
        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        cap = next(row for row in raw["capabilities"] if row["id"] == LINKED_EXILE_COPY_CAPABILITY)
        cap.update(status="blocked", blockers=["Linked exile copy authority withdrawn."])
        withdrawn = CapabilityRegistry(raw)
        source = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        evidence = build_index(registry_value=raw, declaration_source=source,
                               discovered_test_ids=set(discover_tests(ROOT).values()))
        withdrawn.mark_evidence_verified(validate_capability_evidence_index(evidence, registry=withdrawn))
        self.assertNotEqual("exact", compile_record(card, withdrawn).status)
        with patch("quorune.compiler.temporary_self_copy_templates.linked_exile_self_copy_template",
                   return_value=None):
            self.assertNotEqual("exact", compile_record(card, self.registry).status)
        cap["status"] = "trusted"
        cap["blockers"] = []
        entry = next(row for row in raw["capabilities"] if row["id"] == LINKED_GRAVEYARD_ENTRY_CAPABILITY)
        entry.update(status="blocked", blockers=["Linked graveyard entry authority withdrawn."])
        withdrawn_entry = CapabilityRegistry(raw)
        entry_evidence = build_index(registry_value=raw, declaration_source=source,
                                     discovered_test_ids=set(discover_tests(ROOT).values()))
        withdrawn_entry.mark_evidence_verified(validate_capability_evidence_index(entry_evidence, registry=withdrawn_entry))
        self.assertNotEqual("exact", compile_record(card, withdrawn_entry).status)
        with patch("quorune.compiler.linked_graveyard_entry_templates.linked_graveyard_entry_node",
                   return_value=None):
            self.assertNotEqual("exact", compile_record(card, self.registry).status)
        from dataclasses import replace
        other_price = replace(card, oracle_text=card.oracle_text.replace("{2}:", "{2}, {T}:"))
        self.assertNotEqual("exact", compile_record(other_price, self.registry).status)
        for qualifier in ("Activate only as a sorcery.", "Activate only once each turn."):
            with self.subTest(qualifier=qualifier):
                restricted = replace(card, oracle_text=f"{card.oracle_text} {qualifier}")
                self.assertNotEqual("exact", compile_record(restricted, self.registry).status)


    def _cast_announced_entry(self, game, source, x):
        self.priority(game, "A")
        offer, = [action for action in game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
                  if action["id"] == "cast:" + source.ref]
        command = {"action_id": offer["id"], "pay": "auto", "x": x}
        accepted = game.act("pilot:A", command)
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertEqual(x, game.state.stack[-1].x_value)

    def _entry_choice(self, game, *, decline=False):
        pending = game.state.pending_decision
        self.assertEqual(("replacement.order", ["A"]), (pending.kind, pending.actors))
        context = game.packet("pilot:A", full=True)["decision"]["ctx"]
        option, = [row for row in context["options"] if bool(row.get("decline")) == decline]
        return option["id"], context

    def test_native_announced_entry_exiles_selected_own_creatures_links_and_replays(self):
        game = self.session(607_250_03)
        engine = game.engine
        source = self.add(engine, self.source_name, zone="hand", ref="ANNOUNCED-LINKED-SOURCE")
        first = self.add(engine, self.first_model, zone="graveyard", ref="ENTRY-FIRST")
        second = self.add(engine, self.second_model, zone="graveyard", ref="ENTRY-SECOND")
        third = self.add(engine, self.first_model, zone="graveyard", ref="ENTRY-UNSELECTED")
        foreign = self.add(engine, self.first_model, seat="B", zone="graveyard", ref="OTHER-GRAVE")
        land = self.add(engine, self.basic, zone="graveyard", ref="OWN-GRAVE-LAND")
        hidden = self.add(engine, self.first_model, seat="C", zone="hand", ref="HIDDEN-ENTRY-REF")
        self.assert_closed(game, source)
        self.prepare(game, mana={"C": 4, "B": 1, "G": 1, "U": 1})
        self.checkpoint(game)
        self._cast_announced_entry(game, source, 2)
        self.assertIsNotNone(self.resolve(game))
        choice, context = self._entry_choice(game)
        contract = context["object_choices"][choice]
        self.assertEqual((1, 2), (contract["minimum"], contract["maximum"]))
        self.assertEqual({first.ref, second.ref, third.ref}, set(contract["legal_refs"]))
        self.assertNotIn(hidden.ref, json.dumps(context))
        self.assertNotIn(land.ref, contract["legal_refs"])
        game = self.replay(game, load=True)
        command = {"action_id": "choose", "replacement": choice, "replacement_objects": [first.ref]}
        before = authoritative_state_hash(game.state)
        for actor, refs in (("B", [first.ref]), ("A", [first.ref, second.ref, third.ref]),
                            ("A", [first.ref, first.ref]), ("A", [foreign.ref])):
            rejected = game.act("pilot:" + actor, {**command, "replacement_objects": refs})
            self.assertFalse(rejected.ok, rejected.summary)
            self.assertEqual(before, authoritative_state_hash(game.state))
        accepted = game.act("pilot:A", command)
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertIsNone(self.resolve(game))
        current = game.state.cards[source.object_id]
        self.assertEqual(("battlefield", 3), (current.zone, current.counters.get("+1/+1")))
        self.assertEqual(("exile", "graveyard", "graveyard", "graveyard", "graveyard", "hand"),
                         tuple(game.state.cards[card.object_id].zone for card in (first, second, third, foreign, land, hidden)))
        self.activate(game, current, first, reject=(second, hidden))
        self.assertIsNone(self.resolve(game))
        self.assertEqual(self.first_model, game.engine.display_name(current.object_id))
        self.assertEqual(3, game.engine._numeric_stat(current.object_id, "power"))
        self.replay(game, load=True)

    def test_zero_x_and_declined_entry_do_not_exile_creatures(self):
        for x in (0, 2):
            with self.subTest(x=x):
                game = self.session(607_250_04 + x)
                source = self.add(game.engine, self.source_name, zone="hand", ref="ZERO-ENTRY")
                model = self.add(game.engine, self.first_model, zone="graveyard", ref="UNCHOSEN-MODEL")
                self.prepare(game, mana={"C": x, "B": 1, "G": 1, "U": 1})
                self.checkpoint(game)
                self._cast_announced_entry(game, source, x)
                pending = self.resolve(game)
                if x:
                    self.assertIsNotNone(pending)
                    choice, _context = self._entry_choice(game, decline=True)
                    result = game.act("pilot:A", {"action_id": "choose", "replacement": choice})
                    self.assertTrue(result.ok, result.summary)
                    self.assertIsNone(self.resolve(game))
                else:
                    self.assertIsNone(pending)
                self.assertEqual("graveyard", game.state.cards[source.object_id].zone)
                self.assertEqual("graveyard", game.state.cards[model.object_id].zone)
                self.replay(game, load=True)


    def test_copy_of_the_same_printed_source_keeps_only_the_original_linked_activation(self):
        self.first_model = self.source_name
        game, source, first, second, decoy, _hidden = self._linked_setup(607_250_07, mana=4)
        self.activate(game, source, first)
        self.assertIsNone(self.resolve(game))
        current = game.state.cards[source.object_id]
        offers = [
            action for action in game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
            if action["id"].startswith("activate:" + current.ref + ":")
        ]
        self.assertEqual(1, len(offers))
        self.assertTrue(offers[0]["id"].endswith(":linked-exile-copy"))
        self.activate(game, current, second, reject=(decoy,))
        self.assertIsNone(self.resolve(game))
        self.assertEqual(self.second_model, game.engine.display_name(current.object_id))
        self.replay(game, load=True)

    def test_permanent_spell_copy_keeps_x_and_links_each_entry_separately(self):
        game = self.session(607_250_08)
        source = self.add(game.engine, self.source_name, zone="hand", ref="FIRST-CREATURE-SPELL")
        fork = self.add(game.engine, self.copy_spell, zone="hand", ref="COPY-CREATURE-SPELL")
        first = self.add(game.engine, self.first_model, zone="graveyard", ref="COPY-ENTRY-ONLY")
        second = self.add(game.engine, self.second_model, zone="graveyard", ref="ORIGINAL-ENTRY-ONLY")
        self.assert_closed(game, source)
        self.assert_closed(game, fork)
        self.prepare(game, mana={"C": 6, "B": 1, "G": 2, "U": 2})
        self.checkpoint(game)
        self._cast_announced_entry(game, source, 2)
        original = game.state.stack[-1]
        self.cast(game, "A", fork, [original.ref])
        self.resolve_top(game)
        copied, = [item for item in game.state.stack if item.kind == "spell_copy"]
        self.assertEqual(2, copied.x_value)
        self.assertIsNotNone(self.resolve(game))
        choice, context = self._entry_choice(game)
        self.assertEqual({first.ref, second.ref}, set(context["object_choices"][choice]["legal_refs"]))
        game = self.replay(game, load=True)
        accepted = game.act("pilot:A", {"action_id": "choose", "replacement": choice, "replacement_objects": [first.ref]})
        self.assertTrue(accepted.ok, accepted.summary)
        self.until(game, lambda s: s.state.cards[copied.card_object_id].zone == "battlefield")
        self.assertIsNotNone(self.resolve(game))
        choice, context = self._entry_choice(game)
        self.assertEqual([second.ref], context["object_choices"][choice]["legal_refs"])
        accepted = game.act("pilot:A", {"action_id": "choose", "replacement": choice, "replacement_objects": [second.ref]})
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertIsNone(self.resolve(game))
        token = game.state.cards[copied.card_object_id]
        main = game.state.cards[source.object_id]
        self.assertTrue(token.is_token)
        self.assertEqual((3, 3), (token.counters.get("+1/+1"), main.counters.get("+1/+1")))
        self.activate(game, token, first, reject=(second,))
        self.assertIsNone(self.resolve(game))
        self.activate(game, main, second, reject=(first,))
        self.assertIsNone(self.resolve(game))
        self.assertEqual((self.first_model, self.second_model),
                         tuple(game.engine.display_name(card.object_id) for card in (token, main)))
        self.replay(game, load=True)


    def test_new_copy_of_the_same_model_refreshes_its_once_per_turn_ability(self):
        self.first_model = "Trial Once Per Turn"
        game, source, first, _second, _decoy, _hidden = self._linked_setup(602_500_03, mana=4)
        self.activate(game, source, first)
        self.assertIsNone(self.resolve(game))
        current = game.state.cards[source.object_id]

        def copied_options():
            self.priority(game, "A")
            return [action for action in game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
                    if action["id"].startswith("activate:" + current.ref + ":")
                    and not action["id"].endswith(":linked-exile-copy")]

        initial, = copied_options()
        used = game.act("pilot:A", {"action_id": initial["id"], "pay": "auto"})
        self.assertTrue(used.ok, used.summary)
        self.assertIsNone(self.resolve(game))
        self.assertEqual([], copied_options())

        self.activate(game, current, first)
        self.assertIsNone(self.resolve(game))
        offered, = copied_options()
        spent = game.act("pilot:A", {"action_id": offered["id"], "pay": "auto"})
        self.assertTrue(spent.ok, spent.summary)
        self.assertIsNone(self.resolve(game))
        self.assertEqual([], copied_options())
        self.replay(game, load=True)
