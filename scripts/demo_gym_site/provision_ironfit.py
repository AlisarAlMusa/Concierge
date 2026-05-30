#!/usr/bin/env python
"""Provision the IronFit Gym demo: tenant + admin + widget + 6 published CMS pages.

Two-phase to keep the embedding pipeline happy:
  1) DB-direct upsert of tenant / tenant_config / widget / tenant_admin user
     (same pattern as backend/scripts/seed_tenants.py).
  2) HTTP login as the new admin, then POST /tenant/cms/ for each page with
     ``status=published`` so the CmsPageService runs its full embed → chunk
     write pipeline against the live API.

Idempotent: re-running this script is safe. It skips rows that already exist
and republishes CMS pages by upserting on (tenant_id, slug).

Run from inside the api container:

    docker exec concierge-api-1 python /app/../scripts/demo_gym_site/provision_ironfit.py

…or copy it in and run via ``python provision_ironfit.py`` — the script
auto-detects whether it's running inside the container or against the host.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

# Allow running from repo root, backend/, or inside the api container (/app).
THIS_DIR = os.path.dirname(os.path.abspath(__file__))
for candidate in (
    os.path.join(THIS_DIR, "..", "..", "backend"),  # host: scripts/demo_gym_site → backend
    "/app",                                          # api container
):
    if os.path.isdir(os.path.join(candidate, "app")):
        sys.path.insert(0, candidate)
        break

TENANT_SLUG = "ironfit-gym"
TENANT_NAME = "IronFit Gym"
ADMIN_EMAIL = "admin@ironfit-gym.com"
ADMIN_PASSWORD = "Admin!2026"
PUBLIC_WIDGET_ID = "pub_wid_ironfit_001"
DEMO_ORIGIN = "http://localhost:5500"
API_BASE = os.environ.get("CONCIERGE_API", "http://localhost:8000")

WIDGET_GREETING = (
    "Hi! 👋 Welcome to IronFit Gym. Ask me about memberships, classes, "
    "trainers, hours, or anything else about the gym."
)


# ──────────────────────────────────────────────────────────────────────────────
# CMS content — answers to FAQ-style questions about IronFit.
# ──────────────────────────────────────────────────────────────────────────────
CMS_PAGES: list[dict[str, str]] = [
    {
        "slug": "opening-hours",
        "title": "Opening Hours & Location",
        "body": (
            "IronFit Gym opening hours:\n\n"
            "Monday to Friday: 5:00 AM to 11:00 PM\n"
            "Saturday: 7:00 AM to 9:00 PM\n"
            "Sunday: 8:00 AM to 6:00 PM\n\n"
            "We are closed on Christmas Day and New Year's Day. On all other "
            "public holidays we run a reduced schedule from 9:00 AM to 5:00 PM.\n\n"
            "Address: 482 Steelworks Avenue, Downtown District.\n"
            "Phone: (555) 123-IRON (4766).\n"
            "Email: hello@ironfit-gym.example.\n\n"
            "Free parking is available for members in the underground garage "
            "accessible from Forge Lane. The gym is also a 4-minute walk from "
            "the Steelworks Metro station."
        ),
    },
    {
        "slug": "memberships",
        "title": "Memberships and Pricing",
        "body": (
            "IronFit offers three monthly membership tiers, billed monthly with "
            "no contract:\n\n"
            "Basic — $39 per month. Includes 24/7 access to the cardio and "
            "weights floor, free WiFi, and locker access. Does not include "
            "group classes or sauna.\n\n"
            "Standard — $59 per month. Everything in Basic, plus unlimited "
            "group classes (yoga, HIIT, cycling, boxing), full sauna and steam "
            "room access, and one free guest pass per month.\n\n"
            "Elite — $99 per month. Everything in Standard, plus two personal "
            "training sessions per month, nutrition consultation, towel service, "
            "and unlimited guest passes.\n\n"
            "Annual prepayment saves 15% on any tier. Students get a 20% "
            "discount on Basic and Standard memberships with valid ID.\n\n"
            "There is a one-time joining fee of $25, waived for annual "
            "prepayments and for sign-ups during our launch promotions.\n\n"
            "We also offer a $15 day pass and a $79 weekly pass for visitors."
        ),
    },
    {
        "slug": "personal-training",
        "title": "Personal Training",
        "body": (
            "Personal training at IronFit is delivered by certified coaches "
            "who hold NASM or NSCA credentials and an average of seven years "
            "of in-gym experience.\n\n"
            "Pricing:\n"
            "Single session — $65 (60 minutes).\n"
            "Pack of 5 sessions — $300 ($60 per session).\n"
            "Pack of 10 sessions — $550 ($55 per session).\n"
            "Pack of 20 sessions — $1000 ($50 per session).\n\n"
            "Every new member receives one complimentary 30-minute consultation "
            "with a coach to set goals and review form. The first paid session "
            "is risk-free — if you're not satisfied we refund it in full.\n\n"
            "Coaches:\n"
            "Marcus 'Iron' Reed — 12 years of experience, NSCA-CPT, specialises "
            "in powerlifting, strength sport coaching, and post-injury return "
            "to lifting.\n\n"
            "Lena Park — 8 years of experience, NASM-CPT, ACE Nutrition. "
            "Specialises in fat loss, body composition, women's strength "
            "programs, and pre-natal training.\n\n"
            "Diego Salazar — 6 years of experience, former collegiate athlete, "
            "specialises in athletic performance, speed work, and rehab.\n\n"
            "Sessions can be booked online or at the front desk. We require "
            "24 hours' notice to cancel without forfeiting the session."
        ),
    },
    {
        "slug": "class-schedule",
        "title": "Class Schedule",
        "body": (
            "IronFit runs over 60 group classes per week. The weekly schedule:\n\n"
            "Monday — HIIT 6:00 AM with Diego, Vinyasa Yoga 9:00 AM with Lena, "
            "Power Cycle 5:30 PM with Marcus, Boxing 7:00 PM with Diego.\n\n"
            "Tuesday — Strength Foundations 6:30 AM with Marcus, Pilates 10:00 AM "
            "with Lena, HIIT 6:00 PM with Diego, Restorative Yoga 7:30 PM with Lena.\n\n"
            "Wednesday — Power Cycle 6:00 AM with Marcus, Boxing 12:00 PM with "
            "Diego, Strength Foundations 5:30 PM with Marcus, Vinyasa Yoga 7:00 PM "
            "with Lena.\n\n"
            "Thursday — HIIT 6:30 AM with Diego, Mobility 9:00 AM with Lena, "
            "Power Cycle 6:00 PM with Marcus, Boxing 7:30 PM with Diego.\n\n"
            "Friday — Strength Foundations 6:00 AM with Marcus, Vinyasa Yoga "
            "10:00 AM with Lena, HIIT 5:30 PM with Diego, Power Cycle 7:00 PM "
            "with Marcus.\n\n"
            "Saturday — Powerlifting Workshop 9:00 AM with Marcus, Boxing "
            "10:30 AM with Diego, Vinyasa Yoga 12:00 PM with Lena.\n\n"
            "Sunday — Mobility 9:00 AM with Lena, Restorative Yoga 10:30 AM "
            "with Lena, Open Gym all day.\n\n"
            "All classes are 50 minutes long and capped at 18 participants. "
            "Booking opens 72 hours in advance via the IronFit app and the "
            "front desk. Standard and Elite members get unlimited classes; "
            "Basic members can drop in for $10 per class."
        ),
    },
    {
        "slug": "faqs",
        "title": "Frequently Asked Questions",
        "body": (
            "Can I freeze my membership?\n"
            "Yes — Standard and Elite members can freeze their membership for "
            "up to 60 days per year at no charge. Basic members can freeze "
            "for $10 per month, up to 30 days per year.\n\n"
            "Is there a free trial?\n"
            "Yes — every new visitor gets one free three-day pass. Book it at "
            "the front desk or online with valid ID.\n\n"
            "How do I cancel?\n"
            "Cancel anytime in person, by email to membership@ironfit-gym.example, "
            "or through the IronFit app. Monthly plans require 14 days' notice. "
            "Annual prepayments are non-refundable but transferable to another "
            "person.\n\n"
            "Do you have showers and lockers?\n"
            "Yes — full locker rooms with day-use lockers, showers, and hair "
            "dryers. Bring your own padlock or buy one at reception for $5. "
            "Standard and Elite members can rent a permanent locker for $10 "
            "per month.\n\n"
            "Is parking free?\n"
            "Yes — members park free in the underground garage under the "
            "building, accessible from Forge Lane.\n\n"
            "Do you offer childcare?\n"
            "Yes — supervised KidsZone is open Monday to Friday from 9:00 AM "
            "to 12:00 PM and 4:00 PM to 7:00 PM, and Saturdays 9:00 AM to "
            "1:00 PM. Free for Elite members, $5 per visit for Standard and "
            "Basic members. Children must be 6 months to 10 years old.\n\n"
            "What should I bring?\n"
            "A water bottle, a towel for hygiene, and indoor sports shoes. "
            "We provide chalk, sanitiser wipes, and equipment for every class.\n\n"
            "Is the gym accessible?\n"
            "Yes — step-free access from the street, an elevator to every "
            "floor, accessible bathrooms, and adjustable benches for "
            "wheelchair users on the main floor."
        ),
    },
    {
        "slug": "facilities",
        "title": "Facilities and Amenities",
        "body": (
            "IronFit Gym is a 24,000-square-foot facility on three floors. "
            "Highlights:\n\n"
            "Strength floor — 8 power racks, 4 platforms, dumbbells from 5 to "
            "150 lb, a full Eleiko competition setup, and chains and bands "
            "for accommodating resistance.\n\n"
            "Cardio floor — 22 treadmills, 14 rowers (Concept2 RowErgs), 12 "
            "spin bikes, 8 ellipticals, 6 stair climbers, and 4 SkiErgs.\n\n"
            "Functional zone — turf strip for sled pushes and prowler work, "
            "battle ropes, climbing rope, plyo boxes, and Olympic rings.\n\n"
            "Boxing studio — heavy bags, speed bags, a regulation-size ring, "
            "and gloves for rent at $2 per visit.\n\n"
            "Recovery — dry sauna, steam room, two cold plunge tubs (50 °F / "
            "10 °C), Normatec compression boots, and a stretch zone.\n\n"
            "Member perks — free filtered water refills, in-app workout logging, "
            "discounted recovery brand merchandise, and monthly community "
            "events (powerlifting meets, charity rows, run club)."
        ),
    },
]


# ──────────────────────────────────────────────────────────────────────────────
# Phase 1 — DB-direct upserts via app models.
# ──────────────────────────────────────────────────────────────────────────────


async def _upsert_tenant(session, slug, name, log):
    from sqlalchemy import select
    from app.models.tenant import Tenant, TenantStatus

    row = (await session.execute(select(Tenant).where(Tenant.slug == slug))).scalar_one_or_none()
    if row is None:
        row = Tenant(id=uuid.uuid4(), name=name, slug=slug, status=TenantStatus.active)
        session.add(row)
        await session.flush()
        log.info("tenant.created", slug=slug, id=str(row.id))
    else:
        log.info("tenant.exists", slug=slug, id=str(row.id))
    return row


async def _upsert_config(session, tenant_id, log):
    from sqlalchemy import select
    from app.models.tenant_config import TenantConfig

    row = (
        await session.execute(select(TenantConfig).where(TenantConfig.tenant_id == tenant_id))
    ).scalar_one_or_none()
    if row is None:
        session.add(
            TenantConfig(
                tenant_id=tenant_id,
                brand_name="IronFit Gym",
                theme_color="#e63946",
                public_description="Modern strength-and-conditioning gym in the Downtown District.",
                contact_email="hello@ironfit-gym.example",
                persona=(
                    "You are the IronFit Gym concierge. You are friendly, energetic, "
                    "and helpful. Only answer using information from the gym's CMS "
                    "pages. If a question is outside the gym (memberships, classes, "
                    "trainers, hours, facilities, FAQs), politely decline."
                ),
                refusal_tone="friendly",
            )
        )
        log.info("config.created", tenant_id=str(tenant_id))


async def _upsert_widget(session, tenant_id, log):
    from sqlalchemy import select
    from app.models.widget import Widget

    row = (
        await session.execute(select(Widget).where(Widget.public_widget_id == PUBLIC_WIDGET_ID))
    ).scalar_one_or_none()
    if row is None:
        session.add(
            Widget(
                id=uuid.uuid4(),
                tenant_id=tenant_id,
                public_widget_id=PUBLIC_WIDGET_ID,
                name="IronFit website widget",
                allowed_origins=[DEMO_ORIGIN, "http://127.0.0.1:5500"],
                theme={"accent": "#e63946", "mode": "dark"},
                greeting=WIDGET_GREETING,
                enabled=True,
            )
        )
        log.info("widget.created", public_widget_id=PUBLIC_WIDGET_ID)
    else:
        # Make sure the demo origin is allow-listed even on re-runs.
        wanted = {DEMO_ORIGIN, "http://127.0.0.1:5500"}
        current = set(row.allowed_origins or [])
        if not wanted.issubset(current):
            row.allowed_origins = sorted(current | wanted)
            log.info("widget.origins_extended", origins=row.allowed_origins)
        if row.greeting != WIDGET_GREETING:
            row.greeting = WIDGET_GREETING
            log.info("widget.greeting_updated")


async def _upsert_user(session, email, password, tenant_id, password_helper, log):
    from sqlalchemy import select
    from app.models.user import User, UserRole

    row = (await session.execute(select(User).where(User.email == email))).scalar_one_or_none()
    if row is None:
        session.add(
            User(
                id=uuid.uuid4(),
                email=email,
                hashed_password=password_helper.hash(password),
                is_active=True,
                is_superuser=False,
                is_verified=True,
                role=UserRole.tenant_admin,
                tenant_id=tenant_id,
            )
        )
        log.info("user.created", email=email)
    else:
        log.info("user.exists", email=email)


async def phase1_db():
    import structlog
    from fastapi_users.password import PasswordHelper

    from app.core.config import get_settings
    from app.db.session import close_engine, get_engine, get_session_factory

    log = structlog.get_logger("ironfit")
    get_settings()
    get_engine()
    factory = get_session_factory()
    password_helper = PasswordHelper()

    async with factory() as session:
        tenant = await _upsert_tenant(session, TENANT_SLUG, TENANT_NAME, log)
        await _upsert_config(session, tenant.id, log)
        await _upsert_widget(session, tenant.id, log)
        await _upsert_user(session, ADMIN_EMAIL, ADMIN_PASSWORD, tenant.id, password_helper, log)
        await session.commit()

    await close_engine()


# ──────────────────────────────────────────────────────────────────────────────
# Phase 2 — HTTP login + publish CMS pages via the actual admin route.
# ──────────────────────────────────────────────────────────────────────────────


def _http(method, path, *, body=None, headers=None, form=None):
    if form is not None:
        data = urllib.parse.urlencode(form).encode()
    elif body is not None:
        data = json.dumps(body).encode()
    else:
        data = None
    req = urllib.request.Request(API_BASE + path, data=data, method=method)
    if form is not None:
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
    elif body is not None:
        req.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            payload = r.read()
            return r.status, (json.loads(payload) if payload else None)
    except urllib.error.HTTPError as e:
        body_txt = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(body_txt)
        except Exception:
            return e.code, body_txt


def phase2_publish():
    print("\n── Phase 2: login + publish CMS pages via /tenant/cms ─────────────")
    status, resp = _http("POST", "/auth/login",
                         form={"username": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    if status != 200:
        print(f"  ❌  login failed: status={status} body={resp}")
        sys.exit(2)
    jwt = resp["access_token"]
    auth = {"Authorization": f"Bearer {jwt}"}

    # Wipe any pre-existing IronFit CMS pages so re-runs publish fresh, indexed copies.
    status, listing = _http("GET", "/tenant/cms/", headers=auth)
    if status == 200 and isinstance(listing, dict):
        for p in listing.get("items", []):
            _http("DELETE", f"/tenant/cms/{p['id']}", headers=auth)
            print(f"  cleared existing page {p.get('slug')!r}")

    for page in CMS_PAGES:
        status, resp = _http("POST", "/tenant/cms/",
                             body={
                                 "title": page["title"],
                                 "slug": page["slug"],
                                 "body": page["body"],
                                 "status": "published",
                             },
                             headers=auth)
        ok = status in (200, 201)
        chunks = resp.get("chunks_written") if isinstance(resp, dict) else "?"
        marker = "✅" if ok else "❌"
        print(f"  {marker}  {page['slug']:24s}  status={status}  chunks_written={chunks}")
        time.sleep(0.2)  # gentle pacing for the embedding provider


def main():
    print("══════ IronFit Gym demo provisioning ══════")
    print(f"  tenant  : {TENANT_NAME}  ({TENANT_SLUG})")
    print(f"  admin   : {ADMIN_EMAIL}  /  {ADMIN_PASSWORD}")
    print(f"  widget  : {PUBLIC_WIDGET_ID}  (origin: {DEMO_ORIGIN})")
    print(f"  api     : {API_BASE}\n")

    asyncio.run(phase1_db())
    phase2_publish()

    print(
        f"\n══════ Done ══════\n"
        f"  Snippet for the host page:\n"
        f'    <script src="{API_BASE}/widget.js" data-widget-id="{PUBLIC_WIDGET_ID}" async></script>\n\n'
        f"  Serve the demo site:\n"
        f"    cd scripts/demo_gym_site && python3 -m http.server 5500\n\n"
        f"  Then open:\n"
        f"    http://localhost:5500/index.html\n"
    )


if __name__ == "__main__":
    main()
