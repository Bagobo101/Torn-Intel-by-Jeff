"""
modules/chains/sync.py

Keeps the local chains table up to date.
"""

from core.sync import BaseSync
from core.schema import SchemaBuilder
from models.chain import Chain
from repositories.chain_repository import ChainRepository


class ChainSync(BaseSync):

    name = "Chains"

    def __init__(self, services):

        super().__init__(services)

        self.chains = services.chains

        self.repo = ChainRepository(services.database)

        SchemaBuilder(
            services.database,
            services.logger
        ).create(Chain)

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
                self.logger.info(f"Syncing chains for faction {f.tag} ({f.name})...")
                total += self._sync_one_faction(mode=mode, filters=filters, faction=f.tag, **kwargs)
            return total

        return self._sync_one_faction(mode=mode, filters=filters, faction=faction, **kwargs)

    def _sync_one_faction(self, mode="backfill", filters=None, faction=None, **kwargs):

        if mode == "backfill":
            from_ts = kwargs.get("from_timestamp")
            to_ts = kwargs.get("to_timestamp")
            return self._backfill(filters, from_ts, to_ts, faction=faction)
        elif mode == "live":
            return self._live(faction=faction)

        raise ValueError(
            f"Unknown sync mode for chains: '{mode}'"
        )

    #######################################################

    def _backfill(self, filters, from_timestamp=None, to_timestamp=None, faction=None):
        """
        Sync chains from the API, optionally filtered by timestamp range.
        """
        faction_id, faction_tag = self._resolve_faction_meta(faction)
        total = 0

        for page in self.chains.iter_pages(faction_tag=faction_tag):

            for chain in page:

                if self.repo.exists(chain.chain_id):
                    # Skip already synced chains
                    continue

                # Apply timestamp filtering if provided
                if from_timestamp is not None:
                    # Skip chains that ended before the start of the range
                    if chain.timestamp_end < from_timestamp:
                        continue
                
                if to_timestamp is not None:
                    # Skip chains that started after the end of the range
                    if chain.timestamp_start > to_timestamp:
                        continue

                chain.faction_id = faction_id
                chain.faction_tag = faction_tag
                self.repo.insert(chain)

                total += 1

        return total

    #######################################################

    def _live(self, faction=None):
        """
        Sync new chains since last import.
        """
        return self._backfill(None, faction=faction)
        
        for page in self.chains.iter_pages():
            
            for chain in page:
                
                # Only import chains we don't have yet
                if self.repo.exists(chain.chain_id):
                    continue
                
                # Insert new chain
                self.repo.insert(chain)
                total += 1
        
        return total
