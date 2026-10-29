# 执行与环境

## 公开运行依赖

Python 3.10+ 与 openpyxl 3.1.5+（小于4）。不需要 Node.js、宿主私有运行库或 Excel 安装。Windows、macOS、Linux 使用相同Python入口。安装skill不等于自动安装依赖；在当前Python或独立虚拟环境运行：

```bash
python -m pip install -r <skill>/requirements.txt
```

安装失败时报告具体pip错误，检查网络、代理或用户提供的镜像；不要寻找或索取私有库。使用运行脚本的同一个Python安装依赖。缺少openpyxl或模板不支持时，预检会在读取API前停止；缺少图片预览工具不阻止生成Excel。

## 风险及外发看板

```bash
python <skill>/scripts/generate.py --preflight --work-dir <预检目录>
python <skill>/scripts/generate.py --confirmed-inputs --date YYYY-MM-DD --work-dir <新运行目录> --output <报告.xlsx>
```

先真实确认日期、截止时间、地址和本次AK/SK。通过当前进程 `YUNSHU_ACCESS_KEY` / `YUNSHU_SECRET_KEY` 或隐藏输入提供凭据，不写入参数、文件或日志。可指定 `--cutoff 'YYYY-MM-DDTHH:MM:SS+08:00'`、`--template`、`--base-url`、`--api-prefix`。

预检验证Python版本、openpyxl、模板边界以及实际XLSX导入导出和重读。正式执行先预检，再采集、计算、填表、校验、原子发布。旧的 `--node` / `--node-modules` 参数仅为兼容而接受，不再使用。

```bash
python <skill>/scripts/generate.py --confirmed-inputs --date YYYY-MM-DD --resume-run <上次run目录> --output <报告.xlsx>
python <skill>/scripts/generate.py --from-snapshot <run目录>/snapshot.json --work-dir <离线工作目录> --output <报告.xlsx>
```

恢复同一次运行需要确认凭据、保持租户/地址/日期/截止一致；离线重算不读取API、保留原快照时间。不同客户或新刷新使用新目录。

## 只补充软件清单

按 [软件安装统计](software-installations.md) 使用 `software_inventory.py --template <含空白软件区的工作簿> --output <报告.xlsx>`，不会刷新风险/外发数据。脚本使用同一公开依赖直接导出Excel，不必另找宿主表格库。

## 公式与预览

openpyxl不执行通用Excel公式。风险看板只生成风险员工占比公式，脚本同步计算并验证该公式的缓存值；Excel打开后仍可重新计算。其他位置出现未支持的公式时先确认模板，不声称已做通用公式重算。

软件增补保留原有公式和样式，并回填原区域已有的公式缓存；原文件本就没有缓存的公式不伪造结果，Excel打开时重新计算。交付前对照原区域的值、公式、样式、合并、行高和列宽。可用时通过Excel、LibreOffice或宿主预览工具人工检查换行和裁切；没有预览工具时完成结构与内容校验并明确未做图片预览，不因此取消生成。

## 离线验证

```bash
python <skill>/scripts/test_metrics.py
python <skill>/scripts/test_pipeline.py --excel --work-dir <测试目录>
python <skill>/scripts/test_portable.py
```

使用合成数据，不访问真实租户。测试涵盖分页、统计、风险表扩展、同名不同人、合并、公式缓存和软件清单保留原区域。
