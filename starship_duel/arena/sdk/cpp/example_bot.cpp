// Example Starship Duel bot (C++). Build & run:
//
//   g++ -std=c++17 -O2 example_bot.cpp -o example_bot        # needs nlohmann/json
//   python -m starship_duel.run --bot0 heuristic --bot1 "cmd:./example_bot"
//
// Simple hunt-and-hide strategy, mirroring the Python example.

#include "starship_bot.hpp"
using nlohmann::json;

// Per-game memory persists for the whole process.
static std::string g_last_known;

static bool has_action(const json& legal, const std::string& name) {
    for (const auto& a : legal)
        if (a["action"] == name) return true;
    return false;
}

json decide(const json& req) {
    const auto& me = req["you"];
    const auto& rival = req["rival"];
    const auto& legal = req["legal_actions"];
    const auto& systems = req["systems"];
    std::string pos = me["position"].get<std::string>();

    std::string known = rival["known_position"].is_string()
                        ? rival["known_position"].get<std::string>() : "";
    if (!known.empty()) g_last_known = known;

    // 1) Kill if the rival is confirmed in our system.
    if (known == pos && has_action(legal, "FIRE"))
        return json{{"action", "FIRE"}};

    // 2) Rival known adjacent + enough actions -> jump on (fire next).
    if (!known.empty() && me["actions_remaining"].get<int>() >= 2)
        for (const auto& a : legal)
            if (a["action"] == "JUMP" && a.value("target", "") == known)
                return a;

    // 3) Exposed -> re-cloak.
    if (!me["cloaked"].get<bool>() && has_action(legal, "HOLD"))
        return json{{"action", "HOLD"}};

    // 4) Standing on an unclaimed binary -> claim it.
    if (has_action(legal, "CLAIM") && systems.contains(pos)
        && systems[pos].value("binary", false) && systems[pos]["owner"].is_null())
        return json{{"action", "CLAIM"}};

    // 5) Move toward a stable binary, else any stable system.
    const json* fallback = nullptr;
    for (const auto& a : legal) {
        if (a["action"] != "JUMP") continue;
        std::string tgt = a.value("target", "");
        if (!systems.contains(tgt) || systems[tgt].value("status", "") != "STABLE") continue;
        if (systems[tgt].value("binary", false)) return a;
        if (!fallback) fallback = &a;
    }
    if (fallback) return *fallback;

    return json{{"action", has_action(legal, "HOLD") ? "HOLD" : "END_TURN"}};
}

int main() { return starship::run(decide); }
