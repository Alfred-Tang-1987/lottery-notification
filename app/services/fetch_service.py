"""FetchService：双源抓取 + 交叉校验 + 部分源 grace + 退避 + 幂等存储（spec §7.2/§10）。

源三态区分（spec §7.2「空结果语义」）：
  - 返回 DrawNumbers = 有号码
  - 返回 None        = 该期未开奖（HTTP 200 无数据），非错误
  - 抛异常           = 源故障（网络/服务），上抛或退避重试

决策：
  双源都有 → 交叉校验号码一致 → verified=true 入库；不一致 → verified=false 拒入库+告警
  恰一源有 → grace window 重抓缺失源；仍单源 → single_source=true 入库
  双源都无 → not_drawn，不存
  双源都故障 → 告警不存（spec §10）
"""

import json
import logging
import random
import time
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from app.adapters.base import DrawNumbers, DrawSource
from app.models import DrawResult, PendingComparison
from app.seeds import SPECS

logger = logging.getLogger(__name__)

_CST = ZoneInfo('Asia/Shanghai')  # spec：全程 Asia/Shanghai


@dataclass
class FetchResult:
    """单次 fetch_and_store 的结果（供调度/告警/健康面板消费）。"""

    stored: bool
    verified: bool = False
    single_source: bool = False
    not_drawn: bool = False
    draw_result_id: int | None = None
    error: str | None = None


