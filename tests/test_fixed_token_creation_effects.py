from __future__ import annotations

from collections import Counter
import copy
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT, keep_all, load_assets, make_session, pass_current
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardRecord
from quorune.compiled_activated_abilities import compiled_activated_abilities
from quorune.damage import damage_proposal, resolve_damage_batch
from quorune.compiler.token_templates import (
    FIXED_TOKEN_DEFINITION_BATCH_MECHANIC,
    fixed_token_creation_effect_template,
)
from quorune.errors import GameRuleError
from quorune.model import StackItem
from quorune.oracle_ir import (
    compile_oracle_card,
    generated_programs,
    register_generated_programs,
)
from quorune.projection import StateProjector
from quorune.record import (
    authoritative_state_hash,
    checkpoint_envelope,
    replay_record,
)
from quorune.rules.capabilities import (
    CapabilityRegistry,
    capability_dependencies_for_node,
    load_default_capability_registry,
)
from quorune.semantics import SemanticRegistry
from quorune.trigger_processing import begin_pending_trigger_batch


CAPABILITY_REGISTRY_PATH = (
    Path(__file__).resolve().parents[1]
    / "quorune"
    / "rules"
    / "capability-registry.json"
)


def token_record(
    name: str,
    oracle_text: str,
    suffix: int,
    *,
    type_line: str = "Instant",
) -> CardRecord:
    return CardRecord(
        oracle_id=f"00000000-0000-4000-8000-{suffix:012d}",
        name=name,
        mana_cost="{2}",
        mana_value=2.0,
        type_line=type_line,
        oracle_text=oracle_text,
        power="2" if "Creature" in type_line else None,
        toughness="2" if "Creature" in type_line else None,
        loyalty=None,
        defense=None,
        colors=(),
        color_identity=(),
        keywords=(),
        produced_mana=(),
        layout="normal",
        released_at="2026-01-01",
        legalities={"commander": "legal"},
        faces=(),
        raw={},
    )


class PredefinedBloodTokenCompilerTests(unittest.TestCase):
    def test_blood_typed_profile_preserves_all_costs_and_availability(self):
        from types import SimpleNamespace
        from quorune.standard_token_abilities import standard_token_characteristics
        from quorune.abilities import ActivatedAbility
        from quorune.rules.activation.availability import activation_availability

        payload = standard_token_characteristics({
            'activated_ability_profile': 'one_tap_discard_sac_draw_card_v1'})
        ability = ActivatedAbility.from_dict(payload['activated_abilities'][0])
        self.assertEqual(1, ability.mana['GENERIC'])
        self.assertEqual(1, sum(ability.mana.values()))
        self.assertTrue(ability.tap_source)
        self.assertTrue(ability.sacrifice_source)
        self.assertFalse(ability.mana_ability)
        self.assertFalse(ability.sorcery_speed)
        self.assertEqual('builtin:draw:1', ability.builtin_semantic_key)
        self.assertEqual(1, len(ability.choices))
        choice = ability.choices[0]
        self.assertEqual('discard_one', choice.kind)
        self.assertEqual(1, choice.count)
        self.assertEqual('hand', choice.zone)
        self.assertEqual('$actor', choice.predicate['owner'])
        source = SimpleNamespace(zone='battlefield', tapped=False, object_id='typed-blood-source',
            controller='A', entered_turn=0)
        host = SimpleNamespace(state=SimpleNamespace(players={'A': SimpleNamespace()},
            stack=[], active_player='A', turn_number=1),
            _effective_card_data=lambda card: {'type_line': 'Token Artifact — Blood'},
            _type_parts=lambda text: ({'artifact'}, {'blood'}, set()),
            _ability_choice_payable=lambda *args: True,
            _may_activate_creature_as_haste=lambda *args: False,
            _crew_threshold=lambda *args: None, _legendary_creatures_controlled=lambda seat: 0,
            _semantic_event_sources=lambda **kwargs: (),
            _cost_is_affordable=lambda *args, **kwargs: True)
        with patch('quorune.semantic_runtime.static_cast_rules.split_second_is_active', return_value=False), \
                patch('quorune.rules.activation.availability.current_activation_prohibitions', return_value=()), \
                patch('quorune.rules.activation.availability.activation_condition_status', return_value=('payable', None)):
            self.assertEqual(('payable', None), activation_availability(host, 'A', source, ability))
            host._ability_choice_payable = lambda *args: False
            self.assertEqual(('unpayable', 'mandatory_cost_object_unavailable'),
                activation_availability(host, 'A', source, ability))
            host._ability_choice_payable = lambda *args: True
            source.tapped = True
            self.assertEqual(('unavailable', 'source_tapped'), activation_availability(host, 'A', source, ability))
            source.tapped = False
            host._cost_is_affordable = lambda *args, **kwargs: False
            self.assertEqual(('unpayable', 'insufficient_mana'),
                activation_availability(host, 'A', source, ability))

    def test_fixed_blood_definition_requires_complete_activation_profile(self):
        registry = load_default_capability_registry()
        for text, kind in (
            ('Create a Blood token.', 'spell_ability'),
            ('When this creature enters, create two Blood tokens.', 'triggered_ability'),
        ):
            with self.subTest(text=text):
                ir = compile_oracle_card(token_record('Predefined Token Compiler Fixture', text, 3640501,
                    type_line='Creature — Human' if kind == 'triggered_ability' else 'Sorcery'),
                    capability_registry=registry, capability_profile='commander_review')
                self.assertEqual('exact', ir.status, ir.material_residuals)
                node = ir.faces[0].nodes[0]
                self.assertEqual(kind, node.kind)
                self.assertEqual('Token Artifact — Blood', node.effects[0]['characteristics']['type_line'])
                self.assertEqual('one_tap_discard_sac_draw_card_v1',
                    node.effects[0]['characteristics']['activated_ability_profile'])
                self.assertIn('token.ability.fixed_blood', node.capability_dependencies)

    def test_blood_profile_definition_and_dependency_mutants_fail_closed(self):
        registry = load_default_capability_registry()
        record = token_record('Predefined Token Compiler Fixture', 'Create a Blood token.', 3640502)
        from quorune.compiler import token_templates
        with patch.dict(token_templates._PREDEFINED_CREATION_DEFINITIONS, {'blood token': {
            'name': 'Blood', 'type_line': 'Token Artifact — Blood',
            'display_text': 'Draw a card.', 'activated_ability_profile': 'two_sac_draw_card_v1',
        }}):
            changed = token_templates.fixed_token_creation_effect_template(record.oracle_text)
        from quorune.rules.token_creation_capability_shapes import fixed_token_creation_node_capabilities
        self.assertFalse(fixed_token_creation_node_capabilities(
            effects=(changed.effect,), target_schema=None, mechanic_ids=changed.mechanics))
        with patch.object(token_templates, '_FIXED_PREDEFINED_TOKEN') as parser:
            parser.fullmatch.return_value = None
            self.assertNotEqual('exact', compile_oracle_card(record, capability_registry=registry,
                capability_profile='commander_review').status)
        raw = json.loads(CAPABILITY_REGISTRY_PATH.read_text(encoding='utf-8'))
        cap = next(row for row in raw['capabilities'] if row['id'] == 'token.ability.fixed_blood')
        cap.update(status='blocked', blockers=['withdrawn Blood activation producer'])
        withdrawn = CapabilityRegistry(raw)
        withdrawn.mark_evidence_verified('0' * 64)
        self.assertNotEqual('exact', compile_oracle_card(record, capability_registry=withdrawn,
            capability_profile='commander_review').status)


class AdditionalPredefinedTokenCompilerTests(unittest.TestCase):
    def test_mutagen_trigger_beside_unsupported_squad_replacement_fails_closed_at_admission(self):
        from quorune.card_programs import bind_card_program_runtime
        from quorune.card_programs.adapters import compile_best_available_card_program
        from quorune.compiler.unlock_frontier import analyze_card_unlocks

        record = token_record('Generic Mutagen Replacement Boundary',
            'Squad {3} (As an additional cost to cast this spell, you may pay {3} any number of times. '
            'When this creature enters, create that many tokens that are copies of it.)\n'
            'Deathtouch\nWhenever this creature deals combat damage to a player, create a Mutagen token.',
            365209001, type_line='Artifact Creature — Robot')
        registry = load_default_capability_registry()
        ir = compile_oracle_card(record, capability_registry=registry, capability_profile='commander_review')
        self.assertTrue(any(node.exact and 'token.ability.fixed_mutagen' in node.capability_dependencies
                            for face in ir.faces for node in face.nodes))
        # This boundary needs only printed rules; an empty fixture database
        # supplies the ordinary rulings interface without an incidental card.
        from quorune.carddb import CardDatabase
        from scripts.build_test_database import build_fixture_database
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'mutagen-admission.sqlite3'
            fixture = Path(directory) / 'empty.json'
            fixture.write_text('{"schema_version":1,"cards":[],"rulings":[]}', encoding='utf-8')
            build_fixture_database([fixture], path)
            with CardDatabase(path) as database:
                program = compile_best_available_card_program(database, record,
                    semantic_registry=SemanticRegistry(), capability_registry=registry,
                    capability_profile='commander_review')
        frontier = analyze_card_unlocks(ir, program=program, program_error=None,
            capabilities=registry, profile='commander_review')
        self.assertEqual('residual', frontier['card_program_status'])
        # Squad is a repeating additional cost plus an entry copy trigger, not
        # a replacement effect. Its actual printed keyword remains material.
        self.assertTrue(any(residual.kind == 'static_ability'
                            and residual.text.startswith('Squad ')
                            for residual in ir.material_residuals))
        binding = bind_card_program_runtime(program, capability_registry=registry, profile='commander_review')
        self.assertFalse(binding['strict_capability_ready'])
        self.assertFalse(binding['compatible_ready'])
        self.assertIn('trust_basis:unresolved', binding['blockers'])

    def test_lander_and_mutagen_profiles_results_and_dependency_mutants_fail_closed(self):
        from quorune.standard_token_abilities import standard_token_characteristics
        from quorune.abilities import ActivatedAbility
        from quorune.rules.activation.resolution import builtin_activation_resolution
        from quorune.rules.token_creation_capability_shapes import fixed_token_creation_node_capabilities
        for name, profile, mana, result in (
            ('Lander', 'two_tap_sac_basic_land_search_v1', 2, 'search'),
            ('Mutagen', 'one_tap_sac_target_counter_v1', 1, 'place_counters'),
        ):
            with self.subTest(name=name):
                ability = ActivatedAbility.from_dict(standard_token_characteristics({
                    'activated_ability_profile': profile})['activated_abilities'][0])
                self.assertEqual(mana, sum(ability.mana.values()))
                self.assertTrue(ability.tap_source)
                self.assertTrue(ability.sacrifice_source)
                self.assertEqual(name == 'Mutagen', ability.sorcery_speed)
                resolution = builtin_activation_resolution(ability.builtin_semantic_key, 'B')
                self.assertEqual(result, resolution.effects[0]['op'])
                if name == 'Lander':
                    self.assertEqual(('basic',), resolution.effects[0]['selector']['supertypes'])
                    self.assertTrue(resolution.effects[0]['shuffle_after'])
                    self.assertTrue(resolution.effects[0]['enters_tapped_override'])
                else:
                    self.assertEqual('$target.0', resolution.effects[0]['card'])
                    self.assertEqual('+1/+1', resolution.effects[0]['counter'])
                    self.assertEqual(1, resolution.effects[0]['amount'])
                    self.assertEqual(('creature',), ability.target_schema['types_any'])
                template = fixed_token_creation_effect_template(f'Create a {name} token.')
                mutant = copy.deepcopy(template.effect)
                mutant['characteristics']['activated_ability_profile'] = 'two_sac_draw_card_v1'
                self.assertFalse(fixed_token_creation_node_capabilities(effects=(mutant,), target_schema=None,
                    mechanic_ids=template.mechanics))
                raw = json.loads(CAPABILITY_REGISTRY_PATH.read_text(encoding='utf-8'))
                cap = next(row for row in raw['capabilities'] if row['id'] == 'token.ability.fixed_'+name.lower())
                cap.update(status='blocked', blockers=['withdrawn predefined activation profile'])
                withdrawn = CapabilityRegistry(raw)
                withdrawn.mark_evidence_verified('0' * 64)
                card = token_record('Predefined Token Compiler Fixture', f'Create a {name} token.', 3640602)
                self.assertNotEqual('exact', compile_oracle_card(card, capability_registry=withdrawn,
                    capability_profile='commander_review').status)
        for key in ('builtin:fixed-basic-land-search:2:tapped', 'builtin:fixed-target-counter:+1/+1:2'):
            self.assertIsNone(builtin_activation_resolution(key, 'A'))

    def test_lander_and_mutagen_definitions_require_complete_specific_profiles(self):
        registry = load_default_capability_registry()
        for name, profile in (
            ('Lander', 'two_tap_sac_basic_land_search_v1'),
            ('Mutagen', 'one_tap_sac_target_counter_v1'),
        ):
            with self.subTest(name=name):
                card = token_record('Predefined Token Compiler Fixture', f'Create a {name} token.', 3640601)
                ir = compile_oracle_card(card, capability_registry=registry, capability_profile='commander_review')
                self.assertEqual('exact', ir.status, ir.material_residuals)
                effect = ir.faces[0].nodes[0].effects[0]
                self.assertEqual('Token Artifact — '+name, effect['characteristics']['type_line'])
                self.assertEqual(profile, effect['characteristics']['activated_ability_profile'])
                self.assertIn('token.ability.fixed_'+name.lower(), ir.faces[0].nodes[0].capability_dependencies)


class FixedTokenCreationCompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db, cls.mishra, cls.zimone = load_assets()
        cls.capabilities = load_default_capability_registry()

    @classmethod
    def tearDownClass(cls):
        cls.db.close()


    def test_restricted_token_mana_binds_creator_and_quoted_activation(self):
        from quorune.card_programs import bind_card_program_runtime
        from quorune.card_programs.adapters import compile_card_program
        from quorune.granted_activation_model import RESTRICTED_MANA_GRANT_CAPABILITY

        inner = "{T}: Add {R}. Spend this mana only to cast a planeswalker spell."
        record = token_record(
            "Harbor Signal Fixture",
            'Create a 1/1 red Wizard creature token with "' + inner + '".',
            470099, type_line="Sorcery",
        )
        profile = "commander_review"
        ir = compile_oracle_card(record, capability_registry=self.capabilities, capability_profile=profile)
        self.assertEqual(ir.status, "exact")
        creator, activation = ir.faces[0].nodes
        self.assertEqual(activation.kind, "granted_mana_ability")
        self.assertIn(RESTRICTED_MANA_GRANT_CAPABILITY, creator.capability_dependencies)
        self.assertIn(RESTRICTED_MANA_GRANT_CAPABILITY, activation.capability_dependencies)
        binding = bind_card_program_runtime(
            compile_card_program(
                self.db, record, capability_registry=self.capabilities,
                capability_profile=profile, trust_level="trusted",
            ),
            capability_registry=self.capabilities, profile=profile,
        )
        self.assertTrue(binding["strict_capability_ready"], binding["blockers"])

        with patch(
            "quorune.compiler.granted_static_costs.bind_granted_plan_dependencies",
            side_effect=lambda node, plan, registry, profile: node,
        ):
            mutated = bind_card_program_runtime(
                compile_card_program(
                    self.db, record, capability_registry=self.capabilities,
                    capability_profile=profile, trust_level="trusted",
                ),
                capability_registry=self.capabilities, profile=profile,
            )
        self.assertFalse(mutated["strict_capability_ready"])
        self.assertTrue(any(RESTRICTED_MANA_GRANT_CAPABILITY in blocker for blocker in mutated["blockers"]))

    def test_adventure_token_shape_keeps_independent_intrinsic_authority(self):
        from quorune.adventure import ADVENTURE_CAPABILITY, ADVENTURE_COVERAGE
        from quorune.card_programs.binding import bind_semantic_program_runtime
        from quorune.rules.token_creation_capability_shapes import fixed_token_creation_node_capabilities
        from quorune.semantics import SemanticProgram

        template = fixed_token_creation_effect_template("Create three 2/2 green Bear creature tokens.")
        self.assertIsNotNone(template)
        effect = dict(template.effect)
        mechanics = {*template.mechanics, ADVENTURE_COVERAGE}
        required = fixed_token_creation_node_capabilities(
            effects=(effect,), target_schema=None, mechanic_ids=mechanics,
        )
        self.assertIn("token.creation.fixed_definition", required)
        for changed in ({**effect, "quantity": True}, {**effect, "unrepresented_result": True}):
            self.assertFalse(fixed_token_creation_node_capabilities(
                effects=(changed,), target_schema=None, mechanic_ids=mechanics,
            ))
        self.assertFalse(fixed_token_creation_node_capabilities(
            effects=(effect,), target_schema=None,
            mechanic_ids=mechanics | {"intrinsic_unowned_resolution_required"},
        ))
        dependencies = tuple(sorted({*required, ADVENTURE_CAPABILITY}))
        program = SemanticProgram(
            key="token-adventure:spell", oracle_id="00000000-0000-4000-8000-000000009611",
            ability_id="spell:token-adventure", label="Token Adventure", event="resolve", active_zone="stack",
            effects=[effect], coverage=sorted(mechanics), trust_level="trusted", requires_arbiter=False,
            tests=["test_adventure_token_shape_keeps_independent_intrinsic_authority"],
            provenance={"authored_by": "token-shape-witness", "review_status": "reviewed",
                        "source_oracle_hash": "0" * 64, "source_rulings_hash": "1" * 64},
            capability_dependencies=dependencies,
            capability_closure=self.capabilities.closure(dependencies, profile="commander_review").to_dict(),
        )
        self.assertTrue(bind_semantic_program_runtime(
            program, capability_registry=self.capabilities, profile="commander_review",
        )["strict"])
        for withdrawn in (ADVENTURE_CAPABILITY, "token.creation.fixed_definition"):
            reduced = tuple(capability for capability in dependencies if capability != withdrawn)
            missing = replace(
                program, capability_dependencies=reduced,
                capability_closure=self.capabilities.closure(reduced, profile="commander_review").to_dict(),
            )
            binding = bind_semantic_program_runtime(missing, capability_registry=self.capabilities, profile="commander_review")
            self.assertFalse(binding["strict"])
            self.assertTrue(any(withdrawn in blocker for blocker in binding["blockers"]), binding["blockers"])

    def test_fixed_firebending_token_keyword_keeps_one_independent_child_program(self):
        from quorune.ability_fragments import (
            CURRENT_ABILITY_FRAGMENT_COVERAGE, GrantedTriggeredAbilitySpec,
            canonical_ability_fragments,
        )
        for index, (text, kind, amount) in enumerate((
            ("Create two 2/2 red Soldier creature tokens with firebending 1.", "Sorcery", 1),
            ("{8}: Create a 4/4 red Dragon creature token with flying and firebending 4.", "Creature", 4),
            ("When this enchantment enters, create a 2/2 red Soldier creature token with firebending 20.", "Enchantment", 20),
            ('Create a 2/2 red Soldier creature token with "Firebending 1".', "Sorcery", 1),
        )):
            with self.subTest(text=text):
                ir = compile_oracle_card(token_record("Intrinsic Token Keyword", text, 979100 + index,
                    type_line=kind), capability_registry=self.capabilities, capability_profile="commander_review")
                self.assertEqual("exact", ir.status, ir.material_residuals)
                outer, child = ir.faces[0].nodes
                effect = next(effect for effect in outer.effects if effect["op"] == "create_token")
                fragments = canonical_ability_fragments(effect["characteristics"]["ability_fragments"])
                self.assertEqual(1, len(fragments))
                self.assertIsInstance(fragments[0], GrantedTriggeredAbilitySpec)
                self.assertIn(":token:", fragments[0].ability_id)
                self.assertEqual("granted_triggered_ability", child.kind)
                self.assertEqual("creature.attacks", child.event)
                self.assertEqual("fixed-keyword-firebending-event-effect-v1", child.template_id)
                self.assertEqual(("mana", "R", amount, "end_of_combat"),
                    tuple(child.effects[0][key] for key in ("op", "color", "amount", "retain_until")))
                self.assertNotIn(CURRENT_ABILITY_FRAGMENT_COVERAGE, child.runtime_coverage)
                self.assertIn("trigger.keyword.firebending.fixed", child.capability_dependencies)
                self.assertIn("token.creation.typed_ability_definition", outer.capability_dependencies)
                if "flying" in text:
                    self.assertEqual(["Flying"], effect["characteristics"]["keywords"])

    def test_token_keyword_composition_rejects_unclosed_sources_counts_and_other_keywords(self):
        for text in (
            "Create a 2/2 red Soldier creature token with firebending 0.",
            "Create a 2/2 red Soldier creature token with firebending 21.",
            "Create a 2/2 red Soldier creature token with firebending X.",
            "Create a 2/2 red Soldier creature token with firebending 1 and firebending 2.",
            "Create a 2/2 red Soldier creature token with afflict 2.",
            "Create a 2/2 red Soldier creature token with a mysterious ability and firebending 1.",
            "Create a 2/2 red Soldier creature token with firebending 1. Take a mysterious action.",
        ):
            with self.subTest(text=text):
                ir = compile_oracle_card(token_record("Open Token Keyword", text, 979200),
                    capability_registry=self.capabilities, capability_profile="commander_review")
                self.assertNotEqual("exact", ir.status)
                self.assertTrue(ir.material_residuals)
        text = "Create a 2/2 red Soldier creature token with firebending 1."
        for owner in (
            "quorune.compiler.typed_token_ability_nodes._fixed_token_keyword_shell",
            "quorune.compiler.granted_keyword_event_nodes._keyword_inner_node",
        ):
            with self.subTest(omitted_owner=owner), patch(owner, return_value=None):
                ir = compile_oracle_card(token_record("Missing Token Keyword Owner", text, 979201),
                    capability_registry=self.capabilities, capability_profile="commander_review")
                self.assertNotEqual("exact", ir.status)

    def test_query_defined_token_stats_require_complete_intrinsic_definitions(self):
        from quorune.ability_fragments import canonical_ability_fragments
        from quorune.characteristic_fragments import QueryPowerToughnessDefinitionSpec
        from quorune.rules.token_creation_capability_shapes import token_definition_payload_dependencies

        text = (
            'Create a green Ooze creature token with "This token\'s power is equal to '
            'the number of card types among cards in your graveyard and its toughness '
            'is equal to that number plus 1."'
        )
        card = token_record("Defined Ooze", text, 747201)
        ir = compile_oracle_card(card, capability_registry=self.capabilities, capability_profile="commander_review")
        self.assertEqual("exact", ir.status, ir.material_residuals)
        effect, = [effect for face in ir.faces for node in face.nodes for effect in node.effects
                   if effect["op"] == "create_token"]
        characteristics = effect["characteristics"]
        self.assertEqual(("*", "*"), (characteristics["power"], characteristics["toughness"]))
        definition, = canonical_ability_fragments(characteristics["ability_fragments"])
        self.assertIsInstance(definition, QueryPowerToughnessDefinitionSpec)
        self.assertTrue(definition.define_power and definition.define_toughness)
        self.assertEqual(1, definition.toughness_adjustment)
        self.assertIn("continuous.characteristics.query_power_toughness_definition",
                      token_definition_payload_dependencies(effect))
        for invalid_fragments in ([], [*characteristics["ability_fragments"]] * 2):
            with self.subTest(invalid_fragments=invalid_fragments), self.assertRaises(ValueError):
                token_definition_payload_dependencies({**effect, "characteristics": {
                    **characteristics, "ability_fragments": invalid_fragments,
                }})
        with patch("quorune.compiler.typed_token_ability_nodes._CREATURE_TOKEN_WITHOUT_STATS") as omitted:
            omitted.subn.side_effect = lambda _replacement, shell, **_options: (shell, 0)
            mutant = compile_oracle_card(card, capability_registry=self.capabilities, capability_profile="commander_review")
        self.assertNotEqual("exact", mutant.status)
        for unsupported in (
            'Create a green Ooze creature token with "{T}: Add {G}."',
            'Create a green Ooze creature token with "This token\'s power is equal to the number of creatures you control."',
        ):
            with self.subTest(unsupported=unsupported):
                ir = compile_oracle_card(token_record("Open Ooze", unsupported, 747202),
                    capability_registry=self.capabilities, capability_profile="commander_review")
                self.assertNotEqual("exact", ir.status)

    def test_distributed_token_definition_requires_exact_creator_scope(self):
        from quorune.fixed_token_production import FIXED_DISTRIBUTED_TOKEN_CAPABILITY_ID
        from quorune.rules.token_creation_capability_shapes import distributed_token_creation_payload_dependencies

        forms = (
            ("Each player creates a Food token.", "each_player", False),
            ("Each opponent creates a 1/1 white Human creature token.", "each_opponent", False),
            ("You and target opponent each create a tapped Powerstone token.",
             "controller_and_target_opponent", True),
        )
        for index, (text, scope, targeted) in enumerate(forms):
            with self.subTest(text=text):
                card = token_record("Distributed Token Fixture", text, 748200 + index)
                ir = compile_oracle_card(card, capability_registry=self.capabilities, capability_profile="commander_review")
                self.assertEqual("exact", ir.status, ir.material_residuals)
                node, = ir.faces[0].nodes
                effect, = node.effects
                self.assertEqual(("create_distributed_tokens", scope), (effect["op"], effect["scope"]))
                self.assertEqual("$controller", effect["token"]["controller"])
                self.assertEqual(targeted, node.target_schema is not None)
                if targeted:
                    self.assertEqual("opponent", node.target_schema["player_relation"])
                    self.assertEqual("$target.0", effect["target"])
                    self.assertIs(effect["token"]["tapped"], True)
                self.assertIn(FIXED_DISTRIBUTED_TOKEN_CAPABILITY_ID, node.capability_dependencies)
                for bad in ({**effect, "scope": "arbitrary_seats"}, {**effect, "creator_ids": ["A"]}):
                    with self.assertRaises(ValueError):
                        distributed_token_creation_payload_dependencies(bad)

        for text in (
            "Each player creates X Food tokens.",
            "You and an opponent of your choice each create a Food token.",
            "Each player creates a token that's a copy of target creature.",
        ):
            card = token_record("Open Distributed Token Fixture", text, 748209)
            ir = compile_oracle_card(card, capability_registry=self.capabilities, capability_profile="commander_review")
            self.assertNotEqual("exact", ir.status)
        from quorune.rules.capabilities import CapabilityRegistry

        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        dependency = next(row for row in raw["capabilities"] if row["id"] == FIXED_DISTRIBUTED_TOKEN_CAPABILITY_ID)
        dependency.update(status="blocked", blockers=["focused distributed token withdrawal"])
        withdrawn = CapabilityRegistry(raw)
        withdrawn.mark_evidence_verified("0" * 64)
        card = token_record("Withdrawn Token Fixture", forms[0][0], 748211)
        ir = compile_oracle_card(card, capability_registry=withdrawn, capability_profile="commander_review")
        self.assertNotEqual("exact", ir.status)
        with patch("quorune.compiler.fixed_token_production_templates._fixed_distributed_creator_template", return_value=None):
            ir = compile_oracle_card(token_record("Mutated Token Fixture", forms[0][0], 748210),
                                     capability_registry=self.capabilities, capability_profile="commander_review")
            self.assertNotEqual("exact", ir.status)

    def test_fixed_token_creation_compiles_shared_spell_trigger_and_activation_grammar(
        self,
    ):
        fixtures = (
            (
                token_record(
                    "Fixed Spirit Spell",
                    "Create two 1/1 white Spirit creature tokens with flying.",
                    470001,
                ),
                "spell_ability",
                "create-fixed-creature-token-v2",
                {"combat.block.flying", "token.creation.fixed_definition"},
                "Token Creature — Spirit",
            ),
            (
                token_record(
                    "Fixed Spirit Trigger",
                    "When this creature enters, create a tapped 3/2 red and "
                    "white Spirit creature token.",
                    470002,
                    type_line="Creature — Human",
                ),
                "triggered_ability",
                "create-fixed-creature-token-v2",
                {
                    "token.creation.fixed_definition",
                    "trigger.event.normalized_zone_change",
                    "trigger.placement.apnap",
                },
                "Token Creature — Spirit",
            ),
            (
                token_record(
                    "Fixed Food Activation",
                    "{2}, {T}: Create a Food token.",
                    470003,
                    type_line="Artifact",
                ),
                "activated_ability",
                "create-fixed-food-token-v1",
                {"life.change.effect", "token.creation.fixed_definition"},
                "Token Artifact — Food",
            ),
            (
                token_record(
                    "Fixed Glimmer Spell",
                    "Create a 1/1 white Glimmer enchantment creature token.",
                    470004,
                ),
                "spell_ability",
                "create-fixed-creature-token-v2",
                {"token.creation.fixed_definition"},
                "Token Creature Enchantment — Glimmer",
            ),
            (
                token_record(
                    "Fixed Golem Activation",
                    "{2}, {T}: Create a 3/3 colorless Golem enchantment "
                    "artifact creature token.",
                    470005,
                    type_line="Artifact",
                ),
                "activated_ability",
                "create-fixed-creature-token-v2",
                {"token.creation.fixed_definition"},
                "Token Artifact Creature Enchantment — Golem",
            ),
        )
        for record, kind, template_id, required, token_type_line in fixtures:
            with self.subTest(record=record.name):
                ir = compile_oracle_card(
                    record,
                    capability_registry=self.capabilities,
                    capability_profile="commander_review",
                )
                self.assertEqual("exact", ir.status)
                node = ir.faces[0].nodes[0]
                self.assertEqual(kind, node.kind)
                self.assertEqual(template_id, node.template_id)
                self.assertTrue(required.issubset(node.capability_dependencies))
                self.assertEqual(
                    token_type_line,
                    node.effects[0]["characteristics"]["type_line"],
                )
                self.assertEqual(
                    record.oracle_text[node.span.start : node.span.end],
                    node.text,
                )

        registry = SemanticRegistry(include_builtin_packs=False)
        result = register_generated_programs(
            self.db,
            registry,
            tuple(record for record, *_ in fixtures),
            capability_registry=self.capabilities,
            capability_profile="commander_review",
            promote_exact_trigger_programs=True,
            promote_exact_effect_programs=True,
        )
        self.assertEqual(5, result["exact_programs_promoted"])
        self.assertEqual(
            {"trusted"}, {program.trust_level for program in registry.programs()}
        )

    def test_fixed_token_creation_rejects_dynamic_copy_attached_and_custom_ability_variants(
        self,
    ):
        promoted_copy = "Create a token that's a copy of target creature you control."
        self.assertIsNone(fixed_token_creation_effect_template(promoted_copy),
                          "The isolated legacy leaf remains narrow")
        promoted = compile_oracle_card(token_record("Generic Controlled Copy", promoted_copy, 470009),
            capability_registry=self.capabilities, capability_profile="commander_review")
        self.assertEqual("exact", promoted.status, promoted.material_residuals)
        self.assertIn("token.creation.fixed_copy", promoted.faces[0].nodes[0].capability_dependencies)
        role_text = 'Create a Wicked Role token attached to target creature you control.'
        self.assertIsNone(fixed_token_creation_effect_template(role_text))
        role = compile_oracle_card(token_record('Generic Role Integration', role_text, 365211001),
            capability_registry=self.capabilities, capability_profile='commander_review')
        self.assertEqual('exact', role.status, role.material_residuals)
        outer = next(node for node in role.faces[0].nodes if node.kind == 'spell_ability')
        self.assertEqual('create_role_token', outer.effects[0]['op'])
        self.assertEqual('Wicked', outer.effects[0]['name'])
        self.assertEqual('$target.0', outer.effects[0]['aura_target_ref'])
        self.assertEqual('you', outer.target_schema['controller_relation'])
        self.assertIn('token.creation.predefined_role', outer.capability_dependencies)
        unsupported = (
            "Create X 1/1 green Saproling creature tokens.",
            "Create a token that's a copy of the chosen creature you control.",
            "Create an Unrepresented Role token attached to target creature you control.",
            "Create two 1/1 white Soldier creature tokens blocking target attacking creature.",
            "Create a 1/1 blue and red Otter creature token with prowess 2.",
            "Create a 3/3 colorless Golem artifact artifact creature token.",
            "Create 0 1/1 green Saproling creature tokens.",
        )
        base = token_record("Unsupported Token Fixture", unsupported[0], 470010)
        for index, text in enumerate(unsupported):
            with self.subTest(text=text):
                self.assertIsNone(fixed_token_creation_effect_template(text))
                ir = compile_oracle_card(
                    replace(
                        base,
                        oracle_id=f"00000000-0000-4000-8000-{470010 + index:012d}",
                        oracle_text=text,
                    ),
                    capability_registry=self.capabilities,
                    capability_profile="commander_review",
                )
                self.assertNotEqual("exact", ir.status)
                self.assertTrue(ir.material_residuals)

    def test_fixed_token_creator_target_requires_one_closed_player_and_revalidation(self):
        from quorune.rules.token_creation_capability_shapes import (
            fixed_token_creation_node_capabilities,
            token_definition_payload_dependencies,
        )

        for subject, relation in (("target player", "any"), ("target opponent", "opponent")):
            with self.subTest(subject=subject):
                text = f"{subject} creates two tapped Treasure tokens."
                template = fixed_token_creation_effect_template(text)
                self.assertIsNotNone(template)
                self.assertEqual("$target.0", template.effect["target"])
                self.assertNotIn("controller", template.effect)
                self.assertEqual(2, template.effect["quantity"])
                self.assertTrue(template.effect["tapped"])
                self.assertEqual(
                    {"zones": ["player"], "categories": ["player"], "player_relation": relation, "count": 1},
                    template.target_schema,
                )
                ir = compile_oracle_card(
                    token_record("Generic Targeted Token Fixture", text, 711001),
                    capability_registry=self.capabilities,
                    capability_profile="commander_review",
                )
                self.assertEqual("exact", ir.status, ir.material_residuals)
                self.assertIn("target.revalidate_resolution", ir.faces[0].nodes[0].capability_dependencies)
                self.assertIn("target.revalidate_resolution", token_definition_payload_dependencies(template.effect))

                for effect, schema in (
                    ({**template.effect, "controller": "$controller"}, template.target_schema),
                    ({**template.effect, "target": "$target.1"}, template.target_schema),
                    (template.effect, None),
                    (template.effect, {**template.target_schema, "count": True}),
                    (template.effect, {**template.target_schema, "count": 2}),
                    (template.effect, {**template.target_schema, "player_relation": "you"}),
                    (template.effect, {**template.target_schema, "optional": True}),
                ):
                    self.assertEqual(
                        (),
                        fixed_token_creation_node_capabilities(
                            effects=(effect,), target_schema=schema, mechanic_ids=set(template.mechanics),
                        ),
                    )

    def test_explicit_fixed_token_creator_preserves_the_controller_and_closed_body(self):
        implicit = fixed_token_creation_effect_template("Create a Mutagen token.")
        self.assertIsNotNone(implicit)
        self.assertEqual(implicit, fixed_token_creation_effect_template("you create a Mutagen token."))
        for text in (
            "Each opponent creates a Treasure token for each artifact they control.",
            "Another target player creates a Treasure token.",
            "Target opponent creates X Treasure tokens.",
            "Target player creates a token that's a copy of target creature you control.",
        ):
            with self.subTest(text=text):
                self.assertIsNone(fixed_token_creation_effect_template(text))

    def test_literal_token_name_is_shared_by_preview_and_commit_without_catalog_alias(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        from quorune.fixed_token_production import TOKEN_LITERAL_NAME_VERSION_FIELD
        from quorune.rules.token_creation_capability_shapes import fixed_token_creation_node_capabilities
        from quorune.token_creation_identity import (
            TokenCreationError,
            _new_token_identity,
            _preview_token_object,
        )

        template = fixed_token_creation_effect_template(
            "Create a 1/1 red Goblin creature token named Metallic Sliver."
        )
        self.assertIsNotNone(template)
        self.assertEqual("Metallic Sliver", template.effect["name"])
        self.assertEqual(1, template.effect["characteristics"][TOKEN_LITERAL_NAME_VERSION_FIELD])
        catalog_record = SimpleNamespace(oracle_id="catalog-sliver", name="Metallic Sliver")
        host = SimpleNamespace(
            card_db=Mock(), state=SimpleNamespace(token_copy_value_version=1),
            seats=["A", "B"], _stable_runtime_id=lambda namespace, ref: ref,
        )
        host.card_db.lookup.return_value = catalog_record
        values = template.effect["characteristics"]
        preview = _preview_token_object(host, "A", template.effect)
        identity = _new_token_identity(host, name="Metallic Sliver", characteristics=values, ref="T1")
        self.assertEqual("Metallic Sliver", preview.printed_name)
        self.assertEqual("Metallic Sliver", identity[2])
        self.assertTrue(identity[1].startswith("custom-token:"))
        self.assertEqual(preview.annotations, identity[3])
        self.assertNotIn(TOKEN_LITERAL_NAME_VERSION_FIELD, identity[3]["token_characteristics"])
        self.assertEqual("Token Creature — Goblin", identity[3]["token_characteristics"]["type_line"])
        host.card_db.lookup.assert_not_called()

        legacy = {key: value for key, value in values.items() if key != TOKEN_LITERAL_NAME_VERSION_FIELD}
        legacy_identity = _new_token_identity(host, name="Metallic Sliver", characteristics=legacy, ref="T1")
        self.assertEqual(catalog_record.oracle_id, legacy_identity[1])
        host.card_db.lookup.assert_called_once_with("Metallic Sliver")
        for malformed in (True, None, 2, "1"):
            with self.subTest(version=malformed):
                characteristics = {**values, TOKEN_LITERAL_NAME_VERSION_FIELD: malformed}
                self.assertEqual((), fixed_token_creation_node_capabilities(
                    effects=({**template.effect, "characteristics": characteristics},),
                    target_schema=None, mechanic_ids=set(template.mechanics),
                ))
                with self.assertRaisesRegex(TokenCreationError, "Unsupported literal token name version"):
                    _new_token_identity(host, name="Metallic Sliver", characteristics=characteristics, ref="T1")

    def test_tapped_attacking_token_batch_has_complete_normal_admission(self):
        from quorune.card_programs import bind_card_program_runtime
        from quorune.card_programs.adapters import compile_best_available_card_program

        text = "Create two 1/1 white Soldier creature tokens that are tapped and attacking."
        template = fixed_token_creation_effect_template(text)
        self.assertIsNotNone(template)
        self.assertEqual("choose_fixed_attacking_token_destinations", template.effect["op"])
        self.assertEqual(2, template.effect["quantity"])
        self.assertTrue(template.effect["tapped"])
        record = token_record("Generic Attacking Soldier Batch", text, 470009)
        ir = compile_oracle_card(record, capability_registry=self.capabilities,
                                 capability_profile="commander_review")
        self.assertEqual("exact", ir.status)
        program = compile_best_available_card_program(
            self.db, record, semantic_registry=SemanticRegistry(),
            capability_registry=self.capabilities, capability_profile="commander_review",
        )
        binding = bind_card_program_runtime(program, capability_registry=self.capabilities,
                                            profile="commander_review")
        self.assertTrue(binding["strict_capability_ready"], binding["blockers"])

    def test_explicit_token_ability_compiles_as_source_pinned_typed_program(self):
        record = token_record(
            "Typed Pest Token Fixture",
            "Create two 1/1 black and green Pest creature tokens with "
            '"When this token dies, you gain 1 life."',
            470009,
        )
        ir = compile_oracle_card(
            record,
            capability_registry=self.capabilities,
            capability_profile="commander_review",
        )
        self.assertEqual("exact", ir.status, ir.material_residuals)
        outer = next(
            node
            for node in ir.faces[0].nodes
            if node.kind == "spell_ability"
        )
        inner = next(
            node
            for node in ir.faces[0].nodes
            if node.kind == "granted_triggered_ability"
        )
        fragments = outer.effects[0]["characteristics"]["ability_fragments"]
        self.assertEqual("granted_triggered", fragments[0]["kind"])
        self.assertEqual(
            f"{record.oracle_id}:{fragments[0]['value']['ability_id']}",
            fragments[0]["value"]["semantic_key"],
        )
        self.assertEqual(record.oracle_text, outer.text)
        self.assertEqual("When this token dies, you gain 1 life.", inner.text)

    def test_typed_token_ability_grammar_and_dependencies_fail_closed(self):
        fixtures = (
            "Create a 1/1 green Saproling creature token with "
            '"Perform an unsupported action."',
            "Create a 1/1 green Saproling creature token with "
            '"When this token dies, draw a card." and "{T}: Add {G}." '
            'and "This token can\'t block."',
            "Create a 1/1 green Saproling creature token with "
            '"If damage would be dealt to this token, prevent that damage."',
        )
        for index, text in enumerate(fixtures):
            with self.subTest(text=text):
                ir = compile_oracle_card(
                    token_record(
                        "Unsupported Typed Token Fixture",
                        text,
                        470030 + index,
                    ),
                    capability_registry=self.capabilities,
                    capability_profile="commander_review",
                )
                self.assertNotEqual("exact", ir.status)
                self.assertTrue(ir.material_residuals)

        record = token_record(
            "Typed Token Dependency Fixture",
            "Create a 1/1 black and green Pest creature token with "
            '"When this token dies, you gain 1 life."',
            470039,
        )
        baseline = compile_oracle_card(
            record,
            capability_registry=self.capabilities,
            capability_profile="commander_review",
        )
        outer = baseline.faces[0].nodes[0]
        malformed_effects = copy.deepcopy(outer.effects)
        malformed_effects[0]["characteristics"]["ability_fragments"][0][
            "value"
        ]["semantic_key"] = "unrelated:semantic-key"
        self.assertNotIn(
            "token.creation.typed_ability_definition",
            capability_dependencies_for_node(
                effects=malformed_effects,
                target_schema=outer.target_schema,
                mechanic_ids=outer.mechanics,
                cost_schema=outer.cost,
            ),
        )
        for capability_id in (
            "token.creation.typed_ability_definition",
            "token.creation.fixed_definition",
        ):
            with self.subTest(capability_id=capability_id):
                value = json.loads(
                    CAPABILITY_REGISTRY_PATH.read_text(encoding="utf-8")
                )
                row = next(
                    item
                    for item in value["capabilities"]
                    if item["id"] == capability_id
                )
                row["status"] = "blocked"
                row["blockers"] = ["focused typed-token mutation"]
                registry = CapabilityRegistry(value)
                registry.mark_evidence_verified("0" * 64)
                ir = compile_oracle_card(
                    record,
                    capability_registry=registry,
                    capability_profile="commander_review",
                )
                self.assertNotEqual("exact", ir.status)
                self.assertTrue(ir.material_residuals)

        with patch(
            "quorune.compiler.typed_token_ability_nodes."
            "typed_token_ability_nodes",
            return_value=None,
        ):
            mutated = compile_oracle_card(
                record,
                capability_registry=self.capabilities,
                capability_profile="commander_review",
            )
        self.assertEqual("unresolved", mutated.status)

    def test_enchantment_tokens_and_additional_cost_spell_are_closed(self):
        fixtures = (
            (
                token_record(
                    "Golem Enchantment Fixture",
                    "{2}, {T}: Create a 3/3 colorless Golem enchantment "
                    "artifact creature token.",
                    470006,
                    type_line="Artifact",
                ),
                "Token Artifact Creature Enchantment — Golem",
            ),
            (
                token_record(
                    "Cleric Enchantment Fixture",
                    "{2}, {T}: Create a 2/1 white Cleric enchantment "
                    "creature token.",
                    470007,
                    type_line="Artifact",
                ),
                "Token Creature Enchantment — Cleric",
            ),
            (
                token_record(
                    "Additional Cost Token Fixture",
                    "As an additional cost to cast this spell, sacrifice an "
                    "artifact.\nCreate three 1/1 red Goblin creature tokens.",
                    470008,
                    type_line="Sorcery",
                ),
                "Token Creature — Goblin",
            ),
        )
        for record, token_type_line in fixtures:
            with self.subTest(card_name=record.name):
                ir = compile_oracle_card(
                    record,
                    capability_registry=self.capabilities,
                    capability_profile="commander_review",
                )
                node = next(
                    node
                    for face in ir.faces
                    for node in face.nodes
                    if "create-fixed-creature-token-v2" in str(
                        node.template_id
                    )
                )
                self.assertTrue(node.exact, ir.material_residuals)
                self.assertIn(
                    "token.creation.fixed_definition",
                    node.capability_dependencies,
                )
                self.assertEqual(
                    token_type_line,
                    node.effects[0]["characteristics"]["type_line"],
                )

    def test_fixed_token_capability_shape_rejects_malformed_effects(self):
        template = fixed_token_creation_effect_template(
            "Create two 1/1 white Spirit creature tokens with flying."
        )
        self.assertIsNotNone(template)
        effect = dict(template.effect)
        mechanics = template.mechanics
        self.assertIn(
            "token.creation.fixed_definition",
            capability_dependencies_for_node(
                effects=(effect,),
                target_schema=None,
                mechanic_ids=mechanics,
            ),
        )
        malformed = (
            {**effect, "quantity": 0},
            {**effect, "quantity": True},
            {**effect, "controller": "$target.0"},
            {**effect, "tapped": False},
            {**effect, "unknown": True},
            {
                **effect,
                "characteristics": {
                    **effect["characteristics"],
                    "colors": ["R", "W"],
                },
            },
        )
        for candidate in malformed:
            with self.subTest(effect=candidate):
                self.assertNotIn(
                    "token.creation.fixed_definition",
                    capability_dependencies_for_node(
                        effects=(candidate,),
                        target_schema=None,
                        mechanic_ids=mechanics,
                    ),
                )
        self.assertNotIn(
            "token.creation.fixed_definition",
            capability_dependencies_for_node(
                effects=(effect,),
                target_schema=None,
                mechanic_ids=("cr-111-tokens",),
            ),
        )

    def test_fixed_token_compiler_mutation_is_killed(self):
        record = token_record(
            "Fixed Token Mutation Fixture",
            "Create a 1/1 green Saproling creature token.",
            470020,
        )
        self.assertTrue(
            generated_programs(
                self.db,
                record,
                capability_registry=self.capabilities,
                capability_profile="commander_review",
            )
        )
        with patch(
            "quorune.oracle_ir.fixed_token_creation_effect_template",
            return_value=None,
        ):
            ir = compile_oracle_card(
                record,
                capability_registry=self.capabilities,
                capability_profile="commander_review",
            )
        self.assertEqual("unresolved", ir.status)
        self.assertEqual("spell_effect", ir.material_residuals[0].kind)

    def test_fixed_token_batches_and_clues_promote_the_bounded_real_cards(self):
        exact_names = (
            "Mascot Exhibition",
            "Forbidden Friendship",
            "Bestial Menace",
            "Sokka's Sword Training",
            "Cunning Maneuver",
            "Forecasting Fortune Teller",
            "Knowledge Seeker",
            "True Ancestry",
        )
        for name in exact_names:
            with self.subTest(name=name):
                record = self.db.lookup(name)
                ir = compile_oracle_card(
                    record,
                    capability_registry=self.capabilities,
                    capability_profile="commander_review",
                )
                self.assertEqual("exact", ir.status)
                self.assertFalse(ir.material_residuals)
                programs = generated_programs(
                    self.db,
                    record,
                    trust_level="trusted",
                    capability_registry=self.capabilities,
                    capability_profile="commander_review",
                )
                self.assertTrue(programs)
                self.assertEqual(
                    {"trusted"},
                    {program.trust_level for program in programs},
                )

        partial = {"Devouring Sugarmaw // Have for Dinner": "trigger_effect"}
        for name, residual_kind in partial.items():
            with self.subTest(name=name):
                ir = compile_oracle_card(
                    self.db.lookup(name),
                    capability_registry=self.capabilities,
                    capability_profile="commander_review",
                )
                self.assertEqual("partial", ir.status)
                self.assertIn(
                    residual_kind,
                    {residual.kind for residual in ir.material_residuals},
                )
                token_nodes = [
                    node
                    for face in ir.faces
                    for node in face.nodes
                    if "token.creation.fixed_definition"
                    in node.capability_dependencies
                ]
                self.assertEqual(1, len(token_nodes))

    def test_fixed_token_batch_and_clue_shapes_are_exact_and_fail_closed(self):
        batch = fixed_token_creation_effect_template(
            "Create a 1/1 red Dinosaur creature token with haste and a "
            "1/1 white Human Soldier creature token."
        )
        self.assertIsNotNone(batch)
        self.assertEqual(
            "create-fixed-token-definition-batch-v1", batch.template_id
        )
        self.assertEqual("create_token_batch", batch.effect["op"])
        self.assertEqual(2, len(batch.effect["tokens"]))
        self.assertIn(FIXED_TOKEN_DEFINITION_BATCH_MECHANIC, batch.mechanics)
        self.assertEqual(
            {
                "activation.tap_untap_cost.haste",
                "combat.attack.haste",
                "token.creation.fixed_definition",
            },
            set(
                capability_dependencies_for_node(
                    effects=(batch.effect,),
                    target_schema=None,
                    mechanic_ids=batch.mechanics,
                )
            ),
        )

        clue = fixed_token_creation_effect_template("Create a Clue token.")
        self.assertIsNotNone(clue)
        self.assertEqual(
            {
                "token.creation.fixed_definition",
                "zone.draw.library_to_hand",
            },
            set(
                capability_dependencies_for_node(
                    effects=(clue.effect,),
                    target_schema=None,
                    mechanic_ids=clue.mechanics,
                )
            ),
        )

        declaration_batch = fixed_token_creation_effect_template(
            "Create a 1/1 black Rat creature token with \"This token can't "
            "block.\" and a Food token."
        )
        self.assertIsNotNone(declaration_batch)
        self.assertTrue(
            {
                "combat.declaration.typed_components",
                "token.creation.fixed_definition",
            }.issubset(
                capability_dependencies_for_node(
                    effects=(declaration_batch.effect,),
                    target_schema=None,
                    mechanic_ids=declaration_batch.mechanics,
                )
            )
        )

        malformed = (
            {**batch.effect, "tokens": batch.effect["tokens"][:1]},
            {**batch.effect, "tokens": [*batch.effect["tokens"], {}, {}]},
            {**batch.effect, "tokens": tuple(batch.effect["tokens"])},
            {
                **batch.effect,
                "tokens": [
                    {**batch.effect["tokens"][0], "quantity": 0},
                    batch.effect["tokens"][1],
                ],
            },
            {**batch.effect, "unknown": True},
        )
        for effect in malformed:
            with self.subTest(effect=effect):
                self.assertNotIn(
                    "token.creation.fixed_definition",
                    capability_dependencies_for_node(
                        effects=(effect,),
                        target_schema=None,
                        mechanic_ids=batch.mechanics,
                    ),
                )
        self.assertNotIn(
            "token.creation.fixed_definition",
            capability_dependencies_for_node(
                effects=(batch.effect,),
                target_schema=None,
                mechanic_ids=tuple(
                    mechanic
                    for mechanic in batch.mechanics
                    if mechanic != FIXED_TOKEN_DEFINITION_BATCH_MECHANIC
                ),
            ),
        )

        unsupported = (
            "Create X 1/1 green Saproling creature tokens and a Food token.",
            "Create a token that's a copy of target creature and a Food token.",
            "Create a Wicked Role token attached to target creature and a "
            "Treasure token.",
            "Create a 1/1 white Soldier creature token, a 1/1 blue Bird "
            "creature token with flying, a 1/1 black Rat creature token, "
            "and a 1/1 red Goblin creature token.",
        )
        for text in unsupported:
            with self.subTest(text=text):
                self.assertIsNone(fixed_token_creation_effect_template(text))

    def test_fixed_token_batch_compiler_mutation_is_killed(self):
        record = self.db.lookup("Mascot Exhibition")
        self.assertEqual(
            "exact",
            compile_oracle_card(
                record,
                capability_registry=self.capabilities,
                capability_profile="commander_review",
            ).status,
        )
        with patch(
            "quorune.oracle_ir.fixed_token_creation_effect_template",
            return_value=None,
        ):
            mutated = compile_oracle_card(
                record,
                capability_registry=self.capabilities,
                capability_profile="commander_review",
            )
        self.assertEqual("unresolved", mutated.status)
        self.assertTrue(mutated.material_residuals)


class FixedTokenCreationRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db, cls.mishra, cls.zimone = load_assets()
        cls.capabilities = load_default_capability_registry()

    @classmethod
    def tearDownClass(cls):
        cls.db.close()

    def session(
        self,
        seed: int,
        *,
        players: int = 2,
        card_name: str | None = None,
    ):
        deck = self.mishra
        if card_name is not None:
            deck = copy.deepcopy(self.mishra)
            next(
                entry for entry in deck.entries if entry.board == "mainboard"
            ).name = card_name
        session = make_session(
            self.db,
            deck,
            self.zimone,
            players=players,
            seed=seed,
            auto_pass_empty=False,
        )
        keep_all(session)
        engine = session.engine
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine.state.priority_player = None
        engine.state.priority_passes = []
        session.commands.clear()
        session.decisions.clear()
        return session

    @staticmethod
    def _prepare_priority(session, source):
        engine = session.engine
        engine.state.active_player = "A"
        engine.state.started = True
        engine.state.phase = "precombat_main"
        engine.state.step = "main"
        engine.move_card(
            source.object_id,
            "battlefield",
            controller="A",
            tapped=False,
            log=False,
        )
        engine.state.players["A"].mana_pool["W"] = 3
        engine._grant_priority("A")
        engine.pump()

    @staticmethod
    def _resolve_registered_program(engine, program, suffix: int):
        item = StackItem(
            stack_id=f"registered-token-{suffix}",
            ref=f"S-registered-token-{suffix}",
            kind="triggered_ability",
            controller="A",
            label=program.label,
            semantic_key=program.key,
            visibility=list(engine.active_seats),
        )
        engine.state.stack.append(item)
        engine._continue_resolution(
            stack_ref=item.ref,
            effects=[dict(effect) for effect in program.effects],
            destination=program.destination,
            note=program.notes,
        )

    @staticmethod
    def card(engine, name: str):
        return next(
            card
            for card in engine.state.cards.values()
            if card.owner == "A" and card.printed_name == name
        )

    def _compiled_program(self, text: str, suffix: int):
        record = token_record(
            f"Compiled Token Fixture {suffix}",
            text,
            suffix,
        )
        programs = generated_programs(
            self.db,
            record,
            trust_level="trusted",
            capability_registry=self.capabilities,
            capability_profile="commander_review",
        )
        self.assertEqual(1, len(programs))
        program = programs[0]
        self.assertEqual("trusted", program.trust_level)
        self.assertFalse(program.requires_arbiter)
        return program

    def _stack_compiled_program(self, engine, text: str, suffix: int):
        program = self._compiled_program(text, suffix)
        engine.semantics.put(program)
        item = StackItem(
            stack_id=f"compiled-token-{suffix}",
            ref=f"S-compiled-token-{suffix}",
            kind="triggered_ability",
            controller="A",
            label=program.label,
            semantic_key=program.key,
            visibility=list(engine.active_seats),
        )
        engine.state.stack.append(item)
        return item, program

    def _resolve_compiled_program(self, engine, text: str, suffix: int):
        item, program = self._stack_compiled_program(engine, text, suffix)
        engine._continue_resolution(
            stack_ref=item.ref,
            effects=[dict(effect) for effect in program.effects],
            destination=program.destination,
            note=program.notes,
        )
        return program

    def _resolve_typed_token_card(
        self, engine, name: str, *, controller: str = "A"
    ):
        record = self.db.lookup(name, fuzzy=False)
        programs = generated_programs(
            self.db,
            record,
            trust_level="trusted",
            capability_registry=self.capabilities,
            capability_profile="commander_review",
        )
        for program in programs:
            engine.semantics.put(program)
        outer = next(
            program
            for program in programs
            if not program.provenance.get("granted_only")
            and program.effects
        )
        item = StackItem(
            stack_id=f"typed-token-{name}-{controller}",
            ref=f"S-typed-token-{name}-{controller}",
            kind="triggered_ability",
            controller=controller,
            label=outer.label,
            semantic_key=outer.key,
            visibility=list(engine.active_seats),
        )
        engine.state.stack.append(item)
        engine._continue_resolution(
            stack_ref=item.ref,
            effects=[dict(effect) for effect in outer.effects],
            destination=outer.destination,
            note=outer.notes,
        )
        return programs

    @staticmethod
    def _pass_until(session, predicate, *, limit: int = 24):
        for _ in range(limit):
            if predicate():
                return
            principals = session.pending_principals()
            if not principals:
                raise AssertionError("Resolution stopped without a decision")
            result = session.act(
                principals[0], {"action_id": "pass"}
            )
            if not result.ok:
                raise AssertionError(result.summary)
        raise AssertionError("Resolution did not reach the expected state")

    def test_compiled_fixed_token_effect_uses_canonical_creation_owner(self):
        session = self.session(470101)
        engine = session.engine
        before = set(engine.state.cards)
        self._resolve_compiled_program(
            engine,
            "Create two 1/1 white Spirit creature tokens with flying.",
            470101,
        )
        spirits = [
            card
            for object_id, card in engine.state.cards.items()
            if object_id not in before and card.printed_name == "Spirit Token"
        ]
        self.assertEqual(2, len(spirits))
        self.assertEqual(1, len({card.zone_timestamp for card in spirits}))
        self.assertTrue(all(card.is_token for card in spirits))
        self.assertTrue(
            all(
                engine._effective_card_data(card)["keywords"] == ["Flying"]
                for card in spirits
            )
        )
        event = next(
            event
            for event in reversed(engine.state.events)
            if event.code == "token.create"
        )
        self.assertEqual(2, event.details["base_quantity"])

        self._resolve_compiled_program(
            engine,
            "Create a Food token.",
            470102,
        )
        food = next(
            card
            for card in engine.state.cards.values()
            if card.is_token and card.printed_name == "Food Token"
        )
        data = engine._effective_card_data(food)
        self.assertEqual("Artifact — Food", data["type_line"])
        self.assertEqual(1, len(data["activated_abilities"]))
        self.assertEqual("", data["oracle_text"])
        self.assertIn("You gain 3 life", data["display_oracle_text"])

        self._resolve_compiled_program(
            engine,
            "Create a 3/3 colorless Golem enchantment artifact creature token.",
            470103,
        )
        golem = next(
            card
            for card in engine.state.cards.values()
            if card.is_token and card.printed_name == "Golem Token"
        )
        golem_data = engine._effective_card_data(golem)
        self.assertEqual(
            "Artifact Creature Enchantment — Golem",
            golem_data["type_line"],
        )
        self.assertEqual("3", golem_data["power"])
        self.assertEqual("3", golem_data["toughness"])

    def test_typed_token_abilities_execute_across_spell_trigger_and_activation_contexts(self):
        fixtures = (
            (
                token_record(
                    "Typed Token Spell",
                    "Create a 1/1 black and green Pest creature token with "
                    '"When this token dies, you gain 1 life."',
                    470040,
                ),
                ("spell_ability", "granted_triggered_ability"),
            ),
            (
                token_record(
                    "Typed Token Trigger",
                    "When this creature enters, create a 0/1 colorless "
                    "Eldrazi Spawn creature token with "
                    '"Sacrifice this token: Add {C}."',
                    470041,
                    type_line="Creature — Test",
                ),
                ("triggered_ability", "granted_mana_ability"),
            ),
            (
                token_record(
                    "Typed Token Activation",
                    "{2}: Create a 1/1 red Mercenary creature token with "
                    '"{T}: Target creature you control gets +1/+0 until end '
                    'of turn. Activate only as a sorcery."',
                    470042,
                    type_line="Artifact",
                ),
                ("activated_ability", "granted_activated_ability"),
            ),
            (
                token_record(
                    "Typed Token Static",
                    "Create a 0/0 colorless Construct artifact creature token "
                    'with "This token gets +1/+1 for each artifact you control."',
                    470043,
                ),
                ("spell_ability",),
            ),
        )
        for record, expected_kinds in fixtures:
            with self.subTest(record=record.name):
                ir = compile_oracle_card(
                    record,
                    capability_registry=self.capabilities,
                    capability_profile="commander_review",
                )
                self.assertEqual("exact", ir.status, ir.material_residuals)
                self.assertEqual(
                    expected_kinds,
                    tuple(node.kind for node in ir.faces[0].nodes),
                )
                self.assertIn(
                    "token.creation.typed_ability_definition",
                    ir.faces[0].nodes[0].capability_dependencies,
                )

        session = self.session(470141)
        engine = session.engine
        self._resolve_typed_token_card(engine, "Skittering Invasion")
        spawn = next(
            card
            for card in engine.state.cards.values()
            if card.is_token
            and card.zone == "battlefield"
            and card.printed_name == "Eldrazi Spawn Token"
        )
        mana_ability = next(
            ability
            for ability in engine._activated_abilities(spawn)
            if ability.mana_ability
        )
        colorless_before = engine.state.players["A"].mana_pool["C"]
        engine._activate(
            "A",
            {"source": spawn.ref, "ability": mana_ability.ability_id},
        )
        self.assertEqual("outside", spawn.zone)
        self.assertEqual(
            colorless_before + 1,
            engine.state.players["A"].mana_pool["C"],
        )

        static_ir = compile_oracle_card(
            fixtures[-1][0],
            capability_registry=self.capabilities,
            capability_profile="commander_review",
        )
        static_outer = static_ir.faces[0].nodes[0]
        static_item = StackItem(
            stack_id="typed-static-token",
            ref="S-typed-static-token",
            kind="triggered_ability",
            controller="A",
            label="Typed static token",
            visibility=list(engine.active_seats),
        )
        engine.state.stack.append(static_item)
        engine._continue_resolution(
            stack_ref=static_item.ref,
            effects=[dict(effect) for effect in static_outer.effects],
            destination=None,
            note="typed static token witness",
        )
        construct = next(
            card
            for card in engine.state.cards.values()
            if card.is_token
            and card.zone == "battlefield"
            and card.printed_name == "Construct Token"
        )
        self.assertEqual("1", engine._effective_card_data(construct)["power"])
        engine.create_token(
            "A",
            name="Artifact Witness",
            characteristics={"type_line": "Token Artifact"},
            reason="typed token dynamic characteristic witness",
        )
        self.assertEqual("2", engine._effective_card_data(construct)["power"])

    def test_typed_token_ability_survives_creator_departure_and_uses_token_identity(self):
        session = self.session(470140)
        engine = session.engine
        self._resolve_typed_token_card(engine, "Pest Summoning")
        pests = [
            card
            for card in engine.state.cards.values()
            if card.is_token
            and card.zone == "battlefield"
            and card.printed_name == "Pest Token"
        ]
        self.assertEqual(2, len(pests))
        copied_ref = engine.create_token(
            "A",
            name="",
            copy_of=pests[0].ref,
            reason="typed token copiable ability witness",
        )[0]
        copied = engine._resolve_object(
            "A", copied_ref, zones={"battlefield"}
        )
        self.assertEqual(
            engine._effective_card_data(pests[0])["ability_fragments"],
            engine._effective_card_data(copied)["ability_fragments"],
        )
        engine.state.players["A"].life = 20
        engine.move_card(
            copied.object_id,
            "graveyard",
            controller="A",
            reason="typed token death witness",
            semantic_events=True,
        )
        self.assertFalse(begin_pending_trigger_batch(engine))
        matching = [
            item
            for item in engine.state.stack
            if ":granted:token:" in str(item.semantic_key)
        ]
        self.assertTrue(
            matching,
            {
                "annotations": pests[0].annotations,
                "stack": [item.to_dict() for item in engine.state.stack],
                "pending": engine.state.pending_trigger_batches,
                "events": [event.to_dict() for event in engine.state.events[-8:]],
            },
        )
        trigger = matching[0]
        self.assertEqual(copied.object_id, trigger.source_object_id)
        program = engine.semantics.get(trigger.semantic_key)
        self.assertIsNotNone(program)
        engine._continue_resolution(
            stack_ref=trigger.ref,
            effects=[dict(effect) for effect in program.effects],
            destination=program.destination,
            note=program.notes,
        )
        self.assertEqual(21, engine.state.players["A"].life)
        self.assertNotEqual(copied.object_id, pests[0].object_id)
        self.assertEqual("battlefield", pests[0].zone)

    def test_typed_token_ability_apnap_privacy_and_replay(self):
        session = self.session(470142, players=4)
        engine = session.engine
        self._resolve_typed_token_card(
            engine, "Pest Summoning", controller="A"
        )
        self._resolve_typed_token_card(
            engine, "Pest Summoning", controller="B"
        )
        pests = [
            card
            for card in engine.state.cards.values()
            if card.is_token
            and card.zone == "battlefield"
            and card.printed_name == "Pest Token"
        ]
        self.assertEqual(4, len(pests))
        damage_source = self.card(engine, "Mishra, Eminent One")
        engine.move_card(
            damage_source.object_id,
            "battlefield",
            controller="A",
            log=False,
        )
        resolve_damage_batch(
            engine,
            tuple(
                damage_proposal(
                    engine,
                    proposal_id=f"typed-token-apnap:{index}",
                    actor="A",
                    source_ref=damage_source.ref,
                    target=pest.ref,
                    amount=1,
                    combat=False,
                    reason="typed token APNAP witness",
                )
                for index, pest in enumerate(pests)
            ),
        )
        engine._stabilize()
        decision = engine.state.pending_decision
        self.assertIsNotNone(decision)
        self.assertEqual("trigger.order", decision.kind)
        pending = [
            item
            for batch in engine.state.pending_trigger_batches
            for item in batch.items
        ]
        self.assertEqual(4, len(pending))
        self.assertEqual(
            {"A", "B"}, {item.controller for item in pending}
        )
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()

        while (
            engine.state.pending_decision is not None
            and engine.state.pending_decision.kind == "trigger.order"
        ):
            actor = engine.state.pending_decision.actors[0]
            projected = session.packet(f"pilot:{actor}", full=True)["decision"]
            self.assertEqual("trigger.order", projected["kind"])
            for other in "ABCD":
                if other != actor:
                    self.assertIsNone(
                        session.packet(f"pilot:{other}", full=True)["decision"]
                    )
            serialized = json.dumps(projected, sort_keys=True)
            self.assertTrue(
                all(pest.object_id not in serialized for pest in pests)
            )
            refs = [item["id"] for item in projected["ctx"]["triggers"]]
            ordered = session.act(
                f"pilot:{actor}",
                {"action_id": "order", "triggers": refs},
            )
            self.assertTrue(ordered.ok, ordered.summary)

        placed = [
            item
            for item in engine.state.stack
            if ":granted:token:" in str(item.semantic_key)
        ]
        self.assertEqual(4, len(placed))
        self.assertEqual(
            ["A", "A", "B", "B"],
            [item.controller for item in placed],
        )
        life_before = {
            seat: engine.state.players[seat].life for seat in ("A", "B")
        }
        for _ in range(32):
            if not engine.state.stack:
                break
            pass_current(session)
        self.assertFalse(engine.state.stack)
        self.assertEqual(life_before["A"] + 2, engine.state.players["A"].life)
        self.assertEqual(life_before["B"] + 2, engine.state.players["B"].life)
        expected_hash = authoritative_state_hash(engine.state)
        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / "typed-token-ability-record"
            session.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(expected_hash, replay["final_state_hash"])

    def test_compiled_fixed_token_batch_is_one_simultaneous_transaction(self):
        session = self.session(470130)
        engine = session.engine
        before = set(engine.state.cards)
        self._resolve_compiled_program(
            engine,
            "Create a 1/1 red Dinosaur creature token with haste and a "
            "1/1 white Human Soldier creature token.",
            470130,
        )
        created = [
            card
            for object_id, card in engine.state.cards.items()
            if object_id not in before
        ]
        self.assertEqual(
            {"Dinosaur Token", "Human Soldier Token"},
            {card.printed_name for card in created},
        )
        self.assertEqual(1, len({card.zone_timestamp for card in created}))
        dinosaur = next(
            card for card in created if card.printed_name == "Dinosaur Token"
        )
        soldier = next(
            card for card in created if card.printed_name == "Human Soldier Token"
        )
        self.assertEqual(
            ["Haste"], engine._effective_card_data(dinosaur)["keywords"]
        )
        self.assertEqual(
            [], engine._effective_card_data(soldier)["keywords"]
        )
        event = next(
            event
            for event in reversed(engine.state.events)
            if event.code == "token.create"
        )
        self.assertEqual(2, event.details["base_quantity"])
        self.assertEqual(0, event.details["replacement_count"])
        self.assertEqual(
            {card.ref for card in created}, set(event.details["objects"])
        )

    def test_fixed_token_batch_replacement_order_is_private_and_shared(self):
        session = self.session(470131, players=4)
        engine = session.engine
        self._install_replacement_sources(
            engine, ("Stridehangar Automaton", "Worldwalker Helm")
        )
        item, program = self._stack_compiled_program(
            engine,
            "Create a 1/1 white Human creature token and a Food token.",
            470131,
        )
        engine._continue_resolution(
            stack_ref=item.ref,
            effects=[dict(effect) for effect in program.effects],
            destination=program.destination,
            note=program.notes,
        )

        decision = engine.state.pending_decision
        self.assertEqual("replacement.order", decision.kind)
        self.assertEqual(["A"], decision.actors)
        projector = StateProjector(self.db, engine.state)
        projected = projector._decision("pilot:A")
        for seat in ("B", "C", "D"):
            self.assertIsNone(projector._decision(f"pilot:{seat}"))
        before = authoritative_state_hash(engine.state)
        rejected = engine.try_submit(
            token=engine.permissions.capability_for("pilot:A").token,
            principal="pilot:A",
            action="choose",
            payload={"replacement": "unknown-replacement"},
        )
        self.assertFalse(rejected.ok)
        self.assertEqual(before, authoritative_state_hash(engine.state))

        result = engine.submit(
            token=engine.permissions.capability_for("pilot:A").token,
            principal="pilot:A",
            action="choose",
            payload={
                "replacement": projected["ctx"]["options"][0]["id"]
            },
        )
        self.assertTrue(result.ok, result.summary)
        tokens = [
            card
            for card in engine.state.cards.values()
            if card.is_token and card.zone == "battlefield"
        ]
        self.assertEqual(
            {"Food Token", "Human Token", "Map Token", "Thopter Token"},
            {card.printed_name for card in tokens},
        )
        self.assertEqual(1, len({card.zone_timestamp for card in tokens}))
        event = next(
            event
            for event in reversed(engine.state.events)
            if event.code == "token.create"
        )
        self.assertEqual(2, event.details["base_quantity"])
        self.assertEqual(2, event.details["replacement_count"])

    def test_fixed_token_batch_malformed_effect_rolls_back_atomically(self):
        session = self.session(470132)
        engine = session.engine
        template = fixed_token_creation_effect_template(
            "Create a 1/1 red Dinosaur creature token with haste and a "
            "1/1 white Human Soldier creature token."
        )
        self.assertIsNotNone(template)
        malformed = copy.deepcopy(template.effect)
        malformed["tokens"][1]["characteristics"][
            "activated_ability_profile"
        ] = "unsupported-profile"
        before = authoritative_state_hash(engine.state)
        with self.assertRaises(GameRuleError):
            engine.apply_effect(malformed, actor="A")
        self.assertEqual(before, authoritative_state_hash(engine.state))

    def test_compiled_clue_activation_draws_privately_and_replays_exactly(self):
        session = self.session(470133)
        engine = session.engine
        self._resolve_compiled_program(
            engine, "Create a Clue token.", 470133
        )
        clue = next(
            card
            for card in engine.state.cards.values()
            if card.is_token and card.printed_name == "Clue Token"
        )
        abilities = compiled_activated_abilities(engine, clue)
        self.assertEqual(1, len(abilities))
        ability = abilities[0]
        self.assertEqual("builtin:draw:1", ability.builtin_semantic_key)
        self.assertTrue(ability.sacrifice_source)
        self.assertFalse(ability.tap_source)

        engine.state.players["A"].mana_pool["C"] = 2
        engine.state.active_player = "A"
        engine.state.started = True
        engine.state.phase = "precombat_main"
        engine.state.step = "main"
        engine._grant_priority("A")
        engine.pump()
        action_id = f"activate:{clue.ref}:{ability.ability_id}"
        packet = session.packet("pilot:A", full=True)
        offered = {
            action["id"]
            for action in packet["decision"]["ctx"]["legal"]["actions"]
        }
        self.assertIn(action_id, offered)
        top = engine.state.players["A"].zones["library"][-1]
        top_ref = engine.state.cards[top].ref
        hand_before = len(engine.state.players["A"].zones["hand"])
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()

        result = session.act("pilot:A", {"action_id": action_id})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("outside", clue.zone)
        self.assertEqual(0, engine.state.players["A"].mana_pool["C"])
        self._pass_until(session, lambda: not engine.state.stack)
        self.assertEqual(
            hand_before + 1, len(engine.state.players["A"].zones["hand"])
        )
        self.assertIn(top, engine.state.players["A"].zones["hand"])
        self.assertNotIn(
            "hand",
            session.packet("pilot:B", full=True)["state"]["players"]["A"],
        )
        self.assertNotIn(
            top_ref,
            json.dumps(session.packet("pilot:B", full=True)),
        )

        expected_hash = authoritative_state_hash(engine.state)
        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / "clue-token-record"
            session.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(expected_hash, replay["final_state_hash"])

    def _install_replacement_sources(self, engine, names):
        for name in names:
            source = self.card(engine, name)
            engine.move_card(source.object_id, "battlefield", controller="A")

    def test_compiled_fixed_token_effect_suspends_for_replacement_order(self):
        session = self.session(470102, players=4)
        engine = session.engine
        self._install_replacement_sources(
            engine, ("Stridehangar Automaton", "Worldwalker Helm")
        )
        item, program = self._stack_compiled_program(
            engine,
            "Create a Treasure token.",
            470103,
        )
        engine._continue_resolution(
            stack_ref=item.ref,
            effects=[dict(effect) for effect in program.effects],
            destination=program.destination,
            note=program.notes,
        )

        decision = engine.state.pending_decision
        self.assertEqual("replacement.order", decision.kind)
        self.assertEqual(["A"], decision.actors)
        projector = StateProjector(self.db, engine.state)
        projected = projector._decision("pilot:A")
        for seat in ("B", "C", "D"):
            self.assertIsNone(projector._decision(f"pilot:{seat}"))
        serialized = json.dumps(projected)
        self.assertNotIn("replacement_batch", serialized)
        self.assertNotIn("replacement_effects", serialized)
        before = authoritative_state_hash(engine.state)
        capability = engine.permissions.capability_for("pilot:A")
        rejected = engine.try_submit(
            token=capability.token,
            principal="pilot:A",
            action="choose",
            payload={"replacement": "unknown-replacement"},
        )
        self.assertFalse(rejected.ok)
        self.assertEqual(before, authoritative_state_hash(engine.state))

        capability = engine.permissions.capability_for("pilot:A")
        result = engine.submit(
            token=capability.token,
            principal="pilot:A",
            action="choose",
            payload={"replacement": projected["ctx"]["options"][0]["id"]},
        )
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(
            {"Map Token", "Thopter Token", "Treasure Token"},
            {
                card.printed_name
                for card in engine.state.cards.values()
                if card.is_token and card.zone == "battlefield"
            },
        )

    def test_compiled_fixed_token_effect_replays_exactly(self):
        session = self.session(470103)
        engine = session.engine
        self._install_replacement_sources(engine, ("Worldwalker Helm",))
        self._stack_compiled_program(
            engine,
            "Create a Treasure token.",
            470104,
        )
        engine.state.active_player = "A"
        engine.state.phase = "precombat_main"
        engine.state.step = "main"
        engine._grant_priority("A")
        engine._issue_priority("A")
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()

        for principal in ("pilot:A", "pilot:B"):
            result = session.act(principal, {"action_id": "pass"})
            self.assertTrue(result.ok, result.summary)
        self.assertEqual(
            {"Map Token": 1, "Treasure Token": 1},
            {
                name: sum(
                    card.is_token
                    and card.zone == "battlefield"
                    and card.printed_name == name
                    for card in engine.state.cards.values()
                )
                for name in ("Map Token", "Treasure Token")
            },
        )

        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / "fixed-token-record"
            session.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(2, replay["commands"])

    def test_selected_soldier_cost_resolves_combat_prevention_and_replays(
        self,
    ):
        session = self.session(
            470120,
            card_name="Knight-Captain of Eos",
        )
        engine = session.engine
        source = self.card(engine, "Knight-Captain of Eos")
        engine.create_token(
            "A",
            name="Soldier Token",
            quantity=1,
            characteristics={
                "type_line": "Token Creature — Soldier",
                "colors": ["W"],
                "power": "1",
                "toughness": "1",
            },
            reason="typed prevention interaction fixture",
        )
        soldier = next(
            card
            for card in engine.state.cards.values()
            if card.is_token
            and card.controller == "A"
            and card.printed_name == "Soldier Token"
        )
        combat_source = next(
            card
            for card in engine.state.cards.values()
            if card.owner == "B" and card.printed_name == "Seedborn Muse"
        )
        engine.move_card(
            combat_source.object_id,
            "battlefield",
            controller="B",
            log=False,
        )
        soldiers_before = sum(
            card.is_token and card.printed_name == "Soldier Token"
            for card in engine.state.cards.values()
        )
        self._prepare_priority(session, source)

        programs = engine.semantics.programs_for_oracle(source.oracle_id)
        token_program = next(
            program
            for program in programs
            if program.effects
            and program.effects[0].get("op") == "create_token"
        )
        self.assertEqual("trusted", token_program.trust_level)
        prevention_carrier = next(
            program
            for program in programs
            if program.event == "activate"
        )
        self.assertEqual("trusted", prevention_carrier.trust_level)
        self.assertEqual(
            [
                {
                    "op": "create_damage_prevention_shield",
                    "source": "$source",
                    "subject": "*",
                    "mode": "all",
                    "duration": "until_end_of_turn",
                    "damage_kind": "combat",
                }
            ],
            prevention_carrier.effects,
        )
        self.assertEqual(
            {
                "activation.selected_zone_change.fixed",
                "damage.prevention.persistent_amount",
            },
            set(prevention_carrier.capability_dependencies),
        )

        prevention = next(
            ability
            for ability in compiled_activated_abilities(engine, source)
            if "Prevent all combat damage" in ability.effect_text
        )
        self.assertEqual(
            ("payable", None),
            engine._ability_availability("A", source, prevention),
        )
        action_id = f"activate:{source.ref}:{prevention.ability_id}"
        packet = session.packet("pilot:A", full=True)
        action = next(
            action
            for action in packet["decision"]["ctx"]["legal"]["actions"]
            if action["id"] == action_id
        )
        self.assertEqual(
            [soldier.ref],
            action["cost_summary"]["choose_cost"][0]["legal_refs"],
        )
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()

        activated = session.act(
            "pilot:A",
            {"action_id": action_id, "cost_cards": [soldier.ref]},
        )
        self.assertTrue(activated.ok, activated.summary)
        self.assertEqual("outside", soldier.zone)
        self.assertTrue(engine.state.stack)
        self._pass_until(session, lambda: not engine.state.stack)
        self.assertEqual(1, len(engine.state.damage_prevention_shields))
        shield = engine.state.damage_prevention_shields[0]
        self.assertEqual("combat", shield.damage_kind.value)
        self.assertEqual("all", shield.mode.value)

        expected_hash = authoritative_state_hash(engine.state)
        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / "knight-captain-prevention"
            session.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(expected_hash, replay["final_state_hash"])

        life_before = engine.state.players["A"].life
        damage = resolve_damage_batch(
            engine,
            (
                damage_proposal(
                    engine,
                    proposal_id="knight-captain-combat-witness",
                    actor="B",
                    source_ref=combat_source.ref,
                    target="A",
                    amount=3,
                    combat=True,
                    reason="Knight-Captain prevention witness",
                ),
            ),
        )
        self.assertEqual(0, damage.dealt_amount)
        self.assertEqual(3, damage.events[0].prevented_amount)
        self.assertEqual(life_before, engine.state.players["A"].life)

        self._resolve_registered_program(engine, token_program, 470120)
        self.assertEqual(
            soldiers_before + 2,
            sum(
                card.is_token and card.printed_name == "Soldier Token"
                for card in engine.state.cards.values()
            ),
        )

    def test_replacement_residuals_are_not_admitted_beside_trusted_token_trigger(
        self,
    ):
        record = self.db.lookup("Topography Tracker")
        ir = compile_oracle_card(
            record,
            capability_registry=self.capabilities,
            capability_profile="commander_review",
        )
        residual = next(
            row
            for row in ir.material_residuals
            if row.kind == "replacement_effect"
        )
        self.assertEqual(
            "replacement/prevention ordering is not compiled",
            residual.reason,
        )
        self.assertTrue(
            {
                "replacement applicability",
                "self-replacement and prevention ordering",
            }.issubset(residual.blockers)
        )

        session = self.session(
            470121,
            card_name="Topography Tracker",
        )
        engine = session.engine
        source = self.card(engine, "Topography Tracker")
        self._prepare_priority(session, source)
        programs = engine.semantics.programs_for_oracle(source.oracle_id)
        self.assertEqual(1, len(programs))
        self.assertEqual("trusted", programs[0].trust_level)
        self.assertEqual("create_token", programs[0].effects[0]["op"])
        self.assertFalse(programs[0].handlers)
        self.assertFalse(
            engine.semantics.runtime_handler_programs_for_oracle(
                source.oracle_id,
                active_zone="battlefield",
                event="replace",
            )
        )

        self._resolve_registered_program(engine, programs[0], 470121)
        maps = [
            card
            for card in engine.state.cards.values()
            if card.is_token and "map" in engine._type_parts(engine._effective_card_data(card)["type_line"])[1]
        ]
        self.assertEqual(1, len(maps))
        abilities = engine._effective_card_data(maps[0])["activated_abilities"]
        self.assertEqual(1, len(abilities))
        self.assertIn(
            "explores",
            abilities[0]["effect_text"],
        )


class FixedTokenCreatorRuntimeTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from quorune.carddb import CardDatabase
        from quorune.deck import DeckDefinition, DeckEntry
        from scripts.build_test_database import build_fixture_database

        cls.temporary = tempfile.TemporaryDirectory()
        database_path = Path(cls.temporary.name) / "fixed-token-creator.sqlite3"
        fixtures = ROOT / "tests" / "fixtures"
        build_fixture_database(
            [
                fixtures / "bound-effect-program-cards.json",
                fixtures / "fixed-token-creation-cards.json",
                fixtures / "additional-token-definition-cards.json",
                fixtures / "incubate-cards.json",
                fixtures / "nested-token-abilities-cards.json",
            ],
            database_path,
        )
        cls.db = CardDatabase(database_path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition(
            "Fixed token creator",
            [DeckEntry("Generic Bound Commander", 1, "commander"), DeckEntry("Generic Bound Plains", 30)],
            ["Generic Bound Commander"],
        )

    @staticmethod
    def tokens(session):
        return [card for card in session.state.cards.values() if card.is_token and card.zone == "battlefield"]

    def test_nested_token_death_grants_paid_shift_to_the_successor(self):
        game = self.session(7573122)
        original = self.add(game.engine, 'Generic Nesting Skimmer', ref='nesting-parent')
        land = self.add(game.engine, 'Forest', zone='hand', ref='nesting-land')
        removals = [
            self.add(game.engine, 'Lightning Bolt', seat='B', zone='hand', ref=f'nesting-removal-{index}')
            for index in range(2)
        ]
        self.assert_closed(game, original)
        self.prepare(game, mana={'U': 1})
        game.state.players['B'].mana_pool.update({'R': 2})
        game.state.config.semantic_policy = 'trusted_only'
        self.checkpoint(game)
        play = game.act('pilot:A', {'action_id': 'play-land:' + land.ref})
        self.assertTrue(play.ok, play.summary)
        self.assertIsNone(self.resolve(game))
        egg, = (obj for obj in game.state.cards.values()
               if obj.zone == 'battlefield' and obj.object_kind == 'token')
        self.assertEqual('A', egg.controller)
        self.assertEqual(1, game.engine._numeric_stat(egg.object_id, 'power'))
        self.assertEqual(2, game.engine._numeric_stat(egg.object_id, 'toughness'))
        self.assertIn('Defender', game.engine._effective_card_data(egg)['keywords'])
        self.priority(game, 'A')
        actions = game.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']
        self.assertFalse(any(row['id'].startswith('activate:' + egg.ref + ':') for row in actions))

        self.cast(game, 'B', removals[0], [original.ref])
        self.assertIsNone(self.resolve(game))
        self.assertEqual('graveyard', game.state.cards[original.object_id].zone)
        self.cast(game, 'B', removals[1], [egg.ref])
        self.resolve_top(game)
        trigger, = game.state.stack
        self.assertEqual(egg.object_id, trigger.source_object_id)
        self.assertTrue(all(obj.zone != 'battlefield'
                            for obj in game.state.cards.values() if obj.object_kind == 'token'))
        game = self.replay(game, load=True)
        self.assertEqual(egg.object_id, game.state.stack[-1].source_object_id)
        self.assertIsNone(self.resolve(game))

        successor, = (obj for obj in game.state.cards.values()
                   if obj.zone == 'battlefield' and obj.object_kind == 'token')
        self.assertEqual('A', successor.controller)
        self.assertNotEqual(egg.logical_object_id, successor.logical_object_id)
        self.assertEqual(2, game.engine._numeric_stat(successor.object_id, 'power'))
        self.assertEqual(3, game.engine._numeric_stat(successor.object_id, 'toughness'))
        self.assertIn('Flying', game.engine._effective_card_data(successor)['keywords'])
        self.priority(game, 'A')
        actions = game.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']
        activation, = (row for row in actions if row['id'].startswith('activate:' + successor.ref + ':ability:granted:'))
        command = {'action_id': activation['id'], 'pay': 'auto'}
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act('pilot:B', command).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        accepted = game.act('pilot:A', command)
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertEqual(0, game.state.players['A'].mana_pool.get('U', 0))
        self.assertIsNone(self.resolve(game))
        self.assertEqual(2, game.engine._numeric_stat(successor.object_id, 'power'))
        self.assertEqual(4, game.engine._numeric_stat(successor.object_id, 'toughness'))
        self.replay(game, load=True)

    def test_original_wanted_scoundrels_recipient_chooses_replacement_and_owns_tokens(self):
        session = self.session(711010)
        source = self.add(session.engine, "Wanted Scoundrels")
        bolt = self.add(session.engine, "Lightning Bolt", zone="hand")
        for seat in ("A", "B"):
            self.add(session.engine, "Worldwalker Helm", seat=seat, ref=f"{seat}-helm")
            self.add(session.engine, "Stridehangar Automaton", seat=seat, ref=f"{seat}-automaton")
        self.assert_closed(session, source)
        self.assert_closed(session, bolt)
        self.prepare(session, mana={"R": 1})
        self.checkpoint(session)
        self.cast(session, "A", bolt, [source.ref])

        target = self.resolve(session)
        self.assertEqual("semantic.target", target.kind)
        self.assertEqual(["A"], target.actors)
        self.assertEqual("graveyard", session.state.cards[source.object_id].zone)
        offer = session.packet("pilot:A", full=True)["decision"]["ctx"]["target_schema"]
        self.assertEqual({"B", "C", "D"}, set(offer["legal_refs"]))
        before = authoritative_state_hash(session.state)
        rejected = session.act("pilot:A", {"action_id": "choose", "targets": ["A"]})
        self.assertFalse(rejected.ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        self.target(session, "A", ["B"])

        replacement = self.resolve(session)
        self.assertEqual("replacement.order", replacement.kind)
        self.assertEqual(["B"], replacement.actors)
        self.assertEqual([], self.tokens(session))
        for seat in "ACD":
            self.assertIsNone(session.packet("pilot:" + seat, full=True)["decision"])
        session = self.replay(session, load=True)
        options = session.packet("pilot:B", full=True)["decision"]["ctx"]["options"]
        self.assertEqual(2, len(options))
        before = authoritative_state_hash(session.state)
        for principal, selection in (("pilot:A", options[0]["id"]), ("pilot:B", "stale-creator-choice")):
            rejected = session.act(principal, {"action_id": "choose", "replacement": selection})
            self.assertFalse(rejected.ok)
            self.assertEqual(before, authoritative_state_hash(session.state))

        accepted = session.act("pilot:B", {"action_id": "choose", "replacement": options[0]["id"]})
        self.assertTrue(accepted.ok, accepted.summary)
        self.settle(session)
        tokens = self.tokens(session)
        self.assertEqual(Counter({"Treasure Token": 2, "Map Token": 1, "Thopter Token": 1}), Counter(card.printed_name for card in tokens))
        self.assertEqual({("B", "B")}, {(card.owner, card.controller) for card in tokens})
        self.replay(session, load=True)

    def test_original_prismari_command_targeted_creation_can_select_self_and_skips_departed_target(self):
        for recipient in ("A", "B"):
            with self.subTest(recipient=recipient):
                session = self.session(711020 if recipient == "A" else 711021)
                command = self.add(session.engine, "Prismari Command", zone="hand")
                self.assert_closed(session, command)
                self.prepare(session, mana={"C": 1, "U": 1, "R": 1})
                self.checkpoint(session)
                action = self.action(session, "A", command)
                result = session.act("pilot:A", {
                    "action_id": action["id"], "modes": ["mode_1", "mode_3"],
                    "targets": ["C", recipient], "pay": "auto",
                })
                self.assertTrue(result.ok, result.summary)
                if recipient == "B":
                    self.priority(session, "B")
                    result = session.act("pilot:B", {
                        "action_id": "concede", "choices": {"confirm_concede": True},
                    })
                    self.assertTrue(result.ok, result.summary)
                self.assertIsNone(self.resolve(session))
                self.assertEqual(38, session.state.players["C"].life)
                self.assertEqual("graveyard", session.state.cards[command.object_id].zone)
                self.assertEqual(
                    [("Treasure Token", "A", "A")] if recipient == "A" else [],
                    [(card.printed_name, card.owner, card.controller) for card in self.tokens(session)],
                )
                self.replay(session, load=True)


class TokenCopyRecipeModelTests(unittest.TestCase):
    def test_copy_exception_mutation_cannot_discard_fixed_stats(self):
        import inspect
        from quorune import token_copy_recipes as owner
        source = inspect.getsource(owner.apply_copy_exception)
        expression = 'result["power"], result["toughness"] = str(exception.power), str(exception.toughness)'
        self.assertEqual(1, source.count(expression))
        namespace = dict(vars(owner))
        exec(source.replace(expression, 'result["power"], result["toughness"] = "99", "99"'), namespace)
        fixture = {"type_line":"Creature — Elf", "keywords":[], "ability_fragments":[]}
        exception = owner.TokenCopyExceptionSpec(power=1, toughness=1)
        def witness(apply):
            result = apply(fixture, exception)
            self.assertEqual(("1", "1"), (result["power"], result["toughness"]))
        witness(owner.apply_copy_exception)
        with self.assertRaises(AssertionError):
            witness(namespace["apply_copy_exception"])

    def test_populate_shared_spell_trigger_and_activation_compiler_closure(self):
        registry = load_default_capability_registry()
        cases = (("Populate.", "Instant"),
            ("{W}: Populate.", "Creature — Elf"),
            ("At the beginning of your upkeep, populate.", "Enchantment"),
            ("Create a 1/1 white Bird creature token with flying, then populate.", "Instant"))
        for text, type_line in cases:
            with self.subTest(text=text):
                ir = compile_oracle_card(token_record("Generic Populate", text, 25100812, type_line=type_line),
                    capability_registry=registry, capability_profile="commander_review")
                self.assertEqual("exact", ir.status, ir.material_residuals)
                self.assertTrue(any("token.creation.fixed_copy" in node.capability_dependencies for face in ir.faces for node in face.nodes))

    def test_populate_reuses_choice_owner_without_legacy_haste_or_cleanup(self):
        from types import SimpleNamespace
        from quorune.semantic_choices.token_and_copy import TOKEN_AND_COPY_CHOICE_HANDLERS
        from quorune.replacement.immutable import FrozenMap
        handler = next(value for value in TOKEN_AND_COPY_CHOICE_HANDLERS if value.operation == "populate")
        row = SimpleNamespace(ref="TOKEN", logical_object_id="token:1", token=True, controller="A", types=("creature",), printed_name="Generic Token")
        query = SimpleNamespace(object=lambda ref, zones:row)
        continuation = SimpleNamespace(effect=FrozenMap({"_choice_actor":"A", "_legal_refs":["TOKEN"], "_stack_label":"Populate", "_object_identities":{"TOKEN":"token:1"}}), stack_ref="STACK")
        intent = handler.complete(continuation, {"card":"TOKEN"}, query).intents[0]
        self.assertEqual("TOKEN", intent.copy_of)
        self.assertEqual((), intent.temporary_keywords)
        self.assertFalse(intent.sacrifice_at_end_step)
        legacy = next(value for value in TOKEN_AND_COPY_CHOICE_HANDLERS if value.operation == "populate_with_haste")
        old = legacy.complete(continuation, {"card":"TOKEN"}, query).intents[0]
        self.assertEqual(("Haste",), old.temporary_keywords)
        self.assertTrue(old.sacrifice_at_end_step)
        row.logical_object_id = "token:2"
        with self.assertRaisesRegex(ValueError, "incarnation changed"):
            handler.complete(continuation, {"card":"TOKEN"}, query)
        empty_context = SimpleNamespace(actor="A", query=SimpleNamespace(objects=lambda **kw:()))
        empty = handler.prepare({"op":"populate"}, empty_context)
        self.assertIsNone(empty.request)
        self.assertIsNotNone(empty.auto_continue)

    def test_copy_exception_granted_cleanup_is_an_ordinary_copiable_trigger(self):
        registry = load_default_capability_registry()
        for verb in ("sacrifice", "exile"):
            text = 'Create a token that\'s a copy of target creature, except it has haste and "At the beginning of the end step, ' + verb + ' this token."'
            ir = compile_oracle_card(token_record("Generic Copy Grant", text, 25100811),
                capability_registry=registry, capability_profile="commander_review")
            self.assertEqual("exact", ir.status, ir.material_residuals)
            outer, child = ir.faces[0].nodes
            self.assertEqual("granted_triggered_ability", child.kind)
            recipe = outer.effects[0]["copy_spec"]
            self.assertEqual("none", recipe["cleanup"])
            self.assertEqual("granted_triggered", recipe["exception"]["add_ability_fragments"][0]["kind"])
            self.assertEqual("$source.zone_object", child.effects[0]["card"])
        separate = 'Create a token that\'s a copy of target creature. It has "When this token dies, draw a card."'
        ir = compile_oracle_card(token_record("Generic Separate Copy Grant", separate, 25100813),
            capability_registry=registry, capability_profile="commander_review")
        self.assertNotEqual("exact", ir.status)

    def test_copy_recipe_compiler_capability_contract_matrix(self):
        from quorune.compiler.token_copy_templates import token_copy_recipe_template
        from quorune.rules.token_creation_capability_shapes import fixed_token_creation_node_capabilities
        cases = (
            ("Create a token that's a copy of target artifact.", "token.creation.fixed_copy"),
            ("Create a token that's a copy of target non-Frog creature, except it's a 1/1 green Frog.", "target.permanent.characteristic_predicate"),
            ("Create two tokens that are copies of this creature.", "token.creation.fixed_copy"),
            ("Create a token that's a copy of target creature you control, except it has haste.", "combat.attack.haste"),
        )
        for text, required in cases:
            with self.subTest(text=text):
                template = token_copy_recipe_template(text, source_name="Generic Source", source_is_permanent=True)
                capabilities = fixed_token_creation_node_capabilities(effects=(template.effect,),
                    target_schema=template.target_schema, mechanic_ids=set(template.mechanics))
                self.assertIn(required, capabilities)
                for quantity in (True, 0, 6):
                    self.assertEqual((), fixed_token_creation_node_capabilities(
                        effects=({**template.effect, "quantity":quantity},), target_schema=template.target_schema,
                        mechanic_ids=set(template.mechanics)))

    def test_fixed_subtype_override_removes_its_definition_not_other_abilities(self):
        from quorune.ability_fragments import AllCreatureTypesCharacteristicDefinitionSpec, ColorlessCharacteristicDefinitionSpec, ability_fragment_to_dict
        from quorune.token_copy_recipes import TokenCopyExceptionSpec, apply_copy_exception
        source = {"type_line":"Creature — Shapeshifter", "colors":[], "keywords":["Changeling", "Devoid", "Flying"],
            "ability_fragments":[ability_fragment_to_dict(AllCreatureTypesCharacteristicDefinitionSpec()),
                                 ability_fragment_to_dict(ColorlessCharacteristicDefinitionSpec())]}
        changed = apply_copy_exception(source, TokenCopyExceptionSpec(creature_subtypes=("frog",), colors=("G",)))
        self.assertEqual("Creature — Frog", changed["type_line"])
        self.assertEqual([], changed["ability_fragments"])
        self.assertEqual(["Flying"], changed["keywords"])
        additive = apply_copy_exception(source, TokenCopyExceptionSpec(add_subtypes=("frog",)))
        self.assertEqual(source["ability_fragments"], additive["ability_fragments"])

    def test_departure_pinner_reads_only_matching_incarnations(self):
        from types import SimpleNamespace
        from unittest.mock import patch
        from quorune.token_copy_recipes import TokenCopyRecipeSpec
        from quorune.token_copy_references import COPY_REFERENCE_CONTEXT, pin_copy_characteristic_departures
        source = SimpleNamespace(object_id="source", logical_object_id="source:1", ref="SOURCE",
            zone="battlefield", phased_out=True)
        other = SimpleNamespace(object_id="other", logical_object_id="other:1", ref="OTHER",
            zone="battlefield", phased_out=False)
        items = []
        for card in (source, other):
            items.append(SimpleNamespace(ref=f"stack:{card.ref}", source_object_id=card.object_id, card_object_id=None,
                semantic_key="recipe", context={"source_logical_object_id": card.logical_object_id}))
        effect = {"op": "create_token", "copy_spec": TokenCopyRecipeSpec(origin="source").to_dict()}
        host = SimpleNamespace(state=SimpleNamespace(cards={c.object_id:c for c in (source, other)}, stack=items,
                                                     pending_trigger_batches=[]),
            semantics=SimpleNamespace(get=lambda key:SimpleNamespace(effects=(effect,))))
        with patch("quorune.token_copy_references._snapshot", side_effect=lambda h,c:{"logical_object_id":c.logical_object_id}) as read:
            pin_copy_characteristic_departures(host, [])
            read.assert_not_called()
            pin_copy_characteristic_departures(host, [other])
            self.assertEqual([other], [call.args[1] for call in read.call_args_list])
        self.assertNotIn(COPY_REFERENCE_CONTEXT, items[0].context)
        self.assertEqual("other:1", items[1].context[COPY_REFERENCE_CONTEXT]["source"]["logical_object_id"])
        source.logical_object_id = "source:2"
        with patch("quorune.token_copy_references._snapshot") as read:
            pin_copy_characteristic_departures(host, [source])
            read.assert_not_called()

    def test_copy_reference_scanning_visits_payments_not_future_ability_metadata(self):
        from quorune.token_copy_recipes import TokenCopyRecipeSpec
        from quorune.token_copy_references import copy_recipes
        recipe = TokenCopyRecipeSpec(origin="source")
        instruction = {"op":"create_token", "copy_spec":recipe.to_dict()}
        self.assertEqual((recipe,), copy_recipes(({"op":"offer_optional_mana_payment", "effects":[instruction]},)))
        self.assertEqual((), copy_recipes(({"op":"create_token", "characteristics":{"effects":[instruction]}},)))
        from quorune.resolution_conditions import CONDITIONAL_COPY_REFERENCE_SCHEMA_VERSION
        conditional = {"op": "apply_if_public_condition", "schema_version": CONDITIONAL_COPY_REFERENCE_SCHEMA_VERSION,
                       "effects": [instruction], "otherwise_effects": []}
        self.assertEqual((recipe,), copy_recipes((conditional,)))
        for version in (3, 13, True, float(CONDITIONAL_COPY_REFERENCE_SCHEMA_VERSION)):
            self.assertEqual((), copy_recipes(({**conditional, "schema_version": version},)))
        self.assertEqual((), copy_recipes(({"op": "create_token", "characteristics": {"effects": [conditional]}},)))

    def test_copy_references_read_current_then_immediate_lki_not_reentry(self):
        from types import SimpleNamespace
        from unittest.mock import patch
        from quorune.token_copy_recipes import TokenCopyRecipeSpec
        from quorune.token_copy_references import copy_reference_snapshot, pin_copy_characteristic_departures
        for origin in ("source", "target", "event_object"):
            with self.subTest(origin=origin):
                card = SimpleNamespace(object_id="object", logical_object_id="object:1", ref="OBJECT",
                    zone="battlefield", zone_change_counter=0, phased_out=False)
                recipe = TokenCopyRecipeSpec(origin=origin)
                context = {"source_logical_object_id":"object:1", "target_snapshots":{"OBJECT":{"zone_change_counter":0}},
                    "event_context":{"card":"OBJECT", "card_object_identity":"object:1"}}
                item = SimpleNamespace(ref="stack:copy-reference", source_object_id="object", card_object_id=None,
                                       targets=["OBJECT"], context=context, semantic_key="recipe")
                host = SimpleNamespace(state=SimpleNamespace(cards={"object":card}, stack=[item], pending_trigger_batches=[]),
                    semantics=SimpleNamespace(get=lambda key:SimpleNamespace(effects=({"op":"create_token", "copy_spec":recipe.to_dict()},))))
                def snapshot(h, c):
                    return {"object_id":c.object_id, "logical_object_id":c.logical_object_id, "ref":c.ref,
                        "zone":c.zone, "snapshot":{"power":"7"}}
                with patch("quorune.token_copy_references._snapshot", side_effect=snapshot):
                    self.assertEqual("7", copy_reference_snapshot(host, item, recipe)["snapshot"]["power"])
                    pin_copy_characteristic_departures(host, [card])
                card.logical_object_id = "object:2"
                card.zone_change_counter = 1
                with patch("quorune.token_copy_references._snapshot") as current:
                    self.assertEqual("7", copy_reference_snapshot(host, item, recipe)["snapshot"]["power"])
                    pin_copy_characteristic_departures(host, [card])
                    current.assert_not_called()

    def test_copy_recipe_grammar_retains_reference_exception_and_lifecycle(self):
        from quorune.compiler.token_copy_templates import token_copy_recipe_template
        cases = (
            ("Create a token that's a copy of target artifact.", "target", "none"),
            ("Create a token that's a copy of this creature.", "source", "none"),
            ("Create a token that's a copy of target non-Frog creature, except it's a 1/1 green Frog.", "target", "none"),
            ("Create a token that's a copy of target creature you control, except it isn't legendary. It gains haste. Sacrifice it at the beginning of the next end step.", "target", "sacrifice_next_end_step"),
            ("Create a token that's a copy of another target creature you control, except it's an artifact in addition to its other types.", "target", "none"),
        )
        for text, origin, cleanup in cases:
            with self.subTest(text=text):
                compiled = token_copy_recipe_template(text, source_name="Generic Source", source_is_permanent=True)
                self.assertIsNotNone(compiled)
                recipe = compiled.effect["copy_spec"]
                self.assertEqual(origin, recipe["origin"])
                self.assertEqual(cleanup, recipe["cleanup"])
        for text in (
            "Create a token that's a copy of the chosen creature.",
            "Create a token that's a copy of target creature, except it has an unrepresented ability.",
            "Create a token that's a copy of target creature. If it dies, draw a card.",
            "Create a token that's a copy of target creature, except it enters with X counters.",
        ):
            self.assertIsNone(token_copy_recipe_template(text, source_name="Generic Source", source_is_permanent=True))
        for suffix, duration in (("It gains haste.", "zone_object"), ("It gains haste until end of turn.", "until_end_of_turn")):
            compiled = token_copy_recipe_template("Create a token that's a copy of target creature. " + suffix,
                source_name="Generic Source", source_is_permanent=True)
            self.assertEqual(duration, compiled.effect["copy_spec"]["keyword_duration"])
            self.assertEqual([], compiled.effect["copy_spec"]["exception"]["add_keywords"])

    def test_fixed_copy_exception_is_copiable_without_inheriting_source_status(self):
        from quorune.token_copy_recipes import TokenCopyExceptionSpec, apply_copy_exception
        source = {"name": "Generic Original", "type_line": "Legendary Artifact Creature — Elf",
                  "colors": ["U"], "power": "4", "toughness": "5", "keywords": ["Flying"],
                  "ability_fragments": []}
        exception = TokenCopyExceptionSpec(remove_legendary=True, power=1, toughness=1,
            colors=("G",), creature_subtypes=("frog",), add_keywords=("Haste",))
        result = apply_copy_exception(source, exception)
        self.assertEqual("Artifact Creature — Frog", result["type_line"])
        self.assertEqual(("1", "1", ["G"]), (result["power"], result["toughness"], result["colors"]))
        self.assertEqual(["Flying", "Haste"], result["keywords"])
        self.assertEqual("Legendary Artifact Creature — Elf", source["type_line"])

    def test_fixed_base_override_removes_only_its_characteristic_definition(self):
        from quorune.token_copy_recipes import TokenCopyExceptionSpec, apply_copy_exception
        from quorune.characteristic_fragments import CharacteristicQuantityScope, CharacteristicQuantitySpec, QueryPowerToughnessDefinitionSpec
        from quorune.object_predicate import ObjectQuerySpec
        from quorune.ability_fragments import ability_fragment_to_dict
        definition = ability_fragment_to_dict(QueryPowerToughnessDefinitionSpec(
            quantity=CharacteristicQuantitySpec(scope=CharacteristicQuantityScope.CONTROLLER_ZONE,
                query=ObjectQuerySpec(zones=("battlefield",), types_all=("artifact",))),
            define_power=True, define_toughness=True))
        source = {"type_line": "Creature — Elf", "power": "*", "toughness": "*", "colors": [],
                  "keywords": ["Flying"], "ability_fragments": [definition]}
        result = apply_copy_exception(source, TokenCopyExceptionSpec(power=7, toughness=7))
        self.assertEqual([], result["ability_fragments"])
        self.assertEqual(["Flying"], result["keywords"])
        additive = apply_copy_exception(source, TokenCopyExceptionSpec(add_card_types=("artifact",)))
        self.assertEqual([definition], additive["ability_fragments"])

    def test_signed_base_copy_outcome_keeps_negative_copiable_power_and_rejects_other_shapes(self):
        from quorune.token_copy_recipes import (SIGNED_COPY_BASE_STATS_VERSION, TokenCopyExceptionSpec,
            TokenCopyRecipeSpec, apply_copy_exception)

        version = SIGNED_COPY_BASE_STATS_VERSION
        exception = TokenCopyExceptionSpec(power=-1, toughness=3, schema_version=version)
        raw = exception.to_dict()
        self.assertEqual(exception, TokenCopyExceptionSpec.from_dict(raw))
        original = {'name': 'Original', 'type_line': 'Creature — Elf', 'power': '2', 'toughness': '5',
                    'keywords': ['Flying'], 'ability_fragments': []}
        copied = apply_copy_exception(original, exception)
        self.assertEqual(('-1', '3', ['Flying']), (copied['power'], copied['toughness'], copied['keywords']))
        self.assertEqual(('2', '5'), (original['power'], original['toughness']))
        for malformed in ({**raw, 'schema_version': 1}, {**raw, 'power': False},
                          {**raw, 'remove_legendary': True}, {**raw, 'toughness': None}):
            with self.assertRaises(ValueError):
                TokenCopyExceptionSpec.from_dict(malformed)
        with self.assertRaises(ValueError):
            TokenCopyRecipeSpec(origin='target', exception=exception)

    def test_copy_recipe_codec_rejects_unknown_fields_booleans_and_open_values(self):
        from quorune.token_copy_recipes import TokenCopyExceptionSpec, TokenCopyRecipeSpec
        valid = TokenCopyRecipeSpec(origin="target", exception=TokenCopyExceptionSpec(remove_legendary=True)).to_dict()
        self.assertEqual(valid, TokenCopyRecipeSpec.from_dict(valid).to_dict())
        for bad in ({**valid, "extra": 1}, {**valid, "origin": "secret"},
                    {**valid, "cleanup": "repeat_every_end_step"}):
            with self.assertRaises(ValueError):
                TokenCopyRecipeSpec.from_dict(bad)
        for kwargs in ({"power": True, "toughness": 1}, {"power": 1},
                       {"creature_subtypes": ("unrepresented-type",)}, {"add_keywords": ("unknown",)}):
            with self.assertRaises(ValueError):
                TokenCopyExceptionSpec(**kwargs)


class TokenCopyCopiableBoundaryActionTests(unittest.TestCase):
    """CR 707.2: replacement subjects describe the copy, not layer-4 animation."""

    from test_linked_exile_return import LinkedExileReturnActionTests as _helpers
    session = _helpers.session
    add = _helpers.add
    seal = _helpers.seal
    cast = _helpers.cast
    finish = _helpers.finish
    replay = _helpers.replay
    def advance(self, session, phase, step):
        for _ in range(120):
            if (session.state.phase, session.state.step) == (phase, step):
                return
            session.engine.pump()
            principal = session.pending_principals()[0]
            decision = session.packet(principal, full=True)["decision"]
            action = ({"action_id": "pass"} if decision["kind"] == "priority"
                      else {"action_id": "attack", "attackers": []} if decision["kind"] == "combat.attackers"
                      else {"action_id": "block", "blocks": []} if decision["kind"] == "combat.blockers" else None)
            self.assertIsNotNone(action, decision["kind"])
            result = session.act(principal, action)
            self.assertTrue(result.ok, result.summary)
        self.fail("Copy recipe game did not reach the expected phase")

    @classmethod
    def setUpClass(cls):
        from quorune.carddb import CardDatabase
        from quorune.deck import DeckDefinition, DeckEntry
        from scripts.build_test_database import build_fixture_database
        cls.temporary = tempfile.TemporaryDirectory()
        root = Path(__file__).resolve().parents[1]
        path = Path(cls.temporary.name) / "copy-boundary.sqlite3"
        build_fixture_database([
            root / "tests/fixtures/linked-exile-return-cards.json",
            root / "tests/fixtures/token-copy-boundary-cards.json",
            root / "tests/fixtures/token-copy-recipe-cards.json",
        ], path)
        cls.db = CardDatabase(path)
        cls.deck = DeckDefinition("Generic copy boundary", [
            DeckEntry("Generic Blink Commander", 1, "commander"),
            DeckEntry("Generic Blink Plains", 15),
        ], ["Generic Blink Commander"])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    def register(self, session):
        records = tuple(card for card in self.db.iter_cards() if card.oracle_text)
        registry = load_default_capability_registry()
        for record in records:
            ir = compile_oracle_card(record, capability_registry=registry,
                                     capability_profile="commander_review")
            self.assertEqual("exact", ir.status, (record.name, ir.material_residuals))
        register_generated_programs(
            self.db, session.engine.semantics, records,
            trust_level="provisional", capability_registry=registry,
            capability_profile="commander_review",
            promote_exact_runtime_handlers=True, promote_exact_trigger_programs=True,
            promote_exact_effect_programs=True, promote_exact_capability_declarations=True,
        )
        for record in records:
            programs = session.engine.semantics.programs_for_oracle(record.oracle_id)
            self.assertTrue(programs, record.name)
            self.assertTrue(all(session.engine.semantic_program_is_current_trusted(p)
                                for p in programs), record.name)

    def test_copy_recipe_with_unsupported_replacement_fails_closed_at_runtime_admission(self):
        from high_risk_interaction_support import CONDITIONAL_DRAW_STEP_REPLACEMENT
        from quorune.card_programs import bind_card_program_runtime
        from quorune.card_programs.adapters import compile_best_available_card_program
        from quorune.compiler.unlock_frontier import analyze_card_unlocks
        record = token_record("Generic Copy Replacement Boundary",
            f"{CONDITIONAL_DRAW_STEP_REPLACEMENT}\n{{W}}: Create a token that's a copy of target creature you control.",
            25100814, type_line="Artifact")
        registry = load_default_capability_registry()
        ir = compile_oracle_card(record, capability_registry=registry, capability_profile="commander_review")
        self.assertTrue(any(node.exact and "token.creation.fixed_copy" in node.capability_dependencies
                            for face in ir.faces for node in face.nodes))
        program = compile_best_available_card_program(self.db, record, semantic_registry=SemanticRegistry(),
            capability_registry=registry, capability_profile="commander_review")
        frontier = analyze_card_unlocks(ir, program=program, program_error=None, capabilities=registry, profile="commander_review")
        self.assertEqual("residual", frontier["card_program_status"])
        self.assertTrue({"replacement:replacement-applicability", "replacement:self-replacement-and-prevention-ordering"}
                        <= set(frontier["minimum_known_blocker_set"]))
        binding = bind_card_program_runtime(program, capability_registry=registry, profile="commander_review")
        self.assertFalse(binding["strict_capability_ready"])
        self.assertFalse(binding["compatible_ready"])
        self.assertIn("trust_basis:unresolved", binding["blockers"])

    def copy_after_animation(self, *, seed, subject_name, expected_tokens,
                             current_artifact, copied_artifact, animation_spell_name=None):
        session = self.session(seed)
        self.register(session)
        source = self.add(session, subject_name, "SUBJECT", zone="battlefield")
        self.add(session, "Generic Artifact Token Map Bonus", "BONUS", zone="battlefield")
        spell = self.add(session, "Generic Copy Creature Spell", "COPY")
        animation = self.add(session, animation_spell_name, "ANIMATION") if animation_spell_name else None
        self.seal(session)
        if animation is not None:
            self.cast(session, animation, [source.ref])
        else:
            action = next(row for row in session.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
                          if row.get("source") == source.ref and row["id"].startswith("activate:"))
            result = session.act("pilot:A", {"action_id": action["id"], "pay": "auto"})
            self.assertTrue(result.ok, result.summary)
        self.finish(session)
        current = session.engine._type_parts(session.engine._effective_card_data(source)["type_line"])[0]
        self.assertIn("creature", current)
        self.assertEqual(current_artifact, "artifact" in current)
        self.assertEqual(copied_artifact, "artifact" in session.engine._type_parts(
            session.engine._copyable_characteristics(source)["type_line"])[0])
        self.cast(session, spell, [source.ref])
        self.finish(session)
        tokens = [card for card in session.state.cards.values()
                  if card.is_token and card.zone == "battlefield"]
        copies = [card for card in tokens if card.annotations.get("copied_from") == source.object_id]
        self.assertEqual(1, len(copies))
        copied_types = session.engine._type_parts(session.engine._effective_card_data(copies[0])["type_line"])[0]
        self.assertEqual(copied_artifact, "artifact" in copied_types)
        self.assertEqual(expected_tokens, len(tokens),
                         "Artifact-only additional tokens follow the copy's copiable type")
        for seat in "BCD":
            self.assertIsNone(session.packet(f"pilot:{seat}", full=True)["decision"])
        self.replay(session)

    def test_actual_copy_of_nonartifact_animation_retains_artifact_replacement_and_replays(self):
        self.copy_after_animation(seed=25000701, subject_name="Generic Copiable Artifact",
            expected_tokens=2, current_artifact=False, copied_artifact=True)

    def test_actual_copy_does_not_inherit_temporary_artifact_replacement_and_replays(self):
        self.copy_after_animation(seed=25000702, subject_name="Generic Copiable Creature",
            expected_tokens=1, current_artifact=True, copied_artifact=False,
            animation_spell_name="Generic Artifact Creature Animation")

    def test_trusted_recipe_fixed_characteristics_exclusion_rollback_and_replay(self):
        session = self.session(25100801)
        self.register(session)
        original = self.add(session, "Generic Recipe Original", "ORIGINAL", zone="battlefield")
        illegal = self.add(session, "Generic Copiable Creature", "FROG", zone="battlefield")
        spell = self.add(session, "Generic Recipe Fixed Frog", "COPY")
        self.seal(session)
        action = next(row for row in session.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
                      if row["id"] == "cast:" + spell.ref)
        self.assertIn(original.ref, action["target_schema"]["legal_refs"])
        self.assertNotIn(illegal.ref, action["target_schema"]["legal_refs"])
        before = authoritative_state_hash(session.state)
        bad = session.act("pilot:A", {"action_id": action["id"], "targets": [illegal.ref], "pay": "auto"})
        self.assertFalse(bad.ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        self.cast(session, spell, [original.ref])
        self.finish(session)
        copies = [card for card in session.state.cards.values() if card.is_token and card.zone == "battlefield"]
        self.assertEqual(1, len(copies))
        data = session.engine._effective_card_data(copies[0])
        self.assertEqual("Artifact Creature — Frog", data["type_line"])
        self.assertEqual(("1", "1", ["G"]), (data["power"], data["toughness"], data["colors"]))
        self.assertIn("Flying", data["keywords"])
        self.replay(session)

    def test_trusted_recipe_self_sacrifice_uses_immediate_copy_lki_and_replays(self):
        session = self.session(25100802)
        self.register(session)
        source = self.add(session, "Generic Recipe Self Copy", "SOURCE", zone="battlefield")
        self.seal(session)
        action = next(row for row in session.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
                      if row.get("source") == source.ref and row["id"].startswith("activate:"))
        result = session.act("pilot:A", {"action_id": action["id"], "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("graveyard", session.state.cards[source.object_id].zone)
        self.finish(session)
        copies = [card for card in session.state.cards.values() if card.is_token and card.zone == "battlefield"]
        self.assertEqual(1, len(copies))
        self.assertEqual((2, 3), (session.engine._numeric_stat(copies[0].object_id, "power"),
                                 session.engine._numeric_stat(copies[0].object_id, "toughness")))
        self.replay(session)

    def test_trusted_recipe_entry_event_copies_new_incarnation_and_replays(self):
        session = self.session(25100804)
        self.register(session)
        self.add(session, "Generic Recipe Entry Observer", "OBSERVER", zone="battlefield")
        original = self.add(session, "Generic Copiable Creature", "ORIGINAL")
        self.seal(session)
        previous_identity = original.logical_object_id
        self.cast(session, original, [])
        self.finish(session)
        self.assertNotEqual(previous_identity, original.logical_object_id)
        tokens = [card for card in session.state.cards.values() if card.is_token and card.zone == "battlefield"]
        self.assertEqual(1, len(tokens))
        self.assertEqual(original.object_id, tokens[0].annotations["copied_from"])
        self.assertEqual(session.engine._copyable_characteristics(original), session.engine._copyable_characteristics(tokens[0]))
        self.replay(session)

    def test_trusted_recipe_copied_cleanup_ability_triggers_and_replays(self):
        session = self.session(25100805)
        self.register(session)
        original = self.add(session, "Generic Copiable Creature", "ORIGINAL", zone="battlefield")
        spell = self.add(session, "Generic Recipe Copiable Cleanup", "COPY")
        self.seal(session)
        self.cast(session, spell, [original.ref])
        self.finish(session)
        tokens = [card for card in session.state.cards.values() if card.is_token and card.zone == "battlefield"]
        self.assertEqual(1, len(tokens))
        self.assertIn("Haste", session.engine._copyable_characteristics(tokens[0])["keywords"])
        self.assertTrue(session.engine._copyable_characteristics(tokens[0])["ability_fragments"])
        self.assertFalse(any(trigger.label == "Copied token cleanup" for trigger in session.state.delayed_triggers))
        self.advance(session, "ending", "end_step")
        self.finish(session)
        self.assertEqual("outside", session.state.cards[tokens[0].object_id].zone)
        self.replay(session)

    def test_trusted_populate_selects_controlled_creature_token_and_replays_choice(self):
        from quorune.session import CommanderSession
        session = self.session(25100806)
        self.register(session)
        original = self.add(session, "Generic Copiable Creature", "ORIGINAL", zone="battlefield")
        copied_ref = session.engine.create_token("A", name="", copy_of=original.ref, reason="generic Populate starting state")[0]
        opponent_ref = session.engine.create_token("B", name="", copy_of=original.ref, reason="opponent token starting state")[0]
        noncreature_ref = session.engine.create_token("A", name="Clue", reason="noncreature token starting state")[0]
        spell = self.add(session, "Generic Recipe Populate", "POPULATE")
        self.seal(session)
        self.cast(session, spell, [])
        for _ in range(12):
            principal = session.pending_principals()[0]
            decision = session.packet(principal, full=True)["decision"]
            if decision["kind"] != "priority":
                break
            self.assertTrue(session.act(principal, {"action_id":"pass"}).ok)
        self.assertEqual("populate", decision["ctx"]["operation"])
        self.assertEqual([copied_ref], decision["ctx"]["options"])
        for seat in "BCD":
            self.assertIsNone(session.packet(f"pilot:{seat}", full=True)["decision"])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "choice"
            session.save(path)
            restored = CommanderSession.load(self.db, path)
        self.assertEqual(authoritative_state_hash(session.state), authoritative_state_hash(restored.state))
        for ref in (original.ref, opponent_ref, noncreature_ref, "missing"):
            before = authoritative_state_hash(restored.state)
            result = restored.act("pilot:A", {"action_id":"choose", "card":ref})
            self.assertFalse(result.ok)
            self.assertEqual(before, authoritative_state_hash(restored.state))
        self.assertTrue(restored.act("pilot:A", {"action_id":"choose", "card":copied_ref}).ok)
        self.finish(restored)
        copies = [card for card in restored.state.cards.values() if card.is_token and card.controller == "A"
                  and "creature" in restored.engine._type_parts(restored.engine._effective_card_data(card)["type_line"])[0]]
        self.assertEqual(2, len(copies))
        self.assertFalse(any(card.temporary_keywords for card in copies))
        self.assertFalse(any(trigger.active for trigger in restored.state.delayed_triggers))
        self.replay(restored)

    def test_trusted_recipe_group_cleanup_is_incarnation_and_controller_limited(self):
        session = self.session(25100803)
        self.register(session)
        original = self.add(session, "Generic Recipe Original", "ORIGINAL", zone="battlefield")
        spell = self.add(session, "Generic Recipe Temporary Copies", "COPIES")
        self.seal(session)
        self.cast(session, spell, [original.ref])
        self.finish(session)
        tokens = [card for card in session.state.cards.values() if card.is_token and card.zone == "battlefield"]
        self.assertEqual(2, len(tokens))
        self.assertTrue(all("haste" in session.engine._combat_keywords(card) for card in tokens))
        self.assertTrue(all("Haste" not in session.engine._copyable_characteristics(card)["keywords"] for card in tokens))
        self.assertTrue(all(effect.duration.value == "zone_object" for effect in session.state.continuous_effects))
        self.assertEqual(1, sum(trigger.active and trigger.label == "Copied token cleanup" for trigger in session.state.delayed_triggers))
        self.replay(session)
        session.engine.change_control(tokens[0].object_id, "B", reason="bounded cleanup controller diagnostic")
        old_identity = tokens[1].logical_object_id
        session.engine.move_card(tokens[1].object_id, "exile", semantic_events=True)
        self.assertNotEqual(old_identity, tokens[1].logical_object_id)
        self.advance(session, "ending", "end_step")
        self.finish(session)
        self.assertEqual("battlefield", session.state.cards[tokens[0].object_id].zone)
        self.assertEqual("B", session.state.cards[tokens[0].object_id].controller)


    def test_trusted_populate_known_empty_finishes_without_choice_and_replays(self):
        session = self.session(25100807)
        self.register(session)
        self.add(session, "Generic Copiable Creature", "NONTOKEN", zone="battlefield")
        spell = self.add(session, "Generic Recipe Populate", "POPULATE")
        self.seal(session)
        self.cast(session, spell, [])
        self.finish(session)
        self.assertFalse(any(card.is_token for card in session.state.cards.values()))
        self.assertEqual("graveyard", session.state.cards[spell.object_id].zone)
        self.replay(session)

    def test_copy_replacement_choice_preserves_creator_projection_rollback_and_checkpoint(self):
        from quorune.session import CommanderSession
        session = self.session(25000703)
        self.register(session)
        source = self.add(session, "Generic Copiable Artifact Creature", "SUBJECT", owner="B", zone="battlefield")
        self.add(session, "Generic Artifact Token Map Bonus", "MAP", zone="battlefield")
        self.add(session, "Generic Artifact Token Food Bonus", "FOOD", zone="battlefield")
        self.add(session, "Generic Artifact Token Map Bonus", "OTHER-SEAT", owner="B", zone="battlefield")
        spell = self.add(session, "Generic Copy Creature Spell", "COPY")
        self.seal(session)
        before = authoritative_state_hash(session.state)
        rejected = session.act("pilot:A", {"action_id": "cast:" + spell.ref,
                                          "targets": ["missing"], "pay": "auto"})
        self.assertFalse(rejected.ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        self.cast(session, spell, [source.ref])
        for _ in range(12):
            decision = session.packet("pilot:A", full=True)["decision"]
            if decision and decision["kind"] == "replacement.order":
                break
            principal = session.pending_principals()[0]
            result = session.act(principal, {"action_id": "pass"})
            self.assertTrue(result.ok, result.summary)
        else:
            self.fail("Copy creation did not expose competing artifact replacements")
        self.assertFalse(any(card.is_token for card in session.state.cards.values()),
                         "No token is committed before replacement selection")
        for seat in "BCD":
            self.assertIsNone(session.packet(f"pilot:{seat}", full=True)["decision"])
        packet = session.packet("pilot:A", full=True)["decision"]
        self.assertNotIn("replacement_effects", json.dumps(packet))
        self.assertNotIn("replacement_batch", json.dumps(packet))
        with tempfile.TemporaryDirectory() as directory:
            session.save(Path(directory) / "choice")
            restored = CommanderSession.load(self.db, Path(directory) / "choice")
        self.assertEqual(authoritative_state_hash(session.state), authoritative_state_hash(restored.state))
        before = authoritative_state_hash(restored.state)
        rejected = restored.act("pilot:A", {"action_id": "choose", "replacement": "unknown"})
        self.assertFalse(rejected.ok)
        self.assertEqual(before, authoritative_state_hash(restored.state))
        option = restored.packet("pilot:A", full=True)["decision"]["ctx"]["options"][0]["id"]
        result = restored.act("pilot:A", {"action_id": "choose", "replacement": option})
        self.assertTrue(result.ok, result.summary)
        self.finish(restored)
        tokens = [card for card in restored.state.cards.values() if card.is_token and card.zone == "battlefield"]
        self.assertEqual(3, len(tokens))
        self.assertTrue(all(card.owner == "A" and card.controller == "A" for card in tokens))
        self.assertEqual(1, sum(card.annotations.get("copied_from") == source.object_id for card in tokens))
        self.replay(restored)
        archive = getattr(self, "archive_output", None)
        if archive is not None:
            restored.save(archive)


class PostEntryCopyGrantTests(unittest.TestCase):
    from test_linked_exile_return import LinkedExileReturnActionTests as _helpers

    session = _helpers.session
    add = _helpers.add
    seal = _helpers.seal
    cast = _helpers.cast
    finish = _helpers.finish
    replay = _helpers.replay
    advance = TokenCopyCopiableBoundaryActionTests.advance

    @classmethod
    def setUpClass(cls):
        from quorune.carddb import CardDatabase
        from quorune.deck import DeckDefinition, DeckEntry
        from scripts.build_test_database import build_fixture_database

        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / "post-entry-copy.sqlite3"
        build_fixture_database([
            ROOT / "tests/fixtures/linked-exile-return-cards.json",
            ROOT / "tests/fixtures/token-copy-recipe-cards.json",
            ROOT / "tests/fixtures/token-copy-boundary-cards.json",
            ROOT / "tests/fixtures/targeted-destroy-hasty-token-cleanup-cards.json",
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Post-entry copy grants", [
            DeckEntry("Generic Blink Commander", 1, "commander"),
            DeckEntry("Generic Blink Plains", 15),
        ], ["Generic Blink Commander"])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    def create_session(self, seed, *names):
        session = self.session(seed)
        session.state.players["A"].mana_pool.update({"R": 8, "W": 4})
        records = tuple(self.db.lookup(name) for name in ("Jaxis, the Troublemaker", *names))
        register_generated_programs(
            self.db, session.engine.semantics, records,
            trust_level="trusted", capability_registry=self.registry, capability_profile="commander_review",
            promote_exact_runtime_handlers=True, promote_exact_trigger_programs=True,
            promote_exact_effect_programs=True, promote_exact_capability_declarations=True,
        )
        for record in records:
            programs = session.engine.semantics.programs_for_oracle(record.oracle_id)
            self.assertTrue(programs, record.name)
            self.assertTrue(all(session.engine.semantic_program_is_current_trusted(p) for p in programs), record.name)
        return session

    def activate(self, session, source, model, discard, *, excluded=()):
        actions = session.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        action, = (row for row in actions if row.get("source") == source.ref and row["id"].startswith("activate:"))
        self.assertIn(model.ref, action["target_schema"]["legal_refs"])
        self.assertTrue(set(excluded).isdisjoint(action["target_schema"]["legal_refs"]))
        command = {"action_id": action["id"], "pay": "auto", "cost_cards": [discard.ref]}
        for ref in excluded:
            before = authoritative_state_hash(session.state)
            result = session.act("pilot:A", {**command, "targets": [ref]})
            self.assertFalse(result.ok)
            self.assertEqual(before, authoritative_state_hash(session.state))
        before = authoritative_state_hash(session.state)
        rejected = session.act("pilot:A", {**command, "targets": [model.ref], "cost_cards": [model.ref]})
        self.assertFalse(rejected.ok)
        self.assertEqual(before, authoritative_state_hash(session.state))

        mana_before = session.state.players["A"].mana_pool["R"]
        result = session.act("pilot:A", {**command, "targets": [model.ref]})
        self.assertTrue(result.ok, result.summary)
        self.assertTrue(session.state.cards[source.object_id].tapped)
        self.assertEqual("graveyard", session.state.cards[discard.object_id].zone)
        self.assertEqual(mana_before - 1, session.state.players["A"].mana_pool["R"])

    @staticmethod
    def tokens(session):
        return [card for card in session.state.cards.values() if card.is_token and card.zone == "battlefield"]

    def test_actual_post_entry_copy_grants_replacement_members_and_draws_without_copying_grants(self):
        from quorune.ability_fragments import GrantedTriggeredAbilitySpec

        session = self.create_session(26112507, "Anointed Procession", "Generic Copy Creature Spell", "Generic Blink Destroy")
        source = self.add(session, "Jaxis, the Troublemaker", "JAXIS", zone="battlefield")
        model = self.add(session, "Generic Copiable Creature", "MODEL", zone="battlefield")
        opponent = self.add(session, "Generic Copiable Creature", "OPPONENT", owner="B", zone="battlefield")
        discard = self.add(session, "Generic Blink Plains", "DISCARD")
        repeat = self.add(session, "Generic Copy Creature Spell", "REPEAT")
        destroy = self.add(session, "Generic Blink Destroy", "DESTROY")
        removal = self.add(session, "Generic Blink Destroy", "DESTROY-SOURCE")
        self.add(session, "Anointed Procession", "DOUBLE", zone="battlefield")
        model.tapped = True
        model.counters["+1/+1"] = 1
        self.seal(session)

        original_source = source.logical_object_id
        self.activate(session, source, model, discard, excluded=(source.ref, opponent.ref))
        self.cast(session, removal, [source.ref])
        self.finish(session)
        self.assertEqual("graveyard", session.state.cards[source.object_id].zone)
        originals = self.tokens(session)
        self.assertEqual(2, len(originals))
        for card in originals:
            self.assertEqual(session.engine._copyable_characteristics(model), session.engine._copyable_characteristics(card))
            self.assertFalse(card.tapped)
            self.assertEqual({}, card.counters)
            self.assertIn("haste", session.engine._combat_keywords(card))
            grant, = (fragment for fragment in session.engine._effective_ability_fragments(card)
                      if isinstance(fragment, GrantedTriggeredAbilitySpec))
            self.assertEqual("permanent.graveyard.self", grant.event)
        cleanup, = [trigger for trigger in session.state.delayed_triggers if trigger.active]
        self.assertEqual(original_source, cleanup.source_logical_object_id)

        self.cast(session, repeat, [originals[0].ref])
        self.finish(session)
        second_generation = [card for card in self.tokens(session) if card not in originals]
        self.assertEqual(2, len(second_generation))
        for card in second_generation:
            self.assertNotIn("haste", session.engine._combat_keywords(card))
            self.assertFalse(any(isinstance(fragment, GrantedTriggeredAbilitySpec)
                                for fragment in session.engine._effective_ability_fragments(card)))

        self.cast(session, destroy, [originals[0].ref])
        hand_before = len(session.state.players["A"].zones["hand"])
        self.finish(session)
        self.assertEqual(hand_before + 1, len(session.state.players["A"].zones["hand"]))
        self.assertEqual("outside", session.state.cards[originals[0].object_id].zone)
        self.advance(session, "ending", "end_step")
        self.finish(session)
        self.assertEqual(hand_before + 2, len(session.state.players["A"].zones["hand"]))
        self.assertEqual("outside", session.state.cards[originals[1].object_id].zone)
        self.assertEqual({card.object_id for card in second_generation}, {card.object_id for card in self.tokens(session)})
        self.replay(session)

    def test_actual_post_entry_copy_rejects_stale_target_after_paid_activation(self):
        session = self.create_session(26112508, "Generic Immediate Blink")
        source = self.add(session, "Jaxis, the Troublemaker", "JAXIS", zone="battlefield")
        model = self.add(session, "Generic Copiable Creature", "MODEL", zone="battlefield")
        blink = self.add(session, "Generic Immediate Blink", "BLINK")
        discard = self.add(session, "Generic Blink Plains", "DISCARD")
        self.seal(session)
        self.activate(session, source, model, discard)
        old_identity = model.logical_object_id
        self.cast(session, blink, [model.ref])
        self.finish(session)
        self.assertNotEqual(old_identity, session.state.cards[model.object_id].logical_object_id)
        self.assertFalse(self.tokens(session))
        self.assertFalse(session.state.delayed_triggers)
        self.assertTrue(session.state.cards[source.object_id].tapped)
        self.assertEqual("graveyard", session.state.cards[discard.object_id].zone)
        self.replay(session)

    def test_actual_blitz_cast_retains_its_separate_death_draw_and_end_step(self):
        session = self.create_session(26112509)
        source = self.add(session, "Jaxis, the Troublemaker", "JAXIS")
        self.seal(session)
        action = next(row for row in session.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
                      if row["id"] == "cast:" + source.ref)
        self.assertIn("blitz", {option["id"] for option in action["cost_options"]})
        result = session.act("pilot:A", {"action_id": action["id"], "pay": "auto", "cost_option": "blitz"})
        self.assertTrue(result.ok, result.summary)
        self.finish(session)
        vested = session.state.cards[source.object_id]
        self.assertEqual("battlefield", vested.zone)
        self.assertIn("haste", session.engine._combat_keywords(vested))
        hand_before = len(session.state.players["A"].zones["hand"])
        self.advance(session, "ending", "end_step")
        self.finish(session)
        self.assertEqual("graveyard", session.state.cards[source.object_id].zone)
        self.assertEqual(hand_before + 1, len(session.state.players["A"].zones["hand"]))
        self.replay(session)


class TokenCopyCopiableSubjectOwnerTests(unittest.TestCase):
    def test_copiable_subject_and_explicit_snapshot_kill_effective_type_mutation(self):
        import inspect
        from types import SimpleNamespace
        from quorune.characteristic_evaluation import type_parts
        from quorune import token_creation as owner
        host = SimpleNamespace(
            state=SimpleNamespace(cards={}, players={"A": SimpleNamespace(zones={"battlefield": []})}),
            _resolve_object=lambda *args, **kwargs: object(),
            _copyable_characteristics=lambda source: {"type_line": "Artifact — Treasure"},
            _effective_card_data=lambda source: {"type_line": "Creature — Frog"},
            _type_parts=type_parts,
        )
        args = dict(name="", quantity=1, copy_of="SOURCE", characteristics=None)
        self.assertEqual(({"artifact"}, {"treasure"}), owner._creation_subject(host, "A", **args)[:2])
        snapshot = {"characteristics": {"type_line": "Enchantment — Aura"}}
        with patch.object(host, "_resolve_object", side_effect=AssertionError("Explicit snapshot must not read live source")):
            self.assertEqual(({"enchantment"}, {"aura"}), owner._creation_subject(host, "A", **args, copy_snapshot=snapshot)[:2])
        source = inspect.getsource(owner._creation_type_line)
        expression = "host._copyable_characteristics(source)"
        self.assertEqual(1, source.count(expression))
        namespace = dict(vars(owner))
        exec(compile(source.replace(expression, "host._effective_card_data(source)"),
                     "<wrong-copy-replacement-characteristic-boundary>", "exec"), namespace)
        with patch.object(owner, "_creation_type_line", namespace["_creation_type_line"]):
            with self.assertRaises(AssertionError):
                self.assertEqual({"artifact"}, owner._creation_subject(host, "A", **args)[0])


if __name__ == "__main__":
    unittest.main()


class LiveDefinedTokenRuntimeTests(unittest.TestCase):
    from test_fixed_direct_target_predicates import DirectTokenTargetRuntimeTests as _Witnesses

    register_fixture_cards_on_add = True
    session = _Witnesses.session
    add = _Witnesses.add
    prepare = _Witnesses.prepare
    checkpoint = _Witnesses.checkpoint
    resolve = _Witnesses.resolve
    replay = _Witnesses.replay
    resume = _Witnesses.resume
    priority = _Witnesses.priority
    actions = _Witnesses.actions
    binding = _Witnesses.binding
    reject = _Witnesses.reject
    pay_cast = _Witnesses.pay_cast

    @classmethod
    def setUpClass(cls):
        from quorune.carddb import CardDatabase
        from quorune.deck import DeckDefinition, DeckEntry
        from scripts.build_test_database import build_fixture_database

        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / "live-defined-tokens.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures" / name for name in (
            "bound-effect-program-cards.json", "live-defined-token-cards.json",
            "graveyard-resolution-condition-cards.json",
        )], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Live token definitions", [DeckEntry("Generic Bound Commander", 1, "commander"),
                                  DeckEntry("Generic Bound Plains", 20)], ["Generic Bound Commander"])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    def _stats(self, session, card):
        return tuple(session.engine._numeric_stat(card.object_id, field) for field in ("power", "toughness"))

    def _tokens(self, session):
        return [card for card in session.state.cards.values() if card.is_token and card.zone == "battlefield"]

    def test_actual_twingrove_tokens_and_copies_use_live_controller_forests(self):
        session = self.session(747101)
        engine = session.engine
        grove = self.add(engine, "Kalonian Twingrove", zone="hand")
        collector = self.add(engine, "Specimen Collector")
        rites = self.add(engine, "Village Rites", zone="hand")
        rain = self.add(engine, "Stone Rain", zone="hand")
        aura = self.add(engine, "Mind Control", seat="B", zone="hand")
        orrery = self.add(engine, "Vedalken Orrery", seat="B")
        forests = [self.add(engine, "Forest", ref=f"forest-A-{index}") for index in range(3)]
        self.add(engine, "Forest", seat="B", ref="forest-B")
        self.assertTrue(self.binding(session, orrery)["strict_capability_ready"])
        session.state.players["B"].mana_pool.update({"C": 3, "U": 2})
        self.prepare(session, mana={"C": 7, "G": 1, "B": 1, "R": 1})
        self.checkpoint(session)
        self.pay_cast(session, grove, payment={"C": 5, "G": 1})
        self.assertIsNone(self.resolve(session))
        original, = self._tokens(session)
        self.assertEqual((3, 3), self._stats(session, original))
        self.pay_cast(session, rites, payment={"B": 1}, sacrifice_cards=[collector.ref])
        self.assertEqual("semantic.target", self.resolve(session).kind)
        private = session.state.cards[session.state.players["D"].zones["hand"][0]]
        self.assertNotIn(private.ref, json.dumps(session.packet("pilot:A", full=True)))
        self.assertIsNone(session.packet("pilot:B", full=True)["decision"])
        self.reject(session, {"action_id": "choose", "targets": [original.ref]}, seat="B")
        session = self.resume(session)
        chosen = session.act("pilot:A", {"action_id": "choose", "targets": [original.ref]})
        self.assertTrue(chosen.ok, chosen.summary)
        self.assertIsNone(self.resolve(session))
        copied, = [token for token in self._tokens(session) if token.object_id != original.object_id]
        for token in (original, copied):
            current = session.state.cards[token.object_id]
            self.assertEqual((3, 3), self._stats(session, current))
            copy_values = session.engine._copyable_characteristics(current)
            self.assertEqual(("*", "*"), (copy_values["power"], copy_values["toughness"]))

        self.pay_cast(session, rain, [forests[0].ref], payment={"C": 2, "R": 1})
        self.assertIsNone(self.resolve(session))
        self.assertEqual((2, 2), self._stats(session, original))
        self.assertEqual((2, 2), self._stats(session, copied))

        self.pay_cast(session, aura, [copied.ref], seat="B", payment={"C": 3, "U": 2})
        self.assertIsNone(self.resolve(session))
        self.assertEqual("B", session.state.cards[copied.object_id].controller)
        self.assertEqual((2, 2), self._stats(session, original))
        self.assertEqual((1, 1), self._stats(session, copied))
        self.replay(session, load=True)

    def test_actual_voice_cast_and_death_subscriptions_create_independent_live_tokens(self):
        session = self.session(747102)
        engine = session.engine
        voice = self.add(engine, "Voice of Resurgence")
        self.add(engine, "Generic Bound Body")
        own_shock = self.add(engine, "Shock", zone="hand", ref="voice-own-shock")
        opponent_shock = self.add(engine, "Shock", seat="B", zone="hand", ref="voice-opponent-shock")
        rites = self.add(engine, "Village Rites", zone="hand")
        self.assertTrue(self.binding(session, voice)["strict_capability_ready"])
        session.state.players["B"].mana_pool["R"] = 1
        self.prepare(session, mana={"R": 1, "B": 1})
        self.checkpoint(session)
        self.pay_cast(session, own_shock, ["B"], payment={"R": 1})
        self.assertIsNone(self.resolve(session))
        self.assertFalse(self._tokens(session))

        life_before = session.state.players["A"].life
        self.pay_cast(session, opponent_shock, ["A"], seat="B", payment={"R": 1})
        self.assertEqual(2, len(session.state.stack))
        self.assertEqual("A", session.state.stack[-1].controller)
        for _ in range(len(engine.active_seats)):
            principal, = session.pending_principals()
            result = session.act(principal, {"action_id": "pass"})
            self.assertTrue(result.ok, result.summary)
        token, = self._tokens(session)
        self.assertEqual(life_before, session.state.players["A"].life)
        self.assertEqual((3, 3), self._stats(session, token))
        self.assertIsNone(self.resolve(session))

        self.pay_cast(session, rites, payment={"B": 1}, sacrifice_cards=[voice.ref])
        self.assertEqual("graveyard", session.state.cards[voice.object_id].zone)
        self.assertIsNone(self.resolve(session))
        self.assertEqual(2, len(self._tokens(session)))
        for token in self._tokens(session):
            self.assertEqual((3, 3), self._stats(session, token))
        self.replay(session, load=True)


class DistributedTokenCreationRuntimeTests(unittest.TestCase):
    from test_fixed_direct_target_predicates import DirectTokenTargetRuntimeTests as _Witnesses

    register_fixture_cards_on_add = True
    session = _Witnesses.session
    add = _Witnesses.add
    prepare = _Witnesses.prepare
    checkpoint = _Witnesses.checkpoint
    resolve = _Witnesses.resolve
    replay = _Witnesses.replay
    resume = _Witnesses.resume
    priority = _Witnesses.priority
    actions = _Witnesses.actions
    binding = _Witnesses.binding
    reject = _Witnesses.reject
    pay_cast = _Witnesses.pay_cast

    @classmethod
    def setUpClass(cls):
        from quorune.carddb import CardDatabase
        from quorune.deck import DeckDefinition, DeckEntry
        from scripts.build_test_database import build_fixture_database

        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / "distributed-tokens.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures" / name for name in (
            "bound-effect-program-cards.json", "fixed-distributed-token-cards.json",
        )], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Distributed token creation",
            [DeckEntry("Generic Bound Commander", 1, "commander"), DeckEntry("Generic Bound Plains", 20)],
            ["Generic Bound Commander"])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    def _tokens(self, session):
        return [card for card in session.state.cards.values() if card.is_token and card.zone == "battlefield"]

    def test_actual_all_player_food_resolves_creator_choices_before_simultaneous_entry(self):
        from quorune.turn_history import current_turn_history_events
        from test_phasing import PhasingRuntimeTests

        session = self.session(748101)
        engine = session.engine
        source = self.add(engine, "Elephant-Mandrill", seat="C", zone="hand")
        first_doubler = self.add(engine, "Parallel Lives", seat="C", ref="creator-c-doubler")
        additional = self.add(engine, "Peregrin Took", seat="C")
        second_doubler = self.add(engine, "Parallel Lives", ref="creator-a-doubler")
        procession = self.add(engine, "Anointed Procession")
        for card in (first_doubler, additional, second_doubler, procession):
            self.assertTrue(self.binding(session, card)["strict_capability_ready"])
        PhasingRuntimeTests._stage_end_step(self, session, "B")
        PhasingRuntimeTests._pass_end_step(self, session, "C")
        self.prepare(session, actor="C", active="C", mana={"C": 2, "G": 1})
        self.checkpoint(session)
        self.pay_cast(session, source, seat="C", payment={"C": 2, "G": 1})
        decision = self.resolve(session)
        self.assertEqual(("replacement.order", ["C"]), (decision.kind, decision.actors))
        self.assertFalse(self._tokens(session))
        packet = session.packet("pilot:C", full=True)
        options = packet["decision"]["ctx"]["options"]
        self.assertEqual(2, len(options))
        selected, = [row["id"] for row in options if f":{additional.ref}:" in row["id"]]
        self.assertIsNone(session.packet("pilot:A", full=True)["decision"])
        private = session.state.cards[session.state.players["D"].zones["hand"][0]]
        self.assertNotIn(private.ref, json.dumps(packet))
        command = {"action_id": "choose", "replacement": selected}
        self.reject(session, command, seat="A")
        self.reject(session, {**command, "replacement": "unissued"}, seat="C")
        chosen = session.act("pilot:C", command)
        self.assertTrue(chosen.ok, chosen.summary)
        self.assertEqual(["A"], session.state.pending_decision.actors)
        self.assertFalse(self._tokens(session))
        prior_prompt = session.packet("pilot:A", full=True)["decision"]["ctx"]["prompt"]
        self.assertIn("Earlier replacement for C", prior_prompt)
        self.assertIn(additional.ref, prior_prompt)
        session = self.resume(session)
        self.assertEqual(prior_prompt, session.packet("pilot:A", full=True)["decision"]["ctx"]["prompt"])

        observed = []
        dispatch = session.engine._dispatch_semantic_event

        def observe(event, context, **fields):
            if event == "permanent.enter" and context.get("token"):
                tokens = self._tokens(session)
                ids = {card.logical_object_id for card in tokens}
                history = current_turn_history_events(
                    session.state.turn_history, turn_sequence=session.state.turn_sequence, kind="permanent_entered",
                )
                entered = {fact.object_incarnation for fact in history}
                observed.append((len(tokens), len(ids & entered)))
            return dispatch(event, context, **fields)

        options = session.packet("pilot:A", full=True)["decision"]["ctx"]["options"]
        self.assertEqual(2, len(options))
        self.reject(session, command, seat="C")
        with patch.object(session.engine, "_dispatch_semantic_event", side_effect=observe):
            chosen = session.act("pilot:A", {"action_id": "choose", "replacement": options[0]["id"]})
        self.assertTrue(chosen.ok, chosen.summary)
        self.assertIsNone(self.resolve(session))
        tokens = self._tokens(session)
        counts = Counter(card.controller for card in tokens)
        self.assertEqual({"C": 4, "D": 1, "A": 4, "B": 1}, counts)
        self.assertTrue(all(card.owner == card.controller for card in tokens))
        self.assertEqual(1, len({card.zone_timestamp for card in tokens}))
        self.assertEqual([(10, 10)] * 10, observed)
        turn_key = str(session.state.turn_sequence)
        for seat, count in counts.items():
            self.assertEqual(count, session.state.players[seat].stats["tokens_created_by_turn"][turn_key])
        self.replay(session, load=True)

    def test_actual_thran_pair_uses_a_live_opponent_and_revalidates_after_departure(self):
        session = self.session(748102)
        source = self.add(session.engine, "Thran Spider", zone="hand")
        self.prepare(session, mana={"C": 3})
        self.checkpoint(session)
        self.pay_cast(session, source, payment={"C": 3})
        self.assertEqual("semantic.target", self.resolve(session).kind)
        schema = session.packet("pilot:A", full=True)["decision"]["ctx"]["target_schema"]
        self.assertEqual({"B", "C", "D"}, set(schema["legal_refs"]))
        self.reject(session, {"action_id": "choose", "targets": ["A"]}, seat="A")
        chosen = session.act("pilot:A", {"action_id": "choose", "targets": ["C"]})
        self.assertTrue(chosen.ok, chosen.summary)
        valid = self.resume(session)
        self.assertIsNone(self.resolve(valid))
        tokens = self._tokens(valid)
        self.assertEqual({"A", "C"}, {card.controller for card in tokens})
        self.assertEqual(2, len(tokens))
        self.assertTrue(all(card.owner == card.controller and card.tapped for card in tokens))
        self.assertEqual(1, len({card.zone_timestamp for card in tokens}))
        self.replay(valid, load=True)

        self.priority(session, "C")
        conceded = session.act("pilot:C", {"action_id": "concede", "choices": {"confirm_concede": True}})
        self.assertTrue(conceded.ok, conceded.summary)
        self.assertFalse(session.state.players["C"].in_game)
        self.assertIsNone(self.resolve(session))
        self.assertFalse(self._tokens(session))
        self.replay(session, load=True)

    def test_actual_slaughter_specialist_creates_only_for_opponents(self):
        session = self.session(748103)
        source = self.add(session.engine, "Slaughter Specialist", zone="hand")
        self.prepare(session, mana={"C": 1, "B": 1})
        self.checkpoint(session)
        self.pay_cast(session, source, payment={"C": 1, "B": 1})
        self.assertIsNone(self.resolve(session))
        tokens = self._tokens(session)
        self.assertEqual({"B": 1, "C": 1, "D": 1}, Counter(card.controller for card in tokens))
        self.assertTrue(all(card.controller == card.owner for card in tokens))
        self.assertEqual(1, len({card.zone_timestamp for card in tokens}))
        self.replay(session, load=True)


class AuthoredTargetedTokenCompilerTests(unittest.TestCase):
    def test_walker_keeps_explicit_rule_name_and_exact_vanilla_characteristics(self):
        from quorune.compiler.token_templates import fixed_token_creation_effect_template
        token=fixed_token_creation_effect_template('Create three Walker tokens.')
        self.assertIsNotNone(token);effect=token.compiled()[1][0]
        self.assertEqual(('Walker',3),(effect['name'],effect['quantity']))
        definition=effect['characteristics']
        self.assertEqual(('2','2',['B']),(definition['power'],definition['toughness'],definition['colors']))
        self.assertEqual('Token Creature — Zombie',definition['type_line'])
        self.assertEqual(1,definition['literal_token_name_version'])

    def test_targeted_creator_shell_consumes_comma_and_quoted_intrinsic_ability_without_losing_keyword(self):
        from quorune.compiler.typed_token_ability_nodes import _token_ability_shell
        text='When this creature enters, target opponent creates The Void, a legendary 5/5 black Horror Villain creature token with flying, indestructible, and "The Void attacks each combat if able."'
        shell,abilities=_token_ability_shell(text)
        self.assertIn('target opponent creates',shell)
        self.assertTrue(shell.rstrip('.').endswith('with flying, indestructible'))
        self.assertEqual(('The Void attacks each combat if able.',),abilities)

    def test_new_token_shell_still_rejects_unconsumed_quotes_and_unrelated_material(self):
        from quorune.compiler.typed_token_ability_nodes import _token_ability_shell
        self.assertIsNone(_token_ability_shell('Create a token with "unknown one" "unknown two" "unknown three".'))
        self.assertIsNone(_token_ability_shell('A player reads "The Void attacks each combat if able."'))


    def test_authored_token_attack_requirement_keeps_raw_combat_owner_in_parent_node(self):
        from quorune.carddb import CardRecord
        from quorune.oracle_ir import compile_oracle_card
        from quorune.rules.capabilities import load_default_capability_registry
        from dataclasses import replace
        from test_qualified_zone_event_queries import query_record
        text='When this creature enters, target opponent creates The Void, a legendary 5/5 black Horror Villain creature token with flying, indestructible, and "The Void attacks each combat if able."'
        record=replace(query_record(text),type_line='Creature — Human',power='4',toughness='4')
        ir=compile_oracle_card(record,capability_registry=load_default_capability_registry(),capability_profile='commander_review')
        self.assertEqual('exact',ir.status,ir.material_residuals)
        parent=ir.faces[0].nodes[0]
        self.assertIn('combat.declaration.typed_components',parent.capability_dependencies)


class AuthoredTargetedTokenNativeFixture(InheritedNativeFixture):
    @classmethod
    def setUpClass(cls):
        from quorune.carddb import CardDatabase
        from quorune.deck import DeckDefinition,DeckEntry
        from scripts.build_test_database import build_fixture_database
        cls.temporary=tempfile.TemporaryDirectory();root=Path(cls.temporary.name)
        base=json.loads((ROOT/'tests/fixtures/bound-effect-program-cards.json').read_text(encoding='utf-8'))
        base['cards']=[row for row in base['cards'] if row['name']=='Generic Bound Plains'];base['rulings']=[]
        path=root/'base.json';path.write_text(json.dumps(base),encoding='utf-8')
        build_fixture_database([path,ROOT/'tests/fixtures/authored-targeted-token-cards.json'],root/'authored.sqlite3')
        cls.db=CardDatabase(root/'authored.sqlite3');cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Authored tokens',[DeckEntry('Isamaru, Hound of Konda',1,'commander'),
            DeckEntry('Generic Bound Plains',30)],['Isamaru, Hound of Konda'])


