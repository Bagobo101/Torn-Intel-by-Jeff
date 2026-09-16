"""
services/attack_service.py

Turns raw Torn attack data into parsed Attack objects.
No database or HTTP logic lives here.
"""

from modules.attacks.parser import AttackParser


class AttackService:

    def __init__(self, gateway, logger):

        self.gateway = gateway
        self.logger = logger

    #######################################################

    def latest(self, filters=None, faction_tag=None):

        response = self.gateway.faction_attacks(filters=filters, pool=faction_tag or "default")

        return self._parse_all(response)

    #######################################################

    def iter_pages(
        self,
        filters=None,
        sort="DESC",
        from_timestamp=None,
        to_timestamp=None,
        faction_tag=None,
    ):
        """
        Yields one list of parsed Attack objects per page.
        Handles both v1 and v2 API formats with proper pagination.
        """
        pool = faction_tag or "default"

        response = self.gateway.faction_attacks(
            filters=filters,
            sort=sort,
            from_timestamp=from_timestamp,
            to_timestamp=to_timestamp,
            pool=pool,
        )

        consecutive_empty_pages = 0

        while True:

            attacks = self._parse_all(response)

            if not attacks:
                consecutive_empty_pages += 1
                if consecutive_empty_pages >= 2:
                    break
                yield []
                continue

            consecutive_empty_pages = 0
            yield attacks

            raw_attacks = response.get("attacks", [])
            is_v1 = isinstance(raw_attacks, dict)

            if is_v1 and attacks and sort == "DESC":
                oldest_timestamp = min(a.timestamp_started for a in attacks)
                
                if from_timestamp is not None and oldest_timestamp < from_timestamp:
                    response = self.gateway.faction_attacks(
                        filters=filters,
                        sort=sort,
                        from_timestamp=from_timestamp,
                        to_timestamp=oldest_timestamp - 1,
                        pool=pool,
                    )
                    next_attacks = self._parse_all(response)
                    if next_attacks:
                        yield next_attacks
                    break

                response = self.gateway.faction_attacks(
                    filters=filters,
                    sort=sort,
                    from_timestamp=from_timestamp,
                    to_timestamp=oldest_timestamp - 1,
                    pool=pool,
                )

            else:
                next_url = (
                    response
                    .get("_metadata", {})
                    .get("links", {})
                    .get("next")
                )

                if not next_url:
                    break

                response = self.gateway.follow(next_url, pool=pool)

    #######################################################

    def _parse_all(self, response):

        raw_attacks = response.get("attacks", [])

        # Handle both v1 (dict keyed by ID) and v2 (list) formats
        if isinstance(raw_attacks, dict):
            # V1: attacks are keyed by ID, need to pass ID to parser
            return [
                AttackParser.parse({**attack, "id": int(attack_id)})
                for attack_id, attack in raw_attacks.items()
            ]
        else:
            # V2: attacks are in a list, ID is already in each attack
            return [
                AttackParser.parse(attack)
                for attack in raw_attacks
            ]