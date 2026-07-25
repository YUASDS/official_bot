from __future__ import annotations


def build_day_monster_pool(max_day: int = 40) -> dict[str, list[str]]:
    """构建关卡到怪物池的映射，越靠后越偏向高强度怪物。"""
    if max_day < 1:
        return {}

    pools: dict[str, list[str]] = {}
    for day in range(1, max_day + 1):
        if day <= 10:
            pools[str(day)] = ["2", "3", "4"]
        elif day <= 20:
            pools[str(day)] = ["7", "8", "12"]
        elif day <= 30:
            pools[str(day)] = ["15", "18", "22"]
        else:
            pools[str(day)] = ["26", "31", "35"]

    # 关键里程碑日使用更明确的Boss池
    pools["10"] = ["10", "11", "14"]
    if max_day >= 20:
        pools["20"] = ["20", "24", "25"]
    if max_day >= 30:
        pools["30"] = ["30", "32", "34"]
    if max_day >= 40:
        pools["40"] = ["36", "38", "40"]

    return pools
