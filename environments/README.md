# environments/ — 双套环境拓扑（free-api-hub）

依据 DevProjectTeamSkill `environment_topology.md`：每个项目采用「开发测试一套 + 生产一套」双套环境组，
组内按 dev / test 逻辑细分，配置以 `../台账/20_环境配置.csv` 为单一真实源。

```
environments/
├── nonprod/            # 非生产组（dev+test 共用平台文件）
│   ├── dev/            # 开发 slot：源码/venv/局部配置/state.db
│   ├── test/           # 测试 slot：测试脚本/造数/报告
│   └── shared/         # 组内共享：公共模板/工具链
└── prod/              # 生产组独立目录树
    └── deploy/         # 生产部署产物/回滚包/审计
```

## 约定
- 端口：非生产 30000~39999（dev=30000+offset, test=30100+offset），生产 8000~8999（网关映射）。
- 数据卷：`free-api-hub-{envGroup}-{svc}`，dev/test/prod 互不交叉。
- 密钥：仅存别名（见 `.env.nonprod.example` / `.env.prod.example`），真实值经 `.secrets/` 注入。
- 生产变更：走 CAB/回滚预案，禁止人工就地改生产实例（不可变基础设施）。
- 单一制品提升：nonprod 验证通过的同一构建产物提升至 prod（build once, promote）。
