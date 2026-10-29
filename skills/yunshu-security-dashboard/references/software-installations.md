# 软件安装清单补充流程

适用于在已有看板下方补充指定软件的安装情况。先检查用户最新工作簿，确认软件范围、列定义、是否只改新增区域；每次新读取仍确认日期、地址和本次AK/SK。本次已明确确认或仍在同一次执行中时不重复询问。

## 已确认的统计口径

- 三类软件：`Dassault Systemes Software Version`、`Dassault Systemes SIMULIA`、`Solidworks`。
- 清单展示标题分别为 `CATIA 盗版软件安装清单`、`ABAQUS 盗版软件安装清单`、`Solidworks 盗版软件安装清单`。CATIA 对应 `Dassault Systemes Software Version`，ABAQUS 对应 `Dassault Systemes SIMULIA`。仅改展示别名，接口查询及名称前缀匹配仍用原名；各小组的具体软件名、版本保留接口原文，不用别名覆盖。
- 用户最终定义是“安装这三类软件就算”：读取全部安装，不以 `is_piracy=true` 作为纳入条件，不要求安装记录含逐台盗版标识。保留用户模板的“盗版软件安装清单”标题，在该区域注明“统计口径：安装即纳入”。不要对外声称这些记录均通过平台盗版检测。
- 名称不区分大小写。前两类分别匹配上述完整名称前缀，覆盖其后发布年份、Release、版本、B30/B32等名称变体。不要把 Software VC/Prerequisites、B21 等无该前缀的软件混入 Software Version。
- Solidworks 包含名称中含 `solidworks` 的全部安装条目，包括主程序、不同SP版本以及该范围内的 CEF、3DEXPERIENCE、eDrawings、API SDK、Login Manager、To URDF 等组件。逐一保留接口的具体软件名和版本，不压成一个“Solidworks”条目，不丢弃同一员工安装的多个版本。
- 统计单位为安装记录；同一设备的多个软件/版本/路径各自列出。完全相同的员工ID、设备ID、软件名、版本和路径可去重；另报原始条数、重复条数、去重后条数。人数和设备数按ID去重，不能跨软件简单相加当作独立人数。

## 接口与读取

