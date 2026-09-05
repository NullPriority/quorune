from __future__ import annotations

import copy
import unittest

from common import DB_PATH
from quorune.card_programs import (
    bind_card_program_runtime,
    bind_semantic_program_runtime,
    compute_match_trust_closure,
)
from quorune.card_programs.adapters import compile_card_program
from quorune.card_programs.commands import runtime_component_status
from quorune.carddb import CardDatabase, CardRecord
from quorune.rules.capabilities import (
    load_default_capability_registry,
)
from quorune.semantic_runtime import default_semantic_handler_registry
from quorune.semantics import SemanticRegistry


def _bolt() -> CardRecord:
    return CardRecord(
        oracle_id="00000000-0000-4000-8000-00000000b017",
        name="Lightning Bolt",
        mana_cost="{R}",
        mana_value=1.0,
        type_line="Instant",
        oracle_text="Lightning Bolt deals 3 damage to any target.",
        power=None,
        toughness=None,
        loyalty=None,
        defense=None,
        colors=("R",),
        color_identity=("R",),
        keywords=(),
        produced_mana=(),
        layout="normal",
        released_at="1993-08-05",
        legalities={"commander": "legal"},
        faces=(),
        raw={},
    )


class CardProgramTrustTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = CardDatabase(DB_PATH)
        cls.capabilities = load_default_capability_registry()
        cls.bolt = compile_card_program(
            cls.db,
            _bolt(),
            capability_registry=cls.capabilities,
            capability_profile="commander_duel",
            trust_level="trusted",
        )

    @classmethod
    def tearDownClass(cls):
        cls.db.close()

    def test_generated_program_exposes_capability_closed_intrinsic_basis(self):
        trust = self.bolt.trust_closure
        self.assertEqual("capability_closed", trust["trust_basis"])
        self.assertTrue(trust["strict_capability_ready"])
        self.assertTrue(trust["closure_layers"]["intrinsic"]["trusted"])
        self.assertEqual(
            "unbound", trust["closure_layers"]["match"]["status"]
        )
        self.assertTrue(trust["evidence_fingerprints"])
        binding = bind_card_program_runtime(
            self.bolt,
            capability_registry=self.capabilities,
            profile="commander_duel",
        )
        self.assertTrue(binding["strict_capability_ready"])
        self.assertFalse(binding["blockers"])

    def test_reviewed_pack_is_compatibility_not_capability_closed(self):
        program = SemanticRegistry().card_program_for_oracle(
            "9070c98b-fd01-4eeb-a4ec-fc464946c7c0"
        )
        self.assertIsNotNone(program)
        trust = program.trust_closure
        self.assertEqual("legacy_reviewed", trust["trust_basis"])
        self.assertTrue(trust["trusted"])
        self.assertFalse(trust["strict_capability_ready"])
        self.assertTrue(trust["compatibility_provenance"])
        self.assertTrue(
            all(
                row["removal_condition"]
                for row in trust["compatibility_provenance"]
            )
        )
        binding = bind_card_program_runtime(
            program,
            capability_registry=self.capabilities,
            profile="commander_review",
        )
        self.assertFalse(binding["strict_capability_ready"])
        self.assertTrue(binding["compatible_ready"])
        self.assertTrue(
            any(
                "legacy_runtime_dependencies_unbound" in blocker
                for blocker in binding["blockers"]
            )
        )

    def test_match_closure_is_conservative_and_dynamic_fail_closed(self):
        match = compute_match_trust_closure(
            [self.bolt],
            registry=self.capabilities,
            profile="commander_duel",
        )
        self.assertFalse(match["strict_capability_ready"])
        self.assertTrue(match["compatible_ready"])
        self.assertIn(
            "format_profile:capability_inventory_incomplete:commander_duel",
            match["blockers"],
        )
        self.assertNotIn(
            "damage.result.infect",
            match["match_closure"]["reachable"],
        )
        blocked = compute_match_trust_closure(
            [self.bolt],
            registry=self.capabilities,
            profile="commander_duel",
            dynamic_capabilities=["damage.combat.excess"],
        )
        self.assertFalse(blocked["strict_capability_ready"])
        self.assertTrue(
            any(
                "damage.combat.excess" in blocker
                for blocker in blocked["blockers"]
            )
        )

    def test_global_handler_and_component_inventory_is_capability_bound(self):
        status = runtime_component_status("commander_review")
        self.assertEqual(
            default_semantic_handler_registry().inventory(),
            [
                {
                    key: value
                    for key, value in row.items()
                    if key != "capability_closure"
                }
                for row in status["semantic_handlers"]
            ],
        )
        self.assertIn(
            "generic.fixed-player-counter-placement.v1",
            {
                row["handler_id"]
                for row in status["semantic_handlers"]
            },
        )
        self.assertIn(
            "generic.fixed-counter-placement-set.v1",
            {
                row["handler_id"]
                for row in status["semantic_handlers"]
            },
        )
        runtime_component_ids = [
            row["handler_id"] for row in status["runtime_components"]
        ]
        self.assertEqual(
            len(runtime_component_ids), len(set(runtime_component_ids))
        )
        self.assertIn(
            "replacement.zone.conditional-self-entry-counter.v1",
            runtime_component_ids,
        )
        self.assertIn(
            "replacement.counter.quantity.v2",
            {
                row["handler_id"]
                for row in status["runtime_components"]
            },
        )
        self.assertEqual(
            {
                "ability.activated.catalog",
                "ability.activated.mana.color-set",
                "ability.activated.mana.fixed-output",
                "ability.activated.fixed-counter-keyword",
                "ability.activated.cycling",
                "ability.activated.crew",
                "ability.activated.station",
                "ability.activated.self_zone_move",
                "ability.activated.unearth",
                "ability.enchant.linked_graveyard_creature",
                "ability.static.enchant",
                "ability.static.enchant.typed",
                "ability.static.flash",
                "ability.static.all_creature_types_characteristic_definition",
                "ability.static.colorless_characteristic_definition",
                "ability.static.counter_maximum",
                "ability.static.conditional_keyword",
                "ability.static.declaration_cost",
                "ability.static.declaration_requirement",
                "ability.static.declaration_restriction",
                "ability.static.dynamic_power_toughness",
                "ability.static.component_scope",
                "ability.static.protection",
                "ability.static.partner_with",
                "ability.static.query_characteristic_modifier",
                "ability.static.query_power_toughness_definition",
                "ability.static.toxic",
                "ability.static.trigger_multiplier",
                "ability.trigger.bushido",
                "ability.trigger.battle_cry",
                "ability.trigger.cascade",
                "ability.trigger.dethrone",
                "ability.trigger.exalted",
                "ability.trigger.flanking",
                "ability.trigger.melee",
                "ability.trigger.mentor",
                "ability.trigger.prowess",
                "ability.trigger.renown",
                "ability.trigger.storm",
                "ability.trigger.training",
                "ability.trigger.ward",
                "casting.bestow.fixed_mana",
                "casting.cost.modifier.fixed_query",
                "casting.cost.modifier.self_public",
                "casting.disguise.fixed_mana",
                "casting.evoke.fixed_mana",
                "casting.flashback.fixed_mana",
                "casting.kicker.fixed_mana",
                "casting.lifecycle.fixed_public",
                "casting.madness.trigger",
                "casting.megamorph.fixed_mana",
                "casting.morph.fixed_mana",
                "casting.payment.affinity",
                "casting.payment.affinity_artifacts",
                "casting.payment.convoke",
                "casting.payment.delve",
                "casting.payment.improvise",
                "combat.block.self-counter-prohibition",
                "continuous.fixed_query_ability_grant",
                "continuous.fixed_query_characteristic_grant",
                "continuous.fixed_query_keyword_grant",
                "continuous.attached.fixed_characteristics",
                "continuous.characteristics.fixed_public_state",
                "continuous.characteristics.leveler_bands",
                "continuous.fixed_query_power_toughness_anthem",
                "continuous.fixed_power_toughness_anthem",
                "continuous.basic_land_type.add_all_lands",
                "action.draw.reveal_first",
                "modification.activation.loyalty-cost",
                "prevention.damage.all",
                "prevention.damage.fixed",
                "prohibition.combat.goad",
                "participation.untap_step.static",
                "permission.action.static",
                "permission.cast.self-zone",
                "replacement.counter.quantity",
                "replacement.damage.quantity",
                "replacement.damage.redirection.static",
                "replacement.damage.result.life_floor",
                "replacement.draw.dredge",
                "replacement.zone.kicked_entry",
                "replacement.zone.madness_discard",
                "replacement.draw.instruction_quantity",
                "replacement.draw.result_quantity",
                "replacement.fixed_additional_token",
                "replacement.life.gain.multiplier",
                "replacement.zone.destination",
                "replacement.zone.entry_state",
                "replacement.zone.conditional-self-entry-counter",
                "replacement.zone.read-ahead-entry-choice",
                "replacement.zone.riot-entry-choice",
                "replacement.zone.self-entry-counter",
                "replacement.zone.sunburst",
                "restriction.activation.chosen_name_nonmana",
                "restriction.draw.maximum_per_turn",
            },
            {row["family"] for row in status["runtime_components"]},
        )
        self.assertFalse(status["strict_capability_ready"])
        self.assertTrue(
            all(
                row["capability_closure"]["registry_fingerprint"]
                == status["capability_registry_fingerprint"]
                for row in [
                    *status["semantic_handlers"],
                    *status["runtime_components"],
                ]
            )
        )

    def test_runtime_binding_fails_closed_on_stale_closure(self):
        ability = copy.deepcopy(self.bolt.abilities[0])
        ability.capability_closure["fingerprint"] = "0" * 64
        binding = bind_semantic_program_runtime(
            ability,
            capability_registry=self.capabilities,
            profile="commander_duel",
        )
        self.assertIn(
            "capability:closure_binding_mismatch",
            binding["blockers"],
        )
        self.assertFalse(binding["strict"])
        self.assertEqual(
            self.capabilities.evidence_fingerprint,
            binding["capability_evidence_fingerprint"],
        )

    def test_registered_handler_dependency_cannot_hide_in_legacy_program(self):
        program = next(
            value
            for value in SemanticRegistry().programs()
            if any(effect.get("op") == "draw" for effect in value.effects)
        )
        binding = bind_semantic_program_runtime(
            program,
            capability_registry=self.capabilities,
            profile="commander_review",
        )
        self.assertIn(
            "capability:undeclared_runtime_dependency:zone.draw.library_to_hand",
            binding["blockers"],
        )
        self.assertIn(
            "capability:legacy_runtime_dependencies_unbound",
            binding["blockers"],
        )


if __name__ == "__main__":
    unittest.main()
