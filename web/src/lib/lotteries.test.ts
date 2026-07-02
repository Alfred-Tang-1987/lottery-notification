/**
 * T6b: 彩种规则纯函数（lottery-rules.md 权威）。
 *
 * 覆盖：
 * - 7 彩种号码区间（front/back）
 * - 7 彩种玩法词汇（分区型 vs 按位型，lottery-rules.md）
 * - 机选一注（各彩种 count/range 校验）
 * - 号码校验（越界 / 数量错 / 重复 → reject）
 * - CSV 行解析（含错误反馈，不静默丢行）
 */
import { describe, expect, it } from "vitest";
import {
  frontNumbers,
  backNumbers,
  getLotteryRange,
  getPlayTypes,
  PLAY_TYPE_LABELS,
  randomPick,
  validateNumbers,
  parseCsvLine,
  ALL_LOTTERY_CODES,
} from "./lotteries";

describe("lotteries.ts — range per lottery (lottery-rules.md)", () => {
  it("returns ssq front 1-33 count 6, back 1-16 count 1", () => {
    const r = getLotteryRange("ssq");
    expect(r).toEqual({
      front: { min: 1, max: 33, count: 6 },
      back: { min: 1, max: 16, count: 1 },
    });
  });

  it("returns dlt front 1-35 count 5, back 1-12 count 2", () => {
    const r = getLotteryRange("dlt");
    expect(r).toEqual({
      front: { min: 1, max: 35, count: 5 },
      back: { min: 1, max: 12, count: 2 },
    });
  });

  it("returns qlc front 1-30 count 7, no back (特别号同源 01-30 池)", () => {
    const r = getLotteryRange("qlc");
    expect(r?.front).toEqual({ min: 1, max: 30, count: 7 });
    expect(r?.back).toBeNull();
  });

  it("returns qxc front 0-9 count 6, back 0-14 count 1 (2020 改版)", () => {
    const r = getLotteryRange("qxc");
    expect(r).toEqual({
      front: { min: 0, max: 9, count: 6 },
      back: { min: 0, max: 14, count: 1 },
    });
  });

  it("returns fc3d positional 0-9 count 3 (百十个位)", () => {
    const r = getLotteryRange("fc3d");
    expect(r?.front).toEqual({ min: 0, max: 9, count: 3 });
    expect(r?.back).toBeNull();
  });

  it("returns pl3 positional 0-9 count 3", () => {
    const r = getLotteryRange("pl3");
    expect(r?.front).toEqual({ min: 0, max: 9, count: 3 });
    expect(r?.back).toBeNull();
  });

  it("returns pl5 positional 0-9 count 5", () => {
    const r = getLotteryRange("pl5");
    expect(r?.front).toEqual({ min: 0, max: 9, count: 5 });
    expect(r?.back).toBeNull();
  });

  it("returns null for unknown lottery code", () => {
    expect(getLotteryRange("unknown")).toBeNull();
  });
});

describe("lotteries.ts — frontNumbers / backNumbers", () => {
  it("frontNumbers('ssq') returns 1..33", () => {
    const arr = frontNumbers("ssq");
    expect(arr.length).toBe(33);
    expect(arr[0]).toBe(1);
    expect(arr[32]).toBe(33);
  });

  it("backNumbers('ssq') returns 1..16", () => {
    const arr = backNumbers("ssq");
    expect(arr.length).toBe(16);
    expect(arr[0]).toBe(1);
    expect(arr[15]).toBe(16);
  });

  it("backNumbers('qlc') returns empty (no back zone)", () => {
    expect(backNumbers("qlc")).toEqual([]);
  });

  it("frontNumbers('qxc') returns 0..9 (6 位 0-9)", () => {
    expect(frontNumbers("qxc")).toEqual([0, 1, 2, 3, 4, 5, 6, 7, 8, 9]);
  });

  it("backNumbers('qxc') returns 0..14 (后区 0-14)", () => {
    const arr = backNumbers("qxc");
    expect(arr.length).toBe(15);
    expect(arr[0]).toBe(0);
    expect(arr[14]).toBe(14);
  });

  it("frontNumbers('unknown') returns []", () => {
    expect(frontNumbers("unknown")).toEqual([]);
  });
});

