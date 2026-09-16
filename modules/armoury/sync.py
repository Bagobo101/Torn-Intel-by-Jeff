"""
modules/armoury/sync.py

Sync armoury news events from Torn API into database.
"""

from core.sync import BaseSync
from core.schema import SchemaBuilder
from models.armoury_news import ArmouryNews, ItemPrice
from repositories.armoury_news_repository import ArmouryNewsRepository
from repositories.item_price_repository import ItemPriceRepository
from services.http_client import RateLimitError
import time


class ArmourySync(BaseSync):
    """Sync faction armoury news"""
    
    name = "Armoury"
    description = "Faction armoury item usage tracking"
    
    def __init__(self, services):
        """Initialize sync with services"""
        super().__init__(services)
        self.database = self.db  # Alias for consistency with other modules
        self.repo = ArmouryNewsRepository(self.database)
        self.item_repo = ItemPriceRepository(self.database)
        self.armoury = services.armoury
        
        # Ensure tables exist and missing columns are added
        schema = SchemaBuilder(self.database, self.logger)
        schema.create(ArmouryNews)
        schema.create(ItemPrice)
    
    def _resolve_faction_meta(self, faction=None):
        settings = self.services.settings
        if settings:
            cfg = settings.get_faction(faction)
            if cfg:
                return cfg.faction_id, cfg.tag
        return settings.faction_id if settings else None, "GTS"

    def sync(self, mode="backfill", filters=None, faction=None, **kwargs):
        """
        Sync armoury news.
        
        Args:
            mode: 'backfill', 'live', or 'search'
            filters: Query filters (unused for now)
            faction: Faction tag (e.g. GTS, GTH, all)
            **kwargs: Additional options (from_timestamp, to_timestamp)
        
        Returns:
            Count of events imported
        """
        if str(faction or "").strip().lower() == "all":
            total = 0
            for f in self.services.settings.list_factions():
                self.logger.info(f"Syncing armoury for faction {f.tag} ({f.name})...")
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
        elif mode == "live":
            return self._live(filters, faction=faction)
        elif mode == "search":
            return 0
        
        return 0
    
    def _backfill(self, filters, from_timestamp=None, to_timestamp=None, faction=None):
        """
        Backfill armoury news using timestamp-based pagination.
        """
        faction_id, faction_tag = self._resolve_faction_meta(faction)
        if not faction_id:
            self.logger.error(f"TORN_FACTION_ID not set in config for faction {faction or 'default'}")
            return 0
        
        self.logger.info(f"Armoury backfill starting for faction {faction_tag} ({faction_id})")
        
        total = 0

        checkpoint_key = f"armoury_backfill_{faction_tag}"
        start_to_timestamp = to_timestamp
        if start_to_timestamp is None:
            resume_to = self._get_resume_checkpoint(checkpoint_key)
            if resume_to is not None:
                start_to_timestamp = int(resume_to)
                self.logger.info(
                    f"Resuming armoury backfill [{faction_tag}] from checkpoint (to={start_to_timestamp})"
                )

        # Seed resume anchor so failures before first fetched page still resume deterministically.
        initial_anchor = int(start_to_timestamp) if start_to_timestamp is not None else int(time.time())
        self._set_resume_checkpoint(
            checkpoint_key,
            initial_anchor,
            note=f"initial armoury backfill anchor {faction_tag}",
        )

        try:
            for page in self.armoury.iter_pages(
                faction_id=faction_id,
                filters=filters,
                sort="DESC",
                from_timestamp=from_timestamp,
                to_timestamp=start_to_timestamp,
                faction_tag=faction_tag,
            ):
                if page:
                    next_to = min(int(p["timestamp"]) for p in page) - 1
                    if next_to > 0:
                        self._set_resume_checkpoint(
                            checkpoint_key,
                            next_to,
                            note=f"auto-saved during armoury backfill {faction_tag}",
                        )

                for parsed in page:
                    event_id = parsed["event_id"]

                    # Skip if already exists
                    if self.repo.exists(event_id):
                        continue

                    # Get item price and add to parsed data
                    price = self._get_item_price(parsed["item_id"], parsed["item_name"])
                    parsed["item_price"] = price

                    # Insert event
                    self._insert_event(parsed)
                    total += 1

        except RateLimitError as exc:
            self.logger.warning(
                f"Armoury backfill [{faction_tag}] paused due to rate limit: {exc}"
            )
            resume_to = self._get_resume_checkpoint(checkpoint_key)
            if resume_to is not None:
                self.logger.info(
                    f"Resume with: python main.py sync armoury --mode backfill --to {resume_to} --faction {faction_tag}"
                )
            return total

        self._clear_resume_checkpoint(checkpoint_key)

        self.logger.info(f"Armoury backfill [{faction_tag}] complete. Imported {total} events.")
        return total
    
    def _live(self, filters, faction=None):
        """
        Sync only new armoury events.
        Picks up from last synced event timestamp.
        
        Returns:
            Count of events imported
        """
        faction_id, faction_tag = self._resolve_faction_meta(faction)
        if not faction_id:
            self.logger.error(f"TORN_FACTION_ID not set in config for faction {faction or 'default'}")
            return 0
        
        # Get last synced timestamp for this faction
        last_ts = self.repo.latest_timestamp(faction_id=faction_id, faction_tag=faction_tag)
        from_timestamp = None
        
        if last_ts is not None:
            from_timestamp = last_ts + 1
        
        total = 0
        
        # Live walks forward from last timestamp
        for page in self.armoury.iter_pages(
            faction_id=faction_id,
            filters=filters,
            sort="ASC",  # Walk forward for live
            from_timestamp=from_timestamp,
            faction_tag=faction_tag,
        ):
            for parsed in page:
                event_id = parsed["event_id"]
                
                if self.repo.exists(event_id):
                    continue
                
                # Get item price and add to parsed data
                price = self._get_item_price(parsed["item_id"], parsed["item_name"])
                parsed["item_price"] = price
                
                # Insert event
                self._insert_event(parsed)
                total += 1
        
        self.logger.info(f"Armoury live sync [{faction_tag}] complete. Imported {total} events.")
        return total
    
    def _get_item_price(self, item_id, item_name):
        """Get price for item from database or API"""
        if item_id > 0:
            price_data = self.item_repo.get_price(item_id)
            if price_data:
                return price_data.get("effective_price", 0)
        
        # Return 0 if not found (can be filled in later)
        return 0
    
    def _insert_event(self, parsed_event):
        """Insert parsed event into database"""
        sql = """
            INSERT OR IGNORE INTO armoury_news 
            (event_id, faction_id, faction_tag, timestamp, player_id, player_name, event_type, item_id, item_name, 
             item_category, quantity, description, raw_news, item_price, price_source)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        
        self.database.execute(sql, (
            parsed_event["event_id"],
            parsed_event.get("faction_id"),
            parsed_event.get("faction_tag"),
            parsed_event["timestamp"],
            parsed_event["player_id"],
            parsed_event["player_name"],
            parsed_event["event_type"],
            parsed_event["item_id"],
            parsed_event["item_name"],
            parsed_event["item_category"],
            parsed_event["quantity"],
            parsed_event["description"],
            parsed_event["raw_news"],
            parsed_event["item_price"],
            parsed_event["price_source"],
        ))
        
        self.database.commit()
