import { describe, it, expect } from "vitest";
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
  isPositional,
} from "./lotteries";

describe("lotteries.ts — frontNumbers/backNumbers", () => {
  it("returns 1..33 for ssq front", () => {
    const front = frontNumbers("ssq");
    expect(front.length).toBe(33);
    expect(front[0]).toBe(1);
    expect(front[32]).toBe(33);
  });

  it("returns 1..16 for ssq back", () => {
    const back = backNumbers("ssq");
    expect(back.length).toBe(16);
    expect(back[0]).toBe(1);
    expect(back[15]).toBe(16);
  });

  it("returns empty array for unknown code", () => {
    expect(frontNumbers("zzz")).toEqual([]);
    expect(backNumbers("zzz")).toEqual([]);
  });

  it("returns 0..9 for fc3d front (positional)", () => {
    const front = frontNumbers("fc3d");
    expect(front.length).toBe(10);
    expect(front[0]).toBe(0);
    expect(front[9]).toBe(9);
  });

  it("returns 0..14 for qxc back", () => {
    const back = backNumbers("qxc");
    expect(back.length).toBe(15);
    expect(back[0]).toBe(0);
    expect(back[14]).toBe(14);
  });
});

describe("lotteries.ts — getLotteryRange", () => {
  it("returns correct ranges for ssq", () => {
    const r = getLotteryRange("ssq");
    expect(r).toEqual({
      front: { min: 1, max: 33, count: 6 },
      back: { min: 1, max: 16, count: 1 },
    });
  });

  it("returns null for unknown code", () => {
    expect(getLotteryRange("zzz")).toBeNull();
  });

  it("returns correct ranges for positional lotteries", () => {
    const fc3d = getLotteryRange("fc3d");
    expect(fc3d).toEqual({
      front: { min: 0, max: 9, count: 3 },
      back: null,
    });
  });

  it("returns back range 1-30 for QLC (特别号同源 01-30 池，spec §12.2 row 7)", () => {
    // 后端种子 app/seeds/lottery_types.py:37: back: {'min': 1, 'max': 30, 'count': 1}
    // 原型 08-trend.html:275: qlc:{...back:[1,30]}
    // docs/reference/lottery-rules.md:34: 特别号码**同源于 01–30 池**
    const qlc = getLotteryRange("qlc");
    expect(qlc).toEqual({
      front: { min: 1, max: 30, count: 7 },
      back: { min: 1, max: 30, count: 1 },
    });
  });
});

describe("lotteries.ts — getPlayTypes", () => {
  it("returns single/fushi/dantuo for partition lotteries (spec §5.2 + backend seed)", () => {
    // spec line 171: 'single'(单式)/'fushi'(复式)/'dantuo'(胆拖)
    // backend app/seeds/lottery_types.py uses 'fushi' (not 'compound')
    for (const code of ["ssq", "dlt", "qlc", "qxc"]) {
      const types = getPlayTypes(code);
      expect(types).toContain("single");
      expect(types).toContain("fushi");
      expect(types).toContain("dantuo");
      // 'compound' must NOT be used — it breaks the cross-layer contract
      expect(types).not.toContain("compound");
    }
  });

  it("returns danxuan only for fc3d (B1 boundary)", () => {
    expect(getPlayTypes("fc3d")).toEqual(["danxuan"]);
  });

  it("returns zhixuan only for pl3 (B1 boundary)", () => {
    expect(getPlayTypes("pl3")).toEqual(["zhixuan"]);
  });

  it("returns zhixuan only for pl5", () => {
    expect(getPlayTypes("pl5")).toEqual(["zhixuan"]);
  });

  it("returns empty array for unknown code", () => {
    expect(getPlayTypes("zzz")).toEqual([]);
  });
});

describe("lotteries.ts — PLAY_TYPE_LABELS", () => {
  it("has Chinese labels for all play types", () => {
    expect(PLAY_TYPE_LABELS["single"]).toBe("单式");
    expect(PLAY_TYPE_LABELS["fushi"]).toBe("复式");
    expect(PLAY_TYPE_LABELS["dantuo"]).toBe("胆拖");
    expect(PLAY_TYPE_LABELS["danxuan"]).toBe("单选");
    expect(PLAY_TYPE_LABELS["zhixuan"]).toBe("直选");
    expect(PLAY_TYPE_LABELS["zuxuan3"]).toBe("组选3");
    expect(PLAY_TYPE_LABELS["zuxuan6"]).toBe("组选6");
  });
});

