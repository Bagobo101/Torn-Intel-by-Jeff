from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.schema import SchemaBuilder
from models.crime_slot import CrimeMember, CrimeDelayEvent, CrimeDelayNotification
from repositories.crime_slot_repository import CrimeSlotRepository
from services.database import Database


class _Settings:
    def __init__(self, database_path: Path):
        self.database_path = database_path


class _Logger:
    def info(self, *_args, **_kwargs):
        return None

    def success(self, *_args, **_kwargs):
        return None

    def warning(self, *_args, **_kwargs):
        return None

    def error(self, *_args, **_kwargs):
        return None


def _build_repo(tmp_path: Path):
    database = Database(_Settings(tmp_path / "crime-delay-test.db"), _Logger())
    schema = SchemaBuilder(database, _Logger())
    schema.create(CrimeMember)
    schema.create(CrimeDelayEvent)
    schema.create(CrimeDelayNotification)
    return CrimeSlotRepository(database), database


def test_track_flying_delays_starts_updates_and_resolves(tmp_path):
    repo, database = _build_repo(tmp_path)

    members = [
        {
            "user_id": 101,
            "user_name": "FlyerOne",
            "position": "Member",
            "is_in_oc": 1,
            "status_state": "Traveling",
            "status_description": "Traveling to Mexico",
            "last_action": 0,
            "updated_at": 1000,
        }
    ]
    slots = [
        {
            "crime_id": 555,
            "crime_name": "Bombing Run",
            "difficulty": 7,
            "status": "Planning",
            "user_id": 101,
            "user_name": "FlyerOne",
            "slot_position": "Driver",
        }
    ]

    summary = repo.track_flying_delays(
        slots,
        members,
        crime_status_rows=[{"crime_id": 555, "status": "Planning", "ready_at": 1000}],
        observed_at=1000,
    )

    assert summary == {"active": 1, "started": 1, "resolved": 0}

    active_rows = repo.active_delay_events(limit=10)
    assert len(active_rows) == 1
    assert int(active_rows[0]["crime_id"]) == 555
    assert str(active_rows[0]["delaying_user_names"]) == "FlyerOne"

    start_notifications = repo.list_unposted_delay_notifications(limit=10)
    assert len(start_notifications) == 1
    assert str(start_notifications[0]["event_type"]) == "crime_delay_started"

    summary = repo.track_flying_delays(
        slots,
        members,
        crime_status_rows=[{"crime_id": 555, "status": "Planning", "ready_at": 1000}],
        observed_at=1300,
    )

    assert summary == {"active": 1, "started": 0, "resolved": 0}
    start_notifications = repo.list_unposted_delay_notifications(limit=10)
    assert len(start_notifications) == 1

    members[0]["status_state"] = "Okay"
    members[0]["status_description"] = "Okay"

    summary = repo.track_flying_delays(
        slots,
        members,
        crime_status_rows=[{"crime_id": 555, "status": "Completed", "ready_at": 1000}],
        observed_at=15400,
    )

    assert summary == {"active": 0, "started": 0, "resolved": 1}

    active_rows = repo.active_delay_events(limit=10)
    assert active_rows == []

    resolved_rows = repo.resolved_delay_events(limit=10)
    assert len(resolved_rows) == 1
    resolved = resolved_rows[0]
    assert int(resolved["duration_seconds"]) == 14400
    assert str(resolved["resolution"]) == "completed"

    notifications = repo.list_unposted_delay_notifications(limit=10)
    assert len(notifications) == 2
    assert [str(row["event_type"]) for row in notifications] == [
        "crime_delay_started",
        "crime_delay_resolved",
    ]

    database.close()


def test_track_flying_delays_waits_until_planning_timer_is_over(tmp_path):
    repo, database = _build_repo(tmp_path)

    members = [
        {
            "user_id": 202,
            "user_name": "FutureFlyer",
            "position": "Member",
            "is_in_oc": 1,
            "status_state": "Abroad",
            "status_description": "Returning in 15m",
            "last_action": 0,
            "updated_at": 2000,
        }
    ]
    slots = [
        {
            "crime_id": 777,
            "crime_name": "Blackmail",
            "difficulty": 5,
            "status": "Planning",
            "user_id": 202,
            "user_name": "FutureFlyer",
            "slot_position": "Hacker",
        }
    ]

    summary = repo.track_flying_delays(
        slots,
        members,
        crime_status_rows=[{"crime_id": 777, "status": "Planning", "ready_at": 2600}],
        observed_at=2400,
    )

    assert summary == {"active": 0, "started": 0, "resolved": 0}
    assert repo.active_delay_events(limit=10) == []

    summary = repo.track_flying_delays(
        slots,
        members,
        crime_status_rows=[{"crime_id": 777, "status": "Planning", "ready_at": 2600}],
        observed_at=2900,
    )

    assert summary == {"active": 1, "started": 1, "resolved": 0}
    active_rows = repo.active_delay_events(limit=10)
    assert len(active_rows) == 1
    active = active_rows[0]
    assert int(active["started_at"]) == 2600
    assert int(active["duration_seconds"]) == 300

    database.close()


