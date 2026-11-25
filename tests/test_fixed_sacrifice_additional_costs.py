from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT, keep_all, make_session
from inherited_composition_fixture import InheritedNativeFixture
from scripts.build_test_database import build_fixture_database
from quorune.carddb import CardDatabase, CardRecord
from quorune.compiler.spell_additional_cost_templates import (
    FixedSacrificeAdditionalCostTemplate,
    fixed_sacrifice_additional_cost_template,
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
    FixedSacrificeAdditionalCost,
)


REGISTRY_PATH = ROOT / "quorune" / "rules" / "capability-registry.json"


def trusted_registry() -> CapabilityRegistry:
    registry = CapabilityRegistry.from_path(REGISTRY_PATH)
    registry.mark_evidence_verified("0" * 64)
    return registry


def fixture_card(text: str) -> CardRecord:
    return CardRecord(
        oracle_id="00000000-0000-4000-8000-000000601021",
        name="Fixed Sacrifice Fixture",
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


def sacrifice_cost(*types: str) -> dict:
    return dict(
        FixedSacrificeAdditionalCostTemplate(
            permanent_types=tuple(types),
        ).cost_schema
    )


class _NoRulingsDatabase:
    @staticmethod
    def rulings(_record: CardRecord) -> tuple[()]:
        return ()


class FixedSacrificeAdditionalCostCompilerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.capabilities = trusted_registry()

    def compile(self, text: str):
        return compile_oracle_card(
            fixture_card(text),
            capability_registry=self.capabilities,
            capability_profile="commander_review",
        )

    def test_fixed_sacrifice_cost_compiles_source_spanned_v2_program(self):
        examples = (
            ("a creature", ("creature",)),
            ("an artifact", ("artifact",)),
            ("a land", ("land",)),
            ("a creature or planeswalker", ("creature", "planeswalker")),
            ("an artifact or creature", ("artifact", "creature")),
            ("a permanent", ()),
        )
        for phrase, types in examples:
            text = (
                "As an additional cost to cast this spell, sacrifice "
                f"{phrase}.\nDraw two cards."
            )
            with self.subTest(phrase=phrase):
                ir = self.compile(text)
                self.assertEqual("exact", ir.status, ir.to_dict())
                self.assertEqual(1, len(ir.faces[0].nodes))
                node = ir.faces[0].nodes[0]
                self.assertEqual(text, node.text)
                self.assertEqual(text, text[node.span.start : node.span.end])
                self.assertEqual(sacrifice_cost(*types), node.cost)
                self.assertEqual("draw", node.effects[0]["op"])
                self.assertIn(
                    "casting.additional_cost.fixed_sacrifice",
                    node.capability_dependencies,
                )
                programs = generated_programs(
                    _NoRulingsDatabase(),  # type: ignore[arg-type]
                    fixture_card(text),
                    trust_level="trusted",
                    capability_registry=self.capabilities,
                    capability_profile="commander_review",
                )
                self.assertEqual(1, len(programs))
                self.assertEqual(sacrifice_cost(*types), programs[0].cost_schema)
                self.assertFalse(programs[0].requires_arbiter)

    def test_fixed_sacrifice_cost_grammar_fails_closed(self):
        unsupported = (
            "sacrifice two creatures with different powers",
            "sacrifice a modified creature",
            "sacrifice a creature with power 4 or greater",
            "sacrifice an tapped creature",
            "sacrifice a creature you control",
            "you may sacrifice a creature",
            "sacrifice a creature rather than pay this spell's mana cost",
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

        for text in (
            "As an additional cost to cast this spell, sacrifice a creature.\n"
            "Draw a card.\nYou gain 1 life.",
            "As an additional cost to cast this spell, sacrifice a creature.",
            "As an additional cost to cast this spell, sacrifice an creature.\n"
            "Draw two cards.",
            "As an additional cost to cast this spell, sacrifice a artifact.\n"
            "Draw two cards.",
        ):
            with self.subTest(text=text):
                ir = self.compile(text)
                self.assertNotEqual("exact", ir.status)
                self.assertTrue(ir.material_residuals)

    def test_fixed_sacrifice_descriptor_is_closed_and_isolated(self):
        descriptor = sacrifice_cost("artifact", "creature")[
            "additional_costs"
        ][0]
        parsed = FixedSacrificeAdditionalCost.from_descriptor(descriptor)
        original = deepcopy(descriptor)
        original["predicate"]["types_any"].append("land")
        self.assertEqual(("artifact", "creature"), parsed.predicate.types_any)
        self.assertEqual(
            sacrifice_cost("artifact", "creature")["additional_costs"][0],
            parsed.to_descriptor(),
        )
        for mutation in (
            {**descriptor, "count": True},
            {**descriptor, "count": 2},
            {**descriptor, "choice_field": "cost_cards"},
            {**descriptor, "unknown": True},
            {
                **descriptor,
                "predicate": {
                    **descriptor["predicate"],
                    "types_any": ["instant"],
                },
            },
            {
                **descriptor,
                "predicate": {
                    **descriptor["predicate"],
                    "types_any": ["artifact", "creature", "land"],
                },
            },
            {
                **descriptor,
                "predicate": {
                    **descriptor["predicate"],
                    "tapped": False,
                },
            },
        ):
            with self.subTest(mutation=mutation):
                with self.assertRaises(AdditionalCostError):
                    FixedSacrificeAdditionalCost.from_descriptor(mutation)

    def test_fixed_sacrifice_capability_dependency_fails_closed(self):
        text = (
            "As an additional cost to cast this spell, sacrifice a creature.\n"
            "Draw two cards."
        )
        value = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
        dependency = next(
            row
            for row in value["capabilities"]
            if row["id"] == "zone.change.destination_replacement"
        )
        dependency["status"] = "blocked"
        dependency["blockers"] = ["test mutation"]
        registry = CapabilityRegistry(value)
        registry.mark_evidence_verified("0" * 64)
        ir = compile_oracle_card(
            fixture_card(text),
            capability_registry=registry,
            capability_profile="commander_review",
        )
        self.assertNotEqual("exact", ir.status)
        self.assertTrue(ir.material_residuals)

    def test_fixed_sacrifice_compiler_mutant_is_killed(self):
        text = (
            "As an additional cost to cast this spell, sacrifice a creature.\n"
            "Draw two cards."
        )

        def assert_exact() -> None:
            ir = self.compile(text)
            self.assertEqual("exact", ir.status)
            self.assertEqual(
                sacrifice_cost("creature"),
                ir.faces[0].nodes[0].cost,
            )

        assert_exact()
        with patch(
            "quorune.compiler.spell_additional_cost_nodes."
            "fixed_sacrifice_additional_cost_template",
            return_value=None,
        ):
            with self.assertRaises(AssertionError):
                assert_exact()

    def test_fixed_sacrifice_template_is_canonical(self):
        first = fixed_sacrifice_additional_cost_template(
            "As an additional cost to cast this spell, sacrifice "
            "a creature or artifact."
        )
        second = fixed_sacrifice_additional_cost_template(
            "As an additional cost to cast this spell, sacrifice "
            "a creature or artifact."
        )
        self.assertEqual(first, second)
        self.assertIsNotNone(first)
        assert first is not None
        self.assertEqual(("artifact", "creature"), first.permanent_types)


class FixedSacrificeAdditionalCostRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temporary = tempfile.TemporaryDirectory()
        database = Path(cls.temporary.name) / "sacrifice-cast-cost.sqlite3"
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

    def stage_spell(self, session):
        engine = session.engine
        spell = self.card(engine, "A", "Diabolic Intent")
        engine.move_card(spell.object_id, "hand", log=False)
        program = engine.semantics.get(f"{spell.oracle_id}:spell:front")
        self.assertIsNotNone(program)
        self.assertEqual(sacrifice_cost("creature"), program.cost_schema)
        engine.state.players["A"].mana_pool.update({"B": 1, "C": 1})
        return spell

    @staticmethod
    def issue_priority(session) -> None:
        engine = session.engine
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine._grant_priority("A")
        engine._issue_priority("A")

    def test_offer_and_submission_share_effective_type_legality(self):
        session = self.session(601211)
        engine = session.engine
        spell = self.stage_spell(session)
        witness_ref = engine.create_token(
            "A",
            name="Effective Type Witness",
            characteristics={"type_line": "Token Artifact"},
        )[0]
        witness = engine._resolve_object(
            "A", witness_ref, zones={"battlefield"}
        )
        witness.annotations["until_end_of_turn"] = {
            "add_types": ["Creature"]
        }

        action = next(
            value
            for value in engine._priority_action_hints("A")["actions"]
            if value.get("card") == spell.ref
        )
        legal = action["cost_options"][0]["choice_schema"][
            "sacrifice_cards"
        ]["legal_refs"]
        self.assertIn(witness.ref, legal)

        witness.annotations.pop("until_end_of_turn")
        before = authoritative_state_hash(engine.state)
        with self.assertRaises(GameRuleError):
            engine._cast(
                "A",
                {"card": spell.ref, "sacrifice_cards": [witness.ref]},
            )
        self.assertEqual(before, authoritative_state_hash(engine.state))
        self.assertEqual("hand", spell.zone)
        self.assertEqual("battlefield", witness.zone)

    def test_fixed_sacrifice_cost_commits_and_replays(self):
        session = self.session(601212)
        engine = session.engine
        spell = self.stage_spell(session)
        creature = self.card(engine, "A", "Birds of Paradise")
        engine.move_card(creature.object_id, "battlefield", controller="A", log=False)
        self.issue_priority(session)
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()

        result = session.act(
            "pilot:A",
            {
                "a": "cast",
                "card": spell.ref,
                "sacrifice_cards": [creature.ref],
            },
        )
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("graveyard", creature.zone)
        self.assertEqual("stack", spell.zone)
        cast_event = next(
            event
            for event in reversed(engine.state.events)
            if event.code == "stack.cast"
        )
        self.assertEqual(
            [creature.ref], cast_event.details["additional_cost_objects"]
        )
        expected_hash = authoritative_state_hash(engine.state)

        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / "sacrifice-cast-cost-record"
            session.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(expected_hash, replay["final_state_hash"])

    def test_sacrifice_is_not_destruction_and_uses_owner_graveyard(self):
        session = self.session(601214, players=4)
        engine = session.engine
        spell = self.stage_spell(session)
        creature = self.card(engine, "C", "Birds of Paradise")
        opposing_creature = self.card(engine, "B", "Goblin Engineer")
        engine.move_card(
            opposing_creature.object_id,
            "battlefield",
            controller="B",
            log=False,
        )
        engine.move_card(
            creature.object_id,
            "battlefield",
            controller="A",
            log=False,
        )
        creature.temporary_keywords.append("Indestructible")
        self.assertIn(
            "indestructible",
            {
                str(keyword).casefold()
                for keyword in engine._effective_card_data(creature)[
                    "keywords"
                ]
            },
        )
        self.issue_priority(session)

        action = next(
            value
            for value in engine._priority_action_hints("A")["actions"]
            if value.get("card") == spell.ref
        )
        legal = action["cost_options"][0]["choice_schema"][
            "sacrifice_cards"
        ]["legal_refs"]
        self.assertIn(creature.ref, legal)
        self.assertNotIn(opposing_creature.ref, legal)

        result = session.act(
            "pilot:A",
            {
                "a": "cast",
                "card": spell.ref,
                "sacrifice_cards": [creature.ref],
            },
        )
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("C", creature.owner)
        self.assertEqual("graveyard", creature.zone)
        self.assertIn(
            creature.object_id,
            engine.state.players["C"].zones["graveyard"],
        )
        self.assertNotIn(
            creature.object_id,
            engine.state.players["A"].zones["graveyard"],
        )
        self.assertEqual("stack", spell.zone)

    def test_zone_replacement_suspends_cost_before_mutation_and_replays(self):
        session = self.session(601213, players=4)
        engine = session.engine
        spell = self.stage_spell(session)
        creature = self.card(engine, "A", "Birds of Paradise")
        engine.move_card(creature.object_id, "battlefield", controller="A", log=False)
        voidwalker = self.card(engine, "A", "Dauthi Voidwalker")
        engine.move_card(voidwalker.object_id, "battlefield", controller="B", log=False)
        engine.create_token(
            "B",
            name="",
            copy_of=voidwalker.ref,
            reason="fixed sacrifice replacement ordering witness",
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
                "sacrifice_cards": [creature.ref],
            },
        )
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("replacement.order", engine.state.pending_decision.kind)
        # Suspending payment rolls back the attempted cast and replaces cards.
        creature = engine.state.cards[creature.object_id]
        spell = engine.state.cards[spell.object_id]
        self.assertEqual("battlefield", creature.zone)
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
        with self.assertRaisesRegex(
            ReplacementEffectError,
            "continuation event",
        ):
            ReplacementContinuation.from_dict(tampered)

        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / "sacrifice-replacement-record"
            session.save(record_dir)
            restarted = CommanderSession.load(self.db, record_dir)
            restarted_engine = restarted.engine
            restarted_projector = StateProjector(
                self.db, restarted_engine.state
            )
            restarted_packet = restarted_projector._decision("pilot:A")
            self.assertIsNotNone(restarted_packet)
            for seat in ("B", "C", "D"):
                self.assertIsNone(
                    restarted_projector._decision(f"pilot:{seat}")
                )
            restarted_selection = restarted_packet["ctx"]["options"][0]["id"]
            self.assertEqual(selected, restarted_selection)

            dispatched_destinations: list[tuple[str, str | None]] = []
            original_dispatch = restarted_engine._dispatch_zone_change_events

            def observe_dispatch(moved_card, *args, **kwargs):
                dispatched_destinations.append(
                    (moved_card.ref, kwargs.get("destination"))
                )
                return original_dispatch(moved_card, *args, **kwargs)

            with patch.object(
                restarted_engine,
                "_dispatch_zone_change_events",
                side_effect=observe_dispatch,
            ):
                result = restarted.act(
                    "pilot:A",
                    {"a": "choose", "replacement": restarted_selection},
                )
            self.assertTrue(result.ok, result.summary)
            current_creature = restarted_engine.state.cards[
                creature.object_id
            ]
            current_spell = restarted_engine.state.cards[spell.object_id]
            self.assertEqual("exile", current_creature.zone)
            self.assertEqual(1, current_creature.counters["void"])
            self.assertEqual("stack", current_spell.zone)
            self.assertIn(
                (current_creature.ref, "exile"),
                dispatched_destinations,
            )
            self.assertNotIn(
                (current_creature.ref, "graveyard"),
                dispatched_destinations,
            )
            expected_hash = authoritative_state_hash(restarted_engine.state)
            restarted.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(expected_hash, replay["final_state_hash"])



class DamageQualifiedSacrificeNativeTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from quorune.deck import DeckDefinition, DeckEntry
        from quorune.rules.capabilities import load_default_capability_registry

        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / "damage-qualified-sacrifice.sqlite3"
        fixtures = ROOT / "tests" / "fixtures"
        build_fixture_database([
            fixtures / "bound-effect-program-cards.json",
            fixtures / "distinct-creature-damage-alternative-price-cards.json",
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Damage-qualified sacrifice", [
            DeckEntry("Generic Bound Commander", 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], ["Generic Bound Commander"])

    def _quote_greed(self, game, spell):
        self.priority(game, "A")
        actions = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        return next((row for row in actions if row["id"] == f"cast:{spell.ref}"), None)

    def _ping(self, game, seat, source, recipient):
        self.priority(game, seat)
        actions = game.packet(f"pilot:{seat}", full=True)["decision"]["ctx"]["legal"]["actions"]
        action, = (row for row in actions if row["id"].startswith(f"activate:{source.ref}:"))
        result = game.act(f"pilot:{seat}", {"action_id": action["id"], "targets": [recipient], "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(game))

    def _pay(self, game, spell, creature):
        action = self._quote_greed(game, spell)
        self.assertIsNotNone(action)
        result = game.act("pilot:A", {
            "action_id": action["id"], "sacrifice_cards": [creature.ref], "pay": "auto",
        })
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("graveyard", game.state.cards[creature.object_id].zone)
        self.assertEqual("stack", game.state.cards[spell.object_id].zone)
        cast = next(event for event in reversed(game.state.events) if event.code == "stack.cast")
        self.assertEqual([creature.ref], cast.details["additional_cost_objects"])

    def test_original_damage_price_and_both_results_require_complete_authority(self):
        from dataclasses import replace

        from quorune.card_programs.binding import bind_semantic_program_runtime
        from quorune.rules.casting_additional_costs import (
            DAMAGE_QUALIFIED_SACRIFICE_CAPABILITY, DAMAGE_QUALIFIED_SACRIFICE_VERSION,
            FixedZoneChangeAdditionalCost, damage_qualified_sacrifice_predicate,
        )
        from test_controller_creature_keyword_absence import register_card

        original = self.db.lookup("Treacherous Greed", fuzzy=False)
        bound, semantics = register_card(self.db, self.registry, original.name)
        self.assertTrue(bound["strict_capability_ready"], bound["blockers"])
        self.assertEqual("capability_closed", bound["trust_basis"])
        program, = semantics.programs_for_oracle(original.oracle_id)
        cost, = program.cost_schema["additional_costs"]
        decoded = FixedZoneChangeAdditionalCost.from_descriptor(cost)
        self.assertEqual(DAMAGE_QUALIFIED_SACRIFICE_VERSION, decoded.schema_version)
        self.assertEqual(damage_qualified_sacrifice_predicate(), decoded.predicate)
        self.assertEqual(("sacrifice_one", "sacrifice_cards", 1), (decoded.operation, decoded.choice_field, decoded.count))
        self.assertIsNone(program.target_schema)
        self.assertEqual(["draw", "drain_each_opponent"], [effect["op"] for effect in program.effects])
        self.assertIn(DAMAGE_QUALIFIED_SACRIFICE_CAPABILITY, program.capability_dependencies)
        for malformed in (
            {**cost, "count": 2},
            {**cost, "operation": "exile_one_from_battlefield", "choice_field": "exile_cards"},
            {**cost, "predicate": {**cost["predicate"], "types_all": ["artifact"]}},
            {**cost, "unblocked_attacker": False},
        ):
            with self.subTest(malformed=malformed), self.assertRaises(AdditionalCostError):
                FixedZoneChangeAdditionalCost.from_descriptor(malformed)
        remaining = tuple(cap for cap in program.capability_dependencies if cap != DAMAGE_QUALIFIED_SACRIFICE_CAPABILITY)
        changed = replace(program, capability_dependencies=remaining,
                          capability_closure=self.registry.closure(remaining, profile="commander_review").to_dict())
        self.assertIn(
            f"capability:undeclared_runtime_dependency:{DAMAGE_QUALIFIED_SACRIFICE_CAPABILITY}",
            bind_semantic_program_runtime(changed, capability_registry=self.registry, profile="commander_review")["blockers"],
        )
        for qualifier in ("was dealt damage this turn", "dealt combat damage this turn", "dealt damage last turn"):
            changed = replace(original, oracle_text=original.oracle_text.replace("dealt damage this turn", qualifier))
            self.assertNotEqual("exact", compile_oracle_card(
                changed, capability_registry=self.registry, capability_profile="commander_review",
            ).status)
        with patch("quorune.compiler.spell_additional_cost_nodes._damage_qualified_sacrifice_spell_cost", return_value=None):
            omitted, _ = register_card(self.db, self.registry, original.name)
            self.assertFalse(omitted["strict_capability_ready"], omitted)

    def test_actual_paid_price_checks_damage_source_and_keeps_a_departed_recipient(self):
        game = self.session(1200801)
        spell = self.add(game.engine, "Treacherous Greed", zone="hand")
        prevented = self.add(game.engine, "Cunning Sparkmage", ref="greed-prevented-source")
        dealer = self.add(game.engine, "Cunning Sparkmage", ref="greed-qualified-source")
        recipient = self.add(game.engine, "Grizzly Bears", ref="greed-damage-recipient")
        shielded = self.add(game.engine, "Grizzly Bears", seat="B", ref="greed-shielded")
        bounce = self.add(game.engine, "Unsummon", seat="B", zone="hand")
        shielded.counters["shield"] = 1
        game.state.config.semantic_policy = "trusted_only"
        for card in (spell, prevented, dealer, bounce):
            self.assert_closed(game, card)
        game.state.players["B"].mana_pool.update({"U": 1})
        self.prepare(game, mana={"W": 1, "B": 1, "C": 1})
        self.checkpoint(game)
        self.assertIsNone(self._quote_greed(game, spell))
        self._ping(game, "A", prevented, shielded.ref)
        self.assertEqual(0, game.state.cards[shielded.object_id].marked_damage)
        self.assertIsNone(self._quote_greed(game, spell))
        self._ping(game, "A", dealer, recipient.ref)
        action = self._quote_greed(game, spell)
        self.assertIsNotNone(action)
        before = authoritative_state_hash(game.state)
        for ineligible in (recipient, prevented, shielded):
            rejected = game.act("pilot:A", {
                "action_id": action["id"], "sacrifice_cards": [ineligible.ref], "pay": "auto",
            })
            self.assertFalse(rejected.ok)
            self.assertEqual(before, authoritative_state_hash(game.state))
        self.cast(game, "B", bounce, [recipient.ref])
        self.assertIsNone(self.resolve(game))
        self.assertEqual("hand", game.state.cards[recipient.object_id].zone)
        before_hand = set(game.state.players["A"].zones["hand"])
        before_life = {seat: game.state.players[seat].life for seat in "ABCD"}
        self._pay(game, spell, dealer)
        self.assertEqual(before_life, {seat: game.state.players[seat].life for seat in "ABCD"})
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual({seat: before_life[seat] + (3 if seat == "A" else -3) for seat in "ABCD"},
                         {seat: game.state.players[seat].life for seat in "ABCD"})
        drawn = set(game.state.players["A"].zones["hand"]) - before_hand
        self.assertEqual(3, len(drawn))
        for seat in "BCD":
            packet = json.dumps(game.packet(f"pilot:{seat}", full=True))
            self.assertTrue(all(game.state.cards[oid].ref not in packet for oid in drawn))
        self.replay(game, load=True)

    def test_actual_source_reentry_drops_the_old_price_eligibility(self):
        game = self.session(1200802)
        spell = self.add(game.engine, "Treacherous Greed", zone="hand")
        source = self.add(game.engine, "Cunning Sparkmage", ref="greed-reentering-source")
        bounce = self.add(game.engine, "Unsummon", zone="hand")
        game.state.config.semantic_policy = "trusted_only"
        for card in (spell, source, bounce):
            self.assert_closed(game, card)
        self.prepare(game, mana={"W": 1, "B": 1, "C": 3, "U": 1, "R": 1})
        self.checkpoint(game)
        first = source.logical_object_id
        self._ping(game, "A", source, "B")
        self.assertIsNotNone(self._quote_greed(game, spell))
        self.cast(game, "A", bounce, [source.ref])
        self.assertIsNone(self.resolve(game))
        self.assertIsNone(self._quote_greed(game, spell))
        self.cast(game, "A", game.state.cards[source.object_id])
        self.assertIsNone(self.resolve(game))
        reentered = game.state.cards[source.object_id]
        self.assertNotEqual(first, reentered.logical_object_id)
        self.assertIsNone(self._quote_greed(game, spell))
        self._ping(game, "A", reentered, "C")
        self.assertIsNotNone(self._quote_greed(game, spell))
        self.replay(game, load=True)

    def test_actual_new_controller_can_sacrifice_the_same_damage_source(self):
        game = self.session(1200803)
        spell = self.add(game.engine, "Treacherous Greed", zone="hand")
        source = self.add(game.engine, "Cunning Sparkmage", seat="B", ref="greed-transferred-source")
        control = self.add(game.engine, "Act of Treason", zone="hand")
        game.state.config.semantic_policy = "trusted_only"
        for card in (spell, source, control):
            self.assert_closed(game, card)
        self.prepare(game, mana={"W": 1, "B": 1, "C": 3, "R": 1})
        self.checkpoint(game)
        incarnation = source.logical_object_id
        self._ping(game, "B", source, "D")
        self.assertIsNone(self._quote_greed(game, spell))
        self.cast(game, "A", control, [source.ref])
        self.assertIsNone(self.resolve(game))
        controlled = game.state.cards[source.object_id]
        self.assertEqual(("A", "B", incarnation), (controlled.controller, controlled.owner, controlled.logical_object_id))
        self._pay(game, spell, controlled)
        self.assertIn(source.object_id, game.state.players["B"].zones["graveyard"])
        self.assertIsNone(self.resolve(game))
        self.replay(game, load=True)

    def test_verified_damage_sacrifice_withdrawal_preserves_the_distinct_damage_alternative(self):
        from quorune.rules.casting_additional_costs import DAMAGE_QUALIFIED_SACRIFICE_CAPABILITY
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests
        from test_controller_creature_keyword_absence import register_card

        capability = DAMAGE_QUALIFIED_SACRIFICE_CAPABILITY
        raw = json.loads(REGISTRY_PATH.read_bytes())
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        next(row for row in raw["capabilities"] if row["id"] == capability).update(
            status="blocked", blockers=["Scoped damage-qualified sacrifice withdrawal"],
        )
        registry = CapabilityRegistry(raw)
        index = build_index(registry_value=raw, declaration_source=declarations,
                            discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
        closure = registry.closure((capability,), profile="commander_review")
        self.assertIn(f"status:{capability}:blocked", closure.blockers)
        self.assertFalse(any("unverified" in blocker for blocker in closure.blockers))
        rejected, _ = register_card(self.db, registry, "Treacherous Greed")
        self.assertFalse(rejected["strict_capability_ready"], rejected)
        retained, _ = register_card(self.db, registry, "Inferno Trap")
        self.assertTrue(retained["strict_capability_ready"], retained["blockers"])


if __name__ == "__main__":
    unittest.main()
