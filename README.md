# 云枢信息安全运营看板 Skill

读取云枢 OpenAPI 的风险事件、员工与设备信息、文件外发放行日志，按固定模板生成中文 Excel 看板。包含接口分页与断点恢复、统计函数、脱敏模板和离线测试。

## 一条命令安装到 Codex

先具备 `skills` CLI 所需的 Node.js（当前官方要求22.20+，24.x LTS亦可），然后运行：

```bash
npx skills add SEKKOO/yunshu-security-dashboard --skill yunshu-security-dashboard -a codex -g -y
```

安装到其他支持的代理时，可省略 `-a codex`，按提示选择。命令选项见 [skills CLI 官方文档](https://github.com/vercel-labs/skills/blob/main/docs/commands.md)。

也可使用 Codex 自带的 skill-installer：

```bash
python3 "${CODEX_HOME:-$HOME/.codex}/skills/.system/skill-installer/scripts/install-skill-from-github.py" \
  --repo SEKKOO/yunshu-security-dashboard \
  --path skills/yunshu-security-dashboard
```

第二种方式要求本机已有对应的系统安装脚本；安装目录已存在时安装器会停止，不覆盖旧版本。

## 使用

在 Codex 中输入：

```text
使用 $yunshu-security-dashboard 生成信息安全运营看板，先向我确认统计日期和本次 AK/SK。
```

每次新的数据刷新前都会确认日期和本次凭据。仅对同一快照重排版、重算或重新导出时，不重新访问接口。

**安装 Skill 不会自动安装报表运行依赖。** Excel 生成需要 Python 3.10+、Node.js 22+，以及宿主提供的 `@oai/artifact-tool` 电子表格运行库。普通Python终端或未提供该运行库的代理环境不能直接生成报表；统一入口会在访问任何云枢接口之前做实际的模板、导入、渲染、导出预检。通过宿主的 workspace dependencies 工具取得路径，不使用作者机器的路径。

## 报表口径

- 员工总数按官方目录 `is_active = true` 统计。
- 风险涵盖所有等级；事件按上报条数计数，不使用事件内部触发次数。
- 员工按风险总分取TOP10，个人事件按单次风险分降序；同名策略合并，姓名按员工ID合并展示。
- 外发仅统计 `trigger=securityFileSend` 且实际 `action=observe` 的记录。
- 外发大小按已知字节数合计，类型TOP3按大小排序，括号显示该类型合计大小。
- “本月累计”为包含当日的最近30天；历史列分别使用近7天和过去90天。
- 默认不生成折线图或柱状图，保留模板已确认的字段与样式。
- 支持只补充指定软件安装清单，原风险/外发数据与日期保持不变。用户确认“安装即纳入”后，完整列出不同软件名、版本及安装路径，同一员工相邻姓名合并；路径缺失明确标注。该统计与平台逐台盗版检测结果有区别。

软件增补的口径、接口及辅助脚本见 [软件安装统计](skills/yunshu-security-dashboard/references/software-installations.md)。现有统一入口自动生成风险/外发区域；软件辅助脚本生成已验证的中间模型，再由宿主表格工具填入用户的新增区域。

接口和口径细节见 [Skill入口](skills/yunshu-security-dashboard/SKILL.md)、[统计口径](skills/yunshu-security-dashboard/references/metrics.md)。

## 直接执行与验证

[执行与环境说明](skills/yunshu-security-dashboard/references/execution.md) 包含统一生成入口、预检、断点恢复和离线重导出命令。

无需客户凭据即可运行核心离线测试：

```bash
python3 skills/yunshu-security-dashboard/scripts/test_metrics.py
python3 skills/yunshu-security-dashboard/scripts/test_pipeline.py
```

带Excel运行库的完整模拟接口测试也在执行说明中。测试使用合成数据，不访问真实租户。

本仓库只包含代码、说明和脱敏模板，不包含客户凭据、员工目录、审计日志或已填充报表。AK/SK仅通过本次进程环境或隐藏输入提供；认证请求不自动重定向。
