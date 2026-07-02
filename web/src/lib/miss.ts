export interface NumberRange {
  min: number;
  max: number;
}

export interface DrawData {
  front: number[];
  back?: number[];
}

export interface Ranges {
  front: NumberRange;
  back?: NumberRange | null;
}

export interface MissResult {
  front: Record<number, number>;
  back?: Record<number, number>;
}

/**
 * Calculate miss counts (遗漏次数) for lottery numbers.
 * Miss count = number of consecutive draws from most recent where the number did NOT appear.
 * Red and back zones are counted independently.
 *
 * @param draws Array of draw data, ordered from oldest to most recent
 * @param ranges Number ranges for front and back zones
 * @returns Miss counts per number per zone
 */
export function calculateMissCounts(draws: DrawData[], ranges: Ranges): MissResult {
  const result: MissResult = {
    front: {}
  };

  // Initialize all numbers with 0 miss
  for (let n = ranges.front.min; n <= ranges.front.max; n++) {
    result.front[n] = 0;
  }

  if (ranges.back) {
    result.back = {};
    for (let n = ranges.back.min; n <= ranges.back.max; n++) {
      result.back[n] = 0;
    }
  }

  // Track which numbers have been seen (to only record first encounter)
  const frontSeen = new Set<number>();
  const backSeen = new Set<number>();

  // Walk from most recent to oldest, accumulating miss count
  let missCounter = 0;

  for (let i = draws.length - 1; i >= 0; i--) {
    const draw = draws[i];

    // Update front zone
    const frontSet = new Set(draw.front);
    for (let n = ranges.front.min; n <= ranges.front.max; n++) {
      if (frontSet.has(n) && !frontSeen.has(n)) {
        result.front[n] = missCounter;
        frontSeen.add(n);
      }
    }

    // Update back zone independently
    if (ranges.back && result.back) {
      const backSet = new Set(draw.back || []);
      for (let n = ranges.back.min; n <= ranges.back.max; n++) {
        if (backSet.has(n) && !backSeen.has(n)) {
          result.back[n] = missCounter;
          backSeen.add(n);
        }
      }
    }

    missCounter++;
  }

  // Numbers never seen: miss = total number of draws
  for (let n = ranges.front.min; n <= ranges.front.max; n++) {
    if (!frontSeen.has(n)) {
      result.front[n] = draws.length;
    }
  }

  if (ranges.back && result.back) {
    for (let n = ranges.back.min; n <= ranges.back.max; n++) {
      if (!backSeen.has(n)) {
        result.back[n] = draws.length;
      }
    }
  }

  return result;
}
