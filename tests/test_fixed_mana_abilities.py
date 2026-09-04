from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from common import keep_all, load_assets, make_session
from quorune.abilities import parse_activated_abilities
from quorune.carddb import CardRecord
from quorune.fixed_mana_abilities import (
    FixedActivatedManaAbilitySpec,
    FixedManaAbilityError,
    FixedManaMode,
    compile_fixed_activated_mana_ability,
    fixed_mana_modes_from_effect,
)
from quorune.oracle_ir import compile_oracle_card, generated_programs
from quorune.record import checkpoint_envelope, replay_record
from quorune.rules.capabilities import (
    load_default_capability_registry,
)
from quorune.semantic_runtime.mana_abilities import (
    fixed_mana_specs_from_descriptors,
)


def record(oracle_text: str, *, name: str = "Typed Mana Relic") -> CardRecord:
    return CardRecord(
        oracle_id="00000000-0000-4000-8000-000000000605",
        name=name,
        mana_cost="{2}",
        mana_value=2.0,
        type_line="Artifact",
        oracle_text=oracle_text,
        power=None,
        toughness=None,
        loyalty=None,
        defense=None,
        colors=(),
        color_identity=(),
        keywords=(),
        produced_mana=("C",),
        layout="normal",
        released_at="2026-01-01",
        legalities={"commander": "legal"},
        faces=(),
        raw={},
    )


