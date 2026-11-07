from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT, keep_all, make_session
from quorune.carddb import CardDatabase, CardRecord
from quorune.compiler.affected_player_sacrifice_templates import (
    FIXED_AFFECTED_PLAYER_SACRIFICE_CAPABILITY,
    FIXED_AFFECTED_PLAYER_SACRIFICE_MECHANIC,
    fixed_affected_player_sacrifice_effect_template,
)
from quorune.compiler.program_generation import register_generated_programs
from quorune.deck import DeckLoader
from quorune.errors import GameRuleError
from quorune.model import CardInstance, StackItem
from quorune.oracle_ir import compile_oracle_card
from quorune.projection import StateProjector
from quorune.record import (
    authoritative_state_hash,
    checkpoint_envelope,
    replay_record,
)
from quorune.rules.capabilities import (
    CapabilityRegistry,
    capability_dependencies_for_node,
    load_default_capability_registry,
)
from scripts.build_test_database import build_fixture_database


REGISTRY_PATH = ROOT / "quorune" / "rules" / "capability-registry.json"
FIXTURE = (
    ROOT
    / "tests"
    / "fixtures"
    / "fixed-affected-player-sacrifice-cards.json"
)


def focused_database(directory: str) -> CardDatabase:
    database = Path(directory) / "affected-player-sacrifice.sqlite3"
    build_fixture_database(
        [
            ROOT / "tests" / "fixtures" / "scryfall-exact-lists.json",
            FIXTURE,
        ],
        database,
    )
    return CardDatabase(database)


def current_capabilities() -> CapabilityRegistry:
    value = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    registry = CapabilityRegistry(value)
    registry.mark_evidence_verified("0" * 64)
    return registry


