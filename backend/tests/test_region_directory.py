"""Region / zone dropdowns for People, and keeping caches in step across instances."""
from datetime import datetime, timedelta, timezone

from app.models.schemas import Employee, Role
from app.services import cache_sync
from app.services import employees as es


def emp(i, region, zone, rm=None, zm=None, role=Role.BDE, active=True):
    return Employee(employee_id=i, full_name=f"P{i}", region=region, zone=zone,
                    rm_id=rm, zm_id=zm, role=role, is_active=active)


PEOPLE = [
    emp("NHP248", "R11-Srinivasan", "Akshay Yamsanwar", zm="NHP302", role=Role.REGIONAL_MANAGER),
    emp("NHP380", "R11-Srinivasan", "Akshay Yamsanwar", rm="NHP248", zm="NHP302"),
    emp("NHP545", "R11-Srinivasan", "Akshay Yamsanwar", rm="NHP248", zm="NHP302"),
    # Moved by hand, old managers left behind: must not decide the region's RM.
    emp("NHP536", "R11-Srinivasan", "Akshay Yamsanwar", rm="NHP147", zm="NHP056"),
    emp("NHP147", "R13-Saif", "Divyansh Pandey", zm="NHP056", role=Role.REGIONAL_MANAGER),
    emp("NHP364", "R13-Saif", "Divyansh Pandey", rm="NHP147", zm="NHP056"),
    emp("NHP279", "R2-Akif", "Z2", rm="NHP900", zm="NHP901"),
    emp("NHP999", "R1-Old", "Gone", active=False),
    emp("NHP302", None, None, role=Role.ZONAL_MANAGER),
]


def test_each_region_carries_its_zone_rm_and_zonal_manager():
    d = es.region_directory(PEOPLE)
    r11 = next(r for r in d["regions"] if r["region"] == "R11-Srinivasan")
    assert r11 == {"region": "R11-Srinivasan", "zone": "Akshay Yamsanwar",
                   "rm_id": "NHP248", "rm_name": "PNHP248",
                   "zm_id": "NHP302", "zm_name": "PNHP302", "people": 4}


def test_without_an_rm_in_the_region_the_most_common_manager_is_used():
    d = es.region_directory(PEOPLE)
    r2 = next(r for r in d["regions"] if r["region"] == "R2-Akif")
    assert r2["rm_id"] == "NHP900" and r2["rm_name"] is None


def test_regions_are_in_number_order_and_leavers_are_left_out():
    names = [r["region"] for r in es.region_directory(PEOPLE)["regions"]]
    assert names == ["R2-Akif", "R11-Srinivasan", "R13-Saif"]


def test_zones_carry_their_zonal_manager():
    zones = {z["zone"]: z for z in es.region_directory(PEOPLE)["zones"]}
    assert zones["Divyansh Pandey"]["zm_id"] == "NHP056"
    assert zones["Akshay Yamsanwar"]["zm_id"] == "NHP302"
    assert "Gone" not in zones


# --- cache sync ---------------------------------------------------------------
class FakeBQ:
    def __init__(self):
        self.events = []

    def insert_rows(self, table, rows):
        self.events += [datetime.fromisoformat(r["at"]) for r in rows]

    def query(self, sql, params=None):
        return [{"at": max(self.events) if self.events else None}]


def _fresh(monkeypatch):
    fake = FakeBQ()
    monkeypatch.setattr(cache_sync, "bq", fake)
    monkeypatch.setattr(cache_sync, "_table", lambda: "t")
    monkeypatch.setattr(cache_sync, "_last_check", 0.0)
    monkeypatch.setattr(cache_sync, "_last_seen", None)
    cleared = []
    monkeypatch.setattr(cache_sync, "forget_local", lambda: cleared.append(1))
    return fake, cleared


def test_a_write_elsewhere_clears_this_instance(monkeypatch):
    fake, cleared = _fresh(monkeypatch)
    fake.events.append(datetime.now(timezone.utc) - timedelta(minutes=1))
    assert cache_sync.check() is True                     # first look: catch up
    monkeypatch.setattr(cache_sync, "_last_check", 0.0)
    assert cache_sync.check() is False                    # nothing new
    fake.events.append(datetime.now(timezone.utc))        # another instance writes
    monkeypatch.setattr(cache_sync, "_last_check", 0.0)
    assert cache_sync.check() is True and len(cleared) == 2


def test_checks_are_rate_limited(monkeypatch):
    fake, _ = _fresh(monkeypatch)
    cache_sync.check()
    fake.events.append(datetime.now(timezone.utc))
    assert cache_sync.check() is False                    # within CHECK_SECONDS


def test_own_write_does_not_clear_twice(monkeypatch):
    fake, cleared = _fresh(monkeypatch)
    cache_sync.record_write("/api/employees/NHP536")
    assert len(cleared) == 1                              # cleared at the write
    assert cache_sync.check() is False and len(cleared) == 1


def test_a_failed_record_never_fails_the_write(monkeypatch):
    _, cleared = _fresh(monkeypatch)
    def boom(*a, **k):
        raise RuntimeError("bq down")
    monkeypatch.setattr(cache_sync.bq, "insert_rows", boom)
    cache_sync.record_write("/api/targets")               # no exception
    assert cleared == [1]
