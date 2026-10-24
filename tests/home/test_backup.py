"""A home's backup and its restore: what goes in, what never does, the checks that refuse a bad one,
and what a restore says when the backup is older. Real SQLite, real zips, a real vault; nothing
stands in for them."""

import json
import sqlite3
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from atlas.home import (
    BackupError,
    Database,
    DatabaseError,
    Home,
    HomeNameError,
    Migration,
    Redactor,
    Schema,
    VaultSettings,
    backup_name,
    check_backup,
    create_home,
    has_data,
    import_backup,
    list_homes,
    migrate,
    open_home,
    pre_migration_backup,
    prune_backups,
    restore_backup,
    suggest_home_name,
    write_backup,
)
from atlas.secrets import Vault

NOW = datetime(2026, 10, 11, 12, 0, tzinfo=UTC)
NOTES = Schema(
    "memory",
    (Migration(1, "notes", "CREATE TABLE memory_notes (id INTEGER PRIMARY KEY, body TEXT);"),),
)
SCHEMAS = (NOTES,)


@pytest.fixture
def home(tmp_path: Path) -> Home:
    return open_home(tmp_path / "home")


def _notes(home: Home, *bodies: str) -> None:
    database = Database(home.database_file)
    migrate(database, SCHEMAS)
    connection = database.connect()
    try:
        for body in bodies:
            connection.execute("INSERT INTO memory_notes (body) VALUES (?)", (body,))
    finally:
        connection.close()


def _bodies(database: Path) -> list[str]:
    connection = sqlite3.connect(database)
    try:
        return [row[0] for row in connection.execute("SELECT body FROM memory_notes ORDER BY id")]
    finally:
        connection.close()


def _fill(home: Home) -> None:
    """A home as it looks after some use: data, the vault, logs and what a day leaves."""
    _notes(home, "first", "second")
    (home.root / "config.toml").write_text('[model]\nprovider = "openai"\n', encoding="utf-8")
    (home.root / "memory").mkdir()
    (home.root / "memory" / "USER.md").write_text("Ana, designer.", encoding="utf-8")
    (home.root / "SOUL.md").write_text("You are dry and terse.", encoding="utf-8")
    (home.root / "picture.png").write_bytes(b"\x89PNG\r\n\x1a\n the home's picture")
    (home.root / "skills" / "weekly" / "__pycache__").mkdir(parents=True)
    (home.root / "skills" / "weekly" / "SKILL.md").write_text("---\nname: weekly\n---\n", "utf-8")
    (home.root / "skills" / "weekly" / "__pycache__" / "x.pyc").write_bytes(b"\0")
    (home.root / "bots" / "finance").mkdir(parents=True)
    (home.root / "bots" / "finance" / "BOT.md").write_text("Pay the bills.", encoding="utf-8")
    (home.root / "bots" / "finance" / "MEMORY.md").write_text("Rent is due on the 5th.", "utf-8")
    Vault(home.secrets_folder, VaultSettings(), Redactor()).set("OPENAI_API_KEY", "sk-secret")
    for folder in ("logs", "spill", "commands", "documents", "backups", "homes/work"):
        (home.root / folder).mkdir(parents=True)
        (home.root / folder / "something.txt").write_text("not the user's data", "utf-8")
    (home.root / "home").mkdir()
    (home.root / "home" / "notes.txt").write_text("made by the agent", encoding="utf-8")
    (home.root / "home" / backup_name("default", NOW)).write_bytes(b"an older backup")


def _backup(home: Home, tmp_path: Path, when: datetime = NOW) -> Path:
    return write_backup(
        home, tmp_path / backup_name("default", when), version="test", now=when
    ).path


# -- what goes in --------------------------------------------------------------------------------


def test_the_backup_holds_the_users_data_and_nothing_of_the_machine(
    home: Home, tmp_path: Path
) -> None:
    _fill(home)

    path = _backup(home, tmp_path)

    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
    assert names[0] == "manifest.json"
    assert sorted(names[1:]) == [
        "SOUL.md",
        "atlas.db",
        "bots/finance/BOT.md",
        "bots/finance/MEMORY.md",
        "config.toml",
        "home/notes.txt",
        "memory/USER.md",
        "picture.png",
        "skills/weekly/SKILL.md",
    ]
    assert b"sk-secret" not in path.read_bytes()


