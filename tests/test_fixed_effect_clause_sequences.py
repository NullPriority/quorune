from __future__ import annotations

import copy
from dataclasses import replace
from functools import partial
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import DB_PATH, ROOT, keep_all, load_assets, make_session
from inherited_composition_fixture import InheritedNativeFixture
from quorune.card_programs.adapters import compile_card_program
from quorune.carddb import CardDatabase, CardRecord
from quorune.compiler.closed_effect_programs import (
    CLOSED_EFFECT_PROGRAM_TEMPLATE_ID,
)
from quorune.compiler.fixed_effect_clause_sequences import (
    FIXED_EFFECT_CLAUSE_SEQUENCE_MECHANIC,
    fixed_effect_clause_sequence_template,
)
from quorune.deck import DeckDefinition, DeckEntry
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
from scripts.build_test_database import build_fixture_database


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
            (
                sequence_record(
                    "Return target tapped creature an opponent controls to "
                    "its owner's hand. Draw a card."
                ),
                "spell_ability",
                ("bounce", "draw"),
            ),
            (
                sequence_record(
                    "Return target Goblin card from your graveyard to your "
                    "hand. Draw a card."
                ),
                "spell_ability",
                ("return_graveyard_card_to_owner_hand", "draw"),
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

        ordered = fixed_effect_clause_sequence_template(
            "Destroy target creature, then you gain 3 life.", compile_clause=compile_clause,
        )
        self.assertIsNotNone(ordered)
        self.assertEqual(template.effects, ordered.effects)
        self.assertEqual(template.target_schema, ordered.target_schema)
        composed = (
            "Destroy target creature. Draw a card. Scry 1.",
            "Destroy target creature, then its controller gains 3 life.",
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
            "quorune.compiler.effect_template_composition."
            "fixed_effect_clause_sequence_template",
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


class OrderedTokenAbilityNativeTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / 'ordered-token-abilities.sqlite3'
        build_fixture_database([
            ROOT / 'tests/fixtures/bound-effect-program-cards.json',
            ROOT / 'tests/fixtures/typed-token-ability-program-cards.json',
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Ordered token abilities', [
            DeckEntry('Isamaru, Hound of Konda', 1, 'commander'),
            DeckEntry('Generic Bound Plains', 30),
        ], ['Isamaru, Hound of Konda'])

    def battlefield_tokens(self, session):
        return [card for card in session.state.cards.values() if card.is_token and card.zone == 'battlefield']

    def test_growth_spasm_creates_a_native_mana_token_after_the_private_search(self):
        session = self.session(60877701)
        spell = self.add(session.engine, 'Growth Spasm', zone='hand')
        self.assert_closed(session, spell)
        self.prepare(session, mana={'C': 2, 'G': 1})
        self.checkpoint(session)
        self.cast_for(session, 'A', spell)
        self.assertEqual('semantic.search', self.resolve(session).kind)
        self.assertEqual([], self.battlefield_tokens(session))
        self.assertIsNone(session.packet('pilot:B', full=True)['decision'])
        session = self.replay(session, load=True)
        decision = session.packet('pilot:A', full=True)['decision']
        land_ref = decision['ctx']['search_cards'][0]['id']
        command = {'action_id': 'choose', 'search_cards': [land_ref]}
        before = authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:B', command).ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        selected = session.act('pilot:A', command)
        self.assertTrue(selected.ok, selected.summary)
        self.settle(session)
        land = next(card for card in session.state.cards.values() if card.ref == land_ref)
        self.assertEqual(('battlefield', True), (land.zone, land.tapped))
        token, = self.battlefield_tokens(session)
        self.priority(session, 'A')
        actions = session.packet('pilot:A', full=True)['decision']['legal_actions']
        offered, = [action for action in actions if action['id'].startswith(f'activate:{token.ref}:')]
        colorless = session.state.players['A'].mana_pool.get('C', 0)
        command = {'action_id': offered['id'], 'pay': 'auto'}
        before = authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:C', command).ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        paid = session.act('pilot:A', command)
        self.assertTrue(paid.ok, paid.summary)
        self.assertEqual(colorless + 1, session.state.players['A'].mana_pool.get('C', 0))
        self.assertEqual([], self.battlefield_tokens(session))
        self.assertFalse(session.state.stack)
        self.replay(session, load=True)

    def test_spawning_breath_creates_no_token_if_its_only_target_is_illegal(self):
        session = self.session(60877702)
        spell = self.add(session.engine, 'Spawning Breath', zone='hand')
        blocker = self.add(session.engine, 'Grizzly Bears', seat='B')
        bounce = self.add(session.engine, 'Unsummon', seat='B', zone='hand')
        self.add(session.engine, 'Island', seat='B')
        self.assert_closed(session, spell)
        self.prepare(session, mana={'C': 1, 'R': 1})
        self.checkpoint(session)
        self.cast_for(session, 'A', spell, [blocker.ref])
        self.cast_for(session, 'B', bounce, [blocker.ref])
        self.resolve_top(session)
        self.assertEqual('hand', session.state.cards[blocker.object_id].zone)
        session = self.replay(session, load=True)
        self.resolve_top(session)
        self.assertEqual('graveyard', session.state.cards[spell.object_id].zone)
        self.assertEqual([], self.battlefield_tokens(session))
        self.replay(session, load=True)

    def test_make_mischief_creates_a_token_with_its_own_targeted_death_trigger(self):
        session = self.session(60877703)
        spell = self.add(session.engine, 'Make Mischief', zone='hand')
        removal = self.add(session.engine, 'Murder', seat='B', zone='hand')
        self.assert_closed(session, spell)
        self.prepare(session, mana={'C': 2, 'R': 1})
        session.state.players['B'].mana_pool.update({'C': 1, 'B': 2})
        self.checkpoint(session)
        life = {seat: player.life for seat, player in session.state.players.items()}
        self.cast_for(session, 'A', spell, ['B'])
        self.resolve_top(session)
        self.assertEqual(life['B'] - 1, session.state.players['B'].life)
        token, = self.battlefield_tokens(session)
        self.cast_for(session, 'B', removal, [token.ref])
        self.resolve_top(session)
        self.assertEqual([], self.battlefield_tokens(session))
        self.assertEqual('semantic.target', session.state.pending_decision.kind)
        self.assertIsNone(session.packet('pilot:B', full=True)['decision'])
        session = self.replay(session, load=True)
        self.target(session, 'A', ['D'])
        trigger, = session.state.stack
        self.assertEqual(token.object_id, trigger.source_object_id)
        self.resolve_top(session)
        self.assertEqual({**life, 'B': life['B'] - 1, 'D': life['D'] - 1},
                         {seat: player.life for seat, player in session.state.players.items()})
        self.replay(session, load=True)

    def test_ordered_token_component_requires_its_typed_ability_mechanic(self):
        from quorune.fixed_token_production import TYPED_TOKEN_ABILITY_CAPABILITY_ID, TYPED_TOKEN_ABILITY_MECHANIC_ID

        card = self.db.lookup('Corpsehatch', fuzzy=False)
        ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile='commander_review')
        self.assertEqual('exact', ir.status, ir.material_residuals)
        node, = [node for face in ir.faces for node in face.nodes if node.kind == 'spell_ability']
        self.assertIn(TYPED_TOKEN_ABILITY_CAPABILITY_ID, node.capability_dependencies)
        self.assertEqual(('destroy', 'create_token'), tuple(effect['op'] for effect in node.effects))
        self.assertEqual((), fixed_effect_clause_sequence_node_capabilities(
            effects=node.effects, target_schema=node.target_schema,
            mechanic_ids=set(node.mechanics) - {TYPED_TOKEN_ABILITY_MECHANIC_ID},
        ))


class PublicCountPreventionCompositionTests(InheritedNativeFixture, unittest.TestCase):
    originals = ('Respite', 'Riot Control', 'Blunt the Assault')

    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / 'public-count-prevention.sqlite3'
        build_fixture_database([
            ROOT / 'tests/fixtures/bound-effect-program-cards.json',
            ROOT / 'tests/fixtures/prevention-composition-cards.json',
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Public-count prevention', [
            DeckEntry('Generic Bound Commander', 1, 'commander'),
            DeckEntry('Generic Bound Plains', 30),
        ], ['Generic Bound Commander'])

    def bound_original(self, name):
        from quorune.card_programs.adapters import compile_best_available_card_program
        from quorune.card_programs.binding import bind_card_program_runtime
        from quorune.oracle_ir import register_generated_programs
        from quorune.semantics import SemanticRegistry
        card = self.db.lookup(name, fuzzy=False)
        semantics = SemanticRegistry()
        register_generated_programs(self.db, semantics, (card,), trust_level='provisional',
            capability_registry=self.registry, capability_profile='commander_review',
            promote_exact_runtime_handlers=True, promote_exact_trigger_programs=True,
            promote_exact_effect_programs=True, promote_exact_capability_declarations=True)
        program = compile_best_available_card_program(self.db, card, semantic_registry=semantics,
            capability_registry=self.registry, capability_profile='commander_review')
        bound = bind_card_program_runtime(program, capability_registry=self.registry, profile='commander_review')
        self.assertTrue(bound['strict_capability_ready'], bound['blockers'])
        self.assertEqual('capability_closed', bound['trust_basis'])
        return card, program

    def test_originals_keep_their_typed_life_query_and_independent_parent(self):
        for name in self.originals:
            with self.subTest(name=name):
                card, program = self.bound_original(name)
                ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile='commander_review')
                self.assertEqual('exact', ir.status, ir.material_residuals)
                node, = ir.faces[0].nodes
                self.assertEqual(card.oracle_text[node.span.start:node.span.end], node.text)
                self.assertIn('resolution.effect_program.closed_components', node.capability_dependencies)
                self.assertIn('damage.prevention.persistent_amount', node.capability_dependencies)
                self.assertIn('quantity_expression.public_query_effect_amount', node.capability_dependencies)
                life, = [effect for effect in node.effects if effect['op'] == 'life']
                self.assertEqual('public_query_effect_amount', life['delta']['kind'])
                self.assertEqual(1, len(program.abilities))

    def test_unscoped_shields_reject_unowned_fields_and_target_bindings(self):
        from quorune.rules.prevention_component_shapes import fixed_prevention_component_capabilities
        base = {'op': 'create_damage_prevention_shield', 'source': '$source',
            'subject': '*', 'mode': 'all', 'duration': 'until_end_of_turn', 'damage_kind': 'combat'}
        def owned(effect, schema=None):
            return fixed_prevention_component_capabilities(effects=(effect,), target_schema=schema,
                mechanic_ids=('cr-615-prevention-effects',))
        self.assertEqual(('damage.prevention.persistent_amount',), owned(base))
        self.assertEqual(('damage.prevention.persistent_amount',), owned({
            key: value for key, value in {**base, 'subject': '$controller'}.items() if key != 'damage_kind'}))
        for key, value in (('source', '$controller'), ('subject', '$target.0'), ('mode', 'next_instance'),
                ('duration', 'forever'), ('damage_kind', True), ('amount', 1), ('future', True),
                ('scope', {'unknown': True})):
            with self.subTest(key=key):
                self.assertEqual((), owned({**base, key: value}))
        self.assertEqual((), owned(base, {'zones': ['battlefield'], 'categories': ['creature'], 'count': 1}))

    def test_removed_parent_prevention_or_quantity_authority_blocks_original(self):
        from quorune.card_programs.binding import bind_semantic_program_runtime
        _, program = self.bound_original('Respite')
        ability, = program.abilities
        for missing in ('resolution.effect_program.closed_components', 'damage.prevention.persistent_amount', 'life.change.effect',
                        'quantity_expression.public_query_effect_amount'):
            with self.subTest(missing=missing):
                dependencies = tuple(value for value in ability.capability_dependencies if value != missing)
                changed = replace(ability, capability_dependencies=dependencies,
                    capability_closure=self.registry.closure(dependencies, profile='commander_review').to_dict())
                bound = bind_semantic_program_runtime(changed, capability_registry=self.registry, profile='commander_review')
                self.assertFalse(bound['strict'], bound)
                self.assertIn('capability:undeclared_runtime_dependency:' + missing, bound['blockers'])
        dependencies = tuple(value for value in ability.capability_dependencies
            if value != 'damage.prevention.persistent_amount')
        changed = replace(ability, coverage=(), capability_dependencies=dependencies,
            capability_closure=self.registry.closure(dependencies, profile='commander_review').to_dict())
        self.assertFalse(bind_semantic_program_runtime(changed, capability_registry=self.registry,
            profile='commander_review')['strict'])

    def reject_command(self, session, seat, command):
        before = authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:' + seat, command).ok)
        self.assertEqual(before, authoritative_state_hash(session.state))

    def check_private_and_replay(self, session, private):
        for seat in 'ABC':
            self.assertNotIn(private.ref, str(session.packet('pilot:' + seat, full=True)))
        self.replay(session, load=True)

    def global_combat_scene(self, name):
        from quorune.effect_runtime import damage_modifiers
        session = self.session(615810 if name == 'Respite' else 615811)
        spell = self.add(session.engine, name, zone='hand')
        attackers = [self.add(session.engine, 'Grizzly Bears', ref='combat-a-' + str(i)) for i in range(3)]
        blocker = self.add(session.engine, 'Grizzly Bears', seat='B', ref='combat-blocker')
        self.add(session.engine, 'Grizzly Bears', seat='C', ref='combat-other')
        phased = self.add(session.engine, 'Grizzly Bears', seat='D', ref='combat-phased')
        phased.phased_out = True
        for card in attackers:
            card.acquired_control_turn_count = -1
        for i in range(4):
            self.add(session.engine, 'Forest', ref='combat-forest-' + str(i))
        self.add(session.engine, 'Mountain', seat='B', ref='combat-mountain')
        shock = self.add(session.engine, 'Shock', seat='B', zone='hand', ref='combat-shock')
        expiry = self.add(session.engine, 'Shock', seat='B', zone='hand', ref='combat-expiry')
        private = self.add(session.engine, 'Shock', seat='D', zone='hand', ref='combat-private')
        self.assert_closed(session, spell)
        self.prepare(session)
        self.checkpoint(session)
        self.attack(session, [(attackers[0], 'B'), (attackers[1], 'C')])
        initial_life = session.state.players['A'].life
        expected_gain = 2 if name == 'Respite' else 5
        seen_life = []
        commit = damage_modifiers.commit_prevention_shield_creation
        def observe(host, plan):
            seen_life.append(host.state.players['A'].life)
            return commit(host, plan)
        self.reject_command(session, 'C', {'action_id': 'cast:' + spell.ref, 'pay': 'auto'})
        with patch.object(damage_modifiers, 'commit_prevention_shield_creation', side_effect=observe):
            self.cast_for(session, 'A', spell)
            self.assertIsNone(self.resolve(session))
        self.assertEqual([initial_life if name == 'Respite' else initial_life + expected_gain], seen_life)
        self.assertEqual(initial_life + expected_gain, session.state.players['A'].life)
        self.assertEqual(1, len(session.state.damage_prevention_shields))
        self.cast_for(session, 'B', shock, ['A'])
        self.assertIsNone(self.resolve(session))
        self.assertEqual(initial_life + expected_gain - 2, session.state.players['A'].life)
        session = self.replay(session, load=True)
        self.until(session, lambda current: current.state.pending_decision is not None
            and current.state.pending_decision.kind == 'combat.blockers'
            and current.state.pending_decision.actors == ['B'])
        command = {'action_id': 'block', 'blocks': {blocker.ref: attackers[0].ref}}
        self.reject_command(session, 'C', command)
        self.assertTrue(session.act('pilot:B', command).ok)
        life = {seat: player.life for seat, player in session.state.players.items()}
        self.until(session, lambda current: current.state.phase == 'postcombat_main')
        self.assertEqual(life, {seat: player.life for seat, player in session.state.players.items()})
        for card in (attackers[0], attackers[1], blocker):
            current = session.state.cards[card.object_id]
            self.assertEqual('battlefield', current.zone)
            self.assertEqual(0, current.marked_damage)
        self.until(session, lambda current: current.state.active_player == 'B')
        self.assertEqual([], session.state.damage_prevention_shields)
        self.cast_for(session, 'B', expiry, ['A'])
        self.assertIsNone(self.resolve(session))
        self.assertEqual(initial_life + expected_gain - 4, session.state.players['A'].life)
        self.check_private_and_replay(session, private)

    def test_global_combat_shields_keep_printed_order_counts_and_cleanup(self):
        for name in ('Respite', 'Blunt the Assault'):
            with self.subTest(name=name):
                self.global_combat_scene(name)

    def test_controller_shield_counts_current_opponents_and_leaves_other_recipients_unprotected(self):
        session = self.session(615812)
        spell = self.add(session.engine, 'Riot Control', zone='hand')
        own = self.add(session.engine, 'Grizzly Bears', ref='riot-own')
        borrowed = self.add(session.engine, 'Grizzly Bears', ref='riot-borrowed'); borrowed.owner = 'B'
        foreign = self.add(session.engine, 'Grizzly Bears', seat='B', ref='riot-foreign'); foreign.owner = 'A'
        removed = self.add(session.engine, 'Grizzly Bears', seat='C', ref='riot-removed')
        phased = self.add(session.engine, 'Grizzly Bears', seat='D', ref='riot-phased'); phased.phased_out = True
        for i in range(3):
            self.add(session.engine, 'Generic Bound Plains', ref='riot-plains-' + str(i))
        for i in range(4):
            self.add(session.engine, 'Mountain', seat='B', ref='riot-mountain-' + str(i))
        shocks = [self.add(session.engine, 'Shock', seat='B', zone='hand', ref='riot-shock-' + str(i)) for i in range(5)]
        private = self.add(session.engine, 'Shock', seat='D', zone='hand', ref='riot-private')
        self.assert_closed(session, spell)
        self.prepare(session)
        self.checkpoint(session)
        initial_life = {seat: player.life for seat, player in session.state.players.items()}
        self.cast_for(session, 'A', spell)
        self.cast_for(session, 'B', shocks[0], [removed.ref])
        self.resolve_top(session)
        self.assertEqual('graveyard', session.state.cards[removed.object_id].zone)
        self.assertIsNone(self.resolve(session))
        self.assertEqual(initial_life['A'] + 1, session.state.players['A'].life)
        self.assertEqual(1, len(session.state.damage_prevention_shields))
        for shock, target in zip(shocks[1:4], ('A', own.ref, 'C')):
            self.cast_for(session, 'B', shock, [target])
            self.assertIsNone(self.resolve(session))
        self.assertEqual(initial_life['A'] + 1, session.state.players['A'].life)
        self.assertEqual('graveyard', session.state.cards[own.object_id].zone)
        self.assertEqual(initial_life['C'] - 2, session.state.players['C'].life)
        self.assertEqual('battlefield', session.state.cards[borrowed.object_id].zone)
        self.until(session, lambda current: current.state.active_player == 'B')
        self.assertEqual([], session.state.damage_prevention_shields)
        self.cast_for(session, 'B', shocks[4], ['A'])
        self.assertIsNone(self.resolve(session))
        self.assertEqual(initial_life['A'] - 1, session.state.players['A'].life)
        self.check_private_and_replay(session, private)

    def test_missing_shield_commit_is_killed_by_actual_original_spell(self):
        with patch('quorune.effect_runtime.damage_modifiers.commit_prevention_shield_creation', return_value=()):
            with self.assertRaises(AssertionError):
                self.global_combat_scene('Respite')


if __name__ == "__main__":
    unittest.main()
