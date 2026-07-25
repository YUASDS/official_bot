class BattleManager:
    _instance = None
    def __init__(self) -> None:
        self.battles = {}

    @classmethod
    def get_instance(cls):
        if not cls._instance: cls._instance = cls()
        return cls._instance

    def add_battle(self, user_id, service) -> None:
        self.battles[user_id] = service

    def get_battle(self, user_id):
        return self.battles.get(user_id)

    def remove_battle(self, user_id) -> None:
        if user_id in self.battles:
            del self.battles[user_id]

battle_manager = BattleManager.get_instance()