def test_the_manifest_says_what_the_backup_is(home: Home, tmp_path: Path) -> None:
    _fill(home)

    manifest = check_backup(_backup(home, tmp_path), SCHEMAS)

    assert (manifest.format, manifest.atlas, manifest.home) == (1, "test", "default")
    assert manifest.created_at == "2026-10-11T12:00:00Z"
    assert manifest.tables == {"home_migrations": 1, "memory_notes": 2}
    assert manifest.files == 9


def test_rows_another_connection_wrote_and_did_not_checkpoint_are_in_the_backup(
    home: Home, tmp_path: Path
) -> None:
    _notes(home, "first")
    writer = Database(home.database_file).connect()
    writer.execute("PRAGMA wal_autocheckpoint = 0")
    writer.execute("INSERT INTO memory_notes (body) VALUES ('only in the wal')")
    try:
        path = _backup(home, tmp_path)
    finally:
        writer.close()

    restored = open_home(tmp_path / "restored")
    restore_backup(restored, path, SCHEMAS)
    assert _bodies(restored.database_file) == ["first", "only in the wal"]


def test_the_programs_logins_in_the_user_home_folder_stay_on_the_machine(
    home: Home, tmp_path: Path
) -> None:
    _notes(home, "first")
    logins = {
        ".config/gh/hosts.yml": "oauth_token: gho_secret",
        ".config/gws/credentials.enc": "sealed",
        ".config/gws/.encryption_key": "key",
        ".config/gcloud/credentials.db": "refresh",
        ".config/ghostty/config": "theme = dark",  # a name that only starts like one
    }
    for relative, text in logins.items():
        path = home.root / "home" / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    path = _backup(home, tmp_path)

    with zipfile.ZipFile(path) as archive:
        names = [name for name in archive.namelist() if name.startswith("home/")]
    assert names == ["home/.config/ghostty/config"]
    assert b"gho_secret" not in path.read_bytes()


def test_a_login_in_an_older_backup_is_not_restored(home: Home, tmp_path: Path) -> None:
    _notes(home, "first")
    path = _backup(home, tmp_path)
    with zipfile.ZipFile(path) as archive:
        manifest = json.loads(archive.read("manifest.json"))
    manifest["files"] += 1
    _rewrite(
        path,
        {
            "manifest.json": json.dumps(manifest).encode(),
            "home/.config/gh/hosts.yml": b"oauth_token: gho_secret",
        },
    )

    restored = open_home(tmp_path / "restored")
    restore_backup(restored, path, SCHEMAS)

    assert not (restored.root / "home" / ".config" / "gh").exists()
    assert _bodies(restored.database_file) == ["first"]


def test_a_backup_never_writes_over_a_file(home: Home, tmp_path: Path) -> None:
    _notes(home, "first")
    taken = tmp_path / "taken.zip"
    taken.write_bytes(b"something of the user's")

    with pytest.raises(BackupError, match="never writes over"):
        write_backup(home, taken, version="test", now=NOW)

    assert taken.read_bytes() == b"something of the user's"
    assert [p.name for p in tmp_path.iterdir() if p.name.startswith(".")] == []


def test_a_file_the_user_chose_to_replace_is_replaced_by_a_checked_backup(
    home: Home, tmp_path: Path
) -> None:
    # The app's save dialog asked the user before it gave this path (step 10c).
    _notes(home, "first")
    taken = tmp_path / "taken.zip"
    taken.write_bytes(b"an older file")

    written = write_backup(home, taken, version="test", now=NOW, replace=True)

    assert written.path == taken
    assert check_backup(taken, SCHEMAS).tables["memory_notes"] == 1
    assert [p.name for p in tmp_path.iterdir() if p.name.startswith(".")] == []
    with pytest.raises(BackupError, match="never writes over"):
        write_backup(home, tmp_path, version="test", now=NOW, replace=True)


# -- the checks ----------------------------------------------------------------------------------


def _rewrite(path: Path, change: dict[str, bytes | None], *, first: str = "manifest.json") -> None:
    """The archive again, with members replaced (`bytes`), removed (`None`) or added."""
    with zipfile.ZipFile(path) as archive:
        members = {name: archive.read(name) for name in archive.namelist()}
    members.update({k: v for k, v in change.items() if v is not None})
    for name, value in change.items():
        if value is None:
            members.pop(name, None)
    order = [first] + [name for name in members if name != first]
    with zipfile.ZipFile(path, "w") as archive:
        for name in order:
            archive.writestr(name, members[name])