class AuthoredTargetedTokenNativeTests(AuthoredTargetedTokenNativeFixture,unittest.TestCase):
    def trim_prepared_hands(self,s):
        for seat in 'ABCD':
            for object_id in tuple(s.state.players[seat].zones['hand'])[:2]:
                s.engine.move_card(object_id,'library',reason='Bound full-turn native fixture hands',log=False)

    def advance_to_upkeep_target(self,s):
        for _ in range(400):
            s.engine.pump();decision=s.state.pending_decision
            if decision is not None and decision.kind=='semantic.target':return
            self.assertIsNotNone(decision)
            if decision.kind=='priority':command={'action_id':'pass'}
            elif decision.kind=='combat.attackers':command={'action_id':'attack','attackers':{}}
            elif decision.kind=='combat.blockers':command={'action_id':'block','blocks':{}}
            elif decision.kind=='trigger.order':
                group=next(g for g in s.state.pending_trigger_batches[0].groups if g.controller==decision.actors[0])
                command={'action_id':'order','triggers':[item.ref for item in group.items]}
            else:self.fail('Unexpected fullturn fixture decision: '+decision.kind)
            result=s.act(s.pending_principals()[0],command);self.assertTrue(result.ok,result.summary)
        self.fail('Fullfour-player rotation didnot reach upkeep target: '+str((s.state.active_player,s.state.phase,s.state.step)))

    def tokens(self,s,name):
        return [card for card in s.state.cards.values() if card.is_token and card.zone=='battlefield' and card.printed_name==name]

    def test_two_full_originals_bind_normally_and_void_parent_requires_combat_component_without_coverage(self):
        from quorune.card_programs.binding import bind_semantic_program_runtime
        for i,name in enumerate(('The Sentry, Golden Guardian','Hansk, Slayer Zealot')):
            s=self.session(111100200+i);card=self.add(s.engine,name,zone='hand');self.assert_closed(s,card)
            ir=compile_oracle_card(self.db.lookup(name),capability_registry=self.registry,capability_profile='commander_review')
            self.assertEqual('exact',ir.status,ir.material_residuals)
            if i:continue
            programs=[p for p in s.engine.semantics.programs_for_oracle(card.oracle_id) if 'combat.declaration.typed_components' in p.capability_dependencies]
            self.assertTrue(programs)
            for program in programs:
                deps=tuple(c for c in program.capability_dependencies if c!='combat.declaration.typed_components')
                changed=replace(program,capability_dependencies=deps,coverage=(),capability_closure=self.registry.closure(deps,profile='commander_review').to_dict())
                bound=bind_semantic_program_runtime(changed,capability_registry=self.registry,profile='commander_review')
                self.assertFalse(bound['strict'])
                self.assertIn('capability:undeclared_runtime_dependency:combat.declaration.typed_components',bound['blockers'])

    def test_actual_paid_sentry_opponent_creates_named_void_with_intrinsic_rules_private_pending_load_and_replay(self):
        s=self.session(111100210);source=self.add(s.engine,'The Sentry, Golden Guardian',zone='hand')
        secret=self.add(s.engine,'Counterspell',seat='D',zone='hand',ref='authored-unrelated-private')
        self.assert_closed(s,source);self.prepare(s,mana={'C':3,'W':1});self.checkpoint(s)
        self.cast_for(s,'A',source);self.assertEqual('semantic.target',self.resolve(s).kind)
        self.target(s,'A',['B']);s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
        token,=self.tokens(s,'The Void');self.assertEqual(('B','B'),(token.owner,token.controller))
        data=s.engine._effective_card_data(token)
        self.assertEqual((5,5),(s.engine._numeric_stat(token.object_id,'power'),s.engine._numeric_stat(token.object_id,'toughness')))
        self.assertTrue({'horror','villain'}<=set(s.engine._type_parts(data['type_line'])[1]))
        self.assertIn('Legendary',data['type_line']);self.assertEqual(['B'],data['colors'])
        self.assertTrue({'flying','indestructible'}<=set(s.engine._combat_keywords(token)))
        self.assertFalse(any(s.state.players['A'].mana_pool.values()))
        self.assertTrue(all(token.object_id not in s.state.players[seat].zones['battlefield'] for seat in 'ACD'))
        for viewer in 'ABC':self.assertNotIn(secret.ref,json.dumps(s.packet('pilot:'+viewer,full=True)))
        self.replay(s,load=True)

    def test_actual_paid_hansk_next_upkeep_target_creates_three_walkers_then_printed_damage_and_zombie_death_draw_replay(self):
        s=self.session(111100220);self.trim_prepared_hands(s);source=self.add(s.engine,'Hansk, Slayer Zealot',zone='hand')
        secret=self.add(s.engine,'Counterspell',seat='D',zone='hand',ref='hansk-unrelated-private')
        self.assert_closed(s,source);self.prepare(s,mana={'C':2,'R':1,'G':1});self.checkpoint(s)
        self.cast_for(s,'A',source);self.assertIsNone(self.resolve(s))
        self.advance_to_upkeep_target(s)
        self.target(s,'A',['B']);s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
        walkers=self.tokens(s,'Walker');self.assertEqual(3,len(walkers))
        for token in walkers:
            self.assertEqual(('B','B'),(token.owner,token.controller))
            data=s.engine._effective_card_data(token)
            self.assertEqual(['B'],data['colors']);self.assertEqual({'zombie'},set(s.engine._type_parts(data['type_line'])[1]))
            self.assertEqual((2,2),(s.engine._numeric_stat(token.object_id,'power'),s.engine._numeric_stat(token.object_id,'toughness')))
        self.priority(s,'A')
        rows=s.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        action,=[row for row in rows if row['id'].startswith('activate:'+source.ref+':')]
        hand=len(s.state.players['A'].zones['hand']);before_hand=set(s.state.players['A'].zones['hand'])
        result=s.act('pilot:A',{'action_id':action['id'],'targets':[walkers[0].ref],'pay':'auto'});self.assertTrue(result.ok,result.summary)
        self.assertTrue(s.state.cards[source.object_id].tapped);self.assertIsNone(self.resolve(s))
        self.assertNotEqual('battlefield',s.state.cards[walkers[0].object_id].zone)
        self.assertEqual(hand+1,len(s.state.players['A'].zones['hand']))
        drawn=set(s.state.players['A'].zones['hand'])-before_hand
        for viewer in 'BCD':
            packet=json.dumps(s.packet('pilot:'+viewer,full=True))
            for object_id in drawn:self.assertNotIn(s.state.cards[object_id].ref,packet)
        for viewer in 'ABC':self.assertNotIn(secret.ref,json.dumps(s.packet('pilot:'+viewer,full=True)))
        self.replay(s,load=True)


    def test_actual_hansk_token_doubling_belongs_to_target_creator_not_trigger_controller(self):
        for provider,expected in (('A',3),('B',6)):
            with self.subTest(provider=provider):
                s=self.session(111100230+ord(provider));self.trim_prepared_hands(s);source=self.add(s.engine,'Hansk, Slayer Zealot',zone='hand')
                multiplier=self.add(s.engine,'Parallel Lives',seat=provider)
                for card in (source,multiplier):self.assert_closed(s,card)
                self.prepare(s,mana={'C':2,'R':1,'G':1});self.checkpoint(s);self.cast_for(s,'A',source)
                self.assertIsNone(self.resolve(s));self.advance_to_upkeep_target(s)
                self.target(s,'A',['B']);self.assertIsNone(self.resolve(s));tokens=self.tokens(s,'Walker')
                self.assertEqual(expected,len(tokens));self.assertTrue(all(c.owner==c.controller=='B' for c in tokens))
                self.replay(s,load=True)

    def test_void_intrinsic_requirement_follows_token_controller_after_parent_departure_and_rejects_empty_attack(self):
        s=self.session(111100240);self.trim_prepared_hands(s);source=self.add(s.engine,'The Sentry, Golden Guardian',zone='hand')
        fervor=self.add(s.engine,'Fervor',seat='B');self.assert_closed(s,fervor)
        self.prepare(s,mana={'C':3,'W':1});self.checkpoint(s);self.cast_for(s,'A',source)
        self.assertEqual('semantic.target',self.resolve(s).kind);self.target(s,'A',['B']);self.assertIsNone(self.resolve(s))
        token,=self.tokens(s,'The Void')
        # Canonical owner interaction preparation: parent leaves, token's
        # intrinsic rule survives. Gameplay/replay starts from this checkpoint.
        s.engine.move_card(source.object_id,'exile',reason='Prepared departed token-definition parent',log=False)
        self.checkpoint(s)
        self.until(s,lambda x:x.state.active_player=='B' and x.state.pending_decision is not None and x.state.pending_decision.kind=='combat.attackers')
        before=authoritative_state_hash(s.state)
        rejected=s.act('pilot:B',{'action_id':'attack','attackers':{}});self.assertFalse(rejected.ok)
        self.assertEqual(before,authoritative_state_hash(s.state))
        accepted=s.act('pilot:B',{'action_id':'attack','attackers':{token.ref:'C'}});self.assertTrue(accepted.ok,accepted.summary)
        self.assertEqual('exile',s.state.cards[source.object_id].zone)
        self.replay(s,load=True)


    def sentry_pending(self,seed):
        s=self.session(seed);self.trim_prepared_hands(s);source=self.add(s.engine,'The Sentry, Golden Guardian',zone='hand')
        self.prepare(s,mana={'C':3,'W':1});self.checkpoint(s);self.cast_for(s,'A',source)
        self.assertEqual('semantic.target',self.resolve(s).kind);self.target(s,'A',['B'])
        return s,source

    def test_actual_countered_or_departed_target_trigger_creates_no_token_and_keeps_paid_source(self):
        for mode in ('counter','depart'):
            with self.subTest(mode=mode):
                s=self.session(111100250+len(mode));self.trim_prepared_hands(s)
                source=self.add(s.engine,'The Sentry, Golden Guardian',zone='hand')
                counter=self.add(s.engine,'Stifle',seat='B',zone='hand');s.state.players['B'].mana_pool.update({'U':1})
                self.prepare(s,mana={'C':3,'W':1});self.checkpoint(s);self.cast_for(s,'A',source)
                self.assertEqual('semantic.target',self.resolve(s).kind)
                before=authoritative_state_hash(s.state)
                self.assertFalse(s.act('pilot:A',{'action_id':'choose','targets':['A']}).ok)
                self.assertEqual(before,authoritative_state_hash(s.state))
                self.target(s,'A',['B'])
                if mode=='counter':
                    trigger=next(item for item in s.state.stack if item.kind=='triggered_ability' and item.source_object_id==source.object_id)
                    self.assert_closed(s,counter);self.cast_for(s,'B',counter,[trigger.ref])
                else:
                    self.priority(s,'B');result=s.act('pilot:B',{'action_id':'concede','choices':{'confirm_concede':True}});self.assertTrue(result.ok,result.summary)
                s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
                self.assertEqual([],self.tokens(s,'The Void'));self.assertEqual('battlefield',s.state.cards[source.object_id].zone)
                self.assertFalse(any(s.state.players['A'].mana_pool.values()));self.replay(s,load=True)

    def test_actual_post_creation_fault_rolls_back_entire_token_blueprint_and_event_state_then_loads_and_retries(self):
        s,source=self.sentry_pending(111100270);original=s.engine.create_token;seen=[];failed=None;before=None
        def fault(*args,**kwargs):
            created=original(*args,**kwargs);seen.extend(card.object_id for card in self.tokens(s,'The Void'))
            raise GameRuleError('Injected authored token post-creation fault')
        with patch.object(s.engine,'create_token',side_effect=fault):
            for _ in range(16):
                actor=s.pending_principals()[0];before=authoritative_state_hash(s.state);result=s.act(actor,{'action_id':'pass'})
                if not result.ok:
                    self.assertIn('Injected authored token',result.summary);failed=actor;break
            else:self.fail('Actual creation owner was not reached')
        self.assertEqual(1,len(seen));self.assertEqual(before,authoritative_state_hash(s.state));self.assertEqual([],self.tokens(s,'The Void'))
        s=self.replay(s,load=True);result=s.act(failed,{'action_id':'pass'});self.assertTrue(result.ok,result.summary)
        self.assertIsNone(self.resolve(s));token,=self.tokens(s,'The Void');self.assertEqual(('B','B'),(token.owner,token.controller))
        self.replay(s,load=True)

    def test_fresh_native_wrong_creator_mutant_is_killed_after_positive_paid_original(self):
        method='test_actual_paid_sentry_opponent_creates_named_void_with_intrinsic_rules_private_pending_load_and_replay'
        baseline=unittest.TestResult();type(self)(method).run(baseline);self.assertEqual((0,0),(len(baseline.failures),len(baseline.errors)))
        from quorune.engine import CommanderEngine
        original=CommanderEngine.create_token
        def wrong(host,seat,*args,**kwargs):return original(host,'A',*args,**kwargs)
        mutated=unittest.TestResult()
        with patch.object(CommanderEngine,'create_token',wrong):type(self)(method).run(mutated)
        self.assertEqual([],mutated.errors);self.assertEqual(1,len(mutated.failures))


