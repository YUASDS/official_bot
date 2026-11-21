import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest


def test_legacy_duplicate_accounts_survive_startup_and_restart(tmp_path):
    path = tmp_path / "legacy.db"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE user_info (id INTEGER PRIMARY KEY, user_id TEXT, gold INTEGER, sign_num INTEGER)")
    conn.execute("CREATE INDEX user_user_id ON user_info(user_id)")
    conn.executemany("INSERT INTO user_info VALUES (?, ?, ?, ?)", [
        (2, "alice", 30, 4), (5, "alice", 80, 9), (6, "bob", 15, 3),
    ])
    conn.commit()
    conn.close()
    env = dict(os.environ, STORYTELLER_USER_DB_PATH=str(path))
    for _ in range(2):
        subprocess.run([sys.executable, "-c", "import database.db"], env=env,
                       cwd=Path(__file__).resolve().parents[1], check=True)

    conn = sqlite3.connect(path)
    try:
        assert conn.execute("SELECT * FROM user_info ORDER BY id").fetchall() == [
            (2, "alice", 30, 4), (6, "bob", 15, 3),
        ]
        assert conn.execute("SELECT * FROM user_info_duplicates").fetchall() == [(5, "alice", 80, 9)]
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO user_info(user_id) VALUES ('alice')")
    finally:
        conn.close()