def test_a_damaged_member_refuses_the_backup(home: Home, tmp_path: Path) -> None:
    _notes(home, "first")
    path = _backup(home, tmp_path)
    data = bytearray(path.read_bytes())
    at = data.find(b"PRAGMA") if b"PRAGMA" in data else len(data) // 2
    data[at] ^= 0xFF
    path.write_bytes(bytes(data))

    with pytest.raises(BackupError, match=r"damaged|cannot read|does not open|integrity"):
        check_backup(path, SCHEMAS)


def test_a_zip_without_its_manifest_first_is_not_a_backup(home: Home, tmp_path: Path) -> None:
    _notes(home, "first")
    path = _backup(home, tmp_path)
    _rewrite(path, {}, first="atlas.db")

    with pytest.raises(BackupError, match="manifest is not first"):
        check_backup(path, SCHEMAS)


def test_a_backup_from_a_newer_atlas_is_refused(home: Home, tmp_path: Path) -> None:
    _notes(home, "first")
    path = _backup(home, tmp_path)
    with zipfile.ZipFile(path) as archive:
        manifest = json.loads(archive.read("manifest.json"))
    _rewrite(path, {"manifest.json": json.dumps({**manifest, "format": 2}).encode()})

    with pytest.raises(BackupError, match="format 2, written by a newer Atlas"):
        check_backup(path, SCHEMAS)


def test_a_database_a_newer_atlas_migrated_is_refused(home: Home, tmp_path: Path) -> None:
    newer = Schema("memory", (*NOTES.migrations, Migration(2, "title", "SELECT 1;")))
    _notes(home, "first")
    migrate(Database(home.database_file), (newer,))
    path = _backup(home, tmp_path)

    with pytest.raises(BackupError, match=r"cannot be restored here.*newer Atlas"):
        check_backup(path, SCHEMAS)


@pytest.mark.parametrize(
    ("member", "why"),
    [
        ("secrets/vault.json", "not the user's data"),
        ("logs/atlas.log", "not the user's data"),
        ("atlas.db-wal", "not the user's data"),
        ("../outside.txt", "outside the home"),
        ("/etc/passwd", "outside the home"),
        ("C:/Windows/x", "outside the home"),
    ],
)
def test_a_member_a_backup_never_carries_refuses_it(
    home: Home, tmp_path: Path, member: str, why: str
) -> None:
    _notes(home, "first")
    path = _backup(home, tmp_path)
    _rewrite(path, {member: b"x"})

    with pytest.raises(BackupError, match=why):
        check_backup(path, SCHEMAS)


def test_a_database_that_does_not_hold_what_the_manifest_says_is_refused(
    home: Home, tmp_path: Path
) -> None:
    _notes(home, "first", "second")
    path = _backup(home, tmp_path)
    smaller = open_home(tmp_path / "smaller")
    _notes(smaller, "first")
    _rewrite(path, {"atlas.db": smaller.database_file.read_bytes()})

    with pytest.raises(BackupError, match="does not hold what its manifest says: memory_notes"):
        check_backup(path, SCHEMAS)


# -- restoring -----------------------------------------------------------------------------------


def test_a_restore_brings_the_data_back_and_leaves_the_vault_alone(
    home: Home, tmp_path: Path
) -> None:
    _fill(home)
    path = _backup(home, tmp_path)
    other = open_home(tmp_path / "other")
    Vault(other.secrets_folder, VaultSettings(), Redactor()).set("GEMINI_API_KEY", "g-other")

    restored = restore_backup(other, path, SCHEMAS)

    assert restored.files == 9
    assert _bodies(other.database_file) == ["first", "second"]
    assert (other.root / "memory" / "USER.md").read_text(encoding="utf-8") == "Ana, designer."
    assert (other.root / "SOUL.md").read_text(encoding="utf-8") == "You are dry and terse."
    assert (other.root / "home" / "notes.txt").is_file()
    # A home's backup carries its bots, each with its memory.
    memory = other.root / "bots" / "finance" / "MEMORY.md"
    assert memory.read_text(encoding="utf-8") == "Rent is due on the 5th."
    assert Vault(other.secrets_folder, VaultSettings(), Redactor()).names() == ["GEMINI_API_KEY"]


