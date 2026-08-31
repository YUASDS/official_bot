class DaylyLimit:
    limit_dict: dict[str, dict[str, int]] = {}

    @classmethod
    async def day_check(cls, func: str, qq: str, dat_limit: int = 3):
        if qq not in cls.limit_dict:
            cls.limit_dict[qq] = limit_today(qq)
        func_limit = cls.limit_dict[qq].get(func, 0)
        if func_limit >= dat_limit:
            return False
        cls.limit_dict[qq][func] = func_limit + 1
        limit_add_count(qq, func)
        return True

    @classmethod
    def DayCheck(cls, func: str, dat_limit: int = 3):
        async def check(event: MessageEvent):
            if await cls.day_check(func, str(event.sender.id), dat_limit):
                return
            await autoSendMessage(
                event.sender,
                " 今天已经到了上限次数了，前辈可不能贪心哦~",
                event.messageChain.getFirst(Source).id,
            )
            raise ExecutionStop()

        return Depend(check)
