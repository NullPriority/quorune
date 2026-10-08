from __future__ import annotations

import copy
import json
from pathlib import Path
import tempfile
import unittest

from common import ROOT, keep_all, make_session
from quorune.carddb import CardDatabase, CardRecord
from quorune.compiler.fixed_target_effect_sequences import (
    FIXED_SOURCE_CHARACTERISTIC_MECHANIC,
    fixed_source_characteristics_effect_template,
)
from quorune.continuous_effects import (
    CharacteristicState, ContinuousEffect, ContinuousEffectError,
    ContinuousOperation, Layer, evaluate_continuous_effects,
)
from quorune.resolution_characteristic_model import (
    FixedResolutionCharacteristicsSpec, fixed_resolution_characteristic_instruction,
)
from quorune.continuous_effect_state import (
    expire_end_of_turn_continuous_effects,
)
from quorune.deck import DeckLoader
from quorune.errors import GameRuleError
from quorune.model import CardInstance, CombatState
from quorune.oracle_ir import compile_oracle_card, register_generated_programs
from quorune.projection import StateProjector
from quorune.record import (
    authoritative_state_hash,
    checkpoint_envelope,
    replay_record,
)
from quorune.rules.capabilities import (
    capability_dependencies_for_node,
    load_default_capability_registry,
)
from quorune.semantic_runtime.mana_abilities import FixedActivatedManaAbilityHandler
from quorune.session import CommanderSession
from scripts.build_test_database import build_fixture_database


FIXTURE = ROOT / "tests" / "fixtures" / "fixed-source-characteristic-effects.json"
SOURCE_CAPABILITY = (
    "continuous.resolution.fixed_source_characteristics_until_end_of_turn"
)
TARGET_CAPABILITY = "continuous.resolution.fixed_characteristics_until_end_of_turn"


def record(
    oracle_text: str,
    *,
    type_line: str = "Creature — Test",
) -> CardRecord:
    creature = "Creature" in type_line
    return CardRecord(
        oracle_id="19000000-0000-4000-8000-000000000199",
        name="Fixed Source Characteristic Fixture",
        mana_cost="{2}",
        mana_value=2.0,
        type_line=type_line,
        oracle_text=oracle_text,
        power="2" if creature else None,
        toughness="2" if creature else None,
        loyalty=None,
        defense=None,
        colors=("R",) if creature else (),
        color_identity=("R",),
        keywords=(),
        produced_mana=(),
        layout="normal",
        released_at="2026-01-01",
        legalities={"commander": "legal"},
        faces=(),
        raw={},
    )


class FixedSourceCharacteristicCompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.capabilities = load_default_capability_registry()

    def compile(self, text: str, *, type_line: str = "Creature — Test"):
        return compile_oracle_card(
            record(text, type_line=type_line),
            capability_registry=self.capabilities,
            capability_profile="commander_review",
        )

    def test_fixed_animation_and_base_setting_share_context_and_target_owners(self):
        cases = (
            ("{1}: This land becomes a 2/1 blue Faerie creature with flying until end of turn. It's still a land.","Land — Forest",True,False),
            ("Target land you control becomes a 3/3 Elemental creature until end of turn. It's still a land.","Instant",True,False),
            ("Until end of turn, target creature loses all abilities and becomes a blue Frog with base power and toughness 1/1.","Instant",False,True),
            ("Target creature has base power and toughness 4/4 until end of turn.","Instant",False,False),
            ("Whenever you cast a creature spell, this enchantment becomes a 4/4 Illusion creature with flying in addition to its other types until end of turn.","Enchantment",True,False),
            ("Creatures your opponents control have base power and toughness 0/1 until end of turn.","Instant",False,False),
            ("Until end of turn, target artifact or creature becomes a Dinosaur artifact creature with base power and toughness 4/3 in addition to its other types.","Instant",True,False),
        )
        for text,type_line,retains,removes in cases:
            with self.subTest(text=text):
                compiled=self.compile(text,type_line=type_line)
                self.assertEqual('exact',compiled.status,compiled.to_dict())
                node=compiled.faces[0].nodes[0]
                spec,_=fixed_resolution_characteristic_instruction(node.effects[0])
                self.assertEqual(retains,spec.retain_types)
                self.assertEqual(removes,spec.remove_all_abilities)
                self.assertIn(SOURCE_CAPABILITY,node.capability_dependencies)
                self.assertEqual(text,compiled.faces[0].oracle_text[node.span.start:node.span.end])

    def test_fixed_animation_near_misses_remain_material(self):
        cases=(
            "Target land becomes an X/X creature until end of turn. It's still a land.",
            "Target land becomes a 2/2 creature until your next turn. It's still a land.",
            "Target creature becomes a copy of another creature until end of turn.",
            "Target land becomes a 2/2 creature with banding until end of turn. It's still a land.",
            "Target land becomes a 2/2 creature until end of turn. It can't be blocked this turn.",
            "Target creature has base power and toughness 2/2 until end of turn, then draw the chosen number of cards.",
            "Target creature becomes a blue Robot with base power and toughness 2/2 in addition to its other colors and types until end of turn.",
            "Target land becomes a 2/2 mystery creature until end of turn. It's still a land.",
        )
        for text in cases:
            with self.subTest(text=text):
                compiled=self.compile(text,type_line='Instant')
                self.assertNotEqual('exact',compiled.status)
                self.assertTrue(compiled.material_residuals)

    def test_base_setting_draw_composition_is_a_positive_integration_witness(self):
        text = "Target creature has base power and toughness 2/2 until end of turn, then draw a card."
        compiled = self.compile(text, type_line="Instant")
        self.assertEqual("exact", compiled.status, compiled.material_residuals)
        node = compiled.faces[0].nodes[0]
        self.assertEqual(2, len(node.effects))
        spec, _ = fixed_resolution_characteristic_instruction(node.effects[0])
        self.assertEqual((2, 2), (spec.base_power, spec.base_toughness))
        self.assertEqual("draw", node.effects[1]["op"])
        self.assertIn(SOURCE_CAPABILITY, node.capability_dependencies)
        self.assertEqual(text, compiled.faces[0].oracle_text[node.span.start:node.span.end])

    def test_fixed_animation_versioned_schema_rejects_nonboolean_and_open_fields(self):
        compiled=self.compile('Target land becomes a 2/2 creature until end of turn. It\'s still a land.',type_line='Instant')
        effect=compiled.faces[0].nodes[0].effects[0]
        for field,value in (('retain_types',1),('retain_creature_subtypes','true'),('remove_all_abilities',None),('base_power',True),('keywords',['Banding']),('schema_version',True)):
            with self.subTest(field=field):
                mutated=copy.deepcopy(effect);mutated['characteristics'][field]=value
                with self.assertRaises(ValueError):fixed_resolution_characteristic_instruction(mutated)
                self.assertNotIn(SOURCE_CAPABILITY,capability_dependencies_for_node(effects=(mutated,),target_schema=compiled.faces[0].nodes[0].target_schema,mechanic_ids=compiled.faces[0].nodes[0].mechanics))
        mutated=copy.deepcopy(effect);mutated['extra']='open'
        with self.assertRaises(ValueError):fixed_resolution_characteristic_instruction(mutated)

    def test_animation_carrier_mana_node_declares_registered_dependency_contract(self):
        # An ordinary mana sibling must bind before an animated land can be
        # claimed fully closed. This does not alter mana spending permissions.
        compiled=self.compile('{T}: Add {U}.',type_line='Land')
        self.assertEqual('exact',compiled.status,compiled.to_dict())
        node=compiled.faces[0].nodes[0]
        self.assertLessEqual(set(FixedActivatedManaAbilityHandler().capability_dependencies),set(node.capability_dependencies))
        for text in ('{T}: Add {G}.','{T}: Add {G}. Spend this mana only to cast a creature spell.'):
            ir=self.compile(text,type_line='Land')
            self.assertEqual('exact',ir.status,ir.to_dict())
            self.assertLessEqual(set(FixedActivatedManaAbilityHandler().capability_dependencies),set(ir.faces[0].nodes[0].capability_dependencies))

    def test_fixed_animation_does_not_promote_unbound_sibling(self):
        from quorune.card_programs.adapters import compile_best_available_card_program
        from quorune.card_programs import bind_card_program_runtime
        from quorune.semantics import SemanticRegistry
        generic=record("{1}: This land becomes a 2/1 creature until end of turn. It's still a land.\nWhenever a creature is remembered by the moon, draw a card.",type_line='Land')
        program=compile_best_available_card_program(self, generic,semantic_registry=SemanticRegistry(),capability_registry=self.capabilities,capability_profile='commander_review')
        self.assertFalse(bind_card_program_runtime(program,capability_registry=self.capabilities,profile='commander_review')['strict_capability_ready'])

    def test_animation_and_mana_with_unrepresented_prevention_fail_closed(self):
        from quorune.card_programs.adapters import compile_best_available_card_program
        from quorune.card_programs import bind_card_program_runtime
        from quorune.semantics import SemanticRegistry
        prevention='Prevent all damage that would be dealt to this permanent by red spells.'
        for source in ("{1}: This land becomes a 2/1 creature until end of turn. It's still a land.",'{T}: Add {G}. Spend this mana only to cast a creature spell.'):
            with self.subTest(source=source):
                generic=record(source+'\n'+prevention,type_line='Land')
                ir=compile_oracle_card(generic,capability_registry=self.capabilities,capability_profile='commander_review')
                self.assertTrue(ir.faces[0].nodes[0].exact)
                self.assertNotEqual('exact',ir.status)
                self.assertTrue(ir.material_residuals)
                program=compile_best_available_card_program(self,generic,semantic_registry=SemanticRegistry(),capability_registry=self.capabilities,capability_profile='commander_review')
                binding=bind_card_program_runtime(program,capability_registry=self.capabilities,profile='commander_review')
                self.assertFalse(binding['strict_capability_ready'],binding)
                self.assertFalse(binding['compatible_ready'],binding)

    def test_fixed_animation_generated_probe_requires_real_capability_closure(self):
        from scripts.work_selection_cohort_measurements import _bound_effect_program_measurement
        generic=record('Target creature has base power and toughness 4/4 until end of turn.',type_line='Instant')
        ir=self.compile(generic.oracle_text,type_line='Instant')
        ability={'face_id':'front','ability_id':ir.faces[0].nodes[0].node_id,'status':'unresolved','residuals':[{}]}
        frontier={'cards':[{'oracle_id':generic.oracle_id,'oracle_ir_status':'unresolved','abilities':[ability]}]}
        measurement=_bound_effect_program_measurement(frontier=frontier,bundle_id='bundle:fixed-resolution-animation',probe_id='fixed-resolution-animation-existing-owner-v1',cards_by_oracle_id={generic.oracle_id:generic},coverage={'minimum_complete_card_gain':50,'minimum_exact_ability_gain':100,'minimum_material_residual_reduction':100},cohort_fingerprint='0'*64,database=self)
        self.assertEqual(1,measurement['complete_card_gain'])
        self.assertEqual(1,measurement['exact_ability_gain'])
        self.assertFalse(measurement['grants_gameplay_trust'])
        self.assertEqual('retired_below_harvest_floor',measurement['decision'])

    @staticmethod
    def rulings(_record):return ()

    def test_fixed_animation_layer_operations_preserve_rule_distinctions(self):
        # Independently authored CR 205.1b / 613.4b expectations: retained
        # types do not erase Forest, while creature-type replacement erases Elf.
        def evaluate(spec):
            state=CharacteristicState('Generic',card_types={'Land','Creature'},subtypes={'Forest','Elf'},supertypes={'Legendary'},colors={'G'},abilities=['Flying'],power=7,toughness=8)
            effects=[ContinuousEffect(effect_id=f'fixed:{index}',source_id='source',layer=layer,sublayer=sublayer,timestamp=1,operations=operations)
                     for index,(layer,sublayer,operations) in enumerate(spec.layer_operations())]
            effects.append(ContinuousEffect(effect_id='counter-control',source_id='counter',layer=Layer.POWER_TOUGHNESS,sublayer='7c',timestamp=2,operations=(ContinuousOperation('modify_power_toughness',(1,1)),)))
            return evaluate_continuous_effects(state,effects).characteristics
        retained=evaluate(FixedResolutionCharacteristicsSpec(card_types=('Creature',),creature_subtypes=('Faerie',),retain_types=True,retain_creature_subtypes=True,base_power=2,base_toughness=1))
        self.assertEqual({'Land','Creature'},set(retained['card_types']))
        self.assertEqual({'Forest','Elf','Faerie'},set(retained['subtypes']))
        self.assertEqual((3,2),(retained['power'],retained['toughness']))
        artifact=evaluate(FixedResolutionCharacteristicsSpec(card_types=('Artifact','Creature'),creature_subtypes=('Faerie',),retain_types=True,base_power=2,base_toughness=1))
        self.assertEqual({'Land','Creature','Artifact'},set(artifact['card_types']))
        self.assertEqual({'Forest','Faerie'},set(artifact['subtypes']))
        replaced=evaluate(FixedResolutionCharacteristicsSpec(card_types=('Creature',),creature_subtypes=('Frog',),colors=('U',),remove_all_abilities=True,base_power=1,base_toughness=1))
        self.assertEqual({'Creature'},set(replaced['card_types']))
        self.assertEqual({'Frog'},set(replaced['subtypes']))
        self.assertEqual({'Legendary'},set(replaced['supertypes']))
        self.assertEqual({'U'},set(replaced['colors']))
        self.assertEqual([],replaced['abilities'])
        # The type component continues even though that same effect removes
        # the source's ability in layer 6 (CR 613.6), while later keyword
        # additions still use normal layer-6 timestamp ordering.
        spec=FixedResolutionCharacteristicsSpec(card_types=('Creature',),creature_subtypes=('Frog',),remove_all_abilities=True,keywords=('Haste',),base_power=0,base_toughness=1)
        state=CharacteristicState('Same effect',card_types={'Creature'},abilities=['Flying'],power=4,toughness=4)
        layers=[ContinuousEffect(effect_id=f'shared:{i}',source_id='source',layer=l,sublayer=s,timestamp=1,operations=o)for i,(l,s,o)in enumerate(spec.layer_operations())]
        layers.append(ContinuousEffect(effect_id='later-grant',source_id='other',layer=Layer.ABILITY,sublayer='6',timestamp=2,operations=(ContinuousOperation('add_ability','Vigilance'),)))
        actual=evaluate_continuous_effects(state,layers).characteristics
        self.assertEqual({'Haste','Vigilance'},set(actual['abilities']))
        self.assertEqual({'Frog'},set(actual['subtypes']))
        self.assertEqual((0,1),(actual['power'],actual['toughness']))
        for retained in (False,True):
            for base_power in (0,1,7):
                for counter_delta in (-1,0,2):
                    value=FixedResolutionCharacteristicsSpec(card_types=('Creature',),creature_subtypes=('Frog',),retain_types=retained,retain_creature_subtypes=retained,base_power=base_power,base_toughness=1)
                    self.assertEqual(base_power,value.base_power)
                    self.assertEqual('add_types'if retained else 'set_types',value.layer_operations()[0][2][0].op)
                    state=CharacteristicState('Property',power=9,toughness=9,card_types={'Creature'})
                    layers=[ContinuousEffect(effect_id=str(i),source_id='source',layer=l,sublayer=s,timestamp=1,operations=o)for i,(l,s,o)in enumerate(value.layer_operations())]
                    layers.append(ContinuousEffect(effect_id='delta',source_id='counter',layer=Layer.POWER_TOUGHNESS,sublayer='7c',timestamp=2,operations=(ContinuousOperation('modify_power_toughness',(counter_delta,counter_delta)),)))
                    actual=evaluate_continuous_effects(state,layers).characteristics
                    self.assertEqual(base_power+counter_delta,actual['power'])

    def test_fixed_animation_type_retention_mutant_is_killed(self):
        from unittest.mock import patch
        original=FixedResolutionCharacteristicsSpec.layer_operations
        def mutate(spec):
            return original(FixedResolutionCharacteristicsSpec(
                card_types=(('Creature',) if spec.card_types else None),creature_subtypes=spec.creature_subtypes,
                colors=spec.colors,remove_all_abilities=spec.remove_all_abilities,
                keywords=spec.keywords,base_power=spec.base_power,base_toughness=spec.base_toughness))
        with patch.object(FixedResolutionCharacteristicsSpec,'layer_operations',mutate):
            with self.assertRaises(AssertionError):self.test_fixed_animation_layer_operations_preserve_rule_distinctions()

    def test_compiled_animation_absent_and_explicit_subtypes_have_distinct_results(self):
        # CR 205.1a/b expectations are independent of the generated descriptor:
        # no creature-subtype instruction preserves Elf, but losing Land
        # removes Forest. An explicit Frog replaces only creature subtypes.
        cases = (
            ("Target creature becomes a 3/3 blue creature until end of turn.",
             None, {"Creature"}, {"Elf"}),
            ("Target creature becomes a 3/3 blue Frog creature until end of turn.",
             ("Frog",), {"Creature"}, {"Frog"}),
            ("Target creature becomes a 3/3 blue creature in addition to its other types until end of turn.",
             None, {"Land", "Creature"}, {"Forest", "Elf"}),
            ("Target land becomes a 3/3 blue Frog creature until end of turn. It's still a land.",
             ("Frog",), {"Land", "Creature"}, {"Forest", "Elf", "Frog"}),
            ("Target creature has base power and toughness 3/3 until end of turn.",
             None, {"Land", "Creature"}, {"Forest", "Elf"}),
            ("Target creature becomes a 3/3 blue artifact creature until end of turn.",
             None, {"Artifact", "Land", "Creature"}, {"Forest", "Elf"}),
            ("Target creature becomes a 3/3 blue Frog artifact creature until end of turn.",
             ("Frog",), {"Artifact", "Land", "Creature"}, {"Forest", "Frog"}),
        )
        for text, expected_instruction, expected_types, expected_subtypes in cases:
            with self.subTest(text=text):
                compiled = self.compile(text, type_line="Instant")
                self.assertEqual("exact", compiled.status, compiled.to_dict())
                spec, _ = fixed_resolution_characteristic_instruction(
                    compiled.faces[0].nodes[0].effects[0]
                )
                self.assertEqual(expected_instruction, spec.creature_subtypes)
                initial = CharacteristicState(
                    "Generic dual-type Elf", card_types={"Land", "Creature"},
                    subtypes={"Forest", "Elf"}, supertypes={"Legendary"},
                    abilities=["Vigilance"], power=2, toughness=4,
                )
                layers = [
                    ContinuousEffect(
                        effect_id=f"subtype-contrast:{index}", source_id="source",
                        layer=layer, sublayer=sublayer, timestamp=1,
                        operations=operations,
                    )
                    for index, (layer, sublayer, operations)
                    in enumerate(spec.layer_operations())
                ]
                for lower_case in (False, True):
                    if lower_case:
                        initial.card_types = {value.casefold() for value in initial.card_types}
                        initial.subtypes = {value.casefold() for value in initial.subtypes}
                    current = evaluate_continuous_effects(initial, layers).characteristics
                    self.assertEqual({value.casefold() for value in expected_types},
                                     {value.casefold() for value in current["card_types"]})
                    self.assertEqual({value.casefold() for value in expected_subtypes},
                                     {value.casefold() for value in current["subtypes"]})
                    self.assertEqual({"Legendary"}, set(current["supertypes"]))
                    self.assertEqual(["Vigilance"], current["abilities"])
                    self.assertEqual((3, 3), (current["power"], current["toughness"]))

    def test_unspecified_animation_removes_lost_noncreature_subtypes_only(self):
        spec = FixedResolutionCharacteristicsSpec(card_types=("Creature",), base_power=3, base_toughness=3)
        for old_type, old_subtype in (
            ("Land", "Forest"), ("Artifact", "Equipment"),
            ("Enchantment", "Shrine"), ("Planeswalker", "Jace"),
            ("Battle", "Siege"), ("Kindred", "Elf"),
        ):
            with self.subTest(old_type=old_type):
                initial = CharacteristicState(
                    "Generic typed Elf", card_types={"Creature", old_type},
                    subtypes={"Elf", old_subtype}, power=2, toughness=4,
                )
                effects = [
                    ContinuousEffect(effect_id=str(index), source_id="source", layer=layer,
                                     sublayer=sublayer, timestamp=1, operations=operations)
                    for index, (layer, sublayer, operations) in enumerate(spec.layer_operations())
                ]
                actual = evaluate_continuous_effects(initial, effects).characteristics
                self.assertEqual({"Creature"}, set(actual["card_types"]))
                self.assertEqual({"Elf"}, set(actual["subtypes"]))
        with self.assertRaisesRegex(ValueError, "closed creature card-type set"):
            FixedResolutionCharacteristicsSpec(card_types=("Creature", "Land"))

    def test_unspecified_subtype_clear_mutant_is_killed(self):
        from unittest.mock import patch

        original = FixedResolutionCharacteristicsSpec.layer_operations

        def mutate(spec):
            layers = original(spec)
            if spec.card_types is None or spec.retain_types or spec.creature_subtypes is not None:
                return layers
            return tuple(
                (layer, sublayer, operations + (ContinuousOperation("set_types", (), field="subtypes"),))
                if layer == Layer.TYPE else (layer, sublayer, operations)
                for layer, sublayer, operations in layers
            )

        # Call a direct expectation, not a subTest whose failure is swallowed.
        with patch.object(FixedResolutionCharacteristicsSpec, "layer_operations", mutate):
            spec = FixedResolutionCharacteristicsSpec(card_types=("Creature",))
            effects = [
                ContinuousEffect(effect_id=str(index), source_id="source", layer=layer,
                                 sublayer=sublayer, timestamp=1, operations=operations)
                for index, (layer, sublayer, operations) in enumerate(spec.layer_operations())
            ]
            actual = evaluate_continuous_effects(
                CharacteristicState("Generic", card_types={"Creature"}, subtypes={"Elf"}), effects
            ).characteristics
            with self.assertRaises(AssertionError):
                self.assertEqual({"Elf"}, set(actual["subtypes"]))

    def test_type_subtraction_matches_canonical_query_case(self):
        # Live type_parts yields lower-case words; typed operations use title
        # case. Both name the same rules subtype, including typographic quotes.
        for subtypes in ({"elf", "forest", "urza’s"}, {"Elf", "Forest", "Urza's"}):
            state = CharacteristicState("Generic", card_types={"Creature"}, subtypes=subtypes)
            effect = ContinuousEffect(
                effect_id="remove-land-type", source_id="source", layer=Layer.TYPE,
                sublayer="4", timestamp=1,
                operations=(ContinuousOperation("remove_types", ("Forest", "Urza's"), field="subtypes"),),
            )
            actual = evaluate_continuous_effects(state, (effect,)).characteristics
            self.assertEqual({"elf"}, {value.casefold() for value in actual["subtypes"]})

    def test_source_characteristics_compile_across_shared_contexts(self):
        cases = (
            (
                "{1}: This creature gets +2/+0 and gains first strike until end of turn.",
                "Creature — Warrior",
                "activated_ability",
                {"combat.damage.participation.strike_steps"},
            ),
            (
                "Whenever you cast a noncreature spell, this creature gains vigilance and lifelink until end of turn.",
                "Creature — Soldier",
                "triggered_ability",
                {"combat.attack.vigilance", "damage.result.lifelink"},
            ),
            (
                "{1}: This creature becomes colorless until end of turn.",
                "Creature — Kavu",
                "activated_ability",
                set(),
            ),
            (
                "{2}: This artifact becomes a 2/2 white and blue Bird artifact creature with flying until end of turn.",
                "Artifact",
                "activated_ability",
                {"combat.block.flying"},
            ),
            (
                "{3}: This artifact becomes a Shapeshifter artifact creature with base power and toughness 5/5 until end of turn.",
                "Artifact",
                "activated_ability",
                set(),
            ),
            (
                "{3}: This artifact becomes a 4/4 artifact creature until end of turn.",
                "Artifact — Clue",
                "activated_ability",
                set(),
            ),
            (
                "Target creature gains shadow until end of turn.",
                "Instant",
                "spell_ability",
                {"combat.block.shadow", "target.revalidate_resolution"},
            ),
        )
        for text, type_line, kind, additional in cases:
            with self.subTest(text=text):
                compiled = self.compile(text, type_line=type_line)
                self.assertEqual("exact", compiled.status, compiled.to_dict())
                node = compiled.faces[0].nodes[0]
                self.assertEqual(kind, node.kind)
                self.assertTrue(node.exact)
                expected_capability = (
                    TARGET_CAPABILITY
                    if text.startswith("Target creature")
                    else SOURCE_CAPABILITY
                )
                self.assertIn(
                    expected_capability,
                    node.capability_dependencies,
                )
                self.assertLessEqual(
                    additional,
                    set(node.capability_dependencies),
                )
                self.assertEqual(
                    text,
                    compiled.faces[0].oracle_text[
                        node.span.start : node.span.end
                    ],
                )

    def test_source_characteristic_grammar_keeps_open_forms_residual(self):
        cases = (
            (
                "{1}: This creature gets +X/+X and gains trample until end of turn.",
                "Creature — Test",
            ),
            (
                "{1}: This creature gains your choice of flying or haste until end of turn.",
                "Creature — Test",
            ),
            (
                "{1}: This creature gains banding until end of turn.",
                "Creature — Test",
            ),
            (
                "{2}: This land becomes a 2/2 creature until your next turn. It's still a land.",
                "Land",
            ),
            (
                "{2}: This artifact becomes a 2/2 Bird artifact creature until end of turn and can't be blocked this turn.",
                "Artifact",
            ),
            (
                "{2}: This artifact becomes a copy of target creature until end of turn.",
                "Artifact",
            ),
            (
                "{2}: This artifact becomes a 2/2 Bird artifact creature until end of turn.",
                "Creature — Test",
            ),
        )
        for text, type_line in cases:
            with self.subTest(text=text):
                compiled = self.compile(text, type_line=type_line)
                self.assertNotEqual("exact", compiled.status)
                self.assertTrue(compiled.material_residuals)

    def test_source_characteristic_shape_is_closed_and_capability_bound(self):
        template = fixed_source_characteristics_effect_template(
            "This artifact becomes a 2/2 blue Bird artifact creature with flying until end of turn.",
            source_is_permanent=True,
            source_card_types=("artifact",),
        )
        self.assertIsNotNone(template)
        assert template is not None
        self.assertEqual(
            (SOURCE_CAPABILITY,),
            tuple(
                capability
                for capability in capability_dependencies_for_node(
                    effects=template.effects,
                    target_schema=None,
                    mechanic_ids=template.mechanics,
                )
                    if capability == SOURCE_CAPABILITY
            ),
        )
        effect = template.effects[0]
        mutations = (
            {**effect, "card": "$source"},
            {**effect, "set_card_types": ["Artifact", "Artifact"]},
            {**effect, "set_colors": ["U", "W"]},
            {**effect, "base_toughness": None},
            {**effect, "power": {"kind": "dynamic"}},
            {**effect, "keywords": ["Flying", "Flying"]},
            {**effect, "unknown": True},
        )
        for mutated in mutations:
            with self.subTest(mutated=mutated):
                self.assertNotIn(
                    SOURCE_CAPABILITY,
                    capability_dependencies_for_node(
                        effects=(mutated,),
                        target_schema=None,
                        mechanic_ids=template.mechanics,
                    ),
                )
        self.assertEqual(
            (),
            tuple(
                ContinuousOperation(
                    "set_types",
                    [],
                    field="subtypes",
                ).value
            ),
        )
        with self.assertRaises(ContinuousEffectError):
            ContinuousOperation("set_types", [], field="card_types")


class AnimationSubtypeActionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from quorune.deck import DeckDefinition, DeckEntry

        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / "animation-subtypes.sqlite3"
        build_fixture_database(
            [ROOT / "tests/fixtures/animation-subtype-cards.json"], path
        )
        cls.db = CardDatabase(path)
        cls.deck = DeckDefinition(
            "Generic subtype review deck",
            [DeckEntry("Generic Subtype Review Commander", 1, "commander"),
             DeckEntry("Generic Subtype Review Island", 15)],
            ["Generic Subtype Review Commander"],
        )

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    def session(self, seed):
        from quorune.model import GameConfig

        session = CommanderSession.create(
            self.db, {seat: copy.deepcopy(self.deck) for seat in "ABCD"},
            first_player="A", seed=seed,
            config=GameConfig(seed=seed, auto_pass_empty_priority=False),
        )
        keep_all(session)
        engine = session.engine
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine.state.priority_player = None
        engine.state.priority_passes = []
        register_generated_programs(
            self.db, engine.semantics, tuple(self.db.iter_cards()),
            trust_level="trusted", capability_registry=load_default_capability_registry(),
            capability_profile="commander_review", promote_exact_runtime_handlers=True,
            promote_exact_trigger_programs=True, promote_exact_effect_programs=True,
            promote_exact_capability_declarations=True,
        )
        return session

    def add(self, engine, name, ref, *, zone="battlefield", seat="A"):
        row = self.db.lookup(name)
        card = CardInstance(
            object_id=f"animation-subtype:{ref}", ref=ref, oracle_id=row.oracle_id,
            printed_name=row.name, owner=seat, controller=seat, zone=zone,
            zone_timestamp=engine._next_zone_timestamp(),
            known_to=list("ABCD") if zone == "battlefield" else [seat],
        )
        engine.state.cards[card.object_id] = card
        engine.state.players[seat].zones[zone].append(card.object_id)
        return card

    def cast(self, session, spell, *, target=None):
        engine = session.engine
        programs = engine.semantics.programs_for_oracle(spell.oracle_id)
        self.assertTrue(programs)
        self.assertTrue(all(engine.semantic_program_is_current_trusted(p) for p in programs))
        engine.state.active_player = "A"
        engine.state.started = True
        engine.state.phase = "precombat_main"
        engine.state.step = "main"
        engine.state.players["A"].mana_pool["U"] = 1
        engine._grant_priority("A")
        engine.pump()
        action = next(
            row for row in session.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
            if row["id"] == f"cast:{spell.ref}"
        )
        for seat in "BCD":
            self.assertIsNone(session.packet(f"pilot:{seat}", full=True)["decision"])
        if target is not None:
            self.assertIn(target.ref, action["target_schema"]["legal_refs"])
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()
        command = {"action_id": action["id"], "pay": "auto"}
        if target is not None:
            command["targets"] = [target.ref]
        result = session.act("pilot:A", command)
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(0, sum(engine.state.players["A"].mana_pool.values()))

    def resolve(self, session):
        for _ in range(8):
            if not session.engine.state.stack:
                return
            result = session.act(session.pending_principals()[0], {"action_id": "pass"})
            self.assertTrue(result.ok, result.summary)
        self.fail("Generic animation did not resolve")

    def assert_replay(self, session):
        expected = authoritative_state_hash(session.state)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "animation-subtype-record"
            session.save(path)
            self.assertEqual(
                expected, authoritative_state_hash(CommanderSession.load(self.db, path).state)
            )
            result = replay_record(path, self.db, verify=True)
        self.assertTrue(result["ok"], result)
        self.assertEqual(expected, result["final_state_hash"])

    def assert_elf_query(self, engine, card):
        from quorune.object_predicate import ObjectQuerySpec
        from quorune.object_query import object_matches_query, object_query_result

        effective = engine._effective_card_data(card)
        row = object_query_result(
            card, effective, type_parts=engine._type_parts(effective["type_line"]),
            known_to_actor=True, attached_to_ref=None,
        )
        self.assertTrue(object_matches_query(
            row, ObjectQuerySpec(zones=("battlefield",), subtypes_all=("elf",))
        ))
        return row

    def test_trusted_unspecified_animation_preserves_elf_query_counters_and_replay(self):
        session = self.session(240457001)
        engine = session.engine
        target = self.add(engine, "Generic Subtype Review Elf", "ELF-TARGET")
        target.counters["+1/+1"] = 1
        spell = self.add(
            engine, "Generic Unspecified Creature Animation", "ANIMATION-SPELL", zone="hand"
        )
        self.cast(session, spell, target=target)
        self.resolve(session)
        current = engine._effective_card_data(target)
        self.assertEqual({"creature"}, engine._type_parts(current["type_line"])[0])
        self.assertEqual({"elf"}, engine._type_parts(current["type_line"])[1])
        self.assertEqual(("3", "3"), (current["power"], current["toughness"]))
        self.assertEqual(["U"], current["colors"])
        self.assertIn("Vigilance", current["keywords"])
        row = self.assert_elf_query(engine, target)
        self.assertEqual((4, 4), (row.effective_power, row.effective_toughness))
        self.assertEqual({"+1/+1": 1}, target.counters)
        self.assertEqual("graveyard", spell.zone)
        for viewer in "ABCD":
            public = json.dumps(StateProjector(self.db, engine.state)._snapshot(f"pilot:{viewer}"))
            self.assertIn(target.ref, public)
            self.assertNotIn("continuous_effects", public)
            for seat in "ABCD":
                if seat != viewer:
                    for object_id in engine.state.players[seat].zones["hand"]:
                        self.assertNotIn(engine.state.cards[object_id].ref, public)
        self.assert_replay(session)
        expire_end_of_turn_continuous_effects(engine.state)
        restored = engine._effective_card_data(target)
        self.assertEqual(("2", "4"), (restored["power"], restored["toughness"]))
        self.assertEqual({"elf"}, engine._type_parts(restored["type_line"])[1])
        row = self.assert_elf_query(engine, target)
        self.assertEqual((3, 5), (row.effective_power, row.effective_toughness))

    def test_trusted_unspecified_set_locks_membership_control_and_incarnations(self):
        session = self.session(240457002)
        engine = session.engine
        first = self.add(engine, "Generic Subtype Review Land Elf", "SET-FIRST")
        second = self.add(engine, "Generic Subtype Review Elf", "SET-SECOND")
        outsider = self.add(engine, "Generic Subtype Review Elf", "SET-OUTSIDER", seat="B")
        late = self.add(engine, "Generic Subtype Review Elf", "SET-LATE", zone="hand")
        spell = self.add(engine, "Generic Unspecified Creature Set", "SET-SPELL", zone="hand")
        self.cast(session, spell)
        self.resolve(session)
        self.assert_elf_query(engine, first)
        self.assertEqual({"creature"}, engine._type_parts(engine._effective_card_data(first)["type_line"])[0])
        self.assertEqual({"elf"}, engine._type_parts(engine._effective_card_data(first)["type_line"])[1])
        self.assertEqual("2", engine._effective_card_data(outsider)["power"])
        self.assert_replay(session)
        # These post-replay owner diagnostics are not recorded commands.
        engine.change_control(first.object_id, "D", reason="locked animation control contrast")
        self.assertEqual("3", engine._effective_card_data(first)["power"])
        self.assert_elf_query(engine, first)
        engine.move_card(late.object_id, "battlefield", reason="late entry contrast")
        self.assertEqual("2", engine._effective_card_data(late)["power"])
        old_identity = second.logical_object_id
        engine.move_card(second.object_id, "hand", reason="locked animation departure")
        engine.move_card(second.object_id, "battlefield", reason="locked animation reentry")
        self.assertNotEqual(old_identity, second.logical_object_id)
        self.assertEqual("2", engine._effective_card_data(second)["power"])
        expire_end_of_turn_continuous_effects(engine.state)
        restored = engine._effective_card_data(first)
        self.assertEqual({"land", "creature"}, engine._type_parts(restored["type_line"])[0])
        self.assertEqual({"forest", "elf"}, engine._type_parts(restored["type_line"])[1])
        self.assertEqual("D", first.controller)

    def test_trusted_unspecified_animation_rejects_stale_reentered_target(self):
        session = self.session(240457003)
        engine = session.engine
        target = self.add(engine, "Generic Subtype Review Elf", "STALE-ELF")
        spell = self.add(engine, "Generic Unspecified Creature Animation", "STALE-SPELL", zone="hand")
        self.cast(session, spell, target=target)
        old_identity = target.logical_object_id
        engine.move_card(target.object_id, "hand", reason="old target departure")
        engine.move_card(target.object_id, "battlefield", reason="new target incarnation")
        self.assertNotEqual(old_identity, target.logical_object_id)
        self.resolve(session)
        self.assertEqual("2", engine._effective_card_data(target)["power"])
        self.assert_elf_query(engine, target)
        self.assertFalse([
            effect for effect in engine.state.continuous_effects
            if any(identity.object_id == target.object_id for identity in effect.locked_objects)
        ])


class FixedSourceCharacteristicRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temporary = tempfile.TemporaryDirectory()
        database = Path(cls.temporary.name) / "source-characteristics.sqlite3"
        build_fixture_database(
            [
                ROOT / "tests" / "fixtures" / "scryfall-exact-lists.json",
                ROOT
                / "tests"
                / "fixtures"
                / "query-power-toughness-definition-cards.json",
                FIXTURE,
                ROOT / "tests" / "fixtures" / "source-reference-closure-cards.json",
            ],
            database,
        )
        cls.db = CardDatabase(database)
        loader = DeckLoader(cls.db)
        cls.mishra = loader.load(
            ROOT / "examples" / "mishra-eminent-one.txt",
            commander="Mishra, Eminent One",
            deck_name="Mishra",
        )
        cls.zimone = loader.load(
            ROOT / "examples" / "zimone-and-dina.txt",
            commander="Zimone and Dina",
            deck_name="Zimone",
        )
        cls.capabilities = load_default_capability_registry()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.db.close()
        cls.temporary.cleanup()

    def session(self, seed: int, *, players: int = 2):
        session = make_session(
            self.db,
            self.mishra,
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
        engine.state.stack.clear()
        session.commands.clear()
        session.decisions.clear()
        return session

    def add_card(
        self,
        session,
        *,
        name: str,
        ref: str,
        seat: str = "A",
        zone: str = "battlefield",
        register: bool = True,
        trusted: bool = False,
    ) -> CardInstance:
        engine = session.engine
        record_value = self.db.lookup(name)
        card = CardInstance(
            object_id=f"source-characteristic:{ref}",
            ref=ref,
            oracle_id=record_value.oracle_id,
            printed_name=record_value.name,
            owner=seat,
            controller=seat,
            zone=zone,
            zone_timestamp=engine.state.timestamp_sequence + 1,
            known_to=(list(engine.seats) if zone != "hand" else [seat]),
            revealed_to=(list(engine.seats) if zone != "hand" else []),
        )
        engine.state.cards[card.object_id] = card
        engine.state.players[seat].zones[zone].append(card.object_id)
        if register:
            register_generated_programs(
                self.db,
                engine.semantics,
                (record_value,),
                trust_level="trusted" if trusted else "provisional",
                capability_registry=self.capabilities,
                capability_profile="commander_review",
                promote_exact_runtime_handlers=True,
                promote_exact_trigger_programs=True,
                promote_exact_effect_programs=True,
                promote_exact_capability_declarations=True,
            )
        return card

    def assert_replay(self, session) -> None:
        expected=authoritative_state_hash(session.engine.state)
        with tempfile.TemporaryDirectory() as temporary:
            path=Path(temporary)/'fixed-animation-replay'
            session.save(path)
            loaded=CommanderSession.load(self.db,path)
            self.assertEqual(expected,authoritative_state_hash(loaded.engine.state))
            replay=replay_record(path,self.db,verify=True)
        self.assertTrue(replay['ok'],replay)
        self.assertEqual(expected,replay['final_state_hash'])

    def test_pinned_named_attack_chooses_legal_target_without_same_name_dispatch(self):
        session = self.session(26520151, players=4)
        engine = session.engine
        source = self.add_card(session, name="War Machine, James Rhodes", ref="NAMED-ATTACK", trusted=True)
        other = self.add_card(session, name="War Machine, James Rhodes", ref="SAME-NAME-IDLE", seat="C", trusted=True)
        target = self.add_card(session, name="Source Growth Fixture", ref="NAMED-TARGET", seat="B", trusted=True)
        source.temporary_keywords.append("Haste")
        engine.state.active_player = "A"
        engine.state.phase_index = 5
        engine.state.phase = "combat"
        engine.state.step = "declare_attackers"
        engine.state.combat = CombatState()
        engine._issue_attackers()
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()
        declared = session.act("pilot:A", {"a": "attack", "atk": {source.ref: "B"}})
        self.assertTrue(declared.ok, declared.summary)
        matching = [item for item in engine.state.stack if item.source_object_id in {source.object_id, other.object_id}]
        self.assertEqual(1, len(matching))
        self.assertEqual(source.object_id, matching[0].source_object_id)
        self.assertEqual(source.logical_object_id, matching[0].context["source_logical_object_id"])
        self.assertEqual("semantic.target", engine.state.pending_decision.kind)
        context = session.packet("pilot:A", full=True)["decision"]["ctx"]
        self.assertIn(target.ref, str(context["target_schema"]))
        before = authoritative_state_hash(engine.state)
        denied = session.act("pilot:B", {"a": "choose", "targets": [target.ref]})
        self.assertFalse(denied.ok)
        self.assertEqual(before, authoritative_state_hash(engine.state))
        invalid = session.act("pilot:A", {"a": "choose", "targets": ["UNKNOWN-NAMED-TARGET"]})
        self.assertFalse(invalid.ok)
        self.assertEqual(before, authoritative_state_hash(engine.state))
        target = engine.state.cards[target.object_id]
        other = engine.state.cards[other.object_id]
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "named-trigger-pending"
            session.save(path)
            loaded = CommanderSession.load(self.db, path)
            self.assertEqual(before, authoritative_state_hash(loaded.engine.state))
        chosen = session.act("pilot:A", {"a": "choose", "targets": [target.ref]})
        self.assertTrue(chosen.ok, chosen.summary)
        self.resolve_stack(session)
        self.assertTrue(target.tapped)
        self.assertFalse(other.tapped)
        self.assert_replay(session)

    def test_pinned_named_activation_tracks_incarnation_control_and_replays(self):
        session = self.session(26520152, players=4)
        engine = session.engine
        source = self.add_card(session, name="Akroma, Angel of Fury", ref="NAMED-PUMP", trusted=True)
        other = self.add_card(session, name="Akroma, Angel of Fury", ref="SAME-NAME-PUMP", seat="B", trusted=True)
        engine.state.players["A"].mana_pool["R"] = 1
        self.prepare_main(session)
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()
        offer = self.activation_offer(session, source)
        result = session.act("pilot:A", {"action_id": offer["id"]})
        self.assertTrue(result.ok, result.summary)
        self.resolve_stack(session)
        self.assertEqual(7, engine._numeric_stat(source.object_id, "power"))
        self.assertEqual(6, engine._numeric_stat(other.object_id, "power"))
        self.assert_replay(session)

        # A control change preserves this object; leaving and returning does not.
        expire_end_of_turn_continuous_effects(engine.state)
        engine.state.players["A"].mana_pool["R"] = 1
        self.prepare_main(session)
        result = session.act("pilot:A", {"action_id": self.activation_offer(session, source)["id"]})
        self.assertTrue(result.ok, result.summary)
        engine.change_control(source.object_id, "B", reason="named source control witness")
        self.resolve_stack(session)
        self.assertEqual(7, engine._numeric_stat(source.object_id, "power"))
        expire_end_of_turn_continuous_effects(engine.state)
        engine.change_control(source.object_id, "A", reason="named source reset")
        engine.state.players["A"].mana_pool["R"] = 1
        self.prepare_main(session)
        result = session.act("pilot:A", {"action_id": self.activation_offer(session, source)["id"]})
        self.assertTrue(result.ok, result.summary)
        old_identity = source.logical_object_id
        engine.move_card(source.object_id, "graveyard", log=False)
        engine.move_card(source.object_id, "battlefield", controller="A", log=False)
        self.assertNotEqual(old_identity, source.logical_object_id)
        self.resolve_stack(session)
        self.assertEqual(6, engine._numeric_stat(source.object_id, "power"))

    def test_trusted_land_animation_offer_payment_multilayer_result_and_replay(self):
        session=self.session(237020001,players=4);engine=session.engine
        source=self.add_card(session,name='Fixed Land Animation Fixture',ref='LAND-RESULT',trusted=True)
        programs=engine.semantics.programs_for_oracle(source.oracle_id)
        self.assertTrue(programs)
        self.assertTrue(all(engine.semantic_program_is_current_trusted(p) for p in programs))
        engine.state.players['A'].mana_pool['C']=1
        self.prepare_main(session)
        offer=self.activation_offer(session,source)
        for seat in ('B','C','D'):
            self.assertIsNone(session.packet(f'pilot:{seat}',full=True)['decision'])
        engine.state.players['A'].mana_pool['C']=0
        source.tapped=True
        for seat in engine.seats:
            for object_id in engine.state.players[seat].zones['battlefield']:
                engine.state.cards[object_id].tapped=True
        before=authoritative_state_hash(engine.state)
        declined=session.act('pilot:A',{'action_id':offer['id'],'pay':'auto'})
        self.assertFalse(declined.ok)
        self.assertEqual(before,authoritative_state_hash(engine.state))
        engine.state.players['A'].mana_pool['C']=1
        source.tapped=False
        self.prepare_main(session);offer=self.activation_offer(session,source)
        session.initial_checkpoint=checkpoint_envelope(engine.state);session.commands.clear();session.decisions.clear()
        result=session.act('pilot:A',{'action_id':offer['id'],'pay':'auto'})
        self.assertTrue(result.ok,result.summary)
        self.assertEqual(0,sum(engine.state.players['A'].mana_pool.values()))
        self.resolve_stack(session)
        current=engine._effective_card_data(source)
        types,subtypes,supertypes=engine._type_parts(current['type_line'])
        self.assertEqual({'land','creature'},types)
        self.assertEqual({'forest','faerie'},subtypes)
        self.assertIn('legendary',supertypes)
        self.assertEqual(['U'],current['colors'])
        self.assertEqual(('2','1'),(current['power'],current['toughness']))
        self.assertIn('Flying',current['keywords'])
        self.assertEqual(1,len({e.timestamp for e in engine.state.continuous_effects if e.source_id==source.object_id}))
        for viewer in engine.seats:
            snapshot=StateProjector(self.db,engine.state)._snapshot(f'pilot:{viewer}')
            serialized=json.dumps(snapshot)
            self.assertNotIn('continuous_effects',serialized)
            for seat in engine.seats:
                if seat==viewer:continue
                for object_id in engine.state.players[seat].zones['hand']:
                    self.assertNotIn(engine.state.cards[object_id].ref,serialized)
        self.assert_replay(session)
        expire_end_of_turn_continuous_effects(engine.state)
        self.assertEqual({'land'},engine._type_parts(engine._effective_card_data(source)['type_line'])[0])

    def test_trusted_frog_cast_revalidates_target_and_replays_full_result(self):
        session=self.session(237020002,players=4);engine=session.engine
        spell=self.add_card(session,name='Fixed Frog Result Fixture',ref='FROG-SPELL',zone='hand',trusted=True)
        target=self.add_card(session,name='Source Keywords Fixture',ref='FROG-TARGET',seat='B',trusted=True)
        target.temporary_keywords=['Flying']
        engine.state.players['A'].mana_pool['U']=1
        self.prepare_main(session);engine.pump()
        decision=session.packet('pilot:A',full=True)['decision']
        action=next(a for a in decision['ctx']['legal']['actions'] if a['id']==f'cast:{spell.ref}')
        self.assertIn(target.ref,action['target_schema']['legal_refs'])
        session.initial_checkpoint=checkpoint_envelope(engine.state);session.commands.clear();session.decisions.clear()
        result=session.act('pilot:A',{'action_id':action['id'],'targets':[target.ref],'pay':'auto'})
        self.assertTrue(result.ok,result.summary)
        self.resolve_stack(session)
        current=engine._effective_card_data(target)
        self.assertEqual({'frog'},engine._type_parts(current['type_line'])[1])
        self.assertEqual(('1','1'),(current['power'],current['toughness']))
        self.assertEqual(['U'],current['colors'])
        self.assertNotIn('Flying',current['keywords'])
        self.assertEqual((),engine._activated_abilities(target))
        self.assertEqual('graveyard',spell.zone)
        self.assert_replay(session)

    def test_fixed_animation_source_incarnation_and_malformed_rollback(self):
        session=self.session(237020003);engine=session.engine
        source=self.add_card(session,name='Fixed Land Animation Fixture',ref='STALE-ANIMATION',trusted=True)
        engine.state.players['A'].mana_pool['C']=1
        self.prepare_main(session);offer=self.activation_offer(session,source)
        result=session.act('pilot:A',{'action_id':offer['id'],'pay':'auto'})
        self.assertTrue(result.ok,result.summary)
        old=source.logical_object_id
        engine.move_card(source.object_id,'hand',reason='bounded departure')
        engine.move_card(source.object_id,'battlefield',reason='bounded reentry')
        self.assertNotEqual(old,source.logical_object_id)
        self.resolve_stack(session)
        self.assertEqual({'land'},engine._type_parts(engine._effective_card_data(source)['type_line'])[0])
        self.assertFalse([effect for effect in engine.state.continuous_effects if effect.source_id==source.object_id])
        node=self.compile_runtime_fixture('Fixed Land Animation Fixture').faces[0].nodes[-1]
        effect=copy.deepcopy(node.effects[0]);effect['card']=source.ref
        effect['characteristics']['retain_types']=1
        before=authoritative_state_hash(engine.state)
        with self.assertRaises(GameRuleError):engine.apply_effect(effect,actor='A')
        self.assertEqual(before,authoritative_state_hash(engine.state))

    def compile_runtime_fixture(self,name):
        return compile_oracle_card(self.db.lookup(name),capability_registry=self.capabilities,capability_profile='commander_review')

    def test_fixed_animation_group_locks_controller_and_incarnation_membership(self):
        session=self.session(237020004,players=4);engine=session.engine
        first=self.add_card(session,name='Fixed Land Animation Fixture',ref='SET-LAND-A',trusted=True)
        second=self.add_card(session,name='Forest',ref='SET-LAND-B',seat='B',register=False)
        late=self.add_card(session,name='Forest',ref='SET-LAND-LATE',seat='C',zone='hand',register=False)
        node=self.compile_runtime_fixture('Fixed Land Set Fixture').faces[0].nodes[0]
        effect=engine._semantic_value(dict(node.effects[0]),self.stack_context(engine,'A'))
        engine.apply_effect(effect,actor='A')
        self.assertIn('creature',engine._type_parts(engine._effective_card_data(first)['type_line'])[0])
        self.assertIn('creature',engine._type_parts(engine._effective_card_data(second)['type_line'])[0])
        engine.move_card(late.object_id,'battlefield',reason='later entrant')
        self.assertNotIn('creature',engine._type_parts(engine._effective_card_data(late)['type_line'])[0])
        engine.change_control(second.object_id,'D',reason='control change')
        self.assertIn('creature',engine._type_parts(engine._effective_card_data(second)['type_line'])[0])
        engine.move_card(second.object_id,'hand',reason='leave set')
        engine.move_card(second.object_id,'battlefield',reason='new incarnation')
        self.assertNotIn('creature',engine._type_parts(engine._effective_card_data(second)['type_line'])[0])
        expire_end_of_turn_continuous_effects(engine.state)
        self.assertNotIn('creature',engine._type_parts(engine._effective_card_data(first)['type_line'])[0])

    def test_historical_v1_characteristic_instruction_keeps_its_execution_path(self):
        # Genuine historical payload generated by the unchanged v1 leaf owner,
        # compared to that archived operation's exact layer result. This is a
        # descriptor-execution witness, not a claim that old records replay
        # across incompatible runtime fingerprints.
        session=self.session(237020006);engine=session.engine
        source=self.add_card(session,name='Artifact Animation Fixture',ref='V1-RESULT')
        descriptor=fixed_source_characteristics_effect_template(
            'This artifact becomes a 2/2 white and blue Bird artifact creature with flying until end of turn.',
            source_is_permanent=True,source_card_types=('artifact',)).effects[0]
        self.assertNotIn('schema_version',descriptor)
        effect=copy.deepcopy(descriptor);effect['card']=source.ref
        engine.apply_effect(effect,actor='A')
        current=engine._effective_card_data(source)
        self.assertEqual({'artifact','creature'},engine._type_parts(current['type_line'])[0])
        self.assertEqual({'bird'},engine._type_parts(current['type_line'])[1])
        self.assertEqual(('2','2'),(current['power'],current['toughness']))
        self.assertEqual(['W','U'],current['colors'])

    def test_actual_v236_animation_record_fails_closed_before_reinterpretation(self):
        path=ROOT/'tests/fixtures/records/fixed-animation-v236-467d7008'
        provenance=json.loads((path/'provenance.json').read_text(encoding='utf-8'))
        self.assertEqual('oracle-ir-v236',provenance['compiler_version'])
        self.assertEqual('explicit_runtime_trust_incompatibility',provenance['current_runtime_disposition'])
        programs=json.loads((path/'semantics.json').read_text(encoding='utf-8'))['programs'].values()
        animation=next(p for p in programs if p['oracle_id']=='19000000-0000-4000-8000-000000000101')
        self.assertEqual('apply_source_characteristics_until_end_of_turn',animation['effects'][0]['op'])
        self.assertNotIn('schema_version',animation['effects'][0])
        self.assertEqual(5,len((path/'commands.jsonl').read_text(encoding='utf-8').splitlines()))
        with self.assertRaisesRegex(ValueError,'Runtime trust provenance mismatch in record manifest'):
            replay_record(path,self.db,verify=True)

    def test_fixed_animation_dependency_blocking_and_current_target_revalidation(self):
        # Exact-source capability failure is distinct from a legal target
        # becoming illegal after the cast has committed.
        blocked=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        row=next(value for value in blocked['capabilities'] if value['id']==SOURCE_CAPABILITY)
        row['status']='blocked';row['blockers']=['bounded dependency witness']
        from quorune.rules.capabilities import CapabilityRegistry
        registry=CapabilityRegistry(blocked)
        ir=compile_oracle_card(record('Target creature has base power and toughness 4/4 until end of turn.',type_line='Instant'),capability_registry=registry,capability_profile='commander_review')
        self.assertNotEqual('exact',ir.status)
        session=self.session(237020005);engine=session.engine
        spell=self.add_card(session,name='Fixed Frog Result Fixture',ref='REVALIDATE-SPELL',zone='hand',trusted=True)
        target=self.add_card(session,name='Source Growth Fixture',ref='REVALIDATE-TARGET',seat='B',trusted=True)
        engine.state.players['A'].mana_pool['U']=1
        self.prepare_main(session)
        action_id=f'cast:{spell.ref}'
        result=session.act('pilot:A',{'action_id':action_id,'targets':[target.ref],'pay':'auto'})
        self.assertTrue(result.ok,result.summary)
        engine.move_card(target.object_id,'hand',reason='target departure')
        self.resolve_stack(session)
        self.assertEqual('graveyard',spell.zone)
        self.assertFalse([effect for effect in engine.state.continuous_effects if any(identity.object_id==target.object_id for identity in effect.locked_objects)])

    @staticmethod
    def stack_context(engine,controller):
        from quorune.model import StackItem
        return StackItem(stack_id='fixed-set-context',ref='FIXED-SET-CONTEXT',kind='ability',controller=controller,label='Generic fixed set',targets=[])

    @staticmethod
    def prepare_main(session) -> None:
        engine = session.engine
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine.state.active_player = "A"
        engine.state.started = True
        engine.state.phase = "precombat_main"
        engine.state.step = "main"
        engine.state.priority_player = "A"
        engine.state.priority_passes = []
        engine._grant_priority("A")
        engine._issue_priority("A")

    @staticmethod
    def activation_offer(session, source: CardInstance) -> dict:
        engine = session.engine
        ability = engine._activated_abilities(source)[0]
        engine.pump()
        decision = session.packet("pilot:A", full=True)["decision"]
        return next(
            action
            for action in decision["ctx"]["legal"]["actions"]
            if action["id"] == f"activate:{source.ref}:{ability.ability_id}"
        )

    @staticmethod
    def resolve_stack(session) -> None:
        for _ in range(12):
            if not session.engine.state.stack:
                return
            principal = session.pending_principals()[0]
            result = session.act(principal, {"a": "pass"})
            if not result.ok:
                raise AssertionError(result.summary)
        raise AssertionError("Characteristic activation did not resolve")

    def activate(self, session, source: CardInstance) -> None:
        offer = self.activation_offer(session, source)
        result = session.act("pilot:A", {"action_id": offer["id"]})
        self.assertTrue(result.ok, result.summary)
        self.resolve_stack(session)

    def test_artifact_animation_uses_one_multilayer_timestamp_and_replays(self):
        session = self.session(70261101, players=4)
        engine = session.engine
        source = self.add_card(
            session,
            name="Artifact Animation Fixture",
            ref="ANIMATE",
        )
        attacker = self.add_card(
            session,
            name="Goblin Engineer",
            ref="ANIMATE-FLYER",
            seat="B",
            register=False,
        )
        attacker.temporary_keywords = ["Flying"]
        engine.state.players["A"].mana_pool["C"] = 2
        self.prepare_main(session)
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()
        self.activate(session, source)

        effective = engine._effective_card_data(source)
        card_types, subtypes, _supertypes = engine._type_parts(
            effective["type_line"]
        )
        self.assertEqual({"artifact", "creature"}, card_types)
        self.assertEqual({"bird"}, subtypes)
        self.assertEqual(["W", "U"], effective["colors"])
        self.assertEqual("2", effective["power"])
        self.assertEqual("2", effective["toughness"])
        self.assertIn("Flying", effective["keywords"])
        attacker.attacking = "A"
        engine.state.combat = CombatState(
            attackers_declared=True,
            attackers={attacker.object_id: "A"},
            defending_players=["A"],
        )
        self.assertTrue(engine._can_block(attacker, source)[0])
        attacker.attacking = None
        engine.state.combat = CombatState()
        components = [
            effect
            for effect in engine.state.continuous_effects
            if effect.source_id == source.object_id
        ]
        self.assertEqual(
            {Layer.TYPE, Layer.COLOR, Layer.ABILITY, Layer.POWER_TOUGHNESS},
            {effect.layer for effect in components},
        )
        self.assertEqual(1, len({effect.timestamp for effect in components}))
        for principal in ("pilot:A", "pilot:B", "pilot:C", "pilot:D"):
            packet = StateProjector(self.db, engine.state)._snapshot(principal)
            self.assertNotIn("continuous_effects", json.dumps(packet))

        expected_hash = authoritative_state_hash(engine.state)
        with tempfile.TemporaryDirectory() as temporary:
            game_dir = Path(temporary) / "source-animation-replay"
            session.save(game_dir)
            loaded = CommanderSession.load(self.db, game_dir)
            self.assertEqual(
                expected_hash,
                authoritative_state_hash(loaded.engine.state),
            )
            replay = replay_record(game_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(expected_hash, replay["final_state_hash"])

    def test_source_effects_follow_incarnation_control_and_cleanup_boundaries(self):
        session = self.session(70261102)
        engine = session.engine
        growth = self.add_card(
            session,
            name="Source Growth Fixture",
            ref="GROWTH",
        )
        engine.state.players["A"].mana_pool["C"] = 1
        self.prepare_main(session)
        offer = self.activation_offer(session, growth)
        result = session.act("pilot:A", {"action_id": offer["id"]})
        self.assertTrue(result.ok, result.summary)
        engine.change_control(
            growth.object_id,
            "B",
            reason="source characteristic control witness",
        )
        self.resolve_stack(session)
        self.assertEqual(3, engine._numeric_stat(growth.object_id, "power"))
        self.assertIn(
            "first strike",
            engine._combat_keywords(growth),
        )
        self.assertGreater(expire_end_of_turn_continuous_effects(engine.state), 0)
        self.assertEqual(1, engine._numeric_stat(growth.object_id, "power"))

        engine.change_control(growth.object_id, "A", reason="reset witness")
        engine.state.players["A"].mana_pool["C"] = 1
        self.prepare_main(session)
        offer = self.activation_offer(session, growth)
        result = session.act("pilot:A", {"action_id": offer["id"]})
        self.assertTrue(result.ok, result.summary)
        engine.move_card(growth.object_id, "graveyard", log=False)
        engine.move_card(
            growth.object_id,
            "battlefield",
            controller="A",
            log=False,
        )
        self.resolve_stack(session)
        self.assertEqual(1, engine._numeric_stat(growth.object_id, "power"))
        self.assertNotIn("first strike", engine._combat_keywords(growth))

    def test_animation_updates_dynamic_counts_and_detaches_equipment(self):
        session = self.session(70261103)
        engine = session.engine
        source = self.add_card(
            session,
            name="Equipment Animation Fixture",
            ref="ANIMATE-EQUIPMENT",
        )
        recipient = self.add_card(
            session,
            name="Goblin Engineer",
            ref="ANIMATE-RECIPIENT",
            register=False,
        )
        queen = self.add_card(
            session,
            name="Queen Allenal of Ruadach",
            ref="ANIMATE-COUNT",
        )
        engine.state.players["A"].mana_pool["C"] = 4
        self.prepare_main(session)
        equipped = session.act(
            "pilot:A",
            {
                "action_id": f"activate:{source.ref}:ab2",
                "targets": [recipient.ref],
            },
        )
        self.assertTrue(equipped.ok, equipped.summary)
        self.resolve_stack(session)
        self.assertEqual(recipient.object_id, source.attached_to)
        self.assertEqual(2, engine._numeric_stat(queen.object_id, "power"))
        engine.state.players["A"].mana_pool["C"] = 3
        self.prepare_main(session)
        self.activate(session, source)

        self.assertIsNone(source.attached_to)
        self.assertNotIn(source.object_id, recipient.attachments)
        self.assertEqual(3, engine._numeric_stat(queen.object_id, "power"))
        self.assertEqual(1, engine._numeric_stat(source.object_id, "power"))
        self.assertEqual(5, engine._numeric_stat(source.object_id, "toughness"))

    def test_malformed_source_characteristics_roll_back_atomically(self):
        session = self.session(70261104)
        engine = session.engine
        source = self.add_card(
            session,
            name="Source Keywords Fixture",
            ref="SOURCE-ROLLBACK",
        )
        effect = copy.deepcopy(
            fixed_source_characteristics_effect_template(
                "This creature gains vigilance and lifelink until end of turn.",
                source_is_permanent=True,
                source_card_types=("creature",),
            ).effects[0]
        )
        effect["card"] = source.ref
        effect["keywords"] = ["Vigilance", "Vigilance"]
        before = authoritative_state_hash(engine.state)
        with self.assertRaises(GameRuleError):
            engine.apply_effect(effect, actor="A")
        self.assertEqual(before, authoritative_state_hash(engine.state))

    def test_targeted_shadow_uses_shared_keyword_and_block_legality(self):
        session = self.session(70261105, players=4)
        engine = session.engine
        spell = self.add_card(
            session,
            name="Target Shadow Fixture",
            ref="TARGET-SHADOW",
            zone="hand",
        )
        target = self.add_card(
            session,
            name="Goblin Engineer",
            ref="TARGET-SHADOW-BLOCKER",
            register=False,
        )
        attacker = self.add_card(
            session,
            name="Goblin Engineer",
            ref="TARGET-SHADOW-ATTACKER",
            seat="B",
            register=False,
        )
        attacker.temporary_keywords = ["Shadow"]
        attacker.attacking = "A"
        engine.state.combat = CombatState(
            attackers_declared=True,
            attackers={attacker.object_id: "A"},
            defending_players=["A"],
        )
        self.assertFalse(engine._can_block(attacker, target)[0])
        attacker.attacking = None
        engine.state.combat = CombatState()

        engine.state.players["A"].mana_pool["U"] = 1
        self.prepare_main(session)
        engine.permissions.invalidate_current()
        engine._cast(
            "A",
            {"card": spell.ref, "targets": [target.ref], "pay": "auto"},
        )
        engine.state.priority_player = None
        engine._prepare_stack_resolution()
        self.assertEqual("graveyard", spell.zone)
        self.assertIn("shadow", engine._combat_keywords(target))
        attacker.attacking = "A"
        engine.state.combat = CombatState(
            attackers_declared=True,
            attackers={attacker.object_id: "A"},
            defending_players=["A"],
        )
        self.assertTrue(engine._can_block(attacker, target)[0])


if __name__ == "__main__":
    unittest.main()
