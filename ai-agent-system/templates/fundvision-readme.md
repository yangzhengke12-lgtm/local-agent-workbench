# 📈 FundVision - 基金智能分析平台

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.11+-blue?logo=python" alt="Python">
  <img src="https://img.shields.io/badge/FastAPI-0.109+-green?logo=fastapi" alt="FastAPI">
  <img src="https://img.shields.io/badge/ECharts-5.x-red?logo=echarts" alt="ECharts">
  <img src="https://img.shields.io/badge/license-MIT-yellow" alt="License">
  <img src="https://img.shields.io/badge/status-beta-orange" alt="Status">
</p>

> **FundVision** 是一款开源的基金智能分析工具，实时抓取基金净值数据，计算专业金融指标，并通过交互式 ECharts 图表可视化展示，帮助投资者做出更明智的决策。

---

## ✨ 功能特性

- 🔍 **基金搜索** — 支持基金名称/代码模糊搜索，快速定位目标基金
- 📊 **净值走势** — 双 Y 轴展示单位净值和累计净值历史走势
- 📈 **收益分析** — 每日收益率柱状图，直观查看涨跌分布
- 📉 **风险监控** — 滚动最大回撤面积图，红色标记风险区间
- 🎯 **指标计算** — 夏普比率、最大回撤、年化波动率等 10 项专业指标
- 🔄 **多基金对比** — 风险-收益散点图，横向对比多只基金
- 📐 **收益分布** — 直方图叠加正态拟合，评估收益稳定性
- 🕐 **自动刷新** — 每个交易日 15:30 自动抓取最新净值数据

---

## 🚀 快速开始

### 环境要求

- Python 3.11+
- pip / pipenv

### 安装与运行

```bash
# 1. 克隆项目
git clone https://github.com/your-org/fundvision.git
cd fundvision

# 2. 安装依赖
pip install -r requirements.txt

# 3. 启动服务
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

# 4. 打开浏览器访问
open http://localhost:8000
```

### 使用 Docker

```bash
docker compose up -d
```

---

## 🌐 访问地址

| 地址 | 说明 |
|---|---|
| `http://localhost:8000` | 前端仪表盘 |
| `http://localhost:8000/docs` | Swagger API 文档 |
| `http://localhost:8000/redoc` | ReDoc 文档 |

---

## 🏗️ 系统架构

```
┌─────────────────────────────────────────────────────────┐
│                     用户浏览器                            │
│                (HTML5 + ECharts 5.x)                     │
└──────────────────────┬──────────────────────────────────┘
                       │ HTTP / JSON
                       ▼
┌─────────────────────────────────────────────────────────┐
│              FastAPI 后端 (Python 3.11+)                  │
│  ┌──────────┐  ┌──────────┐  ┌───────────────────────┐  │
│  │  Routers  │──▶ Services │──▶ Models (SQLite/DB)    │  │
│  │  funds.py │  │ fetcher  │  │ database.py           │  │
│  │           │  │ calculator│  │                       │  │
│  └──────────┘  └──────────┘  └───────────────────────┘  │
└──────────────────────┬──────────────────────────────────┘
                       │ httpx (异步)
                       ▼
┌─────────────────────────────────────────────────────────┐
│             外部数据源 (天天基金 / 东方财富)               │
│  • 实时估值  • 历史净值  • 基金详情                       │
└─────────────────────────────────────────────────────────┘
```

> 详细架构说明请参阅 [docs/architecture.md](docs/architecture.md)

---

## 📚 文档

| 文档 | 说明 |
|---|---|
| [架构说明](docs/architecture.md) | 系统架构、组件关系、数据流详解 |
| [API 规范](docs/api-spec.md) | 全部 API 端点、请求/响应格式、错误码 |
| [用户手册](docs/user-guide.md) | 启动指南、功能操作、指标解读 |

---

## 📦 技术栈

| 层级 | 技术 | 用途 |
|---|---|---|
| **后端语言** | Python 3.11+ | 主开发语言 |
| **Web 框架** | FastAPI | 高性能异步 API |
| **数据抓取** | httpx (异步) | 请求天天基金/东方财富 API |
| **金融计算** | pandas, numpy, scipy | 指标计算 |
| **数据库** | SQLite (开发) / PostgreSQL (生产) | 数据持久化 |
| **任务调度** | APScheduler | 定时刷新净值 |
| **前端** | HTML5 + ECharts 5.x | 数据可视化 |
| **部署** | Docker / Docker Compose | 容器化部署 |

---

## 🤝 贡献指南

1. Fork 本仓库
2. 创建功能分支 (`git checkout -b feature/amazing-feature`)
3. 提交更改 (`git commit -m 'Add amazing feature'`)
4. 推送到分支 (`git push origin feature/amazing-feature`)
5. 创建 Pull Request

---

## 📄 许可证

本项目基于 MIT 许可证开源。详见 [LICENSE](LICENSE) 文件。

---

## 👥 团队

- **Alex** — 后端开发与架构设计
- **Elena** — 技术文档与知识管理
- **Marcus** — 前端开发与可视化
