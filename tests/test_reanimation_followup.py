from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT
import test_fixed_reveal_additional_costs as reveal
import test_ring_native_actions as turns
import test_self_public_zone_casting as actions
from test_granted_skulk import record

from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.linked_departure_exile_model import ResolvedLinkedDepartureExile
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.reanimation_followup import REANIMATION_FOLLOWUP_CAPABILITY, validate_reanimation_followup
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry
from quorune.turn_step_owner import TURN_STEPS
from scripts.build_test_database import build_fixture_database


TEXT = ("Return target legendary creature card from your graveyard to the battlefield. "
    "That creature gains haste. Exile it at the beginning of the next end step.")
LEAVE_EXILE = " If it would leave the battlefield, exile it instead of putting it anywhere else."


class ReanimationFollowupCompilerTests(unittest.TestCase):
    def test_complete_targeted_return_and_riders_compile_and_open_variants_fail_closed(self):
        registry = load_default_capability_registry()
        ir = compile_oracle_card(record(TEXT, "Instant"), capability_registry=registry)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        node, = ir.faces[0].nodes
        effect, = node.effects
        self.assertEqual(TEXT, node.text)
        self.assertEqual("exile", validate_reanimation_followup(effect).departure)
        self.assertFalse(validate_reanimation_followup(effect).exile_on_leave)
        self.assertIn(REANIMATION_FOLLOWUP_CAPABILITY, node.capability_dependencies)
        self.assertIn("continuous.resolution.fixed_keyword_zone_object", node.capability_dependencies)
        for text in (TEXT.replace("gains haste", "gains an ability"),
            TEXT.replace("the next end step", "the next upkeep"),
            TEXT + LEAVE_EXILE.replace("If it", "If another creature"),
            TEXT.replace("Return target", "Return up to one target") + LEAVE_EXILE,
            TEXT.replace("the next end step", "your next end step") + LEAVE_EXILE):
            with self.subTest(text=text):
                other = compile_oracle_card(record(text, "Instant"), capability_registry=registry)
                self.assertNotEqual("exact", other.status)
        for change in ({"schema_version": True}, {"card": "$source"}, {"tapped": 0},
            {"followup": {**effect["followup"], "power_boost": True}}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate_reanimation_followup({**effect, **change})

        linked = compile_oracle_card(record(TEXT + LEAVE_EXILE, "Instant"), capability_registry=registry)
        self.assertEqual("exact", linked.status, linked.material_residuals)
        effect, = linked.faces[0].nodes[0].effects
        self.assertEqual(5, effect["schema_version"])
        self.assertTrue(validate_reanimation_followup(effect).exile_on_leave)
        for change in ({"schema_version": 4},
                       {"followup": {**effect["followup"], "exile_on_leave": 1}},
                       {"followup": {**effect["followup"], "departure": "sacrifice"}}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate_reanimation_followup({**effect, **change})

    def test_withdrawing_delayed_action_authority_leaves_the_linked_body_unclosed(self):
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests

        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        row = next(row for row in raw["capabilities"] if row["id"] == "trigger.delayed.fixed_object_action")
        row.update(status="blocked", blockers=["withdrawn for the dependency witness"])
        registry = CapabilityRegistry(raw)
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        evidence = build_index(registry_value=raw, declaration_source=declarations,
            discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(evidence, registry=registry))
        self.assertNotEqual("exact", compile_oracle_card(record(TEXT, "Instant"), capability_registry=registry).status)
        self.assertEqual("exact", compile_oracle_card(record(TEXT.split(". ")[0] + ".", "Instant"),
            capability_registry=registry).status)

    def test_omitting_linked_reanimation_compiler_leaves_material_residuals(self):
        registry = load_default_capability_registry()
        card = record(TEXT, "Instant")
        self.assertEqual("exact", compile_oracle_card(card, capability_registry=registry).status)
        from quorune.compiler import resolution_effect_templates as owner

        compilers = tuple(fn for fn in owner._DIRECT_RESOLUTION_EFFECT_COMPILERS
            if fn is not owner.fixed_reanimation_followup_template)
        with patch.object(owner, "_DIRECT_RESOLUTION_EFFECT_COMPILERS", compilers):
            self.assertNotEqual("exact", compile_oracle_card(card, capability_registry=registry).status)


class ReanimationFollowupNativeTests(unittest.TestCase):
    register_fixture_cards_on_add = True
    session = reveal.FixedRevealCostRuntimeTests.session
    add = reveal.FixedRevealCostRuntimeTests.add
    checkpoint = reveal.FixedRevealCostRuntimeTests.checkpoint
    resolve = reveal.FixedRevealCostRuntimeTests.resolve
    replay = reveal.FixedRevealCostRuntimeTests.replay
    reject = reveal.FixedRevealCostRuntimeTests.reject
    prepare = actions.SelfPublicZoneRuntimeTests.prepare
    cast = actions.SelfPublicZoneRuntimeTests.cast
    assert_closed = actions.SelfPublicZoneRuntimeTests.assert_closed
    native_until = turns.RingNativeActionTests.until

    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / "reanimation-followup.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/bound-effect-program-cards.json",
            ROOT / "tests/fixtures/reanimation-followup-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Linked return witness", [DeckEntry("Generic Bound Commander", 1, "commander"),
            DeckEntry("Generic Bound Plains", 30)], ["Generic Bound Commander"])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    def until(self, session, predicate):
        for _ in range(6):
            self.native_until(session, lambda game: predicate(game) or game.state.pending_decision.kind == "cleanup.discard")
            if predicate(session):
                return
            principal = session.pending_principals()[0]
            context = session.packet(principal, full=True)["decision"]["ctx"]
            cards = [row["id"] for row in context["hand"][:context["count"]]]
            result = session.act(principal, {"a": "discard", "cards": cards})
            self.assertTrue(result.ok, result.summary)
        self.fail("The expected end step was not reached")

    @staticmethod
    def has_haste(session, card):
        return "haste" in session.engine._combat_keywords(session.state.cards[card.object_id])

    def test_linked_reanimation_revalidates_the_original_graveyard_target(self):
        session = self.session(6030711)
        first = self.add(session.engine, "Goryo's Vengeance", zone="hand", ref="first-return")
        response = self.add(session.engine, "Goryo's Vengeance", zone="hand", ref="response-return")
        target = self.add(session.engine, "Generic Bound Commander", zone="graveyard", ref="disputed-return")
        self.assert_closed(session, first)
        program, = [item for item in session.engine.semantics.programs_for_oracle(first.oracle_id)
                    if item.event == "resolve"]
        self.assertLessEqual(
            {"target.revalidate_resolution", REANIMATION_FOLLOWUP_CAPABILITY},
            set(program.capability_dependencies),
        )
        self.prepare(session, mana={"B": 2, "C": 2})
        self.checkpoint(session)

        self.cast(session, "A", first, [target.ref])
        first_ref = next(item.ref for item in session.state.stack if item.card_object_id == first.object_id)
        self.cast(session, "A", response, [target.ref])
        session = self.replay(session, load=True)
        self.assertIsNone(self.resolve(session))

        self.assertEqual("battlefield", session.state.cards[target.object_id].zone)
        self.assertTrue(self.has_haste(session, target))
        departure, = [trigger for trigger in session.state.delayed_triggers if trigger.active]
        self.assertEqual(response.object_id, departure.source_object_id)
        counter, = [event for event in session.state.events if event.code == "stack.counter"
                    and event.details["stack"] == first_ref]
        self.assertEqual("rules", counter.details["counter_kind"])
        self.assertEqual("all targets illegal on resolution", counter.details["reason"])
        self.replay(session, load=True)

    def test_goryos_vengeance_captures_one_incarnation_through_delayed_trigger_reload(self):
        session = self.session(6030701)
        engine = session.engine
        spell = self.add(engine, "Goryo's Vengeance", zone="hand")
        blink = self.add(engine, "Cloudshift", zone="hand")
        target = self.add(engine, "Generic Bound Commander", zone="graveyard", ref="returned-legend")
        other = self.add(engine, "Generic Bound Body", zone="graveyard")
        self.assert_closed(session, spell)
        self.prepare(session, mana={"B": 1, "C": 1, "W": 2})
        self.checkpoint(session)
        self.reject(session, "A", action_id=f"cast:{spell.ref}", pay="auto", targets=[other.ref])
        self.cast(session, "A", spell, [target.ref])
        self.assertIsNone(self.resolve(session))
        target = session.state.cards[target.object_id]
        self.assertEqual("battlefield", target.zone)
        self.assertTrue(self.has_haste(session, target))
        trigger, = session.state.delayed_triggers
        self.assertEqual((spell.object_id, "A"), (trigger.source_object_id, trigger.controller))
        self.assertEqual(target.zone_change_counter, trigger.stack_template["context"]["dynamic_effects"][0]["object"]["zone_change_counter"])
        self.cast(session, "A", blink, [target.ref])
        self.assertIsNone(self.resolve(session))
        self.assertFalse(self.has_haste(session, target))
        self.until(session, lambda game: game.state.step == "end_step" and bool(game.state.stack))
        resumed = self.replay(session, load=True)
        self.assertIsNone(self.resolve(resumed))
        self.assertEqual("battlefield", resumed.state.cards[target.object_id].zone)
        self.assertFalse(any(trigger.active for trigger in resumed.state.delayed_triggers))
        self.replay(resumed, load=True)

    def test_puppeteer_clique_waits_for_its_controllers_end_step_and_keeps_indefinite_haste(self):
        session = self.session(6030702)
        engine = session.engine
        source = self.add(engine, "Puppeteer Clique")
        blink = self.add(engine, "Cloudshift", zone="hand")
        target = self.add(engine, "Clone", seat="B", zone="graveyard", ref="opponents-graveyard-clone")
        copy_source = self.add(engine, "Generic Bound Body", seat="D", ref="public-clique-copy-source")
        secret = self.add(engine, "Generic Bound Growth", seat="C", zone="hand", ref="private-clique-control")
        self.add(engine, "Plains")
        self.assert_closed(session, source)
        self.prepare(session, actor="D", active="D")
        session.state.last_normal_turn_player = "D"
        self.checkpoint(session)
        self.cast(session, "A", blink, [source.ref])
        self.assertEqual("semantic.target", self.resolve(session).kind)
        command = {"action_id": "choose", "targets": [target.ref]}
        before = authoritative_state_hash(session.state)
        self.assertFalse(session.act("pilot:D", command).ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        accepted = session.act("pilot:A", command)
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertEqual("replacement.order", self.resolve(session).kind)
        self.assertEqual("graveyard", session.state.cards[target.object_id].zone)
        self.assertFalse(session.state.delayed_triggers)
        session = self.replay(session, load=True)
        options = session.packet("pilot:A", full=True)["decision"]["ctx"]["options"]
        copy_choice = next(option["id"] for option in options if copy_source.ref in option["id"])
        accepted = session.act("pilot:A", {"action_id": "choose", "replacement": copy_choice})
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertIsNone(self.resolve(session))
        target = session.state.cards[target.object_id]
        self.assertEqual(("B", "A", "battlefield"), (target.owner, target.controller, target.zone))
        self.assertEqual("Generic Bound Body", session.engine.display_name(target.object_id))
        trigger, = session.state.delayed_triggers
        self.assertEqual(source.object_id, trigger.source_object_id)
        self.until(session, lambda game: game.state.active_player == "A" and game.state.step == "upkeep")
        self.assertTrue(trigger.active)
        self.assertTrue(self.has_haste(session, target))
        self.assertNotIn(secret.ref, json.dumps(session.packet("pilot:D", full=True)))
        self.until(session, lambda game: game.state.active_player == "A" and game.state.step == "end_step" and bool(game.state.stack))
        resumed = self.replay(session, load=True)
        self.assertIsNone(self.resolve(resumed))
        self.assertEqual("exile", resumed.state.cards[target.object_id].zone)
        self.replay(resumed, load=True)

    def test_macabre_mockery_cleanup_precedes_the_later_sacrifice_and_control_still_matters(self):
        session = self.session(6030703)
        engine = session.engine
        spells = [self.add(engine, "Macabre Mockery", zone="hand", ref=f"mockery-{index}") for index in range(2)]
        targets = [self.add(engine, "Generic Bound Body", seat="B", zone="graveyard", ref=f"mockery-body-{index}") for index in range(2)]
        theft = self.add(engine, "Act of Treason", seat="B", zone="hand")
        for index, name in enumerate(("Mountain", "Plains", "Plains")):
            self.add(engine, name, seat="B", ref=f"mockery-mana-{index}")
        self.assert_closed(session, spells[0])
        self.prepare(session)
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine.state.phase, engine.state.step = "ending", "end_step"
        engine.state.phase_index = TURN_STEPS.index(("ending", "end_step"))
        engine.state.players["A"].mana_pool.update({"B": 4, "R": 4})
        engine._grant_priority("A")
        engine.pump()
        self.checkpoint(session)
        for spell, target in zip(spells, targets, strict=True):
            self.cast(session, "A", spell, [target.ref])
            self.assertIsNone(self.resolve(session))
            self.assertTrue(self.has_haste(session, target))
            self.assertEqual(4, engine._numeric_stat(target.object_id, "power"))
        self.assertEqual(2, sum(trigger.active for trigger in session.state.delayed_triggers))
        self.until(session, lambda game: game.state.active_player == "B" and game.state.phase == "precombat_main")
        for target in targets:
            self.assertEqual("battlefield", target.zone)
            self.assertFalse(self.has_haste(session, target))
            self.assertEqual(2, engine._numeric_stat(target.object_id, "power"))
        self.cast(session, "B", theft, [targets[1].ref])
        self.assertIsNone(self.resolve(session))
        self.assertEqual("B", targets[1].controller)
        self.until(session, lambda game: game.state.step == "end_step" and len(game.state.stack) == 2)
        self.assertIsNone(self.resolve(session))
        self.assertEqual(("graveyard", "battlefield"), tuple(target.zone for target in targets))
        self.replay(session, load=True)

    def test_greasefang_grants_haste_to_a_vehicle_then_returns_it_to_its_owner(self):
        session = self.session(6030704)
        source = self.add(session.engine, "Greasefang, Okiba Boss")
        vehicle = self.add(session.engine, "Sky Skiff", zone="graveyard")
        self.assert_closed(session, source)
        self.prepare(session)
        self.checkpoint(session)
        self.until(session, lambda game: game.state.pending_decision.kind == "semantic.target")
        result = session.act("pilot:A", {"action_id": "choose", "targets": [vehicle.ref]})
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(session))
        self.assertEqual("battlefield", vehicle.zone)
        self.assertTrue(self.has_haste(session, vehicle))
        self.assertNotIn("Creature", session.engine._effective_card_data(vehicle)["type_line"])
        self.until(session, lambda game: game.state.step == "end_step" and bool(game.state.stack))
        self.assertIsNone(self.resolve(session))
        self.assertEqual("hand", session.state.cards[vehicle.object_id].zone)
        self.assertIn(vehicle.object_id, session.state.players[vehicle.owner].zones["hand"])
        self.replay(session, load=True)

    def test_whip_replacement_survives_source_departure_and_countered_delayed_trigger(self):
        session = self.session(6030705)
        engine = session.engine
        whip = self.add(engine, "Whip of Erebos")
        target = self.add(engine, "Generic Bound Body", zone="graveyard", ref="whip-returned-body")
        foreign = self.add(engine, "Generic Bound Body", seat="C", zone="graveyard", ref="whip-ineligible-body")
        disenchant = self.add(engine, "Disenchant", seat="B", zone="hand")
        bounce = self.add(engine, "Unsummon", seat="B", zone="hand")
        stifle = self.add(engine, "Stifle", zone="hand")
        self.add(engine, "Plains", seat="B")
        self.add(engine, "Plains", seat="B", ref="whip-second-plains")
        self.add(engine, "Island", seat="B", ref="whip-bounce-mana")
        self.add(engine, "Island", seat="B", ref="whip-extra-bounce-mana")
        self.add(engine, "Island", ref="whip-stifle-mana")
        self.assert_closed(session, whip)
        self.prepare(session, mana={"B": 2, "C": 2})
        self.checkpoint(session)
        actions = session.packet("pilot:A", full=True)["decision"]["legal_actions"]
        activation, = [action for action in actions if action["id"].startswith(f"activate:{whip.ref}:")]
        command = {"action_id": activation["id"], "pay": "auto"}
        self.reject(session, "A", **command, targets=[foreign.ref])
        accepted = session.act("pilot:A", {**command, "targets": [target.ref]})
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertIsNone(self.resolve(session))
        target = session.state.cards[target.object_id]
        self.assertTrue(self.has_haste(session, target))
        rule, = [effect for effect in session.state.continuous_effects if isinstance(effect, ResolvedLinkedDepartureExile)]
        self.assertEqual((whip.ref, target.logical_object_id), (rule.source_id, rule.subject.logical_object_id))
        self.assertEqual(rule, ResolvedLinkedDepartureExile.from_dict(rule.to_dict()))

        self.cast(session, "B", disenchant, [whip.ref])
        self.assertIsNone(self.resolve(session))
        self.assertEqual("graveyard", session.state.cards[whip.object_id].zone)
        self.until(session, lambda game: game.state.step == "end_step" and bool(game.state.stack))
        session = self.replay(session, load=True)
        trigger, = session.state.stack
        self.assertEqual((whip.object_id, "A"), (trigger.source_object_id, trigger.controller))
        self.cast(session, "A", stifle, [trigger.ref])
        self.assertIsNone(self.resolve(session))
        self.assertEqual("battlefield", session.state.cards[target.object_id].zone)
        self.assertIn(rule, session.state.continuous_effects)
        self.assertFalse(any(effect.active for effect in session.state.delayed_triggers))
        self.cast(session, "B", bounce, [target.ref])
        self.assertIsNone(self.resolve(session))
        self.assertEqual("exile", session.state.cards[target.object_id].zone)
        self.assertNotIn(rule, session.state.continuous_effects)
        self.replay(session, load=True)

    def test_gruesome_encore_binds_opponents_card_and_skips_its_blinked_incarnation(self):
        session = self.session(6030706)
        engine = session.engine
        spell = self.add(engine, "Gruesome Encore", zone="hand")
        blink = self.add(engine, "Cloudshift", zone="hand")
        target = self.add(engine, "Generic Bound Body", seat="B", zone="graveyard", ref="encore-foreign-body")
        friendly = self.add(engine, "Generic Bound Body", zone="graveyard", ref="encore-own-body")
        self.add(engine, "Plains", ref="encore-blink-mana")
        self.assert_closed(session, spell)
        self.prepare(session, mana={"B": 1, "C": 2})
        self.checkpoint(session)
        self.reject(session, "A", action_id=f"cast:{spell.ref}", pay="auto", targets=[friendly.ref])
        self.cast(session, "A", spell, [target.ref])
        self.assertIsNone(self.resolve(session))
        target = session.state.cards[target.object_id]
        self.assertEqual(("B", "A", "battlefield"), (target.owner, target.controller, target.zone))
        self.assertTrue(self.has_haste(session, target))
        original = target.logical_object_id
        self.cast(session, "A", blink, [target.ref])
        self.assertIsNone(self.resolve(session))
        self.assertNotEqual(original, session.state.cards[target.object_id].logical_object_id)
        self.assertFalse(self.has_haste(session, target))
        self.assertFalse(any(isinstance(effect, ResolvedLinkedDepartureExile) for effect in session.state.continuous_effects))
        self.until(session, lambda game: game.state.step == "end_step" and bool(game.state.stack))
        session = self.replay(session, load=True)
        self.assertIsNone(self.resolve(session))
        self.assertEqual("battlefield", session.state.cards[target.object_id].zone)
        self.replay(session, load=True)

    def test_moira_and_teshar_schedule_one_departure_from_a_multitraited_historic_cast(self):
        session = self.session(6030707)
        engine = session.engine
        source = self.add(engine, "Moira and Teshar")
        historic = self.add(engine, "Whip of Erebos", zone="hand")
        target = self.add(engine, "Sol Ring", zone="graveyard")
        land = self.add(engine, "Plains", zone="graveyard")
        self.assert_closed(session, source)
        self.prepare(session, mana={"B": 2, "C": 2})
        self.checkpoint(session)
        self.cast(session, "A", historic)
        self.assertEqual("semantic.target", session.state.pending_decision.kind)
        self.reject(session, "A", action_id="choose", targets=[land.ref])
        result = session.act("pilot:A", {"action_id": "choose", "targets": [target.ref]})
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(session))
        self.assertEqual("battlefield", session.state.cards[target.object_id].zone)
        trigger, = session.state.delayed_triggers
        self.assertEqual((source.object_id, "A"), (trigger.source_object_id, trigger.controller))
        self.until(session, lambda game: game.state.step == "end_step" and bool(game.state.stack))
        self.assertIsNone(self.resolve(session))
        self.assertEqual("exile", session.state.cards[target.object_id].zone)
        self.assertFalse(any(isinstance(effect, ResolvedLinkedDepartureExile) for effect in session.state.continuous_effects))
        self.replay(session, load=True)


