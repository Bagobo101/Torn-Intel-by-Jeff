"""
services/crime_service.py

Turns raw Torn OC crime payloads into parsed slot and CPR rows.
"""

from modules.crimes.parser import CrimeParser


class CrimeService:

    def __init__(self, gateway, logger):
        self.gateway = gateway
        self.logger = logger

    #######################################################

    def _resolve_faction_meta(self, faction=None):
        settings = getattr(self.gateway, "settings", None)
        if settings:
            cfg = settings.get_faction(faction)
            if cfg:
                return cfg.tag, cfg.faction_id
        tag = str(faction).strip().upper() if faction else "GTS"
        return tag, None

    def fetch_active_slots(self, faction=None):

        snapshot = self.fetch_snapshot(faction=faction)
        return snapshot["active_slots"]

    #######################################################

    def fetch_snapshot(self, faction=None):

        faction_tag, faction_id = self._resolve_faction_meta(faction)
        response, has_members = self._fetch_crime_response(pool=faction_tag)
        item_names = self._get_item_name_map()

        if not isinstance(response, dict):
            return {
                "ok": False,
                "error": "Invalid response from Torn API",
                "faction_tag": faction_tag,
                "faction_id": faction_id,
                "members": [],
                "active_slots": [],
                "cpr_rows": [],
                "crime_status_rows": [],
            }

        if response.get("error"):
            self.logger.error(f"Crime API error ({faction_tag}): {response['error']}")
            return {
                "ok": False,
                "error": str(response["error"]),
                "faction_tag": faction_tag,
                "faction_id": faction_id,
                "members": [],
                "active_slots": [],
                "cpr_rows": [],
                "crime_status_rows": [],
            }

        members = CrimeParser.parse_members(response, faction_id=faction_id, faction_tag=faction_tag) if has_members else self.fetch_roster_members(faction=faction)
        if not members:
            members = self.fetch_roster_members(faction=faction)

        member_names = {
            int(member["user_id"]): member["user_name"]
            for member in members
            if int(member.get("user_id") or 0) > 0
        }

        active_slots = CrimeParser.parse_slots(
            response,
            member_names=member_names,
            allowed_statuses={"recruiting", "planning"},
            item_names=item_names,
            faction_id=faction_id,
            faction_tag=faction_tag,
        )

        return {
            "ok": bool(members),
            "faction_tag": faction_tag,
            "faction_id": faction_id,
            "members": members,
            "active_slots": active_slots,
            "cpr_rows": CrimeParser.parse_cpr_rows(active_slots, faction_id=faction_id, faction_tag=faction_tag),
            "crime_status_rows": CrimeParser.parse_crime_status_rows(response, faction_id=faction_id, faction_tag=faction_tag),
        }

    #######################################################

    def _fetch_crime_response(self, pool="default"):

        response = self.gateway.faction_basic_crimes_members_v2(category="available,completed", pool=pool)

        # Fallback if combined endpoint is unavailable.
        if not isinstance(response, dict) or response.get("error"):
            response = self.gateway.faction_crimes_v2(category="available,completed", pool=pool)
            return response, False

        return response, True

    #######################################################

    def fetch_cpr_rows(self, faction=None):

        snapshot = self.fetch_snapshot(faction=faction)

        return snapshot["active_slots"], snapshot["cpr_rows"]

    #######################################################

    def fetch_roster_members(self, faction=None):

        faction_tag, faction_id = self._resolve_faction_meta(faction)
        response = self.gateway.faction_basic_crimes_members_v2(category="available,completed", pool=faction_tag)
        members = CrimeParser.parse_members(response, faction_id=faction_id, faction_tag=faction_tag)

        if members:
            return members

        # Fallback for keys that cannot access the combined v2 endpoint.
        response = self.gateway.faction_basic(pool=faction_tag)
        return CrimeParser.parse_members(response, faction_id=faction_id, faction_tag=faction_tag)

    #######################################################

    def backfill_completed_slots(self, pages=50, faction=None):
        """
        Walk completed crimes pages for historical CPR accumulation.
        """
        faction_tag, faction_id = self._resolve_faction_meta(faction)
        member_names = self._get_member_name_map(faction=faction)
        item_names = self._get_item_name_map()
        all_slots = []

        for page in range(max(1, int(pages or 1))):
            offset = page * 100
            response = self.gateway.faction_crimes_v2(
                category="completed",
                offset=offset,
                limit=100,
                pool=faction_tag,
            )

            if not isinstance(response, dict) or response.get("error"):
                break

            page_slots = CrimeParser.parse_slots(
                response,
                member_names=member_names,
                allowed_statuses={"*"},
                item_names=item_names,
                faction_id=faction_id,
                faction_tag=faction_tag,
            )

            raw_crimes = response.get("crimes", [])
            raw_count = len(raw_crimes) if isinstance(raw_crimes, list) else len(raw_crimes or {})
            if raw_count == 0:
                break

            if page_slots:
                all_slots.extend(page_slots)

            if raw_count < 100:
                break

        return all_slots

    #######################################################

    def _get_member_name_map(self, faction=None):

        return {
            int(member["user_id"]): member["user_name"]
            for member in self.fetch_roster_members(faction=faction)
        }

    #######################################################

    def _get_item_name_map(self):

        response = self.gateway.torn_items()
        if not isinstance(response, dict):
            return {}

        items = response.get("items", {})
        if not isinstance(items, dict):
            return {}

        mapping = {}
        for item_id, payload in items.items():
            if not isinstance(payload, dict):
                continue
            name = payload.get("name")
            if not name:
                continue
            try:
                mapping[int(item_id)] = name
            except Exception:
                mapping[str(item_id)] = name

        return mapping
