"""
Repository for Attack model.
"""

from repositories.base_repository import Repository
from models.attack import Attack


class AttackRepository(Repository):

    def __init__(self, database):
        super().__init__(database, Attack)

    ##########################################################

    def exists(self, attack_id: int) -> bool:

        return (
            self.query()
            .where("attack_id", attack_id)
            .first()
            is not None
        )

    ##########################################################

    def latest_attack(self, faction_tag=None):
        if faction_tag:
            rows = self.db.select("""
                SELECT attack_id
                FROM attacks
                WHERE faction_tag = ?
                ORDER BY attack_id DESC
                LIMIT 1
            """, (faction_tag,))
        else:
            rows = self.db.select("""
                SELECT attack_id
                FROM attacks
                ORDER BY attack_id DESC
                LIMIT 1
            """)

        if rows:
            return rows[0]["attack_id"]

        return None

    ##########################################################

    def adopt_untagged(self, faction_tag, faction_id):
        """
        One-time repair for attacks synced before faction isolation existed.
        Tags legacy rows (faction_tag IS NULL) that clearly belong to this
        faction, based on the Torn faction_id appearing as attacker or defender.
        Returns the number of rows adopted.
        """
        if not faction_tag or not faction_id:
            return 0

        cursor = self.db.execute(
            """
            UPDATE attacks
            SET faction_tag = ?, faction_id = ?
            WHERE faction_tag IS NULL
              AND (attacker_faction_id = ? OR defender_faction_id = ?)
            """,
            (faction_tag, int(faction_id), int(faction_id), int(faction_id)),
        )
        self.db.commit()
        return cursor.rowcount if cursor else 0

    ##########################################################

    def by_chain(self, chain):

        return (
            self.query()
            .where("chain", chain)
            .all()
        )

    ##########################################################

    def by_attacker(self, attacker_id):

        return (
            self.query()
            .where("attacker_id", attacker_id)
            .all()
        )

    ##########################################################

    def by_defender(self, defender_id):

        return (
            self.query()
            .where("defender_id", defender_id)
            .all()
        )

    ##########################################################

    def by_result(self, result):

        return (
            self.query()
            .where("result", result)
            .all()
        )