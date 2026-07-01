# Lessons Learned

## L-20260701T103320Z
title: plan-06 前端 task 测试策略（vitest 必要 scope，不进 gate）
detail: plan-06 是前端 plan（Vue3/TS），但 workflow test_command=uv run pytest 只覆盖 Python。前端 task（T3 API client、T4-T7 组件/页面）测试策略：(1) implementor 须加 vitest+jsdom 做自动化测试（cd web && npm test）——plan 原设计的「手动浏览器验证」在 TDD 纪律下不够、证明不了回归；(2) vitest 依赖 + web/vitest.config.ts（独立于 vite.config.ts）+ npm test script 是 plan 认可的必要 scope，review 不得以「超 scope」否定；(3) 已知盲区：plan-06 gate 只跑 pytest，前端测试不进 gate，手动验收须 npm test。参考 T3 落地：web/src/api/client.ts + client.test.ts（21 tests pass，覆盖 CSRF 缓存/校验、401 重定向、错误原文保留、falsy body 序列化、AbortSignal 超时）。
status: active
