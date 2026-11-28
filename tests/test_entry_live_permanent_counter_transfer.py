"""Exercise a mandatory counter transfer before the original entry."""

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.semantic_runtime.context import SemanticNodeError
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


ORIGINAL = "Thief of Blood"


class EntryLivePermanentCounterTransferTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "entry-live-permanent-counter-transfer.sqlite3"
        fixture = ROOT / "tests/fixtures/entry-live-permanent-counter-transfer-cards.json"
        build_fixture_database([fixture], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Entry counter transfer", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def source_program(self):
        card = self.db.lookup(ORIGINAL, fuzzy=False)
        binding, semantics = register_card(self.db, self.registry, ORIGINAL)
        self.assertTrue(binding["strict_capability_ready"], binding["blockers"])
        program, = [item for item in semantics.programs_for_oracle(card.oracle_id) if item.event == "zone.change"]
        return card, program

    def test_complete_original_keeps_flying_and_one_mandatory_entry_component(self):
        card, program = self.source_program()
        self.assertIsNone(program.target_schema)
        self.assertFalse(program.effects)
        descriptor, = program.handlers
        self.assertEqual("zone.entry.permanent-counter-transfer.v1", descriptor["handler_id"])
        self.assertEqual("zone.change", descriptor["event"])
        self.assertEqual(1, descriptor["schema_version"])
        ir = compile_oracle_card(card, capability_registry=self.registry)
        self.assertIn("Flying", [node.text for face in ir.faces for node in face.nodes])
        for text in (
            card.oracle_text.replace("As this creature enters", "When this creature enters"),
            card.oracle_text.replace("all counters", "all charge counters"),
            card.oracle_text.replace("all permanents", "all creatures"),
            card.oracle_text.replace("for each counter removed this way", "for each creature you control"),
            card.oracle_text + " Draw a card.",
        ):
            with self.subTest(text=text):
                self.assertNotEqual("exact", compile_oracle_card(replace(card, oracle_text=text),
                                    capability_registry=self.registry).status)

    def test_raw_transfer_requires_the_exact_version_and_entry_component(self):
        _, program = self.source_program()
        descriptor, = program.handlers
        for malformed in ({**descriptor, "schema_version": True}, {**descriptor, "allow_players": True}):
            with self.subTest(descriptor=malformed), self.assertRaises(SemanticNodeError):
                replace(program, handlers=(malformed,))
        changed = replace(program, active_zone="battlefield")
        binding = bind_semantic_program_runtime(changed, capability_registry=self.registry,
            profile="commander_review")
        self.assertFalse(binding["strict"], binding)

    def test_actual_mixed_counter_pool_is_removed_before_entry_and_entry_counter_replacement(self):
        game = self.session(61343301)
        engine = game.engine
        thief = self.add(engine, ORIGINAL, zone="hand")
        ripple = self.add(engine, "Reality Ripple", zone="hand")
        scales = self.add(engine, "Hardened Scales")
        feeder = self.add(engine, "Spike Feeder", seat="C")
        jace = self.add(engine, "Jace, Memory Adept", seat="B")
        ring = self.add(engine, "Sol Ring", seat="B")
        hidden_by_phasing = self.add(engine, "Darksteel Citadel", seat="D")
        feeder.counters["+1/+1"] = 2
        jace.counters["loyalty"] = 4
        jace.annotations["loyalty_initialized"] = True
        ring.counters.update({"charge": 3, "stun": 2})
        hidden_by_phasing.counters["charge"] = 7
        game.state.players["D"].poison = 3
        for card in (thief, ripple, scales):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 5, "B": 2, "U": 1})
        self.checkpoint(game)
        self.cast_for(game, "A", ripple, [hidden_by_phasing.ref])
        self.resolve_top(game)
        self.assertTrue(game.state.cards[hidden_by_phasing.object_id].phased_out)
        self.cast_for(game, "A", thief)
        game = self.replay(game, load=True)
        mark = game.state.event_sequence
        removal_zones = []
        original_record = type(game.engine)._record_all_removed_counters

        def observe_removal(host, result, card, *, actor, source_ref):
            removal_zones.append(host.state.cards[thief.object_id].zone)
            return original_record(host, result, card, actor=actor, source_ref=source_ref)

        with patch.object(type(game.engine), "_record_all_removed_counters", observe_removal):
            self.resolve_top(game)
        entrant = game.state.cards[thief.object_id]
        self.assertEqual(("battlefield", 12), (entrant.zone, entrant.counters["+1/+1"]))
        self.assertIn("flying", game.engine._combat_keywords(entrant))
        self.assertEqual({}, game.state.cards[ring.object_id].counters)
        self.assertEqual("graveyard", game.state.cards[feeder.object_id].zone)
        self.assertEqual("graveyard", game.state.cards[jace.object_id].zone)
        self.assertEqual(7, game.state.cards[hidden_by_phasing.object_id].counters["charge"])
        self.assertEqual(3, game.state.players["D"].poison)
        events = [event for event in game.state.events if event.event_id > mark]
        removal = [event for event in events if event.code == "permanent.counters_removed"]
        self.assertEqual({ring.object_id, feeder.object_id, jace.object_id},
                         {object_id for event in removal for object_id in event.changed_objects})
        self.assertEqual(["stack", "stack", "stack"], removal_zones)
        self.replay(game, load=True)

    def test_actual_simultaneous_entries_offer_either_recipient_and_consume_the_pool_once(self):
        for chosen_index in range(2):
            with self.subTest(chosen_index=chosen_index):
                game = self.session(61343303 + chosen_index)
                engine = game.engine
                entrants = [self.add(engine, ORIGINAL, zone="graveyard", seat=seat, ref=f"thief-entry-{seat}")
                            for seat in "BC"]
                spell = self.add(engine, "Rise of the Dark Realms", zone="hand")
                ring = self.add(engine, "Sol Ring", seat="B")
                ring.counters["charge"] = 4
                for card in (spell, *entrants):
                    self.assert_closed(game, card)
                self.prepare(game, mana={"C": 7, "B": 2})
                self.checkpoint(game)
                self.cast_for(game, "A", spell)
                decision = self.resolve(game)
                self.assertEqual("replacement.order", decision.kind)
                self.assertEqual(["pilot:A"], game.pending_principals())
                self.assertEqual(["graveyard", "graveyard"], [game.state.cards[card.object_id].zone for card in entrants])
                self.assertEqual(4, game.state.cards[ring.object_id].counters["charge"])
                game = self.replay(game, load=True)
                packet = game.packet("pilot:A", full=True)["decision"]["ctx"]
                self.assertEqual(2, len(packet["event_order_options"]))
                chosen = entrants[chosen_index]
                event, = [value for value in packet["event_order_options"] if value.endswith(":" + chosen.ref)]
                effect, = packet["event_effect_options"][event]
                command = {"action_id": "choose", "replacement_event": event, "replacement": effect}
                before = authoritative_state_hash(game.state)
                self.assertFalse(game.act("pilot:D", command).ok)
                self.assertEqual(before, authoritative_state_hash(game.state))
                result = game.act("pilot:A", command)
                self.assertTrue(result.ok, result.summary)
                for index, card in enumerate(entrants):
                    entrant = game.state.cards[card.object_id]
                    self.assertEqual(("battlefield", "A"), (entrant.zone, entrant.controller))
                    self.assertEqual(4 if index == chosen_index else 0, entrant.counters.get("+1/+1", 0))
                self.assertEqual({}, game.state.cards[ring.object_id].counters)
                self.replay(game, load=True)

    def test_actual_token_copy_uses_the_original_entry_rule_before_publication(self):
        game = self.session(61343305)
        original = self.add(game.engine, ORIGINAL)
        spell = self.add(game.engine, "Cackling Counterpart", zone="hand")
        original.counters["+1/+1"] = 4
        for card in (original, spell):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 1, "U": 2})
        self.checkpoint(game)
        self.cast_for(game, "A", spell, [original.ref])
        self.resolve_top(game)
        token, = [card for card in game.state.cards.values()
                  if card.is_token and card.zone == "battlefield" and card.controller == "A"]
        self.assertEqual(4, token.counters["+1/+1"])
        self.assertEqual({}, game.state.cards[original.object_id].counters)
        self.assertIn("flying", game.engine._combat_keywords(token))
        self.replay(game, load=True)


