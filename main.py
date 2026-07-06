import json, re, shutil, time
from pathlib import Path
from astrbot.api.event import filter
from astrbot.api.star import StarTools
from astrbot.api.provider import LLMResponse, ProviderRequest
from astrbot.api.all import logger, At, Reply, AstrMessageEvent, Context, Star, AstrBotConfig
from astrbot.core.agent.message import TextPart

class 好感度管理系统:
    """
    好感度、印象与关系管理系统 (FavourPro) - 按群隔离版
    - 数据结构:
    {
        "群1": {
            "用户1": {"favour": int, "attitude": str, "relationship": str, "name": str},
            ...
        },
        "群2": {...}
    }
    """
    默认状态 = {"favour": 0, "attitude": "中立", "relationship": "陌生人", "name": ""}

    def __init__(self, 数据目录: Path):
        self.数据目录 = 数据目录
        self.数据目录.mkdir(parents=True, exist_ok=True)
        self.数据文件 = self.数据目录 / "好感数据.json"
        self.备份文件 = self.数据目录 / "好感数据.json.bak"
        self._好感数据 = self._加载数据()  # type: dict[str, dict[str, dict[str, str|int]]]

    def _加载数据(self) -> dict:
        """加载数据"""
        try:
            try:
                with open(self.数据文件, "r", encoding="utf-8") as f:
                    return json.load(f)
            except FileNotFoundError:
                with open(self.数据文件, "w", encoding="utf-8") as f:
                    f.write("{}")
                return {}
        except PermissionError:
            logger.critical(f"读取数据失败，插件退出，请确保有足够权限读写以保证正常运行，数据文件路径：\n{self.数据文件}\n插件退出")
            raise
        except Exception:
            logger.critical(f"读取数据出现问题，请检查数据后重启，数据文件路径：\n{self.数据文件}\n插件退出")
            raise

    def _保存数据(self):
        try:
            #每次都立即备份，备份、写入出现错误立刻退出
            shutil.copy(self.数据文件, self.备份文件)
            with open(self.数据文件, "w", encoding="utf-8") as f:
                json.dump(self._好感数据, f, ensure_ascii=False, indent=2)
        except PermissionError:
            logger.critical(f"保存数据失败，插件退出，请确保有足够权限读写以保证正常运行，内存中的数据：\n\n{str(self._好感数据)}\n\nEND\n\n")
            raise
        except Exception:
            logger.critical(f"保存数据失败，插件退出，内存中的数据：\n\n{str(self._好感数据)}\n\nEND\n\n")
            raise

    def 保存所有数据(self, data:dict) -> None:
        """保存所有数据并更新"""
        self._好感数据 = data
        self._保存数据()

    def 获取所有数据(self) -> dict:
        """获取所有数据"""
        return self._好感数据

    def 获取用户状态(self, 群ID: str, 用户ID: str) -> dict[str, str|int]:
        """获取指定群内用户的状态，自动初始化默认状态"""
        if 群ID not in self._好感数据:
            self._好感数据[群ID] = {}
        if 用户ID not in self._好感数据[群ID]:
            self._好感数据[群ID][用户ID] = self.默认状态.copy()
        return self._好感数据[群ID][用户ID]

    def 获取群组数据(self, 群ID) -> dict[str, dict[str, str|int]]:
        """获取指定群组的数据"""
        if 群ID not in self._好感数据:
            self._好感数据[群ID] = {}
        return self._好感数据[群ID]

    def 保存群组数据(self, 群ID, data:dict[str, dict[str, str|int]]) -> None:
        """保存指定群组的数据"""
        self._好感数据[群ID] = data
        self._保存数据()

    def 保存用户状态(self, 群ID:str, 用户ID:str, data:dict[str, str|int]) -> None:
        """保存指定用户的好感数据"""
        if 群ID not in self._好感数据:
            self._好感数据[群ID] = {}
        self._好感数据[群ID][用户ID] = data
        self._保存数据()

