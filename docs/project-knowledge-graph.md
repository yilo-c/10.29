# 思忆集项目知识图谱

> 基于 Mermaid 图表的全项目知识拓扑，涵盖架构、组件、数据流和演进路线。
> 生成日期：2026-04-29

---

## 目录

1. [项目全景](#1-项目全景)
2. [桌面端架构分层](#2-桌面端架构分层)
3. [后端 automation-hub 架构](#3-后端-automation-hub-架构)
4. [数据流全景](#4-数据流全景)
5. [组件依赖图](#5-组件依赖图)
6. [对话 / 建议生成链路](#6-对话--建议生成链路)
7. [桌宠子系统](#7-桌宠子系统)
8. [Web UI 迁移路线](#8-web-ui-迁移路线)
9. [技术债务全景](#9-技术债务全景)
10. [文件拓扑 (关键文件)](#10-文件拓扑-关键文件)

---

## 1. 项目全景

```mermaid
graph TB
    subgraph 思忆集["🏠 思忆集 (Siyi Collection)"]
        direction TB
        subgraph Desktop["🖥️ 桌面端 PyQt6"]
            MW["MainWindow<br/>ui_main_window.py"]
            Views["PyQt Views / Dialogs"]
            VM["ViewModels"]
            Services["Services / Repos"]
        end
        subgraph Backend["☁️ 后端 automation-hub"]
            API["FastAPI REST API"]
            Worker["RQ Worker Queue"]
            SQLite["SQLite Database"]
        end
        subgraph Pets["🐾 桌宠子系统"]
            Nana["七奈桌宠<br/>Java/Electron"]
            Neuro["小问桌宠<br/>Python/Neuro"]
        end
        subgraph External["🌐 外部服务"]
            LLM["LLM API<br/>OpenAI / 通义千问 / DeepSeek"]
            Youdao["有道云笔记<br/>OAuth API"]
            News["RSS 新闻源"]
        end
    end

    Desktop -->|HTTP API| API
    Desktop -->|子进程管理| Pets
    Desktop -->|API 调用| LLM
    Desktop -->|OAuth 同步| Youdao
    API --> SQLite
    Worker --> SQLite
    API -->|RSS 抓取| News
    Worker -->|任务执行| LLM
```

---

## 2. 桌面端架构分层

```mermaid
graph TD
    subgraph Layer1["🧩 View 层 (PyQt Widgets)"]
        MW2["MainWindow"]
        Cards["工作台卡片<br/>TasksCard/NotesCard/NewsCard/..."]
        Dialogs["对话框<br/>TasksDialog / NotesDialog / NewsDialog"]
        StatusBar["状态栏<br/>StatusLabel / SyncLabel"]
        Tray["系统托盘<br/>SystemTrayIcon"]
        MenuBar["菜单栏<br/>MenuBarManager"]
    end

    subgraph Layer2["🧠 ViewModel 层 (无 Qt 依赖)"]
        WVM["WorkbenchViewModel<br/>build_card_list_state()<br/>build_status_summary()<br/>collect_sync_status()"]
        SBVM["StatusBarViewModel<br/>build_sync_summary()<br/>build_alarm_count_text()"]
    end

    subgraph Layer3["⚙️ Service 层 (业务逻辑)"]
        AS["AssistantService<br/>assistant_action_dispatcher<br/>assistant_suggestion_planner"]
        DC["DesktopCompanionController<br/>桌宠启停管理"]
        SO["SyncOrchestrator<br/>数据同步编排"]
        DO["DialogCoordinator<br/>对话框协调"]
    end

    subgraph Layer4["🗄️ Repository 层 (数据访问)"]
        TaskRepo["PersonalTaskRepository"]
        NoteRepo["PersonalNoteRepository"]
        NewsRepo["PersonalNewsRepository"]
        TransRepo["TranslationHistoryRepository"]
        VocabRepo["VocabularyRepository"]
        InboxRepo["InboxRepository"]
        SettingsRepo["SettingsRepository"]
    end

    subgraph Layer5["💾 Data 层"]
        SQLite2["SQLite 本地库"]
        Config["配置文件 / YAML"]
    end

    Layer1 -->|读取状态| Layer2
    Layer2 -->|调用逻辑| Layer3
    Layer3 -->|读写数据| Layer4
    Layer4 --> Layer5

    style Layer1 fill:#e1f5fe
    style Layer2 fill:#fff9c4
    style Layer3 fill:#c8e6c9
    style Layer4 fill:#ffccbc
    style Layer5 fill:#d1c4e9
```

---

## 3. 后端 automation-hub 架构

```mermaid
graph TB
    subgraph API_Layer["API 层"]
        MainAPI["main.py<br/>FastAPI 应用入口"]
        Routes["路由模块<br/>/tools /approvals /audit /tasks"]
        Schemas["Pydantic Schemas<br/>请求/响应模型"]
        Middleware["中间件<br/>认证 / 限流 / 日志"]
    end

    subgraph Service_Layer["Service 层"]
        ToolService["ToolService<br/>工具生命周期管理"]
        ApprovalService["ApprovalService<br/>审批流程"]
        AuditService["AuditService<br/>审计与诊断"]
        SyncService["SyncService<br/>数据同步"]
    end

    subgraph Worker_Layer["Worker 层"]
        RQ_Worker["RQ Worker<br/>worker.py"]
        Jobs["Job 定义<br/>jobs_v2.py / jobs/"]
        Executors["执行器<br/>executors/"]
    end

    subgraph Data_Layer["Data 层"]
        DB["DatabaseHandler<br/>裸 SQL 操作"]
        SQLite3["SQLite DB<br/>automation_hub.db"]
    end

    Routes --> Schemas
    Routes --> Service_Layer
    Service_Layer --> DB
    DB --> SQLite3
    Worker_Layer --> DB
    MainAPI --> Routes
    MainAPI --> Middleware
```

---

## 4. 数据流全景

```mermaid
sequenceDiagram
    actor User
    participant MW as MainWindow
    participant VM as ViewModel
    participant Svc as Service
    participant Repo as Repository
    participant SQLite as SQLite Local
    participant API as automation-hub API
    participant LLM as LLM API

    Note over User,LLM: 启动流程
    User->>MW: 启动应用
    MW->>MW: __init__ 初始化所有组件
    MW->>Repo: 加载本地数据
    Repo->>SQLite: SELECT
    SQLite-->>Repo: 数据
    Repo-->>MW: snapshot
    MW->>VM: build_card_list_state(snapshot)
    VM-->>MW: card_states
    MW->>VM: build_status_summary(snapshot)
    VM-->>MW: 状态文本
    MW->>MW: 渲染 UI

    Note over User,LLM: 用户交互流程
    User->>MW: 点击"助手建议"
    MW->>Svc: get_suggestions()
    Svc->>API: POST /assistant/suggestions
    API->>LLM: 调用 LLM
    LLM-->>API: 建议列表
    API-->>Svc: 建议
    Svc-->>MW: suggestions
    MW->>VM: build_suggestions_meta(count, snapshot)
    VM-->>MW: meta 文本
    MW->>MW: 更新建议卡片

    Note over User,LLM: 数据同步流程
    MW->>Repo: list_pending_sync_*()
    Repo->>SQLite: 查询待同步
    SQLite-->>Repo: pending items
    Repo-->>Svc: 待同步数据
    Svc->>API: POST /sync
    API-->>Svc: 同步结果
    Svc-->>MW: sync_status
    MW->>VM: collect_sync_status()
    VM-->>MW: 同步状态文本
```

---

## 5. 组件依赖图

```mermaid
graph LR
    subgraph Core["核心依赖链"]
        AC["AppContext<br/>依赖注入容器"]
        MW3["MainWindow"]
        DC2["DialogCoordinator"]
        MBM["MenuBarManager"]
    end

    subgraph Assistant["助手子系统"]
        APC["AssistantPromptContext<br/>prompt 构造"]
        ASP["AssistantSuggestionPlanner<br/>建议规划"]
        AAD["AssistantActionDispatcher<br/>动作分发"]
        EAK["_extract_action_key<br/>动作键提取"]
    end

    subgraph Companion["桌宠子系统"]
        DCC["DesktopCompanionController<br/>控制器"]
        Nana2["七奈桌宠<br/>独立进程"]
        Neuro2["小问桌宠<br/>独立进程"]
    end

    subgraph Dialogs2["对话框"]
        TasksD["TasksDialog"]
        NotesD["NotesDialog"]
        NewsD["NewsDialog"]
        StartupD["StartupGuideDialog"]
    end

    AC --> MW3
    AC --> DC2
    AC --> MBM
    MW3 --> APC
    MW3 --> ASP
    MW3 --> AAD
    MW3 --> DCC
    ASP --> EAK
    DC2 --> TasksD
    DC2 --> NotesD
    DC2 --> NewsD
    DC2 --> StartupD
    DCC --> Nana2
    DCC --> Neuro2
```

---

## 6. 对话 / 建议生成链路

```mermaid
flowchart LR
    Trigger["触发<br/>定时/手动/事件"] --> Context["构建上下文<br/>AssistantPromptContext"]
    
    Context --> History["对话历史<br/>_HISTORY_TOKEN_BUDGET"]
    Context --> Snapshot["数据快照<br/>_build_work_context_summary"]
    Context --> Doctor["诊断结果<br/>_build_desktop_doctor_continue_suggestion"]
    
    History --> Prompt["组装 Prompt"]
    Snapshot --> Prompt
    Doctor --> Prompt
    
    Prompt --> LLM_Call["LLM API 调用<br/>llm_resilience.py"]
    LLM_Call --> Parse["解析响应<br/>extract_suggestions"]
    
    Parse --> Plan["规划建议<br/>AssistantSuggestionPlanner"]
    Plan --> Sort["排序 + 去重"]
    Sort --> Filter["过滤 (状态/权限)"]
    Filter --> Display["展示给用户"]
```

---

## 7. 桌宠子系统

```mermaid
graph TB
    subgraph Controller["DesktopCompanionController"]
        Launch["launch / close"]
        Mood["mood 管理"]
        Status["status 检测"]
    end

    subgraph NanaPet["七奈桌宠"]
        NanaDir["七奈桌宠软件/"]
        NanaProc["独立 Java 进程"]
        NanaIPC["HTTP / WebSocket IPC"]
    end

    subgraph NeuroPet["小问桌宠"]
        NeuroDir["小问桌宠文件/"]
        NeuroProc["独立 Python 进程"]
        NeuroIPC["HTTP :3002 情绪接口"]
    end

    subgraph PetBridge["Pet Bridge (未来: QWebChannel)"]
        PetSignal["pet_mood_changed Signal"]
        PetSlot["pet Service Slot"]
    end

    Controller -->|子进程| NanaProc
    Controller -->|子进程| NeuroProc
    NanaProc --> NanaDir
    NeuroProc --> NeuroDir
    Controller -->|状态轮询| NeuroProc
    Controller --> PetSignal
```

---

## 8. Web UI 迁移路线

```mermaid
graph LR
    Phase1["阶段 1 (当前)<br/>Python Service 解耦<br/>+ ViewModel 引入"] --> Phase2["阶段 2 (桥接)<br/>QWebChannel 搭建<br/>+ Web UI 骨架"]
    Phase2 --> Phase3["阶段 3 (渐进替换)<br/>逐对话框迁移到 Web"]
    Phase3 --> Phase4["阶段 4 (完成)<br/>MainWindow 纯壳层<br/>< 200 行"]

    Phase1_desc["• ViewModel 层抽离<br/>• app_context 注入<br/>• 消除 parent_ref 穿透<br/>• 清理重复代码"]
    Phase2_desc["• AppBridge QObject<br/>• React + Vite 项目<br/>• 双向通信验证"]
    Phase3_desc["• Tasks → Web<br/>• Notes → Web<br/>• News → Web<br/>• 旧对话框保底"]
    Phase4_desc["• 所有 UI 由 Web 承载<br/>• 旧 Widget 下线<br/>• 统一 Web 技术栈"]

    Phase1 --- Phase1_desc
    Phase2 --- Phase2_desc
    Phase3 --- Phase3_desc
    Phase4 --- Phase4_desc
```

---

## 9. 技术债务全景

```mermaid
graph TB
    subgraph Debt["🔴 技术债务矩阵"]
        subgraph P0["P0 · 安全/停维风险"]
            Pydantic["Pydantic v1<br/>已停维"]
            FastAPI_V["FastAPI 0.105<br/>版本严重落后"]
        end
        subgraph P1["P1 · 架构腐化"]
            ParentRef["parent_ref 穿透<br/>跨层直接访问"]
            DupCode["重复方法<br/>_build_suggestions ×2<br/>_update_status_label ×2<br/>_build_work_context_summary ×2"]
            MagicNum["魔术数字<br/>2400 / 15 / 1800"]
        end
        subgraph P2["P2 · 可维护性"]
            RawSQL["裸 SQL 操作<br/>DatabaseHandler"]
            NoORM["无 ORM / 无迁移"]
            RQ_Old["RQ 队列<br/>无原生重试"]
            Requests["requests 库<br/>无 async 支持"]
        end
        subgraph P3["P3 · 演进"]
            NoWebUI["纯 PyQt UI<br/>无 Web 渲染"]
            NoWebChannel["无 QWebChannel<br/>桥接"]
            NoPkg["无打包方案<br/>PyInstaller"]
        end
    end

    P0 -->|升级路径| PydanticV2["TU-S5-01/02<br/>Pydantic v2 升级"]
    P0 -->|升级路径| FastAPI115["TU-S5-03<br/>FastAPI 0.115+"]
    P1 -->|修复路径| ViewModel["VM-S1 / VM-S2<br/>ViewModel 解耦"]
    P1 -->|修复路径| Cleanup["CL-S3<br/>代码清理"]
    P2 -->|改造路径| SQLAlchemy["TU-S5-04/05<br/>SQLAlchemy 2.0"]
    P2 -->|改造路径| Alembic["TU-S5-06<br/>Alembic 迁移"]
    P2 -->|评估路径| ARQ["TU-S5-07<br/>RQ→ARQ 评估"]
    P3 -->|演进路径| WebUI["TU-S5-09/10<br/>Web UI + Bridge"]
    P3 -->|演进路径| Package["TU-S5-11<br/>打包方案"]
```

---

## 10. 文件拓扑（关键文件）

```mermaid
graph TB
    subgraph DesktopFiles["桌面端 思忆集app/"]
        ui_main["⭐ ui_main_window.py<br/>~3000+ 行 · 核心窗口"]
        app_ctx["app_context.py<br/>依赖注入容器"]
        wvm["workbench_viewmodel.py<br/>🆕 ViewModel"]
        sbvm["status_bar_viewmodel.py<br/>🆕 StatusBar VM"]
        constants["constants.py<br/>🆕 常量集中管理"]
        asp2["assistant_suggestion_planner.py<br/>建议规划器"]
        aad2["assistant_action_dispatcher.py<br/>动作分发"]
        apc2["assistant_prompt_context.py<br/>Prompt 构造"]
        dcc2["desktop_companion_controller.py<br/>桌宠控制器"]
        dc2_2["dialog_coordinator.py<br/>对话框协调"]
        mbm2["menu_bar_manager.py<br/>菜单栏"]
        wbc["workbench_controller.py<br/>工作台控制器"]
        bridge["🆕 desktop_web_bridge.py<br/>QWebChannel 桥接"]
    end

    subgraph DialogFiles["对话盒子文件"]
        tasks_d["ui_tasks_dialog.py"]
        notes_d["ui_notes_dialog.py"]
        news_d["ui_news_dialog.py"]
        startup_d["ui_startup_guide_dialog.py"]
    end

    subgraph BackendFiles["后端 automation-hub/"]
        api_main["api/main.py<br/>FastAPI 入口"]
        schemas["api/schemas/<br/>Pydantic 模型"]
        models["🆕 api/models/<br/>SQLAlchemy 模型"]
        db_handler["DatabaseHandler.py<br/>裸 SQL"]
        worker_py["worker/worker.py<br/>RQ Worker"]
        jobs["worker/jobs/<br/>任务定义"]
    end

    subgraph Docs["文档 docs/"]
        arch["architecture-overview.md<br/>🆕 架构概述"]
        tech["tech-stack.md<br/>🆕 技术栈"]
        bridge_spec["desktop-web-bridge-spec.md<br/>🆕 桥接规范"]
        decouple["main-window-decoupling-plan.md<br/>解耦方案"]
        task_cards["free-ai-task-cards.md<br/>任务卡片"]
    end

    subgraph Tests["测试 tests/"]
        test_aad["test_assistant_action_dispatcher.py"]
        test_asp["test_assistant_suggestion_planner.py"]
        test_dcc["test_desktop_companion_controller.py"]
        test_wvm["🆕 test_workbench_viewmodel.py"]
        test_sbvm["🆕 test_status_bar_viewmodel.py"]
    end

    ui_main --> app_ctx
    ui_main --> asp2
    ui_main --> aad2
    ui_main --> apc2
    ui_main --> dcc2
    ui_main --> dc2_2
    ui_main --> mbm2
    ui_main --> wvm
    ui_main --> sbvm
    ui_main --> constants
    dc2_2 --> tasks_d
    dc2_2 --> notes_d
    dc2_2 --> news_d
    dc2_2 --> startup_d

    style ui_main fill:#ff6b6b,color:#fff
    style bridge fill:#ffd93d,color:#333
    style wvm fill:#6bcb77,color:#fff
    style sbvm fill:#6bcb77,color:#fff
    style constants fill:#6bcb77,color:#fff
    style models fill:#ffd93d,color:#333
```

---

## 附：技术栈全景速查

| 层级 | 技术 | 版本 | 说明 |
|------|------|------|------|
| 桌面框架 | PyQt6 | latest | 含 QtWebEngine |
| 后端框架 | FastAPI | 0.105 → 0.115+ | REST API |
| 数据校验 | Pydantic | v1 → v2 | schema 定义 |
| 数据库 | SQLite | 3.x | 本地存储 |
| ORM | SQLAlchemy | 🆕 2.0 | 替代裸 SQL |
| 迁移 | Alembic | 🆕 latest | schema 版本管理 |
| 任务队列 | RQ | 当前 | 待评估 ARQ/Celery |
| HTTP 客户端 | requests → httpx | 🆕 | 桌面端调用后端 |
| Web UI | React + Vite | 🆕 18+ | 渐进替换 PyQt UI |
| 桥接 | QWebChannel | Qt 内置 | Python ↔ JS 通信 |
| LLM | OpenAI / 通义千问 / DeepSeek | API | AI 功能 |
| 打包 | PyInstaller / Nuitka | 待评估 | 分发 .exe |

---

> **维护说明**：本知识图谱应与代码同步更新。重大架构变更后，请更新对应章节。