class AuthoredTargetedTokenWithdrawalTests(AuthoredTargetedTokenNativeFixture,unittest.TestCase):
    def test_genuine_verified_definition_target_and_intrinsic_owner_withdrawals_keep_unrelated_controls(self):
        from copy import deepcopy
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index,discover_tests
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        doc=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json');known=set(discover_tests(ROOT).values())
        baseline=CapabilityRegistry(raw)
        baseline_index=build_index(registry_value=raw,declaration_source=doc,discovered_test_ids=known)
        baseline.mark_evidence_verified(validate_capability_evidence_index(baseline_index,registry=baseline))
        baseline_dependencies={}
        for name in ('The Sentry, Golden Guardian','Hansk, Slayer Zealot'):
            original=compile_oracle_card(self.db.lookup(name),capability_registry=baseline,capability_profile='commander_review')
            self.assertEqual('exact',original.status,name)
            baseline_dependencies[name]=tuple(sorted({capability for face in original.faces for node in face.nodes for capability in node.capability_dependencies}))
        for cap in ('token.creation.fixed_definition','target.revalidate_resolution','token.creation.typed_ability_definition','combat.declaration.typed_components'):
            value=deepcopy(raw);next(row for row in value['capabilities'] if row['id']==cap).update(status='blocked',blockers=['Authored token or necessary intrinsic owner withdrawn'])
            registry=CapabilityRegistry(value);index=build_index(registry_value=value,declaration_source=doc,discovered_test_ids=known)
            registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry));self.assertTrue(registry.evidence_fingerprint)
            for name in ('The Sentry, Golden Guardian','Hansk, Slayer Zealot'):
                ir=compile_oracle_card(self.db.lookup(name),capability_registry=registry,capability_profile='commander_review')
                required=name=='The Sentry, Golden Guardian' or cap in ('token.creation.fixed_definition','target.revalidate_resolution')
                if required:
                    self.assertNotEqual('exact',ir.status,(cap,name))
                    closure=registry.closure(baseline_dependencies[name],profile='commander_review')
                    self.assertFalse(closure.trusted)
                    self.assertIn('status:'+cap+':blocked',set(closure.blockers))
                    blockers={b for r in ir.material_residuals for b in r.blockers}
                    self.assertFalse(any('unverified' in b for b in blockers))
                else:self.assertEqual('exact',ir.status,(cap,name))
            for name in ('Grizzly Bears','Island'):
                self.assertEqual('exact',compile_oracle_card(self.db.lookup(name),capability_registry=registry,capability_profile='commander_review').status,(cap,name))


