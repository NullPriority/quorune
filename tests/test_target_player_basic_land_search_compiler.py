from dataclasses import replace
import unittest
from unittest.mock import Mock

from quorune.card_programs.adapters import compile_best_available_card_program
from quorune.card_programs.model import CardProgram
from quorune.carddb import CardDatabase, CardRecord
from quorune.library_search_model import counted_search_bound_dependencies
from quorune.oracle_ir import compile_oracle_card, register_generated_programs
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.library_search_capability_shapes import fixed_library_search_node_capabilities
from quorune.semantics import SemanticRegistry


_ENTRY = "This creature enters with two +1/+1 counters on it."
_PRICE = "{1}{G}, Remove a +1/+1 counter from this creature: "
_RESULT = (
    "Target player searches their library for a basic land card, "
    "puts it onto the battlefield tapped, then shuffles."
)
_TARGET = {"zones": ["player"], "categories": ["player"], "player_relation": "any", "count": 1}
_EFFECT = {
    "op": "search", "schema_version": 21, "searching_player": "$target.0", "zone": "library",
    "selector": {"types": ["land"], "supertypes": ["basic"]},
    "count": {"minimum": 1, "maximum": 1}, "destination": "battlefield",
    "enters_tapped_override": True, "reveal": False, "shuffle_after": True,
}
_SEARCH_CAPABILITIES = {
    "library.search.target_player.basic_to_battlefield_tapped",
    "library.search.fixed_counted",
    "target.revalidate_resolution",
}
_ACTIVATION_CAPABILITIES = _SEARCH_CAPABILITIES | {"activation.source_counter_removal.fixed"}


class TargetPlayerBasicLandSearchCompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.capabilities = load_default_capability_registry()

    def setUp(self):
        self.record = CardRecord(
            oracle_id="77777777-7777-4777-8777-777777777778",
            name="Search Draft", mana_cost="{2}{G}", mana_value=3,
            type_line="Creature — Elemental", oracle_text=f"{_ENTRY}\n{_PRICE}{_RESULT}",
            power="0", toughness="0", loyalty=None, defense=None,
            colors=("G",), color_identity=("G",), keywords=(), produced_mana=(),
            layout="normal", released_at="2026-01-01", legalities={"commander": "legal"},
            faces=(), raw={},
        )
        self.db = Mock(spec=CardDatabase)
        self.db.rulings.return_value = []
        self.options = {"capability_registry": self.capabilities, "capability_profile": "commander_review"}

    def test_printed_activation_keeps_the_entry_producer_cost_and_exact_search(self):
        ir = compile_oracle_card(self.record, **self.options)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        entry, activation = ir.faces[0].nodes
        self.assertEqual("fixed-self-entry-counter-v1", entry.template_id)
        self.assertEqual(
            {"counter.producer.fixed_self_entry", "counter.placement.quantity_replacement"},
            set(entry.capability_dependencies),
        )
        self.assertEqual("fixed-target-player-basic-land-search-v21", activation.template_id)
        self.assertEqual((_EFFECT,), activation.effects)
        self.assertEqual(_TARGET, activation.target_schema)
        self.assertEqual(_ACTIVATION_CAPABILITIES, set(activation.capability_dependencies))
        self.assertEqual(
            {"schema_version": 1, "counter_name": "+1/+1", "amount": 1},
            activation.cost["source_counter_removal_cost"],
        )
        self.assertEqual({"G": 1, "GENERIC": 1}, {k: v for k, v in activation.cost["mana"].items() if v})
        span = {"start": len(_ENTRY) + 1, "end": len(self.record.oracle_text), "line": 2}
        self.assertEqual(span, activation.to_dict()["span"])

        registry = SemanticRegistry(include_builtin_packs=False)
        register_generated_programs(
            self.db, registry, (self.record,), promote_exact_effect_programs=True, **self.options,
        )
        programs = registry.programs_for_oracle(self.record.oracle_id)
        search, = (program for program in programs if _EFFECT in program.effects)
        self.assertEqual("trusted", search.trust_level)
        program = compile_best_available_card_program(
            self.db, self.record, semantic_registry=registry, **self.options,
        )
        self.assertTrue(program.trust_closure["trusted"], program.trust_closure["blockers"])
        serialized = program.to_dict()
        ability, = (row for row in serialized["abilities"] if row["effect_nodes"] == [_EFFECT])
        self.assertEqual(span, ability["source_span"])
        self.assertEqual(_TARGET, ability["targets"])
        self.assertEqual(_ACTIVATION_CAPABILITIES, set(ability["capability_dependencies"]))
        self.assertEqual(serialized, CardProgram.from_dict(serialized).to_dict())

    def test_nearby_searches_leave_an_activation_effect_residual(self):
        for result in (
            _RESULT.replace("Target player", "Target opponent"),
            _RESULT.replace("their library", "your library"),
            _RESULT.replace("basic land", "land"),
            _RESULT.replace(" searches", " may search"),
            _RESULT.replace(" battlefield tapped", " battlefield"),
            _RESULT.replace(", then shuffles", ""),
            f"{_RESULT} That player gains 1 life.",
        ):
            with self.subTest(result=result):
                record = replace(self.record, oracle_text=f"{_ENTRY}\n{_PRICE}{result}")
                ir = compile_oracle_card(record, **self.options)
                self.assertEqual("partial", ir.status)
                self.assertTrue(any(residual.kind == "effect" for residual in ir.material_residuals))

    def test_wrong_target_predicate_and_version_fail_closed(self):
        def dependencies(effect, target):
            return fixed_library_search_node_capabilities(
                effects=(effect,), target_schema=target,
                mechanic_ids=("fixed-target-player-basic-land-search",),
            )

        self.assertEqual(_SEARCH_CAPABILITIES, set(dependencies(_EFFECT, _TARGET)))
        self.assertEqual(_SEARCH_CAPABILITIES, set(counted_search_bound_dependencies(_EFFECT)))
        for target in (None, {**_TARGET, "player_relation": "opponent"}, {**_TARGET, "count": True}):
            with self.subTest(target=target):
                self.assertEqual((), dependencies(_EFFECT, target))
        self.assertEqual((), dependencies({**_EFFECT, "schema_version": 18}, _TARGET))
        for effect in (
            {**_EFFECT, "schema_version": 21.0},
            {**_EFFECT, "searching_player": "$controller"},
            {**_EFFECT, "selector": {"types": ["land"]}},
            {**_EFFECT, "enters_tapped_override": False},
            {**_EFFECT, "shuffle_after": False},
        ):
            with self.subTest(effect=effect):
                self.assertEqual((), dependencies(effect, _TARGET))
                with self.assertRaises(ValueError):
                    counted_search_bound_dependencies(effect)

        card = compile_best_available_card_program(
            self.db, self.record, semantic_registry=SemanticRegistry(include_builtin_packs=False),
            **self.options,
        )
        search, = (program for program in card.abilities if _EFFECT in program.effects)
        forged_search = replace(search, target_schema={**_TARGET, "player_relation": "opponent"})
        reopened = CardProgram.create(
            compiler_version=card.compiler_version, oracle_id=card.oracle_id,
            card_name=card.card_name, faces=card.faces,
            oracle_source_hash=card.oracle_source_hash, rulings_source_hash=card.rulings_source_hash,
            abilities=(forged_search,), provenance=card.provenance,
        )
        self.assertFalse(reopened.trust_closure["trusted"])
        self.assertTrue(any(
            "runtime_event_outcome:invalid:Targeted basic-land search" in blocker
            for blocker in reopened.trust_closure["blockers"]
        ))
