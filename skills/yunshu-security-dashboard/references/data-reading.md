# 数据接口与读取

软件分析、详情及安装同步接口的字段、分页和“安装即纳入”口径见 [软件安装统计](software-installations.md)。软件模式独立读取，不必刷新本页的DLP和员工全目录接口。

## API清单

默认地址 `https://cs-hz0.sase.eagleyun.com`，可接受用户明确指定的其他租户地址/前缀。只用GET读取，不创建风险报告任务。

- `/dlp/log_process/list_dlp_file_send_log`：`start_time,end_time,page_num,page_size`；读近30天审计。
- `/dlp/risk/event/list`：同上；读过去90天风险事件。
- `/idaas/identity_source/list`：分页发现全部身份源。
- `/idaas/employee/employee_list`：分页员工姓名、部门、`is_active`、员工ID。
- `/idaas/identity_source/custom/employee/get_count`：逐个身份源传 `iden_source_id`，核对人数。
- `/idaas/employee/list_with_terminal`：逐身份源分页，关联员工和终端。
- `/idaas/employee/get_detail`：传 `employee_id`，只在列表无法解决姓名/部门时补查。
- `/epp/it/terminal/list_all_terminal`：可用 `page_num=1,page_size=1` 读取总数；需要设备详情再分页。

员工、绑定列表建议每页200；风险、审计每页1200。本工作流已验证这些参数；接口拒绝时检查约束，不擅自改成未经验证的筛选参数。

通常响应 `code=200`，列表为 `data.rows`，分页为 `data.page_info.total_num`；也可能是 `data.list/items`，有差异先检查真实响应。`get_count` 的数字可能是字符串。禁止把权限失败或响应结构未知当作空数据。

## HMAC认证

AK/SK通过当前进程环境 `YUNSHU_ACCESS_KEY` / `YUNSHU_SECRET_KEY` 注入，或由统一入口隐藏输入；不写 `.env`、脚本、配置或缓存，不用命令行参数传秘密。Excel子进程移除凭据环境变量。

GET查询参数按键排序；键和值分别URL编码（safe为空），列表值按字符串排序。签名内容为：

```text
GET\n<带前缀路径>\n<规范查询串>\n<AK>\n<RFC1123 GMT Date>\n
```

使用SK做HMAC-SHA256，再Base64编码。请求头：`Accept: application/json`、`Date`、`X-HMAC-ALGORITHM: hmac-sha256`、`X-HMAC-ACCESS-KEY`、`X-HMAC-SIGNATURE`。不禁用TLS校验，不将签名、认证头、异常原始响应中的敏感数据写入报告。

常规报表使用 `generate.py`。需要单接口核验时可用 `fetch_api.py`；分页ID字段由接口自动确定，不提供关闭ID验证的选项。只支持HTTPS原点，前缀单独传入；所有认证重定向（包括同域）均拒绝，确保认证头不会自动转发给其他地址。例（当前进程凭据已安全注入）：

```bash
python3 scripts/fetch_api.py --path /dlp/risk/event/list --params-json '{"start_time": 0, "end_time": 1}' --paginate --page-size 1200 --work-dir '<本次work目录>/pages' --output '<本次work目录>/risk.json'
```

## 完整性、限流和快照

- 先固定T，所有风险与审计读取共享T，不允许分页期间不断用新的“现在”。滚动30天和90天开始日必须由本次确认日期计算，不能硬编码本次日期或历史身份源ID。
- 同一执行恢复使用 `--resume-run`，需再次确认本次凭据；恢复时验证地址、前缀、AK单向指纹、统计日期、固定截止时间。接口页缓存再绑定参数、接口、分页大小、投影版本。缓存目录700、文件600。不把原始AK/SK存入清单。
- 强制校验HTTP/业务code，识别 `data.page_info.total_num/total` 和 `data.total_num/total`；多字段不一致、未知总数、非法数值均失败，绝不按短页推断完整。空页但总数未读完最多尝试4次，失败保留已完成页面，不生成新报告。
- 唯一ID由接口自动指定；用磁盘SQLite索引校验，恢复时从缓存页重建索引。重复、总数漂移、越界均失败；不通过去重掩盖漏页。去掉快照、图片URL等无关字段，只缓存指标所需字段。
- 请求默认单并发并适度间隔；429按 `Retry-After` 或有上限退避，有限次数后停止并报告。不要对权限错误持续重试。
- 运行目录保存 `run.json`（绑定与状态）、逐接口 `manifest.json`（页面/条数/总数/完整性）、最小字段缓存、`snapshot.json`、统计模型和渲染预览；审计日志不累积为全量内存列表。只在最终校验通过后原子替换交付文件，失败不覆盖旧报告。这些运行数据不放入skill或分享包。
- 部门归属和激活状态为当前快照，不宣称历史准确。
