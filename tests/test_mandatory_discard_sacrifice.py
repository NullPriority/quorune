"""Native witnesses for one mandatory discard and one mandatory sacrifice."""

from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
import test_qualified_hexproof as native
from test_controller_creature_keyword_absence import register_card
from quorune.carddb import CardDatabase
from quorune.compiler.mandatory_selected_zone_costs import fixed_discard_sacrifice_template
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.replacement.model import ReplacementEffectError
from quorune.replacement.replay import ReplacementContinuation
from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry
from quorune.rules.evidence import validate_capability_evidence_index
from quorune.rules.evidence_storage import read_evidence_document
from quorune.rules.fixed_payment_groups import (
    FIXED_DISCARD_SACRIFICE_CAPABILITY, fixed_discard_sacrifice_dependencies,
)
from scripts.build_test_database import build_fixture_database
from scripts.update_capability_evidence import build_index, discover_tests


SPELL = "Cinder's Verdict"
BRIDGE = "Stillglass Keeper"
BODY = "Generic Bound Body"
BASIC = "Generic Bound Plains"
PROFILE = "commander_review"
FIXTURES = ROOT / "tests" / "fixtures"


class MandatoryDiscardSacrificeTests(unittest.TestCase):
    register_fixture_cards_on_add = True
    session = native.QualifiedHexproofTests.session
    add = native.QualifiedHexproofTests.add
    prepare = native.QualifiedHexproofTests.prepare
    checkpoint = native.QualifiedHexproofTests.checkpoint
    resolve = native.QualifiedHexproofTests.resolve
    replay = native.QualifiedHexproofTests.replay

    @classmethod
    def setUpClass(cls):
        cls.temp = TemporaryDirectory()
        file = Path(cls.temp.name) / "mandatory-discard-and-sacrifice.sqlite3"
        build_fixture_database([FIXTURES / "bound-effect-program-cards.json",
                                FIXTURES / "mandatory-discard-sacrifice-cards.json"], file)
        cls.db = CardDatabase(file)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Selected zone prices", [
            DeckEntry("Generic Bound Commander", 1, "commander"), DeckEntry(BASIC, 30)
        ], ["Generic Bound Commander"])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temp.cleanup()

    def stage(self, seed, *, replacement=True, second=False, missing_creature=False):
        session = self.session(seed)
        spell = self.add(session.engine, SPELL, zone="hand")
        discard = self.add(session.engine, BASIC, zone="hand", ref="private-selected-discard")
        sacrifice = None if missing_creature else self.add(session.engine, BRIDGE if replacement else BODY,
                                                          ref="paid-creature")
        if second:
            self.add(session.engine, BRIDGE, seat="B", ref="second-replacement-source")
        target = self.add(session.engine, BODY, seat="C", ref="public-target-creature")
        self.prepare(session, mana={"C": 1, "B": 1})
        self.checkpoint(session)
        return session, spell, discard, sacrifice, target

    def options(self, session, spell):
        packet = session.packet("pilot:A", full=True)
        actions = packet["decision"]["ctx"]["legal"]["actions"]
        matched = [row for row in actions if row["id"] == "cast:" + spell.ref]
        return matched[0]["cost_options"] if matched else []

    def command(self, spell, discard, sacrifice, target, option):
        return {"action_id": "cast:" + spell.ref, "targets": [target.ref],
                "cost_option": option["id"], "discard_cards": [discard.ref],
                "sacrifice_cards": [sacrifice.ref], "pay": "manual", "payment": {"C": 1, "B": 1}}

    def assert_unpaid(self, session, spell, discard, sacrifice):
        self.assertEqual(session.state.cards[spell.object_id].zone, "hand")
        self.assertEqual(session.state.cards[discard.object_id].zone, "hand")
        self.assertEqual(session.state.cards[sacrifice.object_id].zone, "battlefield")
        self.assertFalse(session.state.stack)
        self.assertEqual(session.state.players["A"].mana_pool["B"], 1)
        self.assertEqual(session.state.players["A"].mana_pool["C"], 1)

    def test_native_order_options_change_the_current_discard_replacement_and_replay(self):
        for reverse in (False, True):
            with self.subTest(reverse=reverse):
                session, spell, discarded, sacrifice, target = self.stage(601_271+int(reverse))
                options = self.options(session, spell)
                self.assertEqual(len(options), 2)
                option = next(row for row in options if row["id"].endswith("+reverse-zone-costs")==reverse)
                self.assertIn("sacrifice, then discard" if reverse else "discard, then sacrifice", option["label"])
                request = self.command(spell, discarded, sacrifice, target, option)
                accepted = session.act("pilot:A", request)
                self.assertTrue(accepted.ok, accepted.summary)
                self.assertEqual(session.state.cards[sacrifice.object_id].zone, "exile")
                self.assertEqual(session.state.cards[discarded.object_id].zone, "graveyard" if reverse else "exile")
                self.assertEqual(session.state.cards[spell.object_id].zone, "stack")
                session = self.replay(session, load=True)
                self.assertIsNone(self.resolve(session))
                self.assertEqual(session.engine._numeric_stat(target.object_id, "toughness"), 4)
                self.replay(session, load=True)

    def test_paired_discard_and_sacrifice_replacement_journals_resume_in_private(self):
        session, spell, discarded, sacrifice, target = self.stage(601_273, second=True)
        option = next(row for row in self.options(session, spell) if not row["id"].endswith("+reverse-zone-costs"))
        accepted = session.act("pilot:A", self.command(spell, discarded, sacrifice, target, option))
        self.assertTrue(accepted.ok, accepted.summary)
        for index in range(2):
            self.assertEqual(session.state.pending_decision.kind, "replacement.order")
            self.assert_unpaid(session, spell, discarded, sacrifice)
            if index == 1:
                continuation = deepcopy(session.state.pending_decision.continuation)
                sealed = continuation["priority_response"]["_ordered_zone_cost_frame"]
                sealed["payments"].reverse()
                with self.assertRaises(ReplacementEffectError):
                    ReplacementContinuation.from_dict(continuation)
            packet = session.packet("pilot:A", full=True)
            choices = packet["decision"]["ctx"]["options"]
            self.assertTrue(choices)
            before = authoritative_state_hash(session.state)
            wrong = session.act("pilot:D", {"action_id": "choose", "replacement": choices[0]["id"]})
            self.assertFalse(wrong.ok)
            self.assertEqual(before, authoritative_state_hash(session.state))
            for seat in "BCD":
                projection = json.dumps(session.packet("pilot:" + seat, full=True))
                self.assertNotIn(discarded.ref, projection)
                self.assertNotIn("replacement.order", projection)
            session = self.replay(session, load=True)
            choices = session.packet("pilot:A", full=True)["decision"]["ctx"]["options"]
            result = session.act("pilot:A", {"action_id": "choose", "replacement": choices[0]["id"]})
            self.assertTrue(result.ok, result.summary)
        self.assertEqual(session.state.cards[discarded.object_id].zone, "exile")
        self.assertEqual(session.state.cards[sacrifice.object_id].zone, "exile")
        self.assertEqual(session.state.cards[spell.object_id].zone, "stack")
        self.replay(session, load=True)

    def test_invalid_cost_order_or_input_rolls_back_and_missing_creature_hides_the_offer(self):
        session, spell, discarded, sacrifice, target = self.stage(601_274, replacement=False)
        command = self.command(spell, discarded, sacrifice, target, self.options(session, spell)[0])
        before = authoritative_state_hash(session.state)
        for bad in (dict(command, discard_cards=[spell.ref]), dict(command, sacrifice_cards=[target.ref]),
                    dict(command, cost_option="reverse-unoffered"), dict(command, targets=[discarded.ref]),
                    dict(command, discard_cards=None, cost_cards=[discarded.ref]),
                    dict(command, _ordered_zone_cost_frame={"schema_version": 1})):
            with self.subTest(bad=bad):
                self.assertFalse(session.act("pilot:A", bad).ok)
                self.assertEqual(before, authoritative_state_hash(session.state))
                self.assert_unpaid(session, spell, discarded, sacrifice)
        session, impossible, *_ = self.stage(601_275, missing_creature=True)
        self.assertFalse(self.options(session, impossible))

    def test_pair_schema_matches_each_child_and_requires_current_authority(self):
        source = self.db.lookup(SPELL)
        first = source.oracle_text.splitlines()[0]
        pair = fixed_discard_sacrifice_template(first)
        self.assertIsNotNone(pair)
        requirements = fixed_discard_sacrifice_dependencies(pair.cost_schema)
        self.assertEqual(len(requirements), 3)
        self.assertIn(FIXED_DISCARD_SACRIFICE_CAPABILITY, requirements)
        for malformed in (first.replace(" and ", " or "), first.replace("discard a card", "discard two cards"),
                          first.replace("sacrifice a creature", "discard a creature card"), first+" Draw a card."):
            self.assertIsNone(fixed_discard_sacrifice_template(malformed))
        reverse = "As an additional cost to cast this spell, sacrifice a creature and discard a card."
        other = fixed_discard_sacrifice_template(reverse)
        self.assertEqual(other.cost_schema["additional_costs"], list(reversed(pair.cost_schema["additional_costs"])))
        binding, _ = register_card(self.db, self.registry, SPELL)
        self.assertTrue(binding["strict_capability_ready"], binding["blockers"])
        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        ids = set(discover_tests(ROOT).values())
        for owner in requirements:
            value = json.loads(json.dumps(raw))
            next(row for row in value["capabilities"] if row["id"] == owner).update(
                status="blocked", blockers=["Selected cost owner withdrawn."]
            )
            registry = CapabilityRegistry(value)
            index = build_index(registry_value=value, declaration_source=declarations, discovered_test_ids=ids)
            registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
            self.assertNotEqual(compile_oracle_card(source, capability_registry=registry,
                                                    capability_profile=PROFILE).status, "exact")
        with patch("quorune.compiler.spell_additional_cost_nodes.fixed_discard_sacrifice_template", return_value=None):
            self.assertNotEqual(compile_oracle_card(source, capability_registry=self.registry,
                                                    capability_profile=PROFILE).status, "exact")
