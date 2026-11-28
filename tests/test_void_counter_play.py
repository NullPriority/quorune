"""A paid, untargeted exile choice and its ordinary turn play permissions."""

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.card_programs.adapters import compile_best_available_card_program
from quorune.card_programs.binding import bind_card_program_runtime, bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.void_counter_play import CAPABILITY, CHOICE_FIELD, PERMISSIONS_FIELD
from quorune.semantics import SemanticRegistry
from scripts.build_test_database import build_fixture_database


PROFILE = "commander_review"
CARD = "Dauthi Voidwalker"
ORACLE_ID = "f1c2dbe2-fbe0-4058-bdf1-91d1b1832786"
FIXTURE = ROOT / "tests" / "fixtures" / "void-counter-play-cards.json"


class VoidCounterPlayTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        db_path = Path(cls.temporary.name) / "void-counter-play.sqlite3"
        build_fixture_database([FIXTURE], db_path)
        cls.db = CardDatabase(db_path)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("Exile choice", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Plains", 30),
        ], [commander])

    def source(self, game, *, seat="A", ref="void-source"):
        card = self.add(game.engine, CARD, seat=seat, ref=ref)
        card.acquired_control_turn_count = -1
        self.assert_closed(game, card)
        return card

    def mark(self, game, name, *, seat="B", ref="marked-card", known=None):
        card = self.add(game.engine, name, seat=seat, zone="exile", ref=ref)
        card.counters["void"] = 1
        if known is not None:
            card.face_down = True
            card.known_to = list(known)
            card.revealed_to = []
        return card

    def ready(self, game, *, active="A", mana=None):
        game.state.config.semantic_policy = "trusted_only"
        self.prepare(game, active=active, mana=mana or {})
        self.checkpoint(game)

    def offers(self, game, seat="A"):
        self.priority(game, seat)
        actions = game.packet("pilot:" + seat, full=True)["decision"]["ctx"]["legal"]["actions"]
        return {entry["id"]: entry for entry in actions}

    def reject(self, game, seat, command):
        before = authoritative_state_hash(game.state)
        result = game.act("pilot:" + seat, command)
        self.assertFalse(result.ok, result.summary)
        self.assertEqual(before, authoritative_state_hash(game.state))

    def activate_void(self, game, source):
        action_id = f"activate:{source.ref}:ab3"
        self.assertIn(action_id, self.offers(game))
        self.reject(game, "A", {"action_id": action_id, "targets": ["B"], "pay": "auto"})
        result = game.act("pilot:A", {"action_id": action_id, "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        item = game.state.stack[-1]
        self.assertEqual((), tuple(item.targets))
        self.assertEqual(source.object_id, item.source_object_id)
        self.assertNotEqual("battlefield", game.state.cards[source.object_id].zone)

    def offer_choice(self, game):
        self.assertEqual("semantic.choice", game.state.pending_decision.kind)
        self.assertEqual(("pilot:A",), tuple(game.pending_principals()))
        return game.state.pending_decision.payload_by_actor["A"]

    def choose(self, game, card):
        result = game.act("pilot:A", {"action_id": "choose", "objects": [card.ref]})
        self.assertTrue(result.ok, result.summary)

    def test_original_exact_dependency_and_nearby_grammar(self):
        record = self.db.by_oracle_id(ORACLE_ID)
        compiler = lambda card: compile_oracle_card(card, capability_registry=self.registry, capability_profile=PROFILE)
        program = compile_best_available_card_program(
            self.db, record, semantic_registry=SemanticRegistry(),
            capability_registry=self.registry, capability_profile=PROFILE,
        )
        original = compiler(record)
        self.assertEqual("exact", original.status, original.material_residuals)
        self.assertTrue(bind_card_program_runtime(
            program, capability_registry=self.registry, profile=PROFILE,
        )["strict_capability_ready"])
        ability, = (node for node in original.faces[0].nodes if node.event == "activate")
        self.assertIsNone(ability.target_schema)
        self.assertTrue(ability.cost["tap_source"])
        self.assertTrue(ability.cost["sacrifice_source"])
        self.assertIn(CHOICE_FIELD, ability.effects[0])
        registry = SemanticRegistry()
        from common import register_current_fixture_programs
        register_current_fixture_programs(self.db, registry, [record], capability_registry=self.registry)
        current = registry.get(ORACLE_ID + ":ability:ab3")
        self.assertIsNone(current.target_schema)
        self.assertIn(CHOICE_FIELD, current.effects[0])
        for withdrawn in (CAPABILITY, "zone.change.destination_replacement"):
            deps = tuple(value for value in current.capability_dependencies if value != withdrawn)
            mutated = replace(current, capability_dependencies=deps,
                              capability_closure=self.registry.closure(deps, profile=PROFILE).to_dict())
            bound = bind_semantic_program_runtime(mutated, capability_registry=self.registry, profile=PROFILE)
            self.assertFalse(bound["strict"])
            self.assertIn("capability:undeclared_runtime_dependency:" + withdrawn, bound["blockers"])
        for old, new in (
            ("Choose an exiled card", "Target an exiled card"), ("an opponent owns", "you own"),
            ("a void counter", "a time counter"), ("this turn", "until your next turn"),
            ("{T}, Sacrifice", "{1}, {T}, Sacrifice"),
        ):
            with self.subTest(new=new):
                self.assertNotEqual("exact", compiler(replace(
                    record, oracle_text=record.oracle_text.replace(old, new),
                )).status)

    def test_actual_replacement_paid_choice_and_mandatory_additional_cost_replay(self):
        game = self.session(4060101)
        source = self.source(game, seat="C")
        game.engine.change_control(source.object_id, "A", reason="Borrowed replacement source")
        self.assertEqual(("C", "A"), (source.owner, source.controller))
        source.acquired_control_turn_count = -1
        victim = self.add(game.engine, "Grizzly Bears", seat="B", ref="borrowed-opposing-card")
        game.engine.change_control(victim.object_id, "A", reason="Borrowed opposing-owned creature")
        self.assertEqual(("B", "A"), (victim.owner, victim.controller))
        destroy = self.add(game.engine, "Murder", zone="hand", ref="original-destroy")
        payment = self.add(game.engine, "Grizzly Bears", ref="additional-payment")
        remaining = self.add(game.engine, "Grizzly Bears", seat="D", ref="free-spell-target")
        free = self.mark(game, "Bone Splinters", seat="D", ref="earlier-void-sorcery")
        self.assertIn("shadow", game.engine._combat_keywords(source))
        self.ready(game, mana={"C": 1, "B": 2})
        self.cast(game, "A", destroy, [victim.ref])
        self.assertIsNone(self.resolve(game))
        live = game.state.cards[victim.object_id]
        self.assertEqual(("exile", "B", 1), (live.zone, live.owner, live.counters["void"]))
        self.assertFalse(any(event.code == "permanent.dies" and event.details.get("object") == victim.ref
                             for event in game.state.events))
        self.activate_void(game, source)
        spent = game.state.cards[source.object_id]
        self.assertEqual(("exile", "C", 1), (spent.zone, spent.owner, spent.counters["void"]))
        self.resolve(game)
        offered = self.offer_choice(game)
        self.assertEqual({victim.ref, source.ref, free.ref}, {row["id"] for row in offered["objects"]})
        game = self.replay(game, load=True)
        self.reject(game, "C", {"action_id": "choose", "objects": [free.ref]})
        self.choose(game, free)
        offers = self.offers(game)
        action_id = f"cast:{free.ref}"
        self.assertIn(action_id, offers)
        self.assertNotIn(f"cast:{victim.ref}", offers)
        self.reject(game, "A", {"action_id": action_id, "cost_option": "without_mana_cost",
                                "targets": [remaining.ref], "pay": "manual", "payment": {}})
        command = {"action_id": action_id, "cost_option": "without_mana_cost",
                   "targets": [remaining.ref], "sacrifice_cards": [payment.ref],
                   "pay": "manual", "payment": {}}
        result = game.act("pilot:A", command)
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("A", game.state.stack[-1].controller)
        self.assertEqual("graveyard", game.state.cards[payment.object_id].zone)
        self.assertNotIn(PERMISSIONS_FIELD, game.state.cards[free.object_id].annotations)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("graveyard", game.state.cards[remaining.object_id].zone)
        self.replay(game, load=True)

    def test_actual_paid_activation_with_empty_eligible_exile_replays(self):
        game = self.session(4060102)
        source = self.source(game)
        self.mark(game, "Sol Ring", seat="A", ref="own-void-card")
        unrelated = self.add(game.engine, "Forest", seat="B", zone="exile", ref="unmarked-exile")
        self.ready(game)
        self.activate_void(game, source)
        self.assertEqual("graveyard", game.state.cards[source.object_id].zone)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("exile", game.state.cards[unrelated.object_id].zone)
        self.assertFalse(any(event.code == "permission.play" for event in game.state.events))
        self.replay(game, load=True)

    def test_private_known_exile_public_cards_and_stale_selection_rollback(self):
        game = self.session(4060103)
        source = self.source(game)
        stale = self.mark(game, "Sol Ring", ref="stale-exile")
        public = self.mark(game, "Forest", seat="C", ref="public-exile")
        visible = self.mark(game, "Cyclonic Rift", seat="D", ref="known-facedown", known=("A",))
        hidden = self.mark(game, "Murder", ref="unknown-facedown", known=("B",))
        self.add(game.engine, "Grizzly Bears", seat="B", ref="face-down-spell-target")
        self.ready(game)
        self.activate_void(game, source)
        self.resolve(game)
        offered = self.offer_choice(game)
        self.assertEqual({stale.ref, public.ref, visible.ref}, {row["id"] for row in offered["objects"]})
        self.assertIn("Cyclonic Rift", json.dumps(game.packet("pilot:A", full=True)))
        for seat in ("B", "C"):
            packet = json.dumps(game.packet("pilot:" + seat, full=True))
            self.assertIn(public.ref, packet)
            self.assertNotIn("Cyclonic Rift", packet)
        self.reject(game, "A", {"action_id": "choose", "objects": [hidden.ref]})
        self.reject(game, "D", {"action_id": "choose", "objects": [visible.ref]})
        game = self.replay(game, load=True)
        current = game.state.cards[stale.object_id]
        incumbent = current.logical_object_id
        game.engine.move_card(current.object_id, "hand", log=False)
        game.engine.move_card(current.object_id, "exile", log=False)
        current.counters["void"] = 1
        self.assertNotEqual(incumbent, current.logical_object_id)
        self.reject(game, "A", {"action_id": "choose", "objects": [stale.ref]})
        self.choose(game, visible)
        grant = game.engine._temporary_play_permission("A", game.state.cards[visible.object_id])
        self.assertTrue(grant["without_mana_cost"])
        self.assertIsNone(game.engine._temporary_play_permission("D", game.state.cards[visible.object_id]))
        card_offer = self.offers(game)[f"cast:{visible.ref}"]
        self.assertEqual({"without_mana_cost"}, {value["id"] for value in card_offer["cost_options"]})
        for seat in ("B", "C"):
            self.assertNotIn("Cyclonic Rift", json.dumps(game.packet("pilot:" + seat, full=True)))

    def test_ordinary_land_timing_limit_and_end_of_turn_expiry(self):
        for active in ("A", "B"):
            with self.subTest(active=active):
                game = self.session(4060104 + (active == "B"))
                source = self.source(game)
                land = self.mark(game, "Forest")
                own_land = self.add(game.engine, "Plains", zone="hand", ref="extra-land")
                self.ready(game, active=active)
                self.activate_void(game, source)
                self.resolve(game)
                self.choose(game, land)
                action_id = f"play-land:{land.ref}"
                if active == "A":
                    self.assertIn(action_id, self.offers(game))
                    result = game.act("pilot:A", {"action_id": action_id})
                    self.assertTrue(result.ok, result.summary)
                    current = game.state.cards[land.object_id]
                    self.assertEqual(("battlefield", "B", "A"), (current.zone, current.owner, current.controller))
                    self.assertEqual(0, game.state.players["A"].land_plays_remaining)
                    self.assertNotIn(f"play-land:{own_land.ref}", self.offers(game))
                else:
                    self.assertNotIn(action_id, self.offers(game))
                    self.reject(game, "A", {"action_id": action_id})
                    turn = game.state.turn_sequence
                    self.until(game, lambda session: session.state.turn_sequence > turn)
                    current = game.state.cards[land.object_id]
                    self.assertEqual("exile", current.zone)
                    self.assertNotIn(PERMISSIONS_FIELD, current.annotations)
                    self.assertNotIn(action_id, self.offers(game))
                self.replay(game, load=True)

    def test_exact_historical_target_descriptor_replays_without_new_choice(self):
        from quorune.compiler.activated_ability_catalog import with_activated_ability_catalog

        game = self.session(4060107)
        source = self.source(game)
        marked = self.mark(game, "Sol Ring")
        legacy = SemanticRegistry().get(ORACLE_ID + ":ability:ab3")
        (legacy,) = with_activated_ability_catalog(self.db.by_oracle_id(ORACLE_ID), (legacy,))
        game.engine.semantics.put(legacy)
        self.prepare(game)
        self.checkpoint(game)
        action_id = f"activate:{source.ref}:ab3"
        self.assertIn(action_id, self.offers(game))
        result = game.act("pilot:A", {"action_id": action_id, "targets": [marked.ref], "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual((marked.ref,), tuple(game.state.stack[-1].targets))
        self.assertIsNone(self.resolve(game))
        card = game.state.cards[marked.object_id]
        self.assertNotIn(PERMISSIONS_FIELD, card.annotations)
        self.assertTrue(card.annotations["temporary_play_permission"]["without_mana_cost"])
        self.replay(game, load=True)

    def test_excluding_existing_look_permission_mutation_is_killed(self):
        from quorune.rules import void_counter_play
        game = self.session(4060106)
        known = self.mark(game, "Sol Ring", ref="existing-visibility", known=("A",))
        self.mark(game, "Forest", ref="hidden-visibility", known=("B",))

        def witness():
            self.assertEqual((known.ref,), void_counter_play.eligible_exile_card_refs(game.engine, "A"))

        witness()
        original = void_counter_play.is_eligible_exile_card

        def discover_unknown(card, actor, active):
            return original(replace(card, face_down=False), actor, active)

        with patch.object(void_counter_play, "is_eligible_exile_card", side_effect=discover_unknown):
            with self.assertRaises(AssertionError):
                witness()


class UrzaNativeTests(InheritedNativeFixture, unittest.TestCase):
    NAME = "Urza, Lord High Artificer"
    ORACLE = "e87906d2-db1a-4e19-b910-adb4eb339945"
    offers = VoidCounterPlayTests.offers
    reject = VoidCounterPlayTests.reject

    @classmethod
    def setUpClass(cls):
        from os import environ

        cls.temporary = TemporaryDirectory()
        database = environ.get("MTG_CARD_DB")
        if database is None:
            directory = Path(cls.temporary.name)
            fixtures = ROOT / "tests" / "fixtures"
            selected = {
                "creature-subtype-set-results-cards.json": {"Ornithopter"},
                "cast-spell-mana-value-cards.json": {"Blaze"},
                "face-up-exiled-card-owner-shuffle-cards.json": {"Pull from Eternity"},
                "victimize-cards.json": {"Tormod's Crypt"},
            }
            paths = [FIXTURE]
            for name, names in selected.items():
                data = json.loads((fixtures / name).read_bytes())
                data["cards"] = [row for row in data["cards"] if row["name"] in names]
                assert {row["name"] for row in data["cards"]} == names
                ids = {row["oracle_id"] for row in data["cards"]}
                data["rulings"] = [r for r in data.get("rulings", ()) if r["oracle_id"] in ids]
                output = directory / name
                output.write_text(json.dumps(data), encoding="utf-8")
                paths.append(output)
            database = directory / "urza.sqlite3"
            build_fixture_database(paths, database)
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("Urza native", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Plains", 30),
        ], [commander])

    def test_paid_permission_requires_its_original_price_and_ordered_effects(self):
        from common import register_current_fixture_programs

        capability = "activation.resolution.shuffled_top_free_play"
        card = self.db.by_oracle_id(self.ORACLE)
        registry = SemanticRegistry()
        register_current_fixture_programs(self.db, registry, [card], capability_registry=self.registry)
        program = self.paid_program(registry)
        self.assertIn(capability, program.capability_dependencies)
        self.assertIsNone(program.target_schema)
        self.assertEqual("{5}", program.cost_schema["text"])
        self.assertEqual(["shuffle_controller_library", "fixed_impulse_access"],
                         [effect["op"] for effect in program.effects])
        self.assertEqual(9, program.effects[1]["schema_version"])
        self.assertTrue(bind_semantic_program_runtime(program, capability_registry=self.registry,
                                                      profile=PROFILE)["strict"])
        reversed_program = replace(program, effects=list(reversed(program.effects)))
        self.assertFalse(bind_semantic_program_runtime(reversed_program, capability_registry=self.registry,
                                                       profile=PROFILE)["strict"])
        deps = tuple(key for key in program.capability_dependencies if key != capability)
        withdrawn = replace(program, capability_dependencies=deps,
                            capability_closure=self.registry.closure(deps, profile=PROFILE).to_dict())
        failure = bind_semantic_program_runtime(withdrawn, capability_registry=self.registry, profile=PROFILE)
        self.assertFalse(failure["strict"])
        self.assertIn("capability:undeclared_runtime_dependency:" + capability, failure["blockers"])
        compile_card = lambda record: compile_oracle_card(
            record, capability_registry=self.registry, capability_profile=PROFILE)
        self.assertEqual("exact", compile_card(card).status)
        for changed in (
            card.oracle_text.replace("then exile the top card", "then exile the top two cards"),
            card.oracle_text.replace("Until end of turn", "Until your next turn"),
        ):
            with self.subTest(oracle_text=changed):
                self.assertNotEqual("exact", compile_card(replace(card, oracle_text=changed)).status)
        with patch("quorune.compiler.impulse_access_templates.shuffled_top_free_play_activation", return_value=None):
            self.assertNotEqual("exact", compile_card(card).status)

    def test_actual_construct_and_selected_mana_use_a_summoning_sick_borrowed_artifact(self):
        game = self.session(5701121)
        source = self.add(game.engine, self.NAME, zone="hand")
        fresh = self.add(game.engine, "Ornithopter", seat="B", ref="urza-new-borrowed")
        game.engine.change_control(fresh.object_id, "A", reason="Fixture separates control and ownership")
        fresh.acquired_control_turn_count = game.state.players["A"].turns_begun
        foreign = self.add(game.engine, "Sol Ring", seat="B", ref="urza-other-artifact")
        tapped = self.add(game.engine, "Sol Ring", ref="urza-tapped-artifact")
        tapped.tapped = True
        nonartifact = self.add(game.engine, "Grizzly Bears", ref="urza-nonartifact")
        self.prepare(game, mana={"C": 2, "U": 2})
        self.checkpoint(game)
        self.cast(game, "A", source)
        self.assertIsNone(self.resolve(game))
        construct, = (card for card in game.state.cards.values()
                      if card.is_token and card.zone == "battlefield")
        types, subtypes, _ = game.engine._effective_type_parts(construct)
        self.assertEqual({"artifact", "creature"}, set(types))
        self.assertIn("construct", subtypes)
        self.assertEqual((3, 3), tuple(game.engine._numeric_stat(construct.object_id, stat)
                                     for stat in ("power", "toughness")))
        action_id = f"activate:{source.ref}:ab2"
        offer = self.offers(game)[action_id]
        self.assertFalse(offer.get("target_schema"))
        cost, = offer["cost_summary"]["choose_cost"]
        self.assertEqual(("tap", 1), (cost["k"], cost["n"]))
        self.assertEqual({fresh.ref, construct.ref}, set(cost["legal_refs"]))
        self.assertEqual(("$actor", None, False, ["artifact"]),
                         tuple(cost["q"][key] for key in ("controller", "owner", "tapped", "types_all")))
        self.assertEqual(game.state.players["A"].turns_begun,
                         game.state.cards[fresh.object_id].acquired_control_turn_count)
        for illegal in ([], [source.ref], [foreign.ref], [tapped.ref], [nonartifact.ref], [fresh.ref, construct.ref]):
            self.reject(game, "A", {"action_id": action_id, "cost_cards": illegal})
        self.reject(game, "A", {"action_id": action_id, "cost_cards": [fresh.ref], "targets": [foreign.ref]})
        self.reject(game, "B", {"action_id": action_id, "cost_cards": [fresh.ref]})
        game = self.replay(game, load=True)
        accepted = game.act("pilot:A", {"action_id": action_id, "cost_cards": [fresh.ref]})
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertFalse(game.state.stack)
        self.assertEqual(1, game.state.players["A"].mana_pool["U"])
        self.assertTrue(game.state.cards[fresh.object_id].tapped)
        self.assertFalse(game.state.cards[source.object_id].tapped)
        self.assertFalse(game.state.cards[foreign.object_id].tapped)
        self.assertEqual(("B", "A"), (game.state.cards[fresh.object_id].owner, game.state.cards[fresh.object_id].controller))
        self.replay(game, load=True)

    def paid_scene(self, seed, desired, *, active="A", extra_mana=0):
        from random import Random

        game = self.session(seed)
        source = self.add(game.engine, self.NAME)
        self.assert_closed(game, source)
        game.state.config.semantic_policy = "trusted_only"
        player = game.state.players["A"]
        for object_id in tuple(player.zones["library"]):
            game.engine.move_card(object_id, "outside", log=False)
        next_shuffle = int(player.stats.get("shuffle_count", 0)) + 1
        names = ["Sol Ring", "Grizzly Bears", "Forest", "Murder"] if desired else []
        indices = list(range(len(names)))
        Random(f"{seed}|A|shuffle|{next_shuffle}").shuffle(indices)
        if indices:
            names[indices[-1]] = desired
        cards = tuple(self.add(game.engine, name, zone="library", ref=f"urza-unknown-library-{i}")
                      for i, name in enumerate(names))
        for card in cards:
            card.known_to = []
            card.revealed_to = []
        self.prepare(game, active=active, actor="A", mana={"C": 5 + extra_mana})
        shuffled = tuple(cards[index] for index in indices)
        return game, source, cards, shuffled

    def paid_program(self, registry):
        programs = [program for program in registry.programs_for_oracle(self.ORACLE)
                    if program.event == "activate" and (program.cost_schema or {}).get("text") == "{5}"]
        self.assertEqual(1, len(programs))
        return programs[0]

    def start_paid(self, game, source):
        program = self.paid_program(game.engine.semantics)
        action_id = f"activate:{source.ref}:{program.ability_id.removeprefix('ability:')}"
        self.assertIn(action_id, self.offers(game))
        prior = tuple(game.state.players["A"].zones["library"])
        count = game.state.players["A"].stats.get("shuffle_count", 0)
        mana = game.state.players["A"].mana_pool.get("C", 0)
        self.reject(game, "A", {"action_id": action_id, "targets": ["B"], "pay": "auto"})
        accepted = game.act("pilot:A", {"action_id": action_id, "pay": "auto"})
        self.assertTrue(accepted.ok, accepted.summary)
        item, = game.state.stack
        self.assertEqual(("activated_ability", source.object_id, []), (item.kind, item.source_object_id, item.targets))
        self.assertEqual(prior, tuple(game.state.players["A"].zones["library"]))
        self.assertEqual(count, game.state.players["A"].stats.get("shuffle_count", 0))
        self.assertEqual(mana - 5, game.state.players["A"].mana_pool.get("C", 0))
        return item

    def current_permission(self, game, card, seat="A"):
        from quorune.impulse_access import current_temporary_play_permission

        return current_temporary_play_permission(game.state, seat, game.state.cards[card.object_id])

    def finish_paid(self, game, shuffled):
        from quorune.zone_transitions import ZoneTransitionOwner

        event_mark = game.state.event_sequence
        expected = shuffled[-1] if shuffled else None
        original_move = ZoneTransitionOwner.move_cards_simultaneously
        observations = []

        def witness(owner, changes, **kwargs):
            result = original_move(owner, changes, **kwargs)
            if expected and tuple(changes) == ((expected.object_id, "exile"),):
                live = owner.state.cards[expected.object_id]
                shuffle_before = any(event.code == "library.shuffle" and event.event_id > event_mark
                                     for event in owner.state.events)
                observations.append((shuffle_before, live.zone, "temporary_play_permission" in live.annotations))
            return result

        with patch.object(ZoneTransitionOwner, "move_cards_simultaneously", autospec=True, side_effect=witness):
            self.assertIsNone(self.resolve(game))
        phases = [row for row in game.state.events if row.event_id > event_mark
                  and row.code in {"library.shuffle", "library.impulse_access"}]
        self.assertEqual(["library.shuffle", "library.impulse_access"], [row.code for row in phases])
        self.assertEqual([expected.ref] if expected else [], phases[1].details["objects"])
        self.assertEqual([card.object_id for card in shuffled[:-1]], game.state.players["A"].zones["library"])
        if not expected:
            self.assertEqual([], observations)
            return None
        self.assertEqual([(True, "exile", False)], observations)
        grant = self.current_permission(game, expected)
        self.assertTrue(grant["without_mana_cost"])
        self.assertTrue(grant["allow_land"])
        self.assertTrue(grant["allow_spell"])
        self.assertFalse(grant["allows_other_spell_costs"])
        self.assertEqual("until_end_of_turn", grant["duration"])
        for seat in "BCD":
            self.assertIsNone(self.current_permission(game, expected, seat))
        return expected

    def test_actual_paid_shuffle_exile_permission_and_instant_alternative_cost(self):
        game, source, cards, shuffled = self.paid_scene(5701122, "Cyclonic Rift", active="B")
        victim = self.add(game.engine, "Grizzly Bears", seat="B", ref="urza-rift-target")
        removal = self.add(game.engine, "Murder", seat="B", zone="hand", ref="urza-departure-response")
        game.state.players["B"].mana_pool.update({"C": 1, "B": 2})
        self.checkpoint(game)
        self.start_paid(game, source)
        self.cast(game, "B", removal, [source.ref])
        self.resolve_top(game)
        self.assertEqual("graveyard", game.state.cards[source.object_id].zone)
        self.assertNotEqual(cards[-1].object_id, shuffled[-1].object_id)
        card = self.finish_paid(game, shuffled)
        for seat in "ABCD":
            serialized = json.dumps(game.packet(f"pilot:{seat}", full=True))
            self.assertIn(card.ref, serialized)
            self.assertIn(card.printed_name, serialized)
            for remaining in shuffled[:-1]:
                self.assertNotIn(remaining.ref, serialized)
        game = self.replay(game, load=True)
        action = f"cast:{card.ref}"
        offer = self.offers(game)[action]
        self.assertEqual({"without_mana_cost"}, {row["id"] for row in offer["cost_options"]})
        self.reject(game, "A", {"action_id": action, "cost_option": "overload", "pay": "auto"})
        self.reject(game, "B", {"action_id": action, "cost_option": "without_mana_cost", "targets": [victim.ref]})
        paid = game.act("pilot:A", {"action_id": action, "cost_option": "without_mana_cost",
                                   "targets": [victim.ref], "pay": "manual", "payment": {}})
        self.assertTrue(paid.ok, paid.summary)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("hand", game.state.cards[victim.object_id].zone)
        self.assertIsNone(self.current_permission(game, card))
        self.replay(game, load=True)

    def test_actual_free_sorcery_still_requires_its_additional_cost(self):
        game, source, _, shuffled = self.paid_scene(5701123, "Bone Splinters")
        payment = self.add(game.engine, "Grizzly Bears", ref="urza-splinters-cost")
        target = self.add(game.engine, "Grizzly Bears", seat="D", ref="urza-splinters-target")
        self.checkpoint(game)
        self.start_paid(game, source)
        card = self.finish_paid(game, shuffled)
        game = self.replay(game, load=True)
        action = f"cast:{card.ref}"
        self.assertIn(action, self.offers(game))
        command = {"action_id": action, "cost_option": "without_mana_cost", "targets": [target.ref],
                   "pay": "manual", "payment": {}}
        self.reject(game, "A", command)
        result = game.act("pilot:A", {**command, "sacrifice_cards": [payment.ref]})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("graveyard", game.state.cards[payment.object_id].zone)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("graveyard", game.state.cards[target.object_id].zone)
        self.replay(game, load=True)

    def test_actual_free_x_spell_enforces_zero_despite_available_mana(self):
        game, source, _, shuffled = self.paid_scene(5701124, "Blaze", extra_mana=2)
        self.checkpoint(game)
        self.start_paid(game, source)
        card = self.finish_paid(game, shuffled)
        life = game.state.players["B"].life
        action = f"cast:{card.ref}"
        self.assertIn(action, self.offers(game))
        command = {"action_id": action, "cost_option": "without_mana_cost", "targets": ["B"], "pay": "auto"}
        self.reject(game, "A", {**command, "x": 1})
        accepted = game.act("pilot:A", {**command, "x": 0})
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(life, game.state.players["B"].life)
        self.assertEqual(2, game.state.players["A"].mana_pool["C"])
        self.replay(game, load=True)

    def test_actual_land_play_limit_off_turn_sorcery_and_grant_expiry(self):
        cases = (("Forest", "A"), ("Bone Splinters", "B"), ("Forest", "B"))
        for index, (name, active) in enumerate(cases):
            with self.subTest(name=name, active=active):
                game, source, _, shuffled = self.paid_scene(5701125 + index, name, active=active)
                hand_land = self.add(game.engine, "Plains", zone="hand", ref="urza-extra-land")
                self.add(game.engine, "Grizzly Bears", ref="urza-eligible-additional-payment")
                self.add(game.engine, "Grizzly Bears", seat="D", ref="urza-eligible-sorcery-target")
                self.checkpoint(game)
                self.start_paid(game, source)
                card = self.finish_paid(game, shuffled)
                game = self.replay(game, load=True)
                if active == "A":
                    action = f"play-land:{card.ref}"
                    self.assertIn(action, self.offers(game))
                    result = game.act("pilot:A", {"action_id": action})
                    self.assertTrue(result.ok, result.summary)
                    self.assertEqual("battlefield", game.state.cards[card.object_id].zone)
                    self.assertEqual(0, game.state.players["A"].land_plays_remaining)
                    self.assertNotIn(f"play-land:{hand_land.ref}", self.offers(game))
                else:
                    action = f"play-land:{card.ref}" if name == "Forest" else f"cast:{card.ref}"
                    self.assertNotIn(action, self.offers(game))
                    self.reject(game, "A", {"action_id": action})
                    turn = game.state.turn_sequence
                    self.until(game, lambda current: current.state.turn_sequence > turn)
                    self.assertEqual("exile", game.state.cards[card.object_id].zone)
                    self.assertIsNone(self.current_permission(game, card))
                    self.assertNotIn(action, self.offers(game))
                self.replay(game, load=True)

    def test_actual_public_exile_reentry_does_not_restore_the_original_permission(self):
        game, source, _, shuffled = self.paid_scene(5701127, "Forest")
        move = self.add(game.engine, "Pull from Eternity", seat="B", zone="hand")
        crypt = self.add(game.engine, "Tormod's Crypt", seat="C")
        game.state.players["B"].mana_pool["W"] = 1
        self.checkpoint(game)
        self.start_paid(game, source)
        card = self.finish_paid(game, shuffled)
        initial = game.state.cards[card.object_id].logical_object_id
        self.cast(game, "B", move, [card.ref])
        self.assertIsNone(self.resolve(game))
        self.assertEqual("graveyard", game.state.cards[card.object_id].zone)
        offers = self.offers(game, "C")
        ability, = [key for key in offers if key.startswith(f"activate:{crypt.ref}:")]
        accepted = game.act("pilot:C", {"action_id": ability, "targets": ["A"], "pay": "auto"})
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("exile", game.state.cards[card.object_id].zone)
        self.assertNotEqual(initial, game.state.cards[card.object_id].logical_object_id)
        self.assertIsNone(self.current_permission(game, card))
        action = f"play-land:{card.ref}"
        self.assertNotIn(action, self.offers(game))
        self.reject(game, "A", {"action_id": action})
        self.replay(game, load=True)

    def test_actual_empty_library_shuffles_without_granting_access(self):
        game, source, _, shuffled = self.paid_scene(5701128, None)
        retained = self.add(game.engine, "Forest", zone="exile", ref="urza-unrelated-exile")
        self.checkpoint(game)
        self.start_paid(game, source)
        self.assertIsNone(self.finish_paid(game, shuffled))
        self.assertIsNone(self.current_permission(game, retained))
        self.assertNotIn(f"play-land:{retained.ref}", self.offers(game))
        self.replay(game, load=True)
