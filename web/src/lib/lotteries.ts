/**
 * 彩种规则纯函数（lottery-rules.md 权威）。
 *
 * 7 大彩种的号码区间、玩法词汇、机选、校验、CSV 解析。
 * 所有函数纯、无 IO、无 Vue 依赖，可被组件和测试消费。
 */

// ──────────────────────────────────────────────
// 1. 彩种定义
// ──────────────────────────────────────────────

export interface LotteryOption {
  code: string;
  name: string;
}

export const LOTTERIES: LotteryOption[] = [
  { code: "ssq", name: "双色球" },
  { code: "dlt", name: "大乐透" },
  { code: "qlc", name: "七乐彩" },
  { code: "qxc", name: "七星彩" },
  { code: "fc3d", name: "福彩3D" },
  { code: "pl3", name: "排列3" },
  { code: "pl5", name: "排列5" },
];

export const ALL_LOTTERY_CODES: string[] = LOTTERIES.map((l) => l.code);

export function lotteryName(code: string): string {
  return LOTTERIES.find((l) => l.code === code)?.name || code;
}

// ──────────────────────────────────────────────
// 2. 号码区间（lottery-rules.md 权威）
// ──────────────────────────────────────────────

export interface ZoneRange {
  min: number;
  max: number;
  count: number;
}

export interface LotteryRange {
  front: ZoneRange;
  back: ZoneRange | null;
}

const RANGES: Record<string, LotteryRange> = {
  ssq: { front: { min: 1, max: 33, count: 6 }, back: { min: 1, max: 16, count: 1 } },
  dlt: { front: { min: 1, max: 35, count: 5 }, back: { min: 1, max: 12, count: 2 } },
  qlc: { front: { min: 1, max: 30, count: 7 }, back: { min: 1, max: 30, count: 1 } },
  qxc: { front: { min: 0, max: 9, count: 6 }, back: { min: 0, max: 14, count: 1 } },
  fc3d: { front: { min: 0, max: 9, count: 3 }, back: null },
  pl3: { front: { min: 0, max: 9, count: 3 }, back: null },
  pl5: { front: { min: 0, max: 9, count: 5 }, back: null },
};

export function getLotteryRange(code: string): LotteryRange | null {
  return RANGES[code] ?? null;
}

export function frontNumbers(code: string): number[] {
  const r = RANGES[code]?.front;
  if (!r) return [];
  return Array.from({ length: r.max - r.min + 1 }, (_, i) => r.min + i);
}

export function backNumbers(code: string): number[] {
  const r = RANGES[code]?.back;
  if (!r) return [];
  return Array.from({ length: r.max - r.min + 1 }, (_, i) => r.min + i);
}

// ──────────────────────────────────────────────
// 2.5 按位型判断（lottery-rules.md 权威）
// ──────────────────────────────────────────────

// 按位型（positional）：fc3d/pl3/pl5/qxc 前区是数字序列，可重复
// 分区型（partition）：ssq/dlt/qlc 是集合，不可重复
const POSITIONAL_CODES = new Set(["fc3d", "pl3", "pl5", "qxc"]);

export function isPositional(code: string): boolean {
  return POSITIONAL_CODES.has(code);
}

// ──────────────────────────────────────────────
// 3. 玩法（lottery-rules.md 权威 + spec §5.2 + 后端 app/seeds/lottery_types.py）
//    分区型（ssq/dlt/qlc/qxc）→ 单式/复式/胆拖
//    按位型（fc3d）→ 单选/组选3/组选6（现行叫"单选"，非"直选"）
//    按位型（pl3）→ 直选/组选3/组选6（沿用"直选"）
//    按位型（pl5）→ 直选
//    ⚠️ play_type 词汇必须与后端一致：partition 复式 = 'fushi'（非 'compound'）
// ──────────────────────────────────────────────

const PLAY_TYPES: Record<string, string[]> = {
  ssq: ["single", "fushi", "dantuo"],
  dlt: ["single", "fushi", "dantuo"],
  qlc: ["single", "fushi", "dantuo"],
  qxc: ["single", "fushi", "dantuo"],
  fc3d: ["danxuan", "zuxuan3", "zuxuan6"],
  pl3: ["zhixuan", "zuxuan3", "zuxuan6"],
  pl5: ["zhixuan"],
};

