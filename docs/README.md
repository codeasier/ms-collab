# 文档导航

- [渐进迁移计划](migration-plan.md)：公开边界、迁移顺序和验收门槛。
- [工作空间设计目标](workspaces.md)：用户视角与待明确的隔离契约。
- [Public CI](public-ci.md)：公开贡献的隔离边界、检查命令与未验证设置。
- [返回项目首页](../README.md)。

- [可运行本地演示](runnable-demo.md)：运行方式、API、已覆盖检查及未完成边界。

- [Trusted identity boundary](../packages/identity/README.md)：验证器与本地主体绑定接口；不是已接入的登录功能。

- [Pipeline snapshot analytics](../packages/pipelines/README.md)：作用域快照的成功作业耗时趋势；不含真实上游、执行或日志读取。
- [Artifact catalog core](../packages/artifacts/README.md)：显式作用域、季度目录与命名筛选；尚未接入真实存储或下载。

- [Transactional workspace API](../packages/workspace_api/README.md)：可信验证器与 SQLite 同事务授权的配置式 WSGI 适配器；没有真实登录提供方或生产启动入口。

- [Workspace administration UI](../packages/workspace_ui/README.md)：显式配置事务 API 的成员管理页面；不含默认身份或真实登录流程。
- [Workspace lifecycle and modules](../packages/workspace_admin_api/README.md)：平台操作员创建/归档、空间管理员的目录约束模块安装/启停，以及同事务持久幂等；无默认身份或外部执行权限。
- [Authorized artifact HTTP API](../packages/artifacts_api/README.md)：同一 SQLite 快照中的身份/权限、季度目录和命名视图查询；无真实 provider 或下载。
- [GitCode community snapshot adapter](../packages/community_sync/README.md)：显式凭据、只读 provider 与离线 SQLite 快照；事务授权 HTTP 另见下方社区 API。

- [Authorized GUI smoke observations](../packages/gui_smoke/README.md)：固定作用域与显式读者的任务快照、状态摘要和版本分页；无外部面板代理、任务执行、日志或报告下载。

- [Authorized pipeline snapshot API](../packages/pipeline_api/README.md)：同事务授权的流水线快照列表、详情与耗时趋势；只读合成数据边界，不含真实 CI 提供方或写入。

- [Authorized community API](../packages/community_api/README.md)：同库事务授权、只读刷新与精确内容审批；token可见不自动共享，公开空间不绕过成员权限。
- [Scoped pipeline provider acquisition](../packages/pipeline_sync/README.md)：读取适配服务的有界 GET 协议、逐次当前授权与原子快照发布；真实服务兼容未验，不含上游写操作。

- [Authorized artifact browser](../packages/artifacts_ui/README.md)：目录、季度、分页与命名视图的受权只读界面；同步入口另见下方受权索引，页面自身无同步/下载操作。

- [Identity-addressed workspace invitations](../packages/workspace_invitations/README.md)：可信目录指定身份的邀请/加入、24 小时过期、单次/撤销与事务审计；无公开秘密链接或默认身份。

- [Scoped artifact acquisition and index](../packages/artifact_sync/README.md)：OBS 有界只读分页、同库权限/元数据索引及精确内容审批；合成验证，真实账号兼容和受权下载未完成。
- [Authorized pipeline observations UI](../packages/pipeline_ui/README.md)：同源受权运行列表、详情及耗时展示；版本一致性与取消竞态防护，不含日志、执行或上游刷新。
- [Scoped responsibility candidates](../packages/responsibility_api/README.md)：责任田配置/CAS、字面路径匹配及经明确内容审批的只读候选；独立模块与逐 scope 授权，不含真实评审/合入/SLO、UI 或自动采集。

- [Scoped research-asset catalog](../packages/research_assets/README.md)：类型/实体、稳定业务身份、全来源原子发布与同事务受权查询；不含真实来源凭据、镜像读取或执行。
- [Artifact refresh and approval administration](../packages/artifact_admin/README.md)：显式管理员能力、私有刷新、完整候选摘要校验与精确读者审批；并发冲突须重新检视，不含下载。

- [Owner-scoped assistant saved history](../packages/assistant_history/README.md)：当前身份与权限隔离的只读事件、固定版本分页和可逆归档；不含 ACP 执行、真实网络或会话恢复。

- [Authorized responsibility UI](../packages/responsibility_ui/README.md)：结构化配置/CAS冲突处理与真实受权候选分页；同源内存凭据，明确指派未知、证据不等于评审，不含路径发布或上游动作。
- [Configured unified service host](../packages/application_host/README.md)：同一Cookie/session与策略事务下的六类模块和工作空间/邀请API；真实GitCode仍fail-closed，不挂载同步、下载、旧Bearer UI或生产入口。
- [Content-bound artifact download API](../packages/artifact_download/README.md)：独立字节授权、固定 OBS GET、完整 hash 校验后限时输出与逐块撤权；仅合成 provider/HTTP 验证，无前端下载入口或真实云验收。

- [Unified cookie-only frontend](../packages/application_ui/README.md): workspace and responsibility pages on one current session; real provider remains fail-closed.
