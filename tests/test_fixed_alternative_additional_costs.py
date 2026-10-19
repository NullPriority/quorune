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
    fixed_alternative_additional_cost_template,
    fixed_life_payment_additional_cost_template,
)
from quorune.deck import DeckLoader
from quorune.errors import GameRuleError
from quorune.oracle_ir import compile_oracle_card
from quorune.projection import StateProjector
from quorune.record import (
    authoritative_state_hash,
    checkpoint_envelope,
    replay_record,
)
from quorune.rules.capabilities import CapabilityRegistry
from quorune.rules.casting.commit import commit_cast
from quorune.rules.casting.model import (
    CastProposalError,
    CastProposalRequest,
)
from quorune.rules.casting.proposal import build_cast_proposal
from quorune.rules.casting_additional_cost_groups import (
    FixedAlternativeAdditionalCost,
)
from quorune.rules.casting_additional_costs import AdditionalCostError
from quorune.session import CommanderSession


REGISTRY_PATH = ROOT / "quorune" / "rules" / "capability-registry.json"


def trusted_registry() -> CapabilityRegistry:
    registry = CapabilityRegistry.from_path(REGISTRY_PATH)
    registry.mark_evidence_verified("0" * 64)
    return registry


def fixture_card(text: str) -> CardRecord:
    return CardRecord(
        oracle_id="00000000-0000-4000-8000-000000601023",
        name="Fixed Alternative Cost Fixture",
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


def compiled_cost(clause: str) -> dict:
    alternative = fixed_alternative_additional_cost_template(clause)
    if alternative is not None:
        return dict(alternative.cost_schema)
    life = fixed_life_payment_additional_cost_template(clause)
    if life is not None:
        return dict(life.cost_schema)
    raise AssertionError(f"Fixture clause did not compile: {clause}")


class FixedAlternativeAdditionalCostCompilerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.capabilities = trusted_registry()

    def compile(self, text: str):
        return compile_oracle_card(
            fixture_card(text),
            capability_registry=self.capabilities,
            capability_profile="commander_review",
        )

    def test_fixed_alternative_costs_compile_source_spanned_programs(self):
        clauses = (
            "discard a card or pay {5}",
            "pay {4} or sacrifice an artifact or creature",
            "sacrifice a creature or discard a card",
            "discard a card or pay 3 life",
            "pay 5 life or sacrifice a creature or enchantment",
            "exile a creature card from your graveyard or pay {4}",
        )
        for clause in clauses:
            text = (
                f"As an additional cost to cast this spell, {clause}.\n"
                "Draw two cards."
            )
            with self.subTest(clause=clause):
                ir = self.compile(text)
                self.assertEqual("exact", ir.status, ir.to_dict())
                node = ir.faces[0].nodes[0]
                self.assertEqual(text, node.text)
                self.assertEqual(text, text[node.span.start : node.span.end])
                descriptor = node.cost["additional_costs"][0]
                self.assertEqual("alternative_additional_cost", descriptor["kind"])
                self.assertEqual(2, len(descriptor["options"]))
                self.assertIn(
                    "casting.additional_cost.fixed_alternative",
                    node.capability_dependencies,
                )

    def test_alternative_descriptor_and_unsupported_grammar_fail_closed(self):
        unsupported = (
            "you may discard a card or pay {5}",
            "discard a card at random or pay {5}",
            "discard a card or pay {X}",
            "tap an untapped creature with flying you control or pay {3}",
            "pay {2} and discard a card",
            "pay {2} or pay {2}",
            "pay 2 life or pay {2} or discard a card",
            "pay X life or pay {1}",
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

        announced = "As an additional cost to cast this spell, pay X life.\nDraw two cards."
        self.assertEqual("exact", self.compile(announced).status)
        self.assertIsNone(fixed_life_payment_additional_cost_template(announced))
        promoted=self.compile('As an additional cost to cast this spell, tap an untapped creature you control or pay {3}.\nDraw two cards.')
        self.assertEqual('exact',promoted.status,promoted.material_residuals)
        self.assertIn('casting.additional_cost.fixed_tap',promoted.faces[0].nodes[0].capability_dependencies)
        descriptor = compiled_cost(
            "As an additional cost to cast this spell, "
            "discard a card or pay 3 life."
        )["additional_costs"][0]
        parsed = FixedAlternativeAdditionalCost.from_descriptor(descriptor)
        caller = deepcopy(descriptor)
        caller["options"][1]["amount"] = 9
        self.assertEqual(descriptor, parsed.to_descriptor())
        mutations = (
            {**descriptor, "schema_version": 2},
            {**descriptor, "unknown": True},
            {**descriptor, "options": descriptor["options"][:1]},
            {
                **descriptor,
                "options": [descriptor["options"][0]] * 2,
            },
            {
                **descriptor,
                "options": [
                    descriptor["options"][0],
                    {
                        "schema_version": 1,
                        "kind": "alternative_additional_cost",
                        "options": descriptor["options"],
                    },
                ],
            },
        )
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                with self.assertRaises(AdditionalCostError):
                    FixedAlternativeAdditionalCost.from_descriptor(mutation)

    def test_alternative_capability_closure_fails_closed(self):
        text = (
            "As an additional cost to cast this spell, "
            "discard a card or pay 3 life.\n"
            "Draw two cards."
        )
        for dependency_id in (
            "casting.additional_cost.fixed_alternative",
            "casting.additional_cost.fixed_life_payment",
            "zone.change.destination_replacement",
            "trigger.event.normalized_zone_change",
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
            with self.subTest(dependency=dependency_id):
                ir = compile_oracle_card(
                    fixture_card(text),
                    capability_registry=registry,
                    capability_profile="commander_review",
                )
                self.assertNotEqual("exact", ir.status)
                self.assertTrue(ir.material_residuals)

    def test_alternative_compiler_mutant_is_killed(self):
        alternative = (
            "As an additional cost to cast this spell, "
            "discard a card or pay {5}.\n"
            "Draw two cards."
        )
        fixed_life = (
            "As an additional cost to cast this spell, pay 3 life.\n"
            "Draw two cards."
        )

        def assert_exact(text: str) -> None:
            self.assertEqual("exact", self.compile(text).status)

        assert_exact(alternative)
        assert_exact(fixed_life)
        with patch(
            "quorune.compiler.spell_additional_cost_nodes."
            "fixed_alternative_additional_cost_template",
            return_value=None,
        ):
            with self.assertRaises(AssertionError):
                assert_exact(alternative)
        with patch(
            "quorune.compiler.spell_additional_cost_nodes."
            "fixed_life_payment_additional_cost_template",
            return_value=None,
        ):
            with self.assertRaises(AssertionError):
                assert_exact(fixed_life)


class FixedAlternativeAdditionalCostRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temporary = tempfile.TemporaryDirectory()
        database = Path(cls.temporary.name) / "alternative-cast-cost.sqlite3"
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

    def stage_spell(self, session, clause: str, *, mana: dict[str, int]):
        engine = session.engine
        spell = self.card(engine, "A", "Diabolic Intent")
        engine.move_card(spell.object_id, "hand", log=False)
        program = engine.semantics.get(f"{spell.oracle_id}:spell:front")
        self.assertIsNotNone(program)
        program.cost_schema = compiled_cost(clause)
        engine.semantics.put(program)
        engine.state.players["A"].mana_pool = {
            color: int(mana.get(color, 0)) for color in "WUBRGC"
        }
        return spell

    @staticmethod
    def issue_priority(session) -> None:
        engine = session.engine
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine._grant_priority("A")
        engine._issue_priority("A")

    @staticmethod
    def cast_action(engine, spell):
        return next(
            value
            for value in engine._priority_action_hints("A")["actions"]
            if value.get("card") == spell.ref
        )

    def test_cost_options_require_explicit_branch_and_commit_selected_payment(self):
        clause = (
            "As an additional cost to cast this spell, "
            "discard a card or pay {5}."
        )
        session = self.session(601230)
        engine = session.engine
        spell = self.stage_spell(session, clause, mana={"B": 1, "C": 6})
        paid = self.card(engine, "A", "Birds of Paradise")
        engine.move_card(paid.object_id, "hand", log=False)
        action = self.cast_action(engine, spell)
        options = action["cost_options"]
        self.assertEqual(
            {
                "normal+additional-alternative-1",
                "normal+additional-alternative-2",
            },
            {option["id"] for option in options},
        )
        discard = next(option for option in options if "choice_schema" in option)
        mana = next(option for option in options if "choice_schema" not in option)
        self.assertNotIn(
            spell.ref,
            discard["choice_schema"]["discard_cards"]["legal_refs"],
        )
        self.assertIn(
            paid.ref,
            discard["choice_schema"]["discard_cards"]["legal_refs"],
        )
        self.assertEqual(6, mana["requirements"]["GENERIC"])
        before = authoritative_state_hash(engine.state)
        with self.assertRaises(GameRuleError):
            engine._cast("A", {"card": spell.ref})
        self.assertEqual(before, authoritative_state_hash(engine.state))
        engine._cast(
            "A",
            {
                "card": spell.ref,
                "cost_option": mana["id"],
                "pay": "manual",
                "payment": {"B": 1, "C": 6},
            },
        )
        self.assertEqual("hand", paid.zone)
        self.assertEqual("stack", spell.zone)

        other = self.session(601231)
        other_engine = other.engine
        other_spell = self.stage_spell(
            other, clause, mana={"B": 1, "C": 1}
        )
        other_paid = self.card(other_engine, "A", "Birds of Paradise")
        other_engine.move_card(other_paid.object_id, "hand", log=False)
        other_option = self.cast_action(other_engine, other_spell)[
            "cost_options"
        ][0]
        other_engine._cast(
            "A",
            {
                "card": other_spell.ref,
                "cost_option": other_option["id"],
                "discard_cards": [other_paid.ref],
                "pay": "manual",
                "payment": {"B": 1, "C": 1},
            },
        )
        self.assertEqual("graveyard", other_paid.zone)
        self.assertEqual("stack", other_spell.zone)

    def test_fixed_life_and_mana_alternatives_share_total_cost_options(self):
        session = self.session(601232)
        engine = session.engine
        spell = self.stage_spell(
            session,
            "As an additional cost to cast this spell, pay 5 life or pay {5}.",
            mana={"B": 1, "C": 6},
        )
        engine._add_restricted_mana(
            "A", "artifact_spell_or_ability", {"C": 5}
        )
        restricted = self.cast_action(engine, spell)["cost_options"]
        self.assertEqual(1, len(restricted))
        self.assertEqual("Pay 5 life", restricted[0]["label"])

        with patch(
            "quorune.rules.casting.costs._static_generic_reduction",
            return_value=5,
        ):
            options = self.cast_action(engine, spell)["cost_options"]
            self.assertEqual(2, len(options))
            mana = next(option for option in options if option["label"] == "Pay {5}")
            self.assertEqual(1, mana["requirements"]["GENERIC"])
            engine._cast(
                "A",
                {
                    "card": spell.ref,
                    "cost_option": mana["id"],
                    "pay": "manual",
                    "payment": {"B": 1, "C": 1},
                },
            )
        self.assertEqual(40, engine.state.players["A"].life)
        self.assertEqual("stack", spell.zone)
        self.assertEqual(
            5,
            engine.state.players["A"].stats["restricted_mana"]
            ["artifact_spell_or_ability"]["C"],
        )

    def test_fixed_life_cost_compiles_and_pays(self):
        clause = "As an additional cost to cast this spell, pay 3 life."
        text = f"{clause}\nDraw two cards."
        self.assertEqual(
            "exact",
            compile_oracle_card(
                fixture_card(text),
                capability_registry=trusted_registry(),
                capability_profile="commander_review",
            ).status,
        )
        session = self.session(601233)
        engine = session.engine
        spell = self.stage_spell(
            session, clause, mana={"B": 1, "C": 1}
        )
        self.issue_priority(session)
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()
        result = session.act("pilot:A", {"a": "cast", "card": spell.ref})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(37, engine.state.players["A"].life)
        self.assertEqual("stack", spell.zone)
        expected_hash = authoritative_state_hash(engine.state)
        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / "fixed-life-cast-cost-record"
            session.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(expected_hash, replay["final_state_hash"])

    def test_fixed_life_cost_stale_payment_rolls_back(self):
        session = self.session(601234)
        engine = session.engine
        spell = self.stage_spell(
            session,
            "As an additional cost to cast this spell, pay 3 life.",
            mana={"B": 1, "C": 1},
        )
        response = {"card": spell.ref}
        proposal = build_cast_proposal(
            engine,
            CastProposalRequest.from_submission("A", response),
        )
        engine.state.players["A"].life = 2
        before = authoritative_state_hash(engine.state)
        with self.assertRaises(CastProposalError):
            with engine.transaction():
                commit_cast(engine, proposal, response)
        self.assertEqual(before, authoritative_state_hash(engine.state))
        current = engine.state.cards[spell.object_id]
        self.assertEqual("hand", current.zone)
        self.assertEqual(1, engine.state.players["A"].mana_pool["B"])
        self.assertEqual(1, engine.state.players["A"].mana_pool["C"])

    def test_stale_alternative_selection_rolls_back(self):
        session = self.session(601235)
        engine = session.engine
        spell = self.stage_spell(
            session,
            "As an additional cost to cast this spell, discard a card or pay {5}.",
            mana={"B": 1, "C": 1},
        )
        paid = self.card(engine, "A", "Birds of Paradise")
        engine.move_card(paid.object_id, "hand", log=False)
        option = self.cast_action(engine, spell)["cost_options"][0]
        engine.move_card(paid.object_id, "graveyard", log=False)
        before = authoritative_state_hash(engine.state)
        with self.assertRaises(GameRuleError):
            engine._cast(
                "A",
                {
                    "card": spell.ref,
                    "cost_option": option["id"],
                    "discard_cards": [paid.ref],
                },
            )
        self.assertEqual(before, authoritative_state_hash(engine.state))
        self.assertEqual("hand", spell.zone)

    def test_private_alternative_discard_offer_is_seat_scoped(self):
        session = self.session(601236, players=4)
        engine = session.engine
        spell = self.stage_spell(
            session,
            "As an additional cost to cast this spell, discard a card or pay {5}.",
            mana={"B": 1, "C": 1},
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
                json.dumps(
                    projector._snapshot(f"pilot:{seat}"), sort_keys=True
                ),
            )

    def test_zone_replacement_suspends_alternative_cost_before_mutation_and_replays(self):
        session = self.session(601237, players=4)
        engine = session.engine
        spell = self.stage_spell(
            session,
            "As an additional cost to cast this spell, discard a card or pay {5}.",
            mana={"B": 1, "C": 1},
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
            reason="fixed alternative replacement ordering witness",
        )
        option = self.cast_action(engine, spell)["cost_options"][0]
        self.issue_priority(session)
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()
        result = session.act(
            "pilot:A",
            {
                "a": "cast",
                "card": spell.ref,
                "cost_option": option["id"],
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

        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / "alternative-replacement-record"
            session.save(record_dir)
            restarted = CommanderSession.load(self.db, record_dir)
            restarted_packet = StateProjector(
                self.db, restarted.engine.state
            )._decision("pilot:A")
            self.assertIsNotNone(restarted_packet)
            assert restarted_packet is not None
            self.assertEqual(
                selected, restarted_packet["ctx"]["options"][0]["id"]
            )
            result = restarted.act(
                "pilot:A", {"a": "choose", "replacement": selected}
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
