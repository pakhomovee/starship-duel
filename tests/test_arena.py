"""Arena tests: the JSON protocol and the refereed subprocess bot."""

import os
import sys
import tempfile
import textwrap
import unittest

from starship_duel.arena.protocol import encode_request, parse_reply
from starship_duel.arena.subprocess_bot import SubprocessBot
from starship_duel.bots import make_bot
from starship_duel.env import StarshipDuelEnv
from starship_duel.game import Action, ActionType, build_observation
from starship_duel.run import play_skirmish

_EXAMPLE = os.path.join(
    os.path.dirname(os.path.dirname(__file__)),
    "starship_duel", "arena", "sdk", "python", "example_bot.py",
)


def _obs():
    env = StarshipDuelEnv(seed=0)
    env.reset(map_id="reference", first_ship=0)
    return build_observation(env.engine, env.engine.current_ship)


def _script(body: str) -> str:
    f = tempfile.NamedTemporaryFile("w", suffix=".py", delete=False)
    f.write(textwrap.dedent(body))
    f.close()
    return f.name


class TestProtocol(unittest.TestCase):
    def test_request_has_expected_shape(self):
        req = encode_request(_obs())
        for key in ("type", "turn", "you", "rival", "map", "systems", "legal_actions"):
            self.assertIn(key, req)
        self.assertTrue(all("action" in a for a in req["legal_actions"]))

    def test_parse_reply_by_name_and_index(self):
        obs = _obs()
        legal = obs.legal_actions
        first = legal[0]
        wire = {"action": first.type.name}
        if first.dest:
            wire["target"] = first.dest
        self.assertEqual(parse_reply(wire, legal), first)
        self.assertEqual(parse_reply({"index": 0}, legal), first)

    def test_parse_reply_rejects_garbage(self):
        legal = _obs().legal_actions
        self.assertIsNone(parse_reply({"action": "NONSENSE"}, legal))
        self.assertIsNone(parse_reply({"index": 999}, legal))
        self.assertIsNone(parse_reply("not a dict", legal))
        # a well-formed but illegal-here action (JUMP to a non-neighbour)
        self.assertIsNone(parse_reply({"action": "JUMP", "target": "___nowhere___"}, legal))


class TestSubprocessBot(unittest.TestCase):
    def test_example_bot_plays_a_full_game(self):
        bot = SubprocessBot([sys.executable, _EXAMPLE], timeout=5.0)
        res = play_skirmish(make_bot("heuristic"), bot, seed=1)
        self.assertTrue(res["end_reason"])
        self.assertEqual(bot.strikes, 0)  # well-behaved bot never strikes

    def test_timeout_falls_back_safely(self):
        # A bot that never replies -> every move times out, but the game still
        # finishes (engine substitutes a default and records strikes).
        sleeper = _script("import time\nwhile True: time.sleep(10)\n")
        bot = SubprocessBot([sys.executable, sleeper], timeout=0.25)
        res = play_skirmish(make_bot("heuristic"), bot, seed=2)
        self.assertTrue(res["end_reason"])
        self.assertGreater(bot.strikes, 0)
        os.unlink(sleeper)

    def test_malformed_output_falls_back_safely(self):
        garbage = _script("""
            import sys
            for line in sys.stdin:
                print("not json", flush=True)
        """)
        bot = SubprocessBot([sys.executable, garbage], timeout=2.0)
        res = play_skirmish(make_bot("heuristic"), bot, seed=3)
        self.assertTrue(res["end_reason"])
        self.assertGreater(bot.strikes, 0)
        os.unlink(garbage)

    def test_crashing_bot_forfeits(self):
        # A bot whose process dies (runtime error) automatically LOSES.
        crasher = _script("""
            import sys
            for line in sys.stdin:
                raise RuntimeError("boom")
        """)
        bot = SubprocessBot([sys.executable, crasher], timeout=3.0)
        res = play_skirmish(make_bot("heuristic"), bot, seed=1)
        self.assertEqual(res["end_reason"], "crash")
        self.assertEqual(res["winner"], 0)  # the heuristic (ship 0) wins
        os.unlink(crasher)


if __name__ == "__main__":
    unittest.main()
