from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT, keep_all, make_session
from scripts.build_test_database import build_fixture_database
from quorune.carddb import CardDatabase, CardRecord
from quorune.compiler.spell_additional_cost_templates import (
    FixedZoneChangeAdditionalCostTemplate,
    fixed_zone_change_additional_cost_template,
)
from quorune.deck import DeckLoader
from quorune.errors import GameRuleError
from quorune.oracle_ir import compile_oracle_card, generated_programs
from quorune.projection import StateProjector
from quorune.record import (
    authoritative_state_hash,
    checkpoint_envelope,
    replay_record,
)
from quorune.replacement.replay import ReplacementContinuation
from quorune.replacement_effects import ReplacementEffectError
from quorune.rules.capabilities import CapabilityRegistry
from quorune.session import CommanderSession
from quorune.rules.casting_additional_costs import (
    AdditionalCostError,
    FixedZoneChangeAdditionalCost,
)


REGISTRY_PATH = ROOT / "quorune" / "rules" / "capability-registry.json"


def trusted_registry() -> CapabilityRegistry:
    registry = CapabilityRegistry.from_path(REGISTRY_PATH)
    registry.mark_evidence_verified("0" * 64)
    return registry


def fixture_card(text: str) -> CardRecord:
    return CardRecord(
        oracle_id="00000000-0000-4000-8000-000000601022",
        name="Fixed Zone Change Cost Fixture",
        mana_cost="{1}{B}",
        mana_value=2.0,
        type_line="Sorcery",
        oracle_text=text,
        power=None,
        toughness=None,
        loyalty=None,
        defense=None,
        colors=("B",),
        color_identity=("B",),
        keywords=(),
        produced_mana=(),
        layout="normal",
        released_at="2026-01-01",
        legalities={"commander": "legal"},
        faces=(),
        raw={},
    )


def zone_change_cost(clause: str) -> dict:
    template = fixed_zone_change_additional_cost_template(clause)
    if template is None:
        raise AssertionError(f"Fixture clause did not compile: {clause}")
    return dict(template.cost_schema)


class _NoRulingsDatabase:
    @staticmethod
    def rulings(_record: CardRecord) -> tuple[()]:
        return ()


class FixedZoneChangeAdditionalCostCompilerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.capabilities = trusted_registry()

    def compile(self, text: str):
        return compile_oracle_card(
            fixture_card(text),
            capability_registry=self.capabilities,
            capability_profile="commander_review",
        )

    def test_fixed_zone_change_costs_compile_source_spanned_programs(self):
        examples = (
            (
                "As an additional cost to cast this spell, discard a card.",
                "discard_one",
                "casting.additional_cost.zone_change.fixed_discard",
            ),
            (
                "As an additional cost to cast this spell, discard a land card.",
                "discard_one",
                "casting.additional_cost.zone_change.fixed_discard",
            ),
            (
                "As an additional cost to cast this spell, discard a red or green card.",
                "discard_one",
                "casting.additional_cost.zone_change.fixed_discard",
            ),
            (
                "As an additional cost to cast this spell, exile a creature card from your graveyard.",
                "exile_one_from_graveyard",
                "casting.additional_cost.zone_change.fixed_exile",
            ),
            (
                "As an additional cost to cast this spell, exile an instant or sorcery card from your graveyard.",
                "exile_one_from_graveyard",
                "casting.additional_cost.zone_change.fixed_exile",
            ),
            (
                "As an additional cost to cast this spell, exile a creature you control.",
                "exile_one_from_battlefield",
                "casting.additional_cost.zone_change.fixed_exile",
            ),
            (
                "As an additional cost to cast this spell, return a land you control to its owner's hand.",
                "return_one_to_owner_hand",
                "casting.additional_cost.zone_change.fixed_return_to_owner_hand",
            ),
            (
                "As an additional cost to cast this spell, return a permanent you control to its owner's hand.",
                "return_one_to_owner_hand",
                "casting.additional_cost.zone_change.fixed_return_to_owner_hand",
            ),
            (
                "As an additional cost to cast this spell, sacrifice a legendary creature.",
                "sacrifice_one",
                "casting.additional_cost.fixed_sacrifice",
            ),
            (
                "As an additional cost to cast this spell, sacrifice a Goblin.",
                "sacrifice_one",
                "casting.additional_cost.fixed_sacrifice",
            ),
            (
                "As an additional cost to cast this spell, sacrifice a nonland permanent.",
                "sacrifice_one",
                "casting.additional_cost.fixed_sacrifice",
            ),
        )
        for clause, operation, capability in examples:
            text = f"{clause}\nDraw two cards."
            with self.subTest(clause=clause):
                ir = self.compile(text)
                self.assertEqual("exact", ir.status, ir.to_dict())
                node = ir.faces[0].nodes[0]
                self.assertEqual(text, node.text)
                self.assertEqual(text, text[node.span.start : node.span.end])
                descriptor = node.cost["additional_costs"][0]
                self.assertEqual(operation, descriptor["operation"])
                self.assertIn(capability, node.capability_dependencies)
                programs = generated_programs(
                    _NoRulingsDatabase(),  # type: ignore[arg-type]
                    fixture_card(text),
                    trust_level="trusted",
                    capability_registry=self.capabilities,
                    capability_profile="commander_review",
                )
                self.assertEqual(1, len(programs))
                self.assertEqual(node.cost, programs[0].cost_schema)
                self.assertFalse(programs[0].requires_arbiter)

    def test_fixed_zone_change_cost_grammar_fails_closed(self):
        unsupported = (
            "discard two cards",
            "discard a card at random",
            "discard X land cards",
            "exile two creature cards from your graveyard",
            "exile X cards from your graveyard",
            "exile a modified creature you control",
            "return X Swamps you control to their owner's hand",
            "return a tapped creature you control to its owner's hand",
            "sacrifice a modified creature",
            "sacrifice a creature with power 4 or greater",
            "sacrifice a NotARealSubtype",
        )
        for clause in unsupported:
            text = (
                f"As an additional cost to cast this spell, {clause}.\n"
                "Draw two cards."
            )
            with self.subTest(clause=clause):
                ir = self.compile(text)
                self.assertNotEqual("exact", ir.status)
                self.assertTrue(ir.material_residuals)

    def test_unsupported_cost_preserves_independent_result_residuals(self):
        supported_result = (
            "As an additional cost to cast this spell, discard two cards.\n"
            "Draw three cards."
        )
        ir = self.compile(supported_result)
        self.assertEqual(1, len(ir.material_residuals))
        cost_residual = ir.material_residuals[0]
        self.assertEqual("spell_additional_cost", cost_residual.kind)
        self.assertEqual(
            "As an additional cost to cast this spell, discard two cards.",
            cost_residual.text,
        )
        self.assertEqual(
            ("typed spell additional-cost clause",),
            cost_residual.blockers,
        )
        self.assertEqual(
            cost_residual.text,
            supported_result[cost_residual.span.start : cost_residual.span.end],
        )

        unsupported_result = (
            "As an additional cost to cast this spell, pay X life.\n"
            "This spell deals X damage divided as you choose among any number "
            "of target creatures."
        )
        ir = self.compile(unsupported_result)
        self.assertEqual(
            [
                ("spell_additional_cost", ("typed spell additional-cost clause",)),
                ("spell_effect", ()),
            ],
            [(row.kind, row.blockers) for row in ir.material_residuals],
        )
        self.assertEqual(
            tuple(row.residual_id for row in ir.material_residuals),
            ir.faces[0].nodes[0].residual_ids,
        )
        self.assertEqual(
            [
                "As an additional cost to cast this spell, pay X life.",
                (
                    "This spell deals X damage divided as you choose among "
                    "any number of target creatures."
                ),
            ],
            [row.text for row in ir.material_residuals],
        )
        for residual in ir.material_residuals:
            self.assertEqual(
                residual.text,
                unsupported_result[residual.span.start : residual.span.end],
            )

    def test_supported_cost_keeps_unsupported_result_residual_result_scoped(self):
        text = (
            "As an additional cost to cast this spell, discard a card.\n"
            "Draw cards equal to the discarded card's mana value."
        )

        ir = self.compile(text)

        self.assertEqual(1, len(ir.material_residuals))
        residual = ir.material_residuals[0]
        self.assertEqual("spell_effect", residual.kind)
        self.assertEqual((), residual.blockers)
        self.assertEqual(
            "Draw cards equal to the discarded card's mana value.",
            residual.text,
        )
        self.assertEqual(
            residual.text,
            text[residual.span.start : residual.span.end],
        )
        self.assertEqual((residual.residual_id,), ir.faces[0].nodes[0].residual_ids)

    def test_unsupported_cost_preserves_result_composition_blockers(self):
        text = (
            "As an additional cost to cast this spell, discard two cards.\n"
            "Draw two cards.\n"
            "You gain 2 life."
        )
        ir = self.compile(text)
        self.assertEqual(
            [
                ("typed spell additional-cost clause",),
                (
                    "additional-cost composition",
                    "ordered multi-clause spell resolution",
                ),
            ],
            [row.blockers for row in ir.material_residuals],
        )
        self.assertEqual(
            "Draw two cards.\nYou gain 2 life.",
            ir.material_residuals[1].text,
        )
        for residual in ir.material_residuals:
            self.assertEqual(
                residual.text,
                text[residual.span.start : residual.span.end],
            )

    def test_unsupported_cost_preserves_result_dependency_blockers(self):
        value = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
        dependency = next(
            row
            for row in value["capabilities"]
            if row["id"] == "zone.draw.library_to_hand"
        )
        dependency["status"] = "blocked"
        dependency["blockers"] = ["test mutation"]
        registry = CapabilityRegistry(value)
        registry.mark_evidence_verified("0" * 64)
        text = (
            "As an additional cost to cast this spell, discard two cards.\n"
            "Draw three cards."
        )
        ir = compile_oracle_card(
            fixture_card(text),
            capability_registry=registry,
            capability_profile="commander_review",
        )
        self.assertEqual(
            ["spell_additional_cost", "dependency_contract"],
            [row.kind for row in ir.material_residuals],
        )
        self.assertTrue(
            any(
                blocker.startswith("capability:")
                and "zone.draw.library_to_hand" in blocker
                for blocker in ir.material_residuals[1].blockers
            ),
            ir.material_residuals[1].blockers,
        )

    def test_fixed_zone_change_descriptor_is_closed_and_immutable(self):
        descriptor = zone_change_cost(
            "As an additional cost to cast this spell, discard a red or green card."
        )["additional_costs"][0]
        parsed = FixedZoneChangeAdditionalCost.from_descriptor(descriptor)
        caller = deepcopy(descriptor)
        caller["predicate"]["colors_any"].append("U")
        self.assertEqual(("G", "R"), parsed.predicate.colors_any)
        self.assertEqual(descriptor, parsed.to_descriptor())
        for mutation in (
            {**descriptor, "count": True},
            {**descriptor, "count": 2},
            {**descriptor, "choice_field": "cost_cards"},
            {**descriptor, "operation": "discard_many"},
            {**descriptor, "unknown": True},
            {
                **descriptor,
                "predicate": {
                    **descriptor["predicate"],
                    "owner": None,
                },
            },
            {
                **descriptor,
                "predicate": {
                    **descriptor["predicate"],
                    "include_phased_out": True,
                },
            },
            {
                **descriptor,
                "predicate": {
                    **descriptor["predicate"],
                    "supertypes_all": ["world"],
                },
            },
        ):
            with self.subTest(mutation=mutation):
                with self.assertRaises(AdditionalCostError):
                    FixedZoneChangeAdditionalCost.from_descriptor(mutation)

        extended = zone_change_cost(
            "As an additional cost to cast this spell, sacrifice a Vampire or Zombie."
        )["additional_costs"][0]
        for predicate_update in (
            {"excluded_subtypes": ["human"]},
            {"colorless": True},
            {
                "subtypes_all": ["vampire"],
                "subtypes_any": ["vampire", "zombie"],
            },
        ):
            mutation = {
                **extended,
                "predicate": {
                    **extended["predicate"],
                    **predicate_update,
                },
            }
            with self.subTest(predicate_update=predicate_update):
                with self.assertRaises(AdditionalCostError):
                    FixedZoneChangeAdditionalCost.from_descriptor(mutation)

    def test_fixed_zone_change_capability_dependencies_fail_closed(self):
        clauses = (
            "As an additional cost to cast this spell, discard a card.",
            "As an additional cost to cast this spell, exile a creature card from your graveyard.",
            "As an additional cost to cast this spell, return a permanent you control to its owner's hand.",
        )
        for dependency_id in (
            "trigger.event.normalized_zone_change",
            "zone.change.destination_replacement",
        ):
            value = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
            dependency = next(
                row
                for row in value["capabilities"]
                if row["id"] == dependency_id
            )
            dependency["status"] = "blocked"
            dependency["blockers"] = ["test mutation"]
            registry = CapabilityRegistry(value)
            registry.mark_evidence_verified("0" * 64)
            for clause in clauses:
                with self.subTest(dependency=dependency_id, clause=clause):
                    ir = compile_oracle_card(
                        fixture_card(f"{clause}\nDraw two cards."),
                        capability_registry=registry,
                        capability_profile="commander_review",
                    )
                    self.assertNotEqual("exact", ir.status)
                    self.assertTrue(ir.material_residuals)

    def test_fixed_zone_change_compiler_mutant_is_killed(self):
        text = (
            "As an additional cost to cast this spell, discard a card.\n"
            "Draw two cards."
        )

        def assert_exact() -> None:
            ir = self.compile(text)
            self.assertEqual("exact", ir.status)
            self.assertEqual(
                "discard_one",
                ir.faces[0].nodes[0].cost["additional_costs"][0][
                    "operation"
                ],
            )

        assert_exact()
        with patch(
            "quorune.compiler.spell_additional_cost_nodes."
            "fixed_zone_change_additional_cost_template",
            return_value=None,
        ):
            with self.assertRaises(AssertionError):
                assert_exact()

    def test_fixed_zone_change_template_identity_is_canonical(self):
        clause = (
            "As an additional cost to cast this spell, discard a red or green card."
        )
        first = fixed_zone_change_additional_cost_template(clause)
        second = fixed_zone_change_additional_cost_template(clause)
        self.assertEqual(first, second)
        self.assertIsInstance(first, FixedZoneChangeAdditionalCostTemplate)
        assert first is not None
        self.assertEqual(("G", "R"), first.predicate.colors_any)
        self.assertEqual(first.template_id, second.template_id)  # type: ignore[union-attr]

        subtype_union = fixed_zone_change_additional_cost_template(
            "As an additional cost to cast this spell, sacrifice a Vampire or Zombie."
        )
        token = fixed_zone_change_additional_cost_template(
            "As an additional cost to cast this spell, sacrifice a Caribou token."
        )
        nontoken = fixed_zone_change_additional_cost_template(
            "As an additional cost to cast this spell, sacrifice a nontoken Caribou."
        )
        self.assertIsNotNone(subtype_union)
        self.assertIsNotNone(token)
        self.assertIsNotNone(nontoken)
        identities = {
            template.template_id
            for template in (subtype_union, token, nontoken)
            if template is not None
        }
        self.assertEqual(3, len(identities))
        self.assertTrue(
            any("subtypes-any-vampire-zombie" in value for value in identities)
        )
        self.assertTrue(any(value.endswith("-token-v1") for value in identities))
        self.assertTrue(
            any(value.endswith("-nontoken-v1") for value in identities)
        )


class FixedZoneChangeAdditionalCostRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temporary = tempfile.TemporaryDirectory()
        database = Path(cls.temporary.name) / "zone-change-cast-cost.sqlite3"
        build_fixture_database(
            [ROOT / "tests" / "fixtures" / "scryfall-exact-lists.json"],
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

    @classmethod
    def tearDownClass(cls) -> None:
        cls.db.close()
        cls.temporary.cleanup()

    def session(self, seed: int, *, players: int = 2):
        session = make_session(
            self.db,
            self.zimone,
            self.mishra,
            players=players,
            seed=seed,
            auto_pass_empty=False,
        )
        keep_all(session)
        engine = session.engine
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine.state.active_player = "A"
        engine.state.phase = "precombat_main"
        engine.state.step = "main"
        engine.state.stack.clear()
        engine.state.priority_player = "A"
        engine.state.priority_passes = []
        session.commands.clear()
        session.decisions.clear()
        return session

    @staticmethod
    def card(engine, owner: str, name: str):
        return next(
            card
            for card in engine.state.cards.values()
            if card.owner == owner and card.printed_name == name
        )

    def stage_spell(self, session, clause: str):
        engine = session.engine
        spell = self.card(engine, "A", "Diabolic Intent")
        engine.move_card(spell.object_id, "hand", log=False)
        program = engine.semantics.get(f"{spell.oracle_id}:spell:front")
        self.assertIsNotNone(program)
        program.cost_schema = zone_change_cost(clause)
        engine.semantics.put(program)
        engine.state.players["A"].mana_pool.update({"B": 1, "C": 1})
        return spell

    @staticmethod
    def issue_priority(session) -> None:
        engine = session.engine
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine._grant_priority("A")
        engine._issue_priority("A")

    def test_each_fixed_zone_change_cost_commits_to_operation_destination(self):
        examples = (
            (
                "As an additional cost to cast this spell, discard a card.",
                "hand",
                "graveyard",
                "discard_cards",
            ),
            (
                "As an additional cost to cast this spell, exile a creature card from your graveyard.",
                "graveyard",
                "exile",
                "exile_cards",
            ),
            (
                "As an additional cost to cast this spell, exile a creature you control.",
                "battlefield",
                "exile",
                "exile_cards",
            ),
            (
                "As an additional cost to cast this spell, return a creature you control to its owner's hand.",
                "battlefield",
                "hand",
                "return_cards",
            ),
        )
        for index, (clause, origin, destination, field) in enumerate(examples):
            with self.subTest(clause=clause):
                session = self.session(601220 + index)
                engine = session.engine
                spell = self.stage_spell(session, clause)
                paid = self.card(engine, "A", "Birds of Paradise")
                move_kwargs = {"log": False}
                if origin == "battlefield":
                    move_kwargs["controller"] = "A"
                engine.move_card(paid.object_id, origin, **move_kwargs)
                action = next(
                    value
                    for value in engine._priority_action_hints("A")["actions"]
                    if value.get("card") == spell.ref
                )
                schema = action["cost_options"][0]["choice_schema"][field]
                self.assertEqual(origin, schema["zone"])
                self.assertEqual(destination, schema["destination"])
                self.assertIn(paid.ref, schema["legal_refs"])
                self.issue_priority(session)
                session.initial_checkpoint = checkpoint_envelope(engine.state)
                session.commands.clear()
                session.decisions.clear()
                result = session.act(
                    "pilot:A", {"a": "cast", "card": spell.ref, field: [paid.ref]}
                )
                self.assertTrue(result.ok, result.summary)
                self.assertEqual(destination, paid.zone)
                self.assertEqual("stack", spell.zone)
                expected_hash = authoritative_state_hash(engine.state)
                with tempfile.TemporaryDirectory() as temporary:
                    record_dir = Path(temporary) / f"zone-change-{index}"
                    session.save(record_dir)
                    replay = replay_record(record_dir, self.db, verify=True)
                self.assertTrue(replay["ok"], replay)
                self.assertEqual(expected_hash, replay["final_state_hash"])

    def test_private_discard_offer_is_seat_scoped(self):
        session = self.session(601224, players=4)
        engine = session.engine
        spell = self.stage_spell(
            session,
            "As an additional cost to cast this spell, discard a card.",
        )
        paid = self.card(engine, "A", "Birds of Paradise")
        engine.move_card(paid.object_id, "hand", log=False)
        self.issue_priority(session)
        projector = StateProjector(self.db, engine.state)
        own = projector._decision("pilot:A")
        self.assertIsNotNone(own)
        self.assertIn(paid.ref, json.dumps(own, sort_keys=True))
        for seat in ("B", "C", "D"):
            self.assertIsNone(projector._decision(f"pilot:{seat}"))
            self.assertNotIn(
                paid.ref,
                json.dumps(projector._snapshot(f"pilot:{seat}"), sort_keys=True),
            )
        self.assertEqual("hand", spell.zone)

    def test_qualified_sacrifice_uses_pinned_subtype_predicate(self):
        session = self.session(601228)
        engine = session.engine
        spell = self.stage_spell(
            session,
            "As an additional cost to cast this spell, sacrifice a Goblin.",
        )
        goblin_ref = engine.create_token(
            "A",
            name="Typed Cost Goblin",
            characteristics={
                "type_line": "Token Creature — Goblin",
                "power": "1",
                "toughness": "1",
            },
        )[0]
        goblin_object_id = engine._resolve_object(
            "A", goblin_ref, zones={"battlefield"}
        ).object_id
        nongoblin = self.card(engine, "A", "Birds of Paradise")
        engine.move_card(
            nongoblin.object_id,
            "battlefield",
            controller="A",
            log=False,
        )
        action = next(
            value
            for value in engine._priority_action_hints("A")["actions"]
            if value.get("card") == spell.ref
        )
        legal = action["cost_options"][0]["choice_schema"][
            "sacrifice_cards"
        ]["legal_refs"]
        self.assertIn(goblin_ref, legal)
        self.assertNotIn(nongoblin.ref, legal)
        engine._cast(
            "A", {"card": spell.ref, "sacrifice_cards": [goblin_ref]}
        )
        self.assertEqual("outside", engine.state.cards[goblin_object_id].zone)
        self.assertEqual("stack", spell.zone)

    def test_stale_zone_change_selection_rolls_back(self):
        session = self.session(601225)
        engine = session.engine
        spell = self.stage_spell(
            session,
            "As an additional cost to cast this spell, discard a card.",
        )
        paid = self.card(engine, "A", "Birds of Paradise")
        engine.move_card(paid.object_id, "hand", log=False)
        action = next(
            value
            for value in engine._priority_action_hints("A")["actions"]
            if value.get("card") == spell.ref
        )
        self.assertIn(
            paid.ref,
            action["cost_options"][0]["choice_schema"]["discard_cards"][
                "legal_refs"
            ],
        )
        engine.move_card(paid.object_id, "graveyard", log=False)
        before = authoritative_state_hash(engine.state)
        with self.assertRaises(GameRuleError):
            engine._cast(
                "A", {"card": spell.ref, "discard_cards": [paid.ref]}
            )
        self.assertEqual(before, authoritative_state_hash(engine.state))
        self.assertEqual("hand", spell.zone)
        self.assertEqual(1, engine.state.players["A"].mana_pool["B"])
        self.assertEqual(1, engine.state.players["A"].mana_pool["C"])

    def test_return_cost_routes_controlled_permanent_to_owner_hand(self):
        session = self.session(601226, players=4)
        engine = session.engine
        spell = self.stage_spell(
            session,
            "As an additional cost to cast this spell, return a creature you control to its owner's hand.",
        )
        paid = self.card(engine, "C", "Birds of Paradise")
        engine.move_card(
            paid.object_id,
            "battlefield",
            controller="A",
            log=False,
        )
        engine._cast("A", {"card": spell.ref, "return_cards": [paid.ref]})
        self.assertEqual("C", paid.owner)
        self.assertEqual("hand", paid.zone)
        self.assertIn(
            paid.object_id, engine.state.players["C"].zones["hand"]
        )
        self.assertNotIn(
            paid.object_id, engine.state.players["A"].zones["hand"]
        )

    def test_discard_replacement_suspends_before_mutation_and_replays(self):
        session = self.session(601227, players=4)
        engine = session.engine
        spell = self.stage_spell(
            session,
            "As an additional cost to cast this spell, discard a card.",
        )
        paid = self.card(engine, "A", "Birds of Paradise")
        engine.move_card(paid.object_id, "hand", log=False)
        voidwalker = self.card(engine, "A", "Dauthi Voidwalker")
        engine.move_card(
            voidwalker.object_id, "battlefield", controller="B", log=False
        )
        engine.create_token(
            "B",
            name="",
            copy_of=voidwalker.ref,
            reason="fixed discard replacement ordering witness",
        )
        self.issue_priority(session)
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()

        result = session.act(
            "pilot:A",
            {
                "a": "cast",
                "card": spell.ref,
                "discard_cards": [paid.ref],
            },
        )
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("replacement.order", engine.state.pending_decision.kind)
        self.assertEqual("hand", paid.zone)
        self.assertEqual("hand", spell.zone)
        self.assertFalse(engine.state.stack)
        self.assertEqual(1, engine.state.players["A"].mana_pool["B"])
        self.assertEqual(1, engine.state.players["A"].mana_pool["C"])

        projector = StateProjector(self.db, engine.state)
        projected = projector._decision("pilot:A")
        self.assertIsNotNone(projected)
        for seat in ("B", "C", "D"):
            self.assertIsNone(projector._decision(f"pilot:{seat}"))
        assert projected is not None
        selected = projected["ctx"]["options"][0]["id"]

        continuation = deepcopy(engine.state.pending_decision.continuation)
        restored = ReplacementContinuation.from_dict(continuation)
        self.assertEqual("priority_action_cost", restored.resume_kind)
        self.assertEqual("zone.change", restored.batch.events[0].kind)
        tampered = deepcopy(continuation)
        tampered["replacement_batch"]["events"][0]["payload"][
            "destination"
        ] = "hand"
        with self.assertRaisesRegex(ReplacementEffectError, "continuation event"):
            ReplacementContinuation.from_dict(tampered)

        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / "discard-replacement-record"
            session.save(record_dir)
            restarted = CommanderSession.load(self.db, record_dir)
            restarted_projector = StateProjector(
                self.db, restarted.engine.state
            )
            restarted_packet = restarted_projector._decision("pilot:A")
            self.assertIsNotNone(restarted_packet)
            for seat in ("B", "C", "D"):
                self.assertIsNone(
                    restarted_projector._decision(f"pilot:{seat}")
                )
            restarted_selection = restarted_packet["ctx"]["options"][0]["id"]
            self.assertEqual(selected, restarted_selection)
            result = restarted.act(
                "pilot:A",
                {"a": "choose", "replacement": restarted_selection},
            )
            self.assertTrue(result.ok, result.summary)
            self.assertEqual(
                "exile", restarted.engine.state.cards[paid.object_id].zone
            )
            self.assertEqual(
                "stack", restarted.engine.state.cards[spell.object_id].zone
            )
            expected_hash = authoritative_state_hash(restarted.engine.state)
            restarted.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(expected_hash, replay["final_state_hash"])


if __name__ == "__main__":
    unittest.main()
