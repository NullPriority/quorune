from __future__ import annotations

import copy
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
    CLOSED_EFFECT_PROGRAM_MECHANIC,
    CLOSED_EFFECT_PROGRAM_TEMPLATE_ID,
    closed_effect_program_template,
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
from quorune.rules.closed_effect_program_shapes import (
    CLOSED_EFFECT_PROGRAM_CAPABILITY,
    closed_effect_program_node_capabilities,
)


_COMPLETE_CARD_LOWER_BOUND = (
    "Agonizing Syphon",
    "Big Score",
    "Coming In Hot",
    "Courage in Crisis",
    "Covenant of Blood",
    "Cruel Truths",
    "Daring Escape",
    "Deadly Dispute",
    "Defibrillating Current",
    "Demon's Due",
    "Desperate Lunge",
    "Diresight",
    "Douse in Gloom",
    "Dreams of Laguna",
    "Essence Drain",
    "Essence Extraction",
    "Fanatical Offering",
    "Fuel for the Cause",
    "Furious Bellow",
    "Grim Affliction",
    "Hamato Guardian Stance",
    "Hire a Crew",
    "Infectious Inquiry",
    "Integrity // Intervention",
    "Kindled Heroism",
    "Last Kiss",
    "Lifecrafter's Gift",
    "Lightning Helix",
    "Oracle's Restoration",
    "Pharika's Cure",
    "Pirate's Pillage",
    "Quarrel's End",
    "Read the Bones",
    "Risky Research",
    "Sacred Fire",
    "Samut's Sprint",
    "Scale Blessing",
    "Seize the Spoils",
    "Shamble Back",
    "Shelob's Ambush",
    "Smiting Helix",
    "Sorin's Thirst",
    "Sorin's Vengeance",
    "Spread the Sickness",
    "Storm Strike",
    "Taste of Blood",
    "Unexpected Windfall",
    "Vampiric Feast",
    "Vampiric Touch",
    "Vicious Hunger",
    "Void Helix",
    "Wanderer's Strike",
    "Warleader's Helix",
)


def effect_program_record(
    text: str,
    *,
    type_line: str = "Instant",
    name: str = "Closed Effect Program",
) -> CardRecord:
    return CardRecord(
        oracle_id=f"fixture:{abs(hash((text, type_line, name)))}",
        name=name,
        mana_cost="{2}{U}",
        mana_value=3.0,
        type_line=type_line,
        oracle_text=text,
        power="2" if "Creature" in type_line else None,
        toughness="2" if "Creature" in type_line else None,
        loyalty=None,
        defense=None,
        colors=("U",),
        color_identity=("U",),
        keywords=(),
        produced_mana=(),
        layout="normal",
        released_at="2026-01-01",
        legalities={"commander": "legal"},
        faces=(),
        raw={},
    )