class FetchService:
    """双源抓取编排器。构造注入 primary/backup DrawSource + engine。

    `sleep` 可注入（默认 time.sleep）——测试传 no-op 避免 grace/backoff 真睡眠。
    """

    def __init__(
        self,
        primary: DrawSource,
        backup: DrawSource,
        engine: Engine,
        grace_seconds: int = 300,
        max_attempts: int = 6,
        backoff_base: float = 2.0,
        sleep=time.sleep,
    ):
        if max_attempts < 1:
            # max_attempts<=0 使 range() 为空 → _fetch_with_backoff 隐式返回 None，
            # 被 _try_fetch 归类为"未开奖"，把"配置错误"误报成"该期未开奖"（错误方向）。
            # spec §10 准确性优先：配置错误须显式暴露，不得静默伪装成 not_drawn。
            raise ValueError('max_attempts must be >= 1')
        self._primary = primary
        self._backup = backup
        self._engine = engine
        self._grace = grace_seconds
        self._max_attempts = max_attempts
        self._backoff = backoff_base
        self._sleep = sleep

    # ------------------------------------------------------------------ fetch
    def _fetch_with_backoff(self, source: DrawSource, lottery_code: str) -> DrawNumbers | None:
        """指数退避 + 抖动重试，max_attempts 次。

        None = 未开奖（源正常返回无数据）；异常 = 源故障（重试耗尽后上抛）。
        退避/抖动防限流封禁（spec §7.2：避免固定节奏一夜锤 ~100 次触发封禁）。
        """
        for attempt in range(self._max_attempts):
            try:
                return source.fetch(lottery_code)
            except Exception as exc:
                # 源故障不得静默吞没：结构化日志供告警/排障（silent-failure-hunter）。
                # 重试耗尽后仍上抛，由 _try_fetch 归类为 ok=False（源故障态）。
                logger.warning(
                    'source_fetch_failed source=%s lottery=%s attempt=%d/%d error=%s',
                    getattr(source, 'name', 'unknown'),
                    lottery_code,
                    attempt + 1,
                    self._max_attempts,
                    exc,
                )
                if attempt == self._max_attempts - 1:
                    raise
                self._sleep(self._backoff**attempt + random.random())

    def _try_fetch(self, source: DrawSource, lottery_code: str) -> tuple[DrawNumbers | None, bool]:
        """返回 (numbers, ok)。ok=False=源故障（异常被吞）；ok=True+None=未开奖。"""
        try:
            return self._fetch_with_backoff(source, lottery_code), True
        except Exception:
            return None, False

    # ------------------------------------------------------------- main entry
    def fetch_and_store(self, lottery_code: str) -> FetchResult:
        primary, p_ok = self._try_fetch(self._primary, lottery_code)
        backup, b_ok = self._try_fetch(self._backup, lottery_code)

        # 双源都故障 → 告警不存（spec §10）
        if not p_ok and not b_ok:
            return FetchResult(stored=False, error='all_sources_failed')

        # 仅取有效结果（故障源的 None 不算"未开奖"）
        p = primary if p_ok else None
        b = backup if b_ok else None

        # 双源都"未开奖"
        if p is None and b is None:
            return FetchResult(stored=False, not_drawn=True)

        # 双源都有 → 交叉校验
        if p is not None and b is not None:
            if _numbers_match(p, b):
                return self._store(p, verified=True, single_source=False, source_name=self._primary.name)
            return self._mismatch(lottery_code, p, b)

        # 恰一源有效：grace 后重抓缺失源（spec §7.2 部分源 grace window）。
        # 重抓三态分流：拿到数据→双源校验（不一致即拒绝，不得降级单源——否则双源
        # 安全网在 grace 路径被绕过，§10 准确性优先）；仍无/故障→单源兜底。
        #
        # grace 触发条件（2026-07-21 冒烟修正）：仅当缺失源是「未开奖」（ok=True 且
        # None，数据延迟）时才 grace 等待。缺失源若是「故障」（ok=False，HTTP 异常/
        # 超时/鉴权失败），sleep 5 分钟注定再次失败——只白白阻塞启动/cron 数分钟
        # （NAS 场景 healthcheck 超时 → restart 循环）。故障直接走单源兜底。
        present_dn = p if p is not None else b  # 恰一源有效，必非 None
        missing_ok = p_ok if p is None else b_ok  # 缺失源是否「未开奖」而非「故障」
        assert present_dn is not None  # 类型窄化（上面分支已排双 None/双有）
        if self._grace > 0 and missing_ok:
            self._sleep(self._grace)
            # 归属：以实际提供数据的源为准（与单源兜底一致），而非恒记主源——
            # 否则主源故障靠备源恢复入库的行会错标 source=主源，丢失 ops 追溯来源。
            present_source_name = self._primary.name if p is not None else self._backup.name
            verdict = self._grace_refetch(
                lottery_code,
                present_dn=present_dn,
                missing_source=self._primary if p is None else self._backup,
                present_source_name=present_source_name,
            )
            if verdict is not None:
                return verdict  # 双源校验成功入库 / mismatch 拒绝

        # grace 后仍单源（重抓仍无/故障，或缺失源本身故障跳过 grace）→ single_source 存
        only = p if p is not None else b
        src_name = self._primary.name if p is not None else self._backup.name
        return self._store(only, verified=True, single_source=True, source_name=src_name)

    def _mismatch(self, lottery_code: str, a: DrawNumbers, b: DrawNumbers) -> FetchResult:
        """双源交叉校验不一致 → verified=false 拒入库 + 告警日志（spec §7.2）。

        不一致是最严重信号（双源对同期号码有分歧 → 潜在脏数据），必须留痕——否则运维
        查"为何 062 期没入库"无迹可寻（与源故障同属不得静默吞没，silent-failure-hunter）。
        """
        logger.warning(
            'cross_verify_mismatch lottery=%s draw_no=%s primary=%s/%s backup=%s/%s',
            lottery_code,
            a.draw_no,
            a.front,
            a.back,
            b.front,
            b.back,
        )
        return FetchResult(stored=False, verified=False, error='cross_verify_mismatch')

    def _grace_refetch(
        self,
        lottery_code: str,
        present_dn: DrawNumbers,
        missing_source: DrawSource,
        present_source_name: str,
    ) -> FetchResult | None:
        """grace 内重抓缺失源并双源校验。返回 FetchResult=已决（入库/拒绝）；None=重抓仍无/故障。"""
        m2, m2_ok = self._try_fetch(missing_source, lottery_code)
        if not m2_ok or m2 is None:
            return None  # 仍无/故障 → 回主流程单源兜底
        if _numbers_match(m2, present_dn):
            return self._store(present_dn, verified=True, single_source=False, source_name=present_source_name)
        return self._mismatch(lottery_code, m2, present_dn)

    # ------------------------------------------------------------------ store
    def _store(self, dn: DrawNumbers, *, verified: bool, single_source: bool, source_name: str) -> FetchResult:
        """幂等入库：唯一约束 (lottery_code, draw_no) 兜底。

        - 新行：与 pending_comparisons outbox（verified 时，spec §7.1 line271）同事务一次
          commit——绝不可分两次 commit，否则首 commit 落库 verified=true、次 commit 失败时
          outbox 丢失，重试走幂等分支不补 outbox → CompareService 永不比对 → 中奖静默漏通知。
        - 既有行：单源→双源一致时升级 single_source=False（spec §7.2 金标准是双源 verified，
          避免 UI 永久挂黄）。**升级前必须校验号码一致**——既有旧号码 ≠ incoming 双源号码
          属官方更正语义（T6 DrawCorrectService 专管），T3 不 bless 旧号码为双源 verified，
          保守保持原状（仅升级 flag 当且仅当号码一致），把更正留给 T6，避免错误 bless。
          号码一致时不重复写 outbox（比对已跑过）。
        - 并发：唯一约束并发插入触发 IntegrityError → 重读既有行复用升级分支（不抛错）。
        """
        incoming_json = json.dumps(
            {
                'front': list(dn.front),
                'back': list(dn.back) if dn.back else None,
            }
        )
        with Session(self._engine) as s:
            existing = s.exec(
                select(DrawResult).where(
                    DrawResult.lottery_code == dn.lottery_code,
                    DrawResult.draw_no == dn.draw_no,
                )
            ).first()
            if existing:
                return self._upgrade_existing(
                    s, existing, incoming_json, verified=verified, single_source=single_source, source_name=source_name
                )
            try:
                dr = DrawResult(
                    lottery_code=dn.lottery_code,
                    draw_no=dn.draw_no,
                    draw_date=datetime.combine(dn.draw_date, datetime.min.time(), tzinfo=_CST),
                    numbers_json=incoming_json,
                    source=source_name,
                    verified=verified,
                    single_source=single_source,
                    version=1,
                )
                s.add(dr)
                s.flush()  # 拿到 dr.id（PK），PendingComparison FK 可填，仍在同一事务内
                assert dr.id is not None  # flush 后 PK 必已赋值（类型窄化）
                # spec §7.1 line271：verified=true 入库与 outbox 同事务一次 commit
                # ——单事务保证 DrawResult 与 PendingComparison 原子落库（silent-failure 修复）。
                if verified:
                    s.add(PendingComparison(draw_result_id=dr.id))
                s.commit()
                s.refresh(dr)
            except IntegrityError:
                # 并发：另一进程已插入同 (lottery_code, draw_no) → 回滚并重读复用升级分支
                s.rollback()
                existing = s.exec(
                    select(DrawResult).where(
                        DrawResult.lottery_code == dn.lottery_code,
                        DrawResult.draw_no == dn.draw_no,
                    )
                ).first()
                if existing is None:  # 理论不可达
                    raise
                return self._upgrade_existing(
                    s, existing, incoming_json, verified=verified, single_source=single_source, source_name=source_name
                )
            return FetchResult(stored=True, verified=verified, single_source=single_source, draw_result_id=dr.id)

    def _upgrade_existing(
        self,
        session: Session,
        existing: DrawResult,
        incoming_json: str,
        *,
        verified: bool,
        single_source: bool,
        source_name: str,
    ) -> FetchResult:
        """既有行升级：仅当 incoming 号码与既有号码一致时升级 flag，不 bless 旧号码。"""
        upgraded = False
        if existing.numbers_json == incoming_json:
            # 号码一致：升级 single_source/verified flag（双源金标准，避免 UI 永久挂黄）
            if existing.single_source and not single_source:
                existing.single_source = False
                upgraded = True
            if not existing.verified and verified:
                existing.verified = True
                upgraded = True
            if upgraded:
                existing.source = source_name
        # 号码不一致 → 属官方更正（T6），T3 不动号码/不 bless，保守返回现有状态
        if upgraded:
            session.commit()
            session.refresh(existing)
        return FetchResult(
            stored=True,
            verified=existing.verified,
            single_source=existing.single_source,
            draw_result_id=existing.id,
        )


