from __future__ import annotations

import copy
from types import SimpleNamespace
import unittest

from common import DB_PATH
from quorune.card_programs import (
    bind_card_program_runtime,
    bind_semantic_program_runtime,
    compute_match_trust_closure,
)
from quorune.card_programs.adapters import compile_card_program
from quorune.compiler.activated_ability_catalog import with_activated_ability_catalog
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


class CardProgramFingerprintAuditTests(unittest.TestCase):
    def test_source_fingerprint_audit_reports_all_drift_in_one_snapshot(self):
        from unittest.mock import patch

        from quorune.semantics import SemanticProgram
        from scripts.audit_card_program_fingerprints import audit_snapshot, trusted_program_groups

        alpha = SimpleNamespace(oracle_id="alpha", name="Alpha", oracle_text="Draw a card.")
        beta = SimpleNamespace(oracle_id="beta", name="Beta", oracle_text="Destroy target creature.")
        cards = {record.oracle_id: record for record in (alpha, beta)}

        def program(key, oracle_id, *, provisional=False):
            return SemanticProgram(
                key=key, label=key, oracle_id=oracle_id, ability_id="spell:front",
                trust_level="provisional" if provisional else "trusted",
                provenance={
                    "source_oracle_hash": "0" * 64, "source_rulings_hash": "1" * 64,
                    "authored_by": "fixture", "review_status": "reviewed",
                },
                tests=["audit_fixture"],
            )

        registry = SemanticRegistry(include_builtin_packs=False)
        for candidate in (
            program("alpha:spell", "alpha"), program("alpha:cost", "alpha"),
            program("beta:spell", "beta"), program("missing:spell", "missing"),
            program("draft:spell", "draft", provisional=True),
        ):
            registry.put(candidate)
        groups = trusted_program_groups(registry)
        with patch("scripts.audit_card_program_fingerprints.CardDatabase") as open_database:
            db = open_database.return_value.__enter__.return_value
            db.metadata.return_value = {"snapshot": "fixture"}
            db.by_oracle_id.side_effect = cards.__getitem__
            db.rulings.return_value = []
            report = audit_snapshot("fixture.sqlite3", groups)

        self.assertEqual((3, 4), (report["mismatched_cards"], report["mismatched_programs"]))
        self.assertEqual(["alpha", "beta", "missing"], [row["oracle_id"] for row in report["mismatches"]])
        first, second, missing = report["mismatches"]
        self.assertEqual(["alpha:cost", "alpha:spell"], [row["key"] for row in first["programs"]])
        self.assertEqual(["oracle_hash", "rulings_hash"], second["programs"][0]["fields"])
        self.assertEqual(["missing_card"], missing["programs"][0]["fields"])
        self.assertEqual(3, db.by_oracle_id.call_count)
        self.assertEqual(2, db.rulings.call_count)


    def test_dungeon_fingerprint_accepts_the_reviewed_marker_and_rejects_rules_changes(self):
        import json
        from unittest.mock import patch

        from common import ROOT
        from quorune.card_programs.validation import program_source_is_current
        from scripts.audit_card_program_fingerprints import audit_snapshot

        fixture = json.loads((ROOT / "tests/fixtures/dungeon-cards.json").read_bytes())
        row = next(card for card in fixture["cards"] if card["name"] == "Undercity // The Initiative")
        programs = list(SemanticRegistry().programs_for_oracle(row["oracle_id"]))
        self.assertEqual(9, len(programs))
        original = row["oracle_text"]
        original_reminder = (
            "If you te not, enter Undercity You can take the initiative even if you already"
            + chr(10) + "have it."
        )
        corrected = original.replace(
            original_reminder,
            "If you're not, enter Undercity. You can take the initiative even if you already have it.",
        )
        self.assertNotEqual(original, corrected)
        cases = (
            ("reviewed marker", corrected, 0),
            ("changed room", corrected.replace("Scry 2.", "Scry 1."), 9),
            ("changed Initiative rule", corrected.replace("your upkeep", "your end step"), 9),
        )
        with patch("scripts.audit_card_program_fingerprints.CardDatabase") as open_database:
            db = open_database.return_value.__enter__.return_value
            db.metadata.return_value = {"snapshot": "fixture"}
            db.rulings.return_value = []
            for label, text, expected in cases:
                with self.subTest(label=label):
                    db.by_oracle_id.return_value = SimpleNamespace(
                        oracle_id=row["oracle_id"], name=row["name"], layout=row["layout"], oracle_text=text,
                    )
                    report = audit_snapshot("fixture.sqlite3", {row["oracle_id"]: programs})
                    self.assertEqual(expected, report["mismatched_programs"])
                    self.assertEqual(9 - expected, len(report["approved_equivalences"]))
                    self.assertTrue(all(
                        program_source_is_current(db, program) == (expected == 0) for program in programs
                    ))


