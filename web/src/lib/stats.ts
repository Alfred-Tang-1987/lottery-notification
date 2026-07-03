/**
 * Stats aggregation pure functions for MyStats page.
 * All functions pure, no IO, no Vue dependency, testable.
 */

const TIER_NAMES: Record<number, string> = {
  1: '一等奖',
  2: '二等奖',
  3: '三等奖',
  4: '四等奖',
  5: '五等奖',
  6: '六等奖',
  7: '七等奖',
  8: '八等奖',
  9: '九等奖',
};

export interface WinRow {
  prize_tier: number | null;
  prize_amount: number | null;
}

export interface MonthlyRow {
  month: string;
  cost: number;
  prize: number;
}

export interface TierAggregation {
  labels: string[];
  counts: number[];
  amounts: number[];
}

export interface MonthlyAggregation {
  months: string[];
  costs: number[];
  prizes: number[];
}

/**
 * Aggregate win records by prize tier.
 * Sorted by tier number ascending.
 * Null tier → "未知", null amount → 0 (floating prize awaiting backfill).
 */
export function aggregateByTier(wins: WinRow[]): TierAggregation {
  if (wins.length === 0) {
    return { labels: [], counts: [], amounts: [] };
  }

  const tierMap = new Map<number, { count: number; amount: number }>();
  for (const w of wins) {
    const tier = w.prize_tier ?? 0;
    const entry = tierMap.get(tier) ?? { count: 0, amount: 0 };
    entry.count += 1;
    entry.amount += w.prize_amount ?? 0;
    tierMap.set(tier, entry);
  }

  const sortedTiers = Array.from(tierMap.entries()).sort(([a], [b]) => a - b);
  const labels = sortedTiers.map(([t]) => (t === 0 ? '未知' : TIER_NAMES[t] ?? `T${t}`));
  const counts = sortedTiers.map(([, v]) => v.count);
  const amounts = sortedTiers.map(([, v]) => v.amount);

  return { labels, counts, amounts };
}

/**
 * Aggregate monthly cost/prize data.
 * Converts from cents to yuan (÷100).
 */
export function aggregateMonthly(data: MonthlyRow[]): MonthlyAggregation {
  if (data.length === 0) {
    return { months: [], costs: [], prizes: [] };
  }

  const months = data.map((d) => d.month);
  const costs = data.map((d) => d.cost / 100);
  const prizes = data.map((d) => d.prize / 100);

  return { months, costs, prizes };
}