class EnchantedCopyRecipeContractTests(unittest.TestCase):
    def spec(self):
        from quorune.token_copy_recipes import TokenCopyRecipeSpec
        return TokenCopyRecipeSpec(origin='source_attachment',schema_version=3,attachment_kind='enchanted')

    def test_recipe_three_is_immutable_and_explicitly_binds_the_enchanted_source(self):
        from dataclasses import FrozenInstanceError
        from quorune.token_copy_recipes import TokenCopyRecipeSpec
        spec=self.spec();self.assertEqual(spec,TokenCopyRecipeSpec.from_dict(spec.to_dict()))
        self.assertEqual(('source_attachment','enchanted',3),(spec.origin,spec.attachment_kind,spec.schema_version))
        with self.assertRaises(FrozenInstanceError):spec.attachment_kind='equipped'

    def test_old_versions_cannot_carry_new_relation_and_existing_source_and_embalm_encodings_keep_meaning(self):
        from quorune.token_copy_recipes import TokenCopyRecipeSpec
        wire=self.spec().to_dict()
        for version in (1,2):
            with self.subTest(version=version),self.assertRaises(ValueError):
                TokenCopyRecipeSpec.from_dict({**wire,'schema_version':version})
        for old in (TokenCopyRecipeSpec(origin='source'),TokenCopyRecipeSpec(origin='source',schema_version=2,creation_origin='embalm')):
            self.assertEqual(old,TokenCopyRecipeSpec.from_dict(old.to_dict()))
            self.assertNotIn('attachment_kind',old.to_dict())
        with self.assertRaises(ValueError):TokenCopyRecipeSpec(origin='source',schema_version=3,attachment_kind='enchanted')

    def test_unknown_relation_cleanup_grants_and_wire_fields_fail_closed(self):
        from quorune.token_copy_recipes import TokenCopyRecipeSpec
        wire=self.spec().to_dict()
        for change in ({'attachment_kind':'equipped'},{'attachment_kind':True},{'cleanup':'exile_next_end_step'},
                {'temporary_keywords':['Haste']},{'creation_origin':'embalm'},{'extra':None},{'schema_version':True}):
            with self.subTest(change=change),self.assertRaises(ValueError):TokenCopyRecipeSpec.from_dict({**wire,**change})

    def test_new_relation_requires_fine_and_both_parent_owners_independently_of_coverage(self):
        from quorune.rules.token_creation_capability_shapes import token_definition_payload_dependencies
        effect={'op':'create_token','controller':'$controller','quantity':1,'copy_spec':self.spec().to_dict()}
        deps=token_definition_payload_dependencies(effect)
        self.assertIn('token.creation.enchanted_source_copy',deps)
        self.assertEqual({'token.creation.enchanted_source_copy','token.creation.fixed_copy','object.reference.source_attachment_information'},set(deps))