describe("lotteries.ts — isPositional", () => {
  it("returns true for fc3d/pl3/pl5/qxc (按位型, digits may repeat)", () => {
    expect(isPositional("fc3d")).toBe(true);
    expect(isPositional("pl3")).toBe(true);
    expect(isPositional("pl5")).toBe(true);
    expect(isPositional("qxc")).toBe(true);
  });

  it("returns false for partition lotteries (ssq/dlt/qlc)", () => {
    expect(isPositional("ssq")).toBe(false);
    expect(isPositional("dlt")).toBe(false);
    expect(isPositional("qlc")).toBe(false);
  });

  it("returns false for unknown code", () => {
    expect(isPositional("zzz")).toBe(false);
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
      // front 应排序 (for both partition and positional)
      for (let i = 1; i < pick.front.length; i++) {
        expect(pick.front[i]).toBeGreaterThanOrEqual(pick.front[i - 1]);
      }
      // Partition: no duplicates
      if (!isPositional(code)) {
        const uniq = new Set(pick.front);
        expect(uniq.size).toBe(pick.front.length);
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

  it("randomPick with frontCount/backCount override returns requested count (复式, plan Step 2)", () => {
    // plan Step 2: 机选一注（复式/胆拖可配红蓝数）
    const pick = randomPick("ssq", { frontCount: 8, backCount: 2 });
    expect(pick.front.length).toBe(8);
    expect(pick.back?.length).toBe(2);
    // fushi front: unique, sorted, in range
    const uniq = new Set(pick.front);
    expect(uniq.size).toBe(8);
    for (let i = 1; i < pick.front.length; i++) {
      expect(pick.front[i]).toBeGreaterThan(pick.front[i - 1]);
    }
  });

  it("randomPick for positional lottery allows digit repeats (fc3d)", () => {
    // With 3 digits from 0-9, duplicates are very likely across many trials
    let foundRepeat = false;
    for (let i = 0; i < 100; i++) {
      const pick = randomPick("fc3d");
      if (new Set(pick.front).size < pick.front.length) {
        foundRepeat = true;
        break;
      }
    }
    expect(foundRepeat).toBe(true);
  });

  it("randomPick with dantuo returns dan/tuo split (胆拖, plan Step 2)", () => {
    const pick = randomPick("ssq", { playType: "dantuo" });
    expect(pick.dan).toBeDefined();
    expect(pick.tuo).toBeDefined();
    expect(pick.dan!.length).toBeGreaterThanOrEqual(1);
    expect(pick.dan!.length).toBeLessThanOrEqual(5);
    expect(pick.dan!.length + pick.tuo!.length).toBeGreaterThanOrEqual(6);
    // dan and tuo should not overlap
    const danSet = new Set(pick.dan!);
    for (const n of pick.tuo!) {
      expect(danSet.has(n)).toBe(false);
    }
    // All within range
    for (const n of [...pick.dan!, ...pick.tuo!]) {
      expect(n).toBeGreaterThanOrEqual(1);
      expect(n).toBeLessThanOrEqual(33);
    }
  });
});

describe("lotteries.ts — validateNumbers", () => {
  it("accepts single-style numbers", () => {
    expect(validateNumbers("ssq", [1, 2, 3, 4, 5, 6], [7]).ok).toBe(true);
    expect(validateNumbers("dlt", [1, 2, 3, 4, 5], [1, 2]).ok).toBe(true);
  });

  it("rejects out-of-range numbers", () => {
    expect(validateNumbers("ssq", [0, 1, 2, 3, 4, 5], [6]).ok).toBe(false);
    expect(validateNumbers("ssq", [1, 2, 3, 4, 5, 34], [6]).ok).toBe(false);
  });

  it("rejects too few numbers", () => {
    expect(validateNumbers("ssq", [1, 2, 3, 4, 5], [6]).ok).toBe(false);
  });

  it("rejects duplicate front numbers for partition lotteries", () => {
    expect(validateNumbers("ssq", [1, 1, 2, 3, 4, 5], [6]).ok).toBe(false);
    expect(validateNumbers("dlt", [1, 2, 3, 4, 5], [1, 1]).ok).toBe(false);
  });

  it("ALLOWS duplicate digits for positional lotteries (fc3d/pl3/pl5/qxc front)", () => {
    // 组选三 requires exactly 2 identical digits (e.g. 1,1,2)
    expect(validateNumbers("fc3d", [1, 1, 2]).ok).toBe(true);
    expect(validateNumbers("pl3", [5, 5, 5]).ok).toBe(true);
    expect(validateNumbers("pl5", [1, 1, 2, 3, 4]).ok).toBe(true);
    // qxc front is 6 digits 0-9, repeats allowed
    expect(validateNumbers("qxc", [1, 1, 2, 3, 4, 5], [7]).ok).toBe(true);
  });

  it("rejects unknown lottery code", () => {
    expect(validateNumbers("zzz", [1, 2, 3]).ok).toBe(false);
  });

  it("rejects positional lottery with too few digits", () => {
    expect(validateNumbers("fc3d", [1, 2]).ok).toBe(false);
    expect(validateNumbers("pl5", [1, 2, 3]).ok).toBe(false);
  });
});

describe("lotteries.ts — parseCsvLine", () => {
  it("parses valid single-style ssq", () => {
    const r = parseCsvLine("ssq,1,2,3,4,5,6,7", 1);
    expect(r.ok).toBe(true);
    if (r.ok) {
      expect(r.data.code).toBe("ssq");
      expect(r.data.front).toEqual([1, 2, 3, 4, 5, 6]);
      expect(r.data.back).toEqual([7]);
    }
  });

  it("parses valid dlt", () => {
    const r = parseCsvLine("dlt,1,2,3,4,5,6,7", 1);
    expect(r.ok).toBe(true);
    if (r.ok) {
      expect(r.data.code).toBe("dlt");
      expect(r.data.front).toEqual([1, 2, 3, 4, 5]);
      expect(r.data.back).toEqual([6, 7]);
    }
  });

  it("parses positional fc3d", () => {
    const r = parseCsvLine("fc3d,1,2,3", 1);
    expect(r.ok).toBe(true);
    if (r.ok) {
      expect(r.data.code).toBe("fc3d");
      expect(r.data.front).toEqual([1, 2, 3]);
      expect(r.data.back).toBeUndefined();
    }
  });

  it("parses optional draw_no (期号) field after code (plan Step 3: 彩种,期号,号码...)", () => {
    // plan Step 3 CSV format: 彩种,期号,号码...
    // draw_no is alphanumeric period identifier, must not be parsed as a number
    const r = parseCsvLine("ssq,2024090,1,2,3,4,5,6,7", 1);
    expect(r.ok).toBe(true);
    if (r.ok) {
      expect(r.data.code).toBe("ssq");
      expect(r.data.draw_no).toBe("2024090");
      expect(r.data.front).toEqual([1, 2, 3, 4, 5, 6]);
      expect(r.data.back).toEqual([7]);
    }
  });

  it("draw_no is optional — plain 彩种,号码... still parses", () => {
    const r = parseCsvLine("ssq,1,2,3,4,5,6,7", 1);
    expect(r.ok).toBe(true);
    if (r.ok) {
      expect(r.data.draw_no).toBeUndefined();
    }
  });

  it("rejects empty line", () => {
    const r = parseCsvLine("", 1);
    expect(r.ok).toBe(false);
    if (!r.ok) expect(r.error).toContain("空行");
  });

  it("rejects unknown lottery code", () => {
    const r = parseCsvLine("zzz,1,2,3", 1);
    expect(r.ok).toBe(false);
    if (!r.ok) expect(r.error).toContain("未知彩种");
  });

  it("rejects non-numeric values", () => {
    const r = parseCsvLine("ssq,1,a,3,4,5,6", 1);
    expect(r.ok).toBe(false);
    if (!r.ok) expect(r.error).toContain("非数字");
  });

  it("rejects negative numbers with clear message", () => {
    const r = parseCsvLine("ssq,-1,2,3,4,5,6", 1);
    expect(r.ok).toBe(false);
    if (!r.ok) expect(r.error).toContain("负数无效");
  });

  it("rejects out-of-range numbers", () => {
    const r = parseCsvLine("ssq,0,1,2,3,4,5,6", 1);
    expect(r.ok).toBe(false);
    if (!r.ok) expect(r.error).toContain("超出范围");
  });

  it("rejects insufficient numbers", () => {
    const r = parseCsvLine("ssq,1,2,3,4,5", 1);
    expect(r.ok).toBe(false);
    if (!r.ok) expect(r.error).toContain("号码数量不足");
  });

  it("rejects duplicate numbers for partition lotteries", () => {
    const r = parseCsvLine("ssq,1,1,2,3,4,5,6", 1);
    expect(r.ok).toBe(false);
    if (!r.ok) expect(r.error).toContain("重复");
  });
});

// ──────────────────────────────────────────────
// countCombos + calculateCost — 倍投/复式/胆拖/追加 投入自动计算
// 业务规则（lottery-rules.md + app/domain/entry.py Entry.cost）：
//   cost(分) = n_combos × price_per_bet × (append?1.5:1) × multiplier
//   - single/zhixuan/danxuan: n_combos = 1
//   - fushi (分区型 ssq/dlt/qlc): C(front.len, front.count) × C(back.len, back.count)
//   - dantuo: C(tuo.len, front.count - dan.len) × (back? C(back.len, back.count) : 1)
// ──────────────────────────────────────────────

import { countCombos, calculateCost, PRICE_PER_BET } from "./lotteries";

describe("lotteries.ts — countCombos", () => {
  it("single/zhixuan/danxuan: n_combos = 1", () => {
    expect(countCombos({ code: "ssq", playType: "single", front: [1, 2, 3, 4, 5, 6], back: [7] })).toBe(1);
    expect(countCombos({ code: "pl5", playType: "zhixuan", front: [1, 2, 3, 4, 5] })).toBe(1);
    expect(countCombos({ code: "fc3d", playType: "danxuan", front: [1, 2, 3] })).toBe(1);
  });

  it("ssq fushi 7+2: C(7,6)×C(2,1) = 7×2 = 14", () => {
    expect(
      countCombos({
        code: "ssq",
        playType: "fushi",
        front: [1, 2, 3, 4, 5, 6, 7], // 7 个红球
        back: [8, 9], // 2 个蓝球
      }),
    ).toBe(14);
  });

  it("dlt fushi 6+3: C(6,5)×C(3,2) = 6×3 = 18", () => {
    expect(
      countCombos({
        code: "dlt",
        playType: "fushi",
        front: [1, 2, 3, 4, 5, 6],
        back: [7, 8, 9],
      }),
    ).toBe(18);
  });

  it("ssq dantuo 1胆5拖 + 1蓝: C(5,5)×C(1,1) = 1", () => {
    expect(
      countCombos({
        code: "ssq",
        playType: "dantuo",
        front: [1], // 胆 1 个
        back: [7],
        tuo: [2, 3, 4, 5, 6], // 拖 5 个，胆+拖=6=front.count
      }),
    ).toBe(1);
  });

  it("ssq dantuo 1胆6拖 + 1蓝: C(6,5)×C(1,1) = 6", () => {
    expect(
      countCombos({
        code: "ssq",
        playType: "dantuo",
        front: [1], // 胆
        back: [7],
        tuo: [2, 3, 4, 5, 6, 7], // 拖 6 个，需选 5 个
      }),
    ).toBe(6);
  });

  it("dlt dantuo 2胆4拖 + 2蓝: C(4,3)×C(2,2) = 4×1 = 4", () => {
    expect(
      countCombos({
        code: "dlt",
        playType: "dantuo",
        front: [1, 2], // 胆 2
        back: [7, 8],
        tuo: [3, 4, 5, 6], // 拖 4，需选 3
      }),
    ).toBe(4);
  });

  it("无后区彩种（fc3d/pl3/pl5）fushi 不存在该玩法，但单式 n_combos=1", () => {
    expect(countCombos({ code: "pl5", playType: "zhixuan", front: [1, 2, 3, 4, 5] })).toBe(1);
  });
});

describe("lotteries.ts — calculateCost", () => {
  it("ssq single 1倍: 1注 × 200分 × 1 = 200 分 = 2 元", () => {
    expect(
      calculateCost({
        code: "ssq",
        playType: "single",
        front: [1, 2, 3, 4, 5, 6],
        back: [7],
        multiplier: 1,
      }),
    ).toBe(200);
  });

  it("ssq single 5倍: 1注 × 200分 × 5 = 1000 分 = 10 元", () => {
    expect(
      calculateCost({
        code: "ssq",
        playType: "single",
        front: [1, 2, 3, 4, 5, 6],
        back: [7],
        multiplier: 5,
      }),
    ).toBe(1000);
  });

  it("ssq fushi 7+2, 1倍: 14注 × 200分 × 1 = 2800 分 = 28 元", () => {
    expect(
      calculateCost({
        code: "ssq",
        playType: "fushi",
        front: [1, 2, 3, 4, 5, 6, 7],
        back: [8, 9],
        multiplier: 1,
      }),
    ).toBe(2800);
  });

  it("dlt single 追加 1倍: 1注 × 200分 × 1.5 × 1 = 300 分 = 3 元", () => {
    expect(
      calculateCost({
        code: "dlt",
        playType: "single",
        front: [1, 2, 3, 4, 5],
        back: [6, 7],
        multiplier: 1,
        append: true,
      }),
    ).toBe(300);
  });

  it("dlt fushi 6+3 追加 2倍: 18注 × 200 × 1.5 × 2 = 10800 分 = 108 元", () => {
    expect(
      calculateCost({
        code: "dlt",
        playType: "fushi",
        front: [1, 2, 3, 4, 5, 6],
        back: [7, 8, 9],
        multiplier: 2,
        append: true,
      }),
    ).toBe(10800);
  });

  it("非 dlt 彩种 append=true 应抛错（仅大乐透支持追加）", () => {
    expect(() =>
      calculateCost({
        code: "ssq",
        playType: "single",
        front: [1, 2, 3, 4, 5, 6],
        back: [7],
        multiplier: 1,
        append: true,
      }),
    ).toThrow(/append|追加/);
  });

  it("PRICE_PER_BET 所有彩种 = 200 分（2 元）", () => {
    for (const code of ["ssq", "dlt", "qlc", "qxc", "fc3d", "pl3", "pl5"]) {
      expect(PRICE_PER_BET[code]).toBe(200);
    }
  });
});
