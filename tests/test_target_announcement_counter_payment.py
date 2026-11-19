from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
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
from quorune.rules.stack_controller_payment_cost import (
    STACK_CONTROLLER_PAYMENT_CAPABILITY, TARGET_ANNOUNCEMENT_PAYMENT_VERSION,
    bind_target_announcement_payment, target_announcement_counter_payment_effect,
)
from quorune.rules.target_announcements import TARGET_ANNOUNCEMENT_CAPABILITY, TARGET_ANNOUNCEMENT_EVENT
from quorune.tap_state import NEXT_UNTAP_PROHIBITION_ANNOTATION
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


FROST = "Frost Titan"
PROFILE = "commander_review"
FIXTURES = ROOT / "tests" / "fixtures"


class TargetAnnouncementCounterPaymentTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        folder = Path(cls.temporary.name)
        selections = {
            "bound-effect-program-cards.json": {"Generic Bound Plains"},
            "source-attack-alone-cards.json": {"Isamaru, Hound of Konda"},
            "attached-destruction-set-cards.json": {"Grizzly Bears", "Murder", "Unsummon", "Island", "Swamp"},
        }
        fixtures = [FIXTURES / "target-announcement-counter-payment-cards.json"]
        for name, names in selections.items():
            data = json.loads((FIXTURES / name).read_bytes())
            data["cards"] = [row for row in data["cards"] if row["name"] in names]
            assert {row["name"] for row in data["cards"]} == names
            ids = {row["oracle_id"] for row in data["cards"]}
            data["rulings"] = [row for row in data.get("rulings", ()) if row["oracle_id"] in ids]
            selected = folder / name
            selected.write_text(json.dumps(data), encoding="utf-8")
            fixtures.append(selected)
        path = folder / "target-announcement-counter-payment.sqlite3"
        build_fixture_database(fixtures, path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Announced stack controller payment", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def test_complete_original_keeps_both_abilities_and_seals_the_exact_event_operand(self):
        card = self.db.lookup(FROST, fuzzy=False)
        ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile=PROFILE)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        new, unchanged = ir.faces[0].nodes
        self.assertEqual(TARGET_ANNOUNCEMENT_EVENT, new.event)
        self.assertIsNone(new.target_schema)
        self.assertTrue({TARGET_ANNOUNCEMENT_CAPABILITY, STACK_CONTROLLER_PAYMENT_CAPABILITY,
            "stack.counter.effect", "trigger.placement.apnap"} <= set(new.capability_dependencies))
        self.assertEqual((target_announcement_counter_payment_effect(),), new.effects)
        self.assertTrue({"permanent.tap.effect", "untap.step.prohibit_next_controller"}
            <= set(unchanged.capability_dependencies))
        ready, semantics = register_card(self.db, self.registry, FROST)
        self.assertTrue(ready["strict_capability_ready"], ready["blockers"])
        program, = [p for p in semantics.programs_for_oracle(card.oracle_id) if p.event == TARGET_ANNOUNCEMENT_EVENT]
        effect, = thaw_value(program.effects)
        self.assertEqual(TARGET_ANNOUNCEMENT_PAYMENT_VERSION, effect["schema_version"])
        for changes in (
            {"event_condition": None},
            {"effects": [{**effect, "stack": "$target.0"}]},
            {"effects": [{**effect, "cost": {**effect["cost"], "GENERIC": 1}}]},
            {"effects": [effect, {"op": "draw", "player": "$controller", "count": 1}]},
        ):
            with self.subTest(changes=changes):
                result = bind_semantic_program_runtime(replace(program, **changes),
                    capability_registry=self.registry, profile=PROFILE)
                self.assertFalse(result["strict"], result)
        for text in (
            card.oracle_text.replace("pays {2}", "pays {3}"),
            card.oracle_text.replace(" an opponent controls", ""),
            card.oracle_text.replace("the target of a spell or ability", "the target of a spell"),
        ):
            with self.subTest(text=text):
                result = compile_oracle_card(replace(card, oracle_text=text),
                    capability_registry=self.registry, capability_profile=PROFILE)
                self.assertNotEqual("exact", result.status)

    def test_sealed_reference_reads_current_stack_controller_and_disappears_with_that_object(self):
        effect = target_announcement_counter_payment_effect()
        stack = SimpleNamespace(ref="stack-object", controller="C")
        host = SimpleNamespace(state=SimpleNamespace(stack=[stack]))
        actual = bind_target_announcement_payment(host, effect, resolve=lambda value: "stack-object")
        self.assertEqual((2, "stack-object", "C", 2),
            (actual["schema_version"], actual["stack"], actual["player"], actual["cost"]["GENERIC"]))
        host.state.stack.clear()
        self.assertIsNone(bind_target_announcement_payment(host, effect, resolve=lambda value: "stack-object")["stack"])
        with self.assertRaises(ValueError):
            bind_target_announcement_payment(host, effect, resolve=lambda value: None)

    def spell_case(self, seed, *, swamps, reply=False):
        game = self.session(seed)
        frost = self.add(game.engine, FROST)
        murder = self.add(game.engine, "Murder", seat="B", zone="hand")
        private = self.add(game.engine, "Unsummon", seat="D", zone="hand", ref="frost-foreign-private")
        lands = [self.add(game.engine, "Swamp", seat="B", ref=f"frost-cost-swamp-{n}") for n in range(swamps)]
        counter = None
        if reply:
            counter = self.add(game.engine, "Counterspell", zone="hand")
            for n in range(2):
                self.add(game.engine, "Island", ref=f"frost-counter-island-{n}")
            self.assert_closed(game, counter)
        for card in (frost, murder):
            self.assert_closed(game, card)
        self.prepare(game, active="B", actor="B")
        self.checkpoint(game)
        self.cast(game, "B", murder, [frost.ref])
        original, trigger = game.state.stack
        self.assertEqual(("spell", murder.object_id, "B"),
            (original.kind, original.card_object_id, original.controller))
        self.assertEqual(("triggered_ability", frost.object_id, "A"),
            (trigger.kind, trigger.source_object_id, trigger.controller))
        return game, frost, murder, private, lands, original, counter

    def payment_offer(self, game, original):
        decision = self.resolve(game)
        self.assertIsNotNone(decision)
        self.assertEqual(["pilot:B"], game.pending_principals())
        payload = game.packet("pilot:B", full=True)["decision"]
        self.assertEqual(original.ref, payload["ctx"]["target_stack"])
        self.assertEqual(2, payload["ctx"]["cost"]["GENERIC"])
        self.assertEqual([True, False], payload["legal_actions"][0]["choice_schema"]["legal_values"])
        return payload

    def test_actual_opponent_spell_pays_on_resolution_and_replays_private_authority(self):
        game, frost, _, private, lands, original, _ = self.spell_case(87310511, swamps=5)
        self.assertEqual(3, sum(game.state.cards[land.object_id].tapped for land in lands))
        self.payment_offer(game, original)
        before = authoritative_state_hash(game.state)
        for seat, pay in (("A", True), ("B", "true")):
            self.assertFalse(game.act("pilot:" + seat, {"action_id": "choose", "pay": pay}).ok)
            self.assertEqual(before, authoritative_state_hash(game.state))
        for seat in "ACD":
            self.assertIsNone(game.packet("pilot:" + seat, full=True)["decision"])
        game = self.replay(game, load=True)
        paid = game.act("pilot:B", {"action_id": "choose", "pay": True})
        self.assertTrue(paid.ok, paid.summary)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(5, sum(game.state.cards[land.object_id].tapped for land in lands))
        self.assertEqual("graveyard", game.state.cards[frost.object_id].zone)
        self.assertTrue(any(event.code == "counter.unless.paid" and event.details.get("stack") == original.ref
            for event in game.state.events))
        for seat in "ABC":
            packet = json.dumps(game.packet("pilot:" + seat, full=True))
            self.assertNotIn(private.ref, packet)
            self.assertNotIn(private.printed_name, packet)
        self.replay(game, load=True)

    def test_actual_declined_opponent_ability_is_countered_with_the_source_price_retained(self):
        game = self.session(87310512)
        frost = self.add(game.engine, FROST)
        pyromancer = self.add(game.engine, "Prodigal Pyromancer", seat="B")
        pyromancer.acquired_control_turn_count = -1
        lands = [self.add(game.engine, "Generic Bound Plains", seat="B", ref=f"frost-decline-land-{n}") for n in range(2)]
        for card in (frost, pyromancer):
            self.assert_closed(game, card)
        self.prepare(game, active="B", actor="B")
        self.checkpoint(game)
        actions = game.packet("pilot:B", full=True)["decision"]["ctx"]["legal"]["actions"]
        offer, = [row for row in actions if row["id"].startswith("activate:" + pyromancer.ref + ":")]
        accepted = game.act("pilot:B", {"action_id": offer["id"], "targets": [frost.ref], "pay": "auto"})
        self.assertTrue(accepted.ok, accepted.summary)
        source, trigger = game.state.stack
        self.assertEqual(("activated_ability", pyromancer.object_id), (source.kind, source.source_object_id))
        self.assertEqual(frost.object_id, trigger.source_object_id)
        self.payment_offer(game, source)
        declined = game.act("pilot:B", {"action_id": "choose", "pay": False})
        self.assertTrue(declined.ok, declined.summary)
        self.assertFalse(game.state.stack)
        self.assertTrue(game.state.cards[pyromancer.object_id].tapped)
        self.assertFalse(any(game.state.cards[land.object_id].tapped for land in lands))
        self.assertEqual(0, game.state.cards[frost.object_id].marked_damage)
        counters = [event for event in game.state.events if event.code == "stack.counter"
            and event.details.get("stack") == source.ref]
        countered, = counters
        self.assertEqual("A", countered.actor)
        self.replay(game, load=True)

    def test_actual_departed_announcement_stack_object_offers_no_payment(self):
        game, frost, murder, _, _, original, reply = self.spell_case(87310513, swamps=3, reply=True)
        self.cast(game, "A", reply, [original.ref])
        self.resolve_top(game)
        self.assertEqual("graveyard", game.state.cards[murder.object_id].zone)
        self.assertFalse(any(item.ref == original.ref for item in game.state.stack))
        self.assertIsNone(self.resolve(game))
        self.assertFalse(game.state.stack)
        self.assertEqual("battlefield", game.state.cards[frost.object_id].zone)
        self.replay(game, load=True)

    def test_actual_enters_sibling_can_target_itself_without_an_opponent_trigger(self):
        game = self.session(87310514)
        frost = self.add(game.engine, FROST, zone="hand")
        for n in range(2):
            self.add(game.engine, "Island", ref=f"frost-actual-entry-island-{n}")
        self.assert_closed(game, frost)
        self.prepare(game, mana={"C": 4})
        self.checkpoint(game)
        self.cast(game, "A", frost)
        self.assertIsNotNone(self.resolve(game))
        current = game.state.cards[frost.object_id]
        self.assertEqual("battlefield", current.zone)
        self.target(game, "A", [current.ref])
        self.assertEqual(1, len(game.state.stack))
        self.assertIsNone(self.resolve(game))
        current = game.state.cards[frost.object_id]
        self.assertTrue(current.tapped)
        self.assertIs(current.annotations.get(NEXT_UNTAP_PROHIBITION_ANNOTATION), True)
        self.replay(game, load=True)

    def test_omitting_the_exact_event_scope_blocks_ordinary_complete_registration(self):
        with patch("quorune.rules.stack_controller_payment_shapes.source_opponent_target_announcement_condition_is_closed",
                   return_value=False):
            b, _ = register_card(self.db, self.registry, FROST)
        self.assertFalse(b["strict_capability_ready"], b)

    def test_verified_owner_withdrawal_preserves_independent_tap_trigger(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests

        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        known = set(discover_tests(ROOT).values())
        for cap in (TARGET_ANNOUNCEMENT_CAPABILITY, STACK_CONTROLLER_PAYMENT_CAPABILITY):
            with self.subTest(cap=cap):
                rows = deepcopy(raw)
                next(row for row in rows["capabilities"] if row["id"] == cap).update(
                    status="blocked", blockers=["Exact announced counter payment requirement withdrawn"])
                registry = CapabilityRegistry(rows)
                index = build_index(registry_value=rows, declaration_source=declarations, discovered_test_ids=known)
                registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
                closure = registry.closure((cap,), profile=PROFILE)
                self.assertIn("status:" + cap + ":blocked", closure.blockers)
                self.assertFalse(any("unverified" in item for item in closure.blockers))
                ready, _ = register_card(self.db, registry, FROST)
                self.assertFalse(ready["strict_capability_ready"], ready)
                control, _ = register_card(self.db, registry, "Grizzly Bears")
                self.assertTrue(control["strict_capability_ready"], control["blockers"])
                ir = compile_oracle_card(self.db.lookup(FROST, fuzzy=False), capability_registry=registry,
                    capability_profile=PROFILE)
                self.assertTrue(any(n.exact and "untap.step.prohibit_next_controller" in n.capability_dependencies
                    for n in ir.faces[0].nodes))