class FixedManaModelTests(unittest.TestCase):
    def test_closed_output_grammar_accepts_fixed_modes(self):
        cases = {
            "Add {C}{C}.": [{"C": 2}],
            "Add {G} or {U}.": [{"G": 1}, {"U": 1}],
            "Add {G}{G}, {G}{W}, or {W}{W}.": [
                {"G": 2},
                {"G": 1, "W": 1},
                {"W": 2},
            ],
            "Add one mana of any color.": [
                {color: 1} for color in "WUBRG"
            ],
            "Add one mana of any type.": [
                {color: 1} for color in "WUBRGC"
            ],
            "Add three mana of any one color.": [
                {color: 3} for color in "WUBRG"
            ],
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                modes = fixed_mana_modes_from_effect(text)
                self.assertIsNotNone(modes)
                self.assertEqual(
                    expected,
                    [
                        {
                            key: amount
                            for key, amount in mode.bundle.items()
                            if amount
                        }
                        for mode in modes or ()
                    ],
                )

    def test_closed_output_grammar_rejects_ambient_variants(self):
        for text in (
            "Add {G} for each creature you control.",
            "Add one mana of any color in your commander's color identity.",
            "Add {G}. Spend this mana only to cast a creature spell.",
            "Add {G}. This artifact deals 1 damage to you.",
            "Target player adds {G}.",
            "Choose a color. Add one mana of that color.",
        ):
            with self.subTest(text=text):
                self.assertIsNone(fixed_mana_modes_from_effect(text))

    def test_descriptor_is_immutable_canonical_and_strict(self):
        parsed = parse_activated_abilities(
            card_name="Typed Mana Relic",
            oracle_text="{1}, {T}, Pay 2 life: Add {U} or {B}.",
        )[0]
        spec = compile_fixed_activated_mana_ability(parsed)
        self.assertIsNotNone(spec)
        assert spec is not None
        payload = spec.to_dict()
        payload["mana_cost"]["GENERIC"] = 99
        payload["modes"][0]["bundle"]["U"] = 99
        self.assertEqual(1, spec.mana_cost["GENERIC"])
        self.assertEqual(1, spec.modes[0].bundle["U"])
        self.assertEqual(spec, FixedActivatedManaAbilitySpec.from_dict(spec.to_dict()))
        malformed = spec.to_dict()
        malformed["unknown"] = True
        with self.assertRaises(FixedManaAbilityError):
            FixedActivatedManaAbilitySpec.from_dict(malformed)
        malformed = spec.to_dict()
        malformed["oracle_line"] = 1
        with self.assertRaises(FixedManaAbilityError):
            FixedActivatedManaAbilitySpec.from_dict(malformed)
        malformed = spec.to_dict()
        malformed["spend_restriction"] = "unsupported"
        with self.assertRaises(FixedManaAbilityError):
            FixedActivatedManaAbilitySpec.from_dict(malformed)

    def test_fixed_mode_rejects_unknown_or_empty_outputs(self):
        with self.assertRaises(FixedManaAbilityError):
            FixedManaMode.from_bundle({"X": 1})
        with self.assertRaises(FixedManaAbilityError):
            FixedManaMode.from_bundle({})
        with self.assertRaises(FixedManaAbilityError):
            FixedManaMode.from_bundle({"G": True})
        with self.assertRaises(FixedManaAbilityError):
            FixedManaMode.from_bundle({"G": "1"})


class FixedManaCompilerTests(unittest.TestCase):
    def test_fixed_output_compiler_rejects_library_movement_costs_and_effects(self):
        for text in (
            "Mill a card, {T}: Add {C}.",
            "{T}: Add {C}, then draw a card.",
            "{T}: Add {C}, then put a card from your graveyard on the "
            "bottom of your library.",
        ):
            with self.subTest(text=text):
                ability = parse_activated_abilities(
                    card_name="Library Mana Relic",
                    oracle_text=text,
                )[0]
                self.assertIsNone(compile_fixed_activated_mana_ability(ability))

    def test_fixed_output_compiler_lowers_source_spanned_cardprogram(self):
        first_line = "{T}: Add {C}{C}."
        second_line = "{1}, {T}: Add {U} or {B}."
        card = record(f"{first_line}\n{second_line}")
        capabilities = load_default_capability_registry()
        ir = compile_oracle_card(card, capability_registry=capabilities)

        self.assertEqual("exact", ir.status)
        self.assertEqual([1, 2], [node.span.line for node in ir.faces[0].nodes])
        self.assertEqual(
            [(0, len(first_line)), (len(first_line) + 1, len(card.oracle_text))],
            [
                (node.span.start, node.span.end)
                for node in ir.faces[0].nodes
            ],
        )
        self.assertEqual(
            [("mana.activated.fixed_output",)] * 2,
            [node.capability_dependencies for node in ir.faces[0].nodes],
        )

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
        self.assertEqual(
            ["ability:ab1", "ability:ab2"],
            [program.ability_id for program in programs],
        )
        self.assertEqual(
            [1, 2],
            [program.provenance["source_span"]["line"] for program in programs],
        )
        self.assertEqual(
            [
                {"line": 1, "start": 0, "end": len(first_line)},
                {
                    "line": 2,
                    "start": len(first_line) + 1,
                    "end": len(card.oracle_text),
                },
            ],
            [program.provenance["source_span"] for program in programs],
        )
        specs = tuple(
            spec
            for program in programs
            for spec in fixed_mana_specs_from_descriptors(program.handlers)
        )
        self.assertEqual(["ab1", "ab2"], [spec.ability_id for spec in specs])
        self.assertEqual(1, specs[1].mana_cost["GENERIC"])

    def test_fixed_output_compiler_leaves_dynamic_and_restricted_variants_residual(self):
        for text in (
            "{T}: Add {G} for each creature you control.",
            "{T}: Add {G}. Spend this mana only to cast a creature spell.",
            "+1: Add {G}.",
            "{T}: Target player adds {G}.",
            "Discard a card: Add {G}.",
            "Exile a card from your graveyard: Add {B}.",
            "{Q}: Add {U}.",
            "Remove a counter from this artifact: Add {C}.",
            "Sacrifice a creature: Add {C}{C}.",
        ):
            with self.subTest(text=text):
                ir = compile_oracle_card(
                    record(text),
                    capability_registry=load_default_capability_registry(),
                )
                self.assertNotEqual("exact", ir.status)
                self.assertTrue(ir.material_residuals)
                self.assertFalse(any(node.handlers for node in ir.faces[0].nodes))

    def test_parenthesized_basic_land_reminder_is_not_an_executable_program(self):
        card = replace(
            record("({T}: Add {B} or {R}.)", name="Typed Dual"),
            mana_cost="",
            type_line="Land — Swamp Mountain",
            produced_mana=("B", "R"),
        )
        ir = compile_oracle_card(
            card,
            capability_registry=load_default_capability_registry(),
        )

        self.assertEqual("exact", ir.status)
        self.assertFalse(any(node.handlers for node in ir.faces[0].nodes))
        self.assertEqual(
            ("mana.intrinsic.basic_land_type",),
            ir.faces[0].nodes[0].capability_dependencies,
        )

    def test_fixed_output_mana_mode_mutants_are_killed(self):
        def assert_exact_modes() -> None:
            ability = replace(
                parse_activated_abilities(
                    card_name="Typed Mana Relic",
                    oracle_text="{T}: Add {G} or {U}.",
                )[0],
                ability_id="ab4",
                line_index=3,
            )
            spec = compile_fixed_activated_mana_ability(ability)
            self.assertIsNotNone(spec)
            assert spec is not None
            observed = [mode.bundle for mode in spec.modes]
            self.assertEqual(1, observed[0]["G"])
            self.assertEqual(1, observed[1]["U"])
            self.assertEqual(0, observed[0]["U"])
            self.assertEqual(0, observed[1]["G"])

        assert_exact_modes()
        with mock.patch(
            "quorune.fixed_mana_abilities._symbol_bundle",
            side_effect=lambda text: FixedManaMode.from_bundle(
                {"U" if "G" in text else "G": 1}
            ),
        ):
            with self.assertRaises(AssertionError):
                assert_exact_modes()


class FixedManaRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db, cls.mishra, cls.zimone = load_assets()

    @classmethod
    def tearDownClass(cls):
        cls.db.close()

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
        source.tapped = False
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

    def _ring(self, session):
        ring = self._card(session.engine, "Sol Ring")
        self._prepare_priority(session, ring)
        ability = next(
            ability
            for ability in session.engine._activated_abilities(ring)
            if ability.fixed_mana_outputs
        )
        return ring, ability

    def test_compiled_fixed_output_offer_and_activation_share_modes(self):
        session = self.session(60520)
        ring, ability = self._ring(session)
        engine = session.engine
        with mock.patch(
            "quorune.compiler.activated_ability_catalog.parse_activated_abilities",
            side_effect=AssertionError("runtime activation discovery recompiled Oracle"),
        ):
            rediscovered = engine._activated_abilities(ring)
        self.assertEqual(ability, next(row for row in rediscovered if row.fixed_mana_outputs))
        offered = engine._mana_modes_for_ability(ring.controller, ring, ability)
        self.assertEqual([{"C": 2}], [
            {key: amount for key, amount in mode.bundle.items() if amount}
            for mode in offered
        ])
        before = engine.state.players[ring.controller].mana_pool["C"]
        result = session.act(
            f"pilot:{ring.controller}",
            {"a": "activate", "source": ring.ref, "ability": ability.ability_id},
        )
        self.assertTrue(result.ok, result.summary)
        self.assertTrue(ring.tapped)
        self.assertEqual(before + 2, engine.state.players[ring.controller].mana_pool["C"])

    def test_compiled_fixed_output_can_pay_during_casting(self):
        session = self.session(60529)
        ring, ability = self._ring(session)
        engine = session.engine
        spell = self._card(
            engine, "Strionic Resonator", owner=ring.controller
        )
        engine.move_card(spell.object_id, "hand", log=False)

        self.assertTrue(ability.fixed_mana_outputs)
        engine._cast(
            ring.controller,
            {"card": spell.ref, "pay": "auto"},
        )

        self.assertTrue(ring.tapped)
        self.assertEqual("stack", spell.zone)
        self.assertEqual(0, engine.state.players[ring.controller].mana_pool["C"])

    def test_changed_executable_oracle_fails_closed_instead_of_reparsing_mana(self):
        session = self.session(60526)
        ring, _ = self._ring(session)
        engine = session.engine
        changed = dict(engine._effective_card_data(ring))
        changed["executable_oracle_text"] = "{T}: Add {U}."
        changed["activated_abilities"] = []

        with mock.patch.object(
            engine,
            "_effective_card_data",
            return_value=changed,
        ), mock.patch(
            "quorune.compiler.activated_ability_catalog.parse_activated_abilities",
            side_effect=AssertionError("runtime activation discovery recompiled Oracle"),
        ):
            abilities = engine._activated_abilities(ring)

        self.assertFalse(any(ability.mana_ability for ability in abilities))

    def test_invalid_compiled_mana_mode_rolls_back(self):
        session = self.session(60521)
        ring, ability = self._ring(session)
        player = session.state.players[ring.controller]
        before_pool = dict(player.mana_pool)
        before_events = len(session.state.events)

        result = session.act(
            f"pilot:{ring.controller}",
            {
                "a": "activate",
                "source": ring.ref,
                "ability": ability.ability_id,
                "mana_output": {"U": 2},
            },
        )

        self.assertFalse(result.ok)
        restored_ring = session.state.cards[ring.object_id]
        self.assertFalse(restored_ring.tapped)
        self.assertEqual(
            before_pool,
            dict(session.state.players[ring.controller].mana_pool),
        )
        self.assertEqual(before_events, len(session.state.events))

    def test_fixed_mandatory_costs_commit_through_canonical_owners(self):
        session = self.session(60527)
        ring, _ = self._ring(session)
        engine = session.engine
        player = session.state.players[ring.controller]
        player.mana_pool["C"] = 1
        life_before = player.life
        parsed = parse_activated_abilities(
            card_name="Typed Mana Relic",
            oracle_text=(
                "{1}, {T}, Pay 2 life, Sacrifice this artifact: "
                "Add {B}{B}{B}."
            ),
        )[0]
        spec = compile_fixed_activated_mana_ability(parsed)
        self.assertIsNotNone(spec)
        assert spec is not None
        ability = spec.to_activated_ability()

        with mock.patch.object(
            engine, "_activated_abilities", return_value=(ability,)
        ):
            result = session.act(
                f"pilot:{ring.controller}",
                {
                    "a": "activate",
                    "source": ring.ref,
                    "ability": ability.ability_id,
                },
            )

        self.assertTrue(result.ok, result.summary)
        self.assertEqual("graveyard", ring.zone)
        self.assertEqual(life_before - 2, player.life)
        self.assertEqual(0, player.mana_pool["C"])
        self.assertEqual(3, player.mana_pool["B"])

    def test_invalid_mode_rolls_back_fixed_mandatory_costs(self):
        session = self.session(60528)
        ring, _ = self._ring(session)
        engine = session.engine
        player = session.state.players[ring.controller]
        player.mana_pool["C"] = 1
        before_pool = dict(player.mana_pool)
        life_before = player.life
        parsed = parse_activated_abilities(
            card_name="Typed Mana Relic",
            oracle_text=(
                "{1}, {T}, Pay 2 life, Sacrifice this artifact: "
                "Add {B}{B}{B}."
            ),
        )[0]
        spec = compile_fixed_activated_mana_ability(parsed)
        self.assertIsNotNone(spec)
        assert spec is not None
        ability = spec.to_activated_ability()

        with mock.patch.object(
            engine, "_activated_abilities", return_value=(ability,)
        ):
            result = session.act(
                f"pilot:{ring.controller}",
                {
                    "a": "activate",
                    "source": ring.ref,
                    "ability": ability.ability_id,
                    "mana_output": {"U": 3},
                },
            )

        self.assertFalse(result.ok)
        restored_ring = session.state.cards[ring.object_id]
        self.assertEqual("battlefield", restored_ring.zone)
        self.assertFalse(restored_ring.tapped)
        self.assertEqual(life_before, session.state.players[ring.controller].life)
        self.assertEqual(
            before_pool,
            dict(session.state.players[ring.controller].mana_pool),
        )

    def test_compiled_fixed_output_creature_respects_haste_cost_dependency(self):
        session = self.session(60522)
        engine = session.engine
        creature = self._card(engine, "Elves of Deep Shadow")
        engine.move_card(
            creature.object_id,
            "battlefield",
            controller=creature.owner,
            tapped=False,
            log=False,
        )
        parsed = parse_activated_abilities(
            card_name="Typed Mana Creature",
            oracle_text="{T}: Add {G}.",
        )[0]
        spec = compile_fixed_activated_mana_ability(parsed)
        assert spec is not None
        ability = spec.to_activated_ability()

        self.assertEqual(
            ("unavailable", "summoning_sickness"),
            engine._ability_availability(creature.owner, creature, ability),
        )
        creature.temporary_keywords.append("haste")
        self.assertEqual(
            ("payable", None),
            engine._ability_availability(creature.owner, creature, ability),
        )

    def test_compiled_fixed_output_is_controller_scoped_in_four_player_game(self):
        session = self.session(60523, players=4)
        ring = self._card(session.engine, "Sol Ring", owner="A")
        session.engine.move_card(
            ring.object_id,
            "battlefield",
            controller="C",
            tapped=False,
            log=False,
        )
        self._prepare_priority(session, ring)
        ability = next(
            row
            for row in session.engine._activated_abilities(ring)
            if row.fixed_mana_outputs
        )
        own_sources = {
            action.get("source")
            for action in session.engine._priority_action_hints("C")["actions"]
        }
        opposing_sources = {
            action.get("source")
            for action in session.engine._priority_action_hints("A")["actions"]
        }
        self.assertEqual("A", ring.owner)
        self.assertEqual("C", ring.controller)
        self.assertIn(ring.ref, own_sources)
        self.assertNotIn(ring.ref, opposing_sources)
        result = session.act(
            "pilot:C",
            {"a": "activate", "source": ring.ref, "ability": ability.ability_id},
        )
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(2, session.state.players["C"].mana_pool["C"])
        self.assertEqual(0, session.state.players["A"].mana_pool["C"])

    def test_compiled_fixed_output_projection_exposes_no_hidden_cards(self):
        session = self.session(60524, players=4)
        ring = self._card(session.engine, "Sol Ring", owner="A")
        hidden = next(
            card
            for card in session.engine.state.cards.values()
            if card.owner == "D" and card.zone == "hand"
        )
        self._prepare_priority(session, ring)
        packet = json.dumps(session.packet("pilot:A", full=True), sort_keys=True)
        self.assertNotIn(hidden.object_id, packet)
        self.assertIn(ring.ref, packet)

    def test_compiled_fixed_output_activation_replays_exactly(self):
        session = self.session(60525)
        ring, ability = self._ring(session)
        session.initial_checkpoint = checkpoint_envelope(session.state)
        session.commands.clear()
        session.decisions.clear()

        result = session.act(
            f"pilot:{ring.controller}",
            {"a": "activate", "source": ring.ref, "ability": ability.ability_id},
        )
        self.assertTrue(result.ok, result.summary)

        with tempfile.TemporaryDirectory() as temporary:
            game_dir = Path(temporary) / "fixed-mana-replay"
            session.save(game_dir)
            replay = replay_record(game_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)

    def test_compiled_alternative_output_is_advertised_accepted_and_replayed(self):
        session = self.session(60530)
        source = self._card(session.engine, "Luxury Suite", owner="A")
        self._prepare_priority(session, source)
        ability = next(
            row
            for row in session.engine._activated_abilities(source)
            if row.fixed_mana_outputs
        )
        modes = session.engine._mana_modes_for_ability(
            source.controller, source, ability
        )
        self.assertEqual(
            [{"B": 1}, {"R": 1}],
            [
                {
                    color: amount
                    for color, amount in mode.bundle.items()
                    if amount
                }
                for mode in modes
            ],
        )
        session.initial_checkpoint = checkpoint_envelope(session.state)
        session.commands.clear()
        session.decisions.clear()

        result = session.act(
            f"pilot:{source.controller}",
            {
                "a": "activate",
                "source": source.ref,
                "ability": ability.ability_id,
                "mana_output": {"R": 1},
            },
        )
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(1, session.state.players[source.controller].mana_pool["R"])
        self.assertEqual(0, session.state.players[source.controller].mana_pool["B"])

        with tempfile.TemporaryDirectory() as temporary:
            game_dir = Path(temporary) / "fixed-mana-choice-replay"
            session.save(game_dir)
            replay = replay_record(game_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)


if __name__ == "__main__":
    unittest.main()
