/**
 * 供 AI / 自动化修改本前端时的 UI 约定（UTF-8 源文件，勿改编码）。
 * 修改界面时请同步更新此文件，保持与实现一致。
 */
export const AI_AGENT_UI_SPEC = `
# Supreme 下载面板 — UI 规范

## 技术栈
- React 18 + TypeScript + Vite。
- 必须使用 Ant Design（antd）组件：表单输入用 InputNumber、Select、Button、Card、Alert、Spin、Typography、Space、Collapse 等；禁止用原生 input/select/button 作为交互主体。
- 全局由 main.tsx 的 ConfigProvider 提供 locale（zh_CN）与暗色主题 token；业务页勿重复包一层无必要的 Provider。

## 编码与文案
- 所有源码文件 UTF-8（无 BOM）。index.html 须含 <meta charset="UTF-8" />。
- 用户可见中文文案统一在此面板维护，避免出现乱码或 ANSI 混用。

## 交互
- 下载任务进行中：全屏 Spin（fullscreen），并禁用输入与主要按钮，防止重复提交。
- 后端不可达：Alert 说明先启动 web/app.py 与代理端口，错误信息可换行展示。
- 校验失败（如件数非法）：用 App.useApp() 的 message，勿用浏览器原生 alert。

## 可访问性
- 加载态保留 aria-busy / 语义化标题；重要操作用 Button 而非 div。

## API
- 请求经 Vite 代理 /api，与 FastAPI 对齐；勿硬编码绝对域名。
`.trim();