PORTAL_ORACLE = (
    "When this artifact enters, each opponent sacrifices three creatures of their choice.\n"
    "At the beginning of your upkeep, put target creature card from a graveyard onto the battlefield under your control. "
    "It's a Phyrexian in addition to its other types."
)


class PortalToPhyrexiaNativeTests(unittest.TestCase):
    register_fixture_cards_on_add = True
    session = ReanimationFollowupNativeTests.session
    add = ReanimationFollowupNativeTests.add
    checkpoint = ReanimationFollowupNativeTests.checkpoint
    resolve = ReanimationFollowupNativeTests.resolve
    replay = ReanimationFollowupNativeTests.replay
    prepare = ReanimationFollowupNativeTests.prepare
    cast = ReanimationFollowupNativeTests.cast
    assert_closed = ReanimationFollowupNativeTests.assert_closed
    native_until = ReanimationFollowupNativeTests.native_until
    until = ReanimationFollowupNativeTests.until
    tearDownClass = classmethod(ReanimationFollowupNativeTests.tearDownClass.__func__)

    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / "ordered-opponent-portal.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures" / name for name in (
            "bound-effect-program-cards.json", "scryfall-exact-lists.json",
            "reanimation-followup-cards.json", "chosen-type-event-cards.json",
            "conditional-receiver-union-cards.json",
        )], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Ordered opponent and upkeep witness", [
            DeckEntry("Generic Bound Commander", 1, "commander"), DeckEntry("Generic Bound Plains", 20),
        ], ["Generic Bound Commander"])

    def command(self, game, seat, **command):
        result = game.act("pilot:" + seat, command)
        self.assertTrue(result.ok, result.summary)

    def reject_portal(self, game, seat, **command):
        before = authoritative_state_hash(game.state)
        result = game.act("pilot:" + seat, command)
        self.assertFalse(result.ok, result.summary)
        self.assertEqual(before, authoritative_state_hash(game.state))

    def test_portal_opponents_choose_available_three_with_actual_owner_orders(self):
        game = self.session(611201)
        engine = game.engine
        portal = self.add(engine, "Portal to Phyrexia", zone="hand", ref="portal-source")
        own = self.add(engine, "Generic Bound Body", ref="portal-own-creature")
        b = [self.add(engine, "Generic Bound Body", seat="B", ref=f"portal-b-{i}") for i in range(3)]
        c = self.add(engine, "Generic Bound Body", seat="C", ref="portal-c")
        borrowed = [self.add(engine, "Generic Bound Body", ref=f"portal-borrowed-{i}") for i in range(2)]
        for card, seat in zip(borrowed, "BC", strict=True):
            engine.change_control(card.object_id, seat, reason="borrowed creature fixture")
        private = self.add(engine, "Generic Bound Growth", seat="D", zone="hand", ref="portal-unrelated-private")
        self.assert_closed(game, portal)
        self.prepare(game, mana={"C": 9})
        self.checkpoint(game)
        self.cast(game, "A", portal)
        self.assertEqual("choice.apnap", self.resolve(game).kind)
        self.assertEqual(1, sum(row.source_object_id == portal.object_id for row in game.state.stack))
        self.assertEqual(["pilot:B"], game.pending_principals())
        b_selected = [b[0], b[1], borrowed[0]]
        self.reject_portal(game, "B", action_id="choose", cards=[card.ref for card in b_selected[:2]])
        self.reject_portal(game, "A", action_id="choose", cards=[card.ref for card in b_selected])
        self.command(game, "B", action_id="choose", cards=[card.ref for card in b_selected])
        self.assertEqual(["pilot:C"], game.pending_principals())
        decision = game.packet("pilot:C", full=True)["decision"]
        self.assertEqual([card.ref for card in b_selected], decision["ctx"]["prior_public_choices"]["B"])
        self.assertNotIn(private.ref, json.dumps(decision))
        self.command(game, "C", action_id="choose", cards=[borrowed[1].ref, c.ref])
        held = (*b_selected, borrowed[1], c)
        self.assertTrue(all(game.state.cards[card.object_id].zone == "battlefield" for card in held))
        game = self.replay(game, load=True)
        for seat, ordered in (("A", borrowed[::-1]), ("B", b[:2][::-1])):
            decision = game.packet("pilot:" + seat, full=True)["decision"]
            self.assertEqual("permanent.composition_order", decision["kind"])
            refs = [card.ref for card in ordered]
            self.assertEqual(set(refs), {row["id"] for row in decision["ctx"]["cards"]})
            self.reject_portal(game, "D", action_id="order", cards=refs)
            self.reject_portal(game, seat, action_id="order", cards=[refs[0], refs[0]])
            self.command(game, seat, action_id="order", cards=refs)
            if seat == "A":
                self.assertTrue(all(game.state.cards[card.object_id].zone == "battlefield" for card in held))
        self.assertIsNone(self.resolve(game))
        self.assertEqual([card.object_id for card in borrowed[::-1]], game.state.players["A"].zones["graveyard"])
        self.assertEqual([card.object_id for card in b[:2][::-1]], game.state.players["B"].zones["graveyard"])
        self.assertEqual("graveyard", game.state.cards[c.object_id].zone)
        for card in (b[2], own):
            self.assertEqual("battlefield", game.state.cards[card.object_id].zone)
        self.replay(game, load=True)

    def upkeep_target(self, game, portal):
        self.assert_closed(game, portal)
        self.prepare(game, actor="D", active="D")
        self.checkpoint(game)
        self.until(game, lambda current: current.state.pending_decision is not None
                   and current.state.pending_decision.kind == "semantic.target")
        self.assertEqual("A", game.state.active_player)
        self.assertEqual("upkeep", game.state.step)
        self.assertEqual(["pilot:A"], game.pending_principals())
        decision = game.packet("pilot:A", full=True)["decision"]
        return decision["ctx"]["target_schema"]["legal_refs"]

    def test_portal_upkeep_targets_any_graveyard_and_enters_with_its_added_subtype(self):
        game = self.session(611205)
        engine = game.engine
        portal = self.add(engine, "Portal to Phyrexia", ref="portal-upkeep-source")
        target = self.add(engine, "Generic Bound Body", seat="B", zone="graveyard", ref="portal-upkeep-creature")
        other = self.add(engine, "Generic Bound Body", seat="C", zone="graveyard", ref="portal-other-yard-creature")
        noncreature = self.add(engine, "Generic Bound Plains", seat="D", zone="graveyard", ref="portal-yard-land")
        censor = self.add(engine, "Generic Non-Phyrexian Entry Witness", seat="C", ref="portal-censor")
        discovery = self.add(engine, "Kindred Discovery", ref="portal-phyrexian-observer")
        discovery.annotations["chosen_creature_type"] = "phyrexian"
        removal = self.add(engine, "Disenchant", seat="B", zone="hand", ref="portal-remove-source")
        bounce = self.add(engine, "Unsummon", seat="D", zone="hand", ref="portal-remove-result")
        for i in range(2):
            self.add(engine, "Generic Bound Plains", seat="B", ref=f"portal-removal-mana-{i}")
        self.add(engine, "Island", seat="D", ref="portal-bounce-mana")
        for card in (censor, discovery, removal, bounce):
            self.assert_closed(game, card)
        offered = self.upkeep_target(game, portal)
        self.assertEqual({target.ref, other.ref}, set(offered))
        self.assertNotIn(noncreature.ref, offered)
        self.reject_portal(game, "B", action_id="choose", targets=[target.ref])
        self.reject_portal(game, "A", action_id="choose", targets=[noncreature.ref])
        self.command(game, "A", action_id="choose", targets=[target.ref])
        hand_before = len(game.state.players["A"].zones["hand"])
        self.cast(game, "B", removal, [portal.ref])
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        current = game.state.cards[target.object_id]
        self.assertEqual(("battlefield", "B", "A", False), (current.zone, current.owner, current.controller, current.tapped))
        _, subtypes, _ = game.engine._type_parts(game.engine._effective_card_data(current)["type_line"])
        self.assertEqual({"human", "phyrexian"}, subtypes)
        self.assertEqual("graveyard", game.state.cards[portal.object_id].zone)
        self.assertEqual(hand_before + 1, len(game.state.players["A"].zones["hand"]))
        self.cast(game, "D", bounce, [current.ref])
        self.assertIsNone(self.resolve(game))
        departed = game.state.cards[target.object_id]
        self.assertEqual(("hand", "B"), (departed.zone, departed.owner))
        self.assertNotIn("Phyrexian", game.engine._effective_card_data(departed)["type_line"])
        self.replay(game, load=True)

    def test_portal_upkeep_rejects_a_stale_original_target(self):
        game = self.session(611208)
        engine = game.engine
        portal = self.add(engine, "Portal to Phyrexia", ref="portal-stale-source")
        target = self.add(engine, "Generic Bound Body", seat="C", zone="graveyard", ref="portal-stale-target")
        response = self.add(engine, "Return to Nature", seat="B", zone="hand", ref="portal-stale-exile")
        private = self.add(engine, "Generic Bound Growth", seat="D", zone="hand", ref="portal-stale-private")
        for i in range(2):
            self.add(engine, "Forest", seat="B", ref=f"portal-stale-mana-{i}")
        self.assert_closed(game, response)
        self.assertIn(target.ref, self.upkeep_target(game, portal))
        self.command(game, "A", action_id="choose", targets=[target.ref])
        for _ in range(12):
            if game.state.priority_player == "B":
                break
            self.command(game, game.state.priority_player, action_id="pass")
        self.command(game, "B", action_id=f"cast:{response.ref}", modes=["mode_3"], targets=[target.ref], pay="auto")
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        current = game.state.cards[target.object_id]
        self.assertEqual("exile", current.zone)
        self.assertNotIn("Phyrexian", game.engine._effective_card_data(current)["type_line"])
        for seat in "ABC":
            self.assertNotIn(private.ref, json.dumps(game.packet("pilot:" + seat, full=True)))
        self.replay(game, load=True)

    def test_portal_strict_nodes_reject_changed_scope_and_capability_withdrawal(self):
        from dataclasses import replace
        from quorune.card_programs.binding import bind_semantic_program_runtime
        from quorune.rules.ordered_opponent_creature_sacrifice import CAPABILITY as ordered_capability, ordered_opponent_sacrifice_is_closed
        from quorune.rules.reanimation_entry_subtype import CAPABILITY as subtype_capability, entry_subtype_reanimation_node_capabilities

        ir = compile_oracle_card(record(PORTAL_ORACLE, "Artifact"), capability_registry=self.registry)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        entry, upkeep = ir.faces[0].nodes
        self.assertEqual(("permanent.enter.self", "step.begin"), (entry.event, upkeep.event))
        self.assertFalse(ordered_opponent_sacrifice_is_closed({**entry.effects[0], "count": 2}))
        self.assertFalse(ordered_opponent_sacrifice_is_closed({**entry.effects[0], "players": "all"}))
        self.assertEqual((), entry_subtype_reanimation_node_capabilities(upkeep.effects,
                            {**upkeep.target_schema, "owner_relation": "you"}))
        self.assertEqual((), entry_subtype_reanimation_node_capabilities(
            ({**upkeep.effects[0], "reanimation_entry_subtype": "not a creature type"},), upkeep.target_schema))
        game = self.session(611211)
        portal = self.add(game.engine, "Portal to Phyrexia", zone="hand")
        self.assert_closed(game, portal)
        current = game.engine.semantics.programs_for_oracle(portal.oracle_id)
        self.assertEqual({"trigger:front:n1", "trigger:front:n2"}, {program.ability_id for program in current})
        self.assertEqual(2, len(current))
        for authority in (ordered_capability, subtype_capability):
            program, = [value for value in game.engine.semantics.programs_for_oracle(portal.oracle_id)
                        if authority in value.capability_dependencies]
            deps = tuple(value for value in program.capability_dependencies if value != authority)
            mutant = replace(program, capability_dependencies=deps,
                             capability_closure=self.registry.closure(deps, profile="commander_review").to_dict())
            binding = bind_semantic_program_runtime(mutant, capability_registry=self.registry, profile="commander_review")
            self.assertFalse(binding["strict"])
            self.assertIn(f"capability:undeclared_runtime_dependency:{authority}", binding["blockers"])
