from __future__ import annotations

import copy
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
import uuid
from unittest.mock import patch

from common import ROOT, keep_all, make_session
from quorune.carddb import CardDatabase, CardRecord
from quorune.compiler.library_search_templates import (
    FIXED_LIBRARY_SEARCH_CAPABILITY_ID,
    fixed_library_search_effect_template,
)
from quorune.deck import DeckLoader
from quorune.model import CardInstance, StackItem
from quorune.oracle_ir import (
    compile_oracle_card,
    generated_programs,
    register_generated_programs,
)
from quorune.record import checkpoint_envelope, replay_record
from quorune.rules.capabilities import (
    CapabilityRegistry,
    capability_dependencies_for_node,
    load_default_capability_registry,
)
from scripts.build_test_database import build_fixture_database


def card_record(
    oracle_text: str,
    *,
    type_line: str = "Sorcery",
    name: str = "Fixed Library Search Fixture",
) -> CardRecord:
    return CardRecord(
        oracle_id="00000000-0000-4000-8000-000000000301",
        name=name,
        mana_cost="{2}{G}",
        mana_value=3.0,
        type_line=type_line,
        oracle_text=oracle_text,
        power="2" if "Creature" in type_line else None,
        toughness="2" if "Creature" in type_line else None,
        loyalty=None,
        defense=None,
        colors=("G",),
        color_identity=("G",),
        keywords=(),
        produced_mana=(),
        layout="normal",
        released_at="2026-01-01",
        legalities={"commander": "legal"},
        faces=(),
        raw={},
    )


class FixedLibrarySearchCompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.capabilities = load_default_capability_registry()
        cls.registry_value = json.loads(
            (ROOT / "quorune" / "rules" / "capability-registry.json").read_text(
                encoding="utf-8"
            )
        )

    def compile(
        self,
        text: str,
        *,
        type_line: str = "Sorcery",
        capabilities: CapabilityRegistry | None = None,
    ):
        return compile_oracle_card(
            card_record(text, type_line=type_line),
            capability_registry=capabilities or self.capabilities,
            capability_profile="commander_review",
        )

    def test_fixed_search_compiles_across_spell_trigger_activation_and_modal_contexts(
        self,
    ):
        contexts = (
            (
                "Search your library for a Forest card, put it onto the battlefield, then shuffle.",
                "Sorcery",
                "spell_ability",
            ),
            (
                "When this creature enters, search your library for a basic land card, put it onto the battlefield tapped, then shuffle.",
                "Creature — Scout",
                "triggered_ability",
            ),
            (
                "{3}, {T}: Search your library for a Goblin permanent card, put it onto the battlefield, then shuffle.",
                "Creature — Human Mercenary",
                "activated_ability",
            ),
            (
                "Choose one —\n• Search your library for a Cave card, put it onto the battlefield tapped, then shuffle.\n• You gain 3 life.",
                "Sorcery",
                "spell_ability",
            ),
        )
        for text, type_line, kind in contexts:
            with self.subTest(text=text):
                ir = self.compile(text, type_line=type_line)
                self.assertEqual("exact", ir.status, ir.material_residuals)
                nodes = [node for node in ir.faces[0].nodes if node.exact]
                self.assertTrue(nodes)
                self.assertTrue(
                    any(
                        FIXED_LIBRARY_SEARCH_CAPABILITY_ID
                        in node.capability_dependencies
                        for node in nodes
                    )
                )
                self.assertTrue(any(node.kind == kind for node in nodes))

    def test_fixed_search_quality_grammar_is_typed_and_deterministic(self):
        cases = {
            "a basic Forest, Plains, or Island": {
                "types": ["land"],
                "subtypes_any": ["forest", "island", "plains"],
                "supertypes": ["basic"],
            },
            "a land with a basic land type": {
                "types": ["land"],
                "subtypes_any": [
                    "forest",
                    "island",
                    "mountain",
                    "plains",
                    "swamp",
                ],
            },
            "a green creature": {
                "types": ["creature"],
                "colors_any": ["G"],
            },
            "a legendary planeswalker": {
                "types": ["planeswalker"],
                "supertypes": ["legendary"],
            },
            "a Goblin permanent": {
                "types_any": [
                    "artifact",
                    "battle",
                    "creature",
                    "enchantment",
                    "land",
                    "planeswalker",
                ],
                "subtypes_any": ["goblin"],
            },
            "an artifact or creature": {
                "types_any": ["artifact", "creature"],
            },
        }
        for quality, selector in cases.items():
            with self.subTest(quality=quality):
                template = fixed_library_search_effect_template(
                    f"Search your library for {quality} card, put it onto the battlefield, then shuffle."
                )
                self.assertIsNotNone(template)
                assert template is not None
                _template_id, effects, _target_schema, _mechanics = (
                    template.compiled()
                )
                self.assertEqual(selector, effects[0]["selector"])

    def test_multi_tapped_land_search_compiles_as_one_closed_batch(self):
        for count in ("two", "ten"):
            text = (
                f"Search your library for up to {count} land cards, put them "
                "onto the battlefield tapped, then shuffle."
            )
            with self.subTest(count=count):
                template = fixed_library_search_effect_template(text)
                self.assertIsNotNone(template)
                assert template is not None
                _template_id, effects, target_schema, mechanics = (
                    template.compiled()
                )
                self.assertTrue(
                    effects[0]["enters_tapped_override"]
                )
                self.assertIn(
                    FIXED_LIBRARY_SEARCH_CAPABILITY_ID,
                    capability_dependencies_for_node(
                        effects=effects,
                        target_schema=target_schema,
                        mechanic_ids=mechanics,
                    ),
                )
                ir = self.compile(text)
                self.assertEqual("exact", ir.status, ir.material_residuals)

        for unsupported in (
            "Search your library for up to two Forest cards, put them onto the battlefield, then shuffle.",
            "Search your library for up to two creature cards, put them onto the battlefield tapped, then shuffle.",
        ):
            with self.subTest(unsupported=unsupported):
                self.assertIsNone(
                    fixed_library_search_effect_template(unsupported)
                )

    def test_named_linked_attachment_compound_and_open_searches_remain_residual(
        self,
    ):
        leaf_unsupported = (
            "Search your library for a card named Nissa's Chosen, put it onto the battlefield, then shuffle.",
            "Search your library for up to three artifact cards with different names, put them onto the battlefield, then shuffle.",
            "Search your library for an Equipment card, put it onto the battlefield, attach it to a creature you control, then shuffle.",
            "Search your library for a Dinosaur creature card, put it onto the battlefield, then shuffle. It gains indestructible until your next turn.",
            "Search your library for a basic land card, put it onto the battlefield tapped, then shuffle. Investigate.",
            "Search your library for up to two basic land cards and/or Gate cards, put them onto the battlefield tapped, then shuffle.",
        )
        for text in leaf_unsupported:
            with self.subTest(text=text):
                self.assertIsNone(fixed_library_search_effect_template(text))

        integrated_unsupported = tuple(
            text
            for text in leaf_unsupported
            if not text.endswith("Investigate.")
        )
        for text in integrated_unsupported:
            with self.subTest(integrated_text=text):
                ir = self.compile(text)
                self.assertNotEqual("exact", ir.status)
                self.assertTrue(ir.material_residuals)

    def test_fixed_search_shape_and_dependencies_fail_closed(self):
        single = fixed_library_search_effect_template(
            "Search your library for a Forest card, put it onto the battlefield, then shuffle."
        )
        multi = fixed_library_search_effect_template(
            "Search your library for up to two basic land cards, put them onto the battlefield tapped, then shuffle."
        )
        assert single is not None and multi is not None
        _single_id, single_effects, single_target, single_mechanics = (
            single.compiled()
        )
        _multi_id, multi_effects, _multi_target, _multi_mechanics = (
            multi.compiled()
        )
        dependencies = capability_dependencies_for_node(
            effects=single_effects,
            target_schema=single_target,
            mechanic_ids=single_mechanics,
        )
        self.assertIn(FIXED_LIBRARY_SEARCH_CAPABILITY_ID, dependencies)

        single_effect = dict(single_effects[0])
        multi_effect = dict(multi_effects[0])
        for malformed in (
            {**single_effect, "extra": True},
            {**single_effect, "destination": "hand"},
            {**single_effect, "shuffle_after": False},
            {**single_effect, "selector": {"names": ["Forest"]}},
            {**single_effect, "count": {"minimum": 1, "maximum": 11}},
            {
                **multi_effect,
                "enters_tapped_override": False,
            },
            {
                **multi_effect,
                "selector": {"types": ["creature"]},
            },
        ):
            with self.subTest(malformed=malformed):
                self.assertFalse(
                    capability_dependencies_for_node(
                        effects=(malformed,),
                        target_schema=None,
                        mechanic_ids=single_mechanics,
                    )
                )

        for blocked in (
            FIXED_LIBRARY_SEARCH_CAPABILITY_ID,
            "trigger.event.normalized_zone_change",
            "trigger.placement.apnap",
            "zone.change.destination_replacement",
            "zone.entry.tapped_state",
        ):
            with self.subTest(blocked=blocked):
                value = deepcopy(self.registry_value)
                row = next(
                    item
                    for item in value["capabilities"]
                    if item["id"] == blocked
                )
                row["status"] = "blocked"
                row["blockers"] = ["focused library-search mutation"]
                registry = CapabilityRegistry(value)
                self.assertNotEqual(
                    "exact",
                    self.compile(
                        "Search your library for a Forest card, put it onto the battlefield, then shuffle.",
                        capabilities=registry,
                    ).status,
                )

        with patch(
            "quorune.oracle_ir.fixed_library_search_effect_template",
            return_value=None,
        ):
            self.assertNotEqual(
                "exact",
                self.compile(
                    "Search your library for a Forest card, put it onto the battlefield, then shuffle."
                ).status,
            )


class FixedLibrarySearchRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.capabilities = load_default_capability_registry()
        cls.temporary = tempfile.TemporaryDirectory()
        database = Path(cls.temporary.name) / "fixed-library-search.sqlite3"
        build_fixture_database(
            [
                ROOT / "tests" / "fixtures" / "scryfall-exact-lists.json",
                ROOT
                / "tests"
                / "fixtures"
                / "changeling-characteristics-cards.json",
                ROOT
                / "tests"
                / "fixtures"
                / "fixed-library-search-cards.json",
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
        cls.changeling = cls.db.lookup("Universal Automaton")
        cls.moggcatcher = cls.db.lookup("Moggcatcher")
        cls.blighted = cls.db.lookup("Blighted Woodland")

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    def session(self, seed: int):
        session = make_session(
            self.db,
            copy.deepcopy(self.mishra),
            copy.deepcopy(self.zimone),
            players=4,
            seed=seed,
            auto_pass_empty=False,
        )
        keep_all(session)
        registration = register_generated_programs(
            self.db,
            session.engine.semantics,
            (self.changeling,),
            capability_registry=self.capabilities,
            capability_profile="commander_review",
            promote_exact_runtime_handlers=True,
        )
        self.assertEqual(1, registration["runtime_handlers_promoted"])
        session.engine.permissions.invalidate_current()
        session.engine.state.pending_decision = None
        session.engine.state.priority_player = None
        session.engine.state.priority_passes = []
        return session

    def search_program(self, session, record: CardRecord):
        programs = generated_programs(
            self.db,
            record,
            trust_level="trusted",
            capability_registry=self.capabilities,
            capability_profile="commander_review",
        )
        program = next(
            value
            for value in programs
            if any(effect.get("op") == "search" for effect in value.effects)
        )
        session.engine.semantics.put(program)
        self.assertTrue(
            session.engine.semantic_program_is_current_trusted(program)
        )
        return program

    def begin_search(self, session, program, *, ref: str):
        source = CardInstance(
            object_id=f"{ref}-source-object",
            ref=f"{ref}-SOURCE",
            oracle_id=str(program.oracle_id),
            printed_name=program.label,
            owner="B",
            controller="B",
            zone="battlefield",
            known_to=list(session.engine.seats),
            revealed_to=list(session.engine.seats),
        )
        session.state.cards[source.object_id] = source
        session.state.players["B"].zones["battlefield"].append(
            source.object_id
        )
        item = StackItem(
            stack_id=uuid.uuid4().hex,
            ref=ref,
            kind="activated_ability",
            controller="B",
            label=program.label,
            source_object_id=source.object_id,
            semantic_key=program.key,
            visibility=list(session.engine.seats),
        )
        session.engine.state.stack.append(item)
        session.engine._prepare_stack_resolution()
        self.assertEqual(
            "semantic.search",
            session.state.pending_decision.kind,
        )
        session.initial_checkpoint = checkpoint_envelope(session.state)
        session.commands.clear()
        session.decisions.clear()
        return item

    def add_changeling_to_library(self, session) -> CardInstance:
        card = CardInstance(
            object_id="fixed-search-changeling",
            ref="B-SEARCH-CHANGELING",
            oracle_id=self.changeling.oracle_id,
            printed_name=self.changeling.name,
            owner="B",
            controller="B",
            zone="library",
            known_to=["B"],
            revealed_to=[],
        )
        session.state.cards[card.object_id] = card
        session.state.players["B"].zones["library"].append(card.object_id)
        return card

    def test_generated_search_uses_effective_hidden_characteristics_and_replays(
        self,
    ):
        session = self.session(701_230_001)
        card = self.add_changeling_to_library(session)
        program = self.search_program(session, self.moggcatcher)
        self.begin_search(session, program, ref="S-MOGGCATCHER-SEARCH")

        packet = session.packet("pilot:B", full=True)
        candidates = packet["decision"]["ctx"]["search_cards"]
        self.assertIn(card.ref, {value["id"] for value in candidates})
        schema = packet["decision"]["legal_actions"][0]["choice_schema"]
        self.assertEqual(0, schema["minimum"])
        self.assertTrue(schema["rules_may_fail_to_find"])
        for seat in ("A", "C", "D"):
            rendered = json.dumps(session.packet(f"pilot:{seat}", full=True))
            self.assertNotIn(card.ref, rendered)
            self.assertNotIn(card.printed_name, rendered)

        before_shuffle = session.state.players["B"].stats.get(
            "shuffle_count", 0
        )
        result = session.act(
            "pilot:B",
            {
                "action_id": "choose",
                "search_cards": [card.ref],
                "plan": "DEVELOP_TYPED_SEARCH",
                "reason": "Find the effective Goblin through Changeling.",
            },
        )
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("battlefield", card.zone)
        self.assertEqual("B", card.controller)
        self.assertEqual(
            before_shuffle + 1,
            session.state.players["B"].stats["shuffle_count"],
        )
        self.assertIn(
            card.ref,
            json.dumps(session.packet("pilot:A", full=True)),
        )

        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / "fixed-search-changeling"
            session.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)

        failed = self.session(701_230_004)
        failed_program = self.search_program(failed, self.moggcatcher)
        self.begin_search(
            failed,
            failed_program,
            ref="S-MOGGCATCHER-FAIL-TO-FIND",
        )
        failed_before_shuffle = failed.state.players["B"].stats.get(
            "shuffle_count", 0
        )
        failed_result = failed.act(
            "pilot:B",
            {
                "action_id": "choose",
                "search_cards": [],
                "plan": "DEVELOP_TYPED_SEARCH",
                "reason": "Exercise the restrictive hidden-search fail-to-find rule.",
            },
        )
        self.assertTrue(failed_result.ok, failed_result.summary)
        self.assertEqual(
            failed_before_shuffle + 1,
            failed.state.players["B"].stats["shuffle_count"],
        )

        mutated = self.session(701_230_002)
        mutant_card = self.add_changeling_to_library(mutated)
        mutant_program = self.search_program(mutated, self.moggcatcher)
        with patch(
            "quorune.selection.searching.object_matches_query",
            return_value=False,
        ):
            options = mutated.engine._semantic_search_options(
                "B", mutant_program.effects[0]
            )
        self.assertNotIn(mutant_card.ref, {value["id"] for value in options})

    def test_multi_land_search_commits_one_tapped_simultaneous_batch(self):
        session = self.session(701_230_003)
        program = self.search_program(session, self.blighted)
        self.begin_search(session, program, ref="S-BLIGHTED-SEARCH")
        packet = session.packet("pilot:B", full=True)
        candidates = packet["decision"]["ctx"]["search_cards"]
        self.assertGreaterEqual(len(candidates), 2)
        refs = [value["id"] for value in candidates[:2]]
        before_shuffle = session.state.players["B"].stats.get(
            "shuffle_count", 0
        )
        result = session.act(
            "pilot:B",
            {
                "action_id": "choose",
                "search_cards": refs,
                "plan": "DEVELOP_TYPED_SEARCH",
                "reason": "Move two basic lands in one tapped entry batch.",
            },
        )
        self.assertTrue(result.ok, result.summary)
        cards = [
            next(card for card in session.state.cards.values() if card.ref == ref)
            for ref in refs
        ]
        self.assertTrue(all(card.zone == "battlefield" for card in cards))
        self.assertTrue(all(card.tapped for card in cards))
        self.assertEqual(1, len({card.zone_timestamp for card in cards}))
        self.assertEqual(
            before_shuffle + 1,
            session.state.players["B"].stats["shuffle_count"],
        )


if __name__ == "__main__":
    unittest.main()