class ClosedEffectProgramCompilerTests(unittest.TestCase):
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

    def test_closed_program_compiles_across_contexts_and_component_shapes(self):
        fixtures = (
            (
                effect_program_record(
                    "Draw two cards and create two Treasure tokens."
                ),
                "spell_ability",
                ("draw", "create_token"),
            ),
            (
                effect_program_record(
                    "When this creature enters, draw a card and create a "
                    "Treasure token.",
                    type_line="Creature — Wizard",
                ),
                "triggered_ability",
                ("draw", "create_token"),
            ),
            (
                effect_program_record(
                    "{2}, {T}: Draw a card and create a Treasure token.",
                    type_line="Artifact",
                ),
                "activated_ability",
                ("draw", "create_token"),
            ),
            (
                effect_program_record(
                    "Target creature gets +1/+0 and gains first strike "
                    "until end of turn. Scry 1."
                ),
                "spell_ability",
                (
                    "modify_stats_until_end_of_turn",
                    "grant_keyword_until_end_of_turn",
                    "scry",
                ),
            ),
            (
                effect_program_record(
                    "Exile target creature card from a graveyard. Create a "
                    "2/2 black Zombie creature token. You gain 2 life."
                ),
                "spell_ability",
                ("exile_public_graveyard_card", "create_token", "life"),
            ),
        )
        for record, kind, operations in fixtures:
            with self.subTest(text=record.oracle_text):
                ir = self.compile(record)
                self.assertEqual("exact", ir.status, ir.material_residuals)
                node = ir.faces[0].nodes[0]
                self.assertEqual(kind, node.kind)
                self.assertEqual(CLOSED_EFFECT_PROGRAM_TEMPLATE_ID, node.template_id)
                self.assertEqual(
                    operations,
                    tuple(effect["op"] for effect in node.effects),
                )
                self.assertIn(
                    CLOSED_EFFECT_PROGRAM_CAPABILITY,
                    node.capability_dependencies,
                )

        legacy = self.compile(
            effect_program_record("Destroy target creature. You gain 3 life.")
        )
        self.assertEqual(
            "fixed-effect-clause-sequence-v1",
            legacy.faces[0].nodes[0].template_id,
        )

    def test_measured_corpus_lower_bound_is_capability_closed(self):
        self.assertEqual(53, len(_COMPLETE_CARD_LOWER_BOUND))
        for name in _COMPLETE_CARD_LOWER_BOUND:
            with self.subTest(name=name):
                record = self.db.lookup(name, fuzzy=False)
                ir = self.compile(record)
                self.assertEqual("exact", ir.status, ir.material_residuals)
                nodes = tuple(
                    node
                    for face in ir.faces
                    for node in face.nodes
                    if CLOSED_EFFECT_PROGRAM_CAPABILITY
                    in node.capability_dependencies
                )
                self.assertTrue(nodes)
                programs = generated_programs(
                    self.db,
                    record,
                    trust_level="trusted",
                    capability_registry=self.capabilities,
                    capability_profile="commander_review",
                )
                closed = tuple(
                    program
                    for program in programs
                    if CLOSED_EFFECT_PROGRAM_CAPABILITY
                    in program.capability_dependencies
                )
                self.assertTrue(closed)
                self.assertTrue(
                    all(not program.requires_arbiter for program in closed)
                )

    def test_parser_shape_and_compiler_fail_closed(self):
        compile_component = partial(
            _reviewed_atomic_effect_template,
            card_name="Closed Effect Program",
        )
        template = closed_effect_program_template(
            "Draw two cards and create two Treasure tokens.",
            compile_component=compile_component,
        )
        self.assertIsNotNone(template)
        assert template is not None
        self.assertEqual(
            ("draw-controller-v1", "create-fixed-treasure-token-v1"),
            template.component_template_ids,
        )
        self.assertEqual((1, 1), template.component_effect_counts)
        dependencies = closed_effect_program_node_capabilities(
            effects=template.effects,
            target_schema=template.target_schema,
            mechanic_ids=template.mechanic_ids,
        )
        self.assertIn(CLOSED_EFFECT_PROGRAM_CAPABILITY, dependencies)
        self.assertIn("zone.draw.library_to_hand", dependencies)
        self.assertIn("token.creation.fixed_definition", dependencies)

        malformed = (
            (
                template.effects,
                template.target_schema,
                tuple(
                    mechanic
                    for mechanic in template.mechanic_ids
                    if mechanic != CLOSED_EFFECT_PROGRAM_MECHANIC
                ),
            ),
            ((template.effects[0],), None, template.mechanic_ids),
            (
                (*template.effects, *template.effects, *template.effects,
                 *template.effects, template.effects[0]),
                None,
                template.mechanic_ids,
            ),
            (
                (template.effects[0], {"op": "unreviewed"}),
                None,
                template.mechanic_ids,
            ),
            (
                (
                    {"op": "life", "player": "$target.0", "delta": 3},
                    template.effects[1],
                ),
                None,
                template.mechanic_ids,
            ),
            (template.effects, {"zones": ["player"]}, template.mechanic_ids),
            (
                (
                    {"op": "life", "player": "$target.0", "delta": 3},
                    template.effects[1],
                ),
                {
                    "zones": ["player"],
                    "categories": ["player"],
                    "count": 2,
                },
                (
                    CLOSED_EFFECT_PROGRAM_MECHANIC,
                    "cr-115-targets",
                    "cr-119-life",
                    "cr-111-tokens",
                ),
            ),
        )
        for effects, target_schema, mechanics in malformed:
            with self.subTest(effects=effects):
                self.assertEqual(
                    (),
                    closed_effect_program_node_capabilities(
                        effects=effects,
                        target_schema=target_schema,
                        mechanic_ids=mechanics,
                    ),
                )

        unsupported = (
            "Draw a card and you may create a Treasure token.",
            "Draw a card and if you do, create a Treasure token.",
            "Draw a card and repeat this process.",
            "Draw a card for each creature and create a Treasure token.",
            "Draw a card and create that many Treasure tokens.",
            "Destroy target creature and exile another target creature.",
            "Draw a card and perform an unreviewed action.",
            "Draw a card and create a Treasure token and scry 1 and gain 1 "
            "life and surveil 1.",
            'Draw a card and create a token named "And Then".',
        )
        for text in unsupported:
            with self.subTest(text=text):
                self.assertIsNone(
                    closed_effect_program_template(
                        text,
                        compile_component=compile_component,
                    )
                )
                ir = self.compile(effect_program_record(text))
                self.assertNotEqual("exact", ir.status)
                self.assertTrue(ir.material_residuals)

        self.assertIsNone(
            closed_effect_program_template(
                "Draw a card (and reveal it and create a Treasure token.",
                compile_component=compile_component,
            )
        )

        record = effect_program_record(
            "Draw two cards and create two Treasure tokens."
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
            "quorune.oracle_ir.closed_effect_program_template",
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
        self.assertNotEqual("exact", mutated_ir.status)
        self.assertTrue(mutated_ir.material_residuals)
        self.assertEqual(ORACLE_COMPILER_VERSION, current.compiler_version)
        self.assertEqual(ORACLE_COMPILER_VERSION, mutated.compiler_version)
        self.assertNotEqual(current.semantic_hash, mutated.semantic_hash)
        self.assertNotEqual(current.fingerprint, mutated.fingerprint)


class ClosedEffectProgramRuntimeTests(unittest.TestCase):
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
            for program in engine.semantics.programs_for_oracle(source.oracle_id)
            if program.provenance.get("template_id")
            == CLOSED_EFFECT_PROGRAM_TEMPLATE_ID
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
        engine.state.pending_decision = None
        engine.permissions.invalidate_current()
        engine._grant_priority("A")
        engine.state.pending_decision = None
        engine.permissions.invalidate_current()
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

    def test_private_scry_suspends_draw_and_life_then_replays(self):
        session = self.session_with_card(
            "Demon's Due",
            players=4,
            seed=481201,
        )
        engine = session.engine
        source = self.card(engine, owner="A", name="Demon's Due")
        program = self.program(engine, source)
        self.assertEqual(
            ("scry", "draw", "lose_life"),
            tuple(effect["op"] for effect in program.effects),
        )
        engine.state.players["A"].life = 30
        self.stage_spell(session, source, program)
        hand_before = len(engine.state.players["A"].zones["hand"])
        expected = tuple(
            engine.state.cards[object_id].ref
            for object_id in reversed(
                engine.state.players["A"].zones["library"][-2:]
            )
        )

        self.pass_until_choice_or_empty(session)

        self.assertEqual("semantic.choice", engine.state.pending_decision.kind)
        self.assertEqual(hand_before, len(engine.state.players["A"].zones["hand"]))
        self.assertEqual(30, engine.state.players["A"].life)
        own_packet = json.dumps(session.packet("pilot:A", full=True))
        self.assertTrue(all(reference in own_packet for reference in expected))
        for seat in "BCD":
            packet = json.dumps(session.packet(f"pilot:{seat}", full=True))
            self.assertTrue(all(reference not in packet for reference in expected))

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
            hand_before + 2,
            len(engine.state.players["A"].zones["hand"]),
        )
        self.assertEqual(28, engine.state.players["A"].life)
        self.assertEqual(
            "graveyard",
            engine.state.cards[source.object_id].zone,
        )
        self.assert_replays(session, "closed-effect-private-scry")

    def test_target_characteristics_commit_before_private_scry(self):
        session = self.session_with_card(
            "Coming In Hot",
            players=2,
            seed=481202,
        )
        engine = session.engine
        source = self.card(engine, owner="A", name="Coming In Hot")
        target = self.creature(engine, owner="A")
        engine.move_card(target.object_id, "battlefield", controller="A", log=False)
        before_power = engine._numeric_stat(target.object_id, "power")
        program = self.program(engine, source)
        self.stage_spell(session, source, program, targets=(target.ref,))

        self.pass_until_choice_or_empty(session)

        self.assertEqual("semantic.choice", engine.state.pending_decision.kind)
        self.assertEqual(
            before_power + 1,
            engine._numeric_stat(target.object_id, "power"),
        )
        self.assertIn("first strike", engine._combat_keywords(target))
        self.assertEqual(2, len(engine.state.continuous_effects))
        expected = engine.state.cards[
            engine.state.players["A"].zones["library"][-1]
        ].ref
        accepted = session.act(
            "pilot:A",
            {
                "action_id": "choose",
                "cards": {"top": [expected], "bottom": []},
            },
        )
        self.assertTrue(accepted.ok, accepted.summary)
        self.pass_until_choice_or_empty(session)
        self.assertEqual("graveyard", source.zone)

    def test_stale_target_fizzles_every_three_component_effect(self):
        session = self.session_with_card(
            "Shamble Back",
            players=2,
            seed=481203,
        )
        engine = session.engine
        source = self.card(engine, owner="A", name="Shamble Back")
        target = self.creature(engine, owner="B")
        engine.move_card(target.object_id, "graveyard", log=False)
        program = self.program(engine, source)
        self.assertEqual(
            ("exile_public_graveyard_card", "create_token", "life"),
            tuple(effect["op"] for effect in program.effects),
        )
        stale_ref = target.ref
        engine._remove_from_zone(target)
        engine._reset_zone_change(target, "hand")
        target.zone = "hand"
        target.controller = target.owner
        engine.state.players[target.owner].zones["hand"].append(
            target.object_id
        )
        self.stage_spell(session, source, program, targets=(stale_ref,))
        engine.state.players["A"].life = 30
        token_count = sum(card.is_token for card in engine.state.cards.values())

        self.pass_until_choice_or_empty(session)

        self.assertEqual(30, engine.state.players["A"].life)
        self.assertEqual(
            token_count,
            sum(card.is_token for card in engine.state.cards.values()),
        )
        self.assertEqual("hand", target.zone)
        self.assertEqual("graveyard", source.zone)


if __name__ == "__main__":
    unittest.main()
