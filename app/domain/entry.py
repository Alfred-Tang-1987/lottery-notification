from dataclasses import dataclass

MAX_COMBINATIONS = 10000

# Content-hash based memo cache for expand() (Entry is frozen → hashable)
_expand_cache: dict[int, list["SingleCombo"]] = {}


@dataclass(frozen=True)
class SingleCombo:
    """展开后的单式组合（一次比对单元）。"""
    front: tuple[int, ...]
    back: tuple[int, ...] | None


@dataclass(frozen=True)
class Entry:
    """用户注单（原始选择）。比对前由 expand() 展开成 SingleCombo。"""
    lottery_code: str
    play_type: str
    front: tuple[int, ...]   # 原始选择（single=fixed count；fushi=多选；dantuo=胆）
    back: tuple[int, ...] | None
    tuo: tuple[int, ...] | None = None  # 胆拖的拖码
    multiplier: int = 1
    append: bool = False

    def __post_init__(self) -> None:
        if not 1 <= self.multiplier <= 99:
            raise ValueError(f"multiplier 须 1-99，当前 {self.multiplier}")

    def cost(self, price_per_bet: int) -> int:
        """真实投入（分）= 单注价 × 展开注数 × 倍投 × (追加?1.5:1)。
        追加仅大乐透：基本 2 + 追加 1 = 1.5 倍单注价。
        MVP single/zhixuan：n_combos=1（准确）。复式/胆拖 n_combos 需 spec 精确（Phase 2）。"""
        if self.append and self.lottery_code != "dlt":
            raise ValueError(
                f"append 仅大乐透(dlt)支持，当前彩种 {self.lottery_code}"
            )
        n_combos = _count_combos(self)
        per = price_per_bet * (3 if self.append else 2) // 2  # append: +50%（2元→3元）
        return n_combos * per * self.multiplier


def _count_combos(e: Entry) -> int:
    """展开后的单式注数（用于 cost/上限校验）。
    MVP：single/zhixuan=1（准确）。fushi/dantuo 需 spec.front.count/back.count 精确组合，
    Phase 2 实现——硬编码 6 会算错大乐透(5)/七乐彩(7)，故 MVP 直接拒绝而非估错。"""
    if e.play_type in ("single", "zhixuan"):
        return 1
    raise NotImplementedError(
        f"{e.play_type} 展开注数需 spec 精确（Phase 2）；MVP 仅 single/zhixuan"
    )


def expand(e: Entry) -> list[SingleCombo]:
    """展开注单为单式组合。MVP：single/zhixuan 返回自身一注（准确）。
    fushi/dantuo 组合展开需 spec（前区/后区 count 因彩种而异），Phase 2 实现。
    带按 entry 内容 hash 的内存缓存，注单变更则失效。"""
    # Check MAX_COMBINATIONS limit (trivially satisfiable for single/zhixuan, but must exist).
    # _count_combos raises NotImplementedError for fushi/dantuo (Phase 2), so only
    # single/zhixuan reach the construction below.
    n_combos = _count_combos(e)
    if n_combos > MAX_COMBINATIONS:
        raise ValueError(
            f"展开注数 {n_combos} 超过上限 {MAX_COMBINATIONS}"
        )

    h = hash(e)
    cached = _expand_cache.get(h)
    if cached is not None:
        return cached

    result = [SingleCombo(front=e.front, back=e.back)]

    _expand_cache[h] = result
    return result