export function getPlayTypes(code: string): string[] {
  return PLAY_TYPES[code] ?? [];
}

export const PLAY_TYPE_LABELS: Record<string, string> = {
  single: "单式",
  fushi: "复式",
  dantuo: "胆拖",
  danxuan: "单选",
  zhixuan: "直选",
  zuxuan3: "组选3",
  zuxuan6: "组选6",
};

// ──────────────────────────────────────────────
// 4. 机选一注（纯函数，接收 code，返回号码）
// ──────────────────────────────────────────────

export interface RandomPickOptions {
  frontCount?: number;
  backCount?: number;
  playType?: string;
}

export interface RandomPickResult {
  front: number[];
  back?: number[];
  dan?: number[];
  tuo?: number[];
}

/** 从区间 [min,max] 无放回抽取 count 个号码（分区型用），返回升序。 */
function pickUnique(min: number, max: number, count: number): number[] {
  const pool = Array.from({ length: max - min + 1 }, (_, i) => min + i);
  // partial Fisher-Yates: shuffle first `count` slots only
  for (let i = 0; i < count && i < pool.length - 1; i++) {
    const j = i + Math.floor(Math.random() * (pool.length - i));
    [pool[i], pool[j]] = [pool[j], pool[i]];
  }
  return pool.slice(0, count).sort((a, b) => a - b);
}

export function randomPick(code: string, options?: RandomPickOptions): RandomPickResult {
  const r = getLotteryRange(code);
  if (!r) return { front: [] };

  const isDantuo = options?.playType === "dantuo";
  const positional = isPositional(code);

  // 胆拖（仅分区型）：dan ∈ [1, count-1], dan+tuo ≥ count
  if (isDantuo && !positional) {
    const frontPool = Array.from({ length: r.front.max - r.front.min + 1 }, (_, i) => r.front.min + i);
    // partial Fisher-Yates over the full pool to pick dan + tuo
    const danCount = Math.floor(Math.random() * (r.front.count - 1)) + 1;
    for (let i = 0; i < r.front.count && i < frontPool.length - 1; i++) {
      const j = i + Math.floor(Math.random() * (frontPool.length - i));
      [frontPool[i], frontPool[j]] = [frontPool[j], frontPool[i]];
    }
    const dan = frontPool.slice(0, danCount).sort((a, b) => a - b);
    const tuo = frontPool.slice(danCount, r.front.count).sort((a, b) => a - b);

    const result: RandomPickResult = { front: [...dan, ...tuo].sort((a, b) => a - b), dan, tuo };
    if (r.back) {
      result.back = pickUnique(r.back.min, r.back.max, r.back.count);
    }
    return result;
  }

  // 分区型（无重复）
  if (!positional) {
    const frontCount = options?.frontCount ?? r.front.count;
    const backCount = options?.backCount ?? r.back?.count;
    const front = pickUnique(r.front.min, r.front.max, frontCount);
    const result: RandomPickResult = { front };
    if (r.back && backCount !== undefined) {
      result.back = pickUnique(r.back.min, r.back.max, backCount);
    }
    return result;
  }

  // 按位型（positional）：每位独立随机，允许重复
  const front = Array.from({ length: r.front.count }, () =>
    Math.floor(Math.random() * (r.front.max - r.front.min + 1)) + r.front.min,
  ).sort((a, b) => a - b);
  const result: RandomPickResult = { front };
  const back = r.back;
  if (back) {
    result.back = Array.from({ length: back.count }, () =>
      Math.floor(Math.random() * (back.max - back.min + 1)) + back.min,
    );
  }
  return result;
}

// ──────────────────────────────────────────────
// 5. 号码校验
// ──────────────────────────────────────────────

export type ValidateResult = { ok: true } | { ok: false; error: string };

