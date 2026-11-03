import os

import aiosqlite

# /app/data wird in Docker als Volume auf den Unraid-Appdata-Pfad gemountet,
# damit die Datenbank Container-Updates/-Neustarts übersteht.
DB_PATH = os.environ.get("DB_PATH", "/app/data/database.db")


async def _column_exists(db: aiosqlite.Connection, table: str, column: str) -> bool:
    async with db.execute(f"PRAGMA table_info({table})") as cursor:
        columns = [row[1] async for row in cursor]
    return column in columns


async def init_db() -> None:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS guild_config (
                guild_id INTEGER PRIMARY KEY,
                welcome_channel_id INTEGER,
                auto_role_id INTEGER,
                mod_log_channel_id INTEGER
            )
            """
        )
        # Migration für Datenbanken, die vor Einführung von auto_role_id angelegt wurden.
        if not await _column_exists(db, "guild_config", "auto_role_id"):
            await db.execute("ALTER TABLE guild_config ADD COLUMN auto_role_id INTEGER")
        # Migration für Datenbanken, die vor Einführung von mod_log_channel_id angelegt wurden.
        if not await _column_exists(db, "guild_config", "mod_log_channel_id"):
            await db.execute("ALTER TABLE guild_config ADD COLUMN mod_log_channel_id INTEGER")
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS tempvoice_creator_channels (
                guild_id INTEGER NOT NULL,
                channel_id INTEGER NOT NULL,
                PRIMARY KEY (guild_id, channel_id)
            )
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS warnings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                moderator_id INTEGER NOT NULL,
                reason TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                channel_id INTEGER NOT NULL,
                message_id INTEGER,
                title TEXT NOT NULL,
                description TEXT,
                event_time TEXT NOT NULL,
                target_role_id INTEGER,
                created_by INTEGER,
                status TEXT NOT NULL DEFAULT 'scheduled',
                created_at TEXT NOT NULL
            )
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS event_rsvps (
                event_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                status TEXT NOT NULL,
                responded_at TEXT NOT NULL,
                PRIMARY KEY (event_id, user_id)
            )
            """
        )
        await db.commit()


async def set_welcome_channel(guild_id: int, channel_id: int) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO guild_config (guild_id, welcome_channel_id)
            VALUES (?, ?)
            ON CONFLICT(guild_id) DO UPDATE SET welcome_channel_id = excluded.welcome_channel_id
            """,
            (guild_id, channel_id),
        )
        await db.commit()


async def get_welcome_channel(guild_id: int) -> int | None:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT welcome_channel_id FROM guild_config WHERE guild_id = ?",
            (guild_id,),
        ) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else None


async def set_auto_role(guild_id: int, role_id: int) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO guild_config (guild_id, auto_role_id)
            VALUES (?, ?)
            ON CONFLICT(guild_id) DO UPDATE SET auto_role_id = excluded.auto_role_id
            """,
            (guild_id, role_id),
        )
        await db.commit()


async def get_auto_role(guild_id: int) -> int | None:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT auto_role_id FROM guild_config WHERE guild_id = ?",
            (guild_id,),
        ) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else None


async def set_tempvoice_creator_channels(guild_id: int, channel_ids: list[int]) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM tempvoice_creator_channels WHERE guild_id = ?", (guild_id,))
        await db.executemany(
            "INSERT INTO tempvoice_creator_channels (guild_id, channel_id) VALUES (?, ?)",
            [(guild_id, channel_id) for channel_id in channel_ids],
        )
        await db.commit()


async def get_tempvoice_creator_channels(guild_id: int) -> list[int]:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT channel_id FROM tempvoice_creator_channels WHERE guild_id = ?",
            (guild_id,),
        ) as cursor:
            return [row[0] async for row in cursor]


