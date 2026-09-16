"""
repositories/armoury_news_repository.py

Data access layer for armoury news events.
"""

from repositories.base_repository import Repository
from models.armoury_news import ArmouryNews


class ArmouryNewsRepository(Repository):
    """Query and store armoury news events"""
    
    def __init__(self, database):
        super().__init__(database, ArmouryNews)
    
    def by_player(self, player_id, limit=100, faction_tag=None):
        """Get all armoury events for a player"""
        sql = "SELECT * FROM armoury_news WHERE player_id = ?"
        params = [player_id]
        if faction_tag:
            sql += " AND faction_tag = ?"
            params.append(faction_tag)
        sql += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)
        return self.db.select(sql, tuple(params))
    
    def by_item(self, item_id, limit=100, faction_tag=None):
        """Get all usage events for an item"""
        sql = "SELECT * FROM armoury_news WHERE item_id = ?"
        params = [item_id]
        if faction_tag:
            sql += " AND faction_tag = ?"
            params.append(faction_tag)
        sql += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)
        return self.db.select(sql, tuple(params))
    
    def by_type(self, event_type, limit=100, faction_tag=None):
        """Get events by type (used, deposited, filled, loaned, received)"""
        sql = "SELECT * FROM armoury_news WHERE event_type = ?"
        params = [event_type]
        if faction_tag:
            sql += " AND faction_tag = ?"
            params.append(faction_tag)
        sql += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)
        return self.db.select(sql, tuple(params))
    
    def by_category(self, item_category, limit=100, faction_tag=None):
        """Get events by item category (Medical, Utility, Drug, etc.)"""
        sql = "SELECT * FROM armoury_news WHERE item_category = ?"
        params = [item_category]
        if faction_tag:
            sql += " AND faction_tag = ?"
            params.append(faction_tag)
        sql += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)
        return self.db.select(sql, tuple(params))
    
    def by_timerange(self, from_timestamp, to_timestamp, limit=1000, faction_tag=None):
        """Get events within a timestamp range"""
        sql = "SELECT * FROM armoury_news WHERE timestamp BETWEEN ? AND ?"
        params = [from_timestamp, to_timestamp]
        if faction_tag:
            sql += " AND faction_tag = ?"
            params.append(faction_tag)
        sql += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)
        return self.db.select(sql, tuple(params))
    
    def total_cost_by_type(self, event_type, from_timestamp=None, to_timestamp=None, faction_tag=None):
        """Calculate total cost for event type (used, deposited, etc.)"""
        faction_clause = " AND n.faction_tag = ?" if faction_tag else ""
        if from_timestamp and to_timestamp:
            sql = f"""
                SELECT SUM(n.quantity * COALESCE(p.manual_override, p.market_average, n.item_price, 0)) as total_cost,
                       COUNT(*) as event_count,
                       COUNT(DISTINCT n.player_id) as player_count
                FROM armoury_news n
                LEFT JOIN item_prices p ON p.item_id = n.item_id
                WHERE n.event_type = ? AND n.timestamp BETWEEN ? AND ?{faction_clause}
            """
            params = [event_type, from_timestamp, to_timestamp]
            if faction_tag:
                params.append(faction_tag)
            result = self.db.select(sql, tuple(params))
        else:
            sql = f"""
                SELECT SUM(n.quantity * COALESCE(p.manual_override, p.market_average, n.item_price, 0)) as total_cost,
                       COUNT(*) as event_count,
                       COUNT(DISTINCT n.player_id) as player_count
                FROM armoury_news n
                LEFT JOIN item_prices p ON p.item_id = n.item_id
                WHERE n.event_type = ?{faction_clause}
            """
            params = [event_type]
            if faction_tag:
                params.append(faction_tag)
            result = self.db.select(sql, tuple(params))
        
        if result:
            return dict(result[0])
        return {"total_cost": 0, "event_count": 0, "player_count": 0}
    
    def total_cost_by_category(self, item_category, from_timestamp=None, to_timestamp=None, faction_tag=None):
        """Calculate total cost for item category"""
        faction_clause = " AND n.faction_tag = ?" if faction_tag else ""
        if from_timestamp and to_timestamp:
            sql = f"""
                SELECT SUM(n.quantity * COALESCE(p.manual_override, p.market_average, n.item_price, 0)) as total_cost,
                       COUNT(*) as event_count,
                       COUNT(DISTINCT n.item_id) as item_count,
                       COUNT(DISTINCT n.player_id) as player_count
                FROM armoury_news n
                LEFT JOIN item_prices p ON p.item_id = n.item_id
                WHERE n.item_category = ? AND n.timestamp BETWEEN ? AND ?{faction_clause}
            """
            params = [item_category, from_timestamp, to_timestamp]
            if faction_tag:
                params.append(faction_tag)
            result = self.db.select(sql, tuple(params))
        else:
            sql = f"""
                SELECT SUM(n.quantity * COALESCE(p.manual_override, p.market_average, n.item_price, 0)) as total_cost,
                       COUNT(*) as event_count,
                       COUNT(DISTINCT n.item_id) as item_count,
                       COUNT(DISTINCT n.player_id) as player_count
                FROM armoury_news n
                LEFT JOIN item_prices p ON p.item_id = n.item_id
                WHERE n.item_category = ?{faction_clause}
            """
            params = [item_category]
            if faction_tag:
                params.append(faction_tag)
            result = self.db.select(sql, tuple(params))
        
        if result:
            return dict(result[0])
        return {"total_cost": 0, "event_count": 0, "item_count": 0, "player_count": 0}
    
    def latest_timestamp(self, faction_id=None, faction_tag=None):
        """Get the most recent armoury news timestamp"""
        sql = "SELECT MAX(timestamp) as latest FROM armoury_news WHERE 1=1"
        params = []
        if faction_tag:
            sql += " AND faction_tag = ?"
            params.append(faction_tag)
        elif faction_id:
            sql += " AND faction_id = ?"
            params.append(int(faction_id))
        result = self.db.select(sql, tuple(params))
        if result and result[0]["latest"]:
            return result[0]["latest"]
        return None
    
    def get_latest_event_id(self):
        """Get the highest event_id for live sync"""
        sql = "SELECT MAX(event_id) as max_id FROM armoury_news"
        result = self.db.select(sql)
        if result and result[0]["max_id"]:
            return result[0]["max_id"]
        return 0
    
    def exists(self, event_id: int) -> bool:
        """Check if an armoury event already exists"""
        sql = "SELECT 1 FROM armoury_news WHERE event_id = ? LIMIT 1"
        result = self.db.select(sql, (event_id,))
        return bool(result)