export function validateNumbers(
  code: string,
  front: number[],
  back?: number[],
): ValidateResult {
  const r = getLotteryRange(code);
  if (!r) return { ok: false, error: `未知彩种: ${code}` };

  const positional = isPositional(code);

  // front 检查
  for (const n of front) {
    if (n < r.front.min || n > r.front.max) {
      return { ok: false, error: `前区号码 ${n} 超出范围 [${r.front.min}-${r.front.max}]` };
    }
  }
  // front 数量：单式 = count，复式 ≥ count
  if (front.length < r.front.count) {
    return {
      ok: false,
      error: `前区号码数量不足：需要至少 ${r.front.count} 个，当前 ${front.length} 个`,
    };
  }
  // front 重复（分区型禁止，按位型允许）
  if (!positional) {
    const frontSet = new Set(front);
    if (frontSet.size !== front.length) {
      return { ok: false, error: `前区号码存在重复` };
    }
  }

  // back 检查
  if (r.back === null) {
    if (back && back.length > 0) {
      return { ok: false, error: `该彩种无后区号码` };
    }
    return { ok: true };
  }

  if (!back || back.length === 0) {
    return { ok: false, error: `后区号码缺失：需要至少 ${r.back.count} 个` };
  }
  for (const n of back) {
    if (n < r.back.min || n > r.back.max) {
      return { ok: false, error: `后区号码 ${n} 超出范围 [${r.back.min}-${r.back.max}]` };
    }
  }
  if (back.length < r.back.count) {
    return {
      ok: false,
      error: `后区号码数量不足：需要至少 ${r.back.count} 个，当前 ${back.length} 个`,
    };
  }
  const backSet = new Set(back);
  if (backSet.size !== back.length) {
    return { ok: false, error: `后区号码存在重复` };
  }

  return { ok: true };
}

// ──────────────────────────────────────────────
// 6. CSV 行解析（逐行校验 + 错误反馈，不静默丢行）
// ──────────────────────────────────────────────

export type CsvParseResult =
  | { ok: true; data: { code: string; front: number[]; back?: number[]; draw_no?: string } }
  | { ok: false; error: string; line?: number };

export function parseCsvLine(raw: string, lineNum?: number): CsvParseResult {
  const trimmed = raw.trim().replace(/\r$/, "");
  if (!trimmed) return { ok: false, error: "空行", line: lineNum };

  const parts = trimmed.split(",").map((s) => s.trim());
  if (parts.length < 2) {
    return { ok: false, error: "格式错误：至少需要彩种代码和 1 个号码", line: lineNum };
  }

  const code = parts[0].toLowerCase();
  const r = getLotteryRange(code);
  if (!r) {
    return { ok: false, error: `未知彩种: ${code}`, line: lineNum };
  }

  const totalExpected = r.front.count + (r.back ? r.back.count : 0);

  // plan Step 3 CSV format: 彩种,期号,号码...
  // draw_no (期号) is optional. Heuristic: if there is exactly one extra field
  // before the numbers, treat it as draw_no (period identifier, alphanumeric).
  let draw_no: string | undefined;
  let numStrs = parts.slice(1);
  if (numStrs.length === totalExpected + 1) {
    const candidate = numStrs[0];
    // 期号 is alphanumeric (e.g. "2024090", "2024-090"); a lottery ball number
    // is a small integer. Treat the leading extra field as draw_no when it
    // would overflow ball range OR is not a plausible single ball number.
    const n = Number(candidate);
    const looksLikeBall = candidate !== "" && Number.isInteger(n) && n >= 0 && n <= 99;
    if (!looksLikeBall) {
      draw_no = candidate;
      numStrs = numStrs.slice(1);
    }
  }

  const nums: number[] = [];
  for (const s of numStrs) {
    if (s.startsWith("-")) {
      return { ok: false, error: `负数无效: "${s}"`, line: lineNum };
    }
    const n = Number(s);
    if (!Number.isInteger(n) || s === "") {
      return { ok: false, error: `非数字: "${s}"`, line: lineNum };
    }
    nums.push(n);
  }

  if (nums.length < totalExpected) {
    return {
      ok: false,
      error: `号码数量不足：${code} 需要 ${totalExpected} 个号码，当前 ${nums.length} 个`,
      line: lineNum,
    };
  }
  if (nums.length > totalExpected) {
    return {
      ok: false,
      error: `号码数量过多：${code} 需要 ${totalExpected} 个号码，当前 ${nums.length} 个`,
      line: lineNum,
    };
  }

  const front = nums.slice(0, r.front.count);
  const back = r.back ? nums.slice(r.front.count, r.front.count + r.back.count) : undefined;

  const v = validateNumbers(code, front, back);
  if (!v.ok) {
    return { ok: false, error: v.error, line: lineNum };
  }

  return { ok: true, data: { code, front, back, ...(draw_no !== undefined ? { draw_no } : {}) } };
}