async def set_mod_log_channel(guild_id: int, channel_id: int) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO guild_config (guild_id, mod_log_channel_id)
            VALUES (?, ?)
            ON CONFLICT(guild_id) DO UPDATE SET mod_log_channel_id = excluded.mod_log_channel_id
            """,
            (guild_id, channel_id),
        )
        await db.commit()


async def get_mod_log_channel(guild_id: int) -> int | None:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT mod_log_channel_id FROM guild_config WHERE guild_id = ?",
            (guild_id,),
        ) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else None


async def add_warning(guild_id: int, user_id: int, moderator_id: int, reason: str) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """
            INSERT INTO warnings (guild_id, user_id, moderator_id, reason, created_at)
            VALUES (?, ?, ?, ?, datetime('now'))
            """,
            (guild_id, user_id, moderator_id, reason),
        )
        await db.commit()
        return cursor.lastrowid


async def get_warnings(guild_id: int, user_id: int | None = None) -> list[dict]:
    query = "SELECT id, user_id, moderator_id, reason, created_at FROM warnings WHERE guild_id = ?"
    params: list[int] = [guild_id]
    if user_id is not None:
        query += " AND user_id = ?"
        params.append(user_id)
    query += " ORDER BY id DESC"

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(query, params) as cursor:
            return [
                {
                    "id": row[0],
                    "user_id": row[1],
                    "moderator_id": row[2],
                    "reason": row[3],
                    "created_at": row[4],
                }
                async for row in cursor
            ]


async def delete_warning(guild_id: int, warning_id: int) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "DELETE FROM warnings WHERE guild_id = ? AND id = ?",
            (guild_id, warning_id),
        )
        await db.commit()
        return cursor.rowcount > 0


async def clear_warnings(guild_id: int, user_id: int) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "DELETE FROM warnings WHERE guild_id = ? AND user_id = ?",
            (guild_id, user_id),
        )
        await db.commit()
        return cursor.rowcount


_EVENT_COLUMNS = (
    "id",
    "guild_id",
    "channel_id",
    "message_id",
    "title",
    "description",
    "event_time",
    "target_role_id",
    "created_by",
    "status",
    "created_at",
)


def _row_to_event(row) -> dict:
    return dict(zip(_EVENT_COLUMNS, row))


async def create_event(
    guild_id: int,
    channel_id: int,
    title: str,
    description: str,
    event_time: str,
    target_role_id: int | None,
    created_by: int | None,
) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """
            INSERT INTO events
                (guild_id, channel_id, title, description, event_time, target_role_id, created_by, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'scheduled', datetime('now'))
            """,
            (guild_id, channel_id, title, description, event_time, target_role_id, created_by),
        )
        await db.commit()
        return cursor.lastrowid


async def set_event_message(event_id: int, message_id: int) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE events SET message_id = ? WHERE id = ?", (message_id, event_id))
        await db.commit()


async def get_event(event_id: int) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            f"SELECT {', '.join(_EVENT_COLUMNS)} FROM events WHERE id = ?", (event_id,)
        ) as cursor:
            row = await cursor.fetchone()
            return _row_to_event(row) if row else None


async def get_events(guild_id: int, include_cancelled: bool = True) -> list[dict]:
    query = f"SELECT {', '.join(_EVENT_COLUMNS)} FROM events WHERE guild_id = ?"
    params: list = [guild_id]
    if not include_cancelled:
        query += " AND status != 'cancelled'"
    query += " ORDER BY event_time ASC"

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(query, params) as cursor:
            return [_row_to_event(row) async for row in cursor]


async def get_scheduled_events_with_message() -> list[dict]:
    # Für die Wiederherstellung persistenter Button-Views beim Bot-Neustart (cog_load).
    query = (
        f"SELECT {', '.join(_EVENT_COLUMNS)} FROM events "
        "WHERE status = 'scheduled' AND message_id IS NOT NULL"
    )
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(query) as cursor:
            return [_row_to_event(row) async for row in cursor]


async def update_event(
    event_id: int,
    *,
    title: str,
    description: str,
    event_time: str,
    channel_id: int,
    target_role_id: int | None,
) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            UPDATE events
            SET title = ?, description = ?, event_time = ?, channel_id = ?, target_role_id = ?
            WHERE id = ?
            """,
            (title, description, event_time, channel_id, target_role_id, event_id),
        )
        await db.commit()


async def delete_event(event_id: int) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM event_rsvps WHERE event_id = ?", (event_id,))
        await db.execute("DELETE FROM events WHERE id = ?", (event_id,))
        await db.commit()


async def set_rsvp(event_id: int, user_id: int, status: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO event_rsvps (event_id, user_id, status, responded_at)
            VALUES (?, ?, ?, datetime('now'))
            ON CONFLICT(event_id, user_id) DO UPDATE SET
                status = excluded.status,
                responded_at = excluded.responded_at
            """,
            (event_id, user_id, status),
        )
        await db.commit()


async def get_rsvps(event_id: int) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT user_id, status, responded_at FROM event_rsvps WHERE event_id = ?",
            (event_id,),
        ) as cursor:
            return [
                {"user_id": row[0], "status": row[1], "responded_at": row[2]}
                async for row in cursor
            ]
