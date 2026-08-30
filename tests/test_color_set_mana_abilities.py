from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from common import ROOT, keep_all, make_session
from quorune.abilities import parse_activated_abilities
from quorune.carddb import CardDatabase, CardRecord
from quorune.color_set_mana_abilities import (
    COLOR_SET_MANA_HANDLER_ID,
    ColorSetActivatedManaAbilitySpec,
    ColorSetManaAbilityError,
    ColorSetRelation,
    ColorSetSelection,
    compile_color_set_activated_mana_ability,
)
from quorune.deck import DeckLoader
from quorune.model import CardInstance
from quorune.oracle_ir import (
    compile_oracle_card,
    generated_programs,
    register_generated_programs,
)
from quorune.mana import extract_mana_modes
from quorune.record import checkpoint_envelope, replay_record
from quorune.rules.capabilities import (
    load_default_capability_registry,
)
from quorune.semantic_runtime.color_set_mana_abilities import (
    color_set_mana_specs_from_descriptors,
)
from scripts.build_test_database import build_fixture_database


MOX_TEXT = (
    "{T}: Add one mana of any color among legendary creatures and "
    "planeswalkers you control."
)
BLOOM_TEXT = (
    "{T}: For each color among permanents you control, add one mana of "
    "that color."
)
PLAZA_TEXT = (
    "{T}: Add one mana of any color among legendary permanents you control."
)
GREY_HAVENS_TEXT = (
    "{T}: Add one mana of any color among legendary creature cards in "
    "your graveyard."
)


def record(oracle_text: str, *, name: str = "Mox Amber") -> CardRecord:
    return CardRecord(
        oracle_id="7a43bd27-fdd8-41f0-9bc4-92568f3408f1",
        name=name,
        mana_cost="{0}",
        mana_value=0.0,
        type_line="Legendary Artifact",
        oracle_text=oracle_text,
        power=None,
        toughness=None,
        loyalty=None,
        defense=None,
        colors=(),
        color_identity=("W", "U", "B", "R", "G"),
        keywords=(),
        produced_mana=("W", "U", "B", "R", "G"),
        layout="normal",
        released_at="2018-04-27",
        legalities={"commander": "legal"},
        faces=(),
        raw={},
    )


def compiled_spec(text: str = MOX_TEXT) -> ColorSetActivatedManaAbilitySpec:
    parsed = parse_activated_abilities(
        card_name="Typed Color-Set Source",
        oracle_text=text,
    )[0]
    spec = compile_color_set_activated_mana_ability(parsed)
    if spec is None:
        raise AssertionError("Expected color-set mana descriptor")
    return spec


class ColorSetManaModelTests(unittest.TestCase):
    def test_color_set_descriptor_is_immutable_canonical_and_strict(self):
        spec = compiled_spec()
        payload = spec.to_dict()
        payload["mana_cost"]["GENERIC"] = 9
        payload["query"]["types_any"].append("artifact")

        self.assertEqual(0, spec.mana_cost["GENERIC"])
        self.assertEqual(("creature", "planeswalker"), spec.query.types_any)
        self.assertEqual(
            spec,
            ColorSetActivatedManaAbilitySpec.from_dict(spec.to_dict()),
        )

        malformed = spec.to_dict()
        malformed["unknown"] = True
        with self.assertRaises(ColorSetManaAbilityError):
            ColorSetActivatedManaAbilitySpec.from_dict(malformed)
        malformed = spec.to_dict()
        malformed["query"]["controller"] = "A"
        with self.assertRaises(ColorSetManaAbilityError):
            ColorSetActivatedManaAbilitySpec.from_dict(malformed)


