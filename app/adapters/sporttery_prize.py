"""SportteryPrizeSource——中国体彩网（sporttery.cn）官方奖金查询。

覆盖彩种：dlt（大乐透）、qxc（七星彩）。
数据源：sporttery.cn JSON API 主 + PDF 降级（pypdf 正则解析）。
"""
import io
import json
import logging
import re
from datetime import datetime

import httpx
import pypdf

from app.adapters.base import PermanentLookupError, rebuild_full_issue, rebuild_short_period

logger = logging.getLogger(__name__)

# 项目彩种 code → sporttery gameNo 映射
_GAME_NO = {
    'dlt': '85',
    'qxc': '14',
}

# 项目彩种 code → sporttery PDF 路径 code 映射
# ⚠️ qxc 待确认（实现时通过 sporttery 页面抓取确认）
_PDF_CODE = {
    'dlt': 'dlt',
    'qxc': 'qxc',
}

# PDF 大小限制（1A 决策：防异常大 PDF 导致内存膨胀）
_PDF_MAX_BYTES = 5 * 1024 * 1024  # 5MB


class SportteryPrizeSource:
    """中国体彩网浮动奖金查询。JSON 主 → PDF 降级。

    各适配器自建 httpx.Client（D1 决策，与 MxnzpAdapter/JuheAdapter 模式一致）。
    """

    name = 'sporttery'

    def __init__(self, transport: httpx.BaseTransport | None = None):
        self._client = httpx.Client(transport=transport, timeout=10.0)

    def close(self) -> None:
        """释放 httpx.Client 资源（lifespan teardown 调用）。"""
        self._client.close()

    def lookup_amount(
        self, lottery_code: str, draw_no: str, draw_date: datetime, tier: int
    ) -> int | None:
        """统一入口：JSON 主 → PDF 降级。

        三态语义（与 PrizeSource Protocol 契约一致）：
          - 返回 int  → 已公布金额（分）
          - 返回 None → 官方尚未公布（JSON 空/无匹配，下轮重试）
          - raise httpx.HTTPError → 网络故障（worker 通用 except 隔离重试）

        异常分类（2A 决策）：
        - JSON 解析异常/字段缺失 → 降级 PDF（数据格式问题，PDF 可能仍可达）
        - httpx 异常（网络故障）→ 上抛（PDF 站点大概率也不可达）
        - JSON state != 0（上游 transient 报错/限流）→ raise httpx.HTTPError 上抛
          （review round 3，与 cwl_prize.py lines 60-68 同模式但用 option (a)：transient
          可被下轮重试清除，区别于「正常未公布」的 return None 路径）
        - JSON 空/无匹配（未公布）→ 直接 None，不降级 PDF（OV#3：未公布 ≠ 故障）
        """
        try:
            # None 表示「未公布」，直接透传不降级 PDF（OV#3）。
            return self._lookup_json(lottery_code, draw_no, draw_date, tier)
        except (json.JSONDecodeError, KeyError, TypeError, IndexError) as exc:
            # 仅当 JSON 解析异常或字段缺失（数据格式问题）时降级 PDF。
            # httpx 异常（HTTPStatusError/TimeoutException/...）不在此 tuple 内——
            # 它们会上抛到 worker 由通用 except 隔离重试（2A：网络故障 ≠ 数据格式问题）。
            logger.info(
                'sporttery_json_fallback lottery=%s draw_no=%s error=%s',
                lottery_code, draw_no, type(exc).__name__,
            )
            return self._lookup_pdf(lottery_code, draw_no, draw_date, tier)

    def _lookup_json(
        self, lottery_code: str, draw_no: str, draw_date: datetime, tier: int
    ) -> int | None:
        """JSON API 查询。返回 None = 未公布（不触发 PDF 降级）。"""
        game_no = _GAME_NO[lottery_code]
        full_issue = rebuild_full_issue(draw_date, draw_no)
        logger.info(
            'sporttery_json_lookup lottery=%s draw_no=%s full_issue=%s tier=%s',
            lottery_code, draw_no, full_issue, tier,
        )
        r = self._client.get(
            'https://webapi.sporttery.cn/gateway/lottery/getHistoryPageListV1.qry',
            params={
                'gameNo': game_no,
                'provinceId': '0',
                'pageSize': '1',
                'isVerify': '1',
                'pageNo': '1',
                'term': full_issue,
            },
            headers={
                'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36',
            },
        )
        r.raise_for_status()
        body = r.json()

        # Review round 3 hardening（镜像 cwl_prize.py lines 60-68，sporttery 补齐一致性）：
        # state != 0 通常表示上游 transient 故障（接口限流/临时故障），与「该期无数据」的
        # permanent 语义不同。旧实现不检查 state 直接读 data → total=0 → return None，被 worker
        # 当「未公布」下轮重试，日志只有 not_published total=0 一条，无法区分「上游报错」与
        # 「正常未公布」，运维诊断「为何反复查不到」时定位困难（与 cwl_prize 同一 silent trap）。
        # Option (a)（reviewer preferred）：raise httpx.HTTPError 让 worker transient-except 分支
        # 下轮重试（transient 错误可被重试清除），区别于 return None 强制走「未公布」路径。
        # 同步记 WARNING 含 state + message 字段便于运维区分两类故障。
        if body.get('state') != 0:
            logger.warning(
                'sporttery_json_state_error state=%s message=%r',
                body.get('state'), body.get('message'),
            )
            raise httpx.HTTPError(
                f'sporttery state!=0: state={body.get("state")!r} '
                f'message={body.get("message")!r}'
            )

        # Review round 4 Finding C：上游显式返回 {"state":0,"data":null} 时
        # body.get('data', {}) 得到 None（dict.get 默认值仅在 key 缺失时生效，对显式 null
        # 无效），下一行 data.get('total',0) 抛 AttributeError。AttributeError 不在
        # lookup_amount 外层 catch tuple（json.JSONDecodeError/KeyError/TypeError/IndexError）
        # 内 → 不降级 PDF，冒泡到 refill_service.py 通用 except → 被当 transient 隔离重试，
        # 每轮同样抛同样错，耗满 7 天窗口才由 _mark_expired_unresolved 兜底标记（与本文件
        # line 117-125 加固 state!=0 想避免的同一 silent trap：transient/格式错被误分类）。
        # 语义判断（reviewer option (a)）：data:null 更接近「未公布」（上游数据为空）而非
        # 「格式损坏应降级 PDF」——用 `or {}` 将 null/缺失统一为空 dict，自然落入 total=0
        # → return None 的「未公布」分支。cwl_prize.py line 70 `body.get('result', [])` +
        # `if not result` 天然对 null 安全（not None == True），sporttery 因多一层 .get
        # 链式调用暴露此洞。
        data = body.get('data') or {}
        total = data.get('total', 0)
        if total == 0:
            logger.info('sporttery_json_not_published total=0')
            return None

        items = data.get('list', [])
        for item in items:
            if item.get('lotteryUnuseDrawNum') != draw_no:
                continue
            # ⚠️ 字段名未被实际 API 响应验证（EdgeOne 拦截）——
            # 若实现时验证发现字段名不匹配需调整此处解析逻辑（plan T3 已知风险）。
            # KeyError（prizeLevelList 缺失）→ 外层 catch 降级 PDF。
            return self._extract_tier_amount(item, tier)

        logger.info('sporttery_json_draw_no_match draw_no=%s', draw_no)
        return None

    def _extract_tier_amount(self, item: dict, tier: int) -> int | None:
        """从匹配的 item 中提取指定奖级的金额（分）。

        三态：int=已公布 / None=未公布（缺 stakeAmount 或奖级不匹配，下轮重试）/
        raise PermanentLookupError=永久 schema bug（stakeAmount 非数字）。
        KeyError（prizeLevelList 缺失）冒泡到外层 catch 降级 PDF。
        """
        prize_list = item['prizeLevelList']
        for prize in prize_list:
            # prizeLevel 在真实 API 可能是 int 或 str，统一 str 比较
            # （与 CwlPrizeSource grade.type 处理同模式，防 str-vs-int 永不命中）。
            if str(prize.get('prizeLevel')) != str(tier):
                continue

            # Finding 3（review round 1）：stakeAmount 缺失/空 → return None（未公布，
            # 下轮重试），不默认 '0' 持久化为 0 分。stakeAmount 字段语义同 cwl 的
            # typemoney='_'：缺失=官方尚未派奖。默认 '0' 会被 worker（refill_service
            # `if amount is not None`）当 successful refill 持久化为 0 分、停止重试，
            # 真实金额永远无法恢复（silent-wrong-data）。
            # 显式 '0'（合法：追加投注未中等）与缺失/空（未公布）语义不同——
            # 这里区分 None/'' 与 '0'：仅 None 与空字符串视为未公布。
            amount_str = prize.get('stakeAmount')
            if amount_str is None or amount_str == '':
                logger.info(
                    'sporttery_json_not_published_missing_amount tier=%s', tier,
                )
                return None
            # Finding 1（review round 1）：stakeAmount 非数字（含 null 真实类型错误）
            # 属永久 schema bug，镜像 cwl_prize.py lines 94-103：try/except + raise
            # PermanentLookupError from None。旧实现 int() 直接抛 ValueError 未捕获，
            # 被 worker 通用 except 当 transient 隔离重试 7 天 → _mark_expired_unresolved
            # 兜底标记（永久 schema bug 静默耗满 7 天窗口）。
            try:
                amount = int(amount_str) * 100  # 元 → 分
            except (ValueError, TypeError):
                logger.warning(
                    'sporttery_json_amount_unparseable tier=%s raw=%r',
                    tier, amount_str,
                )
                raise PermanentLookupError(
                    f'stakeAmount unparseable: tier={tier} raw={amount_str!r}'
                ) from None
            logger.info('sporttery_json_found tier=%s amount=%s', tier, amount)
            return amount

        logger.info('sporttery_json_tier_no_match tier=%s', tier)
        return None

    def _lookup_pdf(
        self, lottery_code: str, draw_no: str, draw_date: datetime, tier: int
    ) -> int | None:
        """PDF 降级：下载官方公告 PDF，pypdf 提取文本，正则匹配奖金。"""
        pdf_code = _PDF_CODE.get(lottery_code)
        if pdf_code is None:
            # Review round 4 Finding B：_PDF_CODE 缺失映射是编程/配置错误（非「未公布」
            # 上游状态）。旧实现 return None 被 worker 当「未公布」重试 7 天再由
            # _mark_expired_unresolved 兜底标记，真实配置 bug 被静默掩盖。改为 raise
            # PermanentLookupError 让 worker 立即标 unresolved 且 fail loud and fast。
            logger.warning('sporttery_pdf_no_code lottery=%s', lottery_code)
            raise PermanentLookupError(
                f'no PDF code mapping for lottery={lottery_code}'
            ) from None

        period = rebuild_short_period(draw_date, draw_no)
        url = f'https://pdf.sporttery.cn/{pdf_code}/{period}/{period}.pdf'
        logger.info(
            'sporttery_pdf_lookup lottery=%s period=%s url=%s',
            lottery_code, period, url,
        )
        r = self._client.get(url)
        if r.status_code == 404:
            logger.info('sporttery_pdf_404 period=%s', period)
            return None
        r.raise_for_status()

        if len(r.content) > _PDF_MAX_BYTES:
            # Review round 4 Finding A：PDF 超 5MB 是 stable-permanent 条件——同一 URL
            # 每轮返回同一超限 PDF，不可能自愈。旧实现 return None 被 worker
            # （refill_service.py line 111 `if amount is not None`）当「未公布」，
            # 每轮重复下载同一 5MB+ blob 共 7 天（带宽 + 日志噪音），最终由
            # _mark_expired_unresolved 兜底标记 unresolved（与 Finding 2/4 同一 silent trap：
            # 「permanent 条件被误分类为 transient-retry」）。改为 raise
            # PermanentLookupError 让 worker（refill_service.py lines 91-105）立即标
            # unresolved，不耗 7 天重试窗口。
            logger.warning(
                'pdf_too_large size=%s limit=%s period=%s',
                len(r.content), _PDF_MAX_BYTES, period,
            )
            raise PermanentLookupError(
                f'pdf too large: size={len(r.content)} limit={_PDF_MAX_BYTES} period={period}'
            ) from None

        try:
            reader = pypdf.PdfReader(io.BytesIO(r.content))
            text = '\n'.join(page.extract_text() or '' for page in reader.pages)
        except Exception as exc:
            # Finding 2（review round 1）：pypdf 解析失败（损坏/不可读 PDF）每轮都会同样失败，
            # 是永久形状错误，非「未公布」。return None 会让 worker 每轮重新下载+重解析同一坏 PDF
            # 直到 7 天超期，最后由 _mark_expired_unresolved 兜底标记（永久 schema bug 静默耗满
            # 7 天窗口，正是 refill_service.py lines 91-97 警告的反模式）。
            # 404（真正未公布）已在上方 line 175-177 单独返回 None，此处 except 只覆盖「PDF 存在
            # 但内容损坏」的永久错误。
            logger.warning(
                'sporttery_pdf_parse_failed period=%s',
                period, exc_info=True,
            )
            raise PermanentLookupError(
                f'pdf parse failed: period={period}'
            ) from exc

        amount = self._parse_pdf_amount(text, tier)
        if amount is not None:
            logger.info('sporttery_pdf_found tier=%s amount=%s', tier, amount)
        else:
            logger.info('sporttery_pdf_no_match tier=%s', tier)
        return amount

    @staticmethod
    def _parse_pdf_amount(text: str, tier: int) -> int | None:
        """从 PDF 文本中提取指定奖级的奖金（分）。

        ⚠️ 正则基于预期格式，实现时需用真实 PDF 验证并可能调整（plan T3/T7 已知风险）。
        金额含千分位逗号（如 '5,000,000'），需去逗号后转 int。

        三态契约（与 CwlPrizeSource / sporttery _lookup_json 一致）：
          - 文本为空或奖级越界 → return None（真正未公布/越界，下轮重试）
          - 文本非空但正则不命中 → raise PermanentLookupError（Finding 4：格式 drift，
            永久错误，worker 立即标 unresolved）
          - 正则命中 → return int 分
        """
        # 匹配模式：奖级 N 后面的金额数字（含千分位）
        # 预期格式示例："一等奖  5注  5,000,000元"
        chinese_numerals = '一二三四五六七八九'
        if tier < 1 or tier > len(chinese_numerals):
            # 奖级越界（如 tier=10）→ 真正「无该奖级」，return None。
            return None
        # 文本为空（PDF 提取不出任何字符）→ 真正未公布，return None（下轮重试）。
        if not text.strip():
            return None
        pattern = rf'{chinese_numerals[tier - 1]}等奖\s+[\d,]+\s*注\s+([\d,]+)\s*元'
        match = re.search(pattern, text)
        if match:
            amount_str = match.group(1).replace(',', '')
            return int(amount_str) * 100  # 元 → 分
        # Finding 4（review round 1）：文本非空但正则不命中 = 官方 PDF 格式 drift（如改用
        # 「一等奖：500万元」）。格式 drift 每轮都失败，是永久格式错误，非「未公布」。
        # return None 会让 worker 每轮重新下载同一格式 drift 的 PDF 直到 7 天超期。
        logger.warning(
            'sporttery_pdf_format_drift tier=%s text_preview=%r',
            tier, text[:200],
        )
        raise PermanentLookupError(
            f'pdf format drift: tier={tier} regex_no_match text_len={len(text)}'
        )
