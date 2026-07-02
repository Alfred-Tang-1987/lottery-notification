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
  qlc: { front: { min: 1, max: 30, count: 7 }, back: null },
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
// 3. 玩法（lottery-rules.md 权威）
//    分区型（ssq/dlt/qlc/qxc）→ 单式/复式/胆拖
//    按位型（fc3d）→ 单选/组选3/组选6（现行叫"单选"，非"直选"）
//    按位型（pl3）→ 直选/组选3/组选6（沿用"直选"）
//    按位型（pl5）→ 直选
// ──────────────────────────────────────────────

const PLAY_TYPES: Record<string, string[]> = {
  ssq: ["single", "compound", "dantuo"],
  dlt: ["single", "compound", "dantuo"],
  qlc: ["single", "compound", "dantuo"],
  qxc: ["single", "compound", "dantuo"],
  fc3d: ["danxuan", "zuxuan3", "zuxuan6"],
  pl3: ["zhixuan", "zuxuan3", "zuxuan6"],
  pl5: ["zhixuan"],
};

export function getPlayTypes(code: string): string[] {
  return PLAY_TYPES[code] ?? [];
}

export const PLAY_TYPE_LABELS: Record<string, string> = {
  single: "单式",
  compound: "复式",
  dantuo: "胆拖",
  danxuan: "单选",
  zhixuan: "直选",
  zuxuan3: "组选3",
  zuxuan6: "组选6",
};

// ──────────────────────────────────────────────
// 4. 机选一注（纯函数，接收 code，返回号码）
// ──────────────────────────────────────────────

export interface RandomPickResult {
  front: number[];
  back?: number[];
}

export function randomPick(code: string): RandomPickResult {
  const r = getLotteryRange(code);
  if (!r) return { front: [] };

  const pick = (min: number, max: number, count: number): number[] => {
    const pool: number[] = [];
    for (let i = min; i <= max; i++) pool.push(i);
    const shuffled = [...pool].sort(() => Math.random() - 0.5);
    return shuffled.slice(0, count).sort((a, b) => a - b);
  };

  const front = pick(r.front.min, r.front.max, r.front.count);
  if (r.back) {
    return { front, back: pick(r.back.min, r.back.max, r.back.count) };
  }
  return { front };
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
  // front 重复
  const frontSet = new Set(front);
  if (frontSet.size !== front.length) {
    return { ok: false, error: `前区号码存在重复` };
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
  | { ok: true; data: { code: string; front: number[]; back?: number[] } }
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

  const numStrs = parts.slice(1);
  const nums: number[] = [];
  for (const s of numStrs) {
    const n = Number(s);
    if (!Number.isInteger(n) || s === "") {
      return { ok: false, error: `非数字: "${s}"`, line: lineNum };
    }
    nums.push(n);
  }

  const totalExpected = r.front.count + (r.back ? r.back.count : 0);
  if (nums.length < totalExpected) {
    return {
      ok: false,
      error: `号码数量不足：${code} 需要 ${totalExpected} 个号码，当前 ${nums.length} 个`,
      line: lineNum,
    };
  }

  const front = nums.slice(0, r.front.count);
  const back = r.back ? nums.slice(r.front.count, r.front.count + r.back.count) : undefined;

  const v = validateNumbers(code, front, back);
  if (!v.ok) {
    return { ok: false, error: v.error, line: lineNum };
  }

  return { ok: true, data: { code, front, back } };
}