def synthetic_record(
    text: str,
    *,
    name: str,
    type_line: str,
    oracle_id_suffix: int,
) -> CardRecord:
    return CardRecord(
        oracle_id=f"00000000-0000-4000-8000-{oracle_id_suffix:012d}",
        name=name,
        mana_cost="{1}{B}",
        mana_value=2.0,
        oracle_text=text,
        type_line=type_line,
        power="2" if "Creature" in type_line else None,
        toughness="2" if "Creature" in type_line else None,
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


class FixedAffectedPlayerSacrificeCompilerTests(unittest.TestCase):
    def test_fixed_optional_payment_lowers_printed_room_bodies_and_rejects_open_prices(self):
        import re
        from unittest.mock import patch
        from quorune.apnap_object_payment import APNAP_OBJECT_PAYMENT_CAPABILITY
        from quorune.compiler import apnap_object_payment_templates

        cases = (
            ('Each player loses 2 life unless they discard a card.', 'discard', []),
            ('Each player loses 2 life unless they sacrifice a creature, artifact, or land of their choice.',
             'sacrifice', ['artifact', 'creature', 'land']),
        )
        for index, (text, operation, types) in enumerate(cases):
            source = synthetic_record(text, name='Optional payment witness', type_line='Sorcery',
                                      oracle_id_suffix=701_021_910 + index)
            ir = self.compile(source)
            self.assertEqual('exact', ir.status, ir.material_residuals)
            node, = ir.faces[0].nodes
            effect, = node.effects
            self.assertEqual(('all', operation, 1, 2),
                             (effect['players'], effect['then'], effect['count'], effect['declined_life_loss']))
            self.assertEqual(types, effect['predicate']['types_any'])
            self.assertIn(APNAP_OBJECT_PAYMENT_CAPABILITY, node.capability_dependencies)
            for invalid in (True, 0, '2'):
                altered = {**effect, 'declined_life_loss': invalid}
                self.assertNotIn(APNAP_OBJECT_PAYMENT_CAPABILITY, capability_dependencies_for_node(
                    effects=(altered,), target_schema=None, mechanic_ids=node.mechanics))
            with patch.object(apnap_object_payment_templates, '_INSTRUCTION', re.compile(r'(?!)')):
                self.assertNotEqual('exact', self.compile(source).status)
        open_price = synthetic_record('Each player loses 2 life unless they discard a card or sacrifice a creature.',
                                      name='Unrepresented payment choice', type_line='Sorcery',
                                      oracle_id_suffix=701_021_912)
        self.assertNotEqual('exact', self.compile(open_price).status)
        value = json.loads(REGISTRY_PATH.read_text(encoding='utf-8'))
        owner = next(row for row in value['capabilities'] if row['id'] == APNAP_OBJECT_PAYMENT_CAPABILITY)
        owner.update(status='blocked', blockers=['Withdrawn for focused mutation'])
        self.assertNotEqual('exact', self.compile(source, registry=CapabilityRegistry(value)).status)
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.db = focused_database(cls.temporary.name)
        cls.capabilities = current_capabilities()

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    def compile(self, record: CardRecord, *, registry=None):
        return compile_oracle_card(
            record,
            capability_registry=registry or self.capabilities,
            capability_profile="commander_review",
        )

    def test_fixed_affected_player_sacrifices_compile_across_contexts_and_modes(
        self,
    ):
        records = (
            self.db.lookup("Diabolic Edict"),
            synthetic_record(
                "When this creature enters, each opponent sacrifices a "
                "creature of their choice.",
                name="Triggered Edict Fixture",
                type_line="Creature — Test",
                oracle_id_suffix=701_021_001,
            ),
            self.db.lookup("Blighted Fen"),
            self.db.lookup("Sheoldred's Edict"),
            self.db.lookup("Angrath's Rampage"),
        )
        for record in records:
            with self.subTest(card=record.name):
                ir = self.compile(record)
                self.assertEqual("exact", ir.status, ir.material_residuals)
                sacrifice_nodes = [
                    node
                    for node in ir.faces[0].nodes
                    if FIXED_AFFECTED_PLAYER_SACRIFICE_CAPABILITY
                    in node.capability_dependencies
                ]
                self.assertTrue(sacrifice_nodes)
                for node in sacrifice_nodes:
                    self.assertTrue(node.exact)
                    self.assertIn(
                        FIXED_AFFECTED_PLAYER_SACRIFICE_MECHANIC,
                        node.mechanics,
                    )
                    self.assertIn(
                        "zone.change.destination_replacement",
                        node.capability_dependencies,
                    )
                    self.assertEqual(
                        record.oracle_text[node.span.start : node.span.end],
                        node.text,
                    )

        template = fixed_affected_player_sacrifice_effect_template(
            "Each player sacrifices two creatures."
        )
        self.assertIsNotNone(template)
        assert template is not None
        self.assertEqual(2, template.count)
        self.assertEqual("all", template.effects[0]["players"])

    def test_unsupported_sacrifice_choices_and_shape_mutations_fail_closed(
        self,
    ):
        unsupported = (
            "Each player sacrifices X lands of their choice.",
            "Each player sacrifices all permanents they control that are colored.",
            "Each opponent sacrifices a creature with the greatest power.",
            "Target player sacrifices an attacking creature of their choice.",
            "Target player sacrifices half the permanents they control.",
            "You may have target player sacrifice a creature.",
        )
        for index, text in enumerate(unsupported, start=1):
            with self.subTest(text=text):
                self.assertIsNone(
                    fixed_affected_player_sacrifice_effect_template(text)
                )
                ir = self.compile(
                    synthetic_record(
                        text,
                        name=f"Unsupported Edict {index}",
                        type_line="Sorcery",
                        oracle_id_suffix=701_021_100 + index,
                    )
                )
                self.assertNotEqual("exact", ir.status)
                self.assertTrue(ir.material_residuals)

        template = fixed_affected_player_sacrifice_effect_template(
            "Target opponent sacrifices a creature of their choice."
        )
        assert template is not None
        _template_id, effects, schema, mechanics = template.compiled()
        mutations = []
        for path, value in (
            (("count",), 0),
            (("then",), "destroy"),
            (("actor",), "$target.0"),
            (("predicate", "types_all"), ["battle"]),
            (("players",), "opponents"),
        ):
            changed = deepcopy(effects[0])
            if len(path) == 1:
                changed[path[0]] = value
            else:
                changed[path[0]][path[1]] = value
            mutations.append(changed)
        for effect in mutations:
            with self.subTest(effect=effect):
                dependencies = capability_dependencies_for_node(
                    effects=(effect,),
                    target_schema=schema,
                    mechanic_ids=mechanics,
                )
                self.assertNotIn(
                    FIXED_AFFECTED_PLAYER_SACRIFICE_CAPABILITY,
                    dependencies,
                )

    def test_affected_player_sacrifice_dependencies_fail_closed(self):
        record = self.db.lookup("Diabolic Edict")
        for dependency_id in (
            FIXED_AFFECTED_PLAYER_SACRIFICE_CAPABILITY,
            "zone.change.destination_replacement",
        ):
            with self.subTest(dependency=dependency_id):
                value = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
                row = next(
                    item
                    for item in value["capabilities"]
                    if item["id"] == dependency_id
                )
                row["status"] = "blocked"
                row["blockers"] = ["focused dependency mutation"]
                registry = CapabilityRegistry(value)
                registry.mark_evidence_verified("0" * 64)
                ir = self.compile(record, registry=registry)
                self.assertNotEqual("exact", ir.status)
                self.assertTrue(ir.material_residuals)

    def test_affected_player_sacrifice_compiler_mutant_is_killed(self):
        witnesses = (
            self.db.lookup("Diabolic Edict"),
            self.db.lookup("Sheoldred's Edict"),
            self.db.lookup("Blighted Fen"),
        )
        self.assertTrue(all(self.compile(record).status == "exact" for record in witnesses))
        with patch(
            "quorune.compiler.resolution_effect_templates."
            "fixed_affected_player_sacrifice_effect_template",
            return_value=None,
        ):
            self.assertTrue(
                all(self.compile(record).status != "exact" for record in witnesses)
            )


class FixedAffectedPlayerSacrificeRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.db = focused_database(cls.temporary.name)
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

    def session(self, seed: int):
        session = make_session(
            self.db,
            self.mishra,
            self.zimone,
            players=4,
            seed=seed,
        )
        keep_all(session)
        engine = session.engine
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine.state.priority_player = None
        engine.state.priority_passes = []
        engine.state.pending_trigger_batches.clear()
        engine.state.stack.clear()
        session.commands.clear()
        session.decisions.clear()
        return session

    def register(self, engine, *names: str) -> None:
        register_generated_programs(
            self.db,
            engine.semantics,
            tuple(self.db.lookup(name) for name in names),
            trust_level="provisional",
            capability_registry=load_default_capability_registry(),
            capability_profile=engine.state.config.review_profile,
            promote_exact_runtime_handlers=True,
            promote_exact_capability_declarations=True,
            promote_exact_effect_programs=True,
        )

    def permanent(
        self,
        engine,
        *,
        seat: str,
        name: str,
        ref: str,
        type_line: str = "Creature — Test",
        token: bool = True,
    ) -> CardInstance:
        if token:
            created = engine.create_token(
                seat,
                name=name,
                characteristics={
                    "type_line": type_line,
                    "power": "2",
                    "toughness": "2",
                },
            )[0]
            card = next(
                value for value in engine.state.cards.values() if value.ref == created
            )
            card.ref = ref
            return card
        record = self.db.lookup(name)
        card = CardInstance(
            object_id=f"fixture:{ref}",
            ref=ref,
            oracle_id=record.oracle_id,
            printed_name=record.name,
            owner=seat,
            controller=seat,
            zone="battlefield",
            zone_timestamp=engine.state.event_sequence + 1,
            known_to=list(engine.seats),
            revealed_to=list(engine.seats),
        )
        engine.state.cards[card.object_id] = card
        engine.state.players[seat].zones["battlefield"].append(card.object_id)
        return card

    def stage(
        self,
        engine,
        text: str,
        *,
        targets: tuple[str, ...] = (),
    ) -> StackItem:
        template = fixed_affected_player_sacrifice_effect_template(text)
        self.assertIsNotNone(template)
        assert template is not None
        ref = engine._next_ref("S")
        item = StackItem(
            stack_id=engine._stable_runtime_id("stack", ref),
            ref=ref,
            kind="triggered_ability",
            controller="A",
            label="Affected-player sacrifice fixture",
            targets=list(targets),
            visibility=list(engine.seats),
        )
        engine.state.stack.append(item)
        engine._begin_resolve_item(
            item,
            template.effects,
            None,
            note="Affected-player sacrifice fixture",
        )
        return item

    @staticmethod
    def choose(session, seat: str, ref: str):
        return session.act(
            f"pilot:{seat}",
            {"action_id": "choose", "cards": [ref]},
        )

    def choose_replacements(self, session) -> None:
        while (
            session.state.pending_decision is not None
            and session.state.pending_decision.kind == "replacement.order"
        ):
            principal = session.pending_principals()[0]
            projected = StateProjector(
                self.db, session.state
            )._decision(principal)
            self.assertIsNotNone(projected)
            assert projected is not None
            selected = projected["ctx"]["options"][0]["id"]
            result = session.act(
                principal,
                {
                    "action_id": "choose",
                    "replacement": selected,
                    "plan": "ORDER_REPLACEMENTS",
                    "reason": "Choose the zone replacement.",
                },
            )
            self.assertTrue(result.ok, result.summary)

    def assert_replays(self, session, label: str) -> None:
        expected_hash = authoritative_state_hash(session.state)
        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / label
            session.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(expected_hash, replay["final_state_hash"])

    def test_actual_bellowing_mauler_batches_payments_then_declined_life_loss(self):
        from test_bound_effect_programs import BoundEffectProgramRuntimeTests as H
        from test_qualified_public_history import QualifiedPublicHistoryNativeTests as History
        from quorune.card_programs import bind_card_program_runtime
        from quorune.card_programs.adapters import compile_best_available_card_program
        from quorune.turn_step_owner import TURN_STEPS

        session = self.session(70102191)
        engine = session.engine
        engine.state.config.auto_pass_empty_priority = False
        engine.state.config.semantic_policy = 'trusted_only'
        mauler = self.permanent(engine, seat='A', name='Bellowing Mauler', ref='MAULER', token=False)
        b = self.permanent(engine, seat='B', name='Birds of Paradise', ref='B_PAYMENT', token=False)
        c = self.permanent(engine, seat='C', name='Birds of Paradise', ref='C_PAYMENT', token=False)
        d = self.permanent(engine, seat='D', name='Ineligible token', ref='D_TOKEN')
        registry = load_default_capability_registry()
        record = self.db.lookup('Bellowing Mauler')
        register_generated_programs(self.db, engine.semantics, (record,), trust_level='provisional',
                                    capability_registry=registry, capability_profile='commander_review',
                                    promote_exact_trigger_programs=True, promote_exact_effect_programs=True,
                                    promote_exact_runtime_handlers=True, promote_exact_capability_declarations=True)
        program = compile_best_available_card_program(self.db, record, semantic_registry=engine.semantics,
                                                      capability_registry=registry, capability_profile='commander_review')
        binding = bind_card_program_runtime(program, capability_registry=registry, profile='commander_review')
        self.assertTrue(binding['strict_capability_ready'], binding['blockers'])
        engine.state.phase_index = TURN_STEPS.index(('postcombat_main', 'main'))
        engine._enter_step()
        engine.pump()
        self.assertEqual('priority', session.state.pending_decision.kind)
        H.checkpoint(self, session)
        History.until(self, session, lambda game: game.state.pending_decision.kind == 'choice.apnap')
        self.assertEqual(['pilot:A'], session.pending_principals())
        before = authoritative_state_hash(session.state)
        command = {'action_id': 'choose', 'cards': [mauler.ref]}
        self.assertFalse(session.act('pilot:B', command).ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        result = session.act('pilot:A', command)
        self.assertTrue(result.ok, result.summary)
        self.assertEqual('battlefield', session.state.cards[mauler.object_id].zone)
        session = H.replay(self, session, load=True)

        self.assertEqual(['pilot:B'], session.pending_principals())
        packet = session.packet('pilot:B', full=True)['decision']['ctx']
        self.assertEqual({'A': True}, packet['prior_payment_choices'])
        self.assertEqual(4, packet['declined_life_loss'])
        result = session.act('pilot:B', {'action_id': 'choose', 'cards': []})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(['pilot:C'], session.pending_principals())
        self.assertEqual([40] * 4, [session.state.players[seat].life for seat in 'ABCD'])
        result = self.choose(session, 'C', c.ref)
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(['graveyard', 'battlefield', 'graveyard', 'battlefield'],
                         [session.state.cards[card.object_id].zone for card in (mauler, b, c, d)])
        self.assertEqual([40, 36, 40, 36], [session.state.players[seat].life for seat in 'ABCD'])
        losses, = (event for event in session.state.events if event.code == 'effect.life'
                   and event.details.get('players') == ['B', 'D'])
        choices, = (event for event in session.state.events if event.code == 'choice.sacrifice')
        self.assertLess(choices.event_id, losses.event_id)
        self.assert_replays(session, 'bellowing-mauler-optional-payment')

    def test_optional_apnap_discard_projects_payment_decision_without_the_card(self):
        from quorune.compiler.apnap_object_payment_templates import apnap_object_payment_template

        session = self.session(70102192)
        engine = session.engine
        source = StackItem(stack_id='optional-discard-privacy', ref='S-OPTIONAL', kind='triggered_ability',
                           controller='A', label='Optional discard payment fixture', visibility=list(engine.seats))
        engine.state.stack.append(source)
        template = apnap_object_payment_template('Each player loses 2 life unless they discard a card.')
        engine._begin_resolve_item(source, template[1], None, note='Optional discard payment fixture')
        first = session.packet('pilot:A', full=True)['decision']['ctx']
        chosen = first['options'][0]
        result = self.choose(session, 'A', chosen)
        self.assertTrue(result.ok, result.summary)
        next_choice = session.packet('pilot:B', full=True)['decision']['ctx']
        self.assertEqual({'A': True}, next_choice['prior_payment_choices'])
        self.assertEqual({}, next_choice['prior_public_choices'])
        self.assertNotIn(chosen, json.dumps(next_choice))
        self.assertIn(chosen, [engine.state.cards[oid].ref for oid in engine.state.players['A'].zones['hand']])

    def test_each_player_sacrifices_in_apnap_order_and_commits_simultaneously(
        self,
    ):
        session = self.session(70102101)
        engine = session.engine
        engine.state.active_player = "C"
        permanents = {
            seat: self.permanent(
                engine,
                seat=seat,
                name="Birds of Paradise",
                ref=f"{seat}-sacrifice",
                token=False,
            )
            for seat in "ABCD"
        }
        self.stage(
            engine,
            "Each player sacrifices a creature of their choice.",
        )
        self.assertEqual("pilot:C", session.pending_principals()[0])
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()

        for index, seat in enumerate("CDAB"):
            projected = StateProjector(self.db, engine.state)._decision(
                f"pilot:{seat}"
            )
            self.assertIsNotNone(projected)
            assert projected is not None
            serialized = json.dumps(projected, sort_keys=True)
            self.assertIn(permanents[seat].ref, serialized)
            self.assertNotIn(permanents[seat].object_id, serialized)
            for other in set("ABCD") - {seat}:
                self.assertIsNone(
                    StateProjector(self.db, engine.state)._decision(
                        f"pilot:{other}"
                    )
                )
            result = self.choose(session, seat, permanents[seat].ref)
            self.assertTrue(result.ok, result.summary)
            if index < 3:
                self.assertTrue(
                    all(card.zone == "battlefield" for card in permanents.values())
                )

        self.assertTrue(
            all(card.zone == "graveyard" for card in permanents.values())
        )
        self.assert_replays(session, "fixed-each-player-sacrifice")

    def test_target_player_choice_revalidates_and_rolls_back_stale_submission(
        self,
    ):
        session = self.session(70102102)
        engine = session.engine
        creature = self.permanent(
            engine,
            seat="B",
            name="Targeted sacrifice creature",
            ref="targeted-sacrifice",
        )
        self.stage(
            engine,
            "Target player sacrifices a creature of their choice.",
            targets=("B",),
        )
        self.assertEqual("pilot:B", session.pending_principals()[0])
        engine.move_card(
            creature.object_id,
            "graveyard",
            reason="focused stale sacrifice choice",
        )
        before = authoritative_state_hash(engine.state)

        result = self.choose(session, "B", creature.ref)

        self.assertFalse(result.ok)
        self.assertEqual(before, authoritative_state_hash(engine.state))

        malformed = fixed_affected_player_sacrifice_effect_template(
            "Each opponent sacrifices a creature of their choice."
        )
        assert malformed is not None
        effect = deepcopy(malformed.effects[0])
        effect["predicate"]["types_all"] = ["artifact", "artifact"]
        before = authoritative_state_hash(engine.state)
        with self.assertRaises(GameRuleError):
            engine._issue_apnap_choice(
                effect=effect,
                continuation={
                    "stack_ref": engine.state.stack[-1].ref,
                    "effects": [],
                    "destination": None,
                    "note": "malformed sacrifice",
                },
            )
        self.assertEqual(before, authoritative_state_hash(engine.state))

    def test_fixed_count_sacrifices_available_objects_and_skips_empty_player(
        self,
    ):
        session = self.session(70102104)
        engine = session.engine
        permanents: dict[str, list[CardInstance]] = {
            seat: [
                self.permanent(
                    engine,
                    seat=seat,
                    name="Birds of Paradise",
                    ref=f"{seat}-fixed-count-{index}",
                    token=False,
                )
                for index in range(quantity)
            ]
            for seat, quantity in {"A": 2, "B": 1, "C": 0, "D": 2}.items()
        }
        self.stage(engine, "Each player sacrifices two creatures.")

        for seat in "ABD":
            self.assertEqual(f"pilot:{seat}", session.pending_principals()[0])
            result = session.act(
                f"pilot:{seat}",
                {
                    "action_id": "choose",
                    "cards": [card.ref for card in permanents[seat]],
                },
            )
            self.assertTrue(result.ok, result.summary)
            if seat != "D":
                self.assertTrue(
                    all(
                        card.zone == "battlefield"
                        for cards in permanents.values()
                        for card in cards
                    )
                )

        self.assertTrue(
            all(
                card.zone == "graveyard"
                for cards in permanents.values()
                for card in cards
            )
        )

    def test_affected_player_sacrifice_uses_zone_replacement_and_replays(self):
        session = self.session(70102103)
        engine = session.engine
        self.register(engine, "Dauthi Voidwalker")
        self.permanent(
            engine,
            seat="A",
            name="Dauthi Voidwalker",
            ref="voidwalker",
            token=False,
        )
        victims = {
            seat: self.permanent(
                engine,
                seat=seat,
                name="Birds of Paradise",
                ref=f"{seat}-replacement-victim",
                token=False,
            )
            for seat in "BCD"
        }
        self.stage(
            engine,
            "Each opponent sacrifices a creature of their choice.",
        )
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()
        for seat in "BCD":
            result = self.choose(session, seat, victims[seat].ref)
            self.assertTrue(result.ok, result.summary)
        self.choose_replacements(session)

        for victim in victims.values():
            self.assertEqual("exile", victim.zone)
            self.assertEqual(1, victim.counters["void"])
        self.assert_replays(session, "fixed-sacrifice-zone-replacement")


if __name__ == "__main__":
    unittest.main()
