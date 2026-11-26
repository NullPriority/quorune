from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace
import unittest

from quorune.abilities import ActivatedAbility
from quorune.ability_fragments import ability_fragment_to_dict
from quorune.card_programs.adapters import card_program_from_semantic_programs
from quorune.card_programs.model import CardProgramError
from quorune.card_programs.saga_chapter_equivalence import shadowed_reviewed_saga_chapter_keys
from quorune.characteristic_fragments import (
    CharacteristicQuantityScope, CharacteristicQuantitySpec,
    PowerToughnessCalculation, QueryCharacteristicModifierSpec,
)
from quorune.compiler.activated_costs import activated_ability_cost
from quorune.fixed_mana_abilities import FixedActivatedManaAbilitySpec, FixedManaMode
from quorune.object_predicate import ObjectQuerySpec
from quorune.replacement.immutable import FrozenMap
from quorune.semantics import SemanticProgram, SemanticRegistry


class SagaChapterEquivalenceTests(unittest.TestCase):
    ORACLE_ID = "4c6a0c30-b547-4eff-8ff4-0ca25803c076"
    AUGUST = "163e11dd783eb41b1457a6d822579f432c99f7a98cd48660423676396017cf70"
    NOVEMBER = "60f5ce77b06e6a14d702198e83d36744158c9af523c6c5d232d6f2dcaa7e81c4"

    @classmethod
    def setUpClass(cls):
        cls.record = SimpleNamespace(oracle_id=cls.ORACLE_ID)
        cls.reviewed = tuple(
            replace(program, provenance={
                key: value for key, value in program.provenance.items()
                if key != "reviewed_equivalent_rulings_hashes"
            })
            for program in SemanticRegistry().programs_for_oracle(cls.ORACLE_ID)
        )
        cls.old_keys = {program.key for program in cls.reviewed}
        cls.chapters = {program.event: program for program in cls.reviewed}
        assert len(cls.reviewed) == 4

    def candidate(self, ability_id, event, line, *, effects=(), handlers=(), costs=None, grant=False, dependencies=()):
        return SemanticProgram(
            key=f"{self.ORACLE_ID}:{ability_id}", label=ability_id, oracle_id=self.ORACLE_ID,
            ability_id=ability_id, active_zone="battlefield", event=event,
            effects=list(effects), handlers=list(handlers), cost_schema=costs,
            trust_level="trusted", capability_dependencies=list(dependencies),
            tests=["test_exact_primary_source_retires_all_legacy_chapters_and_child"],
            capability_closure={
                "trusted": True, "requested": list(dependencies), "reachable": list(dependencies),
                "profile": "commander_review", "blockers": [], "fingerprint": "1" * 64,
                "registry_fingerprint": "2" * 64, "evidence_fingerprint": "3" * 64,
            },
            provenance={
                "authored_by": "oracle-ir-v-test",
                "review_status": "generated_verified",
                "source_oracle_hash": self.reviewed[0].provenance["source_oracle_hash"],
                "source_rulings_hash": self.AUGUST,
                "source_span": {"start": 0, "end": 10, "line": line},
                **({"granted_only": True} if grant else {}),
            },
        )

    def grant_chapter(self, chapter, ability):
        old = self.chapters[f"saga.chapter.{chapter}"]
        fragment = deepcopy(old.effects[0]["fragment"])
        fragment["value"].update(
            ability_id=f"ability:granted:front:n{chapter + 1}",
            semantic_key=ability.builtin_semantic_key,
            effect_text=ability.effect_text,
        )
        return self.candidate(
            f"trigger:front:n{chapter + 1}", f"saga.chapter.{chapter}", chapter + 1,
            effects=[{"op": "grant_ability_fragment", "schema_version": 2,
                      "source": "$source.zone_object", "fragment": fragment}],
            dependencies=["continuous.ability.battlefield_incarnation_grant"],
        )

    def complete_group(self):
        mana_id = "ability:granted:front:n2"
        token_id = "ability:granted:front:n3"
        mana_ability = ActivatedAbility(
            ability_id="ab2", line_index=1, oracle_line="{T}: Add {C}.",
            cost_text="{T}", effect_text="Add {C}.", zones=("battlefield",),
            mana=FrozenMap(dict.fromkeys(("W", "U", "B", "R", "G", "C", "GENERIC"), 0)),
            tap_source=True, mana_ability=True, builtin_semantic_key=f"{self.ORACLE_ID}:{mana_id}",
            fixed_mana_outputs=(FixedManaMode(colorless=1),),
        )
        mana = FixedActivatedManaAbilitySpec(
            ability_id="ab2", line_index=1, oracle_line="{T}: Add {C}.", cost_text="{T}",
            effect_text="Add {C}.", mana_cost=mana_ability.mana, tap_source=True,
            sacrifice_source=False, life_payment=0, modes=(FixedManaMode(colorless=1),),
        )
        token_oracle = "This token gets +1/+1 for each artifact you control."
        token_text = f"Create a 0/0 colorless Construct artifact creature token with '{token_oracle}'"
        token_ability = replace(
            mana_ability, ability_id="ab3", line_index=2, oracle_line=f"{{2}}, {{T}}: {token_text}",
            cost_text="{2}, {T}", effect_text=token_text,
            mana=FrozenMap(dict(mana_ability.mana) | {"GENERIC": 2}),
            mana_ability=False, builtin_semantic_key=f"{self.ORACLE_ID}:{token_id}", fixed_mana_outputs=(),
        )
        modifier = QueryCharacteristicModifierSpec(
            quantity=CharacteristicQuantitySpec(
                scope=CharacteristicQuantityScope.CONTROLLER_ZONE,
                query=ObjectQuerySpec(zones=("battlefield",), types_all=("artifact",)),
            ),
            calculation=PowerToughnessCalculation.PER_MATCHING_OBJECT, power=1, toughness=1,
        )
        token = {"op": "create_token", "controller": "$controller", "name": "Construct Token", "quantity": 1,
                 "characteristics": {"type_line": "Token Artifact Creature — Construct", "literal_token_name_version": 1,
                                     "power": "0", "toughness": "0", "colors": [],
                                     "ability_fragments": [ability_fragment_to_dict(modifier)]}}
        search = {"op": "search", "zone": "library", "destination": "battlefield", "shuffle_after": True,
                  "count": {"minimum": 1, "maximum": 1},
                  "selector": {"types": ["artifact"], "predicate": "printed_mana_cost_0_or_1"}}
        return (
            self.grant_chapter(1, mana_ability),
            self.grant_chapter(2, token_ability),
            self.candidate("trigger:front:n4", "saga.chapter.3", 4, effects=[search],
                           dependencies=["library.search.artifact_printed_mana_cost_0_or_1", "library.search.fixed_to_battlefield"]),
            self.candidate(mana_id, "activate", 2, costs=activated_ability_cost(mana_ability), grant=True, handlers=[
                {"handler_id": "ability.activated.mana.fixed-output.v1", "schema_version": 1, "event": "activate",
                 "ability": mana.to_dict()},
                {"handler_id": "activation.catalog.pinned.v1", "schema_version": 1, "event": "activate",
                 "ability": mana_ability.to_dict()},
            ], dependencies=["mana.activated.fixed_output", "mana.activated.restricted_fixed_output"]),
            self.candidate(token_id, "activate", 3, effects=[token], costs=activated_ability_cost(token_ability), grant=True,
                           dependencies=["token.creation.fixed_definition", "token.creation.typed_ability_definition"]),
        )

    def keys(self, generated, reviewed=None):
        return shadowed_reviewed_saga_chapter_keys(self.record, generated, self.reviewed if reviewed is None else reviewed)

    @staticmethod
    def with_provenance(program, **fields):
        return replace(program, provenance={**program.provenance, **fields})

    def test_exact_primary_source_retires_all_legacy_chapters_and_child(self):
        complete = self.complete_group()
        originals = deepcopy([program.to_dict() for program in self.reviewed])
        self.assertEqual(self.old_keys, self.keys(complete))
        self.assertEqual(originals, [program.to_dict() for program in self.reviewed])
        for index in range(len(complete)):
            with self.subTest(missing=complete[index].ability_id):
                self.assertEqual(set(), self.keys(complete[:index] + complete[index + 1:]))

    def test_reviewed_november_requires_approval_on_every_legacy_sibling(self):
        complete = tuple(self.with_provenance(program, source_rulings_hash=self.NOVEMBER) for program in self.complete_group())
        approved = tuple(self.with_provenance(program, reviewed_equivalent_rulings_hashes=[self.NOVEMBER])
                         for program in self.reviewed)
        self.assertEqual(self.old_keys, self.keys(complete, approved))
        for index in range(len(approved)):
            with self.subTest(unreviewed=approved[index].key):
                self.assertEqual(set(), self.keys(complete, approved[:index] + (self.reviewed[index],) + approved[index + 1:]))

    def test_mixed_current_or_unknown_ruling_digests_cannot_retire_any_view(self):
        complete = self.complete_group()
        approved = tuple(self.with_provenance(program, reviewed_equivalent_rulings_hashes=[self.NOVEMBER])
                         for program in self.reviewed)
        mixed = (*complete[:4], self.with_provenance(complete[4], source_rulings_hash=self.NOVEMBER))
        unknown = tuple(self.with_provenance(program, source_rulings_hash="e" * 64) for program in complete)
        changed_oracle = tuple(self.with_provenance(program, source_oracle_hash="e" * 64) for program in complete)
        for group in (mixed, unknown, changed_oracle):
            with self.subTest(pair=(group[-1].provenance["source_oracle_hash"], group[-1].provenance["source_rulings_hash"])):
                self.assertEqual(set(), self.keys(group, approved))

    def test_reviewed_alias_does_not_make_a_mixed_source_group_mountable(self):
        old = self.with_provenance(self.reviewed[0], reviewed_equivalent_rulings_hashes=[self.NOVEMBER])
        current = self.with_provenance(self.complete_group()[0], source_rulings_hash=self.NOVEMBER)
        with self.assertRaisesRegex(CardProgramError, "inconsistent rulings hashes"):
            card_program_from_semantic_programs((old, current))

    def test_missing_trust_or_nonmatching_effects_leave_all_legacy_views(self):
        complete = self.complete_group()
        changed_search = deepcopy(complete[2].effects[0])
        changed_search["selector"]["predicate"] = "mana_cost_0_or_1"
        changed_token = deepcopy(complete[4].effects[0])
        changed_token["quantity"] = 2
        changed_mana = deepcopy(complete[3].handlers)
        changed_mana[0]["ability"]["modes"][0]["bundle"]["C"] = 2
        variants = (
            (*complete[:2], replace(complete[2], effects=[changed_search]), *complete[3:]),
            (*complete[:4], replace(complete[4], effects=[changed_token])),
            (*complete[:3], replace(complete[3], handlers=changed_mana), complete[4]),
            (replace(complete[0], trust_level="provisional"), *complete[1:]),
        )
        for variant in variants:
            with self.subTest(keys=[program.key for program in variant]):
                self.assertEqual(set(), self.keys(variant))
