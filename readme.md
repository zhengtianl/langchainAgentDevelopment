# langchainAgentDevelopment



本项目围绕 **便捷打板** 搭建：把 Supreme 等店铺商品的高清素材采集、整理与 **打版图（工艺单示意图）** 生成串成一条可走通的工作流，减少手工截图、整理与反复沟通成本。



## 目标与能力概览



- **打板导向**：下载任务完成后，可在商品图目录下生成 `tech_sheets/`，并与高清图一并打包；打版图支持通过环境变量选用 **通义万相**（DashScope）或 **MiniMax** 等图生图服务（详见 `web/app.py` 顶部说明与 `web/env.example`）。

- **Web 控制台**：`frontend/` 为 React（Vite + Ant Design）界面，调用后端 API 发起采集任务、查看日志与下载 ZIP。

- **后端 API**：`web/app.py`（FastAPI）驱动 Playwright 自动化拉取高清图、协调打版图生成；默认端口 `8765`，可与前端代理联动。

- **自动化脚本**：`scripts/` 内含 Supreme 店铺相关 Playwright 封装、截图与高清图下载等工具模块（`scripts/lib/`），便于扩展其它采集或批处理场景。

- **对话与记忆示例**：根目录 `main.py` 使用通义千问兼容接口与 `src/memory/` 中的记忆拼装，演示流式输出与多轮上下文（便于 Agent / 对话类能力迭代）。



## 技术方案与架构



### 总体思路



采用 **前后端分离 + 异步长任务 + 子进程隔离采集** 的形态：浏览器端仅负责编排与状态展示；采集与 IO 密集逻辑放在独立 Python 子进程中执行，避免阻塞 ASGI 事件循环；打版图生成按 `TECH_SHEET_PROVIDER` 路由至云端图生图 API。作业状态当前保存在进程内字典 `JOBS`（适合单机开发/内网工具场景）。



### 逻辑架构（分层与外部依赖）



```mermaid

flowchart TB

  subgraph Presentation["表现层"]

    UI["React 18 + Vite + Ant Design<br/>frontend/src"]

  end



  subgraph API["应用与编排层"]

    GW["FastAPI / Uvicorn<br/>web/app.py"]

    JOB["作业注册表 JOBS<br/>内存态 job_id → 状态 / 日志 / zip"]

    ORCH["后台线程 orchestration<br/>Thread + subprocess"]

  end



  subgraph Worker["执行层（采集与打包）"]

    SCR["supreme_tshirts_download_hd_images.py<br/>Playwright 自动化"]

    LIB["scripts/lib：会话、店铺 URL、Playwright 封装等"]

    FS["本地工件：downloads_work 下按 job 分区"]

  end



  subgraph TechSheet["打版图子系统"]

    DISPATCH["run_tech_sheets_dispatch<br/>provider: auto | dashscope | minimax | none"]

    QWEN["通义万相异步图生图<br/>web/qwen_wanx_i2i.py"]

    MM["MiniMax 图生图<br/>web/minimax_tech_sheet.py"]

  end



  subgraph External["外部系统"]

    SHOP["Supreme 店铺站点<br/>HTTPS"]

    DS_API["阿里云 DashScope / 百炼"]

    MM_API["MiniMax API"]

  end



  subgraph Experimental["实验能力（独立入口）"]

    CLI["main.py + src/memory<br/>流式对话与多轮记忆"]

  end



  UI -->|"REST：/api/meta、POST /api/jobs<br/>GET /api/jobs/:id、下载 ZIP"| GW

  GW --> JOB

  GW --> ORCH

  ORCH --> SCR

  SCR --> LIB

  SCR --> SHOP

  SCR --> FS

  ORCH --> DISPATCH

  DISPATCH --> QWEN

  DISPATCH --> MM

  QWEN --> DS_API

  MM --> MM_API

  ORCH --> FS

  CLI -.->|"不走 Web 管线"| DS_API

```



### 异步作业与交付物数据流



```mermaid

sequenceDiagram

  participant B as 浏览器

  participant F as FastAPI

  participant T as 后台线程

  participant P as 采集子进程

  participant W as 打版图模块

  participant FS as 本地 downloads_work



  B->>F: POST /api/jobs { mode, max_products, browser_channel }

  F->>F: 创建 job_id，JOBS[job_id]=running

  F->>T: 启动 Thread(_run_hd_download)

  F-->>B: { job_id, collection_url }



  loop 轮询

    B->>F: GET /api/jobs/{job_id}

    F-->>B: status / log

  end



  T->>P: subprocess.run( supreme_tshirts_download_hd_images.py )

  P->>FS: 写出高清图目录

  P-->>T: exit_code / stdout / stderr



  alt 采集成功

    T->>W: run_tech_sheets_dispatch(out_dir)

    W->>FS: 可选写入 tech_sheets/

    T->>FS: shutil.make_archive → *.zip

    T->>F: JOBS[job_id]=done, zip_path

  else 采集失败

    T->>F: JOBS[job_id]=error

  end



  B->>F: GET /api/jobs/{job_id}/download

  F-->>B: FileResponse(application/zip)

```



### 关键技术选型摘要



| 维度 | 选型 | 说明 |

|------|------|------|

| 前端运行时 | React 18 + TypeScript + Vite | 开发与构建；生产可静态部署并与 `VITE_API_BASE` 指向后端 |

| 后端运行时 | FastAPI + Uvicorn | OpenAPI 文档 `/docs`；CORS 限定本地前端来源 |

| 长任务模型 | 线程 + 子进程 | 线程承载阻塞式 `subprocess.run`；与 Playwright 进程边界清晰 |

| 采集引擎 | Playwright（Chromium/Chrome/Edge） | 由 `browser_channel` 选择通道 |

| 打版图 | DashScope Wanx / MiniMax | 由环境变量与 `TECH_SHEET_PROVIDER` 组合决策 |

| 实验对话 | OpenAI 兼容 Base URL + 通义模型 | `main.py` 演示记忆拼装与流式输出 |



## 快速上手（摘录）



- **后端**：`pip install -r requirements-web.txt`，在项目根目录执行 `python web/app.py`。

- **前端**：进入 `frontend/`，`npm install` 后 `npm run dev`；若后端端口非默认，按 `web/app.py` 注释配置 `frontend/.env.local` 中的代理目标。



更细的依赖、环境变量与端口说明见 `web/app.py` 文件头注释。


