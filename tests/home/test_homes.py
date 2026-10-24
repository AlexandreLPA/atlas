"""The homes of a machine: which one a command runs in, and making, cloning, renaming and deleting
them. Every test runs on real folders, a real vault and real locks; nothing stands in for them."""

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from atlas.home import (
    HomeBusyError,
    HomeNameError,
    Redactor,
    VaultSettings,
    active_home,
    choose_home,
    create_home,
    delete_home,
    home_name,
    home_root,
    is_served,
    list_homes,
    open_home,
    rename_home,
    serving,
    use_home,
)
from atlas.secrets import Vault


@pytest.fixture
def root(tmp_path: Path) -> Path:
    return open_home(tmp_path / "atlas").root


def _vault(home: Path) -> Vault:
    return Vault(home / "secrets", VaultSettings(), Redactor())


def _hold_from_another_process(home: Path) -> subprocess.Popen[str]:
    """A process that serves the home until its standard input closes."""
    code = textwrap.dedent(
        """
        import sys
        from pathlib import Path
        from atlas.home import open_home, serving

        with serving(open_home(Path(sys.argv[1]))):
            print("held", flush=True)
            sys.stdin.read()
        """
    )
    holder = subprocess.Popen(  # noqa: S603
        [sys.executable, "-c", code, str(home)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    assert holder.stdout is not None
    assert holder.stdout.readline().strip() == "held"
    return holder


# -- which home ----------------------------------------------------------------------------------


def test_the_default_home_is_the_root_until_another_is_used(root: Path) -> None:
    assert choose_home(root, None) == root
    work = create_home(root, "work")

    use_home(root, "work")

    assert active_home(root) == "work"
    assert choose_home(root, None) == work
    assert choose_home(root, "default") == root
    use_home(root, "default")
    assert choose_home(root, None) == root
    assert not (root / "active_home").exists()


def test_a_home_named_by_its_folder_is_that_home(root: Path) -> None:
    work = create_home(root, "work")
    use_home(root, "default")

    # ATLAS_HOME pointing inside homes/, as the app's server for that home is started.
    assert choose_home(work, None) == work
    assert home_root(work) == root
    assert (home_name(work), home_name(root)) == ("work", "default")
    # Named from inside a home, another one is found from the same root.
    assert choose_home(work, "default") == root


def test_a_missing_home_says_how_to_make_it_or_go_back(root: Path) -> None:
    with pytest.raises(HomeNameError, match=r"There is no home 'nope'\. Make it with `atlas home"):
        choose_home(root, "nope")
    create_home(root, "gone")
    use_home(root, "gone")
    delete_home(
        root,
        "gone",
    )
    (root / "active_home").write_text("gone\n", encoding="utf-8")  # left by an older Atlas
    with pytest.raises(HomeNameError, match="use default"):
        choose_home(root, None)


@pytest.mark.parametrize("name", ["Work", "-work", "my work", "a/b", "x" * 65, "trabalho."])
def test_a_name_outside_the_rule_is_refused(root: Path, name: str) -> None:
    with pytest.raises(HomeNameError, match="not a home name"):
        create_home(root, name)


# -- making them ---------------------------------------------------------------------------------


def test_an_empty_home_has_nothing_of_the_others(root: Path) -> None:
    _vault(root).set("OPENAI_API_KEY", "sk-root")

    work = create_home(root, "work")

    assert sorted(p.name for p in work.iterdir()) == []
    assert _vault(work).names() == []


def test_a_clone_carries_the_homes_set_and_its_keys_open(root: Path) -> None:
    _vault(root).set("OPENAI_API_KEY", "sk-root")
    (root / "config.toml").write_text('[model]\nprovider = "openai"\n', encoding="utf-8")
    (root / "models.toml").write_text("", encoding="utf-8")
    (root / "mcp.json").write_text('{"mcpServers": {}}', encoding="utf-8")
    (root / "SOUL.md").write_text("You are dry and terse.", encoding="utf-8")
    (root / "memory").mkdir()
    (root / "memory" / "USER.md").write_text("Ana, designer.", encoding="utf-8")
    (root / "memory" / "MEMORY.md").write_text("Uses Figma.", encoding="utf-8")
    (root / "memory" / "USER.md.lock").write_text("", encoding="utf-8")
    (root / "skills" / "weekly").mkdir(parents=True)
    (root / "skills" / "weekly" / "SKILL.md").write_text("---\nname: weekly\n---\n", "utf-8")
    (root / "atlas.db").write_bytes(b"conversations live here")
    (root / "logs").mkdir()

    work = create_home(root, "work", clone=root)

    assert sorted(p.name for p in work.iterdir()) == [
        "SOUL.md",
        "config.toml",
        "mcp.json",
        "memory",
        "models.toml",
        "secrets",
        "skills",
    ]
    assert (work / "SOUL.md").read_text(encoding="utf-8") == "You are dry and terse."
    assert sorted(p.name for p in (work / "memory").iterdir()) == ["MEMORY.md", "USER.md"]
    assert (work / "skills" / "weekly" / "SKILL.md").is_file()
    assert _vault(work).get("OPENAI_API_KEY") == "sk-root"
    # Each home keeps its own vault from then on.
    _vault(work).set("GEMINI_API_KEY", "g-work")
    assert _vault(root).names() == ["OPENAI_API_KEY"]


def test_a_home_being_made_is_never_listed(root: Path) -> None:
    (root / "homes" / "half.creating").mkdir(parents=True)
    create_home(root, "work")

    assert [home.name for home in list_homes(root)] == ["default", "work"]
    assert not (root / "homes" / "work.creating").exists()


def test_a_clone_that_fails_leaves_no_half_home(root: Path) -> None:
    with pytest.raises(HomeNameError, match="to clone"):
        create_home(root, "work", clone=root / "nowhere")

    assert not (root / "homes" / "work").exists()
    assert not (root / "homes" / "work.creating").exists()


def test_an_existing_name_is_refused(root: Path) -> None:
    create_home(root, "work")
    with pytest.raises(HomeNameError, match="already a home 'work'"):
        create_home(root, "work")
    with pytest.raises(HomeNameError, match="already a home 'default'"):
        create_home(root, "default")


# -- renaming and deleting -----------------------------------------------------------------------


def test_a_rename_takes_the_active_home_along(root: Path) -> None:
    create_home(root, "work")
    use_home(root, "work")

    renamed = rename_home(root, "work", "job")

    assert renamed == root / "homes" / "job"
    assert active_home(root) == "job"
    with pytest.raises(HomeNameError, match="keeps its name"):
        rename_home(root, "default", "main")


def test_deleting_the_active_home_falls_back_to_the_default(root: Path) -> None:
    work = create_home(root, "work", clone=root)
    use_home(root, "work")

    delete_home(root, "work")

    assert not work.exists()
    assert active_home(root) == "default"
    with pytest.raises(HomeNameError, match="cannot be deleted"):
        delete_home(root, "default")


# -- one server per home -------------------------------------------------------------------------


def test_a_second_server_on_a_home_is_refused(root: Path) -> None:
    home = open_home(root)
    with serving(home):
        assert is_served(root)
        with (
            pytest.raises(HomeBusyError, match="Another Atlas server is serving"),
            serving(home),
        ):
            pass
    assert not is_served(root)


def test_a_served_home_is_neither_renamed_nor_deleted(root: Path) -> None:
    work = create_home(root, "work")
    holder = _hold_from_another_process(work)
    try:
        assert [home.served for home in list_homes(root)] == [False, True]
        with pytest.raises(HomeBusyError, match="cannot be renamed"):
            rename_home(root, "work", "job")
        with pytest.raises(HomeBusyError, match="cannot be deleted"):
            delete_home(root, "work")
    finally:
        holder.communicate("")
    # The lock dies with the process that held it.
    assert not is_served(work)
    delete_home(root, "work")