class ColorSetManaCompilerTests(unittest.TestCase):
    def test_legacy_parser_does_not_widen_color_set_output(self):
        self.assertEqual((), extract_mana_modes(record(MOX_TEXT)))

    def test_color_set_compiler_lowers_each_closed_template(self):
        cases = {
            MOX_TEXT: (
                ColorSetRelation.CONTROLLER,
                ColorSetSelection.CHOOSE_ONE,
                ("creature", "planeswalker"),
                ("legendary",),
                ("battlefield",),
            ),
            "{T}: Add one mana of any color among legendary permanents you control.": (
                ColorSetRelation.CONTROLLER,
                ColorSetSelection.CHOOSE_ONE,
                (),
                ("legendary",),
                ("battlefield",),
            ),
            BLOOM_TEXT: (
                ColorSetRelation.CONTROLLER,
                ColorSetSelection.ONE_EACH,
                (),
                (),
                ("battlefield",),
            ),
            (
                "{T}: Add one mana of any color among legendary creature "
                "cards in your graveyard."
            ): (
                ColorSetRelation.OWNER,
                ColorSetSelection.CHOOSE_ONE,
                (),
                ("legendary",),
                ("graveyard",),
            ),
        }
        capabilities = load_default_capability_registry()
        for text, expected in cases.items():
            with self.subTest(text=text):
                card = record(text)
                ir = compile_oracle_card(
                    card,
                    capability_registry=capabilities,
                )
                self.assertEqual("exact", ir.status)
                node = ir.faces[0].nodes[0]
                self.assertEqual("activated-mana-color-set-v1", node.template_id)
                self.assertEqual(
                    ("mana.activated.color_set",),
                    node.capability_dependencies,
                )
                self.assertEqual(1, node.span.line)
                self.assertEqual((0, len(text)), (node.span.start, node.span.end))

                class Rulings:
                    @staticmethod
                    def rulings(_record):
                        return ()

                programs = generated_programs(
                    Rulings(),
                    card,
                    trust_level="trusted",
                    capability_registry=capabilities,
                )
                specs = tuple(
                    spec
                    for program in programs
                    for spec in color_set_mana_specs_from_descriptors(
                        program.handlers
                    )
                )
                self.assertEqual(1, len(specs))
                spec = specs[0]
                relation, selection, types_any, supertypes, zones = expected
                self.assertIs(relation, spec.relation)
                self.assertIs(selection, spec.selection)
                self.assertEqual(types_any, spec.query.types_any)
                self.assertEqual(supertypes, spec.query.supertypes_all)
                self.assertEqual(zones, spec.query.zones)
                self.assertEqual(
                    COLOR_SET_MANA_HANDLER_ID,
                    programs[0].handlers[0]["handler_id"],
                )

    def test_color_set_compiler_leaves_unrepresented_variants_residual(self):
        for text in (
            "{T}: Add one mana of any color among monocolored permanents you control.",
            "{T}: Add one mana of each color among monocolored permanents you control.",
            "{T}: Add one mana of any color among cards exiled with this artifact.",
            "{T}: Add one mana of any color among permanents your opponents control.",
            (
                "{T}: Add one mana of any color among legendary creatures "
                "you control if you attacked this turn."
            ),
        ):
            with self.subTest(text=text):
                ir = compile_oracle_card(
                    record(text),
                    capability_registry=load_default_capability_registry(),
                )
                self.assertNotEqual("exact", ir.status)
                self.assertTrue(ir.material_residuals)
                self.assertFalse(any(node.handlers for node in ir.faces[0].nodes))

    def test_color_set_handler_rejects_malformed_descriptors(self):
        spec = compiled_spec()
        malformed = {
            "handler_id": COLOR_SET_MANA_HANDLER_ID,
            "schema_version": 1,
            "event": "activate",
            "ability": spec.to_dict(),
            "extra": True,
        }
        with self.assertRaises(ValueError):
            color_set_mana_specs_from_descriptors((malformed,))

    def test_color_set_mana_runtime_mutants_are_killed(self):
        parsed = parse_activated_abilities(
            card_name="Mox Amber",
            oracle_text=MOX_TEXT,
        )[0]
        exact = compile_color_set_activated_mana_ability(parsed)
        self.assertIsNotNone(exact)
        assert exact is not None
        self.assertEqual(("legendary",), exact.query.supertypes_all)
        self.assertEqual(("creature", "planeswalker"), exact.query.types_any)
        self.assertIs(ColorSetSelection.CHOOSE_ONE, exact.selection)

        for change in (
            {"query": replace(exact.query, supertypes_all=())},
            {"selection": ColorSetSelection.ONE_EACH},
        ):
            with self.subTest(change=change):
                with self.assertRaises(ColorSetManaAbilityError):
                    replace(exact, **change)
        widened = replace(exact, query=replace(exact.query, types_any=()))
        with self.assertRaises(AssertionError):
            self.assertEqual(
                ("creature", "planeswalker"), widened.query.types_any
            )


class ColorSetManaRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        database = Path(cls.temporary.name) / "color-set-mana.sqlite3"
        build_fixture_database(
            ROOT / "tests" / "fixtures" / "scryfall-exact-lists.json",
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
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    def session(self, seed: int, *, players: int = 2):
        session = make_session(
            self.db,
            self.mishra,
            self.zimone,
            players=players,
            seed=seed,
        )
        keep_all(session)
        return session

    @staticmethod
    def _card(engine, name: str, *, owner: str | None = None):
        return next(
            card
            for card in engine.state.cards.values()
            if card.is_card_object
            and card.printed_name == name
            and (owner is None or card.owner == owner)
        )

    @staticmethod
    def _prepare_priority(session, source):
        engine = session.engine
        seat = source.controller
        engine.move_card(
            source.object_id,
            "battlefield",
            controller=seat,
            tapped=False,
            log=False,
        )
        source.acquired_control_turn_count = -1
        engine.state.active_player = seat
        engine.state.phase = "precombat_main"
        engine.state.step = "main"
        engine.state.stack.clear()
        engine.state.priority_passes = []
        engine.state.pending_decision = None
        engine.state.priority_player = None
        engine.permissions.invalidate_current()
        engine._grant_priority(seat)
        engine.pump()

    @staticmethod
    def _nonzero_modes(engine, seat, source, ability):
        return [
            {key: amount for key, amount in mode.bundle.items() if amount}
            for mode in engine._mana_modes_for_ability(seat, source, ability)
        ]

    def _typed_source(self, session, *, text: str = MOX_TEXT):
        source = self._card(session.engine, "Sol Ring", owner="A")
        self._prepare_priority(session, source)
        return source, compiled_spec(text).to_activated_ability()

    def _install_actual_mox(self, session):
        engine = session.engine
        mox_record = self.db.lookup("Mox Amber")
        register_generated_programs(
            self.db,
            engine.semantics,
            (mox_record,),
            trust_level="provisional",
            capability_registry=load_default_capability_registry(),
            capability_profile=engine.state.config.review_profile,
            promote_exact_runtime_handlers=True,
        )
        source = CardInstance(
            object_id="fixture-mox-amber",
            ref="A-mox-amber",
            oracle_id=mox_record.oracle_id,
            printed_name=mox_record.name,
            owner="A",
            controller="A",
            zone="hand",
            acquired_control_turn_count=-1,
        )
        engine.state.cards[source.object_id] = source
        engine.state.players["A"].zones["hand"].append(source.object_id)
        self._prepare_priority(session, source)
        ability = next(
            row
            for row in engine._activated_abilities(source)
            if row.color_set_mana_output is not None
        )
        return source, ability

    def test_mox_amber_without_qualifying_legendary_adds_no_mana(self):
        session = self.session(10601)
        source, ability = self._typed_source(session)
        engine = session.engine
        player = engine.state.players[source.controller]
        before = dict(player.mana_pool)

        self.assertEqual(
            [{}],
            self._nonzero_modes(engine, source.controller, source, ability),
        )
        with mock.patch.object(engine, "_activated_abilities", return_value=(ability,)):
            result = session.act(
                f"pilot:{source.controller}",
                {"a": "activate", "source": source.ref, "ability": ability.ability_id},
            )

        self.assertTrue(result.ok, result.summary)
        self.assertTrue(source.tapped)
        self.assertEqual(before, dict(player.mana_pool))

    def test_mox_amber_with_only_a_colorless_legendary_adds_no_mana(self):
        session = self.session(10616)
        source, ability = self._typed_source(session)
        engine = session.engine
        legend = next(
            card
            for card in engine.state.cards.values()
            if card.is_card_object
            and card.owner == source.controller
            and card.object_id != source.object_id
        )
        engine.move_card(
            legend.object_id,
            "battlefield",
            controller=source.controller,
            tapped=False,
            log=False,
        )
        original = engine._effective_card_data

        def colorless_legendary(card, *, ignore_face_down=False):
            value = original(card, ignore_face_down=ignore_face_down)
            if getattr(card, "object_id", card) == legend.object_id:
                value = dict(value)
                value["type_line"] = "Legendary Artifact Creature — Construct"
                value["colors"] = ()
            return value

        player = engine.state.players[source.controller]
        before = dict(player.mana_pool)
        with mock.patch.object(
            engine,
            "_effective_card_data",
            side_effect=colorless_legendary,
        ), mock.patch.object(
            engine,
            "_activated_abilities",
            return_value=(ability,),
        ):
            self.assertEqual(
                [{}],
                self._nonzero_modes(
                    engine,
                    source.controller,
                    source,
                    ability,
                ),
            )
            result = session.act(
                f"pilot:{source.controller}",
                {
                    "a": "activate",
                    "source": source.ref,
                    "ability": ability.ability_id,
                },
            )

        self.assertTrue(result.ok, result.summary)
        self.assertTrue(source.tapped)
        self.assertEqual(before, dict(player.mana_pool))

    def test_actual_mox_amber_program_offers_no_colored_mode_without_legendary(self):
        session = self.session(10611)
        engine = session.engine
        source, ability = self._install_actual_mox(session)
        offer = next(
            action
            for action in engine._priority_action_hints("A")["actions"]
            if action.get("source") == source.ref
            and action.get("ability") == ability.ability_id
        )

        self.assertNotIn("choice_schema", offer)
        self.assertEqual([{}], self._nonzero_modes(engine, "A", source, ability))
        self.assertNotIn(
            source.ref,
            {mana_source.ref for mana_source in engine.available_mana_sources("A")},
        )
        before = dict(engine.state.players["A"].mana_pool)
        result = session.act(
            "pilot:A",
            {"a": "activate", "source": source.ref, "ability": ability.ability_id},
        )
        self.assertTrue(result.ok, result.summary)
        self.assertTrue(source.tapped)
        self.assertEqual(before, dict(engine.state.players["A"].mana_pool))
        undo = session.act(
            "pilot:A",
            {"a": "undo_mana", "source": source.ref},
        )
        self.assertTrue(undo.ok, undo.summary)
        self.assertFalse(source.tapped)
        self.assertEqual(before, dict(engine.state.players["A"].mana_pool))

    def test_actual_mox_amber_auto_mana_uses_only_qualifying_colors(self):
        session = self.session(10612)
        engine = session.engine
        source, _ = self._install_actual_mox(session)
        legend = self._card(engine, "Mishra, Eminent One", owner="A")
        engine.move_card(
            legend.object_id,
            "battlefield",
            controller="A",
            tapped=False,
            log=False,
        )
        mana_source = next(
            row
            for row in engine.available_mana_sources("A")
            if row.ref == source.ref
        )
        self.assertEqual(
            [{"U": 1}, {"B": 1}, {"R": 1}],
            [
                {
                    color: amount
                    for color, amount in mode.bundle.items()
                    if amount
                }
                for mode in mana_source.modes
            ],
        )

    def test_actual_mox_offer_and_command_share_current_color_modes(self):
        session = self.session(10615)
        engine = session.engine
        source, ability = self._install_actual_mox(session)
        legend = self._card(engine, "Mishra, Eminent One", owner="A")
        engine.move_card(
            legend.object_id,
            "battlefield",
            controller="A",
            tapped=False,
            log=False,
        )

        offer = next(
            action
            for action in engine._priority_action_hints("A")["actions"]
            if action.get("source") == source.ref
            and action.get("ability") == ability.ability_id
        )
        self.assertEqual(
            [{"U": 1}, {"B": 1}, {"R": 1}],
            [
                option["value"]
                for option in offer["choice_schema"]["mana_output"]["options"]
            ],
        )

        before = engine.state.players["A"].mana_pool["B"]
        result = session.act(
            "pilot:A",
            {
                "a": "activate",
                "source": source.ref,
                "ability": ability.ability_id,
                "mana_output": {"B": 1},
            },
        )

        self.assertTrue(result.ok, result.summary)
        self.assertTrue(source.tapped)
        self.assertEqual(before + 1, engine.state.players["A"].mana_pool["B"])

    def test_mox_amber_advertises_only_current_qualifying_colors(self):
        session = self.session(10602)
        source, ability = self._typed_source(session)
        engine = session.engine
        legend = self._card(engine, "Zimone and Dina", owner="B")
        engine.move_card(
            legend.object_id,
            "battlefield",
            controller=source.controller,
            tapped=False,
            log=False,
        )

        self.assertEqual(
            [{"U": 1}, {"B": 1}, {"G": 1}],
            self._nonzero_modes(engine, source.controller, source, ability),
        )
        before = engine.state.players[source.controller].mana_pool["U"]
        with mock.patch.object(engine, "_activated_abilities", return_value=(ability,)):
            result = session.act(
                f"pilot:{source.controller}",
                {
                    "a": "activate",
                    "source": source.ref,
                    "ability": ability.ability_id,
                    "mana_output": {"U": 1},
                },
            )
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(
            before + 1,
            engine.state.players[source.controller].mana_pool["U"],
        )

    def test_mox_amber_uses_current_control_and_effective_colors(self):
        session = self.session(10603)
        source, ability = self._typed_source(session)
        engine = session.engine
        legend = self._card(engine, "Zimone and Dina", owner="B")
        engine.move_card(
            legend.object_id,
            "battlefield",
            controller="B",
            tapped=False,
            log=False,
        )
        self.assertEqual([{}], self._nonzero_modes(engine, "A", source, ability))

        legend.controller = "A"
        original = engine._effective_card_data

        def colored_legendary_artifact(card):
            value = original(card)
            if getattr(card, "object_id", card) == source.object_id:
                value = dict(value)
                value["type_line"] = "Legendary Artifact"
                value["colors"] = ("R",)
            return value

        legend.controller = "B"
        with mock.patch.object(
            engine,
            "_effective_card_data",
            side_effect=colored_legendary_artifact,
        ):
            self.assertEqual([{}], self._nonzero_modes(engine, "A", source, ability))
        legend.controller = "A"

        def red_legend(card):
            value = original(card)
            if getattr(card, "object_id", card) == legend.object_id:
                value = dict(value)
                value["colors"] = ("R",)
            return value

        with mock.patch.object(engine, "_effective_card_data", side_effect=red_legend):
            self.assertEqual(
                [{"R": 1}],
                self._nonzero_modes(engine, "A", source, ability),
            )

        def white_planeswalker(card):
            value = original(card)
            if getattr(card, "object_id", card) == legend.object_id:
                value = dict(value)
                value["type_line"] = "Legendary Planeswalker — Test"
                value["colors"] = ("W",)
            return value

        with mock.patch.object(
            engine,
            "_effective_card_data",
            side_effect=white_planeswalker,
        ):
            self.assertEqual(
                [{"W": 1}],
                self._nonzero_modes(engine, "A", source, ability),
            )

        def colorless_legend(card):
            value = original(card)
            if getattr(card, "object_id", card) == legend.object_id:
                value = dict(value)
                value["colors"] = ()
            return value

        with mock.patch.object(
            engine, "_effective_card_data", side_effect=colorless_legend
        ):
            self.assertEqual([{}], self._nonzero_modes(engine, "A", source, ability))

        legend.phased_out = True
        self.assertEqual([{}], self._nonzero_modes(engine, "A", source, ability))

    def test_legendary_permanent_query_includes_noncreature_permanents(self):
        session = self.session(10613)
        source, ability = self._typed_source(session, text=PLAZA_TEXT)
        engine = session.engine
        scepter_record = self.db.lookup("Loki's Scepter")
        scepter = CardInstance(
            object_id="fixture-colored-legendary-artifact",
            ref="A-colored-legendary-artifact",
            oracle_id=scepter_record.oracle_id,
            printed_name=scepter_record.name,
            owner="A",
            controller="A",
            zone="battlefield",
            acquired_control_turn_count=-1,
        )
        engine.state.cards[scepter.object_id] = scepter
        engine.state.players["A"].zones["battlefield"].append(scepter.object_id)

        self.assertEqual(
            [{"R": 1}],
            self._nonzero_modes(engine, "A", source, ability),
        )

    def test_graveyard_query_uses_owner_and_ignores_other_graveyards(self):
        session = self.session(10614)
        source, ability = self._typed_source(session, text=GREY_HAVENS_TEXT)
        engine = session.engine
        own_legend = self._card(engine, "Mishra, Eminent One", owner="A")
        engine.move_card(own_legend.object_id, "graveyard", log=False)
        other_legend = self._card(engine, "Zimone and Dina", owner="B")
        engine.move_card(
            other_legend.object_id,
            "graveyard",
            controller="A",
            log=False,
        )

        self.assertEqual(
            [{"U": 1}, {"B": 1}, {"R": 1}],
            self._nonzero_modes(engine, "A", source, ability),
        )

    def test_bloom_tender_adds_one_of_each_current_permanent_color(self):
        session = self.session(10604)
        source, ability = self._typed_source(session, text=BLOOM_TEXT)
        engine = session.engine
        legend = self._card(engine, "Zimone and Dina", owner="B")
        engine.move_card(
            legend.object_id,
            "battlefield",
            controller="A",
            tapped=False,
            log=False,
        )
        self.assertEqual(
            [{"U": 1, "B": 1, "G": 1}],
            self._nonzero_modes(engine, "A", source, ability),
        )

    def test_invalid_color_set_choice_rolls_back(self):
        session = self.session(10605)
        source, ability = self._typed_source(session)
        engine = session.engine
        legend = self._card(engine, "Zimone and Dina", owner="B")
        engine.move_card(legend.object_id, "battlefield", controller="A", log=False)
        before_pool = dict(engine.state.players["A"].mana_pool)
        before_events = len(engine.state.events)

        with mock.patch.object(engine, "_activated_abilities", return_value=(ability,)):
            result = session.act(
                "pilot:A",
                {
                    "a": "activate",
                    "source": source.ref,
                    "ability": ability.ability_id,
                    "mana_output": {"R": 1},
                },
            )

        self.assertFalse(result.ok)
        self.assertFalse(session.state.cards[source.object_id].tapped)
        self.assertEqual(before_pool, dict(session.state.players["A"].mana_pool))
        self.assertEqual(before_events, len(session.state.events))

    def test_changed_runtime_oracle_does_not_override_pinned_descriptor(self):
        session = self.session(10606)
        engine = session.engine
        bloom = self._card(engine, "Bloom Tender", owner="B")
        self._prepare_priority(session, bloom)
        expected = engine._activated_abilities(bloom)
        changed = dict(engine._effective_card_data(bloom))
        changed["executable_oracle_text"] = "{T}: Add one mana of any color."

        with mock.patch.object(engine, "_effective_card_data", return_value=changed):
            abilities = engine._activated_abilities(bloom)

        self.assertEqual(expected, abilities)
        self.assertTrue(any(ability.color_set_mana_output for ability in abilities))

    def test_color_set_mana_respects_haste_cost_dependency(self):
        session = self.session(10607)
        engine = session.engine
        creature = self._card(engine, "Elves of Deep Shadow", owner="B")
        engine.move_card(
            creature.object_id,
            "battlefield",
            controller=creature.owner,
            tapped=False,
            log=False,
        )
        ability = compiled_spec(BLOOM_TEXT).to_activated_ability()
        self.assertEqual(
            ("unavailable", "summoning_sickness"),
            engine._ability_availability(creature.owner, creature, ability),
        )
        creature.temporary_keywords.append("haste")
        self.assertEqual(
            ("payable", None),
            engine._ability_availability(creature.owner, creature, ability),
        )

    def test_color_set_mana_is_controller_scoped_in_four_player_game(self):
        session = self.session(10608, players=4)
        source, ability = self._typed_source(session)
        engine = session.engine
        own_legend = self._card(engine, "Zimone and Dina", owner="B")
        engine.move_card(own_legend.object_id, "battlefield", controller="A", log=False)
        opposing_legend = self._card(engine, "Zimone and Dina", owner="D")
        engine.move_card(
            opposing_legend.object_id,
            "battlefield",
            controller="D",
            log=False,
        )

        self.assertEqual(
            [{"U": 1}, {"B": 1}, {"G": 1}],
            self._nonzero_modes(engine, "A", source, ability),
        )

    def test_color_set_mana_projection_exposes_no_hidden_cards(self):
        session = self.session(10609, players=4)
        source, ability = self._typed_source(session)
        engine = session.engine
        hidden = next(
            card
            for card in engine.state.cards.values()
            if card.owner == "D" and card.zone == "hand"
        )
        original = engine._effective_card_data

        def public_only(card):
            if getattr(card, "object_id", card) == hidden.object_id:
                raise AssertionError("Color-set query inspected an opposing hand")
            return original(card)

        with mock.patch.object(
            engine, "_activated_abilities", return_value=(ability,)
        ), mock.patch.object(
            engine, "_effective_card_data", side_effect=public_only
        ):
            packet = json.dumps(session.packet("pilot:A", full=True), sort_keys=True)
        self.assertNotIn(hidden.object_id, packet)
        self.assertIn(source.ref, packet)

    def test_color_set_mana_activation_replays_exactly(self):
        session = self.session(10610)
        engine = session.engine
        bloom = self._card(engine, "Bloom Tender", owner="B")
        self._prepare_priority(session, bloom)
        ability = next(
            row
            for row in engine._activated_abilities(bloom)
            if row.color_set_mana_output is not None
        )
        session.initial_checkpoint = checkpoint_envelope(session.state)
        session.commands.clear()
        session.decisions.clear()

        result = session.act(
            f"pilot:{bloom.controller}",
            {"a": "activate", "source": bloom.ref, "ability": ability.ability_id},
        )
        self.assertTrue(result.ok, result.summary)

        with tempfile.TemporaryDirectory() as temporary:
            game_dir = Path(temporary) / "color-set-mana-replay"
            session.save(game_dir)
            replay = replay_record(game_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)


if __name__ == "__main__":
    unittest.main()