def test_a_process_holding_the_database_reads_what_was_restored(home: Home, tmp_path: Path) -> None:
    # The import writes the pages into the live file: an open connection converges on them.
    _notes(home, "from the backup")
    path = _backup(home, tmp_path)
    _notes(home, "written after it")
    server = Database(home.database_file).connect()
    try:
        assert len(server.execute("SELECT body FROM memory_notes").fetchall()) == 2

        restore_backup(home, path, SCHEMAS)

        rows = server.execute("SELECT body FROM memory_notes ORDER BY id").fetchall()
        assert [row[0] for row in rows] == ["from the backup"]
    finally:
        server.close()


def test_an_older_backup_says_what_it_took_away(home: Home, tmp_path: Path) -> None:
    _notes(home, "first")
    path = _backup(home, tmp_path)
    _notes(home, "second", "third")

    restored = restore_backup(home, path, SCHEMAS)

    assert [(s.table, s.before, s.after) for s in restored.shrunk] == [("memory_notes", 3, 1)]


def test_a_refused_backup_touches_nothing(home: Home, tmp_path: Path) -> None:
    _notes(home, "first")
    path = _backup(home, tmp_path)
    _rewrite(path, {"secrets/vault.json": b"{}", "config.toml": b"[model]\n"})
    before = sorted(p.name for p in home.root.iterdir())

    with pytest.raises(BackupError):
        restore_backup(home, path, SCHEMAS)

    assert sorted(p.name for p in home.root.iterdir()) == before


def test_a_home_has_data_once_it_holds_anything_of_the_user(home: Home) -> None:
    migrate(Database(home.database_file), SCHEMAS)
    (home.root / "skills").mkdir()
    assert not has_data(home)
    _notes(home, "first")
    assert has_data(home)


# -- keeping a few -------------------------------------------------------------------------------


def test_pruning_keeps_the_newest_and_never_another_homes(tmp_path: Path) -> None:
    for hours in range(5):
        when = NOW + timedelta(hours=hours)
        (tmp_path / backup_name("work", when)).write_bytes(b"")
        (tmp_path / backup_name("work-x", when)).write_bytes(b"")
    (tmp_path / "atlas-backup-work-mine.zip").write_bytes(b"named by hand")

    removed = prune_backups(tmp_path, "atlas-backup-work-", 3)

    assert sorted(p.name for p in removed) == [
        backup_name("work", NOW),
        backup_name("work", NOW + timedelta(hours=1)),
    ]
    left = sorted(p.name for p in tmp_path.iterdir())
    assert len([n for n in left if n.startswith("atlas-backup-work-x-")]) == 5
    assert "atlas-backup-work-mine.zip" in left
    assert prune_backups(tmp_path, "atlas-backup-work-", 0) == []
    with pytest.raises(BackupError, match="Keep -1"):
        prune_backups(tmp_path, "atlas-backup-work-", -1)


def test_a_migration_of_a_database_with_data_is_backed_up_first(home: Home) -> None:
    taken: list[Path] = []

    def back_up() -> None:
        taken.append(
            pre_migration_backup(home, version="test", now=NOW + timedelta(minutes=len(taken))).path
        )

    database = Database(home.database_file)
    migrate(database, SCHEMAS, before=back_up)  # a new database: nothing to keep
    assert taken == []
    _notes(home, "first")
    newer = Schema(
        "memory",
        (
            *NOTES.migrations,
            Migration(2, "title", "ALTER TABLE memory_notes ADD COLUMN title TEXT;"),
        ),
    )

    migrate(database, (newer,), before=back_up)
    migrate(database, (newer,), before=back_up)  # nothing pending: no backup

    assert [p.parent.name for p in taken] == ["backups"]
    assert taken[0].name.startswith("pre-migration-")
    assert check_backup(taken[0], SCHEMAS).tables["memory_notes"] == 1


def test_the_last_five_backups_before_a_migration_are_kept(home: Home) -> None:
    _notes(home, "first")

    for minute in range(7):
        pre_migration_backup(home, version="test", now=NOW + timedelta(minutes=minute))

    kept = sorted(p.name for p in home.backups_folder.iterdir())
    assert len(kept) == 5
    assert kept[0] == "pre-migration-20261011T120200Z.zip"


# -- importing as a new home (step 10c) ----------------------------------------------------------


def _homes(root: Path) -> list[str]:
    return [info.name for info in list_homes(root)]