class EnchantedCopyReferenceTests(unittest.TestCase):
    @staticmethod
    def fixture():
        from types import SimpleNamespace
        from quorune.model import CardInstance
        from quorune.attachment_references import (
            AttachmentReferenceKind, capture_source_attachment_snapshot,
        )
        from quorune.token_copy_recipes import TokenCopyRecipeSpec
        def card(oid, name):
            return CardInstance(object_id=oid, ref=oid.upper(), oracle_id=oid,
                printed_name=name, owner="A", controller="B", zone="battlefield")
        source, first, second = card("aura", "Aura"), card("first", "First"), card("second", "Second")
        source.attached_to=first.object_id; first.attachments.append(source.object_id)
        cards={v.object_id:v for v in (source,first,second)}
        snapshot=capture_source_attachment_snapshot(cards,source,AttachmentReferenceKind.ENCHANTED)
        recipe=TokenCopyRecipeSpec(origin="source_attachment",schema_version=3,attachment_kind="enchanted")
        item=SimpleNamespace(ref="stack:attachment-recipe",source_object_id=source.object_id,card_object_id=None,semantic_key="recipe",
            context={"source_logical_object_id":source.logical_object_id,
                "source_attachment_snapshot":snapshot.to_dict(),"source_attachment_information_version":1})
        effect={"op":"create_token","controller":"$controller","quantity":1,"copy_spec":recipe.to_dict()}
        host=SimpleNamespace(state=SimpleNamespace(cards=cards,stack=[item],pending_trigger_batches=[]),
            semantics=SimpleNamespace(get=lambda key:SimpleNamespace(effects=(effect,),target_schema=None)))
        return host,item,recipe,source,first,second

    @staticmethod
    def snapshot(host,card):
        return {"object_id":card.object_id,"logical_object_id":card.logical_object_id,
                "ref":card.ref,"zone":card.zone,"snapshot":{"name":card.printed_name}}

    def test_full_consumption_requires_source_relation_and_captures_versioned_information(self):
        from quorune.compiler.token_copy_templates import token_copy_recipe_template
        from quorune.attachment_references import (
            AttachmentReferenceKind, required_attachment_relation, attachment_information_capabilities,
        )
        text="Create a token that's a copy of enchanted creature, except it's a Spirit in addition to its other types."
        compiled=token_copy_recipe_template(text,source_name="Generic Aura",source_is_permanent=True,
            source_attachment_relation=AttachmentReferenceKind.ENCHANTED)
        self.assertIsNotNone(compiled)
        self.assertIsNone(compiled.target_schema)
        effect=compiled.effect
        self.assertEqual(3,effect["copy_spec"]["schema_version"])
        self.assertEqual(["spirit"],effect["copy_spec"]["exception"]["add_subtypes"])
        self.assertIs(AttachmentReferenceKind.ENCHANTED,required_attachment_relation((effect,)))
        self.assertIn("object.reference.source_attachment_information",attachment_information_capabilities((effect,)))
        for relation in (None,AttachmentReferenceKind.EQUIPPED):
            self.assertIsNone(token_copy_recipe_template(text,source_name="Generic Aura",source_is_permanent=True,
                source_attachment_relation=relation))
        for bad in (text+" Draw a card.",text.replace("enchanted","equipped"),
                "Create a token that's a copy of enchanted creature. It gains haste.",
                "Create a token that's a copy of enchanted creature. Exile it at the beginning of the next end step."):
            self.assertIsNone(token_copy_recipe_template(bad,source_name="Generic Aura",source_is_permanent=True,
                source_attachment_relation=AttachmentReferenceKind.ENCHANTED))

    def test_current_relation_overrides_old_snapshot_and_cached_creature(self):
        from quorune.token_copy_references import copy_reference_snapshot, pin_copy_characteristic_departures
        host,item,recipe,source,first,second=self.fixture()
        with patch("quorune.token_copy_references._snapshot",side_effect=self.snapshot):
            pin_copy_characteristic_departures(host,[first])
            source.attached_to=second.object_id;first.attachments.clear();second.attachments.append(source.object_id)
            value=copy_reference_snapshot(host,item,recipe)
        self.assertEqual(second.logical_object_id,value["logical_object_id"])
        self.assertEqual("Second",value["snapshot"]["name"])

    def test_departures_pin_immediate_creature_values_and_neither_reentry_substitutes(self):
        from quorune.attachment_information import pin_attachment_information_departures
        from quorune.token_copy_references import copy_reference_snapshot, pin_copy_characteristic_departures
        host,item,recipe,source,first,second=self.fixture()
        # Aura moved to another creature after the trigger, then both depart.
        source.attached_to=second.object_id;first.attachments.clear();second.attachments.append(source.object_id)
        old_id=second.logical_object_id
        with patch("quorune.token_copy_references._snapshot",side_effect=self.snapshot):
            pin_attachment_information_departures(host,[source,second])
            pin_copy_characteristic_departures(host,[source,second])
            source.zone="graveyard";source.zone_change_counter+=1;source.attached_to=None
            second.zone="graveyard";second.zone_change_counter+=1;second.attachments.clear()
            second.printed_name="Reentered Different Values";second.zone="battlefield"
            source.zone="battlefield";source.attached_to=first.object_id;first.attachments.append(source.object_id)
            value=copy_reference_snapshot(host,item,recipe)
        self.assertEqual(old_id,value["logical_object_id"])
        self.assertEqual("Second",value["snapshot"]["name"])

    def test_source_departure_keeps_last_relation_and_reads_living_creature_now(self):
        from quorune.attachment_information import pin_attachment_information_departures
        from quorune.token_copy_references import copy_reference_snapshot
        host,item,recipe,source,first,second=self.fixture()
        source.attached_to=second.object_id;first.attachments.clear();second.attachments.append(source.object_id)
        pin_attachment_information_departures(host,[source])
        source.zone="graveyard";source.zone_change_counter+=1;source.attached_to=None
        second.printed_name="Current Copiable Values"
        with patch("quorune.token_copy_references._snapshot",side_effect=self.snapshot):
            value=copy_reference_snapshot(host,item,recipe)
        self.assertEqual("Current Copiable Values",value["snapshot"]["name"])
        self.assertEqual(second.logical_object_id,value["logical_object_id"])

    def test_detachment_and_phasing_create_no_reference_and_tampered_context_fails_closed(self):
        from quorune.token_copy_references import copy_reference_snapshot
        host,item,recipe,source,first,second=self.fixture()
        source.attached_to=None;first.attachments.clear()
        self.assertIsNone(copy_reference_snapshot(host,item,recipe))
        source.attached_to=first.object_id;first.attachments.append(source.object_id);source.phased_out=True
        self.assertIsNone(copy_reference_snapshot(host,item,recipe))
        source.phased_out=False
        for key,bad in (("source_logical_object_id","wrong-incarnation"),
                ("source_attachment_information_version",True),("source_attachment_snapshot",{})):
            before=item.context[key];item.context[key]=bad
            with self.subTest(key=key),self.assertRaises(ValueError):
                copy_reference_snapshot(host,item,recipe)
            item.context[key]=before

    def test_wire_cannot_change_canonical_reference_type_relation_or_version(self):
        from quorune.token_copy_recipes import TokenCopyRecipeSpec
        _,_,recipe,_,_,_=self.fixture();wire=recipe.to_dict()
        for field,bad in (("relation","equipped_object"),("required_card_type","permanent"),("schema_version",1)):
            altered={**wire,"attachment_reference":{**wire["attachment_reference"],field:bad}}
            with self.subTest(field=field),self.assertRaises(ValueError):TokenCopyRecipeSpec.from_dict(altered)


    def test_captured_context_json_reload_retains_original_incarnations_and_old_versions_stay_closed(self):
        from quorune.token_copy_references import copy_reference_snapshot
        host,item,recipe,source,first,second=self.fixture()
        with patch("quorune.token_copy_references._snapshot",side_effect=self.snapshot):
            before=copy.deepcopy(item.context)
            expected=copy_reference_snapshot(host,item,recipe)
            item.context=json.loads(json.dumps(item.context))
            self.assertEqual(expected,copy_reference_snapshot(host,item,recipe))
            self.assertEqual(before,item.context)
            item.context.pop("source_attachment_information_version")
            with self.assertRaises(ValueError):copy_reference_snapshot(host,item,recipe)

    def test_fresh_reference_mutant_ignoring_current_attachment_is_killed(self):
        from quorune.token_copy_references import copy_reference_snapshot
        host,item,recipe,source,first,second=self.fixture()
        source.attached_to=second.object_id;first.attachments.clear();second.attachments.append(source.object_id)
        def witness():
            with patch("quorune.token_copy_references._snapshot",side_effect=self.snapshot):
                self.assertEqual(second.logical_object_id,copy_reference_snapshot(host,item,recipe)["logical_object_id"])
        witness()
        with patch("quorune.token_copy_references.source_attachment_copy_identity",
                return_value=(first.object_id,first.logical_object_id,"battlefield")):
            with self.assertRaises(AssertionError):witness()


    def test_semantic_resolution_preserves_typed_copy_reference_for_canonical_copy_owner(self):
        from quorune.semantic_runtime.values import resolve_semantic_value
        host,item,recipe,source,first,second=self.fixture()
        item.controller="C"
        effect={"op":"create_token","controller":"$controller","quantity":1,"copy_spec":recipe.to_dict()}
        resolved=resolve_semantic_value(host,effect,item)
        self.assertEqual("C",resolved["controller"])
        self.assertEqual(recipe.to_dict(),resolved["copy_spec"])
        self.assertIsInstance(resolved["copy_spec"]["attachment_reference"],dict)
        self.assertEqual(source.logical_object_id,item.context["source_logical_object_id"])


    def test_current_entry_copy_retains_physical_face_and_historical_or_token_results_keep_recorded_form(self):
        from types import SimpleNamespace
        from quorune.model import CardInstance
        from quorune.entry_results import commit_prepared_copy_values
        from quorune.entry_copy import COPIABLE_ORACLE_FIELD,COPIABLE_FACE_FIELD
        values={COPIABLE_ORACLE_FIELD:"copied-source",COPIABLE_FACE_FIELD:"front","type_line":"Creature — Bear"}
        prepared=SimpleNamespace(event=SimpleNamespace(payload={"entry_copy":values}))
        def card():
            return CardInstance("physical","PHYSICAL","physical-oracle","Physical double face","A","A","battlefield",active_face="Physical front")
        current=card();commit_prepared_copy_values(prepared,current,error_type=ValueError,preserve_physical_face=True)
        self.assertEqual("Physical front",current.active_face)
        self.assertEqual(values,current.annotations["copy_overrides"])
        old=card();commit_prepared_copy_values(prepared,old,error_type=ValueError)
        self.assertEqual("front",old.active_face)
        token=card();token.is_token=True;token.object_kind="token"
        commit_prepared_copy_values(prepared,token,error_type=ValueError,preserve_physical_face=True)
        self.assertEqual("front",token.active_face)


