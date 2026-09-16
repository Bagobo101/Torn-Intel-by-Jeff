"""
modules/attacks/sync.py

Keeps the local attacks table up to date. Two modes:
- backfill: walk backward from now, stop once an
  already-synced attack is hit.
- live: pick up from the last synced attack and walk
  forward, inserting anything new.

Read-only lookups against already-synced data live in
modules/attacks/queries.py, not here.
"""

from core.sync import BaseSync
from core.schema import SchemaBuilder
from models.attack import Attack
from repositories.attack_repository import AttackRepository
from services.http_client import RateLimitError
import time


class AttackSync(BaseSync):

    name = "Attacks"

    def __init__(self, services):

        super().__init__(services)

        self.attacks = services.attacks

        self.repo = AttackRepository(services.database)

        SchemaBuilder(
            services.database,
            services.logger
        ).create(Attack)

    #######################################################

    def _resolve_faction_meta(self, faction=None):
        settings = self.services.settings
        if settings:
            cfg = settings.get_faction(faction)
            if cfg:
                return cfg.faction_id, cfg.tag
        return settings.faction_id if settings else None, "GTS"

    def sync(self, mode="backfill", filters=None, faction=None, **kwargs):

        if str(faction or "").strip().lower() == "all":
            total = 0
            for f in self.services.settings.list_factions():
                self.logger.info(f"Syncing attacks for faction {f.tag} ({f.name})...")
                total += self._sync_one_faction(mode=mode, filters=filters, faction=f.tag, **kwargs)
            return total

        return self._sync_one_faction(mode=mode, filters=filters, faction=faction, **kwargs)

    def _sync_one_faction(self, mode="backfill", filters=None, faction=None, **kwargs):

        if mode == "backfill":
            return self._backfill(
                filters,
                from_timestamp=kwargs.get("from_timestamp"),
                to_timestamp=kwargs.get("to_timestamp"),
                faction=faction,
            )

        if mode == "live":
            return self._live(filters, faction=faction)

        raise ValueError(
            f"Unknown sync mode for attacks: '{mode}'"
        )

    #######################################################

    def _backfill(self, filters, from_timestamp=None, to_timestamp=None, faction=None):
        """
        Walk backward through attack history, importing any records not yet synced.
        """
        faction_id, faction_tag = self._resolve_faction_meta(faction)
        total = 0
        checkpoint_key = f"attacks_backfill_{faction_tag}"

        start_to_timestamp = to_timestamp
        if start_to_timestamp is None:
            resume_to = self._get_resume_checkpoint(checkpoint_key)
            if resume_to is not None:
                start_to_timestamp = int(resume_to)
                self.logger.info(
                    f"Resuming attacks backfill [{faction_tag}] from checkpoint (to={start_to_timestamp})"
                )

        # Seed resume anchor so failures before first fetched page still resume deterministically.
        initial_anchor = int(start_to_timestamp) if start_to_timestamp is not None else int(time.time())
        self._set_resume_checkpoint(
            checkpoint_key,
            initial_anchor,
            note=f"initial attacks backfill anchor {faction_tag}",
        )

        try:
            for page in self.attacks.iter_pages(
                filters=filters,
                sort="DESC",
                from_timestamp=from_timestamp,
                to_timestamp=start_to_timestamp,
                faction_tag=faction_tag,
            ):
                if page:
                    next_to = min(a.timestamp_started for a in page) - 1
                    if next_to > 0:
                        self._set_resume_checkpoint(
                            checkpoint_key,
                            next_to,
                            note=f"auto-saved during attacks backfill {faction_tag}",
                        )

                for attack in page:
                    if self.repo.exists(attack.attack_id):
                        continue  # Skip duplicates, continue deeper history

                    attack.faction_id = faction_id
                    attack.faction_tag = faction_tag
                    self.repo.insert(attack)
                    total += 1

        except RateLimitError as exc:
            self.logger.warning(
                f"Attacks backfill [{faction_tag}] paused due to rate limit: {exc}"
            )
            resume_to = self._get_resume_checkpoint(checkpoint_key)
            if resume_to is not None:
                self.logger.info(
                    f"Resume with: python main.py sync attacks --mode backfill --to {resume_to} --faction {faction_tag}"
                )
            return total

        self._clear_resume_checkpoint(checkpoint_key)
        return total

    #######################################################

    def _live(self, filters, faction=None):

        faction_id, faction_tag = self._resolve_faction_meta(faction)
        last_id = self.repo.latest_attack(faction_tag=faction_tag)

        from_timestamp = None

        if last_id is not None:

            existing = self.repo.where("attack_id", last_id).first()

            if existing:
                from_timestamp = existing["timestamp_started"] + 1

        total = 0

        for page in self.attacks.iter_pages(
            filters=filters,
            sort="ASC",
            from_timestamp=from_timestamp,
            faction_tag=faction_tag,
        ):

            for attack in page:

                if self.repo.exists(attack.attack_id):
                    continue

                attack.faction_id = faction_id
                attack.faction_tag = faction_tag
                self.repo.insert(attack)

                total += 1

        return total