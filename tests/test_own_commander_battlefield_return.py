"""Native paid return of an owned, physically designated battlefield commander."""

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
from quorune.record import authoritative_state_hash
from quorune.replacement.immutable import thaw_value
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.owned_commander_battlefield_return import CAPABILITY
from quorune.rules.commander_return_choice import COMMANDER_BATTLEFIELD_ENTRY_CAPABILITY, COMMANDER_RETURN_FIELD
from scripts.build_test_database import build_fixture_database


CARD = "Sanctum of Eternity"
COMMANDER = "Grist, the Hunger Tide"

COURSER = "Hellkite Courser"
PARTNERS = ("Rograkh, Son of Rohgahh", "Tymna the Weaver")


class OwnCommanderEntryAndReturnTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        database = Path(cls.temporary.name) / "own-commander-entry.sqlite3"
        fixtures = ROOT / "tests/fixtures"
        build_fixture_database([
            fixtures / "bound-effect-program-cards.json",
            fixtures / "all-blocker-requirement-cards.json",
            fixtures / "commander-pairing-cards.json",
            fixtures / "activation-paid-creature-subtype-cards.json",
            fixtures / "own-commander-battlefield-return-cards.json",
        ], database)
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Owned partner entry", [
            *(DeckEntry(name, 1, "commander") for name in PARTNERS),
            DeckEntry("Generic Bound Plains", 30),
        ], list(PARTNERS))

    def _owned_commanders(self, game, owner="A"):
        return {
            card.printed_name: card for card in game.state.cards.values()
            if card.owner == owner and card.zone == "command" and card.is_commander
        }

    def _choose(self, game, *cards, actor="A", valid=True):
        before = authoritative_state_hash(game.state)
        command = {"action_id": "choose", "objects": [card.ref for card in cards]}
        result = game.act("pilot:" + actor, command)
        self.assertEqual(valid, result.ok, result.summary)
        if not valid:
            self.assertEqual(before, authoritative_state_hash(game.state))

    def _offered(self, game):
        self.assertEqual(["pilot:A"], list(game.pending_principals()))
        decision = game.packet("pilot:A", full=True)["decision"]
        self.assertEqual("semantic.choice", decision["kind"])
        context = decision["ctx"]
        return {row["id"] for row in context["objects"]}

    def _advance_to_delayed_return(self, game):
        self.until(game, lambda current: current.state.step == "end_step" and any(
            item.context.get("delayed_trigger_ref") for item in current.state.stack))
        return self.replay(game, load=True)

    def test_original_closed_trigger_and_mutation_fail_closed(self):
        card = self.db.lookup(COURSER, fuzzy=False)

        def compile_card(original):
            return compile_oracle_card(original, capability_registry=self.registry,
                                       capability_profile="commander_review")

        ir = compile_card(card)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        trigger, = [node for face in ir.faces for node in face.nodes
                    if COMMANDER_BATTLEFIELD_ENTRY_CAPABILITY in node.capability_dependencies]
        self.assertEqual(trigger.text, card.oracle_text[trigger.span.start:trigger.span.end])
        self.assertEqual("permanent.enter.self", trigger.event)
        self.assertIsNone(trigger.target_schema)
        self.assertEqual({"schema_version": 2}, trigger.effects[0][COMMANDER_RETURN_FIELD])
        for before, after in (("you may put", "put"), ("you own", "an opponent owns"),
                              ("gains haste", "gains flying"), ("the next end step", "your next end step")):
            with self.subTest(after=after):
                changed = replace(card, oracle_text=card.oracle_text.replace(before, after))
                self.assertNotEqual("exact", compile_card(changed).status)
        with patch("quorune.compiler.source_self_effect_templates.commander_battlefield_entry_result",
                   return_value=None):
            self.assertEqual("partial", compile_card(card).status)

    def test_native_partner_choice_survives_source_departure_and_returns_without_casting(self):
        game = self.session(90380601)
        source = self.add(game.engine, COURSER, zone="hand")
        bounce = self.add(game.engine, "Unsummon", seat="B", zone="hand")
        owned = self._owned_commanders(game)
        chosen, other = (owned[name] for name in PARTNERS)
        foreign = self._owned_commanders(game, "B")[PARTNERS[0]]
        unmarked = self.add(game.engine, PARTNERS[0], zone="command", ref="undesignated-command-card")
        self.assertFalse(unmarked.is_commander)
        self.assert_closed(game, source)
        self.prepare(game, mana={"C": 4, "R": 2})
        game.state.players["B"].mana_pool["U"] = 1
        game.state.players["A"].commander_casts[chosen.oracle_id] = 2
        counts = dict(game.state.players["A"].commander_casts)
        self.checkpoint(game)
        self.cast_for(game, "A", source)
        self.resolve_top(game)
        self.cast_for(game, "B", bounce, [source.ref])
        self.resolve_top(game)
        self.assertEqual("hand", game.state.cards[source.object_id].zone)
        self.assertEqual("semantic.choice", self.resolve(game).kind)
        self.assertEqual({chosen.ref, other.ref}, self._offered(game))
        self._choose(game, foreign, valid=False)
        self._choose(game, unmarked, valid=False)
        self._choose(game, chosen, actor="B", valid=False)
        game = self.replay(game, load=True)
        self._choose(game, chosen)
        arrival = game.state.cards[chosen.object_id]
        self.assertEqual(("battlefield", "A", chosen.commander_designation_id),
                         (arrival.zone, arrival.controller, arrival.commander_designation_id))
        self.assertIn("haste", game.engine._combat_keywords(arrival))
        self.assertEqual(counts, game.state.players["A"].commander_casts)
        self.assertEqual("command", game.state.cards[other.object_id].zone)
        arrived = arrival.logical_object_id
        self.assertNotEqual(chosen.logical_object_id, arrived)
        self.assertEqual(1, len(game.state.delayed_triggers))
        game = self._advance_to_delayed_return(game)
        self.assertIsNone(self.resolve(game))
        finished = game.state.cards[chosen.object_id]
        self.assertEqual(("command", "A", chosen.commander_designation_id),
                         (finished.zone, finished.owner, finished.commander_designation_id))
        self.assertNotEqual(arrived, finished.logical_object_id)
        self.assertEqual(counts, game.state.players["A"].commander_casts)
        self.replay(game, load=True)

    def test_single_choice_is_optional_and_empty_pool_auto_continues(self):
        for empty in (False, True):
            with self.subTest(empty=empty):
                game = self.session(90380610 + empty)
                source = self.add(game.engine, COURSER, zone="hand")
                owned = self._owned_commanders(game)
                for name in (PARTNERS if empty else PARTNERS[1:]):
                    card = owned[name]
                    game.engine.move_card(card.object_id, "battlefield", controller="A", reason="Prepared battlefield")
                self.assert_closed(game, source)
                self.prepare(game, mana={"C": 4, "R": 2})
                self.checkpoint(game)
                self.cast_for(game, "A", source)
                pending = self.resolve(game)
                if empty:
                    self.assertIsNone(pending)
                else:
                    self.assertEqual("semantic.choice", pending.kind)
                    self.assertEqual({owned[PARTNERS[0]].ref}, self._offered(game))
                    self._choose(game)
                self.assertFalse(game.state.delayed_triggers)
                self.assertFalse(game.state.stack)
                self.replay(game, load=True)

    def test_reentered_commander_does_not_inherit_haste_or_delayed_return(self):
        game = self.session(90380620)
        source = self.add(game.engine, COURSER, zone="hand")
        blink = self.add(game.engine, "Cloudshift", zone="hand")
        chosen = self._owned_commanders(game)[PARTNERS[0]]
        self.assert_closed(game, source)
        self.assert_closed(game, blink)
        self.prepare(game, mana={"C": 4, "R": 2, "W": 1})
        self.checkpoint(game)
        self.cast_for(game, "A", source, reserve={"W": 1})
        self.assertEqual("semantic.choice", self.resolve(game).kind)
        self._choose(game, chosen)
        arrival = game.state.cards[chosen.object_id]
        self.assertIn("haste", game.engine._combat_keywords(arrival))
        first_incarnation = arrival.logical_object_id
        self.cast_for(game, "A", blink, [arrival.ref])
        self.assertIsNone(self.resolve(game))
        replacement = game.state.cards[chosen.object_id]
        self.assertNotEqual(first_incarnation, replacement.logical_object_id)
        self.assertNotIn("haste", game.engine._combat_keywords(replacement))
        game = self._advance_to_delayed_return(game)
        self.assertIsNone(self.resolve(game))
        finished = game.state.cards[chosen.object_id]
        self.assertEqual(("battlefield", replacement.logical_object_id),
                         (finished.zone, finished.logical_object_id))
        self.replay(game, load=True)


class OwnCommanderBattlefieldReturnTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        database = Path(cls.temporary.name) / "own-commander-battlefield-return.sqlite3"
        fixtures = ROOT / "tests/fixtures"
        build_fixture_database([
            fixtures / "paid-commander-lifelink-cards.json",
            fixtures / "own-commander-battlefield-return-cards.json",
        ], database)
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Commander battlefield return", [
            DeckEntry(COMMANDER, 1, "commander"), DeckEntry("Forest", 30),
        ], [COMMANDER])

    def commander(self, game, owner, controller):
        card = next(card for card in game.state.cards.values() if card.owner == owner and card.zone == "command")
        return game.engine.move_card(card.object_id, "battlefield", controller=controller, reason="Commander setup")

    def activate(self, game, source, target, *, ability="ab2", accepted=True):
        self.priority(game, "A")
        before = authoritative_state_hash(game.state)
        answer = game.act("pilot:A", {
            "action_id": f"activate:{source.ref}:{ability}", "targets": [target.ref], "pay": "auto",
        })
        self.assertEqual(accepted, answer.ok, answer.summary)
        if not accepted:
            self.assertEqual(before, authoritative_state_hash(game.state))

    def test_actual_owned_foreign_controlled_commander_uses_owner_choice_and_replays(self):
        for decline, destination in ((True, "hand"), (False, "command")):
            with self.subTest(destination=destination):
                game = self.session(90396200 + int(decline))
                source = self.add(game.engine, CARD)
                clinic = self.add(game.engine, "Witch's Clinic")
                owned = self.commander(game, "A", "B")
                foreign = self.commander(game, "C", "C")
                duplicate = self.add(game.engine, COMMANDER, ref="copy-without-designation")
                designation = owned.commander_designation_id
                old_logical_id = owned.logical_object_id
                self.assertEqual(("A", "B"), (owned.owner, owned.controller))
                self.assertEqual({"planeswalker"}, game.engine._type_parts(
                    game.engine._effective_card_data(owned)["type_line"])[0])
                self.assertFalse(duplicate.is_commander)
                self.assert_closed(game, source)
                self.prepare(game, mana={"C": 4})
                self.checkpoint(game)
                self.activate(game, source, foreign, accepted=False)
                self.activate(game, source, duplicate, accepted=False)
                self.activate(game, clinic, foreign)
                self.assertEqual(1, len(game.state.stack))
                self.activate(game, source, owned)
                self.assertEqual(2, len(game.state.stack))
                self.assertTrue(game.state.cards[source.object_id].tapped)
                self.assertEqual(0, game.state.players["A"].mana_pool["C"])
                pending = self.resolve(game)
                self.assertEqual("replacement.order", pending.kind)
                self.assertEqual(["pilot:A"], list(game.pending_principals()))
                self.assertEqual("battlefield", game.state.cards[owned.object_id].zone)
                game = self.replay(game, load=True)
                options = game.packet("pilot:A", full=True)["decision"]["ctx"]["options"]
                chosen = next(option for option in options if bool(option.get("decline")) is decline)
                command = {"action_id": "choose", "replacement": chosen["id"]}
                before = authoritative_state_hash(game.state)
                self.assertFalse(game.act("pilot:B", command).ok)
                self.assertEqual(before, authoritative_state_hash(game.state))
                accepted = game.act("pilot:A", command)
                self.assertTrue(accepted.ok, accepted.summary)
                self.assertIsNone(self.resolve(game))
                arrived = game.state.cards[owned.object_id]
                self.assertEqual((destination, "A", designation),
                                 (arrived.zone, arrived.owner, arrived.commander_designation_id))
                self.assertNotEqual(old_logical_id, arrived.logical_object_id)
                self.assertEqual("battlefield", game.state.cards[foreign.object_id].zone)
                self.assertEqual("battlefield", game.state.cards[duplicate.object_id].zone)
                self.replay(game, load=True)

    def test_actual_opponent_turn_rejects_return_and_keeps_colorless_mana_offer(self):
        game = self.session(90396202)
        source = self.add(game.engine, CARD)
        commander = self.commander(game, "A", "B")
        self.prepare(game, actor="A", active="B", mana={"C": 2})
        self.checkpoint(game)
        self.activate(game, source, commander, accepted=False)
        self.assertFalse(game.state.cards[source.object_id].tapped)
        self.assertEqual(2, game.state.players["A"].mana_pool["C"])
        paid = game.act("pilot:A", {"action_id": f"activate:{source.ref}:ab1"})
        self.assertTrue(paid.ok, paid.summary)
        self.assertEqual(3, game.state.players["A"].mana_pool["C"])
        self.assertTrue(game.state.cards[source.object_id].tapped)
        self.replay(game, load=True)

    def test_closed_return_authority_rejects_owner_timing_and_price_mutations(self):
        card = self.db.lookup(CARD, fuzzy=False)
        ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile="commander_review")
        self.assertEqual("exact", ir.status, ir.material_residuals)
        mana, return_node = ir.faces[0].nodes
        self.assertEqual("mana_ability", mana.kind)
        self.assertIn(CAPABILITY, return_node.capability_dependencies)
        game = self.session(90396203)
        source = self.add(game.engine, CARD)
        self.assert_closed(game, source)
        program, = (program for program in game.engine.semantics.programs_for_oracle(source.oracle_id)
                    if CAPABILITY in program.capability_dependencies)
        descriptor, = (thaw_value(item) for item in program.handlers)
        descriptor["ability"]["activation_conditions"] = []
        for mutant in (
            replace(program, target_schema={**program.target_schema, "owner_relation": "any"}),
            replace(program, handlers=(descriptor,)),
            replace(program, cost_schema={**program.cost_schema, "tap_source": False}),
        ):
            with self.subTest(program=mutant):
                result = bind_semantic_program_runtime(mutant, capability_registry=self.registry,
                                                       profile="commander_review")
                self.assertFalse(result["strict"], result)