class EnchantedCopyNativeFixture(InheritedNativeFixture):
    @classmethod
    def setUpClass(cls):
        from quorune.carddb import CardDatabase
        from quorune.deck import DeckDefinition,DeckEntry
        from scripts.build_test_database import build_fixture_database
        cls.temporary=tempfile.TemporaryDirectory();root=Path(cls.temporary.name)
        base=json.loads((ROOT/"tests/fixtures/bound-effect-program-cards.json").read_text(encoding="utf-8"))
        base["cards"]=[row for row in base["cards"] if row["name"]=="Generic Bound Plains"];base["rulings"]=[]
        path=root/"base.json";path.write_text(json.dumps(base),encoding="utf-8")
        build_fixture_database([path,ROOT/"tests/fixtures/enchanted-source-token-copy-cards.json"],root/"enchanted-copy.sqlite3")
        cls.db=CardDatabase(root/"enchanted-copy.sqlite3");cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition("Enchanted copies",[DeckEntry("Isamaru, Hound of Konda",1,"commander"),
            DeckEntry("Generic Bound Plains",30)],["Isamaru, Hound of Konda"])

    def tokens(self,s):
        return [c for c in s.state.cards.values() if c.is_token and c.zone=="battlefield"]

    def advance_to_upkeep_trigger(self,s,source):
        for _ in range(400):
            s.engine.pump()
            if any(i.kind=="triggered_ability" and i.source_object_id==source.object_id for i in s.state.stack):
                return
            decision=s.state.pending_decision;self.assertIsNotNone(decision)
            if decision.kind=="priority":command={"action_id":"pass"}
            elif decision.kind=="combat.attackers":command={"action_id":"attack","attackers":{}}
            elif decision.kind=="combat.blockers":command={"action_id":"block","blocks":{}}
            elif decision.kind=="trigger.order":
                group=next(g for g in s.state.pending_trigger_batches[0].groups if g.controller==decision.actors[0])
                command={"action_id":"order","triggers":[i.ref for i in group.items]}
            else:self.fail("Unexpected enchanted-copy turn decision: "+decision.kind)
            result=s.act(s.pending_principals()[0],command);self.assertTrue(result.ok,result.summary)
        self.fail("No printed upkeep trigger in full four-player rotation")

    def trim_prepared_hands(self,s):
        for seat in "ABCD":
            for oid in tuple(s.state.players[seat].zones["hand"])[:2]:
                s.engine.move_card(oid,"library",reason="Bound full-turn enchanted-copy fixture hands",log=False)

    def paid_followed(self,seed,*,target_seat="B"):
        s=self.session(seed);self.trim_prepared_hands(s)
        body=self.add(s.engine,"Grizzly Bears",seat=target_seat)
        source=self.add(s.engine,"Followed Footsteps",zone="hand")
        self.assert_closed(s,source);self.prepare(s,mana={"C":3,"U":2})
        self.checkpoint(s);self.cast_for(s,"A",source,[body.ref]);self.assertIsNone(self.resolve(s))
        self.assertEqual(("battlefield",body.object_id),(source.zone,source.attached_to))
        self.assertFalse(any(s.state.players["A"].mana_pool.values()))
        return s,source,body