def _number_style(code: str) -> str:
    """从 SPECS 取彩种 number_style，未知彩种默认 positional（严格：按位比）。

    spec §10 准确性优先：未知彩种若默认 partition（sorted/multiset，更宽松），会放过
    顺序不同的双源号码 → verified=true 入错号。默认 positional（tuple ==，更严格）更安全——
    顺序不同即拒，宁可误拒也不放过不一致。彩种应在 seeds 注册后才进 adapters，此处兜底。
    """
    return next((s['number_style'] for s in SPECS if s['code'] == code), 'positional')


def _numbers_match(a: DrawNumbers, b: DrawNumbers) -> bool:
    """双源号码一致性校验。按彩种 number_style 分流（spec §5.1 line154 + §5.4 line205
    + §7.2 line290 / docs/reference/lottery-rules.md）。

    - positional / hybrid（福彩3D/排列3/排列5/七星彩）：前区用 PositionalDigits
      （有序、每位独立、**允许跨位重复**）→ 必须逐位 tuple ==，绝不可排序。否则
      (1,1,2,3,4,5) 与 (5,4,3,2,1,1) 会被判同（sorted 多重集相等），但按位对应规则下
      这是不同的开奖结果，交叉校验安全网失效（spec §7.2：号码一致才有意义；§10：准确性优先）。
      七星彩后区是单值(0-14)，tuple == 即标量相等，顺序无意义；positional 彩种无后区。
      回归保护：旧版统一 sorted 对 positional 错、把 hybrid 前区塞进 sorted 分支也错。
    - partition（双色球/大乐透/七乐彩）：前/后区是 NumberRange（集合、去重、无序）→ 排序后比，
      前/后区独立。
    """
    style = _number_style(a.lottery_code)
    if style in ('positional', 'hybrid'):
        if a.front != b.front:  # 按位、有序、允许跨位重复 → tuple ==，不可 sorted
            return False
        if (a.back is None) != (b.back is None):
            return False
        return a.back == b.back  # hybrid 后区单值 / positional 无后区，tuple == 即可
    # partition：集合、去重、无序 → 排序比，前/后区独立
    if sorted(a.front) != sorted(b.front):
        return False
    if (a.back is None) != (b.back is None):
        return False
    a_back, b_back = a.back, b.back
    return not (a_back is not None and b_back is not None and sorted(a_back) != sorted(b_back))
