"""Initialize the SQLite database for the equipment request service."""

import argparse
import sqlite3
from contextlib import closing
from pathlib import Path


def initialize_database(
    db_path: Path,
    *,
    sql_dir: Path = Path("data"),
    seed: bool = False,
) -> None:
    """Apply the schema and optionally seed a new lab database."""
    scripts = [(sql_dir / "schema.sql").read_text(encoding="utf-8")]
    if seed:
        scripts.append((sql_dir / "seed.sql").read_text(encoding="utf-8"))

    db_path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(db_path)) as connection:
        for script in scripts:
            connection.executescript(script)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=Path("tmp/equipment.sqlite3"))
    parser.add_argument("--sql-dir", type=Path, default=Path("data"))
    parser.add_argument(
        "--seed",
        action="store_true",
        help="Load synthetic records once into a new lab database.",
    )
    args = parser.parse_args()

    try:
        initialize_database(args.db, sql_dir=args.sql_dir, seed=args.seed)
    except (OSError, sqlite3.Error) as error:
        parser.exit(1, f"Could not initialize {args.db}: {error}\n")

    print(f"SQLite database ready at {args.db}")


if __name__ == "__main__":
    main()
