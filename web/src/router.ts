import { createRouter, createWebHistory } from 'vue-router';

const routes = [
  {
    path: '/login',
    component: () => import('./pages/Login.vue'),
    meta: { nav: '登录', icon: '🔐', public: true },
  },
  {
    path: '/',
    component: () => import('./pages/Dashboard.vue'),
    meta: { nav: '仪表盘', icon: '📊' },
  },
  {
    path: '/numbers',
    component: () => import('./pages/MyNumbers.vue'),
    meta: { nav: '我的号码', icon: '🎫' },
  },
  {
    path: '/query',
    component: () => import('./pages/DrawQuery.vue'),
    meta: { nav: '开奖查询', icon: '🔍' },
  },
  {
    path: '/wins',
    component: () => import('./pages/WinRecords.vue'),
    meta: { nav: '中奖记录', icon: '🏆' },
  },
  {
    path: '/stats',
    component: () => import('./pages/MyStats.vue'),
    meta: { nav: '我的统计', icon: '📈' },
  },
  {
    path: '/trend',
    component: () => import('./pages/Trend.vue'),
    meta: { nav: '开奖走势', icon: '📉' },
  },
  {
    path: '/settings',
    component: () => import('./pages/Settings.vue'),
    meta: { nav: '设置', icon: '⚙️' },
  },
  {
    path: '/admin',
    component: () => import('./pages/Admin.vue'),
    meta: { nav: '后台管理', icon: '🛠', admin: true },
  },
];

export default createRouter({
  history: createWebHistory(),
  routes,
});
