from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.activation_paid_characteristic_model import (
    ACTIVATION_PAID_STAT_CAPABILITY,
    ACTIVATION_PAID_STAT_CONTEXT,
    ActivationPaidCreatureFact,
)
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.activation.model import ActivationProposalError, ActivationProposalRequest
from quorune.rules.activation.paid_characteristic_results import activation_paid_stat_program_is_closed
from quorune.rules.activation.proposal import build_activation_offer, build_activation_proposal
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


BONEYARD = "Animal Boneyard"
DIRECT = "Miren, the Moaning Well"
PROFILE = "commander_review"
FIXTURES = ROOT / "tests" / "fixtures"
ORIGINALS = FIXTURES / "granted-paid-creature-toughness-cards.json"
GRANT = "continuous.attached.fixed_characteristics"


class GrantedPaidCreatureToughnessTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        folder = Path(cls.temporary.name)
        selected = {
            "bound-effect-program-cards.json": {"Generic Bound Plains", "Disenchant"},
            "targeted-graveyard-exile-cards.json": {"Forest", "Grizzly Bears", "Shock"},
            "source-attack-alone-cards.json": {"Isamaru, Hound of Konda"},
            "activation-paid-sacrifice-stat-cards.json": {DIRECT},
            "self-cast-restriction-cards.json": {"Animate Land"},
        }
        fixtures = [ORIGINALS]
        for name, names in selected.items():
            fixture = json.loads((FIXTURES / name).read_bytes())
            fixture["cards"] = [card for card in fixture["cards"] if card["name"] in names]
            assert {card["name"] for card in fixture["cards"]} == names
            ids = {card["oracle_id"] for card in fixture["cards"]}
            fixture["rulings"] = [row for row in fixture.get("rulings", ()) if row["oracle_id"] in ids]
            output = folder / name
            output.write_text(json.dumps(fixture), encoding="utf-8")
            fixtures.append(output)
        path = folder / "granted-paid-creature.sqlite3"
        build_fixture_database(fixtures, path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Granted paid creature", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def test_complete_original_retains_enchant_land_and_closed_granted_price(self):
        card = self.db.lookup(BONEYARD, fuzzy=False)
        ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile=PROFILE)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        enchant, outer, paid = ir.faces[0].nodes
        self.assertIn("attachment.aura.simple_object", enchant.capability_dependencies)
        self.assertIn(GRANT, outer.capability_dependencies)
        self.assertIn(ACTIVATION_PAID_STAT_CAPABILITY, paid.capability_dependencies)
        ready, registry = register_card(self.db, self.registry, BONEYARD)
        self.assertTrue(ready["strict_capability_ready"], ready["blockers"])
        program, = [p for p in registry.programs_for_oracle(card.oracle_id) if p.event == "activate"]
        self.assertTrue(program.provenance["granted_only"])
        self.assertFalse(program.handlers)
        self.assertTrue(activation_paid_stat_program_is_closed(program))
        self.assertTrue(program.cost_schema["tap_source"])
        self.assertFalse(any(program.cost_schema["mana"].values()))
        effect, = program.effects
        creature, = program.cost_schema["choices"]
        for changed in (
            replace(program, provenance={**program.provenance, "granted_only": False}),
            replace(program, effects=[{**effect, "player": "$opponent"}]),
            replace(program, effects=[{**effect, "delta": {**effect["delta"], "characteristic": "power"}}]),
            replace(program, cost_schema={**program.cost_schema, "mana": {"C": 1}}),
            replace(program, cost_schema={**program.cost_schema, "choices": [{**creature, "other": 1}]}),
        ):
            self.assertFalse(activation_paid_stat_program_is_closed(changed))
            result = bind_semantic_program_runtime(changed, capability_registry=self.registry, profile=PROFILE)
            self.assertFalse(result["strict"], result)
        old, _ = register_card(self.db, self.registry, DIRECT)
        self.assertTrue(old["strict_capability_ready"], old["blockers"])

    def grant_offer(self, game, aura, land, paid_ref):
        self.priority(game, "B")
        program, = [p for p in game.engine.semantics.programs_for_oracle(aura.oracle_id)
            if p.provenance.get("granted_only")]
        source = game.state.cards[land.object_id]
        ability, = [a for a in game.engine._activated_abilities(source) if a.builtin_semantic_key == program.key]
        action_id = f"activate:{source.ref}:{ability.ability_id}"
        offered = game.packet("pilot:B", full=True)["decision"]["ctx"]["legal"]["actions"]
        self.assertTrue(any(row["id"] == action_id for row in offered))
        return {"action_id": action_id, "cost_objects": [paid_ref], "pay": "auto"}

    def assert_mismatched_price_is_closed(self, game, land, command):
        current_abilities = game.engine._activated_abilities
        correct, = [a for a in current_abilities(land)
            if f"activate:{land.ref}:{a.ability_id}" == command["action_id"]]
        mismatched = replace(correct, mana={**correct.mana, "C": 1})

        def inconsistent(source):
            return tuple(mismatched if source.object_id == land.object_id and a == correct else a
                for a in current_abilities(source))

        request = ActivationProposalRequest("B", land.ref, ("battlefield",), correct.ability_id,
            submission={"cost_objects": command["cost_objects"], "pay": "auto"})
        reason = "unresolved_granted_paid_stat_price"
        with patch.object(game.engine, "_activated_abilities", side_effect=inconsistent):
            offer = build_activation_offer(game.engine, "B", land, mismatched)
            self.assertIsNone(offer.offer)
            self.assertEqual(reason, offer.reason)
            with self.assertRaises(ActivationProposalError) as rejected:
                build_activation_proposal(game.engine, request)
            self.assertEqual(reason, rejected.exception.reason)

    def test_actual_foreign_land_pays_hidden_child_and_gains_paid_toughness_after_aura_leaves(self):
        game = self.session(60821321)
        aura = self.add(game.engine, BONEYARD, zone="hand")
        land = self.add(game.engine, "Forest", seat="B", ref="foreign-grant-land")
        paid = self.add(game.engine, "Grizzly Bears", seat="B", ref="paid-five-toughness")
        foreign = self.add(game.engine, "Grizzly Bears", ref="foreign-cost-creature")
        removal = self.add(game.engine, "Disenchant", zone="hand")
        private = self.add(game.engine, "Shock", seat="D", zone="hand", ref="paid-grant-private")
        paid.counters["+1/+1"] = 3
        self.add(game.engine, "Generic Bound Plains", ref="paid-grant-extra-white")
        self.assert_closed(game, aura)
        self.prepare(game, mana={"C": 4, "W": 2})
        self.checkpoint(game)
        self.cast(game, "A", aura, [land.ref])
        self.assertIsNone(self.resolve(game))
        self.assertEqual(land.object_id, game.state.cards[aura.object_id].attached_to)

        command = self.grant_offer(game, aura, land, paid.ref)
        before = authoritative_state_hash(game.state)
        self.assert_mismatched_price_is_closed(game, land, command)
        self.assertFalse(game.act("pilot:A", command).ok)
        self.assertFalse(game.act("pilot:B", {**command, "cost_objects": [foreign.ref]}).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        result = game.act("pilot:B", command)
        self.assertTrue(result.ok, result.summary)
        self.assertTrue(game.state.cards[land.object_id].tapped)
        self.assertEqual("graveyard", game.state.cards[paid.object_id].zone)
        self.assertFalse(game.state.cards[paid.object_id].counters)
        item, = game.state.stack
        self.assertEqual("B", item.controller)
        self.assertEqual(land.object_id, item.source_object_id)
        fact = ActivationPaidCreatureFact.from_dict(item.context[ACTIVATION_PAID_STAT_CONTEXT])
        self.assertEqual((paid.ref, 5), (fact.paid_ref, fact.stats.toughness))
        self.assertEqual(40, game.state.players["B"].life)

        self.cast(game, "A", removal, [aura.ref])
        self.resolve_top(game)
        self.assertEqual("graveyard", game.state.cards[aura.object_id].zone)
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual([40, 45, 40, 40], [game.state.players[seat].life for seat in "ABCD"])
        for seat in "ABC":
            self.assertNotIn(private.ref, json.dumps(game.packet("pilot:" + seat, full=True)))
        self.replay(game, load=True)

    def test_actual_animated_land_can_pay_its_own_tap_and_sacrifice_costs(self):
        game = self.session(60821322)
        aura = self.add(game.engine, BONEYARD, zone="hand")
        animate = self.add(game.engine, "Animate Land", zone="hand")
        land = self.add(game.engine, "Forest", seat="B", ref="paid-granted-source-land")
        self.add(game.engine, "Forest", ref="paid-grant-animate-mana")
        land.acquired_control_turn_count = -1
        for source in (aura, animate):
            self.assert_closed(game, source)
        self.prepare(game, mana={"C": 2, "W": 1, "G": 1})
        self.checkpoint(game)
        self.cast(game, "A", aura, [land.ref])
        self.assertIsNone(self.resolve(game))
        self.cast(game, "A", animate, [land.ref])
        self.assertIsNone(self.resolve(game))
        self.assertEqual(3, game.engine._numeric_stat(land.object_id, "toughness"))

        accepted = game.act("pilot:B", self.grant_offer(game, aura, land, land.ref))
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertEqual("graveyard", game.state.cards[land.object_id].zone)
        self.assertEqual("graveyard", game.state.cards[aura.object_id].zone)
        item, = game.state.stack
        fact = ActivationPaidCreatureFact.from_dict(item.context[ACTIVATION_PAID_STAT_CONTEXT])
        self.assertEqual((land.ref, 3), (fact.paid_ref, fact.stats.toughness))
        self.assertIsNone(self.resolve(game))
        self.assertEqual(43, game.state.players["B"].life)
        self.replay(game, load=True)

    def test_verified_withdrawals_distinguish_the_grant_from_paid_toughness(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests

        source = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        known = set(discover_tests(ROOT).values())

        def verified(rows):
            registry = CapabilityRegistry(rows)
            index = build_index(registry_value=rows, declaration_source=declarations, discovered_test_ids=known)
            registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
            return registry

        names = (BONEYARD, DIRECT, "Grizzly Bears")
        baseline = verified(deepcopy(source))
        for name in names:
            ready, _ = register_card(self.db, baseline, name)
            self.assertTrue(ready["strict_capability_ready"], ready["blockers"])
        for capability, blocked in (
            (GRANT, {BONEYARD}), (ACTIVATION_PAID_STAT_CAPABILITY, {BONEYARD, DIRECT}),
        ):
            with self.subTest(capability=capability):
                rows = deepcopy(source)
                next(row for row in rows["capabilities"] if row["id"] == capability).update(
                    status="blocked", blockers=["Paid granted activation owner withdrawn"])
                registry = verified(rows)
                closure = registry.closure((capability,), profile=PROFILE)
                self.assertIn("status:" + capability + ":blocked", closure.blockers)
                self.assertFalse(any("unverified" in blocker for blocker in closure.blockers))
                for name in names:
                    ready, _ = register_card(self.db, registry, name)
                    self.assertEqual(name not in blocked, ready["strict_capability_ready"],
                        (name, ready["blockers"]))
