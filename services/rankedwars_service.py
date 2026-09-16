"""
services/rankedwars_service.py

Fetch ranked wars from the API and parse them into model instances.
No database calls — just API interaction and parsing.
"""

from modules.rankedwars import parser


class RankedWarsService:
    """
    Stateless service for ranked wars API access and parsing.
    """
    
    def __init__(self, gateway, logger, settings):
        self.gateway = gateway
        self.logger = logger
        self.settings = settings
    
    def fetch_wars(self, faction=None):
        """
        Fetch ranked wars from faction endpoint.
        
        Returns:
            List of RankedWar model instances.
        """
        cfg = self.settings.get_faction(faction) if self.settings else None
        our_faction_id = cfg.faction_id if cfg else (self.settings.faction_id if self.settings else None)
        faction_tag = cfg.tag if cfg else "GTS"
        
        self.logger.info(f"Fetching faction ranked wars [{faction_tag}]...")
        
        response = self.gateway.faction_rankedwars(pool=faction_tag)
        
        if isinstance(response, dict) and response.get("error"):
            error_code = response["error"].get("code")
            error_msg = response["error"].get("info", "Unknown error")
            self.logger.error(f"API error {error_code}: {error_msg}")
            return []
        
        # Parse the response
        wars = parser.parse(
            response,
            our_faction_id=our_faction_id,
            synced_at=None,  # defaults to now in parser
        )
        for w in wars:
            w.faction_tag = faction_tag
        
        self.logger.info(f"Fetched {len(wars)} ranked wars [{faction_tag}]")
        
        return wars