官方文档：[软件分析列表](https://s.apifox.cn/be529838-a3bb-4b14-a24e-107470d33c77/api-433160525)、[软件详情](https://s.apifox.cn/be529838-a3bb-4b14-a24e-107470d33c77/api-445371471)、[安装软件同步明细](https://s.apifox.cn/be529838-a3bb-4b14-a24e-107470d33c77/api-445371470)。文档访问密码由使用者提供，不存入skill。

1. `GET /epp/terminal_software/list`：软件级名称、版本、`software_id`、`soft_id`、`is_piracy`、`terminal_cnt`、`employee_cnt`等。页大小使用100并按显式总数分页。`is_piracy=true` 可用于用户单独询问“平台标记哪些盗版软件”，但本流程不据此删去指定软件。它不返回安装路径。
2. `GET /epp/software/analysis/get_software_detail`：通过 `software_id` 或 `soft_id` 查询软件级详情。软件总安装统计、软件盗版统计和带盗版筛选的列表可能口径不同，不互相替代。
3. `GET /epp/software/analysis/page_installed_software`：传 `sync_mode=full`、`page_num`、`page_size`（上限2000）、`software_name`（模糊名称）。全量模式是当前快照，不能用增量新增/删除变更列表当成当前安装总量。

按软件族查询（本三类可分别查 `Dassault` 和 `Solidworks`），再在本地按上述规则筛选，避免对带空格的长名称反复发送失败请求。始终使用客户端规范编码及HMAC，不为解决签名错误改成明文凭据或关闭认证。

安装记录映射：

- 姓名：`biz_related_emp.name`，身份键：`biz_related_emp.id`。
- 部门：`biz_related_emp.department[].name`，多部门按原顺序用 ` / ` 拼接。
- 设备：`biz_related_terminal.name`，身份键：`biz_related_terminal.id`；名称缺失可明确显示设备ID。
- 软件名、版本：`software_name`、`software_version`，原样保留。
- 安装路径：`install_path`。空值标注“接口未提供安装路径”，不猜测默认目录，不从另一个版本复制。
- 无关联员工标注“未关联员工”，不能根据同名或设备名猜人。缺少设备ID时停止发布并核对接口结构。

每页检查HTTP及业务成功码、`data.rows`、`data.page_info.total_num`、当前页码和总数稳定性；全量读取结束核对累计条数等于总数。业务码200404表示操作不允许，不能当成零条；按接口限流说明有限退避，仍失败则保留已验证快照、说明采集时间，不宣称完成新刷新。官方文档给出的full/incremental各每分钟60次上限不保证每次调用都可立即成功。

**不要再次混淆：** 软件列表的 `is_piracy`、软件详情的盗版计数和逐条安装记录是不同层级。安装同步接口未文档化的 `is_piracy` 参数曾返回与无筛选完全相同的安装集合；仅返回HTTP200不能证明该参数已生效。用户未来要求严格逐台盗版检测时，必须有可验证的明细标识或有效筛选，不能把本流程结果冒充为检测结果。

## 辅助脚本

`scripts/software_inventory.py` 复用本skill签名客户端，分页采集、验证并输出中间模型。仅用于软件安装流程，不刷新风险/外发数据；不保存凭据，也不调用HTML生成器。

```bash
<python> <skill>/scripts/software_inventory.py --confirmed-inputs \
  --work-dir '<新的私有运行目录>' --families 'Dassault Systemes Software Version' 'Dassault Systemes SIMULIA' 'Solidworks'
```

通过当前进程 `YUNSHU_ACCESS_KEY`、`YUNSHU_SECRET_KEY` 或隐藏输入提供本次凭据；`--base-url`、`--api-prefix`可指定确认后的服务。`--confirmed-inputs`仅记录已真实确认，不代替确认。`--from-snapshot '<run>/software-snapshot.json' --work-dir '<离线工作目录>'`可基于同一快照重算，保持原采集时间、不访问API。

脚本生成的是 `software-model.json` 中间模型，不是最终Excel。现有 `generate.py` / `render.mjs` 自动入口只支持原风险及外发区域，不能直接用它执行软件区域增补。按下述流程使用宿主表格工具填充，必要时在当前任务的work目录扩展软件专用渲染器；不得将本次机器路径、客户快照或已填报表打包进skill。

## 填表与校验

- 最新工作簿优先；附带模板没有软件区域时，先确认新增区域位置和字段，不自行决定。
- 原四个字段“姓名、部门、设备、安装路径”保持不变，不添加软件名称列。具体软件名及版本作为各组的小标题，组内沿用这四个字段，列出全部安装行。没有独立版本号时仅显示具体软件名，不伪造版本。
- 容量不足插行并顺延后续区域；不覆盖后面两类清单。保留原列宽、字体及字段，长软件名和路径换行并增大行高，不缩小文字强塞。
- 同一组中按员工ID、设备和路径稳定排序；同一员工相邻的姓名纵向合并并垂直居中。相同姓名不同ID不合并，不跨软件名/版本小组或三类区域合并。
- 只补软件时，原风险、外发区域的值、公式、样式、行高、列宽、合并、原更新日期和周期说明均保留。新增区域分别标注软件快照采集时间（北京时间），不能把旧风险数据标成今天刷新。
- 模型条数与填入安装行数对账；检查所有具体软件名及版本均出现、缺失路径明示、同一软件多版本未丢失。导出重读，逐个核对姓名/部门/设备/路径和合并；对比原区域全部数值、公式与样式；渲染新增区域检查裁切，再交付一个最终xlsx。原始接口数据、预览和中间模型留在私有work目录。
