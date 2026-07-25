export function fmtMoney(cents: number | null | undefined): string {
  if (cents == null) return '待派奖';
  const yuan = cents / 100;
  return `¥${yuan.toLocaleString('zh-CN', { minimumFractionDigits: 0, maximumFractionDigits: 2 })}`;
}

export function fmtDate(iso: string): string {
  const d = new Date(iso);
  if (isNaN(d.getTime())) return '日期异常';
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}

export function fmtShortDate(iso: string): string {
  const d = new Date(iso);
  if (isNaN(d.getTime())) return '异常';
  return `${d.getMonth() + 1}月${d.getDate()}日`;
}

export function fmtDistance(meters: number | null | undefined): string {
  if (meters == null) return '';
  if (meters < 1000) return `${meters} 米`;
  return `${(meters / 1000).toFixed(1)} 公里`;
}
