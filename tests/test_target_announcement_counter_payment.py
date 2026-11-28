from copy import deepcopy
from dataclasses import replace
import json
import os
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
        with patch.object(game.engine, "_dispatch_semantic_event", wraps=game.engine._dispatch_semantic_event) as dispatch:
            self.cast(game, "B", murder, [frost.ref])
        original, trigger = game.state.stack
        self.assertEqual([{
            "schema_version": 1, "stack": original.ref, "stack_controller": "B", "stack_kind": "spell",
            "card": frost.ref, "controller": "A", "card_object_identity": frost.logical_object_id,
            "types": ["creature"],
        }], [call.args[1] for call in dispatch.call_args_list if call.args[0] == TARGET_ANNOUNCEMENT_EVENT])
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

class DiffusionSliverTargetPaymentTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        folder = Path(cls.temporary.name)
        snapshot = os.environ.get("MTG_CARD_DB")
        if snapshot:
            cls.db = CardDatabase(snapshot)
        else:
            sources = (
                "target-announcement-counter-payment-cards.json",
                "source-attack-alone-cards.json",
                "cast-creature-targets.json",
                "changeling-characteristics-cards.json",
                "creature-subtype-set-results-cards.json",
                "targeted-spell-control-cards.json",
                "fixed-stack-spell-copy-cards.json",
            )
            path = folder / "diffusion-sliver.sqlite3"
            build_fixture_database([FIXTURES / name for name in sources], path)
            cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("Diffusion target payment", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Plains", 30),
        ], [commander])

    def scene(self, seed):
        game = self.session(seed)
        game.state.config.semantic_policy = "trusted_only"
        return game, self.add(game.engine, "Diffusion Sliver")

    def triggers(self, game, source):
        return [item for item in game.state.stack
                if item.kind == "triggered_ability" and item.source_object_id == source.object_id]

    def choose(self, game, seat, **fields):
        result = game.act(f"pilot:{seat}", {"action_id": "choose", **fields})
        self.assertTrue(result.ok, result.summary)

    def payment(self, game, controller, stack_ref):
        decision = self.resolve(game)
        self.assertIsNotNone(decision)
        self.assertEqual(("semantic.choice", [controller]), (decision.kind, decision.actors))
        packet = game.packet(f"pilot:{controller}", full=True)["decision"]
        self.assertEqual(stack_ref, packet["ctx"]["target_stack"])
        self.assertEqual(2, packet["ctx"]["cost"]["GENERIC"])
        for other in set("ABCD") - {controller}:
            self.assertIsNone(game.packet(f"pilot:{other}", full=True)["decision"])

    def test_diffusion_sliver_admits_the_exact_trigger_under_strict_profile(self):
        card = self.db.by_oracle_id("458ca9f4-4622-48e1-9ca1-6a8117188973")
        ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile=PROFILE)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        trigger, = ir.faces[0].nodes
        self.assertEqual(TARGET_ANNOUNCEMENT_EVENT, trigger.event)
        self.assertIsNone(trigger.target_schema)
        self.assertTrue({TARGET_ANNOUNCEMENT_CAPABILITY, STACK_CONTROLLER_PAYMENT_CAPABILITY,
            "stack.counter.effect", "trigger.placement.apnap"} <= set(trigger.capability_dependencies))
        binding, _ = register_card(self.db, self.registry, "Diffusion Sliver")
        self.assertTrue(binding["strict_capability_ready"], binding["blockers"])

    def test_actual_distinct_sliver_targets_each_require_payment_and_duplicate_target_requires_one(self):
        for same_target in (False, True):
            with self.subTest(same_target=same_target):
                game, source = self.scene(87310520 + same_target)
                changeling = self.add(game.engine, "Universal Automaton")
                bond = self.add(game.engine, "Common Bond", seat="B", zone="hand")
                for card in (source, changeling, bond):
                    self.assert_closed(game, card)
                self.prepare(game, active="B", actor="B", mana={"C": 5, "G": 1, "W": 1})
                self.checkpoint(game)
                targets = [source.ref, source.ref if same_target else changeling.ref]
                self.cast(game, "B", bond, targets)
                original, = (item for item in game.state.stack if item.card_object_id == bond.object_id)
                self.assertEqual(targets, original.targets)
                if not same_target:
                    decision = game.state.pending_decision
                    self.assertEqual(("trigger.order", ["A"]), (decision.kind, decision.actors))
                    refs = [item.ref for batch in game.state.pending_trigger_batches for item in batch.items]
                    self.assertEqual(2, len(refs))
                    ordered = game.act("pilot:A", {"action_id": "order", "triggers": refs})
                    self.assertTrue(ordered.ok, ordered.summary)
                count = 1 if same_target else 2
                self.assertEqual([("A", [])] * count,
                    [(trigger.controller, trigger.targets) for trigger in self.triggers(game, source)])
                for _ in range(count):
                    self.payment(game, "B", original.ref)
                    self.choose(game, "B", pay=True)
                self.assertIsNone(self.resolve(game))
                self.assertEqual(2 if same_target else 1, game.state.cards[source.object_id].counters["+1/+1"])
                self.assertEqual(0 if same_target else 1,
                    game.state.cards[changeling.object_id].counters.get("+1/+1", 0))
                self.assertEqual([original.ref] * count, [event.details["stack"]
                    for event in game.state.events if event.code == "counter.unless.paid"])
                self.replay(game, load=True)

    def test_actual_opponent_ability_is_countered_even_after_diffusion_leaves(self):
        game, source = self.scene(87310522)
        changeling = self.add(game.engine, "Universal Automaton")
        pyromancer = self.add(game.engine, "Prodigal Pyromancer", seat="B")
        pyromancer.acquired_control_turn_count = -1
        bounce = self.add(game.engine, "Unsummon", zone="hand")
        for card in (source, changeling, pyromancer, bounce):
            self.assert_closed(game, card)
        self.prepare(game, active="B", actor="B", mana={"C": 2})
        game.state.players["A"].mana_pool["U"] = 1
        self.checkpoint(game)
        actions = game.packet("pilot:B", full=True)["decision"]["ctx"]["legal"]["actions"]
        offer, = [row for row in actions if row["id"].startswith(f"activate:{pyromancer.ref}:")]
        result = game.act("pilot:B", {"action_id": offer["id"], "targets": [changeling.ref], "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        original, trigger = game.state.stack
        self.assertEqual(("activated_ability", pyromancer.object_id), (original.kind, original.source_object_id))
        self.assertEqual(("A", source.object_id), (trigger.controller, trigger.source_object_id))
        self.cast(game, "A", bounce, [source.ref])
        self.assertEqual([trigger.ref], [item.ref for item in self.triggers(game, source)])
        self.resolve_top(game)
        self.assertEqual("hand", game.state.cards[source.object_id].zone)
        self.payment(game, "B", original.ref)
        self.choose(game, "B", pay=False)
        self.assertFalse(game.state.stack)
        self.assertTrue(game.state.cards[pyromancer.object_id].tapped)
        self.assertEqual(0, game.state.cards[changeling.object_id].marked_damage)
        self.assertEqual([original.ref], [event.details["stack"]
            for event in game.state.events if event.code == "stack.counter"])
        self.replay(game, load=True)

    def test_actual_diffusion_losing_sliver_type_keeps_protecting_other_slivers(self):
        game, source = self.scene(87310530)
        amoeboid = self.add(game.engine, "Amoeboid Changeling")
        amoeboid.acquired_control_turn_count = -1
        first = self.add(game.engine, "Giant Growth", seat="B", zone="hand", ref="grow-diffusion")
        second = self.add(game.engine, "Giant Growth", seat="B", zone="hand", ref="grow-amoeboid")
        for card in (source, amoeboid, first):
            self.assert_closed(game, card)
        self.prepare(game)
        game.state.players["B"].mana_pool.update({"G": 2})
        self.checkpoint(game)
        strip, = [ability for ability in game.engine._activated_abilities(amoeboid)
                  if "loses all creature types" in ability.effect_text.casefold()]
        result = game.act("pilot:A", {"action_id": f"activate:{amoeboid.ref}:{strip.ability_id}",
                                     "targets": [source.ref], "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(game))
        self.cast(game, "B", first, [source.ref])
        self.assertEqual(["spell"], [item.kind for item in game.state.stack])
        self.assertIsNone(self.resolve(game))
        self.assertEqual(4, game.engine._numeric_stat(source.object_id, "power"))
        self.cast(game, "B", second, [amoeboid.ref])
        original, trigger = game.state.stack
        self.assertEqual(source.object_id, trigger.source_object_id)
        self.payment(game, "B", original.ref)
        self.choose(game, "B", pay=False)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(1, game.engine._numeric_stat(amoeboid.object_id, "power"))
        self.replay(game, load=True)

    def test_actual_friendly_targeting_and_opponent_targeting_unrelated_creatures_do_not_trigger(self):
        for seed, (caster, target_seat, name) in enumerate((
            ("A", "A", "Universal Automaton"),
            ("B", "B", "Universal Automaton"),
            ("B", "A", "Grizzly Bears"),
        ), 87310523):
            with self.subTest(caster=caster, target_seat=target_seat, name=name):
                game, source = self.scene(seed)
                target = self.add(game.engine, name, seat=target_seat)
                growth = self.add(game.engine, "Giant Growth", seat=caster, zone="hand")
                for card in (source, target, growth):
                    self.assert_closed(game, card)
                self.prepare(game, active=caster, actor=caster, mana={"G": 1})
                self.checkpoint(game)
                power = game.engine._numeric_stat(target.object_id, "power")
                self.cast(game, caster, growth, [target.ref])
                self.assertEqual(["spell"], [item.kind for item in game.state.stack])
                self.assertIsNone(self.resolve(game))
                self.assertEqual(power + 3, game.engine._numeric_stat(target.object_id, "power"))
                self.replay(game, load=True)

    def test_actual_opponent_triggered_ability_announces_only_the_chosen_sliver(self):
        game, source = self.scene(87310529)
        frost = self.add(game.engine, FROST, seat="B", zone="hand")
        for card in (source, frost):
            self.assert_closed(game, card)
        self.prepare(game, active="B", actor="B", mana={"C": 6, "U": 2})
        self.checkpoint(game)
        self.cast(game, "B", frost)
        self.assertIsNotNone(self.resolve(game))
        self.assertEqual([], self.triggers(game, source))
        self.target(game, "B", [source.ref])
        original, trigger = game.state.stack
        self.assertEqual(("triggered_ability", frost.object_id, "B"),
            (original.kind, original.source_object_id, original.controller))
        self.assertEqual(("triggered_ability", source.object_id, "A"),
            (trigger.kind, trigger.source_object_id, trigger.controller))
        self.payment(game, "B", original.ref)
        self.choose(game, "B", pay=False)
        self.assertIsNone(self.resolve(game))
        self.assertFalse(game.state.cards[source.object_id].tapped)
        self.replay(game, load=True)

    def test_actual_control_change_keeps_original_object_and_new_target_is_another_occurrence(self):
        for retarget in (False, True):
            with self.subTest(retarget=retarget):
                game, source = self.scene(87310526 + retarget)
                changeling = self.add(game.engine, "Universal Automaton")
                growth = self.add(game.engine, "Giant Growth", seat="B", zone="hand")
                new_controller = "C" if retarget else "A"
                snatch = self.add(game.engine, "Aethersnatch", seat=new_controller, zone="hand")
                for card in (source, changeling, growth, snatch):
                    self.assert_closed(game, card)
                self.prepare(game, active="B", actor="B", mana={"C": 2, "G": 1})
                game.state.players[new_controller].mana_pool.update({"C": 8, "U": 2})
                self.checkpoint(game)
                self.cast(game, "B", growth, [source.ref])
                original, old_trigger = game.state.stack
                self.cast(game, new_controller, snatch, [original.ref])
                decision = self.resolve(game)
                self.assertEqual(("semantic.choice", [new_controller]), (decision.kind, decision.actors))
                chosen = changeling if retarget else source
                self.choose(game, new_controller, targets=[chosen.ref])
                original_item = next(item for item in game.state.stack if item.ref == original.ref)
                self.assertEqual((new_controller, [chosen.ref]), (original_item.controller, original_item.targets))
                triggers = self.triggers(game, source)
                self.assertEqual(2 if retarget else 1, len(triggers))
                self.assertEqual(old_trigger.ref, triggers[0].ref)
                for _ in triggers:
                    self.payment(game, new_controller, original.ref)
                    before = authoritative_state_hash(game.state)
                    self.assertFalse(game.act("pilot:B", {"action_id": "choose", "pay": True}).ok)
                    self.assertEqual(before, authoritative_state_hash(game.state))
                    self.choose(game, new_controller, pay=True)
                self.assertIsNone(self.resolve(game))
                self.assertEqual(4, game.engine._numeric_stat(chosen.object_id, "power"))
                self.assertEqual(2, game.state.players["B"].mana_pool["C"])
                self.replay(game, load=True)

    def test_actual_copy_decline_counters_only_the_copy_and_replays_pending_payment(self):
        game, source = self.scene(87310528)
        changeling = self.add(game.engine, "Universal Automaton")
        growth = self.add(game.engine, "Giant Growth", seat="B", zone="hand")
        twincast = self.add(game.engine, "Twincast", seat="C", zone="hand")
        for card in (source, changeling, growth, twincast):
            self.assert_closed(game, card)
        self.prepare(game, active="B", actor="B", mana={"C": 2, "G": 1})
        game.state.players["C"].mana_pool.update({"C": 2, "U": 2})
        self.checkpoint(game)
        self.cast(game, "B", growth, [source.ref])
        original, old_trigger = game.state.stack
        self.cast(game, "C", twincast, [original.ref])
        decision = self.resolve(game)
        self.assertEqual(("semantic.choice", ["C"]), (decision.kind, decision.actors))
        self.choose(game, "C", targets=[changeling.ref])
        copy, = (item for item in game.state.stack if item.kind == "spell_copy")
        self.assertEqual(("C", [changeling.ref]), (copy.controller, copy.targets))
        old, new = self.triggers(game, source)
        self.assertEqual(old_trigger.ref, old.ref)
        self.assertNotEqual(old.ref, new.ref)
        self.payment(game, "C", copy.ref)
        game = self.replay(game, load=True)
        self.choose(game, "C", pay=False)
        self.assertEqual([original.ref, old_trigger.ref], [item.ref for item in game.state.stack])
        self.payment(game, "B", original.ref)
        self.choose(game, "B", pay=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(4, game.engine._numeric_stat(source.object_id, "power"))
        self.assertEqual(1, game.engine._numeric_stat(changeling.object_id, "power"))
        self.replay(game, load=True)