class CardProgramTrustTests(unittest.TestCase):
    def test_corrected_bare_subtype_anthem_supersedes_only_matching_reviewed_source(self):
        from dataclasses import replace
        from quorune.oracle_ir import generated_programs
        from quorune.card_programs.reviewed_overlay import shadowed_reviewed_program_keys

        record = self.db.lookup("Stridehangar Automaton")
        generated = tuple(generated_programs(self.db, record, trust_level="trusted",
            capability_registry=self.capabilities, capability_profile="commander_review"))
        reviewed = SemanticRegistry().programs_for_oracle(record.oracle_id)
        old = next(program for program in reviewed if any(
            handler.get("handler_id") == "continuous.anthem.power_toughness.v1"
            for handler in program.handlers))
        self.assertEqual({program.key for program in reviewed}, shadowed_reviewed_program_keys(record, generated, reviewed))
        self.assertEqual(set(), shadowed_reviewed_program_keys(record, generated, (
            replace(old, provenance={**old.provenance, "source_oracle_hash": "0" * 64}),)))
        changed = copy.deepcopy(old.handlers)
        changed[0]["modifier"]["power"] = 2
        self.assertEqual(set(), shadowed_reviewed_program_keys(record, generated, (
            replace(old, handlers=changed),)))
        current = next(program for program in generated if any(
            handler.get("handler_id") == "continuous.anthem.fixed-query.v2"
            for handler in program.handlers))
        changed_controller = copy.deepcopy(current.handlers)
        changed_controller[0]["condition"]["target_controller"] = "any"
        self.assertEqual(set(), shadowed_reviewed_program_keys(record, (
            replace(current, handlers=changed_controller),), reviewed))
        excluded = copy.deepcopy(current.handlers)
        excluded[0]["condition"]["exclude_source"] = True
        self.assertEqual(set(), shadowed_reviewed_program_keys(record, (
            replace(current, handlers=excluded),), reviewed))
        self.assertEqual(set(), shadowed_reviewed_program_keys(record, generated, (
            replace(old, provenance={**old.provenance, "authored_by": current.provenance["authored_by"]}),)))
        provisional = tuple(replace(program, trust_level="provisional") for program in generated)
        self.assertEqual(set(), shadowed_reviewed_program_keys(record, provisional, reviewed))

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

    def test_registration_parses_once_for_default_trust_promotion(self):
        from unittest.mock import patch
        from quorune import oracle_ir

        record = _bolt()
        registry = SemanticRegistry()
        with patch.object(
            oracle_ir, "compile_oracle_card", wraps=oracle_ir.compile_oracle_card,
        ) as compiler:
            report = oracle_ir.register_generated_programs(
                self.db,
                registry,
                (record,),
                capability_registry=self.capabilities,
                capability_profile="commander_review",
                promote_exact_effect_programs=True,
            )
        self.assertEqual(1, report["exact_effect_programs_promoted"])
        self.assertEqual(1, compiler.call_count)

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

    def test_intrinsic_classification_retains_descriptor_admission_blockers(self):
        from dataclasses import replace
        from unittest.mock import patch
        import quorune.card_programs.adapters as adapters
        from quorune.card_programs.adapters import compile_best_available_card_program
        from quorune.card_programs.commands import _CardProgramCoverageCollector
        from quorune.oracle_ir import compile_oracle_card
        collector = _CardProgramCoverageCollector(self.capabilities, 'commander_review')
        for text, kind, expected_capability in (
                ('Target creature loses shadow until end of turn.\nDraw a card.', 'Instant',
                 'continuous.resolution.fixed_keyword_removal'),
                ('You may exert this creature as it attacks. When you do, target creature gets +X/+X until end of turn, where X is the number of counters on permanents you control.',
                 'Creature — Human', 'quantity_expression.public_counter_total')):
            with self.subTest(text=text):
                record = replace(_bolt(), name='Generic Admission Witness', type_line=kind,
                                 oracle_text=text, power='2' if kind.startswith('Creature') else None,
                                 toughness='2' if kind.startswith('Creature') else None)
                ir = compile_oracle_card(record, capability_registry=self.capabilities,
                                         capability_profile='commander_review')
                self.assertEqual('exact', ir.status)
                program = compile_best_available_card_program(self.db, record,
                    semantic_registry=SemanticRegistry(), capability_registry=self.capabilities,
                    capability_profile='commander_review')
                trust = program.trust_closure
                binding = bind_card_program_runtime(program, capability_registry=self.capabilities,
                                                    profile='commander_review')
                self.assertEqual('capability_closed', trust['trust_basis'])
                self.assertTrue(trust['strict_capability_ready'])
                self.assertTrue(binding['strict_capability_ready'])
                self.assertFalse(trust['blockers'], trust)
                self.assertTrue(any(expected_capability in row['declared_capabilities']
                                    for row in binding['ability_bindings']), binding)
                collector.observe(record, program)
                if kind == 'Instant':
                    # The composition is supported. A producer that omits its
                    # keyword-removal authority must still fail runtime admission
                    # even though the independently compiled Oracle IR is exact.
                    generate = adapters._generated_programs_from_ir
                    def omit_keyword_owner(*args, **kwargs):
                        for ability in generate(*args, **kwargs):
                            if expected_capability in ability.capability_dependencies:
                                deps = [cap for cap in ability.capability_dependencies if cap != expected_capability]
                                ability = replace(ability, capability_dependencies=deps,
                                    capability_closure=self.capabilities.closure(deps, profile='commander_review').to_dict())
                            yield ability
                    with patch.object(adapters, '_generated_programs_from_ir', side_effect=omit_keyword_owner):
                        blocked = compile_best_available_card_program(self.db, record,
                            semantic_registry=SemanticRegistry(), capability_registry=self.capabilities,
                            capability_profile='commander_review')
                    self.assertEqual('unresolved', blocked.trust_closure['trust_basis'])
                    self.assertTrue(any('runtime_temporary_keyword_removal:invalid:' in value
                        for value in blocked.trust_closure['blockers']), blocked.trust_closure)
                    self.assertFalse(bind_card_program_runtime(blocked, capability_registry=self.capabilities,
                        profile='commander_review')['strict_capability_ready'])
                    collector.observe(record, blocked)
        self.assertEqual({'unresolved': 1, 'capability_closed': 2}, dict(collector.trust_bases))
        self.assertEqual({'untrusted': 1, 'trusted': 2}, dict(collector.statuses))

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

    def test_runtime_component_description_keeps_defensive_lists(self):
        from unittest.mock import patch
        from quorune.semantic_runtime.components import runtime_component_registries

        registry = runtime_component_registries()[0]
        inventory = registry.inventory()
        expected = inventory[0]
        expected_fingerprint = registry.fingerprint
        handler_id = expected["handler_id"]
        with patch.object(registry, "inventory", side_effect=AssertionError("Full inventory was built")):
            description = registry.describe(handler_id)
            self.assertEqual(expected, description)
            description["rule_references"].append("fixture")
            description["capability_dependencies"].clear()
            self.assertEqual(expected, registry.describe(handler_id))
            self.assertIsNone(registry.describe("fixture.missing.handler"))
        self.assertEqual(inventory, registry.inventory())
        self.assertEqual(expected_fingerprint, registry.fingerprint)


    def test_deck_preflight_registers_and_reloads_complete_basic_land_mana(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory

        from quorune.deck import DeckDefinition, DeckEntry
        from quorune.preflight import semantic_preflight
        from quorune.semantics import SemanticRegistry

        commander = "Mishra, Eminent One"
        deck = DeckDefinition("Basic-land admission witness",
            [DeckEntry(commander, 1, "commander"), DeckEntry("Mountain", 49), DeckEntry("Swamp", 50)],
            [commander])
        with TemporaryDirectory() as directory:
            path = Path(directory) / "registry.json"
            registry = SemanticRegistry(path)
            report = semantic_preflight(self.db, deck, registry=registry)
            rows = {row["name"]: row for row in report["cards"]}
            reopened = SemanticRegistry(path)
            for name in ("Mountain", "Swamp"):
                with self.subTest(name=name):
                    row = rows[name]
                    self.assertTrue(row["card_program_strict_capability_ready"], row["unresolved"])
                    self.assertEqual("capability_closed", row["card_program_trust_basis"])
                    oid = self.db.lookup(name, fuzzy=False).oracle_id
                    program = reopened.card_program_for_oracle(oid)
                    self.assertIsNotNone(program)
                    self.assertEqual(row["card_program_fingerprint"], program.fingerprint)
                    self.assertEqual(["intrinsic_ability"], [ability.event for ability in program.abilities])

    def test_retained_dryad_form_recompiles_identically_after_preflight_and_reload(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory

        from quorune.card_programs.adapters import compile_best_available_card_program
        from quorune.deck import DeckDefinition, DeckEntry
        from quorune.preflight import semantic_preflight

        dryad = self.db.lookup("Dryad Arbor", fuzzy=False)
        deck = DeckDefinition("Dryad Arbor form witness", [DeckEntry(dryad.name)], [])
        arguments = {
            "capability_registry": self.capabilities,
            "capability_profile": "commander_review",
        }
        empty = SemanticRegistry()
        expected = compile_best_available_card_program(self.db, dryad, semantic_registry=empty, **arguments)
        expected_provisional = compile_card_program(self.db, dryad, semantic_registry=empty, **arguments)
        with TemporaryDirectory() as directory:
            path = Path(directory) / "registry.json"
            registry = SemanticRegistry(path)
            first, = semantic_preflight(self.db, deck, registry=registry)["cards"]
            self.assertTrue(first["card_program_strict_capability_ready"], first["unresolved"])
            self.assertEqual("fully_playable", first["status"])
            before = path.read_bytes()

            for candidate in (registry, SemanticRegistry(path)):
                with self.subTest(reopened=candidate is not registry):
                    stored, = candidate.programs_for_oracle(dryad.oracle_id)
                    self.assertEqual("intrinsic_ability", stored.event)
                    self.assertIn("generated_card_form_rule", stored.coverage)
                    repeated = compile_best_available_card_program(
                        self.db, dryad, semantic_registry=candidate, **arguments,
                    )
                    self.assertEqual(expected.to_dict(), repeated.to_dict())
                    provisional = compile_card_program(
                        self.db, dryad, semantic_registry=candidate, **arguments,
                    )
                    self.assertEqual(expected_provisional.to_dict(), provisional.to_dict())
                    again, = semantic_preflight(self.db, deck, registry=candidate)["cards"]
                    self.assertEqual(first["card_program_fingerprint"], again["card_program_fingerprint"])
            self.assertEqual(before, path.read_bytes())

    def test_retained_card_form_conflicts_and_source_drift_still_fail_compilation(self):
        from dataclasses import replace

        from quorune.card_programs import CardProgramError
        from quorune.card_programs.adapters import compile_best_available_card_program

        dryad = self.db.lookup("Dryad Arbor", fuzzy=False)
        arguments = {
            "capability_registry": self.capabilities,
            "capability_profile": "commander_review",
        }
        registry = SemanticRegistry(include_builtin_packs=False)
        current = compile_best_available_card_program(self.db, dryad, semantic_registry=registry, **arguments)
        intrinsic, = current.abilities
        for changed in (
            replace(intrinsic, provenance={**intrinsic.provenance, "source_oracle_hash": "0" * 64}),
            replace(intrinsic, provenance={**intrinsic.provenance, "source_rulings_hash": "0" * 64}),
            replace(intrinsic, effects=[{"op": "draw", "player": "$controller", "count": 1}]),
        ):
            with self.subTest(changed=changed), self.assertRaisesRegex(CardProgramError, "Retained card-form ability.*conflicts"):
                registry.put(changed)
                compile_best_available_card_program(self.db, dryad, semantic_registry=registry, **arguments)

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
            ("modification.cast-cost.untapped-mana-floor.v1", "casting.cost.minimum_mana.untapped_source"),
            {(row["handler_id"], row["family"]) for row in status["runtime_components"]},
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
                "activation.cost.modifier.fixed_generic",
                "zone.draw.replacement.controller_library_choice",
                "ability.activated.catalog",
                "ability.activated.class-level",
                "ability.activated.mana.artifact-hand-reveal",
                "ability.activated.mana.color-set",
                "ability.activated.mana.linked-hand-colors",
                "ability.activated.mana.exert-source",
                "ability.activated.mana.forage-combination",
                "ability.activated.mana.whole-hand-sacrifice",
                "ability.activated.mana.commander-entry-counters",
                "ability.activated.mana.commander-identity",
                "ability.activated.mana.commander-type-scry",
                "ability.activated.mana.fixed-output",
                "ability.activated.mana.land-potential",
                "ability.activated.mana.luck-counter-color",
                "ability.activated.mana.public-quantity",
                "ability.activated.mana.source-chosen-color",
                "ability.activated.mana.land-play-output",
                "ability.activated.fixed-counter-keyword",
                "ability.activated.cycling",
                "ability.activated.crew",
                "ability.activated.encore",
                "ability.activated.ninjutsu",
                "ability.activated.station",
                "ability.activated.self_zone_move",
                "ability.activated.unearth",
                "continuous.ability.battlefield_creature_activation_grant",
                "continuous.ability.graveyard_unearth_grant",
                "continuous.ability.hand_ninjutsu_grant",
                "ability.enchant.linked_graveyard_creature",
                "ability.static.enchant",
                "ability.static.enchant.typed",
                "ability.static.flash",
                "ability.static.conditional_flash",
                "ability.static.self_cast_restriction",
                "ability.static.declare_attackers_cast_restriction",
                "ability.static.all_creature_types_characteristic_definition",
                "ability.static.all_colors_characteristic_definition",
                "continuous.characteristics.off_battlefield_self_creature",
                "continuous.characteristics.all_nonbasic_land_types",
                "ability.static.ascend",
                "ability.static.colorless_characteristic_definition",
                "ability.static.counter_maximum",
                "ability.static.legend_rule_exemption",
                "ability.static.conditional_keyword",
                "ability.static.declaration_cost",
                "ability.static.declaration_requirement",
                "ability.static.attached_untap_restraint",
                "ability.static.crew_power_bonus",
                "ability.static.crew_and_saddle_power_bonus",
                "ability.static.enchanted_player_attack_requirement",
                "ability.static.bound_declaration_rules",
                "ability.static.attached_activation_rule",
                "ability.static.declaration_restriction",
                "ability.static.declaration_restriction_component",
                "ability.static.landwalk_block_permission",
                "ability.static.bounded_block_as_though",
                "ability.static.counter_prohibition",
                "ability.static.defender_permission",
                "ability.static.block_capacity",
                "ability.static.as_unblocked_assignment",
                "ability.static.toughness_assignment",
                "ability.static.attached_goad",
                "deck.construction.copy_limit",
                "ability.static.dynamic_power_toughness",
                "ability.static.component_scope",
                "ability.static.protection",
                "ability.static.player_target_protection",
                "ability.static.controller_turn_player_hexproof",
                "ability.static.venture_limit",
                "ability.static.start_your_engines",
                "ability.static.prototype",
                "ability.static.mutate",
                "ability.static.fuse",
                "ability.static.enlist",
                "ability.static.exert_attack",
                "ability.activated.graveyard_copy_keyword",
                "ability.static.partner_with",
                "ability.static.query_characteristic_modifier",
                "ability.static.query_power_toughness_definition",
                "ability.static.toxic",
                "ability.static.umbra_armor",
                "ability.static.trigger_multiplier",
                "ability.static.trigger_participation",
                "ability.static.trigger_prohibition",
                "ability.trigger.bushido",
                "ability.trigger.rampage",
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
                "ability.trigger.commander_cast_copy",
                "ability.trigger.training",
                "ability.trigger.decayed",
                "ability.trigger.ward",
                "casting.alternative-cost.fixed-public",
                "casting.alternative-cost.fixed-battlefield-grant",
                "casting.additional.paid_entry_copy",
                "casting.bestow.fixed_mana",
                "casting.impending.fixed_mana",
                "casting.cost.minimum_mana.untapped_source",
                "casting.cost.modifier.fixed_query",
                "casting.cost.optional_life_colored_reduction",
                "casting.permission.source_once_per_turn",
                "casting.permission.source_graveyard_exile",
                "casting.permission.source_graveyard_permanent_types",
                "casting.permission.source_tapped_entry",
                "casting.permission.command_owned_graveyard",
                "casting.permission.source_current_mill",
                "casting.permission.source_library_arrival_shared_turn",
                "casting.permission.source_nonbattlefield_arrival",
                "casting.cost.targeted_fixed_generic",
                "casting.cost.modifier.self_public",
                "casting.disguise.fixed_mana",
                "casting.evoke.fixed_mana",
                "casting.evoke.fixed_hand_exile",
                "casting.flashback.fixed_mana",
                "casting.harmonize.fixed_announced_x",
                "casting.kicker.fixed_mana",
                "casting.kicker.fixed_nonmana",
                "casting.multikicker.fixed_mana",
                "casting.additional_cost.replicate_fixed_mana",
                "casting.cost.splice",
                "casting.additional_cost.conspire_fixed_tap",
                "casting.additional_cost.casualty_fixed",
                "casting.additional_cost.bargain",
                "casting.additional_cost.per_target_fixed_mana",
                "casting.alternative_cost.emerge_fixed_mana",
                "casting.additional_cost.offering",
                "casting.lifecycle.disturb_fixed_mana",
                "casting.lifecycle.more_than_meets_the_eye_fixed_mana",
                "casting.lifecycle.source_granted_escape",
                "zone.change.self_graveyard_exile",
                "replacement.zone.self-graveyard-library-shuffle",
                "casting.lifecycle.fixed_public",
                "casting.lifecycle.cipher",
                "casting.lifecycle.paradigm",
                "continuous.ability.cipher_encoding",
                "casting.madness.trigger",
                "casting.megamorph.fixed_mana",
                "casting.morph.fixed_mana",
                "casting.morph.fixed_nonmana",
                "casting.payment.affinity",
                "casting.payment.affinity_artifacts",
                "casting.payment.convoke",
                "casting.payment.assist",
                "casting.payment.delve",
                "casting.payment.improvise",
                "combat.block.self-counter-prohibition",
                "combat.defender_permission.current_attachment",
                "combat.defender_permission.current_numeric",
                "continuous.fixed_query_ability_grant",
                "continuous.fixed_query_characteristic_grant",
                "continuous.fixed_query_keyword_grant",
                "continuous.counter_placement_keyword_grant",
                "continuous.ability.commander_permanent_keywords",
                "continuous.opponent_keyword_prohibition",
                "continuous.attached.fixed_characteristics",
                "continuous.attached.combat_counterpart_first_strike",
                "continuous.attached.planeswalker_combat_loyalty",
                "continuous.attached.qualified_characteristics",
                "continuous.attached.source_chosen_color_protection",
                "continuous.attached.commander_complement_protection",
                "continuous.soulbond.paired_grant",
                "continuous.characteristics.fixed_public_state",
                "continuous.characteristics.conditional_creature_type_removal",
                "continuous.characteristics.graveyard_public_state",
                "continuous.ability.graveyard_query_grant",
                "continuous.ability.combat_counterpart_lifelink",
                "continuous.ability.fixed_composite_grant",
                "continuous.characteristics.fixed_type_replacement",
                "continuous.characteristics.cross_zone_chosen_creature_type",
                "continuous.characteristics.cross_zone_type_addition",
                "continuous.characteristics.self_chosen_creature_type_addition",
                "continuous.characteristics.controlled_chosen_creature_subtype",
                "continuous.ability.fixed_subtype_composite_grant",
                "continuous.ability.graveyard_conditional_grant",
                "continuous.characteristics.fixed_public_setting",
                "continuous.characteristics.fixed_nonowner_creature_modifier",
                "continuous.characteristics.fixed_opponent_base_toughness",
                "continuous.characteristics.enchanted_player_query",
                "continuous.ability.disjoint_negative_keyword_grant",
                "continuous.control.attached_source",
                "continuous.ability.fixed_public_state_grant",
                "continuous.ability.spell_payment_grant",
                "continuous.ability.battlefield_cascade_grant",
                "continuous.ability.battlefield_ripple_grant",
                "continuous.ability.conditional_stack_cascade",
                "continuous.ability.conditional_flash",
                "continuous.ability.public_state_color_grant",
                "continuous.declaration.public_state",
                "continuous.defender_permission.public_state",
                "continuous.as_unblocked_assignment.public_state",
                "continuous.block_capacity.public_state",
                "continuous.characteristics.chosen_designation",
                "continuous.characteristics.conditional_chosen_designation",
                "continuous.characteristics.linked_color_protection",
                "continuous.characteristics.linked_exile_creature",
                "continuous.ability.linked_exile_activations",
                "continuous.characteristics.linked_card_type_protection",
                "continuous.characteristics.linked_player_protection",
                "continuous.characteristics.leveler_bands",
                "continuous.characteristics.station_symbols",
                "continuous.fixed_query_power_toughness_anthem",
                "continuous.quantified_query_power_toughness_anthem",
                "continuous.recipient_quantity_power_toughness_anthem",
                "continuous.recipient_shared_type_power_toughness_anthem",
                "continuous.recipient_shared_name_power_toughness_anthem",
                "continuous.fixed_power_toughness_anthem",
                "continuous.basic_land_type.add_all_lands",
                "continuous.basic_land_type.fixed_addition",
                "continuous.basic_land_type.fixed_setting",
                "action.draw.reveal_first",
                "action.draw.miracle_reveal",
                "casting.miracle.trigger",
                "modification.activation.loyalty-cost",
                "prevention.damage.all",
                "prevention.damage.fixed",
                "prevention.damage.fixed_self_counter",
                "prohibition.combat.goad",
                "participation.untap_step.static",
                "participation.turn_step.skip",
                "untap.phasing.keyword",
                "participation.untap_step.fixed_selection_limit",
                "permission.action.static",
                "permission.action.library-search-control",
                "permission.action.library-search-rule",
                "permission.cast.self-zone",
                "permission.cast.classic_flash_cleanup",
                "permission.cast.mana_surcharge_flash",
                "state_query.devotion.fixed_increase",
                "replacement.counter.quantity",
                "replacement.damage.quantity",
                "replacement.damage.redirection.static",
                "replacement.damage.result.life_floor",
                "replacement.draw.dredge",
                "replacement.zone.kicked_entry",
                "replacement.multikicker.named_entry_counters",
                "replacement.zone.madness_discard",
                "replacement.draw.instruction_quantity",
                "replacement.draw.result_quantity",
                "replacement.draw.result_recipient",
                "replacement.draw.discard_result",
                "replacement.draw.exile_turn_play_result",
                "replacement.draw.win_empty_library_result",
                "replacement.fixed_additional_token",
                "replacement.token_quantity",
                "replacement.token.creature_definition",
                "replacement.token.predefined_artifact_set",
                "replacement.life.gain.addition",
                "replacement.life.gain.to_loss",
                "replacement.life.gain.multiplier",
                "life.gain.prohibition",
                "life.total.prohibition",
                "damage.prevention.prohibition",
                "replacement.zone.destination",
                "replacement.zone.entry_state",
                "replacement.zone.external_entry_counter",
                "replacement.zone.entry_designation",
                "zone.entry.linked_basic_land_type",
                "replacement.zone.entry_card_name",
                "replacement.zone.linked_entry_opponent",
                "replacement.zone.linked_entry_player",
                "replacement.zone.counted_entry_opponent",
                "replacement.zone.entry_color_exclusion",
                "replacement.zone.entry_copy",
                "replacement.zone.linked-graveyard-exile",
                "replacement.zone.entry_reveal_hand",
                "replacement.zone.entry_land_discard",
                "replacement.zone.amplify",
                "replacement.zone.conditional-self-entry-counter",
                "replacement.zone.dynamic-self-entry-counter",
                "replacement.zone.entry-counter-transfer",
                "zone.change.opponent_discard_to_battlefield",
                "replacement.zone.read-ahead-entry-choice",
                "replacement.zone.riot-entry-choice",
                "replacement.zone.tribute-entry",
                "replacement.zone.devour",
                "replacement.zone.prepared_entry",
                "ability.static.target_prohibition",
                "ability.static.target_requirement",
                "replacement.zone.keyword-counter-entry-choice",
                "replacement.zone.self-entry-counter",
                "replacement.zone.sunburst",
                "rule.cast.static",
                "rule.player.static_match_outcome_restriction",
                "rule.stack.split_second",
                "rule.cleanup.no_maximum_hand_size",
                "rule.mana.step_color_retention",
                "rule.cleanup.fixed_hand_size_adjustment",
                "rule.cleanup.fixed_hand_size_setting",
                "restriction.activation.chosen_name_nonmana",
                "restriction.activation.fixed_public",
                "permission.activation.boast_twice",
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

    def test_structural_catalog_binding_validates_metadata_without_excusing_rules(self):
        from dataclasses import replace

        record = replace(
            _bolt(), type_line="Land", oracle_text=(
                "{T}, Pay 1 life, Sacrifice this land: Search your library for "
                "a Plains or Island card, put it onto the battlefield, then shuffle."
            ),
        )
        catalog = with_activated_ability_catalog(
            record, (), carrier_provenance={
                "source_oracle_hash": "a" * 64,
                "source_rulings_hash": "b" * 64,
                "authored_by": "fixture-compiler",
                "review_status": "generated_review_required",
            },
        )[0]
        binding = bind_semantic_program_runtime(
            catalog, capability_registry=self.capabilities,
            profile="commander_review",
        )
        self.assertTrue(binding["strict"], binding["blockers"])
        self.assertFalse(binding["compatibility_path"])
        self.assertEqual([], binding["required_registered_capabilities"])

        # Use untrusted input objects so the binding boundary itself is tested,
        # rather than relying on SemanticProgram's earlier descriptor check.
        descriptor = copy.deepcopy(dict(catalog.handlers[0]))
        for change in (
            {"schema_version": 999},
            {"event": "resolve"},
            {"ability": {**descriptor["ability"], "unknown": True}},
        ):
            with self.subTest(change=change):
                malformed = SimpleNamespace(**{
                    **vars_for_binding(catalog),
                    "handlers": ({**descriptor, **change},),
                })
                rejected = bind_semantic_program_runtime(
                    malformed, capability_registry=self.capabilities,
                    profile="commander_review",
                )
                self.assertFalse(rejected["strict"])
                self.assertTrue(rejected["blockers"])

        for change in (
            {"ability_id": "ability:ab1"},
            {"effects": ({"op": "draw", "count": 1},)},
        ):
            with self.subTest(change=change):
                behavioral = SimpleNamespace(**{
                    **vars_for_binding(catalog), **change,
                })
                rejected = bind_semantic_program_runtime(
                    behavioral, capability_registry=self.capabilities,
                    profile="commander_review",
                )
                self.assertFalse(rejected["strict"])
                if "effects" in change:
                    self.assertIn(
                        "capability:undeclared_runtime_dependency:zone.draw.library_to_hand",
                        rejected["blockers"],
                    )


def vars_for_binding(program):
    return {field: getattr(program, field) for field in (
        "ability_id", "key", "effects", "handlers", "capability_dependencies",
        "capability_closure", "trust_level", "provenance", "tests",
        "coverage", "target_schema", "cost_schema", "event_condition", "event", "active_zone",
    )}


if __name__ == "__main__":
    unittest.main()
