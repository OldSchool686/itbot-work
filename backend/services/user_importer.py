import csv
import io
import re
from datetime import datetime, timezone

from sqlalchemy import select, delete as sa_delete
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.allowed_user import AllowedUser
from backend.utils.phone_utils import normalize_phone


async def import_users_csv(
    csv_content: bytes,
    mode: str,
    admin_username: str,
    db: AsyncSession,
) -> dict:
    reader = csv.DictReader(io.StringIO(csv_content.decode("utf-8-sig")))

    added, updated, skipped = 0, 0, 0

    rows = list(reader)

    if mode == "replace" and rows:
        for row in rows:
            phone = normalize_phone(row.get("phone", ""))
            full_name = row.get("full_name", "").strip()
            if not phone or not full_name or not re.match(r"^\+7\d{10}$", phone):
                skipped += 1
        await db.execute(sa_delete(AllowedUser))
        await db.commit()

    for row in rows:
        phone = normalize_phone(row.get("phone", ""))
        full_name = row.get("full_name", "").strip()

        if not phone or not full_name:
            skipped += 1
            continue

        if not re.match(r"^\+7\d{10}$", phone):
            skipped += 1
            continue

        existing = (await db.execute(
            select(AllowedUser).where(AllowedUser.phone == phone)
        )).scalar_one_or_none()

        if existing:
            existing.full_name = full_name
            existing.department = row.get("department", "").strip() or None
            consent_str = row.get("consent_given", "false").lower().strip()
            existing.consent_given = consent_str in ("true", "1", "yes")
            if existing.consent_given and not existing.consent_timestamp:
                existing.consent_timestamp = datetime.now(timezone.utc)
            elif not existing.consent_given:
                existing.consent_timestamp = None
            existing.updated_at = datetime.now(timezone.utc)
            updated += 1
        else:
            consent_str = row.get("consent_given", "false").lower().strip()
            consent_given = consent_str in ("true", "1", "yes")
            new_user = AllowedUser(
                phone=phone,
                full_name=full_name,
                department=row.get("department", "").strip() or None,
                consent_given=consent_given,
                consent_timestamp=datetime.now(timezone.utc) if consent_given else None,
                is_active=True,
                added_by=admin_username,
            )
            db.add(new_user)
            added += 1

    await db.commit()
    return {"added": added, "updated": updated, "skipped": skipped}


async def export_users_csv(db: AsyncSession) -> bytes:
    result = await db.execute(
        select(AllowedUser).where(AllowedUser.is_active == True).order_by(AllowedUser.full_name)
    )
    users = result.scalars().all()

    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=["phone", "full_name", "department", "consent_given"])
    writer.writeheader()
    for user in users:
        writer.writerow({
            "phone": user.phone,
            "full_name": user.full_name,
            "department": user.department or "",
            "consent_given": str(user.consent_given).lower(),
        })
    return output.getvalue().encode("utf-8-sig")