class 喜欢懒大猫(Star):

    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        config.群组隔离 = True
        if not config.好感度提示词.strip():
            config.好感度提示词 = config.schema['好感度提示词']['default']
            logger.warning("配置项好感度提示词为空，已恢复默认")
        self.好感度提示词 = config.好感度提示词.strip()
        config.save_config()
        数据目录 = StarTools.get_data_dir()
        self.好感度系统 = 好感度管理系统(数据目录)

        # 正则表达式（与之前相同）
        self.块匹配正则 = re.compile(
            r"\[\s*(?:Favour:|Attitude:|Relationship:).*?]",
            re.DOTALL
        )
        self.好感正则 = re.compile(r"Favour:\s*(-?\d+)")
        self.印象正则 = re.compile(
            r"Attitude:\s*(.*?)(?=\s*(?:Favour|Attitude|Relationship):|])",
            re.DOTALL
        )
        self.关系正则 = re.compile(
            r"Relationship:\s*(.*?)(?=\s*(?:Favour|Attitude|Relationship):|])",
            re.DOTALL
        )


    async def initialize(self) -> None:
        """可选异步初始化，当插件被激活时会调用这个方法"""

    async def terminate(self) -> None:
        """可选异步终止，当插件被关闭时调用这个方法"""

    @staticmethod
    def 获取上下文群ID(event: AstrMessageEvent) -> str:
        """获取好感度数据分区ID：群聊使用群号，私聊统一使用“私聊”。"""
        return str(event.get_group_id() or "私聊")

    # ------------------- 核心交互 -------------------

    @filter.on_llm_request()
    async def llm请求前(self, event: AstrMessageEvent, req: ProviderRequest):
        """向LLM注入当前用户状态，并指示其更新"""
        群ID = self.获取上下文群ID(event)

        用户ID = event.get_sender_id()

        状态 = self.好感度系统.获取用户状态(群ID, 用户ID)

        # 注入当前状态（包含昵称）
        当前状态提示词 = f"[Favour: {状态['favour']}, Attitude: {状态['attitude']}, Relationship: {状态['relationship']}]"
        req.extra_user_content_parts.append(TextPart(text=f"<favour>{当前状态提示词}</favour>"))

        req.system_prompt += f"\n\n{self.好感度提示词}"

    @filter.on_llm_response()
    async def llm请求后(self, event: AstrMessageEvent, resp: LLMResponse):
        """解析LLM响应中的状态块，更新用户状态，并清理特殊标记"""
        群ID = self.获取上下文群ID(event)

        用户ID = event.get_sender_id()
        名字 = event.get_sender_name()   # 实时昵称，用于保存
        原文 = resp.completion_text

        # 始终更新最新昵称（不论是否有状态块）
        当前状态 = self.好感度系统.获取用户状态(群ID, 用户ID)
        if 当前状态.get('name') != 名字:
            当前状态['name'] = 名字
            self.好感度系统.保存用户状态(群ID, 用户ID, 当前状态)

        # 查找状态块
        块匹配 = self.块匹配正则.search(原文)
        if not 块匹配:
            return
        旧好感值 = 当前状态.get('favour', 0)
        旧印象 = 当前状态.get('attitude', '（无）')
        旧关系 = 当前状态.get('relationship', '（无）')

        # 从回复中移除状态块
        块文本 = 块匹配.group(0)
        清理后文本 = 原文.replace(块文本, '').strip()
        resp.completion_text = 清理后文本

        # 解析块内数据
        好感块 = self.好感正则.search(块文本)
        印象块 = self.印象正则.search(块文本)
        关系块 = self.关系正则.search(块文本)

        if not (好感块 or 印象块 or 关系块):
            return

        # 更新字段
        if 好感块:
            新好感值 = int(好感块.group(1).strip())
            相差 = 新好感值 - 旧好感值

            # 好感度增减阈值限制：每次最多 ±8
            if 相差 > 8:
                新好感值 = 旧好感值 + 8
                logger.warning(f"[好感度限制] 正向变化超限，已截断至 +8 (原增减 {相差:+d})")
                logger.warning(
                    f"[好感度更新] 用户 {名字} 最终好感度: {旧好感值} -> {新好感值} (实际增减 {新好感值 - 旧好感值:+d})")
            elif 相差 < -8:
                新好感值 = 旧好感值 - 8
                logger.warning(f"[好感度限制] 负向变化超限，已截断至 -8 (原增减 {相差:+d})")
                logger.warning(
                    f"[好感度更新] 用户 {名字} 最终好感度: {旧好感值} -> {新好感值} (实际增减 {新好感值 - 旧好感值:+d})")

            # 限制范围在 -100 到 100 之间
            if 新好感值 < -100 or 新好感值 > 100:
                旧范围 = 新好感值
                新好感值 = max(-100, min(100, 新好感值))
                logger.warning(f"[好感度限制] 超出 [-100,100] 范围，已修正: {旧范围} -> {新好感值}")
                logger.warning(
                    f"[好感度更新] 用户 {名字} 最终好感度: {旧好感值} -> {新好感值} (实际增减 {新好感值 - 旧好感值:+d})")

            当前状态['favour'] = 新好感值
            logger.debug(f"用户{名字}（{用户ID}）好感值更新：{旧好感值} -> {新好感值}")

        if 关系块:
            当前状态['relationship'] = 关系块.group(1).strip().strip(' ,，')
            logger.debug(f"用户{名字}（{用户ID}）关系更新：{旧关系} -> {当前状态['relationship']}")

        if 印象块:
            当前状态['attitude'] = 印象块.group(1).strip().strip(' ,，')
            logger.debug(f"用户{名字}（{用户ID}）印象更新：{旧印象} -> {当前状态['attitude']}")

        self.好感度系统.保存用户状态(群ID, 用户ID, 当前状态)

    @staticmethod
    def 获取艾特或引用对象的ID(event:AstrMessageEvent)-> tuple[str, str] | None:
        """返回用户ID，名字"""
        for seg in event.get_messages():
            if isinstance(seg, At):
                qq = str(seg.qq)
                if qq == event.get_self_id():
                    continue
                return qq, seg.name or ""
            if isinstance(seg, Reply):
                qq = str(seg.sender_id)
                if qq == event.get_self_id():
                    continue
                return qq, seg.sender_nickname or ""

        return None

    # ------------------- 管理员命令 -------------------

    @filter.command("查询好感", alias={'好感度', '查看好感'})
    async def 查询好感指令(self, event: AstrMessageEvent, 用户ID: str = "", 群ID:str=None):
        """(管理员) 查询当前群内指定用户的状态，非管理员查询只能自己"""
        群ID = str(群ID or self.获取上下文群ID(event))
        用户ID = str(用户ID)

        名字 = ""
        if event.is_admin():
            if 结果:=self.获取艾特或引用对象的ID(event):
                用户ID, 名字 = 结果
        elif 用户ID:
            yield event.plain_result("错误：此命令仅限管理员使用。")
            return

        目标ID = 用户ID.strip() if 用户ID else event.get_sender_id()
        状态 = self.好感度系统.获取用户状态(群ID, 目标ID)
        if not 名字:
            名字 = 状态['name'] or 用户ID or event.get_sender_name()

        response_text = (
            f"👤 用户 {名字} (ID: {目标ID}) 的状态：\n"
            f"💖 好感度：{状态['favour']}\n"
            f"🤝 关系：{状态['relationship']}\n"
            f"✨ 印象：{状态['attitude']}"
        )
        yield event.plain_result(response_text)

    @filter.command("设置好感")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def 设置好感指令(self, event: AstrMessageEvent, 用户ID: str, 好感值: str, 群ID:str=None):
        """(管理员) 设置当前群内指定用户的好感度"""
        群ID = str(群ID or self.获取上下文群ID(event))
        用户ID = str(用户ID)

        if not event.is_admin():
            yield event.plain_result("错误：此命令仅限管理员使用。")
            return

        try:
            好感值 = int(好感值)
        except ValueError:
            yield event.plain_result("错误：好感度值必须是一个整数。")
            return

        用户ID = 用户ID.strip()
        当前状态 = self.好感度系统.获取用户状态(群ID, 用户ID)
        当前状态['favour'] = 好感值
        # 保留原有昵称，如果没有则设为空
        if 'name' not in 当前状态 or not 当前状态['name']:
            当前状态['name'] = 用户ID
        self.好感度系统.保存用户状态(群ID, 用户ID, 当前状态)

        yield event.plain_result(f"成功：用户 {用户ID} 的好感度已设置为 {好感值}。")

    @filter.command("设置全部")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def 设置全部指令(self, event: AstrMessageEvent, 用户ID: str, 群ID:str=None):
        群ID = str(群ID or self.获取上下文群ID(event))
        用户ID = str(用户ID)

        if not event.is_admin():
            yield event.plain_result("错误：此命令仅限管理员使用。")
            return

        分割 = event.get_message_str().split('\n')
        try:
            好感 = int(分割[1])
            关系 = 分割[2]
            印象 = 分割[3]
        except (ValueError, IndexError):
            yield event.plain_result("格式错误：第一行用户信息，第二行好感度数字，第三行关系，第四行印象，第五行名字（可选）")
            return

        名字 = None
        try:
            名字 = 分割[4]
        except (ValueError, IndexError):
            pass

        当前状态 = self.好感度系统.获取用户状态(群ID, 用户ID)
        回复文本 = (
            f"修改成功，修改前：\n"
            f"用户 {当前状态['name']} (ID: {用户ID}) 的状态：\n"
            f"好感度：{当前状态['favour']}\n"
            f"关系：{当前状态['relationship']}\n"
            f"印象：{当前状态['attitude']}"
        )

        if 名字:
            当前状态['name'] = 名字
        当前状态['favour'] = 好感
        当前状态['relationship'] = 关系
        当前状态['attitude'] = 印象
        self.好感度系统.保存用户状态(群ID, 用户ID, 当前状态)
        状态 = self.好感度系统.获取用户状态(群ID, 用户ID)

        回复文本 += (
            f"\n\n修改后：\n"
            f"用户 {状态['name']} (ID: {用户ID}) 的状态：\n"
            f"好感度：{状态['favour']}\n"
            f"关系：{状态['relationship']}\n"
            f"印象：{状态['attitude']}"
        )
        yield event.plain_result(回复文本)

    @filter.command("设置印象")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def 设置印象指令(self, event: AstrMessageEvent, 用户ID: str, *, 印象: str):
        """(管理员) 设置当前群内指定用户的印象。支持带空格的文本。"""
        群ID = self.获取上下文群ID(event)

        if not event.is_admin():
            yield event.plain_result("错误：此命令仅限管理员使用。")
            return

        用户ID = 用户ID.strip()
        印象 = 印象.strip()
        当前状态 = self.好感度系统.获取用户状态(群ID, 用户ID)
        当前状态['attitude'] = 印象
        if 'name' not in 当前状态 or not 当前状态['name']:
            当前状态['name'] = 用户ID
        self.好感度系统.保存用户状态(群ID, 用户ID, 当前状态)

        yield event.plain_result(f"成功：用户 {用户ID} 的印象已设置为 '{印象}'。")

    @filter.command("设置关系")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def 设置关系指令(self, event: AstrMessageEvent, 用户ID: str, *, 关系: str):
        """(管理员) 设置当前群内指定用户的关系。支持带空格的文本。"""
        群ID = self.获取上下文群ID(event)

        if not event.is_admin():
            yield event.plain_result("错误：此命令仅限管理员使用。")
            return

        用户ID = 用户ID.strip()
        关系 = 关系.strip()
        当前状态 = self.好感度系统.获取用户状态(群ID, 用户ID)
        当前状态['relationship'] = 关系
        if 'name' not in 当前状态 or not 当前状态['name']:
            当前状态['name'] = 用户ID
        self.好感度系统.保存用户状态(群ID, 用户ID, 当前状态)

        yield event.plain_result(f"成功：用户 {用户ID} 的关系已设置为 '{关系}'。")

    @filter.command("重置好感")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def 重置好感指令(self, event: AstrMessageEvent, 用户ID: str, 群ID:str=None):
        """(管理员) 重置当前群内指定用户的全部状态为默认值"""
        群ID = str(群ID or self.获取上下文群ID(event))

        用户ID = str(用户ID)

        if not event.is_admin():
            yield event.plain_result("错误：此命令仅限管理员使用。")
            return

        用户ID = 用户ID.strip()
        # 重置为默认状态，但保留昵称（如果有）
        默认状态 = self.好感度系统.默认状态.copy()
        # 如果原来有昵称，尽量保留，否则使用 user_id 作为临时名
        old_state = self.好感度系统.获取用户状态(群ID, 用户ID)
        默认状态['name'] = old_state.get('name', 用户ID)

        self.好感度系统.保存用户状态(群ID, 用户ID, 默认状态)
        yield event.plain_result(f"成功：用户 {用户ID} 的状态已重置为默认值。")

    @filter.command("重置负面")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def 重置负面指令(self, event: AstrMessageEvent, 群ID: str=None):
        """(管理员) 重置当前群内所有好感度为负数的用户状态"""
        群ID = str(群ID or self.获取上下文群ID(event))

        if not event.is_admin():
            yield event.plain_result("错误：此命令仅限管理员使用。")
            return

        群组数据 = self.好感度系统.获取群组数据(群ID)
        负面用户 = [UID for UID, s in 群组数据.items() if s.get('favour', 0) < 0]

        if not 负面用户:
            yield event.plain_result("信息：当前上下文没有找到任何好感度为负的用户。")
            return

        for UID in 负面用户:
            旧状态 = 群组数据[UID]
            新状态 = self.好感度系统.默认状态.copy()
            新状态['name'] = 旧状态.get('name', UID)
            群组数据[UID] = 新状态

        self.好感度系统.保存群组数据(群ID, 群组数据)
        yield event.plain_result(f"成功：已重置当前群内 {len(负面用户)} 个好感度为负的用户。")

    @filter.command("重置全部")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def 重置全部指令(self, event: AstrMessageEvent, 群ID: str=None):
        """(管理员) 重置当前群内所有用户的状态数据"""
        群ID = str(群ID or self.获取上下文群ID(event))

        if not event.is_admin():
            yield event.plain_result("错误：此命令仅限管理员使用。")
            return
        群组数据 = self.好感度系统.获取群组数据(群ID)
        if 群组数据:
            用户数 = len(群组数据)
            self.好感度系统.保存群组数据(群ID, {})
            yield event.plain_result(f"成功：已清空当前群的全部 {用户数} 个用户数据。")
        else:
            yield event.plain_result("当前群没有任何用户数据。")

    @filter.command("好感排行")
    async def 正好感排行指令(self, event: AstrMessageEvent, 数量: str = "10", 群ID: str=None):
        """显示当前群好感度最高的N个用户"""
        群ID = str(群ID or self.获取上下文群ID(event))

        if not event.is_admin():
            yield event.plain_result("错误：此命令仅限管理员使用。")
            return

        try:
            数量 = int(数量)
            if 数量 <= 0:
                raise ValueError
        except ValueError:
            yield event.plain_result("错误：排行数量必须是一个正整数。")
            return

        群组数据 = self.好感度系统.获取群组数据(群ID)
        if not 群组数据:
            yield event.plain_result("当前上下文没有任何用户数据。")
            return

        # 按好感度降序排序
        排序后 = sorted(
            群组数据.items(),
            key=lambda item: item[1].get('favour', 0),
            reverse=True
        )

        排行列表 = [f"当前群好感度 TOP {数量} 排行榜："]
        for i, (UID, 状态) in enumerate(排序后[:数量]):
            name = 状态.get('name', UID)
            line = (
                f"{i + 1}. {name} (ID: {UID})\n"
                f"    💖 好感：{状态['favour']}\n    🤝 关系：{状态['relationship']}\n    ✨ 印象：{状态['attitude']}"
            )
            排行列表.append(line)

        yield event.plain_result("\n\n".join(排行列表))

    @filter.command("负好感排行")
    async def 负好感排行指令(self, event: AstrMessageEvent, 数量: str = "10", 群ID: str=None):
        """显示当前群好感度最低的N个用户"""
        if 群ID is None:
            群ID = self.获取上下文群ID(event)

        群ID = str(群ID)

        if not event.is_admin():
            yield event.plain_result("错误：此命令仅限管理员使用。")
            return

        try:
            数量 = int(数量)
            if 数量 <= 0:
                raise ValueError
        except ValueError:
            yield event.plain_result("错误：排行数量必须是一个正整数。")
            return

        群组数据 = self.好感度系统.获取群组数据(群ID)
        if not 群组数据:
            yield event.plain_result("当前上下文没有任何用户数据。")
            return

        # 按好感度升序排序
        排序后 = sorted(
            群组数据.items(),
            key=lambda item: item[1].get('favour', 0)
        )

        排行列表 = [f"当前群好感度 BOTTOM {数量} 排行榜："]
        for i, (UID, 状态) in enumerate(排序后[:数量]):
            名字 = 状态.get('name', UID)
            行 = (
                f"{i + 1}. {名字} (ID: {UID})\n"
                f"    💖 好感：{状态['favour']}\n    🤝 关系：{状态['relationship']}\n    ✨ 印象：{状态['attitude']}"
            )
            排行列表.append(行)

        yield event.plain_result("\n".join(排行列表))

    @filter.command("撤销修改")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def 撤销修改(self, event: AstrMessageEvent):
        """(管理员) 从最近的备份文件（好感数据.json.bak）恢复好感度数据，撤销自上次保存以来所有的修改"""
        if not event.is_admin():
            yield event.plain_result("错误：此命令仅限管理员使用。")
            return

        # 将当前数据额外备份一次，防止误操作丢失
        恢复前数据 = self.好感度系统.数据目录 / f"好感数据_恢复前数据_{time.time()}.json"
        shutil.copy(self.好感度系统.数据文件, 恢复前数据)

        try:
            with open(self.好感度系统.备份文件, "r", encoding="utf-8") as f:
                data = json.load(f)
            #再次保存生成新的备份文件，注意此方法已自动加锁
            self.好感度系统.保存所有数据(data)
        except Exception as e:
            logger.error(f"恢复备份失败: {e}")
            yield event.plain_result(f"错误：恢复失败 - {e}")
            return

        yield event.plain_result(f"✅ 已撤销上一个修改（包括llm修改），撤销修改前文件：\n{恢复前数据}")

    # @filter.llm_tool(name="set_user_state")
    # async def 工具_设置用户状态(self, event: AstrMessageEvent, 用户ID: int=None, 好感值: int = None, 关系: str = None,
    #                             印象: str = None) -> str:
    #     """设置指定用户的好感度、关系或印象。至少需要提供一个要修改的参数。
    #     Args:
    #         用户ID(number): （必须）要设置的用户的QQ号
    #         好感值(number): 要设置的好感度数值，范围 -100 到 100 之间的整数
    #         关系(string): 要设置的关系描述
    #         印象(string): 要设置的印象描述
    #     """
    #     群ID = self.获取上下文群ID(event)
    #
    #     if 用户ID is None:
    #         return "❌ 必须传入用户ID"
    #
    #     用户ID = str(int(用户ID) if isinstance(用户ID, float) and 用户ID.is_integer() else 用户ID)
    #
    #     当前状态 = self.好感度系统.获取用户状态(群ID, 用户ID)
    #     修改记录 = []
    #
    #     # 处理好感值
    #     if 好感值 is not None:
    #         try:
    #             好感值 = int(好感值)
    #         except ValueError:
    #             return "❌ 好感值需要整数"
    #         if not (-100 <= 好感值 <= 100):
    #             return "❌ 错误：好感度范围必须在 -100 到 100 之间。"
    #         旧好感 = 当前状态.get('favour', 0)
    #         当前状态['favour'] = 好感值
    #         修改记录.append(f"好感度从 {旧好感} 改为 {好感值}")
    #
    #     if 关系 is not None:
    #         旧关系 = 当前状态.get('relationship', '陌生人')
    #         当前状态['relationship'] = 关系.strip()
    #         修改记录.append(f"关系从 '{旧关系}' 改为 '{关系}'")
    #
    #     if 印象 is not None:
    #         旧印象 = 当前状态.get('attitude', '中立')
    #         当前状态['attitude'] = 印象.strip()
    #         修改记录.append(f"印象从 '{旧印象}' 改为 '{印象}'")
    #
    #     if not 修改记录:
    #         return "⚠️ 未提供任何要修改的参数。请至少指定好感值、关系或印象中的一个。"
    #
    #     self.好感度系统.保存用户状态(群ID, 用户ID, 当前状态)
    #     return f"✅ 用户 {用户ID} 已更新：\n" + "\n".join(修改记录)

    # @filter.llm_tool(name="get_user_favour")
    # async def 工具_查询用户状态(self, event: AstrMessageEvent, 用户ID: int = None) -> str:
    #     """查询指定用户的好感度、关系、印象等信息"""
    #     群ID = self.获取上下文群ID(event)
    #
    #     目标ID = str(用户ID or event.get_sender_id())
    #     群组数据 = self.好感度系统.获取群组数据(群ID)
    #
    #     if 目标ID not in 群组数据:
    #         return "⚠️ 该用户尚无好感数据（未互动过）"
    #
    #     状态 = self.好感度系统.获取用户状态(群ID, 目标ID)
    #     名字 = 状态.get('name', 目标ID) if 状态.get('name') else 目标ID
    #
    #     回复文本 = (
    #         f"📊 用户 {名字} 的好感状态：\n"
    #         f"💖 好感度：{状态['favour']}\n"
    #         f"🤝 关系：{状态['relationship']}\n"
    #         f"💬 印象：{状态['attitude']}"
    #     )
    #     return 回复文本

    @filter.llm_tool(name="favour_rank")
    async def 工具_好感排行(self, event: AstrMessageEvent, 排序方式: int = 1, 数量: int = 10) -> str:
        """查询当前上下文中用户的好感度排行（管理员可用）

        Args:
            排序方式(number): 排行顺序，1为正序（好感度从大到小），0为倒序，默认1（正序）
            数量(number): 要显示的用户数量，默认为10
        """
        群ID = self.获取上下文群ID(event)

        if not event.is_admin():
            return "❌ 错误：好感度排行查询仅限管理员使用。"

        群组数据 = self.好感度系统.获取群组数据(群ID)
        if not 群组数据:
            return "📭 当前群没有任何用户数据。"

        # 根据排序方式决定升序还是降序
        if 排序方式 == 0:
            # 好感度从低到高
            排序后 = sorted(
                群组数据.items(),
                key=lambda item: item[1].get('favour', 0)
            )
            标题 = f"📉 当前群好感度 BOTTOM {数量} 排行榜（倒序）📉"
        else:
            # 默认正序：好感度从高到低
            排序后 = sorted(
                群组数据.items(),
                key=lambda item: item[1].get('favour', 0),
                reverse=True
            )
            标题 = f"✨ 当前群好感度 TOP {数量} 排行榜（正序）✨"

        排行列表 = [标题]
        for i, (用户ID, 状态) in enumerate(排序后[:数量]):
            名字 = 状态.get('name', 用户ID)
            行 = (
                f"{i + 1}. {名字} (ID: {用户ID})\n"
                f"   💖 好感: {状态['favour']}  |  🤝 关系: {状态['relationship']}  |  ✨ 印象: {状态['attitude']}"
            )
            排行列表.append(行)

        return "\n\n".join(排行列表)