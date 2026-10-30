# cangjie-guide

在本机通过 `gh` CLI 驱动本仓库的两个 Azure 工作流，不用打开浏览器，也不用记工作流输入的名字。

这是一个独立项目：自己的 `pyproject.toml`、`uv.lock`、虚拟环境与检查配置。它不参与 SDK 构建，
也不被主项目的工具链扫描。

## 安装

```bash
cd guide
uv sync
uv run cangjie-guide --help
```

## 就绪检查

先把本机与目标仓库的配置查一遍，这一步只读，不派发任何东西：

```bash
uv run cangjie-guide verbose
```

它检查 `gh` 是否安装、是否登录、token 是否带 `repo` 与 `workflow`、对仓库是否有写权限、
三个工作流文件是否存在于指定 ref、以及各工作流所需的 secret 是否齐全。

secret 列表需要仓库 admin 权限；拿不到时该项标为「无法验证」并说明原因，不算失败。
有 `fail` 时退出码非零。

## 构建

```bash
uv run cangjie-guide build                        # linux-x64，tag=main，Spot
uv run cangjie-guide build --target windows-x64
uv run cangjie-guide build --version 1.0.2        # 产出版本号，不带前导 v
uv run cangjie-guide build --compiler 'https://atomgit.com/Cangjie/cangjie_compiler.git#011f9c8'
uv run cangjie-guide build --dry-run              # 只打印将要发送的参数
uv run cangjie-guide build --wait                 # 派发后跟到结束
```

`--version` 决定写进 SDK 与产物名字的版本号，`--tag` 决定从哪个 ref 取源码，两者互不影响。
不给 `--version` 时工作流按 `tag` 推导（分支名归一化成 `0.0.0-dev`）。版本号必须是不带前导
`v` 的 MAJOR.MINOR.PATCH；带 `v` 或不是 SemVer 时本机就报错，因为 harness 会默默剥掉 `v`、
把其余不合法输入换成 `0.0.0-dev`，产物就会以另一个版本的名字发布。

`--compiler` 用的是 `URL#REF`，与工作流输入一致，`#` 前后不需要额外转换。用 `#` 是因为 URL
本身已经带冒号（scheme、端口），分支名又带斜杠（`feat/linux-cross-windows`），只有 `#` 能同时避开两者。
写成 `URL:REF` 会在派发前报错，否则那一整串会被当成 clone URL，git 会去找一个名为该分支的仓库。

派发前会做两项检查：

**同名产物**。`upload-artifact` 遇到同名产物会覆盖，所以一次重构建会顶掉上一次的归档，
即使测试正引用着它。发现同名未过期产物时列出它的来源与过期时间：非交互环境下报错并提示
加 `--force`，有 TUI 时弹出确认（默认选「不替换」）。

**在途运行**。仓库没有声明 `concurrency:`，重复派发不会排队，会再开一台 VM。
两台 16 vCPU 争同一份 `lowPriorityCores` 配额，第二台还可能被静默升到 Regular，价格约五倍。

## 测试

```bash
uv run cangjie-guide test --sdk 40725832486 --suites llt \
  --case testsuites/LLT/compiler/CodegenCJNative/tuple/tuple_ctor.cj
uv run cangjie-guide test --sdk latest --suites llt --changed-in HEAD
uv run cangjie-guide test --sdk https://example.com/cangjie-sdk-linux-x64.tar.gz --suites hlt
```

`--sdk` 接受四种来源：

| 写法 | 含义 |
| --- | --- |
| `40725832486` | 该运行里的 linux 产物 |
| `latest` | 最近一次成功的构建 |
| `cangjie-linux-x64-0.0.0-dev` | 产物名 |
| `https://...` | 直链，走 `sdk_url` |

派发前会拒绝这些组合：`sdk_artifact` 与 `sdk_url` 同时给出、`changed_in` 与 `--case` 同时给出、
`suites` 含 `conformance` 却用 `vm_size=auto`、以及一个用例都没选中的空选择
（空选择在两个测试框架里都会以退出码 0 结束，看起来像通过）。

## 查看

```bash
uv run cangjie-guide runs                 # 最近运行
uv run cangjie-guide status 40725832486   # 作业与当前步骤
uv run cangjie-guide status 40725832486 --watch
uv run cangjie-guide logs 40725832486
uv run cangjie-guide logs 40725832486 --job <job-database-id>
uv run cangjie-guide artifacts 40725832486
uv run cangjie-guide artifacts --search   # 所有未过期的 linux SDK 产物
uv run cangjie-guide artifacts 40725832486 --get cangjie-linux-x64-0.0.0-dev
```

产物下载到 `~/.cache/cangjie-guide/<run-id>/`，不落进工作树。

`cancel` 只提交取消请求。虚拟机由 cleanup 作业删除，那个作业可能失败；泄漏的资源组由每小时
运行的清理工作流兜底，最坏要等八小时。

## 交互式引导

引导界面用 [Questionary](https://github.com/tmbo/questionary) 实现，一次问一个输入。
输出是终端时，`build` 默认就走完整表单：按工作流声明的顺序问每一个输入，每项预填工作流默认值，
回车即接受，选择型输入只能取工作流列出的选项。`--no-tui` 跳过表单、只用命令行参数；
`--tui` 在没有终端时也强制询问。`test`/`logs`/`artifacts`/`cancel` 只在缺少参数且能询问时才弹出
选择（SDK 产物、suite、运行）。按 Ctrl-C 取消会直接终止，不会拿空值继续派发。

## 设计上刻意不做的事

- **不列出可派发的工作流全集**。`reap-orphans.yml` 是 `workflow_dispatch: {}`，零输入，
  会对每个创建超过八小时的 `cangjie-*` 资源组执行 `az group delete`。零输入意味着界面上
  没有任何东西拦在一次点击前面，所以它不在可派发清单里。
- **不调用 `az`**。所有云操作都发生在工作流里，本机只跟 GitHub 对话。
- **不从工作区读工作流定义**。表单按 dispatch ref 拉取 `gh api .../contents/...?ref=`，
  本地分支或未提交改动不会让表单描述一个与实际运行不同的工作流。
- **不吞掉 `gh` 的失败**。错误分三类：未就绪（日志还没生成）、可重试（5xx、限流）、致命。
  只有前两类会被等待或重试。

## 开发

```bash
cd guide
uv run ruff check .
uv run ruff format --check .
uv run pyright
uv run ty check
uv run pytest
```

工作流输入的默认值与选项从 `.github/workflows/*.yml` 读取，不硬编码。工作流改了默认值，
这里跟着走。