// ──────────────────────────────────────────────
// 7. 投入计算（lottery-rules.md + app/domain/entry.py Entry.cost 对齐）
//    cost(分) = n_combos × price_per_bet × (append?1.5:1) × multiplier
// ──────────────────────────────────────────────

/** 各彩种单注价格（分）。与后端 app/seeds/lottery_types.py price_per_bet 对齐。 */
export const PRICE_PER_BET: Record<string, number> = {
  ssq: 200,
  dlt: 200,
  qlc: 200,
  qxc: 200,
  fc3d: 200,
  pl3: 200,
  pl5: 200,
};

/** 组合数 C(n, k) */
function comb(n: number, k: number): number {
  if (k < 0 || k > n || n < 0) return 0;
  if (k === 0 || k === n) return 1;
  k = Math.min(k, n - k);
  let result = 1;
  for (let i = 0; i < k; i++) {
    result = (result * (n - i)) / (i + 1);
  }
  return Math.round(result);
}

export interface CountCombosArgs {
  code: string;
  playType: string;
  front: number[];
  back?: number[];
  tuo?: number[];
}

/** 计算展开后的单式注数 n_combos。
 * 与后端 app/domain/entry.py _count_combos 对齐（后端 NotImplementedError 对 fushi/dantuo，
 * 前端补齐实现——后端比对阶段不依赖 cost，前端计算 cost 用于展示与提交）。
 *
 * - single/zhixuan/danxuan: 1
 * - fushi (分区型 ssq/dlt/qlc): C(front.len, front.count) × C(back.len, back.count)
 * - dantuo: 胆码 front 固定，从拖码 tuo 中选 (front.count - dan.len) 个 → C(tuo.len, need)
 *           × (back ? C(back.len, back.count) : 1)
 * - qxc hybrid: 后区是单值，C(back.len, back.count) = C(n,1) = n（按位型前区不组合）
 */
export function countCombos(args: CountCombosArgs): number {
  const { code, playType, front, back, tuo } = args;
  const r = getLotteryRange(code);
  if (!r) return 0;

  // 单式 / 直选 / 单选：1 注
  if (playType === "single" || playType === "zhixuan" || playType === "danxuan") {
    return 1;
  }

  // 复式（partition 型 ssq/dlt/qlc，前区 ≥ count + 后区 ≥ count）
  if (playType === "fushi") {
    const frontCombos = comb(front.length, r.front.count);
    const backCombos = r.back ? comb(back?.length ?? 0, r.back.count) : 1;
    return frontCombos * backCombos;
  }

  // 胆拖（partition 型）：胆码 front.length 个，拖码 tuo 中需选 (count - dan.len) 个
  if (playType === "dantuo") {
    const danCount = front.length;
    const needFromTuo = r.front.count - danCount;
    if (needFromTuo < 0) return 0;
    const tuoCount = tuo?.length ?? 0;
    const frontCombos = comb(tuoCount, needFromTuo);
    const backCombos = r.back ? comb(back?.length ?? 0, r.back.count) : 1;
    return frontCombos * backCombos;
  }

  return 0;
}

export interface CalculateCostArgs {
  code: string;
  playType: string;
  front: number[];
  back?: number[];
  tuo?: number[];
  multiplier: number;
  append?: boolean;
}

/** 计算投入金额（分）。
 * 公式：n_combos × price_per_bet × (append?1.5:1) × multiplier
 * 与后端 app/domain/entry.py Entry.cost 对齐——前端计算展示+提交，后端存储原值。
 *
 * append 仅大乐透支持（其他彩种传 append=true 抛错，与后端 Entry.cost 一致）。
 */
export function calculateCost(args: CalculateCostArgs): number {
  const { code, playType, front, back, tuo, multiplier, append = false } = args;
  if (append && code !== "dlt") {
    throw new Error(`append 仅大乐透(dlt)支持，当前彩种 ${code}`);
  }
  const pricePerBet = PRICE_PER_BET[code] ?? 0;
  if (pricePerBet <= 0) {
    throw new Error(`未知彩种或价格未配置: ${code}`);
  }
  const nCombos = countCombos({ code, playType, front, back, tuo });
  // append: 基本 2 元 + 追加 1 元 = 3 元（1.5 倍），用整数算避免浮点：price × 3 / 2
  const per = append ? (pricePerBet * 3) / 2 : pricePerBet;
  return nCombos * per * multiplier;
}
