import { describe, it, expect } from 'vitest';
import { calculateMissCounts } from './miss';

describe('calculateMissCounts', () => {
  it('returns zero miss for all numbers when draws is empty', () => {
    const result = calculateMissCounts([], { front: { min: 1, max: 5 } });
    expect(result.front).toEqual({ 1: 0, 2: 0, 3: 0, 4: 0, 5: 0 });
  });

  it('calculates miss for front zone independently', () => {
    const draws = [
      { front: [1, 2, 3] },
      { front: [1, 2, 4] },
      { front: [1, 3, 5] },
    ];
    const result = calculateMissCounts(draws, { front: { min: 1, max: 5 } });
    // Most recent (idx 2): [1,3,5]
    // Number 1: appears in most recent → miss = 0
    // Number 2: not in most recent, last seen at idx 1 → miss = 1
    // Number 3: appears in most recent → miss = 0
    // Number 4: not in most recent, last seen at idx 1 → miss = 1
    // Number 5: appears in most recent → miss = 0
    expect(result.front).toEqual({ 1: 0, 2: 1, 3: 0, 4: 1, 5: 0 });
  });

  it('calculates miss for back zone independently', () => {
    const draws = [
      { front: [1, 2], back: [1, 2] },
      { front: [3, 4], back: [1, 3] },
      { front: [5, 6], back: [2, 3] },
    ];
    const result = calculateMissCounts(draws, {
      front: { min: 1, max: 6 },
      back: { min: 1, max: 3 },
    });
    // Back zone most recent (idx 2): [2,3]
    // Number 1: not in most recent, last seen at idx 1 → miss = 1
    // Number 2: appears in most recent → miss = 0
    // Number 3: appears in most recent → miss = 0
    expect(result.back).toEqual({ 1: 1, 2: 0, 3: 0 });
  });

  it('handles red/blue zone overlap independently (SSQ case: 1-16 in both)', () => {
    // SSQ: front 1-33, back 1-16 (overlapping range)
    const draws = [
      { front: [1, 2, 3], back: [1, 2, 3] },
      { front: [4, 5, 6], back: [1, 4, 5] },
      { front: [7, 8, 9], back: [2, 6, 7] },
    ];
    const result = calculateMissCounts(draws, {
      front: { min: 1, max: 9 },
      back: { min: 1, max: 7 },
    });
    // Front zone:
    // Number 1: last seen at idx 0, not in idx 1 or 2 → miss = 2
    // Number 4: last seen at idx 1, not in idx 2 → miss = 1
    // Number 7: appears in most recent → miss = 0
    expect(result.front[1]).toBe(2);
    expect(result.front[4]).toBe(1);
    expect(result.front[7]).toBe(0);

    // Back zone (independent counting):
    // Number 1: last seen at idx 1, not in idx 2 → miss = 1
    // Number 2: appears in most recent → miss = 0
    // Number 6: appears in most recent → miss = 0
    expect(result.back![1]).toBe(1);
    expect(result.back![2]).toBe(0);
    expect(result.back![6]).toBe(0);
  });

  it('returns miss = number of draws when number never appears', () => {
    const draws = [
      { front: [1, 2, 3] },
      { front: [1, 2, 4] },
      { front: [1, 3, 5] },
    ];
    const result = calculateMissCounts(draws, { front: { min: 1, max: 10 } });
    // Number 10 never appears → miss = 3 (all draws)
    expect(result.front[10]).toBe(3);
  });

  it('handles draws without back zone (e.g., FC3D, PL3)', () => {
    const draws = [
      { front: [1, 2, 3] },
      { front: [4, 5, 6] },
    ];
    const result = calculateMissCounts(draws, { front: { min: 1, max: 6 } });
    expect(result.back).toBeUndefined();
  });

  it('handles QXC with back zone 0-14', () => {
    const draws = [
      { front: [1, 2], back: [0, 5, 14] },
      { front: [3, 4], back: [0, 7, 14] },
    ];
    const result = calculateMissCounts(draws, {
      front: { min: 0, max: 9 },
      back: { min: 0, max: 14 },
    });
    // Back zone: 0 and 14 appear in both, 5 only in idx 0, 7 only in idx 1
    expect(result.back![0]).toBe(0);
    expect(result.back![5]).toBe(1);
    expect(result.back![7]).toBe(0);
    expect(result.back![14]).toBe(0);
  });

  it('calculates miss from most recent to last appearance', () => {
    const draws = [
      { front: [1] }, // idx 0
      { front: [] }, // idx 1
      { front: [] }, // idx 2
      { front: [] }, // idx 3 (most recent)
    ];
    const result = calculateMissCounts(draws, { front: { min: 1, max: 1 } });
    // Number 1: last seen at idx 0, most recent is idx 3
    // Miss = 3 (missed draws at idx 1, 2, 3)
    expect(result.front[1]).toBe(3);
  });
});