describe("lotteries.ts — play types per lottery (lottery-rules.md)", () => {
  it("ssq/dlt/qlc/qxc (分区型) → 单式/复式/胆拖", () => {
    for (const code of ["ssq", "dlt", "qlc", "qxc"]) {
      expect(getPlayTypes(code)).toEqual(["single", "compound", "dantuo"]);
    }
  });

  it("fc3d (福彩3D) → 单选/组选3/组选6 (无'单式', '直选'是旧称)", () => {
    expect(getPlayTypes("fc3d")).toEqual(["danxuan", "zuxuan3", "zuxuan6"]);
  });

  it("pl3 (排列3) → 直选/组选3/组选6 (沿用'直选'叫法, 与 fc3d 不同)", () => {
    expect(getPlayTypes("pl3")).toEqual(["zhixuan", "zuxuan3", "zuxuan6"]);
  });

  it("pl5 (排列5) → 仅直选", () => {
    expect(getPlayTypes("pl5")).toEqual(["zhixuan"]);
  });

  it("labels map 覆盖全部 play type 键", () => {
    const allKeys = new Set<string>();
    for (const code of ALL_LOTTERY_CODES) {
      for (const pt of getPlayTypes(code)) allKeys.add(pt);
    }
    for (const k of allKeys) {
      expect(PLAY_TYPE_LABELS[k], `missing label for ${k}`).toBeDefined();
    }
  });

  it("unknown lottery returns []", () => {
    expect(getPlayTypes("zzz")).toEqual([]);
  });
});

describe("lotteries.ts — randomPick", () => {
  it("returns front count = range.count, all within [min,max]", () => {
    for (const code of ALL_LOTTERY_CODES) {
      const r = getLotteryRange(code);
      if (!r) continue;
      const pick = randomPick(code);
      expect(pick.front.length).toBe(r.front.count);
      for (const n of pick.front) {
        expect(n).toBeGreaterThanOrEqual(r.front.min);
        expect(n).toBeLessThanOrEqual(r.front.max);
      }
      // front 应无重复且排序
      const uniq = new Set(pick.front);
      expect(uniq.size).toBe(pick.front.length);
      for (let i = 1; i < pick.front.length; i++) {
        expect(pick.front[i]).toBeGreaterThan(pick.front[i - 1]);
      }
    }
  });

  it("returns back when lottery has back zone; empty when not", () => {
    for (const code of ALL_LOTTERY_CODES) {
      const r = getLotteryRange(code);
      if (!r) continue;
      const pick = randomPick(code);
      if (r.back) {
        expect(pick.back?.length).toBe(r.back.count);
        for (const n of pick.back || []) {
          expect(n).toBeGreaterThanOrEqual(r.back.min);
          expect(n).toBeLessThanOrEqual(r.back.max);
        }
      } else {
        expect(pick.back).toBeUndefined();
      }
    }
  });

  it("returns empty front/back for unknown code", () => {
    const pick = randomPick("zzz");
    expect(pick.front).toEqual([]);
    expect(pick.back).toBeUndefined();
  });
});

describe("lotteries.ts — validateNumbers", () => {
  it("accepts valid ssq single: 6 front + 1 back", () => {
    const r = validateNumbers("ssq", [1, 2, 3, 4, 5, 6], [7]);
    expect(r.ok).toBe(true);
  });

  it("accepts valid ssq compound (复式): >6 front + >1 back", () => {
    const r = validateNumbers("ssq", [1, 2, 3, 4, 5, 6, 7], [8, 9]);
    expect(r.ok).toBe(true);
  });

  it("rejects out-of-range front number", () => {
    const r = validateNumbers("ssq", [1, 2, 3, 4, 5, 34], [7]);
    expect(r.ok).toBe(false);
    expect(r.error).toMatch(/34/);
  });

  it("rejects out-of-range back number", () => {
    const r = validateNumbers("ssq", [1, 2, 3, 4, 5, 6], [17]);
    expect(r.ok).toBe(false);
    expect(r.error).toMatch(/17/);
  });

  it("rejects too few front numbers (单式 < count)", () => {
    const r = validateNumbers("ssq", [1, 2, 3], [7]);
    expect(r.ok).toBe(false);
    expect(r.error).toMatch(/前区/);
  });

  it("rejects duplicate numbers", () => {
    const r = validateNumbers("ssq", [1, 1, 2, 3, 4, 5], [7]);
    expect(r.ok).toBe(false);
    expect(r.error).toMatch(/重复/);
  });

  it("rejects when lottery without back zone is given back numbers", () => {
    const r = validateNumbers("qlc", [1, 2, 3, 4, 5, 6, 7], [8]);
    expect(r.ok).toBe(false);
  });

  it("accepts qlc without back", () => {
    const r = validateNumbers("qlc", [1, 2, 3, 4, 5, 6, 7]);
    expect(r.ok).toBe(true);
  });

  it("accepts qxc hybrid: 6 front 0-9, 1 back 0-14", () => {
    const r = validateNumbers("qxc", [0, 1, 2, 3, 4, 5], [14]);
    expect(r.ok).toBe(true);
  });

  it("rejects qxc back >14", () => {
    const r = validateNumbers("qxc", [0, 1, 2, 3, 4, 5], [15]);
    expect(r.ok).toBe(false);
  });

  it("returns error for unknown lottery code", () => {
    const r = validateNumbers("zzz", [1, 2, 3]);
    expect(r.ok).toBe(false);
    expect(r.error).toMatch(/未知彩种/);
  });
});

