"use strict";
/* Tiny client-side i18n for the whole site (play / rules / tournament).
 *
 * Static markup is tagged with data-i18n[-html|-title|-ph|-aria] attributes and
 * translated in place; dynamic strings go through t(). Server-produced text
 * (engine log lines, action labels, disabled reasons) is left in English by
 * the backend and rewritten here — see tEvent() / tReason() / actLabel().
 *
 * Strings live in one table: STR[key] = [english, russian]. Switching the
 * language never reloads the page — listeners re-render in place. */

(function () {
const LANGS = ["en", "ru"];
const STORE_KEY = "sd_lang";

const STR = {
  // ---- chrome / navigation -------------------------------------------------
  "lang.en": ["EN", "EN"],
  "lang.ru": ["RU", "RU"],
  "lang.switch": ["Language", "Язык"],

  "nav.play": ["▸ Play", "▸ Играть"],
  "nav.play_back": ["◀ Play", "◀ Играть"],
  "nav.tournament": ["Tournament ♛", "Турнир ♛"],
  "nav.tournament_t": ["Bot ladder & leaderboard", "Лига ботов и таблица лидеров"],
  "nav.rules": ["Rules ?", "Правила ?"],
  "nav.rules_t": ["How to play — the field guide", "Как играть — полевой справочник"],
  "nav.play_t": ["Play a game", "Сыграть партию"],

  // ---- index.html ----------------------------------------------------------
  "index.title": ["Starship Duel", "Starship Duel"],
  "index.kicker": ["Neon Constellation", "Неоновое созвездие"],
  "index.seed": ["Seed", "Сид"],
  "index.seed_ph": ["random", "любой"],
  "index.new_game": ["New Game", "Новая игра"],
  "index.games": ["Games ▤", "Игры ▤"],
  "index.games_t": ["Browse & replay past games", "Просмотр и повтор прошлых партий"],
  "index.reset": ["Reset", "Сброс"],
  "bots.builtin": ["Built-in", "Встроенные"],
  "bots.arena": ["Arena (external)", "Арена (внешние)"],

  "games.title": ["Past games", "Прошлые партии"],
  "games.lead": ["Every finished skirmish is recorded. Pick one to watch its replay.",
    "Каждая завершённая схватка записывается. Выберите любую, чтобы посмотреть повтор."],
  "games.close": ["Close", "Закрыть"],
  "games.loading": ["Loading…", "Загрузка…"],
  "games.load_fail": ["Could not load games.", "Не удалось загрузить партии."],
  "games.empty": ["No games recorded yet — finish a skirmish and it'll show up here.",
    "Записей пока нет — завершите схватку, и она появится здесь."],
  "games.watch": ["Watch ▶", "Смотреть ▶"],
  "games.delete": ["Delete", "Удалить"],
  "games.plies": ["{n} plies", "ходов: {n}"],
  "games.won": ["P{n} won", "Победил И{n}"],
  "games.draw": ["Draw", "Ничья"],
  "games.replay_fail": ["Could not load replay.", "Не удалось загрузить повтор."],
  "games.replay_empty": ["Empty replay.", "Пустой повтор."],

  "watch.step": ["Step ▷", "Шаг ▷"],
  "watch.play": ["Auto ▶", "Авто ▶"],
  "watch.pause": ["Stop ⏸", "Стоп ⏸"],
  "watch.speed": ["Speed", "Скорость"],
  "watch.persp_t": ["Watch through a ship's fog of war", "Смотреть через туман войны одного корабля"],
  "watch.truth": ["Truth", "Всё"],
  "watch.p1view": ["P1 view", "Взгляд И1"],
  "watch.p2view": ["P2 view", "Взгляд И2"],

  "replay.tag": ["REPLAY", "ПОВТОР"],
  "replay.prev": ["Previous action", "Предыдущее действие"],
  "replay.next": ["Next action", "Следующее действие"],
  "replay.play": ["Play ▶", "Пуск ▶"],
  "replay.pause": ["Pause ⏸", "Пауза ⏸"],
  "replay.exit": ["✕ Exit", "✕ Выход"],

  "panel.ships": ["Ships", "Корабли"],
  "panel.your_move": ["Your move", "Ваш ход"],
  "panel.log": ["Log", "Журнал"],

  "hud.player": ["Player {n}", "Игрок {n}"],
  "hud.cloaked": ["· cloaked", "· под маскировкой"],
  "hud.exposed": ["· EXPOSED", "· ОБНАРУЖЕН"],
  "hud.rival_public": ["· rival (public intel)", "· соперник (открытые данные)"],
  "hud.at": ["at {pos}", "в {pos}"],
  "hud.hidden": ["hidden", "скрыт"],
  "hud.act": ["act", "действ."],
  "hud.banked": ["banked", "в запасе"],
  "hud.control": ["CONTROL", "КОНТРОЛЬ"],
  "hud.control_t": ["Map control — first to {n} wins", "Контроль карты — победа при {n}"],
  "hud.lives_t": ["Lives — lose all and you're eliminated", "Жизни — потеряете все и выбываете"],

  "mode.human_vs_bot": ["Human vs Bot", "Человек против бота"],
  "mode.bot_vs_bot": ["Bot vs Bot", "Бот против бота"],
  "mode.human_vs_human": ["Hotseat", "Два игрока"],

  "chip.over": ["Skirmish over", "Схватка окончена"],
  "chip.to_move": ["P{n} to move · turn {t}", "Ходит И{n} · ход {t}"],

  "hint.over": ["Skirmish over — start a new game.", "Схватка окончена — начните новую партию."],
  "hint.bot_turn": ["{who}'s turn — press Step to watch one action, or Auto to run it.",
    "Ход {who} — нажмите «Шаг», чтобы увидеть одно действие, или «Авто»."],
  "hint.tip": ["Tip: glowing systems are jump targets — click them on the map too.",
    "Подсказка: светящиеся системы — цели прыжка, по ним можно кликать прямо на карте."],

  "banner.draw": ["Draw — {reason}", "Ничья — {reason}"],
  "banner.win": ["Player {n} wins — {reason}", "Игрок {n} побеждает — {reason}"],

  "tip.cost": ["Cost: {n}⚡", "Цена: {n}⚡"],
  "tip.unavailable": ["Unavailable — {reason}", "Недоступно — {reason}"],
  "err.prefix": ["Error: ", "Ошибка: "],

  // ---- action names (mirror serialize._LABELS) ------------------------------
  "act.JUMP": ["Jump", "Прыжок"],
  "act.HOLD": ["Hold", "Затаиться"],
  "act.CLAIM": ["Claim", "Захват"],
  "act.FIRE": ["Fire", "Огонь"],
  "act.SCAN": ["Scan", "Скан"],
  "act.DEEP_CLOAK": ["Deep Cloak", "Глубокая маскировка"],
  "act.OVERCHARGE": ["Overcharge", "Перезарядка"],
  "act.UNLOCK_PROXIMITY_ALERT": ["Unlock: Proximity Alert", "Открыть: датчик сближения"],
  "act.UNLOCK_LONG_RANGE_SCANNERS": ["Unlock: Long-Range Scanners", "Открыть: дальние сканеры"],
  "act.UNLOCK_JAMMING": ["Unlock: Jamming", "Открыть: глушение"],
  "act.END_TURN": ["End Turn", "Конец хода"],
  "act.SNIPE": ["Snipe", "Выстрел издали"],

  // ---- action descriptions (tooltips) --------------------------------------
  "desc.JUMP": ["Move to an adjacent system. Entering a rival-claimed or rival-occupied system exposes your position.",
    "Переместиться в соседнюю систему. Вход в систему, захваченную или занятую соперником, раскрывает вашу позицию."],
  "desc.HOLD": ["Stay put and slip back under cloak — the way to disappear again after being spotted.",
    "Остаться на месте и снова уйти под маскировку — способ исчезнуть после того, как вас заметили."],
  "desc.CLAIM": ["Take the system you're standing on for income (binaries pay more) — but claiming exposes your position.",
    "Забрать систему, в которой вы находитесь, ради дохода (двойные звёзды дают больше) — но захват раскрывает вашу позицию."],
  "desc.FIRE": ["Raid your current system (costs 3⚡, charged hit or miss). On a hit — the rival is here — steal their control points, capture the ground under them, and cost them a life. A deep-cloaked rival is immune.",
    "Рейд по вашей текущей системе (3⚡, списываются и при промахе). При попадании — соперник здесь — вы крадёте его очки контроля, захватываете систему под ним и отнимаете жизнь. Соперник под глубокой маскировкой неуязвим."],
  "desc.SCAN": ["Free: sweep the ownership map and pin the rival's exact system — unless they are deep-cloaked.",
    "Бесплатно: просканировать карту владений и точно определить систему соперника — если он не под глубокой маскировкой."],
  "desc.DEEP_CLOAK": ["Spend Energy to become undetectable for 2 turns — Scan and Long-Range Scanners sweep right past you and you can't be raided or lose a life (a point-blank Proximity Alert still catches you).",
    "Потратить энергию и стать необнаружимым на 2 хода — «Скан» и дальние сканеры проходят мимо, вас нельзя атаковать и вы не теряете жизнь (датчик сближения в упор всё же засечёт)."],
  "desc.OVERCHARGE": ["Spend Energy to bank +1 extra action for next turn (stacks).",
    "Потратить энергию, чтобы отложить +1 действие на следующий ход (складывается)."],
  "desc.UNLOCK_PROXIMITY_ALERT": ["Permanent unlock (defensive): a short-range alarm — the rival is revealed (piercing cloak) when they move onto or beside your ship — plus a shield that stops a raid from capturing your territory.",
    "Постоянное улучшение (защита): ближняя тревога — соперник раскрывается (даже под маскировкой), когда входит в вашу систему или соседнюю, — плюс щит, не дающий рейду захватить вашу территорию."],
  "desc.UNLOCK_LONG_RANGE_SCANNERS": ["Permanent unlock (offensive): passively track the rival's exact system while they're within 2 hops, see ownership 2 hops out, and Fire can raid a rival one hop away.",
    "Постоянное улучшение (атака): пассивно отслеживать точную систему соперника в радиусе 2 переходов, видеть владения на 2 перехода вокруг, а «Огонь» достаёт соперника через один переход."],
  "desc.UNLOCK_JAMMING": ["Permanent unlock: your Energy-spending actions show to the rival only as a generic “JAMMED”, and it blinds their Proximity Alert.",
    "Постоянное улучшение: ваши энергозатратные действия видны сопернику только как безликое «JAMMED», а его датчик сближения слепнет."],
  "desc.END_TURN": ["End your turn now. Any actions beyond the base 2 left unspent are banked for next turn.",
    "Завершить ход сейчас. Неиспользованные действия сверх базовых двух переносятся на следующий ход."],
  "desc.SNIPE": ["Long-Range Scanners let this Fire reach the rival one hop away at {pos} — steal control points, capture the ground, and cost them a life without co-locating.",
    "Дальние сканеры позволяют этому выстрелу достать соперника через один переход, в {pos} — украсть очки контроля, захватить систему и отнять жизнь, не входя к нему."],

  // ---- tournament.html -----------------------------------------------------
  "t.title": ["Starship Duel — Tournament", "Starship Duel — Турнир"],
  "t.kicker": ["Bot Ladder", "Лига ботов"],
  "t.h1": ["Starship Duel — Tournament", "Starship Duel — Турнир"],
  "t.standings": ["Standings", "Таблица"],
  "t.scope_quick": ["Live (partial)", "Live (частичная)"],
  "t.scope_full": ["Final (all-pairs)", "Финальная (все пары)"],
  "t.loading": ["Loading…", "Загрузка…"],
  "t.updated": ["updated {when}", "обновлено {when}"],
  "t.not_computed": ["not computed yet", "ещё не рассчитано"],
  "t.no_matches": ["No matches scored yet.", "Пока нет засчитанных матчей."],
  "t.standings_fail": ["Could not load standings: {err}", "Не удалось загрузить таблицу: {err}"],
  "t.col_rank": ["#", "#"],
  "t.col_competitor": ["Competitor", "Участник"],
  "t.col_score": ["Score", "Рейтинг"],
  "t.col_ci": ["90% CI", "90% ДИ"],
  "t.col_wl": ["W–L", "П–П"],
  "t.col_games": ["Games", "Партии"],
  "t.baseline": ["baseline", "эталон"],
  "t.unranked": ["Not yet ranked", "Пока без места"],
  "t.why_errored": ["{n} match(es) failed to run", "матчей не запустилось: {n}"],
  "t.why_drawn": ["{n} played, no decisive result yet", "сыграно {n}, решающего результата нет"],
  "t.why_pending": ["{n} match(es) queued", "матчей в очереди: {n}"],
  "t.why_none": ["no matches yet", "матчей пока нет"],
  "t.wld": ["{w}–{l}–{d} (W–L–D)", "{w}–{l}–{d} (П–П–Н)"],
  "t.error_prefix": ["error: {msg}", "ошибка: {msg}"],

  "t.your_bot": ["Your bot", "Ваш бот"],
  "t.submit_lead": ["Upload a single-file bot — <code>.py</code> (Python) or <code>.cpp</code> (C++, compiled on the server). It's scanned, smoke-tested against <b>random</b>, and — if it passes — becomes your active entry. Use the bundled SDK (<code>starship_sdk</code> / <code>starship_bot.hpp</code>) or speak the JSON protocol directly.",
    "Загрузите бота одним файлом — <code>.py</code> (Python) или <code>.cpp</code> (C++, компилируется на сервере). Файл проверяется, прогоняется тестовой партией против <b>random</b> и, если проходит, становится вашей активной заявкой. Используйте встроенный SDK (<code>starship_sdk</code> / <code>starship_bot.hpp</code>) или говорите на JSON-протоколе напрямую."],
  "t.submit_btn": ["Submit bot", "Отправить бота"],
  "t.no_subs": ["No submissions yet.", "Заявок пока нет."],
  "t.choose_file": ["Choose a .py file first.", "Сначала выберите файл .py."],
  "t.validating": ["Validating…", "Проверка…"],
  "t.upload_failed": ["upload failed", "не удалось загрузить"],
  "t.status_validated": ["validated", "принят"],
  "t.status_rejected": ["rejected", "отклонён"],
  "t.status_pending": ["pending", "в очереди"],
  "t.active_suffix": [" · active", " · активный"],
  "t.queued_eval": [" — {n} evaluation matches queued; standings update as they finish.",
    " — в очередь поставлено матчей: {n}; таблица обновится по мере их завершения."],
  "t.queued_busy": [" — the match queue is busy; your games will be scheduled shortly.",
    " — очередь матчей загружена; ваши игры будут запланированы чуть позже."],
  "t.ci_stale_t": ["Interval from the last full recompute — scores and ranks above are current.",
    "Интервал из последнего полного пересчёта — очки и места выше актуальны."],
  "t.ci_none_t": ["No interval yet: this competitor hasn't been through a full recompute.",
    "Интервала пока нет: этот участник ещё не проходил полный пересчёт."],

  "t.login": ["Log in", "Войти"],
  "t.logout": ["Log out", "Выйти"],
  "t.username": ["username", "имя пользователя"],
  "t.password": ["password", "пароль"],
  "t.login_failed": ["login failed", "не удалось войти"],
  "t.admin": ["admin", "админ"],

  "t.admin_h": ["Admin", "Администрирование"],
  "t.create_user": ["Create user", "Создать пользователя"],
  "t.username_t": ["Login name for the new account (must be unique).",
    "Логин нового аккаунта (должен быть уникальным)."],
  "t.password_t": ["Initial password for the new account.", "Начальный пароль нового аккаунта."],
  "t.admin_chk_t": ["Grant this user admin rights (tournament control, user management, all submissions).",
    "Выдать права администратора (управление турниром, пользователями, все заявки)."],
  "t.create": ["Create", "Создать"],
  "t.create_t": ["Create the account with the name and password above.",
    "Создать аккаунт с указанными именем и паролем."],
  "t.created": ["created", "создан"],
  "t.failed": ["failed", "ошибка"],

  "t.control": ["Tournament control", "Управление турниром"],
  "t.games_each": ["Games each", "Партий на пару"],
  "t.games_each_t": ["How many games to schedule per pairing (split evenly between who moves first).",
    "Сколько партий планировать на пару (поровну по тому, кто ходит первым)."],
  "t.sched_base": ["Schedule vs baselines", "Запланировать против эталонов"],
  "t.sched_base_t": ["Queue matches of every active bot against the reference baselines (random, heuristic, hunter, uppo…). This is the cheap, continuous ladder that drives the Live standings. Idempotent: only tops each pairing up to “Games each”.",
    "Поставить в очередь матчи каждого активного бота против эталонов (random, heuristic, hunter, uppo…). Это дешёвая непрерывная лига, питающая таблицу Live. Идемпотентно: только добирает пары до «Партий на пару»."],
  "t.sched_full": ["Schedule full round-robin", "Запланировать полный круг"],
  "t.sched_full_t": ["Queue the full all-pairs round-robin between every active bot — the heavier post-deadline evaluation behind the Final standings. Idempotent: only adds the missing games.",
    "Поставить в очередь полный круговой турнир между всеми активными ботами — тяжёлая оценка после дедлайна, стоящая за финальной таблицей. Идемпотентно: добавляет только недостающие партии."],
  "t.recompute_quick": ["Recompute live", "Пересчитать live"],
  "t.recompute_quick_t": ["Re-fit the Bradley-Terry ratings over all finished matches and refresh the Live (partial) standings snapshot. Does not run any matches.",
    "Пересчитать рейтинги Брэдли–Терри по всем завершённым матчам и обновить снимок таблицы Live. Матчи не запускаются."],
  "t.recompute_full": ["Recompute final", "Пересчитать финальную"],
  "t.recompute_full_t": ["Re-fit ratings and refresh the Final (all-pairs) standings snapshot. Does not run any matches.",
    "Пересчитать рейтинги и обновить снимок финальной таблицы (все пары). Матчи не запускаются."],
  "t.queue_t": ["Current match-queue state. “error” means matches that failed to run — check the “Not yet ranked” list in Standings for the reason.",
    "Состояние очереди матчей. «ошибок» — матчи, которые не запустились; причину смотрите в списке «Пока без места»."],
  "t.queue": ["queue: {pending} pending · {running} running · {done} done · {error} error",
    "очередь: {pending} в ожидании · {running} идут · {done} готово · {error} ошибок"],
  "t.all_subs": ["All submissions", "Все заявки"],
  "t.view_code": ["View code", "Показать код"],
  "t.prev": ["← Newer", "← Новее"],
  "t.next": ["Older →", "Старее →"],
  "t.subs_range": ["{first}–{last} of {total}", "{first}–{last} из {total}"],
  "t.copy": ["Copy", "Копировать"],
  "t.copied": ["Copied", "Скопировано"],
  "t.close": ["Close", "Закрыть"],
  "t.sched_base_ok": ["scheduled {n} baseline matches", "запланировано матчей с эталонами: {n}"],
  "t.sched_full_ok": ["scheduled {n} round-robin matches", "запланировано матчей кругового турнира: {n}"],
  "t.recomputed_quick": ["recomputed live ({n} ranked)", "live пересчитан (в рейтинге: {n})"],
  "t.recomputed_full": ["recomputed final ({n} ranked)", "финальная пересчитана (в рейтинге: {n})"],
  "t.working": ["…", "…"],

  // ---- protocol.html -------------------------------------------------------
  "nav.protocol": ["Protocol ⌘", "Протокол ⌘"],
  "nav.protocol_t": ["Bot protocol reference — request/reply JSON",
    "Справочник протокола ботов — JSON запроса и ответа"],
  "t.protocol_link": ["→ Full bot protocol reference (request/reply JSON, time limits)",
    "→ Полный справочник протокола (JSON запроса и ответа, лимиты времени)"],

  "p.title": ["Starship Duel — Bot Protocol", "Starship Duel — Протокол ботов"],
  "p.kicker": ["Bot Protocol", "Протокол ботов"],
  "p.hero_kicker": ["One JSON object per line", "По одному JSON-объекту в строке"],
  "p.hero_h1": ["Write a bot in any language", "Пишите бота на любом языке"],
  "p.hero_lede": ["Your bot is a plain program. The engine writes one JSON request to its <b>stdin</b> per action and reads one JSON reply from its <b>stdout</b>. That's the whole interface — no framework, no network, no dependencies.",
    "Ваш бот — обычная программа. Движок пишет по одному JSON-запросу в её <b>stdin</b> на каждое действие и читает по одному JSON-ответу из <b>stdout</b>. Это весь интерфейс — ни фреймворка, ни сети, ни зависимостей."],
  "p.tag_lines": ["📨 Line-delimited JSON", "📨 JSON построчно"],
  "p.tag_tl": ["⏱ 2 s per action", "⏱ 2 с на действие"],
  "p.tag_langs": ["🐍 Python or C++", "🐍 Python или C++"],
  "p.tag_legal": ["✅ Legal moves precomputed", "✅ Легальные ходы уже посчитаны"],

  "p.s1_num": ["01 · The loop", "01 · Цикл"],
  "p.s1_h": ["How a match drives your process", "Как матч управляет вашим процессом"],
  "p.s1_p": ["Your program is started once and stays alive for the whole game, so it can keep its own memory between decisions.",
    "Ваша программа запускается один раз и живёт всю партию, поэтому может хранить собственную память между решениями."],
  "p.life_h": ["Lifecycle", "Жизненный цикл"],
  "p.life_1": ["<b>Spawned once per game.</b> Globals, belief state and opponent models persist across every request.",
    "<b>Запуск один раз за партию.</b> Глобальные переменные, модель убеждений и модель соперника сохраняются между запросами."],
  "p.life_2": ["<b>One request per action</b> — not per turn. A turn is at least 2 actions, so you are asked at least twice per turn.",
    "<b>Один запрос на действие</b>, а не на ход. В ходе минимум 2 действия, значит вас спросят минимум дважды за ход."],
  "p.life_3": ["<b>Read a line, write a line.</b> Always <code>flush</code> after writing, or the engine will see nothing and time out.",
    "<b>Прочитали строку — написали строку.</b> Всегда делайте <code>flush</code> после записи, иначе движок ничего не увидит и получит таймаут."],
  "p.life_4": ["<b>stderr is free.</b> It is not parsed, so use it for logging.",
    "<b>stderr свободен.</b> Он не разбирается, пишите туда логи."],
  "p.turn_h": ["A turn, concretely", "Ход на конкретном примере"],
  "p.turn_p": ["You start a turn with <b>2 actions</b> (plus any banked with Overcharge). Each one is a separate request/reply round-trip. The turn ends when you spend them all or reply <code>END_TURN</code>:",
    "Ход начинается с <b>2 действий</b> (плюс отложенные перезарядкой). Каждое — отдельная пара «запрос/ответ». Ход заканчивается, когда вы потратите все или ответите <code>END_TURN</code>:"],

  "p.s2_num": ["02 · Limits", "02 · Лимиты"],
  "p.s2_h": ["Time limit and failure handling", "Лимит времени и обработка ошибок"],
  "p.s2_p": ["Being slow costs you a move. Crashing costs you the game.",
    "Медлительность стоит хода. Падение стоит партии."],
  "p.tl_h": ["2 seconds per action", "2 секунды на действие"],
  "p.tl_p": ["The limit is <b>2 s of wall-clock per action</b>, measured from the moment the request is written to the moment your reply line arrives. It is <b>not</b> a per-turn or per-game budget — every action gets its own fresh 2 s.",
    "Лимит — <b>2 с реального времени на действие</b>, от момента отправки запроса до прихода строки с вашим ответом. Это <b>не</b> бюджет на ход или на партию: у каждого действия свои свежие 2 с."],
  "p.tl_note": ["<b>Watch your startup.</b> The first request is written immediately after your process launches, so heavy imports or model loading eat into the <b>first action's</b> 2 s. Load lazily, or keep startup well under a second.",
    "<b>Следите за запуском.</b> Первый запрос отправляется сразу после старта процесса, поэтому тяжёлые импорты или загрузка модели съедают 2 с <b>первого действия</b>. Загружайте лениво или укладывайте старт заметно меньше чем в секунду."],
  "p.fail_h": ["What happens when things go wrong", "Что происходит при сбоях"],
  "p.fail_col1": ["Event", "Событие"],
  "p.fail_col2": ["Consequence", "Последствие"],
  "p.fail_slow": ["Reply takes longer than 2 s", "Ответ идёт дольше 2 с"],
  "p.fail_slow_r": ["<b>Strike.</b> The engine plays <code>END_TURN</code> for you (or the first legal action) and the match continues.",
    "<b>Штрафной балл.</b> Движок сыграет за вас <code>END_TURN</code> (или первое легальное действие), матч продолжается."],
  "p.fail_json": ["Reply is not valid JSON", "Ответ — не валидный JSON"],
  "p.fail_json_r": ["<b>Strike</b> — same substitution.", "<b>Штрафной балл</b> — та же подстановка."],
  "p.fail_illegal": ["Reply is an unknown or currently illegal action",
    "Ответ — неизвестное или сейчас нелегальное действие"],
  "p.fail_illegal_r": ["<b>Strike</b> — same substitution.", "<b>Штрафной балл</b> — та же подстановка."],
  "p.fail_crash": ["Process exits, crashes, or closes stdout",
    "Процесс завершается, падает или закрывает stdout"],
  "p.fail_crash_r": ["<b>Immediate loss.</b> An uncaught exception is a forfeit — catch your own errors and return a legal action instead.",
    "<b>Немедленное поражение.</b> Необработанное исключение — это проигрыш: ловите свои ошибки и возвращайте легальное действие."],
  "p.strike_note": ["Strikes are logged but <b>never</b> end a live match, however many you collect — you simply keep losing moves. They only matter at upload, where a bot that misbehaves on more than half its moves is rejected.",
    "Штрафные баллы логируются, но <b>никогда</b> не прерывают идущий матч, сколько бы их ни набралось — вы просто теряете ходы. Они важны только при загрузке: бот, сбоящий более чем на половине ходов, отклоняется."],

  "p.s3_num": ["03 · Input", "03 · Вход"],
  "p.s3_h": ["The request (engine → bot)", "Запрос (движок → бот)"],
  "p.s3_p": ["One object per line, on stdin. Everything you are entitled to know is here — never more.",
    "По объекту в строке, в stdin. Здесь всё, что вам положено знать, — и ничего сверх того."],
  "p.you_h": ["Your ship — private", "Ваш корабль — личные данные"],
  "p.rival_h": ["The rival — public only", "Соперник — только открытые данные"],
  "p.col_field": ["Field", "Поле"],
  "p.col_meaning": ["Meaning", "Значение"],
  "p.f_position": ["The system you are in.", "Система, в которой вы находитесь."],
  "p.f_cloaked": ["Whether your position is currently hidden from the rival.",
    "Скрыта ли сейчас ваша позиция от соперника."],
  "p.f_dcl": ["Turns of Deep Cloak protection remaining; 0 when not deep-cloaked.",
    "Сколько ходов действует глубокая маскировка; 0 — если она не включена."],
  "p.f_energy": ["Spendable energy.", "Доступная энергия."],
  "p.f_banked": ["Extra actions saved for later turns.", "Отложенные действия на будущие ходы."],
  "p.f_actions": ["Actions left in this turn, including this one.",
    "Сколько действий осталось в этом ходе, включая текущее."],
  "p.f_lives": ["Lives left. At 0 you are eliminated.", "Осталось жизней. При 0 вы выбываете."],
  "p.f_dom": ["Your control points, raced to <code>domination_target</code>.",
    "Ваши очки контроля в гонке до <code>domination_target</code>."],
  "p.f_unlocked": ["Which permanent upgrades you own.", "Какие постоянные улучшения у вас есть."],
  "p.f_known": ["Their exact system, but only while it is known for certain. Otherwise <code>null</code>.",
    "Их точная система — но только пока она известна достоверно. Иначе <code>null</code>."],
  "p.f_lastseen": ["The last system they were confirmed in, or <code>null</code> if never.",
    "Последняя система, где они были достоверно замечены, или <code>null</code>."],
  "p.f_msince": ["Upper bound on single hops they could have made since. 0 while currently known.",
    "Верхняя оценка числа переходов, которые они могли сделать с тех пор. 0, пока позиция известна."],
  "p.f_lastturn": ["Every action of their last turn, in order. Ones you could not identify read <code>UNKNOWN</code> (they were hidden) or <code>JAMMED</code> (masked by their Jamming) — so the length still tells you how many actions they spent.",
    "Все действия их прошлого хода по порядку. Те, что вы не смогли опознать, показаны как <code>UNKNOWN</code> (они были скрыты) или <code>JAMMED</code> (замаскировано их глушением) — длина списка всё равно говорит, сколько действий они потратили."],
  "p.f_lastact": ["The final entry of <code>last_turn_actions</code>, for convenience.",
    "Последний элемент <code>last_turn_actions</code>, для удобства."],
  "p.f_rlives": ["Their remaining lives.", "Сколько жизней осталось у них."],
  "p.f_rdom": ["Their control points. Both scores are public.",
    "Их очки контроля. Оба счёта открыты."],
  "p.f_runlocked": ["Which upgrades they have bought.", "Какие улучшения они купили."],
  "p.rival_note": ["Their <b>energy</b>, <b>banked actions</b> and hidden position are never sent.",
    "Их <b>энергия</b>, <b>отложенные действия</b> и скрытая позиция не передаются никогда."],

  "p.s4_num": ["04 · Output", "04 · Выход"],
  "p.s4_h": ["The reply (bot → engine)", "Ответ (бот → движок)"],
  "p.s4_p": ["Exactly one JSON object on stdout, followed by a newline and a flush.",
    "Ровно один JSON-объект в stdout, затем перевод строки и flush."],
  "p.reply_named": ["By name", "По имени"],
  "p.reply_named_p": ["Action names are case-insensitive and whitespace is trimmed.",
    "Имена действий нечувствительны к регистру, пробелы обрезаются."],
  "p.reply_index": ["By index", "По индексу"],
  "p.reply_index_p": ["Picks straight out of the <code>legal_actions</code> array you were sent. The simplest possible valid bot.",
    "Выбор прямо из присланного массива <code>legal_actions</code>. Простейший рабочий бот."],
  "p.target_note": ["<b><code>target</code> is honoured for <code>JUMP</code> only.</b> It is discarded for every other action. In particular a long-range snipe is a plain <code>{\"action\":\"FIRE\"}</code> — the engine picks the target itself.",
    "<b><code>target</code> учитывается только для <code>JUMP</code>.</b> Для всех остальных действий он отбрасывается. В частности, выстрел издали — это обычный <code>{\"action\":\"FIRE\"}</code>: движок сам выберет цель."],
  "p.all_actions_h": ["Every action you can send", "Все действия, которые можно отправить"],
  "p.col_send": ["Send", "Отправить"],
  "p.col_cost": ["Cost", "Цена"],
  "p.col_does": ["What it does", "Что делает"],
  "p.free": ["free", "бесплатно"],
  "p.legal_note": ["Everything currently affordable and legal is already listed in <code>legal_actions</code>, so you never have to re-derive the rules — but the engine validates your reply independently either way.",
    "Всё, что сейчас доступно и легально, уже перечислено в <code>legal_actions</code>, так что выводить правила заново не нужно — но движок в любом случае проверяет ваш ответ самостоятельно."],

  "p.s5_num": ["05 · Hidden information", "05 · Скрытая информация"],
  "p.s5_h": ["What the request deliberately withholds", "Что запрос намеренно скрывает"],
  "p.s5_p": ["This is a fog-of-war game, and the protocol is the fog. Two things you must infer yourself.",
    "Это игра с туманом войны, и протокол — это и есть туман. Две вещи придётся выводить самому."],
  "p.where_h": ["Where is the rival?", "Где соперник?"],
  "p.where_p": ["You get <code>known_position</code> only when it is certain. The \"could be here\" candidate set is <b>not</b> provided — rebuild it yourself from the two seeds you are given:",
    "<code>known_position</code> приходит только когда позиция достоверна. Множество кандидатов «возможно, он здесь» <b>не</b> передаётся — восстановите его сами из двух данных вам зацепок:"],
  "p.where_p2": ["A breadth-first search over <code>map.adjacency</code> is enough. Narrow it further using what you can see: a system you are standing next to and know is empty can be struck off.",
    "Достаточно поиска в ширину по <code>map.adjacency</code>. Сужайте множество тем, что видите: соседнюю систему, о которой вы знаете, что она пуста, можно вычеркнуть."],
  "p.own_h": ["Who owns what?", "Кто чем владеет?"],
  "p.own_p": ["The ownership map is fogged too. This is the trap worth spelling out:",
    "Карта владений тоже затуманена. Вот ловушка, которую стоит проговорить:"],
  "p.col_reads": ["The request says", "В запросе"],
  "p.col_means": ["It means", "Означает"],
  "p.own_owned": ["That ship holds it. Confirmed.", "Система принадлежит этому кораблю. Достоверно."],
  "p.own_free": ["Genuinely unowned. Confirmed.", "Действительно ничья. Достоверно."],
  "p.own_fog": ["You have never sensed this system. It may quietly belong to the rival.",
    "Вы никогда не наблюдали эту систему. Она вполне может тихо принадлежать сопернику."],
  "p.own_note": ["A rival claiming under Deep Cloak grows their empire invisibly, so treating fogged systems as free real estate is how you lose the domination race without ever seeing it happen.",
    "Соперник, захватывающий системы под глубокой маскировкой, растит империю невидимо. Считать затуманенные системы ничейными — верный способ проиграть гонку за доминирование, так и не заметив этого."],

  "p.s6_num": ["06 · Get started", "06 · Начало работы"],
  "p.s6_h": ["A working bot in ten lines", "Рабочий бот в десять строк"],
  "p.s6_p": ["Both SDKs do nothing but the read/parse/write loop, so you can also skip them and speak the protocol directly.",
    "Оба SDK делают только цикл «прочитать/разобрать/записать», так что их можно и не брать, а говорить на протоколе напрямую."],
  "p.py_h": ["Python — <code>starship_sdk.py</code>", "Python — <code>starship_sdk.py</code>"],
  "p.py_note": ["Upload a single <code>.py</code> file. Copy <code>starship_sdk.py</code> beside it, or inline the six-line loop.",
    "Загружайте один файл <code>.py</code>. Положите рядом <code>starship_sdk.py</code> или впишите цикл из шести строк прямо в бота."],
  "p.cpp_h": ["C++ — <code>starship_bot.hpp</code>", "C++ — <code>starship_bot.hpp</code>"],
  "p.cpp_note": ["Upload a single <code>.cpp</code> file — it is compiled on the server with <code>-std=c++17 -O2</code>. <code>nlohmann/json</code> is provided.",
    "Загружайте один файл <code>.cpp</code> — он компилируется на сервере с <code>-std=c++17 -O2</code>. <code>nlohmann/json</code> уже есть."],
  "p.sub_h": ["Submitting", "Отправка"],
  "p.sub_p": ["Upload one file on the <b>Tournament</b> page. It is statically scanned, then smoke-tested against <code>random</code> inside the same sandbox it will compete in. If it builds, survives the game, and misbehaves on no more than half its moves, it becomes your active entry and starts playing the ladder.",
    "Загрузите один файл на странице <b>Турнир</b>. Он проходит статическую проверку, затем тестовую партию против <code>random</code> в той же песочнице, где будет соревноваться. Если он собирается, доживает до конца партии и сбоит не более чем на половине ходов, он становится вашей активной заявкой и выходит в лигу."],
  "p.sub_note": ["Test locally first — point the engine at your program directly:",
    "Сначала протестируйте локально — направьте движок прямо на вашу программу:"],
  "p.cta_submit": ["Submit a bot ♛", "Отправить бота ♛"],
  "p.cta_rules": ["Learn the game ?", "Изучить игру ?"],

  // ---- rules.html ----------------------------------------------------------
  "r.title": ["Starship Duel — Field Guide", "Starship Duel — Полевой справочник"],
  "r.kicker": ["Field Guide", "Полевой справочник"],
  "r.hero_kicker": ["A hidden-information duel", "Дуэль со скрытой информацией"],
  "r.hero_h1": ["Learn to hunt in the dark", "Научитесь охотиться в темноте"],
  "r.hero_lede": ["Two ships fight across a network of star systems. It's 1v1, turn by turn, zero-sum — and your exact location is <b>invisible</b> to your rival unless something gives you away. Score the map, hunt the rival down, or outlast the collapse.",
    "Два корабля сражаются в сети звёздных систем. Один на один, ход за ходом, с нулевой суммой — и ваше точное положение <b>невидимо</b> сопернику, пока вы себя не выдадите. Набирайте очки на карте, выслеживайте соперника или переживите коллапс."],
  "r.tag_fog": ["👁 Fog of war", "👁 Туман войны"],
  "r.tag_win": ["🎯 3 ways to win", "🎯 3 пути к победе"],
  "r.tag_kit": ["🛰 Unlockable kit", "🛰 Открываемое снаряжение"],
  "r.tag_arena": ["💥 A shrinking arena", "💥 Сжимающаяся арена"],

  "r.s1_num": ["01 · Objective", "01 · Цель"],
  "r.s1_h": ["Three ways to win", "Три пути к победе"],
  "r.s1_p": ["You can't just hide — every path to victory means leaving cover at the right moment.",
    "Просто спрятаться не выйдет — любой путь к победе требует вовремя выйти из укрытия."],
  "r.dom_h": ["Domination", "Доминирование"],
  "r.dom_p": ["At the start of each turn you bank <b>control points</b> equal to your income — <b>+1</b> per single-star system you own, <b>+4</b> per binary. First to fill the <b>CONTROL</b> bar wins on points.",
    "В начале каждого хода вы получаете <b>очки контроля</b> по своему доходу: <b>+1</b> за каждую одиночную звезду и <b>+4</b> за двойную. Кто первым заполнит шкалу <b>КОНТРОЛЬ</b>, побеждает по очкам."],
  "r.elim_h": ["Elimination", "Уничтожение"],
  "r.elim_p": ["A landed <b>Fire</b> is a <b>raid</b>: it steals points, <b>captures</b> the system, and costs the rival a <b>life</b> (they respawn hidden). Take all <b>3 lives</b> and you win the hunt.",
    "Попавший <b>выстрел</b> — это <b>рейд</b>: он крадёт очки, <b>захватывает</b> систему и отнимает у соперника <b>жизнь</b> (он возрождается скрытно). Заберите все <b>3 жизни</b> — и охота ваша."],
  "r.coll_h": ["Collapse", "Коллапс"],
  "r.coll_p": ["The arena shrinks: systems go <b>supernova</b> from the outside in. Anyone caught on a dying star is destroyed — outlast a rival who couldn't evacuate in time.",
    "Арена сжимается: системы вспыхивают <b>сверхновыми</b> от края к центру. Кто остался на умирающей звезде — уничтожен. Переживите соперника, не успевшего эвакуироваться."],

  "r.s2_num": ["02 · Economy", "02 · Экономика"],
  "r.s2_h": ["Anatomy of a turn", "Анатомия хода"],
  "r.s2_p": ["Ships alternate turns. Each turn is a small budget of actions fuelled by the territory you hold.",
    "Корабли ходят по очереди. Каждый ход — небольшой бюджет действий, который питает удерживаемая вами территория."],
  "r.get_h": ["What you get", "Что вы получаете"],
  "r.get_1": ["<b>2 actions</b> every turn — plus any you banked with <b>Overcharge</b>.",
    "<b>2 действия</b> каждый ход — плюс отложенные через <b>перезарядку</b>."],
  "r.get_2": ["<b>Energy income</b> at turn start from systems you own: <b>+1</b> single-star, <b>+4</b> binary.",
    "<b>Доход энергии</b> в начале хода с ваших систем: <b>+1</b> за одиночную звезду, <b>+4</b> за двойную."],
  "r.get_3": ["Actions beyond the base 2 <b>carry over</b>; the base 2 do not — so spare tempo is never wasted.",
    "Действия сверх базовых двух <b>переносятся</b>, базовые — нет, так что лишний темп не пропадает."],
  "r.get_4": ["The second mover starts with a small <b>komi</b> handicap so going first isn't an unfair edge.",
    "Второй игрок получает небольшую фору <b>коми</b>, чтобы первый ход не давал несправедливого преимущества."],
  "r.spend_h": ["What you spend it on", "На что вы это тратите"],
  "r.spend_p": ["Actions come in three families — the gallery below shows each one in motion:",
    "Действия делятся на три семейства — галерея ниже показывает каждое в движении:"],
  "r.spend_1": ["<b>Move &amp; hold</b> — reposition and duck back under cloak (free).",
    "<b>Движение и укрытие</b> — сменить позицию и снова уйти под маскировку (бесплатно)."],
  "r.spend_2": ["<b>Score &amp; strike</b> — Claim territory and Fire raids.",
    "<b>Очки и удары</b> — захват территории и огневые рейды."],
  "r.spend_3": ["<b>Tools &amp; unlocks</b> — Energy-priced recon, cloak, and permanent upgrades.",
    "<b>Инструменты и улучшения</b> — разведка, маскировка и постоянные апгрейды за энергию."],
  "r.spend_note": ["Crash your bot or run out of time and you forfeit.",
    "Если ваш бот падает или не укладывается во время — вам засчитывается поражение."],

  "r.s3_num": ["03 · The playbook", "03 · Арсенал"],
  "r.s3_h": ["Every action, in motion", "Каждое действие в движении"],
  "r.s3_p": ["These are live previews built from the game's own art — tap any scene to replay it.",
    "Это живые превью из настоящей графики игры — нажмите на сцену, чтобы повторить."],
  "r.band_move": ["Move &amp; position — free", "Движение и позиция — бесплатно"],
  "r.band_score": ["Score &amp; strike", "Очки и удары"],
  "r.band_energy": ["Recon, cloak &amp; tempo — Energy-priced", "Разведка, маскировка и темп — за энергию"],
  "r.band_unlock": ["Permanent unlocks — buy once, keep forever",
    "Постоянные улучшения — купил один раз, оставил навсегда"],
  "r.replay_hint": ["↻ replay", "↻ повтор"],
  "r.cost_move": ["move", "ход"],
  "r.cost_free": ["free", "бесплатно"],
  "r.unlock6": ["6 ⚡ · unlock", "6 ⚡ · улучшение"],
  "r.unlock8": ["8 ⚡ · unlock", "8 ⚡ · улучшение"],
  "r.unlock10": ["10 ⚡ · unlock", "10 ⚡ · улучшение"],

  "r.jump_p": ["Hop to an adjacent system along a lane.", "Перескочить в соседнюю систему по трассе."],
  "r.jump_note": ["<b>Watch out:</b> entering a rival-claimed or rival-occupied system can expose your position.",
    "<b>Осторожно:</b> вход в систему, захваченную или занятую соперником, может раскрыть вашу позицию."],
  "r.hold_p": ["Stay put and slip back under cloak.", "Остаться на месте и снова уйти под маскировку."],
  "r.hold_note": ["The way to <b>disappear again</b> after you've been spotted.",
    "Способ <b>снова исчезнуть</b> после того, как вас заметили."],
  "r.claim_p": ["Take the system you're standing on for income — even one the rival owns.",
    "Забрать систему, в которой стоите, ради дохода — даже если она принадлежит сопернику."],
  "r.claim_note": ["<b>Watch out:</b> claiming <b>exposes your position</b> — only <b>Deep Cloak</b> keeps the grab hidden (Jamming keeps the territory off their map, but your ship still shows).",
    "<b>Осторожно:</b> захват <b>раскрывает вашу позицию</b> — скрыть его может только <b>глубокая маскировка</b> (глушение прячет территорию с их карты, но корабль всё равно виден)."],
  "r.fire_p": ["Raid your current system. On a hit: <b>steal points</b>, <b>capture</b> the ground, cost the rival a <b>life</b>.",
    "Рейд по вашей текущей системе. При попадании: <b>крадёте очки</b>, <b>захватываете</b> систему, отнимаете <b>жизнь</b>."],
  "r.fire_note": ["Charged whether it lands or misses, so blind spam self-punishes. A <b>deep-cloaked</b> rival is immune.",
    "Списывается и при попадании, и при промахе, так что слепой спам наказывает сам себя. Соперник под <b>глубокой маскировкой</b> неуязвим."],
  "r.scan_p": ["Sweep the ownership map and <b>pin the rival's exact system</b>.",
    "Просканировать карту владений и <b>точно определить систему соперника</b>."],
  "r.scan_note": ["Pure tempo — the read you want before committing a raid. Beaten by <b>Deep Cloak</b>.",
    "Чистый темп — то, что нужно узнать перед рейдом. Бьётся <b>глубокой маскировкой</b>."],
  "r.cloak_p": ["Become <b>undetectable for 2 turns</b> — a rival's <b>Scan and Long-Range Scanners sweep right past you</b>, and you can't be raided or lose a life.",
    "Стать <b>необнаружимым на 2 хода</b> — <b>«Скан» и дальние сканеры соперника проходят мимо</b>, вас нельзя атаковать рейдом и вы не теряете жизнь."],
  "r.cloak_note": ["Sit in enemy territory undisturbed and punch a raid through radar — but a point-blank <b>Proximity Alert</b> still catches you (see the counters below).",
    "Можно спокойно сидеть на вражеской территории и пробить рейд сквозь радар — но <b>датчик сближения</b> в упор всё равно засечёт (см. контрмеры ниже)."],
  "r.oc_p": ["Bank <b>+1 extra action</b> for next turn (it stacks).",
    "Отложить <b>+1 действие</b> на следующий ход (складывается)."],
  "r.oc_note": ["Cheap enough to snowball a lead — buy the tempo to claim twice in a turn.",
    "Достаточно дёшево, чтобы наращивать перевес — купите темп и захватывайте дважды за ход."],
  "r.oc_base": ["base 2", "базовые 2"],
  "r.oc_banked": ["banked +1", "в запасе +1"],
  "r.end_p": ["Stop early on purpose.", "Сознательно закончить ход раньше."],
  "r.end_note": ["Any actions beyond the base 2 left unspent are <b>banked</b> for next turn.",
    "Неиспользованные действия сверх базовых двух <b>переносятся</b> на следующий ход."],
  "r.prox_p": ["<b>Defensive.</b> A <b>capture shield</b> on your ship's system plus a short-range alarm that <b>pierces cloak</b>.",
    "<b>Защита.</b> <b>Щит от захвата</b> на системе вашего корабля плюс ближняя тревога, <b>пробивающая маскировку</b>."],
  "r.prox_note": ["Early warning of an incoming raid — the rival lights up as they close in.",
    "Раннее предупреждение о рейде — соперник подсвечивается, когда подходит близко."],
  "r.lrs_p": ["<b>Offensive.</b> Passively <b>track the rival</b> while they're within range, and see ownership two hops out.",
    "<b>Атака.</b> Пассивно <b>отслеживать соперника</b>, пока он в радиусе, и видеть владения на два перехода вокруг."],
  "r.lrs_note": ["Line up capture-raids without spending a Scan every turn. Also beaten by <b>Deep Cloak</b>.",
    "Готовьте рейды с захватом, не тратя «Скан» каждый ход. Тоже бьётся <b>глубокой маскировкой</b>."],
  "r.jam_p": ["Your Energy actions show to the rival only as a generic <b>“JAMMED”</b>.",
    "Ваши энергозатратные действия видны сопернику только как безликое <b>«JAMMED»</b>."],
  "r.jam_note": ["Your territory grabs stay <b>off their map</b> and it <b>blinds their Proximity Alert</b> — though firing or claiming still exposes your ship.",
    "Ваши захваты территории <b>не появляются на их карте</b>, а их <b>датчик сближения слепнет</b> — хотя выстрел или захват всё равно раскрывает корабль."],
  "r.jam_sees": ["RIVAL SEES…", "СОПЕРНИК ВИДИТ…"],
  "r.jam_quote": ["“JAMMED”", "«JAMMED»"],

  "r.s4_num": ["04 · Mind games", "04 · Игра разума"],
  "r.s4_h": ["Information is the real weapon", "Информация — вот настоящее оружие"],
  "r.s4_p": ["The whole duel is a bluff over where you are and what you're building.",
    "Вся дуэль — это блеф о том, где вы находитесь и что строите."],
  "r.fog_h": ["Fog of war", "Туман войны"],
  "r.fog_p": ["You start <b>cloaked</b>. The rival only ever sees a “could-be-here” cloud — and the ownership map is <b>fogged</b>, so a Claim you make while deep-cloaked expands your empire <b>invisibly</b>. A well-placed Scan tears the fog away:",
    "Вы начинаете <b>под маскировкой</b>. Соперник видит лишь облако «возможно, он здесь», а карта владений <b>затуманена</b> — поэтому захват под глубокой маскировкой расширяет вашу империю <b>невидимо</b>. Вовремя сделанный «Скан» разрывает туман:"],
  "r.counters_h": ["Kit counters", "Контрмеры снаряжения"],
  "r.counters_p": ["The three unlocks form a rock-paper-scissors — the read on your rival's kit tells you which one to buy:",
    "Три улучшения образуют «камень-ножницы-бумагу» — зная снаряжение соперника, вы поймёте, что покупать:"],
  "r.beats": ["beats ▶", "бьёт ▶"],
  "r.c_cloak": ["Deep&nbsp;Cloak", "Глубокая&nbsp;маскировка"],
  "r.c_scan": ["Scan &amp; Scanners", "«Скан» и сканеры"],
  "r.c_prox": ["Proximity&nbsp;Alert", "Датчик&nbsp;сближения"],
  "r.c_cloak2": ["Deep Cloak", "Глубокая маскировка"],
  "r.c_jam": ["Jamming", "Глушение"],
  "r.c_prox2": ["Proximity Alert", "Датчик сближения"],
  "r.c_lrs": ["Long-Range Scanners", "Дальние сканеры"],
  "r.cache_note": ["Energy caches sit on the map and grow in value — but you collect one only by <b>starting your turn on it</b>, which exposes you. Risk for reward.",
    "Энергетические тайники лежат на карте и растут в цене — но забрать тайник можно, только <b>начав ход на нём</b>, а это вас раскрывает. Риск ради награды."],

  "r.s5_num": ["05 · The clock", "05 · Часы"],
  "r.s5_h": ["The collapse forces a finish", "Коллапс заставляет доиграть"],
  "r.s5_p": ["No stalling to a draw — the arena eats itself until the two of you have nowhere left to hide.",
    "Затянуть на ничью не выйдет — арена пожирает себя, пока прятаться станет негде."],
  "r.warn_h": ["Read the warnings", "Читайте предупреждения"],
  "r.warn_p1": ["From about turn 24, systems collapse from the outside in toward a random surviving <b>“eye”</b>. A system first <b>destabilizes</b> — a visible ⚠ countdown giving you a few turns to evacuate — then goes <b>supernova</b>. Anyone still standing on it is destroyed.",
    "Примерно с 24-го хода системы схлопываются от края внутрь, к случайно выбранному уцелевшему <b>«оку»</b>. Сначала система <b>дестабилизируется</b> — виден отсчёт ⚠, дающий несколько ходов на эвакуацию, — затем вспыхивает <b>сверхновой</b>. Всё, что осталось на ней, уничтожается."],
  "r.warn_p2": ["Both ships get squeezed together, so every game resolves and hiding forever isn't a plan.",
    "Оба корабля стискивает вместе, так что любая партия доигрывается, и «прятаться вечно» — не план."],
  "r.leg_stable": ["Stable", "Стабильная"],
  "r.leg_destab": ["Destabilizing", "Дестабилизация"],
  "r.leg_nova": ["Supernova", "Сверхновая"],
  "r.nova_h": ["A star dies", "Звезда умирает"],
  "r.nova_p": ["Watch the outer system decay while the eye holds — and evacuate before you're caught:",
    "Смотрите, как внешняя система разрушается, пока «око» держится, — и эвакуируйтесь, пока не поздно:"],
  "r.the_eye": ["the eye", "око"],
  "r.start_game": ["▶ Start a game", "▶ Начать партию"],
  "r.enter_bot": ["Enter a bot ♛", "Выставить бота ♛"],
};

// -------------------------------------------------------------- core ---------
function detect() {
  const saved = localStorage.getItem(STORE_KEY);
  if (LANGS.includes(saved)) return saved;
  const nav = (navigator.languages || [navigator.language || "en"]).join(",").toLowerCase();
  return /\bru\b|ru-/.test(nav) ? "ru" : "en";
}

let lang = detect();
const listeners = [];

function idx() { return lang === "ru" ? 1 : 0; }

/** Translate `key`, interpolating {placeholders} from `vars`. */
function t(key, vars) {
  const row = STR[key];
  let s = row ? (row[idx()] || row[0]) : key;
  if (vars) s = s.replace(/\{(\w+)\}/g, (m, k) => (vars[k] !== undefined ? String(vars[k]) : m));
  return s;
}

/** Apply data-i18n* attributes under `root` (default: whole document). */
function applyStatic(root) {
  const scope = root || document;
  scope.querySelectorAll("[data-i18n]").forEach((n) => { n.textContent = t(n.dataset.i18n); });
  scope.querySelectorAll("[data-i18n-html]").forEach((n) => { n.innerHTML = t(n.dataset.i18nHtml); });
  scope.querySelectorAll("[data-i18n-title]").forEach((n) => { n.title = t(n.dataset.i18nTitle); });
  scope.querySelectorAll("[data-i18n-ph]").forEach((n) => { n.placeholder = t(n.dataset.i18nPh); });
  scope.querySelectorAll("[data-i18n-aria]").forEach((n) => { n.setAttribute("aria-label", t(n.dataset.i18nAria)); });
  const title = document.querySelector("[data-i18n-doctitle]");
  if (title) document.title = t(title.dataset.i18nDoctitle);
  document.documentElement.lang = lang;
}

function setLang(next) {
  if (!LANGS.includes(next) || next === lang) return;
  lang = next;
  localStorage.setItem(STORE_KEY, lang);
  applyStatic();
  renderSwitch();
  for (const fn of listeners) { try { fn(lang); } catch (e) { console.error(e); } }
}

function onChange(fn) { listeners.push(fn); }

/** True when `key` has a translation (for optional/unknown server enums). */
function has(key) { return Object.prototype.hasOwnProperty.call(STR, key); }

/** Render the EN/RU toggle into every [data-lang-switch] host. */
function renderSwitch() {
  document.querySelectorAll("[data-lang-switch]").forEach((host) => {
    host.className = "lang-switch";
    host.title = t("lang.switch");
    host.replaceChildren();
    for (const l of LANGS) {
      const b = document.createElement("button");
      b.type = "button";
      b.className = "lang-btn" + (l === lang ? " is-on" : "");
      b.textContent = t("lang." + l);
      b.onclick = () => setLang(l);
      host.appendChild(b);
    }
  });
}

// ------------------------------------------------- server-string rewriting ---
// The backend emits English log lines / action metadata; rather than threading a
// locale through the engine we rewrite the handful of known shapes here.

const SHIP = (n) => (lang === "ru" ? `корабль ${Number(n) + 1}` : `ship${n}`);

const EXPOSE_REASON = {
  "entered rival-claimed system": "вошёл в систему соперника",
  "entered rival's system": "вошёл в систему, где стоит соперник",
  "co-location": "оказались вместе",
  "claim": "захват",
  "raid": "рейд",
  "failed fire vs proximity alert": "промах по датчику сближения",
  "cache collection": "сбор тайника",
  "long-range scanners": "дальние сканеры",
  "proximity alert": "датчик сближения",
};

const END_REASON = {
  domination: "доминирование",
  eliminated: "уничтожение",
  fire_hit: "попадание",
  "fire hit": "попадание",
  forced_fire: "вынужденный выстрел",
  "forced fire": "вынужденный выстрел",
  supernova: "сверхновая",
  collapse: "коллапс",
  timeout: "исчерпан лимит ходов",
  crash: "сбой бота",
  forfeit: "отказ от игры",
};

const UNLOCK_NAME = {
  proximity_alert: "датчик сближения",
  long_range_scanners: "дальние сканеры",
  jamming: "глушение",
};

function reason(r) { return EXPOSE_REASON[r] || r; }

/** Human-readable end-of-game reason (also used by the games list). */
function tEndReason(r) {
  const key = String(r || "").replace(/_/g, " ");
  if (lang !== "ru") return key;
  return END_REASON[String(r)] || END_REASON[key] || key;
}

const EVENT_RULES = [
  [/^skirmish start on (.+); ship_(\d) moves first$/,
    (m) => `схватка начинается на карте ${m[1]}; первым ходит ${SHIP(m[2])}`],
  [/^ship(\d) jumps to (.+)$/, (m) => `${SHIP(m[1])} прыгает в ${m[2]}`],
  [/^ship(\d) still exposed -- tracked at (.+)$/,
    (m) => `${SHIP(m[1])} всё ещё обнаружен — засечён в ${m[2]}`],
  [/^ship(\d) trips ship(\d)'s proximity alert at (.+)$/,
    (m) => `${SHIP(m[1])} задевает датчик сближения (${SHIP(m[2])}) в ${m[3]}`],
  [/^ship(\d) holds$/, (m) => `${SHIP(m[1])} затаивается`],
  [/^ship(\d) claims (.+)$/, (m) => `${SHIP(m[1])} захватывает ${m[2]}`],
  [/^ship(\d) FIRES and hits at (.+)$/, (m) => `${SHIP(m[1])} СТРЕЛЯЕТ и попадает в ${m[2]}`],
  [/^ship(\d) fires and misses$/, (m) => `${SHIP(m[1])} стреляет и промахивается`],
  [/^ship(\d) (RAIDS|SNIPES) at (.+), steals (\d+) domination$/,
    (m) => `${SHIP(m[1])} ${m[2] === "RAIDS" ? "СОВЕРШАЕТ РЕЙД" : "БЬЁТ ИЗДАЛИ"} в ${m[3]}, крадёт ${m[4]} очк. контроля`],
  [/^ship(\d)'s proximity shield holds (.+)$/,
    (m) => `щит сближения (${SHIP(m[1])}) удерживает ${m[2]}`],
  [/^ship(\d) captures (.+)$/, (m) => `${SHIP(m[1])} захватывает ${m[2]}`],
  [/^ship(\d) fires: rival deep-cloaked, no effect$/,
    (m) => `${SHIP(m[1])} стреляет: соперник под глубокой маскировкой, без эффекта`],
  [/^ship(\d) scans: rival at (.+)$/, (m) => `${SHIP(m[1])} сканирует: соперник в ${m[2]}`],
  [/^ship(\d) scans: map swept, rival deep-cloaked \(no fix\)$/,
    (m) => `${SHIP(m[1])} сканирует: карта просмотрена, соперник под глубокой маскировкой (без засечки)`],
  [/^ship(\d) engages deep cloak \((\d+) turns\)$/,
    (m) => `${SHIP(m[1])} включает глубокую маскировку (ходов: ${m[2]})`],
  [/^ship(\d) overcharges \(\+1 banked action\)$/,
    (m) => `${SHIP(m[1])} перезаряжается (+1 действие в запас)`],
  [/^ship(\d) unlocks (.+)$/, (m) => `${SHIP(m[1])} открывает: ${UNLOCK_NAME[m[2]] || m[2]}`],
  [/^ship(\d) tracks rival at (.+) \(long-range scanners\)$/,
    (m) => `${SHIP(m[1])} отслеживает соперника в ${m[2]} (дальние сканеры)`],
  [/^ship(\d) exposed \((.+)\) at (.+)$/,
    (m) => `${SHIP(m[1])} обнаружен (${reason(m[2])}) в ${m[3]}`],
  [/^ship(\d) detects rival at (.+) \((.+)\)$/,
    (m) => `${SHIP(m[1])} засекает соперника в ${m[2]} (${reason(m[3])})`],
  [/^ship(\d) ends co-located; ship(\d) force-fires$/,
    (m) => `${SHIP(m[1])} заканчивает ход рядом с соперником; ${SHIP(m[2])} стреляет вынужденно`],
  [/^ship(\d) wins skirmish \((.+)\)$/,
    (m) => `${SHIP(m[1])} выигрывает схватку (${tEndReason(m[2])})`],
  [/^ship(\d) is hit \((\d+) lives left\)$/,
    (m) => `${SHIP(m[1])} получает попадание (жизней осталось: ${m[2]})`],
  [/^ship(\d) respawns and vanishes$/, (m) => `${SHIP(m[1])} возрождается и исчезает`],
  [/^skirmish timeout \(winner=(.+)\)$/,
    (m) => `схватка по лимиту ходов (победитель: ${m[1] === "None" ? "нет" : SHIP(m[1])})`],
  [/^both ships caught in the collapse — draw$/, () => "оба корабля погибли в коллапсе — ничья"],
  [/^ship(\d) crashed \((.*)\) — forfeits$/,
    (m) => `${SHIP(m[1])} упал с ошибкой (${m[2]}) — поражение`],
];

/** Translate one engine log line (identity in English). */
function tEvent(text) {
  if (lang !== "ru") return text;
  for (const [re, fn] of EVENT_RULES) {
    const m = re.exec(text);
    if (m) return fn(m);
  }
  return text;
}

/** Translate a `_disabled_reason()` string from serialize.py. */
function tReason(text) {
  if (lang !== "ru" || !text) return text || "";
  let m;
  if ((m = /^needs (\d+)⚡ \(have (\d+)\)$/.exec(text)))
    return `нужно ${m[1]}⚡ (есть ${m[2]})`;
  return {
    "already unlocked": "уже открыто",
    "unavailable": "недоступно",
    "already yours": "уже ваша",
    "can't claim here": "здесь нельзя захватить",
    "enemy-held system": "система соперника",
    "must move (unstable)": "надо уходить (нестабильна)",
    "route unstable": "маршрут нестабилен",
  }[text] || text;
}

/** Build an action-menu label locally instead of trusting the server's English. */
function actLabel(entry) {
  if (entry.snipe_target) return `${t("act.SNIPE")} → ${entry.snipe_target}`;
  const base = STR["act." + entry.type] ? t("act." + entry.type) : entry.type;
  return entry.dest ? `${base} → ${entry.dest}` : base;
}

window.I18N = { t, has, lang: () => lang, setLang, applyStatic, onChange, renderSwitch,
                tEvent, tReason, tEndReason, actLabel };

// Translate the shell as soon as the DOM is parsed; pages re-render their own
// dynamic parts via onChange().
function initI18n() { applyStatic(); renderSwitch(); }
if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", initI18n);
else initI18n();
})();