def test_track_flying_delays_does_not_resolve_on_empty_snapshot(tmp_path):
    repo, database = _build_repo(tmp_path)

    members = [
        {
            "user_id": 303,
            "user_name": "Diablom",
            "position": "Member",
            "is_in_oc": 1,
            "status_state": "Abroad",
            "status_description": "In South Africa",
            "last_action": 0,
            "updated_at": 1000,
        }
    ]
    slots = [
        {
            "crime_id": 999,
            "crime_name": "Best of the Lot",
            "difficulty": 2,
            "status": "Planning",
            "user_id": 303,
            "user_name": "Diablom",
            "slot_position": "Muscle",
        }
    ]

    # Initial delay starts
    summary = repo.track_flying_delays(
        slots,
        members,
        crime_status_rows=[{"crime_id": 999, "status": "Planning", "ready_at": 1000}],
        observed_at=1000,
    )
    assert summary == {"active": 1, "started": 1, "resolved": 0}
    notifications = repo.list_unposted_delay_notifications(limit=10)
    assert len(notifications) == 1
    assert str(notifications[0]["event_type"]) == "crime_delay_started"

    # Transient API failure: members list is empty
    summary = repo.track_flying_delays(
        [],
        [],
        crime_status_rows=[],
        observed_at=1060,
    )
    assert summary["resolved"] == 0
    active = repo.active_delay_events(limit=10)
    assert len(active) == 1  # Delay is still open, not resolved!

    # Transient API glitch: slots missing for crime still in planning
    summary = repo.track_flying_delays(
        [],
        members,
        crime_status_rows=[{"crime_id": 999, "status": "Planning", "ready_at": 1000}],
        observed_at=1120,
    )
    assert summary["resolved"] == 0
    active = repo.active_delay_events(limit=10)
    assert len(active) == 1  # Delay is still open!

    # Member in hospital abroad is still detected as flying/blocking
    members[0]["status_state"] = "Hospital"
    members[0]["status_description"] = "In a South African hospital for 15 mins"
    summary = repo.track_flying_delays(
        slots,
        members,
        crime_status_rows=[{"crime_id": 999, "status": "Planning", "ready_at": 1000}],
        observed_at=1180,
    )
    assert summary["resolved"] == 0
    active = repo.active_delay_events(limit=10)
    assert len(active) == 1

    # Notifications still only contains 1 start event (no false resolved/started cycle)
    notifications = repo.list_unposted_delay_notifications(limit=10)
    assert len(notifications) == 1

    database.close()


def test_parse_slots_preserves_assigned_members_without_item_requirement():
    from modules.crimes.parser import CrimeParser

    response = {
        "crimes": {
            "2151682": {
                "id": 2151682,
                "name": "Best of the Lot",
                "difficulty": 2,
                "status": "Planning",
                "slots": [
                    {
                        "position": "Car Thief",
                        "checkpoint_pass_rate": 80,
                        "user": {"id": 101, "name": "DriverGuy"},
                        "item_requirement": {"id": 50, "name": "Slim Jim", "is_available": True},
                    },
                    {
                        "position": "Muscle",
                        "checkpoint_pass_rate": 75,
                        "user": {"id": 202, "name": "Diablom"},
                        "item_requirement": None,
                    },
                    {
                        "position": "Imitator",
                        "checkpoint_pass_rate": 0,
                        "user": None,
                        "item_requirement": None,
                    },
                ],
            }
        }
    }

    slots = CrimeParser.parse_slots(response)
    # Should include both DriverGuy (with item) and Diablom (without item), skipping unassigned slot without item
    assert len(slots) == 2
    user_ids = {s["user_id"] for s in slots}
    assert user_ids == {101, 202}