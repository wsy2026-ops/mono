CREATE TABLE IF NOT EXISTS ach_defs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT UNIQUE NOT NULL,
            title TEXT NOT NULL,
            desc TEXT NOT NULL DEFAULT '',
            icon TEXT NOT NULL DEFAULT '🏅',
            group_name TEXT NOT NULL DEFAULT '其它',
            coins INTEGER NOT NULL DEFAULT 0,
            cond_json TEXT NOT NULL DEFAULT '{}',
            sort INTEGER NOT NULL DEFAULT 0
        );
CREATE TABLE IF NOT EXISTS ach_user (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            def_id INTEGER NOT NULL,
            unlocked_at REAL NOT NULL,
            UNIQUE(user_id, def_id)
        );
CREATE TABLE IF NOT EXISTS activities (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            name        TEXT NOT NULL,
            cover       TEXT DEFAULT '',
            intro       TEXT DEFAULT '',
            category    TEXT DEFAULT '趣味闲聊', -- 技术活动/游戏活动/趣味闲聊
            mode        TEXT NOT NULL DEFAULT 'submission', -- submission / online
            status      TEXT NOT NULL DEFAULT 'upcoming', -- upcoming / ongoing / ended
            room_id     INTEGER DEFAULT NULL,
            start_time  REAL NOT NULL DEFAULT 0,
            end_time    REAL NOT NULL DEFAULT 0,
            created_by  INTEGER NOT NULL DEFAULT 0,
            created_at  REAL NOT NULL DEFAULT 0
        );
CREATE TABLE IF NOT EXISTS activity_log (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER NOT NULL DEFAULT 0,
            username    TEXT NOT NULL DEFAULT '',
            action      TEXT NOT NULL DEFAULT '',
            detail      TEXT NOT NULL DEFAULT '',
            ip          TEXT NOT NULL DEFAULT '',
            ua          TEXT NOT NULL DEFAULT '',
            created_at  REAL NOT NULL DEFAULT 0
        );
CREATE TABLE IF NOT EXISTS activity_participants (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            activity_id   INTEGER NOT NULL,
            user_id       INTEGER NOT NULL,
            username      TEXT NOT NULL,
            submission    TEXT DEFAULT '',
            submission_url TEXT DEFAULT '',
            status        TEXT NOT NULL DEFAULT 'pending', -- pending / approved / rejected / awarded
            award_exp     INTEGER NOT NULL DEFAULT 0,
            created_at    REAL NOT NULL DEFAULT 0
        );
CREATE TABLE IF NOT EXISTS admin_logs (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            admin_id    INTEGER NOT NULL,
            admin_name  TEXT NOT NULL,
            action      TEXT NOT NULL,
            target      TEXT DEFAULT '',
            detail      TEXT DEFAULT '',
            created_at  REAL NOT NULL
        );
CREATE TABLE IF NOT EXISTS ai_chat_history (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id    INTEGER NOT NULL,
            role       TEXT NOT NULL,           -- 'user' / 'assistant' / 'system'
            content    TEXT NOT NULL,
            model      TEXT,
            prompt_tokens INTEGER DEFAULT 0,
            completion_tokens INTEGER DEFAULT 0,
            created_at REAL NOT NULL DEFAULT 0
        );
