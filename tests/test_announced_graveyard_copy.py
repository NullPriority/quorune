"""Paid and replayed announced-value copies of the controller's graveyard cards."""

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
from quorune.entry_copy import COPIABLE_ORACLE_FIELD
from quorune.granted_activation_model import GrantedActivatedAbilitySpec
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.announced_graveyard_copy import ANNOUNCED_GRAVEYARD_COPY_CAPABILITY
from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry
from quorune.rules.evidence import validate_capability_evidence_index
from quorune.rules.evidence_storage import read_evidence_document
from quorune.zone_transitions import ZoneTransitionOwner
from scripts.build_test_database import build_fixture_database
from scripts.update_capability_evidence import build_index, discover_tests


class AnnouncedGraveyardCopyTests(InheritedNativeFixture, unittest.TestCase):
    source_name = "Trial Named Mimic"
    two = "Trial Other Form"
    alternate_two = "Ashcoat Bear"
    zero = "Trial Variable Walker"
    expensive = "Trial Distant Sentinel"
    basic = "Generic Bound Plains"

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        database = Path(cls.temporary.name) / "announced-graveyard-copy.sqlite3"
        fixtures = ROOT / "tests/fixtures"
        build_fixture_database([
            fixtures / "bound-effect-program-cards.json",
            fixtures / "announced-graveyard-copy-cards.json",
            fixtures / "fixed-qualified-stack-copy-cards.json",
        ], database)
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        commander = "Generic Bound Commander"
        cls.deck = DeckDefinition("Announced graveyard copy", [
            DeckEntry(commander, 1, "commander"), DeckEntry(cls.basic, 30),
        ], [commander])

    def _offers(self, game, source):
        self.priority(game, "A")
        actions = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        return [row for row in actions
                if row["id"].startswith("activate:" + source.ref + ":")
                and row.get("choice_schema", {}).get("x")]

    def _activate(self, game, source, target, amount):
        offer, = self._offers(game, source)
        result = game.act("pilot:A", {"action_id": offer["id"], "targets": [target.ref],
                                      "pay": "auto", "x": amount})
        self.assertTrue(result.ok, result.summary)
        item = game.state.stack[-1]
        self.assertEqual(amount, item.x_value)
        self.assertEqual(amount, item.context["target_schema_override"]["mana_value"])
        return item

    def _prepare_source(self, game, source, mana):
        self.assert_closed(game, source)
        source.counters["+1/+1"] = 1
        self.prepare(game, mana={"C": mana})
        self.checkpoint(game)

    def test_exact_paid_named_copy_preserves_the_object_and_retains_x(self):
        game = self.session(202_300_11)
        engine = game.engine
        source = self.add(engine, self.source_name, ref="NAMED-COPY-SOURCE")
        two = self.add(engine, self.two, zone="graveyard", ref="OWN-TWO")
        zero = self.add(engine, self.zero, zone="graveyard", ref="OWN-X-ZERO")
        expensive = self.add(engine, self.expensive, zone="graveyard", ref="UNAFFORDABLE-OWN")
        foreign = self.add(engine, self.two, seat="B", zone="graveyard", ref="FOREIGN-TWO")
        land = self.add(engine, self.basic, zone="graveyard", ref="GRAVEYARD-LAND")
        hidden = self.add(engine, self.two, seat="C", zone="hand", ref="HIDDEN-OTHER")
        self._prepare_source(game, source, 3)
        offer, = self._offers(game, source)
        self.assertEqual({two.ref: {"minimum": 2, "maximum": 2},
                          zero.ref: {"minimum": 0, "maximum": 0}},
                         offer["target_schema"]["x_bounds_by_ref"])
        self.assertEqual({two.ref, zero.ref}, set(offer["target_schema"]["legal_refs"]))
        self.assertEqual((0, 2, 0), tuple(offer["choice_schema"]["x"][key]
                                       for key in ("minimum", "maximum", "default")))
        self.assertNotIn(hidden.ref, json.dumps(offer))
        before = authoritative_state_hash(game.state)
        for actor, target, amount in (("A", two, 3), ("A", two, 1), ("A", zero, 2),
                                      ("A", expensive, 5), ("A", foreign, 2), ("A", land, 0),
                                      ("A", hidden, 2), ("B", two, 2), ("A", zero, True)):
            denied = game.act("pilot:" + actor, {"action_id": offer["id"], "targets": [target.ref],
                                                   "pay": "auto", "x": amount})
            self.assertFalse(denied.ok, denied.summary)
            self.assertEqual(before, authoritative_state_hash(game.state))
        self._activate(game, source, two, 2)
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        current = game.state.cards[source.object_id]
        current_values = game.engine._effective_card_data(current)
        _, _, traits = game.engine._type_parts(current_values["type_line"])
        self.assertIn("legendary", traits)
        self.assertEqual(self.source_name, current_values["name"])
        self.assertEqual(3, game.engine._numeric_stat(current.object_id, "power"))
        self.assertEqual(1, game.state.players["A"].mana_pool.get("C", 0))
        second, = self._offers(game, current)
        self.assertTrue(second["id"].endswith(":announced-graveyard-copy"), second)
        self._activate(game, current, zero, 0)
        self.assertIsNone(self.resolve(game))
        turn = game.state.turn_sequence
        self.until(game, lambda state: state.state.turn_sequence > turn)
        current = game.state.cards[source.object_id]
        data = game.engine._effective_card_data(current)
        self.assertEqual((self.source_name, source.logical_object_id, 1),
                         (data["name"], current.logical_object_id, current.counters["+1/+1"]))
        self.assertEqual(0, float(data["mana_value"]))
        self.assertEqual(1, game.engine._numeric_stat(current.object_id, "power"))
        self.assertEqual(("graveyard", "graveyard"),
                         tuple(game.state.cards[card.object_id].zone for card in (two, zero)))
        self.replay(game, load=True)

    def test_offer_defaults_to_the_lowest_payable_mana_value(self):
        for mana in (1, 2):
            with self.subTest(mana=mana):
                game = self.session(202_300_21 + mana)
                source = self.add(game.engine, self.source_name, ref="PAYABLE-COPY-SOURCE")
                two = self.add(game.engine, self.two, zone="graveyard", ref="COPY-TWO")
                self.add(game.engine, self.expensive, zone="graveyard", ref="COPY-EXPENSIVE")
                self._prepare_source(game, source, mana)
                offers = self._offers(game, source)
                if mana == 1:
                    self.assertEqual([], offers)
                else:
                    offer, = offers
                    self.assertEqual([two.ref], offer["target_schema"]["legal_refs"])
                    self.assertEqual((2, 2, 2), tuple(offer["choice_schema"]["x"][key]
                                                   for key in ("minimum", "maximum", "default")))

    def test_target_returning_at_the_same_reference_does_not_receive_the_copy(self):
        game = self.session(202_300_33)
        source = self.add(game.engine, self.source_name, ref="STALE-COPY-SOURCE")
        target = self.add(game.engine, self.two, zone="graveyard", ref="DEPARTED-COPY-TARGET")
        self._prepare_source(game, source, 2)
        self._activate(game, source, target, 2)
        original = target.logical_object_id
        owner = ZoneTransitionOwner(game.engine)
        owner.move_card(target.object_id, "exile", reason="Remove the chosen graveyard incarnation")
        current = owner.move_card(target.object_id, "graveyard", reason="Return the same physical card")
        self.assertEqual(target.ref, current.ref)
        self.assertNotEqual(original, current.logical_object_id)
        self.checkpoint(game)
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertNotIn("copy_overrides", game.state.cards[source.object_id].annotations)
        self.assertEqual(0, game.state.players["A"].mana_pool.get("C", 0))
        self.replay(game, load=True)

    def test_pending_activation_does_not_apply_to_the_returned_source(self):
        game = self.session(202_300_43)
        source = self.add(game.engine, self.source_name, ref="DEPARTED-COPY-SOURCE")
        target = self.add(game.engine, self.two, zone="graveyard", ref="STILL-LEGAL-MODEL")
        self._prepare_source(game, source, 2)
        self._activate(game, source, target, 2)
        original = source.logical_object_id
        owner = ZoneTransitionOwner(game.engine)
        owner.move_card(source.object_id, "hand", reason="The original activated source leaves")
        current = owner.move_card(source.object_id, "battlefield", reason="The physical source returns")
        self.assertNotEqual(original, current.logical_object_id)
        self.checkpoint(game)
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertNotIn("copy_overrides", game.state.cards[source.object_id].annotations)
        self.assertEqual(0, game.state.players["A"].mana_pool.get("C", 0))
        self.replay(game, load=True)

    def test_announced_copy_capability_and_retained_cost_fail_closed(self):
        card = self.db.lookup(self.source_name)
        def compile_card(record, registry):
            return compile_oracle_card(record, capability_registry=registry, capability_profile="commander_review")
        baseline = compile_card(card, self.registry)
        self.assertEqual("exact", baseline.status, baseline.material_residuals)
        document = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        cap = next(row for row in document["capabilities"] if row["id"] == ANNOUNCED_GRAVEYARD_COPY_CAPABILITY)
        cap.update(status="blocked", blockers=["Announced graveyard copy authority withdrawn."])
        withdrawn = CapabilityRegistry(document)
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        index = build_index(registry_value=document, declaration_source=declarations,
                            discovered_test_ids=set(discover_tests(ROOT).values()))
        withdrawn.mark_evidence_verified(validate_capability_evidence_index(index, registry=withdrawn))
        self.assertNotEqual("exact", compile_card(card, withdrawn).status)
        with patch("quorune.compiler.temporary_self_copy_templates.announced_graveyard_self_copy_template",
                   return_value=None):
            self.assertNotEqual("exact", compile_card(card, self.registry).status)
        for text in (card.oracle_text.replace("{X}:", "{X}{1}:"),
                     card.oracle_text + " Activate only as a sorcery.",
                     card.oracle_text.replace("name is " + self.source_name, "name is Another Name"),
                     card.oracle_text.replace("with mana value X", "with mana value X or less")):
            self.assertNotEqual("exact", compile_card(replace(card, oracle_text=text), self.registry).status)
        grant = GrantedActivatedAbilitySpec("ability-copy", "compiled:copy", "{X}", "Copy the named model.",
                                            schema_version=7, announced_x_symbols=1)
        self.assertEqual(grant, GrantedActivatedAbilitySpec.from_dict(grant.to_dict()))
        for changes in ({"announced_x_symbols": True}, {"announced_x_symbols": 2},
                        {"schema_version": 1}, {"cost_text": "{X}{1}"}):
            with self.assertRaises(ValueError):
                GrantedActivatedAbilitySpec.from_dict({**grant.to_dict(), **changes})


    def test_copied_activation_retains_x_when_choosing_a_new_graveyard_card(self):
        game = self.session(202_300_51)
        source = self.add(game.engine, self.source_name, ref="ABILITY-COPY-SOURCE")
        engine = self.add(game.engine, "Lithoform Engine", ref="ABILITY-COPY-ENGINE")
        first = self.add(game.engine, self.two, zone="graveyard", ref="ORIGINAL-MODEL")
        other = self.add(game.engine, self.alternate_two, zone="graveyard", ref="COPY-MODEL")
        free = self.add(game.engine, self.zero, zone="graveyard", ref="WRONG-VALUE-MODEL")
        foreign = self.add(game.engine, self.alternate_two, seat="B", zone="graveyard", ref="WRONG-OWNER-MODEL")
        self.assert_closed(game, engine)
        self._prepare_source(game, source, 4)
        original = self._activate(game, source, first, 2)
        self.priority(game, "A")
        actions = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        copy_offer, = [action for action in actions if action["id"].startswith("activate:" + engine.ref + ":")
                       and original.ref in action.get("target_schema", {}).get("legal_refs", ())]
        paid = game.act("pilot:A", {"action_id": copy_offer["id"], "targets": [original.ref], "pay": "auto"})
        self.assertTrue(paid.ok, paid.summary)
        self.assertEqual("semantic.choice", self.resolve(game).kind)
        game = self.replay(game, load=True)
        card_before = authoritative_state_hash(game.state)
        for target in (free, foreign):
            denied = game.act("pilot:A", {"action_id": "choose", "targets": [target.ref]})
            self.assertFalse(denied.ok, denied.summary)
            self.assertEqual(card_before, authoritative_state_hash(game.state))
        changed = game.act("pilot:A", {"action_id": "choose", "targets": [other.ref]})
        self.assertTrue(changed.ok, changed.summary)
        copied = game.state.stack[-1]
        self.assertEqual((2, source.object_id), (copied.x_value, copied.source_object_id))
        self.assertEqual(2, copied.context["target_schema_override"]["mana_value"])
        self.assertEqual((other.ref,), tuple(copied.targets))
        self.resolve_top(game)
        self.assertEqual(self.db.lookup(self.alternate_two).oracle_id,
                         game.state.cards[source.object_id].annotations["copy_overrides"][COPIABLE_ORACLE_FIELD])
        self.assertIsNone(self.resolve(game))
        current = game.state.cards[source.object_id]
        self.assertEqual(self.db.lookup(self.two).oracle_id, current.annotations["copy_overrides"][COPIABLE_ORACLE_FIELD])
        self.assertEqual((source.logical_object_id, 0),
                         (current.logical_object_id, game.state.players["A"].mana_pool.get("C", 0)))
        self.replay(game, load=True)