describe("lotteries.ts — parseCsvLine", () => {
  it("parses ssq line: ssq,1,2,3,4,5,6,7", () => {
    const r = parseCsvLine("ssq,1,2,3,4,5,6,7");
    expect(r.ok).toBe(true);
    if (r.ok) {
      expect(r.data.code).toBe("ssq");
      expect(r.data.front).toEqual([1, 2, 3, 4, 5, 6]);
      expect(r.data.back).toEqual([7]);
    }
  });

  it("parses dlt line: dlt,5,10,15,20,25,3,8", () => {
    const r = parseCsvLine("dlt,5,10,15,20,25,3,8");
    expect(r.ok).toBe(true);
    if (r.ok) {
      expect(r.data.code).toBe("dlt");
      expect(r.data.front).toEqual([5, 10, 15, 20, 25]);
      expect(r.data.back).toEqual([3, 8]);
    }
  });

  it("parses qlc (no back) line: qlc,1,2,3,4,5,6,7", () => {
    const r = parseCsvLine("qlc,1,2,3,4,5,6,7");
    expect(r.ok).toBe(true);
    if (r.ok) {
      expect(r.data.front).toEqual([1, 2, 3, 4, 5, 6, 7]);
      expect(r.data.back).toBeUndefined();
    }
  });

  it("parses qxc hybrid: qxc,0,1,2,3,4,5,9", () => {
    const r = parseCsvLine("qxc,0,1,2,3,4,5,9");
    expect(r.ok).toBe(true);
    if (r.ok) {
      expect(r.data.front).toEqual([0, 1, 2, 3, 4, 5]);
      expect(r.data.back).toEqual([9]);
    }
  });

  it("parses fc3d positional: fc3d,1,2,3", () => {
    const r = parseCsvLine("fc3d,1,2,3");
    expect(r.ok).toBe(true);
    if (r.ok) {
      expect(r.data.front).toEqual([1, 2, 3]);
    }
  });

  it("trims whitespace and supports CRLF", () => {
    const r = parseCsvLine("  ssq, 1, 2, 3, 4, 5, 6, 7  \r");
    expect(r.ok).toBe(true);
    if (r.ok) {
      expect(r.data.front).toEqual([1, 2, 3, 4, 5, 6]);
    }
  });

  it("rejects unknown lottery code with line number", () => {
    const r = parseCsvLine("xyz,1,2,3", 5);
    expect(r.ok).toBe(false);
    if (!r.ok) {
      expect(r.line).toBe(5);
      expect(r.error).toMatch(/未知彩种/);
    }
  });

  it("rejects non-numeric values", () => {
    const r = parseCsvLine("ssq,1,2,x,4,5,6,7");
    expect(r.ok).toBe(false);
    if (!r.ok) {
      expect(r.error).toMatch(/非数字/);
    }
  });

  it("rejects insufficient numbers (less than front count)", () => {
    const r = parseCsvLine("ssq,1,2,3");
    expect(r.ok).toBe(false);
    if (!r.ok) {
      expect(r.error).toMatch(/数量/);
    }
  });

  it("rejects empty line", () => {
    const r = parseCsvLine("");
    expect(r.ok).toBe(false);
  });

  it("rejects numbers out of range", () => {
    const r = parseCsvLine("ssq,1,2,3,4,5,34,7");
    expect(r.ok).toBe(false);
    if (!r.ok) {
      expect(r.error).toMatch(/34/);
    }
  });
});
