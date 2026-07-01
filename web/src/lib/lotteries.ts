export interface LotteryOption {
  code: string;
  name: string;
}

export const LOTTERIES: LotteryOption[] = [
  { code: 'ssq', name: '双色球' },
  { code: 'dlt', name: '大乐透' },
  { code: 'qlc', name: '七乐彩' },
  { code: 'qxc', name: '七星彩' },
  { code: 'fc3d', name: '福彩3D' },
  { code: 'pl3', name: '排列3' },
  { code: 'pl5', name: '排列5' },
];

export function lotteryName(code: string): string {
  return LOTTERIES.find((l) => l.code === code)?.name || code;
}
