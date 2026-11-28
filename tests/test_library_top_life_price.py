"""Exercise the selected cost through the ordinary four-seat action path."""

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
import test_static_action_permissions as fixtures
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry
from quorune.semantic_runtime.action_permissions import LIBRARY_TOP_LIFE_ACTION_CAPABILITY
from scripts.build_test_database import build_fixture_database


class LibraryTopLifePriceNativeTests(InheritedNativeFixture, unittest.TestCase):
    source = "Vessel of the Archive"
    ordinary = "Generic Library Action Permission"
    profile = "commander_review"
    add_top = fixtures.QualifiedLibraryTopNativeTests.add_top
    actions = fixtures.QualifiedLibraryTopNativeTests.actions
    offer = fixtures.QualifiedLibraryTopNativeTests.offer
    known_top = fixtures.QualifiedLibraryTopNativeTests.known_top
    take = fixtures.QualifiedLibraryTopNativeTests.take

    @classmethod
    def use_database(cls, path, commander, land):
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition(
            "Library-top price", [DeckEntry(commander, 1, "commander"), DeckEntry(land, 30)], [commander],
        )

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "library-top-life-price.sqlite3"
        fixtures = ROOT / "tests/fixtures"
        build_fixture_database(
            [fixtures / name for name in (
                "bound-effect-program-cards.json", "public-library-action-permission-cards.json",
                "empty-permanent-cards.json", "assist-payment-cards.json",
                "conditional-free-cast-cards.json", "counter-replacement-cards.json", "library-top-life-price-cards.json",
            )], path,
        )
        cls.use_database(path, "Generic Bound Commander", "Generic Bound Plains")

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    def life_option(self, session, card, amount):
        action = self.offer(session, card, "cast")
        self.assertIsNotNone(action)
        option, = (row for row in action["cost_options"] if row["id"].startswith("library-top-life:"))
        self.assertEqual(amount, option.get("additional_life", 0))
        return action, option

    def test_private_price_pays_life_and_preserves_replay(self):
        session = self.session(40150601)
        source = self.add(session.engine, self.source)
        later = self.add_top(session, "Lightning Bolt", "hidden-following-top")
        top = self.add_top(session, "Grizzly Bears", "current-life-priced-spell")
        self.assert_closed(session, source)
        self.prepare(session)
        self.checkpoint(session)
        self.assertEqual([top.ref], self.known_top(session))
        for seat in ("B", "C", "D"):
            self.assertEqual([], self.known_top(session, seat))
            self.assertFalse(any(row.get("card") == top.ref for row in self.actions(session, seat)))
        action, option = self.life_option(session, top, 2)
        self.assertEqual([option], action["cost_options"])
        self.take(session, action, cost_option=option["id"], pay="auto")
        self.assertEqual(38, session.state.players["A"].life)
        self.assertEqual(40, session.state.players["B"].life)
        self.assertEqual([later.ref], self.known_top(session))
        self.assertEqual([], self.known_top(session, "B"))
        self.assertIsNone(self.resolve(session))
        self.assertEqual("battlefield", session.state.cards[top.object_id].zone)
        self.replay(session, load=True)

    def test_life_price_does_not_replace_a_separate_ordinary_permission(self):
        session = self.session(40150602)
        source = self.add(session.engine, self.source)
        ordinary = self.add(session.engine, self.ordinary)
        top = self.add_top(session, "Grizzly Bears")
        self.assert_closed(session, source)
        self.assert_closed(session, ordinary)
        self.add(session.engine, "Forest", ref="first-payment-forest")
        self.add(session.engine, "Forest", ref="second-payment-forest")
        self.prepare(session)
        self.checkpoint(session)
        action, life = self.life_option(session, top, 2)
        mana, = (row for row in action["cost_options"] if row["id"] != life["id"])
        self.assertEqual(0, mana.get("additional_life", 0))
        self.take(session, action, cost_option=mana["id"], pay="auto")
        self.assertEqual(40, session.state.players["A"].life)
        self.assertIsNone(self.resolve(session))
        self.replay(session, load=True)

    def test_paid_source_fixes_printed_x_to_zero_and_rejects_unpayable_life(self):
        session = self.session(40150603)
        source = self.add(session.engine, self.source)
        spell = self.add_top(session, "Blaze")
        self.assert_closed(session, source)
        self.prepare(session)
        self.checkpoint(session)
        action, option = self.life_option(session, spell, 1)
        command = {"action_id": action["id"], "cost_option": option["id"], "targets": ["B"], "pay": "auto"}
        before = authoritative_state_hash(session.state)
        self.assertFalse(session.act("pilot:A", {**command, "x": 1}).ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        self.take(session, action, **{key: value for key, value in command.items() if key != "action_id"}, x=0)
        self.assertEqual(39, session.state.players["A"].life)
        self.assertEqual(0, session.state.stack[-1].x_value)
        self.assertIsNone(self.resolve(session))
        self.assertEqual(40, session.state.players["B"].life)
        self.replay(session, load=True)

        unaffordable = self.session(40150604)
        self.add(unaffordable.engine, self.source)
        top = self.add_top(unaffordable, "Grizzly Bears")
        unaffordable.state.players["A"].life = 1
        self.prepare(unaffordable)
        self.assertIsNone(self.offer(unaffordable, top, "cast"))
        self.assertEqual([top.ref], self.known_top(unaffordable))

    def test_life_price_keeps_the_spell_additional_sacrifice(self):
        session = self.session(40150605)
        source = self.add(session.engine, self.source)
        creature = self.add(session.engine, "Grizzly Bears")
        opposing = self.add(session.engine, "Grizzly Bears", seat="B", ref="opposing-bear")
        spell = self.add_top(session, "Village Rites")
        self.assert_closed(session, source)
        self.assert_closed(session, spell)
        self.prepare(session)
        self.checkpoint(session)
        action, option = self.life_option(session, spell, 1)
        command = {"action_id": action["id"], "cost_option": option["id"], "pay": "auto"}
        before = authoritative_state_hash(session.state)
        self.assertFalse(session.act("pilot:A", {**command, "cost_cards": [opposing.ref]}).ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        result = session.act("pilot:A", {**command, "cost_cards": [creature.ref]})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(39, session.state.players["A"].life)
        self.assertEqual("graveyard", session.state.cards[creature.object_id].zone)
        self.assertEqual("battlefield", session.state.cards[opposing.object_id].zone)
        self.assertIsNone(self.resolve(session))
        self.replay(session, load=True)

    def test_next_top_stays_hidden_during_a_library_cast_payment(self):
        session = self.session(40150606)
        self.add(session.engine, self.source)
        tax = self.add(session.engine, "Sphere of Resistance")
        target = self.add(session.engine, "Ancient Brontodon", seat="C")
        later = self.add_top(session, "Lightning Bolt", "pending-payment-next-card")
        top = self.add_top(session, "Bring Down", "pending-payment-current-card")
        self.assert_closed(session, tax)
        self.assert_closed(session, top)
        session.state.players["B"].mana_pool["C"] = 1
        self.prepare(session)
        self.checkpoint(session)
        action, option = self.life_option(session, top, 4)
        self.assertEqual(["B"], option["choice_schema"]["assist_helper"]["legal_values"])
        self.take(session, action, cost_option=option["id"], targets=[target.ref], pay="auto", assist_helper="B")
        self.assertFalse(session.state.stack)
        self.assertEqual(40, session.state.players["A"].life)
        self.assertEqual([top.ref], self.known_top(session))
        self.assertNotIn(later.ref, json.dumps(session.packet("pilot:B", full=True)))
        session = self.replay(session, load=True)
        for seat, response in (
            ("B", {"action_id": "assist:finish-mana"}),
            ("A", {"action_id": "assist:finish-mana"}),
            ("B", {"action_id": "assist:contribute", "assist_amount": 1}),
        ):
            result = session.act("pilot:" + seat, response)
            self.assertTrue(result.ok, result.summary)
            self.assertEqual([top.ref], self.known_top(session))
            self.assertEqual([], self.known_top(session, "B"))
        paid = session.act("pilot:A", {"action_id": "assist:pay"})
        self.assertTrue(paid.ok, paid.summary)
        self.assertEqual(36, session.state.players["A"].life)
        self.assertEqual(0, session.state.players["B"].mana_pool["C"])
        self.assertEqual([later.ref], self.known_top(session))
        self.assertEqual([], self.known_top(session, "B"))
        self.assertIsNone(self.resolve(session))
        self.replay(session, load=True)

    def test_land_play_and_nonland_sacrifice_keep_their_ordinary_owners(self):
        session = self.session(40150607)
        source = self.add(session.engine, self.source)
        fodder = [self.add(session.engine, "Grizzly Bears", ref=f"sacrifice-{number}") for number in range(9)]
        second = self.add_top(session, "Forest", "second-quota-land")
        first = self.add_top(session, "Forest", "first-quota-land")
        self.assert_closed(session, source)
        self.prepare(session)
        self.checkpoint(session)
        self.take(session, self.offer(session, first, "play_land"))
        self.assertEqual([second.ref], self.known_top(session))
        self.assertIsNone(self.offer(session, second, "play_land"))
        action, = (row for row in self.actions(session) if row["id"].startswith(f"activate:{source.ref}:"))
        before = authoritative_state_hash(session.state)
        command = {"action_id": action["id"], "cost_cards": [card.ref for card in fodder]}
        self.assertFalse(session.act("pilot:A", command).ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        activated = session.act("pilot:A", {**command, "cost_cards": [source.ref, *command["cost_cards"]]})
        self.assertTrue(activated.ok, activated.summary)
        self.assertEqual([], self.known_top(session))
        self.assertEqual("graveyard", session.state.cards[source.object_id].zone)
        self.assertIsNone(self.resolve(session))
        self.assertEqual([40, 30, 30, 30], [session.state.players[seat].life for seat in "ABCD"])
        self.replay(session, load=True)

    def test_exact_permission_fails_closed_when_authority_is_withdrawn(self):
        record = self.db.lookup(self.source, fuzzy=False)
        registry = self.registry
        result = compile_oracle_card(record, capability_registry=registry, capability_profile=self.profile,
                                     trusted_mechanics={"cr-101-the-magic-golden-rules", "cr-119-life"})
        self.assertEqual("exact", result.status, result.material_residuals)
        self.assertTrue(any(LIBRARY_TOP_LIFE_ACTION_CAPABILITY in node.capability_dependencies
                            for face in result.faces for node in face.nodes))
        for text in ("pay life equal to its power", "pay life equal to its mana value in addition to paying its mana cost"):
            changed = replace(record, oracle_text=record.oracle_text.replace(
                "pay life equal to its mana value rather than pay its mana cost", text,
            ))
            outcome = compile_oracle_card(changed, capability_registry=registry, capability_profile=self.profile)
            self.assertNotEqual("exact", outcome.status)
        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        next(row for row in raw["capabilities"] if row["id"] == LIBRARY_TOP_LIFE_ACTION_CAPABILITY).update(
            status="blocked", blockers=["life-priced library authority withdrawn"],
        )
        withdrawn = CapabilityRegistry(raw)
        withdrawn.mark_evidence_verified(registry.evidence_fingerprint)
        outcome = compile_oracle_card(record, capability_registry=withdrawn, capability_profile=self.profile)
        self.assertNotEqual("exact", outcome.status)
        self.assertTrue(any(LIBRARY_TOP_LIFE_ACTION_CAPABILITY in blocker
                            for row in outcome.material_residuals for blocker in row.blockers))

    def test_omitting_the_canonical_life_debit_is_detected(self):
        owner = "quorune.rules.casting.commit._pay_fixed_life_additional_cost"
        with patch(owner), self.assertRaisesRegex(AssertionError, "38 != 40"):
            self.test_private_price_pays_life_and_preserves_replay()

class MysticForgeCompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registry = load_default_capability_registry()

    def test_exact_top_exile_price_and_grammar_are_closed(self):
        from quorune.rules.controller_top_exile_activation import CONTROLLER_TOP_EXILE_CAPABILITY
        from test_qualified_zone_event_queries import query_record

        text = "{T}, Pay 1 life: Exile the top card of your library."
        card = replace(query_record(text), type_line="Artifact", power=None, toughness=None)
        options = {"capability_registry": self.registry, "capability_profile": "commander_review"}
        ir = compile_oracle_card(card, **options)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        node, = ir.faces[0].nodes
        self.assertEqual((0, len(text), 1), (node.span.start, node.span.end, node.span.line))
        self.assertEqual("activate", node.event)
        self.assertIsNone(node.target_schema)
        self.assertEqual(({"op": "exile_top_library_card", "player": "$controller"},), node.effects)
        self.assertTrue(node.cost["tap_source"])
        self.assertEqual(1, node.cost["life_payment"])
        self.assertIn(CONTROLLER_TOP_EXILE_CAPABILITY, node.capability_dependencies)
        for wording in (
            text.replace("Pay 1 life", "Pay 2 life"),
            text.replace("{T}, ", ""),
            text.replace("your library", "target player's library"),
            text.replace("your library.", "your library face down."),
            text.replace("top card", "top two cards"),
            text + " Activate only as a sorcery.",
            text + " You may play that card this turn.",
        ):
            with self.subTest(wording=wording):
                changed = compile_oracle_card(replace(card, oracle_text=wording), **options)
                self.assertFalse(any(CONTROLLER_TOP_EXILE_CAPABILITY in row.capability_dependencies
                                     for row in changed.faces[0].nodes))

        owner = "quorune.compiler.activation_paid_sacrifice_results.controller_top_exile_activation"
        with patch(owner, return_value=None):
            omitted = compile_oracle_card(card, **options)
        self.assertNotEqual("exact", omitted.status)
        self.assertTrue(omitted.material_residuals)


class MysticForgeNativeTests(InheritedNativeFixture, unittest.TestCase):
    profile = "commander_review"
    forge = "Mystic Forge"
    add_top = fixtures.QualifiedLibraryTopNativeTests.add_top
    actions = fixtures.QualifiedLibraryTopNativeTests.actions
    offer = fixtures.QualifiedLibraryTopNativeTests.offer
    known_top = fixtures.QualifiedLibraryTopNativeTests.known_top
    take = fixtures.QualifiedLibraryTopNativeTests.take

    @classmethod
    def use_pinned_rows(cls, path):
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        name = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("Controller top exile", [DeckEntry(name, 1, "commander"), DeckEntry("Plains", 30)], [name])

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "controller-top-exile.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/mystic-forge-top-exile-cards.json"], path)
        cls.use_pinned_rows(path)

    def activation(self, game, source):
        return next((row for row in self.actions(game)
                     if row["id"].startswith(f"activate:{source.ref}:")), None)

    def test_mystic_forge_full_admission_and_tampered_owners(self):
        from quorune.card_programs.binding import bind_semantic_program_runtime
        from quorune.replacement.immutable import thaw_value
        from quorune.rules.controller_top_exile_activation import CONTROLLER_TOP_EXILE_CAPABILITY
        from test_controller_creature_keyword_absence import register_card

        card = self.db.lookup(self.forge, fuzzy=False)
        self.assertEqual("994bc16d-fdc6-475c-96df-beeb4d5aa03e", card.oracle_id)
        ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile=self.profile)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        self.assertEqual((), ir.material_residuals)
        look, cast, activation = ir.faces[0].nodes
        self.assertEqual(("look-at-own-library-top-v1", "artifact-or-colorless-library-top-action-v2", "controller-top-exile-activation-v1"),
                         (look.template_id, cast.template_id, activation.template_id))
        self.assertEqual((135, 187, 3), (activation.span.start, activation.span.end, activation.span.line))
        report, semantics = register_card(self.db, self.registry, self.forge)
        self.assertTrue(report["strict_capability_ready"], report)
        program, = [item for item in semantics.programs_for_oracle(card.oracle_id) if item.event == "activate"]
        self.assertNotIn("catalog:front", program.key)
        effect, = thaw_value(program.effects)
        for changed in (
            replace(program, effects=({**effect, "player": "$context.target"},)),
            replace(program, effects=({**effect, "face_down": True},)),
            replace(program, effects=(effect, effect)),
            replace(program, effects=({"op": "offer_optional_effect", "effects": [effect], "player": "$controller"},)),
            replace(program, event="resolve", handlers=()),
            replace(program, active_zone="graveyard"),
            replace(program, event_condition={"field": "card", "op": "eq", "value": "$source"}),
            replace(program, cost_schema={**program.cost_schema, "life_payment": True}),
            replace(program, cost_schema={**program.cost_schema, "tap_source": False}),
            replace(program, cost_schema={**program.cost_schema, "life_payment": 2}),
            replace(program, handlers=()),
        ):
            with self.subTest(changed=changed):
                binding = bind_semantic_program_runtime(changed, capability_registry=self.registry, profile=self.profile)
                self.assertFalse(binding["strict"], binding)
        dependencies = tuple(item for item in program.capability_dependencies if item != CONTROLLER_TOP_EXILE_CAPABILITY)
        omitted = replace(program, capability_dependencies=dependencies,
                          capability_closure=None)
        binding = bind_semantic_program_runtime(omitted, capability_registry=self.registry, profile=self.profile)
        self.assertIn("capability:undeclared_runtime_dependency:" + CONTROLLER_TOP_EXILE_CAPABILITY, binding["blockers"])

    def test_actual_activation_pays_and_exiles_the_current_top(self):
        game = self.session(40150701)
        forge = self.add(game.engine, self.forge)
        response = self.add(game.engine, "Reach Through Mists", zone="hand")
        following = self.add_top(game, "Mountain", "forge-following-top")
        exile = self.add_top(game, "Grizzly Bears", "forge-resolution-top")
        opening = self.add_top(game, "Island", "forge-announcement-top")
        self.assert_closed(game, forge)
        self.assert_closed(game, response)
        self.prepare(game, mana={"U": 1})
        self.checkpoint(game)
        self.assertEqual([opening.ref], self.known_top(game))
        self.assertEqual([], self.known_top(game, "B"))
        action = self.activation(game, forge)
        self.assertIsNotNone(action)
        self.assertEqual("activate", action["kind"])
        command = {"action_id": action["id"], "pay": "auto"}
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:B", command).ok)
        self.assertFalse(game.act("pilot:A", {**command, "targets": [opening.ref]}).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        self.take(game, action, pay="auto")
        self.assertEqual(39, game.state.players["A"].life)
        self.assertTrue(game.state.cards[forge.object_id].tapped)
        self.assertEqual([opening.ref], self.known_top(game))
        self.assertIsNone(self.activation(game, forge))
        self.assertEqual([], game.state.stack[-1].targets)

        self.cast(game, "A", response)
        game = self.replay(game, load=True)
        self.resolve_top(game)
        self.assertEqual("hand", game.state.cards[opening.object_id].zone)
        self.assertEqual([exile.ref], self.known_top(game))
        self.resolve_top(game)
        moved = game.state.cards[exile.object_id]
        self.assertEqual(("exile", False), (moved.zone, moved.face_down))
        self.assertNotIn("temporary_play_permission", moved.annotations)
        self.assertEqual([following.ref], self.known_top(game))
        for seat in "BCD":
            packet = json.dumps(game.packet("pilot:" + seat, full=True))
            self.assertIn(exile.ref, packet)
            self.assertNotIn(opening.ref, packet)
            self.assertNotIn(following.ref, packet)
        self.assertEqual(39, game.state.players["A"].life)
        self.replay(game, load=True)

    def test_paid_activation_handles_counter_and_empty_supply(self):
        countered = self.session(40150702)
        source = self.add(countered.engine, self.forge)
        stifle = self.add(countered.engine, "Stifle", seat="B", zone="hand")
        self.add(countered.engine, "Island", seat="B")
        top = self.add_top(countered, "Grizzly Bears", "forge-countered-top")
        self.assert_closed(countered, stifle)
        self.prepare(countered)
        self.checkpoint(countered)
        self.take(countered, self.activation(countered, source), pay="auto")
        activation = countered.state.stack[-1]
        self.cast(countered, "B", stifle, [activation.ref])
        self.resolve_top(countered)
        self.assertFalse(countered.state.stack)
        self.assertEqual("library", countered.state.cards[top.object_id].zone)
        self.assertEqual(39, countered.state.players["A"].life)
        self.assertTrue(countered.state.cards[source.object_id].tapped)
        self.replay(countered, load=True)

        empty = self.session(40150703)
        empty_source = self.add(empty.engine, self.forge)
        for object_id in tuple(empty.state.players["A"].zones["library"]):
            empty.engine.move_card(object_id, "outside", log=False)
        self.prepare(empty)
        self.checkpoint(empty)
        self.take(empty, self.activation(empty, empty_source), pay="auto")
        self.resolve_top(empty)
        self.assertFalse(empty.state.stack)
        self.assertTrue(empty.state.players["A"].in_game)
        self.assertEqual(39, empty.state.players["A"].life)
        self.assertFalse(any(event.code == "library.ingest" for event in empty.state.events))
        self.replay(empty, load=True)

    def test_static_top_look_and_spell_cast_remain_active(self):
        off_priority = self.session(40150704)
        self.add(off_priority.engine, self.forge)
        top = self.add_top(off_priority, "Seat of the Synod", "forge-look-outside-priority")
        self.prepare(off_priority, active="B", actor="B")
        self.assertEqual([top.ref], self.known_top(off_priority))
        self.assertEqual([], self.known_top(off_priority, "B"))

        for number, (name, mana) in enumerate((
            ("Baleful Strix", {"U": 1, "B": 1}),
            ("Eldrazi Devastator", {"C": 8}),
            ("Whip-Spine Drake", {"C": 3}),
            ("Seat of the Synod", {}),
            ("Grizzly Bears", {"C": 1, "G": 1}),
        )):
            with self.subTest(name=name):
                game = self.session(40150705 + number)
                self.add(game.engine, self.forge)
                top = self.add_top(game, name, "forge-static-top")
                self.prepare(game, mana=mana)
                self.checkpoint(game)
                self.assertEqual([top.ref], self.known_top(game))
                self.assertIsNone(self.offer(game, top, "play_land"))
                if name == "Whip-Spine Drake":
                    conventional = [row for row in self.actions(game) if row["id"] == f"cast:{top.ref}"]
                    self.assertFalse(conventional)
                    morph = next((row for row in self.actions(game) if row["id"] == f"cast-morph:{top.ref}"), None)
                    self.assertIsNotNone(morph)
                    self.take(game, morph, pay="auto")
                    self.assertEqual(0, game.state.players["A"].mana_pool["C"])
                    self.resolve_top(game)
                    self.assertTrue(game.state.cards[top.object_id].face_down)
                    self.replay(game, load=True)
                elif name in {"Seat of the Synod", "Grizzly Bears"}:
                    self.assertIsNone(self.offer(game, top, "cast"))
                else:
                    self.take(game, self.offer(game, top, "cast"), pay="auto")
                    self.assertFalse(any(game.state.players["A"].mana_pool.values()))
                    self.resolve_top(game)
                    self.assertEqual("battlefield", game.state.cards[top.object_id].zone)
