import { describe, it, expect } from 'vitest';
import { aggregateByTier, aggregateMonthly, type WinRow } from './stats';

describe('aggregateByTier', () => {
  it('returns empty arrays when no wins', () => {
    const result = aggregateByTier([]);
    expect(result.labels).toEqual([]);
    expect(result.counts).toEqual([]);
    expect(result.amounts).toEqual([]);
  });

  it('aggregates count and amount per tier, sorted by tier number', () => {
    const wins: WinRow[] = [
      { prize_tier: 3, prize_amount: 300000 },
      { prize_tier: 1, prize_amount: 500000000 },
      { prize_tier: 3, prize_amount: 200000 },
      { prize_tier: 6, prize_amount: 500 },
    ];
    const result = aggregateByTier(wins);
    // Sorted by tier: 1, 3, 6
    expect(result.labels).toEqual(['一等奖', '三等奖', '六等奖']);
    expect(result.counts).toEqual([1, 2, 1]);
    expect(result.amounts).toEqual([500000000, 500000, 500]);
  });

  it('uses 未知 label for tier 0 (null tier fallback)', () => {
    const wins: WinRow[] = [
      { prize_tier: null, prize_amount: 10000 },
      { prize_tier: 2, prize_amount: 200000 },
    ];
    const result = aggregateByTier(wins);
    expect(result.labels[0]).toBe('未知');
    expect(result.counts[0]).toBe(1);
  });

  it('treats null prize_amount as 0 (floating prize awaiting backfill)', () => {
    const wins: WinRow[] = [
      { prize_tier: 1, prize_amount: null },
      { prize_tier: 5, prize_amount: 1000 },
    ];
    const result = aggregateByTier(wins);
    expect(result.amounts).toEqual([0, 1000]);
    expect(result.counts).toEqual([1, 1]);
  });
});

describe('aggregateMonthly', () => {
  it('returns empty arrays when no data', () => {
    const result = aggregateMonthly([]);
    expect(result.months).toEqual([]);
    expect(result.costs).toEqual([]);
    expect(result.prizes).toEqual([]);
  });

  it('extracts month labels and converts cost/prize from cents to yuan', () => {
    const data = [
      { month: '2026-01', cost: 20000, prize: 0 },
      { month: '2026-02', cost: 10000, prize: 500000 },
      { month: '2026-03', cost: 0, prize: 0 },
    ];
    const result = aggregateMonthly(data);
    expect(result.months).toEqual(['2026-01', '2026-02', '2026-03']);
    expect(result.costs).toEqual([200, 100, 0]);
    expect(result.prizes).toEqual([0, 5000, 0]);
  });

  it('handles missing months gracefully (treats as 0)', () => {
    const data = [{ month: '2026-06', cost: 5000, prize: 100000 }];
    const result = aggregateMonthly(data);
    expect(result.months).toEqual(['2026-06']);
    expect(result.costs).toEqual([50]);
    expect(result.prizes).toEqual([1000]);
  });
});
