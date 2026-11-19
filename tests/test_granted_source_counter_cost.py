from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.ability_fragments import ability_fragment_from_dict
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.granted_activation_model import (
    GRANTED_SOURCE_COUNTER_CAPABILITY, GRANTED_SPORE_COUNTER_COST, GrantedActivatedAbilitySpec,
)
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.activation.query import _typed_granted_abilities
from quorune.rules.activation_counter_cost import SourceCounterRemovalCost
from quorune.rules.capabilities import load_default_capability_registry
from quorune.turn_step_owner import TURN_STEPS
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card
import test_temporary_keyword_removal as native_turn


ANCIENT = "Sporoloth Ancient"
THALLID = "Thallid"
SPORESOWER = "Sporesower Thallid"
PROFILE = "commander_review"
GRANT_CAPABILITY = "continuous.ability.fixed_query_grant"
SPORE_PAYMENT = SourceCounterRemovalCost.from_dict(GRANTED_SPORE_COUNTER_COST)
FIXTURES = ROOT / "tests" / "fixtures"


class GrantedSourceCounterCostTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        folder = Path(cls.temporary.name)
        selected = {
            "bound-effect-program-cards.json": {"Generic Bound Plains"},
            "source-attack-alone-cards.json": {"Isamaru, Hound of Konda"},
            "attached-destruction-set-cards.json": {"Murder", "Swamp", "Grizzly Bears", "Unsummon"},
        }
        fixtures = [FIXTURES / "granted-source-counter-cost-cards.json"]
        for name, names in selected.items():
            data = json.loads((FIXTURES / name).read_bytes())
            data["cards"] = [card for card in data["cards"] if card["name"] in names]
            assert {card["name"] for card in data["cards"]} == names
            ids = {card["oracle_id"] for card in data["cards"]}
            data["rulings"] = [row for row in data.get("rulings", ()) if row["oracle_id"] in ids]
            destination = folder / name
            destination.write_text(json.dumps(data), encoding="utf-8")
            fixtures.append(destination)
        path = folder / "granted-source-counter-cost.sqlite3"
        build_fixture_database(fixtures, path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Granted source counter cost", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def test_complete_original_keeps_the_upkeep_and_independent_granted_child(self):
        card = self.db.lookup(ANCIENT, fuzzy=False)
        ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile=PROFILE)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        upkeep, carrier, child = ir.faces[0].nodes
        self.assertIn("counter.producer.fixed_event_trigger", upkeep.capability_dependencies)
        self.assertTrue({GRANT_CAPABILITY, GRANTED_SOURCE_COUNTER_CAPABILITY, "token.creation.fixed_definition"}
            <= set(carrier.capability_dependencies))
        self.assertEqual(SPORE_PAYMENT.to_dict(), child.cost["source_counter_removal_cost"])
        self.assertIn("token.creation.fixed_definition", child.capability_dependencies)
        handler, = carrier.handlers
        fragment, = handler["modifier"]["add_ability_fragments"]
        spec = ability_fragment_from_dict(fragment)
        self.assertIsInstance(spec, GrantedActivatedAbilitySpec)
        self.assertEqual((6, GRANTED_SPORE_COUNTER_COST), (spec.schema_version, spec.source_counter_removal_cost))
        self.assertEqual(spec, GrantedActivatedAbilitySpec.from_dict(spec.to_dict()))
        admitted, _ = register_card(self.db, self.registry, ANCIENT)
        self.assertTrue(admitted["strict_capability_ready"], admitted["blockers"])

    def test_exact_quote_and_granted_cost_payload_exclude_unrepresented_prices_and_scopes(self):
        card = self.db.lookup(ANCIENT, fuzzy=False)
        for old, new in (
            ("Remove two", "Remove three"),
            ("Remove two spore counters", "Remove two counters"),
            ("Creatures you control have", "All creatures have"),
            ("Saproling creature token.", "Saproling creature token. Draw a card."),
            ("from this creature", "from another creature you control"),
        ):
            with self.subTest(new=new):
                changed = replace(card, oracle_text=card.oracle_text.replace(old, new))
                ir = compile_oracle_card(changed, capability_registry=self.registry, capability_profile=PROFILE)
                self.assertNotEqual("exact", ir.status)

        original = GrantedActivatedAbilitySpec("version-one", "probe:version-one", "{T}", "Scry 1.", tap_source=True)
        value = original.to_dict()
        self.assertNotIn("source_counter_removal_cost", value)
        self.assertEqual(original, GrantedActivatedAbilitySpec.from_dict(value))
        price = SPORE_PAYMENT.to_dict()
        exact = {**value, "schema_version": 6, "tap_source": False, "source_counter_removal_cost": price,
            "cost_text": "Remove two spore counters from this creature",
            "effect_text": "Create a 1/1 green Saproling creature token."}
        self.assertEqual(6, GrantedActivatedAbilitySpec.from_dict(exact).schema_version)
        for changes in (
            {"schema_version": 5}, {"tap_source": True}, {"mana": {"G": 1}},
            {"source_counter_removal_cost": {**price, "counter_name": "charge"}},
            {"source_counter_removal_cost": {**price, "amount": 3}},
            {"source_counter_removal_cost": {**price, "amount": 2.0}},
            {"source_counter_removal_cost": {**price, "schema_version": True}},
            {"source_counter_removal_cost": {**price, "schema_version": 2, "counter_name": None}},
        ):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                GrantedActivatedAbilitySpec.from_dict({**exact, **changes})

    @staticmethod
    def actions(game, seat):
        return game.packet("pilot:" + seat, full=True)["decision"]["ctx"]["legal"]["actions"]

    @staticmethod
    def abilities(game, card):
        return game.engine._activated_abilities(game.state.cards[card.object_id])

    def grant(self, game, donor, holder):
        granted, = [ability for ability in self.abilities(game, holder)
            if ability.builtin_semantic_key and ability.builtin_semantic_key.startswith(donor.oracle_id + ":")]
        return granted

    def assert_empty_grant_unavailable(self, game, donor, holder):
        grant = self.grant(game, donor, holder)
        action = f"activate:{holder.ref}:{grant.ability_id}"
        self.assertNotIn(action, {row["id"] for row in self.actions(game, "A")},
            "A zero-spore creature has the grant but cannot offer it")
        return action

    def test_actual_upkeep_and_granted_price_keep_native_ability_and_paid_stack_authority(self):
        game = self.session(70718311)
        donor = self.add(game.engine, ANCIENT)
        holder = self.add(game.engine, THALLID)
        holder.counters["spore"] = 1
        upkeep_support = self.add(game.engine, SPORESOWER)
        empty = self.add(game.engine, "Grizzly Bears")
        foreign = self.add(game.engine, THALLID, seat="B", ref="foreign-spore-holder")
        foreign.counters["spore"] = 2
        removal = self.add(game.engine, "Murder", seat="B", zone="hand")
        private = self.add(game.engine, "Unsummon", seat="C", zone="hand", ref="private-spore-test")
        for n in range(3):
            self.add(game.engine, "Swamp", seat="B", ref=f"spore-removal-land-{n}")
        for card in (donor, holder, upkeep_support, foreign, removal):
            self.assert_closed(game, card)
        prior = game.state.turn_order[game.state.turn_order.index("A") - 1]
        game.engine.permissions.invalidate_current()
        game.state.pending_decision = None
        game.state.started = True
        game.state.active_player = game.state.last_normal_turn_player = prior
        game.state.phase, game.state.step = ("ending", "end_step")
        game.state.phase_index = TURN_STEPS.index((game.state.phase, game.state.step))
        game.engine._grant_priority(prior)
        game.engine.pump()
        self.checkpoint(game)
        native_turn.TemporaryKeywordRemovalRuntimeTests.advance(
            self, game, lambda session: session.state.active_player == "A" and session.state.step == "main")
        self.assertEqual(2, game.state.cards[donor.object_id].counters.get("spore"))
        self.assertEqual(3, game.state.cards[holder.object_id].counters.get("spore"))
        self.assertEqual(0, game.state.cards[empty.object_id].counters.get("spore", 0))
        self.priority(game, "A")
        offered_ids = {row["id"] for row in self.actions(game, "A")}

        empty_action = self.assert_empty_grant_unavailable(game, donor, empty)
        self.assertFalse(any(a.builtin_semantic_key and a.builtin_semantic_key.startswith(donor.oracle_id + ":")
            for a in self.abilities(game, foreign)))

        gained = self.grant(game, donor, holder)
        self.assertEqual(SPORE_PAYMENT, gained.source_counter_removal_cost)
        printed, = [a for a in self.abilities(game, holder)
            if a.source_counter_removal_cost and a.source_counter_removal_cost.amount == 3]
        printed_id = f"activate:{holder.ref}:{printed.ability_id}"
        granted_id = f"activate:{holder.ref}:{gained.ability_id}"
        self.assertTrue({printed_id, granted_id} <= offered_ids)
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:B", {"action_id": granted_id, "pay": "auto"}).ok)
        self.assertFalse(game.act("pilot:A", {"action_id": empty_action, "pay": "auto"}).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))

        paid = game.act("pilot:A", {"action_id": granted_id, "pay": "auto"})
        self.assertTrue(paid.ok, paid.summary)
        self.assertEqual((1, 2, 2), tuple(game.state.cards[card.object_id].counters.get("spore")
            for card in (holder, donor, foreign)))
        ability, = game.state.stack
        self.assertEqual((holder.object_id, "A"), (ability.source_object_id, ability.controller))
        self.assertFalse(any(card.is_token and card.zone == "battlefield" for card in game.state.cards.values()))

        self.cast(game, "B", removal, [donor.ref])
        self.resolve_top(game)
        self.assertEqual("graveyard", game.state.cards[donor.object_id].zone)
        self.assertFalse(any(a.ability_id == gained.ability_id for a in self.abilities(game, holder)))
        self.assertEqual([ability.ref], [item.ref for item in game.state.stack])
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        token, = [card for card in game.state.cards.values() if card.is_token and card.zone == "battlefield"]
        data = game.engine._effective_card_data(token)
        self.assertEqual(("A", "1", "1", ["G"]),
            (token.controller, str(data["power"]), str(data["toughness"]), data["colors"]))
        self.assertIn("saproling", game.engine._type_parts(data["type_line"])[1])
        self.assertEqual(1, game.state.cards[holder.object_id].counters.get("spore"))
        for seat in "ABD":
            packet = json.dumps(game.packet("pilot:" + seat, full=True))
            self.assertNotIn(private.ref, packet)
            self.assertNotIn(private.printed_name, packet)
        self.replay(game, load=True)

    def test_omitting_counter_price_projection_breaks_actual_offer_availability(self):
        def unpriced(data):
            return tuple(replace(ability, source_counter_removal_cost=None)
                if ability.source_counter_removal_cost == SPORE_PAYMENT else ability
                for ability in _typed_granted_abilities(data))

        game = self.session(70718312)
        donor = self.add(game.engine, ANCIENT)
        empty = self.add(game.engine, "Grizzly Bears")
        self.assert_closed(game, donor)
        with patch("quorune.rules.activation.query._typed_granted_abilities", unpriced):
            self.prepare(game)
            self.checkpoint(game)
            with self.assertRaisesRegex(AssertionError, "A zero-spore creature has the grant but cannot offer it"):
                self.assert_empty_grant_unavailable(game, donor, empty)

    def test_verified_withdrawal_separates_grant_and_independent_printed_counter_price(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests

        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        known = set(discover_tests(ROOT).values())

        def verify(rows):
            registry = CapabilityRegistry(rows)
            index = build_index(registry_value=rows, declaration_source=declarations, discovered_test_ids=known)
            registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
            return registry

        names = (ANCIENT, THALLID, "Grizzly Bears")
        baseline = verify(deepcopy(raw))
        for name in names:
            admitted, _ = register_card(self.db, baseline, name)
            self.assertTrue(admitted["strict_capability_ready"], admitted["blockers"])
        for cap, blocked in (
            (GRANT_CAPABILITY, {ANCIENT}), (GRANTED_SOURCE_COUNTER_CAPABILITY, {ANCIENT, THALLID}),
        ):
            with self.subTest(cap=cap):
                rows = deepcopy(raw)
                next(item for item in rows["capabilities"] if item["id"] == cap).update(
                    status="blocked", blockers=["Granted source counter cost dependency withdrawn"])
                registry = verify(rows)
                closure = registry.closure((cap,), profile=PROFILE)
                self.assertIn("status:" + cap + ":blocked", closure.blockers)
                self.assertFalse(any("unverified" in reason for reason in closure.blockers))
                for name in names:
                    admitted, _ = register_card(self.db, registry, name)
                    self.assertEqual(name not in blocked, admitted["strict_capability_ready"],
                        (name, admitted["blockers"]))
