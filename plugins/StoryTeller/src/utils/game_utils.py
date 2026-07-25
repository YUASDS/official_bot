def action2part(action: str) -> str:
    """Map action to equipment part."""
    action_map = {
        "格斗": "近战",
        "反击": "近战",
        "斧": "近战",
        "剑": "近战",
        "电锯": "近战",
        "射击": "远程",
        "三连射": "远程",
        "换弹": "远程",
        "防具": "防具",
    }
    return action_map.get(action, "")
