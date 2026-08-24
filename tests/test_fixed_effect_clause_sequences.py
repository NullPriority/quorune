from __future__ import annotations

import copy
from dataclasses import replace
from functools import partial
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import DB_PATH, keep_all, load_assets, make_session
from quorune.card_programs.adapters import compile_card_program
from quorune.carddb import CardDatabase, CardRecord
from quorune.compiler.closed_effect_programs import (
    CLOSED_EFFECT_PROGRAM_TEMPLATE_ID,
)
from quorune.compiler.fixed_effect_clause_sequences import (
    FIXED_EFFECT_CLAUSE_SEQUENCE_MECHANIC,
    fixed_effect_clause_sequence_template,
)
from quorune.model import StackItem
from quorune.oracle_ir import (
    ORACLE_COMPILER_VERSION,
    _reviewed_atomic_effect_template,
    compile_oracle_card,
    generated_programs,
)
from quorune.record import (
    authoritative_state_hash,
    checkpoint_envelope,
    replay_record,
)
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.fixed_effect_clause_shapes import (
    FIXED_EFFECT_CLAUSE_SEQUENCE_CAPABILITY,
    fixed_effect_clause_sequence_node_capabilities,
)


def sequence_record(
    text: str,
    *,
    type_line: str = "Instant",
    name: str = "Fixed Clause Sequence",
) -> CardRecord:
    return CardRecord(
        oracle_id=f"fixture:{abs(hash((text, type_line, name)))}",
        name=name,
        mana_cost="{2}{B}",
        mana_value=3.0,
        type_line=type_line,
        oracle_text=text,
        power="2" if "Creature" in type_line else None,
        toughness="2" if "Creature" in type_line else None,
        loyalty="5" if "Planeswalker" in type_line else None,
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


class FixedEffectClauseSequenceCompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.db = CardDatabase(DB_PATH)
        cls.capabilities = load_default_capability_registry()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.db.close()

    def compile(self, record: CardRecord):
        return compile_oracle_card(
            record,
            capability_registry=self.capabilities,
            capability_profile="commander_review",
        )

    def test_two_closed_sentences_compile_across_spell_trigger_and_activation(
        self,
    ):
        fixtures = (
            (
                sequence_record(
                    "Destroy target creature. You gain 3 life."
                ),
                "spell_ability",
                ("destroy", "life"),
            ),
            (
                sequence_record(
                    "When this creature enters, create a 0/0 green and blue "
                    "Fractal creature token. Put a +1/+1 counter on each "
                    "Fractal you control.",
                    type_line="Creature — Human Wizard",
                ),
                "triggered_ability",
                ("create_token", "place_counters_on_set"),
            ),
            (
                sequence_record(
                    "{2}{B}, {T}: Destroy target creature. Draw a card.",
                    type_line="Artifact",
                ),
                "activated_ability",
                ("destroy", "draw"),
            ),
            (
                sequence_record(
                    "Draw a card. Target opponent loses 1 life."
                ),
                "spell_ability",
                ("draw", "lose_life"),
            ),
            (
                sequence_record(
                    "Fixed Clause Sequence deals 2 damage to each creature. "
                    "Scry 1."
                ),
                "spell_ability",
                ("damage_fixed_set", "scry"),
            ),
            (
                sequence_record(
                    "Return target card from your graveyard to your hand. "
                    "Draw a card."
                ),
                "spell_ability",
                ("return_graveyard_card_to_owner_hand", "draw"),
            ),
            (
                sequence_record("Tap target creature. Scry 1."),
                "spell_ability",
                ("tap", "scry"),
            ),
            (
                sequence_record(
                    "Return target creature to its owner's hand. Draw a card."
                ),
                "spell_ability",
                ("bounce", "draw"),
            ),
        )
        for record, kind, operations in fixtures:
            with self.subTest(kind=kind):
                ir = self.compile(record)
                self.assertEqual("exact", ir.status, ir.material_residuals)
                node = ir.faces[0].nodes[0]
                self.assertEqual(kind, node.kind)
                self.assertEqual(
                    "fixed-effect-clause-sequence-v1",
                    node.template_id,
                )
                self.assertEqual(
                    operations,
                    tuple(effect["op"] for effect in node.effects),
                )
                self.assertIn(
                    FIXED_EFFECT_CLAUSE_SEQUENCE_CAPABILITY,
                    node.capability_dependencies,
                )
                self.assertEqual(
                    record.oracle_text,
                    record.oracle_text[node.span.start : node.span.end],
                )

    def test_actual_corpus_family_is_capability_closed(self):
        fixtures = (
            (self.db.lookup("Lich's Caress"), ("destroy", "life")),
            (self.db.lookup("Imperial Oath"), ("create_token", "scry")),
            (
                self.db.lookup("Garruk, Cursed Huntsman"),
                ("destroy", "draw"),
            ),
            (
                self.db.lookup("Biomathematician"),
                ("create_token", "place_counters_on_set"),
            ),
            (
                sequence_record(
                    "{1}{B}{R}: Creatures you control get +2/+0 until end "
                    "of turn. Each opponent loses 2 life.",
                    type_line="Creature — Test",
                    name="Sauron Sequence Fixture",
                ),
                (
                    "modify_all_matching_permanents_until_end_of_turn",
                    "lose_life_each_opponent",
                ),
            ),
            (
                sequence_record(
                    "Exile target card from a graveyard. Create a 1/1 white "
                    "and black Spirit creature token with flying."
                ),
                ("exile_public_graveyard_card", "create_token"),
            ),
            (
                sequence_record(
                    "Exile target card from a graveyard. You gain 3 life."
                ),
                ("exile_public_graveyard_card", "life"),
            ),
            (
                sequence_record(
                    "+2: You gain 2 life. Put two +1/+1 counters on up to "
                    "one target creature.",
                    type_line="Legendary Planeswalker — Ajani",
                ),
                ("life", "place_counters_on_targets"),
            ),
            (
                sequence_record(
                    "+1: Draw a card. Target player mills a card.",
                    type_line="Legendary Planeswalker — Jace",
                ),
                ("draw", "mill"),
            ),
            (
                sequence_record(
                    "Search your library for up to three basic land cards, "
                    "put them onto the battlefield tapped, then shuffle. "
                    "You gain 7 life."
                ),
                ("search", "life"),
            ),
            (
                sequence_record(
                    "{2}, {T}, Sacrifice Stone of Erech: Exile target "
                    "player's graveyard. Draw a card.",
                    type_line="Legendary Artifact",
                    name="Stone of Erech",
                ),
                ("move_public_zone_set", "draw"),
            ),
            (
                sequence_record(
                    "Each player sacrifices a creature of their choice. "
                    "Each opponent loses 1 life and you gain 1 life."
                ),
                ("choose_cards_apnap", "drain_each_opponent"),
            ),
            (
                sequence_record(
                    "Zombies you control get +2/+1 until end of turn. Other "
                    "creatures you control get +1/+1 until end of turn."
                ),
                (
                    "modify_all_matching_permanents_until_end_of_turn",
                    "modify_all_matching_permanents_until_end_of_turn",
                ),
            ),
            (
                sequence_record(
                    "Create two 2/2 blue Bird enchantment creature tokens "
                    "with flying. Scry 1."
                ),
                ("create_token", "scry"),
            ),
        )
        for record, operations in fixtures:
            with self.subTest(name=record.name):
                ir = self.compile(record)
                node = next(
                    node
                    for face in ir.faces
                    for node in face.nodes
                    if node.template_id
                    == "fixed-effect-clause-sequence-v1"
                )
                self.assertTrue(node.exact, ir.material_residuals)
                self.assertEqual(
                    operations,
                    tuple(effect["op"] for effect in node.effects),
                )
                programs = generated_programs(
                    self.db,
                    record,
                    trust_level="trusted",
                    capability_registry=self.capabilities,
                    capability_profile="commander_review",
                )
                program = next(
                    program
                    for program in programs
                    if program.provenance.get("template_id")
                    == "fixed-effect-clause-sequence-v1"
                )
                self.assertFalse(program.requires_arbiter)
                self.assertIn(
                    FIXED_EFFECT_CLAUSE_SEQUENCE_CAPABILITY,
                    program.capability_dependencies,
                )

    def test_sequence_parser_and_shape_fail_closed(self):
        compile_clause = partial(
            _reviewed_atomic_effect_template,
            card_name="Fixed Clause Sequence",
        )
        template = fixed_effect_clause_sequence_template(
            "Destroy target creature. You gain 3 life.",
            compile_clause=compile_clause,
        )
        self.assertIsNotNone(template)
        assert template is not None
        self.assertEqual(
            ("destroy-target-creature-v2", "gain-life-controller-v1"),
            template.component_template_ids,
        )
        dependencies = fixed_effect_clause_sequence_node_capabilities(
            effects=template.effects,
            target_schema=template.target_schema,
            mechanic_ids=template.mechanic_ids,
        )
        self.assertEqual(
            {
                "life.change.effect",
                "permanent.destroy.effect",
                FIXED_EFFECT_CLAUSE_SEQUENCE_CAPABILITY,
                "target.revalidate_resolution",
            },
            set(dependencies),
        )

        malformed = (
            (
                template.effects,
                template.target_schema,
                tuple(
                    mechanic
                    for mechanic in template.mechanic_ids
                    if mechanic != FIXED_EFFECT_CLAUSE_SEQUENCE_MECHANIC
                ),
            ),
            (
                (*template.effects, template.effects[1]),
                template.target_schema,
                template.mechanic_ids,
            ),
            (
                (template.effects[0], {"op": "unreviewed"}),
                template.target_schema,
                template.mechanic_ids,
            ),
            (template.effects, None, template.mechanic_ids),
            (
                (
                    template.effects[0],
                    {"op": "life", "player": "$target.0", "delta": 3},
                ),
                template.target_schema,
                template.mechanic_ids,
            ),
            (
                (
                    template.effects[0],
                    {"op": "life", "player": "$controller", "delta": 0},
                ),
                template.target_schema,
                template.mechanic_ids,
            ),
        )
        for effects, target_schema, mechanics in malformed:
            with self.subTest(effects=effects):
                self.assertEqual(
                    (),
                    fixed_effect_clause_sequence_node_capabilities(
                        effects=effects,
                        target_schema=target_schema,
                        mechanic_ids=mechanics,
                    ),
                )

        composed = (
            "Destroy target creature. Draw a card. Scry 1.",
            "Destroy target creature, then you gain 3 life.",
        )
        for text in composed:
            with self.subTest(leaf_text=text):
                self.assertIsNone(
                    fixed_effect_clause_sequence_template(
                        text,
                        compile_clause=compile_clause,
                    )
                )

        for text in (
            "Destroy target creature. You may gain 3 life.",
            "Destroy target creature. If it died this way, draw a card.",
            "Destroy target creature. Exile target creature.",
        ):
            with self.subTest(text=text):
                self.assertIsNone(
                    fixed_effect_clause_sequence_template(
                        text,
                        compile_clause=compile_clause,
                    )
                )
                ir = self.compile(sequence_record(text))
                self.assertNotEqual("exact", ir.status)
                self.assertTrue(ir.material_residuals)

    def test_sequence_compiler_mutation_is_killed(self):
        record = sequence_record(
            "Destroy target creature. You gain 3 life."
        )

        current_ir = self.compile(record)
        current = compile_card_program(
            self.db,
            record,
            capability_registry=self.capabilities,
            capability_profile="commander_review",
            trust_level="provisional",
        )
        with patch(
            "quorune.oracle_ir.fixed_effect_clause_sequence_template",
            return_value=None,
        ):
            mutated_ir = self.compile(record)
            mutated = compile_card_program(
                self.db,
                record,
                capability_registry=self.capabilities,
                capability_profile="commander_review",
                trust_level="provisional",
            )

        self.assertEqual("exact", current_ir.status)
        self.assertEqual("exact", mutated_ir.status, mutated_ir.material_residuals)
        self.assertEqual(
            "fixed-effect-clause-sequence-v1",
            current_ir.faces[0].nodes[0].template_id,
        )
        self.assertEqual(
            CLOSED_EFFECT_PROGRAM_TEMPLATE_ID,
            mutated_ir.faces[0].nodes[0].template_id,
        )
        self.assertEqual(ORACLE_COMPILER_VERSION, current.compiler_version)
        self.assertEqual(ORACLE_COMPILER_VERSION, mutated.compiler_version)
        self.assertNotEqual(current.semantic_hash, mutated.semantic_hash)
        self.assertNotEqual(current.fingerprint, mutated.fingerprint)


class FixedEffectClauseSequenceRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.db, cls.mishra, cls.zimone = load_assets()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.db.close()

    def session_with_card(self, card_name: str, *, players: int, seed: int):
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
    def card(engine, *, owner: str, name: str):
        return next(
            card
            for card in engine.state.cards.values()
            if card.owner == owner and card.printed_name == name
        )

    @staticmethod
    def creature(engine, *, owner: str):
        return next(
            card
            for card in engine.state.cards.values()
            if card.owner == owner
            and (record := engine.card_record(card)) is not None
            and "creature" in record.type_line.casefold()
        )

    @staticmethod
    def program(engine, source):
        return next(
            program
            for program in engine.semantics.programs_for_oracle(
                source.oracle_id
            )
            if program.provenance.get("template_id")
            == "fixed-effect-clause-sequence-v1"
        )

    @staticmethod
    def stage_spell(session, source, program, *, targets=()):
        engine = session.engine
        engine._remove_from_zone(source)
        engine._reset_zone_change(source, "stack")
        source.zone = "stack"
        source.controller = "A"
        engine.state.stack.append(
            StackItem(
                stack_id=f"stack:{source.ref}",
                ref=f"S-{source.ref}",
                kind="spell",
                controller="A",
                label=source.printed_name,
                card_object_id=source.object_id,
                source_object_id=source.object_id,
                semantic_key=program.key,
                targets=list(targets),
                default_destination="graveyard",
                visibility=list(engine.seats),
                context={"targets_chosen_at_creation": True},
            )
        )
        engine.state.active_player = "A"
        engine.state.started = True
        engine.state.phase = "precombat_main"
        engine.state.step = "main"
        engine._grant_priority("A")
        engine._issue_priority("A")
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()

    @staticmethod
    def pass_until_choice_or_empty(session):
        while session.state.stack:
            decision = session.state.pending_decision
            if decision is not None and decision.kind == "semantic.choice":
                return
            principals = session.pending_principals()
            if not principals:
                raise AssertionError("Resolution stopped without priority")
            result = session.act(principals[0], {"action_id": "pass"})
            if not result.ok:
                raise AssertionError(result.summary)

    def assert_replays(self, session, name: str):
        expected_hash = authoritative_state_hash(session.state)
        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / name
            session.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(expected_hash, replay["final_state_hash"])

    def test_life_follows_shielded_destruction_and_replays(self):
        session = self.session_with_card(
            "Lich's Caress",
            players=2,
            seed=481101,
        )
        engine = session.engine
        source = self.card(engine, owner="A", name="Lich's Caress")
        target = self.creature(engine, owner="B")
        engine.move_card(
            target.object_id,
            "battlefield",
            controller="B",
            log=False,
        )
        target.counters["shield"] = 1
        engine.state.players["A"].life = 30
        program = self.program(engine, source)
        self.assertEqual(
            ("destroy", "life"),
            tuple(effect["op"] for effect in program.effects),
        )
        self.assertEqual("trusted", program.trust_level)
        event_start = len(engine.state.events)

        self.stage_spell(session, source, program, targets=(target.ref,))
        self.pass_until_choice_or_empty(session)

        self.assertEqual("battlefield", target.zone)
        self.assertNotIn("shield", target.counters)
        self.assertEqual(33, engine.state.players["A"].life)
        events = [event.code for event in engine.state.events[event_start:]]
        self.assertLess(
            events.index("permanent.destroy.replaced"),
            events.index("effect.life"),
        )
        self.assertEqual("graveyard", source.zone)
        self.assert_replays(session, "fixed-clause-destroy-life")

    def test_private_scry_follows_token_creation_in_four_players(self):
        session = self.session_with_card(
            "Imperial Oath",
            players=4,
            seed=481102,
        )
        engine = session.engine
        source = self.card(engine, owner="A", name="Imperial Oath")
        program = self.program(engine, source)
        expected = tuple(
            engine.state.cards[object_id].ref
            for object_id in reversed(
                engine.state.players["A"].zones["library"][-3:]
            )
        )
        before_tokens = sum(card.is_token for card in engine.state.cards.values())

        self.stage_spell(session, source, program)
        self.pass_until_choice_or_empty(session)

        self.assertEqual("semantic.choice", engine.state.pending_decision.kind)
        self.assertEqual(
            before_tokens + 3,
            sum(card.is_token for card in engine.state.cards.values()),
        )
        own_packet = json.dumps(session.packet("pilot:A", full=True))
        self.assertTrue(all(reference in own_packet for reference in expected))
        for seat in "BCD":
            packet = json.dumps(session.packet(f"pilot:{seat}", full=True))
            self.assertTrue(
                all(reference not in packet for reference in expected)
            )

        before_invalid = authoritative_state_hash(engine.state)
        rejected = session.act(
            "pilot:A",
            {
                "action_id": "choose",
                "cards": {"top": [expected[0]], "bottom": []},
            },
        )
        self.assertFalse(rejected.ok)
        self.assertEqual(before_invalid, authoritative_state_hash(engine.state))
        self.assertEqual(
            before_tokens + 3,
            sum(card.is_token for card in engine.state.cards.values()),
        )

        accepted = session.act(
            "pilot:A",
            {
                "action_id": "choose",
                "cards": {"top": list(expected), "bottom": []},
            },
        )
        self.assertTrue(accepted.ok, accepted.summary)
        self.pass_until_choice_or_empty(session)
        self.assertEqual(
            "graveyard",
            engine.state.cards[source.object_id].zone,
        )
        self.assert_replays(session, "fixed-clause-token-scry")

    def test_stale_target_fizzles_entire_sequence_without_life_change(self):
        session = self.session_with_card(
            "Lich's Caress",
            players=2,
            seed=481103,
        )
        engine = session.engine
        source = self.card(engine, owner="A", name="Lich's Caress")
        target = self.creature(engine, owner="B")
        engine.move_card(
            target.object_id,
            "battlefield",
            controller="B",
            log=False,
        )
        engine.state.players["A"].life = 30
        program = self.program(engine, source)
        self.stage_spell(session, source, program, targets=(target.ref,))
        engine.move_card(target.object_id, "graveyard", log=False)
        event_start = len(engine.state.events)

        self.pass_until_choice_or_empty(session)

        self.assertEqual(30, engine.state.players["A"].life)
        self.assertEqual("graveyard", source.zone)
        self.assertNotIn(
            "effect.life",
            [event.code for event in engine.state.events[event_start:]],
        )


if __name__ == "__main__":
    unittest.main()
