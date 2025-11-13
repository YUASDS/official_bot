import json
import math
import httpx

from loguru import logger
from peewee import SqliteDatabase, Model, CharField, IntegerField, DoesNotExist

COIN_NAME = "乌帕"

db = SqliteDatabase("./database/userData.db")


class BaseModel(Model):
    class Meta:
        database = db


class User(BaseModel):
    user_id = CharField()
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


db.create_tables([User], safe=True)


def init_user(user_id: str):
    user = User.select().where(User.user_id == user_id)
    if not user.exists():
        p = User(user_id=user_id, gold=0)
        p.save()
        logger.info(f"已初始化{user_id}")


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
        except ValueError:
            return 0

    return init


def Updata_Decorator(func):
    async def init(*args, **kwargs):
        try:
            if s := await func(*args, **kwargs):
                return s
            if args:
                init_user(args[0])
            if kwargs:
                init_user(kwargs["user_id"])
            return await func(*args, **kwargs)
        except ValueError:
            return 0

    return init


@Decorator
async def is_sign(user_id: str):
    user: User = User.get(user_id=user_id)
    if user.is_sign:
        return False
    p = User.update(is_sign=1, sign_num=User.sign_num + 1).where(
        User.user_id == user_id
    )
    p.execute()
    return True


@Decorator
def get_info(user_id: str):
    # sourcery skip: inline-immediately-returned-variable
    user: User_info = User.get(user_id=user_id)
    return user


@add_Decorator
def add_gold(user_id: str, num: int):
    logger.info(f"{user_id}的乌帕增加了{num}")
    return User.update(gold=User.gold + num).where(User.user_id == user_id).execute()


@Decorator
async def reduce_gold(user_id: str, num: int, force: bool = False):
    init_user(user_id)
    gold_num = User.get(user_id=user_id).gold
    if gold_num < num:
        if not force:
            return False
        p = User.update(gold=0).where(User.user_id == user_id)
    else:
        p = User.update(gold=User.gold - num).where(User.user_id == user_id)
    return p.execute()


async def trans_all_gold(from_user_id: str, to_user_id: str) -> int:
    init_user(from_user_id)
    init_user(to_user_id)
    from_user_gold = User.get(user_id=from_user_id).gold
    await reduce_gold(from_user_id, from_user_gold)
    add_gold(to_user_id, from_user_gold)
    return from_user_gold


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


def ladder_rent_collection():
    user_list = User.select().where(User.gold >= 1000).order_by(User.gold.desc())
    total_rent = 0
    for user in user_list:
        # user: User
        leadder_rent = 1 - (math.floor(user.gold / 1000) / 100)
        User.update(gold=user.gold * leadder_rent).where(
            User.user_id == user.user_id
        ).execute()
        gold = User.get(User.user_id == user.user_id).gold
        total_rent += user.gold - gold
        logger.info(f"{user.user_id} 被收取 {user.gold - gold} {COIN_NAME}")

    return total_rent


def set_all_user_gold(gold: int):
    User.update(gold=gold).execute()
