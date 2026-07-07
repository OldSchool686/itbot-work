"""Seed departments into PostgreSQL database."""

import os
import sys
from pathlib import Path

import psycopg2
from dotenv import load_dotenv

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent

load_dotenv(PROJECT_ROOT / ".env")

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    print("ERROR: DATABASE_URL not found in .env file.")
    sys.exit(1)

DEPARTMENTS = [
    # Управления (management)
    ("Управление экономики и инвестиций", "management"),
    ("Управление по правовому обеспечению и безопасности муниципального образования", "management"),
    ("Управление комплексного развития муниципального образования", "management"),
    # Службы (service)
    ("Служба информационно-коммуникационных технологий (ИКТ)", "service"),
    ("Служба координации жилищно-коммунального хозяйства", "service"),
    ("Служба сельского хозяйства", "service"),
    ("Служба потребительского рынка", "service"),
    ("Служба экономики, социального развития и инвестиций", "service"),
    ("Служба гражданской обороны, чрезвычайных ситуаций и пожарной безопасности", "service"),
    ("Служба военно-учетной работы", "service"),
    ("Служба координации энергетики и благоустройства", "service"),
    # Отделы (department)
    ("Отдел бухгалтерского учета", "department"),
    ("Отдел по обращениям граждан и делопроизводству", "department"),
    ("Отдел по развитию местного самоуправления", "department"),
    ("Отдел по земельным отношениям", "department"),
    ("Отдел по имуществу", "department"),
    ("Отдел архитектуры, строительства, дорожного хозяйства и транспорта", "department"),
    ("Отдел по жилищным вопросам", "department"),
    ("Отдел культуры и делам молодежи", "department"),
    ("Отдел по физической культуре и спорту", "department"),
    ("Бюджетный отдел", "department"),
    ("Отдел бюджетного учета, отчетности и исполнения бюджета", "department"),
    ("Юридический отдел", "department"),
]


def seed_departments():
    conn = psycopg2.connect(dsn=DATABASE_URL)
    cur = conn.cursor()

    inserted = 0
    skipped = 0

    for name, dept_type in DEPARTMENTS:
        cur.execute(
            """
            INSERT INTO departments (name, type)
            VALUES (%s, %s)
            ON CONFLICT (name) DO NOTHING
            RETURNING id
            """,
            (name, dept_type),
        )
        if cur.rowcount == 1:
            inserted += 1
            print(f"  [+] {name}")
        else:
            skipped += 1
            print(f"    {name} (already exists)")

    conn.commit()
    cur.close()
    conn.close()

    print(f"\nDone: {inserted} inserted, {skipped} skipped. Total: {len(DEPARTMENTS)}")


if __name__ == "__main__":
    seed_departments()