def test_an_import_makes_a_new_home_with_the_backup_and_no_keys(home: Home, tmp_path: Path) -> None:
    _fill(home)
    path = _backup(home, tmp_path)
    machine = open_home(tmp_path / "machine")

    imported = import_backup(machine.root, "restored", path, SCHEMAS)

    assert imported.path == machine.root / "homes" / "restored"
    assert imported.manifest.home == "default"
    assert imported.files == 9
    assert _homes(machine.root) == ["default", "restored"]
    made = Home(imported.path)
    assert _bodies(made.database_file) == ["first", "second"]
    assert (made.root / "memory" / "USER.md").read_text(encoding="utf-8") == "Ana, designer."
    assert made.soul_file.read_text(encoding="utf-8") == "You are dry and terse."
    assert Vault(made.secrets_folder, VaultSettings(), Redactor()).names() == []
    # The home it was imported beside is untouched.
    assert not machine.database_file.exists()


def test_an_import_brings_an_older_database_forward(home: Home, tmp_path: Path) -> None:
    _notes(home, "first")
    path = _backup(home, tmp_path)
    title = Migration(2, "title", "ALTER TABLE memory_notes ADD COLUMN title TEXT;")
    newer = Schema("memory", (*NOTES.migrations, title))

    imported = import_backup(tmp_path / "machine", "restored", path, (newer,))

    connection = sqlite3.connect(Home(imported.path).database_file)
    try:
        columns = [row[1] for row in connection.execute("PRAGMA table_info(memory_notes)")]
    finally:
        connection.close()
    assert columns == ["id", "body", "title"]


def test_a_refused_backup_makes_no_home(home: Home, tmp_path: Path) -> None:
    _notes(home, "first")
    path = _backup(home, tmp_path)
    _rewrite(path, {"secrets/vault.json": b"{}"})
    machine = open_home(tmp_path / "machine")

    with pytest.raises(BackupError, match="not the user's data"):
        import_backup(machine.root, "restored", path, SCHEMAS)

    assert _homes(machine.root) == ["default"]
    assert not (machine.root / "homes" / "restored.creating").exists()


def test_an_import_that_fails_once_restored_leaves_no_home(home: Home, tmp_path: Path) -> None:
    # The backup is sound, but bringing its database forward fails: nothing is published.
    _notes(home, "first")
    path = _backup(home, tmp_path)
    broken = Schema("memory", (*NOTES.migrations, Migration(2, "broken", "ALTER TABLE nowhere;")))
    machine = open_home(tmp_path / "machine")

    with pytest.raises(DatabaseError):
        import_backup(machine.root, "restored", path, (broken,))

    assert _homes(machine.root) == ["default"]
    assert sorted(p.name for p in (machine.root / "homes").iterdir()) == []


def test_an_import_never_takes_a_name_a_home_has(home: Home, tmp_path: Path) -> None:
    _notes(home, "first")
    path = _backup(home, tmp_path)
    machine = open_home(tmp_path / "machine")
    create_home(machine.root, "work")

    for name in ("work", "default"):
        with pytest.raises(HomeNameError, match="already a home"):
            import_backup(machine.root, name, path, SCHEMAS)
    with pytest.raises(HomeNameError, match="not a home name"):
        import_backup(machine.root, "Não", path, SCHEMAS)

    assert _homes(machine.root) == ["default", "work"]


def test_the_name_offered_is_the_homes_or_dated_when_it_is_taken(
    home: Home, tmp_path: Path
) -> None:
    _notes(home, "first")
    machine = open_home(tmp_path / "machine")
    of_default = check_backup(_backup(home, tmp_path), SCHEMAS)
    work = Home(create_home(home.root, "work"))
    _notes(work, "at work")
    of_work = check_backup(
        write_backup(work, tmp_path / "work.zip", version="test", now=NOW).path, SCHEMAS
    )

    # The default home always exists, so its backup is offered a dated name.
    assert suggest_home_name(machine.root, of_default) == "default-2026-10-11"
    create_home(machine.root, "default-2026-10-11")
    assert suggest_home_name(machine.root, of_default) == "default-2026-10-11-2"
    # A named home's backup keeps its name where no home has it.
    assert suggest_home_name(machine.root, of_work) == "work"
    create_home(machine.root, "work")
    assert suggest_home_name(machine.root, of_work) == "work-2026-10-11"