CREATE TABLE IF NOT EXISTS ai_debug_logs (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, problem TEXT, code TEXT, note TEXT, answer TEXT, model TEXT, created_at REAL, prompt_tokens INTEGER NOT NULL DEFAULT 0, completion_tokens INTEGER NOT NULL DEFAULT 0, latency_ms INTEGER NOT NULL DEFAULT 0, success INTEGER NOT NULL DEFAULT 1, error TEXT NOT NULL DEFAULT '', ip TEXT NOT NULL DEFAULT '', out_chars INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS ai_fp_quota (
            fp       TEXT NOT NULL,
            day      TEXT NOT NULL,
            used     INTEGER NOT NULL DEFAULT 0,
            updated_at REAL NOT NULL DEFAULT 0,
            PRIMARY KEY (fp, day)
        );
CREATE TABLE IF NOT EXISTS ai_logs (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id    INTEGER NOT NULL,
            model      TEXT,
            prompt_tokens INTEGER DEFAULT 0,
            completion_tokens INTEGER DEFAULT 0,
            success    INTEGER NOT NULL DEFAULT 1,
            error      TEXT DEFAULT '',
            created_at REAL NOT NULL DEFAULT 0
        , module TEXT NOT NULL DEFAULT '', mode TEXT NOT NULL DEFAULT '', latency_ms INTEGER NOT NULL DEFAULT 0, ip TEXT NOT NULL DEFAULT '', question TEXT NOT NULL DEFAULT '', answer TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS ai_moderation (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                target_type TEXT NOT NULL,      -- post / comment / pm
                target_id INTEGER NOT NULL,
                user_id INTEGER,
                title TEXT,
                body TEXT,
                cold_level TEXT,                -- ok/suspect/bad/skip
                cold_score REAL,
                llm_level TEXT,                 -- ok/suspect/bad/skip
                llm_score REAL,
                llm_why TEXT,
                job_id TEXT,
                status TEXT DEFAULT 'pending',  -- pending/cleared/flagged/hidden/reviewed
                reviewed_by INTEGER,
                reviewed_at REAL,
                created_at REAL,
                updated_at REAL
            , cold_why TEXT);
CREATE TABLE IF NOT EXISTS ai_redeem_codes (id INTEGER PRIMARY KEY AUTOINCREMENT, code TEXT UNIQUE NOT NULL, add_times INTEGER NOT NULL DEFAULT 0, max_uses INTEGER NOT NULL DEFAULT 0, used_count INTEGER NOT NULL DEFAULT 0, note TEXT DEFAULT "", created_at REAL, per_use INTEGER NOT NULL DEFAULT 1, created_by INTEGER NOT NULL DEFAULT 0, valid_from TEXT NOT NULL DEFAULT '', valid_until TEXT NOT NULL DEFAULT '', amount REAL NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS ai_redeem_used (id INTEGER PRIMARY KEY AUTOINCREMENT, code_id INTEGER NOT NULL, user_id INTEGER NOT NULL, add_times INTEGER NOT NULL DEFAULT 0, created_at REAL, used_at REAL NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS ai_square_convs (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id    INTEGER NOT NULL,
            title      TEXT NOT NULL DEFAULT '新对话',
            model      TEXT NOT NULL DEFAULT '',
            created_at REAL NOT NULL DEFAULT 0,
            updated_at REAL NOT NULL DEFAULT 0
        );
CREATE TABLE IF NOT EXISTS ai_square_msgs (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            conv_id    INTEGER NOT NULL,
            role       TEXT NOT NULL,
            content    TEXT NOT NULL,
            cost       REAL NOT NULL DEFAULT 0,
            created_at REAL NOT NULL DEFAULT 0
        , model TEXT NOT NULL DEFAULT '', images TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS ai_square_usage (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id    INTEGER NOT NULL,
            conv_id    INTEGER NOT NULL DEFAULT 0,
            model      TEXT NOT NULL DEFAULT '',
            provider   TEXT NOT NULL DEFAULT '',
            cost       REAL NOT NULL DEFAULT 0,
            prompt_tokens     INTEGER NOT NULL DEFAULT 0,
            completion_tokens INTEGER NOT NULL DEFAULT 0,
            created_at REAL NOT NULL DEFAULT 0
        );
CREATE TABLE IF NOT EXISTS ai_tag_jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                post_id INTEGER,
                job_id TEXT,
                status TEXT DEFAULT 'pending',
                tags TEXT,
                created_at REAL
            , updated_at REAL);
CREATE TABLE IF NOT EXISTS appeals (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            punishment_id INTEGER NOT NULL,
            user_id       INTEGER NOT NULL,
            username      TEXT NOT NULL,
            reason        TEXT DEFAULT '',
            screenshot    TEXT DEFAULT NULL,
            status        TEXT NOT NULL DEFAULT 'pending',  -- pending / upheld / revoked
            admin_note    TEXT DEFAULT '',
            reviewer_id   INTEGER DEFAULT NULL,
            reviewer_name TEXT DEFAULT '',
            reviewed_at   REAL DEFAULT NULL,
            created_at    REAL NOT NULL
        );
CREATE TABLE IF NOT EXISTS attack_events (
            id     INTEGER PRIMARY KEY AUTOINCREMENT,
            ts     REAL NOT NULL,
            kind   TEXT NOT NULL,
            ip     TEXT NOT NULL,
            path   TEXT,
            count  INTEGER NOT NULL DEFAULT 1,
            detail TEXT
        );
CREATE TABLE IF NOT EXISTS blacklist (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER NOT NULL,
            blocked_id  INTEGER NOT NULL,
            created_at  REAL NOT NULL DEFAULT 0,
            UNIQUE(user_id, blocked_id)
        );
CREATE TABLE IF NOT EXISTS board_game_stats (
            user_id      INTEGER NOT NULL,
            game         TEXT NOT NULL,   -- 'gomoku' / 'go' / 'xq' / 'jq'
            wins         INTEGER NOT NULL DEFAULT 0,
            losses       INTEGER NOT NULL DEFAULT 0,
            draws        INTEGER NOT NULL DEFAULT 0,
            games        INTEGER NOT NULL DEFAULT 0,
            ai_best_steps INTEGER NOT NULL DEFAULT 0,  -- 与 AI 对局中人类所走最多手数（取单局最大）
            ai_games     INTEGER NOT NULL DEFAULT 0,
            ai_wins      INTEGER NOT NULL DEFAULT 0, rating REAL NOT NULL DEFAULT 0,   -- 战胜 AI 的次数
            PRIMARY KEY (user_id, game)
        );
CREATE TABLE IF NOT EXISTS changelog (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            date       TEXT NOT NULL DEFAULT '',
            text       TEXT NOT NULL DEFAULT '',
            is_new     INTEGER NOT NULL DEFAULT 0,
            sort_order INTEGER NOT NULL DEFAULT 0,
            created_at REAL NOT NULL DEFAULT 0,
            updated_at REAL NOT NULL DEFAULT 0
        );
CREATE TABLE IF NOT EXISTS chat_rooms (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            name         TEXT NOT NULL,
            description   TEXT DEFAULT '',
            sort_order   INTEGER NOT NULL DEFAULT 0,
            is_closed    INTEGER NOT NULL DEFAULT 0,
            file_upload  INTEGER NOT NULL DEFAULT 1,
            all_muted    INTEGER NOT NULL DEFAULT 0,
            guest_readonly INTEGER NOT NULL DEFAULT 0,
            created_at   REAL NOT NULL DEFAULT 0
        );
CREATE TABLE IF NOT EXISTS chess_rooms (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL DEFAULT '国际象棋房间',
            owner_id INTEGER NOT NULL,
            guest_id INTEGER DEFAULT NULL,
            mode INTEGER NOT NULL DEFAULT 0,  -- 0=PvP 1=PvAI(白=玩家,黑=Stockfish)
            is_public INTEGER NOT NULL DEFAULT 1,
            status TEXT NOT NULL DEFAULT 'waiting',
            board_state TEXT NOT NULL DEFAULT '',  -- 当前 FEN
            uci_moves TEXT NOT NULL DEFAULT '',
            last_move TEXT DEFAULT NULL,
            turn_user_id INTEGER DEFAULT NULL,
            winner_id INTEGER DEFAULT NULL,
            stats_awarded INTEGER NOT NULL DEFAULT 0,
            created_at REAL NOT NULL DEFAULT 0,
            updated_at REAL NOT NULL DEFAULT 0
        , ai_name TEXT NOT NULL DEFAULT '', ai_level INTEGER NOT NULL DEFAULT 0, owner_fp TEXT NOT NULL DEFAULT '', guest_fp TEXT NOT NULL DEFAULT '', owner_ip TEXT NOT NULL DEFAULT '', guest_ip TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS comment_likes (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            comment_id  INTEGER NOT NULL,
            user_id     INTEGER NOT NULL,
            created_at  REAL NOT NULL DEFAULT 0,
            UNIQUE(comment_id, user_id)
        );
CREATE TABLE IF NOT EXISTS consumable_logs (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id    INTEGER NOT NULL,
            item_key   TEXT NOT NULL,
            detail     TEXT NOT NULL DEFAULT '',
            created_at REAL NOT NULL DEFAULT 0
        );
CREATE TABLE IF NOT EXISTS dynamic_comments (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            dynamic_id  INTEGER NOT NULL,
            user_id     INTEGER NOT NULL,
            username    TEXT NOT NULL,
            content     TEXT NOT NULL,
            created_at  REAL NOT NULL
        );
CREATE TABLE IF NOT EXISTS dynamic_likes (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            dynamic_id  INTEGER NOT NULL,
            user_id     INTEGER NOT NULL,
            created_at  REAL NOT NULL DEFAULT 0,
            UNIQUE(dynamic_id, user_id)
        );
CREATE TABLE IF NOT EXISTS dynamics (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER NOT NULL,
            username    TEXT NOT NULL,
            content     TEXT NOT NULL DEFAULT '',
            images      TEXT DEFAULT '',          -- JSON 数组：图片 URL
            visibility  TEXT NOT NULL DEFAULT 'public', -- public / friends / self
            post_id     INTEGER DEFAULT NULL,    -- 转发源动态 id
            repost_of   INTEGER DEFAULT NULL,
            created_at  REAL NOT NULL
        );
CREATE TABLE IF NOT EXISTS email_codes (
    email TEXT NOT NULL, purpose TEXT NOT NULL, code_hash TEXT NOT NULL,
    sent_at REAL NOT NULL DEFAULT 0, expires_at REAL NOT NULL DEFAULT 0,
    used INTEGER NOT NULL DEFAULT 0, tries INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (email, purpose));
CREATE TABLE IF NOT EXISTS email_send_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT, email TEXT NOT NULL,
    purpose TEXT NOT NULL, created_at REAL NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS evolve_chat (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id    INTEGER NOT NULL,
                name       TEXT NOT NULL DEFAULT '',
                text       TEXT NOT NULL DEFAULT '',
                created_at REAL NOT NULL DEFAULT 0
            );
CREATE TABLE IF NOT EXISTS evolve_orders (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id    INTEGER NOT NULL,
                give_res   TEXT NOT NULL DEFAULT '',
                give_amt   REAL NOT NULL DEFAULT 0,
                want_res   TEXT NOT NULL DEFAULT '',
                want_amt   REAL NOT NULL DEFAULT 0,
                status     TEXT NOT NULL DEFAULT 'open',
                created_at REAL NOT NULL DEFAULT 0,
                taken_by   INTEGER NOT NULL DEFAULT 0,
                done_at    REAL NOT NULL DEFAULT 0
            );
CREATE TABLE IF NOT EXISTS evolve_players (
                user_id     INTEGER PRIMARY KEY,
                name        TEXT NOT NULL DEFAULT '',
                military    REAL NOT NULL DEFAULT 0,
                economy     REAL NOT NULL DEFAULT 0,
                resources   TEXT NOT NULL DEFAULT '{}',
                day         INTEGER NOT NULL DEFAULT 0,
                race        TEXT NOT NULL DEFAULT '',
                hostility   INTEGER NOT NULL DEFAULT 0,
                last_sync   REAL NOT NULL DEFAULT 0,
                last_seen   REAL NOT NULL DEFAULT 0
            , pending_loot TEXT NOT NULL DEFAULT '{}', pending_delta TEXT NOT NULL DEFAULT '{}');
CREATE TABLE IF NOT EXISTS evolve_saves (
                user_id    INTEGER PRIMARY KEY,
                data       TEXT NOT NULL DEFAULT '',
                size       INTEGER NOT NULL DEFAULT 0,
                day        INTEGER NOT NULL DEFAULT 0,
                race       TEXT NOT NULL DEFAULT '',
                updated_at REAL NOT NULL DEFAULT 0
            );
CREATE TABLE IF NOT EXISTS evolve_trades (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                from_id     INTEGER NOT NULL,
                to_id       INTEGER NOT NULL,
                give_res    TEXT NOT NULL DEFAULT '',
                give_amt    REAL NOT NULL DEFAULT 0,
                want_res    TEXT NOT NULL DEFAULT '',
                want_amt    REAL NOT NULL DEFAULT 0,
                status      TEXT NOT NULL DEFAULT 'pending',
                created_at  REAL NOT NULL DEFAULT 0,
                done_at     REAL NOT NULL DEFAULT 0
            );
CREATE TABLE IF NOT EXISTS evolve_wars (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                attacker    INTEGER NOT NULL,
                defender    INTEGER NOT NULL,
                won         INTEGER NOT NULL DEFAULT 0,
                loot        TEXT NOT NULL DEFAULT '{}',
                created_at  REAL NOT NULL DEFAULT 0
            );
CREATE TABLE IF NOT EXISTS favorites (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER NOT NULL,
            post_id     INTEGER NOT NULL,
            created_at  REAL NOT NULL DEFAULT 0, target_type TEXT NOT NULL DEFAULT 'post', is_public INTEGER NOT NULL DEFAULT 0,
            UNIQUE(user_id, post_id)
        );
CREATE TABLE IF NOT EXISTS feedback (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    kind TEXT NOT NULL DEFAULT 'feedback',
    content TEXT NOT NULL DEFAULT '',
    image TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'open',
    reply TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS forum_sections (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            name           TEXT NOT NULL,
            description     TEXT DEFAULT '',
            sort_order     INTEGER NOT NULL DEFAULT 0,
            is_closed      INTEGER NOT NULL DEFAULT 0,
            is_hidden      INTEGER NOT NULL DEFAULT 0,
            is_announcement INTEGER NOT NULL DEFAULT 0,
            created_at     REAL NOT NULL DEFAULT 0
        );
CREATE TABLE IF NOT EXISTS friend_messages_sent (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER NOT NULL,
            target_id   INTEGER NOT NULL,
            count       INTEGER NOT NULL DEFAULT 0,
            UNIQUE(user_id, target_id)
        );
CREATE TABLE IF NOT EXISTS friends (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER NOT NULL,
            friend_id   INTEGER NOT NULL,
            status      INTEGER NOT NULL DEFAULT 0, -- 0: pending, 1: accepted
            created_at  REAL NOT NULL DEFAULT 0,
            UNIQUE(user_id, friend_id)
        );
CREATE TABLE IF NOT EXISTS gacha_logs (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id    INTEGER NOT NULL,
            rarity     TEXT NOT NULL,
            prize      TEXT NOT NULL,
            detail     TEXT NOT NULL DEFAULT '',
            created_at REAL NOT NULL DEFAULT 0
        );
CREATE TABLE IF NOT EXISTS game_achievements (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    game_type TEXT NOT NULL DEFAULT 'silence',
    achievement_id TEXT NOT NULL,
    unlocked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(user_id, game_type, achievement_id)
);
CREATE TABLE IF NOT EXISTS game_replays (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            game        TEXT NOT NULL,
            sub         TEXT NOT NULL DEFAULT '',
            user_id     INTEGER NOT NULL,
            username    TEXT NOT NULL DEFAULT '',
            score       INTEGER NOT NULL DEFAULT 0,
            kind        TEXT NOT NULL DEFAULT 'score',
            duration_ms INTEGER NOT NULL DEFAULT 0,
            frames      TEXT NOT NULL DEFAULT '[]',
            created_at  REAL NOT NULL DEFAULT 0
        );
CREATE TABLE IF NOT EXISTS game_saves (  id INTEGER PRIMARY KEY AUTOINCREMENT,  user_id INTEGER NOT NULL,  game TEXT NOT NULL,  slot INTEGER NOT NULL DEFAULT 1,  data TEXT NOT NULL DEFAULT '',  size INTEGER NOT NULL DEFAULT 0,  meta TEXT NOT NULL DEFAULT '{}',  updated_at REAL NOT NULL DEFAULT 0,  UNIQUE(user_id, game, slot));
CREATE TABLE IF NOT EXISTS game_saves_history (  id INTEGER PRIMARY KEY AUTOINCREMENT,  user_id INTEGER NOT NULL,  game TEXT NOT NULL,  slot INTEGER NOT NULL DEFAULT 1,  data TEXT NOT NULL DEFAULT '',  size INTEGER NOT NULL DEFAULT 0,  meta TEXT NOT NULL DEFAULT '{}',  created_at REAL NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS game_stats (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    game_type TEXT NOT NULL DEFAULT 'silence',
    total_achievements INTEGER DEFAULT 0,
    UNIQUE(user_id, game_type)
);
CREATE TABLE IF NOT EXISTS ghost_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
    track INTEGER NOT NULL, time_ms INTEGER NOT NULL,
    frames TEXT NOT NULL, frame_cnt INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS go_rooms (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL DEFAULT '围棋房间',
            board_size INTEGER NOT NULL DEFAULT 19,  -- 9 / 13 / 19 路
            owner_id INTEGER NOT NULL,
            guest_id INTEGER DEFAULT NULL,
            mode INTEGER NOT NULL DEFAULT 0,  -- 0=玩家vs玩家 1=玩家vsAI
            is_public INTEGER NOT NULL DEFAULT 1,  -- 1=公开可随机加入
            status TEXT NOT NULL DEFAULT 'waiting',  -- waiting/playing/finished
            board_state TEXT NOT NULL DEFAULT '',  -- N*N 字符串：. 空 / 1 黑 / 2 白
            turn_user_id INTEGER DEFAULT NULL,
            winner_id INTEGER DEFAULT NULL,
            score_json TEXT DEFAULT NULL,  -- 结束时 {'black':..,'white':..,'komi':..}
            last_move_x INTEGER DEFAULT NULL,
            last_move_y INTEGER DEFAULT NULL,
            passes INTEGER NOT NULL DEFAULT 0,  -- 连续 pass 数（双方连续 pass 结束）
            prev_board TEXT DEFAULT NULL,  -- 上一手后的盘面（打劫检测）
            created_at REAL NOT NULL DEFAULT 0,
            updated_at REAL NOT NULL DEFAULT 0
        , stats_awarded INTEGER NOT NULL DEFAULT 0, ai_name TEXT NOT NULL DEFAULT '', ai_level INTEGER NOT NULL DEFAULT 0, owner_fp TEXT NOT NULL DEFAULT '', guest_fp TEXT NOT NULL DEFAULT '', owner_ip TEXT NOT NULL DEFAULT '', guest_ip TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS gomoku_rooms (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL DEFAULT '五子棋房间',
            owner_id INTEGER NOT NULL,
            guest_id INTEGER DEFAULT NULL,
            mode INTEGER NOT NULL DEFAULT 0,  -- 0=玩家vs玩家 1=玩家vsAI
            is_public INTEGER NOT NULL DEFAULT 1,  -- 1=公开可随机加入
            status TEXT NOT NULL DEFAULT 'waiting',  -- waiting/playing/finished
            board_state TEXT NOT NULL DEFAULT '',  -- 15x15 字符串
            turn_user_id INTEGER DEFAULT NULL,
            winner_id INTEGER DEFAULT NULL,
            win_line TEXT DEFAULT NULL,  -- 5连坐标 JSON
            last_move_x INTEGER DEFAULT NULL,
            last_move_y INTEGER DEFAULT NULL,
            created_at REAL NOT NULL DEFAULT 0,
            updated_at REAL NOT NULL DEFAULT 0
        , stats_awarded INTEGER NOT NULL DEFAULT 0, ai_name TEXT NOT NULL DEFAULT '', ai_level INTEGER NOT NULL DEFAULT 0, owner_fp TEXT NOT NULL DEFAULT '', guest_fp TEXT NOT NULL DEFAULT '', owner_ip TEXT NOT NULL DEFAULT '', guest_ip TEXT NOT NULL DEFAULT '', uci_moves TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS group_announcement_reads (
            announcement_id INTEGER NOT NULL,
            user_id         INTEGER NOT NULL,
            read_at         REAL NOT NULL DEFAULT 0,
            UNIQUE(announcement_id, user_id)
        );
CREATE TABLE IF NOT EXISTS group_announcements (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            group_id    INTEGER NOT NULL,
            user_id     INTEGER NOT NULL,
            title       TEXT NOT NULL DEFAULT '',
            content     TEXT NOT NULL,
            require_read INTEGER NOT NULL DEFAULT 0,
            read_count  INTEGER NOT NULL DEFAULT 0,
            created_at  REAL NOT NULL DEFAULT 0
        );
CREATE TABLE IF NOT EXISTS group_essence (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            group_id    INTEGER NOT NULL,
            message_id  INTEGER NOT NULL,
            operator_id INTEGER NOT NULL,
            created_at  REAL NOT NULL DEFAULT 0,
            UNIQUE(group_id, message_id)
        );
CREATE TABLE IF NOT EXISTS group_files (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            group_id    INTEGER NOT NULL,
            user_id     INTEGER NOT NULL,
            username    TEXT NOT NULL,
            file_name   TEXT NOT NULL,
            file_url    TEXT NOT NULL,
            file_type   TEXT DEFAULT '',
            file_size   INTEGER NOT NULL DEFAULT 0,
            created_at  REAL NOT NULL DEFAULT 0
        , img_meta TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS group_join_requests (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            group_id    INTEGER NOT NULL,
            user_id     INTEGER NOT NULL,
            username    TEXT NOT NULL,
            status      TEXT NOT NULL DEFAULT 'pending', -- pending / approved / rejected
            message     TEXT DEFAULT '',
            reject_reason TEXT DEFAULT '',
            expired_at  REAL NOT NULL DEFAULT 0,
            created_at  REAL NOT NULL DEFAULT 0
        );
CREATE TABLE IF NOT EXISTS group_members (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            group_id    INTEGER NOT NULL,
            user_id     INTEGER NOT NULL,
            role        TEXT NOT NULL DEFAULT 'member', -- owner / admin / member / black
            muted_until  REAL NOT NULL DEFAULT 0,
            joined_at   REAL NOT NULL DEFAULT 0,
            group_nickname TEXT DEFAULT '',
            UNIQUE(group_id, user_id)
        );
CREATE TABLE IF NOT EXISTS group_messages (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            group_id    INTEGER NOT NULL,
            user_id     INTEGER NOT NULL,
            username    TEXT NOT NULL,
            content     TEXT NOT NULL DEFAULT '',
            file_url    TEXT DEFAULT NULL,
            file_name   TEXT DEFAULT NULL,
            file_type   TEXT DEFAULT NULL,
            is_system   INTEGER NOT NULL DEFAULT 0,
            is_deleted  INTEGER NOT NULL DEFAULT 0,
            created_at  REAL NOT NULL
        );
CREATE TABLE IF NOT EXISTS groups (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            name          TEXT NOT NULL,
            avatar        TEXT DEFAULT NULL,
            intro         TEXT DEFAULT '',
            category      TEXT DEFAULT '闲聊群',  -- 技术群/游戏群/闲聊群
            announcement  TEXT DEFAULT '',
            owner_id      INTEGER NOT NULL,
            join_mode     TEXT NOT NULL DEFAULT 'public', -- public / audit / private
            member_count  INTEGER NOT NULL DEFAULT 1,
            allow_file    INTEGER NOT NULL DEFAULT 1,
            allow_invite  INTEGER NOT NULL DEFAULT 1,
            all_muted     INTEGER NOT NULL DEFAULT 0,
            group_no      TEXT DEFAULT '',
            is_searchable INTEGER NOT NULL DEFAULT 1,
            created_at    REAL NOT NULL DEFAULT 0
        );
CREATE TABLE IF NOT EXISTS horn_announcements (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            content    TEXT NOT NULL,
            link       TEXT DEFAULT '',
            created_at REAL NOT NULL
        , username TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS ic_coders (
        user_id     INTEGER PRIMARY KEY,
        code        TEXT UNIQUE NOT NULL,
        created_at  REAL NOT NULL DEFAULT 0
    , fp_digest TEXT NOT NULL DEFAULT '', wechat TEXT);
CREATE TABLE IF NOT EXISTS ic_daily (
        user_id   INTEGER NOT NULL,
        day       TEXT NOT NULL,
        seconds   REAL NOT NULL DEFAULT 0,
        PRIMARY KEY (user_id, day)
    );
CREATE TABLE IF NOT EXISTS ic_invites (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        inviter_id    INTEGER NOT NULL,
        invitee_id    INTEGER NOT NULL,
        code          TEXT NOT NULL DEFAULT '',
        reg_ip        TEXT NOT NULL DEFAULT '',
        status        TEXT NOT NULL DEFAULT 'pending',
        day1          REAL NOT NULL DEFAULT 0,
        day2          REAL NOT NULL DEFAULT 0,
        streak_start  TEXT DEFAULT NULL,
        qualified_at  REAL DEFAULT NULL,
        reject_reason TEXT NOT NULL DEFAULT '',
        created_at    REAL NOT NULL DEFAULT 0,
        UNIQUE(invitee_id)
    );
CREATE TABLE IF NOT EXISTS ic_meta (
        key   TEXT PRIMARY KEY,
        value TEXT NOT NULL
    );
CREATE TABLE IF NOT EXISTS ic_settlements (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id     INTEGER NOT NULL,
        period      TEXT NOT NULL DEFAULT '',
        valid_count INTEGER NOT NULL DEFAULT 0,
        amount      REAL NOT NULL DEFAULT 0,
        status      TEXT NOT NULL DEFAULT 'pending',
        note        TEXT NOT NULL DEFAULT '',
        created_at  REAL NOT NULL DEFAULT 0,
        updated_at  REAL NOT NULL DEFAULT 0,
        UNIQUE(user_id, period)
    );
CREATE TABLE IF NOT EXISTS ip_geo (
            ip         TEXT PRIMARY KEY,
            country    TEXT,
            region     TEXT,
            city       TEXT,
            isp        TEXT,
            updated_at REAL
        );
CREATE TABLE IF NOT EXISTS jq_rooms (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL DEFAULT '揭棋房间',
            owner_id INTEGER NOT NULL,
            guest_id INTEGER DEFAULT NULL,
            mode INTEGER NOT NULL DEFAULT 0,  -- 0=玩家vs玩家 1=玩家vsAI
            is_public INTEGER NOT NULL DEFAULT 1,
            status TEXT NOT NULL DEFAULT 'waiting',  -- waiting/playing/finished
            board_state TEXT NOT NULL DEFAULT '',  -- 揭棋：display|truth（各 90 字符）
            turn_user_id INTEGER DEFAULT NULL,
            winner_id INTEGER DEFAULT NULL,
            last_move TEXT DEFAULT NULL,  -- "x1y1x2y2"
            uci_moves TEXT DEFAULT '',   -- 棋谱（UCI 坐标，空格分隔）
            move_text TEXT DEFAULT '[]', -- 中文着法记谱（JSON 数组）
            created_at REAL NOT NULL DEFAULT 0,
            updated_at REAL NOT NULL DEFAULT 0
        , stats_awarded INTEGER NOT NULL DEFAULT 0, ai_name TEXT NOT NULL DEFAULT '', ai_level INTEGER NOT NULL DEFAULT 0, prev_board TEXT NOT NULL DEFAULT '', owner_fp TEXT NOT NULL DEFAULT '', guest_fp TEXT NOT NULL DEFAULT '', owner_ip TEXT NOT NULL DEFAULT '', guest_ip TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS kanban_items (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            title      TEXT NOT NULL DEFAULT '',
            content    TEXT NOT NULL DEFAULT '',
            author_id  INTEGER NOT NULL DEFAULT 0,
            author_name TEXT NOT NULL DEFAULT '',
            is_pinned  INTEGER NOT NULL DEFAULT 0,
            created_at REAL NOT NULL DEFAULT 0,
            updated_at REAL NOT NULL DEFAULT 0
        );
CREATE TABLE IF NOT EXISTS kubition_fights (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                attacker    INTEGER NOT NULL,
                defender    INTEGER NOT NULL,
                won         INTEGER NOT NULL DEFAULT 0,
                created_at  REAL NOT NULL DEFAULT 0
            );
CREATE TABLE IF NOT EXISTS kubition_graves (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id    INTEGER NOT NULL,
                place      TEXT NOT NULL DEFAULT '',
                place_name TEXT NOT NULL DEFAULT '',
                day        INTEGER NOT NULL DEFAULT 0,
                cause      TEXT NOT NULL DEFAULT '',
                words      TEXT NOT NULL DEFAULT '',
                created_at REAL NOT NULL DEFAULT 0
            );
CREATE TABLE IF NOT EXISTS kubition_players (
                user_id     INTEGER PRIMARY KEY,
                name        TEXT NOT NULL DEFAULT '',
                level       INTEGER NOT NULL DEFAULT 1,
                atk         INTEGER NOT NULL DEFAULT 10,
                def         INTEGER NOT NULL DEFAULT 0,
                max_hp      INTEGER NOT NULL DEFAULT 100,
                hp          INTEGER NOT NULL DEFAULT 100,
                day         INTEGER NOT NULL DEFAULT 1,
                season      TEXT NOT NULL DEFAULT '',
                generation  INTEGER NOT NULL DEFAULT 0,
                wins        INTEGER NOT NULL DEFAULT 0,
                losses      INTEGER NOT NULL DEFAULT 0,
                last_sync   REAL NOT NULL DEFAULT 0
            );
CREATE TABLE IF NOT EXISTS kubition_saves (
                user_id    INTEGER PRIMARY KEY,
                data       TEXT NOT NULL DEFAULT '',
                size       INTEGER NOT NULL DEFAULT 0,
                day        INTEGER NOT NULL DEFAULT 0,
                slot       INTEGER NOT NULL DEFAULT 1,
                updated_at REAL NOT NULL DEFAULT 0
            );
CREATE TABLE IF NOT EXISTS kubition_saves_history (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id    INTEGER NOT NULL,
                slot       INTEGER NOT NULL DEFAULT 1,
                data       TEXT NOT NULL DEFAULT '',
                size       INTEGER NOT NULL DEFAULT 0,
                day        INTEGER NOT NULL DEFAULT 0,
                created_at REAL NOT NULL DEFAULT 0
            );
CREATE TABLE IF NOT EXISTS mall_items (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            name        TEXT NOT NULL,
            type        TEXT NOT NULL,           -- title/bubble/effect/badge/consumable
            value       TEXT NOT NULL,           -- 生效值
            cost        INTEGER NOT NULL DEFAULT 0,
            icon        TEXT NOT NULL DEFAULT '🎁',
            desc        TEXT NOT NULL DEFAULT '',
            sort_order  INTEGER NOT NULL DEFAULT 0,
            limited     INTEGER NOT NULL DEFAULT 0,
            removable   INTEGER NOT NULL DEFAULT 1,
            gacha_only  INTEGER NOT NULL DEFAULT 0
        );
CREATE TABLE IF NOT EXISTS messages (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id    INTEGER NOT NULL,
            username   TEXT NOT NULL,
            content    TEXT NOT NULL,
            file_url   TEXT DEFAULT NULL,
            file_name  TEXT DEFAULT NULL,
            file_type  TEXT DEFAULT NULL,
            is_private INTEGER NOT NULL DEFAULT 0,
            receiver_id INTEGER DEFAULT NULL,
            created_at REAL NOT NULL,
            is_deleted INTEGER NOT NULL DEFAULT 0
        , room_id INTEGER NOT NULL DEFAULT 1, quote_id INTEGER DEFAULT NULL, mentions TEXT DEFAULT '', edited INTEGER NOT NULL DEFAULT 0, edit_history TEXT DEFAULT '', read_at REAL NOT NULL DEFAULT 0, img_meta TEXT DEFAULT '', idem_key TEXT, delivered_at REAL NOT NULL DEFAULT 0);
CREATE VIRTUAL TABLE messages_fts USING fts5(content, tokenize='trigram');
CREATE TABLE IF NOT EXISTS 'messages_fts_config'(k PRIMARY KEY, v) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS 'messages_fts_content'(id INTEGER PRIMARY KEY, c0);
CREATE TABLE IF NOT EXISTS 'messages_fts_data'(id INTEGER PRIMARY KEY, block BLOB);
CREATE TABLE IF NOT EXISTS 'messages_fts_docsize'(id INTEGER PRIMARY KEY, sz BLOB);
CREATE TABLE IF NOT EXISTS 'messages_fts_idx'(segid, term, pgno, PRIMARY KEY(segid, term)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS monitor_samples (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            ts            REAL NOT NULL,
            cpu_pct       REAL,
            load1         REAL,
            load5         REAL,
            load15        REAL,
            mem_used_mb   INTEGER,
            mem_total_mb  INTEGER,
            swap_used_mb  INTEGER,
            swap_total_mb INTEGER,
            disk_used_gb  REAL,
            disk_total_gb REAL,
            net_rx_kb     INTEGER,
            net_tx_kb     INTEGER,
            conn_estab    INTEGER,
            proc_count    INTEGER,
            uptime_sec    INTEGER,
            app_threads   INTEGER,
            req_count     INTEGER,
            req_avg_ms    INTEGER
        );
CREATE TABLE IF NOT EXISTS music_play_logs (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id    INTEGER NOT NULL,
            track_id   INTEGER NOT NULL,
            title      TEXT DEFAULT '',
            artist     TEXT DEFAULT '',
            played_ms  INTEGER NOT NULL DEFAULT 0,
            finished   INTEGER NOT NULL DEFAULT 0,
            created_at REAL NOT NULL
        );
CREATE TABLE IF NOT EXISTS music_playlist (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id    INTEGER NOT NULL,
            track_id   INTEGER NOT NULL,
            position   INTEGER DEFAULT 0,
            added_at   REAL NOT NULL,
            UNIQUE(user_id, track_id)
        );
CREATE TABLE IF NOT EXISTS music_playlist_songs (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            playlist_id INTEGER NOT NULL DEFAULT 0,
            song_key    TEXT NOT NULL DEFAULT '',
            song_id     INTEGER NOT NULL DEFAULT 0,
            source      TEXT NOT NULL DEFAULT 'local',
            platform_id TEXT NOT NULL DEFAULT '',
            title       TEXT NOT NULL DEFAULT '',
            artist      TEXT NOT NULL DEFAULT '',
            cover       TEXT NOT NULL DEFAULT '',
            keyword     TEXT NOT NULL DEFAULT '',
            sort_order  INTEGER NOT NULL DEFAULT 0,
            created_at  REAL NOT NULL DEFAULT 0,
            UNIQUE(playlist_id, song_key)
        );
CREATE TABLE IF NOT EXISTS music_playlists (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id    INTEGER NOT NULL DEFAULT 0,
            name       TEXT NOT NULL DEFAULT '',
            is_fav     INTEGER NOT NULL DEFAULT 0,
            created_at REAL NOT NULL DEFAULT 0
        );
CREATE TABLE IF NOT EXISTS music_songs (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            title         TEXT NOT NULL DEFAULT '',
            artist        TEXT NOT NULL DEFAULT '',
            duration      INTEGER NOT NULL DEFAULT 0,
            file_name     TEXT NOT NULL DEFAULT '',
            orig_name     TEXT NOT NULL DEFAULT '',
            size          INTEGER NOT NULL DEFAULT 0,
            uploader_id   INTEGER NOT NULL DEFAULT 0,
            uploader_name TEXT NOT NULL DEFAULT '',
            created_at    REAL NOT NULL DEFAULT 0
        );
CREATE TABLE IF NOT EXISTS music_tracks (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id    INTEGER NOT NULL,
            username   TEXT DEFAULT '',
            title      TEXT NOT NULL,
            artist     TEXT DEFAULT '',
            filename   TEXT NOT NULL,
            size       INTEGER DEFAULT 0,
            duration   REAL DEFAULT 0,
            created_at REAL NOT NULL
        , plays INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS notif_prefs (
                user_id INTEGER NOT NULL,
                cat_key TEXT NOT NULL,
                created_at REAL NOT NULL,
                PRIMARY KEY (user_id, cat_key)
            );
CREATE TABLE IF NOT EXISTS notifications (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER NOT NULL,
            type        TEXT NOT NULL,
            content     TEXT NOT NULL,
            link_url    TEXT DEFAULT NULL,
            is_read     INTEGER NOT NULL DEFAULT 0,
            created_at  REAL NOT NULL
        );
CREATE TABLE IF NOT EXISTS oj_contests (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            title       TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            start_at    REAL NOT NULL DEFAULT 0,
            end_at      REAL NOT NULL DEFAULT 0,
            is_public   INTEGER NOT NULL DEFAULT 1,
            created_by  INTEGER NOT NULL DEFAULT 0,
            created_at  REAL NOT NULL DEFAULT 0
        , rule TEXT NOT NULL DEFAULT 'ioi');
CREATE TABLE IF NOT EXISTS oj_problems (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            contest_id      INTEGER NOT NULL DEFAULT 0,
            title           TEXT NOT NULL,
            description     TEXT NOT NULL DEFAULT '',
            input_format    TEXT NOT NULL DEFAULT '',
            output_format   TEXT NOT NULL DEFAULT '',
            samples         TEXT NOT NULL DEFAULT '[]',
            hint            TEXT NOT NULL DEFAULT '',
            time_limit_ms   INTEGER NOT NULL DEFAULT 1000,
            memory_limit_mb INTEGER NOT NULL DEFAULT 256,
            test_count      INTEGER NOT NULL DEFAULT 0,
            created_by      INTEGER NOT NULL DEFAULT 0,
            created_at      REAL NOT NULL DEFAULT 0
        , difficulty TEXT NOT NULL DEFAULT '', tags TEXT NOT NULL DEFAULT '', visibility TEXT NOT NULL DEFAULT 'public', status TEXT NOT NULL DEFAULT 'approved', reviewer_id INTEGER NOT NULL DEFAULT 0, reviewed_at REAL NOT NULL DEFAULT 0, reject_reason TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS oj_submissions (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            problem_id  INTEGER NOT NULL,
            contest_id  INTEGER NOT NULL DEFAULT 0,
            user_id     INTEGER NOT NULL,
            code        TEXT NOT NULL DEFAULT '',
            verdict     TEXT NOT NULL DEFAULT '',
            time_ms     INTEGER NOT NULL DEFAULT 0,
            memory_kb   INTEGER NOT NULL DEFAULT 0,
            passed      INTEGER NOT NULL DEFAULT 0,
            total       INTEGER NOT NULL DEFAULT 0,
            created_at  REAL NOT NULL DEFAULT 0
        , score INTEGER NOT NULL DEFAULT 0, full_score INTEGER NOT NULL DEFAULT 0, task_id TEXT NOT NULL DEFAULT '', detail TEXT NOT NULL DEFAULT '', lang TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS oj_testcases (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            problem_id  INTEGER NOT NULL,
            idx         INTEGER NOT NULL DEFAULT 1,
            input       TEXT NOT NULL DEFAULT '',
            expected    TEXT NOT NULL DEFAULT '',
            is_sample   INTEGER NOT NULL DEFAULT 0
        , time_limit_ms INTEGER NOT NULL DEFAULT 0, memory_limit_mb INTEGER NOT NULL DEFAULT 0, score INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS page_blocks (
            page       TEXT NOT NULL,
            key        TEXT NOT NULL,
            content    TEXT NOT NULL DEFAULT '',
            updated_at REAL NOT NULL DEFAULT 0,
            updated_by TEXT DEFAULT '',
            PRIMARY KEY (page, key)
        );
CREATE TABLE IF NOT EXISTS playlist_tracks (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            playlist_id INTEGER NOT NULL,
            track_id    INTEGER NOT NULL,
            position    INTEGER NOT NULL DEFAULT 0,
            added_at    REAL NOT NULL DEFAULT 0
        );
CREATE TABLE IF NOT EXISTS playlists (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id    INTEGER NOT NULL,
            username   TEXT NOT NULL DEFAULT '',
            name       TEXT NOT NULL,
            is_public  INTEGER NOT NULL DEFAULT 1,
            created_at REAL NOT NULL DEFAULT 0
        );
CREATE TABLE IF NOT EXISTS points_log (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER NOT NULL,
            delta       INTEGER NOT NULL,        -- 正加负减
            source_type TEXT NOT NULL DEFAULT '',
            reason      TEXT NOT NULL DEFAULT '',
            ref_id      TEXT DEFAULT NULL,       -- 关联业务 id
            admin_id    INTEGER DEFAULT NULL,    -- 人工操作留痕
            created_at  REAL NOT NULL DEFAULT 0
        , idem_key TEXT);
CREATE TABLE IF NOT EXISTS poll_options (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            poll_id     INTEGER NOT NULL,
            text        TEXT NOT NULL,
            votes       INTEGER NOT NULL DEFAULT 0
        );
CREATE TABLE IF NOT EXISTS poll_votes (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            poll_id     INTEGER NOT NULL,
            option_id   INTEGER NOT NULL,
            user_id     INTEGER NOT NULL,
            created_at  REAL NOT NULL DEFAULT 0,
            UNIQUE(poll_id, user_id)
        );
CREATE TABLE IF NOT EXISTS polls (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            post_id     INTEGER NOT NULL,
            title       TEXT NOT NULL,
            mode        TEXT NOT NULL DEFAULT 'single', -- single / multi
            anonymous   INTEGER NOT NULL DEFAULT 0,
            deadline    REAL DEFAULT 0,              -- 0 表示永久
            closed      INTEGER NOT NULL DEFAULT 0,
            created_at  REAL NOT NULL DEFAULT 0
        );
CREATE TABLE IF NOT EXISTS post_comments (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            post_id     INTEGER NOT NULL,
            user_id     INTEGER NOT NULL,
            username    TEXT NOT NULL,
            content     TEXT NOT NULL,
            created_at  REAL NOT NULL
        , parent_id INTEGER DEFAULT NULL, like_count INTEGER NOT NULL DEFAULT 0, is_deleted INTEGER NOT NULL DEFAULT 0, edited INTEGER NOT NULL DEFAULT 0, root_id INTEGER DEFAULT NULL);
CREATE TABLE IF NOT EXISTS post_likes (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            post_id     INTEGER NOT NULL,
            user_id     INTEGER NOT NULL,
            created_at  REAL NOT NULL DEFAULT 0,
            UNIQUE(post_id, user_id)
        );
CREATE TABLE IF NOT EXISTS post_tags (
            id       INTEGER PRIMARY KEY AUTOINCREMENT,
            post_id  INTEGER NOT NULL,
            tag      TEXT NOT NULL
        );
CREATE TABLE IF NOT EXISTS post_vecs (
            post_id    INTEGER PRIMARY KEY,
            vec        BLOB NOT NULL,
            dim        INTEGER NOT NULL DEFAULT 384,
            updated_at REAL NOT NULL DEFAULT 0
        );
CREATE TABLE IF NOT EXISTS post_views (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id   INTEGER NOT NULL,
            post_id   INTEGER NOT NULL,
            created_at REAL NOT NULL DEFAULT 0,
            UNIQUE(user_id, post_id)
        );
CREATE TABLE IF NOT EXISTS posts (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER NOT NULL,
            username    TEXT NOT NULL,
            title       TEXT NOT NULL,
            content     TEXT NOT NULL,
            likes       INTEGER NOT NULL DEFAULT 0,
            is_pinned   INTEGER NOT NULL DEFAULT 0,
            is_locked   INTEGER NOT NULL DEFAULT 0,
            created_at  REAL NOT NULL,
            updated_at  REAL NOT NULL
        , section_id INTEGER NOT NULL DEFAULT 1, tags TEXT DEFAULT '', is_essence INTEGER NOT NULL DEFAULT 0, view_count INTEGER NOT NULL DEFAULT 0, comment_count INTEGER NOT NULL DEFAULT 0, last_reply_at REAL NOT NULL DEFAULT 0, edited INTEGER NOT NULL DEFAULT 0, is_deleted INTEGER NOT NULL DEFAULT 0, recycle INTEGER NOT NULL DEFAULT 0, summary TEXT NOT NULL DEFAULT '', title_glow INTEGER NOT NULL DEFAULT 0, user_pin_until REAL NOT NULL DEFAULT 0, thread_id INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS posts_tags (
            post_id INTEGER NOT NULL,
            tag_id INTEGER NOT NULL,
            PRIMARY KEY (post_id, tag_id)
        );
CREATE TABLE IF NOT EXISTS punishment_records (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id      INTEGER NOT NULL,
            username     TEXT NOT NULL,
            admin_id     INTEGER NOT NULL,
            admin_name   TEXT NOT NULL,
            ptype        TEXT NOT NULL,            -- ban / mute
            until        REAL NOT NULL,             -- PERMANENT(-1) 或时间戳
            duration_text TEXT DEFAULT '',
            reason        TEXT DEFAULT '',
            status        TEXT NOT NULL DEFAULT 'effective',  -- effective / lifted / revoked
            lifted_at    REAL DEFAULT NULL,
            lift_reason   TEXT DEFAULT '',
            created_at   REAL NOT NULL DEFAULT 0
        );
CREATE TABLE IF NOT EXISTS recall_logs (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            operator_id   INTEGER NOT NULL,
            operator_name TEXT NOT NULL,
            message_id    INTEGER NOT NULL,
            scene         TEXT NOT NULL,   -- chat / private
            content       TEXT DEFAULT '',
            recalled_at   REAL NOT NULL
        );
CREATE TABLE IF NOT EXISTS reports (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            reporter_id    INTEGER NOT NULL,
            reporter_name  TEXT NOT NULL,
            target_type    TEXT NOT NULL,   -- chat / private / post / comment
            target_id      INTEGER NOT NULL,
            snapshot       TEXT DEFAULT '',
            category       TEXT DEFAULT '',
            status         TEXT NOT NULL DEFAULT 'pending',  -- pending / ignored / punished / escalated
            handler_id     INTEGER DEFAULT NULL,
            handler_note   TEXT DEFAULT '',
            created_at     REAL NOT NULL,
            handled_at     REAL DEFAULT NULL
        , reason TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS reversi_rooms (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL DEFAULT '黑白棋房间',
            owner_id INTEGER NOT NULL,
            guest_id INTEGER DEFAULT NULL,
            mode INTEGER NOT NULL DEFAULT 0,  -- 黑白棋仅 PvP（0）
            is_public INTEGER NOT NULL DEFAULT 1,
            status TEXT NOT NULL DEFAULT 'waiting',  -- waiting/playing/finished
            board_state TEXT NOT NULL DEFAULT '',  -- 8x8 字符串（'.'空 / '1'黑 / '2'白）
            turn_user_id INTEGER DEFAULT NULL,
            winner_id INTEGER DEFAULT NULL,
            last_move TEXT DEFAULT NULL,  -- "x,y"
            stats_awarded INTEGER NOT NULL DEFAULT 0,
            created_at REAL NOT NULL DEFAULT 0,
            updated_at REAL NOT NULL DEFAULT 0
        , move_hist TEXT NOT NULL DEFAULT '', ai_name TEXT NOT NULL DEFAULT '', ai_level INTEGER NOT NULL DEFAULT 0, owner_fp TEXT NOT NULL DEFAULT '', guest_fp TEXT NOT NULL DEFAULT '', owner_ip TEXT NOT NULL DEFAULT '', guest_ip TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS runner_best (
    user_id INTEGER NOT NULL, track INTEGER NOT NULL,
    time_ms INTEGER NOT NULL, run_id INTEGER NOT NULL DEFAULT 0,
    updated_at REAL NOT NULL DEFAULT 0, PRIMARY KEY (user_id, track));
CREATE TABLE IF NOT EXISTS sensitive_words (
            id       INTEGER PRIMARY KEY AUTOINCREMENT,
            word     TEXT NOT NULL UNIQUE,
            category TEXT DEFAULT '默认'
        );
CREATE TABLE IF NOT EXISTS sign_logs (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER NOT NULL,
            sign_date   TEXT NOT NULL,          -- 'YYYY-MM-DD'
            is_makeup   INTEGER NOT NULL DEFAULT 0,
            reward_exp  INTEGER NOT NULL DEFAULT 0,
            reward_coin INTEGER NOT NULL DEFAULT 0,
            bonus_coin  INTEGER NOT NULL DEFAULT 0,
            created_at  REAL NOT NULL DEFAULT 0,
            UNIQUE(user_id, sign_date)
        );
CREATE TABLE IF NOT EXISTS silence_save (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER UNIQUE NOT NULL,
    current_scene TEXT NOT NULL DEFAULT 'start',
    inventory TEXT NOT NULL DEFAULT '[]',
    flags TEXT NOT NULL DEFAULT '{}',
    playthrough INTEGER NOT NULL DEFAULT 1,
    sanity INTEGER NOT NULL DEFAULT 100,
    update_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS silence_user_meta (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER UNIQUE NOT NULL,
    achievements TEXT NOT NULL DEFAULT '[]',
    inherit_items TEXT NOT NULL DEFAULT '[]',
    update_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS site_announcement (
            id           INTEGER PRIMARY KEY,
            content      TEXT DEFAULT '',
            updated_at   REAL DEFAULT 0,
            updated_by   INTEGER DEFAULT 0,
            pinned_until REAL DEFAULT 0
        );
CREATE TABLE IF NOT EXISTS site_config (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
CREATE TABLE IF NOT EXISTS survey_questions (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                survey_id INTEGER NOT NULL,
                sort      INTEGER NOT NULL DEFAULT 0,
                qtype     TEXT NOT NULL DEFAULT 'single',
                title     TEXT NOT NULL DEFAULT '',
                options   TEXT NOT NULL DEFAULT '[]',
                required  INTEGER NOT NULL DEFAULT 1
            );
CREATE TABLE IF NOT EXISTS survey_responses (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                survey_id  INTEGER NOT NULL,
                user_id    INTEGER NOT NULL,
                answers    TEXT NOT NULL DEFAULT '{}',
                created_at REAL NOT NULL DEFAULT 0
            );
CREATE TABLE IF NOT EXISTS surveys (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                title       TEXT NOT NULL DEFAULT '',
                intro       TEXT NOT NULL DEFAULT '',
                enabled     INTEGER NOT NULL DEFAULT 0,
                once_only   INTEGER NOT NULL DEFAULT 1,
                created_by  INTEGER NOT NULL DEFAULT 0,
                created_at  REAL NOT NULL DEFAULT 0
            );
CREATE TABLE IF NOT EXISTS tag_hot (
            day TEXT NOT NULL,
            tag_id INTEGER NOT NULL,
            post_cnt INTEGER NOT NULL DEFAULT 0,
            interact_cnt INTEGER NOT NULL DEFAULT 0,
            score REAL NOT NULL DEFAULT 0,
            PRIMARY KEY (day, tag_id)
        );
CREATE TABLE IF NOT EXISTS tag_subs (
            user_id INTEGER NOT NULL,
            tag_id INTEGER NOT NULL,
            created_at REAL NOT NULL,
            PRIMARY KEY (user_id, tag_id)
        );
CREATE TABLE IF NOT EXISTS tags (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            use_count INTEGER NOT NULL DEFAULT 0,
            created_at REAL NOT NULL
        );
CREATE TABLE IF NOT EXISTS thread_subs (
            user_id INTEGER NOT NULL,
            thread_id INTEGER NOT NULL,
            created_at REAL NOT NULL,
            PRIMARY KEY (user_id, thread_id)
        );
CREATE TABLE IF NOT EXISTS threads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            board_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            slug TEXT UNIQUE NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            post_count INTEGER NOT NULL DEFAULT 0,
            sort_order INTEGER NOT NULL DEFAULT 0,
            is_default INTEGER NOT NULL DEFAULT 0,
            created_at REAL NOT NULL
        );
CREATE TABLE IF NOT EXISTS user_actions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL DEFAULT 0,
                action TEXT NOT NULL,
                target_type TEXT NOT NULL DEFAULT '',
                target_id INTEGER NOT NULL DEFAULT 0,
                weight INTEGER NOT NULL DEFAULT 1,
                ip TEXT NOT NULL DEFAULT '',
                created_at REAL NOT NULL
            );
CREATE TABLE IF NOT EXISTS user_consumables (
            id       INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id  INTEGER NOT NULL,
            item_key TEXT NOT NULL,
            count    INTEGER NOT NULL DEFAULT 0,
            UNIQUE(user_id, item_key)
        );
CREATE TABLE IF NOT EXISTS user_files (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER NOT NULL,
            username    TEXT NOT NULL DEFAULT '',
            filename    TEXT NOT NULL,
            stored_name TEXT NOT NULL,
            size        INTEGER NOT NULL DEFAULT 0,
            mime        TEXT DEFAULT '',
            share_token TEXT DEFAULT NULL,
            downloads   INTEGER NOT NULL DEFAULT 0,
            created_at  REAL NOT NULL DEFAULT 0
        , share_expire INTEGER NOT NULL DEFAULT 0, share_pass TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS user_items (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id    INTEGER NOT NULL,
            item_id    INTEGER NOT NULL,
            redeemed_at REAL NOT NULL DEFAULT 0,
            is_equipped INTEGER NOT NULL DEFAULT 0,
            UNIQUE(user_id, item_id)
        );
CREATE TABLE IF NOT EXISTS user_tasks (
            id       INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id  INTEGER NOT NULL,
            task_key TEXT NOT NULL,
            period   TEXT DEFAULT NULL,          -- daily=日期 / once / ach
            progress INTEGER NOT NULL DEFAULT 0,
            claimed  INTEGER NOT NULL DEFAULT 0,
            UNIQUE(user_id, task_key, period)
        );
CREATE TABLE IF NOT EXISTS users (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            username      TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            salt          TEXT NOT NULL,
            avatar        TEXT DEFAULT NULL,
            is_admin      INTEGER NOT NULL DEFAULT 0,
            has_effect    INTEGER NOT NULL DEFAULT 0,
            effect_type   TEXT DEFAULT NULL,
            banned_until  REAL NOT NULL DEFAULT 0,
            muted_until   REAL NOT NULL DEFAULT 0,
            created_at    REAL NOT NULL DEFAULT 0,
            last_active   REAL NOT NULL DEFAULT 0,
            badge_text    TEXT DEFAULT NULL,
            badge_effect  TEXT DEFAULT NULL
        , is_super_admin INTEGER NOT NULL DEFAULT 0, signature TEXT DEFAULT '', private_chat_perm INTEGER NOT NULL DEFAULT 1, level INTEGER NOT NULL DEFAULT 1, exp INTEGER NOT NULL DEFAULT 0, custom_title TEXT DEFAULT '', total_likes INTEGER NOT NULL DEFAULT 0, coins INTEGER NOT NULL DEFAULT 0, mall_title TEXT DEFAULT '', mall_badge TEXT DEFAULT '', bubble_style TEXT DEFAULT '', gacha_pity INTEGER NOT NULL DEFAULT 0, buff_double_until REAL NOT NULL DEFAULT 0, buff_exp_until REAL NOT NULL DEFAULT 0, custom_name_color TEXT DEFAULT '', shield_until REAL NOT NULL DEFAULT 0, fortune_salt TEXT DEFAULT '', high_score_24 INTEGER DEFAULT 0, high_score_sudoku INTEGER DEFAULT 0, high_score_klotski INTEGER DEFAULT 0, high_score_color_blind INTEGER DEFAULT 0, high_score_jump INTEGER DEFAULT 0, best_time_klotski_4 INTEGER DEFAULT NULL, best_time_klotski_5 INTEGER DEFAULT NULL, best_time_klotski_6 INTEGER DEFAULT NULL, best_time_klotski_cn_1 INTEGER DEFAULT NULL, best_time_klotski_cn_2 INTEGER DEFAULT NULL, best_time_klotski_cn_3 INTEGER DEFAULT NULL, best_time_klotski_cn_4 INTEGER DEFAULT NULL, password_ext TEXT DEFAULT NULL, ai_quota INTEGER NOT NULL DEFAULT 30, ai_quota_reset REAL NOT NULL DEFAULT 0, high_score_xianyi INTEGER NOT NULL DEFAULT 0, high_score_hanoi INTEGER NOT NULL DEFAULT 0, hanoi_best_moves INTEGER NOT NULL DEFAULT 0, hanoi_best_ms INTEGER NOT NULL DEFAULT 0, onboarded INTEGER NOT NULL DEFAULT 0, user_no TEXT DEFAULT NULL, deactivated INTEGER NOT NULL DEFAULT 0, last_checkin TEXT DEFAULT '', checkin_streak INTEGER NOT NULL DEFAULT 0, oj_verified INTEGER NOT NULL DEFAULT 0, oj_verified_at REAL DEFAULT 0, mine_best_ms_beginner INTEGER NOT NULL DEFAULT 0, mine_best_ms_intermediate INTEGER NOT NULL DEFAULT 0, mine_best_ms_expert INTEGER NOT NULL DEFAULT 0, buff_double_uses INTEGER NOT NULL DEFAULT 0, ai_debug_left INTEGER NOT NULL DEFAULT 20, email TEXT NOT NULL DEFAULT '', email_verified INTEGER NOT NULL DEFAULT 0, high_score_tetris INTEGER NOT NULL DEFAULT 0, high_score_2048 INTEGER NOT NULL DEFAULT 0, show_online INTEGER NOT NULL DEFAULT 1, github_id TEXT, github_login TEXT, ai_balance REAL NOT NULL DEFAULT 0.1, max_tile_2048 INTEGER NOT NULL DEFAULT 0, sudoku_perfect INTEGER NOT NULL DEFAULT 0, jump_center_streak INTEGER NOT NULL DEFAULT 0, mine_noflag_expert INTEGER NOT NULL DEFAULT 0, notice_seen_date TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS warning_logs (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER NOT NULL,
            username    TEXT NOT NULL,
            scene       TEXT NOT NULL,
            content     TEXT DEFAULT '',
            created_at  REAL NOT NULL
        );
CREATE TABLE IF NOT EXISTS xq_puzzle_done (
            user_id INTEGER NOT NULL,
            puzzle_id INTEGER NOT NULL,
            solved_at REAL NOT NULL DEFAULT 0,
            PRIMARY KEY (user_id, puzzle_id)
        );
CREATE TABLE IF NOT EXISTS xq_puzzles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            fen TEXT NOT NULL,
            source TEXT NOT NULL DEFAULT '',      -- 数据来源：内置集合名 / '用户创作'
            diff INTEGER NOT NULL DEFAULT 2,      -- 难度 1~5（1 最简单）
            author_id INTEGER NOT NULL DEFAULT 0, -- 0=系统
            author_name TEXT NOT NULL DEFAULT '系统',
            intro TEXT NOT NULL DEFAULT '',
            best_uci TEXT NOT NULL DEFAULT '',    -- 引擎验证出的红方最优首着（提示用）
            mate_len INTEGER NOT NULL DEFAULT 0,  -- 引擎自对弈红方取胜的回合数（约）
            status TEXT NOT NULL DEFAULT 'approved',  -- approved=公开 / pending / rejected
            plays INTEGER NOT NULL DEFAULT 0,
            solves INTEGER NOT NULL DEFAULT 0,
            created_at REAL NOT NULL DEFAULT 0
        );
CREATE TABLE IF NOT EXISTS xq_rooms (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL DEFAULT '象棋房间',
            owner_id INTEGER NOT NULL,
            guest_id INTEGER DEFAULT NULL,
            mode INTEGER NOT NULL DEFAULT 0,  -- 0=玩家vs玩家 1=玩家vsAI
            is_public INTEGER NOT NULL DEFAULT 1,
            status TEXT NOT NULL DEFAULT 'waiting',  -- waiting/playing/finished
            board_state TEXT NOT NULL DEFAULT '',  -- 9x10 棋子编码（90 字符）
            turn_user_id INTEGER DEFAULT NULL,
            winner_id INTEGER DEFAULT NULL,
            last_move TEXT DEFAULT NULL,  -- "x1y1x2y2"
            uci_moves TEXT DEFAULT '',   -- Pikafish 棋谱（UCI 坐标，空格分隔）
            created_at REAL NOT NULL DEFAULT 0,
            updated_at REAL NOT NULL DEFAULT 0
        , stats_awarded INTEGER NOT NULL DEFAULT 0, ai_name TEXT NOT NULL DEFAULT '', ai_level INTEGER NOT NULL DEFAULT 0, owner_fp TEXT NOT NULL DEFAULT '', guest_fp TEXT NOT NULL DEFAULT '', owner_ip TEXT NOT NULL DEFAULT '', guest_ip TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS xqm_games (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            red_id       INTEGER NOT NULL,
            black_id     INTEGER NOT NULL,
            state        TEXT NOT NULL DEFAULT '{}',
            status       TEXT NOT NULL DEFAULT 'playing',
            winner       TEXT,
            reason       TEXT DEFAULT '',
            created_at   REAL NOT NULL DEFAULT 0,
            updated_at   REAL NOT NULL DEFAULT 0,
            last_move_at REAL NOT NULL DEFAULT 0
        , loot TEXT NOT NULL DEFAULT '', log TEXT NOT NULL DEFAULT '', red_fp TEXT NOT NULL DEFAULT '', black_fp TEXT NOT NULL DEFAULT '', red_ip TEXT NOT NULL DEFAULT '', black_ip TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS xqm_lineup (
    user_id    INTEGER PRIMARY KEY,
    layout     TEXT NOT NULL DEFAULT '[]',
    updated_at REAL NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS xqm_queue (
            user_id   INTEGER PRIMARY KEY,
            rating    INTEGER NOT NULL DEFAULT 1000,
            joined_at REAL NOT NULL DEFAULT 0,
            last_poll REAL NOT NULL DEFAULT 0
        , fp TEXT NOT NULL DEFAULT '', ip TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS xqm_rating (
            user_id    INTEGER PRIMARY KEY,
            rating     INTEGER NOT NULL DEFAULT 1000,
            wins       INTEGER NOT NULL DEFAULT 0,
            losses     INTEGER NOT NULL DEFAULT 0,
            draws      INTEGER NOT NULL DEFAULT 0,
            updated_at REAL NOT NULL DEFAULT 0
        );
CREATE TABLE IF NOT EXISTS xqm_season (
            id         INTEGER PRIMARY KEY,
            name       TEXT NOT NULL DEFAULT '',
            start_at   REAL NOT NULL DEFAULT 0,
            end_at     REAL NOT NULL DEFAULT 0,
            closed     INTEGER NOT NULL DEFAULT 0
        );
CREATE TABLE IF NOT EXISTS xqm_season_hist (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            season_id   INTEGER NOT NULL,
            season_name TEXT NOT NULL DEFAULT '',
            user_id     INTEGER NOT NULL,
            rating      INTEGER NOT NULL DEFAULT 0,
            wins        INTEGER NOT NULL DEFAULT 0,
            losses      INTEGER NOT NULL DEFAULT 0,
            draws       INTEGER NOT NULL DEFAULT 0,
            rank        INTEGER NOT NULL DEFAULT 0,
            created_at  REAL NOT NULL DEFAULT 0
        );
CREATE TABLE IF NOT EXISTS xqm_shards (
    user_id INTEGER NOT NULL, type TEXT NOT NULL,
    shards INTEGER NOT NULL DEFAULT 0, level INTEGER NOT NULL DEFAULT 1,
    updated_at REAL NOT NULL DEFAULT 0, PRIMARY KEY (user_id, type));
CREATE TABLE IF NOT EXISTS xqm_wallet (
    user_id INTEGER PRIMARY KEY, yizhu INTEGER NOT NULL DEFAULT 0,
    updated_at REAL NOT NULL DEFAULT 0);
CREATE INDEX IF NOT EXISTS idx_ach_user_uid ON ach_user(user_id);
CREATE INDEX IF NOT EXISTS idx_activity_time ON activity_log(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_activity_user ON activity_log(user_id);
CREATE INDEX IF NOT EXISTS idx_aidbg_user ON ai_debug_logs(user_id);
CREATE INDEX IF NOT EXISTS idx_aihist_user ON ai_chat_history(user_id, created_at);
CREATE INDEX IF NOT EXISTS idx_ailog_created ON ai_logs(created_at);
CREATE INDEX IF NOT EXISTS idx_ailog_module ON ai_logs(module);
CREATE INDEX IF NOT EXISTS idx_aim_job ON ai_moderation(job_id);
CREATE INDEX IF NOT EXISTS idx_aim_status ON ai_moderation(status, id DESC);
CREATE UNIQUE INDEX IF NOT EXISTS idx_airedeem_once ON ai_redeem_used(code_id, user_id);
CREATE INDEX IF NOT EXISTS idx_aisq_conv_user ON ai_square_convs(user_id, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_aisq_msg_conv ON ai_square_msgs(conv_id, id);
CREATE INDEX IF NOT EXISTS idx_aisq_usage_time ON ai_square_usage(created_at);
CREATE INDEX IF NOT EXISTS idx_aisq_usage_user ON ai_square_usage(user_id, created_at);
CREATE INDEX IF NOT EXISTS idx_atk_ip ON attack_events(ip);
CREATE INDEX IF NOT EXISTS idx_atk_kind ON attack_events(kind, ts);
CREATE INDEX IF NOT EXISTS idx_atk_ts ON attack_events(ts);
CREATE INDEX IF NOT EXISTS idx_blacklist_blocked ON blacklist(blocked_id);
CREATE INDEX IF NOT EXISTS idx_blacklist_pair ON blacklist(user_id, blocked_id);
CREATE INDEX IF NOT EXISTS idx_comments_post ON post_comments(post_id);
CREATE INDEX IF NOT EXISTS idx_emsend_created ON email_send_log(created_at);
CREATE INDEX IF NOT EXISTS idx_ev_chat ON evolve_chat(id DESC);
CREATE INDEX IF NOT EXISTS idx_ev_order ON evolve_orders(status, id DESC);
CREATE INDEX IF NOT EXISTS idx_ev_seen ON evolve_players(last_seen DESC);
CREATE INDEX IF NOT EXISTS idx_ev_trade_to ON evolve_trades(to_id, status);
CREATE INDEX IF NOT EXISTS idx_ev_war_def ON evolve_wars(defender, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_fav_target ON favorites(target_type, post_id);
CREATE INDEX IF NOT EXISTS idx_friends_a ON friends(user_id, friend_id);
CREATE INDEX IF NOT EXISTS idx_friends_b ON friends(friend_id, user_id);
CREATE INDEX IF NOT EXISTS idx_ghost_user_track ON ghost_runs(user_id, track, time_ms);
CREATE INDEX IF NOT EXISTS idx_gr_board ON game_replays(game, sub, score);
CREATE INDEX IF NOT EXISTS idx_groupmsg_gid ON group_messages(group_id, id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_groups_no ON groups(group_no);
CREATE INDEX IF NOT EXISTS idx_gs_hist ON game_saves_history(user_id, game, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_ic_daily_day ON ic_daily(day);
CREATE INDEX IF NOT EXISTS idx_ic_inv_inviter ON ic_invites(inviter_id, status);
CREATE INDEX IF NOT EXISTS idx_ic_inv_ip ON ic_invites(reg_ip);
CREATE INDEX IF NOT EXISTS idx_kubi_grave_place ON kubition_graves(place, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_kubi_hist ON kubition_saves_history(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_kubi_sync ON kubition_players(last_sync DESC);
CREATE INDEX IF NOT EXISTS idx_messages_priv ON messages(user_id, receiver_id, is_private);
CREATE INDEX IF NOT EXISTS idx_messages_room ON messages(room_id, is_private, id);
CREATE INDEX IF NOT EXISTS idx_mon_ts ON monitor_samples(ts);
CREATE INDEX IF NOT EXISTS idx_mpl_time ON music_play_logs(created_at);
CREATE INDEX IF NOT EXISTS idx_mpl_user ON music_playlists(user_id);
CREATE INDEX IF NOT EXISTS idx_mps_playlist ON music_playlist_songs(playlist_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_msg_idem ON messages(user_id, idem_key) WHERE idem_key IS NOT NULL AND idem_key != '';
CREATE INDEX IF NOT EXISTS idx_msg_priv ON messages(is_private, receiver_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_msg_user_time ON messages(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_notif_user ON notifications(user_id, is_read);
CREATE INDEX IF NOT EXISTS idx_notif_user_read ON notifications(user_id, is_read, id DESC);
CREATE INDEX IF NOT EXISTS idx_pc_parent ON post_comments(parent_id);
CREATE INDEX IF NOT EXISTS idx_pc_post_time ON post_comments(post_id, created_at);
CREATE UNIQUE INDEX IF NOT EXISTS idx_pl_idem ON points_log(user_id, idem_key) WHERE idem_key IS NOT NULL AND idem_key != '';
CREATE INDEX IF NOT EXISTS idx_pl_type ON points_log(source_type, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_pl_user ON playlists(user_id);
CREATE INDEX IF NOT EXISTS idx_pl_user_time ON points_log(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_plt_pl ON playlist_tracks(playlist_id);
CREATE INDEX IF NOT EXISTS idx_points_log_user ON points_log(user_id, created_at);
CREATE INDEX IF NOT EXISTS idx_posts_sec_time ON posts(section_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_posts_section ON posts(section_id, recycle, is_pinned, created_at);
CREATE INDEX IF NOT EXISTS idx_posts_tags_tag ON posts_tags(tag_id);
CREATE INDEX IF NOT EXISTS idx_posts_thread ON posts(thread_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_posts_user ON posts(user_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_sensitive_words_word ON sensitive_words(word);
CREATE INDEX IF NOT EXISTS idx_survey_q ON survey_questions(survey_id, sort);
CREATE INDEX IF NOT EXISTS idx_survey_r ON survey_responses(survey_id, user_id);
CREATE INDEX IF NOT EXISTS idx_tagsub_user ON tag_subs(user_id);
CREATE INDEX IF NOT EXISTS idx_thread_subs_t ON thread_subs(thread_id);
CREATE INDEX IF NOT EXISTS idx_threads_board ON threads(board_id);
CREATE INDEX IF NOT EXISTS idx_ts_user ON thread_subs(user_id);
CREATE INDEX IF NOT EXISTS idx_ua_action_ts ON user_actions(action, created_at);
CREATE INDEX IF NOT EXISTS idx_ua_user_ts ON user_actions(user_id, created_at);
CREATE INDEX IF NOT EXISTS idx_ufile_token ON user_files(share_token);
CREATE INDEX IF NOT EXISTS idx_ufile_user ON user_files(user_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_users_github ON users(github_id) WHERE github_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_xqm_games_status ON xqm_games(status);
CREATE INDEX IF NOT EXISTS idx_xqm_queue_poll ON xqm_queue(last_poll);
CREATE INDEX IF NOT EXISTS idx_xqp_status ON xq_puzzles(status);
CREATE TRIGGER IF NOT EXISTS messages_fts_ad AFTER DELETE ON messages BEGIN
            DELETE FROM messages_fts WHERE rowid = old.id;
        END;
CREATE TRIGGER IF NOT EXISTS messages_fts_ai AFTER INSERT ON messages BEGIN
            INSERT INTO messages_fts(rowid, content) VALUES (new.id, new.content);
        END;