class ChosenEntryCounterTransferTests(InheritedNativeFixture, unittest.TestCase):
    entrant_name = "Siltstone Chronicler"
    destroy_name = "Break the Mast"
    exile_name = "Scatter the Record"
    mixed_name = "Brasshold Scout"

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        home = Path(cls.temporary.name)
        fixture = home / "authored-permanent-choices.json"

        def record(number, name, type_line, text="", colors=(), *, creature=False):
            value = {
                "name": name, "oracle_id": f"adefed92-321d-4fcc-865e-{number:012d}",
                "layout": "normal", "type_line": type_line,
                "oracle_text": text, "colors": list(colors), "color_identity": list(colors),
                "mana_cost": "{G}" if colors == ("G",) else "{B}" if colors == ("B",) else "{1}",
                "cmc": 1, "legalities": {"commander": "legal"},
                "keywords": ["Flying"] if name == cls.entrant_name else [],
            }
            if creature:
                value.update(power="2", toughness="2")
            return value

        source_text = (
            "Flying\nAs this creature enters, remove all counters from any number of creatures, enchantments, and artifacts. "
            "This creature enters with X +1/+1 counters on it, where X equals twice the number of counters removed this way.\n"
            "When this creature dies, put the counters it had on target creature you control. "
            "Then shuffle this card into its owner's library."
        )
        fixture.write_text(json.dumps({"schema_version": 1, "source": "authored entry and departure tests", "rulings": [],
            "cards": [
                record(1, cls.entrant_name, "Creature — Salamander", source_text, ("G",), creature=True),
                record(2, cls.destroy_name, "Instant", "Destroy target creature.", ("B",)),
                record(3, cls.exile_name, "Instant", "Exile target card from a graveyard.", ("B",)),
                record(4, cls.mixed_name, "Artifact Creature — Golem", creature=True),
            ]}), encoding="utf-8")
        path = home / "chosen-entry-counter-transfer.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/entry-live-permanent-counter-transfer-cards.json", fixture], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Chosen entry counter transfer", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def test_chosen_public_transfer_counts_each_selected_permanent_once_and_replays(self):
        game = self.session(61343321)
        entrant = self.add(game.engine, self.entrant_name, zone="hand")
        mixed = self.add(game.engine, self.mixed_name, seat="B", ref="mixed-public-counter-pool")
        enchantment = self.add(game.engine, "Hardened Scales")
        unchosen = self.add(game.engine, "Sol Ring", seat="C")
        land = self.add(game.engine, "Plains", seat="D")
        hidden = self.add(game.engine, self.mixed_name, seat="D", zone="hand", ref="hidden-counter-pool-decoy")
        mixed.counters.update({"charge": 3, "stun": 2})
        enchantment.counters["charge"] = 1
        unchosen.counters["charge"] = 4
        land.counters["charge"] = 7
        self.assert_closed(game, entrant)
        self.prepare(game, mana={"G": 1})
        self.checkpoint(game)
        self.cast_for(game, "A", entrant)
        self.assertEqual("replacement.order", self.resolve(game).kind)
        game = self.replay(game, load=True)
        choice = game.packet("pilot:A", full=True)["decision"]["ctx"]
        effect, = choice["object_choices"]
        options = choice["object_choices"][effect]
        self.assertEqual(0, options["minimum"])
        self.assertEqual({mixed.ref, enchantment.ref, unchosen.ref}, set(options["legal_refs"]))
        self.assertNotIn(hidden.ref, json.dumps(choice))

        def command(refs):
            return {"action_id": "choose", "replacement": effect, "replacement_objects": refs}

        before = authoritative_state_hash(game.state)
        for actor, refs in (("B", [mixed.ref]), ("A", [land.ref]), ("A", [mixed.ref, mixed.ref])):
            result = game.act(f"pilot:{actor}", command(refs))
            self.assertFalse(result.ok)
            self.assertEqual(before, authoritative_state_hash(game.state))
        result = game.act("pilot:A", command([mixed.ref, enchantment.ref]))
        self.assertTrue(result.ok, result.summary)
        self.assertEqual({"+1/+1": 13}, game.state.cards[entrant.object_id].counters)
        self.assertEqual({}, game.state.cards[mixed.object_id].counters)
        self.assertEqual({}, game.state.cards[enchantment.object_id].counters)
        self.assertEqual(4, game.state.cards[unchosen.object_id].counters["charge"])
        self.assertEqual(7, game.state.cards[land.object_id].counters["charge"])
        self.replay(game, load=True)

    def test_simultaneous_chosen_transfers_allow_zero_and_reserve_each_counter_pool(self):
        game = self.session(61343322)
        entrants = [
            self.add(game.engine, self.entrant_name, seat=seat, zone="graveyard", ref=f"counter-entrant-{seat}")
            for seat in "BCD"
        ]
        spell = self.add(game.engine, "Rise of the Dark Realms", zone="hand")
        first = self.add(game.engine, "Sol Ring", seat="B", ref="simultaneous-first-pool")
        second = self.add(game.engine, "Sol Ring", seat="C", ref="simultaneous-second-pool")
        first.counters["charge"] = 3
        second.counters["stun"] = 2
        self.prepare(game, mana={"C": 7, "B": 2})
        self.checkpoint(game)
        self.cast_for(game, "A", spell)
        self.assertEqual("replacement.order", self.resolve(game).kind)
        for entrant, refs in zip(entrants, ([], [first.ref], [first.ref, second.ref]), strict=True):
            ctx = game.packet("pilot:A", full=True)["decision"]["ctx"]
            command = {"action_id": "choose", "replacement_objects": refs}
            if ctx.get("event_order_options"):
                event, = [event for event in ctx["event_order_options"] if event.endswith(":" + entrant.ref)]
                effect, = [effect for effect in ctx["event_effect_options"][event] if effect in ctx["object_choices"]]
                command["replacement_event"] = event
            else:
                effect, = ctx["object_choices"]
            command["replacement"] = effect
            result = game.act("pilot:A", command)
            self.assertTrue(result.ok, result.summary)
        for entrant, amount in zip(entrants, (0, 6, 4), strict=True):
            current = game.state.cards[entrant.object_id]
            self.assertEqual(("battlefield", "A", amount),
                             (current.zone, current.controller, current.counters.get("+1/+1", 0)))
        self.assertEqual({}, game.state.cards[first.object_id].counters)
        self.assertEqual({}, game.state.cards[second.object_id].counters)
        self.replay(game, load=True)

    def _death_stack(self, seed, counters, *, exile_source=False, destroy_target=False):
        game = self.session(seed)
        source = self.add(game.engine, self.entrant_name)
        target = self.add(game.engine, self.mixed_name, seat="B")
        spell = self.add(game.engine, self.destroy_name, zone="hand", ref="departure-destroy-spell")
        followup = self.add(game.engine, self.exile_name if exile_source else self.destroy_name,
                            zone="hand", ref="departure-followup-spell")
        source.counters.update(counters)
        game.engine.change_control(source.object_id, "B", reason="Set owner and controller independently")
        self.assert_closed(game, source)
        self.prepare(game, mana={"B": 2})
        self.checkpoint(game)
        self.cast_for(game, "A", spell, [source.ref])
        self.assertEqual("semantic.target", self.resolve(game).kind)
        self.target(game, "B", [target.ref])
        if exile_source or destroy_target:
            self.cast_for(game, "A", followup, [source.ref if exile_source else target.ref])
            self.resolve_top(game)
        return game, source, target

    def test_death_copies_lki_kinds_then_shuffles_the_distinct_owner_even_if_source_left(self):
        counters = {"+1/+1": 2, "charge": 3, "stun": 1, "flying": 1}
        for exile_source in (False, True):
            with self.subTest(exile_source=exile_source):
                game, source, target = self._death_stack(61343330 + exile_source, counters, exile_source=exile_source)
                game = self.replay(game, load=True)
                shuffled = []
                original = type(game.engine).shuffle_library

                def observe_shuffle(host, player, **kwargs):
                    shuffled.append(player)
                    return original(host, player, **kwargs)

                with patch.object(type(game.engine), "shuffle_library", observe_shuffle):
                    self.assertIsNone(self.resolve(game))
                self.assertEqual(["A"], shuffled)
                self.assertEqual(counters, game.state.cards[target.object_id].counters)
                self.assertEqual("exile" if exile_source else "library", game.state.cards[source.object_id].zone)
                self.replay(game, load=True)

    def test_death_with_illegal_target_keeps_source_and_skips_shuffle(self):
        game, source, target = self._death_stack(61343333, {"charge": 2}, destroy_target=True)
        owner_library = tuple(game.state.players["A"].zones["library"])
        with patch.object(type(game.engine), "shuffle_library", side_effect=AssertionError("Illegal target shuffled")):
            self.assertIsNone(self.resolve(game))
        self.assertEqual("graveyard", game.state.cards[source.object_id].zone)
        self.assertEqual({}, game.state.cards[target.object_id].counters)
        self.assertEqual(owner_library, tuple(game.state.players["A"].zones["library"]))
        self.replay(game, load=True)
