import os

from loguru import logger
from peewee import (
    CharField,
    DoesNotExist,
    IntegerField,
    IntegrityError,
    Model,
    SqliteDatabase,
)

# 测试通过 STORYTELLER_USER_DB_PATH 指向临时库，避免污染生产 userData.db。
USER_DB_PATH = os.environ.get("STORYTELLER_USER_DB_PATH", "./database/userData.db")
db = SqliteDatabase(
    USER_DB_PATH,
    timeout=30,
    pragmas={"journal_mode": "wal", "busy_timeout": 30000},
)


class BaseModel(Model):
    class Meta:
        database = db


class User(BaseModel):
    user_id = CharField(unique=True)
    is_sign = IntegerField(default=0)
    sign_num = IntegerField(default=0)
    english_answer = IntegerField(default=0)
    gold = IntegerField(default=0)
    talk_num = IntegerField(default=0)
    favor = IntegerField(default=0)
    favor_data = IntegerField(default=0)

    class Meta:  # type:ignore
        table_name = "user_info"


class User_info:
    user_id: str
    id: int
    is_sign: int
    sign_num: int
    english_answer: int
    gold: int
    talk_num: int
    favor: int
    favor_data: int

    class Meta:
        table_name = "user_info"


def _initialize_user_table() -> None:
    # get_info 一直以最早的行作为账户。旧重复行完整归档，保留原余额和签到记录。
    with db.atomic("IMMEDIATE"):
        if not User.table_exists():
            db.create_tables([User])
            return

        # 兼容 Peewee 索引、自建索引和表级 UNIQUE 约束。
        for index in db.get_indexes("user_info"):
            if (index.unique and index.columns == ["user_id"]
                    and "WHERE" not in (index.sql or "").upper()):
                return

        duplicates = "id NOT IN (SELECT MIN(id) FROM user_info GROUP BY user_id)"
        count = db.execute_sql(
            f"SELECT COUNT(*) FROM user_info WHERE {duplicates}"
        ).fetchone()[0]
        if count:
            db.execute_sql(
                "CREATE TABLE IF NOT EXISTS user_info_duplicates AS "
                "SELECT * FROM user_info WHERE 0"
            )
            db.execute_sql(
                f"INSERT INTO user_info_duplicates SELECT * FROM user_info WHERE {duplicates}"
            )
            db.execute_sql(f"DELETE FROM user_info WHERE {duplicates}")
        # 旧库可能已有与 Peewee 默认名称相同的普通索引。
        db.execute_sql("CREATE UNIQUE INDEX user_info_user_id_uniq ON user_info(user_id)")
    if count:
        logger.warning(f"已归档 {count} 条重复账户至 user_info_duplicates，当前账户沿用最早记录")


_initialize_user_table()


def init_user(user_id: str):
    user = User.select().where(User.user_id == user_id)
    if not user.exists():
        try:
            User.create(user_id=user_id, gold=0)
            logger.info(f"已初始化{user_id}")
        except IntegrityError:
            logger.debug(f"init_user 并发创建已存在: {user_id}")


def Decorator(func):
    def init(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except DoesNotExist:
            if args:
                init_user(args[0])
            if kwargs:
                init_user(kwargs["user_id"])
            return func(*args, **kwargs)

    return init


def add_Decorator(func):
    def init(*args, **kwargs):
        try:
            if s := func(*args, **kwargs):
                return s
            if args:
                init_user(args[0])
            if kwargs:
                init_user(kwargs["user_id"])
            return func(*args, **kwargs)
        except ValueError as e:
            logger.error(f"add_Decorator error in {func.__name__}: {e}")
            return 0

    return init


@Decorator
def get_info(user_id: str):
    # 旧库若曾有重复 user_id，取最早一行并告警，避免 MultipleObjectsReturned 炸主流程。
    user = User.select().where(User.user_id == user_id).order_by(User.id).first()
    if user is None:
        raise DoesNotExist
    return user


def _record_gold_ledger(user_id: str, delta: int) -> None:
    """统计二期：乌帕变更后写 gold_ledger（写后不理，绝不回抛影响主流程）。

    余额唯一入口在此收口，balance_after 由 stats_service 从 userData.db 冗余快照。
    优先复用已加载模块身份（插件在 bot 内以 plugins.StoryTeller 身份加载，
    独立测试/脚本可能以 src.* 顶层身份加载），避免触发插件包 __init__。
    """
    import sys

    try:
        if "plugins.StoryTeller.src.services.stats_service" in sys.modules:
            record_gold = sys.modules[
                "plugins.StoryTeller.src.services.stats_service"
            ].record_gold
        elif "src.services.stats_service" in sys.modules:
            record_gold = sys.modules["src.services.stats_service"].record_gold
        else:
            from plugins.StoryTeller.src.services.stats_service import record_gold

        record_gold(user_id, delta)
    except Exception as e:  # noqa: BLE001 - 统计写后不理
        logger.warning(f"gold_ledger record skipped: {type(e).__name__} {e}")


@add_Decorator
def add_gold(user_id: str, num: int):
    logger.info(f"{user_id}的乌帕增加了{num}")
    result = (
        User.update(gold=User.gold + num).where(User.user_id == user_id).execute()
    )
    if result:
        _record_gold_ledger(user_id, num)
    return result


@Decorator
def reduce_gold(user_id: str, num: int, force: bool = False):
    init_user(user_id)
    user = User.select().where(User.user_id == user_id).order_by(User.id).first()
    if user is None:
        return False
    gold_num = user.gold
    if gold_num < num:
        if not force:
            return False
        p = User.update(gold=0).where(User.user_id == user_id)
        delta = -gold_num
    else:
        p = User.update(gold=User.gold - num).where(User.user_id == user_id)
        delta = -num
    result = p.execute()
    if result:
        _record_gold_ledger(user_id, delta)
    return result


async def reset_sign():
    User.update(is_sign=0).where(User.is_sign == 1).execute()
    return


async def all_sign_num():
    all_num = User.select().count()
    sign_num = User.select().where(User.is_sign == 1).count()
    return [sign_num, all_num]


async def give_all_gold(num: int):
    User.update(gold=User.gold + num).execute()
    return


def set_all_user_gold(gold: int):
    User.update(gold=gold).execute()
