// Starship Duel — C++ bot SDK (single header).
//
// Requires nlohmann/json (header-only): https://github.com/nlohmann/json
// Put json.hpp on your include path, then:
//
//     #include "starship_bot.hpp"
//     using nlohmann::json;
//
//     json decide(const json& req) {
//         if (req["rival"]["known_position"] == req["you"]["position"])
//             return json{{"action", "FIRE"}};
//         return req["legal_actions"][0];   // any legal action
//     }
//     int main() { return starship::run(decide); }
//
// Build: g++ -std=c++17 -O2 example_bot.cpp -o example_bot
// Run against the engine:
//     python -m starship_duel.run --bot0 heuristic --bot1 "cmd:./example_bot"
//
// The harness reads one JSON request per line from stdin and writes one JSON
// action per line to stdout. The process persists for the whole game, so keep
// any per-game memory (belief, opponent model) in your own globals.
//
// request: turn, your_id, map_id, domination_target,
//   you{position,cloaked,deep_cloak_turns_left,energy,banked_overcharge,
//       actions_remaining,unlocked,lives,domination},
//   rival{known_position,last_seen,moves_since_seen,unlocked,last_action,
//         lives,domination},
//   map{adjacency,binary_systems},
//   systems{name:{owner,owner_known,status,binary,cache,collapse_in}},
//   campaign_score, skirmish, legal_actions[{action,target?}]
// reply: one legal action, e.g. {{"action","JUMP"},{"target","Veyra"}}, or
//   {{"index", k}} to pick legal_actions[k]. `target` is honoured for JUMP only.
//
// Both win races are public: you/rival each carry `lives` and `domination`
// (map-control points, first to `domination_target` wins). owner_known is false
// where you have never sensed the system -- `owner` is unknown, not unowned.

#ifndef STARSHIP_BOT_HPP
#define STARSHIP_BOT_HPP

#include <functional>
#include <iostream>
#include <string>
#include <nlohmann/json.hpp>

namespace starship {

// decide: (request json) -> action json, e.g. {{"action","JUMP"},{"target","Veyra"}}
//
// NOTE: an unhandled exception in decide() is a runtime error — the process
// exits non-zero and the engine scores it as an automatic LOSS. Handle your own
// errors (return a valid action) if you want to survive them.
inline int run(const std::function<nlohmann::json(const nlohmann::json&)>& decide) {
    std::ios::sync_with_stdio(false);
    std::string line;
    while (std::getline(std::cin, line)) {
        if (line.empty()) continue;
        nlohmann::json request = nlohmann::json::parse(line);
        nlohmann::json action = decide(request);
        std::cout << action.dump() << "\n" << std::flush;
    }
    return 0;
}

}  // namespace starship

#endif  // STARSHIP_BOT_HPP