class EnchantedCopyNativeTests(EnchantedCopyNativeFixture,unittest.TestCase):
    def test_two_full_originals_bind_normally_and_raw_fine_owner_cannot_be_stripped_without_coverage(self):
        from quorune.card_programs.binding import bind_semantic_program_runtime
        fine="token.creation.enchanted_source_copy"
        for j,name in enumerate(("Followed Footsteps","Mirrorhall Mimic // Ghastly Mimicry")):
            s=self.session(707303400+j);source=self.add(s.engine,name,zone="hand");self.assert_closed(s,source)
            ir=compile_oracle_card(self.db.lookup(name),capability_registry=self.registry,capability_profile="commander_review")
            self.assertEqual("exact",ir.status,ir.material_residuals)
            programs=[p for p in s.engine.semantics.programs_for_oracle(source.oracle_id) if fine in p.capability_dependencies]
            self.assertTrue(programs)
            for p in programs:
                deps=tuple(d for d in p.capability_dependencies if d!=fine)
                changed=replace(p,coverage=(),capability_dependencies=deps,
                    capability_closure=self.registry.closure(deps,profile="commander_review").to_dict())
                bound=bind_semantic_program_runtime(changed,capability_registry=self.registry,profile="commander_review")
                self.assertFalse(bound["strict"])
                self.assertIn("capability:undeclared_runtime_dependency:"+fine,bound["blockers"])

    def test_actual_paid_aura_copies_opponents_creature_for_ability_controller_with_private_load_and_replay(self):
        s=self.session(707303410);self.trim_prepared_hands(s)
        body=self.add(s.engine,"Grizzly Bears",seat="B")
        source=self.add(s.engine,"Followed Footsteps",zone="hand")
        secret=self.add(s.engine,"Counterspell",seat="D",zone="hand",ref="enchanted-copy-private")
        self.assert_closed(s,source);self.prepare(s,mana={"C":3,"U":2});self.checkpoint(s)
        self.cast_for(s,"A",source,[body.ref]);self.assertIsNone(self.resolve(s))
        self.assertEqual(body.object_id,source.attached_to);self.assertFalse(any(s.state.players["A"].mana_pool.values()))
        self.advance_to_upkeep_trigger(s,source)
        trigger,=[i for i in s.state.stack if i.source_object_id==source.object_id]
        self.assertFalse(trigger.targets)
        s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
        token,=self.tokens(s);self.assertEqual(("A","A"),(token.owner,token.controller))
        self.assertEqual("Grizzly Bears",token.printed_name)
        self.assertEqual((2,2),tuple(s.engine._numeric_stat(token.object_id,k) for k in ("power","toughness")))
        self.assertEqual("B",s.state.cards[body.object_id].controller)
        for viewer in "ABC":self.assertNotIn(secret.ref,json.dumps(s.packet("pilot:"+viewer,full=True)))
        self.replay(s,load=True)

    def test_actual_source_removal_keeps_last_enchanted_creature_and_independent_trigger_replays(self):
        s,source,body=self.paid_followed(707303420)
        # Response mana is present before the record boundary and paid normally.
        removal=self.add(s.engine,"Disenchant",seat="B",zone="hand")
        for j in range(2):self.add(s.engine,"Generic Bound Plains",seat="B",ref="removal-plains-"+str(j))
        self.checkpoint(s);self.advance_to_upkeep_trigger(s,source)
        self.cast_for(s,"B",removal,[source.ref]);self.resolve_top(s)
        self.assertEqual("graveyard",s.state.cards[source.object_id].zone)
        s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
        token,=self.tokens(s);self.assertEqual(("A","A","Grizzly Bears"),(token.owner,token.controller,token.printed_name))
        self.replay(s,load=True)

    def test_actual_creature_blink_leaves_aura_unattached_and_creates_no_token_from_reentered_object(self):
        s,source,body=self.paid_followed(707303430)
        blink=self.add(s.engine,"Cloudshift",seat="B",zone="hand")
        self.add(s.engine,"Generic Bound Plains",seat="B")
        before_identity=body.logical_object_id
        self.checkpoint(s);self.advance_to_upkeep_trigger(s,source)
        self.cast_for(s,"B",blink,[body.ref]);self.resolve_top(s)
        self.assertNotEqual(before_identity,s.state.cards[body.object_id].logical_object_id)
        self.assertEqual("graveyard",s.state.cards[source.object_id].zone)
        s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
        self.assertFalse(self.tokens(s))
        self.replay(s,load=True)


    def test_actual_paid_mimic_front_copies_creature_and_copiable_spirit_values_with_pending_load(self):
        s=self.session(707303450)
        body=self.add(s.engine,"Colossal Dreadmaw",seat="B")
        source=self.add(s.engine,"Mirrorhall Mimic // Ghastly Mimicry",zone="hand")
        self.assert_closed(s,source);self.prepare(s,mana={"C":3,"U":1});self.checkpoint(s)
        self.cast_for(s,"A",source)
        self.assertEqual("replacement.order",self.resolve(s).kind)
        s=self.replay(s,load=True)
        option=next(r for r in s.packet("pilot:A",full=True)["decision"]["ctx"]["options"]
                    if body.ref in r["id"] and not r["id"].startswith("decline:"))
        command={"action_id":"choose","replacement":option["id"]}
        before=authoritative_state_hash(s.state)
        self.assertFalse(s.act("pilot:C",command).ok);self.assertEqual(before,authoritative_state_hash(s.state))
        result=s.act("pilot:A",command);self.assertTrue(result.ok,result.summary);self.assertIsNone(self.resolve(s))
        current=s.state.cards[source.object_id];data=s.engine._effective_card_data(current)
        self.assertEqual("Colossal Dreadmaw",data["name"])
        self.assertEqual({"dinosaur","spirit"},set(s.engine._type_parts(data["type_line"])[1]))
        self.assertEqual((6,6),tuple(s.engine._numeric_stat(current.object_id,k) for k in ("power","toughness")))
        self.assertIn("trample",s.engine._combat_keywords(current))
        self.assertFalse(any(s.state.players["A"].mana_pool.values()))
        self.replay(s,load=True)

    def test_actual_paid_disturb_aura_upkeep_copies_spirit_addition_and_printed_graveyard_destination_exiles(self):
        s=self.session(707303440);self.trim_prepared_hands(s)
        body=self.add(s.engine,"Grizzly Bears",seat="B")
        source=self.add(s.engine,"Mirrorhall Mimic // Ghastly Mimicry",zone="graveyard")
        removal=self.add(s.engine,"Disenchant",seat="B",zone="hand")
        for j in range(2):self.add(s.engine,"Generic Bound Plains",seat="B",ref="mimic-removal-plains-"+str(j))
        secret=self.add(s.engine,"Counterspell",seat="D",zone="hand",ref="mimic-private")
        self.assert_closed(s,source);self.prepare(s,mana={"C":3,"U":2});self.checkpoint(s)
        action_id="cast:"+source.ref+":face:Ghastly Mimicry"
        self.assertIn(action_id,[r["id"] for r in s.packet("pilot:A",full=True)["decision"]["ctx"]["legal"]["actions"]])
        command={"action_id":action_id,"face":"Ghastly Mimicry","cost_option":"disturb","targets":[body.ref],"pay":"auto"}
        before=authoritative_state_hash(s.state)
        self.assertFalse(s.act("pilot:C",command).ok);self.assertEqual(before,authoritative_state_hash(s.state))
        result=s.act("pilot:A",command);self.assertTrue(result.ok,result.summary);self.assertIsNone(self.resolve(s))
        self.assertEqual(("battlefield",body.object_id),(source.zone,source.attached_to))
        self.assertFalse(any(s.state.players["A"].mana_pool.values()))
        self.advance_to_upkeep_trigger(s,source);s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
        token,=self.tokens(s);self.assertEqual(("A","A"),(token.owner,token.controller))
        self.assertEqual({"bear","spirit"},set(s.engine._type_parts(s.engine._effective_card_data(token)["type_line"])[1]))
        self.assertEqual((2,2),tuple(s.engine._numeric_stat(token.object_id,k) for k in ("power","toughness")))
        self.cast_for(s,"B",removal,[s.state.cards[source.object_id].ref]);self.assertIsNone(self.resolve(s))
        self.assertEqual("exile",s.state.cards[source.object_id].zone)
        for viewer in "ABC":self.assertNotIn(secret.ref,json.dumps(s.packet("pilot:"+viewer,full=True)))
        self.replay(s,load=True)


    def controlled_upkeep(self,seed):
        from quorune.attachments import attach_objects
        from quorune.control_effects import synchronize_control_effects
        s=self.session(seed)
        body=self.add(s.engine,"Grizzly Bears",seat="C")
        source=self.add(s.engine,"Followed Footsteps",seat="A")
        theft=self.add(s.engine,"Confiscate",seat="B")
        self.add(s.engine,"Parallel Lives",seat="B")
        self.assert_closed(s,source);self.assert_closed(s,theft)
        attach_objects(s.state.cards,source,body,source_timestamp=source.zone_timestamp,players=s.state.players)
        attach_objects(s.state.cards,theft,source,source_timestamp=theft.zone_timestamp,players=s.state.players)
        synchronize_control_effects(s.engine,reason="Canonical initial Aura-control fixture")
        self.assertEqual(("A","B","C"),(source.owner,source.controller,body.controller))
        self.prepare(s);self.checkpoint(s);self.advance_to_upkeep_trigger(s,source)
        trigger,=[i for i in s.state.stack if i.source_object_id==source.object_id]
        self.assertEqual("B",trigger.controller)
        return s,source,body

    def test_actual_aura_owner_controller_and_creature_controller_are_distinct_and_creator_replacement_applies(self):
        s,source,body=self.controlled_upkeep(707303460)
        hidden=s.state.cards[s.state.players["D"].zones["hand"][0]].ref
        s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
        tokens=self.tokens(s);self.assertEqual(2,len(tokens))
        self.assertTrue(all((c.owner,c.controller)==("B","B") for c in tokens))
        self.assertTrue(all(c.printed_name=="Grizzly Bears" for c in tokens))
        self.assertEqual(("A","B","C"),(s.state.cards[source.object_id].owner,
            s.state.cards[source.object_id].controller,s.state.cards[body.object_id].controller))
        for viewer in "ABC":self.assertNotIn(hidden,json.dumps(s.packet("pilot:"+viewer,full=True)))
        self.replay(s,load=True)

    def test_actual_disk_simultaneous_departure_copies_last_enchanted_creature_and_replays(self):
        s=self.session(707303470);self.trim_prepared_hands(s)
        body=self.add(s.engine,"Grizzly Bears",seat="B")
        source=self.add(s.engine,"Followed Footsteps",zone="hand")
        disk=self.add(s.engine,"Nevinyrral's Disk",seat="B")
        self.add(s.engine,"Generic Bound Plains",seat="B")
        self.assert_closed(s,source);self.assert_closed(s,disk)
        self.prepare(s,mana={"C":3,"U":2});self.checkpoint(s)
        self.cast_for(s,"A",source,[body.ref]);self.assertIsNone(self.resolve(s))
        self.advance_to_upkeep_trigger(s,source);self.priority(s,"B")
        action,=[r for r in s.packet("pilot:B",full=True)["decision"]["ctx"]["legal"]["actions"]
                 if r["id"].startswith("activate:"+disk.ref+":")]
        result=s.act("pilot:B",{"action_id":action["id"],"pay":"auto"});self.assertTrue(result.ok,result.summary)
        self.resolve_top(s)
        self.assertTrue(all(s.state.cards[c.object_id].zone=="graveyard" for c in (source,body,disk)))
        s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
        token,=self.tokens(s);self.assertEqual(("A","A","Grizzly Bears"),(token.owner,token.controller,token.printed_name))
        self.assertEqual((2,2),tuple(s.engine._numeric_stat(token.object_id,k) for k in ("power","toughness")))
        self.replay(s,load=True)

    def test_actual_post_copy_group_fault_rolls_back_full_state_and_loads_retries(self):
        from quorune import token_creation as owner
        s,source,body=self.controlled_upkeep(707303480)
        original=owner.create_tokens;seen=[];failed=None;before=None
        def fault(*args,**kwargs):
            created=original(*args,**kwargs);seen.extend(created)
            raise GameRuleError("Injected enchanted-copy post-group fault")
        with patch.object(owner,"create_tokens",side_effect=fault):
            for _ in range(16):
                actor=s.pending_principals()[0];before=authoritative_state_hash(s.state)
                result=s.act(actor,{"action_id":"pass"})
                if not result.ok:
                    self.assertIn("Injected enchanted-copy",result.summary);failed=actor;break
            else:self.fail("Canonical copy group was not reached")
        self.assertEqual(2,len(seen));self.assertEqual(before,authoritative_state_hash(s.state))
        self.assertFalse(self.tokens(s))
        s=self.replay(s,load=True);result=s.act(failed,{"action_id":"pass"});self.assertTrue(result.ok,result.summary)
        self.assertIsNone(self.resolve(s));self.assertEqual(2,len(self.tokens(s)))
        self.assertTrue(all((c.owner,c.controller)==("B","B") for c in self.tokens(s)))
        self.replay(s,load=True)

    def test_fresh_native_wrong_creator_mutant_is_killed_with_original_aura_owner_held_constant(self):
        from quorune import token_creation as owner
        method="test_actual_aura_owner_controller_and_creature_controller_are_distinct_and_creator_replacement_applies"
        baseline=unittest.TestResult();type(self)(method).run(baseline)
        self.assertEqual((0,0),(len(baseline.failures),len(baseline.errors)))
        original=owner.create_tokens;seen=[]
        def wrong(host,creator,*args,**kwargs):
            seen.append(creator);return original(host,"A",*args,**kwargs)
        changed=unittest.TestResult()
        with patch.object(owner,"create_tokens",wrong):type(self)(method).run(changed)
        self.assertEqual(["B"],seen);self.assertEqual([],changed.errors);self.assertEqual(1,len(changed.failures))


class EnchantedCopyWithdrawalTests(EnchantedCopyNativeFixture,unittest.TestCase):
    def test_genuine_verified_fine_and_reference_parent_withdrawals_block_both_full_originals(self):
        from copy import deepcopy
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index,discover_tests
        raw=json.loads((ROOT/"quorune/rules/capability-registry.json").read_text(encoding="utf-8"))
        doc=read_evidence_document(ROOT/"platform/capability-evidence-declarations.json")
        known=set(discover_tests(ROOT).values())
        def verified(value):
            r=CapabilityRegistry(value)
            index=build_index(registry_value=value,declaration_source=doc,discovered_test_ids=known)
            r.mark_evidence_verified(validate_capability_evidence_index(index,registry=r));return r
        baseline=verified(raw);cards=[self.db.lookup(name) for name in
            ("Followed Footsteps","Mirrorhall Mimic // Ghastly Mimicry")]
        dependencies={}
        for card in cards:
            ir=compile_oracle_card(card,capability_registry=baseline,capability_profile="commander_review")
            self.assertEqual("exact",ir.status,ir.material_residuals)
            dependencies[card.oracle_id]=tuple(d for f in ir.faces for n in f.nodes for d in n.capability_dependencies)
        for cap in ("token.creation.enchanted_source_copy","token.creation.fixed_copy","object.reference.source_attachment_information"):
            changed=deepcopy(raw);next(r for r in changed["capabilities"] if r["id"]==cap).update(
                status="blocked",blockers=["focused enchanted-copy owner withdrawal"])
            withdrawn=verified(changed)
            for card in cards:
                self.assertIn(cap,dependencies[card.oracle_id])
                self.assertNotEqual("exact",compile_oracle_card(card,capability_registry=withdrawn,capability_profile="commander_review").status)
                self.assertIn("status:"+cap+":blocked",withdrawn.closure(dependencies[card.oracle_id],profile="commander_review").blockers)
            self.assertEqual("exact",compile_oracle_card(self.db.lookup("Grizzly Bears"),capability_registry=withdrawn).status)
            if cap!="token.creation.fixed_copy":
                self.assertEqual("exact",compile_oracle_card(token_record("Ordinary targeted copy",
                    "Create a token that's a copy of target creature you control.",707303499),
                    capability_registry=withdrawn).status)

class EventReferentCopyCompilerTests(unittest.TestCase):
    def test_event_it_is_the_same_closed_recipe_as_explicit_event_creature(self):
        from quorune.compiler.token_copy_templates import token_copy_recipe_template
        for event in ("permanent.enter","artifact.enter","creature.enter","creature.dies","permanent.graveyard","permanent.leave"):
            with self.subTest(event=event):
                kw=dict(source_name="Generic Observer",source_is_permanent=True,event=event)
                old=token_copy_recipe_template("Create a token that's a copy of that creature, except the token isn't legendary.",**kw)
                new=token_copy_recipe_template("Create a token that's a copy of it, except the token isn't legendary.",**kw)
                self.assertIsNotNone(new)
                self.assertEqual(old,new)
                self.assertEqual("event_object",new.effect["copy_spec"]["origin"])

    def test_it_without_an_owned_public_event_never_becomes_a_self_or_target_copy(self):
        from quorune.compiler.token_copy_templates import token_copy_recipe_template
        for event in (None,"resolve","activate","spell.cast","spell.cast.self","creature.attacks","player.draw_card"):
            with self.subTest(event=event):
                self.assertIsNone(token_copy_recipe_template("Create a token that's a copy of it, except it isn't legendary.",
                    source_name="Generic Observer",source_is_permanent=True,event=event))
        for body in ("Create a token that's a copy of it and draw a card.",
                     "Create a token that's a copy of it, except its power is equal to your life total."):
            self.assertIsNone(token_copy_recipe_template(body,source_name="Generic Observer",
                source_is_permanent=True,event="creature.enter"))

    def test_full_normalized_entry_pronoun_requires_event_and_copy_owners(self):
        from quorune.token_copy_recipes import TokenCopyRecipeSpec
        from quorune.rules.capabilities import load_default_capability_registry
        ir=compile_oracle_card(token_record("Generic Entry Copy",
            "Whenever another nontoken Dragon you control enters, create a token that's a copy of it, except the token isn't legendary.",27071801,type_line="Enchantment"),
            capability_registry=load_default_capability_registry(),capability_profile="commander_review")
        self.assertEqual("exact",ir.status,ir.material_residuals)
        node=next(n for n in ir.faces[0].nodes if n.kind=="triggered_ability")
        self.assertEqual("event_object",TokenCopyRecipeSpec.from_dict(node.effects[0]["copy_spec"]).origin)
        self.assertIn("token.creation.fixed_copy",node.capability_dependencies)
        self.assertIn("trigger.event.normalized_zone_change",node.capability_dependencies)
        self.assertEqual(1,node.span.line)

class EventReferentCopyNativeFixture(InheritedNativeFixture):
    @classmethod
    def setUpClass(cls):
        from quorune.carddb import CardDatabase
        from quorune.deck import DeckDefinition,DeckEntry
        from scripts.build_test_database import build_fixture_database
        cls.temporary=tempfile.TemporaryDirectory();folder=Path(cls.temporary.name)
        base=json.loads((ROOT/"tests/fixtures/bound-effect-program-cards.json").read_text(encoding="utf-8"))
        base["cards"]=[r for r in base["cards"] if r["name"]=="Generic Bound Plains"];base["rulings"]=[]
        path=folder/"base.json";path.write_text(json.dumps(base),encoding="utf-8")
        build_fixture_database([path,ROOT/"tests/fixtures/event-referent-copy-cards.json"],folder/"event-copy.sqlite3")
        cls.db=CardDatabase(folder/"event-copy.sqlite3");cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition("Event copy",[DeckEntry("Isamaru, Hound of Konda",1,"commander"),
            DeckEntry("Generic Bound Plains",30)],["Isamaru, Hound of Konda"])

    def event_tokens(self,s):
        return [c for c in s.state.cards.values() if c.is_token and c.zone=="battlefield"]


class EventReferentCopyNativeTests(EventReferentCopyNativeFixture,unittest.TestCase):
    def test_actual_paid_original_qualifies_only_other_controlled_nontoken_dragons_and_copies_nonlegendary(self):
        s=self.session(70720701)
        source=self.add(s.engine,"Miirym, Sentinel Wyrm",zone="hand")
        own=self.add(s.engine,"Rorix Bladewing",zone="hand",ref="event-copy-own-dragon")
        foreign=self.add(s.engine,"Rorix Bladewing",seat="B",ref="event-copy-foreign-dragon")
        blink=self.add(s.engine,"Cloudshift",seat="B",zone="hand")
        bear=self.add(s.engine,"Grizzly Bears",zone="hand")
        secret=self.add(s.engine,"Unsummon",seat="D",zone="hand",ref="event-copy-private")
        for card in (source,own,foreign,bear,blink):self.assert_closed(s,card)
        self.prepare(s,mana={"C":7,"G":2,"U":1,"R":4})
        s.state.players["B"].mana_pool.update({"W":1})
        self.checkpoint(s)
        self.cast_for(s,"A",source);self.assertIsNone(self.resolve(s))
        self.assertFalse(self.event_tokens(s))
        self.cast_for(s,"A",bear);self.assertIsNone(self.resolve(s))
        self.assertFalse(self.event_tokens(s))
        old_incarnation=foreign.logical_object_id
        self.cast_for(s,"B",blink,[foreign.ref]);self.assertIsNone(self.resolve(s))
        self.assertNotEqual(old_incarnation,s.state.cards[foreign.object_id].logical_object_id)
        self.assertFalse(self.event_tokens(s))
        for seat in "ABC":self.assertNotIn(secret.ref,json.dumps(s.packet("pilot:"+seat,full=True)))
        self.cast_for(s,"A",own);self.resolve_top(s)
        self.assertEqual(1,sum(i.kind=="triggered_ability" and i.source_object_id==source.object_id for i in s.state.stack))
        s=self.replay(s,load=True)
        self.assertIsNone(self.resolve(s))
        token,=self.event_tokens(s)
        self.assertEqual(("A","A"),(token.owner,token.controller))
        self.assertEqual(own.object_id,token.annotations["copied_from"])
        data=s.engine._copyable_characteristics(token)
        self.assertEqual("Rorix Bladewing",data["name"])
        self.assertNotIn("Legendary",data["type_line"])
        self.assertEqual((6,5),tuple(s.engine._numeric_stat(token.object_id,k) for k in ("power","toughness")))
        self.assertEqual(1,len(self.event_tokens(s)))
        self.replay(s,load=True)

    def test_full_original_binds_normally_through_the_canonical_adapter(self):
        from quorune.card_programs.adapters import compile_best_available_card_program
        from quorune.card_programs import bind_card_program_runtime
        card=self.db.lookup("Miirym, Sentinel Wyrm")
        program=compile_best_available_card_program(self.db,card,semantic_registry=SemanticRegistry(),
            capability_registry=self.registry,capability_profile="commander_review")
        self.assertTrue(bind_card_program_runtime(program,capability_registry=self.registry,
            profile="commander_review")["strict_capability_ready"])


    def choose_entry_copy(self, session, referent):
        decision = self.resolve(session)
        self.assertEqual("replacement.order", decision.kind)
        option, = (
            row for row in session.packet("pilot:A", full=True)["decision"]["ctx"]["options"]
            if referent.ref in row["id"] and not row["id"].startswith("decline:")
        )
        result = session.act("pilot:A", {"action_id": "choose", "replacement": option["id"]})
        self.assertTrue(result.ok, result.summary)

    def pending_copied_entrant(self, seed, *, double=False):
        session = self.session(seed)
        engine = session.engine
        source = self.add(engine, "Miirym, Sentinel Wyrm", zone="hand")
        dragon = self.add(engine, "Rorix Bladewing", seat="B", ref="event-copy-basis")
        bear = self.add(engine, "Grizzly Bears", seat="C")
        entrant = self.add(engine, "Clone", zone="hand")
        blink = self.add(engine, "Cloudshift", zone="hand")
        self.add(engine, "Parallel Lives", seat="B", ref="event-copy-foreign-doubler")
        if double:
            self.add(engine, "Parallel Lives", ref="event-copy-own-doubler")
        for card in (source, dragon, bear, entrant, blink):
            self.assert_closed(session, card)
        self.prepare(session, mana={"C": 6, "G": 1, "U": 2, "R": 1, "W": 1})
        self.checkpoint(session)
        self.cast_for(session, "A", source, reserve={"W": 1, "U": 1})
        self.assertIsNone(self.resolve(session))
        self.cast_for(session, "A", entrant, reserve={"W": 1})
        self.assertEqual(1, session.state.players["A"].mana_pool.get("W", 0))
        self.choose_entry_copy(session, dragon)
        self.assertEqual(1, len(session.state.stack))
        trigger = session.state.stack[-1]
        self.assertEqual(("triggered_ability", source.object_id, "A"),
                         (trigger.kind, trigger.source_object_id, trigger.controller))
        current = session.state.cards[entrant.object_id]
        self.assertEqual("Rorix Bladewing", engine._copyable_characteristics(current)["name"])
        self.assertEqual((current.ref, current.logical_object_id),
                         (trigger.context["card"], trigger.context["card_object_identity"]))
        self.assertFalse(self.event_tokens(session))
        return session, entrant, blink, bear

    def return_as_bear(self, session, entrant, blink, bear):
        old = session.state.cards[entrant.object_id].logical_object_id
        self.cast_for(session, "A", blink, [entrant.ref])
        self.choose_entry_copy(session, bear)
        current = session.state.cards[entrant.object_id]
        self.assertNotEqual(old, current.logical_object_id)
        self.assertEqual("Grizzly Bears", session.engine._copyable_characteristics(current)["name"])
        self.assertEqual(1, len(session.state.stack))
        from quorune.token_copy_references import COPY_REFERENCE_CONTEXT
        captured = session.state.stack[-1].context[COPY_REFERENCE_CONTEXT]["event_object"]
        self.assertEqual(old, captured["logical_object_id"])
        self.assertEqual("Rorix Bladewing", captured["snapshot"]["characteristics"]["name"])

    def assert_entrant_copies(self, session, entrant, count):
        tokens = self.event_tokens(session)
        self.assertEqual(count, len(tokens))
        for card in tokens:
            self.assertEqual(("A", "A"), (card.owner, card.controller))
            self.assertEqual(entrant.object_id, card.annotations["copied_from"])
            characteristics = session.engine._copyable_characteristics(card)
            self.assertEqual("Rorix Bladewing", characteristics["name"])
            self.assertNotIn("Legendary", characteristics["type_line"])

    def test_paid_copy_entrant_uses_live_copiable_values_and_creators_replacement(self):
        session, entrant, _blink, _bear = self.pending_copied_entrant(70720702, double=True)
        self.assertIsNone(self.resolve(session))
        self.assert_entrant_copies(session, entrant, 2)
        self.replay(session, load=True)

    def test_paid_blinked_entrant_keeps_departure_copy_values_across_load_and_replay(self):
        session, entrant, blink, bear = self.pending_copied_entrant(70720703)
        self.return_as_bear(session, entrant, blink, bear)
        session = self.replay(session, load=True)
        self.assertIsNone(self.resolve(session))
        self.assert_entrant_copies(session, entrant, 1)
        self.replay(session, load=True)

    def test_actual_post_copy_fault_restores_full_state_and_loads_before_retry(self):
        from quorune import token_creation as owner
        session, entrant, _blink, _bear = self.pending_copied_entrant(70720704, double=True)
        original = owner.create_tokens
        seen = []

        def fail_after_group(*args, **kwargs):
            seen.extend(original(*args, **kwargs))
            raise GameRuleError("Injected event-copy fault after token group")

        with patch.object(owner, "create_tokens", side_effect=fail_after_group):
            for _ in range(12):
                principal = session.pending_principals()[0]
                before = authoritative_state_hash(session.state)
                result = session.act(principal, {"action_id": "pass"})
                if not result.ok:
                    self.assertIn("Injected event-copy fault", result.summary)
                    break
            else:
                self.fail("The canonical token creation group was not reached")
        self.assertEqual(2, len(seen))
        self.assertEqual(before, authoritative_state_hash(session.state))
        self.assertFalse(self.event_tokens(session))
        session = self.replay(session, load=True)
        self.assertTrue(session.act(principal, {"action_id": "pass"}).ok)
        self.assertIsNone(self.resolve(session))
        self.assert_entrant_copies(session, entrant, 2)
        self.replay(session, load=True)

    def test_native_reentry_substitution_mutant_changes_the_copied_values(self):
        from quorune import token_copy_runtime as runtime
        from quorune.token_copy_references import _snapshot
        original = runtime.copy_reference_snapshot
        seen = []

        def substitute(host, item, recipe, *, target_index=None, use_phased_lki=False):
            bound = original(host, item, recipe, target_index=target_index, use_phased_lki=use_phased_lki)
            if recipe.origin != "event_object":
                return bound
            current = host.state.cards[bound["object_id"]]
            seen.append((bound["logical_object_id"], current.logical_object_id))
            return _snapshot(host, current)

        outcome = unittest.TestResult()
        with patch.object(runtime, "copy_reference_snapshot", substitute):
            type(self)("test_paid_blinked_entrant_keeps_departure_copy_values_across_load_and_replay").run(outcome)
        self.assertEqual([], outcome.errors)
        self.assertEqual(1, len(seen))
        self.assertNotEqual(*seen[0])
        self.assertEqual(1, len(outcome.failures))
        self.assertIn("Grizzly Bears", outcome.failures[0][1])


class EventReferentCopyWithdrawalTests(EventReferentCopyNativeFixture, unittest.TestCase):
    def test_genuinely_verified_copy_and_event_withdrawals_block_complete_original(self):
        from quorune.card_programs import bind_card_program_runtime
        from quorune.card_programs.adapters import compile_best_available_card_program
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests

        raw = json.loads(CAPABILITY_REGISTRY_PATH.read_text(encoding="utf-8"))
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        tests = set(discover_tests(ROOT).values())
        card = self.db.lookup("Miirym, Sentinel Wyrm")

        def registry_with_verified_index(value):
            registry = CapabilityRegistry(value)
            index = build_index(registry_value=value, declaration_source=declarations, discovered_test_ids=tests)
            registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
            return registry

        def binding(record, registry):
            program = compile_best_available_card_program(
                self.db, record, semantic_registry=SemanticRegistry(), capability_registry=registry,
                capability_profile="commander_review",
            )
            return bind_card_program_runtime(program, capability_registry=registry, profile="commander_review")

        baseline = registry_with_verified_index(raw)
        self.assertTrue(binding(card, baseline)["strict_capability_ready"])
        for capability in ("token.creation.fixed_copy", "trigger.event.normalized_zone_change"):
            with self.subTest(capability=capability):
                updated = copy.deepcopy(raw)
                owner = next(row for row in updated["capabilities"] if row["id"] == capability)
                owner.update(status="blocked", blockers=["Focused event-copy owner withdrawal"])
                registry = registry_with_verified_index(updated)
                self.assertFalse(binding(card, registry)["strict_capability_ready"])
                self.assertTrue(binding(self.db.lookup("Grizzly Bears"), registry)["strict_capability_ready"])
